# Model card — National facility chain v3 (E1/E2 open-data coefficients)

| Field | Value |
|---|---|
| Package | National facility chain v3 (frozen screening package) |
| Freeze tag | `national-facility-chain-v3` |
| Date | 2026-11-23 |
| Parent commit at freeze | `029eef1d89014c0bda8af1dfa49e78a19ae58d4f` |
| Data commit | `d77472c7ac877b60c3c7833d718a3e1bafe593a7` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Stage artifacts | Inventory v1 → capacity v3 → assignment v6 → product-chain v4 → facility residue v6 → residue curve v6 → chain scenarios v6 → OSeCAN handoff v7 |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Energy-side status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No code, model, UI, or public OSeCAN change; no legal/AAC claim; no data request, DUA, or approval dependency |

## 1. Purpose

The national facility chain is a reproducible screening chain that connects a
documented national harvest case to a bounded set of Canadian forest-product
facilities, then converts assigned feedstock into products and process/harvest
residue. Version 3 replaces the nine remaining donor provincial product
coefficients with E2 open-data imputation coefficients and applies CFS
branch/foliage allometry to the E2 stems. BC, QC, NB, and YT retain E1 measured
coefficients. All 13 jurisdictions therefore use a province-specific E1 or E2
product and total-residue coefficient: **no donor provincial product
coefficient is used**.

Within-class SAW/PANEL splits, other-roundwood routing, pellet/bio splits,
recovery fractions, and mill conversion ratios remain donor assumptions and are
labelled as such. Version 3 is designed to answer four limited questions:

1. Can a 100/119/150 Mm³ national harvest case be placed on the facility
   inventory without an unbounded sink, and with what local/cross-border split?
2. How much of that placement relies on published capacity versus a
   class-median proxy?
3. What products and residue follow when every province has an explicit E1
   measured or E2 imputed coefficient, and how large is total harvest residue
   under alternative recovery scenarios?
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
| Product chain | `national_product_chain_v4` | Applies E1 measured BC/QC/NB/YT and E2 imputed AB/SK/MB/ON/NS/PE/NL/NT/NU product shares and total residue | 42.291 Mm³ solid product; 505.333 PJ process residue; 390.418 PJ gross harvest residue |
| Facility residue | `facility_residue_supply_v6` | Assigns process residue to facilities; keeps harvest residue origin-province | 27.315 M odt process residue; 505.333 PJ |
| Residue curve | `national_residue_curve_v6` | Converts process/harvest residue to 0/25/50/75% and 35/60/75% cases at 18.5 GJ/ODT | National and provincial PJ curves |
| Scenarios | `national_facility_chain_scenarios_v6` | Re-runs 100/119/150 Mm³ on the capacity-v3 floor and applies the E1/E2 product/residue coefficients | Full scenario chain |
| OSeCAN handoff | `forestry_osecan_handoff_v7` | Allocates province/case residue to all 13 OSeCAN zones | 119 handoff rows; 91 zone/case summary rows |

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

### 3.3 E1 measured and E2 imputed coefficients

Version 3 uses two evidence tiers for provincial product and residue
coefficients. Every jurisdiction has one of them; no province uses a donor
product coefficient.

- **E1 measured (BC, QC, NB, YT).** `four_province_bucking_pilot_v0` measures
  **591 stems**: BC 347, QC 85, NB 106, and YT 53. Expansion-weighted
  recovered-log shares yield province-specific solid (sawlog), pulp
  (pulpwood), other-roundwood, and gross total harvest-residue coefficients.
- **E2 open-data imputation (AB, SK, MB, ON, NS, PE, NL, NT, NU).** The
  `open_data_imputation_v1` tree lists contain **324 stems**, 36 per province,
  built from SCANFI v2 province species composition, deterministic weighted
  donor DBH/height draws from the measured BC/QC/NB/YT tree lists, and labelled
  BC Kozak or QC Schneider donor tapers. Imputed expansion is 1,000 stems/ha,
  a round representative density. Every E2 row is `measured=false`,
  `imputed=true`, and `synthetic=false`.
