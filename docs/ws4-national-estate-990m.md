# WS4 national forest estate at 990 m: SCANFI pixels × RSYC group curves

## Status and non-claims

This is a **national-scope** compiled WS4 forest-estate dataset built from the
SCANFI 30-m age and landcover rasters and the RSYC tile production library.
It is a **`model_estimate` / `sensitivity_only`** diagnostic. It is **not**
official OSeCAN, does **not** change chain v5.1, the frozen artifacts, or the
UI, and makes no policy-grade, tenure, operability, investment, or national
supply claim. `national_scope=true` and `national_claim=false` are recorded
together in the manifest.

**Candidate release:** this national estate is consolidated in
[`docs/national-forest-estate-ws4-v1.md`](national-forest-estate-ws4-v1.md)
under annotated tag `national-forest-estate-ws4-v1`.

Output:

```text
data/derived/ws4/scanfi-national-990m/
├── dataset/                          # 12 WS4 tables + manifest.json
├── inventory/
│   ├── forest_cells_990m.parquet     # one row per 990-m fragment
│   ├── node_lookup.parquet           # 50-km node identity and raster area
│   └── curves_used.csv
├── schedules/national_cc_sample.csv
├── runs/national_cc_20y/
├── inventory_summary.json
└── README.md
```

## 1. Resolution change: 30-m inventory, 990-m WS4 fragments

The previous pilot used one 30-m SCANFI pixel as one WS4 fragment. One 50-km
tile alone contained 2.45M fragments, so a national 30-m WS4 dataset would be
billions of fragments and is not tractable as one dataset. This build keeps the
full 30-m inventory for measurement but aggregates it into 990-m fragments:

| Label | Value |
|---|---|
| `inventory_resolution` | `30m` |
| `inventory_resolution_m` | 30 |
| `ws4_fragment_resolution` | `990m` |
| `ws4_fragment_resolution_m` | 990 |
| fragment pixels | 33 × 33 = 1,089 |
| maximum fragment area | 98.01 ha |
| area basis | forest pixel count × 0.09 ha |

A fragment is created for every 990-m cell containing at least one forest
pixel. Its `area_ha` is the forest area inside the cell, not the full 98.01 ha
cell. The 50-km supply nodes are retained as inventory windows and reporting
units, not as fragments.

The SCANFI grid is 178,400 × 119,100 pixels in its own custom NAD83 Canada
Lambert CRS. Block-aligned processing uses 5,406 × 3,609 = 19,510,254 990-m
cells. The last 2 pixel columns and 3 pixel rows do not form a complete 33 × 33
block and are ignored (773,394 pixels, 0.0036% of the raster).

All CRS handling uses the raster's own `rasterio` CRS and transform. The raster
is **not** assumed to be EPSG:3978.

## 2. Method

### 2.1 Chunked national scan

The national rasters are read once, in block-row strips of 8 × 5,406 blocks
(264 × 178,398 pixels per chunk). Each chunk is reshaped to
`(block_rows, 33, block_cols, 33)` and reduced without reading the full
21.25-billion-pixel array at once.

For each 990-m block:

1. **Forest mask:** landcover ∈ {5 broadleaf, 6 conifer, 7 mixed}, age valid
   (`> 0`, not `-32768`), and landcover not `255`.
2. **Forest area:** forest pixel count × 0.09 ha; zero-forest blocks are
   skipped.
3. **Age:** area-weighted mean of forest-pixel ages (equal 0.09-ha pixel
   weights), rounded half-up to an integer. Age class =
   `floor(age / 10) * 10`.
4. **Landcover group:** count conifer, broadleaf, and mixed pixels. Choose
   `CONIFEROUS` if conifer ≥ broadleaf, else `BROADLEAF`; mixed pixels follow
   the chosen dominant group. `group_fraction` =
   `(dominant_count + mixed_count) / forest_pixel_count`.
5. **Parent node:** each block centre is mapped through a 990-m rasterization
   of the true node geometry, `subcell ∩ (tile ∩ province)`. Using the
   allocation geometry rather than a province raster alone prevents a cell in
   an unallocated neighbouring province from being assigned to a node that does
   not exist.
6. **Curve:** the parent node's `(tile_id, province)` supplies the RSYC tile
   coniferous or broadleaf group row; a missing broadleaf group row falls back
   to the tile generic row. A node with no conifer group curve, or no
   broadleaf/generic curve, fails the build. Species-specific RSYC curves are
   **not** selected because the national build does not process the SCANFI
   species rasters.
7. **Fragment:** one row per included forest 990-m cell, with `area_ha`,
   `age`, and the natural track for `(province, curve_key)`.

### 2.2 Connected blocks

