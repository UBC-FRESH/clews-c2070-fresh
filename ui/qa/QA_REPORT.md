# UI QA report — iteration 0, fix pass 1

> **Historical note (2026-11-20):** the sections below record the pre-fleet
> bundle (47 scenarios, 26 trade-off eligible) and the intermediate
> one-canonical fleet pass. The current bundle is 51 scenarios / 17 families /
> 1 Pareto-eligible scenario (canonical 119 Mm³) plus 3 harvest sensitivity/
> compare rows; see the **Fleet-correction canonicalization**, **Harvest ladder
> export**, and **Harvest feasibility gate + policy selector** sections at the
> end of this file.

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

Header contract (pre-fleet bundle, superseded 2026-11-20): **47 scenarios**,
**26 trade-off eligible**, rail **47 of 47 shown**, **16** family chips
(All + 15).

Tabs: all six (Overview, Dispatch, Trade-off, Map, OSeCAN Links, Inputs)
activate, set `aria-selected`, expose exactly one visible panel, and update the
hash.

Scenario rail: selected `emission_factor_fix__efix_baseline_119m`; masthead
label matched exactly, 1 row selected, hash
`#overview/emission_factor_fix__efix_baseline_119m`.

Trade-off chart: 26 points across 7 family series; guard hidden; count reads
`26 pre-fleet eligible · 21 excluded`. Point click now loads the clicked scenario and
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
logs are observable, writes 300 `dispatch_by_zone.csv` rows (250 normalized
dispatch/supply rows plus 50 `category=other` audit rows) and
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
| Output | **PASS** — `/api/runs/{id}/output` renders objective 123.50 M$, cumulative emissions 0.500 Mt, national BCW 30.00 PJ, PWRBCW 4.00 PJ; source path is run-relative (`output/dispatch_by_zone.csv`). API metrics also expose PWRBIO 5.00 PJ and bio share 0.172413793 (PWRBIO ÷ PWR excluding PWRTRN). |
| Session rail/overview | **PASS** — completed run appears under **Session runs**, selects into a live overview with `screening_only` and `policy_grade=false` badges and no publishable claim. |
| Trade-off | **PASS** — count includes `+1 live session run(s), screening only`; live point series is present. |
| Dispatch | **PASS** — **Open dispatch** renders the ECharts canvas from the live columnar output; source note reports the live `dispatch_by_zone.csv`. |
| Failed run | **PASS** — failed status and error message shown; no metrics, no rail entry, no trade-off point, no dispatch; form is re-enabled. |
| Concurrent-run guard | **PASS** — while the queued run is active, `#run-create`, `#run-queue`, and `#osecan-queue` are disabled; double-clicking queue/create adds exactly **1** run. |
| Mobile 390×844 | **PASS** — 7 tabs visible in a 3-column grid (`scrollWidth === clientWidth === 278`), including `OSeCAN Links` unclipped; Run panel opens, no page errors. |
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

## Milestone-1c review fix pass (2026-11-20)

Re-ran the same Chromium flow against the fixed tree with the live server on
`127.0.0.1:8013` and static fallback on `127.0.0.1:8012`:

| Check | Result |
|---|---|
| `category=other` dispatch rows | **PASS** — runner wrote 50 `other` audit rows; `/output` returned 250 normalized rows and categories `["dispatch", "supply"]`. |
| PWRBCW vs PWRBIO vs PWRTRN | **PASS** — output card shows PWRBCW 4.00 PJ; API metrics expose `pwrbcw_2050=4`, `pwrbio_2050=5`, `bio_share_2050=0.172413793`; PWRTRN is excluded from the share denominator. |
| Non-completed output | **PASS** — failed run renders the explicit “No result — failed” card; `/output` returns 409. |
| Path handling | **PASS** — output source is `output/dispatch_by_zone.csv`; no `/tmp` host path is exposed. |
| Mode messaging | **PASS** — static fallback and live mode show distinct copy; no “Live runner available” claim in static/degraded/read-only states. |
| Concurrent runs | **PASS** — create/queue/OSeCAN queue stay disabled while `activeRun` is set; exactly one run was created after repeated clicks. |
| Mobile tab bar | **PASS** — 3-column grid at 390×844 keeps all seven labels inside the tab bar. |

