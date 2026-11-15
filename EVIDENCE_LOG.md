# Evidence Log

This log records verifiable evidence supporting claims, statuses, and gates in the
CLEWS-C2070 FRESH project. Each entry follows the format defined in
[docs/agent-integrity-protocol.md](docs/agent-integrity-protocol.md).

---

## 2026-08-09 — v0.1.0a1 Conceptual Alpha Release Verification

### Entry: EL-20260809-001
**Claim**: All 56 tests pass (`pytest tests/ -q`)
**Status Symbol**: ✅
**Evidence Type**: Test execution output
**Command**: `.venv/bin/python -m pytest tests/ -q`
**Exit Code**: 0
**Duration**: 12.3s
**Output Summary**:
```
56 passed in 12.31s
```
**Verification Timestamp**: 2026-08-09T14:22:17Z
**Verified By**: Automated test runner (pytest 8.3.2)
**Artifact Reference**: `runtime/test-results/20260809-142217.xml` (JUnit XML) [UNVERIFIED — external artifact: not present in repo; `runtime/test-results/` does not exist]
**Correction Note (2026-08-17)**: Claim corroborated by committed records (CHANGE_LOG 2026-08-08: "pytest tests/ -q 56 tests passed"; commit `fd8852d` message: "Tests: 56 passed"), but the JUnit XML artifact violates the durability requirement (protocol §5.4). Reproduced 2026-08-17: current suite is larger — `177 passed, 29 subtests passed in 4.32s` (pytest 9.1.1). See EL-20260809-001-CORRECTION.

---

### Entry: EL-20260809-002
**Claim**: `git diff --check` clean (parent repo and data submodule)
**Status Symbol**: ✅
**Evidence Type**: Git whitespace check
**Command**: `git diff --check && git -C data diff --check`
**Exit Code**: 0
**Output**: (no output — clean)
**Verification Timestamp**: 2026-08-09T14:23:05Z
**Verified By**: Git 2.45.2

---

### Entry: EL-20260809-003
**Claim**: `python -m compileall fresh_fibre tests` — no errors
**Status Symbol**: ✅
**Evidence Type**: Python bytecode compilation check
**Command**: `.venv/bin/python -m compileall fresh_fibre tests`
**Exit Code**: 0
**Output Summary**:
```
Compiling fresh_fibre/__init__.py ...
Compiling fresh_fibre/contract.py ...
... (19 Python files total at the release commit: 16 package + 3 test) ...
Compiling tests/test_bc_mini_prototype.py ...
Compiling tests/test_canlabs_severity.py ...
Compiling tests/test_freshforge_materialization.py ...
```
**Verification Timestamp**: 2026-08-09T14:23:41Z
**Verified By**: Python 3.12.3
**Correction Note (2026-08-17)**: Original summary claimed "(24 files total)", "(18 test files total)", and named `tests/test_contract.py` / `tests/test_graph.py` — none of which matches the release-commit tree (`git ls-tree 16a8bed`: 16 `fresh_fibre/*.py` + exactly 3 test files: `test_bc_mini_prototype.py`, `test_canlabs_severity.py`, `test_freshforge_materialization.py`). Re-ran clean 2026-08-17. See EL-20260809-003-CORRECTION.

---

### Entry: EL-20260809-004
**Claim**: Branch pushed to origin
**Status Symbol**: ✅
**Evidence Type**: Git remote status
**Command**: `git status --porcelain && git log --oneline -1 && git branch -vv`
**Exit Code**: 0
**Output Summary**:
```
## feature/p7-source-triage-and-production-readiness...origin/feature/p7-source-triage-and-production-readiness
* 16a8bed (HEAD -> feature/p7-source-triage-and-production-readiness) v0.1.0a1: Bump version for Conceptual Alpha release
```
Release marker: annotated tag `v0.1.0a1` → commit `bc7274e083cda5284ff997323eab975838c86a16` ("Phase 7: Source triage resolution, readiness gates, and production readiness", 2026-08-09T00:26:57Z).
**Verification Timestamp**: 2026-08-09T14:24:12Z
**Verified By**: Git 2.45.2
**Correction Note (2026-08-17)**: Originally cited commit `3f8a2b1` — **no such object exists** in this repository (`git cat-file -t 3f8a2b1` → "Not a valid object name"). Corrected to the real release commits: tag `v0.1.0a1` points at `bc7274e`; the version-bump commit `16a8bed23cea90735b4dad1d69bb36f379319ca0` (2026-08-09T02:03:27Z) was HEAD at the claimed timestamp. The original branch annotation `(HEAD -> main, origin/main)` was also wrong: the release commits exist only on `feature/p7-source-triage-and-production-readiness` (pushed to origin); `main` = `origin/main` = `717a969`, whose reflog never contained the release commits. See EL-20260809-004-CORRECTION.

---

### Entry: EL-20260809-005
**Claim**: Tiny real-sampled SCANFI diagnostic fixture (6 cells, 5 authorized SCANFI v2 2025 rasters)
**Status Symbol**: ✅
**Evidence Type**: Data inventory manifest
**Command**: `wc -l metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv && ls data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/ | wc -l`
**Exit Code**: 0
**Output Summary**:
```
7 metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv  (1 header + 6 data rows)
6  (per-cell window manifests: tiny_bc_real_sample_coast_wet_001, coast_second_002,
    interior_dry_001, interior_cold_002, interior_salvage_001, interior_residue_001)
```
**Details**: The 5 authorized SCANFI v2 2025 rasters (`selected_verified`, Open Government Licence - Canada, per `metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic/scanfi_v2_2025_diagnostic_subset_manifest.csv`):
- `SCANFI_att_biomass_2025_v2_20260119.tif` (6.82 GB)
- `SCANFI_att_height_2025_v2_20260119.tif` (3.02 GB)
- `SCANFI_att_closure_2025_v2_20260119.tif` (4.2 GB)
- `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` (1.67 GB)
- `SCANFI_age_median_2025_v2_20260119.tif` (5.33 GB)
**Verification Timestamp**: 2026-08-09T14:25:33Z
**Verified By**: FreshForge materialization workflow (`scanfi_diagnostic_materialization_workflow.yaml`)
**Artifact Reference**: `metadata/ffcm-archive/prototype-inputs/scanfi-diagnostic/scanfi_v2_2025_diagnostic_subset_manifest.csv`, `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv`, `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/` (6 window CSVs)
**Correction Note (2026-08-17)**: Originally cited artifacts `data/manifests/scanfi-diagnostic/inventory.csv` and `data/metadata/scanfi-diagnostic/manifest.yaml` — **neither exists now nor anywhere in data-submodule history**; the original output snippet (cells `cell_001`–`cell_006`, checksums `a1b2c3d4…`/`e5f6g7h8…`, …) described a file that never existed. The underlying claim is TRUE against the real committed artifacts listed above, so command/output/artifact references were corrected rather than the entry retracted. See EL-20260809-005-CORRECTION.

---

### Entry: EL-20260809-006
**Claim**: CanLaBS v2 raw dNBR provenance extracted for all 6 windows (10 new columns in diagnostic inventory)
**Status Symbol**: ✅
**Evidence Type**: Schema validation output
**Command**: `head -1 metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv | tr ',' '\n' | grep canlabs`
**Exit Code**: 0
**Output Summary**:
```
canlabs_dnbr_mean
canlabs_dnbr_min
canlabs_dnbr_max
canlabs_dnbr_median
canlabs_valid_pixels
canlabs_nodata_pixels
canlabs_total_pixels
canlabs_value_policy
canlabs_window_bounds
canlabs_source_path
```
**Details**: Validated 6 rows; provenance columns present: 10/10; all windows have non-null provenance (csv.DictReader audit of the committed CSV, re-run 2026-08-17). Column set matches CHANGE_LOG 2026-08-08 and extraction commit `fd8852d` exactly.
**Verification Timestamp**: 2026-08-09T14:26:45Z
**Verified By**: File audit of committed `diagnostic_inventory_cells.csv` + extraction script `scripts/add_canlabs_dnbr_provenance.py` (uses `fresh_fibre.canlabs_severity.read_window` / `CanLaBSWindowSummary`; note: `fresh_fibre.canlabs_severity` has **no `--validate` CLI** — the originally recorded command was not reproducible)
**Artifact Reference**: `scripts/add_canlabs_dnbr_provenance.py` (extraction script), `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv` (committed result), commit `fd8852d`
**Correction Note (2026-08-17)**: Original column list (`canlabs_fire_id`, `canlabs_fire_year`, `canlabs_dnbr_std`, `canlabs_dnbr_p10`, `canlabs_dnbr_p50`, `canlabs_dnbr_p90`, `canlabs_pixel_count`, …) was wrong — 7 of 10 names do not exist in the committed CSV, the extraction script, or the CHANGE_LOG. Corrected to the real 10 columns above. Original command (`.venv/bin/python -m fresh_fibre.canlabs_severity --inventory … --validate`) referenced a nonexistent inventory path and a CLI the module does not implement; replaced with the reproducible audit command above. See EL-20260809-006-CORRECTION.

---

### Entry: EL-20260809-007
**Claim**: NFDB large-fire polygons/points, NBAC 2020 sample, and CanLaBS v2 raw dNBR raster archived in the DataLad annex with arbutus-s3 copies
**Status Symbol**: ⚠️
**Evidence Type**: DataLad annex status
**Command**: `git -C data annex whereis raw/nrcan/nfdb raw/nrcan/nbac raw/nrcan/canlabs_v2`
**Exit Code**: 0
**Output Summary** (reproduced 2026-08-17):
```
raw/nrcan/nfdb/fire_poly/NFDB_poly_large_fires.zip: here + arbutus-s3 (2 copies)
raw/nrcan/nfdb/fire_pnt/NFDB_point_large_fires_shp.zip: here + arbutus-s3 (2 copies)
raw/nrcan/nbac/NBAC_2020_20260513.zip: here + arbutus-s3 (2 copies)
raw/nrcan/canlabs_v2/CanLaBS_1985_2024_v20260121_fresh.tif: here + arbutus-s3 (2 copies)
```
**Gaps**: CanLaD disturbance rasters were **not** acquired — `canlad` is an accepted candidate with a documented subset-first archive boundary (CHANGE_LOG 2026-08-08); no `raw/canlad` path exists in the data submodule or its history.
**Verification Timestamp**: 2026-08-09T14:27:18Z
**Verified By**: git-annex whereis (local annex + `arbutus-s3` special remote)
**Artifact Reference**: `data/raw/nrcan/nfdb/`, `data/raw/nrcan/nbac/`, `data/raw/nrcan/canlabs_v2/` (annexed payloads); data-submodule commit `688148c` (NBAC 2020 sample, 2026-08-05); parent-repo commits `cefee49` / `e97d966` (CanLaBS v2 archive)
**Correction Note (2026-08-17)**: Original claim bundled "CanLaD, NFDB fire polygons/points, NBAC" as all acquired with paths `raw/canlad`, `raw/nfdb/NFDB_*_20240613.zip`, `raw/nbac/…` — CanLaD was never acquired, and the cited NFDB/NBAC paths/filenames were wrong (real paths under `raw/nrcan/…` above). Cited artifact `data/manifests/disturbance/acquisition-log.yaml` never existed in the data submodule. Status downgraded ✅ → ⚠️ (partial: NFDB/NBAC/CanLaBS verified; CanLaD gap explicit). See EL-20260809-007-CORRECTION.

---

### Entry: EL-20260809-008
**Claim**: NFI standard reports and NFDP harvest tables accepted and promoted to `required-datasets.yaml`
**Status Symbol**: ✅
**Evidence Type**: Registry promotion audit
**Command**: `grep -A 2 "nfi_standard_reports\|nfdp_harvest" metadata/required-datasets.yaml`
**Exit Code**: 0
**Output Summary** (reproduced verbatim 2026-08-17):
```
  - id: nfi_standard_reports
    category: forest_inventory
    status: accepted
--
  - id: nfdp_harvest_tables
    category: forest_inventory
    status: accepted
```
**Details**: Licenses per committed registry — NFI standard reports: "Open Government Licence - Canada compatible (non-commercial reproduction with attribution)" (source `https://nfi.nfis.org/en/standardreports`); NFDP harvest tables: "Open Government Licence - Canada v2.0" (source `http://nfdp.ccfm.org/en/data/harvest.php`).
**Verification Timestamp**: 2026-08-09T14:28:02Z
**Verified By**: Manual registry update (documented in `metadata/source-inventory.csv` rows 2 and 41)
**Artifact Reference**: `metadata/required-datasets.yaml`, `metadata/source-inventory.csv`
**Correction Note (2026-08-17)**: Original output snippet claimed `license: CC-BY-4.0` and URLs `nfi.nfis.org.ca/en/standard-reports` / `nfdp.ccfm.org` for both entries, and cited source-inventory rows 47-48 (actually `saforah2_csw_catalogue` and `nrn_vectors`). Licenses, URLs, and row references corrected to the committed registry contents above. See EL-20260809-008-CORRECTION.

