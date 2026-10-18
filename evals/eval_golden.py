"""Golden-50 evaluation (offline; no model calls). Human labels are read ONLY here, never by any model role.

    python evals/eval_golden.py --run-id golden-eval        # predictions from runs/<id>/enriched.jsonl or state DB

Outputs evals/golden/: per_case.csv, results.json, confusion_topic.csv, confusion_intent.csv, gold_benchmark.json
(check_submission.py --gold format) and disagreements.md (filled with the automated part of the error analysis).
"""
import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pipeline.labels import INTENTS, TOPICS  # noqa: E402

G = ROOT / "evals" / "golden"
SENT_TOL = 0.5  # predeclared tolerance


def parse_alts(s):
    out = defaultdict(set)
    for part in (s or "").replace(",", ";").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            k, v = k.strip().lower(), v.strip().lower()
            if k in ("topic", "intent", "severity") and v:
                out[k].add(int(v) if k == "severity" else v)
    return out


def load_gold(path):
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8-sig", newline="")))
    gold = {}
    for r in rows:
        alts = parse_alts(r.get("acceptable_alternatives"))
        g = {"review_id": r["review_id"], "review_text": r["review_text"], "topic": r["topic"].strip(),
             "intent": r["intent"].strip(), "severity": int(r["severity"]), "sentiment": float(r["sentiment"]),
             "entities": json.loads(r["entities"] or "[]") if r["entities"].strip().startswith("[") else
             [e.strip() for e in r["entities"].split(",") if e.strip()],
             "evidence_quote": r["evidence_quote"], "needs_review": str(r["needs_review"]).lower() == "true",
             "ambiguous": str(r.get("ambiguous", "")).lower() == "true", "notes": r.get("notes", "")}
        assert g["topic"] in TOPICS and g["intent"] in INTENTS and 1 <= g["severity"] <= 5, r["review_id"]
        g["accepted"] = {"topic": {g["topic"]} | alts["topic"], "intent": {g["intent"]} | alts["intent"],
                         "severity": {g["severity"]} | alts["severity"]}
        gold[g["review_id"]] = g
    return gold


