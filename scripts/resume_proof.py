"""Show that a resumed run made no enrichment calls for reviews completed before the interruption (offline)."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
run_id = sys.argv[1] if len(sys.argv) > 1 else "final-100k"
before = set(json.loads((ROOT / "runs" / run_id / "checkpoint_before.json").read_text())["completed_ids"])
db = sqlite3.connect(ROOT / "state" / f"{run_id}.sqlite")
sent = [v for (v,) in db.execute("SELECT value FROM calls, json_each(calls.review_ids) "
                                 "WHERE role='enrich' AND phase='resume'")]
completed = db.execute("SELECT COUNT(*) FROM results WHERE status='completed'").fetchone()[0]
print("checkpoint_before completed reviews :", f"{len(before):,}")
print("review IDs sent in resume calls     :", f"{len(sent):,}")
print("overlap with checkpoint_before      :", len(set(sent) & before))
print("completed now                       :", f"{completed:,}")
