# Handoff — National facility chain v5.1 (assignment v10 at 100/119/150 Mm³)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Frozen all-case assignment-v10 screening chain; model estimate, not observed, not policy grade |
| Freeze tag | `national-facility-chain-v5.1` (annotated; points at this documentation commit) |
| Model commit | `2775317cb733a1a75ac0fd3d1dc88963a1e2eab1` |
| Data commit | `0d3b2ff4c27f18bad2f93fd6c269c180c4dbcadc` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Supersedes | `national-facility-chain-v5` (assignment v10 at 119 Mm³ only) |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| OSeCAN status | Read-only handoff; `official_osecan_changed=false`; no transport cost in official tiers |
| Scope boundary | No UI change and no official OSeCAN change |

Version 5.1 removes the v5 scope boundary that left the 100 and 150 Mm³ cases
on assignment-v6 local-first. All three harvest cases now use the assignment
v10 node-to-facility transport-aware LP with the same 525 supply nodes, the
same capacity-v3 eligible facilities and 1.2× floor, and the same transport
cost v1 curve. National products and residue remain invariant at each case.
The 119 Mm³ results are unchanged from frozen v5.

## 1. Chain stages

```
inventory v1 -> capacity v3 -> assignment v10 cases (100/119/150)
             -> product-chain v5 -> facility residue v9 -> residue curve v9
             -> chain scenarios v9 -> OSeCAN handoff v10
             -> OSeCAN scenarios v9
```

New code:

- `workflows/national/facilities/national_facility_assignment_v10_cases.py`
- `workflows/national/facilities/facility_residue_supply_v9.py`
- `workflows/national/facilities/national_residue_curve_v9.py`
- `workflows/national/facilities/national_facility_chain_scenarios_v9.py`
- `workflows/national/facilities/forestry_osecan_handoff_v10.py`
- `workflows/national/facilities/forestry_osecan_scenarios_v9.py`

New artifacts:

- `data/derived/national/facilities/national_facility_assignment_v10/`
  (extended with v10 case extension case files; primary 119 files unchanged)
- `data/derived/national/facilities/facility_residue_supply_v9/`
- `data/derived/national/facilities/national_residue_curve_v9/`
- `data/derived/national/facilities/national_facility_chain_scenarios_v9/`
- `data/derived/national/facilities/forestry_osecan_handoff_v10/`
- `data/derived/national/facilities/forestry_osecan_scenarios_v9/`

## 2. Assignment v10 case extension

The 100 and 150 Mm³ cases scale each province's 119 Mm³ node harvest vector to
the documented provincial allocation and re-solve the same node-to-facility
transportation LP. Within-province node shares, the 525-node set, the eligible
facility set, finite capacity bounds, the 1.2× floor and the transport cost v1
curve are unchanged. The 119 Mm³ primary assignment CSVs are byte-identical to
frozen v5.

| Metric | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|
| Assigned volume Mm³ | 100.000 | 118.977 | 150.000 |
| Facilities used | 270 | 296 | 333 |
| Cross-border volume Mm³ | 1.161 | 2.325 | 11.036 |
| Cross-border share | 1.161% | 1.954% | 7.357% |
| Proxy share of assigned | 54.134% | 54.077% | 52.973% |
| Facility HHI | 0.008742 | 0.008868 | 0.008580 |
| Top-5 facility share | 12.222% | 12.943% | 12.245% |
| Transport cost billion CAD/yr | 2.702 | 3.392 | 5.018 |
| Transport cost CAD/m³ | 27.016 | 28.511 | 33.454 |
| Transport cost CAD/GJ | 6.638 | 7.005 | 8.220 |
| Mean road distance km | 255.328 | 270.289 | 319.790 |
| Median road distance km | 138.680 | 147.217 | 168.264 |
| P90 road distance km | 611.709 | 635.737 | 775.996 |
| Share ≥150 km | 45.670% | 48.619% | 55.970% |
| Share ≥300 km | 27.363% | 30.163% | 33.987% |

Against assignment-v6 local-first at the same harvest case, v10 lowers truck
transport cost by 30.73% at 100 Mm³ and 26.13% at 150 Mm³, and mean road
distance by 31.97% and 27.04%. The 119 Mm³ deltas remain the frozen v5 values
(−31.59% cost, −32.78% distance).

## 3. National products and residue are invariant

At each harvest case the product/residue totals are identical to the
assignment-v6 chain v7 baseline:

| Metric | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|
| Sawlog product Mm³ | 28.566 | 33.957 | 41.585 |
| Panel product Mm³ | 3.127 | 3.717 | 4.552 |
| Pulp product M odt | 10.137 | 12.066 | 15.551 |
| Pellet product M odt | 4.372 | 5.230 | 7.039 |
| Bio product GWh | 778.319 | 930.946 | 1,253.014 |
| Process residue PJ | 419.238 | 498.751 | 626.865 |
| Harvest residue gross PJ | 373.244 | 444.411 | 573.125 |
| Harvest recoverable 60% PJ | 223.946 | 266.646 | 343.875 |
| Total residue gross PJ | 792.482 | 943.162 | 1,199.991 |
| Total residue recoverable 60% PJ | 643.184 | 765.397 | 970.740 |

`v5_v6_product_residue_invariance.csv` records zero delta for all 30
case/metric rows; `v6_v10_case_comparison.csv` records zero process/harvest
residue deltas.

