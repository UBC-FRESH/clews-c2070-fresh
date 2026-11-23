# Handoff — National facility chain v5 (assignment v10 node transport-aware)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Frozen assignment-v10 screening chain; model estimate, not observed, not policy grade |
| Freeze tag | `national-facility-chain-v5` (annotated; points at this documentation commit) |
| Model commit | recorded after the code/data commit; see section 8 |
| Data commit | `86ad55506d6ef10bcd60a6b6701d4d06c0026d12` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Supersedes | `national-facility-chain-v4` (centroid/local-first assignment v6) |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| OSeCAN status | Read-only handoff; `official_osecan_changed=false`; no transport cost in official tiers |
| Scope boundary | No UI change and no official OSeCAN change |

This chain keeps the v4 E1 measured + E2c density-calibrated product and residue
coefficients and replaces the 119 Mm³ facility distribution with the
assignment v10 node-to-facility transport-aware LP. National product and
residue totals are invariant. Facility/province distribution, modelled
transport cost and distance bands change.

## 1. Chain stages

```
inventory v1 -> capacity v3 -> assignment v10 -> product-chain v5
             -> facility residue v8 -> residue curve v8
             -> chain scenarios v8 -> OSeCAN handoff v9
             -> OSeCAN scenarios v8
```

New code:

- `workflows/national/facilities/facility_residue_supply_v8.py`
- `workflows/national/facilities/national_residue_curve_v8.py`
- `workflows/national/facilities/national_facility_chain_scenarios_v8.py`
- `workflows/national/facilities/forestry_osecan_handoff_v9.py`
- `workflows/national/facilities/forestry_osecan_scenarios_v8.py`

New artifacts:

- `data/derived/national/facilities/facility_residue_supply_v8/`
- `data/derived/national/facilities/national_residue_curve_v8/`
- `data/derived/national/facilities/national_facility_chain_scenarios_v8/`
- `data/derived/national/facilities/forestry_osecan_handoff_v9/`
- `data/derived/national/facilities/forestry_osecan_scenarios_v8/`

The product-chain v5, capacity v3 and inventory v1 artifacts are unchanged.

## 2. Assignment v10 and case scope

Assignment v10 replaces the province-centroid origin with 525 committed RSYC
supply nodes (486 positive-volume nodes and 39 zero-volume NU nodes) and solves
a bipartite node-to-facility transportation LP with capacity v3 finite limits,
the 1.2× provincial floor, the unchanged 118.977 Mm³ harvest and the transport
cost v1 curve. Node-facility distance is haversine × facility-province road
factor; the 81-pair public OSRM sample is an external check only.

At the 119 Mm³ reference:

| Metric | v4 assignment v6 centroid/local-first | v5 assignment v10 node transport-aware |
|---|---:|---:|
| Assigned volume Mm³ | 118.977 | 118.977 |
| Facilities used | 208 | 296 |
| Cross-border volume Mm³ | 0.036 | 2.325 |
| Cross-border share | 0.031% | 1.954% |
| Proxy share of assigned | 39.893% | 54.077% |
| Facility HHI | 0.012870 | 0.008868 |
| Top-5 facility share | 15.746% | 12.943% |

V10 is solved on the 119 Mm³ provincial allocation. The 100 and 150 Mm³
sensitivity cases remain the v7 assignment-v6 local-first screening cases and
are labelled `assignment_v6_local_first` in chain v8. This is a scope boundary,
not a claim that transport-aware assignment is available at all three cases.

## 3. National products and residue are invariant

At 119 Mm³ the chain v8 national totals are identical to v4:

| Metric | Value |
|---|---:|
| Sawlog product Mm³ | 33.957 |
| Panel product Mm³ | 3.717 |
| Pulp product M odt | 12.066 |
| Pellet product M odt | 5.230 |
| Bio product GWh | 930.946 |
| Process residue PJ | 498.751 |
| Harvest residue gross PJ | 444.411 |
| Harvest recoverable 60% PJ | 266.646 |
| Total residue gross PJ | 943.162 |
| Total residue recoverable 60% PJ | 765.397 |

The facility residue v8 artifact also preserves process residue at
26.959509 M odt / 498.751 PJ and all product-chain/feedstock conservation
identities. Assignment changes the location and allocation weights, not the
national product or residue totals.

## 4. Transport cost and distance bands

Transport is the separate transport cost v1 trucking curve (52.5 m³ base
payload, empty return, 2026 CAD). It is **not** included in official OSeCAN
supply-curve tiers and is not an OSeCAN input.

| Metric | v4 | v5 | Delta |
|---|---:|---:|---:|
| Transport cost (billion CAD/yr) | 4.958 | 3.392 | −1.566 (−31.59%) |
| Transport cost (CAD/m³) | 41.676 | 28.511 | −13.165 |
| Transport cost (CAD/GJ) | 10.240 | 7.005 | −3.235 |
| Mean road distance (km) | 402.123 | 270.289 | −131.833 (−32.78%) |
| Median road distance (km) | 345.675 | 147.217 | −198.458 |
| P90 road distance (km) | 762.517 | 635.737 | −126.780 |
| Share ≥150 km | 83.83% | 48.62% | −35.21 pp |
| Share ≥300 km | 52.65% | 30.16% | −22.49 pp |

