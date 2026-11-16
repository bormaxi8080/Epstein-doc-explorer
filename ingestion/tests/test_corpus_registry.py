"""Canonical physical documents retain all frozen source occurrences."""

import tempfile
import unittest
from pathlib import Path

from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_storage import CorpusLimits


class RegistryTests(unittest.TestCase):
    def test_registry_restart_and_contiguous_pages(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            c = Corpus(root, create=True, limits=CorpusLimits(reserve_bytes=0))
            doc_id = document_id("a" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                (doc_id, "a" * 64, 42),
            )
            c.inspected(doc_id, {"format": "pdf", "status": "inspected", "pages": 3})
            c.inspected(doc_id, {"format": "pdf", "status": "inspected", "pages": 3})
            self.assertEqual(c.db.execute("SELECT COUNT(*) FROM corpus_pages").fetchone()[0], 3)
            with self.assertRaises(ValueError):
                c.inspected(doc_id, {"format": "pdf", "status": "inspected", "pages": 4})
            c.close()
            c = Corpus(root, limits=CorpusLimits(reserve_bytes=0))
            self.assertEqual(c.document(doc_id)["page_count"], 3)
            c.close()

    def test_incomplete_selection_cannot_start_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            c.db.execute("INSERT INTO selections VALUES ('bad','{}','importing',10)")
            with self.assertRaisesRegex(ValueError, "completely"):
                c.plan_inspection("bad")
