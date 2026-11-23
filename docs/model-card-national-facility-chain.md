# Model card — National facility chain v5.1 (assignment v10 node transport-aware, all cases)

| Field | Value |
|---|---|
| Package | National facility chain v5.1 (frozen all-case assignment-v10 screening package) |
| Freeze tag (documentation) | `national-facility-chain-v5.1` |
| Date | 2026-11-23 |
| Parent commit at model freeze | recorded after the code/data commit (see freeze record) |
| Data commit | `0d3b2ff4c27f18bad2f93fd6c269c180c4dbcadc` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Stage artifacts | Inventory v1 → capacity v3 → assignment v10 (100/119/150) → product-chain v5 → facility residue v9 → residue curve v9 → chain scenarios v9 → OSeCAN handoff v10 → OSeCAN scenarios v9 |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Energy-side status | Read-only handoff; `official_osecan_changed=false`; transport cost is not in official tiers |
| Scope boundary | Documentation/tag freeze only; no code, model artifact, UI, or public OSeCAN change at the freeze commit; no legal/AAC claim; no data request, DUA, or approval dependency |

## Transport/siting companion card

Road factors and OSRM evidence, transport cost v1, supply-node construction and
assignment v10, and the separate greenfield k-median/break-even screen are
documented in `docs/model-card-national-transport-siting.md` (annotated tag
`national-transport-siting-v1`). That card records the transport/siting data
commits `16e979a` (assignment v10), `1f0a02a` (greenfield siting), `5b7d717`
(break-even), and `068787d` (case screen). The cross-reference changes no v5.1
number, artifact, or tag. The greenfield screen is not part of the chain, and
transport cost remains a chain summary rather than an OSeCAN input.

### Addendum and scope-decision pause

The transport/siting analysis is consolidated in
[`docs/addendum-national-transport-siting-v1.md`](addendum-national-transport-siting-v1.md)
(annotated tag `national-transport-siting-addendum-v1`). The addendum freezes
the v1 numbers, records the class-specific follow-up, and pauses further proxy
and siting iteration pending a C2070 scope decision. It changes no v5.1
number, artifact, or tag, and it adds no facility-chain result.

## v5.1 supersession summary

Version 5.1 extends assignment v10 to the 100 and 150 Mm³ sensitivity cases;
frozen v5 had solved v10 only at 119 Mm³. All three cases use the same 525
supply nodes, eligible capacity-v3 facilities, 1.2× floor and transport cost v1
curve. The 119 Mm³ results are invariant from v5. At 100 Mm³, truck transport
cost is 2.702 billion CAD/yr (versus 3.900 billion for v6 local-first, −30.73%)
and mean road distance is 255.33 km (versus 375.33 km, −31.97%). At 150 Mm³,
cost is 5.018 billion CAD/yr (versus 6.793 billion, −26.13%) and mean distance
is 319.79 km (versus 438.28 km, −27.04%). National products and residue are
invariant at each case: process residue 419.238 / 498.751 / 626.865 PJ and
harvest gross 373.244 / 444.411 / 573.125 PJ. Facility process-residue HHI
falls from 0.032562 to 0.023893 at 100 Mm³ and from 0.028928 to 0.019132 at
150 Mm³. OSeCAN handoff v10 adds explicit 100/119/150 case zone summaries; all
21 tier positions are unchanged and transport cost is not an OSeCAN input. No
UI or official OSeCAN change is made.

## v5 supersession summary

Version 5 keeps the v4 E1 measured + E2c density-calibrated product chain and
replaces the 119 Mm³ facility distribution with assignment v10 node-to-facility
transport-aware flows. At 119 Mm³, assigned volume is still 118.977 Mm³ and
national products/residue are invariant (process residue 498.751 PJ; harvest
gross 444.411 PJ; recoverable 60% 266.646 PJ). Assignment v10 uses 296
facilities rather than 208, raises cross-border volume from 0.036 to 2.325 Mm³
and lowers modelled truck transport cost from 4.958 to 3.392 billion CAD/yr
(−31.59%). Assigned-volume-weighted mean road distance falls from 402.12 to
270.29 km; the ≥150 km share falls from 83.83% to 48.62% and the ≥300 km share
from 52.65% to 30.16%. Facility process-residue concentration falls (HHI
0.028873→0.023211, top-5 29.51%→24.67%) but proxy residue share rises
(37.78%→56.60%). OSeCAN national tiers are unchanged at all 21
scenario/stream/case positions; process-residue PJ shifts between zones
because facility provinces change (process 50%: Q1 −5.505, Q2 −3.509, L1
+6.708, T1 +2.634 PJ). Transport cost remains a separate chain summary and is
not part of official OSeCAN tiers. V10 is solved at the 119 Mm³ reference;
100 and 150 Mm³ remain assignment-v6 local-first sensitivity cases.

## 1. Purpose

The national facility chain is a reproducible screening chain that connects a
documented national harvest case to a bounded set of Canadian forest-product
facilities, then converts assigned feedstock into products and process/harvest
residue. Version 4 keeps the frozen v3.2 inventory v1 / capacity v3 /
assignment v6 provincial-floor allocation and replaces the nine E2 v1 product
coefficients with E2c density-calibrated coefficients. BC, QC, NB, and YT
retain E1 measured coefficients. All 13 jurisdictions therefore use a
province-specific E1 measured or E2c product and total-residue coefficient:
**zero donor provincial product coefficients are applied**.

Within-class SAW/PANEL splits, other-roundwood routing, pellet/bio splits,
recovery fractions, and mill conversion ratios remain donor assumptions and
are labelled as such. Version 4 is designed to answer four limited questions:

1. Can a 100/119/150 Mm³ national harvest case be placed on the facility
   inventory without an unbounded sink, and with what local/cross-border split?
2. How much of that placement relies on published capacity versus a
   class-median proxy?
