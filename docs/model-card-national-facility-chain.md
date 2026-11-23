# Model card — National facility chain v2 (measured total residue)

| Field | Value |
|---|---|
| Package | National facility chain v2 (frozen screening package) + v2.1 trade addendum |
| Freeze tag | `national-facility-chain-v2` (base) / `national-facility-chain-v2.1` (trade rebuild) |
| Date | 2026-11-23 |
| Parent commit at freeze | `64a6b83492f06f125fa6865a7986180689c3f703` |
| Data commit | `e9fc837adf66a4ac4006bc036126c458a0a23214` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Stage artifacts | Inventory v1 → capacity v3 → assignment v6 → product-chain v3 → residue curve v5 → facility residue v5 → chain scenarios v5 → OSeCAN handoff v6 |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Energy-side status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No UI change; no public OSeCAN change; no legal/AAC claim |

## 1. Purpose

The national facility chain is a reproducible screening chain that connects a
documented national harvest case to a bounded set of Canadian forest-product
facilities, then converts assigned feedstock into products and process/harvest
residue. Version 2 replaces the BC-anchored donor product shares and stem-only
harvest residue with measured four-province bucking coefficients and measured
stem + branch/foliage total residue for BC, QC, NB, and YT. It is designed to
answer four limited questions:

1. Can a 100/119/150 Mm³ national harvest case be placed on the facility
   inventory without an unbounded sink, and with what local/cross-border split?
2. How much of that placement relies on published capacity versus a
   class-median proxy?
3. What products and residue follow from the assigned feedstock when measured
   four-province coefficients replace the donor coefficients, and how large is
   total harvest residue under alternative recovery scenarios?
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
| Product chain | `national_product_chain_v3` | Applies measured BC/QC/NB/YT bucking shares and total residue; donor coefficients for the other nine | Products unchanged from product-chain v2; gross harvest residue 313.640 PJ |
| Residue curve | `national_residue_curve_v5` | Converts process/harvest residue to 0/25/50/75% and 35/60/75% cases at 18.5 GJ/ODT | National and provincial PJ curves |
| Facility residue | `facility_residue_supply_v5` | Assigns process residue to facilities; keeps harvest residue origin-province | 27.817 M odt process residue; 514.614 PJ |
| Scenarios | `national_facility_chain_scenarios_v5` | Re-runs 100/119/150 Mm³ on the capacity-v3 floor and applies measured product/residue coefficients | Full scenario chain |
| OSeCAN handoff | `forestry_osecan_handoff_v6` | Allocates province/case residue to all 13 OSeCAN zones | 119 handoff rows; 91 zone/case summary rows |

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

### 3.3 Measured product and residue coefficients

- **Measured pilot**: `four_province_bucking_pilot_v0` measures **591 stems**:
  BC 347, QC 85, NB 106, and YT 53. Expansion-weighted recovered-log shares
  yield province-specific solid (sawlog), pulp (pulpwood), other-roundwood, and
  gross harvest-residue coefficients.
- **Coverage**: 4 of 13 jurisdictions are measured; the other 9 retain the BC
  aggregate donor coefficients. There are 16 measured coefficient replacement
  rows (four coefficients × four provinces) and 9 donor jurisdictions.
- **Solid share** replaces the SAW+PANEL feedstock share. The within-solid
  SAW/PANEL split remains the v1 donor split because the pilot does not
  measure panel logs separately.
- **Other roundwood** is routed to PELLET/BIO because the facility chain has no
  OTHER class. That routing and the pellet/bio split are donor assumptions.
- **Harvest residue** for the four measured provinces is gross total residue
  (stem taper residue + live branch + live foliage), not the stem-only donor
  coefficient. The recoverable share remains a scenario choice, not a measured
  mill recovery.
- **PANEL** is absent from BC Stage A; its allocation is the explicit BC panel
  nameplate share of sawlog plus panel nameplate, labelled as a BC anchor.

### 3.4 Branch and foliage

Branch and foliage dry mass is computed for every measured stem from the
vendored CFS Biomass and Nutrients tree-level DBH + height equations
(Lambert, Ung and Raulier 2005; Ung, Bernier and Guo 2008; Open Government
Licence – Canada):

