# Forest-economics evidence v2d — QC procurement cost and BC partial coverage

> **Correction (2026-11-21): output layer renamed and QC semantics fixed.**
> The v2d layer is now `forest_economics_v2d/`, built by
> `workflows/national/harvest/forest_economics_v2d.py`. The old
> `net_value_v2d/` directory is deprecated. The QC reference is an exact
> validated copy of `procurement_benchmark/`: `107.22` is a buyer-side
> procurement cost, not revenue, and `25.82/0.81` are procurement-channel cost
> spreads. Every summary row is `net_value_status=not_estimated`,
> `policy_grade=false`, and `net_value_is_combined_national=false`. There is no
> net value for QC, no BC partial net value, and no combined national net value.
> Read `qc_procurement_reference.csv` and `planning/procurement-benchmark.md`.

| Field | Value |
|---|---|
| Document | `planning/net-value-v2d.md` |
| Date | 2026-11-21 |
| Status | Evidence layer complete; all net values deliberately not estimated. No merged-ledger, model, UI, or OSeCAN change. |
| Scope | QC 2022 survey procurement cost benchmark; BC 2019–2026 log-market prices, 2026 BCTS stumpage, and 2026 appraisal allowances |
| Companion | `planning/procurement-benchmark.md` (corrected QC cost-spread note); `planning/net-value-benchmark.md` (deprecated framing); `planning/forest-sector-evidence-v2.md`; `data/derived/national/harvest_basis/forest_economics_v2d/` |

**Decision summary.** The v2d layer separates seven evidence classes and refuses
to combine them. The QC 2022 T2/T20 quantities are procurement costs and cost
spreads, not revenue, not a new estimate, and not net value. BC has observed
mill-delivered log-price averages, observed BCTS standing-timber stumpage, and
published appraisal cost allowances for road, bridge, culvert, silviculture,
specified operations, and tenure obligations.
BC has **no** published general logging (tree-to-truck) or truck-haul $/m3:
both are embedded in the estimated winning bid (EWB). BC
`net_value_status=not_estimated` with reason `missing_logging_and_haul_cost`.
No BC partial net value is computed and no combined national net value exists.

## 1. Definitions

| Evidence tier | What it is | What it is not |
|---|---|---|
| `survey_procurement_cost` | QC 2022 T2 purchase cost vs T20 BGAD cost | Not revenue, not an observed transaction, not net value, not policy-grade |
| `survey_regional_procurement_cost` | QC 2022 regional conditional cost spread | Not an observed regional price or net value |
| `benchmark_historical` | BMMB 2011–2015 delivered-sale revenue, nominal | Not a 2022 price, not a cost stack, not deflated |
| `observed_price` | BC published mill-delivered log-market average | Not a transaction record, not volume-weighted by grade |
| `observed_stumpage` | BC BCTS winning bid for standing timber | Not delivered-log revenue, not a complete bid population |
| `appraisal_allowance` | BC IAM/CAM tabular cost allowance | Not an observed contractor cost, winning bid, or payment |
| `not_available` | Missing logging, truck haul, or full net value | Not zero |

Every generated row is `policy_grade=false`. The evidence classes are never
summed or re-keyed into one population.

## 2. QC procurement reference

The QC benchmark is not overwritten or re-derived. `qc_procurement_reference.csv`
copies 18 source rows (9 provincial and 9 regional) after validating the
procurement manifest, source hashes, arithmetic identities, and conditional
regional arithmetic. `bmmb_reference.csv` copies the three BMMB all-year
scopes. **These are procurement-channel cost spreads and historical revenue,
not net values:**

- Three provincial cost spreads: SEPM (107.22 purchase cost; 25.82 before /
  0.81 after transfers CAD/m3), hardwood sciage (5.69 / -14.53), poplar
  trituration (-7.27 / -9.45).
- Seven regional SEPM conditional cost spreads: 10.15–48.49 before transfers and
  -10.88–28.44 after transfers, with the provincial purchase cost held
  constant. The min/max bound rows are independent column-wise extrema.
- Three BMMB all-year scopes: Crown-only primary (9 full rates / 74,550 m3 /
  35.7547), teaching/research (2 / 15,350 / 50.6266), and all-in secondary
  (11 / 89,900 / 38.2940 CAD/m3).

