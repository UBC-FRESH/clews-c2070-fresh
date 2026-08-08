# Handoff Prompt — CanLaBS Signal Integration

Continue the model-development work in `/home/gep/projects/clews-c2070-fresh`.

## User intent

The goal is to build an actual forestry/biomass model, not substitute governance or process language for implementation. Make concrete, testable engineering progress. Preserve the existing dirty worktree and generated artifacts. Do not claim completion without directly verifying files and command results. Do not invent scientific mappings, thresholds, classes, multipliers, calibration, or production readiness.

## Repository and runtime

- Repository: `/home/gep/projects/clews-c2070-fresh`
- Use the repository virtual environment: `.venv/bin/python`
- Correct regression command: `.venv/bin/python -m pytest tests/ -q`
- Do **not** run bare `pytest -q`; it recursively collects unrelated `.venv` tests.
- Run `git diff --check` for the parent repository and, when relevant, `git -C data diff --check`.
- Keep heavy raster payloads outside tracked parent-repository paths.

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

## Current implementation handoff

A delegated implementation task was started with this bounded scope:

- `fresh_fibre/canlabs_severity.py`
- `fresh_fibre/scanfi_supply.py`
- `tests/test_canlabs_severity.py`

The intended implementation is:

1. Add an isolated CanLaBS module with lazy `rasterio`/`numpy` imports.
2. Provide a pure-array, nodata-aware dNBR summarizer.
3. Provide explicit raster-window reading with validation for single band, EPSG:3978, and 30 m resolution.
4. Return an immutable summary containing valid/nodata counts, min/max/mean/median, source path, CRS, resolution, nodata, value policy, and optional manifest/checksum references.
5. Handle empty/all-nodata windows without crashing.
6. Add an optional CanLaBS summary mapping to `build_harvest_rows()`.
7. Propagate raw dNBR/provenance fields through harvest-row aggregation.
8. Keep the signal diagnostic/provenance-only by default: no yield attenuation, no quantity changes, no cost changes, and no solver changes.
9. Preserve all existing fixture outputs and objective values.

The supervisor reported a completed implementation with these files:

- `fresh_fibre/canlabs_severity.py` — new module
- `fresh_fibre/scanfi_supply.py` — integration
- `tests/test_canlabs_severity.py` — focused tests

That report was truncated, so independently inspect the actual diff and files before accepting it.

## Immediate work

1. Inspect `git diff --stat`, `git diff -- fresh_fibre/canlabs_severity.py fresh_fibre/scanfi_supply.py tests/test_canlabs_severity.py`, and the new module/tests.
2. Confirm the implementation does not introduce arbitrary classes, thresholds, multipliers, yield attenuation, fake coordinates, or full-raster scanning by default.
3. Confirm legacy `fresh_fibre.scanfi_supply` behavior and field compatibility.
4. Run focused tests:
   `.venv/bin/python -m pytest tests/test_canlabs_severity.py tests/test_bc_mini_prototype.py -q`
5. Run:
   `.venv/bin/python -m compileall -q fresh_fibre tests`
6. Run:
   `git diff --check`
7. Then run the full scoped suite:
   `.venv/bin/python -m pytest tests/ -q`
8. Fix concrete defects directly, keeping changes minimal and within the intended files.
9. Update the task checklist to reflect verified status.

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

Do not report production readiness.
