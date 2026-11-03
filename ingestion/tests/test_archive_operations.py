"""Recovery and observability use synthetic local files and loopback transfers."""

import json
import shutil
import sqlite3
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_catalog import CONTROL, Archive, StorageUnavailable, report
from explorer_ingest.archive_cli import main
from explorer_ingest.archive_maintenance import backup, rebuild, restore
from explorer_ingest.archive_observer import ArchiveView, doctor, watch
from explorer_ingest.archive_transfer import fetch, verify
from explorer_ingest.state import digest_file


class OperationTests(fixtures.HTTPFixture):
    def test_observer_works_while_writer_held_and_never_writes(self):
        before = digest_file(self.root / CONTROL / "archive.sqlite3")
        view = ArchiveView(self.root)
        try:
            samples = list(watch(view, interval=0.001, samples=2))
            self.assertTrue(samples[0]["writer_active"])
            self.assertIsNone(samples[0]["eta_seconds"])
            self.assertEqual(samples[1]["bytes_per_second"], 0)
            with self.assertRaises(sqlite3.OperationalError):
                view.db.execute("CREATE TABLE forbidden(id)")
        finally:
            view.close()
        self.assertEqual(digest_file(self.root / CONTROL / "archive.sqlite3"), before)
        self.assertEqual(
            main(["--destination", str(self.root), "status", "--watch", "--samples", "1"]), 0
        )

    def test_doctor_is_offline_and_missing_disk_is_never_created(self):
        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("offline")):
            result = doctor(self.root, reserve_bytes=0, write_probe=True)
        self.assertTrue(result["ok"])
        self.assertTrue(result["writer_active"])
        self.assertEqual(result["write_probe"], "passed")
        self.assertEqual(list(self.root.glob(".pw010-probe-*")), [])
        missing = self.base / "unmounted"
        with self.assertRaises(StorageUnavailable):
            doctor(missing)
        self.assertFalse(missing.exists())

    def test_backup_restore_keeps_originals_and_requires_new_audit(self):
        fetch(self.archive, self.options)
        verify(self.archive, self.options)
        saved = self.base / "backup"
        result = backup(self.archive, saved, reserve_bytes=0)
        self.assertFalse(result["contains_originals"])
        self.assertFalse((saved / "archive-files").exists())
        restored = self.base / "restored"
        restored.mkdir()
        shutil.copytree(self.root / "archive-files", restored / "archive-files")
        self.assertTrue(restore(restored, saved, reserve_bytes=0)["fresh_full_audit_required"])
        archive = Archive(restored)
        try:
            self.assertIsNone(report(archive)["last_full_audit"])
            self.assertTrue(verify(archive, self.options)["complete_at_last_audit"])
            self.assertEqual(archive.marker, self.archive.marker)
        finally:
            archive.close()
        with self.assertRaisesRegex(ValueError, "never replaces"):
            restore(restored, saved, reserve_bytes=0)

    def test_restore_missing_payload_retries_and_corrupt_backup_never_publishes(self):
        fetch(self.archive, self.options)
        saved = self.base / "backup"
        backup(self.archive, saved, reserve_bytes=0)
        restored = self.base / "restored"
        restored.mkdir()
        restore(restored, saved, reserve_bytes=0)
        archive = Archive(restored)
        try:
            self.assertFalse(list((restored / CONTROL / "receipts").glob("*.json")))
            self.assertTrue(fetch(archive, self.options)["download_complete"])
        finally:
            archive.close()
        receipt = next((saved / "receipts").glob("*.json"))
        receipt.write_text("{}")
        rejected = self.base / "rejected"
        rejected.mkdir()
        with self.assertRaisesRegex(ValueError, "checksum"):
            restore(rejected, saved, reserve_bytes=0)
        self.assertFalse((rejected / CONTROL).exists())

    def test_rebuild_lost_ledger_from_inventory_and_receipts(self):
        fetch(self.archive, self.options)
        before = digest_file(self.archive.path(self.row()["relative_path"]))
        self.archive.close()
        (self.root / CONTROL / "archive.sqlite3").unlink()
        self.archive = Archive(self.root, create=True, recover=True)
        result = rebuild(self.archive, self.source, self.root / CONTROL / "receipts", self.options)
        self.assertTrue(result["complete_at_last_audit"])
        self.assertEqual(result["recovered_receipts"], 1)
        self.assertEqual(digest_file(self.archive.path(self.row()["relative_path"])), before)
        # Check inside the lease: an existing ledger is never replaced.
        self.archive.close()
        with self.assertRaisesRegex(ValueError, "never replaces"):
            Archive(self.root, create=True, recover=True)
        self.archive = Archive(self.root)

    def test_unsafe_backup_member_is_rejected(self):
        saved = self.base / "backup"
        backup(self.archive, saved, reserve_bytes=0)
        manifest = saved / "files.jsonl"
        manifest.write_text(
            json.dumps({"path": "../escape", "sha256": "0" * 64, "bytes": 0}) + "\n"
        )
        header = json.loads((saved / "backup.json").read_text())
        header["files_sha256"] = digest_file(manifest)
        (saved / "backup.json").write_text(json.dumps(header))
        restored = self.base / "restored"
        restored.mkdir()
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            restore(restored, saved, reserve_bytes=0)
