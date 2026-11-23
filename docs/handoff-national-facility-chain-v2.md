# Handoff — National facility chain v2

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Handoff and interpretation note; documentation only |
| Base freeze | `national-facility-chain-v2` |
| Trade addendum | `national-facility-chain-v2.1` |
| Sensitivity freeze | `national-chain-sensitivity-v1` |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Data dependency | Open data only; no DUA, request, or approval required |
| OSeCAN status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No code, model, UI, or official OSeCAN change in this handoff |

This note is the short transfer document for a reader who needs to use the
frozen national facility chain without re-deriving it. The authoritative
method card is `docs/model-card-national-facility-chain.md`; the artifact
reports under `data/derived/national/facilities/` remain the numeric source of
truth.

## 1. What the chain does

The chain is a static, single-period screening sequence:

```
inventory v1 -> capacity v3 -> assignment v6 -> product-chain v3
             -> residue curve v5 -> facility residue v5
             -> chain scenarios v5 -> OSeCAN handoff v6
```

It answers four limited questions:

1. Where can a 100/119/150 Mm³ national harvest case be placed on a bounded
   facility inventory, and how much is local versus cross-border?
2. How much of the placement uses published capacity versus a class-median
   proxy?
3. What products, process residue, and harvest residue follow when measured
   BC/QC/NB/YT coefficients replace donor coefficients?
4. Can the residue streams be handed to the 13 OSeCAN zones as a read-only
   sensitivity without changing the official energy model?

It does not model forest growth, annual time paths, transport cost, revenue,
net value, facility entry/exit, or interprovincial trade. All results are
screening estimates.

## 2. Frozen tags and artifacts

### 2.1 Tags

| Tag | Freeze commit | Data commit | Role |
|---|---|---|---|
| `national-facility-chain-v1` | `94c7501` | `6ca272e` | Superseded stem-only v1 chain; retained for audit |
| `national-facility-chain-v2` | `64a6b83` | `e9fc837` | Frozen local-processing v6 base with measured total residue |
| `national-facility-chain-v2.1` | `cd0562c` | `c91ee9b` | Trade-v5 measured rebuild and local-v6 comparison addendum |
| `national-chain-sensitivity-v1` | `006e84e` | `ec2f7fc` | 16-run global screening of the 119 Mm³ chain |

The v2 tag records the local-processing base. The v2.1 tag records the
trade-permissive sensitivity rebuilt on the same measured coefficients; it is
not a replacement base. The sensitivity tag records a screening experiment, not
a new model version.

No new tag is created by this handoff note. The tags above remain the immutable
references for the frozen numbers. Later documentation commits do not change
the frozen artifacts.

### 2.2 Stage artifacts

| Stage | Artifact id | Published path | Key report |
|---|---|---|---|
| Inventory | `national_facility_inventory_v1` | `data/derived/national/facilities/national_facility_inventory_v1/` | `NATIONAL_FACILITY_INVENTORY_V1.md` |
| Capacity | `national_facility_capacity_v3` | `data/derived/national/facilities/national_facility_capacity_v3/` | `NATIONAL_FACILITY_CAPACITY_V3.md` |
| Assignment | `national_facility_assignment_v6` | `data/derived/national/facilities/national_facility_assignment_v6/` | `NATIONAL_FACILITY_ASSIGNMENT_V6.md` |
| Product chain | `national_product_chain_v3` | `data/derived/national/facilities/national_product_chain_v3/` | `NATIONAL_PRODUCT_CHAIN_V3.md` |
| Residue curve | `national_residue_curve_v5` | `data/derived/national/facilities/national_residue_curve_v5/` | `NATIONAL_RESIDUE_CURVE_V5.md` |
| Facility residue | `facility_residue_supply_v5` | `data/derived/national/facilities/facility_residue_supply_v5/` | `FACILITY_RESIDUE_SUPPLY_V5.md` |
| Chain scenarios | `national_facility_chain_scenarios_v5` | `data/derived/national/facilities/national_facility_chain_scenarios_v5/` | `NATIONAL_FACILITY_CHAIN_SCENARIOS_V5.md` |
| OSeCAN handoff | `forestry_osecan_handoff_v6` | `data/derived/national/facilities/forestry_osecan_handoff_v6/` | `FORESTRY_OSECAN_HANDOFF_V6.md` |