3. What products and residue follow when every province has an explicit E1
   measured or E2c density-calibrated coefficient, and how large is total
   harvest residue under alternative recovery scenarios?
4. Can those residue streams be handed to the 13 OSeCAN zones as a read-only,
   traceable sensitivity, without changing the official energy model?

It is not a mill-by-mill operating plan, a timber-supply forecast, an
interprovincial trade forecast, a policy evaluation, or an investment case.

**Baseline correction: v3.2 was E2 v1; v4 is E2c; E2 v2 was not integrated.**
The frozen v3.2 `national_product_chain_v4` reads
`open_data_imputation_v1/product_coefficients.csv`, which is the E2 v1
coefficient set. E2 v2 was built as open-inventory-informed tree lists but was
never integrated into a facility chain. The v4 chain uses
`open_data_imputation_v3` (E2c), which keeps E2 v2 geometry, donor
DBH/height draws, donor tapers, and bucking unchanged and recalibrates only the
stems/ha expansion. A coefficient check confirms E2 v2 and E2c share identical
per-m³ solid/pulp/other shares and stem/branch-foliage/total residue
coefficients: all 54 coefficient rows are unchanged (`changed=false`). Density
calibration is therefore an extensive change (expanded reference volume), not
an intensive per-m³ change. Calling v3.2 an “E2 v2” baseline would be wrong.

## 2. Chain and scope

| Stage | Artifact | What it does | Key output |
|---|---|---|---|
| Inventory | `national_facility_inventory_v1` | Adds the 2025 Canadian Biomass pellet-map producers to inventory v0 and dedupes them | 519 records; 212 with published capacity |
| Capacity | `national_facility_capacity_v3` | Adds Quebec MRNF 2026 permitted volumes, five company disclosures, and retains class-median proxies | 689 records; 271.342 Mm³/yr total capacity |
| Assignment | `national_facility_assignment_v6` | Local-first, finite-capacity allocation of the 119 Mm³ case on capacity v3 with the 1.2× provincial floor as primary | 208 facilities; 118.977 Mm³ assigned |
| Product chain | `national_product_chain_v5` | Applies E1 measured BC/QC/NB/YT and E2c density-calibrated AB/SK/MB/ON/NS/PE/NL/NT/NU product shares and total residue | 37.674 Mm³ solid product; 498.751 PJ process residue; 444.411 PJ gross harvest residue |
| Facility residue | `facility_residue_supply_v7` | Assigns process residue to facilities; keeps harvest residue origin-province | 26.960 M odt process residue; 498.751 PJ |
| Residue curve | `national_residue_curve_v7` | Converts process/harvest residue to 0/25/50/75% and 35/60/75% cases at 18.5 GJ/ODT | National and provincial PJ curves |
| Scenarios | `national_facility_chain_scenarios_v7` | Re-runs 100/119/150 Mm³ on the capacity-v3 floor and applies the E1/E2c product/residue coefficients | Full scenario chain |
| OSeCAN handoff | `forestry_osecan_handoff_v8` | Allocates province/case residue to all 13 OSeCAN zones | 119 handoff rows; 91 zone/case summary rows |
| OSeCAN scenarios | `forestry_osecan_scenarios_v7` | Carries all three harvest cases and seven availability cases to the 13 zones | 357 residue rows; 273 zone summary rows |

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

### 3.3 E1 measured and E2c density-calibrated coefficients

Version 4 uses two evidence tiers for provincial product and residue
coefficients. Every jurisdiction has one of them; no province uses a donor
product coefficient.

- **E1 measured (BC, QC, NB, YT).** `four_province_bucking_pilot_v0` measures
  **591 stems**: BC 347, QC 85, NB 106, and YT 53. Expansion-weighted
  recovered-log shares yield province-specific solid (sawlog), pulp
  (pulpwood), other-roundwood, and gross total harvest-residue coefficients.
- **E2c density-calibrated open-data imputation (AB, SK, MB, ON, NS, PE, NL,
  NT, NU).** The `open_data_imputation_v3` tree lists contain **324 stems**, 36
  per province, built from SCANFI v2 province species composition,
  deterministic weighted donor DBH/height draws from the measured
  BC/QC/NB/YT tree lists, and labelled BC Kozak or QC Schneider donor tapers.
  The E2c tier is `E2c_open_inventory_density_calibrated`; every E2c row is
  `measured=false`, `imputed=true`, `synthetic=false`, and
  `density_calibrated=true`.
- **Density expansion.** SK, NS, and PE use open-inventory stem density; MB
  uses a donor median plot density; AB, ON, NL, NT, and NU retain a labelled
  1,000 stems/ha screening proxy. Density changes stems/ha and the expanded
  reference volume only. It does not change per-m³ product shares, residue
  coefficients, donor tapers, or bucking.
- **Zero donor product coefficients.** The `national_product_chain_v5`
  coefficient table has solid, pulp, other, and total harvest-residue values
  for all 13 provinces from either E1 or E2c. The donor coefficient column is
  retained only as an audit comparison; it is not applied.
- **Retained donor splits, explicitly labelled.** The within-solid SAW/PANEL
  split, the other-roundwood routing to PELLET/BIO (the facility chain has no
  OTHER class), the pellet/bio split, the harvest recovery fraction, and all
  mill conversion ratios remain donor assumptions. They are labelled
  `donor_within_class_split` or `donor_conversion` in the product-chain rows.
- **Total residue for both tiers.** E1 stems use measured stem, branch, and
  foliage components. E2c stems use stem taper residue plus CFS branch and
  foliage equations with explicit species/group proxies. Branch and foliage
  are dry mass; they are never added to stem taper volume.
- **PANEL in BC.** PANEL is absent from BC Stage A; its allocation is the
  explicit BC panel nameplate share of sawlog plus panel nameplate, labelled as
  a BC anchor.

E2c density evidence:

