# C2070 OSeCAN 13-Zone Crosswalk

**Status:** authoritative zone/province extraction from the public OSeCAN code, with
FRESH-side boundary notes clearly marked provisional.

**Source pin:** `deltae/C2070` public repository, commit `40d5b43`
(`40d5b43bb3f8b3f69c16e953144d35eeed5f0f44`), submodule path
`runtime/osecan_model/`.

**Data artifact:** `data/derived/national/clews/osecan/zone_crosswalk.csv`
(data submodule commit `89c5ac3`).

**Read-only guarantee:** no file in `runtime/osecan_model/` was modified by this
extraction. The submodule has pre-existing uncommitted local edits (additive
FRESH biomass edits in
`src/osecan/{model_structure,build_params,sets_n_ratios}.py`, plus a coal-bound
change in `config/capacity.yaml:116-118`); the province/zone block in
`model_structure.py` and the `zone_capacity_share` block in `capacity.yaml` are
unchanged from `40d5b43` and are the authority used here.

## 1. Authoritative zone list

The model has exactly **13 zones over 9 OSeCAN province groups**. The four
largest provinces are split into two zones; the rest are single-zone. The
declaration is `runtime/osecan_model/src/osecan/model_structure.py:66-88`.

| Zone | Zone name (exact code label) | OSeCAN province group | Source |
|---|---|---|---|
| B1 | South Coast / Lower Mainland | BC | `model_structure.py:75` |
| B2 | Interior & North | BC | `model_structure.py:76` |
| A1 | Calgary–Edmonton corridor | AB | `model_structure.py:77` |
| A2 | North (oil sands) | AB | `model_structure.py:78` |
| S1 | Saskatchewan | SK | `model_structure.py:79` |
| M1 | Manitoba | MB | `model_structure.py:80` |
| O1 | Southern Ontario | ON | `model_structure.py:81` |
| O2 | Northern Ontario | ON | `model_structure.py:82` |
| Q1 | Southern Quebec | QC | `model_structure.py:83` |
| Q2 | Northern Quebec (hydro) | QC | `model_structure.py:84` |
| T1 | Atlantic | AT (NB+NS+PE) | `model_structure.py:85` |
| L1 | Newfoundland & Labrador | NL | `model_structure.py:86` |
| N1 | North (territories) | NO (YT+NT+NU) | `model_structure.py:87` |

Cross-check: `runtime/osecan_model/docs/model-description.md:50-57` prints the
same 13-zone list, and `docs/model-description.md:278` states "Zones | 13
(fixed-width 2-char codes)".

The 9 province groups are declared at `model_structure.py:66-70`:

| OSeCAN group | Full label | Zones |
|---|---|---|
| BC | British Columbia | B1, B2 |
| AB | Alberta | A1, A2 |
| SK | Saskatchewan | S1 |
| MB | Manitoba | M1 |
| ON | Ontario | O1, O2 |
| QC | Quebec | Q1, Q2 |
| AT | Atlantic (NB+NS+PE) | T1 |
| NL | Newfoundland & Labrador | L1 |
| NO | North (YT+NT+NU) | N1 |

## 2. Province/territory crosswalk

Every Canadian province and territory maps to at least one OSeCAN zone. The
`AT` and `NO` groups aggregate three jurisdictions each, so the 9 OSeCAN groups
expand to 13 provinces/territories.

| Province / territory | Postal code | OSeCAN group | Zone(s) | Basis |
|---|---|---|---|---|
| British Columbia | BC | BC | B1, B2 | code |
| Alberta | AB | AB | A1, A2 | code |
| Saskatchewan | SK | SK | S1 | code |
| Manitoba | MB | MB | M1 | code |
| Ontario | ON | ON | O1, O2 | code |
| Quebec | QC | QC | Q1, Q2 | code |
| New Brunswick | NB | AT | T1 | code |
| Nova Scotia | NS | AT | T1 | code |
| Prince Edward Island | PE | AT | T1 | code |
| Newfoundland and Labrador | NL | NL | L1 | code |
| Yukon | YT | NO | N1 | code |
| Northwest Territories | NT | NO | N1 | code |
| Nunavut | NU | NO | N1 | code |

