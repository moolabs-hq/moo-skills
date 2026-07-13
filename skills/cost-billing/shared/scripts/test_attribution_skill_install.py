from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
INSTALLER = REPO_ROOT / "skills" / "cost-billing" / "install.sh"
SKILL_PATH = REPO_ROOT / "skills" / "attribution-middleware-discovery"
PLUGIN_PATH = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"
SUITE_TEST_PATH = REPO_ROOT / "skills" / "cost-billing" / "scripts" / "test-suite.sh"


class AttributionSkillInstallTests(unittest.TestCase):
    def _build_plugin_zip(self, package_dir: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "/bin/bash",
                str(INSTALLER),
                "--plugin-zip",
                "--persona",
                "engineering",
                "--plugin-zip-dir",
                str(package_dir),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def _no_rsync_path(self, path_dir: Path) -> str:
        for command in ("awk", "bash", "cat", "cp", "dirname", "du", "find", "mkdir", "python3", "rm", "sed", "tail", "unzip", "zip"):
            executable = shutil.which(command)
            self.assertIsNotNone(executable, f"required test command missing: {command}")
            (path_dir / command).symlink_to(executable)
        return str(path_dir)

    def _write_fake_python(self, path: Path) -> None:
        path.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${PYTHON_PROBE_LOG:-}\" ]; then\n"
            "  printf '%s\\n' \"$*\" >> \"$PYTHON_PROBE_LOG\"\n"
            "fi\n"
            "exit \"${FAKE_PYTHON_EXIT_CODE:-0}\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def test_root_plugin_registers_attribution_discovery(self) -> None:
        plugin = json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))
        self.assertIn("./skills/attribution-middleware-discovery", plugin["skills"])

    def test_engineering_dry_run_uses_root_skill_source(self) -> None:
        result = subprocess.run(
            [
                "bash",
                str(INSTALLER),
                "--persona",
                "engineering",
                "--dry-run",
                "--skip-codegraph",
                "--skip-plugins",
                "--no-bootstrap-cta",
                "--no-prune",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(SKILL_PATH.is_dir())
        self.assertIn(f"would copy:   {SKILL_PATH}", result.stdout)
        self.assertIn("attribution-middleware-discovery", result.stdout)

    def test_suite_executes_root_attribution_skill_tests(self) -> None:
        suite_test = SUITE_TEST_PATH.read_text(encoding="utf-8")
        self.assertIn("ATTRIBUTION_SKILL_ROOT", suite_test)
        self.assertIn('"$ATTRIBUTION_SKILL_ROOT"', suite_test)

    def test_switching_to_finance_prunes_attribution_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            stale = Path(home) / ".codex" / "skills" / "attribution-middleware-discovery"
            stale.mkdir(parents=True)
            (stale / "stale.txt").write_text("stale\n", encoding="utf-8")
            env = os.environ.copy()
            env["HOME"] = home
            result = subprocess.run(
                [
                    "bash",
                    str(INSTALLER),
                    "--platform",
                    "codex",
                    "--user",
                    "--persona",
                    "finance",
                    "--skip-codegraph",
                    "--skip-plugins",
                    "--no-bootstrap-cta",
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists(), result.stdout)

    def test_installed_attribution_skill_finds_installed_discovery_scanner(self) -> None:
        with tempfile.TemporaryDirectory() as home:
            env = os.environ.copy()
            env["HOME"] = home
            install = subprocess.run(
                [
                    "bash",
                    str(INSTALLER),
                    "--platform",
                    "codex",
                    "--user",
                    "--persona",
                    "engineering",
                    "--skip-codegraph",
                    "--skip-plugins",
                    "--no-bootstrap-cta",
                    "--no-prune",
                ],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr)
            discover = Path(home) / ".codex" / "skills" / "attribution-middleware-discovery" / "scripts" / "discover.py"
            fixture = SKILL_PATH / "fixtures" / "drift"
            output = Path(home) / "instrumentation-map.json"
            scan = subprocess.run(
                [sys.executable, str(discover), "--repo", str(fixture), "--output", str(output),
                 "--generated-at", "2026-07-13T00:00:00Z"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertTrue(output.is_file())

    def test_extracted_engineering_packages_run_without_site_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "packages"
            result = subprocess.run(
                [
                    "bash",
                    str(INSTALLER),
                    "--package",
                    "--persona",
                    "engineering",
                    "--package-dir",
                    str(package_dir),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

            extracted = Path(temp_dir) / "extracted"
            extracted.mkdir()
            with zipfile.ZipFile(package_dir / "attribution-middleware-discovery.zip") as package:
                package.extractall(extracted)
            signoff_root = extracted / "cost-billing-signoff"
            with zipfile.ZipFile(package_dir / "cost-billing-signoff.zip") as package:
                package.extractall(signoff_root)

            attribution_root = extracted
            self.assertTrue((attribution_root / "SKILL.md").is_file())
            self.assertTrue((attribution_root / "scripts" / "vendor" / "repo_scan.py").is_file())
            self.assertTrue((attribution_root / "scripts" / "attribution_scan.py").is_file())
            self.assertTrue((attribution_root / "scripts" / "drift_lint.py").is_file())
            customer_repo = Path(temp_dir) / "customer-repo"
            shutil.copytree(attribution_root / "fixtures" / "drift", customer_repo)
            (customer_repo / "service" / "app.py").write_text(
                "from fastapi import Depends, FastAPI\n"
                "app = FastAPI()\n"
                "app.add_middleware(AttributionMiddleware)\n"
                "@app.get('/orders')\n"
                "def orders(claims=Depends(verify_jwt)):\n"
                "    customer = claims.customer_id\n"
                "    parsed = UUID(customer)\n"
                "    return {'customer': str(parsed)}\n",
                encoding="utf-8",
            )
            for command in (
                ["git", "init", "--quiet"],
                ["git", "add", "."],
                [
                    "git",
                    "-c",
                    "user.name=Test Engineer",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "--quiet",
                    "-m",
                    "initial fixture",
                ],
            ):
                initialized = subprocess.run(
                    command,
                    cwd=customer_repo,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(initialized.returncode, 0, initialized.stderr)

            output = customer_repo / ".moolabs" / "attribution" / "instrumentation-map.yaml"
            clean_env = {"PATH": os.environ["PATH"], "PYTHONNOUSERSITE": "1"}
            scan = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(attribution_root / "scripts" / "discover.py"),
                    "--repo",
                    str(customer_repo),
                    "--output",
                    str(output),
                    "--generated-at",
                    "2026-07-13T00:00:00Z",
                ],
                env=clean_env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertTrue(output.is_file())

            signoff = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(signoff_root / "scripts" / "attribution_map_signoff.py"),
                    "create",
                    str(output),
                    "--output",
                    str(Path(temp_dir) / "instrumentation-map-signoff.yaml"),
                    "--repo",
                    str(customer_repo),
                    "--operator",
                    "A. Engineer",
                    "--codegen-model",
                    "implementation-model",
                    "--reviewer-model",
                    "independent-reviewer",
                    "--review-evidence",
                    "review://ws5/extracted-package",
                    "--review-verdict",
                    "clean",
                    "--findings-resolved",
                    "0",
                    "--findings-rejected-as-false-positive",
                    "0",
                ],
                env=clean_env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(signoff.returncode, 0, signoff.stderr)

            verify = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(signoff_root / "scripts" / "attribution_map_signoff.py"),
                    "verify",
                    str(output),
                    str(Path(temp_dir) / "instrumentation-map-signoff.yaml"),
                    "--repo",
                    str(customer_repo),
                ],
                env=clean_env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)

    def test_plugin_zip_version_matches_root_and_marketplace_versions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package_dir = Path(temp_dir) / "plugin"
            result = self._build_plugin_zip(package_dir)
            self.assertEqual(result.returncode, 0, result.stderr)

            with zipfile.ZipFile(package_dir / "cost-billing-plugin.zip") as package:
                packaged_plugin = json.loads(package.read(".claude-plugin/plugin.json"))

            root_plugin = json.loads(PLUGIN_PATH.read_text(encoding="utf-8"))
            marketplace = json.loads(MARKETPLACE_PATH.read_text(encoding="utf-8"))
            marketplace_plugin = next(plugin for plugin in marketplace["plugins"] if plugin["name"] == root_plugin["name"])
            self.assertEqual(packaged_plugin["version"], root_plugin["version"])
            self.assertEqual(packaged_plugin["version"], marketplace_plugin["version"])

    def test_plugin_zip_falls_back_without_rsync_and_keeps_attribution_skill_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            package_dir = temp_path / "plugin"
            no_rsync_bin = temp_path / "bin"
            no_rsync_bin.mkdir()
            env = os.environ.copy()
            env["PATH"] = self._no_rsync_path(no_rsync_bin)
            result = self._build_plugin_zip(package_dir, env)
            self.assertEqual(result.returncode, 0, result.stderr)

            extracted = temp_path / "extracted"
            extracted.mkdir()
            with zipfile.ZipFile(package_dir / "cost-billing-plugin.zip") as package:
                package.extractall(extracted)

            attribution_root = extracted / "skills" / "attribution-middleware-discovery"
            self.assertTrue((attribution_root / "SKILL.md").is_file())
            self.assertTrue((attribution_root / "scripts" / "vendor" / "repo_scan.py").is_file())
            output = temp_path / "instrumentation-map.yaml"
            scan = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(attribution_root / "scripts" / "discover.py"),
                    "--repo",
                    str(attribution_root / "fixtures" / "drift"),
                    "--output",
                    str(output),
                    "--generated-at",
                    "2026-07-13T00:00:00Z",
                ],
                env={"PATH": os.environ["PATH"], "PYTHONNOUSERSITE": "1"},
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scan.returncode, 0, scan.stderr)
            self.assertTrue(output.is_file())

    def test_plugin_packaging_accepts_a_supported_python_probe_without_host_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_python = temp_path / "python"
            probe_log = temp_path / "python-probe.log"
            self._write_fake_python(fake_python)
            env = os.environ.copy()
            env.update({"PYTHON_BIN": str(fake_python), "PYTHON_PROBE_LOG": str(probe_log)})
            result = self._build_plugin_zip(temp_path / "plugin", env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(probe_log.is_file())
            self.assertIn("sys.version_info", probe_log.read_text(encoding="utf-8"))

    def test_plugin_packaging_rejects_an_unsupported_python_probe_with_a_friendly_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            fake_python = temp_path / "python"
            self._write_fake_python(fake_python)
            env = os.environ.copy()
            env.update({"PYTHON_BIN": str(fake_python), "FAKE_PYTHON_EXIT_CODE": "1"})
            result = self._build_plugin_zip(temp_path / "plugin", env)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Python 3.11+ is required", result.stderr)


if __name__ == "__main__":
    unittest.main()
