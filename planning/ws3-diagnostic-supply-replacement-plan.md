# ws3 Diagnostic Supply Replacement Plan

## Purpose

This note defines the next supply-side replacement step after the ws3 smoke
bridge. The goal is to replace the current ws3 TSA24 example producer with a
first accepted diagnostic inventory path, without downloading large payloads or
changing downstream LP mechanics in this tranche.

## Decision

Use the reviewed SCANFI v2 2025 subset as the first accepted diagnostic
inventory candidate for replacing the ws3 smoke/example supply path.

Supporting stance:

- Treat SCANFI v2 as the first spatial inventory source because it is already
  reviewed, Open Government Licence - Canada, and marked
  `accepted_subset_recommended`.
- Use NFI standard reports only as validation/reference summaries until
  license and re-hosting status are resolved.
- Use the Gauthier et al. (2015) merchantable-volume method as the primary
  yield-policy reproduction path. RSYC remains a strong secondary biomass
  candidate; MAGPlot and provincial yield curves remain later candidates.
- Keep the current ws3 TSA24 smoke path as a runtime bridge test and diagnostic
  baseline to retire once an accepted diagnostic inventory/yield fixture exists.

## Replacement Stages

1. **Current state:** ws3 TSA24 smoke model emits real ws3 harvest-output rows
   through `ws3_supply_crosswalk.csv`; quantities and costs remain diagnostic.
2. **Current inventory-metadata tranche:** create a tiny SCANFI-derived
   diagnostic inventory manifest and inventory-cell fixture for the selected
   2025 subset. Do not commit raw rasters. Keep quantity generation synthetic
   or rule-based until yield policy is chosen.
3. **Current supply-generation tranche:** convert the diagnostic inventory
   cells into an explicit ws3-style harvest-output and `supply_offer` bridge
   fixture using documented placeholder yield rules.
4. **Current yield policy tranche:** reproduce the Gauthier et al. (2015)
   method as a lightweight diagnostic fixture and identify missing accepted
   inputs before replacing placeholder yield rules.
5. **First accepted ws3 supply fixture:** replace the TSA24 example producer
   with a ws3 fixture backed by accepted inventory provenance, explicit
   spatial/supply-node crosswalk rules, and documented yield assumptions.

## Acceptance Criteria

The first accepted diagnostic ws3 supply input is ready when:

- Input inventory provenance is tied to reviewed source metadata, with no raw
  payloads committed to the parent repository.
- Spatial/supply-node crosswalk rows are explicit, validated, and no longer
  labelled `diagnostic` without an accepted-source rationale.
- Quantity generation has a documented yield policy, even if deliberately
  simple for the first diagnostic region.
- NFI standard reports are used only for QA summaries unless license review
  clears report snapshot archiving.
- The generated supply can pass through the existing `supply_offer` exporter,
  downstream LP solve, and comparison workflow.

## Completed Inventory-Metadata Tranche

Build a tiny SCANFI-derived diagnostic inventory fixture from the selected
manifest rows in
`metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic/`. The current
manifest verifies the five 2025 raster rows, English README, and English update
report against `metadata/source-file-manifests/scanfi_v2_files.csv`. The north
extrapolation mask is recorded as `missing_from_source_manifest` because it was
recommended during source review but is absent from the current local and live
public directory listings.

The generated `diagnostic_inventory_cells.csv` fixture maps a small set of
synthetic BC diagnostic inventory cells to the current BC mini spatial and
supply nodes. Its values are not sampled from SCANFI rasters; they are
deliberately labelled as synthetic proxies and carry verified SCANFI source-file
references for biomass, height, closure, land-cover, and age fields.

## Completed Supply-Generation Tranche

Convert the diagnostic inventory cells into a first explicit supply-generation
fixture. `diagnostic_yield_rules.csv` defines synthetic placeholder yields by
diagnostic region, and `scanfi_diagnostic_harvest_output.csv` aggregates
inventory-cell contributions at the supply-node/commodity boundary so the rows
can pass through the existing ws3 export and LP graph machinery.

The generated `bc-mini-scanfi-diagnostic` variant replaces the base
`supply_offer.csv` table with SCANFI-provenance diagnostic supply, rebuilds the
graph, solves the downstream LP, and writes a variant summary. The result is
still diagnostic: quantities and costs remain placeholder assumptions until a
MAGPlot/provincial-yield policy is accepted.

## Completed Comparison Tranche

Compare the SCANFI diagnostic variant against the synthetic BC mini and ws3
smoke variants. The comparison surface writes a variant run matrix, objective
comparison, supply comparison, solution-summary comparison, pathway-flow
comparison, and short decision memo under
`metadata/ffcm-archive/prototype-comparisons/bc-mini-three-way/`.

The comparison separates inventory provenance progress from yield assumption
quality so the model does not overclaim calibration.

## Current Yield-Policy Tranche

The active yield-policy path is documented in
`planning/ws3-yield-policy-decision-gate.md`. The Gauthier diagnostic fixture
under `metadata/ffcm-archive/prototype-inputs/gauthier-diagnostic/` encodes the
published coniferous and deciduous merchantable-volume coefficients, runs a
synthetic method-reproduction spike, and emits candidate yield-rule rows marked
`candidate_not_accepted`.

The fixture is a method test, not an accepted yield source. It should not
replace `diagnostic_yield_rules.csv` until reference-pixel access, raster input
selection, species aggregation, climate predictors, and validation policy are
resolved.

## Next Implementation Tranche

Use the Gauthier input gap matrix to prepare a real-data acquisition and
substitution decision: NFI reference pixels versus SCANFI-derived proxies,
climate predictor source, conifer/deciduous aggregation, and ws3 yield-table
versus static supply-proxy output semantics.

Keep large rasters in the data submodule or scratch space, not in the parent
repository.
