"""Real multimodal HTTP requests against the local FCC stand-in."""

import json
import os
import unittest
import urllib.request

from explorer_ingest.corpus_contracts import validate
from explorer_ingest.corpus_fcc import FCCConfig, FCCFailure, FCCMessages


class FCCTests(unittest.TestCase):
    def client(self, model="synthetic-ocr", **kwargs):
        return FCCMessages(
            FCCConfig(model, "test-1", base_url=os.environ["FCC_TEST_BASE_URL"], **kwargs)
        )

    def test_actual_image_blocks_and_usage_reach_server(self):
        base = os.environ["FCC_TEST_BASE_URL"]
        before = json.load(urllib.request.urlopen(base + "/calls"))["imageRequests"]
        result = self.client().generate("Transcribe.", b"\xff\xd8synthetic")
        validate("ocr", result["output"])
        after = json.load(urllib.request.urlopen(base + "/calls"))["imageRequests"]
        self.assertEqual(after, before + 1)
        self.assertEqual(result["usage"]["input_tokens"], 100)
        self.assertEqual(result["reported_model"], "synthetic-ocr")

    def test_truncation_malformed_and_http_errors_fail_closed(self):
        for model in ("ocr-truncated", "ocr-invalid-json", "failure-500"):
            with self.subTest(model=model), self.assertRaises(FCCFailure):
                self.client(model).generate("Transcribe.", b"jpeg")
        with self.assertRaises(FCCFailure) as error:
            self.client("failure-500").generate("Transcribe.", b"jpeg")
        self.assertTrue(error.exception.retryable)

    def test_image_response_limits_and_remote_http(self):
        with self.assertRaises(ValueError):
            self.client(max_image_bytes=1).generate("Transcribe.", b"jpeg")
        with self.assertRaisesRegex(FCCFailure, "budget"):
            self.client(max_response_bytes=1).generate("Transcribe.", b"jpeg")
        with self.assertRaises(ValueError):
            FCCConfig("model", "revision", base_url="http://outside.invalid")

    def test_envelope_is_recorded_once_before_structured_validation(self):
        from unittest.mock import patch

        recorded = []
        client = self.client()
        client.recorder = recorded.append
        with patch("explorer_ingest.corpus_fcc.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = (
                b"invalid-envelope"
            )
            with self.assertRaises(FCCFailure):
                client.generate("Synthetic")
        self.assertEqual(recorded, [b"invalid-envelope"])

    def test_complete_fenced_json_without_prose_is_accepted(self):
        from unittest.mock import patch

        raw = json.dumps(
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '```json\n{"synthetic": true}\n```'}],
            }
        ).encode()
        with patch("explorer_ingest.corpus_fcc.urllib.request.build_opener") as opener:
            opener.return_value.open.return_value.__enter__.return_value.read.return_value = raw
            self.assertEqual(self.client().generate("Synthetic")["output"], {"synthetic": True})

    def test_http_error_diagnostics_are_retained_without_logging_payload(self):
        recorded = []
        client = self.client("failure-500")
        client.recorder = recorded.append
        with self.assertRaises(FCCFailure):
            client.generate("Synthetic")
        self.assertEqual(json.loads(recorded[0])["http_error"], 500)
