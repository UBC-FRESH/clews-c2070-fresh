# Provincial / Territorial Transport Layers — Source Review

Review date: 2026-08-09 (Phase 2b Transport Network Acquisition).
Inventory ids covered: `sk_highways_wms`, `nwt_highways_and_resource_roads_wms`,
`yk_forest_resource_roads_50k`. New Brunswick (`nb_*`) remains a future
expansion slot — see §4.

This review aggregates the provincial / territorial WMS (and where present
WFS / direct-download) transport sources that complement the national
NRN / NRWN vectors covered in `nrn_vectors.md` and `nrwn_vectors.md`.
The provincial layers carry operational forest-resource roads that the
national baseline either omits or lags, and they are the natural follow-up
to the SAFORAH2 CSW discovery pass.

## §1. Saskatchewan — SK Highways (Saskatchewan NFIS WMS)

- Inventory id: `sk_highways_wms`.
- Theme: `transport`.
- Status: `candidate` (Phase 2b discovery row).

### Source

- Dataset: Highways layer on the Saskatchewan NFIS MapServer.
- Publisher: Government of Saskatchewan — Ministry of Environment
  (NFIS partnership with NRCan-CFS).
- Licence: **Open Government Licence - Saskatchewan (TO-VERIFY)** —
  the SK NFIS mapserver historically carries OGL-Saskatchewan terms,
  but a licence-field verify pass is required before archived
  payloads.
- WMS endpoint (verified URL pattern):
  `https://sk.nfis.org/mapserver/cgi-bin/skmap.cgi?service=WMS&version=1.1.1&request=GetMap&layers=Highways`.
- Reachability: TO-VERIFY (responsive probe from CI).

### Attribute summary

- Single `Highways` layer; line geometry, province-wide.
- Standard NFIS MapServer attributes: `ROADCLASS`, `PAVSURF`,
  `ROADJURIS` — typically comparable to NRN `ROADSEG` fields.
- No forest-resource-road layer is exposed by the same mapserver at
  the live snapshot; jurisdiction-specific forest roads for SK remain
  in the `sk_forest_resource_roads` candidate (transport / SK geoDiscover).

### Verification actions

1. Issue a WMS `GetCapabilities` probe against
   `https://sk.nfis.org/mapserver/cgi-bin/skmap.cgi?` and record the
   document version and `LAYER=HIGHWAYS` reachability.
2. Confirm licence field via SK NFIS attribution page or via
   `metadata/discovery-snapshots/sk_highways_wms_<date>.xml`.
3. Tag the source row with `sub-Linke` rating once licensed,
   archived, and SHA256-valid.

## §2. Northwest Territories — Highways + Resource Roads (GNWT Transportation WMS)

- Inventory id: `nwt_highways_and_resource_roads_wms`.
- Theme: `transport`.
- Status: `candidate` (Phase 2b discovery row).

### Source

- Dataset: GNWT Transportation_LCC ArcGIS MapServer.
- Publisher: Government of the Northwest Territories (GNWT) —
  Lands (LANDS), Infrastructure (INF), Environment & Natural
  Resources (ENR / CIMP), and the Centre for Geomatics.
- Licence: **OGL-canada-equivalent (TO-VERIFY)** — the GNWT Centre
  for Geomatics redistributes portal layers under OGL-aligned terms;
  a licence-field verify pass is required before archived payloads.
- WMS endpoint (verified URL pattern):
  `https://www.apps.geomatics.gov.nt.ca/arcgis/services/GNWT/Transportation_LCC/MapServer/WmsServer?service=WMS&version=1.1.1&request=GetMap&layers=0`.
  Layer `0` is the `Highways` line layer.
- Companion layer (per SAFORAH2 metadata):
  `https://www.apps.geomatics.gov.nt.ca/arcgis/services/GNWT/Transportation_LCC/MapServer/WmsServer?…&layers=1` —
  Resource Roads line layer.
- Combined coverage: subprovincial_NWT + buffer (resource roads
  extend into AB / SK / YT / NU borders, hence the buffer label).

### Attribute summary

- `Highways` layer (`layers=0`) — FEATURECODE `RHWYLIN` per
  SAFORAH2 metadata snapshot.
- `Resource Roads` layer (`layers=1`) — FEATURECODE `RRESLIN` per
  SAFORAH2 metadata snapshot.
- Both inherit GNWT standard attributes: `ROADCLASS`, surface
  type, jurisdiction.
- The combined layer is the most operationally relevant national
  forest-access source for the boreal north, and is the only
  inventory row that explicitly exposes a `Resource Roads`
  layer alongside a `Highways` layer in a single MapServer.

### Verification actions

1. Issue a WMS `GetCapabilities` probe against the GNWT Transportation
   MapServer and validate both `layers=0` (RHWYLIN) and `layers=1`
   (RRESLIN).
