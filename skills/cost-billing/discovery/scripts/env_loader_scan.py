#!/usr/bin/env python3
"""Phase 1.7-scan — env-loader pattern detection for /cost-billing-discovery.

Scans each service's source tree for recognized env-loading patterns
(pydantic-settings, dotenv, viper, etc.). Produces
.moolabs/customer-context/env-routing-inventory.yaml describing the
recognized pattern (or stub_required=true when none found) and the
deployment-surface insertion points (Terraform, k8s, docker-compose,
.env.example).

Detection is driven by the framework-capability tree (the framework-node
registry) at shared/assets/frameworks/<lang>/<fw>.yaml — the single source of
truth for env-loader patterns.

Usage:
    python env_loader_scan.py \\
        --signed-yaml .moolabs/chain/04-final.signed.yaml \\
        --customer-context-dir .moolabs/customer-context \\
        [--repo-root .]
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# The transitive pydantic-settings base-resolution detector lives in the shared
# framework-capability tree (shared/scripts/strategies.py), exposed via its
# DETECTORS registry. env_loader_scan reuses it through a thin ScanResult shim.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shared" / "scripts"))
import strategies  # noqa: E402
import framework_registry  # noqa: E402

# Framework-capability tree node files. load_registry(_FRAMEWORKS_DIR) returns
# {language: {framework: Node}}; each Node carries detection.kind ("regex" |
# "code"), detection.import_signals/structural_signals/priority (regex nodes),
# and detection.detector (code nodes — a key into strategies.DETECTORS).
_FRAMEWORKS_DIR = Path(__file__).resolve().parents[2] / "shared" / "assets" / "frameworks"


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


# Confidence-band thresholds.
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
                    if inner_stripped in {"}", "};", "})", "});", "}))", "}));"}:
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


# ──────────────────────────────────────────────────────────────────────
# Transitive pydantic-settings detection (Dogfood #1a) — moved to
# shared/scripts/strategies.py (DETECTORS registry). The detection LOGIC now
# lives there and returns a bool; this thin shim reconstructs the ScanResult
# that the registry scan expects, using the local _python_insert_line for the
# class's insertion line. See strategies._first_transitive_settings_class.
# ──────────────────────────────────────────────────────────────────────


def _detect_settings_subclass(
    path: Path, text: str, search_roots: list[Path],
) -> ScanResult | None:
    """If `path` defines a class that transitively extends BaseSettings via a
    project base (resolved across files), return a high-confidence
    python-pydantic-settings-subclass ScanResult, else None. Classes that
    extend BaseSettings DIRECTLY are left to the precise v1/v2 regex patterns
    (which carry the modify-mode accessor).

    Detection is delegated to strategies; this shim wraps the matched class +
    bases back into the ScanResult shape the registry scan consumes."""
    match = strategies._first_transitive_settings_class(path, text, search_roots)
    if match is None:
        return None
    cname, bases = match
    line = _python_insert_line(text, rf"class\s+{re.escape(cname)}\s*\(")
    return ScanResult(
        pattern_id="python-pydantic-settings-subclass",
        file=str(path),
        line_to_insert=line,
        confidence="high",
        confidence_score=0.95,
        evidence=[
            f"class {cname}({', '.join(bases)}) -> BaseSettings (transitive)"
        ],
        wire_target={"kind": "add_pydantic_settings_field"},
    )


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


def _is_excluded_candidate(path: Path) -> bool:
    """Path-only candidate-exclusion predicate used by
    scan_service_via_registry. Returns True iff `path` must NOT be considered as
    the customer's app-config source.

    NOTE: caller still applies `path.is_file()` (first) and the per-language
    extension filter inline — those aren't path-name-only and don't belong
    in this predicate.

    Excludes:
      - any segment in _SKIP_DIRS (vendored deps, build dirs, VCS, caches)
      - test / smoke files across ALL languages (a stray env access in a
        test must never outrank the real settings module)
      - the skill's OWN previously-emitted artifacts
        (moolabs_settings.* / moolabs_client.* / slugs_*.*) — else a re-run
        would wire the codemod into its own output (circular)
    """
    if any(part in _SKIP_DIRS for part in path.parts):
        return True
    _nm = path.name
    if (
        _nm.startswith("test_")                                  # py
        or _nm == "conftest.py"                                  # py
        or re.search(r"_test\.(py|go)$", _nm)                    # py / go
        or re.search(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$", _nm)  # ts / js
        or ({"tests", "test", "__tests__", "spec", "specs", "e2e", "__mocks__"} & set(path.parts))
    ):
        return True
    if (
        re.match(r"^moolabs_settings\.(py|ts|go)$", _nm)
        or re.match(r"^moolabs_client\.(py|ts|go)$", _nm)
        or re.match(r"^slugs_[\w-]+\.(py|ts|go)$", _nm)
    ):
        return True
    return False


# ──────────────────────────────────────────────────────────────────────
# Registry-driven scan (framework-capability tree)
# ──────────────────────────────────────────────────────────────────────
#
# Drives off framework_registry Nodes (shared/assets/frameworks/<lang>/<fw>.yaml)
# — the single source of truth for env-loader detection. Reuses the
# _signal_score banding, the code-detector fallback, the _is_excluded_candidate
# exclusions, and the tiebreak (confidence_score desc -> detection.priority desc
# -> path depth asc).


def scan_file_via_registry(
    path: Path,
    language: str,
    nodes: list | None = None,
    search_roots: list[Path] | None = None,
) -> ScanResult | None:
    """Scan a single file against the language's framework-tree Nodes. Returns
    the best ScanResult (whose pattern_id is the winning node's id), or None.

    Two-phase:
      1. REGEX nodes, evaluated priority-desc, banded by _signal_score:
         import + structural -> 0.95 (high); structural-only -> 0.65 (medium);
         import-only -> 0.35 (low); below _LOW_THRESHOLD -> skip. Strict-greater
         keeps the first node on a score tie (so the higher-priority node wins).
      2. CODE nodes, run only as a fallback when no regex node reached the HIGH
         band (best is None or best.confidence_score < _HIGH_THRESHOLD), and
         only replacing when strictly greater. The detector
         (strategies.DETECTORS[...]) returns bool; the matched class name (for
         line_to_insert) and bases come from _first_transitive_settings_class.
    """
    if nodes is None:
        reg = framework_registry.load_registry(_FRAMEWORKS_DIR)
        nodes = list(reg.get(language, {}).values())
    if not nodes:
        return None

    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return None

    regex_nodes = [n for n in nodes if n.detection.get("kind") == "regex"]
    code_nodes = [n for n in nodes if n.detection.get("kind") == "code"]

    # Phase 1 — regex band scoring, highest priority first (so a same-score tie
    # is won by the higher-priority node, since we keep on strict-greater).
    regex_nodes.sort(key=lambda n: -int(n.detection.get("priority", 0)))

    best: ScanResult | None = None
    best_score: float = 0.0

    for node in regex_nodes:
        import_signals = list(node.detection.get("import_signals", []) or [])
        structural_signals = list(node.detection.get("structural_signals", []) or [])
        import_score, import_evidence = _signal_score(text, import_signals)
        struct_score, struct_evidence = _signal_score(text, structural_signals)

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
            line_to_insert = 1
            if structural_signals:
                if language == "python":
                    line_to_insert = _python_insert_line(text, structural_signals[0])
                elif language == "typescript":
                    line_to_insert = _ts_insert_line(text, structural_signals[0])
                elif language == "go":
                    line_to_insert = _go_insert_line(text, structural_signals[0])
                else:
                    line_to_insert = _python_insert_line(text, structural_signals[0])
            best = ScanResult(
                pattern_id=node.id,
                file=str(path),
                line_to_insert=line_to_insert,
                confidence=_band(score),
                confidence_score=score,
                evidence=import_evidence + struct_evidence,
                wire_target={},
            )

    # Phase 2 — code detectors, only when no regex node reached the HIGH band.
    if best is None or best.confidence_score < _HIGH_THRESHOLD:
        for node in code_nodes:
            detector_name = node.detection.get("detector")
            detector = strategies.DETECTORS.get(detector_name)
            if detector is None:
                continue
            roots = search_roots or [path.parent]
            if not detector(path, text, roots):
                continue
            # Recover the matched class name for the insertion line. The
            # detectors are bool-returning; _first_transitive_settings_class
            # gives back (class_name, bases) for the same match.
            match = strategies._first_transitive_settings_class(path, text, roots)
            line_to_insert = 1
            evidence: list[str] = []
            if match is not None:
                cname, bases = match
                line_to_insert = _python_insert_line(
                    text, rf"class\s+{re.escape(cname)}\s*\("
                )
                evidence = [
                    f"class {cname}({', '.join(bases)}) -> BaseSettings (transitive)"
                ]
            sub = ScanResult(
                pattern_id=node.id,
                file=str(path),
                line_to_insert=line_to_insert,
                confidence="high",
                confidence_score=0.95,
                evidence=evidence,
                wire_target={},
            )
            if best is None or sub.confidence_score > best.confidence_score:
                best = sub

    return best


def scan_service_via_registry(
    service_root: Path,
    language: str,
    search_roots: list[Path] | None = None,
) -> ScanResult | None:
    """Walk a service directory and return the best framework-tree node match,
    or None. Exclusions (_is_excluded_candidate), per-language extension filter,
    and tiebreak (confidence_score desc -> node detection.priority desc -> path
    depth asc).
    """
    reg = framework_registry.load_registry(_FRAMEWORKS_DIR)
    nodes = list(reg.get(language, {}).values())
    if not nodes:
        return None

    extensions = _EXTENSION_BY_LANGUAGE.get(language, ())
    if not extensions:
        return None

    candidates: list[ScanResult] = []
    for path in service_root.rglob("*"):
        if not path.is_file():
            continue
        if _is_excluded_candidate(path):
            continue
        if path.suffix not in extensions:
            continue
        hit = scan_file_via_registry(
            path, language, nodes=nodes,
            search_roots=search_roots or [service_root],
        )
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return None

    # Priority comes straight from each node's detection.priority — the code
    # subclass node carries its declared 85 (no setdefault hack needed; the
    # node IS in the registry now, unlike the old catalog).
    priority_by_id = {n.id: int(n.detection.get("priority", 0)) for n in nodes}

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
    scope: str = "service"  # "service" (under services/<svc>/) | "repo" (centralized at repo root)


# Per-surface skip dirs are MORE permissive than _SKIP_DIRS — we explicitly
# want to scan infra/, deployment/, k8s/, etc.
#
# `.terraform` / `.terragrunt-cache` MUST be skipped (PR #7 review IMPORTANT):
# `terraform init` copies module SOURCES into `.terraform/modules/<name>/*.tf`
# and terragrunt copies the full module tree into `.terragrunt-cache/<hash>/`.
# These are machine-generated, gitignored, and NOT customer-editable — a dev
# who ran `terraform init` locally would otherwise see these vendored copies
# pulled in as false-positive repo-scope terraform surfaces, producing
# CHECKLIST entries pointing at paths that don't exist in the committed tree
# AND falsely clearing infra_discovery_gap for a repo with no real IaC.
_SURFACE_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", "vendor",
    ".terraform", ".terragrunt-cache",
})

# Repo-root directories that may hold CENTRALIZED infrastructure for monorepos
# (instead of, or in addition to, per-service infra). moolabs is the canonical
# centralized-infra example: `infrastructure/terraform/{modules,environments,
# regional,global,accounts}/` defines shared Terraform that every service
# inherits via module composition; no `services/<svc>/infra/` exists.
#
# When a service is scanned, we ALSO walk these repo-root dirs and tag any
# surfaces found with scope="repo" so the instrument layer can emit a
# CHECKLIST entry (rather than auto-modifying centralized infra, which would
# affect every service simultaneously and risks cross-service blast radius).
_REPO_LEVEL_INFRA_DIRS = frozenset({
    "infrastructure", "infra", "terraform", "tf",
    "deploy", "deployment", "deployments",
    "k8s", "kubernetes", "helm", "charts",
    "ops",
})


def _envfrom_secretref_in_same_container(text: str) -> bool:
    """True iff `envFrom:` is followed (within the same container block) by
    `- secretRef:`. The naive `envFrom:[\\s\\S]{0,200}secretRef:` regex was
    too loose: in multi-container manifests where container A had
    `envFrom: - configMapRef:` and container B had `valueFrom: secretRef:`
    a few hundred chars later, the regex matched across the container
    boundary and produced a false-positive k8s surface entry.

    Approach: walk lines. For each `envFrom:` line, scan subsequent lines
    looking for `- secretRef:` that belongs to envFrom's list. YAML allows
    the list items to live at the SAME indent as the key (common k8s
    style) OR deeper. The envFrom block ends when we see:
      - any non-list line (no leading dash) at indent <= envfrom_indent
        (sibling key at the container level, e.g. `volumes:`)
      - a `- name:` line (next container in the parent list)
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r'^(\s*)envFrom:\s*$', line)
        if not m:
            continue
        envfrom_indent = len(m.group(1))
        for j in range(i + 1, len(lines)):
            sub = lines[j]
            if not sub.strip():
                continue
            sub_indent = len(sub) - len(sub.lstrip(" "))
            stripped = sub.lstrip()
            # `- name:` at any indent <= envfrom_indent indicates a new
            # container (the parent list's next item). End envFrom block.
            if stripped.startswith("- name:") and sub_indent <= envfrom_indent:
                break
            # A non-list line at indent <= envfrom_indent is a sibling key
            # at the container level (or shallower). End envFrom block.
            if not stripped.startswith("-") and sub_indent <= envfrom_indent:
                break
            # Found a `- secretRef:` somewhere inside envFrom's list.
            if re.match(r'-\s*secretRef:', stripped):
                return True
    return False