Updated screenshots: `run-static.png`, `run-complete.png`, `run-error.png`, and
new `run-mobile.png`. Machine-readable evidence: `ui/qa/run_qa_results.json`.

## Milestone-1d Explore pass (2026-11-20)

**Scope:** new Explore view — parallel coordinates, Pareto front, and A/B
comparison/delta — over the 26 published post-fix scenarios plus live session
runs. Static fallback only; no data files were changed. `node --check` passes
for `ui/app.js` and `ui/api.js`; the existing UI pytest suite is green (86
passed).

**Environment:** Chromium via `/tmp/ui-qa-venv`, Playwright 1.65.2, static
server `python3 -m http.server 8017 --bind 127.0.0.1 -d ui`, viewports
1440×1200 and 390×844 (`is_mobile`, `has_touch`). Browser flags:
`--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist
--disable-dev-shm-usage`.

### Results

| Check | Result |
|---|---|
| Explore tab | **PASS** — 8 tabs total; Explore activates, sets `aria-selected`, exposes exactly one panel, and updates the hash. |
| Parallel coordinates | **PASS** — **26** plotted scenario paths over **11** axes (objective, emissions, national BCW 2050, PWRBCW 2050, bio share, harvest, carbon price, recovery, PWRBIO cap, nuclear cap, wind cap/integration). |
| Brushing / filtering | **PASS** — vertical drag on the emissions axis filtered to **4 of 26** visible, dimmed 22 paths, added one removable filter chip, and reset returned to 26 of 26. Axis click also clears its brush. |
| Filter chips / form | **PASS** — removable chips; keyboard range form applies and rejects inverted ranges (`minimum must not exceed maximum`) without changing data. |
| URL/hash state | **PASS** — the brush range, Pareto projections, and A/B pair are encoded in `#explore/...?brush=...`; a fresh page load restores the chip and the 4-of-26 status. |
| Pareto front | **PASS** — defaults minimize objective, minimize cumulative emissions, maximize national BCW 2050; **17 on front / 26 evaluated** with no missing metrics. Dominance is computed on min–max normalized values. |
| Pareto axes | **PASS** — projection X/Y and third objective are selectable and swap rather than silently duplicating a metric. |
| Pareto click / badge | **PASS** — clicking a front point selected `emission_factor_fix__efix_baseline_119m`, updated the rail, overview, hash, and displayed the **ON FRONT** badge. |
| Pareto CSV | **PASS** — export downloaded `pareto_front_iteration0.csv` with header `rank,scenario_id,label,family,grade,…` and **18 rows** (17 front rows + header). |
| Comparison/delta | **PASS** — A/B selects, **6** metric rows, absolute and relative deltas, unit labels per row; defaults A `emission_factor_fix__efix_stress_119m`, B `pwrbio_cap__pwrbio_cap10_windint3_tier200`. First objective delta `+7,546.71` (`+0.6%`). |
| Differing dimensions | **PASS** — **2 of 10** differ for the default pair (PWRBIO cap, wind cap/integration), listed as explicit chips. |
| Dispatch overlay | **PASS** — optional overlay rendered an ECharts canvas for the electricity ledger (`Annual national PJ electricity · 2021—2070`); toggling hides it. Fuel-ledger absence is stated, not imputed. |
| Live/session separation | **PASS (code path)** — live rows are dashed amber in parallel coordinates, diamonds/`screening_only` in tooltips and comparison badges; they are appended to Explore but never added to `state.eligible` or the 26-ID pre-fleet ledger. No live runner was started in this static pass. |
| Mobile 390×844 | **PASS** — 8 tabs in a 4×2 grid (`scrollWidth === clientWidth === 303`), page width 390 with no horizontal overflow; parallel chart scrolls horizontally (968 px content / 332 px viewport); comparison table scrolls inside its wrapper (686 / 322). |
| Console/page/HTTP errors | **PASS (unexpected)** — 0 page errors, 0 failed requests, 0 unexpected console errors or HTTP responses. The only console/HTTP entry is the intentional static-mode `/api/health` **404** used to select the static badge. |

