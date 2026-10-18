"""Write run artifacts (runs/<id>/) and the standardized grading/ export (GRADING_CONTRACT.md)."""
import csv
import gzip
import json
import shutil
from pathlib import Path

VERSION = "a5-audit-v1"


def record_rows(store, include_synthetic=False):
    rows = store.q(f"""SELECT rv.review_id, rv.source_sha256, r.* FROM reviews rv JOIN results r USING(review_id)
                       {'' if include_synthetic else 'WHERE rv.synthetic=0'} ORDER BY rv.row_idx""")
    for r in rows:
        if r["status"] == "completed":
            rec = {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": "completed",
                   "topic": r["topic"], "intent": r["intent"], "sentiment": r["sentiment"], "severity": r["severity"],
                   "entities": json.loads(r["entities"] or "[]"), "evidence_quote": r["evidence_quote"],
                   "needs_review": bool(r["needs_review"]), "label_config": r["label_config"],
                   "subtopic": r["subtopic"]}
            if r["cache_source_id"]:
                rec["cache_source_id"] = r["cache_source_id"]
        else:
            rec = {"review_id": r["review_id"], "source_sha256": r["source_sha256"], "status": r["status"],
                   "reason": r["reason"] or ("not_yet_processed" if r["status"] == "pending" else "unknown"),
                   "attempts": r["attempts"]}
        yield rec


def call_rows(store, roles=None):
    for c in store.q("SELECT * FROM calls ORDER BY started_at"):
        if roles and c["role"] not in roles:
            continue
        yield {"request_id": c["request_id"], "role": c["role"], "review_ids": json.loads(c["review_ids"] or "[]"),
               "model": c["model"], "phase": c["phase"], "outcome": c["outcome"], "label_config": c["label_config"],
               "input_tokens": int(c["input_tokens"] or 0), "output_tokens": int(c["output_tokens"] or 0),
               "cache_creation_input_tokens": int(c["cache_creation_input_tokens"] or 0),
               "cache_read_input_tokens": int(c["cache_read_input_tokens"] or 0),
               "cost_usd": round(c["cost_usd"] or 0, 8), "latency_ms": c["latency_ms"], "attempt": c["attempt"],
               "stop_reason": c["stop_reason"], "error": c["error"], "invocation": c["invocation"],
               "provider_request_id": c["provider_request_id"], "started_at": c["started_at"],
               "settings": json.loads(c["settings"] or "{}")}


def write_jsonl(path, rows, compress=False):
    path = Path(str(path) + ".gz") if compress else Path(path)
    opener = (lambda p: gzip.open(p, "wt", encoding="utf-8")) if compress else (lambda p: p.open("w", encoding="utf-8"))
    n = 0
    with opener(path) as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return path, n


def write_claims(memo, path):
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["claim_id", "issue_id", "metric", "value"])
        for c in memo["claims"]:
            w.writerow([c["claim_id"], c["issue_id"], c["metric"], c["value"]])


def grading_export(store, run_dir, out_dir, *, analysis_count, analysis_sha256, ingestion_json, compress=True):
    run_dir, out_dir = Path(run_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("*"):
        if stale.is_file() and stale.name != "README.md":
            stale.unlink()
    (out_dir / "run.json").write_text(json.dumps({
        "version": VERSION, "analysis_count": analysis_count, "analysis_sha256": analysis_sha256,
        "classification_input_fields": ["review_text"], "allow_multi_issue": False,
        "run_id": run_dir.name, "scope_note": "See README: declared >=100k analysis subset; full file profiled."},
        indent=2) + "\n")
    shutil.copy(ingestion_json, out_dir / "ingestion.json")
    write_jsonl(out_dir / "records.jsonl", record_rows(store), compress)
    write_jsonl(out_dir / "calls.jsonl", call_rows(store), compress)
    for name in ("membership.csv", "ranking.csv", "claims.csv", "checkpoint_before.json", "checkpoint_after.json"):
        src = run_dir / name
        if src.exists():
            shutil.copy(src, out_dir / name)
    return sorted(p.name for p in out_dir.iterdir())