2. Confirm licence via `metadata/license-determinations/` once the
   legal-team review signs.
3. Cross-walk against the existing `nt_forest_resource_roads`
   candidate in `source-inventory.csv` to deduplicate jurisdictions
   once NWT Geomatics resource roads are bundled.

## §3. Yukon — Forest Resource Roads 1:50,000 (Yukon GeoYukon)

- Inventory id: `yk_forest_resource_roads_50k`.
- Theme: `transport`.
- Status: `candidate` (Phase 2b discovery row).

### Source

- Dataset: Forest Resource Roads 1:50,000 base — Yukon GeoYukon
  forestry-themed MapServer / feature service.
- Publisher: Government of Yukon — Energy, Mines and Resources
  (EMR) Geomatics, via the GeoYukon open-data portal.
- Licence: **OGL-canada-equivalent / Yukon Open Government Licence
  (TO-VERIFY)** — Yukon Open Government Licence is the standing
  redistribution posture for the GeoYukon portal; a confirmatory
  licence pass is required before archived payloads.
- WMS endpoint:
  `https://mapservices.gov.yk.ca/arcgis/services/GeoYukon/GY_Forestry/MapServer/WMSServer?service=WMS&version=1.3.0&request=GetMap&layers=31`.
- WFS / feature service available at the same root
  `…/MapServer/WMSServer` (also exposed as
  `…/MapServer/FeatureServer?f=json`).
- Vector-download option: TO-VERIFY — Yukon GeoYukon frequently
  exposes a sibling shapefile / GeoJSON download at the same EMRC
  portal.

### Attribute summary

- 1:50,000 scale forest resource roads in Yukon.
- Layer index `31` in the GY_Forestry MapServer; line geometry.
- Standard Yukon forestry attributes: `ROADCLASS`, surface, owner,
  operational status — typical Yukon GeoYukon schema.
- Coverage: subprovincial_YT with patchy coverage outside the
  contiguous road network (resource roads only).

### Verification actions

1. Probe the WMS endpoint and confirm `layers=31` is the
   `Forest Resource Roads 50k` layer.
2. Probe the feature server / WFS endpoint and decide between
   direct WFS pull vs. shapefile download for Phase 3 ingestion.
3. Confirm licence via `metadata/license-determinations/`.
4. Cross-walk against the existing `yt_forest_resource_roads`
   candidate in `source-inventory.csv` to deduplicate jurisdiction
   once the Yukon Open Data portal download manifests match.

## §4. New Brunswick — Future Expansion Slot

- Inventory id: `nb_forest_resource_roads_…` (placeholder — do NOT
  invent a slug yet).
- Theme: `transport`.
- Status: NOT ADDED. Awaiting Phase NB-expansion scope-open.

### Why the slot exists

The Future-NB slot is held in this review for forward planning only.
The current top-level inventory row `nb_forest_resource_roads` already
covers the NB Department of Natural Resources and Energy Development
portal candidate (`https://geonb.snb.ca`). When a NB-specific WMS or
WFS layer is identified (analogous to the SK / NWT / YT pattern above),
a new top-level inventory row will be added (estimated slug
`nb_forest_resource_roads_wms`) and a corresponding sub-section
appended to this review.

### Verification actions when NB opens

1. Probe the GeoNB portal for a `Roads` / `Forest Roads` ArcGIS
   MapServer or WMS endpoint.
2. If only a feature service is exposed (no WMS), record both the
   WFS and the feature service URLs in `source_url` and flag
   `access_mode=wfs_or_direct_download`.
3. Confirm licence posture (typically OGL-aligned for GeoNB).

## §5. Cross-reference to `calibrated_cost_surfaces` readiness gate

The `calibrated_cost_surfaces` gate in `metadata/readiness-gates.yaml`
is currently `blocked` and lists `nrn` and `forest_resource_roads`
as blocking datasets. Operator-facing revisions made by Phase 2b:

- `nrn_vectors` and `nrwn_vectors` (this batch) — see
  `metadata/source-reviews/nrn_vectors.md` and
  `metadata/source-reviews/nrwn_vectors.md` for the vector baselines.
- Provincial WMS / WFS rows in this review are follow-up sources
  required to resolve operational forest access; the gate's
  `notes` field is extended to record the SAFORAH2 / GeoGratis
  discovery cycle and the Phase 3.10 acquisition plan.

Until the readiness gate flips to `in_progress`, no Phase 3 cost
calibration may use any of these vector or WMS layers for production
results. The diagnostic LP transport table
(`metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/transport_link.csv`)
remains the temporary carrier of synthetic costs per the standing
`transport_cost` row in
`metadata/ffcm-archive/residue-cost-validation-policy-gates.csv`.
