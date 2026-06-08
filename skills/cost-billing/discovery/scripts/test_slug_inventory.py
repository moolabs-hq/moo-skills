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


class DeriveProviders(unittest.TestCase):
    def test_providers_from_catalog(self):
        cost_inv = {"entries": []}
        usage_inv = {"entries": []}
        omap = {"edges": []}
        provider_catalog = {
            "providers": [
                {"slug": "openai", "name": "OpenAI"},
                {"slug": "anthropic", "name": "Anthropic"},
                {"slug": "stripe", "name": "Stripe"},
            ],
        }
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog
        )
        # PROVIDER constants are global across products — every product
        # gets the same enum from the catalog.
        # For an empty inventory we expect at least one "default" product
        # entry carrying the providers.
        self.assertIn("default", by_product)
        providers = by_product["default"]["PROVIDER"]
        names = {e["name"] for e in providers}
        self.assertEqual(names, {"OPENAI", "ANTHROPIC", "STRIPE"})


class DeriveSpanTypes(unittest.TestCase):
    def test_span_types_from_cost_kind(self):
        cost_inv = {
            "entries": [
                {"workflow_id": "x", "event_type": "x", "cost_kind": "llm-tokens",
                 "product_slug": "billing"},
                {"workflow_id": "y", "event_type": "y", "cost_kind": "gpu-seconds",
                 "product_slug": "billing"},
                {"workflow_id": "z", "event_type": "z", "cost_kind": "llm-tokens",
                 "product_slug": "billing"},  # duplicate cost_kind — de-duped
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        span_types = by_product["billing"]["SPAN_TYPE"]
        names = {e["name"] for e in span_types}
        self.assertEqual(names, {"LLM_TOKENS", "GPU_SECONDS"})


class DuplicateDetection(unittest.TestCase):
    def test_duplicate_name_in_same_category_raises(self):
        # Two cost-event entries with workflow_ids that collapse to the
        # same UPPER_SNAKE_CASE name → CRITICAL: refuse-to-run.
        cost_inv = {
            "entries": [
                {"workflow_id": "checkout.recommendation",
                 "event_type": "checkout.recommendation",
                 "product_slug": "billing"},
                {"workflow_id": "checkout-recommendation",  # same canonical name
                 "event_type": "checkout-recommendation",
                 "product_slug": "billing"},
            ],
        }
        usage_inv = {"entries": []}
        omap = {"edges": []}
        by_product = si.derive_per_product_constants(
            cost_inv, usage_inv, omap, provider_catalog=None
        )
        errors = si.check_duplicates(by_product)
        # The EVENT_TYPE bucket gets CHECKOUT_RECOMMENDATION twice from
        # two different value strings — that's a name collision.
        # NOTE: _add_unique() dedupes by name, so only one entry exists;
        # check_duplicates() catches the SOURCE collision by comparing
        # raw inventory entries directly. See the implementation.
        self.assertTrue(any("CHECKOUT_RECOMMENDATION" in e for e in errors),
                        f"Expected name collision in errors: {errors}")


class YamlEmit(unittest.TestCase):
    def test_emit_yaml_contains_per_product_blocks(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "slug-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
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
                            "SPAN_TYPE": [],
                        },
                    },
                ],
            }
            si.emit_slug_inventory_yaml(inventory, out)
            content = out.read_text()
            self.assertIn("product_slug: billing", content)
            self.assertIn("EVENT_TYPE:", content)
            self.assertIn("name: SEAT_ASSIGNED", content)
            self.assertIn('value: "seat.assigned"', content)


class YamlEmitRoundtrip(unittest.TestCase):
    """Regression guard for the YAML escape bug class. The hand-rolled emitter
    must round-trip through PyYAML preserving the exact field values,
    including values with backslashes (workflow_ids constructed from regex
    patterns or path-like keys) and quotes."""

    def test_emit_roundtrips_through_pyyaml(self):
        import yaml
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "slug-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
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
                            "PROVIDER": [
                                {"name": "OPENAI", "value": "openai"},
                            ],
                            "SPAN_TYPE": [
                                {"name": "LLM_TOKENS", "value": "llm-tokens"},
                            ],
                        },
                    },
                ],
            }
            si.emit_slug_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(len(parsed["products"]), 1)
            product = parsed["products"][0]
            self.assertEqual(product["product_slug"], "billing")
            event_types = product["constants"]["EVENT_TYPE"]
            self.assertEqual(event_types[0]["value"], "seat.assigned")
            # PR #8 review #3-sibling guard: generated_at must round-trip as a
            # string, not be coerced to datetime (build_slugs_emit_tasks reads
            # it and re-emits str(datetime) — space not T — into tasks.yaml).
            self.assertIsInstance(parsed["generated_at"], str)

    def test_emit_handles_backslash_in_value(self):
        """Slug values constructed from regex patterns or path-like keys
        could contain backslashes. The emitter must double-escape them
        so PyYAML doesn't interpret `\\n` as newline etc."""
        import yaml
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "slug-inventory.yaml"
            value_with_backslash = r"foo\bar.baz"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "products": [
                    {
                        "product_slug": "test",
                        "constants": {
                            "EVENT_TYPE": [
                                {"name": "FOO_BAR_BAZ", "value": value_with_backslash},
                            ],
                            "METER_SLUG": [],
                            "FEATURE_KEY": [],
                            "PROVIDER": [],
                            "SPAN_TYPE": [],
                        },
                    },
                ],
            }
            si.emit_slug_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["products"][0]["constants"]["EVENT_TYPE"][0]["value"],
                value_with_backslash,
            )


if __name__ == "__main__":
    unittest.main()
