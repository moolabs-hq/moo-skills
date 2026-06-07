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

    def test_python_decouple_routes_to_stub(self):
        """Direct-export pattern (decouple) — accessor would be a bare
        identifier the helper template doesn't import. Routes to stub mode
        instead (PR #4 review CRIT-1 fix)."""
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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "app.services.moolabs_settings")
        self.assertEqual(
            plan["api_key_accessor"],
            "get_settings().moolabs_api_key.get_secret_value()",
        )

    def test_python_dotenv_os_getenv_routes_to_stub(self):
        """Same root cause as decouple — dotenv-os-getenv is a flat module
        pattern; stub mode is the correct path (PR #4 review CRIT-1 fix)."""
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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "app.services.moolabs_settings")


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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "@/services/moolabs-settings")
        self.assertEqual(plan["api_key_accessor"], "getSettings().MOOLABS_API_KEY")

    def test_process_env_direct_routes_to_stub(self):
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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "@/services/moolabs-settings")

    def test_env_var_library_routes_to_stub(self):
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
        self.assertEqual(plan["mode"], "stub")


class GoWireTargetDispatch(unittest.TestCase):
    def test_viper_routes_to_stub(self):
        """go-viper needs `import viper` in the helper template, but the
        template only imports the customer's `config` alias. Routes to
        stub mode (PR #4 review CRIT-3 fix)."""
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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "internal/moolabsconfig")

    def test_envconfig(self):
        """go-envconfig is the only Go pattern that works in modify mode.
        Customer's config package exports Get() returning a struct with
        MoolabsAPIKey — composes correctly with the template's `config`
        import alias."""
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
        self.assertEqual(plan["api_key_accessor"], "config.Get().MoolabsAPIKey")

    def test_go_os_getenv_routes_to_stub(self):
        """go-os-getenv uses stdlib os directly — the customer's config
        package isn't actually used by the accessor expression, leaving the
        `config` import dead. Go's `imported and not used` is a compile
        error. Routes to stub mode (PR #4 review CRIT-3 fix)."""
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
        self.assertEqual(plan["mode"], "stub")
        self.assertEqual(plan["settings_import_path"], "internal/moolabsconfig")


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


class BuildPlanFromInventory(unittest.TestCase):
    def test_build_plan_one_service(self):
        inventory = {
            "granularity": "per-service",
            "services": [
                {
                    "service_slug": "svc-a",
                    "app_config": {
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
                {"service_slug": "svc", "app_config": {"pattern": "unrecognized"},
                 "deployment_surfaces": []},
            ],
        }
        plan = cw.build_plan(inventory, services_languages={})
        # No language declared → default to python (Phase A's
        # parse_services_and_granularity makes the same fallback).
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


class AccessorRuntimeRegression(unittest.TestCase):
    """Regression guard against PR #4 review C-1/C-2/C-3. Every accessor in
    every _LANG_PATTERN_ACCESSORS map must compose with the helper template's
    import contract — i.e. when the helper renders `return <accessor>`, the
    accessor must be a valid expression in the helper's scope (`get_settings`
    is imported; nothing else).

    Earlier accessors like `"MOOLABS_API_KEY"` or `viper.GetString(...)`
    rendered to expressions referencing undefined names. py-compile / gofmt
    passed (syntactically valid) but the rendered helper crashed at runtime
    with NameError / undefined: viper. This test would have caught the bug
    by executing the rendered _resolve_api_key with a mocked get_settings.
    """

    def test_every_python_accessor_executes_without_nameerror(self):
        """For every pattern in _PYTHON_PATTERN_ACCESSORS, render the helper
        and execute _resolve_api_key. Mocked get_settings returns an object
        compatible with the accessor expression."""
        from jinja2 import Environment, FileSystemLoader
        tpl_dir = Path(__file__).resolve().parents[1] / "assets" / "codemod-templates"
        env = Environment(loader=FileSystemLoader(str(tpl_dir)))
        for pattern, accessor in cw._PYTHON_PATTERN_ACCESSORS.items():
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

    def test_python_decouple_NOT_in_accessor_map(self):
        """C-1 regression guard: python-decouple's accessor was a bare
        identifier (NameError). It must NOT be in the accessor map — the
        absence routes it to stub mode."""
        self.assertNotIn("python-decouple", cw._PYTHON_PATTERN_ACCESSORS)
        self.assertNotIn("python-dotenv-os-getenv", cw._PYTHON_PATTERN_ACCESSORS)

    def test_no_ts_patterns_in_accessor_map(self):
        """C-2 regression guard: all 3 TS patterns produced TS compile
        errors AND runtime ReferenceError. Map must be empty until
        pattern-aware TS template variants exist."""
        self.assertEqual(cw._TS_PATTERN_ACCESSORS, {})

    def test_go_viper_and_os_getenv_NOT_in_accessor_map(self):
        """C-3 regression guard: go-viper needed a separate viper import,
        go-os-getenv left the config import unused. Both routed to stub."""
        self.assertNotIn("go-viper", cw._GO_PATTERN_ACCESSORS)
        self.assertNotIn("go-os-getenv", cw._GO_PATTERN_ACCESSORS)
        self.assertIn("go-envconfig", cw._GO_PATTERN_ACCESSORS)


if __name__ == "__main__":
    unittest.main()
