"""OFFLINE cost/runtime calculator (default mode: no API key, no network, no model calls).

    python -m cost.calculator                         # recompute from saved usage x editable rates -> cost/report.md
    python -m cost.calculator --rates my_rates.csv    # what-if pricing
    python -m cost.calculator --assumptions a.json    # what-if budget/workers/fallback/targets

Every charged item = billed_units x price_usd / per_units. Items are mutually exclusive (Anthropic usage reports
input_tokens excluding cache reads/writes; output tokens already include any thinking, so nothing is added twice).
"""
import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COST = ROOT / "cost"
ITEMS = ("input", "cache_write_5m", "cache_read", "output")
FIELD = {"input": "input_tokens", "cache_write_5m": "cache_creation_input_tokens",
         "cache_read": "cache_read_input_tokens", "output": "output_tokens"}


# ----------------------------------------------------------------------------- pure functions (tested)
def load_rates(path):
    rates, meta = {}, {}
    with Path(path).open(newline="") as f:
        for r in csv.DictReader(f):
            rates[(r["model"], r["billing_item"])] = float(r["price_usd"]) / float(r["per_units"])
            meta[(r["model"], r["billing_item"])] = r
    return rates, meta


def load_usage(path):
    with Path(path).open(newline="") as f:
        return [dict(r, billed_units=int(r["billed_units"])) for r in csv.DictReader(f)]


def item_cost(units, model, item, rates):
    if (model, item) not in rates:
        raise KeyError(f"no rate for {model}/{item}")
    return units * rates[(model, item)]


def api_costs(usage, rates):
    """Sum of billed_units x unit price, grouped by pilot_run / role / model / item."""
    by = defaultdict(float)
    units = defaultdict(int)
    for u in usage:
        if not u["billed_units"]:
            continue
        c = item_cost(u["billed_units"], u["model"], u["billing_item"], rates)
        for key in [("total",), ("run", u["pilot_run"]), ("run_role", u["pilot_run"], u["role"]),
                    ("run_role_item", u["pilot_run"], u["role"], u["model"], u["billing_item"])]:
            by[key] += c
            units[key] += u["billed_units"]
    return by, units


def tokens_per_unit(calls, role, denominator, models=None):
    """Average billed tokens per unit of work for a role, by (model, item). Includes failed attempts/retries."""
    acc = defaultdict(float)
    for c in calls:
        if c["role"] != role or (models and c["model"] not in models):
            continue
        for item in ITEMS:
            acc[(c["model"], item)] += c[FIELD[item]]
    return {k: v / denominator for k, v in acc.items()} if denominator else {}


def price_tokens(tok, rates, multiplier=1.0):
    return sum(units * multiplier * rates[(model, item)] for (model, item), units in tok.items())


# ----------------------------------------------------------------------------- measurement
def read_jsonl(path):
    return [json.loads(l) for l in Path(path).open(encoding="utf-8") if l.strip()]


def measure_pilot(rates):
    runs = json.loads((COST / "pilot_runs.json").read_text())
    calls = read_jsonl(COST / "pilot_calls.jsonl")
    usage = load_usage(COST / "usage.csv")
    records = read_jsonl(COST / "pilot_records.jsonl")
    by, units = api_costs(usage, rates)
    cold_calls = [c for c in calls if c["pilot_run"] == "cold"]
    originals = sum(1 for r in records if r["status"] == "completed" and not r.get("cache_source_id"))
    return {"runs": runs, "calls": calls, "usage": usage, "records": records, "by": by, "units": units,
            "cold_calls": cold_calls, "originals": originals}


