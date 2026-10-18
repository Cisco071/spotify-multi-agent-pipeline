# 100-review cost & runtime calculator

Everything here replays **offline** from saved measurements: no API key, no network, no model calls.

## Offline replay (default — what graders run)

```bash
python -m cost.calculator                          # recompute cost/report.md from usage.csv x rates.csv
python -m cost.calculator --rates my_rates.csv     # what-if pricing (e.g. double every price)
python -m cost.calculator --assumptions a.json     # what-if budget / workers / fallback / targets
python -m pytest tests/test_calculator.py          # arithmetic checks incl. "2x rates => 2x API spend, same time"
```

Importing or opening the calculator never makes calls; `cost/calculator.py` does not import the API client.

## Explicit paid pilot (only when you mean to spend ~$0.08)

```bash
python -m cost.pilot --paid          # cold run (empty result cache, 1 worker) then warm run on cost_100.csv
python -m cost.pilot --collect-only  # no calls: re-extract the evidence files below from saved state
```

## Files

| File | Contents |
|---|---|
| `pilot_runs.json` | input checksum check, the 100 review IDs, cold/warm wall-clock, per-stage seconds, statuses, cache hits, model/effort/prompt/schema per role, batch sizes |
| `pilot_records.jsonl` | one record per pilot ID (contract schema + row hash), separate from `grading/records.jsonl` |
| `pilot_calls.jsonl` | every attempted call (cold + warm), with run label, request IDs, role, model, settings, raw usage, latency, outcome |
| `usage.csv` | one row per call x billing item (`input`, `cache_write_5m`, `cache_read`, `output`) — mutually exclusive token categories as reported by the provider |
| `rates.csv` | editable prices with unit, tier, currency, dated source link |
| `assumptions.json` | editable budget, output cap, max workers, fallback cap, verification fraction, scenarios, projection targets, refresh runs |
| `report.md` | generated tables: measured pilot, per stage, refreshed 100→500→10k measurements, controls, projections |

## Formula

`item_cost = billed_units × price_usd / per_units`, `total = Σ item_cost`. Anthropic `usage.input_tokens` already
excludes cache reads/writes, so the four items never overlap; thinking was disabled for Haiku and any Sonnet thinking
is already inside `output_tokens` (never added twice). Only the standard tier was used (no Batch API discount).
Local compute (the laptop) is not metered and is reported as **unknown**, not $0.

## Projections

Per-stage token usage per unit of work (per text sent, per verified record, per grouping call, per memo) is taken
from the largest refreshed measurement (`dev-10k`), multiplied by the target's work counts and priced with
`rates.csv` — so changing rates rescales projections, while changing projected volume never changes measured
results. Fixed overhead (grouping, memo, full-file profile) is added once. The conservative scenario adds a 10%
retry rate, the fallback at its 2% cap, +25% tokens and a 2x memo.
