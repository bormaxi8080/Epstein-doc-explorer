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
