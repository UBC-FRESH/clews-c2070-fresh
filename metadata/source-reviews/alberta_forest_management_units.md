# Source Review: alberta_forest_management_units

**Reviewed:** 2026-08-05 (initial), 2026-08-06 (Phase 7 update)
**Status:** ⚠️ BLOCKED — No GIS layer evidenced
**Priority:** medium

## Publisher & License
- **Publisher:** Alberta Environment and Protected Areas, Government of Alberta
- **License:** Open Government Licence - Alberta (OGLA)
- **Access mode:** Cartographic PDFs only (no direct GIS download)

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
- **BLOCKER:** No direct GIS download (shapefile/GDB/REST) found on Open Alberta page
- Only cartographic PDF maps available at 1:1,000,000 scale

## Investigation History

### 2026-08-05 (Initial)
- Open Alberta page only lists PDF cartographic maps
- GeoDiscover Alberta geoportal REST API endpoints returned 404/empty
- URL structure may have changed

### 2026-08-06 (Phase 7 Follow-up)
- GeoDiscover Alberta API (`geodiscover.alberta.ca/api/v1/layers`) timed out on multiple attempts
- Alberta Open Data JSON endpoint (`open.alberta.ca/opendata/...json`) returned 404
- No alternative API endpoints identified
- **Conclusion:** Cannot verify GIS layer existence via automated endpoints

## Blocker
- **No actual GIS layer can be evidenced** through public endpoints
- GeoDiscover Alberta API is unresponsive (timeout)
- Open Alberta page only exposes cartographic PDF series
- Actual GIS data may exist but is not accessible via documented endpoints

## Recommended Actions
1. **Manual browser check:** User navigates `https://geodiscover.alberta.ca` to search for FMU/FM Agreement Holder layers
2. **Direct contact:** Contact Alberta EPA to request authoritative GIS download URL
3. **Alternative:** Use national `forest_management_canada` layer for Alberta (already accepted) as fallback
4. **Do NOT claim GIS layer exists** without verified download endpoint

## Fallback Option
- Use **national `forest_management_canada`** layer (already accepted) for Alberta FMU boundaries
- This layer includes Alberta FMU classifications at national scale
- Less detailed than provincial source but available and licensed (OGL Canada)

## Status Summary
| Aspect | Status |
|--------|--------|
| PDF cartographic maps | ✅ Available (Open Alberta) |
| GIS shapefile/GDB | ❌ Not evidenced |
| REST service | ❌ API unresponsive |
| Fallback (national layer) | ✅ Available (already accepted) |

## Decision
- **Primary source (AB FMU GIS):** BLOCKED — no GIS layer evidenced
- **Fallback:** Use national `forest_management_canada` layer for Alberta
- **Next step:** Manual GeoDiscover check or direct Alberta EPA contact required to unblock
