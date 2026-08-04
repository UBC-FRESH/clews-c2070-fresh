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
- The ws3 bridge, SCANFI diagnostic inventory/supply path, variant comparison,
  Gauthier yield-method fixture, and SCANFI+Gauthier diagnostic variant are
  documented and tested.
- Placeholder yield rules are clearly marked as diagnostic; log-yield
  placeholders are replaced only by Gauthier diagnostic method-reproduction
  values, while residue yield remains unresolved.
- All child issue checklists are complete or explicitly deferred.
- A pull request from the phase branch to `main` is opened after child issues
  are complete.

## Active Extension

P3.6 completed the first closeout pass and opened PR `#9`. P3.7 extended the
same phase to replace the SCANFI diagnostic log-yield placeholder with
Gauthier et al. (2015) diagnostic method-reproduction rules while keeping raw
rasters, NFI pixels, and production calibration out of scope. PR `#9` merged
and parent issue `#2` is closed; the phase is complete.
