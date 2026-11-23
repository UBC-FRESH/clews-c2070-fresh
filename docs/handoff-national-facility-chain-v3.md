# Handoff — National facility chain v3 (E1/E2 open-data coefficients)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Handoff and interpretation note; documentation plus v3.2 manifest metadata fix |
| Base freeze | `national-facility-chain-v3` |
| Documentation tags | `national-facility-chain-v3.1`; `national-facility-chain-v3.2` (manifest integrity fix) |
| Freeze commit | `029eef1d89014c0bda8af1dfa49e78a19ae58d4f` |
| Data commit | `d77472c7ac877b60c3c7833d718a3e1bafe593a7` for the v3 numbers; `ef64c903f57c2e89f12eca62a0c9a2be49d8a28d` for the v3.2 manifest fix |
| Supersedes | `national-facility-chain-v2` (donor product coefficients for nine provinces) |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Data dependency | Open data only; no DUA, request, or approval required |
| OSeCAN status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No code, model-value, UI, or official OSeCAN change; v3.2 corrects only manifest metadata |

This note is the short transfer document for a reader who needs to use the
frozen national facility chain v3 without re-deriving it. The authoritative
method card is `docs/model-card-national-facility-chain.md`; the artifact
reports under `data/derived/national/facilities/` remain the numeric source of
truth.

Tag `national-facility-chain-v3.1` is a documentation-only publication of this
note, the v3 model card, and the data pointer. It does not move v3, rebuild an
artifact, or change a number.

Tag `national-facility-chain-v3.2` fixes the sole stale manifest metadata
entry in the v3 data commit: the `facility_residue_supply_v6` manifest now
records the committed `summary.json` size and SHA-256. All 66 output entries
across the nine v3 artifact manifests verify. No numeric artifact, code, UI,
or official OSeCAN file changed, and v3/v3.1 are not moved.

## 1. What changed in v3

Version 3 completes the province-specific coefficient coverage. All 13
jurisdictions now use either an E1 measured or an E2 open-data imputed
solid/pulp/other product share and total harvest-residue rate. **No donor
provincial product coefficient is applied.** The nine provinces that used donor
product coefficients in v2 are now covered by E2 imputation, and their E2 stems
carry CFS branch and foliage residue. BC, QC, NB, and YT keep E1 measured
coefficients.

The assignment layer is unchanged from v2. The chain is now:

```
inventory v1 -> capacity v3 -> assignment v6 -> product-chain v4
             -> facility residue v6 -> residue curve v6
             -> chain scenarios v6 -> OSeCAN handoff v7
```

The chain remains a static, single-period screening sequence. It does not model
forest growth, annual time paths, transport cost, revenue, net value, facility
entry/exit, or interprovincial trade. All results are screening estimates.

## 2. Frozen tags and artifacts

### 2.1 Tags

| Tag | Freeze commit | Data commit | Role |
|---|---|---|---|
| `national-facility-chain-v1` | `94c7501` | `6ca272e` | Superseded stem-only v1 chain; retained for audit |
| `national-facility-chain-v2` | `64a6b83` | `e9fc837` | Frozen local-processing v6 base with measured total residue for four provinces |
| `national-facility-chain-v2.1` | `cd0562c` | `c91ee9b` | Trade-v5 measured rebuild and local-v6 comparison addendum |
| `national-chain-sensitivity-v1` | `006e84e` | `ec2f7fc` | 16-run global screening of the 119 Mm³ chain |
| `national-facility-chain-v3` | `029eef1` | `d77472c` | 13-province E1/E2 open-data chain; current model base |
| `national-facility-chain-v3.1` | `0ecc1d2` | `d77472c` | Documentation superset: v3 model card, v3 handoff, data pointer; no artifact change |
| `national-facility-chain-v3.2` | v3.2 fix commit | `ef64c90` | Manifest integrity fix: correct `facility_residue_supply_v6/summary.json` metadata; no number change |