| Province | Density stems/ha | Density statistic | Evidence class | Expanded volume m³/ha |
|---|---:|---|---|---:|
| SK | 2,033.65 | `area_weighted_median` | `open_inventory` | 88.692 |
| NS | 1,409.82 | `area_weighted_median` | `open_inventory` | 152.342 |
| PE | 802.84 | `area_weighted_median` | `open_inventory` | 116.794 |
| MB | 2,050.00 | `donor_median_plot_density` | `donor` | 251.565 |
| AB | 1,000.00 | `proxy_default` | `proxy` | 409.088 |
| ON | 1,000.00 | `proxy_default` | `proxy` | 122.913 |
| NL | 1,000.00 | `proxy_default` | `proxy` | 178.398 |
| NT | 1,000.00 | `proxy_default` | `proxy` | 146.149 |
| NU | 1,000.00 | `proxy_default` | `proxy` | 102.728 |

### 3.4 Branch and foliage

Branch and foliage dry mass is computed for every E1 and E2c stem from the
vendored CFS Biomass and Nutrients tree-level DBH + height equations (Lambert,
Ung and Raulier 2005; Ung, Bernier and Guo 2008; Open Government Licence –
Canada):

```
Biomass_kg = a * DBH_cm^b * Height_m^c
```

The equations are applied only to live standing stems. Documented proxies
include mountain hemlock (conifer aggregate), birch (Betula aggregate), and
black spruce for YT spruce. Branch and foliage are model estimates, not weighed
components. They add dry mass and energy but no measured volume, so the m³
feedstock routing remains the stem taper residue; products and m³ process
residue are driven by the E1/E2c product shares and class conversion ratios.

### 3.5 OSeCAN zone context

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

### 3.6 TSA24 sort validation status (not a pass)

The package includes a TSA24 (Prince George) industrial-sort check against
TIPSY industrial-log curves. It is a diagnostic, not a validation pass and not
an official grade check.

| Check | Value |
|---|---|
| Sample frame | 558 plots |
| Comparable managed-AU plots | 341 of 558 (61.1%); 15 additional species-fallback plots |
| Mean per-plot TVD | 0.505 |
| Median per-plot TVD | 0.480 |
| Volume-weighted per-plot TVD | 0.491 |
| Pooled-share TVD | 0.433 |
| Pooled overlap | 0.567 |
| `pass_fail` | `not_assessed_no_pre_registered_tolerance` |
| `grade_status` | `not_assigned` |
| `official_grade_validation` | `false` |
| Pre-registered tolerance | none |

No tolerance was pre-registered, so no pass/fail conclusion is drawn. The
check compares a dimension-only, value-maximising bucker with TIPSY model
output; it carries no defect, sweep, knot, rot, or product-quality information.
Multiple PSP plots share a managed-AU curve, so per-plot errors are not
independent. The optional `tipsy45` profile does not close the gap (pooled TVD
0.4404, mean per-plot TVD 0.5354). Do not describe the chain as
“grade-validated” or “sort-validated”.

## 4. Facility inventory, capacity, and assignment

### 4.1 Assignment rule

- 13 jurisdictions; NU has no modelled harvest in the 119 Mm³ case.
- Relevant facility classes are SAW, PULP, PANEL, PELLET, and BIO.
- Every assignment row carries a finite capacity. There is no unbounded sink.
- Allocation is **local first**: a province's eligible local facilities are
  exhausted before any cross-border volume is considered.
- Distance is screened with a 1.3 road factor and a 500 km cross-border
  penalty. This is a locational screen, not a transport-cost model.
- The primary case applies a **1.2× provincial floor**. Where published plus
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
The published capacity is unchanged by the floor. Assignment is identical to
v3.2; v4 changes only the downstream product and residue coefficients.

## 5. Product and residue chain

Product-chain v5 applies E1 measured coefficients in BC/QC/NB/YT and E2c
density-calibrated coefficients in the nine other jurisdictions. All
quantities are model estimates, not measured mill output or a facility survey.

### 5.1 119 Mm³ national chain, v3.2 (E2 v1) → v4 (E2c)

Assignment and harvest are identical across the two chains, so every delta is
a coefficient effect. E1 provinces are unchanged.

| Metric | v3.2 (E2 v1) | v4 (E2c) | Delta | Delta % |
|---|---:|---:|---:|---:|
| Sawlog product Mm³ | 38.119 | 33.957 | −4.162 | −10.92% |
| Panel product Mm³ | 4.172 | 3.717 | −0.456 | −10.92% |
| **Solid product Mm³** | **42.291** | **37.674** | **−4.617** | **−10.92%** |
| Pulp product M odt | 10.824 | 12.066 | +1.242 | +11.47% |
| Pellet product M odt | 4.356 | 5.230 | +0.874 | +20.06% |
| Bio product GWh | 775.4 | 930.9 | +155.5 | +20.06% |
| Process residue PJ | 505.333 | 498.751 | −6.582 | −1.30% |
| Harvest gross PJ | 390.418 | 444.411 | +53.993 | +13.83% |
| Harvest recoverable PJ (60%) | 234.251 | 266.646 | +32.396 | +13.83% |
| Total gross PJ | 895.751 | 943.162 | +47.411 | +5.29% |
| Total recoverable PJ (60% harvest) | 739.584 | 765.397 | +25.814 | +3.49% |

The solid decrease and pulp increase reflect the E2c province shares, not the
density expansion. Harvest residue rises because the E2c total-residue
coefficients differ from E2 v1; process residue falls slightly because the
class mix changes while class residue ratios stay donor assumptions.

### 5.2 E1/E2c coefficient table (119 Mm³)

