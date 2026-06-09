#!/usr/bin/env python3
"""Gate for deterministic placement across the THREE supported languages (N).

`py_compile` (and a TS/Go type-check) can't catch misplacement — dead code after a
`return` compiles fine — so this runs capture -> splice and FAILS on the real bug
modes (after-return, wrong indent, mid-multiline-statement). Python uses stdlib
`ast` (always runs); TS/Go use tree-sitter and SKIP when it is absent (soft-dep
posture). A fallback test asserts the absent path — the one MOST customers hit —
degrades to None (manual placement), never crashes.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import insertion_point as ip  # noqa: E402

_HAS_TS = ip._ts_parser("typescript") is not None
_HAS_GO = ip._ts_parser("go") is not None


def _emit_idx(lines):
    return next(i for i, ln in enumerate(lines) if "EMIT" in ln)


def _return_idx(lines):
    return next(i for i, ln in enumerate(lines) if ln.strip().startswith("return"))


class Python(unittest.TestCase):
    def test_after_work_before_return(self):
        src = "def h(req):\n    x = work(req)\n    return x\n"
        p = ip.find_insertion_point(src, 2, "python")
        self.assertEqual((p.function, p.after_line, p.indent), ("h", 2, "    "))
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))      # not dead code
        self.assertEqual(lines[_emit_idx(lines)], "    EMIT()")    # indent matches

    def test_multiline_after_full_span(self):
        src = "def h():\n    x = work(\n        a,\n    )\n    return x\n"
        p = ip.find_insertion_point(src, 3, "python")  # entry.line mid-statement
        self.assertEqual(p.after_line, 4)              # after the WHOLE call

    def test_nested_branch_indent(self):
        src = "def h(r):\n    if r.ok:\n        x = work(r)\n        return x\n    return None\n"
        p = ip.find_insertion_point(src, 3, "python")
        self.assertEqual(p.indent, "        ")          # 8-space branch body

    def test_syntax_error_returns_none(self):
        self.assertIsNone(ip.find_insertion_point("def (:\n", 1, "python"))

    def test_module_level_no_function(self):
        p = ip.find_insertion_point("x = work()\n", 1, "python")
        self.assertIsNone(p.function)
        self.assertEqual(p.after_line, 1)


@unittest.skipUnless(_HAS_TS, "tree-sitter (typescript) not installed")
class TypeScript(unittest.TestCase):
    def test_multiline_after_span_before_return(self):
        src = ("async function h(req) {\n"
               "  const x = await work(\n"
               "    req,\n"
               "  );\n"
               "  return x;\n}\n")
        p = ip.find_insertion_point(src, 3, "typescript")
        self.assertEqual(p.after_line, 4)              # after the multi-line const
        self.assertEqual(p.indent, "  ")
        lines = ip.apply_insert(src, p.after_line, "EMIT();", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))


@unittest.skipUnless(_HAS_GO, "tree-sitter (go) not installed")
class Go(unittest.TestCase):
    def test_after_work_before_return_preserves_tab(self):
        src = "func H(r *R) error {\n\tx := work(r)\n\treturn x\n}\n"
        p = ip.find_insertion_point(src, 2, "go")
        self.assertEqual(p.after_line, 2)
        self.assertEqual(p.indent, "\t")               # TAB, not spaces — gofmt-correct
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))
        self.assertEqual(lines[_emit_idx(lines)], "\tEMIT()")


class TreeSitterAbsentFallback(unittest.TestCase):
    """The path MOST customers hit: tree-sitter absent (or version-skewed) -> TS/Go
    capture returns None (caller falls back to manual placement), never crashes.
    `_ts_parser` already swallows ImportError/TypeError/AttributeError/ValueError;
    forcing it to None here exercises the same degraded result."""

    def setUp(self):
        self._orig = ip._ts_parser

    def tearDown(self):
        ip._ts_parser = self._orig

    def test_ts_and_go_degrade_to_none(self):
        ip._ts_parser = lambda language: None
        self.assertIsNone(ip.find_insertion_point("function h(){\n const x=1;\n}\n", 2, "typescript"))
        self.assertIsNone(ip.find_insertion_point("func H(){\n\tx:=1\n}\n", 2, "go"))
        # python is dep-free and unaffected:
        self.assertIsNotNone(ip.find_insertion_point("def h():\n    x = 1\n", 2, "python"))


class ApplyInsertLanguageAgnostic(unittest.TestCase):
    def test_indent_string_and_blank_lines(self):
        out = ip.apply_insert("a\nb\n", 1, "X\n\nY", "  ")
        self.assertEqual(out, "a\n  X\n\n  Y\nb\n")   # blank line stays blank


if __name__ == "__main__":
    unittest.main()
