# Canada Base Map Transportation Service (CBMT) — WMS only — Source Review

Review date: 2026-08-09 (Phase 2b Transport Network Acquisition).
Inventory id: `cbmt_wms`.
Status: `candidate` (Phase 2b discovery row — NOT acquired yet).

> **Reminder: CBMT is WMS-only.** CBMT is *not* a vector download and
> *not* a feature service. There is no `cbmt.gpkg`, no `cbmt.shp`, no
> `cbmt.geojson`, and no `cbmt_WFS` endpoint at the GeoGratis catalogue
> for this product. Anyone treating CBMT as a vector source will
> silently introduce a *rendered raster* (often pre-tiled PNG/JPEG
> inside the WMS response) into a layer that is supposed to carry
> routing attributes — and the LP will see "no geometry" for every route.

## Source

- Dataset: Canada Base Map — Transportation Service (CBMT).
  Background reference basemap provided as a Web Map Service only.
- Publisher: Natural Resources Canada (NRCan) — Canada Centre for
  Mapping and Earth Observation (CCMEO).
- Licence: **Open Government Licence - Canada**.
- WMS endpoint (verified URL pattern):
  `https://maps.geogratis.gc.ca/wms/CBMT?service=WMS&version=1.1.1&request=GetCapabilities`.

### URL and licence verification posture

The WMS `GetCapabilities` endpoint above is the standing GeoGratis
CBMT service URL (verified by the WMS service pattern and the OGL
Canada redistribution posture on the GeoGratis catalogue). The
endpoint does *not* need a separate discovery walk for the WMS
service to work, but the team should still issue a `GetCapabilities`
probe from a CI scheduler so that a service outage is recorded in
the standing CSW harvest log.

SAFORAH2 CSW indexing note: the SAFORAH2 record cited by the
researcher shows the CBMT-WMS UUID as `8a0fc8703289c7efc75b919acf588aa5f5ca7ec9`.
**The first 8 hex characters of this id collide visually with NRN's
known UUID (`0e83b48e…`)** — `8a0fc870` vs `0e83b48e`. Researchers
should pull the SAFORAH2 metadata record and verify the UUID-to-product
mapping before any URL is encoded into a saved reference. The
`metadata/source-reviews/cbmt_wms.md` URL above is the GeoGratis
catalogue URL, not the SAFORAH2 CSW UUID — favoured because the
GeoGratis URL is the live WMS endpoint, not a metadata surrogate.

## When to use CBMT

Use CBMT **only** for visual basemap context. Concretely:

1. As the underlying tile layer behind QA maps of NRN, NRWN, or
   jurisdictional forest/resource roads.
2. As the basemap for scouting reports and stakeholder-deliverable
   PDF / PNG renderings where the operator wants a national-context
   underlay.
3. As a sanity backdrop when previewing the SK NFIS, NWT Geomatics,
   or Yukon GeoYukon WMS layers in the same Map composition.

## When NOT to use CBMT

Do **not** use CBMT for any cost calibration, geometry-driven supply
analysis, or routing step. Specifically:

1. Cost calibration — CBMT is a rendered raster; it has no
   per-segment attributes. Use `nrn_vectors` (national road
   centerlines) or the jurisdictional roads in
   `provincial_transport.md` for cost work.
2. Geometric joins — CBMT WMS returns maps, not features.
3. Spatial analysis outside of a visual preview map — CBMT cannot
   punctually resolve an arbitrary (x, y) for a forest supply node
   since there is no `GetFeatureInfo` that survives post-rendering
   re-projection. The CBMT upstream WMS does support a basic
   `GetFeatureInfo` extraction for some layers, but the response
   is rendered-page metadata and should not be assumed
   semantically rich.

## Cross-reference to NRN vectors for cost work

For any Phase 3 cost work, route through:

- `metadata/source-reviews/nrn_vectors.md` — national road
  centerlines (vector download).
- `metadata/source-reviews/provincial_transport.md` — SK
  Highways WMS, NWT Highways + Resource Roads WMS, Yukon
  Forest Resource Roads WFS / WMS.
- `metadata/source-reviews/nrwn_vectors.md` — national rail
  centerlines (vector download).

CBMT is the visual context layer; NRN/NRWN vectors and the
provincial transport review are the cost surfaces.