### How the mapping is encoded

- `Provinces` (`model_structure.py:66-70`) defines the 9 policy/reporting
  groups, including the Atlantic and North aggregations.
- `Zones` (`model_structure.py:74-88`) maps each 2-character zone to
  `[province_group, label]`. Zone codes are fixed width two
  (`ZONE_WIDTH = 2`, `model_structure.py:94`) and are parsed by slice
  throughout the codebase.
- `zones_of(province)` (`model_structure.py:95-105`) returns the zones of a
  province group in declaration order; this is the function used by the demand
  and capacity cleaners.
- `ZoneDemandShare` (`model_structure.py:117-121`) splits provincial demand
  across zones: B1/B2 0.75/0.25, A1/A2 0.65/0.35, O1/O2 0.85/0.15, Q1/Q2
  0.80/0.20, and 1.0 for every single-zone province. The source comment
  (`model_structure.py:107-116`) states these are population/load-centre
  guesses, not measurements.
- `TransmissionLinks` (`model_structure.py:139-145`) confirms the intended
  topology: B1–B2, A1–A2, O1–O2 and Q1–Q2 are intra-provincial links; N1 has no
  link and is deliberately islanded.
- The demand cleaner maps CER regions to the 9 groups at
  `src/osecan/data/clean_demand.py:21-27` and splits by `zones_of` at
  `clean_demand.py:73-80`.
- The capacity cleaner uses the same `PROV_MAP`
  (`src/osecan/data/clean_capacity.py:33-39`) and the configured
  `zone_capacity_share` at `config/capacity.yaml:46-77`.

## 3. B1/B2 comparison with the FRESH provisional split

### OSeCAN side (code-defined)

- B1 label: **South Coast / Lower Mainland**; B2 label: **Interior & North**
  (`model_structure.py:75-76`).
- Province: both are BC (`model_structure.py:75-76`).
- Demand share: B1 0.75 / B2 0.25 (`model_structure.py:117-121`).
- Capacity default share: B1 0.75 / B2 0.25 (`config/capacity.yaml:51-60`);
  hydro is reversed to B1 0.15 / B2 0.85 (`capacity.yaml:61-64`), wind is
  B1 0.30 / B2 0.70 (`capacity.yaml:70-72`), and uranium is B1 1.00 / B2 0.00
  (`capacity.yaml:79-86`).
- No sub-provincial boundary, polygon or official geometry is published. The
  OSeCAN coupling review explicitly asks "Where are the zone boundary
  geometries for B1/B2, A1/A2, O1/O2 and Q1/Q2?"
  (`docs/coupling_WS3_OSeCAN.md:166`).

### FRESH side (provisional)

The FRESH-side crosswalk is documented in
`planning/bc-woody-residue-supply-curve.md:9-27` and implemented in
`workflows/national/clews/bc_residue_supply.py:93-99`:

- **B1** = Georgia Depression + Southern Coastal Mountains (two BC
  ecoprovinces).
- **B2** = the remaining BC ecoprovinces, including the BC portions of the
  Montane Cordillera, Boreal Cordillera, Boreal Plains and Taiga Plains.
- The source note says explicitly: "C2070 has no published B1/B2 boundary. The
  crosswalk is a FRESH-side proposal" (`planning/bc-woody-residue-supply-curve.md:24-27`).
- The per-ecoprovince crosswalk has 14 rows: 2 for B1 and 12 for B2 (the
  extra B2 row is `NO_ECOPROVINCE`, an unlabelled raster sliver assigned by
  documented fallback). Every row is `fresh_provisional` /
  `no_published_boundary` (`data/derived/national/clews_supply/bc_woody_residue/bc_zone_crosswalk.csv`).
- The FRESH harvest allocation is B1 17.399562% / B2 82.600438% on the 2018
  AAC zone log share, flagged `no_fmu_tsa_map_documented_zone_share`
  (`planning/bc-residue-recovery.md:38-39`;
  `data/derived/national/clews/bc_residue_recovery.csv` rows B1/B2).