| Province | Tier | Solid | Pulp | Other | Stem ODT/m³ | Branch+foliage ODT/m³ | Total ODT/m³ |
|---|---|---:|---:|---:|---:|---:|---:|
| BC | E1 measured | 0.6953 | 0.2948 | 0.0100 | 0.040577 | 0.121045 | 0.161622 |
| AB | E2c | 0.4077 | 0.5846 | 0.0077 | 0.014555 | 0.145210 | 0.159765 |
| SK | E2c | 0.0000 | 0.6432 | 0.3568 | 0.349895 | 0.285004 | 0.634899 |
| MB | E2c | 0.3919 | 0.5785 | 0.0296 | 0.084668 | 0.109293 | 0.193961 |
| ON | E2c | 0.4529 | 0.5076 | 0.0396 | 0.084967 | 0.125217 | 0.210184 |
| QC | E1 measured | 0.3420 | 0.6417 | 0.0164 | 0.059410 | 0.158097 | 0.217507 |
| NB | E1 measured | 0.5238 | 0.4672 | 0.0090 | 0.030341 | 0.157085 | 0.187426 |
| NS | E2c | 0.2165 | 0.7695 | 0.0140 | 0.123222 | 0.173239 | 0.296461 |
| PE | E2c | 0.2539 | 0.7274 | 0.0188 | 0.064730 | 0.173242 | 0.237971 |
| NL | E2c | 0.5103 | 0.4873 | 0.0024 | 0.065918 | 0.154253 | 0.220171 |
| YT | E1 measured | 0.4246 | 0.5625 | 0.0129 | 0.123046 | 0.146642 | 0.269689 |
| NT | E2c | 0.6242 | 0.3589 | 0.0169 | 0.072550 | 0.104472 | 0.177021 |
| NU | E2c | 0.7051 | 0.2816 | 0.0132 | 0.139130 | 0.137916 | 0.277046 |

The prior donor values are retained in
`e1_e2_coefficient_replacement.csv` and `donor_vs_e2_coefficients.csv` as audit
columns. They are not applied to any chain row. NU has no modelled harvest in
the 119 Mm³ case, so its coefficients do not affect the national totals.

### 5.3 Provincial process residue (119 Mm³, by origin)

| Province | Process PJ | Harvest gross PJ | Harvest 0.60 PJ |
|---|---:|---:|---:|
| BC | 174.450 | 119.536 | 71.722 |
| AB | 94.397 | 67.659 | 40.595 |
| SK | 14.660 | 51.031 | 30.619 |
| MB | 4.726 | 4.074 | 2.444 |
| ON | 56.406 | 52.469 | 31.481 |
| QC | 110.033 | 107.334 | 64.401 |
| NB | 26.371 | 21.618 | 12.971 |
| NS | 9.896 | 13.098 | 7.859 |
| PE | 1.227 | 1.330 | 0.798 |
| NL | 6.238 | 5.923 | 3.554 |
| YT | 0.189 | 0.219 | 0.131 |
| NT | 0.158 | 0.119 | 0.071 |
| NU | 0.000 | 0.000 | 0.000 |

Provincial process residue is shown at 100% availability. The national process
total is 498.751 PJ; the harvest totals are gross residue and the uniform 0.60
recoverable value. Process and harvest columns are separate streams.

### 5.4 Residue curve v7 and facility residue v7

At 18.5 GJ/ODT, the national process-availability cases are **0 / 124.688 /
249.375 / 374.063 PJ** for 0/25/50/75%. The national harvest recovery cases are
**155.544 / 266.646 / 333.308 PJ** for 35/60/75%. Gross harvest residue is
24.022 M odt / 444.411 PJ; recoverable amounts are **8.408 / 14.413 / 18.017 M
odt** for 35/60/75% (155.544 / 266.646 / 333.308 PJ). The base process residue
is 26.960 M odt / 498.751 PJ.

The RSYC 119 Mm³ base is 7.080 M odt / 130.981 PJ; the familiar 129.6709 PJ
figure is the RSYC 100 Mm³ reference, not the 119 Mm³ value. The harvest 60%
case is 14.413 M odt / 266.646 PJ, a ratio of **2.0358** to the RSYC 119 Mm³
base (`model_above_rsyc`). The 35% and 75% cases bracket it at 1.1875 and
2.5447. Only harvest recoverable rows are labelled `rsyc_comparable=true`.
The ratio increase reflects E2c branch/foliage mass and the E2c total-residue
coefficients, not calibration.

Facility residue v7 keeps process residue at 26.960 M odt / 498.751 PJ and
distributes it to 208 facilities receiving class-matched feedstock; 186 have
positive residue. Proxy-attributed residue is 37.78% of process residue;
scaled-proxy residue is 29.87%. Published-capacity residue is 62.22%. National
class-fallback residue is 5.20% (1.402 M odt); cross-border process residue is
5.22% (1.407 M odt). HHI of process residue is 0.028873 (effective facility
count 34.6); top-1 share is 11.27% and top-5 share is 29.51%. Process residue
per m³ feedstock is 0.204419 odt/m³.

### 5.5 Open-volume checks

| Province | Density stems/ha | Expanded volume m³/ha | Open inventory m³/ha | Ratio | Status |
|---|---:|---:|---:|---:|---|
| SK | 2,033.65 | 88.692 | 100.126 | 0.8858 | within_50pct |
| NS | 1,409.82 | 152.342 | 105.239 | 1.4476 | within_50pct |
| PE | 802.84 | 116.794 | 170.824 | 0.6837 | within_50pct |
| AB | 1,000.00 | 409.088 | — | — | not_available |
| MB | 2,050.00 | 251.565 | — | — | not_available |
| ON | 1,000.00 | 122.913 | — | — | not_available |
| NL | 1,000.00 | 178.398 | — | — | not_available |
| NT | 1,000.00 | 146.149 | — | — | not_available |
| NU | 1,000.00 | 102.728 | — | — | not_available |

SK, NS, and PE pass the ±50% screening check. AB, ON, NL, NT, and NU have no
open volume field; `not_available` is not a pass. MB uses donor median plot
density, which is cross-jurisdiction donor evidence, not Manitoba inventory
density; its open-volume status is also `not_available`. AB AVI publishes
density classes, not numeric stems/ha.

