"""Offline handoff protects originals, completeness and source occurrences."""

import json
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_adoption import adopt_archive
from explorer_ingest.archive_catalog import CONTROL, Archive
from explorer_ingest.archive_transfer import fetch, verify
from explorer_ingest.extraction import extract_assets
from explorer_ingest.state import Store, digest_file


class AdoptionTests(fixtures.HTTPFixture):
    def setUp(self):
        super().setUp()
        self.store = Store(self.base / "derived")
        self.addCleanup(self.store.close)

    def release_archive(self):
        self.archive.close()
        self.archive = None

    def tearDown(self):
        if self.archive is None:
            self.archive = Archive(self.root)
        super().tearDown()

    def completed(self):
        fetch(self.archive, self.options)
        verify(self.archive, self.options)
        self.original = self.archive.path(self.row()["relative_path"])
        self.release_archive()

    def test_adoption_and_extraction_preserve_originals_and_occurrences(self):
        self.completed()
        before = digest_file(self.original)
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("offline")):
            one = adopt_archive(self.store, self.root)
            self.assertEqual(adopt_archive(self.store, self.root), one)
            self.assertEqual(extract_assets(self.store)[0]["documents"], 1)
        self.assertEqual(digest_file(self.original), before)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 1)
        asset = self.store.db.execute("SELECT * FROM assets").fetchone()
        self.assertEqual(len(json.loads(asset["metadata"])["occurrences"]), 2)
        doc = self.store.db.execute("SELECT * FROM documents").fetchone()
        self.assertTrue(doc["original_path"].startswith("archive://"))
        self.assertFalse((self.store.root / "archive-files").exists())
        self.assertEqual(extract_assets(self.store), [])

    def test_requires_full_audit_and_no_ingestion_rows_on_failure(self):
        fetch(self.archive, self.options)
        self.release_archive()
        with self.assertRaisesRegex(ValueError, "full audit"):
            adopt_archive(self.store, self.root)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)

    def test_rejects_changed_original_manifest_and_later_source_audit(self):
        self.completed()
        original = self.original.read_bytes()
        self.original.write_bytes(b"X" * len(original))
        with self.assertRaisesRegex(ValueError, "Original changed"):
            adopt_archive(self.store, self.root)
        self.original.write_bytes(original)
        adopt_archive(self.store, self.root)
        archive = Archive(self.root)
        with archive.db:
            archive.set_meta("last_full_audit", None)
        archive.close()
        with self.assertRaisesRegex(ValueError, "full audit"):
            extract_assets(self.store)
        manifest = self.root / CONTROL / "verified-files.jsonl"
        manifest.write_text(manifest.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "full audit"):
            adopt_archive(self.store, self.root)

    def test_writer_lease_and_overlapping_roots_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "owns"):
            adopt_archive(self.store, self.root)
        with self.assertRaisesRegex(ValueError, "separate roots"):
            adopt_archive(Store(self.root / "nested"), self.root)