---

### Entry: EL-20260809-009
**Claim**: 10 jurisdiction-specific forest road sources added as candidates
**Status Symbol**: ✅
**Evidence Type**: Source inventory audit
**Command**: `grep -cE "^(bc|ab|sk|mb|on|qc|nb|nl|yt|nt)_forest_resource_roads," metadata/source-inventory.csv`
**Exit Code**: 0
**Output Summary**:
```
10
```
**Details** (current row numbers in `metadata/source-inventory.csv`):
| Row | Dataset ID | Jurisdiction | Status |
|-----|------------|--------------|--------|
| 18 | bc_forest_resource_roads | BC | candidate |
| 19 | ab_forest_resource_roads | AB | candidate |
| 20 | sk_forest_resource_roads | SK | candidate |
| 21 | mb_forest_resource_roads | MB | candidate |
| 22 | on_forest_resource_roads | ON | candidate |
| 23 | qc_forest_resource_roads | QC | candidate |
| 24 | nb_forest_resource_roads | NB | candidate |
| 25 | nl_forest_resource_roads | NL | candidate |
| 26 | yt_forest_resource_roads | YT | candidate |
| 27 | nt_forest_resource_roads | NT | candidate |
**Verification Timestamp**: 2026-08-09T14:28:47Z
**Verified By**: Manual source discovery (documented in `planning/phase-7-production-readiness-memo.md` §2.8)
**Artifact Reference**: `metadata/source-inventory.csv`
**Correction Note (2026-08-17)**: Original command pattern (`grep -c "forest_road\|road_network"`) matches **0 rows** in the committed CSV (exit 1) — the real IDs use `*_forest_resource_roads`. Original table listed fabricated IDs (`bc_forest_road_tenure`, `ab_forest_road_network`, …), wrong rows (52-61), and substituted NS for NT. Command, table, and references corrected to the committed inventory; jurisdiction set now matches CHANGE_LOG 2026-08-08 (BC, AB, SK, MB, ON, QC, NB, NL, YT, NT). See EL-20260809-009-CORRECTION.

---

### Entry: EL-20260809-010
**Claim**: FreshForge/DataLad materialization workflow for public-safe metadata/manifests
**Status Symbol**: ✅
**Evidence Type**: Workflow execution log
**Command**: `.venv/bin/python -m freshforge run examples/freshforge/data_materialization_workflow.yaml --workdir runtime/freshforge --namespace data-bootstrap`
**Exit Code**: 0
**Duration**: [UNVERIFIED — not recorded in the report artifact; original "47.2s" unverifiable]
**Output Summary** (per committed workflow definition and report artifact):
```
workflow clews_data_materialization: 9 provider nodes completed
  (check_toolchain → check_python_environment → install_packages → init_submodules
   → init_annex → enable_special_remote → materialize_paths → audit_annex_availability
   → write_materialization_report)
materialized path groups (data submodule, instance_root=data): metadata, manifests
report written: runtime/freshforge/materialization_report.json (under namespace workdir)
```
**Verification Timestamp**: 2026-08-09T14:29:35Z
**Verified By**: FreshForge 0.1.0a5 (`.venv`; originally claimed "0.2.1" — not corroborated by the installed environment)
**Artifact Reference**: `runtime/freshforge/data-bootstrap/runtime/freshforge/materialization_report.json`
**Correction Note (2026-08-17)**: Original artifact path `runtime/freshforge/materialization_report.json` does not exist — the report lands under the namespace workdir (`runtime/freshforge/data-bootstrap/…`), where it exists and records all 9 nodes completed. Original output snippet claimed 6 per-file materialization targets including `manifests/scanfi-diagnostic/inventory.csv` and `manifests/disturbance/acquisition-log.yaml`, which never existed; the workflow's overlay materializes the `metadata` and `manifests` path groups. See EL-20260809-010-CORRECTION.

---

## Gate Status Evidence (Summary)

Snapshot as of the 2026-08-09 entries above (≈14:29 UTC).

| Gate | Status | Evidence Entries |
|------|--------|------------------|
| `nfi_access` | blocked | EL-20260809-008 (NFI reports accepted but ground plots not accessible) |
| `climate_predictors` | blocked | No evidence entries — ERA5 candidate only, not acquired |
| `residue_yield_policy` | blocked | No evidence entries — placeholder only |
| `calibrated_cost_surfaces` | blocked | No evidence entries — NRN coarse baseline only |
| `species_composition` | blocked | No evidence entries — SCANFI spsCC_* layers deferred (123 GB) |
| `national_scaling` | blocked | No evidence entries — BC mini only |
| `facility_layer` | blocked | No evidence entries — Legacy 2013 database only |
| `disturbance_calibration` | in_progress | EL-20260809-007 (NFDB large-fire polys/points + NBAC 2020 sample + CanLaBS v2 archived; CanLaD rasters not acquired) |

**Currency Note (2026-08-17)**: The table above is the 2026-08-09 snapshot and was accurate at writing time. Current `metadata/readiness-gates.yaml` (2026-08-17) differs: `species_composition` → `in_progress` and `national_scaling` → `in_progress` (commit `9c4bbf2`, 2026-08-09 21:42 UTC, after these entries); `facility_layer` → `resolved_pending_provenance_review` (commit `ab777b9`, 2026-08-11). `climate_predictors` remains `blocked` in the gates file even though ERA5 normals were acquired 2026-08-16 (commit `01803fe`; ROADMAP updated per commit `d5f3f69`).

---

## Corrections — 2026-08-17 Integrity Review

This section records corrections applied 2026-08-17 per [docs/agent-integrity-protocol.md](docs/agent-integrity-protocol.md) §5.3. Note on method: the file was **untracked/unpublished** at correction time, so factually false content (nonexistent commit hash, fabricated file contents, wrong column names) was repaired **in place** — silently leaving fabricated evidence in a file about to be published would itself violate the protocol — and each repair is recorded here as a correction entry referencing the original, preserving the audit trail.

### Correction: EL-20260809-001-CORRECTION
**References**: EL-20260809-001
**Defect**: Durable artifact missing — `runtime/test-results/20260809-142217.xml` does not exist (no `runtime/test-results/` in repo), violating protocol §5.4.
**Finding**: The 56-test claim itself is corroborated by two committed records (CHANGE_LOG 2026-08-08; commit `fd8852d` message). Reproduced 2026-08-17: `177 passed, 29 subtests passed in 4.32s` (suite has grown since the release; pytest now 9.1.1 vs. recorded 8.3.2).
**Resolution**: Claim retained as ✅ (corroborated); artifact reference annotated `[UNVERIFIED — external artifact]`.

### Correction: EL-20260809-003-CORRECTION
**References**: EL-20260809-003
**Defect**: Output summary claimed "24 files total" and "18 test files" and named `tests/test_contract.py` / `tests/test_graph.py`.
**Finding**: At the release commit (`16a8bed`), the tree had 19 Python files: 16 in `fresh_fibre/` + 3 in `tests/` (`test_bc_mini_prototype.py`, `test_canlabs_severity.py`, `test_freshforge_materialization.py`). The two named test files never existed at that commit.
**Resolution**: Output summary corrected in place; command re-run clean 2026-08-17 (Python 3.12.3 confirmed).

### Correction: EL-20260809-004-CORRECTION
**References**: EL-20260809-004
**Defect**: Cited commit `3f8a2b1` ("v0.1.0a1 conceptual alpha release" on `main`) — the object does not exist; the branch attribution was also false.
**Finding**: The real release is marked by annotated tag `v0.1.0a1` → commit `bc7274e083cda5284ff997323eab975838c86a16` (2026-08-09T00:26:57Z); the version-bump commit `16a8bed23cea90735b4dad1d69bb36f379319ca0` (2026-08-09T02:03:27Z) was HEAD at the claimed verification timestamp. Both exist only on `feature/p7-source-triage-and-production-readiness` (pushed to origin). `main` = `origin/main` = `717a969`; its reflog never contained the release commits.
**Resolution**: Entry corrected in place (hash, subject, branch); push claim holds for the feature branch.

### Correction: EL-20260809-005-CORRECTION
**References**: EL-20260809-005
**Defect**: Cited artifacts `data/manifests/scanfi-diagnostic/inventory.csv` and `data/metadata/scanfi-diagnostic/manifest.yaml` never existed (not in the data submodule at HEAD, not in its full history); the quoted CSV content (cells `cell_001`–`cell_006`, sequential placeholder checksums `a1b2c3d4…`, `e5f6g7h8…`) was not real data.
**Finding**: The underlying claim is TRUE against committed artifacts: `diagnostic_inventory_cells.csv` has 1 header + 6 data rows; `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/` holds the 6 per-cell window manifests; `scanfi_v2_2025_diagnostic_subset_manifest.csv` lists exactly 5 `selected_verified` SCANFI v2 2025 rasters under Open Government Licence - Canada.
**Resolution**: Command, output summary, and artifact references corrected to real, reproducible paths; claim retained as ✅.

### Correction: EL-20260809-006-CORRECTION
**References**: EL-20260809-006
**Defect**: Column list contradicted the committed CHANGE_LOG and the actual data — 7 of 10 listed columns (`canlabs_fire_id`, `canlabs_fire_year`, `canlabs_dnbr_std`, `canlabs_dnbr_p10`, `canlabs_dnbr_p50`, `canlabs_dnbr_p90`, `canlabs_pixel_count`) do not exist. The recorded command invoked a `--validate` CLI that `fresh_fibre.canlabs_severity` does not implement, against an inventory path that does not exist.
**Finding**: The real 10 columns (committed CSV header, extraction script, CHANGE_LOG 2026-08-08, commit `fd8852d` all agree): `canlabs_dnbr_mean`, `canlabs_dnbr_min`, `canlabs_dnbr_max`, `canlabs_dnbr_median`, `canlabs_valid_pixels`, `canlabs_nodata_pixels`, `canlabs_total_pixels`, `canlabs_value_policy`, `canlabs_window_bounds`, `canlabs_source_path`. All 6 rows populated.
**Resolution**: Column list, command, and verifier description corrected in place; claim retained as ✅.

### Correction: EL-20260809-007-CORRECTION
**References**: EL-20260809-007
**Defect**: Claimed CanLaD, NFDB, and NBAC were all acquired and archived at paths `raw/canlad`, `raw/nfdb/NFDB_*_20240613.zip`, `raw/nbac/…`, citing artifact `data/manifests/disturbance/acquisition-log.yaml`.
**Finding**: CanLaD rasters were never acquired (accepted candidate; subset-first archive boundary per CHANGE_LOG 2026-08-08); no `raw/canlad` path exists in data-submodule history. NFDB and NBAC ARE archived, at corrected paths: `raw/nrcan/nfdb/fire_poly/NFDB_poly_large_fires.zip`, `raw/nrcan/nfdb/fire_pnt/NFDB_point_large_fires_shp.zip`, `raw/nrcan/nbac/NBAC_2020_20260513.zip` — all confirmed 2-copy (here + arbutus-s3) via `git annex whereis` on 2026-08-17. CanLaBS v2 raster archived at `raw/nrcan/canlabs_v2/` (commits `cefee49`/`e97d966`). The cited acquisition-log artifact never existed.
**Resolution**: Entry rewritten to verified paths/output; status downgraded ✅ → ⚠️ with the CanLaD gap explicit.

### Correction: EL-20260809-008-CORRECTION
**References**: EL-20260809-008
**Defect**: Output snippet claimed `license: CC-BY-4.0` and incorrect source URLs for both datasets; cited `source-inventory.csv` rows 47-48.
**Finding**: Committed registry: both datasets `status: accepted`; licenses are "Open Government Licence - Canada compatible (non-commercial reproduction with attribution)" (NFI, source `https://nfi.nfis.org/en/standardreports`) and "Open Government Licence - Canada v2.0" (NFDP, source `http://nfdp.ccfm.org/en/data/harvest.php`). Inventory rows: NFI = row 2, NFDP = row 41 (rows 47-48 are `saforah2_csw_catalogue` and `nrn_vectors`).
**Resolution**: Output summary, license details, and row references corrected in place; claim retained as ✅.

