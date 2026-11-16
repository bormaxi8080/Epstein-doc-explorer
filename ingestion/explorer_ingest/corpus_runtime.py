"""Explicit operational configuration, free-route admission and honest coverage."""

import base64
import hashlib
import json
import os
import time
import urllib.request
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .corpus_fcc import FCCConfig, FCCFailure, FCCMessages
from .corpus_storage import CorpusLimits
from .inventory_parts import guard_directory
from .state import readonly_db
from .transport import tls_context


@dataclass(frozen=True)
class RunConfig:
    root: str
    model: str
    routing_revision: str
    base_url: str = "http://127.0.0.1:8082"
    request_timeout: float = 120
    max_output_tokens: int = 8192
    max_calls_per_run: int = 300
    max_calls_total: int = 1000
    minimum_call_interval: float = 5
    dpi: int = 200
    force_ocr: bool = False
    max_fragment_bytes: int = 12000
    limits: dict = field(default_factory=dict)

    def __post_init__(self):
        if not Path(self.root).is_absolute() or not self.model or not self.routing_revision:
            raise ValueError("Explicit root/model/routing revision required")
        if (
            self.max_calls_per_run < 1
            or self.max_calls_total < 1
            or self.minimum_call_interval < 5
            or not 50 <= self.dpi <= 400
            or not 128 <= self.max_fragment_bytes <= 24000
        ):
            raise ValueError("Invalid call/render/fragment budgets")
        if not 1 <= self.request_timeout <= 300 or not 128 <= self.max_output_tokens <= 16384:
            raise ValueError("Invalid FCC timeout/output budget")
        CorpusLimits(**self.limits)
        self.fcc()

    def fcc(self):
        return FCCConfig(
            self.model,
            self.routing_revision,
            self.base_url,
            timeout=self.request_timeout,
            max_tokens=self.max_output_tokens,
        )

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text()))


def free_route(config, require_image=True):
    # No paid fallback or unverified route. Catalogue retrieval itself makes no inference.
    prefix = "claude-3-freecc-no-thinking/open_router/"
    if not config.model.startswith(prefix) or not config.model.endswith(":free"):
        raise ValueError("Only an explicitly verified OpenRouter :free route is admitted")
    model = config.model[len(prefix) :]
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/models", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30, context=tls_context()) as response:
        raw = response.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2:
        raise ValueError("Model catalogue too large")
    entry = next((v for v in json.loads(raw)["data"] if v["id"] == model), None)
    if not entry:
        raise ValueError("Free model unavailable")
    pricing = entry["pricing"]
    if any(float(pricing.get(k, "1")) != 0 for k in ("prompt", "completion")):
        raise ValueError("Route is not free")
    if require_image and "image" not in entry["architecture"]["input_modalities"]:
        raise ValueError("Route does not advertise image input")
    return dict(
        model=model, pricing=pricing, architecture=entry["architecture"], checked_at=time.time()
    )


class CallBudget:
    def __init__(self, corpus, config):
        self.corpus, self.config = corpus, config
        self.calls = 0
        self.current = None
        corpus.db.execute("""CREATE TABLE IF NOT EXISTS corpus_model_calls(
          id INTEGER PRIMARY KEY,at REAL,model TEXT,state TEXT,usage TEXT,raw_ref TEXT);""")

    def before(self, identity):
        c = self.config
        db = self.corpus.db
        previous = db.execute(
            "SELECT COUNT(*),COALESCE(MAX(at),0) FROM corpus_model_calls"
        ).fetchone()
        pilot = 0
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_model_calls'").fetchone():
            pilot = db.execute("SELECT COUNT(*) FROM pilot_model_calls").fetchone()[0]
        if self.calls >= c.max_calls_per_run or previous[0] + pilot >= c.max_calls_total:
            raise FCCFailure("Configured free call budget reached", True, 300)
        if identity != vars(c.fcc()):
            raise ValueError("Call provider differs from admitted configuration")
        delay = max(0, c.minimum_call_interval - (time.time() - previous[1]))
        while delay > 0:
            time.sleep(min(delay, 1))
            delay = max(0, c.minimum_call_interval - (time.time() - previous[1]))
        self.corpus.storage.check()
        self.current = db.execute(
            "INSERT INTO corpus_model_calls(at,model,state) VALUES (?,?,'started')",
            (time.time(), c.model),
        ).lastrowid
        self.calls += 1

    def record(self, raw):
        ref = self.corpus.storage.put(
            "fcc-envelope",
            {
                "encoding": "base64",
                "body": base64.b64encode(raw).decode("ascii"),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        if self.current is not None:
            self.corpus.db.execute(
                "UPDATE corpus_model_calls SET raw_ref=? WHERE id=?",
                (json.dumps(ref), self.current),
            )

    def after(self, result):
        self.corpus.db.execute(
            "UPDATE corpus_model_calls SET state='completed',usage=? WHERE id=?",
            (json.dumps(result["usage"]), self.current),
        )

    def client(self):
        return FCCMessages(
            self.config.fcc(), os.environ.get("FCC_API_KEY", ""), recorder=self.record
        )


@contextmanager
def operation_lock(corpus):
    import fcntl

    corpus.storage.check()
    with corpus.storage.path("operator.lock").open("a") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another corpus operation is active") from None
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_status(root):
    # Read-only diagnostics do not initialize directories, take a writer lease or mutate WAL.
    root = Path(root)
    guard = guard_directory(root)
    marker = json.loads((root / "identity.json").read_text())
    with closing(readonly_db(root / "corpus.sqlite3")) as db:
        docs = dict(db.execute("SELECT status,COUNT(*) FROM corpus_documents GROUP BY status"))
        pages = dict(db.execute("SELECT status,COUNT(*) FROM corpus_pages GROUP BY status"))
        jobs = dict(db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status"))
        counts = db.execute("""SELECT COUNT(*),COUNT(text_ref),COUNT(analysis_ref),COUNT(approved_key),
           SUM(CASE WHEN page_count IS NULL THEN 1 ELSE 0 END) FROM corpus_documents""").fetchone()
        unsupported = sum(v for k, v in docs.items() if k in ("unsupported", "container_pending"))
        missing_pages = db.execute(
            "SELECT COUNT(*) FROM corpus_pages WHERE selected_ref IS NULL"
        ).fetchone()[0]
        review = db.execute(
            "SELECT COUNT(*) FROM corpus_pages WHERE status!='accepted'"
        ).fetchone()[0]
        failures = jobs.get("failed", 0)
        unfinished = sum(v for k, v in jobs.items() if k not in ("done", "superseded"))
        complete = (
            bool(counts[0])
            and counts[0] - docs.get("container_expanded", 0) == counts[3]
            and not (unfinished or unsupported or missing_pages or review or counts[4])
        )
        selections = [
            dict(scope=json.loads(header)["scope"], state=state, files=imported)
            for header, state, imported in db.execute(
                "SELECT header,state,imported FROM selections"
            )
        ]
        result = dict(
            selections=selections,
            full_archive_complete=complete
            and any(r["scope"] == "full" and r["state"] == "complete" for r in selections),
            uuid=marker["uuid"],
            documents=docs,
            pages=pages,
            jobs=jobs,
            texts=counts[1],
            analyses=counts[2],
            approved=counts[3],
            uninspected=counts[4] or 0,
            unsupported=unsupported,
            pages_missing=missing_pages,
            pages_requiring_review=review,
            failed_jobs=failures,
            complete=complete,
        )
    guard()
    return result
