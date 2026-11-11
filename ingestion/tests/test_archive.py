"""Synthetic archive tests; never contact DOJ or inspect the user's pending dump."""

import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from explorer_ingest.archive_catalog import CONTROL, Archive, StorageUnavailable, plan, report
from explorer_ingest.archive_transfer import (
    Options,
    StoragePause,
    artifact_paths,
    fetch,
    quarantine,
    validate_content,
    verify,
)
from explorer_ingest.state import atomic_json, digest_file
from ijson.common import JSONError
from jsonschema import ValidationError
from pypdf import PdfWriter


def pdf_bytes():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


PDF = pdf_bytes()


def inventory(url="https://www.justice.gov/datasets/one.pdf", version=2, certified=True):
    files = [
        {
            "id": f"file-{i}",
            "name": "Original.pdf",
            "kind": "file",
            "asset_id": "asset-1",
            "url": url,
        }
        for i in (1, 2)
    ]
    return {
        "schema_version": version,
        "metadata": {
            "root_url": "https://www.justice.gov/epstein/doj-disclosures",
            "task_uid": "PW-010",
            "task_version": "test",
            "snapshot_started_at": "2026-11-02T00:00:00Z",
            "snapshot_completed_at": "2026-11-02T01:00:00Z",
            "snapshot_id": "synthetic-snapshot",
            "documents_downloaded": False,
        },
        "tree": {
            "children": [
                {"children": files, "id": "section", "kind": "directory", "name": "Data set 1"}
            ],
            "id": "root",
            "kind": "directory",
            "name": "DOJ",
        },
        **(
            {
                "storage_tree": {
                    "id": "storage",
                    "name": "URLs",
                    "kind": "directory",
                    "children": [files[0]],
                }
            }
            if version == 1
            else {}
        ),
        "assets": {
            "asset-1": {
                "asset_id": "asset-1",
                "url": url,
                "download_url": url,
                "extension": ".pdf",
                "file_kind": "pdf",
                "original_name": "Original.pdf",
                "source_path_segments": ["datasets"],
                "status": "verified",
                "size_bytes": len(PDF),
                "size_source": "content-length",
                "final_url": url,
                "mime_type": "application/pdf",
                "occurrences": [
                    {
                        "node_id": f"file-{i}",
                        "page_id": "page-1",
                        "page_url": "https://www.justice.gov/listing",
                        "groups": ["Data set 1"],
                        "raw_url": url,
                        "label": "Original",
                        "tag": "a",
                        "attribute": "href",
                        "ordinal": i,
                    }
                    for i in (1, 2)
                ],
            }
        },
        "pages": [
            {
                "page_id": "page-1",
                "url": "https://www.justice.gov/listing",
                "state": "done",
                "checked_at": "2026-11-02T00:00:00Z",
                "structure_hash": "observed",
                "verified_hash": "observed",
                "error": None,
            }
        ],
        "validation": {
            "schema_valid": True,
            "structure_complete": certified,
            "metadata_complete": True,
            "source_stable": certified,
            "complete": certified,
            "pages_count": 1,
            "unique_files_count": 1,
            "file_placements_count": 2,
            "known_unique_size_bytes": len(PDF),
            "unknown_sizes_count": 0,
            "issues_count": 0,
        },
        "issues": [],
    }


class ArchiveFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "external"
        self.root.mkdir()
        self.source = self.base / "separate-inputs" / "inventory.json"
        self.source.parent.mkdir()
        self.source.write_text(json.dumps(inventory()))
        self.archive = Archive(self.root, create=True)
        self.options = Options(
            allow_hosts=("127.0.0.1",),
            allow_http=True,
            reserve_bytes=0,
            chunk_bytes=16,
            retries=1,
            retry_delay=0,
            request_delay=0,
            timeout=2,
        )

    def tearDown(self):
        self.archive.close()
        self.temporary.cleanup()

    def row(self):
        return self.archive.db.execute("SELECT * FROM assets").fetchone()