def measure_run(run_id):
    """Refresh measurement from a saved development run (runs/<id>/): first invocation only (cold)."""
    rd = ROOT / "runs" / run_id
    if not (rd / "calls.jsonl").exists():
        return None
    calls = read_jsonl(rd / "calls.jsonl")
    summaries = [json.loads(l) for l in (rd / "run_summaries.jsonl").open()]
    enrich_calls = [c for c in calls if c["role"] == "enrich"]
    enriched = read_jsonl(rd / "enriched.jsonl") if (rd / "enriched.jsonl").exists() else []
    originals = sum(1 for r in enriched if not r.get("cache_source_id"))
    enrich_seconds = sum(s["stages"].get("enrich", {}).get("seconds", 0) for s in summaries)
    workers = max((s["workers"] for s in summaries if "enrich" in s["stages"]), default=1)
    ver = [c for c in calls if c["role"] == "verify"]
    ver_ids = sum(len(c["review_ids"]) for c in ver if c["outcome"] == "succeeded")
    haiku = [c for c in enrich_calls if "haiku" in c["model"]]
    verify_seconds = sum(s["stages"].get("verify", {}).get("seconds", 0) for s in summaries)
    return {"run_id": run_id, "records": len(enriched), "originals": originals,
            "retry_rate": sum((c["attempt"] or 0) > 0 or c["outcome"] == "failed" for c in haiku) / max(1, len(haiku)),
            "fallback_fraction": sum("sonnet" in c["model"] for c in enrich_calls) / max(1, originals),
            "fallback_tokens_per_call": tokens_per_unit([c for c in enrich_calls if "sonnet" in c["model"]], "enrich",
                                                        max(1, sum("sonnet" in c["model"] for c in enrich_calls))),
            "verify_sec_per_record": verify_seconds / ver_ids if ver_ids else None,
            "cache_reuse": len(enriched) - originals, "enrich_calls": len(enrich_calls),
            "enrich_failed_calls": sum(c["outcome"] == "failed" for c in enrich_calls),
            "fallback_calls": sum("sonnet" in c["model"] for c in enrich_calls),
            "enrich_tokens_per_text": tokens_per_unit(calls, "enrich", originals),
            "verify_tokens_per_record": tokens_per_unit(calls, "verify", ver_ids),
            "enrich_seconds": enrich_seconds, "workers": workers,
            "texts_per_second": originals / enrich_seconds if enrich_seconds else None}


# ----------------------------------------------------------------------------- projection
def project(target, basis, a, rates, scen):
    distinct = target["distinct_texts"]
    tm = scen["token_multiplier"]
    retry = scen["retry_rate"] if scen["retry_rate"] is not None else basis["retry_rate"]
    fb = scen["fallback_fraction"] if scen["fallback_fraction"] is not None else basis["fallback_fraction"]
    fb = min(fb, a["max_fallback_fraction"])
    # measured per-text tokens already include measured retries; a scenario retry_rate adds extra retried work
    enrich = price_tokens(basis["enrich_tok"], rates, tm) * distinct * (1 + (retry if scen["retry_rate"] is not None else 0))
    fallback = price_tokens(basis["fallback_tok"], rates, tm) * distinct * fb
    verify_n = math.ceil(distinct * a["verify_fraction"])
    verify = price_tokens(basis["verify_tok"], rates, tm) * verify_n
    group_calls = math.ceil(a["issues_expected"] / a["group_issues_per_call"])
    group = price_tokens(basis["group_tok_per_call"], rates, tm) * group_calls
    memo = price_tokens(basis["memo_tok"], rates, scen["memo_multiplier"])
    total = enrich + fallback + verify + group + memo
    eff_workers = max(1.0, min(a["max_workers"], basis["workers_ceiling"]) * a["parallel_efficiency"])
    enrich_s = distinct * basis["sec_per_text"] * (1 + (retry or 0)) / eff_workers
    verify_s = verify_n * basis["verify_sec_per_record"]
    fixed_s = basis["ingest_profile_s"] * target["rows"] / 660622 + basis["group_s"] + basis["memo_s"]
    return {"target": target["name"], "rows": target["rows"], "nonempty": target["nonempty"],
            "empty_quarantined": target["empty"], "distinct_texts_sent": distinct,
            "cache_reuse_rows": target["nonempty"] - distinct,
            "cost": {"enrich": enrich, "fallback": fallback, "verify": verify, "group": group, "memo": memo,
                     "api_total": total},
            "assumptions": {"retry_rate": retry, "fallback_fraction": fb, "token_multiplier": tm,
                            "verify_records": verify_n, "group_calls": group_calls, "effective_workers": eff_workers},
            "time_s": {"enrich": enrich_s, "verify": verify_s, "fixed": fixed_s, "total": enrich_s + verify_s + fixed_s},
            "over_budget": total > a["spend_limit_usd"]}


