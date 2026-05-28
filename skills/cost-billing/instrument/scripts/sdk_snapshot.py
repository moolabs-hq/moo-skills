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
EXPECTED_METHODS = {
    "python": [
        "client.usage.ingest_events",
    ],
    "typescript": [
        "client.usage.ingestEvents",
    ],
    "go": [],
}

# Cost-event endpoint candidates. The unified SDK exposes `client.cost.*`
# today (capability "cost" in CAPABILITY_MAP routes to CostEventsApi +
# SdkIngestApi on the acute backend — verified against moolabs-py@v0.2.0-rc9).
COST_EVENT_CANDIDATES = {
    "python": [
        "client.cost.ingest_events",
        "client.cost.ingest_events_batch",
    ],
    "typescript": [
        "client.cost.ingestEvents",
        "client.cost.ingestEventsBatch",
    ],
    "go": [],
}

# Methods to EXCLUDE — generated openapi variants that customers should not call.
_PYTHON_INTERNAL_SUFFIXES = ("_with_http_info", "_without_preload_content")


@dataclass
class Namespace:
    path: str           # e.g. "client.meter.events"
    methods: list[str]  # e.g. ["ingest_events"]


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
    # MODE A — capability map (current SDK shape)
    cap_map = _extract_capability_map(src)
    if cap_map is not None:
        out: list[Namespace] = []
        for capability, backing_classes in cap_map.items():
            methods: list[str] = []
            for cls in backing_classes:
                methods.extend(_api_class_methods(src, cls))
            methods = sorted(set(methods))
            if methods:
                out.append(Namespace(path=f"client.{capability}", methods=methods))
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


def introspect_typescript(src: Path) -> list[Namespace]:
    """Parse .d.ts to extract the Moolabs client surface.

    v1 implementation: shells out to `tsc --declaration --emitDeclarationOnly`
    when source is present, then parses the generated .d.ts with a regex-based
    extractor. If TS toolchain isn't available, falls back to reading the
    shipped `dist/**/*.d.ts` directly.

    For v1 we keep this minimal — the goal is to verify `client.meter.events.ingestEvents`
    exists and detect any new cost-event method. Full type-level introspection
    is deferred.
    """
    # Look for the entry .d.ts file
    pkg_json = src / "package.json"
    if not pkg_json.exists():
        raise SystemExit(f"package.json not found at {pkg_json}")
    try:
        pkg_meta = json.loads(pkg_json.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"could not parse package.json: {e}")

    dts_candidates: list[Path] = []
    if "types" in pkg_meta:
        dts_candidates.append(src / pkg_meta["types"])
    if "typings" in pkg_meta:
        dts_candidates.append(src / pkg_meta["typings"])
    dts_candidates.extend(src.glob("**/index.d.ts"))

    namespaces: list[Namespace] = []
    seen: set[str] = set()
    # Conservative regex parse — looks for `<name>: <ResourceClass>;` in the Moolabs class
    # and for `<method>(...): ...` inside each resource class block.
    moolabs_block = re.compile(r"class\s+Moolabs[^{]*\{([^}]+)\}", re.DOTALL)
    method_pat = re.compile(r"^\s*([a-zA-Z][a-zA-Z0-9]*)\s*\(", re.MULTILINE)
    for dts in dts_candidates:
        if not dts.exists():
            continue
        text = dts.read_text(errors="ignore")
        m = moolabs_block.search(text)
        if not m:
            continue
        body = m.group(1)
        # Top-level resources on Moolabs
        for line in body.splitlines():
            assign_match = re.match(r"\s*(?:public\s+|readonly\s+)?(\w+):\s*(\w+)\s*;", line)
            if not assign_match:
                continue
            attr_name, resource_cls = assign_match.groups()
            if attr_name.startswith("_"):
                continue
            # Locate the resource class definition and extract its methods
            res_block = re.search(rf"class\s+{re.escape(resource_cls)}[^{{]*\{{([^}}]+)\}}", text, re.DOTALL)
            if not res_block:
                continue
            methods = method_pat.findall(res_block.group(1))
            methods = sorted({m for m in methods if not m.startswith("_") and m != "constructor"})
            path = f"client.{attr_name}"
            if path not in seen and methods:
                namespaces.append(Namespace(path=path, methods=methods))
                seen.add(path)
    return namespaces


