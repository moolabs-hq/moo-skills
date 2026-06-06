#!/usr/bin/env python3
"""Unit tests for task_planner.py.

Stdlib unittest; runs in the bash smoke suite's Phase 8 (auto-discovered).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_planner as tp  # noqa: E402


class LoadSlugInventory(unittest.TestCase):
    def test_load_basic_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "slug-inventory.yaml"
            inv.write_text(
                "generated_at: 2026-06-06T00:00:00+00:00\n"
                "products:\n"
                "  - product_slug: billing\n"
                "    constants:\n"
                "      EVENT_TYPE:\n"
                "        - name: SEAT_ASSIGNED\n"
                "          value: \"seat.assigned\"\n"
                "      METER_SLUG:\n"
                "        - name: SEAT_ASSIGNED\n"
                "          value: \"seat.assigned\"\n"
                "      FEATURE_KEY: []\n"
                "      PROVIDER: []\n"
                "      SPAN_TYPE: []\n"
            )
            data = tp.load_slug_inventory(inv)
            self.assertEqual(len(data["products"]), 1)
            self.assertEqual(data["products"][0]["product_slug"], "billing")

    def test_load_missing_file_returns_empty(self):
        data = tp.load_slug_inventory(Path("/nonexistent/path.yaml"))
        self.assertEqual(data, {"products": []})


class SlugIndex(unittest.TestCase):
    def test_index_builds_value_to_constant_lookup(self):
        inventory = {
            "products": [
                {
                    "product_slug": "billing",
                    "constants": {
                        "EVENT_TYPE": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                            {"name": "CHECKOUT_DELIVERED",
                             "value": "checkout.delivered"},
                        ],
                        "METER_SLUG": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "FEATURE_KEY": [],
                        "PROVIDER": [],
                        "SPAN_TYPE": [
                            {"name": "LLM_TOKENS", "value": "llm-tokens"},
                        ],
                    },
                },
            ],
        }
        index = tp.build_slug_index(inventory)
        # Index is keyed by product_slug -> category -> {value: constant_name}
        self.assertIn("billing", index)
        self.assertEqual(
            index["billing"]["EVENT_TYPE"]["seat.assigned"],
            "EVENT_TYPE_SEAT_ASSIGNED",
        )
        self.assertEqual(
            index["billing"]["EVENT_TYPE"]["checkout.delivered"],
            "EVENT_TYPE_CHECKOUT_DELIVERED",
        )
        self.assertEqual(
            index["billing"]["SPAN_TYPE"]["llm-tokens"],
            "SPAN_TYPE_LLM_TOKENS",
        )

    def test_empty_inventory_yields_empty_index(self):
        index = tp.build_slug_index({"products": []})
        self.assertEqual(index, {})


class SlugConstantResolver(unittest.TestCase):
    def setUp(self):
        self.inventory = {
            "products": [
                {
                    "product_slug": "billing",
                    "constants": {
                        "EVENT_TYPE": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "METER_SLUG": [
                            {"name": "SEAT_ASSIGNED", "value": "seat.assigned"},
                        ],
                        "FEATURE_KEY": [
                            {"name": "ASSIGNED", "value": "assigned"},
                        ],
                        "PROVIDER": [],
                        "SPAN_TYPE": [
                            {"name": "LLM_TOKENS", "value": "llm-tokens"},
                        ],
                    },
                },
            ],
        }
        self.index = tp.build_slug_index(self.inventory)

    def test_resolve_event_type_constant(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="billing",
            event_type="seat.assigned", workflow_id="seat.assigned",
            cost_kind="llm-tokens",
        )
        self.assertEqual(consts["event_type_const"], "EVENT_TYPE_SEAT_ASSIGNED")
        self.assertEqual(consts["meter_slug_const"], "METER_SLUG_SEAT_ASSIGNED")
        self.assertEqual(consts["feature_key_const"], "FEATURE_KEY_ASSIGNED")
        self.assertEqual(consts["span_type_const"], "SPAN_TYPE_LLM_TOKENS")
        self.assertIsNone(consts["provider_const"])

    def test_unknown_product_returns_none_consts(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="unknown",
            event_type="x.y", workflow_id="x.y", cost_kind="z",
        )
        self.assertIsNone(consts["event_type_const"])
        self.assertIsNone(consts["meter_slug_const"])


class BuildSlugsEmitTasks(unittest.TestCase):
    def test_one_task_per_product(self):
        inventory = {
            "products": [
                {"product_slug": "billing", "constants": {}},
                {"product_slug": "analytics", "constants": {}},
            ],
        }
        tasks = tp.build_slugs_emit_tasks(inventory)
        self.assertEqual(len(tasks), 2)
        slugs = {t.product_slug for t in tasks}
        self.assertEqual(slugs, {"billing", "analytics"})

    def test_empty_inventory_yields_no_tasks(self):
        tasks = tp.build_slugs_emit_tasks({"products": []})
        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
