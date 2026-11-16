"""Read-only baseline contracts and explicit verified Git LFS materialization."""

import hashlib
import os
import re
import shutil
import urllib.request
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from .inventory_parts import guard_directory
from .state import digest_file, readonly_db
from .transport import tls_context

REQUIRED = {
    "documents": {
        "doc_id",
        "file_path",
        "one_sentence_summary",
        "paragraph_summary",
        "category",
        "content_tags",
        "full_text",
        "date_range_earliest",
        "date_range_latest",
    },
    "rdf_triples": {"id", "doc_id", "actor", "action", "target", "timestamp", "triple_tags"},
}


def lfs_identity(pointer):
    text = Path(pointer).read_text()
    match = re.fullmatch(
        r"version https://git-lfs.github.com/spec/v1\noid sha256:([a-f0-9]{64})\nsize ([0-9]+)\n?",
        text,
    )
    if not match:
        raise ValueError("Expected a Git LFS pointer")
    return match[1], int(match[2])


def inspect_baseline(path, expected_sha256=None):
    path = Path(path).resolve(strict=True)
    with path.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Baseline is not SQLite; materialize its Git LFS object")
    if any(Path(str(path) + s).exists() for s in ("-wal", "-shm", "-journal")):
        raise ValueError("Use a closed standalone baseline")
    sha = digest_file(path)
    if expected_sha256 and sha != expected_sha256:
        raise ValueError("Baseline SHA-256 mismatch")
    with closing(readonly_db(path)) as db:
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Baseline SQLite integrity failed")
        columns = {}
        for table, required in REQUIRED.items():
            columns[table] = [r[1] for r in db.execute("PRAGMA table_info(" + table + ")")]
            if required - set(columns[table]):
                raise ValueError("Baseline is missing Explorer columns: " + table)
        counts = {
            table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in REQUIRED
        }
    if digest_file(path) != sha:
        raise ValueError("Baseline changed during inspection")
    return {"sha256": sha, "bytes": path.stat().st_size, "columns": columns, "counts": counts}


def materialize(pointer, destination, url):
    expected, size = lfs_identity(pointer)
    destination = Path(destination).absolute()
    check = guard_directory(destination.parent)
    if destination.exists():
        return inspect_baseline(destination, expected)
    if destination.is_symlink():
        raise ValueError("Baseline symlinks are forbidden")
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "media.githubusercontent.com":
        raise ValueError("Baseline materialization requires the GitHub media HTTPS host")
    temporary = destination.with_suffix(".download")
    if temporary.is_symlink():
        raise ValueError("Baseline symlinks are forbidden")
    if temporary.exists():
        raise ValueError("An unfinished baseline download already exists")
    if shutil.disk_usage(destination.parent).free - size < 1024**3:
        raise ValueError("Baseline filesystem reserve is unavailable")
    digest, total = hashlib.sha256(), 0
    created = False
    try:
        with (
            urllib.request.urlopen(url, timeout=60, context=tls_context()) as source,
            temporary.open("xb") as output,
        ):
            created = True
            if urlsplit(source.url).scheme != "https":
                raise ValueError("Baseline redirect downgraded HTTPS")
            while block := source.read(1024 * 1024):
                check()
                total += len(block)
                if total > size:
                    raise ValueError("Baseline exceeds the LFS byte size")
                output.write(block)
                digest.update(block)
            output.flush()
            os.fsync(output.fileno())
        if total != size or digest.hexdigest() != expected:
            raise ValueError("Downloaded baseline differs from the pinned Git LFS identity")
        result = inspect_baseline(temporary, expected)
        check()
        temporary.rename(destination)
        return result
    except BaseException:
        if created:
            temporary.unlink(missing_ok=True)
        raise
