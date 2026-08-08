# SCANFI v2 Source Review

Review date: 2026-06-25.

## Source

- Dataset: SCANFI v2: the Spatialized Canadian National Forest Inventory data
  product.
- Publisher: Natural Resources Canada, Canadian Forest Service.
- Licence: Open Government Licence - Canada.
- DOI: `https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf`.
- Public resource root:
  `https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v2/`.

## Package Inventory

The public directory was inventoried on 2026-06-25.

- Total directory size: about 312 GB.
- Total file count: 420.
- GeoTIFF count: 135, about 307.9 GB.
- Overview file count: 135, about 4.3 GB.
- Auxiliary XML count: 136.
- Documentation/thumbnail files: 5.
- Time steps: 1985, 1990, 1995, 2000, 2005, 2010, 2015, 2020, 2025.

The full file manifest is tracked at:

- `metadata/source-file-manifests/scanfi_v2_files.csv`

## Layer Families

Approximate package sizes by family:

- `att_biomass`: 59.168 GB.
- `age_median`: 54.754 GB.
- `att_closure`: 36.177 GB.
- `att_height`: 25.933 GB.
- `att_nfiLandcover`: 14.560 GB.
- `spsCC_blackSpruce`: 28.648 GB.
- `spsCC_broadleaf`: 21.701 GB.
- `spsCC_otherConiferous`: 21.457 GB.
- `spsCC_tamarack`: 16.215 GB.
- `spsCC_jackPine`: 12.740 GB.
- `spsCC_balsamFir`: 9.570 GB.
- `spsCC_lodgepolePine`: 6.197 GB.
- `spsCC_whiteRedPine`: 2.246 GB.
- `spsCC_douglasFir`: 2.112 GB.
- `spsCC_ponderosaPine`: 0.725 GB.

## README Notes

The English README describes SCANFI v2 as a 30 m national forest attribute
product with wall-to-wall maps of forest structure, composition, biomass, and
age, derived from Landsat time series, National Forest Inventory data, and
auxiliary environmental layers.

Projection:

- NAD83 Canada Lambert Conformal Conic.
- proj4:
  `+proj=lcc +lat_0=0 +lon_0=-95 +lat_1=49 +lat_2=77 +x_0=0 +y_0=0 +datum=NAD83 +units=m +no_defs`.

Important modelling notes:

- Species layers are crown closure percentages, not proportions of total crown
  closure as in SCANFI v1.
- Species-level biomass can be approximated by scaling pixel-level biomass by
  relative species crown closure.
- Forest age is derived by combining disturbance history with NFI-based age
  estimates for undisturbed stands.
- The product is optimized for regional to national analyses, not local
  plot-level validation or fine temporal trend detection.
- Northern extrapolation areas are explicitly mapped in
  `SCANFI_north_extrapol_area.tif`.

## Update Report Notes

The English update report is 10 pages and was created on 2026-04-21.

Key v2 updates:

- Transition to Landsat Collection 2.
- Five-year SCANFI time series using LandTrendr-smoothed winter imagery.
- Updated 1990-2020 climate normals.
- Denser NFI training-data sampling.
- Improved water prediction and urban/cropland delineation.
- New 1985-2025 national forest age maps.
- Modified target and response variable sets.

The update report references "Canadian Landsat Disturbance 2 (CanLaD 2)" as an
input to forest-age mapping. This should be reconciled with the current Open
Canada resource metadata during CanLaD triage.

## Recommendation

Accept SCANFI v2 as a canonical national forest inventory candidate, but do not
mirror the full 312 GB package by default.

Recommended first-pass DataLad subset:

- `SCANFI_att_biomass_2025_v2_20260119.tif`
- `SCANFI_att_height_2025_v2_20260119.tif`
- `SCANFI_att_closure_2025_v2_20260119.tif`
- `SCANFI_att_nfiLandcover_2025_v2_20260119.tif`
- `SCANFI_age_median_2025_v2_20260119.tif`
- `SCANFI_north_extrapol_area.tif`
- `_SCANFI_v2_read_me.txt`
- `___SCANFI v2 - EN - Update report.pdf`

Defer until modelling requirements are explicit:

- full 1985-2025 time series;
- all species crown-closure rasters; and
- `.ovr` overview files unless needed for desktop GIS review.

## Acquisition Reconciliation — 2026-08-08

The authorized five-file 2025 subset was downloaded, SHA-256 verified, added to
the DataLad/git-annex data repository, and published to `arbutus-s3`. The
durable acquisition record is
`data/manifests/scanfi-v2-2025-acquisition.csv`; the payloads are under
`data/raw/scanfi/v2/`.

This does not authorize or imply acquisition of the remaining SCANFI package.
The full 1985–2025 time series, species crown-closure layers, overview files,
and north extrapolation mask remain outside the approved subset. The six
heuristic diagnostic windows are software fixtures and must not be used as
inferential evidence.
