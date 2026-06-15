from moo_cloud_bill.cur_columns import (
    DEFAULT_COLUMN_MAP,
    load_column_map,
    save_column_map,
)


def test_cur2_default_region_is_region_code():
    # CUR 2.0 uses product_region_code (legacy used product_region).
    assert DEFAULT_COLUMN_MAP["region"] == "product_region_code"
    assert DEFAULT_COLUMN_MAP["cost"] == "line_item_unblended_cost"


def test_load_column_map_merges_over_defaults(tmp_path):
    path = tmp_path / "cur-column-map.yaml"
    save_column_map({"cost": "line_item_net_unblended_cost"}, path)
    merged = load_column_map(path)
    assert merged["cost"] == "line_item_net_unblended_cost"
    assert merged["service_name"] == DEFAULT_COLUMN_MAP["service_name"]


def test_load_missing_returns_defaults(tmp_path):
    assert load_column_map(tmp_path / "nope.yaml") == DEFAULT_COLUMN_MAP
