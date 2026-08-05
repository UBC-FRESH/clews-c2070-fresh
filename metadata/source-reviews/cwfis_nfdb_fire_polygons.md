# CWFIS NFDB Fire Polygon Data - Source Review

**Review date**: 2026-08-05
**Source ID**: `cwfis_nfdb_fire_polygons`
**Status**: `accepted`

## Source Overview

- **Dataset**: Canadian National Fire Database (NFDB) - fire polygon data
- **Publisher**: Natural Resources Canada / Canadian Forest Service
- **License**: Open Government Licence - Canada
- **Access**: Direct download via cwfis.cfs.nrcan.gc.ca
- **URL**: `https://cwfis.cfs.nrcan.gc.ca/downloads/nfdb/fire_poly/current_version/`
- **Temporal**: 1972–present (2024 in current package)

## Available Resources

| File | Size | Description |
|------|------|-------------|
| `NFDB_poly.zip` | 742 MB | All fire perimeters 1972–present |
| `NFDB_poly_large_fires.zip` | 647 MB | Large-fire subset (≥2000 ha or ≥2000 m perimeter) ← **ACQUIRED 2026-08-05** |
| `NFDB_poly_pre1972.zip` | 35 MB | Pre-1972 historical subset |

## Acquisition

- Downloaded 2026-08-05 to `data/raw/nrcan/nfdb/fire_poly/NFDB_poly_large_fires.zip`
- Verified with `unzip -t`: no errors detected
- Archived to DataLad git-annex; content hash locked; pushed to arbutus-s3
| `NFDB_poly_large_fires.zip` | 647 MB | Large fires only (perimeter-based) |
| `NFDB_poly_pre1972.zip` | 35 MB | Pre-1972 fire perimeters |

Also available: XML metadata, documentation PDFs.

## Fit to FRESH

- **Disturbance masking**: Yes — fire perimeter history can mask recent burns
  in SCANFI supply estimation (fires suppress merchantable yield).
- **Return interval**: Yes — NFDB polygons + CanLaD annual rasters give
  complementary fire vs. harvest disturbance distinction.
- **Large-fire filtering**: `NFDB_poly_large_fires.zip` provides pre-filtered
  large-fire subset for efficiency.

## Next Steps

1. Download accepted payload (742 MB or 647 MB large-fire subset).
2. Inspect attribute table for fire year, cause, size.
3. Archive to `data/raw/nrcan/nfdb/fire_poly/`.
4. Consider spatial join with SCANFI supply nodes for disturbance masking.
