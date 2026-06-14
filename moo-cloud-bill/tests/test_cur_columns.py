from moo_cloud_bill.cur_columns import (
    DEFAULT_COLUMN_MAP,
    build_column_map_from_manifest,
    load_column_map,
    save_column_map,
)


def test_manifest_maps_camelcase_to_snake():
    manifest = {
        "columns": [
            {"category": "lineItem", "name": "ProductCode"},
            {"category": "lineItem", "name": "ResourceId"},
            {"category": "lineItem", "name": "UnblendedCost"},
            {"category": "product", "name": "region"},
        ]
    }
    cmap = build_column_map_from_manifest(manifest)
    assert cmap["service_name"] == "line_item_product_code"
    assert cmap["resource_id"] == "line_item_resource_id"
    assert cmap["cost"] == "line_item_unblended_cost"
    assert cmap["region"] == "product_region"


def test_manifest_falls_back_to_default_for_missing():
    cmap = build_column_map_from_manifest({"columns": []})
    assert cmap == DEFAULT_COLUMN_MAP


def test_load_column_map_merges_over_defaults(tmp_path):
    path = tmp_path / "cur-column-map.yaml"
    save_column_map({"cost": "line_item_net_unblended_cost"}, path)
    merged = load_column_map(path)
    assert merged["cost"] == "line_item_net_unblended_cost"
    assert merged["service_name"] == DEFAULT_COLUMN_MAP["service_name"]


def test_load_missing_returns_defaults(tmp_path):
    assert load_column_map(tmp_path / "nope.yaml") == DEFAULT_COLUMN_MAP
