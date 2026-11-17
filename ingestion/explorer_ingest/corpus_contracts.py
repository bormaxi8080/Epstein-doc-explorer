"""Versioned corpus artifacts and stable identities, independent of model calls."""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

CORPUS_VERSION = 1
OCR_PROMPT_VERSION = "transcribe-1"
ANALYSIS_PROMPT_VERSION = "corpus-evidence-4"
SUPPORTED_ANALYSIS_PROMPTS = ("corpus-evidence-3", ANALYSIS_PROMPT_VERSION)
STAGES = ("inspect", "extract", "render", "ocr", "text", "analyze", "assemble", "tags", "export")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def document_id(source_sha256):
    if not re.fullmatch(r"[a-f0-9]{64}", source_sha256):
        raise ValueError("Invalid source SHA-256")
    return "DOJ_" + source_sha256[:40]


def cache_key(stage, source_sha256, parameters, page=None):
    if stage not in STAGES or (page is not None and (type(page) is not int or page < 1)):
        raise ValueError("Invalid corpus stage/page")
    document_id(source_sha256)
    return fingerprint([CORPUS_VERSION, stage, source_sha256, page, parameters])


@lru_cache(maxsize=None)
def validator(kind):
    schema = json.loads(Path(__file__).with_name("corpus-v1.schema.json").read_text())
    if kind not in schema["$defs"]:
        raise ValueError("Unknown corpus artifact")
    return Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/" + kind})


def validate(kind, value):
    validator(kind).validate(value)
    if kind == "page" and value["doc_id"] != document_id(value["source_sha256"]):
        raise ValueError("Page identity does not match its source")
    return value
