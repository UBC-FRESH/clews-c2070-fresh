# WS4 sustainable 119 Mm³/yr schedule (national 990 m estate)

**Status:** pass. The 118,977,339.1 m³/yr target is sustained for 100 years on
the national 990 m WS4 estate with no period shortfall.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

**Boundary:** whole-block model estimate, not an AAC, a policy result, an
operability/tenure screen, an investment case, or an official OSeCAN change.

## 1. Inputs

| Input | Value |
|---|---|
| Dataset | `data/derived/ws4/scanfi-national-990m/dataset` |
| Tracks | 2,799 |
| Fragments | 6,807,192 |
| Blocks | 1,984,017 |
| Forest area | 411,250,630.32 ha |
| Curves | 718 (RSYC tile coniferous/broadleaf/generic) |
| WS4 CLI | `.venv-ws4/bin/ws4` (0.2.0) |
| Target | 118,977,339.1 m³/yr |
| Period target | 1,189,773,391.0 m³ per 10 years |
| Horizon | 100 years, offsets 0–99 |

The full forest estate is used. No operability, tenure, road, slope, or mill
capacity screen is applied yet; the schedule is a volume-arithmetic
sustainability test, not an implementable harvest plan.

## 2. Method

### 2.1 Block volume and age

Every block's volume is the exact fragment sum

```
volume_m3 = Σ_fragment area_ha × natural_yield_curve(age)
```

and its age is the fragment-area-weighted mean age. The natural yield curve is
the `feature.Yield.<province>.<state>.Total` curve of the block's track. The
managed and thinned curves are numerically identical to the natural curve in
this build, so a harvested block keeps the same yield curve after CC.

### 2.2 Rotation

For each natural curve, MAI = volume/age is evaluated at integer ages 1–600.
The rotation is the age of maximum MAI, capped to the 40–200 year band and
flagged when the raw peak is outside it. On this dataset the raw peaks are
2 years (383 curves), 13 years (92), 24 years (243), and 1 year (1); every
selected block's raw peak is below 40, so the used rotation is 40 years for
100% of selected blocks. The raw peak is recorded in
`rotation_distribution.csv` and in every selected-block row.

The cap is an operability floor, not a yield claim. The RSYC curves are
volume-over-age curves whose MAI peaks very early; harvesting at the raw peak
would not be a defensible rotation. The cap is applied uniformly and flagged,
and rotation is never relaxed to meet the volume target.

### 2.3 Eligibility and selection

At each period:

- a natural/unmanaged block is eligible when its area-weighted age is at least
  its rotation age;
- a managed block is eligible when years since its last harvest are at least
  its rotation age;
- blocks below rotation are never selected.

Within each province, eligible blocks are sorted oldest-first, then by exact
period-start volume (descending), then by block id. Whole blocks are taken
until the provincial period target is met. No block is split, scaled, or
fabricated. A final block may overshoot the target; the overshoot is recorded.

The national target is allocated to provinces with the frozen 119 Mm³
`allocated_share` values (`data/derived/national/harvest_basis/
provincial_allocation/119m.csv`). The sum of provincial targets is exactly the
national period target. The provincial allocation is used because the frozen
chain v5.1 coefficients and the OSeCAN handoff are per-province; a national
oldest-first free run would liquidate the oldest BC inventory first and change
the province-weighted residue mix (see §6). Inside each province the
selection is still oldest-first and volume-aware.

If a province's eligible volume in a period is below its target, all eligible
blocks are selected and the shortfall is recorded. No rotation is relaxed.

### 2.4 Annual assignment

Selected blocks are assigned to the ten annual offsets of their period by
longest-processing-time balancing: blocks are ordered by mean exact annual
volume and each is placed in the year with the lowest resulting load. The
exact volume at the assigned year is used. The period total and every annual
load are read back from the selection and reconciled against the WS4 run.

### 2.5 WS4 run

```
.venv-ws4/bin/ws4 validate --dataset data/derived/ws4/scanfi-national-990m/dataset
.venv-ws4/bin/ws4 simulate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset \
  --schedule data/derived/ws4/sustainable-119m/schedule.csv \
  --years 100 \
  --out data/derived/ws4/sustainable-119m/run_100y
```

The run is the authority for harvested volume and treated area. The
schedule and run are reconciled per province and per offset; a mismatch above
1e-6 m³/ha aborts the build.

## 3. Results

### 3.1 Period harvest

| Period | Offsets | Target Mm³ | Achieved Mm³ | Overshoot % | Area Mha | Blocks |
|---:|---|---:|---:|---:|---:|---:|
| 0 | 0–9 | 1,189.773 | 1,193.799 | 0.338 | 9.620 | 44,372 |
| 1 | 10–19 | 1,189.773 | 1,221.490 | 2.666 | 8.452 | 27,275 |
| 2 | 20–29 | 1,189.773 | 1,192.381 | 0.219 | 8.306 | 24,402 |
| 3 | 30–39 | 1,189.773 | 1,198.530 | 0.736 | 8.381 | 30,046 |
| 4 | 40–49 | 1,189.773 | 1,209.635 | 1.669 | 8.803 | 34,418 |
| 5 | 50–59 | 1,189.773 | 1,190.648 | 0.074 | 8.374 | 31,822 |
| 6 | 60–69 | 1,189.773 | 1,206.508 | 1.407 | 7.816 | 32,050 |
| 7 | 70–79 | 1,189.773 | 1,190.730 | 0.080 | 8.378 | 29,796 |
| 8 | 80–89 | 1,189.773 | 1,193.295 | 0.296 | 8.197 | 29,524 |
| 9 | 90–99 | 1,189.773 | 1,191.373 | 0.134 | 8.021 | 35,115 |

