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


if __name__ == "__main__":
    unittest.main()
