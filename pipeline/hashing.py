"""Row hashing identical to check_submission.py (canonical compact JSON of six source strings)."""
import csv
import hashlib
import json
from pathlib import Path

FIELDS = ("review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def row_sha(row):
    # Strings preserved exactly: no trimming, translation or Unicode normalization.
    return hashlib.sha256(canonical([row[k] for k in FIELDS]).encode("utf-8")).hexdigest()


def text_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def csv_rows(path):
    """Strict multiline-safe CSV reader (same rules as the course checker)."""
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, strict=True)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("Missing or duplicate CSV headers")
        missing = [k for k in FIELDS if k not in reader.fieldnames]
        if missing:
            raise ValueError(f"CSV lacks required fields: {missing}")
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError("Malformed CSV row")
            yield row
