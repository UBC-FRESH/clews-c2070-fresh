# WS4 sustainable cases on the share-based operable landbase

**Status:** shortfall result. The 100, 119, and 150 Mm³/yr sustainable cases were
rerun on a share-based operable proxy of the national 990 m WS4 estate. None is
sustained under its frozen provincial allocation shares. The maximum
schedule-only levels on the jurisdictions with operable coverage are 68.46,
67.85, and 61.49 Mm³/yr nominal control (69.34, 69.23, and 62.71 Mm³/yr actual
operable harvest) for the 100/119/150 share sets. All outputs are model
estimates.

**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `sustainable_schedule=true`,
`share_based_operable_proxy=true`, `legal_thlb=false`, `slope_screen=false`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`.

**Boundary:** this is not a legal THLB, an AAC, a slope screen, a spatial road
overlay, a policy result, an investment case, an official OSeCAN change, or a
national supply claim. No chain v5.1, full-forest sustainable artifact, official
OSeCAN file, WS4 dataset, or UI surface is changed.

## 1. Operable screen input

Source: `data/derived/national/managed_landbase/operable_proxy_v2/operable_proxy_v2_by_province.csv`,
`screen_case=screen_core30`.

`screen_core30` is the national operable proxy v2 case with:

- SCANFI median age >= 30 years;
- NBAC 2020 burned pixels excluded;
- block centre within 10 km of a public NRN road.

The published national screen area is 77,050,071.64 ha, or 18.2377% of the
422,475,761.07 ha SCANFI treed area. The road-distance class table
(`road_distance_classes.csv`) reports 121.58 Mha within 5 km, 34.92 Mha from
5–10 km, 41.11 Mha from 10–20 km, and 134.99 Mha beyond 20 km of a public NRN
road. The screen uses only the 0–10 km portion of that public-road proxy.

### 1.1 Area-share mask

The WS4 estate is 1,984,017 blocks / 411,250,630.32 ha. It is already tagged by
province. The mask is built per province:

1. compute a stable `blake2b(province:block_id, digest_size=8)` key for every
   block;
2. rank blocks by that key, with block id as the tie-break;
3. take whole blocks in rank order until the chosen prefix best matches
   `operable_share_of_treed × WS4 province forest area`;
4. never select a block twice.

The selected prefix is the one closest to the share target, so the final block
may overshoot or undershoot by at most half of the largest provincial block.
The published absolute `operable_proxy_ha` is reported as a cross-check but is
not forced onto the WS4 block denominator.

National result: **370,752 blocks / 75,374,046.00 ha**, or 18.3280% of the WS4
forest area. The published absolute area is 77,050,071.64 ha (18.2377% of
SCANFI treed area); the selected area is 1.676 Mha (−2.175%) lower because the
WS4 block forest denominator is smaller than the SCANFI treed denominator. The
share is preserved province by province.

| Province | Coverage | Published share | Published area Mha | WS4 forest Mha | Share target Mha | Selected Mha | Selected blocks |
|---|---|---:|---:|---:|---:|---:|---:|
| BC | yes | 33.6629% | 22.422 | 65.698 | 22.116 | 22.116 | 164,442 |
| AB | yes | 28.5791% | 10.959 | 37.550 | 10.732 | 10.731 | 47,774 |
| SK | yes | 11.2245% | 2.936 | 23.593 | 2.648 | 2.648 | 12,040 |
| MB | yes | 6.8898% | 2.326 | 31.879 | 2.196 | 2.146 | 9,272 |
| ON | yes | 33.4358% | 22.909 | 66.682 | 22.296 | 22.317 | 71,707 |
| QC | yes | 12.8963% | 10.532 | 81.424 | 10.501 | 10.501 | 45,146 |
| NB | yes | 26.4599% | 1.771 | 6.605 | 1.748 | 1.748 | 7,685 |
| NS | yes | 18.1834% | 0.860 | 4.674 | 0.850 | 0.850 | 3,311 |
| PE | **no** | n/a | n/a | 0.269 | 0 | 0 | 0 |
| NL | yes | 2.8825% | 0.678 | 23.342 | 0.673 | 0.673 | 2,660 |
| YT | yes | 5.6880% | 1.474 | 25.809 | 1.468 | 1.468 | 6,025 |
| NT | yes | 0.4234% | 0.184 | 42.370 | 0.179 | 0.177 | 690 |
| NU | **no** | n/a | n/a | 1.355 | 0 | 0 | 0 |

`PE` and `NU` have no accepted 2016 tenure coverage, so the operable proxy is
intentionally blank, not zero. No block is selected there; their case targets
are recorded as shortfalls rather than substituted with harvested volume.

### 1.2 Gaps and limitations

- **No legal THLB.** Tenure is a rights footprint, not a harvest-landbase
  determination. The mask is an area-share proxy.
- **No national slope DEM.** No slope, elevation, steep-terrain, or
  site-degradation filter is applied. Steep and inoperable terrain remains.
- **No spatial road overlay.** Road distance is a 990 m public-NRN Euclidean
  distance proxy. Resource, industrial, winter, and decommissioned roads are
  absent.
- **Only NBAC 2020.** The 2010 and 2015 disturbance cases are gaps, not
  zero-disturbance assumptions.
- **SCANFI age is a prediction surface.** Age >= 30 is a screening convention,
  not a legal rule.
- **Uniform managed share.** The v1 tenure share is uniform inside each
  province-ecoprovince; spatial tenure pattern is not represented.
- **No block-level spatial intersection.** Selected blocks are not intersected
  with the tenure, burn, road, or slope rasters.

## 2. Case method

The scheduler is the sustainable 119 Mm³/yr whole-block scheduler with one
additional eligibility filter:

- eligible blocks are operable-mask blocks whose age at harvest is at least the
  integer MAI rotation (capped to 40–200 years and flagged when the raw peak is
  outside that band);
- blocks are selected oldest-first, volume-aware, within the frozen case
  provincial `allocated_share` targets;
- no block is split, scaled, or harvested below rotation;
- a province that cannot meet its period target records a shortfall and the
  schedule continues;
- the cross-province national fallback is disabled, so a provincial shortfall
  is not hidden by another province's surplus;
- selected blocks are spread over the ten annual offsets of their period by
  longest-processing-time balancing;
- WS4 simulate is run for 100 years and is the authority for harvested volume
  and treated area.

The maximum sustainable level is a schedule-only diagnostic. It binary-searches
the largest nominal control total that keeps every jurisdiction with operable
coverage at its period target, using the same frozen shares and mask. It does
not run WS4 and is not a policy-tested level.

## 3. Case results

| Case | Target Mm³/yr | Sustained | Annual min Mm³/yr | Annual max Mm³/yr | Annual mean Mm³/yr | Annual CV | Period shortfall Mm³ | Provincial shortfall Mm³ | Selected blocks | Treated area Mha | Max nominal Mm³/yr | Max operable Mm³/yr |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 100.0000 | no | 94.4141 | 105.4949 | 98.6880 | 0.0221 | 160.4216 | 259.6635 | 310,750 | 68.234 | 68.4570 | 69.3438 |
| 119m | 118.9773 | no | 97.9475 | 123.0149 | 113.1749 | 0.0700 | 623.2648 | 752.8481 | 352,262 | 76.783 | 67.8543 | 69.2302 |
| 150m | 150.0000 | no | 111.6734 | 154.4895 | 132.6875 | 0.1215 | 1,774.8825 | 1,896.9787 | 410,086 | 89.775 | 61.4868 | 62.7054 |

`Period shortfall` is the sum of negative national period deltas. `Provincial
shortfall` is the sum of provincial target shortfalls before offsetting
provincial overshoot; it is larger when some provinces overshoot while others
shortfall. Neither is a harvest level: the WS4 run remains the volume
authority.

### 3.1 Provincial shortfalls

| Case | Shortfall provinces (Mm³ over 100 years) |
|---|---|
| 100m | QC 132.215, NB 68.855, NL 32.846, PE 25.134, NS 0.614 |
| 119m | QC 534.324, NB 64.018, NL 54.826, SK 41.094, PE 30.221, NS 28.365 |
| 150m | QC 1,277.161, SK 250.729, NS 146.023, NL 111.850, NB 66.280, PE 44.936 |

QC is the binding province in every case. The 100m case also fails because NB,
NL, PE, and NS cannot meet their allocated shares on the screened area. At
119m, SK joins the shortfall set; at 150m, the QC shortfall dominates. Every
10-year period (0–9) contains at least one shortfall in all three cases.

No block is harvested under rotation or outside the operable mask. The selected
block count includes reharvests after a full rotation: 71,311 / 82,582 /
105,174 blocks respectively.

## 4. Full-forest versus operable

The full-forest cases are the existing sustainable artifacts. They sustain
their targets because the full 411.25 Mha estate is eligible. The operable mask
removes about 81.7% of the estate and makes the provincial shares infeasible.

| Case | Full-forest annual mean Mm³/yr | Full-forest sustained | Operable annual mean Mm³/yr | Operable sustained | Max nominal Mm³/yr |
|---|---:|---|---:|---|---:|
| 100m | 101.1050 | yes | 98.6880 | no | 68.4570 |
| 119m | 119.8839 | yes | 113.1749 | no | 67.8543 |
| 150m | 151.2477 | yes | 132.6875 | no | 61.4868 |

## 5. Chain comparison against frozen v5.1 full-forest totals

Frozen v5.1 per-province coefficients are applied to operable WS4 harvest by
origin province. The primary basis is the first-period annual average; the
achieved basis is the 100-year annual average. Both are unscaled.

| Case | Metric | v5.1 full-forest | Operable primary | Delta % | Operable achieved | Delta % |
|---|---|---:|---:|---:|---:|---:|
| 100m | harvest Mm³/yr | 100.0000 | 102.5384 | +2.5384 | 98.6880 | −1.3120 |
| 100m | solid product Mm³ | 31.6928 | 32.3381 | +2.0362 | 31.3412 | −1.1094 |
| 100m | pulp odt | 10.1367 | 10.4490 | +3.0808 | 9.9703 | −1.6420 |
| 100m | process residue PJ | 419.2380 | 429.7831 | +2.5153 | 413.7154 | −1.3173 |
| 100m | harvest residue gross PJ | 373.2439 | 383.1812 | +2.6624 | 368.7077 | −1.2154 |
| 100m | total residue gross PJ | 792.4819 | 812.9644 | +2.5846 | 782.4231 | −1.2693 |
| 119m | harvest Mm³/yr | 118.9773 | 119.1450 | +0.1410 | 113.1749 | −4.8770 |
| 119m | solid product Mm³ | 37.6736 | 37.7533 | +0.2114 | 36.4888 | −3.1449 |
| 119m | pulp odt | 12.0663 | 12.0719 | +0.0466 | 11.2776 | −6.5367 |
| 119m | process residue PJ | 498.7509 | 499.4616 | +0.1425 | 475.0618 | −4.7497 |
| 119m | harvest residue gross PJ | 444.4108 | 445.1429 | +0.1647 | 417.4732 | −6.0614 |
| 119m | total residue gross PJ | 943.1617 | 944.6045 | +0.1530 | 892.5350 | −5.3678 |
| 150m | harvest Mm³/yr | 150.0000 | 150.0962 | +0.0641 | 132.6875 | −11.5416 |
| 150m | solid product Mm³ | 46.1367 | 46.2059 | +0.1501 | 42.8594 | −7.1033 |
| 150m | pulp odt | 15.5509 | 15.5463 | −0.0294 | 13.2281 | −14.9363 |
| 150m | process residue PJ | 626.8652 | 627.3019 | +0.0697 | 557.1407 | −11.1227 |
| 150m | harvest residue gross PJ | 573.1254 | 573.5647 | +0.0766 | 481.5267 | −15.9823 |
| 150m | total residue gross PJ | 1,199.9906 | 1,200.8666 | +0.0730 | 1,038.6674 | −13.4437 |

The first-period basis can be above the frozen target because the first period
overshoots its target and later periods shortfall. The 100-year achieved basis
is the better summary of the operable shortfall.

## 6. OSeCAN tiers

The operable handoff reuses the frozen handoff-v10 province-to-zone weights,
frozen v9 process/harvest definitions, and frozen v5.1 tier thresholds. Frozen
v5.1 reference positions match handoff v10 21/21. The official OSeCAN tree is
unchanged: 146 files, `80caa91c...`, 0 changed files.

| Case | Primary matches | Achieved matches | Positions |
|---|---:|---:|---:|
| 100m | 6/7 | 7/7 | 7/7 |
| 119m | 7/7 | 6/7 | 7/7 |
| 150m | 7/7 | 6/7 | 7/7 |

The changed positions are findings from the operable residue totals, not
official OSeCAN changes. The combined handoff writes 546 zone rows and 42 tier
positions across the three cases.

## 7. Artifacts

- Mask: `data/derived/ws4/operable-mask/`
  (`operable_mask.parquet`, `operable_mask_summary.csv`, `mask_summary.json`,
  `OPERABLE_MASK.md`).
- Schedules and WS4 runs: `data/derived/ws4/operable-{100m,119m,150m}/`.
- Chains, combined summary/report, comparison, and OSeCAN:
  `data/derived/national/facilities/ws4_operable_cases/`.

Reproduce with:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_operable_cases
```

Tests:

```bash
.venv/bin/python -m pytest tests/test_ws4_operable_cases.py -q
```
