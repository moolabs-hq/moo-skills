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
import os
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
# Transitive pydantic-settings detection (Dogfood #1a)
# ──────────────────────────────────────────────────────────────────────
#
# A real app config often extends a PROJECT base — `class Settings(CommonBase)`
# — where CommonBase (not the leaf) is the one that extends BaseSettings. The
# "env loader" is the BaseSettings inheritance ITSELF (it makes every field read
# from OS env vars); an `env_file` only loads a local .env for dev and is NOT a
# reliable signal. So we resolve the base chain transitively to BaseSettings,
# following imports across files — no modeling on any one repo's base name.

_PYDANTIC_SETTINGS_BASES = frozenset({"BaseSettings"})
_MAX_BASE_DEPTH = 8

# Per-root index of source .py files, built once via a PRUNED os.walk (never
# descends into vendored/build/VCS dirs — unlike Path.rglob, which walks them
# all and would take ~100s on a real monorepo). Used by the src-layout
# fallback in _resolve_module_files to locate a package by module-path suffix.
_PY_INDEX_CACHE: dict[str, list[str]] = {}
# Non-dotted heavy dirs to prune from the source walk. All DOT-prefixed dirs
# (.venv, .git, .tox, .terraform, .mypy_cache, …) are pruned separately via the
# `startswith(".")` check, so they need not be listed here.
_WALK_PRUNE_DIRS = frozenset({
    "node_modules", "venv", "site-packages", "vendor",
    "dist", "build", "__pycache__",
})


