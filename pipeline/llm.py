"""Anthropic client wrapper used by every model role.

* model allowlist, structured JSON output, bounded transient retries (exponential backoff + jitter)
* reserves spend in the shared ledger before each attempt, logs EVERY attempt (success or failure) to the store
"""
import json
import os
import random
import threading
import time
import uuid

from dotenv import load_dotenv

from . import config
from .ledger import BudgetExceeded

load_dotenv(config.ROOT / ".env")

_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        if os.environ.get("PIPELINE_FAKE_LLM") == "1":
            from .fake_llm import FakeAnthropic
            _client = FakeAnthropic()
            return _client
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY is not set (copy .env.example to .env). Offline commands need no key.")
        _client = anthropic.Anthropic(max_retries=0, timeout=180)  # retries are ours, so every attempt is logged
    return _client


def prompt_text(name):
    return (config.PROMPTS / f"{name}.md").read_text(encoding="utf-8")


def est_tokens(text):
    return int(len(text) / 3.2) + 50


_journaled = 0


def _crash_hook():
    """TEST ONLY: PIPELINE_CRASH_AFTER_JOURNAL=N hard-kills the process right after the Nth response is journaled
    (before results are committed) to prove crash recovery. Never set in real runs."""
    global _journaled
    n = os.environ.get("PIPELINE_CRASH_AFTER_JOURNAL")
    if n:
        _journaled += 1
        if _journaled >= int(n):
            os._exit(9)


class CallFailed(Exception):
    pass


class TransientExhausted(CallFailed):
    """Network/rate-limit/server errors outlasted the retry budget: NOT the records' fault.
    Callers must leave the records pending (no attempt counted, no fallback, no quarantine)."""


def call(store, ledger, *, role_key, role, user_content, schema, review_ids, phase, invocation, label_config,
         attempt_tag=0):
    """One logical model call with bounded transient retries. Returns (parsed_json, call_record).
    Raises CallFailed after the retry budget, BudgetExceeded when the ledger refuses admission."""
    import anthropic
    r = config.ROLES[role_key]
    model = r["model"]
    if os.environ.get("PIPELINE_FAKE_LLM") == "1":
        model = "fake-" + model  # never confused with real usage
    if model.removeprefix("fake-") not in config.ALLOWED_MODELS:
        raise ValueError(f"model {model} not in allowlist")
    system = prompt_text(r["prompt"])
    settings = {"thinking": r["thinking"], "effort": r["effort"], "max_tokens": r["max_tokens"], "prompt": r["prompt"],
                "schema": config.SCHEMA_VERSION, "agent": threading.current_thread().name}
    kwargs = dict(model=model, max_tokens=r["max_tokens"],
                  system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                  messages=[{"role": "user", "content": user_content}],
                  thinking=r["thinking"], output_config={"effort": r["effort"],
                                                          "format": {"type": "json_schema", "schema": schema}})
    pmodel = model.removeprefix("fake-")
    reserve = ledger.worst_case(pmodel, est_tokens(system + user_content), r["max_tokens"])
    last_err = None
    for t in range(config.TRANSIENT_RETRIES + 1):
        ledger.reserve(reserve)  # may raise BudgetExceeded -> caller stops admitting work
        rec = {"request_id": f"local-{uuid.uuid4().hex[:16]}", "invocation": invocation, "role": role, "model": model,
               "phase": phase, "review_ids": review_ids, "label_config": label_config, "started_at": time.time(),
               "attempt": attempt_tag * 10 + t, "settings": settings, "input_tokens": 0, "output_tokens": 0,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0, "cost_usd": 0.0}
        t0 = time.monotonic()
        store.mark_inflight(rec)  # journal BEFORE sending
        try:
            resp = client().messages.create(**kwargs)
            u = resp.usage
            usage = {"input_tokens": u.input_tokens or 0, "output_tokens": u.output_tokens or 0,
                     "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                     "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
            cost = config.cost_usd(pmodel, usage)
            ledger.settle(reserve, cost)
            rec.update(usage, cost_usd=cost, latency_ms=int((time.monotonic() - t0) * 1000), stop_reason=resp.stop_reason,
                       provider_request_id=getattr(resp, "_request_id", None) or resp.id)
            text = "".join(b.text for b in resp.content if b.type == "text")
            store.journal_response(rec, text)  # paid response is durable before anything else happens
            _crash_hook()
            if resp.stop_reason in ("refusal", "max_tokens"):
                rec.update(outcome="failed", error=f"stop_reason={resp.stop_reason}")
                store.log_call(rec)
                store.mark_consumed(rec["request_id"])
                raise CallFailed(rec["error"])
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                rec.update(outcome="failed", error=f"invalid_json: {e}")
                store.log_call(rec)
                store.mark_consumed(rec["request_id"])
                raise CallFailed(rec["error"])
            rec["outcome"] = "succeeded"
            return data, rec  # caller logs the successful call in the same transaction as its results
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError,
                anthropic.APITimeoutError) as e:
            # Unknown/transient outcome: keep the reservation as a charged estimate for timeouts only.
            charged = reserve if isinstance(e, anthropic.APITimeoutError) else 0.0
            ledger.settle(reserve, charged)
            rec.update(outcome="failed", error=f"{type(e).__name__}: {str(e)[:200]}", cost_usd=charged,
                       latency_ms=int((time.monotonic() - t0) * 1000),
                       settings={**settings, "cost_is_estimate": bool(charged)})
            store.log_call(rec)
            store.clear_inflight(rec["request_id"])
            last_err = e
            retry_after = None
            if isinstance(e, anthropic.RateLimitError):
                try:
                    retry_after = float(e.response.headers.get("retry-after"))
                except (TypeError, ValueError, AttributeError):
                    pass
            delay = retry_after or min(60, 2 ** t) + random.uniform(0, 1)
            time.sleep(delay)
        except anthropic.APIStatusError as e:  # 4xx: not retryable
            ledger.settle(reserve, 0.0)
            rec.update(outcome="failed", error=f"{e.status_code}: {str(e)[:300]}",
                       latency_ms=int((time.monotonic() - t0) * 1000))
            store.log_call(rec)
            store.clear_inflight(rec["request_id"])
            raise CallFailed(rec["error"])
        except CallFailed:
            raise
    raise TransientExhausted(f"transient retries exhausted: {last_err}")
