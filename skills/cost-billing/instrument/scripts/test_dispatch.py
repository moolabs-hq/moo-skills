#!/usr/bin/env python3
"""Unit tests for dispatch.py — the thin per-node script dispatcher."""
from __future__ import annotations
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch as dp  # noqa: E402


class DispatchRunsOnlyDeclaredScripts(unittest.TestCase):
    def test_runs_node_scripts_in_order(self):
        calls = []
        handlers = {
            "config_wire": lambda ctx: calls.append("config_wire"),
            "render_artifacts": lambda ctx: calls.append("render_artifacts"),
            "never": lambda ctx: calls.append("never"),
        }
        node = {"scripts": ["config_wire", "render_artifacts"]}
        executed = dp.dispatch_node(node, ctx={}, handlers=handlers)
        self.assertEqual(calls, ["config_wire", "render_artifacts"])
        self.assertEqual(executed, ["config_wire", "render_artifacts"])
        self.assertNotIn("never", calls)

    def test_unknown_script_raises(self):
        with self.assertRaises(dp.DispatchError):
            dp.dispatch_node({"scripts": ["ghost"]}, ctx={}, handlers={})

    def test_empty_scripts_is_noop(self):
        self.assertEqual(dp.dispatch_node({"scripts": []}, ctx={}, handlers={}), [])
        self.assertEqual(dp.dispatch_node({}, ctx={}, handlers={}), [])

    def test_ctx_passed_to_handlers(self):
        seen = {}
        dp.dispatch_node(
            {"scripts": ["a"]}, ctx={"k": "v"},
            handlers={"a": lambda ctx: seen.update(ctx)})
        self.assertEqual(seen, {"k": "v"})


if __name__ == "__main__":
    unittest.main()
