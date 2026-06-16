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


def test_is_usable_export_rejects_nonconforming():
    # A reused export must match what the mapper reads, or push silently
    # under-attributes (missing region/tags) / fails later (missing required col).
    import copy

    base = build_data_export(name="r", s3_bucket="b", s3_prefix="cur2")

    no_resources = copy.deepcopy(base)
    no_resources["DataQuery"]["TableConfigurations"]["COST_AND_USAGE_REPORT"]["INCLUDE_RESOURCES"] = "FALSE"
    assert not is_usable_export(no_resources)

    no_gzip = copy.deepcopy(base)
    no_gzip["DestinationConfigurations"]["S3Destination"]["S3OutputConfigurations"]["Compression"] = "PARQUET"
    assert not is_usable_export(no_gzip)

    not_overwrite = copy.deepcopy(base)
    not_overwrite["DestinationConfigurations"]["S3Destination"]["S3OutputConfigurations"]["Overwrite"] = "CREATE_NEW_REPORT"
    assert not is_usable_export(not_overwrite)

    # SQL missing a column the mapper needs (e.g. the tags/region map column).
    missing_col = copy.deepcopy(base)
    missing_col["DataQuery"]["QueryStatement"] = "SELECT line_item_product_code, line_item_unblended_cost FROM COST_AND_USAGE_REPORT"
    assert not is_usable_export(missing_col)

    # A SUPERSET export (extra columns) is still usable.
    superset = copy.deepcopy(base)
    superset["DataQuery"]["QueryStatement"] = base["DataQuery"]["QueryStatement"].replace(
        " FROM ", ", bill_payer_account_id FROM "
    )
    assert is_usable_export(superset)


def test_bucket_policy_covers_both_principals():
    pol = build_data_export_bucket_policy("b", account_id="123")
    principals = pol["Statement"][0]["Principal"]["Service"]
    assert "billingreports.amazonaws.com" in principals
    assert "bcm-data-exports.amazonaws.com" in principals
