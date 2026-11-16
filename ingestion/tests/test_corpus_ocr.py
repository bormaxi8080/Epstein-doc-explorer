"""Transcription quality is separate from JSON/HTTP success."""

import unittest

from explorer_ingest.corpus_ocr import quality_checks


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
