# WS4 sustainable 100 and 150 Mm³/yr cases

**Status:** pass. The 100 and 150 Mm³/yr documented provincial ceiling cases
are each sustained for 100 years on the national 990 m WS4 estate with the
same whole-block scheduler used for the sustainable 119 Mm³/yr case. No period
shortfall is recorded. All outputs are model estimates.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

**Boundary:** whole-block model estimate, not an AAC, a policy result, an
operability/tenure screen, an investment case, or an official OSeCAN change.
The full forest estate is used; no operability, tenure, road, slope, or mill
capacity screen is applied yet.

## 1. Inputs

| Input | Value |
|---|---|
| Dataset | `data/derived/ws4/scanfi-national-990m/dataset` |
| Tracks | 2,799 |
| Fragments | 6,807,192 |
| Blocks | 1,984,017 |
| Forest area | 411,250,630.32 ha |
| Curves | 719 (RSYC group curves) |
| WS4 CLI | `.venv-ws4/bin/ws4` |
| 100 Mm³ control | `data/derived/national/harvest_basis/provincial_allocation/100m.csv` |
| 150 Mm³ control | `data/derived/national/harvest_basis/provincial_allocation/150m.csv` |
| Chain reference | `data/derived/national/facilities/national_facility_chain_scenarios_v9/scenario_summary.csv` |
| Frozen coefficients | `data/derived/national/facilities/national_product_chain_v5/product_chain_by_province.csv` |
| Horizon | 100 years, ten 10-year periods |

The controls are the frozen `allocated_share` values. Their control-total
harvests are 100,000,000.1 m³/yr and 150,000,000.2 m³/yr; the shares are
normalized only to remove published rounding so the provincial period targets
sum exactly to the control total.

## 2. Method

The scheduler and block model are identical to the sustainable 119 Mm³ case:

- block volume is the exact fragment sum
  `Σ area_ha × natural_yield_curve(age)`; block age is fragment-area weighted;
- rotation is the integer age of maximum MAI on the natural RSYC curve, capped
  to 40–200 years and flagged when the raw peak is outside that band;
- a natural block is eligible at age ≥ rotation; a managed block is eligible
  at years-since-harvest ≥ rotation; blocks below rotation are never selected;
- within each province, eligible blocks are sorted oldest-first, then by exact
  period-start volume descending, then by block id; whole blocks are taken
  until the provincial period target is met;
- no block is split, scaled, or harvested below rotation; a final block may
  overshoot and the overshoot is recorded;
- selected blocks are assigned to the ten annual offsets of the period by
  longest-processing-time balancing of exact annual volumes;
- if eligible volume is below a provincial target, the shortfall is recorded;
  rotation is never relaxed to meet a target.

The WS4 run is the authority for harvested volume and treated area. It is
reconciled with the schedule per province and per offset; a mismatch above
1e-6 m³/ha aborts the build.

### 2.1 Chain bases

Frozen chain v5.1 per-province product/residue coefficients are applied to two
harvest bases:

- **primary** (`sustainable_100m` / `sustainable_150m`): the first-period
  annual average, offsets 0–9. This is the like-for-like comparison with the
  frozen first-period case totals.
- **achieved** (`sustainable_achieved`): the 100-year achieved annual average.

Both bases are unscaled; no factor is applied to force a match to the control
total. The 100/150 reference values are the frozen targets in the all-case
scenario summary, independently guarded against the values published in the
national facility-chain model card.

## 3. Schedule results

| Case | Target Mm³/yr | Period target Mm³ | Annual min Mm³/yr | Annual max Mm³/yr | Annual mean Mm³/yr | Annual CV | Period min Mm³ | Period max Mm³ | Shortfall Mm³ | Sustained |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 100 | 100.000000 | 1,000.000001 | 100.096807 | 102.955282 | 101.104998 | 0.007418 | 1,001.491188 | 1,026.565550 | 0 | yes |
| 150 | 150.000000 | 1,500.000002 | 149.997027 | 153.495684 | 151.247672 | 0.008033 | 1,501.156257 | 1,533.710762 | 0 | yes |