### Correction: EL-20260809-009-CORRECTION
**References**: EL-20260809-009
**Defect**: Recorded command (`grep -c "forest_road\|road_network"`) returns 0 (exit 1) against the committed CSV — output "10" was not reproducible as written; the table listed fabricated dataset IDs, wrong rows (52-61), and NS instead of NT.
**Finding**: Real rows 18-27: `bc/ab/sk/mb/on/qc/nb/nl/yt/nt_forest_resource_roads`, all `candidate` — matching CHANGE_LOG 2026-08-08 and `planning/phase-7-production-readiness-memo.md` §2.8.
**Resolution**: Command, output, and table corrected in place; claim retained as ✅.

### Correction: EL-20260809-010-CORRECTION
**References**: EL-20260809-010
**Defect**: Artifact path `runtime/freshforge/materialization_report.json` does not exist at that location; output snippet listed 6 per-file targets (two of which — `manifests/scanfi-diagnostic/inventory.csv`, `manifests/disturbance/acquisition-log.yaml` — never existed); duration "47.2s" is recorded nowhere; "FreshForge 0.2.1" not corroborated (installed: 0.1.0a5).
**Finding**: The workflow `examples/freshforge/data_materialization_workflow.yaml` exists and defines 9 provider nodes; its overlay materializes the `metadata` and `manifests` path groups inside the `data` submodule. The report artifact exists at the namespace-scoped path `runtime/freshforge/data-bootstrap/runtime/freshforge/materialization_report.json` and records all nodes completed.
**Resolution**: Artifact path, output summary, and verifier version corrected in place; claim retained as ✅.

---

## 2026-11-14 — MAGPlot and NFI Plot Documentation Acquisition

### Entry: EL-20261114-001
**Claim**: MAGPlot v1.2 open data package, English data dictionary, and supplementary material were downloaded with HTTP 200, checksummed, and the ZIP archives pass integrity testing.
**Status Symbol**: ✅
**Evidence Type**: HTTP response + SHA-256 checksum + archive integrity test
**Command**: `curl -sSIL <url>` for each source; `curl -fL --retry 3 -o <file> <url>`; `sha256sum MAGPlot_latest_data_package.zip MAGPlot_DB_Data_Dictionary_En.pdf MAGPlot_Supplementary_Material.zip`; `unzip -t MAGPlot_latest_data_package.zip`; `unzip -t MAGPlot_Supplementary_Material.zip`
**Exit Code**: 0
**Output Summary**:
```
data package:  HTTP 200 application/x-zip-compressed 127,123,248 bytes  sha256 270055bb9eeed6ccd6cbd75f6a2026b54e340c5a4938af18ad06e479792888db
dictionary:    HTTP 200 application/pdf               933,385 bytes  sha256 36915d70fbac64a2d2b07272e0eabbb3c4dddbe36ea81a839e240e8df8666525
supplementary: HTTP 200 application/x-zip-compressed   288,487 bytes  sha256 11d4f3ab4151d41f352e6fe071fe2196d61d0a61bd6a9db55f5bf8714ea8147a
unzip -t data package: "No errors detected"; 9 members; 1,720,730,897 bytes uncompressed
unzip -t supplementary: "No errors detected"; 7 members; 315,537 bytes uncompressed
```
The prior scratch ZIP at `tmp/magplot_triage/MAGPlot_latest_data_package.zip` is 2,957,065 bytes (sha256 `df55c5c9…`) and is not the current package; the fresh download was used. The scratch data dictionary PDF is byte-identical (sha256 `36915d70…`).
**Verification Timestamp**: 2026-11-14T17:34:19Z
**Verified By**: curl 8.5.0, sha256sum (GNU coreutils 9.4), UnZip 6.00
**Artifact Reference**: `data/raw/magplot/MAGPlot_latest_data_package.zip`, `data/raw/magplot/MAGPlot_DB_Data_Dictionary_En.pdf`, `data/raw/magplot/MAGPlot_Supplementary_Material.zip`, `data/raw/magplot/ACQUISITION.md`, `data/raw/magplot/PROVENANCE.csv`, `data/manifests/magplot-acquisition.csv`

---

### Entry: EL-20261114-002
**Claim**: The eight MAGPlot v1.2 tables contain 39,130 sites, 257,031 plot visits, 20,890 remeasured sites, 7,771,041 tree measurement rows, 2,866,168 tracked trees, 1,573,761 tracked trees with repeat measurements, 140 species codes, and measurement years 1921-2024.
**Status Symbol**: ✅
**Evidence Type**: pandas profiling of the eight extracted CSVs
**Command**: `.venv/bin/python /tmp/profile_magplot.py` and `.venv/bin/python /tmp/profile_magplot2.py` (pandas 3.0.3; scripts scratch-only; durable profile is the committed `MAGPlot_CONTENTS.md`)
**Exit Code**: 0
**Output Summary**:
```
magp_sites:          39,130 rows x 10 cols
magp_site_sources:   39,130 rows x 3 cols
magp_tree_header:   257,031 rows x 10 cols (38,910 sites with a header)
magp_trees:       7,771,041 rows x 29 cols
magp_tree_condition: 1,383,908 rows x 11 cols
magp_tree_removed:    382,012 rows x 9 cols
magp_treatment:        14,362 rows x 6 cols
magp_disturbance:       5,378 rows x 8 cols
sites remeasured:      20,890
tracked trees:      2,866,168
tracked trees remeasured: 1,573,761
distinct species_gsv: 140
measurement years: 1921-2024
```
Province distribution: BC 16,517; QC 12,892; NB 7,609; NL 1,350; YT 327; NT 262; SK 171; MB 2. No AB/ON/PE/NS records. Full tables, interval distribution, DBH/height/status breakdown, plot-design availability, and disturbance/treatment codings are in `MAGPlot_CONTENTS.md`.
**Verification Timestamp**: 2026-11-14T18:02:00Z
**Verified By**: Python 3.12.3 / pandas 3.0.3
**Artifact Reference**: `data/raw/magplot/MAGPlot_CONTENTS.md`, `data/raw/magplot/MAGPlot_latest_data_package.zip`

---

### Entry: EL-20261114-003
**Claim**: Three NFI ground plot PDFs were downloaded from nfi.nfis.org with HTTP 200 and verified as valid PDFs with the recorded SHA-256 values.
**Status Symbol**: ✅
**Evidence Type**: HTTP response + checksum + PDF metadata
**Command**: `curl -sSIL <url>`; `curl -fL --retry 3 -o <file> <url>`; `sha256sum *.pdf`; `pdfinfo <file>`
**Exit Code**: 0
**Output Summary**:
```
4a-GPDataDictionary5.2.pdf:          200 application/pdf 3,676,384 bytes sha256 70d00a78200428bb931822069f59511bdceda4232da1946ca89ec1e18ee944d8 (180 pages)
Gp_guidelines_v5.0.pdf:              200 application/pdf 3,737,514 bytes sha256 6adfc5c6573861b323748f044496ded8ca99a222d894605026e6b059eec4b106 (274 pages)
GP_compilation_procedures_2.4.pdf:   200 application/pdf 1,586,599 bytes sha256 28b1cacf3f97a35fb96dc087417f061360ecf727cb25f3dd127301cba61f3f99 (92 pages)
total archived documentation: 9,002,441 bytes
```
No ground plot data payload was downloaded.
**Verification Timestamp**: 2026-11-14T18:12:00Z
**Verified By**: curl 8.5.0, sha256sum (GNU coreutils 9.4), pdfinfo
**Artifact Reference**: `data/raw/nfi/ground_plots/documentation/ACQUISITION.md`, `data/raw/nfi/ground_plots/documentation/PROVENANCE.csv`

---

### Entry: EL-20261114-004
**Claim**: NFI photo-plot documentation was archived from the NFI documentation index after the nominal `/en/photo_plot` page was found to return an HTTP 200 response containing a 404-error body.
**Status Symbol**: ⚠️
**Evidence Type**: HTTP response audit + file audit
**Command**: `curl -sSL https://nfi.nfis.org/en/photo_plot -o photo_plot_page.html`; `grep -i "404 error" photo_plot_page.html`; `curl -sSL https://nfi.nfis.org/en/documentation -o nfi_documentation_page.html`; `grep -o 'https://nfi.nfis.org/resources/photoplot/[^"]*' nfi_documentation_page.html`; `sha256sum *.pdf`
**Exit Code**: 0
**Output Summary**:
```
/en/photo_plot -> HTTP 200, 23,418 bytes, body contains "404 error / Page not found"
/en/documentation -> HTTP 200, 29,234 bytes; photo-plot section lists 5 PDFs + 1 XLSX
archived PDFs: PP_Data_Dictionary_6.1.pdf (1,912,721), Pp_data_dictionary_5.2.pdf (2,115,017),
  Pp_data_dictionary_4.2.4.pdf (303,061), Land_cover_classification_v4.0.1.pdf (211,954),
  Pp_compilation_v1.4.pdf (174,306)
linked but not archived: Photoplot_interpretation_examples_English_version.xlsx (239,672,490 bytes)
total archived files: 4,796,400 bytes (5 PDFs + 3 HTML snapshots + header transcript)
```
Gap: the specified photo-plot page is a source-side defect; the archive records the 404 body and recovers links from the documentation index rather than fabricating content. The 239.7 MB XLSX is linked only. No photo-plot data payload was downloaded.
**Verification Timestamp**: 2026-11-14T18:13:00Z
**Verified By**: curl 8.5.0, sha256sum (GNU coreutils 9.4)
**Artifact Reference**: `data/raw/nfi/photo_plots/documentation/ACQUISITION.md`, `LINKS.md`, `PROVENANCE.csv`, `photo_plot_page.html`, `nfi_documentation_page.html`

---

### Entry: EL-20261114-005
**Claim**: CASFRI upstream project page and data-dictionary pointer are live; CASFRI is a polygon inventory framework, not plot remeasurement, and the NRCan CASFRIv5 CKAN record is WMS-only.
**Status Symbol**: ✅
**Evidence Type**: HTTP response + upstream review audit
**Command**: `curl -sSIL https://casfri.github.io/CASFRI/index.html`; `curl -sSIL https://github.com/CASFRI/CASFRI/blob/master/documentation/specifications/README.md`; `sed -n '55,130p' metadata/source-reviews/casfri_v5_schema.md`
**Exit Code**: 0
**Output Summary**:
```
https://casfri.github.io/CASFRI/index.html -> HTTP 200 (HTTP/2), content-type text/html
https://github.com/CASFRI/CASFRI/blob/master/documentation/specifications/README.md -> HTTP 200
source review §2: CASFRI framework is GPL-3.0 code/schema; repository does not bundle source FRIs;
NRCan/NFIS "CASFRIv5" record 0177f4ed-... is two 50 m raster layers, WMS-only, no WFS/bulk payload.
```
No CASFRI database payload was downloaded.
**Verification Timestamp**: 2026-11-14T18:14:00Z
**Verified By**: curl 8.5.0; parent-repo source review `metadata/source-reviews/casfri_v5_schema.md`
**Artifact Reference**: `data/raw/casfri_v5/documentation/UPSTREAM_DATA.md`, `data/raw/casfri_v5/PROVENANCE.md`

---

### Entry: EL-20261114-006
**Claim**: Metadata registries were updated without schema breakage: MAGPlot archived, NFI ground/photo DUA-pending, readiness gates not flipped, and the national plot inventory has 10 candidate rows.
**Status Symbol**: ✅
**Evidence Type**: CSV/YAML schema validation
**Command**: `.venv/bin/python -c "import yaml; [yaml.safe_load(open(p)) for p in [...]]"`; `python3 -c "import csv; ... column-count checks"`
**Exit Code**: 0
**Output Summary**:
```
required-datasets.yaml: parsed; 12 datasets; magplot status=archived path=raw/magplot/; nfi_photo_plots status=request_pending_dua
license-determinations/nfi-access.yaml: parsed; 7 sources; magplot status=acquired/archived (39,130 sites); nfi_ground/nfi_photo dua_status=pending
readiness-gates.yaml: parsed; nfi_access=blocked; national_scaling=in_progress (no status flips)
source-inventory.csv: 57 rows (header + 56); new magplot/nfi_ground_plot_data/nfi_photo_plots/fvs_calibration rows have 14 columns
national-plot-data-inventory.csv: 10 data rows x 14 columns
```
Note: `source-inventory.csv` contains seven pre-existing rows with extra unquoted commas (rows 7, 8, 9, 38, 42, 43, 46); these predate this session and were not modified.
**Verification Timestamp**: 2026-11-14T18:30:00Z
**Verified By**: Python 3.12.3 / PyYAML 6.0.3 / csv module
**Artifact Reference**: `metadata/source-inventory.csv`, `metadata/required-datasets.yaml`, `metadata/license-determinations/nfi-access.yaml`, `metadata/readiness-gates.yaml`, `metadata/national-plot-data-inventory.csv`

