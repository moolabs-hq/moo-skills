"""Tests for tx_guard — flags billing emits placed BEFORE the transaction commits.

A billing emit inside an open transaction phantom-bills on rollback (the action is undone
but the bill was already sent) and over-bills if the commit later fails. The retry-dedup
on entity_id does NOT cover this: a rollback produces no retry. So the emit must fire
AFTER commit. This statically flags the provable pre-commit placements (inside a tx
with-block, a tx-decorated function, or before a later commit() in the same function)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import tx_guard as tg  # noqa: E402


class ClassifyTxPosition(unittest.TestCase):
    def test_inside_with_begin_block_is_flagged(self):
        src = (
            "def process(session):\n"
            "    with session.begin():\n"
            "        record = save(session)\n"
            "        emit_usage_event_safe(entity_id=record.id)\n"   # line 4 — inside tx
            "    return record\n"
        )
        r = tg.classify_tx_position(src, 4)
        self.assertEqual(r.status, "inside_tx")
        self.assertTrue(r.flagged)

    def test_inside_django_atomic_block_is_flagged(self):
        src = (
            "def process():\n"
            "    with transaction.atomic():\n"
            "        save()\n"
            "        emit_usage_event_safe(entity_id=x)\n"           # line 4
            "    other()\n"
        )
        self.assertEqual(tg.classify_tx_position(src, 4).status, "inside_tx")

    def test_transactional_decorated_function_is_flagged(self):
        src = (
            "@transactional\n"
            "def process():\n"
            "    save()\n"
            "    emit_usage_event_safe(entity_id=x)\n"               # line 4 — whole fn is a tx
        )
        self.assertEqual(tg.classify_tx_position(src, 4).status, "inside_tx")

    def test_emit_before_a_later_commit_is_flagged(self):
        src = (
            "def process(session):\n"
            "    save(session)\n"
            "    emit_usage_event_safe(entity_id=x)\n"               # line 3 — before commit
            "    session.commit()\n"                                  # line 4
        )
        r = tg.classify_tx_position(src, 3)
        self.assertEqual(r.status, "before_commit")
        self.assertTrue(r.flagged)

    def test_emit_after_commit_is_clear(self):
        src = (
            "def process(session):\n"
            "    save(session)\n"
            "    session.commit()\n"                                  # line 3
            "    emit_usage_event_safe(entity_id=x)\n"               # line 4 — after commit
        )
        r = tg.classify_tx_position(src, 4)
        self.assertEqual(r.status, "clear")
        self.assertFalse(r.flagged)

    def test_no_transaction_is_clear(self):
        src = "def process():\n    emit_usage_event_safe(entity_id=x)\n"
        self.assertEqual(tg.classify_tx_position(src, 2).status, "clear")

    def test_non_python_is_unknown_not_flagged(self):
        r = tg.classify_tx_position("// ts code", 1, language="typescript")
        self.assertEqual(r.status, "unknown")
        self.assertFalse(r.flagged)

    def test_syntax_error_is_unknown(self):
        self.assertEqual(tg.classify_tx_position("def (:\n", 1).status, "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
