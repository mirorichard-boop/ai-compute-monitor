# AI Compute Monitor

Static dashboard testing one thesis: falling token prices drive AI demand and may squeeze the middle of the stack. It tracks token price against GPU rent, power, and supply. A daily GitHub Action runs the collectors, rebuilds `docs/data.json`, and commits; GitHub Pages serves `docs/`.

Spec: `Oz's Vault/Investing/AI Compute Dashboard Spec.md`.

## Layout

| Path | What |
|---|---|
| `collectors/` | One script per source; each merges into an append-only CSV in `data/`. `run_all.py` runs them and writes `data/status.json`. |
| `data/` | Append-only CSVs, keyed per row. Never hand-edit. |
| `config/models.yml` | Tracked models, tier baskets, reference model, blend ratio |
| `config/hardware.yml` | Relative inference perf, kW per GPU, efficiency anchors (**placeholders, need sources**) |
| `config/manual.yml` | Term-curve deals, capex guidance, CoWoS, HBM, power auctions |
| `config/companies.yml` | Named companies per part of the stack, each with its reason for exposure. Chip owners that sell AI output are added daily from OpenRouter's provider list. |
| `build/build.py` | Derives all metrics, writes `docs/data.json` |
| `docs/index.html` | The page; reads `data.json` from the same origin |

## Run locally

```bash
python3 collectors/run_all.py          # all sources; or name some: run_all.py ornn vast
python3 build/build.py
python3 -m http.server 8766 -d docs    # open http://localhost:8766
```

Keys go in a gitignored `.env` (`AA_API_KEY=…`, `EIA_API_KEY=…`, `SEC_USER_AGENT="name email"`) or the environment. Missing keys skip that source; they never fail the run.

## Sources (tested 2026-10-18)

| Source | Status | Notes |
|---|---|---|
| Ornn OCPI | working, no key | `data.ornn.com/api/public-index/gpu/{gpu}/index-history`: 3 months daily for H100 SXM, H200, B200, A100 SXM4. Re-pulled daily and merged, so history grows past 3 months. |
| Ornn OTPI | working, no key | `api.ornnai.com/api/otpi` (4 labs) and `/otpi/weight-class` (closed vs open). 3-month backfill. Drives the regime flag. |
| OpenRouter | working, no key | `/models` list prices; `/models/{id}/endpoints` per-provider prices. Throughput fields are null, so not collected. |
| Vast.ai | working, no key | `/api/v0/bundles/` offer search, verified hosts. Floor series. |
| Epoch AI | working | Price-trend CSV (backfill, ends 2025-02) and Frontier Data Centers timelines. The benchmark hub zip has no prices. |
| SEC EDGAR | working | companyfacts API. AMZN/NVDA use `PaymentsToAcquireProductiveAssets`. NVDA data-center segment not exposed; total revenue used. |
| EIA | working, key | Industrial price by state (monthly) and PJM/ERCOT daily load. Needs `EIA_API_KEY`. |
| Artificial Analysis | written, untested | Needs `AA_API_KEY`. Field names follow v2 docs, parsed defensively. |
| Ornn forward curve | paid | `/api/forward` needs a key. Term curve stays manual. |
| Aggregators | not built | computeprices.com / getdeploying.com: terms not reviewed. |

## Deploy

1. Create the GitHub repo and push.
2. Settings → Pages → Deploy from branch `main`, folder `/docs`.
3. Settings → Secrets → Actions: `SEC_USER_AGENT` (e.g. `ai-compute-monitor you@example.com`), and `EIA_API_KEY` / `AA_API_KEY` when you have them.
4. Actions → Daily data update → Run workflow, once, to confirm.
