# Change Log

## 2026-11-23

- **Implemented the nine-province open-data imputation tree list and residue
  comparison.** Added `workflows/national/bucking/open_data_imputation.py`
  and `tests/test_open_data_imputation.py`; published
  `data/derived/national/bucking/open_data_imputation_v1/`. The artifact
  covers 13 provinces. BC/QC/NB/YT remain E1 measured (591 stems); AB/SK/MB/
  ON/NS/PE/NL/NT/NU are E2 `open_data_imputation` lists (324 stems, 36 per
  province) built from SCANFI v2 province species composition, deterministic
  weighted donor DBH/height draws from the measured BC/QC/NB/YT tree lists,
  and labelled BC Kozak or QC Schneider donor tapers. Imputed expansion is
  1000 stems/ha, a round representative density; imputed residue is
  stem-only. Every imputed row is `measured=false`, `imputed=true`,
  `synthetic=false`; no national claim, official grade, restricted data,
  DUA or request dependency. Tests enforce 13-province coverage, E1/E2
  provenance, local/donor taper labels, per-tree volume/residue balance, no
  NaN or negative core numeric fields, and deterministic regeneration.
  Facility coefficient and RSYC comparisons are screening checks and flag
  donor-vs-imputation divergence without calibration.

- **Corrected the national-chain plan to an open-data completion path.** Replaced
  `docs/data-request-magplot-tier2.md` with an optional, non-blocking note; it
  is not a roadmap milestone, gate, or model dependency. Updated
  `docs/handoff-national-facility-chain-v2.md` to define E1 measured / E2
  open-data imputation / E3 donor evidence tiers, state that non-open-tree-list
  provinces use open SCANFI/NFI summaries, donor distributions, and explicit
  imputed/synthetic tree lists, and state that no DUA, request, or approval is
  required to complete the model. Optional requests only improve evidence.
  Updated ROADMAP P7.21 to remove the request as a milestone. Documentation
  only; no code/model/UI change, no official OSeCAN change, and no unexpected
  `D`.

- **Wrote the national facility chain v2 handoff and the MAGPlot Tier 2/3 /
  NFI ground-plot data-request draft.** Added
  `docs/handoff-national-facility-chain-v2.md` covering the frozen
  `national-facility-chain-v2`, `national-facility-chain-v2.1`, and
  `national-chain-sensitivity-v1` tags and stage artifacts; the measured
  BC/QC/NB/YT coverage (591 stems) versus the nine synthetic/donor
  jurisdictions; the 100/119/150 Mm³ assignment, product, and residue results;
  the stem-only versus measured total residue recovery cases at 0.35/0.60/0.75
  (119 Mm³: 127.860 / 313.640 PJ gross; 44.751/76.716/95.895 versus
  109.774/188.184/235.230 PJ recoverable); the OSeCAN handoff paths, 119 Mm³
  tiers (process T1/T3/above_T4/above_T4; harvest T3/T4/above_T4), and the
  trade-v5 BC process shift; the 16-run global sensitivity ranking (process
  availability, residue basis, price scenario, harvest recovery, product
  coefficients, capacity/assignment, zone weights); the priority questions for
  the Taco/energy team; and non-claims. Added
  `docs/data-request-magplot-tier2.md` as a **draft, not sent** request for
  MAGPlot Tier 2/3 and NFI ground plots in AB, SK, MB, and ON, with requested
  fields, purpose, licence/DUA, storage boundary, and open questions.
  Documentation only; no code/model/UI change, no new tag, no public OSeCAN
  change, and no unexpected `D`.

- **Rebuilt the trade-enabled assignment-v5 chain with measured total residue
  and froze the v2.1 addendum.** Added
  `national_facility_chain_scenarios_v5_trade.py`,
  `facility_residue_supply_v5_trade.py`,
  `national_residue_curve_v5_trade.py`,
  `forestry_osecan_handoff_v6_trade.py`, and
  `national_facility_chain_v5_trade_comparison.py`, plus their contract tests
  and `tests/test_facility_residue_supply_v5.py` /
  `tests/test_national_residue_curve_v5.py` (the test paths named in the v5
  reports). The trade chain uses assignment v5 `published_plus_proxy`
  (unscaled capacity) with the same `national_product_chain_v3` measured
  coefficients and total residue scenarios as the local v5 chain. At 119 Mm3
  the trade case has 21.902 Mm3 cross-border (18.41%), 33.9% proxy, 263
  facilities, HHI 0.00755 and a 30.282 Mm3 pre-floor shortfall; the local v6
  base has 36,376 m3 (0.031%), 39.9% proxy, 208 facilities and HHI 0.01287.
  National products, process residue, harvest residue and all 0/25/50/75%
  and 35/60/75% scenarios are unchanged; facility-province process residue
  moves BC 180.270 -> 254.958 PJ (+74.687), AB 106.747 -> 41.207 PJ (-65.540),
  SK -10.584 PJ, ON -8.584 PJ and QC +11.247 PJ. OSeCAN handoff v6 trade keeps
  the same zone weights, national case totals and tiers; BC process 50% rises
  90.135 -> 127.479 PJ and the official OSeCAN tree remains hash-identical.
  Created annotated tag `national-facility-chain-v2.1` and recorded data commit
  `c91ee9bd19442d239b15dadf2818d96bd1b6c253`. No UI or public OSeCAN change; no unexpected `D`.

- **Froze and documented the national facility chain v2.** Replaced
  `docs/model-card-national-facility-chain.md` with the v2 measured
  total-residue card covering the inventory v1 → capacity v3 → assignment v6 →
  product-chain v3 → residue curve v5 → facility residue v5 → chain scenarios
  v5 → OSeCAN handoff v6 chain. The card records the BC/QC/NB/YT measured
  product-share and harvest-residue replacement, stem + branch/foliage total
  residue with 0.35/0.60/0.75 recovery scenarios and no chosen factor, the
  119 Mm³ results (harvest gross 313.640 PJ, reference-recoverable 195.433 PJ,
  process 514.614 PJ, total gross 828.254 PJ), the OSeCAN harvest tier shifts
  T1/T1/T2 → T3/T4/above_T4, taper and allometry proxy limitations, and
  non-claims. Created annotated tag `national-facility-chain-v2` at parent
  `64a6b83` and recorded data commit `e9fc837`. Documentation and tag only; no
  code, model, UI, or official OSeCAN change; no unexpected `D`.

- **Integrated four-province branch/foliage total residue and recovery
  scenarios into the measured facility chain.** Added
  `four_province_biomass_allometry.py`, the four-province CFS equation map, a
  new `allometry_coverage.csv` and `residue_recovery_scenarios.csv` in the
  measured pilot, and `docs/four-province-biomass-allometry.md`. All 591
  measured BC/QC/NB/YT stems now carry stem, branch, foliage and gross total
  residue with recoverable values at 0.35/0.60/0.75; 0.60 is a reference
  scenario, not a calibrated factor. Province gross total rates are BC
  0.161622, QC 0.217507, NB 0.187426 and YT 0.269689 ODT/m3 recovered versus
  stem-only 0.040577/0.059410/0.030341/0.123046. Added
  `national_product_chain_v3.py`, `national_facility_chain_scenarios_v5.py`,
  `facility_residue_supply_v5.py`, `national_residue_curve_v5.py`,
  `forestry_osecan_handoff_v6.py` and `forestry_osecan_scenarios_v5.py`.
  Products, process residue and feedstock routing are unchanged from v2/v4.
  At 119 Mm3, gross harvest residue rises from 127.860 to 313.640 PJ and
  recoverable harvest residue from 90.990 to 195.433 PJ. OSeCAN harvest tiers
  move from T1/T1/T2 at 0.35/0.60/0.75 to T3/T4/above_T4; process tiers are
  unchanged. The official OSeCAN tree remains hash-identical. Tests cover
  mapping/proxies, per-tree conservation, scenarios, v2 product equality,
  chain reconciliation, tier positions, labels and no NaN/negative values. No
  UI or public OSeCAN change; no unexpected `D`.

- **Integrated measured four-province bucking coefficients into the national
  facility chain.** Added `measured_bucking_coefficients.py`,
  `national_product_chain_v2.py`, `national_facility_chain_scenarios_v4.py`,
  `facility_residue_supply_v4.py`, `national_residue_curve_v4.py`,
  `forestry_osecan_handoff_v5.py`, and `forestry_osecan_scenarios_v4.py`.
  BC, QC, NB and YT now use the pilot's measured solid (sawlog), pulp
  (pulpwood), other (other roundwood) and gross harvest-residue ODT/m3
  coefficients; the other nine jurisdictions keep the v1 donor coefficients.
  Solid replaces SAW+PANEL feedstock, with the SAW/PANEL split, pellet/bio
  split, residue recovery and mill conversion ratios retained as labelled
  donor assumptions. At 119 Mm3 the measured chain changes sawlog product
  from 48.467 to 42.520 Mm3, pulp product from 7.590 to 9.527 M odt, process
  residue from 526.006 to 514.614 PJ, and gross harvest residue from 167.779
  to 127.860 PJ versus the donor chain. OSeCAN handoff v5 and scenario v4 are
  read-only and unchanged in the official tree. Tests cover conservation,
  measured/donor split, comparison and labels. Taper coverage and hardwood
  limitations are documented. No UI or public OSeCAN change; no unexpected
  `D`.

- **Added BC branch and foliage allometry to the measured-tree residue ledger.**
  Added `workflows/national/bucking/bc_biomass_allometry.py`, the vendored CFS
  tree-level DBH + height coefficient table
  (`workflows/national/bucking/rules/cfs_biomass/`), a BC species map, and
  `docs/bc-biomass-allometry.md`. The CFS table is Open Government Licence -
  Canada and implements Lambert et al. (2005) and Ung et al. (2008). BC measured
  stems now carry `branch_odt`, `foliage_odt`, `branch_foliage_odt`,
  `stem_residue_odt`, gross `total_residue_odt`, and
  `recoverable_residue_odt` at the existing 0.60 pilot recovery factor; stem
  residue remains separate. The national-sort gross total is 0.161622 ODT/m3
  (branch+foliage 0.121045), the 0.60-recoverable total is 0.096973 ODT/m3, and
  the prior stem-only result is 0.040577 ODT/m3. The facility gross coefficient
  is 0.076226 ODT/m3, so the gross total is +112.03% and the recoverable total
  is +27.22% versus facility; both are reported, neither is silently chosen.
  Non-BC synthetic rows keep stem residue only and leave branch/foliage empty,
  never zero. Added `bc_residue_allometry.csv` and
  `bc_residue_allometry_summary.csv`; refreshed the v2 ledger, schema, report,
  summary, and manifest. Tests: equations, all-species positive values, group
  fallback, domain guards, ledger conservation, gross/recoverable separation,
  evidence and labels, manifest hashes, and recomputation from the committed
  measured list. No UI/public OSeCAN change and no unexpected `D`.

- **Added a separate TIPSY 4.5 bucking profile and reran TSA24 sort validation.**
  Added `workflows/national/bucking/profiles.py`,
  `workflows/national/bucking/rules/tipsy45_profile.yaml`, and
  `tests/test_bucking_profiles.py`. The profile is opt-in
  (`bc_psp_validation.py --bucking-profile tipsy45`) and does not change the
  default `BuckingProfile(5.0, 7.5, 10.0)`. Geometry uses the published TIPSY
  4.5 nominal target lengths 13/11/8/6.3/5.1/5.0 m plus the 2.5 m documented
  deciduous base. FAN$IER 4.5 Help publishes no numeric default log prices or
  sort-specific relativities, so the price scenario is explicitly labelled
  `assumption`: it keeps the published BC Interior Log Market Report
  product-class prices and invents no sort-specific values. The full TSA24
  rerun writes `data/derived/national/bucking/bc_psp_validation_tipsy45_v1/`
  and the sort validation writes
  `data/derived/national/bucking/tsa24_sort_validation_tipsy45_v1/`. Compared
  with the default v1 result, pooled TVD rises 0.4329 to 0.4404 and mean
  per-plot TVD rises 0.5052 to 0.5354: the gap does **not** close. The profile
  removes all 7.5 m and 10.0 m non-nominal logs (4,171 logs), but the
  value-only dynamic program without a published FAN$IER length/grade price
  surface over-selects 13/11 m logs and under-selects the 8 m and 5.1 m
  classes. Volume bias remains positive (+2.9% TIPSY/bucked). A descriptive
  comparison is in
  `data/derived/national/bucking/tsa24_sort_validation_profile_comparison_v1/`;
  it is `not_assigned`, makes no official grade claim, and was not tuned.
  Tests: profile contract and comparison tests passed. No UI/public OSeCAN
  change and no unexpected `D`.

- **Added national bucking pilot v2 with a measured BC TSA24 PSP tree list.**
  Added `workflows/national/bucking/national_bucking_pilot_v2.py` and
  `tests/test_national_bucking_pilot_v2.py`. The v2 list replaces the ten
  synthetic BC trees from v1 with 347 measured FAIB PSP stems selected as the
  PHF-weighted median of each species x BEC zone x 2.5 cm DBH stratum; pooled
  stratum `PHF_TREE` is the expansion factor, preserving the source expansion
  total (1,188,462.589) exactly. DBH and height remain measured values and BC
  rows are labelled `measured_tree_list` with `synthetic=false`; the other 12
  jurisdictions keep the v1 `representative_synthetic` lists. BC is bucked with
  the BC interior TIPSY 4.5 dimension sorts and with the national pilot sorts.
  The TIPSY rerun reproduces the v0 full-population expanded banks closely
  (taper 98.7%, recovered logs 98.8%, stem residue 98.2%). National-sort BC
  solid share is 0.705 versus the facility-chain solid feedstock share 0.689
  (ratio 1.023); residue is 0.041 ODT/m3 versus facility harvest residue
  0.076 ODT/m3 (ratio 0.532), so the facility screen remains outside the 20%
  band on residue. Every row is `observed=false`, `policy_grade=false`,
  `national_claim=false`; `grade=not_assigned`, `sort_basis=dimension_only`.
  Outputs, selection audit, BC TIPSY logs/residue, v1-to-v2 comparison,
  facility comparison, population reconciliation, summary, report and
  checksummed manifest are under
  `data/derived/national/bucking/national_bucking_pilot_v2/`. Tests: 10 v2
  tests passed; 38 v1/v0/bucking tests passed. No UI/public OSeCAN change and
  no unexpected `D`.

## 2026-11-22

- **Added the Quebec pilot grade-rule rerun v1.** Added
  `workflows/national/bucking/qc_grade_pilot.py`, a 44-row pilot grade set
  (`rules/qc_pilot_grade_sorts.csv`), and `tests/test_qc_grade_pilot.py`. The
  rerun applies MERIS quality classes DER, F1-F4, PATE and AUTRE to the ten
  committed Quebec representative trees from
  `national_bucking_pilot_v1`, with explicit top-DIB, target-length and
  tree-defect thresholds. Every grade is `pilot_<class>` with
  `grade_status=pilot_assumption`; it is not an official Quebec grade, MERIS
  classification or Petro prediction. Dimensions are tagged
  `published_table`; quality mapping and prices are `assumption`. The
  dimension-only comparator (`qc_pilot_sorts.csv`) yields 11 logs, 0.873 m3
  recovered gross, 0.277 m3 residue, 0.680 sawlog+peeler share and 55.904 CAD
  value. The grade rules yield 18 logs, 1.002 m3 recovered gross, 0.148 m3
  residue, 0.895 sawlog+peeler share and 134.242 CAD value on the same 1.149
  m3 taper total; all per-tree and total volume balances are exact. No DER-
  class log is recovered from this small tree list, but the DER mapping is in
  the rule set. Outputs, grade distribution, scenario comparison, volume
  reconciliation, summary, report and checksummed manifest are under
  `data/derived/national/bucking/qc_grade_pilot_v1/`. Tests: 31 passed across
  the QC grade, QC Phase 0/2 and 13-jurisdiction pilot suites. No UI/public
  OSeCAN change and no unexpected `D`.

- **Added the 13-jurisdiction national bucking representative pilot v1.**
  Added `workflows/national/bucking/national_bucking_pilot_v1.py`, a national
  dimension-only pilot sort set
  (`rules/national_pilot_sorts.csv`), and
  `tests/test_national_bucking_pilot_v1.py`. The pilot builds ten
  representative synthetic trees per province from NFI coverage species
  composition and national DBH/height fits, with explicit documented
  composition assumptions for AB, ON, NS and NU. Taper dispatch is local
  Schneider (2013) for QC, local Kozak (2004) for BC, and a labelled donor
  taper elsewhere. Every row is `model_estimate=true`, `synthetic=true`,
  `observed=false`, `policy_grade=false`, `national_claim=false`;
  `grade=not_assigned` and `sort_basis=dimension_only`. The run emits
  `BuckedLog`, `ResidueLedger`, per-tree reconciliation, province summary,
  facility coefficient comparison, `summary.json`, report and checksummed
  manifest under
  `data/derived/national/bucking/national_bucking_pilot_v1/`. Volume balance is
  exact per tree and in total (130 trees, 211 logs, 22.086 m3 taper total,
  3.053 m3 residue). No UI/public OSeCAN change and no unexpected `D`.

- **Added the national bucking Phase 0/2 Quebec pilot.** Added
  `workflows/national/bucking/national_bucking_pilot.py`, `schneider2013.py`,
  `pilot_schema.py`, a versioned Schneider (2013) taper registry
  (`rules/qc_schneider2013_taper.csv`), a pilot dimension sort set
  (`rules/qc_pilot_sorts.csv`), and `tests/test_national_bucking_pilot.py`.
  The pilot runs a 24-tree synthetic Quebec tree list through the published
  Schneider (2013) fixed-effect taper and a value-maximising bucker, then emits
  `BuckedLog`, `ResidueLedger`, per-tree reconciliation, schema documentation,
  and a checksummed manifest under
  `data/derived/national/bucking/national_bucking_pilot_v0/`. Taper geometry is
  `published_table`; tree geometry, sorts, prices, density, moisture, chip and
  hog yields are labelled `assumption`. Scope is a pilot only: no national
  aggregation, official grade assignment, or operational supply claim.

- **Froze and documented the national facility chain v1.** Added
  `docs/model-card-national-facility-chain.md` covering purpose, scope, data
  sources and capacity evidence tiers, inventory/capacity/assignment, the
  product/residue chain, the 100/119/150 Mm3 cases, the v6 local-processing
  base versus the v5 `published_plus_proxy` trade sensitivity, the OSeCAN
  handoff v4, key results, limitations, run commands, and non-claims. Created
  annotated tag `national-facility-chain-v1` at parent `94c7501` and recorded
  data commit `6ca272e` (inventory v1, capacity v3, assignment v6, product-chain
  v1, residue curve v3, facility residue v3, chain scenarios v3, OSeCAN handoff
  v4). The 119 Mm3 v6 base assigns 118.977 Mm3 with 118.941 Mm3 local (99.97%),
  36,376 m3 cross-border (0.03%), 47.463 Mm3 proxy (39.89%), 208 facilities,
  HHI 0.01287, and largest-facility share 6.80%; the v5 sensitivity has
  21.902 Mm3 cross-border (18.41%) and 40.319 Mm3 proxy (33.89%). The
  100/119/150 total gross residue is 583.12 / 693.78 / 874.68 PJ and total
  recoverable residue is 542.46 / 645.40 / 813.69 PJ. OSeCAN handoff v4 maps
  all 13 zones with no `UNMAPPED` bucket: 119 Mm3 process 50% is 263.003 PJ
  and harvest 60% is 100.667 PJ, against a 2050 context of 152.923 PJ national
  / 43.524 PJ BC. Documentation only: no code, model, UI, or public OSeCAN
  change, and no unexpected `D`.

- **Added floor-primary assignment v6 and processing comparisons.** Added
  `national_facility_assignment_v6.py`, `facility_residue_supply_v3.py`,
  `national_residue_curve_v3.py`, `national_facility_chain_scenarios_v3.py`,
  `forestry_osecan_handoff_v4.py`, `forestry_osecan_scenarios_v3.py`, and
  `national_processing_v5_v6_comparison.py` with regression tests. Assignment
  v6 applies the 1.2x provincial floor to capacity v3 by scaling proxy
  capacity only in AB, SK, MB, and ON; published capacity is unchanged. The
  119 Mm3 floor case assigns 118.977 Mm3 with 118.941 Mm3 local (99.97%) and
  36,376 m3 cross-border (0.03%), versus v5 `published_plus_proxy` at
  21.902 Mm3 cross-border (18.4%). Proxy share rises 33.9% -> 39.9%, HHI
  0.00755 -> 0.01287, largest-facility share 1.82% -> 6.80%, and the
  provincial floor shortfall falls 30.28 Mm3 -> 43,651 m3 (NT only). Facility
  residue v3 keeps process residue at 28.433 M odt / 526.006 PJ but shifts the
  receiving-province distribution (BC -4.04 M odt, AB +3.55 M odt); scaled
  proxy residue is 31.3%. The v3->v4 OSeCAN handoff keeps national totals and
  zone weights unchanged while moving 119 Mm3 process 50% zone PJ (B2
  -34.25 PJ, A1 +21.11 PJ, A2 +11.68 PJ). All rows remain
  `model_estimate`, `observed=false`, `policy_grade=false`, and
  `official_osecan_changed=false`; no UI/public OSeCAN change and no
  unexpected `D`.

- **Added public AB/ON/QC mill-capacity extracts, capacity v3 and assignment
  v5.** Added `workflows/national/facilities/national_facility_capacity_v3.py`,
  `national_facility_assignment_v5.py`, and three test modules; published
  `data/derived/national/facilities/public_mill_capacity_2026/`,
  `national_facility_capacity_v3/` and `national_facility_assignment_v5/` with
  CSV/JSON summaries, reports and SHA-256 manifests. The Quebec MRNF June 2026
  register supplies 242 permitted mills; 19 category-8 paper rows have no
  permitted wood volume and are excluded rather than imputed, leaving 223
  volume-bearing rows (216 in relevant classes). Matching to inventory v1 is
  one-to-one by province, class, normalised name and distance: 49 Quebec mills
  matched existing records and 167 were added. Ontario's 2026 MNRF mill list
  (94 facilities) has no facility capacity and is used only for names, types,
  cities and coordinates. Five per-mill company disclosures add four
  GreenFirst Ontario sawmills and Tolko High Level, Alberta (2 matched, 3 new);
  their sawn-lumber output is converted at 2.36 m3/Mfbm and explicitly labelled
  output-basis. Capacity v3 carries 689 records: published 212 -> 433, proxy
  292 -> 241, published capacity 100.29 -> 166.61 Mm3/yr, proxy capacity
  120.24 -> 104.73 Mm3/yr; published share of capacity 45.5% -> 61.4%.
  Assignment v5 reproduces the 119 Mm3 case: assigned 118.977 Mm3, published
  m3 49.43 -> 78.66 Mm3, proxy m3 69.55 -> 40.32 Mm3, proxy share 58.5% ->
  33.9%, cross-border 23.69 -> 21.90 Mm3 (19.9% -> 18.4%), HHI 0.00737 ->
  0.00755. All rows remain `model_estimate`, `observed=false`,
  `policy_grade=false`; no legal/AAC claim is made; no restricted data and no
  UI/public OSeCAN change. The builder/tests fail closed on unexpected `D`;
  none was present. 19 new tests pass.

