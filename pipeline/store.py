"""SQLite run state. One DB per run; every batch commits atomically (results + call log in one transaction)."""
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY, row_idx INTEGER NOT NULL, source_sha256 TEXT NOT NULL, text_sha TEXT NOT NULL,
  review_text TEXT NOT NULL, review_rating TEXT, review_likes TEXT, app_version TEXT, review_timestamp TEXT,
  is_empty INTEGER NOT NULL, synthetic INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS reviews_text ON reviews(text_sha);
CREATE TABLE IF NOT EXISTS results (
  review_id TEXT PRIMARY KEY REFERENCES reviews(review_id), status TEXT NOT NULL,
  topic TEXT, subtopic TEXT, intent TEXT, sentiment REAL, severity INTEGER, entities TEXT, evidence_quote TEXT,
  needs_review INTEGER, review_reason TEXT, label_config TEXT, cache_source_id TEXT, reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0, request_id TEXT, phase TEXT, notes TEXT, updated_at REAL);
CREATE INDEX IF NOT EXISTS results_status ON results(status);
CREATE TABLE IF NOT EXISTS calls (
  request_id TEXT PRIMARY KEY, invocation TEXT, role TEXT, model TEXT, phase TEXT, outcome TEXT, review_ids TEXT,
  label_config TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_creation_input_tokens INTEGER,
  cache_read_input_tokens INTEGER, cost_usd REAL, started_at REAL, latency_ms INTEGER, attempt INTEGER,
  stop_reason TEXT, error TEXT, provider_request_id TEXT, settings TEXT);
CREATE TABLE IF NOT EXISTS invocations (
  invocation TEXT PRIMARY KEY, command TEXT, phase TEXT, started_at REAL, ended_at REAL, wall_s REAL, notes TEXT);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
-- write-ahead journal: a request is recorded BEFORE it is sent, and its raw response is saved the moment it arrives,
-- so a crash between "paid response received" and "results committed" never loses (or re-buys) that work.
CREATE TABLE IF NOT EXISTS inflight (request_id TEXT PRIMARY KEY, role TEXT, review_ids TEXT, invocation TEXT,
  started_at REAL, rec TEXT);
CREATE TABLE IF NOT EXISTS responses (request_id TEXT PRIMARY KEY, role TEXT, raw TEXT, rec TEXT, received_at REAL,
  consumed INTEGER NOT NULL DEFAULT 0);
"""


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=60)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)

    # --- transactions -------------------------------------------------------------------------------
    def tx(self):
        store = self

        class _Tx:
            def __enter__(self):
                store.lock.acquire()
                store.db.execute("BEGIN IMMEDIATE")
                return store.db

            def __exit__(self, exc_type, exc, tb):
                try:
                    store.db.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store.lock.release()
                return False
        return _Tx()

    def q(self, sql, params=()):
        with self.lock:
            return self.db.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def backup(self, dest):
        """Consistent online snapshot (SQLite backup API) written atomically: tmp file then rename."""
        import os
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(".tmp")
        if tmp.exists():
            tmp.unlink()
        target = sqlite3.connect(tmp)
        with self.lock:
            self.db.backup(target)
        target.close()
        os.replace(tmp, dest)
        return dest

    def get(self, k, default=None):
        row = self.one("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(row["v"]) if row else default

    def put(self, k, v):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v)))

    # --- call log -----------------------------------------------------------------------------------
    @staticmethod
    def insert_call(db, call):
        cols = ["request_id", "invocation", "role", "model", "phase", "outcome", "review_ids", "label_config",
                "input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "cost_usd",
                "started_at", "latency_ms", "attempt", "stop_reason", "error", "provider_request_id", "settings"]
        vals = [json.dumps(call[c]) if c in ("review_ids", "settings") else call.get(c) for c in cols]
        db.execute(f"INSERT INTO calls ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", vals)

    def log_call(self, call):
        with self.tx() as db:
            self.insert_call(db, call)
            db.execute("UPDATE responses SET consumed=1 WHERE request_id=?", (call["request_id"],))

    # --- write-ahead journal ------------------------------------------------------------------------
    def mark_inflight(self, rec):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO inflight VALUES (?,?,?,?,?,?)",
                       (rec["request_id"], rec["role"], json.dumps(rec["review_ids"]), rec["invocation"],
                        rec["started_at"], json.dumps(rec, default=str)))

    def journal_response(self, rec, raw):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO responses VALUES (?,?,?,?,?,0)",
                       (rec["request_id"], rec["role"], raw, json.dumps(rec, default=str), time.time()))
            db.execute("DELETE FROM inflight WHERE request_id=?", (rec["request_id"],))

    def mark_consumed(self, request_id, db=None):
        if db is not None:
            db.execute("UPDATE responses SET consumed=1 WHERE request_id=?", (request_id,))
            return
        with self.tx() as d:
            d.execute("UPDATE responses SET consumed=1 WHERE request_id=?", (request_id,))

    def clear_inflight(self, request_id):
        with self.tx() as db:
            db.execute("DELETE FROM inflight WHERE request_id=?", (request_id,))

    def spent_usd(self):
        row = self.one("SELECT COALESCE(SUM(cost_usd),0) AS s FROM calls")
        return float(row["s"])

    def start_invocation(self, inv, command, phase):
        with self.tx() as db:
            db.execute("INSERT INTO invocations (invocation, command, phase, started_at) VALUES (?,?,?,?)",
                       (inv, command, phase, time.time()))

    def end_invocation(self, inv, notes=None):
        with self.tx() as db:
            db.execute("UPDATE invocations SET ended_at=?, wall_s=? - started_at, notes=? WHERE invocation=?",
                       (time.time(), time.time(), json.dumps(notes or {}), inv))
