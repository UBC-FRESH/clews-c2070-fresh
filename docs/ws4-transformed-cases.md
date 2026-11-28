# WS4 sustainable and operable cases on transformed RSYC curves

**Artifact:** `ws4_transformed_cases` **Version:** 1.0.0

**Status:** pass. The 100 and 150 Mm3/yr sustainable cases and the 100, 119, and
150 Mm3/yr operable cases were rerun on the transformed national RSYC curves.
The two full-forest cases are sustained with zero period shortfall. The three
operable cases keep their provincial shortfalls visible and report the
schedule-only maximum sustainable levels. This is a model estimate, not an
AAC, policy result, tenure plan, legal THLB, official OSeCAN change, or
national supply claim.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`transformed_curves=true`, `official_osecan_changed=false`,
`chain_v51_unchanged=true`.

## 1. Purpose and inputs

The national RSYC transform v1 library replaces original RSYC tile group curve
shapes with donor-fitted 5-parameter curves while preserving volume at age 100
(V100). The existing WS4 workflows are unchanged except for the dataset path:

| Input | Value |
|---|---|
| Dataset | `data/derived/ws4/scanfi-national-990m-transformed/dataset` |
| Build summary | `data/derived/ws4/scanfi-national-990m-transformed/build_summary.json` |
| WS4 curves mapped | 718 of 718 referenced (curve 1 unreferenced) |
| RSYC models mapped | 718 |
| Transformed cell curves aggregated | 3,948 |
| V100 maximum absolute error | 0.000500 m3/ha (tolerance 0.01) |
| `ws4 validate` | `Dataset is valid` |
| Chain reference | frozen v5.1 `national_facility_chain_scenarios_v9/scenario_summary.csv` |
| Frozen coefficients | frozen v5.1 `product_chain_by_province.csv` |
| Horizon | 100 years, ten 10-year periods |

The original national 990 m dataset, original-curve case artifacts, frozen
chain v5.1, and the official OSeCAN tree are read-only.

## 2. Sustainable 100 and 150 Mm3/yr

The scheduler is unchanged: whole eligible blocks selected oldest-first and
volume-aware inside each province, provincial period targets from the frozen
`allocated_share` controls, annual longest-processing-time spread, rotation from
maximum MAI capped to 40-200 years, no under-rotation harvest, no block split or
scaling. The WS4 run is the authority for harvested volume and treated area.

| Case | Target Mm3/yr | Annual min | Annual max | Annual mean | Annual CV | Period shortfall Mm3 | Sustained | Blocks | Area Mha | Reharvested | Mean rotation yr |
|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| 100m | 100.000000 | 100.2499 | 103.1923 | 101.1221 | 0.008491 | 0.000000 | True | 212,570 | 61.733 | 0 | 53.45 |
| 150m | 150.000000 | 150.5393 | 153.8005 | 151.6402 | 0.007858 | 0.000000 | True | 313,862 | 93.151 | 541 | 53.75 |

Both cases meet every 10-year period target with zero period shortfall. The
transformed 150 Mm3 case keeps every annual offset above the nominal annual
rate; the original-curve 150 Mm3 case had one offset 0.0020% below it.

### 2.1 Rotation use

| Case | Blocks at 40 yr | Main rotations above 40 yr | Out-of-range raw peaks |
|---|---:|---|---:|
| 100m | 150,057 | 96: 29,486, 121: 6,980, 120: 6,690, 111: 6,662, 119: 5,734 | 150,057 |
| 150m | 221,320 | 96: 38,426, 111: 14,874, 119: 10,542, 121: 8,852, 120: 7,912 | 221,320 |

The raw MAI peak is below 40 years for 176,177 of the 1,984,017 blocks, so the
40-year operability floor is used and flagged. Curves with raw peaks above 40
years keep their own rotation up to the 200-year cap. Rotation is never reduced
to meet a target.

### 2.2 Harvest by province

Annual averages, Mm3/yr. Target is the frozen case share times the control
total.

