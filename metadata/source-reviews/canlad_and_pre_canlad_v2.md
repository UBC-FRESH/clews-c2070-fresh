# CanLaD And Pre-CanLaD v2 Source Review

Review date: 2026-06-25.

## Sources

CanLaD:

- Dataset: Canada Landsat Disturbance (CanLaD) - Including Forest Insect Pest.
- DOI: `https://doi.org/10.23687/902801fd-4d9d-4df4-9e95-319e429545cc`.
- Open Canada metadata licence: Open Government Licence - Canada.
- README licence statement: Creative Commons Attribution 4.0 International.
- Resource root:
  `https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/canlad_including_insect_defoliation/v1.1/`.

Pre-CanLaD v2:

- Dataset: Pre-CanLaD v2: Canada Landsat Disturbance (CanLaD) 30-m resolution
  disturbance detection prior to 1984.
- DOI: `https://doi.org/10.23687/8d49698f-40f9-40da-b097-a3f4c90adf5a`.
- Licence: Open Government Licence - Canada.
- Resource root:
  `https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/canada_disturbances_1965to1984/v2/`.

## CanLaD Findings

Disturbance classes:

- 1: wildfire;
- 2: harvesting and other human-induced disturbances;
- 3: other disturbances;
- 4: water extension;
- 5: defoliation and harvesting;
- 6: low severity defoliation;
- 7: medium severity defoliation; and
- 8: high severity defoliation.

**2026-08-05 update**: Raster payloads are accessible via two subdirectories:

- `Latest_filtered_CAN_20260508/`: 5 latest-disturbance mosaics (~2.5 GB total),
  including `canlad_1985_2025_latest_type_v1_1_20260508.tif` (442 MB).
- `TS_FinalFilter_20260508/`: 42 annual time-series rasters (~80 MB each, ~3.3 GB
  total), covering `canlad_annual_1984` through `canlad_annual_2025`.

Publication date is 2026-05-08 — the package is freshly updated. No `data/`,
`rasters/`, or `annual/` subdirectories exist; rasters live in the two named
subdirectories above. Total payload is ~5.8 GB. Access confirmed via HTTP 200 on
directory listing and TIF file headers.

Manifest:

- `metadata/source-file-manifests/canlad_files.csv`

## Pre-CanLaD v2 Findings

The public Pre-CanLaD v2 directory is internally consistent and directly lists
the payloads.

Observed on 2026-06-25:

- Total files: 20.
- Total size: about 1.229 GB.
- Core GeoTIFF rasters: 4, about 1.153 GB.
- Overview files: 4, about 0.074 GB.

Core rasters:

- `preCanlad_disturbanceType_v2_20251126.tif`
- `preCanlad_disturbanceYear_v2_20251126.tif`
- `preCanlad_correctionMask_v2_20251126.tif`
- `preCanlad_updateMask_v2_20251126.tif`

Important coding notes from the README:

- disturbance type: `2 = Fire`, `3 = Harvest`, `4 = Insect`;
- disturbance year: `1950-1984`, with `999 = unknown`; users should convert
  `999` to `1955`;
- correction mask records data provenance and fire/harvest confirmation codes;
- update mask identifies retained v1 versus new/updated v2 pixels.

Manifest:

- `metadata/source-file-manifests/pre_canlad_v2_files.csv`

## Recommendation

CanLaD should remain high priority, but set status to
`needs_access_resolution` until NRCan's public raster payload access is
confirmed or corrected. Do not archive the 1985-2025 CanLaD support files alone
as if the dataset were complete.

Pre-CanLaD v2 should be accepted as the pre-1985 companion disturbance source.
The full core raster payload is manageable for DataLad/S3 after PI approval.

For the first national disturbance baseline:

- use CanLaD once the 1985-2025 raster payloads are reachable;
- use Pre-CanLaD v2 for pre-1985 disturbance history and age/carbon context;
- keep NBAC/NFDB in the queue for fire-specific validation and year adjustment;
  and
- document any conversion of Pre-CanLaD `999` disturbance years to `1955` in
  preprocessing metadata.
