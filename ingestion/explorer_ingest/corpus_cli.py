"""Explicit single-machine corpus commands; no inference on import/status/plan."""

import argparse
import hashlib
import json
import logging
import signal
import sys
from logging.handlers import RotatingFileHandler

from .corpus_analysis import analyze_fragment, approve, assemble_analysis, plan_analysis
from .corpus_contracts import fingerprint
from .corpus_export import export_corpus
from .corpus_extract import extract_page, inspect_document
from .corpus_fcc import FCCFailure
from .corpus_formats import expand_container
from .corpus_graph import classify_tag, initialize_tags, propose_alias, review_alias, review_tag
from .corpus_ocr import ocr_page, review_page, route_native
from .corpus_registry import Corpus
from .corpus_runtime import CallBudget, RunConfig, free_route, operation_lock, read_status
from .corpus_sources import freeze_sources
from .corpus_storage import CorpusLimits
from .corpus_text import assemble_text
from .corpus_workflow import advance, advance_document


def plan_pages(corpus, config, doc_id=None):
    count = 0
    corpus.db.execute("BEGIN IMMEDIATE")
    try:
        sql = "SELECT * FROM corpus_documents WHERE page_count>0" + (
            " AND doc_id=?" if doc_id else ""
        )
        for doc in corpus.db.execute(sql, (doc_id,) if doc_id else ()):
            for page in range(1, doc["page_count"] + 1):
                payload = dict(
                    doc_id=doc["doc_id"],
                    page=page,
                    dpi=config.dpi,
                    force_ocr=config.force_ocr,
                    provider=vars(config.fcc()),
                    extractor="formats-2",
                )
                key = fingerprint([doc["sha256"], payload])
                corpus.queue.enqueue(key, "extract", payload)
                count += 1
                if count % 5000 == 0:
                    corpus.storage.check()
                    corpus.db.commit()
                    corpus.db.execute("BEGIN IMMEDIATE")
        corpus.db.commit()
    except BaseException:
        corpus.db.rollback()
        raise
    return count