- **Zero donor product coefficients.** The `national_product_chain_v4`
  coefficient table has solid, pulp, other, and total harvest-residue values
  for all 13 provinces from either E1 or E2. The donor coefficient column is
  retained only as an audit comparison; it is not applied.
- **Retained donor splits, explicitly labelled.** The within-solid SAW/PANEL
  split, the other-roundwood routing to PELLET/BIO (the facility chain has no
  OTHER class), the pellet/bio split, the harvest recovery fraction, and all
  mill conversion ratios remain donor assumptions. They are labelled
  `donor_within_class_split` or `donor_conversion` in the product-chain rows.
- **Total residue for both tiers.** E1 stems use measured stem, branch, and
  foliage components. E2 stems use stem taper residue plus CFS branch and
  foliage equations with explicit species/group proxies. Branch and foliage
  are dry mass; they are never added to stem taper volume.
- **PANEL in BC.** PANEL is absent from BC Stage A; its allocation is the
  explicit BC panel nameplate share of sawlog plus panel nameplate, labelled as
  a BC anchor.

### 3.4 Branch and foliage

Branch and foliage dry mass is computed for every E1 and E2 stem from the
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
residue are driven by the E1/E2 product shares and class conversion ratios.

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
The published capacity is unchanged by the floor. The assignment layer is
identical to v2; v3 changes only the downstream product and residue chain.

## 5. Product and residue chain

Product-chain v4 replaces all nine donor provincial product coefficients with
E2 coefficients and applies total residue (stem plus branch/foliage) to E1 and
E2 stems. All quantities are model estimates, not measured mill output or a
facility survey.

### 5.1 119 Mm³ national chain

| Metric | v3 E1/E2 | v2 donor product | Delta |
|---|---:|---:|---:|
| Solid product | 42.291 Mm³ | 47.174 Mm³ | −10.35% |
| Sawlog product | 38.119 Mm³ | 42.520 Mm³ | −10.35% |
| Panel product | 4.172 Mm³ | 4.654 Mm³ | −10.35% |
| Pulp product | 10.824 M odt | 9.527 M odt | +13.62% |
| Pellet product | 4.356 M odt | 4.172 M odt | +4.40% |
| Bio product | 775.428 GWh | 742.725 GWh | +4.40% |
| Process residue | 27.315 M odt / 505.333 PJ | 27.817 M odt / 514.614 PJ | −1.80% |
| Harvest residue, gross | 21.104 M odt / 390.418 PJ | 16.954 M odt / 313.640 PJ | +24.48% |
| Harvest residue, recoverable | 12.662 M odt / 234.251 PJ | 10.564 M odt / 195.433 PJ | +19.86% |
| Total residue, gross | 48.419 M odt / 895.751 PJ | 44.771 M odt / 828.254 PJ | +8.15% |
| Total residue, recoverable | 39.977 M odt / 739.584 PJ | 38.381 M odt / 710.047 PJ | +4.16% |

The percentages compare the v3 E1/E2 chain with the frozen v2 donor-product
chain at the same 119 Mm³ harvest and assignment. Assignment, harvest, and
facility set are unchanged; the differences are coefficient effects only.

The v2 harvest-recoverable baseline is the mixed reference definition: 0.60 on
total residue for the four measured provinces and the v1 donor stem-recoverable
share for the nine donor provinces. In v3 all 13 jurisdictions carry total
residue, so the comparable field is a uniform 0.60 on gross harvest residue.
The +19.86% is the change in that field; the v2 uniform-0.60 value was
188.184 PJ. The total-recoverable row is process residue plus the harvest
recoverable field, not 0.60 of total gross residue.

Process residue per m³ feedstock is 0.209747 odt/m³; gross harvest residue per
m³ harvest is 0.177375 odt/m³; total gross residue per m³ harvest is 0.406959
odt/m³. Process and harvest residue are separate streams and are never summed
into a combined OSeCAN row.

