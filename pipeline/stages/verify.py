"""Stage 3 - Verify (model role: VERIFIER, claude-sonnet-5-5; separate prompt; never sees the first prediction).

Code draws a declared deterministic random sample of completed ORIGINAL records, sends only review text, then
compares the two label sets field by field and writes a disagreement report. A planted-error test copy checks
that the comparison logic flags a deliberately wrong label.
"""
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from .. import config, llm
from ..schema import BATCH_SCHEMA, validate_item
from .enrich import _repair_quote, _user_content

SENTIMENT_TOLERANCE = 0.5  # predeclared: |enricher - verifier| <= 0.5 counts as agreement


def draw_sample(store, fraction, seed=config.VERIFY_SEED, minimum=10):
    rows = store.q("""SELECT r.review_id FROM results r JOIN reviews rv USING(review_id)
                      WHERE r.status='completed' AND r.cache_source_id IS NULL AND rv.synthetic=0""")
    ids = sorted((r["review_id"] for r in rows), key=lambda x: hashlib.sha256(f"{seed}:{x}".encode()).hexdigest())
    n = max(minimum, round(len(ids) * fraction)) if ids else 0
    return ids[:n]


def compare(first, second):
    d = {
        "topic": first["topic"] == second["topic"],
        "intent": first["intent"] == second["intent"],
        "severity_exact": first["severity"] == second["severity"],
        "severity_abs_diff": abs(first["severity"] - second["severity"]),
        "sentiment_within_tol": abs(first["sentiment"] - second["sentiment"]) <= SENTIMENT_TOLERANCE,
        "complaint_flag": (first["intent"] in ("complaint", "cancellation")) == (second["intent"] in ("complaint", "cancellation")),
    }
    d["all_core_agree"] = d["topic"] and d["intent"] and d["severity_exact"]
    return d


def summarize(pairs):
    n = len(pairs)
    if not n:
        return {"n": 0}
    c = Counter()
    conf_topic, conf_intent = Counter(), Counter()
    for p in pairs:
        cmp = p["comparison"]
        for k in ("topic", "intent", "severity_exact", "sentiment_within_tol", "complaint_flag", "all_core_agree"):
            c[k] += cmp[k]
        c["sev_abs"] += cmp["severity_abs_diff"]
        conf_topic[f"{p['enricher']['topic']}->{p['verifier']['topic']}"] += 1
        conf_intent[f"{p['enricher']['intent']}->{p['verifier']['intent']}"] += 1
    return {"n": n, "agreement": {k: round(c[k] / n, 4) for k in ("topic", "intent", "severity_exact",
                                                                    "sentiment_within_tol", "complaint_flag", "all_core_agree")},
            "severity_mae": round(c["sev_abs"] / n, 4), "disagreements": n - c["all_core_agree"],
            "topic_pairs": dict(conf_topic.most_common()), "intent_pairs": dict(conf_intent.most_common()),
            "sentiment_tolerance": SENTIMENT_TOLERANCE}


