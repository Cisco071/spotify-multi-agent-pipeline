"""Persistent cross-run result cache (state/result_cache.sqlite).

Key: (exact text sha256, label_config). Value: the validated model result, the review_id it was produced for,
and the successful call record that produced it (so reuse in a later run keeps model-call evidence).
Reuse is only allowed when the producing review_id is part of the new run; other identical texts in the run then
point at it via cache_source_id (direct originals only, never chains).
"""
import json
import os
import sqlite3
import threading

from . import config

_lock = threading.Lock()
_conn = None


def conn():
    global _conn
    if _conn is None:
        config.STATE.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.STATE / "result_cache.sqlite", check_same_thread=False, isolation_level=None, timeout=60)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("""CREATE TABLE IF NOT EXISTS cache (text_sha TEXT, label_config TEXT, review_id TEXT, result TEXT,
                         call TEXT, run_id TEXT, created_at REAL, PRIMARY KEY (text_sha, label_config))""")
    return _conn


def put(text_sha, label_config, review_id, result, call, run_id):
    if label_config.startswith("FAKE") and os.environ.get("PIPELINE_CACHE_ALLOW_FAKE") != "1":
        return
    with _lock:
        conn().execute("INSERT OR IGNORE INTO cache VALUES (?,?,?,?,?,?,strftime('%s','now'))",
                       (text_sha, label_config, review_id, json.dumps(result, ensure_ascii=False),
                        json.dumps(call, ensure_ascii=False, default=str), run_id))


def lookup_many(text_shas, label_configs):
    out = {}
    shas = list(text_shas)
    with _lock:
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            q = (f"SELECT * FROM cache WHERE text_sha IN ({','.join('?' * len(chunk))}) "
                 f"AND label_config IN ({','.join('?' * len(label_configs))})")
            for row in conn().execute(q, [*chunk, *label_configs]):
                out[row[0]] = {"text_sha": row[0], "label_config": row[1], "review_id": row[2],
                               "result": json.loads(row[3]), "call": json.loads(row[4]), "run_id": row[5]}
    return out


def stats():
    with _lock:
        return dict(conn().execute("SELECT label_config, COUNT(*) FROM cache GROUP BY label_config").fetchall())
