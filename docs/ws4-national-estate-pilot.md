# WS4 national estate pilot: SCANFI pixels × RSYC curves

## Status and non-claims

This is the first real WS4 forest-estate pilot built from SCANFI 30-m pixels
and the RSYC tile production library. It is a **`model_estimate` /
`sensitivity_only`** diagnostic pilot. It is **not** official OSeCAN, does
**not** change chain v5.1, and makes no policy-grade, investment, tenure,
operability, or national-supply claim.

**Candidate release:** this pilot is consolidated in
[`docs/national-forest-estate-ws4-v1.md`](national-forest-estate-ws4-v1.md)
under annotated tag `national-forest-estate-ws4-v1`.

Pilot output:

```text
data/derived/ws4/scanfi-pilot-G2-BC-S32/
├── dataset/                 # 12 WS4 tables + manifest.json
├── schedules/pilot_cc.csv
├── runs/pilot_cc_50y/
├── inventory_summary.json
└── README.md
```

## 1. Pilot tile

The pilot selects the highest-`harvest_m3` BC row of
`data/derived/national/facilities/supply_node_50km_v1/supply_nodes_50km.csv`:

| Field | Value |
|---|---|
| `node_id` | `G2|BC|S32` |
| `parent_node_id` | `G2|BC` |
| `tile_id` | `G2` |
| `row`, `col` | 3, 2 (top-middle 50-km subcell) |
| centroid lat/lon | 49.836521, -125.994053 |
| `harvest_m3` | 228,408.176800 |
| `subcell_area_ha` (productive allocation) | 224,737.998031 |
| geometric 50-km rectangle | 250,000 ha |
| BC province intersection | 247,332.369040 ha |

The 150-km parent RSYC tile geometry is read from
`data/raw/national/rsyc/RSYC_tiles.gpkg`, layer `RSYC_tiles`, EPSG:3978. Its
bounding box is split 3×3; rows count from the south (row 1) and columns from
the west (col 1), matching `workflows/national/facilities/supply_node_50km_v1.py`.
The selected subcell bounds in EPSG:3978 are:

```text
x: -2,160,910.524 .. -2,110,910.524
y:    598,648.106 ..    648,648.106
```

The full 50-km rectangle is retained for the inventory window. It is
transformed into the SCANFI raster's own custom NAD83 Canada Lambert CRS with
`rasterio.warp.transform_geom`; the tile is **not** assumed to share the
raster CRS. In this custom CRS the x coordinate happens to align with
EPSG:3978 but y is offset by about +6,585,076 m, so using the raster's own
CRS is mandatory. The 50-km square lands in raster window
`col_off=6019, row_off=73425, width=1668, height=1668`; 2,777,222 pixel centres
fall inside the transformed rectangle.

## 2. SCANFI inventory method

Inputs (all local, 30 m, aligned):

- `data/raw/scanfi/v2/SCANFI_age_median_2025_v2_20260119.tif`
- `data/raw/scanfi/v2/SCANFI_att_nfiLandcover_2025_v2_20260119.tif`
- ten `data/raw/scanfi/v2/SCANFI_spsCC_*_2025_v2_20260119.tif` species
  crown-closure layers
- `data/derived/national/assortments/rsyc_tile_production/rsyc_tile_production_library.csv`

Rules applied:

1. **Forest mask:** landcover ∈ {5 broadleaf, 6 conifer, 7 mixed}; exclude
   landcover nodata (255), age nodata (-32768), and age ≤ 0.
2. **Age:** integer median age. Age class = `floor(age/10)*10`.
3. **Dominant species:** maximum species crown-closure layer at the pixel. If
   the maximum closure is 0 (none in this window), fall back to the landcover
   group.
4. **Species mapping:** `blackSpruce→PICE.MAR`, `balsamFir→ABIE.BAL`,
   `jackPine→PINU.BAN`, `lodgepolePine→PINU.CON`,
   `ponderosaPine→PINU.PON`, `douglasFir→PSEU.MEN`, `tamarack→LARI.LAR`,
   `whiteRedPine→PINU.STR/PINU.RES`, `broadleaf→BROADLEAF`,
   `otherConiferous→CONIFEROUS`.
5. **Curve selection:** exact species row, then group (`CONIFEROUS` /
   `BROADLEAF`), then generic. A forest pixel with no available curve fails the
   build. G2 has only five library rows, so missing `PICE`, `ABIE`, or `LARI`
   species rows fall back to the coniferous group rather than failing.
6. **Fragment:** one forest pixel, `area_ha = 0.09` (30 m × 30 m = 900 m²;
   verified from the raster transform). Fragment IDs are sequential in
   row-major order.
