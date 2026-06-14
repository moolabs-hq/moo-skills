from decimal import Decimal

from moo_cloud_bill.commands.seed import seed_findings
from moo_cloud_bill.models import Finding

from ._fakes import RecordingAcuteClient


def _finding(**kw):
    base = dict(
        resource_id="r1", service="AmazonBedrock",
        monthly_cost_estimate_usd=Decimal("10"), untagged_share_pct=100,
        primary_pattern="p", severity="high",
    )
    base.update(kw)
    return Finding(**base)


def test_only_seedable_rows_are_posted():
    findings = [
        _finding(decision="map", approved=True, service_name="ai"),       # seedable
        _finding(resource_id="r2", decision="map", approved=False, service_name="ai"),  # not approved
        _finding(resource_id="r3", decision="absorb", approved=True),     # not map
    ]
    client = RecordingAcuteClient()
    summary = seed_findings(findings, client)
    assert summary.ok == 1
    assert summary.skipped == 2
    assert len(client.map_calls) == 1
    body = client.map_calls[0]
    assert body["cloud_provider"] == "aws"
    assert body["service_name"] == "ai"
    assert "tenant_id" not in body


def test_failed_seed_sets_exit_code():
    findings = [_finding(decision="map", approved=True, service_name="ai")]
    summary = seed_findings(findings, RecordingAcuteClient(status=500))
    assert summary.failed == 1
    assert summary.exit_code == 1
