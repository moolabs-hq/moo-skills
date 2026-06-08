#!/usr/bin/env python3
"""Install-portability: the discovery/instrument scripts must run STANDALONE in
the INSTALLED layout, where install.sh ships `shared/` as a SIBLING skill dir
`cost-billing-shared` (not a `shared/` subdir of `cost-billing/`).

This closes the gap that let a PR #11 regression ship: the rest of the suite's
tests import the scripts with the package dir already on sys.path (or just
py_compile them), so they never exercised `python3 <path>/script.py` in the
installed sibling layout — where the hardcoded `parents[2]/"shared"/"scripts"`
import path resolved to a non-existent dir and crashed every installed user with
`ModuleNotFoundError: No module named 'strategies'`.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# .../cost-billing/discovery/scripts/this_file → cost-billing root is parents[2]
_CB_ROOT = Path(__file__).resolve().parents[2]


def _build_installed_layout(dest: Path) -> Path:
    """Copy each persona skill as a SIBLING `cost-billing-<skill>` dir, mirroring
    install.sh's layout (shared → cost-billing-shared)."""
    for skill in ("discovery", "instrument", "shared"):
        src = _CB_ROOT / skill
        if src.is_dir():
            shutil.copytree(src, dest / f"cost-billing-{skill}")
    return dest


class InstalledLayoutStandaloneInvocation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.root = _build_installed_layout(Path(self._tmp))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _run(self, rel: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / rel), *args],
            capture_output=True, text=True, timeout=60,
        )

    def test_env_loader_scan_imports_in_installed_layout(self):
        r = self._run("cost-billing-discovery/scripts/env_loader_scan.py", "--help")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("ModuleNotFoundError", r.stderr)

    def test_config_wire_imports_in_installed_layout(self):
        r = self._run("cost-billing-instrument/scripts/config_wire.py", "--help")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("ModuleNotFoundError", r.stderr)

    def test_task_planner_imports_in_installed_layout(self):
        r = self._run("cost-billing-instrument/scripts/task_planner.py", "--help")
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertNotIn("ModuleNotFoundError", r.stderr)

    def test_registry_and_frameworks_resolve_in_installed_layout(self):
        # The frameworks asset dir must resolve to cost-billing-shared/assets/...
        # and load all 16 nodes — not just the scripts importing.
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import env_loader_scan as e;"
            "d=e._FRAMEWORKS_DIR;"
            "print(d.is_dir(), len(list(d.glob('*/*.yaml'))))"
            % str(self.root / "cost-billing-discovery" / "scripts")
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
        self.assertIn("True 16", r.stdout.strip())

    def test_catalog_arg_accepted_for_backcompat(self):
        # PR #11 retired --catalog; it must still be ACCEPTED (ignored) so stale
        # invocations don't hard-fail with "unrecognized arguments".
        r = self._run("cost-billing-discovery/scripts/env_loader_scan.py",
                      "--catalog", "/nonexistent/env-loader-patterns.yaml",
                      "--repo-root", self._tmp)
        self.assertNotIn("unrecognized arguments", r.stderr)
        self.assertNotIn("ModuleNotFoundError", r.stderr)


if __name__ == "__main__":
    unittest.main()