7. **Block:** connected clique of pixels with the same `(age_class,
   curve_key)` under 8-neighbour connectivity. Block age offset is the
   age-class lower bound; block area is the sum of fragment areas.

### Counts and exclusions

| Quantity | Value |
|---|---:|
| Window pixels (1,668 × 1,668) | 2,782,224 |
| Pixels outside the 50-km rectangle | 5,002 |
| Landcover nodata | 4 |
| Non-forest landcover | 323,880 |
| Forest class with age ≤ 0 | 6,975 |
| Age nodata in forest class | 0 |
| **Forest fragments** | **2,446,363** |
| **Forest area** | **220,172.67 ha** (2,201.7267 km²) |
| **Blocks** | **932,041** |
| Fragments with zero species closure | 0 |

Age distribution (10-year classes, fragments): median class 280, mean age
210.24; 65.3% of fragments are older than 150 years. Classes run from 0
(84,944 fragments) to 500 (1 fragment).

Clique sizes: median 1 pixel, mean 2.625 pixels, p90 3, p99 16, maximum 7,971
pixels (717.39 ha); 658,252 blocks are singletons (70.6%). The high singleton
share reflects 30-m age/species noise, not an operability judgement.

## 3. RSYC curves

Four curve keys are used. x runs 0..600. The published RSYC form is used for
x = 1..600 and the origin is explicit `(0, 0)`:

```text
y(x) = b1 * exp(-b4*x) * (1 - exp(-b2*x))^b3
```

| curve_id | curve_key | species | level | fragments | area (ha) |
|---:|---|---|---|---:|---:|
| 2 | `rsyc_volume_national_tile_coniferous_g2` | CONIFEROUS | tile group | 2,342,754 | 210,847.86 |
| 3 | `rsyc_volume_national_tile_generic_g2` | BROADLEAF (generic model) | tile generic | 15,613 | 1,405.17 |
| 4 | `rsyc_volume_national_tile_pinu_con_g2` | PINU.CON | tile exact | 2,723 | 245.07 |
| 5 | `rsyc_volume_national_tile_pseu_men_g2` | PSEU.MEN | tile exact | 85,273 | 7,674.57 |

Curve 1 is the constant area curve (`y = 1`) used by `product.Treated`.

The G2 library reuses the coniferous `model_id` for the `PINU.PON` fallback row
with identical coefficients. That is the same curve, not an ambiguity; no
selected `model_id` maps to two different coefficient sets. Had one done so,
the key would carry a coefficient digest, and the ambiguity would be reported
in `curve_diagnostics`.

Coefficients (b1, b2, b3, b4):

| curve_key | b1 | b2 | b3 | b4 |
|---|---:|---:|---:|---:|
| coniferous_g2 | 451.249708238704 | 0.0293094320189197 | 1.03423280911212 | 0.000638469722672 |
| generic_g2 | 600.702967116366 | 0.0323445908475582 | 1.24891610581182 | 0.00036182218531 |
| pinu_con_g2 | 138.563518081908 | 0.0316830420292665 | 1.13706350127079 | 0.0009668217100812 |
| pseu_men_g2 | 446.499946817197 | 0.038980331460633 | 1.28566907865182 | 0.0009850102186229 |

## 4. WS4 tracks and treatments

For each curve key there are three tracks (12 tracks total):

```text
natural: AU(G2)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(baseline)+CURVE(<key>)+treatment()
thinned: AU(G2)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(thinned)+CURVE(<key>)+treatment()
managed: AU(G2)+IFM(managed)+ORIGIN(planted)+SILV_STATE(cc_pl)+CURVE(<key>)+treatment()
```

Each track offers `THIN` and `CC` treatments:

| From | Treatment | Begin | End | New track | Offset | Retain |
|---|---|---:|---:|---|---:|---:|
| natural | THIN | 20 | 40 | thinned | -1 | 0 |
| natural | CC | 40 | 1000 | managed | 0 | 0 |
| thinned | THIN | 20 | 40 | thinned | -1 | 0 |
| thinned | CC | 40 | 1000 | managed | 0 | 0 |
| managed | THIN | 20 | 40 | managed | -1 | 0 |
| managed | CC | 40 | 1000 | managed | 0 | 0 |

The negative THIN offset is the WS4 partial-cut sentinel: it keeps the
fragment's current age instead of resetting it to the treatment offset. The
thinned track uses the **same** RSYC curve as the natural and managed tracks.
This pilot has no thinning or planting yield-response data, so `THIN` and the
managed transition record state and produce volume at the pre-treatment curve
only; they do not claim a silvicultural response.

