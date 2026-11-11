"""A durable first-in-tree pilot bound to the complete source inventory."""

import hashlib
from datetime import datetime, timezone

from .archive_catalog import CONTROL, atomic_json, log_event, report


def selection_hash(archive):
    digest = hashlib.sha256()
    for row in archive.db.execute("SELECT asset_id FROM pilot_assets ORDER BY rank"):
        digest.update((row[0] + "\n").encode())
    return digest.hexdigest()


def create_pilot(archive, count):
    archive.ready()
    if count <= 0:
        raise ValueError("Pilot count must be positive")
    previous = archive.meta("pilot")
    if previous:
        if previous["count"] != count or previous["source_sha256"] != archive.meta("source_sha256"):
            raise ValueError("This destination already has a different fixed pilot")
        pilot_status(archive)
    else:
        selected = 0
        with archive.db:
            archive.db.execute("DELETE FROM pilot_assets")
            for row in archive.db.execute(
                "SELECT asset_id FROM nodes WHERE kind='file' ORDER BY seq"
            ):
                added = archive.db.execute(
                    "INSERT OR IGNORE INTO pilot_assets VALUES (?,?)", (selected + 1, row[0])
                ).rowcount
                selected += added
                if selected == count:
                    break
            if selected != count:
                raise ValueError("Source has fewer unique files than the requested pilot")
            archive.set_meta(
                "pilot",
                {
                    "count": count,
                    "source_sha256": archive.meta("source_sha256"),
                    "selection_sha256": selection_hash(archive),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            archive.set_meta("pilot_audit", None)
    manifest = {
        **archive.meta("pilot"),
        "assets": [
            dict(row)
            for row in archive.db.execute(
                "SELECT p.rank,a.id,a.expected_bytes,a.relative_path FROM pilot_assets p CROSS JOIN assets a ON a.id=p.asset_id ORDER BY p.rank"
            )
        ],
    }
    atomic_json(archive.path(f"{CONTROL}/pilot.json"), manifest)
    log_event(
        archive,
        "pilot_selected",
        count=count,
        expected_bytes=pilot_status(archive)["expected_bytes"],
    )
    return report(archive)


def pilot_status(archive):
    specification = archive.meta("pilot")
    if not specification:
        raise ValueError("Run pilot --first N before using --pilot")
    if specification["source_sha256"] != archive.meta("source_sha256") or specification[
        "selection_sha256"
    ] != selection_hash(archive):
        raise ValueError("Pilot identity changed")
    totals = dict(
        archive.db.execute("""SELECT COUNT(*) total,COALESCE(SUM(a.expected_bytes),0) expected_bytes,
        COALESCE(SUM(CASE WHEN a.status='downloaded' THEN a.actual_bytes ELSE 0 END),0) downloaded_bytes
        FROM pilot_assets p CROSS JOIN assets a ON a.id=p.asset_id""").fetchone()
    )
    if totals["total"] != specification["count"]:
        raise ValueError("Pilot membership count changed")
    statuses = dict(
        archive.db.execute(
            """SELECT a.status,COUNT(*) FROM pilot_assets p CROSS JOIN assets a ON a.id=p.asset_id GROUP BY a.status"""
        )
    )
    audit = archive.meta("pilot_audit")
    return {
        **specification,
        **totals,
        "statuses": statuses,
        "download_complete": statuses.get("downloaded", 0) == totals["total"],
        "audit": audit,
        "verified": bool(
            audit and audit.get("passed") and statuses.get("downloaded", 0) == totals["total"]
        ),
    }


def asset_rows(archive, *, pilot=False, pending=False):
    if pilot:
        pilot_status(archive)
        query = "SELECT a.* FROM pilot_assets p CROSS JOIN assets a ON a.id=p.asset_id"
        if pending:
            query += " WHERE a.status!='downloaded'"
        return archive.db.execute(query + " ORDER BY p.rank")
    query = "SELECT * FROM assets"
    if pending:
        query += " WHERE status!='downloaded'"
    return archive.db.execute(query + " ORDER BY id")
