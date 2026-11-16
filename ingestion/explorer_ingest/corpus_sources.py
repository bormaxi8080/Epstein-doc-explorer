"""Freeze verified source selections without importing/copying the full archive."""

import fcntl
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from .archive_catalog import CONTROL, atomic_json, sync_directory
from .archive_contracts import read_artifact, validate_artifact
from .archive_observer import ArchiveView
from .corpus_contracts import validate
from .inventory_parts import guard_directory
from .state import digest_file


def freeze_sources(archive_root, output, scope="pilot"):
    if scope not in ("pilot", "full"):
        raise ValueError("Source scope must be pilot or full")
    output = Path(output).absolute()
    check = guard_directory(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError("Selection output must be a new directory")
    with closing(ArchiveView(archive_root)) as archive:
        archive.ready()
        if not archive.meta("source_certified"):
            raise ValueError("A certified complete source inventory is required")
        if archive.root == output.resolve() or archive.root in output.resolve().parents:
            raise ValueError("Source selections need a separate derived root")
        lock_path = archive.path(f"{CONTROL}/writer.lock")
        with lock_path.open("rb") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError(
                    "Pause the archive writer before freezing a source selection"
                ) from None
            audit = archive.meta("pilot_audit" if scope == "pilot" else "last_full_audit") or {}
            if not audit.get("passed") or audit.get("failures"):
                raise ValueError("A successful fresh audit of the selected scope is required")
            if audit.get("source_sha256") != archive.meta("source_sha256"):
                raise ValueError("Audit source identity mismatch")
            manifest = archive.path(
                f"{CONTROL}/"
                + ("pilot-verified-files.jsonl" if scope == "pilot" else "verified-files.jsonl")
            )
            binding = digest_file(manifest)
            if binding != audit.get("manifest_sha256"):
                raise ValueError("Audit manifest checksum mismatch")
            pilot = archive.meta("pilot") if scope == "pilot" else None
            expected = (
                pilot["count"]
                if pilot
                else archive.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
            )
            if audit.get("checked") != expected:
                raise ValueError("Audit coverage does not match source selection")
            with tempfile.TemporaryDirectory(
                prefix=".corpus-selection-", dir=output.parent
            ) as directory:
                stage = Path(directory) / "selection"
                stage.mkdir()
                count, selection_digest = 0, hashlib.sha256()
                with closing(sqlite3.connect(stage / "seen.sqlite3")) as seen:
                    seen.execute("CREATE TABLE seen(id TEXT PRIMARY KEY)")
                    with (
                        manifest.open() as source,
                        (stage / "sources.jsonl").open("w", encoding="utf-8") as target,
                    ):
                        for line in source:
                            check()
                            archive.check()
                            item = validate_artifact("manifest-row", json.loads(line))
                            row = archive.db.execute(
                                "SELECT * FROM assets WHERE id=?", (item["asset_id"],)
                            ).fetchone()
                            if (
                                not row
                                or row["status"] != "downloaded"
                                or any(
                                    (
                                        row["relative_path"] != item["relative_path"],
                                        row["sha256"] != item["sha256"],
                                        row["actual_bytes"] != item["bytes"],
                                        json.loads(row["metadata"]) != item["metadata"],
                                    )
                                )
                            ):
                                raise ValueError("Selected manifest and ledger disagree")
                            count += 1
                            if pilot:
                                member = archive.db.execute(
                                    "SELECT asset_id FROM pilot_assets WHERE rank=?", (count,)
                                ).fetchone()
                                if not member or member[0] != item["asset_id"]:
                                    raise ValueError("Pilot membership/order changed")
                            seen.execute("INSERT INTO seen VALUES (?)", (item["asset_id"],))
                            receipt = read_artifact(archive.path(item["receipt"]), "receipt")
                            if any(
                                (
                                    receipt != json.loads(row["receipt"]),
                                    receipt["asset_id"] != item["asset_id"],
                                    receipt["source_sha256"] != archive.meta("source_sha256"),
                                    receipt["relative_path"] != item["relative_path"],
                                    receipt["sha256"] != item["sha256"],
                                    receipt["actual_bytes"] != item["bytes"],
                                )
                            ):
                                raise ValueError("Selected receipt binding mismatch")
                            path = archive.path(item["relative_path"])
                            if (
                                path.stat().st_size != item["bytes"]
                                or digest_file(path) != item["sha256"]
                            ):
                                raise ValueError("Selected original changed after audit")
                            selection_digest.update((item["asset_id"] + "\n").encode())
                            target.write(
                                json.dumps({**item, "receipt_data": receipt}, ensure_ascii=False)
                                + "\n"
                            )
                        target.flush()
                        os.fsync(target.fileno())
                (stage / "seen.sqlite3").unlink()
                selection_sha = selection_digest.hexdigest()
                if count != expected or (pilot and selection_sha != pilot["selection_sha256"]):
                    raise ValueError("Selection is incomplete or changed")
                if digest_file(manifest) != binding:
                    raise ValueError("Source audit changed during handoff")
                result = {
                    "format_version": 1,
                    "scope": scope,
                    "archive_uuid": archive.marker["uuid"],
                    "source_sha256": archive.meta("source_sha256"),
                    "selection_sha256": selection_sha,
                    "audit_manifest_sha256": binding,
                    "files_count": count,
                }
                validate("selection", result)
                atomic_json(
                    stage / "selection.json",
                    {
                        **result,
                        "archive_root": str(archive.root),
                        "rows_sha256": digest_file(stage / "sources.jsonl"),
                    },
                )
                check()
                archive.check()
                sync_directory(stage)
                stage.rename(output)
                sync_directory(output.parent)
                return result


def selected_sources(selection):
    path = Path(selection).resolve(strict=True)
    header = json.loads(path.read_text())
    validate(
        "selection",
        {
            k: header[k]
            for k in (
                "format_version",
                "scope",
                "archive_uuid",
                "source_sha256",
                "selection_sha256",
                "audit_manifest_sha256",
                "files_count",
            )
        },
    )
    rows = path.with_name("sources.jsonl")
    if rows.is_symlink() or digest_file(rows) != header["rows_sha256"]:
        raise ValueError("Frozen source rows checksum mismatch")
    count, digest = 0, hashlib.sha256()
    with rows.open() as stream:
        for line in stream:
            record = json.loads(line)
            count += 1
            digest.update((record["asset_id"] + "\n").encode())
            yield header, record
    if count != header["files_count"] or digest.hexdigest() != header["selection_sha256"]:
        raise ValueError("Frozen source membership mismatch")
