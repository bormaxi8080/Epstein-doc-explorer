"""Scoped handoffs preserve archive data and remain independent of later fetches."""

import test_archive as fixtures
from explorer_ingest.archive_catalog import Archive
from explorer_ingest.archive_selection import create_pilot
from explorer_ingest.archive_transfer import fetch, verify
from explorer_ingest.corpus_sources import freeze_sources, selected_sources
from explorer_ingest.state import digest_file


class SourceTests(fixtures.HTTPFixture):
    def test_verified_pilot_is_frozen_without_copying_originals(self):
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        verify(self.archive, self.options, pilot=True)
        original = self.archive.path(self.row()["relative_path"])
        before = digest_file(original)
        self.archive.close()
        try:
            output = self.base / "selection"
            self.assertEqual(freeze_sources(self.root, output)["files_count"], 1)
            records = list(selected_sources(output / "selection.json"))
            self.assertEqual(len(records[0][1]["metadata"]["occurrences"]), 2)
            self.assertEqual(digest_file(original), before)
            self.assertFalse(list(output.rglob("*.pdf")))
            (output / "sources.jsonl").write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum"):
                list(selected_sources(output / "selection.json"))
        finally:
            self.archive = Archive(self.root)

    def test_active_writer_and_missing_audit_reject_handoff(self):
        with self.assertRaisesRegex(ValueError, "writer"):
            freeze_sources(self.root, self.base / "selection")
        self.archive.close()
        try:
            with self.assertRaisesRegex(ValueError, "audit"):
                freeze_sources(self.root, self.base / "selection")
        finally:
            self.archive = Archive(self.root)
