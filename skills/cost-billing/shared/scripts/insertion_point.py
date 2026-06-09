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
    # Set when placement is a BEST GUESS, not certain (def-line anchor, multiple
    # top-level returns). The caller MUST prepend a `# REVIEW PLACEMENT:` marker so
    # an un-verifiable placement is LOUD (reviewer-catchable) instead of a silent
    # mis-bill. None = high-confidence placement (happy path) — stay quiet.
    review_reason: str | None = None


def with_placement_marker(insert_text: str, review_reason: str | None,
                          comment_prefix: str) -> str:
    """Prepend a `<comment_prefix>REVIEW PLACEMENT: <reason>` line to `insert_text`
    when `review_reason` is set; return it unchanged otherwise. Deterministic +
    language-agnostic (comment_prefix is "# " for python, "// " for ts/go) so the
    loudness is itself test-gateable even though placement correctness is not."""
    if not review_reason:
        return insert_text
    return f"{comment_prefix}REVIEW PLACEMENT: {review_reason}\n{insert_text}"


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

    `lineno` only SELECTS the enclosing function; placement then targets that
    function's SUCCESS-RETURN path — before the final return, after the last work
    statement (and therefore after any early-failure guards). This is robust to the
    anchor being a def line, a `return`, or the work call, and it never places after
    the whole function (a scope/F821 crash) or after a return (dead code). A clean
    terminal try (body-success + handler-failures) is RECURSED into so the emit lands
    inside it, not dead after it. When placement is a best guess — def-line anchor,
    multiple top-level returns (success-vs-guard ambiguity), conditional/looped work
    hoisted to function level, or an after-position that may be UNREACHABLE (terminal
    all-return if/try/else) — `review_reason` is set so the caller can prepend a loud
    `# REVIEW PLACEMENT:` marker (see `with_placement_marker`).

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

def _innermost_function(tree: ast.Module, lineno: int):
    best, best_span = None, None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end:
                span = end - start
                if best_span is None or span < best_span:
                    best, best_span = node, span
    return best


def _module_level_point(source: str, tree: ast.Module, lineno: int) -> "InsertionPoint | None":
    # No enclosing function: fall back to the innermost statement containing the
    # line (module-level emit — rare).
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
    return InsertionPoint(function=None, after_line=after_line,
                          indent=_leading_ws(source, start_line))