class CatalogTests(ArchiveFixture):
    def test_offline_plan_preserves_tree_and_all_placements(self):
        with patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("Network forbidden")
        ):
            result = plan(self.archive, self.source)
        self.assertEqual(result["total"], 1)
        self.assertEqual(
            self.archive.db.execute("SELECT COUNT(*) FROM placements").fetchone()[0], 2
        )
        saved = self.archive.path(f"{CONTROL}/inputs/{result['source_sha256']}.json")
        self.assertEqual(saved.read_bytes(), self.source.read_bytes())
        self.assertEqual(plan(self.archive, self.source)["total"], 1)
        self.assertFalse(result["complete_at_last_audit"])

    def test_schema_v1_and_counts(self):
        self.source.write_text(json.dumps(inventory(version=1)))
        self.assertEqual(plan(self.archive, self.source)["total"], 1)

    def test_partial_plan_needs_explicit_pilot_flags(self):
        self.source.write_text(json.dumps(inventory(certified=False)))
        with self.assertRaisesRegex(ValueError, "Partial inventory"):
            plan(self.archive, self.source)
        self.assertFalse(
            plan(self.archive, self.source, allow_partial=True)["source_inventory_complete"]
        )
        with self.assertRaisesRegex(ValueError, "requires --allow-partial"):
            fetch(self.archive, self.options)

    def test_inconsistent_counts_and_dangling_provenance_roll_back(self):
        for change in (
            "count",
            "placement",
            "page",
            "occurrence_page",
            "asset_identity",
            "unresolved",
            "missing_field",
        ):
            with self.subTest(change=change):
                data = inventory()
                if change == "count":
                    data["validation"]["unique_files_count"] = 2
                elif change == "placement":
                    data["assets"]["asset-1"]["occurrences"][0]["node_id"] = "missing"
                elif change == "page":
                    data["pages"][0]["verified_hash"] = "different"
                elif change == "occurrence_page":
                    data["assets"]["asset-1"]["occurrences"][0]["page_id"] = "missing"
                elif change == "asset_identity":
                    data["assets"]["asset-1"]["asset_id"] = "different"
                elif change == "unresolved":
                    data["tree"]["children"].append(
                        {"id": "unknown-link", "name": "Unknown", "kind": "unresolved"}
                    )
                else:
                    del data["metadata"]["task_uid"]
                self.source.write_text(json.dumps(data))
                with self.assertRaises((ValueError, ValidationError)):
                    plan(self.archive, self.source)
                self.assertFalse(self.archive.meta("plan_complete"))
                self.assertEqual(
                    self.archive.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0
                )

    def test_different_dump_cannot_rebind_existing_destination(self):
        plan(self.archive, self.source)
        data = inventory()
        data["metadata"]["snapshot_id"] = "new-snapshot"
        self.source.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "immutable dump"):
            plan(self.archive, self.source)

    def test_evidence_db_is_optional_read_only_and_snapshot_checked(self):
        database = self.base / "elsewhere.sqlite3"
        with closing(sqlite3.connect(database)) as db:
            db.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            db.execute("INSERT INTO meta VALUES ('snapshot_id','\"wrong-snapshot\"')")
            db.commit()
        with self.assertRaisesRegex(ValueError, "different snapshots"):
            plan(self.archive, self.source, database)
        with closing(sqlite3.connect(database)) as db:
            db.execute("UPDATE meta SET value='\"synthetic-snapshot\"'")
            db.commit()
        before = digest_file(database)
        plan(self.archive, self.source, database)
        self.assertEqual(digest_file(database), before)
        self.assertEqual(self.archive.meta("evidence")["sha256"], before)

    def test_missing_disk_is_not_created(self):
        missing = self.base / "unmounted" / "archive"
        with self.assertRaises(StorageUnavailable):
            Archive(missing, create=True)
        self.assertFalse(missing.exists())

    def test_same_destination_has_one_writer(self):
        with self.assertRaisesRegex(ValueError, "Another downloader"):
            Archive(self.root, create=True)

    def test_input_truncation_cannot_make_ready_plan(self):
        self.source.write_text('{"schema_version":2,"metadata":')
        with self.assertRaises(JSONError):
            plan(self.archive, self.source)
        self.assertFalse(self.archive.meta("plan_complete"))