The control unit is the 10-year period. Both cases meet every period target
with zero period shortfall. The 100 Mm³ case also keeps every annual offset at
or above the nominal annual rate. The 150 Mm³ case has one annual offset
149,997,027.4 m³, 2,972.8 m³ (0.0020%) below the nominal annual rate, while its
minimum period is 1,501,156,257.2 m³, 1,156,255.2 m³ above the period target.
That is a longest-processing-time spread residual and is reported, not forced.

| Case | Selected blocks | Selected area Mha | Reharvested within 100 years | Blocks harvested |
|---|---:|---:|---:|---:|
| 100 | 267,389 | 71.659 | 0 | 267,389 |
| 150 | 400,049 | 106.577 | 586 | 400,635 |

### 3.1 Rotation

| Case | Raw MAI peak (blocks) | Used rotation (blocks) | Out-of-range flagged |
|---|---|---|---:|
| 100 | 2 yr: 251,272; 13 yr: 19; 24 yr: 16,098 | 40 yr: 267,389 | 267,389 |
| 150 | 2 yr: 364,759; 13 yr: 31; 24 yr: 35,259 | 40 yr: 400,049 | 400,049 |

Every selected block has a raw MAI peak below 40 years, so the 40-year
operability floor is used and flagged for 100% of selected blocks. Rotation is
never reduced to meet a target.

### 3.2 Harvest by province

Annual averages, Mm³/yr. `Target` is the frozen control share × the case
control total.

| Province | 100 target | 100 achieved (100 yr) | 150 target | 150 achieved (100 yr) |
|---|---:|---:|---:|---:|
| BC | 33.248823 | 33.254767 | 45.223878 | 45.261657 |
| AB | 19.038002 | 19.276570 | 30.100000 | 30.198334 |
| SK | 3.613337 | 3.644138 | 6.460155 | 6.473863 |
| MB | 0.944137 | 1.062180 | 1.687988 | 1.784902 |
| ON | 11.222165 | 11.410957 | 20.063703 | 20.328844 |
| QC | 22.184137 | 22.336965 | 34.115200 | 34.289241 |
| NB | 6.234700 | 6.251985 | 6.234700 | 6.251245 |
| NS | 1.986222 | 2.328170 | 3.551095 | 4.043474 |
| PE | 0.251338 | 0.258346 | 0.449358 | 0.459572 |
| NL | 1.209274 | 1.211042 | 2.012233 | 2.050867 |
| YT | 0.036532 | 0.037550 | 0.065314 | 0.066533 |
| NT | 0.031334 | 0.032327 | 0.036376 | 0.039149 |
| NU | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

Whole-block selection explains the departures from the exact provincial
targets: MB, NS, and NT overshoot most in the 150 Mm³ case because their
targets are small relative to their block sizes.

## 4. Chain comparison against frozen v5.1

The seven headline metrics. The full 22-metric comparison is in
`comparison_vs_v51.csv` for each case and in the combined
`data/derived/national/facilities/ws4_sustainable_cases/comparison_vs_v51.csv`.

### 4.1 100 Mm³

| Metric | Unit | Frozen v5.1 | Primary | Delta % | Achieved | Delta % |
|---|---|---:|---:|---:|---:|---:|
| Harvest | m³ | 100,000,000.100 | 100,176,736.536 | +0.1767 | 101,104,998.307 | +1.1050 |
| Solid product | m³ | 31,692,807.151 | 31,734,986.725 | +0.1331 | 31,936,905.275 | +0.7702 |
| Pulp product | odt | 10,136,702.864 | 10,157,930.479 | +0.2094 | 10,281,853.784 | +1.4319 |
| Process residue | PJ | 419.238018 | 419.956046 | +0.1713 | 423.797409 | +1.0875 |
| Harvest residue gross | PJ | 373.243923 | 374.055208 | +0.2174 | 378.082981 | +1.2965 |
| Harvest residue 60% | PJ | 223.946354 | 224.433125 | +0.2174 | 226.849788 | +1.2965 |
| Total residue gross | PJ | 792.481942 | 794.011254 | +0.1930 | 801.880390 | +1.1860 |

### 4.2 150 Mm³

