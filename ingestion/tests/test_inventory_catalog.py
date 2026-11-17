import json
import os
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import test_archive as fixtures
from explorer_ingest.distribution_demo import demo_bundle
from explorer_ingest.distribution_release import verify_database
from explorer_ingest.inventory_catalog import build_catalog
from explorer_ingest.inventory_parts import split_inventory
from explorer_ingest.state import digest_file


class CatalogueTests(unittest.TestCase):
    def test_equivalent_parts_hierarchy_placements_and_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            source.write_text(json.dumps(fixtures.inventory()))
            original_sha = digest_file(source)
            split_inventory(source, root / "parts", 1600, reserve_bytes=0)
            whole = build_catalog(source, root / "whole.db", reserve_bytes=0)
            parts = build_catalog(root / "parts/manifest.json", root / "parts.db", True, 0)
            self.assertEqual(whole["signature"], parts["signature"])
            self.assertEqual(digest_file(source), original_sha)
            with sqlite3.connect(root / "parts.db") as db:
                self.assertEqual(
                    db.execute("SELECT count(*) FROM nodes WHERE asset_id='asset-1'").fetchone()[0],
                    2,
                )
                self.assertEqual(db.execute("SELECT count(*) FROM assets").fetchone()[0], 1)
                self.assertEqual(
                    db.execute(
                        "SELECT count(*) FROM assets_fts WHERE assets_fts MATCH 'Original'"
                    ).fetchone()[0],
                    1,
                )
                payload = json.loads(db.execute("SELECT payload FROM assets").fetchone()[0])
                self.assertEqual(len(payload["occurrences"]), 2)
            binding = dict(
                snapshot_id=parts["snapshot_id"],
                source_sha256=parts["source_sha256"],
                unique_assets=1,
                complete=True,
            )
            verify_database(root / "parts.db", "catalog-db", "public", binding)
            binding["source_sha256"] = "f" * 64
            with self.assertRaises(ValueError):
                verify_database(root / "parts.db", "catalog-db", "public", binding)
            with self.assertRaises(ValueError):
                build_catalog(source, root / "whole.db", reserve_bytes=0)
            bad = fixtures.inventory()
            bad["tree"]["children"][0]["children"][0]["asset_id"] = "absent"
            source.write_text(json.dumps(bad))
            with self.assertRaises(ValueError):
                build_catalog(source, root / "bad.db", reserve_bytes=0)
            self.assertFalse((root / "bad.db").exists())
            self.assertFalse(list(root.glob(".catalog-*")))

    def test_real_api_large_search_paging_no_writes_and_storage_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "viewer"
            demo_bundle(bundle, 0)
            source = root / "inventory.json"
            source.write_text(json.dumps(fixtures.inventory()))
            catalog = bundle / "inventory.sqlite3"
            build_catalog(source, catalog, reserve_bytes=0)
            with sqlite3.connect(catalog) as db:
                db.executemany(
                    "INSERT INTO assets VALUES (?,?,?,?,?,?)",
                    (
                        (f"s-{i}", f"https://example.invalid/{i}", "Ordinary", "pdf", 10, "{}")
                        for i in range(100001)
                    ),
                )
                db.execute(
                    "INSERT INTO assets VALUES ('rare','https://example.invalid/rare','Rare archive example','video',10,'{}')"
                )
                db.execute("INSERT INTO assets_fts(assets_fts) VALUES ('rebuild')")
            before = digest_file(catalog)
            checkout = Path(__file__).resolve().parents[2]
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            with (root / "api.log").open("w") as log:
                process = subprocess.Popen(
                    [str(checkout / "node_modules/.bin/tsx"), "api_server.ts"],
                    cwd=checkout,
                    env={
                        **os.environ,
                        "PORT": str(port),
                        "DB_PATH": str(bundle / "document_analysis.db"),
                        "TAG_CLUSTERS_PATH": str(bundle / "tag_clusters.json"),
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

                    for _ in range(150):
                        try:
                            read("/health")
                            break
                        except OSError:
                            if process.poll() is not None:
                                self.fail("API exited: " + (root / "api.log").read_text()[-2000:])
                            time.sleep(0.05)
                    self.assertTrue(read("/api/dataset")["synthetic"])
                    self.assertFalse(read("/api/dataset")["processing_complete"])
                    self.assertEqual(read("/api/archive/children")["nodes"][0]["name"], "DOJ")
                    children = read("/api/archive/children?parent=2&limit=1")
                    self.assertEqual(len(children["nodes"]), 1)
                    self.assertIsNotNone(children["next"])
                    self.assertEqual(
                        len(
                            read(
                                "/api/archive/children?parent=2&limit=1&after="
                                + str(children["next"])
                            )["nodes"]
                        ),
                        1,
                    )
                    rare = read("/api/archive/assets?q=Rare&kind=video&limit=1")
                    self.assertEqual(rare["assets"][0]["id"], "rare")
                    self.assertEqual(read("/api/archive/assets?q=Rare&kind=pdf")["assets"], [])
                    self.assertEqual(len(read("/api/archive/asset/asset-1")["placements"]), 2)
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        read("/api/archive/assets?limit=100000")
                    self.assertEqual(err.exception.code, 400)
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{port}/health",
                        headers={"Origin": "https://onrender.com.attacker.invalid"},
                    )
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(request, timeout=5)
                    self.assertEqual(digest_file(catalog), before)
                    catalog.rename(bundle / "detached.sqlite3")
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        read("/api/dataset")
                    self.assertEqual(err.exception.code, 503)
                finally:
                    process.terminate()
                    process.wait(timeout=15)
