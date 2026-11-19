# UI QA report — iteration 0 (headless browser pass)

**Date:** 2026-11-19 19:14 UTC  
**Scope:** real Chromium rendering and interaction QA of `ui/` at HEAD
(`feature/osecan-zonal-bc-supply`), served over HTTP. No UI code or data was
modified.  
**Result:** **PASS with defects.** All six views render; scenario selection and
tab navigation work; 0 console errors, 0 page errors, 0 failed requests. Six
visual/UX defects were found, including one missing-content bug and one
unreadable-status-label bug.

## Install (versions/commands)

| Item | Version / path |
|---|---|
| Python | 3.12.3 (`/usr/bin/python3`) |
| pip | 26.3 |
| Playwright (Python package) | 1.65.2 |
| Playwright bundled driver | 1.65.1 |
| Chromium | 159.0.8154.6 (`chromium-1255`, `chromium_headless_shell-1255`) |
| Pillow (pixel checks) | 13.0.0 |
| Isolated environment | `/tmp/ui-qa-venv` |
| HTTP server | `python3 -m http.server 8012 --bind 127.0.0.1 -d ui` |

```bash
python3 -m venv /tmp/ui-qa-venv
/tmp/ui-qa-venv/bin/pip install --upgrade pip
/tmp/ui-qa-venv/bin/pip install playwright pillow
/tmp/ui-qa-venv/bin/playwright install chromium
```

`playwright install-deps chromium` was **not needed**; the headless shell
launched successfully with the base image. `sudo` was available but unused.
Chromium was launched headless with:

```text
--use-gl=swiftshader --enable-unsafe-swiftshader --ignore-gpu-blocklist --disable-dev-shm-usage
```

Server checks:

```text
GET /                                 200
GET /public/data/scenarios.json        200
```

## Visual QA (screenshots, console errors, 404s)

Full-page screenshots (all under `ui/qa/`, total 4.0 MB):

| Screenshot | Viewport | Actual image |
|---|---|---|
| `desktop.png` | 1440×1200 | 1440×1610 |
| `mobile.png` | 390×844 | 390×3631 |
| `dispatch.png` | 1440×1200 | 1440×1610 |
| `map.png` | 1440×1200 | 1440×1610 |
| `tradeoff.png` (extra evidence) | 1440×1200 | 1440×1610 |
| `osecan.png` (extra evidence) | 1440×1200 | 1440×2047 |
| `map-fit.png` (extra evidence, canvas only) | — | 723×726 |

Checks:

- Header contract: **47 scenarios**, **26 trade-off eligible**, rail **47 of 47
  shown**, 16 family chips (All + 15).
- Local fonts: `document.fonts.status === "loaded"`; all 6 faces loaded.
  `document.fonts.check()` returned true for Fraunces, IBM Plex Sans, and IBM
  Plex Mono. Computed `h1` font family is Fraunces; body is IBM Plex Sans.
- Horizontal overflow: **none** at either viewport
  (`scrollWidth === innerWidth`: 1440/1440 and 390/390). The mobile tab bar is an
  intentional horizontal scroller (`scrollWidth 440` vs `clientWidth 354`).
- Dispatch: ECharts canvas **1030×560**, non-blank
  (stddev 79.1/50.3/39.8 RGB; 242 sampled colours). Legend has 6 entries.
  Source note says `dispatch`, `PJ electricity`, 13 zones, no `CAN` aggregate.
- Trade-off: ECharts canvas **1030×560**, non-blank
  (stddev 11.6/11.3/12.9; 262 sampled colours). **26 points** across 7 family
  series; guard hidden; count `26 eligible · 21 excluded`.
- Map: MapLibre canvas **722×725**, non-blank (stddev 80.3/58.9/44.7; 4099
  sampled colours). Stats show **13 provinces / 36 ecoprovinces / 385 RSYC
  tiles**, 2/13 anchors, 11 zone gaps. `#gap-zones` contains exactly **11 chips**
  (A1, A2, L1, M1, N1, O1, O2, Q1, Q2, S1, T1) and the visible note
  **“11 C2070 zones lack polygons”**.
- OSeCAN: national 109.42 PJ, BC 43.53 PJ, rest 65.89 PJ, supply cost $2.457/GJ;
  tier sum validated at 213.538 PJ with residual 0.000000 PJ.
- Inputs: harvest ECharts canvas 1030×360; 5 tier rows; 14 managed-landbase
  rows; summary cards populated.
- Tabs: all six (Overview, Dispatch, Trade-off, Map, OSeCAN Links, Inputs)
  activate, set `aria-selected`, and expose exactly one visible panel.

Runtime console/network:

- **0** page errors.
- **0** failed requests.
- **0** HTTP responses ≥ 400 (no 404s).
- **4** console warnings, all the same SwiftShader performance message:
  `GPU stall due to ReadPixels`. This is a headless-software-GL artefact, not an
  application warning. No ECharts or MapLibre application warnings.

## Interaction checks (tabs/scenario/tradeoff)

| Check | Result |
|---|---|
| Click all six tabs | PASS — one panel visible each time |
| Select scenario in rail | PASS — `emission_factor_fix__efix_baseline_119m` selected; overview masthead, metrics, status, rail selection, and URL hash all updated |
| Overview after rail selection | PASS — label and 5 metric cards changed |
| Dispatch after rail selection | PASS — re-rendered with the selected scenario; source note and 13-zone selector present |
| Hover a trade-off point | PASS — ECharts tooltip shows ID, label, family, objective, emissions, national BCW, bio share |
| Click a trade-off point | **FAIL** — ECharts `click` event fired for the point, but no scenario selection, rail ring, hash, or overview update occurred. Panel copy says “select in the rail to ring a point”, so this may be deliberate, but it does not satisfy the requested point-to-overview interaction. |
| Map tile popups | Not exhausted; hover cursor and visible layer legends checked. |
| OSeCAN calculator controls | PASS — default values and validation strip populate. |

