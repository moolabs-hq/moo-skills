#!/usr/bin/env python3
"""Unit tests for slug_inventory.py (Phase 1.7-slugs).

Stdlib unittest; runs in the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import slug_inventory as si  # noqa: E402


class WorkflowIdToConstantName(unittest.TestCase):
    def test_dotted_workflow_id(self):
        self.assertEqual(si.to_constant_name("checkout.recommendation.delivered"),
                         "CHECKOUT_RECOMMENDATION_DELIVERED")

    def test_hyphenated_value(self):
        self.assertEqual(si.to_constant_name("llm-tokens"), "LLM_TOKENS")

    def test_mixed_separators(self):
        self.assertEqual(si.to_constant_name("foo.bar-baz_qux"),
                         "FOO_BAR_BAZ_QUX")

    def test_already_upper_snake(self):
        self.assertEqual(si.to_constant_name("ALREADY_GOOD"), "ALREADY_GOOD")

    def test_strips_leading_trailing_punctuation(self):
        self.assertEqual(si.to_constant_name(".leading.trailing."),
                         "LEADING_TRAILING")


class DeriveEventTypes(unittest.TestCase):
    def test_from_cost_events_inventory(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation.delivered",
                 "event_type": "checkout.recommendation.delivered",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        self.assertIn("billing", by_product)
        event_types = by_product["billing"]["EVENT_TYPE"]
        names = {e["name"] for e in event_types}
        self.assertIn("CHECKOUT_RECOMMENDATION_DELIVERED", names)


class DeriveMeterSlugs(unittest.TestCase):
    def test_meter_slug_from_workflow_id(self):
        cost_inv = {"entries": []}
        usage_inv = {
            "entries": [
                {"workflow_id": "seat.assigned", "event_type": "seat.assigned",
                 "product_slug": "billing"},
            ],
        }
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        meter_slugs = by_product["billing"]["METER_SLUG"]
        values = {e["value"] for e in meter_slugs}
        self.assertIn("seat.assigned", values)


class DeriveFeatureKeys(unittest.TestCase):
    def test_feature_key_is_second_dotted_segment(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation.delivered",
                 "event_type": "checkout.recommendation.delivered",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        feature_keys = by_product["billing"]["FEATURE_KEY"]
        # checkout.recommendation.delivered → feature_key = "recommendation"
        # (second segment of dotted workflow_id)
        values = {e["value"] for e in feature_keys}
        self.assertIn("recommendation", values)

    def test_single_segment_workflow_id_uses_whole_value_as_feature_key(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "seat", "event_type": "seat",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        feature_keys = by_product["billing"]["FEATURE_KEY"]
        values = {e["value"] for e in feature_keys}
        self.assertIn("seat", values)


if __name__ == "__main__":
    unittest.main()