```
Biomass_kg = a * DBH_cm^b * Height_m^c
```

The equations are applied only to measured live standing stems. Documented
proxies include mountain hemlock (conifer aggregate), birch (Betula aggregate),
and black spruce for YT spruce. Branch and foliage are model estimates, not
weighed components. They add dry mass and energy but no measured volume, so
the m³ feedstock routing remains the stem taper residue; products and process
residue are unchanged from product-chain v2.

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
identical to v1; v2 changes only the product and residue downstream.

## 5. Product and residue chain

Product-chain v3 preserves every product and process-residue value from v2 and
replaces stem harvest residue with measured total residue for BC, QC, NB, and
YT. All quantities are model estimates, not measured mill output or a facility
survey.

### 5.1 119 Mm³ national chain

| Metric | Value |
|---|---:|
| Sawlog feedstock | 61.818 Mm³ |
| Pulp feedstock | 46.030 Mm³ |
| Panel feedstock | 10.119 Mm³ |
| Pellet feedstock | 10.644 Mm³ |
| Bio feedstock | 2.228 Mm³ |
| Total feedstock | 130.840 Mm³ |
| Sawlog product | 42.520 Mm³ |
| Pulp product | 9.527 M odt |
| Panel product | 4.654 Mm³ |
| Pellet product | 4.172 M odt |
| Bio product | 742.725 GWh |
| Process residue | 27.817 M odt / 514.614 PJ |
| Harvest residue, gross | 16.954 M odt / 313.640 PJ |
| Harvest residue, reference-recoverable | 10.564 M odt / 195.433 PJ |
| Harvest residue, unrecovered | 6.390 M odt / 118.207 PJ |
| Total residue, gross | 44.771 M odt / 828.254 PJ |
| Total residue, reference-recoverable | 38.381 M odt / 710.047 PJ |

Process residue per m³ feedstock is 0.212602 odt/m³; gross harvest residue per
m³ harvest is 0.142494 odt/m³; total gross residue per m³ harvest is 0.376294
odt/m³. Process and harvest residue are separate streams and are never summed
into a combined OSeCAN row.

The 195.433 PJ reference-recoverable value is the product-chain definition:
the four measured provinces apply 0.60 to total gross residue, while the nine
donor provinces retain the v1 donor stem-recoverable share. A uniform 0.60
recovery applied to national gross total residue (all 13 jurisdictions) is
**188.184 PJ** (10.172 M odt), the value used in the residue curve and OSeCAN
handoff. The 0.60 factor is the existing reference scenario, not a calibrated
recovery factor.

### 5.2 Measured coefficient replacement (119 Mm³)

| Province | Coefficient | Donor | Measured | Ratio |
|---|---|---:|---:|---:|
| BC | solid_share | 0.689193 | 0.695281 | 1.009 |
| BC | pulp_share | 0.308234 | 0.294764 | 0.956 |
| BC | other_share | 0.080095 | 0.009955 | 0.124 |
| BC | harvest_residue_odt_per_m3 | 0.076226 | 0.040577 | 0.532 |
| QC | solid_share | 0.689193 | 0.341993 | 0.496 |
| QC | pulp_share | 0.308234 | 0.641655 | 2.082 |
| QC | other_share | 0.080095 | 0.016352 | 0.204 |
| QC | harvest_residue_odt_per_m3 | 0.076226 | 0.059410 | 0.779 |
| NB | solid_share | 0.689193 | 0.523792 | 0.760 |
| NB | pulp_share | 0.308234 | 0.467185 | 1.516 |
| NB | other_share | 0.080095 | 0.009023 | 0.113 |
| NB | harvest_residue_odt_per_m3 | 0.076226 | 0.030341 | 0.398 |
| YT | solid_share | 0.689193 | 0.424617 | 0.616 |
| YT | pulp_share | 0.308234 | 0.562531 | 1.825 |
| YT | other_share | 0.080095 | 0.012851 | 0.160 |
| YT | harvest_residue_odt_per_m3 | 0.076226 | 0.123046 | 1.614 |

