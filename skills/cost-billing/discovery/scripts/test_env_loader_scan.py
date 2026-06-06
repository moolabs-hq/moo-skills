#!/usr/bin/env python3
"""Unit tests for env_loader_scan.py (Phase 1.7-scan).

Stdlib unittest; runs in the bash smoke suite's Phase 8. Fixtures are
generated in-process via tempfile.TemporaryDirectory — no checked-in
fixture directory.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_loader_scan as els  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
CATALOG_PATH = REPO_ROOT / "skills" / "cost-billing" / "shared" / "assets" / "env-loader-patterns.yaml"


class CatalogLoad(unittest.TestCase):
    def test_catalog_has_ten_patterns(self):
        catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.assertEqual(len(catalog), 10)

    def test_catalog_groups_by_language(self):
        catalog = els.load_pattern_catalog(CATALOG_PATH)
        by_lang = els.group_patterns_by_language(catalog)
        self.assertEqual(len(by_lang["python"]), 4)
        self.assertEqual(len(by_lang["typescript"]), 3)
        self.assertEqual(len(by_lang["go"]), 3)


class PythonPydanticSettingsV2(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_pydantic_settings_v2_high_confidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from pydantic_settings import BaseSettings\n"
                "from pydantic import Field\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
                "    redis_url: str = Field(..., env='REDIS_URL')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")
            self.assertEqual(result.confidence, "high")

    def test_pydantic_settings_v2_finds_insertion_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
                "    redis_url: str\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            # Insertion line is the last field of the class — line 5 here (1-indexed).
            self.assertEqual(result.line_to_insert, 5)


class PythonPydanticV1Settings(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_pydantic_v1_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "settings.py"
            cfg.write_text(
                "from pydantic import BaseSettings\n"
                "\n"
                "class Config(BaseSettings):\n"
                "    api_url: str\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-pydantic-v1-settings")


class PythonDecouple(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_decouple(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from decouple import config\n"
                "\n"
                "DATABASE_URL = config('DATABASE_URL')\n"
                "REDIS_URL = config('REDIS_URL')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-decouple")


class PythonDotenvOsGetenv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_detects_dotenv_os_getenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "import os\n"
                "from dotenv import load_dotenv\n"
                "\n"
                "load_dotenv()\n"
                "DATABASE_URL = os.getenv('DATABASE_URL', '')\n"
            )
            result = els.scan_file(cfg, self.python_patterns)
            self.assertEqual(result.pattern_id, "python-dotenv-os-getenv")


class UnrecognizedFileReturnsNone(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.python_patterns = els.group_patterns_by_language(self.catalog)["python"]

    def test_random_python_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "random.py"
            f.write_text("def hello():\n    return 42\n")
            result = els.scan_file(f, self.python_patterns)
            self.assertIsNone(result)


class TypeScriptZodEnv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_zod_env_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "env.ts"
            f.write_text(
                "import { z } from 'zod';\n"
                "\n"
                "export const envSchema = z.object({\n"
                "  DATABASE_URL: z.string(),\n"
                "  REDIS_URL: z.string(),\n"
                "});\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-zod-env-schema")
            self.assertEqual(result.confidence, "high")


class TypeScriptProcessEnvDirect(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_process_env_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "export const DATABASE_URL = process.env.DATABASE_URL ?? '';\n"
                "export const REDIS_URL = process.env.REDIS_URL ?? '';\n"
                "export const API_PORT = process.env.API_PORT ?? '8080';\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-process-env-direct")
            # Only structural hit (no import) → medium confidence.
            self.assertEqual(result.confidence, "medium")


class TypeScriptEnvVarLibrary(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_detects_env_var_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "import * as env from 'env-var';\n"
                "\n"
                "export const DATABASE_URL = env.get('DATABASE_URL').required().asString();\n"
            )
            result = els.scan_file(f, self.ts_patterns)
            self.assertEqual(result.pattern_id, "ts-env-var-library")


class TypeScriptInsertLineHeuristic(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.ts_patterns = els.group_patterns_by_language(self.catalog)["typescript"]

    def test_zod_schema_insert_line_is_inside_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "env.ts"
            f.write_text(
                "import { z } from 'zod';\n"          # line 1
                "\n"                                   # line 2
                "export const envSchema = z.object({\n"  # line 3
                "  DATABASE_URL: z.string(),\n"       # line 4
                "  REDIS_URL: z.string(),\n"          # line 5
                "});\n"                               # line 6
            )
            result = els.scan_file(f, self.ts_patterns)
            # Insertion line should be inside the object — line 5 (the last
            # field before the closing brace).
            self.assertEqual(result.line_to_insert, 5)


class GoViper(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_viper(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"github.com/spf13/viper\"\n"
                "\n"
                "func Init() {\n"
                "    viper.SetEnvPrefix(\"APP\")\n"
                "    viper.AutomaticEnv()\n"
                "    viper.BindEnv(\"database_url\")\n"
                "}\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-viper")
            self.assertEqual(result.confidence, "high")


class GoEnvconfig(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_envconfig(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"github.com/kelseyhightower/envconfig\"\n"
                "\n"
                "type Config struct {\n"
                "    DatabaseURL string `envconfig:\"DATABASE_URL\" required:\"true\"`\n"
                "    RedisURL    string `envconfig:\"REDIS_URL\"`\n"
                "}\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-envconfig")


class GoOsGetenv(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_detects_os_getenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"
                "\n"
                "import \"os\"\n"
                "\n"
                "var DatabaseURL = os.Getenv(\"DATABASE_URL\")\n"
                "var RedisURL = os.Getenv(\"REDIS_URL\")\n"
            )
            result = els.scan_file(f, self.go_patterns)
            self.assertEqual(result.pattern_id, "go-os-getenv")


class GoEnvconfigInsertLine(unittest.TestCase):
    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)
        self.go_patterns = els.group_patterns_by_language(self.catalog)["go"]

    def test_envconfig_insert_line_is_inside_struct(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.go"
            f.write_text(
                "package config\n"                                                     # 1
                "\n"                                                                    # 2
                "import \"github.com/kelseyhightower/envconfig\"\n"                     # 3
                "\n"                                                                    # 4
                "type Config struct {\n"                                                # 5
                "    DatabaseURL string `envconfig:\"DATABASE_URL\"`\n"                # 6
                "    RedisURL    string `envconfig:\"REDIS_URL\"`\n"                   # 7
                "}\n"                                                                   # 8
            )
            result = els.scan_file(f, self.go_patterns)
            # Insertion point: last field of the struct → line 7.
            self.assertEqual(result.line_to_insert, 7)


class ServiceScan(unittest.TestCase):
    """Scan a service directory (multiple files) and return the best match."""

    def setUp(self):
        self.catalog = els.load_pattern_catalog(CATALOG_PATH)

    def test_scan_service_finds_pydantic_settings_in_config_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            (svc / "app").mkdir(parents=True)
            (svc / "app" / "main.py").write_text("from app.config import Settings\n")
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")
            self.assertTrue(result.file.endswith("config.py"))

    def test_scan_service_picks_highest_confidence_when_multiple_match(self):
        """If a service has BOTH pydantic-settings AND a dotenv+os.getenv
        config file, pick the higher-priority pydantic one."""
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            (svc / "app").mkdir(parents=True)
            # The "real" config — pydantic-settings, high confidence
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    database_url: str\n"
            )
            # A legacy helper using dotenv + os.getenv (lower priority)
            (svc / "app" / "legacy_env.py").write_text(
                "from dotenv import load_dotenv\n"
                "import os\n"
                "load_dotenv()\n"
                "DB = os.getenv('DB')\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            # Pydantic-settings priority=100 vs dotenv priority=70 → pydantic wins
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")

    def test_scan_service_returns_none_when_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "no-config"
            svc.mkdir(parents=True)
            (svc / "main.py").write_text("def main(): pass\n")
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNone(result)

    def test_scan_service_skips_irrelevant_extensions(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "py-svc"
            svc.mkdir(parents=True)
            # A Go config file in a Python service — should be skipped when
            # we ask for language=python.
            (svc / "config.go").write_text(
                "import \"github.com/spf13/viper\"\n"
                "viper.AutomaticEnv()\n"
            )
            result = els.scan_service(svc, "python", self.catalog)
            self.assertIsNone(result)


class DeploymentSurfaceScan(unittest.TestCase):
    def test_detects_terraform_variables(self):
        with tempfile.TemporaryDirectory() as tmp:
            tf_dir = Path(tmp) / "infra" / "terraform" / "payments-api"
            tf_dir.mkdir(parents=True)
            (tf_dir / "variables.tf").write_text(
                'variable "database_url" { type = string }\n'
            )
            (tf_dir / "main.tf").write_text("# main\n")
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            terraform_hits = [s for s in surfaces if s.kind == "terraform"]
            self.assertEqual(len(terraform_hits), 1)
            self.assertTrue(terraform_hits[0].path.endswith("variables.tf"))
            self.assertEqual(terraform_hits[0].insert_kind, "variable_block_append")

    def test_detects_k8s_deployment_with_envfrom(self):
        with tempfile.TemporaryDirectory() as tmp:
            k8s = Path(tmp) / "infra" / "k8s" / "payments-api"
            k8s.mkdir(parents=True)
            (k8s / "deployment.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: payments-api\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: app\n"
                "        envFrom:\n"
                "        - secretRef:\n            name: payments-secrets\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            k8s_hits = [s for s in surfaces if s.kind == "k8s"]
            self.assertEqual(len(k8s_hits), 1)
            self.assertEqual(k8s_hits[0].insert_kind, "secret_ref_checklist")

    def test_detects_docker_compose(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "docker-compose.yml").write_text(
                "services:\n  app:\n    image: app:latest\n    environment:\n"
                "      - DATABASE_URL=postgres://...\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            compose_hits = [s for s in surfaces if s.kind == "docker-compose"]
            self.assertEqual(len(compose_hits), 1)

    def test_detects_dotenv_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "payments-api"
            svc.mkdir(parents=True)
            (svc / ".env.example").write_text("DATABASE_URL=\nREDIS_URL=\n")
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            dotenv_hits = [s for s in surfaces if s.kind == "dotenv_example"]
            self.assertEqual(len(dotenv_hits), 1)
            self.assertEqual(dotenv_hits[0].insert_kind, "line_append")

    def test_dockerfile_with_env_lines_emits_checklist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "Dockerfile").write_text(
                "FROM python:3.11\n"
                "ENV DATABASE_URL=postgres://baked-in\n"  # security smell
                "COPY . /app\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            docker_hits = [s for s in surfaces if s.kind == "dockerfile"]
            self.assertEqual(len(docker_hits), 1)
            self.assertEqual(docker_hits[0].insert_kind, "checklist_only")


class GranularityHandling(unittest.TestCase):
    def test_per_service_emits_one_entry_per_service(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "services" / "svc-a" / "app").mkdir(parents=True)
            (repo / "services" / "svc-a" / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    x: str\n"
            )
            (repo / "services" / "svc-b" / "app").mkdir(parents=True)
            (repo / "services" / "svc-b" / "app" / "config.py").write_text(
                "from pydantic import BaseSettings\n"
                "class S(BaseSettings):\n    x: str\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[
                    {"slug": "svc-a", "root": "services/svc-a", "language": "python"},
                    {"slug": "svc-b", "root": "services/svc-b", "language": "python"},
                ],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="per-service",
                granularity_source="declared",
                shared_config_path=None,
            )
            self.assertEqual(len(inventory["services"]), 2)
            slugs = {s["service_slug"] for s in inventory["services"]}
            self.assertEqual(slugs, {"svc-a", "svc-b"})

    def test_unrecognized_pattern_yields_stub_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "services" / "weird" / "app").mkdir(parents=True)
            (repo / "services" / "weird" / "app" / "main.py").write_text(
                "def main(): pass\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[{"slug": "weird", "root": "services/weird", "language": "python"}],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="per-service",
                granularity_source="declared",
                shared_config_path=None,
            )
            entry = inventory["services"][0]
            self.assertEqual(entry["app_config"]["pattern"], "unrecognized")
            self.assertTrue(entry["app_config"]["stub_required"])

    def test_repo_wide_uses_shared_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            shared = repo / "packages" / "config"
            shared.mkdir(parents=True)
            (shared / "settings.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    x: str\n"
            )
            inventory = els.build_inventory(
                repo_root=repo,
                services=[
                    {"slug": "svc-a", "root": "services/svc-a", "language": "python"},
                    {"slug": "svc-b", "root": "services/svc-b", "language": "python"},
                ],
                catalog=els.load_pattern_catalog(CATALOG_PATH),
                granularity="repo-wide",
                granularity_source="declared",
                shared_config_path="packages/config",
            )
            # Both services share the same wire target — the shared file.
            self.assertEqual(len(inventory["services"]), 2)
            for entry in inventory["services"]:
                self.assertTrue(entry["app_config"]["file"].endswith("settings.py"))
                self.assertEqual(entry["app_config"]["pattern"], "python-pydantic-settings-v2")


class InventoryYamlEmit(unittest.TestCase):
    def test_emit_yaml_has_top_level_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            out = repo / "env-routing-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "granularity": "per-service",
                "granularity_source": "declared",
                "services": [],
            }
            els.emit_inventory_yaml(inventory, out)
            content = out.read_text()
            self.assertIn("generated_at:", content)
            self.assertIn("granularity: per-service", content)
            self.assertIn("granularity_source: declared", content)
            self.assertIn("services: []", content)


if __name__ == "__main__":
    unittest.main()
