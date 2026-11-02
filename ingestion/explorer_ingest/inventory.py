"""Stream a PW-010 inventory without importing Oriol or its live checkpoint."""

import json
from pathlib import Path
from urllib.parse import urlparse

import ijson

from .state import digest_file, fingerprint


def import_inventory(store, source, allow_partial=False):
    source = Path(source)
    snapshot = digest_file(source)
    destination = store.root / "inventories" / (snapshot + ".json")
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        import shutil

        temporary = destination.with_suffix(".tmp")
        shutil.copyfile(source, temporary)
        if digest_file(temporary) != snapshot:
            temporary.unlink()
            raise ValueError("Inventory changed during import; retry from an immutable snapshot")
        temporary.replace(destination)
    elif digest_file(destination) != snapshot:
        raise ValueError("Stored inventory checksum mismatch")
    source = destination
    # Validate the full stream before committing anything; retain the hierarchy
    # in an immutable copy rather than loading the large tree into memory.
    with source.open("rb") as stream:
        metadata = next(ijson.items(stream, "metadata", use_float=True), None)
    with source.open("rb") as stream:
        validation = next(ijson.items(stream, "validation", use_float=True), None)
    with source.open("rb") as stream:
        version = next(ijson.items(stream, "schema_version"), 1)
    if version not in (1, 2) or not isinstance(metadata, dict) or not isinstance(validation, dict):
        raise ValueError("Expected a PW-010 schema v1/v2 inventory")
    if metadata.get("task_uid") != "PW-010" or metadata.get("documents_downloaded") is not False:
        raise ValueError("Expected the PW-010 inventory-only contract")
    flags = ("schema_valid", "structure_complete", "metadata_complete", "source_stable", "complete")
    complete = all(validation.get(key) is True for key in flags)
    if complete and validation.get("issues_count") != 0:
        raise ValueError("Certified inventory reports issues")
    if not complete and not allow_partial:
        raise ValueError("Inventory is partial; --allow-partial is required for a pilot")
    with source.open("rb") as stream:
        issues = sum(1 for _ in ijson.items(stream, "issues.item"))
    if complete and issues:
        raise ValueError("Certified inventory contains issues")
    count = 0
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO inventories VALUES (?,?,?,?)",
            (snapshot, version, int(complete), json.dumps(metadata)),
        )
        with source.open("rb") as stream:
            for asset_id, asset in ijson.kvitems(stream, "assets", use_float=True):
                if not isinstance(asset, dict) or not asset.get("occurrences"):
                    raise ValueError("An asset must retain its original occurrences")
                if urlparse(asset.get("url", "")).scheme not in ("http", "https"):
                    raise ValueError("Asset URLs must use HTTP(S)")
                size = asset.get("size_bytes")
                if size is not None and (type(size) is not int or size < 0):
                    raise ValueError("Invalid advertised file size")
                key = fingerprint([snapshot, asset_id])
                store.db.execute(
                    """INSERT OR IGNORE INTO assets
                    (id,inventory_id,original_id,metadata) VALUES (?,?,?,?)""",
                    (key, snapshot, asset_id, json.dumps(asset)),
                )
                count += 1
        if count == 0:
            raise ValueError("Inventory contains no assets")
        if validation.get("unique_files_count") != count:
            raise ValueError("Inventory asset count does not match validation")
    return {"inventory_id": snapshot, "assets": count, "complete": complete}
