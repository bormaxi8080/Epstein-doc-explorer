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

    def test_nested_container_handoff_and_exported_lineage(self):
        import sqlite3
        from contextlib import contextmanager

        from explorer_ingest.corpus_contracts import document_id
        from explorer_ingest.corpus_export import export_corpus
        from explorer_ingest.corpus_formats import expand_container
        from explorer_ingest.corpus_ocr import select_page
        from explorer_ingest.corpus_registry import Corpus
        from explorer_ingest.corpus_text import assemble_text
        from explorer_ingest.state import digest_file

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "data").mkdir()
            inner = root / "inner.zip"
            with zipfile.ZipFile(inner, "w") as z:
                z.writestr("nested/file.txt", "Synthetic member text")
            outer = root / "outer.zip"
            with zipfile.ZipFile(outer, "w") as z:
                z.write(inner, "folder/inner.zip")
            c = Corpus(root / "data", create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            sha = digest_file(outer)
            doc = document_id(sha)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                (doc, sha, outer.stat().st_size),
            )
            original = c.original

            @contextmanager
            def source(doc_id):
                if doc_id == doc:
                    yield outer
                else:
                    with original(doc_id) as path:
                        yield path

            c.original = source
            c.inspected(doc, dict(format="zip", status="container_pending", pages=0))
            expand_container(c, doc)
            child = c.db.execute(
                "SELECT doc_id FROM corpus_members WHERE parent_id=?", (doc,)
            ).fetchone()[0]
            with self.assertRaisesRegex(ValueError, "depth"):
                expand_container(c, child, max_depth=1)
            expand_container(c, child)
            leaf = c.db.execute(
                "SELECT doc_id FROM corpus_members WHERE parent_id=?", (child,)
            ).fetchone()[0]
            with c.original(leaf) as text:
                self.assertEqual(text.read_text(), "Synthetic member text")
            c.inspected(leaf, dict(format="text", status="inspected", pages=1))
            select_page(c, leaf, 1, "Synthetic member text", "native", {})
            assemble_text(c, leaf)
            clusters = root / "clusters.json"
            clusters.write_text(json.dumps([dict(id=0, name="Base", tags=[], exemplars=[])]))
            export_corpus(c, root / "bundle", clusters, include_unreviewed=True)
            db = sqlite3.connect(root / "bundle/document_analysis.db")
            self.addCleanup(db.close)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ingestion_members").fetchone()[0], 2)