| Province | 100 target | 100 achieved (100 yr) | 150 target | 150 achieved (100 yr) |
|---|---:|---:|---:|---:|
| BC | 33.248823 | 33.254672 | 45.223878 | 45.250712 |
| QC | 22.184137 | 22.394661 | 34.115200 | 34.207248 |
| AB | 19.038002 | 19.290487 | 30.100000 | 30.644746 |
| ON | 11.222165 | 11.357771 | 20.063703 | 20.214750 |
| NB | 6.234700 | 6.245190 | 6.234700 | 6.245190 |
| SK | 3.613337 | 3.730773 | 6.460155 | 6.554472 |
| NS | 1.986222 | 2.323636 | 3.551095 | 4.036523 |
| NL | 1.209274 | 1.219165 | 2.012233 | 2.046103 |
| MB | 0.944137 | 0.975458 | 1.687988 | 1.877947 |
| PE | 0.251338 | 0.258435 | 0.449358 | 0.452701 |
| YT | 0.036532 | 0.038061 | 0.065314 | 0.067274 |
| NT | 0.031333 | 0.033746 | 0.036376 | 0.042538 |
| NU | 0.000000 | 0.000000 | 0.000000 | 0.000000 |

Whole-block selection explains the provincial departures from the exact
targets; MB, NS, and NT overshoot most in relative terms because their targets
are small relative to their block sizes.

## 3. Chain versus frozen v5.1

Frozen v5.1 per-province coefficients are applied to two unscaled bases:
the first-period annual average (offsets 0-9) and the 100-year achieved annual
average. Reference values are the frozen v5.1 100/150 Mm3 targets. The full
22-metric table is in `comparison_sustainable_vs_v51.csv`.

| Case | Metric | Unit | v5.1 | First period | Delta % | Achieved | Delta % |
|---|---|---|---:|---:|---:|---:|---:|
| 100m | harvest_m3 | m3 | 100,000,000.100000 | 100,710,612.145284 | +0.7106 | 101,122,055.001091 | +1.1221 |
| 100m | solid_product_m3 | m3 | 31,692,807.151334 | 31,855,830.870359 | +0.5144 | 31,915,916.256395 | +0.7040 |
| 100m | pulp_product_odt | odt | 10,136,702.863917 | 10,220,847.937901 | +0.8301 | 10,286,376.089621 | +1.4765 |
| 100m | process_residue_pj | PJ | 419.238018 | 422.093335 | +0.6811 | 423.797376 | +1.0875 |
| 100m | harvest_residue_gross_pj | PJ | 373.243923 | 376.674317 | +0.9191 | 378.847821 | +1.5014 |
| 100m | harvest_residue_recoverable_r060_pj | PJ | 223.946354 | 226.004590 | +0.9191 | 227.308693 | +1.5014 |
| 100m | total_residue_gross_pj | PJ | 792.481942 | 798.767652 | +0.7932 | 802.645197 | +1.2825 |
| 150m | harvest_m3 | m3 | 150,000,000.200000 | 153,800,473.489748 | +2.5336 | 151,640,202.795479 | +1.0935 |
| 150m | solid_product_m3 | m3 | 46,136,665.718213 | 46,740,796.879038 | +1.3094 | 46,496,333.428212 | +0.7796 |
| 150m | pulp_product_odt | odt | 15,550,881.011045 | 16,125,857.615050 | +3.6974 | 15,764,755.001192 | +1.3753 |
| 150m | process_residue_pj | PJ | 626.865219 | 642.494523 | +2.4932 | 633.598663 | +1.0741 |
| 150m | harvest_residue_gross_pj | PJ | 573.125413 | 593.300131 | +3.5201 | 580.444232 | +1.2770 |
| 150m | harvest_residue_recoverable_r060_pj | PJ | 343.875248 | 355.980079 | +3.5201 | 348.266539 | +1.2770 |
| 150m | total_residue_gross_pj | PJ | 1,199.990632 | 1,235.794654 | +2.9837 | 1,214.042894 | +1.1710 |