def introspect_go(src: Path) -> list[Namespace]:
    """Run `go doc -all ./...` and parse the public type/method surface.

    Go is **P0** (Decision #2 reversed 2026-05-28 — first customer is Go). This
    introspector must verify the normalized client surface against `dx_client.go`
    (`client.Usage.IngestEvents`, `client.Cost.IngestEventsBatch`) — NOT the SDK
    README, which still documents the stale `client.Meter.Events.*` shape. Full
    implementation tracked with the Go adapter work; returns an empty list until
    then (callers fall back to the structured-log rail for Go cost emission).
    """
    return []


def build_capabilities(lang_snap: LanguageSnapshot, lang: str) -> dict[str, Any]:
    """Compute capability flags from the introspected namespaces."""
    usage_present = lang_snap.has_method(EXPECTED_METHODS[lang][0]) if EXPECTED_METHODS[lang] else False
    cost_method_path: str | None = None
    for candidate in COST_EVENT_CANDIDATES.get(lang, []):
        if lang_snap.has_method(candidate):
            cost_method_path = candidate
            break
    return {
        "usage_event_emit": usage_present,
        "cost_event_direct_emit": cost_method_path is not None,
        "cost_event_method_path": cost_method_path,
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
    without requiring PyYAML. Returns per-language config dict."""
    # Minimal: look for the sdk_package_install block by line markers.
    # Real implementation would use PyYAML; this avoids the dep for now.
    text = path.read_text()
    cfg: dict[str, dict[str, str]] = {}
    block_re = re.compile(
        r"sdk_package_install:\s*\n((?:\s{4,}.*\n)+)", re.MULTILINE
    )
    m = block_re.search(text)
    if not m:
        return cfg
    block = m.group(1)
    current_lang: str | None = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if re.match(r"\s{6}(python|typescript|go):", line):
            current_lang = line.strip().rstrip(":")
            cfg.setdefault(current_lang, {})
        elif current_lang and re.match(r"\s{8}\w+:", line):
            key, _, value = line.strip().partition(":")
            cfg[current_lang][key] = value.strip()
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

    langs = args.lang or ["python", "typescript"]  # Go deferred per v1 scope
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="moolabs-sdk-"))
    workdir.mkdir(parents=True, exist_ok=True)

    install_cfg = parse_signed_yaml(Path(args.signed_yaml))
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

        # Compute capabilities per language; the first language to report
        # cost_event_direct_emit=true wins (they're auto-generated from one
        # OpenAPI spec, so they should agree — but we record the first match).
        caps = build_capabilities(snap, lang)
        if not snapshot.capabilities:
            snapshot.capabilities = caps
        else:
            # Merge: usage must be true in ALL inspected languages; cost is
            # true if ANY inspected language has it (defensive).
            snapshot.capabilities["usage_event_emit"] = (
                snapshot.capabilities["usage_event_emit"] and caps["usage_event_emit"]
            )
            if caps["cost_event_direct_emit"] and not snapshot.capabilities["cost_event_direct_emit"]:
                snapshot.capabilities["cost_event_direct_emit"] = True
                snapshot.capabilities["cost_event_method_path"] = caps["cost_event_method_path"]

        # Contract drift: expected methods MUST be present
        for expected in EXPECTED_METHODS.get(lang, []):
            if not snap.has_method(expected):
                snapshot.contract_drift["missing_expected_methods"].append(
                    f"{lang}: {expected}"
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
    if not snapshot.capabilities.get("usage_event_emit"):
        print("CRITICAL: usage_event_emit=false; codemod MUST NOT proceed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