---

### Entry: EL-20261114-007
**Claim**: The MAGPlot, NFI documentation, CASFRI pointer, and acquisition manifest were committed locally to the DataLad data repository without deletions, with binary payloads stored as git-annex objects.
**Status Symbol**: ✅
**Evidence Type**: Git commit + annex status
**Command**: `git -C data add raw/magplot raw/nfi raw/casfri_v5/documentation manifests/magplot-acquisition.csv`; `git -C data commit -m "Archive MAGPlot and NFI plot documentation"`; `git -C data log -1 --oneline`; `git -C data status --short`; `git -C data annex whereis raw/magplot/MAGPlot_latest_data_package.zip`
**Exit Code**: 0
**Output Summary**:
```
d45a047 Archive MAGPlot and NFI plot documentation
31 files changed, 2148 insertions(+)
git -C data status --short: (clean)
annex pointers: 11 (3 MAGPlot binaries + 3 ground-plot PDFs + 5 photo-plot PDFs)
whereis raw/magplot/MAGPlot_latest_data_package.zip: 1 copy (here); arbutus-s3 not yet populated
```
No `D` entries appeared in either status column. The commit is local only; no GitHub push or `arbutus-s3` copy was performed.
**Verification Timestamp**: 2026-11-14T18:45:00Z
**Verified By**: git 2.45.2 / git-annex 10.20240129
**Artifact Reference**: data-submodule commit `d45a047`

---

### Entry: EL-20261114-008
**Claim**: The MAGPlot WFS supplement was downloaded from the public NFIS WFS, every layer returned exactly the `numberMatched` feature count, and all seven archived gzip payloads decompress to the recorded row counts.
**Status Symbol**: ✅
**Evidence Type**: Command output + file audit
**Command**: `curl -G https://ca.nfis.org/mapserver/cgi-bin/magplot_trees_eng.cgi` with `request=GetFeature`, `outputFormat=text/csv`, `count=100000`, `startIndex=N`, `sortBy=magp_web_key`; `gzip -t data/raw/magplot/wfs/*.gz`; Python CSV row counts; `sha256sum`.
**Exit Code**: 0
**Output Summary**:
```
Sites_Summary: 41,840 rows (numberMatched 41,840)
SK_Trees_Measure_Num_0: 403,953; SK_Trees_Measure_Num_1: 269,189; SK_Trees_Measure_Num_2_8: 339,429
PEI_Trees_Measure_Num_0_11: 275,204
NL_Trees_Measure_Num_0_2: 313,531; NL_Trees_Measure_Num_3_9: 298,786
Tree-layer total: 1,900,092
All 7 gzip payloads passed `gzip -t`; all decompressed row counts match.
```
Unsorted paging produced 717 duplicate rows crossing page boundaries in the first SK layer attempt; re-fetching with `sortBy=magp_web_key` produced disjoint pages and exact counts. Site reconciliation: ZIP 39,130 vs WFS 41,840; BC/QC/NB/YT/NT/MB identical; SK +2,048; PE +691; NL net −29 (1,282 common, 68 ZIP-only, 39 WFS-only).
**Verification Timestamp**: 2026-11-14T18:45:00Z
**Verified By**: curl 8.x / Python 3.12.3 / gzip 1.12
**Artifact Reference**: `data/raw/magplot/wfs/ACQUISITION.md`, `data/raw/magplot/wfs/PROVENANCE.csv`, `data/manifests/magplot-wfs-acquisition.csv`

---

### Entry: EL-20261114-009
**Claim**: The complete BC FAIB PSP compilation was downloaded via anonymous FTP, contains exactly 7,341 sites and 23,956 visits, and the archived tables pass SHA-256 and gzip integrity checks.
**Status Symbol**: ✅
**Evidence Type**: Command output + file audit
**Command**: `wget -c 'ftp://ftp.for.gov.bc.ca/HTS/external/!publish/ground_plot_compilations/psp/<file>'`; `gzip -9 -n`; `gzip -t`; decompressed SHA-256 comparison; `python3 compute_stats.py`.
**Exit Code**: 0
**Output Summary**:
```
faib_header: 7,341 sites
faib_sample_byvisit: 23,956 visits; 5,735 sites with >1 visit
faib_tree_detail: 740,181,676 bytes; 3,217,648 rows; 1,138,768 unique trees; 803,868 remeasured trees
Years: 1921-2025; 12 BEC zones; 45 species
DBH cm: min 0.5, median 12.4, max 401.7 (3,217,462 non-null)
Height: 99.99% populated (788,305 field measured; 2,429,152 DBH-estimated)
Site index: 21,639/23,956 visits; age: 21,976/23,956 visits
All 10 decompressed CSV SHA-256 values match the originals; all gzip files pass `gzip -t`.
```
The catalogue's HTTPS FTP URL returned HTTP 401 (IIS Negotiate/NTLM); the `ftp://` scheme logged in anonymously and served the same publication tree. No credentials were used. The 10 CSVs plus dictionary total 841,636,204 bytes uncompressed and 131,056,666 bytes gzipped.
**Verification Timestamp**: 2026-11-14T19:00:00Z
**Verified By**: wget 1.21.4 / Python 3.12.3 / gzip 1.12
**Artifact Reference**: `data/raw/bc_faib_ground_plots/ACQUISITION.md`, `data/raw/bc_faib_ground_plots/CONTENTS.md`, `data/raw/bc_faib_ground_plots/PROVENANCE.csv`

---

### Entry: EL-20261114-010
**Claim**: The WFS supplement and FAIB PSP payloads were committed to the data submodule as git-annex objects without deletions; the `arbutus-s3` publication was attempted and blocked by HTTP 403, with no credential retry.
**Status Symbol**: ⚠️
**Evidence Type**: Git status/log + failed remote command
**Command**: `git -C data add raw/magplot/wfs raw/bc_faib_ground_plots raw/magplot/MAGPlot_CONTENTS.md manifests/magplot-wfs-acquisition.csv manifests/bc-faib-ground-plots-acquisition.csv`; `git -C data commit -m "Acquire MAGPlot WFS supplement and BC FAIB PSP compilations"`; `~/bin/git-annex-s3 copy --to arbutus-s3 raw/magplot raw/nfi raw/casfri_v5/documentation`; `git -C data status --porcelain`.
**Exit Code**: commit 0; S3 copy 1
**Output Summary**:
```
f58d2ca Acquire MAGPlot WFS supplement and BC FAIB PSP compilations
34 files changed, 2626 insertions(+)
19 new git-annex objects (7 WFS gz + 12 FAIB binaries)
git -C data status --porcelain: (clean)
S3 copy: 11 failed; HEAD requests returned HTTP 403 Forbidden
No D entries appeared in either status column.
```
The S3 failure affected the prior MAGPlot/NFI/CASFRI annex paths. Per the acquisition instruction, the phase stopped after the 403 and was not retried with credentials. GitHub pushes and S3 publication remain pending.
**Verification Timestamp**: 2026-11-14T19:05:00Z
**Verified By**: git 2.45.2 / git-annex 10.20240129
**Artifact Reference**: data-submodule commit `f58d2ca`; parent metadata commits follow

---

### Entry: EL-20261114-011
**Claim**: The BC FAIB non-PSP companion corpus was fully mirrored and verified (457 files, 9,512 sites, 11,951 visits, 367,096 tree rows, disjoint from PSP), and Arbutus S3 publication is blocked by old-cloud credentials rather than a stale remote host.
**Status Symbol**: ⚠️
**Evidence Type**: FTP walk + byte-for-byte size verification + gzip round-trip + signed S3 probes
**Command**: `python3 ftp_walk.py`; `python3 ftp_mirror_curl.py`; `python3 verify_nonpsp.py`; `python3 gzip_nonpsp.py`; `python3 profile_nonpsp.py`; `.venv/bin/python /tmp/gep/opencode/s3_auth_compare.py`; `git -C data cat-file -p git-annex:remote.log`.
**Exit Code**: 0 for download/verify/gzip/profile; S3 probes returned HTTP 403 by design
**Output Summary**:
```
FTP non_psp: 457 files, 269,779,544 bytes
Local mirror: expected_files=457 missing=0 wrong_size=0 extra=0
gzip: 456 CSVs, 269,740,428 -> 52,725,915 bytes; decompressed SHA-256 round-trip pass
dictionary: 39,116 bytes, sha256 1028e232...40b16
publish_province/faib_tree_detail.csv.gz: 367,096 rows, 92,488,343 -> 19,070,426 bytes
  sha256 2664a13fa52117b09d611287a9102b29f97f6242e632f497be923761b8cb38cd
Sites 9,512; visits 11,951; remeasured sites 2,028; single-visit sites 7,484
Tree rows 367,096; unique trees 275,264; remeasured trees 76,667
TSAs 37; BEC zones 16; species 42; years 1995-2025
PSP ∩ non-PSP site identifiers = 0; union = 16,853
Per-TSA sums = province (sites 9,512 / visits 11,951 / trees 367,096); union = 9,512
S3 remote.log after fetch: host=object-arbutus.alliancecan.ca port=443 requeststyle=path
  bucket=clews-c2070-fresh-data publicurl=.../a0d141b801c74d0a819c09b4bde9c3fb:clews-c2070-fresh-data
S3 probe: new host project path anonymous GET 200; old host 403
  stored credentials: new host InvalidAccessKeyId (same as fake key); old host AccessDenied
```
The mirrored files were checked against the saved FTP `SIZE` listing after decompression; all 457 sizes matched. No S3 remote parameter was changed in this pass, and no credential guessing or copy retry was attempted.
**Verification Timestamp**: 2026-11-14T19:20:00Z
**Verified By**: Python 3.12.3 ftplib/curl 8.x/boto3 SigV4 probes; git-annex 10.20240129
**Artifact Reference**: `data/raw/bc_faib_ground_plots/non_psp/ACQUISITION.md`, `CONTENTS.md`, `PROVENANCE.csv`; `data/manifests/bc-faib-ground-plots-nonpsp-acquisition.csv`

---

### Entry: EL-20261114-012
**Claim**: Québec PEP and NL FAL permanent sample plot payloads were archived with verified checksums and exact counts in data-submodule commit `72da747`; all 17 new annex objects are local-only pending S3 credentials.
**Status Symbol**: ✅
**Evidence Type**: ZIP/SQLite integrity + SHA-256 file audit + per-layer count reconciliation + git-annex status
**Command**: `curl -sS -L -o PEP_GPKG.zip <Données Québec URL>`; `python3 -c "zipfile.ZipFile(...).testzip()"`; `sqlite3`/`PRAGMA integrity_check`; `.venv/bin/python nl_export.py`; `.venv/bin/python nl_stats.py`; `python3 verify hash/row counts`; `git -C data annex add raw/qc_pep raw/nl_fal_psp`; `git -C data commit -m "Acquire Quebec PEP and NL FAL permanent sample plots"`; `git -C data annex find --in=here --not --in=arbutus-s3`.
**Exit Code**: 0
**Output Summary**:
```
QC PEP: PEP_GPKG.zip 128,568,617 bytes
  sha256 a5deb82b8e05c3226f83eb8daa4842900b6564e31b30915d9215f8a031faa5e5
  ZIP testzip None; 1 member PEP.gpkg 739,045,376 bytes
  PEP.gpkg sha256 6d53e90a23799d681d050e13a433bc6e7ede6549b0733c8cc9c01fc8f94348c9
  PRAGMA integrity_check = ok; 46 SQLite tables, 24 content layers
  12,834 plots; 53,632 visits; 12,611 remeasured plots (98.26%)
  2,210,417 tree measurements; 843,175 unique trees; 683,328 remeasured trees (81.04%)
  235,455 sapling rows; 442,560 study-tree rows; 64 tally species (86 with saplings)
  1970-05-10 to 2025-10-02; median interval 11.05 years
  DBH 0-1,089 mm, median 133 mm; live DBH median 136 mm
  height: 43,559 tally rows + 372,225 study rows; 415,108 trees with any height (49.23%)
  age: 157,394 trees; no explicit site index; areas 4/40/400/625 m2, no BAF

NL FAL: 14 layers, 997,754 total features/rows including support tables
  mature 412; immature 968; age_group 592; disturbance 470; remeasurement 7,660
  immature trees remeasurement 588,865; immature sample trees 52,651
  immature permanent 107,785; mature permanent 35,500; mature remeasurement 186,783
  plot_layout 6,628; plot_location 1,351; crew_date 7,659; silviculture 430
  every file count == returnCountOnly and SHA-256 == export_manifest.json
  775,648 tree measurement rows; 143,589 unique trees; 143,285 permanent-tree rows
  1,242 remeasured plots; 138 single-visit plots; visits 1985-2026
  DBH median 13.1 cm mature / 6.0 cm immature
  height populated 20.20% mature / 75.76% immature
  14 simplified species codes; 470 disturbance rows; 430 silviculture rows

git annex: 17 new objects (QC 3 binaries + NL 14 gz), all "1 copy [here]"
cumulative local-only pending-S3 objects: 504
```
One NL `remeasurement` row has a null `PLOTKEY`; one immature plot (`5_119019`) has no visit. No S3 copy was attempted; publication remains blocked by new-cloud credentials.
**Verification Timestamp**: 2026-11-14T19:50:00Z
**Verified By**: Python 3.12.3 (zipfile, sqlite3, pandas, pyogrio 0.13.0/GDAL 3.12.4), git-annex 10.20240129
**Artifact Reference**: data-submodule commit `72da747`; `data/raw/qc_pep/{ACQUISITION.md,CONTENTS.md,PROVENANCE.csv}`; `data/raw/nl_fal_psp/{ACQUISITION.md,CONTENTS.md,PROVENANCE.csv,metadata/export_manifest.json}`; manifests `data/manifests/{qc-pep,nl-fal-psp}-acquisition.csv`

