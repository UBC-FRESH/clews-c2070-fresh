# Forest-Sector Evidence v2

| Field | Value |
|---|---|
| Document | `planning/forest-sector-evidence-v2.md` |
| Date | 2026-11-21 |
| Status | Evidence collection complete; v2d forest-economics evidence layer complete. QC procurement cost and BMMB historical revenue only; BC partial appraisal coverage; all net values deliberately `not_estimated`. Documentation only; no model, UI, data, or interface change is implemented by this note. |
| Scope | Observed and model-input evidence for BC/QC log markets, costs, and the national operable-landbase screen |
| Audience | FRESH team; Taco Niet and the energy/OSeCAN policy team |
| Companion documents | `planning/forest-cost-revenue-ledger.md`; `planning/forest-sector-lp-v1.md`; `planning/meris-qc-extraction.md`; `planning/module-boundaries.md`; `planning/harvest-aac-feasibility.md`; `planning/procurement-benchmark.md`; `planning/net-value-v2d.md` |

**Decision summary.** Forest-Sector Evidence v2 assembles the public evidence
needed to describe BC/QC log-market and cost conditions and to screen the
national operable landbase. It is **screening evidence**, not a legal timber
harvesting landbase (THLB), an allowable annual cut (AAC), a growth-and-yield
estimate, or a policy-grade net-value result. The evidence package separates
observed records from model input, refuses to mix them, and records each
limitation rather than filling it with a proxy.

## 1. Scope and non-claims

### 1.1 In scope

- A source review of the available BC/QC log-price, cost, and landbase evidence.
- A jurisdiction-resolved cost/revenue ledger with observed and model-input
  rows kept distinct.
- Stand-alone observed datasets for the Quebec BMMB delivered auctions, the
  Quebec MRNF 2022 forest cost/revenue survey, and the BC Coast/Interior
  log-market reports.
- A national operable-landbase screen with age, fire, road-proximity, and slope
  sensitivities.
- The evidence needed to decide whether a BC benchmark and a v2d forest-economics
  evidence layer are ready to publish.

### 1.2 Non-claims

- **Not a legal THLB.** The operable proxy is a screening landbase assembled
  from SCANFI, tenure-share, fire, road, and coarse-slope layers. It is not a
  legal harvest-landbase determination and must never be labelled `THLB`.
- **Not an AAC.** Documented AAC, NFD, planning-volume, and proxy ceilings are
  carried as separate evidence. Their sum is not a national AAC.
- **Not a growth-and-yield estimate.** Target-volume divided by operable area is
  an intensity arithmetic check. It does not demonstrate sustainable supply,
  site productivity, or future yield.
- **Not a policy-grade net value.** The ledger reports `net_value_status =
  not_estimated` wherever a required component is missing or partial. No
  transaction-level delivered-log price and full production cost pair exists
  for either province.
- **Not an energy-policy result.** Energy-policy design, capacity expansion,
  transmission, curtailment, carbon price, and the OSeCAN model remain with
  Taco Niet and the energy/OSeCAN policy team. FRESH owns only the forest-supply
  evidence upstream of the `SUPBCW`/`SUPBCWB1`/`SUPBCWB2` interface.

### 1.3 Evidence rules

- Observed rows and model-input rows are counted and reported separately. They
  are never summed, monetised together, or presented as one population.
- Missing is missing, not zero. Suppressed, blank, and unavailable values are
  recorded as gaps.
- Source series that are alternatives are non-additive: QC courant and tendance
  price series, and the quality-class and DBH curve families, remain separate.
- A screening result is not promoted to `policy_grade` by a later pipeline
  step. The label travels with the artifact.

## 2. Workstream status

