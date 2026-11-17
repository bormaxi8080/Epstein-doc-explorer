import json
import tempfile
import unittest

from explorer_ingest.corpus_analysis import (
    analyze_fragment,
    approve,
    assemble_analysis,
    fragments,
    plan_analysis,
)
from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_fcc import FCCFailure
from explorer_ingest.corpus_ocr import select_page
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_storage import CorpusLimits
from explorer_ingest.corpus_text import assemble_text


class Client:
    calls = 0

    def identity(self):
        return {"model": "synthetic"}

    def generate(self, prompt):
        self.calls += 1
        return {
            "output": dict(
                one_sentence_summary="Synthetic summary",
                paragraph_summary="Full document summary",
                category="other",
                content_tags=[],
                date_range_earliest=None,
                date_range_latest=None,
                rdf_triples=[],
            ),
            "reported_model": "synthetic",
            "usage": {},
        }


class AnalysisTests(unittest.TestCase):
    def test_bounded_unicode_resume_and_synthesis(self):
        chunks = list(fragments("😀" * 2000, 1000))
        self.assertTrue(all(len(t.encode()) <= 1000 for _, t in chunks))
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("a" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "a" * 64)
            )
            c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
            select_page(c, doc, 1, "Synthetic source text " * 100, "native", {})
            assemble_text(c, doc)
            client = Client()
            plan_analysis(c, doc, client.identity(), 1000)
            with self.assertRaisesRegex(ValueError, "incomplete"):
                assemble_analysis(c, doc, client)
            while job := c.queue.claim("test", "analyze"):
                result = analyze_fragment(c, json.loads(job["payload"]), client)
                c.queue.finish(job["id"], job["token"], result)
            ref = assemble_analysis(c, doc, client)
            calls = client.calls
            self.assertEqual(assemble_analysis(c, doc, client), ref)
            self.assertEqual(client.calls, calls)
            self.assertEqual(approve(c, doc), ref["sha256"])
            manifest = c.storage.get(ref)
            self.assertIn("summary_ref", manifest)
            plan_analysis(c, doc, {"model": "changed"}, 500)
            self.assertIsNone(c.document(doc)["approved_key"])
            with self.assertRaisesRegex(ValueError, "provider/text"):
                assemble_analysis(c, doc, client)
            active = c.db.execute(
                "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc,)
            ).fetchone()[0]
            root = c.db.execute(
                "SELECT job_id FROM corpus_plan_roots WHERE plan_id=? LIMIT 1", (active,)
            ).fetchone()[0]
            payload = json.loads(
                c.db.execute("SELECT payload FROM jobs WHERE id=?", (root,)).fetchone()[0]
            )
            self.assertEqual(payload["provider"], {"model": "changed"})
            select_page(c, doc, 1, "Changed", "native", {})
            self.assertIsNone(c.document(doc)["approved_key"])

    def test_adaptive_split(self):
        class Truncated(Client):
            def generate(self, prompt):
                raise FCCFailure("FCC response is truncated or incomplete")

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("b" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "b" * 64, json.dumps({"sha256": "x"})),
            )
            payload = dict(
                doc_id=doc,
                text_key="x",
                page=1,
                start=0,
                text="x" * 1000,
                provider=Truncated().identity(),
            )
            self.assertEqual(len(analyze_fragment(c, payload, Truncated())["split"]), 2)

    def test_private_review_analysis_cannot_be_publicly_approved(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("f" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "f" * 64)
            )
            c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
            select_page(c, doc, 1, "Synthetic stamp", "fcc_ocr", {}, issues=["review_photo"])
            assemble_text(c, doc, allow_review=True)
            with self.assertRaises(ValueError):
                plan_analysis(c, doc, Client().identity())
            plan_analysis(c, doc, Client().identity(), allow_review=True)
            job = c.queue.claim("test", "analyze")
            result = analyze_fragment(c, json.loads(job["payload"]), Client())
            c.queue.finish(job["id"], job["token"], result)
            ref = assemble_analysis(c, doc, Client(), allow_review=True)
            self.assertEqual(c.storage.get(ref)["review_required"], 1)
            with self.assertRaisesRegex(ValueError, "reviewed"):
                approve(c, doc)
            plan_analysis(c, doc, {"model": "changed"}, allow_review=True)
            self.assertEqual(c.document(doc)["status"], "text_review")

    def test_schema_repair_is_bounded_and_retains_rejected_output(self):
        class RepairClient(Client):
            calls = 0

            def generate(self, prompt):
                result = Client.generate(self, prompt)
                if self.calls == 1:
                    result["output"]["unexpected_field"] = "synthetic"
                return result

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("3" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "3" * 64, json.dumps({"sha256": "text"})),
            )
            client = RepairClient()
            payload = dict(
                doc_id=doc,
                text_key="text",
                page=1,
                start=0,
                text="Synthetic source text",
                provider=client.identity(),
            )
            ref = analyze_fragment(c, payload, client)["reference"]
            result = c.storage.get(ref)
            self.assertEqual(client.calls, 2)
            self.assertEqual(result["validation_attempts"], 2)
            self.assertIsNotNone(result["rejected_ref"])
            self.assertEqual(
                c.storage.get(result["rejected_ref"])["reason"], "schema.additionalProperties"
            )

    def test_two_bad_evidence_answers_never_publish_an_accepted_chunk(self):
        class Invalid(Client):
            calls = 0

            def generate(self, prompt):
                value = Client.generate(self, prompt)
                value["output"]["rdf_triples"] = [
                    dict(
                        actor="Synthetic A",
                        action="stated",
                        target="Synthetic B",
                        timestamp=None,
                        location=None,
                        tags=[],
                        explicit_topic="",
                        assertion_type="document_statement",
                        evidence={"page": 1, "quote": "Invented words"},
                    )
                ]
                return value

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("4" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "4" * 64, json.dumps({"sha256": "text"})),
            )
            client = Invalid()
            payload = dict(
                doc_id=doc,
                text_key="text",
                page=1,
                start=0,
                text="Synthetic source",
                provider=client.identity(),
            )
            with self.assertRaisesRegex(ValueError, "evidence"):
                analyze_fragment(c, payload, client)
            self.assertEqual(client.calls, 2)
            self.assertFalse(list(c.storage.root.glob("artifacts/analysis-chunk/*/*.json")))

    def test_prompt_upgrade_retires_old_pending_jobs_with_history(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("6" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "6" * 64)
            )
            c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
            select_page(c, doc, 1, "Synthetic source", "native", {})
            assemble_text(c, doc)
            with patch("explorer_ingest.corpus_analysis.ANALYSIS_PROMPT_VERSION", "old-test"):
                plan_analysis(c, doc, Client().identity())
            old = c.db.execute("SELECT id FROM jobs WHERE stage='analyze'").fetchone()[0]
            plan_analysis(c, doc, Client().identity())
            self.assertEqual(
                c.db.execute("SELECT status FROM jobs WHERE id=?", (old,)).fetchone()[0],
                "superseded",
            )
            self.assertEqual(c.queue.status()["states"], {"pending": 1, "superseded": 1})

    def test_v4_feedback_identifies_exact_quote_path_and_keeps_whitespace_strict(self):
        test = self
        source = "Synthetic A\nstated Synthetic B"

        class FeedbackClient(Client):
            calls = 0

            def generate(self, prompt):
                value = Client.generate(self, prompt)
                if self.calls == 2:
                    test.assertIn("VALIDATION_FEEDBACK:", prompt)
                    test.assertIn("PREVIOUS_ANSWER:", prompt)
                    test.assertIn("quote_exact_substring", prompt)
                    test.assertIn('["rdf_triples", 0, "evidence", "quote"]', prompt)
                value["output"]["rdf_triples"] = [
                    dict(
                        actor="Synthetic A",
                        action="stated",
                        target="Synthetic B",
                        timestamp=None,
                        location=None,
                        tags=[],
                        explicit_topic="",
                        assertion_type="document_statement",
                        evidence={
                            "page": 1,
                            "quote": source if self.calls == 2 else source.replace("\n", " "),
                        },
                    )
                ]
                return value

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("8" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "8" * 64, json.dumps({"sha256": "text"})),
            )
            client = FeedbackClient()
            payload = dict(
                doc_id=doc,
                text_key="text",
                page=1,
                start=0,
                text=source,
                provider=client.identity(),
                prompt="corpus-evidence-4",
            )
            value = c.storage.get(analyze_fragment(c, payload, client)["reference"])
            self.assertEqual(value["validation_attempts"], 2)
            self.assertEqual(value["correction_prompt_version"], "corpus-evidence-4")
            self.assertEqual(len(value["correction_prompt_sha256"]), 64)
            rejected = c.storage.get(value["rejected_ref"])
            self.assertEqual(
                rejected["feedback"],
                [dict(error="quote_exact_substring", path=["rdf_triples", 0, "evidence", "quote"])],
            )

    def test_v4_previous_answer_feedback_has_a_utf8_byte_budget(self):
        test = self

        class Oversized(Client):
            calls = 0

            def generate(self, prompt):
                value = Client.generate(self, prompt)
                if self.calls == 1:
                    value["output"]["unexpected_field"] = "😀" * 20000
                else:
                    test.assertIn("previous_answer_omitted", prompt)
                    test.assertIn("schema.additionalProperties", prompt)
                    test.assertLess(len(prompt.encode("utf-8")), 20000)
                return value

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("9" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "9" * 64, json.dumps({"sha256": "text"})),
            )
            client = Oversized()
            payload = dict(
                doc_id=doc,
                text_key="text",
                page=1,
                start=0,
                text="Synthetic",
                provider=client.identity(),
                prompt="corpus-evidence-4",
            )
            self.assertIn("reference", analyze_fragment(c, payload, client))
            self.assertEqual(client.calls, 2)

    def test_explicit_prompt_upgrade_preserves_other_versions_and_approvals(self):
        from types import SimpleNamespace

        from explorer_ingest.corpus_analysis import plan_prompt_version
        from explorer_ingest.corpus_workflow import advance_document

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            docs = [document_id(ch * 64) for ch in ("a", "b")]
            client = Client()
            for doc, ch in zip(docs, ("a", "b"), strict=True):
                c.db.execute(
                    "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)",
                    (doc, ch * 64),
                )
                c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
                select_page(c, doc, 1, "Synthetic source", "native", {})
                assemble_text(c, doc)
                plan_analysis(c, doc, client.identity(), prompt_version="corpus-evidence-3")
            while job := c.queue.claim("test", "analyze"):
                value = analyze_fragment(c, json.loads(job["payload"]), client)
                c.queue.finish(job["id"], job["token"], value)
            for doc in docs:
                assemble_analysis(c, doc, client)
                approve(c, doc)
            original = dict(c.document(docs[0]))
            self.assertEqual(
                c.storage.get(json.loads(original["analysis_ref"]))["analysis_prompt_version"],
                "corpus-evidence-3",
            )
            plan_analysis(c, docs[1], client.identity(), prompt_version="corpus-evidence-4")
            config = SimpleNamespace(
                fcc=lambda: SimpleNamespace(model="synthetic"), max_fragment_bytes=12000
            )
            before = c.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            for doc in docs:
                advance_document(c, config, doc)
            self.assertEqual(c.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], before)
            self.assertEqual(c.document(docs[0])["analysis_ref"], original["analysis_ref"])
            self.assertEqual(c.document(docs[0])["approved_key"], original["approved_key"])
            self.assertIsNone(c.document(docs[1])["analysis_ref"])
            for doc, expected in zip(docs, ("corpus-evidence-3", "corpus-evidence-4"), strict=True):
                plan = c.db.execute(
                    "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc,)
                ).fetchone()[0]
                self.assertEqual(plan_prompt_version(c, plan), expected)
            config.max_fragment_bytes = 500
            advance_document(c, config, docs[0])
            self.assertIsNone(c.document(docs[0])["approved_key"])
            plan = c.db.execute(
                "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + docs[0],)
            ).fetchone()[0]
            self.assertEqual(plan_prompt_version(c, plan), "corpus-evidence-4")

    def test_legacy_v3_correction_keeps_its_original_prompt_contract(self):
        test = self

        class Legacy(Client):
            calls = 0

            def generate(self, prompt):
                value = Client.generate(self, prompt)
                test.assertNotIn("VALIDATION_FEEDBACK:", prompt)
                test.assertNotIn("contiguous DATA.text", prompt)
                if self.calls == 1:
                    value["output"]["extra"] = "synthetic"
                else:
                    test.assertIn("CORRECTION:", prompt)
                return value

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("c" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes,text_ref) VALUES (?,?,1,?)",
                (doc, "c" * 64, json.dumps({"sha256": "text"})),
            )
            client = Legacy()
            payload = dict(
                doc_id=doc,
                text_key="text",
                page=1,
                start=0,
                text="Synthetic",
                provider=client.identity(),
                prompt="corpus-evidence-3",
            )
            value = c.storage.get(analyze_fragment(c, payload, client)["reference"])
            self.assertEqual(value["correction_prompt_version"], "corpus-evidence-3")
            self.assertEqual(client.calls, 2)
