import json
import tempfile
import unittest
from pathlib import Path
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
            c.db.execute(
                "INSERT INTO selections VALUES (?, ?, ?, ?)",
                ("pilot", json.dumps({"scope": "pilot"}), "complete", 1),
            )
            before = c.db.total_changes
            result = read_status(d)
            self.assertFalse(result["complete"])
            self.assertEqual(result["selections"][0]["scope"], "pilot")
            self.assertFalse(result["full_archive_complete"])
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

    def test_worker_checks_ocr_parameters_and_requires_explicit_calls(self):
        import os

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            config = RunConfig(
                d,
                "synthetic-ocr",
                "test",
                base_url=os.environ["FCC_TEST_BASE_URL"],
                limits={"reserve_bytes": 0},
            )
            doc = document_id("e" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "e" * 64)
            )
            payload = dict(doc_id=doc, page=1, dpi=200, parameters={"provider": vars(config.fcc())})
            c.queue.enqueue("ocr", "ocr", payload)
            with (
                patch("explorer_ingest.corpus_cli.free_route", return_value={"synthetic": True}),
                patch("explorer_ingest.corpus_cli.ocr_page", return_value={"ok": True}) as ocr,
            ):
                self.assertEqual(work(c, config, 1, "ocr", allow_fcc=True)["completed_jobs"], 1)
                ocr.assert_called_once()

    def test_incremental_workflow_reaches_analysis_without_per_document_commands(self):
        import os
        from contextlib import contextmanager

        from explorer_ingest.corpus_analysis import approve

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "corpus").mkdir()
            source = root / "file.txt"
            source.write_text("A sufficiently long synthetic native text without relationships.")
            c = Corpus(root / "corpus", create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("1" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "1" * 64)
            )

            @contextmanager
            def original(doc_id):
                yield source

            c.original = original
            c.queue.enqueue("inspect", "inspect", {"doc_id": doc})
            config = RunConfig(
                str(root / "corpus"),
                "synthetic-ocr",
                "test",
                base_url=os.environ["FCC_TEST_BASE_URL"],
                limits={"reserve_bytes": 0},
            )
            output = dict(
                one_sentence_summary="Synthetic",
                paragraph_summary="Synthetic complete document",
                category="other",
                content_tags=[],
                date_range_earliest=None,
                date_range_latest=None,
                rdf_triples=[],
            )
            with (
                patch("explorer_ingest.corpus_cli.free_route", return_value={"synthetic": True}),
                patch(
                    "explorer_ingest.corpus_fcc.FCCMessages.generate",
                    return_value={"output": output, "usage": {}, "reported_model": "synthetic"},
                ),
            ):
                result = work(c, config, 20, None, allow_fcc=True)
            self.assertEqual(result["completed_jobs"], 5)
            self.assertEqual(c.document(doc)["status"], "analyzed")
            self.assertFalse(result["status"]["complete"])
            approve(c, doc)
            self.assertIsNotNone(c.document(doc)["approved_key"])
            import sqlite3

            from explorer_ingest.corpus_export import export_corpus

            clusters = root / "clusters.json"
            clusters.write_text(json.dumps([dict(id=0, name="Base", tags=[], exemplars=[])]))
            export_corpus(c, root / "bundle", clusters)
            with sqlite3.connect(root / "bundle/document_analysis.db") as db:
                self.assertEqual(
                    db.execute("SELECT reported_model FROM ingestion_analysis_chunks").fetchone()[
                        0
                    ],
                    "synthetic",
                )

    def test_stale_text_job_cannot_publish_another_page_version(self):
        from explorer_ingest.corpus_ocr import select_page
        from explorer_ingest.corpus_workflow import advance_document

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            config = RunConfig(d, MODEL, "test", limits={"reserve_bytes": 0})
            doc = document_id("2" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "2" * 64)
            )
            c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
            select_page(c, doc, 1, "Original selected page", "native", {})
            advance_document(c, config, doc)
            select_page(c, doc, 1, "Changed selected page", "native", {})
            result = work(c, config, 1, "text")
            self.assertEqual(result["completed_jobs"], 0)
            self.assertIsNone(c.document(doc)["text_ref"])
            self.assertEqual(result["status"]["failed_jobs"], 1)

    def test_work_limit_counts_failed_jobs_as_attempted(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            config = RunConfig(d, MODEL, "test", limits={"reserve_bytes": 0})
            doc = document_id("5" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "5" * 64)
            )
            c.queue.enqueue("one", "analyze", {"doc_id": doc})
            c.queue.enqueue("two", "analyze", {"doc_id": doc})
            with (
                patch("explorer_ingest.corpus_cli.free_route", return_value={}),
                patch(
                    "explorer_ingest.corpus_cli.analyze_fragment",
                    side_effect=ValueError("synthetic"),
                ),
            ):
                result = work(c, config, 1, "analyze", allow_fcc=True)
            self.assertEqual(result["attempted_jobs"], 1)
            self.assertEqual(result["completed_jobs"], 0)
            self.assertEqual(c.queue.status()["states"], {"failed": 1, "pending": 1})
