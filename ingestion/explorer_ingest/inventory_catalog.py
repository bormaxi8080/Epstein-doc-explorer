"""Offline, bounded-memory search index of the complete immutable inventory."""

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .distribution_contracts import ExternalDirectory
from .inventory_parts import FLAGS, Inventory, fsync_directory


@contextmanager
def catalog_database(stage):
    db = sqlite3.connect(stage / "inventory.sqlite3")
    previous = db.execute("PRAGMA temp_store_directory").fetchone()
    try:
        db.execute("PRAGMA temp_store_directory='" + str(stage).replace("'", "''") + "'")
        if db.execute("PRAGMA temp_store_directory").fetchone() != (str(stage),):
            raise ValueError("SQLite did not accept external temporary storage")
        yield db
    finally:
        db.execute(
            "PRAGMA temp_store_directory='"
            + (previous[0] if previous else "").replace("'", "''")
            + "'"
        )
        db.close()


def build_catalog(source, output, manifest=False, reserve_bytes=1024**3):
    source = Inventory(source, manifest=manifest)
    output = Path(output).absolute()
    storage = ExternalDirectory(output.parent, reserve_bytes)
    if output.exists() or output.is_symlink():
        raise ValueError("Catalogue output must be new")
    stage = storage.mkdir(".catalog-" + uuid.uuid4().hex)
    try:
        with catalog_database(stage) as db:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("PRAGMA cache_size=-16384")
            db.execute("PRAGMA temp_store=FILE")
            db.set_progress_handler(lambda: storage.check() or 0, 100000)
            db.executescript("""
              CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE assets(id TEXT UNIQUE NOT NULL,url TEXT NOT NULL,name TEXT NOT NULL,
                kind TEXT NOT NULL,bytes INTEGER,payload TEXT NOT NULL);
              CREATE TABLE nodes(seq INTEGER PRIMARY KEY,parent INTEGER,node_id TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL,name TEXT NOT NULL,asset_id TEXT,payload TEXT NOT NULL);
              CREATE TABLE records(kind TEXT NOT NULL,payload TEXT NOT NULL);
            """)
            n = 0

            def accept(record):
                nonlocal n
                d = record["data"]
                payload = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
                if record["type"] == "asset":
                    url = d.get("download_url") or d["url"]
                    parsed = urlsplit(url)
                    if (
                        parsed.scheme not in ("http", "https")
                        or not parsed.hostname
                        or parsed.username
                        or parsed.password
                    ):
                        raise ValueError("Invalid source download URL")
                    db.execute(
                        "INSERT INTO assets VALUES (?,?,?,?,?,?)",
                        (
                            record["id"],
                            url,
                            d.get("original_name") or url.rsplit("/", 1)[-1],
                            d.get("file_kind") or "unknown",
                            d.get("size_bytes"),
                            payload,
                        ),
                    )
                elif record["type"] == "tree":
                    db.execute(
                        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                        (
                            record["seq"],
                            record["parent"],
                            d["id"],
                            d["kind"],
                            d["name"],
                            d.get("asset_id"),
                            payload,
                        ),
                    )
                else:
                    db.execute(
                        "INSERT INTO records VALUES (?,?)",
                        (record["type"], json.dumps(record, ensure_ascii=False)),
                    )
                n += 1
                if n % 5000 == 0:
                    storage.check()
                    db.commit()

            signature = source.scan(sink=accept, check=storage.check)
            db.executescript("""
              CREATE INDEX nodes_parent_seq ON nodes(parent,seq);
              CREATE INDEX nodes_asset ON nodes(asset_id);
              CREATE INDEX assets_kind ON assets(kind);
              CREATE VIRTUAL TABLE assets_fts USING fts5(name,url,content='assets',content_rowid='rowid');
              INSERT INTO assets_fts(assets_fts) VALUES ('rebuild');
              INSERT INTO assets_fts(assets_fts) VALUES ('integrity-check');
            """)
            if db.execute(
                "SELECT 1 FROM nodes n LEFT JOIN nodes p ON p.seq=n.parent WHERE n.parent IS NOT NULL AND p.seq IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("Missing inventory parent")
            if db.execute(
                "SELECT 1 FROM nodes n LEFT JOIN assets a ON a.id=n.asset_id WHERE n.asset_id IS NOT NULL AND a.id IS NULL LIMIT 1"
            ).fetchone():
                raise ValueError("Missing inventory asset")
            meta = dict(
                format="explorer-inventory-catalog",
                version=1,
                snapshot_id=source.header["metadata"]["snapshot_id"],
                source_sha256=source.sha256,
                complete=all(source.header["validation"].get(k) is True for k in FLAGS),
                validation=source.header["validation"],
                signature=signature,
            )
            db.executemany(
                "INSERT INTO meta VALUES (?,?)", ((k, json.dumps(v)) for k, v in meta.items())
            )
            db.commit()
            if db.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise ValueError("Catalogue integrity check failed")
        storage.check()
        with (stage / "inventory.sqlite3").open("rb") as stream:
            import os

            os.fsync(stream.fileno())
        (stage / "inventory.sqlite3").rename(output)
        fsync_directory(output.parent)
        return dict(output=str(output), **meta)
    finally:
        storage.guard()
        shutil.rmtree(stage)
