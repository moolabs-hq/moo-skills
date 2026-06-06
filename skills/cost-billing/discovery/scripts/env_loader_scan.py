#!/usr/bin/env python3
"""Phase 1.7-scan — env-loader pattern detection for /cost-billing-discovery.

Scans each service's source tree for recognized env-loading patterns
(pydantic-settings, dotenv, viper, etc.). Produces
.moolabs/customer-context/env-routing-inventory.yaml describing the
recognized pattern (or stub_required=true when none found) and the
deployment-surface insertion points (Terraform, k8s, docker-compose,
.env.example).

Pattern catalog lives at shared/assets/env-loader-patterns.yaml.

Usage:
    python env_loader_scan.py \\
        --signed-yaml .moolabs/chain/04-final.signed.yaml \\
        --customer-context-dir .moolabs/customer-context \\
        --catalog skills/cost-billing/shared/assets/env-loader-patterns.yaml \\
        [--repo-root .]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Catalog loading
# ──────────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    id: str
    language: str
    detection_signal: str
    import_signals: list[str]
    structural_signals: list[str]
    wire_target: dict[str, str]
    priority: int


def load_pattern_catalog(path: Path) -> list[Pattern]:
    """Load env-loader-patterns.yaml. Uses PyYAML when available, falls back
    to a minimal hand-rolled parser otherwise (matches sdk_snapshot.py's
    no-runtime-dep approach for the codemod environment).
    """
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
    except ImportError:
        data = _hand_rolled_yaml_load(path)

    out: list[Pattern] = []
    for entry in data.get("patterns", []):
        out.append(Pattern(
            id=entry["id"],
            language=entry["language"],
            detection_signal=entry["detection_signal"],
            import_signals=list(entry.get("import_signals", []) or []),
            structural_signals=list(entry.get("structural_signals", []) or []),
            wire_target=entry["wire_target"],
            priority=int(entry.get("priority", 50)),
        ))
    return out


def group_patterns_by_language(patterns: list[Pattern]) -> dict[str, list[Pattern]]:
    """Group patterns by language, sorted by priority descending within each."""
    by_lang: dict[str, list[Pattern]] = {"python": [], "typescript": [], "go": []}
    for p in patterns:
        by_lang.setdefault(p.language, []).append(p)
    for lang in by_lang:
        by_lang[lang].sort(key=lambda p: -p.priority)
    return by_lang


def _hand_rolled_yaml_load(path: Path) -> dict:
    """Minimal YAML reader for the pattern catalog when PyYAML is absent.
    Only supports the catalog's known shape; not a general YAML parser.
    """
    # Phase A leans on PyYAML being available in the test environment (smoke
    # already imports yaml). This fallback is here so the script can run in
    # a customer's codemod environment that lacks PyYAML — Phase B's instrument
    # side will revisit this.
    raise NotImplementedError(
        "PyYAML required for env_loader_scan.py in Phase A. "
        "Install pyyaml or run from the smoke environment."
    )


# ──────────────────────────────────────────────────────────────────────
# Per-file scan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanResult:
    """Result of scanning one file against the relevant patterns."""
    pattern_id: str
    file: str
    line_to_insert: int
    confidence: str  # "high" | "medium" | "low"
    confidence_score: float  # 0.0 to 1.0
    evidence: list[str] = field(default_factory=list)
    wire_target: dict[str, str] = field(default_factory=dict)


# Confidence-band thresholds. Matches catalog comment.
_HIGH_THRESHOLD = 0.85
_MEDIUM_THRESHOLD = 0.50
_LOW_THRESHOLD = 0.30


def _band(score: float) -> str:
    if score >= _HIGH_THRESHOLD:
        return "high"
    if score >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _signal_score(text: str, regexes: list[str]) -> tuple[float, list[str]]:
    """Return (signal_strength, matched_evidence_lines). 1.0 if any regex
    matches, 0.0 if none. Evidence is the first matching line for each regex.
    """
    if not regexes:
        return 0.0, []
    evidence: list[str] = []
    matched = False
    for rx in regexes:
        for i, line in enumerate(text.splitlines(), start=1):
            if re.search(rx, line):
                evidence.append(f"line {i}: {line.strip()[:120]}")
                matched = True
                break
    return (1.0 if matched else 0.0), evidence


def _python_insert_line(text: str, class_pattern: str) -> int:
    """For Python class-based patterns: return the last field line of the
    first matching class (1-indexed). For non-class patterns: return the
    last non-blank, non-import line + 1.
    """
    lines = text.splitlines()

    # Try to find the class block
    in_class = False
    class_re = re.compile(class_pattern)
    last_field_line = 0
    class_start = 0
    for i, line in enumerate(lines, start=1):
        if not in_class and class_re.search(line):
            in_class = True
            class_start = i
            last_field_line = i
            continue
        if in_class:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if stripped == "":
                continue
            # Dedented back to module level → end of class
            if indent == 0 and stripped:
                break
            # A class body line — track as last field if it looks like a field
            if ":" in stripped or "=" in stripped:
                last_field_line = i

    if last_field_line > class_start:
        return last_field_line

    # Fallback for non-class patterns: insert after the last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1


def _ts_insert_line(text: str, opening_pattern: str) -> int:
    """For TS object/schema patterns: return the last entry line of the
    first matched balanced-brace block (1-indexed). Falls back to last
    non-blank line + 1 when no balanced match.
    """
    lines = text.splitlines()
    opening_re = re.compile(opening_pattern)
    for i, line in enumerate(lines, start=1):
        if opening_re.search(line):
            # Found the opener; now find the closing brace and the last
            # content line inside.
            depth = line.count("{") - line.count("}")
            if depth <= 0:
                continue
            last_content_line = i
            for j in range(i + 1, len(lines) + 1):
                inner = lines[j - 1]
                inner_stripped = inner.strip()
                depth += inner.count("{") - inner.count("}")
                if depth <= 0:
                    # We've closed the block — return the line BEFORE the closer.
                    # If the closer is on a line by itself, last_content_line
                    # already points at the last content line.
                    if inner_stripped in {"}", "};", "})", "});", "})", "}));"}:
                        return last_content_line
                    return j
                # Only track content lines while still inside the block
                if inner_stripped and not inner_stripped.startswith("//"):
                    last_content_line = j
            return last_content_line
    # Fallback: last non-blank line
    for i in range(len(lines), 0, -1):
        if lines[i - 1].strip():
            return i
    return 1


def _go_insert_line(text: str, opening_pattern: str) -> int:
    """For Go struct/func patterns: find the last content line inside the
    first matched balanced-brace block (1-indexed). Same shape as
    _ts_insert_line but tuned for Go's `type X struct { ... }` and
    `func X() { ... }` layouts.

    For Go patterns the structural signal often matches individual field/
    statement lines (e.g. envconfig struct tags, viper.BindEnv calls) rather
    than the block opener itself.  In that case we return the last matching
    line — the natural place to append the next field or call.  If the first
    match opens a brace block we delegate to _ts_insert_line's depth-tracking
    logic.
    """
    lines = text.splitlines()
    opening_re = re.compile(opening_pattern)

    # Collect all matching line numbers.
    matching: list[int] = []
    for i, line in enumerate(lines, start=1):
        if opening_re.search(line):
            matching.append(i)

    if not matching:
        # Fallback: last non-blank line.
        for i in range(len(lines), 0, -1):
            if lines[i - 1].strip():
                return i
        return 1

    first_match_line = matching[0]
    first_match_text = lines[first_match_line - 1]

    # If the first match opens a brace block, use TS depth-tracking.
    if first_match_text.count("{") > first_match_text.count("}"):
        return _ts_insert_line(text, opening_pattern)

    # Otherwise (field-level or statement-level signal): return the last
    # matching line — the natural append point for the next field/call.
    return matching[-1]


def scan_file(path: Path, patterns: list[Pattern]) -> ScanResult | None:
    """Scan a single file against the patterns. Return the highest-confidence
    match, or None if no pattern reaches the LOW threshold."""
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None

    best: ScanResult | None = None
    best_score: float = 0.0

    for p in patterns:
        import_score, import_evidence = _signal_score(text, p.import_signals)
        struct_score, struct_evidence = _signal_score(text, p.structural_signals)

        # Combine: structural weighted higher than imports.
        # Both → 0.95 (high); structural only → 0.65 (medium);
        # import only → 0.35 (low); neither → 0.0
        if import_score > 0 and struct_score > 0:
            score = 0.95
        elif struct_score > 0:
            score = 0.65
        elif import_score > 0:
            score = 0.35
        else:
            score = 0.0

        if score < _LOW_THRESHOLD:
            continue

        if score > best_score:
            best_score = score
            # For class-based Python patterns, derive insertion line from the class
            line_to_insert = 1
            if p.structural_signals:
                if p.language == "python":
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
                elif p.language == "typescript":
                    line_to_insert = _ts_insert_line(text, p.structural_signals[0])
                elif p.language == "go":
                    line_to_insert = _go_insert_line(text, p.structural_signals[0])
                else:
                    line_to_insert = _python_insert_line(text, p.structural_signals[0])
            best = ScanResult(
                pattern_id=p.id,
                file=str(path),
                line_to_insert=line_to_insert,
                confidence=_band(score),
                confidence_score=score,
                evidence=import_evidence + struct_evidence,
                wire_target=p.wire_target,
            )

    return best


# ──────────────────────────────────────────────────────────────────────
# Service-level scan
# ──────────────────────────────────────────────────────────────────────

_EXTENSION_BY_LANGUAGE = {
    "python": (".py",),
    "typescript": (".ts", ".tsx", ".mts"),
    "go": (".go",),
}

# Skip directories that never contain config (saves walk time + avoids
# false positives in vendored dependencies).
_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".tox",
    "vendor", "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
})


def scan_service(
    service_root: Path,
    language: str,
    catalog: list[Pattern],
) -> ScanResult | None:
    """Walk a service directory and return the best env-loader-pattern match
    found, or None if no file passes the LOW threshold.

    Conflict resolution: the highest-priority pattern wins. If two files
    match the SAME pattern, the deepest match (most-specific path) wins —
    `app/config.py` beats `app/legacy/old_config.py`.
    """
    by_lang = group_patterns_by_language(catalog)
    patterns = by_lang.get(language, [])
    if not patterns:
        return None

    extensions = _EXTENSION_BY_LANGUAGE.get(language, ())
    if not extensions:
        return None

    candidates: list[ScanResult] = []
    for path in service_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix not in extensions:
            continue
        hit = scan_file(path, patterns)
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return None

    # Sort by: confidence_score desc, then by priority of the matched
    # pattern desc, then by path depth asc (shallower path = more canonical
    # config location).
    priority_by_id = {p.id: p.priority for p in catalog}

    def sort_key(r: ScanResult) -> tuple:
        depth = r.file.count("/")
        return (
            -r.confidence_score,
            -priority_by_id.get(r.pattern_id, 0),
            depth,
        )

    candidates.sort(key=sort_key)
    return candidates[0]


# ──────────────────────────────────────────────────────────────────────
# Deployment-surface scan
# ──────────────────────────────────────────────────────────────────────

@dataclass
class DeploymentSurface:
    kind: str          # "terraform" | "k8s" | "docker-compose" | "dotenv_example" | "dockerfile"
    path: str          # repo-relative path
    insert_kind: str   # "variable_block_append" | "secret_ref_checklist" |
                       # "environment_block_append" | "line_append" | "checklist_only"


# Per-surface skip dirs are MORE permissive than _SKIP_DIRS — we explicitly
# want to scan infra/, deployment/, k8s/, etc.
_SURFACE_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", "vendor"})


def scan_deployment_surfaces(repo_root: Path) -> list[DeploymentSurface]:
    """Walk the repo for deployment-surface insertion points. Each detected
    surface becomes one entry; the instrument side decides per-entry whether
    to emit a stub file, append to an existing file, or emit a CHECKLIST
    comment.

    Recognition rules (all non-destructive — no file modification here):
      - Terraform: any `variable "..." {}` block in a *.tf file
      - k8s: Deployment / StatefulSet / DaemonSet manifests with envFrom: secretRef
      - docker-compose: `services.<X>.environment:` block in compose yaml
      - .env.example / .env.sample: presence
      - Dockerfile: ENV lines (security smell — checklist only)
    """
    out: list[DeploymentSurface] = []

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SURFACE_SKIP_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(repo_root))

        # Terraform
        if path.suffix == ".tf":
            text = path.read_text(errors="ignore")
            if re.search(r'variable\s+"[^"]+"\s*\{', text):
                out.append(DeploymentSurface(
                    kind="terraform",
                    path=rel,
                    insert_kind="variable_block_append",
                ))
            continue

        # Kubernetes manifests — must be a Deployment/StatefulSet/DaemonSet
        # AND already wire env vars via envFrom: secretRef. A bare workload
        # manifest that doesn't reference a secret was previously over-detected
        # (docstring promised envFrom check; implementation didn't). The fix
        # narrows the recognition so Phase B's checklist hints only fire when
        # the customer is already using the secret-ref pattern we'd extend.
        if path.suffix in (".yaml", ".yml"):
            text = path.read_text(errors="ignore")
            has_workload_kind = bool(re.search(
                r'^\s*kind:\s*(Deployment|StatefulSet|DaemonSet)\b',
                text, re.MULTILINE,
            ))
            has_envfrom_secretref = bool(re.search(
                r'envFrom:[\s\S]{0,200}secretRef:', text,
            ))
            if has_workload_kind and has_envfrom_secretref:
                out.append(DeploymentSurface(
                    kind="k8s",
                    path=rel,
                    insert_kind="secret_ref_checklist",
                ))
                continue
            # docker-compose detection by filename — accept the standard
            # filenames PLUS env-/profile-suffix variants like
            # `docker-compose.prod.yaml`, `compose.staging.yml` that real
            # repositories ship.
            name = path.name
            is_compose = (
                (name.startswith("docker-compose") or name.startswith("compose"))
                and name.endswith((".yml", ".yaml"))
            )
            if is_compose:
                if re.search(r'^\s*environment:\s*$', text, re.MULTILINE) or \
                   re.search(r'^\s*environment:\s*\[', text, re.MULTILINE):
                    out.append(DeploymentSurface(
                        kind="docker-compose",
                        path=rel,
                        insert_kind="environment_block_append",
                    ))
            continue

        # .env.example / .env.sample
        if path.name in {".env.example", ".env.sample"}:
            out.append(DeploymentSurface(
                kind="dotenv_example",
                path=rel,
                insert_kind="line_append",
            ))
            continue

        # Dockerfile
        if path.name == "Dockerfile" or path.name.startswith("Dockerfile."):
            text = path.read_text(errors="ignore")
            if re.search(r'^\s*ENV\s+\w+', text, re.MULTILINE):
                out.append(DeploymentSurface(
                    kind="dockerfile",
                    path=rel,
                    insert_kind="checklist_only",
                ))

    return out


# ──────────────────────────────────────────────────────────────────────
# Inventory build
# ──────────────────────────────────────────────────────────────────────

def _service_entry(
    repo_root: Path,
    service: dict,
    scan_root: Path,
    catalog: list[Pattern],
) -> dict:
    """Build one services[] entry from a scan_service result + deployment
    surfaces under the service's path."""
    language = service.get("language", "python")
    result = scan_service(scan_root, language, catalog)

    if result is None:
        app_config = {
            "pattern": "unrecognized",
            "confidence": "none",
            "evidence": [],
            "stub_required": True,
        }
    else:
        rel_file = str(Path(result.file).relative_to(repo_root)) \
            if Path(result.file).is_relative_to(repo_root) else result.file
        app_config = {
            "pattern": result.pattern_id,
            "file": rel_file,
            "line_to_insert": result.line_to_insert,
            "confidence": result.confidence,
            "confidence_score": round(result.confidence_score, 2),
            "evidence": result.evidence,
            "stub_required": result.confidence == "low",
            "wire_target": result.wire_target,
        }

    # Deployment surfaces scoped to the SERVICE's path (not the whole repo).
    service_path = repo_root / service["root"]
    surfaces = scan_deployment_surfaces(service_path) if service_path.exists() else []

    return {
        "service_slug": service["slug"],
        "app_config": app_config,
        "deployment_surfaces": [
            {"kind": s.kind, "path": s.path, "insert_kind": s.insert_kind}
            for s in surfaces
        ],
    }


