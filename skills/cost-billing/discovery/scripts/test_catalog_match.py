#!/usr/bin/env python3
"""Unit tests for catalog_match.py (W3 of worker-coverage-design.md).

Loads the REAL provider-catalog.starter.yaml so the matcher is tested against the
shipped patterns, not a fixture that could drift from it. Stdlib unittest; runs in
the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import catalog_match as cm  # noqa: E402

_CATALOG_PATH = HERE.parent / "assets" / "provider-catalog.starter.yaml"

try:
    import yaml
    _HAVE_YAML = True
except ImportError:
    _HAVE_YAML = False


def _src(code: str) -> str:
    return textwrap.dedent(code).lstrip("\n")


@unittest.skipUnless(_HAVE_YAML, "PyYAML not installed")
class CatalogMatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
        cls.ops = cm.load_operations(catalog)

    def _scan(self, code: str):
        return cm.scan_source(_src(code), self.ops, "x.py")

    # ── vendor detection ─────────────────────────────────────────────────
    def test_openai_chat_completions_detected(self):
        sites = self._scan("""
            import openai
            def h():
                client = openai.OpenAI()
                client.chat.completions.create(model="x")
        """)
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].vendor, "openai")
        self.assertEqual(sites[0].cost_dimension, "llm_tokens")

    def test_anthropic_messages_detected(self):
        sites = self._scan("""
            import anthropic
            def h():
                client.messages.create(model="x")
        """)
        self.assertTrue(any(s.vendor == "anthropic" for s in sites), sites)

    def test_replicate_run_detected(self):
        sites = self._scan("""
            import replicate
            def h():
                replicate.run("owner/model")
        """)
        self.assertTrue(any(s.vendor == "replicate" for s in sites), sites)

    # ── import gate (precision) ──────────────────────────────────────────
    def test_no_match_without_vendor_import(self):
        # Right suffix, but openai is NOT imported → must not match.
        sites = self._scan("""
            from fastapi import FastAPI
            def h():
                client.chat.completions.create(model="x")
        """)
        self.assertEqual(sites, [])

    # ── execution-context wiring (the W3 point) ──────────────────────────
    def test_cost_call_in_http_handler_is_http(self):
        sites = self._scan("""
            import openai
            from fastapi import FastAPI
            app = FastAPI()
            @app.post("/x")
            async def handler():
                openai.chat.completions.create(model="m")
        """)
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].execution_context, "http_request")
        self.assertFalse(sites[0].needs_confirmation)

    def test_cost_call_in_celery_task_is_queue_worker(self):
        sites = self._scan("""
            import openai, celery
            app = celery.Celery()
            @app.task
            def charge(payload):
                openai.chat.completions.create(model="m")
        """)
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].execution_context, "queue_worker")
        self.assertFalse(sites[0].needs_confirmation)

    def test_cost_call_in_kafka_consumer_is_stream(self):
        sites = self._scan("""
            import openai, aiokafka
            async def run():
                consumer = aiokafka.AIOKafkaConsumer("t")
                async for msg in consumer:
                    openai.chat.completions.create(model="m")
        """)
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].execution_context, "stream_consumer")

    def test_cost_call_in_plain_helper_is_unknown_and_flagged(self):
        # The carry-forward case: a cost call in an undecorated helper resolves to
        # 'unknown' and MUST be flagged for Phase 1.6 — never assumed http_request.
        sites = self._scan("""
            import openai
            def _helper(payload):
                openai.chat.completions.create(model="m")
        """)
        self.assertEqual(len(sites), 1, sites)
        self.assertEqual(sites[0].execution_context, "unknown")
        self.assertTrue(sites[0].needs_confirmation)
        self.assertIn("Phase 1.6", sites[0].confirmation_reason)

    # ── catalog loaded something real ────────────────────────────────────
    def test_catalog_has_openai_and_anthropic_ops(self):
        vendors = {op.vendor for op in self.ops}
        self.assertIn("openai", vendors)
        self.assertIn("anthropic", vendors)


if __name__ == "__main__":
    unittest.main(verbosity=2)
