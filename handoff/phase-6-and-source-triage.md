# Handoff Prompt: FRESH Phase 6 And Source Triage

Copy the prompt below into a fresh Codex session opened at the repository
root. Treat this document as an operational handoff, not as an authorization
to download broad datasets or change external infrastructure.

---

You are continuing work in `/home/gep/projects/clews-c2070-fresh`, the UBC
FRESH workspace for the forestry and biomass supply component of CLEWs-C2070.

## Current Repository State

- Current branch: `feature/p7-source-triage-and-production-readiness`.
- Current committed base: `717a969`; the branch currently has preserved
  uncommitted Phase 6/Phase 7 work and is intentionally not clean.
- Phase 7 parent issue `#26` is active; recovery task `#27` is attached under
  it, and source triage remains governed by issue `#1`.
- P6.0–P6.5 are complete for the diagnostic lane. The six heuristic SCANFI
  windows are quarantined as `SOFTWARE_FIXTURE_NOT_EVIDENCE` and must not be
  used as inferential evidence.
- G0/G1 of `planning/scientific-model-recovery-plan.md` were approved on
  2026-08-08 and completed. G2–G8 remain unapproved; no new heavy computation
  is authorized without a later explicit gate decision.
- The repository uses a root-local `.venv` for Python, DataLad, FreshForge, and
  tests. Prefer explicit `.venv/bin/...` commands.
- The repository uses a root-local `.venv` for Python, DataLad, FreshForge, and
  tests. Prefer explicit `.venv/bin/...` commands.
- At handoff, the `data` submodule is a fresh scaffold containing only its
  metadata/manifests documentation and annex configuration; the selected
  SCANFI source rasters have not yet been acquired into `data/raw/scanfi/v2/`.
- Heavy raw, intermediate, archived, and generated payloads belong in the
  `data` DataLad/git-annex submodule, not in the parent repository.
- Credentials, Arbutus/S3 profiles, bucket names, private paths, and machine
  configuration must remain outside tracked files and issue comments.

## Immediate Objective

Continue the next roadmap gate: **Phase 6, Tiny Real-Sampled Diagnostic Supply
Lane**. The goal is to materialize and use only the tiny approved SCANFI-derived
fixture for the existing BC diagnostic lane. Preserve all existing synthetic,
ws3-smoke, SCANFI-diagnostic, and SCANFI+Gauthier variants.

Do not claim production readiness. The Phase 6 lane remains diagnostic and must
retain explicit labels for unresolved residue yield, cost calibration, climate
predictors, NFI access, validation benchmarks, and national scaling.

## Read These Files First

1. `AGENTS.md` and the repository instructions supplied by the user.
2. `ROADMAP.md`, especially Phase 6 and Phase 7.
3. `planning/phase-5-decision-memo.md`.
4. `metadata/ffcm-archive/phase-5-phase-6-authorization.csv`.
5. `planning/phase-5-tiny-real-sampling-fixture.md`.
6. `metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv`.
7. `metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv`.
8. `metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv`.
9. `planning/source-triage-queue.md` and `metadata/source-inventory.csv`.
10. `docs/data-repository-runbook.md` and the relevant FreshForge workflow
    examples under `examples/freshforge/`.

Before starting a task, run `gh auth status`, inspect the relevant issue/PR
state, and check `git status --short`. Use the existing roadmap and issue
workflow; do not invent a new phase structure.

## Phase 6 Authorization Boundary

The Phase 5 decision authorizes only the following:

- Selected SCANFI v2 2025 source references under
  `data/raw/scanfi/v2/`, only as needed for the tiny fixture.
- Tiny raster windows under
  `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`.
- Tiny manifests under
  `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`.
- One public-safe parent-repository derived inventory table at
  `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv`.

The selected SCANFI source layers are the 2025 v2 biomass, height, closure,
NFI-compatible landcover, and median-age rasters listed in the extraction
contract. Use the six planned fixture cells and the existing BC mini spatial
unit/supply-node mappings. Preserve source filenames, checksums, CRS,
resolution, nodata handling, sampling method, cell IDs, and value-policy labels
in the manifests.

Do not materialize broad `data/raw`, all SCANFI years, species layers, the
north extrapolation mask unless separately authorized, private NFI plots or
reference pixels, climate payloads, or production benchmark data. Do not put
raw rasters, raster windows, large model outputs, or annex payloads in the
parent repository.

### Acquisition Prerequisite

Phase 5 authorization means that these source rasters are permitted inputs for
the tiny fixture; it does not mean that their annexed content already exists or
that a large CDN download is automatically authorized. Before running P6.1,
check each expected file under `data/raw/scanfi/v2/` and confirm whether the
corresponding annex key/content is available. The current expected five-file
set is:

- `SCANFI_att_biomass_2025_v2_20260119.tif`
- `SCANFI_att_height_2025_v2_20260119.tif`
- `SCANFI_att_closure_2025_v2_20260119.tif`
- `SCANFI_att_nfiLandcover_2025_v2_20260119.tif`
- `SCANFI_age_median_2025_v2_20260119.tif`

The five rasters are approximately 21 GB in total according to the current
planning estimate; verify current sizes from
`metadata/source-file-manifests/scanfi_v2_files.csv` before any acquisition.
The north extrapolation mask is a documented follow-up source and is not part
of this five-file Phase 6 extraction set.

