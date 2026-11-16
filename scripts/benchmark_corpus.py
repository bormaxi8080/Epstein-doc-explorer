#!/usr/bin/env python3
"""Measure durable synthetic scheduling on a chosen external root; no model calls."""

import argparse
import json
import resource
import sys
import time
from pathlib import Path

from explorer_ingest.corpus_queue import JobQueue
from explorer_ingest.corpus_storage import CorpusLimits, CorpusStorage
from explorer_ingest.state import atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=1000000)
    parser.add_argument("--resolve", type=int, default=1000)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 10000000 or not 0 <= args.resolve <= args.jobs:
        parser.error("Invalid synthetic job count")
    storage = CorpusStorage(args.root, create=True, limits=CorpusLimits())
    queue = JobQueue(storage.path("corpus.sqlite3"))
    storage.bind_queue()
    try:
        started = time.monotonic()
        queue.db.execute("BEGIN IMMEDIATE")
        for i in range(args.jobs):
            queue.enqueue(f"synthetic-{i:08d}", "inspect", dict(doc_id=f"synthetic-{i:08d}"))
            if (i + 1) % 5000 == 0:
                storage.check()
                queue.db.commit()
                queue.db.execute("BEGIN IMMEDIATE")
        queue.db.commit()
        scheduled = time.monotonic() - started
        resumed = JobQueue(storage.path("corpus.sqlite3"))
        resumed.close()
        started = time.monotonic()
        for _ in range(args.resolve):
            job = queue.claim("synthetic-benchmark")
            queue.finish(job["id"], job["token"], {"synthetic": True})
        resolved = time.monotonic() - started
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        result = dict(
            synthetic=True,
            model_calls=0,
            jobs=args.jobs,
            resolved=args.resolve,
            schedule_seconds=scheduled,
            jobs_per_second=args.jobs / scheduled,
            resolve_seconds=resolved,
            peak_rss_bytes=peak if sys.platform == "darwin" else peak * 1024,
            queue=queue.status(),
        )
        atomic_json(storage.path("benchmark.json"), result)
        print(json.dumps(result, indent=2))
    finally:
        queue.close()


if __name__ == "__main__":
    main()