### Screenshots

| File | Content |
|---|---|
| `parallel.png` | 11 parallel axes, emissions brush band, selected scenario, 22 dimmed paths, filter chip, 4-of-26 status |
| `pareto.png` | 17-point highlighted front over 26 evaluated scenarios, selected point + ON FRONT badge |
| `compare.png` | A/B table with absolute and relative deltas, units, and 2-of-10 differing-dimension chips |
| `explore-mobile.png` | 390 px Explore panel: 4×2 tab grid, horizontally scrollable parallel chart and comparison table |

Machine-readable evidence is in `ui/qa/qa_results.json` under `explore`.
Screenshots were cropped from a full-page capture at scroll zero so the sticky
tab bar appears only once; application styles were not altered for capture.

### Live integration spot-check

A separate QA-only run used the existing fake runner
(`/tmp/ui-run-qa/fake_runner.py`, not a solver) behind
`workflows.ui.server --port 8018 --allow-runs`. The UI reported
`LIVE API · RUNNER READY`, created and completed one session run, and then
opened Explore:

| Check | Result |
|---|---|
| Explore rows | **PASS** — 27 parallel paths = 26 static eligible + **1** live session run; the live path carries `pc-line--live` and the legend gains `Live session · screening_only`. |
| Pareto | **PASS** — `18 on front · 27 evaluated`; the live run participates without entering the 26-ID pre-fleet publishable ledger. |
| Explore mode note | **PASS** — states that the live run is screening_only and excluded from the publishable ledger. |
| A/B against live | **PASS** — B set to the live run; grade badges show `LIVE · SCREENING_ONLY` and `SCREENING_ONLY · NOT POLICY GRADE`; PWRBIO 2050 appears only for the live row and missing static deltas are suppressed as `—`. |
| Errors | **0** page errors, **0** console errors in the live spot-check. |

The fake runner is QA infrastructure only and is not committed; it does not
represent a real solve or policy-grade result.

## Milestone-1e linked spatial/temporal pass (2026-11-20)

**Scope:** map drill-down and linked selection (Canada → province →
ecoprovince → RSYC tile), explicit no-zone-mapping handling, B1/B2 anchor
dispatch filtering, shared 2021–2070 year control with playback and vertical
guide, year snapshot table, URL/hash restore/reset, and mobile 390×844.
Static fallback only; no data files were changed.

**Environment:** Chromium via `/tmp/ui-qa-venv` (Playwright 1.65.2), static
server `python3 -m http.server 8021 --bind 127.0.0.1 -d ui`, viewports
1440×1200 and 390×844 (`is_mobile`, `has_touch`). Browser flags:
`--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist
--disable-dev-shm-usage`. `node --check ui/app.js ui/api.js` passes.

### Results