Fragments are connected under 8-neighbour adjacency when they share
`(age_class, curve_key, province)`. Province is part of the block key because
WS4 tracks and groups are per province (`province:<p>|curve:<key>`); a block
must not straddle two track domains. The 50-km subcell boundaries are **not**
part of the key and do not clip a block. A clique may cross any number of
50-km windows inside the same tile-province curve domain.

Block ids are ordered by the first fragment in row-major scan order. Block area
is the sum of fragment forest areas. Block `offset` is the age-class lower
bound. Union-find-equivalent labelling uses a sparse 8-neighbour graph and
`scipy.sparse.csgraph.connected_components`; there is no per-curve full-grid
loop.

### 2.3 WS4 tracks, treatments, features, and products

For each used `(province, curve_key)` pair:

```text
natural: AU(<province>)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(baseline)+CURVE(<key>)+treatment()
thinned: AU(<province>)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(thinned)+CURVE(<key>)+treatment()
managed: AU(<province>)+IFM(managed)+ORIGIN(planted)+SILV_STATE(cc_pl)+CURVE(<key>)+treatment()
```

Each track offers `THIN` and `CC`:

| From | Treatment | Begin | End | New track | Offset | Retain |
|---|---|---:|---:|---|---:|---:|
| natural | THIN | 20 | 40 | thinned | -1 | 0 |
| natural | CC | 40 | 1000 | managed | 0 | 0 |
| thinned | THIN | 20 | 40 | thinned | -1 | 0 |
| thinned | CC | 40 | 1000 | managed | 0 | 0 |
| managed | THIN | 20 | 40 | managed | -1 | 0 |
| managed | CC | 40 | 1000 | managed | 0 | 0 |

The negative THIN offset is the WS4 partial-cut sentinel: the fragment keeps
its current age instead of resetting to zero. The thinned, managed, and
natural tracks use the **same** RSYC curve: there is no thinning, planting,
silviculture, fertilization, or genetic yield-response data in this build.
`THIN` records a partial-cut state transition and produces volume on the
pre-treatment curve; it is not a thinning-response model.

Features:

```text
feature.Yield.<province>.unmanaged.Total
feature.Yield.<province>.thinned.Total
feature.Yield.<province>.managed.Total
```

Products (each state x treatment x product kind):

```text
product.HarvestedVolume.<province>.<state>.THIN
product.HarvestedVolume.<province>.<state>.CC
product.Treated.<province>.<state>.THIN
product.Treated.<province>.<state>.CC
```

where `<state>` is `unmanaged`, `thinned`, or `managed`.

Curves use `x = 0` then `1..600` from the published RSYC equation:

```text
y(x) = b1 * exp(-b4*x) * (1 - exp(-b2*x))^b3
```

Curve id 1 is the constant area curve (`y = 1`) used by `product.Treated`.
Yield curves are ids `2..719`, one id per used `curve_key`, sorted by key.
`accounts` and `protoaccounts` contain one row per unique feature/product
label, group `_ALL_`, attribute = account = label, and `sum=1.0`.

## 3. National counts

| Quantity | Value |
|---|---:|
| Aligned 30-m pixels scanned | 21,246,666,606 |
| Forest-class pixels, age > 0 | 4,623,607,984 |
| Forest-class pixels with age ≤ 0 (excluded) | 70,567,131 |
| Age nodata inside forest class | 0 |
| Landcover nodata | 11,237,131,830 |
| Non-forest landcover | 5,315,359,661 |
| Forest outside the tile-province allocation | 54,156,536 px (4,874,088.24 ha) |
| **Included forest pixels** | **4,569,451,448** |
| **WS4 fragments** | **6,807,192** |
| **Blocks** | **1,984,017** |
| **Forest area** | **411,250,630.32 ha (4,112,506.30 km²)** |
| Used curves | 718 of 770 resolved |
| Province-curve pairs | 933 |
| Tracks (natural + thinned + managed) | 2,799 |
| Accounts / protoaccounts labels | 195 |

The 4.87M ha difference between forest-class pixels and included fragments is
forest outside the RSYC tile-province allocation grid (for example, southern
areas not covered by an RSYC tile, or a province part of a tile that has no
committed RSYC allocation). It is reported, not imputed. A further 50 nodes
are geometric slivers smaller than one 990-m cell; they retain a curve
specification but no fragment is invented for them.

### Age distribution (fragments / area)

- Median age class: 60 (fragments), 70 (area).
- Age class < 40: 1,494,093 fragments, 50,784,118.38 ha (12.35% of area).
- Age class ≥ 150: 364,820 fragments (5.36% of fragments).
- Classes run from 0 to 430; the national distribution is younger than the
  wet-coastal G2 pilot, which is expected for the boreal and fire-driven
  portions of the national grid.

### Landcover group

