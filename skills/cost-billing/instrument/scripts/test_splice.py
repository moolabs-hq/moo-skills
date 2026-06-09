#!/usr/bin/env python3
"""Gate test for deterministic placement (verbatim-dogfood N).

The whole point: `py_compile` PASSES on dead-after-return code, so it cannot prove
placement. This test exercises the real path — discovery captures the insertion
point (context_classifier.find_insertion_point) and instrument splices at it
(splice.apply_insert) — and FAILS on the exact misplacements the line-driven
codemod produced: after a `return` (dead code), wrong indentation, or inside a
multi-line statement.
"""

from __future__ import annotations

import py_compile
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent.parent / "discovery" / "scripts"))

import splice  # noqa: E402
from context_classifier import find_insertion_point  # noqa: E402

_EMIT = "emit_usage_event_safe(\n    event_type='x.y',\n    value=1,\n)"


def _index(lines, needle):
    for i, ln in enumerate(lines):
        if needle in ln:
            return i
    return -1


class DeterministicPlacement(unittest.TestCase):
    def _splice_at_capture(self, src, work_line):
        ip = find_insertion_point(src, work_line)
        self.assertIsNotNone(ip, "capture must find the work statement")
        return splice.apply_insert(src, ip.after_line, _EMIT, ip.indent), ip

    def test_lands_after_work_before_return_not_dead_code(self):
        src = ("def handler(req):\n"
               "    email = compose_email(req)\n"   # line 2 — the work
               "    return email\n")                # line 3 — must NOT precede the emit
        out, ip = self._splice_at_capture(src, 2)
        self.assertEqual(ip.after_line, 2)          # targets the work line, NOT the return (3)
        self.assertEqual(ip.indent, 4)
        lines = out.splitlines()
        emit_i = _index(lines, "emit_usage_event_safe(")
        ret_i = _index(lines, "return email")
        self.assertGreater(emit_i, _index(lines, "compose_email"))  # after the work
        self.assertLess(emit_i, ret_i, "emit must precede the return — not dead code")
        self.assertTrue(lines[emit_i].startswith("    emit_usage_event_safe("))  # indent 4
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(out); p = fh.name
        try:
            py_compile.compile(p, doraise=True)
        finally:
            Path(p).unlink(missing_ok=True)

    def test_nested_branch_lands_inside_the_branch(self):
        src = ("def handler(req):\n"
               "    if req.ok:\n"
               "        email = compose_email(req)\n"   # line 3 — work, indent 8
               "        return email\n"
               "    return None\n")
        out, ip = self._splice_at_capture(src, 3)
        self.assertEqual(ip.indent, 8)                  # matches the branch body indent
        lines = out.splitlines()
        emit_i = _index(lines, "emit_usage_event_safe(")
        self.assertTrue(lines[emit_i].startswith("        emit_usage_event_safe("))
        self.assertLess(emit_i, _index(lines, "return email"))  # before the branch return

    def test_multiline_work_statement_inserts_after_full_span(self):
        # the line-driven bug landed mid-statement; capture uses the statement's
        # end_lineno so the emit goes after the WHOLE call, not inside it.
        src = ("def handler(req):\n"
               "    email = compose_email(\n"   # line 2 — statement starts
               "        req,\n"                  # line 3 — entry.line could point here
               "    )\n"                          # line 4 — statement ends
               "    return email\n")
        out, ip = self._splice_at_capture(src, 3)
        self.assertEqual(ip.after_line, 4)        # after the full multi-line statement
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
            fh.write(out); p = fh.name
        try:
            py_compile.compile(p, doraise=True)   # would be a SyntaxError if spliced mid-call
        finally:
            Path(p).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