- **Added multi-scenario forestry residue OSeCAN handoff v1.** Added
  `workflows/national/facilities/forestry_osecan_scenarios.py` and
  `tests/test_forestry_osecan_scenarios.py`; published
  `data/derived/national/facilities/forestry_osecan_scenarios_v1/` with
  scenario residue, zone summary, candidate activity limits, supply-curve
  comparison, JSON summary, report and SHA-256 manifest. Applies the
  `forestry_osecan_handoff_v2` weights to the 100/119/150 Mm3 scenario
  process/harvest residue totals across all 13 OSeCAN zones with no
  `UNMAPPED` bucket. National process residue is 442.11 / 526.01 / 663.16 PJ
  and gross harvest residue is 141.02 / 167.78 / 211.53 PJ; the 0/25/50/75%
  process cases and 35/60/75% recovery cases are reported separately. The
  119m cases reproduce `national_residue_curve_v1` exactly at the national
  level (process 0 / 131.50 / 263.00 / 394.50 PJ; harvest 58.72 / 100.67 /
  125.83 PJ). Under the 119m scenario, B2 is 81.05 PJ at process 50% and
  31.02 PJ at harvest 60%. Comparison is context only: 119m process at 25%
  is 0.616 of the T4 upper and 0.860 of the 2050 national supply context,
  while process at 50% is 1.232 of T4; BC process at 25% is 1.015 of the
  BC zonal upper. The candidate activity-limit table (273 rows) is
  documented as read-only and not applied; only B1/B2 have official
  zone-specific 2050 limits (`SUPBCWB1` 3.605475 PJ, `SUPBCWB2` 39.918341 PJ).
  All rows are `model_estimate`, `observed=false`, `policy_grade=false`,
  `official_osecan_changed=false`; official OSeCAN files are hash-checked
  before and after the build. The builder/tests fail closed on unexpected
  `D`; none was present. No UI or public OSeCAN change.
  Added `national_facility_chain_scenarios.py` and
  `tests/test_national_facility_chain_scenarios.py`; published
  `data/derived/national/facilities/national_facility_chain_scenarios_v1/`
  with scenario assignment, product/residue, summary, comparison, JSON
  summary, report and SHA-256 manifest. Runs assignment v4
  `published_plus_proxy` with capacity v2 for each documented ceiling:
  assigned volumes are 100.000 / 118.977 / 150.000 Mm3, all fully placed with
  no unbounded sink and no overflow. Cross-border is 16.637 / 23.689 /
  40.136 Mm3 (16.64% / 19.91% / 26.76%); proxy share is 57.91% / 58.46% /
  59.32%; max facility share is 2.16% / 1.82% / 2.33%; HHI is 0.00859 /
  0.00737 / 0.00700. Product-chain-v1 province/class coefficients are applied
  to assigned class feedstock: process residue is 442.11 / 526.01 /
  663.16 PJ, harvest residue is 141.02 / 167.78 / 211.53 PJ gross and
  100.35 / 119.40 / 150.53 PJ recoverable, and total gross residue is 583.12 /
  693.78 / 874.68 PJ. Capacity utilization on eligible assignment capacity is
  52.0% / 61.8% / 78.0%; local capacity binds in AB, SK, MB, ON and NT, while
  global capacity does not bind. The 150m documented ceiling is reported
  separately from the 1.0/1.2 m3/ha/yr operable-proxy intensity screens, which
  remain infeasible at 150 Mm3 (16.296 / 4.492 Mm3 deficits) and are not
  conflated with it. All rows are `model_estimate`, `observed=false`,
  `policy_grade=false`; proxy capacity is a caveat and no legal/AAC claim is
  made. The builder/tests fail closed on unexpected `D`; none was present. No
  restricted data, UI or public OSeCAN change.

- **Upgraded national facility capacity evidence and reran assignment v4.**
  Added `pellet_map_2025.py`, `national_facility_inventory_v1.py`,
  `national_facility_capacity_v2.py`, `national_facility_assignment_v4.py`, and
  four regression test modules; published
  `canadian_biomass_pellet_map_2025/`,
  `national_facility_inventory_v1/`, `national_facility_capacity_v2/`, and
  `national_facility_assignment_v4/`. The public Canadian Biomass 2025 Pellet
  Mill Map current-producer table contributes 43 plants (4 proposed plants
  excluded), of which 10 match existing same-province PELLET records and 33 are
  new. Reported finished-pellet tonnes are converted at 650 kg/m3, inside the
  NRCan / CAN-CSA-ISO 17225-2 bulk-density range of 600-750 kg/m3, and labelled
  `published_capacity_converted`; 31 new records carry capacity, while Skeena
  Bioenergy is temporarily closed with no reported capacity. Converted values
  are finished-pellet bulk volume, not infeed-equivalent, and are excluded from
  the BC class-median anchor. Existing published BC infeed capacities are
  preserved. Inventory v1 has 519 records (212 published, 15 not published);
  capacity v2 has 292 class-median proxies. Primary assignment v4
  (`published_plus_proxy`) assigns 118.977 Mm3, with cross-border 23.689 Mm3
  (19.91%), proxy share 58.46%, max facility share 1.82%, and HHI 0.007373,
  versus v3 25.318 Mm3 (21.28%), 61.23%, 1.82%, and 0.007571. The 1.2x floor
  sensitivity retains the 43,651 m3 NT residual and raises published assignment
  from 39.98 to 44.21 Mm3. All rows are `model_estimate`, `observed=false`,
  `policy_grade=false`; no legal/AAC claim is made. No restricted data, UI, or
  public OSeCAN change; the builder and tests fail closed on unexpected `D`.

- **Added the national residue supply-curve sensitivity v1.** Added
  `workflows/national/facilities/national_residue_curve.py` and
  `tests/test_national_residue_curve.py`; published
  `data/derived/national/facilities/national_residue_curve_v1/` with
  national/province curve CSVs, OSeCAN demand-context CSV, JSON summary,
  report and SHA-256 manifest. Process residue from
  `facility_residue_supply_v1` is 28.43 M odt / 526.01 PJ and is exposed at
  0/25/50/75% availability by SAW/PULP/PANEL/PELLET/BIO class and total;
  harvest residue is 9.07 M odt gross / 167.78 PJ and is exposed at
  0.35/0.60/0.75 recovery, giving 58.72 / 100.67 / 125.83 PJ. The RSYC
  119 Mm3 base is 7.08 M odt / 130.98 PJ; the 0.60 harvest case is 0.7686
  of it. Process and harvest remain separate streams with no combined curve
  row. OSeCAN `stress_119m` 2050 context is 152.923 PJ national and
  43.524 PJ BC supply activity, with 41.108 / 11.700 PJ power dispatch; the
  official files are read-only and hash-checked. All rows are
  `model_estimate`, `observed=false`, `policy_grade=false`, `not_policy=true`;
  `rsyc_comparable=true` only on harvest recoverable rows. The builder/tests
  fail closed on unexpected `D`; none was present. No UI or public OSeCAN
  change.

- **Added facility-level residue supply v1 on assignment v3.** Added
  `workflows/national/facilities/facility_residue_supply.py` and
  `tests/test_facility_residue_supply.py`; published
  `data/derived/national/facilities/facility_residue_supply_v1/` with
  facility allocation, province residue, national summary, JSON summary,
  report and SHA-256 manifest. The builder joins `national_product_chain_v1`
  to assignment v3 `province_capacity_floor`: each origin province/class
  feedstock stream is allocated only to same-class facilities in proportion
  to assigned volume, local assigned class pools first with an explicit
  national same-class fallback. Facility process residue is 28.43 M odt /
  526.01 PJ at the documented RSYC dry LHV of 18.5 GJ/odt (201
  facilities; 188 with positive residue). Provincial harvest residue is kept
  separate and not facility-assigned: 9.07 M odt gross / 167.78 PJ,
  6.45 M odt recoverable / 119.40 PJ and 2.62 M odt unrecovered / 48.38 PJ.
  The RSYC 119 Mm3 base is 7.08 M odt / 130.98 PJ; model recoverable harvest
  residue is 0.7686 of that (`model_below_rsyc`). The familiar 129.6709 PJ
  figure is the documented RSYC 100 Mm3 base, not the 119 Mm3 value; both are
  shown. Process residue is never added to the RSYC stream without an explicit
  `combined` label (34.89 M odt / 645.40 PJ). Proxy and scaled-proxy capacity
  carry 64.00% of process residue (33.30% scaled); published capacity carries
  36.00%. The explicit national class fallback carries 4.81% of process
  residue, and 4.81% is generated in a province different from the origin,
  while assignment v3 cross-border volume is 0.03%. All rows are
  `model_estimate`, `observed=false`, `policy_grade=false`; proxy capacity
  remains a BC class-median screening fill. The builder/tests fail closed on
  unexpected `D`; none was present. No UI or public OSeCAN change.

- **Added the provincial capacity-floor sensitivity and assignment v3.**
  Added `workflows/national/facilities/national_facility_assignment_v3.py` and
  `tests/test_national_facility_assignment_v3.py`; published
  `data/derived/national/facilities/national_facility_assignment_v3/` with
  assignment/utilization/province/class/concentration CSVs, floor CSVs,
  v2-v3 comparison, summary, report and SHA-256 manifest. The floor is
  provincial harvest x 1.2 on relevant-class finite capacity. Four provinces
  are below it on published plus proxy capacity: AB, SK, MB and ON. Their 49
  proxy records are scaled by one province-wide factor each, with
  `capacity_status=proxy_harvest_scaled` and
  `capacity_evidence=class_median_proxy_scaled_to_harvest_floor`; all 179
  published values are unchanged. Floor shortfall falls from 33.70 Mm3/yr
  across five provinces to 43,651 m3/yr in NT, which has no eligible facility
  capacity. Assignment remains 118.98 Mm3 (zero unassigned); cross-border
  volume falls from 25.32 Mm3 (21.28%) in v2 to 36,376 m3 (0.03%) because
  local scaled capacity satisfies every province with facilities. Proxy share
  of assigned rises from 61.23% to 66.40%, maximum facility share from 1.82%
  to 7.11%, and HHI from 0.007571 to 0.013508. All rows are `model_estimate`,
  `observed=false`, `policy_grade=false`; capacity remains caveated. The
  builder/tests fail closed on unexpected `D`; none was present. No UI or
  public OSeCAN change.

- **Added the facility-level product/residue chain v1.** Added
  `workflows/national/facilities/facility_product_chain.py` and
  `tests/test_facility_product_chain.py`; published
  `data/derived/national/facilities/facility_product_chain_v1/` with 900
  facility-origin allocation rows, 65 province/class rows, 13 province rows,
  national summary, JSON summary, report and SHA-256 manifest. The builder
  joins `national_product_chain_v1` to assignment v2
  `published_plus_proxy`: each origin province/class feedstock stream is
  allocated only to same-class facilities in proportion to assigned volume.
  Local assigned class pools are used first (29 pools); 31 province/class
  pools with no locally assigned same-class facility use the explicit
  national same-class fallback (8.81 Mm3, 6.61% of facility feedstock).
  Facility feedstock totals 133.32 Mm3, products are 48.47 Mm3 lumber,
  7.59 M odt pulp, 5.30 Mm3 panel, 4.75 M odt pellet and 845,182 MWh
  electricity; process residue is 28.43 M odt. Provincial harvest residue is
  kept separate (9.07 M odt gross, 6.45 M odt recoverable) and is not assigned
  to facilities. Conservation checks pass for province/class feedstock and
  national products/process residue; no NaN or negative values. Facility HHI
  is 0.009905 (top-1 4.14%, top-5 13.50%); proxy-capacity facilities process
  55.06% of feedstock, compared with 61.23% proxy share in assignment v2, and
  proxy facilities hold 62.00% of used capacity. The RSYC comparison is
  unchanged at 0.7686 (`model_below_rsyc`). All rows are `model_estimate`,
  `observed=false`, `policy_grade=false`; proxy capacity is a BC class-median
  screening fill. The builder fails closed on any unexpected `D` in the parent
  or `data` repository status; none was present. No UI or public OSeCAN change.

- **Added the national facility capacity v1 layer and bounded assignment v2.**
  Added `workflows/national/facilities/national_facility_capacity.py`,
  `national_facility_assignment_v2.py` and their tests; published
  `data/derived/national/facilities/national_facility_capacity_v1/` and
  `national_facility_assignment_v2/` with CSVs, summaries, reports and SHA-256
  manifests. Capacity v1 carries 179 published BC values unchanged and fills
  292 blank relevant-class records with labelled BC class-median proxies
  (p25-p75 reported); 15 OTHER records remain blank because no anchor exists.
  Combined capacity is 216.08 Mm3/yr (95.84 published, 120.24 proxy). Public
  sources were screened and recorded with licence/access status; the Canadian
  Biomass pellet map is accessible but reports finished pellet tonnes/yr and
  was not adopted. Assignment v2 replaces the v1 unbounded sink with
  `published_only` (68.54 Mm3 assigned, 50.44 Mm3 unassigned) and
  `published_plus_proxy` (118.98 Mm3 assigned, zero unassigned); every
  assignment row has finite capacity, maximum single-facility share is 1.82%
  in the proxy scenario, and there is no unbounded facility. All rows are
  `model_estimate`, `observed=false`, `policy_grade=false`; no legal/AAC claim,
  no UI or public OSeCAN change.

- **Added the province break-even delivered-price artifact from
  national-economics v2.3.** Added
  `workflows/national/harvest/province_breakeven.py` and
  `tests/test_province_breakeven.py`; published
  `data/derived/national/harvest_basis/province_breakeven/` with 13 province
  break-even rows, summary JSON, report and SHA-256 manifest. Break-even is
  the central v2.3 modeled total cost: BC/QC component stacks are summed once;
  the ten aggregate-proxy provinces use the single StatCan NAICS 11331 total
  with no road, silviculture or haul add-on; NU uses the donor-total fallback.
  The donor_flat margin is positive for all 13 provinces (minimum NB
  +17.1615 CAD/m3); stumpage_scaled is negative for AB, MB, ON, QC and NB.
  Five negative scaled margins are retained unclipped. All rows are
  `evidence=model_estimate`, `observed=false`, `policy_grade=false`; no
  combined national net value, policy claim, UI or public OSeCAN input.

- **Applied the national-economics v2 review label fixes.** Proxy
  `price_basis` values no longer contain `delivered`
  (`stumpage_scaled_price_proxy_not_observed`,
  `donor_flat_price_proxy_not_observed`,
  `flat_BC_donor_price_proxy_not_observed`); BC's observed reported average
  keeps its `mill_delivered_observed_report_average` basis. Removed the
  `preferred` framing from v2/v2.1 code, reports, assumptions and the UI;
  `donor_flat` is base and `stumpage_scaled` is sensitivity. Provincial price
  rows now use `stumpage_rate_unit=CAD_nominal_per_m3` and carry per-province
  `source_publication_date` and `rate_basis`, so BC (2025) and NS (2024-07)
  are not labelled 2026. Rebuilt v2 and v2.1 artifacts and re-exported the live
  UI bundle; national diagnostics are unchanged at +33.1365 (base) and
  -16.1220 (sensitivity). Added ratio-3.3745, national -16.1220 and
  proxy-basis label tests. No tagged static bundle, Forest LP or public OSeCAN
  change.

- **Fixed the national-economics base/sensitivity framing as v2.1.** Added
  `workflows/national/harvest/national_economics_v2_1.py` and
  `tests/test_national_economics_v2_1.py`, publishing
  `data/derived/national/harvest_basis/national_economics_v2_1/` with
  `base_scenario=donor_flat` and `sensitivity_scenario=stumpage_scaled`.
  The v1 flat BC donor is the base; the v2 provincial stumpage-scaled proxies
  are a non-observed sensitivity. Outputs: 78 margin rows
  (13 jurisdictions x 2 scenarios x low/base/high), 28 summary rows, 14
  scenario-comparison rows, 39 tier-coverage rows, report, summary and
  manifest. National volume-weighted donor-flat diagnostic 33.1365 CAD/m3
  (low -94.7046, high 81.3168); scaled sensitivity -16.1220 (low -130.0389,
  high 38.4085). Both are `model_estimate_not_policy`; no combined national
  net value. All rows `evidence=model_estimate`, `observed=false`,
  `policy_grade=false`; 34 negative margins (13 donor-flat, 21 scaled) are
  unclipped. The comparison explains why stumpage/dues rates are not delivered
  prices and why scaled deltas are method effects only. No v1/v2 artifact, UI
  or public OSeCAN change.

- **Published the corrected BC open-data net-value model inputs as an open-data
  bundle.** Added `data/derived/national/harvest_basis/open_data_net_value/
  model_inputs/` with the exact primary 3-month 2025-12 price panel and separate
  1-month sensitivity panel (190 rows; 10 Interior weighted-average rows
  explicitly excluded), the full 945-row logging/haul cost grid with formula,
  parameter and source IDs, the 36 referenced cost parameters, the seven
  Interior basic silviculture rows plus all 15 Coast CAM CAD/ha exclusions, the
  exact QC 2022 SEPM 0.81 CAD/m3 procurement cost-spread reference, the
  zone/region/rate-group mapping, 18 cited raw sources, an SHA-256 manifest and
  a fail-closed reproducer. Labels are
  `input_publication=derived_model_inputs` and
  `raw_source_redistribution=false`; raw copyrighted PDFs are cited by URL,
  retrieval date and SHA-256 but not redistributed. The committed code
  reproduces median 48.84 CAD/m3, range -23.38 to 86.85, 168/945 negative base
  rows, South West ground 64.40, North West ground 57.76 and QC 0.81, with
  byte-identical core outputs. Added `planning/open-data-policy.md` and nine
  bundle/reproduction tests. No model result, UI or public OSeCAN change.

- **Added the open-data BC modeled-margin layer.** `workflows/national/harvest/
  open_data_net_value.py` combines one 2025-12 3-month published log-market
  vintage (Coast and Interior, Interior aggregates excluded) with the open
  logging/haul cost model and the published Interior basic silviculture CAD/m3
  allowance by forest zone. It emits 945 `model_estimate` rows and 21
  zone-system summaries in `open_data_net_value/`, plus a separate QC
  purchased-wood 0.81 CAD/m3 reference. Every row is `observed=false`,
  `policy_grade=false`, `price_basis=mill_delivered_reported_average`,
  `cost_basis=open_proxy_model`, and `net_value_status=model_estimate`; there is
  no combined BC+QC national total and no merged-ledger, model, UI, or public
  OSeCAN change. Negative modeled losses (182 base rows, mostly helicopter) are
  reported, not clipped. Added 14 focused tests and a report/manifest with
  caveats and one-at-a-time price, cost, silviculture, and 1-month-vintage
  sensitivities.

## 2026-11-21

- **Fixed the final v2d review findings.** Materialized the confidentiality-flag
  iterable in `procurement_benchmark_v1.py` so `qc_2022_peupliers_trituration`
  and `R09` retain `confidentiality_flag=three_respondents` and the
  caution note through `procurement_benchmark/` and
  `forest_economics_v2d/qc_procurement_reference.csv`. Added `period_year` to
  the BC `benchmark_id` and a 37th uniqueness check in `validate_outputs`, so
  all 1,852 annual/period price rows have distinct keys. Tightened the plausible
  range check to `1.0 <= minimum` and `maximum <= 1000.0`; made the teaching
  note year dynamic (`Nominal 2014-2015`); removed the unused `ledger_path`
  parameter, the second 7.9 MB CSV read, and the redundant non-finite loop; and
  derived the report date from the build timestamp. Added generator regression,
  exact-copy, `benchmark_id` uniqueness, relative-path/committed-manifest, and
  fail-closed negative tests. Added a DEPRECATED banner to
  `net_value_benchmark_v1.py` and marked its tests audit-only. Replaced the
  stale "Residual" framing in `planning/forest-sector-evidence-v2.md` with
  procurement-channel cost spreads, recorded 128 three-respondent flags and the
  BMMB Crown-only 35.7547 / teaching 50.6266 / all-in 38.2940 split with the
  22,200 m3 re-offer caveat, and corrected the QC evidence tier/revenue basis in
  `planning/net-value-v2d.md`. No merged-ledger, model, UI, or public OSeCAN
  change.

- **Rebuilt the v2d evidence layer as `forest_economics_v2d` and deprecated the
  old net-value artifacts.** Added
  `workflows/national/harvest/forest_economics_v2d.py`, which emits
  `data/derived/national/harvest_basis/forest_economics_v2d/` with
  `forest_economics_v2d_summary.csv`, `qc_procurement_reference.csv`,
  `bc_revenue_benchmark.csv`, `bc_cost_coverage.csv`, `bmmb_reference.csv`,
  `summary.json`, `FOREST_ECONOMICS_V2D_REPORT.md`, and `manifest.json`. The QC
  reference is an exact validated copy of `procurement_benchmark/` (SEPM 107.22
  purchase cost, 81.40/25.01/106.41 BGAD before/transfers/after, 25.82/0.81
  cost spreads); it is not revenue and is never presented as a net value. BC
  price ranges keep 1-month and 3-month vintages separate, exclude Interior
  `is_aggregate=true` rows from product sums, and retain 16 zero / 7 below-1 /
  17 above-1,000 flags. BMMB reports Crown-only primary (35.7547), teaching
  (50.6266), and all-in secondary (38.2940) nominal historical rates. BC cost
  coverage records road, bridge, culvert, silviculture, specified operations,
  and TOA allowances as partial, with general logging, truck haul, and EWB
  missing. Every summary row is `net_value_status=not_estimated`,
  `policy_grade=false`, and `net_value_is_combined_national=false`; no BC
  partial net value and no combined QC+BC monetary total are computed. Marked
  `net_value_v2d/` and `net_value_benchmark/` deprecated with pointers, updated
  `planning/net-value-v2d.md`, and replaced the old test with deprecation checks.
  Added 36 fail-closed checks. No merged-ledger, model, UI, or public OSeCAN
  change.

- **Corrected the BC log-market Coast share boundary and published the
  log-market metadata.** The Coast table parser now stops strictly before the
  footer row, rejects the `Species %` label as a grade share, and hard-fails if
  any `volume_share_pct` is outside 0–100. This fixes 12 second-growth grade-Y
  shares that had been overwritten by footer species volumes, including
  `1m2gc_sept_23` Fir grade Y (`125,344.26` → `0.14`), and adds a TSV/text
  regression fixture. Added `currency_basis=CAD_nominal`, explicit
  non-additivity rules for 1-month/3-month and overlapping 3-month vintages,
  the April 2023 Coast participant-expansion break note, lowercase
  `is_aggregate` tokens, `volume_share_status=not_applicable` for the
  schema-absent Interior field, LF line endings, and corrected the
  `PROVENANCE.md` statement that Interior `weighted_average` rows are emitted.
  Rebuilt 505 reports / 15,705 rows. No QC, BMMB, v2d output, model, UI, or
  public OSeCAN change; `net_value_v2d.py` only accepts the new lowercase
  `is_aggregate` token.
- **Corrected the QC benchmark from “net value” to a procurement cost spread.**
  Added `workflows/national/harvest/procurement_benchmark_v1.py`, which reads
  T2 purchase cost and T20 BGAD synthesis rows as two different procurement
  channels. SEPM is 107.22 purchase cost, 81.40 BGAD before, 25.01 transfers,
  106.41 after; the 25.82 and 0.81 CAD/m3 differences are now
  `cost_spread_before/after_transfers`, not residuals or net value. Every row is
  `net_value_status=not_estimated`,
  `interpretation=purchased_vs_bgad_procurement_cost_spread`, and
  `policy_grade=false`. The QC/M CAD volume sensitivity is removed. Regional
  rows cite REG `regional_2022`
  (`Compilation_donnees_regionalisees_2022.pdf`), preserve the R09 `n=3` flag on
  its row and both min/max bound rows, and state that bound columns are
  independent extrema. BMMB reports Crown-only as primary (9 full rates /
  74,550 m3 / 35.7547 CAD/m3), teaching/research separately (2 / 15,350 /
  50.6266), and all-in as secondary (11 / 89,900 / 38.2940), with dynamic
  per-year counts, per-year price bases, and the 2012 re-offer overlap caveat.
  Added `procurement_benchmark/`, `DEPRECATED.md` in the old artifact,
  `planning/procurement-benchmark.md`, planning correction banners, tests, and
  fail-closed validation. No merged-ledger, model, UI, BC log-market, or public
  OSeCAN change.
- **Built the v2d net-value evidence layer with QC and BC evidence separated.**
  Added `workflows/national/harvest/net_value_v2d.py`, which validates and
  references the QC 2022 survey benchmark without rewriting it; builds 1,852
  annual/period BC log-market price-range rows from 15,705 published cells,
  keeping one-month and three-month vintages separate, excluding Interior
  `is_aggregate=true` rows from product cells, and retaining zero/outlier flags;
  reports 51 observed BCTS standing-timber stumpage sales separately; and
  documents that BC appraisal allowances cover road, bridge, culvert,
  silviculture, specified operations, and TOA but not general logging or
  truck haul. BC `net_value_status=not_estimated` with reason
  `missing_logging_and_haul_cost`; no BC partial net value and no combined
  national net value are computed. Added the output bundle, planning note,
  tests, and 29 fail-closed checks. No merged-ledger, model, UI, or OSeCAN
  change.
- **Built the initial QC “net-value” benchmark and sensitivity (superseded by
  the procurement-cost correction above).** Added
  `workflows/national/harvest/net_value_benchmark_v1.py`, which uses the T2
  delivered purchase price and the T20 synthesis procurement rows to compute
  before/after-transfer residuals for SEPM (25.82 / 0.81 CAD/m3), hardwood
  sciage (5.69 / -14.53), and poplar trituration (-7.27 / -9.45); reports the
  seven-region SEPM procurement range with the provincial purchase price held
  constant; reports BMMB as a separate nominal 2011–2015 benchmark (18 sales,
  11 full rates, weighted 38.2940, median 36.6668, no grade split); and adds a
  three-case QC screening-volume sensitivity (572.8–880.9 M CAD before
  transfers, 18.0–27.6 M CAD after). All rows are `evidence=survey_benchmark`
  or `benchmark_historical`, `policy_grade=false`, and
  `net_value_status=benchmark_only`. Added the output bundle, planning note,
  and tests. No merged-ledger, model, UI, or public OSeCAN change; Forest LP v1
  objective unchanged.
- **Extended the forest cost/revenue ledger schema for quality and DBH curves.**
  Added optional `quality` (`F1`–`F4`, `DER`, `PATE`, `AUTRE`, `MIXED`, `NA`)
  and `dbh_cm` (positive finite, blank/`NA` allowed) to the ledger key and
  fail-closed validator. The normalized key now distinguishes QC quality classes
  that previously collided (`DER`/`F1`–`F4`/`PATE`), while a legacy 16-field CSV
  still loads with `quality=NA` and no DBH. Added quality/product/component/unit
  compatibility checks and restricted `dbh_cm` to model-input
  `delivered_log_revenue` species/DBH curves with `quality=NA`. Updated the
  template header, ledger planning note, merge header handling, and synthetic
  tests. The existing 940-row merged ledger loads unchanged. No MERIS/BCTS
  ingestion, model, UI, or public OSeCAN change.
