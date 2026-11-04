# National Road Network — NRN 2.0 vector (NRCan GeoGratis) — Source Review

Review date: 2026-08-09 (Phase 2b Transport Network Acquisition).
Inventory id: `nrn_vectors`.
Status: `candidate` (Phase 2b discovery row — NOT acquired yet).

## Source

- Dataset: National Road Network 2.0 vector — NRCan GeoGratis distribution.
- Publisher: Natural Resources Canada (NRCan) — Canada Centre for Mapping and
  Earth Observation (CCMEO), via the GeoGratis catalogue portal.
- Licence: **Open Government Licence - Canada** (verified categorically; OGL
  Canada is the standing GeoGratis licence for NRN releases).
- Documented catalogue URI (TO-VERIFY on live catalogue):
  `https://geogratis.gc.ca/site/lang-en/data-version/id/0e83b48e-03a9-49b6-a506-1c2827afcf0b`.
  This is the GeoGratis `data-version/id/<UUID>` landing pattern; the same
  UUID (`0e83b48e…`) is *also* cited by SAFORAH2 CSW as the
  NRN record UUID, which is helpful as a cross-check.
- Companion Open Canada mirror (already known from the existing `nrn` row):
  `https://open.canada.ca/data/en/dataset/3d282116-e556-400c-9306-ca1a3cada77f`
  (Statistics Canada / GeoBase statistical mirror).
- Distribution formats: GeoPackage (jurisdiction-bundled GPKG ZIPs) and
  Shapefile (geometry-split SHP ZIPs).
- Web services that index this same NRN layer (NOT the vector itself):
  - WMS:
    `https://geo.statcan.gc.ca/geo_wa/services/NRN-RRN/nrn_rrn/MapServer/WMSServer?request=GetCapabilities&service=WMS`
  - ESRI REST:
    `https://geo.statcan.gc.ca/geo_wa/rest/services/NRN-RRN/nrn_rrn/MapServer`
  - CBMT WMS reference (visual basemap only):
    `https://maps.geogratis.gc.ca/wms/CBMT?service=WMS&version=1.1.1&request=GetCapabilities`.

### URL and vintage verification posture

The GeoGratis `data-version/id/0e83b48e…` URI above is **TO-VERIFY**. The
researcher-side citation chain was extracted from a SAFORAH2 CSW record
snapshot, not a live fetch; the GeoGratis catalogue page has not been hit
since the snapshot was taken.

Open question for the team: confirm on `geogratis.gc.ca` that the data-version
URI is still live, identify the *current* NRN 2.0 release package (file names,
byte sizes, document version), and snapshot the catalogue page so any URL
cited from the parent repo is dated. Add the snapshot to
`metadata/discovery-snapshots/` and reference it from this review.

The 2018 vintage figure is the GeoGratis release vintage most recently seen
in the SAFORAH2 metadata; if the live catalogue has a newer release, update
`temporal_coverage` in `source-inventory.csv` accordingly.

## Schema / attribute fields (vector download)

The NRN 2.0 GeoPackage bundles all entities for one jurisdiction in a single
file. The segmented view used by both GeoBase/Statistics Canada and the
GeoGratis vector product includes the same entities (interactive nomenclature
write-through — the NRCan CCMEO standard mirrors the GeoBase segmentation):

- `ROADSEG` — Road Segment (line layer, primary cost-routing layer).
- `FERRYSEG` — Ferry Connection Segment (line layer).
- `JUNCTION` — Junction (point layer).
- `ADDRANGE` — Address Range table.
- `ALTNAMLINK` — Alternate Name Link table.
- `BLKPASSAGE` — Blocked Passage (point layer).
- `STRPLANAME` — Street and Place Names table.
- `TOLLPOINT` — Toll Point (point layer).

Common metadata carries acquisition technique, coverage, creation date,
dataset name, planimetric accuracy, provider, revision date, and standard
version.

`ROADSEG` fields relevant to cost calibration:

- `ROADCLASS` — functional road class (`Freeway`, `Expressway / Highway`,
  `Arterial`, `Collector`, `Local / Street`, `Local / Strata`,
  `Local / Unknown`, `Alleyway / Lane`, `Ramp`, `Resource / Recreation`,
  `Rapid Transit`, `Service Lane`, `Winter`).
