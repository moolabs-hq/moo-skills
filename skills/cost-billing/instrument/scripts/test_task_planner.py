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


class LoadSlugInventoryMalformedYaml(unittest.TestCase):
    """PR #5 review I-1 fix: malformed YAML degrades to empty inventory
    instead of crashing the planner with an uncaught yaml.YAMLError."""

    def test_malformed_yaml_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "broken.yaml"
            # Tab-after-colon in unquoted string is YAML-invalid.
            bad.write_text("products:\n  - product_slug: billing\n    bad: [ unclosed\n")
            data = tp.load_slug_inventory(bad)
            self.assertEqual(data, {"products": []})


class SlugConstantResolverSingleSegment(unittest.TestCase):
    """PR #5 review I-2 fix: workflow_id with no dots (e.g. "seat") must
    use the whole value as feature_key, not crash. Single-dot workflow_ids
    (e.g. "seat.assigned") must extract the second segment ("assigned")."""

    def setUp(self):
        inventory = {
            "products": [{
                "product_slug": "billing",
                "constants": {
                    "EVENT_TYPE": [{"name": "SEAT", "value": "seat"}],
                    "METER_SLUG": [{"name": "SEAT", "value": "seat"}],
                    "FEATURE_KEY": [
                        {"name": "SEAT", "value": "seat"},
                        {"name": "ASSIGNED", "value": "assigned"},
                    ],
                    "PROVIDER": [],
                    "SPAN_TYPE": [],
                },
            }],
        }
        self.index = tp.build_slug_index(inventory)

    def test_zero_dot_workflow_id_uses_whole_value(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="billing",
            event_type="seat", workflow_id="seat", cost_kind=None,
        )
        self.assertEqual(consts["feature_key_const"], "FEATURE_KEY_SEAT")

    def test_single_dot_workflow_id_uses_second_segment(self):
        consts = tp.resolve_slug_constants(
            self.index, product_slug="billing",
            event_type="seat", workflow_id="seat.assigned", cost_kind=None,
        )
        # Python resolver uses len(parts) >= 2 → second segment = "assigned"
        self.assertEqual(consts["feature_key_const"], "FEATURE_KEY_ASSIGNED")