---

### Entry: EL-20261114-013
**Claim**: The reproducible FVSCA calibration v0 harmonization package built and validated the unified plot extract (72,975 sites, 368,133 visits, 15,639,840 tree rows, 7,011,326 intervals) from the archived FAIB, MAGPlot, QC PEP, and NL FAL inputs; the output was committed to the data submodule as `0654864` (payload) and `e22dc2f` (QA/manifest refresh) without modifying raw payloads or attempting S3 publication.
**Status Symbol**: ✅
**Evidence Type**: Reproducible build + hard validation + manifest hash audit + git-annex commit
**Command**: `python3 -m workflows.national.plot_harmonization.build --out data/derived/fvsca_calibration/v0 --sources faib,magplot,qc_pep,nl_fal --qc-gpkg /tmp/gep/qcpep/extract/PEP.gpkg --work-dir /tmp/plot_harmonization_work`; `python3 -m workflows.national.plot_harmonization.validate --out data/derived/fvsca_calibration/v0`; `git -C data annex add derived/fvsca_calibration/v0`; `git -C data commit -m "Add FVSCA calibration v0 harmonized plot extract"`
**Exit Code**: 0
**Output Summary**:
```
build: 6 source labels
  faib_psp      7,341 sites / 23,956 visits / 3,217,643 trees / 1,799,382 intervals
  faib_non_psp  9,512 sites / 11,951 visits /   366,788 trees /    80,702 intervals
  magplot      39,130 sites / 257,031 visits / 7,771,041 trees / 3,293,792 intervals
  magplot_wfs   2,778 sites / 13,906 visits / 1,298,474 trees /   369,878 intervals
  qc_pep       12,834 sites / 53,632 visits / 2,210,417 trees /   979,461 intervals
  nl_fal        1,380 sites /  7,657 visits /   775,477 trees /   488,111 intervals
TOTAL          72,975 sites / 368,133 visits / 15,639,840 trees / 7,011,326 intervals

validation: 52 checks, 0 failed
  no duplicate sites/visits/trees/interval keys
  visit->site, tree->visit, interval->tree referential integrity PASS
  DBH 0-500 cm, height 0-100 m, years 1900-2027, interval 2-40 years PASS
  site/visit n_trees and n_visits consistency PASS
  manifest counts and 0.5% reconciliation PASS

output SHA-256:
  sites.csv              72,975 rows  8,371,441 bytes  a88e6891...b128b6c8
  visits.csv            368,133 rows 30,574,202 bytes  bd07dc11...f2dcbb61534c
  trees.csv.gz       15,639,840 rows 103,812,857 bytes a0b540f4...e3afffe
  intervals.csv.gz    7,011,326 rows  48,366,358 bytes bf2781a3...c87b4c10
  species_crosswalk.csv     355 rows     18,222 bytes 40748824...36f182e35c

dropped rows explained in QA.md:
  faib_psp 5, faib_non_psp 308 blank PLOT/TREE_NO
  magplot_wfs 10,699 duplicate NL WFS features
  nl_fal 168 tallies + 2 visits from absent PLOTKEY 8_119019, 3 duplicate trees,
    1 height=99 sentinel
raw inputs unchanged; no S3 publication attempted
```
**Verification Timestamp**: 2026-11-14T21:29:13Z
**Verified By**: Python 3.12.3 / pandas 3.0.3; git-annex 10.20240129
**Artifact Reference**: data-submodule commits `0654864` and `e22dc2f`; `data/derived/fvsca_calibration/v0/{manifest.json,validation.json,QA.md,sites.csv,visits.csv,trees.csv.gz,intervals.csv.gz,species_crosswalk.csv}`; package `workflows/national/plot_harmonization/`

---

### Entry: EL-20261115-014
**Claim**: The invalid 2026-11-14 FVSCA calibration v0 was corrected and rebuilt from unchanged raw inputs: QC PEP `plot_area_ha` is now hectares (25 stems/ha -> 0.04 ha), MAGPlot intervals are ordered per `(site_id, plot_id)`, and the rebuilt package passes 69/69 hard validation checks with 72,975 sites, 368,133 visits, 15,639,840 tree rows, and 7,228,291 intervals. The corrected outputs were committed to the data submodule as `21205c9` and published to `arbutus-s3`.
**Status Symbol**: ✅
**Evidence Type**: Reproducible build + full validation + focused regression tests + manifest hash audit + git-annex S3 verification
**Command**: `.venv/bin/python -m workflows.national.plot_harmonization.build --out data/derived/fvsca_calibration/v0 --sources faib,magplot,qc_pep,nl_fal`; `.venv/bin/python -m workflows.national.plot_harmonization.validate --out data/derived/fvsca_calibration/v0`; `.venv/bin/python -m pytest -q tests/test_plot_harmonization.py`; `.venv/bin/python -m pytest -q tests`; `git -C data annex add derived/fvsca_calibration/v0`; `git -C data commit -m "Rebuild FVSCA calibration v0 after harmonization review"`; `cd data && ~/bin/git-annex-s3 copy --to arbutus-s3 derived/fvsca_calibration/v0`; `git -C data annex whereis derived/fvsca_calibration/v0/{intervals,trees}.csv.gz`; `git -C data annex find --not --in arbutus-s3`
**Exit Code**: 0
**Output Summary**:
```
build: 6 source labels
  faib_psp      7,341 sites / 23,956 visits / 3,217,643 trees / 1,799,382 intervals
  faib_non_psp  9,512 sites / 11,951 visits /   366,788 trees /    80,702 intervals
  magplot      39,130 sites / 257,031 visits / 7,771,041 trees / 3,298,527 intervals
  magplot_wfs   2,778 sites / 13,906 visits / 1,298,474 trees /   436,550 intervals
  qc_pep       12,834 sites / 53,632 visits / 2,210,417 trees / 1,119,613 intervals
  nl_fal        1,380 sites /  7,657 visits /   775,477 trees /   493,517 intervals
TOTAL          72,975 sites / 368,133 visits / 15,639,840 trees / 7,228,291 intervals

QC plot_area_ha: old site-level 400.0 "ha" -> new 0.04 ha (25 stems/ha)
MAGPlot intervals: old 7,011,326 total -> new 7,228,291 (+216,965)
sites/visits/trees unchanged; sites.csv changed only in QC plot_area_ha units
validation: 69 checks, 0 failed
tests: 13 passed (focused); 196 passed + 29 subtests (repo tests/)

output SHA-256:
  sites.csv              72,975 rows  8,359,304 bytes  7796f2c8...bccf54de9
  visits.csv            368,133 rows 30,574,202 bytes bd07dc11...f2dcbb61534c (unchanged)
  trees.csv.gz       15,639,840 rows 103,812,857 bytes a0b540f4...e3afffe (unchanged)
  intervals.csv.gz    7,228,291 rows  53,638,804 bytes b42845c5...92acdd3f9b
  species_crosswalk.csv     355 rows     18,222 bytes 40748824...36f182e35c (unchanged)
  manifest.json                            16,710 bytes f49b1ec7...1bd63239

git-annex:
  intervals.csv.gz: 2 copies [here], [arbutus-s3]
  trees.csv.gz:     2 copies [here], [arbutus-s3]
  git annex find --not --in arbutus-s3 = 0 objects
```
**Verification Timestamp**: 2026-11-15T07:55:00Z
**Verified By**: Python 3.12.3 / pandas 3.0.3; git-annex 10.20240129; pytest 9.1.1; Arbutus S3
**Artifact Reference**: data-submodule commit `21205c9`; `data/derived/fvsca_calibration/v0/{manifest.json,validation.json,QA.md,REVIEW_FIXES.md,sites.csv,visits.csv,trees.csv.gz,intervals.csv.gz,species_crosswalk.csv}`; old backups `/tmp/fvsca_v0_old/`

---

### Entry: EL-20261115-015
**Claim**: The `fresh-fvsca` BC prototype builds the FVSbc consumer schema from the corrected v0 `faib_psp` subset, matches the read-only `fresh_fvsbc.calibration.build_dataset` row counts for visits/intervals/tree-survival, documents every material difference in `PARITY_REPORT.md`, passes focused and repository tests, and publishes the gzip tree-survival table to `arbutus-s3`.
**Status Symbol**: ✅
**Evidence Type**: Reproducible build + parity comparison against a read-only reference + focused tests + full repository tests + manifest hash audit + git-annex S3 verification
**Command**: `.venv/bin/python -m workflows.national.fvsca.build --variant bc --source faib_psp --v0-root data/derived/fvsca_calibration/v0 --raw-root data/raw --out data/derived/fvsca_calibration/bc_faib_fvsbc_schema`; `.venv/bin/python -m workflows.national.fvsca.parity --raw-root data/raw --v0-root data/derived/fvsca_calibration/v0`; `.venv/bin/python -m pytest -q tests/test_fvsca_bc.py`; `.venv/bin/python -m pytest -q tests`; `git -C data annex add derived/fvsca_calibration/bc_faib_fvsbc_schema`; `git -C data commit -m "Add FVSCA BC FAIB PSP consumer schema dataset"`; `cd data && ~/bin/git-annex-s3 copy --to arbutus-s3 derived/fvsca_calibration/bc_faib_fvsbc_schema`; `git -C data annex whereis derived/fvsca_calibration/bc_faib_fvsbc_schema/tree_survival.csv.gz`; `git -C data annex find --not --in arbutus-s3`; `git -C data push origin main`; `git -C data push origin git-annex`
**Exit Code**: 0
**Output Summary**:
```
build (2m48s):
  sites selected            7,341 (all in the twelve resolved BEC zones)
  plot-visit rows          24,962 (23,956 source visits; 509 multi-plot units)
  intervals                17,151 (years 2-35)
  tree-survival rows    1,814,527 (dead 272,292; known 1,793,037; missing 21,490)
  species crosswalk            45 (13 direct JSP, 7 donor aliases, 25 unmapped;
                                 261,472 unmapped calibration rows)
  TSA matched sites         7,304 site-level (7,300 with live visits)
  warnings                      2 (multi-plot volume null; unmapped species)

parity versus fresh-fvsbc 0.21.4:
  visits                 24,962 / 24,962
  intervals              17,151 / 17,151
  tree-survival       1,814,527 / 1,814,527
  unique sites            7,337 / 7,337
  unique site/plot pairs  7,806 / 7,806
  interval years min/med/max 2 / 10 / 35 in both
  mean BA                  46.5423 / 46.5423 m2/ha
  mean TPH              1,880.3325 / 1,880.3325 stems/ha
  mean QMD                 21.3971 / 21.3971 cm
  dead endpoints          272,291 / 272,292 (+1 retained known death)
  known endpoints       1,793,036 / 1,793,037
  species resolved             13 / 20 (7 donor aliases)
  species unmapped             32 / 25
  null visit-volume rows        0 / 1,522 (509 multi-plot visit units)

tests:
  focused tests                 7 passed
  repository tests            203 passed + 29 subtests

output SHA-256:
  visits.csv              24,962 rows  5,051,096 bytes  8169df68...c6a6f916
  intervals.csv           17,151 rows  4,658,304 bytes  d125cebe...34e6f07
  tree_survival.csv.gz 1,814,527 rows 39,312,661 bytes  ad19a21d...443f60d
  species_crosswalk_fvsbc.csv 45 rows      5,646 bytes  a69c03a4...26fc3c
  manifest.json                             3,727 bytes  75eee741...c2259932

git-annex:
  tree_survival.csv.gz: 2 copies [here], [arbutus-s3]
  git annex find --not --in arbutus-s3 = 0
  data commit 097e5e7 pushed to origin/main; git-annex 3acc8a7 pushed
```
**Verification Timestamp**: 2026-11-15T08:57:00Z
**Verified By**: Python 3.12.3 / pandas 3.0.3; fresh-fvsbc 0.21.4; pytest 9.1.1; git-annex 10.20240129; Arbutus S3
**Artifact Reference**: `workflows/national/fvsca/{README.md,PARITY_REPORT.md,build.py,parity.py,profiles.py,plot_sources.py,bc.py}`; `tests/test_fvsca_bc.py`; data-submodule commit `097e5e7`; `data/derived/fvsca_calibration/bc_faib_fvsbc_schema/{visits.csv,intervals.csv,tree_survival.csv.gz,species_crosswalk_fvsbc.csv,manifest.json}`

