from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
HARNESS = HERE / "pressure_harness.py"


class PressureHarnessTests(unittest.TestCase):
    def test_harness_is_executable_deterministic_and_compares_both_cases(self) -> None:
        self.assertTrue(HARNESS.is_file())
        self.assertTrue(os.access(HARNESS, os.X_OK))

        command = [sys.executable, str(HARNESS)]
        first = subprocess.run(command, check=False, capture_output=True, text=True)
        second = subprocess.run(command, check=False, capture_output=True, text=True)

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        report = json.loads(first.stdout)
        self.assertEqual([case["case"] for case in report["cases"]], ["dynamic-route", "raw-header"])
        self.assertTrue(all(case["naive_unsafe"] for case in report["cases"]))
        self.assertTrue(all(case["scanner_contract_honest"] for case in report["cases"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
