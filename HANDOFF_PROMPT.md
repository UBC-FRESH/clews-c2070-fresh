# Handoff Prompt — CanLaBS Signal Integration

Continue the model-development work in `/home/gep/projects/clews-c2070-fresh`.

## User intent

The goal is to build an actual forestry/biomass model, not substitute governance or process language for implementation. Make concrete, testable engineering progress. The repository was cleaned and published on 2026-08-08; do not recreate generated-output spillover or claim completion without directly verifying files and command results. Do not invent scientific mappings, thresholds, classes, multipliers, calibration, or production readiness.

## Repository and runtime

- Repository: `/home/gep/projects/clews-c2070-fresh`
- Use the repository virtual environment: `.venv/bin/python`
- Correct regression command: `.venv/bin/python -m pytest tests/ -q`
- Do **not** run bare `pytest -q`; it recursively collects unrelated `.venv` tests.
- Run `git diff --check` for the parent repository and, when relevant, `git -C data diff --check`.
- Keep heavy raster payloads outside tracked parent-repository paths.

## Repository state at handoff

- Parent branch: `feature/p7-source-triage-and-production-readiness`
- Parent HEAD: `5d2e8c4` (`Add CanLaBS signal and clean diagnostic outputs`)
- Parent branch is pushed and tracks
   `origin/feature/p7-source-triage-and-production-readiness`.
- The parent worktree is clean and synchronized with its upstream.
- The `data` submodule is on `main` at `87087b2`, clean, and synchronized with
   `origin/main`.
- No pull request has been opened or merged.
- Reproducible LP/variant outputs are ignored under the narrowly scoped rules
   in `.gitignore`; do not commit generated CSVs or run directories from the
   real-sampled diagnostic lane.

## Completed context

The repository already contains:

- A working inventory → harvest → supply-offer → graph → LP path in `fresh_fibre/`.
- SCANFI diagnostic and SCANFI+Gauthier diagnostic variants with passing regression coverage.
- A fresh, locally verified CanLaBS v2 primary severity raster:
  `tmp/canlabs_v2_fresh/CanLaBS_1985_2024_v20260121_fresh.tif`
- CanLaBS transfer evidence in:
  - `tmp/canlabs_v2_fresh/transfer-manifest.json`
  - `metadata/source-reviews/canlabs_v2.md`
  - `metadata/required-datasets.yaml`
  - `metadata/source-inventory.csv`

The CanLaBS raster facts are:

- 1985–2024, 30 m, EPSG:3978, one int16 band, BigTIFF, Deflate compression.
- Nodata is `-32768`.
- The layer contains signed dNBR-like values, not project-defined categorical classes.
- Observed valid values include negative and positive values; do not infer classes from arbitrary cutoffs.
- No publisher-attested SHA-256 was advertised; the local SHA-256 is a transfer-integrity record.
- The fresh file remains in `tmp/`; it has not been archived into `data`.

The existing SCANFI diagnostic fixture uses synthetic proxy values. Its `geometry_ref` values such as `data:diagnostic/scanfi_bc_coast_wet_forest_001` are symbolic references, not usable coordinates or polygons. Do not sample the national raster at fabricated centroids.

## Completed implementation

The implemented CanLaBS signal is bounded as follows:

1. Add an isolated CanLaBS module with lazy `rasterio`/`numpy` imports.
2. Provide a pure-array, nodata-aware dNBR summarizer.
3. Provide explicit raster-window reading with validation for single band, EPSG:3978, and 30 m resolution.
4. Return an immutable summary containing valid/nodata counts, min/max/mean/median, source path, CRS, resolution, nodata, value policy, and optional manifest/checksum references.
5. Handle empty/all-nodata windows without crashing.
6. Add an optional CanLaBS summary mapping to `build_harvest_rows()`.
7. Propagate raw dNBR/provenance fields through harvest-row aggregation.
8. Keep the signal diagnostic/provenance-only by default: no yield attenuation, no quantity changes, no cost changes, and no solver changes.
9. Preserve all existing fixture outputs and objective values.

Implemented files include:

- `fresh_fibre/canlabs_severity.py` — lazy-import raster/window summary module.
- `fresh_fibre/scanfi_supply.py` — optional raw dNBR/provenance propagation.
- `tests/test_canlabs_severity.py` — focused unit and integration tests.
- `docs/validation-maps.md`, `scripts/render_validation_map.py`, and
   `scripts/repair_scanfi_source_crs.py` — lightweight validation tooling.
- `.gitignore` — scoped rules preventing generated real-sampled LP outputs from
   accumulating in the parent worktree.
- `HANDOFF_PROMPT.md` — this handoff.

## Immediate next work

1. Verify the pushed branch and clean tree before making further changes:
   `git status --short --branch --untracked-files=all`
2. Continue from the raw dNBR/provenance seam rather than adding arbitrary
   severity classes or yield multipliers.
3. Materialize and validate real cell geometry/window references before using
   the verified CanLaBS raster for any cell-level model signal.
4. If model behavior is changed, add a new explicit diagnostic variant and
   preserve the existing synthetic, ws3-smoke, SCANFI, and Gauthier variants.
5. Run focused tests after each change, then the full scoped suite:
   `.venv/bin/python -m pytest tests/ -q`
6. Run `.venv/bin/python -m compileall -q fresh_fibre tests` and both parent/data
   `git diff --check` commands.
7. Keep commits thematic, update the governing child issue/checklist, and open
   a PR only when the branch-level implementation and documentation are ready.

## Scientific boundary

The first defensible CanLaBS integration is a raw dNBR summary carried through model provenance. It is useful in the actual model path because it can be attached to inventory/harvest/supply records and later used by a documented, calibrated disturbance or salvage policy. It must not silently change harvest quantities until:

- the cell geometry/window extraction is real and validated;
- CanLaBS semantics and any thresholds are documented from official material;
- a forestry/biomass response relationship is calibrated and validated;
- production gates for yields, costs, NFI/climate predictors, and benchmarks are resolved.

Do not modify `graph.py`, `lp.py`, `ws3_export.py`, `contract.py`, or `validation.py` unless a verified test failure proves it is necessary.

## Expected final response

Report briefly:

- files actually changed;
- exact validation commands and results;
- whether the raw dNBR signal reaches harvest/supply provenance;
- whether default quantities/objectives remain unchanged;
- any concrete remaining blocker or caveat.

Before reporting repository state, independently verify the branch, upstream
tracking, clean worktree, and submodule synchronization. Do not say that work
is pushed unless the remote comparison confirms it.

Do not report production readiness.
