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


if __name__ == "__main__":
    unittest.main()
