"""Stage 5 - Rank (code only, no model). Deterministic from saved records + membership.

priority(issue) = complaint_count x mean_severity = severity_sum (exact integer arithmetic).
Order: descending priority_score, then ascending issue_id. Means: 6 decimals, decimal half-up.
"""
import csv
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

COLUMNS = ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"]


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def compute_ranking(records, membership):
    """records: {review_id: record with intent, severity, status}; membership: iterable of (issue_id, review_id)."""
    members = defaultdict(list)
    seen = set()
    for iid, rid in membership:
        if (iid, rid) in seen:
            raise ValueError(f"duplicate membership {(iid, rid)}")
        seen.add((iid, rid))
        rec = records[rid]
        if rec["status"] != "completed" or rec["intent"] not in ("complaint", "cancellation"):
            raise ValueError(f"non-complaint in membership: {rid}")
        members[iid].append(int(rec["severity"]))
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v),
             "mean_severity": mean_string(sum(v), len(v)), "priority_score": sum(v)} for iid, v in members.items()]
    rows.sort(key=lambda r: (-r["priority_score"], r["issue_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def write_ranking(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: str(r[k]) for k in COLUMNS})


def read_records_jsonl(path):
    import gzip
    path = Path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as f:
        return {r["review_id"]: r for r in map(json.loads, filter(str.strip, f))}


def read_membership(path):
    with Path(path).open(encoding="utf-8", newline="") as f:
        return [(r["issue_id"], r["review_id"]) for r in csv.DictReader(f)]


def rerank_offline(records_path, membership_path, out_path):
    """Regenerate ranking.csv from saved outputs only (no model calls, no API key)."""
    rows = compute_ranking(read_records_jsonl(records_path), read_membership(membership_path))
    write_ranking(rows, out_path)
    return rows
