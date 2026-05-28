#!/usr/bin/env python3
"""context_classifier.py — W2 of worker-coverage-design.md.

Given a Python cost/usage call site, walk UP the AST to classify the enclosing
execution context into the shared taxonomy (shared/assets/execution-context.schema.yaml):

    http_request | queue_worker | stream_consumer | scheduled_job | cli_batch
    | background_task | unknown

CRITICAL design rule: never default to `http_request`. The original suite bug was
assuming every emission site was an HTTP handler, which made workers invisible. When
no signal matches we return `unknown` (confidence 0.0) so the site surfaces for
review/Phase-1.6 confirmation instead of being silently mis-attributed.

This is detection-only (no edits). The pipeline (W3) calls `classify_call_site`
with the line of a detected cost/usage call; W4 maps the returned context to the
right attribution-source family; W5 maps it to the right codemod template +
transport default.

Usage (library):
    from context_classifier import classify_call_site, classify_file
    result = classify_call_site(source_text, lineno=42)
    # result.context, result.signal, result.confidence
"""

from __future__ import annotations

import ast
from dataclasses import dataclass


# ── Output ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContextClassification:
    context: str        # execution_context enum value, or "unknown"
    signal: str         # what matched (audit trail), e.g. "decorator:celery.task"
    confidence: float   # 0.0 (unknown) .. 1.0


# ── Signal tables ────────────────────────────────────────────────────────────
# Keyed on the LAST attribute of a decorator's dotted path, so `@app.task`,
# `@celery.task`, and `@shared_task` all key on their final segment.
_DECORATOR_LAST: dict[str, str] = {
    # HTTP route decorators (FastAPI/Starlette/Flask/APIRouter/Blueprint)
    "get": "http_request", "post": "http_request", "put": "http_request",
    "patch": "http_request", "delete": "http_request", "head": "http_request",
    "options": "http_request", "route": "http_request", "websocket": "http_request",
    "api_route": "http_request",
    # Queue-worker task decorators (Celery/Dramatiq/Huey/RQ-via-job)
    "task": "queue_worker", "shared_task": "queue_worker", "actor": "queue_worker",
    # Scheduled-job decorators (Celery beat periodic, Huey periodic, APScheduler,
    # fastapi-utils repeat_every, aiocron)
    "periodic_task": "scheduled_job", "scheduled_job": "scheduled_job",
    "crontab": "scheduled_job", "repeat_every": "scheduled_job",
    # Stream-consumer decorator (Faust agent)
    "agent": "stream_consumer",
    # CLI decorators (Click/Typer)
    "command": "cli_batch", "group": "cli_batch",
}

# Module top-level imports that corroborate a stream-consumer loop.
_STREAM_IMPORTS = frozenset({
    "kafka", "aiokafka", "confluent_kafka", "faust",
    "google", "nats", "pulsar", "boto3",  # boto3 → kinesis (coarse)
})

# Imports that corroborate cli_batch.
_CLI_IMPORTS = frozenset({"argparse", "click", "typer"})

# Consumer-ish method names; only count when the receiver path also looks
# kafka/consumer-related (avoids false positives on unrelated `.poll()`).
_CONSUMER_METHODS = frozenset({"poll", "getmany", "consume"})


# ── AST helpers ──────────────────────────────────────────────────────────────

def _dotted(node: ast.AST | None) -> str | None:
    """Render a Name/Attribute chain as a dotted string, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _decorator_path(dec: ast.AST) -> str | None:
    target = dec.func if isinstance(dec, ast.Call) else dec
    return _dotted(target)


def _collect_imports(tree: ast.Module) -> frozenset[str]:
    """Top-level module names from `import x` / `from x import y`."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return frozenset(names)


def _has_main_guard(tree: ast.Module) -> bool:
    """Detect a module-level `if __name__ == "__main__":` block."""
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name) \
                and test.left.id == "__name__":
            for comp in test.comparators:
                if isinstance(comp, ast.Constant) and comp.value == "__main__":
                    return True
    return False


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_scope_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """Yield nodes in func's OWN scope, NOT descending into nested function/class
    bodies. `ast.walk` would descend into nested defs, so a function whose nested
    helper has a consumer loop would be misclassified — this keeps the scan local."""
    def _recurse(node: ast.AST):
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPES):
                continue
            yield from _recurse(child)

    for stmt in func.body:
        if isinstance(stmt, _NESTED_SCOPES):
            continue
        yield from _recurse(stmt)


