"""Offline immutable release builder and independent semantic verification."""

import gzip
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from . import __version__
from .distribution_contracts import ExternalDirectory, load_release, safe_path, validate_release
from .distribution_io import decoded, durable, transfer
from .inventory_parts import FLAGS, Inventory, fsync_directory
from .state import atomic_json, digest_file


def database(path):
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError("Transport databases must be standalone closed snapshots")
    db = sqlite3.connect("file:" + quote(str(path.resolve())) + "?mode=ro&immutable=1", uri=True)
    db.execute("PRAGMA cache_size=-8192")
    db.execute("PRAGMA temp_store=MEMORY")
    return db


def verify_database(path, role, visibility, inventory):
    with closing(database(path)) as db:
        check = db.execute("PRAGMA quick_check")
        if check.fetchone() != ("ok",) or check.fetchone() is not None:
            raise ValueError("Database integrity check failed")
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if role == "inventory-db":
            if not {"meta", "assets", "pages"} <= tables:
                raise ValueError("Wrong producer database role")
            row = db.execute("SELECT value FROM meta WHERE key='snapshot_id'").fetchone()
            value = row[0] if row else None
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass
            if value != inventory["snapshot_id"]:
                raise ValueError("Producer database snapshot mismatch")
            if (
                db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                != inventory["unique_assets"]
            ):
                raise ValueError("Producer database asset count mismatch")
        elif role in ("viewer-db", "baseline-db"):
            if not {"documents", "rdf_triples"} <= tables:
                raise ValueError("Wrong Explorer database role")
            if role == "viewer-db":
                if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                    raise ValueError("Viewer database needs standalone DELETE journal mode")
                if db.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Viewer foreign keys are inconsistent")
                if visibility == "public" and "ingestion_documents" in tables:
                    if db.execute(
                        "SELECT 1 FROM ingestion_documents WHERE review_status!='approved' LIMIT 1"
                    ).fetchone():
                        raise ValueError("Public viewer contains unapproved drafts")
                if "ingestion_archive_sources" in tables:
                    if db.execute(
                        "SELECT 1 FROM ingestion_archive_sources WHERE source_sha256!=? LIMIT 1",
                        (inventory["source_sha256"],),
                    ).fetchone():
                        raise ValueError("Viewer sources belong to another inventory")
        else:
            raise ValueError("Unknown database role")


def verify_installed(root, release, selected):
    by_role = {}
    for item in selected:
        by_role.setdefault(item["role"], []).append(item)
        if item["role"] in ("inventory-db", "viewer-db", "baseline-db"):
            verify_database(
                safe_path(root, item["install_path"]),
                item["role"],
                release["visibility"],
                release["inventory"],
            )
    sources = []
    for role, bundled in (("inventory-full", False), ("inventory-manifest", True)):
        for item in by_role.get(role, []):
            source = Inventory(safe_path(root, item["install_path"]), manifest=bundled)
            if source.sha256 != release["inventory"]["source_sha256"]:
                raise ValueError("Inventory origin SHA mismatch")
            if source.header["metadata"]["snapshot_id"] != release["inventory"]["snapshot_id"]:
                raise ValueError("Inventory snapshot mismatch")
            complete = all(source.header["validation"].get(k) is True for k in FLAGS)
            if complete != release["inventory"]["complete"]:
                raise ValueError("Inventory completeness mismatch")
            signature = source.scan()
            if signature["counts"].get("asset", 0) != release["inventory"]["unique_assets"]:
                raise ValueError("Inventory asset coverage mismatch")
            sources.append(signature)
    if sources and any(s != sources[0] for s in sources[1:]):
        raise ValueError("Whole and partitioned inventories are not semantically equivalent")
    for item in by_role.get("viewer-db", []):
        directory = safe_path(root, item["install_path"]).parent
        meta = json.loads((directory / "manifest.json").read_text())
        if meta["database_sha256"] != digest_file(directory / "document_analysis.db") or meta[
            "clusters_sha256"
        ] != digest_file(directory / "tag_clusters.json"):
            raise ValueError("Mixed Explorer bundle versions")
        if release["visibility"] == "public" and meta.get("include_unreviewed"):
            raise ValueError("Draft bundle cannot become a public release")
        with closing(database(directory / "document_analysis.db")) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='ingestion_texts'").fetchone():
                for relative, sha, rowid, size in db.execute(
                    "SELECT t.path,t.sha256,d.id,length(CAST(d.full_text AS BLOB)) "
                    "FROM ingestion_texts t JOIN documents d USING(doc_id)"
                ):
                    path = safe_path(directory, relative)
                    if digest_file(path) != sha:
                        raise ValueError("Viewer TXT checksum mismatch")
                    digest = hashlib.sha256()
                    if hasattr(db, "blobopen"):
                        with db.blobopen("documents", "full_text", rowid, readonly=True) as blob:
                            while block := blob.read(1024 * 1024):
                                digest.update(block)
                    elif size is not None and size <= 16 * 1024**2:
                        value = db.execute(
                            "SELECT full_text FROM documents WHERE id=?", (rowid,)
                        ).fetchone()[0]
                        digest.update(value.encode())
                    else:
                        raise ValueError("Python 3.11+ required to verify large document text")
                    if digest.hexdigest() != sha:
                        raise ValueError("Viewer TXT and full_text disagree")
    return dict(
        artifacts=len(selected),
        semantic_inventory_checked=bool(sources),
        equivalent_representations=len(sources),
        visibility=release["visibility"],
    )