def _is_true_literal(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _has_break(body) -> bool:
    return any(isinstance(sub, ast.Break)
               for stmt in body for sub in ast.walk(stmt))


def _block_exits(body) -> bool:
    return bool(body) and _always_exits(body[-1])


def _always_exits(stmt) -> bool:
    """True iff control NEVER passes to the statement AFTER `stmt` — every path
    returns or raises. Used to detect when placing after `stmt` is unreachable. Must
    only return True when CERTAIN (a false True makes us recurse into the wrong
    branch and under-bill the fall-through path)."""
    if isinstance(stmt, (ast.Return, ast.Raise)):
        return True
    if isinstance(stmt, ast.If):
        return bool(stmt.orelse) and _block_exits(stmt.body) and _block_exits(stmt.orelse)
    if isinstance(stmt, ast.Try):
        if stmt.finalbody and _block_exits(stmt.finalbody):
            return True
        success = stmt.orelse if stmt.orelse else stmt.body
        return _block_exits(success) and all(_block_exits(h.body) for h in stmt.handlers)
    if isinstance(stmt, ast.While):
        return _is_true_literal(stmt.test) and not _has_break(stmt.body)
    if isinstance(stmt, ast.With):
        return _block_exits(stmt.body)
    return False


def _is_clean_terminal_try(stmt) -> bool:
    """The ONE unreachable shape we can resolve deterministically: a try whose body
    returns on success and whose handlers all exit — and NO else/finally (those move
    the success path off the try body). Success path is unambiguously the try body."""
    return (isinstance(stmt, ast.Try) and not stmt.orelse and not stmt.finalbody
            and bool(stmt.body) and isinstance(stmt.body[-1], ast.Return)
            and all(_block_exits(h.body) for h in stmt.handlers))


def _success_target(body, source: str):
    """(after_line, indent_str, unreachable_uncertain) for a block's success-return
    path. Recurses into a clean terminal try (quiet); flags uncertain-unreachable for
    every other all-exit terminal shape (if/else, try/else, try/finally, while-True)."""
    last = body[-1]
    indent = _leading_ws(source, body[0].lineno)
    if isinstance(last, ast.Return):
        if len(body) >= 2:
            prev = body[-2]
            return (getattr(prev, "end_lineno", None) or prev.lineno), indent, False
        return last.lineno - 1, indent, False
    after = getattr(last, "end_lineno", None) or last.lineno
    if _always_exits(last):
        if _is_clean_terminal_try(last):
            return _success_target(last.body, source)   # recurse into the success path
        return after, indent, True                       # unreachable + can't resolve -> mark
    return after, indent, False


def _python_insertion_point(source: str, lineno: int) -> "InsertionPoint | None":
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    func = _innermost_function(tree, lineno)
    if func is None:
        return _module_level_point(source, tree, lineno)

    # Placement targets the enclosing function's SUCCESS-RETURN path — NOT the
    # statement at `entry.line`. entry.line only SELECTS the function; the emit then
    # lands before the function's final return (after the last work statement + after
    # any early-failure guards) so it fires on success. This is robust to the anchor
    # being a def line / a return / the work call, and never places after the whole
    # function (the F821 crash) or after a return (dead code).
    body = func.body
    if not body:
        return None
    # Placement = the function's success-return path (recurses into a clean terminal
    # try). `unreachable` is True when the after-position provably can't run and we
    # could NOT resolve it deterministically.
    after_line, body_indent, unreachable = _success_target(body, source)

    # make-it-loud: placement is a best guess (not certain) in these shapes.
    reasons: list[str] = []
    if lineno < body[0].lineno:
        reasons.append("anchor was the def signature, not a work statement — confirm "
                       "this is the right function and the emit is on the success path")
    if sum(1 for s in body if isinstance(s, ast.Return)) >= 2:
        reasons.append("function has multiple top-level returns — confirm the emit "
                       "fires on the SUCCESS path, not after an early-return guard")
    # conditional/looped work: entry.line is INSIDE a top-level CONDITIONAL/LOOP block
    # (NOT try/with, which run unconditionally), but the emit is hoisted to the
    # function level — so it would fire even when that block did not run (over-bill).
    for s in body:
        if isinstance(s, (ast.If, ast.For, ast.AsyncFor, ast.While)):
            end = getattr(s, "end_lineno", None) or s.lineno
            if s.lineno < lineno <= end:
                reasons.append("the anchored work is inside a conditional/loop block "
                               "but the emit is hoisted to the function level — confirm "
                               "it should fire even when that block did not execute")
                break
    if unreachable:
        reasons.append("the emit is placed after a block that always returns or raises "
                       "on every path — it may be UNREACHABLE; confirm it lands on the "
                       "executed success path")
    review_reason = "; ".join(reasons) or None

    return InsertionPoint(function=func.name, after_line=after_line,
                          indent=body_indent, review_reason=review_reason)


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
    # Walk up to the enclosing function (mirrors the python rule: entry.line SELECTS
    # the function; placement then targets the function's success-return path).
    func = None
    cur = node
    while cur is not None:
        if cur.type in _TS_FUNC_TYPES:
            func = cur
            break
        cur = cur.parent
    if func is None:
        # module/top-level: fall back to the innermost block-child statement.
        stmt = node
        c = node
        while c is not None:
            if c.parent is not None and c.parent.type in _TS_BLOCK_TYPES:
                stmt = c
                break
            c = c.parent
        return InsertionPoint(function=None, after_line=stmt.end_point[0] + 1,
                              indent=_leading_ws(source, stmt.start_point[0] + 1))

    nm = func.child_by_field_name("name")
    func_name = nm.text.decode() if nm is not None else func.type
    container = _ts_statements_container(func)
    if container is None:
        return None
    stmts = [c for c in container.children if c.is_named and c.type != "comment"]
    if not stmts:
        return None
    body_indent = _leading_ws(source, stmts[0].start_point[0] + 1)
    last = stmts[-1]
    if last.type == "return_statement":
        if len(stmts) >= 2:
            after_line = stmts[-2].end_point[0] + 1
        else:
            after_line = last.start_point[0]   # body is just `return ...` (line-1)
    else:
        after_line = last.end_point[0] + 1

    reasons: list[str] = []
    # def-line anchor: entry.line is at/above the FIRST body statement (the
    # signature/brace line) — not container.start (the `{` shares the signature line).
    if lineno < stmts[0].start_point[0] + 1:
        reasons.append("anchor was the function signature, not a work statement — "
                       "confirm this is the right function and the emit is on the success path")
    if sum(1 for s in stmts if s.type == "return_statement") >= 2:
        reasons.append("function has multiple top-level returns — confirm the emit "
                       "fires on the SUCCESS path, not after an early-return guard")
    # conditional/looped work: the anchored node's nearest enclosing block is a
    # NESTED block (not the function's own body container), so hoisting the emit to
    # the function level would fire it even when that block did not run.
    blk = node
    while blk is not None and blk.type not in _TS_BLOCK_TYPES:
        blk = blk.parent
    if blk is not None and blk.start_byte != container.start_byte:
        reasons.append("the anchored work is inside a conditional/loop block but the "
                       "emit is hoisted to the function level — confirm it should fire "
                       "even when that block did not execute")
    # Conservative unreachability mark (ts/go): full reachability analysis is
    # Python-only (stdlib ast); here we flag the direct analog of the proven dead-
    # placement shape — a function ending in a try/catch (or a switch), where placing
    # after it is dead if every branch returns. Narrow on purpose (low false-positive)
    # vs a full ts/go control-flow analysis, which the tree-sitter path does not do.
    if last.type != "return_statement" and last.type in (
            "try_statement", "switch_statement", "select_statement"):
        reasons.append("the emit is placed after a terminal " + last.type.split("_")[0]
                       + " — it may be UNREACHABLE if every branch returns; confirm it "
                       "lands on the executed success path")
    review_reason = "; ".join(reasons) or None

    return InsertionPoint(function=func_name, after_line=after_line,
                          indent=body_indent, review_reason=review_reason)


def _ts_statements_container(func):
    """The node whose direct children are the function body's statements: ts uses
    `statement_block`; go nests a `statement_list` inside a `block`."""
    block = None
    for ch in func.children:
        if ch.type in ("statement_block", "block"):
            block = ch
            break
    if block is None:
        return None
    for ch in block.children:
        if ch.type == "statement_list":   # go
            return ch
    return block                           # ts
