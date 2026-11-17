import gzip
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import test_archive as fixtures
from explorer_ingest.distribution_release import build_release, verify_release
from explorer_ingest.inventory_parts import Inventory, split_inventory
from explorer_ingest.state import digest_file


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "original.json"
        self.source.write_text(json.dumps(fixtures.inventory()))
        self.parts = self.root / "parts"
        split_inventory(self.source, self.parts, 1600, reserve_bytes=0)
        inventory = Inventory(self.source)
        self.recipe = dict(
            release_id="synthetic-1",
            visibility="public",
            inventory=dict(
                snapshot_id=inventory.header["metadata"]["snapshot_id"],
                source_sha256=inventory.sha256,
                unique_assets=1,
                complete=True,
            ),
            artifacts=[],
        )
        self.add("full", self.source, "inventory-full", "inventory-full", "full.json")
        self.add(
            "manifest",
            self.parts / "manifest.json",
            "inventory-parts",
            "inventory-manifest",
            "parts/manifest.json",
        )
        for part in inventory_parts(self.parts):
            self.add(part.stem, part, "inventory-parts", "inventory-part", "parts/" + part.name)

    def add(self, name, source, group, role, install):
        self.recipe["artifacts"].append(
            dict(
                id=name,
                source=str(source),
                group=group,
                role=role,
                encoding="gzip",
                path="files/" + name + ".gz",
                install_path=install,
            )
        )

    def build(self, name="package", allow=False):
        recipe = self.root / "recipe.json"
        recipe.write_text(json.dumps(self.recipe))
        return build_release(recipe, self.root / name, reserve_bytes=0, allow_drafts=allow)

    def test_lossless_compressed_roundtrip_and_immutable_source(self):
        before = digest_file(self.source)
        result = self.build()
        self.assertEqual(result["verified"]["equivalent_representations"], 2)
        self.assertEqual(digest_file(self.source), before)
        with self.assertRaises(ValueError):
            self.build()
        blob = self.root / "package/files/full.gz"
        blob.write_bytes(blob.read_bytes() + b"corrupt")
        with self.assertRaises(ValueError):
            verify_release(self.root / "package", reserve_bytes=0)

    def test_wrong_snapshot_or_oversized_gzip_never_publishes(self):
        self.recipe["inventory"]["snapshot_id"] = "different"
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "package").exists())
        self.recipe["inventory"]["snapshot_id"] = Inventory(self.source).header["metadata"][
            "snapshot_id"
        ]
        zipped = self.root / "source.gz"
        with gzip.open(zipped, "wb") as f:
            f.write(self.source.read_bytes())
        self.recipe["artifacts"][0].update(
            source=str(zipped), source_encoding="gzip", expected_content_bytes=1
        )
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.root / "package").exists())

    def test_private_release_requires_opt_in(self):
        self.recipe["visibility"] = "private"
        with self.assertRaises(ValueError):
            self.build()
        self.build(allow=True)
        with self.assertRaises(ValueError):
            verify_release(self.root / "package", reserve_bytes=0)

    def test_wrong_producer_database_pair_rejected(self):
        path = self.root / "producer.sqlite3"
        with sqlite3.connect(path) as db:
            db.executescript(
                "CREATE TABLE meta(key,value); CREATE TABLE assets(url); CREATE TABLE pages(url);"
            )
            db.execute("INSERT INTO meta VALUES ('snapshot_id','wrong')")
            db.execute("INSERT INTO assets VALUES ('synthetic')")
        self.add("db", path, "inventory-db", "inventory-db", "inventory.sqlite3")
        with self.assertRaisesRegex(ValueError, "snapshot"):
            self.build()

    def test_same_claimed_origin_with_different_part_content_is_rejected(self):
        value = fixtures.inventory()
        value["tree"]["extension"] = "different retained information"
        altered = self.root / "altered.json"
        altered.write_text(json.dumps(value))
        variant = self.root / "variant"
        split_inventory(altered, variant, 1600, reserve_bytes=0)
        path = variant / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["source_sha256"] = digest_file(self.source)
        manifest["source_bytes"] = self.source.stat().st_size
        path.write_text(json.dumps(manifest))
        self.recipe["artifacts"] = self.recipe["artifacts"][:1]
        self.add("manifest", path, "inventory-parts", "inventory-manifest", "parts/manifest.json")
        for part in inventory_parts(variant):
            self.add(part.stem, part, "inventory-parts", "inventory-part", "parts/" + part.name)
        with self.assertRaisesRegex(ValueError, "semantically"):
            self.build()

    def test_public_bundle_cannot_hide_pending_documents(self):
        from explorer_ingest.export import SCHEMA

        path = self.root / "viewer.db"
        with sqlite3.connect(path) as db:
            db.executescript(SCHEMA)
            db.execute(
                "INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,category,content_tags,analysis_timestamp) VALUES ('test','x.txt','x','x','other','[]','now')"
            )
            db.execute(
                "INSERT INTO ingestion_documents VALUES ('test','doj','sha','key','pending','{}')"
            )
        self.add("viewer", path, "viewer", "viewer-db", "viewer/document_analysis.db")
        with self.assertRaisesRegex(ValueError, "unapproved"):
            self.build()


def inventory_parts(root):
    return sorted(root.glob("part-*.json"))
