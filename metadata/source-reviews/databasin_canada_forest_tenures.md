# Data Basin Canada Forest Tenures - Source Review

**Review date**: 2026-08-05
**Source ID**: `databasin_canada_forest_tenures`
**Status**: `accepted` (interim)

## Source Overview

- **Dataset**: Canada - forest tenures (draft)
- **Publisher**: Data Basin / James R. Strittholt; original compilation: Global Forest
  Watch Canada
- **License**: Public domain (per use constraint inside each zip archive)
- **Access**: Data Basin gallery (login required for download; acquired via web UI)
- **Format**: Shapefile (4 provincial zips)
- **Gallery URL**: https://databasin.org/galleries/1dd5bbe4e47a4855a2095eae7cf3253a/
- **Created**: 2012-07-02 (draft); geometry dates ~2010
- **Temporal**: ~2010 geometry; 2012 upload to Data Basin

## Acquired Payloads

Located in `tmp/databasin/`:

| File | Provinces | Shapefile | Records |
|------|-----------|-----------|---------|
| `BC_tenures/` | British Columbia | 54.7 MB .shp | 2501 polys, 424 unique tenure IDs |
| `AB_SK_MB_tenures/` | Alberta, Saskatchewan, Manitoba | ~ | ~ |
| `ON_QC_tenures/` | Ontario, Quebec | 64.3 MB .shp | ~ |
| `Atlantic_tenures/` | New Brunswick, Newfoundland, Nova Scotia | 21 MB .shp | ~ |

**BC tenure IDs include**: TFL (Tree Farm Licenses), TSAs (Timber Supply Areas),
UAU (User Area Units), and numeric forest district codes.

**Coverage**: 9 of 13 provinces/territories. Missing: Yukon, Northwest
Territories, Nunavut.

## Key Findings

1. **License**: Public domain — no use restriction beyond attribution to Global
   Forest Watch Canada / Data Basin.
2. **Source lineage**: Global Forest Watch Canada compiled from provincial tenure
   data. BC boundaries compiled from "several different sources" (per CONTENTS.txt).
3. **Spatial unit**: Forest tenure/management area polygons — directly relevant to
   FRESH supply-node spatial unit definitions.
4. **Currency**: Geometry dates ~2010 (shapefile internal dates). 2012 Data Basin
   upload date. Consider stale for active tenure management.
5. **Gap**: No northern territory coverage (YK, NT, NU).

## Status

**Accepted as interim** national forest tenure boundary layer. Replace with more
current authoritative provincial open data when available. BC tenure boundaries are
the highest priority for FRESH supply node spatial definition.

## Decision

**Triage status**: `needs_member_dataset_resolution`

Remain in candidate state until member dataset URL, source lineage, license,
and FMU-boundary fit are confirmed.
