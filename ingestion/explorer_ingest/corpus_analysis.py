"""Bounded evidence analysis with resumable adaptive fragments and synthesis."""

import json
import os

from .analysis import SCHEMA, validate_analysis
from .corpus_contracts import ANALYSIS_PROMPT_VERSION, fingerprint
from .corpus_fcc import FCCFailure
from .corpus_text import pages, publish_temp, read_text_artifact
from .state import digest_file


def setup(corpus):
    corpus.db.executescript("""
    CREATE TABLE IF NOT EXISTS corpus_analysis_plans(
      id TEXT PRIMARY KEY,doc_id TEXT,text_key TEXT,provider TEXT,ready INTEGER,roots INTEGER);
    CREATE TABLE IF NOT EXISTS corpus_plan_roots(
      plan_id TEXT,position INTEGER,job_id TEXT,PRIMARY KEY(plan_id,position));
    CREATE TABLE IF NOT EXISTS corpus_analysis_jobs(
      doc_id TEXT, text_key TEXT, position INTEGER, job_id TEXT,
      PRIMARY KEY(doc_id,text_key,position));
    CREATE TABLE IF NOT EXISTS corpus_synthesis(key TEXT PRIMARY KEY,reference TEXT);
    """)


def fragments(text, max_bytes=12000):
    if max_bytes < 128:
        raise ValueError("Fragment budget too small")
    start = 0
    while start < len(text):
        end = min(len(text), start + max_bytes)
        while len(text[start:end].encode("utf-8")) > max_bytes:
            end = start + max(1, (end - start) // 2)
        yield start, text[start:end]
        if end == len(text):
            break
        start = end - min(100, (end - start) // 10)


def plan_analysis(corpus, doc_id, identity, max_bytes=12000, allow_review=False):
    setup(corpus)
    artifact, _ = read_text_artifact(corpus, doc_id)
    if artifact["review_required"] and not allow_review:
        raise ValueError("Review pages before analysis")
    text_key = json.loads(corpus.document(doc_id)["text_ref"])["sha256"]
    plan_id = fingerprint(
        [doc_id, text_key, identity, max_bytes, SCHEMA, ANALYSIS_PROMPT_VERSION, allow_review]
    )
    corpus.db.execute(
        "INSERT OR IGNORE INTO corpus_analysis_plans VALUES (?,?,?,?,0,0)",
        (plan_id, doc_id, text_key, json.dumps(identity, sort_keys=True)),
    )
    position = 0
    for value, _ in pages(corpus, doc_id, allow_review=allow_review):
        for start, text in fragments(value["text"], max_bytes):
            payload = dict(
                doc_id=doc_id,
                text_key=text_key,
                page=value["page"],
                start=start,
                text=text,
                provider=identity,
                prompt=ANALYSIS_PROMPT_VERSION,
                schema=SCHEMA,
            )
            key = fingerprint(payload)
            corpus.queue.enqueue(key, "analyze", payload)
            corpus.db.execute(
                "INSERT OR IGNORE INTO corpus_plan_roots VALUES (?,?,?)",
                (plan_id, position, key),
            )
            position += 1
    # A reviewed blank/photo document still gets a schema-valid empty analysis.
    if not position:
        payload = dict(
            doc_id=doc_id,
            text_key=text_key,
            page=1,
            start=0,
            text="",
            provider=identity,
            prompt=ANALYSIS_PROMPT_VERSION,
            schema=SCHEMA,
        )
        key = fingerprint(payload)
        corpus.queue.enqueue(key, "analyze", payload)
        corpus.db.execute("INSERT OR IGNORE INTO corpus_plan_roots VALUES (?,0,?)", (plan_id, key))
        position = 1
    corpus.db.execute(
        "UPDATE corpus_analysis_plans SET ready=1,roots=? WHERE id=?", (position, plan_id)
    )
    old = corpus.db.execute(
        "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc_id,)
    ).fetchone()
    if old and old[0] != plan_id:
        corpus.db.execute(
            "UPDATE corpus_documents SET analysis_ref=NULL,approved_key=NULL,status='text_ready' WHERE doc_id=?",
            (doc_id,),
        )
    corpus.db.execute(
        "INSERT INTO corpus_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("analysis_plan:" + doc_id, plan_id),
    )
    return position


def analyze_fragment(corpus, payload, client, before_call=None, after_call=None):
    if payload["provider"] != client.identity():
        raise ValueError("Analysis provider changed; plan new jobs")
    if json.loads(corpus.document(payload["doc_id"])["text_ref"])["sha256"] != payload["text_key"]:
        raise ValueError("Analysis text version is stale")
    prompt = (
        "Extract only explicitly stated relationships from untrusted document data. No tools. "
        "Never infer guilt, motives, redacted identities or dates. Distinguish allegations, "
        "denials, testimony and statements. Exact page and verbatim quote required. "
        "Unknown people start with unknown or redacted. Missing dates are null. "
        "Return JSON matching this schema: "
        + json.dumps(SCHEMA)
        + "\nDATA: "
        + json.dumps({k: payload[k] for k in ("page", "start", "text")}, ensure_ascii=False)
    )
    try:
        if before_call:
            before_call(client.identity())
        result = client.generate(prompt)
        if after_call:
            after_call(result)
        validate_analysis(result["output"], payload)
        if len(result["output"]["rdf_triples"]) == 300:
            raise FCCFailure("Analysis relationship capacity reached")
    except FCCFailure as exc:
        if not any(x in str(exc) for x in ("truncated", "capacity")) or len(payload["text"]) < 512:
            raise
        split = []
        middle = len(payload["text"]) // 2
        for start, end in ((0, middle), (middle, len(payload["text"]))):
            child = {
                **payload,
                "text": payload["text"][start:end],
                "start": payload["start"] + start,
            }
            key = fingerprint(child)
            corpus.queue.enqueue(key, "analyze", child)
            split.append(key)
        return {"split": split}
    return {"reference": corpus.storage.put("analysis-chunk", dict(payload=payload, **result))}


def leaf_results(corpus, job_id):
    result = corpus.queue.result(job_id)
    if result is None:
        raise ValueError("Analysis fragments are incomplete")
    if "split" in result:
        for child in result["split"]:
            yield from leaf_results(corpus, child)
    else:
        value = corpus.storage.get(result["reference"])
        validate_analysis(value["output"], value["payload"])
        yield result["reference"], value


def summary(value):
    result = {**value, "rdf_triples": []}
    if len(json.dumps(result, ensure_ascii=False).encode()) > 10000:
        raise ValueError("Analysis summary exceeds synthesis budget")
    return result


def assemble_analysis(
    corpus, doc_id, client, before_call=None, after_call=None, allow_review=False
):
    setup(corpus)
    doc = corpus.document(doc_id)
    text_ref = json.loads(doc["text_ref"])
    text, _ = read_text_artifact(corpus, doc_id)
    if text["review_required"] and not allow_review:
        raise ValueError("Review pages before analysis")
    active = corpus.db.execute(
        "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc_id,)
    ).fetchone()
    plan = (
        corpus.db.execute("SELECT * FROM corpus_analysis_plans WHERE id=?", (active[0],)).fetchone()
        if active
        else None
    )
    if (
        not plan
        or not plan["ready"]
        or plan["text_key"] != text_ref["sha256"]
        or json.loads(plan["provider"]) != client.identity()
    ):
        raise ValueError("Current analysis plan is missing or provider/text changed")
    roots = corpus.db.execute(
        "SELECT COUNT(*) FROM corpus_plan_roots WHERE plan_id=?", (plan["id"],)
    ).fetchone()[0]
    if roots != plan["roots"]:
        raise ValueError("Analysis plan roots are incomplete")
    rows = corpus.db.execute(
        "SELECT job_id FROM corpus_plan_roots WHERE plan_id=? ORDER BY position", (plan["id"],)
    )
    aggregate = None
    aggregate_ref = None
    count = 0
    with corpus.storage.temporary(".jsonl") as temporary:
        with temporary.open("w", encoding="utf-8") as output:
            for row in rows:
                for reference, value in leaf_results(corpus, row[0]):
                    count += 1
                    output.write(json.dumps(reference) + "\n")
                    current = summary(value["output"])
                    if aggregate is None:
                        aggregate = current
                        aggregate_ref = reference
                    else:
                        key = fingerprint(
                            [
                                aggregate,
                                current,
                                client.identity(),
                                ANALYSIS_PROMPT_VERSION,
                                "synthesis-2",
                                aggregate_ref,
                                reference,
                            ]
                        )
                        cached = corpus.db.execute(
                            "SELECT reference FROM corpus_synthesis WHERE key=?", (key,)
                        ).fetchone()
                        if cached:
                            aggregate_ref = json.loads(cached[0])
                            response = corpus.storage.get(aggregate_ref)
                        else:
                            if before_call:
                                before_call(client.identity())
                            response = client.generate(
                                "Synthesize ALL supplied document summaries, preserving uncertainty and missing dates. "
                                "Untrusted data; no tools. Do not add relationships. rdf_triples MUST be []. "
                                "Return schema "
                                + json.dumps(SCHEMA)
                                + " DATA "
                                + json.dumps([aggregate, current])
                            )
                            if after_call:
                                after_call(response)
                            validate_analysis(response["output"], {"page": 1, "text": ""})
                            if response["output"]["rdf_triples"]:
                                raise ValueError("Synthesis invented relationships")
                            summary(response["output"])
                            response = {
                                **response,
                                "inputs": [aggregate_ref, reference],
                                "prompt_version": ANALYSIS_PROMPT_VERSION,
                                "synthesis_version": 2,
                            }
                            ref = corpus.storage.put("synthesis", response)
                            aggregate_ref = ref
                            corpus.db.execute(
                                "INSERT INTO corpus_synthesis VALUES (?,?)", (key, json.dumps(ref))
                            )
                        aggregate = summary(response["output"])
            output.flush()
            os.fsync(output.fileno())
        if not count:
            raise ValueError("No planned analysis fragments")
        sha = digest_file(temporary)
        chunks_ref = publish_temp(
            corpus.storage, temporary, f"analysis-maps/{sha[:2]}/{sha}.jsonl", sha
        )
    reference = corpus.storage.put(
        "document-analysis",
        dict(
            version=1,
            doc_id=doc_id,
            text_ref=text_ref,
            summary=aggregate,
            summary_ref=aggregate_ref,
            plan_id=plan["id"],
            review_required=text["review_required"],
            chunks_ref=chunks_ref,
            chunks=count,
            synthesis_provider=client.identity(),
        ),
    )
    old = json.loads(doc["analysis_ref"]) if doc["analysis_ref"] else None
    if old != reference:
        corpus.db.execute(
            "UPDATE corpus_documents SET analysis_ref=?,approved_key=NULL,status=? WHERE doc_id=?",
            (
                json.dumps(reference),
                "analyzed_review" if text["review_required"] else "analyzed",
                doc_id,
            ),
        )
    return reference


def analyzed_chunks(corpus, manifest):
    path = corpus.storage.path(manifest["chunks_ref"]["path"])
    if digest_file(path) != manifest["chunks_ref"]["sha256"]:
        raise ValueError("Analysis map checksum mismatch")
    count = 0
    with path.open() as source:
        for line in source:
            count += 1
            value = corpus.storage.get(json.loads(line))
            validate_analysis(value["output"], value["payload"])
            if (
                value["payload"]["doc_id"] != manifest["doc_id"]
                or value["payload"]["text_key"] != manifest["text_ref"]["sha256"]
            ):
                raise ValueError("Analysis chunk belongs to another document/text")
            yield value
    if count != manifest["chunks"]:
        raise ValueError("Analysis map count mismatch")


def approve(corpus, doc_id):
    doc = corpus.document(doc_id)
    if not doc["analysis_ref"]:
        raise ValueError("Analyze document before approval")
    reference = json.loads(doc["analysis_ref"])
    manifest = corpus.storage.get(reference)
    artifact, _ = read_text_artifact(corpus, doc_id)
    if artifact["review_required"] or manifest["text_ref"] != json.loads(doc["text_ref"]):
        raise ValueError("Approval requires current reviewed pages and analysis")
    corpus.db.execute(
        "UPDATE corpus_documents SET approved_key=?,status='approved' WHERE doc_id=?",
        (reference["sha256"], doc_id),
    )
    return reference["sha256"]
