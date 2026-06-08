#!/usr/bin/env python3
"""Unit tests for strategies.py — named detectors + import rules."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import strategies as st  # noqa: E402


class PydanticSubclassDetector(unittest.TestCase):
    def test_registered_in_DETECTORS(self):
        self.assertIn("pydantic_settings_subclass", st.DETECTORS)

    def test_cross_file_transitive_chain_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "app").mkdir()
            (root / "app" / "common.py").write_text(
                "from pydantic_settings import BaseSettings\n"
                "class CommonSettings(BaseSettings):\n    region: str = 'us'\n")
            cfg = root / "app" / "config.py"
            cfg.write_text(
                "from app.common import CommonSettings\n"
                "class Settings(CommonSettings):\n    x: str = 'y'\n")
            detect = st.DETECTORS["pydantic_settings_subclass"]
            self.assertTrue(detect(cfg, cfg.read_text(), [root]))

    def test_data_model_not_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "m.py"
            f.write_text("from pydantic import BaseModel\nclass M(BaseModel):\n    a: int\n")
            detect = st.DETECTORS["pydantic_settings_subclass"]
            self.assertFalse(detect(f, f.read_text(), [Path(tmp)]))


class ImportRules(unittest.TestCase):
    # config_file is SERVICE-RELATIVE (relative to the service root).
    def test_python_package_app_layout(self):
        emit_dir, import_path = st.IMPORT_RULES["python_package"](
            "app/config.py", "moolabs_settings", "moolabs")
        self.assertEqual(emit_dir, "app")
        self.assertEqual(import_path, "app.moolabs_settings")

    def test_python_package_nested_package(self):
        # Nested package must keep ALL segments — not just the immediate parent.
        emit_dir, import_path = st.IMPORT_RULES["python_package"](
            "app/core/config.py", "moolabs_settings", "x")
        self.assertEqual(emit_dir, "app/core")
        self.assertEqual(import_path, "app.core.moolabs_settings")

    def test_python_package_src_layout(self):
        emit_dir, import_path = st.IMPORT_RULES["python_package"](
            "src/myapp/config.py", "moolabs_settings", "billing")
        self.assertEqual(emit_dir, "src/myapp")
        self.assertEqual(import_path, "myapp.moolabs_settings")

    def test_python_package_flat(self):
        emit_dir, import_path = st.IMPORT_RULES["python_package"](
            "config.py", "moolabs_settings", "billing")
        self.assertEqual(emit_dir, "")
        self.assertEqual(import_path, "moolabs_settings")

    def test_go_module_sibling(self):
        emit_dir, import_path = st.IMPORT_RULES["go_module"](
            "internal/conf/config.go", "moolabs_settings", "billing")
        self.assertEqual(emit_dir, "internal/conf")

    def test_ts_alias_sibling(self):
        emit_dir, import_path = st.IMPORT_RULES["ts_alias"](
            "src/config.ts", "moolabs-settings", "billing")
        self.assertEqual(emit_dir, "src")


if __name__ == "__main__":
    unittest.main()
