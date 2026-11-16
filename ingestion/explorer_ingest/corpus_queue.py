"""Durable leased jobs; one accepted result, bounded retries, no implicit inference."""

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .corpus_contracts import STAGES


class JobQueue:
    def __init__(self, path):
        path = Path(path)
        if path.is_symlink() or not path.parent.is_dir():
            raise ValueError("Queue needs an existing safe storage directory")
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          PRAGMA synchronous=FULL;
          PRAGMA foreign_keys=ON;
          PRAGMA cache_size=-8192;
          CREATE TABLE IF NOT EXISTS jobs(
            id TEXT PRIMARY KEY, stage TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL, available_at REAL NOT NULL DEFAULT 0,
            owner TEXT, token TEXT, lease_until REAL, result TEXT, error TEXT);
          CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status,available_at,stage);
          CREATE INDEX IF NOT EXISTS jobs_claim_all ON jobs(status,available_at);
          CREATE INDEX IF NOT EXISTS jobs_claim_stage ON jobs(status,stage,available_at);
          CREATE TABLE IF NOT EXISTS job_retry_grants(
            seq INTEGER PRIMARY KEY,job_id TEXT,extra INTEGER,reason TEXT,at REAL);
          CREATE INDEX IF NOT EXISTS job_retry_job ON job_retry_grants(job_id);
          CREATE TABLE IF NOT EXISTS job_events(
            seq INTEGER PRIMARY KEY, at REAL NOT NULL, job_id TEXT NOT NULL,
            event TEXT NOT NULL, detail TEXT);
        """)

    def close(self):
        self.db.close()

    def enqueue(self, key, stage, payload, max_attempts=3):
        if stage not in STAGES or max_attempts < 1:
            raise ValueError("Invalid job stage or attempt budget")
        body = json.dumps(payload, sort_keys=True, allow_nan=False)
        self.db.execute(
            "INSERT OR IGNORE INTO jobs(id,stage,payload,max_attempts) VALUES (?,?,?,?)",
            (key, stage, body, max_attempts),
        )
        row = self.db.execute("SELECT * FROM jobs WHERE id=?", (key,)).fetchone()
        if row["stage"] != stage or row["payload"] != body or row["max_attempts"] != max_attempts:
            raise ValueError("Job identity collides with different immutable inputs")
        return key

    def claim(self, owner, stage=None, lease_seconds=600, now=None, job_id=None):
        now = time.time() if now is None else now
        if not owner or not 1 <= lease_seconds <= 3600 or (stage and stage not in STAGES):
            raise ValueError("Invalid worker lease")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                """UPDATE jobs SET status=CASE WHEN attempts<max_attempts+COALESCE((SELECT SUM(extra) FROM job_retry_grants g WHERE g.job_id=jobs.id),0) THEN 'retry' ELSE 'failed' END,
                owner=NULL,token=NULL,lease_until=NULL,error='Previous worker lease expired'
                WHERE status='running' AND lease_until<=?""",
                (now,),
            )
            row = None
            for state in ("retry", "pending"):
                if job_id:
                    row = self.db.execute(
                        "SELECT * FROM jobs WHERE id=? AND status=? AND available_at<=? AND (? IS NULL OR stage=?)",
                        (job_id, state, now, stage, stage),
                    ).fetchone()
                elif stage:
                    row = self.db.execute(
                        "SELECT * FROM jobs WHERE status=? AND stage=? AND available_at<=? ORDER BY available_at,rowid LIMIT 1",
                        (state, stage, now),
                    ).fetchone()
                else:
                    row = self.db.execute(
                        "SELECT * FROM jobs WHERE status=? AND available_at<=? ORDER BY available_at,rowid LIMIT 1",
                        (state, now),
                    ).fetchone()
                if row:
                    break
            if row is None:
                self.db.commit()
                return None
            token = uuid.uuid4().hex
            self.db.execute(
                """UPDATE jobs SET status='running',attempts=attempts+1,
                owner=?,token=?,lease_until=? WHERE id=?""",
                (owner, token, now + lease_seconds, row["id"]),
            )
            self.db.execute(
                "INSERT INTO job_events(at,job_id,event) VALUES (?,?,'claimed')", (now, row["id"])
            )
            self.db.commit()
            return dict(self.db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
        except BaseException:
            self.db.rollback()
            raise

    def heartbeat(self, job_id, token, lease_seconds=600, now=None):
        now = time.time() if now is None else now
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Invalid heartbeat lease")
        changed = self.db.execute(
            """UPDATE jobs SET lease_until=? WHERE id=? AND token=?
            AND status='running' AND lease_until>?""",
            (now + lease_seconds, job_id, token, now),
        ).rowcount
        if changed != 1:
            raise ValueError("Stale job lease")

    def finish(self, job_id, token, result, now=None):
        self._resolve(job_id, token, result=result, now=now)

    def fail(self, job_id, token, error, retryable=True, delay=5, now=None):
        if delay < 0:
            raise ValueError("Retry delay cannot be negative")
        self._resolve(job_id, token, error=error, retryable=retryable, delay=delay, now=now)

    def _resolve(self, job_id, token, result=None, error=None, retryable=False, delay=0, now=None):
        now = time.time() if now is None else now
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE id=? AND token=? AND status='running' AND lease_until>?",
                (job_id, token, now),
            ).fetchone()
            if not row:
                raise ValueError("Stale job lease")
            state = (
                "done"
                if error is None
                else (
                    "retry"
                    if retryable and row["attempts"] < row["max_attempts"] + self.granted(job_id)
                    else "failed"
                )
            )
            self.db.execute(
                """UPDATE jobs SET status=?,result=?,error=?,available_at=?,owner=NULL,
                token=NULL,lease_until=NULL WHERE id=?""",
                (
                    state,
                    json.dumps(result, allow_nan=False) if result is not None else None,
                    error,
                    now + delay,
                    job_id,
                ),
            )
            self.db.execute(
                "INSERT INTO job_events(at,job_id,event,detail) VALUES (?,?,?,?)",
                (now, job_id, state, error),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def result(self, key):
        row = self.db.execute(
            "SELECT result FROM jobs WHERE id=? AND status='done'", (key,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def status(self):
        states = dict(self.db.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status"))
        return {
            "jobs": sum(states.values()),
            "states": states,
            "complete": bool(states) and sum(states.values()) == states.get("done", 0),
        }

    def granted(self, job_id):
        return self.db.execute(
            "SELECT COALESCE(SUM(extra),0) FROM job_retry_grants WHERE job_id=?", (job_id,)
        ).fetchone()[0]

    def retry_failed(self, job_id, reason, extra=1):
        if not reason.strip() or not 1 <= extra <= 3:
            raise ValueError("Recovery needs a reason and bounded attempts")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE id=? AND status='failed'", (job_id,)
            ).fetchone()
            if not row:
                raise ValueError("Only failed jobs can receive retry grants")
            self.db.execute(
                "INSERT INTO job_retry_grants(job_id,extra,reason,at) VALUES (?,?,?,?)",
                (job_id, extra, reason, time.time()),
            )
            self.db.execute("UPDATE jobs SET status='retry',available_at=0 WHERE id=?", (job_id,))
            self.db.execute(
                "INSERT INTO job_events(at,job_id,event,detail) VALUES (?,?,'operator_retry',?)",
                (time.time(), job_id, reason),
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def supersede(self, job_id, replacement, reason):
        old = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        new = self.db.execute("SELECT * FROM jobs WHERE id=?", (replacement,)).fetchone()
        if (
            not reason.strip()
            or not old
            or not new
            or old["id"] == new["id"]
            or old["stage"] != new["stage"]
            or old["status"] == "running"
        ):
            raise ValueError("Invalid replacement job")
        a, b = json.loads(old["payload"]), json.loads(new["payload"])
        if any(a.get(k) != b.get(k) for k in ("doc_id", "page")):
            raise ValueError("Replacement belongs to another page/document")
        self.db.execute("UPDATE jobs SET status='superseded' WHERE id=?", (job_id,))
        self.db.execute(
            "INSERT INTO job_events(at,job_id,event,detail) VALUES (?,?,'superseded',?)",
            (time.time(), job_id, json.dumps(dict(replacement=replacement, reason=reason))),
        )
