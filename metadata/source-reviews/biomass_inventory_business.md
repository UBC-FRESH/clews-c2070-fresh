# Biomass Inventory Mapping and Analysis - Business Data Source Review

**Review date**: 2026-08-04
**Source ID**: `biomass_inventory_business`
**Status**: `triage_ready`

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

Not yet inventoried. Next action: download and inspect the package contents
to identify facility, residue, location, and quantity fields.

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

**Triage status**: `needs_field_inspection`

Do not archive or promote until field-level inspection confirms fit.
