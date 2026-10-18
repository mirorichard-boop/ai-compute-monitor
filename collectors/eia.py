"""EIA Open Data v2: industrial retail electricity price and PJM/ERCOT daily demand.

Needs EIA_API_KEY (free at eia.gov/opendata). Both routes verified 2026-10-18.
"""
from common import SkipSource, get_json, merge_csv, secret

BASE = "https://api.eia.gov/v2"
REGIONS = {"PJM": "PJM", "ERCO": "ERCOT"}


def _pages(route, params, key):
    out, offset = [], 0
    while True:
        p = dict(params, api_key=key, offset=offset, length=5000)
        resp = get_json(f"{BASE}/{route}/data/", params=p)["response"]
        data = resp.get("data", [])
        out += data
        offset += len(data)
        if not data or offset >= int(resp.get("total", 0)):
            return out


def run():
    key = secret("EIA_API_KEY")
    if not key:
        raise SkipSource("EIA_API_KEY not set")

    price = _pages("electricity/retail-sales", {
        "frequency": "monthly", "data[0]": "price", "facets[sectorid][]": "IND",
        "facets[stateid][]": ["US", "VA", "TX", "GA", "OH", "AZ"], "start": "2018-01"}, key)
    merge_csv("eia_retail_price.csv",
              [{"month": r["period"], "state": r["stateid"], "cents_per_kwh": r.get("price")} for r in price],
              ["month", "state"], ["month", "state", "cents_per_kwh"])

    demand = _pages("electricity/rto/daily-region-data", {
        "frequency": "daily", "data[0]": "value", "facets[respondent][]": list(REGIONS),
        "facets[type][]": "D", "facets[timezone][]": "Eastern", "start": "2019-01-01"}, key)
    merge_csv("eia_demand.csv",
              [{"date": r["period"], "region": REGIONS.get(r["respondent"], r["respondent"]),
                "demand_mwh": r.get("value")} for r in demand],
              ["date", "region"], ["date", "region", "demand_mwh"])
    return f"{len(price)} retail-price rows, {len(demand)} demand rows"
