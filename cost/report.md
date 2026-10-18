# 100-review cost & runtime calculator — report

Generated offline by `python -m cost.calculator` from saved usage (`cost/usage.csv`) × editable rates (`cost/rates.csv`). No API key or model call is needed. Formula per billed item: `item_cost = billed_units × price_usd / per_units`; `total = Σ item_cost`. Items are mutually exclusive (input excludes cache reads/writes; output already includes any thinking).

## Measured pilot (cost_100.csv)

- Input: `cost_100.csv` sha256 `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e` — checksum matches manifest: **True**
- Review IDs: 100 (listed in `pilot_runs.json`), unique texts: 100
- Verification sample: 10% (10 reviews), lowest sha256('verify-v1:'+review_id)

| run | wall-clock (measured) | completed | quarantined | enrich calls ok/failed | result-cache hits | API spend |
|---|---|---|---|---|---|---|
| cold | 162.8 s | 100 | 0 | 2/0 | 0 | $0.0801 |
| warm | 0.1 s | 100 | 0 | 0/0 | 100 | $0.0000 |

- Cold API spend per 1,000 input rows: **$0.8008**; per completed record: **$0.0008**; cold throughput: **0.61 rows/s** end-to-end (1 worker).
- Warm run incremental API spend: **$0.0000** (enrichment calls in warm run: 0).
- Local compute: not metered. Laptop (Apple Silicon). Electricity/hardware cost not measured: reported as unknown, not zero. Reported as **unknown** (not $0).

### Per stage (cold run)

| stage | provider | model | effort / thinking | prompt+schema | batch | workers | requests (ok/failed) | input | cache write | cache read | output | API cost | stage time |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| enrich | anthropic | claude-haiku-5-5 | low / disabled | enrich_v1+schema_v1 | 50 | 1 | 2/0 | 7,363 | 4,006 | 4,006 | 12,546 | $0.0076 | 47.0 s |
| enrich fallback | anthropic | claude-sonnet-5-5 | low / between_tools | enrich_v1 | 1 | 1 | 0 (not triggered) | 0 | 0 | 0 | 0 | $0 | — |
| verify | anthropic | claude-sonnet-5-5 | low / between_tools | verify_v1+schema_v1 | 25 | 1 | 1/0 | 547 | 3,146 | 0 | 1,168 | $0.0206 | 65.5 s |
| group | anthropic | claude-haiku-5-5 | low / disabled | group_v1+schema_v1 | 10 issues | 1 | 2/0 | 3,914 | 776 | 776 | 1,744 | $0.0014 | 9.6 s |
| memo | anthropic | claude-sonnet-5-5 | medium / adaptive | memo_v1+schema_v1 | 1 | 1 | 1/0 | 6,380 | 1,513 | 0 | 3,398 | $0.0505 | 40.5 s |

Stage times are measured wall-clock per stage; summed call latencies are not used as wall-clock. Ingest stage: 0.03 s; rank: 0.00 s; export: 0.00 s (local, no API cost).

### Rates used (editable `cost/rates.csv`)