| Check | Result |
|---|---|
| Map click: tile | **PASS** — clicked a rendered RSYC tile; selection panel read `RSYC tile U6`, area 0.23 Mha, recoverable base 35.3 PJ, 1 RSYC tile, province Northwest Territories, ecoprovince Old Crow-Eagle Plains. |
| Map click: province | **PASS** — with the tile layer hidden, clicked province fill; panel read `Northwest Territories`, area 64.58 Mha, 7,620.0 PJ, 67 RSYC tiles. Breadcrumb `Canada / Northwest Territories`. |
| Breadcrumb / drill-down | **PASS** — Canada → British Columbia → Central Montane Cordillera → tile restores through breadcrumb buttons; root reset returns to `Canada` and clears the hash selection. |
| Selection panel | **PASS** — area, recoverable PJ, cell/tile count, province, ecoprovince, explicit species-not-joined caveat, evidence/caveats, fit/reset actions. |
| No-zone mapping message | **PASS** — every province/ecoprovince/tile selection states that no documented C2070 zone mapping exists and that dispatch/overview stay national; no zone geometry is invented. |
| B1/B2 anchor mapping | **PASS** — clicking the B1 marker selects zone B1 through its authoritative point anchor; dispatch zone filter becomes `B1` and the snapshot caption reads `Zone B1 · 2050`. The overview shows the linked-context strip and dispatch shortcut. |
| Reset zone filter | **PASS** — resetting the map selection removes `sel=` from the hash and restores dispatch zone to `ALL` (national CAN aggregate). |
| Shared year slider | **PASS** — default 2050; slider to 2060 updates the readout, hash `y=2060`, chart vertical guide (category index 39), and snapshot caption/year. |
| Play / pause | **PASS** — play advanced the year to 2062 during the QA interval and set `aria-pressed=true`; pause stopped playback and set `aria-pressed=false`. Reset returns to 2050. |
| Year-window coherence | **PASS** — narrowing the window to 2021—2055 while the shared year is 2060 marks the year outside the window; moving the shared year to 2062 expands the window to 2021—2062. Existing From/To sliders remain independent. |
| Year snapshot | **PASS** — table lists dispatch by technology family for the selected year with PJ, share, and total; it uses the same CAN/zone filter as the chart. |
| URL deep links | **PASS** — fresh load of `#map/<id>?sel=tile%3AU6&y=2060` restores tile U6 and the 2060 year; `#map/<id>?sel=zone%3AB2&y=2050` restores zone B2 and filters dispatch to B2. |
| Explore/Pareto/Compare regression | **PASS** — the existing Explore browser QA script was rerun against the new build: 26 parallel paths / 11 axes, brush filter and chip reset, Pareto front and CSV export, A/B delta table, and dispatch overlay all passed; runtime pass true. |
| Mobile 390×844 | **PASS** — 8 tabs in the 4×2 grid; map selection, time deck, dispatch chart, and snapshot render with `scrollWidth === innerWidth === 390`. |
| Console/page/HTTP errors | **PASS** — 0 page errors, 0 failed requests, 0 unexpected console or HTTP errors in all desktop/deep-link/screenshot/mobile runs. The only HTTP/console entry is the intentional static-mode `/api/health` 404. |

### Screenshots

| File | Content |
|---|---|
| `map-linked.png` | 1440×2296 — shared year deck, Canada/British Columbia/Central Montane Cordillera breadcrumb, highlighted ecoprovince, selection panel with no-zone mapping notice, layers and evidence |
| `timeline.png` | 1440×1817 — shared year 2050, play control, dispatch chart with vertical 2050 guide, and the dispatch-by-family snapshot table |
| `timeline-mobile.png` | 390 px dispatch view — time deck, guide, and snapshot table without horizontal overflow |

Machine-readable evidence is under `milestone_1e` in `ui/qa/qa_results.json`
(map click results, selection facts, zone anchor, temporal checks, deep links,
mobile overflow, runtime summary). All six runtime groups report pass.

### Files changed

- `ui/app.js`: spatial data preparation (province/ecoprovince/tile lookups,
  centroid/vertex ecoprovince assignment, tile aggregation), map click
  priority, selection source/highlight, breadcrumbs, selection panel, B1/B2
  anchor selection, dispatch/overview linked context, hash selection/year
  restore, shared temporal state, playback, chart guide, snapshot table.
- `ui/index.html`: shared year deck, map breadcrumb, selection panel, reset
  button, map year gauge, dispatch snapshot section.
- `ui/styles.css`: time deck, map breadcrumb/selection/gauge, snapshot table,
  linked-context strip, mobile rules.
- `ui/README.md`: view notes and map deep-link examples.
- `ui/qa/QA_REPORT.md`, `ui/qa/qa_results.json`, `ui/qa/map-linked.png`,
  `ui/qa/timeline.png`, `ui/qa/timeline-mobile.png`.
- `CHANGE_LOG.md`: milestone-1e entry.

No data submodule, manifest, output, or public OSeCAN surface was modified.
`git status --porcelain` contains no `D` entries.

## Milestone-1f session persistence, cancel/retry pass (2026-11-20)

