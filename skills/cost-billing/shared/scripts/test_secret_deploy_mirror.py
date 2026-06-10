"""Tests for secret_deploy_mirror — ADD the new secret's terraform wiring by mirroring the
exemplar's lines ADDITIVELY, anchored on the exemplar's UNIQUE env-var name.

The load-bearing safety property: a secret that SHARES the exemplar's store key
(`shared/api-key`) must NOT be touched — only the line carrying the unique anchor env var
is mirrored, and only as a new sibling (additive)."""

from __future__ import annotations

import contextlib
import io
import json
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
        # ANCHOR-LOCAL: the mirror is the line right AFTER the anchor -> skip (re-run).
        d = self._repo({
            "main.tf": '{ name = "ARC_GLOBAL_API_KEY" }\n{ name = "MOOLABS_API_KEY" }\n',
        })
        swaps = {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY"}
        self.assertEqual(sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", swaps), [])

    def test_distant_new_env_in_OTHER_block_does_not_suppress_this_block(self):
        # THE moo-arc bug (regional/main.tf): the UI's ui_secrets block already carries
        # MOOLABS_API_KEY, but arc's OWN task-def must still be wired. A file-wide
        # idempotency check skipped the whole file -> arc unwired in regional. Anchor-
        # local idempotency must still wire arc's anchor despite the distant MOOLABS.
        # Convention: REUSE shared/api-key (no store swap) — matching the UI + ARC_GLOBAL.
        d = self._repo({
            "regional/main.tf":
                '    { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '    { name = "RESEND_WEBHOOK_SECRET", valueFrom = x }\n'
                '  ]\n'
                '  ui_secrets = [\n'
                '    { name = "MOOLABS_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '  ]\n',
        })
        swaps = {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY"}   # reuse shared/api-key
        edits = sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", swaps)
        self.assertEqual(len(edits), 1)                    # arc's block IS wired
        self.assertEqual(edits[0].anchor_line, 1)
        self.assertIn('name = "MOOLABS_API_KEY"', edits[0].new_line)
        self.assertIn('shared/api-key', edits[0].new_line)  # convention reuse, NOT a dedicated key

    def test_comment_lines_mentioning_env_are_not_mirrored(self):
        # real moo-arc regional/main.tf has COMMENTS mentioning ARC_GLOBAL_API_KEY
        # (e.g. "# ARC accepts this shared key via ARC_GLOBAL_API_KEY ..."). grep finds
        # them, but mirroring a comment emits a nonsense sibling — only WIRING lines count.
        d = self._repo({
            "main.tf":
                '    # ARC accepts this shared key via ARC_GLOBAL_API_KEY and resolves tenant\n'
                '    { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n',
        })
        edits = sdm.plan_inserts(d, "ARC_GLOBAL_API_KEY", {"ARC_GLOBAL_API_KEY": "MOOLABS_API_KEY"})
        self.assertEqual(len(edits), 1)            # ONLY the wiring line, NOT the comment
        self.assertEqual(edits[0].anchor_line, 2)

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


@unittest.skipUnless(shutil.which("grep") and shutil.which("git"), "grep+git required")
class DeclarationAndApply(unittest.TestCase):
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

    def test_declaration_mirrors_existing_entry_in_same_namespace(self):
        d = self._repo({
            "environments/prod/main.tf":
                '    "arc/together-api-key" = { description = "Together API key" }\n'
                '    "bff/resend-api-key"   = { description = "Resend email API key" }\n',
        })
        e = sdm.plan_declaration_insert(d, "arc/moolabs-api-key", "Moolabs SDK API key")
        self.assertIsNotNone(e)
        self.assertIn('"arc/moolabs-api-key"', e.new_line)
        self.assertIn('description = "Moolabs SDK API key"', e.new_line)
        self.assertTrue(e.new_line.startswith("    "))   # indentation mirrored

    def test_declaration_idempotent_when_already_declared(self):
        d = self._repo({"main.tf": '    "arc/moolabs-api-key" = { description = "x" }\n'})
        self.assertIsNone(sdm.plan_declaration_insert(d, "arc/moolabs-api-key", "y"))

    def test_apply_writes_sibling_after_anchor(self):
        d = self._repo({"main.tf": "line1\nANCHOR\nline3\n"})
        edits = [sdm.InsertEdit(file="main.tf", anchor_line=2, anchor_text="ANCHOR", new_line="NEW")]
        self.assertEqual(sdm.apply_inserts(d, edits), ["main.tf"])
        with open(os.path.join(d, "main.tf")) as f:
            self.assertEqual(f.read(), "line1\nANCHOR\nNEW\nline3\n")

    def test_apply_multiple_inserts_same_file_keeps_line_numbers_valid(self):
        d = self._repo({"main.tf": "a\nb\nc\n"})
        edits = [
            sdm.InsertEdit(file="main.tf", anchor_line=1, anchor_text="a", new_line="A2"),
            sdm.InsertEdit(file="main.tf", anchor_line=3, anchor_text="c", new_line="C2"),
        ]
        sdm.apply_inserts(d, edits)
        with open(os.path.join(d, "main.tf")) as f:
            self.assertEqual(f.read(), "a\nA2\nb\nc\nC2\n")   # both land at the right anchors


@unittest.skipUnless(shutil.which("grep") and shutil.which("git"), "grep+git required")
class Cli(unittest.TestCase):
    """The CLI Phase 1.7 invokes: --plan previews the diff for the permission ask
    (writes nothing + surfaces already-wired sites); --apply writes the additive edit."""

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

    def _run(self, argv: list[str]):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = sdm.main(argv)
        return rc, json.loads(buf.getvalue())

    def test_plan_reuse_convention_surfaces_already_wired_writes_nothing(self):
        d = self._repo({
            # arc's block: fresh -> injection edit (REUSE shared/api-key, no store swap)
            "environments/prod/main.tf":
                '    { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '    { name = "CLS_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n',
            # already mirrored AT THIS anchor (a prior apply) -> skipped + SURFACED
            "regional/main.tf":
                '    { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '    { name = "MOOLABS_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n',
        })
        rc, out = self._run([
            "--repo-root", d, "--anchor-env", "ARC_GLOBAL_API_KEY",
            "--swaps", "ARC_GLOBAL_API_KEY=MOOLABS_API_KEY", "--plan",   # reuse shared/api-key
        ])
        self.assertEqual(rc, 0)
        inj_files = {e["file"] for e in out["injection_edits"]}
        self.assertIn("environments/prod/main.tf", inj_files)       # fresh -> planned
        self.assertNotIn("regional/main.tf", inj_files)             # already mirrored at anchor -> skip
        self.assertIn('valueFrom = module.secrets.secret_arns["shared/api-key"]',
                      out["injection_edits"][0]["new_line"])        # CONVENTION reuse, not dedicated
        self.assertIsNone(out["declaration_edit"])                  # no new store key -> no declaration
        surfaced = {s["file"] for s in out["skipped_already_wired"]}
        self.assertIn("regional/main.tf", surfaced)                 # honest surfacing
        self.assertNotIn("MOOLABS_API_KEY",
                         open(os.path.join(d, "environments/prod/main.tf")).read())  # wrote nothing

    def test_apply_reuse_convention_wires_this_block_not_the_other(self):
        # The moo-arc multi-block reality: arc's task-def + the UI's ui_secrets (already has
        # MOOLABS_API_KEY) in ONE file. --apply must wire ARC's block and leave the UI's alone.
        d = self._repo({
            "regional/main.tf":
                '    { name = "ARC_GLOBAL_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '    { name = "RESEND_WEBHOOK_SECRET", valueFrom = x }\n'
                '  ]\n'
                '  ui_secrets = [\n'
                '    { name = "MOOLABS_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"] }\n'
                '  ]\n',
        })
        rc, out = self._run([
            "--repo-root", d, "--anchor-env", "ARC_GLOBAL_API_KEY",
            "--swaps", "ARC_GLOBAL_API_KEY=MOOLABS_API_KEY", "--apply",
        ])
        self.assertEqual(rc, 0)
        self.assertIn("regional/main.tf", out["written"])
        lines = open(os.path.join(d, "regional/main.tf")).read().splitlines()
        self.assertIn("ARC_GLOBAL_API_KEY", lines[0])               # anchor untouched
        self.assertIn(                                              # arc's mirror inserted right after
            'name = "MOOLABS_API_KEY", valueFrom = module.secrets.secret_arns["shared/api-key"]', lines[1])
        content = "\n".join(lines)
        self.assertEqual(content.count('name = "MOOLABS_API_KEY"'), 2)  # new arc + pre-existing UI
        self.assertIn("ui_secrets", content)                        # UI block intact


if __name__ == "__main__":
    unittest.main(verbosity=2)