Distance-band volumes and shares are in
`facility_residue_supply_v8/transport_cost_summary.csv`. The v10 diagnostic
distance-only LP reaches 255.8 km and the nearest-facility weighted mean is
191.5 km, so facility geography and capacity still leave a residual distance
floor above the unconstrained nearest-facility case.

## 5. Facility and province distribution

Assignment v10 uses 296 facilities versus 208 in v4. The facility residue
distribution changes even though the national totals do not:

| Metric | v4 | v5 | Delta |
|---|---:|---:|---:|
| Facilities with positive process residue | 186 | 271 | +85 |
| Process residue odt | 26,959,509 | 26,959,509 | 0 |
| Cross-border process residue odt | 1,405,576 | 2,291,443 | +884,867 |
| Cross-border residue share | 5.22% | 8.50% | +3.28 pp |
| Process-residue HHI | 0.028873 | 0.023211 | −0.005662 |
| Top-1 residue share | 11.265% | 10.933% | −0.332 pp |
| Top-5 residue share | 29.511% | 24.668% | −4.843 pp |
| Proxy residue share | 37.780% | 56.600% | +18.820 pp |
| Published residue share | 62.220% | 43.400% | −18.820 pp |

Facility-level and province-level v4/v5 tables are in
`facility_residue_supply_v8/v4_v5_facility_distribution.csv`,
`v4_v5_facility_comparison.csv`, `v4_v5_province_comparison.csv` and
`v4_v5_transport_comparison.csv`. Chain v8 carries the same comparison as
`v4_v5_chain_comparison.csv`.

## 6. OSeCAN handoff v9 and scenarios v8

Handoff v9 maps curve v8 to all 13 OSeCAN zones with no `UNMAPPED` bucket. The
official OSeCAN tree is hash-checked before and after; 146 files and tree hash
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a` are
unchanged.

National tiers at 119 Mm³ are unchanged from v4:

| Stream | Case | PJ | Tier |
|---|---|---:|---|
| Process | 0% | 0.000 | T1 |
| Process | 25% | 124.688 | T3 |
| Process | 50% | 249.375 | above_T4 |
| Process | 75% | 374.063 | above_T4 |
| Harvest | 35% | 155.544 | T4 |
| Harvest | 60% | 266.646 | above_T4 |
| Harvest | 75% | 333.308 | above_T4 |

The process-residue zone distribution shifts because v10 changes the facility
province. At process 50%, Q1/Q2 fall by 5.505/3.509 PJ and L1/T1 rise by
6.708/2.634 PJ; other zones move by less than 1 PJ. Harvest zone PJ is
unchanged because harvest residue remains origin-province only. The full
zone table is `forestry_osecan_handoff_v9/v4_v5_zone_comparison.csv`; the
all-scenario tier table is
`forestry_osecan_scenarios_v8/v4_v5_tier_comparison.csv`.

All 21 scenario/stream/case tier positions are unchanged
(`tier_changed=false`) and `transport_cost_in_osecan=false`. Transport cost is
reported only in the facility chain.

## 7. Verification and status

- Focused v5 tests: `tests/test_national_facility_chain_v5.py` — 8 passed.
- Existing facility/chain/curve/OSeCAN tests — 37 passed (45 total in the
  combined run).
- Manifests: all five v5 manifests verify (46 output entries), as do the
  unchanged assignment v10, product-chain v5 and capacity v3 manifests
  (27 entries). Total 73/73 entries, 0 mismatches.
- Official OSeCAN tree unchanged: 146 files, tree SHA-256
  `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
  0 changed files before/after the handoff build.
- No unexpected `D` in the parent or data repository status.
- No request, DUA, restricted data, UI change, or official OSeCAN change.
- No national supply, policy, operating, or grade-validation claim.

## 8. Limitations and freeze record

Limitations:

- V10 is a screening transportation LP with haversine × province-factor
  distances, not observed routes for every node-facility pair.
- Transport cost is trucking only; it excludes feedstock, processing,
  conversion, handling, storage, border, ferry and winter-road costs.
- V10 exists only at the 119 Mm³ reference. The 100 and 150 Mm³ cases remain
  local-first v6 screening assignments.
- Proxy capacity remains material (54.08% of assigned volume at 119 Mm³).
- Facility class matching and the national class fallback remain v1 rules.
- Products and residue are invariant model estimates, not observed mill output.
- No national or policy claim is made.

**Freeze record.** Annotated tag `national-facility-chain-v5` marks this
documentation commit. It records model commit `0943b12027f9eb994bda3646d9e3fe7548454fb0` and
data commit `86ad55506d6ef10bcd60a6b6701d4d06c0026d12`. It does not move
`national-facility-chain-v4` or any earlier tag. The freeze commit is
documentation and tag only; no code, model artifact, UI, or official OSeCAN file
changes at that commit.
