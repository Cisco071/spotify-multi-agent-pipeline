"""Stage 4 - Group.

Code: deterministic membership - each completed complaint/cancellation record joins exactly one issue,
issue_id = topic.subtopic (from the enricher's fixed taxonomy). Saved to membership.csv so ranking needs no model.
Model (GROUPER, claude-haiku-5-5): names/describes each issue and flags misfits from a bounded evidence pack
(<= PACK_SIZE quotes per issue, <= ISSUES_PER_CALL issues per call). Its output never changes membership.
"""
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from .. import config, llm
from ..labels import COMPLAINT_INTENTS, issue_id

PACK_SIZE = 8
ISSUES_PER_CALL = 10

GROUP_SCHEMA = {
    "type": "object",
    "properties": {"issues": {"type": "array", "items": {
        "type": "object",
        "properties": {"issue_id": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
                       "coherent": {"type": "boolean"}, "misfit_review_ids": {"type": "array", "items": {"type": "string"}},
                       "suggested_split": {"type": "string"}},
        "required": ["issue_id", "title", "description", "coherent", "misfit_review_ids", "suggested_split"],
        "additionalProperties": False}}},
    "required": ["issues"], "additionalProperties": False}


def build_membership(store):
    rows = store.q("""SELECT r.review_id, r.topic, r.subtopic, r.severity, r.evidence_quote FROM results r
                      JOIN reviews rv USING(review_id)
                      WHERE r.status='completed' AND r.intent IN ('complaint','cancellation') AND rv.synthetic=0
                      ORDER BY rv.row_idx""")
    members = defaultdict(list)
    for r in rows:
        members[issue_id(r["topic"], r["subtopic"])].append(dict(r))
    return members


def write_membership(members, path):
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["issue_id", "review_id"])
        for iid in sorted(members):
            for m in members[iid]:
                w.writerow([iid, m["review_id"]])


def evidence_pack(iid, mems, k=PACK_SIZE):
    """Deterministic bounded sample: lowest sha256(issue:review_id), originals preferred (distinct quotes)."""
    ranked = sorted(mems, key=lambda m: hashlib.sha256(f"{iid}:{m['review_id']}".encode()).hexdigest())
    seen, pack = set(), []
    for m in ranked:
        if m["evidence_quote"] in seen:
            continue
        seen.add(m["evidence_quote"])
        pack.append({"review_id": m["review_id"], "severity": m["severity"], "quote": m["evidence_quote"][:300]})
        if len(pack) == k:
            break
    return pack


def run_group(store, ledger, *, invocation, out_dir, log=print):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    members = build_membership(store)
    write_membership(members, out_dir / "membership.csv")
    packs = {iid: evidence_pack(iid, m) for iid, m in members.items()}
    cache_path = out_dir / "group_names.json"
    lc = config.label_config("group")
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    # downstream cache: reuse names when the issue's pack and config are unchanged
    key = lambda iid: hashlib.sha256(json.dumps([lc, packs[iid]], sort_keys=True).encode()).hexdigest()
    todo = [iid for iid in sorted(members) if cache.get(iid, {}).get("key") != key(iid)]
    for i in range(0, len(todo), ISSUES_PER_CALL):
        chunk = todo[i:i + ISSUES_PER_CALL]
        payload = [{"issue_id": iid, "member_count": len(members[iid]), "evidence": packs[iid]} for iid in chunk]
        try:
            data, rec = llm.call(store, ledger, role_key="group", role="group",
                                 user_content="<issues>\n" + json.dumps(payload, ensure_ascii=False) + "\n</issues>",
                                 schema=GROUP_SCHEMA, review_ids=[], phase="initial", invocation=invocation,
                                 label_config=lc)
        except llm.CallFailed as e:
            log(f"[group] naming failed for {chunk}: {e}")
            continue
        rec["inputs"] = [f"evidence_pack:{iid}" for iid in chunk]
        store.log_call({k: v for k, v in rec.items() if k != "inputs"})
        for it in data.get("issues", []):
            iid = it.get("issue_id")
            if iid not in chunk:
                continue  # reject issue IDs the model invented
            pack_ids = {p["review_id"] for p in packs[iid]}
            cache[iid] = {"key": key(iid), "title": it["title"][:80], "description": it["description"][:300],
                          "coherent": it["coherent"],
                          "misfit_review_ids": [x for x in it["misfit_review_ids"] if x in pack_ids],
                          "rejected_ids": [x for x in it["misfit_review_ids"] if x not in pack_ids],
                          "suggested_split": it["suggested_split"][:80], "request_id": rec["request_id"]}
        log(f"[group] named {min(i + len(chunk), len(todo))}/{len(todo)} issues")
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
    issues = []
    for iid in sorted(members):
        meta = cache.get(iid, {})
        topic, sub = iid.split(".", 1)
        issues.append({"issue_id": iid, "topic": topic, "subtopic": sub, "member_count": len(members[iid]),
                       "title": meta.get("title", iid), "description": meta.get("description", ""),
                       "coherent": meta.get("coherent"), "misfit_review_ids": meta.get("misfit_review_ids", []),
                       "suggested_split": meta.get("suggested_split", ""), "evidence_pack": packs[iid],
                       "named_by": meta.get("request_id"),
                       "member_review_ids_file": "membership.csv"})
    (out_dir / "issues.json").write_text(json.dumps(issues, indent=2, ensure_ascii=False))
    return members, issues