Trade v2.1 artifacts:

- `data/derived/national/facilities/national_facility_chain_scenarios_v5_trade/`
- `data/derived/national/facilities/facility_residue_supply_v5_trade/`
- `data/derived/national/facilities/national_residue_curve_v5_trade/`
- `data/derived/national/facilities/forestry_osecan_handoff_v6_trade/`
- `data/derived/national/facilities/national_facility_chain_v5_trade_comparison/`

Sensitivity artifact:

- `data/derived/national/facilities/national_chain_global_sensitivity_v1/`
  (`NATIONAL_CHAIN_GLOBAL_SENSITIVITY_V1.md`, `main_effects.csv`,
  `overall_ranking.csv`, `level_sweep.csv`, `design_matrix.csv`,
  `summary.json`, `manifest.json`).

Each published artifact directory contains a report, a JSON summary, and a
SHA-256 manifest. Rebuild against the recorded data commit to reproduce the
frozen numbers.

## 3. Province coverage: measured versus open-data imputation

### 3.1 Coverage and evidence tiers

The chain covers 13 jurisdictions. Four have measured tree lists. The other
nine do not depend on restricted data; they are covered by an explicit
open-data imputation path and are labelled with their current evidence tier.

| Jurisdiction | Frozen v2 coefficient tier | Open-data inputs | Evidence note |
|---|---|---|---|
| BC | E1 measured | 347 stems | Local Kozak taper; 100.0% taper-supported |
| QC | E1 measured | 85 stems | Local Schneider taper; 74.4% taper-supported |
| NB | E1 measured | 106 stems | QC Schneider donor taper; 37.0% taper-supported |
| YT | E1 measured | 53 stems | BC Kozak donor taper; 98.9% taper-supported |
| AB | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| SK | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; open MAGPlot Tier 1 records where usable; donor distributions; explicit synthetic tree list | No restricted record used or required |
| MB | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; open MAGPlot Tier 1 records where usable; donor distributions; explicit synthetic tree list | No restricted record used or required |
| ON | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| NS | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| PE | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| NL | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| NT | E3 donor; E2 path | SCANFI v2 structure; NFI standard-report summaries; donor distributions; explicit synthetic tree list | No restricted record used or required |
| NU | E3 donor; E2 path | None needed | No modelled harvest in the 119 Mm³ case |

Total measured stems: **591** (BC 347, QC 85, NB 106, YT 53). The measured
pilot replaces 16 coefficients: four (solid share, pulp share, other-roundwood
share, gross harvest-residue rate) for each of four provinces.

Evidence tiers used by this handoff:

- **E1 measured tree list.** Individual stems with measured species, DBH, height,
  live/dead status, and expansion weights. The four-province bucking pilot is
  E1; it does not measure mill output or province-wide species populations.
- **E2 open-data imputation.** Open SCANFI v2 inventory structure and NFI
  standard-report summaries, national DBH/height fits, donor product-share and
  residue distributions, and an explicitly labelled imputed or synthetic tree
  list. Every imputed field is flagged; no imputed value is reported as
  observed.
- **E3 donor aggregate.** The BC aggregate product shares and stem-residue
  coefficient applied directly, with no province-specific tree list.

The frozen v2 coefficients for the nine non-measured jurisdictions are **E3**.
The documented completion path is **E2**: it uses only open data and requires no
DUA, request, or approval. Applying E2 is an evidence upgrade, not a gate on
model completion. Optional restricted records would supply E1 evidence for a
subset of jurisdictions and would only improve the tier.