def scan_deployment_surfaces(
    repo_root: Path,
    scope: str = "service",
    path_anchor: Path | None = None,
) -> list[DeploymentSurface]:
    """Walk the repo for deployment-surface insertion points. Each detected
    surface becomes one entry; the instrument side decides per-entry whether
    to emit a stub file, append to an existing file, or emit a CHECKLIST
    comment.

    Args:
      repo_root: dir to walk (a service path for scope=service, a repo-level
                 infra dir like `infrastructure/` for scope=repo).
      scope: tagged onto each emitted surface so the instrument layer can
             distinguish service-scoped surfaces (safe to auto-modify) from
             repo-scoped centralized infra (CHECKLIST only — modifying
             centralized Terraform affects every service simultaneously).
      path_anchor: when present, relative paths in emitted DeploymentSurfaces
                   are computed against this anchor instead of `repo_root`.
                   Used for repo-scope scans so the emitted path is
                   `infrastructure/terraform/...` (repo-relative) rather
                   than `terraform/...` (anchor-relative). Defaults to
                   `repo_root` when None.

    Recognition rules (all non-destructive — no file modification here):
      - Terraform: any `variable "..." {}` block in a *.tf file
      - k8s: Deployment / StatefulSet / DaemonSet manifests with envFrom: secretRef
      - docker-compose: `services.<X>.environment:` block in compose yaml
      - .env.example / .env.sample: presence
      - Dockerfile: ENV lines (security smell — checklist only)
    """
    out: list[DeploymentSurface] = []
    anchor = path_anchor if path_anchor is not None else repo_root

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SURFACE_SKIP_DIRS for part in path.parts):
            continue
        try:
            rel = str(path.relative_to(anchor))
        except ValueError:
            # path isn't under the anchor — warn-and-skip rather than emit a
            # machine-absolute path into a customer-committed inventory YAML
            # (PR #7 review MINOR). Unreachable with current callers (rglob
            # only yields paths under repo_root, and anchor is always an
            # ancestor), but a future caller passing a non-ancestor anchor
            # would otherwise silently leak absolute paths. Skip loudly.
            sys.stderr.write(
                f"WARN: deployment-surface scan skipping {path} — not under "
                f"anchor {anchor} (would emit a non-repo-relative path)\n"
            )
            continue

        # Terraform
        if path.suffix == ".tf":
            # NOTE: the repo-level scan intentionally finds ALL centralized
            # terraform variable blocks (incl. modules/secrets) — that is the
            # designed comprehensive surface inventory. In a large centralized
            # infra this can be many files; SCOPING to the service's actual
            # deployment point is the instrument layer's responsibility (config_wire
            # / the codemod), NOT a blanket dir-skip here (which would drop the
            # legitimate secrets module). See handoff: env-wiring scoping.
            text = path.read_text(errors="ignore")
            if re.search(r'variable\s+"[^"]+"\s*\{', text):
                out.append(DeploymentSurface(
                    kind="terraform",
                    path=rel,
                    insert_kind="variable_block_append",
                    scope=scope,
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
            has_envfrom_secretref = _envfrom_secretref_in_same_container(text)
            if has_workload_kind and has_envfrom_secretref:
                out.append(DeploymentSurface(
                    kind="k8s",
                    path=rel,
                    insert_kind="secret_ref_checklist",
                    scope=scope,
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
                        scope=scope,
                    ))
            continue

        # .env.example / .env.sample
        if path.name in {".env.example", ".env.sample"}:
            out.append(DeploymentSurface(
                kind="dotenv_example",
                path=rel,
                insert_kind="line_append",
                scope=scope,
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
                    scope=scope,
                ))

    return out


def scan_repo_level_deployment_surfaces(repo_root: Path) -> list[DeploymentSurface]:
    """Scan centralized-infra dirs at the repo root (per `_REPO_LEVEL_INFRA_DIRS`).
    Each result is tagged scope="repo" so the instrument layer can emit a
    CHECKLIST entry instead of auto-modifying — centralized Terraform/k8s
    affects every service simultaneously and shouldn't be touched by a
    per-service codemod.

    Catches the moolabs-shape monorepo: `infrastructure/terraform/modules/
    secrets/variables.tf` is shared by every service; without this scan it
    was invisible to env_loader_scan and PR #531 shipped without any
    Terraform stub for moolabs' actual infra layer.
    """
    out: list[DeploymentSurface] = []
    for dir_name in _REPO_LEVEL_INFRA_DIRS:
        candidate = repo_root / dir_name
        if not candidate.is_dir():
            continue
        # path_anchor=repo_root keeps emitted paths repo-relative
        # (e.g. "infrastructure/terraform/...") rather than truncated
        # to the infra-dir-relative form ("terraform/...").
        out.extend(scan_deployment_surfaces(
            candidate, scope="repo", path_anchor=repo_root,
        ))
    return out


# ──────────────────────────────────────────────────────────────────────
# Inventory build
# ──────────────────────────────────────────────────────────────────────

# File extension per language — used to spell the derived emit artifact's
# filename (basename + ext). Mirrors the language axis of _EXTENSION_BY_LANGUAGE
# but yields the SINGLE canonical write extension (not the candidate-match set).
_EMIT_EXT_BY_LANGUAGE = {
    "python": ".py",
    "typescript": ".ts",
    "go": ".go",
}

# Conventional fallback (service-relative) emit dir + import path per language,
# used ONLY when no config is detected (unrecognized). Matches the framework
# nodes' emit.fallback_dir convention so a detected-vs-fallback artifact lands
# in the same place a recognized config would route to.
_FALLBACK_EMIT_BY_LANGUAGE = {
    "python": ("app/services", "moolabs_settings", "app.services.moolabs_settings"),
    "typescript": ("src/services", "moolabs_settings", "@/services/moolabs_settings"),
    "go": ("internal/moolabsconfig", "settings", "internal/moolabsconfig"),
}


def _derive_emit_path(service_root_rel: str, emit_dir_rel: str, filename: str) -> str:
    """Join service_root + service-relative emit dir + filename into a single
    forward-slash repo-relative path, collapsing empty segments. A flat layout
    (emit_dir_rel == "") yields service_root/filename."""
    parts = [seg for seg in (service_root_rel.rstrip("/"), emit_dir_rel, filename) if seg]
    return "/".join(parts)


def _service_entry(
    repo_root: Path,
    service: dict,
    scan_root: Path,
    catalog=None,
) -> dict:
    """Build one services[] entry from a registry scan result + deployment
    surfaces under the service's path.

    Detection runs off the framework-capability tree (scan_service_via_registry)
    so each entry carries the winning node's id AND the DERIVED artifact paths
    (emit_path / import_path) computed via the node's emit rule. The legacy
    `catalog` arg is accepted but IGNORED — existing callers still pass one;
    Task 9 removes the parameter."""
    language = service.get("language", "python")
    service_root_rel = service["root"]
    # Emit/import anchor = the scan_root's repo-relative path, NOT service["root"].
    # In per-service granularity these are equal (scan_root == repo_root/service
    # root). In repo-wide granularity scan_root == repo_root/shared_config_path,
    # and the stub must land beside the SHARED config (where its import resolves),
    # not under each service tree — otherwise emit_path and import_path describe
    # different trees and the customer-side import fails (PR #11 review F1).
    scan_root_rel = (
        str(scan_root.relative_to(repo_root))
        if scan_root.is_relative_to(repo_root)
        else service_root_rel
    )
    # search_roots include the repo root so a Settings base imported from a
    # shared repo-level package (outside the service tree) still resolves for
    # the transitive pydantic-settings detection (Dogfood #1a).
    result = scan_service_via_registry(
        scan_root, language, search_roots=[scan_root, repo_root]
    )

    if result is None:
        # No config detected → fallback artifact placement (spec: "fallback only
        # when no config detected"). The per-language conventional dir matches
        # the framework nodes' emit.fallback_dir.
        fb_dir, fb_basename, fb_import = _FALLBACK_EMIT_BY_LANGUAGE.get(
            language, _FALLBACK_EMIT_BY_LANGUAGE["python"]
        )
        ext = _EMIT_EXT_BY_LANGUAGE.get(language, ".py")
        app_config = {
            "pattern": "unrecognized",
            "node_id": "",
            "confidence": "none",
            "evidence": [],
            "stub_required": True,
            "emit_path": _derive_emit_path(scan_root_rel, fb_dir, fb_basename + ext),
            "import_path": fb_import,
        }
    else:
        rel_file = str(Path(result.file).relative_to(repo_root)) \
            if Path(result.file).is_relative_to(repo_root) else result.file
        node_id = result.pattern_id

        # Locate the winning node in the registry to read its emit rule. The
        # scan already loaded the registry; re-load here (load_registry re-globs
        # + re-parses the node files — Task 9 can thread the loaded registry
        # through to avoid the second load) and find the node by id within the
        # service's language.
        reg = framework_registry.load_registry(_FRAMEWORKS_DIR)
        node = next(
            (n for n in reg.get(language, {}).values() if n.id == node_id), None
        )

        emit_path = None
        import_path = None
        if node is not None:
            # SCAN-ROOT-RELATIVE config path: result.file lives under scan_root
            # (the scan rglob'd it), so relative_to(scan_root) is safe. The
            # import rule strips package-root markers (src/) from this; the emit
            # path re-anchors the SAME scan-root-relative dir under scan_root_rel
            # so emit_path and import_path always describe ONE consistent tree
            # (correct in both per-service and repo-wide granularity — F1).
            service_rel = str(Path(result.file).relative_to(scan_root))
            basename = node.emit["artifact_basename"]
            emit_dir_rel, import_path = strategies.IMPORT_RULES[
                node.emit["import_rule"]
            ](service_rel, basename, "")
            ext = _EMIT_EXT_BY_LANGUAGE.get(language, ".py")
            emit_path = _derive_emit_path(
                scan_root_rel, emit_dir_rel, basename + ext
            )

        app_config = {
            # Keep `pattern` == node_id for back-compat with consumers that read
            # `pattern`; expose `node_id` as the canonical new field too.
            "pattern": node_id,
            "node_id": node_id,
            "file": rel_file,
            "line_to_insert": result.line_to_insert,
            "confidence": result.confidence,
            "confidence_score": round(result.confidence_score, 2),
            "evidence": result.evidence,
            "stub_required": result.confidence == "low",
            "wire_target": result.wire_target,
            "emit_path": emit_path,
            "import_path": import_path,
        }

    # Deployment surfaces — BOTH scopes:
    #   - service-scope: walks services/<svc>/ (the per-service infra convention)
    #   - repo-scope: walks repo-root infrastructure/ infra/ terraform/ deploy/
    #     k8s/ helm/ etc. (the CENTRALIZED-INFRA convention used by monorepos
    #     like moolabs where one shared Terraform tree serves all services)
    # PR #531 against moolabs shipped without any Terraform stub because the
    # earlier service-only scan never saw infrastructure/terraform/ — fix
    # restored by including repo-scope surfaces tagged scope="repo".
    service_path = repo_root / service["root"]
    surfaces: list[DeploymentSurface] = []
    if service_path.exists():
        surfaces.extend(scan_deployment_surfaces(service_path, scope="service"))

    # PR #7 review IMPORTANT (Challenge 3): when a service root lives UNDER one
    # of the repo-level infra dirs (e.g. service at `deploy/myservice/`), the
    # repo-level scan would re-detect the same files the service-scope scan
    # already found — producing duplicate surfaces with conflicting scope tags
    # (service→new_file AND repo→checklist_only for the SAME physical file).
    # Drop any repo-scope surface whose path is under this service's root.
    # NOTE: repo-scope paths are repo-relative (e.g. "deploy/myservice/x.tf")
    # while service-scope paths are service-relative ("x.tf") — they never
    # string-match, so we compare against the service ROOT prefix, not the
    # service-scope path strings.
    service_root_rel = service["root"].rstrip("/")
    service_prefix = f"{service_root_rel}/"
    for s in scan_repo_level_deployment_surfaces(repo_root):
        if s.path == service_root_rel or s.path.startswith(service_prefix):
            continue  # already covered by the service-scope scan above
        surfaces.append(s)

    # Gap-detection: when no infra surface (terraform / k8s / dockerfile)
    # was found at EITHER scope, set infra_discovery_gap=True. The
    # instrument layer surfaces this in the PR body as a DEVELOPER ACTION
    # REQUIRED checklist asking where their IaC actually lives (covers
    # non-standard paths like iac/, cdk/, pulumi/, or repos where Terraform
    # is committed outside the conventional dirnames). Treats .env.example /
    # docker-compose alone as INSUFFICIENT — they don't reach production
    # secret-routing in most setups.
    infra_kinds = {"terraform", "k8s", "dockerfile"}
    has_infra = any(s.kind in infra_kinds for s in surfaces)

    return {
        "service_slug": service["slug"],
        "app_config": app_config,
        "infra_discovery_gap": not has_infra,
        "deployment_surfaces": [
            {"kind": s.kind, "path": s.path,
             "insert_kind": s.insert_kind, "scope": s.scope}
            for s in surfaces
        ],
    }


def build_inventory(
    repo_root: Path,
    services: list[dict],
    granularity: str,
    granularity_source: str,
    shared_config_path: str | None,
    catalog=None,
) -> dict:
    """Build the env-routing-inventory dict that will be YAML-emitted.

    Detection is registry-driven via _service_entry. The trailing `catalog`
    arg is accepted for back-compat with existing callers but IGNORED.

    Granularity behavior:
      - per-service:  scan each service's root independently
      - repo-wide:    scan ONLY shared_config_path; every service entry
                      points at the same file
      - hybrid:       OUT OF SCOPE for Phase A. Logs a loud stderr WARNING
                      and degrades to per-service. The output YAML records
                      granularity="hybrid (degraded to per-service)" so
                      downstream consumers (and adversarial review) can
                      see the degradation happened.
      - TBD:          per-service best-effort with granularity_source flag
    """
    # Drop any stale source-file index from a prior in-process scan (F3): the
    # index is keyed by path only, so a server-style caller re-scanning a mutated
    # tree must start fresh. Within this run the index is rebuilt once per root.
    strategies.clear_index_cache()
    if granularity == "repo-wide" and shared_config_path:
        scan_root = repo_root / shared_config_path
        service_entries: list[dict] = []
        for svc in services:
            entry = _service_entry(repo_root, svc, scan_root, catalog)
            service_entries.append(entry)
        effective_granularity = granularity
    else:
        if granularity == "hybrid":
            # Hybrid is declared but Phase A doesn't implement the per-slug
            # split between shared and per-service services. Make the
            # degradation LOUD so the engineer notices.
            print(
                "WARNING: env_loader_granularity=hybrid is out-of-scope for "
                "Phase A. Falling back to per-service scanning. "
                "shared_config_path will NOT be honored. "
                "Track Phase B (config_wire.py) for hybrid support.",
                file=sys.stderr,
            )
            effective_granularity = "hybrid (degraded to per-service)"
        else:
            # per-service or TBD — both run per-service.
            effective_granularity = granularity
        service_entries = []
        for svc in services:
            svc_root = repo_root / svc["root"]
            entry = _service_entry(repo_root, svc, svc_root, catalog)
            service_entries.append(entry)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "granularity": effective_granularity,
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
    # Quote generated_at so PyYAML safe_load keeps it a string, not a datetime
    # (PR #8 review #3-sibling — same bug class as task_planner/config_wire).
    lines.append(f'generated_at: "{inventory["generated_at"]}"')
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
            # node_id is the canonical framework-tree node id. Presence-guarded
            # (`in`, not truthiness) so the unrecognized branch's empty-string
            # node_id still emits as `node_id: ""`, and hand-built test dicts
            # that omit it don't KeyError.
            if "node_id" in ac:
                nid = str(ac["node_id"]).replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'      node_id: "{nid}"')
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
            # Derived artifact placement (Task 8): emit_path is the REPO-relative
            # path the codemod writes the Settings artifact to; import_path is how
            # the customer's code imports it. Presence-guarded so hand-built test
            # dicts that omit them don't KeyError. Quote both (paths/dotted ids).
            if "emit_path" in ac:
                ep = str(ac["emit_path"]).replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'      emit_path: "{ep}"')
            if "import_path" in ac:
                ip = str(ac["import_path"]).replace('\\', '\\\\').replace('"', '\\"')
                lines.append(f'      import_path: "{ip}"')

            # PR #531 lesson: surface the gap when scanner found no infra
            # (no terraform / k8s / dockerfile at either scope). The
            # instrument layer reads this flag to emit a DEVELOPER ACTION
            # REQUIRED checklist in the PR body — "where does your IaC
            # live?" — covering non-standard paths like iac/, cdk/, pulumi/.
            lines.append(
                f"    infra_discovery_gap: "
                f"{str(svc.get('infra_discovery_gap', False)).lower()}"
            )
            if svc["deployment_surfaces"]:
                lines.append(f"    deployment_surfaces:")
                for s in svc["deployment_surfaces"]:
                    lines.append(f"      - kind: {s['kind']}")
                    # Quote path because customer-authored dir names can
                    # contain YAML metacharacters (rare but defensive).
                    path_safe = str(s['path']).replace('\\', '\\\\').replace('"', '\\"')
                    lines.append(f'        path: "{path_safe}"')
                    lines.append(f"        insert_kind: {s['insert_kind']}")
                    # scope distinguishes per-service (auto-modifiable)
                    # from centralized-infra (CHECKLIST only — modifying
                    # shared infra has cross-service blast radius).
                    lines.append(f"        scope: {s.get('scope', 'service')}")
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
    ap.add_argument("--repo-root", default=".")
    args = ap.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
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
