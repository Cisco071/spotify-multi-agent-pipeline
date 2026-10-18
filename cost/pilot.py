"""EXPLICIT PAID PILOT. Runs the real pipeline on cost_100.csv: cold (empty result cache, 1 worker), then warm.

    python -m cost.pilot --paid            # makes real API calls (needs ANTHROPIC_API_KEY)
    python -m cost.pilot --collect-only    # no calls: re-extract evidence files from saved state

Without --paid nothing is executed. Offline replay is `python -m cost.calculator`.
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline import config  # noqa: E402
from pipeline.hashing import file_sha  # noqa: E402

COST = ROOT / "cost"
INPUT = config.DATASET / "cost_100.csv"
EXPECTED_SHA = "c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e"
RUN_ID = "pilot-100"
ITEMS = [("input", "input_tokens"), ("cache_write_5m", "cache_creation_input_tokens"),
         ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens")]


def run_pipeline(label, extra):
    cmd = [sys.executable, "-m", "pipeline", "run", "--input", str(INPUT), "--run-id", RUN_ID, "--workers", "1",
           "--verify-fraction", "0.10", "--verify-min", "10", "--spend-cap", "2.00", *extra]
    print(f"== {label}: {' '.join(cmd[1:])}", flush=True)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT)
    wall = time.time() - t0
    if proc.returncode:
        print(f"{label} run exited with {proc.returncode}", flush=True)
    return {"label": label, "wall_clock_seconds": round(wall, 3), "exit_code": proc.returncode,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0))}


def collect(runs_meta=None):
    sys.path.insert(0, str(ROOT))
    from pipeline.cli import open_store
    from pipeline import export
    store = open_store(RUN_ID)
    rd = config.RUNS / RUN_ID
    invs = [dict(r) for r in store.q("SELECT * FROM invocations ORDER BY started_at")]
    meta_path = COST / "pilot_runs.json"
    meta = runs_meta or (json.loads(meta_path.read_text()) if meta_path.exists() else {"runs": []})
    labels = {}
    for i, inv in enumerate(invs):
        labels[inv["invocation"]] = "cold" if i == 0 else ("warm" if i == 1 else f"extra_{i}")
    summaries = [json.loads(l) for l in (rd / "run_summaries.jsonl").open()] if (rd / "run_summaries.jsonl").exists() else []
    for s in summaries:
        lab = labels.get(s["invocation"])
        for r in meta["runs"]:
            if r["label"] == lab:
                r.update({"invocation": s["invocation"], "stage_seconds": {k: v["seconds"] for k, v in s["stages"].items()},
                          "pipeline_wall_seconds": s["wall_seconds"], "status_counts": s["status_counts"],
                          "workers": s["workers"], "spend_cap_usd": s["spend_cap_usd"]})
                en = s["stages"].get("enrich", {}).get("result", {})
                r["enrich_cache_hits"] = en.get("cache_hits")  # recomputed from records below
                r["enrich_calls_ok"] = en.get("calls_ok")
                r["enrich_calls_failed"] = en.get("calls_failed")
    ids = [r["review_id"] for r in store.q("SELECT review_id FROM reviews ORDER BY row_idx")]
    meta.update({"input": INPUT.name, "input_sha256": file_sha(INPUT), "expected_sha256": EXPECTED_SHA,
                 "checksum_ok": file_sha(INPUT) == EXPECTED_SHA, "review_ids": ids, "records": len(ids),
                 "unique_texts": store.one("SELECT COUNT(DISTINCT text_sha) n FROM reviews WHERE is_empty=0")["n"],
                 "declared_verification_sample": "10% (10 reviews), lowest sha256('verify-v1:'+review_id)",
                 "roles": {k: {**v, "label_config": config.label_config(k)} for k, v in config.ROLES.items()},
                 "batch_size": config.BATCH_SIZE, "verify_batch_size": config.VERIFY_BATCH_SIZE})
    reuse = store.one("SELECT COUNT(*) n FROM results WHERE cache_source_id IS NOT NULL")["n"]
    for r in meta["runs"]:
        # cache hits per run = records completed by exact-text reuse in that invocation (warm: completed work reused)
        if r["label"] == "cold":
            r["enrich_cache_hits"] = reuse
        elif r["label"] == "warm":
            r["enrich_cache_hits"] = r.get("status_counts", {}).get("completed", 0)
            r["cache_hit_note"] = "all completed records reused from saved results; 0 enrichment calls"
    meta_path.write_text(json.dumps(meta, indent=2))
    export.write_jsonl(COST / "pilot_records.jsonl", export.record_rows(store))
    calls = []
    for c in export.call_rows(store):
        c["pilot_run"] = labels.get(c["invocation"], "unknown")
        c["run_id"] = RUN_ID
        calls.append(c)
    export.write_jsonl(COST / "pilot_calls.jsonl", calls)
    with (COST / "usage.csv").open("w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["request_id", "pilot_run", "role", "model", "outcome", "billing_item", "billed_units", "unit",
                    "usage_source"])
        for c in calls:
            for item, field in ITEMS:
                w.writerow([c["request_id"], c["pilot_run"], c["role"], c["model"], c["outcome"], item, c[field],
                            "token", "provider response.usage" if c["outcome"] == "succeeded" or c[field] else
                            "no usage returned (failed before response)"])
    print(f"collected: {len(ids)} records, {len(calls)} calls -> cost/pilot_records.jsonl, pilot_calls.jsonl, usage.csv, pilot_runs.json")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--paid", action="store_true", help="actually run the paid cold+warm pilot")
    p.add_argument("--collect-only", action="store_true")
    a = p.parse_args()
    if a.collect_only:
        return collect()
    if not a.paid:
        print(__doc__)
        return
    if file_sha(INPUT) != EXPECTED_SHA:
        raise SystemExit("cost_100.csv checksum mismatch")
    state = config.STATE / f"{RUN_ID}.sqlite"
    if state.exists():
        raise SystemExit(f"{state} exists: the cold run needs an empty result cache. Move it aside first.")
    runs = [run_pipeline("cold", []), run_pipeline("warm", [])]
    collect({"runs": runs})
    subprocess.run([sys.executable, "-m", "cost.calculator"], cwd=ROOT)


if __name__ == "__main__":
    main()