**What “synthetic/imputed” means here.** E2 combines open inventory structure,
open summary statistics, and donor distributions into a tree list whose species,
DBH, height, and expansion assumptions are stated. The word “synthetic” refers
to that explicit imputation, not to fabricated facility or harvest data.

### 3.2 Why the donor block matters

At 119 Mm³, the four priority donor jurisdictions account for a material share
of the chain:

| Donor province | Process residue PJ | Harvest gross PJ |
|---|---:|---:|
| AB | 101.204 | 32.281 |
| SK | 19.208 | 6.127 |
| MB | 5.019 | 1.601 |
| ON | 59.656 | 19.028 |
| **Four-province total** | **185.087** | **59.037** |
| Share of national 119 Mm³ | 36.0% of 514.614 | 18.8% of 313.640 |

These values are model estimates computed with donor coefficients. They show
why open-data imputation for AB, SK, MB, and ON is the priority evidence
upgrade; an optional restricted-data request would be a secondary refinement,
not a dependency.

### 3.3 Branch and foliage

All measured stems carry stem, branch, foliage, and gross total residue.
Branch and foliage dry mass uses the vendored CFS equations (Lambert, Ung and
Raulier 2005; Ung, Bernier and Guo 2008; Open Government Licence – Canada).
The equations are applied only to measured live standing stems. Documented
proxies include mountain hemlock (conifer aggregate), birch (Betula aggregate),
and black spruce for YT spruce.

Branch and foliage add dry mass and energy but no measured volume. Products and
process residue are unchanged from product-chain v2; only harvest residue
changes.

### 3.4 Completion does not require external approval

The model is complete on open data. No DUA, data request, or approval is
required to finish the national facility chain, and no jurisdiction is gated on
restricted MAGPlot Tier 2/3 or NFI ground-plot access. Optional requests, if
pursued, can only improve evidence tiers; they do not unblock model completion
and must not appear as a roadmap dependency.

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

### 4.2 Products and residue

| Scenario | Sawlog Mm³ | Pulp M odt | Panel Mm³ | Pellet M odt | Bio GWh | Process PJ | Harvest gross PJ | Harvest recoverable PJ | Total gross PJ | Total recoverable PJ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100m | 35.688 | 8.025 | 3.906 | 3.493 | 621.8 | 432.43 | 264.48 | 164.72 | 696.92 | 597.15 |
| 119m | 42.520 | 9.527 | 4.654 | 4.172 | 742.7 | 514.61 | 313.64 | 195.43 | 828.25 | 710.05 |
| 150m | 53.646 | 12.006 | 5.872 | 5.354 | 953.0 | 649.26 | 385.20 | 241.25 | 1,034.46 | 890.51 |

Relative to 119 Mm³:

- 100 Mm³: −18.977 Mm³ assigned and −131.34 PJ total gross residue.
- 150 Mm³: +31.023 Mm³ assigned and +206.21 PJ total gross residue.

Products and residue order as 100 < 119 < 150 within each metric.

**150 Mm³ note.** The 150 Mm³ case is the documented provincial ceiling table.
It is not the 1.0 or 1.2 m³/ha/yr operable-proxy intensity screen. Those
intensity screens are infeasible at 150 Mm³ (16.296 and 4.492 Mm³ deficits) and
must not be used to relabel or reject the documented 150 Mm³ case.

### 4.3 Base versus trade sensitivity (119 Mm³)

The v6 base is local-first with the 1.2× provincial floor. The v5 trade case is
the earlier unscaled `published_plus_proxy` allocation; it is a counterfactual,
not a forecast.

