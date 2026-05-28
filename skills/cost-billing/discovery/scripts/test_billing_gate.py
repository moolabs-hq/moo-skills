#!/usr/bin/env python3
"""Unit tests for billing_gate.py (Phase-4 billing-surface gate).

Stdlib unittest; runs in the bash smoke suite's Phase 8. Inputs are deliberately
generic (booleans/ints) — no product/vendor/repo names — so the gate can't encode
any one codebase's specifics.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import billing_gate as bg  # noqa: E402


class BillingSurfaceGate(unittest.TestCase):
    def test_internal_only_product_is_suppressed(self):
        d = bg.gate_usage_candidate(product_internal_only=True, applicable_billable_unit_count=0)
        self.assertFalse(d.billable)
        self.assertEqual(d.suppression_category, bg.INTERNAL_ONLY_PRODUCT)

    def test_internal_only_wins_even_with_units(self):
        # CPO internal_only is the stronger statement: suppress even if a stray
        # finance unit scopes here.
        d = bg.gate_usage_candidate(product_internal_only=True, applicable_billable_unit_count=3)
        self.assertFalse(d.billable)
        self.assertEqual(d.suppression_category, bg.INTERNAL_ONLY_PRODUCT)

    def test_zero_billable_units_is_suppressed(self):
        # The exact gap that let the phantom events through: no unit to suppress
        # against → the Vendor-COGS rule never fires → must be caught here.
        d = bg.gate_usage_candidate(product_internal_only=False, applicable_billable_unit_count=0)
        self.assertFalse(d.billable)
        self.assertEqual(d.suppression_category, bg.NO_BILLABLE_UNIT)

    def test_billable_when_surface_exists(self):
        d = bg.gate_usage_candidate(product_internal_only=False, applicable_billable_unit_count=2)
        self.assertTrue(d.billable)
        self.assertEqual(d.suppression_category, "")

    def test_single_unit_is_enough(self):
        d = bg.gate_usage_candidate(product_internal_only=False, applicable_billable_unit_count=1)
        self.assertTrue(d.billable)

    def test_suppressed_decisions_carry_a_reason(self):
        for kwargs in (
            {"product_internal_only": True, "applicable_billable_unit_count": 0},
            {"product_internal_only": False, "applicable_billable_unit_count": 0},
        ):
            d = bg.gate_usage_candidate(**kwargs)
            self.assertTrue(d.reason, f"suppression must explain itself: {kwargs}")

    def test_categories_match_schema_enum(self):
        # Guard against drift from usage-events.schema.yaml#suppression_category.
        self.assertEqual(
            {bg.VENDOR_COGS_ONLY, bg.INTERNAL_ONLY_PRODUCT, bg.NO_BILLABLE_UNIT},
            {"vendor_cogs_only", "internal_only_product", "no_billable_unit"},
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