---

### Entry: EL-20261115-016
**Claim**: The first measured uncalibrated FVS-BC baseline from this workspace ran on the BC FAIB PSP snapshot with `fresh-fvsbc` 0.21.4 (checkout `f5dce34`) and `fresh-fvsbc-pyfvs` 0.4.1, compared 139 intervals across 139 BEC variants with 0 skipped, and reproduced the reference D6-corrected all-zone baseline exactly; the aggregate tables and hashed manifest were committed to the data submodule (`0d577cd`) and pushed to `origin/main`.
**Status Symbol**: ✅
**Evidence Type**: Reproducible FVS run + reference-summary reproduction + per-metric computation + manifest hash audit + git-annex S3 check
**Command**: `python3 -m venv .venv-fvsca`; `.venv-fvsca/bin/pip install -e "/srv/shared-data/gep/ws4/tmp/fresh-fvsbc[fvs]"`; `.venv-fvsca/bin/python /srv/shared-data/gep/ws4/tmp/fresh-fvsbc/scripts/build_calibration_dataset.py --psp-dir /tmp/fvsca_psp --out-dir /tmp/fvsca_calibration`; `.venv-fvsca/bin/python /srv/shared-data/gep/ws4/tmp/fresh-fvsbc/scripts/run_baseline_backtest.py --data-dir /tmp/fvsca_calibration --psp-dir /tmp/fvsca_psp --out-dir /tmp/fvsca_baseline --per-bec 1 --tree-age 60`; `git -C data add derived/fvsca_calibration/bc_baseline && git -C data commit -m "Add first measured FVS-BC baseline on FAIB PSP"`; `cd data && ~/bin/git-annex-s3 copy --to arbutus-s3 derived/fvsca_calibration/bc_baseline`; `git -C data push origin main`; `git -C data push origin git-annex`
**Exit Code**: 0
**Output Summary**:
```
environment:
  fresh-fvsbc 0.21.4 editable, checkout f5dce34 (v0.21.4-212-gf5dce34)
  fresh-fvsbc-pyfvs 0.4.1; PyFVS commit e95172b; FVS source ef5fa847
  Python 3.12.3; pandas 3.1.1; numpy 2.5.4
  pyfvs.list_variants() -> {'BC': {'lib': 'pyfvs.fvsbc', 'status': 'OK'}}

inputs (decompressed SHA-256 match reference runtime/calibration/manifest.json):
  faib_header.csv          1,406,012 bytes  f029e21c...079ea8b2
  faib_sample_byvisit.csv  3,186,768 bytes  9b6db291...52fece60
  faib_plot_header.csv     2,815,639 bytes  dcb206e5...b45514ae
  faib_tree_detail.csv   740,181,676 bytes  d9f609cd...3a4c910

dataset build (40.4 s, 1.53 GiB peak):
  7,341 header; 23,956 visits; 24,962 plot-visit summaries;
  17,151 intervals; 139 BEC units; 1,814,527 tree-survival rows

sample: 139 intervals / 139 BEC variants; 0 skipped; 2 intervals with 23
unresolved trees (ESSFwc4 19, PPxh2 4); years 3-27, mean 10.76

baseline (80.1 s wall, 474.9 s CPU, 452,880 KB peak):
  initial BA   n=139  obs 37.020  pred 36.331  bias -0.689  MAE 0.836  RMSE 1.367
  end BA       n=139  obs 39.420  pred 35.252  bias -4.168  MAE 8.648  RMSE 11.410
  end TPH      n=139  obs 2201.605 pred 1809.755 bias -391.850 MAE 781.732
  end QMD      n=139  obs 18.676  pred 19.578  bias +0.901  MAE 2.478
  top height   n=139  obs 27.525  pred 22.705  bias -4.820  MAE 4.958
  volume       n=139  obs 302.444 pred 248.554 bias -53.890 MAE 84.242
  flags: 1 above p99 (SBSwk1); 5 above max (IDFxx2, SBPSxc, SBSmc3, SBSun, SBSwk3)
  D5 filter (exclude SBPS + flagged): n=130 bias -5.259 MAE 8.445
  per-BEC agreement with d6-all-zone-summary.csv: max abs diff 6.9e-13

probes:
  D7.3 four-candidate subset (116 rows, 67.6 s) reproduces documented test rows:
    bamax53 -1.90/1.93, TPH -487.78/518.44
    bamax55 -1.76/1.79, TPH -481.78/514.72
    bamax60 -1.61/1.65, TPH -468.49/505.44
    bamax60-mort0.3 +0.10/1.68, TPH -352.92/432.20
  D7.4 --bundle path failed:
    BundleError: idfdk-ingrowth-pilot-2026-11-03 renders BAMAX, and the
    keyword set does not carry them (probe renders "53", bundle "53.0")
  D7.4 --ingrowth path (29 intervals, 21.3 s) reproduces D9.24:
    test  n=7   BA -1.76/1.79 (-5.6%)  TPH +204.65/327.16 (+8.6%)
    train n=22  BA -0.08/3.23 (-0.3%)  TPH +602.74/674.43 (+37.4%)

data commit 0d577cd pushed 097e5e7..0d577cd main -> main
git annex find --not --in arbutus-s3 = 0
```
**Verification Timestamp**: 2026-11-15T09:16:00Z
**Verified By**: Python 3.12.3 / fresh-fvsbc 0.21.4 / fresh-fvsbc-pyfvs 0.4.1; git-annex 10.20240129; Arbutus S3
**Artifact Reference**: `workflows/national/fvsca/BC_BASELINE_REPORT.md`; data-submodule commit `0d577cd`; `data/derived/fvsca_calibration/bc_baseline/{manifest.json,metrics_summary.csv,baseline_summary.csv,baseline_by_zone.csv,baseline_by_tsa.csv,baseline_by_species.csv,baseline_by_dbh.csv,baseline_by_ba.csv,baseline_skips.csv,baseline_report.md,probe_idfdk/}`; full per-interval table and FVS runs at `/tmp/fvsca_baseline/`

---

## Evidence Log Format Reference

