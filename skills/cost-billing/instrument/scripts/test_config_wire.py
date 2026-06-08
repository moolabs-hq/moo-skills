#!/usr/bin/env python3
"""Unit tests for config_wire.py (Phase 1.7 env-wire orchestrator).

Stdlib unittest; runs in the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config_wire as cw  # noqa: E402


class LoadEnvRoutingInventory(unittest.TestCase):
    def test_load_basic_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "env-routing-inventory.yaml"
            inv.write_text(
                "generated_at: 2026-06-06T00:00:00+00:00\n"
                "granularity: per-service\n"
                "granularity_source: declared\n"
                "services:\n"
                "  - service_slug: payments-api\n"
                "    app_config:\n"
                "      pattern: python-pydantic-settings-v2\n"
                "      file: services/payments-api/app/config.py\n"
                "      line_to_insert: 5\n"
                "      confidence: high\n"
                "      stub_required: false\n"
                "      wire_target:\n"
                "        kind: \"add_pydantic_settings_field\"\n"
                "        field_template: \"moolabs_api_key: SecretStr\"\n"
                "    deployment_surfaces: []\n"
            )
            data = cw.load_env_routing_inventory(inv)
            self.assertEqual(data["granularity"], "per-service")
            self.assertEqual(len(data["services"]), 1)
            self.assertEqual(data["services"][0]["service_slug"], "payments-api")

    def test_load_missing_file_returns_empty(self):
        data = cw.load_env_routing_inventory(Path("/nonexistent/path.yaml"))
        self.assertEqual(data, {"services": []})


class PythonWireTargetDispatch(unittest.TestCase):
    """Derive settings_import_path + api_key_accessor for each recognized
    Python pattern. The helper template's _resolve_api_key() will render
    these verbatim."""

    def test_pydantic_settings_v2(self):
        service = {
            "service_slug": "payments-api",
            "app_config": {
                "node_id": "python-pydantic-settings-v2",
                "pattern": "python-pydantic-settings-v2",
                "file": "services/payments-api/app/config.py",
                "line_to_insert": 5,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_pydantic_settings_field",
                                "field_template": "moolabs_api_key: SecretStr"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        # settings module derived from file path: services/payments-api/app/config.py
        # → import via app.config
        self.assertEqual(plan["settings_import_path"], "app.config")
        # pydantic-settings v2 uses SecretStr — extracted via .get_secret_value()
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )

    def test_pydantic_v1_settings(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-pydantic-v1-settings",
                "pattern": "python-pydantic-v1-settings",
                "file": "svc/app/settings.py",
                "line_to_insert": 3,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_pydantic_settings_field",
                                "field_template": "moolabs_api_key: SecretStr"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        # modify imports the CUSTOMER's existing module (file-derived).
        self.assertEqual(plan["settings_import_path"], "app.settings")
        # accessor comes from the node's wiring, not a per-pattern hardcode.
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )

    def test_python_decouple_routes_to_stub(self):
        """Direct-export pattern (decouple) — the node declares wiring.mode=stub
        (the modify accessor would be a bare identifier the helper doesn't
        import). Stub paths come from the inventory (emit_path/import_path)."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-decouple",
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
                "emit_path": "svc/app/moolabs_settings.py",
                "import_path": "app.moolabs_settings",
                "line_to_insert": 10,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_decouple_line",
                                "line_template": "MOOLABS_API_KEY = config('MOOLABS_API_KEY')"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        # stub import path is inventory-derived, not the old hardcode.
        self.assertEqual(plan["settings_import_path"], "app.moolabs_settings")
        self.assertEqual(plan["stub_emit_path"], "svc/app/moolabs_settings.py")
        # the stub always exposes get_settings(); accessor is the constant body.
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )

    def test_python_dotenv_os_getenv_routes_to_stub(self):
        """Same root cause as decouple — dotenv-os-getenv's node declares
        wiring.mode=stub. Stub paths are inventory-derived."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-dotenv-os-getenv",
                "pattern": "python-dotenv-os-getenv",
                "file": "svc/app/config.py",
                "emit_path": "svc/app/moolabs_settings.py",
                "import_path": "app.moolabs_settings",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MOOLABS_API_KEY = os.getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "app.moolabs_settings")

    def test_pydantic_settings_subclass_routes_to_stub(self):
        """Dogfood #1b: the project-base Settings pattern (a class extending a
        custom base, often with a module-level `settings` instance and no
        get_settings()) MUST route to stub — the modify-mode accessor
        `get_settings()...` would break a config that doesn't expose it. The
        node declares wiring.mode=stub (the stub provides its own
        get_settings()). Stub paths are inventory-derived."""
        service = {
            "service_slug": "moo-arc",
            "app_config": {
                "node_id": "python-pydantic-settings-subclass",
                "pattern": "python-pydantic-settings-subclass",
                "file": "services/moo-arc/app/config.py",
                "emit_path": "services/moo-arc/app/moolabs_settings.py",
                "import_path": "app.moolabs_settings",
                "line_to_insert": 14,
                "confidence": "high",
                "stub_required": False,  # high confidence, but node mode=stub
                "wire_target": {"kind": "add_pydantic_settings_field"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "app.moolabs_settings")
        self.assertEqual(
            plan["stub_emit_path"], "services/moo-arc/app/moolabs_settings.py"
        )


class TypeScriptWireTargetDispatch(unittest.TestCase):
    """All three recognized TS patterns route to stub mode.

    The helper template (typescript-moolabs-client.ts.j2) unconditionally
    imports `getSettings` and renders the accessor as the body of
    resolveApiKey(). None of the recognized TS patterns export a
    getSettings function — they export `env` (zod), or a const
    `MOOLABS_API_KEY` (process-env-direct / env-var-library). Modify mode
    for these patterns would produce TS compile errors AND runtime
    ReferenceError. Stub mode emits a getSettings() wrapper the customer
    can adopt. PR #4 review CRIT-2 fix."""

    def test_zod_env_schema_routes_to_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "ts-zod-env-schema",
                "pattern": "ts-zod-env-schema",
                "file": "src/env.ts",
                "emit_path": "src/moolabs-settings.ts",
                "import_path": "@/moolabs-settings",
                "line_to_insert": 5,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_zod_field",
                                "field_template": "MOOLABS_API_KEY: z.string().min(1)"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        # stub import path is inventory-derived, not the old hardcode.
        self.assertEqual(plan["settings_import_path"], "@/moolabs-settings")
        self.assertEqual(plan["stub_emit_path"], "src/moolabs-settings.ts")
        self.assertEqual(plan["api_key_accessor"], "getSettings().MOOLABS_API_KEY")

    def test_process_env_direct_routes_to_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "ts-process-env-direct",
                "pattern": "ts-process-env-direct",
                "file": "src/config.ts",
                "emit_path": "src/moolabs-settings.ts",
                "import_path": "@/moolabs-settings",
                "line_to_insert": 12,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_process_env_line",
                                "line_template": "export const MOOLABS_API_KEY = process.env.MOOLABS_API_KEY ?? \"\";"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "@/moolabs-settings")

    def test_env_var_library_routes_to_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "ts-env-var-library",
                "pattern": "ts-env-var-library",
                "file": "src/env.ts",
                "emit_path": "src/moolabs-settings.ts",
                "import_path": "@/moolabs-settings",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_env_var_line",
                                "line_template": "export const MOOLABS_API_KEY = env.get(\"MOOLABS_API_KEY\").required().asString();"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "@/moolabs-settings")