The v3 tag is the immutable reference for the v3 numbers. Documentation-only
commits after the tag do not change the frozen artifacts. v3.1 publishes the
v3 documentation at its own commit and does not move v3. v3.2 corrects only
the stale `facility_residue_supply_v6` manifest entry left by the v3 data
commit; it does not move v3 or v3.1, rebuild the numeric artifacts, or change
a number.

### 2.2 Stage artifacts

| Stage | Artifact id | Published path | Key report |
|---|---|---|---|
| Inventory | `national_facility_inventory_v1` | `data/derived/national/facilities/national_facility_inventory_v1/` | `NATIONAL_FACILITY_INVENTORY_V1.md` |
| Capacity | `national_facility_capacity_v3` | `data/derived/national/facilities/national_facility_capacity_v3/` | `NATIONAL_FACILITY_CAPACITY_V3.md` |
| Assignment | `national_facility_assignment_v6` | `data/derived/national/facilities/national_facility_assignment_v6/` | `NATIONAL_FACILITY_ASSIGNMENT_V6.md` |
| Product chain | `national_product_chain_v4` | `data/derived/national/facilities/national_product_chain_v4/` | `NATIONAL_PRODUCT_CHAIN_V4.md` |
| Facility residue | `facility_residue_supply_v6` | `data/derived/national/facilities/facility_residue_supply_v6/` | `FACILITY_RESIDUE_SUPPLY_V6.md` |
| Residue curve | `national_residue_curve_v6` | `data/derived/national/facilities/national_residue_curve_v6/` | `NATIONAL_RESIDUE_CURVE_V6.md` |
| Chain scenarios | `national_facility_chain_scenarios_v6` | `data/derived/national/facilities/national_facility_chain_scenarios_v6/` | `NATIONAL_FACILITY_CHAIN_SCENARIOS_V6.md` |
| OSeCAN handoff | `forestry_osecan_handoff_v7` | `data/derived/national/facilities/forestry_osecan_handoff_v7/` | `FORESTRY_OSECAN_HANDOFF_V7.md` |
| OSeCAN scenarios | `forestry_osecan_scenarios_v6` | `data/derived/national/facilities/forestry_osecan_scenarios_v6/` | `FORESTRY_OSECAN_SCENARIOS_V6.md` |

Each published artifact directory contains a report, a JSON summary, and a
SHA-256 manifest. Rebuild against data commit `d77472c` to reproduce the frozen
numbers. At v3.2, all 66 output entries across the nine manifests verify; the
v3.2 data commit `ef64c90` changes only the
`facility_residue_supply_v6/manifest.json` `summary.json` metadata entry. A
rebuild still regenerates identical numeric files and the same pre-wrapper
manifest metadata, so the committed v3.2 manifest is the integrity reference.

## 3. Province coverage: E1 measured and E2 imputed

### 3.1 Coverage

| Jurisdiction | Tier | Evidence |
|---|---|---|
| BC | E1 measured | 347 stems; local Kozak taper; 100.0% taper-supported |
| QC | E1 measured | 85 stems; local Schneider taper; 74.4% taper-supported |
| NB | E1 measured | 106 stems; QC Schneider donor taper; 37.0% taper-supported |
| YT | E1 measured | 53 stems; BC Kozak donor taper; 98.9% taper-supported |
| AB | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| SK | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| MB | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| ON | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| NS | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| PE | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| NL | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| NT | E2 imputed | 36 stems; SCANFI v2 composition; donor geometry and taper |
| NU | E2 imputed | 36 stems; SCANFI v2 composition; no modelled 119 Mm³ harvest |

E1 totals **591 measured stems**. E2 totals **324 imputed stems** (36 per
province). E2 expansion is 1,000 stems/ha; every row is `measured=false`,
`imputed=true`, `synthetic=false`. E2 draws DBH and height deterministically
from the measured BC/QC/NB/YT lists. E2 is an explicit open-data imputation,
not fabricated facility or harvest data and not an observed province-wide tree
population.

