# Source Review: gfw_canada_forest_tenures_2016

**Reviewed:** 2026-08-05
**Status:** ✅ ACCEPTED
**Priority:** high

## Publisher & License
- **Publisher:** Global Forest Watch Canada (compiled from provincial/territorial sources)
- **Compiled by:** markempey (ArcGIS Online)
- **License:** Not explicitly stated on the item page; access is public
- **Access mode:** Direct download (Shapefile ZIP)

## Source URL
- ArcGIS Online: `https://www.arcgis.com/sharing/rest/content/items/aa8797d31edd47209e8eabf1c7789e5c/data`
- Live Feature Service: `https://gis-gfw.wri.org/arcgis/rest/services/country_data/north_america/MapServer/1`

## Available Resources
| File | Size | Description |
|------|------|-------------|
| `Canada_Forest_Tenures_2016.shp` | 289 MB | Main shapefile |
| `Canada_Forest_Tenures_2016.dbf` | 25 MB | Attribute data |
| `Canada_Forest_Tenures_2016.shx` | 29 KB | Shape index |
| `Canada_Forest_Tenures_2016.prj` | 459 B | Projection |
| `Canada_Forest_Tenures_2016.shp.xml` | 28 KB | Metadata XML |

## Coverage
- **Spatial:** Canada — 11 provinces and territories (BC, AB, SK, MB, ON, QC, NB, NL, NS, NT, YT)
- **Temporal:** 2016 (geometry and tenure status)
- **Format:** Shapefile (polygons)

## Dataset Properties
- **Features:** 3,680 tenure polygons
- **Jurisdictions:** 11 (missing only Nunavut)
- **Tenure Types:** Area-based, Volume-based, Area-based and Volume-based
- **Tenure Classes:** 21 classes including Forest Management Agreement, Tree Farm Licence, Timber Licence, Forest Licence, Community Forest, Pulpwood Agreement, Woodlot Licence, etc.
- **Key Attributes:** NAME, CLASS, TYPE, JURISDICTI, COMPANY1-21, Area_km, START_DATE, END_DATE

## Acquisition
- Downloaded 2026-08-05 from ArcGIS Online (192 MB ZIP, expanded 313 MB)
- Copied to `data/raw/gfw/tenures/`
- Archived to DataLad git-annex; content hash locked; pushed to arbutus-s3

## Comparison with databasin_canada_forest_tenures
- **databasin**: 4 provincial zips, 9 provinces, geometry ~2010, public domain
- **GFW 2016**: 3,680 polys, 11 jurisdictions, 2016 vintage, provincial sources compiled
- **Recommendation**: Use GFW 2016 as the canonical national tenures source; databasin becomes supplementary reference

## Modelled Use
- National forest tenure boundary layer for supply-node spatial units
- Harvest area allocation by tenure class and jurisdiction
- Complement to `forest_management_canada` (national management constraint layer) and provincial FMU layers (ON, MB, BC, AB)

## Notes
- Nunavut is absent (not in source data)
- Original provincial sources listed in the item description include: BC (DataBC), AB (Altalis), MB (MLI2), NB (SNB), NL (FPAC), NS (NSGI), ON (LIO), QC (MERN), SK (email request), NT (Tom Lakusta), YT (GeoYukon)
- License should be verified with Global Forest Watch Canada before commercial use
