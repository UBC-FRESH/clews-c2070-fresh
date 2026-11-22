# Model card — National facility chain v1

| Field | Value |
|---|---|
| Package | National facility chain v1 (frozen screening package) |
| Freeze tag | `national-facility-chain-v1` |
| Date | 2026-11-22 |
| Parent commit at freeze | `94c7501deb1924608e38df36e11f49728f93a1e0` |
| Data commit | `6ca272e358ab9f2ae8879bd47d673f96ff46c374` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Stage artifacts | Inventory v1 → capacity v3 → assignment v6 → product-chain v1 → residue curve v3 → facility residue v3 → chain scenarios v3 → OSeCAN handoff v4 |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false` |
| Energy-side status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No UI change; no public OSeCAN change; no legal/AAC claim |

## 1. Purpose

The national facility chain is a reproducible screening chain that connects a
documented national harvest case to a bounded set of Canadian forest-product
facilities, then converts assigned feedstock into products and process/harvest
residue. It is designed to answer four limited questions:

1. Can a 100/119/150 Mm³ national harvest case be placed on the facility
   inventory without an unbounded sink, and with what local/cross-border split?
2. How much of that placement relies on published capacity versus a
   class-median proxy?
3. What products and residue follow from the assigned feedstock under
   transparently labelled BC-anchored coefficients?
4. Can those residue streams be handed to the 13 OSeCAN zones as a read-only,
   traceable sensitivity, without changing the official energy model?

It is not a mill-by-mill operating plan, a timber-supply forecast, an
interprovincial trade forecast, a policy evaluation, or an investment case.

## 2. Chain and scope

| Stage | Artifact | What it does | Key output |
|---|---|---|---|
| Inventory | `national_facility_inventory_v1` | Adds the 2025 Canadian Biomass pellet-map producers to inventory v0 and dedupes them | 519 records; 212 with published capacity |
| Capacity | `national_facility_capacity_v3` | Adds Quebec MRNF 2026 permitted volumes, five company disclosures, and retains class-median proxies | 689 records; 271.342 Mm³/yr total capacity |
| Assignment | `national_facility_assignment_v6` | Local-first, finite-capacity allocation of the 119 Mm³ case on capacity v3 with the 1.2× provincial floor as primary | 208 facilities; 118.977 Mm³ assigned |
| Product chain | `national_product_chain_v1` | Applies BC anchor / donor coefficients to assigned and provincial feedstock | Products, process residue, gross/recoverable harvest residue |
| Residue curve | `national_residue_curve_v3` | Converts process/harvest residue to 0/25/50/75% and 35/60/75% cases at 18.5 GJ/ODT | National and provincial PJ curves |
| Facility residue | `facility_residue_supply_v3` | Assigns process residue to facilities; keeps harvest residue origin-province | 28.433 M odt process residue; 526.006 PJ |
| Scenarios | `national_facility_chain_scenarios_v3` | Re-runs 100/119/150 Mm³ on the capacity-v3 floor and applies product/residue coefficients | Full scenario chain |
| OSeCAN handoff | `forestry_osecan_handoff_v4` | Allocates province/case residue to all 13 OSeCAN zones | 119 handoff rows; 91 zone/case summary rows |

The chain is static and single-period at each harvest case. It contains no
forest-growth, investment, facility-entry/exit, or annual time path. The
OSeCAN 2050 values used for context are external model context, not a
projection produced by this chain.

## 3. Data sources and evidence tiers

### 3.1 Facility inventory (v1)

Inventory v0 contributed 486 records. The public Canadian Biomass 2025 Pellet
Mill Map contributed 43 current-producer rows: 10 matched existing pellet
records and 33 were added (32 with a reported capacity). Inventory v1 therefore
has **519 records** and **212 published-capacity records**.

Other inventory sources establish presence and location, not capacity:

- OpenStreetMap facility presence for non-BC records;
- BC facility layer derived from the FFCM / ISED Mills DB 2015 lineage;
- OpenStreetMap Nominatim city centroids for pellet-map plant cities. City
  centroids are not plant-gate coordinates.

### 3.2 Capacity (v3)

Capacity v3 has **689 records**: **433 published**, **241 class-median proxy**,
and **15 not published / unavailable**. Total capacity is **271.342 Mm³/yr**:
**166.609 Mm³/yr published** and **104.733 Mm³/yr proxy**. Eligible assignment
capacity after excluding unlocated and non-operating records is
**243.230 Mm³/yr**; **27.302 Mm³/yr** of unlocated capacity is excluded, and
2 non-operating records are excluded.

| Published evidence | Records | Capacity (Mm³/yr) | Basis |
|---|---:|---:|---|
| `published_mills_db_2015_infeed_proxy` | 179 | 95.838 | BC 2015 processing-line infeed proxy |
| `published_authorized_consumption` | 216 | 64.086 | Quebec MRNF June 2026 permitted softwood + hardwood consumption |
| `published_capacity_converted` | 33 | 4.447 | 2025 finished-pellet tonnes/yr at 650 kg/m³ |
| `published_sawn_output_converted` | 5 | 2.237 | Company sawn-lumber output at 2.36 m³/Mfbm |
| `class_median_proxy` | 241 | 104.733 | BC Mills DB / FFCM class median for blank relevant-class records |

Source details:

- **Quebec MRNF, June 2026 register**: 242 permitted primary-processing mills;
  19 category-8 paper rows without a permitted wood volume are excluded rather
  than imputed, leaving 223 volume-bearing rows (216 in relevant classes).
  Matching added 167 new records and matched 49 existing records. The permitted
  volume is a regulatory ceiling, not observed throughput.
- **Ontario MNRF 2026–27 mill list**: 94 licensed mills; the public list has no
  facility capacity and is used only for names, classes, cities, and
  coordinates.
- **Company disclosures**: four GreenFirst Ontario sawmills and Tolko High
  Level, Alberta. Sawn-lumber output is converted at 2.36 m³/Mfbm and is an
  output basis, not log intake.
- **Canadian Biomass 2025 pellet map**: finished-pellet tonnes/yr converted at
  650 kg/m³, inside the NRCan / CAN-CSA-ISO 17225-2 600–750 kg/m³ range. This is
  finished-product bulk volume, not infeed-equivalent.
- **BC Mills DB 2015 / FFCM**: 179 published infeed-proxy records and the
  class-median anchor for proxy rows. These are historical engineering proxies,
  not current licensed capacity.
- **Class-median proxy**: a statistical placeholder applied to a blank
  relevant-class record; it is not a facility measurement. The proxy pool is
  241 records / 104.733 Mm³/yr.

The capacity bases are not interchangeable. In particular, a 2015 infeed proxy,
a 2026 Quebec permit ceiling, a finished-pellet bulk volume, and a converted
sawn-lumber output must not be added or compared as if they were the same
measurement.

### 3.3 Product and residue coefficients

- **BC anchor (32 coefficient rows)** uses the 2028 BC supply and Stage A
  assignment plus FFCM processing-line ratios. Softwood and hardwood grade
  shares, class allocations, and class technology ratios are BC-specific.
- **Donor coefficients (35 rows)** apply the BC aggregate donor ratio to every
  other province. No provincial yield is fabricated.
- **Harvest residue** starts from gross BC supply and uses the BC Stage A
  residue recovery share. The recoverable portion is routed to PELLET/BIO and
  the remainder is reported as unrecovered roadside residue.
- **PANEL** is absent from BC Stage A; its allocation is the explicit BC panel
  nameplate share of sawlog plus panel nameplate, labelled as a BC anchor.

### 3.4 OSeCAN zone context

The handoff uses existing OSeCAN evidence, not new energy modelling:

- BC zone weights use 2050 OSeCAN zonal supply dispatch
  (`SUPBCWB1` / `SUPBCWB2`): B1 0.082839, B2 0.917161.
- AB, ON, and QC use 2050 OSeCAN zonal dispatch activity
  (`PWRBCW<zone>`) as a proxy: A1 0.643794 / A2 0.356206;
  O1 0.833225 / O2 0.166775; Q1 0.610703 / Q2 0.389297.
- Single-zone and aggregate jurisdictions use weight 1.0: SK → S1,
  MB → M1, NB/NS/PE → T1, NL → L1, YT/NT/NU → N1.
- No defensible ecoprovince-to-zone mapping exists for A1/A2, O1/O2, or Q1/Q2,
  so operable-area weights are not applied there. Equal split is implemented
  but not activated.
- Every multi-zone allocation is `proxy_share=true`; no observed
  sub-provincial residue split is used.

## 4. Facility inventory, capacity, and assignment

### 4.1 Assignment rule

- 13 jurisdictions; NU has no modelled harvest in the 119 Mm³ case.
- Relevant facility classes are SAW, PULP, PANEL, PELLET, and BIO.
- Every assignment row carries a finite capacity. There is no unbounded sink.
- Allocation is **local first**: a province's eligible local facilities are
  exhausted before any cross-border volume is considered.
- Distance is screened with a 1.3 road factor and a 500 km cross-border
  penalty. This is a locational screen, not a transport-cost model.
- The primary v6 case applies a **1.2× provincial floor**. Where published plus
  proxy capacity is below `1.2 × harvest`, proxy capacities only are scaled by
  one provincial factor. Published capacity is never altered. In the 119 Mm³
  case, 47 proxy records / 38.035 Mm³ are scaled in AB, SK, MB, and ON
  (factors 4.505, 7.359, 1.892, and 1.595 respectively). Scaled capacity is
  not observed capacity.
- NT has no eligible local capacity. Its 36,376 m³ is placed cross-border; the
  remaining **43,651 m³** provincial-floor gap is the 1.2× NT buffer, not
  unassigned harvest.

### 4.2 119 Mm³ base (assignment v6, `province_capacity_floor`)

| Metric | Value |
|---|---:|
| Harvest / assigned | 118.977 Mm³ |
| Local | 118.941 Mm³ (99.97%) |
| Cross-border | 36,376 m³ (0.031%) |
| Published volume | 71.514 Mm³ (60.11%) |
| Proxy volume | 47.463 Mm³ (39.89%) |
| Scaled proxy volume | 38.035 Mm³ |
| Facilities used | 208 |
| Capacity utilization | 43.5% of 273.468 Mm³ eligible |
| Largest facility | 8.093 Mm³ (6.80%) |
| Top-5 share / HHI | 15.75% / 0.01287 |
| Unassigned | 0 m³ |
| Provincial floor shortfall | 43,651 m³ (NT only) |

Validation reports conservation, finite capacity, no capacity violation, no
unbounded assignment, no non-operating assignment, and `max_utilization=1.0`.
The published capacity is unchanged by the floor.

## 5. Product and residue chain

Product-chain v1 converts assigned class feedstock plus provincial harvest
residue. All quantities are model estimates, not measured mill output or a
facility survey.

### 5.1 119 Mm³ national chain

| Metric | Value |
|---|---:|
| Sawlog feedstock | 70.464 Mm³ |
| Pulp feedstock | 36.673 Mm³ |
| Panel feedstock | 11.534 Mm³ |
| Pellet feedstock | 12.112 Mm³ |
| Bio feedstock | 2.536 Mm³ |
| Total feedstock | 133.319 Mm³ |
| Sawlog product | 48.467 Mm³ |
| Pulp product | 7.590 M odt |
| Panel product | 5.305 Mm³ |
| Pellet product | 4.748 M odt |
| Bio product | 845.182 GWh |
| Process residue | 28.433 M odt / 526.006 PJ |
| Harvest residue, gross | 9.069 M odt / 167.779 PJ |
| Harvest residue, recoverable | 6.454 M odt / 119.397 PJ |
| Harvest residue, unrecovered | 2.615 M odt / 48.381 PJ |
| Total residue, gross | 37.502 M odt / 693.784 PJ |
| Total residue, recoverable | 34.887 M odt / 645.403 PJ |

Process residue per m³ feedstock is 0.213268 odt/m³; gross harvest residue per
m³ harvest is 0.076226 odt/m³; total gross residue per m³ harvest is 0.315202
odt/m³. Process and harvest residue are separate streams and are never summed
into a combined OSeCAN row.

### 5.2 Residue curve v3

At 18.5 GJ/ODT, the national process-availability cases are 0 / 131.501 /
263.003 / 394.504 PJ for 0/25/50/75%. The national harvest recovery cases are
58.722 / 100.667 / 125.834 PJ for 35/60/75%. The base process residue is
28.433 M odt / 526.006 PJ. Gross harvest residue is 9.069 M odt; recoverable
amounts are 3.174 / 5.441 / 6.802 M odt for 35/60/75%.

The RSYC 119 Mm³ base is 7.080 M odt / 130.981 PJ; the familiar 129.6709 PJ
figure is the RSYC 100 Mm³ reference, not the 119 Mm³ value. The harvest 60%
case is 5.441 M odt / 100.667 PJ, a ratio of **0.769** to the RSYC 119 Mm³ base
(`model_below_rsyc`). Only harvest recoverable rows are labelled
`rsyc_comparable=true`.

Facility residue v3 keeps process residue at 28.433 M odt / 526.006 PJ and
distributes it to 186 facilities with positive residue. Proxy-attributed
residue is 38.66% of process residue; scaled-proxy residue is 31.27%.
Cross-border residue is 1.087 M odt (3.82%); fallback residue is 1.081 M odt
(3.80%). HHI of process residue is 0.01547; top-5 share is 18.30%.

## 6. 100 / 119 / 150 Mm³ scenarios

All three documented provincial ceiling cases are fully placed with no
unassigned volume and no overflow. Global capacity does not bind; NT is the
only locally binding jurisdiction. The 150 Mm³ documented ceiling is a
separate case from the 1.0 and 1.2 m³/ha/yr operable-proxy intensity screens,
which are infeasible at 150 Mm³ (16.296 and 4.492 Mm³ deficits). The intensity
screens must not be used to relabel or reject the documented 150 Mm³ case.

| Scenario | Harvest Mm³ | Assigned Mm³ | Eligible capacity Mm³/yr | Utilization | Cross-border Mm³ | Proxy share | Facilities | Max facility share | HHI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 100.000 | 100.000 | 265.012 | 37.7% | 0.031 | 39.7% | 185 | 6.61% | 0.01354 |
| 119m | 118.977 | 118.977 | 273.468 | 43.5% | 0.036 | 39.9% | 208 | 6.80% | 0.01287 |
| 150m | 150.000 | 150.000 | 293.272 | 51.1% | 0.036 | 43.9% | 242 | 7.24% | 0.01350 |

### 6.1 Products and residue by scenario

| Scenario | Sawlog Mm³ | Pulp M odt | Panel Mm³ | Pellet M odt | Bio GWh | Process residue PJ | Harvest gross PJ | Harvest recoverable PJ | Total gross PJ | Total recoverable PJ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 40.736 | 6.380 | 4.459 | 3.991 | 710.4 | 442.11 | 141.02 | 100.35 | 583.12 | 542.46 |
| 119m | 48.467 | 7.590 | 5.305 | 4.748 | 845.2 | 526.01 | 167.78 | 119.40 | 693.78 | 645.40 |
| 150m | 61.104 | 9.569 | 6.688 | 5.986 | 1,065.6 | 663.16 | 211.53 | 150.53 | 874.68 | 813.69 |

Relative to the 119 Mm³ case, 100 Mm³ reduces assigned volume by 18.977 Mm³ and
total gross residue by 110.66 PJ; 150 Mm³ adds 31.023 Mm³ and 180.90 PJ. Product
and residue ordering is validated as 100 < 119 < 150 within each metric.

## 7. Base v6 local processing versus v5 trade sensitivity

The **v6 base** is the `province_capacity_floor` case: capacity v3, the 1.2×
provincial floor, local-first allocation, and cross-border only for the local
residual. The **v5 sensitivity** is the earlier unscaled
`published_plus_proxy` case. It is a trade-permissive counterfactual, not a
forecast and not evidence of provincial self-sufficiency.

| Metric | V5 `published_plus_proxy` | V6 `province_capacity_floor` | Delta v6−v5 |
|---|---:|---:|---:|
| Assigned | 118.977 Mm³ | 118.977 Mm³ | 0 |
| Local | 97.075 Mm³ (81.59%) | 118.941 Mm³ (99.97%) | +21.866 Mm³ |
| Cross-border | 21.902 Mm³ (18.41%) | 0.036 Mm³ (0.031%) | −21.866 Mm³ |
| Published | 78.658 Mm³ (66.11%) | 71.514 Mm³ (60.11%) | −7.144 Mm³ |
| Proxy | 40.319 Mm³ (33.89%) | 47.463 Mm³ (39.89%) | +7.144 Mm³ |
| Scaled proxy | 0 | 38.035 Mm³ | +38.035 Mm³ |
| Facilities used | 263 | 208 | −55 |
| Largest facility | 2.163 Mm³ (1.82%) | 8.093 Mm³ (6.80%) | +5.930 Mm³ |
| Top-5 share | 7.98% | 15.75% | +7.77 pp |
| HHI | 0.00755 | 0.01287 | +0.00532 |
| Capacity shortfall | 30.282 Mm³/yr | 43,651 m³/yr | −30.239 Mm³/yr |
| Provinces below floor | 5 | 1 | −4 |

The local-first floor changes the **distribution** of the same total process
residue, not its national total. At 119 Mm³, process residue shifts by province
(BC −4.037 M odt / −74.678 PJ; AB +3.545 M odt / +65.585 PJ; SK +0.572 M odt /
+10.584 PJ; MB +0.065 M odt / +1.202 PJ; ON +0.464 M odt / +8.582 PJ;
QC −0.611 M odt / −11.298 PJ). In the OSeCAN zone view, the 119 Mm³ process 50%
case moves B2 −34.246 PJ, A1 +21.112 PJ, A2 +11.681 PJ, and S1 +5.292 PJ
versus v5. Harvest residue is origin-province based and is unchanged by the
assignment floor.

The v5 sensitivity's 18.4% cross-border share is a useful upper-bound trade
counterfactual, but the v6 local-processing case is the frozen base because the
1.2× floor is the documented primary assumption for this release.

## 8. OSeCAN handoff v4

Handoff v4 is a read-only forestry-side screening handoff from residue curve
v3. It preserves the all-zone v2 proxy weights and refreshes the province/case
totals from the assignment-v6 residue chain. It maps to all 13 OSeCAN zones
with no `UNMAPPED` bucket.

| Case | National PJ | Tier / position |
|---|---:|---|
| Process 0% | 0.000 | T1 |
| Process 25% | 131.501 | T3 |
| Process 50% | 263.003 | above T4 |
| Process 75% | 394.504 | above T4 |
| Harvest 35% | 58.722 | T1 |
| Harvest 60% | 100.667 | T2 |
| Harvest 75% | 125.834 | T3 |

The adopted supply-curve reference points are T1 85.007 PJ, T2 109.399 PJ,
T3 149.273 PJ, and T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ
national and 43.524 PJ BC; these are context, not policy targets. At 119 Mm³,
process 25% is 0.860× the 2050 national context and process 50% is 1.720×;
harvest 60% is 0.658×. In BC, process 25% (45.680 PJ) is 1.050× the 43.524 PJ
BC zonal reference, process 50% is 2.099×, and harvest 60% is 0.777×.

The 119 Mm³ process 50% zone results include B2 83.792 PJ, Q1 37.620 PJ,
A1 34.402 PJ, O1 23.804 PJ, Q2 23.981 PJ, A2 19.034 PJ, T1 18.787 PJ, and
B1 7.568 PJ. The harvest 60% results include B2 31.024 PJ, Q1 13.783 PJ,
A1 12.469 PJ, O1 9.513 PJ, Q2 8.786 PJ, T2 7.552 PJ, and B1 2.802 PJ. All
zone rows carry `model_estimate=true`, `observed=false`, `policy_grade=false`,
and `official_osecan_changed=false`.

Handoff validation passed 20 checks with 0 failures, including row uniqueness,
zone coverage, province-weight sums, conservation to national totals, no
combined stream, explicit proxy labels, and an unchanged OSeCAN tree hash.
`forestry_osecan_scenarios_v3` provides the parallel 100/119/150 scenario
handoff; it is not an official OSeCAN result and does not apply candidate
activity limits.

## 9. Key results

- **119 Mm³ v6 base:** 118.977 Mm³ assigned; 99.97% local; 36,376 m³
  cross-border; 39.9% proxy; 208 facilities; HHI 0.01287; largest facility
  6.80%; no unassigned harvest; NT floor gap 43,651 m³.
- **v5 trade sensitivity:** 21.902 Mm³ cross-border (18.41%); 33.9% proxy;
  263 facilities; HHI 0.00755; 30.282 Mm³ capacity shortfall across 5
  provinces.
- **100/119/150 total gross residue:** 583.12 / 693.78 / 874.68 PJ; total
  recoverable 542.46 / 645.40 / 813.69 PJ.
- **119 Mm³ residue:** 28.433 M odt / 526.006 PJ process; 9.069 M odt /
  167.779 PJ gross harvest; 6.454 M odt / 119.397 PJ recoverable harvest.
- **RSYC check:** 0.769 of the RSYC 119 Mm³ base (`model_below_rsyc`), not a
  calibration.
- **OSeCAN handoff v4:** 119 process 50% = 263.003 PJ; harvest 60% =
  100.667 PJ; 2050 context = 152.923 PJ national / 43.524 PJ BC.
- **Capacity evidence:** 433 published records / 166.609 Mm³/yr; 241 proxy
  records / 104.733 Mm³/yr; 15 records without capacity.

## 10. Limitations and non-use

- **Proxy capacity is material.** Proxy capacity is 39.9% of assigned volume
  in the 119 Mm³ v6 base (47.463 of 118.977 Mm³) and 38.66% of process
  residue; scaled proxy is 31.27% of process residue. The 241 proxy records are
  class medians, not facility measurements. Scaled proxy capacity (38.035 Mm³
  in the v6 floor case) is not observed capacity and is not evidence of
  provincial self-sufficiency.
- **Published capacity is mixed-basis.** BC is a 2015 infeed proxy; Quebec is
  a 2026 permit ceiling; pellet-map rows are converted finished-pellet bulk
  volume; company rows are converted sawn-lumber output. The bases are not
  interchangeable. A permit does not prove operation, and no record is
  observed throughput.
- **Donor coefficients.** Only BC has species-resolved coefficients. The other
  12 jurisdictions use the BC aggregate donor; 35 coefficient rows are donor
  rows. Provincial species mixes differ, so sawlog/pulp/panel shares may be
  wrong in ways the national total does not reveal. Provinces with large
  hardwood shares may be over-assigned to sawlog. Product and residue values
  are not measured mill output.
- **Zone weights are proxies.** BC uses zonal supply dispatch; AB, ON, and QC
  use zonal energy dispatch activity, not harvest volume; the remaining
  jurisdictions use single-zone or aggregate membership. No observed
  sub-provincial residue polygon or split exists. Zone shares are frozen across
  all cases by design.
- **No transport cost.** The assignment uses a distance screen (1.3 road
  factor, 500 km cross-border penalty) but no $/t/km rate, road-network route,
  mode choice, backhaul, or transport objective. Cross-border volume is a
  residual allocation, not an interprovincial trade forecast.
- **No annual dynamics.** Each harvest case is a static snapshot. There is no
  forest growth, age-class transition, natural disturbance, capacity
  expansion/closure, facility entry/exit, investment, or 2021–2070 time path.
  The OSeCAN 2050 context is an external model reference, not a chain
  projection.
- **No economics.** No revenue, cost, profit, net value, stumpage, tenure, or
  capacity-expansion economics enter the chain. Do not report a benefit-cost or
  return result from this package.
- **No legal or operating determination.** The 100/119/150 Mm³ cases are
  planning allocations, not legal AACs, sustainable-supply determinations, or
  observed harvest outcomes. There is no legal THLB, tenure, or operating-status
  claim. Coordinates can be city centroids or province-level locations.
- **No policy-grade result.** Every row is `model_estimate`,
  `observed=false`, and `policy_grade=false`. OSeCAN official files are
  unchanged; the handoff is not an official OSeCAN result and candidate
  activity limits are not applied.
- **No public OSeCAN or UI change.** The freeze adds documentation only. It
  does not modify the public OSeCAN repository, the energy model, or any UI
  surface.

## 11. Reproduce and verify

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_inventory_v1
.venv/bin/python -m workflows.national.facilities.national_facility_capacity_v3
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v5
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v6
.venv/bin/python -m workflows.national.facilities.national_product_chain
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v3
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v3
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v3
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v4
.venv/bin/python -m workflows.national.facilities.forestry_osecan_scenarios_v3
.venv/bin/python -m workflows.national.facilities.national_processing_v5_v6_comparison
```