### 3.2 Even-flow metrics and rotation

| Metric | Value |
|---|---:|
| Annual minimum | 119,043,264.5 m³/yr |
| Annual maximum | 122,161,035.7 m³/yr |
| Annual mean | 119,883,890.8 m³/yr |
| Annual CV | 0.008461 |
| Period minimum | 1,190,648,029.4 m³ |
| Period maximum | 1,221,490,022.7 m³ |
| Period shortfall | 0 m³ |
| 119 Mm³/yr sustained 100 years | true |
| Selected blocks | 318,820 |
| Selected area | 84,348,104.4 ha |
| Blocks reharvested within 100 years | 0 |
| Used rotation | 40 years (100% of selected blocks) |
| Raw rotation below 40 | 318,820 blocks (flagged) |

The minimum annual harvest is above 118,977,339.1 m³/yr; the maximum is 2.7%
above it. The CV is below 1%. No block is harvested twice within the horizon,
so the rotation constraint is satisfied by construction and by the independent
selected-block audit.

### 3.3 Harvest by province

| Province | Control share | Target Mm³/yr | Achieved Mm³/yr | 119 Mm³ basis Mm³/yr | Area Mha | Blocks |
|---|---:|---:|---:|---:|---:|---:|
| BC | 33.602% | 39.979 | 39.984 | 39.682 | 16.424 | 114,163 |
| AB | 19.240% | 22.891 | 22.962 | 22.788 | 12.196 | 31,963 |
| SK | 3.652% | 4.345 | 4.522 | 4.488 | 4.228 | 14,500 |
| MB | 0.954% | 1.135 | 1.221 | 1.211 | 1.343 | 2,849 |
| ON | 11.341% | 13.494 | 13.557 | 13.454 | 13.301 | 26,015 |
| QC | 22.420% | 26.674 | 26.803 | 26.600 | 28.981 | 96,944 |
| NB | 5.240% | 6.235 | 6.251 | 6.204 | 3.498 | 15,941 |
| NS | 2.007% | 2.388 | 2.730 | 2.709 | 1.861 | 5,502 |
| PE | 0.254% | 0.302 | 0.305 | 0.303 | 0.227 | 1,673 |
| NL | 1.222% | 1.454 | 1.465 | 1.454 | 2.189 | 8,368 |
| YT | 0.037% | 0.044 | 0.045 | 0.044 | 0.047 | 377 |
| NT | 0.031% | 0.036 | 0.039 | 0.039 | 0.052 | 525 |
| NU | 0.000% | 0.000 | 0.000 | 0.000 | 0.000 | 0 |
| CAN | 100.000% | 118.977 | 119.884 | 118.977 | 84.348 | 318,820 |

Provincial totals can overshoot by the final whole block; the achieved
100-year annual mean is 119,883,890.8 m³/yr, 0.762% above the target. The
`sustainable_119m` chain basis applies one national factor
(0.992391399481) to bring the total back to exactly 118,977,339.1 m³/yr.

### 3.4 Age class at harvest

The selected blocks are old by design (oldest-first). The mean block age at
harvest is 204.1 years (area-weighted 182.0 years); the annual mean ranges
from 170.3 to 228.4 years across the 100 annual offsets. The full 10-year
age-class table is in `data/derived/ws4/sustainable-119m/age_class_harvest.csv`;
period/province tables are in `province_period_harvest.csv`.

## 4. Chain versus frozen v5.1

Two accounting bases are written:

- `sustainable_119m`: 100-year annual average by province scaled by one
  national factor to exactly 118,977,339.1 m³/yr; provincial shares are the
  sustainable shares.
- `sustainable_achieved`: the unscaled 100-year annual average
  (119,883,890.8 m³/yr).

| Metric | Unit | Frozen v5.1 | 119 Mm³ basis | Delta % | Achieved basis | Delta % |
|---|---|---:|---:|---:|---:|---:|
| Harvest | m³ | 118,977,339.1 | 118,977,339.1 | 0.0000 | 119,883,890.8 | +0.7620 |
| Solid product | m³ | 37,673,626.8 | 37,538,259.6 | −0.3593 | 37,824,282.7 | +0.3999 |
| Pulp product | odt | 12,066,311.3 | 12,098,450.1 | +0.2663 | 12,190,626.8 | +1.0303 |
| Process residue | PJ | 498.7509 | 498.5766 | −0.0349 | 502.3756 | +0.7267 |
| Harvest residue gross | PJ | 444.4108 | 446.3916 | +0.4457 | 449.7929 | +1.2111 |
| Harvest residue 60% | PJ | 266.6465 | 267.8350 | +0.4457 | 269.8757 | +1.2111 |
| Total residue gross | PJ | 943.1617 | 944.9682 | +0.1915 | 952.1685 | +0.9550 |

