"""Orchestrator CLI. Code decides the next step; models only do bounded language tasks.

  python -m pipeline run --input data/analysis_100k.csv --run-id final-100k        # paid: all stages
  python -m pipeline run --input X.csv --run-id R --stages ingest,enrich --limit 500  # paid: partial
  python -m pipeline rank --run-id R                                                # offline, no key
  python -m pipeline export --run-id R --out grading                                # offline, no key
  python -m pipeline status --run-id R                                              # offline, no key
  python -m pipeline profile --input spotify_reviews_18months.csv                   # offline full profile
"""
import argparse
import hashlib
import json
import platform
import signal
import subprocess
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from . import config
from .hashing import file_sha

STAGES = ["ingest", "enrich", "verify", "group", "rank", "insights", "memo", "export"]


def run_dir(run_id):
    d = config.RUNS / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def open_store(run_id):
    from .store import Store
    return Store(config.STATE / f"{run_id}.sqlite")


class RunLog:
    def __init__(self, path, invocation):
        self.path, self.inv = Path(path), invocation
        self.lock = threading.Lock()

    def __call__(self, msg, quiet=False, **fields):
        ev = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "invocation": self.inv, "msg": msg, **fields}
        if not quiet:
            print(f"{ev['ts']} {msg}" + (f" {json.dumps(fields, default=str)}" if fields else ""), flush=True)
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False, default=str) + "\n")


def git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=config.ROOT, stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return None


def write_manifest(rd, input_csv, store):
    prompts = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(config.PROMPTS.glob("*.md"))}
    m = {"run_id": rd.name, "input": str(Path(input_csv).name), "input_sha256": file_sha(input_csv),
         "full_source_sha256": config.FULL_SHA256, "code_commit": git_commit(), "python": platform.python_version(),
         "prompts_sha256": prompts, "schema_version": config.SCHEMA_VERSION, "taxonomy_version": config.TAXONOMY_VERSION,
         "roles": {k: {**v, "label_config": config.label_config(k)} for k, v in config.ROLES.items()},
         "limits": {"batch_size": config.BATCH_SIZE, "verify_batch_size": config.VERIFY_BATCH_SIZE,
                    "fallback_fraction_cap": config.FALLBACK_FRACTION_CAP, "invalid_output_retries": config.INVALID_OUTPUT_RETRIES,
                    "transient_retries": config.TRANSIENT_RETRIES},
         "prices_usd_per_mtok": config.PRICES, "price_source": config.PRICE_SOURCE, "price_checked": config.PRICE_CHECKED}
    (rd / "manifest.json").write_text(json.dumps(m, indent=2))
    store.put("input_csv", str(input_csv))
    return m


def status_counts(store):
    rows = store.q("""SELECT r.status, COUNT(*) n FROM results r JOIN reviews rv USING(review_id)
                      WHERE rv.synthetic=0 GROUP BY r.status""")
    return {r["status"]: r["n"] for r in rows}


def usage_by_role(store, invocation=None):
    where = "WHERE invocation=?" if invocation else ""
    rows = store.q(f"""SELECT role, model, outcome, COUNT(*) calls, SUM(input_tokens) i, SUM(output_tokens) o,
                       SUM(cache_creation_input_tokens) cw, SUM(cache_read_input_tokens) cr, SUM(cost_usd) usd,
                       SUM(latency_ms) lat FROM calls {where} GROUP BY role, model, outcome""", (invocation,) if invocation else ())
    return [dict(r) for r in rows]


def checkpoint(store, path):
    ids = [r["review_id"] for r in store.q("""SELECT r.review_id FROM results r JOIN reviews rv USING(review_id)
                                              WHERE r.status='completed' AND rv.synthetic=0 ORDER BY rv.row_idx""")]
    Path(path).write_text(json.dumps({"completed_ids": ids, "count": len(ids),
                                      "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S")}) + "\n")
    return len(ids)


