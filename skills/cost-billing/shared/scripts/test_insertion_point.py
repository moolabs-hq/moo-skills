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
    """Placement targets the enclosing function's SUCCESS-RETURN path, tested against
    the anchor shapes discovery ACTUALLY emits (def-line, return-line,
    call-before-guard, multi-return) — NOT a clean work statement. The old
    'innermost statement containing entry.line' rule crashed (def-line -> emit after
    the whole function -> F821) or dead-coded (return-line -> emit after the return);
    both pass py_compile, so the fixtures must encode the REAL anchors."""

    def test_clean_work_line_single_return_is_quiet(self):
        # the happy path: work statement + one return -> place before the return,
        # no REVIEW PLACEMENT noise.
        src = "def h(req):\n    x = work(req)\n    return x\n"
        p = ip.find_insertion_point(src, 2, "python")
        self.assertEqual(p.function, "h")
        self.assertEqual(p.indent, "    ")
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))   # before the return
        self.assertIsNone(p.review_reason)                      # quiet on the happy path

    def test_def_line_anchor_descends_never_post_function(self):
        # communications.py:705 shape — entry.line is the def signature. MUST place
        # INSIDE the function (no F821 from class-body `self`), and be LOUD.
        src = ("class Agent:\n"
               "    def build(self, x):\n"   # line 2 — the def (anchor)
               "        y = work(x)\n"
               "        return y\n")
        p = ip.find_insertion_point(src, 2, "python")
        self.assertEqual(p.function, "build")
        self.assertEqual(p.indent, "        ")            # 8 — function body, not 4
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))
        self.assertIsNotNone(p.review_reason)
        self.assertIn("def signature", p.review_reason)

    def test_return_line_anchor_places_before_return_not_dead_code(self):
        # cash_application.py:134 shape — entry.line is `return result`.
        src = "def handler(s):\n    result = work()\n    return result\n"
        p = ip.find_insertion_point(src, 3, "python")
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))   # NOT after the return

    def test_call_before_failure_guard_places_after_guard(self):
        # inbound_classifier.py shape — work + `if x is None: return None` guard.
        # Placing before the SUCCESS return lands AFTER the early guard -> no phantom
        # bill on the failure path.
        src = ("def handler(s):\n"
               "    result = call_llm()\n"   # line 2 — the call (anchor)
               "    if result is None:\n"
               "        return None\n"
               "    return result\n")
        p = ip.find_insertion_point(src, 2, "python")
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        guard_i = next(i for i, l in enumerate(lines) if "return None" in l)
        self.assertGreater(_emit_idx(lines), guard_i)   # AFTER the failure guard
        # the guard is a NESTED return; only ONE top-level return -> the success-path
        # placement is deterministic here, so it's correctly placed AND quiet.
        self.assertIsNone(p.review_reason)

    def test_multiple_top_level_returns_is_loud(self):
        # two TOP-LEVEL returns -> which is the success path is ambiguous -> the
        # placement is a best guess and MUST carry a REVIEW PLACEMENT marker.
        src = ("def handler(r):\n"
               "    if not r.ok:\n"
               "        return None\n"   # nested guard (doesn't count)
               "    x = work(r)\n"
               "    if x:\n"
               "        return x\n"      # NOT top-level (nested in `if x:`)
               "    return None\n")      # top-level
        src2 = ("def handler(r):\n"
                "    x = work(r)\n"
                "    return early(x)\n"   # top-level return #1
                "    return x\n")         # top-level return #2 (unreachable, but shows the shape)
        p = ip.find_insertion_point(src2, 2, "python")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("multiple top-level returns", p.review_reason)

    def test_conditional_work_is_loud(self):
        # the anchored work is inside an `if` but the success-return rule hoists the
        # emit to function level -> it would fire even when the branch didn't run
        # (over-bill). Single top-level return + not a def-line, so ONLY the
        # conditional-work trigger can make this loud.
        src = ("def handler(req):\n"
               "    if req.needs_email:\n"
               "        email = compose_email(req)\n"   # line 3 — conditional work
               "    return req.id\n")
        p = ip.find_insertion_point(src, 3, "python")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("conditional", p.review_reason)

    def test_terminal_try_all_return_recurses_into_success_path_quiet(self):
        # llm_helpers shape: function ends in a try whose body returns on success and
        # whose handlers all return -> placing AFTER the try is DEAD. Recurse into the
        # try body, before its success return. Work anchor (not def line) so a def-sig
        # marker can't mask a miss.
        src = ("def call_llm_json(prompt):\n"
               "    payload = build(prompt)\n"   # line 2 — work anchor
               "    try:\n"
               "        result = call(payload)\n"
               "        return result\n"          # success return INSIDE try
               "    except TimeoutError:\n"
               "        return None\n"
               "    except ValueError:\n"
               "        return None\n")
        p = ip.find_insertion_point(src, 2, "python")
        lines = ip.apply_insert(src, p.after_line, "EMIT()", p.indent).splitlines()
        ei = _emit_idx(lines)
        # emit lands INSIDE the try, before `return result` — not after the try (dead)
        self.assertTrue(lines[ei].startswith("        EMIT()"))   # try-body indent (8)
        self.assertLess(ei, next(i for i, l in enumerate(lines) if "return result" in l))
        self.assertIsNone(p.review_reason)                        # clean shape -> quiet

    def test_terminal_try_fall_through_places_after_quiet(self):
        # the try is last but FALLS THROUGH (no success return) -> after-try is
        # REACHABLE -> place after it, do NOT recurse, quiet.
        src = ("def f():\n"
               "    work()\n"            # line 2 — work anchor
               "    try:\n"
               "        log()\n"
               "    except Exception:\n"
               "        pass\n")
        p = ip.find_insertion_point(src, 2, "python")
        self.assertIsNone(p.review_reason)
        self.assertEqual(p.after_line, 6)      # after the whole try (reachable)

    def test_terminal_if_all_return_is_loud(self):
        # if/else where BOTH branches return -> after-if unreachable, success branch
        # ambiguous -> MARK, do not silently pick one.
        src = ("def f(c):\n"
               "    x = work()\n"        # line 2 — work anchor
               "    if c:\n"
               "        return a(x)\n"
               "    else:\n"
               "        return b(x)\n")
        p = ip.find_insertion_point(src, 2, "python")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("UNREACHABLE", p.review_reason)

    def test_try_else_is_loud(self):
        # success path is the ELSE (runs on no-exception); after-try unreachable and
        # NOT the clean body-success shape -> MARK.
        src = ("def f():\n"
               "    x = work()\n"        # line 2 — work anchor
               "    try:\n"
               "        risky()\n"
               "    except Exception:\n"
               "        return None\n"
               "    else:\n"
               "        return x\n")
        p = ip.find_insertion_point(src, 2, "python")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("UNREACHABLE", p.review_reason)

    def test_multiline_work_after_full_span(self):
        src = "def h():\n    x = work(\n        a,\n    )\n    return x\n"
        p = ip.find_insertion_point(src, 3, "python")
        self.assertEqual(p.after_line, 4)              # after the WHOLE call

    def test_placement_marker_helper(self):
        marked = ip.with_placement_marker("EMIT()", "multiple returns", "# ")
        self.assertTrue(marked.startswith("# REVIEW PLACEMENT: multiple returns\n"))
        self.assertEqual(ip.with_placement_marker("EMIT()", None, "# "), "EMIT()")

    def test_target_function_overrides_a_wrong_line_anchor(self):
        # D1: discovery's entry.line points at the WRONG function (a prompt builder),
        # but discovery NAMES the right billable function (derivation_note). When the
        # engine is given that name, it places in the NAMED function, not the line's,
        # and flags the disagreement loudly.
        src = ("def _build_prompt(x):\n"          # line 1 — where entry.line wrongly points
               "    return f'p {x}'\n"
               "\n"
               "def generate_dunning_email_async(c):\n"  # line 4 — the REAL billable fn
               "    email = compose(c)\n"
               "    return email\n")
        p = ip.find_insertion_point(src, 2, "python", target_function="generate_dunning_email_async")
        self.assertEqual(p.function, "generate_dunning_email_async")   # NOT _build_prompt
        self.assertIsNotNone(p.review_reason)
        self.assertIn("generate_dunning_email_async", p.review_reason)

    def test_target_function_not_found_falls_back_and_flags(self):
        src = "def handler(x):\n    y = work(x)\n    return y\n"
        p = ip.find_insertion_point(src, 2, "python", target_function="does_not_exist")
        self.assertEqual(p.function, "handler")           # fell back to the line function
        self.assertIsNotNone(p.review_reason)
        self.assertIn("not found", p.review_reason)

    def test_target_function_matching_line_is_quiet(self):
        src = "def handler(x):\n    y = work(x)\n    return y\n"
        p = ip.find_insertion_point(src, 2, "python", target_function="handler")
        self.assertEqual(p.function, "handler")
        self.assertIsNone(p.review_reason)               # line + named agree -> quiet

    def test_validate_target_function(self):
        src = "def a():\n    return 1\n\ndef b():\n    return 2\n"
        self.assertIsNone(ip.validate_target_function(src, "b", "python"))   # exists -> ok
        # NEGATIVE: a name that is NOT a function in the file is flagged, not accepted
        reason = ip.validate_target_function(src, "nonexistent", "python")
        self.assertIsNotNone(reason)
        self.assertIn("nonexistent", reason)
        self.assertIsNone(ip.validate_target_function(src, "", "python"))    # empty -> no-op

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
        self.assertIsNone(p.review_reason)             # clean work line -> quiet
        lines = ip.apply_insert(src, p.after_line, "EMIT();", p.indent).splitlines()
        self.assertLess(_emit_idx(lines), _return_idx(lines))

    def test_def_line_anchor_descends_and_is_loud(self):
        # method signature anchor -> place INSIDE the method (never post-function),
        # and be LOUD.
        src = ("class A {\n"
               "  build(x) {\n"          # line 2 — the signature (anchor)
               "    const y = work(x);\n"
               "    return y;\n"
               "  }\n}\n")
        p = ip.find_insertion_point(src, 2, "typescript")
        self.assertEqual(p.function, "build")
        self.assertEqual(p.indent, "    ")             # inside the method body
        self.assertIsNotNone(p.review_reason)
        self.assertIn("function signature", p.review_reason)

    def test_conditional_work_is_loud(self):
        src = ("function h(req) {\n"
               "  if (req.x) {\n"
               "    const e = work(req);\n"   # line 3 — conditional work
               "  }\n"
               "  return req.id;\n}\n")
        p = ip.find_insertion_point(src, 3, "typescript")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("conditional", p.review_reason)

    def test_terminal_try_catch_is_loud(self):
        # ts analog of the python dead-after-try case: function ends in try/catch.
        # Full reachability is python-only; ts marks the terminal-try shape.
        src = ("async function h(req) {\n"
               "  const p = build(req);\n"   # line 2 — work anchor
               "  try {\n"
               "    return await call(p);\n"
               "  } catch (e) {\n"
               "    return null;\n"
               "  }\n}\n")
        p = ip.find_insertion_point(src, 2, "typescript")
        self.assertIsNotNone(p.review_reason)
        self.assertIn("UNREACHABLE", p.review_reason)


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