Focused regression tests:

```bash
.venv/bin/python -m pytest \
  tests/test_national_facility_inventory_v1.py \
  tests/test_national_facility_capacity_v3.py \
  tests/test_national_facility_assignment_v5.py \
  tests/test_national_facility_assignment_v6.py \
  tests/test_national_product_chain.py \
  tests/test_facility_residue_supply_v3.py \
  tests/test_national_facility_chain_scenarios_v3.py \
  tests/test_forestry_osecan_handoff_v4.py \
  tests/test_forestry_osecan_scenarios_v3.py \
  tests/test_national_processing_v5_v6_comparison.py -q
```

Every published artifact directory contains its report, JSON summary, and
SHA-256 manifest. The handoff manifest records the OSeCAN tree hash check and
the exact input hashes. Rebuild against data commit `6ca272e` to reproduce the
frozen numbers; later data commits may supersede the inputs.

## 12. Non-claims and references

**Non-claims.** This package does not claim: a legal AAC or allowable cut; a
sustainable harvest level; a legal THLB; current mill operating status; observed
mill capacity or throughput; an interprovincial trade forecast; a transport,
cost, revenue, or net-value result; a facility investment or closure plan; an
energy-policy recommendation; an official OSeCAN scenario; or a public UI
release.

**References.**