class BuildTasksWiresSlugConstants(unittest.TestCase):
    """PR #5 review CRITICAL fix regression guard: build_tasks must wire
    slug_inventory through resolve_slug_constants so each Insert.entry
    carries event_type_const / meter_slug_const / feature_key_const /
    span_type_const + slugs_import_path. Without this wiring, the
    framework callsite templates emit string literals — defeating the
    slugs-as-source-of-truth contract."""

    def test_build_tasks_populates_entry_constants(self):
        slug_inventory = {
            "generated_at": "2026-06-06T00:00:00+00:00",
            "products": [{
                "product_slug": "billing",
                "constants": {
                    "EVENT_TYPE": [{"name": "SEAT_ASSIGNED", "value": "seat.assigned"}],
                    "METER_SLUG": [{"name": "SEAT_ASSIGNED", "value": "seat.assigned"}],
                    "FEATURE_KEY": [{"name": "ASSIGNED", "value": "assigned"}],
                    "PROVIDER": [],
                    "SPAN_TYPE": [{"name": "LLM_TOKENS", "value": "llm-tokens"}],
                },
            }],
        }
        usage_inv = {
            "entries": [{
                "file": "app/services/seat.py",
                "line": 10,
                "workflow_id": "seat.assigned",
                "event_type": "seat.assigned",
                "cost_kind": "llm-tokens",
                "product_slug": "billing",
            }],
        }
        cost_inv = {"entries": []}
        omap = {"edges": []}
        snapshot = {"capabilities": {}}
        signed = {
            "service_slug": "svc",
            "repo": {"languages": ["python"], "frameworks": ["fastapi"]},
        }
        repo_profile = {"language": "python", "framework": "fastapi"}
        tasks = tp.build_tasks(
            cost_inv, usage_inv, omap, snapshot, signed, repo_profile,
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[],
            slug_inventory=slug_inventory,
        )
        self.assertEqual(len(tasks), 1)
        self.assertEqual(len(tasks[0].inserts), 1)
        entry = tasks[0].inserts[0].entry
        # CRITICAL fix verification: these keys are now populated.
        self.assertEqual(entry["event_type_const"], "EVENT_TYPE_SEAT_ASSIGNED")
        self.assertEqual(entry["meter_slug_const"], "METER_SLUG_SEAT_ASSIGNED")
        self.assertEqual(entry["feature_key_const"], "FEATURE_KEY_ASSIGNED")
        self.assertEqual(entry["span_type_const"], "SPAN_TYPE_LLM_TOKENS")
        self.assertEqual(
            entry["slugs_import_path"],
            "app.services.moolabs.slugs_billing",
        )

    def test_build_tasks_without_slug_inventory_yields_none_consts(self):
        """When slug_inventory is None or empty, the const fields are None —
        the framework callsite templates fall back to inline literals."""
        usage_inv = {
            "entries": [{
                "file": "app/services/seat.py",
                "line": 10,
                "workflow_id": "seat.assigned",
                "event_type": "seat.assigned",
                "product_slug": "billing",
            }],
        }
        signed = {
            "service_slug": "svc",
            "repo": {"languages": ["python"], "frameworks": ["fastapi"]},
        }
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []},
            {"capabilities": {}}, signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[],
            slug_inventory=None,
        )
        if tasks:  # may be empty if no template — that's fine for this test
            entry = tasks[0].inserts[0].entry
            self.assertIsNone(entry["event_type_const"])

    def test_emit_tasks_yaml_serializes_none_const_as_yaml_null(self):
        """F (dogfood 2026-06-08): a None const was written as the YAML bareword
        `None`, which safe_load reloads as the STRING "None" — truthy in the
        template's `{% if entry.event_type_const %}` guard, producing
        `from app.slugs import None` (SyntaxError). None consts MUST round-trip to
        Python None (YAML `null`) so the guard stays falsy and the inline-literal
        fallback fires."""
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        usage_inv = {"entries": [{
            "file": "app/services/seat.py", "line": 10,
            "workflow_id": "seat.assigned", "event_type": "seat.assigned",
            "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=None)
        self.assertTrue(tasks, "fastapi+python should yield a task")
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "tasks.yaml"
            tp.emit_tasks_yaml(tasks, dest)
            text = dest.read_text()
            # raw serialization must NOT contain the Python bareword for a const
            self.assertNotIn("_const: None", text)
            reloaded = yaml.safe_load(text)
            entry = reloaded["tasks"][0]["inserts"][0]["entry"]
            for k in ("event_type_const", "meter_slug_const", "provider_const"):
                self.assertIsNone(entry[k], f"{k} must reload as None, not the string 'None'")

    def test_slugs_import_path_for_typescript_uses_at_aliased_path(self):
        self.assertEqual(
            tp._slugs_import_path_for("typescript", "billing"),
            "@/services/moolabs/slugs_billing",
        )

    def test_slugs_import_path_for_go_uses_internal_path(self):
        self.assertEqual(
            tp._slugs_import_path_for("go", "billing"),
            "internal/moolabsclient/slugs_billing",
        )

    def test_slugs_import_path_hyphenated_product_slug_uses_underscore(self):
        # PR #5 review M-3 sibling: hyphen in product_slug must become
        # underscore for import-path safety (Go package names + Python
        # module paths both reject hyphens).
        self.assertEqual(
            tp._slugs_import_path_for("python", "my-product"),
            "app.services.moolabs.slugs_my_product",
        )


class EnvWireTaskGapDetection(unittest.TestCase):
    """PR #531 follow-up: EnvWireTask must carry the infra_discovery_gap
    flag from config-wiring-plan through to tasks.yaml, so the execution
    agent can render a DEVELOPER ACTION REQUIRED block in the PR body
    when the scanner found no IaC. Each deployment_stub also carries a
    scope field for service-vs-repo partitioning."""

    def test_build_env_wire_tasks_reads_gap_flag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            plan_path = Path(t) / "config-wiring-plan.yaml"
            plan_path.write_text(
                'services:\n'
                '  - service_slug: "tiny"\n'
                '    mode: stub\n'
                '    settings_import_path: "app.services.moolabs_settings"\n'
                '    api_key_accessor: "get_settings().moolabs_api_key.get_secret_value()"\n'
                '    stub_emit_path: "app/services/moolabs_settings.py"\n'
                '    infra_discovery_gap: true\n'
                '    deployment_stubs: []\n'
            )
            tasks = tp.build_env_wire_tasks(plan_path)
            self.assertEqual(len(tasks), 1)
            self.assertTrue(tasks[0].infra_discovery_gap)

    def test_build_env_wire_tasks_defaults_gap_to_false(self):
        """Backward compat: plans from before the field existed have no
        infra_discovery_gap key. Default to False."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            plan_path = Path(t) / "config-wiring-plan.yaml"
            plan_path.write_text(
                'services:\n'
                '  - service_slug: "svc"\n'
                '    mode: modify\n'
                '    settings_import_path: "app.config"\n'
                '    api_key_accessor: "get_settings().moolabs_api_key"\n'
                '    stub_emit_path: null\n'
                '    deployment_stubs: []\n'
            )
            tasks = tp.build_env_wire_tasks(plan_path)
            self.assertFalse(tasks[0].infra_discovery_gap)

    def test_emit_tasks_yaml_includes_gap_flag_and_per_stub_scope(self):
        """The tasks.yaml output must include infra_discovery_gap AND each
        deployment_stub's scope so the execution agent reads them. Without
        this passthrough, the PR-body CHECKLIST never fires."""
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "tasks.yaml"
            env_wire_tasks = [
                tp.EnvWireTask(
                    task_id="env_wire_001_moo-arc",
                    service_slug="moo-arc",
                    mode="stub",
                    settings_import_path="app.services.moolabs_settings",
                    api_key_accessor="get_settings().moolabs_api_key.get_secret_value()",
                    stub_emit_path="app/services/moolabs_settings.py",
                    deployment_stubs=[
                        {"kind": "terraform",
                         "source_path": "infrastructure/terraform/modules/secrets/variables.tf",
                         "mode": "checklist_only",
                         "scope": "repo"},
                        {"kind": "dotenv_example",
                         "source_path": "services/moo-arc/.env.example",
                         "emit_path": "services/moo-arc/.env.example",
                         "mode": "append",
                         "scope": "service"},
                    ],
                    infra_discovery_gap=False,
                ),
            ]
            tp.emit_tasks_yaml([], out, env_wire_tasks=env_wire_tasks)
            parsed = yaml.safe_load(out.read_text())
            ewt = parsed["env_wire_tasks"][0]
            self.assertEqual(ewt["infra_discovery_gap"], False)
            scopes = {s["kind"]: s["scope"] for s in ewt["deployment_stubs"]}
            self.assertEqual(scopes["terraform"], "repo")
            self.assertEqual(scopes["dotenv_example"], "service")
            # source_path round-trips for repo-scope CHECKLIST rendering.
            tf_stub = next(s for s in ewt["deployment_stubs"] if s["kind"] == "terraform")
            self.assertEqual(
                tf_stub["source_path"],
                "infrastructure/terraform/modules/secrets/variables.tf",
            )

    def test_emit_tasks_yaml_gap_true_round_trips(self):
        """When the gap flag is True, it must serialize as YAML true and
        round-trip as Python bool."""
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "tasks.yaml"
            env_wire_tasks = [
                tp.EnvWireTask(
                    task_id="env_wire_001_tiny",
                    service_slug="tiny",
                    mode="stub",
                    settings_import_path="app.services.moolabs_settings",
                    api_key_accessor="get_settings().moolabs_api_key.get_secret_value()",
                    stub_emit_path="app/services/moolabs_settings.py",
                    deployment_stubs=[],
                    infra_discovery_gap=True,
                ),
            ]
            tp.emit_tasks_yaml([], out, env_wire_tasks=env_wire_tasks)
            parsed = yaml.safe_load(out.read_text())
            self.assertIs(parsed["env_wire_tasks"][0]["infra_discovery_gap"], True)


class TasksYamlTopLevelGeneratedAtQuoted(unittest.TestCase):
    """Dogfood #3: the top-level `generated_at` in emit_tasks_yaml was emitted
    UNQUOTED, so PyYAML safe_load coerces the ISO-8601 string to a
    datetime.datetime object. A downstream consumer doing string ops on it
    crashes. (The slugs_emit_tasks generated_at was already quoted; the
    top-level one at the head of tasks.yaml was missed by commit 42d51a8.)"""

    def test_top_level_generated_at_round_trips_as_str(self):
        import tempfile
        import yaml
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "tasks.yaml"
            tp.emit_tasks_yaml([], out)
            parsed = yaml.safe_load(out.read_text())
            self.assertIsInstance(
                parsed["generated_at"], str,
                "top-level generated_at must round-trip as str, not datetime "
                "(dogfood #3 — PyYAML coerces unquoted ISO-8601 to datetime)",
            )


class SlugResolutionRobustToMissingProductSlug(unittest.TestCase):
    """Dogfood #5 (architectural): slug resolution must NOT depend on the
    single-bucket accident. The downstream `_default_product_slug` fallback
    only works when every product collapses into one bucket (because discovery
    drops product_slug). The moment discovery is fixed to emit real per-entry
    product_slug, a multi-product index has >=2 buckets, `_default_product_slug`
    returns "" and resolution breaks again.

    The robust fix: when the declared product_slug is absent/unknown, resolve
    by the slug VALUE across all product buckets (event_type/workflow_id slug
    values are globally unique/namespaced), and use the product that owns the
    value for the import path."""

    def _multi_product_index(self):
        inventory = {
            "products": [
                {"product_slug": "arc", "constants": {
                    "EVENT_TYPE": [{"name": "ARC_SHARED_LLMPORT_CALL",
                                    "value": "arc.shared.llmport-call"}],
                    "METER_SLUG": [{"name": "ARC_SHARED_LLMPORT_CALL",
                                    "value": "arc.shared.llmport-call"}],
                    "FEATURE_KEY": [], "PROVIDER": [], "SPAN_TYPE": [],
                }},
                {"product_slug": "meter", "constants": {
                    "EVENT_TYPE": [{"name": "METER_INGEST_BATCH",
                                    "value": "meter.ingest.batch"}],
                    "METER_SLUG": [], "FEATURE_KEY": [], "PROVIDER": [],
                    "SPAN_TYPE": [],
                }},
            ],
        }
        return tp.build_slug_index(inventory)

    def test_resolves_in_multiproduct_index_with_no_declared_product(self):
        """The exact failure the fragile fallback masks: 2+ products, entry has
        no product_slug, but the event_type value is globally unique → must
        resolve to the owning product's constant (NOT None)."""
        idx = self._multi_product_index()
        # _default_product_slug returns "" here (2 products) — the old path misses.
        self.assertEqual(tp._default_product_slug(idx), "")
        eff = tp._effective_product_slug(
            idx, declared="", event_type="arc.shared.llmport-call",
            workflow_id="arc.shared.llmport-call",
        )
        self.assertEqual(eff, "arc")
        consts = tp.resolve_slug_constants(
            idx, product_slug=eff,
            event_type="arc.shared.llmport-call",
            workflow_id="arc.shared.llmport-call", cost_kind=None,
        )
        self.assertEqual(consts["event_type_const"], "EVENT_TYPE_ARC_SHARED_LLMPORT_CALL")

    def test_declared_product_still_wins_fast_path(self):
        idx = self._multi_product_index()
        eff = tp._effective_product_slug(
            idx, declared="meter", event_type="meter.ingest.batch",
            workflow_id="meter.ingest.batch",
        )
        self.assertEqual(eff, "meter")

    def test_unknown_value_returns_declared_or_empty(self):
        idx = self._multi_product_index()
        eff = tp._effective_product_slug(
            idx, declared="", event_type="nonexistent.value",
            workflow_id="nonexistent.value",
        )
        # Not found in any product, 2 products → "" (literal fallback downstream)
        self.assertEqual(eff, "")

    def test_single_product_still_resolves_via_default(self):
        """The moo-arc dogfood case: all entries collapsed into one bucket
        because discovery dropped product_slug. Must still resolve."""
        inventory = {"products": [{"product_slug": "default", "constants": {
            "EVENT_TYPE": [{"name": "X", "value": "x.y"}],
            "METER_SLUG": [], "FEATURE_KEY": [], "PROVIDER": [], "SPAN_TYPE": [],
        }}]}
        idx = tp.build_slug_index(inventory)
        eff = tp._effective_product_slug(idx, declared="", event_type="x.y",
                                         workflow_id="x.y")
        self.assertEqual(eff, "default")

    def test_value_in_two_products_warns_and_takes_first(self):
        """PR #8 review 2.1: when a slug value appears in >1 product (should be
        impossible given slug_inventory's duplicate guard, but defensive), the
        value-search must WARN and take the first owner — not crash, not pick
        silently. Locks the documented first-wins design choice."""
        import io
        import contextlib
        inventory = {"products": [
            {"product_slug": "arc", "constants": {
                "EVENT_TYPE": [{"name": "DUP", "value": "shared.dup"}],
                "METER_SLUG": [], "FEATURE_KEY": [], "PROVIDER": [], "SPAN_TYPE": [],
            }},
            {"product_slug": "meter", "constants": {
                "EVENT_TYPE": [{"name": "DUP", "value": "shared.dup"}],
                "METER_SLUG": [], "FEATURE_KEY": [], "PROVIDER": [], "SPAN_TYPE": [],
            }},
        ]}
        idx = tp.build_slug_index(inventory)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            owner = tp._product_owning_value(idx, "EVENT_TYPE", "shared.dup")
        # First-wins (dict insertion order preserves "arc" first).
        self.assertEqual(owner, "arc")
        self.assertIn("multiple products", stderr.getvalue())


class SlugsPathFromImportRule(unittest.TestCase):
    def test_slugs_import_path_uses_strategies_rule(self):
        # anchor_dir is the SERVICE-RELATIVE dir of the detected config; src/ is stripped
        path = tp.slugs_import_path("python", "billing", anchor_dir="src/myapp")
        self.assertEqual(path, "myapp.slugs_billing")

    def test_slugs_import_path_app_layout(self):
        self.assertEqual(tp.slugs_import_path("python", "billing", anchor_dir="app"),
                         "app.slugs_billing")

    def test_slugs_emit_path_from_stub(self):
        # given the service's stub emit_path, the slugs file is a sibling
        emit, imp = tp.slugs_paths_from_stub(
            "services/svc/src/myapp/moolabs_settings.py", "myapp.moolabs_settings",
            "billing", "py")
        self.assertEqual(emit, "services/svc/src/myapp/slugs_billing.py")
        self.assertEqual(imp, "myapp.slugs_billing")

    def test_slugs_paths_from_stub_bare_import_segment(self):
        # a bare (no-dot) stub import → whole segment replaced
        emit, imp = tp.slugs_paths_from_stub(
            "moolabs_settings.py", "moolabs_settings", "billing", "py")
        self.assertEqual(emit, "slugs_billing.py")
        self.assertEqual(imp, "slugs_billing")

    def test_slugs_paths_from_stub_hyphenated_product(self):
        # hyphens in product → underscores in both basename + import segment
        emit, imp = tp.slugs_paths_from_stub(
            "app/moolabs_settings.py", "app.moolabs_settings", "my-product", "py")
        self.assertEqual(emit, "app/slugs_my_product.py")
        self.assertEqual(imp, "app.slugs_my_product")


class StubAnchorDerivation(unittest.TestCase):
    """Task 12: stub_anchor picks the SOLE stub-mode env-wire task with a
    non-null stub_emit_path. Zero or >1 distinct stub → None (legacy fallback),
    since the inventory carries no product→service edge to disambiguate."""

    def _stub(self, slug, emit, imp, mode="stub"):
        return tp.EnvWireTask(
            task_id=f"ew_{slug}", service_slug=slug, mode=mode,
            settings_import_path=imp, api_key_accessor="a",
            stub_emit_path=emit, deployment_stubs=[],
        )

    def test_single_stub_yields_anchor(self):
        ewt = [self._stub("svc", "services/svc/src/myapp/moolabs_settings.py",
                           "myapp.moolabs_settings")]
        self.assertEqual(
            tp.stub_anchor(ewt),
            ("services/svc/src/myapp/moolabs_settings.py", "myapp.moolabs_settings"),
        )

    def test_no_env_wire_tasks_returns_none(self):
        self.assertIsNone(tp.stub_anchor(None))
        self.assertIsNone(tp.stub_anchor([]))

    def test_all_modify_mode_returns_none(self):
        # modify mode has no stub file to swap on → no anchor.
        ewt = [self._stub("svc", None, "app.config", mode="modify")]
        self.assertIsNone(tp.stub_anchor(ewt))

    def test_multiple_distinct_stubs_anchors_on_first_not_hardcode(self):
        # F2: multi-service must anchor on a REAL customer stub (the FIRST),
        # NEVER fall back to the app/services/moolabs literal.
        ewt = [
            self._stub("a", "services/a/app/moolabs_settings.py", "a.moolabs_settings"),
            self._stub("b", "services/b/app/moolabs_settings.py", "b.moolabs_settings"),
        ]
        anchor = tp.stub_anchor(ewt)
        self.assertEqual(anchor, ("services/a/app/moolabs_settings.py", "a.moolabs_settings"))
        self.assertNotIn("app/services/moolabs", anchor[0])

    def test_duplicate_stub_path_collapses_to_single_anchor(self):
        # same stub_emit_path twice (idempotent) → still a single anchor.
        ewt = [
            self._stub("a", "app/moolabs_settings.py", "app.moolabs_settings"),
            self._stub("a2", "app/moolabs_settings.py", "app.moolabs_settings"),
        ]
        self.assertEqual(
            tp.stub_anchor(ewt), ("app/moolabs_settings.py", "app.moolabs_settings"))


class BuildSlugsEmitTasksAnchor(unittest.TestCase):
    """Task 12: with a stub anchor, each SlugsEmitTask.slugs_emit_path is a
    sibling of the stub (real config package); without one it stays None so
    render_artifacts falls back to its legacy convention."""

    def _inv(self):
        return {"generated_at": "2026-06-06T00:00:00+00:00",
                "products": [{"product_slug": "billing", "constants": {}}]}

    def test_anchor_sets_slugs_emit_path(self):
        anchor = ("services/svc/src/myapp/moolabs_settings.py", "myapp.moolabs_settings")
        tasks = tp.build_slugs_emit_tasks(self._inv(), "python", anchor)
        self.assertEqual(tasks[0].slugs_emit_path,
                         "services/svc/src/myapp/slugs_billing.py")

    def test_no_anchor_leaves_slugs_emit_path_none(self):
        tasks = tp.build_slugs_emit_tasks(self._inv(), "python", None)
        self.assertIsNone(tasks[0].slugs_emit_path)

    def test_legacy_default_signature_still_works(self):
        # build_slugs_emit_tasks(inventory) — language/anchor default → no path.
        tasks = tp.build_slugs_emit_tasks(self._inv())
        self.assertIsNone(tasks[0].slugs_emit_path)
        self.assertEqual(tasks[0].product_slug, "billing")

    def test_typescript_emit_stays_none_matching_import_gate(self):
        # IMP-1: TS/Go slugs EMIT must be gated to legacy exactly like the
        # IMPORT (_slugs_import_for_entry is python-only) — else the slugs file
        # is written where no TS callsite imports it. Anchor-derived TS/Go is a
        # follow-up; until then emit AND import both use the legacy convention.
        anchor = ("src/moolabs-settings.ts", "@/moolabs-settings")
        tasks = tp.build_slugs_emit_tasks(self._inv(), "typescript", anchor)
        self.assertIsNone(tasks[0].slugs_emit_path)
        # The per-callsite import is also legacy for TS → emit + import agree.
        self.assertEqual(
            tp._slugs_import_for_entry("typescript", "billing", anchor),
            tp._slugs_import_path_for("typescript", "billing"))

    def test_go_emit_stays_none_matching_import_gate(self):
        anchor = ("internal/conf/settings.go", "internal/conf")
        tasks = tp.build_slugs_emit_tasks(self._inv(), "go", anchor)
        self.assertIsNone(tasks[0].slugs_emit_path)


class BuildTasksAnchorDerivedImport(unittest.TestCase):
    """Task 12: build_tasks' per-callsite slugs_import_path is anchor-derived
    for Python (so the import targets the slugs module emitted beside the
    stub), but falls back to the legacy convention for TS/Go (a dotted swap
    corrupts @/ aliases / slash paths)."""

    _slug_inv = {"products": [{"product_slug": "billing", "constants": {
        "EVENT_TYPE": [{"name": "SEAT_ASSIGNED", "value": "seat.assigned"}],
        "METER_SLUG": [], "FEATURE_KEY": [], "PROVIDER": [], "SPAN_TYPE": []}}]}

    def test_python_callsite_import_is_anchor_derived(self):
        usage_inv = {"entries": [{
            "file": "services/svc/src/myapp/seat.py", "line": 10,
            "workflow_id": "seat.assigned", "event_type": "seat.assigned",
            "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=self._slug_inv,
            anchor=("services/svc/src/myapp/moolabs_settings.py",
                    "myapp.moolabs_settings"))
        self.assertEqual(
            tasks[0].inserts[0].entry["slugs_import_path"], "myapp.slugs_billing")

    def test_typescript_callsite_import_falls_back_to_legacy(self):
        usage_inv = {"entries": [{
            "file": "src/seat.ts", "line": 5,
            "workflow_id": "seat.assigned", "event_type": "seat.assigned",
            "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["typescript"], "frameworks": ["express"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "typescript", "framework": "express"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=self._slug_inv,
            anchor=("src/moolabs-settings.ts", "@/moolabs-settings"))
        self.assertEqual(
            tasks[0].inserts[0].entry["slugs_import_path"],
            "@/services/moolabs/slugs_billing")

    def test_no_anchor_python_uses_legacy(self):
        usage_inv = {"entries": [{
            "file": "app/seat.py", "line": 10,
            "workflow_id": "seat.assigned", "event_type": "seat.assigned",
            "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=self._slug_inv, anchor=None)
        self.assertEqual(
            tasks[0].inserts[0].entry["slugs_import_path"],
            "app.services.moolabs.slugs_billing")


class EmitTasksYamlSlugsEmitPath(unittest.TestCase):
    """Task 12: emit_tasks_yaml writes slugs_emit_path into each
    slugs_emit_tasks entry (quoted), or null when absent, so Task 13
    (render_artifacts) can read it for the slugs file destination."""

    def test_slugs_emit_path_round_trips(self):
        import yaml
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "tasks.yaml"
            sets = [tp.SlugsEmitTask(
                task_id="slugs_emit_001_billing", product_slug="billing",
                constants={}, generated_at="2026-06-06T00:00:00+00:00",
                slugs_emit_path="services/svc/src/myapp/slugs_billing.py")]
            tp.emit_tasks_yaml([], out, slugs_emit_tasks=sets)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["slugs_emit_tasks"][0]["slugs_emit_path"],
                "services/svc/src/myapp/slugs_billing.py")

    def test_absent_slugs_emit_path_serializes_as_null(self):
        import yaml
        with tempfile.TemporaryDirectory() as t:
            out = Path(t) / "tasks.yaml"
            sets = [tp.SlugsEmitTask(
                task_id="slugs_emit_001_billing", product_slug="billing",
                constants={}, generated_at="2026-06-06T00:00:00+00:00",
                slugs_emit_path=None)]
            tp.emit_tasks_yaml([], out, slugs_emit_tasks=sets)
            parsed = yaml.safe_load(out.read_text())
            self.assertIsNone(parsed["slugs_emit_tasks"][0]["slugs_emit_path"])


class AttributionKeyContract(unittest.TestCase):
    """Sibling-of-E (found via the e2e render check): the callsite templates
    reference attribution_sources.{customer_id, request_id, consumer_agent}
    directly; under StrictUndefined an ABSENT key raises. The producer must
    guarantee all three keys exist (None for absent), regardless of what the
    attribution bindings populated."""

    def test_resolve_sources_always_has_canonical_keys(self):
        # defaults omit consumer_agent entirely
        out = tp._resolve_sources_for_file(
            "app/x.py", {"customer_id": "req.cid", "request_id": "req.rid"}, [])
        self.assertIn("consumer_agent", out)
        self.assertIsNone(out["consumer_agent"])
        self.assertEqual(out["customer_id"], "req.cid")

    def test_emitted_attribution_sources_includes_consumer_agent(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        usage_inv = {"entries": [{"file": "app/s.py", "line": 1,
                                  "workflow_id": "x.y", "event_type": "x.y",
                                  "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "c", "request_id": "r"},  # no consumer_agent
            attribution_overrides=[], slug_inventory=None)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "tasks.yaml"
            tp.emit_tasks_yaml(tasks, dest)
            reloaded = yaml.safe_load(dest.read_text())
        srcs = reloaded["tasks"][0]["inserts"][0]["attribution_sources"]
        self.assertIn("consumer_agent", srcs)
        self.assertIsNone(srcs["consumer_agent"])

    def test_binding_requires_import_threads_to_attribution_imports(self):
        # Dogfood ruff F821: a binding's requires_import must reach the insert so
        # the codemod emits it (else the rendered insert NameErrors on the symbol).
        tasks = tp.build_tasks(
            {"entries": []},
            {"entries": [{"file": "app/s.py", "line": 1, "workflow_id": "x.y",
                          "event_type": "x.y", "product_slug": "billing"}]},
            {"edges": []}, {"capabilities": {}},
            {"service_slug": "svc", "repo": {"languages": ["python"], "frameworks": ["fastapi"]}},
            {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "self.tenant_id",
                                  "request_id": "get_correlation_id()"},
            attribution_overrides=[], slug_inventory=None,
            attribution_import_defaults={
                "request_id": "from app.obs import get_correlation_id",
                # an import for a binding whose source is NOT used must be dropped:
                "consumer_agent": "from app.unused import nope"})
        imports = tasks[0].inserts[0].entry["attribution_imports"]
        self.assertEqual(imports, ["from app.obs import get_correlation_id"])

    def test_attribution_imports_empty_when_no_requires_import(self):
        tasks = tp.build_tasks(
            {"entries": []},
            {"entries": [{"file": "app/s.py", "line": 1, "workflow_id": "x.y",
                          "event_type": "x.y", "product_slug": "billing"}]},
            {"edges": []}, {"capabilities": {}},
            {"service_slug": "svc", "repo": {"languages": ["python"], "frameworks": ["fastapi"]}},
            {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "self.tenant_id", "request_id": "r"},
            attribution_overrides=[], slug_inventory=None)
        self.assertEqual(tasks[0].inserts[0].entry["attribution_imports"], [])


class DerivationCoercion(unittest.TestCase):
    """H (dogfood 2026-06-08): refund_unit.derivation must be a numeric scalar
    (rendered as `value=<derivation>`); prose must be coerced + preserved."""

    def test_prose_derivation_coerced_to_leading_scalar(self):
        out = tp._coerce_derivation(
            {"unit": "completion", "derivation": "1 apply_remittance completion (post-success)"})
        self.assertEqual(out["derivation"], 1)
        self.assertEqual(out["derivation_note"],
                         "1 apply_remittance completion (post-success)")

    def test_numeric_derivation_unchanged_no_note(self):
        out = tp._coerce_derivation({"unit": "completion", "derivation": 3})
        self.assertEqual(out["derivation"], 3)
        self.assertNotIn("derivation_note", out)

    def test_string_numeric_derivation_no_redundant_note(self):
        out = tp._coerce_derivation({"unit": "x", "derivation": "1"})
        self.assertEqual(out["derivation"], 1)
        self.assertNotIn("derivation_note", out)  # "1" == str(1) → no note

    def test_non_numeric_prose_defaults_to_one(self):
        out = tp._coerce_derivation({"unit": "x", "derivation": "per session"})
        self.assertEqual(out["derivation"], 1)
        self.assertEqual(out["derivation_note"], "per session")
        self.assertTrue(out["derivation_needs_review"])

    def test_runtime_expression_emitted_verbatim(self):
        # round-5: derivation is a runtime EXPRESSION (usage-events schema); a valid
        # expression MUST emit verbatim into value=<expr>, NOT collapse to 1.
        for expr in ("response.usage.completion_tokens", "len(text.split())",
                     "req.body.tokens"):
            out = tp._coerce_derivation({"unit": "x", "derivation": expr})
            self.assertEqual(out["derivation"], expr, f"{expr} must stay verbatim")
            self.assertNotIn("derivation_note", out)
            self.assertNotIn("derivation_needs_review", out)

    def test_prose_is_flagged_for_review(self):
        out = tp._coerce_derivation(
            {"unit": "x", "derivation": "1 apply_remittance completion"})
        self.assertEqual(out["derivation"], 1)
        self.assertTrue(out["derivation_needs_review"])

    def test_build_tasks_coerces_prose_derivation(self):
        usage_inv = {"entries": [{
            "file": "app/services/seat.py", "line": 10,
            "workflow_id": "seat.assigned", "event_type": "seat.assigned",
            "product_slug": "billing",
            "refund_unit": {"unit": "completion", "derivation": "1 _send_sms() completion"}}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=None)
        self.assertTrue(tasks)
        ru = tasks[0].inserts[0].entry["refund_unit"]
        self.assertEqual(ru["derivation"], 1)
        self.assertEqual(ru["derivation_note"], "1 _send_sms() completion")


class CostKindAndValueMissing(unittest.TestCase):
    """G (dogfood 2026-06-08): template reads entry.cost_kind (inventories may
    name it cost_dimension); a cost-bearing entry with no cost value source must
    be flagged loudly."""

    def _cost_only_inv(self, **entry_extra):
        base = {"file": "app/llm.py", "line": 5,
                "workflow_id": "arc.shared.llmport-call",
                "event_type": "arc.shared.llmport-call",
                "classification": "cost-only", "product_slug": "billing"}
        base.update(entry_extra)
        return {"entries": [base]}

    def _build(self, cost_inv):
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        return tp.build_tasks(
            cost_inv, {"entries": []}, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=None)

    def test_cost_dimension_mapped_to_cost_kind(self):
        tasks = self._build(self._cost_only_inv(
            cost_dimension="llm_tokens", cost_micros_source="resp.cm"))
        self.assertTrue(tasks)
        self.assertEqual(tasks[0].inserts[0].entry["cost_kind"], "llm_tokens")

    def test_explicit_cost_kind_preferred_over_dimension(self):
        tasks = self._build(self._cost_only_inv(
            cost_kind="gpu-seconds", cost_dimension="llm_tokens",
            cost_micros_source="resp.cm"))
        self.assertEqual(tasks[0].inserts[0].entry["cost_kind"], "gpu-seconds")

    def test_cost_bearing_without_value_source_flagged(self):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            tasks = self._build(self._cost_only_inv(cost_dimension="llm_tokens"))
        self.assertTrue(tasks[0].inserts[0].entry["cost_value_missing"])
        self.assertIn("cost_micros_source", buf.getvalue())  # loud stderr warning

    def test_cost_bearing_with_value_source_not_flagged(self):
        tasks = self._build(self._cost_only_inv(
            cost_dimension="llm_tokens", cost_micros_source="resp.cm"))
        self.assertFalse(tasks[0].inserts[0].entry["cost_value_missing"])

    def test_usage_only_never_flagged(self):
        usage_inv = {"entries": [{"file": "app/s.py", "line": 1,
                                  "workflow_id": "x.y", "event_type": "x.y",
                                  "product_slug": "billing"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": ["python"], "frameworks": ["fastapi"]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}},
            signed, {"language": "python", "framework": "fastapi"},
            attribution_defaults={"customer_id": "x", "request_id": "y"},
            attribution_overrides=[], slug_inventory=None)
        self.assertFalse(tasks[0].inserts[0].entry["cost_value_missing"])


class EmitTasksYamlEscaping(unittest.TestCase):
    """round-5 CRITICAL: the main tasks block emitted quote-bearing string values
    (helper_import, attribution bindings) WITHOUT escaping, so a TypeScript service
    (helper_import = `import {...} from "@/services/moolabs-client";`) produced a
    tasks.yaml that yaml.safe_load couldn't parse at all."""

    def _emit_reload(self, language, framework, attribution_defaults):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        usage_inv = {"entries": [{"file": "src/x", "line": 5,
                                  "workflow_id": "a.b.c", "event_type": "a.b",
                                  "product_slug": "p"}]}
        signed = {"service_slug": "svc",
                  "repo": {"languages": [language], "frameworks": [framework]}}
        tasks = tp.build_tasks(
            {"entries": []}, usage_inv, {"edges": []}, {"capabilities": {}}, signed,
            {"language": language, "framework": framework},
            attribution_defaults=attribution_defaults, attribution_overrides=[],
            slug_inventory=None)
        with tempfile.TemporaryDirectory() as d:
            dest = Path(d) / "tasks.yaml"
            tp.emit_tasks_yaml(tasks, dest)
            text = dest.read_text()
            return yaml.safe_load(text)  # raises ScannerError if unescaped

    def test_typescript_tasks_yaml_parses(self):
        # TS helper_import carries literal double-quotes.
        reloaded = self._emit_reload("typescript", "express",
                                     {"customer_id": "req.user.id", "request_id": "r"})
        hi = reloaded["tasks"][0]["helper_import"]
        self.assertIn("from", hi)
        self.assertIn('"@/services/moolabs-client"', hi)  # quotes survived intact

    def test_quote_bearing_attribution_binding_parses(self):
        reloaded = self._emit_reload(
            "python", "fastapi",
            {"customer_id": 'req.headers["x-customer"]', "request_id": "r"})
        srcs = reloaded["tasks"][0]["inserts"][0]["attribution_sources"]
        self.assertEqual(srcs["customer_id"], 'req.headers["x-customer"]')

    def test_mixed_quote_binding_parses(self):
        # round-6 CRITICAL: a binding with BOTH quote styles broke repr-based
        # _yaml_scalar (it emitted a single-quoted Python literal with \' that YAML
        # rejects). The double-quoted serializer handles it.
        reloaded = self._emit_reload(
            "python", "fastapi",
            {"customer_id": 'request.headers.get(\'X\', "")', "request_id": "r"})
        srcs = reloaded["tasks"][0]["inserts"][0]["attribution_sources"]
        self.assertEqual(srcs["customer_id"], 'request.headers.get(\'X\', "")')

    def test_yaml_scalar_roundtrips_all_special_chars(self):
        # _yaml_scalar is now the SINGLE emit serializer — it must round-trip every
        # special char through yaml.safe_load (round-6 root fix).
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        for v in ('plain', "a'b", 'a"b', 'a\'"b', 'l1\nl2', 'a\\b',
                  'svc: prod', 'a #c', '', '   ', '@alias'):
            with self.subTest(value=v):
                tok = tp._yaml_scalar(v)
                self.assertEqual(yaml.safe_load(f"k: {tok}")["k"], v)
        # non-strings
        self.assertEqual(tp._yaml_scalar(None), "null")
        self.assertEqual(tp._yaml_scalar(True), "true")
        self.assertEqual(tp._yaml_scalar(False), "false")
        self.assertEqual(tp._yaml_scalar(3), "3")


if __name__ == "__main__":
    unittest.main()
