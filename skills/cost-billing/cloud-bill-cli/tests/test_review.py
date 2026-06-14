from decimal import Decimal

from moo_cloud_bill.commands.review import review_findings
from moo_cloud_bill.models import Finding
from moo_cloud_bill.ui import ScriptedUI

from ._fakes import RecordingAcuteClient


def _finding():
    return Finding(
        resource_id="r1", service="AmazonBedrock",
        monthly_cost_estimate_usd=Decimal("8920"), untagged_share_pct=100,
        primary_pattern="p", severity="critical", suggested_service_mapping="bedrock",
    )


def test_map_decision_records_service_and_approves():
    f = _finding()
    ui = ScriptedUI(choices=[0], answers=["ai-chat", ""])  # map; service_name; team blank
    review_findings([f], ui)
    assert f.decision == "map"
    assert f.approved is True
    assert f.service_name == "ai-chat"
    assert f.is_seedable()


def test_skip_leaves_finding_unapproved():
    f = _finding()
    ui = ScriptedUI(choices=[3])  # skip
    review_findings([f], ui)
    assert f.approved is False


def test_review_with_seed_client_posts_approved():
    f = _finding()
    ui = ScriptedUI(choices=[0], answers=["ai-chat", ""])
    client = RecordingAcuteClient()
    review_findings([f], ui, seed_client=client)
    assert len(client.map_calls) == 1
