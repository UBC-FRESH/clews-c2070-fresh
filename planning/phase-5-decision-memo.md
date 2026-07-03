# Phase 5 Decision Memo

## Decision

Phase 5 authorizes Phase 6 to implement the tiny real-sampled diagnostic lane
only within the public-safe boundaries defined by the Phase 5 contracts. This
authorization is narrow: Phase 6 may materialize the selected tiny SCANFI
diagnostic fixture paths and may commit only the approved parent-repo derived
metadata outputs.

This decision does not authorize broad raw-data checkout, private or restricted
NFI payloads, production residue or cost assumptions, climate predictor
selection, production validation benchmarks, or national scaling.

## Authorized For Phase 6

- Reference the selected SCANFI v2 2025 source rasters under
  `data/raw/scanfi/v2/` only as needed to produce the tiny fixture.
- Materialize tiny raster-window outputs under
  `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`.
- Materialize tiny fixture manifests under
  `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`.
- Commit the public-safe derived diagnostic inventory table at
  `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv`
  if it satisfies the Phase 5 validation contract.

## Not Authorized

- Broad `data/raw` materialization outside the selected SCANFI source raster
  references.
- Private or restricted NFI reference pixels, plots, or report snapshots.
- Production residue-yield policy, cost calibration, climate predictor policy,
  validation benchmark acceptance, or national scaling.
- Parent-repo storage of raw rasters, raster windows, large model outputs, or
  DataLad/git-annex payloads.

## Evidence

- `metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv`
- `metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv`
- `metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv`
- `metadata/ffcm-archive/conceptual-alpha-readiness.csv`
- `metadata/ffcm-archive/phase-5-phase-6-authorization.csv`

## Closeout

Phase 5 is complete when the authorization table, roadmap, changelog, issue
checklists, PR body, and verification results are reconciled. The Phase 5
parent issue remains open until PR `#25` merges.