### Comparison

| Aspect | OSeCAN B1/B2 | FRESH provisional B1/B2 | Assessment |
|---|---|---|---|
| Labels | South Coast / Lower Mainland; Interior & North | Same two labels | Consistent |
| Province | BC | BC | Consistent |
| Sub-provincial definition | None published | B1 = Georgia Depression + Southern Coastal Mountains; B2 = all other BC ecoprovinces | FRESH operationalizes the labels; not an official C2070 boundary |
| Geometry | Point anchor only (B1/B2); no polygon | Ecoprovince labels; no polygon | Both lack an authoritative polygon |
| Quantitative split | Demand 75/25; capacity default 75/25 | Harvest/log 17.3996/82.6004 (2018 AAC) | Different quantities; not directly comparable |
| Official status | Zone IDs/province assignment code-defined; boundary undefined | `fresh_provisional`; `no_published_boundary` | Boundary remains provisional |

**Assessment.** The FRESH split is semantically compatible with OSeCAN's
label-level distinction (southern coast/Lower Mainland versus interior and
north) and uses a documented, reproducible ecoprovince rule. It is not an
official C2070 boundary. The main judgment calls are:

1. **Southern Coastal Mountains** is assigned to B1 even though OSeCAN's label
   says "Lower Mainland"; a stricter reading would put more of the coastal
   mountains in B2.
2. **Northern Coastal Mountains** is assigned to B2; a strict "coast" reading
   could instead put it in B1.
3. The B1/B2 demand and capacity shares (75/25) are modeller judgments and are
   not evidence for the geographic boundary; the harvest share (17.4/82.6) is
   a different metric and does not contradict them.
4. The B1/B2 point anchors are harvest-weighted TSA centroids, not zone
   centroids. B1's anchor (51.596433°N, 126.245649°W) is a central-coast
   harvest node, not a Lower Mainland location; it must not be used as a
   boundary or label centroid.

## 4. Geometry availability

### Explicit finding: no C2070 zone polygons

No shapefile, GeoJSON, GeoPackage, KML or coordinate boundary representing a
C2070 zone exists in the public OSeCAN repository, the parent repository, or the
data submodule. Searches over `runtime/osecan_model/`, `data/`, and `ui/` found
province, ecoprovince, forest-tile, forest-tenure, and road layers, but no zone
boundary layer. The only C2070 zone geometry is two **point anchors** for
B1/B2; the other 11 zones are explicit gaps.

- `ui/public/data/spatial/zone_nodes.json` declares `zone_count: 13`,
  `anchor_count: 2`, `gap_count: 11`. B1 and B2 have `anchor: true`; the other
  11 have `anchor: false`, `gap: true` and the reason "No authoritative C2070
  zone anchor in repository; do not invent geometry."
- The UI states the same rule at `ui/README.md:60-67` and `ui/README.md:128-129`:
  "The 11 C2070 zones without authoritative polygons are shown as gaps, never
  as invented geometry" and "No C2070 zone polygon is drawn; only B1/B2 anchors
  are plotted."
- OSeCAN's own transmission map uses GADM **province** boundaries, not zone
  boundaries (`src/osecan/vis/transmission_map.py:12-29`), and its cache
  `data/external/gadm/CAN_adm1.gpkg` is not present in this checkout.
- `docs/_build_site.py:101-105` contains 13 `ZP` pixel coordinates for the
  website's decorative SVG. These are display coordinates, not geography, and
  carry no CRS; they must not be treated as zone geometry.
- `src/osecan/vis/canada_svg.py:1-12` likewise builds a decorative province
  silhouette with zone nodes, not zone polygons.

### B1/B2 point anchors (not polygons)

`data/derived/national/bc_chain/bc_zone_nodes.csv` and
`ui/public/data/spatial/zone_nodes.json` provide:

