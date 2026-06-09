#!/usr/bin/env python3
"""splice.py — deterministic insertion of a rendered emit block into customer source.

Verbatim-dogfood finding N: the Phase-2d subagent placed inserts line-driven and
guessed — landing mid-multiline-statement, AFTER a `return` (dead code), or at the
wrong indent. `py_compile` cannot catch any of these (dead-after-return compiles
fine), so placement was un-gateable. This module makes placement DETERMINISTIC:
given the discovery-captured insertion point (see
``discovery/scripts/context_classifier.find_insertion_point`` -> after_line +
indent), it splices the rendered block at exactly that point, so a fixture can
FAIL on misplacement (the gate py_compile could never be).

Stdlib only; no soft deps.
"""

from __future__ import annotations


def apply_insert(source: str, after_line: int, insert_text: str, indent: int) -> str:
    """Splice ``insert_text`` into ``source`` immediately AFTER the 1-based
    ``after_line``, re-indented to ``indent`` spaces.

    LANGUAGE-AGNOSTIC: this is pure text-line manipulation, so it works for Python,
    TypeScript, and Go alike. The only Python-bound piece is the CAPTURE
    (``find_insertion_point``); for TS/Go the caller supplies ``after_line`` +
    ``indent`` by the same rule, manually.

    - ``after_line`` is the insertion point's ``after_line`` (the end line of the
      "work" statement) — the block lands in the SAME block as the work, before the
      function's return, never as dead code after it.
    - Each non-blank line of ``insert_text`` (the template renders at column 0) is
      prefixed with ``indent`` spaces; blank lines stay blank (no trailing WS).
    - ``after_line`` is clamped to [0, len(lines)] so an out-of-range capture
      appends rather than raising.
    """
    if after_line < 0:
        after_line = 0
    lines = source.splitlines()
    after = min(after_line, len(lines))
    pad = " " * indent
    block = [pad + ln if ln.strip() else "" for ln in insert_text.splitlines()]
    out = lines[:after] + block + lines[after:]
    text = "\n".join(out)
    return text + "\n" if source.endswith("\n") else text
