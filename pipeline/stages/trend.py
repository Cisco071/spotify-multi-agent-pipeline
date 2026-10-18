"""Offline trend comparison (code only): complaint shares in two comparable 6-month windows of FULL calendar months.
The first (2022-05) and last (2023-11) months are partial and excluded. Reports numerators and denominators.

    python -m pipeline.stages.trend --run-id final-100k
"""
import argparse
import json
from collections import Counter

from .. import config
from .memo import AREAS

WINDOWS = {"H2-2022 (2022-06..2022-11)": ("2022-06", "2022-11"), "mid-2023 (2023-05..2023-10)": ("2023-05", "2023-10")}


def compare(store):
    rows = store.q("""SELECT r.topic, r.intent, substr(rv.review_timestamp,1,7) m FROM results r JOIN reviews rv USING(review_id)
                      WHERE r.status='completed' AND rv.synthetic=0""")
    out = {}
    for name, (a, b) in WINDOWS.items():
        w = [r for r in rows if a <= r["m"] <= b]
        comp = [r for r in w if r["intent"] in ("complaint", "cancellation")]
        by_area = Counter()
        for r in comp:
            for area, ts in AREAS.items():
                if r["topic"] in ts:
                    by_area[area] += 1
        out[name] = {"classified": len(w), "complaints": len(comp),
                     "complaint_share": f"{100 * len(comp) / len(w):.1f}%" if w else None,
                     "areas": {k: {"complaints": by_area[k], "share_of_classified": f"{100 * by_area[k] / len(w):.2f}%",
                                   "share_of_complaints": f"{100 * by_area[k] / len(comp):.1f}%" if comp else None}
                               for k in AREAS}}
    return {"windows": out, "note": "Full calendar months only; shares use classified reviews in the window as the "
                                    "denominator. Review volume is self-selected (e.g. spikes in 2023-07 and 2023-10)."}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args()
    from ..cli import open_store
    res = compare(open_store(a.run_id))
    (config.RUNS / a.run_id / "trend.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