The 0.60 factor remains the existing reference scenario, not a calibrated
recovery factor.

### 5.2 E1/E2 coefficient table (119 Mm³)

| Province | Tier | Solid | Pulp | Other | Stem ODT/m³ | Branch+foliage ODT/m³ | Total ODT/m³ |
|---|---|---:|---:|---:|---:|---:|---:|
| BC | E1 measured | 0.6953 | 0.2948 | 0.0100 | 0.040577 | 0.121045 | 0.161622 |
| AB | E2 imputed | 0.5985 | 0.3773 | 0.0242 | 0.040511 | 0.092006 | 0.132517 |
| SK | E2 imputed | 0.4704 | 0.5008 | 0.0288 | 0.063432 | 0.105375 | 0.168808 |
| MB | E2 imputed | 0.5099 | 0.4611 | 0.0290 | 0.094930 | 0.123141 | 0.218071 |
| ON | E2 imputed | 0.4529 | 0.5076 | 0.0396 | 0.083483 | 0.124172 | 0.207654 |
| QC | E1 measured | 0.3420 | 0.6417 | 0.0164 | 0.059410 | 0.158097 | 0.217507 |
| NB | E1 measured | 0.5238 | 0.4672 | 0.0090 | 0.030341 | 0.157085 | 0.187426 |
| NS | E2 imputed | 0.4080 | 0.5754 | 0.0166 | 0.038670 | 0.154820 | 0.193490 |
| PE | E2 imputed | 0.3840 | 0.5908 | 0.0252 | 0.034972 | 0.149139 | 0.184111 |
| NL | E2 imputed | 0.5103 | 0.4873 | 0.0024 | 0.065621 | 0.154070 | 0.219691 |
| YT | E1 measured | 0.4246 | 0.5625 | 0.0129 | 0.123046 | 0.146642 | 0.269689 |
| NT | E2 imputed | 0.6242 | 0.3589 | 0.0169 | 0.070509 | 0.103371 | 0.173880 |
| NU | E2 imputed | 0.7051 | 0.2816 | 0.0132 | 0.137096 | 0.137126 | 0.274223 |

The prior donor values are retained in
`e1_e2_coefficient_replacement.csv` and `donor_vs_e2_coefficients.csv` as audit
columns. They are not applied to any chain row. NU has no modelled harvest in
the 119 Mm³ case, so its coefficients do not affect the national totals.

### 5.3 Provincial process residue (119 Mm³)

| Province | Process PJ | Harvest gross PJ | Harvest 0.60 PJ |
|---|---:|---:|---:|
| BC | 174.450 | 119.536 | 71.722 |
| AB | 97.382 | 56.120 | 33.672 |
| SK | 18.188 | 13.568 | 8.141 |
| MB | 4.841 | 4.580 | 2.748 |
| ON | 56.376 | 51.837 | 31.102 |
| QC | 110.033 | 107.334 | 64.401 |
| NB | 26.371 | 21.618 | 12.971 |
| NS | 9.873 | 8.549 | 5.129 |
| PE | 1.234 | 1.029 | 0.618 |
| NL | 6.238 | 5.910 | 3.546 |
| YT | 0.189 | 0.219 | 0.131 |
| NT | 0.158 | 0.117 | 0.070 |
| NU | 0.000 | 0.000 | 0.000 |

Provincial process residue is shown at 100% availability. The national process
total is 505.333 PJ; the harvest totals are gross residue and the uniform 0.60
recoverable value. Process and harvest columns are separate streams.

### 5.4 Residue curve v6

At 18.5 GJ/ODT, the national process-availability cases are **0 / 126.333 /
252.666 / 379.000 PJ** for 0/25/50/75%. The national harvest recovery cases are
**136.646 / 234.251 / 292.813 PJ** for 35/60/75%. Gross harvest residue is
21.104 M odt; recoverable amounts are **7.386 / 12.662 / 15.828 M odt** for
35/60/75%. The base process residue is 27.315 M odt / 505.333 PJ.

