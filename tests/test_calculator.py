"""Calculator arithmetic checks (offline, synthetic fixtures; no API key)."""
import copy
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from cost import calculator as calc  # noqa: E402

RATES = ROOT / "cost" / "rates.csv"


def fixture_usage():
    rows = []
    for rid, run, role, model, items in [
        ("a", "cold", "enrich", "claude-haiku-5-5", {"input": 3000, "cache_write_5m": 2500, "cache_read": 0, "output": 4000}),
        ("b", "cold", "enrich", "claude-haiku-5-5", {"input": 3100, "cache_write_5m": 0, "cache_read": 2500, "output": 4100}),
        ("c", "cold", "verify", "claude-sonnet-5-5", {"input": 1500, "cache_write_5m": 2000, "cache_read": 0, "output": 2000}),
        ("d", "cold", "memo", "claude-sonnet-5-5", {"input": 9000, "cache_write_5m": 0, "cache_read": 0, "output": 3000}),
    ]:
        for item, units in items.items():
            rows.append({"request_id": rid, "pilot_run": run, "role": role, "model": model, "outcome": "succeeded",
                         "billing_item": item, "billed_units": units, "unit": "token"})
    return rows


def doubled_rates(tmp_path):
    out = tmp_path / "rates_x2.csv"
    with RATES.open() as fin, out.open("w", newline="") as fout:
        r = csv.DictReader(fin)
        w = csv.DictWriter(fout, fieldnames=r.fieldnames)
        w.writeheader()
        for row in r:
            row["price_usd"] = str(float(row["price_usd"]) * 2)
            w.writerow(row)
    return out


def test_item_cost_formula():
    rates, _ = calc.load_rates(RATES)
    # 1,000,000 Haiku output tokens at $0.50 per 1M = $0.50
    assert abs(calc.item_cost(1_000_000, "claude-haiku-5-5", "output", rates) - 0.50) < 1e-12
    by, _ = calc.api_costs(fixture_usage(), rates)
    manual = (3000 * .10 + 2500 * .125 + 4000 * .50 + 3100 * .10 + 2500 * .01 + 4100 * .50) / 1e6 \
        + (1500 * 2 + 2000 * 2.5 + 2000 * 10 + 9000 * 2 + 3000 * 10) / 1e6
    assert abs(by[("total",)] - manual) < 1e-12


def test_doubling_rates_doubles_api_spend(tmp_path):
    r1, _ = calc.load_rates(RATES)
    r2, _ = calc.load_rates(doubled_rates(tmp_path))
    u = fixture_usage()
    b1, units1 = calc.api_costs(u, r1)
    b2, units2 = calc.api_costs(u, r2)
    for k in b1:
        assert abs(b2[k] - 2 * b1[k]) < 1e-12
    assert units1 == units2  # usage untouched


def test_projection_scales_with_rates_not_time(tmp_path):
    r1, _ = calc.load_rates(RATES)
    r2, _ = calc.load_rates(doubled_rates(tmp_path))
    basis = {"enrich_tok": {("claude-haiku-5-5", "input"): 60, ("claude-haiku-5-5", "output"): 80},
             "fallback_tok": {("claude-sonnet-5-5", "input"): 3000, ("claude-sonnet-5-5", "output"): 120},
             "verify_tok": {("claude-sonnet-5-5", "input"): 120, ("claude-sonnet-5-5", "output"): 90},
             "group_tok_per_call": {("claude-haiku-5-5", "input"): 2000, ("claude-haiku-5-5", "output"): 600},
             "memo_tok": {("claude-sonnet-5-5", "input"): 9000, ("claude-sonnet-5-5", "output"): 3000},
             "retry_rate": 0.01, "fallback_fraction": 0.001, "sec_per_text": 0.05, "verify_sec_per_record": 0.2,
             "group_s": 5, "memo_s": 30, "ingest_profile_s": 5.4, "workers_ceiling": 8}
    a = json.loads((ROOT / "cost" / "assumptions.json").read_text())
    t = {"name": "full", "rows": 660622, "nonempty": 660609, "empty": 13, "distinct_texts": 484189}
    for scen in a["scenarios"].values():
        p1 = calc.project(t, basis, a, r1, scen)
        p2 = calc.project(t, basis, a, r2, scen)
        assert abs(p2["cost"]["api_total"] - 2 * p1["cost"]["api_total"]) < 1e-9
        assert p1["time_s"] == p2["time_s"]


def test_projected_volume_does_not_change_measured(tmp_path):
    r1, _ = calc.load_rates(RATES)
    u = fixture_usage()
    before = calc.api_costs(copy.deepcopy(u), r1)[0][("total",)]
    a = json.loads((ROOT / "cost" / "assumptions.json").read_text())
    a["targets"][1]["distinct_texts"] = 1
    after = calc.api_costs(copy.deepcopy(u), r1)[0][("total",)]
    assert before == after
