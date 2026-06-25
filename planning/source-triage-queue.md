# Source Triage Queue

Date prepared: 2026-06-25.

This is the first practical queue for Tyler's dataset discovery work. The goal
is not to download everything; the goal is to decide which sources become
canonical inputs and what work is needed before archiving them in `data/`.

## Triage Rules

- Work from `metadata/source-inventory.csv`.
- Do not store large payloads in the parent repository.
- For each source, verify the official landing page, resource endpoints,
  license, package size, update cadence, and known limitations.
- Use local scratch space or the DataLad-backed `data/` submodule for any test
  downloads.
- Promote only reviewed sources into `metadata/required-datasets.yaml`.

## First Five Sources

1. `scanfi_v2`
   - File list and total storage have been inventoried in
     `metadata/source-file-manifests/scanfi_v2_files.csv`.
   - Read `metadata/source-reviews/scanfi_v2.md`.
   - Confirm or revise which rasters are needed for first-pass forest inventory,
     biomass, age, canopy, and species-composition screening.
   - Decide whether to approve the recommended first-pass subset before any
     raster payloads are archived in `data/`.

2. `nfi_standard_reports`
   - Public report links have been inventoried in
     `metadata/source-file-manifests/nfi_standard_reports_links.csv`.
   - Read `metadata/source-reviews/nfi_standard_reports.md`.
   - Resolve license/re-hosting status before treating archived CSV snapshots
     as open model inputs.
   - Record reporting variables relevant to area, volume,
     biomass, ownership, protection, age, and forest type.
   - Decide whether exact report snapshots should be archived in `data/` or
     used only as source-referenced validation summaries.

3. `canlad`
   - Inventory the CanLaD v1.1 raster products and documentation.
   - Review `pre_canlad_v2` as the companion pre-1985 disturbance-history
     source flagged by Greg.
   - Confirm whether NRCan uses "CanLaD v2" informally for the pre-1985
     companion product, or whether an updated 1985-2024 v2 package is expected
     but not yet exposed in the Open Canada package resources.
   - Identify variables needed for fire, harvest, insect, and other disturbance
     calibration.
   - Compare scope against NFDB/NBAC products before selecting a canonical
     disturbance layer.

4. `nrn`
   - Confirm all province/territory GeoPackage packages.
   - Record package size and schema.
   - Assess whether NRN is sufficient for national haul-distance screening or
     whether jurisdiction-specific forest/resource roads are mandatory.

5. `biomass_inventory_business`
   - Compare FGDB, GeoJSON, and CSV packages.
   - Identify facility, residue, location, and quantity fields.
   - Decide whether it can seed the forest industry/facility layer or only
     supports residue screening.

## Review Outcome Codes

- `accepted`: promote or keep in `metadata/required-datasets.yaml`.
- `needs_license_review`: source is promising but reuse/redistribution is not
  clear enough.
- `needs_access_request`: source may require login, partner request, or manual
  acquisition.
- `deferred`: useful later, not needed for the first modelling baseline.
- `rejected`: not suitable; record rationale in the inventory notes.
