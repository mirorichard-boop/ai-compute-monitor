"""Artificial Analysis: daily snapshot of price, speed, and Intelligence Index per model.

Needs AA_API_KEY (free tier). Credit Artificial Analysis wherever shown. Provider-level and
historical data are commercial-tier only, so history starts at the first keyed run.
The payload shape below follows AA's v2 docs and was NOT test-called (no key on hand);
field lookups are defensive and a mismatch fails soft with the error on the page.
"""
from common import SkipSource, get_json, merge_csv, secret, today

URLS = ["https://artificialanalysis.ai/api/v2/language/models",
        "https://artificialanalysis.ai/api/v2/language/models/free"]


def _dig(d, *paths):
    for p in paths:
        cur = d
        for k in p.split("."):
            cur = cur.get(k) if isinstance(cur, dict) else None
        if cur is not None:
            return cur
    return None


def run():
    key = secret("AA_API_KEY")
    if not key:
        raise SkipSource("AA_API_KEY not set")
    err = None
    for url in URLS:
        try:
            payload = get_json(url, headers={"x-api-key": key})
            break
        except RuntimeError as e:
            err = e
    else:
        raise err
    models = payload.get("data", payload) if isinstance(payload, dict) else payload
    d, rows = today(), []
    for m in models:
        rows.append({
            "date": d,
            "model": _dig(m, "slug", "id", "name"),
            "creator": _dig(m, "model_creator.slug", "model_creator.name", "creator"),
            "intelligence_index": _dig(m, "evaluations.artificial_analysis_intelligence_index",
                                       "intelligence_index"),
            "usd_per_mtok_blended": _dig(m, "pricing.price_1m_blended_3_to_1", "price_1m_blended_3_to_1"),
            "usd_per_mtok_input": _dig(m, "pricing.price_1m_input_tokens", "price_1m_input_tokens"),
            "usd_per_mtok_output": _dig(m, "pricing.price_1m_output_tokens", "price_1m_output_tokens"),
            "tokens_per_s": _dig(m, "median_output_tokens_per_second", "output_speed"),
        })
    rows = [r for r in rows if r["model"]]
    if not rows:
        raise RuntimeError("payload parsed to zero models; check field names")
    merge_csv("artificial_analysis.csv", rows, ["date", "model"],
              ["date", "model", "creator", "intelligence_index", "usd_per_mtok_blended",
               "usd_per_mtok_input", "usd_per_mtok_output", "tokens_per_s"])
    return f"{len(rows)} models"
