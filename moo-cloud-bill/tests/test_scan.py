from decimal import Decimal

from moo_cloud_bill.commands.scan import find_untagged
from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP

CM = DEFAULT_COLUMN_MAP


def row(cost, *, service="AmazonBedrock", rid="r1", tags=None):
    r = {
        CM["service_name"]: service,
        CM["resource_id"]: rid,
        CM["cost"]: str(cost),
    }
    for k, v in (tags or {}).items():
        r[f"resource_tags_{k}"] = v
    return r


def test_no_monetary_threshold_tiny_finding_surfaces():
    findings = find_untagged([row(Decimal("0.50"))], CM)
    assert len(findings) == 1
    assert findings[0].monthly_cost_estimate_usd == Decimal("0.50")


def test_tagged_resource_is_not_flagged():
    findings = find_untagged([row(100, tags={"tenant": "t1"})], CM)
    assert findings == []


def test_untagged_share_and_suggestion():
    findings = find_untagged([row(1000, service="AmazonBedrock")], CM)
    assert findings[0].untagged_share_pct == 100
    assert findings[0].suggested_service_mapping == "bedrock"
    assert findings[0].severity == "high"


def test_partial_tagging_reports_residual_only():
    rows = [
        row(80, rid="r1", tags={"tenant": "t1"}),  # attributed
        row(20, rid="r1"),                          # untagged residual
    ]
    findings = find_untagged(rows, CM)
    assert len(findings) == 1
    assert findings[0].monthly_cost_estimate_usd == Decimal("20")
    assert findings[0].untagged_share_pct == 20