| model | item | price | unit | tier | source | checked |
|---|---|---|---|---|---|---|
| claude-haiku-5-5 | cache_read | $0.01 | per 1,000,000 tokens | standard (prompts <=100K tokens) | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-haiku-5-5 | cache_write_5m | $0.125 | per 1,000,000 tokens | standard (prompts <=100K tokens) | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-haiku-5-5 | input | $0.1 | per 1,000,000 tokens | standard (prompts <=100K tokens) | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-haiku-5-5 | output | $0.5 | per 1,000,000 tokens | standard (prompts <=100K tokens) | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-sonnet-5-5 | cache_read | $0.2 | per 1,000,000 tokens | standard | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-sonnet-5-5 | cache_write_5m | $2.5 | per 1,000,000 tokens | standard | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-sonnet-5-5 | input | $2 | per 1,000,000 tokens | standard | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |
| claude-sonnet-5-5 | output | $10 | per 1,000,000 tokens | standard | [platform.claude.com](https://platform.claude.com/docs/en/about-claude/pricing) | 2026-10-13 |

## Refreshed measurements (100 → 500 → 10,000)

| run | originals sent | exact-text reuse | enrich calls (failed) | fallback calls | Haiku tokens/text in·cw·cr·out | texts/s | workers |
|---|---|---|---|---|---|---|---|
| pilot-100 | 100 | 0 | 2 (0) | 0 | 74 · 40 · 40 · 125 | 2.13 | 1 |
| dev-500 | 479 | 21 | 11 (0) | 0 | 73 · 25 · 67 · 128 | 6.09 | 2 |
| dev-10k | 8,448 | 1,552 | 183 (0) | 2 | 74 · 1 · 84 · 130 | 10.28 | 4 |

Projection basis: **dev-10k (8448 originals)**. Measured retry/invalid rate 0.061; measured fallback fraction 0.0002; seconds/text at 1 worker 0.389. 

## Controls

- Spending limit: **$15.00** (enforced in code by `pipeline/ledger.py`: reserve worst-case cost before dispatch, stop admitting work when spent + reserved + next > cap).
- Output-token cap per enrichment request: 8,000; max workers: 4; max fallback fraction: 2%; verification fraction: 1%; parallel efficiency assumption: 0.85.

## Projections (estimates, not measurements)

- **declared analysis subset (100k) — base**: rows 100,013 (nonempty 100,000, empty quarantined 13), texts sent 78,099, cache-reuse rows 21,901. API ≈ **$7.0601** (enrich $5.7146, fallback $0.1366, verify $1.1543, group $0.0041, memo $0.0505); time ≈ **2h 48m 26s** at 3.4 effective workers.
- **declared analysis subset (100k) — conservative**: rows 100,013 (nonempty 100,000, empty quarantined 13), texts sent 78,099, cache-reuse rows 21,901. API ≈ **$23.83** (enrich $7.8576, fallback $14.42, verify $1.4429, group $0.0051, memo $0.1010); time ≈ **2h 54m 17s** at 3.4 effective workers. ⚠️ **EXCEEDS BUDGET**
- **full corpus, exact-text reuse — base**: rows 660,622 (nonempty 660,609, empty quarantined 13), texts sent 484,189, cache-reuse rows 176,420. API ≈ **$43.49** (enrich $35.43, fallback $0.8468, verify $7.1566, group $0.0041, memo $0.0505); time ≈ **17h 19m 53s** at 3.4 effective workers. ⚠️ **EXCEEDS BUDGET**
- **full corpus, exact-text reuse — conservative**: rows 660,622 (nonempty 660,609, empty quarantined 13), texts sent 484,189, cache-reuse rows 176,420. API ≈ **$147.19** (enrich $48.71, fallback $89.42, verify $8.9458, group $0.0051, memo $0.1010); time ≈ **17h 56m 07s** at 3.4 effective workers. ⚠️ **EXCEEDS BUDGET**
- **full corpus, no reuse — base**: rows 660,622 (nonempty 660,609, empty quarantined 13), texts sent 660,609, cache-reuse rows 0. API ≈ **$59.31** (enrich $48.34, fallback $1.1553, verify $9.7654, group $0.0041, memo $0.0505); time ≈ **23h 38m 27s** at 3.4 effective workers. ⚠️ **EXCEEDS BUDGET**
- **full corpus, no reuse — conservative**: rows 660,622 (nonempty 660,609, empty quarantined 13), texts sent 660,609, cache-reuse rows 0. API ≈ **$200.78** (enrich $66.46, fallback $122.00, verify $12.21, group $0.0051, memo $0.1010); time ≈ **24h 27m 54s** at 3.4 effective workers. ⚠️ **EXCEEDS BUDGET**

Fixed overhead (group naming, memo, full-file profile) is added once per run, never multiplied per review. Local compute is excluded (unknown). Batch-API discounts are not applied (not used).

## Replay

```bash
python -m cost.calculator
python -m pytest tests/test_calculator.py
```
