# Source Review: nbac

**Reviewed:** 2026-08-05
**Status:** candidate
**Priority:** high

## Publisher & License
- **Publisher:** Natural Resources Canada, Canadian Forest Service
- **License:** Open Government Licence - Canada (assumed; consistent with CWFIS/NFDB products)
- **Access mode:** Direct download (annual ZIPs or composite shapefile)

## Source URL
- Root: `https://cwfis.cfs.nrcan.gc.ca/downloads/nbac/`
- Composite: `NBAC_1972to2025_20260513_shp.zip` (all years)
- Annual: `NBAC_YYYY_20260513.zip` (yearly parcels, 1972–present)

## Available Resources
- Annual burned-area polygons (shapefile) per year from 1972
- Composite national dataset: `NBAC_1972to2025_20260513_shp.zip`
- PNG summary: `NBAC_1972to2025.png`
- Metadata PDF: `NBAC_1972to2025_20260513_shp_metadata.pdf`

## Coverage
- **Spatial:** Canada (national)
- **Temporal:** 1972–present (annual)
- **Format:** Shapefile

## Acquisition
- Download attempted 2026-08-05; composite ZIP (261 MB) in progress
- Will archive to DataLad upon completion

## Modelled Use
- Annual burned-area polygons — best-available national BAU product
- Complement to NFDB polygons: NBAC provides the "corrected" burned perimeters (vs. raw NFDB fire reports)
- Use for annual disturbance masking, carbon accounting, and fire return interval mapping
- **Key advantage over NFDB**: NBAC is the corrected/validated burned area, NFDB is the source fire record

## Relationship to NFDB
- NFDB polygons: raw fire perimeters from provincial/territorial reporting
- NBAC: validated/standardized burned area polygons derived from NBAC methodology
- Both are OGL Canada; NBAC is the "corrected" product for spatial analysis

## Notes
- Annual ZIPs allow time-series analysis by year
- Metadata PDF describes classification methodology
- Recommend downloading composite first, then annual subsets as needed