Detailed selection evidence:

- Rail target: `emission_factor_fix__efix_baseline_119m`
  (“119M base baseline: $95->115/t, normal hydro”).
- After click: masthead label matched exactly, `selectedRowCount = 1`, hash
  `#overview/emission_factor_fix__efix_baseline_119m`.
- Trade-off click target: `emission_factor_fix__efix_baseline_119m`, a point
  different from the currently selected `emission_factor_fix__efix_stress_119m`.
  ECharts emitted the click, but selected ID remained the stress scenario and the
  hash stayed on the trade-off view.

## Visual/layout issues found (with severity)

### 1. HIGH — OSeCAN “File & check ledger” is missing all 15 family rows

`osecan-family-table` contains only the 148-byte validation strip; it has
**0 `<details>` rows**, so the promised source-file/check ledger is visually
empty. Evidence: `osecan.png`.

Root cause is a compound-assignment side-effect bug at `ui/app.js:1563`:

```js
familyHtml += links.families.map(function (family) {
  ...
  familyHtml += '<details ...';   // mutates familyHtml inside the RHS
  ...
}).join('');
```

`+=` reads the left-hand value before evaluating the right-hand side. The
callback appends 15 `<details>` strings, then the outer assignment overwrites
`familyHtml` with the pre-map value plus `''`. Fix by building a separate
`familyRows` string (or returning markup from the callback) before appending:

```js
var familyRows = links.families.map(function (family) {
  return '<details ...>...</details>';
}).join('');
$('osecan-family-table').innerHTML = familyHtml + familyRows;
```

### 2. HIGH — Overview scenario-status badges are effectively invisible

The badges `post-fix emissions`, `publishable`, and `trade-off eligible` under
the scenario ID render at **8.48 px** with light text on a light card. Computed
styles:

- text `rgb(220, 233, 213)` on a translucent green badge over `#fbf6e9`;
  approximate contrast **≈1.1:1** (WCAG AA needs 4.5:1).
- third badge text `rgba(243, 234, 215, 0.72)` on cream, also **≈1.1:1**.

Visible as barely-there grey letters in `desktop.png` and `mobile.png`. This
looks like dark-rail badge styling reused inside the cream masthead. The flags
are duplicated in the rail and status card, but the primary overview status is
unreadable.

### 3. MEDIUM — Chart axis tick labels lose all useful resolution

- **Trade-off x-axis:** every tick label reads **“1M”** (objective spans roughly
  1.0M–1.7M M$), so the axis has no readable scale. See `tradeoff.png`.
- **Dispatch y-axis:** labels repeat **“3k, 3k … 2k, 2k”** because intermediate
  ticks (e.g. 2.5k) are rounded at zero decimals. See `dispatch.png`.

Both use the same zero-decimal compact formatter. Use adaptive precision
(e.g. `fmtCompact(v, v >= 1e6 ? 1 : 0)` or explicit `1.0M`, `1.2M`, `1.4M`) so
adjacent ticks remain distinct.

### 4. MEDIUM — Map default framing clips eastern Canada and most tiles

Default MapLibre view is `center [-101, 58]`, `zoom 2.6` on a 722×725 canvas,
which spans approximately **-142.9° to -59.1° longitude**, cutting eastern
Quebec/Labrador/Newfoundland and the eastern RSYC tiles. `map.png` shows only
western/central Canada. Clicking **Fit to Canada** reveals the full country and
all tile coverage (`map-fit.png`); the difference is large (canvas mean
difference 72/52/34 RGB). Default should fit the national extent, since the view
claims 13 provinces and 385 tiles.

### 5. LOW — Trade-off point click is not wired to scenario selection

The ECharts point click fires, but there is no handler calling
`selectScenario()`; only the rail selection rings a point. The chart copy
explicitly instructs users to select in the rail, so this is a UX limitation
rather than a rendering failure. If point selection is expected, add
`tradeoffChart.on('click', params => selectScenario(params.data.meta.id))`.

### 6. LOW — Mobile hierarchy puts the 635 px rail ahead of all content

At 390×844 the header is 494 px tall and the scenario rail 635 px, so the
overview starts roughly 1,128 px down the page; only 5 of 47 rail rows are
visible at once (the rail scrolls internally). The tab bar also scrolls
horizontally (440 px content / 354 px viewport), leaving **Inputs** off-screen.
Both are usable, but a collapsible rail or explicit scroll affordance would
reduce first-screen friction.

## Commits/status (no D)

- Committed: `ui/qa/QA_REPORT.md`, `ui/qa/desktop.png`, `ui/qa/mobile.png`,
  `ui/qa/dispatch.png`, `ui/qa/map.png`, plus evidence captures
  `ui/qa/tradeoff.png`, `ui/qa/osecan.png`, `ui/qa/map-fit.png`.
- Screenshot total: **4.0 MB** (< 10 MB), so screenshots are committed.
- No UI code or data files were modified.
- Pre-existing untracked files (`docs/UngGuoFortin2013.pdf`,
  `docs/agent-integrity-protocol.md`, `extract_provenance.py`,
  `planning/national-bucking-supply-architecture.md`) were left untouched.
- `git status --porcelain` contains no `D` entries.
