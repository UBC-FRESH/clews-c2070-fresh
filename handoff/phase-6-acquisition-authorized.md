# Handoff Prompt: Authorized SCANFI Acquisition And Phase 6 Resume

You are continuing work in `/home/gep/projects/clews-c2070-fresh`.

The user has now explicitly authorized acquisition of the five approved SCANFI
v2 2025 rasters needed for Phase 6. This authorization covers downloading the
public source files, adding them to the `data` DataLad/git-annex repository,
publishing the annex content through the configured `arbutus-s3` special
remote, and then resuming the tiny real-sampled diagnostic lane.

Do not download the full 312 GB SCANFI package. Acquire only these five files:

- `SCANFI_att_biomass_2025_v2_20260119.tif`
- `SCANFI_att_height_2025_v2_20260119.tif`
- `SCANFI_att_closure_2025_v2_20260119.tif`
- `SCANFI_att_nfiLandcover_2025_v2_20260119.tif`
- `SCANFI_age_median_2025_v2_20260119.tif`

Official source directory:

`https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/`

The five files are approximately 21 GB total. Expected target paths in the
data repository are:

`data/raw/scanfi/v2/<filename>`

## Read Before Acting

Read these files first:

1. `AGENTS.md` and the repository instructions.
2. `docs/data-repository-runbook.md` in the parent repository.
3. `metadata/source-reviews/scanfi_v2.md`.
4. `metadata/source-file-manifests/scanfi_v2_files.csv`.
5. `planning/phase-5-decision-memo.md`.
6. `metadata/ffcm-archive/phase-5-phase-6-authorization.csv`.
7. `metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv`.
8. `metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv`.

Before starting:

- Run `gh auth status`.
- Run `git status --short` in the parent repository.
- Run `git -C data status --short`.
- Confirm the repository-root `.venv` exists and prefer explicit
  `.venv/bin/datalad`, `.venv/bin/python`, and `.venv/bin/freshforge` paths.
- Confirm `git annex version` is available.
- Confirm the `data` remote configuration and `arbutus-s3` state without
  printing credentials, bucket secrets, or private configuration.

## Acquisition Procedure

Use the runbook's DataLad/git-annex pattern. Keep temporary downloads outside
the parent repository or in an ignored temporary workspace. Do not download
directly into a tracked parent-repository path.

For each of the five files:

1. Verify the official URL and expected size against
   `metadata/source-file-manifests/scanfi_v2_files.csv`.
2. Download with a resumable method to a temporary acquisition path.
3. Record the URL, filename, acquisition timestamp, byte size, and SHA-256
   checksum in an acquisition manifest.
4. Add or save the payload at `data/raw/scanfi/v2/<filename>` using DataLad so
   the payload is annexed rather than committed as an ordinary Git blob.
5. Verify the annex key/content and that the working tree contains no
   accidental non-annex copy in the parent repository.

Use the exact commands supported by the installed DataLad/git-annex versions.
Do not invent an S3 bucket name or change the existing special-remote
configuration. If `arbutus-s3` is not enabled or credentials are unavailable,
stop after local annex ingestion, report the precise blocker, and do not put
credentials into tracked files or issue comments.

After all five files are annexed:

1. Create/update an acquisition manifest under the data repository's
   `manifests/` or `metadata/` directory, following the runbook's layout.
2. Commit the data-repository metadata and annex pointer changes.
3. Publish annex content to `arbutus-s3` with the runbook-approved workflow.
4. Push the data repository's Git metadata and annex branch as appropriate.
5. Verify availability from the configured special remote with `git annex
   whereis` or the equivalent non-secret audit command.
6. Update the parent submodule pointer only after the data repository commit is
   complete and pushed.

Do not commit the raster payloads, temporary downloads, caches, credentials,
or machine-specific paths to the parent repository.

## Resume Phase 6

Once all five source files are available at the approved DataLad paths:

1. Run the relevant FreshForge validate/plan workflow before materialization.
2. Extract only the six planned fixture-cell windows for the five approved
   layers under:
   `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`.
3. Write manifests under:
   `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`.
4. Apply the tiny real-sampling validation contract, including nodata,
   nonnegative values, cell-area accounting, expected ranges, CRS/resolution,
   provenance, and checksums.
5. Commit only the approved public-safe derived inventory table at:
   `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv`.
6. Continue P6.3 through P6.5: generate the ws3-shaped supply output, solve
   the LP variant, produce comparisons, and write the validation memo.

Preserve all existing synthetic, ws3-smoke, SCANFI-diagnostic, and
SCANFI+Gauthier variants. Label the new lane as real-sampled but diagnostic.
Do not introduce production residue yields, calibrated costs, climate
predictors, private NFI inputs, production benchmarks, or national scaling.

## Planning And GitHub Updates

After successful acquisition and verification:

- Update the SCANFI source status and acquisition notes in the parent metadata.
- Update the Phase 6 progress in `ROADMAP.md`, the relevant planning note, and
  `CHANGE_LOG.md`.
- Add a progress comment to the governing source-triage issue `#1` describing
  what was acquired, where it lives, checksums/manifests, and any remaining
  gates. Never include credentials or private paths.
- Keep the Phase 6 issue state consistent with the repository workflow. Phase 6
  currently has no active GitHub parent issue; do not invent issue numbers.

## Stop Conditions

Stop and report instead of guessing if:

- a public URL no longer returns the expected file;
- the downloaded byte size or checksum does not match the acquisition record;
- DataLad would store a payload as a normal Git blob;
- `arbutus-s3` needs credentials or configuration that are not available;
- the workflow would materialize files outside the five approved source files,
  six fixture cells, and authorized outputs; or
- unrelated user changes are present in the parent or data repository.

At completion, report the data-repository commit, parent submodule commit,
manifest paths, verification commands, remote availability status, and the
remaining diagnostic/production gates. Leave both repositories clean except
for intentional, clearly reported changes.
