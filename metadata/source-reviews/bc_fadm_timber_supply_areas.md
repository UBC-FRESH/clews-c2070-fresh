# Source Review: bc_fadm_timber_supply_areas

**Reviewed:** 2026-08-05
**Status:** ✅ ACCEPTED
**Priority:** high

## Publisher & License
- **Publisher:** BC Ministry of Forests, Forest Tenures Branch (via DataBC Catalogue)
- **License:** Open Government Licence - British Columbia
- **Access mode:** Direct download + ESRI REST

## Source URL
- DataBC: `https://catalogue.data.gov.bc.ca/dataset/fadm-timber-supply-areas-tsa-view`

## Available Resources
| Format | Name |
|--------|------|
| ESRI REST View | FADM - Timber Supply Areas (TSA) - View (3 resources) |
| HTML | Metadata |

## Coverage
- **Spatial:** British Columbia
- **Temporal:** Current (as-maintained)
- **Format:** Vector polygons (ESRI service)

## Modelled Use
- Timber Supply Area boundaries for BC
- Supply-node spatial unit boundaries for BC
- Authoritative BC provincial source via DataBC
- **Note:** BC FADM management areas also available in FEMIC external data at `femic/external/femic-public-data/` — compare for completeness

## Relationship to FEMIC
- FEMIC external data contains FADM layer (BC Forest Analysis and Inventory Materials)
- DataBC TSA view may be more current or have different geometry than FEMIC copy
- Use DataBC as authoritative source; FEMIC copy as local mirror

## Notes
- OGL BC compatible with commercial use
- ESRI REST API accessible for on-demand feature extraction
- Consider archiving a snapshot of the TSA boundaries to DataLad