- **Populated the QC MERIS quality and DBH dimensions and rebuilt the BC/QC
  merge.** The MERIS extractor now writes the 18-field ledger block with
  `quality` and `dbh_cm`, ingests both the courant and tendance price series
  (251 quality-class rows; DER→peeler, F1–F4→sawlog, PATE→pulpwood,
  AUTRE→mixed_log), and ingests the 12-species × 76-DBH × 2-series SEPM curves
  (1,824 `mixed_log` rows, `quality=NA`) from the canonical `Profil_2`. The QC
  block grows to **2,688 rows**; the merged `forest_cost_revenue.csv` grows to
  **2,888 rows** (200 observed BCTS + 2,688 model input). `load_ledger` passes,
  the old 17-row sample is excluded, and Forest LP v1 is re-run with all 34
  checks passing. Net value remains `not_estimated`; no observed delivered price
  or full cost was introduced. No public OSeCAN change.

## 2026-11-20

- **Added per-instance deployment isolation and committed the v1 release/handoff
  docs.** `deploy/run_instance.sh` launches one loopback server per analyst or
  team with a validated instance name, managed port, `0700` runtime, per-runtime
  lock, existing-process/port checks, and role rules (`--allow-runs` only for
  `analyst`). `deploy/clews-ui-analyst@.service` runs `%i` as
  `clews-ui-%i`, reads `PORT`/`ROLE` from `/etc/clews-ui/%i.env`, creates `0700`
  `RuntimeDirectory`/`StateDirectory`, and keeps secrets out of the unit. The
  runbook now documents ports, per-instance service accounts, Cloudflare Access
  mapping, publication, backup/retention, and v1.2 A11 acceptance. Added
  `docs/release-v1.md` and `docs/model-card.md`. A local two-instance smoke
  passed with distinct temporary runtimes/ports (`0700`), analyst
  `allow_runs=true`, public `read_only=true`/POST 403, and no solver or host
  service. The server remains single-tenant within one runtime; in-app accounts
  and authorization are still not provided. No public OSeCAN change.

- **Added deployment templates and non-authenticating role guards for the
  forestry UI.** `deploy/` now holds loopback-only systemd units for a public
  read-only instance (`127.0.0.1:8013`, role `public`) and an analyst writable
  instance (`127.0.0.1:8014`, role `analyst`, `--allow-runs`, two concurrent
  runs), plus a Cloudflare Tunnel ingress example and an operator runbook for
  Access, publication, backup, retention, DataLad/S3, and the no-in-app-auth
  boundary. The server accepts `--role` / `CLEWS_UI_ROLE` with `public`,
  `analyst`, `model_builder`, and `energy_modeller`; `public` and
  `energy_modeller` force read-only and reject `--allow-runs`, unknown roles
  fail closed, and `--read-only` now reports effective `allow_runs=false`.
  Added `/api/version` and `/api/roles`; `/api/health` reports the active role
  and effective flags. Roles select actions and do not authenticate callers;
  per-user runtime isolation remained a v1.2 deliverable at that stage. No
  service was deployed or started, and no public OSeCAN change was made.

- **Integrated the full QC MERIS model-input deck and the Forest-Sector LP v1
  synthesis.** Replaced the 17-row QC sample in the cost/revenue merge with
  `meris_qc_full/forest_cost_revenue_qc_model.csv`; the sample is excluded
  because its keys overlap the full block. The merged ledger now has **940
  rows** (BC 200 observed, QC 740 model input), **100 Crown / 840
  forest-sector** rows, and **100 paired mirrors**; `load_ledger` accepts all
  rows. Coverage now reports observed and model-input rows separately:
  **200 observed**, **740 model input**, **0 observed delivered-log revenue**,
  **127 model-input delivered-log rows**, and **0 observed QC harvest volume**.
  Added `workflows/national/harvest/forest_lp_v1.py` and
  `tests/test_forest_lp_v1.py`. The synthesis writes **117 allocation rows**
  (13 provinces x 3 levels x 3 cap cases); documented-ceiling sums are
  **100.000 / 118.977 / 150.000 Mm3**, base recoverable residue is **109.681 /
  130.981 / 166.801 PJ**, the 150M intensity cases remain infeasible
  (**16.296 / 4.492 Mm3** deficits), and every province-year keeps
  `net_value_status=not_estimated`. Model-input rows are counted but never
  monetised; no cross-perspective, cross-source, or combined monetary total is
  produced. Generated LP outputs stay under `out/forest_lp_v1/`. No public
  OSeCAN changes.

- **Staged the BC Timber Sales sale schedule and built a Crown stumpage ledger
  sample.** Added `workflows/national/harvest/bcts_bc.py`,
  `scripts/national/build_bcts_bc.py`, and `tests/test_bcts_bc.py`. The BCGW
  layer `WHSE_FOREST_TENURE.BCTS_SALES_SCHEDULE_SV` returned **162 sale blocks**
  (99 licences; **1,940,031.229 m3**; status P=140, D=22; FY2026=68,
  FY2027=94), staged without geometry at
  `data/derived/national/harvest_basis/bcts_bc/`. Parsed the **2026-11-01**
  Interior non-appraised tabular PDF into **350 rates** (5 tables x 7 zones x
  10 columns) and extracted the 2025 Interior/Coast MPS counts, coefficients,
  and representative specified-operation/tenure-obligation values. The
  `ledger_sample.csv` has **63 Crown `stumpage_base` rows** from Table 6-1 only
  (7 zones x 9 species), with `volume_m3=1.0` as an explicit unit-m3 rate
  weight, not a harvest volume. The layer has **no upset/winning bid/bonus
  field**, and no delivered log price or harvest/road/haul/silviculture actual
  was found; those remain gaps and no zero or proxy value was inserted. Source
  inventory registers `bcts_sales_schedule`, `bc_nonappraised_tabular`, and
  `bc_mps_update` under OGL-BC. No public OSeCAN changes.

- **Integrated the operable-proxy landbase into the provincial allocation LP
  and forestry UI.** The intensity screen now uses
  `upper_p = min(documented AAC/NFD/planning proxy, I * operable_proxy_ha)` with
  the 144.69 Mha base operable proxy, not the 166.28 Mha managed proxy. PE and
  NU have no operable proxy, so their documented caps are retained and flagged
  `operable_missing` (no zero substitution). Recomputing the 100/119/150 Mm3
  cases at I=1.0/1.2 gives 150 Mm3 deficits of **16.296 Mm3** and **4.492 Mm3**
  under the per-province min caps; the national operable envelope alone gives
  **5.310 Mm3** at 1.0 and clears at 1.2, because BC/ON/YT/NT documented caps
  bind below the operable product. 100 and 119 Mm3 remain feasible at both
  intensities. The UI adds an operable-proxy context card and Inputs table,
  keeps the managed proxy as a comparison column, and labels 150 Mm3
  conditional and the proxy as a screening landbase, not a legal THLB.
  Screenshots: `ui/qa/operable-{overview,allocation,inputs}.png`. No public
  OSeCAN changes.

- **Built the national merchantable/operable screening proxy from SCANFI v2
  and the managed-landbase proxy.** Added
  `scripts/national/build_operable_proxy.py`, which makes one national 30 m
  pass over SCANFI land cover, height, biomass, and crown closure, aggregates
  to 990 m blocks, and applies the existing managed-proxy share inside each
  province-ecoprovince. The default screen (height >= 5 m, biomass >= 20 Mg/ha,
  closure >= 10%) retains **332.60 of 422.48 Mha** treed area; the operable
  proxy is **144.69 Mha** (87.0% of the 166.28 Mha managed proxy; 0.39x NFI
  forest land). Sensitivity cases are 383.38 Mha (low) and 267.94 Mha (high).
  Outputs are under
  `data/derived/national/managed_landbase/operable_proxy/`, with a parent
  report copy at `docs/operable-proxy-report.md`. The product is a screening
  proxy, **not** a legal THLB, and applies no slope, road-access, age, or
  economic constraint. No public OSeCAN changes.

- **Added the THLB/operability intensity sensitivity and exposed the
  allocation-LP family in the UI.** The provincial allocation LP now writes
  per-intensity artifacts under
  `data/derived/national/harvest_basis/provincial_allocation/intensity_1.0/`
  and `intensity_1.2/` (feasible-case CSVs + summary JSON) and records
  `thlb_intensity_screen` deficits in the main summary/report. At 150 Mm³ the
  1.0 m³/ha/yr managed-proxy screen is infeasible by **6.113 Mm³**
  (143.887 Mm³ effective capacity); 1.2 is feasible (157.090 Mm³ effective
  capacity). 100 and 119 Mm³ are feasible at both intensities with zero deficit.
  The proxy is a 2016 tenure union, **not a THLB**, and the screen is not a
  legal AAC. The UI now exports `allocation_screening_ladder/{119m,150m}` as
  family `allocation_screening` (`lp_119m`, `lp_150m`) with
  `allocation=lp_*`, `allocation_role=reallocated`, `fleet_corrected=true`,
  `screening_only=true`, `policy_grade=false`, `pareto_eligible=false`, and
  `tradeoff_eligible=false`. Trade-off shows observed 119/150 vs LP 119/150 with
  observed/reallocated and feasibility badges, the THLB caveat, and provincial
  reallocation detail; Explore/Compare add the two context rows. Counts are now
  **53 scenarios / 18 families / 1 Pareto-eligible**. Regenerated
  `ui/public/data`, added allocation/exporter/UI tests, and captured Chromium
  screenshots (`ui/qa/allocation-*.png`). No public OSeCAN repository changes.

- **Integrated the provincial harvest allocation into the residue supply basis
  and reran the 119/150 Mm³ screening ladder.** Added
  `workflows/national/harvest/residue_factor_allocation.py`, which weights the
  province residue factors by allocated log volume:
  `f = sum_p(h_p * f_p) / sum_p(h_p)`. The new artifact
  `data/derived/national/harvest_basis/residue_factor_by_allocation/` compares
  the 100/119/150 Mm³ LP allocations, the observed 2024 mix, and the
  area-share reference. Weighted gross factors are **0.09881 / 0.09918 /
  0.10018 ODT/m³ log** (100/119/150) versus the area-share **0.11682**;
  weighted base recoverable factors are **0.05929 / 0.05951 / 0.06011**
  versus **0.07009**, and base supply is **109.68 / 130.98 / 166.80 PJ/yr**
  (the area-share base at 100M is 129.67 PJ/100M log). The runner contract now
  accepts optional `harvest.allocation`
  (`observed`/`lp_100m`/`lp_119m`/`lp_150m`/`none`); absent or `none` keeps the
  existing area-share factor, while LP cases use the weighted factor for the
  national/SUPBCWT cap and fail closed on missing province factors or
  non-conserving allocations. Ran 119M and 150M `screening_v0` with the LP
  allocation (same fleet model, two concurrent solves): objectives **1,447,033.42
  / 1,444,917.38 M$**, cumulative emissions **424.599186 / 422.927369 Mt**,
  2050 emissions **0.0 / 0.0**, national BCW 2050 **130.981149 / 166.801093
  PJ**, PWRBCW 2050 **35.209970 / 44.838970 PJ**. Against the observed 119M
  (1,445,655.44 M$/424.710127 Mt) the LP case is **+1,377.98 M$/−0.110941 Mt**;
  against the observed 150M (1,443,635.15 M$/422.690612 Mt) it is
  **+1,282.23 M$/+0.236757 Mt**. Lightweight run artifacts are committed under
  `data/derived/national/clews/osecan/allocation_screening_ladder/{119m,150m}/`.
  The national supply cap still binds from 2028. The allocation is a screening
  weighting input, not an enforced provincial AAC/THLB/cost constraint and not a
  forecast. No public OSeCAN repository changes.

- **Added the provincial harvest-allocation screening LP.** Implemented
  `workflows/national/harvest/provincial_allocation.py` (PuLP/CBC) to allocate
  the 100/119/150 Mm³ national ladder across the twelve reporting
  jurisdictions, with NU held at zero. Caps use documented AAC where available, with explicit
  proxies for MB (7.39–7.99 range midpoint 7.69), PE (0.46 planning volume),
  NT (36,376 actual-mean proxy), and NU (not assessed, held at zero). The
  primary objective minimizes L1 deviation from the 2024 observed provincial
  shares; a lexicographic second stage minimizes the maximum proportional
  increase, and a managed-landbase intensity cap is a 1.0/1.2 m³/ha/yr
  sensitivity, not a legal constraint. All three ladder cases solve; 200M is
  infeasible by 30.477 Mm³ against the model ceiling (15.400 Mm³ against the
  most generous documented ceiling). At 150M, BC (−3.790), NB (−5.542), and NT
  (−0.010) are reduced; AB, QC, and NL fill their ceilings and the rest is
  distributed proportionally. Added tests and the report/manifest under
  `data/derived/national/harvest_basis/provincial_allocation/`. No public
  OSeCAN repository changes.

- **Implemented the 150 Mm³ harvest feasibility gate and exposed
  `policy_test_v1` in the UI.** The runner now resolves the actual harvest path
  and rejects >150,000,000 m³/yr before copying the model, with a clear
  `feasibility` error; `--allow-infeasible` is the only diagnostic override and
  such runs are labelled `feasibility_status=fail`, `diagnostic=true`, and
  `pareto_eligible=false`. Added the documented partial AAC sum (157,343,387
  m³), NFD potential supply (207,680,142 m³), 150M conditional and 200M fail
  labels, and tenure/provincial caveats. Scenario summaries, manifests,
  `supply_caps.json`, and the static export now carry `feasibility_status`,
  `feasibility_notes`, `pareto_eligible`, and `harvest_exogenous`. The exporter
  keeps the 100/150/200 ladder as sensitivity/compare context with
  `pareto_eligible=false`; only `fleet_capacity__screening_119m_fleet` enters the
  default energy Pareto. UI rail/overview/Trade-off/Explore/Compare show
  AAC/NFD pass/conditional/fail badges and the caveat. `/api/constraints` now
  returns both `screening_v0` and `policy_test_v1` (draft,
  `policy_grade=false`); the contract accepts both IDs and rejects unknown IDs;
  the Run form sends `constraints.set_id` and shows the draft badge, with
  `policy_test_v1` disabled in static mode. Added contract/runner/exporter/UI
  tests, regenerated `ui/public/data` (51 scenarios / 17 families / 1 Pareto
  eligible), and recorded Chromium QA (`ui/qa/feasibility-gate.png`,
  `ui/qa/policy-selector.png`). No public OSeCAN repository changes.
- **Exported the fleet-corrected harvest ladder to the UI with an explicit
  exogenous-harvest caveat.** Added family `fleet_screening_ladder` with the
  100/150/200 Mm³ cases (the 119 Mm³ case remains the single canonical/base
  scenario `fleet_capacity__screening_119m_fleet`). Each ladder scenario carries
  `fleet_corrected=true`, `screening_only=true`, `policy_grade=false`,
  `harvest_exogenous=true`, and `tradeoff_eligible=true`; the exporter reads
  `harvest_m3_per_year` dimensions and metrics from each `run_summaries.json`.
  Regenerated `ui/public/data` to **51 scenarios / 17 families / 4 trade-off
  eligible**, updated boot guards, rail/overview labels (**canonical/base** vs
  **sensitivity**), Explore/Pareto/Compare, and pinned the caveat on Trade-off
  and Explore: *“Harvest is exogenous and free in this model; higher harvest
  lowers cost and emissions because no harvest cost/THLB/AAC constraint is
  modeled. This is a sensitivity ladder, not a policy frontier.”* The Pareto
  front remains mechanically computed: with the default objectives the 200 Mm³
  case dominates the 119/100/150 cases, which is expected when harvest is free
  and is not presented as a policy frontier. Updated exporter/app-guard tests,
  `ui/README.md`, and Chromium QA (`ui/qa/ladder-*.png`). No data/submodule or
  public OSeCAN repository changes.
- **Added the fleet-corrected harvest ladder.** Solved 100/119/150/200 Mm³
  log/yr under canonical `screening_v0` with the CER 2021 fleet model
  (`runtime/osecan_model`, tree `61dace92...f1fb0`) at two concurrent solves.
  Committed lightweight artifacts under
  `data/derived/national/clews/osecan/fleet_screening_ladder/{100m,119m,150m,200m}/`
  (`run_summaries.json`, `dispatch_by_zone.csv`, `manifest.json` only; data
  commit `ff36af9`). All four solves returned `optimal` with zero 2050
  emissions; objectives were **1,447,160.18 / 1,445,655.44 / 1,443,635.15 /
  1,440,837.42 M$** and cumulative emissions **423.050600 / 424.710127 /
  422.690612 / 421.029794 Mt**. The 119m case reproduced the canonical
  objective, emissions, and a byte-identical dispatch file
  (`eeba3efd...4da96`). National BCW 2050 was **128.530929 / 152.922679 /
  192.796393 / 257.061857 PJ** and PWRBCW 2050 **34.551309 / 41.108218 /
  51.826955 / 69.102626 PJ**; the national supply cap binds from 2028 in every
  case. The artifact commit made no UI or public OSeCAN repository changes; the
  UI export follows above. Raw model copies and solve directories were not
  committed.
- **Made the fleet-corrected 119 Mm³ run the canonical UI screening result.**
  Committed the lightweight artifact
  `data/derived/national/clews/osecan/fleet_capacity_119m/` (summary, dispatch,
  manifest only; data commit `fb877d2`) and exported it as
  `fleet_capacity__screening_119m_fleet`: objective **1,445,655.44 M$**,
  cumulative emissions **424.710127 Mt**, 2050 emissions **0.0**, with
  `screening_only=true` and `policy_grade=false`. The exporter now maps the
  run's flat summary schema, treats it as the sole canonical/publishable
  trade-off member, and drops its 12,977 `category=other` demand/audit rows from
  the supply-side columnar output (4,215 rows retained; count recorded in
  `excluded_categories`). All 15 legacy families (47 scenarios, including
  `efix_*`) carry `superseded_for_fleet=true`, remain visible for audit, and are
  excluded from default trade-off/Pareto eligibility; `EFix` emissions
  supersession is kept separate. Regenerated `ui/public/data` to 48 scenarios /
  16 families / 1 eligible, updated UI boot guards and copy, and added visible
  **fleet-corrected** and **pre-fleet model — not comparable** badges/banners.
  Added exporter and app-guard tests plus Chromium static QA evidence
  (`ui/qa/fleet-*.png`, `ui/qa/QA_REPORT.md`). Open gap: the former 26-scenario
  ledger has not been rerun under the fleet model, and Stage 1 `policy_test_v1`
  remains unrun. No public OSeCAN repository was changed.
- **Ingested the CER EF2026 2021 capacity fleet into the reproducible model.**
  `setup_osecan_model.py` now downloads/copies and SHA-256 verifies
  `electricity-capacity-2026.csv` (`8db7b814...f5fcc`), runs `osecan
  clean-capacity --scenario "Current Measures" --base-year 2021`, and records
  the cleaner summary plus its explicit oil/battery drop report in the manifest.
  A clean setup now writes `ResidualCapacity.csv` for 2021-2070 and all four
  capacity-bound files: 70 non-zero `PWR*` rows totalling 145.019845 GW in 2021,
  `PWRBIO` carrying 2.56888 GW, `PWRBSW`/`PWRBCW` at zero, coal retired by 2030,
  and build-rate rows for hydro/solar/wind/nuclear. Added patch
  `04_capacity_bounds.patch` so the coal bound stays above the observed stock
  before phase-out, and floored the runner's `nuclear_cap_gw` total-capacity
  row at residual capacity so a zero new-build cap does not strand the 14.5 GW
  fleet. Manifest tree hash moved to `61dace92...f1fb0`. The real 119 Mm3
  screening solve is feasible again: objective **1,445,655.44 M$**, cumulative
  emissions **424.710127 Mt**, 2050 emissions 0.0, versus the pre-capacity
  **1,411,447.21 M$ / 0.538720 Mt**. No public OSeCAN repository was changed.
- **Added milestone-1f session persistence, cancel/retry, and long-run UX.**
  Added `POST /api/runs/{id}/cancel` (queued removal or process-group SIGTERM →
  SIGKILL, `cancelled` terminal event, no monitor overwrite) and
  `POST /api/runs/{id}/retry` (validated immutable `scenario.json` snapshot,
  new run ID, 409 while active or snapshot missing). `GET /api/runs` now
  hydrates scenario levers, metrics, output availability, and cancel/retry
  flags. The Run UI rehydrates completed/active/terminal runs after reload,
  deduplicates session-rail entries, reconnects SSE/polling for queued/running
  jobs, exposes Cancel/Retry, shows cancelled states, computes elapsed time from
  server timestamps, and throttles a 250-line log tail. Added backend tests for
  queued/running/terminal cancel, snapshot retry, and no status overwrite, plus
  Chromium QA (`ui/qa/cancel.png`, `retry.png`, `reload-hydrated.png`). No data
  files or public OSeCAN surfaces were changed.
- **Added milestone-1e linked spatial/temporal exploration to the UI.** Map
  drill-down now links Canada → province → ecoprovince → RSYC tile with
  breadcrumbs, a selection panel (area, recoverable PJ, tile cells, explicit
  species-not-joined caveat, evidence), map click priority, and URL/hash
  restore/reset. Provinces, ecoprovinces, and tiles state explicitly that no
  documented C2070 zone mapping exists; B1/B2 remain authoritative point anchors
  and filter dispatch to their zones. A shared 2021–2070 year control with
  play/pause, vertical chart guide, map gauge, and dispatch-by-family snapshot
  is synced to the existing year-window sliders. No data, public OSeCAN, or
  dependency changes; QA evidence is in `ui/qa/QA_REPORT.md` and
  `ui/qa/map-linked.png` / `ui/qa/timeline.png`.
- **Fixed milestone-1c live-runner review findings.** `/api/runs/{id}/output`
  now skips runner `category=other` audit rows, returns 409 for every
  non-completed terminal status, validates `run_id`/`output_dir`, confines
  summary and dispatch reads to the run output directory (including symlink
  targets), and keeps `source` run-relative. Metrics now report
  `pwrbcw_2050` from `PWRBCW*`, `pwrbio_2050` from `PWRBIO*`, and
  `bio_share_2050` as `PWRBIO* ÷ PWR*` excluding `PWRTRN*`. SSE terminal
  drain loops through all 256 KiB event chunks. The Run UI locks create/queue
  during an active run, scopes SSE/poll/finalize callbacks by `run_id`, clears
  stale poll timers, resumes polling on non-terminal finalization, re-enables
  the form on terminal-read errors, validates health payloads, dedupes events by
  sequence, sources provenance from `/api/constraints`, derives zero tiers from
  T3/T4, and resets Run defaults before OSeCAN levers. Mobile tabs use a
  3-column grid at ≤440 px. Added regression tests for each backend case and
  refreshed Chromium QA evidence (`ui/qa/run-mobile.png` added). No data files
  or public OSeCAN surfaces were changed.
- **Wired the iteration-0 UI to the live FastAPI scenario runner (milestone
  1c).** Added `/api/constraints` and `/api/runs/{id}/output`, which normalize
  `run_summaries.json` + `dispatch_by_zone.csv` into the UI's columnar contract
  with summary metrics and explicit `screening_only` / `policy_grade` flags.
  Added the server `--runner-command` test/QA override while preserving the
  immutable `--scenario` snapshot and `--out` arguments. The UI keeps working
  from the committed static bundle when `/api/health` fails (visible **Static
  mode · no backend** badge and disabled mutations), and adds a **Run** tab that
  creates `clews.scenario.v1` scenarios from `screening_v0` defaults, queues
  them, streams SSE logs/status, fetches normalized output, and adds completed
  runs to a session rail, overview, trade-off, and dispatch. Failed runs add no
  results. OSeCAN exports remain the static fallback and gain **Queue this
  scenario** when the live API is writable. Added FastAPI tests for constraints,
  output (409 active / 404 absent), and the CLI runner override, plus Chromium QA
  against a fake runner (`ui/qa/run-static.png`, `run-complete.png`,
  `run-error.png`). No data files or public OSeCAN surfaces were changed.
- **Closed the OSeCAN runner reproducibility gap.** Added the committed patch
  set `workflows/ui/patches/01_emission_factors.patch`,
  `02_zonal_bc.patch`, and `03_tiered_supply.patch`, plus
  `workflows/ui/setup_osecan_model.py`. The setup script checks out public
  `deltae/C2070@40d5b43`, verifies and applies all three patch hashes,
  regenerates `SETs` and `data/params`, installs and hash-verifies the pinned
  CER EF2026 demand file before `osecan clean-demand "Current Measures"`, runs
  `osecan harmonize`, verifies the zonal/tier structure, corrected output-basis
  emission factors, and non-placeholder demand, and writes
  `model_manifest.json` with the source commit, patch hashes, demand
  provenance, interpreter, and deterministic model-tree hash. The runner now
  defaults only to `runtime/osecan_model`, verifies that manifest when present,
  and fails with a setup instruction instead of silently using `/tmp`. Added
  setup/patch/manifest tests, a dry-run from the rebuilt tree, and a real 119 Mm3
  solve that reproduces the prior local result exactly (objective 1,411,447.21
  M$, emissions 0.538720 Mt). No public OSeCAN repository or remote was
  modified.

