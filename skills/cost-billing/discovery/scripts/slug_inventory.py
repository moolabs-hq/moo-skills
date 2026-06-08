#!/usr/bin/env python3
"""Phase 1.7-slugs — per-product event-slug constants inventory.

Reads cost-events-inventory.yaml + usage-events-inventory.yaml +
output-input-map.yaml + provider-catalog.starter.yaml + CPO bootstrap's
product list. Derives the constants Phase B+C will emit as a slugs
module per product.

Categories per product:
  EVENT_TYPE   — per-feature canonical event identifiers
  METER_SLUG   — per-feature billing routing keys
  FEATURE_KEY  — per-feature short identifiers
  PROVIDER     — recognized vendor identifiers (from provider-catalog)
  SPAN_TYPE    — canonical span-kind identifiers (from cost_kind values)

Output: .moolabs/customer-context/slug-inventory.yaml

Usage:
    python slug_inventory.py \\
        --cost-events .moolabs/inventory/cost-events-inventory.yaml \\
        --usage-events .moolabs/inventory/usage-events-inventory.yaml \\
        --output-input-map .moolabs/inventory/output-input-map.yaml \\
        --provider-catalog skills/cost-billing/discovery/assets/provider-catalog.starter.yaml \\
        --customer-context-dir .moolabs/customer-context
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────
# Naming convention
# ──────────────────────────────────────────────────────────────────────

_NAME_SEPARATORS = re.compile(r"[.\-_]+")


def to_constant_name(value: str) -> str:
    """Convert a slug value to UPPER_SNAKE_CASE.

    Examples:
        "checkout.recommendation.delivered" -> "CHECKOUT_RECOMMENDATION_DELIVERED"
        "llm-tokens"                        -> "LLM_TOKENS"
        "foo.bar-baz_qux"                   -> "FOO_BAR_BAZ_QUX"
        ".leading.trailing."                -> "LEADING_TRAILING"
    """
    parts = _NAME_SEPARATORS.split(value)
    parts = [p for p in parts if p]  # strip empties from leading/trailing/internal dups
    return "_".join(parts).upper()


# ──────────────────────────────────────────────────────────────────────
# Derivation per product
# ──────────────────────────────────────────────────────────────────────

def _feature_key_for(workflow_id: str) -> str:
    """Derive a feature_key from a dotted workflow_id. Convention:

    - Multi-segment (a.b.c.d):  use the SECOND segment ('b')
    - Two-segment   (a.b):      use the SECOND segment ('b')
    - Single-segment (a):       use the whole value ('a')

    Matches the framework callsite template's existing inline derivation
    (`entry.workflow_id.split('.')[1] if count('.') >= 1 else workflow_id`).
    """
    parts = workflow_id.split(".")
    if len(parts) >= 2:
        return parts[1]
    return workflow_id


def derive_per_product_constants(
    cost_inv: dict,
    usage_inv: dict,
    omap: dict,  # noqa: ARG001 — kept for future cross-edge derivations
    provider_catalog: dict | None,
) -> dict[str, dict[str, list[dict]]]:
    """Return {product_slug: {CATEGORY: [{name, value}, ...]}}.

    Each (CATEGORY, name) is unique within a product. Duplicate detection
    is the caller's responsibility (see check_duplicates()).
    """
    by_product: dict[str, dict[str, list[dict]]] = {}

    def _ensure(product: str) -> dict[str, list[dict]]:
        return by_product.setdefault(product, {
            "EVENT_TYPE": [],
            "METER_SLUG": [],
            "FEATURE_KEY": [],
            "PROVIDER": [],
            "SPAN_TYPE": [],
        })

    def _add_unique(bucket: list[dict], name: str, value: str) -> None:
        if not any(e["name"] == name and e["value"] == value for e in bucket):
            bucket.append({"name": name, "value": value})

    # EVENT_TYPE, METER_SLUG, FEATURE_KEY from cost-events + usage-events
    for source in (cost_inv, usage_inv):
        for entry in source.get("entries", []) or []:
            product = entry.get("product_slug") or "default"
            bucket = _ensure(product)

            event_type = entry.get("event_type") or entry.get("workflow_id")
            if event_type:
                _add_unique(bucket["EVENT_TYPE"],
                            to_constant_name(event_type), event_type)

            workflow_id = entry.get("workflow_id")
            if workflow_id:
                _add_unique(bucket["METER_SLUG"],
                            to_constant_name(workflow_id), workflow_id)
                fk_value = _feature_key_for(workflow_id)
                _add_unique(bucket["FEATURE_KEY"],
                            to_constant_name(fk_value), fk_value)

    # SPAN_TYPE from cost_kind values in cost-events-inventory
    for entry in cost_inv.get("entries", []) or []:
        product = entry.get("product_slug") or "default"
        bucket = _ensure(product)
        cost_kind = entry.get("cost_kind")
        if cost_kind:
            _add_unique(bucket["SPAN_TYPE"],
                        to_constant_name(cost_kind), cost_kind)

    # PROVIDER from provider-catalog (global — every product gets the same
    # enum). When inventories are empty, we still ensure a "default" bucket
    # so the providers are surfaced somewhere.
    if provider_catalog:
        providers = provider_catalog.get("providers") or []
        if not by_product and providers:
            _ensure("default")
        for product in list(by_product.keys()):
            bucket = by_product[product]
            for p in providers:
                slug = p.get("slug")
                if slug:
                    _add_unique(bucket["PROVIDER"],
                                to_constant_name(slug), slug)

    return by_product


# ──────────────────────────────────────────────────────────────────────
# Duplicate detection
# ──────────────────────────────────────────────────────────────────────

def check_duplicates(by_product: dict[str, dict[str, list[dict]]]) -> list[str]:
    """Detect (product, category, name) entries where multiple source values
    collapse to the same canonical NAME. Returns a list of error strings.
    Empty list = clean.

    `_add_unique()` in `derive_per_product_constants()` dedupes by the
    `(name, value)` PAIR — so exact duplicates (same name + same value)
    drop, but two different source values that to_constant_name() collapses
    to the SAME canonical name both land in the bucket. This function walks
    each bucket grouping by name and reports any name with multiple
    distinct values as a collision.
    """
    errors: list[str] = []
    for product, categories in by_product.items():
        for category, entries in categories.items():
            # Build {name: [values]} from the bucket
            by_name: dict[str, list[str]] = {}
            for e in entries:
                by_name.setdefault(e["name"], []).append(e["value"])
            for name, values in by_name.items():
                if len(set(values)) > 1:
                    errors.append(
                        f"duplicate slug name {name} in product {product} "
                        f"category {category}: values={values}"
                    )
    return errors


# ──────────────────────────────────────────────────────────────────────
# YAML emit (hand-rolled)
# ──────────────────────────────────────────────────────────────────────

def emit_slug_inventory_yaml(inventory: dict, dest: Path) -> None:
    """Hand-rolled YAML emit for slug-inventory.yaml."""
    lines: list[str] = []
    # Quote generated_at so PyYAML safe_load keeps it a STRING. Unquoted
    # ISO-8601 is coerced to datetime — build_slugs_emit_tasks then re-emits
    # str(datetime) ("2026-06-06 00:00:00+00:00", space not T) into tasks.yaml
    # (PR #8 review #3-sibling; the dogfood handoff named slug-inventory as
    # THE fix location for #3).
    lines.append(f'generated_at: "{inventory["generated_at"]}"')
    if not inventory.get("products"):
        lines.append("products: []")
    else:
        lines.append("products:")
        for product in inventory["products"]:
            lines.append(f"  - product_slug: {product['product_slug']}")
            lines.append(f"    constants:")
            for category in ("EVENT_TYPE", "METER_SLUG", "FEATURE_KEY",
                             "PROVIDER", "SPAN_TYPE"):
                entries = product["constants"].get(category, [])
                if not entries:
                    lines.append(f"      {category}: []")
                    continue
                lines.append(f"      {category}:")
                for e in entries:
                    lines.append(f"        - name: {e['name']}")
                    # Escape BOTH backslashes AND quotes for YAML double-quoted
                    # scalars. Backslash MUST come first or the quote-escape
                    # backslash gets double-escaped. Without backslash escape,
                    # any slug value containing `\` (rare but possible in
                    # workflow_id / cost_kind strings) produces malformed YAML.
                    v = str(e["value"]).replace('\\', '\\\\').replace('"', '\\"')
                    lines.append(f'          value: "{v}"')

    dest.write_text("\n".join(lines) + "\n")


# ──────────────────────────────────────────────────────────────────────
# I/O helpers (read inventories)
# ──────────────────────────────────────────────────────────────────────

def _read_yaml_safe(path: Path) -> dict:
    """Read a YAML file via PyYAML. Returns {} if missing or unreadable."""
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    except ImportError:
        return {}


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-events", default=".moolabs/inventory/cost-events-inventory.yaml")
    ap.add_argument("--usage-events", default=".moolabs/inventory/usage-events-inventory.yaml")
    ap.add_argument("--output-input-map", default=".moolabs/inventory/output-input-map.yaml")
    ap.add_argument("--provider-catalog", default="skills/cost-billing/discovery/assets/provider-catalog.starter.yaml")
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    cost_inv = _read_yaml_safe(Path(args.cost_events))
    usage_inv = _read_yaml_safe(Path(args.usage_events))
    omap = _read_yaml_safe(Path(args.output_input_map))
    provider_catalog = _read_yaml_safe(Path(args.provider_catalog))

    by_product = derive_per_product_constants(
        cost_inv, usage_inv, omap, provider_catalog
    )

    errors = check_duplicates(by_product)
    if errors:
        print(
            "CRITICAL: slug-name collisions detected — refusing to run:",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 2

    inventory = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "products": [
            {"product_slug": slug, "constants": cats}
            for slug, cats in sorted(by_product.items())
        ],
    }

    out_dir = Path(args.customer_context_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "slug-inventory.yaml"
    emit_slug_inventory_yaml(inventory, out_path)
    print(f"wrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
