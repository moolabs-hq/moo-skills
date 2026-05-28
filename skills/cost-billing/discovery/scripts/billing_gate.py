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
sail through as phantom billable usage events.

CRITICAL — surface, don't silently resolve. A conflict between the CODE evidence
(a strong usage candidate) and the BILLING MODEL (internal_only / no unit) is
ambiguous: the op may be genuinely internal, OR the billing model may have missed
a unit. (A real dogfood incident: a metering product's per-event-ingested billing
lived in a spec doc the finance stage never ingested; the heuristic correctly
found the call site, but the billing model said "internal," and the conflict was
silently resolved toward "internal" — burying a real billable event.) So:
  - LOW-confidence candidate + billing conflict  → suppress (billing model wins).
  - HIGH-confidence candidate + billing conflict → SURFACE for reconciliation —
    never silently suppress; force a source-grounded human decision.

Pure, dependency-free predicate so it is trivially testable and reusable by the
Phase-4/Phase-5 logic (and the agent) without any repo-specific knowledge.
"""

from __future__ import annotations

from dataclasses import dataclass


# Billing-model conflict signals — must match usage-events.schema.yaml#suppression_category.
VENDOR_COGS_ONLY = "vendor_cogs_only"
INTERNAL_ONLY_PRODUCT = "internal_only_product"
NO_BILLABLE_UNIT = "no_billable_unit"

# Dispositions — must match usage-events.schema.yaml#suppressions[].disposition.
EMIT = "emit"
SUPPRESS = "suppress"
SURFACE = "surface_for_reconciliation"

# A candidate at/above this refund-test confidence is "high confidence" — strong
# enough that a billing-model conflict is more likely a capture gap than a true
# internal op. Matches terminal-event-heuristics.yaml `high_confidence_terminal`.
HIGH_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True)
class GateDecision:
    disposition: str            # EMIT | SUPPRESS | SURFACE
    # The conflicting billing-model signal for SUPPRESS/SURFACE; "" for EMIT.
    suppression_category: str
    reason: str

    @property
    def billable(self) -> bool:
        return self.disposition == EMIT


def gate_usage_candidate(
    *,
    product_internal_only: bool,
    applicable_billable_unit_count: int,
    candidate_confidence: float,
    high_confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
) -> GateDecision:
    """Decide whether a terminal-event candidate may be emitted as billable.

    Inputs (generic to every customer — no product/vendor/event names):
      - product_internal_only: CPO Stage 2 marked this product internal_only.
      - applicable_billable_unit_count: number of finance Stage 1 customer-billable
        units (classification customer_facing_billable / sibling_pair — NOT
        vendor_cogs_only) whose scope covers this product/service.
      - candidate_confidence: the refund-test score for this candidate.

    Returns a GateDecision whose disposition is EMIT, SUPPRESS, or SURFACE. SURFACE
    means "the code and the billing model disagree and the code is confident — do
    NOT silently suppress; route to source-grounded human adjudication."
    """
    # Which billing-model signal (if any) says "do not bill"? internal_only is
    # checked first — the CPO's explicit declaration is the stronger statement.
    if product_internal_only:
        conflict_category = INTERNAL_ONLY_PRODUCT
    elif applicable_billable_unit_count <= 0:
        conflict_category = NO_BILLABLE_UNIT
    else:
        return GateDecision(
            disposition=EMIT,
            suppression_category="",
            reason="Product has a billable surface; candidate emitted as a usage event.",
        )

    # The billing model says "don't bill". Does the CODE strongly disagree?
    if candidate_confidence >= high_confidence_threshold:
        return GateDecision(
            disposition=SURFACE,
            suppression_category=conflict_category,
            reason=(
                f"DIVERGENCE: refund-test confidence {candidate_confidence:.2f} "
                f">= {high_confidence_threshold:.2f} marks this a strong "
                f"customer-facing usage candidate, but the billing model says "
                f"'{conflict_category}'. Do NOT silently suppress — surface for "
                f"adjudication: genuinely internal, or did the billing model miss "
                f"a unit? Resolve against the provided spec/doc sources."
            ),
        )

    # Low-confidence candidate — the authoritative billing model governs; suppress.
    return GateDecision(
        disposition=SUPPRESS,
        suppression_category=conflict_category,
        reason=(
            f"Refund-test confidence {candidate_confidence:.2f} below "
            f"{high_confidence_threshold:.2f}; billing model "
            f"('{conflict_category}') governs — suppressed."
        ),
    )