def build_inventory(
    repo_root: Path,
    services: list[dict],
    catalog: list[Pattern],
    granularity: str,
    granularity_source: str,
    shared_config_path: str | None,
) -> dict:
    """Build the env-routing-inventory dict that will be YAML-emitted.

    Granularity behavior:
      - per-service:  scan each service's root independently
      - repo-wide:    scan ONLY shared_config_path; every service entry
                      points at the same file
      - hybrid:       per-service for services not in the shared set;
                      shared_config_path for the rest (out of scope for
                      Phase A — falls back to per-service)
      - TBD:          per-service best-effort with granularity_source flag
    """
    if granularity == "repo-wide" and shared_config_path:
        scan_root = repo_root / shared_config_path
        service_entries: list[dict] = []
        for svc in services:
            entry = _service_entry(repo_root, svc, scan_root, catalog)
            service_entries.append(entry)
    else:
        # per-service (or TBD / hybrid → per-service for Phase A)
        service_entries = []
        for svc in services:
            svc_root = repo_root / svc["root"]
            entry = _service_entry(repo_root, svc, svc_root, catalog)
            service_entries.append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "granularity": granularity,
        "granularity_source": granularity_source,
        "services": service_entries,
    }


# ──────────────────────────────────────────────────────────────────────
# YAML emit (hand-rolled, matches sdk_snapshot.py convention)
# ──────────────────────────────────────────────────────────────────────