| Metric | Local v6 | Trade v5 measured |
|---|---:|---:|
| Assigned | 118.977 Mm³ | 118.977 Mm³ |
| Local | 118.941 Mm³ (99.97%) | 97.075 Mm³ (81.59%) |
| Cross-border | 36,376 m³ (0.031%) | 21.902 Mm³ (18.41%) |
| Published | 71.514 Mm³ (60.11%) | 78.658 Mm³ (66.11%) |
| Proxy | 47.463 Mm³ (39.89%) | 40.319 Mm³ (33.89%) |
| Scaled proxy | 38.035 Mm³ | 0 |
| Facilities used | 208 | 263 |
| HHI | 0.01287 | 0.00755 |
| Capacity shortfall before floor | 30.282 Mm³/yr | 30.282 Mm³/yr |

Both cases have the same 30.282 Mm³ pre-floor shortfall. After the 1.2× floor,
the local case has a 43,651 m³ residual floor gap in NT; that gap is a buffer,
not unassigned harvest. The trade case runs before floor scaling, so its
after-floor shortfall is not the local case's 43,651 m³.

National products and residue are identical between the two cases at 119 Mm³.
The trade case moves the same process residue across provinces and facilities;
BC process 50% rises from 90.135 to 127.479 PJ. The v5 trade case is not
evidence of provincial self-sufficiency, and the v6 local case is not evidence
of autarky.

## 5. Stem-only versus total residue and recovery

### 5.1 119 Mm³ comparison

The measured total residue adds live branch and foliage to the stem taper
residue. The gross harvest residue is 16.954 M odt / 313.640 PJ; the stem-only
value is 127.860 PJ.

| Basis | Gross PJ | Recoverable at 0.35 PJ | Recoverable at 0.60 PJ | Recoverable at 0.75 PJ |
|---|---:|---:|---:|---:|
| Stem-only v4 | 127.860 | 44.751 | 76.716 | 95.895 |
| Measured total v5 | 313.640 | 109.774 | 188.184 | 235.230 |
| Delta (total − stem) | +185.780 | +65.023 | +111.468 | +139.335 |
| Ratio (total / stem) | 2.453 | 2.453 | 2.453 | 2.453 |

The stem-only recoverable values at 0.35 and 0.75 are arithmetic from the
published gross value and the recovery fraction; 0.60 is also reported in the
sensitivity level sweep. They are not separate calibrated artifacts.

The product-chain reference-recoverable value at 119 Mm³ is **195.433 PJ**
(10.564 M odt). It is **not** the uniform 0.60 scenario:

- four measured provinces apply 0.60 to total gross residue;
- nine donor provinces retain the v1 donor stem-recoverable share.

The uniform 0.60 total-residue case is **188.184 PJ** (10.172 M odt). The
difference is 7.249 PJ. Neither value is a preferred or calibrated recovery
factor; 0.60 is the existing reference scenario only.

### 5.2 Provincial total rates

| Province | Stem-only ODT/m³ | Measured total ODT/m³ | Ratio |
|---|---:|---:|---:|
| BC | 0.040577 | 0.161622 | 3.98 |
| QC | 0.059410 | 0.217507 | 3.66 |
| NB | 0.030341 | 0.187426 | 6.18 |
| YT | 0.123046 | 0.269689 | 2.19 |

These are gross total rates (stem taper plus live branch and foliage) from the
measured stem sample. They are not mill recovery rates and do not include
recovery losses.

## 6. OSeCAN handoff: paths and tier results

### 6.1 Paths

| Purpose | Path |
|---|---|
| 119 Mm³ local v6 handoff | `data/derived/national/facilities/forestry_osecan_handoff_v6/` |
| 119 Mm³ trade v5 handoff | `data/derived/national/facilities/forestry_osecan_handoff_v6_trade/` |
| 100/119/150 Mm³ scenario handoff | `data/derived/national/facilities/forestry_osecan_scenarios_v5/` |
| Local-v6 versus trade-v5 comparison | `data/derived/national/facilities/national_facility_chain_v5_trade_comparison/` |
| OSeCAN 2050 context (external) | `data/derived/national/clews/osecan/national_base_119m/dispatch_by_zone.csv` |

