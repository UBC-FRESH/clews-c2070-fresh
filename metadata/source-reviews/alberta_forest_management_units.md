# Source Review: alberta_forest_management_units

**Reviewed:** 2026-08-05
**Status:** candidate
**Priority:** medium

## Publisher & License
- **Publisher:** Alberta Environment and Protected Areas, Government of Alberta
- **License:** Open Government Licence - Alberta (OGLA)
- **Access mode:** Cartographic PDFs (no direct GIS download on Open Alberta page)

## Source URL
- Open Alberta: `https://open.alberta.ca/opendata/gda-1faebc46-55bd-4d8a-be73-a213c2681d72`

## Available Resources
| Format | Name |
|--------|------|
| HTML | Metadata record |
| XML | Metadata XML |
| PDF | 1M Base Map + FMU / FM Agreement Holders cartographic series |

## Coverage
- **Spatial:** Alberta
- **Temporal:** 2021-08-27 metadata created; updated 2026-07-16
- **Format:** Cartographic PDF series (1:1,000,000 scale)

## Modelling Use
- FMU and Forest Management Agreement Holder boundaries for Alberta
- **Issue:** No direct GIS download (shapefile/GDB) found on this page — only PDF map sheets
- FMU boundaries likely available via GeoDiscover Alberta geoportal (separate from Open Alberta portal)

## Next Steps
- Search GeoDiscover Alberta (`geodiscover.alberta.ca`) for actual FMU boundary GIS download
- If found, treat as `accepted_direct_download`
- If only PDF cartographic product, downgrade to `reference_only`

## Investigation 2026-08-05
- Open Alberta page (`gda-1faebc46-...`) only lists PDF cartographic maps (1M scale)
- GeoDiscover Alberta geoportal REST API endpoints returned 404/empty — URL structure may have changed
- Direct browser search of GeoDiscover Alberta may be needed to locate actual FMU boundary GIS layer
- **Recommend**: User manually navigates `https://geodiscover.alberta.ca` in a browser to find the FMU/FM Agreement Holder GIS layer, or contacts Alberta EPA for the authoritative download URL
