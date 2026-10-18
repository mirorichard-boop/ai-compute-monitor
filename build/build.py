#!/usr/bin/env python3
"""Merge data/*.csv and config/*.yml into docs/data.json for the static page.

All derived metrics are computed here so the page only draws. Same-origin JSON means the
page works from GitHub Pages with no server and no CORS.
"""
import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "collectors"))
from common import blended, load_yaml  # noqa: E402

DATA, OUT = ROOT / "data", ROOT / "docs" / "data.json"

# Days since last success before a source is flagged stale on the page.
STALE_AFTER = {"openrouter": 3, "ornn": 4, "vast": 3, "epoch": 45, "edgar": 120,
               "eia": 45, "artificial_analysis": 3}
SOURCE_LABELS = {"openrouter": "OpenRouter", "ornn": "Ornn OCPI / OTPI", "vast": "Vast.ai",
                 "epoch": "Epoch AI", "edgar": "SEC EDGAR", "eia": "EIA",
                 "artificial_analysis": "Artificial Analysis"}
REGIME_FLAT = 0.03  # |30d change| below this counts as flat
HYPERSCALERS = ["MSFT", "GOOGL", "AMZN", "META"]


def rd(name):
    p = DATA / name
    if not p.exists():
        return []
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def series(pairs):
    return [[d, round(v, 6)] for d, v in sorted(pairs) if v is not None]


def pct_change(s, days):
    """Change from the value on or before (last date - days) to the last value."""
    if len(s) < 2:
        return None
    last_d, last_v = s[-1]
    cutoff = (dt.date.fromisoformat(last_d) - dt.timedelta(days=days)).isoformat()
    base = [v for d, v in s if d <= cutoff]
    if not base or not base[-1]:
        return None
    return last_v / base[-1] - 1


def regime(tok, rent):
    if tok is None or rent is None:
        return None
    t = "flat" if abs(tok) < REGIME_FLAT else ("down" if tok < 0 else "up")
    r = "flat" if abs(rent) < REGIME_FLAT else ("down" if rent < 0 else "up")
    table = {
        ("down", "up"): "Token volume outrunning efficiency. Rents hold, labs absorb the squeeze",
        ("down", "down"): "Supply catching up, normalization",
        ("flat", "up"): "Scarcity pricing passing through",
        ("up", "up"): "Demand pull",
    }
    return {"token": t, "rent": r, "reading": table.get((t, r), "Off the regime table: no clean read"),
            "token_chg_30d": tok, "rent_chg_30d": rent}


def index_to_100(s):
    if not s:
        return []
    b = s[0][1]
    return [[d, v / b * 100] for d, v in s]


# ---- plain-English signals -------------------------------------------------------------------
# Each signal states its rule in words, so a reader can check the call against the numbers.
# Status: "steady" = no inflection, "watch" = moving toward one, "turn" = inflection rule met,
# "pending" = not enough data to judge.
WINDOW = 85    # "3 months": Ornn's free history is ~88 days, so 85 keeps a full window
MOVE = 0.05    # a 3-month move smaller than 5% counts as flat
NEW_CHIPS = ["H200", "B200"]
DC_STATES = ["VA", "OH", "TX", "GA", "AZ"]  # largest AI data center build-outs
FLEET = ["H100 SXM", "H200", "B200"]

SCENARIOS = [
    {"key": "boom", "tokens": "down", "rent": "up", "name": "Demand outrunning supply",
     "plain": "AI is getting cheaper to buy, yet renting the chips that make it costs more. Usage is growing faster than chips can be built.",
     "means": "Chip owners keep pricing power. The squeeze lands on the companies that rent chips and sell AI output at falling prices.",
     "squeezed": ["token_renters"], "benefits": ["gpu_landlords", "chipmakers"]},
    {"key": "transition", "tokens": "down", "rent": "flat", "name": "Rents have stopped rising",
     "plain": "AI keeps getting cheaper, but chip rents have gone flat. The shortage is no longer getting worse.",
     "means": "Often the step between a shortage and a glut. Watch whether rents tip down next.",
     "squeezed": ["token_renters"], "next": ["gpu_landlords"]},
    {"key": "glut", "tokens": "down", "rent": "down", "name": "Supply catching up",
     "plain": "AI prices and chip rents are both falling. Enough chips are now online that owners compete on price.",
     "means": "The shortage is over. In past chip cycles this preceded capex cuts and falling chip orders.",
     "squeezed": ["gpu_landlords"], "next": ["chipmakers"], "benefits": ["token_renters"]},
    {"key": "passthrough", "tokens": "flat", "rent": "up", "name": "Shortage passed to customers",
     "plain": "Chip rents are rising and AI prices have stopped falling. Sellers are passing higher costs on.",
     "means": "AI sellers have pricing power; demand is strong enough to absorb it.",
     "benefits": ["token_renters", "gpu_landlords"]},
    {"key": "pull", "tokens": "up", "rent": "up", "name": "Customers paying more for everything",
     "plain": "AI prices and chip rents are both rising.",
     "means": "Strongest demand reading. Rare so far.",
     "benefits": ["token_renters", "gpu_landlords", "chipmakers"]},
]


