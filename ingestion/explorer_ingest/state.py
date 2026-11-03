"""Persistent ingestion ledger. Raw data belongs outside the checkout."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote


def digest_file(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def readonly_db(path):
    return sqlite3.connect("file:" + quote(str(Path(path).resolve())) + "?mode=ro", uri=True)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "ingestion.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA foreign_keys=ON;
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS inventories (
            id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
            complete INTEGER NOT NULL, metadata TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY, inventory_id TEXT NOT NULL REFERENCES inventories(id),
            original_id TEXT NOT NULL, metadata TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'discovered', sha256 TEXT, path TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
            member_path TEXT NOT NULL, sha256 TEXT NOT NULL, original_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'extracted', pages_json TEXT,
            analysis_json TEXT, analysis_key TEXT, approved_key TEXT, error TEXT);
          CREATE TABLE IF NOT EXISTS analyses (
            cache_key TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(doc_id),
            response_path TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS document_sources (
            doc_id TEXT NOT NULL REFERENCES documents(doc_id),
            asset_id TEXT NOT NULL REFERENCES assets(id), member_path TEXT NOT NULL,
            PRIMARY KEY(doc_id,asset_id,member_path));
          CREATE TABLE IF NOT EXISTS archive_imports (
            inventory_id TEXT PRIMARY KEY REFERENCES inventories(id), archive_root TEXT NOT NULL,
            archive_uuid TEXT NOT NULL, manifest_sha256 TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS archive_links (
            asset_id TEXT PRIMARY KEY REFERENCES assets(id), source_id TEXT NOT NULL,
            relative_path TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS extracted_assets (
            asset_id TEXT PRIMARY KEY REFERENCES assets(id), version TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    @contextmanager
    def writer(self):
        # A process-level lease covers file writes as well as SQLite transactions.
        import fcntl

        with (self.root / "writer.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Another ingestion writer is active") from exc
            try:
                yield self
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def relative(self, path):
        return str(Path(path).resolve().relative_to(self.root))

    def path(self, relative):
        candidate = (self.root / relative).resolve()
        candidate.relative_to(self.root)
        return candidate

    def status(self):
        return {
            table: [
                dict(row)
                for row in self.db.execute(
                    f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status"
                )
            ]
            for table in ("assets", "documents")
        }