| Workstream | Status | Primary evidence | Data / parent commits |
|---|---|---|---|
| v2a source review | Complete | Source inventory and findings consolidated in this note and the stand-alone dataset reviews | — |
| v2b cost/revenue ledger | Complete (ledger only) | `forest_cost_revenue.csv`, 2,888 rows | `0b4285f` / `06d3187` |
| BMMB delivered auctions | Complete, observed benchmark, Crown/teaching/all-in split | 18 delivered sales, 13 awarded; Crown-only 9 full rates / 74,550 m3 / 35.7547 CAD/m3 | `5ec04fc` / `0e82310` |
| QC MRNF 2022 survey | Complete, observed aggregates, corrected flags | 660 survey observations; 128 three-respondent values | `e5fb044` / `2c81500` |
| BC log market | Complete, observed averages, corrected parser | 15,705 price cells from 505 PDFs | `1fc5165` / `107abdc` |
| Operable proxy v2 | Complete, screening | core20 99.87 Mha; core30 77.05 Mha | `5181122` / `1ef587a` |
| Slope screen | Complete, screening | 960 m MRDEM-30; >30% removes 1.51% | `30729b0` / `6aa01d5` |
| Operable proxy v3 | Complete, screening | core20 >30% 98.39 Mha; core30 76.01 Mha | `aeac8af` / `36cedf3` |
| v2d forest-economics evidence | Complete, evidence only, no net value | 14 summary rows; QC procurement cost reference; BC price/stumpage/appraisal; all `not_estimated` | `567ed97` / `8acc794` |

## 3. Completed evidence

### 3.1 v2a source review

The source review's main negative finding is that Quebec publishes no public,
transaction-level mill-settlement ledger, and no comparable BC transaction
ledger is available. The available evidence is a set of samples, published
averages, survey aggregates, and model-input decks.
Each can support a benchmark or a calibration target; none can be treated as a
province-wide transaction population.

| Source | What it provides | What it does not provide | v2 use |
|---|---|---|---|
| Quebec BMMB archived auctions | Delivered-to-destination sale totals for 18 sales, 2011–2015 | Species/grade price, destination, service bundle, current period | Observed delivered-sale benchmark only |
| Quebec MRNF/BMMB 2022 survey | 660 provincial/regional procurement, transformation, and revenue aggregates | Mill-level records, observed permit volume, respondent detail | Cost/revenue benchmark and calibration target |
| Quebec MERIS 3.3.0 | 191 tarification zones; administered rates; cost and model-input revenue decks | Observed permit volume or observed delivered price | `model_input` ledger rows only |
| BC Coast/Interior log-market reports | 505 PDFs; 15,705 published average price cells, 2019–2026 | Transaction-level records, full grade volumes, complete monthly coverage | Observed mill-delivered average benchmark |
| NRCan MRDEM-30 | 30 m national DTM for slope screening | A 30 m operability classification or legal landbase | Coarse slope screen at 960 m |
| Legal THLB sources | None usable as a national legal layer | A legal THLB boundary | Explicit gap; screening proxy only |

The BC legal THLB layer is restricted and is not part of this package. The
available BC area weight is a label-point sample, not a legal boundary. For
Quebec, MERIS is a tarification and cost-parameter system, not a register of
actual harvest.

### 3.2 v2b cost/revenue ledger

The merged ledger is a concatenation of two province blocks. It is never
re-keyed across provinces and never summed into a combined monetary total.

| Block | Rows | Evidence class | Perspective | Role |
|---|---:|---|---|---|
| BC BCTS auction ledger | 200 | `observed` | Crown 100; forest sector 100 | Observed stumpage base and bid premium |
| QC MERIS model deck | 2,688 | `model_input` | Forest sector | Model-input cost and revenue parameters |
| **Total** | **2,888** | 200 observed + 2,688 model input | 100 Crown + 2,788 forest sector | Two non-additive perspectives |

Key ledger facts:

- **100 paired mirrors.** The Crown and forest-sector views of the same
  stumpage transfer are stored as paired rows with opposite signs.
- **2,075 delivered-log rows.** They comprise 251 quality-class price-deck rows
  and 1,824 species/DBH curve rows. All are `model_input`; **0 are observed**.
- **No observed delivered-log revenue.** BC BCTS sells standing timber, so its
  upset and bonus are stumpage, not a log price. The QC MERIS values are
  mill-gate model inputs, not settlements.
- **Series are non-additive.** QC `courant` and `tendance` are alternative
  scenarios. The quality-class and species/DBH curve families are separate
  model-input representations and are not summed.