## 6. 100 / 119 / 150 Mm³ scenarios

All three documented provincial ceiling cases are fully placed with no
unassigned volume and no overflow. Global capacity does not bind; NT is the
only locally binding jurisdiction. The 150 Mm³ documented ceiling is a
separate case from the 1.0 and 1.2 m³/ha/yr operable-proxy intensity screens,
which are infeasible at 150 Mm³ (16.296 and 4.492 Mm³ deficits). The intensity
screens must not be used to relabel or reject the documented 150 Mm³ case.

### 6.1 Assignment metrics

| Scenario | Harvest Mm³ | Assigned Mm³ | Eligible capacity Mm³/yr | Utilization | Cross-border Mm³ | Proxy share | Facilities | Max facility share | HHI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 100.000 | 100.000 | 265.012 | 37.7% | 0.031 | 39.7% | 185 | 6.61% | 0.01354 |
| 119m | 118.977 | 118.977 | 273.468 | 43.5% | 0.036 | 39.9% | 208 | 6.80% | 0.01287 |
| 150m | 150.000 | 150.000 | 293.272 | 51.1% | 0.036 | 43.9% | 242 | 7.24% | 0.01350 |

### 6.2 Products and residue by scenario

| Scenario | Sawlog Mm³ | Pulp M odt | Panel Mm³ | Pellet M odt | Bio GWh | Process residue PJ | Harvest gross PJ | Harvest recoverable PJ | Total gross PJ | Total recoverable PJ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 28.566 | 10.137 | 3.127 | 4.372 | 778.3 | 419.238 | 373.244 | 223.946 | 792.482 | 643.184 |
| 119m | 33.957 | 12.066 | 3.717 | 5.230 | 930.9 | 498.751 | 444.411 | 266.646 | 943.162 | 765.397 |
| 150m | 41.585 | 15.551 | 4.552 | 7.039 | 1,253.0 | 626.865 | 573.125 | 343.875 | 1,199.991 | 970.740 |

Relative to the 119 Mm³ case, 100 Mm³ reduces assigned volume by 18.977 Mm³ and
total gross residue by 150.68 PJ; 150 Mm³ adds 31.023 Mm³ and 256.83 PJ.
Product and residue ordering is validated as 100 < 119 < 150 within each
metric. These are planning allocations, not legal harvest levels.

### 6.3 E2 v2 versus E2c coefficient equality

The `e2_v2_vs_e2c_coefficients.csv` comparison covers the nine E2c provinces ×
six coefficients (54 rows). All 54 rows are unchanged: solid, pulp, and other
shares, stem residue, branch+foliage residue, and total residue are identical
to machine precision (`changed=false`). E2 v2 was built but never integrated
into a facility chain. The v3.2 baseline is E2 v1; the v4 chain is E2c. The
density calibration is extensive and appears in the expanded reference volume
and open-volume checks, not in the per-m³ coefficients.

## 7. Base v6 local processing versus v5 trade sensitivity

The **v6 base** is the `province_capacity_floor` case: capacity v3, the 1.2×
provincial floor, local-first allocation, and cross-border only for the local
residual. The **v5 sensitivity** is the earlier unscaled
`published_plus_proxy` case. It is a trade-permissive counterfactual, not a
forecast and not evidence of provincial self-sufficiency. The trade sensitivity
was built on the v2 donor-product chain and is not rebuilt on E2c in v4; its
national products and residue therefore do not match the v4 base.

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
residue, not its national total. The v5 sensitivity's 18.4% cross-border share
remains a useful upper-bound trade counterfactual, but the v6 local-processing
case is the frozen base because the 1.2× floor is the documented primary
assumption for this release. The v4 E1/E2c product chain applies to the v6
assignment base. A separate screening `facility_residue_supply_v7_trade`
rebuilds the assignment-v5 trade placement with E2c coefficients for the
sensitivity v2 design; it is not part of the frozen v4 base and must not be
mixed with v4 totals. The older v2.1 trade and sensitivity addenda retain v2
donor product coefficients and must not be mixed with v4 totals.

## 8. OSeCAN handoff v8 and scenarios v7

Handoff v8 is a read-only forestry-side screening handoff from residue curve
v7. It preserves the all-zone v2 proxy weights and refreshes the province/case
totals from the E1/E2c chain. It maps to all 13 OSeCAN zones with no `UNMAPPED`
bucket and 119 handoff rows.

| Case | National PJ | Tier / position |
|---|---:|---|
| Process 0% | 0.000 | T1 |
| Process 25% | 124.688 | T3 |
| Process 50% | 249.375 | above_T4 |
| Process 75% | 374.063 | above_T4 |
| Harvest 35% | 155.544 | T4 |
| Harvest 60% | 266.646 | above_T4 |
| Harvest 75% | 333.308 | above_T4 |

The adopted supply-curve reference points are T1 85.007 PJ, T2 109.399 PJ,
T3 149.273 PJ, and T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ
national and 43.524 PJ BC; these are context, not policy targets.

**Tier shifts versus v3.2.** The harvest 35% case moves from T3 to T4. Harvest
60% and 75% remain above T4; all process tiers are unchanged:

| Stream | Case | v3.2 (E2 v1) | v4 (E2c) | Changed |
|---|---|---|---|---|
| Harvest | 35% | T3 | T4 | yes |
| Harvest | 60% | above_T4 | above_T4 | no |
| Harvest | 75% | above_T4 | above_T4 | no |
| Process | 0% / 25% / 50% / 75% | T1 / T3 / above_T4 / above_T4 | T1 / T3 / above_T4 / above_T4 | no |

