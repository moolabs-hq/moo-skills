"""Pure CUR → Acute transform + daily aggregation. No I/O, no AWS, no HTTP.

C1 (PRD): Acute inserts rows under a partial-unique index on
(period, service_name, resource_id, region, usage_type). Duplicate grain within
a batch aborts the whole batch, so we MUST aggregate-and-sum to that grain.
C2: one batch per UTC day.
Negative cost (credits): Acute rejects cost<0 (422), so we exclude + record them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .models import CloudCostRow, ImportBatch

GrainKey = tuple[str, str, str, str]


def parse_timestamp(value: str) -> datetime:
    """Parse a CUR ISO-8601 timestamp to an aware UTC datetime."""
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def day_bounds(dt: datetime) -> tuple[datetime, datetime]:
    """UTC day window [00:00, +1d 00:00) containing ``dt``."""
    start = dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def extract_tags(raw: dict, prefix: str) -> dict:
    return {
        k[len(prefix):]: v
        for k, v in raw.items()
        if k.startswith(prefix) and v not in (None, "")
    }


def _grain_key(service: str, resource_id, region, usage_type) -> GrainKey:
    return (service, resource_id or "", region or "", usage_type or "")


def build_daily_batches(
    raw_rows,
    column_map: dict[str, str],
    *,
    reporting_currency: str = "USD",
    cloud_provider: str = "aws",
    tags_prefix: str = "resource_tags_",
) -> tuple[list[ImportBatch], list[dict]]:
    """Map raw CUR rows → daily ImportBatches (aggregated) + a credits list.

    Returns ``(batches, credits)`` where credits are the skipped cost<0 lines.
    """
    col = column_map
    # day_window -> grain_key -> mutable accumulator
    buckets: dict[tuple[datetime, datetime], dict[GrainKey, dict]] = {}
    credits: list[dict] = []

    for raw in raw_rows:
        service = str(raw.get(col["service_name"], "") or "")
        cost = Decimal(str(raw.get(col["cost"], "0") or "0"))
        resource_id = raw.get(col["resource_id"]) or None
        region = raw.get(col["region"]) or None
        usage_type = raw.get(col["usage_type"]) or None
        currency = str(raw.get(col["currency"], reporting_currency) or reporting_currency)

        if cost < 0:
            credits.append({
                "service": service,
                "resource_id": resource_id,
                "cost": str(cost),
                "currency": currency,
                "reason": "negative cost; Acute rejects cost<0",
            })
            continue

        start, end = day_bounds(parse_timestamp(raw[col["usage_start"]]))
        grain = _grain_key(service, resource_id, region, usage_type)
        day = buckets.setdefault((start, end), {})
        acc = day.get(grain)
        if acc is None:
            day[grain] = {
                "service_name": service,
                "resource_id": resource_id,
                "region": region,
                "usage_type": usage_type,
                "currency": currency,
                "cost": cost,
                "tags": extract_tags(raw, tags_prefix),
            }
        else:
            acc["cost"] += cost  # SUM to the unique-index grain (C1)

    batches: list[ImportBatch] = []
    for (start, end) in sorted(buckets):
        accs = buckets[(start, end)]
        rows = [
            CloudCostRow(
                service_name=a["service_name"],
                cost=a["cost"],
                resource_id=a["resource_id"],
                region=a["region"],
                usage_type=a["usage_type"],
                currency=a["currency"],
                tags=a["tags"],
            )
            for _, a in sorted(accs.items())
        ]
        batches.append(ImportBatch(
            cloud_provider=cloud_provider,
            billing_period_start=start,
            billing_period_end=end,
            rows=rows,
            reporting_currency=reporting_currency,
        ))
    return batches, credits