- **Net value remains `not_estimated`.** BC has no delivered revenue and no
  production costs. QC has partial model-input components: harvest is
  loading-only, road excludes tertiary road, and silviculture is a CAD/ha
  catalogue without a yield conversion.

### 3.3 BMMB delivered auctions

| Measure | Observed delivered subset |
|---|---:|
| Sales offered | 18 |
| Sales awarded | 13 |
| Sales with an observed price | 13 |
| Full CAD/m3 rate computable | 11 |
| Partial-award lower bound only | 2 |
| Years | 2011, 2012, 2014, 2015 |
| Offered volume | 159,850 m3 |
| Awarded volume | 108,750 m3 |
| Observed sale value | 3,878,958.50 CAD nominal |
| Value with a computable rate | 3,442,633.50 CAD over 89,900 m3 |
| Crown-only primary weighted rate | **35.7547 CAD/m3** (9 full rates; 74,550 m3) |
| Teaching/research weighted rate | **50.6266 CAD/m3** (2 full rates; 15,350 m3) |
| All-in secondary weighted rate | **38.2940 CAD/m3** (11 full rates; 89,900 m3) |
| Individual full-rate range | 30.1042–51.3995 CAD/m3 |

The dataset is stand-alone and is not merged into the ledger. The archive
publishes lot totals, not species or grade prices. The 156
species-by-product volume lines are unpriced and must not be used to allocate
the sale total. The destination field is empty, so the price point is
"delivered to destination", not a confirmed mill gate. The 2012 partial awards
are lower bounds and are excluded from the weighted rate. Values are nominal
CAD and must not be mixed with 2023-dollar MERIS model input without a
documented deflator.

Only nine of the eleven computable sales are Crown public-forest sales; they
cover 74,550 m3 and give the **35.7547 CAD/m3** primary benchmark. The other two
are teaching/research forest lots (15,350 m3, **50.6266 CAD/m3**). Pooling all
eleven gives **38.2940 CAD/m3**, which the non-Crown lots raise by
**+2.54 CAD/m3**; the all-in figure is secondary and must not be presented as a
Crown benchmark. The 2012 re-offer overlap is **2 sales / 22,200 m3**; it
double-counts offered and awarded volume totals but does not change the
full-rate numerator or denominator because both re-offer sales are excluded
from the rate.

### 3.4 Quebec MRNF 2022 survey

| Measure | Value |
|---|---:|
| Numeric observations | 660 |
| Source tables parsed | 23 |
| Additive reconciliations passed | 56 |
| Known source inconsistencies | 1 (Table 14, peupliers-sciage overhead) |
| Direct PDF spot checks passed | 8 |
| Three-respondent means flagged | 128 |

Provincial SEPM procurement stack, nominal 2022 CAD/m3:

| Basis | CAD/m3 | Note |
|---|---:|---|
| Delivered wood purchase | 107.22 | Table 21 purchase series; buyer-side procurement cost, not log-sale revenue |
| Procurement before transfers | 81.40 | Table 20 synthesis |
| Transfers to the State | 25.01 | Table 20 synthesis |
| Procurement after transfers | 106.41 | Table 20 synthesis |
| Procurement-channel cost spread before transfers | **25.82** | Derived: 107.22 − 81.40; not net value |
| Procurement-channel cost spread after transfers | **0.81** | Derived: 107.22 − 106.41; not net value |

The two spreads are derived arithmetically in this note; they are not printed
source rows. They are procurement-channel cost spreads, not forest-sector net
value. The T2 purchase cost is a buyer-side cost, and the purchase and BGAD
values come from different respondent samples. The survey therefore cannot
support a QC net-value claim or a volume-scaled M CAD sensitivity. The dataset
is intentionally not merged into the ledger. It provides a full
harvest/road/haul cost stack at provincial and regional aggregation, but it has
no mill-level or respondent-level values and no observed volume register. It is
a benchmark, not a drop-in ledger input and not a revenue population.

### 3.5 BC log market

