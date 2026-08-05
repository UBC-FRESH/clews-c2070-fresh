# CWFIS NFDB Fire Point Data - Source Review

**Review date**: 2026-08-05
**Source ID**: `cwfis_nfdb_fire_points`
**Status**: `accepted`

## Source Overview

- **Dataset**: Canadian National Fire Database (NFDB) - fire point data
- **Publisher**: Natural Resources Canada / Canadian Forest Service
- **License**: Open Government Licence - Canada
- **Access**: Direct download via cwfis.cfs.nrcan.gc.ca
- **URL**: `https://cwfis.cfs.nrcan.gc.ca/downloads/nfdb/fire_pnt/current_version/`
- **Temporal**: 1972–present

## Available Resources

| File | Size | Description |
|------|------|-------------|
| `NFDB_point_shp.zip` | 1 GB | All fire point locations |
| `NFDB_point_large_fires_shp.zip` | 65 MB | Large fires only |
| `NFDB_point_txt.zip` | 90 MB | All fires as CSV/text |

Also available: stats spreadsheet, metadata PDFs.

## Fit to FRESH

- **Point-based fire analysis**: Yes — NFDB points provide precise ignition locations
  for fire frequency analysis.
- **Complement to polygons**: Polygons give perimeter/area; points give location.
  Both useful for different disturbance analyses.
- **Large-fire subset**: `NFDB_point_large_fires_shp.zip` (65 MB) for efficiency
  when full dataset is not needed.

## Next Steps

1. Download accepted payload (consider large-fire subset first at 65 MB).
2. Inspect attribute table for fire year, cause, size.
3. Archive to `data/raw/nrcan/nfdb/fire_pnt/`.