The RSYC 119 Mm³ base is 7.080 M odt / 130.981 PJ; the familiar 129.6709 PJ
figure is the RSYC 100 Mm³ reference, not the 119 Mm³ value. The harvest 60%
case is 12.662 M odt / 234.251 PJ, a ratio of **1.7884** to the RSYC 119 Mm³
base (`model_above_rsyc`). The 35% and 75% cases bracket it at 1.0433 and
2.2355. Only harvest recoverable rows are labelled `rsyc_comparable=true`. The
v2 ratio was 1.4367; the increase reflects E2 branch/foliage mass in the nine
imputed provinces, not calibration.

Facility residue v6 keeps process residue at 27.315 M odt / 505.333 PJ and
distributes it to 208 facilities receiving class-matched feedstock; 186 have
positive residue. Proxy-attributed residue is 38.92% of process residue;
scaled-proxy residue is 31.16%. Published-capacity residue is 61.08%. National
class-fallback residue is 4.08% (1.114 M odt); cross-border process residue is
4.10% (1.119 M odt). HHI of process residue is 0.022084 (effective facility
count 45.3); top-1 share is 7.31% and top-5 share is 25.12%.

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
| 100m | 32.028 | 9.104 | 3.505 | 3.646 | 649.0 | 424.71 | 328.34 | 197.00 | 753.05 | 621.72 |
| 119m | 38.119 | 10.824 | 4.172 | 4.356 | 775.4 | 505.33 | 390.42 | 234.25 | 895.75 | 739.58 |
| 150m | 47.330 | 13.873 | 5.180 | 5.656 | 1,006.8 | 636.14 | 494.83 | 296.90 | 1,130.97 | 933.04 |

Relative to the 119 Mm³ case, 100 Mm³ reduces assigned volume by 18.977 Mm³ and
total gross residue by 142.70 PJ; 150 Mm³ adds 31.023 Mm³ and 235.22 PJ.
Product and residue ordering is validated as 100 < 119 < 150 within each
metric.

### 6.2 Donor-product versus E1/E2 chain (119 Mm³)

| Metric | v2 donor product | v3 E1/E2 | Delta | Delta % |
|---|---:|---:|---:|---:|
| Solid product Mm³ | 47.174 | 42.291 | −4.883 | −10.35% |
| Pulp product M odt | 9.527 | 10.824 | +1.297 | +13.62% |
| Panel product Mm³ | 4.654 | 4.172 | −0.482 | −10.35% |
| Pellet product M odt | 4.172 | 4.356 | +0.184 | +4.40% |
| Bio product GWh | 742.725 | 775.428 | +32.704 | +4.40% |
| Process residue PJ | 514.614 | 505.333 | −9.281 | −1.80% |
| Harvest gross PJ | 313.640 | 390.418 | +76.777 | +24.48% |
| Harvest recoverable PJ | 195.433 | 234.251 | +38.818 | +19.86% |
| Total gross PJ | 828.254 | 895.751 | +67.496 | +8.15% |
| Total recoverable PJ | 710.047 | 739.584 | +29.537 | +4.16% |

The solid decrease and pulp increase follow from the E2 product shares in the
nine imputed provinces: the donor solid share was uniformly high, while the E2
lists resolve substantial pulpwood in AB, SK, MB, ON, NS, PE, NL, and NT. The
process residue decrease is smaller than the product shift because process
residue per unit feedstock differs by class. The harvest increase comes from
CFS branch and foliage in the E2 provinces. These are model estimates at
identical harvest and assignment; they are not observed mill outcomes.

## 7. Base v6 local processing versus v5 trade sensitivity

