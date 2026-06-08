#!/usr/bin/env python3
"""catalog_match.py — W3 of worker-coverage-design.md (Python).

The cost-call-anchored scanner: find vendor cost/usage call sites by matching
provider-catalog patterns in the AST, then classify the EXECUTION CONTEXT of each
hit via the W2 classifier. This is what makes workers visible — a cost call inside
a Celery task is found the same way as one in an HTTP handler, and tagged
`queue_worker` instead of being missed.

Match rule (precision-first):
  A call matches a catalog operation iff
    (a) the vendor's SDK package is imported in the file (corroboration), AND
    (b) the call's dotted attribute path ends with the operation pattern's last
        K=min(2, len(tail)) attribute segments (args stripped).
  Requiring the import gate keeps precision high — a stray `.create()` won't match
  OpenAI unless `openai` is imported. Recall varies by vendor (boto3/sendgrid
  instance-variable patterns are lower-recall by design; see KNOWN LIMITATIONS).

For every match we attach the execution_context. `unknown` (or low confidence)
sets `needs_confirmation=True` so the pipeline routes it to Phase 1.6 instead of
guessing http_request.

KNOWN LIMITATIONS (deferred per design):
  - boto3/bedrock & sendgrid: `client = boto3.client('s3'); client.put_object()`
    loses the vendor in a local var — matched only when the var chain aligns.
  - TS / Go scanners: Python first; not implemented here.
  - One-hop call-graph propagation for `unknown` helper sites: W3 emits
    needs_confirmation; the propagation pass is a later refinement.
  - Two vendors sharing a method suffix (e.g. anthropic & twilio `messages.create`)
    both imported in one file would double-match — rare; flagged in output.

Usage:
    python catalog_match.py <repo_or_file> --catalog <provider-catalog.yaml>
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from context_classifier import classify_call_site  # noqa: E402

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

# Confidence below which an otherwise-classified site is still flagged for review.
_CONTEXT_REVIEW_THRESHOLD = 0.6

_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    # `.direnv` (Nix/direnv) holds store artifacts incl. DIRECTORIES named like
    # `*.py`; `.terraform` holds vendored providers. Walking them crashed the
    # scanner on a real monorepo (dogfood 2026-06-08, finding C).
    ".direnv", ".terraform", ".tox", ".mypy_cache",
}


@dataclass(frozen=True)
class _Operation:
    vendor: str
    op_id: str
    cost_dimension: str
    tail: tuple[str, ...]          # attribute segments (args stripped)
    sdk_packages: tuple[str, ...]  # python SDK package top-names


@dataclass
class CostCallSite:
    file: str
    line: int
    vendor: str
    operation: str
    cost_dimension: str
    call_path: str
    execution_context: str
    execution_context_confidence: float
    context_signal: str
    needs_confirmation: bool
    confirmation_reason: str = ""


# ── Catalog → operation matchers ─────────────────────────────────────────────

def _pattern_tail(pattern: str) -> tuple[str, ...]:
    """Strip embedded call args, split on '.', return attribute segments."""
    stripped = re.sub(r"\([^)]*\)", "", pattern)
    return tuple(seg for seg in stripped.split(".") if seg)


def load_operations(catalog: dict) -> list[_Operation]:
    """Flatten the provider catalog into Python-matchable operations."""
    ops: list[_Operation] = []
    for vendor, vdata in (catalog.get("vendors") or {}).items():
        sdk_pkgs = tuple(
            p.split("/")[0].split("[")[0]
            for p in ((vdata.get("sdk_packages") or {}).get("python") or [])
        )
        for op in (vdata.get("operations") or []):
            py_pattern = (op.get("patterns") or {}).get("python")
            if not py_pattern:
                continue
            tail = _pattern_tail(py_pattern)
            if not tail:
                continue
            ops.append(_Operation(
                vendor=vendor,
                op_id=op.get("id", py_pattern),
                cost_dimension=op.get("cost_dimension", "other"),
                tail=tail,
                sdk_packages=sdk_pkgs,
            ))
    return ops


# ── AST helpers ──────────────────────────────────────────────────────────────

def _dotted(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Call):
        return _dotted(node.func)
    return None


def _file_imports(tree: ast.Module) -> frozenset[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return frozenset(names)


def _suffix_matches(call_segs: list[str], tail: tuple[str, ...]) -> bool:
    k = min(2, len(tail))
    needle = tail[-k:]
    return tuple(call_segs[-k:]) == tuple(needle) if len(call_segs) >= k else False


# ── Scan ─────────────────────────────────────────────────────────────────────

def scan_source(source: str, ops: list[_Operation], rel_path: str) -> list[CostCallSite]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    imports = _file_imports(tree)
    sites: list[CostCallSite] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        path = _dotted(node.func)
        if not path:
            continue
        call_segs = path.split(".")
        for op in ops:
            # (a) vendor import gate
            if op.sdk_packages and not (imports & set(op.sdk_packages)):
                continue
            # (b) suffix match
            if not _suffix_matches(call_segs, op.tail):
                continue

            cls = classify_call_site(source, node.lineno)
            needs = cls.context == "unknown" or cls.confidence < _CONTEXT_REVIEW_THRESHOLD
            reason = ""
            if cls.context == "unknown":
                reason = "execution context unclassified — confirm in Phase 1.6"
            elif needs:
                reason = f"low context confidence ({cls.confidence:.2f}); confirm context={cls.context}"

            sites.append(CostCallSite(
                file=rel_path,
                line=node.lineno,
                vendor=op.vendor,
                operation=op.op_id,
                cost_dimension=op.cost_dimension,
                call_path=path,
                execution_context=cls.context,
                execution_context_confidence=cls.confidence,
                context_signal=cls.signal,
                needs_confirmation=needs,
                confirmation_reason=reason,
            ))
            break  # first matching op wins per call node
    return sites


def scan_repo(root: Path, ops: list[_Operation]) -> list[CostCallSite]:
    sites: list[CostCallSite] = []
    paths = [root] if root.is_file() else root.rglob("*.py")
    for py in paths:
        if py.suffix != ".py":
            continue
        if any(part in _IGNORE_DIRS for part in py.parts):
            continue
        # Robust guard against the root cause class: a DIRECTORY whose name ends
        # in `.py` (Nix store artifacts, etc.) would otherwise crash read_text
        # with IsADirectoryError (dogfood 2026-06-08, finding C). Belt-and-braces
        # with the skip-set above — covers any future ignored-dir we miss.
        if not py.is_file():
            continue
        try:
            rel = str(py.relative_to(root)) if root.is_dir() else py.name
        except ValueError:
            rel = str(py)
        sites.extend(scan_source(py.read_text(encoding="utf-8", errors="replace"), ops, rel))
    return sites


def main() -> int:  # pragma: no cover
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", help="Repo or single .py file to scan")
    ap.add_argument("--catalog", required=True, help="provider-catalog.yaml")
    args = ap.parse_args()
    if yaml is None:
        print("PyYAML required", file=sys.stderr)
        return 2
    catalog = yaml.safe_load(Path(args.catalog).read_text(encoding="utf-8"))
    ops = load_operations(catalog)
    sites = scan_repo(Path(args.path), ops)
    print(json.dumps([asdict(s) for s in sites], indent=2))
    n_conf = sum(1 for s in sites if s.needs_confirmation)
    print(f"\n{len(sites)} cost-call site(s); {n_conf} need Phase-1.6 context confirmation",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
