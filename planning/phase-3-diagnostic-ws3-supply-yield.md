# Phase 3 Diagnostic ws3 Supply and Yield Replacement

## Purpose

Phase 3 turns the Phase 2 BC mini-network feasibility prototype into a
diagnostic supply-replacement lane governed by explicit provenance. The phase
keeps the downstream LP mechanics stable while progressively replacing
synthetic supply inputs with ws3-shaped outputs, SCANFI inventory provenance,
and a documented Gauthier et al. (2015) yield-method reproduction path.

## Data Boundary

Tracked outputs are limited to code, planning notes, lightweight CSV metadata,
synthetic diagnostic fixtures, and compact generated audit tables. Raw SCANFI
rasters, NFI reference pixels, private correspondence, RSYC payloads, and any
large model outputs remain under ignored `tmp/` or the `data` submodule.

## Phase Close-Out Criteria

- The active branch and GitHub issue hierarchy match the roadmap.
- The ws3 bridge, SCANFI diagnostic inventory/supply path, three-way comparison,
  and Gauthier yield-method fixture are documented and tested.
- Placeholder yield rules are clearly marked as diagnostic and not accepted.
- All child issue checklists are complete or explicitly deferred.
- A pull request from the phase branch to `main` is opened after child issues
  are complete.