- `NBRLANES` — number of lanes.
- `PAVSTATUS`, `PAVSURF`, `UNPAVSURF` — paved / unpaved and surface type.
- `ROADJURIS` — road jurisdiction.
- `SPEED` — speed restrictions.
- `TRAFFICDIR` — traffic direction.
- Route-name and route-number fields.
- `CLOSING` — closing period (seasonal access).

## Recommended cutoff for cost-calibration Phase 3 use

Use the following cutoff policy until Phase 3 ingestion is authorized:

1. Treat NRN 2.0 vectors as a **coarse national baseline**; do not use them
   alone for operational biomass-haul cost claims.
2. Include `Freeway`, `Expressway / Highway`, `Arterial`, and `Collector`
   `ROADCLASS` values for long-haul trunk-haul nodes and `Local / Street`
   only where it pulls traffic to a mill or terminal.
3. Exclude `Resource / Recreation` from primary cost runs until the
   jurisdiction-specific forest/resource roads are ingested
   (see `metadata/source-reviews/provincial_transport.md`).
4. Retain `ROADJURIS` so that jurisdictional reopen dates and seasonal
   restrictions can be parameterized in Phase 3 (`CLOSING` field).
5. Cross-reference the live transport cost catalog against the existing
   `nrn` (Statistics Canada / GeoBase mirror) row, which has documented
   package byte sizes; if the GeoGratis vector bundles differ, prefer the
   GeoGratis vector for currency and the Statistics Canada mirror for
   provenance cross-walk.

## Cross-reference to `fresh_fibre.transport_cost.py` (forward use)

The forward plan is to wire vector-routable transport attributes into
`fresh_fibre/transport_cost.py` (a module not yet present — confirm future
branch before implementation). Today, the LP transport layer is fed by
`transport_link.csv` instances with `transport_cost` and `transport_cost_unit`
fields per `metadata/ffcm-archive/fresh-interface-contract.csv` row 9, and
the live network layer consumed by `fresh_fibre/lp.py` and `fresh_fibre/graph.py`
is the Government of Yukon / SK NFIS / NWT Geomatics WMS trio covered in
`metadata/source-reviews/provincial_transport.md`.

When `fresh_fibre/transport_cost.py` lands, it should accept the
`ROADSEG-with-ROADCLASS` extract produced by ingesting NRN 2.0 vectors via
DataLad into `data/external/nrcan_geogratis/nrn_2_0/`, and emit
`distance`, `travel_time`, and `transport_cost` columns keyed to
`transport_link.route_id`. The existing
`metadata/ffcm-archive/residue-cost-validation-policy-gates.csv` policy gate
(`transport_cost` row) calls out that FFCM route records are evidence only
and *not* production data — NRN 2.0 vectors are the live production source.

## Open questions + verification actions

The team must validate the following before Phase 3 ingestion is authorized:

1. **Live GeoGratis URL.** Hit
   `https://geogratis.gc.ca/site/lang-en/data-version/id/0e83b48e-03a9-49b6-a506-1c2827afcf0b`
   and confirm the data-version page resolves; archive the page snapshot to
   `metadata/discovery-snapshots/nrn_vectors_geogratis_snapshot_<date>.html`.
2. **Current vintage.** Identify the latest NRN 2.0 package release;
   record the release date and replace `temporal_coverage="2018 vintage"`
   in `source-inventory.csv` with the actual date.
3. **Distribution byte sizes.** Record per-jurisdiction GPKG and SHP ZIP
   `Content-Length` headers; compare against the existing `nrn` row's totals
   (~1.628 GB GPKG, ~1.539 GB SHP) for drift.
4. **Bundle-by-jurisdiction schema.** Open one GPKG (BC recommended) and
   confirm `ROADSEG` fields match the schema in this review; record any
   newly added attributes to `metadata/source-crosswalks/`.
5. **Complementarity with CBMT-WMS.** Decide whether the visual WMS in
   `metadata/source-reviews/cbmt_wms.md` will be retained as a sanity
   layer for QA tiles, or dropped in favour of the vector for the same
   purpose.
6. **Ingestion path.** Confirm that `data/external/nrcan_geogratis/`
   is the intended DataLad install path and that the parent repo does
   not vendor the GPKG ZIPs.
