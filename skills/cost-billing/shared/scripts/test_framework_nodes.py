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
        # 3 typescript + 3 go nodes
        self.assertEqual(len(reg["typescript"]), 3)
        self.assertEqual(len(reg["go"]), 3)
        # go-envconfig is the only non-pydantic modify node (it has an accessor)
        self.assertEqual(reg["go"]["envconfig"].wiring["mode"], "modify")
        self.assertEqual(reg["go"]["viper"].wiring["mode"], "stub")
        self.assertEqual(reg["typescript"]["zod-env"].wiring["mode"], "stub")


if __name__ == "__main__":
    unittest.main()
