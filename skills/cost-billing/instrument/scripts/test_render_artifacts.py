#!/usr/bin/env python3
"""Unit tests for render_artifacts.py — the deterministic Phase-2d render driver.

Stdlib unittest; runs in the bash smoke suite's Phase 8. Tests that require
Jinja rendering are skipped when jinja2 is not installed (matching the suite's
soft-dependency posture).
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_artifacts as ra  # noqa: E402

_TEMPLATES_DIR = (
    Path(__file__).resolve().parent.parent / "assets" / "codemod-templates"
)

try:
    import jinja2  # noqa: F401
    _HAS_JINJA = True
except ImportError:
    _HAS_JINJA = False


def _tasks(stub_path="app/services/moolabs_settings.py", deployment_stubs=None,
           slugs=None):
    return {
        "env_wire_tasks": [{
            "task_id": "env_wire_001_svc",
            "service_slug": "svc",
            "mode": "stub",
            "settings_import_path": "app.services.moolabs_settings",
            "api_key_accessor": "get_settings().moolabs_api_key.get_secret_value()",
            "stub_emit_path": stub_path,
            "infra_discovery_gap": False,
            "deployment_stubs": deployment_stubs or [],
        }],
        "slugs_emit_tasks": slugs or [],
    }


class LanguageInference(unittest.TestCase):
    def test_python_from_stub_extension(self):
        self.assertEqual(ra.infer_language(_tasks("app/x/moolabs_settings.py")), "python")

    def test_typescript_from_stub_extension(self):
        self.assertEqual(
            ra.infer_language(_tasks("src/x/moolabs_settings.ts")), "typescript")

    def test_go_from_stub_extension(self):
        self.assertEqual(
            ra.infer_language(_tasks("internal/x/moolabs_settings.go")), "go")

    def test_defaults_python_when_unknown(self):
        self.assertEqual(ra.infer_language({"env_wire_tasks": []}), "python")

    def test_prefers_insert_task_language_over_stub_path(self):
        """Reliable source: the per-file insert tasks carry `language` even in
        modify mode (no stub_emit_path). An all-modify TS repo must NOT default
        to python."""
        tasks = {
            "tasks": [{"task_id": "tsk_1", "language": "typescript"}],
            "env_wire_tasks": [],  # all modify mode → no stub_emit_path
        }
        self.assertEqual(ra.infer_language(tasks), "typescript")

    def test_insert_task_language_wins_when_present(self):
        tasks = {
            "tasks": [{"task_id": "tsk_1", "language": "go"}],
            "env_wire_tasks": [{"stub_emit_path": "app/x/moolabs_settings.py"}],
        }
        self.assertEqual(ra.infer_language(tasks), "go")


class SlugsFilePath(unittest.TestCase):
    def test_python_convention(self):
        self.assertTrue(
            ra.slugs_file_path("python", "billing").endswith("slugs_billing.py"))

    def test_typescript_convention(self):
        self.assertTrue(
            ra.slugs_file_path("typescript", "billing").endswith("slugs_billing.ts"))

    def test_go_convention(self):
        self.assertTrue(
            ra.slugs_file_path("go", "billing").endswith("slugs_billing.go"))

    def test_hyphenated_product_slug_underscored(self):
        # A hyphenated product must yield a valid module identifier.
        p = ra.slugs_file_path("python", "moo-arc")
        self.assertNotIn("-", Path(p).name)


class PlanRenderJobs(unittest.TestCase):
    def test_stub_job_emitted(self):
        jobs = ra.plan_render_jobs(_tasks(), _TEMPLATES_DIR, Path("/repo"))
        stub = [j for j in jobs if j.kind == "stub"]
        self.assertEqual(len(stub), 1)
        self.assertEqual(stub[0].mode, "new_file")
        self.assertTrue(stub[0].template.endswith("python-moolabs-settings.py.j2"))

    def test_slugs_job_emitted_with_derived_path(self):
        tasks = _tasks(slugs=[{
            "task_id": "slugs_emit_001_billing", "product_slug": "billing",
            "generated_at": "2026-06-08T00:00:00+00:00",
            "constants": {"EVENT_TYPE": [{"name": "X", "value": "x.y"}]},
        }])
        jobs = ra.plan_render_jobs(tasks, _TEMPLATES_DIR, Path("/repo"))
        slugs = [j for j in jobs if j.kind == "slugs"]
        self.assertEqual(len(slugs), 1)
        self.assertTrue(slugs[0].dest.endswith("slugs_billing.py"))
        self.assertEqual(slugs[0].mode, "new_file")

    def test_deployment_modes_preserved(self):
        tasks = _tasks(deployment_stubs=[
            {"kind": "terraform", "emit_path": "infra/moolabs.tf", "mode": "new_file"},
            {"kind": "dotenv_example", "emit_path": "svc/.env.example", "mode": "append"},
            {"kind": "dockerfile", "mode": "checklist_only"},
        ])
        jobs = ra.plan_render_jobs(tasks, _TEMPLATES_DIR, Path("/repo"))
        modes = {j.context.get("kind"): j.mode for j in jobs if j.kind == "deployment"}
        self.assertEqual(modes.get("terraform"), "new_file")
        self.assertEqual(modes.get("dotenv_example"), "append")
        self.assertEqual(modes.get("dockerfile"), "checklist_only")


@unittest.skipUnless(_HAS_JINJA, "jinja2 not installed")
class RenderAndWrite(unittest.TestCase):
    def test_stub_written_new_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            ra.render_and_write(
                ra.plan_render_jobs(_tasks(), _TEMPLATES_DIR, repo),
                repo, _TEMPLATES_DIR,
            )
            self.assertTrue((repo / "app/services/moolabs_settings.py").is_file())

    def test_slugs_written_with_constants(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks = _tasks(slugs=[{
                "task_id": "slugs_emit_001_billing", "product_slug": "billing",
                "generated_at": "2026-06-08T00:00:00+00:00",
                "constants": {"EVENT_TYPE": [
                    {"name": "SEAT_ASSIGNED", "value": "seat.assigned"}]},
            }])
            ra.render_and_write(
                ra.plan_render_jobs(tasks, _TEMPLATES_DIR, repo), repo, _TEMPLATES_DIR)
            written = list(repo.rglob("slugs_billing.py"))
            self.assertEqual(len(written), 1)
            self.assertIn("SEAT_ASSIGNED", written[0].read_text())

    def test_checklist_only_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            tasks = _tasks(deployment_stubs=[
                {"kind": "dockerfile", "mode": "checklist_only",
                 "source_path": "svc/Dockerfile"},
            ])
            manifest = ra.render_and_write(
                ra.plan_render_jobs(tasks, _TEMPLATES_DIR, repo), repo, _TEMPLATES_DIR)
            # No file written; the checklist item is recorded.
            self.assertFalse((repo / "svc/Dockerfile").exists())
            self.assertTrue(any(m["action"] == "checklist" for m in manifest))

    def test_append_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "svc").mkdir()
            envfile = repo / "svc" / ".env.example"
            envfile.write_text("EXISTING=1\n")
            tasks = _tasks(deployment_stubs=[
                {"kind": "dotenv_example", "emit_path": "svc/.env.example",
                 "mode": "append"},
            ])
            jobs = ra.plan_render_jobs(tasks, _TEMPLATES_DIR, repo)
            ra.render_and_write(jobs, repo, _TEMPLATES_DIR)
            after_first = envfile.read_text()
            # Original content preserved (append, not overwrite).
            self.assertIn("EXISTING=1", after_first)
            self.assertIn("MOOLABS_API_KEY", after_first)
            # Second run must NOT duplicate the MOOLABS_API_KEY line.
            ra.render_and_write(jobs, repo, _TEMPLATES_DIR)
            after_second = envfile.read_text()
            self.assertEqual(
                after_first, after_second,
                "append must be idempotent — MOOLABS_API_KEY duplicated on re-run")

    def test_new_file_refuses_to_clobber_customer_file(self):
        """new_file must NOT overwrite a hand-written customer file (no
        generated marker) at the destination path — file corruption guard."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app" / "services").mkdir(parents=True)
            customer = repo / "app" / "services" / "moolabs_settings.py"
            customer.write_text("# my hand-written config\nAPI = 'keep me'\n")
            manifest = ra.render_and_write(
                ra.plan_render_jobs(_tasks(), _TEMPLATES_DIR, repo),
                repo, _TEMPLATES_DIR)
            # Untouched.
            self.assertEqual(customer.read_text(),
                             "# my hand-written config\nAPI = 'keep me'\n")
            self.assertTrue(any(m["action"] == "skipped_customer_file"
                                for m in manifest))

    def test_new_file_regenerates_own_prior_output(self):
        """A pre-existing file carrying our generated marker IS overwritten
        (regenerated) — re-runs must refresh our own artifacts."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "app" / "services").mkdir(parents=True)
            ours = repo / "app" / "services" / "moolabs_settings.py"
            ours.write_text("# Generated by /cost-billing-instrument (stale)\n")
            manifest = ra.render_and_write(
                ra.plan_render_jobs(_tasks(), _TEMPLATES_DIR, repo),
                repo, _TEMPLATES_DIR)
            self.assertNotIn("(stale)", ours.read_text())
            self.assertTrue(any(m["action"] == "regenerated" for m in manifest))

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            manifest = ra.render_and_write(
                ra.plan_render_jobs(_tasks(), _TEMPLATES_DIR, repo),
                repo, _TEMPLATES_DIR, dry_run=True)
            self.assertFalse((repo / "app/services/moolabs_settings.py").exists())
            self.assertTrue(all(m["action"] in ("would_write", "would_checklist",
                                                "would_append") for m in manifest))


if __name__ == "__main__":
    unittest.main()
