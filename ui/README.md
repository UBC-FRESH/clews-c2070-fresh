# Forest Ledger — CLEWS/C2070 iteration-0 explorer

Static, FRESH-side scenario explorer for the forest-biomass component of
CLEWS-C2070. **Provisional: not C2070 official.** No build step. The committed
views work without a backend; an optional FastAPI runner adds live
screening-only scenarios.

## Run

Static fallback only (existing precomputed views):

```bash
python3 -m http.server 8000 -d ui
# open http://localhost:8000/
```

Live mode (same static files plus the API, SSE, and runner):

```bash
.venv/bin/python -m workflows.ui.server --port 8013 --allow-runs
# open http://127.0.0.1:8013/
```

If `/api/health` is unreachable the UI shows a **Static mode · no backend** badge,
disables create/queue, and keeps all precomputed views and OSeCAN exports.
If the API is up but `--allow-runs` is absent, the badge reads
**Live API · runs disabled**; read-only mode disables mutations.

The UI is vanilla HTML/CSS/JS. ECharts and MapLibre GL JS are vendored under
`ui/vendor/`, with local WOFF2 Latin subsets of Fraunces, IBM Plex Sans, and IBM
Plex Mono. No runtime CDN request is required.

## Views

- **Overview** — scenario masthead, five metric cards (objective M$, cumulative
  emissions Mt CO₂, national BCW 2050 PJ, PWRBCW 2050 PJ, bio share), dimensions,
  flags, and source paths.
- **Dispatch** — ECharts stacked annual energy by technology family
  (HYD/WND/SOL/BIO/BCW/NGS/URN/COA/other), 2021–2070. Electricity and fuel are
  separate ledgers. Includes zone filter, year-window sliders, tooltips, legend,
  and a dashed total line. A shared 2021–2070 year control (slider, play/pause,
  reset) draws a vertical guide on the chart and drives a dispatch snapshot
  table by technology family for the selected year.
- **Trade-off** — objective (M$) vs cumulative emissions (Mt CO₂) for the four
  eligible fleet-corrected scenarios: the 119 Mm³ canonical/base run plus the
  100/150/200 Mm³ harvest sensitivity ladder; colour by family. Pre-fleet,
  superseded, and diagnostic rows are explicitly excluded but remain visible in
  the rail for audit as **pre-fleet model — not comparable**. A prominent caveat
  states that harvest is exogenous and free, so the ladder is a sensitivity
  exercise, not a policy frontier.
- **Explore** — multi-criteria workspace over the four eligible fleet-corrected
  scenarios (119 Mm³ canonical/base + 100/150/200 Mm³ sensitivity) plus any live
  session runs. The same exogenous-harvest caveat is pinned above the workspace.
  Parallel coordinates cover 11 axes (objective, emissions, national BCW, PWRBCW,
  bio share, harvest, carbon price, recovery, PWRBIO cap, nuclear cap, wind
  cap/integration). Vertically brush an axis or use the range form; every filter
  appears as a removable chip, and active filters persist in the URL hash. A
  three-objective Pareto view defaults to minimize objective, minimize cumulative
  emissions, and maximize national BCW 2050; dominance is computed mechanically
  on min–max normalized metrics, the front can be exported as CSV, and missing
  metrics are excluded with a visible label. Because harvest is free, the 200 Mm³
  sensitivity case can dominate the canonical base; the UI says so rather than
  forcing the base onto the front. Scenario A/B comparison shows absolute and
  relative deltas with units, lists differing dimensions, and offers an optional
  annual dispatch overlay for one energy ledger at a time. Live session runs are
  dashed/marked `screening_only` and are never folded into the canonical ledger.
- **Map** — MapLibre with 13 provinces, 36 ecoprovinces, 385 RSYC tiles, and a
  `recoverable_pj_base` choropleth. Linked Canada → province → ecoprovince →
  RSYC tile drill-down with breadcrumbs, a selection panel (area, recoverable
  PJ, tile cells, species caveat, evidence), layer toggles, zoom, fit/reset, and
  B1/B2 authoritative anchors. Selections without a documented C2070 zone
  mapping say so explicitly; dispatch stays national rather than inventing a
  crosswalk. The 11 C2070 zones without authoritative polygons are shown as
  gaps, never as invented geometry.
