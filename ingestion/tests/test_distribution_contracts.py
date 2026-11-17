import copy
import json
import tempfile
import unittest
from pathlib import Path

from explorer_ingest.distribution_contracts import (
    ExternalDirectory,
    load_workspace,
    validate_release,
)


def release():
    return dict(
        format="epstein-explorer-release",
        version=1,
        release_id="synthetic-1",
        minimum_app_version="1.1.0",
        created_at="2026-11-17T00:00:00Z",
        visibility="public",
        inventory=dict(
            snapshot_id="a" * 32, source_sha256="b" * 64, unique_assets=1, complete=True
        ),
        artifacts=[
            dict(
                id="one",
                group="viewer",
                role="viewer-db",
                path="files/db.gz",
                install_path="viewer/document_analysis.db",
                encoding="gzip",
                bytes=10,
                sha256="c" * 64,
                content_bytes=20,
                content_sha256="d" * 64,
            )
        ],
    )


class ContractTests(unittest.TestCase):
    def test_release_rejects_unsafe_colliding_or_incompatible_paths(self):
        validate_release(release())
        for name in (".", "../escape", "/root", "a/../b", "a\\b", "a//b", "x:", "a/."):
            value = release()
            value["artifacts"][0]["install_path"] = name
            with self.assertRaises(ValueError):
                validate_release(value)
        value = release()
        other = copy.deepcopy(value["artifacts"][0])
        other.update(id="two", path="files/other", install_path="VIEWER/document_analysis.db")
        value["artifacts"].append(other)
        with self.assertRaises(ValueError):
            validate_release(value)
        with self.assertRaises(ValueError):
            validate_release(release(), app_version="1.0.0")

    def test_missing_or_replaced_external_root_never_creates_a_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "data"
            with self.assertRaises(ValueError):
                ExternalDirectory(root, 0)
            self.assertFalse(root.exists())
            root.mkdir()
            storage = ExternalDirectory(root, 0)
            storage.mkdir("safe/sub")
            moved = Path(d) / "moved"
            root.rename(moved)
            root.mkdir()
            with self.assertRaises(ValueError):
                storage.mkdir("unexpected")
            self.assertFalse((root / "unexpected").exists())

    def test_workspace_rejects_role_aliases_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "data"
            root.mkdir()
            config = Path(d) / "workspace.json"
            value = dict(
                format="explorer-workspace",
                version=1,
                root=str(root),
                reserve_bytes=0,
                paths=dict(
                    packages="packages",
                    inventory="inventory",
                    downloads="originals",
                    corpus="corpus",
                    releases="releases",
                    scratch="tmp",
                ),
            )
            config.write_text(json.dumps(value))
            load_workspace(config)
            (root / "tmp").symlink_to(Path(d), target_is_directory=True)
            with self.assertRaises(ValueError):
                load_workspace(config)