def emit_inventory_yaml(inventory: dict, dest: Path) -> None:
    """Hand-rolled YAML emit for env-routing-inventory.yaml. Avoids PyYAML
    runtime dep for the customer codemod environment."""
    lines: list[str] = []
    lines.append(f"generated_at: {inventory['generated_at']}")
    lines.append(f"granularity: {inventory['granularity']}")
    lines.append(f"granularity_source: {inventory['granularity_source']}")
    if not inventory["services"]:
        lines.append("services: []")
    else:
        lines.append("services:")
        for svc in inventory["services"]:
            lines.append(f"  - service_slug: {svc['service_slug']}")
            ac = svc["app_config"]
            lines.append(f"    app_config:")
            lines.append(f"      pattern: {ac['pattern']}")
            if ac.get("file"):
                lines.append(f"      file: {ac['file']}")
                lines.append(f"      line_to_insert: {ac['line_to_insert']}")
                lines.append(f"      confidence: {ac['confidence']}")
                lines.append(f"      confidence_score: {ac['confidence_score']}")
            else:
                lines.append(f"      confidence: {ac['confidence']}")
            lines.append(f"      stub_required: {str(ac['stub_required']).lower()}")
            if ac.get("evidence"):
                lines.append(f"      evidence:")
                for e in ac["evidence"]:
                    # Escape BOTH backslashes AND quotes for YAML
                    # double-quoted scalars. Backslash must come FIRST or the
                    # later quote-escape backslash gets double-escaped. Without
                    # the backslash escape, source lines containing Windows
                    # paths or regex literals (e.g. `\w+`) produce YAML that
                    # PyYAML reads as `\n` → newline, `\t` → tab, etc.
                    e_safe = e.replace('\\', '\\\\').replace('"', '\\"')
                    lines.append(f'        - "{e_safe}"')
            if ac.get("wire_target"):
                lines.append(f"      wire_target:")
                for k, v in ac["wire_target"].items():
                    v_str = str(v).replace('\\', '\\\\').replace('"', '\\"')
                    lines.append(f'        {k}: "{v_str}"')

            if svc["deployment_surfaces"]:
                lines.append(f"    deployment_surfaces:")
                for s in svc["deployment_surfaces"]:
                    lines.append(f"      - kind: {s['kind']}")
                    lines.append(f"        path: {s['path']}")
                    lines.append(f"        insert_kind: {s['insert_kind']}")
            else:
                lines.append(f"    deployment_surfaces: []")

    dest.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# Signed-yaml parser (read services + env_loader_granularity)