Key files in the 119 Mm³ handoff directory:

- `forestry_residue_handoff.csv` — zone × stream × case rows.
- `zone_summary.csv` — zone/case summary.
- `zone_allocation_weights.csv` — province-to-zone weights and evidence tier.
- `supply_curve_comparison.csv` — case versus T1–T4 and 2050 context.
- `tier_positions_stem_vs_total.csv` — stem-only versus total tier shifts.
- `summary.json`, `manifest.json` — validation and hashes.

Builders:

- `workflows/national/facilities/forestry_osecan_handoff_v6.py`
- `workflows/national/facilities/forestry_osecan_handoff_v6_trade.py`
- `workflows/national/facilities/forestry_osecan_scenarios_v5.py`
- `workflows/national/facilities/national_facility_chain_scenarios_v5.py`
- `workflows/national/facilities/national_facility_chain_v5_trade_comparison.py`

### 6.2 National tier results (119 Mm³)

Adopted supply-curve reference points: T1 85.007 PJ, T2 109.399 PJ,
T3 149.273 PJ, T4 213.538 PJ. The 2050 OSeCAN context is 152.923 PJ national
and 43.524 PJ BC; it is context, not a policy target.

| Stream | Case | PJ | Tier | Position |
|---|---|---:|---|---|
| Process | 0% | 0.000 | T1 | below T1 |
| Process | 25% | 128.654 | T3 | between T2 and T3 |
| Process | 50% | 257.307 | above_T4 | above T4 |
| Process | 75% | 385.961 | above_T4 | above T4 |
| Harvest | 35% | 109.774 | T3 | between T2 and T3 |
| Harvest | 60% | 188.184 | T4 | between T3 and T4 |
| Harvest | 75% | 235.230 | above_T4 | above T4 |

Stem-only versus total residue tier shifts:

| Stream | Case | Stem-only v5 | Total residue v6 | Changed |
|---|---|---|---|---|
| Harvest | 35% | T1 | T3 | yes |
| Harvest | 60% | T1 | T4 | yes |
| Harvest | 75% | T2 | above_T4 | yes |
| Process | 0/25/50/75% | T1/T3/above_T4/above_T4 | T1/T3/above_T4/above_T4 | no |

The harvest tier shift is caused by measured branch and foliage mass, not by
calibration. Process tiers are unchanged because products and process residue
are unchanged.

### 6.3 Zone results (119 Mm³)

Process 50% (PJ): B2 82.668, Q1 35.172, A1 34.362, O1 23.807, Q2 22.420,
A2 19.012, T1 18.407, B1 7.467.

Harvest 60% (PJ): B2 65.780, Q1 39.330, Q2 25.071, T1 15.247, A1 12.469,
O1 9.513, A2 6.899, B1 5.941, S1 3.676, L1 1.230, M1 0.961, N1 0.162.

The handoff maps all 13 zones with no `UNMAPPED` bucket. BC uses 2050 zonal
supply dispatch (`SUPBCWB1`/`SUPBCWB2`); AB, ON, and QC use 2050 zonal dispatch
activity (`PWRBCW<zone>`) as a proxy; single-zone and aggregate jurisdictions
use membership weight 1.0. Every multi-zone weight is `proxy_share=true`; no
observed sub-provincial residue split is used.

Validation passed 20 checks with 0 failures, including row uniqueness, zone
coverage, province-weight sums, conservation to national totals, no combined
stream, and explicit proxy labels. The official OSeCAN tree is hash-identical
(`80caa91c…`, 146 files before and after).

### 6.4 Trade handoff

The trade handoff keeps the same zone weights and national case totals. At
119 Mm³, BC process residue rises from 45.068 to 63.739 PJ at 25% availability
and from 90.135 to 127.479 PJ at 50%. National process and harvest cases, and
all case tiers, are unchanged. The official OSeCAN tree remains unchanged.