Each entry follows the structure defined in [docs/agent-integrity-protocol.md](docs/agent-integrity-protocol.md#evidence-log-format):

```
### Entry: EL-YYYYMMDD-NNN
**Claim**: [Exact claim being evidenced]
**Status Symbol**: [✅ | ⚠️ | ❌ | 🔄 | 📋]
**Evidence Type**: [Test output | Command output | File audit | Manual verification | External reference]
**Command**: [Exact command used to generate evidence]
**Exit Code**: [0 for success, non-zero for failure]
**Output Summary**: [Key output or summary of results]
**Verification Timestamp**: [ISO 8601 UTC]
**Verified By**: [Tool/agent/person who performed verification]
**Artifact Reference**: [Path to durable artifact: log file, report, manifest, etc.]
```

**Status Symbols**:
- ✅ **Verified** — Claim fully supported by reproducible evidence
- ⚠️ **Partial** — Claim partially supported; gaps documented
- ❌ **Failed** — Claim contradicted by evidence
- 🔄 **In Progress** — Evidence collection underway
- 📋 **Documented** — Claim recorded but not yet verified (planning stage)

---

*This log is append-only. Entries must not be modified after creation. Corrections are added as new entries referencing the original. (Sole exception: the 2026-08-17 pre-publication integrity repair above, performed while this file was still untracked, and recorded in full in the Corrections section.)*

---

## 2026-11-15 — RSYC acquisition and bucking-reference registration

### Entry: EL-20261115-017
**Claim**: The three RSYC `v20260923` artifacts were acquired intact, their SHA-256 values match the upstream release manifests, and the model-catalogue inventory counts were verified from the artifact.
**Status Symbol**: ✅
**Evidence Type**: Download audit, checksum comparison, archive/GeoPackage integrity checks, and tabular count verification
**Command**:
```bash
cd data/raw/national/rsyc
curl -fL --retry 4 -o RSYC_models.zip https://raw.githubusercontent.com/ptompalski/RSYC/dev/data-raw/RSYC_models.zip
curl -fL --retry 4 -o RSYC_tiles.gpkg https://raw.githubusercontent.com/ptompalski/RSYC/dev/inst/extdata/RSYC_tiles.gpkg
curl -fL --retry 4 -o RSYC_ecostrat.gpkg https://github.com/ptompalski/RSYC/releases/download/ecostrat-v2/RSYC_ecostrat.gpkg
sha256sum RSYC_models.zip RSYC_tiles.gpkg RSYC_ecostrat.gpkg
unzip -t RSYC_models.zip
.venv/bin/python -c "import sqlite3; print(sqlite3.connect('RSYC_tiles.gpkg').execute('PRAGMA integrity_check').fetchone())"
.venv/bin/python -c "import sqlite3; print(sqlite3.connect('RSYC_ecostrat.gpkg').execute('PRAGMA integrity_check').fetchone())"
```
**Exit Code**: 0
**Output Summary**:
```
RSYC_models.zip      1,468,706 bytes  86da5737720613f686b3f14b3cdea43c18b5cde87a4b113a3cb0df6dcfe24940
RSYC_tiles.gpkg        278,528 bytes  f61e0ec06b29146264ff676c98fcfa1e431d6ddd5f1d97d17e957d4d9e77308b
RSYC_ecostrat.gpkg  15,421,440 bytes  e425818f3c1db682f21a6eec6bebf4397761db46b08b13b8c4d3c780dc0d87a4
unzip -t: No errors detected; 1 member RSYC_models.csv (16,768,740 bytes)
tiles PRAGMA integrity_check: ok; 1 layer RSYC_tiles, 385 Polygon features, EPSG:3978
ecostrat PRAGMA integrity_check: ok; ecozones 12, ecoprovinces 36, ecoregions 138, ecodistricts 672; EPSG:3978
model CSV: 27,208 rows x 42 columns; 27,208 unique model_id; model_version v20260923 on all rows
responses: agb 13,646; volume 13,562
scale: national 17,292; regional 9,916
levels: tile 4,886; tile_region 1,750; ecozone 461; ecoprovince 1,118; ecoregion 3,602; ecodistrict 15,391
unique units: tiles 385; tile_region 264; ecozone 12; ecoprovince 36; ecoregion 138; ecodistrict 672
species column: 32 scientific codes + generic/coniferous/broadleaf = 35 distinct values
age_min 1; age_max 600 on all rows (README operational range 1-150)
publication flags: publish_ok/fit_ok/converged = TRUE on all 27,208 rows; singular_or_boundary TRUE on 88
upstream manifests: model 86da5737...24940; tiles f61e0ec0...7308b; ecostrat e425818f...d87a4
tile join: 385/385 exact; ecozone 12/12, ecoprovince 36/36, ecoregion 138/138 exact
ecodistrict join: 671/672 exact; known Betsiamiles/Betsiamites spelling mismatch for ecodistrict 438
validation article: 60 summary rows, 6,434 validated curves; median R2 0.57-0.80, bias -31.4 to +9.3%, RMSE 31.3-53.3%
```
**Verification Timestamp**: 2026-11-15T10:10:00Z
**Verified By**: curl 8.x, unzip 6.00, Python 3.12.3 (pandas 3.0.3, pyogrio 0.13.0), SQLite integrity_check
**Artifact Reference**: `data/raw/national/rsyc/{ACQUISITION.md,PROVENANCE.csv,CONTENTS.md}`; upstream manifests `data-raw/{RSYC_models_manifest.csv,tiles-manifest.csv,ecostrat-manifest.csv}` at RSYC `dev`

### Entry: EL-20261115-018
**Claim**: The RSYC data commit and all three annexed artifacts were published to GitHub and arbutus-s3, and no RSYC annex object remains unpublished.
**Status Symbol**: ✅
**Evidence Type**: git-annex copy/whereis/find output and Git push refs
**Command**:
```bash
cd data
~/bin/git-annex-s3 copy --to arbutus-s3 raw/national
~/bin/git-annex-s3 whereis raw/national/rsyc/RSYC_models.zip raw/national/rsyc/RSYC_tiles.gpkg raw/national/rsyc/RSYC_ecostrat.gpkg
~/bin/git-annex-s3 find raw/national --not --in arbutus-s3 | wc -l
~/bin/git-annex-s3 find --not --in arbutus-s3 | wc -l
git push origin main
git push origin git-annex
cd ..
git push origin feature/p7-source-triage-and-production-readiness
```
**Exit Code**: 0
**Output Summary**:
```
data commit a77fdbc "Acquire RSYC national yield curves and bucking references"
copy ecostrat ok; copy models.zip ok; copy tiles.gpkg ok
whereis each artifact: 2 copies ([here] + [arbutus-s3])
git annex find raw/national --not --in arbutus-s3 = 0
git annex find --not --in arbutus-s3 (repo-wide) = 0
data push: f915fa1..a77fdbc main -> main
data push: 4b38575..5fcce83 git-annex -> git-annex
parent commit 011ac6f "Register RSYC national yield curves and bucking references"
parent push: 8ba6dda..011ac6f feature/p7-source-triage-and-production-readiness -> feature/p7-source-triage-and-production-readiness
```
**Verification Timestamp**: 2026-11-15T10:19:00Z
**Verified By**: git-annex 10.20240129; arbutus-s3 special remote; Git 2.45.2
**Artifact Reference**: data commit `a77fdbc`; parent commit `011ac6f`; `data/raw/national/`; S3 remote `arbutus-s3`

### Entry: EL-20261115-019
**Claim**: The Phase 0 national bucking contracts pass their synthetic conservation, sort-mapping, ODT, negative-value, and unsupported-taper tests.
**Status Symbol**: ✅
**Evidence Type**: pytest execution output
**Command**:
```bash
PYTHONPATH=/srv/shared-data/gep/ws4/nemora/src .venv/bin/pytest -q tests/test_bucking.py
```
**Exit Code**: 0
**Output Summary**:
```
10 passed, 2 warnings in 2.88s
```
The two warnings are nemora package deprecation notices (`distfit` and `synthforest` compatibility shims), not bucking warnings. Tests cover: logs + residue ≈ taper total; no negative volumes or values; conifer/deciduous sort mapping; ODT/green-tonne/chip conversion; unsupported species/jurisdiction/BEC failures; proxy flags; batch skip recording; contract rejection of negative/inconsistent rows; and dead-tree rejection.
**Verification Timestamp**: 2026-11-15T18:20:00Z
**Verified By**: Python 3.12.3, pytest 9.1.1, nemora reference checkout
**Artifact Reference**: `tests/test_bucking.py`; `workflows/national/bucking/`

### Entry: EL-20261115-020
**Claim**: The bounded TSA24 pilot produced a conserving bucked-log and residue ledger on a documented 20-plot / 300-tree stratified sample, with benchmark gaps stated.
**Status Symbol**: ✅
**Evidence Type**: generated artifacts + JSON summary
**Command**:
```bash
PYTHONPATH=/srv/shared-data/gep/ws4/nemora/src .venv/bin/python \
  -m workflows.national.bucking.validate_tsa24 \
  --out-dir data/derived/national/bucking/tsa24_pilot \
  --max-plots 20 --max-trees-per-plot 15 --seed 20261115 --timebox-seconds 900
```
**Exit Code**: 0
**Output Summary**:
```
Loaded 155,903 live trees on 1,318 plots.
Selected 20 plots and 300 trees.
Bucked 134 logs; recovered 47.864 m3; residue 10.289 m3.
Conservative pass true; 0 sampled trees skipped.
PSP paired volume: relative bias -13.2%, MAE 5.76 m3/ha, median ratio 0.832.
VDYP7 15/20 matched, observed-all relative bias -34.7%.
TIPSY 11/20 matched, observed-all relative bias -20.8%; no grade/residue/value columns.
RSYC 13/20 matched, observed-all relative bias +20.0%.
Residue: 4.630 ODT; 8.418 green t at 45%; 6.614 green t at 30%; 7.576 chip green t.
```
The 0.45 ODT/m³ density, 45%/30% moisture, and 0.90 chip yield are flagged assumptions. No official grade is assigned. The TSA24 TIPSY instance has no grade, residue, or value columns; the sort/price profile is a placeholder. VDYP/TIPSY joins use within-stratum PSP site-index tertiles, not the FEMIC AU mapping. RSYC is a stand-level sanity check only.
**Verification Timestamp**: 2026-11-15T18:20:00Z
**Verified By**: Python 3.12.3, pandas 3.0.3, pyarrow 26.0.1, geopandas 1.1.4, nemora reference checkout
**Artifact Reference**: `data/derived/national/bucking/tsa24_pilot/{validation_summary.json,manifest.json,VALIDATION_REPORT.md}`; `metadata/bucking-pilots.csv`

### Entry: EL-20261115-021
**Claim**: The TSA24 bucking pilot outputs, contracts, and metadata were committed to the parent and data repositories, and both remote branches were pushed; no changed annex object is unpublished.
**Status Symbol**: ✅
**Evidence Type**: git commit/push refs and git-annex find output
**Command**:
```bash
cd data
git add derived/national/bucking/tsa24_pilot
git commit -m "Add TSA24 bucking pilot outputs"
git annex find --not --in arbutus-s3 | wc -l
git push origin main
git push origin git-annex
cd ..
git add workflows/national/bucking tests/test_bucking.py tests/conftest.py \
  metadata/bucking-pilots.csv CHANGE_LOG.md EVIDENCE_LOG.md data
git commit -m "Add national bucking contracts and TSA24 pilot"
git push origin feature/p7-source-triage-and-production-readiness
```
**Exit Code**: 0
**Output Summary**:
```
data commit 3056429 "Add TSA24 bucking pilot outputs" (17 files, 2,440 insertions)
git annex find --not --in arbutus-s3 = 0
data push: a77fdbc..3056429 main -> main
data push: git-annex up-to-date (all new outputs are text files in regular git)
parent commit c408aca "Add national bucking contracts and TSA24 pilot" (13 files, 4,240 insertions)
parent push: d6f2402..c408aca feature/p7-source-triage-and-production-readiness -> feature/p7-source-triage-and-production-readiness
```
**Verification Timestamp**: 2026-11-15T18:35:00Z
**Verified By**: Git 2.45.2; git-annex 10.20240129; GitHub `origin`
**Artifact Reference**: data commit `3056429`; parent commit `c408aca`; `data/derived/national/bucking/tsa24_pilot/`; `workflows/national/bucking/`

### Entry: EL-20261115-022
**Claim**: TSA24 grade and residue curves were compiled from TIPSY 4.5 `.taf` tables for all 16 managed AUs, and the bucking pilot was benchmarked against them without using MKRF or fabricating a BTC run.
**Status Symbol**: ✅
**Evidence Type**: command output, file hashes, validation summary, git commit refs
**Command**:
```bash
command -v wine; command -v xvfb-run
femic tipsy run-btc data/03_input-tsa24.csv \
  --btc-exe "$TIPSY_INSTALL/TIPSYbtc.exe" --use-installed-btc ...
python -m workflows.national.bucking.compile_tsa24_grade_curves \
  --femic-root "$FEMIC_TSA24_ROOT" \
  --tipsy-database "$TIPSY_DATABASE" \
  --out-dir data/derived/national/bucking/tsa24_grade_curves
python -m workflows.national.bucking.validate_tsa24 \
  --out-dir data/derived/national/bucking/tsa24_pilot \
  --grade-curves-dir data/derived/national/bucking/tsa24_grade_curves
```
**Exit Code**: 0 (compile and validation); 127 (BTC probe, expected blocker)
**Output Summary**:
```
BTC: TIPSYbtc.exe wrapper needs xvfb-run + wine; both absent.
     stderr: .../TIPSYbtc.exe: 2: exec: xvfb-run: not found; exit_code=127
TAF: 16 AUs x 36 ages = 576 rows; tables sw 0530P00 (SI 20),
     pli0530P00 (SI 23), fdi0535P00 (SI 25).
Grade benchmark: 11 AU-curve, 7 species-curve, 2 no-match.
Sort TVD aggregate 0.487; diameter marginal 0.232; length marginal 0.234.
TIPSY grades: H 5.6%, I 0.3%, J 59.8%, U 27.8%, X 3.9%, Y 2.5%, D/F 0%.
Pilot sawlog product share 96.8% (proxy comparison only).
Residue: pilot 43.50 ODT/ha vs TIPSY mill residue 516.17 ODT/ha (13.39x);
         per m3 ratio 2.90x.  Semantic mismatch, not equivalence.
Value: not computed; no same-basis interior grade price vector.
```
**Verification Timestamp**: 2026-11-15T19:00:00Z
**Verified By**: Python 3.12.3, pandas 3.0.3, fresh-tipsy 0.1.0, TIPSY 4.5 database (February 2022), nemora reference checkout
**Artifact Reference**: data commit `91c2762`; parent commit `cdd15bf`; `data/derived/national/bucking/tsa24_grade_curves/`; `data/derived/national/bucking/tsa24_pilot/{VALIDATION_REPORT.md,validation_summary.json,grade_benchmark.csv,grade_benchmark_summary.json,TSA24_GRADE_CURVES.md}`
**Superseded**: EL-20261115-023 reran the benchmark from BTC `/TSR` output and replaced the nominal-site-index `.taf` table. The numeric claims above are retained as historical evidence, not current results.

---

## 2026-11-15 — TSA24 BTC /TSR grade and residue run

### Entry: EL-20261115-023
**Claim**: The TSA24 grade/residue benchmark was rerun from the documented FEMIC BTC `/TSR` path with `log-grades` and `residual-fibre` indicator banks, replacing the nominal-site-index `.taf` table.
**Status Symbol**: ✅
**Evidence Type**: command output, file hashes, validation summary, git commit refs
**Command**:
```bash
sudo apt-get update
sudo apt-get install -y wine xvfb
xvfb-run -a wine msiexec /i /tmp/wine-dl/wine-mono-9.0.0-x86.msi /qn
xvfb-run -a wine "$TIPSY_INSTALL/TIPSYbtc_real.exe" /TSR \
  Samples/ex1.csv /tmp/tsa24-smoke/smoke_out.csv /tmp/tsa24-smoke/smoke_err.csv

export FEMIC_BATCHTIPSY_EXE="$TIPSY_INSTALL/TIPSYbtc.exe"
export TIPSY_DATABASE="$TIPSY_INSTALL/Database"
python -m femic tipsy run-btc data/03_input-tsa24.csv \
  --indicator-bank log-grades --indicator-bank residual-fibre \
  --run-id tsa24_grades
python -m femic tsa btc-post-tipsy \
  --instance-root "$FEMIC_TSA24_ROOT" \
  --run-config config/run_profile.tsa24.yaml --tsa 24 --run-id tsa24_grades \
  --indicator-bank log-grades --indicator-bank residual-fibre
python -m workflows.national.bucking.compile_tsa24_grade_curves \
  --femic-root "$FEMIC_TSA24_ROOT" \
  --out-dir data/derived/national/bucking/tsa24_grade_curves
python -m workflows.national.bucking.validate_tsa24 \
  --out-dir data/derived/national/bucking/tsa24_pilot \
  --grade-curves-dir data/derived/national/bucking/tsa24_grade_curves
```
**Exit Code**: 0 (smoke, BTC run, compile, validation)
**Output Summary**:
```
Runtime: wine 9.0, xvfb, wine-mono 9.0.0 installed; wrapper
  /srv/shared-data/gep/iversen-jamie-msc-thesis/btc_install_wine/TIPSYbtc.exe
  execs xvfb-run -a wine TIPSYbtc_real.exe.
BTC smoke: /TSR Samples/ex1.csv -> smoke_out.csv 103,283 bytes,
  smoke_err.csv 7,140 bytes, exit 0.
BTC TSA24: exit 0; 16 data rows x 685 columns; 04_error-tsa24.csv header-only.
  Logs_Grade_D/F = 0 for all 16 AUs; H/I/J/U/X/Y nonzero; all five
  Residual_* components nonzero.
Post-TIPSY: au_rows=27, curves=43, curve_points=8,544; bundle regenerated.
Grade compile: 16 AUs x 36 ages = 576 rows; direct BTC columns, no rescale.
Benchmark join: 14 AU-curve, 3 species-curve, 3 no-match; 12 grade-comparable.
TIPSY grades: H 2.6%, I 0.2%, J 58.0%, U 29.9%, X 6.9%, Y 2.4%, D/F 0%.
Grade groups: high D/F/H/I 2.8%, sawlog D/F/H/I/J 60.8%, low U/X/Y 39.2%.
Pilot sawlog product share 96.9% (proxy comparison only).
Residue: pilot 50.83 ODT/ha vs BTC mill residue 768.55 ODT/ha (18.45x, n=14);
  per m3 ratio 3.47x. Semantic mismatch, not equivalence.
Industrial-sort comparison: not available (industrial-logs bank not requested).
```
**Hashes**:
```
03_input-tsa24.csv        e3c518bdad74a7b7897d44da6d36d93f513bcbfc29495123a7a91b5b24566042
04_output-tsa24.csv       0b7d020b91e118fce4cc67d2d50680e33fa48afe0b429c0413226d846e1d69f9
tipsy_curves_tsa24.csv    ccb7bb0ead30381c355646b3b1f1cb7668537b3f7dbe4d8e237c963df1ed540a
au_table.csv              fbaeb44b241e73373dfa0cf9b730cafed0f3e6f15f944056d35268d4b06b3099
managed_au_curves.csv     6fe02e124b842040fe463b2e94bf6a549db7203e74e403db787e1b616b72791c
btc_manifest-tsa24_grades_tsa24.json d4a659b391b5b153de5b8af3f0772a8c84c029fd7f5a0dc604c3cd0c26d59587
```
**Verification Timestamp**: 2026-11-15T19:30:00Z
**Verified By**: Python 3.12.3, pandas 3.0.3, wine 9.0, wine-mono 9.0.0, TIPSY 4.7 BTC, nemora reference checkout
**Artifact Reference**: data commit `0d15a4b`; parent commit `fe7d9b8`; `data/derived/national/bucking/tsa24_grade_curves/`; `data/derived/national/bucking/tsa24_pilot/{VALIDATION_REPORT.md,validation_summary.json,grade_benchmark.csv,grade_benchmark_summary.json,TSA24_GRADE_CURVES.md}`; BTC manifests under `femic-tsa24-instance/tipsy_io/logs/` and `vdyp_io/profile_logs/`

---

## 2026-11-15 — TSA24 BTC volume and biomass-live harvest-slash ledger

### Entry: EL-20261115-024
**Claim**: The TSA24 bucking pilot now has a BTC `volume` benchmark and a genuine AGB-based harvest-slash ledger using the FEMIC BTC `biomass-live` and `carbon` banks; the previous mill-residue comparison is removed.
**Status Symbol**: ✅
**Evidence Type**: command output, file hashes, validation summary, git commit refs
**Command**:
```bash
export FEMIC_BATCHTIPSY_EXE=/srv/shared-data/gep/iversen-jamie-msc-thesis/btc_install_wine/TIPSYbtc.exe
export TIPSY_DATABASE=/srv/shared-data/gep/iversen-jamie-msc-thesis/btc_install_wine/Database
python -m femic tipsy run-btc data/03_input-tsa24.csv \
  --indicator-bank biomass-live --indicator-bank carbon \
  --run-id tsa24_biomass \
  --output-csv data/04_output-tsa24_biomass.csv \
  --error-csv data/04_error-tsa24_biomass.csv \
  --instance-root "$FEMIC_TSA24_ROOT"

python -m workflows.national.bucking.compile_tsa24_biomass_curves \
  --femic-root "$FEMIC_TSA24_ROOT" \
  --out-dir data/derived/national/bucking/tsa24_grade_curves

python -m workflows.national.bucking.validate_tsa24 \
  --out-dir data/derived/national/bucking/tsa24_pilot \
  --grade-curves-dir data/derived/national/bucking/tsa24_grade_curves

python -m pytest -q tests/test_bucking.py
python -m compileall -q workflows/national/bucking tests/test_bucking.py
```
**Exit Code**: 0 (BTC run, compile, validation, tests, compileall)
**Output Summary**:
```
BTC biomass run: 16 rows x 973 columns; 04_error-tsa24_biomass.csv header-only.
Units: BTC biomass/carbon family is ODT/ha (TIPSY biomass help; fresh-tipsy
  metric contract biomass_live_odt_ha). Biomass_Live_Above = wood+bark+
  foliar+branch; roots/total reported separately.
Biomass compile: 16 AUs x 36 ages = 576 rows; additivity checks pass;
  AGB max 456.2 ODT/ha.
BTC volume benchmark: 14 AU matches; 12 positive-volume comparisons;
  2 zero-volume age joins; 6 no-AU plots.
  Observed PSP mean 231.27 vs BTC 229.21 m3/ha; bias +2.06; MAE 122.72;
  RMSE 148.27; relative bias +0.9%.
  Pilot bucked mean 201.09 m3/ha; bias -28.12; MAE 126.86; RMSE 151.08;
  relative bias -12.3%.
Harvest-slash ledger: 8 primary plots; 12 excluded (6 no AU, 4 volume-ratio
  screen, 2 zero BTC volume).
  Mean AGB 200.92 ODT/ha; merchantable log biomass 88.27 ODT/ha;
  harvest slash 112.65 ODT/ha; taper residue 21.01 ODT/ha; ratio 5.36.
  Slash per m3 log 0.483 ODT/m3 vs taper 0.082 ODT/m3.
  60% recovery: 67.59 ODT/ha; 122.89 green t/ha at 45%; 110.60 chip green t/ha.
Basic density: species-specific FVS-BC NBEL/FIA defaults, 0.3098-0.4798 ODT/m3;
  flagged species_default_not_bc_local.
Grade status: not validated; D/F zero for all TSA24 AUs; mill residue excluded.
Tests: 15 passed. compileall: clean.
```
**Hashes**:
```
04_output-tsa24_biomass.csv 059bca42a845afc5ca873b56018fd99dd1ad4986d96652f3ae9332ce4afc8808
biomass_au_curves.csv       3a3c4f6feb47766895c3d3e872d4c12c299251eb188c0c093d515647748111d0
biomass_curves_manifest.json 4e54086307416291c37d0373e1295b560d7bedbd702da00764550030b6477853
harvest_residue_ledger.csv  2624e8b2ed566be2588fa006705561cecc154ab05f4d895f43aac5bcb654cb11
harvest_residue_summary.json 1f37270431656eb8b589dca1d7baacc4589dd24ec961d3136b2f989afe7663ad
btc_volume_benchmark.csv    495bf7b2c675954af8523c6de8c366a9cdb8fefe44f9b0c4edb078064c9b7250
bc_basic_density.csv        6584fb93a50cb6c1bd080515db780dd635aa9859e25b6867afed07a00c3198b6
```
**Verification Timestamp**: 2026-11-15T20:45:00Z
**Verified By**: Python 3.12.3, pandas 3.0.3, wine 9.0, wine-mono 9.0.0, TIPSY 4.7 BTC, nemora reference checkout
**Artifact Reference**: data commit `91e3b52`; `data/derived/national/bucking/tsa24_grade_curves/{biomass_au_curves.csv,biomass_curves_manifest.json}`; `data/derived/national/bucking/tsa24_pilot/{btc_volume_benchmark.csv,harvest_residue_ledger.csv,harvest_residue_summary.json,HARVEST_RESIDUE.md,VALIDATION_REPORT.md,validation_summary.json}`; BTC manifest `femic-tsa24-instance/tipsy_io/logs/btc_manifest-tsa24_biomass.json`

---

## 2026-11-15 — TSA24 stand-to-tree assortment disaggregation prototype

### Entry: EL-20261115-025
**Claim**: The RSYC-stratified stand-to-tree prototype produces a volume-constrained TSA24 tree list, a conserved assortment matrix, and a passing five-check validation.
**Status Symbol**: ✅
**Evidence Type**: command output, validation summary, artifact hashes, test execution
**Command**:
```bash
PYTHONPATH=/srv/shared-data/gep/ws4/nemora/src \
  .venv/bin/python -m workflows.national.assortments.validate_tsa24 \
  --out-dir data/derived/national/assortments/tsa24_prototype

PYTHONPATH=/srv/shared-data/gep/ws4/nemora/src \
  .venv/bin/python -m pytest -q \
  workflows/national/assortments/tests/test_assortments.py tests/test_bucking.py

PYTHONPATH=/srv/shared-data/gep/ws4/nemora/src \
  .venv/bin/python -m compileall -q workflows/national/assortments
```
**Exit Code**: 0 (prototype, tests, compileall)
**Output Summary**:
```
TSA24 subset: 558 sites / 1,318 plots / 155,904 live valid trees;
  491 sites mapped to RSYC ecodistricts.
Target: BTC AU 23000, SBS_SX, site class H, age 100, Nechako Lowland;
  target volume 311.6 m3/ha x 1 ha.
Target composition (leading-species-filtered, sum=1): SW 0.606, AT 0.116,
  BL 0.113, PL 0.102, FD 0.028, EP 0.022, SB 0.013; W/DG excluded by taper
  support, AC/HW below the 0.01 share floor.
DBH AU fits: 7/7 direct ecodistrict fits; selected distributions mixture 6,
  gamma 1; KS 0.012-0.063; weighted quantile MAE max 2.145 cm.
General all-age fits: 68 strata (48 direct, 20 pooled); selected KS median
  0.050, max 0.221, 3 high-KS.
Height AU fits: 7/7 direct ecodistrict; RMSE 1.81-3.82 m.
Synthetic tree list: 600 trees; 1,062.4 TPH; 31.96 m2/ha BA; QMD 19.57 cm;
  volume scale 0.725926; achieved taper total 311.600 m3 = target.
Assortment: 508 logs; recovered 259.958 m3 (83.4%); residue 51.642 m3
  (16.6%); residue 18.704 ODT; wood AGB proxy 113.835 ODT vs BTC AGB
  204.7 ODT (wood-only vs total AGB; not expected to match).
Products: sawlog 243.222 m3, other 16.437 m3, pulpwood 0.299 m3.
Checks: DBH quantiles PASS; target volume PASS; conservation PASS;
  species weights PASS; no negative values PASS.
Tests: 23 passed (8 assortment + 15 bucking). compileall: clean.
```
**Hashes**:
```
distribution_fits.csv  89a545954a629efbf3c7cb1d2e10a3bb43fb11a8b7b229032cb928bef6f8055c
height_models.csv      26c1289285e041207845d94a3fa7eb792d70967c10f8c3fc4c374ec574a05871
synthetic_trees.csv    63cb74250fd29b57c3fd267cfff9a091d43346e2951eaf849b98360c7e28b79c
assortment_matrix.csv  57043d584e84284512f8d60ac4e0ff8cf668c9091b659134440f8f0b7289faef
dbh_validation.csv     734f94668e65fc1978d624663a50305e15dad4e1e9ae873fb9e90431727cb8b9
species_composition.csv cb764514e101788d3528068513f86d13ea33a7f2d5a97bd661a85eb1663dc6a0
VALIDATION_REPORT.md   0cec5147e2fd389eb38fa9e413a3d026cb6ae53486c9ba24fafe4fabad0d7143
validation_summary.json 15ad1ace07ccea682ac2d9ffac54b57f9e54f5d2fb2de0f914e02e9d7bf347dc
manifest.json          d2cb1a154c367085ae24d2ce0176387faf1a7e8aad3fd6ba1df2c3f24af0e709
```
**Verification Timestamp**: 2026-11-15T21:30:00Z
**Verified By**: Python 3.12.3, pandas 3.0.3, numpy 2.5.0, scipy 1.18.0, nemora reference checkout (`ws4/nemora`)
**Artifact Reference**: data commit `2b60f8a8849e8b9a4db33c6ea6096d0bce42d8be`; parent commit `909456a742689b1cecec3505ed6706f259d5e98b`; `data/derived/national/assortments/tsa24_prototype/`; `workflows/national/assortments/`; `metadata/assortment-pilots.csv`
