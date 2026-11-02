"""Synthetic regression fixtures: no real archive records or provider credentials."""

import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

from explorer_ingest.analysis import analyze_documents, approve_document, validate_analysis
from explorer_ingest.download import download_assets
from explorer_ingest.export import SCHEMA, export_bundle
from explorer_ingest.extraction import extract_assets
from explorer_ingest.inventory import import_inventory
from explorer_ingest.provider import FCCProvider
from explorer_ingest.state import Store, digest_file

TEXT = "Jeffrey Epstein sent email to Person B."
TEXT_BYTES = TEXT.encode()


def inventory(url="https://www.justice.gov/example.txt", complete=True):
    return {
        "schema_version": 2,
        "metadata": {"task_uid": "PW-010", "documents_downloaded": False},
        "validation": {
            key: complete
            for key in (
                "schema_valid",
                "structure_complete",
                "metadata_complete",
                "source_stable",
                "complete",
            )
        }
        | {"issues_count": 0, "unique_files_count": 1},
        "tree": {"id": "root", "kind": "directory", "name": "root", "children": []},
        "pages": [],
        "issues": [],
        "assets": {
            "source-1": {
                "url": url,
                "original_name": "example.txt",
                "size_bytes": len(TEXT.encode()),
                "occurrences": [{"page_id": "listing-a"}, {"page_id": "listing-b"}],
            }
        },
    }


def analysis():
    return {
        "one_sentence_summary": "An email.",
        "paragraph_summary": "A synthetic email.",
        "category": "email",
        "content_tags": ["correspondence"],
        "date_range_earliest": None,
        "date_range_latest": None,
        "rdf_triples": [
            {
                "actor": "Jeffrey Epstein",
                "action": "sent email to",
                "target": "Person B",
                "timestamp": None,
                "location": None,
                "tags": ["correspondence"],
                "explicit_topic": "email",
                "assertion_type": "document_statement",
                "evidence": {"page": 1, "quote": TEXT},
            }
        ],
    }


