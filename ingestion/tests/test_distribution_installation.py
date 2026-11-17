import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from explorer_ingest.distribution_demo import demo_bundle
from explorer_ingest.distribution_doctor import diagnose
from explorer_ingest.distribution_release import verify_database


class InstallationTests(unittest.TestCase):
    def test_demo_is_explicitly_synthetic_and_database_is_portable(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "demo"
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                result = demo_bundle(path, reserve_bytes=0)
            self.assertTrue(result["synthetic"])
            self.assertFalse(result["network_or_model_calls"])
            verify_database(
                path / "document_analysis.db", "viewer-db", "public", dict(source_sha256="a" * 64)
            )
            with self.assertRaises(ValueError):
                demo_bundle(path, reserve_bytes=0)
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertFalse(manifest["include_unreviewed"])

    def test_processor_diagnostics_do_not_require_node_or_create_paths(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "external"
            root.mkdir()
            config = Path(d) / "workspace.json"
            config.write_text(
                json.dumps(
                    dict(
                        format="explorer-workspace",
                        version=1,
                        root=str(root),
                        reserve_bytes=0,
                        paths=dict(
                            packages="packages",
                            inventory="inventory",
                            downloads="downloads",
                            corpus="corpus",
                            releases="releases",
                            scratch="tmp",
                        ),
                    )
                )
            )
            with (
                patch("subprocess.run", side_effect=AssertionError("no process expected")),
                patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")),
            ):
                result = diagnose(config, component="processor")
            self.assertTrue(result["passed"])
            self.assertFalse(result["network_or_model_calls"])
            self.assertEqual(list(root.iterdir()), [])
