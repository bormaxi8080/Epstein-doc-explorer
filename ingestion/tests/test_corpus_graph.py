import sqlite3
import unittest

from explorer_ingest.corpus_graph import refresh_graph_sql, review_alias, scoped_name
from explorer_ingest.export import SCHEMA


class GraphTests(unittest.TestCase):
    def test_disk_bfs_and_review(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        for a, b in [("Jeffrey Epstein", "A"), ("A", "B"), ("X", "Y"), ("unknown person", "A")]:
            db.execute(
                "INSERT INTO rdf_triples(doc_id,actor,action,target,sequence_order) VALUES ('d',?,'states',?,0)",
                (a, b),
            )
        review_alias(db, "B", "C", "Reviewed source spelling")
        with self.assertRaises(ValueError):
            review_alias(db, "C", "B", "cycle")
        with self.assertRaises(ValueError):
            review_alias(db, "unknown person", "A", "unsafe")
        review_alias(db, "C", "D", "Second reviewed spelling")
        refresh_graph_sql(db)
        distances = dict(
            db.execute("SELECT canonical_name,hop_distance_from_principal FROM canonical_entities")
        )
        self.assertEqual(distances["D"], 2)
        self.assertEqual(
            db.execute(
                "SELECT canonical_name FROM entity_aliases WHERE original_name='B'"
            ).fetchone()[0],
            "D",
        )
        self.assertEqual(distances["X"], 1000)
        self.assertNotIn("unknown person", distances)
        self.assertNotEqual(scoped_name("redacted", "d1"), scoped_name("redacted", "d2"))

    def test_invalid_baseline_alias_chains_are_retained_and_unresolved(self):
        import json

        from explorer_ingest.corpus_graph import quarantine_baseline_alias_cycles

        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        for a, b in [
            ("Cycle A", "Cycle B"),
            ("Cycle B", "Cycle A"),
            ("Feeder", "Cycle A"),
            ("Valid", "Terminal"),
        ]:
            db.execute(
                "INSERT INTO entity_aliases(original_name,canonical_name,reasoning) VALUES (?,?,'upstream')",
                (a, b),
            )
        self.assertEqual(quarantine_baseline_alias_cycles(db), 3)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM entity_aliases").fetchone()[0], 1)
        raw = json.loads(
            db.execute(
                "SELECT raw_row FROM ingestion_alias_issues WHERE original_name=?", ("Feeder",)
            ).fetchone()[0]
        )
        self.assertEqual(raw["canonical_name"], "Cycle A")
        refresh_graph_sql(db)

    def test_orphaned_baseline_edges_are_preserved_without_invented_documents(self):
        import json

        from explorer_ingest.corpus_graph import quarantine_baseline_orphans

        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.executescript(SCHEMA)
        db.execute(
            "INSERT INTO rdf_triples(doc_id,actor,action,target,sequence_order) VALUES ('missing','Synthetic A','stated','Synthetic B',0)"
        )
        self.assertEqual(quarantine_baseline_orphans(db), 1)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM documents").fetchone()[0], 0)
        self.assertIsNone(db.execute("PRAGMA foreign_key_check").fetchone())
        raw = json.loads(
            db.execute("SELECT raw_row FROM ingestion_baseline_triple_issues").fetchone()[0]
        )
        self.assertEqual(raw["doc_id"], "missing")
        self.assertEqual(raw["action"], "stated")