- **Implemented the milestone-1b real OSeCAN runner behind the milestone-1a
  CLI.** Added `workflows/ui/osecan_runner.py` and extended
  `workflows/ui/run_scenario.py` with `--dry-run` and `--model-root`. The
  runner selects a disposable model copy (`runtime/osecan_model`), copies a
  reduced tree below the run output, applies the
  scenario parameter files, runs `osecan harmonize` and the GLPK solve, and
  writes normalized `run_summaries.json`, `dispatch_by_zone.csv`,
  `supply_caps.json`, and `manifest.json`. It never modifies the source model.
  A missing/incomplete model copy is an operational failure, not a fabricated
  result.
- **Added the canonical `screening_v0` constraint set** at
  `data/derived/national/clews/osecan/canonical_constraint_set.json`. It records
  the explicitly omitted reserve-margin, transmission, curtailment, and
  build-rate constraints; the corrected output-basis emission factors (gas
  0.1377, coal 0.261 Mt/PJ output, fuel factor x IAR); the RSYC supply constants;
  and the screening defaults. It distinguishes the future `policy_test_v1` and
  marks every result `screening_only`, `policy_grade: false`.
- **Added milestone-1b tests** for canonical-set validation, 100/119/150/200 Mm3
  cap math, tier-sum/no-double-count checks, reference-path versus custom
  harvest, fail-closed tier shortfall, and a real-model dry run. The milestone-1a
  runner stub test became a dry-run contract test. A real 119 Mm3 screening solve
  completed optimal (objective 1,411,447.21 M$, emissions 0.538720 Mt); it is
  reported in the run summary as screening-only, not policy-grade.

## 2026-11-19

- **Built the iteration-0 static data contract for the CLEWS/C2070 web UI.**
  Added `workflows/ui/export_iteration0.py`, which normalizes the OSeCAN
  `runs`/`cases` shapes, maps `objective_musd || objective` and
  `emissions_total_mt || emissions_mt.total`, normalizes solver statuses, and
  fails loudly on unknown shapes or units.  It writes 47 scenarios under
  `ui/public/data/` (26 publishable post-fix/tradeoff scenarios; pre-fix
  families retained with `superseded_for_emissions=true`), columnar dispatch
  outputs split by category/unit, harvest/supply/landbase/links inputs, and
  simplified EPSG:4326 provinces, ecoprovinces, RSYC tile supply, and B1/B2
  zone anchors with explicit gaps for the other 11 C2070 zones.  Bundle is
  7.5 MB total (spatial 3.96 MB); all build-time checks and focused contract
  tests pass.  No scenario artifacts were modified; frontend intentionally not
  built.

- **Ran the final 20%-zonal-wind-cap full-constraint test on the corrected-
  emission OSeCAN model.**  Started from the existing full-constraint case
  (5%-of-zone-generation `PWRBIO<z>` caps, 0 GW nuclear, T4=0, T3 kept or zero,
  $200/t, 2021 hydro upper cap, corrected EAR, BCW tiers/BC zonal unchanged)
  and added hard `PWRWND<z>` annual activity caps at 20% of each zone's
  `efix_tier_200` 2050 activity, held constant 2021-2070.  The 13 caps total
  193.596136 PJ/yr; the +$3/GJ wind adder is retained as a price but the cap
  binds.  Both cases solve optimal.  Primary (T4=0, T3 kept): 1,410,717.32 M$,
  0.523856 Mt, 2050 gas 0, wind 193.60 PJ, hydro 2,230.55 PJ, BIO 162.61 PJ,
  PWRBCW 51.83 PJ, T3 binding at 1,598.59 PJ over the horizon, solar 600.15 PJ.
  Optional (T3=T4=0): 1,411,456.31 M$, 0.538720 Mt, 2050 gas 0, wind 193.60 PJ,
  solar 610.87 PJ, PWRBCW 41.11 PJ.  Gas does not return at scale: all-years
  gas is 3.804 PJ (3.340 PJ in 2022-2024, 0.464 PJ in a 2036-2042 trace) and
  2050 gas is zero.  The remaining uncapped backstop is solar PV (plus 13.44 PJ
  of BSW), not gas.  All 13 wind caps, PWRBIO caps, hydro caps, T1/T2/T3 and
  BC zonal caps bind; nuclear stays zero.  Cap loading, dispatch reconciliation,
  tier caps and `osecan harmonize` pass.  Artifacts under
  `data/derived/national/clews/osecan/full_windcap/`; public OSeCAN untouched.

- **Ran the full-backstop constraint test on the corrected-emission OSeCAN
  model.**  Applied every backstop constraint at once: 5%-of-zone-generation
  `PWRBIO<z>` caps (162.608451 PJ/yr total), +$3/GJ on all `PWRWND<z>`, 0 GW
  nuclear via `TotalAnnualMaxCapacity` and `TotalAnnualMaxCapacityInvestment`
  for all 13 `PWRURN<z>`, `SUPBCWT4` = 0 (primary), and `SUPBCWT3` +
  `SUPBCWT4` = 0 (optional), keeping T1/T2 at 109.398863 PJ and the BC zonal
  caps at 43.523816 PJ; `RNWBIO` stays uncapped.  Both cases solve optimal.
  Primary (T4=0): 1,387,944.73 M$, 0.465597 Mt, 2050 gas 0, wind 807.19 PJ,
  BIO 162.61 PJ, PWRBCW 51.83 PJ, T3 binding at 1,610.71 PJ over the horizon,
  T4 zero; all 13 PWRBIO caps and T1/T2/BC zonal caps bind.  Optional
  (T3=T4=0): 1,388,092.40 M$, same emissions, wind 817.91 PJ, PWRBCW 41.11 PJ.
  Gas does not return: all-years gas is 3.381 PJ (3.307 PJ in 2022-2024,
  0.074 PJ in 2037/2042) and 2050 gas is zero.  The remaining zero-carbon
  backstop is uncapped wind; the +$3/GJ adder does not suppress it.  Cap
  loading, dispatch reconciliation, tier caps and `osecan harmonize` pass.
  Artifacts under `data/derived/national/clews/osecan/full_constraint/`;
  public OSeCAN untouched.

- **Ran the tight PWRBIO backstop-cap test on the corrected-emission OSeCAN
  model.**  Capped BIO fuel input to power bioenergy at the reference 2050
  level, 35.72 PJ/yr (12.533855 PJ/yr of output at the fixed 2.85 input ratio),
  using per-zone `TotalTechnologyAnnualActivityUpperLimit` rows from the
  reference 2050 distribution in every year 2021-2070.  `RNWBIO` stays uncapped
  for the fixed non-power BIO demand (801.96 PJ/yr); tiers, BC zonal caps,
  demand, hydro cap and corrected EAR are unchanged.  Results: 35.72 PJ cap
  reproduces the post-fix $200 reference (1,371,773.03 M$, 0.460752 Mt, gas
  2050 0, wind 967.98 PJ, T1/T2 bind, T3/T4 idle); 0 PJ cap gives 1,371,789.49
  M$, 0.542358 Mt, wind 980.52 PJ, gas 2050 0; wind +$3/GJ with the 35.72 PJ
  cap gives 1,385,135.04 M$, 0.530558 Mt, gas 2050 0, wind 0, uranium 957.26
  PJ, PWRBCW 51.83 PJ, T3 binding at 39.8737 PJ and T4 idle.  Gas does not
  return at scale; post-2026 gas is a 0.037 PJ `PWRNGSN1` sliver in 2037 and
  2042.  `osecan harmonize` and the cap-loading, dispatch-reconciliation and
  tier-cap tests pass.  Artifacts under
  `data/derived/national/clews/osecan/pwrbio_tight/`; public OSeCAN untouched.

- **Audited the uncapped nuclear backstop in the corrected-emission OSeCAN
  stress.**  Added `TotalAnnualMaxCapacity` and
  `TotalAnnualMaxCapacityInvestment` caps for all 13 `PWRURN<z>` technologies in
  every year 2021-2070, holding the 5% PWRBIO cap, +$3/GJ wind adder, BCW tiers,
  BC zonal caps, 2021 hydro cap and corrected EAR fixed.  No-nuclear case:
  optimal, 1,387,922.37 M$, 0.465597 Mt, 2050 gas 0, wind 789.91 PJ, T4 binding
  at 2,531.55 PJ over the horizon.  10 GW case: optimal, 1,385,098.46 M$,
  0.496039 Mt, nuclear 283.82 PJ at 2050, gas 0, T4 binding at 2,142.21 PJ.
  The zero-nuclear $200/t reference is unchanged (1,371,773.03 M$, 0.460752 Mt)
  because the reference has no nuclear dispatch.  Gas does not return; T3 and
  T4 both activate once nuclear is capped, and the near-zero result is robust
  to the nuclear cap but still relies on uncapped wind and the high-cost T3/T4
  biomass tier.  `osecan harmonize` and the cap-loading, dispatch-reconciliation
  and tier-cap tests pass.  Artifacts under
  `data/derived/national/clews/osecan/nuclear_audit/`; public OSeCAN untouched.

- **Corrected the OSeCAN emission-factor unit and reran the core national
  cases.**  `EmissionActivityRatio` and the UDC emission-cap multiplier now
  multiply the fuel-input factors by each plant's `InputActivityRatio`
  (gas 0.051 -> 0.1377, coal 0.090 -> 0.261 Mt/PJ output).  A 10 PJ gas check
  gives 27 PJ fuel x 0.051 = 1.377 Mt, equal to 10 x 0.1377; coal is 2.61 Mt.
  Post-fix: 119M baseline objective 1,331,575.72 M$ and 0.484962 Mt; 119M
  stress 1,371,773.42 M$ and 0.460752 Mt; tiered $200 stress 1,371,773.03 M$
  and 0.460752 Mt with T1+T2 binding and T3/T4 zero; $250/$300 1,371,773.13 M$
  and 0.450327 Mt with unchanged dispatch.  The pre-fix $200->$250 36 Mt
  emissions switch disappears because $200 is already near-zero gas.  Artifacts
  under `data/derived/national/clews/osecan/emission_factor_fix/`; numeric files
  in `national_150m`, `national_base_119m`, `national_supply_curve`, and
  `demand_sensitivity` are retained but marked superseded for emissions and
  objective.  Local disposable model patch only; public OSeCAN untouched.

## 2026-11-18

- **Filled the national harvest basis and ran the C2070 base at 119M m3
  log/yr.**  FSD 2021-2024 harvest now populates all 12 jurisdictions; the 2024
  Canada total is 118,977,339 m3 and the 12-province sum matches it.  Added 2024
  AAC/proxy and NFD wood-supply values, a 2024-hold base path for 2025-2070, and
  national low-100M/high-150M scenario rows.  The 150M case is now explicitly an
  upper bound, 26.1% above the observed 2024 harvest.  BC's repo-parsed AAC path
  is retained.  The new `national_base_119m` OSeCAN scenario converts
  118,977,339 log m3 to 155,991,469.70 total m3, 13,776,817.89 ODT, and 152.923
  PJ at 0.60 recovery; BC zonal is 43.523816 PJ and rest-of-Canada 109.399 PJ.
  Stress results: objective 1,371,124.38 M$, emissions 38.544682 Mt, SUPBCW
  binds 2027-2070; baseline remains zero-biomass at 25.714753 Mt.  `osecan
  harmonize` passes for all three parameter sets.  Public OSeCAN repo untouched.
  Files under `data/derived/national/harvest_basis/` and
  `data/derived/national/clews/osecan/national_base_119m/`.

- **Completed the full public QC BFEC/Woodstock extraction.** Crawled all 13
  diffusion regions, selected the latest public model ZIP for 52
  `Modeles_compresses` units plus notice-bearing root bundles `08462` and
  `08562`, and parsed all 54 units with 0 failures. Hardened ws3 with
  text/comma constants, `#CONST` yield cells, missing-theme skip warnings,
  `FOREACH` block skips, and case-insensitive/multi-shapefile inventory
  handling. Results: 9,857,465 inventory polygons, 880,096 inventory
  development types, 2,622 unique yield tables, 37,852,030.7 ha. Inventory
  `SUPERFICIE` matched the `.are` area exactly for every unit; 52 units have
  100% DT coverage and two have small flagged gaps (08462 99.85%, 09351
  99.77%). Three units (`06451`, `08462`, `08562`) carry confidentiality
  notices; their curves are published with provenance, while their
  development-type aggregates and inventory crosswalks stay in the
  unpublished `restricted/` scratch output. Public derivatives are under
  `data/derived/national/qc_bfec_full/`; scripts are
  `scripts/qc_bfec_index.py` and `scripts/qc_bfec_extract.py`; source index is
  `metadata/qc-bfec-source-index.json`; report is
  `docs/qc-bfec-full-extraction-report.md`. Large public CSVs are
  git-annex-backed and copied to `arbutus-s3`; the restricted crosswalk is not
  committed or pushed.

- **Built national SCANFI v2 area weights and inventoried donor candidates.**
  Added `scripts/national/build_scanfi_area_weights.py` and
  `scripts/national/build_donor_candidates.py`.  The full 30 m national grid
  was aggregated in 181 s to exact 990 m (33 x 33 pixel) blocks; forest area
  uses NFI land-cover classes 5-7 and species area normalises the ten SCANFI
  crown-closure layers within each block.  Outputs cover all 36 ecoprovinces,
  13 provinces/territories, 5,800 ecodistrict x province units, and 1,191 batch
  cells.  National forest area is 422.5 Mha (1.145x the 369.0 Mha NFI Table 4.0
  benchmark); coastline/lake blocks were nearest-unit filled (12.5 Mha flagged).
  Donor inventory ranks 398 weak AB/ON/NS/NU/MB batch cells and 371 no-plot
  ecodistricts; MGM joins remain pending.  Outputs are under
  `data/derived/national/assortments/national_area_weights/`.  The public OSeCAN
  repository was not modified.

- **Adopted the refined FFCM-implied B2 recovery as the BC base and reran the
  zonal OSeCAN.**  The base recovery is no longer held at the stale 2026-11-17
  calibration.  `bc_residue_recovery.py` now recomputes
  `implied_B2 = ffcm_residue_B2 / (ffcm_log_B2 * taper_ratio_B2)` from the
  current batch and FFCM ledger, asserts it at 0.723799 (tolerance 1e-5) and in
  [0.2, 1.0], and uses it for B2; B1 remains the documented 0.60 fallback
  (implied 1.7690 out of bounds).  Base 2028+ caps: B1 3.605475 PJ, B2
  39.918341 PJ, total 43.523816 PJ.  Low/high stay at 0.35/0.75 (B2 19.302895 /
  41.363347 PJ).  `BC_RESIDUE_RECONCILIATION.md` records the old published
  42.958795 PJ, interim held 35.809946 PJ, and adopted 43.523816 PJ.  Rebuilt
  the 2028 chain and co-opt: harvest recoverable 2,352,639 ODT, total residue
  9,258,723 ODT, all assigned, BIO 2,535,332 ODT (27.4% of assigned), net
  contribution CAD 957,802,389.  Cap equals chain recoverable residue per zone
  (B1 3.605475, B2 39.918341 PJ).  Reran `Current Measures` in
  `/tmp/osecan_zonal`: control objective 1,371,550.76 M$, zonal 1,371,599.09
  M$; zonal emissions 61.659110 Mt; B1 binds 2026-2070, B2 2032-2070.
  Outputs under `data/derived/national/clews/osecan/reconcile_ffcm/`; the
  public OSeCAN repo was not modified.

- **Reconciled the BC OSeCAN residue caps with the refined all-BC chain and
  reran the zonal case.**  Rebuilt `bc_residue_recovery.py` against the refined
  area-weighted batch (`bc_national_batch_area_joined.csv` sha256
  `fd12a1a...`; the cap used the stale `e0a51d5...` file).  Updated the batch
  constants (log 8.936B -> 11.283B m3; residue 722.692M -> 823.763M ODT),
  added a pinned batch-hash and sibling-manifest check, and made the time path
  use the matched-cell zone taper ratio.  The base recovery is held at the
  published 2026-11-17 calibration (B1 0.60, B2 0.583931) so the batch
  refinement lowers the gross taper instead of silently reflating recovery;
  the FFCM-implied B2 fraction (0.723799) is reported as a diagnostic.
  Base 2028+ caps move B1 3.672286 -> 3.605475 PJ, B2 39.286509 -> 32.204471
  PJ, total 42.958795 -> 35.809946 PJ; low/high cases and the `SUPBCW` rollup
  are updated, costs unchanged.  The rebuilt caps equal
  `bc_supply_by_zone.csv` recoverable residue exactly.  Added
  `BC_RESIDUE_RECONCILIATION.md` and cap/chain/manifest tests.  Reran
  `Current Measures` in `/tmp/osecan_zonal` with the new caps: control
  objective 1,371,586.14 M$, zonal 1,371,621.94 M$ (previous 1,371,553.35 /
  1,371,600.56); B2 binds 2030-2070 (previous 2032-2070).  Outputs under
  `data/derived/national/clews/osecan/reconcile/`; the public OSeCAN repo was
  not modified.

- **Fixed the BC residue co-optimisation review findings.**  Harvest sources
  now use the period-specific `residue_recoverable_odt` offers in
  `bc_supply_by_zone.csv` by zone, keeping the Stage A log assignment fixed and
  reporting Stage A-assigned residue separately.  Route provenance is carried
  into every co-opt link; `ffcm_route_fallback_road` links are excluded by
  default and reported as `non_nrn_fallback`, with an explicit
  `allow_fallback_links` sensitivity.  The road graph now keeps the winning
  edge mode/provenance per node pair, rejects same-node fallback ferry snaps,
  and Stage A/Stage B share one road/ferry cost decomposition.  Report shares
  and utilization are zero-safe.  Re-ran the 2028 chain and co-opt; outputs and
  `BC_COOPT_FIX_REPORT.md` are under `data/derived/national/bc_chain/`.

- **Refined the all-BC chain with NRN ferry connectivity and documented species
  aliases.** Added NRN `FERRYSEG` edges (53 connected segments plus one
  documented Comox-Powell River fallback; 1,951.95 km) and a flagged
  `0.03 CAD/m3/km` ferry-cost placeholder.  Added a documented alias table for
  SX, S, YC, H, B, ACT, and PA, recovering 10,845,346 ha and reducing residual
  unmapped area from 19.5% to 0.29%.  Re-ran area weights, 2028 supply, Stage A,
  and Stage B.  Stage A now assigns 47,672,271 m3 (100%) with B1 SAWLOG
  unassigned at zero; 12 previously disconnected island/coastal facilities are
  routable and 11 receive 8,470,715 m3.  Outputs and `BC_REFINEMENT_REPORT.md`
  are under `data/derived/national/bc_chain/`; 119 assignment/assortment tests
  pass.

- **Built the all-BC B1/B2 supply -> Stage A -> Stage B chain.**
  Added `workflows/national/assignments/bc_chain.py` and
  `workflows/national/assignments/tests/test_bc_chain.py`.  The chain scales the
  area-weighted BC assortment batch to the 2021/2028 zonal AAC, assigns
  sawlog/pulp/other/residue through NRN zone-to-facility links, derives sawmill
  residue from Stage A SAW inputs, and reports OSeCAN cap comparisons.  Outputs,
  report, and manifest are under `data/derived/national/bc_chain/`.

- **Added zonal BC woody-residue supply to OSeCAN and ran the zonal vs
  national comparison.** Added `workflows/national/clews/osecan_zonal.py` and
  `workflows/national/clews/tests/test_osecan_zonal.py`.
  - In a disposable `/tmp/osecan_zonal` copy of public `deltae/C2070` commit
    `40d5b43`, added `BCWB1`/`BCWB2` fuels and `SUPBCWB1`/`SUPBCWB2` supply
    technologies, switched `PWRBCWB1`/`PWRBCWB2` to the zone fuels at the
    unchanged 3.72 input ratio, and kept the national `BCW` chain for the other
    eleven zones.  `harmonize` exit 0 and `osecan info` exit 0.
  - Ran three `Current Measures` cases with hydro frozen at 2021 activity and
    CO2 at 95 $/t to 2025 and 200 $/t from 2026: national `SUPBCW` control,
    zonal `SUPBCWB1/B2`, and zonal plus `RNWBIO` at 4 $/GJ.  All solved
    optimal; the control reproduced 1,371,553.35 M$.
  - Zonal BCW dispatches: `PWRBCWB1` 0.987 PJ/yr from 2025 and `PWRBCWB2`
    10.561 PJ/yr by 2050; `SUPBCWB1` binds 2026-2070 and `SUPBCWB2` binds
    2032-2070.  The zonal constraint raises cost by 47.21 M$ and cumulative
    CO2 by 17.669 Mt relative to the national control; the higher generic-BIO
    cost leaves BCW dispatch unchanged.
  - Published `data/derived/national/clews/osecan/zonal/` with
    `zonal_structure.json`, `run_summaries.json`, `dispatch_by_zone.csv`,
    `ZONAL_OSECAN_REPORT.md`, and `manifest.json`.  23 focused tests pass;
    ruff clean.
## 2026-11-17

- **Built the recoverable-residue curve and 2021-2070 time path for OSeCAN
  B1/B2.** Added `workflows/national/clews/bc_residue_recovery.py`,
  `workflows/national/clews/tests/test_bc_residue_recovery.py`, and
  `planning/bc-residue-recovery.md`.
  - Parsed all 467 FFCM BC FMUs: 134,745,540.345 m3 log, 8,488,969.042 ODT
    logging residue, 0.063 ODT/m3, and a uniform 45.454084659 $/ODT supply
    cost.  FFCM has no FMU -> TSA/zone crosswalk; the BC totals are allocated
    to B1/B2 by the documented 2018 AAC zone log share (17.399562% /
    82.600438%) and flagged, not presented as an FMU location.
  - The same-log-basis implied recovery is 1.736815 for B1 (outside
    [0.2, 1.0], so the documented 0.60 fallback is used) and 0.583931 for B2.
    Base cost is 2.456978 $/GJ on a dry ODT basis (18.5 GJ/ODT).
  - Parsed AAC 2011-2028 from `TSA Tenure Apportionment V5.xls`: 41 TSA
    sheets, 37 positive, 2018 total exactly 52,774,801 m3/y.  The 2021-2028
    values are used directly and 2028 is held to 2070 (`aac_2028_hold`).
  - Result: base 50.472521 PJ/yr in 2021 (B1 3.678381; B2 46.794140) and
    42.958795 PJ/yr from 2028, at 2.456978 $/GJ; low 30.193455 PJ/yr at
    3.194071 $/GJ; high 64.700261 PJ/yr at 1.965582 $/GJ.  Against the
    50 PJ/$4 placeholder, the base 2021 cap is 1.009x and the base cost is
    0.614x; against the previous gross 93.170084 PJ, the base is 0.542x.
  - The AGB non-stem sensitivity uses the age-100 (Bark + Foliar + Branch) /
    Above median 0.246837 (range 0.169403-0.366018) and RSYC age-100 AGB;
    it is 3.07x the B1 taper base and 1.17x the B2 taper base.  It is a
    sensitivity, not the base curve.
  - Published `data/derived/national/clews/` with `bc_residue_recovery.csv`,
    `bc_residue_time_path.csv`, `bc_residue_cases_summary.json`, 12 zone and
    6 national parameter files, `BC_RESIDUE_RECOVERY_REPORT.md`,
    `manifest.json`, and `audit/bc_ffcm_fmu_supply.csv`.  14 focused tests
    pass; ruff clean.

