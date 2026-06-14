"""Read/write ``untagged-findings.yaml`` — the persistent, reviewable source of
truth for the absorb-vs-fix decision (PRD US-004).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from .models import Finding

_FIELDS = (
    "resource_id", "service", "monthly_cost_estimate_usd", "untagged_share_pct",
    "primary_pattern", "severity", "suggested_service_mapping", "decision",
    "approved", "service_name", "team_name", "environment", "resource_type", "tags",
)


def _to_dict(f: Finding) -> dict:
    d = {k: getattr(f, k) for k in _FIELDS}
    # str(), not float(): keep the Decimal exact in YAML (float() would round-trip
    # through IEEE-754). _from_dict reads it back via Decimal(str(...)).
    d["monthly_cost_estimate_usd"] = str(f.monthly_cost_estimate_usd)
    return d


def _from_dict(d: dict) -> Finding:
    return Finding(
        resource_id=d["resource_id"],
        service=d.get("service", ""),
        monthly_cost_estimate_usd=Decimal(str(d.get("monthly_cost_estimate_usd", "0"))),
        untagged_share_pct=int(d.get("untagged_share_pct", 0)),
        primary_pattern=d.get("primary_pattern", ""),
        severity=d.get("severity", "low"),
        suggested_service_mapping=d.get("suggested_service_mapping"),
        decision=d.get("decision", "map"),
        approved=bool(d.get("approved", False)),
        service_name=d.get("service_name"),
        team_name=d.get("team_name"),
        environment=d.get("environment"),
        resource_type=d.get("resource_type"),
        tags=d.get("tags") or {},
    )


def save_findings(
    findings: list[Finding],
    path: Path,
    *,
    billing_period: str | None = None,
    generated_at: str | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "generated_at": generated_at,
        "billing_period": billing_period,
        "findings": [_to_dict(f) for f in findings],
    }
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return path


def load_findings(path: Path) -> list[Finding]:
    data = yaml.safe_load(Path(path).read_text()) or {}
    return [_from_dict(d) for d in data.get("findings", [])]
