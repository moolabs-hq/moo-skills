"""Tests for secret_deploy_mirror — ADD the new secret's terraform wiring by mirroring the
exemplar's lines ADDITIVELY, anchored on the exemplar's UNIQUE env-var name.

The load-bearing safety property: a secret that SHARES the exemplar's store key
(`shared/api-key`) must NOT be touched — only the line carrying the unique anchor env var
is mirrored, and only as a new sibling (additive)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import secret_deploy_mirror as sdm  # noqa: E402


@unittest.skipUnless(shutil.which("grep") and shutil.which("git"), "grep+git required")
class PlanInserts(unittest.TestCase):
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

    def test_additive_insert_anchored_on_unique_env_does_not_touch_shared_key_secrets(self):
        d = self._repo({
            "regional/main.tf":
                '  { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '  { name = "API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n',
        })
        swaps = {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY", "shared/api-key": "arc/moolabs-api-key"}
        edits = sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", swaps)

        self.assertEqual(len(edits), 1)          # ONLY the exemplar's line — NOT API_KEY's
        e = edits[0]
        self.assertEqual(e.anchor_line, 1)
        self.assertIn('name = "MOOLABS_API_KEY"', e.new_line)
        self.assertIn("arc/moolabs-api-key", e.new_line)
        self.assertNotIn("ARC_GLOBAL_API_KEY", e.new_line)
        self.assertTrue(e.new_line.startswith("  "))            # indentation preserved
        self.assertIn("ARC_GLOBAL_API_KEY", e.anchor_text)      # original kept for review

    def test_idempotent_skips_file_already_carrying_new_env(self):
        d = self._repo({
            "main.tf": '{ name = "ARC_GLOBAL_API_KEY" }\n{ name = "MOOLABS_API_KEY" }\n',
        })
        swaps = {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY"}
        self.assertEqual(sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", swaps), [])

    def test_scopes_to_infra_files_never_app_code(self):
        d = self._repo({
            "app/config.py": 'arc_global_api_key = os.environ["ARC_GLOBAL_API_KEY"]\n',
            "main.tf": '{ name = "ARC_GLOBAL_API_KEY", valueFrom = x["shared/api-key"] }\n',
        })
        swaps = {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY", "shared/api-key": "arc/moolabs-api-key"}
        files = {e.file for e in sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", swaps)}
        self.assertEqual(files, {"main.tf"})                   # .py is app code -> never edited

    def test_empty_inputs_return_empty(self):
        self.assertEqual(sdm.plan_inserts("/tmp", "", {}), [])
        self.assertEqual(sdm.plan_inserts("/tmp", "X", {}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