def build_basis(m, refresh, rates):
    """Per-unit token usage + throughput. Uses the largest refreshed measurement available for enrichment."""
    cold = m["cold_calls"]
    originals = max(1, m["originals"])
    enrich_haiku = [c for c in cold if c["role"] == "enrich" and "haiku" in c["model"]]
    fb_calls = [c for c in cold if c["role"] == "enrich" and "sonnet" in c["model"]]
    basis = {"source": "pilot-100 (cold)", "enrich_tok": tokens_per_unit(enrich_haiku, "enrich", originals),
             "retry_rate": sum((c["attempt"] or 0) > 0 or c["outcome"] == "failed" for c in enrich_haiku) / max(1, len(enrich_haiku)),
             "fallback_fraction": len(fb_calls) / originals}
    ver = [c for c in cold if c["role"] == "verify"]
    ver_n = sum(len(c["review_ids"]) for c in ver if c["outcome"] == "succeeded")
    basis["verify_tok"] = tokens_per_unit(ver, "verify", max(1, ver_n))
    grp = [c for c in cold if c["role"] == "group"]
    basis["group_tok_per_call"] = tokens_per_unit(grp, "group", max(1, len(grp)))
    basis["memo_tok"] = tokens_per_unit([c for c in cold if c["role"] == "memo"], "memo", 1)
    # Fallback per text: one single-review Sonnet request. If none happened, price the Haiku per-text input as an
    # uncached single request on Sonnet (system prompt + text) as a conservative stand-in.
    if fb_calls:
        basis["fallback_tok"] = tokens_per_unit(fb_calls, "enrich", len(fb_calls))
    else:
        sys_tokens = max((c["cache_read_input_tokens"] + c["cache_creation_input_tokens"] for c in enrich_haiku), default=3000)
        per_in = sum(v for (mo, it), v in basis["enrich_tok"].items() if it == "input")
        per_out = sum(v for (mo, it), v in basis["enrich_tok"].items() if it == "output")
        basis["fallback_tok"] = {("claude-sonnet-5-5", "cache_read"): sys_tokens, ("claude-sonnet-5-5", "input"): per_in + 60,
                                 ("claude-sonnet-5-5", "output"): per_out * 1.5}
        basis["fallback_note"] = ("no fallback measured; modeled as a single-review Sonnet request (system prompt as cache "
                                  "read, review + wrapper uncached, 1.5x output)")
    cold_run = next((r for r in m["runs"]["runs"] if r["label"] == "cold"), {})
    st = cold_run.get("stage_seconds", {})
    basis["sec_per_text"] = st.get("enrich", 0) / originals
    basis["verify_sec_per_record"] = st.get("verify", 0) / max(1, ver_n)
    basis["group_s"], basis["memo_s"] = st.get("group", 0), st.get("memo", 0)
    basis["ingest_profile_s"] = 5.4  # measured full-file profile (runs/ingestion/ingestion_report.json)
    basis["workers_ceiling"] = 8
    for r in refresh:  # later, larger measurements replace the pilot's enrichment basis
        if r and r["originals"] >= 400 and r["enrich_tokens_per_text"]:
            basis["enrich_tok"] = {k: v for k, v in r["enrich_tokens_per_text"].items() if "haiku" in k[0]}
            basis["source"] = f"{r['run_id']} ({r['originals']} originals)"
            if r["texts_per_second"]:
                basis["sec_per_text"] = r["workers"] / r["texts_per_second"]  # normalize back to one worker
            if r["verify_tokens_per_record"]:
                basis["verify_tok"] = r["verify_tokens_per_record"]
            basis["retry_rate"], basis["fallback_fraction"] = r["retry_rate"], r["fallback_fraction"]
            if r["fallback_tokens_per_call"]:
                basis["fallback_tok"] = r["fallback_tokens_per_call"]
                basis.pop("fallback_note", None)
            if r["verify_sec_per_record"]:
                basis["verify_sec_per_record"] = r["verify_sec_per_record"]
    return basis


# ----------------------------------------------------------------------------- report
def usd(x):
    return f"${x:,.4f}" if x < 10 else f"${x:,.2f}"


def hms(s):
    s = int(round(s))
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


