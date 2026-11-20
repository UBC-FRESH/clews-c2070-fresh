# UI QA report — iteration 0, fix pass 1

**Date:** 2026-11-19 (post-fix rerun)
**Scope:** real Chromium rendering and interaction QA of `ui/` served over HTTP,
after fixing the six defects in the original iteration-0 pass. No data files
were changed.
**Result:** **PASS.** All six defects are fixed. All six views render; scenario
selection, trade-off point selection, map framing, OSeCAN ledger, and mobile
navigation work. 0 console errors, 0 page errors, 0 failed requests, 0 HTTP
responses ≥ 400.

## Environment / commands

| Item | Version / path |
|---|---|
| Python | 3.12.3 (`/usr/bin/python3`) |
| Playwright (Python package) | 1.65.2 |
| Playwright bundled driver | 1.65.1 |
| Chromium | 159.0.8154.6 (`chromium-1255`) |
| Pillow (pixel checks) | 13.0.0 |
| Isolated environment | `/tmp/ui-qa-venv` |
| HTTP server | `python3 -m http.server 8012 --bind 127.0.0.1 -d ui` |

Chromium launch flags:

```text
--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --disable-dev-shm-usage
```

Layout checks used viewports 1440×1200 (desktop) and 390×844 (mobile,
`is_mobile`, `has_touch`). Full-page screenshots were written to `ui/qa/`.

## Before / after — the six defects

| # | Severity | Before | After | Evidence |
|---|---|---|---|---|
| 1 | HIGH | `#osecan-family-table` had **0** family `<details>` rows. The `+=` expression read the pre-map value while its callback appended rows, then overwrote the result. | **15** `details.family-row` rows render (all 15 scenario families), each with status badge and source directory. | `osecan.png`; rows=15; family labels captured in `qa_results.json` |
| 2 | HIGH | Overview badges were **8.48 px**, light text on cream, ~1.1:1 contrast. | Badges are **12 px**, dark text on pale status-coloured backgrounds. Measured contrast: **9.79:1** (post-fix / publishable) and **9.14:1** (trade-off eligible), both above WCAG AA 4.5:1. Family/status colours remain distinct. | `desktop.png`, `mobile.png`; computed styles in `qa_results.json` |
| 3 | MEDIUM | Trade-off x-axis repeated `1M`; dispatch y-axis repeated `3k`/`2k`. | Adaptive tick formatter: trade-off x = `1.320M … 1.420M`; dispatch y = `0, 500, 1.0k, 1.5k, 2.0k, 2.5k, 3.0k, 3.5k`; trade-off y = `0.44 … 0.56`. Every adjacent label is unique. | `tradeoff.png`, `dispatch.png`; `axes` block in `qa_results.json` |
| 4 | MEDIUM | Default MapLibre view `center [-101, 58]`, `zoom 2.6` cut eastern QC/Labrador/NL (east edge ~-59.1°). | Map now calls `fitBounds([[-141, 41], [-52, 84]])` on load, the same national fit as the **Fit to Canada** button (button kept). Measured post-load bounds: west **-164.59°**, east **-28.41°**; St John's, NL projects inside the 722×725 canvas at (593, 643). | `map.png`, `map-fit.png`; `bounds` block in `qa_results.json` |
| 5 | LOW | ECharts trade-off `click` fired but did not select a scenario. | ECharts `click` now calls `selectScenario(meta.id)`, so the overview, rail ring, hash, and trade-off ring update. Verified click selected `wind_integration_audit__efix_tier_200`, overview masthead changed, exactly 1 rail row selected, hash `#tradeoff/wind_integration_audit__efix_tier_200`. | `tradeoff.png`; `tradeoff_selection` block |
| 6 | LOW | At 390 px the header was 494 px, rail 635 px, overview began ~1,128 px down; **Inputs** tab was off-screen in a horizontal scroller. | Mobile header is now **291 px**; the rail is a **46 px** collapsed toggle (`aria-expanded=false`, opens to `max-height:35vh`); overview starts at **457 px**. The tab bar is a 3×2 grid, all six tabs fully visible (`tabbar.scrollWidth 278 = clientWidth 278`), and Inputs activates. Desktop layout unchanged. | `mobile.png`; `mobile` block in `qa_results.json` |