def cmd_run(a):
    from .ledger import Ledger
    from .stages import enrich as enrich_stage
    from .stages import group as group_stage
    from .stages import ingest as ingest_stage
    from .stages import memo as memo_stage
    from .stages import rank as rank_stage
    from .stages import verify as verify_stage
    from . import export

    rd = run_dir(a.run_id)
    store = open_store(a.run_id)
    inv = f"inv-{time.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
    log = RunLog(rd / "run_log.jsonl", inv)
    stages = [s for s in STAGES if s in a.stages.split(",")] if a.stages else STAGES
    input_csv = Path(a.input) if a.input else Path(store.get("input_csv"))
    prior_enrich = store.one("SELECT COUNT(*) n FROM calls WHERE role='enrich'")["n"]
    phase = "resume" if prior_enrich else "initial"
    store.start_invocation(inv, " ".join(["run", f"--run-id={a.run_id}", f"--stages={','.join(stages)}",
                                          f"--limit={a.limit}", f"--workers={a.workers}"]), phase)
    ledger = Ledger(a.spend_cap, already_spent=store.spent_usd())
    stop = threading.Event()

    def on_sigint(sig, frame):
        if stop.is_set():
            raise KeyboardInterrupt
        log("SIGINT: finishing in-flight batches, saving state, then stopping (Ctrl-C again to abort)")
        stop.set()
    signal.signal(signal.SIGINT, on_sigint)
    signal.signal(signal.SIGTERM, on_sigint)  # `kill <pid>` / scripts/stop_run.sh -> same graceful stop
    (rd / "pid").write_text(str(__import__("os").getpid()))

    def periodic_saves():
        """Every BACKUP_EVERY_S: consistent DB snapshot + small checkpoint file (counts + digest of completed IDs).
        Results are ALSO committed after every batch; this protects against disk/DB corruption and gives a timeline."""
        snap_dir = rd / "checkpoints"
        snap_dir.mkdir(exist_ok=True)
        last_t, last_n = time.time(), status_counts(store).get("completed", 0)
        while not done_evt.wait(15):
            n_now = status_counts(store).get("completed", 0)
            if time.time() - last_t < config.BACKUP_EVERY_S and n_now - last_n < config.BACKUP_EVERY_N:
                continue
            last_t, last_n = time.time(), n_now
            try:
                store.backup(config.STATE / "backups" / f"{a.run_id}.sqlite")
                ids = [r["review_id"] for r in store.q("SELECT review_id FROM results WHERE status='completed' ORDER BY review_id")]
                snap = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "invocation": inv, "status": status_counts(store),
                        "completed_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
                        "spent_usd": round(store.spent_usd(), 6), "db_backup": f"state/backups/{a.run_id}.sqlite"}
                (snap_dir / f"snapshot_{time.strftime('%Y%m%dT%H%M%S')}.json").write_text(json.dumps(snap, indent=2))
                log("periodic checkpoint saved", **{k: snap[k] for k in ("status", "spent_usd")})
            except Exception as e:  # never let backup problems stop the run
                log(f"periodic checkpoint failed: {e}")
    done_evt = threading.Event()
    threading.Thread(target=periodic_saves, daemon=True, name="checkpointer").start()

    summary = {"run_id": a.run_id, "invocation": inv, "phase": phase, "stages": {}, "spend_cap_usd": a.spend_cap,
               "workers": a.workers, "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    t_run = time.time()
    log("run start", run_id=a.run_id, stages=stages, phase=phase, input=str(input_csv), spent_before=ledger.spent)
    ranking = issues = None
    try:
        for st in stages:
            if stop.is_set() and st != "export":
                log(f"skip {st}: stop requested")
                continue
            t0 = time.time()
            if st == "ingest":
                write_manifest(rd, input_csv, store)
                info = ingest_stage.load_run(store, input_csv)
                (rd / "ingest.json").write_text(json.dumps(info, indent=2))
                res = info
            elif st == "enrich":
                before = status_counts(store)
                en = enrich_stage.Enricher(store, ledger, invocation=inv, phase=phase, workers=a.workers,
                                           limit=a.limit, stop_event=stop, log=log, run_id=a.run_id,
                                           progress_path=rd / "progress.json", event_log=log,
                                           use_global_cache=not a.no_global_cache, stop_after_calls=a.stop_after)
                res = en.run()
                res["status_before"], res["status_after"] = before, status_counts(store)
                if stop.is_set():
                    # The FIRST interruption is the contract's checkpoint_before; later stops never overwrite it.
                    target = rd / "checkpoint_before.json"
                    if target.exists():
                        (rd / "checkpoints").mkdir(exist_ok=True)
                        target = rd / "checkpoints" / f"stop_{time.strftime('%Y%m%dT%H%M%S')}.json"
                    n = checkpoint(store, target)
                    log(f"interrupted: {target.relative_to(rd)} saved", completed=n)
                elif phase == "resume" and (rd / "checkpoint_before.json").exists():
                    n = checkpoint(store, rd / "checkpoint_after.json")
                    log("resumed: checkpoint_after.json saved", completed=n)
            elif st == "verify":
                rep, planted = verify_stage.run_verify(store, ledger, invocation=inv, out_dir=rd / "verify",
                                                       fraction=a.verify_fraction, log=log, minimum=a.verify_min)
                res = {"summary": rep["summary"], "planted_error_pass": planted["pass"]}
            elif st == "group":
                members, issues = group_stage.run_group(store, ledger, invocation=inv, out_dir=rd, log=log)
                res = {"issues": len(members), "memberships": sum(map(len, members.values()))}
            elif st == "rank":
                from .stages.rank import compute_ranking, read_membership, write_ranking
                recs = {r["review_id"]: r for r in export.record_rows(store)}
                ranking = compute_ranking(recs, read_membership(rd / "membership.csv"))
                write_ranking(ranking, rd / "ranking.csv")
                res = {"issues_ranked": len(ranking), "top5": [r["issue_id"] for r in ranking[:5]]}
            elif st == "insights":
                from .stages import insights as insights_stage
                ins = insights_stage.build(store)
                (rd / "insights.json").write_text(json.dumps(ins, indent=2, ensure_ascii=False))
                res = {"anomalies": len(ins["anomalies"]), "recurring": len(ins["recurring"])}
            elif st == "memo":
                from .stages.rank import compute_ranking, read_membership
                if ranking is None:
                    recs = {r["review_id"]: r for r in export.record_rows(store)}
                    ranking = compute_ranking(recs, read_membership(rd / "membership.csv"))
                if issues is None:
                    issues = json.loads((rd / "issues.json").read_text())
                memo, agg = memo_stage.run_memo(store, ledger, invocation=inv, out_dir=rd, ranking=ranking,
                                                issues=issues, log=log)
                (rd / "aggregates.json").write_text(json.dumps(agg, indent=2))
                export.write_claims(memo, rd / "claims.csv")
                render_memo(rd, memo, agg, ranking)
                res = {"recommended_area": memo["recommended_area"], "claims": len(memo["claims"])}
            elif st == "export":
                write_run_files(store, rd)
                res = {"files": sorted(p.name for p in rd.iterdir())}
            summary["stages"][st] = {"seconds": round(time.time() - t0, 2), "result": res}
            log(f"stage {st} done", seconds=round(time.time() - t0, 2))
    finally:
        done_evt.set()
        try:
            store.backup(config.STATE / "backups" / f"{a.run_id}.sqlite")
        except Exception as e:
            log(f"final backup failed: {e}")
        summary["wall_seconds"] = round(time.time() - t_run, 2)
        summary["status_counts"] = status_counts(store)
        summary["usage_this_invocation"] = usage_by_role(store, inv)
        summary["usage_all_invocations"] = usage_by_role(store)
        summary["ledger"] = ledger.snapshot()
        summary["stopped"] = stop.is_set()
        store.end_invocation(inv, {"wall_seconds": summary["wall_seconds"], "stopped": stop.is_set()})
        hist = rd / "run_summaries.jsonl"
        with hist.open("a") as f:
            f.write(json.dumps(summary, default=str) + "\n")
        (rd / "run_summary.json").write_text(json.dumps(summary, indent=2, default=str))
        log("run end", wall_seconds=summary["wall_seconds"], status=summary["status_counts"],
            spent=summary["ledger"]["spent_usd"])


def write_run_files(store, rd):
    from . import export
    export.write_jsonl(rd / "enriched.jsonl", (r for r in export.record_rows(store) if r["status"] == "completed"))
    export.write_jsonl(rd / "quarantine.jsonl", ({**r, "last_error": r.get("reason")} for r in export.record_rows(store)
                                                 if r["status"] != "completed"))
    export.write_jsonl(rd / "calls.jsonl", export.call_rows(store))
    agg_path = rd / "aggregates.csv"
    if (rd / "ranking.csv").exists():
        import csv
        issues = {i["issue_id"]: i for i in json.loads((rd / "issues.json").read_text())} if (rd / "issues.json").exists() else {}
        with (rd / "ranking.csv").open() as fin, agg_path.open("w", newline="") as fout:
            rows = list(csv.DictReader(fin))
            w = csv.writer(fout, lineterminator="\n")
            w.writerow(["rank", "issue_id", "title", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
            for r in rows:
                w.writerow([r["rank"], r["issue_id"], issues.get(r["issue_id"], {}).get("title", ""), r["complaint_count"],
                            r["severity_sum"], r["mean_severity"], r["priority_score"]])


def render_memo(rd, memo, agg, ranking):
    claims = "\n".join(f"| {c['claim_id']} | `{c['issue_id']}` | {c['metric']} | {c['value']} |" for c in memo["claims"])
    alts = "\n".join(f"- **{x['area']}** — {x['assessment']}" for x in memo["alternatives"])
    lims = "\n".join(f"- {x}" for x in memo["limitations"])
    s = agg["scope"]
    md = f"""# Decision memo — {memo['headline']}

**Recommended priority area:** `{memo['recommended_area']}`
*Run `{rd.name}` · {s['records']:,} records in scope · {s['completed']:,} completed · {s['quarantined']:,} quarantined · {s['pending']:,} pending. Generated by the memo role (`{memo.get('_lc', 'claude-sonnet-5-5')}`) from saved aggregates only; every issue-level number is a checked claim in `claims.csv`.*

{memo['memo_markdown']}

## Alternatives considered
{alts}

## Limitations
{lims}

## Claims (validated against `ranking.csv`)
| claim | issue | metric | value |
|---|---|---|---|
{claims}
"""
    (rd / "memo.md").write_text(md, encoding="utf-8")


def cmd_rank(a):
    from .stages.rank import rerank_offline
    rd = config.RUNS / a.run_id
    src = Path(a.records) if a.records else (rd / "enriched.jsonl")
    if not src.exists() and Path(str(src) + ".gz").exists():
        src = Path(str(src) + ".gz")
    out = Path(a.out) if a.out else rd / "ranking.offline.csv"
    rows = rerank_offline(src, Path(a.membership) if a.membership else rd / "membership.csv", out)
    same = (rd / "ranking.csv").exists() and (rd / "ranking.csv").read_bytes() == out.read_bytes()
    print(f"wrote {out} ({len(rows)} issues); identical to saved ranking.csv: {same}")


def cmd_export(a):
    from . import export
    store = open_store(a.run_id)
    rd = config.RUNS / a.run_id
    input_csv = Path(store.get("input_csv"))
    files = export.grading_export(store, rd, a.out, analysis_count=store.one("SELECT COUNT(*) n FROM reviews WHERE synthetic=0")["n"],
                                  analysis_sha256=file_sha(input_csv), ingestion_json=a.ingestion,
                                  compress=not a.no_gzip)
    print("grading export:", files)


def cmd_status(a):
    store = open_store(a.run_id)
    print(json.dumps({"status": status_counts(store), "usage": usage_by_role(store), "spent_usd": store.spent_usd(),
                      "invocations": [dict(r) for r in store.q("SELECT * FROM invocations")]}, indent=2, default=str))


def cmd_watch(a):
    """Live view of a running (or finished) run: progress.json + latest log events. Read-only."""
    rd = config.RUNS / a.run_id
    while True:
        p = rd / "progress.json"
        if p.exists():
            s = json.loads(p.read_text())
            total = s["completed"] + s["pending"] + s["quarantined"]
            pct = 100 * (s["completed"] + s["quarantined"]) / total if total else 0
            eta = f"{int(s['eta_s'] // 60)}m{int(s['eta_s'] % 60):02d}s" if s.get("eta_s") else "-"
            print(f"\r{s['updated_at']} {pct:5.1f}% done | completed {s['completed']:,} pending {s['pending']:,} "
                  f"quarantined {s['quarantined']} | reuse {s['exact_text_reuse']:,} imported {s['imported_from_global_cache']:,} | "
                  f"calls ok/failed {s['calls_ok']}/{s['calls_failed']} retried {s['retried_ids']} fallback {s['fallback_ids']} | "
                  f"{s['model_records_per_s']}/s ETA {eta} | ${s['ledger']['spent_usd']:.4f} of ${s['ledger']['cap_usd']:.2f} "
                  f"| agents {len(s['agents'])}   ", end="", flush=True)
        if a.once:
            print()
            if p.exists():
                print(json.dumps(json.loads(p.read_text())["agents"], indent=2))
            return
        time.sleep(a.interval)


def cmd_cache(a):
    from . import result_cache
    from .export import call_rows
    if a.seed_from:
        store = open_store(a.seed_from)
        calls = {c["request_id"]: c for c in call_rows(store)}
        n = 0
        for r in store.q("""SELECT r.*, rv.text_sha FROM results r JOIN reviews rv USING(review_id)
                            WHERE r.status='completed' AND r.cache_source_id IS NULL AND rv.synthetic=0"""):
            call = calls.get(r["request_id"])
            if not call or call["outcome"] != "succeeded":
                continue
            res = {"topic": r["topic"], "subtopic": r["subtopic"], "intent": r["intent"], "sentiment": r["sentiment"],
                   "severity": r["severity"], "entities": json.loads(r["entities"] or "[]"),
                   "evidence_quote": r["evidence_quote"], "needs_review": bool(r["needs_review"]),
                   "review_reason": r["review_reason"] or "", "notes": r["notes"]}
            result_cache.put(r["text_sha"], r["label_config"], r["review_id"], res, call, a.seed_from)
            n += 1
        print(f"seeded {n} results from {a.seed_from}")
    print(json.dumps(result_cache.stats(), indent=2))


def cmd_profile(a):
    from .stages.ingest import profile_full
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    p = profile_full(a.input)
    (out / "ingestion_report.json").write_text(json.dumps(p, indent=2, ensure_ascii=False))
    print(json.dumps(p["contract_profile"]["counts"]), "distinct_nonempty_texts", p["distinct_nonempty_texts"])


def cmd_subset(a):
    from .stages.ingest import build_subset
    print(json.dumps(build_subset(a.full, a.out, a.nonempty, a.include), indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run pipeline stages (paid model calls for enrich/verify/group/memo)")
    r.add_argument("--input"); r.add_argument("--run-id", required=True)
    r.add_argument("--stages", help=f"comma list from {STAGES}")
    r.add_argument("--limit", type=int, help="max new records to dispatch for enrichment this invocation")
    r.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    r.add_argument("--spend-cap", type=float, default=config.DEFAULT_SPEND_CAP_USD)
    r.add_argument("--verify-fraction", type=float, default=config.VERIFY_SAMPLE_FRACTION)
    r.add_argument("--verify-min", type=int, default=10)
    r.add_argument("--stop-after", type=int, help="demo: graceful stop after N successful enrichment calls")
    r.add_argument("--no-global-cache", action="store_true", help="do not import results from earlier runs")
    r.set_defaults(fn=cmd_run)
    k = sub.add_parser("rank", help="offline: regenerate ranking from saved records + membership")
    k.add_argument("--run-id", required=True); k.add_argument("--records"); k.add_argument("--membership"); k.add_argument("--out")
    k.set_defaults(fn=cmd_rank)
    e = sub.add_parser("export", help="offline: write grading/ export")
    e.add_argument("--run-id", required=True); e.add_argument("--out", default="grading")
    e.add_argument("--ingestion", default="runs/ingestion/ingestion.json"); e.add_argument("--no-gzip", action="store_true")
    e.set_defaults(fn=cmd_export)
    s = sub.add_parser("status"); s.add_argument("--run-id", required=True); s.set_defaults(fn=cmd_status)
    w = sub.add_parser("watch", help="live progress of a run (read-only)")
    w.add_argument("--run-id", required=True); w.add_argument("--interval", type=float, default=2.0)
    w.add_argument("--once", action="store_true"); w.set_defaults(fn=cmd_watch)
    c = sub.add_parser("cache", help="cross-run result cache: stats / seed from a finished run")
    c.add_argument("--seed-from"); c.set_defaults(fn=cmd_cache)
    f = sub.add_parser("profile"); f.add_argument("--input", default=str(config.FULL_CSV)); f.add_argument("--out", default="runs/ingestion")
    f.set_defaults(fn=cmd_profile)
    b = sub.add_parser("subset"); b.add_argument("--full", default=str(config.FULL_CSV)); b.add_argument("--out", required=True)
    b.add_argument("--nonempty", type=int, default=100000); b.add_argument("--include", nargs="*", default=[])
    b.set_defaults(fn=cmd_subset)
    a = p.parse_args()
    a.fn(a)
