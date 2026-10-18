"""Ornn: OCPI GPU rental index and OTPI token price index.

Public tier, no key (verified 2026-10-18, robots.txt allows /api/public-index/*):
  - data.ornn.com/api/public-index/gpu/{gpu}/index-history: trailing 3 months daily settles
  - api.ornnai.com/api/otpi?startDate&endDate: per-lab token index, trailing 3 months
  - api.ornnai.com/api/otpi/weight-class: closed vs open-weights token index
Every run re-pulls the 3-month window and merges, so history accumulates past 3 months
and gaps from missed runs fill themselves. Forward curves (/api/forward) need a paid key.
Ornn also sells compute priced against this index: one source, not the truth.
"""
import datetime as dt
import urllib.parse

from common import get_json, merge_csv

GPUS = ["H100 SXM", "H200", "B200", "A100 SXM4"]
DATA_API = "https://data.ornn.com/api/public-index"
TOKEN_API = "https://api.ornnai.com/api"


def _window():
    end = dt.date.today()
    return (end - dt.timedelta(days=88)).isoformat(), end.isoformat()


def run():
    gpu_rows = []
    for g in GPUS:
        r = get_json(f"{DATA_API}/gpu/{urllib.parse.quote(g)}/index-history")
        for p in r.get("data", []):
            gpu_rows.append({"date": p["timestamp"][:10], "gpu": g, "usd_per_gpu_hour": p["index_value"]})
    if not gpu_rows:
        raise RuntimeError("OCPI returned no rows")
    merge_csv("ornn_ocpi.csv", gpu_rows, ["date", "gpu"], ["date", "gpu", "usd_per_gpu_hour"])

    start, end = _window()
    otpi = get_json(f"{TOKEN_API}/otpi", params={"startDate": start, "endDate": end}).get("data", [])
    merge_csv("ornn_otpi.csv",
              [{"date": x["date"], "lab": x["lab"], "usd_per_mtok": x["indexPerMtok"]} for x in otpi],
              ["date", "lab"], ["date", "lab", "usd_per_mtok"])

    wc = get_json(f"{TOKEN_API}/otpi/weight-class", params={"startDate": start, "endDate": end}).get("data", [])
    merge_csv("ornn_otpi_class.csv",
              [{"date": x["date"], "class": x["class"], "usd_per_mtok": x["indexPerMtok"],
                "labs_priced": x.get("labsPriced")} for x in wc],
              ["date", "class"], ["date", "class", "usd_per_mtok", "labs_priced"])

    last = max(r["date"] for r in gpu_rows)
    h100 = next(r["usd_per_gpu_hour"] for r in gpu_rows if r["date"] == last and r["gpu"] == "H100 SXM")
    return f"OCPI {len(gpu_rows)} rows through {last} (H100 ${h100}); OTPI {len(otpi)} lab rows, {len(wc)} class rows"