The 100/119/150 scenario handoff (`forestry_osecan_scenarios_v5`) is parallel
and read-only. It does not apply candidate activity limits and is not an
official OSeCAN result.

## 7. Global sensitivity ranking

### 7.1 Design

A 16-run Plackett–Burman/Hadamard screen at the 119 Mm³ provincial allocation.
Seven factors: assignment, product coefficients, residue basis, harvest
recovery, process availability, price scenario, and zone weights. The design is
resolution IV: main effects are clear of two-factor interactions among the seven
factors, but two-factor interactions are aliased in triplets and higher-order
interactions are not resolved. No foldover was run.

### 7.2 Overall ranking

| Rank | Factor | Mean normalized absolute effect | Material responses | Largest response |
|---:|---|---:|---:|---|
| 1 | Process availability | 0.5000 | 3 | National process residue |
| 2 | Residue basis | 0.4127 | 3 | Harvest gross residue |
| 3 | Price scenario | 0.1667 | 1 | National modeled margin |
| 4 | Harvest recovery | 0.1571 | 1 | Harvest recoverable residue |
| 5 | Product coefficients | 0.0933 | 5 | Total gross residue |
| 6 | Capacity / assignment | 0.0284 | 1 | BC process residue |
| 7 | Zone weights | 0.0000 | 0 | None |

The ranking is the mean normalized absolute main effect across six required
responses. It is a screening rank, not a variance decomposition.

### 7.3 Main effects

| Factor | National process PJ | Harvest gross PJ | Harvest recoverable PJ | Total gross PJ | National modeled margin M CAD | BC process PJ |
|---|---:|---:|---:|---:|---:|---:|
| Capacity / assignment | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | +28.006 |
| Product coefficients | −4.272 | −39.918 | −21.955 | −44.190 | 0.000 | −0.917 |
| Residue basis | 0.000 | +185.780 | +102.179 | +185.780 | 0.000 | 0.000 |
| Harvest recovery | 0.000 | 0.000 | +96.284 | 0.000 | 0.000 | 0.000 |
| Process availability | +390.232 | 0.000 | 0.000 | +390.232 | 0.000 | +164.127 |
| Price scenario | 0.000 | 0.000 | 0.000 | 0.000 | −5,860.648 | +0.002 |
| Zone weights | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |

Main-effect interpretation:

- **Process availability dominates** national process residue and total gross
  residue. The 0→75% contrast moves national process residue by 390.232 PJ.
- **Residue basis dominates** harvest gross residue (+185.780 PJ) and, with
  recovery, harvest recoverable residue.
- **Harvest recovery** is material only for the recoverable harvest response
  (+96.284 PJ for 0.35→0.75).
- **Product coefficients** are material across several responses but smaller
  than the leading physical factors.
- **Price scenario** is the only material screen for the national modeled
  margin; the physical chain factors do not re-price that diagnostic.
- **Capacity/assignment** changes BC process residue (+28.006 PJ) by moving
  facilities, but national process residue is unchanged.
- **Zone weights** leave the requested provincial and national responses
  invariant; only sub-provincial dispatch diagnostics move.

### 7.4 Sensitivity caveats

- The screen is at 119 Mm³ only; 100 and 150 Mm³ are not screened.
- The 0.60 recovery and 25/50% availability levels are descriptive sweep
  points, not Plackett–Burman contrasts.
- The donor + total-basis combination is a partial lower bound: donor provinces
  have no branch/foliage coefficient, so the measured branch/foliage increment
  is added only where a measured coefficient exists
  (`residue_basis_complete=false`).
- The chain summary `harvest_residue_recoverable_pj` is the product-chain
  residue-feedstock quantity, not the 0.35/0.60/0.75 recovery scenario. The
  sensitivity artifact defines recoverable as gross harvest residue × recovery.