def _py_file_index(root: Path) -> list[str]:
    """All source .py file paths under `root`, via a pruned walk (cached)."""
    key = str(root)
    cached = _PY_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in-place so os.walk never descends into vendored/VCS/build dirs.
        dirnames[:] = [
            d for d in dirnames
            if d not in _WALK_PRUNE_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if fn.endswith(".py"):
                paths.append(os.path.join(dirpath, fn))
    # Sort for DETERMINISTIC src-layout suffix-match: os.walk order is
    # filesystem-dependent, so two same-named modules under different roots
    # would otherwise resolve differently across platforms. Shortest path first
    # (closest-to-root package wins the suffix tie).
    paths.sort(key=lambda p: (p.count(os.sep), p))
    _PY_INDEX_CACHE[key] = paths
    return paths

_CLASS_DEF_RE = re.compile(r"^[ \t]*class\s+(\w+)\s*\(([^)]*)\)\s*:", re.MULTILINE)
_FROM_IMPORT_RE = re.compile(
    r"^[ \t]*from\s+(\.*)([\w.]*)\s+import\s+(.+)$", re.MULTILINE
)
# Collapses `from x import (\n A,\n B,\n)` to one line. Non-greedy to the first
# closing paren; [\s\S] spans newlines.
_PAREN_IMPORT_RE = re.compile(
    r"(from\s+\.*[\w.]*\s+import\s*)\(([\s\S]*?)\)"
)


def _parse_class_bases(text: str) -> dict[str, list[str]]:
    """Return {class_name: [base simple-names]}. Base names are reduced to the
    simple identifier (module prefix + subscripts stripped):
    `pydantic_settings.BaseSettings` -> 'BaseSettings'; `Generic[T]` -> 'Generic'.
    Keyword bases (e.g. `metaclass=...`) are skipped."""
    out: dict[str, list[str]] = {}
    for m in _CLASS_DEF_RE.finditer(text):
        name = m.group(1)
        bases: list[str] = []
        for raw in m.group(2).split(","):
            raw = raw.strip()
            if not raw or "=" in raw:
                continue
            simple = raw.split("[")[0].strip().split(".")[-1].strip()
            if simple:
                bases.append(simple)
        out[name] = bases
    return out


def _parse_from_imports(text: str) -> dict[str, tuple[int, str, str]]:
    """Return {name-as-used-locally: (relative_level, module, original_name)} for
    `from ... import ...`. The original name matters for aliased imports — the
    class in the TARGET file carries the original name, not the alias:
    `from a.b import C` -> {'C': (0, 'a.b', 'C')};
    `from x.config import Settings as CommonSettings`
        -> {'CommonSettings': (0, 'x.config', 'Settings')};
    `from . import M` -> {'M': (1, '', 'M')}.

    Parenthesized multi-line imports (`from x import (\\n A,\\n B,\\n)`) are
    collapsed to a single line first so the line-anchored regex matches them."""
    text = _PAREN_IMPORT_RE.sub(
        lambda m: m.group(1) + " " + " ".join(m.group(2).split()),
        text,
    )
    out: dict[str, tuple[int, str, str]] = {}
    for m in _FROM_IMPORT_RE.finditer(text):
        level = len(m.group(1))
        module = m.group(2)
        names = m.group(3).split("#")[0]
        for part in names.split(","):
            part = part.strip().strip("()").strip()
            if not part:
                continue
            toks = part.split()
            orig = toks[0]
            alias = toks[2] if len(toks) >= 3 and toks[1] == "as" else orig
            out[alias] = (level, module, orig)
    return out


def _resolve_module_files(
    level: int, module: str, name: str,
    current_file: Path, search_roots: list[Path],
) -> list[Path]:
    """Map a `from [.]*module import name` to candidate .py files on disk."""
    candidates: list[Path] = []
    rel = module.replace(".", "/") if module else ""
    if level == 0:
        for root in search_roots:
            if rel:
                candidates.append(root / f"{rel}.py")
                candidates.append(root / rel / "__init__.py")
    else:
        base = current_file.parent
        for _ in range(level - 1):
            base = base.parent
        if rel:
            candidates.append(base / f"{rel}.py")
            candidates.append(base / rel / "__init__.py")
        else:
            # `from . import name` — name may be a submodule or in __init__
            candidates.append(base / f"{name}.py")
            candidates.append(base / "__init__.py")
    # Dedupe preserving order, keep only existing files.
    seen: set[str] = set()
    out: list[Path] = []
    for c in candidates:
        s = str(c)
        if s not in seen and c.is_file():
            seen.add(s)
            out.append(c)
    if out:
        return out

    # Fallback for src-layout / monorepo packages: an absolute import like
    # `python_common.config` often lives at `packages/<pkg>/src/python_common/
    # config.py` — a root the literal search_roots don't include. Find the file
    # by its full module-path SUFFIX anywhere under the broadest search root
    # (skipping vendored/build dirs). Bounded: only runs when direct resolution
    # failed, and stops at the first match.
    if level == 0 and rel:
        broadest = min(search_roots, key=lambda r: len(str(r))) if search_roots else None
        if broadest is not None:
            suffix_py = f"{os.sep}{rel.replace('/', os.sep)}.py"
            suffix_init = f"{os.sep}{rel.replace('/', os.sep)}{os.sep}__init__.py"
            for p in _py_file_index(broadest):
                if p.endswith(suffix_py) or p.endswith(suffix_init):
                    return [Path(p)]
    return out


def _class_reaches_basesettings(
    class_name: str, file_path: Path, search_roots: list[Path],
    visited: set, depth: int = 0,
) -> bool:
    """True iff `class_name` in `file_path` transitively extends BaseSettings,
    following same-file and imported bases. visited-set + depth cap guard
    against import cycles and runaway chains."""
    key = (str(file_path), class_name)
    if depth > _MAX_BASE_DEPTH or key in visited:
        return False
    visited.add(key)
    try:
        text = file_path.read_text(errors="ignore")
    except OSError:
        return False
    classes = _parse_class_bases(text)
    bases = classes.get(class_name)
    if bases is None:
        return False
    if any(b in _PYDANTIC_SETTINGS_BASES for b in bases):
        return True
    imports = _parse_from_imports(text)
    for b in bases:
        if b in classes and _class_reaches_basesettings(
            b, file_path, search_roots, visited, depth + 1
        ):
            return True
        if b in imports:
            level, module, orig = imports[b]
            # Recurse looking for the ORIGINAL class name in the target file —
            # `from x import Settings as CommonSettings` defines `Settings`
            # there, not `CommonSettings`.
            for f in _resolve_module_files(level, module, orig, file_path, search_roots):
                if _class_reaches_basesettings(orig, f, search_roots, visited, depth + 1):
                    return True
    return False


def _detect_settings_subclass(
    path: Path, text: str, search_roots: list[Path],
) -> ScanResult | None:
    """If `path` defines a class that transitively extends BaseSettings via a
    project base (resolved across files), return a high-confidence
    python-pydantic-settings-subclass ScanResult, else None. Classes that
    extend BaseSettings DIRECTLY are left to the precise v1/v2 regex patterns
    (which carry the modify-mode accessor)."""
    for cname, bases in _parse_class_bases(text).items():
        if not bases or any(b in _PYDANTIC_SETTINGS_BASES for b in bases):
            continue
        if _class_reaches_basesettings(cname, path, search_roots, set(), 0):
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
    return None


def scan_file(
    path: Path, patterns: list[Pattern], search_roots: list[Path] | None = None,
) -> ScanResult | None:
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

    # Transitive pydantic-settings-subclass detection (Dogfood #1a): only for
    # Python files, and only when no precise high-confidence regex pattern
    # already matched (a DIRECT BaseSettings subclass keeps its v1/v2 match +
    # modify accessor). Catches a Settings class that extends a project base.
    if path.suffix == ".py" and (best is None or best.confidence_score < _HIGH_THRESHOLD):
        sub = _detect_settings_subclass(path, text, search_roots or [path.parent])
        if sub is not None and (best is None or sub.confidence_score > best.confidence_score):
            best = sub

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
    search_roots: list[Path] | None = None,
) -> ScanResult | None:
    """Walk a service directory and return the best env-loader-pattern match
    found, or None if no file passes the LOW threshold.

    Conflict resolution (sort_key order):
      1. Highest confidence_score wins.
      2. Among equal scores, highest catalog priority wins.
      3. Among equal priority, the SHALLOWEST path wins (closest to the
         service root = most canonical config location).
         e.g. `app/config.py` beats `app/legacy/old_config.py`.
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
        # Test files are NEVER the canonical app-config source — across ALL
        # supported languages (Python/TS/JS/Go) a stray env access in a test
        # (os.getenv / process.env / os.Getenv) must not outrank the real
        # settings module. (Fixes the v0.3 misdetection of a test file over the
        # real config when the real Settings extends a custom base the pattern's
        # structural signal doesn't match.)
        _nm = path.name
        if (
            _nm.startswith("test_")                                  # py
            or _nm == "conftest.py"                                  # py
            or re.search(r"_test\.(py|go)$", _nm)                    # py / go
            or re.search(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$", _nm)  # ts / js
            or ({"tests", "test", "__tests__", "spec", "specs", "e2e", "__mocks__"} & set(path.parts))
        ):
            continue
        # The skill's OWN previously-emitted artifacts must never be detected as
        # the customer's config on a re-run — the stub Settings module IS a
        # BaseSettings subclass, so it would otherwise win and the codemod would
        # wire into its own output (circular). Skip them by their canonical
        # emitted basenames (language-agnostic).
        if (
            re.match(r"^moolabs_settings\.(py|ts|go)$", _nm)
            or re.match(r"^moolabs_client\.(py|ts|go)$", _nm)
            or re.match(r"^slugs_[\w-]+\.(py|ts|go)$", _nm)
        ):
            continue
        if path.suffix not in extensions:
            continue
        hit = scan_file(path, patterns, search_roots=search_roots or [service_root])
        if hit is not None:
            candidates.append(hit)

    if not candidates:
        return None

    # Sort by: confidence_score desc, then by priority of the matched
    # pattern desc, then by path depth asc (shallower path = more canonical
    # config location).
    priority_by_id = {p.id: p.priority for p in catalog}
    # The transitive subclass detector is a code-based pattern (not in the YAML
    # catalog). Rank it above flat env-read patterns (dotenv=70, decouple=80) so
    # it beats a smoke/script env-read on a confidence tie, but below the
    # precise direct-BaseSettings patterns (v1=90, v2=100) — a direct config is
    # a more certain match than a transitive project-base subclass.
    priority_by_id.setdefault("python-pydantic-settings-subclass", 85)

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

def _service_entry(
    repo_root: Path,
    service: dict,
    scan_root: Path,
    catalog: list[Pattern],
) -> dict:
    """Build one services[] entry from a scan_service result + deployment
    surfaces under the service's path."""
    language = service.get("language", "python")
    # search_roots include the repo root so a Settings base imported from a
    # shared repo-level package (outside the service tree) still resolves for
    # the transitive pydantic-settings detection (Dogfood #1a).
    result = scan_service(
        scan_root, language, catalog, search_roots=[scan_root, repo_root]
    )

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
      - hybrid:       OUT OF SCOPE for Phase A. Logs a loud stderr WARNING
                      and degrades to per-service. The output YAML records
                      granularity="hybrid (degraded to per-service)" so
                      downstream consumers (and adversarial review) can
                      see the degradation happened.
      - TBD:          per-service best-effort with granularity_source flag
    """
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
