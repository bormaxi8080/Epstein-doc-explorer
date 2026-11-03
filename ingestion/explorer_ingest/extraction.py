"""Text/PDF extraction and bounded ZIP expansion with explicit unsupported states."""

import json
import shutil
import zipfile
from pathlib import Path, PurePosixPath

from .archive_adoption import original_paths
from .state import atomic_json, digest_file

EXTRACTION_VERSION = "text-pdf-1"


def read_pages(path, name):
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF requires a separate extraction adapter")
        pages = [
            {"page": index + 1, "text": page.extract_text() or ""}
            for index, page in enumerate(reader.pages)
        ]
        # Do not silently analyze only the readable portion of a mixed PDF.
        if any(len(page["text"].strip()) < 20 for page in pages):
            return pages, "needs_ocr"
        return pages, "extracted"
    if suffix in (".txt", ".md", ".csv"):
        text = Path(path).read_text(encoding="utf-8-sig")
        return [{"page": 1, "text": text}], "extracted" if text.strip() else "empty"
    return [], "unsupported"


def register_document(store, asset, path, member, original_reference=None):
    sha = digest_file(path)
    doc_id = "DOJ_" + sha[:40]
    existing = store.db.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    if not existing:
        status, pages, error = "extraction_failed", [], None
        try:
            pages, status = read_pages(path, member)
        except Exception as exc:
            error = type(exc).__name__
        page_path = store.root / "pages" / (sha + ".json")
        page_path.parent.mkdir(exist_ok=True)
        atomic_json(page_path, {"version": EXTRACTION_VERSION, "pages": pages})
        with store.db:
            store.db.execute(
                """INSERT INTO documents
                (doc_id,asset_id,member_path,sha256,original_path,status,pages_json,error)
                VALUES (?,?,?,?,?,?,?,?)""",
                (
                    doc_id,
                    asset["id"],
                    member,
                    sha,
                    original_reference or store.relative(path),
                    status,
                    store.relative(page_path),
                    error,
                ),
            )
    with store.db:
        store.db.execute(
            "INSERT OR IGNORE INTO document_sources VALUES (?,?,?)", (doc_id, asset["id"], member)
        )
    return doc_id


def extract_assets(store, limit=10, max_bytes=512 * 1024**2, max_members=10000):
    rows = store.db.execute(
        """SELECT a.* FROM assets a WHERE status='downloaded'
        AND NOT EXISTS (SELECT 1 FROM extracted_assets s WHERE s.asset_id=a.id)
        ORDER BY id LIMIT ?""",
        (limit,),
    ).fetchall()
    with original_paths(store, rows) as paths:
        return _extract_batch(store, rows, paths, max_bytes, max_members)


def _extract_batch(store, rows, paths, max_bytes, max_members):
    results = []
    for asset in rows:
        path = paths[asset["id"]]
        if digest_file(path) != asset["sha256"]:
            raise ValueError("Original checksum mismatch")
        name = json.loads(asset["metadata"]).get("original_name") or "unknown"
        if zipfile.is_zipfile(path):
            directory = store.root / "members"
            directory.mkdir(exist_ok=True)
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                if len(members) > max_members or sum(x.file_size for x in members) > max_bytes:
                    raise ValueError("ZIP expansion budget exceeded")
                # Validate every entry before registering any document.
                for entry in members:
                    safe = PurePosixPath(entry.filename.replace("\\", "/"))
                    if (
                        safe.is_absolute()
                        or ".." in safe.parts
                        or ":" in entry.filename
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000
                    ):
                        raise ValueError("Unsafe ZIP member")
                    if entry.file_size > max(1, entry.compress_size) * 200:
                        raise ValueError("ZIP compression ratio exceeds budget")
                # Crash recovery replays members idempotently; a per-asset marker
                # is written only after all members were consumed successfully.
                doc_ids = []
                for entry in members:
                    if entry.is_dir():
                        continue
                    temporary = directory / (asset["id"] + ".tmp")
                    try:
                        with archive.open(entry) as source, temporary.open("wb") as output:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                        sha = digest_file(temporary)
                        destination = directory / sha
                        temporary.replace(destination)
                        doc_ids.append(register_document(store, asset, destination, entry.filename))
                    finally:
                        temporary.unlink(missing_ok=True)
                results.append({"asset_id": asset["id"], "documents": len(doc_ids)})
        else:
            if path.stat().st_size > max_bytes:
                raise ValueError("Extraction size exceeds budget")
            register_document(store, asset, path, name, asset["path"])
            results.append({"asset_id": asset["id"], "documents": 1})
        with store.db:
            store.db.execute(
                "INSERT INTO extracted_assets VALUES (?,?)", (asset["id"], EXTRACTION_VERSION)
            )
    return results
