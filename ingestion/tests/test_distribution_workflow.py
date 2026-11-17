import json
import unittest
from unittest.mock import patch

import test_distribution_release as fixtures
from explorer_ingest.distribution_install import import_release
from explorer_ingest.distribution_workflow import prepare_workflow


class WorkflowTests(unittest.TestCase):
    def test_preparation_emits_explicit_commands_without_starting_work(self):
        case = fixtures.ReleaseTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build()
        installation = case.root / "installed"
        import_release(
            case.root / "package", installation, groups=("inventory-parts",), reserve_bytes=0
        )
        root = case.root / "workspace"
        root.mkdir()
        roles = dict(
            packages="packages",
            inventory="inventory",
            downloads="originals",
            corpus="corpus",
            releases="releases",
            scratch="tmp",
        )
        for name in roles.values():
            (root / name).mkdir()
        workspace = case.root / "workspace.json"
        workspace.write_text(
            json.dumps(
                dict(
                    format="explorer-workspace",
                    version=1,
                    root=str(root),
                    reserve_bytes=0,
                    paths=roles,
                )
            )
        )
        model = "claude-3-freecc-no-thinking/open_router/inclusionai/ling-3.2-flash:free"
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = prepare_workflow(workspace, installation, model, "test")
        self.assertFalse(result["executed_work"])
        self.assertIn("--manifest", result["commands"]["plan"])
        self.assertNotIn("--allow-fcc", result["commands"]["offline_work"])
        self.assertFalse((root / "corpus/corpus.sqlite3").exists())
        with self.assertRaisesRegex(ValueError, "differs"):
            prepare_workflow(workspace, installation, model, "changed")
