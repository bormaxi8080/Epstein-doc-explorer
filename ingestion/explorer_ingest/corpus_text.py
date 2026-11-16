"""Canonical UTF-8 document text and streaming, versioned page maps."""

import hashlib
import json
import os

from .archive_catalog import sync_directory
from .corpus_contracts import validate
from .state import digest_file


def pages(corpus, doc_id, allow_review=False):
    doc = corpus.document(doc_id)
    expected = doc["page_count"]
    if not expected:
        raise ValueError("Document has no registered pages")
    count = 0
    for row in corpus.db.execute(
        "SELECT * FROM corpus_pages WHERE doc_id=? ORDER BY page", (doc_id,)
    ):
        count += 1
        if row["page"] != count or not row["selected_ref"]:
            raise ValueError("Document pages are incomplete")
        reference = json.loads(row["selected_ref"])
        value = validate("page", corpus.storage.get(reference))
        if (
            value["doc_id"] != doc_id
            or value["page"] != count
            or value["source_sha256"] != doc["sha256"]
        ):
            raise ValueError("Page source identity mismatch")
        if value["quality"] != "accepted" and not allow_review:
            raise ValueError("Document has pages requiring review")
        yield value, reference
    if count != expected:
        raise ValueError("Document page count mismatch")


def publish_temp(storage, temporary, relative, sha):
    storage.check()
    if temporary.parent != storage.path("tmp"):
        raise ValueError("Publication requires managed scratch")
    target = storage.path(relative)
    storage.mkdir(target.parent.relative_to(storage.root))
    if target.exists():
        if digest_file(target) != sha:
            raise ValueError("Existing immutable text/map checksum mismatch")
    else:
        temporary.replace(target)
        sync_directory(target.parent)
    return {"path": relative, "sha256": sha}


def assemble_text(corpus, doc_id, allow_review=False):
    storage = corpus.storage
    digest = hashlib.sha256()
    count = offset = review = 0
    with storage.temporary(".txt") as temporary, storage.temporary(".jsonl") as map_file:
        with temporary.open("xb") as output, map_file.open("w", encoding="utf-8") as mapping:
            for value, reference in pages(corpus, doc_id, allow_review):
                count += 1
                marker = ("\n\n" if count > 1 else "") + f"[Page {count}]\n"
                text = value["text"]
                chunk = (marker + text).encode("utf-8")
                storage.check(len(chunk))
                if output.tell() + len(chunk) > storage.limits.max_scratch_bytes // 2:
                    raise ValueError("Canonical text exceeds scratch admission budget")
                output.write(chunk)
                digest.update(chunk)
                start = offset + len(marker)
                mapping.write(
                    json.dumps(
                        {
                            "page": count,
                            "start": start,
                            "end": start + len(text),
                            "page_ref": reference,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                offset = start + len(text)
                review += value["quality"] != "accepted"
            for stream in (output, mapping):
                stream.flush()
                os.fsync(stream.fileno())
        sha = digest.hexdigest()
        text_ref = publish_temp(storage, temporary, f"texts/{sha[:2]}/{doc_id}/{sha}.txt", sha)
        map_sha = digest_file(map_file)
        map_ref = publish_temp(
            storage, map_file, f"page-maps/{map_sha[:2]}/{map_sha}.jsonl", map_sha
        )
    doc = corpus.document(doc_id)
    artifact = {
        "format_version": 2,
        "doc_id": doc_id,
        "source_sha256": doc["sha256"],
        "text_sha256": sha,
        "pages_count": count,
        "relative_path": text_ref["path"],
        "page_map_ref": map_ref,
        "offset_unit": "unicode_codepoint",
        "review_required": review,
    }
    validate("text_v2", artifact)
    reference = storage.put("text-v2", artifact)
    old = json.loads(doc["text_ref"]) if doc["text_ref"] else None
    if old != reference:
        corpus.db.execute(
            """UPDATE corpus_documents SET text_ref=?,text_sha256=?,status=?,
            analysis_ref=NULL,approved_key=NULL WHERE doc_id=?""",
            (json.dumps(reference), sha, "text_review" if review else "text_ready", doc_id),
        )
    return reference


def read_text_artifact(corpus, doc_id):
    doc = corpus.document(doc_id)
    if not doc["text_ref"]:
        raise ValueError("Assemble document text first")
    artifact = validate("text_v2", corpus.storage.get(json.loads(doc["text_ref"])))
    path = corpus.storage.path(artifact["relative_path"])
    if digest_file(path) != artifact["text_sha256"]:
        raise ValueError("Canonical text checksum mismatch")
    mapping = corpus.storage.path(artifact["page_map_ref"]["path"])
    if digest_file(mapping) != artifact["page_map_ref"]["sha256"]:
        raise ValueError("Page map checksum mismatch")
    return artifact, path