# ──────────────────────────────────────────────────────────────────────

def parse_services_and_granularity(signed_yaml_path: Path) -> tuple[list[dict], str, str, str | None]:
    """Read `04-final.signed.yaml` and return:
      (services, env_loader_granularity, granularity_source, shared_config_path)

    Defaults: granularity="TBD", source="default-fallback" if absent.
    """
    if not signed_yaml_path.exists():
        return [], "TBD", "default-fallback", None
    try:
        import yaml
        data = yaml.safe_load(signed_yaml_path.read_text()) or {}
    except ImportError:
        # Phase A leans on PyYAML; documented in the module docstring.
        return [], "TBD", "default-fallback", None

    integration = data.get("integration") or {}
    services_raw = integration.get("services") or []
    services: list[dict] = []
    for s in services_raw:
        services.append({
            "slug": s.get("slug") or s.get("service_slug") or "",
            "root": s.get("root") or s.get("path") or "",
            "language": s.get("language") or "python",
        })

    granularity = integration.get("env_loader_granularity")
    if granularity:
        return services, granularity, "declared", integration.get("shared_config_path")
    return services, "TBD", "default-fallback", None


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--catalog", required=True, help="path to env-loader-patterns.yaml")
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    catalog = load_pattern_catalog(Path(args.catalog))
    services, granularity, granularity_source, shared_config_path = \
        parse_services_and_granularity(Path(args.signed_yaml))

    if not services:
        print(
            "WARNING: no services found in 04-final.signed.yaml. "
            "Inventory will have an empty services list.",
            file=sys.stderr,
        )

    inventory = build_inventory(
        repo_root=repo_root,
        services=services,
        catalog=catalog,
        granularity=granularity,
        granularity_source=granularity_source,
        shared_config_path=shared_config_path,
    )

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "env-routing-inventory.yaml"
    emit_inventory_yaml(inventory, out_path)
    print(f"wrote {out_path}", file=sys.stderr)

    # No exit code 2 / refuse-to-run in Phase A — the stub_required flag
    # downstream handles the unrecognized-pattern case. The codemod
    # adversarial review surfaces low-confidence entries.
    return 0


if __name__ == "__main__":
    sys.exit(main())
