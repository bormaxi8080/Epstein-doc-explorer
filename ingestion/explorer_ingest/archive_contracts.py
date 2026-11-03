"""Versioned archive artifact contracts, including implicit-version legacy v1 rows."""

import json
from functools import lru_cache
from pathlib import Path, PurePosixPath

from jsonschema import Draft202012Validator, FormatChecker, ValidationError


class ArtifactError(ValueError):
    """A published artifact violates its versioned consumer contract."""


@lru_cache(maxsize=None)
def validator(kind):
    schema = json.loads(Path(__file__).with_name("archive-artifacts-v1.schema.json").read_text())
    if kind not in schema["$defs"]:
        raise ArtifactError("Unknown artifact kind")
    return Draft202012Validator(
        {"$defs": schema["$defs"], "$ref": f"#/$defs/{kind}"},
        format_checker=FormatChecker(),
    )


def validate_artifact(kind, value):
    if not isinstance(value, dict) or value.get("format_version", 1) != 1:
        raise ArtifactError(f"Unsupported {kind} artifact version")
    try:
        validator(kind).validate(value)
    except ValidationError as exc:
        raise ArtifactError(f"Invalid {kind} artifact ({exc.validator})") from None
    for key in ("relative_path", "receipt", "original"):
        if key not in value:
            continue
        path = value[key]
        if not isinstance(path, str) or not path or "\\" in path:
            raise ArtifactError(f"Invalid {kind} relative path")
        parsed = PurePosixPath(path)
        if parsed.is_absolute() or ".." in parsed.parts or ":" in parsed.parts[0]:
            raise ArtifactError(f"Unsafe {kind} relative path")
    if kind == "receipt" and value["expected_bytes"] is not None:
        if value["expected_bytes"] != value["actual_bytes"]:
            raise ArtifactError("Receipt size contradicts source metadata")
    if kind == "report":
        if sum(value["statuses"].values()) != value["total"]:
            raise ArtifactError("Report status totals disagree")
        downloaded = value["total"] > 0 and value["statuses"].get("downloaded", 0) == value["total"]
        if value["download_complete"] != downloaded:
            raise ArtifactError("Report download completeness contradicts its counts")
        audit = value["last_full_audit"] or {}
        if audit.get("passed") and (
            audit["failures"] or audit["checked"] != value["total"] or not audit["manifest_sha256"]
        ):
            raise ArtifactError("Report audit evidence is incomplete")
        if value["complete_at_last_audit"] and not (
            downloaded and value["source_inventory_complete"] and audit.get("passed")
        ):
            raise ArtifactError("Report claims unsupported completeness")
    return value


def read_artifact(path, kind):
    return validate_artifact(kind, json.loads(Path(path).read_text()))