class HTTPFixture(ArchiveFixture):
    def setUp(self):
        super().setUp()
        self.requests = []
        self.mode = "normal"
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                owner.requests.append(dict(self.headers))
                if owner.mode == "rate-limit" and len(owner.requests) == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", getattr(owner, "retry_after", "0"))
                    self.end_headers()
                    return
                if owner.mode == "server-error":
                    self.send_response(500)
                    self.end_headers()
                    return
                body, status, start = PDF, 200, 0
                etag = '"synthetic-v1"'
                if "Range" in self.headers and owner.mode not in ("ignore-range", "changed-etag"):
                    start = int(self.headers["Range"].split("=")[1].split("-")[0])
                    status = 206
                    body = PDF[start:]
                if owner.mode == "changed-etag":
                    etag = '"synthetic-v2"'
                if owner.mode == "html":
                    body = b"<html>blocked</html>".ljust(len(PDF), b" ")
                self.send_response(status)
                self.send_header(
                    "Content-Type", "text/html" if owner.mode == "html" else "application/pdf"
                )
                self.send_header("Content-Length", str(len(body)))
                if owner.mode != "no-validator":
                    self.send_header("ETag", etag)
                if status == 206:
                    range_start = start + 1 if owner.mode == "bad-range" else start
                    self.send_header(
                        "Content-Range", f"bytes {range_start}-{len(PDF) - 1}/{len(PDF)}"
                    )
                self.end_headers()
                if owner.mode == "disconnect" and len(owner.requests) == 1:
                    self.wfile.write(body[:96])
                    self.wfile.flush()
                    self.close_connection = True
                else:
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.source.write_text(
            json.dumps(inventory(f"http://127.0.0.1:{self.server.server_port}/data/one.pdf"))
        )
        plan(self.archive, self.source)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        super().tearDown()

    def seed_partial(self, valid=True, etag='"synthetic-v1"'):
        partial, sidecar, receipt = artifact_paths(self.archive, self.row())
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(PDF[:80] if valid else b"X" * 80)
        atomic_json(
            sidecar,
            {
                "asset_id": "asset-1",
                "source_sha256": self.archive.meta("source_sha256"),
                "etag": etag,
                "checkpoint_bytes": 80,
                "checkpoint_sha256": hashlib.sha256(PDF[:80]).hexdigest(),
            },
        )

    def assert_downloaded(self):
        result = fetch(self.archive, self.options)
        self.assertTrue(result["download_complete"])
        path = self.archive.path(self.row()["relative_path"])
        self.assertEqual(path.read_bytes(), PDF)
        self.assertTrue(verify(self.archive, self.options)["complete_at_last_audit"])


class TransferTests(HTTPFixture):
    def test_real_http_download_offline_full_audit_and_manifest(self):
        self.assert_downloaded()
        with patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline audit")
        ):
            result = verify(self.archive, self.options)
        self.assertTrue(result["last_full_audit"]["passed"])
        manifest = self.archive.path(f"{CONTROL}/verified-files.jsonl")
        self.assertEqual(digest_file(manifest), result["last_full_audit"]["manifest_sha256"])
        self.assertEqual(len(json.loads(manifest.read_text())["metadata"]["occurrences"]), 2)

    def test_interrupted_transfer_resumes_with_range_and_if_range(self):
        self.mode = "disconnect"
        self.assert_downloaded()
        self.assertEqual(self.requests[1]["Range"], "bytes=96-")
        self.assertEqual(self.requests[1]["If-Range"], '"synthetic-v1"')

    def test_seeded_partial_resumes_without_duplicate_prefix(self):
        self.seed_partial()
        self.assert_downloaded()
        self.assertEqual(self.requests[0]["Range"], "bytes=80-")

    def test_ignored_range_restarts_and_changed_etag_is_recorded(self):
        self.mode = "changed-etag"
        self.seed_partial()
        self.assert_downloaded()
        self.assertEqual(json.loads(self.row()["receipt"])["etag"], '"synthetic-v2"')

    def test_bad_range_never_publishes(self):
        self.mode = "bad-range"
        self.seed_partial()
        result = fetch(self.archive, self.options)
        self.assertEqual(result["statuses"], {"failed": 1})
        self.assertFalse(self.archive.path(self.row()["relative_path"]).exists())

    def test_corrupt_partial_and_weak_validator_restart(self):
        self.seed_partial(valid=False)
        self.assert_downloaded()
        self.assertNotIn("Range", self.requests[0])

    def test_weak_validator_restart(self):
        self.seed_partial(etag='W/"weak"')
        self.assert_downloaded()
        self.assertNotIn("Range", self.requests[0])

    def test_html_gate_is_not_an_original(self):
        self.mode = "html"
        self.assertEqual(fetch(self.archive, self.options)["statuses"], {"failed": 1})
        self.assertFalse(self.archive.path(self.row()["relative_path"]).exists())

    def test_optional_source_checksum_is_enforced(self):
        data = inventory(json.loads(self.row()["metadata"])["url"])
        data["assets"]["asset-1"]["sha256"] = "0" * 64
        self.archive.close()
        self.root = self.base / "source-checksum-case"
        self.root.mkdir()
        self.archive = Archive(self.root, create=True)
        self.source.write_text(json.dumps(data))
        plan(self.archive, self.source)
        self.assertEqual(fetch(self.archive, self.options)["statuses"], {"failed": 1})
        self.assertFalse(self.archive.path(self.row()["relative_path"]).exists())

    def test_bounded_audit_cannot_publish_a_full_manifest(self):
        self.assert_downloaded()
        result = verify(self.archive, self.options, limit=1)
        self.assertFalse(result["complete_at_last_audit"])
        self.assertFalse(self.archive.path(f"{CONTROL}/verified-files.jsonl").exists())

    def test_rate_limit_retries_and_server_errors_are_bounded(self):
        self.mode = "server-error"
        self.assertEqual(fetch(self.archive, self.options)["statuses"], {"failed": 1})
        self.assertEqual(len(self.requests), 2)
        self.requests.clear()
        self.mode = "rate-limit"
        self.assert_downloaded()
        self.assertEqual(len(self.requests), 2)

    def test_size_preserving_corruption_detected_and_quarantined_for_retry(self):
        self.assert_downloaded()
        path = self.archive.path(self.row()["relative_path"])
        path.write_bytes(path.read_bytes().replace(b"pypdf", b"pypdX"))
        self.assertFalse(verify(self.archive, self.options)["last_full_audit"]["passed"])
        self.assertFalse(self.archive.path(f"{CONTROL}/verified-files.jsonl").exists())
        result = quarantine(self.archive, "asset-1")
        self.assertTrue(self.archive.path(result["quarantine"]).exists())
        self.assert_downloaded()

    def test_receipt_recovers_crash_before_rename_without_http(self):
        self.assert_downloaded()
        row = self.row()
        partial = artifact_paths(self.archive, row)[0]
        self.archive.path(row["relative_path"]).replace(partial)
        with self.archive.db:
            self.archive.db.execute("UPDATE assets SET status='downloading'")
        requests = len(self.requests)
        self.assert_downloaded()
        self.assertEqual(len(self.requests), requests)

    def test_tampered_reference_is_reported_then_reconciled(self):
        self.assert_downloaded()
        placement = self.archive.db.execute(
            "SELECT relative_path FROM placements LIMIT 1"
        ).fetchone()[0]
        self.archive.path(placement).write_text("{}")
        self.assertFalse(verify(self.archive, self.options)["last_full_audit"]["passed"])
        requests = len(self.requests)
        self.assert_downloaded()
        self.assertEqual(len(self.requests), requests)

    def test_reserve_pause_makes_no_request(self):
        options = Options(allow_hosts=("127.0.0.1",), allow_http=True, reserve_bytes=2**63)
        with self.assertRaises(StoragePause):
            fetch(self.archive, options)
        self.assertEqual(self.requests, [])

    def test_removed_destination_has_no_fallback(self):
        self.root.rename(self.base / "disconnected")
        with self.assertRaises(StorageUnavailable):
            fetch(self.archive, self.options)
        self.assertFalse(self.root.exists())
        self.assertEqual(self.requests, [])


