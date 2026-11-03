"""Offline, audited archive handoff; originals remain on their selected disk."""

import json
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .archive_catalog import CONTROL, Archive, report
from .archive_contracts import read_artifact, validate_artifact
from .inventory import import_inventory
from .state import digest_file, fingerprint


def audit_binding(archive):
    result = report(archive)
    if not result["complete_at_last_audit"]:
        raise ValueError("A complete inventory and a successful full audit are required")
    manifest = archive.path(f"{CONTROL}/verified-files.jsonl")
    sha = digest_file(manifest)
    if sha != result["last_full_audit"]["manifest_sha256"]:
        raise ValueError("Verified manifest changed; run a fresh full audit")
    return sha


def adopt_archive(store, destination):
    root = Path(destination).resolve(strict=True)
    if root == store.root or root in store.root.parents or store.root in root.parents:
        raise ValueError("Archive originals and derived data need separate roots")
    with ExitStack() as stack:
        archive = Archive(root)
        stack.callback(archive.close)
        binding = audit_binding(archive)
        source_sha = archive.meta("source_sha256")
        source = archive.path(f"{CONTROL}/inputs/{source_sha}.json")
        if digest_file(source) != source_sha:
            raise ValueError("Stored inventory checksum mismatch")
        # Validate into a disk-backed temporary registry before importing anything.
        store.db.execute("DROP TABLE IF EXISTS temp.adoption")
        store.db.execute("CREATE TEMP TABLE adoption(id TEXT PRIMARY KEY,path TEXT,sha TEXT)")
        count = 0
        with archive.path(f"{CONTROL}/verified-files.jsonl").open() as stream:
            for line in stream:
                item = validate_artifact("manifest-row", json.loads(line))
                row = archive.db.execute(
                    "SELECT * FROM assets WHERE id=?", (item["asset_id"],)
                ).fetchone()
                if not row or any(
                    (
                        row["status"] != "downloaded",
                        row["relative_path"] != item["relative_path"],
                        row["sha256"] != item["sha256"],
                        row["actual_bytes"] != item["bytes"],
                        json.loads(row["metadata"]) != item["metadata"],
                    )
                ):
                    raise ValueError("Manifest and ledger disagree")
                receipt = read_artifact(archive.path(item["receipt"]), "receipt")
                if (
                    receipt != json.loads(row["receipt"])
                    or receipt["asset_id"] != item["asset_id"]
                    or receipt["source_sha256"] != source_sha
                    or receipt["relative_path"] != item["relative_path"]
                    or receipt["sha256"] != item["sha256"]
                    or receipt["actual_bytes"] != item["bytes"]
                ):
                    raise ValueError("Manifest and receipt disagree")
                if digest_file(archive.path(item["relative_path"])) != item["sha256"]:
                    raise ValueError("Original changed after audit")
                store.db.execute(
                    "INSERT INTO adoption VALUES (?,?,?)",
                    (item["asset_id"], item["relative_path"], item["sha256"]),
                )
                count += 1
                if count % 1000 == 0:
                    archive.check()
        if count != report(archive)["total"] or audit_binding(archive) != binding:
            raise ValueError("Verified manifest is incomplete or changed")
        store.db.commit()
        result = import_inventory(store, source)
        with store.db:
            old = store.db.execute(
                "SELECT * FROM archive_imports WHERE inventory_id=?", (source_sha,)
            ).fetchone()
            if old and (
                old["archive_uuid"] != archive.marker["uuid"] or old["manifest_sha256"] != binding
            ):
                raise ValueError("Inventory already belongs to a different archive/audit")
            store.db.execute(
                "INSERT OR REPLACE INTO archive_imports VALUES (?,?,?,?)",
                (source_sha, str(root), archive.marker["uuid"], binding),
            )
            for row in store.db.execute("SELECT * FROM adoption"):
                key = fingerprint([source_sha, row["id"]])
                previous = store.db.execute(
                    "SELECT sha256 FROM assets WHERE id=?", (key,)
                ).fetchone()[0]
                if previous and previous != row["sha"]:
                    raise ValueError("Existing derived asset has a different checksum")
                store.db.execute(
                    "INSERT OR REPLACE INTO archive_links VALUES (?,?,?)",
                    (key, row["id"], row["path"]),
                )
                reference = f"archive://{archive.marker['uuid']}/{row['path']}"
                store.db.execute(
                    "UPDATE assets SET status='downloaded',sha256=?,path=?,error=NULL WHERE id=?",
                    (row["sha"], reference, key),
                )
        archive.check()
        return {
            **result,
            "adopted": count,
            "archive_uuid": archive.marker["uuid"],
            "manifest_sha256": binding,
        }


@contextmanager
def original_paths(store, assets):
    """Hold source leases for one extraction batch, with one manifest hash per archive."""
    with ExitStack() as stack:
        archives, paths = {}, {}
        for asset in assets:
            link = store.db.execute(
                """SELECT l.*,i.* FROM archive_links l
                JOIN assets a ON a.id=l.asset_id JOIN archive_imports i ON i.inventory_id=a.inventory_id
                WHERE l.asset_id=?""",
                (asset["id"],),
            ).fetchone()
            if not link:
                paths[asset["id"]] = store.path(asset["path"])
                continue
            key = link["archive_uuid"]
            if key not in archives:
                archive = Archive(link["archive_root"])
                stack.callback(archive.close)
                if (
                    archive.marker["uuid"] != key
                    or audit_binding(archive) != link["manifest_sha256"]
                ):
                    raise ValueError("Adopted archive identity/audit changed; re-adopt explicitly")
                archives[key] = archive
            archive = archives[key]
            row = archive.db.execute(
                "SELECT * FROM assets WHERE id=?", (link["source_id"],)
            ).fetchone()
            if (
                not row
                or row["sha256"] != asset["sha256"]
                or row["relative_path"] != link["relative_path"]
            ):
                raise ValueError("Adopted source binding changed")
            paths[asset["id"]] = archive.path(link["relative_path"])
        yield paths
        for archive in archives.values():
            archive.check()