The 119 Mm³ process 50% zone results include B2 83.191 PJ, Q1 35.553 PJ,
A1 32.714 PJ, O1 23.383 PJ, Q2 22.663 PJ, T1 18.516 PJ, A2 18.101 PJ,
B1 7.514 PJ, O2 4.680 PJ, L1 1.871 PJ, M1 1.044 PJ, S1 0.107 PJ, and
N1 0.040 PJ. The harvest 60% results include B2 65.780 PJ, Q1 39.330 PJ,
S1 30.619 PJ, O1 26.231 PJ, A1 26.135 PJ, Q2 25.071 PJ, T1 21.628 PJ,
A2 14.460 PJ, B1 5.941 PJ, O2 5.250 PJ, L1 3.554 PJ, M1 2.444 PJ, and
N1 0.203 PJ. All zone rows carry `model_estimate=true`, `observed=false`,
`policy_grade=false`, and `official_osecan_changed=false`.

Scenarios v7 carries all three harvests and all seven cases to the 13 zones.
Zone weights remain the v2 proxy weights: BC 2050 zonal supply dispatch,
AB/ON/QC 2050 zonal dispatch activity, single-zone/aggregate membership
elsewhere. Every multi-zone weight is `proxy_share=true`.

Handoff validation passed 20 checks with 0 failures, including row uniqueness,
zone coverage, province-weight sums, conservation to national totals, no
combined stream, explicit proxy labels, and an unchanged OSeCAN tree hash
(`80caa91c…`, 146 files before and after). Scenario v7 validation also passed
with 0 failures.

## 9. Key results

- **119 Mm³ v6 base:** 118.977 Mm³ assigned; 99.97% local; 36,376 m³
  cross-border; 39.9% proxy; 208 facilities; HHI 0.01287; largest facility
  6.80%; no unassigned harvest; NT floor gap 43,651 m³.
- **E1/E2c coverage:** 13 of 13 jurisdictions have a province-specific product
  and total-residue coefficient. E1 measured covers BC 347, QC 85, NB 106, and
  YT 53 stems (591 total); E2c density-calibrated imputation covers
  AB/SK/MB/ON/NS/PE/NL/NT/NU with 324 stems (36 each). Zero donor provincial
  product coefficients. SK/NS/PE use open density; MB donor density; five
  jurisdictions retain the 1,000 stems/ha proxy.
- **119 Mm³ products:** 37.674 Mm³ solid (sawlog 33.957 + panel 3.717),
  12.066 M odt pulp, 5.230 M odt pellet, 930.946 GWh bio.
- **119 Mm³ residue:** 26.960 M odt / 498.751 PJ process; 24.022 M odt /
  444.411 PJ gross harvest; 14.413 M odt / 266.646 PJ harvest at 0.60;
  50.982 M odt / 943.162 PJ total gross; 41.373 M odt / 765.397 PJ total
  recoverable (process + harvest recoverable).
- **Change versus v3.2 (E2 v1):** solid −10.92%, pulp +11.47%, process
  −1.30%, harvest gross +13.83%, harvest 0.60 +13.83%, total gross +5.29%,
  total recoverable +3.49%.
- **Recovery scenarios:** harvest 35/60/75% = 155.544 / 266.646 / 333.308 PJ;
  process 0/25/50/75% = 0 / 124.688 / 249.375 / 374.063 PJ; no recovery factor
  is calibrated, and 0.60 is the reference scenario only.
- **100/119/150 total gross residue:** 792.482 / 943.162 / 1,199.991 PJ; total
  recoverable 643.184 / 765.397 / 970.740 PJ.
- **RSYC check:** 60% harvest case is 2.0358 of the RSYC 119 Mm³ base
  (`model_above_rsyc`), not a calibration.
- **OSeCAN handoff v8:** 119 process 50% = 249.375 PJ; harvest 60% =
  266.646 PJ; harvest 35% moves T3 → T4; harvest 60/75% and all process tiers
  are unchanged; 2050 context = 152.923 PJ national / 43.524 PJ BC.
- **Capacity evidence:** 433 published records / 166.609 Mm³/yr; 241 proxy
  records / 104.733 Mm³/yr; 15 records without capacity.

## 10. Limitations and non-use

- **Nine jurisdictions are E2c, not measured.** AB, SK, MB, ON, NS, PE, NL, NT,
  and NU use 36-stem open-data imputation lists built from SCANFI composition,
  donor DBH/height geometry, and donor tapers. They are not province-specific
  measured tree lists and not observed mill outcomes. SK/NS/PE have open
  density evidence, MB uses donor median density, and AB/ON/NL/NT/NU retain a
  1,000 stems/ha proxy. The proxy is an assumption, not a measurement.
- **Density calibration is extensive.** It changes stems/ha and the expanded
  reference volume; it does not change per-m³ product or residue coefficients.
  E2 v2 and E2c per-m³ coefficients are identical. E2 v2 was never integrated
  into a facility chain; v3.2 is E2 v1, not E2 v2.
- **Open-volume checks are screening, not validation.** SK (0.8858), NS
  (1.4476), and PE (0.6837) pass the ±50% check against open inventory volume.
  AB, MB, ON, NL, NT, and NU return `not_available`; `not_available` is not a
  pass. MB's density is donor evidence.
- **E2c donor geometry and tapers; 11 of 13 jurisdictions use a donor taper.**
  The E2c lists draw DBH/height from the measured BC/QC/NB/YT stems and use BC
  Kozak or QC Schneider donor tapers. NB uses the QC Schneider donor and YT
  uses the BC Kozak donor, so only BC and QC use a local taper. Species without
  a mapped taper and unsupported hardwoods are excluded or proxied; they are
  not imputed as observations.
- **E1 taper coverage is partial.** The taper-supported share of the eligible
  measured frame is 100.0% for BC (347 trees), 74.4% for QC (85 trees), 37.0%
  for NB (106 trees), and 98.9% for YT (53 trees). BC, NB, and YT residual proxy
  species remain labelled proxies. The E1 coefficients cover only the
  taper-supported measured frame, not all provincial species or all hardwood
  volume.
