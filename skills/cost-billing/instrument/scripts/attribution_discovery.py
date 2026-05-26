"""attribution_discovery.py — Phase 1.6 of /cost-billing-instrument.

Discover where the customer's code keeps the attribution variables the
templates need (request_id, customer_id, consumer_agent),
present proposals to the developer ONE KEY AT A TIME, persist confirmed
choices as `.moolabs/customer-context/attribution-bindings.yaml`.

The codemod's templates DO NOT hardcode framework conventions. They
substitute `{{ attribution_sources.X }}` per-file using the bindings this
script produces. No bindings → codemod refuses to run (fail loud).

Usage:
    python attribution_discovery.py \
        --service-root services/billing-api \
        --framework fastapi \
        --customer-context-dir .moolabs/customer-context \
        [--reconfirm]            # re-prompt even for previously confirmed keys
        [--non-interactive]      # take highest-confidence proposal; print decisions

This script SCANS — it does not import or run customer code. The developer
makes every final call.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# What we need bindings for. The codemod templates reference exactly these keys.
ATTRIBUTION_KEYS = ["request_id", "customer_id", "consumer_agent"]

# Confidence ranking — used to pick a default when --non-interactive.
_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "n_a": 0}


@dataclass
class Proposal:
    source: str
    confidence: str  # high | medium | low
    evidence: list[str] = field(default_factory=list)
    # Free-form note shown to the developer alongside the proposal.
    note: str = ""


@dataclass
class Binding:
    source: str | None  # None = developer says "not available; skip"
    confidence: str
    evidence: list[str] = field(default_factory=list)
    fallback_when_absent: str = "skip"  # skip | error
    confirmed_by: str = ""
    confirmed_at: str = ""


# ─── Per-framework scanners ────────────────────────────────────────────────

_PY_REQUEST_STATE = re.compile(r"request\.state\.(\w+)\s*=")
_PY_REQUEST_SCOPE = re.compile(r"request\.scope\[\s*['\"](\w+)['\"]\s*\]\s*=")
_PY_FLASK_G = re.compile(r"\bg\.(\w+)\s*=")
_PY_DJANGO_REQUEST_ATTR = re.compile(r"\brequest\.(\w+)\s*=(?!=)")
_PY_DJANGO_META = re.compile(r"request\.META\[\s*['\"]([\w_]+)['\"]\s*\]")
_TS_REQ_DOT_ASSIGN = re.compile(r"(?:request|req)\.(\w+)\s*=(?!=)")
_TS_REQ_HEADER = re.compile(r"(?:request|req)\.headers\[?[\.'\"]?([a-z0-9-]+)[\"'\]]?")
_TS_HEADERS_GET = re.compile(r"\.headers\.get\(\s*['\"]([a-z0-9-]+)['\"]\s*\)")


def _scan_python_fastapi(service_root: Path) -> dict[str, list[Proposal]]:
    """Look for FastAPI middleware setting request.state.X / request.scope[X]."""
    proposals: dict[str, list[Proposal]] = {k: [] for k in ATTRIBUTION_KEYS}
    for py in service_root.rglob("*.py"):
        if "__pycache__" in py.parts or "/tests/" in str(py):
            continue
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _PY_REQUEST_STATE.finditer(line):
                attr = m.group(1)
                _maybe_add(
                    proposals, attr,
                    Proposal(
                        source=f"request.state.{attr}",
                        confidence="high",
                        evidence=[f"{py.relative_to(service_root)}:{lineno}"],
                    ),
                )
            for m in _PY_REQUEST_SCOPE.finditer(line):
                key = m.group(1)
                _maybe_add(
                    proposals, key,
                    Proposal(
                        source=f"request.scope['{key}']",
                        confidence="medium",
                        evidence=[f"{py.relative_to(service_root)}:{lineno}"],
                        note="scope[] access is unusual for FastAPI; verify middleware really sets it here.",
                    ),
                )
    return proposals


def _scan_python_flask(service_root: Path) -> dict[str, list[Proposal]]:
    proposals: dict[str, list[Proposal]] = {k: [] for k in ATTRIBUTION_KEYS}
    for py in service_root.rglob("*.py"):
        if "__pycache__" in py.parts or "/tests/" in str(py):
            continue
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if "from flask import" not in text and "import flask" not in text:
                continue
            for m in _PY_FLASK_G.finditer(line):
                attr = m.group(1)
                _maybe_add(
                    proposals, attr,
                    Proposal(
                        source=f"flask.g.{attr}",
                        confidence="high",
                        evidence=[f"{py.relative_to(service_root)}:{lineno}"],
                    ),
                )
    return proposals


def _scan_python_django(service_root: Path) -> dict[str, list[Proposal]]:
    proposals: dict[str, list[Proposal]] = {k: [] for k in ATTRIBUTION_KEYS}
    for py in service_root.rglob("*.py"):
        if "__pycache__" in py.parts or "/tests/" in str(py):
            continue
        try:
            text = py.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _PY_DJANGO_REQUEST_ATTR.finditer(line):
                attr = m.group(1)
                if attr in ("META", "GET", "POST", "user", "session", "path", "method", "body"):
                    continue
                _maybe_add(
                    proposals, attr,
                    Proposal(
                        source=f"getattr(request, '{attr}', None)",
                        confidence="medium",
                        evidence=[f"{py.relative_to(service_root)}:{lineno}"],
                        note="Django middleware setting request.X directly — confirm getattr guard is OK.",
                    ),
                )
            for m in _PY_DJANGO_META.finditer(line):
                header = m.group(1)
                if header.startswith("HTTP_"):
                    semantic = header.replace("HTTP_", "").replace("_", "-").lower()
                    _maybe_add(
                        proposals, _meta_header_to_key(semantic),
                        Proposal(
                            source=f'request.META.get("{header}")',
                            confidence="medium",
                            evidence=[f"{py.relative_to(service_root)}:{lineno}"],
                        ),
                    )
    return proposals


def _scan_typescript(service_root: Path, flavor: str) -> dict[str, list[Proposal]]:
    """flavor: express | nestjs | nextjs"""
    proposals: dict[str, list[Proposal]] = {k: [] for k in ATTRIBUTION_KEYS}
    exts = ("*.ts", "*.tsx", "*.js")
    files: list[Path] = []
    for pat in exts:
        files.extend(service_root.rglob(pat))
    for ts in files:
        if "node_modules" in ts.parts or "/dist/" in str(ts) or "/__tests__/" in str(ts):
            continue
        try:
            text = ts.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in _TS_REQ_DOT_ASSIGN.finditer(line):
                attr = m.group(1)
                if attr in ("body", "params", "query", "headers", "method", "url", "ip", "user"):
                    continue
                _maybe_add(
                    proposals, attr,
                    Proposal(
                        source=(
                            f"(req as any).{attr}" if flavor == "express"
                            else f"(request as any).{attr}"
                        ),
                        confidence="high",
                        evidence=[f"{ts.relative_to(service_root)}:{lineno}"],
                    ),
                )
            for m in _TS_HEADERS_GET.finditer(line):
                header = m.group(1)
                if not header.startswith("x-"):
                    continue
                if flavor == "nextjs":
                    expr = f"request.headers.get('{header}') ?? ''"
                elif flavor == "express":
                    expr = f"String(req.headers['{header}'] ?? '')"
                else:
                    expr = f"String(request.headers['{header}'] ?? '')"
                _maybe_add(
                    proposals, _header_to_key(header),
                    Proposal(
                        source=expr,
                        confidence="medium",
                        evidence=[f"{ts.relative_to(service_root)}:{lineno}"],
                    ),
                )
    return proposals


def _meta_header_to_key(header_lc: str) -> str:
    if header_lc in ("x-request-id", "request-id"):
        return "request_id"
    if header_lc in (
        "x-customer-id", "customer-id",
        "x-tenant-id", "tenant-id", "x-org-id", "x-organization-id",
    ):
        return "customer_id"
    return header_lc.replace("-", "_")


def _header_to_key(header: str) -> str:
    return _meta_header_to_key(header.lower())


def _maybe_add(proposals: dict[str, list[Proposal]], attr: str, proposal: Proposal) -> None:
    """Add the proposal to the right attribution bucket (if it matches a known key).

    Matches are name-based: 'tenant_id', 'tenantId', 'org_id' → customer_id, etc.
    """
    normalized = _normalize_key(attr)
    if normalized in ATTRIBUTION_KEYS:
        # de-dupe: same source already proposed
        if any(p.source == proposal.source for p in proposals[normalized]):
            existing = next(p for p in proposals[normalized] if p.source == proposal.source)
            existing.evidence.extend(proposal.evidence)
            existing.evidence = sorted(set(existing.evidence))[:5]
            return
        proposals[normalized].append(proposal)


def _normalize_key(attr: str) -> str:
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", attr).lower()
    if snake in ("request_id", "req_id", "correlation_id"):
        return "request_id"
    if snake in ("customer_id", "user_id", "account_id", "tenant_id", "org_id", "organization_id", "workspace_id"):
        return "customer_id"
    if snake in ("agent", "agent_name", "consumer_agent", "executor"):
        return "consumer_agent"
    return snake


# ─── Interactive prompt ─────────────────────────────────────────────────────

def _prompt(key: str, proposals: list[Proposal], current: Binding | None, non_interactive: bool) -> Binding:
    """Ask the developer where to source `key`. Returns confirmed Binding."""
    confirmed_by = _current_user()
    confirmed_at = datetime.now(timezone.utc).isoformat()

    print()
    print("─" * 70)
    print(f"  Attribution key:  {key}")
    print("─" * 70)

    if current is not None and current.confirmed_at:
        print(f"  Already confirmed by {current.confirmed_by} at {current.confirmed_at}")
        if not non_interactive:
            ans = input("  Keep existing? [Y/n/edit] ").strip().lower()
            if ans in ("", "y", "yes"):
                return current

    if not proposals:
        print(f"  No candidates detected for {key} by static scan.")
        if non_interactive:
            print("  Marking source=null (codemod will skip this attribute everywhere).")
            return Binding(source=None, confidence="n_a", confirmed_by=confirmed_by, confirmed_at=confirmed_at)
        custom = input("  Enter source expression or leave blank to skip: ").strip()
        if not custom:
            return Binding(source=None, confidence="n_a", confirmed_by=confirmed_by, confirmed_at=confirmed_at)
        return Binding(source=custom, confidence="confirmed", confirmed_by=confirmed_by, confirmed_at=confirmed_at)

    proposals_sorted = sorted(proposals, key=lambda p: _CONFIDENCE_RANK.get(p.confidence, 0), reverse=True)
    for idx, p in enumerate(proposals_sorted, start=1):
        print(f"  [{idx}] {p.source}  (confidence={p.confidence})")
        for ev in p.evidence[:3]:
            print(f"        evidence: {ev}")
        if p.note:
            print(f"        note: {p.note}")

    if non_interactive:
        chosen = proposals_sorted[0]
        print(f"  → non-interactive: picked [1] {chosen.source}")
        return Binding(
            source=chosen.source,
            confidence=chosen.confidence,
            evidence=chosen.evidence,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
        )

    print(f"  [c] custom expression")
    print(f"  [s] mark source=null (skip {key} attribute everywhere)")
    ans = input(f"  Pick [1-{len(proposals_sorted)}/c/s] (default=1): ").strip().lower()
    if not ans:
        ans = "1"
    if ans == "c":
        custom = input("  Enter custom source expression: ").strip()
        return Binding(source=custom or None, confidence="confirmed", confirmed_by=confirmed_by, confirmed_at=confirmed_at)
    if ans == "s":
        return Binding(source=None, confidence="n_a", confirmed_by=confirmed_by, confirmed_at=confirmed_at)
    try:
        choice = proposals_sorted[int(ans) - 1]
    except (ValueError, IndexError):
        print("  Invalid; defaulting to [1].")
        choice = proposals_sorted[0]
    return Binding(
        source=choice.source,
        confidence=choice.confidence,
        evidence=choice.evidence,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )


def _current_user() -> str:
    import os
    return os.environ.get("MOOLABS_OPERATOR") or os.environ.get("USER") or "unknown@localhost"


# ─── YAML I/O (avoid PyYAML dep where possible) ────────────────────────────

def _emit_yaml(service_slug: str, framework: str, bindings: dict[str, Binding], overrides: list[dict[str, Any]], dest: Path) -> None:
    lines: list[str] = []
    lines.append(f"service_slug: {service_slug}")
    lines.append(f"framework: {framework}")
    lines.append(f"generated_at: {datetime.now(timezone.utc).isoformat()}")
    lines.append("bindings:")
    for key in ATTRIBUTION_KEYS:
        b = bindings.get(key)
        lines.append(f"  {key}:")
        if b is None or b.source is None:
            lines.append(f"    source: null")
            lines.append(f"    confidence: n_a")
            if b:
                lines.append(f"    confirmed_by: {b.confirmed_by}")
                lines.append(f"    confirmed_at: {b.confirmed_at}")
            continue
        lines.append(f'    source: "{b.source}"')
        lines.append(f"    confidence: {b.confidence}")
        if b.evidence:
            lines.append(f"    evidence: [{', '.join(repr(e) for e in b.evidence[:5])}]")
        lines.append(f"    fallback_when_absent: {b.fallback_when_absent}")
        lines.append(f"    confirmed_by: {b.confirmed_by}")
        lines.append(f"    confirmed_at: {b.confirmed_at}")
    if overrides:
        lines.append("overrides:")
        for ov in overrides:
            lines.append(f'  - file: {ov["file"]}')
            lines.append(f'    reason: {ov["reason"]!r}')
            lines.append("    bindings:")
            for k, v in ov.get("bindings", {}).items():
                lines.append(f"      {k}:")
                lines.append(f'        source: "{v["source"]}"')
                lines.append(f"        confidence: {v.get('confidence', 'confirmed')}")
    dest.write_text("\n".join(lines) + "\n")


def _load_existing(path: Path) -> dict[str, Binding]:
    if not path.exists():
        return {}
    try:
        import yaml
        data = yaml.safe_load(path.read_text())
    except ImportError:
        # naive fallback — sufficient for our shape
        data = _naive_yaml_load(path.read_text())
    out: dict[str, Binding] = {}
    for k, v in (data or {}).get("bindings", {}).items():
        if not isinstance(v, dict):
            continue
        out[k] = Binding(
            source=v.get("source"),
            confidence=v.get("confidence", "n_a"),
            evidence=v.get("evidence", []) or [],
            fallback_when_absent=v.get("fallback_when_absent", "skip"),
            confirmed_by=v.get("confirmed_by", ""),
            confirmed_at=v.get("confirmed_at", ""),
        )
    return out


def _naive_yaml_load(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"bindings": {}}
    current_key: str | None = None
    in_bindings = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw.startswith("bindings:"):
            in_bindings = True
            continue
        if not raw.startswith(" ") and in_bindings:
            in_bindings = False
        if not in_bindings:
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 2 and stripped.endswith(":"):
            current_key = stripped[:-1]
            out["bindings"][current_key] = {}
        elif indent >= 4 and current_key and ":" in stripped:
            k, _, v = stripped.partition(":")
            out["bindings"][current_key][k.strip()] = v.strip().strip('"').strip("'") or None
    return out


# ─── Entry point ────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service-root", required=True, help="Path to the service to scan, e.g. services/billing-api")
    ap.add_argument("--framework", required=True, choices=["fastapi", "django", "flask", "express", "nestjs", "nextjs"])
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    ap.add_argument("--reconfirm", action="store_true", help="re-prompt for previously confirmed keys")
    ap.add_argument("--non-interactive", action="store_true", help="auto-pick highest-confidence proposal")
    ap.add_argument("--service-slug", default=None, help="override the service slug used in the YAML")
    args = ap.parse_args(argv)

    service_root = Path(args.service_root).resolve()
    if not service_root.is_dir():
        sys.stderr.write(f"--service-root {service_root} is not a directory\n")
        return 2

    service_slug = args.service_slug or service_root.name

    scanners = {
        "fastapi": _scan_python_fastapi,
        "flask": _scan_python_flask,
        "django": _scan_python_django,
        "express": lambda root: _scan_typescript(root, "express"),
        "nestjs": lambda root: _scan_typescript(root, "nestjs"),
        "nextjs": lambda root: _scan_typescript(root, "nextjs"),
    }
    proposals = scanners[args.framework](service_root)

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bindings_path = out_dir / "attribution-bindings.yaml"
    existing = {} if args.reconfirm else _load_existing(bindings_path)

    bindings: dict[str, Binding] = {}
    print(f"Phase 1.6: attribution discovery for {service_slug} (framework={args.framework})")
    for key in ATTRIBUTION_KEYS:
        current = existing.get(key)
        if current and current.source is not None and not args.reconfirm and not args.non_interactive:
            print(f"  [keep] {key} = {current.source}  (confirmed by {current.confirmed_by})")
            bindings[key] = current
            continue
        bindings[key] = _prompt(key, proposals[key], current, args.non_interactive)

    overrides: list[dict[str, Any]] = []  # populated by --override flag in v2
    _emit_yaml(service_slug, args.framework, bindings, overrides, bindings_path)
    print()
    print(f"Wrote {bindings_path}")
    print("Phase 2c (task_planner.py) will refuse to run if any key the templates need is not confirmed here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