The transformed 150 Mm3 first-period basis overshoots the control because the
whole-block first period selects somewhat more volume than the 150 Mm3 annual
target; the achieved basis is closer, at
+1.0935%.

## 4. Sustainable OSeCAN handoff

- Sustainable 100/150: frozen v5.1 21/21; 100m 7/7 primary, 7/7 achieved; 150m 7/7 primary, 7/7 achieved; official tree unchanged `True`.

The handoff uses the frozen handoff-v10 province-to-zone weights, the frozen
v9 process/harvest definitions, and the official v5.1 tier thresholds. Tier
positions are compared with handoff v10; a changed position would be a finding,
not an official OSeCAN change.

## 5. Operable 100, 119, and 150 Mm3/yr

The operable landbase is the existing `screen_core30` province-share mask:
370,752 blocks /
75.374 Mha
(18.3280%
of WS4 forest area). The transformed mask is block-for-block identical to the
original-curve mask because only yield curves changed. It remains an area-share
proxy, not a legal THLB, slope screen, or spatial road overlay.

| Case | Target Mm3/yr | Annual min | Annual max | Annual mean | Period shortfall Mm3 | Province shortfall Mm3 | Sustained | Covered sustained | Blocks | Area Mha | Max nominal Mm3/yr | Max operable Mm3/yr |
|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|
| 100m | 100.000000 | 98.2434 | 102.8632 | 100.5917 | 34.6651 | 67.2620 | False | False | 251,320 | 58.370 | 76.0498 | 77.1195 |
| 119m | 118.977339 | 102.6644 | 122.8093 | 116.0305 | 377.7192 | 457.3300 | False | False | 285,948 | 66.370 | 76.2780 | 77.0942 |
| 150m | 150.000000 | 104.4241 | 150.9391 | 133.6562 | 1,645.1650 | 1,761.3301 | False | False | 330,938 | 76.337 | 68.4448 | 70.3013 |

All three strict all-province cases are not sustained. Provincial shortfalls
are retained and the cross-province fallback is disabled:

| Case | Province shortfall (Mm3, 100-yr total) | Shortfall periods |
|---|---|---|
| 100m | PE 25.13, NL 23.94, SK 16.93, NB 1.26 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| 119m | QC 287.32, SK 89.36, NL 45.83, PE 30.22, NS 4.32, NB 0.28 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |
| 150m | QC 1,022.35, SK 294.99, AB 178.28, NS 118.04, NL 102.36, PE 44.94, NB 0.38 | 0, 1, 2, 3, 4, 5, 6, 7, 8, 9 |

The maximum sustainable level is a schedule-only diagnostic with the same
provincial shares and mask; it is not a WS4-simulated or policy-tested level.
It tests adequacy on the eleven provinces with operable coverage (NU and PE
have no 2016 tenure coverage and no operable blocks). Under transformed curves
the 100 Mm3 maximum nominal level rises from
68.4570
to 76.0498
Mm3/yr versus the original curves.

## 6. Full-forest versus operable

| Case | Full-forest mean Mm3/yr | Full-forest area Mha | Full-forest blocks | Operable mean Mm3/yr | Operable area Mha | Operable blocks |
|---|---:|---:|---:|---:|---:|---:|
| 100m | 101.1221 | 61.733 | 212,570 | 100.5917 | 58.370 | 251,320 |
| 119m | 120.0561 | 72.388 | 253,476 | 116.0305 | 66.370 | 285,948 |
| 150m | 151.6402 | 93.151 | 313,862 | 133.6562 | 76.337 | 330,938 |

The full-forest references are the transformed sustainable cases (100/150) and
the transformed 119 Mm3 case. The operable mean is the 100-year WS4 achieved
average on the mask.

## 7. Operable chain versus frozen v5.1

The full-forest v5.1 targets are shown for reference; operable deltas include
the effect of the landbase screen and the transformed curves.

