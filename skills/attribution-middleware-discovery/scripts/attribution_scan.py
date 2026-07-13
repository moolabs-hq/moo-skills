"""Read-only static discovery for attribution middleware candidates."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterator


RUNTIME_IGNORED_PARTS = {
    ".git", ".moolabs", ".next", ".nuxt", ".pytest_cache", ".venv", "__pycache__",
    "build", "coverage", "dist", "examples", "fixtures", "generated", "migrations",
    "node_modules", "sdk", "sdks", "test", "testdata", "tests", "vendor", "venv",
    "archive", "archived", "design", "outputs", "reset", "tmp", "worktree", "worktrees",
}
RUNTIME_IGNORED_PART = re.compile(
    r"^(?:__tests__|__generated__|generated[-_]?src|generated[-_]?sources?|"
    r"test[-_](?:utils?|helpers?|fixtures?|data)|tests?[-_](?:unit|integration|e2e|fixtures?))$",
    re.I,
)
SOURCE_SUFFIXES = {".py", ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs"}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
SUPPORTED_JS_FRAMEWORKS = {"express", "hono", "nextjs"}
SUPPORTED_PYTHON_FRAMEWORKS = {"fastapi"}
GENERATED_SOURCE_NAME = re.compile(r"(?:^|[._-])gen(?:erated)?(?:[._-]|$)", re.I)
SDK_SOURCE_PART = re.compile(r"(?:^|[-_])sdk$", re.I)
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "assets" / "instrumentation-map.schema.yaml"


class DiscoveryError(ValueError):
    """A user-visible discovery or validation error."""


def _repo_scan_candidates() -> list[Path]:
    skills_root = Path(__file__).resolve().parents[2]
    return [
        Path(__file__).resolve().parent / "vendor" / "repo_scan.py",
        skills_root / "cost-billing" / "discovery" / "scripts" / "repo_scan.py",
        skills_root / "cost-billing-discovery" / "scripts" / "repo_scan.py",
        skills_root / "discovery" / "scripts" / "repo_scan.py",
    ]


def _load_repo_scan():
    sibling = next((candidate for candidate in _repo_scan_candidates() if candidate.is_file()), None)
    if sibling is None:
        checked = ", ".join(str(candidate) for candidate in _repo_scan_candidates())
        raise DiscoveryError(f"unable to locate required cost-billing discovery scanner; checked: {checked}")
    spec = importlib.util.spec_from_file_location("cost_billing_repo_scan", sibling)
    if spec is None or spec.loader is None:
        raise DiscoveryError(f"unable to load sibling scanner: {sibling}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves postponed annotations through sys.modules during import.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_scan_inputs(repo: Path) -> Iterator[Path]:
    """Yield every manifest/config path consumed by the shared repo scanner."""
    scanner = _load_repo_scan()
    manifest_names = {
        name for names in scanner.MANIFESTS.values() for name in names
    }
    ignored_parts = set(getattr(scanner, "IGNORE_DIRS", ()))
    candidates: list[Path] = []
    for path in repo.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.name not in manifest_names:
            continue
        relative = path.relative_to(repo)
        if any(part in ignored_parts or part.startswith(".") for part in relative.parts):
            continue
        candidates.append(path)
    yield from sorted(candidates, key=lambda path: path.relative_to(repo).as_posix())


def iter_runtime_files(repo: Path, service_path: str) -> Iterator[Path]:
    """Yield regular, in-repo runtime sources in stable repo-relative order."""
    root = repo / service_path if service_path else repo
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink() or not path.is_file() or path.suffix.lower() not in SOURCE_SUFFIXES:
            continue
        try:
            relative = path.relative_to(repo)
        except ValueError:
            continue
        name = path.name.lower()
        if any(
            part.lower() in RUNTIME_IGNORED_PARTS
            or part.startswith(".")
            or RUNTIME_IGNORED_PART.fullmatch(part)
            for part in relative.parts
        ):
            continue
        if (name.startswith("test_") or re.search(r"_test\.[^.]+$", name) or ".test." in name
                or ".spec." in name or GENERATED_SOURCE_NAME.search(name)):
            continue
        candidates.append(path)
    yield from sorted(candidates, key=lambda path: path.relative_to(repo).as_posix())


def _location(repo: Path, path: Path, line: int) -> dict[str, Any]:
    return {"file": path.relative_to(repo).as_posix(), "line": line}


def _path_value(value: str) -> tuple[str | None, str]:
    value = value.strip()
    quoted = re.fullmatch(r"[\"']([^\"']*)[\"']", value)
    if quoted:
        return quoted.group(1), "high"
    return None, "low"


def _route(
    service_path: str,
    framework: str,
    method: str | None,
    raw_path: str,
    evidence: dict[str, Any],
    auth_scope: str = "unknown",
    receiver: str | None = None,
    middleware_covered: bool = False,
) -> dict[str, Any]:
    path_template, confidence = _path_value(raw_path)
    stable_path = path_template if path_template is not None else f"dynamic:{raw_path.strip()}"
    route_id = hashlib.sha256(
        f"{service_path}|{framework}|{method}|{stable_path}|{evidence['file']}".encode()
    ).hexdigest()[:16]
    slug_source = path_template or f"unresolved-{route_id}"
    slug = re.sub(r"[^a-z0-9]+", "-", slug_source.strip("/").replace("{", "").replace("}", "").lower()).strip("-")
    return {
        "route_id": route_id,
        "framework": framework,
        "method": method,
        "path_template": path_template,
        "confidence": confidence,
        "auth_scope": auth_scope,
        "evidence": evidence,
        "feature_proposal": {
            "slug": slug or "root",
            "confidence": confidence,
            "requires_engineer_signoff": True,
        },
        "_receiver": receiver,
        "_middleware_covered": middleware_covered,
    }


def _mount(
    framework: str,
    target: str,
    raw_prefix: str | None,
    evidence: dict[str, Any],
    resolved: bool = True,
) -> dict[str, Any]:
    if not resolved:
        prefix, confidence = None, "low"
    elif raw_prefix is None:
        prefix, confidence = "", "high"
    else:
        prefix, confidence = _path_value(raw_prefix)
    return {
        "framework": framework,
        "target": target,
        "prefix": prefix,
        "confidence": confidence,
        "evidence": evidence,
    }


def _dotted_name(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except (TypeError, ValueError):
        return ""


def _call_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Call):
        return _dotted_name(node.func)
    return _dotted_name(node)


def _auth_name(value: str) -> bool:
    leaf = value.rsplit(".", 1)[-1]
    return bool(re.search(
        r"(?:authentication|authenticate|authorization|auth)(?:_?middleware)?|"
        r"require_?auth|verify_?(?:jwt|token)|current_?user",
        leaf,
        re.I,
    ))


def _attribution_name(value: str) -> bool:
    leaf = value.rsplit(".", 1)[-1]
    return bool(re.search(r"(?:attribution|moolabs).*(?:middleware|context)|(?:middleware|context).*(?:attribution|moolabs)", leaf, re.I))


def _has_auth_dependency(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and _call_name(child.func).rsplit(".", 1)[-1] == "Depends":
            if child.args and _auth_name(_dotted_name(child.args[0])):
                return True
    return False


def _dead_python_nodes(tree: ast.AST) -> set[int]:
    dead: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.If, ast.While)) and isinstance(node.test, ast.Constant) and not bool(node.test.value):
            for statement in node.body:
                dead.update(id(child) for child in ast.walk(statement))
    return dead


def _literal_source(node: ast.AST | None) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return json.dumps(node.value)
    return _dotted_name(node)


def _attribution_middleware_call(text: str, call: str) -> bool:
    return bool(
        re.search(
            rf"{call}\s*\([^\n\)]*(?:attribution\w*middleware|moolabs\w*middleware|middleware\w*attribution)",
            text,
            re.I,
        )
    )


def _without_line_comments(text: str) -> str:
    return "\n".join(re.sub(r"//.*$", "", line) for line in text.splitlines())


def _without_js_comments(text: str) -> str:
    """Mask JS comments without changing offsets, newlines, or string contents."""
    output = list(text)
    state = "code"
    escaped = False
    index = 0
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                output[index] = " "
        elif state == "block-comment":
            if character == "*" and following == "/":
                output[index] = output[index + 1] = " "
                index += 1
                state = "code"
            elif character != "\n":
                output[index] = " "
        elif state in {"single", "double", "template"}:
            delimiter = {"single": "'", "double": '"', "template": "`"}[state]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == delimiter:
                state = "code"
        elif character == "/" and following == "/":
            output[index] = output[index + 1] = " "
            index += 1
            state = "line-comment"
        elif character == "/" and following == "*":
            output[index] = output[index + 1] = " "
            index += 1
            state = "block-comment"
        elif character == "'":
            state = "single"
        elif character == '"':
            state = "double"
        elif character == "`":
            state = "template"
        index += 1
    return "".join(output)


def _outside_js_string(text: str, position: int) -> bool:
    quote: str | None = None
    escaped = False
    for character in text[:position]:
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
    return quote is None


def _prefixed_raw_path(prefix: str | None, raw_path: str) -> str:
    path, _ = _path_value(raw_path)
    if prefix is None or path is None:
        return raw_path if prefix == "" else "<dynamic>"
    return json.dumps((prefix.rstrip("/") + "/" + path.lstrip("/")) or "/")


def _balanced_call_text(text: str, open_parenthesis: int) -> str:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_parenthesis, len(text)):
        character = text[index]
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return text[open_parenthesis:index + 1]
    return text[open_parenthesis:]


def _balanced_block_end(text: str, open_brace: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(open_brace, len(text)):
        character = text[index]
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"', "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return len(text)


def _boundary_kind(name: str) -> str | None:
    lowered = name.lower()
    qualified = re.fullmatch(r"(axios|httpx|requests)\.\w+", lowered)
    if qualified:
        return qualified.group(1)
    leaf = lowered.rsplit(".", 1)[-1]
    if leaf == "send":
        if "." not in lowered:
            return None
        receiver = lowered.rsplit(".", 1)[0].rsplit(".", 1)[-1]
        return "send" if re.search(r"(?:producer|publisher|queue|broker|kafka|topic|stream)", receiver) else None
    if leaf in {"fetch", "publish", "produce", "delay", "enqueue", "consume", "subscribe"}:
        return leaf
    return None


def _propagation_status(call_text: str) -> str:
    code = _without_string_literals(call_text)
    operation = re.search(
        r"\b((?:inject|extract|bind|propagat)\w*)\s*\(",
        code,
        re.I,
    )
    has_thread_id = re.search(r"\bthread[_-]?id\b", code, re.I)
    operation_names_thread_id = bool(
        operation
        and "threadid" in re.sub(r"[^a-z0-9]", "", operation.group(1).lower())
    )
    return "verified" if operation and (has_thread_id or operation_names_thread_id) else "missing"


def _without_string_literals(text: str) -> str:
    result = list(text)
    quote: str | None = None
    escaped = False
    for index, character in enumerate(text):
        if quote is not None:
            result[index] = " "
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
            result[index] = " "
    return "".join(result)


def _async_boundaries(repo: Path, path: Path, text: str) -> list[dict[str, Any]]:
    boundaries: list[dict[str, Any]] = []
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return boundaries
        dead_nodes = _dead_python_nodes(tree)
        for node in ast.walk(tree):
            if id(node) in dead_nodes or not isinstance(node, ast.Call):
                continue
            kind = _boundary_kind(_call_name(node.func))
            if kind is None:
                continue
            call_text = ast.get_source_segment(text, node) or ""
            boundaries.append({
                "kind": kind,
                "propagation": _propagation_status(call_text),
                "evidence": _location(repo, path, node.lineno),
            })
        return boundaries

    js_suffixes = {".js", ".jsx", ".mjs", ".ts", ".tsx"}
    code = (
        _without_js_comments(text)
        if path.suffix.lower() in js_suffixes
        else _without_line_comments(text)
    )
    if path.suffix.lower() in js_suffixes and re.match(
        r"^\s*(['\"])use client\1\s*;?",
        code,
    ):
        return boundaries
    pattern = re.compile(
        r"\b((?:axios|httpx|requests)\.\w+|fetch|publish|produce|(?:\w+\.)?send|delay|enqueue|consume|subscribe)\s*(\()",
        re.I,
    )
    for match in pattern.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        kind = _boundary_kind(match.group(1))
        if kind is None:
            continue
        call_text = _balanced_call_text(code, match.start(2))
        boundaries.append({
            "kind": kind,
            "propagation": _propagation_status(call_text),
            "evidence": _location(repo, path, code.count("\n", 0, match.start()) + 1),
        })
    return boundaries


def _python_module_name(service_root: Path, path: Path) -> tuple[str, bool]:
    relative = path.relative_to(service_root).with_suffix("")
    parts = list(relative.parts)
    is_package = bool(parts and parts[-1] == "__init__")
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _python_import_aliases(tree: ast.AST, module: str, is_package: bool) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name.split(".", 1)[0]] = imported.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = module.split(".") if is_package else module.split(".")[:-1]
                keep = max(0, len(package) - node.level + 1)
                base = ".".join(package[:keep] + ([base] if base else []))
            for imported in node.names:
                if imported.name == "*":
                    continue
                value = ".".join(part for part in (base, imported.name) if part)
                aliases[imported.asname or imported.name] = value
    return aliases


def _python_service_context(repo: Path, service_path: str, files: list[Path]) -> dict[str, Any]:
    service_root = repo / service_path if service_path else repo
    file_contexts: dict[Path, dict[str, Any]] = {}
    local_prefixes: dict[str, str | None] = {}
    receivers: set[str] = set()

    for path in files:
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        module, is_package = _python_module_name(service_root, path)
        dead_nodes = _dead_python_nodes(tree)
        locals_in_file: set[str] = set()
        for node in ast.walk(tree):
            if id(node) in dead_nodes or not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not isinstance(value, ast.Call) or _call_name(value.func).rsplit(".", 1)[-1] not in {"FastAPI", "APIRouter"}:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            prefix_node = next((keyword.value for keyword in value.keywords if keyword.arg == "prefix"), None)
            local_prefix = _path_value(_literal_source(prefix_node))[0] if prefix_node else ""
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                canonical = ".".join(part for part in (module, target.id) if part)
                receivers.add(canonical)
                locals_in_file.add(target.id)
                local_prefixes[canonical] = local_prefix
        file_contexts[path] = {
            "tree": tree,
            "dead_nodes": dead_nodes,
            "module": module,
            "aliases": _python_import_aliases(tree, module, is_package),
            "locals": locals_in_file,
        }

    def canonical(path: Path, name: str) -> str:
        context = file_contexts.get(path)
        if context is None or not name:
            return name
        head, separator, tail = name.partition(".")
        expanded = context["aliases"].get(head, head)
        if separator:
            expanded = f"{expanded}.{tail}"
        if expanded in receivers:
            return expanded
        local = ".".join(part for part in (context["module"], expanded) if part)
        return local if local in receivers else expanded

    attribution_receivers: set[str] = set()
    global_auth_roots: set[str] = set()
    authenticated_routers: set[str] = set()
    mounted_auth: dict[str, list[bool]] = {}
    parents: dict[str, list[tuple[str, str | None]]] = {}
    for path, context in file_contexts.items():
        tree = context["tree"]
        dead_nodes = context["dead_nodes"]
        for node in ast.walk(tree):
            if id(node) in dead_nodes:
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.Call) and _call_name(value.func).rsplit(".", 1)[-1] in {"FastAPI", "APIRouter"}:
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if not isinstance(target, ast.Name) or not _has_auth_dependency(value):
                            continue
                        receiver = canonical(path, target.id)
                        if _call_name(value.func).rsplit(".", 1)[-1] == "FastAPI":
                            global_auth_roots.add(receiver)
                        else:
                            authenticated_routers.add(receiver)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr == "middleware"
                        and _attribution_name(node.name)
                    ):
                        attribution_receivers.add(canonical(path, _dotted_name(decorator.func.value)))
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            parent = canonical(path, _dotted_name(node.func.value))
            if node.func.attr == "add_middleware" and node.args:
                middleware_name = _dotted_name(node.args[0])
                if _auth_name(middleware_name):
                    global_auth_roots.add(parent)
                if _attribution_name(middleware_name):
                    attribution_receivers.add(parent)
            elif node.func.attr == "include_router" and node.args:
                child = canonical(path, _dotted_name(node.args[0]))
                if child not in receivers:
                    continue
                prefix_node = next((keyword.value for keyword in node.keywords if keyword.arg == "prefix"), None)
                mount_prefix = _path_value(_literal_source(prefix_node))[0] if prefix_node else ""
                parents.setdefault(child, []).append((parent, mount_prefix))
                mounted_auth.setdefault(child, []).append(_has_auth_dependency(node))

    authenticated_routers.update(
        child for child, auth_sites in mounted_auth.items() if auth_sites and all(auth_sites)
    )

    effective_prefixes: dict[str, str | None] = {}

    def effective_prefix(receiver: str, stack: frozenset[str] = frozenset()) -> str | None:
        if receiver in effective_prefixes:
            return effective_prefixes[receiver]
        if receiver in stack:
            return None
        local_prefix = local_prefixes.get(receiver)
        mounted = parents.get(receiver, [])
        if not mounted:
            effective_prefixes[receiver] = local_prefix
            return local_prefix
        values: set[str | None] = set()
        for parent, mount_prefix in mounted:
            parent_prefix = effective_prefix(parent, stack | {receiver})
            if parent_prefix is None or mount_prefix is None or local_prefix is None:
                values.add(None)
            else:
                values.add(
                    (parent_prefix.rstrip("/") + "/" + mount_prefix.strip("/") + "/" + local_prefix.lstrip("/")).rstrip("/")
                    or "/"
                )
        effective_prefixes[receiver] = next(iter(values)) if len(values) == 1 else None
        return effective_prefixes[receiver]

    for receiver in receivers:
        effective_prefix(receiver)

    def inherited(receiver: str, roots: set[str], seen: frozenset[str] = frozenset()) -> bool:
        if receiver in roots:
            return True
        if receiver in seen:
            return False
        return any(inherited(parent, roots, seen | {receiver}) for parent, _ in parents.get(receiver, []))

    return {
        "files": file_contexts,
        "canonical": canonical,
        "prefixes": effective_prefixes,
        "receivers": receivers,
        "attribution_receivers": attribution_receivers,
        "attribution_covered": {receiver for receiver in receivers if inherited(receiver, attribution_receivers)},
        "global_auth": {receiver for receiver in receivers if inherited(receiver, global_auth_roots)},
        "authenticated_routers": authenticated_routers,
    }


def _scan_python(
    repo: Path,
    service_path: str,
    path: Path,
    text: str,
    service_context: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return routes, False, mounts
    dead_nodes = _dead_python_nodes(tree)
    framework = "fastapi" if any(
        isinstance(node, (ast.Name, ast.Attribute)) and _dotted_name(node).rsplit(".", 1)[-1] in {"FastAPI", "APIRouter"}
        for node in ast.walk(tree)
    ) else "starlette"
    canonical = (
        (lambda value: service_context["canonical"](path, value))
        if service_context is not None
        else (lambda value: value)
    )
    prefixes: dict[str, str | None] = dict(service_context["prefixes"]) if service_context else {"app": ""}
    authenticated_routers: set[str] = set(service_context["authenticated_routers"]) if service_context else set()
    global_auth: set[str] = set(service_context["global_auth"]) if service_context else set()
    attribution_receivers: set[str] = set(service_context["attribution_receivers"]) if service_context else set()
    attribution_covered: set[str] = set(service_context["attribution_covered"]) if service_context else attribution_receivers
    function_defs = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in dead_nodes
    }

    for node in ast.walk(tree):
        if id(node) in dead_nodes:
            continue
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if isinstance(value, ast.Call) and _call_name(value.func).rsplit(".", 1)[-1] in {"FastAPI", "APIRouter"}:
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    prefix_node = next((keyword.value for keyword in value.keywords if keyword.arg == "prefix"), None)
                    receiver_name = canonical(target.id)
                    if service_context is None:
                        prefixes[receiver_name] = _path_value(_literal_source(prefix_node))[0] if prefix_node else ""
                    if _has_auth_dependency(value):
                        if _call_name(value.func).rsplit(".", 1)[-1] == "FastAPI":
                            global_auth.add(receiver_name)
                        else:
                            authenticated_routers.add(receiver_name)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = canonical(_dotted_name(node.func.value))
        if node.func.attr == "add_middleware" and node.args:
            middleware_name = _dotted_name(node.args[0])
            if _auth_name(middleware_name):
                global_auth.add(receiver)
            if _attribution_name(middleware_name):
                attribution_receivers.add(receiver)
        if node.func.attr == "include_router" and node.args:
            target = _dotted_name(node.args[0])
            router_name = canonical(target)
            prefix_node = next((keyword.value for keyword in node.keywords if keyword.arg == "prefix"), None)
            raw_prefix = _literal_source(prefix_node) if prefix_node else None
            resolved = router_name in prefixes
            mount_path = _path_value(raw_prefix)[0] if raw_prefix is not None else ""
            child = prefixes.get(router_name)
            if resolved and service_context is None:
                prefixes[router_name] = None if mount_path is None or child is None else mount_path.rstrip("/") + "/" + child.lstrip("/")
                if _has_auth_dependency(node):
                    authenticated_routers.add(router_name)
            mount = _mount("fastapi", target, raw_prefix, _location(repo, path, node.lineno), resolved)
            mount["_target_receiver"] = router_name
            mounts.append(mount)

    for node in ast.walk(tree):
        if id(node) in dead_nodes or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "middleware"
                and _attribution_name(node.name)
            ):
                attribution_receivers.add(canonical(_dotted_name(decorator.func.value)))

    for node in ast.walk(tree):
        if id(node) in dead_nodes:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                receiver = canonical(_dotted_name(decorator.func.value))
                method = decorator.func.attr.upper()
                if decorator.func.attr == "middleware" and _attribution_name(node.name):
                    attribution_receivers.add(receiver)
                    continue
                if method not in METHODS or not decorator.args:
                    continue
                scope = (
                    "handler" if _has_auth_dependency(node) or _has_auth_dependency(decorator)
                    else "router" if receiver in authenticated_routers
                    else "global" if receiver in global_auth
                    else "unknown"
                )
                routes.append(_route(
                    service_path, framework, method,
                    _prefixed_raw_path(prefixes.get(receiver, ""), _literal_source(decorator.args[0])),
                    _location(repo, path, decorator.lineno), scope, receiver,
                    receiver in attribution_covered,
                ))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route" and node.args:
            receiver = canonical(_dotted_name(node.func.value))
            handler_name = _dotted_name(node.args[1]).rsplit(".", 1)[-1] if len(node.args) > 1 else ""
            handler = function_defs.get(handler_name)
            methods_node = next((keyword.value for keyword in node.keywords if keyword.arg == "methods"), None)
            methods = [item.value for item in methods_node.elts if isinstance(item, ast.Constant)] if isinstance(methods_node, (ast.List, ast.Tuple)) else [None]
            for method in methods:
                routes.append(_route(
                    service_path, framework, str(method).upper() if method else None,
                    _prefixed_raw_path(prefixes.get(receiver, ""), _literal_source(node.args[0])),
                    _location(repo, path, node.lineno),
                    "handler" if _has_auth_dependency(node) or (handler is not None and _has_auth_dependency(handler))
                    else "router" if receiver in authenticated_routers
                    else "global" if receiver in global_auth
                    else "unknown",
                    receiver,
                    receiver in attribution_covered,
                ))
        if isinstance(node, ast.Call) and _call_name(node.func).rsplit(".", 1)[-1] == "Route" and node.args:
            methods_node = next((keyword.value for keyword in node.keywords if keyword.arg == "methods"), None)
            methods = [item.value for item in methods_node.elts if isinstance(item, ast.Constant)] if isinstance(methods_node, (ast.List, ast.Tuple)) else [None]
            for method in methods:
                routes.append(_route(service_path, "starlette", str(method).upper() if method else None, _literal_source(node.args[0]), _location(repo, path, node.lineno)))
    return routes, bool(attribution_receivers), mounts


JS_SOURCE_SUFFIXES = {".js", ".jsx", ".mjs", ".ts", ".tsx"}
JS_RECEIVER_DECLARATION_PATTERN = re.compile(
    r"(?:\b(?:const|let|var)\s+)?\b(\w+)\s*=\s*"
    r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()"
)
JS_EXPORTED_RECEIVER_PATTERN = re.compile(
    r"\bexport\s+(?:const|let|var)\s+(\w+)\s*=\s*"
    r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()"
)


def _js_receiver_framework(constructor: str) -> str:
    return "hono" if "Hono" in constructor else "express"


def _js_imported_route_receivers(files: list[Path]) -> dict[Path, dict[str, str]]:
    source_files = [path for path in files if path.suffix.lower() in JS_SOURCE_SUFFIXES]
    exported: dict[Path, dict[str, str]] = {}
    for path in source_files:
        code = _without_js_comments(path.read_text(encoding="utf-8", errors="replace"))
        for match in JS_EXPORTED_RECEIVER_PATTERN.finditer(code):
            if _outside_js_string(code, match.start()):
                exported.setdefault(path.resolve(), {})[match.group(1)] = (
                    _js_receiver_framework(match.group(2))
                )

    imported: dict[Path, dict[str, str]] = {}
    import_pattern = re.compile(
        r"\bimport\s*\{([^}]+)\}\s*from\s*['\"](\.[^'\"]+)['\"]",
        re.S,
    )
    for path in source_files:
        code = _without_js_comments(path.read_text(encoding="utf-8", errors="replace"))
        for match in import_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            module = (path.parent / match.group(2)).resolve()
            candidates = [module]
            if module.suffix.lower() not in JS_SOURCE_SUFFIXES:
                candidates.extend(Path(f"{module}{suffix}") for suffix in JS_SOURCE_SUFFIXES)
                candidates.extend(module / f"index{suffix}" for suffix in JS_SOURCE_SUFFIXES)
            source_exports = next(
                (exported[candidate] for candidate in candidates if candidate in exported),
                None,
            )
            if source_exports is None:
                continue
            for binding in match.group(1).split(","):
                parts = re.split(r"\s+as\s+", binding.strip(), maxsplit=1)
                exported_name = parts[0].removeprefix("type ").strip()
                local_name = parts[-1].strip()
                framework = source_exports.get(exported_name)
                if framework is not None:
                    imported.setdefault(path, {})[local_name] = framework
    return imported


def _scan_js(
    repo: Path,
    service_path: str,
    path: Path,
    text: str,
    imported_receivers: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    code = _without_js_comments(text)
    receiver_frameworks = dict(imported_receivers or {})
    route_receivers: set[str] = set(receiver_frameworks)
    declared_receivers: set[str] = set()
    parents: dict[str, list[tuple[str, str | None]]] = {}
    unsupported_receivers = set(
        re.findall(r"\b(\w+)\s*=\s*(?:Fastify|fastify)\s*\(", code)
    )
    attribution_pattern = re.compile(
        r"\b(\w+)\.use\s*\([^\)]*?(?:attribution\w*middleware|moolabs\w*middleware|middleware\w*attribution)",
        re.I | re.S,
    )
    auth_pattern = re.compile(
        r"\b(\w+)\.use\s*\([^\)]*?(?:require[_-]?auth|authenticate\w*|auth(?:entication)?middleware|verify(?:jwt|token))",
        re.I | re.S,
    )
    attribution_receivers = {match.group(1) for match in attribution_pattern.finditer(code) if _outside_js_string(code, match.start())}
    auth_receivers = {match.group(1) for match in auth_pattern.finditer(code) if _outside_js_string(code, match.start())}
    for match in JS_RECEIVER_DECLARATION_PATTERN.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        route_receivers.add(match.group(1))
        declared_receivers.add(match.group(1))
        receiver_frameworks[match.group(1)] = _js_receiver_framework(match.group(2))
    mount_pattern = re.compile(
        r"\b(\w+)\.(use|route)\s*\(\s*([^,\)]+)\s*,\s*(\w+)\s*\)",
        re.S,
    )
    for match in mount_pattern.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        parent, mount_kind, mount, child = match.groups()
        if parent not in route_receivers and child not in declared_receivers:
            continue
        resolved = child in declared_receivers
        if resolved:
            parents.setdefault(child, []).append((parent, _path_value(mount)[0]))
        route_receivers.add(child)
        line = code.count("\n", 0, match.start()) + 1
        mounts.append(_mount("hono" if mount_kind == "route" else "express", child, mount, _location(repo, path, line), resolved))

    effective_prefixes: dict[str, str | None] = {}

    def effective_prefix(receiver: str, stack: frozenset[str] = frozenset()) -> str | None:
        if receiver in effective_prefixes:
            return effective_prefixes[receiver]
        if receiver in stack:
            return None
        mounted = parents.get(receiver, [])
        if not mounted:
            effective_prefixes[receiver] = ""
            return ""
        values: set[str | None] = set()
        for parent, mount_prefix in mounted:
            parent_prefix = effective_prefix(parent, stack | {receiver})
            if parent_prefix is None or mount_prefix is None:
                values.add(None)
            else:
                values.add(
                    (parent_prefix.rstrip("/") + "/" + mount_prefix.lstrip("/")).rstrip("/")
                    or "/"
                )
        effective_prefixes[receiver] = next(iter(values)) if len(values) == 1 else None
        return effective_prefixes[receiver]
    pattern = re.compile(
        r"\b(\w+)\.(get|post|put|patch|delete|head|options)\s*\(\s*([^,\)]+)",
        re.I | re.S,
    )
    default_framework = "hono" if re.search(r"\bHono\b", code) else "express"
    for match in pattern.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        if match.group(1) not in route_receivers:
            continue
        receiver = match.group(1)
        if receiver in unsupported_receivers:
            continue
        line = code.count("\n", 0, match.start()) + 1
        arguments = code[match.end():code.find(")", match.end())]
        handler_auth = bool(re.search(r"\b(?:require[_-]?auth|authenticate\w*|verify(?:jwt|token)|withAuth)\b", arguments, re.I))
        scope = "handler" if handler_auth else "global" if receiver in auth_receivers else "unknown"
        routes.append(_route(
            service_path, receiver_frameworks.get(receiver, default_framework),
            match.group(2).upper(),
            _prefixed_raw_path(effective_prefix(receiver), match.group(3).strip()),
            _location(repo, path, line), scope, receiver,
            receiver in attribution_receivers,
        ))
    if re.search(r"(?:^|/)app/(?:.+/)?route\.(?:ts|tsx|js|jsx)$", path.relative_to(repo).as_posix()):
        route_parts = list(path.relative_to(repo).parts)
        app_index = route_parts.index("app")
        segments = route_parts[app_index + 1:-1]
        rendered = ["{" + segment[1:-1] + "}" if segment.startswith("[") and segment.endswith("]") else segment for segment in segments]
        template = "/" + "/".join(segment for segment in rendered if segment)
        for number, line in enumerate(code.splitlines(), 1):
            match = re.search(
                r"\bexport\s+(?:(?:async\s+)?function\s+|const\s+)(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b",
                line,
            )
            if match:
                if not _outside_js_string(line, match.start()):
                    continue
                scope = "handler" if re.search(r"\b(?:withAuth|requireAuth|authenticate)\b", line, re.I) else "unknown"
                routes.append(_route(service_path, "nextjs-app-router", match.group(1), json.dumps(template), _location(repo, path, number), scope))
    return routes, bool(attribution_receivers), mounts


GO_ATTRIBUTION_PATTERN = re.compile(
    r"\b(\w+)\.Use\s*\([^\)]*?(?:Attribution\w*Middleware|Moolabs\w*Middleware|Middleware\w*Attribution)",
    re.I | re.S,
)


def _go_function_blocks(code: str) -> list[tuple[int, int, str, set[str]]]:
    blocks: list[tuple[int, int, str, set[str]]] = []
    pattern = re.compile(
        r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)[^{]*\{",
        re.S,
    )
    for match in pattern.finditer(code):
        router_parameters = set(
            re.findall(r"\b(\w+)\s+(?:chi\.Router|\*chi\.Mux)\b", match.group(2))
        )
        blocks.append((
            match.start(),
            _balanced_block_end(code, match.end() - 1),
            match.group(1),
            router_parameters,
        ))
    return blocks


def _go_attributed_call_targets(text: str) -> set[str]:
    code = _without_js_comments(text)
    blocks = _go_function_blocks(code)
    targets: set[str] = set()
    for middleware in GO_ATTRIBUTION_PATTERN.finditer(code):
        containing = [
            block for block in blocks if block[0] <= middleware.start() < block[1]
        ]
        if not containing:
            continue
        block = min(containing, key=lambda item: item[1] - item[0])
        receiver = re.escape(middleware.group(1))
        call_pattern = re.compile(
            rf"(?<![\w.])([A-Za-z_]\w*)\s*\(\s*{receiver}\s*\)"
        )
        targets.update(
            call.group(1)
            for call in call_pattern.finditer(code, middleware.end(), block[1])
        )
    return targets


def _scan_go(repo: Path, service_path: str, path: Path, text: str) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    code = _without_js_comments(text)
    auth_pattern = re.compile(
        r"\b(\w+)\.Use\s*\([^\)]*?(?:Auth\w*|Authenticate\w*|Verify(?:JWT|Token))",
        re.I | re.S,
    )
    attribution_receivers = {match.group(1) for match in GO_ATTRIBUTION_PATTERN.finditer(code) if _outside_js_string(code, match.start())}
    auth_receivers = {match.group(1) for match in auth_pattern.finditer(code) if _outside_js_string(code, match.start())}
    framework = "chi" if "github.com/go-chi/chi" in code or re.search(r"\bchi\.\w+", code) else "net-http"
    chi_receivers = set(re.findall(r"\b(\w+)\s*(?::=|=)\s*chi\.NewRouter\s*\(", code))
    chi_receivers.update(re.findall(r"\b(\w+)\s+(?:chi\.Router|\*chi\.Mux)\b", code))
    function_blocks = _go_function_blocks(code)
    parents: dict[str, list[tuple[str, str | None]]] = {}
    mount_pattern = re.compile(
        r"\b(\w+)\.Mount\s*\(\s*([^,\)]+)(?:\s*,\s*([\w.]+))?",
        re.S,
    )
    for mount in mount_pattern.finditer(code):
        if not _outside_js_string(code, mount.start()) or mount.group(1) not in chi_receivers:
            continue
        child = mount.group(3)
        resolved = child in chi_receivers if child else True
        if child and resolved:
            parents.setdefault(child, []).append((mount.group(1), _path_value(mount.group(2).strip())[0]))
        number = code.count("\n", 0, mount.start()) + 1
        mounts.append(_mount(
            framework, child or "inline-router", mount.group(2).strip(),
            _location(repo, path, number), resolved,
        ))

    effective_prefixes: dict[str, str | None] = {}

    def effective_prefix(receiver: str, stack: frozenset[str] = frozenset()) -> str | None:
        if receiver in effective_prefixes:
            return effective_prefixes[receiver]
        if receiver in stack:
            return None
        mounted = parents.get(receiver, [])
        if not mounted:
            effective_prefixes[receiver] = ""
            return ""
        values: set[str | None] = set()
        for parent, mount_prefix in mounted:
            parent_prefix = effective_prefix(parent, stack | {receiver})
            if parent_prefix is None or mount_prefix is None:
                values.add(None)
            else:
                values.add(
                    (parent_prefix.rstrip("/") + "/" + mount_prefix.lstrip("/")).rstrip("/")
                    or "/"
                )
        effective_prefixes[receiver] = next(iter(values)) if len(values) == 1 else None
        return effective_prefixes[receiver]

    inline_blocks: list[tuple[int, int, str, str | None]] = []
    inline_route_pattern = re.compile(
        r"\b(\w+)\.Route\s*\(\s*([^,\)]+)\s*,\s*"
        r"func\s*\(\s*(\w+)\s+chi\.Router\s*\)\s*\{",
        re.S,
    )
    for mount in inline_route_pattern.finditer(code):
        parent, raw_prefix, child = mount.groups()
        if parent not in chi_receivers:
            continue
        containing = [
            block for block in inline_blocks
            if block[0] < mount.start() < block[1] and block[2] == parent
        ]
        parent_prefix = (
            min(containing, key=lambda block: block[1] - block[0])[3]
            if containing
            else effective_prefix(parent)
        )
        mount_prefix = _path_value(raw_prefix.strip())[0]
        block_prefix = (
            None
            if parent_prefix is None or mount_prefix is None
            else (parent_prefix.rstrip("/") + "/" + mount_prefix.lstrip("/")).rstrip("/") or "/"
        )
        block_end = _balanced_block_end(code, mount.end() - 1)
        inline_blocks.append((mount.start(), block_end, child, block_prefix))
        number = code.count("\n", 0, mount.start()) + 1
        mounts.append(_mount(
            framework, "inline-router", raw_prefix.strip(),
            _location(repo, path, number),
        ))
    patterns = (
        re.compile(r"\bhttp\.HandleFunc\s*\(\s*([^,\)]+)", re.S),
        re.compile(r"\bhttp\.Handle\s*\(\s*([^,\)]+)", re.S),
        re.compile(
            r"\b(\w+)\.(Get|Post|Put|Patch|Delete|Head|Options|Method)\s*\(\s*([^,\)]+)",
            re.I | re.S,
        ),
    )
    for pattern in patterns[:2]:
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            number = code.count("\n", 0, match.start()) + 1
            routes.append(_route(
                service_path, framework, None, match.group(1).strip(),
                _location(repo, path, number),
            ))
    for match in patterns[2].finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        receiver, method, raw_path = match.groups()
        if receiver not in chi_receivers or method.upper() == "METHOD":
            continue
        number = code.count("\n", 0, match.start()) + 1
        containing = [
            block for block in inline_blocks
            if block[0] < match.start() < block[1] and block[2] == receiver
        ]
        route_prefix = (
            min(containing, key=lambda block: block[1] - block[0])[3]
            if containing
            else effective_prefix(receiver)
        )
        route = _route(
            service_path, framework, method.upper(),
            _prefixed_raw_path(route_prefix, raw_path.strip()),
            _location(repo, path, number),
            "global" if receiver in auth_receivers else "unknown", receiver,
            receiver in attribution_receivers,
        )
        containing_function = [
            block for block in function_blocks
            if block[0] <= match.start() < block[1] and receiver in block[3]
        ]
        if containing_function:
            route["_go_function"] = min(
                containing_function,
                key=lambda item: item[1] - item[0],
            )[2]
        routes.append(route)
    return routes, bool(attribution_receivers), mounts


IDENTITY_VERIFIER_PATTERN = re.compile(
    r"(?:verify_signed_(?:(?:customer|tenant)_identity|(?:customer|tenant|identity)(?:_id)?)|"
    r"verify_(?:customer|tenant)_(?:jwt|token)|"
    r"authenticate_(?:customer|tenant)_identity)",
    re.I,
)


def _verified_identity_source_line(
    value: ast.AST,
    text: str,
    raw_pattern: re.Pattern[str],
    raw_variables: dict[str, int],
) -> int | None:
    if not isinstance(value, ast.Call) or not IDENTITY_VERIFIER_PATTERN.fullmatch(
        _call_name(value.func).rsplit(".", 1)[-1]
    ):
        return None
    if not value.args:
        return None
    source = value.args[0]
    if isinstance(source, ast.Name):
        return raw_variables.get(source.id)
    source_text = ast.get_source_segment(text, source) or _dotted_name(source)
    return source.lineno if raw_pattern.search(source_text) else None


def _verified_python_identity_header_lines(text: str, raw_pattern: re.Pattern[str]) -> set[int]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    verified_sources: set[int] = set()
    dead_nodes = _dead_python_nodes(tree)
    context_target = re.compile(
        r"request\.(?:state|context)\.[A-Za-z_]*(?:customer|tenant|account)[A-Za-z_]*",
        re.I,
    )
    for function in (
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        raw_variables: dict[str, int] = {}
        verified_variables: dict[str, int] = {}
        scoped_nodes: list[ast.AST] = list(function.body)
        scoped_assignments: list[ast.Assign | ast.AnnAssign] = []
        while scoped_nodes:
            node = scoped_nodes.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and id(node) not in dead_nodes:
                scoped_assignments.append(node)
            scoped_nodes.extend(ast.iter_child_nodes(node))
        assignments = sorted(
            scoped_assignments,
            key=lambda node: (node.lineno, node.col_offset),
        )
        for assignment in assignments:
            value = assignment.value
            targets = assignment.targets if isinstance(assignment, ast.Assign) else [assignment.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
            expression = _dotted_name(value)
            source_line = _verified_identity_source_line(
                value,
                text,
                raw_pattern,
                raw_variables,
            )
            if source_line is None and raw_pattern.search(expression):
                for name in names:
                    raw_variables[name] = assignment.lineno
                continue

            if source_line is not None:
                for name in names:
                    verified_variables[name] = source_line

            for target in targets:
                if not context_target.fullmatch(_dotted_name(target)):
                    continue
                if isinstance(value, ast.Name) and value.id in verified_variables:
                    verified_sources.add(verified_variables[value.id])
                elif source_line is not None:
                    verified_sources.add(source_line)
    return verified_sources


def _resolver_and_async(repo: Path, files: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_headers: list[dict[str, Any]] = []
    candidates: list[tuple[Path, int, str, str]] = []
    async_hops: list[dict[str, Any]] = []
    raw_pattern = re.compile(r"(?:headers\s*\[|headers?\.(?:get|Get)\s*\()[^\n]*(?:x[-_]?(?:moolabs|customer|tenant)[-_]?(?:id|customer|tenant)?)", re.I)
    context_pattern = re.compile(r"(?:request\.(?:state|context)|claims|auth|current_user)\.[A-Za-z_]*(?:customer|tenant|account)[A-Za-z_]*", re.I)
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        verified_header_lines = (
            _verified_python_identity_header_lines(text, raw_pattern)
            if path.suffix == ".py"
            else set()
        )
        lines = text.splitlines()
        for number, line in enumerate(lines, 1):
            if raw_pattern.search(line):
                if number in verified_header_lines:
                    raw_headers.append({
                        "code": "verified_identity_header",
                        "severity": "info",
                        "message": "raw inbound identity header has a supported verification and context-binding chain",
                        "evidence": _location(repo, path, number),
                    })
                else:
                    raw_headers.append({"code": "raw_identity_header", "severity": "high", "message": "raw inbound identity header is not trusted resolver evidence", "evidence": _location(repo, path, number)})
        async_hops.extend(_async_boundaries(repo, path, text))
        if path.suffix != ".py":
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        dead_nodes = _dead_python_nodes(tree)
        for function in (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in dead_nodes
        ):
            trusted_roots: set[str] = set()
            raw_variables: dict[str, int] = {}
            verified_variables: set[str] = set()
            verified_contexts: set[str] = set()
            positional = list(function.args.posonlyargs) + list(function.args.args)
            defaults = [None] * (len(positional) - len(function.args.defaults)) + list(function.args.defaults)
            for argument, default in zip(positional, defaults):
                if isinstance(default, ast.Call) and _call_name(default.func).rsplit(".", 1)[-1] == "Depends":
                    if default.args and _auth_name(_dotted_name(default.args[0])):
                        trusted_roots.add(argument.arg)
            aliases: dict[str, tuple[str, str]] = {}
            scoped_nodes: list[ast.AST] = list(function.body)
            events: list[ast.Assign | ast.AnnAssign | ast.Call] = []
            parents: dict[int, ast.AST] = {}
            while scoped_nodes:
                node = scoped_nodes.pop()
                if id(node) in dead_nodes:
                    continue
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Call)):
                    events.append(node)
                for child in ast.iter_child_nodes(node):
                    parents[id(child)] = node
                    scoped_nodes.append(child)

            def event_order(
                node: ast.Assign | ast.AnnAssign | ast.Call,
            ) -> tuple[int, int, int, int]:
                if isinstance(node, ast.Call):
                    ancestor = parents.get(id(node))
                    while ancestor is not None and not isinstance(
                        ancestor,
                        (ast.Assign, ast.AnnAssign),
                    ):
                        ancestor = parents.get(id(ancestor))
                    if isinstance(ancestor, (ast.Assign, ast.AnnAssign)):
                        return (
                            ancestor.lineno,
                            ancestor.col_offset,
                            0,
                            node.col_offset,
                        )
                return (
                    node.lineno,
                    node.col_offset,
                    1 if isinstance(node, (ast.Assign, ast.AnnAssign)) else 0,
                    node.col_offset,
                )

            events.sort(key=event_order)

            for node in events:
                if isinstance(node, (ast.Assign, ast.AnnAssign)):
                    value = node.value
                    targets = (
                        node.targets if isinstance(node, ast.Assign) else [node.target]
                    )
                    target_names = [
                        target.id for target in targets if isinstance(target, ast.Name)
                    ]
                    expression = _dotted_name(value)
                    verified_source_line = _verified_identity_source_line(
                        value,
                        text,
                        raw_pattern,
                        raw_variables,
                    )
                    direct_raw_source = bool(
                        verified_source_line is None and raw_pattern.search(expression)
                    )
                    inherited_verified = bool(
                        isinstance(value, ast.Name)
                        and value.id in verified_variables
                    )
                    inherited_provenance = (
                        aliases.get(value.id) if isinstance(value, ast.Name) else None
                    )
                    root = expression.split(".", 1)[0]
                    trusted_context = bool(
                        context_pattern.fullmatch(expression)
                        and (root in trusted_roots or expression in verified_contexts)
                    )

                    for target_name in target_names:
                        raw_variables.pop(target_name, None)
                        verified_variables.discard(target_name)
                        aliases.pop(target_name, None)
                        trusted_roots.discard(target_name)
                    for target in targets:
                        context_expression = _dotted_name(target)
                        if not context_pattern.fullmatch(context_expression):
                            continue
                        verified_contexts.discard(context_expression)
                        if verified_source_line is not None or inherited_verified:
                            verified_contexts.add(context_expression)

                    if isinstance(value, ast.Call) and _auth_name(_call_name(value.func)):
                        trusted_roots.update(target_names)
                    if direct_raw_source:
                        for target_name in target_names:
                            raw_variables[target_name] = node.lineno
                    if verified_source_line is not None:
                        verified_variables.update(target_names)

                    provenance = inherited_provenance
                    if provenance is None and trusted_context:
                        provenance = ("trusted", expression)
                    elif provenance is None and direct_raw_source:
                        provenance = ("raw", expression)
                    if provenance is not None:
                        for target_name in target_names:
                            aliases[target_name] = provenance
                    continue

                if not node.args:
                    continue
                validator = _call_name(node.func)
                argument = node.args[0]
                if isinstance(argument, ast.Name):
                    provenance = aliases.get(argument.id)
                else:
                    expression = _dotted_name(argument)
                    root = expression.split(".", 1)[0]
                    provenance = (
                        ("trusted", expression)
                        if context_pattern.fullmatch(expression)
                        and (root in trusted_roots or expression in verified_contexts)
                        else None
                    )
                if not provenance or provenance[0] != "trusted":
                    continue
                if re.fullmatch(r"(?:UUID|uuid\.UUID|uuid\.Parse|validate_uuid|is_uuid|parse_uuid)", validator, re.I):
                    candidates.append((path, node.lineno, provenance[1], "moolabs_uuid"))
                elif re.search(r"(?:crosswalk|resolve_customer|lookup_customer)", validator, re.I):
                    candidates.append((path, node.lineno, provenance[1], "external_key_crosswalk"))
    unsupported = {
        "JavaScript/TypeScript": next(
            (path for path in files if path.suffix.lower() in {".js", ".jsx", ".mjs", ".ts", ".tsx"}),
            None,
        ),
        "Go": next((path for path in files if path.suffix.lower() == ".go"), None),
    }
    for language, path in unsupported.items():
        if path is not None:
            raw_headers.append({
                "code": "resolver_provenance_unsupported",
                "severity": "info",
                "message": f"resolver provenance analysis is unsupported for {language}",
                "evidence": _location(repo, path, 1),
            })
    if candidates:
        path, number, expression, kind = sorted(candidates, key=lambda item: (item[0].as_posix(), item[1]))[0]
        return ({"state": "proposed", "identity_kind": kind, "expression": expression,
                 "template": "reject empty values and validate before binding attribution context",
                 "evidence": _location(repo, path, number)}, raw_headers, async_hops)
    return ({"state": "unresolved", "identity_kind": None, "expression": None,
             "template": None, "evidence": None}, raw_headers, async_hops)


def _python_parse_findings(repo: Path, files: list[Path]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as error:
            findings.append({
                "code": "python_parse_error",
                "severity": "warning",
                "message": "Python source could not be parsed; discovery for this file is unknown",
                "evidence": _location(repo, path, error.lineno or 1),
            })
    return findings


def _fingerprint(repo: Path, services: list[dict[str, Any]]) -> dict[str, str]:
    digest = hashlib.sha256()
    inputs = set(_repo_scan_inputs(repo))
    for service in services:
        inputs.update(iter_runtime_files(repo, service["service_path"]))
    for path in sorted(inputs, key=lambda item: item.relative_to(repo).as_posix()):
        digest.update(path.relative_to(repo).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return {"algorithm": "sha256", "value": digest.hexdigest()}


def _relevant_source_path(
    relative: Path,
    service_paths: list[str],
    manifest_names: set[str],
) -> bool:
    if any(
        part.lower() in RUNTIME_IGNORED_PARTS
        or part.startswith(".")
        or RUNTIME_IGNORED_PART.fullmatch(part)
        for part in relative.parts
    ):
        return False
    if relative.name in manifest_names:
        return True
    if not any(
        service_path in {"", "."}
        or relative == Path(service_path)
        or Path(service_path) in relative.parents
        for service_path in service_paths
    ):
        return False
    if relative.suffix.lower() not in SOURCE_SUFFIXES:
        return False
    name = relative.name.lower()
    return not (
        name.startswith("test_")
        or re.search(r"_test\.[^.]+$", name)
        or ".test." in name
        or ".spec." in name
        or GENERATED_SOURCE_NAME.search(name)
    )


def _git_output(repo: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _source_revision(repo: Path, services: list[dict[str, Any]]) -> dict[str, str | None]:
    top_level_result = _git_output(repo, "rev-parse", "--show-toplevel")
    commit_result = _git_output(repo, "rev-parse", "--verify", "HEAD^{commit}")
    if top_level_result.returncode or commit_result.returncode:
        return {"state": "unversioned", "git_commit": None}
    commit = commit_result.stdout.decode("ascii", errors="ignore").strip()
    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit):
        return {"state": "unversioned", "git_commit": None}

    git_root = Path(top_level_result.stdout.decode("utf-8", errors="surrogateescape").strip()).resolve()
    service_paths = [service["service_path"] for service in services]
    scanner = _load_repo_scan()
    manifest_names = {
        name for names in scanner.MANIFESTS.values() for name in names
    }
    changed = _git_output(git_root, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = _git_output(git_root, "ls-files", "--others", "--exclude-standard", "-z", "--")
    if changed.returncode or untracked.returncode:
        return {"state": "unversioned", "git_commit": None}
    for raw_path in (changed.stdout + untracked.stdout).split(b"\0"):
        if not raw_path:
            continue
        absolute = git_root / raw_path.decode("utf-8", errors="surrogateescape")
        try:
            relative = absolute.relative_to(repo.resolve())
        except ValueError:
            continue
        if _relevant_source_path(relative, service_paths, manifest_names):
            return {"state": "dirty", "git_commit": None}
    return {"state": "clean", "git_commit": commit}


def _ignored_service_path(service_path: str) -> bool:
    parts = [part.lower() for part in Path(service_path).parts]
    if any(part in RUNTIME_IGNORED_PARTS or part.startswith(".") for part in parts):
        return True
    if any(SDK_SOURCE_PART.search(part) for part in parts):
        return True
    return any(parts[index:index + 2] == ["api", "client"] for index in range(len(parts) - 1))


def select_services(repo: Path, selector: str | None) -> list[Any]:
    profile = _load_repo_scan().scan(repo)
    services = sorted(
        (service for service in profile.services if not _ignored_service_path(service.path or "")),
        key=lambda service: service.path,
    )
    if selector is None and len(services) > 1 and any(service.path for service in services):
        services = [service for service in services if service.path]
    if selector is None:
        return services
    normalized = selector.strip("/")
    exact = [service for service in services if service.path.strip("/") == normalized]
    if exact:
        return exact
    basename = [service for service in services if Path(service.path).name == normalized]
    if len(basename) == 1:
        return basename
    if not basename:
        raise DiscoveryError(f"service not found: {selector}")
    raise DiscoveryError(f"service selector is ambiguous: {selector}")


def discover(repo: Path, generated_at: str, service_selector: str | None = None) -> dict[str, Any]:
    if not repo.is_dir():
        raise DiscoveryError(f"repo not found: {repo}")
    services: list[dict[str, Any]] = []
    all_findings: list[dict[str, Any]] = []
    for profile in select_services(repo, service_selector):
        service_path = profile.path
        files = list(iter_runtime_files(repo, service_path))
        python_context = _python_service_context(repo, service_path, files)
        js_imported_receivers = _js_imported_route_receivers(files)
        go_attributed_call_targets = set().union(*(
            _go_attributed_call_targets(
                path.read_text(encoding="utf-8", errors="replace")
            )
            for path in files
            if path.suffix == ".go"
        ))
        routes: list[dict[str, Any]] = []
        mounts: list[dict[str, Any]] = []
        middleware = False
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix == ".py" and profile.frameworks_detected and not (
                set(profile.frameworks_detected) & SUPPORTED_PYTHON_FRAMEWORKS
            ):
                detected, present, detected_mounts = [], False, []
            elif path.suffix == ".py":
                detected, present, detected_mounts = _scan_python(
                    repo, service_path, path, text, python_context,
                )
            elif path.suffix == ".go":
                detected, present, detected_mounts = _scan_go(repo, service_path, path, text)
            elif profile.frameworks_detected and not (
                set(profile.frameworks_detected) & SUPPORTED_JS_FRAMEWORKS
            ):
                detected, present, detected_mounts = [], False, []
            else:
                detected, present, detected_mounts = _scan_js(
                    repo,
                    service_path,
                    path,
                    text,
                    js_imported_receivers.get(path),
                )
            routes.extend(detected)
            mounts.extend(detected_mounts)
            middleware = middleware or present
        resolver, findings, async_hops = _resolver_and_async(repo, files)
        findings.extend(_python_parse_findings(repo, files))
        unresolved_mount_targets = {
            mount.get("_target_receiver", mount["target"].rsplit(".", 1)[-1])
            for mount in mounts
            if mount["prefix"] is None or mount["confidence"] == "low"
        }
        for route in routes:
            if route.get("_receiver") not in unresolved_mount_targets:
                continue
            route["path_template"] = None
            route["confidence"] = "low"
            route["feature_proposal"]["slug"] = f"unresolved-{route['route_id']}"
            route["feature_proposal"]["confidence"] = "low"
        ingress_state = (
            "http-ingress" if routes
            else "no-middleware-inherits-thread-id" if profile.execution_runtimes and not profile.frameworks_detected
            else "unknown"
        )
        route_keys: set[tuple[Any, ...]] = set()
        unique_routes: list[dict[str, Any]] = []
        for route in sorted(routes, key=lambda item: (item["framework"], str(item["method"]), str(item["path_template"]), item["evidence"]["file"], item["evidence"]["line"])):
            if (
                route["framework"] == "chi"
                and not route.get("_middleware_covered")
                and route.get("_go_function") in go_attributed_call_targets
            ):
                route["_middleware_covered"] = True
            key = (route["framework"], route["method"], route["path_template"], route["evidence"]["file"], route["evidence"]["line"])
            if key not in route_keys:
                route_keys.add(key)
                unique_routes.append(route)
        covered_routes = sum(bool(route.get("_middleware_covered")) for route in unique_routes)
        known_uncovered_routes = [
            route for route in unique_routes
            if not route.get("_middleware_covered")
        ]
        if known_uncovered_routes:
            findings.append({"code": "middleware_missing", "severity": "warning", "message": "one or more route receivers have no static attribution middleware registration", "evidence": None})
        slugs: dict[str, list[dict[str, Any]]] = {}
        for route in unique_routes:
            slugs.setdefault(route["feature_proposal"]["slug"], []).append(route)
        for slug, colliding in sorted(slugs.items()):
            if len(colliding) > 1:
                findings.append({"code": "feature_slug_collision", "severity": "warning",
                                 "message": f"multiple routes propose feature slug: {slug}",
                                 "evidence": colliding[0]["evidence"]})
        unique_mounts: list[dict[str, Any]] = []
        mount_keys: set[tuple[Any, ...]] = set()
        for mount in sorted(
            mounts,
            key=lambda item: (
                item["framework"], item["target"], str(item["prefix"]),
                item["evidence"]["file"], item["evidence"]["line"],
            ),
        ):
            key = (
                mount["framework"], mount["target"], mount["prefix"],
                mount["evidence"]["file"], mount["evidence"]["line"],
            )
            if key not in mount_keys:
                mount_keys.add(key)
                mount.pop("_target_receiver", None)
                unique_mounts.append(mount)
        for route in unique_routes:
            route.pop("_receiver", None)
            route.pop("_middleware_covered", None)
            route.pop("_go_function", None)
        service = {"service_path": service_path or ".", "frameworks": sorted(profile.frameworks_detected),
                   "ingress_state": ingress_state, "middleware_detected": middleware,
                   "routes": unique_routes, "mounts": unique_mounts, "resolver": resolver,
                   "async_hops": sorted(async_hops, key=lambda item: (item["kind"], item["evidence"]["file"], item["evidence"]["line"])),
                   "findings": sorted(findings, key=lambda item: (item["code"], str(item["evidence"]))),
                   "_routes_statically_covered": covered_routes}
        services.append(service)
        all_findings.extend({**finding, "service_path": service["service_path"]} for finding in service["findings"])
    services.sort(key=lambda item: item["service_path"])
    route_total = sum(len(service["routes"]) for service in services)
    result = {"schema_version": "1.0", "generated_at": generated_at,
              "source_revision": _source_revision(repo, services),
              "source_fingerprint": _fingerprint(repo, services),
              "discovery_projection": {"routes_discovered": route_total,
                                       "routes_statically_covered": sum(service["_routes_statically_covered"] for service in services),
                                       "routes_unknown": route_total - sum(service["_routes_statically_covered"] for service in services)},
              "services": services, "findings": sorted(all_findings, key=lambda item: (item["service_path"], item["code"], str(item["evidence"]))) }
    for service in services:
        service.pop("_routes_statically_covered", None)
    validate_map(result)
    return result


def _schema_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise DiscoveryError(f"unsupported schema reference: {reference}")
    value: Any = root
    for part in reference[2:].split("/"):
        if not isinstance(value, dict) or part not in value:
            raise DiscoveryError(f"invalid schema reference: {reference}")
        value = value[part]
    if not isinstance(value, dict):
        raise DiscoveryError(f"schema reference is not an object: {reference}")
    return value


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }.get(expected, lambda: False)()


def _validate_schema(value: Any, schema: dict[str, Any], root: dict[str, Any], location: str) -> None:
    if "$ref" in schema:
        _validate_schema(value, _schema_ref(root, schema["$ref"]), root, location)
        return
    if "oneOf" in schema:
        matches = 0
        for branch in schema["oneOf"]:
            try:
                _validate_schema(value, branch, root, location)
            except DiscoveryError:
                continue
            matches += 1
        if matches != 1:
            raise DiscoveryError(f"schema validation failed at {location}: expected exactly one matching shape")
        return
    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_matches_type(value, item) for item in allowed):
            raise DiscoveryError(f"schema validation failed at {location}: expected {allowed}")
    if "const" in schema and value != schema["const"]:
        raise DiscoveryError(f"schema validation failed at {location}: unexpected constant")
    if "enum" in schema and value not in schema["enum"]:
        raise DiscoveryError(f"schema validation failed at {location}: value is not in enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise DiscoveryError(f"schema validation failed at {location}: string is too short")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            raise DiscoveryError(f"schema validation failed at {location}: string does not match pattern")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        raise DiscoveryError(f"schema validation failed at {location}: number is below minimum")
    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise DiscoveryError(f"schema validation failed at {location}: missing {', '.join(missing)}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise DiscoveryError(f"schema validation failed at {location}: unexpected {', '.join(extras)}")
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], root, f"{location}.{key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _validate_schema(item, schema["items"], root, f"{location}[{index}]")


def validate_map(document: dict[str, Any]) -> None:
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DiscoveryError(f"unable to load attribution map JSON schema: {error}") from error
    if not isinstance(schema, dict):
        raise DiscoveryError("attribution map JSON schema must be an object")
    _validate_schema(document, schema, schema, "$")


def dump_document(document: dict[str, Any]) -> str:
    """JSON is valid YAML 1.2, avoiding a required PyYAML runtime dependency."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def load_document(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as error:
            raise DiscoveryError("PyYAML is required to read non-JSON YAML") from error
        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise DiscoveryError("map must be a mapping")
        return loaded


def _contained_atomic_write(path: Path, content: str, containment_root: Path) -> None:
    root = containment_root.resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise DiscoveryError("default output must remain inside the repository") from error
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise DiscoveryError("default output path is invalid")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(root, directory_flags)
    temporary_name: str | None = None
    try:
        for component in relative.parent.parts:
            try:
                child_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            except FileNotFoundError:
                os.mkdir(component, mode=0o755, dir_fd=directory_fd)
                child_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child_fd

        for attempt in range(100):
            candidate = f".{relative.name}.tmp-{os.getpid()}-{attempt}"
            try:
                temporary_fd = os.open(candidate, file_flags, 0o666, dir_fd=directory_fd)
            except FileExistsError:
                continue
            temporary_name = candidate
            break
        else:
            raise OSError("unable to allocate atomic output temporary file")

        try:
            payload = content.encode("utf-8")
            offset = 0
            while offset < len(payload):
                offset += os.write(temporary_fd, payload[offset:])
            os.fsync(temporary_fd)
        finally:
            os.close(temporary_fd)
        os.replace(
            temporary_name,
            relative.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def atomic_write(path: Path, content: str, containment_root: Path | None = None) -> None:
    if containment_root is not None:
        _contained_atomic_write(path, content, containment_root)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