**Scope:** persisted run hydration on reload, queued/running cancel, retry from
the immutable `runs/{id}/scenario.json` snapshot, `cancelled` SSE/terminal
status, run history with actions, elapsed timer, and throttled/bounded log
tails. No fake progress percentage was added.

**Environment:** Chromium via `/tmp/ui-qa-venv` (Playwright 1.65.2), live server
`workflows.ui.server --port 8025 --runtime-dir /tmp/ui-1f-qa/runtime --allow-runs
--runner-command ".../qa_runner.py"`, viewport 1440×1200 plus a 390×844 mobile
check. The QA runner is a fake local script that sleeps 3 s for labels
containing `long`; it is not a real OSeCAN solve.

### Results

| Check | Result |
|---|---|
| Running cancel | **PASS** — queued run reached `running`, then **Cancel run** returned the job as `cancelled`; the process group stopped, the event log ended with `cancelled · run cancelled by user`, and **0** completed events were appended. |
| Cancelled state | **PASS** — badge `CANCELLED`, explicit “No result — cancelled”, “process group was stopped”, and a **Retry this run** action. No session-rail result was added. |
| Retry | **PASS** — retry created a distinct run ID from the immutable snapshot; it reached `completed`, rendered the output card (`123.50 M$`, `0.500 Mt CO₂`, `30.00 PJ`), and added exactly **1** session-rail entry. History showed `completed` + `cancelled`. |
| Reload hydration | **PASS** — after `page.reload()`, history rehydrated **2 runs** (completed + cancelled), the completed retry output was fetched again, and the session rail had exactly **1** live entry (unique IDs = 1; no duplicate). |
| Elapsed/log UX | **PASS** — elapsed timer runs from server `started_utc`/`created_utc`; queued/running copy states there is no progress estimate; log tail is capped at 250 lines and rendered via a 180 ms throttle. |
| Mobile 390×844 | **PASS** — `innerWidth = scrollWidth = 390`; Run history rows stack and remain readable. |
| Errors | **PASS** — 0 console errors, 0 page errors. |

### Screenshots

| File | Content |
|---|---|
| `cancel.png` | Running run cancelled: cancelled badge, terminal log, “No result — cancelled”, Retry action, cancelled history row |
| `retry.png` | Retried run completed from the snapshot: output card, completed + cancelled history rows, one live session rail entry |
| `reload-hydrated.png` | After reload: 2-run history and exactly 1 deduplicated hydrated session-rail result |

Machine-readable evidence is under `milestone_1f` in
`ui/qa/run_qa_results.json`.

### Files changed

- `workflows/ui/server.py`: `cancelled` terminal status/event, supervisor
  cancel with queued removal and pre-SIGTERM terminal write, cancel/retry
  endpoints, immutable-snapshot retry, hydrated `GET /api/runs` payloads.
- `ui/api.js`: `listRuns()`, `cancelRun()`, `retryRun()`, `cancelled` SSE event.
- `ui/app.js`: reload hydration, deduplicated session runs, run history,
  cancel/retry actions, cancelled state, server-time elapsed timer, throttled
  bounded log rendering; fixed no-hash startup parsing.
- `ui/index.html`, `ui/styles.css`: run status note/actions and run history.
- `tests/test_ui_scenario_api.py`, `tests/test_ui_app_guards.py`: queued/running/
  terminal cancel, retry, no status overwrite, hydration, and UI guard tests.

No data submodule, manifest, output, or public OSeCAN surface was modified.
`git status --porcelain` contains no `D` entries.

## Fleet-correction canonicalization pass (2026-11-20)

**Scope:** static bundle regenerated from the CER 2021 capacity-fleet rerun;
canonical scenario, pre-fleet historical flags, 48/16/1 boot guards, and visible
fleet/pre-fleet badges and banners. No backend was used.

**Environment:** Chromium via `/tmp/ui-qa-venv` (Playwright), static
`python3 -m http.server 8765 -d ui`, viewport 1440×1100. The only HTTP 404 is the
intentional `/api/health` probe in static mode; it is handled by the mode badge.

### Results