| Series | Reports | Period | Gap |
|---|---:|---|---|
| Coast 1-month old growth | 85 | 2019-01 to 2026-01 | none |
| Coast 1-month second growth | 84 | 2019-01 to 2026-01 | 2023-10 not listed |
| Coast 3-month old growth | 84 | 2019-01 to 2025-12 | none |
| Coast 3-month second growth | 83 | 2019-01 to 2025-12 | 2021-03 not listed |
| Interior 1-month | 85 | 2019-01 to 2026-09 | 2026-01 to 2026-08 not listed |
| Interior 3-month | 84 | 2019-01 to 2025-12 | none |
| **Total** | **505** | 2019–2026 | published gaps only |

The dataset is stand-alone and is not merged into the ledger, model, or UI.
It has 15,705 reported price cells: 13,128 Coast grade-by-species
cells out of 54,120 possible, and 2,577 Interior product-by-species cells out of
9,464 possible. Blank cells are not zeros and are not emitted as price rows.
Prices are published averages with `price_basis = mill_delivered` and
`observation_type = reported_market_average`; they are not transaction records.
Values are nominal CAD (`currency_basis = CAD_nominal`). One-month and
three-month reports are independent vintages, three-month windows overlap, and
Interior `weighted_average` rows carry `is_aggregate = true` and must not be
added to product cells. Coast old-growth values are a published blend of old and
mixed growth, and three-month totals are not reconciled across publication
vintages.

### 3.6 Operable proxy v2

The v2 screen starts from the v1 merchantable proxy (SCANFI treed classes,
height, biomass, closure, and managed share) and adds age, 2020 fire, and public
road proximity.

| Case | Age | Disturbance | Road | Operable area (Mha) |
|---|---|---|---:|---:|
| Base | — | none | — | 144.69 |
| Age ≥ 20 | ≥ 20 yr | none | — | 134.56 |
| Age ≥ 30 | ≥ 30 yr | none | — | 127.37 |
| 2020 fire excluded | — | NBAC 2020 | — | 144.67 |
| Road ≤ 5 km | — | none | ≤ 5 km | 69.60 |
| Road ≤ 10 km | — | none | ≤ 10 km | 87.97 |
| Road ≤ 20 km | — | none | ≤ 20 km | 107.20 |
| **core20** | ≥ 20 yr | NBAC 2020 | ≤ 20 km | **99.87** |
| **core30** | ≥ 30 yr | NBAC 2020 | ≤ 10 km | **77.05** |

Road proximity is the binding screen. Age removes 21.08 Mha below 20 years and
37.93 Mha below 30 years; the 2020 fire mask removes only 0.06 Mha. NRN public
roads are an optimistic access proxy because resource, industrial, winter, and
decommissioned roads are absent. No slope filter is applied in v2.

### 3.7 National 960 m slope screen

The slope layer is derived from the NRCan MRDEM-30 DTM, averaged to a 960 m
processing grid with a Horn 3×3 kernel. Forest-weighted steep shares are applied
to the v2 operable area as an overlay, not as a pixel-exact intersection.

| v2 case | Operable before (Mha) | >30% lost (Mha) | >30% lost share | Operable after >30% (Mha) | >15% lost share |
|---|---:|---:|---:|---:|---:|
| Base | 144.69 | 2.19 | 1.51% | 142.50 | 8.66% |
| core20 | 99.87 | 1.48 | 1.48% | **98.39** | 8.28% |
| core30 | 77.05 | 1.04 | 1.35% | **76.01** | 7.62% |

The national forest-weighted mean slope is 2.30%; the p99 is 24.93%. Local 30 m
slopes can be much steeper than the 960 m average. The layer cannot classify
harvest systems, cable/ground operability, or site degradation.

### 3.8 Operable proxy v3

v3 combines the v2 cases with the slope screen. The `none` case reproduces v2;
the `>30%` case is the screening exclusion used for the current intensity
arithmetic.

| Case | Operable (Mha) | 100M (m3/ha/yr) | 119M (m3/ha/yr) | 150M (m3/ha/yr) |
|---|---:|---:|---:|---:|
| core20, no slope | 99.87 | 1.001 | 1.191 | 1.502 |
| **core20, >30%** | **98.39** | **1.016** | **1.209** | **1.525** |
| core30, no slope | 77.05 | 1.298 | 1.544 | 1.947 |
| **core30, >30%** | **76.01** | **1.316** | **1.565** | **1.973** |