class GoWireTargetDispatch(unittest.TestCase):
    def test_viper_routes_to_stub(self):
        """go-viper's node declares wiring.mode=stub (viper needs a separate
        import the template doesn't carry). Stub paths are inventory-derived."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "go-viper",
                "pattern": "go-viper",
                "file": "internal/config/config.go",
                "emit_path": "internal/moolabsconfig/settings.go",
                "import_path": "internal/moolabsconfig",
                "line_to_insert": 14,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_viper_bindenv",
                                "line_template": "viper.BindEnv(\"moolabs_api_key\", \"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "internal/moolabsconfig")
        self.assertEqual(
            plan["stub_emit_path"], "internal/moolabsconfig/settings.go"
        )

    def test_envconfig(self):
        """go-envconfig is the only Go pattern that works in modify mode.
        Customer's config package exports Get() returning a struct with
        MoolabsAPIKey — composes correctly with the template's `config`
        import alias."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "go-envconfig",
                "pattern": "go-envconfig",
                "file": "internal/config/config.go",
                "line_to_insert": 6,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_envconfig_field",
                                "field_template": "MoolabsAPIKey string `envconfig:\"MOOLABS_API_KEY\"`"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        # modify imports the CUSTOMER's existing config package (file-derived).
        self.assertEqual(plan["settings_import_path"], "internal/config")
        # accessor comes from the node's wiring.
        self.assertEqual(plan["api_key_accessor"], "config.Get().MoolabsAPIKey")

    def test_go_os_getenv_routes_to_stub(self):
        """go-os-getenv's node declares wiring.mode=stub (stdlib os leaves the
        config import dead, a Go compile error). Stub paths inventory-derived."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "go-os-getenv",
                "pattern": "go-os-getenv",
                "file": "internal/config/config.go",
                "emit_path": "internal/moolabsconfig/settings.go",
                "import_path": "internal/moolabsconfig",
                "line_to_insert": 5,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MoolabsAPIKey := os.Getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "internal/moolabsconfig")


class StubModeFallback(unittest.TestCase):
    def test_unrecognized_pattern_triggers_stub(self):
        """No node resolves (unrecognized service) → stub mode. Stub paths come
        from the inventory's emit_path/import_path (Phase A derived them from
        the customer's real layout)."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "unrecognized",
                "emit_path": "app/services/moolabs_settings.py",
                "import_path": "app.services.moolabs_settings",
                "confidence": "none",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        # Stub Settings file path comes from the inventory.
        self.assertEqual(plan["stub_emit_path"], "app/services/moolabs_settings.py")
        # Accessor reads from the stub (constant body).
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )
        self.assertEqual(plan["settings_import_path"], "app.services.moolabs_settings")

    def test_unrecognized_without_inventory_paths_yields_none(self):
        """Degenerate input — no node, no inventory emit_path/import_path. Stub
        mode still applies but the paths are None (no hardcoded fallback)."""
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        self.assertIsNone(plan["stub_emit_path"])
        self.assertIsNone(plan["settings_import_path"])

    def test_stub_required_true_overrides_recognized_pattern(self):
        """python-decouple's node declares wiring.mode=stub regardless of
        confidence, so this routes to stub via the node, not the old
        stub_required gate."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-decouple",
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
                "emit_path": "svc/app/moolabs_settings.py",
                "import_path": "app.moolabs_settings",
                "confidence": "low",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")

    def test_typescript_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "unrecognized",
                "emit_path": "src/services/moolabs-settings.ts",
                "import_path": "@/services/moolabs-settings",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "src/services/moolabs-settings.ts")

    def test_go_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "unrecognized",
                "emit_path": "internal/moolabsconfig/settings.go",
                "import_path": "internal/moolabsconfig",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "internal/moolabsconfig/settings.go")


class DeploymentSurfacePlan(unittest.TestCase):
    def test_per_surface_emit_paths(self):
        service = {
            "service_slug": "payments-api",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "terraform", "path": "infra/terraform/payments-api/variables.tf",
                 "insert_kind": "variable_block_append"},
                {"kind": "k8s", "path": "infra/k8s/payments-api/deployment.yaml",
                 "insert_kind": "secret_ref_checklist"},
                {"kind": "dotenv_example", "path": "services/payments-api/.env.example",
                 "insert_kind": "line_append"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(len(stubs), 3)
        kinds = {s["kind"] for s in stubs}
        self.assertEqual(kinds, {"terraform", "k8s", "dotenv_example"})
        terraform = next(s for s in stubs if s["kind"] == "terraform")
        # Stub file goes ALONGSIDE the existing infra (never modifying it).
        self.assertEqual(terraform["emit_path"],
                         "infra/terraform/payments-api/moolabs.tf")
        k8s = next(s for s in stubs if s["kind"] == "k8s")
        self.assertEqual(k8s["emit_path"],
                         "infra/k8s/payments-api/secret-moolabs.yaml")
        dotenv = next(s for s in stubs if s["kind"] == "dotenv_example")
        # .env.example gets a line APPENDED — same file, not a new one.
        self.assertEqual(dotenv["emit_path"],
                         "services/payments-api/.env.example")
        self.assertEqual(dotenv["mode"], "append")

    def test_dockerfile_emits_checklist_only(self):
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "dockerfile", "path": "services/svc/Dockerfile",
                 "insert_kind": "checklist_only"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(len(stubs), 1)
        self.assertEqual(stubs[0]["mode"], "checklist_only")
        self.assertNotIn("emit_path", stubs[0])


class RepoScopeSurfacePlan(unittest.TestCase):
    """Regression guard for PR #531 fix — repo-scope (centralized infra)
    surfaces must be downgraded to checklist_only. Auto-emitting a
    moolabs.tf alongside `infrastructure/terraform/modules/secrets/
    variables.tf` would commit a shared-infra change without the customer
    realizing it affects every service simultaneously."""

    def test_repo_scope_terraform_is_checklist_only(self):
        service = {
            "service_slug": "moo-arc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "terraform",
                 "path": "infrastructure/terraform/modules/secrets/variables.tf",
                 "insert_kind": "variable_block_append",
                 "scope": "repo"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(len(stubs), 1)
        # repo-scope downgrades to checklist_only — NEVER auto-emit a
        # moolabs.tf alongside centralized infra (cross-service blast radius).
        self.assertEqual(stubs[0]["mode"], "checklist_only")
        self.assertEqual(stubs[0]["scope"], "repo")
        # No emit_path because no file is being written.
        self.assertNotIn("emit_path", stubs[0])
        # source_path preserved so the instrument layer can name the file
        # the developer must edit by hand.
        self.assertEqual(
            stubs[0]["source_path"],
            "infrastructure/terraform/modules/secrets/variables.tf",
        )

    def test_service_scope_terraform_still_auto_emits(self):
        """Per-service infra (services/<svc>/infra/) is safe to auto-modify
        — emit moolabs.tf as before."""
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                {"kind": "terraform",
                 "path": "services/svc/infra/terraform/variables.tf",
                 "insert_kind": "variable_block_append",
                 "scope": "service"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        self.assertEqual(stubs[0]["mode"], "new_file")
        self.assertEqual(stubs[0]["scope"], "service")
        self.assertEqual(
            stubs[0]["emit_path"],
            "services/svc/infra/terraform/moolabs.tf",
        )

    def test_infra_discovery_gap_propagates_to_plan(self):
        """When the inventory sets infra_discovery_gap=True, the plan must
        carry it forward so the instrument layer can surface the DEVELOPER
        ACTION REQUIRED checklist in the PR body."""
        service = {
            "service_slug": "svc",
            "infra_discovery_gap": True,
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertTrue(plan["infra_discovery_gap"])

    def test_default_scope_is_service_for_back_compat(self):
        """Inventories from before the scope field existed have no scope
        key on each surface. Default to service-scope (the old behavior)."""
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [
                # No "scope" key — pre-fix inventory shape
                {"kind": "terraform", "path": "services/svc/infra/variables.tf",
                 "insert_kind": "variable_block_append"},
            ],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        stubs = plan.get("deployment_stubs", [])
        # Falls back to service-scope auto-emit (preserves pre-fix behavior).
        self.assertEqual(stubs[0]["mode"], "new_file")


class BuildPlanFromInventory(unittest.TestCase):
    def test_build_plan_one_service(self):
        inventory = {
            "granularity": "per-service",
            "services": [
                {
                    "service_slug": "svc-a",
                    "app_config": {
                        "node_id": "python-pydantic-settings-v2",
                        "pattern": "python-pydantic-settings-v2",
                        "file": "svc-a/app/config.py",
                        "stub_required": False,
                    },
                    "deployment_surfaces": [],
                },
            ],
        }
        plan = cw.build_plan(inventory, services_languages={"svc-a": "python"})
        self.assertEqual(len(plan["services"]), 1)
        self.assertEqual(plan["services"][0]["mode"], "modify")

    def test_build_plan_unknown_language_falls_back_to_python(self):
        inventory = {
            "services": [
                {"service_slug": "svc",
                 "app_config": {
                     "pattern": "unrecognized",
                     "emit_path": "app/services/moolabs_settings.py",
                     "import_path": "app.services.moolabs_settings",
                 },
                 "deployment_surfaces": []},
            ],
        }
        plan = cw.build_plan(inventory, services_languages={})
        # No language declared → default to python (Phase A's
        # parse_services_and_granularity makes the same fallback). No node
        # resolves the unrecognized pattern → stub; paths from the inventory.
        self.assertEqual(plan["services"][0]["mode"], "stub")
        self.assertEqual(
            plan["services"][0]["stub_emit_path"],
            "app/services/moolabs_settings.py",
        )


class YamlEmit(unittest.TestCase):
    def test_emit_yaml_roundtrips(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config-wiring-plan.yaml"
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [
                    {
                        "service_slug": "svc",
                        "mode": "modify",
                        "settings_import_path": "app.config",
                        "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
                        "stub_emit_path": None,
                        "deployment_stubs": [
                            {"kind": "dotenv_example",
                             "source_path": ".env.example",
                             "emit_path": ".env.example",
                             "mode": "append"},
                        ],
                    },
                ],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(parsed["services"][0]["service_slug"], "svc")
            self.assertEqual(parsed["services"][0]["mode"], "modify")
            self.assertEqual(
                parsed["services"][0]["api_key_accessor"],
                "get_settings().moolabs_api_key.get_secret_value()",
            )
            # M-3 regression guard: generated_at must round-trip as a string,
            # not auto-coerced to datetime by PyYAML's implicit ISO-8601 parse.
            self.assertIsInstance(
                parsed["generated_at"], str,
                "generated_at must round-trip as str (M-3 regression guard)",
            )

    def test_emit_yaml_service_slug_with_yaml_metachar_roundtrips(self):
        """M-4 regression guard: service_slug containing YAML metacharacters
        (`:`, `#`) must round-trip cleanly. Unquoted scalar would break parse."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.yaml"
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [{
                    "service_slug": "svc:weird#case",
                    "mode": "stub",
                    "settings_import_path": "app.services.moolabs_settings",
                    "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
                    "stub_emit_path": "app/services/moolabs_settings.py",
                    "deployment_stubs": [],
                }],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(parsed["services"][0]["service_slug"], "svc:weird#case")

    def test_emit_yaml_handles_backslash_in_accessor(self):
        """Defensive against the Phase A YAML escape bug class. Accessors
        could legitimately contain `\` (regex literals, escape sequences)."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.yaml"
            accessor_with_bs = r'get_settings()["api\nkey"]'
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [{
                    "service_slug": "svc",
                    "mode": "modify",
                    "settings_import_path": "app.config",
                    "api_key_accessor": accessor_with_bs,
                    "stub_emit_path": None,
                    "deployment_stubs": [],
                }],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["services"][0]["api_key_accessor"],
                accessor_with_bs,
            )

    def test_emit_yaml_none_settings_import_path_is_null(self):
        """Node-driven refactor: settings_import_path is inventory-derived and
        may be None for a degenerate stub with no inventory path. The emitter
        must write YAML `null` (round-trips to None), NOT the literal string
        "None" (which would render `from None import get_settings`)."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plan.yaml"
            plan = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "services": [{
                    "service_slug": "svc",
                    "mode": "stub",
                    "settings_import_path": None,
                    "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
                    "stub_emit_path": None,
                    "deployment_stubs": [],
                }],
            }
            cw.emit_config_wiring_plan_yaml(plan, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertIsNone(parsed["services"][0]["settings_import_path"])
            # Defend against the literal-"None" leak explicitly.
            self.assertNotEqual(
                parsed["services"][0]["settings_import_path"], "None"
            )


def _modify_accessors(language: str) -> dict[str, str]:
    """Pull {node_id: accessor} for every modify-mode node in a language from
    the framework registry — the source of truth that replaced the old
    _LANG_PATTERN_ACCESSORS maps."""
    reg = cw._registry()
    return {
        node.id: node.wiring.get("accessor", "")
        for node in reg.get(language, {}).values()
        if node.wiring.get("mode") == "modify"
    }


class AccessorRuntimeRegression(unittest.TestCase):
    """Regression guard against PR #4 review C-1/C-2/C-3. Every modify-mode
    accessor declared in the framework registry must compose with the helper
    template's import contract — i.e. when the helper renders
    `return <accessor>`, the accessor must be a valid expression in the
    helper's scope (`get_settings` is imported; nothing else).

    Earlier accessors like `"MOOLABS_API_KEY"` or `viper.GetString(...)`
    rendered to expressions referencing undefined names. py-compile / gofmt
    passed (syntactically valid) but the rendered helper crashed at runtime
    with NameError / undefined: viper. This test would have caught the bug
    by executing the rendered _resolve_api_key with a mocked get_settings.
    The accessor source moved (registry, not hardcoded maps) — the runtime
    composition check is unchanged.
    """

    def test_every_python_accessor_executes_without_nameerror(self):
        """For every modify-mode python node, render the helper and execute
        _resolve_api_key. Mocked get_settings returns an object compatible
        with the accessor expression."""
        from jinja2 import Environment, FileSystemLoader
        tpl_dir = Path(__file__).resolve().parents[1] / "assets" / "codemod-templates"
        env = Environment(loader=FileSystemLoader(str(tpl_dir)))
        accessors = _modify_accessors("python")
        self.assertTrue(accessors, "expected at least one modify-mode python node")
        for pattern, accessor in accessors.items():
            with self.subTest(pattern=pattern):
                ctx = {
                    'service_slug': 'svc',
                    'signoff_chain_hashes': [],
                    'sdk_pinned_version': 'v0.3.0-rc1',
                    'telemetry': {'mode': 'greenfield'},
                    'generated_at': '2026-06-06',
                    'env_config': {
                        'mode': 'modify',
                        'settings_import_path': 'mock.settings',
                        'api_key_accessor': accessor,
                        'stub_emit_path': None,
                    },
                }
                rendered = env.get_template('python-moolabs-client.py.j2').render(**ctx)
                # Stub external imports so the module loads in isolation.
                rendered = rendered.replace(
                    "from mock.settings import get_settings",
                    "class _MS:\n"
                    "    class _Key:\n"
                    "        def get_secret_value(self):\n"
                    "            return 'mock-key'\n"
                    "    moolabs_api_key = _Key()\n"
                    "def get_settings():\n"
                    "    return _MS()",
                ).replace(
                    "import structlog",
                    "structlog = type('structlog', (), {'get_logger': lambda *a, **k: None})()",
                ).replace(
                    "from moolabs import Moolabs",
                    "Moolabs = type('Moolabs', (), {})",
                )
                ns: dict = {}
                exec(compile(rendered, f'<{pattern}>', 'exec'), ns)
                result = ns['_resolve_api_key']()
                self.assertEqual(
                    result, 'mock-key',
                    f"_resolve_api_key for pattern {pattern} returned {result!r}",
                )

    def _node_mode(self, node_id: str, language: str) -> str | None:
        node = cw._resolve_node(node_id, language)
        return node.wiring.get("mode") if node is not None else None

    def test_python_decouple_routes_to_stub_via_node(self):
        """C-1 regression guard: python-decouple's accessor was a bare
        identifier (NameError). Its node now declares wiring.mode=stub — the
        registry is the source of truth that keeps it out of modify mode."""
        self.assertEqual(self._node_mode("python-decouple", "python"), "stub")
        self.assertEqual(
            self._node_mode("python-dotenv-os-getenv", "python"), "stub"
        )

    def test_no_ts_nodes_are_modify(self):
        """C-2 regression guard: all 3 TS patterns produced TS compile
        errors AND runtime ReferenceError. Every TS node must declare
        wiring.mode=stub until pattern-aware TS template variants exist."""
        ts_nodes = cw._registry().get("typescript", {})
        self.assertTrue(ts_nodes, "expected typescript nodes in the registry")
        for node in ts_nodes.values():
            self.assertEqual(
                node.wiring.get("mode"), "stub",
                f"TS node {node.id} must be stub mode",
            )

    def test_go_viper_and_os_getenv_route_to_stub_via_node(self):
        """C-3 regression guard: go-viper needed a separate viper import,
        go-os-getenv left the config import unused. Both nodes declare
        wiring.mode=stub; go-envconfig is the only modify-mode Go node."""
        self.assertEqual(self._node_mode("go-viper", "go"), "stub")
        self.assertEqual(self._node_mode("go-os-getenv", "go"), "stub")
        self.assertEqual(self._node_mode("go-envconfig", "go"), "modify")


class NodeDrivenWiring(unittest.TestCase):
    def test_stub_emit_path_comes_from_inventory_not_hardcode(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-pydantic-settings-subclass",
                "pattern": "python-pydantic-settings-subclass",
                "file": "src/myapp/config.py",
                "emit_path": "src/myapp/moolabs_settings.py",
                "import_path": "myapp.moolabs_settings",
                "confidence": "high",
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "src/myapp/moolabs_settings.py")
        self.assertEqual(plan["settings_import_path"], "myapp.moolabs_settings")

    def test_modify_uses_node_accessor_and_customer_module(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "node_id": "python-pydantic-settings-v2",
                "pattern": "python-pydantic-settings-v2",
                "file": "services/svc/app/config.py",
                "emit_path": "services/svc/app/moolabs_settings.py",
                "import_path": "app.moolabs_settings",
                "confidence": "high",
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"],
                         "get_settings().moolabs_api_key.get_secret_value()")
        # modify imports the CUSTOMER module (file-derived), not the stub
        self.assertEqual(plan["settings_import_path"], "app.config")


if __name__ == "__main__":
    unittest.main()