The stem residue rates above are only one component of total residue. Measured
total rates (stem + branch/foliage) are BC 0.161622, QC 0.217507, NB 0.187426,
and YT 0.269689 ODT/m³ harvest. Products and process residue are unchanged by
the branch/foliage addition.

### 5.3 Residue curve v5

At 18.5 GJ/ODT, the national process-availability cases are **0 / 128.654 /
257.307 / 385.961 PJ** for 0/25/50/75%. The national harvest recovery cases are
**109.774 / 188.184 / 235.230 PJ** for 35/60/75%. Gross harvest residue is
16.954 M odt; recoverable amounts are **5.934 / 10.172 / 12.715 M odt** for
35/60/75%. The base process residue is 27.817 M odt / 514.614 PJ.

The RSYC 119 Mm³ base is 7.080 M odt / 130.981 PJ; the familiar 129.6709 PJ
figure is the RSYC 100 Mm³ reference, not the 119 Mm³ value. The harvest 60%
case is 10.172 M odt / 188.184 PJ, a ratio of **1.4367** to the RSYC 119 Mm³
base (`model_above_rsyc`). The 35% and 75% cases bracket it at 0.8381 and
1.7959. Only harvest recoverable rows are labelled `rsyc_comparable=true`.
This reverses the v1 stem-only check (0.769, `model_below_rsyc`); the shift is
driven by measured branch and foliage mass, not by calibration.

Facility residue v5 keeps process residue at 27.817 M odt / 514.614 PJ and
distributes it to 208 facilities receiving class-matched feedstock; 186 have
positive residue. Proxy-attributed residue is 39.41% of process residue;
scaled-proxy residue is 31.96%. Published-capacity residue is 60.59%. National
class-fallback residue is 3.81% (1.060 M odt); cross-border process residue is
3.83% (1.066 M odt). HHI of process residue is 0.018359 (effective facility
count 54.5); top-1 share is 5.72% and top-5 share is 21.21%.

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
| 100m | 35.688 | 8.025 | 3.906 | 3.493 | 621.8 | 432.43 | 264.48 | 164.72 | 696.92 | 597.15 |
| 119m | 42.520 | 9.527 | 4.654 | 4.172 | 742.7 | 514.61 | 313.64 | 195.43 | 828.25 | 710.05 |
| 150m | 53.646 | 12.006 | 5.872 | 5.354 | 953.0 | 649.26 | 385.20 | 241.25 | 1,034.46 | 890.51 |

Relative to the 119 Mm³ case, 100 Mm³ reduces assigned volume by 18.977 Mm³ and
total gross residue by 131.34 PJ; 150 Mm³ adds 31.023 Mm³ and 206.21 PJ.
Product and residue ordering is validated as 100 < 119 < 150 within each
metric.

### 6.2 Measured total versus stem-only (119 Mm³)

| Metric | Stem-only v4 | Measured total v5 | Delta | Delta % |
|---|---:|---:|---:|---:|
| Products (all) | — | — | 0.000 | +0.00% |
| Process residue PJ | 514.614 | 514.614 | 0.000 | +0.00% |
| Harvest gross PJ | 127.860 | 313.640 | +185.780 | +145.30% |
| Harvest recoverable PJ | 90.990 | 195.433 | +104.443 | +114.79% |
| Total gross PJ | 642.474 | 828.254 | +185.780 | +28.92% |
| Total recoverable PJ | 605.604 | 710.047 | +104.443 | +17.25% |

The increase is concentrated in BC (harvest gross +89.525 PJ), QC (+78.017 PJ),
and NB (+18.118 PJ); YT adds +0.119 PJ. Provincial process residue is unchanged
by the total-residue step but differs from the v1 donor chain because of the
measured product shares (BC 174.450, AB 101.204, SK 19.208, MB 5.019, ON
59.656, QC 110.033, NB 26.371, NS 10.559, PE 1.336, NL 6.428, YT 0.189, NT
0.161 PJ at 119 Mm³).

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
residue, not its national total. The v5 trade sensitivity has now been rebuilt
on the same measured `national_product_chain_v3` coefficients and total-residue
scenarios (v2.1 addendum); the v1 province-level process-residue deltas are not
carried forward as results. The v5 sensitivity's 18.4% cross-border share
remains a useful upper-bound trade counterfactual, but the v6 local-processing
case is the frozen base because the 1.2× floor is the documented primary
assumption for this release.