The existing
`examples/freshforge/scanfi_diagnostic_materialization_workflow.yaml` can
initialize the submodule, enable the special remote, and materialize paths
that are already represented in the DataLad repository. It is not a CDN
acquisition workflow and cannot retrieve source files that have never been
annexed.

Use this decision tree:

1. If all five source files are present and retrievable, continue with P6.1
   and use the FreshForge/DataLad workflow selectively.
2. If any source file is absent, do not run `datalad get` expecting it to
   create an unregistered dataset item, do not download the five rasters into
   the parent repository, and do not silently begin a roughly 21 GB external
   acquisition.
3. When files are absent, record the blocker and prepare an acquisition plan
   containing the official URLs, exact filenames, expected sizes, license,
   acquisition date/command, checksum plan, target DataLad paths, and required
   `arbutus-s3` publication steps. Then continue with source triage and any
   Phase 6 code/contracts that do not require raster content.
4. Proceed with actual CDN download, DataLad ingestion, annex publication, or
   other stateful data-infrastructure changes only after the user explicitly
   authorizes that acquisition and the runbook prerequisites are satisfied.
   Do not ask the user the same question repeatedly after documenting the
   blocker; leave a precise handoff for the authorized acquisition step.

## Phase 6 Work Sequence

Implement and verify the roadmap tasks in order:

1. **P6.0 Resolve source availability, then P6.1 materialize the tiny
   fixture.** First apply the acquisition prerequisite above. If the source
   files are available, use the repo-root `.venv`, the FreshForge/DataLad
   workflow pattern, and selective retrieval. Resolve any
   missing submodule or special-remote state by reading the runbook first.
   Do not issue raw DataLad, git-annex, or S3 infrastructure commands without
   understanding the runbook and current repository state.
2. **P6.2 Generate the derived inventory table.** Sample only the approved
   windows, apply the extraction and validation contracts, and write the
   public-safe derived table in the parent repository. Keep sampled values
   distinguishable from synthetic proxy values.
3. **P6.3 Build the ws3-shaped supply output.** Reuse the existing adapters and
   Gauthier diagnostic log-yield rules. Do not silently introduce residue
   yields, calibrated costs, or climate assumptions.
4. **P6.4 Solve the LP variant.** Emit the existing contract artifacts:
   `ws3_harvest_output`, `supply_offer`, `solution_flow`,
   `solution_summary`, and comparison tables. Preserve the stable variants and
   make the new lane's provenance explicit.
5. **P6.5 Write a validation memo.** State what was sampled, what checks
   passed, what remains diagnostic, and which production gates remain open.

Scale tests and documentation to the changed surface. At minimum, run the
focused regression tests around extraction, validation, ws3 export, LP solving,
and provenance; run CSV/YAML parsing checks, `git diff --check`, and the
FreshForge validate/plan commands relevant to the workflow.

## Parallel Source-Triage Work

GitHub issue `#1` remains open. The current active source-triage target is
`biomass_inventory_business`. The newly flagged candidate is
`databasin_canada_forest_tenures`:

- Gallery: `https://databasin.org/galleries/1dd5bbe4e47a4855a2095eae7cf3253a/`
- Likely member dataset to verify:
  `https://databasin.org/datasets/2f20af8c4f7949ad8ab631b77dc733c3/`

For the Data Basin candidate, resolve the actual member dataset URL/download
endpoint, source lineage, spatial unit semantics, license, update status, and
fit to the FRESH FMU/supply-node boundary contract. Keep it a candidate until
those checks pass. Do not archive or materialize it merely because the gallery
exists. Accepted payloads must follow the DataLad data-repository pattern.

Update `metadata/source-inventory.csv` first, then the relevant source review,
planning queue, `ROADMAP.md`, `CHANGE_LOG.md`, and GitHub issue comments as the
triage status changes. Promote a source into
`metadata/required-datasets.yaml` only after publisher, access, license,
coverage, provenance, and modelling use have been reviewed.

## GitHub And Planning Discipline

- Phase 3 is complete: PR `#9` merged and parent issue `#2` is closed.
- Phase 4 is complete: parent issue `#12` and child issues `#13` through `#17`
  are closed.
- Phase 5 is complete: PR `#25` merged, parent issue `#19` and child issues
  `#20` through `#24` are closed.
- Phase 6 is planned but has no active parent issue yet. Do not create a Phase
  6 issue hierarchy unless the user or project workflow explicitly authorizes
  starting that phase. If the phase is formally started, create one parent
  issue and child issues matching the roadmap, then backfill the issue numbers
  into the planning surfaces.
- Issue `#1` remains the governing source-triage issue and should remain open
  until the source-discovery work is actually complete.
- Keep `ROADMAP.md`, planning notes, `CHANGE_LOG.md`, issue comments, PR text,
  and commits synchronized whenever task status changes materially.

## Data And Safety Stop Conditions

Stop and report the blocker instead of guessing when:

- a source's license or redistribution terms are unclear;
- a DataLad special remote, bucket, credential, or access profile is missing;
- a requested payload exceeds the approved tiny Phase 6 scope;
- a result would require private NFI data or unapproved climate/validation
  inputs;
- a proposed modelling assumption would turn a diagnostic lane into an implied
  production claim; or
- the current worktree contains unrelated user changes that would be at risk.

When finished, leave the worktree clean if changes were requested, report the
commit and verification commands, identify any remaining open gates, and state
which payloads were deliberately not materialized.

---

End of handoff prompt.
