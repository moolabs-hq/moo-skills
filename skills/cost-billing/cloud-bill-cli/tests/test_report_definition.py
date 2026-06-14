import pytest

from moo_cloud_bill.report_definition import (
    build_report_definition,
    is_usable_cur,
    validate_s3_prefix,
)


def test_build_has_required_legacy_cur_settings():
    rd = build_report_definition(report_name="r", s3_bucket="b", s3_prefix="cost/hourly")
    assert rd["TimeUnit"] == "HOURLY"
    assert rd["Format"] == "Parquet"
    assert rd["ReportVersioning"] == "CREATE_NEW_REPORT"
    assert rd["RefreshClosedReports"] is True
    assert "RESOURCES" in rd["AdditionalSchemaElements"]
    assert "SPLIT_COST_ALLOCATION_DATA" in rd["AdditionalSchemaElements"]


@pytest.mark.parametrize("bad", ["", "/cost", "cost/", "/", "   "])
def test_prefix_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_s3_prefix(bad)


def test_prefix_allows_mid_slash():
    assert validate_s3_prefix("cost/hourly") == "cost/hourly"


def test_build_rejects_trailing_slash_prefix():
    with pytest.raises(ValueError):
        build_report_definition(report_name="r", s3_bucket="b", s3_prefix="cost/")


def test_is_usable_cur():
    assert is_usable_cur({"TimeUnit": "HOURLY", "AdditionalSchemaElements": ["RESOURCES"]})
    assert not is_usable_cur({"TimeUnit": "DAILY", "AdditionalSchemaElements": ["RESOURCES"]})
    assert not is_usable_cur({"TimeUnit": "HOURLY", "AdditionalSchemaElements": []})
