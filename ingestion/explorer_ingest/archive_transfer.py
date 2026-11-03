"""Explicit binary transfers, safe HTTP resume and offline archive verification."""

import codecs
import hashlib
import http.client
import json
import math
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from pypdf.errors import PyPdfError

from .archive_catalog import (
    CONTROL,
    StorageUnavailable,
    artifact_json,
    atomic_json,
    report,
    sync_directory,
)
from .archive_contracts import read_artifact, validate_artifact
from .archive_faults import boundary
from .archive_limits import ValidationBudgetError, run_limited
from .state import digest_file
from .transport import tls_context


class IntegrityError(ValueError):
    """The response does not represent the expected original document."""


class StoragePause(StorageUnavailable):
    """Keep durable partials; stop before exhausting the selected filesystem."""


@dataclass(frozen=True)
class Options:
    allow_hosts: tuple = ("www.justice.gov", "justice.gov")
    allow_http: bool = False
    timeout: float = 60
    retries: int = 2
    max_file_bytes: int = 64 * 1024**3
    reserve_bytes: int = 1024**3
    chunk_bytes: int = 1024**2
    request_delay: float = 1
    retry_delay: float = 1
    max_zip_bytes: int = 20 * 1024**3
    max_zip_members: int = 1000000
    validation_timeout: float = 30
    validation_memory_mb: int = 512

    def __post_init__(self):
        if (
            not isinstance(self.allow_hosts, (tuple, list))
            or not self.allow_hosts
            or any(not isinstance(h, str) or "/" in h for h in self.allow_hosts)
        ):
            raise ValueError("allow_hosts must contain explicit hostnames")
        if type(self.allow_http) is not bool or any(
            type(value) is not int
            for value in (
                self.retries,
                self.max_file_bytes,
                self.reserve_bytes,
                self.chunk_bytes,
                self.max_zip_bytes,
                self.max_zip_members,
                self.validation_memory_mb,
            )
        ):
            raise ValueError(
                "Byte/member/retry limits must be integers; allow_http must be boolean"
            )
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in (
                self.timeout,
                self.request_delay,
                self.retry_delay,
                self.validation_timeout,
            )
        ):
            raise ValueError("Timeouts and delays must be finite numbers")
        if (
            not 0 <= self.retries <= 10
            or self.timeout <= 0
            or self.reserve_bytes < 0
            or self.validation_timeout <= 0
            or self.validation_memory_mb < 16
        ):
            raise ValueError("Invalid retry, timeout or reserve settings")
        if (
            min(self.max_file_bytes, self.chunk_bytes, self.max_zip_bytes, self.max_zip_members)
            <= 0
        ):
            raise ValueError("Byte/member limits must be positive")
        if min(self.request_delay, self.retry_delay) < 0:
            raise ValueError("Request delays cannot be negative")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(exc):
    if isinstance(exc, (IntegrityError, ValidationBudgetError)):
        return type(exc).__name__ + ": " + str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        return "HTTP " + str(exc.code)
    return type(exc).__name__


def storage_check(archive, options, required=0):
    archive.check()
    if shutil.disk_usage(archive.root).free < options.reserve_bytes + required:
        raise StoragePause("External storage reserve would be exceeded")


def checked_url(value, options):
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise IntegrityError("Invalid download URL")
    if parsed.scheme == "http" and not options.allow_http:
        raise IntegrityError("HTTP requires explicit allow_http configuration")
    if parsed.hostname.lower() not in {host.lower() for host in options.allow_hosts}:
        raise IntegrityError("Download/redirect hostname is not allowed")


def validate_content(path, metadata, options, check=lambda: None):
    response = run_limited(
        [sys.executable, "-m", "explorer_ingest.archive_validation_worker"],
        {
            "path": str(path),
            "metadata": metadata,
            "options": asdict(options),
            "parent_pid": os.getpid(),
        },
        options,
        check,
    )
    if "error_type" in response:
        if response["error_type"] == "BadZipFile":
            raise zipfile.BadZipFile("ZIP content validation failed")
        if response["error_type"] == "MemoryError":
            raise ValidationBudgetError("Content validator exhausted its memory budget")
        raise IntegrityError(response.get("message", "Content validation failed"))
    return validate_artifact("content_validation", response["result"])


