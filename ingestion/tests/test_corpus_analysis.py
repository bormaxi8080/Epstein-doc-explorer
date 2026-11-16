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
