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


if __name__ == "__main__":
    unittest.main()