| Zone | Node | Longitude | Latitude | CRS | Weight basis |
|---|---|---:|---:|---|---|
| B1 | ZONE_B1 | -126.245649 | 51.596433 | EPSG:4326 | `2018_apportionment_m3_x_zone_thlb_share` |
| B2 | ZONE_B2 | -122.509043 | 53.621846 | EPSG:4326 | `2018_apportionment_m3_x_zone_thlb_share` |

The source node files also record projected `x_m`/`y_m` values (EPSG:3005; see
`workflows/national/assignments/build_road_network.py:64`). They are
apportionment/THLB-weighted averages of TSA centroids, generated by
`workflows/national/assignments/bc_chain.py:528-537`; they are routing nodes,
not boundaries.

### Selected non-zone geometry layers

| Path | Content | CRS | Fields |
|---|---|---|---|
| `ui/public/data/spatial/provinces.geojson` | 13 province polygons | EPSG:4326 | `name`, `iso` |
| `ui/public/data/spatial/ecoprovinces.geojson` | 36 ecoprovince polygons | EPSG:4326 | `ecoprovince_id`, `ecoprovince`, `ecozone` |
| `ui/public/data/spatial/rsyc_tiles.geojson` | 385 forest tiles | EPSG:4326 | `tile_id`, `provinces`, `area_ha`, `volume_m3`, `recoverable_odt_base`, `recoverable_pj_base`, `over_capacity` |
| `data/raw/national/rsyc/RSYC_ecostrat.gpkg` | ecozones/ecoprovinces/ecoregions/ecodistricts | EPSG:3978 | `parameter_level`, `ecozone`, `ecoprovince`, `ecoregion`, `ecodistrict`, IDs, `join_key` |
| `data/raw/national/rsyc/RSYC_tiles.gpkg` | RSYC tiles | EPSG:3978 | `TileID` |
| `data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson` | 13 province polygons | GeoJSON (WGS84) | `shapeName`, `shapeISO`, `shapeID`, `shapeGroup`, `shapeType` |

The ecoprovince layer is the ingredient used by the FRESH B1/B2 proposal, but
dissolving it would still produce a **provisional FRESH geometry**, not an
authoritative C2070 zone polygon. This extraction deliberately does not create
such a polygon.

## 5. Validation

Validated against the authoritative commit, not the dirty working tree:

```bash
git -C runtime/osecan_model show 40d5b43:src/osecan/model_structure.py > /tmp/osecan_ms_40d5b43.py
```

Checks performed:

| Check | Result |
|---|---|
| Zone count equals 13 | PASS |
| Zone IDs unique in CSV | PASS (13 unique) |
| CSV zone set equals source `Zones` keys | PASS |
| Zone name and province group equal source for every row | PASS |
| All 13 provinces/territories covered by at least one zone | PASS |
| All 9 OSeCAN province groups have at least one zone | PASS |
| B1 and B2 present in code and documented on both sides | PASS |
| B1/B2 FRESH crosswalk is provisional and has no published boundary | PASS (14 rows, all `fresh_provisional` / `no_published_boundary`) |
| No C2070 zone polygons present | PASS (0 polygons; 2 point anchors; 11 gaps) |

## 6. Gaps and cautions

1. **No authoritative zone boundaries.** The 13-zone system is code-defined at
   the province-group level only. A1/A2, O1/O2, Q1/Q2 and B1/B2 have no
   published dividing line; T1/N1 are multi-province aggregates.
2. **B1/B2 FRESH split is provisional.** It is an ecoprovince-based proposal,
   not a C2070 decision. Any use must carry the `fresh_provisional` status.
3. **No FMU → TSA/zone crosswalk.** FRESH allocates FFCM totals to B1/B2 by a
   documented 2018 AAC share and flags this
   (`planning/bc-residue-recovery.md:38-39`); it does not assign individual
   FMUs to zones.
4. **B1/B2 points are routing nodes, not centroids of the named region.**
   Do not use them to test point-in-polygon or to draw boundaries.