def direction(x):
    return None if x is None else "flat" if abs(x) < MOVE else "down" if x < 0 else "up"


def pct(x, d=0):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def signals(out, ocpi, tok_open, big4):
    chg = {g: pct_change(s, WINDOW) for g, s in ocpi.items()}
    chg30 = {g: pct_change(s, 30) for g, s in ocpi.items()}
    tok3 = pct_change(tok_open, WINDOW)
    fleet = [chg[g] for g in FLEET if chg.get(g) is not None]
    rent3 = sum(fleet) / len(fleet) if fleet else None
    sig = []

    # 1. New-chip rents
    new3 = [chg[g] for g in NEW_CHIPS if chg.get(g) is not None]
    new30 = [chg30[g] for g in NEW_CHIPS if chg30.get(g) is not None]
    n3 = sum(new3) / len(new3) if new3 else None
    n30 = sum(new30) / len(new30) if new30 else None
    st = "pending" if n3 is None else "turn" if n3 < -MOVE else "watch" if (n3 < MOVE or (n30 or 0) < 0) else "steady"
    sig.append({
        "id": "new_rent", "title": "Are the newest chips still scarce?",
        "value": pct(n3), "value_label": "3-month change in rent, H200 and B200",
        "status": st,
        "now": ("Rents on Nvidia's newest chips are still climbing: buyers are competing for a limited supply."
                if st == "steady" else "Rent growth on the newest chips has stalled or reversed over the last month."
                if st == "watch" else "Rents on the newest chips are falling. Supply has caught up with demand."),
        "rule": f"Turns when the 3-month change drops below {pct(-MOVE)}. Watch when it is between {pct(-MOVE)} and {pct(MOVE)}, or the last month is negative.",
        "why": "Rising rents on the newest chips are the cleanest sign that demand exceeds supply. When they roll over, the shortage that justifies record capex is ending.",
    })

    # 2. Older-chip rents (the canary)
    h3, h30 = chg.get("H100 SXM"), chg30.get("H100 SXM")
    st = "pending" if h3 is None else "turn" if h3 < -0.10 else "watch" if h3 < MOVE else "steady"
    ret = out["retirement"]["latest"]
    sig.append({
        "id": "old_rent", "title": "Are older chips losing value?",
        "value": pct(h3), "value_label": "3-month change in H100 rent",
        "status": st,
        "now": (f"H100 rent is roughly flat ({pct(h3)} over 3 months, {pct(h30)} over 1 month) while newer chips rise. "
                "Demand is moving to the new chips, but H100s are not yet being dumped."
                if st == "watch" else "H100 rent is still rising: even 2023-era chips are in demand." if st == "steady"
                else "H100 rent is falling fast. Older fleets are losing pricing power."),
        "rule": "Turns when H100 rent falls more than 10% over 3 months. Watch when it is flat or falling.",
        "why": "The H100 is the most widely installed AI chip. An oversupply shows up here first, and it hits the balance sheets of companies that borrowed to buy H100 fleets.",
        "extra": (f"Cost per unit of work, H100 vs B200: {ret:.2f}. Above 1.00 means an H100 costs more per unit of work than "
                  "the newer B200, the point where operators retire H100s. Provisional: the performance factor behind it is "
                  "not yet sourced.") if ret else None,
    })

    # 3. Squeeze gap
    gap = None if tok3 is None or rent3 is None else rent3 - tok3
    st = "pending" if gap is None else "turn" if (direction(rent3) == "down") else "watch" if gap > 0.30 else "steady"
    sig.append({
        "id": "squeeze", "title": "Is AI getting cheaper faster than the chips?",
        "value": "n/a" if gap is None else f"{pct(tok3)} vs {pct(rent3)}",
        "value_label": "AI output price vs chip rent, 3 months",
        "status": st,
        "now": (f"Over 3 months the price of open-model AI output fell {abs(tok3) * 100:.0f}% while chip rents {'rose' if rent3 > 0 else 'fell'} {abs(rent3) * 100:.0f}% "
                f"(average of H100, H200, B200). A chip-hour whose output sold for $1.00 three months ago now sells for "
                f"${1 + tok3:.2f}, while the chip-hour costs {abs(rent3) * 100:.0f}% {'more' if rent3 > 0 else 'less'} to rent.") if gap is not None else "Not enough data yet.",
        "rule": "Watch while the gap is above 30 points: someone in the middle is absorbing it. Turns when chip rents start falling too: the squeeze then shifts from AI sellers to chip owners.",
        "why": "Cheaper AI drives more usage, which is the bull case for chip demand. But a company that rents chips and sells AI output at falling prices sees margins shrink unless efficiency keeps up.",
    })

    # 4. Break-even workload
    req = out["throughput"]["otpi_open"]["tokens_per_gpu_hour"]
    anchors = out["throughput"]["anchors"]
    growth = req[-1][1] / req[0][1] - 1 if len(req) > 1 else None
    st = "pending" if not req else ("turn" if anchors and req[-1][1] > max(a["tokens_per_gpu_hour"] for a in anchors)
                                    else "watch" if (growth or 0) > 0.5 else "steady")
    sig.append({
        "id": "breakeven", "title": "How hard must each chip work to pay its rent?",
        "value": f"{req[-1][1] / 1e6:.0f}M" if req else "n/a",
        "value_label": "AI tokens one H100 must produce per hour to cover rent",
        "status": st,
        "now": (f"At today's prices an H100 must produce about {req[-1][1] / 1e6:.0f} million tokens an hour "
                f"(≈{req[-1][1] / 3600:,.0f} per second, nonstop) just to cover its rent. That is {pct(growth)} since {req[0][0]}."
                if req else "Not enough data yet."),
        "rule": ("Turns when the break-even rate exceeds what a chip can actually produce. "
                 + ("Compared against published benchmarks." if anchors else
                    "No published benchmark is logged yet, so this can only flag the trend: watch when the requirement rises 50%+.")),
        "why": "This is the squeeze in one number. If the tokens a chip must sell rise faster than software and new hardware can deliver, serving AI on rented chips stops paying.",
    })

    # 5. Contract vs spot
    t5 = [e for e in out["term"]["entries"] if e.get("spot_over_term")]
    r = t5[-1]["spot_over_term"] if t5 else None
    st = "pending" if len(t5) < 2 else "turn" if r < 1.1 else "watch" if r < 1.3 else "steady"
    sig.append({
        "id": "term", "title": "Does the market expect cheaper chips later?",
        "value": f"{r:.2f}×" if r else "n/a", "value_label": "today's rent ÷ 5-year contract rent",
        "status": st,
        "now": (f"Renting an H100 today costs {r:.2f}× the reported 5-year contract rate. Buyers pay a premium for chips now, which says the market expects rents to fall. Long contracts are also cheaper for volume and credit reasons, so the trend in this ratio matters more than its level."
                + (" One unverified data point so far, so no call." if len(t5) < 2 else "")) if r else "No contract prices logged.",
        "rule": "Turns when today's rent falls to within 10% of long-term contract rents: the shortage premium is gone.",
        "why": "The premium of spot over long contracts measures how much of today's price is shortage. It shrinks before rents fall outright.",
    })

    # 6. Big Tech capex growth
    yoy = [[big4[i][0], big4[i][1] / big4[i - 4][1] - 1] for i in range(4, len(big4))]
    st = "pending"
    if len(yoy) >= 4:
        steps = list(zip(yoy[-4:], yoy[-3:]))
        slower = sum(b[1] < a[1] for a, b in steps)
        st = "turn" if yoy[-1][1] < 0 else "watch" if slower >= 2 else "steady"
    sig.append({
        "id": "capex", "title": "Is Big Tech still accelerating spending?",
        "value": pct(yoy[-1][1]) if yoy else "n/a",
        "value_label": f"Microsoft, Alphabet, Amazon, Meta capex vs a year earlier ({yoy[-1][0] if yoy else ''})",
        "status": st,
        "now": (f"Combined capex was ${big4[-1][1]:.0f}B last quarter, {pct(yoy[-1][1])} on a year ago, and the growth rate is "
                f"{'still rising' if yoy[-1][1] > yoy[-2][1] else 'slowing'} ({pct(yoy[-2][1])} the quarter before).") if len(yoy) > 1 else "",
        "rule": "Watch when the growth rate is lower than the quarter before in two of the last three quarters: spending still rises but the acceleration is fading. Turns when spending falls below the year before.",
        "why": "Chipmaker revenue follows the change in capex, not its level. In 2022 growth slowed from +30% to +7% over three quarters; this rule flagged it at Q4 2022, one quarter before spending fell in 2023.",
        "series": yoy,
    })

    # 7. Power
    load = {r: v[-1][1] for r, v in out["power"]["eia_demand_yoy"].items() if v}
    px = out["power"]["eia_price_12m"]
    dc_px = {st: px[st][-1][1] for st in DC_STATES if px.get(st)}
    dcp = median(dc_px.values()) if dc_px else None  # median: one state's rate case shouldn't drive the call
    lead = sorted(dc_px.items(), key=lambda x: -x[1])[:2]
    usp = px["US"][-1][1] if px.get("US") else None
    hot_load = any(v > 0.03 for v in load.values())
    hot_px = dcp is not None and dcp > 0.05
    st = "pending" if not load or dcp is None else "turn" if (hot_load and hot_px) else "watch" if (hot_load or hot_px) else "steady"
    pm = px["US"][-1][0] if px.get("US") else ""
    sig.append({
        "id": "power", "title": "Is power becoming the bottleneck?",
        "value": " / ".join(f"{pct(load[r], 1)}" for r in ("PJM", "ERCOT") if r in load),
        "value_label": "12-month growth in grid load, PJM (Virginia–Ohio) / ERCOT (Texas)",
        "status": st, "status_label": {"turn": "Binding", "watch": "Tightening"}.get(st),
        "now": (f"Electricity use on the two grids with the most data center construction is growing "
                f"{pct(load.get('PJM'), 1)} (PJM) and {pct(load.get('ERCOT'), 1)} (ERCOT) a year. For most of the "
                f"2010s US demand barely grew, so this is new load. Industrial power prices in the big data-center states "
                f"are {pct(dcp, 1)} on a year ago (median of {len(dc_px)} states, led by "
                f"{' and '.join(f'{k} {pct(v)}' for k, v in lead)}; US average {pct(usp, 1)}), 12-month average to {pm}.") if load else "",
        "rule": "Tightening when either grid's load grows more than 3% a year, or the median industrial price in data-center states rises more than 5%. Binding when both happen.",
        "why": "If power, not chips, limits how many chips can be switched on, the shortage lasts longer: chips can't flood the market faster than grids connect them. That supports chip rents and favors power-efficient new chips, but it caps how many chips get installed.",
        "extra": f"Data-center states: {', '.join(DC_STATES)}. EIA price data runs about three months behind.",
    })

    # compact labels and gauges for the tile view. A gauge is a bar from lo to hi split into
    # status zones; each zone runs up to `to`. The marker is the current value.
    INF = float("inf")

    def gauge(label, v, lo, hi, zones, fmt="pct"):
        return {"label": label, "value": v, "lo": lo, "hi": hi, "fmt": fmt,
                "zones": [{"to": None if z == INF else z, "status": st} for z, st in zones]}  # None = open-ended

    COMPACT = {
        "new_rent": ("Newest chips still scarce?", "Turns below −5%",
                     [gauge("H200/B200 rent, 3 mo", n3, -0.3, 0.5, [(-MOVE, "turn"), (MOVE, "watch"), (INF, "steady")])]),
        "old_rent": ("Older chips losing value?", "Turns below −10%",
                     [gauge("H100 rent, 3 mo", h3, -0.3, 0.3, [(-0.10, "turn"), (MOVE, "watch"), (INF, "steady")])]),
        "squeeze": ("AI cheapening faster than chips?", "Turns when rents fall too",
                    [gauge("Gap, rent minus AI price", gap, 0, 1.0, [(0.30, "steady"), (INF, "watch")], "pts")]),
        "breakeven": ("Work each chip must do to pay rent", "Watch above +50%",
                      [gauge("Break-even tokens, change", growth, 0, 2.0, [(0.5, "steady"), (INF, "watch")])]),
        "term": ("Market expects cheaper chips?", "Turns below 1.1×",
                 [gauge("Spot ÷ 5-yr contract", r, 0.8, 2.0, [(1.1, "turn"), (1.3, "watch"), (INF, "steady")], "x")]),
        "capex": ("Big Tech still accelerating?", "Watch: 2 of 3 quarters slower", []),
        "power": ("Power the bottleneck?", "Binding: load >3% and price >5%",
                  [gauge(f"{k} load, 12 mo", load.get(k), -0.02, 0.08, [(0.03, "steady"), (INF, "watch")])
                   for k in ("PJM", "ERCOT") if k in load]
                  + [gauge("Power price, DC states", dcp, -0.05, 0.15, [(0.05, "steady"), (INF, "watch")])]),
    }
    for x in sig:
        x["short"], x["rule_short"], x["gauges"] = COMPACT[x["id"]]

    # who is exposed to each signal; "now" when the signal is already flashing, else "if it turns"
    EXPOSURE = {
        "new_rent": (["gpu_landlords", "chipmakers"], "Hit first if this turns"),
        "old_rent": (["gpu_landlords"], "Hit first if this turns"),
        "squeeze": (["token_renters"], "Being squeezed now" if sig[2]["status"] in ("watch", "turn") else "Squeezed if this widens"),
        "breakeven": (["token_renters", "ai_output_owners"], "Selling AI output against this break-even"),
        "term": (["gpu_landlords"], "Hit if the premium disappears"),
        "capex": (["chipmakers"], "Hit first if this turns"),
        "power": (["power"], "Benefits while grids are tight"),
    }
    for x in sig:
        groups, label = EXPOSURE[x["id"]]
        x["exposure"] = {"label": label, "groups": groups}

    # scenario (the four-way table) on 3-month changes, fleet-average rent
    td, rd_ = direction(tok3), direction(rent3)
    cur = next((s for s in SCENARIOS if s["tokens"] == td and s["rent"] == rd_), None)
    scen = {"tokens": td, "rent": rd_, "token_chg": tok3, "rent_chg": rent3,
            "current": cur["key"] if cur else None, "all": SCENARIOS,
            "rule": f"3-month change in open-model AI price vs average H100/H200/B200 rent; moves under {pct(MOVE)} count as flat."}
    scen["tripwires"] = tripwires(sig, n3, h3, gap, growth, r, t5, yoy, load, dcp)
    scen["verdict"] = verdict(cur, scen["tripwires"])
    return sig, scen