### 7.1 Trade v5 measured rebuild (v2.1 addendum)

The trade chain is `national_facility_chain_scenarios_v5_trade` →
`facility_residue_supply_v5_trade` → `national_residue_curve_v5_trade` →
`forestry_osecan_handoff_v6_trade`. It uses assignment v5
`published_plus_proxy` (unscaled capacity, trade-permissive cross-border
allocation) with the same `national_product_chain_v3` measured coefficients and
the same 0.35/0.60/0.75 harvest recovery and 0/25/50/75% process availability
scenarios. The comparison artifact is
`national_facility_chain_v5_trade_comparison`.

| Metric (119 Mm³) | Local v6 | Trade v5 measured | Delta trade−local |
|---|---:|---:|---:|
| Assigned | 118.977 Mm³ | 118.977 Mm³ | 0 |
| Local | 118.941 Mm³ (99.97%) | 97.075 Mm³ (81.59%) | −21.866 Mm³ |
| Cross-border | 36,376 m³ (0.031%) | 21.902 Mm³ (18.41%) | +21.866 Mm³ |
| Published | 71.514 Mm³ (60.11%) | 78.658 Mm³ (66.11%) | +7.144 Mm³ |
| Proxy | 47.463 Mm³ (39.89%) | 40.319 Mm³ (33.89%) | −7.144 Mm³ |
| Scaled proxy | 38.035 Mm³ | 0 | −38.035 Mm³ |
| Facilities used | 208 | 263 | +55 |
| Largest facility share | 6.80% | 1.82% | −4.98 pp |
| Top-5 share | 15.75% | 7.98% | −7.77 pp |
| HHI | 0.01287 | 0.00755 | −0.00532 |
| Capacity shortfall before floor | 30.282 Mm³/yr | 30.282 Mm³/yr | 0 |

National products and residue are unchanged: at 119 Mm³ both cases have
514.614 PJ process residue, 313.640 PJ gross harvest residue, 195.433 PJ
product-chain recoverable harvest residue, and 828.254 PJ total gross residue.
The trade case moves the same national total across facilities and provinces.
At 119 Mm³ the facility-province process residue moves BC 180.270 → 254.958 PJ
(+74.687), AB 106.747 → 41.207 PJ (−65.540), SK 12.645 → 2.062 PJ (−10.584),
ON 57.143 → 48.559 PJ (−8.584), QC 115.184 → 126.431 PJ (+11.247), MB
3.428 → 2.226 PJ (−1.202), and YT 0.080 → 0.186 PJ (+0.106). The remaining
provinces move by less than 0.1 PJ. The trade case is a screening
counterfactual, not an interprovincial trade forecast.

OSeCAN handoff v6 trade keeps the same zone weights and the same national case
totals. BC process residue rises from 45.068 to 63.739 PJ at 25% availability
and from 90.135 to 127.479 PJ at 50%; the national process and harvest cases
and all case tiers are unchanged (process T1/T3/above_T4/above_T4; harvest
T3/T4/above_T4). The official OSeCAN tree remains hash-identical.

## 8. OSeCAN handoff v6

Handoff v6 is a read-only forestry-side screening handoff from residue curve
v5. It preserves the all-zone v2 proxy weights and refreshes the province/case
totals from the assignment-v6 measured total-residue chain. It maps to all 13
OSeCAN zones with no `UNMAPPED` bucket.

| Case | National PJ | Tier / position |
|---|---:|---|
| Process 0% | 0.000 | T1 |
| Process 25% | 128.654 | T3 |
| Process 50% | 257.307 | above T4 |
| Process 75% | 385.961 | above T4 |
| Harvest 35% | 109.774 | T3 |
| Harvest 60% | 188.184 | T4 |
| Harvest 75% | 235.230 | above T4 |

