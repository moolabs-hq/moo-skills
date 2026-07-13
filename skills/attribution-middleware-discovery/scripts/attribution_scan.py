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
SCANNER_VERSION = "1.0.0"


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


def _call_argument(
    node: ast.Call,
    position: int,
    keyword: str,
) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    return next(
        (item.value for item in node.keywords if item.arg == keyword),
        None,
    )


def _python_route_methods(
    node: ast.Call,
    fixed_method: str | None = None,
) -> list[str | None]:
    if fixed_method is not None:
        return [fixed_method]
    methods_node = next(
        (keyword.value for keyword in node.keywords if keyword.arg == "methods"),
        None,
    )
    if not isinstance(methods_node, (ast.List, ast.Tuple, ast.Set)):
        return [None]
    methods: list[str | None] = []
    for item in methods_node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            methods.append(None)
            continue
        method = item.value.upper()
        methods.append(method if method in METHODS else None)
    return list(dict.fromkeys(methods or [None]))


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
            dependency = _call_argument(child, 0, "dependency")
            if dependency is not None and _auth_name(_dotted_name(dependency)):
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


def _python_scope_context(
    tree: ast.AST,
    module: str,
    is_package: bool,
) -> dict[str, Any]:
    module_scope = f"{module or '<root>'}:<module>"
    node_scopes: dict[int, str] = {}
    scope_parents: dict[str, str | None] = {module_scope: None}
    bindings: dict[str, dict[str, list[str | None]]] = {module_scope: {}}
    receivers: set[str] = set()
    local_prefixes: dict[str, str | None] = {}

    def bind(scope: str, name: str, value: str | None) -> None:
        bindings.setdefault(scope, {}).setdefault(name, []).append(value)

    def target_names(target: ast.AST) -> list[str]:
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, ast.Starred):
            return target_names(target.value)
        if isinstance(target, (ast.Tuple, ast.List)):
            return [name for item in target.elts for name in target_names(item)]
        return []

    def receiver_identity(scope: str, name: str) -> str:
        if scope == module_scope:
            return ".".join(part for part in (module, name) if part)
        return f"{scope}.<receiver>.{name}"

    def import_base(node: ast.ImportFrom) -> str:
        base = node.module or ""
        if not node.level:
            return base
        package = module.split(".") if is_package else module.split(".")[:-1]
        keep = max(0, len(package) - node.level + 1)
        return ".".join(package[:keep] + ([base] if base else []))

    class ScopeVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope = module_scope

        def generic_visit(self, node: ast.AST) -> None:
            node_scopes[id(node)] = self.scope
            super().generic_visit(node)

        def _visit_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            node_scopes[id(node)] = self.scope
            bind(self.scope, node.name, None)
            for decorator in node.decorator_list:
                self.visit(decorator)
            for default in (*node.args.defaults, *node.args.kw_defaults):
                if default is not None:
                    self.visit(default)
            if node.returns is not None:
                self.visit(node.returns)
            inner = f"{self.scope}.<function:{node.name}@{node.lineno}>"
            scope_parents[inner] = self.scope
            bindings.setdefault(inner, {})
            previous = self.scope
            self.scope = inner
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ):
                node_scopes[id(argument)] = inner
                bind(inner, argument.arg, None)
            if node.args.vararg is not None:
                bind(inner, node.args.vararg.arg, None)
            if node.args.kwarg is not None:
                bind(inner, node.args.kwarg.arg, None)
            for statement in node.body:
                self.visit(statement)
            self.scope = previous

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_function(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_function(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            node_scopes[id(node)] = self.scope
            bind(self.scope, node.name, None)
            for decorator in node.decorator_list:
                self.visit(decorator)
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword)
            inner = f"{self.scope}.<class:{node.name}@{node.lineno}>"
            scope_parents[inner] = self.scope
            bindings.setdefault(inner, {})
            previous = self.scope
            self.scope = inner
            for statement in node.body:
                self.visit(statement)
            self.scope = previous

        def visit_Import(self, node: ast.Import) -> None:
            node_scopes[id(node)] = self.scope
            for imported in node.names:
                local = imported.asname or imported.name.split(".", 1)[0]
                value = imported.name if imported.asname else local
                bind(self.scope, local, value)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            node_scopes[id(node)] = self.scope
            base = import_base(node)
            for imported in node.names:
                if imported.name == "*":
                    continue
                value = ".".join(part for part in (base, imported.name) if part)
                bind(self.scope, imported.asname or imported.name, value)

        def _visit_assignment(
            self,
            node: ast.Assign | ast.AnnAssign | ast.AugAssign,
        ) -> None:
            node_scopes[id(node)] = self.scope
            value = node.value
            if value is not None:
                self.visit(value)
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            constructor = (
                _call_name(value.func).rsplit(".", 1)[-1]
                if isinstance(value, ast.Call)
                else ""
            )
            prefix_node = (
                next(
                    (keyword.value for keyword in value.keywords if keyword.arg == "prefix"),
                    None,
                )
                if isinstance(value, ast.Call)
                else None
            )
            for target in targets:
                self.visit(target)
                for name in target_names(target):
                    identity = None
                    if constructor in {"FastAPI", "APIRouter", "Starlette"}:
                        identity = receiver_identity(self.scope, name)
                        receivers.add(identity)
                        local_prefixes[identity] = (
                            _path_value(_literal_source(prefix_node))[0]
                            if prefix_node is not None
                            else ""
                        )
                    bind(self.scope, name, identity)

        def visit_Assign(self, node: ast.Assign) -> None:
            self._visit_assignment(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            self._visit_assignment(node)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:
            self._visit_assignment(node)

    ScopeVisitor().visit(tree)

    return {
        "module_scope": module_scope,
        "node_scopes": node_scopes,
        "scope_parents": scope_parents,
        "bindings": bindings,
        "receivers": receivers,
        "prefixes": local_prefixes,
    }


def _python_scoped_binding(
    context: dict[str, Any],
    name: str,
    node: ast.AST | None,
) -> tuple[bool, str | None]:
    head, separator, tail = name.partition(".")
    scope = context["node_scopes"].get(id(node), context["module_scope"])
    while scope is not None:
        if head in context["bindings"].get(scope, {}):
            values = set(context["bindings"][scope][head])
            if len(values) != 1 or None in values:
                return True, None
            expanded = next(iter(values))
            return True, f"{expanded}.{tail}" if separator else expanded
        scope = context["scope_parents"].get(scope)
    return False, None


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
        scopes = _python_scope_context(tree, module, is_package)
        receivers.update(scopes["receivers"])
        local_prefixes.update(scopes["prefixes"])
        file_contexts[path] = {
            "tree": tree,
            "dead_nodes": dead_nodes,
            "module": module,
            "aliases": _python_import_aliases(tree, module, is_package),
            **scopes,
        }

    def canonical(path: Path, name: str, node: ast.AST | None = None) -> str:
        context = file_contexts.get(path)
        if context is None or not name:
            return name
        found, expanded = _python_scoped_binding(context, name, node)
        if found:
            return expanded or f"<unresolved:{context['module']}:{name}>"
        return name

    static_values: dict[
        tuple[Path, str, str],
        list[tuple[tuple[int, int], ast.AST | None]],
    ] = {}
    for path, context in file_contexts.items():
        for node in ast.walk(context["tree"]):
            if id(node) in context["dead_nodes"] or not isinstance(
                node,
                (ast.Assign, ast.AnnAssign),
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                scope = context["node_scopes"].get(
                    id(target),
                    context["module_scope"],
                )
                static_values.setdefault((path, scope, target.id), []).append(
                    ((node.lineno, node.col_offset), node.value)
                )

    def static_value(
        path: Path,
        name: str,
        use_node: ast.AST,
    ) -> tuple[tuple[Path, str, str, tuple[int, int]], ast.AST | None] | None:
        context = file_contexts[path]
        scope = context["node_scopes"].get(id(use_node), context["module_scope"])
        use_position = (
            getattr(use_node, "lineno", sys.maxsize),
            getattr(use_node, "col_offset", sys.maxsize),
        )
        while scope is not None:
            candidates = [
                (position, value)
                for position, value in static_values.get((path, scope, name), [])
                if position < use_position
            ]
            if candidates:
                position, value = max(candidates, key=lambda item: item[0])
                return (path, scope, name, position), value
            if name in context["bindings"].get(scope, {}):
                return None
            scope = context["scope_parents"].get(scope)
        return None

    def static_starlette_routes(
        path: Path,
        node: ast.AST,
        seen: frozenset[tuple[Path, str, str, tuple[int, int]]] = frozenset(),
    ) -> tuple[list[ast.Call], bool]:
        if isinstance(node, ast.Constant) and node.value is None:
            return [], False
        if isinstance(node, ast.Name):
            binding = static_value(path, node.id, node)
            if binding is None or binding[0] in seen or binding[1] is None:
                return [], True
            return static_starlette_routes(
                path,
                binding[1],
                seen | {binding[0]},
            )
        if isinstance(node, ast.Call):
            constructor = canonical(path, _dotted_name(node.func), node)
            return ([node], False) if constructor == "starlette.routing.Route" else ([], True)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            routes: list[ast.Call] = []
            unresolved = False
            for item in node.elts:
                value = item.value if isinstance(item, ast.Starred) else item
                item_routes, item_unresolved = static_starlette_routes(
                    path,
                    value,
                    seen,
                )
                routes.extend(item_routes)
                unresolved = unresolved or item_unresolved
            return routes, unresolved
        return [], True

    starlette_route_receivers: dict[int, set[str]] = {}
    starlette_unknown_routes: dict[Path, list[tuple[str, ast.AST]]] = {}
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
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                constructor = (
                    _call_name(value.func).rsplit(".", 1)[-1]
                    if isinstance(value, ast.Call)
                    else ""
                )
                if constructor == "Starlette":
                    routes_node = _call_argument(value, 1, "routes")
                    if routes_node is not None:
                        route_calls, unresolved = static_starlette_routes(
                            path,
                            routes_node,
                        )
                        for target in targets:
                            if not isinstance(target, ast.Name):
                                continue
                            receiver = canonical(path, target.id, target)
                            for route_call in route_calls:
                                starlette_route_receivers.setdefault(
                                    id(route_call),
                                    set(),
                                ).add(receiver)
                            if unresolved:
                                starlette_unknown_routes.setdefault(path, []).append(
                                    (receiver, routes_node)
                                )
                if constructor in {"FastAPI", "APIRouter"}:
                    for target in targets:
                        if not isinstance(target, ast.Name) or not _has_auth_dependency(value):
                            continue
                        receiver = canonical(path, target.id, target)
                        if constructor == "FastAPI":
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
                        receiver = canonical(
                            path,
                            _dotted_name(decorator.func.value),
                            decorator,
                        )
                        if receiver in receivers:
                            attribution_receivers.add(receiver)
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            parent = canonical(path, _dotted_name(node.func.value), node)
            if parent not in receivers:
                continue
            if node.func.attr == "add_middleware" and node.args:
                middleware_name = _dotted_name(node.args[0])
                if _auth_name(middleware_name):
                    global_auth_roots.add(parent)
                if _attribution_name(middleware_name):
                    attribution_receivers.add(parent)
            elif node.func.attr in {"include_router", "mount"}:
                if node.func.attr == "include_router":
                    child_node = _call_argument(node, 0, "router")
                    prefix_node = _call_argument(node, 1, "prefix")
                else:
                    child_node = _call_argument(node, 1, "app")
                    prefix_node = _call_argument(node, 0, "path")
                if child_node is None:
                    continue
                child = canonical(path, _dotted_name(child_node), child_node)
                if child not in receivers:
                    continue
                mount_prefix = _path_value(_literal_source(prefix_node))[0] if prefix_node else ""
                parents.setdefault(child, []).append((parent, mount_prefix))
                if node.func.attr == "include_router":
                    mounted_auth.setdefault(child, []).append(
                        _has_auth_dependency(node)
                    )

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
                parts = [
                    part.strip("/")
                    for part in (parent_prefix, mount_prefix, local_prefix)
                    if part.strip("/")
                ]
                values.add(f"/{'/'.join(parts)}" if parts else "")
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
        "starlette_route_receivers": starlette_route_receivers,
        "starlette_unknown_routes": starlette_unknown_routes,
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
    context = service_context["files"].get(path) if service_context else None
    if context is not None:
        tree = context["tree"]
        dead_nodes = context["dead_nodes"]
    else:
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
        (lambda value, node=None: service_context["canonical"](path, value, node))
        if service_context is not None
        else (lambda value, node=None: value)
    )
    prefixes: dict[str, str | None] = dict(service_context["prefixes"]) if service_context else {"app": ""}
    receivers: set[str] = set(service_context["receivers"]) if service_context else set(prefixes)
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
            if isinstance(value, ast.Call) and _call_name(value.func).rsplit(".", 1)[-1] in {"FastAPI", "APIRouter", "Starlette"}:
                for target in targets:
                    if not isinstance(target, ast.Name):
                        continue
                    prefix_node = next((keyword.value for keyword in value.keywords if keyword.arg == "prefix"), None)
                    receiver_name = canonical(target.id, target)
                    if service_context is None:
                        prefixes[receiver_name] = _path_value(_literal_source(prefix_node))[0] if prefix_node else ""
                    if _has_auth_dependency(value):
                        if _call_name(value.func).rsplit(".", 1)[-1] == "FastAPI":
                            global_auth.add(receiver_name)
                        else:
                            authenticated_routers.add(receiver_name)
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        receiver = canonical(_dotted_name(node.func.value), node)
        if receiver not in receivers:
            continue
        if node.func.attr == "add_middleware" and node.args:
            middleware_name = _dotted_name(node.args[0])
            if _auth_name(middleware_name):
                global_auth.add(receiver)
            if _attribution_name(middleware_name):
                attribution_receivers.add(receiver)
        if node.func.attr in {"include_router", "mount"}:
            if node.func.attr == "include_router":
                target_node = _call_argument(node, 0, "router")
                prefix_node = _call_argument(node, 1, "prefix")
            else:
                target_node = _call_argument(node, 1, "app")
                prefix_node = _call_argument(node, 0, "path")
            target = _dotted_name(target_node) or "<unknown>"
            router_name = canonical(target, target_node)
            raw_prefix = _literal_source(prefix_node) if prefix_node else None
            resolved = router_name in prefixes
            mount_path = _path_value(raw_prefix)[0] if raw_prefix is not None else ""
            child = prefixes.get(router_name)
            if resolved and service_context is None:
                prefixes[router_name] = None if mount_path is None or child is None else mount_path.rstrip("/") + "/" + child.lstrip("/")
                if node.func.attr == "include_router" and _has_auth_dependency(node):
                    authenticated_routers.add(router_name)
            mount = _mount(
                "fastapi" if node.func.attr == "include_router" else framework,
                target,
                raw_prefix,
                _location(repo, path, node.lineno),
                resolved,
            )
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
                receiver = canonical(_dotted_name(decorator.func.value), decorator)
                if receiver in receivers:
                    attribution_receivers.add(receiver)

    for node in ast.walk(tree):
        if id(node) in dead_nodes:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                receiver = canonical(_dotted_name(decorator.func.value), decorator)
                decorator_name = decorator.func.attr
                if decorator.func.attr == "middleware" and _attribution_name(node.name):
                    if receiver in receivers:
                        attribution_receivers.add(receiver)
                    continue
                path_node = _call_argument(decorator, 0, "path")
                if (
                    decorator_name.upper() not in METHODS
                    and decorator_name != "api_route"
                ) or path_node is None:
                    continue
                if receiver not in receivers:
                    continue
                scope = (
                    "handler" if _has_auth_dependency(node) or _has_auth_dependency(decorator)
                    else "router" if receiver in authenticated_routers
                    else "global" if receiver in global_auth
                    else "unknown"
                )
                methods = _python_route_methods(
                    decorator,
                    decorator_name.upper()
                    if decorator_name.upper() in METHODS
                    else None,
                )
                for method in methods:
                    routes.append(_route(
                        service_path, framework, method,
                        _prefixed_raw_path(prefixes.get(receiver, ""), _literal_source(path_node)),
                        _location(repo, path, decorator.lineno), scope, receiver,
                        receiver in attribution_covered,
                    ))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_api_route":
            receiver = canonical(_dotted_name(node.func.value), node)
            if receiver not in receivers:
                continue
            path_node = _call_argument(node, 0, "path")
            endpoint_node = _call_argument(node, 1, "endpoint")
            if path_node is None:
                continue
            handler_name = _dotted_name(endpoint_node).rsplit(".", 1)[-1]
            handler = function_defs.get(handler_name)
            for method in _python_route_methods(node):
                routes.append(_route(
                    service_path, framework, method,
                    _prefixed_raw_path(prefixes.get(receiver, ""), _literal_source(path_node)),
                    _location(repo, path, node.lineno),
                    "handler" if _has_auth_dependency(node) or (handler is not None and _has_auth_dependency(handler))
                    else "router" if receiver in authenticated_routers
                    else "global" if receiver in global_auth
                    else "unknown",
                    receiver,
                    receiver in attribution_covered,
                ))
        if isinstance(node, ast.Call) and context is not None:
            imported, constructor = _python_scoped_binding(
                context,
                _dotted_name(node.func),
                node,
            )
            if not imported or constructor != "starlette.routing.Route":
                continue
            path_node = _call_argument(node, 0, "path")
            raw_path = _literal_source(path_node) if path_node is not None else "<dynamic>"
            route_receivers: list[str | None] = sorted(
                service_context["starlette_route_receivers"].get(id(node), set())
            ) or [None]
            for receiver in route_receivers:
                auth_scope = (
                    "global"
                    if receiver is not None and receiver in global_auth
                    else "unknown"
                )
                prefixed_path = (
                    _prefixed_raw_path(prefixes.get(receiver, ""), raw_path)
                    if receiver is not None
                    else raw_path
                )
                for method in _python_route_methods(node):
                    routes.append(_route(
                        service_path,
                        "starlette",
                        method,
                        prefixed_path,
                        _location(repo, path, node.lineno),
                        auth_scope,
                        receiver,
                        receiver is not None and receiver in attribution_covered,
                    ))
    if service_context is not None:
        for receiver, routes_node in service_context[
            "starlette_unknown_routes"
        ].get(path, []):
            routes.append(_route(
                service_path,
                "starlette",
                None,
                _prefixed_raw_path(prefixes.get(receiver, ""), "<dynamic>"),
                _location(repo, path, routes_node.lineno),
                "global" if receiver in global_auth else "unknown",
                receiver,
                receiver in attribution_covered,
            ))
    return routes, bool(attribution_receivers), mounts


JS_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs")
JS_RECEIVER_DECLARATION_PATTERN = re.compile(
    r"(?:\b(?:const|let|var)\s+)?\b(\w+)\s*=\s*"
    r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()"
)
JS_EXPORTED_RECEIVER_PATTERN = re.compile(
    r"\bexport\s+(?:const|let|var)\s+(\w+)\s*=\s*"
    r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()"
)
JS_SCOPED_RECEIVER_PATTERN = re.compile(
    r"\b(?:(const|let|var)\s+)?([A-Za-z_$]\w*)\s*=\s*"
    r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()"
)


def _js_receiver_framework(constructor: str) -> str:
    return "hono" if "Hono" in constructor else "express"


def _js_middleware_enabled(
    metadata: dict[str, Any],
    state_key: str,
    position: int,
) -> bool:
    return bool(metadata.get(state_key)) or any(
        registration <= position
        for registration in metadata.get(f"{state_key}_positions", [])
    )


def _js_receiver_snapshot(
    metadata: dict[str, Any],
    position: int,
) -> dict[str, Any]:
    return {
        "framework": metadata["framework"],
        "attribution": _js_middleware_enabled(metadata, "attribution", position),
        "auth": _js_middleware_enabled(metadata, "auth", position),
    }


def _js_brace_ranges(code: str) -> dict[int, tuple[int, int]]:
    ranges: dict[int, tuple[int, int]] = {}
    stack: list[int] = []
    quote: str | None = None
    escaped = False
    for position, character in enumerate(code):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {"'", '"', "`"}:
            quote = character
        elif character == "{":
            stack.append(position)
        elif character == "}" and stack:
            start = stack.pop()
            ranges[start] = (start, position + 1)
    return ranges


def _js_receiver_state(
    code: str,
    imported_receivers: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, dict[str, Any]], Any, set[str]]:
    brace_ranges = _js_brace_ranges(code)
    lexical_scopes = list(brace_ranges.values())
    module_scope = (0, len(code) + 1)
    function_scopes: list[tuple[int, int]] = []
    function_matches: list[tuple[re.Match[str], tuple[int, int], str]] = []
    function_patterns = (
        re.compile(r"\bfunction(?:\s+[A-Za-z_$]\w*)?\s*\(([^)]*)\)\s*\{"),
        re.compile(r"(?:\(([^)]*)\)|([A-Za-z_$]\w*))\s*=>\s*\{"),
    )
    for pattern in function_patterns:
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            scope = brace_ranges.get(match.end() - 1)
            if scope is None:
                continue
            parameters = next(
                (group for group in match.groups() if group is not None),
                "",
            )
            function_scopes.append(scope)
            function_matches.append((match, scope, parameters))

    def containing_scope(
        position: int,
        scopes: list[tuple[int, int]],
    ) -> tuple[int, int]:
        containing = [scope for scope in scopes if scope[0] < position < scope[1]]
        return min(containing, key=lambda scope: scope[1] - scope[0]) if containing else module_scope

    states: dict[str, dict[str, Any]] = {}
    bindings: dict[str, list[tuple[tuple[int, int], int, str | None]]] = {}
    local_receiver_ids: set[str] = set()

    def add_binding(
        name: str,
        scope: tuple[int, int],
        position: int,
        identity: str | None,
    ) -> None:
        bindings.setdefault(name, []).append((scope, position, identity))

    for name, metadata in (imported_receivers or {}).items():
        states[name] = {
            **metadata,
            "attribution_positions": [],
            "auth_positions": [],
        }
        add_binding(name, module_scope, -1, name)

    receiver_declarations: set[int] = set()
    for match in JS_SCOPED_RECEIVER_PATTERN.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        kind, name, constructor = match.groups()
        scopes = function_scopes if kind == "var" else lexical_scopes
        scope = containing_scope(match.start(), scopes)
        identity = name if scope == module_scope else f"{name}@{match.start()}"
        states[identity] = {
            "framework": _js_receiver_framework(constructor),
            "attribution": False,
            "auth": False,
            "attribution_positions": [],
            "auth_positions": [],
        }
        add_binding(name, scope, match.start(), identity)
        local_receiver_ids.add(identity)
        receiver_declarations.add(match.start(2))

    relevant_names = set(bindings)
    declaration_pattern = re.compile(r"\b(const|let|var)\s+([A-Za-z_$]\w*)\b")
    for match in declaration_pattern.finditer(code):
        if (
            match.start(2) in receiver_declarations
            or match.group(2) not in relevant_names
            or not _outside_js_string(code, match.start())
        ):
            continue
        scopes = function_scopes if match.group(1) == "var" else lexical_scopes
        add_binding(
            match.group(2),
            containing_scope(match.start(), scopes),
            match.start(),
            None,
        )

    for match, scope, parameters in function_matches:
        for argument in _split_call_arguments(f"({parameters})"):
            binding = argument.split("=", 1)[0].strip()
            if binding in relevant_names:
                add_binding(binding, scope, match.start(), None)

    def resolve(name: str, position: int) -> tuple[str, dict[str, Any]] | None:
        candidate_scopes = sorted(
            {
                scope
                for scope, _, _ in bindings.get(name, [])
                if scope[0] <= position < scope[1]
            },
            key=lambda scope: scope[1] - scope[0],
        )
        for scope in candidate_scopes:
            scoped = [
                (binding_position, identity)
                for binding_scope, binding_position, identity in bindings[name]
                if binding_scope == scope
            ]
            preceding = [item for item in scoped if item[0] <= position]
            if not preceding:
                return None
            _, identity = max(preceding, key=lambda item: item[0])
            if identity is None:
                return None
            return identity, states[identity]
        return None

    middleware_patterns = (
        (
            re.compile(
                r"\b([A-Za-z_$]\w*)\.use\s*\([^\)]*?"
                r"(?:attribution\w*middleware|moolabs\w*middleware|middleware\w*attribution)",
                re.I | re.S,
            ),
            "attribution",
        ),
        (
            re.compile(
                r"\b([A-Za-z_$]\w*)\.use\s*\([^\)]*?"
                r"(?:require[_-]?auth|authenticate\w*|auth(?:entication)?middleware|verify(?:jwt|token))",
                re.I | re.S,
            ),
            "auth",
        ),
    )
    for pattern, state_key in middleware_patterns:
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            resolved = resolve(match.group(1), match.start())
            if resolved is not None:
                resolved[1][f"{state_key}_positions"].append(match.start())

    return states, resolve, local_receiver_ids


def _js_import_shadow_ranges(
    code: str,
    imported_names: set[str],
) -> dict[str, list[tuple[int, int]]]:
    shadows = {name: [] for name in imported_names}
    if not imported_names:
        return shadows
    brace_ranges = _js_brace_ranges(code)
    lexical_ranges = list(brace_ranges.values())
    function_ranges: list[tuple[int, int]] = []

    function_patterns = (
        re.compile(r"\bfunction(?:\s+[A-Za-z_$]\w*)?\s*\(([^)]*)\)\s*\{"),
        re.compile(r"(?:\(([^)]*)\)|([A-Za-z_$]\w*))\s*=>\s*\{"),
    )
    for pattern in function_patterns:
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            scope = brace_ranges.get(match.end() - 1)
            if scope is None:
                continue
            function_ranges.append(scope)
            parameters = next(
                (group for group in match.groups() if group is not None),
                "",
            )
            binding_sides = [
                argument.split("=", 1)[0]
                for argument in _split_call_arguments(f"({parameters})")
            ]
            for name in imported_names:
                if any(re.search(rf"\b{re.escape(name)}\b", side) for side in binding_sides):
                    shadows[name].append(scope)

    def containing_range(
        position: int,
        ranges: list[tuple[int, int]],
    ) -> tuple[int, int] | None:
        containing = [scope for scope in ranges if scope[0] < position < scope[1]]
        return min(containing, key=lambda scope: scope[1] - scope[0]) if containing else None

    for name in imported_names:
        declaration = re.compile(
            rf"\b(const|let|var)\s+(?:{re.escape(name)}\b|"
            rf"[{{\[][^;\n=]*\b{re.escape(name)}\b)"
        )
        for match in declaration.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            ranges = function_ranges if match.group(1) == "var" else lexical_ranges
            scope = containing_range(match.start(), ranges)
            if scope is not None:
                shadows[name].append(scope)
    return shadows


def _js_imported_route_receivers(
    files: list[Path],
) -> dict[Path, dict[str, dict[str, Any]]]:
    source_files = [path for path in files if path.suffix.lower() in JS_SOURCE_SUFFIXES]
    source_paths = {path.resolve() for path in source_files}
    exported: dict[Path, dict[str, dict[str, Any]]] = {}
    for path in source_files:
        code = _without_js_comments(path.read_text(encoding="utf-8", errors="replace"))
        _, resolve, _ = _js_receiver_state(code)
        for match in JS_EXPORTED_RECEIVER_PATTERN.finditer(code):
            if _outside_js_string(code, match.start()):
                receiver = resolve(match.group(1), match.end())
                if receiver is not None:
                    exported.setdefault(path.resolve(), {})[match.group(1)] = (
                        _js_receiver_snapshot(receiver[1], len(code))
                    )
        export_clause_pattern = re.compile(r"\bexport\s*\{([^}]+)\}\s*;?", re.S)
        for match in export_clause_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            for binding in match.group(1).split(","):
                parts = re.split(r"\s+as\s+", binding.strip(), maxsplit=1)
                local_name = parts[0].removeprefix("type ").strip()
                exported_name = parts[-1].strip()
                receiver = resolve(local_name, len(code))
                if receiver is not None:
                    exported.setdefault(path.resolve(), {})[exported_name] = (
                        _js_receiver_snapshot(receiver[1], len(code))
                    )
        default_constructor = re.search(
            r"\bexport\s+default\s+"
            r"(express\s*\(|express\.Router\s*\(|Router\s*\(|new\s+Hono\s*\()",
            code,
        )
        if default_constructor and _outside_js_string(code, default_constructor.start()):
            exported.setdefault(path.resolve(), {})["default"] = {
                "framework": _js_receiver_framework(default_constructor.group(1)),
                "attribution": False,
                "auth": False,
            }
        default_name = re.search(
            r"\bexport\s+default\s+([A-Za-z_$]\w*)\s*;?",
            code,
        )
        if default_name and _outside_js_string(code, default_name.start()):
            receiver = resolve(default_name.group(1), default_name.start())
            if receiver is not None:
                exported.setdefault(path.resolve(), {})["default"] = (
                    _js_receiver_snapshot(receiver[1], len(code))
                )

    imported: dict[Path, dict[str, dict[str, Any]]] = {}
    named_import_pattern = re.compile(
        r"\bimport\s*\{([^}]+)\}\s*from\s*['\"](\.[^'\"]+)['\"]",
        re.S,
    )
    default_import_pattern = re.compile(
        r"\bimport\s+([A-Za-z_$]\w*)\s+from\s*['\"](\.[^'\"]+)['\"]"
    )
    combined_import_pattern = re.compile(
        r"\bimport\s+([A-Za-z_$]\w*)\s*,\s*\{([^}]+)\}\s*"
        r"from\s*['\"](\.[^'\"]+)['\"]",
        re.S,
    )

    def source_exports(
        path: Path,
        specifier: str,
    ) -> dict[str, dict[str, Any]] | None:
        module = (path.parent / specifier).resolve()
        candidates = [module]
        if module.suffix.lower() not in JS_SOURCE_SUFFIXES:
            candidates.extend(Path(f"{module}{suffix}") for suffix in JS_SOURCE_SUFFIXES)
            candidates.extend(module / f"index{suffix}" for suffix in JS_SOURCE_SUFFIXES)
        existing = [candidate for candidate in candidates if candidate in source_paths]
        return exported.get(existing[0]) if len(existing) == 1 else None

    def bind_named_imports(
        path: Path,
        bindings: str,
        available_exports: dict[str, dict[str, Any]],
    ) -> None:
        for binding in bindings.split(","):
            parts = re.split(r"\s+as\s+", binding.strip(), maxsplit=1)
            exported_name = parts[0].removeprefix("type ").strip()
            local_name = parts[-1].strip()
            metadata = available_exports.get(exported_name)
            if metadata is not None:
                imported.setdefault(path, {})[local_name] = dict(metadata)

    for path in source_files:
        code = _without_js_comments(path.read_text(encoding="utf-8", errors="replace"))
        for match in combined_import_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            available_exports = source_exports(path, match.group(3))
            if available_exports is None:
                continue
            default_metadata = available_exports.get("default")
            if default_metadata is not None:
                imported.setdefault(path, {})[match.group(1)] = dict(default_metadata)
            bind_named_imports(path, match.group(2), available_exports)
        for match in named_import_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            available_exports = source_exports(path, match.group(2))
            if available_exports is None:
                continue
            bind_named_imports(path, match.group(1), available_exports)
        for match in default_import_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            available_exports = source_exports(path, match.group(2))
            metadata = available_exports.get("default") if available_exports else None
            if metadata is not None:
                imported.setdefault(path, {})[match.group(1)] = dict(metadata)
    return imported


def _next_route_segment(segment: str) -> str | None:
    if re.fullmatch(r"\([^)]*\)", segment):
        return None
    optional_catchall = re.fullmatch(r"\[\[\.\.\.([^\]]+)\]\]", segment)
    if optional_catchall:
        return f"{{...{optional_catchall.group(1)}?}}"
    catchall = re.fullmatch(r"\[\.\.\.([^\]]+)\]", segment)
    if catchall:
        return f"{{...{catchall.group(1)}}}"
    dynamic = re.fullmatch(r"\[([^\]]+)\]", segment)
    if dynamic:
        return f"{{{dynamic.group(1)}}}"
    return segment


def _scan_js(
    repo: Path,
    service_path: str,
    path: Path,
    text: str,
    imported_receivers: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    code = _without_js_comments(text)
    states, resolve_receiver, local_receiver_ids = _js_receiver_state(
        code,
        imported_receivers,
    )
    parents: dict[str, list[tuple[str, str | None]]] = {}
    mount_pattern = re.compile(
        r"\b(\w+)\.(use|route)\s*\(\s*([^,\)]+)\s*,\s*(\w+)\s*\)",
        re.S,
    )
    for match in mount_pattern.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        parent, mount_kind, mount, child = match.groups()
        parent_receiver = resolve_receiver(parent, match.start())
        child_receiver = resolve_receiver(child, match.start())
        if parent_receiver is None and child_receiver is None:
            continue
        resolved = (
            child_receiver is not None
            and child_receiver[0] in local_receiver_ids
        )
        if resolved:
            parents.setdefault(child_receiver[0], []).append(
                (
                    parent_receiver[0] if parent_receiver is not None else parent,
                    _path_value(mount)[0],
                )
            )
        line = code.count("\n", 0, match.start()) + 1
        mounted = _mount(
            "hono" if mount_kind == "route" else "express",
            child,
            mount,
            _location(repo, path, line),
            resolved,
        )
        mounted["_target_receiver"] = (
            child_receiver[0] if child_receiver is not None else child
        )
        mounts.append(mounted)

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
    for match in pattern.finditer(code):
        if not _outside_js_string(code, match.start()):
            continue
        resolved = resolve_receiver(match.group(1), match.start())
        if resolved is None:
            continue
        receiver, metadata = resolved
        line = code.count("\n", 0, match.start()) + 1
        arguments = code[match.end():code.find(")", match.end())]
        handler_auth = bool(re.search(r"\b(?:require[_-]?auth|authenticate\w*|verify(?:jwt|token)|withAuth)\b", arguments, re.I))
        scope = (
            "handler"
            if handler_auth
            else "global"
            if _js_middleware_enabled(metadata, "auth", match.start())
            else "unknown"
        )
        routes.append(_route(
            service_path, metadata["framework"],
            match.group(2).upper(),
            _prefixed_raw_path(effective_prefix(receiver), match.group(3).strip()),
            _location(repo, path, line), scope, receiver,
            _js_middleware_enabled(metadata, "attribution", match.start()),
        ))
    if re.search(r"(?:^|/)app/(?:.+/)?route\.(?:ts|tsx|js|jsx)$", path.relative_to(repo).as_posix()):
        route_parts = list(path.relative_to(repo).parts)
        app_index = route_parts.index("app")
        segments = route_parts[app_index + 1:-1]
        rendered = [_next_route_segment(segment) for segment in segments]
        template = "/" + "/".join(segment for segment in rendered if segment)
        method_exports: list[tuple[str, int, str]] = []
        direct_export_pattern = re.compile(
            r"\bexport\s+(?:(?:async\s+)?function\s+|const\s+)"
            r"(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
        )
        for match in direct_export_pattern.finditer(code):
            if _outside_js_string(code, match.start()):
                method_exports.append((match.group(1), match.start(), match.group(0)))
        export_clause_pattern = re.compile(r"\bexport\s*\{([^}]+)\}", re.S)
        for match in export_clause_pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            for binding in match.group(1).split(","):
                parts = re.split(r"\s+as\s+", binding.strip(), maxsplit=1)
                exported_name = parts[-1].strip()
                if exported_name in METHODS:
                    method_exports.append((exported_name, match.start(), binding))
        seen_exports: set[tuple[str, int]] = set()
        for method, position, evidence_text in method_exports:
            number = code.count("\n", 0, position) + 1
            if (method, number) in seen_exports:
                continue
            seen_exports.add((method, number))
            scope = (
                "handler"
                if re.search(
                    r"\b(?:withAuth|requireAuth|authenticate)\b",
                    evidence_text,
                    re.I,
                )
                else "unknown"
            )
            routes.append(_route(
                service_path,
                "nextjs-app-router",
                method,
                json.dumps(template),
                _location(repo, path, number),
                scope,
            ))
    return (
        routes,
        any(
            _js_middleware_enabled(metadata, "attribution", len(code))
            for metadata in states.values()
        ),
        mounts,
    )


GO_ATTRIBUTION_PATTERN = re.compile(
    r"\b(\w+)\.Use\s*\([^\)]*?(?:Attribution\w*Middleware|Moolabs\w*Middleware|Middleware\w*Attribution)",
    re.I | re.S,
)


def _split_call_arguments(call_text: str) -> list[str]:
    arguments: list[str] = []
    start = 1
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    escaped = False
    for index, character in enumerate(call_text[1:-1], 1):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif quote:
            if character == quote:
                quote = None
        elif character in {'"', "'", "`"}:
            quote = character
        elif character in depths:
            depths[character] += 1
        elif character in closing:
            depths[closing[character]] -= 1
        elif character == "," and not any(depths.values()):
            arguments.append(call_text[start:index].strip())
            start = index + 1
    final = call_text[start:-1].strip()
    if final or arguments:
        arguments.append(final)
    return arguments


def _go_import_declarations(code: str) -> list[tuple[str | None, str]]:
    declarations = [
        match.groups()
        for match in re.finditer(r"\bimport\s+(?:(\w+)\s+)?\"([^\"]+)\"", code)
    ]
    for block in re.finditer(r"\bimport\s*\((.*?)\)", code, re.S):
        declarations.extend(
            match.groups()
            for match in re.finditer(
                r"(?m)^\s*(?:(\w+|\.|_)\s+)?\"([^\"]+)\"",
                block.group(1),
            )
        )
    return declarations


def _go_chi_aliases(code: str) -> set[str]:
    return {
        alias or "chi"
        for alias, import_path in _go_import_declarations(code)
        if alias not in {".", "_"}
        and re.fullmatch(r"github\.com/go-chi/chi(?:/v\d+)?", import_path)
    }


def _go_dead_ranges(code: str) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for match in re.finditer(r"\b(?:if|for)\s+false\s*\{", code):
        if _outside_js_string(code, match.start()):
            ranges.append((match.start(), _balanced_block_end(code, match.end() - 1)))
    return ranges


def _go_router_parameters(parameters: str, chi_aliases: set[str]) -> dict[str, int]:
    router_parameters: dict[str, int] = {}
    pending: list[tuple[str, int]] = []
    qualifier = "|".join(re.escape(alias) for alias in sorted(chi_aliases))
    if not qualifier:
        return router_parameters
    for position, parameter in enumerate(_split_call_arguments(f"({parameters})")):
        parts = parameter.split()
        if len(parts) == 1 and re.fullmatch(r"[A-Za-z_]\w*", parts[0]):
            pending.append((parts[0], position))
            continue
        if len(parts) < 2:
            pending.clear()
            continue
        names = pending + [(parts[0], position)]
        pending.clear()
        if re.fullmatch(
            rf"(?:(?:{qualifier})\.Router|\*(?:{qualifier})\.Mux)",
            "".join(parts[1:]),
        ):
            router_parameters.update(names)
    return router_parameters


def _go_function_blocks(
    code: str,
    chi_aliases: set[str],
) -> list[tuple[int, int, str, dict[str, int]]]:
    blocks: list[tuple[int, int, str, dict[str, int]]] = []
    pattern = re.compile(
        r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)\s*\(([^)]*)\)[^{]*\{",
        re.S,
    )
    for match in pattern.finditer(code):
        blocks.append((
            match.start(),
            _balanced_block_end(code, match.end() - 1),
            match.group(1),
            _go_router_parameters(match.group(2), chi_aliases),
        ))
    return blocks


def _go_import_package_keys(code: str, module_path: str) -> dict[str, str]:
    imports: dict[str, str] = {}
    for alias, import_path in _go_import_declarations(code):
        if alias in {".", "_"} or not module_path or (
            import_path != module_path and not import_path.startswith(f"{module_path}/")
        ):
            continue
        package_key = import_path.removeprefix(module_path).lstrip("/") or "."
        imports[alias or import_path.rsplit("/", 1)[-1]] = package_key
    return imports


def _go_attributed_call_targets(
    path: Path,
    text: str,
    service_root: Path,
    module_path: str,
) -> set[tuple[str, str, int]]:
    code = _without_js_comments(text)
    blocks = _go_function_blocks(code, _go_chi_aliases(code))
    dead_ranges = _go_dead_ranges(code)
    package_key = path.parent.relative_to(service_root).as_posix() or "."
    import_packages = _go_import_package_keys(code, module_path)
    targets: set[tuple[str, str, int]] = set()
    for middleware in GO_ATTRIBUTION_PATTERN.finditer(code):
        if not _outside_js_string(code, middleware.start()) or any(
            start <= middleware.start() < end for start, end in dead_ranges
        ):
            continue
        containing = [
            block for block in blocks if block[0] <= middleware.start() < block[1]
        ]
        if not containing:
            continue
        block = min(containing, key=lambda item: item[1] - item[0])
        receiver = middleware.group(1)
        call_pattern = re.compile(
            r"(?<![\w.])((?:[A-Za-z_]\w*\.)?[A-Za-z_]\w*)\s*(\()"
        )
        for call in call_pattern.finditer(code, middleware.end(), block[1]):
            if not _outside_js_string(code, call.start()):
                continue
            target = call.group(1)
            if "." in target:
                alias, function_name = target.split(".", 1)
                target_package = import_packages.get(alias)
                if target_package is None:
                    continue
            else:
                target_package = package_key
                function_name = target
            arguments = _split_call_arguments(_balanced_call_text(code, call.start(2)))
            targets.update(
                (target_package, function_name, position)
                for position, argument in enumerate(arguments)
                if argument == receiver
            )
    return targets


def _go_receiver_state(
    code: str,
    chi_aliases: set[str],
) -> tuple[
    dict[str, dict[str, bool]],
    Any,
    list[tuple[int, int, str, dict[str, int]]],
    list[tuple[int, int]],
]:
    function_blocks = _go_function_blocks(code, chi_aliases)
    dead_ranges = _go_dead_ranges(code)
    brace_scopes = list(_js_brace_ranges(code).values())
    module_scope = (0, len(code) + 1)
    states: dict[str, dict[str, bool]] = {}
    bindings: dict[str, list[tuple[tuple[int, int], int, str | None]]] = {}

    def is_dead(position: int) -> bool:
        return any(start <= position < end for start, end in dead_ranges)

    def containing_scope(position: int) -> tuple[int, int]:
        containing = [
            scope for scope in brace_scopes if scope[0] < position < scope[1]
        ]
        return (
            min(containing, key=lambda scope: scope[1] - scope[0])
            if containing
            else module_scope
        )

    def add_receiver(
        name: str,
        scope: tuple[int, int],
        position: int,
        identity: str,
    ) -> None:
        states[identity] = {"attribution": False, "auth": False}
        bindings.setdefault(name, []).append((scope, position, identity))

    for start, end, function_name, parameters in function_blocks:
        for name in parameters:
            add_receiver(
                name,
                (start, end),
                start,
                f"{function_name}@{start}:parameter:{name}",
            )

    qualifier = "|".join(re.escape(alias) for alias in sorted(chi_aliases))
    receiver_declarations: set[int] = set()
    if qualifier:
        constructor_pattern = re.compile(
            rf"\b([A-Za-z_]\w*)\s*(?::=|=)\s*(?:{qualifier})\.NewRouter\s*\("
        )
        for match in constructor_pattern.finditer(code):
            if not _outside_js_string(code, match.start()) or is_dead(match.start()):
                continue
            identity = f"{match.group(1)}@{match.start()}"
            add_receiver(
                match.group(1),
                containing_scope(match.start()),
                match.start(),
                identity,
            )
            receiver_declarations.add(match.start(1))

        typed_pattern = re.compile(
            rf"\bvar\s+([A-Za-z_]\w*)\s+"
            rf"(?:(?:{qualifier})\.Router|\*(?:{qualifier})\.Mux)\b"
        )
        for match in typed_pattern.finditer(code):
            if not _outside_js_string(code, match.start()) or is_dead(match.start()):
                continue
            identity = f"{match.group(1)}@{match.start()}"
            add_receiver(
                match.group(1),
                containing_scope(match.start()),
                match.start(),
                identity,
            )
            receiver_declarations.add(match.start(1))

        inline_parameter_pattern = re.compile(
            rf"\bfunc\s*\(\s*([A-Za-z_]\w*)\s+"
            rf"(?:{qualifier})\.Router\s*\)\s*\{{"
        )
        for match in inline_parameter_pattern.finditer(code):
            if not _outside_js_string(code, match.start()) or is_dead(match.start()):
                continue
            scope = (match.start(), _balanced_block_end(code, match.end() - 1))
            add_receiver(
                match.group(1),
                scope,
                match.start(),
                f"inline@{match.start()}:parameter:{match.group(1)}",
            )

    relevant_names = set(bindings)
    for match in re.finditer(r"\b([A-Za-z_]\w*)\s*:=", code):
        if (
            match.start(1) in receiver_declarations
            or match.group(1) not in relevant_names
            or not _outside_js_string(code, match.start())
            or is_dead(match.start())
        ):
            continue
        bindings.setdefault(match.group(1), []).append(
            (containing_scope(match.start()), match.start(), None)
        )

    def resolve(name: str, position: int) -> tuple[str, dict[str, bool]] | None:
        if is_dead(position):
            return None
        candidate_scopes = sorted(
            {
                scope
                for scope, binding_position, _ in bindings.get(name, [])
                if scope[0] <= position < scope[1] and binding_position <= position
            },
            key=lambda scope: scope[1] - scope[0],
        )
        for scope in candidate_scopes:
            preceding = [
                (binding_position, identity)
                for binding_scope, binding_position, identity in bindings[name]
                if binding_scope == scope and binding_position <= position
            ]
            if not preceding:
                continue
            _, identity = max(preceding, key=lambda item: item[0])
            if identity is None:
                return None
            return identity, states[identity]
        return None

    auth_pattern = re.compile(
        r"\b(\w+)\.Use\s*\([^\)]*?"
        r"(?:Auth\w*|Authenticate\w*|Verify(?:JWT|Token))",
        re.I | re.S,
    )
    for pattern, state_key in (
        (GO_ATTRIBUTION_PATTERN, "attribution"),
        (auth_pattern, "auth"),
    ):
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()):
                continue
            receiver = resolve(match.group(1), match.start())
            if receiver is not None:
                receiver[1][state_key] = True

    return states, resolve, function_blocks, dead_ranges


def _scan_go(repo: Path, service_path: str, path: Path, text: str) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    routes: list[dict[str, Any]] = []
    mounts: list[dict[str, Any]] = []
    code = _without_js_comments(text)
    chi_aliases = _go_chi_aliases(code)
    states, resolve_receiver, function_blocks, dead_ranges = _go_receiver_state(
        code,
        chi_aliases,
    )

    def is_dead(position: int) -> bool:
        return any(start <= position < end for start, end in dead_ranges)

    framework = "chi" if chi_aliases else "net-http"
    parents: dict[str, list[tuple[str, str | None]]] = {}
    mount_pattern = re.compile(
        r"\b(\w+)\.Mount\s*\(\s*([^,\)]+)(?:\s*,\s*([\w.]+))?",
        re.S,
    )
    for mount in mount_pattern.finditer(code):
        if not _outside_js_string(code, mount.start()) or is_dead(mount.start()):
            continue
        parent_receiver = resolve_receiver(mount.group(1), mount.start())
        if parent_receiver is None:
            continue
        child = mount.group(3)
        child_receiver = resolve_receiver(child, mount.start()) if child else None
        resolved = child_receiver is not None if child else True
        if child_receiver is not None:
            parents.setdefault(child_receiver[0], []).append(
                (parent_receiver[0], _path_value(mount.group(2).strip())[0])
            )
        number = code.count("\n", 0, mount.start()) + 1
        mounted = _mount(
            framework, child or "inline-router", mount.group(2).strip(),
            _location(repo, path, number), resolved,
        )
        mounted["_target_receiver"] = (
            child_receiver[0] if child_receiver is not None else child or "inline-router"
        )
        mounts.append(mounted)

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
    qualifier = "|".join(re.escape(alias) for alias in sorted(chi_aliases))
    if qualifier:
        inline_route_pattern = re.compile(
            r"\b(\w+)\.Route\s*\(\s*([^,\)]+)\s*,\s*"
            rf"func\s*\(\s*(\w+)\s+(?:{qualifier})\.Router\s*\)\s*\{{",
            re.S,
        )
        for mount in inline_route_pattern.finditer(code):
            if not _outside_js_string(code, mount.start()) or is_dead(mount.start()):
                continue
            parent, raw_prefix, child = mount.groups()
            parent_receiver = resolve_receiver(parent, mount.start())
            child_receiver = resolve_receiver(child, mount.end())
            if parent_receiver is None or child_receiver is None:
                continue
            containing = [
                block for block in inline_blocks
                if block[0] < mount.start() < block[1]
                and block[2] == parent_receiver[0]
            ]
            parent_prefix = (
                min(containing, key=lambda block: block[1] - block[0])[3]
                if containing
                else effective_prefix(parent_receiver[0])
            )
            mount_prefix = _path_value(raw_prefix.strip())[0]
            block_prefix = (
                None
                if parent_prefix is None or mount_prefix is None
                else (
                    parent_prefix.rstrip("/") + "/" + mount_prefix.lstrip("/")
                ).rstrip("/")
                or "/"
            )
            block_end = _balanced_block_end(code, mount.end() - 1)
            inline_blocks.append(
                (mount.start(), block_end, child_receiver[0], block_prefix)
            )
            number = code.count("\n", 0, mount.start()) + 1
            mounts.append(_mount(
                framework, "inline-router", raw_prefix.strip(),
                _location(repo, path, number),
            ))
    patterns = (
        re.compile(r"\bhttp\.HandleFunc\s*\(\s*([^,\)]+)", re.S),
        re.compile(r"\bhttp\.Handle\s*\(\s*([^,\)]+)", re.S),
        re.compile(
            r"\b(\w+)\.(Get|Post|Put|Patch|Delete|Head|Options)\s*\(\s*([^,\)]+)",
            re.I | re.S,
        ),
    )
    for pattern in patterns[:2]:
        for match in pattern.finditer(code):
            if not _outside_js_string(code, match.start()) or is_dead(match.start()):
                continue
            number = code.count("\n", 0, match.start()) + 1
            routes.append(_route(
                service_path, framework, None, match.group(1).strip(),
                _location(repo, path, number),
            ))
    registered_routes: list[tuple[int, str, str | None, str]] = []
    for match in patterns[2].finditer(code):
        if not _outside_js_string(code, match.start()) or is_dead(match.start()):
            continue
        receiver_name, method, raw_path = match.groups()
        registered_routes.append(
            (match.start(), receiver_name, method.upper(), raw_path)
        )
    method_pattern = re.compile(
        r"\b(\w+)\.Method\s*\(\s*([^,\)]+)\s*,\s*([^,\)]+)",
        re.I | re.S,
    )
    for match in method_pattern.finditer(code):
        if not _outside_js_string(code, match.start()) or is_dead(match.start()):
            continue
        receiver_name, raw_method, raw_path = match.groups()
        literal_method, _ = _path_value(raw_method)
        method = (
            literal_method.upper()
            if literal_method is not None and literal_method.upper() in METHODS
            else None
        )
        registered_routes.append((match.start(), receiver_name, method, raw_path))

    for position, receiver_name, method, raw_path in sorted(registered_routes):
        receiver = resolve_receiver(receiver_name, position)
        if receiver is None:
            continue
        receiver_identity, receiver_state = receiver
        number = code.count("\n", 0, position) + 1
        containing = [
            block for block in inline_blocks
            if block[0] < position < block[1]
            and block[2] == receiver_identity
        ]
        route_prefix = (
            min(containing, key=lambda block: block[1] - block[0])[3]
            if containing
            else effective_prefix(receiver_identity)
        )
        route = _route(
            service_path, framework, method,
            _prefixed_raw_path(route_prefix, raw_path.strip()),
            _location(repo, path, number),
            "global" if receiver_state["auth"] else "unknown", receiver_identity,
            receiver_state["attribution"],
        )
        containing_function = [
            block for block in function_blocks
            if block[0] <= position < block[1] and receiver_name in block[3]
        ]
        if containing_function:
            function_block = min(
                containing_function,
                key=lambda item: item[1] - item[0],
            )
            service_root = repo / service_path if service_path else repo
            route["_go_package"] = path.parent.relative_to(service_root).as_posix() or "."
            route["_go_function"] = function_block[2]
            route["_go_router_parameter"] = function_block[3][receiver_name]
        routes.append(route)
    return routes, any(state["attribution"] for state in states.values()), mounts


IDENTITY_VERIFIER_PATTERN = re.compile(
    r"(?:verify_signed_(?:(?:customer|tenant)_identity|(?:customer|tenant|identity)(?:_id)?)|"
    r"verify_(?:customer|tenant)_(?:jwt|token)|"
    r"authenticate_(?:customer|tenant)_identity)",
    re.I,
)
AUTH_TOKEN_VERIFIER_PATTERN = re.compile(r"verify_?(?:jwt|token)", re.I)
IDENTITY_CROSSWALK_PATTERN = re.compile(
    r"(?:(?:customer|tenant)_crosswalk|crosswalk_(?:customer|tenant)|"
    r"(?:resolve|lookup)_(?:customer|tenant)(?:_(?:identity|id|key))?)",
    re.I,
)


def _trusted_identity_provider_name(value: str) -> bool:
    leaf = value.rsplit(".", 1)[-1]
    return any(
        pattern.fullmatch(leaf) is not None
        for pattern in (
            IDENTITY_VERIFIER_PATTERN,
            AUTH_TOKEN_VERIFIER_PATTERN,
            IDENTITY_CROSSWALK_PATTERN,
        )
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
    sources = [*value.args, *(keyword.value for keyword in value.keywords)]
    for source in sources:
        if isinstance(source, ast.Name):
            source_line = raw_variables.get(source.id)
            if source_line is not None:
                return source_line
            continue
        source_text = ast.get_source_segment(text, source) or _dotted_name(source)
        if raw_pattern.search(source_text):
            return source.lineno
    return None


def _assignment_bindings(
    node: ast.Assign | ast.AnnAssign | ast.AugAssign,
) -> list[tuple[ast.AST, ast.AST | None]]:
    def bind(target: ast.AST, value: ast.AST | None) -> list[tuple[ast.AST, ast.AST | None]]:
        if isinstance(target, ast.Starred):
            return bind(target.value, None)
        if not isinstance(target, (ast.Tuple, ast.List)):
            return [(target, value)]
        if (
            isinstance(value, (ast.Tuple, ast.List))
            and len(target.elts) == len(value.elts)
            and not any(isinstance(item, ast.Starred) for item in target.elts)
        ):
            return [
                binding
                for target_item, value_item in zip(target.elts, value.elts)
                for binding in bind(target_item, value_item)
            ]
        return [
            binding
            for target_item in target.elts
            for binding in bind(target_item, None)
        ]

    if isinstance(node, ast.AugAssign):
        return bind(node.target, None)
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [binding for target in targets for binding in bind(target, node.value)]


def _python_ingress_reachable_functions(
    files: list[Path],
    service_context: dict[str, Any],
) -> set[tuple[Path, int]]:
    definitions: dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]] = {}
    local_definitions: dict[Path, dict[str, str]] = {}

    for path, context in service_context["files"].items():
        module = context["module"]
        grouped: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for node in context["tree"].body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                grouped.setdefault(node.name, []).append(node)
        for name, nodes in grouped.items():
            if len(nodes) != 1:
                continue
            identity = ".".join(part for part in (module, name) if part)
            definitions[identity] = (path, nodes[0])
            local_definitions.setdefault(path, {})[name] = identity

    def resolve_function(path: Path, expression: ast.AST) -> str | None:
        context = service_context["files"].get(path)
        name = _dotted_name(expression)
        if context is None or not name:
            return None
        head, separator, tail = name.partition(".")
        scope = context["node_scopes"].get(id(expression), context["module_scope"])
        while scope is not None:
            scoped = context["bindings"].get(scope, {})
            if head not in scoped:
                scope = context["scope_parents"].get(scope)
                continue
            values = set(scoped[head])
            if (
                scope == context["module_scope"]
                and not separator
                and values == {None}
            ):
                return local_definitions.get(path, {}).get(head)
            if len(values) != 1 or None in values:
                return None
            expanded = next(iter(values))
            identity = f"{expanded}.{tail}" if separator else expanded
            return identity if identity in definitions else None
        return None

    edges: dict[str, set[str]] = {identity: set() for identity in definitions}
    roots: set[str] = set()
    receivers = service_context["receivers"]

    for identity, (path, function) in definitions.items():
        for decorator in function.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if isinstance(decorator.func, ast.Attribute):
                receiver = service_context["canonical"](
                    path,
                    _dotted_name(decorator.func.value),
                    decorator,
                )
                if receiver in receivers and (
                    decorator.func.attr.upper() in METHODS
                    or decorator.func.attr in {"api_route", "middleware"}
                ):
                    roots.add(identity)
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            target_node = call.func
            if _call_name(call.func).rsplit(".", 1)[-1] == "Depends":
                dependency = _call_argument(call, 0, "dependency")
                if dependency is None:
                    continue
                target_node = dependency
            target = resolve_function(path, target_node)
            if target is not None:
                edges[identity].add(target)

    for path, context in service_context["files"].items():
        for call in (node for node in ast.walk(context["tree"]) if isinstance(node, ast.Call)):
            if isinstance(call.func, ast.Attribute):
                receiver = service_context["canonical"](
                    path,
                    _dotted_name(call.func.value),
                    call,
                )
                if receiver in receivers and call.func.attr == "add_api_route":
                    endpoint = _call_argument(call, 1, "endpoint")
                    if endpoint is not None:
                        target = resolve_function(path, endpoint)
                        if target is not None:
                            roots.add(target)
            constructor = service_context["canonical"](
                path,
                _dotted_name(call.func),
                call,
            )
            dependency_site = (
                constructor.rsplit(".", 1)[-1] in {"FastAPI", "APIRouter"}
                or (
                    isinstance(call.func, ast.Attribute)
                    and call.func.attr in {"add_api_route", "include_router"}
                    and receiver in receivers
                )
            )
            if dependency_site:
                for dependency_call in (
                    child
                    for child in ast.walk(call)
                    if isinstance(child, ast.Call)
                    and _call_name(child.func).rsplit(".", 1)[-1] == "Depends"
                ):
                    dependency = _call_argument(
                        dependency_call,
                        0,
                        "dependency",
                    )
                    if dependency is None:
                        continue
                    target = resolve_function(path, dependency)
                    if target is not None:
                        roots.add(target)
            if constructor != "starlette.routing.Route" or len(call.args) < 2:
                continue
            target = resolve_function(path, call.args[1])
            if target is not None:
                roots.add(target)

    reachable = set(roots)
    pending = list(roots)
    while pending:
        current = pending.pop()
        for target in edges.get(current, set()):
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    return {
        (definitions[identity][0], definitions[identity][1].lineno)
        for identity in reachable
    }