# ---- tripwires ---------------------------------------------------------------------------------
# One row per turn the market could take. progress runs from 0 (as far from the trigger as the
# scale goes) to 1 (trigger hit); "kind" says what tripping means:
#   turn       = the chip cycle turning down   pressure = a squeeze already biting
#   constraint = power limiting the build-out (benefits power names)
def tripwires(sig, n3, h3, gap, growth, r, t5, yoy, load, dcp):
    def prog(v, far, trig):
        if v is None:
            return None
        return max(0.0, min(1.25, (far - v) / (far - trig)))

    st = {x["id"]: x["status"] for x in sig}
    max_load = max(load.values()) if load else None
    pw = None if max_load is None or dcp is None else min(max(0, max_load) / 0.03, max(0, dcp) / 0.05)
    rows = [
        {"id": "old_rent", "kind": "turn", "name": "H100 rent drops 10% in 3 months", "now": pct(h3), "trigger": "−10%",
         "progress": prog(h3, 0.30, -0.10), "hit": ["gpu_landlords"]},
        {"id": "new_rent", "kind": "turn", "name": "H200/B200 rents drop over 3 months", "now": pct(n3), "trigger": "−5%",
         "progress": prog(n3, 0.50, -MOVE), "hit": ["gpu_landlords", "chipmakers"]},
        {"id": "capex", "kind": "turn", "name": "Big Tech capex shrinks", "now": pct(yoy[-1][1]) if yoy else "n/a",
         "trigger": "below a year ago", "progress": prog(yoy[-1][1] if yoy else None, 1.0, 0.0), "hit": ["chipmakers"]},
        {"id": "term", "kind": "turn", "name": "Shortage premium gone", "now": f"{r:.2f}×" if r else "n/a",
         "trigger": "spot ≤ 1.1× contract", "progress": prog(r, 2.0, 1.1) if len(t5) >= 2 else None,
         "hit": ["gpu_landlords"], "note": "needs more contract data" if len(t5) < 2 else None},
        {"id": "squeeze", "kind": "pressure", "name": "AI price outruns rent by 30 pts", "now": f"{gap * 100:.0f} pts" if gap is not None else "n/a",
         "trigger": "30 pts", "progress": prog(gap, 0.0, 0.30), "hit": ["token_renters"]},
        {"id": "breakeven", "kind": "pressure", "name": "Break-even work up 50%", "now": pct(growth) if growth is not None else "n/a",
         "trigger": "+50%", "progress": prog(growth, 0.0, 0.50), "hit": ["token_renters", "ai_output_owners"]},
        {"id": "power", "kind": "constraint", "name": "Power becomes the limit", "now": f"load {pct(max_load, 1)}, price {pct(dcp, 1)}",
         "trigger": "load >3% and price >5%", "progress": None if pw is None else min(1.25, pw), "hit": ["power"]},
    ]
    vals = {"old_rent": (h3, -0.10), "new_rent": (n3, -MOVE), "capex": (yoy[-1][1] if yoy else None, 0.0),
            "squeeze": (gap, 0.30), "breakeven": (growth, 0.50)}
    for x in rows:
        x["tripped"] = x["progress"] is not None and x["progress"] >= 1
        v, t = vals.get(x["id"], (None, None))
        x["distance"] = None if v is None else f"{abs(v - t) * 100:.0f} pts {'past' if x['tripped'] else 'to go'}"
        if x["id"] == "term" and r:
            x["distance"] = f"{r - 1.1:.2f}× to go"
        x["status"] = st.get(x["id"])
    order = {"turn": 0, "pressure": 1, "constraint": 2}
    rows.sort(key=lambda x: (x["progress"] is None, order[x["kind"]], -(x["progress"] or 0)))
    return rows


