"""Lease fencing and interruption tests use persistent synthetic state."""

import tempfile
import unittest
from pathlib import Path

from explorer_ingest.corpus_queue import JobQueue


class QueueTests(unittest.TestCase):
    def test_restart_cache_and_duplicate_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "jobs.sqlite3"
            q = JobQueue(p)
            q.enqueue("one", "ocr", {"page": 1})
            with self.assertRaises(ValueError):
                q.enqueue("one", "ocr", {"page": 2})
            job = q.claim("worker", now=10)
            q.finish(job["id"], job["token"], {"artifact": "durable"}, now=11)
            q.close()
            q = JobQueue(p)
            self.assertEqual(q.result("one"), {"artifact": "durable"})
            self.assertIsNone(q.claim("next", now=12))
            self.assertTrue(q.status()["complete"])
            q.close()

    def test_expired_worker_cannot_overwrite_new_worker(self):
        with tempfile.TemporaryDirectory() as d:
            q = JobQueue(Path(d) / "jobs.sqlite3")
            self.addCleanup(q.close)
            q.enqueue("one", "render", {})
            first = q.claim("lost", lease_seconds=1, now=1)
            second = q.claim("recovery", now=3)
            with self.assertRaisesRegex(ValueError, "Stale"):
                q.finish("one", first["token"], {}, now=4)
            q.heartbeat("one", second["token"], now=4)
            q.finish("one", second["token"], {"ok": True}, now=5)

    def test_retry_budget_and_delay_are_persistent(self):
        with tempfile.TemporaryDirectory() as d:
            q = JobQueue(Path(d) / "jobs.sqlite3")
            self.addCleanup(q.close)
            q.enqueue("one", "analyze", {}, max_attempts=2)
            j = q.claim("worker", now=1)
            q.fail("one", j["token"], "timeout", delay=10, now=2)
            self.assertIsNone(q.claim("worker", now=3))
            j = q.claim("worker", now=12)
            q.fail("one", j["token"], "timeout", now=13)
            self.assertIsNone(q.claim("worker", now=100))
            self.assertEqual(q.status()["states"], {"failed": 1})
            self.assertFalse(q.status()["complete"])

    def test_targeted_recovery_does_not_claim_other_work(self):
        with tempfile.TemporaryDirectory() as d:
            q = JobQueue(Path(d) / "queue.sqlite3")
            self.addCleanup(q.close)
            q.enqueue("first", "ocr", {})
            q.enqueue("second", "ocr", {})
            job = q.claim("test", job_id="second", now=1)
            self.assertEqual(job["id"], "second")
            q.finish(job["id"], job["token"], {}, now=2)
            self.assertIsNone(q.claim("test", job_id="second", now=3))
            self.assertEqual(q.claim("test", now=3)["id"], "first")
