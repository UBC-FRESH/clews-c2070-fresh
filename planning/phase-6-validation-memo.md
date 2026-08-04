# Phase 6 Validation Memo

**Phase**: P6.5 Validation Memo
**Status**: DRAFT - pending SCANFI raster acquisition and sampling
**Date**: 2026-08-04

## Summary

Phase 6 authorized a tiny real-sampled diagnostic supply lane using six
SCANFI v2 2025 raster windows. This memo documents what was sampled (or
planned), what validation checks passed or failed, what remains diagnostic,
and which production gates remain open.

## Sampling Status

| Fixture Cell | Region | Status | Blocker |
|-------------|--------|--------|---------|
| `tiny_bc_real_sample_coast_wet_001` | coast_wet_forest | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |
| `tiny_bc_real_sample_coast_second_002` | coast_second_growth | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |
| `tiny_bc_real_sample_interior_dry_001` | interior_dry_forest | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |
| `tiny_bc_real_sample_interior_cold_002` | interior_cold_forest | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |
| `tiny_bc_real_sample_interior_salvage_001` | interior_disturbance_mix | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |
| `tiny_bc_real_sample_interior_residue_001` | interior_residue_candidate | PLANNED_NOT_SAMPLED | Awaiting SCANFI acquisition |

**Overall status**: BLOCKED - SCANFI raster acquisition not yet authorized or completed.

## Planned Source Layers

The following five SCANFI v2 2025 layers were authorized for sampling:

| Layer | File | Role |
|-------|------|------|
| Biomass | `SCANFI_att_biomass_2025_v2_20260119.tif` | inventory_biomass_screening |
| Height | `SCANFI_att_height_2025_v2_20260119.tif` | inventory_height_screening |
| Closure | `SCANFI_att_closure_2025_v2_20260119.tif` | inventory_canopy_closure_screening |
| Landcover | `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` | inventory_landcover_screening |
| Age | `SCANFI_age_median_2025_v2_20260119.tif` | inventory_age_screening |

## Validation Check Status

Per `tiny-real-sampling-validation-contract.csv`, the following checks are
required before the sampled inventory can feed the LP variant:

### Diagnostic Acceptance Gates (Required for Phase 6)

| Check | Status | Notes |
|-------|--------|-------|
| csv_parse_required_columns | PENDING | Skeleton CSV created; values to be filled after sampling |
| unique_fixture_sample_ids | PENDING | Skeleton has unique IDs; verify after sampling |
| fixture_cell_coverage | PENDING | All 6 cells planned; confirm after sampling |
| source_layer_coverage | PENDING | All 5 layers planned per extraction contract |
| nonnegative_area | PENDING | Area fields present as design; verify after sampling |
| nonnegative_sampled_values | PENDING | Sampled values pending |
| nodata_pixel_accounting | PENDING | Manifest will record nodata; verify after sampling |
| biomass_diagnostic_range | PENDING | Check 0-1000 t/ha; pending real values |
| height_diagnostic_range | PENDING | Check 0-80 m; pending real values |
| closure_diagnostic_range | PENDING | Check 0-100%; pending real values |
| age_diagnostic_range | PENDING | Check 0-400 years; pending real values |
| landcover_forest_candidate_logic | PENDING | pending real landcover class values |
| crs_resolution_consistency | PENDING | pending window extraction |
| checksum_manifest_completeness | PENDING | pending window manifests |
| provenance_completeness | PENDING | Source files identified; manifest paths pending |
| value_policy_status_labels | READY | Labels distinguish design vs sampled vs production |

### Production Calibration Gates (Blocked for Phase 6)

| Gate | Status | Notes |
|------|--------|-------|
| residue_yield_policy_gate | BLOCKED_OPEN | Diagnostic lane may keep residue visible but production residue yield is not accepted |
| cost_calibration_gate | BLOCKED_OPEN | Synthetic diagnostic costs must not be treated as calibrated economics |
| climate_predictor_gate | BLOCKED_OPEN | Gauthier climate predictors pending separate authorization |
| nfi_access_gate | BLOCKED_OPEN | NFI material remains outside parent repo until access/license review |
| production_benchmark_validation_gate | BLOCKED_OPEN | Production benchmark validation requires accepted benchmark sources |

## Unresolved Items Requiring Future Authorization

1. **SCANFI raster acquisition** (~21 GB): Required before P6.1 materialization
2. **Residue yield policy**: Production logging residue requires separate policy decision
3. **Cost calibration**: Calibrated cost surfaces require sourced economics data
4. **Climate predictors**: Gauthier temperature/precipitation normal period unresolved
5. **NFI access**: Ground plot access or standard report license review pending
6. **Production validation benchmarks**: NFI or other accepted benchmarks required
7. **North extrapolation mask**: Separate authorization needed for northern mask

## Deliberately Not Materialized

Per Phase 5 authorization and safety stop conditions:

- Broad `data/raw` checkout (not authorized for Phase 6)
- All SCANFI time steps except 2025 (not in authorized subset)
- Species composition layers (not in authorized subset)
- Private NFI reference pixels or plots (not authorized)
- Climate payloads (not in authorized subset)
- Production benchmark data (not available)

## Next Steps

1. **Await SCANFI acquisition authorization** before proceeding with P6.1
2. **After acquisition**: Run window extraction for all 6 fixture cells
3. **After extraction**: Populate `diagnostic_inventory_cells.csv` with sampled values
4. **After population**: Run validation contract checks
5. **After validation**: Proceed to P6.3 (ws3-shaped supply output)
6. **After supply output**: Proceed to P6.4 (LP solve)
7. **After LP solve**: Update this memo with actual validation results

## Phase 6 Completion Criteria

This phase is complete when:

- [ ] SCANFI rasters acquired into `data/raw/scanfi/v2/`
- [ ] Six raster windows extracted to `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`
- [ ] Window manifests written to `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`
- [ ] `diagnostic_inventory_cells.csv` populated with real sampled values
- [ ] All diagnostic acceptance checks pass
- [ ] `ws3_harvest_output` and `supply_offer` generated
- [ ] LP variant solved with solution artifacts emitted
- [ ] Comparison tables generated against existing variants
- [ ] This validation memo finalized with actual results
- [ ] Planning surfaces updated (ROADMAP, CHANGE_LOG, source-inventory)