The 150M path is an upper scenario bound, not a forecast or AAC. The 119M path
is the v2 reference base (118,977,339 m3/yr). These intensities are target
volume divided by post-slope operable area; they are not growth/yield estimates
and do not demonstrate sustainability.

### 3.9 BC appraisal cost allowances

The IAM/CAM and silviculture extraction publishes **879 allowance rows** (280
Coast and 599 Interior). Every row is `appraisal_allowance`, not an observed
contractor cost, winning bid, or payment. The rows map to six covered
components: road, bridge, culvert, silviculture, specified operations, and
tenure obligation (TOA). The covered components use CAD/km, CAD/each, CAD/ha,
and CAD/m3; 67 rows are direct CAD/m3 and the rest require site quantities
before they can be converted.

General logging/tree-to-truck, general truck haul, and the estimated winning
bid (EWB) are not published as $/m3 costs. Truck haul is represented only by
five rail-cycle CAD/m3 rows. No total BC cost per m3 is computed, and no
allowance is treated as an observed cost.

### 3.10 v2d forest-economics evidence layer

`forest_economics_v2d/` is the completed evidence layer. It emits 14 summary
rows (5 QC and 9 BC). Every row carries `net_value_status=not_estimated`,
`policy_grade=false`, `net_value_is_combined_national=false`, and an explicit
`net_value_reason`; no monetary net-value column exists. All 37 builder checks
pass.

- **QC reference.** 18 exact validated copies from `procurement_benchmark/`
  (9 provincial and 9 regional), including the restored three-respondent flags
  on `qc_2022_peupliers_trituration` and `R09`. The purchase cost remains a
  buyer-side procurement cost, and the 25.82 / 0.81 values remain
  procurement-channel cost spreads.
- **BMMB reference.** Three nominal historical scopes: Crown-only primary
  35.7547, teaching/research 50.6266, and all-in secondary 38.2940 CAD/m3,
  with the 22,200 m3 re-offer caveat.
- **BC evidence.** 1,852 annual/period price range rows derived from 15,705
  published cells; observed 2026 BCTS standing-timber stumpage; and the 879
  appraisal allowance rows. One-month and three-month vintages stay separate,
  Interior aggregates are not added to product cells, and blank cells are not
  zero-filled.
- **No net value.** QC `not_estimated`; BC `not_estimated` with reason
  `missing_logging_and_haul_cost`; no BC partial net value and no combined
  national net value. The layer is isolated from the merged ledger, Forest LP,
  UI, and OSeCAN.

### 3.11 Deprecated net-value artifacts

The former `net_value_v2d/` and `net_value_benchmark/` outputs are retained
only for audit with `DEPRECATED.md` pointers. `net_value_benchmark_v1.py`
carries a deprecation banner, and its test module is audit-only. Neither the
withdrawn "residual" framing nor the M CAD sensitivity may be cited as current
evidence.

## 4. Evidence gaps

| Gap | Consequence | Status |
|---|---|---|
| BC general logging and truck-haul $/m3 | No full BC net value; v2d reports appraisal allowances and stamps `missing_logging_and_haul_cost` | Open |
| QC observed revenue basis | T2 purchase is a buyer-side procurement cost, not log-sale revenue; no observed delivered-price population | Open |
| No observed QC transaction ledger | BMMB is a small auction sample; MERIS is model input; no provincial delivered-price population | Open |
| BC THLB restricted | No legal BC THLB; area weights are a label-point sample | Open |
| 13.27 Mha ecoprovince gap | 3.14% of SCANFI treed area has no independent ecoprovince slope statistics | Open; nearest-ecoprovince attribution, not zero-filled |
| No legal operability | 960 m slope overlay is not a 30 m operability or harvest-system classification | Open |
| Observed/model-input mixing prohibited | Any mixed monetary total or province-wide scaling is invalid | Enforced by ledger and reports |

Additional source limits:

- BMMB has no species, grade, destination, or service-bundle detail; values are
  nominal CAD and end in 2015.
- The QC survey is aggregate-only, has no observed volume, and contains one
  documented internal inconsistency.
