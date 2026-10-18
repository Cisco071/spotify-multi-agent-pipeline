"""Central configuration: models, prompt/schema versions, label_config strings, prices and limits.

Changing any value that feeds a label_config invalidates the exact-text result cache by design.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"
RUNS = ROOT / "runs"
STATE = ROOT / "state"
DATASET = ROOT / "Final Assignment - Spotify Reviews Dataset"
FULL_CSV = DATASET / "spotify_reviews_18months.csv"
FULL_SHA256 = "1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6"

SCHEMA_VERSION = "schema_v1"
TAXONOMY_VERSION = "taxonomy_v1"

# Model allowlist. Anything not listed here is refused by llm.py.
HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
ALLOWED_MODELS = {HAIKU, SONNET}

ROLES = {
    # role: (model, prompt file, thinking setting, effort)
    "enrich": {"model": HAIKU, "prompt": "enrich_v1", "thinking": {"type": "disabled"}, "effort": "low",
               "max_tokens": 8000},
    "enrich_fallback": {"model": SONNET, "prompt": "enrich_v1", "thinking": {"type": "between_tools"}, "effort": "low",
                        "max_tokens": 4000},
    "verify": {"model": SONNET, "prompt": "verify_v1", "thinking": {"type": "between_tools"}, "effort": "low",
               "max_tokens": 8000},
    "group": {"model": HAIKU, "prompt": "group_v1", "thinking": {"type": "disabled"}, "effort": "low",
              "max_tokens": 4000},
    "memo": {"model": SONNET, "prompt": "memo_v2", "thinking": {"type": "adaptive"}, "effort": "medium",
             "max_tokens": 12000},
}


def label_config(role="enrich"):
    r = ROLES[role]
    think = r["thinking"]["type"]
    fake = "FAKE-DRYRUN+" if os.environ.get("PIPELINE_FAKE_LLM") == "1" else ""
    return f"{fake}{r['model']}+thinking-{think}+effort-{r['effort']}+{r['prompt']}+{SCHEMA_VERSION}+{TAXONOMY_VERSION}"


# Limits and controls (editable; also overridable on the CLI).
BATCH_SIZE = 50                 # contract maximum per enrichment request
VERIFY_BATCH_SIZE = 25
DEFAULT_SPEND_CAP_USD = 15.00   # whole-run cap enforced by the ledger
DEFAULT_WORKERS = 1
MAX_WORKERS = 8
FALLBACK_FRACTION_CAP = 0.02    # at most 2% of nonempty records may go to the Sonnet fallback
INVALID_OUTPUT_RETRIES = 1      # contract: retry invalid output once
TRANSIENT_RETRIES = 4
MAX_OUTAGE_S = int(os.environ.get("PIPELINE_MAX_OUTAGE_S", 1800))            # continuous network/API outage tolerated before a graceful stop
BACKUP_EVERY_S = 600            # snapshot of the run state DB at least this often...
BACKUP_EVERY_N = 5000           # ...or whenever this many more reviews have completed, whichever comes first           # 429/5xx/connection: bounded exponential backoff with jitter
VERIFY_SAMPLE_FRACTION = 0.01
VERIFY_SEED = "verify-v1"
SHORT_TEXT_QUOTE_LIMIT = 300    # deterministic quote fallback: whole text when this short

# Prices in USD per million tokens. Source + date recorded in cost/rates.csv.
PRICES = {
    HAIKU: {"input": 0.10, "cache_write": 0.125, "cache_read": 0.01, "output": 0.50},
    SONNET: {"input": 2.00, "cache_write": 2.50, "cache_read": 0.20, "output": 10.00},
}
PRICE_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICE_CHECKED = "2026-10-13"


def cost_usd(model, usage):
    p = PRICES[model]
    return (usage.get("input_tokens", 0) * p["input"] + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"] + usage.get("output_tokens", 0) * p["output"]) / 1e6
