# Phase 6 Validation Memo

**Phase**: P6.5 Validation Memo
**Status**: COMPLETE — diagnostic validation only; production gates remain open
**Date**: 2026-08-08

## Summary

Phase 6 delivered a tiny SCANFI v2 2025 real-sampled supply lane for software
integration and smoke testing. All six planned fixture cells and the five
authorized 2025 source layers were materialized, manifested, and used to
produce ws3-shaped and LP artifacts.

The six windows are heuristic sampling points, not a statistically defensible
sample of BC. They are therefore quarantined as
`SOFTWARE_FIXTURE_NOT_EVIDENCE`. The outputs prove that the pipeline runs;
they do not support BC-scale biomass, yield, supply, economic, or scenario
claims. The quarantine decision and labels are recorded in
`planning/scientific-model-recovery-plan.md` and the fixture manifests.

## Sampling Status

| Fixture Cell | Region | Status | Blocker |
|-------------|--------|--------|---------|
| `tiny_bc_real_sample_coast_wet_001` | coast_wet_forest | **QUARANTINED FIXTURE** | Heuristic 100,000-ha square; not inferential evidence |
| `tiny_bc_real_sample_coast_second_002` | coast_second_growth | **QUARANTINED FIXTURE** | Heuristic 100,000-ha square; not inferential evidence |
| `tiny_bc_real_sample_interior_dry_001` | interior_dry_forest | **QUARANTINED FIXTURE** | Materialized window; not inferential evidence |
| `tiny_bc_real_sample_interior_cold_002` | interior_cold_forest | **QUARANTINED FIXTURE** | Heuristic 100,000-ha square; not inferential evidence |
| `tiny_bc_real_sample_interior_salvage_001` | interior_disturbance_mix | **QUARANTINED FIXTURE** | Heuristic 100,000-ha square; not inferential evidence |
| `tiny_bc_real_sample_interior_residue_001` | interior_residue_candidate | **QUARANTINED FIXTURE** | Heuristic 100,000-ha square; not inferential evidence |

**Overall status**: COMPLETE for the diagnostic lane. All six cells were
materialized on 2026-08-07. The parent inventory has
`value_policy=quarantined` and `sampling_status=quarantined`; every manifest
row has `value_policy_status_labels=SOFTWARE_FIXTURE_NOT_EVIDENCE`.

## Planned Source Layers

The following five SCANFI v2 2025 layers were authorized for sampling:

| Layer | File | Role |
|-------|------|------|
| Biomass | `SCANFI_att_biomass_2025_v2_20260119.tif` | inventory_biomass_screening |
| Height | `SCANFI_att_height_2025_v2_20260119.tif` | inventory_height_screening |
| Closure | `SCANFI_att_closure_2025_v2_20260119.tif` | inventory_canopy_closure_screening |
| Landcover | `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` | inventory_landcover_screening |
| Age | `SCANFI_age_median_2025_v2_20260119.tif` | inventory_age_screening |

## Downstream Diagnostic Outputs

The sampled inventory was passed through the existing diagnostic adapters and
LP contract. The generated artifacts include ws3-shaped harvest output,
`supply_offer`, solution flow and summary tables, graph/contract validation
tables, provenance, and comparison outputs under
`metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/` and its
diagnostic LP run directories.

The recorded Phase 6 diagnostic solve produced:

- `49,665 m³` of supply;
- LP objective value of `$2,350,244 CAD`; and
- total supply cost of `$2,483,246 CAD`, matching `49,665 m³ × $50/m³`.

These are reproducibility and integration results for the diagnostic fixture.
They are not calibrated economic estimates or evidence of BC-scale supply.
The yield rule remains diagnostic, including the `0.75 m³/ha` placeholder;
there is no species×grade disaggregation and no multi-rotation WS3 workflow.

## Validation Check Status

Per `tiny-real-sampling-validation-contract.csv`, the following checks were
used for diagnostic acceptance. Passing them authorizes software integration
use only; it does not make the heuristic windows inferential evidence.

### Diagnostic Acceptance Gates (Required for Phase 6)

