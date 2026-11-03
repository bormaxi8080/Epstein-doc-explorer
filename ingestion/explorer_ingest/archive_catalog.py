"""Disk-backed PW-010 download plans; no Oriol runtime dependency or HTTP calls."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import ijson
from jsonschema import Draft202012Validator

from .archive_contracts import read_artifact, validate_artifact
from .state import atomic_json as state_atomic_json
from .state import digest_file

CONTROL = ".pw010-download"
FLAGS = ("schema_valid", "structure_complete", "metadata_complete", "source_stable", "complete")


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            os.fsync(descriptor)
        except OSError as exc:
            if exc.errno not in (22, 45, 95):
                raise
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    state_atomic_json(path, value)
    sync_directory(Path(path).parent)


def artifact_json(path, kind, value):
    validate_artifact(kind, value)
    atomic_json(path, value)


def component(value):
    value = str(value)
    suffix = hashlib.sha256(value.encode()).hexdigest()[:16]
    return (quote(value, safe="-_.")[:64].strip(".") or "unnamed") + "--" + suffix


def original_path(asset_id, asset):
    parsed = urlsplit(asset["url"])
    pieces = [part for part in parsed.path.split("/") if part]
    name = asset["original_name"] or (unquote(pieces[-1]) if pieces else "document")
    extension = Path(name).suffix[:16]
    if not re.fullmatch(r"\.[A-Za-z0-9]{1,12}", extension):
        extension = ""
    stem = name[: -len(extension)] if extension else name
    filename = (
        component(stem) + "--" + hashlib.sha256(asset_id.encode()).hexdigest()[:16] + extension
    )
    return str(
        Path(
            "archive-files",
            component(parsed.netloc),
            *(component(unquote(p)) for p in pieces[:-1]),
            filename,
        )
    )


class StorageUnavailable(ValueError):
    """The selected filesystem changed or disappeared; never create a fallback."""


class Archive:
    def __init__(self, destination, create=False):
        requested = Path(destination)
        if not requested.is_absolute() or not requested.is_dir():
            raise StorageUnavailable("Destination must be an existing absolute directory")
        self.root = requested.resolve()
        if self.root.parts[:2] == ("/", "Volumes"):
            mount = Path(*self.root.parts[:3])
            if not os.path.ismount(mount):
                raise StorageUnavailable("External volume is not mounted")
        stat = self.root.stat()
        self.identity = (stat.st_dev, stat.st_ino)
        self.control = self.path(CONTROL)
        if not create and not (self.control / "identity.json").is_file():
            raise ValueError("No download plan; run plan first")
        if create and not self.control.exists():
            if (self.root / "archive-files").exists() or (self.root / "catalog").exists():
                raise ValueError("Destination contains unmanaged archive directories")
            self.control.mkdir()
        self.lock = self.path(f"{CONTROL}/writer.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            raise ValueError("Another downloader owns this destination") from exc
        try:
            marker = self.path(f"{CONTROL}/identity.json")
            if not marker.exists():
                if not create or (self.control / "archive.sqlite3").exists():
                    raise ValueError("Destination identity is missing")
                artifact_json(marker, "identity", {"format_version": 1, "uuid": uuid.uuid4().hex})
            self.marker = read_artifact(marker, "identity")
            if self.marker.get("format_version") != 1:
                raise ValueError("Unsupported archive layout")
            database = self.path(f"{CONTROL}/archive.sqlite3")
            if not create and not database.exists():
                raise ValueError("Download ledger is missing")
            self.db = sqlite3.connect(database)
            self.db.row_factory = sqlite3.Row
            self.db.executescript("""
              PRAGMA journal_mode=DELETE;
              PRAGMA synchronous=FULL;
              PRAGMA foreign_keys=ON;
              CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS assets(
                id TEXT PRIMARY KEY, canonical_url TEXT UNIQUE NOT NULL, metadata TEXT NOT NULL, expected_bytes INTEGER,
                relative_path TEXT NOT NULL UNIQUE COLLATE NOCASE,
                status TEXT NOT NULL DEFAULT 'pending', sha256 TEXT, actual_bytes INTEGER,
                validation TEXT, receipt TEXT, error TEXT, attempts INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS nodes(
                seq INTEGER PRIMARY KEY, parent INTEGER, node_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, kind TEXT NOT NULL, asset_id TEXT, page_id TEXT,
                FOREIGN KEY(parent) REFERENCES nodes(seq) DEFERRABLE INITIALLY DEFERRED);
              CREATE TABLE IF NOT EXISTS pages(id TEXT PRIMARY KEY,url TEXT UNIQUE NOT NULL);
              CREATE TABLE IF NOT EXISTS occurrences(
                asset_id TEXT NOT NULL, node_id TEXT NOT NULL UNIQUE, page_id TEXT, page_url TEXT);
              CREATE TABLE IF NOT EXISTS placements(
                node_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id),
                relative_path TEXT NOT NULL UNIQUE COLLATE NOCASE);
              CREATE TABLE IF NOT EXISTS storage_assets(asset_id TEXT PRIMARY KEY);
              CREATE INDEX IF NOT EXISTS idx_placements_asset ON placements(asset_id);
              CREATE INDEX IF NOT EXISTS idx_nodes_parent ON nodes(parent);
              CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status,actual_bytes,expected_bytes);
            """)
            stat = database.stat()
            self.database_identity = (stat.st_dev, stat.st_ino)
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self.lock.close()
            raise

    def close(self):
        self.db.close()
        self.lock.close()

    def check(self):
        try:
            stat = self.root.stat()
            if (stat.st_dev, stat.st_ino) != self.identity:
                raise StorageUnavailable("Destination filesystem identity changed")
            marker = json.loads(self.path(f"{CONTROL}/identity.json").read_text())
            stat = self.path(f"{CONTROL}/archive.sqlite3").stat()
            if marker != self.marker or (stat.st_dev, stat.st_ino) != self.database_identity:
                raise StorageUnavailable("Download ledger or destination marker changed")
        except OSError as exc:
            raise StorageUnavailable("Destination is unavailable") from exc

    def path(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe archive path")
        candidate = self.root / relative
        for part in [candidate, *candidate.parents]:
            if part == self.root:
                break
            if part.is_symlink():
                raise ValueError("Symlinks are not allowed inside archive storage")
        return candidate

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def make_parent(self, path):
        """Create descendants only; never recreate the selected destination itself."""
        relative = Path(path).relative_to(self.root)
        current = self.root
        for part in relative.parent.parts:
            self.check()
            current = current / part
            self.path(current.relative_to(self.root))
            current.mkdir(exist_ok=True)

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def ready(self):
        self.check()
        if not self.meta("plan_complete"):
            raise ValueError("Plan has not completed validation")


def read_item(path, prefix):
    with Path(path).open("rb") as stream:
        return next(ijson.items(stream, prefix, use_float=True), None)


def evidence_receipt(path, metadata):
    path = Path(path).resolve(strict=True)
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("Use a closed SQLite backup, not a live WAL checkpoint")
    before = digest_file(path)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        db.execute("PRAGMA query_only=ON")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Evidence database failed SQLite quick_check")
        row = db.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone()
        if (
            not metadata.get("snapshot_id")
            or not row
            or json.loads(row[0]) != metadata["snapshot_id"]
        ):
            raise ValueError("Evidence database and JSON belong to different snapshots")
    if digest_file(path) != before:
        raise ValueError("Evidence database changed while being inspected")
    return {
        "path": str(path),
        "sha256": before,
        "snapshot_id": metadata["snapshot_id"],
        "role": "snapshot_identity_evidence_only",
    }


def stream_nodes(archive, source, validator, root="tree"):
    stack, sequence, keys = [], 0, set()
    with source.open("rb") as stream:
        for prefix, event, value in ijson.parse(stream, use_float=True):
            if prefix == "" and event == "map_key":
                if value in keys:
                    raise ValueError("Duplicate root JSON key")
                keys.add(value)
            if prefix != root and not prefix.startswith(root + "."):
                continue
            if event == "start_map" and (prefix == root or prefix.endswith(".children.item")):
                sequence += 1
                if len(stack) >= 64:
                    raise ValueError("Source hierarchy exceeds supported depth")
                stack.append(
                    {
                        "seq": sequence,
                        "parent": stack[-1]["seq"] if stack else None,
                        "prefix": prefix,
                        "data": {},
                    }
                )
            elif stack and event in ("string", "number", "boolean", "null"):
                frame = stack[-1]
                key = prefix[len(frame["prefix"]) + 1 :]
                if "." not in key:
                    frame["data"][key] = value
            elif stack and event == "start_array" and prefix == stack[-1]["prefix"] + ".children":
                stack[-1]["data"]["children"] = []
            elif stack and event == "end_map" and prefix == stack[-1]["prefix"]:
                frame = stack.pop()
                node = frame["data"]
                validator.validate(node)
                if root == "tree":
                    archive.db.execute(
                        "INSERT INTO nodes VALUES (?,?,?,?,?,?,?)",
                        (
                            frame["seq"],
                            frame["parent"],
                            node["id"],
                            node["name"],
                            node["kind"],
                            node.get("asset_id"),
                            node.get("page_id"),
                        ),
                    )
                elif node["kind"] == "file":
                    archive.db.execute("INSERT INTO storage_assets VALUES (?)", (node["asset_id"],))
                if sequence % 5000 == 0:
                    archive.check()
    return keys


def plan(archive, source, evidence_db=None, allow_partial=False):
    archive.check()
    source = Path(source).resolve(strict=True)
    sha = digest_file(source)
    if archive.meta("source_sha256") and archive.meta("source_sha256") != sha:
        raise ValueError("A destination belongs to one immutable dump; select a new directory")
    inputs = archive.path(f"{CONTROL}/inputs")
    inputs.mkdir(exist_ok=True)
    saved = inputs / (sha + ".json")
    if not saved.exists():
        temporary = saved.with_suffix(".tmp")
        shutil.copyfile(source, temporary)
        with temporary.open("rb") as copied:
            os.fsync(copied.fileno())
        if digest_file(temporary) != sha:
            raise ValueError("Source JSON changed during snapshot copy")
        temporary.replace(saved)
        sync_directory(saved.parent)
    elif digest_file(saved) != sha:
        raise ValueError("Stored JSON checksum mismatch")
    version = read_item(saved, "schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Only PW-010 JSON schema v1/v2 is supported")
    schema = json.loads(Path(__file__).with_name(f"pw010-v{version}.schema.json").read_text())

    @lru_cache(maxsize=None)
    def validator(name):
        return Draft202012Validator({"$defs": schema["$defs"], "$ref": f"#/$defs/{name}"})

    metadata, checks = read_item(saved, "metadata"), read_item(saved, "validation")
    for key, value in (("metadata", metadata), ("validation", checks)):
        Draft202012Validator(schema["properties"][key]).validate(value)
    certified = all(checks[key] is True for key in FLAGS)
    if not certified and not allow_partial:
        raise ValueError("Partial inventory: only explicit --allow-partial permits a pilot plan")
    evidence = evidence_receipt(evidence_db, metadata) if evidence_db else None
    if archive.meta("plan_complete"):
        previous_evidence = archive.meta("evidence") or {}
        if evidence and any(
            evidence[key] != previous_evidence.get(key) for key in ("sha256", "snapshot_id")
        ):
            raise ValueError("Plan already bound; use the same evidence input")
        return report(archive)
    with archive.db:
        archive.db.execute("DELETE FROM occurrences")
        archive.db.execute("DELETE FROM placements")
        archive.db.execute("DELETE FROM assets")
        archive.db.execute("DELETE FROM nodes")
        archive.db.execute("DELETE FROM pages")
        archive.db.execute("DELETE FROM storage_assets")
        keys = stream_nodes(archive, saved, validator("node"))
        if set(schema["required"]) - keys or keys - set(schema["properties"]):
            raise ValueError("Missing or unsupported top-level inventory fields")
        if version == 1:
            stream_nodes(archive, saved, validator("node"), root="storage_tree")
        count = known = unknown = placements = pages = issues = checksum_count = 0
        with saved.open("rb") as stream:
            for page in ijson.items(stream, "pages.item", use_float=True):
                validator("page").validate(page)
                if certified and (
                    page["state"] != "done"
                    or not page["verified_hash"]
                    or page["verified_hash"] != page["structure_hash"]
                ):
                    raise ValueError("Certified inventory contains an unverified page")
                archive.db.execute("INSERT INTO pages VALUES (?,?)", (page["page_id"], page["url"]))
                pages += 1
        with saved.open("rb") as stream:
            for asset_id, asset in ijson.kvitems(stream, "assets", use_float=True):
                validator("asset").validate(asset)
                if asset.get("asset_id", asset_id) != asset_id:
                    raise ValueError("Asset key and embedded identity disagree")
                parsed = urlsplit(asset["url"])
                if (
                    parsed.scheme not in ("http", "https")
                    or not parsed.hostname
                    or parsed.username
                    or parsed.password
                ):
                    raise ValueError("Source URL must be HTTP(S) without embedded credentials")
                if certified and (asset["size_bytes"] is None or asset["status"] != "verified"):
                    raise ValueError("Certified asset is missing verified metadata")
                expected_hash = asset.get("sha256")
                if expected_hash is not None and (
                    not isinstance(expected_hash, str)
                    or len(expected_hash) != 64
                    or any(c not in "0123456789abcdef" for c in expected_hash)
                ):
                    raise ValueError("Invalid optional source SHA-256")
                checksum_count += expected_hash is not None
                archive.db.execute(
                    "INSERT INTO assets(id,canonical_url,metadata,expected_bytes,relative_path) VALUES (?,?,?,?,?)",
                    (
                        asset_id,
                        asset["url"],
                        json.dumps(asset),
                        asset["size_bytes"],
                        original_path(asset_id, asset),
                    ),
                )
                for occurrence in asset["occurrences"]:
                    archive.db.execute(
                        "INSERT INTO occurrences VALUES (?,?,?,?)",
                        (
                            asset_id,
                            occurrence["node_id"],
                            occurrence.get("page_id"),
                            occurrence.get("page_url"),
                        ),
                    )
                    placements += 1
                count += 1
                unknown += asset["size_bytes"] is None
                known += asset["size_bytes"] or 0
                if count % 5000 == 0:
                    archive.check()
        with saved.open("rb") as stream:
            for issue in ijson.items(stream, "issues.item"):
                Draft202012Validator(schema["properties"]["issues"]["items"]).validate(issue)
                issues += 1
        if certified and (not count or issues):
            raise ValueError("Certified inventory is empty or reports issues")
        measured = {
            "unique_files_count": count,
            "known_unique_size_bytes": known,
            "unknown_sizes_count": unknown,
            "file_placements_count": placements,
            "pages_count": pages,
            "issues_count": issues,
        }
        if any(checks[key] != value for key, value in measured.items()):
            raise ValueError("Inventory counters do not match its registries")
        if (
            certified
            and archive.db.execute("SELECT 1 FROM nodes WHERE kind='unresolved' LIMIT 1").fetchone()
        ):
            raise ValueError("Certified inventory contains unresolved hierarchy entries")
        page_join = "o.page_id=p.id" if version == 2 else "o.page_url=p.url"
        bad = archive.db.execute(f"""SELECT 1 FROM occurrences o LEFT JOIN nodes n ON o.node_id=n.node_id
            LEFT JOIN pages p ON {page_join}
            WHERE n.kind!='file' OR n.asset_id!=o.asset_id OR n.seq IS NULL OR p.id IS NULL LIMIT 1""").fetchone()
        missing = archive.db.execute("""SELECT 1 FROM nodes n LEFT JOIN occurrences o ON n.node_id=o.node_id
            WHERE n.kind='file' AND o.node_id IS NULL LIMIT 1""").fetchone()
        dangling = archive.db.execute("""SELECT 1 FROM nodes n LEFT JOIN pages p ON n.page_id=p.id
            WHERE n.kind='reference' AND p.id IS NULL LIMIT 1""").fetchone()
        if bad or missing or dangling:
            raise ValueError("Invalid asset/page/placement references in source hierarchy")
        if version == 1 and (
            archive.db.execute(
                "SELECT 1 FROM assets WHERE id NOT IN (SELECT asset_id FROM storage_assets) LIMIT 1"
            ).fetchone()
            or archive.db.execute(
                "SELECT 1 FROM storage_assets WHERE asset_id NOT IN (SELECT id FROM assets) LIMIT 1"
            ).fetchone()
        ):
            raise ValueError("Legacy storage tree does not match the asset registry")

        @lru_cache(maxsize=8192)
        def directory_path(sequence):
            if sequence is None:
                return "catalog"
            row = archive.db.execute(
                "SELECT parent,node_id,name FROM nodes WHERE seq=?", (sequence,)
            ).fetchone()
            return str(
                Path(directory_path(row["parent"]), component(row["name"] + "_" + row["node_id"]))
            )

        for index, node in enumerate(
            archive.db.execute(
                "SELECT seq,parent,node_id,name,asset_id FROM nodes WHERE kind='file'"
            )
        ):
            relative = str(
                Path(
                    directory_path(node["parent"]),
                    component(node["name"] + "_" + node["node_id"]) + ".ref.json",
                )
            )
            archive.db.execute(
                "INSERT INTO placements VALUES (?,?,?)",
                (node["node_id"], node["asset_id"], relative),
            )
            if index % 5000 == 0:
                archive.check()
        archive.set_meta("source_sha256", sha)
        archive.set_meta("source_metadata", metadata)
        archive.set_meta("source_validation", checks)
        archive.set_meta("source_certified", certified)
        archive.set_meta("evidence", evidence)
        archive.set_meta("plan_complete", True)
        archive.set_meta("schema_version", version)
        archive.set_meta("source_checksums_count", checksum_count)
    return report(archive)


def report(archive):
    archive.ready()
    totals = dict(
        archive.db.execute(
            "SELECT COUNT(*) total,COALESCE(SUM(expected_bytes),0) expected_bytes, COALESCE(SUM(expected_bytes IS NULL),0) unknown_sizes, COALESCE(SUM(CASE WHEN status='downloaded' THEN actual_bytes ELSE 0 END),0) downloaded_bytes FROM assets"
        ).fetchone()
    )
    statuses = {
        row[0]: row[1]
        for row in archive.db.execute("SELECT status,COUNT(*) FROM assets GROUP BY status")
    }
    result = {
        "format_version": 1,
        "source_sha256": archive.meta("source_sha256"),
        "source_inventory_complete": archive.meta("source_certified"),
        **totals,
        "statuses": statuses,
        "download_complete": statuses.get("downloaded", 0) == totals["total"]
        and totals["total"] > 0,
        "last_full_audit": archive.meta("last_full_audit"),
        "source_checksums_count": archive.meta("source_checksums_count", 0),
    }
    audit = result["last_full_audit"] or {}
    result["complete_at_last_audit"] = bool(
        result["source_inventory_complete"] and result["download_complete"] and audit.get("passed")
    )
    return validate_artifact("report", result)