- **Branch and foliage are model estimates.** They use published CFS equations
  with documented proxies (mountain hemlock, birch, black spruce for YT spruce),
  not weighed components. They add dry mass and energy but no measured volume.
- **Sort recovery is reported, not validated.** The TSA24 check has 341
  comparable managed-AU plots out of 558, mean per-plot TVD 0.505 and pooled TVD
  0.433. No tolerance was pre-registered
  (`pass_fail=not_assessed_no_pre_registered_tolerance`); grade is
  `not_assigned` and `official_grade_validation=false`. The comparison is
  dimension-only against TIPSY model output and is not an official BC log-grade
  validation. See §3.6.
- **Donor splits remain material.** Solid/pulp/other are E1 or E2c, but the
  within-solid SAW/PANEL split, other-roundwood routing to PELLET/BIO, the
  pellet/bio split, and all class conversion ratios remain donor assumptions.
  The measured and E2c pilots do not measure panel logs separately. These rows
  carry explicit donor labels.
- **No chosen recovery factor.** Gross total residue is reported with
  0.35/0.60/0.75 recoverable scenarios. The 0.60 value is the existing
  reference scenario, not a calibrated, observed, or policy recovery factor.
  The product-chain recoverable field and the uniform 0.60 case coincide
  because all provinces carry total residue; neither is a preferred answer.
- **Class-median and floor-scaled capacity are material.** Proxy capacity is
  39.9% of assigned volume in the 119 Mm³ v6 base (47.463 of 118.977 Mm³) and
  37.78% of process residue; scaled proxy is 29.87% of process residue. The 241
  proxy records are class medians, not facility measurements. Scaled proxy
  capacity (38.035 Mm³ in the v6 floor case) is not observed capacity and is
  not evidence of provincial self-sufficiency.
- **Published capacity is mixed-basis.** BC is a 2015 infeed proxy; Quebec is
  a 2026 permit ceiling; pellet-map rows are converted finished-pellet bulk
  volume; company rows are converted sawn-lumber output. The bases are not
  interchangeable. A permit does not prove operation, and no record is
  observed throughput.
- **OSeCAN zone weights are proxies.** BC uses zonal supply dispatch; AB, ON,
  and QC use zonal energy dispatch activity, not harvest volume; the remaining
  jurisdictions use single-zone or aggregate membership. No observed
  sub-provincial residue polygon or split exists. Zone shares are frozen across
  all cases by design.
- **NU and NT are special cases.** NU has zero modelled harvest in the
  119 Mm³ case. NT has no eligible local capacity, so its 36,376 m³ is placed
  cross-border; the separate 43,651 m³ NT floor gap is a 1.2× buffer, not
  unassigned harvest.
- **Transport cost is a screening layer, not an observed or net cost.** V5.1
  assignment v10 prices node-to-facility truck flows with transport cost v1
  (52.5 m³ payload, empty return) and haversine × province-road-factor
  distances, with a 500 km cross-border soft penalty. It is not an observed
  route, mode choice, backhaul, border/ferry/winter-road, or delivered-cost
  model. Cross-border volume is a soft-penalty allocation, not an
  interprovincial trade forecast. Transport cost is a chain summary and not an
  OSeCAN input. The greenfield siting screen is separate and changes no chain
  total; see `docs/model-card-national-transport-siting.md`.
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
- **No data-request dependency.** The chain is complete on open data. No DUA,
  data request, or approval is required. Optional requests could only improve
  evidence tiers; they do not unblock the model.
- **No public OSeCAN or UI change.** The freeze adds documentation and a tag
  only. It does not modify the public OSeCAN repository, the energy model, or
  any UI surface.

## 11. Reproduce and verify

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m workflows.national.facilities.e1_e2c_coefficients
.venv/bin/python -m workflows.national.facilities.national_product_chain_v5
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v7
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v7
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v7
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v8
.venv/bin/python -m workflows.national.facilities.forestry_osecan_scenarios_v7
```

The upstream inventory v1, capacity v3, and assignment v6 artifacts are frozen
inputs and are not rebuilt by the v4 chain. Evidence inputs for the coefficient
tiers are in `open_data_imputation_v3` (E2c) and
`four_province_bucking_pilot_v0` (E1).

### 11.1 v4 artifact manifest list

| Artifact | Manifest | Output entries | Manifest SHA-256 |
|---|---|---:|---|
| `e1_e2c_product_coefficients_v1` | `data/derived/national/facilities/e1_e2c_product_coefficients_v1/manifest.json` | 4 | `203530a9fc476491264b5ffa8d6b361a5c1a384047e0414d1bbbca42bd95dbaa` |
| `national_product_chain_v5` | `data/derived/national/facilities/national_product_chain_v5/manifest.json` | 11 | `eab84d4f513a2c9567c9ee5ec886e3713c8db16e8bdfc12993b708e2d76a9794` |
| `facility_residue_supply_v7` | `data/derived/national/facilities/facility_residue_supply_v7/manifest.json` | 5 | `b91eb5374f8d37a1aa476744ac315f61f088551896cbb77ccd17b3a0831c441d` |
| `national_residue_curve_v7` | `data/derived/national/facilities/national_residue_curve_v7/manifest.json` | 5 | `ec0db3e47bd58ec5185666e1ca0c2f70310033e42f0196a5af533835a697830d` |
| `national_facility_chain_scenarios_v7` | `data/derived/national/facilities/national_facility_chain_scenarios_v7/manifest.json` | 8 | `f483a6eab52f54ed120c6d25baada13d59bc0e28c85ec2be8e9f7b3e8608571a` |
| `forestry_osecan_handoff_v8` | `data/derived/national/facilities/forestry_osecan_handoff_v8/manifest.json` | 7 | `c3820a6a8e7566183e0cd8a175cca9c30858d4946f4d3779d7e743eeaa8af111` |
| `forestry_osecan_scenarios_v7` | `data/derived/national/facilities/forestry_osecan_scenarios_v7/manifest.json` | 7 | `be42f3a411385b99b089e9de640c3fb408f105c58ee6631655698aa45b54f38a` |

Every published v4 artifact directory contains its report, JSON summary, and
SHA-256 manifest. All seven v4 manifests verify (47 output entries). The three
unchanged upstream manifests (inventory v1, capacity v3, assignment v6; 25
entries) also verify, for 72/72 entries and 0 mismatches. The handoff manifest
records the OSeCAN tree hash check and the exact input hashes. At the v4
freeze, the official OSeCAN tree hash is
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`
(146 files, unchanged since v3.2).