## Screenshots

| Screenshot | Viewport | Actual image | Result |
|---|---|---|---|
| `desktop.png` | 1440×1200 | 1440×1610 | Overview, readable badges |
| `mobile.png` | 390×844 | 390×2904 | Collapsed rail, compact header, 3×2 tabs |
| `dispatch.png` | 1440×1200 | 1440×1610 | Unique y-axis ticks |
| `tradeoff.png` | 1440×1200 | 1440×1610 | Unique x/y ticks, point click selection ring |
| `map.png` | 1440×1200 | 1440×1610 | National extent, eastern tiles visible |
| `osecan.png` | 1440×1200 | 1440×2764 | 15 family ledger rows, readable badges |
| `map-fit.png` | — | 723×726 (canvas) | Fit-to-Canada canvas evidence |

Screenshot total is ~3.9 MB (<10 MB), so screenshots remain committed.

## Checks

Header contract: **47 scenarios**, **26 trade-off eligible**, rail **47 of 47
shown**, **16** family chips (All + 15).

Tabs: all six (Overview, Dispatch, Trade-off, Map, OSeCAN Links, Inputs)
activate, set `aria-selected`, expose exactly one visible panel, and update the
hash.

Scenario rail: selected `emission_factor_fix__efix_baseline_119m`; masthead
label matched exactly, 1 row selected, hash
`#overview/emission_factor_fix__efix_baseline_119m`.

Trade-off chart: 26 points across 7 family series; guard hidden; count reads
`26 eligible · 21 excluded`. Point click now loads the clicked scenario and
rings it in the rail and chart.

OSeCAN: national 109.42 PJ, BC 43.53 PJ, rest 65.89 PJ, supply cost $2.457/GJ;
tier sum validated at 213.538 PJ with residual 0.000000 PJ; **15** family
ledger rows; 5 tier rows; input and SUPBCW tables populated.

Map: 13 provinces / 36 ecoprovinces / 385 RSYC tiles, 2/13 anchors, 11 zone
gaps; 11 gap chips (A1, A2, L1, M1, N1, O1, O2, Q1, Q2, S1, T1); national fit on
load and working Fit to Canada button; eastern tiles and St John's, NL visible.

Inputs: harvest ECharts canvas renders; 5 tier rows; 14 managed-landbase rows;
summary cards populated.

Horizontal overflow: none. `scrollWidth === innerWidth` at 1440/1440 and
390/390.

Runtime console/network:

- **0** page errors.
- **0** failed requests.
- **0** HTTP responses ≥ 400.
- **0** console errors.
- **4** console warnings, all the SwiftShader `GPU stall due to ReadPixels`
  message (headless software-GL artefact, not an application warning).

## Files changed

- `ui/app.js`: OSeCAN ledger row construction; adaptive `fmtAxis()` formatter
  (dispatch y, trade-off x/y); trade-off chart click → `selectScenario()`; map
  national `fitBounds` on load; mobile rail toggle binding/count.
- `ui/index.html`: mobile rail toggle button and controlled rail body; updated
  trade-off guidance copy (“click a point or select in the rail”).
- `ui/styles.css`: WCAG-AA badge typography/colours on cream surfaces; mobile
  header compaction; collapsible mobile rail; 3×2 mobile tab grid.
- `ui/qa/QA_REPORT.md`, `ui/qa/qa_results.json`, and the seven screenshots.

No data files, manifests, or outputs were modified. `git status --porcelain`
contains no `D` entries.

---

# Milestone 1c UI QA — live runner wiring

