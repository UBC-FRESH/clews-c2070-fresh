# National Growth-and-Yield Data Landscape and the Path to an All-Canada Model

| Field | Value |
|-------|-------|
| Document | `planning/national-gy-data-landscape.md` |
| Date | 2026-11-14 |
| Status | Data-landscape record and acquisition plan; no calibration result |
| Audience | PI and collaborating modellers |
| Gate context | G7 one-TSA pilot before G8 BC scale-up; no national gate (`planning/scientific-model-recovery-plan.md` §4) |

## Executive summary

- CLEWS-c2070 has a BC forestry component built on BC VRI vector inventory plus VDYP/TIPSY/FVS-BC through FEMIC (`planning/qc_bc_yield_curves_extraction.md`; `fresh-fvsbc` README, https://github.com/UBC-FRESH/fresh-fvsbc). The next step is an all-BC mini-model, one WS4 instance per TSA.
- The national step is not an extension of that path. It requires replacing BC VRI with SCANFI raster inventory and replacing BC-specific growth-and-yield with a national system. That replacement is the unsolved part (`metadata/readiness-gates.yaml`, gate `national_scaling`).
- `fresh-fvsca` cannot be one new FVS variant. FVS ships regional variants, not a Canada-wide variant; CFS's response is a multi-model API and meta-model framework, not a single national FVS (`https://github.com/USDAForestService/ForestVegetationSimulator`; `https://github.com/CWFC-CCFB/CFSStandGrowthAPI`).
- MAGPlot v1.2, archived on 2026-11-14, is the only new open plot archive. It provides 1.57 million remeasured trees but no Alberta, Ontario, or Nova Scotia records and no age or site-index tables (`data/raw/magplot/MAGPlot_CONTENTS.md`).
- NFI ground plots remain essential and request-only. The DUA package is pre-submission ready but unsubmitted (`metadata/license-determinations/nfi-targeted-request.md`).
- Recommended architecture: a multi-variant orchestrator with a plot adapter and calibration bundles, using CFS-style meta-models as the fallback for regions no FVS variant represents.
- This document records data holdings and contracts. It does not claim a validated national model.

## 1. Purpose and framing

The BC component is built on BC VRI vector inventory plus VDYP/TIPSY/FVS-BC through FEMIC (`planning/qc_bc_yield_curves_extraction.md` §B; https://github.com/UBC-FRESH/fresh-fvsbc). The all-BC mini-model is a stepping stone: one WS4 instance per TSA, exercising per-TSA orchestration and input contracts at provincial scale.

The national model changes both data and model. NFI raster inventory (SCANFI) replaces VRI polygons, and a national G&Y system replaces BC-specific G&Y. Neither replacement is solved (`metadata/readiness-gates.yaml`, gates `national_scaling` and `species_composition`).

FVS is regional. The source tree has `canada/bc`, `canada/on`, and `acd`; variants are selected at compile time, and no `canada` variant covers all provinces (https://github.com/USDAForestService/ForestVegetationSimulator/tree/main/canada; https://github.com/USDAForestService/ForestVegetationSimulator/tree/main/acd).

CFS's route, described by Fortin and Lavoie, is a meta-model API, not a single variant: `CFSStandGrowthAPI`, `CFSStandGrowth4R`, `CFSMetaModel4R`, `QcPSP`, and `CFT` (https://github.com/CWFC-CCFB; Fortin and Lavoie 2022, https://doi.org/10.1139/cjfr-2022-0002). The public meta-model repository currently covers Québec only, with maritime planned (`CFSStandGrowth4R` wiki). The 2024 CFS review documents a provincial model mosaic and proposes national collaboration through common interfaces (Metsaranta, Fortin and White 2024, https://doi.org/10.5558/tfc2024-005). A monolithic national FVS is not a realistic deliverable.

`fresh-fvsca` (working name) is therefore an integration layer, not a new Fortran variant: (1) a variant registry and router; (2) a plot adapter normalizing NFI, MAGPlot, and provincial PSP records into tree lists; (3) SCANFI tree-list imputation, flagged as predicted; (4) a harmonized volume/biomass post-processor; and (5) versioned calibration bundles carrying domain limits, parameter bounds, and provenance (`https://github.com/UBC-FRESH/fresh-fvsbc/blob/main/docs/reference/calibration-bundle.rst`). Non-FVS backends (GYPSY, MGM, VDYP, TIPSY, ARTEMIS, OSM) cover regions no FVS variant represents.

## 2. What CLEWS held before 2026-11-14

| Source | What was held | Size / coverage | Local path |
|---|---|---|---|
| SCANFI v2 2025 | 5 attribute rasters + 10 species crown-closure rasters | 21.28 GB + 14.58 GB | `data/raw/scanfi/v2/` |
| Beaudoin et al. kNN 2001 | 8 national attribute archives | 15.74 GB | `data/raw/beaudoin_knn/` |
| CASFRI v5 | Translation tables and docs; no payload | 129 CSVs | `data/raw/casfri_v5/` |
| NFI standard reports | Area, volume, biomass, carbon | 7 of 270 CSVs | `data/raw/nfi/standard_reports/t1/` |
| Québec IEQM 2023 | One sample feuillet | 1 of 60 | `data/raw/qc_inventory/2023-ieqm-eighth/` |
| BC VRI | 3-polygon test stub; no production inventory | 3 polygons | `tests/fixtures/bc_vri_sample/` |

The inventory held no NFI ground or photo plots, no MAGPlot, no provincial PSP data, no FVS code or parameters, no TSA boundaries, no BEC polygons, no site-index raster, and no THLB (`metadata/source-inventory.csv`; `metadata/national-plot-data-inventory.csv`).

The NFI DUA package is pre-submission ready but unsubmitted. Three placeholder contact lines remain at lines 9, 182, and 183 of `metadata/license-determinations/nfi-targeted-request.md` (`planning/nfi_status_tracker.md` §1).

## 3. What was newly acquired on 2026-11-14

MAGPlot v1.2 is archived at `data/raw/magplot/`. The ZIP is 127,123,248 bytes, SHA-256 `270055bb9eeed6ccd6cbd75f6a2026b54e340c5a4938af18ad06e479792888db`; it expands to 1,720,730,897 bytes and contains eight relational CSVs plus a coordinate-type PDF. The data dictionary is archived alongside it (`data/raw/magplot/ACQUISITION.md`).

| Measure | Value |
|---|---:|
| Sites | 39,130 |
| Remeasured sites | 20,890 (53.4%) |
| Tree measurement rows | 7,771,041 |
| Tracked trees | 2,866,168 |
| Tracked trees with repeat measurements | 1,573,761 |
| Measurement years | 1921–2024 |
| Median remeasurement interval | 10 years |

Province coverage in the ZIP is BC 16,517; QC 12,892; NB 7,609; NL 1,350; YT 327; NT 262; SK 171; MB 2 (`data/raw/magplot/MAGPlot_CONTENTS.md` §4). The open package is Tier 1, about 70.7% of sites. A WFS supplement is in progress and is not archived locally, so PEI and additional SK/NL records are not yet counted; do not report PEI coverage until it lands.

Limitations are material: no age, site-index, soil, or ecological tables; height missing for 39.3% of tree rows; coordinates fuzzed ±5–10 km (±10 km documented); variable-radius BC plots lack BAF/plot area; 1,027 BC sites lack coordinates (`data/raw/magplot/MAGPlot_CONTENTS.md` §§9–11; `data/raw/magplot/ACQUISITION.md`).

NFI documentation was also archived: ground-plot data dictionary v5.2, sampling guidelines 5.0, and compilation procedures 2.4 under `data/raw/nfi/ground_plots/documentation/`; photo-plot dictionaries, compilation, and land-cover documents under `data/raw/nfi/photo_plots/documentation/`. Plot data was not downloaded and remains request-only.

CASFRI is recorded as a pointer, not a payload: a polygon inventory framework, not a plot remeasurement dataset; the full database is request-only or self-build, and the NRCan CKAN record is WMS-only (`data/raw/casfri_v5/documentation/UPSTREAM_DATA.md`).

## 4. Access matrix for national plot sources

| Dataset | Holder | Level of measurement | Coverage / scale | Access | License | Fit for FVS-CA calibration |
|---|---|---|---|---|---|---|
| NFI Ground Plots | NRCan / CFS | Tree remeasurement; SI, age, volume, biomass, soil, climate | ~1,114–1,188 forested plots; all non-Arctic ecozones | Request + DUA; derived products; exact coords justification, ±10 km default (https://nfi.nfis.org/en/datarequestform) | OGL metadata; request-gated | **Essential for national calibration and SI.** Only national tree-level source with SI and age. |
| NFI Photo Plots | NRCan / CFS | Polygon/photo-interpreted; no tree lists | 26,139 grid locations; 18,570 target | Request + DUA (same form) | OGL metadata; request-gated | Validation/stratification only. |
| MAGPlot v1.2 | NFI Canada / NRCan | Individual-tree remeasurement | Tier 1 ~70.7%; 1920s–2020s; 1.57M remeasured | Open (https://open.canada.ca/data/en/dataset/8824392d-464e-413d-8bde-eaed61c79743) | OGL-Canada | **Best immediate substrate**; BC/QC-weighted; no age/SI. |
| BC FAIB ground plots | BC Ministry of Forests, FAIB | Tree remeasurement; real coords and SI | 12,242+ plots; 2,906 active PSPs; up to 12+ remeasurements | Open (https://catalogue.data.gov.bc.ca/dataset/824e684b-4114-4a05-a490-aa56332b57f4; https://github.com/bcgov/FAIBCompiler) | OGL-BC | **`fresh-fvsbc`'s existing corpus.** |
| Québec PEP | MRNF, Direction des inventaires forestiers | Tracked trees ≥9.1 cm + saplings + age/height subsamples | >12,000 plots; 10-yr cycle | Open (https://www.donneesquebec.ca/recherche/dataset/placettes-echantillons-permanentes-1970-a-aujourd-hui; https://github.com/CWFC-CCFB/QcPSP) | CC BY 4.0 | **Richer than MAGPlot for QC.** |
| NL FAL PSP | NL Fisheries, Forestry and Agriculture | Tree remeasurement; age, silviculture, disturbance | 1,380 plots (412 mature + 968 immature); 186,783+ mature remeasurement rows | Open ArcGIS (https://services8.arcgis.com/aCyQID5qQcyrJMm2/arcgis/rest/services/FAL_-_Permanent_Sample_Plots/FeatureServer) | NL OGL | **Best Atlantic coverage.** |
| Alberta PGYI / PSP | Alberta Forestry | Tree remeasurement | 2,193 sites; 1960–2024 | Request via AB Forestry + Tier 3; manual (https://open.alberta.ca/publications/alberta-government-forestry-permanent-sample-plots-manual) | DSA | **Critical Boreal Plain/Foothills gap.** |
| Ontario PSP / PGP / PIP | Ontario MNRF | Tree remeasurement | 5,477 sites; 1980–2023 | Request via `info.MNRscience@ontario.ca` + Tier 3; https://data.ontario.ca/dataset/ontario-growth-and-yield-data | DSA | **Critical Boreal Shield/Mixedwood.** |
| Nova Scotia PSP | NS Natural Resources and Renewables | Tree remeasurement | 3,368 sites; 1965–2022 | Request (https://novascotia.ca/natr/forestry/) | DSA | Fills Atlantic Maritime. |
| Manitoba / NWT | MB Forestry; GNWT | Tree remeasurement | 495 / 471 sites | Request; Tier 3 | DSA | Small; fills Taiga gaps. |
| CASFRI v5 | CASFRI / Université Laval | Polygon inventory; no remeasurement | 49 FRI snapshots | Request/self-build (https://github.com/CASFRI/CASFRI) | GPL-3.0 framework; provincial licences vary | Initial conditions only. |
| SCANFI v2/v3 | NRCan | 30 m wall-to-wall raster | National | Open (https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf) | OGL-Canada | Prediction surface, not calibration truth; circularity risk. |

## 5. Feasibility verdict for `fresh-fvsca`

MAGPlot alone supports regional calibration prototypes for BC, QC, NL, and YT, plus a small NB subset. It has 1.57M remeasured trees, a 10-year median interval, and usable live/dead, removal, treatment, and disturbance fields; fixed-area plots carry `stem_ha` (`data/raw/magplot/MAGPlot_CONTENTS.md` §§3, 9, 10).

It cannot support national calibration: no AB/ON/NS records; no age or site index; coordinates too approximate or incomplete for pixel linkage; BC variable-radius plots lack BAF; height missing for 39.3%.

The minimum data contract is the one `fresh-fvsbc` applies: ≥2 visits, persistent tree identity, species, DBH, live/dead, height or documented imputation, expansion factor/plot design, site key (BEC in BC; SI in ON), and disturbance/harvest exclusion (`https://github.com/UBC-FRESH/fresh-fvsbc/blob/main/docs/reference/calibration-data.rst`). Thresholds: 5,000 intervals/20 sites per variant-level unit; 10,000/40 per subzone, else pool to zone (`notes/reference/calibration/d9-bec-stratification.md` in `fresh-fvsbc`). The BC mortality fit used 1.78M live-tree intervals (`notes/reference/calibration/d9-provincial-mortality.md` in `fresh-fvsbc`).

| Option | What it is | Strength | Weakness | Verdict |
|---|---|---|---|---|
| (a) Multi-variant orchestrator + plot adapter + keyword bundles | Route each region to FVSbc/FVSon/FVSacd or a non-FVS backend; calibrate with bounded bundles | Preserves FVS-style outputs and the `fresh-fvsbc` investment; transparent per-region calibration | Integration is large; species and volume definitions need harmonizing | **Recommended** |
| (b) CFS meta-model pattern on SCANFI | Landscape meta-models fitted to complex-model simulations over SCANFI strata | Matches CFS architecture; cheap landscape prediction | Needs long simulations and a training design; public meta-models cover QC only | Fallback for unrepresented regions |
| (c) Empirical yield curves by ecozone × species × productivity, with CBM/GCBM | Aggregate yield tables plus a carbon model | Fast; easy to explain | Aggregates away stand dynamics; weak link to existing work; false-precision risk | Benchmark only |

Option (a) keeps the individual-tree outputs WS4 and FEMIC consume, keeps the BC calibration reusable, and makes unrepresented regions explicit rather than averaging them away. Option (b) needs simulation infrastructure that does not yet exist at national scale. Option (c) is a cross-check, not the primary model.

## 6. Acquisition plan

| Phase | Dataset | Route | Owner | Notes |
|---|---|---|---|---|
| A | MAGPlot ZIP + WFS supplement | ZIP archived at `data/raw/magplot/`; pull WFS and reconcile PEI/SK/NL | CLEWS data | Do not count PEI until the supplement is archived. |
| A | BC FAIB ground plots | BC Data Catalogue dataset `824e684b-4114-4a05-a490-aa56332b57f4`; FAIB FTP and `bcgov/FAIBCompiler` | CLEWS data | Standalone archive; do not rely on MAGPlot’s BC subset. |
| A | Québec PEP | Données Québec `PEP_GPKG.zip`; `CWFC-CCFB/QcPSP` | CLEWS data | 12,000+ plots; CC BY 4.0. |
| A | NL FAL PSP | ArcGIS FeatureServer (URL above) | CLEWS data | 1,380 plots; mature and immature layers. |
| A | SCANFI v3 | NRCan product page | CLEWS data | Confirm the release identifier and licence before download; v2 2025 is the archived baseline. |
| B | NFI Ground Plots | Data request form + DUA; add exact-coordinate justification | PI | Essential; derived products only by default. |
| B | Alberta PGYI / PSP | Alberta Forestry request + MAGPlot Tier 3 | PI | 2,193 sites; critical for Boreal Plain/Foothills. |
| B | Ontario PSP / PGP / PIP | `info.MNRscience@ontario.ca` + NFI Tier 3 | PI | 5,477 sites; critical for Boreal Shield/Mixedwood. |
| B | Nova Scotia PSP | NS DNRR request | PI | 3,368 sites. |
| B | Manitoba / NWT | Provincial/territorial request + Tier 3 | PI | 495 + 471 sites. |
| B | NFI Photo Plots | Data request form + DUA | PI | Validation and stratification only. |
| B | CASFRI | Request or self-build | Collaborator | Last; polygon inventory, not calibration data. |

Named contacts:

- Michael Smith (NFI coordinator); Omnia Ibrahim (NFI); Byron Smiley (CFS, MAGPlot/NFI plot harmonization); Mathieu Fortin (CFS, CFSStandGrowth/CFSMetaModel; `mathieu.fortin@nrcan-rncan.gc.ca`).
- BC FAIB Ground Sample Data team via the BC Data Catalogue dataset; Alberta Forestry via the PSP manual and data request; Ontario MNRF Science, `info.MNRscience@ontario.ca`; Québec MRNF Direction des inventaires forestiers, `Inventaires.Forestiers@mrnf.gouv.qc.ca`; NL Forest Analysis and Inventory via the ArcGIS service owner.

## 7. Risks and open questions

- **NFI DUA timeline.** No service-level agreement; 6–18 months is the observed federal pattern, and exact-coordinate approvals add delay (`planning/nfi_status_tracker.md` §3). Do not put NFI on the critical path for BC or QC.
- **MAGPlot fuzzing and missing site index.** Approximate coordinates prevent pixel linkage, and v1.2 has no site-index or age tables (`data/raw/magplot/MAGPlot_CONTENTS.md` §§9, 11). Site index must come from NFI, provincial PSPs, or documented imputation.
- **SCANFI circularity.** NFI plots may be in the SCANFI training set. Validation without an independence check violates the G6 hard stop (`planning/scientific-model-recovery-plan.md` §4, G6).
- **Provincial licensing.** Terms range from OGL-BC and CC BY 4.0 to DSA-only; Ontario GeoHub redistribution is prohibited in some cases (`planning/national_expansion_plan.md` §3.7). Track derived-products-only terms per source.
- **DBH limits.** PSP starts at 2 cm; CMI/YSM/VRI/NFI at 4 cm; some main plots at 9 cm (`data/raw/magplot/MAGPlot_CONTENTS.md` §9). Harmonize to 4 cm first, then document 2 cm supplements.
- **CLEWS gates.** G7 one-TSA pilot must pass before G8 BC scale-up; there is no national gate (`planning/scientific-model-recovery-plan.md` §4). This is data and contract work, not a validated national result.

## 8. Immediate next actions

| # | Action | Owner | Done when |
|---:|---|---|---|
| 1 | Publish MAGPlot to Arbutus (`arbutus-s3`) with manifest and checksum | CLEWS data | Object and manifest pushed and verified. |
| 2 | Pull the MAGPlot WFS supplement; reconcile PEI/SK/NL counts | CLEWS data | Supplement archived; province table updated. |
| 3 | Acquire BC FAIB, Québec PEP, NL FAL; acquire SCANFI v3 if published | CLEWS data | Archives plus provenance; SCANFI status recorded. |
| 4 | Fill the three DUA placeholder lines; submit the NFI request with exact-coordinate justification | PI | Request submitted; acknowledgement logged. |
| 5 | Send Alberta, Ontario, Nova Scotia, Manitoba, and NWT requests | PI + collaborator | Requests sent; tracker updated. |
| 6 | Write `fresh-fvsca` variant-profile and adapter design: registry, plot contract, SCANFI imputation, post-processor | Collaborator | Design reviewed against §5. |
| 7 | Update readiness gates and source inventory; do not flip a gate without evidence | CLEWS data | `readiness-gates.yaml` and `source-inventory.csv` updated. |

---

*End of document. No national calibration is claimed; the MAGPlot archive is open Tier 1, and all NFI and provincial request-only sources remain pending until a signed agreement is in hand.*
