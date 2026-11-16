"""Derived storage cannot recreate an unplugged/replaced root or mutate artifacts."""

import tempfile
import unittest
from pathlib import Path

from explorer_ingest.corpus_storage import CorpusLimits, CorpusStorage


class StorageTests(unittest.TestCase):
    def test_immutable_artifact_and_tampering(self):
        with tempfile.TemporaryDirectory() as d:
            s = CorpusStorage(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            a = s.put("page", {"text": "Synthetic"})
            self.assertEqual(s.put("page", {"text": "Synthetic"}), a)
            self.assertEqual(s.get(a), {"text": "Synthetic"})
            s.path(a["path"]).write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum"):
                s.get(a)

    def test_removed_or_replaced_root_never_gets_recreated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "disk"
            root.mkdir()
            s = CorpusStorage(root, create=True, limits=CorpusLimits(reserve_bytes=0))
            root.rename(Path(d) / "removed")
            with self.assertRaises(OSError):
                s.put("page", {})
            self.assertFalse(root.exists())
            root.mkdir()
            with self.assertRaises(ValueError):
                s.put("page", {})
            self.assertEqual(list(root.iterdir()), [])

    def test_scratch_and_paths_stay_under_root(self):
        with tempfile.TemporaryDirectory() as d:
            s = CorpusStorage(d, create=True, limits=CorpusLimits(reserve_bytes=0))
            with s.scratch_environment():
                self.assertEqual(tempfile.gettempdir(), str(s.root / "tmp"))
            with self.assertRaises(ValueError):
                s.path("../outside")
            outside = Path(d) / "bad"
            outside.symlink_to("/tmp")
            with self.assertRaises(ValueError):
                s.path("bad/file")

    def test_aggregate_scratch_reservations_include_stale_files(self):
        with tempfile.TemporaryDirectory() as d:
            storage = CorpusStorage(
                d,
                create=True,
                limits=CorpusLimits(reserve_bytes=0, max_scratch_bytes=100, max_artifact_bytes=20),
            )
            with storage.temporary(expected_bytes=70):
                with self.assertRaisesRegex(ValueError, "scratch"):
                    with storage.temporary(expected_bytes=31):
                        pass
            storage.path("tmp/stale").write_bytes(b"x" * 80)
            with self.assertRaisesRegex(ValueError, "scratch"):
                with storage.temporary(expected_bytes=21):
                    pass
