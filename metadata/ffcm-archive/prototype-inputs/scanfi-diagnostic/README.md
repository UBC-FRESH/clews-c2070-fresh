# SCANFI Diagnostic Inventory Subset

This directory contains lightweight metadata for the first SCANFI v2 diagnostic
inventory subset selected for the ws3 supply replacement path. It does not
contain raw SCANFI rasters, reports, or downloaded payloads.

`scanfi_v2_2025_diagnostic_subset_manifest.csv` is generated from
`metadata/source-file-manifests/scanfi_v2_files.csv` by
`scripts/ffcm_archive/build_scanfi_diagnostic_manifest.py`.

The five 2025 raster rows, English README, and English update report are
verified against the local SCANFI source-file manifest. The north extrapolation
mask is recorded as missing because it was recommended during source review but
is absent from the current local and live public SCANFI v2 directory listings.