| Check | Result |
|---|---|
| Boot guard | **PASS** — 48 scenarios, 16 families, 1 trade-off eligible; fatal banner hidden. |
| Canonical default | **PASS** — `fleet_capacity__screening_119m_fleet` selected; overview shows objective **1,445,655.44 M$**, cumulative **424.710 Mt CO₂**, 2050 **0.000 Mt CO₂**. |
| Canonical banner | **PASS** — visible **FLEET-CORRECTED** callout and masthead badges; screening-only/not-policy copy present. |
| Pre-fleet banner | **PASS** — `emission_factor_fix__efix_baseline_119m` shows **pre-fleet model — not comparable** in the rail (47 rows) and overview banner/badge. |
| Trade-off | **PASS** — guard hidden; ledger count `1 canonical fleet-corrected eligible · 47 historical/excluded`. |
| Explore/Pareto | **PASS** — 1 static eligible, 1 on front, no missing metrics, guard hidden. |
| Dispatch | **PASS** — 4,215 supply-side rows from the fleet run; `category=other` excluded (12,977 rows). |
| Errors | **PASS** — 0 page errors and 0 non-static console errors. |

### Screenshots

| File | Content |
|---|---|
| `fleet-canonical.png` | Default fleet-corrected overview with metrics and canonical banner |
| `fleet-prefleet.png` | Historical scenario with pre-fleet banner, badge, and tab metadata |
| `fleet-tradeoff.png` | One-canonical-scenario trade-off view and historical exclusion count |
| `fleet-dispatch.png` | Fleet-run dispatch ledger sourced from `fleet_capacity_119m` |
| `fleet-explore.png` | One-eligible Explore/Pareto view with no missing-metric guard |

### Files changed

- `workflows/ui/export_iteration0.py`: flat-summary mapping, fleet family,
  supersession/canonical flags, `other`-row exclusion, dispatch-derived PWRBCW.
- `ui/public/data/**`: regenerated 48-scenario bundle.
- `ui/app.js`, `ui/index.html`, `ui/styles.css`: counts, fleet/pre-fleet
  badges and banners, canonical default and copy.
- `tests/test_ui_export_iteration0.py`, `tests/test_ui_app_guards.py`: contract,
  metric, flag, eligibility, and banner guards.
- `planning/fleet-correction-canonicalization.md`, `CHANGE_LOG.md`.

Data artifact commit `fb877d2` in the data submodule. `git status --porcelain`
contains no `D` entries. No public OSeCAN repository was changed.

## Harvest ladder export — 2026-11-20

**Scope:** static bundle regenerated with the fleet-corrected 100/150/200 Mm³
harvest sensitivity ladder plus the canonical 119 Mm³ base. Boot guards moved to
51 scenarios / 17 families / 4 trade-off eligible; the exogenous-harvest caveat
was added to Trade-off and Explore.

**Environment:** Chromium headless shell (`chrome-headless-shell`), static
`python3 -m http.server 8123 -d ui`, viewport 1600 px wide; screenshots captured
with virtual time. No backend was used. The only expected network miss is the
intentional static-mode `/api/health` probe.

### Results

| Check | Result |
|---|---|
| Boot guard | **PASS** — 51 scenarios, 17 families, 4 trade-off eligible; fatal banner absent in DOM dump. |
| Ladder family | **PASS** — `fleet_screening_ladder` has 3 scenarios at 100/150/200 Mm³; each is fleet-corrected, screening-only, not policy-grade, harvest-exogenous, and trade-off eligible. |
| Canonical role | **PASS** — 119 Mm³ remains the single canonical/base scenario and is not duplicated by the ladder export. |
| Trade-off | **PASS** — 4 eligible points visible, ledger count `4 eligible · 1 canonical/base + 3 harvest sensitivity`; guard hidden. |
| Caveat | **PASS** — exact caveat visible above both Trade-off and Explore. |
| Pareto | **PASS** — mechanically computed; 1 on front (200 Mm³) and 4 eligible evaluated. The canonical base is shown as dominated, not forced onto the front. |
| Compare | **PASS** — 119 Mm³ vs 200 Mm³ shows 2 of 11 differing dimensions (harvest and harvest volume) and the expected metric deltas. |
| Errors | **PASS** — no data-contract failure in the DOM dump; static-mode badge only. |

