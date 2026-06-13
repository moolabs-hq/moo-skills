"""Tests for reachability — the gate that flags emit sites placed in functions PRODUCTION
never calls (#572 dead-emit class: test-only / admin-only / dead-twin / orphan).

The verdict is bounded + deterministic: find a target's CALLERS, classify them by file
kind. Only-test / only-admin / no callers -> FLAGGED. A prod caller -> live_candidate
(not auto-confirmed — dynamic dispatch needs the runtime trace; it just isn't obviously
dead). Mirrors the audit's three dead emits."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reachability as rc  # noqa: E402


class FileKind(unittest.TestCase):
    def test_path_kind_heuristics(self):
        self.assertEqual(rc._file_kind("tests/integration/test_scenario_b.py"), "test")
        self.assertEqual(rc._file_kind("app/agents/risk_scoring_test.py"), "test")
        self.assertEqual(rc._file_kind("src/foo.spec.ts"), "test")
        self.assertEqual(rc._file_kind("app/admin/router.py"), "admin")
        self.assertEqual(rc._file_kind("e2e/flows/checkout.ts"), "e2e")
        self.assertEqual(rc._file_kind("app/agents/orchestrator.py"), "prod")


@unittest.skipUnless(shutil.which("grep") and shutil.which("git"), "grep+git required")
class ClassifyReachability(unittest.TestCase):
    def _repo(self, files: dict[str, str]) -> str:
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        for path, content in files.items():
            full = os.path.join(d, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(content)
        subprocess.run(["git", "init", "-q", d], check=True)
        subprocess.run(["git", "-C", d, "add", "-A"], check=True)
        return d

    def test_prod_caller_is_unverified_not_flagged(self):
        d = self._repo({
            "app/agents/risk_scoring.py": "def evaluate_case(x):\n    return x\n",
            "app/orchestrator.py": "from app.agents.risk_scoring import evaluate_case\n"
                                   "def run():\n    evaluate_case(1)\n",
        })
        r = rc.classify_reachability(d, "evaluate_case", "app/agents/risk_scoring.py")
        self.assertEqual(r.status, "unverified")   # NOT a pass — runtime trace still owed
        self.assertFalse(r.flagged)
        self.assertIn("app/orchestrator.py", r.prod_caller_files)

    def test_prod_caller_alongside_admin_does_not_flag(self):
        # the advisor's false-positive guard: any prod caller -> unverified, even with an
        # admin caller present (else a live site that also has an admin entry would flag).
        d = self._repo({
            "app/agents/risk_scoring.py": "def evaluate_case(x):\n    return x\n",
            "app/orchestrator.py": "from x import evaluate_case\ndef run():\n    evaluate_case(1)\n",
            "app/admin/router.py": "from x import evaluate_case\ndef debug():\n    evaluate_case(2)\n",
        })
        r = rc.classify_reachability(d, "evaluate_case", "app/agents/risk_scoring.py")
        self.assertEqual(r.status, "unverified")
        self.assertFalse(r.flagged)

    def test_only_test_caller_is_flagged_test_only(self):
        # the audit's dispute case: classify_dispute called ONLY from a test
        d = self._repo({
            "app/agents/dispute.py": "def classify_dispute(x):\n    return x\n",
            "tests/integration/test_scenario_b.py":
                "from app.agents.dispute import classify_dispute\n"
                "def test_b():\n    classify_dispute(1)\n",
        })
        r = rc.classify_reachability(d, "classify_dispute", "app/agents/dispute.py")
        self.assertEqual(r.status, "test_only")
        self.assertTrue(r.flagged)

    def test_only_admin_caller_is_flagged(self):
        # the audit's cash-app case: apply_single_remittance only from admin/router
        d = self._repo({
            "app/cash.py": "def apply_single_remittance(x):\n    return x\n",
            "app/admin/router.py": "from app.cash import apply_single_remittance\n"
                                   "def sweep():\n    apply_single_remittance(1)\n",
        })
        r = rc.classify_reachability(d, "apply_single_remittance", "app/cash.py")
        self.assertEqual(r.status, "admin_e2e_only")
        self.assertTrue(r.flagged)

    def test_no_caller_is_flagged_orphan(self):
        # the audit's dunning-email case: the standalone async twin nothing live calls
        d = self._repo({
            "app/comms.py": "def generate_dunning_email_async(x):\n    return x\n",
        })
        r = rc.classify_reachability(d, "generate_dunning_email_async", "app/comms.py")
        self.assertEqual(r.status, "orphan")
        self.assertTrue(r.flagged)

    def test_definition_line_is_not_counted_as_a_caller(self):
        d = self._repo({"app/x.py": "def foo(a):\n    return a\n"})
        self.assertEqual(rc.find_callers(d, "foo", "app/x.py"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
