"""Corpus contracts protect stable IDs and extraction/analysis cache boundaries."""

import unittest

from explorer_ingest.corpus_contracts import cache_key, document_id, validate
from jsonschema import ValidationError


class ContractTests(unittest.TestCase):
    def test_identity_and_cache_invalidate_only_derived_versions(self):
        sha = "a" * 64
        self.assertEqual(document_id(sha), "DOJ_" + "a" * 40)
        one = cache_key("ocr", sha, {"model": "one", "render": 1}, 1)
        self.assertNotEqual(one, cache_key("ocr", sha, {"model": "two", "render": 1}, 1))
        self.assertNotEqual(one, cache_key("ocr", sha, {"model": "one", "render": 2}, 1))
        self.assertNotEqual(one, cache_key("ocr", sha, {"model": "one", "render": 1}, 2))
        with self.assertRaises(ValueError):
            document_id("untrusted filename")

    def test_page_identity_and_required_provenance(self):
        value = {
            "format_version": 1,
            "doc_id": document_id("a" * 64),
            "source_sha256": "a" * 64,
            "page": 1,
            "method": "native",
            "text": "",
            "quality": "needs_review",
            "provenance": {"extractor": "test"},
        }
        validate("page", value)
        with self.assertRaises(ValueError):
            validate("page", {**value, "source_sha256": "b" * 64})
        value.pop("provenance")
        with self.assertRaises(ValidationError):
            validate("page", value)

    def test_ocr_does_not_accept_analysis_or_invalid_coordinates(self):
        value = {
            "text": "Visible text",
            "page_kind": "document",
            "needs_review": False,
            "blocks": [{"text": "Visible text", "kind": "paragraph", "bbox": None}],
        }
        validate("ocr", value)
        with self.assertRaises(ValidationError):
            validate("ocr", {**value, "rdf_triples": []})
        value["blocks"][0]["bbox"] = [0, 0, 1001, 2]
        with self.assertRaises(ValidationError):
            validate("ocr", value)
