# Forest Ledger v1.0.0 — static public runtime

A self-contained, static-only copy of the CLEWS-C2070 FRESH forestry/biomass
scenario explorer. **This is a demonstration publication, not a live service,
not a C2070 official product, and not energy-policy advice.**

## Release identity

| Field | Value |
|---|---|
| Release label | `clews-c2070-fresh-v1` |
| Tag | `v1.0.0` (annotated; peels to `b69e258`) |
| Release commit | `b69e2588a089b8532bbc74afe77baaf069e83401` |
| Publication date | 2026-11-20 |
| Data submodule commit | `e177f357a0a7678f1f53438bf92173196db0f4df` (`data` tag `v1.0.0`) |
| OSeCAN model source | public `deltae/C2070` commit `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` + committed UI patches |
| Data manifest | 63 files, 8,732,120 bytes, `sha256:2a924d6cc7dbbf9be36e31aae4d9924b397a93ec20e2d0f5127b54e27dc5e102` |
| Data bundle hash | `sha256:064c1e82af2f3e4eb02fa824a99f8002b80e7941223b325894c91238805b4738` |
| Bundle hash rule | SHA-256 of the sorted `sha256␠␠path` lines for the 63 data-manifest files |

The data bundle hash covers the sorted content of the 63 files listed in
`public/data/manifest.json`; the manifest itself is hashed separately above.
All 63 files were re-hashed byte-for-byte from this publication copy and match.

## What this is

- A static HTML/CSS/JavaScript copy of the v1.0.0 Forest Ledger UI.
- Precomputed views for **53 scenarios / 18 families / 1 Pareto-eligible
  scenario**, with **53 columnar dispatch outputs**.
- All assets are local: ECharts, MapLibre GL JS, and WOFF2 fonts are vendored.
  No CDN or external request is required.
- Canonical scenario: `fleet_capacity__screening_119m_fleet` (source run ID
  `screening_119m`), a fleet-corrected 119 Mm³ screening case.

## What this is not

- **Static demo only.** There is no live solver, no FastAPI server, no run
  queue, no SSE stream, and no live session storage in this publication.
  `publication-mode.js` blocks every same-origin `/api` request so the UI cannot
  attach to a live runner even if it is served behind one.
- **Not C2070 official.** This is a FRESH-side provisional publication
  (`screening_only=true`, `policy_grade=false`).
- **No energy-policy advice.** It is not an energy-policy recommendation, an
  investment case, a legal AAC/THLB determination, or a sustainable-supply
  forecast.
- **No legal or operational decision support.** Do not use it for harvest
  allocation, tenure, permitting, or investment decisions.

## Headline counts and canonical result

| Item | Value |
|---|---:|
| Scenarios | 53 |
| Families | 18 |
| Pareto eligible | 1 |
| Dispatch outputs | 53 |
| Data-bundle files | 63 |
| Horizon | 2021–2070 |

Canonical fleet-corrected 119 Mm³ screening result:

| Metric | Value |
|---|---:|
| Objective | **1,445,655.44 M$** |
| Cumulative CO₂, 2021–2070 | **424.710127 Mt CO₂** |
| CO₂ in 2050 | **0.0 Mt CO₂** |
| National BCW in 2050 | **152.922679 PJ** |
| PWRBCW in 2050 | **41.108217 PJ** |
| Biomass share in 2050 | **0.049992** (4.9992%) |
| Harvest level | **118,977,339 m³/yr** |
| Flags | `screening_only=true`, `policy_grade=false`, `canonical=true`, `fleet_corrected=true` |

`policy_test_v1` is a **draft interface test**, not a release result and not a
policy scenario:

| Metric | Value |
|---|---:|
| Objective | 1,458,305.16 M$ |
| Cumulative CO₂, 2021–2070 | 1,024.824 Mt CO₂ |
| CO₂ in 2050 | 0.490203 Mt CO₂ |
| Status | `draft` |
| `policy_grade` | `false` |
| Availability here | Disabled in static mode (`policy_test_v1 — unavailable in static mode`) |

The canonical and `policy_test_v1` rows use different constraint families and
configurations. Their difference is **not a policy effect** and must not be
reported as one. The draft does not replace the canonical row.

## Static/live-mode behaviour

The publication starts in static mode and displays the badge
**“Static mode · no backend”**. The role badge reads
**“Role · static · no role”** and `document.body[data-role="static"]`.

The guard in `publication-mode.js` rejects same-origin `/api` requests. As a
result:

- the UI never sends a health probe or any other API request;
- create, queue, cancel, and retry are disabled;
- the `policy_test_v1` constraint option is present but disabled;
- no live runs enter the ledger;
- all committed scenarios, dispatch charts, trade-off/Pareto views, Explore,
  Map, Zones, Inputs, and OSeCAN exports remain available;
- hash deep links and the shared 2021–2070 year control continue to work.

## Validation and QA

**Static server (Python `http.server`, 2026-11-20):**

