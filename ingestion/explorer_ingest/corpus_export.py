"""Immutable compatible bundles with streaming per-document evidence and TXT."""

import json
import os
import shutil
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .corpus_analysis import analyzed_chunks
from .corpus_contracts import fingerprint
from .corpus_graph import assign_triples, initialize_tags, refresh_graph_sql, scoped_name
from .corpus_text import pages, read_text_artifact
from .export import SCHEMA
from .state import atomic_json, digest_file, readonly_db


def export_corpus(corpus, output, clusters_path, base_db=None, include_unreviewed=False):
    output = Path(output).absolute()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("Bundle needs a new path under an existing external parent")
    checkout = Path(__file__).resolve().parents[2]
    parent = output.parent.resolve(strict=True)
    if (
        checkout == parent
        or checkout in parent.parents
        or parent.stat().st_dev != corpus.storage.root.stat().st_dev
    ):
        raise ValueError("Export must stay on the corpus filesystem outside Git")
    corpus.storage.check((Path(base_db).stat().st_size * 2) if base_db else 0)
    temporary = parent / (output.name + ".tmp-" + uuid.uuid4().hex)
    temporary.mkdir()
    count = 0
    try:
        with (
            corpus.storage.scratch_environment(),
            closing(sqlite3.connect(temporary / "document_analysis.db")) as db,
        ):
            if base_db:
                with closing(readonly_db(base_db)) as source:
                    source.backup(db)
            db.execute("PRAGMA temp_store=FILE")
            db.execute("PRAGMA cache_size=-8192")
            db.executescript(SCHEMA)
            if "top_cluster_ids" not in {
                r[1] for r in db.execute("PRAGMA table_info(rdf_triples)")
            }:
                db.execute("ALTER TABLE rdf_triples ADD COLUMN top_cluster_ids TEXT")
            db.executescript("""
              CREATE TABLE IF NOT EXISTS ingestion_pages(doc_id TEXT,page INTEGER,text TEXT,
                quality TEXT,method TEXT,provenance TEXT,PRIMARY KEY(doc_id,page));
              CREATE TABLE IF NOT EXISTS ingestion_texts(doc_id TEXT PRIMARY KEY,path TEXT,sha256 TEXT,pages INTEGER);
              CREATE TEMP TABLE seen_triples(key TEXT PRIMARY KEY) WITHOUT ROWID;
            """)
            fallback = initialize_tags(db, clusters_path)
            now = datetime.now(timezone.utc).isoformat()
            for doc in corpus.db.execute(
                "SELECT * FROM corpus_documents WHERE text_ref IS NOT NULL ORDER BY doc_id"
            ):
                corpus.storage.check()
                analysis_ref = json.loads(doc["analysis_ref"]) if doc["analysis_ref"] else None
                approved = analysis_ref and doc["approved_key"] == analysis_ref["sha256"]
                if not approved and not include_unreviewed:
                    continue
                artifact, txt = read_text_artifact(corpus, doc["doc_id"])
                manifest = corpus.storage.get(analysis_ref) if analysis_ref else None
                if manifest and manifest["text_ref"] != json.loads(doc["text_ref"]):
                    raise ValueError("Analysis does not match current text")
                if approved and artifact["review_required"]:
                    raise ValueError("Approved document has unreviewed pages")
                summary = (
                    manifest["summary"]
                    if manifest
                    else dict(
                        one_sentence_summary="Analysis pending",
                        paragraph_summary="Analysis pending",
                        category="other",
                        content_tags=[],
                        date_range_earliest=None,
                        date_range_latest=None,
                    )
                )
                if db.execute(
                    "SELECT 1 FROM documents WHERE doc_id=?", (doc["doc_id"],)
                ).fetchone():
                    raise ValueError("Document ID collides with baseline")
                corpus.storage.check(txt.stat().st_size * 3)
                relative = f"data/ocr/{doc['doc_id'][4:6]}/{doc['doc_id']}.txt"
                target = temporary / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(txt, target)
                if digest_file(target) != artifact["text_sha256"]:
                    raise ValueError("Export TXT changed")
                # Blob streaming avoids materializing a large full_text string in Python.
                cur = db.execute(
                    """INSERT INTO documents(doc_id,file_path,one_sentence_summary,paragraph_summary,
                  date_range_earliest,date_range_latest,category,content_tags,full_text,analysis_timestamp)
                  VALUES (?,?,?,?,?,?,?,?,zeroblob(?),?)""",
                    (
                        doc["doc_id"],
                        relative,
                        summary["one_sentence_summary"],
                        summary["paragraph_summary"],
                        summary["date_range_earliest"],
                        summary["date_range_latest"],
                        summary["category"],
                        json.dumps(summary["content_tags"]),
                        txt.stat().st_size,
                        now,
                    ),
                )
                if hasattr(db, "blobopen"):
                    with (
                        db.blobopen("documents", "full_text", cur.lastrowid) as blob,
                        txt.open("rb") as stream,
                    ):
                        while data := stream.read(1024 * 1024):
                            blob.write(data)
                    db.execute(
                        "UPDATE documents SET full_text=CAST(full_text AS TEXT) WHERE id=?",
                        (cur.lastrowid,),
                    )
                else:
                    if txt.stat().st_size > 16 * 1024**2:
                        raise ValueError("Python 3.11+ required for large text export")
                    db.execute(
                        "UPDATE documents SET full_text=? WHERE id=?",
                        (txt.read_text(), cur.lastrowid),
                    )
                db.execute(
                    "INSERT INTO ingestion_texts VALUES (?,?,?,?)",
                    (doc["doc_id"], relative, artifact["text_sha256"], artifact["pages_count"]),
                )
                db.execute(
                    "INSERT INTO ingestion_documents VALUES (?,?,?,?,?,?)",
                    (
                        doc["doc_id"],
                        "doj",
                        doc["sha256"],
                        analysis_ref["sha256"] if analysis_ref else "",
                        "approved" if approved else "pending",
                        json.dumps(
                            dict(
                                text=json.loads(doc["text_ref"]),
                                analysis=analysis_ref,
                                review_required=artifact["review_required"],
                            )
                        ),
                    ),
                )
                for value, ref in pages(corpus, doc["doc_id"], allow_review=include_unreviewed):
                    db.execute(
                        "INSERT INTO ingestion_pages VALUES (?,?,?,?,?,?)",
                        (
                            doc["doc_id"],
                            value["page"],
                            value["text"],
                            value["quality"],
                            value["method"],
                            json.dumps(dict(page_ref=ref, provenance=value["provenance"])),
                        ),
                    )
                for source in corpus.db.execute(
                    "SELECT * FROM corpus_sources WHERE doc_id=?", (doc["doc_id"],)
                ):
                    db.execute(
                        "INSERT INTO ingestion_sources VALUES (?,?,?,?,?)",
                        (
                            doc["doc_id"],
                            source["id"],
                            source["source_sha256"],
                            "",
                            source["metadata"],
                        ),
                    )
                db.execute("DELETE FROM seen_triples")
                sequence = 0
                for chunk in analyzed_chunks(corpus, manifest) if manifest else ():
                    for triple in chunk["output"]["rdf_triples"]:
                        key = fingerprint(triple)
                        if not db.execute(
                            "INSERT OR IGNORE INTO seen_triples VALUES (?)", (key,)
                        ).rowcount:
                            continue
                        cur = db.execute(
                            """INSERT INTO rdf_triples(doc_id,timestamp,actor,action,target,location,
                          triple_tags,explicit_topic,implicit_topic,sequence_order) VALUES (?,?,?,?,?,?,?,?,NULL,?)""",
                            (
                                doc["doc_id"],
                                triple["timestamp"],
                                scoped_name(triple["actor"], doc["doc_id"]),
                                triple["action"],
                                scoped_name(triple["target"], doc["doc_id"]),
                                triple["location"],
                                json.dumps(triple["tags"]),
                                triple["explicit_topic"],
                                sequence,
                            ),
                        )
                        db.execute(
                            "INSERT INTO ingestion_evidence VALUES (?,?,?,?,?)",
                            (
                                cur.lastrowid,
                                triple["evidence"]["page"],
                                triple["evidence"]["quote"],
                                triple["assertion_type"],
                                fingerprint(chunk["payload"]),
                            ),
                        )
                        sequence += 1
                count += 1
                if count % 100 == 0:
                    db.commit()
            assign_triples(db, fallback)
            refresh_graph_sql(db)
            db.executescript("""
              CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(full_text,content='documents',content_rowid='id');
              INSERT INTO documents_fts(documents_fts) VALUES ('rebuild');
              CREATE INDEX IF NOT EXISTS documents_category_id ON documents(category,doc_id);
            """)
            db.commit()
            if (
                db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or db.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise ValueError("Bundle integrity check failed")
            with (temporary / "tag_clusters.json").open("w") as stream:
                stream.write("[")
                for position, (cid, name, exemplars) in enumerate(
                    db.execute("SELECT * FROM corpus_clusters ORDER BY id")
                ):
                    if position:
                        stream.write(",")
                    stream.write(
                        json.dumps(dict(id=cid, name=name, exemplars=json.loads(exemplars)))[:-1]
                        + ',"tags":['
                    )
                    for index, (tag,) in enumerate(
                        db.execute(
                            "SELECT tag FROM corpus_tags WHERE cluster_id=? ORDER BY tag", (cid,)
                        )
                    ):
                        if index:
                            stream.write(",")
                        stream.write(json.dumps(tag))
                    stream.write("]}")
                stream.write("]\n")
                stream.flush()
                os.fsync(stream.fileno())
        result = dict(
            format_version=2,
            new_documents=count,
            include_unreviewed=include_unreviewed,
            originals_included=False,
            created_at=now,
            database_sha256=digest_file(temporary / "document_analysis.db"),
            clusters_sha256=digest_file(temporary / "tag_clusters.json"),
            baseline_sha256=digest_file(base_db) if base_db else None,
        )
        atomic_json(temporary / "manifest.json", result)
        corpus.storage.check()
        temporary.rename(output)
        return result
    except BaseException:
        shutil.rmtree(temporary)
        raise
