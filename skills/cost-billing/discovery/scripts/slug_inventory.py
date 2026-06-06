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
        if not any(e["name"] == name for e in bucket):
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
# CLI (skeleton — fleshed out in later tasks)
# ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost-events", required=False, default=".moolabs/inventory/cost-events-inventory.yaml")
    ap.add_argument("--usage-events", required=False, default=".moolabs/inventory/usage-events-inventory.yaml")
    ap.add_argument("--output-input-map", required=False, default=".moolabs/inventory/output-input-map.yaml")
    ap.add_argument("--provider-catalog", required=False)
    ap.add_argument("--customer-context-dir", default=".moolabs/customer-context")
    args = ap.parse_args(argv)

    print(
        "Phase A Task 10 skeleton — derives EVENT_TYPE / METER_SLUG / FEATURE_KEY only. "
        "PROVIDER / SPAN_TYPE / per-product split / YAML emit land in later tasks.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
