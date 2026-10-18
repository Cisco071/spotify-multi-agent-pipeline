"""Durability tests with the offline fake model (no API key, no cost): hard crash, outage, graceful stop/resume."""
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "Final Assignment - Spotify Reviews Dataset"


def run(run_id, *args, env=None):
    e = {**os.environ, "PIPELINE_FAKE_LLM": "1", **(env or {})}
    return subprocess.run([sys.executable, "-m", "pipeline", "run", "--run-id", run_id, "--stages", "ingest,enrich",
                           "--no-global-cache", *args], cwd=ROOT, env=e, capture_output=True, text=True)


def q(run_id, sql):
    with sqlite3.connect(ROOT / "state" / f"{run_id}.sqlite") as c:
        return c.execute(sql).fetchall()


def clean(run_id):
    for p in (ROOT / "state").glob(f"{run_id}.sqlite*"):
        p.unlink()
    shutil.rmtree(ROOT / "runs" / run_id, ignore_errors=True)
    (ROOT / "state" / "backups" / f"{run_id}.sqlite").unlink(missing_ok=True)


def test_hard_crash_after_paid_response_is_recovered_without_new_call():
    rid = "t-crash"
    clean(rid)
    r1 = run(rid, "--input", str(DATA / "checkpoint_500.csv"), env={"PIPELINE_CRASH_AFTER_JOURNAL": "3"})
    assert r1.returncode == 9
    assert q(rid, "SELECT COUNT(*) FROM responses WHERE consumed=0")[0][0] == 1
    r2 = run(rid)
    assert "crash recovery: 1 saved responses committed without new calls" in r2.stdout
    assert q(rid, "SELECT COUNT(*) FROM results WHERE status='completed'")[0][0] == 500
    dup = q(rid, """SELECT COUNT(*) FROM (SELECT value FROM calls, json_each(calls.review_ids)
                    WHERE outcome='succeeded' AND role='enrich' GROUP BY value HAVING COUNT(*) > 1)""")[0][0]
    assert dup == 0
    clean(rid)


def test_outage_pauses_and_never_quarantines():
    rid = "t-outage"
    clean(rid)
    r = run(rid, "--input", str(DATA / "cost_100.csv"), env={"PIPELINE_FAKE_OUTAGE_SECONDS": "35"})
    assert "OUTAGE detected" in r.stdout
    assert q(rid, "SELECT COUNT(*) FROM results WHERE status='completed'")[0][0] == 100
    assert q(rid, "SELECT COUNT(*) FROM results WHERE status='quarantined'")[0][0] == 0
    clean(rid)


def test_graceful_stop_then_resume_has_no_repeat_calls():
    rid = "t-resume"
    clean(rid)
    run(rid, "--input", str(DATA / "checkpoint_500.csv"), "--stop-after", "2")
    import json
    before = set(json.loads((ROOT / "runs" / rid / "checkpoint_before.json").read_text())["completed_ids"])
    assert 0 < len(before) < 500
    run(rid)
    after = set(json.loads((ROOT / "runs" / rid / "checkpoint_after.json").read_text())["completed_ids"])
    assert before < after and len(after) == 500
    resumed = {v for (v,) in q(rid, """SELECT value FROM calls, json_each(calls.review_ids)
                                       WHERE role='enrich' AND phase='resume'""")}
    assert not (resumed & before)
    clean(rid)
