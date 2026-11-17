"""Incremental downstream planning; never require one CLI command per document."""

import hashlib
import json

from .corpus_analysis import (
    ANALYSIS_PROMPT_VERSION,
    SCHEMA,
    plan_analysis,
    plan_prompt_version,
    setup,
)
from .corpus_contracts import SUPPORTED_ANALYSIS_PROMPTS, fingerprint


def tree_done(corpus, key):
    result = corpus.queue.result(key)
    if result is None:
        return False
    return all(tree_done(corpus, k) for k in result.get("split", []))


def advance_document(corpus, config, doc_id, allow_review=False):
    doc = corpus.document(doc_id)
    if not doc["page_count"]:
        return 0
    count = 0
    review = False
    digest = hashlib.sha256()
    for row in corpus.db.execute(
        "SELECT page,selected_ref,status FROM corpus_pages WHERE doc_id=? ORDER BY page", (doc_id,)
    ):
        count += 1
        if not row["selected_ref"] or row["page"] != count:
            return 0
        review |= row["status"] != "accepted"
        digest.update(row["selected_ref"].encode())
    if count != doc["page_count"] or (review and not allow_review):
        return 0
    if doc["text_ref"] and not doc["analysis_ref"]:
        desired = "text_review" if review else "text_ready"
        if doc["status"] != desired:
            corpus.db.execute(
                "UPDATE corpus_documents SET status=? WHERE doc_id=?", (desired, doc_id)
            )
    if not doc["text_ref"]:
        payload = dict(doc_id=doc_id, pages_key=digest.hexdigest(), allow_review=allow_review)
        key = fingerprint(["canonical-text-2", doc["sha256"], payload])
        corpus.queue.enqueue(key, "text", payload)
        return 1
    active = corpus.db.execute(
        "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc_id,)
    ).fetchone()
    # Resume the chosen version; upgrades are explicit per-document plans.
    prompt_version = (
        plan_prompt_version(corpus, active[0]) if active else None
    ) or ANALYSIS_PROMPT_VERSION
    if prompt_version not in SUPPORTED_ANALYSIS_PROMPTS:
        prompt_version = ANALYSIS_PROMPT_VERSION
    expected = fingerprint(
        [
            doc_id,
            json.loads(doc["text_ref"])["sha256"],
            vars(config.fcc()),
            config.max_fragment_bytes,
            SCHEMA,
            prompt_version,
            allow_review,
        ]
    )
    if not active or active[0] != expected:
        plan_analysis(corpus, doc_id, vars(config.fcc()), config.max_fragment_bytes, allow_review)
    plan_id = corpus.db.execute(
        "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc_id,)
    ).fetchone()[0]
    if doc["analysis_ref"]:
        manifest = corpus.storage.get(json.loads(doc["analysis_ref"]))
        if manifest.get("plan_id") == plan_id:
            return 0
    for row in corpus.db.execute(
        "SELECT job_id FROM corpus_plan_roots WHERE plan_id=?", (plan_id,)
    ):
        if not tree_done(corpus, row[0]):
            return 0
    payload = dict(
        doc_id=doc_id, plan_id=plan_id, provider=vars(config.fcc()), allow_review=allow_review
    )
    corpus.queue.enqueue(fingerprint(["document-synthesis-2", payload]), "assemble", payload)
    return 1


def advance(corpus, config, limit=10000, after="", allow_review=False):
    if not 1 <= limit <= 10000000:
        raise ValueError("Invalid planner window")
    setup(corpus)
    count = scheduled = 0
    last = after
    for row in corpus.db.execute(
        "SELECT doc_id FROM corpus_documents WHERE doc_id>? ORDER BY doc_id LIMIT ?", (after, limit)
    ):
        last = row[0]
        count += 1
        scheduled += advance_document(corpus, config, last, allow_review)
    more = corpus.db.execute(
        "SELECT 1 FROM corpus_documents WHERE doc_id>? LIMIT 1", (last,)
    ).fetchone()
    return dict(visited=count, scheduled_documents=scheduled, next=last if more else None)