def _validate_content(path, metadata, options, check=lambda: None):
    with path.open("rb") as stream:
        prefix = stream.read(1024)
    kind, name = metadata["file_kind"], metadata["original_name"].lower()
    if kind not in ("other",) and prefix.lstrip().lower().startswith((b"<!doctype html", b"<html")):
        raise IntegrityError("HTML gate/error page received instead of an original file")
    if kind == "pdf" or name.endswith(".pdf"):
        from pypdf import PdfReader

        if b"%PDF-" not in prefix:
            raise IntegrityError("PDF signature mismatch")
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            if b"%%EOF" not in stream.read():
                raise IntegrityError("PDF end marker is missing")
        with path.open("rb") as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                return {"method": "encrypted_pdf_header", "fully_checked": False}
            count = len(reader.pages)
        return {"method": "pdf_structure", "fully_checked": True, "pages": count}
    if name.endswith((".zip", ".docx", ".xlsx", ".pptx")) or prefix.startswith(b"PK\x03\x04"):
        expanded = 0
        with zipfile.ZipFile(path) as archive_file:
            members = archive_file.infolist()
            if (
                len(members) > options.max_zip_members
                or sum(x.file_size for x in members) > options.max_zip_bytes
            ):
                raise IntegrityError("ZIP verification budget exceeded")
            for member in members:
                check()
                parts = PurePosixPath(member.filename.replace("\\", "/"))
                if parts.is_absolute() or ".." in parts.parts or member.flag_bits & 1:
                    raise IntegrityError("ZIP has unsafe paths or encrypted members")
                if member.is_dir():
                    continue
                with archive_file.open(member) as stream:
                    while block := stream.read(options.chunk_bytes):
                        expanded += len(block)
                        if expanded > options.max_zip_bytes:
                            raise IntegrityError("ZIP verification budget exceeded")
                        check()
        return {"method": "zip_crc", "fully_checked": True, "members": len(members)}
    if name.endswith((".txt", ".csv", ".json", ".md", ".dat", ".opt")):
        if name.endswith((".dat", ".opt")):
            return {"method": "preserved_load_file", "fully_checked": False}
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        with path.open("rb") as stream:
            while block := stream.read(options.chunk_bytes):
                decoder.decode(block)
                check()
        decoder.decode(b"", final=True)
        return {"method": "utf8_stream", "fully_checked": True}
    signatures = {
        "image": (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"II*\x00",
            b"MM\x00*",
            b"GIF8",
            b"RIFF",
            b"BM",
        ),
        "audio": (b"ID3", b"RIFF", b"OggS", b"fLaC", b"\xff"),
        "video": (b"RIFF", b"\x1aE\xdf\xa3"),
    }
    if kind in signatures:
        valid = (
            prefix.startswith(signatures[kind])
            or kind in ("audio", "video")
            and prefix[4:8] == b"ftyp"
        )
        if not valid:
            raise IntegrityError("Media signature mismatch; decoder review is required")
        return {"method": "media_signature_only", "fully_checked": False}
    return {"method": "opaque_original", "fully_checked": False}


def artifact_paths(archive, row):
    key = hashlib.sha256(row["id"].encode()).hexdigest()
    return (
        archive.path(f"{CONTROL}/partials/{key}.part"),
        archive.path(f"{CONTROL}/partials/{key}.json"),
        archive.path(f"{CONTROL}/receipts/{key}.json"),
    )