IDENTITY_HEADER_NAME_PATTERN = re.compile(
    r"x[-_]?(?:moolabs|customer|tenant)[-_]?(?:id|customer|tenant)?",
    re.I,
)


def _python_raw_identity_header_lines(
    tree: ast.AST,
    dead_nodes: set[int],
) -> list[int]:
    lines: set[int] = set()

    def is_headers(value: ast.AST) -> bool:
        return (
            isinstance(value, ast.Name)
            and value.id.lower() in {"header", "headers"}
        ) or (
            isinstance(value, ast.Attribute)
            and value.attr.lower() in {"header", "headers"}
        )

    def is_identity_header(value: ast.AST) -> bool:
        return (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and IDENTITY_HEADER_NAME_PATTERN.search(value.value) is not None
        )

    for node in ast.walk(tree):
        if id(node) in dead_nodes:
            continue
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr.lower() == "get"
            and is_headers(node.func.value)
            and node.args
            and is_identity_header(node.args[0])
        ):
            lines.add(node.lineno)
        elif (
            isinstance(node, ast.Subscript)
            and is_headers(node.value)
            and is_identity_header(node.slice)
        ):
            lines.add(node.lineno)
    return sorted(lines)


def _lexical_raw_identity_header_lines(
    text: str,
    raw_pattern: re.Pattern[str],
) -> list[int]:
    code = _without_js_comments(text)
    return sorted({
        code.count("\n", 0, match.start()) + 1
        for match in raw_pattern.finditer(code)
        if _outside_js_string(code, match.start())
    })


