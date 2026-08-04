# Source Triage Queue

Date updated: 2026-08-04.

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

## Current Queue

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
   - Read `metadata/source-reviews/canlad_and_pre_canlad_v2.md`.
   - `canlad` is flagged `needs_access_resolution`: the README names 45 raster
     products, but the public directory currently exposes only support files and
     exact raster URLs return 404.
   - `pre_canlad_v2` is accepted as a manageable companion source, pending PI
     approval to mirror the four core rasters.
   - Follow up with NRCan or monitor the Open Canada package for corrected
     1985-2025 CanLaD raster payload links.

4. `nrn`
   - File list and package sizes have been inventoried in
     `metadata/source-file-manifests/nrn_files.csv`.
   - Read `metadata/source-reviews/nrn.md`.
   - Use NRN as a coarse national haul-distance baseline when exact GPKG
     release ZIPs are archived for model outputs.
   - Continue jurisdiction-specific forest/resource road triage before
     operational access or delivered-cost claims.

5. `biomass_inventory_business`
   - Compare FGDB, GeoJSON, and CSV packages.
   - Identify facility, residue, location, and quantity fields.
   - Decide whether it can seed the forest industry/facility layer or only
     supports residue screening.

6. `databasin_canada_forest_tenures`
   - Start from the user-flagged gallery:
     `https://databasin.org/galleries/1dd5bbe4e47a4855a2095eae7cf3253a/`.
   - Verify whether the likely member dataset, `Provincial Forest Tenures -
     Forest Management Areas (draft)`, is actually contained in the gallery:
     `https://databasin.org/datasets/2f20af8c4f7949ad8ab631b77dc733c3/`.
   - Resolve the member dataset download endpoint, source lineage, spatial
     unit semantics, license, update status, and fit to the FRESH FMU/supply
     node boundary contract.
   - Materialize accepted payloads only through the DataLad-backed `data`
     repository after the access and provenance checks are complete.

## Current Status

- `scanfi_v2`: first-pass review complete; subset-first mirroring remains the
  recommended path. Phase 6 acquisition blocked pending explicit authorization.
- `nfi_standard_reports`: first-pass review complete; license/re-hosting
  decision remains open.
- `canlad`: first-pass review complete; raster payload access remains
  unresolved. `pre_canlad_v2` is the manageable companion candidate.
- `nrn`: accepted as a coarse national haul-distance baseline; operational
  forest/resource road follow-up remains open.
- `biomass_inventory_business`: reviewed 2026-08-04; source review created at
  `metadata/source-reviews/biomass_inventory_business.md`; field-level
  inspection of FGDB/CSV package needed before archive decision.
- `databasin_canada_forest_tenures`: reviewed 2026-08-04; source review
  created at `metadata/source-reviews/databasin_canada_forest_tenures.md`;
  member dataset URL, license, provenance, and FMU-boundary fit require
  resolution before materialization. Remains candidate state.

## Review Outcome Codes

- `accepted`: promote or keep in `metadata/required-datasets.yaml`.
- `needs_license_review`: source is promising but reuse/redistribution is not
  clear enough.
- `needs_access_request`: source may require login, partner request, or manual
  acquisition.
- `needs_access_resolution`: public metadata and payload access are
  inconsistent enough that maintainer follow-up is required.
- `deferred`: useful later, not needed for the first modelling baseline.
- `rejected`: not suitable; record rationale in the inventory notes.
