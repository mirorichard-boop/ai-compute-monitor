"""Vast.ai marketplace: daily floor and median ask per GPU, normalized per GPU-hour.

The offer search endpoint answered without auth on 2026-10-18. This is a marketplace of
hobbyist and small-host listings, so treat it as a floor series, not a market rate.
"""
import json
from statistics import median

from common import get_json, merge_csv, today

URL = "https://console.vast.ai/api/v0/bundles/"
GPUS = {"A100 SXM4": "A100 SXM4", "H100 SXM": "H100 SXM", "H200": "H200", "B200": "B200"}


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def run():
    d, rows = today(), []
    for label, name in GPUS.items():
        q = {"gpu_name": {"eq": name}, "rentable": {"eq": True}, "verified": {"eq": True},
             "order": [["dph_total", "asc"]], "limit": 500}
        offers = get_json(URL, params={"q": json.dumps(q)}).get("offers", [])
        per_gpu = [o["dph_total"] / o["num_gpus"] for o in offers if o.get("num_gpus")]
        if not per_gpu:
            continue
        rows.append({"date": d, "gpu": label, "offers": len(per_gpu),
                     "gpus_listed": sum(o["num_gpus"] for o in offers if o.get("num_gpus")),
                     "min": round(min(per_gpu), 4), "p25": round(_pct(per_gpu, 0.25), 4),
                     "median": round(median(per_gpu), 4)})
    if not rows:
        raise RuntimeError("no offers returned for any GPU")
    merge_csv("vast.csv", rows, ["date", "gpu"], ["date", "gpu", "offers", "gpus_listed", "min", "p25", "median"])
    return "; ".join(f"{r['gpu']} n={r['offers']} med ${r['median']:.2f}" for r in rows)