| Group | Fragments | Area (ha) | Group fraction mean | Median |
|---|---:|---:|---:|---:|
| CONIFEROUS | 5,938,754 | 362,458,966.95 | 0.9591 | 0.9968 |
| BROADLEAF | 868,438 | 48,791,663.37 | 0.8618 | 0.8836 |

### Blocks (990-m cell cliques)

- 1,984,017 blocks; median 1 fragment, mean 3.43, p90 5, p99 30.
- Maximum 13,660 fragments (402,329.52 ha).
- 1,181,500 singleton blocks (59.6%).
- 4,757 blocks contain at least 100 fragments.

These are inventory cliques of equal age class and curve, not operable units.
The high singleton share reflects 990-m age/group heterogeneity, not an
operability judgement.

### Province distribution

| Province | Fragments | Area (ha) | Blocks | Eligible blocks (offset ≥ 40) |
|---|---:|---:|---:|---:|
| BC | 875,576 | 65,698,051.50 | 489,602 | 452,371 |
| AB | 547,659 | 37,550,227.95 | 167,762 | 136,347 |
| SK | 434,932 | 23,593,447.53 | 106,880 | 72,904 |
| MB | 573,349 | 31,879,233.63 | 118,658 | 93,041 |
| ON | 938,855 | 66,681,544.50 | 224,346 | 201,570 |
| QC | 1,351,409 | 81,424,141.47 | 349,550 | 302,115 |
| NB | 75,492 | 6,604,566.30 | 29,097 | 24,568 |
| NS | 58,287 | 4,673,782.53 | 16,101 | 14,319 |
| PE | 5,970 | 269,064.27 | 2,472 | 2,112 |
| NL | 382,414 | 23,342,376.96 | 84,866 | 77,141 |
| YT | 425,308 | 25,808,643.72 | 126,539 | 98,802 |
| NT | 939,626 | 42,370,426.80 | 217,192 | 155,820 |
| NU | 198,315 | 1,355,123.16 | 50,952 | 33,224 |

All 13 provinces and territories are present.

## 4. Curves

The RSYC library has 3,948 rows, 385 tiles, and 555 tile-province cells.
Coniferous group rows exist for all 555 tile-province cells. Broadleaf group
rows exist for 342; the other 213 use the tile generic row. The build resolves
770 unique group/generic model ids and 718 are used by at least one national
fragment. The remaining 52 resolved curves occur only in tile-province cells
with no forest fragments (or no conifer/broadleaf fragments of that group).

Used curve roles:

| `source_species` | Curves used | Fragments | Area (ha) |
|---|---:|---:|---:|
| coniferous | 383 | 5,938,754 | 362,458,966.95 |
| broadleaf | 243 | 847,085 | 48,000,309.57 |
| generic | 92 | 21,353 | 791,353.80 |

No coefficient `model_id` maps to two coefficient sets. Every curve is finite,
non-negative, and starts at `(0, 0)`. Negative published `b4` values are
retained as published; they do not create invalid curve values over 0..600.

## 5. Validate and simulate

WS4 is the editable install at `.venv-ws4/bin/ws4`, sourced from
`/srv/shared-data/gep/ws4/ws4-gpu`, version 0.2.0.

```bash
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset
```

Result: **exit 0**, `Dataset is valid`.

Schedule: 2.0% of eligible blocks (`offset ≥ 40`), deterministically stratified
by province, offset 0:

```bash
.venv-ws4/bin/ws4 simulate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset \
  --schedule data/derived/ws4/scanfi-national-990m/schedules/national_cc_sample.csv \
  --years 20 \
  --out data/derived/ws4/scanfi-national-990m/runs/national_cc_20y
```

| Result | Value |
|---|---:|
| Eligible blocks | 1,664,334 |
| Eligible area | 360,466,511.94 ha |
| Schedule rows | 33,284 |
| Scheduled area | 7,514,902.98 ha |
| Effective fraction of eligible blocks | 1.9998% |
| Harvested volume | 968,900,774.756 m³ |
| Treated / final managed area | 7,514,902.98 ha |
| Final managed yield at offset 19 | 448,784,158.666 m³ |
| Final unmanaged yield at offset 19 | 49,594,899,616.869 m³ |

The simulated treated area equals the scheduled block area exactly. All
scheduled natural-track fragments transition to their managed track, so the
run has both nonzero harvested volume and nonzero managed area. The CC run
produces 52 product labels across 13 provinces (harvested volume and treated
area, each for the unmanaged and managed tracks); all provinces have nonzero
harvest and treated area. The dataset also carries THIN and thinned-state
labels for the partial-cut treatment, which the CC-only schedule does not
invoke. The product/account labels are the provincial labels written in the
dataset, not renamed during simulation.

## 6. Manifest and provenance

`schema_version` 0.1, model `II`, `period_length=1`, recipe
`SCANFI_RSYC_national_990m`. The manifest lists all 12 tables and SHA-256
values for the five source inputs:

