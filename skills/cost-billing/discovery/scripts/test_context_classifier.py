#!/usr/bin/env python3
"""Unit tests for context_classifier.py (W2 of worker-coverage-design.md).

Stdlib unittest only — runnable via `python3 test_context_classifier.py` and by
the bash smoke suite's Phase 8.
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_classifier as cc  # noqa: E402


def _src(code: str) -> str:
    return textwrap.dedent(code).lstrip("\n")


class DecoratorClassification(unittest.TestCase):
    def _ctx(self, code: str, fn: str = None) -> cc.ContextClassification:
        results = cc.classify_file(_src(code))
        if fn:
            results = [r for r in results if r[0] == fn]
        self.assertTrue(results, "no function classified")
        return results[0][2]

    def test_celery_task(self):
        c = self._ctx("""
            import celery
            app = celery.Celery()
            @app.task
            def charge(payload):
                openai.chat.completions.create()
        """)
        self.assertEqual(c.context, "queue_worker")
        self.assertGreaterEqual(c.confidence, 0.9)

    def test_shared_task_bare(self):
        c = self._ctx("""
            from celery import shared_task
            @shared_task
            def work(payload): ...
        """)
        self.assertEqual(c.context, "queue_worker")

    def test_dramatiq_actor(self):
        c = self._ctx("""
            import dramatiq
            @dramatiq.actor
            def render(job): ...
        """)
        self.assertEqual(c.context, "queue_worker")

    def test_huey_task_vs_periodic(self):
        c_task = self._ctx("""
            from huey import huey
            @huey.task()
            def a(): ...
        """, fn="a")
        self.assertEqual(c_task.context, "queue_worker")
        c_periodic = self._ctx("""
            from huey import huey
            @huey.periodic_task(crontab(minute='0'))
            def b(): ...
        """, fn="b")
        self.assertEqual(c_periodic.context, "scheduled_job")

    def test_fastapi_route_is_http(self):
        c = self._ctx("""
            from fastapi import FastAPI
            app = FastAPI()
            @app.post("/charge")
            async def charge(): ...
        """)
        self.assertEqual(c.context, "http_request")

    def test_flask_route_is_http(self):
        c = self._ctx("""
            from flask import Flask
            app = Flask(__name__)
            @app.route("/x", methods=["POST"])
            def x(): ...
        """)
        self.assertEqual(c.context, "http_request")

    def test_apirouter_get_is_http(self):
        c = self._ctx("""
            from fastapi import APIRouter
            router = APIRouter()
            @router.get("/items")
            async def items(): ...
        """)
        self.assertEqual(c.context, "http_request")

    def test_faust_agent_is_stream(self):
        c = self._ctx("""
            import faust
            app = faust.App("x")
            @app.agent(topic)
            async def process(stream): ...
        """)
        self.assertEqual(c.context, "stream_consumer")

    def test_click_command_is_cli(self):
        c = self._ctx("""
            import click
            @click.command()
            def main(): ...
        """, fn="main")
        self.assertEqual(c.context, "cli_batch")


class LoopAndGuardClassification(unittest.TestCase):
    def test_aiokafka_async_consumer_loop(self):
        c = cc.classify_call_site(_src("""
            from aiokafka import AIOKafkaConsumer
            async def run():
                consumer = AIOKafkaConsumer("t")
                async for msg in consumer:
                    emit_cost(msg)   # <- call site
        """), lineno=5)
        self.assertEqual(c.context, "stream_consumer")
        self.assertGreaterEqual(c.confidence, 0.7)

    def test_confluent_poll_loop(self):
        c = cc.classify_call_site(_src("""
            from confluent_kafka import Consumer
            def run():
                consumer = Consumer({})
                while True:
                    msg = consumer.poll(1.0)
                    handle(msg)      # <- call site
        """), lineno=6)
        self.assertEqual(c.context, "stream_consumer")

    def test_main_guard_argparse_is_cli(self):
        c = cc.classify_call_site(_src("""
            import argparse
            def main():
                args = argparse.ArgumentParser().parse_args()
                backfill(args)       # <- call site
            if __name__ == "__main__":
                main()
        """), lineno=4)
        self.assertEqual(c.context, "cli_batch")

    def test_module_level_call_with_main_guard(self):
        c = cc.classify_call_site(_src("""
            import click
            backfill_everything()    # <- module-level call site
            if __name__ == "__main__":
                pass
        """), lineno=2)
        self.assertEqual(c.context, "cli_batch")


class ScopeBoundaries(unittest.TestCase):
    def test_nested_consumer_loop_does_not_taint_outer(self):
        # The consumer loop lives in inner(); the cost call is in outer().
        # outer() has no decorator and no loop of its own → must be 'unknown',
        # not 'stream_consumer' (ast.walk would wrongly descend into inner).
        c = cc.classify_call_site(_src("""
            def outer():
                def inner():
                    async for msg in consumer:
                        pass
                cost_call()          # line 5, in outer, not inner
        """), lineno=5)
        self.assertEqual(c.context, "unknown")

    def test_consumer_loop_in_own_body_still_detected(self):
        c = cc.classify_call_site(_src("""
            async def run():
                async for msg in consumer:
                    cost_call()      # line 3, in run's own loop
        """), lineno=3)
        self.assertEqual(c.context, "stream_consumer")

    def test_classifier_output_is_in_documented_set(self):
        # Every result must be a documented execution_context (incl. 'unknown').
        documented = {
            "http_request", "queue_worker", "stream_consumer",
            "scheduled_job", "cli_batch", "background_task", "unknown",
        }
        samples = [
            "@app.task\ndef a(): cost()",
            "@app.post('/x')\ndef b(): cost()",
            "def c(): cost()",
        ]
        for s in samples:
            for _, _, res in cc.classify_file(_src(s)):
                self.assertIn(res.context, documented, f"undocumented: {res.context}")


class UnknownNeverHttp(unittest.TestCase):
    """The anti-regression: undecorated / unrecognized sites must NOT be assumed http."""

    def test_plain_function_is_unknown_not_http(self):
        c = cc.classify_call_site(_src("""
            def helper(payload):
                emit_cost(payload)   # <- call site, no decorator, no loop
        """), lineno=2)
        self.assertEqual(c.context, "unknown")
        self.assertEqual(c.confidence, 0.0)
        self.assertNotEqual(c.context, "http_request")

    def test_bare_module_call_is_unknown(self):
        c = cc.classify_call_site(_src("""
            do_thing()               # <- module-level, no main guard
        """), lineno=1)
        self.assertEqual(c.context, "unknown")


class CallSiteResolution(unittest.TestCase):
    def test_call_site_maps_to_enclosing_task(self):
        c = cc.classify_call_site(_src("""
            import celery
            app = celery.Celery()
            @app.task
            def charge(payload):
                x = 1
                openai.chat.completions.create()   # line 6 <- call site
        """), lineno=6)
        self.assertEqual(c.context, "queue_worker")

    def test_innermost_function_wins(self):
        # A nested helper inside an HTTP handler: the call site in the nested fn
        # resolves to the nested fn (unknown), not the outer handler.
        c = cc.classify_call_site(_src("""
            from fastapi import FastAPI
            app = FastAPI()
            @app.post("/x")
            async def handler():
                def inner():
                    emit_cost()      # line 6 <- inside inner
                inner()
        """), lineno=6)
        # inner() has no decorator → unknown (innermost wins over the http outer).
        self.assertEqual(c.context, "unknown")

    def test_call_in_http_handler_body_is_http(self):
        # Sanity counterpart: a call directly in the handler body IS http.
        c = cc.classify_call_site(_src("""
            from fastapi import FastAPI
            app = FastAPI()
            @app.post("/x")
            async def handler():
                emit_cost()          # line 5 <- directly in handler
        """), lineno=5)
        self.assertEqual(c.context, "http_request")


if __name__ == "__main__":
    unittest.main(verbosity=2)
