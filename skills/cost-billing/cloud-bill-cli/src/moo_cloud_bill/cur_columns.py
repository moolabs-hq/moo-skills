"""CUR 2.0 column → logical-field mapping.

The export's SQL selects exactly these columns (see report_definition.EXPORT_COLUMNS),
so the CSV header is deterministic — no manifest parsing needed. CUR 2.0 uses
``product_region_code`` (legacy used ``product_region``). Overridable via
``cur-column-map.yaml``.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_COLUMN_MAP: dict[str, str] = {
    "service_name": "line_item_product_code",
    "resource_id": "line_item_resource_id",
    "region": "product_region_code",
    "usage_type": "line_item_usage_type",
    "cost": "line_item_unblended_cost",
    "currency": "line_item_currency_code",
    "usage_start": "line_item_usage_start_date",
}
# CUR 2.0 emits a single `resource_tags` map column (JSON in CSV), not the legacy
# per-key `resource_tags_user_*` columns. The mapper parses this.
TAGS_COLUMN = "resource_tags"


def load_column_map(path: Path | None) -> dict[str, str]:
    """Load a column map from YAML, merged over the defaults. None → defaults."""
    merged = dict(DEFAULT_COLUMN_MAP)
    if path is None:
        return merged
    p = Path(path)
    if not p.exists():
        return merged
    data = yaml.safe_load(p.read_text()) or {}
    for key, value in data.items():
        if value:
            merged[key] = value
    return merged


def save_column_map(column_map: dict[str, str], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(column_map, sort_keys=True))
    return path
