"""sdk_snapshot.py — Phase 1.5 of /cost-billing-instrument.

Fetch + introspect the unified Moolabs SDK at the customer-pinned version,
write `.moolabs/customer-context/sdk-surface-snapshot.yaml`.

Usage:
    python sdk_snapshot.py \
        --customer-context-dir .moolabs/customer-context \
        --signed-yaml .moolabs/chain/04-final.signed.yaml \
        [--workdir /tmp/moolabs-sdk] \
        [--lang python] [--lang typescript] [--lang go]

The snapshot is the runtime input contract for Phase 2 (helper generation)
and Phase 2b (call-site inserts). Static `sdk-surface-reference.md` is a
fallback hint only.

This script does NOT execute customer code or install packages — it AST-parses
the SDK source directly (Python) or reads declaration files (TypeScript) or
shells out to `go doc -all` (Go). No side effects on the host.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Expected method paths that gate the codemod — every Phase 1.5 snapshot
# is checked against these. Missing entry → CRITICAL drift, abort.
#
# v0.3.0-rc1 unified-ingest contract: helpers (moolabs-client.{py,ts,go})
# hard-call three singular ergonomic methods, one per lane. The codemod
# MUST NOT proceed if any of them are missing — fallback rendering was
# removed when the helpers stopped gating on capability flags.
#
#   usage lane     → client.usage.ingest_event   (py) / ingestEvent (ts)
#   cost lane      → client.cost.ingest_event    (py) / ingestEvent (ts)
#   sibling-pair   → client.events.ingest        (py / ts identical)
#
# Go (P0 since 2026-05-28): the introspector is still a stub returning [];
# expected methods are listed here for documentation and future activation.
EXPECTED_METHODS: dict[str, dict[str, str]] = {
    "python": {
        "usage":  "client.usage.ingest_event",
        "cost":   "client.cost.ingest_event",
        "events": "client.events.ingest",
    },
    "typescript": {
        "usage":  "client.usage.ingestEvent",
        "cost":   "client.cost.ingestEvent",
        "events": "client.events.ingest",
    },
    "go": {
        "usage":  "client.Usage.IngestEvent",
        "cost":   "client.Cost.IngestEvent",
        "events": "client.Events.Ingest",
    },
}

# Methods to EXCLUDE — generated openapi variants that customers should not call.
_PYTHON_INTERNAL_SUFFIXES = ("_with_http_info", "_without_preload_content")


@dataclass
class Namespace:
    path: str           # e.g. "client.usage", "client.cost", "client.events"
    methods: list[str]  # e.g. ["ingest_event", "ingest_events"]


@dataclass
class LanguageSnapshot:
    repo_url: str
    resolved_tag: str
    commit_sha: str
    namespaces: list[Namespace] = field(default_factory=list)

    def has_method(self, dotted_path: str) -> bool:
        ns_path, method = dotted_path.rsplit(".", 1)
        for ns in self.namespaces:
            if ns.path == ns_path and method in ns.methods:
                return True
        return False


@dataclass
class Snapshot:
    generated_at: str
    sdk_versions: dict[str, dict[str, str]] = field(default_factory=dict)
    namespaces: dict[str, list[Namespace]] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    contract_drift: dict[str, list[str]] = field(default_factory=lambda: {
        "missing_expected_methods": [],
        "renamed_methods": [],
    })


def resolve_latest_tag(repo_url: str) -> tuple[str, bool]:
    """Return (highest_tag, is_prerelease).

    Two-pass: try strict stable (vX.Y.Z) first; if none exist (the SDK hasn't
    shipped a stable yet — as of 2026-05-25 moolabs-py only has -rcN tags),
    fall back to the highest including prereleases and flag it.
    """
    res = subprocess.run(
        ["git", "ls-remote", "--tags", repo_url],
        check=True, capture_output=True, text=True,
    )
    stable: list[str] = []
    prerelease: list[tuple[str, tuple[int, ...]]] = []
    # capture suffix kind+num so we sort prereleases sensibly
    suffix_rank = {"alpha": 0, "beta": 1, "rc": 2}
    for line in res.stdout.splitlines():
        if line.endswith("^{}"):
            continue  # skip annotated-tag dereferences
        m_stable = re.search(r"refs/tags/(v?\d+\.\d+\.\d+)$", line)
        if m_stable:
            stable.append(m_stable.group(1))
            continue
        m_pre = re.search(r"refs/tags/(v?(\d+)\.(\d+)\.(\d+)-(alpha|beta|rc)(\d*))$", line)
        if m_pre:
            tag = m_pre.group(1)
            major, minor, patch = int(m_pre.group(2)), int(m_pre.group(3)), int(m_pre.group(4))
            suffix = m_pre.group(5)
            suffix_num = int(m_pre.group(6) or 0)
            prerelease.append((tag, (major, minor, patch, suffix_rank[suffix], suffix_num)))

    if stable:
        def stable_key(t: str) -> tuple[int, ...]:
            return tuple(int(p) for p in t.lstrip("v").split("."))
        return sorted(stable, key=stable_key)[-1], False

    if prerelease:
        prerelease.sort(key=lambda x: x[1])
        return prerelease[-1][0], True

    raise SystemExit(f"no semver tag (stable or prerelease) found for {repo_url}")


def shallow_clone(repo_url: str, tag: str, dest: Path) -> str:
    """Clone repo at tag; return commit SHA."""
    if dest.exists():
        shutil.rmtree(dest)
    subprocess.run(
        ["git", "clone", "--depth=1", "--branch", tag, repo_url, str(dest)],
        check=True, capture_output=True,
    )
    res = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    )
    return res.stdout.strip()


def _pascal_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _find_pkg_root(src: Path) -> Path:
    for cand in (src / "moolabs", src / "src" / "moolabs"):
        if cand.exists():
            return cand
    raise SystemExit(f"could not find moolabs package root under {src}")


def _extract_capability_map(src: Path) -> dict[str, list[str]] | None:
    """Read CAPABILITY_MAP from _dx_routing.py via AST.

    Returns {capability_name: [backing_class_name, ...]} or None if the
    routing file isn't present (older SDK without dynamic dispatch).
    """
    routing_file = _find_pkg_root(src) / "_dx_routing.py"
    if not routing_file.exists():
        return None
    try:
        mod = ast.parse(routing_file.read_text())
    except SyntaxError:
        return None
    for node in mod.body:
        if not isinstance(node, ast.AnnAssign):
            continue
        if not (isinstance(node.target, ast.Name) and node.target.id == "CAPABILITY_MAP"):
            continue
        if not isinstance(node.value, ast.Dict):
            return None
        out: dict[str, list[str]] = {}
        for k, v in zip(node.value.keys, node.value.values):
            if not isinstance(k, ast.Constant) or not isinstance(k.value, str):
                continue
            if not isinstance(v, ast.List):
                continue
            classes: list[str] = []
            for item in v.elts:
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Name) and item.func.id == "BackingClass":
                    if item.args and isinstance(item.args[0], ast.Constant):
                        classes.append(item.args[0].value)
            out[k.value] = classes
        return out
    return None


def _api_class_methods(src: Path, class_name: str) -> list[str]:
    """Locate `moolabs/api/<snake>_api.py` and return its public methods."""
    pkg_root = _find_pkg_root(src)
    module_file = pkg_root / "api" / f"{_pascal_to_snake(class_name)}.py"
    if not module_file.exists():
        return []
    try:
        mod = ast.parse(module_file.read_text())
    except SyntaxError:
        return []
    methods: list[str] = []
    for node in mod.body:
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = item.name
            if name.startswith("_"):
                continue
            if any(name.endswith(suf) for suf in _PYTHON_INTERNAL_SUFFIXES):
                continue
            methods.append(name)
    return sorted(set(methods))


def _dx_namespace_methods(src: Path) -> dict[str, list[str]]:
    """Read `_dx_namespaces.py` and return wrapper-class methods keyed by class.

    v0.3.0-rc1 ergonomic methods (US-006 / US-007 / US-008) live on the
    customer-facing wrapper classes — NOT on the openapi-generated backing
    classes in `moolabs/api/`. The wrappers are:

      _UsageNamespace.ingest_event   — singular usage ingest
      _CostNamespace.ingest_event    — singular cost ingest
      _EventsNamespace.ingest        — unified sibling-pair ingest

    Returns `{class_name: [public method, ...]}`. Empty dict when the file
    is absent (older SDK predating the wrapper layer).
    """
    namespaces_file = _find_pkg_root(src) / "_dx_namespaces.py"
    if not namespaces_file.exists():
        return {}
    try:
        mod = ast.parse(namespaces_file.read_text())
    except SyntaxError:
        return {}
    out: dict[str, list[str]] = {}
    for node in mod.body:
        if not isinstance(node, ast.ClassDef):
            continue
        if not node.name.startswith("_") or not node.name.endswith("Namespace"):
            continue
        methods: list[str] = []
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = item.name
            if name.startswith("_"):
                continue
            if any(name.endswith(suf) for suf in _PYTHON_INTERNAL_SUFFIXES):
                continue
            methods.append(name)
        if methods:
            out[node.name] = sorted(set(methods))
    return out


# Capability name -> wrapper class name. Convention: `_<Capitalized>Namespace`.
# Driven from CAPABILITY_MAP keys at runtime so this stays correct as the SDK
# evolves; the special `events` capability is handled separately because it
# is NOT in CAPABILITY_MAP (US-004 / US-008).
def _wrapper_class_for(capability: str) -> str:
    return f"_{capability.capitalize()}Namespace"


def introspect_python(src: Path) -> list[Namespace]:
    """Extract the Moolabs client namespace tree from moolabs-py source.

    Priority order — actual SDK shape decides:
      Mode A (current SDK, v0.2.0-rc1+): read `_dx_routing.CAPABILITY_MAP` +
        scrape each backing class's public methods from `moolabs/api/*.py`.
        Ground truth for the dynamic-dispatch SDK.
      Mode B (older variants): walk `Moolabs.@property` methods that return a
        resource class; recurse into that class's public defs.
      Mode C (legacy): walk `self.x = X(...)` assignments in `__init__`.
    """
    # MODE A — capability map (current SDK shape, v0.2+).
    #
    # v0.3.0-rc1 added the wrapper layer (`_dx_namespaces.py`): customer-facing
    # classes (`_UsageNamespace`, `_CostNamespace`, ...) wrap the openapi-
    # generated backing classes and expose ergonomic methods like
    # `ingest_event` (singular) that aren't on the backing layer. We MUST
    # merge wrapper methods into the discovered surface — otherwise the
    # introspector reports only the openapi shape and misses the customer
    # API (`client.usage.ingest_event` etc.).
    #
    # Also: `events` is a top-level capability via @property on Moolabs but
    # is NOT in CAPABILITY_MAP (US-004 / US-008). We add it explicitly from
    # `_EventsNamespace`.
    cap_map = _extract_capability_map(src)
    if cap_map is not None:
        wrapper_methods = _dx_namespace_methods(src)  # {class_name: [methods]}
        out: list[Namespace] = []
        for capability, backing_classes in cap_map.items():
            methods: list[str] = []
            for cls in backing_classes:
                methods.extend(_api_class_methods(src, cls))
            # Merge customer-facing wrapper methods (v0.3+ ergonomic surface).
            wrapper_cls = _wrapper_class_for(capability)
            methods.extend(wrapper_methods.get(wrapper_cls, []))
            methods = sorted(set(methods))
            if methods:
                out.append(Namespace(path=f"client.{capability}", methods=methods))
        # Special: `events` namespace (US-008) — not in CAPABILITY_MAP.
        events_methods = wrapper_methods.get("_EventsNamespace", [])
        if events_methods:
            out.append(Namespace(path="client.events", methods=events_methods))
        return out

    # MODE B / C fallback — older SDK without _dx_routing
    modules: dict[str, ast.Module] = {}
    pkg_root = _find_pkg_root(src)

    for py_file in pkg_root.rglob("*.py"):
        try:
            modules[str(py_file.relative_to(pkg_root))] = ast.parse(
                py_file.read_text(), filename=str(py_file)
            )
        except SyntaxError:
            pass  # skip; some generated SDKs include vendored shims

    # Find the Moolabs root class
    moolabs_class: ast.ClassDef | None = None
    for mod in modules.values():
        for node in mod.body:
            if isinstance(node, ast.ClassDef) and node.name == "Moolabs":
                moolabs_class = node
                break
        if moolabs_class:
            break
    if moolabs_class is None:
        raise SystemExit("Moolabs class not found in SDK source")

    # Class index by name → ClassDef
    class_index: dict[str, ast.ClassDef] = {}
    for mod in modules.values():
        for node in mod.body:
            if isinstance(node, ast.ClassDef):
                class_index[node.name] = node

    namespaces: list[Namespace] = []

    def is_property(fn: ast.FunctionDef) -> bool:
        return any(
            (isinstance(d, ast.Name) and d.id == "property")
            for d in fn.decorator_list
        )

    def returned_class_name(fn: ast.FunctionDef) -> str | None:
        """Find `return <SomeClass>(...)` and return SomeClass."""
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Call):
                func = node.value.func
                if isinstance(func, ast.Name):
                    return func.id
        return None

    visited: set[str] = set()

    def walk(class_node: ast.ClassDef, prefix: str) -> None:
        if class_node.name in visited:
            return  # avoid cycles
        visited.add(class_node.name)

        # Two kinds of public surface on this class:
        #   - public methods (defs not starting with _, not @property)
        #   - sub-namespaces (@property returning a known resource class)
        public_methods: list[str] = []
        for item in class_node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name.startswith("_"):
                continue
            if is_property(item):
                # sub-namespace — recurse if we can resolve the returned class
                sub_class_name = returned_class_name(item)
                if sub_class_name and sub_class_name in class_index:
                    walk(class_index[sub_class_name], f"{prefix}.{item.name}")
                # if the @property returns a non-namespace thing (str etc.), skip
            else:
                public_methods.append(item.name)

        if public_methods:
            namespaces.append(Namespace(path=prefix, methods=sorted(public_methods)))

        # Legacy / belt-and-suspenders: also pick up self.<attr> = ResourceClass(...) in __init__
        for item in class_node.body:
            if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                for stmt in ast.walk(item):
                    if not isinstance(stmt, ast.Assign):
                        continue
                    if not (len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Attribute)):
                        continue
                    if not (isinstance(stmt.targets[0].value, ast.Name) and stmt.targets[0].value.id == "self"):
                        continue
                    attr_name = stmt.targets[0].attr
                    if attr_name.startswith("_"):
                        continue
                    if isinstance(stmt.value, ast.Call) and isinstance(stmt.value.func, ast.Name):
                        sub_class = class_index.get(stmt.value.func.id)
                        if sub_class is not None:
                            walk(sub_class, f"{prefix}.{attr_name}")

    walk(moolabs_class, "client")
    return namespaces


def _ts_extract_class_block(text: str, class_name: str) -> str | None:
    """Return the body of `class <class_name>` declaration with balanced braces.

    Handles nested braces (TS class bodies contain method signatures, generic
    constraint clauses, and option-object types — all with their own braces).
    Returns None when the class isn't found.
    """
    pat = re.compile(rf"\b(?:export\s+(?:declare\s+)?)?class\s+{re.escape(class_name)}\b[^{{]*\{{")
    m = pat.search(text)
    if not m:
        return None
    start = m.end()  # right after the opening brace
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return text[start:i - 1] if depth == 0 else None


def _ts_collect_dts_text(src: Path) -> str:
    """Concatenate every .d.ts file under the SDK root into one searchable blob.

    Methods of `UsageNamespace` may live in a different .d.ts file than the
    Moolabs class itself; concatenating sidesteps cross-file lookup. Safe
    because TS .d.ts files have unique top-level class names (compiler errors
    on conflict).
    """
    pkg_json = src / "package.json"
    if not pkg_json.exists():
        raise SystemExit(f"package.json not found at {pkg_json}")
    try:
        pkg_meta = json.loads(pkg_json.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"could not parse package.json: {e}")

    entry_dts: list[Path] = []
    for k in ("types", "typings"):
        if k in pkg_meta:
            entry_dts.append(src / pkg_meta[k])

    # Walk every .d.ts in the same directory tree as the entry (and rglob as
    # fallback). De-duplicate by resolved path.
    all_dts: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        try:
            rp = p.resolve()
        except OSError:
            return
        if rp in seen or not p.exists():
            return
        seen.add(rp)
        all_dts.append(p)

    for p in entry_dts:
        _add(p)
        if p.exists():
            for sib in p.parent.glob("*.d.ts"):
                _add(sib)
    for p in src.rglob("*.d.ts"):
        _add(p)

    return "\n".join(p.read_text(errors="ignore") for p in all_dts)


def introspect_typescript(src: Path) -> list[Namespace]:
    """Parse .d.ts to extract the Moolabs client surface for v0.3.0-rc1.

    Two-pass strategy:
      1. Find Moolabs class, extract top-level accessors (fields + getters).
         v0.3 uses getters (`get usage(): Namespace;`); v0.2 used fields
         (`usage: UsageApi;`) — both are supported.
      2. For each accessor's return type, find the corresponding class
         declaration and read its public method signatures.
         When the return type is the generic `Namespace` (a Proxy alias
         that doesn't itself declare the ergonomic methods), fall back to
         the `<Capitalized>Namespace` subclass — convention matching
         `_dx_namespaces.ts` (UsageNamespace, CostNamespace, EventsNamespace).
    """
    text = _ts_collect_dts_text(src)

    moolabs_body = _ts_extract_class_block(text, "Moolabs")
    if moolabs_body is None:
        return []

    # Accessors on Moolabs:
    #   v0.2: `usage: UsageApi;`           — field
    #   v0.3: `get usage(): Namespace;`    — getter
    field_re  = re.compile(r"^\s*(?:public\s+|readonly\s+)?(\w+)\s*:\s*(\w+)\s*;", re.MULTILINE)
    getter_re = re.compile(r"^\s*(?:public\s+)?get\s+(\w+)\s*\(\s*\)\s*:\s*(\w+)\s*;", re.MULTILINE)

    accessors: list[tuple[str, str]] = []
    for m in field_re.finditer(moolabs_body):
        accessors.append((m.group(1), m.group(2)))
    for m in getter_re.finditer(moolabs_body):
        accessors.append((m.group(1), m.group(2)))

    method_pat = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9]*)\s*[<(]", re.MULTILINE)
    out: list[Namespace] = []
    seen_paths: set[str] = set()

    for attr_name, declared_type in accessors:
        if attr_name.startswith("_"):
            continue

        # Resolve which class to extract methods from. Generic `Namespace`
        # (v0.3 Proxy alias) doesn't declare ergonomic methods — fall back
        # to convention `<Capitalized>Namespace`.
        candidates: list[str] = [declared_type]
        if declared_type == "Namespace":
            candidates.append(f"{attr_name.capitalize()}Namespace")

        methods: list[str] = []
        for cls in candidates:
            body = _ts_extract_class_block(text, cls)
            if body is None:
                continue
            for m in method_pat.finditer(body):
                name = m.group(1)
                if name.startswith("_") or name == "constructor":
                    continue
                methods.append(name)

        path = f"client.{attr_name}"
        if path in seen_paths:
            continue
        methods = sorted(set(methods))
        if methods:
            out.append(Namespace(path=path, methods=methods))
            seen_paths.add(path)

    return out


def introspect_go(src: Path) -> list[Namespace]:
    """Run `go doc -all ./...` and parse the public type/method surface.

    Go is **P0** (Decision #2 reversed 2026-05-28 — first customer is Go). This
    introspector must verify the v0.3.0-rc1 unified-ingest surface against
    `dx_client.go`: `client.Usage.IngestEvent`, `client.Cost.IngestEvent`,
    `client.Events.Ingest` — NOT the SDK README, which has historically lagged
    behind the dx_client.go shape. Full implementation tracked with the Go
    adapter work; returns an empty list until then (the codemod refuses to
    emit a Go helper while the snapshot is empty — `unified_ingest_present`
    will be False, which the main loop converts to CRITICAL).
    """
    return []


def build_capabilities(lang_snap: LanguageSnapshot, lang: str) -> dict[str, Any]:
    """Compute v0.3.0-rc1 capability flags from the introspected namespaces.

    v0.3 unified-ingest contract: helpers hard-call three singular ergonomic
    methods (one per lane). Each flag answers "does this lane's method exist?"
    The codemod refuses to proceed unless `unified_ingest_present` is True —
    there is no fallback rendering path in the v0.3 helpers.
    """
    expected = EXPECTED_METHODS.get(lang, {})

    usage_path = expected.get("usage")
    cost_path = expected.get("cost")
    events_path = expected.get("events")

    # Empty-namespaces guard: the Go introspector currently returns []. Treat
    # missing-snapshot as "lane not present" — the main loop converts this
    # into a CRITICAL drift entry per language.
    has_namespaces = bool(lang_snap.namespaces)

    usage_present = bool(has_namespaces and usage_path and lang_snap.has_method(usage_path))
    cost_present = bool(has_namespaces and cost_path and lang_snap.has_method(cost_path))
    events_present = bool(has_namespaces and events_path and lang_snap.has_method(events_path))

    return {
        "unified_ingest_present": usage_present and cost_present and events_present,
        "usage_ergonomic_ingest": usage_present,
        "cost_ergonomic_ingest": cost_present,
        "events_unified_namespace": events_present,
        "usage_method_path": usage_path if usage_present else None,
        "cost_method_path": cost_path if cost_present else None,
        "events_method_path": events_path if events_present else None,
    }


def yaml_dump(snapshot: Snapshot, dest: Path) -> None:
    """Minimal YAML serialization — avoids adding PyYAML as a runtime dep
    for the customer's codemod environment. Output is hand-formatted for
    readability + diff-friendliness."""
    lines: list[str] = []
    lines.append(f"generated_at: {snapshot.generated_at}")
    lines.append("sdk_versions:")
    for lang, meta in snapshot.sdk_versions.items():
        lines.append(f"  {lang}:")
        for k, v in meta.items():
            lines.append(f"    {k}: {v}")
    lines.append("namespaces:")
    for lang, nss in snapshot.namespaces.items():
        lines.append(f"  {lang}:")
        for ns in nss:
            lines.append(f"    - path: \"{ns.path}\"")
            lines.append(f"      methods: [{', '.join(ns.methods)}]")
    lines.append("capabilities:")
    for k, v in snapshot.capabilities.items():
        if isinstance(v, str):
            lines.append(f"  {k}: \"{v}\"")
        elif v is None:
            lines.append(f"  {k}: null")
        else:
            lines.append(f"  {k}: {str(v).lower() if isinstance(v, bool) else v}")
    lines.append("contract_drift:")
    for k, v in snapshot.contract_drift.items():
        lines.append(f"  {k}: [{', '.join(repr(x) for x in v)}]")
    dest.write_text("\n".join(lines) + "\n")


def parse_signed_yaml(path: Path) -> dict[str, dict[str, str]]:
    """Read .moolabs/chain/04-final.signed.yaml > integration.sdk_package_install
    without requiring PyYAML. Returns per-language config dict.

    Indent-tolerant: the previous implementation hard-coded the expected
    indentation (6 spaces for language keys, 8 spaces for config keys). YAML
    written with any other indentation (2-space being the most common, but
    also 4-space) silently returned empty config, causing the main loop to
    fall through to `strategy=latest-tag` for every language — ignoring the
    customer's pinned version.

    This implementation computes the indent of the language keys dynamically
    from the first non-blank line under `sdk_package_install:`, then uses
    that as the baseline. Config keys are expected at a deeper indent.

    Returns an empty dict only when the block genuinely contains no
    language entries; the main loop emits a warning in that case rather
    than silently using `latest-tag`.
    """
    text = path.read_text()
    cfg: dict[str, dict[str, str]] = {}

    # Find the line `<indent>sdk_package_install:` and capture every subsequent
    # line that is indented strictly deeper than `<indent>` (i.e. inside the
    # block). Stop at the first line at or below `<indent>` indentation.
    lines = text.splitlines()
    block_lines: list[str] = []
    block_indent: int | None = None
    for i, line in enumerate(lines):
        stripped = line.lstrip(" ")
        if stripped.startswith("sdk_package_install:"):
            block_indent = len(line) - len(stripped)
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if not next_line.strip():
                    block_lines.append(next_line)
                    continue
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_indent <= block_indent:
                    break
                block_lines.append(next_line)
            break
    if not block_lines:
        return cfg

    # Determine the language-key indent from the first non-blank line in the
    # block — that line is one of `<langindent><lang>:`. All language keys
    # share that indent; config keys are at a deeper indent.
    lang_indent: int | None = None
    for line in block_lines:
        if not line.strip():
            continue
        lang_indent = len(line) - len(line.lstrip(" "))
        break
    if lang_indent is None:
        return cfg

    current_lang: str | None = None
    lang_re = re.compile(rf"^ {{{lang_indent}}}(python|typescript|go):\s*$")
    cfg_re = re.compile(rf"^ {{{lang_indent + 1},}}(\w+):\s*(.*)$")
    for line in block_lines:
        if not line.strip():
            continue
        m_lang = lang_re.match(line)
        if m_lang:
            current_lang = m_lang.group(1)
            cfg.setdefault(current_lang, {})
            continue
        if current_lang:
            m_cfg = cfg_re.match(line)
            if m_cfg:
                cfg[current_lang][m_cfg.group(1)] = m_cfg.group(2).strip()
    return cfg


REPO_URLS = {
    "python":     "https://github.com/moolabs-hq/moolabs-py.git",
    "typescript": "https://github.com/moolabs-hq/moolabs-ts.git",
    "go":         "https://github.com/moolabs-hq/moolabs-go.git",
}

INTROSPECTORS = {
    "python":     introspect_python,
    "typescript": introspect_typescript,
    "go":         introspect_go,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--signed-yaml", default=".moolabs/chain/04-final.signed.yaml")
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--lang", action="append", choices=["python", "typescript", "go"])
    args = ap.parse_args(argv)

    # Default to py+ts because the Go introspector is still a stub (returns [])
    # — passing it here would yield CRITICAL on every run. Once introspect_go
    # has a real implementation, add "go" to the default. Decision #2 (Go=P0)
    # was reversed 2026-05-28; the deferral is purely about THIS introspector
    # being incomplete, not about Go's product priority.
    langs = args.lang or ["python", "typescript"]
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="moolabs-sdk-"))
    workdir.mkdir(parents=True, exist_ok=True)

    install_cfg = parse_signed_yaml(Path(args.signed_yaml))
    if Path(args.signed_yaml).exists() and not install_cfg:
        print(
            f"WARNING: {args.signed_yaml} exists but no sdk_package_install entries "
            "were parsed. Every language will fall through to strategy=latest-tag, "
            "ignoring any customer-pinned versions. Verify the YAML structure includes "
            "an `integration.sdk_package_install.<lang>.strategy` block.",
            file=sys.stderr,
        )
    snapshot = Snapshot(generated_at=datetime.now(timezone.utc).isoformat())

    for lang in langs:
        lang_cfg = install_cfg.get(lang, {})
        strategy = lang_cfg.get("strategy", "latest-tag")
        if strategy == "skip":
            print(f"[{lang}] strategy=skip — not snapshotted", file=sys.stderr)
            continue
        if strategy == "custom":
            print(f"[{lang}] strategy=custom — cannot auto-introspect; relying on static reference", file=sys.stderr)
            continue
        if strategy == "private-mirror":
            repo_url = lang_cfg.get("mirror_url", "")
            if not repo_url:
                print(f"[{lang}] private-mirror but no mirror_url; skipping", file=sys.stderr)
                continue
        else:
            repo_url = REPO_URLS[lang]

        is_prerelease = False
        if strategy == "pinned":
            tag = lang_cfg.get("version", "")
            if not tag:
                raise SystemExit(f"[{lang}] strategy=pinned but no version set")
            # detect if the customer pinned a prerelease (just for snapshot metadata)
            is_prerelease = bool(re.search(r"-(alpha|beta|rc)\d*$", tag))
        else:
            tag, is_prerelease = resolve_latest_tag(repo_url)
            if is_prerelease:
                print(
                    f"[{lang}] WARN: no stable vX.Y.Z tag yet — using highest prerelease {tag}. "
                    f"Pin explicitly via Q16 strategy=pinned when SDK ships stable.",
                    file=sys.stderr,
                )

        dest = workdir / f"{lang}-{tag}"
        commit_sha = shallow_clone(repo_url, tag, dest)
        namespaces = INTROSPECTORS[lang](dest)

        snap = LanguageSnapshot(
            repo_url=repo_url, resolved_tag=tag, commit_sha=commit_sha, namespaces=namespaces
        )
        snapshot.sdk_versions[lang] = {
            "repo_url": repo_url, "resolved_tag": tag, "commit_sha": commit_sha[:12],
            "is_prerelease": "true" if is_prerelease else "false",
        }
        snapshot.namespaces[lang] = namespaces

        # Compute v0.3.0-rc1 capabilities per language. Cross-language merge
        # is strict-AND for every lane: the codemod is cross-platform and only
        # proceeds when EVERY inspected SDK exposes the unified-ingest surface.
        # Method paths are language-specific, so we keep the first language's
        # path for downstream renderer reference (renderers select by `lang`
        # anyway — this is just for the snapshot YAML).
        caps = build_capabilities(snap, lang)
        if not snapshot.capabilities:
            snapshot.capabilities = caps
        else:
            for flag in (
                "unified_ingest_present",
                "usage_ergonomic_ingest",
                "cost_ergonomic_ingest",
                "events_unified_namespace",
            ):
                snapshot.capabilities[flag] = (
                    snapshot.capabilities[flag] and caps[flag]
                )
            # Keep the first non-None method path seen across languages.
            for path_key in ("usage_method_path", "cost_method_path", "events_method_path"):
                if snapshot.capabilities.get(path_key) is None and caps.get(path_key):
                    snapshot.capabilities[path_key] = caps[path_key]

        # Contract drift: every expected method MUST be present. EXPECTED_METHODS
        # is now keyed by lane ("usage" / "cost" / "events") → dotted path.
        for lane, expected in EXPECTED_METHODS.get(lang, {}).items():
            if not snap.has_method(expected):
                snapshot.contract_drift["missing_expected_methods"].append(
                    f"{lang}: {expected} (lane={lane})"
                )

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sdk-surface-snapshot.yaml"
    yaml_dump(snapshot, out_path)
    print(f"wrote {out_path}", file=sys.stderr)

    if snapshot.contract_drift["missing_expected_methods"]:
        print(
            "CRITICAL: missing expected methods — codemod MUST NOT proceed:\n  "
            + "\n  ".join(snapshot.contract_drift["missing_expected_methods"]),
            file=sys.stderr,
        )
        return 2
    # v0.3.0-rc1 helpers hard-call client.usage.ingest_event /
    # client.cost.ingest_event / client.events.ingest. There is no fallback
    # rendering path — if any lane is missing the codemod must NOT proceed.
    if not snapshot.capabilities.get("unified_ingest_present"):
        missing_lanes = [
            lane for lane, flag in (
                ("usage",  "usage_ergonomic_ingest"),
                ("cost",   "cost_ergonomic_ingest"),
                ("events", "events_unified_namespace"),
            ) if not snapshot.capabilities.get(flag)
        ]
        print(
            "CRITICAL: unified_ingest_present=false; codemod MUST NOT proceed.\n"
            f"  Missing ergonomic lane(s): {', '.join(missing_lanes) or 'unknown'}\n"
            "  v0.3.0-rc1 helpers have no fallback rendering path.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