- Process residue is facility-assigned and harvest residue is origin-province
  only. Total gross is the availability-adjusted process stream plus harvest
  gross; it is not a combined OSeCAN row.
- All values are model estimates; `observed=false` and `policy_grade=false`.

## 8. Priority questions for the Taco / energy team

These are the questions that most affect whether the forestry handoff is
interpreted correctly by the energy model. Until they are answered, report the
full scenario range and do not choose a single value.

### Q1. What is the recovery basis?

**Question.** Should OSeCAN use gross total residue × a uniform recovery
fraction, or the product-chain “reference-recoverable” definition (0.60 on
total residue for the four measured provinces, donor stem-recoverable share for
the nine donor provinces)? Is 0.60 a reference only, or is there a calibrated
recovery basis the energy team expects? Should branch and foliage be counted as
energy feedstock at all?

**Why it matters.** At 119 Mm³, uniform 0.60 gives 188.184 PJ; the mixed
product-chain definition gives 195.433 PJ; the 0.35–0.75 range spans
109.774–235.230 PJ. The harvest tier moves from T1/T1/T2 to T3/T4/above_T4 when
total residue replaces stem-only residue. The answer changes the tier position,
not just the quantity.

**What would resolve it.** A documented recovery definition, the evidence grade
behind it (survey, mill trial, literature, or assumption), and whether it is
province-, species-, or stream-specific.

### Q2. What process-availability fraction should be used?

**Question.** What fraction of mill process residue is actually available to
the energy system in the OSeCAN 2050 context? Are the 0/25/50/75% screening
cases acceptable, or is there an OSeCAN-specific availability parameter? Should
availability be applied by residue class (SAW, PULP, PANEL, PELLET, BIO) or
uniformly across all classes?

**Why it matters.** Process availability is the top-ranked global sensitivity
factor. The 0→75% contrast moves national process residue by 390.232 PJ, and
the 50% case alone is 257.307 PJ. A single availability assumption can dominate
the comparison with the 2050 context (152.923 PJ national).

**What would resolve it.** The OSeCAN parameter or source, its units and
denominator, whether it is a technical availability or an economic
dispatch outcome, and whether it varies by zone or year.

### Q3. What biomass and waste incineration assumptions apply?

**Question.** Does OSeCAN include biomass or waste incineration beyond forestry
residue — for example municipal solid waste, agricultural residue,
construction/demolition wood, or black liquor? If so, is forestry residue
competing with those feedstocks for the same combustion capacity? Which
technologies consume solid biomass, and what heat rate, lifetime, and emission
assumptions apply?

**Why it matters.** The forestry handoff reports process and harvest residue as
separate streams and never combines them. If OSeCAN's biomass supply stack also
contains non-forestry waste or black liquor, the forestry tiers are not directly
comparable unless the denominators and technology set are aligned. Double
counting is possible if mill process residue is counted both as a forestry
supply and as an internal mill energy input.

**What would resolve it.** The OSeCAN biomass/waste technology list, feedstock
definitions, capacity/activity limits, and the treatment of internal mill
energy use.

### Q4. What industrial feedstock demand is assumed?

**Question.** Which industrial demands are in the OSeCAN demand stack — pellet
export, pulp, panel, bioenergy, liquid fuels, or other — and do they compete
with the energy system for the same residue? Should the facility-chain process
residue be net of internal mill use and already-committed industrial feedstock?
Are the chain's SAW/PULP/PANEL/PELLET/BIO classes consistent with OSeCAN's
demand sectors?

**Why it matters.** The facility chain assigns process residue to facilities by
class and province; it does not net out internal mill consumption or existing
industrial contracts. If OSeCAN assumes those volumes are already committed,
the available energy residue is lower than the handoff total. Conversely, if
OSeCAN adds new pellet or biofuel demand, the same residue may be double
counted.

**What would resolve it.** The OSeCAN industrial demand quantities, price
assumptions, and the accounting boundary between mill self-use, existing
industrial demand, and new energy demand.

