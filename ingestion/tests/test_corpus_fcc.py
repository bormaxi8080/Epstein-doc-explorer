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