**Date:** 2026-11-20
**Scope:** real Chromium QA of the new **Run** tab, static fallback, FastAPI
create→queue→SSE→output flow, session-rail/overview/trade-off/dispatch
integration, and the failed-run path. The runner was a local fake/dry-run
script, not a four-minute OSeCAN solve.

## Environment

| Item | Version / path |
|---|---|
| Chromium | Playwright 1.65.2 bundled (`/tmp/ui-qa-venv`) |
| Static server | `python3 -m http.server 8015 -d ui` |
| Live server | `.venv/bin/python -m workflows.ui.server --port 8014 --runtime-dir /tmp/ui-run-qa/runtime --allow-runs --runner-command ".../python /tmp/ui-run-qa/fake_runner.py"` |
| Browser flags | `--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --disable-dev-shm-usage` |

The fake runner reads the immutable `--scenario` snapshot, sleeps briefly so SSE
logs are observable, writes 200 `dispatch_by_zone.csv` rows and
`run_summaries.json` with `screening_only: true`, `policy_grade: false`, and
exits 1 with a failed summary when the scenario id/label contains `fail`.
Therefore a successful 4-minute solve is **not** claimed or exercised here.

## Results

| Check | Result |
|---|---|
| Static fallback (`/api/health` 404) | **PASS** — badge `Static mode · no backend`; 47 committed scenarios; create/queue and OSeCAN queue disabled; existing precomputed views still render. Expected console entry: one 404 for the health probe. |
| Live detection/defaults | **PASS** — badge `Live API · runner ready`; `/api/constraints` defaults populate harvest 119, recovery 0.60, carbon 115, PWRBIO 0.05, nuclear 0, T3 39.873714, T4 64.265464. |
| Create | **PASS** — form creates `clews.scenario.v1` with a slugged id and enables **Queue run**. |
| Queue + SSE | **PASS** — 202 accepted; event log shows `started`, runner snapshot/log lines, and terminal `completed`; elapsed timer updates. |
| Output | **PASS** — `/api/runs/{id}/output` renders objective 123.50 M$, cumulative emissions 0.500 Mt, national BCW 30.00 PJ, PWRBCW 5.00 PJ; source path shown. |
| Session rail/overview | **PASS** — completed run appears under **Session runs**, selects into a live overview with `screening_only` and `policy_grade=false` badges and no publishable claim. |
| Trade-off | **PASS** — count includes `+1 live session run(s), screening only`; live point series is present. |
| Dispatch | **PASS** — **Open dispatch** renders the ECharts canvas from the live columnar output; source note reports the live `dispatch_by_zone.csv`. |
| Failed run | **PASS** — failed status and error message shown; no metrics, no rail entry, no trade-off point, no dispatch. |
| Mobile 390×844 | **PASS** — 7 tabs visible in a 4-column grid, Run panel opens, `scrollWidth === innerWidth`, no page errors. |
| Console/page errors (live page) | **0** page errors, **0** console errors, **0** HTTP responses ≥400. |

## Screenshots

| File | Content |
|---|---|
| `run-static.png` | Static-mode badge, disabled Run form, precomputed rail intact |
| `run-complete.png` | Completed run, SSE log, screening-only output card, session rail entry |
| `run-error.png` | Failed run, error log, and explicit “No fake results” card |

Machine-readable evidence is in `ui/qa/run_qa_results.json`. Screenshots were
captured with the sticky tab bar made static and the skip link hidden for the
capture only (Playwright full-page stitching artefacts); the application styles
are unchanged.

## Files exercised

- `ui/api.js` — health/constraints/scenario/run/SSE/output client.
- `ui/app.js` — Run tab state machine, session rail, live overview/trade-off/
  dispatch integration, static fallback.
- `ui/index.html`, `ui/styles.css` — Run panel, mode badges, responsive layout.
- `workflows/ui/server.py` — `/api/constraints`, `/api/runs/{id}/output`,
  `--runner-command`.
- `tests/test_ui_scenario_api.py` — endpoint and override tests.

No data files, manifests, or committed static outputs were modified.
`git status --porcelain` contains no `D` entries.
