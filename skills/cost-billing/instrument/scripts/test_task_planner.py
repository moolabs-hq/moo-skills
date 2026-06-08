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

    def test_multiple_distinct_stubs_returns_none(self):
        ewt = [
            self._stub("a", "services/a/app/moolabs_settings.py", "app.moolabs_settings"),
            self._stub("b", "services/b/app/moolabs_settings.py", "app.moolabs_settings"),
        ]
        self.assertIsNone(tp.stub_anchor(ewt))

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

    def test_typescript_ext_in_emit_basename(self):
        anchor = ("src/moolabs-settings.ts", "@/moolabs-settings")
        tasks = tp.build_slugs_emit_tasks(self._inv(), "typescript", anchor)
        self.assertEqual(tasks[0].slugs_emit_path, "src/slugs_billing.ts")


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


if __name__ == "__main__":
    unittest.main()