| Check | Status | Notes |
|-------|--------|-------|
| csv_parse_required_columns | PASSED | CSV parsed successfully with all required columns |
| unique_fixture_sample_ids | PASSED | All 6 cells have unique IDs |
| fixture_cell_coverage | PASSED | All 6 cells sampled |
| source_layer_coverage | PASSED | All 5 layers present per extraction contract |
| nonnegative_area | PASSED | Area fields present and nonnegative |
| nonnegative_sampled_values | PASSED | All sampled values are nonnegative |
| nodata_pixel_accounting | PASSED | Manifest records nodata counts |
| biomass_diagnostic_range | PASSED | All values in 0-1000 t/ha range |
| height_diagnostic_range | PASSED | All values in 0-80 m range |
| closure_diagnostic_range | PASSED | All values in 0-100% range |
| age_diagnostic_range | PASSED | All values in 0-400 years range |
| landcover_forest_candidate_logic | PASSED | Landcover class values present |
| crs_resolution_consistency | PASSED | Manifest CRS and 30m resolution fields are present and consistent within the extracted windows; the source-WKT CRS is retained and no EPSG:3978 inference is made |
| checksum_manifest_completeness | PASSED | SHA256 checksums present in all manifests |
| provenance_completeness | PASSED | Source files and manifest paths identified |
| value_policy_status_labels | PASSED | All 30 manifest rows are `SOFTWARE_FIXTURE_NOT_EVIDENCE`; parent mirror rows are `quarantined` |

Focused verification also passed on 2026-08-08: `tests/test_bc_mini_prototype.py`
reported 21 passed, and `git diff --check` passed in both the parent repository
and the `data` submodule.

### Production Calibration Gates (Blocked for Phase 6)

| Gate | Status | Notes |
|------|--------|-------|
| residue_yield_policy_gate | BLOCKED_OPEN | Diagnostic lane may keep residue visible but production residue yield is not accepted |
| cost_calibration_gate | BLOCKED_OPEN | Synthetic diagnostic costs must not be treated as calibrated economics |
| climate_predictor_gate | BLOCKED_OPEN | Gauthier climate predictors pending separate authorization |
| nfi_access_gate | BLOCKED_OPEN | NFI material remains outside parent repo until access/license review |
| production_benchmark_validation_gate | BLOCKED_OPEN | Production benchmark validation requires accepted benchmark sources |
| species_composition_gate | BLOCKED_OPEN | No species-composition source or calibrated species mix was materialized |
| multi_rotation_ws3_gate | BLOCKED_OPEN | No 10-year disaggregation, one-year sub-periods, or species×grade outputs |
| national_scaling_gate | BLOCKED_OPEN | No provincial or national inferential scale-up is authorized |

## Unresolved Items Requiring Future Authorization

1. **Residue yield policy**: The 0.75 m³/ha yield remains a diagnostic placeholder,
	not a production residue-yield policy.
2. **Cost calibration**: The LP uses diagnostic cost inputs; calibrated cost
	surfaces require sourced economics data.
3. **Climate predictors**: Gauthier temperature/precipitation normal period
	remains unresolved and was not materialized.
4. **NFI access and independence**: Ground-plot access, license, and SCANFI
	training-independence evidence remain unresolved.
5. **Production validation benchmarks**: Independent accepted benchmarks are
	required; heuristic windows cannot serve as benchmarks.
6. **North extrapolation mask**: Separate authorization remains required.
7. **Species composition and workflow detail**: Species×grade outputs and the
	proposed WS3 two-rotation workflow were not materialized.
8. **Inferential recovery**: The six heuristic windows remain software fixtures;
	any G2–G8 work requires separate developer approval under the recovery plan.

## Deliberately Not Materialized

Per Phase 5 authorization and safety stop conditions:

- Broad `data/raw` checkout (not authorized for Phase 6)
- All SCANFI time steps except 2025 (not in authorized subset)
- Species composition layers (not in authorized subset)
- Private NFI reference pixels or plots (not authorized)
- Climate payloads (not in authorized subset)
- Production benchmark data (not available)

## Next Steps

1. Preserve the extracted windows, manifests, parent inventory, and generated
	LP artifacts as diagnostic software fixtures.
2. Reconcile this memo, `ROADMAP.md`, `CHANGE_LOG.md`, the handoff, and issue
	`#27` so the Phase 6 carryover and Phase 7 status agree.
3. Keep G2–G8 closed until each later gate receives explicit developer
	approval and its required contracts and independent evidence are defined.
4. Continue Phase 7 source triage under issue `#1` / parent issue `#26` without
	promoting heuristic fixture outputs to production evidence.

## Phase 6 Completion Criteria

The diagnostic Phase 6 lane is complete:

- [x] SCANFI rasters acquired into `data/raw/scanfi/v2/` through the `data`
	DataLad/git-annex boundary.
- [x] Six raster windows extracted to
	`data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`.
- [x] Window manifests written to
	`data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`.
- [x] `diagnostic_inventory_cells.csv` populated with sampled values and
	quarantined from inferential use.
- [x] Diagnostic acceptance checks pass.
- [x] `ws3_harvest_output` and `supply_offer` generated.
- [x] LP variant solved with solution artifacts emitted.
- [x] Comparison tables generated against existing variants.
- [x] This validation memo finalized with actual diagnostic findings.
- [ ] Production calibration, independent validation, species composition,
	multi-rotation WS3, and national scaling remain open by design.