The differences are the result of applying frozen per-province coefficients
to the sustainable provincial harvest mix, not a coefficient change. Frozen
chain v5.1 is unchanged; the full 22-metric comparison is in
`comparison_vs_v51.csv`.

## 5. OSeCAN handoff

The existing WS4 origin-province handoff logic is applied to the sustainable
chain under
`data/derived/national/facilities/ws4_sustainable_119m/osecan/`.

| Check | Result |
|---|---|
| Frozen v5.1 reference positions | 21 / 21 |
| 119 Mm³ tier positions, `sustainable_119m` | 7 / 7 |
| 119 Mm³ tier positions, `sustainable_achieved` | 7 / 7 |
| Official OSeCAN tree | 146 files, `80caa91c3e91c890...`, 0 changed |

The official OSeCAN tree is hashed before and after; handoff v10 and residue
curve v9 are read-only. No official OSeCAN file is written.

## 6. National oldest-first free-run sensitivity

The same scheduler without provincial allocation selects 363,447 blocks /
66.710 Mha and sustains the period target, with annual min 118,822,074.8 m³,
annual max 119,670,982.7 m³, and CV 0.001074. However, its 100-year harvest is
78.60% BC, 8.93% QC, 4.84% AB, 3.11% ON, and the rest small, because BC holds
the oldest inventory. Applying the frozen coefficients to that mix gives a
harvest 35% residue of 132.55 PJ (T3), not 155.54 PJ (T4), so that free run
would be 6/7 on the 119 Mm³ tier check. It is therefore reported only as a
sensitivity (`free_run_sensitivity.json`) and is not used for the chain or
OSeCAN basis. No volume is fabricated and no rotation is relaxed in either
run.

## 7. Boundary and caveats

- This is a whole-block volume-arithmetic schedule on the full forest estate.
  No operability, tenure, road, slope, mill capacity, or economic screen is
  applied.
- It is not an AAC, a legal harvest level, a policy result, or a national
  supply claim.
- The 40-year rotation is a cap on the raw MAI peak, applied to every block
  and flagged. It is not a silvicultural prescription.
- The provincial allocation uses the frozen 119 Mm³ control shares. It is a
  scheduling device to keep the chain/OSeCAN comparison meaningful, not a
  provincial policy target.
- Managed and natural curves are identical in this build; planting response,
  thinning response, fertilization, and genetic gain are not modelled.
- Product and residue values apply frozen chain v5.1 per-province
  coefficients. Facility assignment, mill capacity, transport, and
  interprovincial trade are not re-run.
- No official OSeCAN file, frozen chain v5.1 artifact, or UI surface is
  changed.
- 100 Mm³ and 150 Mm³ scenarios are follow-up work, not part of this artifact.

## 8. Reproduce

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_sustainable_119m
.venv-ws4/bin/python -m workflows.national.facilities.ws4_sustainable_osecan_handoff
```

## 9. Artifacts

Data commit: `ce1670c62`.

| Path | Contents |
|---|---|
| `data/derived/ws4/sustainable-119m/schedule.csv` | 318,820 whole-block CC rows |
| `data/derived/ws4/sustainable-119m/selected_blocks.csv` | selected block, period, offset, age, volume, area, rotation |
| `data/derived/ws4/sustainable-119m/annual_harvest.csv` | 100 annual harvest/area rows |
| `data/derived/ws4/sustainable-119m/period_harvest.csv` | 10 period targets and achievements |
| `data/derived/ws4/sustainable-119m/province_period_harvest.csv` | province × period harvest |
| `data/derived/ws4/sustainable-119m/age_class_harvest.csv` | period × province × age-class harvest |
| `data/derived/ws4/sustainable-119m/rotation_distribution.csv` | raw/capped rotation distribution |
| `data/derived/ws4/sustainable-119m/shortfalls.csv` | empty: no period shortfall |
| `data/derived/ws4/sustainable-119m/free_run_sensitivity.json` | national oldest-first diagnostic |
| `data/derived/ws4/sustainable-119m/run_100y/` | WS4 validate/simulate output |
| `data/derived/national/facilities/ws4_sustainable_119m/sustainable_harvest_by_province.csv` | sustainable harvest by province |
| `data/derived/national/facilities/ws4_sustainable_119m/sustainable_product_chain.csv` | frozen v5.1 coefficients by province and basis |
| `data/derived/national/facilities/ws4_sustainable_119m/comparison_vs_v51.csv` | 22-metric comparison |
| `data/derived/national/facilities/ws4_sustainable_119m/summary.json` | full summary |
| `data/derived/national/facilities/ws4_sustainable_119m/manifest.json` | hashes and labels |
| `data/derived/national/facilities/ws4_sustainable_119m/WS4_SUSTAINABLE_119M.md` | generated chain report |
| `data/derived/national/facilities/ws4_sustainable_119m/osecan/` | 7/7 + 21/21 OSeCAN handoff |
