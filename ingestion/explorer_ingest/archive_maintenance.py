"""Offline metadata backups and explicit recovery; never copy/fetch originals."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .archive_catalog import CONTROL, StorageUnavailable, atomic_json, plan, sync_directory
from .archive_contracts import read_artifact
from .archive_transfer import artifact_paths, verify
from .state import digest_file


def directory_guard(directory):
    requested = Path(directory).absolute()
    root = requested.resolve(strict=True)
    identity = root.stat()
    expected = identity.st_dev, identity.st_ino

    def check():
        try:
            current = requested.resolve(strict=True)
            stat = current.stat()
            if current != root or (stat.st_dev, stat.st_ino) != expected:
                raise StorageUnavailable("Metadata destination filesystem identity changed")
            if root.parts[:2] == ("/", "Volumes") and not os.path.ismount(Path(*root.parts[:3])):
                raise StorageUnavailable("Metadata destination volume is not mounted")
        except OSError as exc:
            raise StorageUnavailable("Metadata destination is unavailable") from exc

    check()
    return check


def sync_tree(directory):
    # Make every copied file and directory durable before publishing its parent.
    for root, _, files in os.walk(directory, topdown=False):
        for name in files:
            with (Path(root) / name).open("rb") as stream:
                os.fsync(stream.fileno())
        sync_directory(root)


def backup_path(root, relative):
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or "\\" in relative:
        raise ValueError("Unsafe backup path")
    allowed = relative in ("identity.json", "archive.sqlite3") or re.fullmatch(
        r"(inputs|receipts)/[0-9a-f]{64}\.json", relative
    )
    if not allowed:
        raise ValueError("Unsupported backup member")
    candidate = root / path
    for parent in (candidate, *candidate.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError("Backup symlinks are forbidden")
    return candidate


def backup(archive, output, reserve_bytes=1024**3):
    archive.ready()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("Backup output must not exist")
    if not output.parent.is_dir():
        raise ValueError("Backup parent must already exist")
    check_destination = directory_guard(output.parent)
    if archive.root == output.resolve() or archive.root in output.resolve().parents:
        raise ValueError("Backup must be outside the archive")
    if (
        shutil.disk_usage(output.parent).free
        - archive.path(f"{CONTROL}/archive.sqlite3").stat().st_size
        < reserve_bytes
    ):
        raise ValueError("Backup filesystem reserve is unavailable")
    with tempfile.TemporaryDirectory(prefix=".pw010-backup-", dir=output.parent) as directory:
        check_destination()
        stage = Path(directory) / "metadata"
        stage.mkdir()
        connection = sqlite3.connect(stage / "archive.sqlite3")
        try:
            archive.db.backup(connection)
        finally:
            connection.close()
        count = 0
        with (stage / "files.jsonl").open("w") as manifest:

            def record(relative):
                nonlocal count
                archive.check()
                check_destination()
                if shutil.disk_usage(output.parent).free < reserve_bytes:
                    raise ValueError("Backup filesystem reserve exhausted")
                destination = backup_path(stage, relative)
                if relative != "archive.sqlite3":
                    destination.parent.mkdir(exist_ok=True)
                    original_size = archive.path(f"{CONTROL}/{relative}").stat().st_size
                    if shutil.disk_usage(output.parent).free - original_size < reserve_bytes:
                        raise ValueError("Backup filesystem reserve exhausted")
                    shutil.copyfile(archive.path(f"{CONTROL}/{relative}"), destination)
                with destination.open("rb") as saved:
                    os.fsync(saved.fileno())
                manifest.write(
                    json.dumps(
                        {
                            "path": relative,
                            "sha256": digest_file(destination),
                            "bytes": destination.stat().st_size,
                        }
                    )
                    + "\n"
                )
                count += 1

            record("archive.sqlite3")
            record("identity.json")
            for folder in ("inputs", "receipts"):
                source = archive.path(f"{CONTROL}/{folder}")
                if source.exists():
                    for item in source.iterdir():
                        if item.is_file():
                            record(f"{folder}/{item.name}")
            manifest.flush()

            os.fsync(manifest.fileno())
        header = {
            "format_version": 1,
            "archive_uuid": archive.marker["uuid"],
            "source_sha256": archive.meta("source_sha256"),
            "files": count,
            "files_sha256": digest_file(stage / "files.jsonl"),
            "contains_originals": False,
        }
        atomic_json(stage / "backup.json", header)
        check_destination()
        sync_tree(stage)
        if output.exists() or output.is_symlink():
            raise ValueError("Backup output appeared during snapshot")
        stage.rename(output)
        sync_directory(output.parent)
    return header


def inspect_backup(source):
    source = Path(source).resolve(strict=True)
    header = json.loads((source / "backup.json").read_text())
    if (
        header.get("format_version") != 1
        or header.get("contains_originals") is not False
        or type(header.get("files")) is not int
        or header["files"] < 2
    ):
        raise ValueError("Unsupported metadata backup")
    if (source / "files.jsonl").is_symlink() or digest_file(source / "files.jsonl") != header.get(
        "files_sha256"
    ):
        raise ValueError("Backup manifest checksum mismatch")
    # Disk-backed unique registry avoids loading a million receipt names in RAM.
    with tempfile.TemporaryDirectory(prefix="pw010-restore-check-") as directory:
        db = sqlite3.connect(Path(directory) / "members.sqlite3")
        try:
            db.execute("CREATE TABLE members(path TEXT PRIMARY KEY)")
            count = 0
            with (source / "files.jsonl").open() as stream:
                for line in stream:
                    item = json.loads(line)
                    path = backup_path(source, item["path"])
                    db.execute("INSERT INTO members VALUES (?)", (item["path"],))
                    if path.stat().st_size != item["bytes"] or digest_file(path) != item["sha256"]:
                        raise ValueError("Backup member checksum mismatch")
                    count += 1
            for required in (
                "identity.json",
                "archive.sqlite3",
                f"inputs/{header['source_sha256']}.json",
            ):
                if not db.execute("SELECT 1 FROM members WHERE path=?", (required,)).fetchone():
                    raise ValueError("Backup is missing required metadata")
            if count != header["files"]:
                raise ValueError("Backup member count mismatch")
        finally:
            db.close()
    if read_artifact(source / "identity.json", "identity")["uuid"] != header["archive_uuid"]:
        raise ValueError("Backup archive identity mismatch")
    return source, header


def restore(destination, source, reserve_bytes=1024**3):
    root = Path(destination)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("Restore destination must be an existing absolute directory")
    root = root.resolve()
    check_destination = directory_guard(root)
    control = root / CONTROL
    if control.exists() or control.is_symlink():
        raise ValueError("Restore never replaces existing archive metadata")
    from .archive_observer import doctor

    doctor(root, reserve_bytes)
    source, header = inspect_backup(source)
    if shutil.disk_usage(root).free < reserve_bytes:
        raise ValueError("Restore filesystem reserve is unavailable")
    # Validate and invalidate the copied ledger before publishing control metadata.
    with tempfile.TemporaryDirectory(prefix=".pw010-restore-", dir=root) as directory:
        check_destination()
        stage = Path(directory) / CONTROL
        stage.mkdir()
        with (source / "files.jsonl").open() as stream:
            for line in stream:
                check_destination()
                item = json.loads(line)
                original = backup_path(source, item["path"])
                target = backup_path(stage, item["path"])
                target.parent.mkdir(exist_ok=True)
                if shutil.disk_usage(root).free - original.stat().st_size < reserve_bytes:
                    raise ValueError("Restore filesystem reserve exhausted")
                shutil.copyfile(original, target)
                if digest_file(target) != item["sha256"]:
                    raise ValueError("Backup changed during restore")
        db = sqlite3.connect(stage / "archive.sqlite3")
        try:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup ledger integrity check failed")
            row = db.execute("SELECT value FROM meta WHERE key='source_sha256'").fetchone()
            if not row or json.loads(row[0]) != header["source_sha256"]:
                raise ValueError("Backup ledger/source binding mismatch")
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES ('last_full_audit','null')")
                db.execute("UPDATE assets SET status='pending',error=NULL")
                # Receipts for absent originals are historical evidence, not a
                # promise that recover_publication can publish a missing partial.
                for asset_id, relative in db.execute("SELECT id,relative_path FROM assets"):
                    receipt = (
                        stage
                        / "receipts"
                        / (hashlib.sha256(asset_id.encode()).hexdigest() + ".json")
                    )
                    path = root / relative
                    if path.is_absolute() and (
                        root not in path.parents or ".." in Path(relative).parts
                    ):
                        raise ValueError("Unsafe ledger original path")
                    if not path.exists() and receipt.exists():
                        history = stage / "historical-receipts"
                        history.mkdir(exist_ok=True)
                        receipt.rename(history / receipt.name)
        finally:
            db.close()
        check_destination()
        sync_tree(stage)
        if control.exists() or control.is_symlink():
            raise ValueError("Archive metadata appeared during restore")
        stage.rename(control)
        sync_directory(root)
    return {
        "format_version": 1,
        "restored": True,
        "archive_uuid": header["archive_uuid"],
        "fresh_full_audit_required": True,
        "originals_copied": 0,
    }


def rebuild(archive, dump, receipts, options, allow_partial=False):
    """Recreate a missing ledger only; caller must reserve the recovery lease."""
    if archive.meta("plan_complete"):
        raise ValueError("Rebuild requires a new/missing ledger")
    result = plan(archive, dump, allow_partial=allow_partial)
    copied = 0
    for row in archive.db.execute("SELECT * FROM assets"):
        _, _, target = artifact_paths(archive, row)
        source = Path(receipts) / target.name
        if not source.exists():
            continue
        receipt = read_artifact(source, "receipt")
        if receipt["asset_id"] != row["id"] or receipt["source_sha256"] != result["source_sha256"]:
            raise ValueError("Recovery receipt belongs to a different inventory")
        path = archive.path(row["relative_path"])
        if not path.exists():
            continue
        if digest_file(path) != receipt["sha256"]:
            raise ValueError("Recovery original checksum mismatch; preserve and quarantine it")
        if source.resolve() != target.resolve():
            target.parent.mkdir(exist_ok=True)
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise ValueError("Recovery would replace an existing receipt")
            shutil.copyfile(source, target)
        copied += 1
    result = verify(archive, options)
    return {**result, "recovered_receipts": copied}