The adopted supply-curve reference points are T1 85.007 PJ, T2 109.399 PJ,
T3 149.273 PJ, and T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ
national and 43.524 PJ BC; these are context, not policy targets. At 119 Mm³,
process 25% is 0.841× the 2050 national context and process 50% is 1.683×;
harvest 60% is 1.231×. In BC, process 25% (45.068 PJ) is 1.035× the 43.524 PJ
BC zonal reference, process 50% is 2.071×, and harvest 60% is 1.648×.

**Tier shifts.** Replacing stem-only harvest residue with measured total
residue moves all three harvest cases up the adopted curve; process tiers are
unchanged:

| Stream | Case | Stem-only v5 | Total residue v6 | Changed |
|---|---|---|---|---|
| Harvest | 35% | T1 | T3 | yes |
| Harvest | 60% | T1 | T4 | yes |
| Harvest | 75% | T2 | above_T4 | yes |
| Process | 0% / 25% / 50% / 75% | T1 / T3 / above_T4 / above_T4 | T1 / T3 / above_T4 / above_T4 | no |

The 119 Mm³ process 50% zone results include B2 82.668 PJ, Q1 35.172 PJ,
A1 34.362 PJ, O1 23.807 PJ, Q2 22.420 PJ, A2 19.012 PJ, T1 18.407 PJ, and
B1 7.467 PJ. The harvest 60% results include B2 65.780 PJ, Q1 39.330 PJ,
Q2 25.071 PJ, T1 15.247 PJ, A1 12.469 PJ, O1 9.513 PJ, A2 6.899 PJ,
B1 5.941 PJ, S1 3.676 PJ, L1 1.230 PJ, M1 0.961 PJ, and N1 0.162 PJ. All zone
rows carry `model_estimate=true`, `observed=false`, `policy_grade=false`, and
`official_osecan_changed=false`.

Handoff validation passed 20 checks with 0 failures, including row uniqueness,
zone coverage, province-weight sums, conservation to national totals, no
combined stream, explicit proxy labels, and an unchanged OSeCAN tree hash
(`80caa91c…`, 146 files before and after). `forestry_osecan_scenarios_v5`
provides the parallel 100/119/150 scenario handoff; it is not an official
OSeCAN result and does not apply candidate activity limits.

## 9. Key results

- **119 Mm³ v6 base:** 118.977 Mm³ assigned; 99.97% local; 36,376 m³
  cross-border; 39.9% proxy; 208 facilities; HHI 0.01287; largest facility
  6.80%; no unassigned harvest; NT floor gap 43,651 m³.
- **v5 trade sensitivity (measured rebuild):** 21.902 Mm³ cross-border
  (18.41%); 33.9% proxy; 263 facilities; HHI 0.00755; largest facility 1.82%;
  30.282 Mm³ capacity shortfall before floor scaling across 5 provinces.
  National products and residue match the local v6 base; process residue moves
  from AB/SK/ON toward BC/QC, and BC process 50% rises to 127.479 PJ.
- **Measured coefficient coverage:** 4 of 13 jurisdictions measured (BC, QC,
  NB, YT; 591 stems); 9 donor jurisdictions.
- **119 Mm³ residue:** 27.817 M odt / 514.614 PJ process; 16.954 M odt /
  313.640 PJ gross harvest; 10.564 M odt / 195.433 PJ reference-recoverable
  harvest; 44.771 M odt / 828.254 PJ total gross; 38.381 M odt / 710.047 PJ
  total reference-recoverable.
- **Recovery scenarios:** harvest 35/60/75% = 109.774 / 188.184 / 235.230 PJ;
  process 0/25/50/75% = 0 / 128.654 / 257.307 / 385.961 PJ; no recovery factor
  is calibrated, and 0.60 is the reference scenario only.
- **100/119/150 total gross residue:** 696.92 / 828.25 / 1,034.46 PJ; total
  reference-recoverable 597.15 / 710.05 / 890.51 PJ.
