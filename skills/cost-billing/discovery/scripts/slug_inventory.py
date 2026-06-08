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


def _build_product_map_from_specs(
    specs: list[dict],
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Build the authoritative event→product map from per-feature-spec docs.

    Dogfood #5: cost/usage inventory entries are agent-authored and routinely
    omit product_slug. per-feature-spec.yaml IS the authoritative product
    mapping (top-level product_slug + event_type_convention.namespace_prefix +
    features[].event_type). Returns:
      - exact:  {event_type: product_slug}  (from features[].event_type)
      - prefix: [(namespace_prefix, product_slug)]  (longest-first so the most
                specific prefix wins)
    """
    exact: dict[str, str] = {}
    prefix: list[tuple[str, str]] = []
    for spec in specs or []:
        # Defensive (PR #9 review NIT): a per-feature-spec.yaml that parses to
        # a non-dict (top-level list, scalar) would otherwise AttributeError on
        # .get(). These are agent-authored docs — tolerate malformed shapes by
        # skipping rather than crashing the whole slug-inventory build.
        if not isinstance(spec, dict):
            continue
        product = spec.get("product_slug") or ""
        if not product:
            continue
        conv = spec.get("event_type_convention")
        conv = conv if isinstance(conv, dict) else {}
        ns = conv.get("namespace_prefix") or ""
        if ns:
            prefix.append((ns, product))
        feats = spec.get("features")
        for feat in feats if isinstance(feats, list) else []:
            if not isinstance(feat, dict):
                continue
            et = feat.get("event_type")
            if et:
                exact[et] = product
    # Longest prefix first so "arc.sub." beats "arc." when both are declared.
    prefix.sort(key=lambda pp: len(pp[0]), reverse=True)
    return exact, prefix


def _product_for_event(
    event_type: str | None,
    exact_map: dict[str, str],
    prefix_map: list[tuple[str, str]],
) -> str:
    """Resolve the product_slug for an event value deterministically.

    Order: exact per-feature-spec match → namespace-prefix match → first
    dotted segment (the namespace convention) → "" (caller defaults to
    'default'). Decouples slug bucketing from the agent emitting product_slug.
    """
    if not event_type:
        return ""
    if event_type in exact_map:
        return exact_map[event_type]
    for ns, product in prefix_map:
        if event_type.startswith(ns):
            return product
    # First dotted segment as the namespace convention fallback
    # (arc.shared.llmport-call -> "arc"). Single-token events -> "".
    head, sep, _ = event_type.partition(".")
    return head if sep else ""


def derive_per_product_constants(
    cost_inv: dict,
    usage_inv: dict,
    omap: dict,  # noqa: ARG001 — kept for future cross-edge derivations
    provider_catalog: dict | None,
    product_map: tuple[dict[str, str], list[tuple[str, str]]] | None = None,
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

    exact_map, prefix_map = product_map or ({}, [])

    def _product_of(entry: dict) -> str:
        # Declared product_slug wins; else derive from the event value via the
        # authoritative per-feature-spec map (Dogfood #5 — agent-authored
        # entries omit product_slug, which previously collapsed every product
        # into the single "default" bucket and defeated slug resolution).
        declared = entry.get("product_slug")
        if declared:
            return declared
        ev = entry.get("event_type") or entry.get("workflow_id")
        return _product_for_event(ev, exact_map, prefix_map) or "default"

    # EVENT_TYPE, METER_SLUG, FEATURE_KEY from cost-events + usage-events
    for source in (cost_inv, usage_inv):
        for entry in source.get("entries", []) or []:
            product = _product_of(entry)
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
        product = _product_of(entry)
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
# Consolidation double-count detection (Dogfood #4 enforcement)
# ──────────────────────────────────────────────────────────────────────

def check_consolidation_double_count(cost_inv: dict, omap: dict) -> list[str]:
    """Detect cost-events marked `pattern: sibling-pair` at a CONSOLIDATION
    site — a single cost feeding >=2 usage outputs.

    SKILL.md (Cost-consolidation rule) requires such a cost to be `cost-only`
    at its single emission site, with each usage event `usage-only`. Marking
    it `sibling-pair` emits a garbage cost lane at each agent site AND
    double-counts the cost (once per agent + once at the consolidation site).
    f70ce55 added the prose guidance; this is the deterministic detector the
    guidance lacked — the agent ignored the prose and shipped sibling-pair.

    Detection signal (from the SKILL.md rule): invert output-input-map; a cost
    workflow referenced by >=2 distinct output (usage) workflows is a
    consolidation point. Returns a list of warning strings (empty = clean).
    """
    # Invert the omap: cost_workflow_id -> set(output_workflow_ids).
    # Defensive (PR #9 review NIT): edges/inputs/entries from agent-authored
    # YAML may parse to non-dict shapes; skip those rather than AttributeError.
    fan_out: dict[str, set[str]] = {}
    for edge in (omap.get("edges") if isinstance(omap, dict) else []) or []:
        if not isinstance(edge, dict):
            continue
        out_wf = edge.get("output_workflow_id")
        if not out_wf:
            continue
        for inp in edge.get("inputs") or []:
            if not isinstance(inp, dict):
                continue
            cost_wf = inp.get("cost_workflow_id")
            if cost_wf:
                fan_out.setdefault(cost_wf, set()).add(out_wf)

    warnings: list[str] = []
    for entry in (cost_inv.get("entries") if isinstance(cost_inv, dict) else []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("pattern") != "sibling-pair":
            continue
        wf = entry.get("workflow_id")
        outs = fan_out.get(wf, set())
        if len(outs) >= 2:
            warnings.append(
                f"consolidation double-count: cost '{wf}' is marked "
                f"pattern: sibling-pair but feeds {len(outs)} usage outputs "
                f"({sorted(outs)}). Per the cost-consolidation rule it MUST be "
                f"pattern: cost-only (each usage event usage-only) — "
                f"sibling-pair double-counts the cost + emits a garbage cost "
                f"lane per agent site."
            )
    return warnings


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


def _load_per_feature_specs(customer_context_dir: Path) -> list[dict]:
    """Load every per-feature-spec.yaml under the customer-context dir — the
    root one plus per-product subdirs (e.g. moo-acute/per-feature-spec.yaml).
    Each is the authoritative product↔event map for #5 product derivation."""
    specs: list[dict] = []
    if not customer_context_dir.is_dir():
        return specs
    # Root + one-level subdir per-product specs.
    for p in sorted(customer_context_dir.glob("per-feature-spec.yaml")):
        d = _read_yaml_safe(p)
        if d:
            specs.append(d)
    for p in sorted(customer_context_dir.glob("*/per-feature-spec.yaml")):
        d = _read_yaml_safe(p)
        if d:
            specs.append(d)
    return specs


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

    # Dogfood #5: derive product_slug per entry from the authoritative
    # per-feature-spec map so cost/usage entries that omit product_slug bucket
    # under their REAL product instead of collapsing into "default".
    specs = _load_per_feature_specs(Path(args.customer_context_dir))
    product_map = _build_product_map_from_specs(specs)

    by_product = derive_per_product_constants(
        cost_inv, usage_inv, omap, provider_catalog, product_map=product_map
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

    # Dogfood #4: surface (don't block) cost-consolidation sites wrongly
    # marked sibling-pair — the deterministic enforcement the prose guidance
    # lacked. Visible at three-role + adversarial review; the agent owns the
    # final pattern call, so warn rather than refuse-to-run.
    consolidation_warnings = check_consolidation_double_count(cost_inv, omap)
    for w in consolidation_warnings:
        print(f"WARNING (consolidation): {w}", file=sys.stderr)

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
