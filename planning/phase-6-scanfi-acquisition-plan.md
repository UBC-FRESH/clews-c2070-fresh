# Phase 6 SCANFI Acquisition Plan

**Status**: COMPLETED for the authorized five-file 2025 subset; full-package
expansion remains blocked
**Date**: 2026-08-04
**Phase**: P6.0 / P6.1

## Blocker Summary

The five authorized SCANFI v2 2025 source rasters were downloaded, checksum
verified, added to `data/raw/scanfi/v2/`, and published to the DataLad/git-annex
remote. The acquisition record is
`data/manifests/scanfi-v2-2025-acquisition.csv`; the five files total about
20.6 GB. The six heuristic windows derived from these rasters remain software
fixtures and are quarantined from inferential use.

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

## Acquisition Record

- CDN access, download, SHA-256 verification, annex registration, and
   `arbutus-s3` publication are complete for all five authorized files.
- Exact checksums, byte sizes, source URLs, acquisition dates, and
   `downloaded_verified` statuses are recorded in
   `data/manifests/scanfi-v2-2025-acquisition.csv`.
- The archived payloads are limited to `data/raw/scanfi/v2/` and do not include
   other SCANFI time steps, species layers, or the north extrapolation mask.

## NOT Authorized

- Full SCANFI v2 directory checkout (~312 GB)
- Other SCANFI time steps (1985-2020)
- Species composition layers (spsCC_*)
- North extrapolation mask (separate follow-up)
- Private NFI data
- Climate payloads

## Continue Without Additional Raster Content

The following Phase 6 tasks can proceed without raster acquisition:
- P6.2: Draft derived inventory table contract and validation checks
- P6.3: ws3-shaped supply output contract (using fixture cell design)
- P6.4: LP variant contract artifacts (provenance skeleton)
- P6.5: Validation memo skeleton
- Source triage: `biomass_inventory_business` and `databasin_canada_forest_tenures`

## Resumption Triggers

Further SCANFI acquisition requires a new explicit authorization. The current
record does not authorize the full 312 GB package, other time steps, species
composition layers, or the north extrapolation mask.
