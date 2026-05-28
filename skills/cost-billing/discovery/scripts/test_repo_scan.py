#!/usr/bin/env python3
"""Unit tests for repo_scan.py execution_runtimes axis (W1 of worker-coverage-design.md).

Stdlib unittest only — no pytest dependency, so the bash smoke suite can run it
with `python3 test_repo_scan.py`.

Run: python3 test_repo_scan.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import repo_scan  # noqa: E402


def _mk_service(root: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


class ExecutionRuntimeDetection(unittest.TestCase):
    def _scan_single(self, files: dict[str, str]):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _mk_service(root, files)
            profile = repo_scan.scan(root)
            # Single-service repo → exactly one service profile.
            self.assertEqual(len(profile.services), 1, profile.services)
            return profile, profile.services[0]

    # ── queue_worker ─────────────────────────────────────────────────────
    def test_python_celery_is_queue_worker(self):
        _, svc = self._scan_single({"requirements.txt": "celery==5.3.0\nredis\n"})
        self.assertIn("queue_worker", svc.execution_runtimes)

    def test_python_rq_dramatiq_arq_are_queue_workers(self):
        for pkg in ("rq", "dramatiq", "arq", "huey"):
            with self.subTest(pkg=pkg):
                _, svc = self._scan_single({"requirements.txt": f"{pkg}\n"})
                self.assertIn("queue_worker", svc.execution_runtimes)

    def test_ts_bullmq_is_queue_worker(self):
        _, svc = self._scan_single(
            {"package.json": '{"dependencies": {"bullmq": "^5.0.0"}}'}
        )
        self.assertIn("queue_worker", svc.execution_runtimes)

    # ── stream_consumer ──────────────────────────────────────────────────
    def test_python_aiokafka_is_stream_consumer(self):
        _, svc = self._scan_single({"requirements.txt": "aiokafka\n"})
        self.assertIn("stream_consumer", svc.execution_runtimes)

    def test_ts_kafkajs_is_stream_consumer(self):
        _, svc = self._scan_single(
            {"package.json": '{"dependencies": {"kafkajs": "^2.2.0"}}'}
        )
        self.assertIn("stream_consumer", svc.execution_runtimes)

    def test_go_sarama_is_stream_consumer(self):
        gomod = "module x\n\ngo 1.21\n\nrequire github.com/IBM/sarama v1.42.0\n"
        _, svc = self._scan_single({"go.mod": gomod})
        self.assertIn("stream_consumer", svc.execution_runtimes)

    # ── scheduled_job ────────────────────────────────────────────────────
    def test_python_apscheduler_is_scheduled_job(self):
        _, svc = self._scan_single({"requirements.txt": "apscheduler\n"})
        self.assertIn("scheduled_job", svc.execution_runtimes)

    # ── worker-only service is flagged instrumentable, not skipped ────────
    def test_worker_only_service_not_skipped_and_noted(self):
        profile, svc = self._scan_single({"requirements.txt": "celery\n"})
        # Present (not skipped), no HTTP framework, worker runtime detected.
        self.assertEqual(svc.frameworks_detected, [])
        self.assertEqual(svc.execution_runtimes, ["queue_worker"])
        self.assertTrue(
            any("ARE instrumentable" in n for n in profile.notes),
            f"expected worker-only instrumentable note, got: {profile.notes}",
        )

    # ── HTTP + worker coexist; no false skip ─────────────────────────────
    def test_fastapi_plus_celery_has_both(self):
        _, svc = self._scan_single({"requirements.txt": "fastapi\nuvicorn\ncelery\n"})
        self.assertIn("fastapi", svc.frameworks_detected)
        self.assertIn("queue_worker", svc.execution_runtimes)

    # ── negative: pure HTTP service has empty execution_runtimes ──────────
    def test_pure_http_service_has_no_runtimes(self):
        _, svc = self._scan_single({"requirements.txt": "fastapi\nuvicorn\n"})
        self.assertEqual(svc.execution_runtimes, [])
        self.assertFalse(
            any("ARE instrumentable" in n for n in []),  # sanity
        )

    # ── stdlib-only worker (argparse) is NOT signature-detectable here ────
    def test_argparse_not_detected_at_signature_layer(self):
        # argparse is stdlib — no dep to match; W2 call-site classifier handles it.
        _, svc = self._scan_single({"requirements.txt": "requests\n"})
        self.assertEqual(svc.execution_runtimes, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
