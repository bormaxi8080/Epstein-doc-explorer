"""TXT and page maps use the same Unicode serialization as publication."""

import json
import tempfile
import unittest

from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_ocr import select_page
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_storage import CorpusLimits
from explorer_ingest.corpus_text import assemble_text, read_text_artifact


class TextTests(unittest.TestCase):
    def test_unicode_map_and_idempotent_reassembly(self):
        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("a" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                (doc, "a" * 64, 1),
            )
            c.inspected(doc, {"format": "pdf", "status": "inspected", "pages": 2})
            select_page(c, doc, 1, "First page 😀", "native", {})
            with self.assertRaisesRegex(ValueError, "incomplete"):
                assemble_text(c, doc)
            select_page(c, doc, 2, "Вторая страница", "native", {})
            reference = assemble_text(c, doc)
            artifact, path = read_text_artifact(c, doc)
            text = path.read_text()
            self.assertEqual(text, "[Page 1]\nFirst page 😀\n\n[Page 2]\nВторая страница")
            mapping = [
                json.loads(line)
                for line in c.storage.path(artifact["page_map_ref"]["path"])
                .read_text()
                .splitlines()
            ]
            self.assertEqual(text[mapping[1]["start"] : mapping[1]["end"]], "Вторая страница")
            self.assertEqual(assemble_text(c, doc), reference)
            select_page(c, doc, 1, "First page 😀", "native", {})
            self.assertIsNotNone(c.document(doc)["text_ref"])
            select_page(c, doc, 1, "Changed page", "native", {})
            self.assertIsNone(c.document(doc)["text_ref"])
