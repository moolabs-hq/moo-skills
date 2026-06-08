#!/usr/bin/env python3
"""End-to-end: the framework-capability tree derives emit paths from the
customer's REAL layout — proving it is no longer modeled on moo-arc's
`app/services/` shape.

- Real moo-arc (if present): a project-base Settings extending a shared
  package's BaseSettings is detected (transitive code node) and routed to stub,
  anchored at the customer's actual `app/` package.
- Synthetic non-`app` `src/`-layout repo: artifacts emit to `src/<pkg>/` with a
  resolvable import (`<pkg>.moolabs_settings`, `src/` stripped).
"""
from __future__ import annotations
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import env_loader_scan as els  # noqa: E402

# The real moolabs repo is a sibling of moo-skills under personal/moolabs/.
# From this file: parents[4]=moo-skills, parents[5]=personal/moolabs, so the
# repo is parents[5]/"moolabs".
_MOOLABS = Path(__file__).resolve().parents[5] / "moolabs"


class EndToEndFrameworkTree(unittest.TestCase):
    @unittest.skipUnless((_MOOLABS / "services" / "moo-arc").is_dir(),
                         "real moolabs/moo-arc not present")
    def test_real_moo_arc_subclass_to_stub_anchored_at_app(self):
        svc = _MOOLABS / "services" / "moo-arc"
        entry = els._service_entry(
            _MOOLABS,
            {"slug": "moo-arc", "root": "services/moo-arc", "language": "python"},
            svc, catalog=None)
        ac = entry["app_config"]
        self.assertEqual(ac["node_id"], "python-pydantic-settings-subclass")
        self.assertTrue(ac["file"].endswith("app/config.py"),
                        f"detected {ac['file']}")
        self.assertTrue(
            ac["emit_path"].endswith("services/moo-arc/app/moolabs_settings.py"),
            f"emit_path {ac['emit_path']}")
        self.assertEqual(ac["import_path"], "app.moolabs_settings")

    def test_synthetic_src_layout_emits_to_src_pkg(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            svc = repo / "services" / "svc"
            (svc / "src" / "myapp").mkdir(parents=True)
            # Shared base in a repo-level package (resolved cross-file, outside
            # the scanned service tree).
            (repo / "shared").mkdir()
            (repo / "shared" / "base.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class AppBase(BaseSettings):\n    region: str = 'us'\n")
            (svc / "src" / "myapp" / "config.py").write_text(
                "from shared.base import AppBase\n"
                "class Settings(AppBase):\n    log_format: str = 'json'\n")
            entry = els._service_entry(
                repo, {"slug": "svc", "root": "services/svc", "language": "python"},
                svc, catalog=None)
            ac = entry["app_config"]
            self.assertEqual(ac["node_id"], "python-pydantic-settings-subclass")
            # Artifact emits to the REAL package, not a hardcoded app/services/.
            self.assertTrue(
                ac["emit_path"].endswith("services/svc/src/myapp/moolabs_settings.py"),
                f"emit_path {ac['emit_path']}")
            # Import strips the src/ package-root marker.
            self.assertEqual(ac["import_path"], "myapp.moolabs_settings")
            self.assertNotIn("app/services", ac["emit_path"])

    def test_repo_wide_shared_package_emit_matches_import(self):
        """F1: repo-wide granularity (shared config package). The stub must land
        BESIDE the shared config (where its import resolves), not under each
        service tree — emit_path and import_path must describe ONE tree."""
        import env_loader_scan as e
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Shared config package at packages/common/src/python_common/.
            cfg_dir = repo / "packages" / "common" / "src" / "python_common"
            cfg_dir.mkdir(parents=True)
            (cfg_dir / "config.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class Settings(BaseSettings):\n    api: str\n")
            inv = e.build_inventory(
                repo,
                [{"slug": "svcA", "root": "services/svcA", "language": "python"},
                 {"slug": "svcB", "root": "services/svcB", "language": "python"}],
                granularity="repo-wide", granularity_source="declared",
                shared_config_path="packages/common")
            for svc in inv["services"]:
                ac = svc["app_config"]
                # emit lands beside the SHARED config, NOT under services/<svc>/
                self.assertEqual(
                    ac["emit_path"],
                    "packages/common/src/python_common/moolabs_settings.py",
                    f"repo-wide emit_path wrong: {ac['emit_path']}")
                # import resolves against packages/common/src (src stripped)
                self.assertEqual(ac["import_path"], "python_common.moolabs_settings")
                self.assertNotIn("services/svc", ac["emit_path"])


if __name__ == "__main__":
    unittest.main()
