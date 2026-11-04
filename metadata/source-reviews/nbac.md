# Source Review: nbac

**Reviewed:** 2026-08-05 (initial), 2026-08-06 (validation)
**Status:** accepted
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

### 2020 Annual Sample (ARCHIVED)
- **File:** `NBAC_2020_20260513.zip`
- **Size:** 7.2 MB
- **Path:** `data/raw/nrcan/nbac/NBAC_2020_20260513.zip`
- **Status:** Validated and archived
- **Contents:** Shapefile (.shp, .dbf, .prj, .shx, .sbn, .sbx, .cpg) + ISO 19115 XML metadata + PDF documentation
- **Validation:** ZIP integrity confirmed; all required components present

### Composite (1972–2025)
- **File:** `NBAC_1972to2025_20260513_shp.zip` (261 MB)
- **Status:** Not yet acquired (lower priority than annual samples for Phase 7)

## Validation Results (2026-08-06)
- [x] ZIP archive valid (Python zipfile module)
- [x] All shapefile components present
- [x] ISO 19115 metadata XML present
- [x] Character encoding: UTF-8
- [x] Projection file present (.prj)

## Integration with CanLaBS v2
- CanLaBS v2 severity layer is built upon NBAC fire perimeters (1985–2024)
- NBAC 2020 sample provides ground-truth validation for CanLaBS 2020 severity values
- Spatial reference should match CanLaBS v2 (NAD83 Canada Lambert)

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

## Risks and Limitations
1. **Annual vs. Composite:** 2020 sample is one year; composite covers 1972–2025
2. **License:** Open Government Licence - Canada assumed; verify against official metadata
3. **Temporal Match:** Ensure NBAC 2020 aligns with CanLaBS 2020 severity layer for validation