**Donor tapers in 11 of 13 jurisdictions.** BC uses a local Kozak taper and QC
a local Schneider taper. NB and YT use QC/BC donor tapers, and all nine E2
provinces use a labelled BC Kozak or QC Schneider donor taper. Thus only 2 of
13 jurisdictions have a local taper.

### 3.2 Zero donor product coefficients, labelled donor splits

The `national_product_chain_v4` coefficient table has solid, pulp, other, and
total harvest-residue values for all 13 provinces from E1 or E2. The prior donor
values are retained only as audit columns in
`e1_e2_coefficient_replacement.csv` and `donor_vs_e2_coefficients.csv`; no
chain row applies them.

The following remain donor assumptions and carry explicit labels:

- within-solid SAW/PANEL split (`donor_within_class_split`);
- other-roundwood routing to PELLET/BIO because the facility chain has no OTHER
  class;
- pellet/bio split;
- harvest recovery fraction;
- all mill conversion ratios (`donor_conversion`).

Branch and foliage are added to E1 and E2 stems with CFS equations and
documented proxies (mountain hemlock, birch, black spruce for YT spruce). They
add dry mass and energy but no measured volume, so m³ feedstock routing remains
the stem taper residue.

### 3.3 No request, DUA, or approval dependency

The chain is complete on open data. No DUA, data request, or approval is
required to reproduce v3, and no jurisdiction is gated on restricted MAGPlot
Tier 2/3 or NFI ground-plot access. Optional requests could only improve
evidence tiers; they are not a roadmap dependency and do not unblock the model.
`docs/data-request-magplot-tier2.md` is an optional, non-blocking note.

## 4. 100 / 119 / 150 Mm³ results

All three documented provincial ceiling cases are fully placed with no
unassigned volume and no overflow. Global capacity does not bind; NT is the only
locally binding jurisdiction.

### 4.1 Assignment

| Scenario | Harvest Mm³ | Assigned Mm³ | Eligible capacity Mm³/yr | Utilization | Cross-border Mm³ | Proxy share | Facilities | Largest facility | HHI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 100.000 | 100.000 | 265.012 | 37.7% | 0.031 | 39.7% | 185 | 6.61% | 0.01354 |
| 119m | 118.977 | 118.977 | 273.468 | 43.5% | 0.036 | 39.9% | 208 | 6.80% | 0.01287 |
| 150m | 150.000 | 150.000 | 293.272 | 51.1% | 0.036 | 43.9% | 242 | 7.24% | 0.01350 |

The assignment is identical to v2. In the 119 Mm³ base, 118.941 Mm³ is local
(99.97%), 36,376 m³ is cross-border (0.031%), published capacity supplies
71.514 Mm³ (60.11%), proxy capacity supplies 47.463 Mm³ (39.89%), and 38.035
Mm³ of proxy capacity is scaled by the 1.2× floor. The 43,651 m³ NT floor gap
is a buffer, not unassigned harvest.

### 4.2 Products and residue

| Scenario | Sawlog Mm³ | Pulp M odt | Panel Mm³ | Pellet M odt | Bio GWh | Process PJ | Harvest gross PJ | Harvest recoverable PJ | Total gross PJ | Total recoverable PJ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 32.028 | 9.104 | 3.505 | 3.646 | 649.0 | 424.71 | 328.34 | 197.00 | 753.05 | 621.72 |
| 119m | 38.119 | 10.824 | 4.172 | 4.356 | 775.4 | 505.33 | 390.42 | 234.25 | 895.75 | 739.58 |
| 150m | 47.330 | 13.873 | 5.180 | 5.656 | 1,006.8 | 636.14 | 494.83 | 296.90 | 1,130.97 | 933.04 |

Relative to 119 Mm³, 100 Mm³ reduces assigned volume by 18.977 Mm³ and total
gross residue by 142.70 PJ; 150 Mm³ adds 31.023 Mm³ and 235.22 PJ. Products and
residue order as 100 < 119 < 150 within each metric.