5. **Zone demand/capacity shares are expert judgment.** They are not
   measurements (`model_structure.py:107-116`;
   `config/capacity.yaml:46-50`) and must not be used as boundary evidence.
6. **N1 is islanded.** It has no `TransmissionLinks` entry
   (`model_structure.py:137-145`), so territorial supply cannot be displaced by
   grid imports in the model.
7. **Do not infer zone geometry from the decorative SVG.** The `ZP` pixel
   coordinates in `docs/_build_site.py:101-105` and the silhouette in
   `src/osecan/vis/canada_svg.py` are presentation-only.

## 7. Source references

### Public OSeCAN (commit `40d5b43`)

- `runtime/osecan_model/src/osecan/model_structure.py:66-70` — `Provinces`.
- `runtime/osecan_model/src/osecan/model_structure.py:74-88` — `Zones`.
- `runtime/osecan_model/src/osecan/model_structure.py:94-105` — `ZONE_WIDTH`,
  `zones_of`.
- `runtime/osecan_model/src/osecan/model_structure.py:107-121` —
  `ZoneDemandShare` and its judgment caveat.
- `runtime/osecan_model/src/osecan/model_structure.py:137-145` —
  `TransmissionLinks`, including N1 islanding.
- `runtime/osecan_model/src/osecan/model_structure.py:167-179` —
  `link_kind` intra/inter classification.
- `runtime/osecan_model/docs/model-description.md:50-57` — 13-zone naming table.
- `runtime/osecan_model/docs/model-description.md:278` — 13 zones.
- `runtime/osecan_model/docs/coupling_WS3_OSeCAN.md:83-93` — province/zone
  table with demand shares.
- `runtime/osecan_model/docs/coupling_WS3_OSeCAN.md:103-107` — crosswalk and
  boundary gap.
- `runtime/osecan_model/docs/coupling_WS3_OSeCAN.md:166` — open question on
  zone boundary geometries.
- `runtime/osecan_model/config/capacity.yaml:46-77` — `zone_capacity_share`.
- `runtime/osecan_model/src/osecan/data/clean_demand.py:21-27,73-80` — CER
  region map and zone split.
- `runtime/osecan_model/src/osecan/data/clean_capacity.py:33-39,83-89` — same
  province map and capacity shares.

### Parent repository / data submodule

- `planning/bc-woody-residue-supply-curve.md:9-27` — FRESH B1/B2 proposal.
- `planning/bc-residue-recovery.md:38-39` — FRESH B1/B2 AAC log shares.
- `workflows/national/clews/bc_residue_supply.py:93-99` — B1/B2 names and
  `B1_ECOPROVINCES`.
- `data/derived/national/clews_supply/bc_woody_residue/bc_zone_crosswalk.csv` —
  14-row ecoprovince crosswalk.
- `data/derived/national/clews/bc_residue_recovery.csv` — B1/B2 shares and
  recovery status.
- `data/derived/national/bc_chain/bc_zone_nodes.csv` — B1/B2 point anchors.
- `workflows/national/assignments/bc_chain.py:528-537` — anchor construction.
- `ui/public/data/spatial/zone_nodes.json` — 13 zones, 2 anchors, 11 gaps.
- `ui/README.md:60-67,104-107,128-129` — UI gap policy and spatial data
  contract.
- `ui/public/data/spatial/provinces.geojson`,
  `ui/public/data/spatial/ecoprovinces.geojson`,
  `ui/public/data/spatial/rsyc_tiles.geojson` — available non-zone geometry.
- `data/raw/national/rsyc/RSYC_ecostrat.gpkg`,
  `data/raw/national/rsyc/RSYC_tiles.gpkg`,
  `data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson` — other
  available geometry, not C2070 zones.

### Presentation-only (do not use as geography)

- `runtime/osecan_model/docs/_build_site.py:101-105` — SVG pixel coordinates.
- `runtime/osecan_model/src/osecan/vis/transmission_map.py:12-29` — GADM
  province boundaries; no zone polygons.
- `runtime/osecan_model/src/osecan/vis/canada_svg.py:1-12` — decorative
  province silhouette.