def work(corpus, config, limit, stage, allow_fcc=False, job_id=None, allow_review=False):
    if limit < 1:
        raise ValueError("Positive work limit required")
    budget = CallBudget(corpus, config)
    if allow_fcc:
        proof = free_route(config)
        corpus.storage.put("free-route-proof", proof)
    client = budget.client()
    stop = False

    def pause(*args):
        nonlocal stop
        stop = True

    old = {s: signal.signal(s, pause) for s in (signal.SIGINT, signal.SIGTERM)}
    completed = 0
    attempted = 0
    paused = None
    try:
        while attempted < limit and not stop:
            # Offline work never claims OCR/analysis jobs.
            selected = stage
            if not allow_fcc and selected in ("ocr", "analyze", "assemble"):
                raise ValueError("Explicit --allow-fcc required")
            job = None
            priorities = (
                [selected]
                if selected
                else (
                    ["assemble", "analyze", "text", "ocr", "extract", "inspect"]
                    if allow_fcc
                    else ["text", "extract", "inspect"]
                )
            )
            for candidate in priorities:
                job = corpus.queue.claim("corpus-cli", candidate, job_id=job_id)
                if job:
                    break
            if not job:
                break
            attempted += 1
            payload = json.loads(job["payload"])
            doc_id = payload["doc_id"]
            try:
                corpus.storage.check()
                if job["stage"] == "inspect":
                    ref = inspect_document(corpus, doc_id)
                    if corpus.document(doc_id)["format"] == "zip":
                        ref = expand_container(corpus, doc_id)
                    else:
                        plan_pages(corpus, config, doc_id)
                elif job["stage"] == "extract":
                    if payload["provider"] != client.identity():
                        raise ValueError("Extraction routing changed; replan")
                    ref = extract_page(corpus, doc_id, payload["page"])
                    if corpus.document(doc_id)["format"] == "text":
                        from .corpus_ocr import select_page

                        native = corpus.storage.get(ref)
                        select_page(
                            corpus,
                            doc_id,
                            payload["page"],
                            native["text"],
                            "native",
                            dict(native_ref=ref),
                            issues=[]
                            if native["quality"]["accepted"]
                            else ["native_text_requires_review"],
                        )
                    else:
                        route_native(
                            corpus,
                            doc_id,
                            payload["page"],
                            client.identity(),
                            payload["dpi"],
                            payload["force_ocr"],
                        )
                elif job["stage"] == "text":
                    digest = hashlib.sha256()
                    for row in corpus.db.execute(
                        "SELECT selected_ref FROM corpus_pages WHERE doc_id=? ORDER BY page",
                        (doc_id,),
                    ):
                        if not row[0]:
                            raise ValueError("Canonical page selection is incomplete")
                        digest.update(row[0].encode())
                    if digest.hexdigest() != payload["pages_key"]:
                        raise ValueError("Canonical page selection changed; advance new jobs")
                    ref = assemble_text(corpus, doc_id, payload["allow_review"])
                elif job["stage"] == "assemble":
                    active = corpus.db.execute(
                        "SELECT value FROM corpus_meta WHERE key=?", ("analysis_plan:" + doc_id,)
                    ).fetchone()
                    if not active or active[0] != payload["plan_id"]:
                        raise ValueError("Stale synthesis plan")
                    ref = assemble_analysis(
                        corpus, doc_id, client, budget.before, budget.after, payload["allow_review"]
                    )
                elif job["stage"] == "ocr":
                    if payload["parameters"]["provider"] != client.identity():
                        raise ValueError("OCR routing changed; replan")
                    ref = ocr_page(
                        corpus,
                        doc_id,
                        payload["page"],
                        client,
                        payload["dpi"],
                        budget.before,
                        budget.after,
                    )
                elif job["stage"] == "analyze":
                    ref = analyze_fragment(corpus, payload, client, budget.before, budget.after)
                else:
                    raise ValueError("Unsupported worker stage")
                corpus.queue.finish(job["id"], job["token"], ref)
                logging.info("job_done stage=%s id=%s", job["stage"], job["id"])
                completed += 1
                try:
                    advance_document(corpus, config, doc_id, allow_review)
                except ValueError as exc:
                    logging.error("planner_blocked doc_id=%s error=%s", doc_id, type(exc).__name__)
            except FCCFailure as exc:
                corpus.queue.fail(
                    job["id"], job["token"], str(exc), exc.retryable, max(30, exc.retry_after)
                )
                logging.warning("fcc_failed job=%s reason=%s", job["id"], str(exc))
                if exc.retryable:
                    paused = str(exc)
                    break
            except Exception as exc:
                corpus.queue.fail(job["id"], job["token"], type(exc).__name__, retryable=False)
                logging.error(
                    "job_failed stage=%s id=%s error=%s",
                    job["stage"],
                    job["id"],
                    type(exc).__name__,
                )
        return dict(
            completed_jobs=completed,
            attempted_jobs=attempted,
            paused=paused or ("signal" if stop else None),
            status=read_status(config.root),
        )
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "plan", "preflight"):
        sub.add_parser(name)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("archive_root")
    freeze.add_argument("output")
    freeze.add_argument("--scope", choices=["pilot", "full"], default="pilot")
    imp = sub.add_parser("import")
    imp.add_argument("selection")
    w = sub.add_parser("work")
    w.add_argument("--limit", type=int, default=100)
    w.add_argument("--stage", choices=["inspect", "extract", "ocr", "text", "analyze", "assemble"])
    w.add_argument("--allow-fcc", action="store_true")
    w.add_argument("--job-id")
    w.add_argument("--allow-review", action="store_true")
    adv = sub.add_parser("advance")
    adv.add_argument("--limit", type=int, default=10000)
    adv.add_argument("--after", default="")
    adv.add_argument("--allow-review", action="store_true")
    for name in ("text", "analyze-plan", "synthesize", "approve"):
        cmd = sub.add_parser(name)
        cmd.add_argument("doc_id")
        if name in ("text", "analyze-plan", "synthesize"):
            cmd.add_argument("--allow-review", action="store_true")
        if name == "synthesize":
            cmd.add_argument("--allow-fcc", action="store_true", required=True)
    retry = sub.add_parser("retry-job")
    retry.add_argument("job_id")
    retry.add_argument("--reason", required=True)
    retry.add_argument("--extra", type=int, default=1)
    sup = sub.add_parser("supersede-job")
    sup.add_argument("job_id")
    sup.add_argument("replacement")
    sup.add_argument("--reason", required=True)
    r = sub.add_parser("review-page")
    r.add_argument("doc_id")
    r.add_argument("page", type=int)
    r.add_argument("--reviewer", required=True)
    r.add_argument("--reason", required=True)
    r.add_argument("--no-body-text", action="store_true")
    tags = sub.add_parser("init-tags")
    tags.add_argument("--clusters", required=True)
    tag = sub.add_parser("propose-tag")
    tag.add_argument("tag")
    tag.add_argument("--allow-fcc", action="store_true", required=True)
    tag = sub.add_parser("review-tag")
    tag.add_argument("tag")
    tag.add_argument("cluster_id", type=int)
    tag.add_argument("--reason", required=True)
    alias = sub.add_parser("propose-alias")
    alias.add_argument("original")
    alias.add_argument("--candidate", action="append", default=[])
    alias.add_argument("--allow-fcc", action="store_true", required=True)
    alias = sub.add_parser("review-alias")
    alias.add_argument("original")
    alias.add_argument("canonical")
    alias.add_argument("--reason", required=True)
    exp = sub.add_parser("export")
    exp.add_argument("output")
    exp.add_argument("--clusters", required=True)
    exp.add_argument("--base-db")
    exp.add_argument("--include-unreviewed", action="store_true")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    config = RunConfig.load(args.config)
    if args.command == "status":
        result = read_status(config.root)
        print(json.dumps(result, indent=2))
        return 0 if result["complete"] else 3
    if args.command == "preflight":
        print(json.dumps(free_route(config), indent=2))
        return 0
    if args.command == "freeze":
        print(json.dumps(freeze_sources(args.archive_root, args.output, args.scope), indent=2))
        return 0
    corpus = Corpus(
        config.root, create=args.command == "init", limits=CorpusLimits(**config.limits)
    )
    handler = RotatingFileHandler(
        corpus.storage.path("operations.log"), maxBytes=5 * 1024**2, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.INFO)
    try:
        with operation_lock(corpus), corpus.storage.scratch_environment():
            if args.command == "init":
                result = corpus.status()
            elif args.command == "import":
                result = corpus.import_selection(args.selection)
            elif args.command == "plan":
                inspections = 0
                for row in corpus.db.execute("SELECT id FROM selections WHERE state='complete'"):
                    inspections += corpus.plan_inspection(row[0])
                result = dict(inspections=inspections, pages=plan_pages(corpus, config))
            elif args.command == "advance":
                result = advance(corpus, config, args.limit, args.after, args.allow_review)
            elif args.command == "work":
                result = work(
                    corpus,
                    config,
                    args.limit,
                    args.stage,
                    args.allow_fcc,
                    args.job_id,
                    args.allow_review,
                )
            elif args.command == "text":
                result = assemble_text(corpus, args.doc_id, args.allow_review)
            elif args.command == "analyze-plan":
                result = plan_analysis(
                    corpus,
                    args.doc_id,
                    vars(config.fcc()),
                    config.max_fragment_bytes,
                    args.allow_review,
                )
            elif args.command == "synthesize":
                corpus.storage.put("free-route-proof", free_route(config))
                budget = CallBudget(corpus, config)
                result = assemble_analysis(
                    corpus,
                    args.doc_id,
                    budget.client(),
                    budget.before,
                    budget.after,
                    args.allow_review,
                )
            elif args.command == "retry-job":
                result = corpus.queue.retry_failed(args.job_id, args.reason, args.extra)
            elif args.command == "supersede-job":
                result = corpus.queue.supersede(args.job_id, args.replacement, args.reason)
            elif args.command == "review-page":
                result = review_page(
                    corpus,
                    args.doc_id,
                    args.page,
                    args.no_body_text,
                    reviewer=args.reviewer,
                    reason=args.reason,
                )
            elif args.command == "approve":
                result = approve(corpus, args.doc_id)
            elif args.command == "init-tags":
                result = initialize_tags(corpus.db, args.clusters)
            elif args.command == "review-tag":
                result = review_tag(corpus.db, args.tag, args.cluster_id, args.reason)
            elif args.command == "review-alias":
                result = review_alias(corpus.db, args.original, args.canonical, args.reason)
            elif args.command in ("propose-tag", "propose-alias"):
                corpus.storage.put("free-route-proof", free_route(config, require_image=False))
                budget = CallBudget(corpus, config)
                result = (
                    classify_tag(corpus, args.tag, budget.client(), budget.before, budget.after)
                    if args.command == "propose-tag"
                    else propose_alias(
                        corpus,
                        args.original,
                        args.candidate,
                        budget.client(),
                        budget.before,
                        budget.after,
                    )
                )
            elif args.command == "export":
                result = export_corpus(
                    corpus, args.output, args.clusters, args.base_db, args.include_unreviewed
                )
            else:
                raise ValueError("Unknown command")
        print(json.dumps(result, indent=2))
        if args.command == "work" and not result["status"]["complete"]:
            return 3
        return 0
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
        corpus.close()


def entry(argv=None):
    try:
        return main(argv)
    except Exception as exc:
        print("Corpus operation failed: " + type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(entry())