**150 Mm³ note.** The 150 Mm³ case is the documented provincial ceiling table.
It is not the 1.0 or 1.2 m³/ha/yr operable-proxy intensity screen. Those
intensity screens are infeasible at 150 Mm³ (16.296 and 4.492 Mm³ deficits) and
must not be used to relabel or reject the documented 150 Mm³ case.

## 5. Change versus the donor-product chain (119 Mm³)

The percentages compare v3 E1/E2 with the frozen v2 donor-product chain at the
same harvest and assignment. The differences are coefficient effects only.

| Metric | v2 donor product | v3 E1/E2 | Delta | Delta % |
|---|---:|---:|---:|---:|
| Solid product Mm³ | 47.174 | 42.291 | −4.883 | **−10.35%** |
| Pulp product M odt | 9.527 | 10.824 | +1.297 | **+13.62%** |
| Process residue PJ | 514.614 | 505.333 | −9.281 | **−1.80%** |
| Harvest gross PJ | 313.640 | 390.418 | +76.777 | **+24.48%** |
| Harvest recoverable PJ | 195.433 | 234.251 | +38.818 | **+19.86%** |
| Total gross PJ | 828.254 | 895.751 | +67.496 | **+8.15%** |

Solid product is sawlog product 38.119 Mm³ plus panel product 4.172 Mm³; both
components scale by the same solid share and fall 10.35%. Pulp product is
10.824 M odt. Pellet (4.356 M odt) and bio (775.4 GWh) rise 4.40% because the
E2 other-roundwood share changes the PELLET/BIO feedstock. The process-residue
decrease is smaller than the solid-product decrease because process residue per
unit feedstock differs by class.

The v2 harvest-recoverable baseline (195.433 PJ) is the mixed reference
definition: 0.60 on total residue for the four measured provinces and the v1
donor stem-recoverable share for the nine donor provinces. In v3 all 13
provinces carry total residue, so 234.251 PJ is a uniform 0.60 on gross harvest
residue. The prior uniform-0.60 value was 188.184 PJ. Neither value is a
calibrated recovery factor.

At 119 Mm³ the v3 residue totals are 27.315 M odt / 505.333 PJ process and
21.104 M odt / 390.418 PJ gross harvest. Harvest recovery 35/60/75% is
136.646 / 234.251 / 292.813 PJ; process availability 0/25/50/75% is 0 / 126.333
/ 252.666 / 379.000 PJ. The harvest 60% case is 1.7884× the RSYC 119 Mm³ base
(`model_above_rsyc`), not a calibration.

## 6. OSeCAN handoff v7

Handoff v7 is a read-only forestry-side screening handoff from residue curve
v6. It preserves the all-zone v2 proxy weights and refreshes the province/case
totals from the E1/E2 chain. It maps to all 13 OSeCAN zones with no `UNMAPPED`
bucket.

### 6.1 National tiers (119 Mm³)

Adopted supply-curve reference points: T1 85.007 PJ, T2 109.399 PJ, T3
149.273 PJ, T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ national and
43.524 PJ BC; it is context, not a policy target.

| Stream | Case | PJ | Tier | Position |
|---|---|---:|---|---|
| Process | 0% | 0.000 | T1 | below T1 |
| Process | 25% | 126.333 | T3 | between T2 and T3 |
| Process | 50% | 252.666 | above_T4 | above T4 |
| Process | 75% | 379.000 | above_T4 | above T4 |
| Harvest | 35% | 136.646 | T3 | between T2 and T3 |
| Harvest | 60% | 234.251 | above_T4 | above T4 |
| Harvest | 75% | 292.813 | above_T4 | above T4 |

**Tier shift versus the donor-product chain.** The harvest 60% case moves
from T4 to above T4. The harvest 35% and 75% cases and all process cases keep
their tiers:

| Stream | Case | Donor product v6 | E1/E2 v7 | Changed |
|---|---|---|---|---|
| Harvest | 35% | T3 | T3 | no |
| Harvest | 60% | T4 | above_T4 | yes |
| Harvest | 75% | above_T4 | above_T4 | no |
| Process | 0/25/50/75% | T1/T3/above_T4/above_T4 | T1/T3/above_T4/above_T4 | no |