- **RSYC check:** 60% harvest case is 1.4367 of the RSYC 119 Mm³ base
  (`model_above_rsyc`), not a calibration.
- **OSeCAN handoff v6:** 119 process 50% = 257.307 PJ; harvest 60% =
  188.184 PJ; harvest tiers shift T1/T1/T2 → T3/T4/above_T4; process tiers
  unchanged; 2050 context = 152.923 PJ national / 43.524 PJ BC.
- **Capacity evidence:** 433 published records / 166.609 Mm³/yr; 241 proxy
  records / 104.733 Mm³/yr; 15 records without capacity.

## 10. Limitations and non-use

- **Only 4 of 13 jurisdictions are measured.** BC, QC, NB, and YT use measured
  bucking-pilot shares; AB, SK, MB, ON, NS, PE, NL, NT, and NU retain the BC
  aggregate donor. Provincial species mixes differ, so the nine donor
  jurisdictions may be wrong in ways the national total does not reveal.
  Product and residue values are not measured mill output.
- **Donor tapers for NB and YT.** BC and QC use local published tapers
  (Kozak 2004; Schneider 2013); NB uses the QC Schneider donor and YT uses the
  BC Kozak donor. These are transfer assumptions, not local taper measurements.
- **Taper coverage is partial.** The taper-supported share of the eligible
  measured frame is 100.0% for BC (347 trees), 74.4% for QC (85 trees), 37.0%
  for NB (106 trees), and 98.9% for YT (53 trees). Species without a mapped
  taper, stems outside the taper domain, and unsupported hardwoods are
  excluded and not imputed. BC, NB, and YT residual proxy species remain
  labelled proxies. The measured coefficients cover only the taper-supported
  measured frame, not all provincial species or all hardwood volume.
- **Branch and foliage are proxies.** They are model estimates from published
  CFS equations, not weighed components. Documented equation proxies include
  mountain hemlock (conifer aggregate), birch (Betula aggregate), and black
  spruce for YT spruce. The equations describe live trees; the measured lists
  are live standing stems. Branch and foliage add dry mass and energy but no
  measured volume, so m³ feedstock routing remains stem-only.
- **No chosen recovery factor.** Gross total residue is reported with
  0.35/0.60/0.75 recoverable scenarios. The 0.60 value is the existing
  reference scenario, not a calibrated, observed, or policy recovery factor.
  The product-chain reference-recoverable value (195.433 PJ) is a mixed
  definition: 0.60 on total residue for the four measured provinces and the v1
  donor stem-recoverable share for the nine donor provinces. The uniform 0.60
  curve value is 188.184 PJ. Neither is a preferred or calibrated answer.
- **Proxy capacity is material.** Proxy capacity is 39.9% of assigned volume
  in the 119 Mm³ v6 base (47.463 of 118.977 Mm³) and 39.41% of process
  residue; scaled proxy is 31.96% of process residue. The 241 proxy records are
  class medians, not facility measurements. Scaled proxy capacity (38.035 Mm³
  in the v6 floor case) is not observed capacity and is not evidence of
  provincial self-sufficiency.
- **Published capacity is mixed-basis.** BC is a 2015 infeed proxy; Quebec is
  a 2026 permit ceiling; pellet-map rows are converted finished-pellet bulk
  volume; company rows are converted sawn-lumber output. The bases are not
  interchangeable. A permit does not prove operation, and no record is
  observed throughput.
- **Product splits are partly donor.** Solid is measured, but the SAW/PANEL
  split inside solid, the other-roundwood routing to PELLET/BIO, the pellet/bio
  split, the recoverable share, and all mill conversion ratios remain donor
  assumptions. The measured pilot does not measure panel logs separately.
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
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v6
.venv/bin/python -m workflows.national.facilities.national_product_chain_v3
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v5
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v5
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v5
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v6
.venv/bin/python -m workflows.national.facilities.forestry_osecan_scenarios_v5
```

Trade v5 measured rebuild and comparison:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v5_trade
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v5_trade
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v5_trade
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v6_trade
.venv/bin/python -m workflows.national.facilities.national_facility_chain_v5_trade_comparison
```

