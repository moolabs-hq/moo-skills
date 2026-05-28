#!/usr/bin/env python3
"""billing_gate.py — Phase-4 billing-surface gate for /cost-billing-discovery.

The refund-test heuristic (terminal-event-heuristics.yaml) identifies what LOOKS
like a terminal event. It is necessary but NOT sufficient: a candidate may only
be emitted as a billable usage event if the product/service it belongs to has a
billable surface in the AUTHORITATIVE billing model.

Root cause this closes (general, not specific to any one repo): the Vendor-COGS
suppression rule iterates finance `billable_units[]`, so a product with ZERO
billable units — pure infrastructure / internal tooling — has nothing to suppress
against. Verb patterns like _ingested / _generated / _aggregated / _delivered then
sail through as phantom billable usage events. This gate suppresses them instead,
keyed only on the two authoritative signals every customer's billing model has:
the CPO `internal_only` flag and the finance `billable_units[]` count.

Pure, dependency-free predicate so it is trivially testable and reusable by the
Phase-4/Phase-5 logic (and the agent) without any repo-specific knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass


# Suppression categories — must match usage-events.schema.yaml#suppression_category.
VENDOR_COGS_ONLY = "vendor_cogs_only"
INTERNAL_ONLY_PRODUCT = "internal_only_product"
NO_BILLABLE_UNIT = "no_billable_unit"


@dataclass(frozen=True)
class GateDecision:
    billable: bool
    # Empty string when billable=True; a suppression_category otherwise.
    suppression_category: str
    reason: str


def gate_usage_candidate(
    *,
    product_internal_only: bool,
    applicable_billable_unit_count: int,
) -> GateDecision:
    """Decide whether a terminal-event candidate may be emitted as billable.

    Inputs are the two authoritative billing-model signals (generic to every
    customer — no product/vendor/event names):
      - product_internal_only: CPO Stage 2 marked this product internal_only.
      - applicable_billable_unit_count: number of finance Stage 1 billable_units
        whose scope covers this product/service.

    `internal_only` is checked first: an internal product is suppressed even if a
    stray finance unit happens to scope to it, because the CPO's internal_only
    declaration is the stronger statement about customer-billable intent.
    """
    if product_internal_only:
        return GateDecision(
            billable=False,
            suppression_category=INTERNAL_ONLY_PRODUCT,
            reason=(
                "CPO marked this product internal_only — no customer-billable "
                "surface; terminal-event heuristic suppressed regardless of score."
            ),
        )
    if applicable_billable_unit_count <= 0:
        return GateDecision(
            billable=False,
            suppression_category=NO_BILLABLE_UNIT,
            reason=(
                "Finance declares zero billable_units covering this "
                "product/service — nothing to bill against; heuristic suppressed."
            ),
        )
    return GateDecision(billable=True, suppression_category="", reason="")
