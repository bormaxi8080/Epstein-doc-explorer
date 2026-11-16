"""Identity-checked external storage, immutable artifacts and bounded scratch."""

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .archive_catalog import atomic_json, sync_directory
from .inventory_parts import guard_directory
from .state import digest_file


@dataclass(frozen=True)
class CorpusLimits:
    reserve_bytes: int = 5 * 1024**3
    max_scratch_bytes: int = 512 * 1024**2
    max_artifact_bytes: int = 16 * 1024**2
    max_input_bytes: int = 512 * 1024**2
    max_pixels: int = 20_000_000
    worker_memory_mb: int = 512
    worker_timeout: int = 60

    def __post_init__(self):
        if self.reserve_bytes < 0 or any(
            v <= 0 for k, v in vars(self).items() if k != "reserve_bytes"
        ):
            raise ValueError("Invalid corpus resource limits")


class CorpusStorage:
    def __init__(self, root, create=False, limits=None):
        requested = Path(root)
        if not requested.is_absolute() or not requested.is_dir():
            raise ValueError("Corpus root must be an existing absolute directory")
        self.root = requested.resolve(strict=True)
        checkout = Path(__file__).resolve().parents[2]
        if checkout == self.root or checkout in self.root.parents:
            raise ValueError("Corpus data must be outside the checkout")
        self.guard = guard_directory(requested)
        self.limits = limits or CorpusLimits()
        marker = self.path("identity.json")
        if not marker.exists():
            if not create or any(self.root.iterdir()):
                raise ValueError("Initialize an empty corpus directory explicitly")
            atomic_json(marker, {"format_version": 1, "uuid": uuid.uuid4().hex})
        self.marker = json.loads(marker.read_text())
        if self.marker.get("format_version") != 1 or not re.fullmatch(
            r"[a-f0-9]{32}", str(self.marker.get("uuid"))
        ):
            raise ValueError("Invalid corpus identity")
        self.queue_identity = None
        self.mkdir("tmp")
        self.check()

    def bind_queue(self):
        stat = self.path("corpus.sqlite3").stat()
        self.queue_identity = stat.st_dev, stat.st_ino

    def check(self, required=0):
        self.guard()
        if json.loads(self.path("identity.json").read_text()) != self.marker:
            raise ValueError("Corpus identity changed")
        if self.queue_identity:
            stat = self.path("corpus.sqlite3").stat()
            if (stat.st_dev, stat.st_ino) != self.queue_identity:
                raise ValueError("Corpus database identity changed")
        if shutil.disk_usage(self.root).free - required < self.limits.reserve_bytes:
            raise ValueError("Corpus filesystem reserve exhausted")

    def path(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
            raise ValueError("Unsafe corpus path")
        path = self.root / relative
        for current in (path, *path.parents):
            if current == self.root:
                break
            if current.is_symlink():
                raise ValueError("Corpus symlinks are forbidden")
        return path

    def mkdir(self, relative):
        current = Path()
        for component in Path(relative).parts:
            self.guard()
            current /= component
            self.path(current).mkdir(exist_ok=True)

    def put(self, kind, value):
        if not re.fullmatch(r"[a-z][a-z0-9_-]*", kind):
            raise ValueError("Invalid corpus artifact kind")
        data = json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(data) > self.limits.max_artifact_bytes:
            raise ValueError("Corpus artifact exceeds its byte budget")
        self.check(len(data))
        sha = hashlib.sha256(data).hexdigest()
        relative = f"artifacts/{kind}/{sha[:2]}/{sha}.json"
        path = self.path(relative)
        self.mkdir(path.parent.relative_to(self.root))
        if path.exists():
            if digest_file(path) != sha:
                raise ValueError("Immutable corpus artifact checksum mismatch")
        else:
            with self.temporary(".json") as temporary:
                with temporary.open("xb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                self.check()
                temporary.replace(path)
                sync_directory(path.parent)
        return {"path": relative, "sha256": sha}

    def get(self, reference):
        self.check()
        path = self.path(reference["path"])
        if (
            path.stat().st_size > self.limits.max_artifact_bytes
            or digest_file(path) != reference["sha256"]
        ):
            raise ValueError("Corpus artifact checksum/size mismatch")
        return json.loads(path.read_text())

    @contextmanager
    def temporary(self, suffix=".tmp"):
        self.check()
        scratch = self.path("tmp")
        used = sum(p.stat().st_size for p in scratch.iterdir() if p.is_file())
        if used >= self.limits.max_scratch_bytes:
            raise ValueError("Corpus scratch budget exhausted")
        path = self.path("tmp/" + uuid.uuid4().hex + suffix)
        try:
            yield path
        finally:
            if path.exists() and self.root.exists():
                self.guard()
                path.unlink()

    @contextmanager
    def scratch_environment(self):
        self.check()
        old = tempfile.tempdir, {k: os.environ.get(k) for k in ("TMPDIR", "SQLITE_TMPDIR")}
        try:
            for k in old[1]:
                os.environ[k] = str(self.path("tmp"))
            tempfile.tempdir = str(self.path("tmp"))
            yield
        finally:
            tempfile.tempdir = old[0]
            for k, v in old[1].items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
