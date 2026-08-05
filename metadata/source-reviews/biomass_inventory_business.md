# Biomass Inventory Mapping and Analysis - Business Data Source Review

**Review date**: 2026-08-04
**Updated**: 2026-08-05 — field inspection complete
**Source ID**: `biomass_inventory_business`
**Status**: `not_suitable` — agricultural biomass, not forest biomass

## Source Overview

- **Dataset**: Biomass Inventory Mapping and Analysis (BIMA) business data
- **Publisher**: Agriculture and Agri-Foo
- **License**: Open Government Licence - Canada
- **Access**: Direct download
- **Format**: FGDB/GDB, GeoJSON, CSV, PDF documentation
- **URL**: https://open.canada.ca/data/en/dataset/1a759d95-3008-4078-87af-5bb1bdf657b3
- **Temporal**: Current from source (metadata modified 2025-10-15)
- **Published**: 2013-04-16

## Package Inventory

Inspected 2026-08-05 via direct download from Open Canada portal.

**Available formats**:
- `BIOMASS_MSW_INV.csv` — 1,843 rows: Municipal solid waste (organic, paper) volumes by census farmland grid cell
- `BIOMASS_AG_INV.csv` — Agriculture crop area, yield, and residue data by grid cell
- `BIOMASS_AG_INV_10.csv` — Same at 10 km grid resolution
- `BIOMASS_AG_INV_20.csv` — Same at 20 km grid resolution
- `BIOMASS_INV_CT_FGDB.zip` (~11 MB) — Same tables with geometry (census farmland grid polygons)
- `BIOMASS_REPORT_FRMWRK_FGDB.zip` (~11 MB) — Reporting framework/lookup tables

**Schema (MSW)**:
- `OBJECTID`, `BIOMASS_GRID_ID`, `MNCPL_SOLID_WASTE_TOTAL_VOL`, `MNCPL_SOLID_WASTE_ORGANIC_VOL`, `MNCPL_SOLID_WASTE_PAPER_VOL`
- Volumes in cubic metres (estimated)
- `BIOMASS_GRID_ID` links to Census farmland enumeration areas, NOT forest inventory

**Schema (AG)**:
- Crop area (barley, corn, flax, oat, wheat, canola, soybean)
- Crop yield and quantity
- Residue yield (chaff, straw, stover) by tillage practice
- Livestock straw requirements

**No forest-related content confirmed.**

## Intended Modelling Role

Per `source-inventory.csv`: intended for industry/facility layer seeding or
residue screening.

## Key Questions

1. Does the package contain facility location point data?
2. Are residue type, quantity, and source (mill type) fields present?
3. What is the spatial unit of the data (facility-level,承包 region-level)?
4. What is the update cadence and last refresh date?
5. Does the FGDB contain geometry or only attributes?
6. Are there BioFiber or other specific commodity fields relevant to forest
   biomass supply?

## Recommended Next Steps

1. Download the FGDB or CSV package from the Open Canada portal
2. Inspect feature class or table schema for facility/residue fields
3. Assess fit for forest industry/facility layer or residue screening
4. Determine whether exact facility locations can be used in model or only
   aggregate residue quantities

## Decision

**Triage status**: `not_suitable`

**Rationale**: BIMA is agricultural biomass inventory (crop residues, municipal solid waste from farmland areas). It does NOT contain:
- Forest facility locations
- Mill residue data
- Forest biomass supply data
- Any content relevant to the forest industry/facility layer or forest residue screening

The dataset is correctly named "Biomass Inventory Mapping and Analysis" from Agriculture and Agri-Food Canada — its scope is farmland biomass, not forest biomass.

**Recommended actions**:
1. Mark `biomass_inventory_business` as `not_suitable` in `source-inventory.csv`
2. Do NOT archive or promote to `required-datasets.yaml`
3. Close P7.1 sub-task for this item
4. Look for forest industry facility data elsewhere (e.g., NRCan's "Forest Industry Facility Database" or provincial timber facility lists)
