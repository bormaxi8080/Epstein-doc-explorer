"""Small deterministic scale/corpus fixtures; large benchmarks are explicit."""

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from explorer_ingest.archive_catalog import Archive, plan
from explorer_ingest.extraction import read_pages
from explorer_ingest.synthetic_archive import generate_inventory
from explorer_ingest.synthetic_corpus import generate_corpus


class SyntheticTests(unittest.TestCase):
    def test_streamed_inventory_has_all_registries_and_no_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.json"
            generated = generate_inventory(source, 101, page_size=10)
            self.assertEqual(generated["pages"], 11)
            destination = root / "archive"
            destination.mkdir()
            archive = Archive(destination, create=True)
            try:
                with patch(
                    "urllib.request.OpenerDirector.open", side_effect=AssertionError("No network")
                ):
                    result = plan(archive, source)
                self.assertEqual(result["total"], 101)
                self.assertEqual(
                    archive.db.execute("SELECT COUNT(*) FROM placements").fetchone()[0], 101
                )
            finally:
                archive.close()

    def test_generated_corpus_covers_extraction_and_scan_routes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "corpus"
            corpus = generate_corpus(root)
            self.assertEqual(len(corpus["files"]), 10)
            for name in ("text.pdf", "rotated.pdf", "table.pdf"):
                pages, status = read_pages(root / name, name)
                self.assertEqual(status, "extracted")
                self.assertIn("Synthetic", pages[0]["text"])
            self.assertEqual(read_pages(root / "scan.pdf", "scan.pdf")[1], "needs_ocr")
            with self.assertRaisesRegex(ValueError, "Encrypted"):
                read_pages(root / "encrypted.pdf", "encrypted.pdf")
            data = (root / "multipage.tiff").read_bytes()
            offset = struct.unpack_from("<I", data, 4)[0]
            count = 0
            while offset:
                entries = struct.unpack_from("<H", data, offset)[0]
                offset = struct.unpack_from("<I", data, offset + 2 + entries * 12)[0]
                count += 1
            self.assertEqual(count, 2)
