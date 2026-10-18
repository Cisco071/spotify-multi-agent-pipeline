"""Stage 1 - Prepare (code only, no model).

* profile_full(): reads every row of a CSV, deterministic profile + duplicate-text and quality stats.
* build_subset(): declared deterministic >=100k analysis subset of the full file (exact original rows).
* load_run(): loads an input CSV into run state, hashes rows, quarantines empty texts, queues the rest.
"""
import csv
import hashlib
import json
import time
from collections import Counter
from pathlib import Path

from ..hashing import FIELDS, csv_rows, file_sha, row_sha, text_sha

SUBSET_SEED = "berkeley-fall-2026-assignment-5-v1"


def profile_full(path):
    t0 = time.time()
    ids, months, ratings, texts = set(), Counter(), Counter(), Counter()
    counts = Counter(records=0, duplicate_review_ids=0, empty_review_text=0, missing_app_version=0)
    content = hashlib.sha256()
    first = last = None
    lengths = []
    empty_ids, bad_rating = [], 0
    for row in csv_rows(path):
        digest = row_sha(row)
        content.update(bytes.fromhex(digest))
        counts["records"] += 1
        counts["duplicate_review_ids"] += row["review_id"] in ids
        ids.add(row["review_id"])
        empty = not row["review_text"].strip()
        counts["empty_review_text"] += empty
        if empty:
            empty_ids.append(row["review_id"])
        else:
            texts[row["review_text"]] += 1
            lengths.append(len(row["review_text"]))
        counts["missing_app_version"] += not row["app_version"].strip()
        months[row["review_timestamp"][:7]] += 1
        ratings[row["review_rating"]] += 1
        bad_rating += row["review_rating"] not in {"1", "2", "3", "4", "5"}
        ts = row["review_timestamp"]
        first = min(first, ts) if first else ts
        last = max(last, ts) if last else ts
    lengths.sort()
    dup_rows = sum(c for c in texts.values() if c > 1)
    return {
        # Same keys/values as check_submission.py profile (that file is written separately as grading/ingestion.json)
        "contract_profile": {"file_sha256": file_sha(path), "parsed_rows_sha256": content.hexdigest(), "counts": dict(counts),
                             "reviews_by_month": dict(months), "reviews_by_rating": dict(ratings),
                             "first_review": first, "last_review": last},
        "file_bytes": Path(path).stat().st_size,
        "nonempty_rows": counts["records"] - counts["empty_review_text"],
        "distinct_nonempty_texts": len(texts),
        "rows_with_duplicated_text": dup_rows,
        "exact_text_reuse_rows": counts["records"] - counts["empty_review_text"] - len(texts),
        "top_duplicate_texts": [{"text": t[:80], "rows": c} for t, c in texts.most_common(15)],
        "empty_review_ids": empty_ids,
        "invalid_ratings": bad_rating,
        "text_length_chars": {"min": lengths[0], "p50": lengths[len(lengths) // 2], "p90": lengths[int(len(lengths) * .9)],
                              "p99": lengths[int(len(lengths) * .99)], "max": lengths[-1],
                              "mean": round(sum(lengths) / len(lengths), 2)} if lengths else None,
        "profile_seconds": round(time.time() - t0, 2),
    }


def _rank_key(rid):
    return hashlib.sha256(f"{SUBSET_SEED}:{rid}".encode()).hexdigest()


def build_subset(full, out, nonempty_target, include_files=()):
    """Exact rows of `full`, in source order: all empty texts + every ID in include_files + lowest-hash nonempty
    rows until `nonempty_target` nonempty rows are selected. Duplicated texts are kept (cache reuse is tested)."""
    must = set()
    for f in include_files:
        must |= {r["review_id"] for r in csv_rows(f)}
    rows_meta = []
    for i, row in enumerate(csv_rows(full)):
        rows_meta.append((row["review_id"], bool(row["review_text"].strip())))
    chosen = {rid for rid, ne in rows_meta if not ne} | must
    chosen_nonempty = sum(1 for rid, ne in rows_meta if ne and rid in chosen)
    for rid in sorted((rid for rid, ne in rows_meta if ne and rid not in chosen), key=_rank_key):
        if chosen_nonempty >= nonempty_target:
            break
        chosen.add(rid)
        chosen_nonempty += 1
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with Path(full).open(encoding="utf-8-sig", newline="") as fin, out.open("w", encoding="utf-8", newline="") as fout:
        reader = csv.DictReader(fin, strict=True)
        writer = csv.DictWriter(fout, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        n = 0
        for row in reader:
            if row["review_id"] in chosen:
                writer.writerow(row)
                n += 1
    meta = {"subset_file": str(out.name), "rows": n, "nonempty": chosen_nonempty, "empty": n - chosen_nonempty,
            "sha256": file_sha(out), "seed": SUBSET_SEED, "method": __doc__.strip().splitlines()[0],
            "rule": build_subset.__doc__, "forced_includes": [Path(f).name for f in include_files],
            "source_sha256": file_sha(full),
            "distinct_nonempty_texts": len({r["review_text"] for r in csv_rows(out) if r["review_text"].strip()})}
    # Verify every subset row is byte-identical in hash to the full file row.
    full_hash = {r["review_id"]: row_sha(r) for r in csv_rows(full) if r["review_id"] in chosen}
    assert all(row_sha(r) == full_hash[r["review_id"]] for r in csv_rows(out)), "subset row differs from source"
    out.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def load_run(store, input_csv, synthetic=False):
    """Idempotent: re-loading the same CSV keeps existing results. Empty texts -> quarantined/empty_review_text."""
    inserted = 0
    with store.tx() as db:
        existing = {r[0] for r in db.execute("SELECT review_id FROM reviews")}
        base = len(existing)
        seen = set()
        for i, row in enumerate(csv_rows(input_csv)):
            rid = row["review_id"]
            if rid in seen:
                raise ValueError(f"duplicate review_id in input: {rid}")
            seen.add(rid)
            if rid in existing:
                continue
            empty = not row["review_text"].strip()
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (rid, base + i, row_sha(row), text_sha(row["review_text"]), row["review_text"],
                        row["review_rating"], row["review_likes"], row["app_version"], row["review_timestamp"],
                        int(empty), int(synthetic)))
            if empty:
                db.execute("INSERT INTO results (review_id, status, reason, attempts, updated_at) VALUES (?,?,?,?,?)",
                           (rid, "quarantined", "empty_review_text", 0, time.time()))
            else:
                db.execute("INSERT INTO results (review_id, status, attempts, updated_at) VALUES (?,?,?,?)",
                           (rid, "pending", 0, time.time()))
            inserted += 1
    total = store.one("SELECT COUNT(*) n FROM reviews")["n"]
    nonempty = store.one("SELECT COUNT(*) n FROM reviews WHERE is_empty=0")["n"]
    distinct = store.one("SELECT COUNT(DISTINCT text_sha) n FROM reviews WHERE is_empty=0")["n"]
    return {"input": str(input_csv), "input_sha256": file_sha(input_csv), "rows": total, "inserted": inserted,
            "nonempty": nonempty, "empty_quarantined": total - nonempty, "distinct_nonempty_texts": distinct}