Focused regression tests:

```bash
.venv/bin/python -m pytest \
  tests/test_national_facility_inventory_v1.py \
  tests/test_national_facility_capacity_v3.py \
  tests/test_national_facility_assignment_v6.py \
  tests/test_national_product_chain_v3.py \
  tests/test_national_facility_chain_scenarios_v5.py \
  tests/test_forestry_osecan_handoff_v6.py \
  tests/test_forestry_osecan_scenarios_v5.py \
  tests/test_facility_residue_supply_v5.py \
  tests/test_national_residue_curve_v5.py \
  tests/test_national_facility_chain_scenarios_v5_trade.py \
  tests/test_facility_residue_supply_v5_trade.py \
  tests/test_national_residue_curve_v5_trade.py \
  tests/test_forestry_osecan_handoff_v6_trade.py \
  tests/test_national_facility_chain_v5_trade_comparison.py \
  tests/test_four_province_bucking_pilot.py \
  tests/test_four_province_biomass_allometry.py -q
```

Every published artifact directory contains its report, JSON summary, and
SHA-256 manifest. The handoff manifest records the OSeCAN tree hash check and
the exact input hashes. Rebuild against data commit `e9fc837` to reproduce the
frozen numbers; later data commits may supersede the inputs.

## 12. Non-claims and references

**Non-claims.** This package does not claim: a legal AAC or allowable cut; a
sustainable harvest level; a legal THLB; current mill operating status; observed
mill capacity or throughput; measured mill product yields; a calibrated residue
recovery factor; an interprovincial trade forecast; a transport, cost, revenue,
or net-value result; a facility investment or closure plan; an energy-policy
recommendation; an official OSeCAN scenario; or a public UI release.

**References.**

- `data/derived/national/facilities/national_facility_inventory_v1/NATIONAL_FACILITY_INVENTORY_V1.md`
- `data/derived/national/facilities/national_facility_capacity_v3/NATIONAL_FACILITY_CAPACITY_V3.md`
- `data/derived/national/facilities/national_facility_assignment_v6/NATIONAL_FACILITY_ASSIGNMENT_V6.md`
- `data/derived/national/facilities/national_product_chain_v3/NATIONAL_PRODUCT_CHAIN_V3.md`
- `data/derived/national/facilities/facility_residue_supply_v5/FACILITY_RESIDUE_SUPPLY_V5.md`
- `data/derived/national/facilities/national_residue_curve_v5/NATIONAL_RESIDUE_CURVE_V5.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v5/NATIONAL_FACILITY_CHAIN_SCENARIOS_V5.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v6/FORESTRY_OSECAN_HANDOFF_V6.md`
- `data/derived/national/facilities/forestry_osecan_scenarios_v5/FORESTRY_OSECAN_SCENARIOS_V5.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v5_trade/NATIONAL_FACILITY_CHAIN_SCENARIOS_V5_TRADE.md`
- `data/derived/national/facilities/facility_residue_supply_v5_trade/FACILITY_RESIDUE_SUPPLY_V5_TRADE.md`
- `data/derived/national/facilities/national_residue_curve_v5_trade/NATIONAL_RESIDUE_CURVE_V5_TRADE.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v6_trade/FORESTRY_OSECAN_HANDOFF_V6_TRADE.md`
- `data/derived/national/facilities/national_facility_chain_v5_trade_comparison/LOCAL_V6_VS_TRADE_V5_COMPARISON.md`
- `docs/four-province-biomass-allometry.md`
- `docs/bc-biomass-allometry.md`

**Freeze record.** Annotated tag `national-facility-chain-v2` marks parent
commit `64a6b83492f06f125fa6865a7986180689c3f703` and records data commit
`e9fc837adf66a4ac4006bc036126c458a0a23214`. Annotated tag
`national-facility-chain-v2.1` marks the trade v5 measured rebuild and records
data commit `c91ee9bd19442d239b15dadf2818d96bd1b6c253`. The tags are the immutable references for the
key result numbers in this model card. Documentation-only commits after a tag
do not change the frozen artifacts.
