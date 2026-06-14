"""Pure CUR → Acute transform + daily aggregation. No I/O, no AWS, no HTTP.

C1 (PRD): Acute inserts rows under a partial-unique index on
(period, service_name, resource_id, region, usage_type). Duplicate grain within
a batch aborts the whole batch, so we MUST aggregate-and-sum to that grain.
C2: one batch per UTC day.
Negative cost (credits): Acute rejects cost<0 (422), so we exclude + record them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .errors import ColumnMapError
from .models import CloudCostRow, ImportBatch

GrainKey = tuple[str, str, str, str]

# Logical fields the mapper cannot proceed without. A column-map that points any
# of these at a column absent from the CUR row must fail LOUDLY — silently
# defaulting `cost` to 0 would post a whole CUR as zero-cost and Acute would 201 it.
# (`currency` is intentionally NOT required: if a non-standard CUR omits it, falling
# back to the reporting currency — i.e. "no FX" — is a sensible, non-corrupting default.)
REQUIRED_COLUMNS = ("service_name", "cost", "usage_start")


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
    # Matches Acute's partial-unique index EXACTLY (tenant+period+service+
    # COALESCE(resource_id,'')+COALESCE(region,'')+COALESCE(usage_type,'')).
    # Currency is NOT in the index, so it must NOT be in this key — else a
    # same-resource mixed-currency pair becomes a dup-grain row that aborts the
    # whole daily batch. Mixed currency on one grain is handled as an anomaly
    # in build_daily_batches (skipped + recorded), never summed.
    return (service, resource_id or "", region or "", usage_type or "")


def _require_columns(row: dict, col: dict) -> None:
    missing = [f for f in REQUIRED_COLUMNS if col[f] not in row]
    if missing:
        cols = ", ".join(f"{f}→{col[f]}" for f in missing)
        raise ColumnMapError(
            f"CUR column map points required field(s) at absent column(s): {cols}. "
            f"Present columns: {list(row)[:12]}. Fix cur-column-map.yaml."
        )


def parse_cost(raw_value, col_name: str) -> Decimal:
    # Null cost cell (pyarrow yields None) → zero; non-numeric → loud map error.
    if raw_value is None:
        return Decimal(0)
    try:
        return Decimal(str(raw_value))
    except InvalidOperation as exc:
        raise ColumnMapError(
            f"cost column '{col_name}' has non-numeric value {raw_value!r} — "
            f"the column map likely points 'cost' at the wrong column."
        ) from exc


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

    validated = False
    for raw in raw_rows:
        if not validated:
            _require_columns(raw, col)  # fail loudly on a misconfigured column map
            validated = True

        service = str(raw[col["service_name"]] or "")
        cost = parse_cost(raw[col["cost"]], col["cost"])
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
        elif acc["currency"] != currency:
            # Same Acute grain, different currency — Acute can't store both (currency
            # isn't in its index) and summing across currencies is meaningless. Keep
            # the LARGER cost (so we never drop the bigger spend regardless of row
            # order); record the smaller as a currency_conflict. Near-impossible in
            # real CUR (a resource bills in one currency).
            if cost > acc["cost"]:
                loser_cost, loser_ccy, kept_ccy = acc["cost"], acc["currency"], currency
                # Promote the new row fully — its currency AND its tags win together.
                acc["cost"], acc["currency"] = cost, currency
                acc["tags"] = extract_tags(raw, tags_prefix)
            else:
                loser_cost, loser_ccy, kept_ccy = cost, currency, acc["currency"]
            credits.append({
                "service": service,
                "resource_id": resource_id,
                "cost": str(loser_cost),
                "currency": loser_ccy,
                "reason": f"currency_conflict: kept {kept_ccy}, dropped {loser_ccy}",
            })
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
