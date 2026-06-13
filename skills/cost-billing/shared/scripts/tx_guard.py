"""Post-commit guard — flag billing emits placed BEFORE the transaction commits.

A billing emit inside an open transaction PHANTOM-BILLS on rollback: the action is rolled
back but the bill was already sent, and there is no retry to reconcile it away (the
entity_id retry-dedup only covers DUPLICATE emits, not a rollback that should have emitted
NOTHING). It also OVER-bills if the commit later fails. So a billing emit must fire AFTER
the transaction recording the billable action has committed — ideally via an outbox (a row
written in the SAME tx, dispatched post-commit) so the emit is atomic with the action.

This statically flags the PROVABLE pre-commit placements (the bounded, certain cases) and
stays silent otherwise — it cannot prove a placement is post-commit (control flow is
unbounded), so `clear` is "not obviously pre-commit", not a guarantee. Same loud-when-
certain contract as the reachability gate. Python-focused (AST); other languages -> unknown.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

# Transaction context-manager call names (SQLAlchemy / Django / generic DBAPI):
# `session.begin()`, `transaction.atomic()`, `db.transaction()`, `engine.begin()`, ...
_TX_CTX_NAMES = {"begin", "atomic", "transaction", "begin_nested", "transaction_scope"}
# Whole-function transaction decorators: `@transactional`, `@atomic`, `@transaction.atomic`.
_TX_DECOS = {"transactional", "atomic", "transaction"}


@dataclass(frozen=True)
class TxPosition:
    status: str   # inside_tx | before_commit | clear | unknown
    note: str

    @property
    def flagged(self) -> bool:
        """Provable pre-commit placements flag. `clear`/`unknown` do not — but `clear` is
        not a proof of post-commit; confirm the action is durable before the emit."""
        return self.status in ("inside_tx", "before_commit")


def _name_of(node) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_of(node.func)
    return None


def _innermost_func(tree, line):
    best = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.lineno <= line <= (getattr(node, "end_lineno", None) or node.lineno):
                if best is None or node.lineno > best.lineno:
                    best = node
    return best


def classify_tx_position(source: str, line: int, language: str = "python") -> TxPosition:
    """Where does the emit at `line` sit relative to the transaction boundary?"""
    if language != "python":
        return TxPosition("unknown",
                          f"{language}: tx position not statically checked here — verify by "
                          f"hand that the emit fires AFTER commit")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return TxPosition("unknown", "source did not parse — verify after-commit by hand")

    fn = _innermost_func(tree, line)

    # (1) whole-function transaction decorator -> the emit is inside the tx
    if fn is not None:
        for d in fn.decorator_list:
            if _name_of(d) in _TX_DECOS:
                return TxPosition(
                    "inside_tx",
                    f"emit is inside @{_name_of(d)}-decorated function '{fn.name}' — the whole "
                    f"function runs in a transaction; the emit fires BEFORE commit -> "
                    f"phantom-bill on rollback. Move it AFTER commit (outbox dispatch).",
                )

    # (2) enclosing transaction with-block -> the emit is inside the tx
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            if any(_name_of(item.context_expr) in _TX_CTX_NAMES for item in node.items):
                if node.lineno <= line <= (getattr(node, "end_lineno", None) or node.lineno):
                    return TxPosition(
                        "inside_tx",
                        "emit is inside a transaction with-block (begin/atomic/transaction) "
                        "-> fires before commit -> phantom-bill on rollback. Move it after "
                        "the block exits.",
                    )

    # (3) an explicit .commit() LATER in the same function -> the emit likely precedes it
    if fn is not None:
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit" and node.lineno > line):
                return TxPosition(
                    "before_commit",
                    f"a .commit() at line {node.lineno} follows this emit (line {line}) in "
                    f"'{fn.name}' — the emit likely fires BEFORE commit -> phantom/over-bill "
                    f"on rollback. Place the emit AFTER the commit.",
                )

    return TxPosition(
        "clear",
        "no enclosing transaction block / no later commit() detected — not obviously "
        "pre-commit. This is NOT a proof of post-commit: confirm the billable action is "
        "durably committed before the emit fires.",
    )
