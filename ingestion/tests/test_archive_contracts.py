"""Contract compatibility and hostile artifact regression checks."""

import json
import unittest
from pathlib import Path

from explorer_ingest.archive_contracts import ArtifactError, validate_artifact, validator


class ContractTests(unittest.TestCase):
    def test_previous_v1_fixtures_and_false_completeness(self):
        directory = Path(__file__).parent / "fixtures" / "archive-v1"
        for kind in ("receipt", "manifest-row", "report"):
            value = json.loads((directory / (kind + ".json")).read_text())
            validate_artifact(kind, value)
        value = json.loads((directory / "report.json").read_text())
        value["complete_at_last_audit"] = True
        with self.assertRaises(ArtifactError):
            validate_artifact("report", value)

    def test_schemas_are_valid(self):
        for kind in (
            "receipt",
            "manifest-row",
            "report",
            "partial",
            "identity",
            "reference",
            "progress",
        ):
            validator(kind).check_schema(validator(kind).schema)

    def test_legacy_v1_manifest_row_accepts_additive_fields(self):
        row = {
            "asset_id": "original",
            "relative_path": "archive-files/file.pdf",
            "sha256": "a" * 64,
            "bytes": 10,
            "metadata": {},
            "receipt": ".pw010-download/receipts/a.json",
            "future_optional": True,
        }
        self.assertEqual(validate_artifact("manifest-row", row), row)
        explicit = {**row, "format_version": 1}
        self.assertEqual(validate_artifact("manifest-row", explicit), explicit)
        for path in ("../escape", "/absolute", "C:/drive", "folder\\file"):
            bad = {**row, "relative_path": path}
            with self.assertRaises(ArtifactError):
                validate_artifact("manifest-row", bad)
        with self.assertRaisesRegex(ArtifactError, "Unsupported"):
            validate_artifact("manifest-row", {**row, "format_version": 2})

    def test_missing_and_wrong_types_are_rejected(self):
        value = {"format_version": 1, "uuid": "a" * 32}
        self.assertEqual(validate_artifact("identity", value), value)
        for invalid in ({}, {**value, "uuid": 123}, {**value, "format_version": 2}):
            with self.assertRaises(ArtifactError):
                validate_artifact("identity", invalid)