| Metric | Unit | Frozen v5.1 | Primary | Delta % | Achieved | Delta % |
|---|---|---:|---:|---:|---:|---:|
| Harvest | m³ | 150,000,000.200 | 153,371,076.211 | +2.2474 | 151,247,672.317 | +0.8318 |
| Solid product | m³ | 46,136,665.718 | 46,741,639.058 | +1.3113 | 46,414,539.794 | +0.6023 |
| Pulp product | odt | 15,550,881.011 | 16,047,204.684 | +3.1916 | 15,715,281.869 | +1.0572 |
| Process residue | PJ | 626.865219 | 640.905817 | +2.2398 | 632.046174 | +0.8265 |
| Harvest residue gross | PJ | 573.125413 | 590.557241 | +3.0415 | 578.744364 | +0.9804 |
| Harvest residue 60% | PJ | 343.875248 | 354.334345 | +3.0415 | 347.246619 | +0.9804 |
| Total residue gross | PJ | 1,199.990632 | 1,231.463058 | +2.6227 | 1,210.790538 | +0.9000 |

The 150 Mm³ primary basis is 2.25% above the frozen harvest target because
the first period selects whole blocks and overshoots several small provincial
targets. The achieved basis, which averages over 100 years, is 0.83% above.
No value is scaled or forced.

## 5. OSeCAN handoff

The combined handoff applies the existing WS4 origin-province logic to both
case chains with the frozen handoff-v10 zone weights, the frozen v9 process
and harvest recovery definitions, and the official OSeCAN tier thresholds.

- Frozen v5.1 reference: **21/21** positions match handoff v10.
- 100 Mm³: **7/7** primary and **7/7** achieved tier positions match.
- 150 Mm³: **7/7** primary and **7/7** achieved tier positions match.
- Official OSeCAN tree: **146 files**, SHA-256 `80caa91c...`, **unchanged**;
  `official_osecan_changed=false`.

| Scenario | Stream | Case | Tier |
|---|---|---|---|
| 100 | process | 0% / 25% / 50% / 75% | T1 / T2 / T4 / above_T4 |
| 100 | harvest | 35% / 60% / 75% | T3 / above_T4 / above_T4 |
| 150 | process | 0% / 25% / 50% / 75% | T1 / T4 / above_T4 / above_T4 |
| 150 | harvest | 35% / 60% / 75% | T4 / above_T4 / above_T4 |

The 119 Mm³ rows in the combined 21-position comparison remain
`not_available`; the 119 case is already documented separately and is not
rewritten.

## 6. Reproduce

```bash
.venv/bin/python -m workflows.national.facilities.ws4_sustainable_cases
.venv/bin/python -m pytest tests/test_ws4_sustainable_cases.py
```

The build validates the WS4 dataset, reuses a matching 100-year run when the
schedule is unchanged, and refuses to overwrite a run whose schedule differs.
The official OSeCAN tree and frozen chain v5.1 are hashed before and after the
build; any change aborts.

## 7. Outputs

Data commit: `64edb5f38`.

| Output | Path |
|---|---|
| 100 schedule | `data/derived/ws4/sustainable-100m/` |
| 150 schedule | `data/derived/ws4/sustainable-150m/` |
| 100 chain | `data/derived/national/facilities/ws4_sustainable_cases/100m/` |
| 150 chain | `data/derived/national/facilities/ws4_sustainable_cases/150m/` |
| Combined summary/comparison | `data/derived/national/facilities/ws4_sustainable_cases/` |
| OSeCAN handoff | `data/derived/national/facilities/ws4_sustainable_cases/osecan/` |

## 8. Caveats

- The full forest estate is used; no operability, tenure, road, slope, or
  mill-capacity screen is applied.
- Whole blocks are selected; provincial and period overshoot is retained and
  reported. The 150 Mm³ first-period overshoot is 2.25% and is not forced down.
- The 150 Mm³ annual LPT spread leaves one annual offset 0.0020% below the
  nominal annual rate while the period is above target; the period is the
  control unit.
- Rotation is capped to the 40–200 year band and flagged; it is never relaxed
  to meet a target.
- Frozen chain v5.1, handoff v10, residue curve v9, official OSeCAN, the 119
  case artifacts, and the WS4 dataset are read-only.
- No UI surface is changed.