The **v6 base** is the `province_capacity_floor` case: capacity v3, the 1.2×
provincial floor, local-first allocation, and cross-border only for the local
residual. The **v5 sensitivity** is the earlier unscaled
`published_plus_proxy` case. It is a trade-permissive counterfactual, not a
forecast and not evidence of provincial self-sufficiency. The trade sensitivity
was built on the v2 donor-product chain and is not rebuilt on E1/E2 in v3; its
national products and residue therefore do not match the v3 base.

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
assumption for this release. The v3 E1/E2 product chain applies to the v6
assignment only.

## 8. OSeCAN handoff v7

Handoff v7 is a read-only forestry-side screening handoff from residue curve
v6. It preserves the all-zone v2 proxy weights and refreshes the province/case
totals from the E1/E2 chain. It maps to all 13 OSeCAN zones with no `UNMAPPED`
bucket.

| Case | National PJ | Tier / position |
|---|---:|---|
| Process 0% | 0.000 | T1 |
| Process 25% | 126.333 | T3 |
| Process 50% | 252.666 | above T4 |
| Process 75% | 379.000 | above T4 |
| Harvest 35% | 136.646 | T3 |
| Harvest 60% | 234.251 | above T4 |
| Harvest 75% | 292.813 | above T4 |

The adopted supply-curve reference points are T1 85.007 PJ, T2 109.399 PJ,
T3 149.273 PJ, and T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ
national and 43.524 PJ BC; these are context, not policy targets. At 119 Mm³,
process 25% is 0.826× the 2050 national context and process 50% is 1.652×;
harvest 60% is 1.532×. In BC, process 25% (44.921 PJ) is 1.032× the 43.524 PJ
BC zonal reference, process 50% is 2.064×, and harvest 60% is 1.648×.

**Tier shifts versus the donor-product chain.** Replacing the nine donor
product coefficients with E2 total-residue coefficients moves the harvest 60%
case from T4 to above T4; the other harvest cases and all process cases keep
their tiers:

| Stream | Case | Donor product v6 | E1/E2 v7 | Changed |
|---|---|---|---|---|
| Harvest | 35% | T3 | T3 | no |
| Harvest | 60% | T4 | above_T4 | yes |
| Harvest | 75% | above_T4 | above_T4 | no |
| Process | 0% / 25% / 50% / 75% | T1 / T3 / above_T4 / above_T4 | T1 / T3 / above_T4 / above_T4 | no |

The 119 Mm³ process 50% zone results include B2 82.400 PJ, Q1 35.190 PJ,
A1 33.330 PJ, O1 23.154 PJ, Q2 22.432 PJ, A2 18.441 PJ, T1 18.179 PJ, and
B1 7.443 PJ. The harvest 60% results include B2 65.780 PJ, Q1 39.330 PJ,
O1 25.915 PJ, Q2 25.071 PJ, A1 21.678 PJ, T1 18.718 PJ, A2 11.994 PJ,
S1 8.141 PJ, B1 5.941 PJ, O2 5.187 PJ, L1 3.546 PJ, M1 2.748 PJ, and
N1 0.202 PJ. All zone rows carry `model_estimate=true`, `observed=false`,
`policy_grade=false`, and `official_osecan_changed=false`.

Handoff validation passed 20 checks with 0 failures, including row uniqueness,
zone coverage, province-weight sums, conservation to national totals, no
combined stream, explicit proxy labels, and an unchanged OSeCAN tree hash
(`80caa91c…`, 146 files before and after).

## 9. Key results

- **119 Mm³ v6 base:** 118.977 Mm³ assigned; 99.97% local; 36,376 m³
  cross-border; 39.9% proxy; 208 facilities; HHI 0.01287; largest facility
  6.80%; no unassigned harvest; NT floor gap 43,651 m³.
- **E1/E2 coverage:** 13 of 13 jurisdictions have a province-specific product
  and total-residue coefficient. E1 measured covers BC 347, QC 85, NB 106, and
  YT 53 stems (591 total); E2 imputation covers AB/SK/MB/ON/NS/PE/NL/NT/NU
  with 324 stems (36 each). Zero donor provincial product coefficients.