def mark_downloaded(archive, row, receipt, receipt_path, options):
    archive.check()
    for placement in archive.db.execute("SELECT * FROM placements WHERE asset_id=?", (row["id"],)):
        storage_check(archive, options)
        path = archive.path(placement["relative_path"])
        archive.make_parent(path)
        artifact_json(
            path,
            "reference",
            {
                "format_version": 1,
                "asset_id": row["id"],
                "node_id": placement["node_id"],
                "source_sha256": archive.meta("source_sha256"),
                "original": row["relative_path"],
                "sha256": receipt["sha256"],
                "receipt": str(receipt_path.relative_to(archive.root)),
            },
        )
    boundary("references_committed")
    with archive.db:
        archive.db.execute(
            """UPDATE assets SET status='downloaded',sha256=?,actual_bytes=?,validation=?,receipt=?,error=NULL WHERE id=?""",
            (
                receipt["sha256"],
                receipt["actual_bytes"],
                json.dumps(receipt["content_validation"]),
                json.dumps(receipt),
                row["id"],
            ),
        )
    boundary("ledger_committed")


def recover_publication(archive, row, options):
    partial, sidecar, receipt_path = artifact_paths(archive, row)
    if not receipt_path.exists():
        return False
    receipt = read_artifact(receipt_path, "receipt")
    if receipt.get("asset_id") != row["id"] or receipt.get("source_sha256") != archive.meta(
        "source_sha256"
    ):
        raise IntegrityError("Receipt belongs to another source")
    destination = archive.path(row["relative_path"])
    if row["status"] == "downloaded" and destination.exists():
        return True
    candidate = destination if destination.exists() else partial
    if not candidate.exists() or digest_file(candidate) != receipt["sha256"]:
        raise IntegrityError("Published/committing file checksum mismatch; run verify")
    if not destination.exists():
        archive.make_parent(destination)
        candidate.replace(destination)
        sync_directory(destination.parent)
    boundary("original_published")
    mark_downloaded(archive, row, receipt, receipt_path, options)
    sidecar.unlink(missing_ok=True)
    return True