Negative QC cost spreads are documented source arithmetic, not clipped or
filled. The QC numbers remain `net_value_status=not_estimated`, not
policy-grade.

## 3. BC revenue evidence

### 3.1 Log-market averages

The source is 15,705 reported price cells from 505 public PDFs. Blank cells are
missing, not zero. The v2d layer reports 1,852 annual/period range rows by
region, published vintage, log class, grade or end use, and species.

Key rules:

- **Vintages stay separate.** One-month and three-month series are never
  combined. Neither is annualized; overlapping three-month windows are not
  summed with monthly reports.
- **Interior aggregate rows are separate.** The 890 `weighted_average` cells
  with `is_aggregate=true` are reported as aggregate references and excluded
  from every product-cell range and volume sum.
- **Flagged cells are retained, not clipped.** 16 exact zeros, 7 non-zero values
  below CAD 1/m3, and 17 values above CAD 1,000/m3 are excluded from the primary
  range but kept in the source and counted per group.
- **No zero fill.** Blank price and volume cells are not emitted as zeros.

Period range of plausible product cells (CAD/m3, cross-grade and cross-species):

| Region | Vintage | Log class | Median | P25–P75 | Min–max |
|---|---|---|---:|---|---|
| Coast | 1-month | Old growth | 92.83 | 53.60–241.77 | 2.44–999.56 |
| Coast | 1-month | Second growth | 100.98 | 62.74–192.30 | 1.49–805.02 |
| Coast | 3-month | Old growth | 94.31 | 52.74–261.74 | 2.76–987.21 |
| Coast | 3-month | Second growth | 101.00 | 59.29–212.74 | 1.07–900.81 |
| Interior | 1-month | Not reported | 109.34 | 62.65–144.81 | 37.95–624.38 |
| Interior | 3-month | Not reported | 103.09 | 60.60–143.50 | 35.48–350.95 |

These are published-average ranges, not transaction prices and not a single
product price. Coast old growth is a published old/mixed-growth blend.

### 3.2 BCTS stumpage

The 2026 BCTS sample has 51 awarded sales: 49 full winning bids (upset plus
bonus) and 2 premium-only lower bounds because the upset value is missing. Full
bid range is 0.25–257.31 CAD/m3; volume-weighted 42.79 CAD/m3 over 1.25 Mm3.
This is standing-timber stumpage, not delivered revenue.

## 4. BC cost coverage and gap

| Component | Coverage | Rows | Unit bases | Net-value usability |
|---|---|---:|---|---|
| Road (development + management) | Published appraisal allowance | 124 | CAD/km, CAD/km/%, CAD/m3 | Requires road km and volume |
| Bridge | Published appraisal allowance | 260 | CAD/each | Requires structure counts and volume |
| Culvert | Published appraisal allowance | 207 | CAD/each, CAD/m | Requires counts/lengths and volume |
| Silviculture | Published appraisal allowance | 247 | CAD/ha, CAD/m3 | Partially direct; CAD/ha needs area and volume |
| Specified operations | Published appraisal allowance | 19 | CAD/m3, CAD/m3/km, CAD/tree | Conditional on route and operation |
| Tenure obligation (TOA) | Published appraisal allowance | 17 | CAD/m3, CAD/ha variants | Partially direct |
| Logging / tree-to-truck | **Not published** | 0 | — | Missing; embedded in EWB |
| General truck haul | **Partial only** | 5 | CAD/m3 (rail only) | Missing general truck rate; embedded in EWB |
| EWB | **Not published as a cost** | 0 | — | Stumpage formula, not a cost allowance |
| Full BC net value | **Not estimated** | 0 | — | `missing_logging_and_haul_cost` |

All 879 cost rows are `appraisal_allowance`, not observed costs. The covered
components use CAD/km, CAD/each, CAD/ha, and CAD/m3; they cannot be added into a
single $/m3 without road length, structure counts, treated area, and harvest
volume.

## 5. Why BC net value is not estimated

