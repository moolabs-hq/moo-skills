from decimal import Decimal

from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP
from moo_cloud_bill.mapper import build_daily_batches

CM = DEFAULT_COLUMN_MAP


def row(cost, *, service="AmazonBedrock", rid="r1", region="us-east-1",
        usage="Invoke", currency="USD", start="2026-05-14T03:00:00Z", tags=None):
    r = {
        CM["service_name"]: service,
        CM["resource_id"]: rid,
        CM["region"]: region,
        CM["usage_type"]: usage,
        CM["cost"]: str(cost),
        CM["currency"]: currency,
        CM["usage_start"]: start,
    }
    for k, v in (tags or {}).items():
        r[f"resource_tags_{k}"] = v
    return r


def test_hourly_lines_aggregate_to_one_daily_row():
    rows = [
        row(1.50, start="2026-05-14T03:00:00Z"),
        row(2.25, start="2026-05-14T09:00:00Z"),
    ]
    batches, credits = build_daily_batches(rows, CM)
    assert len(batches) == 1
    assert len(batches[0].rows) == 1, "same grain must collapse (C1)"
    assert batches[0].rows[0].cost == Decimal("3.75")
    assert not credits


def test_negative_cost_excluded_and_recorded():
    batches, credits = build_daily_batches([row("-5.00")], CM)
    assert batches == []
    assert len(credits) == 1
    assert Decimal(credits[0]["cost"]) == Decimal("-5")


def test_distinct_days_become_distinct_batches():
    rows = [row(1, start="2026-05-14T01:00:00Z"), row(1, start="2026-05-15T01:00:00Z")]
    batches, _ = build_daily_batches(rows, CM)
    assert len(batches) == 2
    assert batches[0].billing_period_start.date().isoformat() == "2026-05-14"


def test_grain_splits_on_usage_type():
    rows = [row(1, usage="A"), row(1, usage="B")]
    batches, _ = build_daily_batches(rows, CM)
    assert len(batches) == 1
    assert len(batches[0].rows) == 2


def test_bedrock_service_name_is_exact_not_substring():
    batches, _ = build_daily_batches([row(1, service="AmazonBedrock")], CM)
    assert batches[0].rows[0].service_name == "AmazonBedrock"


def test_tags_and_currency_carried():
    batches, _ = build_daily_batches(
        [row(1, currency="EUR", tags={"user_tenant": "t1"})], CM, reporting_currency="EUR"
    )
    r = batches[0].rows[0]
    assert r.currency == "EUR"
    assert r.tags["user_tenant"] == "t1"


def test_daily_period_bounds_are_half_open_utc_day():
    batches, _ = build_daily_batches([row(1, start="2026-05-14T23:30:00Z")], CM)
    b = batches[0]
    assert b.billing_period_start.isoformat() == "2026-05-14T00:00:00+00:00"
    assert b.billing_period_end.isoformat() == "2026-05-15T00:00:00+00:00"
