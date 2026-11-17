import json
import tempfile
import unittest
from pathlib import Path

from explorer_ingest.distribution_activate import activate
from explorer_ingest.distribution_demo import demo_bundle
from explorer_ingest.distribution_install import import_release
from explorer_ingest.distribution_release import build_release
from explorer_ingest.state import digest_file


class ActivationTests(unittest.TestCase):
    def test_verified_relative_switch_rollback_and_reject_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "synthetic"
            demo_bundle(bundle, 0)
            releases = root / "releases"
            releases.mkdir()
            workspace = root / "workspace.json"
            workspace.write_text(
                json.dumps(
                    dict(
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
                )
            )
            recipe = dict(
                release_id="test-release",
                visibility="public",
                inventory=dict(
                    snapshot_id="synthetic", source_sha256="a" * 64, unique_assets=0, complete=False
                ),
                artifacts=[],
            )
            for i, f in enumerate(sorted(p for p in bundle.rglob("*") if p.is_file())):
                name = str(f.relative_to(bundle))
                role = {
                    "document_analysis.db": "viewer-db",
                    "tag_clusters.json": "viewer-clusters",
                    "manifest.json": "viewer-manifest",
                }.get(name, "viewer-text")
                recipe["artifacts"].append(
                    dict(
                        id=f"item-{i}",
                        source=str(f),
                        group="viewer",
                        role=role,
                        encoding="gzip",
                        path=f"files/{i}.gz",
                        install_path="viewer/" + name,
                    )
                )
            recipe_path = root / "recipe.json"
            recipe_path.write_text(json.dumps(recipe))
            package = root / "package"
            build_release(recipe_path, package, 0)
            for name in ("first", "second"):
                import_release(
                    package,
                    releases / name,
                    reserve_bytes=0,
                    expected_sha256=digest_file(package / "release.json"),
                )
            active = activate(workspace, releases / "first")
            self.assertEqual(active["current"], "first/viewer")
            self.assertTrue((releases / "current").is_symlink())
            self.assertEqual(activate(workspace, releases / "second")["previous"], "first/viewer")
            activate(workspace, releases / "first")
            (releases / "second/viewer/document_analysis.db").write_bytes(b"damaged")
            with self.assertRaises(ValueError):
                activate(workspace, releases / "second")
            self.assertEqual(
                (releases / "current").resolve(), (releases / "first/viewer").resolve()
            )
            (releases / "current").unlink()
            (releases / "current").symlink_to(root)
            with self.assertRaises(ValueError):
                activate(workspace, releases / "first")
