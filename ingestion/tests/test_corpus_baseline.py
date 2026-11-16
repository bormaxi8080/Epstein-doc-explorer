"""Baseline inspection never mutates the upstream corpus."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from explorer_ingest.corpus_baseline import inspect_baseline, lfs_identity
from explorer_ingest.export import SCHEMA
from explorer_ingest.state import digest_file


class BaselineTests(unittest.TestCase):
    def test_real_schema_and_hash_are_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "baseline.db"
            with sqlite3.connect(p) as db:
                db.executescript(SCHEMA)
            before = digest_file(p)
            result = inspect_baseline(p, before)
            self.assertEqual(result["counts"], {"documents": 0, "rdf_triples": 0})
            self.assertEqual(digest_file(p), before)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                inspect_baseline(p, "f" * 64)

    def test_lfs_pointer_is_identity_not_database(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / "pointer"
            p.write_text(
                "version https://git-lfs.github.com/spec/v1\noid sha256:"
                + "a" * 64
                + "\nsize 100\n"
            )
            self.assertEqual(lfs_identity(p), ("a" * 64, 100))
            with self.assertRaisesRegex(ValueError, "not SQLite"):
                inspect_baseline(p)
