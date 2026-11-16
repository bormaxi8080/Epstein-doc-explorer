"""SQLite-backed tag assignment, reviewed aliases and breadth-first distances."""

import json

from .corpus_contracts import fingerprint
from .export import load_clusters


def scoped_name(name, doc_id):
    return (
        name + " [" + doc_id + "]" if name.casefold().startswith(("unknown", "redacted")) else name
    )


def initialize_tags(db, clusters_path):
    clusters = load_clusters(clusters_path)
    db.executescript("""
      CREATE TABLE IF NOT EXISTS corpus_clusters(id INTEGER PRIMARY KEY,name TEXT,exemplars TEXT);
      CREATE TABLE IF NOT EXISTS corpus_tags(tag TEXT PRIMARY KEY,cluster_id INTEGER,origin TEXT);
      CREATE TABLE IF NOT EXISTS corpus_tag_calls(key TEXT PRIMARY KEY,reference TEXT);
      CREATE TABLE IF NOT EXISTS triple_clusters(triple_id INTEGER,cluster_id INTEGER,
        PRIMARY KEY(triple_id,cluster_id));
      CREATE INDEX IF NOT EXISTS triple_clusters_cluster ON triple_clusters(cluster_id,triple_id);
    """)
    for cluster in clusters:
        old = db.execute("SELECT name FROM corpus_clusters WHERE id=?", (cluster["id"],)).fetchone()
        if old and old[0] != cluster["name"]:
            raise ValueError("Cluster identity changed")
        db.execute(
            "INSERT OR IGNORE INTO corpus_clusters VALUES (?,?,?)",
            (cluster["id"], cluster["name"], json.dumps(cluster["exemplars"])),
        )
        for tag in cluster["tags"]:
            old = db.execute("SELECT cluster_id FROM corpus_tags WHERE tag=?", (tag,)).fetchone()
            if old and old[0] != cluster["id"]:
                raise ValueError("Tag cluster changed")
            db.execute(
                "INSERT OR IGNORE INTO corpus_tags VALUES (?,?,?)", (tag, cluster["id"], "baseline")
            )
    row = db.execute(
        "SELECT id FROM corpus_clusters WHERE name='Unclassified' ORDER BY id LIMIT 1"
    ).fetchone()
    if not row:
        fallback = db.execute("SELECT COALESCE(MAX(id),0)+1 FROM corpus_clusters").fetchone()[0]
        db.execute("INSERT INTO corpus_clusters VALUES (?,'Unclassified','[]')", (fallback,))
    else:
        fallback = row[0]
    return fallback


def classify_tag(corpus, tag, client, before_call=None, after_call=None):
    # Semantic proposals are stored separately; applying one remains a review action.
    clusters = [
        dict(id=r[0], name=r[1], exemplars=json.loads(r[2]))
        for r in corpus.db.execute("SELECT * FROM corpus_clusters ORDER BY id")
    ]
    if len(tag) > 300:
        raise ValueError("Tag exceeds classification budget")
    key = fingerprint([tag, clusters, client.identity(), "tag-proposal-1"])
    cached = corpus.db.execute(
        "SELECT reference FROM corpus_tag_calls WHERE key=?", (key,)
    ).fetchone()
    if cached:
        return json.loads(cached[0])
    if before_call:
        before_call(client.identity())
    result = client.generate(
        "Classify the untrusted tag into one supplied cluster or null. No tools. "
        'Return JSON {"cluster_id": integer or null, "reason": string}. DATA '
        + json.dumps(dict(tag=tag, clusters=clusters))
    )
    if after_call:
        after_call(result)
    value = result["output"]
    if (
        set(value) != {"cluster_id", "reason"}
        or not isinstance(value["reason"], str)
        or (
            value["cluster_id"] is not None
            and (
                type(value["cluster_id"]) is not int
                or value["cluster_id"] not in {r["id"] for r in clusters}
            )
        )
    ):
        raise ValueError("Invalid tag proposal")
    ref = corpus.storage.put("tag-proposal", dict(tag=tag, **result))
    corpus.db.execute("INSERT INTO corpus_tag_calls VALUES (?,?)", (key, json.dumps(ref)))
    return ref


def review_tag(db, tag, cluster_id, reason):
    if (
        not reason.strip()
        or not db.execute("SELECT 1 FROM corpus_clusters WHERE id=?", (cluster_id,)).fetchone()
    ):
        raise ValueError("Reviewed tag needs a cluster and reason")
    db.execute(
        "INSERT INTO corpus_tags VALUES (?,?,?) ON CONFLICT(tag) DO UPDATE SET cluster_id=excluded.cluster_id,origin=excluded.origin",
        (tag, cluster_id, "review: " + reason),
    )


