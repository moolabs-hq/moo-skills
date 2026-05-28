#!/usr/bin/env python3
"""Unit tests for billing_gate.py (Phase-4 billing-surface gate).

Stdlib unittest; runs in the bash smoke suite's Phase 8. Inputs are deliberately
generic (booleans/ints/floats) — no product/vendor/repo names — so the gate can't
encode any one codebase's specifics.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import billing_gate as bg  # noqa: E402

LOW = 0.40   # below HIGH_CONFIDENCE_THRESHOLD
HIGH = 0.90  # at/above it


class BillableSurfaceExists(unittest.TestCase):
    def test_emit_when_units_and_not_internal(self):
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=2, candidate_confidence=HIGH
        )
        self.assertEqual(d.disposition, bg.EMIT)
        self.assertTrue(d.billable)
        self.assertEqual(d.suppression_category, "")

    def test_single_unit_is_enough(self):
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=1, candidate_confidence=LOW
        )
        self.assertEqual(d.disposition, bg.EMIT)


class LowConfidenceConflictSuppresses(unittest.TestCase):
    def test_internal_only_low_conf_suppressed(self):
        d = bg.gate_usage_candidate(
            product_internal_only=True, applicable_billable_unit_count=0, candidate_confidence=LOW
        )
        self.assertEqual(d.disposition, bg.SUPPRESS)
        self.assertEqual(d.suppression_category, bg.INTERNAL_ONLY_PRODUCT)
        self.assertFalse(d.billable)

    def test_no_unit_low_conf_suppressed(self):
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=0, candidate_confidence=LOW
        )
        self.assertEqual(d.disposition, bg.SUPPRESS)
        self.assertEqual(d.suppression_category, bg.NO_BILLABLE_UNIT)


class HighConfidenceConflictSurfaces(unittest.TestCase):
    """The fix: a strong usage candidate that the billing model contradicts must be
    SURFACED, not silently suppressed. This is the meter.event.ingested case."""

    def test_no_unit_high_conf_surfaces(self):
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=0, candidate_confidence=HIGH
        )
        self.assertEqual(d.disposition, bg.SURFACE)
        self.assertEqual(d.suppression_category, bg.NO_BILLABLE_UNIT)
        self.assertFalse(d.billable)  # surfaced, not emitted — needs adjudication
        self.assertIn("DIVERGENCE", d.reason)

    def test_internal_only_high_conf_surfaces(self):
        # Even an internal_only flag is surfaced (not silently obeyed) when the
        # code strongly disagrees — internal_only itself may have been mis-set.
        d = bg.gate_usage_candidate(
            product_internal_only=True, applicable_billable_unit_count=0, candidate_confidence=HIGH
        )
        self.assertEqual(d.disposition, bg.SURFACE)
        self.assertEqual(d.suppression_category, bg.INTERNAL_ONLY_PRODUCT)

    def test_threshold_boundary_is_high(self):
        # Exactly at threshold counts as high-confidence → surface.
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=0,
            candidate_confidence=bg.HIGH_CONFIDENCE_THRESHOLD,
        )
        self.assertEqual(d.disposition, bg.SURFACE)

    def test_just_below_threshold_suppresses(self):
        d = bg.gate_usage_candidate(
            product_internal_only=False, applicable_billable_unit_count=0,
            candidate_confidence=bg.HIGH_CONFIDENCE_THRESHOLD - 0.01,
        )
        self.assertEqual(d.disposition, bg.SUPPRESS)


class Invariants(unittest.TestCase):
    def test_every_non_emit_decision_carries_a_reason(self):
        for conf in (LOW, HIGH):
            for internal in (True, False):
                d = bg.gate_usage_candidate(
                    product_internal_only=internal, applicable_billable_unit_count=0,
                    candidate_confidence=conf,
                )
                self.assertTrue(d.reason)

    def test_categories_match_schema_enum(self):
        self.assertEqual(
            {bg.VENDOR_COGS_ONLY, bg.INTERNAL_ONLY_PRODUCT, bg.NO_BILLABLE_UNIT},
            {"vendor_cogs_only", "internal_only_product", "no_billable_unit"},
        )

    def test_dispositions_match_schema_enum(self):
        self.assertEqual(
            {bg.EMIT, bg.SUPPRESS, bg.SURFACE},
            {"emit", "suppress", "surface_for_reconciliation"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
