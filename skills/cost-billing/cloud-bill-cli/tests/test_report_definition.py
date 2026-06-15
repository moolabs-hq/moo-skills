import pytest

from moo_cloud_bill.report_definition import (
    EXPORT_COLUMNS,
    build_data_export,
    build_data_export_bucket_policy,
    is_usable_export,
    validate_s3_prefix,
)


def test_build_data_export_is_cur2_csv_overwrite():
    ed = build_data_export(name="moolabs-cur2", s3_bucket="b", s3_prefix="cur2")
    out = ed["DestinationConfigurations"]["S3Destination"]["S3OutputConfigurations"]
    assert out["Format"] == "TEXT_OR_CSV"        # CSV, not Parquet
    assert out["Compression"] == "GZIP"
    assert out["Overwrite"] == "OVERWRITE_REPORT"  # one current set → no double-count
    tc = ed["DataQuery"]["TableConfigurations"]["COST_AND_USAGE_REPORT"]
    assert tc["TIME_GRANULARITY"] == "HOURLY"
    assert tc["INCLUDE_RESOURCES"] == "TRUE"
    assert ed["RefreshCadence"]["Frequency"] == "SYNCHRONOUS"


def test_query_selects_the_columns_the_mapper_needs():
    sql = build_data_export(name="r", s3_bucket="b", s3_prefix="cur2")["DataQuery"]["QueryStatement"]
    for col in ("line_item_unblended_cost", "line_item_product_code",
                "product_region_code", "line_item_usage_start_date"):
        assert col in sql
    assert "line_item_unblended_cost" in EXPORT_COLUMNS


@pytest.mark.parametrize("bad", ["", "/cur2", "cur2/", "/", "   "])
def test_prefix_rejects_invalid(bad):
    with pytest.raises(ValueError):
        validate_s3_prefix(bad)


def test_prefix_allows_mid_slash():
    assert validate_s3_prefix("cost/cur2") == "cost/cur2"


def test_is_usable_export():
    good = build_data_export(name="r", s3_bucket="b", s3_prefix="cur2")
    assert is_usable_export(good)
    assert not is_usable_export({})  # not a CUR 2.0 export


def test_bucket_policy_covers_both_principals():
    pol = build_data_export_bucket_policy("b", account_id="123")
    principals = pol["Statement"][0]["Principal"]["Service"]
    assert "billingreports.amazonaws.com" in principals
    assert "bcm-data-exports.amazonaws.com" in principals