Features are `feature.Yield.unmanaged.Total`, `feature.Yield.thinned.Total`,
and `feature.Yield.managed.Total`, all on the same curve id. Products pair each
state with each treatment:

```text
product.HarvestedVolume.<state>.THIN    product.Treated.<state>.THIN
product.HarvestedVolume.<state>.CC      product.Treated.<state>.CC
```

The products table has 48 rows (4 curves x 3 states x 2 treatments x 2
product kinds); `accounts`/`protoaccounts` have one row per unique label
(15 rows) with `sum=1.0`.

Curve IDs are stable: area curve 1, RSYC curves 2..5 in sorted `curve_key`
order. Blocks map to `curve:<key>` groups.

## 5. Compiled dataset

`schema_version` 0.1, model `II`, period length 1, recipe
`SCANFI_RSYC_pilot`. Twelve tables:

| Table | Rows |
|---|---:|
| fragments | 2,446,363 |
| blocks | 932,041 |
| tracks | 12 |
| strata | 12 |
| treatments | 24 |
| curves | 3,005 |
| features | 12 |
| products | 48 |
| protoaccounts | 15 |
| accounts | 15 |
| groups | 932,041 |
| messages | 1 |

`accounts` and `protoaccounts` use one row per unique feature/product label,
group `_ALL_`, attribute = account = label, and `sum=1.0` (the TSA24 account
contract), not 0.

The manifest records source SHA-256 values for the age raster, landcover
raster, ten species rasters, the RSYC library CSV, the RSYC tile GPKG, and the
supply-node CSV. Metadata carries the `model_estimate` / `observed=false` /
`policy_grade=false` / `national_claim=false` labels and the pilot identity.

## 6. Validate and simulate

WS4 is the editable install at `.venv-ws4/bin/ws4`, sourced from
`/srv/shared-data/gep/ws4/ws4-gpu` (not `.../ws4/ws4`). Version 0.2.0.

```bash
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset
```

Result: exit 0, `Dataset is valid`.

Schedule: `block_id,treatment,offset` for 10% of eligible blocks (block age
class ≥ 40), offset 0.

```bash
.venv-ws4/bin/ws4 simulate \
  --dataset data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset \
  --schedule data/derived/ws4/scanfi-pilot-G2-BC-S32/schedules/pilot_cc.csv \
  --years 50 \
  --out data/derived/ws4/scanfi-pilot-G2-BC-S32/runs/pilot_cc_50y
```

| Result | Value |
|---|---:|
| Eligible blocks (offset ≥ 40) | 898,942 |
| Schedule rows / scheduled blocks | 89,895 / 89,895 |
| Scheduled area | 17,844.30 ha |
| Harvested volume | 6,547,384.791 m³ |
| Treated area (managed area established) | 17,844.30 ha |
| Final managed yield (offset 49) | 5,915,085.904 m³ |
| Final unmanaged yield (offset 49) | 74,542,197.605 m³ |

The simulated treated area equals the scheduled block area exactly. All
scheduled natural-track fragments transition to their managed track, so the
simulation shows both nonzero harvested volume and nonzero managed area.

## 7. Limitations

- Single 50-km pilot tile and a single 50-year schedule; not a national run.
- 30-m fragments retained because the tile has 2.446M forest pixels, below the
  2.5M aggregation trigger. No 60/90-m aggregation was used.
- Natural, thinned, and managed tracks share one RSYC curve; there is no
  thinning, planting, silviculture, fertilization, or genetic yield response.
  `THIN` is a partial-cut state transition recorded at the same curve; it is
  not a growth model.
- No operability, tenure, THLB, road, slope, riparian, cutblock, green-up, or
  spatial-topology constraints. Pixel cliques are inventory blocks, not
  operable units.
- Species selection is the maximum SCANFI crown-closure layer. Group/generic
  fallbacks are explicit; a missing curve would fail the build.
- Age is the integer SCANFI median age; age classes are 10-year bins.
- Non-forest and nonpositive-age pixels are excluded; no imputation.
- National scaling requires tile-by-tile builds or an explicit aggregation
  design, with the same fail-closed curve and area checks.
- No official OSeCAN change, no UI change, no chain v5.1 change.

## 8. Reproduction

```bash
.venv/bin/python workflows/ws4_instances/build_scanfi_estate_pilot.py --run-ws4
```

The builder refuses to write if either repository has an unexpected `D` in
`git status`, if a raster is not 30-m aligned, if a forest pixel has no RSYC
curve, or if fragment/block counts or areas do not conserve.