- `data/derived/national/facilities/national_facility_inventory_v1/NATIONAL_FACILITY_INVENTORY_V1.md`
- `data/derived/national/facilities/national_facility_capacity_v3/NATIONAL_FACILITY_CAPACITY_V3.md`
- `data/derived/national/facilities/national_facility_assignment_v6/NATIONAL_FACILITY_ASSIGNMENT_V6.md`
- `data/derived/national/facilities/national_product_chain_v1/NATIONAL_PRODUCT_CHAIN_V1.md`
- `data/derived/national/facilities/facility_residue_supply_v3/FACILITY_RESIDUE_SUPPLY_V3.md`
- `data/derived/national/facilities/national_residue_curve_v3/NATIONAL_RESIDUE_CURVE_V3.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v3/NATIONAL_FACILITY_CHAIN_SCENARIOS_V3.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v4/FORESTRY_OSECAN_HANDOFF_V4.md`
- `data/derived/national/facilities/forestry_osecan_scenarios_v3/FORESTRY_OSECAN_SCENARIOS_V3.md`
- `data/derived/national/facilities/national_processing_v5_v6_comparison/NATIONAL_PROCESSING_V5_V6_COMPARISON.md`

**Freeze record.** Annotated tag `national-facility-chain-v1` marks parent
commit `94c7501deb1924608e38df36e11f49728f93a1e0` and records data commit
`6ca272e358ab9f2ae8879bd47d673f96ff46c374`. The tag is the immutable reference
for the key result numbers in this model card. Documentation-only commits after
the tag do not change the frozen artifacts.
