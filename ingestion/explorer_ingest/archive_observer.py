"""Read-only ledger observation and local preflight; no downloads or model calls."""

import fcntl
import importlib.util
import json
import math
import os
import shutil
import sqlite3
import time
from pathlib import Path

from .archive_catalog import CONTROL, Archive, StorageUnavailable, report
from .archive_contracts import read_artifact
from .state import readonly_db


class ArchiveView(Archive):
    def __init__(self, destination):
        requested = Path(destination)
        if not requested.is_absolute() or not requested.is_dir():
            raise StorageUnavailable("Destination must be an existing absolute directory")
        self.root = requested.resolve()
        if self.root.parts[:2] == ("/", "Volumes") and not os.path.ismount(
            Path(*self.root.parts[:3])
        ):
            raise StorageUnavailable("External volume is not mounted")
        stat = self.root.stat()
        self.identity = stat.st_dev, stat.st_ino
        self.control = self.path(CONTROL)
        self.marker = read_artifact(self.path(f"{CONTROL}/identity.json"), "identity")
        database = self.path(f"{CONTROL}/archive.sqlite3")
        stat = database.stat()
        self.database_identity = stat.st_dev, stat.st_ino
        self.db = readonly_db(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")

    def close(self):
        self.db.close()

    def writer_active(self):
        path = self.path(f"{CONTROL}/writer.lock")
        if not path.exists():
            return False
        with path.open("r") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return False


def snapshot(view, previous=None):
    # A short SQLite read transaction gives mutually consistent counters/meta.
    with view.db:
        view.db.execute("BEGIN")
        result = report(view)
        errors = [
            dict(row)
            for row in view.db.execute("""SELECT error,COUNT(*) count FROM assets
            WHERE status='failed' GROUP BY error ORDER BY count DESC LIMIT 20""")
        ]
    now = time.monotonic()
    transferred = result["downloaded_bytes"]
    progress = None
    path = view.path(f"{CONTROL}/progress.json")
    if path.exists():
        try:
            progress = read_artifact(path, "progress")
        except (ValueError, OSError, json.JSONDecodeError):
            progress = None
    active = view.writer_active()
    if active and progress and progress.get("phase") == "download" and "asset_id" in progress:
        row = view.db.execute(
            "SELECT status,expected_bytes FROM assets WHERE id=?", (progress["asset_id"],)
        ).fetchone()
        if row and row["status"] == "downloading":
            transferred += min(
                progress.get("bytes", 0), row["expected_bytes"] or progress.get("bytes", 0)
            )
    speed = None
    if previous and now > previous[0] and transferred >= previous[1]:
        speed = (transferred - previous[1]) / (now - previous[0])
    remaining = max(0, result["expected_bytes"] - transferred)
    result.update(
        writer_active=active,
        sampled_transferred_bytes=transferred,
        progress=progress,
        errors=errors,
        bytes_per_second=speed,
        remaining_known_bytes=remaining,
        eta_seconds=remaining / speed if speed and not result["unknown_sizes"] else None,
    )
    view.check()
    return result, (now, transferred)


def watch(view, interval=2, samples=0):
    if not math.isfinite(interval) or interval <= 0 or samples < 0:
        raise ValueError("Watch interval must be positive; samples must be nonnegative")
    previous, count = None, 0
    while True:
        result, previous = snapshot(view, previous)
        yield result
        count += 1
        if samples and count >= samples:
            return
        time.sleep(interval)


def doctor(destination, reserve_bytes=1024**3, write_probe=False):
    root = Path(destination)
    if not root.is_absolute() or not root.is_dir():
        raise StorageUnavailable("Destination must be an existing absolute directory")
    root = root.resolve()
    if root.parts[:2] == ("/", "Volumes") and not os.path.ismount(Path(*root.parts[:3])):
        raise StorageUnavailable("External volume is not mounted")
    usage = shutil.disk_usage(root)
    result = {
        "format_version": 1,
        "free_bytes": usage.free,
        "reserve_bytes": reserve_bytes,
        "reserve_available": usage.free >= reserve_bytes,
        "dependencies": {
            name: importlib.util.find_spec(name) is not None
            for name in ("ijson", "jsonschema", "pypdf", "certifi")
        },
        "ledger_present": (root / CONTROL / "archive.sqlite3").exists(),
        "write_probe": "not_requested",
        "network_requests": 0,
    }
    if result["ledger_present"]:
        view = ArchiveView(root)
        try:
            result["sqlite_quick_check"] = view.db.execute("PRAGMA quick_check").fetchone()[0]
            result["writer_active"] = view.writer_active()
            view.check()
        finally:
            view.close()
    from .transport import tls_context

    tls_context()
    result["tls_ca_context"] = "verified"
    if write_probe:
        import tempfile

        from .archive_catalog import sync_directory

        with tempfile.TemporaryDirectory(prefix=".pw010-probe-", dir=root) as directory:
            a = Path(directory) / "Probe-é"
            b = Path(directory) / "published-é"
            with a.open("xb") as output:
                output.write(b"local probe\n")
                output.flush()
                os.fsync(output.fileno())
            a.rename(b)
            sync_directory(b.parent)
            result["case_sensitive"] = not b.with_name("PUBLISHED-é").exists()
            result["unicode_roundtrip"] = b.read_bytes() == b"local probe\n"
        result["write_probe"] = "passed"
    result["ok"] = bool(
        result["reserve_available"]
        and all(result["dependencies"].values())
        and result.get("sqlite_quick_check", "ok") == "ok"
    )
    return result