def _resolver_and_async(
    repo: Path,
    files: list[Path],
    python_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_headers: list[dict[str, Any]] = []
    candidates: list[tuple[Path, int, str, str]] = []
    async_hops: list[dict[str, Any]] = []
    raw_pattern = re.compile(
        r"(?:headers\s*\[\s*|headers?\.(?:get|Get)\s*\(\s*)"
        r"[\"'][^\"']*"
        r"x[-_]?(?:moolabs|customer|tenant)[-_]?(?:id|customer|tenant)?"
        r"[^\"']*[\"']",
        re.I | re.S,
    )
    context_pattern = re.compile(r"(?:request\.(?:state|context)|claims|auth|current_user)\.[A-Za-z_]*(?:customer|tenant|account)[A-Za-z_]*", re.I)
    reachable_functions = _python_ingress_reachable_functions(files, python_context)

    def record_header_findings(
        path: Path,
        lines: list[int],
        verified_lines: set[int],
    ) -> None:
        for number in lines:
            if number in verified_lines:
                raw_headers.append({
                    "code": "verified_identity_header",
                    "severity": "info",
                    "message": "raw inbound identity header has a supported verification and context-binding chain",
                    "evidence": _location(repo, path, number),
                })
            else:
                raw_headers.append({
                    "code": "raw_identity_header",
                    "severity": "high",
                    "message": "raw inbound identity header is not trusted resolver evidence",
                    "evidence": _location(repo, path, number),
                })

    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        verified_header_lines: set[int] = set()
        async_hops.extend(_async_boundaries(repo, path, text))
        if path.suffix != ".py":
            record_header_findings(
                path,
                _lexical_raw_identity_header_lines(text, raw_pattern),
                verified_header_lines,
            )
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            record_header_findings(path, [], verified_header_lines)
            continue
        dead_nodes = _dead_python_nodes(tree)
        for function in (
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and id(node) not in dead_nodes
        ):
            state: dict[str, Any] = {
                "trusted_roots": set(),
                "trusted_root_aliases": {"request": "request"},
                "raw_variables": {},
                "verified_variables": {},
                "verified_contexts": {},
                "verified_binding_source_lines": set(),
                "tainted_contexts": set(),
                "tainted_context_roots": set(),
                "aliases": {},
                "resolver_candidates": {},
            }
            terminal_states: list[tuple[str, dict[str, Any]]] = []
            positional = list(function.args.posonlyargs) + list(function.args.args)
            defaults = [None] * (len(positional) - len(function.args.defaults)) + list(function.args.defaults)
            argument_defaults = [
                *zip(positional, defaults),
                *zip(function.args.kwonlyargs, function.args.kw_defaults),
            ]
            for argument, default in argument_defaults:
                if isinstance(default, ast.Call) and _call_name(default.func).rsplit(".", 1)[-1] == "Depends":
                    dependency = _call_argument(default, 0, "dependency")
                    if dependency is not None and _trusted_identity_provider_name(
                        _dotted_name(dependency)
                    ):
                        state["trusted_roots"].add(argument.arg)
                        state["trusted_root_aliases"][argument.arg] = argument.arg

            def clone(current: dict[str, Any]) -> dict[str, Any]:
                return {
                    key: value.copy()
                    for key, value in current.items()
                }

            def merge(paths: list[dict[str, Any] | None]) -> dict[str, Any] | None:
                reachable = [path_state for path_state in paths if path_state is not None]
                if not reachable:
                    return None
                merged = clone(reachable[0])
                for key in (
                    "trusted_roots",
                    "verified_binding_source_lines",
                ):
                    merged[key].intersection_update(
                        *(path_state[key] for path_state in reachable[1:])
                    )
                merged["tainted_contexts"].update(
                    *(path_state["tainted_contexts"] for path_state in reachable[1:])
                )
                merged["tainted_context_roots"].update(
                    *(path_state["tainted_context_roots"] for path_state in reachable[1:])
                )
                for key in (
                    "raw_variables",
                    "verified_variables",
                    "verified_contexts",
                    "trusted_root_aliases",
                    "aliases",
                    "resolver_candidates",
                ):
                    merged[key] = {
                        name: value
                        for name, value in merged[key].items()
                        if all(path_state[key].get(name) == value for path_state in reachable[1:])
                    }
                return merged

            def equivalent_trusted_contexts(
                expression: str,
                root_aliases: dict[str, str],
            ) -> set[str]:
                context_identity = trusted_context_identity(
                    expression,
                    root_aliases,
                )
                if context_identity is None:
                    return {expression}
                canonical_root, suffix = context_identity
                equivalents = set()
                for alias, canonical in root_aliases.items():
                    if canonical == canonical_root:
                        equivalents.add(f"{alias}.{suffix}")
                    elif canonical_root.startswith(f"{canonical}."):
                        relative_root = canonical_root[len(canonical) + 1 :]
                        equivalents.add(f"{alias}.{relative_root}.{suffix}")
                if canonical_root in {"request.state", "request.context"}:
                    equivalents.add(f"{canonical_root}.{suffix}")
                return equivalents

            def trusted_context_identity(
                expression: str,
                root_aliases: dict[str, str],
            ) -> tuple[str, str] | None:
                for canonical_root in ("request.state", "request.context"):
                    prefix = f"{canonical_root}."
                    if expression.startswith(prefix):
                        return canonical_root, expression[len(prefix) :]
                root_name, separator, suffix = expression.partition(".")
                canonical_root = root_aliases.get(root_name)
                if not separator or canonical_root is None:
                    return None
                if canonical_root == "request":
                    request_part, nested_separator, nested_suffix = suffix.partition(".")
                    if nested_separator and request_part in {"state", "context"}:
                        return f"request.{request_part}", nested_suffix
                return canonical_root, suffix

            def canonical_mutation_root(
                expression: str,
                root_aliases: dict[str, str],
            ) -> str | None:
                if expression in {"request", "request.state", "request.context"}:
                    return expression
                root_name, separator, suffix = expression.partition(".")
                canonical = root_aliases.get(root_name)
                if canonical is None:
                    return None
                expanded = (
                    f"{canonical}.{suffix}"
                    if separator
                    else canonical
                )
                if expanded == "request":
                    return expanded
                for request_root in ("request.state", "request.context"):
                    if expanded == request_root or expanded.startswith(
                        f"{request_root}."
                    ):
                        return request_root
                return canonical

            def trusted_context_expression(
                expression: str,
                root_aliases: dict[str, str],
            ) -> bool:
                context_identity = trusted_context_identity(
                    expression,
                    root_aliases,
                )
                if context_identity is None:
                    return False
                _canonical_root, suffix = context_identity
                return bool(
                    re.search(r"(?:customer|tenant|account)", suffix, re.I)
                )

            def is_context_expression(
                expression: str,
                root_aliases: dict[str, str],
            ) -> bool:
                return bool(
                    context_pattern.fullmatch(expression)
                    or trusted_context_expression(expression, root_aliases)
                )

            def verified_context_source(
                expression: str,
                root_aliases: dict[str, str],
                verified_contexts: dict[str, int],
            ) -> int | None:
                return next(
                    (
                        verified_contexts[equivalent]
                        for equivalent in sorted(
                            equivalent_trusted_contexts(expression, root_aliases)
                        )
                        if equivalent in verified_contexts
                    ),
                    None,
                )

            def context_is_trusted(
                expression: str,
                current: dict[str, Any],
            ) -> tuple[bool, int | None]:
                root_aliases = current["trusted_root_aliases"]
                equivalents = equivalent_trusted_contexts(expression, root_aliases)
                if not is_context_expression(expression, root_aliases) or any(
                    equivalent in current["tainted_contexts"]
                    for equivalent in equivalents
                ):
                    return False, None
                root_name = expression.split(".", 1)[0]
                source_line = verified_context_source(
                    expression,
                    root_aliases,
                    current["verified_contexts"],
                )
                context_identity = trusted_context_identity(expression, root_aliases)
                canonical_root = context_identity[0] if context_identity else None
                root_tainted = canonical_root in current["tainted_context_roots"] or (
                    "request" in current["tainted_context_roots"]
                    and canonical_root in {"request.state", "request.context"}
                )
                return (
                    source_line is not None
                    or (
                        root_name in current["trusted_roots"]
                        and not root_tainted
                    ),
                    source_line,
                )

            def taint_context(
                expression: str,
                current: dict[str, Any],
                root_aliases: dict[str, str],
            ) -> None:
                equivalent_contexts = equivalent_trusted_contexts(
                    expression,
                    root_aliases,
                )
                current["tainted_contexts"].update(equivalent_contexts)
                for equivalent_context in equivalent_contexts:
                    current["verified_contexts"].pop(equivalent_context, None)
                    current["resolver_candidates"].pop(equivalent_context, None)
                current["aliases"] = {
                    name: provenance
                    for name, provenance in current["aliases"].items()
                    if provenance[0] != "trusted"
                    or not (
                        equivalent_trusted_contexts(provenance[1], root_aliases)
                        & equivalent_contexts
                    )
                }

            def taint_context_root(
                expression: str,
                current: dict[str, Any],
                root_aliases: dict[str, str],
            ) -> None:
                canonical_root = canonical_mutation_root(expression, root_aliases)
                if canonical_root is None:
                    return
                current["tainted_context_roots"].add(canonical_root)

                def affected(context_expression: str) -> bool:
                    identity = trusted_context_identity(
                        context_expression,
                        root_aliases,
                    )
                    if identity is None:
                        return False
                    context_root = identity[0]
                    return context_root == canonical_root or (
                        canonical_root == "request"
                        and context_root in {"request.state", "request.context"}
                    )

                current["verified_contexts"] = {
                    context_expression: source_line
                    for context_expression, source_line in current[
                        "verified_contexts"
                    ].items()
                    if not affected(context_expression)
                }
                current["resolver_candidates"] = {
                    context_expression: candidate
                    for context_expression, candidate in current[
                        "resolver_candidates"
                    ].items()
                    if not affected(context_expression)
                }
                current["aliases"] = {
                    name: provenance
                    for name, provenance in current["aliases"].items()
                    if provenance[0] != "trusted" or not affected(provenance[1])
                }

            def record_calls(root: ast.AST | None, current: dict[str, Any]) -> None:
                if root is None:
                    return
                for call in (child for child in ast.walk(root) if isinstance(child, ast.Call)):
                    call_name = _call_name(call.func)
                    mutation_calls = {
                            "setattr",
                            "builtins.setattr",
                            "delattr",
                            "builtins.delattr",
                            "object.__setattr__",
                            "object.__delattr__",
                        }
                    bound_dunder = (
                        isinstance(call.func, ast.Attribute)
                        and call.func.attr in {"__setattr__", "__delattr__"}
                        and call_name not in {
                            "object.__setattr__",
                            "object.__delattr__",
                        }
                    )
                    if call_name in mutation_calls or bound_dunder:
                        argument_offset = 0 if bound_dunder else 1
                        mutated_root_node = (
                            call.func.value
                            if bound_dunder
                            else call.args[0] if call.args else None
                        )
                        if mutated_root_node is None:
                            continue
                        mutated_root = _dotted_name(mutated_root_node)
                        field = (
                            call.args[argument_offset]
                            if len(call.args) > argument_offset
                            else None
                        )
                        if not (
                            isinstance(field, ast.Constant)
                            and isinstance(field.value, str)
                        ):
                            taint_context_root(
                                mutated_root,
                                current,
                                current["trusted_root_aliases"],
                            )
                            continue
                        mutated_expression = f"{mutated_root}.{field.value}"
                        if is_context_expression(
                            mutated_expression,
                            current["trusted_root_aliases"],
                        ):
                            taint_context(
                                mutated_expression,
                                current,
                                current["trusted_root_aliases"],
                            )
                            if call_name not in {
                                "delattr",
                                "builtins.delattr",
                                "object.__delattr__",
                            } and not (
                                bound_dunder and call.func.attr == "__delattr__"
                            ) and len(call.args) > argument_offset + 1:
                                value = call.args[argument_offset + 1]
                                source_line = _verified_identity_source_line(
                                    value,
                                    text,
                                    raw_pattern,
                                    current["raw_variables"],
                                )
                                if source_line is None and isinstance(value, ast.Name):
                                    source_line = current["verified_variables"].get(
                                        value.id
                                    )
                                if source_line is not None:
                                    for equivalent in equivalent_trusted_contexts(
                                        mutated_expression,
                                        current["trusted_root_aliases"],
                                    ):
                                        current["tainted_contexts"].discard(equivalent)
                                        current["verified_contexts"][equivalent] = (
                                            source_line
                                        )
                                    current["verified_binding_source_lines"].add(
                                        source_line
                                    )
                        continue
                    if not call.args:
                        continue
                    validator = call_name
                    argument = call.args[0]
                    if isinstance(argument, ast.Name):
                        provenance = current["aliases"].get(argument.id)
                    else:
                        expression = _dotted_name(argument)
                        trusted, source_line = context_is_trusted(
                            expression,
                            current,
                        )
                        provenance = (
                            (
                                "trusted",
                                expression,
                                source_line,
                            )
                            if trusted
                            else None
                        )
                    if not provenance or provenance[0] != "trusted":
                        continue
                    if re.fullmatch(
                        r"(?:UUID|uuid\.UUID|uuid\.Parse|validate_uuid|is_uuid|parse_uuid)",
                        validator,
                        re.I,
                    ):
                        current["resolver_candidates"][provenance[1]] = (
                            path,
                            call.lineno,
                            provenance[1],
                            "moolabs_uuid",
                            provenance[2],
                        )
                    elif IDENTITY_CROSSWALK_PATTERN.fullmatch(
                        validator.rsplit(".", 1)[-1]
                    ):
                        current["resolver_candidates"][provenance[1]] = (
                            path,
                            call.lineno,
                            provenance[1],
                            "external_key_crosswalk",
                            provenance[2],
                        )

            def apply_assignment(
                assignment: ast.Assign | ast.AnnAssign | ast.AugAssign,
                current: dict[str, Any],
            ) -> None:
                raw_before = dict(current["raw_variables"])
                verified_before = dict(current["verified_variables"])
                aliases_before = dict(current["aliases"])
                verified_contexts_before = dict(current["verified_contexts"])
                tainted_contexts_before = set(current["tainted_contexts"])
                trusted_root_aliases_before = dict(current["trusted_root_aliases"])
                evaluated: list[
                    tuple[
                        ast.AST,
                        ast.AST | None,
                        int | None,
                        int | None,
                        tuple[str, str, int | None] | None,
                    ]
                ] = []
                for target, value in _assignment_bindings(assignment):
                    if value is None:
                        evaluated.append((target, value, None, False, None))
                        continue
                    expression = _dotted_name(value)
                    verified_source_line = _verified_identity_source_line(
                        value,
                        text,
                        raw_pattern,
                        raw_before,
                    )
                    direct_raw_source = bool(
                        verified_source_line is None and raw_pattern.search(expression)
                    )
                    inherited_verified_source = (
                        verified_before.get(value.id)
                        if isinstance(value, ast.Name)
                        else None
                    )
                    provenance = (
                        aliases_before.get(value.id)
                        if isinstance(value, ast.Name)
                        else None
                    )
                    before_state = {
                        "trusted_roots": set(current["trusted_roots"]),
                        "trusted_root_aliases": trusted_root_aliases_before,
                        "verified_contexts": verified_contexts_before,
                        "tainted_contexts": tainted_contexts_before,
                        "tainted_context_roots": set(
                            current["tainted_context_roots"]
                        ),
                    }
                    trusted, trusted_source_line = context_is_trusted(
                        expression,
                        before_state,
                    )
                    if provenance is None and trusted:
                        provenance = (
                            "trusted",
                            expression,
                            trusted_source_line,
                        )
                    elif provenance is None and direct_raw_source:
                        provenance = ("raw", expression, value.lineno)
                    evaluated.append((
                        target,
                        value,
                        verified_source_line,
                        inherited_verified_source,
                        provenance,
                    ))

                for (
                    target,
                    value,
                    verified_source_line,
                    inherited_verified_source,
                    provenance,
                ) in evaluated:
                    target_names = [target.id] if isinstance(target, ast.Name) else []
                    for target_name in target_names:
                        current["raw_variables"].pop(target_name, None)
                        current["verified_variables"].pop(target_name, None)
                        current["aliases"].pop(target_name, None)
                        current["trusted_roots"].discard(target_name)
                        current["trusted_root_aliases"].pop(target_name, None)
                        current["tainted_contexts"] = {
                            expression
                            for expression in current["tainted_contexts"]
                            if expression != target_name
                            and not expression.startswith(f"{target_name}.")
                        }
                        current["resolver_candidates"] = {
                            expression: candidate
                            for expression, candidate in current["resolver_candidates"].items()
                            if expression != target_name
                            and not expression.startswith(f"{target_name}.")
                        }
                    context_expression = _dotted_name(target)
                    equivalent_contexts = equivalent_trusted_contexts(
                        context_expression,
                        trusted_root_aliases_before,
                    )
                    if is_context_expression(
                        context_expression,
                        trusted_root_aliases_before,
                    ):
                        taint_context(
                            context_expression,
                            current,
                            trusted_root_aliases_before,
                        )
                    if value is None:
                        continue

                    expression = _dotted_name(value)
                    direct_raw_source = bool(
                        verified_source_line is None and raw_pattern.search(expression)
                    )
                    context_source_line = (
                        verified_source_line
                        if verified_source_line is not None
                        else inherited_verified_source
                    )
                    if (
                        (
                            is_context_expression(
                                context_expression,
                                trusted_root_aliases_before,
                            )
                        )
                        and context_source_line is not None
                    ):
                        for equivalent_context in equivalent_contexts:
                            current["tainted_contexts"].discard(equivalent_context)
                            current["verified_contexts"][
                                equivalent_context
                            ] = context_source_line
                        current["verified_binding_source_lines"].add(
                            context_source_line
                        )
                    if isinstance(value, ast.Call) and _trusted_identity_provider_name(
                        _call_name(value.func)
                    ):
                        current["trusted_roots"].update(target_names)
                        for target_name in target_names:
                            current["trusted_root_aliases"][target_name] = target_name
                    elif (
                        isinstance(value, ast.Name)
                        and value.id in trusted_root_aliases_before
                    ):
                        canonical_root = trusted_root_aliases_before[value.id]
                        for target_name in target_names:
                            if value.id in current["trusted_roots"]:
                                current["trusted_roots"].add(target_name)
                            current["trusted_root_aliases"][target_name] = canonical_root
                            inherited_taints = set()
                            for tainted_expression in current["tainted_contexts"]:
                                tainted_identity = trusted_context_identity(
                                    tainted_expression,
                                    trusted_root_aliases_before,
                                )
                                if (
                                    tainted_identity is not None
                                    and tainted_identity[0] == canonical_root
                                ):
                                    inherited_taints.add(
                                        f"{target_name}.{tainted_identity[1]}"
                                    )
                            current["tainted_contexts"].update(inherited_taints)
                    elif _dotted_name(value) in {
                        "request.state",
                        "request.context",
                    }:
                        for target_name in target_names:
                            current["trusted_root_aliases"][target_name] = _dotted_name(
                                value
                            )
                    if direct_raw_source:
                        for target_name in target_names:
                            current["raw_variables"][target_name] = value.lineno
                    if verified_source_line is not None:
                        for target_name in target_names:
                            current["verified_variables"][target_name] = verified_source_line
                    if provenance is not None:
                        for target_name in target_names:
                            current["aliases"][target_name] = provenance

            def clear_target(target: ast.AST, current: dict[str, Any]) -> None:
                for child, _ in _assignment_bindings(ast.AnnAssign(target, None, None, 1)):
                    root_aliases_before = dict(current["trusted_root_aliases"])
                    if isinstance(child, ast.Name):
                        current["raw_variables"].pop(child.id, None)
                        current["verified_variables"].pop(child.id, None)
                        current["aliases"].pop(child.id, None)
                        current["trusted_roots"].discard(child.id)
                        current["trusted_root_aliases"].pop(child.id, None)
                        current["tainted_contexts"] = {
                            expression
                            for expression in current["tainted_contexts"]
                            if expression != child.id
                            and not expression.startswith(f"{child.id}.")
                        }
                        current["resolver_candidates"] = {
                            expression: candidate
                            for expression, candidate in current["resolver_candidates"].items()
                            if expression != child.id
                            and not expression.startswith(f"{child.id}.")
                        }
                    context_expression = _dotted_name(child)
                    if is_context_expression(
                        context_expression,
                        root_aliases_before,
                    ):
                        taint_context(
                            context_expression,
                            current,
                            root_aliases_before,
                        )

            def process_statements(
                statements: list[ast.stmt],
                current: dict[str, Any],
            ) -> dict[str, Any] | None:
                for statement in statements:
                    if id(statement) in dead_nodes:
                        continue
                    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        continue
                    if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                        record_calls(statement.value, current)
                        apply_assignment(statement, current)
                    elif isinstance(statement, ast.If):
                        record_calls(statement.test, current)
                        if isinstance(statement.test, ast.Constant):
                            selected = statement.body if bool(statement.test.value) else statement.orelse
                            result = process_statements(selected, current)
                        else:
                            result = merge([
                                process_statements(statement.body, clone(current)),
                                process_statements(statement.orelse, clone(current))
                                if statement.orelse
                                else clone(current),
                            ])
                        if result is None:
                            return None
                        current = result
                    elif isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                        expression = statement.iter if isinstance(
                            statement, (ast.For, ast.AsyncFor)
                        ) else statement.test
                        record_calls(expression, current)
                        terminal_start = len(terminal_states)
                        body_state = process_statements(statement.body, clone(current))
                        loop_terminals = terminal_states[terminal_start:]
                        del terminal_states[terminal_start:]
                        terminal_states.extend(
                            terminal
                            for terminal in loop_terminals
                            if terminal[0] not in {"break", "continue"}
                        )
                        break_states = [
                            path_state
                            for kind, path_state in loop_terminals
                            if kind == "break"
                        ]
                        continue_states = [
                            path_state
                            for kind, path_state in loop_terminals
                            if kind == "continue"
                        ]
                        looping_result = merge([
                            current,
                            body_state,
                            *continue_states,
                        ])
                        result = merge([looping_result, *break_states])
                        if result is None:
                            return None
                        if statement.orelse:
                            else_state = process_statements(
                                statement.orelse,
                                clone(looping_result),
                            )
                            result = merge([else_state, *break_states])
                            if result is None:
                                return None
                        current = result
                    elif isinstance(statement, (ast.With, ast.AsyncWith)):
                        for item in statement.items:
                            record_calls(item.context_expr, current)
                        result = process_statements(statement.body, current)
                        if result is None:
                            return None
                        current = result
                    elif isinstance(statement, ast.Try):
                        terminal_start = len(terminal_states)
                        normal = process_statements(statement.body, clone(current))
                        if normal is not None and statement.orelse:
                            normal = process_statements(statement.orelse, normal)
                        paths = [normal]
                        paths.extend(
                            process_statements(handler.body, clone(current))
                            for handler in statement.handlers
                        )
                        if statement.finalbody:
                            pending_terminals = terminal_states[terminal_start:]
                            del terminal_states[terminal_start:]
                            paths = [
                                process_statements(
                                    statement.finalbody,
                                    clone(path_state),
                                )
                                if path_state is not None
                                else None
                                for path_state in paths
                            ]
                            for kind, path_state in pending_terminals:
                                resumed = process_statements(
                                    statement.finalbody,
                                    clone(path_state),
                                )
                                if resumed is not None:
                                    terminal_states.append((kind, resumed))
                        result = merge(paths)
                        if result is None:
                            return None
                        current = result
                    elif isinstance(statement, ast.Match):
                        record_calls(statement.subject, current)
                        paths: list[dict[str, Any] | None] = []
                        exhaustive = False
                        for case in statement.cases:
                            case_state = clone(current)
                            record_calls(case.guard, case_state)
                            paths.append(process_statements(case.body, case_state))
                            exhaustive = exhaustive or isinstance(case.pattern, ast.MatchAs) and (
                                case.pattern.name is None and case.pattern.pattern is None
                            )
                        if not exhaustive:
                            paths.append(current)
                        result = merge(paths)
                        if result is None:
                            return None
                        current = result
                    elif isinstance(statement, ast.Delete):
                        for target in statement.targets:
                            clear_target(target, current)
                    elif isinstance(statement, ast.Return):
                        for child in ast.iter_child_nodes(statement):
                            record_calls(child, current)
                        terminal_states.append(("return", clone(current)))
                        return None
                    elif isinstance(statement, ast.Raise):
                        for child in ast.iter_child_nodes(statement):
                            record_calls(child, current)
                        terminal_states.append(("raise", clone(current)))
                        return None
                    elif isinstance(statement, ast.Break):
                        terminal_states.append(("break", clone(current)))
                        return None
                    elif isinstance(statement, ast.Continue):
                        terminal_states.append(("continue", clone(current)))
                        return None
                    else:
                        record_calls(statement, current)
                return current

            fallthrough_state = process_statements(function.body, state)
            final_state = merge([
                *(
                    path_state
                    for kind, path_state in terminal_states
                    if kind == "return"
                ),
                fallthrough_state,
            ])
            if final_state is not None:
                verified_header_lines.update(
                    final_state["verified_binding_source_lines"]
                )
                verified_header_lines.update(final_state["verified_contexts"].values())
                for candidate in final_state["resolver_candidates"].values():
                    if (path, function.lineno) in reachable_functions:
                        candidates.append(candidate[:4])
                    if candidate[4] is not None:
                        verified_header_lines.add(candidate[4])
        record_header_findings(
            path,
            _python_raw_identity_header_lines(tree, dead_nodes),
            verified_header_lines,
        )
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
    digest.update(b"moolabs-attribution-scanner\0")
    digest.update(SCANNER_VERSION.encode("ascii"))
    digest.update(b"\0")
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
        service_root = repo / service_path if service_path else repo
        go_mod = service_root / "go.mod"
        module_match = re.search(
            r"(?m)^\s*module\s+(\S+)",
            go_mod.read_text(encoding="utf-8", errors="replace")
            if go_mod.is_file()
            else "",
        )
        go_module_path = module_match.group(1) if module_match else ""
        go_attributed_call_targets = set().union(*(
            _go_attributed_call_targets(
                path,
                path.read_text(encoding="utf-8", errors="replace"),
                service_root,
                go_module_path,
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
        resolver, findings, async_hops = _resolver_and_async(
            repo,
            files,
            python_context,
        )
        findings.extend(_python_parse_findings(repo, files))
        unresolved_mount_targets = {
            mount.get("_target_receiver", mount["target"].rsplit(".", 1)[-1])
            for mount in mounts
            if mount["prefix"] is None or mount["confidence"] == "low"
        }
        unresolved_mounts = [
            mount
            for mount in mounts
            if mount["prefix"] is None or mount["confidence"] == "low"
        ]
        if unresolved_mounts:
            findings.append({
                "code": "mount_unresolved",
                "severity": "high",
                "message": "mount target or path could not be resolved statically",
                "evidence": sorted(
                    unresolved_mounts,
                    key=lambda item: (
                        item["evidence"]["file"],
                        item["evidence"]["line"],
                    ),
                )[0]["evidence"],
            })
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
        if ingress_state == "no-middleware-inherits-thread-id":
            resolver = {
                "state": "not-required",
                "identity_kind": None,
                "expression": None,
                "template": None,
                "evidence": None,
            }
        route_keys: set[tuple[Any, ...]] = set()
        unique_routes: list[dict[str, Any]] = []
        for route in sorted(routes, key=lambda item: (item["framework"], str(item["method"]), str(item["path_template"]), item["evidence"]["file"], item["evidence"]["line"])):
            if (
                route["framework"] == "chi"
                and not route.get("_middleware_covered")
                and (
                    route.get("_go_package"),
                    route.get("_go_function"),
                    route.get("_go_router_parameter"),
                ) in go_attributed_call_targets
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
            route.pop("_go_package", None)
            route.pop("_go_function", None)
            route.pop("_go_router_parameter", None)
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
    result = {"schema_version": "1.0", "scanner_version": SCANNER_VERSION,
              "generated_at": generated_at,
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
    for branch in schema.get("allOf", []):
        _validate_schema(value, branch, root, location)
    if "if" in schema:
        try:
            _validate_schema(value, schema["if"], root, location)
            condition_matches = True
        except DiscoveryError:
            condition_matches = False
        branch = schema.get("then" if condition_matches else "else")
        if branch is not None:
            _validate_schema(value, branch, root, location)
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