- 86/86 payload URLs returned HTTP **200** (index, JS, CSS, fonts, vendor,
  all data files, `public/data/manifest.json`, `manifest.json`, and docs).
- All **63/63** data-manifest entries returned HTTP 200 and matched their
  recorded SHA-256 and byte count.
- **0** HTTP 404s and **0** failed requests.
- 11,371,472 bytes served across the validation set.

**Chromium (Playwright 1.65.2, headless; 1600×1200):**

- Title, header count **53**, badge **“Static mode · no backend”**, role
  **“Role · static · no role”**, `data-role="static"`.
- All nine tabs rendered: Overview, Dispatch, Trade-off, Explore, Map, Zones,
  OSeCAN Links, Run, Inputs.
- Dispatch and trade-off ECharts canvases rendered; Zones table rendered
  **14 rows** (13 zones + separate `CAN`) and the heatmap rendered **14 rows**.
- OSeCAN activity-limits CSV export worked: 201 lines, 5,529 bytes.
- Run controls disabled; `policy_test_v1` option disabled.
- **0** page errors, **0** console errors, **0** HTTP responses ≥ 400, and
  **0** `/api` network requests.
- Explicit probe check: `fetch('/api/health')` rejected locally with
  `static publication: live API disabled`.

Evidence: `ui/qa/publication-v1.png` and
`ui/qa/publication_v1_qa_results.json`.

## Licences and provenance

### Vendored browser assets

| Asset | Version | Licence |
|---|---|---|
| Apache ECharts | 5.5.1 | Apache-2.0 |
| MapLibre GL JS | 4.7.1 | BSD-3-Clause |
| Fraunces | variable WOFF2 | SIL Open Font License 1.1 |
| IBM Plex Sans / Mono | Latin WOFF2 subsets | SIL Open Font License 1.1 |

Upstream licence texts are included under `vendor/*.LICENSE.txt`; source URLs
are recorded in `vendor/README.md`. No runtime CDN request is made.

### Data and model provenance

The UI bundle is generated from the committed v1.0.0 data submodule and the
canonical/historical OSeCAN-derived outputs described in
`docs/release-v1.md` and `docs/model-card.md`. Principal upstream sources
include NRCan/CFS RSYC yield curves and ecological strata, CER EF2026 demand
and capacity, BC BCTS public bid results, Québec MERIS, and national/provincial
forest inventory products. Those sources retain their own licences and
attribution requirements (for example Open Government Licence – Canada,
Open Government Licence – British Columbia, CC BY 4.0, and GPL-3.0-or-later
for RSYC code/catalogue material).

This publication redistributes aggregated/derived UI data, not raw upstream
records. Do not strip source attribution when reusing the bundle. The parent
repository checkout has no top-level `LICENSE` file; this publication does not
grant a new licence for the project code.

## Caveats

- The data bundle is a snapshot of the v1.0.0 UI data contract, not a remote
  DataLad/`git-annex` retrieval. The clean-checkout restore drill used a local
  data materialization proxy.
- The canonical 119 Mm³ run is a screening reference. Harvest is exogenous and
  free in the energy objective; higher harvest lowers modelled cost/emissions
  because no harvest cost, THLB, or AAC constraint is in the objective.
- The operable merchantable-forest proxy (144.69 Mha) is a screening landbase,
  not a legal THLB.
- The 100/150/200 Mm³ ladder is sensitivity context, not a policy frontier.
  200 Mm³ is not admissible under the documented AAC/NFD screen; 150 Mm³ is
  conditional and fails the 1.0 m³/ha/yr provincial operable-proxy screen by
  16.296 Mm³ (and the 1.2 screen by 4.492 Mm³).
- The 13-zone view is a reporting fallback. Only B1/B2 have authoritative
  point anchors; the other 11 zones are explicit gaps. No C2070 polygon is
  invented, and `CAN` is never summed with zones.
- Pre-fleet/superseded/diagnostic rows remain visible for audit but are not
  Pareto-eligible and are not comparable to the canonical fleet-corrected row.
- `policy_test_v1` is a draft interface test. Its result is not policy-grade
  and its difference from the canonical row is not a policy effect.
- This publication has no host deployment, Cloudflare Tunnel, Access policy,
  authentication, or public URL. Hosting and edge access are separate
  operations.

## Module boundary

The FRESH forestry/biomass module owns the RSYC-based supply evidence, harvest
and allocation logic, bucking/residue chain, cost/supply curve, BC/QC
cost-revenue ledger, and this forestry UI. Taco Niet and the energy/OSeCAN
policy team own energy-policy design, OSeCAN structure, and OSeCAN constraints.
OSeCAN is used here as a black-box interface test. Nothing in this publication
is an energy-policy recommendation or a C2070 official result.

## Reproduction

```bash
cd ui_publications/v1.0.0
python3 -m http.server 8000
# open http://127.0.0.1:8000/
```

The page must show **“Static mode · no backend”** and must not issue any
`/api` request. Verify the bundle with `manifest.json` and the outer
`PUBLICATION.json` record; see `README.md` for the short form.
