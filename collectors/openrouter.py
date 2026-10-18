"""OpenRouter: daily snapshot of list prices and per-provider endpoint prices.

Public, no key. /models gives one list price per model; /models/{id}/endpoints gives each
provider's price for the same weights (verified 2026-10-18). Throughput fields on the
endpoints payload come back null, so throughput is not collected.
"""
from statistics import median

from common import get_json, load_yaml, merge_csv, today

BASE = "https://openrouter.ai/api/v1"


def _f(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v >= 0 else None


def run():
    cfg = load_yaml("models.yml")
    d = today()
    wanted = {m for t in cfg["tiers"].values() for m in t["models"]} | set(cfg["endpoint_models"])

    models = get_json(f"{BASE}/models")["data"]
    rows = []
    for m in models:
        if m["id"] not in wanted:
            continue
        p = m.get("pricing", {})
        rows.append({"date": d, "model": m["id"], "prompt": _f(p.get("prompt")),
                     "completion": _f(p.get("completion")), "cache_read": _f(p.get("input_cache_read"))})
    missing = sorted(wanted - {r["model"] for r in rows})
    merge_csv("openrouter_models.csv", rows, ["date", "model"],
              ["date", "model", "prompt", "completion", "cache_read"])

    ep_rows = []
    for mid in cfg["endpoint_models"]:
        eps = get_json(f"{BASE}/models/{mid}/endpoints")["data"]["endpoints"]
        seen = set()
        for e in eps:
            prov, q = e.get("provider_name"), e.get("quantization") or ""
            tag = e.get("tag") or f"{prov}/{q}"
            if tag in seen:
                continue
            seen.add(tag)
            p = e.get("pricing", {})
            pr, co = _f(p.get("prompt")), _f(p.get("completion"))
            if not pr and not co:
                continue  # free endpoints distort the median
            ep_rows.append({"date": d, "model": mid, "endpoint": tag, "provider": prov,
                            "quantization": q, "prompt": pr, "completion": co})
    merge_csv("openrouter_endpoints.csv", ep_rows, ["date", "model", "endpoint"],
              ["date", "model", "endpoint", "provider", "quantization", "prompt", "completion"])

    ref = [r for r in ep_rows if r["model"] == cfg["reference_model"]]
    note = f"{len(rows)} models, {len(ep_rows)} endpoints; reference has {len(ref)} providers"
    if ref:
        note += f", median output ${median(r['completion'] for r in ref) * 1e6:.2f}/Mtok"
    if missing:
        note += f"; not listed: {', '.join(missing)}"
    return note
