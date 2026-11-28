# Transformed RSYC Curves in the National 990 m WS4 119 Mm3 Case

**Artifact:** `ws4_transformed_119m` **Version:** 1.0.0

**Status:** pass. The 118,977,339.1 m3/yr (119 Mm3/yr) sustainable target is
met for 100 years with zero period shortfall on the transformed national RSYC
curve library.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`transformed_curves=true`, `official_osecan_changed=false`,
`chain_v51_unchanged=true`.

This is a model estimate, not an AAC, policy result, tenure plan, or investment
case. It does not replace official OSeCAN curves.

## 1. Purpose

The national RSYC transform v1 library replaces the original RSYC tile group
curve shapes with donor-fitted 5-parameter curves while preserving volume at
age 100 (V100). This case feeds that library into the national 990 m WS4
estate and reruns the existing sustainable 119 Mm3 scheduler, chain, and
origin-province OSeCAN handoff. The original national 990 m dataset, frozen
chain v5.1, and official OSeCAN tree are read-only.

## 2. Transformed dataset

- Source dataset:
  `data/derived/ws4/scanfi-national-990m/dataset` (12 tables, 6,807,192
  fragments, 1,984,017 blocks, 2,799 tracks).
- Transformed dataset:
  `data/derived/ws4/scanfi-national-990m-transformed/dataset`.
- Patch manifest:
  `data/derived/ws4/scanfi-national-990m-transformed/patch_manifest.json`.
- Curve mapping:
  `data/derived/ws4/scanfi-national-990m-transformed/curve_mapping.csv`.
- Build summary:
  `data/derived/ws4/scanfi-national-990m-transformed/build_summary.json`.

Method:

1. Parse `CURVE(<model_id>)` from `tracks.name`; use
   `feature.Yield.<province>.<state>.Total` to map each track to its WS4
   `curve_id`.
2. Fail if one `curve_id` maps to multiple model IDs or a model ID is missing
   from the transformed library.
3. A WS4 model can be represented by several transformed cell curves because
   the donor class depends on province and species. The model-level curve is
   the forest-area-weighted mean of the transformed cell curves. This is a
   national group curve: every cell under a model shares one original RSYC
   coefficient set, and the weighted mean preserves the group V100.
4. Replace `curves.parquet` values only: age 0 = 0; ages 1-200 = transformed
   volume; ages 201-600 = the age-200 value held flat. Ages 201-600 are outside
   the transformed fit and are documented as a hold, not a forecast.
5. Leave curve 1 unchanged because no `Yield.Total` feature references it.

Checks:

| Check | Result |
|---|---|
| WS4 curves mapped | 718 of 718 referenced (curve 1 unreferenced) |
| RSYC models mapped | 718 |
| Transformed cell curves aggregated | 3,948 |
| Tracks mapped | 2,799 |
| `curve_id` to multiple model IDs | 0 |
| Missing model IDs | 0 |
| V100 maximum absolute error | 0.000500 m3/ha (tolerance 0.01) |
| Negative or NaN curve values | 0 |
| `ws4 validate` | `Dataset is valid` |

The original dataset hashes are recorded before the copy and are unchanged.

## 3. Sustainable 119 Mm3 schedule

The scheduler is unchanged from `ws4_sustainable_119m.py`: 100 years, ten
10-year periods, whole eligible blocks selected oldest-first and
volume-aware, provincial 119 Mm3 control shares, annual longest-processing-time
spread, rotation from maximum MAI capped to 40-200 years, and no
under-rotation harvest. Shortfalls are reported, not forced.

| Metric | Value |
|---|---:|
| Annual minimum | 119,254,007.7 m3/yr |
| Annual maximum | 121,777,630.2 m3/yr |
| Annual mean | 120,056,052.5 m3/yr |
| Annual CV | 0.007310 |
| Period shortfall | 0.0 m3 |
| Sustained for 100 years | yes |
| Selected blocks | 253,476 |
| Selected area | 72,388,258.65 ha |
| Blocks reharvested within 100 years | 0 |

Rotation: 176,177 selected blocks use the 40-year cap (raw MAI peaks of 13,
17, or 18 years); the remaining blocks use natural-curve rotations of 76-174
years. The area-weighted mean rotation is 53.97 years. All harvest ages are at
or above the selected rotation.

### Comparison with the original RSYC 119 Mm3 case

