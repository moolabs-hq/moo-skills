"""Contract regression test: the bodies this CLI POSTs must validate against
Acute's real request schemas.

WHY THIS EXISTS
  models.py claims "bodies map exactly to Acute's contract". This test PROVES it
  by validating real CLI-produced bodies against a verbatim copy of Acute's
  Pydantic request models. It catches CLI-side drift immediately (a renamed/typed
  field fails here) and server-side drift at the next manual re-verification.

SOURCE OF TRUTH (keep the SCHEMA SNAPSHOT below in sync if Acute changes):
  moolabs/services/moo-acute/app/api/v1/cloud_billing/router.py
    - class CloudCostRowInput
    - class ImportBatchRequest
    - class ResourceMapCreateRequest
  Last verified against that source: 2026-06-16.

The CLI does NOT depend on pydantic (it uses plain dataclasses), so this test is
skipped when pydantic is absent — install the dev extras to run it (it runs in CI).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

import pytest

pydantic = pytest.importorskip("pydantic")
from pydantic import BaseModel, Field, ValidationError, model_validator  # noqa: E402

from moo_cloud_bill.cur_columns import DEFAULT_COLUMN_MAP as CM  # noqa: E402
from moo_cloud_bill.mapper import build_daily_batches  # noqa: E402
from moo_cloud_bill.models import Finding  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# SCHEMA SNAPSHOT — copied VERBATIM from moo-acute cloud_billing/router.py.
# Do not "improve" these; they must mirror the server exactly to be a valid oracle.
# ─────────────────────────────────────────────────────────────────────────────


class CloudCostRowInput(BaseModel):
    service_name: str
    resource_id: Optional[str] = None
    region: Optional[str] = None
    usage_type: Optional[str] = None
    cost: Decimal = Field(..., ge=0)
    currency: str = "USD"
    tags: dict = Field(default_factory=dict)


class ImportBatchRequest(BaseModel):
    cloud_provider: str = Field(..., description="'aws', 'gcp', or 'azure'")
    billing_period_start: datetime
    billing_period_end: datetime
    rows: list[CloudCostRowInput] = Field(..., min_length=1)
    reporting_currency: str = "USD"

    @model_validator(mode="after")
    def validate_dates(self) -> "ImportBatchRequest":
        if self.billing_period_end <= self.billing_period_start:
            raise ValueError("billing_period_end must be > billing_period_start")
        return self


class ResourceMapCreateRequest(BaseModel):
    cloud_provider: str
    resource_id: str
    resource_type: Optional[str] = None
    service_name: str
    team_name: Optional[str] = None
    environment: Optional[str] = None
    tags: dict = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: real CUR-shaped rows run through the real mapper.
# ─────────────────────────────────────────────────────────────────────────────


def _raw_rows():
    return [
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1", CM["region"]: "us-east-1",
         CM["usage_type"]: "Invoke", CM["cost"]: "1.00", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-14T03:00:00Z", "resource_tags": '{"team":"ml"}'},
        {CM["service_name"]: "AmazonBedrock", CM["resource_id"]: "r1", CM["region"]: "us-east-1",
         CM["usage_type"]: "Invoke", CM["cost"]: "2.50", CM["currency"]: "USD",
         CM["usage_start"]: "2026-05-14T09:00:00Z", "resource_tags": '{"team":"ml"}'},
        {CM["service_name"]: "AmazonS3", CM["resource_id"]: "r2", CM["region"]: "us-east-1",
         CM["usage_type"]: "DataTransfer", CM["cost"]: "5E-7", CM["currency"]: "USD",  # sci-notation risk
         CM["usage_start"]: "2026-05-14T10:00:00Z", "resource_tags": ""},
        {CM["service_name"]: "Credit", CM["resource_id"]: "r3", CM["region"]: "us-east-1",
         CM["usage_type"]: "Refund", CM["cost"]: "-9.99", CM["currency"]: "USD",  # negative → dropped
         CM["usage_start"]: "2026-05-14T11:00:00Z", "resource_tags": ""},
    ]


def _import_body():
    batches, _ = build_daily_batches(_raw_rows(), CM)
    return batches[0].to_body()


# ─────────────────────────────────────────────────────────────────────────────
# /import contract
# ─────────────────────────────────────────────────────────────────────────────


def test_import_body_validates_against_server_model():
    req = ImportBatchRequest(**_import_body())
    assert len(req.rows) == 2  # two Bedrock lines summed to one grain + the S3 grain


def test_cost_is_fixed_point_string_never_scientific():
    # cost "5E-7" must reach the wire as "0.0000005" so the server's Decimal parses it.
    body = _import_body()
    costs = [r["cost"] for r in body["rows"]]
    assert all("E" not in c and "e" not in c for c in costs), costs
    req = ImportBatchRequest(**body)
    assert Decimal("0.0000005") in [r.cost for r in req.rows]


def test_negative_cost_lines_are_dropped_before_send():
    # Server enforces cost ge=0; the CLI must never send a negative (it 422s).
    body = _import_body()
    assert all(Decimal(r["cost"]) >= 0 for r in body["rows"])


def test_no_tenant_id_anywhere_in_import_body():
    body = _import_body()
    assert "tenant_id" not in body
    assert all("tenant_id" not in r for r in body["rows"])


def test_all_credit_day_emits_no_empty_batch():
    # Server requires rows min_length=1; an all-credit day must yield 0 batches,
    # not a batch with rows=[] (which would 422).
    only_credit = [dict(_raw_rows()[3])]
    batches, _ = build_daily_batches(only_credit, CM)
    assert batches == []


def test_server_rejects_empty_rows_so_the_guard_matters():
    # Proves the constraint the mapper protects against is real, not assumed.
    with pytest.raises(ValidationError):
        ImportBatchRequest(
            cloud_provider="aws",
            billing_period_start=datetime.fromisoformat("2026-05-14T00:00:00+00:00"),
            billing_period_end=datetime.fromisoformat("2026-05-15T00:00:00+00:00"),
            rows=[],
        )


def test_billing_period_is_half_open_so_end_gt_start():
    req = ImportBatchRequest(**_import_body())
    assert req.billing_period_end > req.billing_period_start


# ─────────────────────────────────────────────────────────────────────────────
# /resource-map contract (scan / seed)
# ─────────────────────────────────────────────────────────────────────────────


def test_resource_map_body_validates_against_server_model():
    f = Finding(
        resource_id="arn:aws:s3:::x", service="AmazonS3",
        monthly_cost_estimate_usd=Decimal("12"), untagged_share_pct=80,
        primary_pattern="no-tags", severity="high",
        service_name="checkout", team_name="payments", environment="prod",
        resource_type="bucket", tags={"k": "v"}, approved=True, decision="map",
    )
    body = f.to_resource_map_body()
    req = ResourceMapCreateRequest(**body)
    assert req.service_name == "checkout"
    # tenant_id and is_active are server-owned — must not be in the client body.
    assert "tenant_id" not in body
    assert "is_active" not in body