- **119 Mm³ products:** 42.291 Mm³ solid (sawlog 38.119 + panel 4.172),
  10.824 M odt pulp, 4.356 M odt pellet, 775.428 GWh bio.
- **119 Mm³ residue:** 27.315 M odt / 505.333 PJ process; 21.104 M odt /
  390.418 PJ gross harvest; 12.662 M odt / 234.251 PJ harvest at 0.60;
  48.419 M odt / 895.751 PJ total gross; 39.977 M odt / 739.584 PJ total
  recoverable (process + harvest recoverable).
- **Change versus donor-product v2:** solid −10.35%, pulp +13.62%, process
  −1.80%, harvest gross +24.48%, harvest 0.60 +19.86%, total gross +8.15%.
- **Recovery scenarios:** harvest 35/60/75% = 136.646 / 234.251 / 292.813 PJ;
  process 0/25/50/75% = 0 / 126.333 / 252.666 / 379.000 PJ; no recovery factor
  is calibrated, and 0.60 is the reference scenario only.
- **100/119/150 total gross residue:** 753.05 / 895.75 / 1,130.97 PJ; total
  recoverable 621.72 / 739.58 / 933.04 PJ.
- **RSYC check:** 60% harvest case is 1.7884 of the RSYC 119 Mm³ base
  (`model_above_rsyc`), not a calibration.
- **OSeCAN handoff v7:** 119 process 50% = 252.666 PJ; harvest 60% =
  234.251 PJ; harvest 60% moves T4 → above_T4; process tiers unchanged;
  2050 context = 152.923 PJ national / 43.524 PJ BC.
- **Capacity evidence:** 433 published records / 166.609 Mm³/yr; 241 proxy
  records / 104.733 Mm³/yr; 15 records without capacity.

## 10. Limitations and non-use

- **Nine jurisdictions are E2 imputed, not measured.** AB, SK, MB, ON, NS, PE,
  NL, NT, and NU use 36-stem open-data imputation lists built from SCANFI
  composition, donor DBH/height geometry, and donor tapers. They are not
  province-specific measured tree lists and not observed mill outcomes. The
  imputed expansion of 1,000 stems/ha is an assumption.
- **E2 donor geometry and tapers.** The E2 lists draw DBH/height from the
  measured BC/QC/NB/YT stems and use BC Kozak or QC Schneider donor tapers.
  Species without a mapped taper and unsupported hardwoods are excluded or
  proxied; they are not imputed as observations.
- **E1 taper coverage is partial.** The taper-supported share of the eligible
  measured frame is 100.0% for BC (347 trees), 74.4% for QC (85 trees), 37.0%
  for NB (106 trees), and 98.9% for YT (53 trees). BC, NB, and YT residual proxy
  species remain labelled proxies. The E1 coefficients cover only the
  taper-supported measured frame, not all provincial species or all hardwood
  volume.
- **Branch and foliage are model estimates.** They use published CFS equations
  with documented proxies (mountain hemlock, birch, black spruce for YT spruce),
  not weighed components. They add dry mass and energy but no measured volume.
- **Donor splits remain material.** Solid/pulp/other are E1 or E2, but the
  within-solid SAW/PANEL split, other-roundwood routing to PELLET/BIO, the
  pellet/bio split, and all class conversion ratios remain donor assumptions.
  The measured and E2 pilots do not measure panel logs separately. These rows
  carry explicit donor labels.
- **No chosen recovery factor.** Gross total residue is reported with
  0.35/0.60/0.75 recoverable scenarios. The 0.60 value is the existing
  reference scenario, not a calibrated, observed, or policy recovery factor.
  The product-chain recoverable field and the uniform 0.60 case now coincide
  because all provinces carry total residue; neither is a preferred answer.
- **Proxy capacity is material.** Proxy capacity is 39.9% of assigned volume
  in the 119 Mm³ v6 base (47.463 of 118.977 Mm³) and 38.92% of process
  residue; scaled proxy is 31.16% of process residue. The 241 proxy records are
  class medians, not facility measurements. Scaled proxy capacity (38.035 Mm³
  in the v6 floor case) is not observed capacity and is not evidence of
  provincial self-sufficiency.