- **OSeCAN Links** — source-file/check ledger plus a screening lever calculator
  (harvest 100/119/150/200 Mm³; recovery 0.35/0.60/0.75; cost basis), national
  PJ and BC/rest split, tier-sum validation, and client-side CSV exports. When
  the live API is available, **Queue this scenario** creates a live scenario from
  the same levers.
- **Run** — create a `clews.scenario.v1` scenario from `screening_v0` defaults,
  queue it on the FastAPI runner, follow SSE logs/status, and fetch normalized
  output on completion. Completed runs enter a **Session runs** rail group, the
  overview, trade-off, and dispatch; they are marked **screening_only** and
  **policy_grade=false**. Failed runs add no result.
- **Inputs** — harvest reference paths, supply-curve tiers, and the
  managed-landbase proxy with caveats.

Scenario selection in the left rail updates the overview, dispatch, trade-off,
and Explore views. Filters include family chips and free-text search. Deep links
use hashes such as `#dispatch/emission_factor_fix__efix_stress_119m`. Explore
adds query parameters to the hash for brush ranges, Pareto projections, and the
A/B pair, for example
`#explore/emission_factor_fix__efix_stress_119m?brush=emissions_total%3A0.45%3A0.49&px=objective&py=emissions_total&pz=national_bcw_2050`.
Map deep links carry the spatial selection and shared year, for example
`#map/emission_factor_fix__efix_stress_119m?sel=ecoprovince%3A14.2&y=2050` or
`?sel=zone%3AB1&y=2060`; dispatch links carry `?y=`.

## Data contract

Read-only bundle under `ui/public/data/` (regenerated 2026-11-20 from the
fleet-corrected 119 Mm³ run and the historical pre-fleet scenarios):

| Path | Content |
|---|---|
| `scenarios.json` | 51 scenarios, 17 families, 4 trade-off IDs (1 canonical/base + 3 harvest sensitivity), units, flags, dimensions, metrics |
| `outputs/{scenario_id}.json` | Columnar `technology`, `category`, `zone`, `year`, `value_pj`; units per category |
| `inputs/harvest_basis.json` | Harvest template summary and 2021–2070 reference paths |
| `inputs/supply_curve.json` | Four-tier national supply curve, reconciliation, physical basis |
| `inputs/managed_landbase.json` | Screening proxy by province and ecoprovince; caveats |
| `inputs/osecan_links.json` | Source-file ledger, hashes, national cap checks |
| `spatial/provinces.geojson` | 13 province polygons |
| `spatial/ecoprovinces.geojson` | 36 ecoprovince polygons |
| `spatial/rsyc_tiles.geojson` | 385 RSYC tiles with `recoverable_pj_base` |
| `spatial/zone_nodes.json` | 13 C2070 zones; B1/B2 anchors; 11 explicit gaps |

The canonical fleet-corrected base artifact is
`data/derived/national/clews/osecan/fleet_capacity_119m/` (summary, dispatch,
manifest only; raw model/results are not committed). Its headline metrics are
objective **1,445,655.44 M$**, cumulative emissions **424.710127 Mt CO₂**, and
2050 emissions **0.0 Mt CO₂**. The fleet run records demand/audit rows under
`category=other`; the exporter omits them from the supply-side columnar output
and reports the count in `excluded_categories`.

The sensitivity ladder lives under
`data/derived/national/clews/osecan/fleet_screening_ladder/{100m,150m,200m}/`
(the 119m directory duplicates the canonical case and is deliberately not
exported twice). It is exported as family `fleet_screening_ladder` with
`harvest_m3_per_year` dimensions and metrics read from each run summary. All
three cases are `fleet_corrected=true`, `screening_only=true`,
`policy_grade=false`, `harvest_exogenous=true`, and `tradeoff_eligible=true`.

> **Harvest is exogenous and free in this model; higher harvest lowers cost and
> emissions because no harvest cost/THLB/AAC constraint is modeled. This is a
> sensitivity ladder, not a policy frontier.**

### Units and guards

- Electricity categories (`dispatch`, `power_*`) are reported in **PJ
  electricity**. Fuel categories (`supply`, `supply_*`, `bio_supply`) are reported
  in **PJ fuel**. The dispatch view selects one ledger at a time and never sums
  the two.
- If a scenario has both `dispatch` and `power_*` (or both `supply` and
  `supply_*`), the aggregate is used and the detail is deliberately not added, to
  avoid double counting. The UI states which category set was used.
