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
