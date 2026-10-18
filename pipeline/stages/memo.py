"""Stage 6 - Recommend.

Code builds aggregates + a bounded evidence pack (never raw data); the MEMO WRITER (claude-sonnet-5-5) drafts a
structured memo; code validates every claim value against the ranking, every cited claim/review ID, and rejects
unsupported numbers. One corrective retry with the validation errors, then fail loudly.
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from .. import config, llm
from ..labels import TOPICS
from .rank import mean_string

AREAS = {"access": ("access",), "usability": ("usability",), "playback": ("playback", "downloads"),
         "billing_support": ("billing", "support")}
AREA_NOTE = ("Area roll-up declared by code: playback includes downloads (offline playback); billing_support combines "
             "billing and support; catalog and other are reported but are outside the four candidate areas.")
TOP_N = 15
EVIDENCE_ISSUES = 8
EVIDENCE_PER_ISSUE = 4

MEMO_SCHEMA = {
    "type": "object",
    "properties": {
        "recommended_area": {"type": "string", "enum": list(AREAS)},
        "headline": {"type": "string"},
        "memo_markdown": {"type": "string"},
        "recommendations": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "detail": {"type": "string"},
            "issue_ids": {"type": "array", "items": {"type": "string"}},
            "claim_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["title", "detail", "issue_ids", "claim_ids"], "additionalProperties": False}},
        "alternatives": {"type": "array", "items": {"type": "object", "properties": {
            "area": {"type": "string"}, "assessment": {"type": "string"}},
            "required": ["area", "assessment"], "additionalProperties": False}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "claims": {"type": "array", "items": {"type": "object", "properties": {
            "claim_id": {"type": "string"}, "issue_id": {"type": "string"},
            "metric": {"type": "string", "enum": ["complaint_count", "severity_sum", "mean_severity", "priority_score"]},
            "value": {"type": "string"}},
            "required": ["claim_id", "issue_id", "metric", "value"], "additionalProperties": False}},
        "cited_review_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["recommended_area", "headline", "memo_markdown", "recommendations", "alternatives", "limitations",
                 "claims", "cited_review_ids"],
    "additionalProperties": False,
}


def aggregates(store):
    recs = store.q("""SELECT r.review_id, r.status, r.topic, r.intent, r.severity, r.needs_review, rv.review_timestamp
                      FROM results r JOIN reviews rv USING(review_id) WHERE rv.synthetic=0""")
    total = len(recs)
    status = Counter(r["status"] for r in recs)
    completed = [r for r in recs if r["status"] == "completed"]
    comp = [r for r in completed if r["intent"] in ("complaint", "cancellation")]
    by_topic = {}
    for t in TOPICS:
        tr = [r for r in completed if r["topic"] == t]
        tc = [r for r in tr if r["intent"] in ("complaint", "cancellation")]
        sev = sum(r["severity"] for r in tc)
        by_topic[t] = {"completed_records": len(tr), "complaint_count": len(tc), "severity_sum": sev,
                       "mean_severity": mean_string(sev, len(tc)) if tc else None,
                       "cancellation_count": sum(r["intent"] == "cancellation" for r in tr),
                       "severity_4_plus": sum(r["severity"] >= 4 for r in tc),
                       "intents": dict(Counter(r["intent"] for r in tr))}
    by_area = {}
    for a, ts in AREAS.items():
        cc = sum(by_topic[t]["complaint_count"] for t in ts)
        ss = sum(by_topic[t]["severity_sum"] for t in ts)
        by_area[a] = {"topics": list(ts), "complaint_count": cc, "severity_sum": ss,
                      "mean_severity": mean_string(ss, cc) if cc else None,
                      "cancellation_count": sum(by_topic[t]["cancellation_count"] for t in ts),
                      "severity_4_plus": sum(by_topic[t]["severity_4_plus"] for t in ts),
                      "share_of_all_complaints": f"{100 * cc / len(comp):.1f}%" if comp else None}
    months = defaultdict(lambda: Counter())
    for r in completed:
        m = r["review_timestamp"][:7]
        months[m]["completed"] += 1
        if r["intent"] in ("complaint", "cancellation"):
            months[m]["complaints"] += 1
            for a, ts in AREAS.items():
                if r["topic"] in ts:
                    months[m][a] += 1
    monthly = {m: {"completed": c["completed"], "complaints": c["complaints"],
                   "complaint_share": f"{100 * c['complaints'] / c['completed']:.1f}%",
                   **{f"{a}_complaints": c[a] for a in AREAS}} for m, c in sorted(months.items())}
    return {"scope": {"records": total, "completed": status["completed"], "quarantined": status["quarantined"],
                      "pending": status["pending"], "complaint_or_cancellation_records": len(comp),
                      "needs_review_flagged": sum(bool(r["needs_review"]) for r in completed),
                      "cache_reused_records": store.one("SELECT COUNT(*) n FROM results WHERE cache_source_id IS NOT NULL")["n"]},
            "by_topic": by_topic, "by_area": by_area, "area_note": AREA_NOTE,
            "monthly": monthly, "monthly_note": "First (2022-05) and last (2023-11) calendar months are partial."}


def evidence(issues, ranking):
    by_id = {i["issue_id"]: i for i in issues}
    pack = []
    for row in ranking[:EVIDENCE_ISSUES]:
        iss = by_id[row["issue_id"]]
        quotes = [p for p in iss["evidence_pack"] if p["review_id"] not in iss.get("misfit_review_ids", [])]
        pack.append({"issue_id": row["issue_id"], "title": iss["title"], "description": iss["description"],
                     "examples": quotes[:EVIDENCE_PER_ISSUE]})
    return pack


def _numbers_in(obj, acc):
    if isinstance(obj, dict):
        for k, v in obj.items():
            _numbers_in(k, acc)
            _numbers_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_in(v, acc)
    elif obj is not None and not isinstance(obj, bool):
        for n in re.findall(r"\d+(?:\.\d+)?", str(obj)):
            acc.add(n.lstrip("0") or "0")
    return acc


def unsupported_numbers(text, inputs):
    """Numbers in prose must exist in the inputs (claim IDs, review IDs, small ordinals and dates excluded)."""
    allowed = _numbers_in(inputs, set())
    text = re.sub(r"\[C\d+\]|[0-9a-f]{8}-[0-9a-f-]{27}|\b20\d\d-\d\d\b", " ", text)
    bad = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        n = raw.replace(",", "").lstrip("0") or "0"
        if n in allowed or (n.isdigit() and int(n) <= 10) or n in ("2022", "2023"):
            continue
        bad.append(raw)
    return sorted(set(bad))


def validate(memo, ranking, pack, inputs=None):
    errors = []
    if inputs is not None:
        prose = memo["memo_markdown"] + " " + memo["headline"] + " " + " ".join(
            r["detail"] for r in memo["recommendations"]) + " " + " ".join(a["assessment"] for a in memo["alternatives"])
        for n in unsupported_numbers(prose, inputs):
            errors.append(f"number {n} in prose does not appear in the inputs (do not compute new numbers)")
    calc = {r["issue_id"]: r for r in ranking}
    claim_ids = set()
    for c in memo["claims"]:
        if not re.fullmatch(r"C\d+", c["claim_id"]) or c["claim_id"] in claim_ids:
            errors.append(f"bad or duplicate claim_id {c['claim_id']}")
        claim_ids.add(c["claim_id"])
        target = calc.get(c["issue_id"])
        if not target:
            errors.append(f"claim {c['claim_id']}: unknown issue_id {c['issue_id']}")
        elif str(target[c["metric"]]) != c["value"]:
            errors.append(f"claim {c['claim_id']}: {c['issue_id']}.{c['metric']} is {target[c['metric']]}, not {c['value']}")
    text = memo["memo_markdown"] + " ".join(r["detail"] for r in memo["recommendations"])
    for cid in set(re.findall(r"\[(C\d+)\]", text)):
        if cid not in claim_ids:
            errors.append(f"prose cites undefined claim {cid}")
    for cid in claim_ids - set(re.findall(r"\[(C\d+)\]", memo["memo_markdown"])):
        errors.append(f"claim {cid} is not cited in memo_markdown")
    pack_ids = {e["review_id"] for p in pack for e in p["examples"]}
    for rid in set(memo["cited_review_ids"]) | set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)):
        if rid not in pack_ids:
            errors.append(f"review id {rid} not in evidence pack")
    for r in memo["recommendations"]:
        for iid in r["issue_ids"]:
            if iid not in calc:
                errors.append(f"recommendation cites unknown issue {iid}")
        for cid in r["claim_ids"]:
            if cid not in claim_ids:
                errors.append(f"recommendation cites undefined claim {cid}")
    return errors


def compact_insights(ins):
    """Bounded slice of insights.json for the memo (no raw reviews beyond the evidence pack)."""
    if not ins:
        return None
    return {"rules": ins["rules"], "comparable_windows": ins["comparable_windows"],
            "anomalies": [{k: a[k] for k in ("month", "series", "value", "share_pct", "expected_share_pct", "ratio",
                                             "partial_month")} | {"top_driver": a["drivers"][0]["issue"] if a["drivers"] else None}
                          for a in ins["anomalies"]],
            "recurring": ins["recurring"], "alt_rankings": ins["alt_rankings"],
            "alt_ranking_table_top10": ins["alt_ranking_table"][:10],
            "events": [{k: e[k] for k in ("id", "month", "title", "matching_anomalies")} for e in ins["events"]["events"]],
            "events_note": ins["events"]["_note"]}


def run_memo(store, ledger, *, invocation, out_dir, ranking, issues, log=print):
    out_dir = Path(out_dir)
    agg = aggregates(store)
    pack = evidence(issues, ranking)
    titles = {i["issue_id"]: i["title"] for i in issues}
    ins_path = out_dir / "insights.json"
    inputs = {"ranking_top": [{**r, "title": titles.get(r["issue_id"], "")} for r in ranking[:TOP_N]],
              "ranking_rule": "priority_score = severity_sum = complaint_count x mean_severity; desc score, asc issue_id",
              "issues_ranked_total": len(ranking), "aggregates": agg, "evidence_pack": pack,
              "insights": compact_insights(json.loads(ins_path.read_text())) if ins_path.exists() else None}
    (out_dir / "memo_inputs.json").write_text(json.dumps(inputs, indent=2, ensure_ascii=False))
    lc = config.label_config("memo")
    user = "<inputs>\n" + json.dumps(inputs, ensure_ascii=False) + "\n</inputs>"
    import hashlib
    input_key = hashlib.sha256((lc + user + llm.prompt_text(config.ROLES["memo"]["prompt"])).encode()).hexdigest()
    prev = out_dir / "memo.json"
    if prev.exists():  # downstream cache: identical inputs + config -> reuse, no model call
        old = json.loads(prev.read_text())
        if old.get("input_key") == input_key and not old.get("validation_errors"):
            log("[memo] inputs unchanged: reusing saved memo (0 calls)")
            return old["memo"], agg
    errors = []
    for attempt in range(2):
        content = user if not errors else (user + "\n\nYour previous draft failed validation. Fix exactly these "
                                           "problems and return the full JSON again:\n- " + "\n- ".join(errors))
        data, rec = llm.call(store, ledger, role_key="memo", role="memo", user_content=content, schema=MEMO_SCHEMA,
                             review_ids=[], phase="initial", invocation=invocation, label_config=lc, attempt_tag=attempt)
        errors = validate(data, ranking, pack, inputs)
        if errors:
            rec["outcome"] = "failed"
            rec["error"] = "validation: " + "; ".join(errors)[:500]
        store.log_call(rec)
        if not errors:
            break
        log(f"[memo] validation failed (attempt {attempt + 1}): {errors[:5]}")
    (out_dir / "memo.json").write_text(json.dumps({"memo": data, "validation_errors": errors, "label_config": lc,
                                                  "input_key": input_key, "request_id": rec["request_id"]},
                                                  indent=2, ensure_ascii=False))
    if errors:
        raise RuntimeError(f"memo failed validation after retry: {errors}")
    return data, agg
