#!/usr/bin/env python3
"""insertion_point.py — deterministic emit-insert placement for the THREE supported
languages (python / typescript / go). Ships in cost-billing-shared (used by both
discovery and instrument; portable — no cross-skill import).

Verbatim-dogfood finding N: placement is a SEMANTIC problem `py_compile` (and a TS/Go
type-check) cannot catch — dead code AFTER a `return` compiles fine. Eyeballing
`entry.line` mis-placed every usage-only insert (mid-multiline-statement, after the
function's return, wrong indent). This makes placement DETERMINISTIC + test-gateable:

    find_insertion_point(source, lineno, language) -> InsertionPoint | None
    apply_insert(source, after_line, insert_text, indent) -> str

- Python uses the stdlib `ast` (no dependency).
- TypeScript / Go use tree-sitter — a SOFT dependency (`tree_sitter` +
  `tree_sitter_typescript` / `tree_sitter_go`). When it is ABSENT or its API is
  version-SKEWED, the capture returns None so the caller degrades to manual
  placement (human-PR-review-gated) instead of crashing. That absent path is the
  one most customers hit, so it is a first-class, tested behaviour — not an error.
- apply_insert is pure text manipulation: language-agnostic, no dependency.

`indent` is the actual leading-whitespace STRING of the work statement's line (not a
count) so tab-indented Go and space-indented python/ts are both reproduced exactly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# tree-sitter function/block node types for the languages we support. Kept narrow
# (the 3 supported languages) on purpose — this is NOT a generic multi-language AST.
_TS_FUNC_TYPES = frozenset({
    "function_declaration", "function_expression", "method_definition",
    "arrow_function", "generator_function_declaration",  # typescript / javascript
    "method_declaration", "func_literal",                 # go
})
# block containers whose DIRECT children are statements. ts: statement_block;
# go: a `block` wraps a `statement_list` whose children are the statements — so
# statement_list is the real container (a statement's parent), not `block`.
_TS_BLOCK_TYPES = frozenset({"statement_block", "block", "statement_list"})


@dataclass(frozen=True)
class InsertionPoint:
    function: str | None   # enclosing function name (None = module/top level)
    after_line: int        # 1-based line AFTER which the insert is placed
    indent: str            # leading-whitespace string the inserted block must use


def _leading_ws(source: str, line_1based: int) -> str:
    lines = source.splitlines()
    if 1 <= line_1based <= len(lines):
        ln = lines[line_1based - 1]
        return ln[: len(ln) - len(ln.lstrip())]
    return ""


def find_insertion_point(source: str, lineno: int,
                         language: str = "python") -> "InsertionPoint | None":
    """Return the deterministic placement target for an emit insert for the call at
    `lineno` in `source`, or None.

    The target is the INNERMOST statement that contains `lineno` (the "work"), the
    line after its FULL span, and that statement's indentation — so the emit lands
    right after the work, in the SAME block, before the enclosing function's return,
    never as dead code and never inside a multi-line expression.

    None means "no deterministic capture": for python a real syntax error / no
    enclosing statement (caller STOPs); for typescript/go ALSO when tree-sitter is
    absent or version-skewed (caller falls back to manual placement — NOT an error).
    """
    lang = (language or "python").lower()
    if lang == "python":
        return _python_insertion_point(source, lineno)
    if lang in ("typescript", "javascript", "go"):
        return _treesitter_insertion_point(source, lineno, lang)
    return None


def apply_insert(source: str, after_line: int, insert_text: str, indent: str) -> str:
    """Splice `insert_text` into `source` immediately AFTER the 1-based `after_line`,
    re-indented with the `indent` whitespace string. LANGUAGE-AGNOSTIC (pure text):
    works for python, typescript, and go alike. Blank lines stay blank (no trailing
    whitespace). `after_line` is clamped to [0, len(lines)] so out-of-range appends."""
    if after_line < 0:
        after_line = 0
    lines = source.splitlines()
    after = min(after_line, len(lines))
    block = [indent + ln if ln.strip() else "" for ln in insert_text.splitlines()]
    text = "\n".join(lines[:after] + block + lines[after:])
    return text + "\n" if source.endswith("\n") else text


# ── python (stdlib ast) ───────────────────────────────────────────────────────

def _innermost_function_name(tree: ast.Module, lineno: int) -> str | None:
    best, best_span = None, None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end:
                span = end - start
                if best_span is None or span < best_span:
                    best, best_span = node, span
    return best.name if best else None


def _python_insertion_point(source: str, lineno: int) -> "InsertionPoint | None":
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    func = _innermost_function_name(tree, lineno)
    target = None  # (span, end_line, start_line)
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end:
                span = end - start
                if target is None or span < target[0]:
                    target = (span, end, start)
    if target is None:
        return None
    _span, after_line, start_line = target
    return InsertionPoint(function=func, after_line=after_line,
                          indent=_leading_ws(source, start_line))


# ── typescript / go (tree-sitter soft dep) ────────────────────────────────────

def _ts_parser(language: str):
    """Build a tree-sitter parser for `language`, or None if tree-sitter is absent
    or its API is version-skewed. Catches the API-shape errors (TypeError/
    AttributeError/ValueError) the ecosystem throws across minor versions — not just
    ImportError — so version skew degrades to manual instead of crashing."""
    try:
        import tree_sitter as _ts
        if language in ("typescript", "javascript"):
            import tree_sitter_typescript as _lang
            grammar = _lang.language_typescript()
        elif language == "go":
            import tree_sitter_go as _lang
            grammar = _lang.language()
        else:
            return None
        return _ts.Parser(_ts.Language(grammar))
    except (ImportError, TypeError, AttributeError, ValueError):
        return None


def _treesitter_insertion_point(source: str, lineno: int,
                                language: str) -> "InsertionPoint | None":
    parser = _ts_parser(language)
    if parser is None:
        return None  # absent / skewed -> manual fallback (NOT an error)
    try:
        tree = parser.parse(source.encode())
    except Exception:  # noqa: BLE001 - any parse failure -> manual fallback
        return None

    def _deepest(node):
        for ch in node.children:
            if ch.start_point[0] + 1 <= lineno <= ch.end_point[0] + 1:
                return _deepest(ch)
        return node

    node = _deepest(tree.root_node)
    if node is None:
        return None
    stmt = node
    found_stmt = False
    func_name: str | None = None
    cur = node
    while cur is not None:
        if cur.type in _TS_FUNC_TYPES and func_name is None:
            nm = cur.child_by_field_name("name")
            func_name = nm.text.decode() if nm is not None else cur.type
        # The INNERMOST statement is the first node (walking up) whose parent is a
        # block container — take it once, don't let an outer block overwrite it
        # (go's statement_list is itself a child of `block`).
        if (not found_stmt and cur.parent is not None
                and cur.parent.type in _TS_BLOCK_TYPES):
            stmt = cur
            found_stmt = True
        cur = cur.parent
    return InsertionPoint(function=func_name,
                          after_line=stmt.end_point[0] + 1,
                          indent=_leading_ws(source, stmt.start_point[0] + 1))
