import json
import tempfile
import unittest
from unittest.mock import patch

from explorer_ingest.corpus_cli import work
from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_fcc import FCCFailure
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_runtime import CallBudget, RunConfig, free_route, read_status
from explorer_ingest.corpus_storage import CorpusLimits

MODEL = "claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free"


class RuntimeTests(unittest.TestCase):
    def test_free_admission_and_durable_caps(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            config = RunConfig(d, MODEL, "test", max_calls_total=1)
            budget = CallBudget(c, config)
            budget.before(vars(config.fcc()))
            with self.assertRaisesRegex(FCCFailure, "budget"):
                CallBudget(c, config).before(vars(config.fcc()))
            with self.assertRaisesRegex(ValueError, "Only"):
                free_route(RunConfig(d, "paid-model", "test"))
            with self.assertRaises(ValueError):
                RunConfig(d, MODEL, "test", minimum_call_interval=0)
            with patch("explorer_ingest.corpus_runtime.urllib.request.urlopen") as fetch:
                fetch.return_value.__enter__.return_value.read.return_value = json.dumps(
                    {
                        "data": [
                            {
                                "id": "inclusionai/ling-3.2-flash:free",
                                "pricing": {"prompt": "0", "completion": "0"},
                                "architecture": {"input_modalities": ["text", "image"]},
                            }
                        ]
                    }
                ).encode()
                self.assertEqual(free_route(config)["model"], "inclusionai/ling-3.2-flash:free")

    def test_readonly_status_and_offline_worker(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            config = RunConfig(d, MODEL, "test", limits={"reserve_bytes": 0})
            doc = document_id("d" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "d" * 64)
            )
            c.queue.enqueue("x", "ocr", dict(doc_id=doc))
            before = c.db.total_changes
            result = read_status(d)
            self.assertFalse(result["complete"])
            self.assertEqual(result["uninspected"], 1)
            self.assertEqual(c.db.total_changes, before)
            with patch(
                "explorer_ingest.corpus_runtime.FCCMessages.generate",
                side_effect=AssertionError("No calls"),
            ):
                self.assertEqual(work(c, config, 10, None)["completed_jobs"], 0)
            self.assertEqual(c.queue.status()["states"], {"pending": 1})

    def test_operator_retry_preserves_history_and_inputs(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            payload = dict(doc_id="doc", page=1)
            c.queue.enqueue("old", "ocr", payload, max_attempts=1)
            job = c.queue.claim("test")
            c.queue.fail("old", job["token"], "invalid", retryable=False)
            c.queue.retry_failed("old", "Provider format corrected", 1)
            c.queue.enqueue("old", "ocr", payload, max_attempts=1)
            job = c.queue.claim("test")
            self.assertEqual(job["attempts"], 2)
            c.queue.fail("old", job["token"], "invalid", retryable=True)
            self.assertEqual(c.queue.status()["states"], {"failed": 1})
            c.queue.enqueue("new", "ocr", {**payload, "revision": 2})
            c.queue.supersede("old", "new", "New prompt version")
            self.assertEqual(
                c.db.execute(
                    "SELECT COUNT(*) FROM job_events WHERE event='operator_retry'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(c.queue.status()["states"], {"pending": 1, "superseded": 1})
