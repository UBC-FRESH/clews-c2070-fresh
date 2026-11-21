# QC net-value benchmark — 2022 survey and BMMB historical check

| Field | Value |
|---|---|
| Document | `planning/net-value-benchmark.md` |
| Date | 2026-11-21 |
| Status | Benchmark documentation. No ledger, model, or UI change. |
| Scope | Quebec 2022 forest-sector residual at the mill gate; BMMB 2011–2015 delivered-sale historical benchmark |
| Companion | `planning/forest-cost-revenue-ledger.md`; `data/derived/national/harvest_basis/net_value_benchmark/` |

**Decision summary.** The 2022 Quebec MRNF survey supports a transparent
before/after-transfer residual for SEPM and for two product rows. It does not
support a provincial net-value point estimate, a species-total residual, or a
policy-grade input. BMMB delivered sales remain a separate nominal 2011–2015
historical benchmark with no species or grade allocation. Every generated row
is `evidence=survey_benchmark` or `evidence=benchmark_historical`,
`policy_grade=false`, and `net_value_status=benchmark_only`.

## 1. Definitions

The benchmark uses the survey synthesis rows, not the overlapping component
tables:

| Field | Source | Meaning |
|---|---|---|
| `delivered_purchase` | T2 `purchase.wood_purchase_delivered` | Delivered wood purchase price to mill, CAD/m3, `delivered_to_mill`. |
| `procurement_before_transfers` | T20 `synthesis_procurement.procurement_total_before_transfers` | Harvest + road + haul + hosting + other costs, CAD/m3. |
| `transfers_total` | T20 `synthesis_procurement.transfers_total` | Payments to the State, CAD/m3. |
| `procurement_after_transfers` | T20 `synthesis_procurement.procurement_total_after_transfers` | Before-transfer procurement + transfers, CAD/m3. |

Formulas:

```text
residual_before_transfers = delivered_purchase - procurement_before_transfers
residual_after_transfers  = delivered_purchase - procurement_after_transfers
```

The two residuals satisfy:

```text
procurement_after_transfers = procurement_before_transfers + transfers_total
residual_before_transfers   = residual_after_transfers + transfers_total
```

This is the ledger identity `pre-Crown surplus = forest-sector net value +
Crown revenue` at the survey's 2022 basis. The before-transfer residual is a
pre-Crown surplus, not a mill margin. Transformation, lumber, and by-product
revenue are deliberately excluded: netting those against log costs would
produce a processing margin, not forest net value.

## 2. Sources

| Source | Role | Status |
|---|---|---|
| `data/derived/national/harvest_basis/qc_cost_survey_2022/qc_cost_revenue_survey.csv` | T2 purchase and T20 synthesis rows, 2022 nominal CAD/m3 | Observed survey aggregates |
| `data/derived/national/harvest_basis/qc_cost_survey_2022/manifest.json` | Row count, hashes, reconciliation, limitations | Provenance |
| `data/derived/national/harvest_basis/bmmb_delivered/bmmb_delivered_sales.csv` | 18 delivered-to-destination auction sales, 2011–2015 nominal CAD | Observed auction sample |
| `data/derived/national/harvest_basis/provincial_allocation/{100m,119m,150m}.csv` | QC harvest volumes by screening case | Screening allocation, not observed harvest |
| `planning/forest-cost-revenue-ledger.md` | Definitions, perspectives, identity, non-mixing rules | Design authority |

The survey has 660 numeric observations and 37 respondents in the regional
provincial-total row. Provincial survey means are not volume-weighted to the
provincial harvest. The 109 `three_respondents` values are retained as flags;
pine and cedar purchase prices are three-respondent means. Regional R09 has
`sample_n=3` and is flagged as a three-respondent regional mean even though the
source's `confidentiality_flag` is `none`.

## 3. Results

### 3.1 Provincial residual (2022, CAD/m3 nominal)

| Species | Product | Purchase | Before | Transfers | After | Residual before | Residual after |
|---|---|---:|---:|---:|---:|---:|---:|
| SEPM | total | 107.22 | 81.40 | 25.01 | 106.41 | 25.82 | 0.81 |
| Hardwood | sciage | 85.42 | 79.73 | 20.22 | 99.95 | 5.69 | -14.53 |
| Poplar | trituration | 65.01 | 72.28 | 2.18 | 74.46 | -7.27 | -9.45 |

Pine (purchase 101.09) and cedar (purchase 80.58) have no matching T20 synthesis
row, so their residual is not computable. Hardwood and poplar totals are also
purchase-only for the same reason. Product rows are not additive to a species
total without published volumes.

The SEPM after-transfer residual is small (0.81 CAD/m3) because 25.01 of the
25.82 pre-Crown surplus is transferred to the State. Hardwood sciage is negative
after transfers; poplar trituration is negative on both definitions. Negative
residuals are reported as published arithmetic and are not clipped to zero.
They are not evidence of an error by themselves, but they are a warning that the
product-level cost and price bases are not interchangeable with the SEPM total.

### 3.2 Regional range (SEPM only, 2022)

Seven published regional groups have T20 synthesis procurement rows. They do
not publish a regional purchase price. The residual range therefore holds the
provincial SEPM purchase price of 107.22 constant:

