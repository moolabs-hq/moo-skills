"""CUR column → logical-field mapping.

The logical fields are what the mapper needs: service_name, resource_id, region,
usage_type, cost, currency, usage_start, tags_prefix. Physical CUR column names
are format-dependent (slash in CSV, underscore in Parquet), so the map is data,
auto-filled from the delivered CUR manifest (resolves PRD OQ-2) and overridable
via ``cur-column-map.yaml``.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Defaults target Legacy CUR Parquet (underscore-normalized) names. Used before
# the first manifest is available; the manifest overrides these per real export.
DEFAULT_COLUMN_MAP: dict[str, str] = {
    "service_name": "line_item_product_code",
    "resource_id": "line_item_resource_id",
    "region": "product_region",
    "usage_type": "line_item_usage_type",
    "cost": "line_item_unblended_cost",
    "currency": "line_item_currency_code",
    "usage_start": "line_item_usage_start_date",
}
TAGS_PREFIX = "resource_tags_"

# Candidate physical columns per logical field, best-match-first. Used to map a
# real manifest's column list back to our logical fields.
_CANDIDATES: dict[str, list[str]] = {
    "service_name": ["line_item_product_code", "product_servicecode", "product_servicename"],
    "resource_id": ["line_item_resource_id"],
    "region": ["product_region", "product_region_code", "product_location"],
    "usage_type": ["line_item_usage_type"],
    "cost": ["line_item_unblended_cost", "line_item_net_unblended_cost"],
    "currency": ["line_item_currency_code", "pricing_currency"],
    "usage_start": ["line_item_usage_start_date"],
}


def build_column_map_from_manifest(manifest: dict) -> dict[str, str]:
    """Map logical fields to actual columns present in a CUR manifest.

    Manifest shape (Legacy CUR): ``{"columns": [{"category":..,"name":..}, ...]}``.
    Falls back to the documented default for any field not found.
    """
    present = _manifest_columns(manifest)
    present_set = set(present)
    result = dict(DEFAULT_COLUMN_MAP)
    for field, candidates in _CANDIDATES.items():
        for cand in candidates:
            if cand in present_set:
                result[field] = cand
                break
    return result


def _camel_to_snake(s: str) -> str:
    """``lineItem`` -> ``line_item``; ``UnblendedCost`` -> ``unblended_cost``."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


def _manifest_columns(manifest: dict) -> list[str]:
    """Physical Parquet column names from a Legacy CUR manifest.

    The manifest lists columns as ``{"category": "lineItem", "name": "ProductCode"}``;
    the Parquet column is ``line_item_product_code`` (each part camel→snake, joined).
    """
    cols = manifest.get("columns") or []
    names: list[str] = []
    for c in cols:
        if isinstance(c, dict):
            name = c.get("name")
            if not name:
                continue
            cat = c.get("category")
            physical = _camel_to_snake(name) if not cat else f"{_camel_to_snake(cat)}_{_camel_to_snake(name)}"
            names.append(physical)
        elif isinstance(c, str):
            names.append(c.lower())
    return names


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