class RecordedProvider:
    def __init__(self):
        self.calls = 0
        self.revision = "test-1"

    def identity(self):
        return {"model": "synthetic", "routing_revision": self.revision}

    def generate(self, prompt):
        self.calls += 1
        return {"analysis": analysis(), "reported_model": "synthetic", "usage": {}}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")
        self.source = self.root / "inventory.json"
        self.source.write_text(json.dumps(inventory()))
        self.clusters = self.root / "clusters.json"
        self.clusters.write_text(
            json.dumps(
                [
                    {
                        "id": 0,
                        "name": "Correspondence",
                        "tags": ["correspondence"],
                        "exemplars": ["correspondence"],
                    },
                    {"id": 1, "name": "Other", "tags": [], "exemplars": []},
                ]
            )
        )

    def tearDown(self):
        self.store.close()
        self.temporary.cleanup()

    def seed(self, filename="example.txt", body=TEXT_BYTES):
        import_inventory(self.store, self.source)
        original = self.store.root / "seed"
        original.write_bytes(body)
        metadata = inventory()["assets"]["source-1"] | {"original_name": filename}
        with self.store.db:
            self.store.db.execute(
                "UPDATE assets SET status='downloaded',path='seed',sha256=?,metadata=?",
                (digest_file(original), json.dumps(metadata)),
            )
        extract_assets(self.store)
        return self.store.db.execute("SELECT doc_id FROM documents").fetchone()[0]

    def test_partial_inventory_is_explicit_and_import_idempotent(self):
        self.source.write_text(json.dumps(inventory(complete=False)))
        with self.assertRaisesRegex(ValueError, "partial"):
            import_inventory(self.store, self.source)
        result = import_inventory(self.store, self.source, allow_partial=True)
        self.assertFalse(result["complete"])
        import_inventory(self.store, self.source, allow_partial=True)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
        retained = json.loads(self.store.db.execute("SELECT metadata FROM assets").fetchone()[0])
        self.assertEqual(len(retained["occurrences"]), 2)

    def test_bad_inventory_rolls_back(self):
        value = inventory()
        value["validation"]["unique_files_count"] = 2
        self.source.write_text(json.dumps(value))
        with self.assertRaises(ValueError):
            import_inventory(self.store, self.source)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)

    def test_legacy_inventory_adapter(self):
        value = inventory()
        value["schema_version"] = 1
        self.source.write_text(json.dumps(value))
        self.assertEqual(import_inventory(self.store, self.source)["assets"], 1)

    def test_cache_reapproval_and_baseline_preservation(self):
        doc_id = self.seed()
        provider = RecordedProvider()
        self.assertEqual(analyze_documents(self.store, provider)[0]["status"], "analyzed")
        analyze_documents(self.store, provider)
        self.assertEqual(provider.calls, 1)
        empty = self.root / "unapproved"
        self.assertEqual(export_bundle(self.store, empty, self.clusters)["new_documents"], 0)
        approve_document(self.store, doc_id)
        baseline = self.root / "baseline.db"
        with closing(sqlite3.connect(baseline)) as db:
            db.executescript(SCHEMA)
            db.execute(
                "INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,category,content_tags,analysis_timestamp) VALUES ('UPSTREAM','upstream.txt','Original','Original','other','[]','2020-01-01')"
            )
            db.commit()
        baseline_hash = digest_file(baseline)
        out = self.root / "bundle"
        result = export_bundle(self.store, out, self.clusters, baseline)
        self.assertEqual(result["new_documents"], 1)
        self.assertEqual(digest_file(baseline), baseline_hash)
        with closing(sqlite3.connect(out / "document_analysis.db")) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT quote FROM ingestion_evidence").fetchone()[0], TEXT)
            self.assertEqual(
                db.execute(
                    "SELECT hop_distance_from_principal FROM canonical_entities WHERE canonical_name='Person B'"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                db.execute("SELECT top_cluster_ids FROM rdf_triples").fetchone()[0], "[0]"
            )
            self.assertEqual(
                len(
                    json.loads(db.execute("SELECT metadata FROM ingestion_sources").fetchone()[0])[
                        "occurrences"
                    ]
                ),
                2,
            )
        with self.assertRaisesRegex(ValueError, "immutable"):
            export_bundle(self.store, out, self.clusters)
        provider.revision = "test-2"
        analyze_documents(self.store, provider)
        self.assertIsNone(self.store.db.execute("SELECT approved_key FROM documents").fetchone()[0])

    def test_invalid_evidence_and_dates(self):
        chunk = {"page": 1, "text": TEXT}
        value = analysis()
        value["rdf_triples"][0]["evidence"]["quote"] = "Invented"
        with self.assertRaises(ValueError):
            validate_analysis(value, chunk)
        value = analysis()
        value["rdf_triples"][0]["timestamp"] = "2020-02-31"
        with self.assertRaises(ValueError):
            validate_analysis(value, chunk)

    def test_export_rejects_changed_pages_after_approval(self):
        doc_id = self.seed()
        analyze_documents(self.store, RecordedProvider())
        approve_document(self.store, doc_id)
        page_path = self.store.path(
            self.store.db.execute("SELECT pages_json FROM documents").fetchone()[0]
        )
        page_path.write_text(page_path.read_text().replace(TEXT, "Changed document text."))
        with self.assertRaisesRegex(ValueError, "changed after analysis"):
            export_bundle(self.store, self.root / "tampered", self.clusters)
        self.assertFalse((self.root / "tampered").exists())

    def test_zip_traversal_and_resume(self):
        archive = self.root / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("../escape.txt", TEXT)
        with self.assertRaisesRegex(ValueError, "Unsafe ZIP"):
            self.seed("fixture.zip", archive.read_bytes())
        self.assertFalse((self.store.root / "escape.txt").exists())

    def test_unclassified_tags_keep_existing_cluster_ids(self):
        doc_id = self.seed()
        self.clusters.write_text(
            json.dumps([{"id": 27, "name": "Existing", "tags": ["different"], "exemplars": []}])
        )
        analyze_documents(self.store, RecordedProvider())
        approve_document(self.store, doc_id)
        out = self.root / "new-cluster"
        export_bundle(self.store, out, self.clusters)
        clusters = json.loads((out / "tag_clusters.json").read_text())
        self.assertEqual(clusters[0]["id"], 27)
        self.assertEqual(clusters[1]["id"], 28)
        self.assertEqual(clusters[1]["tags"], ["correspondence"])
        with closing(sqlite3.connect(out / "document_analysis.db")) as db:
            self.assertEqual(
                db.execute("SELECT top_cluster_ids FROM rdf_triples").fetchone()[0], "[28]"
            )

    def test_zip_duplicates_keep_all_members(self):
        archive = self.root / "fixture.zip"
        with zipfile.ZipFile(archive, "w") as stream:
            stream.writestr("one.txt", TEXT)
            stream.writestr("two.txt", TEXT)
        self.seed("fixture.zip", archive.read_bytes())
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM document_sources").fetchone()[0], 2
        )
        self.assertEqual(extract_assets(self.store), [])

    def test_reviewed_alias_chains_and_cycles(self):
        doc_id = self.seed()
        analyze_documents(self.store, RecordedProvider())
        approve_document(self.store, doc_id)
        aliases = self.root / "aliases.json"
        mappings = [
            {
                "original_name": "Person B",
                "canonical_name": "Person C",
                "reasoning": "Synthetic review",
            },
            {
                "original_name": "Person C",
                "canonical_name": "Person D",
                "reasoning": "Synthetic review",
            },
        ]
        aliases.write_text(json.dumps(mappings))
        out = self.root / "aliases-bundle"
        export_bundle(self.store, out, self.clusters, aliases_path=aliases)
        with closing(sqlite3.connect(out / "document_analysis.db")) as db:
            self.assertEqual(
                db.execute(
                    "SELECT canonical_name FROM entity_aliases WHERE original_name='Person B'"
                ).fetchone()[0],
                "Person D",
            )
        mappings[1]["canonical_name"] = "Person B"
        aliases.write_text(json.dumps(mappings))
        with self.assertRaisesRegex(ValueError, "alias cycle"):
            export_bundle(self.store, self.root / "cyclic", self.clusters, aliases_path=aliases)
        self.assertFalse((self.root / "cyclic").exists())

    def test_scanned_pdf_needs_ocr(self):
        import io

        from pypdf import PdfWriter

        pdf = PdfWriter()
        pdf.add_blank_page(width=100, height=100)
        output = io.BytesIO()
        pdf.write(output)
        self.seed("scan.pdf", output.getvalue())
        self.assertEqual(
            self.store.db.execute("SELECT status FROM documents").fetchone()[0], "needs_ocr"
        )

    def test_unknown_people_scoped_and_lfs_pointer_rejected(self):
        doc_id = self.seed()
        provider = RecordedProvider()
        value = analysis()
        value["rdf_triples"][0]["target"] = "unknown person A"
        provider.generate = lambda prompt: {"analysis": value, "reported_model": "synthetic"}
        analyze_documents(self.store, provider)
        approve_document(self.store, doc_id)
        out = self.root / "scoped"
        export_bundle(self.store, out, self.clusters)
        with closing(sqlite3.connect(out / "document_analysis.db")) as db:
            self.assertIn(doc_id, db.execute("SELECT target FROM rdf_triples").fetchone()[0])
        pointer = self.root / "lfs.db"
        pointer.write_text("version https://git-lfs.github.com/spec/v1")
        with self.assertRaisesRegex(ValueError, "LFS"):
            export_bundle(self.store, self.root / "bad-lfs", self.clusters, pointer)

    def test_http_download_checks_size_and_host(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(TEXT.encode())

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.source.write_text(
                json.dumps(inventory(f"http://127.0.0.1:{server.server_port}/fixture.txt"))
            )
            import_inventory(self.store, self.source)
            self.assertEqual(download_assets(self.store)[0]["status"], "download_failed")
            bad_metadata = inventory()["assets"]["source-1"]
            bad_metadata["url"] = f"http://127.0.0.1:{server.server_port}/fixture.txt"
            bad_metadata["size_bytes"] += 1
            with self.store.db:
                self.store.db.execute("UPDATE assets SET metadata=?", (json.dumps(bad_metadata),))
            self.assertEqual(
                download_assets(self.store, allow_hosts=["127.0.0.1"])[0]["status"],
                "download_failed",
            )
            self.assertIsNone(self.store.db.execute("SELECT path FROM assets").fetchone()[0])
            bad_metadata["size_bytes"] -= 1
            with self.store.db:
                self.store.db.execute("UPDATE assets SET metadata=?", (json.dumps(bad_metadata),))
            self.assertEqual(
                download_assets(self.store, allow_hosts=["127.0.0.1"])[0]["status"], "downloaded"
            )
            self.assertEqual(
                digest_file(
                    self.store.path(self.store.db.execute("SELECT path FROM assets").fetchone()[0])
                ),
                self.store.db.execute("SELECT sha256 FROM assets").fetchone()[0],
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


@unittest.skipUnless(
    os.environ.get("FCC_TEST_BASE_URL"), "Start the local Postman simulation first"
)
class FCCSimulationTests(unittest.TestCase):
    setUp = PipelineTests.setUp
    tearDown = PipelineTests.tearDown
    seed = PipelineTests.seed

    def test_fcc_success_cache_export(self):
        doc_id = self.seed()
        provider = FCCProvider(
            os.environ["FCC_TEST_BASE_URL"], "synthetic", "simulation-1", timeout=2, retries=0
        )
        self.assertEqual(analyze_documents(self.store, provider)[0]["status"], "analyzed")
        approve_document(self.store, doc_id)
        self.assertEqual(
            export_bundle(self.store, self.root / "fcc-bundle", self.clusters)["new_documents"], 1
        )

    def test_fcc_failures(self):
        self.seed()
        for model in ("failure-evidence", "failure-truncated", "failure-500", "failure-timeout"):
            with self.subTest(model=model):
                provider = FCCProvider(
                    os.environ["FCC_TEST_BASE_URL"], model, model, timeout=0.05, retries=0
                )
                self.assertEqual(
                    analyze_documents(self.store, provider)[0]["status"], "analysis_failed"
                )
                self.assertEqual(
                    self.store.db.execute("SELECT COUNT(*) FROM analyses").fetchone()[0], 0
                )

    def test_fcc_rate_limit_retry(self):
        self.seed()
        provider = FCCProvider(
            os.environ["FCC_TEST_BASE_URL"], "failure-429", "rate-limit", timeout=2, retries=1
        )
        self.assertEqual(analyze_documents(self.store, provider)[0]["status"], "analyzed")

    def test_fcc_server_error_retry_is_bounded(self):
        self.seed()
        provider = FCCProvider(
            os.environ["FCC_TEST_BASE_URL"], "failure-500", "bounded-error", timeout=2, retries=1
        )
        self.assertEqual(analyze_documents(self.store, provider)[0]["status"], "analysis_failed")


if __name__ == "__main__":
    unittest.main()
