# National Railway Network — NRWN vector (NRCan GeoGratis) — Source Review

Review date: 2026-08-09 (Phase 2b Transport Network Acquisition).
Inventory id: `nrwn_vectors`.
Status: `candidate` (Phase 2b discovery row — NOT acquired yet).

## Source

- Dataset: National Railway Network — NRWN vector — NRCan GeoGratis
  distribution.
- Publisher: Natural Resources Canada (NRCan) — Canada Centre for
  Mapping and Earth Observation (CCMEO), via the GeoGratis catalogue
  portal.
- Licence: **Open Government Licence - Canada** (verified categorically;
  OGL Canada is the standing GeoGratis licence for NRWN releases).
- Documented catalogue URI (TO-VERIFY on live catalogue):
  `https://geogratis.gc.ca/site/lang-en/data-version/id/13e8e98d-99fb-46c2-a306-86e8d6e94ee7`.
  This is the GeoGratis `data-version/id/<UUID>` landing pattern for the
  NRWN product; the UUID is taken from a SAFORAH2 CSW harvest snapshot.
- Companion Open Canada mirror (already known from the existing `nrwn`
  row in `source-inventory.csv`):
  `https://open.canada.ca/data/en/dataset/ac26807e-a1e8-49fa-87bf-451175a859b8`
  (Statistics Canada / GeoBase statistical mirror).
- Distribution formats: GeoPackage (jurisdiction-bundled GPKG ZIPs) and
  Shapefile (geometry-split SHP ZIPs).
- Web services that index this same NRWN layer (NOT the vector itself):
  - WMS:
    `https://geo.statcan.gc.ca/geo_wa/services/NRWN-RRNW/nrwn_rrnw/MapServer/WMSServer?request=GetCapabilities&service=WMS`
  - ESRI REST:
    `https://geo.statcan.gc.ca/geo_wa/rest/services/NRWN-RRNW/nrwn_rrnw/MapServer`.

### URL and vintage verification posture

The GeoGratis `data-version/id/13e8e98d…` URI above is **TO-VERIFY**. The
researcher-side citation chain was extracted from a SAFORAH2 CSW record
snapshot, not a live fetch; the GeoGratis catalogue page has not been hit
since the snapshot was taken.

Open question for the team: confirm on `geogratis.gc.ca` that the data-version
URI is still live, identify the *current* NRWN release package (file names,
byte sizes, document version), and snapshot the catalogue page so any URL
cited from the parent repo is dated. Add the snapshot to
`metadata/discovery-snapshots/` and reference it from this review.

The 2018 vintage figure is the GeoGratis release vintage most recently seen
in the SAFORAH2 metadata; if the live catalogue has a newer release, update
`temporal_coverage` in `source-inventory.csv` accordingly.

## Schema / attribute fields (vector download)

The NRWN GeoPackage bundles all entities for one railway network, similar
to the NRN bundle pattern. The segmented view used by both GeoBase and the
GeoGratis vector product includes:

- `RAILSEG` — Rail Segment (line layer; primary routing layer for rail
  links to intermodal terminals and ports).
- `RAILLINK` — Rail Link table (associative table that names the
  segment-to-railway ownership join).
- `JUNCTION` — Junction (point layer; interlockings, sidings).
- `STATION` — Station / facility point layer (used to identify rail-served
  nodes for facility cost calibration).
- `TRACK` — Track geometry (multiple parallel tracks carried by one
  segment) — advisory on capacity, not used as a routing attribute.
- `OWNER` / `OPERATOR` — owner / operator attributes (above the segment
  level, for tariff and routing policy).

Common metadata fields mirror the NRN standard: acquisition technique,
coverage, creation date, dataset name, planimetric accuracy, provider,
revision date, and standard version.

`RAILSEG` fields relevant to cost calibration and intermodal routing:

- `RAILCLAS` — rail classification (`Class 1` mainline, `Class 2/3`
  branchline, siding, spur) — strongly predictive of operating-cost
  regime.
- `TRACKTYPE` — single- vs. multi-track.
- `STATUS` — operational / abandoned / disused / under-construction.
- `LOADCAP` / `AXLELOAD` — load capacity (advisory).
- `OWNER`, `OPERATOR` — railway owner / operator.
- Route-name and division fields for tariff lookups.
- `CLOSING` — closing / embargo period.

## Recommended cutoff for cost-calibration Phase 3 use

Use the following cutoff policy until Phase 3 ingestion is authorized:

1. Treat NRWN as a **coarse national baseline** for rail haul distances
   and ownership, not the cost surface itself; per-tonne rail rates come
   from a separate economic study.
2. Include only `RAILCLAS` ∈ {`Class 1 mainline`, `Class 2/3 branchline`}
   in primary rail-served nodes.
3. Exclude `STATUS` ∈ {`abandoned`, `disused`, `under-construction`} from
   operational routing.
4. Retain `OWNER` / `OPERATOR` so tariff-policy scenarios can be modelled
   in Phase 3 sensitivity work alongside the existing
   `metadata/source-reviews/ports.md` (placeholder) facility layer.
5. Use `STATION` to cross-walk the existing `forest_facilities_2013`
   inventory candidate via spatial join.

## Cross-reference to `fresh_fibre.transport_cost.py` (forward use)

The forward plan is to wire rail-vector attributes into
`fresh_fibre/transport_cost.py` (a module not yet present — confirm future
branch before implementation). Today, the LP transport layer is fed by
`transport_link.csv` instances with `transport_cost` and
`transport_cost_unit` fields per
`metadata/ffcm-archive/fresh-interface-contract.csv` row 9, and rail
routing is not yet live.

When `fresh_fibre/transport_cost.py` lands, it should accept the
`RAILSEG-with-RAILCLAS` extract produced by ingesting NRWN vectors via
DataLad into `data/external/nrcan_geogratis/nrwn/`, and emit
`distance_rail_km`, `rail_tariff_CAD_per_t`, and `transport_mode="rail"`
rows keyed to `transport_link.route_id`. The rail leg should plug into
the existing LP transport mode index in `fresh_fibre/lp.py` and the
graph-side cost lookups in `fresh_fibre/graph.py`.

## Open questions + verification actions

The team must validate the following before Phase 3 ingestion is authorized:

1. **Live GeoGratis URL.** Hit
   `https://geogratis.gc.ca/site/lang-en/data-version/id/13e8e98d-99fb-46c2-a306-86e8d6e94ee7`
   and confirm the data-version page resolves; archive the page snapshot
   to
   `metadata/discovery-snapshots/nrwn_vectors_geogratis_snapshot_<date>.html`.
2. **Current vintage.** Identify the latest NRWN package release; record
   the release date and replace `temporal_coverage="2018 vintage"` in
   `source-inventory.csv` with the actual date.
3. **Distribution byte sizes.** Record per-package GPKG and SHP ZIP
   `Content-Length` headers; this is a new attribute for the existing
   `nrwn` row, which the original review did not yet confirm.
4. **Bundle-by-network schema.** Open one GPKG and confirm `RAILSEG`,
   `RAILLINK`, `JUNCTION`, `STATION`, and `OWNER` / `OPERATOR` fields
   match the schema in this review; record any newly added attributes to
   `metadata/source-crosswalks/`.
5. **STATION vs. facility layer.** Decide whether `STATION` will be the
   primary rail-served node source until the
   `forest_facilities_2013` candidate resolves, or whether the two layers
   will cross-walk in the LP via shared IDs.
6. **Ingestion path.** Confirm that `data/external/nrcan_geogratis/nrwn/`
   is the intended DataLad install path and that the parent repo does
   not vendor the GPKG ZIPs.