- **Built the all-BC woody-residue supply curve for OSeCAN zones B1/B2.**
  Added `workflows/national/clews/bc_residue_supply.py`,
  `workflows/national/clews/tests/test_bc_residue_supply.py`,
  `workflows/national/clews/README.md`, and
  `planning/bc-woody-residue-supply-curve.md`.
  - Defined a FRESH-side provisional crosswalk: B1 = Georgia Depression +
    Southern Coastal Mountains; B2 = the remaining BC ecoprovinces.  Mapped
    each TSA to its THLB-dominant zone and reported seven material split TSAs
    (Bulkley, Cascadia, Fraser, GBR North, Kispiox, Nass, Soo); harvest is
    apportioned across ecoprovinces by THLB area within each TSA.
  - Primary harvest basis is the all-BC 2018 AAC (52,774,801 m3/y = parsed
    44,399,801 m3/y + Prince George 8,375,000 m3/y), chosen because the
    2021-2070 profile needs the sustainable annual cut.  The parsed 36-TSA AAC
    (44,399,801), parsed apportionment (38,726,439), and all-BC apportionment
    (58,424,023, adding five TSAs the generic parser misses) are retained as
    comparison scenarios.
  - Primary residue ratio is the area-weighted national batch, resolved by
    ecoprovince (BC all-species 0.080872 ODT/m3; range 0.035732-0.364484).
    The TSA24 Stage B mill-residue ratio (0.259553 ODT/m3) is reported and
    rejected as a different mass basis.
  - Energy is 18.5 GJ/ODT dry; no moisture credit.  Results: B1 6.162027 PJ/yr
    at 2.994574 $/GJ; B2 87.008058 PJ/yr at 1.706656 $/GJ; national 93.170084
    PJ/yr at 1.791835 $/GJ.  The 50 PJ/$4 placeholder is conservative on both
    quantity and cost; a 60% recovery sensitivity gives 55.902 PJ.
  - Published `data/derived/national/clews_supply/bc_woody_residue/` with
    `bc_zone_crosswalk.csv`, `bc_tsa_zone_crosswalk.csv`,
    `bc_annual_residue.csv`, `SUPBCWB1_*`/`SUPBCWB2_*` activity-limit and
    variable-cost rows, audit/comparison tables, `BC_CLEWS_SUPPLY_REPORT.md`,
    `README.md`, and `manifest.json`.  22 focused tests pass; ruff and mypy
    clean on the new module.

- **Built the all-BC forest/THLB area-weight table and joined it to the
  national batch.** Added
  `workflows/national/assortments/bc_area_weights.py` and
  `workflows/national/assortments/tests/test_bc_area_weights.py`.
  - Read 5,057,731 VRI 2025 R1 polygons passing the documented Crown
    management / vegetated / treed filter, allocated `SPECIES_CD_1..6` by
    normalised `SPECIES_PCT_1..6`, and mapped BC codes through the CASFRI
    crosswalk.  Produced 2,266 `(TSA, ecoprovince, ecoregion, RSYC species)`
    rows covering 56,428,146 ha of forest and 18,653,774 ha of THLB.
  - THLB is the per-polygon label-point sample of `misc.thlb.tif`
    (`area_ha * percent / 100`); 559,917 polygons / 4,953,977 ha have no THLB
    attribution and are kept at zero.  The raster itself totals 20,032,336 ha
    on its 32.6% valid grid.
  - 11,007,317 ha (19.5%) is `UNMAPPED_BC`, dominated by hybrid spruce (`SX`,
    7.74M ha), spruce aggregate (`S`), yellow-cedar (`YC`), hemlock aggregate
    (`H`), and fir aggregate (`B`); unmapped area is reported, not
    redistributed.
  - Joined 429 BC-relevant national batch cells: 192 matched by
    `(ecoprovince, species)`, 237 retained as `no_area` with zero totals
    (never the 1 ha placeholder).  BC area-weighted log volume is 8.94B m3,
    residue 1.97B m3 / 722.7M odt, taper total 10.91B m3; these are standing
    volume proxies, not an annual supply estimate.
  - Published `data/derived/national/assortments/bc_area_weights/` with
    `bc_area_weights.csv`, `bc_area_summary.json`,
    `bc_national_batch_area_joined.csv`, `BC_AREA_REPORT.md`, and
    `manifest.json`.  Runtime 123.8 s; 6 area-weight tests pass.

- **Built and ran the Phase 1 national representative-stand batch.**
  Added `workflows/national/assortments/national_batch.py` and
  `workflows/national/assortments/tests/test_national_batch.py`, plus the
  governing note `planning/national-representative-stand-batch.md`.
  - Planned 1,260 ecoprovince × species cells; 1,205 had a valid distribution,
    height model, TPH target, and taper dispatch.  Completed 1,191 stands.
    The 14 explicit failures are 11 `BETU.ALL` cells whose RSYC volume cannot
    be reached at the donor TPH with a positive diameter multiplier, two
    sampler-truncation failures, and one invalid-log geometry failure.  The 55
    skipped cells are 33 `GENERIC` fits with no defensible taper proxy, 19
    no-fit cells, and 3 cells without a finite plot TPH target.
  - Volume target is RSYC age-100 volume; two cells use the flagged observed
    plot-volume fallback.  TPH is the expansion-weighted mean live plot-visit
    TPH from the cell; borrowed cells use the donor ecozone or national species
    mean.  Every cell uses a **1 ha placeholder** (`placeholder_equal_area`).
  - Built 488 species × height-model buck-yield tables over 2 cm DBH bins
    (2–100 cm) with the BC interior dimension sorts and Kozak taper.  The
    tables cover 1,191 stands and are validated against a 20-tree exact
    bucking sample in every cell: 1,184 pass the taper/residue gate, 7 fail,
    and 224 flag product-total allocation as a diagnostic.  Total taper,
    recovered logs, and residue conserve exactly.
  - Equal-area pilot totals: 144,415.816 m3 recovered logs, 23,704.258 m3
    residue (8,822.230 ODT), 168,120.075 m3 total.  These are not a national
    supply estimate.  Runtime 494.9 s (prep 25.3 s, table build 327.6 s,
    stand synthesis 141.9 s, assembly 0.2 s), inside the 780 s timebox.
  - Published `data/derived/national/assortments/national_batch/` with
    `national_stands.csv`, `national_assortment_matrix.csv`,
    `national_assortment_summary.json`, `national_buck_yield_table.csv`,
    `NATIONAL_BATCH_REPORT.md`, `manifest.json`, 488 table partials, and 1,205
    cell partials.  Added `metadata/national-batch.csv`.
- **Built the Stage B sawmill-residue and bioenergy allocation pilot for the
  TSA24/BC ws4-quota run.** Added
  `workflows/national/assignments/residue.py` (FFCM outfeed/infeed parsing,
  residue supply, recipient demand, and unit conversions) and
  `workflows/national/assignments/stage_b.py` (NRN facility-to-facility
  routing, PuLP allocation, validation, tables, report, and manifest).
  - Parsed 94 ws4-quota SAW facilities (32,076,713 m3 of assigned sawlogs)
    against the FFCM processing lines; all 94 have complete chips,
    sawdust/shavings, hogfuel/bark, and internally-used-woodfuel outfeed
    ratios.  Generated 8,325,597.703 ODT of residue: 5,099,906.709 chips,
    1,748,418.017 sawdust/shavings, 866,729.145 hogfuel/bark, and 610,543.832
    other.  Every ratio and unit conversion is recorded in
    `residue_conversions.csv`; missing ratios are excluded, never assumed.
  - Screened 41 PAP/PEL/BIO recipient candidates to 24 lines with a
    corroborated Mills DB coordinate and a road path (5 PAP, 9 PEL, 10 BIO).
    Recipient capacity is the FFCM line infeed normalised to ODT; product
    prices are FFCM commodity prices (all available, so the run is
    profit-maximising).  Proxy-coordinate recipients and island recipients
    without a road path are excluded and reported.
  - Routed 2,026 source-recipient links on the real NRN BC road graph
    (1,150 real, 782 real from proxy-located sources, 94 explicit
    hub-and-spoke FFCM road fallbacks for a disconnected recipient).  Three
    co-located source-recipient pairs share a road node (0 km) and are flagged.
  - Assigned 6,501,439.981 ODT (78.09%) and left 1,824,157.722 ODT
    unassigned: 1,048,992.210 chips (recipient capacity and missing links),
    140,390.240 sawdust/shavings, 24,231.402 hogfuel/bark (capacity and
    negative net contribution), and 610,543.832 other (no consumer class).
    15 of 24 included recipient facilities are capacity-binding.
  - Bioenergy share is 10.12% of generated residue and 12.96% of assigned
    residue, producing 624,018.634 MWh.  Objective +CAD 759,507,703.72:
    product revenue CAD 1,502,041,517.98, transport CAD 201,593,954.16, and
    processing CAD 540,939,860.09.
  - Published `data/derived/national/assignments/tsa24_pilot/stage_b/`
    (`residue_supply.csv`, `residue_demand.csv`,
    `stage_b_transport_links.csv`, `stage_b_assignment.csv`,
    `stage_b_utilization.csv`, `residue_balance.csv`, `bioenergy_share.csv`,
    `objective_decomposition.csv`, `validation_summary.json`,
    `STAGE_B_REPORT.md`, and `manifest.json`).  Validation passes all nine
    checks, including mass balance, capacity, eligibility, transport
    existence, unit consistency, and objective reconciliation.  Added 13
    Stage B tests; 69 tests pass.
- **Built the BC mill-to-tenure crosswalk and re-ran Stage A with
  apportionment-derived maximum bounds.** Added
  `workflows/national/assignments/tenure_crosswalk.py`, which parses the 2018
  TSA Tenure Apportionment workbook, normalizes holder/company names while
  keeping industry words, applies an explicit major-holder alias table, joins
  facilities to FADM TSA polygons (cached in `bc_facility_tsa.csv`), and accepts
  a match only by exact company, same-TSA alias, or province-wide unique
  holder. Ambiguous holder/TSA combinations never receive a facility ID.
  - Parsed 52 workbook sheets (40 TSA sheets), 146 positive 2018
    apportionment-class rows across 32 TSAs (38,726,439 m3/y), and 152
    positive licence rows across 33 TSAs / 77 holders (18,438,022 m3/y).
    Rejected 130 zero/negative 2018 rows to
    `tsa_apportionment_2018_rejected.csv`.
  - Matched 28 licence rows to 20 facilities (8,134,098 m3/y: 21.0% of
    apportionment-class volume, 44.1% of licence-detail volume).  Method mix:
    13 alias same-TSA, 9 exact same-TSA, 5 exact unique, 1 alias unique.
    Confidence: 10 high, 17 medium, 1 low.  50 rows / 7,376,469 m3/y stayed
    ambiguous and 74 rows / 2,927,455 m3/y had no candidate.  TL-hub proxy
    coordinates are kept for audit but never used to disambiguate a match.
  - Published `facility_tenure_crosswalk.csv`,
    `facility_tenure_crosswalk_candidates.csv`,
    `contracts_bc_apportionment.csv` (20 maximum entitlements; 16 Stage A,
    4 out-of-scope PLY; 4 exceed nameplate), and
    `tenure_crosswalk_summary.json`.  Unmatched facilities keep `min=0`,
    `max=nameplate`, and `contract_source=diagnostic_absent`.
  - Re-ran Stage A with the real NRN links.  Base 1,000 ha is unchanged
    (311,600 m3 assigned, 0 unassigned, +CAD 4.960M, CAD 125.35/m3) because no
    entitlement binds at that scale.  ws4-quota sensitivity: 70,625,268 m3
    assigned / 24,739,071 unassigned (was 77,461,964 / 17,902,375), objective
    -CAD 25,943,496,439.84 (was -CAD 19,233,509,115.39), delivered CAD
    146.83/m3 (was 150.20), utilization 77.5% (was 85.0%), 115 nameplate-binding
    facilities (was 129), and 14 of 16 apportionment contracts binding.  Class
    mix SAW/PAP/PEL/BIO 45.4/33.9/9.7/10.9% versus 50.2/30.9/8.9/10.0%
    previously and FFCM nameplate 49.6/33.8/7.5/9.1%.  The tighter caps add
    6.84M m3 of unassigned SAWLOG; they were not relaxed.
  - Added normalization, alias-family, same-TSA, ambiguous-rejection,
    contract-construction, and loader tests; 65 tests pass.

- **Recovered BC facility coordinates and built the BC Fibre Database
  diagnostic contract table, then re-ran Stage A.** Added
  `workflows/national/assignments/bc_fibre.py`, which parses the 2015 CFS Mills
  DB, joins FFCM raw site names/cities from
  `original_list_BP.xlsx`, and accepts a coordinate only when name, city, or
  coordinate evidence corroborates the code.  The eight legacy exact-code
  matches are collisions and are rejected in
  `bc_facility_match_conflicts.csv`.  Coverage improved from 8 Mills DB / 126
  TL-hub proxy / 45 missing to 98 / 65 / 16 across all 179 BC facilities
  (Stage A scope: 8/113/41 -> 88/58/15).  Method counts: 47 company+city, 45
  mill-name, 5 coordinate-corroborated, 1 coordinate-named, 81 unmatched
  (5 of those have no FFCM registry row).
  - Parsed `Sawmill Fibre Allocation Input V3.xls` (596 dimensionless residue
    shares across Chips, SawShav, Hog, Other Biomass) and
    `TSA Tenure Apportionment V5.xls` (36 TSA sheets; 146 positive 2018
    apportionment-class rows totalling 38,726,439 m3/y; licence-level detail in
    `tsa_tenure_apportionment_long.csv`).  The workbooks have no facility-level
    volume: `contracts_bc_diagnostic.csv` keeps TSA volumes without facility IDs
    and residue shares without volumes, and Stage A keeps
    `contract_source=diagnostic_absent` on all 161 facilities.
  - Rebuilt the NRN road links from the recovered coordinates: 156/163
    facilities snapped, 134 real paths, 196 grade links.  Stage A now uses real
    links for 118 facilities (196 links) and flagged FFCM fallback for 11 (14
    links); 65 facilities moved fallback -> real, while 10 moved real ->
    fallback and 5 moved real -> missing because the legacy coordinate was a
    code collision.  The TSA24 anchor also moved from `BC-SAW-488` (actually
    FFCM Canfor Houston) to `BC-SAW-506` (Canfor Prince George) at the
    documented 53.99, -122.69 proxy.  Base 1,000 ha: 311,600 m3 assigned, 0
    unassigned, objective +CAD 4.960M, delivered CAD 125.35/m3 (transport CAD
    0.65/m3).  ws4-quota sensitivity: 77.46M m3 assigned / 17.90M unassigned,
    objective -CAD 19.234B, delivered CAD 150.21/m3.  Class mix SAW/PAP/PEL/BIO
    50.2/30.9/8.9/10.0% versus 49.8/31.1/8.6/10.5% previously and FFCM
    nameplate 49.6/33.8/7.5/9.1%.  Added matching and contract-evidence tests;
    43 tests pass.

- **Built the real NRN BC road-network transport pilot and re-ran Stage A.**
  Added `workflows/national/assignments/build_road_network.py`, which reads the
  archived NRN BC GeoPackage, filters haulable road classes, projects to BC
  Albers (EPSG:3005), snaps endpoints to a 1 m grid, assembles a 203,511-node /
  242,059-edge undirected graph in 11.5 s (peak 579 MB), snaps facilities to the
  nearest node within 5 km, and writes `transport_links_bc_real.csv`,
  `snap_diagnostics.csv`, `road_network_summary.json`, `NETWORK_REPORT.md`, and
  `distance_comparison.png`. 134 facilities carry coordinates: 79 snapped, 55
  `too_far`, 45 `missing_coord`; 77 facilities have a real path (BC-SAW-383 is
  on a separate Vancouver Island component). Stage A now uses real links for 67
  in-scope facilities and flagged FFCM TL fallback for 67; 26 + the anchor
  remain unlinked. The 1,000 ha base is unchanged (311,600 m3 assigned,
  objective -CAD 1.624M, all three assigned facilities are fallback), while the
  ws4-quota sensitivity moves to 79.48M m3 assigned / 15.89M unassigned
  (objective -CAD 18.449B) from 77.25M / 18.11M (-CAD 21.346B) on the previous
  TL-graph run. Added graph/snap/cost tests; 34 tests pass.

- **Upgraded the Stage A TSA24/BC facility and transport inputs and re-ran the
  pilot.** Added `workflows/national/assignments/units.py` (declared
  `m3_green`/`odt`/`bdmt`/`admt`/`mbf`/`kwh`/`mwh`/`gj` conversions with
  fail-closed ambiguity handling), `facilities.py` (one row per BC facility,
  Mills DB -> CFS suppliers.dbf -> FFCM TL/water hub anchor -> missing
  coordinate precedence, unit-normalized nameplate), and `transport.py` (FFCM
  TL-graph shortest paths, road/rail/combination modes, line-haul and transload
  costs, path confidence). Published `facilities_bc.csv` and
  `transport_links_bc.csv` under `data/derived/national/assignments/tsa24_pilot/`.
  - **Facilities:** 179 BC rows; 161 Stage A SAW/PAP/PEL/BIO facilities.
    Coordinate coverage all BC: 8 `mills_db`, 0 `suppliers_dbf`, 126
    `tl_anchor_proxy`, 45 `missing`; Stage A scope: 7 / 0 / 113 / 41. No
    Euclidean guesses; 8 TL/water hub proxies documented. No zero/blank
    nameplate facility remained.
  - **Transport:** TSA24 supply anchor is the Mills-DB Prince George sawmill
    proxy `BC-SAW-488` because no TSA24 FMU exists in the FFCM graph. 210
    grade-specific links across 129 facilities: 61 `direct_route`, 68
    `graph_shortest_path`; 31 facilities have no TL route and the anchor mill is
    excluded from a self-haul, so 32 are `missing`. Modes: 105 road, 24
    combination. Median graph distance 920.6 km versus 261.2 km under the
    previous 150 km diagnostic (+690.7 km median); SAWLOG median cost CAD
    61.39/m3 versus 14.23, residue CAD 31.63/m3 versus 13.70. All links record
    `capacity=None` and `capacity_source=absent`.
  - **Units:** `bdmt/y` capacity now converts at the declared 0.45 ODT/m3
    density; `admt/y` needs a declared air-dry moisture; `mbf/y` needs a
    declared recovery factor; energy units never convert to volume. All 268 BC
    in-scope processing lines passed the unit screen.
  - **Base pilot (1,000 ha):** 311,600 m3 assigned, 0 unassigned, objective
    -CAD 1.624M, delivered cost CAD 146.48/m3 (transport CAD 21.78/m3), net
    contribution -CAD 5.21/m3. Class mix is unchanged from the previous pilot
    (SAW 70.3%, PAP 7.0%, PEL 22.7%, BIO 0%). The unassigned penalty keeps the
    SAWLOG stream assigned even though its diagnostic margin is negative.
  - **ws4-quota sensitivity (306,047.3 ha/period):** 77.25M m3 assigned, 18.11M
    m3 unassigned (RESIDUE 8.72M, SAWLOG 9.39M, all capacity-exhausted),
    objective -CAD 21.35B, 129 facilities binding. Class mix: SAW 50.1%, PAP
    31.0%, PEL 8.9%, BIO 10.0%.
  - **Contracts/capacity:** all 161 contracts remain `min=0`,
    `max=nameplate`, `gate=0`, now with `contract_source=diagnostic_absent`;
    no real contract or route capacity data exists.
  - Added tests for units, facility coverage, and transport paths under
    `workflows/national/assignments/tests/`; `pytest -q tests/test_assignment.py
    workflows/national/assignments/tests` passes 27 tests. No MKRF input or
    output is used; assortment sorts remain dimension-only.

## 2026-11-16

- **Built the Stage A facility-assignment pilot and ran TSA24/BC.** Added
  `workflows/national/assignments/` with a typed supply/facility/transport/
  contract contract, a PuLP transportation LP, a fail-closed validator, a
  TSA24 fixture builder, a pilot driver, and nine synthetic LP tests. The
  fixture keeps BC SAW/PAP/PEL/BIO lines, normalizes m3/y and odt/y capacity,
  rejects bdmt/y capacity lines, converts processing cost and product revenue
  through same-line outfeed ratios, and uses a documented 150 km diagnostic
  TSA24 access leg plus the minimum FFCM facility-to-TL road distance. The
  1,000 ha base run is diagnostic: 311,600 m3 supply, all assigned, objective
  +CAD 2.57M, no capacity binding. The optional ws4-quota sensitivity
  (306,047.3 ha/period) assigns 78.8M m3, leaves 16.5M m3 unassigned with
  capacity-exhausted reasons, and binds 130 facilities; it is a period-scale
  diagnostic, not an annual forecast. Validation passes all 14 checks, and
  `tests/test_assignment.py` covers conservation, capacity binding, grade
  eligibility, contract min/max, unassigned penalty, moisture, unit rejection,
  and objective reconciliation. Published under
  `data/derived/national/assignments/tsa24_pilot/`. No MKRF input or output is
  used; assortment sorts remain dimension-only.

- **Built and ran the first national hierarchical DBH/height fit.** Added
  `workflows/national/assortments/national_fit.py`, a resumable 8-worker
  workflow that treats the coverage audit as the sampling frame, fits local
  DBH distributions only for `direct_fit`/`pooled_fit` cells, and resolves
  `no_data` cells through the nested parent chain (`pooled_parent`) or the
  national species fit (`species_national`). Borrowed shapes are never labelled
  local; direct/pooled failures are reported as `no_fit`. Each cell records the
  selected distribution, parameters, spike weight, support, AIC/AICc/BIC/KS,
  DBH limit, evidence tier and sources, plus a height model
  (`H = 1.3 + exp(a) * DBH^b`) and the publishable RSYC volume/AGB curve at age
  100 as a scale prior. Added an optional candidate-family filter to
  `distributions.py` so this pass uses the specified single Weibull/gamma,
  `mix_weibull_2`, `mix_weibull_gamma`, and constrained-spike candidates
  (legacy two-gamma, generalized beta, three-component Weibull, and
  unconstrained spikes remain available to other callers). Ecoprovince run:
  1,260 target cells in 321.6 s — 319 direct / 29 pooled / 132 parent /
  761 species-national / 19 no_fit, with 0 timebox-pending. Ecoregion run
  (resumed 560 coarser cells): 4,830 target cells in 525.2 s — 757 direct /
  154 pooled / 1,142 parent / 2,705 species-national / 72 no_fit, with
  0 timebox-pending. Validation: 1,252 support-level direct fits (median KS
  0.040, p90 0.138, 67 flagged KS > 0.20), 193 support-level pooled fits
  (110 flagged `low_support`), 0 borrowed rows labelled local, 0 no_data cells
  without an RSYC prior, and 1,337 local / 5,208 borrowed height models. The
  98 deterministic local failures are retained in `remaining_cells.csv` with
  `resumable=false`; they are not timebox work. Published
  `data/derived/national/assortments/national_fit/` (distribution and height
  CSVs, summary JSON, report, evidence heatmap, remaining-cell list, 1,543 cell
  partials, manifest) and `metadata/national-fit.csv`.

- **Built the national plot-to-RSYC-stratum coverage audit.** Added
  `workflows/national/assortments/national_coverage.py`, a reproducible
  fail-closed audit that spatially joins the harmonized national plot extract
  to the RSYC ecodistrict hierarchy, maps source species codes to the 32 RSYC
  scientific species plus its `coniferous`/`broadleaf`/`generic` model groups,
  and classifies every stratum × species cell as `direct_fit` (n_trees >= 100
  and n_plots >= 5), `pooled_fit` (not direct and 30 <= n_trees < 100 or
  2 <= n_plots < 5), or `no_data`. Results: 67,477 of 72,975 sites (92.5%)
  mapped to 301 of 672 ecodistricts; 3,404 sites missing coordinates, 1,027
  `-1/-1` sentinel sites, and 1,067 valid-coordinate sites outside the RSYC
  forested ecozones reported rather than dropped. At ecodistrict level the
  audit classifies 2,001 direct / 768 pooled / 20,751 no_data cells
  (2,001 / 768 / 7,766 in data-bearing districts); ecoregion 765 / 218 /
  3,847; ecoprovince 321 / 46 / 893; ecozone 142 / 16 / 262; national rollup
  35 / 0 / 0. The crosswalk resolves 355 observed source codes: 113 exact
  species, 2 documented aliases, 202 functional-group fallbacks, 38 generic,
  and 0 unmapped; 240 codes (376,636 live trees) do not resolve to an RSYC
  scientific species. The report identifies 371 plotless ecodistricts, two
  plotless ecozones (Taiga Cordillera, Taiga Shield West), and the absent
  AB/ON/NS/NU jurisdictions. No distribution was fitted. Published
  `data/derived/national/assortments/national_coverage/` (`coverage.csv`,
  `coverage_summary.json`, `species_crosswalk_national.csv`,
  `COVERAGE_REPORT.md`, `coverage_heatmap.png`, `manifest.json`) and
  `metadata/national-coverage.csv`. Runtime 454.5 s.

- **Added dual volume+TPH constraint scaling to synthetic tree lists and
  re-ran the TSA24 target AU.** `SynthesisTarget.target_tph` fixes expansion
  factors at the target TPH (`phf_i = target_tph * w_i / sum(w)`) and a
  bracketed `scipy.optimize.brentq` search finds the positive diameter
  multiplier that keeps the taper total at the target volume; height is
  recomputed from the scaled DBH, never linearly scaled.  The documented
  bracket is `[0.10, 10]`, with one `[0.01, 25]` retry before a loud failure;
  `diameter_scale_factor`, bracket, iterations, and residual are carried in the
  synthesis manifest and validation summary.  With `target_tph` absent the
  legacy volume-only expansion-factor scale is unchanged.  TSA24 (`SBS_SX`, H,
  age 100) now matches **1,463.5502 TPH** and **311.6 m3** exactly; the diameter
  scale is **0.929782671** (16 root iterations), QMD falls from 18.84 to
  **17.52 cm**, and basal area rises from 33.17 to **35.28 m2/ha**.  Thinner
  stems move volume from sawlogs to residue: sawlog 228.72 -> 219.06, other
  24.11 -> 21.07, pulpwood 0.33 -> 0.60, recovered 253.16 -> 240.74, residue
  58.44 -> 70.86 m3; 385 logs and exact conservation (240.735518 + 70.864482 =
  311.600000 m3).  Added tests for dual matching, the unchanged volume-only
  path, no-sign-change failure, species weights/per-species expansion factors,
  and post-scaling conservation; updated `synthetic_trees.csv`,
  `assortment_matrix.csv`, `residue_ledger.csv`, `validation_summary.json`,
  `VALIDATION_REPORT.md`, `manifest.json`, `metadata/assortment-pilots.csv`,
  and the synthetic/assortment plots.

