import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_catalog import Archive, plan
from explorer_ingest.archive_selection import create_pilot
from explorer_ingest.archive_transfer import fetch, verify
from explorer_ingest.corpus_analysis import analyze_fragment, assemble_analysis, plan_analysis
from explorer_ingest.corpus_contracts import document_id
from explorer_ingest.corpus_ocr import select_page
from explorer_ingest.corpus_registry import Corpus
from explorer_ingest.corpus_runtime import operation_lock
from explorer_ingest.corpus_sources import freeze_sources, selected_sources
from explorer_ingest.corpus_storage import CorpusLimits
from explorer_ingest.corpus_text import assemble_text
from explorer_ingest.distribution_pilot import adopt_pilot, export_pilot, lines, validate_pilot
from explorer_ingest.state import digest_file
from test_corpus_analysis import Client


class PortablePilotTests(fixtures.HTTPFixture):
    def prepare(self):
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        verify(self.archive, self.options, pilot=True)
        original = self.archive.path(self.row()["relative_path"])
        self.original = original
        self.archive.close()
        freeze_sources(self.root, self.base / "selection")
        self.archive = Archive(self.root)
        croot = self.base / "corpus"
        croot.mkdir()
        c = Corpus(croot, create=True, limits=CorpusLimits(reserve_bytes=0))
        try:
            c.import_selection(self.base / "selection/selection.json")
            with operation_lock(c):
                sha = digest_file(original)
                doc = document_id(sha)
                c.inspected(doc, dict(format="pdf", status="inspected", pages=1))
                select_page(
                    c,
                    doc,
                    1,
                    "SYNTHETIC source for portable testing",
                    "native",
                    {},
                    issues=["review"],
                )
                assemble_text(c, doc, allow_review=True)
                client = Client()
                plan_analysis(c, doc, client.identity(), allow_review=True)
                job = c.queue.claim("test", "analyze")
                value = analyze_fragment(c, json.loads(job["payload"]), client)
                c.queue.finish(job["id"], job["token"], value)
                assemble_analysis(c, doc, client, allow_review=True)
        finally:
            c.close()
        output = self.base / "portable"
        export_pilot(self.base / "selection/selection.json", croot, output, reserve_bytes=0)
        for record in lines(output / "sources.jsonl"):
            target = output / record["relative_path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        return croot, output

    def test_roundtrip_new_archive_keeps_ids_receipts_and_requires_review(self):
        _, portable = self.prepare()
        before = digest_file(self.original)
        header = validate_pilot(portable, original_root=portable)
        self.assertTrue(header["requires_review"])
        destination = self.base / "new-archive"
        destination.mkdir()
        target = Archive(destination, create=True)
        try:
            plan(target, self.source, reserve_bytes=0)
            with patch(
                "explorer_ingest.archive_transfer.receive", side_effect=AssertionError("no HTTP")
            ):
                result = adopt_pilot(target, portable, self.options)
            self.assertTrue(result["pilot"]["verified"])
            self.assertFalse(result["complete_at_last_audit"])
            self.assertEqual(digest_file(self.original), before)
            self.assertNotEqual(target.marker["uuid"], self.archive.marker["uuid"])
            receipt = json.loads(target.db.execute("SELECT receipt FROM assets").fetchone()[0])
            origin = next(lines(portable / "sources.jsonl"))["receipt_data"]
            self.assertEqual(receipt, origin)
        finally:
            target.close()
        config = self.base / "options.json"
        config.write_text(json.dumps(asdict(self.options)))
        result = subprocess.run(
            [
                sys.executable,
                "scripts/download_archive.py",
                "--destination",
                str(destination),
                "--config",
                str(config),
                "import-pilot",
                str(portable),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        freeze_sources(destination, self.base / "relocated-selection")
        record = next(selected_sources(self.base / "relocated-selection/selection.json"))[1]
        self.assertEqual(document_id(record["sha256"]), document_id(before))

    def test_tampered_original_and_analysis_coverage_fail_closed(self):
        _, portable = self.prepare()
        record = next(lines(portable / "sources.jsonl"))
        (portable / record["relative_path"]).write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "original checksum"):
            validate_pilot(portable, original_root=portable)
        shutil.copyfile(self.original, portable / record["relative_path"])
        value = next(lines(portable / "analyses.jsonl"))
        value["start"] = 1
        path = portable / "analyses.jsonl"
        path.write_text(json.dumps(value) + "\n")
        header = json.loads((portable / "selection.json").read_text())
        header["files"]["analyses.jsonl"] = dict(
            bytes=path.stat().st_size, sha256=digest_file(path)
        )
        (portable / "selection.json").write_text(json.dumps(header))
        with self.assertRaisesRegex(ValueError, "coverage gap"):
            validate_pilot(portable, original_root=portable)

    def test_export_does_not_snapshot_an_active_corpus_writer(self):
        croot, _ = self.prepare()
        c = Corpus(croot)
        try:
            with operation_lock(c):
                with self.assertRaisesRegex(ValueError, "Pause"):
                    export_pilot(
                        self.base / "selection/selection.json",
                        croot,
                        self.base / "busy",
                        reserve_bytes=0,
                    )
            self.assertFalse((self.base / "busy").exists())
        finally:
            c.close()