## 4. Facility residue distribution

Process residue is facility-assigned. The 119 Mm³ facility residue rows are
inherited unchanged from frozen `facility_residue_supply_v8`. The 100/150 Mm³
rows use the same class-matching rule and E1/E2c product-chain v5
coefficients.

| Metric | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|
| Facilities with positive process residue | 248 | 271 | 303 |
| Process residue M odt | 22.662 | 26.960 | 33.885 |
| Process residue PJ | 419.238 | 498.751 | 626.865 |
| Cross-border residue share | 2.513% | 8.500% | 12.117% |
| Proxy residue share | 56.401% | 56.600% | 53.504% |
| Process-residue HHI | 0.023893 | 0.023211 | 0.019132 |
| Top-1 residue share | 10.535% | 10.933% | 10.203% |
| Top-5 residue share | 25.900% | 24.668% | 21.278% |

At 100 Mm³ the v10 distribution uses 248 positive-residue facilities versus
164 for v6; at 150 Mm³ it uses 303 versus 215. The facility residue HHI falls
from 0.032562 to 0.023893 at 100 Mm³ and from 0.028928 to 0.019132 at
150 Mm³. The proxy residue share rises by 18.06 pp at 100 Mm³ and 12.62 pp at
150 Mm³; this is a screening distribution result, not observed mill output.

## 5. Residue curve v9 and OSeCAN handoff v10

Curve v9 keeps the 119 Mm³ national and province curve files byte-identical to
frozen v8 and adds `scenario_residue_curve.csv` for the 100/119/150 cases. The
119 national curve reproduces v8 with a maximum PJ gap of 0.0.

Handoff v10 keeps the all-zone province-to-zone proxy weights, applies them to
curve v9, and adds `case_zone_summary.csv` and `case_tier_positions.csv` for
all three harvest cases. The 119 Mm³ zone totals reproduce handoff v9 exactly.
The official OSeCAN tree is hash-checked before and after: 146 files, tree
SHA-256
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
0 changed files.

OSeCAN tiers remain unchanged at all 21 scenario/stream/case positions versus
scenarios v8. Transport cost is reported only in the facility chain and is not
an OSeCAN input (`transport_cost_in_osecan=false`).

| Stream | Case | 100 Mm³ PJ | 119 Mm³ PJ | 150 Mm³ PJ | Tier |
|---|---|---:|---:|---:|---|
| Process | 25% | 104.810 | 124.688 | 156.716 | T2/T3/T4 |
| Process | 50% | 209.619 | 249.375 | 313.433 | T4/above/above |
| Process | 75% | 314.429 | 374.063 | 470.149 | above/above/above |
| Harvest | 35% | 130.635 | 155.544 | 200.594 | T3/T4/T4 |
| Harvest | 60% | 223.946 | 266.646 | 343.875 | above/above/above |
| Harvest | 75% | 279.933 | 333.308 | 429.844 | above/above/above |

## 6. 119 Mm³ invariance guards

- Assignment primary `v10_node_assignment.csv`,
  `v10_facility_utilization.csv` and `v10_province_summary.csv` are
  byte-identical to frozen v5.
- Chain v9 119 assignment rows have zero maximum numeric difference from
  chain v8.
- Facility residue v9 119 facility totals have zero maximum difference from
  v8 (294 facilities, 26,959,509.023 odt).
- Curve v9 119 national, province and OSeCAN-context files are byte-identical
  to v8.
- Handoff v10 119 zone totals reproduce handoff v9 (maximum PJ gap 0.0).
- Scenarios v9 has 0 changed tier positions and 0.0 maximum PJ delta versus
  scenarios v8.

## 7. Verification and status

- All six v5.1 manifests verify (68 output entries, 0 mismatches).
- Assignment v10 primary files are unchanged; the extended primary manifest
  covers 20 entries.
- 119 invariance guards pass as listed in section 6.
- All 30 product/residue invariance rows have zero delta.
- Official OSeCAN tree unchanged: 146 files, tree SHA-256
  `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
  0 changed files.
- No unexpected `D` in the parent or data repository status.
- No UI change, no official OSeCAN change, no data request or DUA.
- No national supply, policy, operating, or grade-validation claim.

## 8. Limitations and freeze record

Limitations:

- V10 is a screening transportation LP with haversine × province-factor
  distances, not observed routes for every node-facility pair.
- The 100/150 Mm³ cases scale within-province node harvest shares from the
  119 Mm³ allocation; they are not independent node-level inventories.
- Transport cost is trucking only; it excludes feedstock, processing,
  conversion, handling, storage, border, ferry and winter-road costs.
- Proxy capacity remains material (52.97–54.13% of assigned volume).
- Facility class matching and the national class fallback remain v1 rules.
- Products and residue are invariant model estimates, not observed mill output.
- No national or policy claim is made.

**Freeze record.** Annotated tag `national-facility-chain-v5.1` marks this
documentation commit. It records model commit
`2775317cb733a1a75ac0fd3d1dc88963a1e2eab1` and data commit
`0d3b2ff4c27f18bad2f93fd6c269c180c4dbcadc`. It does not move
`national-facility-chain-v5` or any earlier tag. The freeze commit is
documentation and tag only; no code, model artifact, UI, or official OSeCAN
file changes at that commit.
