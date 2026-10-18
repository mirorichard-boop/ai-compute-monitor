"""SEC EDGAR XBRL: quarterly capex for the hyperscalers plus Oracle, and Nvidia revenue.

Cash-flow facts are filed year-to-date, so quarters are derived by differencing
consecutive YTD facts that share a fiscal-year start (the 10-K annual minus the 9-month
YTD gives Q4). Concept names differ by filer and drift over time, so each company lists
candidates and every candidate is merged, latest filing winning per quarter.

Verified 2026-10-18: MSFT/GOOGL/META/ORCL file PaymentsToAcquirePropertyPlantAndEquipment;
AMZN and NVDA moved to PaymentsToAcquireProductiveAssets. Nvidia data-center revenue is a
segment member (dimensional), which the companyfacts API does not expose, so total revenue
is collected instead.
"""
import datetime as dt
import time
from collections import defaultdict

from common import get_json, merge_csv, secret

CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
REVENUE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]
COMPANIES = [  # ticker, CIK, metric, concepts
    ("MSFT", 789019, "capex", CAPEX),
    ("GOOGL", 1652044, "capex", CAPEX),
    ("AMZN", 1018724, "capex", CAPEX),
    ("META", 1326801, "capex", CAPEX),
    ("ORCL", 1341439, "capex", CAPEX),
    ("NVDA", 1045810, "revenue", REVENUE),
]
SINCE = "2021-01-01"


def _d(s):
    return dt.date.fromisoformat(s)


def quarters(facts):
    """{(start,end): (val, filed)} for durations, latest filing wins -> {quarter_end: val}."""
    spans = {}
    for f in facts:
        if "start" not in f or f.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
            continue
        k = (f["start"], f["end"])
        if k not in spans or f["filed"] > spans[k][1]:
            spans[k] = (f["val"], f["filed"])
    out = {}
    for (s, e), (v, _) in spans.items():
        if 80 <= (_d(e) - _d(s)).days <= 100:
            out[e] = (s, v)
    by_start = defaultdict(list)
    for (s, e), (v, _) in spans.items():
        if (_d(e) - _d(s)).days <= 380:
            by_start[s].append((e, v))
    for s, items in by_start.items():
        items.sort()
        for (e0, v0), (e1, v1) in zip(items, items[1:]):
            if 80 <= (_d(e1) - _d(e0)).days <= 100 and e1 not in out:
                out[e1] = ((_d(e0) + dt.timedelta(days=1)).isoformat(), v1 - v0)
    return out


def run():
    ua = secret("SEC_USER_AGENT") or "ai-compute-monitor research contact@example.com"
    rows, notes = [], []
    for tic, cik, metric, concepts in COMPANIES:
        facts = get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json",
                         headers={"User-Agent": ua})["facts"].get("us-gaap", {})
        merged = {}
        for c in concepts:  # later candidates only fill quarters earlier ones lack
            if c in facts:
                for e, sv in quarters(facts[c]["units"].get("USD", [])).items():
                    merged.setdefault(e, (*sv, c))
        kept = {e: v for e, v in merged.items() if e >= SINCE}
        for e, (s, v, c) in kept.items():
            rows.append({"ticker": tic, "metric": metric, "period_start": s, "period_end": e,
                         "usd": v, "concept": c})
        if kept:
            last = max(kept)
            notes.append(f"{tic} {metric} {last} ${kept[last][1] / 1e9:.1f}B")
        else:
            notes.append(f"{tic}: no quarters")
        time.sleep(0.25)  # SEC fair-access limit is 10 req/s
    merge_csv("edgar.csv", rows, ["ticker", "metric", "period_end"],
              ["ticker", "metric", "period_start", "period_end", "usd", "concept"])
    return "; ".join(notes)
