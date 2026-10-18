"""Business insights (code only, no model): monthly series, anomaly detection with drivers, recurring problems,
alternative rankings, comparable-period trend, app-version and severity breakdowns, external event annotations.

Feeds the memo writer and the dashboard.   python -m pipeline.stages.insights --run-id final-100k
Rules (declared): anomaly = full or partial month whose share is >= 2x the median of the previous 3 months AND
>= +1.0 percentage point AND >= 50 reviews. Recurring = complaint share >= 0.5% in a month. Shares use classified
reviews that month as the denominator. Keyword series are regex matches (code), not model labels.
"""
import argparse
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict

from .. import config
from ..labels import TOPICS
from .memo import AREAS
from .trend import compare as comparable_windows

KEYWORDS = {"boycott": r"boycott", "sweden_quran": r"\b(swed\w*|quran|qur.?an|koran)\b", "premium_paywall": r"premium|paywall",
            "update": r"\bupdate", "ads": r"\bads?\b|advert", "lyrics": r"lyric", "shuffle": r"shuffle"}
FULL_MONTHS = ("2022-06", "2023-10")
MIN_COUNT, RATIO, MIN_PP = 50, 2.0, 1.0


def pct(a, b, d=2):
    return round(100 * a / b, d) if b else 0.0


def build(store, top_n=12):
    rows = [dict(r) for r in store.q("""SELECT r.review_id, r.topic, r.subtopic, r.intent, r.severity, r.evidence_quote,
                    rv.review_text, rv.review_likes, rv.app_version, substr(rv.review_timestamp,1,7) m
                    FROM results r JOIN reviews rv USING(review_id) WHERE r.status='completed' AND rv.synthetic=0""")]
    for r in rows:
        r["issue"] = f"{r['topic']}.{r['subtopic']}"
        r["is_c"] = r["intent"] in ("complaint", "cancellation")
    months = sorted({r["m"] for r in rows})
    by_m = defaultdict(list)
    for r in rows:
        by_m[r["m"]].append(r)
    comp = [r for r in rows if r["is_c"]]
    score = Counter()
    for r in comp:
        score[r["issue"]] += r["severity"]
    top_issues = [i for i, _ in score.most_common() if not i.startswith("other.general")][:top_n]

    # ---------------- monthly series
    monthly = []
    for m in months:
        rs = by_m[m]
        n, cs = len(rs), [r for r in rs if r["is_c"]]
        row = {"month": m, "partial": m not in [x for x in months if FULL_MONTHS[0] <= x <= FULL_MONTHS[1]],
               "classified": n, "complaints": len(cs), "complaint_share": pct(len(cs), n),
               "cancellations": sum(r["intent"] == "cancellation" for r in rs),
               "severity_4_plus": sum(r["severity"] >= 4 for r in cs),
               "mean_severity_complaints": round(statistics.mean(r["severity"] for r in cs), 3) if cs else None,
               "areas": {a: sum(r["topic"] in ts for r in cs) for a, ts in AREAS.items()},
               "issues": {i: sum(r["issue"] == i for r in cs) for i in top_issues},
               "boycott_political": sum(r["issue"] == "other.boycott_political" for r in rs),
               "keywords": {k: sum(bool(re.search(p, r["review_text"], re.I)) for r in rs) for k, p in KEYWORDS.items()}}
        monthly.append(row)

    # ---------------- anomalies (expected range = median share of previous 3 months)
    def series():
        yield "volume", "Review volume", lambda row: row["classified"], None
        yield "boycott", "Boycott / political reviews", lambda row: row["boycott_political"], "other.boycott_political"
        for a in AREAS:
            yield f"area:{a}", f"{a} complaints", (lambda a: lambda row: row["areas"][a])(a), None
        for i in top_issues:
            yield f"issue:{i}", f"{i} complaints", (lambda i: lambda row: row["issues"][i])(i), i
    anomalies = []
    for key, label, get, issue in series():
        for k, row in enumerate(monthly):
            if k < 3:
                continue
            prev = monthly[k - 3:k]
            if key == "volume":
                val, base = row["classified"], statistics.median(p["classified"] for p in prev)
                ok = val >= RATIO * base
                share, exp = None, None
            else:
                share = pct(get(row), row["classified"])
                exp = statistics.median(pct(get(p), p["classified"]) for p in prev)
                ok = get(row) >= MIN_COUNT and share >= RATIO * max(exp, 0.01) and share - exp >= MIN_PP
                val, base = get(row), exp
            if not ok:
                continue
            rs = by_m[row["month"]]
            # drivers: issues with the largest share increase vs the previous 3 months
            prev_rs = [r for p in prev for r in by_m[p["month"]]]
            cur_c, prev_c = Counter(r["issue"] for r in rs if r["is_c"] or r["issue"] == "other.boycott_political"), \
                Counter(r["issue"] for r in prev_rs if r["is_c"] or r["issue"] == "other.boycott_political")
            pool = [i for i in cur_c if (issue is None or i == issue) and
                    (key.startswith("area:") is False or i.split(".")[0] in AREAS[key[5:]])]
            drivers = sorted(pool, key=lambda i: -(pct(cur_c[i], len(rs)) - pct(prev_c[i], len(prev_rs))))[:3]
            ev_issue = issue or (drivers[0] if drivers else None)
            examples = sorted([r for r in rs if r["issue"] == ev_issue],
                              key=lambda r: (-r["severity"], -int(r["review_likes"] or 0),
                                             hashlib.sha256(r["review_id"].encode()).hexdigest()))[:3]
            anomalies.append({"month": row["month"], "series": key, "label": label, "partial_month": row["partial"],
                              "value": val, "share_pct": share, "expected_share_pct": exp if key != "volume" else None,
                              "expected_value": base if key == "volume" else None,
                              "ratio": round((share / exp) if share is not None and exp else (val / base if base else 0), 2),
                              "drivers": [{"issue": i, "count": cur_c[i], "share_pct": pct(cur_c[i], len(rs)),
                                           "prev_share_pct": pct(prev_c[i], len(prev_rs))} for i in drivers],
                              "examples": [{"review_id": e["review_id"], "severity": e["severity"],
                                            "quote": e["evidence_quote"][:220]} for e in examples]})
    anomalies.sort(key=lambda a: (a["month"], a["series"]))

    # ---------------- recurring problems
    full = [row for row in monthly if not row["partial"]]
    recurring = []
    for i in top_issues:
        shares = [pct(row["issues"][i], row["classified"]) for row in full]
        ranks = []
        for row in full:
            order = sorted(top_issues, key=lambda x: -row["issues"][x])
            ranks.append(order.index(i) + 1)
        first, last = statistics.mean(shares[:6]), statistics.mean(shares[-6:])
        peak = max(range(len(full)), key=lambda k: shares[k])
        recurring.append({"issue": i, "complaints": sum(row["issues"][i] for row in monthly),
                          "months_present": sum(s >= 0.5 for s in shares), "full_months": len(full),
                          "months_in_top5": sum(r <= 5 for r in ranks),
                          "first6_mean_share_pct": round(first, 2), "last6_mean_share_pct": round(last, 2),
                          "trend": "rising" if last >= 1.5 * first and last - first >= 0.5 else
                                   "falling" if first >= 1.5 * last and first - last >= 0.5 else "steady",
                          "peak_month": full[peak]["month"], "peak_share_pct": shares[peak],
                          "pattern": "chronic" if sum(s >= 0.5 for s in shares) >= 0.75 * len(full) else "episodic"})
    recurring.sort(key=lambda r: -r["complaints"])

    # ---------------- alternative rankings (baseline stays the official one)
    issue_rows = defaultdict(list)
    for r in comp:
        issue_rows[r["issue"]].append(r)
    recent = [m for m in months if "2023-08" <= m <= "2023-10"]
    recent_n = sum(len(by_m[m]) for m in recent)
    alt = []
    for i, rs in issue_rows.items():
        alt.append({"issue": i, "complaint_count": len(rs), "severity_sum": sum(r["severity"] for r in rs),
                    "severity_4_plus": sum(r["severity"] >= 4 for r in rs),
                    "cancellations": sum(r["intent"] == "cancellation" for r in rs),
                    "recent_3m_complaints": sum(r["m"] in recent for r in rs),
                    "recent_3m_share_pct": pct(sum(r["m"] in recent for r in rs), recent_n)})
    rankings = {k: [a["issue"] for a in sorted(alt, key=lambda a: (-a[k], a["issue"]))[:10]]
                for k in ("severity_sum", "severity_4_plus", "cancellations", "recent_3m_complaints")}

    # ---------------- severity heatmap and cancellation lens
    heat = {t: {s: sum(1 for r in comp if r["topic"] == t and r["severity"] == s) for s in range(1, 6)} for t in TOPICS}

    # ---------------- app versions (release lens): versions with >= 400 classified reviews
    ver = defaultdict(list)
    for r in rows:
        v = ".".join(r["app_version"].split(".")[:3]) if r["app_version"].strip() else "(missing)"
        ver[v].append(r)
    versions = []
    for v, rs in ver.items():
        if len(rs) < 400:
            continue
        cs = [r for r in rs if r["is_c"]]
        top = Counter(r["issue"] for r in cs if not r["issue"].startswith("other.")).most_common(1)
        versions.append({"version": v, "classified": len(rs), "complaint_share_pct": pct(len(cs), len(rs)),
                         "severity_4_plus_share_pct": pct(sum(r["severity"] >= 4 for r in cs), len(rs)),
                         "top_issue": top[0][0] if top else None, "top_issue_count": top[0][1] if top else 0,
                         "first_month": min(r["m"] for r in rs)})
    versions.sort(key=lambda v: (v["version"] == "(missing)", [int(x) if x.isdigit() else 0 for x in v["version"].split(".")]))

    events = json.loads((config.ROOT / "data" / "events.json").read_text())
    for e in events["events"]:
        e["matching_anomalies"] = [f"{a['month']}:{a['series']}" for a in anomalies
                                   if a["month"] in (e["month"], _next(e["month"])) and
                                   (a["series"] in e["related_series"] or (a["series"] == "boycott" and "issue:other.boycott_political" in e["related_series"]))]
    return {"rules": __doc__.split("\n\n")[1].strip(), "months": months, "top_issues": top_issues, "monthly": monthly,
            "anomalies": anomalies, "recurring": recurring, "alt_rankings": rankings,
            "alt_ranking_table": sorted(alt, key=lambda a: -a["severity_sum"])[:25],
            "comparable_windows": comparable_windows(store), "severity_heatmap": heat, "versions": versions,
            "events": events}


def _next(m):
    y, mo = map(int, m.split("-"))
    return f"{y + (mo == 12)}-{1 if mo == 12 else mo + 1:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    from ..cli import open_store
    res = build(open_store(a.run_id))
    (config.RUNS / a.run_id / "insights.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    print(f"months {len(res['months'])} | anomalies {len(res['anomalies'])} | recurring {len(res['recurring'])} | versions {len(res['versions'])}")
    for x in res["anomalies"]:
        print(f"  {x['month']} {x['series']:<38} value {x['value']:>6} share {x['share_pct']} vs expected {x['expected_share_pct'] or x['expected_value']} (x{x['ratio']}) drivers {[d['issue'] for d in x['drivers']]}")
    for r in res["recurring"]:
        print(f"  {r['issue']:<38} {r['pattern']:<8} {r['trend']:<8} months>=0.5%: {r['months_present']}/{r['full_months']} top5: {r['months_in_top5']} first6 {r['first6_mean_share_pct']} last6 {r['last6_mean_share_pct']} peak {r['peak_month']}")
    for e in res["events"]["events"]:
        print("  ", e["id"], e["month"], e["title"], "->", e["matching_anomalies"])


if __name__ == "__main__":
    main()