### 6.2 Zone results (119 Mm³)

Process 50% (PJ): B2 82.400, Q1 35.190, A1 33.330, O1 23.154, Q2 22.432,
A2 18.441, T1 18.179, B1 7.443.

Harvest 60% (PJ): B2 65.780, Q1 39.330, O1 25.915, Q2 25.071, A1 21.678,
T1 18.718, A2 11.994, S1 8.141, B1 5.941, O2 5.187, L1 3.546, M1 2.748,
N1 0.202.

BC uses 2050 zonal supply dispatch (`SUPBCWB1`/`SUPBCWB2`); AB, ON, and QC use
2050 zonal dispatch activity (`PWRBCW<zone>`) as a proxy; single-zone and
aggregate jurisdictions use membership weight 1.0. Every multi-zone weight is
`proxy_share=true`; no observed sub-provincial residue split is used.

Validation passed 20 checks with 0 failures, including row uniqueness, zone
coverage, province-weight sums, conservation to national totals, no combined
stream, explicit proxy labels, and an unchanged OSeCAN tree hash
(`80caa91c…`, 146 files before and after). All zone rows carry
`model_estimate=true`, `observed=false`, `policy_grade=false`, and
`official_osecan_changed=false`.

### 6.3 TSA24 sort status (not a pass)

The TSA24 industrial-sort check is a diagnostic, not a grade validation. Of 558
plots, **341** are comparable on a managed AU (61.1%); 15 more use the species
fallback. Mean per-plot TVD is **0.505** and pooled-share TVD is **0.433**
(pooled overlap 0.567). No tolerance was pre-registered
(`pass_fail=not_assessed_no_pre_registered_tolerance`), grade is
`not_assigned`, and `official_grade_validation=false`. The bucker is
dimension-only against TIPSY model output and has no defect, sweep, knot, rot,
or product-quality information. Do not call the chain sort-validated or
grade-validated. The optional `tipsy45` profile leaves the gap open (pooled TVD
0.4404, mean per-plot 0.5354).

## 7. Limitations and non-claims

- Nine jurisdictions are E2 imputed, not measured. The 36-stem lists, 1,000
  stems/ha expansion, and donor DBH/height geometry are explicit assumptions,
  not province-wide measurements.
- **Donor tapers in 11 of 13 jurisdictions.** Only BC (local Kozak) and QC
  (local Schneider) use a local taper. NB uses QC Schneider, YT uses BC Kozak,
  and all nine E2 provinces use a labelled BC Kozak or QC Schneider donor taper.
- E1 taper coverage is partial: BC 100.0%, QC 74.4%, NB 37.0%, and YT 98.9% of
  the eligible measured frame. Species without a mapped taper are excluded or
  proxied.
- Branch and foliage are CFS model estimates with documented proxies (mountain
  hemlock, birch, black spruce for YT spruce), not weighed components. They add
  dry mass and energy but no measured volume.
- **Sort recovery is not validated.** TSA24 has 341 of 558 comparable plots,
  mean per-plot TVD 0.505 and pooled TVD 0.433; `pass_fail` is
  `not_assessed_no_pre_registered_tolerance`, grade is `not_assigned`, and
  `official_grade_validation=false`. See §6.3.
- Within-class SAW/PANEL and PELLET/BIO splits, other-roundwood routing,
  recovery fractions, and mill conversion ratios remain donor assumptions and
  are labelled.
- No recovery factor is calibrated. The 0.60 case is a reference scenario.
- **Class-median and floor-scaled capacity are material.** Proxy capacity is
  39.9% of assigned volume and 38.92% of process residue; scaled proxy is
  31.16% of process residue. The 241 proxy records are class medians, not
  facility measurements. The 38.035 Mm³ floor-scaled proxy volume is not
  observed capacity. Published capacity mixes a 2015 infeed proxy, a 2026
  permit ceiling, converted pellet bulk volume, and converted sawn-lumber
  output; the bases are not interchangeable.