### Screenshots

| File | Content |
|---|---|
| `ladder-tradeoff.png` | Four eligible points, canonical/base + sensitivity badges, and the prominent exogenous-harvest caveat |
| `ladder-explore.png` | Explore workspace with the caveat, 4 eligible rows, and the mechanical Pareto front (200 Mm³ on front) |
| `ladder-compare.png` | 119 Mm³ canonical/base vs 200 Mm³ sensitivity comparison and differing dimensions |

`git status --porcelain` contains no `D` entries. No data submodule or public
OSeCAN repository changes were made by the UI export.

## Harvest feasibility gate + policy selector — 2026-11-20

**Scope:** the 150 Mm³ national harvest gate, severity labels, Pareto
eligibility, and the `policy_test_v1` run-form selector. The static bundle was
regenerated at 51 scenarios / 17 families / **1 Pareto-eligible scenario**;
the ladder remains visible as sensitivity/compare context only. Chromium QA used
the static bundle and a live FastAPI server with the existing fake runner.
Machine-readable evidence: `ui/qa/feasibility_qa_results.json`.

### Environment

| Item | Value |
|---|---|
| Chromium | Playwright 1.65.2 bundled, `--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --disable-dev-shm-usage` |
| Static server | `python3 -m http.server 8133 --bind 127.0.0.1 -d ui` |
| Live server | `.venv/bin/python -m workflows.ui.server --port 8134 --runtime-dir /tmp/ui-feas-qa/runtime --allow-runs --runner-command ".../.venv/bin/python /tmp/ui-run-qa/fake_runner.py"` |
| Viewport | 1440×1200 |

### Results

| Check | Result |
|---|---|
| Boot / counts | **PASS** — 51 scenarios, 17 families, `Pareto eligible = 1`; no fatal banner. |
| Rail feasibility badges | **PASS** — canonical 119 `AAC/NFD pass`; ladder 100 `AAC/NFD pass`, 150 `AAC conditional`, 200 `AAC/NFD fail` + `diagnostic`. |
| Trade-off default | **PASS** — only the canonical 119 Mm³ point is eligible; count reads `1 Pareto eligible · canonical 119 Mm³ only · 3 harvest sensitivity/compare excluded`. The gate caveat reads “200 Mm³ not admissible under documented AAC/NFD; 150 Mm³ conditional; harvest exogenous.” |
| Explore default | **PASS** — 4 rows visible (1 Pareto eligible + 3 ladder context); Pareto count `1 on front · 1 Pareto-eligible evaluated · 3 context excluded`; 150/200 cannot enter the front. |
| Static policy fallback | **PASS** — `policy_test_v1 — unavailable in static mode` option is disabled; the selector is disabled with the static-mode badge. |
| Live `/api/constraints` | **PASS** — returns both `screening_v0` (default) and `policy_test_v1` (`status=draft`, `policy_grade=false`, `available=true`). |
| Policy selector | **PASS** — option text `policy_test_v1 — draft · policy_grade=false`; badge shows draft/Stage-1 reserve/build caps; the run-form policy flag is visible. |
| Selector payload | **PASS** — Chromium created `policy-selector-qa-150-mwoluyqu`; stored JSON has `constraints.set_id=policy_test_v1` and `harvest.level_m3_per_year=150000000`. |
| Fake-runner path | **PASS** — queued run completed with fake objective 123.5 M$; the session path is not claimed as a policy-grade or Pareto result. |
| Console/page errors | **PASS** — live page 0 console/page errors; static page only the intentional `/api/health` 404 used to select static mode. |

### Screenshots

| File | Content |
|---|---|
| `feasibility-gate.png` | Trade-off gate caveat, 1 Pareto-eligible canonical, rail 100 pass / 150 conditional / 200 fail badges |
| `policy-selector.png` | Live Run form with `policy_test_v1` selected, draft badge, 150 conditional harvest option |

`git status --porcelain` contains no `D` entries. No public OSeCAN repository was
changed.
