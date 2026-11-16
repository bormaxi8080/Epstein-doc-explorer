"""Actual isolated PDF parsing/rendering; no OCR or provider calls."""

import tempfile
import unittest
from pathlib import Path

import test_archive as fixtures
from explorer_ingest.archive_limits import ValidationBudgetError
from explorer_ingest.corpus_extract import native_quality, parse
from explorer_ingest.corpus_storage import CorpusLimits, CorpusStorage


class ExtractTests(unittest.TestCase):
    def test_stamp_and_broken_layers_are_not_accepted_as_body_text(self):
        self.assertFalse(native_quality("EFTA00000001")["accepted"])
        self.assertFalse(native_quality("broken " + "\ufffd" * 100)["accepted"])
        self.assertTrue(
            native_quality(
                "A sufficiently long synthetic sentence with ordinary readable source text."
            )["accepted"]
        )

    def test_actual_disposable_parser_and_pixel_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "state"
            root.mkdir()
            s = CorpusStorage(root, create=True, limits=CorpusLimits(reserve_bytes=0))
            pdf = Path(d) / "synthetic.pdf"
            pdf.write_bytes(fixtures.PDF)
            self.assertEqual(parse(s, pdf, "inspect")["pages"], 1)
            with s.temporary(".json") as output:
                parse(s, pdf, "extract", output)
                self.assertIn('"text"', output.read_text())
            with s.temporary(".jpg") as output:
                result = parse(s, pdf, "render", output, dpi=72)
                self.assertEqual((result["width"], result["height"]), (100, 100))
                self.assertTrue(output.read_bytes().startswith(b"\xff\xd8"))
            limited = CorpusStorage(root, limits=CorpusLimits(reserve_bytes=0, max_pixels=10))
            with limited.temporary(".jpg") as output:
                with self.assertRaises(ValidationBudgetError):
                    parse(limited, pdf, "render", output, dpi=72)
            self.assertEqual(list((root / "tmp").iterdir()), [])