def run_verify(store, ledger, *, invocation, out_dir, fraction=config.VERIFY_SAMPLE_FRACTION, ids=None, log=print,
               minimum=10):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sample = ids if ids is not None else draw_sample(store, fraction, minimum=minimum)
    done_path = out_dir / "verifier_predictions.jsonl"
    done = {}
    if done_path.exists():  # resume: never re-verify the same ID under the same config
        for line in done_path.open(encoding="utf-8"):
            r = json.loads(line)
            if r.get("label_config") == config.label_config("verify"):
                done[r["review_id"]] = r
    todo = [rid for rid in sample if rid not in done]
    texts = {r["review_id"]: r["review_text"] for r in store.q(
        f"SELECT review_id, review_text FROM reviews WHERE review_id IN ({','.join('?' * len(todo))})", todo)} if todo else {}
    lc = config.label_config("verify")
    failures = []
    for i in range(0, len(todo), config.VERIFY_BATCH_SIZE):
        chunk = todo[i:i + config.VERIFY_BATCH_SIZE]
        try:
            data, rec = llm.call(store, ledger, role_key="verify", role="verify",
                                 user_content=_user_content([(r, texts[r]) for r in chunk]), schema=BATCH_SCHEMA,
                                 review_ids=chunk, phase="initial", invocation=invocation, label_config=lc)
        except llm.CallFailed as e:
            failures.append({"review_ids": chunk, "error": str(e)})
            continue
        store.log_call(rec)
        with done_path.open("a", encoding="utf-8") as f:
            for item in data.get("results", []):
                rid = item.get("review_id")
                if rid not in chunk or rid in done:
                    continue
                clean, err = validate_item(item, texts[rid])
                if err in ("quote_not_substring", "empty_quote") and _repair_quote(item, texts[rid]):
                    clean, err = validate_item(item, texts[rid])
                row = {"review_id": rid, "label_config": lc, "request_id": rec["request_id"], "error": err,
                       **({k: clean[k] for k in ("topic", "subtopic", "intent", "sentiment", "severity")} if clean else {})}
                done[rid] = row
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        log(f"[verify] {min(i + len(chunk), len(todo))}/{len(todo)} spent=${ledger.snapshot()['spent_usd']:.4f}")
    first = {r["review_id"]: dict(r) for r in store.q(
        f"SELECT review_id, topic, subtopic, intent, sentiment, severity, evidence_quote FROM results WHERE review_id IN ({','.join('?' * len(sample))})",
        sample)} if sample else {}
    pairs, invalid = [], []
    for rid in sample:
        v = done.get(rid)
        if not v or v.get("error"):
            invalid.append({"review_id": rid, "error": (v or {}).get("error", "not_verified")})
            continue
        pairs.append({"review_id": rid, "review_text": texts.get(rid) or store.one(
            "SELECT review_text FROM reviews WHERE review_id=?", (rid,))["review_text"],
            "enricher": first[rid], "verifier": v, "comparison": compare(first[rid], v)})
    report = {"label_config_verifier": lc, "label_config_enricher": config.label_config("enrich"),
              "sample_rule": f"lowest sha256('{config.VERIFY_SEED}:'+review_id) among completed original records; "
                             f"fraction={fraction}, min={minimum}",
              "sample_size": len(sample), "verified": len(pairs), "verify_failures": invalid + failures,
              "summary": summarize(pairs), "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (out_dir / "verifier_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    with (out_dir / "disagreements.jsonl").open("w", encoding="utf-8") as f:
        for p in pairs:
            if not p["comparison"]["all_core_agree"]:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
    planted = planted_error_test(pairs)
    (out_dir / "planted_error_test.json").write_text(json.dumps(planted, indent=2, ensure_ascii=False))
    return report, planted


def planted_error_test(pairs, k=10):
    """On a SEPARATE test copy: take verified pairs where both roles agreed, deliberately corrupt the enricher label,
    and confirm the comparison flags every planted error. Synthetic; never written back to business records."""
    agreed = [p for p in pairs if p["comparison"]["all_core_agree"]][:k]
    flips = {"topic": lambda r: {**r, "topic": "support" if r["topic"] != "support" else "access"},
             "intent": lambda r: {**r, "intent": "praise" if r["intent"] != "praise" else "complaint"},
             "severity": lambda r: {**r, "severity": 5 if r["severity"] < 4 else 1}}
    cases = []
    for i, p in enumerate(agreed):
        field = list(flips)[i % 3]
        wrong = flips[field](dict(p["enricher"]))
        cmp = compare(wrong, p["verifier"])
        cases.append({"synthetic": True, "review_id": p["review_id"], "planted_field": field,
                      "original": p["enricher"][field], "planted": wrong[field], "verifier": p["verifier"][field],
                      "flagged": not cmp["all_core_agree"]})
    return {"synthetic_test_copy": True, "cases": cases, "planted": len(cases),
            "flagged": sum(c["flagged"] for c in cases),
            "pass": bool(cases) and all(c["flagged"] for c in cases)}
