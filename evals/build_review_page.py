"""Build a human spot-check page for a run's enricher output (development data only; never the golden set).

    python evals/build_review_page.py --run-id dev-500 [--max 60]
    python evals/review_server.py   ->  http://localhost:8765/spotcheck/dev-500/review.html

Stratified sample (declared, deterministic): every verifier disagreement, every severity 4-5, every cancellation,
every needs_review, then random fill by sha256('spotcheck:'+review_id); capped at --max (earlier strata first).
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.labels import INTENTS, SUBTOPICS, TOPICS  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--max", type=int, default=60)
    p.add_argument("--random", type=int, default=15)
    a = p.parse_args()
    from pipeline.cli import open_store
    store = open_store(a.run_id)
    rd = ROOT / "runs" / a.run_id
    golden = set()
    gpath = ROOT / "Final Assignment - Spotify Reviews Dataset" / "golden_50_to_label.csv"
    if gpath.exists():
        import csv
        golden = {r["review_id"] for r in csv.DictReader(gpath.open(encoding="utf-8"))}
    recs = [dict(r) for r in store.q("""SELECT r.*, rv.review_text, rv.review_rating FROM results r JOIN reviews rv USING(review_id)
                                        WHERE r.status='completed' AND r.cache_source_id IS NULL AND rv.synthetic=0
                                        ORDER BY rv.row_idx""") if r["review_id"] not in golden]
    by_id = {r["review_id"]: r for r in recs}
    dis = {}
    dpath = rd / "verify" / "disagreements.jsonl"
    if dpath.exists():
        for line in dpath.open(encoding="utf-8"):
            d = json.loads(line)
            dis[d["review_id"]] = {k: d["verifier"][k] for k in ("topic", "intent", "severity")}
    strata = [("verifier_disagreement", [r for r in recs if r["review_id"] in dis]),
              ("severity_4_5", [r for r in recs if r["severity"] >= 4]),
              ("cancellation", [r for r in recs if r["intent"] == "cancellation"]),
              ("needs_review", [r for r in recs if r["needs_review"]])]
    chosen, why = [], {}
    for name, rows in strata:
        for r in rows:
            why.setdefault(r["review_id"], []).append(name)
            if r["review_id"] not in chosen and len(chosen) < a.max - a.random:
                chosen.append(r["review_id"])
    for r in sorted(recs, key=lambda r: hashlib.sha256(f"spotcheck:{r['review_id']}".encode()).hexdigest()):
        if len(chosen) >= a.max:
            break
        if r["review_id"] not in chosen:
            chosen.append(r["review_id"])
            why.setdefault(r["review_id"], []).append("random")
    items = [{"review_id": rid, "text": by_id[rid]["review_text"], "stars": by_id[rid]["review_rating"],
              "why": why[rid], "pred": {k: by_id[rid][k] for k in ("topic", "subtopic", "intent", "severity", "sentiment",
                                                                   "evidence_quote", "review_reason")},
              "entities": json.loads(by_id[rid]["entities"] or "[]"), "needs_review": bool(by_id[rid]["needs_review"])}
             for rid in chosen]
    out = ROOT / "evals" / "spotcheck" / a.run_id
    out.mkdir(parents=True, exist_ok=True)
    meta = {"run_id": a.run_id, "label_config": recs[0]["label_config"] if recs else None, "sample_rule": __doc__.split("\n\n")[2].strip(),
            "strata_sizes": {n: len(rows) for n, rows in strata}, "records_in_run": len(recs), "sampled": len(items)}
    (out / "sample.json").write_text(json.dumps({"meta": meta, "items": items}, indent=2, ensure_ascii=False))
    html = (ROOT / "evals" / "review_template.html").read_text(encoding="utf-8")
    html = html.replace("__DATA__", json.dumps({"meta": meta, "items": items, "topics": TOPICS, "intents": INTENTS,
                                                 "subtopics": SUBTOPICS}, ensure_ascii=False))
    (out / "review.html").write_text(html, encoding="utf-8")
    print(json.dumps(meta, indent=2))
    print(f"open http://localhost:8765/spotcheck/{a.run_id}/review.html (run: python evals/review_server.py)")


if __name__ == "__main__":
    main()
