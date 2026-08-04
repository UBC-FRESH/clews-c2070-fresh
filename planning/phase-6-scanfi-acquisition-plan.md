# Phase 6 SCANFI Acquisition Plan

**Status**: BLOCKED - awaiting explicit authorization before CDN download
**Date**: 2026-08-04
**Phase**: P6.0 / P6.1

## Blocker Summary

The five authorized SCANFI v2 2025 source rasters are not present in the `data`
submodule. Phase 5 authorization permits their use but does not automatically
authorize the ~21 GB download or create the annexed content. The data submodule
is a fresh scaffold at commit `731667f`.

## Authorized Source Files

Per Phase 5 authorization and `tiny-real-sampling-extraction-contract.csv`:

| File | Size (est.) | SHA-256 (expected) | Source URL |
|------|-------------|-------------------|------------|
| `SCANFI_att_biomass_2025_v2_20260119.tif` | 6.82 GB | TBD after download | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/SCANFI_att_biomass_2025_v2_20260119.tif` |
| `SCANFI_att_height_2025_v2_20260119.tif` | 3.02 GB | TBD after download | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/SCANFI_att_height_2025_v2_20260119.tif` |
| `SCANFI_att_closure_2025_v2_20260119.tif` | 4.20 GB | TBD after download | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/SCANFI_att_closure_2025_v2_20260119.tif` |
| `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` | 1.67 GB | TBD after download | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/SCANFI_att_nfiLandcover_2025_v2_20260119.tif` |
| `SCANFI_age_median_2025_v2_20260119.tif` | 5.33 GB | TBD after download | `https://download-telecharger.services.geo.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/SCANFI_age_median_2025_v2_20260119.tif` |

**Total estimated size**: ~21 GB

Verify exact sizes from `metadata/source-file-manifests/scanfi_v2_files.csv`.

## License

Open Government Licence - Canada. Permits use, reproduction, and
distribution without restriction. No additional license review required.

## Acquisition Steps (Pending Authorization)

1. **Verify CDN accessibility** - confirm all five URLs return HTTP 200 before
   attempting download
2. **Create target directory** - `data/raw/scanfi/v2/` in the data submodule
3. **Download with checksum verification** - compute SHA-256 during download
4. **Add to DataLad/git-annex** - `git annex add` each file to create annex keys
5. **Publish to arbutus-s3** - `git annex copy --to arbutus-s3` for each file
6. **Record checksums** - update `metadata/source-file-manifests/scanfi_v2_files.csv`
   with actual SHA-256 values and download dates
7. **Commit to data submodule** - push to `origin/main` in data repo
8. **Update parent repo** - commit submodule pointer update

## NOT Authorized

- Full SCANFI v2 directory checkout (~312 GB)
- Other SCANFI time steps (1985-2020)
- Species composition layers (spsCC_*)
- North extrapolation mask (separate follow-up)
- Private NFI data
- Climate payloads

## Continue Without Raster Content

The following Phase 6 tasks can proceed without raster acquisition:
- P6.2: Draft derived inventory table contract and validation checks
- P6.3: ws3-shaped supply output contract (using fixture cell design)
- P6.4: LP variant contract artifacts (provenance skeleton)
- P6.5: Validation memo skeleton
- Source triage: `biomass_inventory_business` and `databasin_canada_forest_tenures`

## Resumption Triggers

Resume P6.1 materialization when:
1. User explicitly authorizes the ~21 GB SCANFI acquisition
2. Runbook prerequisites satisfied (git config, credentials if needed)
3. CDN URLs confirmed accessible
4. Target directory `data/raw/scanfi/v2/` created in data submodule
