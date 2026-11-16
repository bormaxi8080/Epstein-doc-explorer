"""Post-page native quality decisions and isolated PDFium parsing/rendering."""

import json
import os
import re
import sys
from types import SimpleNamespace

from .archive_limits import run_limited


def native_quality(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body = "\n".join(
        line for line in lines if not re.fullmatch(r"(?:EFTA[0-9]+|DOJ[-_A-Z0-9]+|[0-9]+)", line)
    )
    issues = []
    if len(body.strip()) < 40:
        issues.append("short_body")
    if text and text.count("\ufffd") / len(text) > 0.01:
        issues.append("replacement_characters")
    if len(lines) > 10 and len(set(lines)) / len(lines) < 0.2:
        issues.append("repetition")
    if body and sum(ch.isalnum() for ch in body) / len(body) < 0.3:
        issues.append("low_text_content")
    return {
        "accepted": not issues,
        "issues": issues,
        "characters": len(text),
        "body_characters": len(body),
    }


def parse(storage, path, action, output=None, page=1, dpi=200):
    limits = storage.limits
    if not 50 <= dpi <= 400 or page < 1 or action not in ("inspect", "extract", "render"):
        raise ValueError("Invalid parser operation/page/DPI")
    options = SimpleNamespace(
        validation_timeout=limits.worker_timeout, validation_memory_mb=limits.worker_memory_mb
    )
    payload = {
        "path": str(path),
        "action": action,
        "page": page,
        "dpi": dpi,
        "output": str(output) if output else None,
        "max_pixels": limits.max_pixels,
        "input_bytes": limits.max_input_bytes,
        "output_bytes": limits.max_artifact_bytes,
        "seconds": limits.worker_timeout,
        "memory_mb": limits.worker_memory_mb,
        "parent_pid": os.getpid(),
    }
    with storage.scratch_environment():
        result = run_limited(
            [sys.executable, "-m", "explorer_ingest.corpus_parse_worker"],
            payload,
            options,
            storage.check,
        )
    if output and (not output.is_file() or output.stat().st_size > limits.max_artifact_bytes):
        raise ValueError("Parser output exceeds its byte budget")
    return result


def inspect_document(corpus, doc_id):
    with corpus.original(doc_id) as path:
        result = parse(corpus.storage, path, "inspect")
    corpus.inspected(doc_id, result)
    return corpus.storage.put("inspection", result)


def extract_page(corpus, doc_id, page):
    with corpus.original(doc_id) as source, corpus.storage.temporary(".json") as output:
        parse(corpus.storage, source, "extract", output, page)
        result = json.loads(output.read_text())
        result["quality"] = native_quality(result["text"])
        reference = corpus.storage.put("native", result)
    corpus.db.execute(
        "UPDATE corpus_pages SET native_ref=?,status=? WHERE doc_id=? AND page=?",
        (
            json.dumps(reference),
            "native_ready" if result["quality"]["accepted"] else "needs_ocr",
            doc_id,
            page,
        ),
    )
    return reference