def verdict(cur, tw):
    turns = [x for x in tw if x["kind"] == "turn" and x["progress"] is not None]
    tripped_turns = [x for x in turns if x["tripped"]]
    pressure = [x for x in tw if x["kind"] == "pressure" and x["tripped"]]
    power = next((x for x in tw if x["kind"] == "constraint"), None)
    nearest = max((x for x in turns if not x["tripped"]), key=lambda x: x["progress"], default=None)
    head = ("The cycle is turning." if tripped_turns else
            "Shortage intact." if cur and cur["key"] == "boom" else
            f"{cur['name']}." if cur else "No clean read.")
    parts = [head]
    if pressure:
        parts.append("Sellers of AI output are being squeezed.")
    if power and power["tripped"]:
        parts.append("Power is the binding limit.")
    if nearest:
        parts.append(f"Nearest turn: {nearest['name']} (now {nearest['now']}, trigger {nearest['trigger']}, {nearest['distance']}).")
    return {"text": " ".join(parts), "nearest": nearest["id"] if nearest else None,
            "tripped_turns": len(tripped_turns), "turns": len(turns), "pressure": len(pressure),
            "power_binding": bool(power and power["tripped"])}


def build():
    models = load_yaml("models.yml")
    hw = load_yaml("hardware.yml")
    manual = load_yaml("manual.yml")
    ratio = models.get("blend_input_to_output", 3)
    ref = models["reference_model"]
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "config": {"blend_ratio": ratio, "reference_model": ref, "regime_flat": REGIME_FLAT,
                      "perf": hw["relative_inference_perf"], "perf_source": hw["perf_source"],
                      "kw": hw["kw_per_gpu"], "kw_source": hw["kw_source"]}}

    # ---- source status -------------------------------------------------------------------
    st = json.loads((DATA / "status.json").read_text()) if (DATA / "status.json").exists() else {}
    now = dt.datetime.now(dt.timezone.utc)
    status = []
    for k, label in SOURCE_LABELS.items():
        s = st.get(k, {})
        ls = s.get("last_success")
        age = (now - dt.datetime.fromisoformat(ls)).total_seconds() / 86400 if ls else None
        stale = s.get("state") != "skipped" and (age is None or age > STALE_AFTER[k] or s.get("state") == "error")
        status.append({"key": k, "label": label, "state": s.get("state", "never run"),
                       "last_success": ls, "age_days": age, "stale": stale,
                       "note": s.get("note"), "error": s.get("error")})
    out["status"] = status

    # ---- token prices --------------------------------------------------------------------
    orm = rd("openrouter_models.csv")
    by_date_model = {(r["date"], r["model"]): blended(num(r["prompt"]) or 0, num(r["completion"]) or 0, ratio)
                     for r in orm if num(r["prompt"]) is not None}
    dates = sorted({d for d, _ in by_date_model})
    tiers = {}
    for t, spec in models["tiers"].items():
        pts = []
        for d in dates:
            vals = [by_date_model[(d, m)] for m in spec["models"] if (d, m) in by_date_model]
            if vals:
                pts.append((d, median(vals) * 1e6))
        tiers[t] = {"models": spec["models"], "series": series(pts)}

    ep = rd("openrouter_endpoints.csv")
    ref_pts, ref_spread, endpoint_latest = defaultdict(list), [], {}
    for r in ep:
        b = blended(num(r["prompt"]) or 0, num(r["completion"]) or 0, ratio)
        if r["model"] == ref:
            ref_pts[r["date"]].append(b)
    ref_series = series((d, median(v) * 1e6) for d, v in ref_pts.items())
    for d, v in sorted(ref_pts.items()):
        ref_spread.append([d, min(v) * 1e6, median(v) * 1e6, max(v) * 1e6, len(v)])
    if ep:
        last = max(r["date"] for r in ep)
        for m in models["endpoint_models"]:
            rows = [r for r in ep if r["date"] == last and r["model"] == m]
            endpoint_latest[m] = sorted(
                [{"provider": r["provider"], "quant": r["quantization"],
                  "usd_per_mtok": blended(num(r["prompt"]) or 0, num(r["completion"]) or 0, ratio) * 1e6}
                 for r in rows], key=lambda x: x["usd_per_mtok"])

    otpi = defaultdict(list)
    for r in rd("ornn_otpi.csv"):
        otpi[r["lab"]].append((r["date"], num(r["usd_per_mtok"])))
    otpi_class = defaultdict(list)
    for r in rd("ornn_otpi_class.csv"):
        otpi_class[r["class"]].append((r["date"], num(r["usd_per_mtok"])))

    epoch = defaultdict(list)
    for r in rd("epoch_price_backfill.csv"):
        epoch[f"{r['benchmark']} ≥ {r['threshold_model']}"].append(
            [r["date"], num(r["usd_per_mtok"]), r["model"]])
    for v in epoch.values():
        v.sort()

    aa = rd("artificial_analysis.csv")
    aa_tiers = {}
    if aa:
        for t, spec in models["tiers"].items():
            pts = defaultdict(list)
            for r in aa:
                ii, p = num(r["intelligence_index"]), num(r["usd_per_mtok_blended"])
                if ii is not None and p and ii >= spec["aa_min_intelligence"]:
                    pts[r["date"]].append(p)
            aa_tiers[t] = series((d, min(v)) for d, v in pts.items())

    out["tokens"] = {
        "tiers": tiers, "aa_tiers": aa_tiers,
        "reference": {"model": ref, "series": ref_series, "spread": ref_spread},
        "endpoints_latest": endpoint_latest,
        "otpi_labs": {k: series(v) for k, v in otpi.items()},
        "otpi_class": {k: series(v) for k, v in otpi_class.items()},
        "epoch_backfill": dict(epoch),
    }

    # ---- GPU rent ------------------------------------------------------------------------
    ocpi = defaultdict(list)
    for r in rd("ornn_ocpi.csv"):
        ocpi[r["gpu"]].append((r["date"], num(r["usd_per_gpu_hour"])))
    ocpi = {k: series(v) for k, v in ocpi.items()}
    vast = defaultdict(lambda: {"median": [], "min": [], "offers": []})
    for r in rd("vast.csv"):
        for f in ("median", "min"):
            vast[r["gpu"]][f].append([r["date"], num(r[f])])
        vast[r["gpu"]]["offers"].append([r["date"], int(r["offers"])])
    out["gpu"] = {"ocpi": ocpi, "vast": dict(vast),
                  "chg": {g: {"d7": pct_change(s, 7), "d30": pct_change(s, 30), "d90": pct_change(s, 85)}
                          for g, s in ocpi.items()}}

    # ---- term curve ----------------------------------------------------------------------
    h100 = ocpi.get("H100 SXM", [])
    spot = h100[-1][1] if h100 else None
    term = []
    for e in manual.get("term_curve") or []:
        g = e.get("gpu")
        s = ocpi.get(g, h100)
        sp = s[-1][1] if s else None
        term.append({**{k: (v.isoformat() if isinstance(v, dt.date) else v) for k, v in e.items()},
                     "spot_ref": sp, "spot_ref_gpu": g if g in ocpi else "H100 SXM",
                     "spot_over_term": (sp / e["usd_per_gpu_hour"]) if sp and e.get("usd_per_gpu_hour") else None})
    out["term"] = {"spot_h100": spot, "entries": term}

    # ---- required throughput index -------------------------------------------------------
    h100_by_d = dict(h100)

    def rent_on(d):
        ks = [k for k in h100_by_d if k <= d]
        return h100_by_d[max(ks)] if ks else None

    req_ref = []
    for d, p in ref_series:  # p in USD/Mtok
        r = rent_on(d)
        if r and p:
            req_ref.append([d, r / (p / 1e6)])
    req_open = []
    for d, p in series(otpi_class.get("open", [])):
        r = rent_on(d)
        if r and p:
            req_open.append([d, r / (p / 1e6)])
    out["throughput"] = {
        "reference": {"tokens_per_gpu_hour": req_ref, "index": index_to_100(req_ref)},
        "otpi_open": {"tokens_per_gpu_hour": req_open, "index": index_to_100(req_open)},
        "anchors": [{**a, "date": str(a.get("date"))} for a in hw.get("efficiency_anchors") or []],
    }

    # ---- H100 retirement signal and rent per kW ------------------------------------------
    perf, kw = hw["relative_inference_perf"], hw["kw_per_gpu"]
    b200 = dict(ocpi.get("B200", []))
    ret = [[d, (v / b200[d]) * (perf["B200"] / perf["H100 SXM"])] for d, v in h100 if d in b200 and b200[d]]
    out["retirement"] = {"series": ret, "latest": ret[-1][1] if ret else None}
    out["rent_per_kw"] = {g: [[d, v / kw[g]] for d, v in s] for g, s in ocpi.items() if g in kw}
    out["rent_per_work"] = {g: [[d, v / perf[g]] for d, v in s] for g, s in ocpi.items() if g in perf}

    # ---- regime ------------------------------------------------------------------------------
    tok_open = series(otpi_class.get("open", []))
    tok_closed = series(otpi_class.get("closed", []))
    out["regime"] = {
        "primary": regime(pct_change(tok_open, 30), pct_change(h100, 30)),
        "closed": regime(pct_change(tok_closed, 30), pct_change(h100, 30)),
        "token_series": "Ornn OTPI open-weights class",
        "rent_series": "Ornn OCPI H100 SXM",
        "history": [],
    }
    # rolling regime history (each day vs 30 days earlier), for the review phase
    for i, (d, _) in enumerate(tok_open):
        tc = pct_change(tok_open[: i + 1], 30)
        rc = pct_change([p for p in h100 if p[0] <= d], 30)
        g = regime(tc, rc)
        if g:
            out["regime"]["history"].append([d, g["token"], g["rent"]])

    # ---- power -----------------------------------------------------------------------------
    this_month = dt.date.today().isoformat()[:7]
    dc = rd("epoch_dc_power.csv")
    out["power"] = {
        "epoch_dc": [[r["month"], num(r["power_mw"]), num(r["h100e"]), r["month"] > this_month] for r in dc],
        "eia_price": defaultdict(list), "eia_demand_yoy": {},
        "manual": [{**m, "date": str(m.get("date"))} for m in manual.get("power_manual") or []],
    }
    for r in rd("eia_retail_price.csv"):
        out["power"]["eia_price"][r["state"]].append([r["month"], num(r["cents_per_kwh"])])
    out["power"]["eia_price_12m"] = {}
    for stt, v in out["power"]["eia_price"].items():
        v.sort()
        vals = [x[1] for x in v]
        # 12-month average price vs the 12 months before: strips the summer/winter swing
        out["power"]["eia_price_12m"][stt] = [
            [v[i][0], (sum(vals[i - 11:i + 1]) / 12) / (sum(vals[i - 23:i - 11]) / 12) - 1]
            for i in range(23, len(v)) if None not in vals[i - 23:i + 1]]
    dem = defaultdict(dict)
    for r in rd("eia_demand.csv"):
        if num(r["demand_mwh"]):
            dem[r["region"]][r["date"]] = num(r["demand_mwh"])
    for reg, byd in dem.items():
        # trailing 365-day total vs the 365 days before it: strips weather and season, leaving
        # structural load growth. The last 2 days are dropped (EIA revises them).
        ds = sorted(byd)[:-2]
        pts = []
        for i in range(len(ds) - 1, 729, -7):
            end = dt.date.fromisoformat(ds[i])
            cur = [v for k in range(365) if (v := byd.get((end - dt.timedelta(days=k)).isoformat()))]
            prv = [v for k in range(365) if (v := byd.get((end - dt.timedelta(days=k + 365)).isoformat()))]
            if len(cur) < 355 or len(prv) < 355:
                continue  # compare daily averages so an odd missing day doesn't break the window
            pts.append([ds[i], (sum(cur) / len(cur)) / (sum(prv) / len(prv)) - 1])
        out["power"]["eia_demand_yoy"][reg] = sorted(pts)

    # ---- capex -----------------------------------------------------------------------------
    ed = rd("edgar.csv")
    capex = defaultdict(list)
    revenue = defaultdict(list)
    for r in ed:
        (capex if r["metric"] == "capex" else revenue)[r["ticker"]].append([r["period_end"], num(r["usd"]) / 1e9])
    for v in list(capex.values()) + list(revenue.values()):
        v.sort()
    total = defaultdict(dict)
    for t in HYPERSCALERS:
        for d, v in capex.get(t, []):
            total[d][t] = v
    big4 = [[d, sum(v.values())] for d, v in sorted(total.items()) if len(v) == len(HYPERSCALERS)]
    out["capex"] = {"by_company": dict(capex), "revenue": dict(revenue), "big4_total": big4,
                    "guidance": [{**g, "date": str(g.get("date"))} for g in manual.get("capex_guidance") or []],
                    "cowos": [{**g, "date": str(g.get("date"))} for g in manual.get("cowos_capacity") or []],
                    "hbm": [{**g, "date": str(g.get("date"))} for g in manual.get("hbm_supply") or []]}

    out["signals"], out["scenario"] = signals(out, ocpi, tok_open, big4)

    # daily log of each signal's status and tripwire progress, for "what changed" over time
    from common import merge_csv
    d0 = dt.date.today().isoformat()
    prog = {t["id"]: t["progress"] for t in out["scenario"]["tripwires"]}
    merge_csv("signals_history.csv",
              [{"date": d0, "id": x["id"], "status": x["status"],
                "progress": "" if prog.get(x["id"]) is None else round(prog[x["id"]], 4)} for x in out["signals"]],
              ["date", "id"], ["date", "id", "status", "progress"])
    hist = defaultdict(list)
    for r_ in rd("signals_history.csv"):
        hist[r_["id"]].append([r_["date"], r_["status"], num(r_["progress"])])
    out["signal_history"] = {k: sorted(v) for k, v in hist.items()}

    # named companies; token sellers are topped up with today's OpenRouter providers
    comp = load_yaml("companies.yml")
    tick = comp.get("public_providers") or {}
    live = defaultdict(set)
    if ep:
        last_d = max(r["date"] for r in ep)
        for r in ep:
            if r["date"] == last_d:
                live[r["provider"]].add(r["model"].split("/")[-1])
    out["companies"] = {
        "groups": comp["groups"],
        "live_sellers": sorted(({"name": k, "ticker": tick.get(k), "models": sorted(v)} for k, v in live.items()),
                               key=lambda x: (x["ticker"] is None, -len(x["models"]), x["name"])),
        "live_models": models["endpoint_models"],
    }

    OUT.write_text(json.dumps(out, separators=(",", ":"), default=str))
    print(f"wrote {OUT.relative_to(ROOT)} ({OUT.stat().st_size / 1024:.0f} KB)")
    reg = out["regime"]["primary"]
    if reg:
        print(f"regime: token {reg['token']} ({reg['token_chg_30d']:+.1%}), "
              f"rent {reg['rent']} ({reg['rent_chg_30d']:+.1%}) -> {reg['reading']}")


if __name__ == "__main__":
    build()
