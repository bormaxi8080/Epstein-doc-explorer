"""Portable private pilot metadata/originals, independent of machine-bound job state."""

import fcntl
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from .analysis import validate_analysis
from .archive_catalog import CONTROL, artifact_json, log_event
from .archive_contracts import validate_artifact
from .archive_observer import ArchiveView
from .archive_selection import create_pilot
from .archive_transfer import artifact_paths, recover_publication, verify
from .corpus_contracts import document_id, validate
from .corpus_sources import selected_sources
from .distribution_contracts import ExternalDirectory, safe_path
from .distribution_io import durable, transfer
from .inventory_parts import fsync_directory
from .state import atomic_json, digest_file

MAX_METADATA_BYTES = 64 * 1024**2


def lines(path):
    with Path(path).open("rb") as f:
        while line := f.readline(16 * 1024**2 + 1):
            if len(line) > 16 * 1024**2:
                raise ValueError("Portable metadata row exceeds its budget")
            yield json.loads(line)


def read_ref(root, ref):
    path = safe_path(root, ref["path"])
    if path.stat().st_size > 16 * 1024**2 or digest_file(path) != ref["sha256"]:
        raise ValueError("Pilot artifact changed or exceeds its bound")
    return json.loads(path.read_text())


def export_pilot(selection, corpus_root, output, reserve_bytes=1024**3):
    selection, corpus_root, output = Path(selection), Path(corpus_root), Path(output).absolute()
    storage = ExternalDirectory(output.parent, reserve_bytes)
    if output.exists() or output.is_symlink():
        raise ValueError("Portable pilot output must be new")
    if selection.stat().st_size > 1024**2:
        raise ValueError("Frozen selection header exceeds its budget")
    header = json.loads(selection.read_text())
    if header.get("scope") != "pilot" or not 1 <= header["files_count"] <= 1000:
        raise ValueError("Portable pilot is limited to a frozen pilot of at most 1000 files")
    if selection.with_name("sources.jsonl").stat().st_size > MAX_METADATA_BYTES:
        raise ValueError("Pilot source metadata exceeds budget")
    records = [record for _, record in selected_sources(selection)]
    source_storage = ExternalDirectory(header["archive_root"], reserve_bytes=0)
    with closing(ArchiveView(header["archive_root"])) as archive:
        if archive.marker["uuid"] != header["archive_uuid"]:
            raise ValueError("Pilot origin archive changed")
        layout = archive.meta("layout") or "url"
    corpus_storage = ExternalDirectory(corpus_root, reserve_bytes=0)
    with corpus_storage.path("operator.lock").open("rb") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Pause the corpus writer before portable export") from None
        with tempfile.TemporaryDirectory(prefix=".pilot-export-", dir=output.parent) as temporary:
            stage = Path(temporary) / "pilot"
            stage.mkdir()
            with closing(
                sqlite3.connect(
                    "file:" + quote(str(corpus_root / "corpus.sqlite3")) + "?mode=ro", uri=True
                )
            ) as db:
                db.row_factory = sqlite3.Row
                seen, pages_count, analyses_count = set(), 0, 0
                with (
                    (stage / "sources.jsonl").open("w") as sources,
                    (stage / "documents.jsonl").open("w") as docs,
                    (stage / "pages.jsonl").open("w") as pages,
                    (stage / "analyses.jsonl").open("w") as analyses,
                ):
                    for record in records:
                        storage.check()
                        source_storage.check()
                        corpus_storage.check()
                        original = source_storage.path(record["relative_path"])
                        if (
                            original.stat().st_size != record["bytes"]
                            or digest_file(original) != record["sha256"]
                        ):
                            raise ValueError("Frozen original changed")
                        validate_artifact("receipt", record["receipt_data"])
                        sources.write(json.dumps(record, ensure_ascii=False) + "\n")
                        doc_id = document_id(record["sha256"])
                        if doc_id in seen:
                            continue
                        seen.add(doc_id)
                        doc = db.execute(
                            "SELECT * FROM corpus_documents WHERE doc_id=?", (doc_id,)
                        ).fetchone()
                        if not doc or not doc["text_ref"] or not doc["analysis_ref"]:
                            raise ValueError("Pilot requires completed text and private analysis")
                        text_ref, analysis_ref = (
                            json.loads(doc["text_ref"]),
                            json.loads(doc["analysis_ref"]),
                        )
                        text = read_ref(corpus_root, text_ref)
                        result = read_ref(corpus_root, analysis_ref)
                        if (
                            result["text_ref"] != text_ref
                            or digest_file(safe_path(corpus_root, text["relative_path"]))
                            != text["text_sha256"]
                        ):
                            raise ValueError("Pilot analysis/text versions differ")
                        docs.write(
                            json.dumps(
                                dict(
                                    doc_id=doc_id,
                                    sha256=doc["sha256"],
                                    bytes=doc["bytes"],
                                    pages=doc["page_count"],
                                    text_sha256=text["text_sha256"],
                                    review_required=text["review_required"],
                                    summary=result["summary"],
                                ),
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                        for page in db.execute(
                            "SELECT * FROM corpus_pages WHERE doc_id=? ORDER BY page", (doc_id,)
                        ):
                            value = validate(
                                "page", read_ref(corpus_root, json.loads(page["selected_ref"]))
                            )
                            ocr = None
                            if value["provenance"].get("response"):
                                ocr = read_ref(corpus_root, value["provenance"]["response"])[
                                    "output"
                                ]
                                validate("ocr", ocr)
                            pages.write(
                                json.dumps(dict(selected_page=value, ocr=ocr), ensure_ascii=False)
                                + "\n"
                            )
                            pages_count += 1
                        mapping = safe_path(corpus_root, result["chunks_ref"]["path"])
                        if digest_file(mapping) != result["chunks_ref"]["sha256"]:
                            raise ValueError("Pilot analysis map changed")
                        for ref in lines(mapping):
                            chunk = read_ref(corpus_root, ref)
                            payload = chunk["payload"]
                            validate_analysis(chunk["output"], payload)
                            if (
                                payload["doc_id"] != doc_id
                                or payload["text_key"] != text_ref["sha256"]
                            ):
                                raise ValueError("Pilot chunk belongs to another text")
                            analyses.write(
                                json.dumps(
                                    dict(
                                        doc_id=doc_id,
                                        page=payload["page"],
                                        start=payload["start"],
                                        end=payload["start"] + len(payload["text"]),
                                        output=chunk["output"],
                                        provenance=dict(
                                            prompt=payload["prompt"],
                                            provider=payload["provider"],
                                            reported_model=chunk.get("reported_model"),
                                            usage=chunk.get("usage"),
                                            response_sha256=chunk.get("raw_sha256"),
                                        ),
                                    ),
                                    ensure_ascii=False,
                                )
                                + "\n"
                            )
                            analyses_count += 1
                        if sum(p.stat().st_size for p in stage.iterdir()) > MAX_METADATA_BYTES:
                            raise ValueError("Portable pilot metadata exceeds its budget")
                    for stream in (sources, docs, pages, analyses):
                        durable(stream)
            metadata = {
                name: dict(bytes=(stage / name).stat().st_size, sha256=digest_file(stage / name))
                for name in ("sources.jsonl", "documents.jsonl", "pages.jsonl", "analyses.jsonl")
            }
            portable = dict(
                format="explorer-portable-pilot",
                version=1,
                scope="pilot",
                export_complete=True,
                source_sha256=header["source_sha256"],
                selection_sha256=header["selection_sha256"],
                files_count=len(records),
                documents_count=len(seen),
                pages_count=pages_count,
                analyses_count=analyses_count,
                layout=layout,
                requires_review=True,
                files=metadata,
            )
            atomic_json(stage / "selection.json", portable)
            validate_pilot(stage, original_root=source_storage.root)
            storage.check()
            fsync_directory(stage)
            stage.rename(output)
            return dict(
                output=str(output),
                files=len(records),
                documents=len(seen),
                pages=pages_count,
                private=True,
                work_queues_copied=False,
                network_or_model_calls=False,
            )


def validate_pilot(root, original_root=None):
    root = Path(root)
    header = json.loads(safe_path(root, "selection.json").read_text())
    if (
        header.get("format") != "explorer-portable-pilot"
        or header.get("version") != 1
        or header.get("scope") != "pilot"
        or header.get("requires_review") is not True
        or not 1 <= header["files_count"] <= 1000
    ):
        raise ValueError("Invalid private pilot header")
    names = {"sources.jsonl", "documents.jsonl", "pages.jsonl", "analyses.jsonl"}
    if (
        set(header["files"]) != names
        or sum(v["bytes"] for v in header["files"].values()) > MAX_METADATA_BYTES
    ):
        raise ValueError("Invalid portable metadata membership/budget")
    for name, meta in header["files"].items():
        path = safe_path(root, name)
        if path.stat().st_size != meta["bytes"] or digest_file(path) != meta["sha256"]:
            raise ValueError("Portable pilot metadata checksum mismatch")
    sources = list(lines(root / "sources.jsonl"))
    digest = hashlib.sha256()
    seen = set()
    for item in sources:
        if item["asset_id"] in seen:
            raise ValueError("Duplicate pilot source")
        seen.add(item["asset_id"])
        digest.update((item["asset_id"] + "\n").encode())
        receipt = validate_artifact("receipt", item["receipt_data"])
        if (
            receipt["asset_id"] != item["asset_id"]
            or receipt["source_sha256"] != header["source_sha256"]
            or receipt["sha256"] != item["sha256"]
            or receipt["actual_bytes"] != item["bytes"]
            or receipt["relative_path"] != item["relative_path"]
        ):
            raise ValueError("Portable receipt/source binding mismatch")
        if original_root is not None:
            original = safe_path(original_root, item["relative_path"])
            if original.stat().st_size != item["bytes"] or digest_file(original) != item["sha256"]:
                raise ValueError("Portable original checksum mismatch")
    if len(sources) != header["files_count"] or digest.hexdigest() != header["selection_sha256"]:
        raise ValueError("Portable pilot membership differs")
    docs = {}
    source_docs = {document_id(i["sha256"]): (i["sha256"], i["bytes"]) for i in sources}
    for doc in lines(root / "documents.jsonl"):
        key = doc["doc_id"]
        if key in docs or source_docs.get(key) != (doc["sha256"], doc["bytes"]):
            raise ValueError("Portable document source binding differs")
        if type(doc["pages"]) is not int or not 1 <= doc["pages"] <= 10000:
            raise ValueError("Portable page budget invalid")
        validate_analysis(doc["summary"], {"page": 1, "text": ""})
        docs[key] = doc
    if len(docs) != header["documents_count"] or set(docs) != {
        document_id(i["sha256"]) for i in sources
    }:
        raise ValueError("Portable document membership differs")
    pages, review, counts = {}, {}, {}
    for row in lines(root / "pages.jsonl"):
        page = validate("page", row["selected_page"])
        key = page["doc_id"], page["page"]
        if key in pages or key[0] not in docs or page["source_sha256"] != docs[key[0]]["sha256"]:
            raise ValueError("Invalid portable page identity")
        if row["ocr"] is not None:
            validate("ocr", row["ocr"])
        pages[key] = page["text"]
        review[key[0]] = review.get(key[0], 0) + (page["quality"] != "accepted")
    if len(pages) != header["pages_count"]:
        raise ValueError("Portable page count differs")
    for doc_id, doc in docs.items():
        value = hashlib.sha256()
        for page in range(1, doc["pages"] + 1):
            marker = ("\n\n" if page > 1 else "") + f"[Page {page}]\n"
            value.update((marker + pages[doc_id, page]).encode())
        if value.hexdigest() != doc["text_sha256"] or review[doc_id] != doc["review_required"]:
            raise ValueError("Portable text/review provenance differs")
    intervals = {}
    for row in lines(root / "analyses.jsonl"):
        key = row["doc_id"], row["page"]
        source = pages[key]
        if not 0 <= row["start"] <= row["end"] <= len(source):
            raise ValueError("Invalid portable fragment range")
        validate_analysis(row["output"], dict(page=key[1], text=source[row["start"] : row["end"]]))
        if (row["start"], row["end"]) in intervals.get(key, []):
            raise ValueError("Duplicate portable analysis fragment")
        intervals.setdefault(key, []).append((row["start"], row["end"]))
        counts[row["doc_id"]] = counts.get(row["doc_id"], 0) + 1
    if sum(counts.values()) != header["analyses_count"]:
        raise ValueError("Portable analysis count differs")
    for key, text in pages.items():
        end = 0
        for start, stop in sorted(intervals.get(key, [])):
            if start > end:
                raise ValueError("Portable analysis coverage gap")
            end = max(end, stop)
        if end != len(text) or key not in intervals:
            raise ValueError("Incomplete portable analysis coverage")
    return header


def adopt_pilot(archive, root, options):
    archive.ready()
    root = Path(root).absolute()
    header = validate_pilot(root, original_root=root)
    if (
        archive.meta("source_sha256") != header["source_sha256"]
        or (archive.meta("layout") or "url") != header["layout"]
    ):
        raise ValueError("Plan the matching inventory and layout before pilot adoption")
    create_pilot(archive, header["files_count"])
    if archive.meta("pilot")["selection_sha256"] != header["selection_sha256"]:
        raise ValueError("Local pilot membership differs")
    records = list(lines(root / "sources.jsonl"))
    for record in records:
        row = archive.db.execute(
            "SELECT * FROM assets WHERE id=?", (record["asset_id"],)
        ).fetchone()
        if (
            not row
            or row["relative_path"] != record["relative_path"]
            or json.loads(row["metadata"]) != record["metadata"]
            or row["expected_bytes"] != record["bytes"]
        ):
            raise ValueError("Portable originals differ from the local plan")
    with archive.db:
        archive.set_meta("last_full_audit", None)
        archive.set_meta("pilot_audit", None)
    copied = 0
    for record in records:
        archive.check()
        row = archive.db.execute(
            "SELECT * FROM assets WHERE id=?", (record["asset_id"],)
        ).fetchone()
        source = safe_path(root, record["relative_path"])
        target = archive.path(row["relative_path"])
        if target.exists():
            if digest_file(target) != record["sha256"]:
                raise ValueError("Existing original differs; preserve it for review")
        else:
            archive.make_parent(target)
            temp = archive.path(
                f"{CONTROL}/tmp/adopt-{hashlib.sha256(row['id'].encode()).hexdigest()}.part"
            )
            storage = ExternalDirectory(archive.root, options.reserve_bytes)
            archive.make_parent(temp)
            with source.open("rb") as incoming, temp.open("wb") as outgoing:
                transfer(incoming, outgoing, storage, record["bytes"], record["sha256"])
                durable(outgoing)
            archive.check()
            temp.replace(target)
            copied += 1
        _, _, receipt_path = artifact_paths(archive, row)
        archive.make_parent(receipt_path)
        if not receipt_path.exists():
            artifact_json(receipt_path, "receipt", record["receipt_data"])
        recover_publication(archive, row, options)
    log_event(
        archive,
        "portable_pilot_import",
        files=len(records),
        copied=copied,
        source_sha256=header["source_sha256"],
        selection_sha256=header["selection_sha256"],
    )
    result = verify(archive, options, pilot=True)
    return result