def receive(archive, row, options):
    metadata = json.loads(row["metadata"])
    partial, sidecar, receipt_path = artifact_paths(archive, row)
    for path in (partial, receipt_path):
        archive.make_parent(path)
    destination = archive.path(row["relative_path"])
    if destination.exists():
        raise IntegrityError("Unreceipted destination exists; it will not be overwritten")
    expected = row["expected_bytes"]
    if expected is not None and expected > options.max_file_bytes:
        raise IntegrityError("File exceeds configured max_file_bytes")
    previous = read_artifact(sidecar, "partial") if sidecar.exists() else {}
    offset = partial.stat().st_size if partial.exists() else 0
    etag = previous.get("etag", "") or ""
    if (
        previous.get("source_sha256") != archive.meta("source_sha256")
        or previous.get("asset_id") != row["id"]
    ):
        offset = 0
    if not (etag.startswith('"') and etag.endswith('"')):
        offset = 0
    hasher = hashlib.sha256()
    if offset:
        checkpoint = previous.get("checkpoint_bytes", 0)
        if type(checkpoint) is not int or not 0 < checkpoint <= offset:
            offset = 0
        else:
            with partial.open("rb") as stream:
                remaining = checkpoint
                while remaining:
                    block = stream.read(min(options.chunk_bytes, remaining))
                    if not block:
                        raise IntegrityError("Partial file changed while checking its prefix")
                    hasher.update(block)
                    remaining -= len(block)
            if hasher.hexdigest() != previous.get("checkpoint_sha256"):
                offset = 0
            else:
                offset = checkpoint
                with partial.open("r+b") as stream:
                    stream.truncate(offset)
    if expected is not None and offset >= expected:
        # A complete partial without a receipt may precede validation/publication.
        # Restart safely instead of requesting an impossible bytes=N- range.
        offset = 0
    storage_check(archive, options, max(0, (expected or options.chunk_bytes) - offset))
    url = metadata.get("download_url") or metadata["url"]
    checked_url(url, options)
    headers = {"Accept-Encoding": "identity", "User-Agent": "EpsteinArchiveDownloader/1"}
    if offset:
        headers.update({"Range": f"bytes={offset}-", "If-Range": etag})

    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, response_headers, newurl):
            checked_url(newurl, options)
            if urlsplit(request.full_url).scheme == "https" and urlsplit(newurl).scheme != "https":
                raise IntegrityError("HTTPS downgrade rejected")
            return super().redirect_request(request, fp, code, msg, response_headers, newurl)

    opener = urllib.request.build_opener(
        Redirect(), urllib.request.HTTPSHandler(context=tls_context())
    )
    with opener.open(
        urllib.request.Request(url, headers=headers), timeout=options.timeout
    ) as response:
        checked_url(response.geturl(), options)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            raise IntegrityError("Encoded transfer cannot be compared with inventory bytes")
        length = response.headers.get("Content-Length")
        length = int(length) if length is not None else None
        new_etag = response.headers.get("ETag")
        if response.status == 206:
            match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
            )
            if not offset or not match:
                raise IntegrityError("Unexpected or malformed partial response")
            start, end, total = map(int, match.groups())
            if (
                start != offset
                or end < start
                or end >= total
                or new_etag != etag
                or expected is not None
                and total != expected
                or length is not None
                and length != end - start + 1
            ):
                sidecar.unlink(missing_ok=True)
                raise IntegrityError("Range response identity/size mismatch")
            target = total
        elif response.status == 200:
            offset = 0  # Range ignored or representation changed: restart, never append.
            hasher = hashlib.sha256()
            target = expected if expected is not None else length
            if expected is not None and length is not None and length != expected:
                raise IntegrityError("Server size differs from inventory")
        else:
            raise IntegrityError("Unexpected response status")
        if target is not None and target > options.max_file_bytes:
            raise IntegrityError("Response exceeds configured size budget")
        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if mime == "text/html" and metadata["file_kind"] != "other":
            raise IntegrityError("HTML gate/error response instead of a binary original")
        partial_metadata = {
            "asset_id": row["id"],
            "source_sha256": archive.meta("source_sha256"),
            "etag": new_etag,
            "last_modified": response.headers.get("Last-Modified"),
            "expected_bytes": target,
            "checkpoint_bytes": offset,
            "checkpoint_sha256": hasher.hexdigest(),
        }
        artifact_json(sidecar, "partial", partial_metadata)
        boundary("partial_checkpoint")
        received, last_progress = offset, time.monotonic()
        with partial.open("ab" if offset else "wb") as output:
            try:
                while block := response.read(options.chunk_bytes):
                    storage_check(archive, options, len(block))
                    received += len(block)
                    if (
                        received > options.max_file_bytes
                        or target is not None
                        and received > target
                    ):
                        raise IntegrityError("Response exceeds expected byte count")
                    output.write(block)
                    hasher.update(block)
                    if time.monotonic() - last_progress >= 5:
                        output.flush()
                        os.fsync(output.fileno())
                        partial_metadata.update(
                            checkpoint_bytes=received, checkpoint_sha256=hasher.hexdigest()
                        )
                        artifact_json(sidecar, "partial", partial_metadata)
                        boundary("partial_checkpoint")
                        artifact_json(
                            archive.path(f"{CONTROL}/progress.json"),
                            "progress",
                            {
                                "asset_id": row["id"],
                                "bytes": received,
                                "phase": "download",
                                "at": utc_now(),
                            },
                        )
                        last_progress = time.monotonic()
            finally:
                output.flush()
                os.fsync(output.fileno())
                partial_metadata.update(
                    checkpoint_bytes=received, checkpoint_sha256=hasher.hexdigest()
                )
                artifact_json(sidecar, "partial", partial_metadata)
                boundary("partial_checkpoint")
        if target is not None and received != target:
            raise http.client.IncompleteRead(b"", target - received)
        content = validate_content(
            partial, metadata, options, lambda: storage_check(archive, options)
        )
        sha = digest_file(partial)
        if metadata.get("sha256") and sha != metadata["sha256"]:
            raise IntegrityError("Source SHA-256 mismatch")
        receipt = {
            "format_version": 1,
            "asset_id": row["id"],
            "source_sha256": archive.meta("source_sha256"),
            "source_url": metadata["url"],
            "requested_url": url,
            "final_url": response.geturl(),
            "relative_path": row["relative_path"],
            "expected_bytes": expected,
            "actual_bytes": received,
            "sha256": sha,
            "etag": new_etag,
            "last_modified": response.headers.get("Last-Modified"),
            "mime_type": mime,
            "downloaded_at": utc_now(),
            "content_validation": content,
            "source_checksum_verified": bool(metadata.get("sha256")),
        }
    archive.check()
    # Receipt first: a crash before/after rename is reconciled without a second GET.
    artifact_json(receipt_path, "receipt", receipt)
    boundary("receipt_committed")
    archive.make_parent(destination)
    partial.replace(destination)
    sync_directory(destination.parent)
    boundary("original_published")
    mark_downloaded(archive, row, receipt, receipt_path, options)
    sidecar.unlink(missing_ok=True)