| Region | n | Before | After | Residual before | Residual after |
|---|---:|---:|---:|---:|---:|
| R01-R03-R11-R12 | 11 | 58.73 | 78.78 | 48.49 | 28.44 |
| R09 | 3 | 96.58 | 118.10 | 10.64 | -10.88 |
| R02 | 6 | 82.09 | 111.86 | 25.13 | -4.64 |
| R04 | 4 | 73.43 | 103.42 | 33.79 | 3.80 |
| R14-R15-R07-R08 | 5 | 97.07 | 114.57 | 10.15 | -7.35 |
| R08 | 4 | 82.04 | 110.40 | 25.18 | -3.18 |
| R10 | 4 | 89.30 | 111.75 | 17.92 | -4.53 |

Range: procurement before 58.73–97.07; after 78.78–118.10; residual before
10.15–48.49; after -10.88–28.44 CAD/m3. Five regions have a negative
after-transfer residual under the constant provincial purchase price. This is a
conditional procurement-cost range, not an observed regional net value.

### 3.3 BMMB historical benchmark (nominal 2011–2015)

The BMMB delivered subset has 18 sales, 13 awarded, 11 full rates, and 2
partial awards. The 11 full rates cover 89,900 m3 and CAD 3,442,633.50:

```text
volume-weighted rate = 3,442,633.50 / 89,900 = 38.2940 CAD/m3
median rate          = 36.6668 CAD/m3
```

The two 2012 partial awards (CAD 436,325) are excluded from the rate and kept
only as 10.6588 and 39.0211 CAD/m3 lower bounds. The table is **nominal, 18
sales, no grade split**. It is not deflated or compared with the 2022 survey as
if it were the same price year. The archive does not publish species, grade,
destination, or service-bundle allocation, and the sale type is not confirmed
as mill gate.

### 3.4 QC total sensitivity (screening, M CAD)

QC harvest volumes are clearly mapped from the provincial allocation screening
files. Applying the SEPM residual gives:

| Case | QC volume (Mm3) | Before transfers (M CAD) | After transfers (M CAD) |
|---|---:|---:|---:|
| 100m | 22.184 | 572.8 | 18.0 |
| 119m | 26.674 | 688.7 | 21.6 |
| 150m | 34.115 | 880.9 | 27.6 |

The range is 572.8–880.9 M CAD before transfers and 18.0–27.6 M CAD after
transfers. It is a sensitivity only: all QC volume is assumed to earn the SEPM
residual, no species mix is available, the survey is not volume-weighted to the
provincial population, and the allocation volume is a screening total, not an
observed harvest or a forecast.

## 4. Validation

The builder recomputes, and fails closed on:

- T20 cost components summing to `procurement_total_before_transfers`;
- before-transfer procurement plus transfers equalling after-transfer
  procurement;
- every residual from the delivered purchase price;
- the T2/T21 purchase-price overlap (T21 is a cross-check, not a second input);
- the seven regional groups and their range;
- the BMMB weighted and median rate, full-rate count, and partial-award
  exclusion;
- the QC sensitivity arithmetic.

It rejects missing rows, duplicate keys, unexpected units, non-finite values,
and arithmetic mismatches. The overlapping T2/T21 purchase rows are not both
used: T2 is the input, T21 is a consistency check only.

## 5. Non-claims

- Not an observed transaction net value.
- Not a policy result and not `policy_grade`.
- Not a mill margin; transformation and lumber revenue are excluded.
- Not a stumpage rate.
- Not a current or representative BMMB price.
- Not a provincial harvest-population estimate.
- Not merged into `forest_cost_revenue.csv`, the Forest LP, or the UI.
- Not deflated; BMMB remains nominal 2011–2015.
- Not species- or grade-allocated.
- Not a legal AAC, sustainable-supply estimate, or forecast.

## 6. Use rules

1. Use the SEPM before/after-transfer residual as a **benchmark or calibration
   target**, not as an observed ledger row.
2. Do not merge these rows into the merged cost/revenue ledger. The survey
   aggregate grain, sample, and price basis are different.
3. Do not net transformation, lumber, or by-product revenue against log costs.
4. Do not present the BMMB rate as a 2022 price, a stumpage rate, or a mill-gate
   transaction price.
5. Do not use the QC sensitivity as a policy result. It is a bounded arithmetic
   sensitivity with an explicit species-mix assumption.
6. Do not change the Forest LP v1 objective or the UI from this note.

## 7. Reproduction

```bash
.venv/bin/python -m workflows.national.harvest.net_value_benchmark_v1
.venv/bin/python -m pytest tests/test_net_value_benchmark.py -q
```

Outputs:

```text
data/derived/national/harvest_basis/net_value_benchmark/
  qc_net_value_benchmark.csv
  qc_net_value_regional_range.csv
  bmmb_historical_benchmark.csv
  qc_net_value_sensitivity.csv
  summary.json
  NET_VALUE_BENCHMARK_REPORT.md
  manifest.json
```

## 8. Next

The BC benchmark remains blocked on observed appraisal-cost data landing. When
BC costs arrive, build the same before/after-transfer residual with a separate
BC source and do not apply QC values to BC or vice versa.