def select_artifacts(release, groups=None, allow_drafts=False):
    if release["visibility"] == "private" and not allow_drafts:
        raise ValueError("Private drafts require explicit --allow-drafts")
    selected = [a for a in release["artifacts"] if groups is None or a["group"] in groups]
    if not selected:
        raise ValueError("Requested profile is not present in this release")
    return selected


def verify_release(directory, groups=None, allow_drafts=False, reserve_bytes=1024**3):
    directory = Path(directory).absolute()
    storage = ExternalDirectory(directory, reserve_bytes)
    release = load_release(storage.path("release.json"))
    selected = select_artifacts(release, groups, allow_drafts)
    storage.check(sum(a["content_bytes"] for a in selected))
    with tempfile.TemporaryDirectory(prefix=".verify-", dir=directory) as scratch:
        for number, item in enumerate(selected, 1):
            source = storage.path(item["path"])
            if source.stat().st_size != item["bytes"] or digest_file(source) != item["sha256"]:
                raise ValueError("Transport artifact checksum mismatch")
            target = safe_path(scratch, item["install_path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            with decoded(source, item["encoding"]) as incoming, target.open("xb") as outgoing:
                transfer(incoming, outgoing, storage, item["content_bytes"], item["content_sha256"])
                durable(outgoing)
            print(
                f"release_verify artifact={number}/{len(selected)} id={item['id']}", file=sys.stderr
            )
        return verify_installed(Path(scratch), release, selected)


def build_release(recipe_path, output, reserve_bytes=1024**3, allow_drafts=False):
    recipe = json.loads(Path(recipe_path).read_text())
    output = Path(output).absolute()
    storage = ExternalDirectory(output.parent, reserve_bytes)
    if output.exists() or output.is_symlink():
        raise ValueError("Release output must be a new directory")
    template = {key: recipe[key] for key in ("release_id", "inventory", "visibility")}
    template.update(
        format="epstein-explorer-release",
        version=1,
        minimum_app_version=__version__,
        created_at=datetime.now(timezone.utc).isoformat(),
        artifacts=[],
    )
    if template["visibility"] == "private" and not allow_drafts:
        raise ValueError("Private build requires explicit --allow-drafts")
    stage = storage.mkdir(".building-" + uuid.uuid4().hex)
    try:
        for number, request in enumerate(recipe["artifacts"], 1):
            item = {
                k: request[k] for k in ("id", "group", "role", "path", "install_path", "encoding")
            }
            target = safe_path(stage, item["path"])
            if target.exists():
                raise ValueError("Duplicate artifact target")
            target.parent.mkdir(parents=True, exist_ok=True)
            source = Path(request["source"])
            if not source.is_absolute() or not source.is_file() or source.is_symlink():
                raise ValueError("Artifact source must be an existing absolute regular file")
            before = source.stat()
            source_encoding = request.get("source_encoding", "raw")
            expected = request.get("expected_content_bytes")
            if source_encoding == "raw":
                expected = before.st_size
            if type(expected) is not int or expected < 0:
                raise ValueError("Compressed input needs its expected decoded size")
            storage.check(expected)
            with decoded(source, source_encoding) as incoming, target.open("xb") as outgoing:
                if item["encoding"] == "gzip":
                    with gzip.GzipFile(filename="", fileobj=outgoing, mode="wb", mtime=0) as zipped:
                        sha = transfer(
                            incoming,
                            zipped,
                            storage,
                            expected,
                            request.get("expected_content_sha256"),
                        )
                else:
                    sha = transfer(
                        incoming,
                        outgoing,
                        storage,
                        expected,
                        request.get("expected_content_sha256"),
                    )
                durable(outgoing)
            after = source.stat()
            if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_size) != (
                after.st_dev,
                after.st_ino,
                after.st_mtime_ns,
                after.st_size,
            ):
                raise ValueError("Artifact source changed during build")
            item.update(
                bytes=target.stat().st_size,
                sha256=digest_file(target),
                content_bytes=expected,
                content_sha256=sha,
            )
            template["artifacts"].append(item)
            print(
                f"release_build artifact={number}/{len(recipe['artifacts'])} id={item['id']}",
                file=sys.stderr,
            )
        validate_release(template)
        atomic_json(stage / "release.json", template)
        report = verify_release(stage, allow_drafts=allow_drafts, reserve_bytes=reserve_bytes)
        atomic_json(stage / "verification.json", report)
        (stage / "README.md").write_text(
            "# Explorer data release\n\nVerify release.json against the publisher's pinned SHA-256.\n"
            "Use explorer-data import with a profile and an external destination.\n"
            "Working downloader/OCR state is created locally; see docs/distribution.md in the source release.\n"
        )
        sums = [(a["sha256"], a["path"]) for a in template["artifacts"]]
        sums += [
            (digest_file(stage / n), n) for n in ("release.json", "README.md", "verification.json")
        ]
        (stage / "SHA256SUMS").write_text(
            "".join(f"{sha}  {name}\n" for sha, name in sorted(sums, key=lambda r: r[1]))
        )
        storage.check()
        fsync_directory(stage)
        stage.rename(output)
        fsync_directory(output.parent)
        return dict(
            release_id=template["release_id"],
            output=str(output),
            artifacts=len(template["artifacts"]),
            manifest_sha256=digest_file(output / "release.json"),
            verified=report,
        )
    except BaseException:
        # A removed/replaced volume must not trigger deletion on a new mount.
        try:
            storage.guard()
        except (OSError, ValueError):
            pass
        else:
            shutil.rmtree(stage)
        raise