- **OSeCAN zone weights are proxies.** BC uses 2050 zonal supply dispatch; AB,
  ON, and QC use zonal dispatch activity (not harvest volume); the remaining
  jurisdictions use single-zone or aggregate membership. No observed
  sub-provincial residue polygon or split exists.
- **NU and NT are special cases.** NU has zero modelled harvest in the 119 Mm³
  case. NT has no eligible local capacity, so its 36,376 m³ is placed
  cross-border; the 43,651 m³ NT floor gap is a 1.2× buffer, not unassigned
  harvest.
- No transport cost, annual dynamics, economics, legal AAC/THLB, mill operating
  status, or policy-grade result is produced.
- The 100/119/150 Mm³ cases are planning allocations, not legal harvest levels.
- OSeCAN official files and all UI surfaces are unchanged; the handoff is not
  an official OSeCAN result.
- No DUA, data request, or approval is required; optional restricted records
  could only improve evidence tiers.

### 7.1 v2.1 trade and sensitivity disposition

`national-facility-chain-v2.1` (the measured trade-v5 chain and local-v6
comparison) and `national-chain-sensitivity-v1` (the 16-run global screening)
are v2-pinned addenda. They retain donor product coefficients for the nine
non-measured provinces and are **not rebuilt on v3 E1/E2**. Their numbers are
valid only inside the v2 model definition and must not be mixed with v3 totals
or described as v3 sensitivities. The v5 trade case in the model card is a
v2-pinned counterfactual, not the v3 base.

All rows are `model_estimate`, `observed=false`, `policy_grade=false`, and
`national_claim=false`. The 2050 OSeCAN values are external model context, not
policy targets.

## 8. Reproduction

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_inventory_v1
.venv/bin/python -m workflows.national.facilities.national_facility_capacity_v3
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v6
.venv/bin/python -m workflows.national.bucking.open_data_imputation
.venv/bin/python -m workflows.national.facilities.national_product_chain_v4
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v6
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v6
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v6
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v7
.venv/bin/python -m workflows.national.facilities.forestry_osecan_scenarios_v6
```

Focused regression tests are listed in the model card §11. The handoff itself
is documentation only; no code or model test is required to read it.

## 9. References

- `docs/model-card-national-facility-chain.md` — authoritative method card.
- `docs/four-province-biomass-allometry.md` — total-residue equations and
  recovery scenarios.
- `docs/bc-biomass-allometry.md` — BC branch/foliage implementation.
- `data/derived/national/facilities/national_product_chain_v4/NATIONAL_PRODUCT_CHAIN_V4.md`
- `data/derived/national/facilities/national_residue_curve_v6/NATIONAL_RESIDUE_CURVE_V6.md`
- `data/derived/national/facilities/national_facility_chain_scenarios_v6/NATIONAL_FACILITY_CHAIN_SCENARIOS_V6.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v7/FORESTRY_OSECAN_HANDOFF_V7.md`
- `data/derived/national/bucking/open_data_imputation_v1/report.md`
- `data/derived/national/bucking/tsa24_sort_validation_v1/VALIDATION_REPORT.md`
  — industrial-sort diagnostic; `pass_fail=not_assessed_no_pre_registered_tolerance`.
- `docs/data-request-magplot-tier2.md` — optional, non-blocking note only.

**Freeze record.** `national-facility-chain-v3` remains at model commit
`029eef1` with data commit `d77472c`. `national-facility-chain-v3.1` is an
annotated documentation tag at the commit that publishes this handoff, the v3
model card, and the data pointer; it records parent `029eef1` and data commit
`d77472c`. It does not move v3 or change an artifact.
`national-facility-chain-v3.2` is an annotated tag at the manifest-integrity
fix commit; it records that commit, data commit
`ef64c903f57c2e89f12eca62a0c9a2be49d8a28d`, and the unchanged v3/v3.1
references. It changes manifest metadata only and moves neither v3 nor v3.1.