@unittest.skipUnless(
    os.environ.get("ARCHIVE_TEST_BASE_URL"), "Start the local Postman archive simulation"
)
class PostmanArchiveTests(ArchiveFixture):
    def test_standalone_cli_with_separate_inputs_and_destination(self):
        url = os.environ["ARCHIVE_TEST_BASE_URL"] + "/source/document.pdf"
        self.archive.close()
        self.source.write_text(json.dumps(inventory(url)))
        config = self.base / "settings.json"
        config.write_text(
            json.dumps(
                {
                    "allow_hosts": ["127.0.0.1", "localhost"],
                    "allow_http": True,
                    "reserve_bytes": 0,
                    "request_delay": 0,
                    "retry_delay": 0,
                }
            )
        )
        prefix = ["--destination", str(self.root), "--config", str(config)]
        script = Path(__file__).resolve().parents[2] / "scripts" / "download_archive.py"
        for command in (["plan", "--dump", str(self.source)], ["fetch"], ["verify"]):
            result = subprocess.run(
                [sys.executable, str(script), *prefix, *command],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.archive = Archive(self.root)
        self.assertTrue(report(self.archive)["complete_at_last_audit"])


class FormatTests(ArchiveFixture):
    def test_zip_crc_and_verification_budget(self):
        path = self.base / "original.zip"
        with zipfile.ZipFile(path, "w") as stream:
            stream.writestr("folder/document.txt", "Synthetic archive record")
        metadata = {"file_kind": "archive", "original_name": "original.zip"}
        self.assertEqual(validate_content(path, metadata, self.options)["method"], "zip_crc")
        with self.assertRaisesRegex(ValueError, "budget"):
            validate_content(path, metadata, Options(max_zip_bytes=1))
        with zipfile.ZipFile(path) as stream:
            member = stream.infolist()[0]
            offset = member.header_offset + 30 + len(member.filename.encode()) + len(member.extra)
        data = bytearray(path.read_bytes())
        data[offset] ^= 1
        path.write_bytes(data)
        with self.assertRaises(zipfile.BadZipFile):
            validate_content(path, metadata, self.options)

    def test_unsafe_zip_is_not_extracted_or_accepted(self):
        path = self.base / "unsafe.zip"
        with zipfile.ZipFile(path, "w") as stream:
            stream.writestr("../escape.txt", "Synthetic")
        with self.assertRaisesRegex(ValueError, "unsafe paths"):
            validate_content(
                path, {"file_kind": "archive", "original_name": "unsafe.zip"}, self.options
            )
        self.assertFalse((self.base / "escape.txt").exists())