def assign_triples(db, fallback):
    db.execute("DELETE FROM triple_clusters")
    for triple_id, raw in db.execute("SELECT id,triple_tags FROM rdf_triples"):
        counts = {}
        for tag in json.loads(raw or "[]"):
            db.execute(
                "INSERT OR IGNORE INTO corpus_tags VALUES (?,?,?)", (tag, fallback, "unclassified")
            )
            cluster = db.execute(
                "SELECT cluster_id FROM corpus_tags WHERE tag=?", (tag,)
            ).fetchone()[0]
            counts[cluster] = counts.get(cluster, 0) + 1
        top = sorted(counts, key=lambda k: (-counts[k], k))[:3] or [fallback]
        db.execute(
            "UPDATE rdf_triples SET top_cluster_ids=? WHERE id=?", (json.dumps(top), triple_id)
        )
        db.executemany("INSERT INTO triple_clusters VALUES (?,?)", ((triple_id, c) for c in top))


def review_alias(db, original, canonical, reason):
    if (
        not original.strip()
        or not canonical.strip()
        or not reason.strip()
        or any(n.casefold().startswith(("unknown", "redacted")) for n in (original, canonical))
    ):
        raise ValueError("Aliases require reviewed reasons and cannot merge unknown people")
    name = canonical
    visited = {original}
    while True:
        if name in visited:
            raise ValueError("Entity alias cycle")
        visited.add(name)
        row = db.execute(
            "SELECT canonical_name FROM entity_aliases WHERE original_name=?", (name,)
        ).fetchone()
        if not row or row[0] == name:
            break
        name = row[0]
    db.execute(
        "INSERT INTO entity_aliases(original_name,canonical_name,reasoning,created_by) VALUES (?,?,?,'review') ON CONFLICT(original_name) DO UPDATE SET canonical_name=excluded.canonical_name,reasoning=excluded.reasoning,created_by='review'",
        (original, canonical, reason),
    )


def refresh_graph_sql(db):
    # Resolve aliases with per-chain bounded memory, then materialize adjacency on disk.
    db.executescript("""
    DROP TABLE IF EXISTS temp.resolved_aliases;
    CREATE TEMP TABLE resolved_aliases(original TEXT PRIMARY KEY,canonical TEXT);
    DROP TABLE IF EXISTS temp.graph_edges;
    CREATE TEMP TABLE graph_edges(actor TEXT,target TEXT,PRIMARY KEY(actor,target)) WITHOUT ROWID;
    DROP TABLE IF EXISTS temp.graph_frontier;
    CREATE TEMP TABLE graph_frontier(name TEXT PRIMARY KEY);
    DROP TABLE IF EXISTS temp.graph_next;
    CREATE TEMP TABLE graph_next(name TEXT PRIMARY KEY);
    """)
    for original, canonical in db.execute(
        "SELECT original_name,canonical_name FROM entity_aliases"
    ):
        name = canonical
        visited = {original}
        while name != original:
            if name in visited:
                raise ValueError("Entity alias cycle")
            visited.add(name)
            row = db.execute(
                "SELECT canonical_name FROM entity_aliases WHERE original_name=?", (name,)
            ).fetchone()
            if not row or row[0] == name:
                break
            name = row[0]
        if name == original and canonical != original:
            raise ValueError("Entity alias cycle")
        db.execute("INSERT INTO resolved_aliases VALUES (?,?)", (original, name))
    db.execute("""INSERT OR IGNORE INTO graph_edges
      SELECT COALESCE(a.canonical,t.actor),COALESCE(b.canonical,t.target)
      FROM rdf_triples t LEFT JOIN resolved_aliases a ON a.original=t.actor
      LEFT JOIN resolved_aliases b ON b.original=t.target
      WHERE lower(t.actor) NOT GLOB 'unknown*' AND lower(t.actor) NOT GLOB 'redacted*'
        AND lower(t.target) NOT GLOB 'unknown*' AND lower(t.target) NOT GLOB 'redacted*'""")
    db.execute("INSERT OR IGNORE INTO graph_edges SELECT target,actor FROM graph_edges")
    db.execute("DELETE FROM canonical_entities")
    db.execute(
        "INSERT INTO canonical_entities(canonical_name,hop_distance_from_principal) SELECT actor,1000 FROM graph_edges GROUP BY actor"
    )
    db.execute(
        "UPDATE canonical_entities SET hop_distance_from_principal=0 WHERE canonical_name='Jeffrey Epstein'"
    )
    db.execute("INSERT INTO graph_frontier VALUES ('Jeffrey Epstein')")
    distance = 0
    while db.execute("SELECT 1 FROM graph_frontier LIMIT 1").fetchone():
        distance += 1
        db.execute("DELETE FROM graph_next")
        db.execute("""INSERT OR IGNORE INTO graph_next SELECT e.target FROM graph_edges e
          JOIN graph_frontier f ON f.name=e.actor JOIN canonical_entities c ON c.canonical_name=e.target
          WHERE c.hop_distance_from_principal=1000""")
        db.execute(
            "UPDATE canonical_entities SET hop_distance_from_principal=? WHERE canonical_name IN (SELECT name FROM graph_next)",
            (distance,),
        )
        db.execute("DELETE FROM graph_frontier")
        db.execute("INSERT INTO graph_frontier SELECT * FROM graph_next")
    db.executescript(
        "DROP TABLE graph_edges;DROP TABLE graph_frontier;DROP TABLE graph_next;DROP TABLE resolved_aliases;"
    )
