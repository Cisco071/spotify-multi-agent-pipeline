"""Summarize a human spot-check (offline). Writes evals/spotcheck/<run>/analysis.json."""
import argparse
import collections
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F = ["topic", "intent", "severity", "quote"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    a = p.parse_args()
    d = ROOT / "evals" / "spotcheck" / a.run_id
    dec = json.loads((d / "decisions.json").read_text())["decisions"]
    sample = json.loads((d / "sample.json").read_text())
    items = {i["review_id"]: i for i in sample["items"]}
    done = [k for k, v in dec.items() if all(f in v and "ok" in v[f] for f in F)]
    per_field = {f: sum(dec[k][f]["ok"] for k in done) for f in F}
    per_stratum = collections.defaultdict(lambda: collections.Counter())
    errors = []
    for k in done:
        allok = all(dec[k][f]["ok"] for f in F)
        for w in items[k]["why"]:
            per_stratum[w]["n"] += 1
            per_stratum[w]["all_ok"] += allok
        if not allok:
            errors.append({"review_id": k, "text": items[k]["text"], "why_sampled": items[k]["why"],
                           "pred": items[k]["pred"], "human": {f: dec[k][f] for f in F}, "note": dec[k].get("note", "")})
    out = {"run_id": a.run_id, "label_config": sample["meta"]["label_config"], "sample_rule": sample["meta"]["sample_rule"],
           "reviewed": len(done), "sampled": len(items),
           "accepted_by_field": {f: f"{per_field[f]}/{len(done)}" for f in F},
           "records_fully_accepted": sum(all(dec[k][f]["ok"] for f in F) for k in done),
           "per_stratum": {k: dict(v) for k, v in per_stratum.items()}, "errors": errors,
           "caveat": "Reviewer saw the model's labels (spot-check, not blind); acceptance is an upper bound on accuracy. "
                     "The blind measure is the golden-50 evaluation."}
    (d / "analysis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("reviewed", "accepted_by_field", "records_fully_accepted", "per_stratum")}, indent=2))


if __name__ == "__main__":
    main()
