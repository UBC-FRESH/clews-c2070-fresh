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
- Keep yield generation unresolved for now. MAGPlot and provincial yield curves
  remain candidate sources for the first defensible growth/yield policy.
- Keep the current ws3 TSA24 smoke path as a runtime bridge test and diagnostic
  baseline to retire once an accepted diagnostic inventory/yield fixture exists.

## Replacement Stages

1. **Current state:** ws3 TSA24 smoke model emits real ws3 harvest-output rows
   through `ws3_supply_crosswalk.csv`; quantities and costs remain diagnostic.
2. **Next implementation tranche:** create a tiny SCANFI-derived diagnostic
   inventory manifest or fixture for one small analysis window. Do not commit
   raw rasters. Keep quantity generation synthetic or rule-based until yield
   policy is chosen.
3. **Yield policy tranche:** select a first growth/yield source or documented
   placeholder policy from MAGPlot, provincial yield curves, or another
   reviewed source.
4. **First accepted ws3 supply fixture:** replace the TSA24 example producer
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

## Next Implementation Tranche

Build a tiny SCANFI-derived diagnostic inventory manifest or fixture that
selects the minimum 2025 subset needed for a first ws3 supply prototype:
biomass, height, closure, land cover, age, north extrapolation mask, README, and
update report. Keep large rasters in the data submodule or scratch space, not
in the parent repository.
