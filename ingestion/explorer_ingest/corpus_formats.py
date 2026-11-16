"""Bounded non-PDF parsing in the same disposable parser process."""

import hashlib
import json
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from .corpus_contracts import document_id, fingerprint
from .corpus_text import publish_temp

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".xml", ".dat", ".opt"}
IMAGE_SUFFIXES = {".gif", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}


def non_pdf(request, path, header):
    action = request["action"]
    suffix = path.suffix.lower()
    if suffix in {".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pptm", ".odt", ".ods", ".odp"}:
        return dict(format="office", pages=0, status="unsupported")
    if header.startswith(b"PK"):
        if action == "inspect":
            return dict(format="zip", pages=0, status="container_pending")
        raise ValueError("ZIP needs explicit bounded expansion")
    if suffix in TEXT_SUFFIXES:
        if path.stat().st_size > request["output_bytes"] // 2:
            raise ValueError("Text exceeds extraction budget")
        text = path.read_text(encoding="utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError("Binary text source")
        if action == "inspect":
            return dict(format="text", pages=1, status="inspected")
        if request["page"] != 1 or action != "extract":
            raise ValueError("Text has no raster page")
        Path(request["output"]).write_text(
            json.dumps(dict(text=text, geometry=None, extractor="utf8-1"), ensure_ascii=False)
        )
        return dict(format="text", pages=1)
    if suffix in IMAGE_SUFFIXES or header.startswith(
        (b"\x89PNG", b"\xff\xd8\xff", b"II*\x00", b"MM\x00*")
    ):
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = request["max_pixels"]
        with Image.open(path) as image:
            if image.width * image.height > request["max_pixels"]:
                raise ValueError("Image pixel budget exceeded")
            frames = getattr(image, "n_frames", 1)
            if frames > 10000:
                raise ValueError("Image frame count exceeds budget")
            if action == "inspect":
                return dict(format="image", pages=frames, status="inspected")
            if not 1 <= request["page"] <= frames:
                raise ValueError("Image frame outside range")
            image.seek(request["page"] - 1)
            geometry = dict(
                width_pixels=image.width,
                height_pixels=image.height,
                frame=request["page"],
                rotation=0,
            )
            if action == "extract":
                Path(request["output"]).write_text(
                    json.dumps(dict(text="", geometry=geometry, extractor="image-no-native-1"))
                )
                return dict(format="image", pages=frames)
            with image.convert("RGB") as rgb:
                rgb.save(request["output"], format="JPEG", quality=90)
                return dict(
                    format="jpeg",
                    width=rgb.width,
                    height=rgb.height,
                    dpi=None,
                    geometry=geometry,
                    renderer="pillow-12.3.0",
                )
    return dict(format=suffix.lstrip(".") or "unknown", pages=0, status="unsupported")


def expand_worker(request, path):
    target = Path(request["output"])
    target.mkdir()
    total = 0
    entries = []
    names = set()
    with zipfile.ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > 1000:
            raise ValueError("ZIP member count exceeds budget")
        for info in members:
            if len(info.filename) > 1024:
                raise ValueError("ZIP member name too long")
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in info.filename
                or ":" in info.filename
                or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1
                or info.filename.casefold() in names
            ):
                raise ValueError("Unsafe ZIP member")
            names.add(info.filename.casefold())
            total += info.file_size
            if (
                total > request["expanded_bytes"]
                or info.file_size > max(1, info.compress_size) * 100
            ):
                raise ValueError("ZIP expansion budget exceeded")
            output = target / (str(len(entries)) + name.suffix[:20])
            digest = hashlib.sha256()
            written = 0
            with archive.open(info) as source, output.open("xb") as stream:
                while data := source.read(1024 * 1024):
                    written += len(data)
                    if written > info.file_size:
                        raise ValueError("ZIP size mismatch")
                    digest.update(data)
                    stream.write(data)
            if written != info.file_size:
                raise ValueError("Truncated ZIP member")
            entries.append(
                dict(name=info.filename, path=output.name, bytes=written, sha256=digest.hexdigest())
            )
    manifest = target / "manifest.json"
    manifest.write_text(json.dumps(dict(format="zip", members=entries, expanded_bytes=total)))
    return dict(format="zip", manifest="manifest.json", count=len(entries))


def expand_container(corpus, doc_id, max_depth=3):
    import os
    import sys
    from types import SimpleNamespace

    from .archive_limits import run_limited

    corpus.db.executescript("""CREATE TABLE IF NOT EXISTS corpus_members(
      parent_id TEXT,name TEXT,doc_id TEXT,path TEXT,sha256 TEXT,bytes INTEGER,depth INTEGER,
      PRIMARY KEY(parent_id,name));""")
    parent = (
        corpus.db.execute(
            "SELECT MAX(depth) FROM corpus_members WHERE doc_id=?", (doc_id,)
        ).fetchone()[0]
        or 0
    )
    if parent >= max_depth:
        raise ValueError("Nested container depth exceeded")
    limits = corpus.storage.limits
    with (
        corpus.original(doc_id) as original,
        corpus.storage.temporary(
            ".members", min(limits.max_scratch_bytes // 2, limits.max_input_bytes)
        ) as temporary,
    ):
        try:
            with corpus.storage.scratch_environment():
                result = run_limited(
                    [sys.executable, "-m", "explorer_ingest.corpus_parse_worker"],
                    dict(
                        path=str(original),
                        action="expand",
                        output=str(temporary),
                        seconds=limits.worker_timeout,
                        memory_mb=limits.worker_memory_mb,
                        parent_pid=os.getpid(),
                        input_bytes=limits.max_input_bytes,
                        output_bytes=limits.max_artifact_bytes,
                        expanded_bytes=min(limits.max_scratch_bytes // 2, limits.max_input_bytes),
                    ),
                    SimpleNamespace(
                        validation_timeout=limits.worker_timeout,
                        validation_memory_mb=limits.worker_memory_mb,
                    ),
                    corpus.storage.check,
                )
            result = json.loads((temporary / "manifest.json").read_text())
            for entry in result["members"]:
                source = temporary / entry["path"]
                sha = entry["sha256"]
                child = document_id(sha)
                # Move through managed top-level scratch so publication checks remain shared.
                with corpus.storage.temporary(source.suffix, max(1, entry["bytes"])) as staged:
                    source.replace(staged)
                    ref = publish_temp(
                        corpus.storage, staged, f"members/{sha[:2]}/{sha}{source.suffix}", sha
                    )
                corpus.db.execute(
                    "INSERT OR IGNORE INTO corpus_documents(doc_id,sha256,bytes) VALUES (?,?,?)",
                    (child, sha, entry["bytes"]),
                )
                doc = corpus.document(child)
                if doc["sha256"] != sha or doc["bytes"] != entry["bytes"]:
                    raise ValueError("Member identity collision")
                corpus.db.execute(
                    "INSERT OR IGNORE INTO corpus_members VALUES (?,?,?,?,?,?,?)",
                    (doc_id, entry["name"], child, ref["path"], sha, entry["bytes"], parent + 1),
                )
                key = fingerprint(["member-inspect-1", sha, source.suffix])
                corpus.queue.enqueue(key, "inspect", dict(doc_id=child))
            corpus.db.execute(
                "UPDATE corpus_documents SET status='container_expanded' WHERE doc_id=?", (doc_id,)
            )
            return corpus.storage.put("container", result)
        finally:
            if temporary.is_dir():
                corpus.storage.guard()
                shutil.rmtree(temporary)
