import json
import unittest
from unittest.mock import patch

import test_distribution_release as fixtures
from explorer_ingest.distribution_sources import write_sources


class SourceMapTests(unittest.TestCase):
    def test_offline_complete_binding_and_reject_partial_or_unsafe_urls(self):
        case = fixtures.ReleaseTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build()
        manifest = case.root / "package/release.json"
        template = case.root / "template.json"
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            self.assertFalse(write_sources(manifest, template)["ready_for_fetch"])
            value = json.loads(template.read_text())
            self.assertTrue(all(v is None for v in value["artifacts"].values()))
            for key in value["artifacts"]:
                value["artifacts"][key] = {"drive_file_id": "synthetic-" + key}
            template.write_text(json.dumps(value))
            output = case.root / "sources.json"
            self.assertTrue(write_sources(manifest, output, template)["ready_for_fetch"])
            self.assertEqual(json.loads(output.read_text())["format"], "explorer-release-sources")
            key = next(iter(value["artifacts"]))
            value["artifacts"][key] = {"url": "https://evil.invalid/collect"}
            template.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                write_sources(manifest, case.root / "bad.json", template)
            del value["artifacts"][key]
            template.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                write_sources(manifest, case.root / "missing.json", template)