- **Added constrained finite-mean spike/zero-inflated first-bin mixtures to the
  RSYC DBH fits and re-ran TSA24.** `spike_weibull` / `spike_gamma` add a point
  mass at the plot DBH limit plus one Weibull/gamma tail left-truncated at the
  first bin upper edge.  The disjoint-support EM sets the spike weight to the
  first-bin share, counts it as one AIC parameter, rejects weights outside
  `(0.001, 0.95)`, and samples the spike uniformly inside the first bin so every
  stem stays above the plot limit.  `spike_weibull_constrained` /
  `spike_gamma_constrained` add a bounded grouped-MLE finite-mean tail with
  shape >= 1 and scale >= 0.1 cm; a tail at any declared bound is reported as
  `boundary_reject` and is never selected.  The unconstrained variants remain
  audit-only because their untruncated tail mean can be infinite.  Spike
  families are gated on first-bin share > 20% or best simple KS > 0.15, and
  selection keeps the two-AIC parsimony rule.  TSA24 re-fit (68 general + 7 AU):
  spike fits attempted on 29 gated strata (116 candidates, 58 constrained); 28
  constrained boundary rejects; 8 constrained spikes selected and no
  unconstrained spike selected.  Both CW high-KS strata land on the exponential
  shape bound (`a = 1` / `p = 1`), are `boundary_reject`, and keep the legacy
  two-gamma selection (AIC -98,914 / -29,981; KS 0.221 / 0.217).  Five fallbacks
  are more than 2 AIC worse than the previous unconstrained spike selection:
  CW ecozone +795.2, Nazko Upland BL +382.6, CW ecoprovince +271.7, Hart Ranges
  BL +85.0, and Upper Fraser Trench SW +20.4.  The AU (`SBS_SX`, H, age 100)
  changes only `BL` to `spike_weibull_constrained`.  The run also uses the
  `volume_and_tph` synthesis mode: TPH 1,463.55 matches the BTC target, QMD is
  17.52 cm, basal area 35.28 m2/ha, and the 311.6 m3 taper target is still met
  exactly (recovered 240.74 + residue 70.86 m3; sawlog 219.06 m3).  Against the
  volume-only constrained-spike run (TPH 1,189.39, QMD 18.84 cm, logs 253.16,
  residue 58.44 m3), the dual constraint raises TPH by 274.2 and residue by
  12.4 m3 and lowers sawlog by 9.7 m3, with zero volume error in both runs.
  Added constrained-bound, boundary-rejection,
  first-bin-weight, truncated-sampling, and finite-mean recovery tests; updated
  `distribution_fits.csv`, `dbh_mixture_comparison.csv`,
  `validation_summary.json`, `manifest.json`, `VALIDATION_REPORT.md`, the
  assortment README, and the working-tree `--working` plot generator.

## 2026-11-15

- **Added multi-component Weibull/mixed mixtures to the RSYC-stratified DBH
  fits and re-ran TSA24.** New `mix_weibull_2`, `mix_weibull_gamma`, and
  optional `mix_weibull_3` families use a left-truncated EM that delegates each
  component M-step to `fit_censored_inventory` (Weibull) or the documented
  gamma moment update, canonicalises component order after every update, uses
  two deterministic starts, rejects weights below `1e-4`/non-finite
  parameters/collapsed components, and records convergence diagnostics.  The
  legacy two-gamma mixture remains the fallback (with a ten-unit AIC penalty
  when non-converged); new Weibull families are selected only when converged.
  Mixture sampling carries component names and respects the DBH limit.  TSA24
  re-fit: 45 of 75 DBH targets switched to a new Weibull mixture, 42 improved
  by >2 AIC, one was worse by >2 AIC (`SB` AU, +6.7 AIC); high-KS selections
  remain the two `CW` Montane Cordillera strata, where the new mixtures are
  worse than the single Weibull; `AC` Montane Cordillera improved from KS
  0.184 to 0.040 and `DG` Fraser Basin from 0.156 to 0.065.  The target AU
  (`SBS_SX`, H, age 100) switched six of seven species to Weibull mixtures;
  the volume target and conservation remain exact (311.6 m3; logs 252.58 +
  residue 59.02), TPH rises from 1,062 to 1,225 and QMD falls from 19.57 to
  18.72 cm.  Added `dbh_mixture_comparison.csv`, new distribution_fits.csv
  fields, mixture tests, and updated the assortment README/report/manifest.
  Tests: 13 assortment + 15 bucking = 28 passed.

- **Added the national assortment/stand-to-tree disaggregation prototype and
  TSA24 demonstration.** New `workflows/national/assortments/` package fits
  RSYC-stratified DBH distributions (`fit_censored_inventory` Weibull/gamma,
  grouped generalized beta, two-component gamma mixture) with
  ecodistrict -> ecoregion -> ecoprovince -> ecozone -> TSA24-species fallback;
  fits hierarchical height-diameter models with
  `fit_height_diameter_models`; synthesizes a volume-constrained tree list;
  bucks it with the existing BC interior rules and Kozak taper; and aggregates
  a species x product/sort x top-diameter/length matrix with a residue/AGB
  ledger and a facility feedstock contract. The documented shape-vs-scale
  contract is explicit: the distribution sets shape, the target volume sets
  scale. TSA24 target is BTC AU `23000` (`SBS_SX`, site class H) at age 100 in
  Nechako Lowland, 311.6 m3/ha: 600 trees, 1,062.4 TPH, 31.96 m2/ha BA, QMD
  19.57 cm; recovered logs 259.96 m3 (83.4%), residue 51.64 m3 (16.6%),
  taper total 311.60 m3; DBH quantile MAE max 2.15 cm; volume conservation
  exact; all five checks pass. General all-age fits: 68 strata (48 direct, 20
  pooled), selected KS median 0.050, max 0.221, 3 high-KS; generalized beta is
  retained as a diagnostic candidate but excluded from synthesis because it
  lacks a documented inverse-CDF sampler. Added
  `metadata/assortment-pilots.csv` and artifacts under
  `data/derived/national/assortments/tsa24_prototype/`. Tests: 8 assortment +
  15 bucking = 23 passed; `compileall` clean.

- **Corrected the TSA24 residue validation with the documented BTC volume and
  `biomass-live` surfaces.** Ran `femic tipsy run-btc` with the `biomass-live`
  and `carbon` banks to `04_output-tsa24_biomass.csv` (16 AUs × 36 ages, 973
  columns, ODT/ha), added `tsa24_biomass_curves.py` /
  `compile_tsa24_biomass_curves.py`, and compiled a 576-row
  `biomass_au_curves.csv` with explicit additivity checks. Added
  `validate_tsa24_btc.py`: the BTC `volume` benchmark matches 14 of 20 plots
  (12 positive-volume comparisons, 2 zero-volume age joins, 6 no-AU), with
  observed mean 231.27 vs BTC 229.21 m³/ha, MAE 122.72, RMSE 148.27, relative
  bias +0.9%; pilot bucked mean 201.09 m³/ha, bias -28.12, MAE 126.86, RMSE
  151.08, relative bias -12.3%. The harvest-slash ledger uses
  `AGB - merchantable log biomass` on 8 volume-consistent plots: mean AGB
  200.92 ODT/ha, log biomass 88.27 ODT/ha, harvest slash 112.65 ODT/ha, taper
  residue 21.01 ODT/ha (ratio 5.36); 60% roadside recovery, 45%/30% moisture,
  0.90 chip yield. Added species basic-density defaults
  (`rules/bc_basic_density.csv`, FVS-BC NBEL/FIA) and removed the mill-residue
  ratio comparison from the grade benchmark; grade shares are explicitly
  **not validated** (dimension-only pilot; D/F zero in TSA24). New artifacts:
  `btc_volume_benchmark.csv`, `harvest_residue_ledger.csv`,
  `harvest_residue_summary.json`, `HARVEST_RESIDUE.md`,
  `biomass_curves_manifest.json`. Tests: 15 passed; `compileall` clean.

- **Reran the TSA24 grade/residue benchmark from BTC `/TSR` output and
  superseded the nominal-site-index `.taf` table.** Installed `wine` and
  `xvfb` with non-interactive `sudo` and added Wine Mono, then ran the
  documented FEMIC path: `femic tipsy run-btc` with `log-grades` and
  `residual-fibre` banks, followed by `femic tsa btc-post-tipsy`. The BTC run
  exited 0, wrote 16 rows × 685 columns to `04_output-tsa24.csv`, and produced
  an empty error CSV (header only). The refreshed `tipsy_curves_tsa24.csv`
  has 576 AU-age rows with `Logs_Grade_D/F/H/I/J/U/X/Y` and
  `Residual_Chips/Sawdust/Shavings/Trim/Bark`. `D` and `F` are zero for every
  AU; `H/I/J/U/X/Y` and all five residual components are populated. Added
  `workflows/national/bucking/tsa24_grade_curves_btc.py` and pointed
  `compile_tsa24_grade_curves.py` at it; no nominal site index, species
  surrogate, or grade-share rescale is applied. The corrected AU join uses
  the FEMIC AU crosswalk (BEC + leading species + FEMIC site-index class),
  including combined-species strata such as `SBS_SX+AT`, and reports 14
  AU-curve / 3 species-curve / 3 no-match pilot plots. TIPSY grade shares are
  H 2.6%, I 0.2%, J 58.0%, U 29.9%, X 6.9%, Y 2.4%, D/F 0%; grade-group proxy
  high-grade 2.8%, sawlog-group 60.8%, low-grade 39.2%. BTC residual fibre sums
  to 768.55 ODT/ha against 50.83 pilot ODT/ha (18.45x, n=14); per m³ the ratio
  is 3.47x. The industrial-sort comparison is marked unavailable because the
  industrial-logs bank was not requested; the old nominal-SI industrial table
  is replaced by a header-only file with a manifest supersede note. Hashes:
  `04_output` `0b7d020b…d69f9`, `tipsy_curves` `ccb7bb0e…540a`,
  `managed_au_curves` (grade table) `6fe02e12…2791c` in the data manifest.
  Data commit `0d15a4b`; this parent entry is the accompanying code/documentation
  commit.

- **Compiled TSA24 grade/residue curves from TIPSY `.taf` tables and validated
  the bucking pilot against them (superseded later the same day by the BTC
  `/TSR` entry above).** BTC remained unavailable on this host:
  the local `TIPSYbtc.exe` is a shell wrapper around
  `xvfb-run -a wine TIPSYbtc_real.exe`, and neither `xvfb-run` nor `wine` is
  installed; a bounded `femic tipsy run-btc` probe exited 127. The usable path
  was `fresh-tipsy`'s 151-field `.taf` parser. Added
  `workflows/national/bucking/tsa24_grade_curves.py`,
  `compile_tsa24_grade_curves.py`, and `validate_tsa24_grades.py`. The compile
  covers all 16 TSA24 managed AUs (576 AU-age rows) using `sw 0530P00` (SW
  SI 20; SE/BL surrogate), `pli0530P00` (PLI SI 23), and `fdi0535P00` (FDI
  SI 25), selecting the untreated run block closest to each AU's planted
  density. Grade volumes are share-preserving rescales to FEMIC managed
  volume; residual fibre is TIPSY/SYLVER mill residue, not harvest slash.
  `validate_tsa24.py` now reports an 11 AU-curve / 7 species-curve / 2
  no-match join. Exact industrial-sort TVD is 0.487 aggregate (0.670 mean,
  overlap 0.330); diameter and length marginal TVDs are 0.232 and 0.234.
  TIPSY grade shares are H 5.6%, I 0.3%, J 59.8%, U 27.8%, X 3.9%, Y 2.5%,
  D/F 0%; the pilot sawlog product share is 96.8% on comparable plots, a
  proxy gap because the pilot is dimension-only. TIPSY residual fibre sums to
  516.2 ODT/ha against 43.5 pilot ODT/ha (13.4x); per m³ the ratio is 2.9x.
  No value comparison: no same-basis interior grade price vector. Outputs:
  `data/derived/national/bucking/tsa24_grade_curves/` and the pilot report,
  summary, `grade_benchmark.csv`, and `TSA24_GRADE_CURVES.md`. Tests:
  `tests/test_bucking.py` (10 passed); `compileall` clean.

- **Added national bucking Phase 0 contracts and the bounded TSA24 pilot.**
  New `workflows/national/bucking/` package: `schema.py` (`TreeRecord`,
  `BuckedLog`, `ResidueLedger`, `GradeRule`, `GradeRuleSet`), `taper.py`
  (`TaperModel` protocol, `NemoraKozakTaper`, registry keyed by jurisdiction /
  species / BEC zone with explicit unsupported failures), `bucking.py`
  (`buck_stem_sorted` wrapper, `BuckedLog` + `ResidueLedger`, batch API),
  `tree_lists.py` (FAIB PSP adapter with documented column mapping and
  stratified sample selection), `rules/bc_interior.yaml` (TIPSY 4.5 interior
  dimension sorts; geometry-only, not official grade), `validate_tsa24.py`,
  and `README.md`. The pilot selected 20 plots / 300 live trees across SBS
  (210 trees), ESSF (45), ICH (30), and SBPS (15) and the dominant species
  (SW 91, BL 67, PL 42, AT 23, EP 23, SE 23, HW 12, FD 11, CW 5, AC 3).
  Bucking produced 134 logs, 47.864 m³ recovered, 10.289 m³ residue (17.7% of
  taper), and 4.630 ODT at the 0.45 ODT/m³ default density. Conservation
  passes at abs 1e-7 m³ + rel 1e-5; the reference bucker's internal balance
  passes at abs 1e-6 m³ + rel 1e-4. Paired sampled-tree merchantable volume is
  875.73 m³/ha observed vs 760.53 m³/ha bucked (relative bias −13.2%, MAE 5.76
  m³/ha, median ratio 0.832). VDYP7 matched 15/20 plots (observed-all relative
  bias −34.7%); TIPSY matched 11/20 (observed-all −20.8%) and the TSA24
  instance has no grade/residue/value columns; RSYC matched 13/20
  (observed-all +20.0%). No official grade is assigned; density, moisture, and
  chip yield are flagged assumptions. Added `metadata/bucking-pilots.csv`.
  Outputs: `data/derived/national/bucking/tsa24_pilot/`. Tests:
  `tests/test_bucking.py` (10 passed); `tests/conftest.py` makes the repository
  root importable under plain `pytest`.

- **Acquired RSYC national yield-curve artifacts and registered national
  bucking tool references.** Downloaded the RSYC `v20260923` model catalogue
  (`RSYC_models.zip`, 1,468,706 bytes, sha256 `86da5737…24940`), 150 km tile
  grid (`RSYC_tiles.gpkg`, 278,528 bytes, `f61e0ec0…7308b`), and ecological
  boundary v2 (`RSYC_ecostrat.gpkg`, 15,421,440 bytes, `e425818f…d87a4`) into
  `data/raw/national/rsyc/`. All three hashes match the upstream release
  manifests; ZIP and GeoPackage integrity checks pass; the 385 tile IDs join
  exactly to the 385 tile models. The catalogue is 27,208 × 42: 13,646 AGB +
  13,562 volume curves; 385 tiles, 12 ecozones, 36 ecoprovinces, 138
  ecoregions, 672 ecodistricts; 32 species codes + 3 model groups; catalogue
  age bounds 1–600 with 1–150 documented as the operational range. Recorded
  `ACQUISITION.md`, `PROVENANCE.csv`, and `CONTENTS.md`, including the
  published median validation ranges (R² 0.57–0.80, RMSE 31.3–53.3%, bias
  −31.4 to +9.3%) and the ecodistrict-438 `Betsiamiles`/`Betsiamites` spelling
  caveat. Added metadata-only `data/raw/national/bucking/REFERENCES.md`
  covering BuckR, FSvolLib, biomasscan, CFSForestTools, and the FVS Canadian
  variants (citation, licence, input/output contract, architecture role); no
  upstream code was cloned or vendored. Registered RSYC in
  `metadata/national-plot-data-inventory.csv` and
  `metadata/required-datasets.yaml`. Published the three annex objects to
  `arbutus-s3`; `git annex find --not --in arbutus-s3` is 0 repo-wide. Data
  commit `a77fdbc`; parent commit `011ac6f`.

- **Added the WS4 TSA instance factory and the first TSA24 pilot instance.**
  `workflows/ws4_instances/` now carries the standard (`README.md`), instance
  templates, `make_instance.py`, and the area-control runner
  (`area_control.py`). The runner validates the explicit AU/volume/treatment
  contract against the compiled tables, builds the area-weighted managed MAI
  curve, derives `R`, computes `Q = A*P/R`, and writes an oldest-first
  `schedule.csv`, per-AU/period `diagnostics.csv`, and `summary.json`. Two
  readings are explicit: `quota_scope` (`global` default, matching the
  documented TSA24/TSA29 reference; `per_au` for AU caps) and `eligibility`
  (`operable` default; `earliest_only` for a strict first-window rule). The
  TSA24 pilot at `data/derived/ws4/tsa24/` (data commit `f915fa1`) has
  `A = 3,060,473.1 ha`, `R = 100`, `Q = 306,047.3 ha/period`,
  `MAI(R) = 2.183 m3/ha/yr`, 205,959 schedule rows, and a 100-year WS4
  simulation. Period 1 harvest is 71.68M m3 (reference ~71.7M); the 10-period
  total is 814.04M m3, with period 10 at 69.94M m3 just below the 70M
  diagnostic floor. The schedule matches the reference first-entry area in
  every period; the remaining volume difference is the reference's multi-entry
  chains and later in-period offsets. Added
  `metadata/ws4-instances.csv`, the instance manifest, and a planning note.

- **Ran the first measured FVS-BC baseline on the BC FAIB PSP data.**
  Created the repository-local `.venv-fvsca` environment with
  `fresh-fvsbc` 0.21.4 (editable read-only checkout at `f5dce34`) and
  `fresh-fvsbc-pyfvs` 0.4.1 / PyFVS `e95172b` / FVS source `ef5fa847`, then
  ran the reference `run_baseline_backtest.py` on the default BEC-stratified
  sample: **139 intervals across 139 BEC variants, 0 skipped**, in 80.1 s wall
  (452,880 KB peak RSS). Initial BA bias -0.689 / MAE 0.836 m2/ha; end BA bias
  -4.168 / MAE 8.648; end TPH bias -391.85 / MAE 781.73; end QMD +0.901 /
  MAE 2.478; top height -4.820 / MAE 4.958; volume -53.890 / MAE 84.242.
  Envelope flags: 1 above p99 (`SBSwk1`) and 5 above max (`IDFxx2`,
  `SBPSxc`, `SBSmc3`, `SBSun`, `SBSwk3`). The run reproduces the reference
  D6-corrected all-zone baseline exactly (per-BEC bias/MAE maximum absolute
  difference `6.9e-13`) and the D5 end-BA numbers under D5's exclusion filter
  (-5.259 / 8.445, n=130); the SBPS `457,348 m2/ha` explosion is absent on the
  0.4.1 `REGENT`-fixed build. Added
  `workflows/national/fvsca/BC_BASELINE_REPORT.md` and the aggregate tables,
  metrics, probe aggregates, and a hashed manifest under
  `data/derived/fvsca_calibration/bc_baseline/` (data commit `0d577cd`, pushed
  to `origin/main`; no new annex objects, `git annex find --not --in
  arbutus-s3` = 0). Optional IDFdk probes: the four-candidate D7.3 subset
  reproduces the documented test-fold rows exactly; the D7.4 `--bundle` path
  fails on a `BAMAX` field-format mismatch (`53` vs `53.0`), and the documented
  `--ingrowth` path reproduces the D9.24 corrected percentages (test BA -5.6%,
  TPH +8.6%; train TPH +37.4%), not the stale D7.4 TPH table. Raw inputs
  unchanged; per-interval tables and FVS run directories stayed in `/tmp`
  because they carry site identifiers. Also ignored `.venv-*/` and the
  generated `pyfvs.log`.

- **Added the `fresh-fvsca` BC prototype, parity report, and consumer dataset.**
  `workflows/national/fvsca/` now contains the multi-variant contract
  (`VariantProfile`: BC implemented; ON and ACD stubs; AB, SK, MB, YT, NT
  explicitly unrepresented and needing non-FVS backends), the `PlotSource`
  protocol with `HarmonizedV0Source`, the BC consumer adapter, a build CLI, a
  parity harness against the read-only `fresh_fvsbc.calibration` builder, and
  seven focused tests. The BC dataset is
  `data/derived/fvsca_calibration/bc_faib_fvsbc_schema/` (data commit
  `097e5e7`): 7,341 source sites (7,337 with live visits), 24,962 plot-visit
  rows, 17,151 intervals, 1,814,527 tree-survival rows, 45 observed source
  species (13 direct JSP, 7 donor aliases, 25 unmapped), and 1,522 multi-plot
  visit rows with null volume and an explicit `volume_basis`. Parity against
  `fresh-fvsbc` 0.21.4 is exact for visit/interval/tree-survival rows, sites,
  plots, interval years, TPH, and post-filter tree rows; the report explains
  the one-endpoint dead-DBH difference, donor species, BEC handling, TSA
  enrichment, and multi-plot volume. Fixed four adapter/profile bugs exposed by
  tests: an interval-start `year` merge collision, a missing `TSA_DESC` column
  in tree survival, duplicate visit identifier columns, and a BEC zone parser
  that swallowed lower-case subzones. Output hashes are recorded in the dataset
  manifest; the gzip tree-survival table is published to `arbutus-s3`. Raw
  inputs unchanged.

- **FVSCA calibration v0 corrected and rebuilt after harmonization review.**
  The 2026-11-14 v0 (`0654864`, `e22dc2f`) is **invalid**: QC PEP
  `plot_area_ha` used `10000 / tige_ha`, so a 25 stems/ha plot was stored as
  400.0 "ha" instead of 0.04 ha (10,000x error), and MAGPlot
  `_visit_order` was assigned per site rather than per `(site_id, plot_id)`,
  discarding valid intervals (especially WFS nested plots). The corrected
  build also fixes float status codes (`10.0`/`0.0`), retains dead endpoints
  with null DBH, requires a start DBH, adds interval `visit_id0`/`visit_id1`,
  makes reconciliation use exact `explained_drops` counts, makes a missing
  manifest and empty required frames hard failures, validates output
  hashes/counts and the species crosswalk, honors `--sources`/`--no-wfs`/
  `--qc-gpkg` in `manifest["inputs"]`, passes the full ZIP site-id set to
  `load_wfs` under `--max-sites`, and converts NL `ORIGINAL_DATE` in
  `America/St_Johns`. New totals: 72,975 sites, 368,133 visits, 15,639,840
  tree rows, **7,228,291 intervals** (+216,965), 355 crosswalk rows, and
  69/69 validation checks. Sites/visits/trees are unchanged. New output
  SHA-256: `sites.csv` `7796f2c8...bccf54de9`, `visits.csv`
  `bd07dc11...f2dcbb61534c`, `trees.csv.gz` `a0b540f4...e3afffe`,
  `intervals.csv.gz` `b42845c5...92acdd3f9b`, `species_crosswalk.csv`
  `40748824...36f182e35c`, `manifest.json` `f49b1ec7...1bd63239`. The previous
  output hashes are retained in
  `data/derived/fvsca_calibration/v0/REVIEW_FIXES.md` and
  `/tmp/fvsca_v0_old/` (build-local backup): `sites.csv`
  `a88e6891...b128b6c8`, `intervals.csv.gz` `bf2781a3...c87b4c10`,
  `manifest.json` `ffe8ea09...fe13879f`, `QA.md`
  `0e293864...ccd6bbf3`. Added 13 focused regression tests. The corrected
  outputs were published to `arbutus-s3` (data `git-annex` branch) and
  recorded in `EVIDENCE_LOG.md`.

## 2026-11-14

- **FVSCA calibration v0 harmonized plot extract built and committed.** Added
  `workflows/national/plot_harmonization/` with a schema, FAIB/MAGPlot/QC
  PEP/NL FAL adapters, a streaming `build.py`, and a hard `validate.py`.
  The full build produced 72,975 sites, 368,133 visits, 15,639,840 tree
  measurement rows, 7,011,326 intervals, 355 species-crosswalk rows, and
  52/52 passing validation checks in
  `data/derived/fvsca_calibration/v0/` (data submodule commits `0654864` and `e22dc2f`).
  Output SHA-256: `sites.csv` `a88e6891...b128b6c8`, `visits.csv`
  `bd07dc11...f2dcbb61534c`, `trees.csv.gz` `a0b540f4...e3afffe`,
  `intervals.csv.gz` `bf2781a3...c87b4c10`, `species_crosswalk.csv`
  `40748824...36f182e35c`. The extract preserves source IDs, maps QC DBH
  mm→cm, keeps source expansion factors, leaves absent BAF/site index blank,
  and documents MAGPlot WFS overlap, NL sentinel/duplicate/plot-key drops, and
  FAIB blank-tree-key drops in `QA.md`. Raw payloads were not modified and no
  S3 publication was attempted.

