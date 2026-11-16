"""Partitioning and downloader compatibility use synthetic, offline inventories."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import test_archive as fixtures
from explorer_ingest.archive_adoption import adopt_archive
from explorer_ingest.archive_catalog import CONTROL, Archive, plan, report
from explorer_ingest.archive_maintenance import backup, rebuild, restore
from explorer_ingest.archive_selection import create_pilot
from explorer_ingest.archive_transfer import fetch, verify
from explorer_ingest.inventory_parts import Inventory, guard_directory, split_inventory
from explorer_ingest.state import Store, digest_file
from explorer_ingest.synthetic_archive import generate_inventory


class PartitionTests(fixtures.ArchiveFixture):
    def split(self, source=None, name="parts", target=3000):
        result = split_inventory(source or self.source, self.base / name, target, reserve_bytes=0)
        return Path(result["manifest"])

    def test_roundtrip_preserves_order_nested_extensions_unicode_and_original(self):
        data = fixtures.inventory()
        data["tree"]["extension.key"] = {"nested": [1, {"name": "Документы 📄"}]}
        data["tree"]["extension.children.item"] = {"retained": ["literal dotted key"]}
        data["tree"]["children"][0]["children"][0]["extra"] = [False, None, {"x": 0.5}]
        self.source.write_text(json.dumps(data, ensure_ascii=False))
        before = digest_file(self.source)
        manifest = self.split(target=1600)
        original, bundle = Inventory(self.source), Inventory(manifest, manifest=True)
        self.assertEqual(original.scan(), bundle.scan())
        self.assertEqual(bundle.sha256, before)
        self.assertEqual(digest_file(self.source), before)
        nodes = {}
        records = list(bundle.records("tree"))
        for r in records:
            nodes[r["seq"]] = r["data"]
        for r in sorted(records, key=lambda r: r["seq"]):
            if r["parent"] is not None:
                nodes[r["parent"]]["children"].append(nodes[r["seq"]])
        self.assertEqual(nodes[1], data["tree"])
        for part in bundle.manifest["parts"]:
            self.assertEqual(
                len(json.loads(bundle.member(part).read_text())["records"]),
                sum(part["counts"].values()),
            )
            self.assertLessEqual(part["bytes"], 1600)

    def test_v1_bundle_plan_and_missing_original_rebuild(self):
        self.source.write_text(json.dumps(fixtures.inventory(version=1)))
        manifest = self.split()
        result = plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertEqual(result["total"], 1)
        self.assertEqual(self.archive.meta("schema_version"), 1)
        self.assertEqual(
            self.archive.db.execute("SELECT COUNT(*) FROM placements").fetchone()[0], 2
        )
        self.assertFalse(
            (self.root / CONTROL / "inputs" / (digest_file(self.source) + ".json")).exists()
        )
        self.archive.close()
        (self.root / CONTROL / "archive.sqlite3").unlink()
        self.archive = Archive(self.root, create=True, recover=True)
        result = rebuild(
            self.archive, manifest, self.base / "absent-receipts", self.options, manifest=True
        )
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["download_complete"])

    def test_missing_corrupt_unsafe_and_duplicate_parts_cannot_make_ready_plan(self):
        manifest = self.split()
        original = json.loads(manifest.read_text())
        part = manifest.parent / original["parts"][0]["path"]
        contents = part.read_bytes()
        for case in ("missing", "corrupt", "symlink", "duplicate", "traversal"):
            with self.subTest(case=case):
                manifest.write_text(json.dumps(original))
                if part.exists() or part.is_symlink():
                    part.unlink()
                part.write_bytes(contents)
                if case == "missing":
                    part.unlink()
                elif case == "corrupt":
                    part.write_bytes(contents + b" ")
                elif case == "symlink":
                    part.unlink()
                    part.symlink_to(self.source)
                else:
                    altered = json.loads(json.dumps(original))
                    if case == "duplicate":
                        altered["parts"].append(altered["parts"][0])
                    else:
                        altered["parts"][0]["path"] = "../inventory.json"
                    manifest.write_text(json.dumps(altered))
                with self.assertRaises((OSError, ValueError)):
                    plan(self.archive, manifest, manifest=True, reserve_bytes=0)
                self.assertFalse(self.archive.meta("plan_complete"))

    def test_envelope_extra_field_is_rejected_even_with_updated_member_hash(self):
        manifest = self.split()
        data = json.loads(manifest.read_text())
        part = manifest.parent / data["parts"][0]["path"]
        payload = json.loads(part.read_text())
        payload["unexpected"] = []
        part.write_text(json.dumps(payload))
        data["parts"][0].update(bytes=part.stat().st_size, sha256=digest_file(part))
        manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "envelope"):
            Inventory(manifest, manifest=True).scan()

    def test_forged_origin_hash_cannot_rebind_existing_download_plan(self):
        plan(self.archive, self.source)
        before = self.row()["relative_path"]
        origin = digest_file(self.source)
        different = fixtures.inventory()
        different["assets"]["asset-1"]["original_name"] = "Replacement.pdf"
        source = self.base / "different.json"
        source.write_text(json.dumps(different))
        manifest = self.split(source)
        forged = json.loads(manifest.read_text())
        forged["source_sha256"] = origin
        manifest.write_text(json.dumps(forged))
        with self.assertRaisesRegex(ValueError, "differs"):
            plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertIsNone(self.archive.meta("source_bundle"))
        self.assertEqual(self.row()["relative_path"], before)

    def test_global_references_are_checked_before_bundle_plan_becomes_ready(self):
        data = fixtures.inventory()
        data["assets"]["asset-1"]["occurrences"][0]["node_id"] = "absent-node"
        self.source.write_text(json.dumps(data))
        manifest = self.split()
        with self.assertRaisesRegex(ValueError, "references"):
            plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertFalse(self.archive.meta("plan_complete"))
        with self.assertRaisesRegex(ValueError, "not completed"):
            self.archive.ready()

    def test_interrupted_asset_import_resumes_checkpoint_without_duplicate_records(self):
        source = self.base / "large.json"
        generate_inventory(source, 5002, page_size=200)
        manifest = self.split(source, target=100_000)
        from explorer_ingest.archive_catalog import log_event

        def interrupt(archive, event, **details):
            if event == "plan_checkpoint" and details == {"phase": "asset", "records": 5000}:
                raise KeyboardInterrupt
            log_event(archive, event, **details)

        with patch("explorer_ingest.archive_catalog.log_event", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertFalse(self.archive.meta("plan_complete"))
        self.assertEqual(self.archive.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 5000)
        self.assertEqual(self.archive.db.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        result = plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertEqual(result["total"], 5002)
        self.assertEqual(
            self.archive.db.execute("SELECT COUNT(*) FROM placements").fetchone()[0], 5002
        )
        self.assertIsNone(self.archive.meta("planning_progress"))

    def test_interrupted_copy_publishes_manifest_last_and_reuses_completed_parts(self):
        manifest = self.split(target=1000)
        from explorer_ingest.inventory_parts import checked_copy

        calls = 0

        def interrupt(source, target, *args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt
            return checked_copy(source, target, *args)

        with patch("explorer_ingest.inventory_parts.checked_copy", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        saved = next((self.root / CONTROL / "inputs" / "bundles").glob("*/part-00001.json"))
        before = saved.stat().st_mtime_ns
        self.assertFalse(saved.with_name("manifest.json").exists())
        self.assertEqual(plan(self.archive, manifest, manifest=True, reserve_bytes=0)["total"], 1)
        self.assertEqual(saved.stat().st_mtime_ns, before)

    def test_malformed_children_are_rejected_without_losing_source_fields(self):
        for children in (None, {}, [1]):
            with self.subTest(children=children):
                data = fixtures.inventory()
                data["tree"]["children"][0]["children"][0]["children"] = children
                self.source.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "children"):
                    self.split()
                self.assertFalse((self.base / "parts").exists())

    def test_invalid_root_collection_types_are_not_silently_converted_to_empty_arrays(self):
        for key in ("tree", "assets", "pages", "issues"):
            with self.subTest(key=key):
                data = fixtures.inventory()
                data[key] = None
                self.source.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "value type"):
                    self.split()
                self.assertFalse((self.base / "parts").exists())

    def test_partial_source_and_changed_volume_do_not_publish_output(self):
        data = fixtures.inventory(certified=False)
        self.source.write_text(json.dumps(data))
        before = digest_file(self.source)
        with self.assertRaisesRegex(ValueError, "complete"):
            self.split()
        self.assertEqual(digest_file(self.source), before)
        self.assertFalse((self.base / "parts").exists())
        volume = self.base / "disk"
        volume.mkdir()
        check = guard_directory(volume)
        volume.rename(self.base / "removed")
        with self.assertRaises(OSError):
            check()
        self.assertFalse(volume.exists())


class PartitionTransferTests(fixtures.HTTPFixture):
    def attach(self):
        manifest = Path(split_inventory(self.source, self.base / "parts", 1500, 0)["manifest"])
        plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        return manifest

    def test_attach_preserves_pilot_receipts_paths_attempts_and_idempotent_audit(self):
        create_pilot(self.archive, 1)
        fetch(self.archive, self.options, pilot=True)
        before = dict(self.row())
        manifest = self.attach()
        self.assertEqual(dict(self.row()), before)
        self.assertEqual(verify(self.archive, self.options, pilot=True)["pilot"]["verified"], True)
        audit = self.archive.meta("pilot_audit")
        plan(self.archive, manifest, manifest=True, reserve_bytes=0)
        self.assertEqual(self.archive.meta("pilot_audit"), audit)
        self.assertEqual(
            fetch(self.archive, self.options, pilot=True)["pilot"]["download_complete"], True
        )
        self.assertEqual(self.row()["attempts"], before["attempts"])

    def test_source_failure_invalidates_previous_successful_audit(self):
        self.attach()
        fetch(self.archive, self.options)
        self.assertTrue(verify(self.archive, self.options)["complete_at_last_audit"])
        source_bundle = self.archive.meta("source_bundle")
        manifest = self.root / source_bundle["path"]
        part = manifest.parent / "part-00001.json"
        part.write_bytes(part.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(self.archive, self.options)
        self.assertFalse(report(self.archive)["complete_at_last_audit"])

    def test_bundle_only_backup_restore_and_downstream_adoption(self):
        self.attach()
        original_json = (
            self.root / CONTROL / "inputs" / (self.archive.meta("source_sha256") + ".json")
        )
        original_json.unlink()  # Synthetic fixture: prove no hidden single-JSON dependency.
        fetch(self.archive, self.options)
        self.assertTrue(verify(self.archive, self.options)["complete_at_last_audit"])
        saved = self.base / "backup"
        backup(self.archive, saved, reserve_bytes=0)
        restored = self.base / "restored"
        restored.mkdir()
        shutil.copytree(self.root / "archive-files", restored / "archive-files")
        restore(restored, saved, reserve_bytes=0)
        with_archive = Archive(restored)
        try:
            self.assertTrue(verify(with_archive, self.options)["complete_at_last_audit"])
        finally:
            with_archive.close()
        store = Store(self.base / "derived")
        try:
            result = adopt_archive(store, restored)
            self.assertEqual(result["assets"], 1)
            saved_manifests = list((store.root / "inventories").glob("*.parts/*/manifest.json"))
            self.assertTrue(saved_manifests)
            self.assertEqual(digest_file(saved_manifests[0]), saved_manifests[0].parent.name)
        finally:
            store.close()
        self.assertTrue(report(self.archive)["download_complete"])