- The BC log-market series are averages, not transactions; Coast grade volumes
  are unavailable and publication vintages are not reconciled.
- MERIS has no permit-volume register, no zone-to-UA crosswalk, no full harvest
  equation per zone, and no haul destination mix.

## 5. Next steps

1. **Complete the BC benchmark gaps (open).** The appraisal-manual extraction is
   complete for road, bridge, culvert, silviculture, specified operations, and
   TOA, labeled as allowances rather than audited actuals. A full BC net value
   still requires public general logging/tree-to-truck and general truck-haul
   $/m3, or a documented EWB decomposition, plus site quantities.
2. **v2d forest-economics evidence layer (complete).** The layer is published
   with separated evidence classes and explicit `net_value_status=not_estimated`
   on every row. QC procurement costs are not revenue; BC partial coverage has
   no partial net value; no combined national net value exists.
3. **Later model/UI wiring (not started).** Wire the evidence into the model or
   UI only after a separately authorized change. No interface change, no OSeCAN
   change, and no harvest recommendation follows from this note.

## 6. Provenance

The paired commits below identify the data-repository artifact and the parent
repository record. All derived reports and manifests are under
`data/derived/national/harvest_basis/` and
`data/derived/national/managed_landbase/`.

| Workstream | Data commit | Parent commit | Primary artifact |
|---|---|---|---|
| v2b ledger hardening | `0b4285f` | `06d3187` | `forest_cost_revenue/` |
| BMMB delivered auctions (Crown/teaching split) | `5ec04fc` | `0e82310` | `bmmb_delivered/` |
| QC MRNF 2022 survey (corrected flags) | `e5fb044` | `2c81500` | `qc_cost_survey_2022/` |
| BC log market (parser fix) | `1fc5165` | `107abdc` | `bc_log_market/` |
| BC appraisal cost allowances | `1a8bd41` | `b90f00e` | `bc_appraisal_costs/` |
| QC procurement cost benchmark | `567ed97` | `8acc794` | `procurement_benchmark/` |
| v2d forest-economics evidence | `567ed97` | `8acc794` | `forest_economics_v2d/` |
| Operable proxy v2 | `5181122` | `1ef587a` | `operable_proxy_v2/` |
| 960 m slope screen | `30729b0` | `6aa01d5` | `slope_screen/` |
| Operable proxy v3 | `aeac8af` | `36cedf3` | `operable_proxy_v3/` |

The v2d rebuild that fixes the confidentiality-flag, `benchmark_id`, and
evidence-table review findings is the paired `567ed97` / `8acc794` row above.
The preceding v2d publication is `1ed8550` / `1d165e9`.

## References

- `planning/forest-cost-revenue-ledger.md` — ledger perspectives, fields, and
  merge rules.
- `planning/forest-sector-lp-v1.md` — v1 allocation design and v2/v3 staging.
- `planning/meris-qc-extraction.md` — MERIS extraction and model-input limits.
- `planning/module-boundaries.md` — FRESH/OSeCAN ownership and interface rules.
- `planning/harvest-aac-feasibility.md` — AAC/NFD/proxy ceilings and cost-data
  inventory.
- `data/derived/national/harvest_basis/forest_cost_revenue/FOREST_COST_REVENUE_COVERAGE.md`
- `data/derived/national/harvest_basis/bmmb_delivered/README.md`
- `data/derived/national/harvest_basis/bc_log_market/README.md`
- `data/derived/national/harvest_basis/qc_cost_survey_2022/README.md`
- `data/derived/national/harvest_basis/bc_appraisal_costs/README.md`
- `data/derived/national/harvest_basis/procurement_benchmark/PROCUREMENT_BENCHMARK_REPORT.md`
- `data/derived/national/harvest_basis/forest_economics_v2d/FOREST_ECONOMICS_V2D_REPORT.md`
- `data/derived/national/managed_landbase/operable_proxy_v2/OPERABLE_PROXY_V2_REPORT.md`
- `data/derived/national/managed_landbase/slope_screen/SLOPE_SCREEN_REPORT.md`
- `data/derived/national/managed_landbase/operable_proxy_v3/OPERABLE_V3_REPORT.md`