- **Québec PEP and NL FAL permanent sample plots archived.** Added two open
  provincial corpora to the data submodule in commit `72da747`.
  Québec PEP (`data/raw/qc_pep/`): `PEP_GPKG.zip` 128,568,617 bytes, SHA-256
  `a5deb82b8e05c3226f83eb8daa4842900b6564e31b30915d9215f8a031faa5e5`;
  the ZIP contains `PEP.gpkg` (739,045,376 bytes; integrity check `ok`),
  12,834 plots, 53,632 visits, 12,611 remeasured plots, 2,210,417 tree
  measurements, 843,175 unique trees, 683,328 remeasured trees, 235,455
  sapling rows, 64 tally species (86 with saplings), 1970–2025, median
  interval 11.05 years, DBH median 133 mm (live 136 mm), no explicit site
  index, fixed-area plots 4/40/400/625 m² with no BAF. Licence CC BY 4.0.
  Documentation (`LISEZ-MOI_pep.pdf`, `DICTIONNAIRE_PLACETTE.xlsx`) and the
  CKAN package snapshot are archived alongside; manifest at
  `data/manifests/qc-pep-acquisition.csv`. The direct URL returns 404 to HEAD
  but 200 to GET; acquisition used GET.
- **NL FAL PSP archived.** Exported all 14 layers of the public
  `FAL_-_Permanent_Sample_Plots` FeatureServer into gzipped GeoJSON (2 point
  layers) and CSV (12 tables): 1,380 plots (412 mature, 968 immature),
  7,659 usable visits, 1,242 remeasured plots, 775,648 tree measurement rows,
  143,285 permanent-tree rows, 14 simplified species codes, visits
  1985–2026, DBH median 13.1 cm mature / 6.0 cm immature, height populated
  20.2% mature / 75.8% immature, 470 disturbance rows, 430 silviculture rows,
  no numeric site index. Every file was verified against the layer
  `returnCountOnly` count and hashed; total gzipped payload 31,795,357 bytes.
  One remeasurement row has a null `PLOTKEY`; one immature plot has no visit.
  Files under `data/raw/nl_fal_psp/`; manifest at
  `data/manifests/nl-fal-psp-acquisition.csv`. Licence Open Government
  Licence — Newfoundland and Labrador.
- **National landscape document and data runbook corrected.** Updated
  `planning/national-gy-data-landscape.md` to record the archived MAGPlot WFS
  supplement (41,840 sites; 1,900,092 tree-measurement features in seven
  gzipped layers at `data/raw/magplot/wfs/`) instead of describing it as
  in progress. Updated `docs/data-repository-runbook.md` to the working
  Arbutus remote (`object-arbutus.alliancecan.ca`, port 443, project-prefixed
  `publicurl`, plain `bucket`) and documented the new-cloud
  `<project-id>:<bucket>` addressing plus the `InvalidAccessKeyId` failure
  mode for old-cloud keys.
- **Arbutus S3 publication blocked by stale cloud credentials, not remote
  configuration.** The local `data` clone's git-annex branch was stale; after
  fetching `origin/git-annex`, `remote.log` already carries the 2026-10-11
  update (`host=object-arbutus.alliancecan.ca`, `port=443`,
  `requeststyle=path`, new project-prefixed publicurl, bucket unchanged at
  `clews-c2070-fresh-data`). No remote-parameter change was made. Anonymous
  GET of
  `https://object-arbutus.alliancecan.ca/a0d141b801c74d0a819c09b4bde9c3fb:clews-c2070-fresh-data`
  returns 200; the old host returns 403. Signed probes with the stored
  `~/.aws/credentials [default]` key return `InvalidAccessKeyId` on the new
  host (identical to a synthetic fake key) and `AccessDenied` on the old host,
  so the stored key is an old-cloud credential that the new Arbutus cloud does
  not recognise. No credential guessing was attempted and no S3 copy was
  retried; publication remains pending a new-cloud access key. Backup of the
  pre-fetch remote.log is at `/tmp/gep/opencode/remote.log.bak` (outside the
  repository).
- **National growth-and-yield data landscape plan committed.**
  `planning/national-gy-data-landscape.md` added in parent commit `3142bf7`.
  The document records the 2026-11-14 holdings, access matrix, and phased
  acquisition plan; no calibration result is claimed.
- **BC FAIB non-PSP companion corpus archived.** Mirrored all 457 files from
  `ftp://ftp.for.gov.bc.ca/HTS/external/!publish/ground_plot_compilations/non_psp/`
  (12 province-wide tables, 37 TSA directories × 12 tables, and the non-PSP
  dictionary): 269,779,544 bytes uncompressed, 52,765,031 bytes after
  `gzip -9 -n`. Archive at `data/raw/bc_faib_ground_plots/non_psp/`; per-file
  provenance at `non_psp/PROVENANCE.csv`;
  manifest at `data/manifests/bc-faib-ground-plots-nonpsp-acquisition.csv`.
  Profile: 9,512 sites, 11,951 visits, 367,096 tree rows, 275,264 unique
  trees, 76,667 remeasured trees, 2,028 remeasured sites, 7,484 single-visit
  sites, 37 TSAs, 16 BEC zones, 42 species, 1995–2025, DBH 97.5% populated,
  height 77.8% populated, site index 78.0% of visits, age 94.4% of visits.
  Site identifiers are disjoint from the 7,341-site PSP corpus (combined
  16,853 sites / 35,907 visits). The per-TSA tables are exact subsets of the
  province-wide tables. Updated `metadata/national-plot-data-inventory.csv`
  and `metadata/required-datasets.yaml` with the non-PSP row; root
  `ACQUISITION.md`, `CONTENTS.md`, and `PROVENANCE.csv` now cover both
  sub-corpora.
- **MAGPlot v1.2 archived (Tier-1).** Downloaded the open MAGPlot data
  package, data dictionary, and supplementary material from ca.nfis.org
  (HTTP 200; 127,123,248 / 933,385 / 288,487 bytes). Data package SHA-256
  `270055bb9eeed6ccd6cbd75f6a2026b54e340c5a4938af18ad06e479792888db`; ZIP
  integrity verified (9 members, 1,720,730,897 bytes uncompressed). Archived
  under `data/raw/magplot/` with ACQUISITION.md, PROVENANCE.csv, and
  MAGPlot_CONTENTS.md; manifest at `data/manifests/magplot-acquisition.csv`.
  The eight tables contain 39,130 sites; 257,031 plot visits; 20,890
  remeasured sites; 7,771,041 tree measurements; 2,866,168 tracked trees;
  1,573,761 tracked trees with repeat measurements; 140 species codes;
  1921-2024. Open coverage is BC, QC, NB, NL, YT, NT, SK, MB only (no
  AB/ON/PE/NS); published coordinates are approximate within +/-10 km;
  v1.2 lacks age/site-index/soil tables. The prior scratch ZIP (2,957,065
  bytes) is not the current package and was not used.
- **NFI plot documentation archived; data remains DUA-pending.** Ground plot
  docs (data dictionary 5.2, guidelines 5.0, compilation procedures 2.4;
  9,002,441 bytes) and photo-plot docs (5 PDFs; 4,796,400 bytes including
  HTML snapshots) archived under `data/raw/nfi/`. The nominal
  `/en/photo_plot` page returns HTTP 200 with a 404-error body; links were
  recovered from `/en/documentation` and recorded in LINKS.md. No NFI plot
  data payload was downloaded. Request route recorded:
  `https://nfi.nfis.org/en/datarequestform`; pre-submission package
  `metadata/license-determinations/nfi-targeted-request.md` unchanged.
- **CASFRI upstream pointer added.** `data/raw/casfri_v5/documentation/
  UPSTREAM_DATA.md` records the project page, data-dictionary pointer, and
  the fact that CASFRI is polygon inventory (not plot remeasurement) and
  the NRCan CKAN record is WMS-only. No CASFRI database payload downloaded.
- **Metadata registries updated.** `metadata/source-inventory.csv` magplot
  -> `archived`, nfi_ground_plot_data -> `request_pending_dua`, new
  `nfi_photo_plots` and `fvs_calibration` rows; `required-datasets.yaml`
  gains `magplot` (archived) and `nfi_photo_plots` (DUA-pending);
  `license-determinations/nfi-access.yaml` magplot -> `acquired/archived`
  with counts and adds nfi_photo; `readiness-gates.yaml` records the dated
  evidence without flipping `nfi_access` or `national_scaling`; new
  `metadata/national-plot-data-inventory.csv` (10 candidates).
- **MAGPlot WFS supplement archived.** Queried the NFIS MAGPlot WFS
  (`https://ca.nfis.org/mapserver/cgi-bin/magplot_trees_eng.cgi`, WFS 2.0.0)
  and archived `Sites_Summary` plus all SK/PEI/NL tree layers under
  `data/raw/magplot/wfs/` (7 gzip payloads; 1,900,092 tree features; 41,840
  sites). Paging required `sortBy=magp_web_key`; unsorted paging duplicated
  rows across page boundaries. Reconciliation: ZIP 39,130 sites vs WFS 41,840
  (+2,048 SK, +691 PE, +39 WFS-only NL; 68 ZIP-only NL; net NL -29). Schemas
  differ: WFS trees use `magp_web_key`/`meas_year` and lack ZIP `meas_num`
  and flag columns.
- **BC FAIB PSP compilations archived.** Resolved BC Data Catalogue
  `824e684b-4114-4a05-a490-aa56332b57f4`; the catalogue HTTPS FTP link returns
  401, but the same tree is anonymously readable over `ftp://` and yielded the
  complete PSP compilation (10 CSV tables + dictionary; 841,636,204 bytes
  uncompressed, 131,056,666 bytes gzipped). Exact `fresh-fvsbc` corpus:
  7,341 sites / 23,956 visits / 5,735 remeasured sites; 3,217,648 tree
  measurements; 1,138,768 unique trees; 803,868 remeasured trees; 1921-2025;
  12 BEC zones; 45 species; DBH 0.5/12.4/401.7 cm; height 99.99% populated
  (75.5% DBH-estimated); site index/age present for ~90-92% of visits. The
  non-PSP companion was not acquired.
- **Metadata registries updated.** `national-plot-data-inventory.csv` gains
  the WFS supplement row and updates BC FAIB to archived with counts;
  `source-inventory.csv` gains `bc_faib_ground_plots` and links it from
  `fvs_calibration`; `required-datasets.yaml` gains
  `bc_faib_ground_plots` (archived, checksum, 7,341/23,956). Acquisition
  manifests added at `data/manifests/magplot-wfs-acquisition.csv` and
  `data/manifests/bc-faib-ground-plots-acquisition.csv`.
- **Publish status.** Data-submodule commit `f58d2ca` contains the new
  payloads and documentation (19 new git-annex objects). The `arbutus-s3`
  copy for the prior annex (`d45a047`/`f2fbaa5` paths) was attempted on
  2026-11-14 with the `git-annex-s3` wrapper but returned HTTP 403 Forbidden;
  no retry with credentials was made. GitHub pushes and S3 publication remain
  pending. Parent repo commit follows.

## 2026-08-17

- Closed the fail-open hole in `directory_contains_tracked_files` found in
  code review of the workdir hardening: `git ls-files` nonzero exits now log
  stderr at WARNING level, return `False` only for the expected
  "not a git repository" / "must be run in a work tree" refusals, and fail
  closed (treat the directory as tracked, blocking the destructive rebuild)
  for any other error — `safe.directory` dubious-ownership protection,
  corrupt index, or leaked `GIT_DIR`/`GIT_WORK_TREE` can no longer silently
  bypass the guard. `subprocess.TimeoutExpired` is now re-raised as
  `RuntimeError` with the target directory attached. Added
  `TestRegenerateWorkdirTrackedFileGuard` (6 tests) covering the tracked-file
  block, the `force=True` opt-in, untracked-only and non-repo free rebuilds,
  corrupt-index fail-closed behaviour, and the timeout path. Broadened the
  `AGENTS.md` fail-closed collateral rule to halt on any `D` in either
  status column of `git status --porcelain` (staged `D ` as well as
  unstaged ` D`).
- Hardened `fresh_fibre.bc_qc_mini_2prov.regenerate_workdir` after a forensic
  review found silent data loss: the helper defaulted to the git-tracked
  fixture directory (`metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/`)
  and ran `shutil.rmtree` + `shutil.copytree` directly over versioned content,
  so any committed fixture file the generator no longer writes would vanish
  without a trace. The workdir target now defaults to a fresh temporary
  directory, and regeneration over a directory containing git-tracked files
  (detected via `git ls-files`) fails fast with a `RuntimeError` unless the
  caller passes the new explicit `force` opt-in (`--force-regenerate-workdir`
  on the CLI; `force_regenerate` on `run_full_two_province_variant` /
  `run_full_two_province_real_yield_variant`). Added the fail-closed
  "unexpected deletions" collateral rule to `AGENTS.md`: any unexpected ` D`
  entries in `git status --porcelain` halt the session pending investigation.
- **P7.1.CASFRI.13/14 support tooling: FreshForge materialization workflow
  for ERA5 normals + SCANFI species downloads.** Registered two provider node
  contracts in `fresh_fibre/freshforge_materialization.py`
  (`download_era5_normals`, `download_scanfi_species_bc`) with command
  builders that dispatch to the repo scripts through the overlay venv; added
  the workflow/overlay pair
  `examples/freshforge/era5_scanfi_species_materialization_workflow.yaml` +
  `examples/freshforge/era5_scanfi_species_overlay.yaml`; added
  `scripts/download_scanfi_species_bc.py` (BC-relevant SCANFI v2 `spsCC_*`
  layers from the NRCan FTP with SHA-256 sidecar verification into
  `raw/nrcan/scanfi_v2/species/`). ROADMAP.md P7.1.CASFRI.14 checkbox marked
  [x] (commit `d5f3f69`).

## 2026-08-16

- **P7.1.CASFRI.12: Geographic crosswalk for the Ghafghazi 2017 facility
  layer.** The FCM LP solves on internal km-distances between TL/W transload
  nodes and does not require lat/lon; this crosswalk is the wall-to-wall
  visualisation and spatial-join layer on top.
  - `metadata/facility_geocoding.csv` — 1,371 FCM facilities joined to the
    ISED Mills DB (738) and CFS `suppliers.dbf` (622) via (province,
    facility_class, numeric_id); match statuses MATCHED_BOTH,
    MATCHED_MILLS_DB_ONLY, MATCHED_SUPPLIERS_ONLY, MATCHED_BOTH_CONFLICT,
    NO_MATCH (~1,360 unique mills).
  - `metadata/fmu_tl_anchors.csv` — 733 FMU → TL hub transport-graph anchors
    from `tmp/FMU.xlsm`; FMUs carry no polygon geometry (transport-graph
    anchored only).
  - `metadata/tenure_area_rollup.csv` — per-province + national subtotals:
    GFW tenures 3,680 polys / 4,438,087.73 km^2 / 11 jurisdictions (no NU);
    Data Basin tenures 10,222 polys / 2,100,451.56 km^2 / 9 provinces (no
    YT/NT/NU).
  - `metadata/databasin_tenure_provenance.csv` — 10,222 rows with per-record
    provincial source attribution (9 unique source_agency values).
  - `metadata/source-inventory.csv` row 55 appended
    (`ghafghazi_et_al_2017_geographic_crosswalk`; row 32 notes amended);
    `facility_layer` gate notes appended; Geographic Crosswalk Note added to
    `planning/ffcm-archive-analysis.md`; ROADMAP.md P7.1.CASFRI.12 marked
    [x]. No data-submodule changes.
- **P7.1.CASFRI.13: SCANFI v2 2025 species crown-closure layers acquired.**
  10 `spsCC_*` rasters (2025 time step, ~14.6 GB) downloaded, SHA-256
  verified, annexed into the data submodule, and published to arbutus-s3:
  balsamFir (1.16 GB), blackSpruce (3.31 GB), broadleaf (2.68 GB), douglasFir
  (242 MB), jackPine (1.62 GB), lodgepolePine (752 MB), otherConiferous
  (2.58 GB), ponderosaPine (82.4 MB), tamarack (1.87 GB), whiteRedPine
  (275 MB). `data/raw/scanfi/v2/` now holds 25 GeoTIFFs (5 att_* + 10
  spsCC_* + 10 .aux.xml); acquisition manifest at
  `data/manifests/scanfi-v2-2025-species-acquisition.csv`. The layers provide
  the wall-to-wall species crown-closure percentages the FCM LP needs for
  yield stratification and product allocation.
  - `metadata/source-inventory.csv` scanfi_v2 status →
    `accepted_expanded_subset`; `species_composition` gate notes appended
    (remaining: full 1985-2025 time series, .ovr overviews, northern
    extrapolation mask); acquisition reconciliation section appended to
    `metadata/source-reviews/scanfi_v2.md`; ROADMAP.md P7.1.CASFRI.13 marked
    [x]. The remaining SCANFI package stays outside the approved subset.
- **P7.1.CASFRI.14: ERA5 1991-2020 climate normals acquired — 9 NetCDF + 48
  derived bioclim COGs.** Downloaded from the Copernicus CDS
  `reanalysis-era5-single-levels-monthly-means` dataset (CC-BY +
  licence-to-use-copernicus-products accepted programmatically 2026-08-16):
  2 m temperature, total precipitation, and 2 m dewpoint temperature, each in
  three decade chunks (1991-2000 / 2001-2010 / 2011-2020; 360 months; ~95 MB
  total), Canada bbox 41-84°N / 141-52°W at 0.25° in EPSG:4326; annexed in
  data submodule `c346518` under `raw/ecmwf/era5/normals/` with
  ACQUISITION.md, download logs, and the 58-row manifest
  `manifests/ecmwf-era5-normals-acquisition.csv`.
  - `scripts/download_era5_normals.py` — resume-safe CDS API fetch (3
    variables × 3 decade chunks; NetCDF magic validated on every chunk).
  - `scripts/compute_era5_normals.py` — 30-year monthly normals, annual
    summaries (MAT, MAP, d2m_annual), 9 bioclim variables (GDD5, FFP, VPD,
    Twarm, Tcold, Pwarm, Pcold, Pseas, continentality), unit conversion
    (K→°C; m/day → mm/month via 365-day climatology), Canada-boundary clip.
  - `scripts/validate_era5_normals.py` — GeoTIFF / EPSG:4326 / nodata checks
    plus physical sanity bounds on the annual and bioclim rasters.
  - 48 derived COGs at `derived/era5-normals/` (36 monthly normals + MAT /
    MAP / d2m_annual + 9 bioclim), 173×357 grid, nodata=-9999. Known
    limitations recorded in ACQUISITION.md: t2m min/max not offered by the
    ERA5 monthly-means product; FFP approximated from monthly means; VPD from
    monthly mean T/Td underestimates peaks. ROADMAP.md P7.1.CASFRI.14 marked
    [x] (commit `d5f3f69`); the `climate_predictors` gate is not yet flipped
    — remains `blocked` in `metadata/readiness-gates.yaml` pending gate
    bookkeeping.

## 2026-08-11

- **P7.1.CASFRI.8: Dataset ingest batch 2 to arbutus-s3 (18 archives).**
  19 attempted / 18 archived / 1 deferred under the m0331 directive to
  datalad-stash listed-but-unmaterialized datasets; per-archive sha256
  sidecars + PROVENANCE.csv; all 18 confirmed 2-copy (here + arbutus-s3) via
  `git annex whereis`. Batch aggregate ~11.34 GiB; combined annex ~39.45 GiB.
  - Beaudoin kNN archives → `data/raw/beaudoin_knn/` (PROVENANCE.csv 212
    rows): kNN-StructureStandVolume.tar (1.69 GiB), kNN-SpeciesGroups.tar
    (1.93 GiB), kNN-SpeciesDominant.tar (1.26 GiB), kNN-Genus.tar (1.54 GiB),
    kNN-LandCover.tar (620 MB), kNN-Soils.tar (2.81 GiB). kNN-Dominant.tar
    deferred — the upstream URL 302-redirects to a placeholder page;
    rationale at `data/raw/beaudoin_knn/DOWNLOAD_DEFERRED_kNN-Dominant.txt`.
  - NRN per-province GeoPackage ZIPs for the 12 remaining jurisdictions →
    `data/raw/nrn/` (PROVENANCE.csv 40 rows): AB 253 MB, SK 147 MB, ON
    484 MB, QC 384 MB, NB 63 MB, MB 42 MB, NS 124 MB, NL 23 MB, PE 14 MB,
    NT 10 MB, YT 6 MB, NU 4 MB.
  - `metadata/source-inventory.csv` notes updated on 5 rows (nrn_vectors,
    nrwn_vectors, beaudoin_knn_attribute_archives_2014_2017,
    yk_forest_resource_roads_50k, provincial_forest_resource_roads_themes).
- **P7.1.CASFRI.9: SAFORAH 2.0 fallback resolves the kNN-Dominant
  pseudogap.** "kNN-Dominant" is not a real upstream tarball — dominant
  species per pixel is derivable as argmax across the 109 per-species layers
  in the already-archived kNN-Species.tar; the historical kNN-Dominant.tar
  URL is a persistent 302 placeholder for a derived product that was never
  packaged separately. Captured the SAFORAH 2.0 `ca_kNN_DominantGenus_250m_r`
  WMS layer as visual confirmation into `data/raw/beaudoin_knn_v2/`: WMS
  GetCapabilities XML (81 KB), GeoNetwork catalogue JSON (15 KB; record UUID
  `de39ef283de2d96e7bfabf0a789b5f95458d5a5a`), 3 WMS GetMap PNGs (PNG magic +
  IHDR verified; arbutus-s3 confirmed), PROVENANCE.csv, and the
  PROXY_REPLACEMENT_kNN-Dominant.txt lineage doc. WFS/WCS endpoints are
  disabled upstream (OWS probe) and the bulk GeoTIFF is not publicly
  downloadable, so raw dominant-genus values require CFS Data Manager
  engagement — the argmax derivation remains the operational path.
  `metadata/source-inventory.csv` rows 45-46 notes updated.
- **P7.1.CASFRI.10: kNN-Dominant cleanup + derivation smoke test.** Cleanup
  option (c) executed on `data/raw/beaudoin_knn_v2/`: the 3 WMS PNG snapshots
  unannexed (74 KB); GeoNetwork catalogue JSON, PROXY_REPLACEMENT lineage
  doc, and PROVENANCE.csv retained as durable research artefacts. Added
  `tests/test_knn_dominant_derived.py` (3 tests) anchoring in code the argmax
  derivation formula over kNN-Species.tar and locking the known-unavailable
  status of kNN-Dominant.tar at SAFORAH 1.0 (302 placeholder), the NRCan
  mirror (no per-layer category), and SAFORAH 2.0 (WMS-only).
  `metadata/source-inventory.csv` row 46 notes + next_action updated.
- **P7.1.CASFRI.11: NRWN per-province batch 3 archived; Ghafghazi 2017
  facility layer accepted and `facility_layer` gate resolved.**
  - NRWN: 33 of 39 attempted files archived to `data/raw/nrwn/` (~109 MB) —
    GML EN/FR + KML EN for BC + AB/SK/MB/ON/QC/NB/NS/NL/NT/YT (GML EN
    31.45 MB / GML FR 31.48 MB / KML EN 45.92 MB; KML files are .kmz, caught
    by probe correction); PE + NU deferred (no per-province directory on the
    ftp.maps.canada.ca mirror). PROVENANCE.csv now 49 rows; ACQUISITION.md
    updated; `nrwn_vectors` notes record the expansion and the PE/NU
    deferral.
  - Facility layer: the national Ghafghazi et al. 2017 FFCM payload
    (`tmp/Saeed_data.zip`, sha256 `d59d6ac3…b335d2`) accepted as the
    canonical facility-layer source per the project-start user directive —
    `metadata/source-inventory.csv` row 54 appended
    (`ghafghazi_et_al_2017_ffcm_payload`) and row 32 (forest_facilities_2013)
    marked superseded; `metadata/readiness-gates.yaml` facility_layer →
    `resolved_pending_provenance_review` (blocking_datasets swapped to the
    new row); `metadata/ffcm-archive/data-source-disposition.csv` row 12
    rewritten "Accepted per user directive";
    `metadata/ffcm-archive/prototype-gap-register.csv` gap_003 closed;
    Resolution Note prepended to `planning/ffcm-archive-analysis.md`;
    ROADMAP.md P2.2 items marked [x]. Canonical parsed CSVs:
    `metadata/ffcm-archive/parsed/facilities.csv` (1,371 facilities),
    `processing_lines.csv` (957), `routes.csv` (38,390); the raw archive
    stays ignored under `tmp/` per the Data Boundary.