A clean rebuild of all seven v4 stages to a scratch directory reproduced every
numeric CSV byte-for-byte; only `generated_utc`-derived summary/manifest hashes
differed. Rebuild against data commit `f0f77a0` to reproduce the frozen
numbers; later data commits may supersede the inputs.

Focused regression tests:

```bash
.venv/bin/python -m pytest \
  tests/test_e1_e2c_facility_chain_v4.py \
  tests/test_e1_e2_facility_chain_v6.py \
  tests/test_open_data_imputation_v3.py -q
```

Result at freeze: 27 passed. The v4 contract test covers 13 jurisdictions,
E1/E2c source and zero donor product coefficients, product/feedstock/residue
conservation, 100/119/150 scenarios, 0.35/0.60/0.75 cases, v3.2→v4 and E2
v2→E2c comparisons, open-volume checks, OSeCAN v8 labels and 13-zone coverage,
and no NaN/infinity/negative values.

## 12. Non-claims and references

**Non-claims.** This package does not claim: a legal AAC or allowable cut; a
sustainable harvest level; a legal THLB; current mill operating status; observed
mill capacity or throughput; measured mill product yields; a calibrated residue
recovery factor; an interprovincial trade forecast; a transport, cost, revenue,
or net-value result; a facility investment or closure plan; an energy-policy
recommendation; an official OSeCAN scenario; or a public UI release. It does
not claim that E2c density-calibrated coefficients are observed or
province-specific measurements, and it does not require a data request, DUA, or
approval. `not_available` open-volume status is not a validation pass.

**References.**

- `data/derived/national/facilities/e1_e2c_product_coefficients_v1/report.md`
- `data/derived/national/facilities/national_product_chain_v5/NATIONAL_PRODUCT_CHAIN_V5.md`
- `data/derived/national/facilities/facility_residue_supply_v7/FACILITY_RESIDUE_SUPPLY_V7.md`
- `data/derived/national/facilities/national_residue_curve_v7/NATIONAL_RESIDUE_CURVE_V7.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v7/NATIONAL_FACILITY_CHAIN_SCENARIOS_V7.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v8/FORESTRY_OSECAN_HANDOFF_V8.md`
- `data/derived/national/facilities/forestry_osecan_scenarios_v7/FORESTRY_OSECAN_SCENARIOS_V7.md`
- `data/derived/national/bucking/open_data_imputation_v3/`
- `data/derived/national/facilities/facility_residue_supply_v8/FACILITY_RESIDUE_SUPPLY_V8.md`
- `data/derived/national/facilities/national_residue_curve_v8/NATIONAL_RESIDUE_CURVE_V8.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v8/NATIONAL_FACILITY_CHAIN_SCENARIOS_V8.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v9/FORESTRY_OSECAN_HANDOFF_V9.md`
- `data/derived/national/facilities/forestry_osecan_scenarios_v8/FORESTRY_OSECAN_SCENARIOS_V8.md`
- `docs/handoff-national-facility-chain-v4.md`
- `docs/handoff-national-facility-chain-v5.md`
- `docs/handoff-e2-v3-density-calibrated-tree-lists.md`
- `docs/four-province-biomass-allometry.md`
- `docs/bc-biomass-allometry.md`

**Freeze record.** Annotated tag `national-facility-chain-v1` marks parent
commit `94c7501` and records data commit `6ca272e`. Annotated tag
`national-facility-chain-v2` marks parent commit
`64a6b83492f06f125fa6865a7986180689c3f703` and records data commit
`e9fc837adf66a4ac4006bc036126c458a0a23214`. Annotated tag
`national-facility-chain-v2.1` marks the trade v5 measured rebuild and records
data commit `c91ee9bd19442d239b15dadf2818d96bd1b6c253`. Annotated tag
`national-facility-chain-v3` marks parent commit
`029eef1d89014c0bda8af1dfa49e78a19ae58d4f` and records data commit
`d77472c7ac877b60c3c7833d718a3e1bafe593a7`. Annotated tag
`national-facility-chain-v3.1` marks the documentation-only superset and does
not move v3. Annotated tag `national-facility-chain-v3.2` marks the
manifest-integrity fix and records data commit
`ef64c903f57c2e89f12eca62a0c9a2be49d8a28d`; it changes manifest metadata
only. Annotated tag `national-facility-chain-v4` marks the documentation commit
carrying the v4 card and handoff, records model commit
`60409639b57d3aa6ff614dfba8e4c2bdb8b28247` and data commit
`f0f77a0e6cfeb3fbbcafd685beedcd7324dbc9c8`, and does not move any earlier
tag. Annotated tag `national-facility-chain-v5` marks the v5 documentation
commit, records model commit `0943b12027f9eb994bda3646d9e3fe7548454fb0` and data commit
`86ad55506d6ef10bcd60a6b6701d4d06c0026d12`, and does not move v4 or any
earlier tag. Annotated tag `national-transport-siting-v1` marks the
transport/siting documentation commit, records transport/siting data commits
`16e979a` (assignment v10), `1f0a02a` (greenfield siting), `5b7d717`
(break-even), and `068787d` (case screen), and does not move v5.1 or any earlier
tag. The freeze commits are documentation and tag only; no code, model
artifact, UI, or official OSeCAN file changed at those commits. The tags are the
immutable references for the frozen numbers.