def load_predictions(run_id):
    rd = ROOT / "runs" / run_id
    if (rd / "enriched.jsonl").exists():
        preds = {}
        for line in (rd / "enriched.jsonl").open(encoding="utf-8"):
            r = json.loads(line)
            preds[r["review_id"]] = r
        return preds
    from pipeline.cli import open_store
    from pipeline.export import record_rows
    return {r["review_id"]: r for r in record_rows(open_store(run_id)) if r["status"] == "completed"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-id", required=True)
    p.add_argument("--gold", default=str(G / "golden_50_labeled.csv"))
    a = p.parse_args()
    gold, preds = load_gold(a.gold), load_predictions(a.run_id)
    cases, c = [], Counter()
    conf = {"topic": Counter(), "intent": Counter()}
    per_topic = defaultdict(Counter)
    for rid, g in gold.items():
        pr = preds.get(rid)
        row = {"review_id": rid, "review_text": g["review_text"], "ambiguous": g["ambiguous"],
               "gold_topic": g["topic"], "gold_intent": g["intent"], "gold_severity": g["severity"],
               "gold_sentiment": g["sentiment"], "gold_needs_review": g["needs_review"],
               "accepted_topic": "|".join(sorted(g["accepted"]["topic"])),
               "accepted_intent": "|".join(sorted(g["accepted"]["intent"])),
               "accepted_severity": "|".join(map(str, sorted(g["accepted"]["severity"])))}
        if not pr:
            row.update(pred_status="missing", topic_ok=False, intent_ok=False, severity_exact=False)
            c["missing"] += 1
        else:
            tok = pr["topic"] in g["accepted"]["topic"]
            iok = pr["intent"] in g["accepted"]["intent"]
            sok = pr["severity"] in g["accepted"]["severity"]
            sev_d = min(abs(pr["severity"] - s) for s in g["accepted"]["severity"])
            sent_d = abs(pr["sentiment"] - g["sentiment"])
            quote_sub = pr["evidence_quote"] in g["review_text"]
            unsupported_ents = [e for e in pr["entities"] if e.lower() not in g["review_text"].lower()]
            row.update(pred_status="completed", pred_topic=pr["topic"], pred_subtopic=pr.get("subtopic"),
                       pred_intent=pr["intent"], pred_severity=pr["severity"], pred_sentiment=pr["sentiment"],
                       pred_needs_review=pr["needs_review"], pred_quote=pr["evidence_quote"],
                       pred_entities=json.dumps(pr["entities"], ensure_ascii=False),
                       topic_ok=tok, intent_ok=iok, severity_exact=sok, severity_abs_err=sev_d,
                       sentiment_abs_err=round(sent_d, 3), sentiment_within_tol=sent_d <= SENT_TOL,
                       quote_exact_substring=quote_sub, unsupported_entities=json.dumps(unsupported_ents, ensure_ascii=False),
                       quote_supports_label="", label_config=pr["label_config"])
            c["topic"] += tok; c["intent"] += iok; c["sev"] += sok; c["sev_err"] += sev_d; c["sent_err"] += sent_d
            c["sent_tol"] += sent_d <= SENT_TOL; c["quote"] += quote_sub; c["ents_unsupported"] += bool(unsupported_ents)
            c["joint"] += tok and iok and sok; c["n_pred"] += 1
            c["nr_tp"] += pr["needs_review"] and g["needs_review"]; c["nr_fp"] += pr["needs_review"] and not g["needs_review"]
            c["nr_fn"] += (not pr["needs_review"]) and g["needs_review"]
            conf["topic"][(g["topic"], pr["topic"])] += 1
            conf["intent"][(g["intent"], pr["intent"])] += 1
            per_topic[g["topic"]]["gold"] += 1
            per_topic[pr["topic"]]["pred"] += 1
            per_topic[g["topic"]]["correct"] += tok
        c["ambiguous"] += g["ambiguous"]
        cases.append(row)
    n = len(gold)

    def f1(label, field):
        tp = conf[field][(label, label)]
        sup = sum(v for (gl, _), v in conf[field].items() if gl == label)
        pred = sum(v for (_, pl), v in conf[field].items() if pl == label)
        return {"support": sup, "predicted": pred, "f1": round(2 * tp / (sup + pred), 4) if sup + pred else None}

    res = {"run_id": a.run_id, "n": n, "missing_predictions": c["missing"], "ambiguous_cases": c["ambiguous"],
           "note": "Missing predictions count as incorrect. Accepted alternatives only for cases marked ambiguous by the labeler.",
           "agreement": {"topic": round(c["topic"] / n, 4), "intent": round(c["intent"] / n, 4),
                         "severity_exact": round(c["sev"] / n, 4), "joint_topic_intent_severity": round(c["joint"] / n, 4)},
           "severity_mae": round(c["sev_err"] / max(1, c["n_pred"]), 4),
           "sentiment_mae": round(c["sent_err"] / max(1, c["n_pred"]), 4),
           "sentiment_within_tolerance": round(c["sent_tol"] / max(1, c["n_pred"]), 4), "sentiment_tolerance": SENT_TOL,
           "evidence_quote_exact_substring_rate": round(c["quote"] / max(1, c["n_pred"]), 4),
           "records_with_unsupported_entities": c["ents_unsupported"],
           "needs_review_as_prediction": {"tp": c["nr_tp"], "fp": c["nr_fp"], "fn": c["nr_fn"],
                                          "gold_positive": sum(g["needs_review"] for g in gold.values())},
           "per_topic": {t: dict(per_topic[t]) for t in TOPICS},
           "topic_f1": {t: f1(t, "topic") for t in TOPICS}, "intent_f1": {i: f1(i, "intent") for i in INTENTS}}
    vals = [v["f1"] for v in res["topic_f1"].values() if v["f1"] is not None]
    res["topic_macro_f1_observed_labels"] = round(sum(vals) / len(vals), 4) if vals else None
    (G / "results.json").write_text(json.dumps(res, indent=2))
    cols = sorted({k for r in cases for k in r}, key=lambda k: list(cases[0]).index(k) if k in cases[0] else 99)
    with (G / "per_case.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(dict.fromkeys([*cases[0].keys(), *[k for r in cases for k in r]])), lineterminator="\n")
        w.writeheader()
        w.writerows(cases)
    for field, labels in (("topic", TOPICS), ("intent", INTENTS)):
        with (G / f"confusion_{field}.csv").open("w", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow([f"gold\\pred"] + list(labels))
            for gl in labels:
                w.writerow([gl] + [conf[field][(gl, pl)] for pl in labels])
    bench = {"version": "golden50-human-v1", "cases": [
        {"review_id": g["review_id"], "status": "approved", "reviewer": "student",
         "accepted_topic": sorted(g["accepted"]["topic"]), "accepted_intent": sorted(g["accepted"]["intent"]),
         "accepted_severity": sorted(g["accepted"]["severity"])} for g in gold.values()]}
    (G / "gold_benchmark.json").write_text(json.dumps(bench, indent=2))
    dis = [r for r in cases if not (r.get("topic_ok") and r.get("intent_ok") and r.get("severity_exact"))]
    lines = [f"# Golden-50 disagreements (run `{a.run_id}`)", "",
             f"{len(dis)} of {n} cases differ on topic, intent or severity. Automated listing; analysis notes follow each case.", ""]
    for r in dis:
        lines += [f"### {r['review_id']}", f"> {r['review_text']}", "",
                  f"- gold: {r['gold_topic']} / {r['gold_intent']} / sev {r['gold_severity']}"
                  + (" (ambiguous)" if r["ambiguous"] else ""),
                  f"- predicted: {r.get('pred_topic')} / {r.get('pred_intent')} / sev {r.get('pred_severity')} — quote: \"{r.get('pred_quote')}\"",
                  "- analysis: _TODO_", ""]
    (G / "disagreements.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("n", "agreement", "severity_mae", "sentiment_mae", "ambiguous_cases",
                                           "missing_predictions", "needs_review_as_prediction")}, indent=2))


if __name__ == "__main__":
    main()
