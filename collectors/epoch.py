"""Epoch AI: token-price backfill at fixed performance, and Frontier Data Centers power.

Both are CC-BY; credit Epoch AI wherever shown.
  - LLM inference price trends (lowest_price_models_data.csv): cheapest model clearing each
    benchmark threshold over time. Historical only (last row 2025-02 when checked
    2026-10-18); the benchmark hub zip (benchmark_data.zip) has scores but no prices.
  - Frontier Data Centers timelines: per-site power (MW) and H100-equivalents at dated
    satellite/permit observations. Rolled up to a month-end total, carrying each site's
    last observation forward.
"""
import csv
import io
from collections import defaultdict

from common import get, merge_csv

PRICE_URL = "https://epoch.ai/data/charts/llm-inference-price-trends/lowest_price_models_data.csv"
DC_URL = "https://epoch.ai/data/data_centers/data_center_timelines.csv"


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _month_ends(first, last):
    y, m = int(first[:4]), int(first[5:7])
    while f"{y:04d}-{m:02d}" <= last[:7]:
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1


def run():
    rows = list(csv.DictReader(io.StringIO(get(PRICE_URL).decode("utf-8"))))
    price = [{"benchmark": r["Benchmark"], "threshold_model": r["Threshold model"],
              "model": r["Model Name"], "date": r["Release Date"],
              "usd_per_mtok": r["USD per 1M Tokens"]} for r in rows if _num(r["USD per 1M Tokens"])]
    merge_csv("epoch_price_backfill.csv", price, ["benchmark", "threshold_model", "date", "model"],
              ["benchmark", "threshold_model", "date", "model", "usd_per_mtok"])

    tl = list(csv.DictReader(io.StringIO(get(DC_URL).decode("utf-8"))))
    by_site = defaultdict(list)
    for r in tl:
        if r.get("Date"):
            by_site[r["Data center"]].append((r["Date"], _num(r.get("Power (MW)")) or 0.0,
                                              _num(r.get("IT power (MW)")) or 0.0,
                                              _num(r.get("H100 equivalents")) or 0.0))
    for v in by_site.values():
        v.sort()
    dates = [d for v in by_site.values() for d, *_ in v]
    out = []
    for mo in _month_ends(min(dates), max(dates)):
        tot = it = h = 0.0
        n = 0
        for v in by_site.values():
            last = [x for x in v if x[0][:7] <= mo]
            if last:
                _, p, i, e = last[-1]
                tot, it, h = tot + p, it + i, h + e
                n += p > 0
        out.append({"month": mo, "power_mw": round(tot, 1), "it_power_mw": round(it, 1),
                    "h100e": round(h), "sites_powered": n})
    merge_csv("epoch_dc_power.csv", out, ["month"], ["month", "power_mw", "it_power_mw", "h100e", "sites_powered"])
    return (f"{len(price)} price-backfill rows through {max(p['date'] for p in price)}; "
            f"{len(by_site)} data centers, {out[-1]['power_mw']:,.0f} MW tracked at {out[-1]['month']}")
