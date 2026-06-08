#!/usr/bin/env python3
"""Detection-contract tests: each node's signals/detector match their fixture,
and the registry assembles the on-disk tree."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import framework_registry as fr  # noqa: E402

_FW = Path(__file__).resolve().parents[1] / "assets" / "frameworks"


class NodeTreeLoads(unittest.TestCase):
    def test_pydantic_nodes_present(self):
        reg = fr.load_registry(_FW)
        self.assertIn("pydantic-settings-v2", reg["python"])
        self.assertIn("pydantic-settings-subclass", reg["python"])
        # subclass is a code node referencing the named detector
        sub = reg["python"]["pydantic-settings-subclass"]
        self.assertEqual(sub.detection["kind"], "code")
        self.assertEqual(sub.detection["detector"], "pydantic_settings_subclass")
        # v2 regex routes modify; subclass routes stub
        self.assertEqual(reg["python"]["pydantic-settings-v2"].wiring["mode"], "modify")
        self.assertEqual(sub.wiring["mode"], "stub")


class AllExistingPatternsMigrated(unittest.TestCase):
    def test_ids_present_and_routing(self):
        reg = fr.load_registry(_FW)
        ids = {n.id for fws in reg.values() for n in fws.values()}
        for expected in (
            "python-pydantic-settings-v2", "python-pydantic-v1-settings",
            "python-decouple", "python-dotenv-os-getenv",
            "python-pydantic-settings-subclass",
            "ts-zod-env-schema", "ts-process-env-direct", "ts-env-var-library",
            "go-viper", "go-envconfig", "go-os-getenv",
        ):
            self.assertIn(expected, ids)
        # typescript + go each have their migrated patterns plus a new node
        # (convict / koanf added in the new-framework tasks).
        self.assertEqual(len(reg["typescript"]), 4)
        self.assertEqual(len(reg["go"]), 4)
        # go-envconfig is the only non-pydantic modify node (it has an accessor)
        self.assertEqual(reg["go"]["envconfig"].wiring["mode"], "modify")
        self.assertEqual(reg["go"]["viper"].wiring["mode"], "stub")
        self.assertEqual(reg["typescript"]["zod-env"].wiring["mode"], "stub")


class NewFrameworkDetection(unittest.TestCase):
    """Detection contract for the newly-added config frameworks, via the
    registry scan path (scan_file_via_registry)."""

    def setUp(self):
        import sys as _sys
        _sys.path.insert(
            0, str(Path(__file__).resolve().parents[2] / "discovery" / "scripts"))
        import env_loader_scan  # noqa: F401
        self.els = env_loader_scan

    def _detect(self, suffix, body, language):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / f"config{suffix}"
            f.write_text(body)
            return self.els.scan_file_via_registry(f, language)

    def test_dynaconf(self):
        r = self._detect(".py",
            "from dynaconf import Dynaconf\nsettings = Dynaconf(envvar_prefix='APP')\n",
            "python")
        self.assertEqual(r.pattern_id, "python-dynaconf")

    def test_django_settings(self):
        r = self._detect(".py",
            "from django.conf import settings\nSECRET_KEY = 'x'\nDATABASES = {}\n",
            "python")
        self.assertEqual(r.pattern_id, "python-django-settings")

    def test_environs(self):
        r = self._detect(".py",
            "from environs import Env\nenv = Env()\nDB = env.str('DB')\n",
            "python")
        self.assertEqual(r.pattern_id, "python-environs")

    def test_convict(self):
        r = self._detect(".ts",
            "import convict from 'convict';\nconst c = convict({ x: {} });\n",
            "typescript")
        self.assertEqual(r.pattern_id, "ts-convict")

    def test_koanf(self):
        r = self._detect(".go",
            'package x\nimport "github.com/knadh/koanf"\nfunc f() { k := koanf.New(".") }\n',
            "go")
        self.assertEqual(r.pattern_id, "go-koanf")


if __name__ == "__main__":
    unittest.main()