| Case | Metric | Unit | v5.1 | First period | Delta % | Achieved | Delta % |
|---|---|---|---:|---:|---:|---:|---:|
| 100m | harvest_m3 | m3 | 100,000,000.100000 | 102,336,789.973684 | +2.3368 | 100,591,714.079084 | +0.5917 |
| 100m | solid_product_m3 | m3 | 31,692,807.151334 | 32,246,568.280373 | +1.7473 | 31,915,227.415936 | +0.7018 |
| 100m | pulp_product_odt | odt | 10,136,702.863917 | 10,437,511.951396 | +2.9675 | 10,195,064.815553 | +0.5757 |
| 100m | process_residue_pj | PJ | 419.238018 | 428.887354 | +2.3016 | 421.820452 | +0.6160 |
| 100m | harvest_residue_gross_pj | PJ | 373.243923 | 382.256317 | +2.4146 | 373.915104 | +0.1798 |
| 100m | harvest_residue_recoverable_r060_pj | PJ | 223.946354 | 229.353790 | +2.4146 | 224.349063 | +0.1798 |
| 100m | total_residue_gross_pj | PJ | 792.481942 | 811.143671 | +2.3548 | 795.735556 | +0.4106 |
| 119m | harvest_m3 | m3 | 118,977,339.100000 | 119,110,540.863327 | +0.1120 | 116,030,509.304327 | -2.4768 |
| 119m | solid_product_m3 | m3 | 37,673,626.761068 | 37,734,656.240074 | +0.1620 | 37,293,264.823729 | -1.0096 |
| 119m | pulp_product_odt | odt | 12,066,311.269557 | 12,073,987.345619 | +0.0636 | 11,641,636.822471 | -3.5195 |
| 119m | process_residue_pj | PJ | 498.750917 | 499.312684 | +0.1126 | 487.279936 | -2.2999 |
| 119m | harvest_residue_gross_pj | PJ | 444.410766 | 444.558236 | +0.0332 | 425.074473 | -4.3510 |
| 119m | harvest_residue_recoverable_r060_pj | PJ | 266.646460 | 266.734942 | +0.0332 | 255.044684 | -4.3510 |
| 119m | total_residue_gross_pj | PJ | 943.161683 | 943.870920 | +0.0752 | 912.354409 | -3.2664 |
| 150m | harvest_m3 | m3 | 150,000,000.200000 | 150,139,197.455525 | +0.0928 | 133,656,178.228273 | -10.8959 |
| 150m | solid_product_m3 | m3 | 46,136,665.718213 | 46,225,635.529205 | +0.1928 | 43,151,853.409751 | -6.4695 |
| 150m | pulp_product_odt | odt | 15,550,881.011045 | 15,551,206.202699 | +0.0021 | 13,363,103.575321 | -14.0685 |
| 150m | process_residue_pj | PJ | 626.865219 | 627.486116 | +0.0990 | 561.568214 | -10.4164 |
| 150m | harvest_residue_gross_pj | PJ | 573.125413 | 573.225245 | +0.0174 | 484.114827 | -15.5307 |
| 150m | harvest_residue_recoverable_r060_pj | PJ | 343.875248 | 343.935147 | +0.0174 | 290.468896 | -15.5307 |
| 150m | total_residue_gross_pj | PJ | 1,199.990632 | 1,200.711361 | +0.0601 | 1,045.683041 | -12.8591 |

## 8. Operable OSeCAN handoff

- Operable 100/119/150: frozen v5.1 21/21; 100m 6/7 primary, 7/7 achieved; 119m 7/7 primary, 6/7 achieved; 150m 7/7 primary, 6/7 achieved; official tree unchanged `True`.

## 9. Comparison with the original-curve operable cases

The original-curve operable cases used the same mask, controls, and scheduler
on the original RSYC curves.