- **Published capacity is mixed-basis.** BC is a 2015 infeed proxy; Quebec is
  a 2026 permit ceiling; pellet-map rows are converted finished-pellet bulk
  volume; company rows are converted sawn-lumber output. The bases are not
  interchangeable. A permit does not prove operation, and no record is
  observed throughput.
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
- **No data-request dependency.** The chain is complete on open data. No DUA,
  data request, or approval is required. Optional requests could only improve
  evidence tiers; they do not unblock the model.
- **No public OSeCAN or UI change.** The freeze adds documentation and a tag
  only. It does not modify the public OSeCAN repository, the energy model, or
  any UI surface.

## 11. Reproduce and verify

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_inventory_v1
.venv/bin/python -m workflows.national.facilities.national_facility_capacity_v3
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v6
.venv/bin/python -m workflows.national.facilities.national_product_chain_v4
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v6
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v6
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v6
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v7
```

Evidence inputs for the coefficient tiers:

```bash
.venv/bin/python -m workflows.national.bucking.open_data_imputation
.venv/bin/python -m workflows.national.facilities.national_product_chain_v4
```

Focused regression tests:

```bash
.venv/bin/python -m pytest \
  tests/test_national_facility_inventory_v1.py \
  tests/test_national_facility_capacity_v3.py \
  tests/test_national_facility_assignment_v6.py \
  tests/test_national_product_chain_v4.py \
  tests/test_open_data_imputation.py \
  tests/test_facility_residue_supply_v6.py \
  tests/test_national_residue_curve_v6.py \
  tests/test_national_facility_chain_scenarios_v6.py \
  tests/test_forestry_osecan_handoff_v7.py \
  tests/test_four_province_bucking_pilot.py \
  tests/test_four_province_biomass_allometry.py -q
```

Every published artifact directory contains its report, JSON summary, and
SHA-256 manifest. The handoff manifest records the OSeCAN tree hash check and
the exact input hashes. Rebuild against data commit `d77472c` to reproduce the
frozen numbers; later data commits may supersede the inputs.

## 12. Non-claims and references

**Non-claims.** This package does not claim: a legal AAC or allowable cut; a
sustainable harvest level; a legal THLB; current mill operating status; observed
mill capacity or throughput; measured mill product yields; a calibrated residue
recovery factor; an interprovincial trade forecast; a transport, cost, revenue,
or net-value result; a facility investment or closure plan; an energy-policy
recommendation; an official OSeCAN scenario; or a public UI release. It does
not claim that E2 imputed coefficients are observed or province-specific
measurements, and it does not require a data request, DUA, or approval.

**References.**

- `data/derived/national/facilities/national_facility_inventory_v1/NATIONAL_FACILITY_INVENTORY_V1.md`
- `data/derived/national/facilities/national_facility_capacity_v3/NATIONAL_FACILITY_CAPACITY_V3.md`
- `data/derived/national/facilities/national_facility_assignment_v6/NATIONAL_FACILITY_ASSIGNMENT_V6.md`
- `data/derived/national/facilities/national_product_chain_v4/NATIONAL_PRODUCT_CHAIN_V4.md`
- `data/derived/national/facilities/facility_residue_supply_v6/FACILITY_RESIDUE_SUPPLY_V6.md`
- `data/derived/national/facilities/national_residue_curve_v6/NATIONAL_RESIDUE_CURVE_V6.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v6/NATIONAL_FACILITY_CHAIN_SCENARIOS_V6.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v7/FORESTRY_OSECAN_HANDOFF_V7.md`
- `data/derived/national/bucking/open_data_imputation_v1/report.md`
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
`d77472c7ac877b60c3c7833d718a3e1bafe593a7`. The tags are the immutable
references for the frozen numbers. Documentation-only commits after a tag do
not change the frozen artifacts.
