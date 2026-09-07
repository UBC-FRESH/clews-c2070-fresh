# Source Review: Provincial Forest Inventories

**Reviewed:** 2026-08-06
**Status:** Candidate — jurisdiction-specific sub-rows recommended
**Priority:** high

## Overview
Provincial forest inventory data is held by individual provinces/territories.
No single national open portal aggregates all provincial inventory attributes.
Each jurisdiction has distinct publishers, licenses, formats, and update cadences.

## Jurisdiction-by-Jurisdiction Summary

### Alberta (AB)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU Boundaries** | Alberta Forest Management Units | Alberta EPA | OGLA | Cartographic PDF only | **BLOCKER:** No GIS download on Open Alberta; GeoDiscover Alberta API unresponsive |
| **Inventory Maps** | Historical Forest Inventory Phase 1/2/3 | Alberta EPA | OGLA | Raster/Map | West of 4th/5th/6th Meridian; historical only |
| **Yield Curves** | — | — | — | — | Not found on Open Canada; check Alberta Forestry website directly |

**Status:** FMU boundaries blocked (PDF only); inventory maps historical; yield curves unverified.

### British Columbia (BC)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU/TSA Boundaries** | FADM Timber Supply Areas | BC Forests | OGL BC | ESRI REST + download | ✅ Accepted; authoritative |
| **Inventory Attributes** | BC Forest Inventory (BCFI) | BC Forests | OGL BC | GeoPackage/Shapefile | Via DataBC; check `catalogue.data.gov.bc.ca` |
| **Yield Curves** | BC Forest Yield Tables | BC Forests | OGL BC | Tabular/PDF | Check BC Ministry of Forests publications |

**Status:** FMU boundaries accepted; inventory and yield sources need verification via DataBC.

### Manitoba (MB)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU Boundaries** | Manitoba Forest Management Units v4 | MB Environment | OpenMB | ArcGIS Service | ✅ Accepted (2021 vintage) |
| **FML Areas** | Manitoba Forest Management License Areas v4 | MB Environment | OpenMB | ArcGIS Service | Supplementary tenure data |
| **Inventory** | Manitoba Forest Inventory 1970s | MB Environment | OpenMB | Tabular/Raster | Historical; verify if newer inventory exists |
| **Yield Curves** | — | — | — | — | Not found on Open Canada |

**Status:** FMU boundaries accepted; inventory is historical (1970s); yield curves unverified.

### Ontario (ON)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU Boundaries** | Ontario Forest Management Units | ON MNRF | OGL Canada | Shapefile/FGDB/REST | ✅ Accepted |
| **Inventory** | Forest Resource Inventory Term 2 (2018-2028) | ON MNRF | OGL Ontario | Tabular | New find; verify attributes |
| **Yield Curves** | — | — | — | — | Check ON MNRF publications |

**Status:** FMU boundaries accepted; FRI Term 2 needs attribute verification.

### Quebec (QC)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU Boundaries** | — | — | — | — | Not found on Open Canada; check `donneesquebec.ca` |
| **Inventory** | Portrait of forest cover in Quebec | QC Government | CC-BY | Raster | National-scale; check for provincial detail |
| **Inventory** | Forest biomass and carbon in southern Quebec | QC Government | CC-BY | Raster | Southern QC only |
| **Sample Plots** | Temporary/Permanent sample plots | QC Government | CC-BY | Tabular | Field plot data |

**Status:** FMU boundaries not found on Open Canada; inventory sources are CC-BY but may lack provincial FMU detail.

### Saskatchewan (SK)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **FMU/Boundary** | Provincial Forest Boundary Polygon | SK Government | SK Standard Unrestricted Use | Shapefile/GDB | ✅ Authoritative |
| **Inventory** | FRI: Forest stands | SK Government | SK Standard Unrestricted Use | Shapefile/ZIP | Forest stands data |
| **Yield Curves** | — | — | — | — | Not found on Open Canada |

**Status:** FMU boundaries available; inventory data found; yield curves unverified.

### Atlantic Provinces (NB, NS, PE, NL)
| Category | Source | Publisher | License | Format | Notes |
|----------|--------|-----------|---------|--------|-------|
| **New Brunswick** | Forest Communities and Habitats | NB Government | OGL NB | Tabular | Check for FMU/boundary data |
| **Nova Scotia** | — | — | — | — | Not found on Open Canada; check `data.novascotia.ca` |
| **Prince Edward Island** | — | — | — | — | Not found on Open Canada; check `www.princeedwardisland.ca` |
| **Newfoundland** | — | — | — | — | Not found on Open Canada; check `data.gov.nl.ca` |

**Status:** Atlantic sources sparse on Open Canada; direct provincial portal checks needed.

## Key Distinctions

### FMU Boundaries vs Inventory Attributes vs Yield Curves
- **FMU Boundaries:** Spatial polygons defining management units (used for supply-node spatial units)
- **Inventory Attributes:** Forest composition, age, biomass, height (used for biomass estimation)
- **Yield Curves:** Temporal production functions (volume/ha by age class); rarely published as open data

### License Summary by Jurisdiction
- **AB:** Open Government Licence - Alberta (OGLA)
- **BC:** Open Government Licence - British Columbia (OGL BC)
- **MB:** OpenMB Information and Data Use License
- **ON:** Open Government Licence - Ontario
- **QC:** Creative Commons 4.0 Attribution (CC-BY)
- **SK:** Government of Saskatchewan Standard Unrestricted Use Data License (v2.0)
- **NB:** Open Government Licence - New Brunswick
- **NS/PE/NL:** Vary by province

## Recommended Next Steps
1. Verify BC inventory and yield sources via DataBC (`catalogue.data.gov.bc.ca`)
2. Check Quebec FMU boundaries via `donneesquebec.ca`
3. Verify Atlantic province sources via direct provincial portals
4. Search for yield curve data via provincial forestry ministry websites (not Open Canada)
5. Consider `magplot` (Multi-Agency Ground Plot Database) as supplementary yield calibration source

## Evidence
- Open Canada API searches (2026-08-06)
- Existing repo source reviews (ONT, MB, BC FMU already accepted)
- Alberta Open Alberta page (PDF only for FMU)