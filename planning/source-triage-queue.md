# Source Triage Queue

Date updated: 2026-08-08 (SCANFI subset acquisition reconciled; BIMA closed as
not_suitable).

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
   - **Subset reconciled 2026-08-08**: the five authorized 2025 rasters are
     downloaded, checksum-verified, and archived at `data/raw/scanfi/v2/`;
     see `data/manifests/scanfi-v2-2025-acquisition.csv`.
   - Keep the subset-first boundary. Full time-series, species layers, and the
     north extrapolation mask require separate approval; do not treat the six
     heuristic windows as inferential evidence.

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
   - **Resolved 2026-08-05**: Raster payloads are accessible. Two subdirectories
     confirmed: `Latest_filtered_CAN_20260508/` (5 latest mosaics, ~2.5 GB) and
     `TS_FinalFilter_20260508/` (42 annual rasters, ~3.3 GB, 1984–2025). Total
     ~5.8 GB. License: OGL Canada. Updated to `accepted` in source-inventory.csv.
   - `pre_canlad_v2` is accepted as a companion source; mirroring its four core
     rasters and documentation remains pending PI approval.

3a. `canlabs_v2`
    - **Open 2026-08-08**: Source metadata and the official FTP endpoint are
      documented, but the local primary severity TIFF is not safe to use or
      archive. Its 1,787,556,259-byte local size exceeds the observed server
      Content-Length of 1,751,083,497 bytes.
    - Resolve the transfer with a fresh download and SHA-256 comparison before
      DataLad archiving or model use. Do not treat structural BigTIFF
      readability as proof of provenance.

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

7. `cwfis_nfdb_fire_polygons`
   - **Resolved 2026-08-05**: NFDB polygon data directly downloadable. 742 MB all
     perimeters, 647 MB large-fire subset, 35 MB pre-1972. OGL Canada. Updated
     to `accepted`.

8. `cwfis_nfdb_fire_points`
   - **Resolved 2026-08-05**: NFDB point data directly downloadable. 1 GB all
     points, 65 MB large-fire subset. OGL Canada. Updated to `accepted`.

7. `forest_management_canada`
   - **Resolved 2026-08-05**: Accepted as national management constraint layer.
     NRCan's "Map of Forest Management in Canada" — OGL Canada, direct download
     via ca.nfis.org (TIFF 2020, TIFF 2017, FGDB 2017 vector). Generalized
     classification of protected areas, Treaty/Settlement Lands, and forest
     management zones. Use for management constraint screening; use databasin
     tenures for supply-node spatial unit boundaries. Source review created.

6. `databasin_canada_forest_tenures`
   - **Resolved 2026-08-05**: Superseded by `gfw_canada_forest_tenures_2016` (see
     below). Databasin zips remain as reference but are no longer the canonical
     source. Status updated to `accepted_superseded`.

9a. `gfw_canada_forest_tenures_2016`
    - **Resolved 2026-08-05**: Downloaded from ArcGIS Online (item aa8797d,
      owner markempey). 3,680 tenure polygons, 11 jurisdictions (BC, AB, SK, MB,
      ON, QC, NB, NL, NS, NT, YT — missing NU only), 2016 vintage. 313 MB.
      Source: Global Forest Watch Canada compiled from provincial/territorial
      government sources. Live feature service at
      `gis-gfw.wri.org/arcgis/rest/services/country_data/north_america/MapServer/1`.
      Archived to DataLad (git-annex + arbutus-s3). Status: `accepted` as
      canonical national forest tenure boundary layer. Source review created.
      Supersedes `databasin_canada_forest_tenures` zips (9 provinces, ~2010).

9. `ontario_forest_management_units`
   - **Resolved 2026-08-05**: OGL Canada, ESRI REST + shapefile/FGDB via Open
     Canada. Official ON provincial layer — more detail than national
     `forest_management_canada` for ON. Source review created.

10. `manitoba_forest_management_boundaries`
    - **Resolved 2026-08-05**: OGL Canada, ArcGIS tile service only on Open
      Canada. 2021 MB boundaries (forest sections, FMUs, provincial forests,
      FMLAs). Source review created.

11. `alberta_forest_management_units`
    - **Open 2026-08-05**: OGL Alberta, but only PDF cartographic maps found on
      Open Alberta. Actual GIS boundary data may be on GeoDiscover Alberta
      geoportal. Source review created; need to resolve GIS access before
      accepting.

12. `bc_fadm_timber_supply_areas`
    - **Resolved 2026-08-05**: OGL BC, ESRI REST + direct download via DataBC.
      Authoritative BC TSA boundaries. Also see FEMIC/external/femic-public-data
      for BC FADM layer as local mirror. Source review created.

13. `nfdp_harvest_tables`
    - **Open 2026-08-05**: Tableau-based with CSV export. License/redistribution
      rights unclear. Download CSV exports manually and verify license before
      promoting. Source review created.

14. `eccc_forest_management_disturbances`
    - **Resolved 2026-08-05**: Small CSV summary files (~7.5 kB total) downloaded
      to tmp/. Accepted as reference_only. Source review updated.

15. `cpcad`
    - **Resolved 2026-08-05**: OGL Canada, FGDB + ESRI REST + WMS. Accepted as
      national protected/conserved areas constraint layer. Source review created.

16. `nbac`
    - **Resolved 2026-08-05**: OGL Canada, annual zips at cwfis.cfs.nrcan.gc.ca/
      downloads/nbac/. Accepted; 2020 sample archived to DataLad. Composite
      ~756 MB download interrupted; annual subsets work. Source review created.

## Current Status

- `scanfi_v2`: first-pass review complete; the authorized five-file 2025 subset
  is acquired and verified. Further expansion remains blocked pending separate
  authorization.
- `canlabs_v2`: source accepted with a provenance caveat; primary severity
  payload remains blocked pending a fresh transfer and checksum comparison.
- `nfi_standard_reports`: first-pass review complete; license/re-hosting
  decision remains open.
- `canlad`: first-pass review complete; raster payload access was confirmed on
  2026-08-05 and the source is accepted in the inventory. Archive or use only
  the documented payload subset; `pre_canlad_v2` remains the companion source
  pending PI approval to mirror.
- `nrn`: accepted as a coarse national haul-distance baseline; operational
  forest/resource road follow-up remains open.
- `biomass_inventory_business`: **not_suitable** — inspected 2026-08-05; BIMA is
  agricultural biomass (crop residues, farmland MSW), NOT forest biomass.
  No forest facility locations or mill residue data. Closed; see
  `metadata/source-reviews/biomass_inventory_business.md` for full rationale.
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
