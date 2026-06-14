"""Domain models. Bodies map exactly to Acute's contract (verified against
moo-acute/app/api/v1/cloud_billing/router.py).

Acute derives the tenant from the Bearer key, so ``tenant_id`` is NEVER in any
body these models produce.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CloudCostRow:
    """One aggregated cost row → Acute ``CloudCostRowInput``.

    ``cost`` is Decimal and MUST be >= 0 (Acute rejects negatives with 422).
    """

    service_name: str
    cost: Decimal
    resource_id: str | None = None
    region: str | None = None
    usage_type: str | None = None
    currency: str = "USD"
    tags: dict = field(default_factory=dict)

    def to_body(self) -> dict:
        return {
            "service_name": self.service_name,
            "resource_id": self.resource_id,
            "region": self.region,
            "usage_type": self.usage_type,
            # Fixed-point, never scientific notation: format(Decimal("5E-7"),"f")
            # == "0.0000005". Keeps precision and avoids any strict server-side
            # Decimal parser choking on "5E-7".
            "cost": format(self.cost, "f"),
            "currency": self.currency,
            "tags": dict(self.tags),
        }


@dataclass(frozen=True)
class ImportBatch:
    """One ``ImportBatchRequest`` for a single (provider, period). We use daily
    periods: ``billing_period_start``/``end`` are a UTC day ``[00:00, +1d 00:00)``.
    """

    cloud_provider: str
    billing_period_start: datetime
    billing_period_end: datetime
    rows: list[CloudCostRow]
    reporting_currency: str = "USD"

    def to_body(self) -> dict:
        return {
            "cloud_provider": self.cloud_provider,
            "billing_period_start": self.billing_period_start.isoformat(),
            "billing_period_end": self.billing_period_end.isoformat(),
            "reporting_currency": self.reporting_currency,
            "rows": [r.to_body() for r in self.rows],
        }


@dataclass
class Finding:
    """An untagged-spend finding. ``scan`` emits evidence + defaults; a human sets
    ``decision``/``approved``; ``seed`` consumes approved ``map`` rows.
    """

    resource_id: str
    service: str
    monthly_cost_estimate_usd: Decimal
    untagged_share_pct: int
    primary_pattern: str
    severity: str
    suggested_service_mapping: str | None = None
    decision: str = "map"  # map | absorb | ignore
    approved: bool = False
    service_name: str | None = None
    team_name: str | None = None
    environment: str | None = None
    resource_type: str | None = None
    tags: dict = field(default_factory=dict)

    def is_seedable(self) -> bool:
        return self.approved and self.decision == "map" and bool(self.service_name)

    def to_resource_map_body(self) -> dict:
        return {
            "cloud_provider": "aws",
            "resource_id": self.resource_id,
            "resource_type": self.resource_type,
            "service_name": self.service_name,
            "team_name": self.team_name,
            "environment": self.environment,
            "tags": dict(self.tags),
        }
