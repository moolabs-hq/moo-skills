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
        self.assertEqual(plan["settings_import_path"], "app.settings")

    def test_python_decouple(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
                "line_to_insert": 10,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_decouple_line",
                                "line_template": "MOOLABS_API_KEY = config('MOOLABS_API_KEY')"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        # decouple exposes module-level constants — accessor is direct import
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")

    def test_python_dotenv_os_getenv(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-dotenv-os-getenv",
                "file": "svc/app/config.py",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MOOLABS_API_KEY = os.getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")


class TypeScriptWireTargetDispatch(unittest.TestCase):
    def test_zod_env_schema(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-zod-env-schema",
                "file": "src/env.ts",
                "line_to_insert": 5,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_zod_field",
                                "field_template": "MOOLABS_API_KEY: z.string().min(1)"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        # Convention: TS env modules export via @/-aliased path
        self.assertEqual(plan["settings_import_path"], "@/env")
        self.assertEqual(plan["api_key_accessor"], "env.MOOLABS_API_KEY")

    def test_process_env_direct(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-process-env-direct",
                "file": "src/config.ts",
                "line_to_insert": 12,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_process_env_line",
                                "line_template": "export const MOOLABS_API_KEY = process.env.MOOLABS_API_KEY ?? \"\";"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["settings_import_path"], "@/config")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")

    def test_env_var_library(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "ts-env-var-library",
                "file": "src/env.ts",
                "line_to_insert": 8,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_env_var_line",
                                "line_template": "export const MOOLABS_API_KEY = env.get(\"MOOLABS_API_KEY\").required().asString();"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], "MOOLABS_API_KEY")


class GoWireTargetDispatch(unittest.TestCase):
    def test_viper(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "go-viper",
                "file": "internal/config/config.go",
                "line_to_insert": 14,
                "confidence": "high",
                "stub_required": False,
                "wire_target": {"kind": "add_viper_bindenv",
                                "line_template": "viper.BindEnv(\"moolabs_api_key\", \"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        # Go convention: import the package containing the config struct
        self.assertEqual(plan["settings_import_path"], "internal/config")
        # viper uses GetString("moolabs_api_key")
        self.assertEqual(plan["api_key_accessor"], 'viper.GetString("moolabs_api_key")')

    def test_envconfig(self):
        service = {
            "service_slug": "svc",
            "app_config": {
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
        self.assertEqual(plan["settings_import_path"], "internal/config")
        # envconfig uses struct field — accessor via config.Get().MoolabsAPIKey
        self.assertEqual(plan["api_key_accessor"], "config.Get().MoolabsAPIKey")

    def test_go_os_getenv(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "go-os-getenv",
                "file": "internal/config/config.go",
                "line_to_insert": 5,
                "confidence": "medium",
                "stub_required": False,
                "wire_target": {"kind": "add_os_getenv_line",
                                "line_template": "MoolabsAPIKey := os.Getenv(\"MOOLABS_API_KEY\")"},
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="go")
        self.assertEqual(plan["mode"], "modify")
        self.assertEqual(plan["api_key_accessor"], 'os.Getenv("MOOLABS_API_KEY")')


class StubModeFallback(unittest.TestCase):
    def test_unrecognized_pattern_triggers_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "unrecognized",
                "confidence": "none",
                "stub_required": True,
            },
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="python")
        self.assertEqual(plan["mode"], "stub")
        # Stub Settings file is emitted at a conventional service-relative path.
        self.assertEqual(plan["stub_emit_path"], "app/services/moolabs_settings.py")
        # Accessor reads from the stub.
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )
        self.assertEqual(plan["settings_import_path"], "app.services.moolabs_settings")

    def test_stub_required_true_overrides_recognized_pattern(self):
        """When confidence is low even for a recognized pattern, stub_required
        triggers the stub fallback to avoid wiring into an uncertain match."""
        service = {
            "service_slug": "svc",
            "app_config": {
                "pattern": "python-decouple",
                "file": "svc/app/config.py",
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
            "app_config": {"pattern": "unrecognized", "stub_required": True},
            "deployment_surfaces": [],
        }
        plan = cw.plan_service_env_wire(service, language="typescript")
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["stub_emit_path"], "src/services/moolabs-settings.ts")

    def test_go_stub(self):
        service = {
            "service_slug": "svc",
            "app_config": {"pattern": "unrecognized", "stub_required": True},
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


if __name__ == "__main__":
    unittest.main()