def report(rates, rmeta, a, m, refresh):
    by, units = m["by"], m["units"]
    runs = {r["label"]: r for r in m["runs"]["runs"]}
    rr = m["runs"]
    L = ["# 100-review cost & runtime calculator — report", "",
         "Generated offline by `python -m cost.calculator` from saved usage (`cost/usage.csv`) × editable rates "
         "(`cost/rates.csv`). No API key or model call is needed. Formula per billed item: "
         "`item_cost = billed_units × price_usd / per_units`; `total = Σ item_cost`. Items are mutually exclusive "
         "(input excludes cache reads/writes; output already includes any thinking).", "",
         "## Measured pilot (cost_100.csv)", "",
         f"- Input: `{rr['input']}` sha256 `{rr['input_sha256']}` — checksum matches manifest: **{rr['checksum_ok']}**",
         f"- Review IDs: {rr['records']} (listed in `pilot_runs.json`), unique texts: {rr['unique_texts']}",
         f"- Verification sample: {rr['declared_verification_sample']}", ""]
    L += ["| run | wall-clock (measured) | completed | quarantined | enrich calls ok/failed | result-cache hits | API spend |",
          "|---|---|---|---|---|---|---|"]
    for lab in ("cold", "warm"):
        r = runs.get(lab, {})
        sc = r.get("status_counts", {})
        L.append(f"| {lab} | {r.get('wall_clock_seconds', 0):.1f} s | {sc.get('completed', 0)} | {sc.get('quarantined', 0)} | "
                 f"{r.get('enrich_calls_ok', 0)}/{r.get('enrich_calls_failed', 0)} | {r.get('enrich_cache_hits', 0)} | "
                 f"{usd(by.get(('run', lab), 0.0))} |")
    cold_total = by.get(("run", "cold"), 0.0)
    comp = runs.get("cold", {}).get("status_counts", {}).get("completed", 0) or 1
    L += ["", f"- Cold API spend per 1,000 input rows: **{usd(cold_total / rr['records'] * 1000)}**; per completed record: "
          f"**{usd(cold_total / comp)}**; cold throughput: **{rr['records'] / max(1e-9, runs.get('cold', {}).get('wall_clock_seconds', 1)):.2f} rows/s** end-to-end (1 worker).",
          f"- Warm run incremental API spend: **{usd(by.get(('run', 'warm'), 0.0))}** (enrichment calls in warm run: "
          f"{sum(1 for c in m['calls'] if c['pilot_run'] == 'warm' and c['role'] == 'enrich')}).",
          "- Local compute: not metered. " + a["local_compute"]["note"] + " Reported as **unknown** (not $0).", "",
          "### Per stage (cold run)", "",
          "| stage | provider | model | effort / thinking | prompt+schema | batch | workers | requests (ok/failed) | input | cache write | cache read | output | API cost | stage time |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    cold = runs.get("cold", {})
    roles = rr["roles"]
    for role, key in (("enrich", "enrich"), ("enrich", "enrich_fallback"), ("verify", "verify"), ("group", "group"), ("memo", "memo")):
        model = roles[key]["model"]
        cs = [c for c in m["cold_calls"] if c["role"] == role and c["model"] == model]
        if key == "enrich_fallback" and not cs:
            L.append(f"| enrich fallback | anthropic | {model} | {roles[key]['effort']} / {roles[key]['thinking']['type']} | {roles[key]['prompt']} | 1 | 1 | 0 (not triggered) | 0 | 0 | 0 | 0 | $0 | — |")
            continue
        tok = {it: sum(c[FIELD[it]] for c in cs) for it in ITEMS}
        cost = sum(item_cost(tok[it], model, it, rates) for it in ITEMS)
        batch = {"enrich": rr["batch_size"], "enrich_fallback": 1, "verify": rr["verify_batch_size"], "group": "10 issues", "memo": "1"}[key]
        L.append(f"| {role}{' fallback' if key == 'enrich_fallback' else ''} | anthropic | {model} | {roles[key]['effort']} / "
                 f"{roles[key]['thinking']['type']} | {roles[key]['prompt']}+schema_v1 | {batch} | 1 | "
                 f"{sum(c['outcome'] == 'succeeded' for c in cs)}/{sum(c['outcome'] == 'failed' for c in cs)} | "
                 f"{tok['input']:,} | {tok['cache_write_5m']:,} | {tok['cache_read']:,} | {tok['output']:,} | {usd(cost)} | "
                 f"{cold.get('stage_seconds', {}).get(role, 0):.1f} s |")
    L += ["", f"Stage times are measured wall-clock per stage; summed call latencies are not used as wall-clock. "
          f"Ingest stage: {cold.get('stage_seconds', {}).get('ingest', 0):.2f} s; rank: {cold.get('stage_seconds', {}).get('rank', 0):.2f} s; "
          f"export: {cold.get('stage_seconds', {}).get('export', 0):.2f} s (local, no API cost).", "",
          "### Rates used (editable `cost/rates.csv`)", "", "| model | item | price | unit | tier | source | checked |", "|---|---|---|---|---|---|---|"]
    for (model, item), r in sorted(rmeta.items()):
        L.append(f"| {model} | {item} | ${float(r['price_usd']):g} | per {int(float(r['per_units'])):,} {r['unit']}s | {r['tier']} | [{r['source_url'].split('/')[2]}]({r['source_url']}) | {r['checked_date']} |")
    L += ["", "## Refreshed measurements (100 → 500 → 10,000)", "",
          "| run | originals sent | exact-text reuse | enrich calls (failed) | fallback calls | Haiku tokens/text in·cw·cr·out | texts/s | workers |",
          "|---|---|---|---|---|---|---|---|"]
    for r in refresh:
        if not r:
            continue
        t = r["enrich_tokens_per_text"]
        g = lambda it: sum(v for (mo, i), v in t.items() if i == it and "haiku" in mo)
        L.append(f"| {r['run_id']} | {r['originals']:,} | {r['cache_reuse']:,} | {r['enrich_calls']} ({r['enrich_failed_calls']}) | "
                 f"{r['fallback_calls']} | {g('input'):.0f} · {g('cache_write_5m'):.0f} · {g('cache_read'):.0f} · {g('output'):.0f} | "
                 f"{(r['texts_per_second'] or 0):.2f} | {r['workers']} |")
    basis = build_basis(m, refresh, rates)
    L += ["", f"Projection basis: **{basis['source']}**. Measured retry/invalid rate {basis['retry_rate']:.3f}; measured "
          f"fallback fraction {basis['fallback_fraction']:.4f}; seconds/text at 1 worker {basis['sec_per_text']:.3f}. "
          + (basis.get("fallback_note", "")), "",
          "## Controls", "",
          f"- Spending limit: **${a['spend_limit_usd']:.2f}** (enforced in code by `pipeline/ledger.py`: reserve worst-case cost before dispatch, stop admitting work when spent + reserved + next > cap).",
          f"- Output-token cap per enrichment request: {a['output_token_cap_per_enrich_request']:,}; max workers: {a['max_workers']}; max fallback fraction: {a['max_fallback_fraction']:.0%}; verification fraction: {a['verify_fraction']:.0%}; parallel efficiency assumption: {a['parallel_efficiency']}.",
          "", "## Projections (estimates, not measurements)", ""]
    for t in a["targets"]:
        for name, scen in a["scenarios"].items():
            p = project(t, basis, a, rates, scen)
            c = p["cost"]
            warn = " ⚠️ **EXCEEDS BUDGET**" if p["over_budget"] else ""
            L.append(f"- **{p['target']} — {name}**: rows {p['rows']:,} (nonempty {p['nonempty']:,}, empty quarantined "
                     f"{p['empty_quarantined']}), texts sent {p['distinct_texts_sent']:,}, cache-reuse rows {p['cache_reuse_rows']:,}. "
                     f"API ≈ **{usd(c['api_total'])}** (enrich {usd(c['enrich'])}, fallback {usd(c['fallback'])}, verify "
                     f"{usd(c['verify'])}, group {usd(c['group'])}, memo {usd(c['memo'])}); time ≈ **{hms(p['time_s']['total'])}** "
                     f"at {p['assumptions']['effective_workers']:.1f} effective workers.{warn}")
    L += ["", "Fixed overhead (group naming, memo, full-file profile) is added once per run, never multiplied per review. "
          "Local compute is excluded (unknown). Batch-API discounts are not applied (not used).", "",
          "## Replay", "", "```bash", "python -m cost.calculator", "python -m pytest tests/test_calculator.py", "```"]
    return "\n".join(L) + "\n"


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rates", default=str(COST / "rates.csv"))
    p.add_argument("--assumptions", default=str(COST / "assumptions.json"))
    p.add_argument("--out", default=str(COST / "report.md"))
    a = p.parse_args()
    rates, rmeta = load_rates(a.rates)
    assumptions = json.loads(Path(a.assumptions).read_text())
    for t in assumptions["targets"]:
        if t.get("distinct_texts") is None and t.get("distinct_from"):
            meta = ROOT / Path(t["distinct_from"]).with_suffix(".meta.json")
            t["distinct_texts"] = json.loads(meta.read_text()).get("distinct_nonempty_texts") if meta.exists() else t["nonempty"]
    m = measure_pilot(rates)
    refresh = [measure_run(r) for r in assumptions.get("refresh_runs", [])]
    text = report(rates, rmeta, assumptions, m, refresh)
    Path(a.out).write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