- `CAN` is a national aggregate. When present, the "National" zone option uses
  `CAN` only; zone rows are not added to it.
- The app fails closed at boot on schema/count/unit/tier-sum mismatches. Each
  scenario output is validated on load (scenario ID, column lengths, finite
  values, 50-year coverage, category units).
- Trade-off eligibility is re-derived from flags (`publishable`,
  `post_fix_emissions`, `fleet_corrected`, `tradeoff_eligible`, and neither
  `superseded_for_emissions` nor `superseded_for_fleet`) and checked against the
  4-ID ledger. A visible guard appears if they disagree.
- `fleet_capacity__screening_119m_fleet` is the single canonical/base scenario
  and is `screening_only=true`, `policy_grade=false`. The three
  `fleet_screening_ladder__*` runs are labelled **sensitivity** with
  **exogenous harvest**; the canonical run is labelled **canonical/base**. Every
  earlier scenario carries `superseded_for_fleet=true` and the visible
  **pre-fleet model — not comparable** badge/banner.
- The trade-off/Pareto front is computed mechanically. It is legitimate for the
  200 Mm³ sensitivity to dominate the 119 Mm³ base because harvest has no cost,
  THLB, or AAC constraint in this model; the UI shows the caveat rather than
  hiding the dominated base.
- Superseded and diagnostic flags are visible in the rail, masthead, and file
  ledger.
- Spatial counts and the 11-zone gap count are guarded. No C2070 zone polygon is
  drawn; only B1/B2 anchors are plotted.

## OSeCAN screening calculator

This is a transparent, proportional screening model — **not a solver run**:

```text
factor      = (harvest Mm³ ÷ 118,977,339 m³) × (recovery ÷ 0.60)
national PJ = committed 2050 base national cap (109.398863 PJ) × factor
BC PJ       = committed BC zonal total (43.523816 PJ) × factor, capped by national PJ
rest PJ     = national PJ − BC PJ
```

The requested national PJ is allocated through T1–T4 incremental caps. If it
exceeds the reconciled tier-sum ceiling (213.538041 PJ), the result is clamped
and flagged. Tier sum, terminal upper bound, and source reconciliation residual
are checked before display.

CSV exports use the exact header `REGION,TECHNOLOGY,YEAR,VALUE`:

- `SUPBCW_activity_limits_h{harvest}m_r{recovery}.csv` — national `CAN` rows for
  `SUPBCWT1`–`SUPBCWT4`, 2021–2070, **PJ fuel/year**.
- `SUPBCW_variable_cost_h{harvest}m_r{recovery}.csv` — national `CAN` rows for
  the same tiers, 2021–2070, **USD/GJ**.

The exports are illustrative inputs for a future CLEWS run. They do not contain
BC/rest aggregate rows, to avoid mixing national and zonal regions in one sum.

## Spatial notes

`recoverable_pj_base` is displayed as quantile classes over the 385 RSYC tiles.
Tile popups expose area, volume, recoverable ODT/base energy, and
`over_capacity`. The map uses a local blank MapLibre style (no external basemap),
so it does not leak network requests. WebGL is required by MapLibre; the UI
shows a clear error if it is unavailable.

## Vendor / licences

| Asset | Version | Licence |
|---|---|---|
| ECharts | 5.5.1 | Apache-2.0 (`vendor/echarts.LICENSE.txt`) |
| MapLibre GL JS + CSS | 4.7.1 | BSD-3-Clause (`vendor/maplibre-gl.LICENSE.txt`) |
| Fraunces | variable Latin subset | SIL OFL 1.1 (`vendor/fraunces.LICENSE.txt`) |
| IBM Plex Sans / Mono | Latin subsets | SIL OFL 1.1 (`vendor/fonts.LICENSE.txt`) |

## Live API client

`ui/api.js` is a small no-build client for `/api/health`, `/api/constraints`,
`/api/scenarios`, `/api/runs`, `/api/runs/{id}/events` (SSE), and
`/api/runs/{id}/output`. It never writes to `public/data/`; session runs live in
browser memory and disappear on reload. The static UI is the fallback, not a
replaced surface.

## Verify

```bash
node --check ui/app.js
node --check ui/api.js
python3 -m http.server 8010 -d ui
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/public/data/scenarios.json
.venv/bin/python -m pytest -q tests/test_ui_scenario_api.py
```

No generated data or solver artifacts are written by the static UI.
