#!/usr/bin/env python3
"""Unit tests for framework_registry.py."""
from __future__ import annotations
import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import framework_registry as fr  # noqa: E402

_VALID_NODE = """\
id: python-x
language: python
framework: x
detection:
  kind: regex
  import_signals: ["from x import y"]
  structural_signals: []
  priority: 50
wiring:
  mode: stub
  accessor: ""
emit:
  artifact_basename: moolabs_settings
  anchor: detected_config_dir
  import_rule: python_package
  fallback_dir: "app/services"
scripts: ["config_wire", "render_artifacts"]
"""


class LoadRegistry(unittest.TestCase):
    def _tree(self, tmp, rel, body):
        p = Path(tmp) / "frameworks" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
        return Path(tmp) / "frameworks"

    def test_loads_node_into_language_framework_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, "python/x.yaml", _VALID_NODE)
            reg = fr.load_registry(root)
            self.assertIn("python", reg)
            self.assertIn("x", reg["python"])
            self.assertEqual(reg["python"]["x"].id, "python-x")
            self.assertEqual(reg["python"]["x"].detection["kind"], "regex")

    def test_missing_required_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, "python/bad.yaml",
                              _VALID_NODE.replace("language: python\n", ""))
            with self.assertRaises(fr.RegistryError):
                fr.load_registry(root)

    def test_bad_enum_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._tree(tmp, "python/bad.yaml",
                              _VALID_NODE.replace("kind: regex", "kind: wat"))
            with self.assertRaises(fr.RegistryError):
                fr.load_registry(root)

    def test_empty_dir_returns_empty_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "frameworks").mkdir()
            self.assertEqual(fr.load_registry(Path(tmp) / "frameworks"), {})


if __name__ == "__main__":
    unittest.main()
