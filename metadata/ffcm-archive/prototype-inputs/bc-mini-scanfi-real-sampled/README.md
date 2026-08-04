# BC Mini-Network Tiny Real-Sampled SCANFI Diagnostic Variant

**Fixture ID**: `tiny_bc_real_sampling_001`
**Scenario ID**: `scanfi_real_sampled_diagnostic_001`
**Phase**: Phase 6 (Tiny Real-Sampled Diagnostic Supply Lane)
**Status**: DIAGNOSTIC - awaiting SCANFI raster acquisition and sampling

## Overview

This variant is the Phase 6 tiny real-sampled extension of the BC mini-network
prototype. It replaces synthetic SCANFI proxy values with actual sampled values
from the five authorized SCANFI v2 2025 rasters for the six planned fixture
cells defined in `tiny-real-sampling-fixture-cells.csv`.

Unlike the synthetic `bc-mini-scanfi-diagnostic` variant (where values are
designed proxies), this variant contains real sampled statistics from the
approved raster windows while keeping the same downstream BC mini network,
technology, transport, and market tables.

## Source Data

Sampled from the five authorized SCANFI v2 2025 layers:

- `SCANFI_att_biomass_2025_v2_20260119.tif` - aboveground biomass
- `SCANFI_att_height_2025_v2_20260119.tif` - forest height
- `SCANFI_att_closure_2025_v2_20260119.tif` - crown closure
- `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` - NFI-compatible landcover
- `SCANFI_age_median_2025_v2_20260119.tif` - median forest age

Source references are in `data/raw/scanfi/v2/` (DataLad submodule).
Derived windows are in `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`.
Manifests are in `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`.

## Value Policy

**DO NOT TREAT AS PRODUCTION-CALIBRATED INPUTS.**

This variant is diagnostic only:
- Residue yield policy remains OPEN (logging_residue_yield_gate is not resolved)
- Cost calibration remains OPEN (cost_calibration_gate is not resolved)
- Climate predictors remain OPEN (climate_predictor_gate is not resolved)
- NFI access remains OPEN (nfi_access_gate is not resolved)
- Production benchmark validation remains OPEN (production_benchmark_validation_gate is not resolved)

The `value_policy` column distinguishes `real_sampling_fixture_design_not_sampled`
(planning) from `real_sampled` (actual raster sample). The current CSV is a
design skeleton; real sampled values will be added after P6.1 materialization.

## Variant Relationship

```
bc-mini (base)
  ├── bc-mini-scanfi-diagnostic (synthetic proxies)
  ├── bc-mini-ws3-smoke (real ws3 output)
  ├── bc-mini-scanfi-gauthier-diagnostic (SCANFI + Gauthier rules)
  └── bc-mini-scanfi-real-sampled (real SCANFI raster samples)  ← THIS VARIANT
```

## Contents

- `diagnostic_inventory_cells.csv` - sampled inventory cell values (skeleton;
  values to be filled after raster acquisition)
- `supply_offer.csv` - LP-ready supply offers aggregated from sampled cells
- `ws3_harvest_output.csv` - ws3-shaped harvest output with full provenance
- `solution_summary.csv` - LP solve summary (to be generated at P6.4)
- `solution_flow.csv` - LP solution flows (to be generated at P6.4)
- Comparison tables against synthetic and ws3-smoke variants

## Validation

See `tiny-real-sampling-validation-contract.csv` for required diagnostic
acceptance checks. All `required_for_phase6_diagnostic` checks must pass before
this variant is used in any LP solve.

## Production Gate Status

| Gate | Status | Blocking |
|------|--------|----------|
| Residue yield policy | OPEN | Yes - production blocked |
| Cost calibration | OPEN | Yes - production blocked |
| Climate predictors | OPEN | Yes - production blocked |
| NFI access | OPEN | Yes - production blocked |
| Production benchmark validation | OPEN | Yes - production blocked |
| National scaling | NOT_APPLICABLE | N/A for BC mini |

## Acquisition Blocker

This variant is blocked pending SCANFI raster acquisition. See
`planning/phase-6-scanfi-acquisition-plan.md` for the authorized source files,
sizes, and acquisition steps. Do not proceed with P6.1 until acquisition is
authorized and completed.