| Source | SHA-256 |
|---|---|
| `SCANFI_age_median_2025_v2_20260119.tif` | `809c4bb098dd9bb75c8021dddb2d5908a09e3d928e47c9a09d0138e533b2420b` |
| `SCANFI_att_nfiLandcover_2025_v2_20260119.tif` | `668438f6388731a1cf6a9e899a3d7aeb8a998e35c97e5199007db9da03ef46b2` |
| `rsyc_tile_production_library.csv` | `23767f1ce6b198d9842f68c68c1bdb2703a96b81804d86cfe4792c8152158256` |
| `RSYC_tiles.gpkg` | `f61e0ec06b29146264ff676c98fcfa1e431d6ddd5f1d97d17e957d4d9e77308b` |
| `supply_nodes_50km.csv` | `ca4a2dac76927d04951a129bb81ee1f91e1f1f470d0b513e388df1fcdb254a03` |

Key dataset table SHA-256 values:

| Table | Rows | SHA-256 |
|---|---:|---|
| `fragments.parquet` | 6,807,192 | `f1d3d52764f141584208d609359edf9433a70d84730853608000864a644fb6f1` |
| `blocks.parquet` | 1,984,017 | `a6eac3812fe4a857a7c39c2cb09ea329312677cb994129990f33eae630e7fabf` |
| `groups.parquet` | 1,984,017 | `d3998afba3bcdf71df6b2802a614e838b65d951b339771b1ee5cb7d8afe5340c` |
| `curves.parquet` | 432,119 | `f28749fd02c024bc28f8721d640b094156e18d7baca658332a3f87a6e03a2364` |
| `tracks.parquet` | 2,799 | `aea0422f25e62e1d2c8c60ca6513710e06da6c8c63bc42596aed4755ef30408a` |

Metadata labels: `model_estimate`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `official_osecan_changed=false`, `ui_changed=false`,
`sensitivity_only=true`, `chain_v51_unchanged=true`. Metadata also carries the
explicit resolution change, node-grid diagnostics, scan totals, curve specs,
and schedule sample.

## 7. Limitations

- **Group curves only.** The national basis is the RSYC tile coniferous or
  broadleaf group curve, with the tile generic curve as the broadleaf fallback.
  Species-specific curves are not used nationally because the SCANFI species
  rasters are not processed in this build.
- **No thinning or planting response.** Natural, thinned, and managed tracks
  share one RSYC curve. The thinned track records the `THIN` partial-cut state
  transition at the pre-treatment age; the managed track records the `CC`
  management-state transition. There is no thinning, planting, silviculture,
  fertilization, genetic gain, or regeneration-delay response.
- **Not operable.** No tenure, THLB, road, slope, riparian, cutblock, green-up,
  patch-size, harvest-flow, or spatial-topology constraints. Pixel cliques are
  inventory blocks, not cutblocks or operable units.
- **Static inventory.** Age and landcover are the SCANFI 2025 snapshot. There
  is no natural disturbance, harvest-induced age reset, climate change, or
  future landcover transition in the dataset. The simulation only applies the
  supplied schedule to the static estate.
- **Resolution loss.** The 990-m fragment loses within-cell age and species
  heterogeneity. Age is an area-weighted mean rounded to an integer; the
  landcover group is a dominance rule; `group_fraction` records the dominant
  group's share but is not used to split a fragment.
- **Coverage boundary.** Forest outside the RSYC tile-province allocation grid
  is excluded (4.87M ha of forest-class pixels; 1.17% of forest-class area).
  This is an allocation/inventory-window boundary, not a claim that those
  forests do not exist.
- **Edge strip.** Two 30-m columns and three 30-m rows at the raster edge are
  not part of a complete 33 × 33 block and are ignored (0.0036% of pixels).
- **Tiny nodes.** Fifty geometric node slivers are smaller than one 990-m cell;
  they have no fragments. No fragments are fabricated for them.
- **Not policy-grade.** This is a diagnostic national estate for sensitivity
  analysis. It is not official OSeCAN, not a timber-supply forecast, and not a
  basis for tenure, investment, or operational decisions.
- **No chain/UI change.** Chain v5.1, the frozen artifacts, the UI, and official
  OSeCAN are unchanged.

## 8. Reproduction

```bash
PYTHONPATH=. .venv/bin/python -m \
  workflows.ws4_instances.build_scanfi_national_estate \
  --sample-fraction 0.02 --max-schedule-blocks 80000 --years 20 --run-ws4
```

The builder halts on an unexpected `D` in either repository, a non-30-m or
misaligned raster, a forest fragment or block without a curve, a missing
natural track, an area/count conservation failure, a curve-domain defect, a
WS4 validation failure, or a simulation with zero harvest/treated area.
