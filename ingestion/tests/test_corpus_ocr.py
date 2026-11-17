"""Transcription quality is separate from JSON/HTTP success."""

import tempfile
import unittest

from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_ocr import quality_checks, select_page
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_storage import CorpusLimits


class OCRTests(unittest.TestCase):
    def result(self, text="A sufficiently long synthetic text transcribed from a document page."):
        return {
            "text": text,
            "page_kind": "document",
            "needs_review": False,
            "blocks": [{"text": text, "kind": "paragraph", "bbox": None}],
        }

    def test_accepted_transcription_and_disagreement(self):
        self.assertEqual(quality_checks(self.result()), [])
        native = {
            "text": "Completely different long source text with other facts and different wording",
            "quality": {"accepted": True},
        }
        self.assertIn("native_ocr_disagreement", quality_checks(self.result(), native))

    def test_photo_empty_and_inverted_coordinates_require_attention(self):
        value = self.result("")
        self.assertIn("empty_document_transcription", quality_checks(value))
        value["page_kind"] = "photo"
        self.assertIn("verify_page_kind_and_body_coverage", quality_checks(value))
        value = self.result()
        value["blocks"][0]["bbox"] = [100, 0, 10, 20]
        with self.assertRaisesRegex(ValueError, "order"):
            quality_checks(value)


class ManualCorrectionTests(unittest.TestCase):
    def test_correction_preserves_lineage_and_invalidates_downstream_versions(self):
        import hashlib

        from explorer_ingest.corpus_ocr import review_page

        with tempfile.TemporaryDirectory() as d:
            c = Corpus(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            try:
                doc = document_id("9" * 64)
                c.db.execute(
                    "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)",
                    (doc, "9" * 64),
                )
                c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
                original = select_page(
                    c, doc, 1, "Incorrect OCR", "fcc_ocr", {}, issues=["needs_check"]
                )
                with self.assertRaises(ValueError):
                    review_page(c, doc, 1)
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    review_page(
                        c,
                        doc,
                        1,
                        reviewer="test",
                        reason="test",
                        corrected_text="text",
                        correction_sha256="0" * 64,
                    )
                c.db.execute(
                    "UPDATE corpus_documents SET text_ref='old',analysis_ref='old',approved_key='old' WHERE doc_id=?",
                    (doc,),
                )
                text = "SYNTHETIC corrected text\nВторая строка"
                sha = hashlib.sha256(text.encode()).hexdigest()
                ref = review_page(
                    c,
                    doc,
                    1,
                    reviewer="synthetic reviewer",
                    reason="Compared to the synthetic source",
                    corrected_text=text,
                    correction_sha256=sha,
                )
                value = c.storage.get(ref)
                self.assertEqual(value["text"], text)
                self.assertEqual(value["method"], "human_corrected")
                self.assertEqual(value["provenance"]["previous_page_sha256"], original["sha256"])
                self.assertEqual(value["provenance"]["correction_sha256"], sha)
                self.assertIsNone(c.document(doc)["approved_key"])
                self.assertIsNone(c.document(doc)["text_ref"])
                self.assertIsNone(c.document(doc)["analysis_ref"])
            finally:
                c.close()
