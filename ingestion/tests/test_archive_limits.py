"""Subprocess limits and forced-crash tests use only local synthetic originals."""

import subprocess
import sys
import unittest
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_catalog import Archive, plan
from explorer_ingest.archive_limits import ValidationBudgetError, run_limited
from explorer_ingest.archive_transfer import Options, fetch, verify


class LimitTests(unittest.TestCase):
    def test_hung_worker_is_killed_and_reaped(self):
        with self.assertRaisesRegex(ValidationBudgetError, "wall-time"):
            run_limited(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                {},
                Options(validation_timeout=0.2),
            )

    def test_memory_monitor_stops_worker(self):
        with patch("explorer_ingest.archive_limits.resident_bytes", return_value=2**30):
            with self.assertRaisesRegex(ValidationBudgetError, "resident-memory"):
                run_limited([sys.executable, "-c", "import time; time.sleep(10)"], {}, Options())

    def test_actual_child_allocation_is_bounded(self):
        with self.assertRaisesRegex(ValidationBudgetError, "resident-memory"):
            run_limited(
                [sys.executable, "-c", "import time; data=bytearray(64*1024**2); time.sleep(10)"],
                {},
                Options(validation_memory_mb=32, validation_timeout=3),
            )

    def test_storage_interrupt_reaps_worker(self):
        def interrupted():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            run_limited(
                [sys.executable, "-c", "import time; time.sleep(10)"], {}, Options(), interrupted
            )


class CrashTests(fixtures.HTTPFixture):
    def test_forced_exit_at_each_publication_boundary_is_resumable(self):
        self.archive.close()
        for point in (
            "partial_checkpoint",
            "receipt_committed",
            "original_published",
            "references_committed",
            "ledger_committed",
            "audit_manifest_published",
        ):
            with self.subTest(point=point):
                root = self.base / point
                root.mkdir()
                self.archive = Archive(root, create=True)
                plan(self.archive, self.source)
                if point == "audit_manifest_published":
                    fetch(self.archive, self.options)
                self.archive.close()
                code = """
import os,sys
from explorer_ingest.archive_catalog import Archive
from explorer_ingest.archive_transfer import Options,fetch
import explorer_ingest.archive_transfer as transfer
root, point = sys.argv[1:]
hits = 0
def crash(name):
 global hits
 if name == point:
  hits += 1
  if point != "partial_checkpoint" or hits == 2:
   os._exit(73)
transfer.boundary=crash
archive=Archive(root)
opts=Options(allow_hosts=("127.0.0.1",),allow_http=True,reserve_bytes=0,request_delay=0)
if point == "audit_manifest_published":
 transfer.verify(archive,opts)
else:
 fetch(archive,opts)
"""
                child = subprocess.run(
                    [sys.executable, "-c", code, str(root), point],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(child.returncode, 73, child.stderr)
                self.archive = Archive(root)
                self.assertTrue(fetch(self.archive, self.options)["download_complete"])
                self.assertTrue(verify(self.archive, self.options)["complete_at_last_audit"])
                self.assertEqual(
                    self.archive.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 1
                )
                self.archive.close()
        self.archive = Archive(root)
