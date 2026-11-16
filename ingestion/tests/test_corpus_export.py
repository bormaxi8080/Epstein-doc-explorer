import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_export import export_corpus
from explorer_ingest.corpus_graph import initialize_tags, review_alias, review_tag
from explorer_ingest.corpus_ocr import select_page
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_storage import CorpusLimits
from explorer_ingest.corpus_text import assemble_text
from explorer_ingest.export import SCHEMA
from explorer_ingest.state import digest_file


class ExportTests(unittest.TestCase):
    def test_compatible_text_baseline_and_catalogue_api(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "corpus").mkdir()
            c = Corpus(root / "corpus", create=True, limits=CorpusLimits(reserve_bytes=0))
            self.addCleanup(c.close)
            doc = document_id("c" * 64)
            c.db.execute(
                "INSERT INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,1)", (doc, "c" * 64)
            )
            c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
            text = "Synthetic Unicode text 😀 with zero relationships"
            select_page(c, doc, 1, text, "native", {})
            assemble_text(c, doc)
            clusters = root / "clusters.json"
            clusters.write_text(json.dumps([dict(id=0, name="Base", tags=["tag"], exemplars=[])]))
            initialize_tags(c.db, clusters)
            review_tag(c.db, "reviewed-new-tag", 0, "Synthetic reviewed assignment")
            review_alias(
                c.db, "Synthetic spelling", "Synthetic canonical", "Synthetic reviewed alias"
            )
            baseline = root / "base.db"
            db = sqlite3.connect(baseline)
            db.executescript(SCHEMA)
            db.execute(
                "INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,category,content_tags,analysis_timestamp) VALUES ('base','base.txt','base','base','other','[]','now')"
            )
            db.executemany(
                "INSERT INTO rdf_triples(doc_id,actor,action,target,sequence_order,triple_tags,timestamp) VALUES ('base','A','states','B',?,'[]','1960-01-01')",
                ((i,) for i in range(100001)),
            )
            db.execute(
                "INSERT INTO rdf_triples(doc_id,actor,action,target,sequence_order,triple_tags,timestamp) VALUES ('base','RareActor','states','RareTarget',100002,'[]','2026-01-01')"
            )
            db.commit()
            db.close()
            sha = digest_file(baseline)
            empty = export_corpus(c, root / "approved", clusters)
            self.assertEqual(empty["new_documents"], 0)
            result = export_corpus(c, root / "private", clusters, baseline, include_unreviewed=True)
            self.assertEqual(result["new_documents"], 1)
            exported_clusters = json.loads((root / "private/tag_clusters.json").read_text())
            self.assertIn("reviewed-new-tag", exported_clusters[0]["tags"])
            self.assertEqual(digest_file(baseline), sha)
            db = sqlite3.connect(root / "private/document_analysis.db")
            full, path = db.execute(
                "SELECT full_text,file_path FROM documents WHERE doc_id=?", (doc,)
            ).fetchone()
            self.assertEqual(full, (root / "private" / path).read_text())
            self.assertEqual(full, "[Page 1]\n" + text)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)
            db.close()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            checkout = Path(__file__).resolve().parents[2]
            with (root / "api.log").open("w") as log:
                process = subprocess.Popen(
                    [str(checkout / "node_modules/.bin/tsx"), "api_server.ts"],
                    cwd=checkout,
                    env={
                        **os.environ,
                        "PORT": str(port),
                        "DB_PATH": str(root / "private/document_analysis.db"),
                        "TAG_CLUSTERS_PATH": str(root / "private/tag_clusters.json"),
                    },
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:

                    def read(url):
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}" + url, timeout=10
                        ) as r:
                            return json.load(r)

                    for _ in range(100):
                        try:
                            read("/health")
                            break
                        except OSError:
                            if process.poll() is not None:
                                self.fail("API process exited")
                            time.sleep(0.05)
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/health",
                        headers={"Origin": f"http://127.0.0.1:{port}"},
                    )
                    with urllib.request.urlopen(request, timeout=10) as response:
                        self.assertEqual(
                            response.headers["Access-Control-Allow-Origin"],
                            f"http://127.0.0.1:{port}",
                        )
                    catalogue = read("/api/documents?q=Unicode")
                    self.assertEqual(catalogue["documents"][0]["doc_id"], doc)
                    self.assertEqual(
                        read("/api/document/" + doc + "/pages")["pages"][0]["text"], text
                    )
                    rare = read(
                        "/api/relationships?keywords=rare&yearMin=2026&yearMax=2026&includeUndated=false&limit=1"
                    )
                    self.assertEqual(rare["relationships"][0]["actor"], "RareActor")
                    self.assertEqual(rare["totalBeforeLimit"], 1)
                    self.assertEqual(
                        read("/api/relationships?yearMin=1900&yearMax=1960&limit=1")[
                            "totalBeforeLimit"
                        ],
                        100001,
                    )
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
