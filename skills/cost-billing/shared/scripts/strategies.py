#!/usr/bin/env python3
"""Named detection strategies for the framework-capability tree.

Each entry in DETECTORS maps a strategy name to a pure predicate
`(path, text, search_roots) -> bool`, so framework nodes can reference a
detector by name rather than wiring detection logic inline.

The first detector, `pydantic_settings_subclass`, is the transitive
pydantic-settings base-resolution detector moved verbatim out of
discovery/scripts/env_loader_scan.py. It resolves a class's base chain
across files to BaseSettings — no modeling on any one repo's base name.
"""
from __future__ import annotations

import os
import re
from pathlib import Path


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


def _first_transitive_settings_class(
    path: Path, text: str, search_roots: list[Path],
) -> tuple[str, list[str]] | None:
    """Return (class_name, base_simple_names) for the first class in `text`
    that transitively extends BaseSettings via a PROJECT base (resolved across
    files), or None. Classes that extend BaseSettings DIRECTLY are skipped —
    they are left to the precise v1/v2 regex patterns (which carry the
    modify-mode accessor)."""
    for cname, bases in _parse_class_bases(text).items():
        if not bases or any(b in _PYDANTIC_SETTINGS_BASES for b in bases):
            continue
        if _class_reaches_basesettings(cname, path, search_roots, set(), 0):
            return cname, bases
    return None


def pydantic_settings_subclass(
    path: Path, text: str, search_roots: list[Path],
) -> bool:
    """True iff `path` defines a class transitively extending BaseSettings."""
    return _first_transitive_settings_class(path, text, search_roots) is not None


DETECTORS = {"pydantic_settings_subclass": pydantic_settings_subclass}


# ── Import-path rules (de-hardcoded artifact placement) ─────────────────
# Each rule: (config_file, basename, product) -> (emit_dir, import_path).
# Artifacts are placed beside the DETECTED config file; `src/` is a package
# root marker (stripped from the python import path).

_PY_ROOT_MARKERS = {"src"}


def python_package(config_file: str, basename: str, product: str) -> tuple[str, str]:
    p = config_file.replace("\\", "/")
    parts = p.split("/")
    emit_dir = "/".join(parts[:-1])
    # The IMMEDIATE parent directory is the package; `src/` is stripped as a
    # package-root marker so a `src/config.py` layout imports flat.
    pkg_parts = [seg for seg in parts[:-1] if seg not in _PY_ROOT_MARKERS]
    last = pkg_parts[-1] if pkg_parts else ""
    dotted = f"{last}.{basename}" if last else basename
    return emit_dir, dotted


def go_module(config_file: str, basename: str, product: str) -> tuple[str, str]:
    p = config_file.replace("\\", "/")
    emit_dir = "/".join(p.split("/")[:-1])
    return emit_dir, emit_dir  # go import path resolved against go.mod by caller


def ts_alias(config_file: str, basename: str, product: str) -> tuple[str, str]:
    p = config_file.replace("\\", "/")
    emit_dir = "/".join(p.split("/")[:-1])
    return emit_dir, f"./{basename}"


IMPORT_RULES = {
    "python_package": python_package,
    "go_module": go_module,
    "ts_alias": ts_alias,
}