def fetch(archive, options, limit=0, allow_partial=False):
    archive.ready()
    if not archive.meta("source_certified") and not allow_partial:
        raise ValueError("Fetching a pilot partial plan requires --allow-partial")
    with archive.db:
        archive.set_meta("last_full_audit", None)
    archive.path(f"{CONTROL}/verified-files.jsonl").unlink(missing_ok=True)
    rows = archive.db.execute("SELECT * FROM assets WHERE status!='downloaded' ORDER BY id")
    processed = 0
    for row in rows:
        if limit and processed >= limit:
            break
        archive.check()
        try:
            recovered = recover_publication(archive, row, options)
        except (OSError, ValueError) as exc:
            archive.check()
            with archive.db:
                archive.db.execute(
                    "UPDATE assets SET status='failed',error=? WHERE id=?",
                    (safe_error(exc), row["id"]),
                )
            processed += 1
            continue
        if recovered:
            processed += 1
            continue
        for attempt in range(options.retries + 1):
            storage_check(archive, options)
            with archive.db:
                archive.db.execute(
                    "UPDATE assets SET status='downloading',attempts=attempts+1,error=NULL WHERE id=?",
                    (row["id"],),
                )
            try:
                receive(archive, row, options)
                break
            except StorageUnavailable:
                raise
            except (
                OSError,
                ValueError,
                http.client.HTTPException,
                zipfile.BadZipFile,
                PyPdfError,
            ) as exc:
                archive.check()
                retryable = not isinstance(exc, (IntegrityError, ValidationBudgetError))
                delay = options.retry_delay * (2**attempt)
                if isinstance(exc, urllib.error.HTTPError):
                    retryable = exc.code in (408, 416, 429, 500, 502, 503, 504)
                    value = exc.headers.get("Retry-After", "")
                    if value.isdigit():
                        delay = float(value)
                    if exc.code == 416:
                        artifact_paths(archive, row)[1].unlink(missing_ok=True)
                    exc.close()
                if isinstance(exc, OSError) and exc.errno == 28:
                    raise StoragePause("External disk is full; partial transfer retained") from exc
                with archive.db:
                    archive.db.execute(
                        "UPDATE assets SET status='failed',error=? WHERE id=?",
                        (safe_error(exc), row["id"]),
                    )
                if not retryable or attempt == options.retries:
                    break
                time.sleep(min(delay, 30))
        processed += 1
        if options.request_delay:
            time.sleep(options.request_delay)
    return report(archive)


