"""`scan` — surface untagged/unattributable spend as findings. No monetary
threshold: every untagged resource surfaces (PRD FR-8).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from ..errors import ColumnMapError
from ..findings import save_findings
from ..mapper import extract_tags
from ..models import Finding

# Tags that, if present and non-empty, mean a line IS attributable.
DEFAULT_ATTRIBUTION_TAGS = (
    "tenant", "tenant_id", "feature", "product", "team", "environment", "service",
)


def _severity(cost: Decimal) -> str:
    if cost >= 5000:
        return "critical"
    if cost >= 1000:
        return "high"
    if cost >= 100:
        return "medium"
    return "low"


def _suggest(service: str) -> str:
    s = service.strip()
    for prefix in ("Amazon", "AWS"):
        if s.startswith(prefix):
            s = s[len(prefix):]
    return s.lower() or "unknown"


def find_untagged(
    raw_rows,
    column_map: dict[str, str],
    *,
    attribution_tags=DEFAULT_ATTRIBUTION_TAGS,
    tags_prefix: str = "resource_tags_",
) -> list[Finding]:
    col = column_map
    agg: dict[tuple[str, str | None], dict] = {}
    validated = False
    for raw in raw_rows:
        if not validated:
            # Same root cause as mapper: a misconfigured cost column would
            # silently zero every line and report no findings. Fail loudly.
            for field in ("service_name", "cost"):
                if col[field] not in raw:
                    raise ColumnMapError(
                        f"scan: column map field '{field}'→'{col[field]}' absent from CUR row"
                    )
            validated = True
        cost = Decimal(str(raw[col["cost"]]))
        if cost <= 0:
            continue
        service = str(raw[col["service_name"]] or "")
        resource_id = raw.get(col["resource_id"]) or None
        tags = extract_tags(raw, tags_prefix)
        attributed = any(tags.get(t) for t in attribution_tags)
        a = agg.setdefault((service, resource_id), {"total": Decimal(0), "untagged": Decimal(0)})
        a["total"] += cost
        if not attributed:
            a["untagged"] += cost

    findings: list[Finding] = []
    for (service, resource_id), a in agg.items():
        if a["untagged"] <= 0:
            continue
        share = int((a["untagged"] / a["total"] * 100).to_integral_value()) if a["total"] else 0
        findings.append(Finding(
            resource_id=resource_id or "",
            service=service,
            monthly_cost_estimate_usd=a["untagged"],
            untagged_share_pct=share,
            primary_pattern=f"{service} spend with no attribution tag",
            severity=_severity(a["untagged"]),
            suggested_service_mapping=_suggest(service),
        ))
    findings.sort(key=lambda f: f.monthly_cost_estimate_usd, reverse=True)
    return findings


def run_scan(
    raw_rows,
    column_map: dict[str, str],
    out_path: Path,
    *,
    billing_period: str | None = None,
    generated_at: str | None = None,
    out=print,
) -> int:
    findings = find_untagged(raw_rows, column_map)
    save_findings(findings, Path(out_path), billing_period=billing_period, generated_at=generated_at)
    out(f"Wrote {len(findings)} finding(s) to {out_path}. Edit decisions, then run `seed`.")
    return 0
