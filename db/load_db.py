"""Load a finished run's saved artifacts into Postgres (DATABASE_URL in .env). No model calls.

    python db/load_db.py --run-id final-100k --final
"""
import argparse
import csv
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def jl(path):
    p = Path(path)
    if not p.exists() and Path(str(p) + ".gz").exists():
        p = Path(str(p) + ".gz")
    op = gzip.open if p.suffix == ".gz" else open
    with op(p, "rt", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--final", action="store_true", help="mark as the run the dashboard shows by default")
    a = ap.parse_args()
    import psycopg
    from pipeline.cli import open_store
    rd = ROOT / "runs" / a.run_id
    store = open_store(a.run_id)
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL not set in .env")
    J = psycopg.types.json.Jsonb
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute((ROOT / "db" / "schema.sql").read_text())
        for t in ("records", "issues", "membership", "ranking", "aggregates", "memo", "claims", "evidence", "runs"):
            cur.execute(f"DELETE FROM {t} WHERE run_id=%s", (a.run_id,))
        if a.final:
            cur.execute("UPDATE runs SET is_final=FALSE")
        agg = json.loads((rd / "aggregates.json").read_text())
        cur.execute("INSERT INTO runs (run_id, is_final, manifest, summary, scope) VALUES (%s,%s,%s,%s,%s)",
                    (a.run_id, a.final, J(json.loads((rd / "manifest.json").read_text())),
                     J(json.loads((rd / "run_summary.json").read_text())), J(agg["scope"])))
        rows = store.q("""SELECT rv.review_id, rv.source_sha256, rv.review_text, rv.review_rating, rv.review_likes,
                                 rv.app_version, rv.review_timestamp, r.* FROM reviews rv JOIN results r USING(review_id)
                          WHERE rv.synthetic=0""")
        with cur.copy("""COPY records (run_id, review_id, status, reason, topic, subtopic, intent, sentiment, severity,
                         entities, evidence_quote, needs_review, label_config, cache_source_id, review_text, review_rating,
                         review_likes, app_version, review_timestamp, source_sha256) FROM STDIN""") as cp:
            for r in rows:
                cp.write_row((a.run_id, r["review_id"], r["status"], r["reason"], r["topic"], r["subtopic"], r["intent"],
                              r["sentiment"], r["severity"], r["entities"] or "[]", r["evidence_quote"],
                              None if r["needs_review"] is None else bool(r["needs_review"]), r["label_config"],
                              r["cache_source_id"], r["review_text"], int(r["review_rating"]) if r["review_rating"] else None,
                              int(r["review_likes"] or 0), r["app_version"], r["review_timestamp"], r["source_sha256"]))
        for i in json.loads((rd / "issues.json").read_text()):
            cur.execute("INSERT INTO issues VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (a.run_id, i["issue_id"], i["topic"], i["subtopic"], i["title"], i["description"], i["member_count"],
                         i["coherent"], J(i["misfit_review_ids"]), J(i["evidence_pack"])))
        with cur.copy("COPY membership (run_id, issue_id, review_id) FROM STDIN") as cp:
            for m in csv.DictReader((rd / "membership.csv").open()):
                cp.write_row((a.run_id, m["issue_id"], m["review_id"]))
        for r in csv.DictReader((rd / "ranking.csv").open()):
            cur.execute("INSERT INTO ranking VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (a.run_id, int(r["rank"]), r["issue_id"], int(r["complaint_count"]), int(r["severity_sum"]),
                         r["mean_severity"], int(r["priority_score"])))
        cur.execute("INSERT INTO aggregates VALUES (%s,%s)", (a.run_id, J(agg)))
        mj = json.loads((rd / "memo.json").read_text())
        m = mj["memo"]
        cur.execute("INSERT INTO memo VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (a.run_id, m["recommended_area"], m["headline"], m["memo_markdown"], J(m["recommendations"]),
                     J(m["alternatives"]), J(m["limitations"]), J(m["cited_review_ids"]), mj["label_config"], mj.get("request_id")))
        for c in csv.DictReader((rd / "claims.csv").open()):
            cur.execute("INSERT INTO claims VALUES (%s,%s,%s,%s,%s)", (a.run_id, c["claim_id"], c["issue_id"], c["metric"], c["value"]))
        ev = {"verifier": rd / "verify" / "verifier_report.json", "planted_error": rd / "verify" / "planted_error_test.json",
              "golden": ROOT / "evals" / "golden" / "results.json", "injection": ROOT / "evals" / "injection" / "injection_results.json",
              "spotcheck": ROOT / "evals" / "spotcheck" / "dev-500" / "analysis.json",
              "insights": rd / "insights.json", "trend": rd / "trend.json",
              "memo_v1": rd / "memo_v1_backup.json"}
        for kind, p in ev.items():
            if p.exists():
                payload = json.loads(p.read_text())
                if kind == "injection":
                    payload = {k: payload[k] for k in ("cases", "passed", "requests", "max_reviews_per_request")} | {
                        "results": [{k: r[k] for k in ("review_id", "pass", "failures")} for r in payload["results"]]}
                cur.execute("INSERT INTO evidence VALUES (%s,%s,%s)", (a.run_id, kind, J(payload)))
        calls = jl(rd / "calls.jsonl")
        usage = {}
        for c in calls:
            u = usage.setdefault(f"{c['role']}|{c['model']}", {"calls": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
                                                              "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
            u["calls"] += 1; u["failed"] += c["outcome"] == "failed"
            u["input_tokens"] += c["input_tokens"]; u["output_tokens"] += c["output_tokens"]
            u["cache_read"] += c["cache_read_input_tokens"]; u["cache_write"] += c["cache_creation_input_tokens"]
            u["cost_usd"] += c["cost_usd"]
        cur.execute("INSERT INTO evidence VALUES (%s,%s,%s)", (a.run_id, "usage", J(usage)))
        conn.commit()
        cur.execute("SELECT COUNT(*) FROM records WHERE run_id=%s", (a.run_id,))
        print(f"loaded run {a.run_id}: {cur.fetchone()[0]} records, final={a.final}")


if __name__ == "__main__":
    main()