## 2026-08-09

- **P7.1.CASFRI.1 + P7.1.CASFRI.3–7: First concrete implementation increment after the source-review / planning cycle.** QC inventory openly-downloadable from MFFP / Données Québec (CC-BY 4.0, no DUA) registered in `data/raw/qc_inventory/2023-ieqm-eighth/`: provenance layer (LISEZ-MOI 1.3 MB, per-feuillet index 31 KB, per-agence index 8 KB) plus a real-leaf sample (feuille 21M, 815 MB). CASFRI v5.3.1 (commit `c09a70cc1f90894a1d32c2f20606430b49ae274f`, 2021-08-17) translation-table sidecar vendored into `data/raw/casfri_v5/translation_tables/` (129 CSV files); GPL-3.0 framework source (conversion/, workflow/, helperfunctions/, summary_statistics/, translation/test/) intentionally **NOT** vendored.
  - `fresh_fibre/casfri_qc_reader.py` — QC inventory → CASFRI-shape 30-column row adapter. Reads translation tables at runtime; tolerant of QC producer-naming drift via multi-alias column dispatch; surfaces BC+FDC, AT, BL; QC+ SAB, EPB, BOP, BOJ, MEH, PEU producer codes via `qc_ini03_species_lookup.csv` and cross-jurisdiction `species_code_mapping.csv`.
  - `fresh_fibre/species_canonical.py` — cross-jurisdiction species canonicalisation with provenance prefix policy (BC_*, QC_*) preserved alongside CASFRI canonical (`PICE_MAR_###`, `POPU_TRE_###`, `BETU_ALL_###`, …). Deterministic `decision_id` indexed via `hashlib.sha256`.
  - `fresh_fibre/qc_ws3_export.py` — QC inventory → ws3-shaped harvest-output bridge. Mirrors `fresh_fibre.scanfi_supply` but sources cells through the QC mini fixture and CASFRI translation.
  - `fresh_fibre/bc_qc_mini_2prov.py` — two-province LP fixture runner. Builds merged fixture (BC mini base + QC mini), regenerates the graph, runs the LP, emits `bc_qc_mini_supply_comparison.csv`, `bc_qc_mini_pathway_flow.csv`, `bc_qc_mini_solution_summary.csv`, `bc_qc_mini_variant_run_matrix.csv`.
  - `metadata/source-crosswalks/qc_casfri_v5_schema.md` — schema crosswalk mirroring `nfi_variable-crosswalk.md` style with explicit gap register (10 named gaps including QC `CODE_DEN` A-E bucket-to-percent translation, height layer-2 defaulting, residue-policy gap, station-join gap, NFL split pending, disturbance 5E IEQM un-resolved).
  - `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/` — fixture directory; LP solves `Optimal` (objective `~6.1 M CAD`) on a fresh generation.
  - `tests/test_casfri_qc_reader.py` (24 tests), `tests/test_species_canonical.py` (23 tests), `tests/test_bc_qc_mini_2prov.py` (8 tests) — all green.
  - `planning/qc_two_province_incremental_memo.md` — implementation memo recording motivation, sources, schema crosswalk, supply lane design, solution summary, readiness-gate impact, and next-step sequencing.
  - `metadata/readiness-gates.yaml` — two gates (`species_composition`, `national_scaling`) advance from `blocked` to `in_progress` with explicit evidence notes citing the new QC inventory acquisition, the BC + QC LP solve, and the jurisdictional-prefix policy.
  - `ROADMAP.md` P7.1.CASFRI sub-step statuses updated (5 of 7 subtasks completed this increment; P*.2 and P*.6 deferred pending license counsel).
  - `fresh_fibre/scanfi_supply.py` — `INVENTORY_DIAGNOSTIC_POLICIES` set extended to include QC-specific value policies (`qc_species_resolved_via_lookup`, `qc_species_unmapped_unresolved`, `qc_species_empty_skipped`) so that the existing post-LP validation accepts the new QC supply rows.
- Completed P7.5: Created `planning/phase-7-production-readiness-memo.md` documenting the diagnostic-only boundary of v0.1.0a1, all 8 readiness gates (7 blocked: `nfi_access`, `climate_predictors`, `residue_yield_policy`, `calibrated_cost_surfaces`, `species_composition`, `national_scaling`, `facility_layer`; 1 in_progress: `disturbance_calibration`), gate-by-gate post-alpha work program with priority ordering and dependencies, and verified release criteria (56 tests pass, git diff --check clean, compileall clean, branch pushed, CHANGE_LOG/ROADMAP current).
- Phase 7 complete: All P7.1–P7.5 tasks checked. Phase 8 (v0.1.0a1 Conceptual Alpha release preparation) authorized.
- Documented CASFRI v5 schema integration plan: added `metadata/source-reviews/casfri_v5_schema.md` (D1 source review with the four-artifact disambiguation table, 49-row truth table, schema inventory, ws3 crosswalk, GPL-3.0 licensing posture, Tier 1–5 acquisition strategy, and readiness-gate impact), created `planning/casfri-v5-schema-integration-plan.md` (D2 plan with P7.1.CASFRI subtask breakdown P*.1 through P*.7 plus a Phase 9 candidate stub, schema-mapping sketch, GPL-3.0 contamination risk register, acceptance criteria, and sequencing vs. the in-flight NFI Ground Plot DUA), added `casfri_v5_schema` row to `metadata/source-inventory.csv` (status=`candidate`, GPL-3.0, framework-sidecar submodule pattern; NOT promoted to required-datasets.yaml), updated `metadata/readiness-gates.yaml` `species_composition.notes` to reference the proposed child issue under governing #1 (status remains `blocked` per the team's existing discipline until Tier-3 ingestion evidence exists). Source review distinguishes the four CASFRI-named artifacts (U. Laval/Racine/Edwards/Houle conversion framework vs. NRCan/NFIS WMS raster vs. 49 provincial/territorial FRIs vs. SCANFI v2). Child issue naming `#29` is a **proposal**; the team creates the actual GitHub issue.
- Reconciled SK (Saskatchewan) row in `metadata/source-reviews/casfri_v5_schema.md` §3 per reviewer Major finding M1: split SK into "Open (SK Standard Unrestricted Use Data License v2.0) for older vintages" (added to §3.a) and "DUA for newer BAM/BEACON-derived products" (kept in §3.c). Updated `planning/casfri-v5-schema-integration-plan.md` P*.2 acceptance criteria and `ROADMAP.md` P7.1.CASFRI to include SK reconciliation as Tier-3-prerequisite. Reviewer confidence recovered to ~95% (was 75% pre-reconciliation).
- Registered candidate source `nlcc_landcover_2005_250m` (NRCan/CCRS Land Cover of Canada 2005 V1.4 at 250 m) in `metadata/source-inventory.csv` as a complement to SCANFI for land-cover-class diagnostics. `license_status=TO_VERIFY` pending source review.
- Registered candidate source `beaudoin_knn_attribute_archives_2014_2017` (Beaudoin et al. 2014/2017 SAFORAH 2.0 kNN national forest attribute archives) in `metadata/source-inventory.csv`; reviewed in `metadata/source-reviews/beaudoin_knn_archives.md`. Closes `species_composition` and `residue_yield_policy` readiness gates once ingested via DataLad into `data/external/nrcan_saforah/`.
- Registered candidate source `saforah2_csw_catalogue` (NRCan/CFS federated forestry CSW 2.0.2 catalogue) in `metadata/source-inventory.csv`; reviewed in `metadata/source-reviews/saforah2_csw_catalogue.md`. Discovery-only layer; harvest automation written at `scripts/discovery/saforah2_csw_harvest.py`.

## 2026-08-08

- Archived verified CanLaBS v2 payload (1,751,083,497 bytes, SHA-256
  152ba33cca7c473ddc6c7d804d0855581f6d734b50f300637c8f2ee040ac5e8b) to
  DataLad at `data/raw/nrcan/canlabs_v2/` and pushed to arbutus-s3 remote.
  Updated `required-datasets.yaml`: `archive_status=archived`,
  `datalad_mirror.include=archived`.
- Extracted CanLaBS raw dNBR provenance for all 6 SCANFI diagnostic fixture
  windows and added 10 provenance columns to
  `diagnostic_inventory_cells.csv`: `canlabs_dnbr_mean`, `canlabs_dnbr_min`,
  `canlabs_dnbr_max`, `canlabs_dnbr_median`, `canlabs_valid_pixels`,
  `canlabs_nodata_pixels`, `canlabs_total_pixels`, `canlabs_value_policy`,
  `canlabs_window_bounds`, `canlabs_source_path`. All values are raw signed
  dNBR integers; no severity classes, thresholds, or yield multipliers.
- Resolved NFI standard reports license: NFI non-commercial reproduction notice
  permits personal/public non-commercial use with attribution. Updated
  `source-inventory.csv` status to `accepted` and promoted to
  `required-datasets.yaml` with `datalad_mirror.include=yes`.
- Confirmed NFDP harvest tables OGL Canada v2.0 license. Updated
  `source-inventory.csv` status to `accepted` and added new entry to
  `required-datasets.yaml`.
- Added 10 jurisdiction-specific forest/resource road child sources to
  `source-inventory.csv` (BC, AB, SK, MB, ON, QC, NB, NL, YT, NT) as follow-up
  to NRN accepted baseline.
- Created `metadata/readiness-gates.yaml` with 8 cross-cutting production
  readiness gates: `nfi_access`, `climate_predictors`, `residue_yield_policy`,
  `calibrated_cost_surfaces`, `species_composition`, `national_scaling`,
  `facility_layer`, `disturbance_calibration` (7 blocked, 1 in_progress).
- Verification: parent/data `git diff --check` passed; `pytest tests/ -q` 56
  tests passed; git-annex confirms CanLaBS v2 present locally and on arbutus-s3.


- Completed P6.5 as a diagnostic-only validation memo. The six heuristic SCANFI
  windows are explicitly quarantined as software fixtures and must not support
  BC-scale inferential claims.
- Recorded developer approval for G0/G1 in
  `planning/scientific-model-recovery-plan.md`; G2–G8 remain unapproved.
- Applied `SOFTWARE_FIXTURE_NOT_EVIDENCE` to all 30 rows in the six SCANFI
  fixture manifests and set the six parent inventory rows to
  `value_policy=quarantined` and `sampling_status=quarantined`.
- Created and attached recovery task `#27` under Phase 7 parent issue `#26`.
- Created and attached Phase 7 source-triage child task `#28` under parent
  issue `#26`, linked to governing issue `#1`.
- Reconciled CanLaD triage evidence: public raster access confirmed on
  2026-08-05, so the queue and required-dataset registry now record `canlad` as
  accepted while preserving the documented subset-first archive boundary.
- Removed the stale BIMA registry entry; `biomass_inventory_business` remains
  rejected because its inspected content is agricultural biomass and does not
  provide forest facilities or mill-residue data.
- Reconciled SCANFI v2 acquisition evidence across the Phase 6 plan, source
  queue, inventory, registry, and source review: the authorized five-file 2025
  subset is checksum-verified and archived, while all broader SCANFI expansion
  remains separately gated.
- Clarified Pre-CanLaD v2 semantics: the source is accepted, but mirroring its
  four core rasters and documentation remains pending PI approval.
- Added `canlabs_v2` to the curated registry with an explicit provenance gate:
  the local BigTIFF/server Content-Length mismatch must be resolved by a fresh
  transfer and checksum comparison before model use or DataLad archiving.
- Validation: parent/data `git diff --check` passed; focused BC mini regression
  passed with 21 tests.

## 2026-08-04

- **SCANFI v2 2025 acquisition complete**: Downloaded all 5 authorized rasters
  from ftp.maps.canada.ca (~20.6 GB total), verified SHA-256 checksums, added
  to git-annex at `data/raw/scanfi/v2/`, created acquisition manifest at
  `data/manifests/scanfi-v2-2025-acquisition.csv`, and pushed to origin.
  - Files: biomass (6.4G), height (2.9G), closure (4.0G), nfiLandcover (1.6G),
    age_median (5.0G)
  - **S3 publish complete**: All 5 files published to arbutus-s3 (~20.6 GB
    verified). P6.1 now authorized to proceed.

- Reconciled source-triage and phase-closeout notes against current `main` and
  GitHub state. Restored the Data Basin Canada forest-tenures candidate to the
  source inventory, added it to the active triage queue, updated Phase 3 and
  Phase 5 merge/closure status, and advanced the roadmap handoff from Phase 5
  design to Phase 6 implementation.
- Phase 6 initiation: confirmed SCANFI v2 2025 rasters absent from data
  submodule (fresh scaffold at commit `731667f`), created acquisition blocker
  plan at `planning/phase-6-scanfi-acquisition-plan.md`, and prepared Phase 6
  contracts and skeleton artifacts that do not require raster content.
- Added source review for `biomass_inventory_business` (
  `metadata/source-reviews/biomass_inventory_business.md`) noting field
  inspection needed before archive decision.
- Added source review for `databasin_canada_forest_tenures` (
  `metadata/source-reviews/databasin_canada_forest_tenures.md`) noting member
  dataset URL, license, provenance, and FMU-boundary fit require resolution
  before materialization.
- Created Phase 6 real-sampled fixture directory at
  `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/` with
  skeleton `diagnostic_inventory_cells.csv` (six planned fixture cells, correct
  column schema, placeholder values pending raster sampling) and `README.md`.
- Created Phase 6 validation memo skeleton at
  `planning/phase-6-validation-memo.md` with check tables and completion
  criteria.
- Updated ROADMAP.md Phase 6 section to reflect P6.0 blocker status and
  contracts completed.

## 2026-07-03

- Added the P5.5 Phase 5 decision memo and Phase 6 authorization table,
  allowing only tiny SCANFI diagnostic fixture materialization while blocking
  broad raw checkout, restricted NFI payloads, production assumptions, and
  national scaling.
- Added the P5.4 conceptual-alpha readiness surface, updated the forest
  inventory/yield gap register to reflect designed-but-not-materialized tiny
  real-sampling contracts, and kept Phase 6 materialization and production
  readiness gates explicitly open.
- Added the P5.3 tiny real-sampling validation contract, separating diagnostic
  acceptance checks for future sampled SCANFI fixture outputs from production
  calibration gates for residue yield, costs, climate predictors, NFI access,
  and benchmark validation.
- Added the P5.2 tiny SCANFI extraction contract, defining the five selected
  SCANFI v2 2025 source layers, planned DataLad raw/window/manifest paths,
  parent-repo derived-output boundary, nodata/sampling/checksum policies, and
  regression checks without materializing raster payloads.
- Added the P5.1 tiny BC real-sampling fixture-cell skeleton, defining six
  planned SCANFI raster-window cells that cover all current BC mini
  SCANFI+Gauthier spatial units and supply nodes without materializing raw
  rasters or sampled payloads.

## 2026-07-02

- Added the `v0.1.0a1` Conceptual Alpha release roadmap and opened Phase 5
  planning for a tiny public-safe SCANFI real-sampling fixture skeleton,
  connecting the Phase 4 real-input gates to release-bound Phases 6 through 8.
- Added a CLEWS-owned FreshForge materialization provider and workflow fixtures
  for initializing the `data` DataLad submodule and selectively materializing
  lightweight `metadata` and `manifests` paths through the FEMIC-style
  provider/overlay pattern.
- Added repo-root `.venv` bootstrap guidance requiring DataLad and FreshForge
  from the local virtual environment and `git-annex` as an OS-level
  prerequisite.
- Merged Phase 3 and the CLEWS FreshForge materialization workflow to `main`,
  closed the Phase 3 parent issue, and opened Phase 4 for real diagnostic
  SCANFI+Gauthier input materialization.
- Added the Phase 4 planning note, GitHub issue hierarchy, and
  `real-diagnostic-input-contract.csv` to define the first tiny real-input gate
  across SCANFI rasters, Gauthier predictors, NFI validation/access, residue
  yield, and cost calibration.
- Added a Phase 4 SCANFI diagnostic FreshForge materialization overlay and
  workflow targeting only `data/metadata/scanfi-diagnostic` and
  `data/manifests/scanfi-diagnostic`, with tests and docs confirming raw
  SCANFI rasters remain opt-in.
- Added `gauthier-predictor-access-gates.csv` to distinguish accepted
  diagnostic predictors from open access, license, climate, species
  composition, measured-volume, and NFI reference-pixel gates.
- Added `residue-cost-validation-policy-gates.csv` to keep logging residue,
  mill residue, cost calibration, market prices, and validation benchmarks as
  explicit production-acceptance gates rather than implicit diagnostic
  assumptions.
- Added the Phase 4 decision memo and completed the Phase 4 roadmap task
  checklist, recommending a follow-on tiny real-sampling implementation phase
  that keeps raw rasters and private data in the DataLad boundary.

## 2026-06-27

- Added the Gauthier et al. 2015 method as the primary yield-policy
  reproduction path, including a lightweight coefficient table, synthetic
  diagnostic evaluator fixture, candidate yield-rule output, and method input
  gap matrix.
- Added Phase 3 workflow scaffolding for diagnostic ws3 supply and yield
  replacement, including a dedicated phase planning note, roadmap task/subtask
  breakdown, feature-branch name, and GitHub issue hierarchy draft.
- Added P3.7 as a Phase 3 continuation, tightened CLEWS agent workflow notes
  around the UBC-FRESH roadmap/issue/branch pattern, and created child issue
  `#11` under parent issue `#2`.
- Added a SCANFI-compatible Gauthier yield-rule adapter, the
  `bc-mini-scanfi-gauthier-diagnostic` solved LP variant, and a four-lane BC
  mini comparison that preserves the existing LP contract while replacing log
  yield placeholders with diagnostic Gauthier method-reproduction values.

## 2026-06-26

- Added a tested `fresh_fibre.ws3_export` bridge from normalized ws3
  harvest-output rows to the FRESH `supply_offer` contract, plus a synthetic
  BC mini-network upstream fixture and generated comparison artifact.
- Added a generated ws3 export manifest with row counts and SHA-256 hashes for
  the normalized upstream table, adapter output, and downstream LP input.
- Added a real ws3 smoke-run bridge using the local editable `../ws3` clone,
  ws3's TSA24 clipped Woodstock example, and the existing `supply_offer`
  exporter.
- Added an explicit diagnostic `ws3_supply_crosswalk.csv` fixture and
  validation path for mapping ws3 smoke-run development types to FRESH spatial
  units and supply nodes.
- Added a repeatable `bc-mini-ws3-smoke` LP variant that swaps in real ws3
  smoke supply while preserving the stable synthetic BC mini fixture.
- Added generated comparison tables for objective, cost, market quantity, and
  pathway-flow deltas between the synthetic BC mini solve and ws3-smoke variant.
- Added a ws3 diagnostic supply replacement decision layer selecting SCANFI v2
  2025 subset as the first inventory candidate, keeping NFI standard reports as
  validation-only pending license review, and marking yield policy unresolved.
- Added a generated SCANFI v2 2025 diagnostic subset manifest fixture without
  downloading raw rasters, including an explicit missing-source flag for the
  north extrapolation mask.
- Added a tiny SCANFI-derived diagnostic inventory-cell fixture that maps
  synthetic proxy cells to BC mini supply nodes while preserving verified
  SCANFI layer provenance and explicitly marking values as unsampled proxies.
- Added a SCANFI diagnostic supply generator, synthetic placeholder yield-rule
  table, ws3-shaped harvest-output artifact, and solved BC mini SCANFI
  diagnostic LP variant.
- Added a three-way BC mini variant comparison surface covering synthetic,
  ws3-smoke, and SCANFI-diagnostic supply paths, with objective, supply,
  solution-summary, pathway-flow, and decision-memo outputs.

## 2026-06-25

- Added a dedicated FFCM archive reverse-engineering roadmap phase, planning
  brief, and lightweight metadata indexes for the transferred local archive:
  full file manifest, directory summary, curated document index, FCM evidence
  queue, and architecture crosswalk.
- Added a repeatable FCM configuration parser and generated normalized metadata
  tables for FMU supply, facilities, processing lines/flows, routes,
  transporters, commodities, markets, imports, and transload costs.
- Added a repeatable LP forensics summarizer and generated first-pass summaries
  for small report LP examples plus large basecase log and secondary
  log/residue allocation models.
- Added a repeatable FCM runtime dependency inventory and flagged CPLEX,
  lp_solve, database drivers, and GIS/visualization libraries as implementation
  evidence rather than FRESH runtime dependencies.
- Added an implementation-boundary matrix separating FFCM schemas and
  formulation evidence to preserve/adapt from engines, solvers, visualization
  runtime, database drivers, and license-sensitive workbooks to replace or
  avoid.
- Added an initial FRESH replacement architecture note, ws3-to-LP soft-link
  interface contract, and data-source disposition table for FFCM-derived
  prototype seeds versus production rebuild requirements.
- Scoped the first feasibility prototype as a BC diagnostic mini-network using
  synthetic or clearly labelled diagnostic ws3-shaped supply records feeding
  the downstream LP model.
- Downloaded the likely surviving gist notebook seed for the ws3-compatible
  network-flow model, extracted notebook code text/markdown/cell metadata, and
  documented its relationship to CIRRELT-2018-24 section 2.1.
- Added a rebuild plan, function inventory, and schema crosswalk for refactoring
  the recovered notebook's five-layer network-flow model into a clean FRESH
  module.
- Added a synthetic BC mini-network fixture and repeatable solver-independent
  graph builder that emits typed `src`, `dsp`, `cdt`, `prc`, and `snk` node/arc
  tables for the first FRESH soft-link prototype.
- Added a PuLP/CBC BC mini-network LP prototype, generated variable,
  constraint, objective, `solution_flow`, and `solution_summary` tables, and a
  repeatable validation script for bounds/pathway checks.
- Promoted the BC mini-network graph, LP solve, and validation logic into a
  reusable `fresh_fibre` Python package while preserving the archive-analysis
  script entrypoints as compatibility CLIs.
- Added typed `fresh_fibre.schema` adapters for core contract rows and rewired
  graph, LP, and validation modules to share CSV I/O, numeric parsing, and
  row-level model objects without changing generated output tables.
- Added `fresh_fibre.units` validation for canonical commodity units,
  cost/price unit bases, generated graph arc units, and technology conversion
  coefficient dimensions.
- Added lightweight unit and conversion registries covering current prototype
  units plus candidate `bdmt`, `t`, `kg`, `MWh`, and product-specific board/area
  units.
- Reworked the BC mini-network LP reporting path so `solution_flow` and
  `solution_summary` derive run, scenario, period, region, solver, and
  provenance metadata from fixture input tables instead of hard-coded values.
- Standardized generated CSV writes on LF line endings through the shared
  schema writer.
- Added `fresh_fibre.contract` validation against
  `fresh-interface-contract.csv`, including a generated BC mini-network
  contract-coverage audit table.
- Added stdlib regression tests for the BC mini-network graph, LP solve,
  contract coverage, unit registry, and negative validation cases.
- Added the BC mini-network decision memo and gap register, recommending an
  independent FRESH implementation informed by selected FFCM schemas and
  formulation concepts rather than a port of the FCM runtime.
- Completed first-pass National Road Network triage: inventoried all 13
  province/territory GeoPackage ZIP packages and all 13 shapefile ZIP packages,
  recorded HTTP package sizes and release validity/version notes, accepted NRN
  as a coarse national haul-distance baseline, and flagged jurisdiction-specific
  forest/resource road layers as mandatory follow-up before operational access
  modelling.

## 2026-06-24

- Bootstrapped the CLEWs-C2070 FRESH parent repository with operating notes,
  README, roadmap, data repository runbook, Tyler onboarding brief, and initial
  metadata inventory templates.
- Created and linked the `UBC-FRESH/clews-c2070-fresh-data` DataLad repository
  as the parent repo's `data` submodule, configured the `arbutus-s3` git-annex
  special remote, uploaded a tiny annexed smoke artifact, and verified retrieval
  from a clean clone.
- Added the first national source-discovery triage queue, verified Open
  Government Licence/resource metadata for the highest-priority national layers
  where available, and opened GitHub issue `#1` to govern P1.2 source triage.
- Added `pre_canlad_v2` as a high-priority disturbance candidate after the user
  flagged the recent CanLaD v2 release, and updated the CanLaD triage notes to
  distinguish the 1985-2024 `v1.1` package from the separate pre-1985 `v2`
  companion dataset.
- Completed first-pass SCANFI v2 source triage: inventoried the 420-file
  public directory, recorded the approximate 312 GB package size, generated a
  lightweight file manifest, and recommended a subset-first DataLad mirroring
  strategy.
- Completed first-pass NFI standard reports triage: parsed public report links,
  recorded 270 CSV summary tables across four reporting geographies, and flagged
  the source for license review because NFI notices do not use Open Government
  Licence language.
- Completed first-pass CanLaD and Pre-CanLaD v2 triage: flagged CanLaD for
  access resolution because README-named raster payloads were not publicly
  reachable, and accepted Pre-CanLaD v2 as a manageable companion disturbance
  dataset pending PI approval to mirror.