def _has_consumer_loop(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the function's OWN body has a stream-consumer-shaped loop or poll call."""
    for node in _own_scope_nodes(func):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            it = node.iter.func if isinstance(node.iter, ast.Call) else node.iter
            itername = (_dotted(it) or "").lower()
            # Only "consumer" — NOT "subscription": in a cost-billing codebase
            # `for sub in subscriptions` is a billing loop, not a pub/sub consumer.
            # Real pub/sub subscriptions are caught via the .poll()/.consume()
            # method check below + a stream import, which don't collide with billing.
            if "consumer" in itername:
                return True
        if isinstance(node, ast.Call):
            fpath = (_dotted(node.func) or "").lower()
            last = fpath.rsplit(".", 1)[-1]
            if last in _CONSUMER_METHODS and ("consumer" in fpath or "kafka" in fpath):
                return True
    return False


# ── Core classification ──────────────────────────────────────────────────────

def classify_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    imports: frozenset[str] = frozenset(),
    has_main_guard: bool = False,
) -> ContextClassification:
    """Classify a single function by its decorators / body shape / module context."""
    # 1. Decorators are the strongest signal.
    for dec in func.decorator_list:
        path = _decorator_path(dec)
        if not path:
            continue
        last = path.rsplit(".", 1)[-1]
        ctx = _DECORATOR_LAST.get(last)
        if ctx:
            return ContextClassification(ctx, f"decorator:{path}", 0.9)

    # 2. Stream-consumer loop shape (aiokafka/confluent/etc. without a decorator).
    if _has_consumer_loop(func):
        conf = 0.7 if (imports & _STREAM_IMPORTS) else 0.55
        return ContextClassification("stream_consumer", "consumer-loop", conf)

    # 3. CLI entrypoint: a `main` under a __main__ guard with argparse/click.
    if func.name == "main" and has_main_guard and (imports & _CLI_IMPORTS):
        return ContextClassification("cli_batch", "main-guard+cli-import", 0.7)

    # 4. No signal — DO NOT assume http_request. Surface for review.
    return ContextClassification("unknown", "no-signal", 0.0)


def _innermost_function_containing(
    tree: ast.Module, lineno: int
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the smallest-span function whose body spans `lineno`, else None."""
    best: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    best_span = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", None) or start
            if start <= lineno <= end:
                span = end - start
                if best_span is None or span < best_span:
                    best, best_span = node, span
    return best


def classify_call_site(source: str, lineno: int) -> ContextClassification:
    """Classify the execution context enclosing the call site at `lineno`.

    The primary entry point for the W3 cost-call scanner: pass the line of a
    detected cost/usage call; get back the execution context to drive attribution
    + transport. Returns `unknown` (never http_request) when nothing matches.

    LIMITATION (by design for W2): this walks the AST, not the call graph. A cost
    call inside a helper function called from a handler resolves to the helper —
    which is `unknown`, since the helper itself carries no decorator/loop signal.
    In real codebases this means a meaningful share of sites return `unknown`.
    W3 must route `unknown` to Phase 1.6 confirmation, and may add a one-hop
    "who-imports / who-calls" propagation pass to inherit a caller's context.
    """
    tree = ast.parse(source)
    imports = _collect_imports(tree)
    has_main = _has_main_guard(tree)

    enclosing = _innermost_function_containing(tree, lineno)
    if enclosing is None:
        # Module-level call site (script body).
        if has_main and (imports & _CLI_IMPORTS):
            return ContextClassification("cli_batch", "module-main-guard", 0.6)
        return ContextClassification("unknown", "module-level", 0.0)

    return classify_function(enclosing, imports=imports, has_main_guard=has_main)


def classify_file(source: str) -> list[tuple[str, int, ContextClassification]]:
    """Classify every function in a file. Returns (name, lineno, classification)."""
    tree = ast.parse(source)
    imports = _collect_imports(tree)
    has_main = _has_main_guard(tree)
    out: list[tuple[str, int, ContextClassification]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno,
                        classify_function(node, imports=imports, has_main_guard=has_main)))
    return out