| Metric | Original RSYC 119m | Transformed 119m |
|---|---:|---:|
| Annual minimum (m3/yr) | 119,043,264.5 | 119,254,007.7 |
| Annual maximum (m3/yr) | 122,161,035.7 | 121,777,630.2 |
| Annual mean (m3/yr) | 119,883,890.8 | 120,056,052.5 |
| Annual CV | 0.008461 | 0.007310 |
| Selected blocks | 318,820 | 253,476 |
| Selected area (ha) | 84,348,104.4 | 72,388,258.65 |
| Area-weighted mean rotation (years) | 40.00 | 53.97 |
| Median maximum MAI (m3/ha/yr) | 2.609 | 3.255 |
| Median raw MAI-peak age (years) | 2 | 18 |
| Curves outside the 40-200 cap | 719 | 391 |

The transformed curves carry more volume per hectare at the same rotation cap,
so fewer blocks and hectares are needed to meet the fixed 119 Mm3 target. Both
cases sustain the target with zero period shortfall.

## 4. Chain versus frozen v5.1

Frozen v5.1 per-province product and residue coefficients are applied to the
100-year annual average (`sustainable_119m` basis, scaled to exactly
118,977,339.1 m3/yr) and to the unscaled average (`sustainable_achieved`
basis). The first-period annual average is also reported on the same frozen
coefficients.

| Metric | Frozen v5.1 | 100-year 119m basis | Delta % | First-period 119m basis | Delta % |
|---|---:|---:|---:|---:|---:|
| Solid product (m3) | 37,673,626.8 | 37,547,496.5 | -0.335 | 37,538,173.8 | -0.360 |
| Pulp product (odt) | 12,066,311.3 | 12,099,425.9 | +0.274 | 12,088,679.9 | +0.185 |
| Process residue (PJ) | 498.750917 | 498.601926 | -0.030 | 498.451203 | -0.060 |
| Harvest residue gross (PJ) | 444.410766 | 445.837090 | +0.321 | 446.765843 | +0.530 |
| Harvest residue 60% (PJ) | 266.646460 | 267.502254 | +0.321 | 268.059506 | +0.530 |
| Total residue gross (PJ) | 943.161683 | 944.439016 | +0.135 | 945.217046 | +0.218 |

The unscaled 100-year achieved basis is 120,056,052.5 m3/yr: solid
37,887,922.6 m3, pulp 12,209,125.8 odt, process 503.122523 PJ, harvest gross
449.879292 PJ, harvest 60% 269.927575 PJ, total gross 953.001815 PJ.

## 5. OSeCAN handoff

The handoff is rebuilt from the transformed sustainable chain using the frozen
handoff-v10 province-to-zone weights, frozen v9 process availability and
harvest recovery, and the frozen v5.1 tier thresholds. It uses the
origin-province basis, not a facility-province reallocation.

| Check | Result |
|---|---|
| Frozen v5.1 reference positions | 21/21 |
| 119 Mm3 primary-basis tiers | 7/7 |
| 119 Mm3 achieved-basis tiers | 7/7 |
| Official OSeCAN file count | 146 |
| Official OSeCAN tree hash | `80caa91c3e91c890...` unchanged |

No official OSeCAN file, frozen chain v5.1 artifact, or UI surface is changed.

## 6. Artifacts and reproduction

Build the transformed dataset:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_transformed_dataset
```

Run the transformed 119 Mm3 case, chain, and OSeCAN handoff:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_transformed_119m
```

Test:

```bash
.venv-ws4/bin/python -m pytest tests/test_ws4_transformed_119m.py -q
```

Outputs:

- `data/derived/ws4/scanfi-national-990m-transformed/` — transformed dataset,
  patch manifest, curve mapping, build summary.
- `data/derived/ws4/scanfi-national-990m-transformed/sustainable-119m/` —
  schedule, selected blocks, annual/period/province harvest, rotation,
  shortfalls, WS4 run, and combined case summary.
- `data/derived/national/facilities/ws4_transformed_119m/` — chain tables,
  first-period comparison, original-case comparison, report, manifest, and
  `osecan/` handoff.

## 7. Caveats

- The transformed curves are model estimates and are not official OSeCAN
  curves.
- Ages 201-600 are held at the age-200 transformed value; they are not a
  fitted forecast.
- The model-level curve is an area-weighted mean of cell-level transforms, not
  a single fitted donor curve.
- The schedule is a national even-flow arithmetic model with no operability,
  tenure, road, slope, mill-capacity, or interprovincial-trade screen.
- Provincial achieved volume can overshoot the period target by the final
  whole block.
- Frozen chain v5.1 coefficients are applied by origin province; facility
  assignment, capacity, transport, and trade are not re-run.