### Requested response format

For each question, the forestry side needs: the chosen definition or parameter,
the evidence grade, the geographic and temporal scope, and whether it is a
model assumption or an observed value. Until then, the handoff will continue to
report the full 0.35/0.60/0.75 and 0/25/50/75% ranges and will not label any
case as preferred.

## 9. Optional restricted-data requests

`docs/data-request-magplot-tier2.md` is an **optional, non-blocking note**, not
a request and not a roadmap gate. The model does not require MAGPlot Tier 2/3,
NFI ground plots, a DUA, or any approval. The open-data path in §3 completes the
chain; a future request could only upgrade evidence tiers for some
jurisdictions. No request has been sent, no access has been granted, and no raw
plot data are in the parent repository.

## 10. Non-claims

This handoff does not claim:

- a legal allowable cut, AAC, sustainable harvest level, or legal THLB;
- current mill operating status, observed capacity, or observed throughput;
- measured mill product yields or a calibrated residue recovery factor;
- an interprovincial trade forecast, transport-cost result, or market
  equilibrium;
- revenue, cost, profit, net value, stumpage, tenure, or investment results;
- a facility expansion, closure, or entry plan;
- an energy-policy recommendation, an official OSeCAN scenario, or a change to
  the official OSeCAN tree;
- a public UI release or a production-ready national model;
- that the nine non-measured jurisdictions are represented by province-specific
  measurements or that their current E3 donor coefficients are observed;
- that restricted MAGPlot/NFI records, a DUA, a data request, or an approval
  are required to complete the model;
- that the 100/119/150 Mm³ cases are legal harvest levels rather than planning
  allocations.

All rows are `model_estimate`, `observed=false`, `policy_grade=false`, and
`national_claim=false`. The 2050 OSeCAN values are external model context, not
policy targets.

## 11. Reproduction and verification

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_inventory_v1
.venv/bin/python -m workflows.national.facilities.national_facility_capacity_v3
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v6
.venv/bin/python -m workflows.national.facilities.national_product_chain_v3
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v5
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v5
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v5
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v6
.venv/bin/python -m workflows.national.facilities.forestry_osecan_scenarios_v5
.venv/bin/python -m workflows.national.facilities.national_chain_global_sensitivity
```

Trade addendum:

```bash
.venv/bin/python -m workflows.national.facilities.national_facility_chain_scenarios_v5_trade
.venv/bin/python -m workflows.national.facilities.facility_residue_supply_v5_trade
.venv/bin/python -m workflows.national.facilities.national_residue_curve_v5_trade
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_v6_trade
.venv/bin/python -m workflows.national.facilities.national_facility_chain_v5_trade_comparison
```

Focused tests are listed in the model card §11. The handoff itself is
documentation only; no code or model test is required to read it.

## 12. References

- `docs/model-card-national-facility-chain.md` — authoritative method card.
- `docs/four-province-biomass-allometry.md` — total-residue equations and
  recovery scenarios.
- `docs/bc-biomass-allometry.md` — BC branch/foliage implementation.
- `data/derived/national/facilities/national_facility_chain_scenarios_v5/NATIONAL_FACILITY_CHAIN_SCENARIOS_V5.md`
- `data/derived/national/facilities/national_residue_curve_v5/NATIONAL_RESIDUE_CURVE_V5.md`
- `data/derived/national/facilities/forestry_osecan_handoff_v6/FORESTRY_OSECAN_HANDOFF_V6.md`
- `data/derived/national/facilities/national_chain_global_sensitivity_v1/NATIONAL_CHAIN_GLOBAL_SENSITIVITY_V1.md`
- `metadata/source-reviews/magplot.md`
- `metadata/source-reviews/nfi_ground_plot_data.md`
- `metadata/license-determinations/nfi-access.yaml`
- `metadata/license-determinations/requests/nfi-ground-photo-request.md`
