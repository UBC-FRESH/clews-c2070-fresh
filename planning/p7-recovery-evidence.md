# Phase 7 Recovery Evidence

**Date**: 2026-08-08  
**Task**: GitHub issue #27  
**Parent**: GitHub issue #26  
**Branch**: `feature/p7-source-triage-and-production-readiness`  
**Committed base**: `717a969` (same commit as local `main` and `origin/main`)

This note classifies the preserved dirty state after the code-server reset. It
is an evidence map, not an authorization to delete, clean, reset, or rerun
heavy computation.

## Phase 6 diagnostic carryover

- `data` submodule pointer and its materialized SCANFI windows/manifests.
- `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/`
  inventory, supply, provenance, export, and generated LP artifacts.
- `fresh_fibre/scanfi_supply.py` and
  `tests/test_bc_mini_prototype.py` changes supporting the diagnostic lane.
- `planning/phase-6-validation-memo.md` and the Phase 6 portion of
  `ROADMAP.md`.
- `requirements-prototype.txt` changes used by the preserved prototype lane.
- `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled-lp-run/`,
  `bc-mini-scanfi-real-sampled-lp-test/`, and
  `bc-mini-scanfi-real-sampled-variant/` generated diagnostic runs.

These artifacts are retained as software integration evidence. The six
heuristic SCANFI windows are not inferential evidence. Their labels are
recorded in `planning/scientific-model-recovery-plan.md` and the manifests.

## Phase 7 source triage

- `metadata/source-inventory.csv`
- `metadata/required-datasets.yaml`
- Existing source reviews under `metadata/source-reviews/`, including the
  Alberta FMU and NBAC modifications and the newly added reviews.
- The source-triage context in `ROADMAP.md` and
  `planning/source-triage-queue.md`.

The active governance is Phase 7 parent issue #26 and source-triage issue #1.
`biomass_inventory_business` was locally reviewed as unsuitable for forest
biomass; it is not treated as an accepted source.

## Scientific recovery and validation planning

- `planning/scientific-model-recovery-plan.md`: G0 approved and G1 completed
  on 2026-08-08; G2-G8 remain unapproved.
- `planning/advisor_dossier.md`: continuity record for the recovery reasoning.
- `scripts/render_validation_map.py` and
  `scripts/repair_scanfi_source_crs.py`: preparatory validation utilities.
- `docs/validation-maps.md`: validation-map documentation.
- `ROADMAP.md`, `CHANGE_LOG.md`, and
  `handoff/phase-6-and-source-triage.md`: synchronized recovery surfaces.

These items do not authorize new raster extraction, LP solving, uncertainty
fitting, or production claims.

## Validation evidence

- Parent inventory: 6 rows; all `value_policy=quarantined` and
  `sampling_status=quarantined`.
- Six fixture manifests: 30 rows; all
  `value_policy_status_labels=SOFTWARE_FIXTURE_NOT_EVIDENCE`.
- Parent and `data` submodule `git diff --check`: passed.
- Focused regression `tests/test_bc_mini_prototype.py`: 21 passed.

## Remaining boundary

The next implementation task is Phase 7 source triage under issue #1, but no
new child task should begin until this recovery task's evidence and planning
surfaces are accepted. G2-G8 remain closed pending separate developer approval.
