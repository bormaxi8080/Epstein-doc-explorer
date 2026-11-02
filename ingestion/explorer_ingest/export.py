"""Build an atomic Explorer bundle, preserving an optional upstream baseline."""

import json
import shutil
import sqlite3
import uuid
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .state import atomic_json, digest_file, fingerprint, readonly_db

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT UNIQUE NOT NULL, file_path TEXT NOT NULL,
 one_sentence_summary TEXT NOT NULL, paragraph_summary TEXT NOT NULL,
 date_range_earliest TEXT, date_range_latest TEXT, category TEXT NOT NULL,
 content_tags TEXT NOT NULL, full_text TEXT, analysis_timestamp TEXT NOT NULL,
 input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
 cost_usd REAL, error TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS rdf_triples (
 id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT NOT NULL, timestamp TEXT,
 actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, location TEXT,
 actor_likely_type TEXT, triple_tags TEXT, explicit_topic TEXT, implicit_topic TEXT,
 sequence_order INTEGER NOT NULL, top_cluster_ids TEXT,
 created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
 FOREIGN KEY(doc_id) REFERENCES documents(doc_id));
CREATE TABLE IF NOT EXISTS entity_aliases (
 original_name TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, reasoning TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, created_by TEXT DEFAULT 'review');
CREATE TABLE IF NOT EXISTS canonical_entities (
 canonical_name TEXT PRIMARY KEY, hop_distance_from_principal INTEGER NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ingestion_documents (
 doc_id TEXT PRIMARY KEY REFERENCES documents(doc_id), collection_id TEXT NOT NULL,
 sha256 TEXT NOT NULL, analysis_key TEXT NOT NULL, review_status TEXT NOT NULL,
 analysis_provenance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ingestion_sources (
 doc_id TEXT NOT NULL REFERENCES documents(doc_id), asset_id TEXT NOT NULL,
 inventory_id TEXT NOT NULL, member_path TEXT NOT NULL, metadata TEXT NOT NULL,
 PRIMARY KEY(doc_id,asset_id,member_path));
CREATE TABLE IF NOT EXISTS ingestion_evidence (
 triple_id INTEGER PRIMARY KEY REFERENCES rdf_triples(id), page INTEGER NOT NULL,
 quote TEXT NOT NULL, assertion_type TEXT NOT NULL, chunk_key TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_rdf_triples_doc_id ON rdf_triples(doc_id);
CREATE INDEX IF NOT EXISTS idx_rdf_triples_actor ON rdf_triples(actor);
CREATE INDEX IF NOT EXISTS idx_rdf_triples_target ON rdf_triples(target);
CREATE INDEX IF NOT EXISTS idx_rdf_triples_timestamp ON rdf_triples(timestamp);
"""


def load_clusters(path):
    clusters = json.loads(Path(path).read_text())
    ids, tags = set(), set()
    if not isinstance(clusters, list) or not clusters:
        raise ValueError("A nonempty tag cluster list is required")
    for cluster in clusters:
        if (
            type(cluster.get("id")) is not int
            or cluster["id"] in ids
            or not isinstance(cluster.get("name"), str)
        ):
            raise ValueError("Invalid or duplicate cluster ID")
        if not isinstance(cluster.get("tags"), list) or not isinstance(
            cluster.get("exemplars"), list
        ):
            raise ValueError("Clusters need tags and exemplars")
        ids.add(cluster["id"])
        for tag in cluster["tags"]:
            if not isinstance(tag, str) or tag in tags:
                raise ValueError("Tags must belong to exactly one cluster")
            tags.add(tag)
    return clusters


def refresh_graph(db):
    aliases = dict(db.execute("SELECT original_name,canonical_name FROM entity_aliases"))
    resolved = {}
    for original in aliases:
        name, visited = original, set()
        while name in aliases and aliases[name] != name:
            if name in visited:
                raise ValueError("Entity alias cycle; review the mapping before export")
            visited.add(name)
            name = aliases[name]
        resolved[original] = name
    aliases = resolved
    db.executemany(
        "UPDATE entity_aliases SET canonical_name=? WHERE original_name=?",
        [(canonical, original) for original, canonical in aliases.items()],
    )
    adjacency = defaultdict(set)
    for actor, target in db.execute("SELECT actor,target FROM rdf_triples"):
        actor, target = aliases.get(actor, actor), aliases.get(target, target)
        if actor.lower().startswith(("unknown", "redacted")) or target.lower().startswith(
            ("unknown", "redacted")
        ):
            continue
        adjacency[actor].add(target)
        adjacency[target].add(actor)
    distances = {"Jeffrey Epstein": 0}
    queue = deque(distances)
    while queue:
        actor = queue.popleft()
        for target in adjacency[actor]:
            if target not in distances:
                distances[target] = distances[actor] + 1
                queue.append(target)
    db.execute("DELETE FROM canonical_entities")
    db.executemany(
        "INSERT INTO canonical_entities(canonical_name,hop_distance_from_principal) VALUES (?,?)",
        [(name, distances.get(name, 1000)) for name in adjacency],
    )


def export_bundle(
    store, output, clusters_path, base_db=None, include_unreviewed=False, aliases_path=None
):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Output must be a new version directory; published bundles are immutable")
    clusters = load_clusters(clusters_path)
    if base_db:
        with Path(base_db).open("rb") as baseline_file:
            if baseline_file.read(16) != b"SQLite format 3\x00":
                raise ValueError("Baseline is not SQLite; fetch its Git LFS object first")
    temporary = output.with_name(output.name + ".tmp-" + uuid.uuid4().hex)
    temporary.mkdir(parents=True)
    try:
        db_path = temporary / "document_analysis.db"
        with closing(sqlite3.connect(db_path)) as db:
            if base_db:
                with closing(readonly_db(base_db)) as baseline:
                    baseline.backup(db)
            db.execute("PRAGMA journal_mode=DELETE")
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(rdf_triples)")}
            if "top_cluster_ids" not in columns:
                db.execute("ALTER TABLE rdf_triples ADD COLUMN top_cluster_ids TEXT")
            db.execute("PRAGMA foreign_keys=ON")
            if aliases_path:
                for alias in json.loads(Path(aliases_path).read_text()):
                    if not alias.get("reasoning") or any(
                        alias[role].lower().startswith(("unknown", "redacted"))
                        for role in ("original_name", "canonical_name")
                    ):
                        raise ValueError(
                            "Aliases require reviewed reasoning and cannot merge unknown people"
                        )
                    db.execute(
                        """INSERT INTO entity_aliases(original_name,canonical_name,reasoning,created_by)
                       VALUES (?,?,?,'review') ON CONFLICT(original_name) DO UPDATE SET
                       canonical_name=excluded.canonical_name,reasoning=excluded.reasoning,created_by='review'""",
                        (alias["original_name"], alias["canonical_name"], alias["reasoning"]),
                    )
            tag_map = {tag: cluster["id"] for cluster in clusters for tag in cluster["tags"]}
            fallback_cluster = next((x for x in clusters if x["name"] == "Unclassified"), None)
            if fallback_cluster is None:
                fallback_cluster = {
                    "id": max(x["id"] for x in clusters) + 1,
                    "name": "Unclassified",
                    "tags": [],
                    "exemplars": [],
                }
                clusters.append(fallback_cluster)
            fallback = fallback_cluster["id"]
            unclassified_tags = set(fallback_cluster["tags"])
            now = datetime.now(timezone.utc).isoformat()
            documents = store.db.execute(
                """SELECT * FROM documents WHERE analysis_key IS NOT NULL AND
               (status='approved' AND approved_key=analysis_key OR ? AND status='analyzed') ORDER BY doc_id""",
                (int(include_unreviewed),),
            ).fetchall()
            for doc in documents:
                aggregate = json.loads(doc["analysis_json"])
                if digest_file(store.path(doc["pages_json"])) != aggregate.get("pages_sha256"):
                    raise ValueError(
                        "Extracted pages changed after analysis; reanalyze and review again"
                    )
                analyses = [item["analysis"] for item in aggregate["chunks"]]
                pages = json.loads(store.path(doc["pages_json"]).read_text())["pages"]
                if db.execute(
                    "SELECT 1 FROM documents WHERE doc_id=?", (doc["doc_id"],)
                ).fetchone():
                    raise ValueError(
                        "Document ID collides with baseline; export into the original upstream baseline"
                    )
                tags = sorted({tag for a in analyses for tag in a["content_tags"]})
                earliest = [a["date_range_earliest"] for a in analyses if a["date_range_earliest"]]
                latest = [a["date_range_latest"] for a in analyses if a["date_range_latest"]]
                db.execute(
                    """INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,
                  date_range_earliest,date_range_latest,category,content_tags,full_text,analysis_timestamp)
                  VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        doc["doc_id"],
                        "originals/" + doc["sha256"],
                        analyses[0]["one_sentence_summary"],
                        "\n\n".join(a["paragraph_summary"] for a in analyses),
                        min(earliest) if earliest else None,
                        max(latest) if latest else None,
                        analyses[0]["category"]
                        if len({a["category"] for a in analyses}) == 1
                        else "mixed_document",
                        json.dumps(tags),
                        "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in pages),
                        now,
                    ),
                )
                provenance = [
                    {
                        key: item.get(key)
                        for key in (
                            "cache_key",
                            "provider",
                            "reported_model",
                            "usage",
                            "prompt_version",
                        )
                    }
                    for item in aggregate["chunks"]
                ]
                db.execute(
                    "INSERT INTO ingestion_documents VALUES (?,?,?,?,?,?)",
                    (
                        doc["doc_id"],
                        "doj",
                        doc["sha256"],
                        doc["analysis_key"],
                        "approved" if doc["approved_key"] == doc["analysis_key"] else "pending",
                        json.dumps(provenance),
                    ),
                )
                for source in store.db.execute(
                    """SELECT s.*,a.inventory_id,a.metadata FROM document_sources s
                    JOIN assets a ON s.asset_id=a.id WHERE doc_id=?""",
                    (doc["doc_id"],),
                ):
                    db.execute(
                        "INSERT INTO ingestion_sources VALUES (?,?,?,?,?)",
                        (
                            doc["doc_id"],
                            source["asset_id"],
                            source["inventory_id"],
                            source["member_path"],
                            source["metadata"],
                        ),
                    )
                seen, sequence = set(), 0
                for item in aggregate["chunks"]:
                    for triple in item["analysis"]["rdf_triples"]:
                        triple = json.loads(json.dumps(triple))
                        for role in ("actor", "target"):
                            if triple[role].lower().startswith(("unknown", "redacted")):
                                triple[role] += " [" + doc["doc_id"] + "]"
                        key = fingerprint(triple)
                        if key in seen:
                            continue
                        seen.add(key)
                        cursor = db.execute(
                            """INSERT INTO rdf_triples(doc_id,timestamp,actor,action,target,location,
                            triple_tags,explicit_topic,implicit_topic,sequence_order) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (
                                doc["doc_id"],
                                triple["timestamp"],
                                triple["actor"],
                                triple["action"],
                                triple["target"],
                                triple["location"],
                                json.dumps(triple["tags"]),
                                triple["explicit_topic"],
                                None,
                                sequence,
                            ),
                        )
                        db.execute(
                            "INSERT INTO ingestion_evidence VALUES (?,?,?,?,?)",
                            (
                                cursor.lastrowid,
                                triple["evidence"]["page"],
                                triple["evidence"]["quote"],
                                triple["assertion_type"],
                                item["cache_key"],
                            ),
                        )
                        sequence += 1
            # Recompute ALL assignments: the baseline and new corpus share one
            # cluster version, including any reviewed additions to its tag lists.
            for triple_id, raw_tags in db.execute(
                "SELECT id,triple_tags FROM rdf_triples"
            ).fetchall():
                counts = defaultdict(int)
                for tag in json.loads(raw_tags or "[]"):
                    if tag in tag_map:
                        counts[tag_map[tag]] += 1
                    else:
                        unclassified_tags.add(tag)
                        counts[fallback] += 1
                top = sorted(counts, key=lambda item: (-counts[item], item))[:3]
                if not top:
                    top = [fallback]
                db.execute(
                    "UPDATE rdf_triples SET top_cluster_ids=? WHERE id=?",
                    (json.dumps(top), triple_id),
                )
            fallback_cluster["tags"] = sorted(unclassified_tags)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_top_cluster_ids ON rdf_triples(top_cluster_ids)"
            )
            refresh_graph(db)
            db.commit()
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("Export database failed integrity checks")
        atomic_json(temporary / "tag_clusters.json", clusters)
        manifest = {
            "format_version": 1,
            "created_at": now,
            "new_documents": len(documents),
            "include_unreviewed": include_unreviewed,
            "baseline_sha256": digest_file(base_db) if base_db else None,
            "database_sha256": digest_file(db_path),
            "clusters_sha256": digest_file(temporary / "tag_clusters.json"),
            "originals_included": False,
        }
        atomic_json(temporary / "manifest.json", manifest)
        temporary.rename(output)
        return manifest
    except Exception:
        shutil.rmtree(temporary)
        raise
