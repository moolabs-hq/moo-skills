from decimal import Decimal

from moo_cloud_bill.findings import load_findings, save_findings
from moo_cloud_bill.models import Finding


def test_findings_round_trip(tmp_path):
    f = Finding(
        resource_id="arn:bedrock:abc",
        service="AmazonBedrock",
        monthly_cost_estimate_usd=Decimal("8920.10"),
        untagged_share_pct=100,
        primary_pattern="no tag",
        severity="critical",
        suggested_service_mapping="bedrock",
        decision="map",
        approved=True,
        service_name="ai-chat",
    )
    path = tmp_path / "untagged-findings.yaml"
    save_findings([f], path, billing_period="2026-05")
    loaded = load_findings(path)
    assert len(loaded) == 1
    g = loaded[0]
    assert g.resource_id == "arn:bedrock:abc"
    assert g.monthly_cost_estimate_usd == Decimal("8920.10")
    assert g.approved is True
    assert g.service_name == "ai-chat"
    assert g.is_seedable()
