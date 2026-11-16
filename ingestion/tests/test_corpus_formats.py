import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from explorer_ingest.corpus_extract import parse
from explorer_ingest.corpus_formats import expand_worker
from explorer_ingest.corpus_storage import CorpusLimits, CorpusStorage
from PIL import Image


class FormatTests(unittest.TestCase):
    def test_text_and_image_in_isolated_worker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            storage = CorpusStorage(
                root / "data", create=True, limits=CorpusLimits(reserve_bytes=0)
            )
            txt = root / "file.txt"
            txt.write_text("Synthetic 😀")
            self.assertEqual(parse(storage, txt, "inspect")["pages"], 1)
            with storage.temporary(".json") as output:
                parse(storage, txt, "extract", output)
                self.assertEqual(json.loads(output.read_text())["text"], "Synthetic 😀")
            image = root / "file.png"
            with Image.new("RGB", (32, 20), "white") as im:
                im.save(image)
            self.assertEqual(parse(storage, image, "inspect")["format"], "image")
            with storage.temporary(".jpg") as output:
                self.assertEqual(parse(storage, image, "render", output)["width"], 32)

    def test_zip_traversal_and_expansion_budgets(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "bad.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("../escape.txt", "unsafe")
            request = dict(output=str(root / "out"), expanded_bytes=100)
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                expand_worker(request, source)
            source = root / "large.zip"
            with zipfile.ZipFile(source, "w") as z:
                z.writestr("file.txt", "x" * 101)
            request["output"] = str(root / "out2")
            with self.assertRaisesRegex(ValueError, "budget"):
                expand_worker(request, source)
