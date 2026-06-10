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


class PythonPydanticSettingsV2(unittest.TestCase):
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
            result = els.scan_file_via_registry(cfg, "python")
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
            result = els.scan_file_via_registry(cfg, "python")
            # Insertion line is the last field of the class — line 5 here (1-indexed).
            self.assertEqual(result.line_to_insert, 5)


class PythonPydanticV1Settings(unittest.TestCase):
    def test_detects_pydantic_v1_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "settings.py"
            cfg.write_text(
                "from pydantic import BaseSettings\n"
                "\n"
                "class Config(BaseSettings):\n"
                "    api_url: str\n"
            )
            result = els.scan_file_via_registry(cfg, "python")
            self.assertEqual(result.pattern_id, "python-pydantic-v1-settings")


class PydanticSettingsSubclassTransitive(unittest.TestCase):
    """Dogfood #1a (general fix): a settings class often extends a PROJECT base
    (`class Settings(CommonBase)`) where CommonBase — not the leaf — is the one
    that extends BaseSettings. The 'env loader' is the BaseSettings inheritance
    itself (which makes every field read from OS env vars), NOT an `env_file`
    (that only loads a local .env for dev). So detection resolves the base
    chain transitively to BaseSettings — no modeling on any one repo's base
    name or on env_file."""

    def test_same_file_intermediate_plus_cross_file_terminal(self):
        """A same-file intermediate base (Settings -> Mid) chained to a
        cross-file terminal (Mid -> AppBase(BaseSettings)). The file has NO
        direct BaseSettings, so the precise v2 regex does not match — the
        transitive detector must follow Settings -> Mid (same file) ->
        AppBase (imported) -> BaseSettings."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "shared").mkdir()
            (root / "shared" / "base.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class AppBase(BaseSettings):\n"
                "    region: str = 'us'\n"
            )
            cfg = root / "config.py"
            cfg.write_text(
                "from shared.base import AppBase\n"
                "\n"
                "class Mid(AppBase):\n"
                "    tier: str = 'std'\n"
                "\n"
                "class Settings(Mid):\n"
                "    log_format: str = 'json'\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[root])
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-subclass")
            self.assertEqual(result.confidence, "high")

    def test_cross_file_absolute_import_chain(self):
        """The leaf has NO env_file and NO direct BaseSettings — the base lives
        in another module reached via an absolute import."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "common.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class CommonSettings(BaseSettings):\n"
                "    region: str = 'us'\n"
            )
            cfg = root / "app" / "config.py"
            cfg.write_text(
                "from pydantic import Field\n"
                "from app.common import CommonSettings\n"
                "\n"
                "class Settings(CommonSettings):\n"
                "    log_format: str = 'json'\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[root])
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_multiline_parenthesized_base_import_resolves(self):
        """PR #10 review IMP: a base imported via a parenthesized multi-line
        `from x import (\\n Base,\\n)` form must still resolve transitively."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "common.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class CommonSettings(BaseSettings):\n    region: str = 'us'\n"
            )
            cfg = root / "app" / "config.py"
            cfg.write_text(
                "from app.common import (\n"
                "    CommonSettings,\n"
                "    something_else,\n"
                ")\n"
                "class Settings(CommonSettings):\n    x: str = 'y'\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[root])
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_cross_file_relative_import_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "common.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class CommonSettings(BaseSettings):\n"
                "    region: str = 'us'\n"
            )
            cfg = root / "app" / "config.py"
            cfg.write_text(
                "from .common import CommonSettings\n"
                "class Settings(CommonSettings):\n"
                "    x: str = 'y'\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[root])
            self.assertIsNotNone(result)
            self.assertEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_data_model_not_detected(self):
        """A plain pydantic data model (BaseModel, no settings chain) must NOT
        be detected as the app config."""
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "schemas.py"
            f.write_text(
                "from pydantic import BaseModel\n"
                "\n"
                "class PTPExtraction(BaseModel):\n"
                "    amount: float\n"
            )
            result = els.scan_file_via_registry(f, "python", search_roots=[Path(tmp)])
            if result is not None:
                self.assertNotEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_unresolvable_base_does_not_crash(self):
        """A base imported from a 3rd-party package not on disk → not detected,
        no crash."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from some_vendor.lib import VendorBase\n"
                "class Settings(VendorBase):\n"
                "    x: str = 'y'\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[Path(tmp)])
            # VendorBase unresolvable → not a settings subclass.
            if result is not None:
                self.assertNotEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_import_cycle_terminates(self):
        """Mutually-importing base files must not infinite-loop."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.py").write_text(
                "from b import BBase\nclass ABase(BBase):\n    x: str = '1'\n")
            (root / "b.py").write_text(
                "from a import ABase\nclass BBase(ABase):\n    y: str = '2'\n")
            cfg = root / "config.py"
            cfg.write_text("from a import ABase\nclass Settings(ABase):\n    z: str = '3'\n")
            # No BaseSettings anywhere in the cycle → not detected, terminates.
            result = els.scan_file_via_registry(cfg, "python", search_roots=[root])
            if result is not None:
                self.assertNotEqual(result.pattern_id, "python-pydantic-settings-subclass")

    def test_direct_basesettings_prefers_precise_pattern(self):
        """A DIRECT BaseSettings subclass keeps the precise v2 pattern (which
        carries the get_settings() modify accessor) — the transitive detector
        only fires when no precise high-confidence pattern matched."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from pydantic_settings import BaseSettings\n"
                "\n"
                "class Settings(BaseSettings):\n"
                "    api_url: str\n"
            )
            result = els.scan_file_via_registry(cfg, "python", search_roots=[Path(tmp)])
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")

    def test_skill_own_stub_not_detected_as_config_on_rerun(self):
        """Re-run safety: a previously-emitted moolabs_settings.py stub (itself a
        BaseSettings subclass) must NOT be detected as the customer's config —
        else the codemod would wire into its own output."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            svc = repo / "services" / "svc-x"
            (svc / "app" / "services").mkdir(parents=True)
            # The skill's own prior output — a real BaseSettings stub.
            (svc / "app" / "services" / "moolabs_settings.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class MoolabsSettings(BaseSettings):\n"
                "    moolabs_api_key: str = ''\n"
                "def get_settings():\n    return MoolabsSettings()\n"
            )
            # The real customer config.
            (repo / "shared").mkdir()
            (repo / "shared" / "base.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class AppBase(BaseSettings):\n    region: str = 'us'\n"
            )
            (svc / "app" / "config.py").write_text(
                "from shared.base import AppBase\n"
                "class Settings(AppBase):\n    log_format: str = 'json'\n"
            )
            entry = els._service_entry(
                repo,
                {"slug": "svc-x", "root": "services/svc-x", "language": "python"},
                svc,
                catalog=None,
            )
            self.assertTrue(entry["app_config"]["file"].endswith("app/config.py"))
            self.assertNotIn("moolabs_settings", entry["app_config"]["file"])

    def test_service_entry_picks_real_config_over_smoke_script(self):
        """End-to-end #1a: a service whose Settings extends a base in a
        REPO-LEVEL shared package (outside the service tree, so it's resolved
        cross-file but isn't itself a scanned candidate) AND a smoke script
        using os.getenv → app_config.file must be the real config, not the
        smoke script (the exact dogfood misdetection). The base is resolved via
        repo_root in search_roots; only config.py is a scanned candidate."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Shared base lives at repo root, OUTSIDE services/ — not scanned,
            # only resolved.
            (repo / "shared").mkdir(parents=True)
            (repo / "shared" / "base.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class AppBase(BaseSettings):\n"
                "    region: str = 'us'\n"
            )
            svc = repo / "services" / "svc-x"
            (svc / "app").mkdir(parents=True)
            (svc / "scripts").mkdir(parents=True)
            (svc / "app" / "config.py").write_text(
                "from shared.base import AppBase\n"
                "class Settings(AppBase):\n"
                "    log_format: str = 'json'\n"
            )
            (svc / "scripts" / "smoke_e2e_dev.py").write_text(
                "import os\nTOKEN = os.getenv('SOME_TOKEN')\n"
            )
            entry = els._service_entry(
                repo,
                {"slug": "svc-x", "root": "services/svc-x", "language": "python"},
                svc,
                catalog=None,
            )
            self.assertTrue(
                entry["app_config"]["file"].endswith("app/config.py"),
                f"picked {entry['app_config']['file']} instead of app/config.py",
            )
            self.assertEqual(
                entry["app_config"]["pattern"], "python-pydantic-settings-subclass")


class PythonDecouple(unittest.TestCase):
    def test_detects_decouple(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.py"
            cfg.write_text(
                "from decouple import config\n"
                "\n"
                "DATABASE_URL = config('DATABASE_URL')\n"
                "REDIS_URL = config('REDIS_URL')\n"
            )
            result = els.scan_file_via_registry(cfg, "python")
            self.assertEqual(result.pattern_id, "python-decouple")


class PythonDotenvOsGetenv(unittest.TestCase):
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
            result = els.scan_file_via_registry(cfg, "python")
            self.assertEqual(result.pattern_id, "python-dotenv-os-getenv")


class UnrecognizedFileReturnsNone(unittest.TestCase):
    def test_random_python_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "random.py"
            f.write_text("def hello():\n    return 42\n")
            result = els.scan_file_via_registry(f, "python")
            self.assertIsNone(result)


class TypeScriptZodEnv(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "typescript")
            self.assertEqual(result.pattern_id, "ts-zod-env-schema")
            self.assertEqual(result.confidence, "high")


class TypeScriptProcessEnvDirect(unittest.TestCase):
    def test_detects_process_env_direct(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "export const DATABASE_URL = process.env.DATABASE_URL ?? '';\n"
                "export const REDIS_URL = process.env.REDIS_URL ?? '';\n"
                "export const API_PORT = process.env.API_PORT ?? '8080';\n"
            )
            result = els.scan_file_via_registry(f, "typescript")
            self.assertEqual(result.pattern_id, "ts-process-env-direct")
            # Only structural hit (no import) → medium confidence.
            self.assertEqual(result.confidence, "medium")


class TypeScriptEnvVarLibrary(unittest.TestCase):
    def test_detects_env_var_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.ts"
            f.write_text(
                "import * as env from 'env-var';\n"
                "\n"
                "export const DATABASE_URL = env.get('DATABASE_URL').required().asString();\n"
            )
            result = els.scan_file_via_registry(f, "typescript")
            self.assertEqual(result.pattern_id, "ts-env-var-library")


class TypeScriptInsertLineHeuristic(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "typescript")
            # Insertion line should be inside the object — line 5 (the last
            # field before the closing brace).
            self.assertEqual(result.line_to_insert, 5)


class GoViper(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "go")
            self.assertEqual(result.pattern_id, "go-viper")
            self.assertEqual(result.confidence, "high")


class GoEnvconfig(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "go")
            self.assertEqual(result.pattern_id, "go-envconfig")


class GoOsGetenv(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "go")
            self.assertEqual(result.pattern_id, "go-os-getenv")


class GoEnvconfigInsertLine(unittest.TestCase):
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
            result = els.scan_file_via_registry(f, "go")
            # Insertion point: last field of the struct → line 7.
            self.assertEqual(result.line_to_insert, 7)


class ServiceScan(unittest.TestCase):
    """Scan a service directory (multiple files) and return the best match."""

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
            result = els.scan_service_via_registry(svc, "python")
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
            result = els.scan_service_via_registry(svc, "python")
            # Pydantic-settings priority=100 vs dotenv priority=70 → pydantic wins
            self.assertEqual(result.pattern_id, "python-pydantic-settings-v2")

    def test_scan_service_returns_none_when_no_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "services" / "no-config"
            svc.mkdir(parents=True)
            (svc / "main.py").write_text("def main(): pass\n")
            result = els.scan_service_via_registry(svc, "python")
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
            result = els.scan_service_via_registry(svc, "python")
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

    def test_detects_env_suffixed_compose_filenames(self):
        """Real customer repos ship docker-compose.prod.yaml,
        docker-compose.staging.yml, compose.dev.yaml, etc. The whitelist
        was previously docker-compose.yml/yaml + compose.yml/yaml only —
        these variants were silently missed."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for name in ("docker-compose.prod.yaml", "docker-compose.staging.yml",
                         "compose.dev.yml", "compose.production.yaml"):
                (repo / name).write_text(
                    "services:\n  app:\n    image: app:latest\n    environment:\n"
                    "      - DATABASE_URL=postgres://...\n"
                )
            surfaces = els.scan_deployment_surfaces(repo)
            compose_paths = {s.path for s in surfaces if s.kind == "docker-compose"}
            self.assertEqual(compose_paths, {
                "docker-compose.prod.yaml", "docker-compose.staging.yml",
                "compose.dev.yml", "compose.production.yaml",
            })

    def test_k8s_deployment_without_envfrom_is_not_flagged(self):
        """A bare Deployment manifest that doesn't use envFrom: secretRef
        should NOT produce a k8s deployment-surface entry. Previously the
        detector fired on ANY Deployment kind, inflating the Phase B
        checklist with services that have no secret-ref pattern to extend."""
        with tempfile.TemporaryDirectory() as tmp:
            k8s = Path(tmp) / "infra" / "k8s"
            k8s.mkdir(parents=True)
            (k8s / "deployment.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: payments-api\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: app\n"
                "        env:\n"
                "        - name: DATABASE_URL\n          value: postgres://...\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            k8s_hits = [s for s in surfaces if s.kind == "k8s"]
            self.assertEqual(k8s_hits, [])

    def test_k8s_envfrom_in_different_container_is_not_a_match(self):
        """Cross-container false positive guard. If container A has
        `envFrom: - configMapRef:` and container B has `valueFrom: secretRef:`,
        the naive `envFrom:[\\s\\S]{0,200}secretRef:` regex matched across
        the container boundary. The detector must require envFrom and
        secretRef to belong to the SAME container's envFrom list."""
        with tempfile.TemporaryDirectory() as tmp:
            k8s = Path(tmp) / "infra" / "k8s"
            k8s.mkdir(parents=True)
            (k8s / "deployment.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: app\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: sidecar\n"
                "        envFrom:\n"
                "        - configMapRef:\n            name: my-config\n"
                "      - name: app\n"
                "        env:\n"
                "        - name: PASS\n          valueFrom:\n            secretRef:\n              name: secrets\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            k8s_hits = [s for s in surfaces if s.kind == "k8s"]
            self.assertEqual(k8s_hits, [],
                             f"Expected no k8s match (cross-container) but got: {k8s_hits}")

    def test_k8s_envfrom_secretref_in_same_container_IS_a_match(self):
        """Positive case for the tightened detector: envFrom: secretRef:
        in the same container correctly produces a k8s surface entry."""
        with tempfile.TemporaryDirectory() as tmp:
            k8s = Path(tmp) / "infra" / "k8s"
            k8s.mkdir(parents=True)
            (k8s / "deployment.yaml").write_text(
                "apiVersion: apps/v1\n"
                "kind: Deployment\n"
                "metadata:\n  name: app\n"
                "spec:\n  template:\n    spec:\n      containers:\n"
                "      - name: app\n"
                "        envFrom:\n"
                "        - secretRef:\n            name: my-secrets\n"
            )
            surfaces = els.scan_deployment_surfaces(Path(tmp))
            k8s_hits = [s for s in surfaces if s.kind == "k8s"]
            self.assertEqual(len(k8s_hits), 1)
            self.assertEqual(k8s_hits[0].insert_kind, "secret_ref_checklist")

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
                catalog=None,
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
                catalog=None,
                granularity="per-service",
                granularity_source="declared",
                shared_config_path=None,
            )
            entry = inventory["services"][0]
            self.assertEqual(entry["app_config"]["pattern"], "unrecognized")
            self.assertTrue(entry["app_config"]["stub_required"])

    def test_hybrid_warns_and_degrades_to_per_service(self):
        """Phase A does not implement hybrid granularity. If an engineer
        declares hybrid, the scanner must:
          (a) print a stderr warning so the degradation is visible
          (b) actually run per-service (not silently produce wrong data)
          (c) record the degradation in the output YAML so adversarial
              review can spot it.
        """
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "services" / "svc-a" / "app").mkdir(parents=True)
            (repo / "services" / "svc-a" / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    x: str\n"
            )
            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                inventory = els.build_inventory(
                    repo_root=repo,
                    services=[
                        {"slug": "svc-a", "root": "services/svc-a", "language": "python"},
                    ],
                    catalog=None,
                    granularity="hybrid",
                    granularity_source="declared",
                    shared_config_path="packages/config",
                )
            warning = captured.getvalue()
            self.assertIn("hybrid", warning)
            self.assertIn("out-of-scope", warning)
            self.assertIn("per-service", warning)
            # Degradation is recorded in the YAML output so downstream
            # consumers can see it
            self.assertIn("degraded", inventory["granularity"])
            # The scan still ran per-service (didn't crash, produced a result)
            self.assertEqual(len(inventory["services"]), 1)
            self.assertEqual(
                inventory["services"][0]["app_config"]["pattern"],
                "python-pydantic-settings-v2",
            )

            # YAML round-trip: the granularity value "hybrid (degraded to
            # per-service)" contains spaces and parens. emit_inventory_yaml
            # writes it as a plain scalar. Verify PyYAML reads it back
            # as the exact string (round-trip stability).
            import yaml as _yaml
            out = repo / "hybrid-inventory.yaml"
            els.emit_inventory_yaml(inventory, out)
            parsed = _yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["granularity"],
                "hybrid (degraded to per-service)",
            )

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
                catalog=None,
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

    def test_emit_yaml_roundtrips_through_pyyaml(self):
        """Regression guard: the emitted YAML must be parseable by PyYAML
        and preserve the exact field values. This catches the entire class
        of YAML escape bugs (backslash, quote, special chars) that the
        assertIn-based tests would otherwise miss."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            out = repo / "env-routing-inventory.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "granularity": "per-service",
                "granularity_source": "declared",
                "services": [
                    {
                        "service_slug": "svc-a",
                        "app_config": {
                            "pattern": "python-pydantic-settings-v2",
                            "file": "services/svc-a/app/config.py",
                            "line_to_insert": 5,
                            "confidence": "high",
                            "confidence_score": 0.95,
                            "stub_required": False,
                            "evidence": [
                                'line 3: from pydantic_settings import BaseSettings',
                            ],
                            "wire_target": {
                                "kind": "add_pydantic_settings_field",
                                "field_template": 'moolabs_api_key: SecretStr',
                            },
                        },
                        "deployment_surfaces": [
                            {"kind": "terraform", "path": "infra/variables.tf",
                             "insert_kind": "variable_block_append"},
                        ],
                    },
                ],
            }
            els.emit_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(parsed["granularity"], "per-service")
            self.assertEqual(len(parsed["services"]), 1)
            svc = parsed["services"][0]
            self.assertEqual(svc["service_slug"], "svc-a")
            self.assertEqual(svc["app_config"]["pattern"], "python-pydantic-settings-v2")
            self.assertEqual(svc["app_config"]["line_to_insert"], 5)
            # PR #8 review #3-sibling guard: generated_at must round-trip as a
            # string, not be coerced to datetime by PyYAML.
            self.assertIsInstance(parsed["generated_at"], str)
            # PR #11 review round 5: the config FILE path must round-trip intact
            # (it is an arbitrary customer FS path).
            self.assertEqual(svc["app_config"]["file"], "services/svc-a/app/config.py")

    def test_emit_yaml_file_path_with_space_hash_not_truncated(self):
        """PR #11 review round 5: a legal path containing ` #` must NOT be
        silently truncated by PyYAML's comment rule (unquoted emit bug)."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "inv.yaml"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "granularity": "per-service", "granularity_source": "declared",
                "services": [{
                    "service_slug": "svc",
                    "app_config": {
                        "pattern": "python-pydantic-settings-v2",
                        "node_id": "python-pydantic-settings-v2",
                        "file": "weird #2/config.py",
                        "line_to_insert": 3, "confidence": "high",
                        "confidence_score": 0.95, "stub_required": False,
                        "evidence": [], "wire_target": {},
                        "emit_path": "weird #2/moolabs_settings.py",
                        "import_path": "x.moolabs_settings",
                    },
                    "deployment_surfaces": [],
                }],
            }
            els.emit_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["services"][0]["app_config"]["file"], "weird #2/config.py")

    def test_emit_yaml_handles_backslash_in_evidence(self):
        """Regression guard for the backslash YAML escape bug. Source
        files containing Windows paths or regex literals can produce
        evidence strings with backslashes. The hand-rolled emitter must
        double-escape backslashes (and quotes) so PyYAML doesn't read
        `\\n` as newline, `\\t` as tab, etc."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            out = repo / "env-routing-inventory.yaml"
            evidence_with_backslash = r"line 12: pattern = r'\w+\s*=\s*\d+'"
            wire_value_with_backslash = r"prefix\suffix"
            inventory = {
                "generated_at": "2026-06-06T00:00:00+00:00",
                "granularity": "per-service",
                "granularity_source": "declared",
                "services": [
                    {
                        "service_slug": "svc-bs",
                        "app_config": {
                            "pattern": "python-decouple",
                            "file": "config.py",
                            "line_to_insert": 12,
                            "confidence": "medium",
                            "confidence_score": 0.65,
                            "stub_required": False,
                            "evidence": [evidence_with_backslash],
                            "wire_target": {
                                "kind": "add_decouple_line",
                                "field_template": wire_value_with_backslash,
                            },
                        },
                        "deployment_surfaces": [],
                    },
                ],
            }
            els.emit_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            self.assertEqual(
                parsed["services"][0]["app_config"]["evidence"][0],
                evidence_with_backslash,
            )
            self.assertEqual(
                parsed["services"][0]["app_config"]["wire_target"]["field_template"],
                wire_value_with_backslash,
            )


class RepoLevelInfraScan(unittest.TestCase):
    """Regression guard for PR #531 root cause — scanner was scoped to
    services/<svc>/ and never saw centralized infra at the repo root.
    moolabs has infrastructure/terraform/modules/secrets/variables.tf
    shared by every service; the scanner emitted zero Terraform surfaces
    for moo-arc."""

    def _moolabs_shape(self, tmp):
        """Build a fixture that mimics moolabs' shape: services/<svc>/ plus
        centralized infrastructure/terraform/modules/secrets/variables.tf."""
        (tmp / "services" / "moo-arc").mkdir(parents=True)
        (tmp / "services" / "moo-arc" / ".env.example").write_text("FOO=\n")
        (tmp / "services" / "moo-arc" / "Dockerfile").write_text(
            "FROM python\nENV PYTHONPATH=.\n"
        )
        (tmp / "infrastructure" / "terraform" / "modules" / "secrets").mkdir(parents=True)
        (tmp / "infrastructure" / "terraform" / "modules" / "secrets" / "variables.tf").write_text(
            'variable "db_password" {\n  type = string\n}\n'
        )
        (tmp / "infrastructure" / "terraform" / "regional").mkdir(parents=True)
        (tmp / "infrastructure" / "terraform" / "regional" / "variables.tf").write_text(
            'variable "region" {\n  type = string\n}\n'
        )

    def test_scan_repo_level_finds_centralized_terraform(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._moolabs_shape(tmp)
            surfaces = els.scan_repo_level_deployment_surfaces(tmp)
            tf_paths = sorted(s.path for s in surfaces if s.kind == "terraform")
            # Both Terraform files emitted with repo-relative paths
            # (NOT truncated to infra-dir-relative).
            self.assertIn("infrastructure/terraform/modules/secrets/variables.tf", tf_paths)
            self.assertIn("infrastructure/terraform/regional/variables.tf", tf_paths)
            # All repo-level surfaces tagged scope="repo"
            for s in surfaces:
                self.assertEqual(s.scope, "repo")

    def test_scan_repo_level_returns_empty_for_no_infra_dirs(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "src").mkdir()
            (tmp / "src" / "app.py").write_text("pass\n")
            surfaces = els.scan_repo_level_deployment_surfaces(tmp)
            self.assertEqual(surfaces, [])

    def test_service_entry_combines_both_scopes(self):
        """_service_entry must aggregate service-scope AND repo-scope
        surfaces, tagging each. Without this, PR #531's bug (no Terraform
        stub for moolabs) ships again."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            self._moolabs_shape(tmp)
            service = {"slug": "moo-arc", "root": "services/moo-arc", "language": "python"}
            entry = els._service_entry(
                tmp, service, tmp / "services" / "moo-arc", catalog=[]
            )
            kinds_by_scope = {
                (s["kind"], s["scope"]) for s in entry["deployment_surfaces"]
            }
            # service-scope detections
            self.assertIn(("dotenv_example", "service"), kinds_by_scope)
            self.assertIn(("dockerfile", "service"), kinds_by_scope)
            # repo-scope detection
            self.assertIn(("terraform", "repo"), kinds_by_scope)
            # gap=False because terraform + dockerfile were found
            self.assertFalse(entry["infra_discovery_gap"])

    def test_infra_discovery_gap_true_when_no_infra(self):
        """When neither scope finds terraform/k8s/dockerfile, the gap flag
        is set. .env.example alone is INSUFFICIENT (doesn't reach prod
        secret-routing). The instrument layer reads this flag to ask the
        developer where their IaC actually lives — covers non-standard
        paths like iac/, cdk/, pulumi/."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "services" / "tiny").mkdir(parents=True)
            (tmp / "services" / "tiny" / ".env.example").write_text("FOO=\n")
            # No terraform/, no k8s/, no Dockerfile anywhere.
            service = {"slug": "tiny", "root": "services/tiny", "language": "python"}
            entry = els._service_entry(
                tmp, service, tmp / "services" / "tiny", catalog=[]
            )
            self.assertTrue(entry["infra_discovery_gap"])

    def test_scan_walks_all_repo_level_infra_dir_names(self):
        """Every _REPO_LEVEL_INFRA_DIRS entry must be honored. Covers
        customers using 'infra' instead of 'infrastructure', 'tf' instead
        of 'terraform', etc."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for dir_name in ("infra", "tf", "deploy", "k8s", "helm", "ops"):
                tf_dir = tmp / dir_name / "secrets"
                tf_dir.mkdir(parents=True)
                (tf_dir / "vars.tf").write_text(
                    'variable "x" {\n  type = string\n}\n'
                )
            surfaces = els.scan_repo_level_deployment_surfaces(tmp)
            found_dirs = {
                s.path.split("/")[0] for s in surfaces if s.kind == "terraform"
            }
            for dir_name in ("infra", "tf", "deploy", "k8s", "helm", "ops"):
                self.assertIn(
                    dir_name, found_dirs,
                    f"_REPO_LEVEL_INFRA_DIRS missing {dir_name}",
                )

    def test_emit_yaml_includes_scope_and_gap_flag(self):
        """The YAML emitter must include the new scope field on each
        deployment_surface entry AND the infra_discovery_gap flag at the
        service level. Without this, downstream consumers (config_wire,
        instrument) can't distinguish service-scope from repo-scope or
        know to surface the gap CHECKLIST."""
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "inv.yaml"
            inventory = {
                "generated_at": "2026-06-07T00:00:00+00:00",
                "granularity": "per-service",
                "env_loader_granularity": "per-service",
                "granularity_source": "declared",
                "services": [{
                    "service_slug": "moo-arc",
                    "infra_discovery_gap": False,
                    "app_config": {
                        "pattern": "unrecognized",
                        "confidence": "none",
                        "evidence": [],
                        "stub_required": True,
                    },
                    "deployment_surfaces": [
                        {"kind": "dotenv_example",
                         "path": "services/moo-arc/.env.example",
                         "insert_kind": "line_append",
                         "scope": "service"},
                        {"kind": "terraform",
                         "path": "infrastructure/terraform/modules/secrets/variables.tf",
                         "insert_kind": "variable_block_append",
                         "scope": "repo"},
                    ],
                }],
            }
            els.emit_inventory_yaml(inventory, out)
            parsed = yaml.safe_load(out.read_text())
            svc = parsed["services"][0]
            # gap flag round-trips as bool
            self.assertIs(svc["infra_discovery_gap"], False)
            # scope round-trips on each surface
            scopes = {s["kind"]: s["scope"] for s in svc["deployment_surfaces"]}
            self.assertEqual(scopes["dotenv_example"], "service")
            self.assertEqual(scopes["terraform"], "repo")


