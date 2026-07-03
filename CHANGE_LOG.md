# Change Log

## 2026-07-03

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
