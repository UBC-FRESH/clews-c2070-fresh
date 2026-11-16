# Change Log

## 2026-11-16

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
  `metadata/national-coverage.csv`. Runtime 431 s.

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