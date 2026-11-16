"""Disk-backed source/document/page registry; originals stay in the downloader."""

import json
from contextlib import closing, contextmanager
from pathlib import Path

from .archive_catalog import CONTROL
from .archive_observer import ArchiveView
from .corpus_contracts import cache_key, document_id, fingerprint
from .corpus_queue import JobQueue
from .corpus_sources import selected_sources
from .corpus_storage import CorpusStorage
from .inventory_parts import guard_directory
from .state import digest_file


class Corpus:
    def __init__(self, root, create=False, limits=None):
        self.storage = CorpusStorage(root, create=create, limits=limits)
        path = self.storage.path("corpus.sqlite3")
        if not create and not path.exists():
            raise ValueError("Corpus queue is missing; initialize explicitly")
        with self.storage.scratch_environment():
            self.queue = JobQueue(path)
            self.storage.bind_queue()
            self.db = self.queue.db
            self.db.executescript("""
              CREATE TABLE IF NOT EXISTS selections(
                id TEXT PRIMARY KEY, header TEXT NOT NULL, state TEXT NOT NULL,
                imported INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS corpus_documents(
                doc_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
                format TEXT, status TEXT NOT NULL DEFAULT 'registered', page_count INTEGER,
                text_ref TEXT, text_sha256 TEXT, analysis_ref TEXT, approved_key TEXT);
              CREATE TABLE IF NOT EXISTS corpus_sources(
                id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES corpus_documents(doc_id),
                archive_root TEXT NOT NULL, archive_uuid TEXT NOT NULL, relative_path TEXT NOT NULL,
                metadata TEXT NOT NULL, receipt TEXT NOT NULL, source_sha256 TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS corpus_sources_doc ON corpus_sources(doc_id);
              CREATE TABLE IF NOT EXISTS selection_members(
                selection_id TEXT NOT NULL REFERENCES selections(id),
                source_id TEXT NOT NULL REFERENCES corpus_sources(id),
                PRIMARY KEY(selection_id,source_id));
              CREATE TABLE IF NOT EXISTS corpus_pages(
                doc_id TEXT NOT NULL REFERENCES corpus_documents(doc_id), page INTEGER NOT NULL,
                native_ref TEXT, ocr_ref TEXT, selected_ref TEXT, status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY(doc_id,page));
              CREATE INDEX IF NOT EXISTS corpus_documents_status ON corpus_documents(status);
          CREATE INDEX IF NOT EXISTS corpus_pages_status ON corpus_pages(status);
          CREATE TABLE IF NOT EXISTS corpus_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            """)
        self.source_cache = {}

    def close(self):
        self.queue.close()

    def import_selection(self, selection):
        self.storage.check()
        selection = Path(selection).resolve(strict=True)
        identity = digest_file(selection)
        header = json.loads(selection.read_text())
        old = self.db.execute("SELECT * FROM selections WHERE id=?", (identity,)).fetchone()
        if old and old["state"] == "complete":
            return {"selection_id": identity, "files": old["imported"]}
        count = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO selections(id,header,state) VALUES (?,?,'importing')",
                (identity, json.dumps(header)),
            )
            for _, record in selected_sources(selection):
                sha = record["sha256"]
                doc_id = document_id(sha)
                self.db.execute(
                    "INSERT OR IGNORE INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                    (doc_id, sha, record["bytes"]),
                )
                row = self.document(doc_id)
                if row["sha256"] != sha or row["bytes"] != record["bytes"]:
                    raise ValueError("Physical document identity collision")
                source_id = fingerprint([header["source_sha256"], record["asset_id"]])
                values = (
                    source_id,
                    doc_id,
                    header["archive_root"],
                    header["archive_uuid"],
                    record["relative_path"],
                    json.dumps(record["metadata"], sort_keys=True),
                    json.dumps(record["receipt_data"], sort_keys=True),
                    header["source_sha256"],
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO corpus_sources VALUES (?,?,?,?,?,?,?,?)", values
                )
                actual = self.db.execute(
                    "SELECT * FROM corpus_sources WHERE id=?", (source_id,)
                ).fetchone()
                if tuple(actual) != values:
                    raise ValueError("Source identity already has different immutable inputs")
                self.db.execute(
                    "INSERT OR IGNORE INTO selection_members VALUES (?,?)", (identity, source_id)
                )
                count += 1
                if count % 5000 == 0:
                    self.storage.check()
                    self.db.execute(
                        "UPDATE selections SET imported=? WHERE id=?", (count, identity)
                    )
                    self.db.commit()
                    self.db.execute("BEGIN IMMEDIATE")
            if digest_file(selection) != identity or count != header["files_count"]:
                raise ValueError("Source selection changed during import")
            self.db.execute(
                "UPDATE selections SET state='complete',imported=? WHERE id=?", (count, identity)
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return {"selection_id": identity, "files": count}

    def document(self, doc_id):
        row = self.db.execute("SELECT * FROM corpus_documents WHERE doc_id=?", (doc_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown corpus document")
        return row

    def plan_inspection(self, selection_id):
        selected = self.db.execute(
            "SELECT state FROM selections WHERE id=?", (selection_id,)
        ).fetchone()
        if not selected or selected[0] != "complete":
            raise ValueError("Only completely imported selections can be processed")
        count = 0
        self.db.execute("BEGIN IMMEDIATE")
        try:
            for doc in self.db.execute(
                """SELECT d.* FROM corpus_documents d WHERE EXISTS (
                SELECT 1 FROM corpus_sources s JOIN selection_members m ON m.source_id=s.id
                WHERE s.doc_id=d.doc_id AND m.selection_id=?)""",
                (selection_id,),
            ):
                key = cache_key("inspect", doc["sha256"], {"inspector": 2})
                self.queue.enqueue(key, "inspect", {"doc_id": doc["doc_id"]})
                count += 1
                if count % 5000 == 0:
                    self.storage.check()
                    self.db.commit()
                    self.db.execute("BEGIN IMMEDIATE")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        return count

    @contextmanager
    def original(self, doc_id):
        self.storage.check()
        doc = self.document(doc_id)
        row = self.db.execute(
            "SELECT * FROM corpus_sources WHERE doc_id=? ORDER BY id LIMIT 1", (doc_id,)
        ).fetchone()
        if not row:
            exists = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='corpus_members'"
            ).fetchone()
            member = (
                self.db.execute(
                    "SELECT * FROM corpus_members WHERE doc_id=? ORDER BY parent_id,name LIMIT 1",
                    (doc_id,),
                ).fetchone()
                if exists
                else None
            )
            if not member:
                raise ValueError("Document has no frozen archive source")
            path = self.storage.path(member["path"])
            if digest_file(path) != doc["sha256"]:
                raise ValueError("Container member checksum mismatch")
            before = path.stat()
            yield path
            self.storage.check()
            after = path.stat()
            if (before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ValueError("Container member changed")
            return
        check = guard_directory(row["archive_root"])
        with closing(ArchiveView(row["archive_root"])) as archive:
            if archive.marker["uuid"] != row["archive_uuid"]:
                raise ValueError("Original archive identity changed")
            path = archive.path(row["relative_path"])
        stat = path.stat()
        stamp = stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        if stat.st_size != doc["bytes"]:
            raise ValueError("Original size changed")
        if self.source_cache.get(str(path)) != (doc["sha256"], stamp):
            if digest_file(path) != doc["sha256"]:
                raise ValueError("Original checksum mismatch")
            if len(self.source_cache) >= 8:
                self.source_cache.clear()
            self.source_cache[str(path)] = doc["sha256"], stamp
        yield path
        check()
        marker = json.loads((Path(row["archive_root"]) / CONTROL / "identity.json").read_text())
        after = path.stat()
        if (
            marker["uuid"] != row["archive_uuid"]
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != stamp
        ):
            raise ValueError("Original changed during processing")

    def inspected(self, doc_id, info):
        doc = self.document(doc_id)
        count = info.get("pages", 0)
        if type(count) is not int or count < 0 or count > 1_000_000:
            raise ValueError("Invalid page count")
        if doc["page_count"] is not None and doc["page_count"] != count:
            raise ValueError("Reinspection changed the physical page count")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "UPDATE corpus_documents SET format=?,status=CASE WHEN text_ref IS NULL THEN ? ELSE status END,page_count=? WHERE doc_id=?",
                (info["format"], info["status"], count, doc_id),
            )
            self.db.executemany(
                "INSERT OR IGNORE INTO corpus_pages(doc_id,page) VALUES (?,?)",
                ((doc_id, p) for p in range(1, count + 1)),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def status(self):
        return {
            "documents": dict(
                self.db.execute("SELECT status,COUNT(*) FROM corpus_documents GROUP BY status")
            ),
            "pages": dict(
                self.db.execute("SELECT status,COUNT(*) FROM corpus_pages GROUP BY status")
            ),
            "queue": self.queue.status(),
        }
