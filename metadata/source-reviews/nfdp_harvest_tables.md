# Source Review: nfdp_harvest_tables

**Reviewed:** 2026-08-05
**Status:** candidate
**Priority:** medium

## Publisher & License
- **Publisher:** Canadian Forest Service / National Forestry Database (NFDP/CCFDM)
- **License:** Terms of use unclear — NFDP is a collaborative product; user should verify redistribution rights before open archiving
- **Access mode:** Web (Tableau visualizations with CSV/Excel export)

## Source URL
- Main: `http://nfdp.ccfm.org/en/data/harvest.php`

## Available Data
- Area harvested by province, tenure, harvest method, year
- Net merchantable volume of roundwood by category and tenure
- Tableau-based interactive visualizations with downloadable CSV exports:
  - `1-harvest-and-supply.csv`
  - `2-number-of-forest-fires.csv`
  - `3-area-burned.csv`
  - `4-insect-defoliation.csv`
  - `5-annual-deforestation.csv`

## Coverage
- **Spatial:** Canada by province
- **Temporal:** Annual series (varies by table)
- **Format:** CSV/Excel via Tableau export

## Modelled Use
- Harvest calibration reference (area, volume by log type, tenure, method)
- Provincial yield curve validation
- Disturbance history cross-checks
- Useful for `provincial_yield_curves` and harvest calibration tasks in P7.1

## Issues
- Tableau-based (requires browser interaction to export — not a direct download URL)
- License/redistribution rights not explicitly stated on the page
- Treat as reference/validation data until license is confirmed

## Next Steps
- Download CSV exports manually or via headless browser
- Clarify NFDP/CCFDM data redistribution terms before adding to required-datasets.yaml