def verify(archive, options, limit=0):
    archive.ready()
    if (
        archive.db.execute("PRAGMA quick_check").fetchone()[0] != "ok"
        or archive.db.execute("PRAGMA foreign_key_check").fetchone()
    ):
        raise IntegrityError("Download ledger failed SQLite integrity checks")
    source = archive.path(f"{CONTROL}/inputs/{archive.meta('source_sha256')}.json")
    if digest_file(source) != archive.meta("source_sha256"):
        raise IntegrityError("Stored source inventory checksum mismatch")
    with archive.db:
        archive.set_meta("last_full_audit", None)
    failures = checked = fully_checked = 0
    manifest = archive.path(f"{CONTROL}/verified-files.jsonl")
    manifest.unlink(missing_ok=True)
    temporary = manifest.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        for row in archive.db.execute("SELECT * FROM assets ORDER BY id"):
            if limit and checked >= limit:
                break
            storage_check(archive, options)
            try:
                partial, sidecar, receipt_path = artifact_paths(archive, row)
                recover_publication(archive, row, options)
                receipt = read_artifact(receipt_path, "receipt")
                path = archive.path(row["relative_path"])
                if (
                    path.stat().st_size != receipt["actual_bytes"]
                    or row["expected_bytes"] is not None
                    and path.stat().st_size != row["expected_bytes"]
                ):
                    raise IntegrityError("Offline size mismatch")
                content = validate_content(
                    path,
                    json.loads(row["metadata"]),
                    options,
                    lambda: storage_check(archive, options),
                )
                sha = digest_file(path)
                metadata = json.loads(row["metadata"])
                if sha != receipt["sha256"] or metadata.get("sha256") and sha != metadata["sha256"]:
                    raise IntegrityError("Offline SHA-256 mismatch")
                fully_checked += content["fully_checked"]
                for placement in archive.db.execute(
                    "SELECT * FROM placements WHERE asset_id=?", (row["id"],)
                ):
                    reference = read_artifact(archive.path(placement["relative_path"]), "reference")
                    if (
                        reference["sha256"] != receipt["sha256"]
                        or reference["original"] != row["relative_path"]
                    ):
                        raise IntegrityError("Catalog reference mismatch")
                mark_downloaded(archive, row, receipt, receipt_path, options)
                output.write(
                    json.dumps(
                        validate_artifact(
                            "manifest-row",
                            {
                                "format_version": 1,
                                "asset_id": row["id"],
                                "relative_path": row["relative_path"],
                                "sha256": receipt["sha256"],
                                "bytes": receipt["actual_bytes"],
                                "metadata": json.loads(row["metadata"]),
                                "receipt": str(receipt_path.relative_to(archive.root)),
                            },
                        )
                    )
                    + "\n"
                )
            except StorageUnavailable:
                raise
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, PyPdfError) as exc:
                failures += 1
                archive.check()
                with archive.db:
                    archive.db.execute(
                        "UPDATE assets SET status='failed',error=? WHERE id=?",
                        (safe_error(exc), row["id"]),
                    )
            checked += 1
        output.flush()
        os.fsync(output.fileno())
    total = archive.db.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
    passed = not limit and checked == total and not failures
    if passed:
        temporary.replace(manifest)
        boundary("audit_manifest_published")
        sync_directory(manifest.parent)
    else:
        temporary.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
    with archive.db:
        archive.set_meta(
            "last_full_audit",
            {
                "at": utc_now(),
                "passed": passed,
                "checked": checked,
                "failures": failures,
                "all_content_fully_checked": fully_checked == total,
                "manifest_sha256": digest_file(manifest) if passed else None,
            },
        )
    return report(archive)


def quarantine(archive, asset_id):
    """Explicitly preserve a suspect artifact and reset only that asset for retry."""
    import uuid

    archive.ready()
    row = archive.db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown asset ID")
    directory = archive.path(f"{CONTROL}/quarantine/{uuid.uuid4().hex}")
    archive.make_parent(directory / "provenance.json")
    original = archive.path(row["relative_path"])
    for index, path in enumerate([original, *artifact_paths(archive, row)]):
        if path.exists():
            path.replace(directory / f"{index}-{path.name}")
    atomic_json(
        directory / "provenance.json",
        {
            "asset_id": asset_id,
            "relative_path": row["relative_path"],
            "source_sha256": archive.meta("source_sha256"),
            "at": utc_now(),
        },
    )
    with archive.db:
        archive.db.execute(
            "UPDATE assets SET status='pending',sha256=NULL,actual_bytes=NULL,validation=NULL,receipt=NULL,error=NULL WHERE id=?",
            (asset_id,),
        )
        archive.set_meta("last_full_audit", None)
    archive.path(f"{CONTROL}/verified-files.jsonl").unlink(missing_ok=True)
    return {
        "asset_id": asset_id,
        "status": "pending",
        "quarantine": str(directory.relative_to(archive.root)),
    }