class TerraformVendorMirrorSkip(unittest.TestCase):
    """PR #7 review IMPORTANT regression guard: `.terraform/` (terraform init
    module mirrors) and `.terragrunt-cache/` (terragrunt module copies) MUST
    be skipped. A dev who ran `terraform init` would otherwise see vendored
    `variables.tf` copies pulled in as false-positive repo-scope surfaces —
    CHECKLIST entries pointing at gitignored machine-generated paths, AND a
    falsely-cleared infra_discovery_gap for a repo with no real committed IaC."""

    def test_dotterraform_module_mirror_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "infrastructure" / "terraform").mkdir(parents=True)
            (tmp / "infrastructure" / "terraform" / "variables.tf").write_text(
                'variable "real" {\n  type = string\n}\n'
            )
            mirror = (tmp / "infrastructure" / "terraform"
                      / ".terraform" / "modules" / "vendored")
            mirror.mkdir(parents=True)
            (mirror / "variables.tf").write_text(
                'variable "vendored_noise" {\n  type = string\n}\n'
            )
            surfaces = els.scan_repo_level_deployment_surfaces(tmp)
            tf_paths = [s.path for s in surfaces if s.kind == "terraform"]
            self.assertIn("infrastructure/terraform/variables.tf", tf_paths)
            self.assertFalse(
                any(".terraform" in p for p in tf_paths),
                f"vendored .terraform mirror leaked into surfaces: {tf_paths}",
            )

    def test_terragrunt_cache_excluded(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            cache = (tmp / "infrastructure" / ".terragrunt-cache"
                     / "abc123" / "module")
            cache.mkdir(parents=True)
            (cache / "variables.tf").write_text(
                'variable "cached_noise" {\n  type = string\n}\n'
            )
            surfaces = els.scan_repo_level_deployment_surfaces(tmp)
            tf_paths = [s.path for s in surfaces if s.kind == "terraform"]
            self.assertEqual(
                tf_paths, [],
                f".terragrunt-cache leaked into surfaces: {tf_paths}",
            )

    def test_dotterraform_only_repo_flags_gap(self):
        """A repo whose ONLY terraform is inside .terraform/ (fresh init, no
        committed IaC) must flag infra_discovery_gap=True — the vendored copy
        must not falsely clear the gap."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "services" / "svc").mkdir(parents=True)
            (tmp / "services" / "svc" / "app.py").write_text("pass\n")
            mirror = tmp / "infrastructure" / ".terraform" / "modules" / "m"
            mirror.mkdir(parents=True)
            (mirror / "variables.tf").write_text(
                'variable "noise" {\n  type = string\n}\n'
            )
            entry = els._service_entry(
                tmp, {"slug": "svc", "root": "services/svc", "language": "python"},
                tmp / "services" / "svc", catalog=[],
            )
            self.assertTrue(
                entry["infra_discovery_gap"],
                "vendored .terraform copy falsely cleared the gap",
            )


class ServiceUnderInfraDirNoDoubleScan(unittest.TestCase):
    """PR #7 review IMPORTANT regression guard (Challenge 3): when a service
    root lives UNDER a repo-level infra dir (e.g. `deploy/myservice/`), the
    repo-level scan must NOT re-detect the service's own files — otherwise the
    same physical file gets two surfaces (scope=service→new_file AND
    scope=repo→checklist_only), producing conflicting downstream action items.

    NOTE: the two scopes use different path representations (service-relative
    vs repo-relative), so a naive string-dedup would silently no-op — the fix
    drops repo-scope surfaces whose path is under service['root']."""

    def test_service_under_deploy_dir_no_duplicate(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "deploy" / "myservice").mkdir(parents=True)
            (tmp / "deploy" / "myservice" / "variables.tf").write_text(
                'variable "x" {\n  type = string\n}\n'
            )
            (tmp / "deploy" / "myservice" / "app.py").write_text("pass\n")
            entry = els._service_entry(
                tmp,
                {"slug": "myservice", "root": "deploy/myservice", "language": "python"},
                tmp / "deploy" / "myservice", catalog=[],
            )
            tf = [(s["path"], s["scope"]) for s in entry["deployment_surfaces"]
                  if s["kind"] == "terraform"]
            # Exactly ONE terraform surface, and it's the service-scope one.
            self.assertEqual(len(tf), 1, f"double-scan produced {tf}")
            self.assertEqual(tf[0][1], "service")
            # Round-3 NIT pin: dropping the repo-scope DUPLICATE must NOT
            # leave the service falsely flagged as having no infra — the
            # surviving service-scope copy satisfies has_infra.
            self.assertFalse(entry["infra_discovery_gap"])

    def test_centralized_infra_not_dropped_for_normal_service(self):
        """The dedup must NOT over-drop: a service under services/ must still
        see the centralized repo-scope infra (services/moo-arc doesn't live
        under infrastructure/, so nothing should be dropped)."""
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            (tmp / "services" / "svc").mkdir(parents=True)
            (tmp / "services" / "svc" / "app.py").write_text("pass\n")
            (tmp / "infrastructure" / "terraform").mkdir(parents=True)
            (tmp / "infrastructure" / "terraform" / "variables.tf").write_text(
                'variable "x" {\n  type = string\n}\n'
            )
            entry = els._service_entry(
                tmp, {"slug": "svc", "root": "services/svc", "language": "python"},
                tmp / "services" / "svc", catalog=[],
            )
            repo_tf = [s for s in entry["deployment_surfaces"]
                       if s["kind"] == "terraform" and s["scope"] == "repo"]
            self.assertEqual(len(repo_tf), 1)
            self.assertEqual(
                repo_tf[0]["path"], "infrastructure/terraform/variables.tf"
            )


class RegistryDrivenScan(unittest.TestCase):
    def test_scan_service_uses_registry_nodes(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            svc = Path(tmp) / "svc"; (svc / "app").mkdir(parents=True)
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    api: str\n")
            res = els.scan_service_via_registry(svc, "python", search_roots=[svc])
            self.assertEqual(res.pattern_id, "python-pydantic-settings-v2")

    def test_code_node_subclass_wins_when_no_regex(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp); svc = repo / "svc"; (svc / "app").mkdir(parents=True)
            (repo / "shared").mkdir()
            (repo / "shared" / "base.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class AppBase(BaseSettings):\n    r: str = 'x'\n")
            (svc / "app" / "config.py").write_text(
                "from shared.base import AppBase\n"
                "class Settings(AppBase):\n    y: str = 'z'\n")
            res = els.scan_service_via_registry(svc, "python", search_roots=[svc, repo])
            self.assertEqual(res.pattern_id, "python-pydantic-settings-subclass")


class DerivedPathsInInventory(unittest.TestCase):
    def test_app_config_carries_node_and_derived_paths(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp); svc = repo / "services" / "svc"
            (svc / "app").mkdir(parents=True)
            (svc / "app" / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    api: str\n")
            entry = els._service_entry(
                repo, {"slug": "svc", "root": "services/svc", "language": "python"},
                svc, catalog=None)
            ac = entry["app_config"]
            self.assertEqual(ac["node_id"], "python-pydantic-settings-v2")
            self.assertTrue(ac["emit_path"].endswith("services/svc/app/moolabs_settings.py"))
            self.assertEqual(ac["import_path"], "app.moolabs_settings")


class ServiceSurfacePathRepoRelative(unittest.TestCase):
    """Q: a scope=service surface (.env.example) must be recorded REPO-relative
    (services/<svc>/.env.example), not service-relative (.env.example) — else the
    emit side (which treats paths as repo-relative) writes a stray repo-root
    ./.env.example instead of appending to the service's existing file."""

    def test_dotenv_surface_is_service_prefixed_with_repo_anchor(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            svc = repo / "services" / "moo-arc"
            svc.mkdir(parents=True)
            (svc / ".env.example").write_text("EXISTING=1\n")
            surfaces = els.scan_deployment_surfaces(svc, scope="service", path_anchor=repo)
            dotenv = [s.path for s in surfaces if s.kind == "dotenv_example"]
            self.assertEqual(dotenv, ["services/moo-arc/.env.example"])

    def test_without_repo_anchor_path_is_bare_the_bug(self):
        # Documents WHY the caller must pass path_anchor=repo_root: defaulting the
        # anchor to the service dir yields the bare root-relative path that caused Q.
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            repo = Path(t)
            svc = repo / "services" / "moo-arc"
            svc.mkdir(parents=True)
            (svc / ".env.example").write_text("EXISTING=1\n")
            surfaces = els.scan_deployment_surfaces(svc, scope="service")  # no anchor
            self.assertEqual([s.path for s in surfaces if s.kind == "dotenv_example"],
                             [".env.example"])


class SingleServiceFallback(unittest.TestCase):
    """Blocker 2 (raw dogfood): a per-`--service` run's signed doc has NO
    integration.services (not in the 04-final schema); the service identity is the
    top-level service_slug + scan_scope + repo. Without the fallback, the scan found
    no services and wired nothing."""

    def _parse(self, yaml_text):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "04-final.signed.yaml"
            p.write_text(yaml_text)
            return els.parse_services_and_granularity(p)

    def test_derives_single_service_from_top_level_when_integration_services_absent(self):
        svcs, _, _, _ = self._parse(
            "service_slug: moo-arc\n"
            "scan_scope:\n  service_subdir: services/moo-arc\n"
            "repo:\n  languages: [python]\n"
            "integration:\n  env_loader_granularity: per-service\n")
        self.assertEqual(svcs, [{"slug": "moo-arc", "root": "services/moo-arc",
                                 "language": "python"}])

    def test_root_is_service_dir_not_package_dir_from_target(self):
        # the moo-arc dogfood doc shape: repo has no languages; sdk_package_install does;
        # scan_scope.target is the PACKAGE dir (services/moo-arc/app). root must strip the
        # package tail to the service dir so config -> app/config.py -> import app.moolabs_*.
        svcs, _, _, _ = self._parse(
            "service_slug: moo-arc\n"
            "scan_scope:\n  target: services/moo-arc/app\n"
            "repo:\n  primary_path: .\n"
            "integration:\n  sdk_package_install:\n    python: pip install moolabs\n")
        self.assertEqual(svcs[0]["slug"], "moo-arc")
        self.assertEqual(svcs[0]["language"], "python")
        self.assertEqual(svcs[0]["root"], "services/moo-arc")   # service dir, NOT .../app

    def test_integration_services_still_wins_when_present(self):
        svcs, _, _, _ = self._parse(
            "service_slug: ignored\n"
            "integration:\n  services:\n    - slug: a\n      root: services/a\n      language: go\n")
        self.assertEqual([s["slug"] for s in svcs], ["a"])   # NOT the fallback

    def test_no_service_slug_no_derivation(self):
        svcs, _, _, _ = self._parse("repo:\n  shape: monorepo\n")
        self.assertEqual(svcs, [])


if __name__ == "__main__":
    unittest.main()