A BC full net value requires a consistent revenue and cost stack per m3. The
public appraisal manuals do not publish general logging or truck-haul $/m3;
both are embedded in the EWB. Consequently:

```text
log price - road allowance - silviculture allowance  !=  BC net value
```

That calculation omits logging and general truck haul, mixes units without site
quantities, and treats appraisal allowances as observed costs. It is prohibited
in this layer. BC rows carry `net_value_status=not_estimated` and
`net_value_reason=missing_logging_and_haul_cost`.

## 6. QC + BC comparison

| Dimension | QC 2022 survey benchmark | BC 2026 evidence | Comparability |
|---|---|---|---|
| Evidence tier | `survey_benchmark` | `observed_price` + `observed_stumpage` + `appraisal_allowance` | Not comparable |
| Revenue basis | Delivered-to-mill survey purchase, nominal 2022 | Mill-delivered published averages (2019–2026); standing-timber BCTS bids (2026) | Not comparable |
| Cost coverage | Full survey procurement stack including harvest and haul | Partial appraisal allowances; logging and general truck haul missing | Not comparable |
| Net value | `not_estimated`; SEPM procurement cost spread 25.82 / 0.81 CAD/m3 | `not_estimated` (`missing_logging_and_haul_cost`) | Not comparable |
| Policy grade | `false` | `false` | Neither is policy-grade |

The QC column is a complete survey benchmark for its own scope. The BC column is
partial coverage with an explicit gap. They are not two estimates of the same
quantity.

## 7. Non-claims

- Not an observed transaction net value.
- Not policy-grade; `policy_grade=false` on every row.
- Not a BC full net value and not a BC partial net value.
- Not a combined national net value.
- Not a mill margin; transformation and by-product revenue are excluded.
- Not a QC stumpage rate and not a BCTS delivered-log price.
- Not an observed contractor cost; appraisal allowances are policy inputs.
- Not merged into `forest_cost_revenue.csv`, the Forest LP, the UI, or OSeCAN.
- Not deflated; years and currency bases remain separate.
- Not a legal AAC, sustainable-supply estimate, or forecast.

## 8. Validation and reproduction

The builder fails closed on unexpected row counts, missing columns, duplicate
keys, non-finite values, negative BC prices/stumpage/costs, aggregate misuse,
vintage mixing, volume reconciliation failures, QC hash or arithmetic
mismatches, and any missing BC gap reason. All 36 checks pass.

```bash
.venv/bin/python -m workflows.national.harvest.forest_economics_v2d
.venv/bin/python -m pytest tests/test_forest_economics_v2d.py -q
.venv/bin/ruff check workflows/national/harvest/forest_economics_v2d.py tests/test_forest_economics_v2d.py
```

Outputs under `data/derived/national/harvest_basis/forest_economics_v2d/`:

```text
forest_economics_v2d_summary.csv
qc_procurement_reference.csv
bc_revenue_benchmark.csv
bc_cost_coverage.csv
bmmb_reference.csv
summary.json
FOREST_ECONOMICS_V2D_REPORT.md
manifest.json
```

## 9. What would unlock BC net value

1. A public or authorized general logging/tree-to-truck $/m3 and general
   truck-haul $/m3, or a documented decomposition of the EWB into cost
   components. Restricted ILCR/CCTMCS microdata were not used.
2. Site quantities to convert allowances: road km, bridge and culvert counts,
   treated hectares, and harvest volume by appraisal zone.
3. A region/grade/volume weighting for the log-price series and a cost stack
   from a matching vintage.

Until those exist, the layer reports revenue, stumpage, and partial allowance
coverage only, and labels BC net value as not estimated.

## 10. Use rules

1. Use the QC procurement cost spread as a benchmark or calibration target,
   not a ledger row and not a net value.
2. Use BC log-market prices as observed average revenue evidence, not
   transactions or a complete population.
3. Use BCTS stumpage as standing-timber evidence, never as delivered revenue.
4. Use appraisal allowances as policy inputs, never as observed costs.
5. Never subtract a subset of BC allowances from a log price and call it net
   value.
6. Never combine QC and BC into one national net value or one monetary total.
7. Do not change the Forest LP v1 objective, the merged ledger, the UI, or
   OSeCAN from this note.