| Case | Metric | Original curves | Transformed curves | Delta |
|---|---|---:|---:|---:|
| 100m | annual min Mm3/yr | 94.4141 | 98.2434 | +3.8293 |
| 100m | annual max Mm3/yr | 105.4949 | 102.8632 | -2.6317 |
| 100m | annual mean Mm3/yr | 98.6880 | 100.5917 | +1.9037 |
| 100m | period shortfall Mm3 | 160.4216 | 34.6651 | -125.7566 |
| 100m | province shortfall Mm3 | 259.6635 | 67.2620 | -192.4014 |
| 100m | max nominal Mm3/yr | 68.4570 | 76.0498 | +7.5928 |
| 100m | max operable Mm3/yr | 69.3438 | 77.1195 | +7.7757 |
| 119m | annual min Mm3/yr | 97.9475 | 102.6644 | +4.7170 |
| 119m | annual max Mm3/yr | 123.0149 | 122.8093 | -0.2057 |
| 119m | annual mean Mm3/yr | 113.1749 | 116.0305 | +2.8556 |
| 119m | period shortfall Mm3 | 623.2648 | 377.7192 | -245.5456 |
| 119m | province shortfall Mm3 | 752.8481 | 457.3300 | -295.5181 |
| 119m | max nominal Mm3/yr | 67.8543 | 76.2780 | +8.4237 |
| 119m | max operable Mm3/yr | 69.2302 | 77.0942 | +7.8640 |
| 150m | annual min Mm3/yr | 111.6734 | 104.4241 | -7.2492 |
| 150m | annual max Mm3/yr | 154.4895 | 150.9391 | -3.5504 |
| 150m | annual mean Mm3/yr | 132.6875 | 133.6562 | +0.9687 |
| 150m | period shortfall Mm3 | 1,774.8825 | 1,645.1650 | -129.7174 |
| 150m | province shortfall Mm3 | 1,896.9787 | 1,761.3301 | -135.6486 |
| 150m | max nominal Mm3/yr | 61.4868 | 68.4448 | +6.9580 |
| 150m | max operable Mm3/yr | 62.7054 | 70.3013 | +7.5959 |

The transformed operable cases have smaller period and provincial shortfalls
and higher schedule-only maximum levels than the original-curve cases, while
still failing the strict all-province targets.

## 10. Integrity

- Frozen chain v5.1 unchanged:
  `True`.
- Official OSeCAN unchanged:
  `True`.
- Original national 990 m dataset unchanged:
  `True`.
- Unexpected deletion status:
  `True` (parent and data).
- Transformed mask matches the original-curve mask:
  `True`.
- No under-rotation harvest in any case:
  `True`.

## 11. Artifacts

| Artifact | Path |
|---|---|
| Sustainable schedules | `data/derived/ws4/scanfi-national-990m-transformed/sustainable-100m` and `sustainable-150m` |
| Operable schedules | `data/derived/ws4/scanfi-national-990m-transformed/operable-100m`, `operable-119m`, and `operable-150m` |
| Operable mask | `data/derived/ws4/scanfi-national-990m-transformed/operable-mask` |
| Sustainable chains | `data/derived/national/facilities/ws4_transformed_cases/sustainable/100m` and `150m` |
| Operable chains | `data/derived/national/facilities/ws4_transformed_cases/operable/100m`, `119m`, and `150m` |
| Combined summary | `data/derived/national/facilities/ws4_transformed_cases/summary.json` |
| Combined comparisons | `comparison_sustainable_vs_v51.csv`, `comparison_sustainable_to_original_curves.csv`, `comparison_operable_vs_transformed_full_forest.csv`, `comparison_operable_vs_original_curves.csv`, `comparison_operable_chain_vs_v51.csv` |
| OSeCAN handoffs | `osecan/sustainable` and `osecan/operable` |

## 12. Caveats

- Transformed curves are a national model library, not official OSeCAN curves.
- Ages 201-600 are held at the age-200 value; this is a hold, not a forecast.
- The operable mask is a province-share area proxy, not a legal THLB, slope
  screen, or spatial road overlay. NU and PE have no 2016 tenure coverage.
- Whole blocks are selected; provincial overshoot and shortfall are retained.
- Frozen chain v5.1 coefficients are applied by origin province; facility
  assignment, mill capacity, transport, and interprovincial trade are not
  re-run.
- Maximum sustainable levels are schedule-only diagnostics.
- No original dataset, original-curve case, frozen chain v5.1, official OSeCAN,
  or UI artifact is changed.
