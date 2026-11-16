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
        refresh_graph_sql(db)
        distances = dict(
            db.execute("SELECT canonical_name,hop_distance_from_principal FROM canonical_entities")
        )
        self.assertEqual(distances["C"], 2)
        self.assertEqual(distances["X"], 1000)
        self.assertNotIn("unknown person", distances)
        self.assertNotEqual(scoped_name("redacted", "d1"), scoped_name("redacted", "d2"))
