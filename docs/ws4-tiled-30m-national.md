# WS4 tiled 30-m national architecture (representative run and staged completion)

Status: **model-estimate sensitivity sidecar**. `evidence_label=model_estimate`,
`observed=false`, `policy_grade=false`, `national_claim=false`,
`tiled_30m=true`, `tile_resolution_m=30000`, `inventory_resolution=30m`.
No official OSeCAN change, no chain v5.1 change, no UI change, and no change to
the national 990-m WS4 dataset or the G2 30-m pilot dataset.

## 1. What this is

The national 990-m estate is one tractable dataset. This artifact implements the
requested tiled alternative: **one WS4 dataset per committed 50-km supply node**,
with every 30-m SCANFI forest pixel as a WS4 fragment. A representative
24-tile sample was built, validated, and simulated first. The full
3,562-candidate staged build was subsequently completed: 3,345 datasets are
built and validated, and 217 candidates are explicitly skipped as zero-harvest
(203 NU) or no-forest slivers (14). See
`docs/ws4-tiled-30m-staged-national.md` for the stage evidence.

The sample is:

- the highest-harvest 50-km node in every province with positive harvest
  (12 provinces; NU has zero harvest and is skipped explicitly);
- every committed PEI node (13 nodes across the six unique RSYC parent tiles
  that intersect PEI). The committed PEI grid has 13 50-km subnodes, not six
  standalone tiles; all 13 are built so the small-province example is complete.

## 2. Architecture

### 2.1 Tile geometry

Each node is a 50-km subcell of a parent RSYC 150-km tile (3 x 3 equal split in
EPSG:3978). The inventory window is:

```text
50-km subcell  intersection  province ADM1 geometry
```

This is the committed `subcell_geometry_area_ha` geometry, not the full
un-clipped square. Clipping matters because 398 of the 3,562 committed nodes are
multi-province rectangles: the same rectangle appears once per intersecting
province. Clipping makes the 3,562 datasets partition the land base instead of
building the same forest twice. The full-run validation is that the sum of all
committed `subcell_geometry_area_ha` equals the ADM1 land area
(708,967,117.68 ha), and every selected tile's reconstructed clipped geometry
matches its committed value (the build fails otherwise).

The G2 pilot used the full 50-km square and reported 2,446,363 fragments. The
tiled G2|BC|S32 dataset uses the BC-clipped geometry and has 2,445,603 fragments
and 220,104.27 ha of forest; the small difference is the cross-boundary forest
excluded by the clip. This difference is expected and is not a data loss.

### 2.2 Fragments, blocks, curves

- **Fragment**: one 30-m SCANFI forest pixel (0.09 ha). `age` is the SCANFI
  median age; `track_id` is the natural track for the resolved curve.
- **Block**: an 8-neighbour connected component of forest pixels with equal
  `(age_class, curve_key)`. `age_class = floor(age / 10) * 10`.
- **Forest mask**: NFI landcover classes 5 (broadleaf), 6 (coniferous), and
  7 (generic); valid age > 0; excludes landcover nodata 255 and age nodata
  -32768.
- **Curve mapping**: dominant SCANFI species closure, then exact RSYC species
  curve, then the tile group curve (`CONIFEROUS` / `BROADLEAF`), then the tile
  generic curve. Group rows are recognised by `source_species` as well as by the
  literal `species` value, matching the committed RSYC library. A forest pixel
  with no resolvable curve fails the tile build; no curve is fabricated.
- **Tables**: the twelve canonical WS4 tables (`fragments`, `blocks`, `tracks`,
  `strata`, `treatments`, `curves`, `features`, `products`, `protoaccounts`,
  `accounts`, `groups`, `messages`) plus `manifest.json`.
- **Treatment**: the shared builder now emits a natural -> thinned `THIN`
  partial cut at age 20-40 (negative offset retains age) and a thinned -> managed
  `CC` at age 40, alongside the existing clear-cut (CC). All three tracks share
  the same RSYC curve: there is no thinning or planting yield-response data.
  The committed 3,345-tile coverage and the 24-tile representative sample
  predate this builder change and were not rebuilt; their schedules remain the
  10% CC schedule and their datasets contain only the original natural/managed
  tracks.

### 2.3 Output contract

```text
data/derived/ws4/tiled-30m/
  national_tiled_manifest.json
  README.md
  <node_slug>/
    README.md
    inventory_summary.json
    dataset/                 # 12 parquet tables + manifest.json
    schedules/cc_10pct.csv
    runs/cc_20y/             # ws4 simulate output
```

`node_slug` is the node identity with `|` replaced by `-` (for example
`G2-BC-S32`). Large parquet and CSV payloads are git-annex objects.

## 3. Selected tiles and per-tile evidence

Selection rules are implemented in
`workflows/ws4_instances/build_scanfi_tiled_30m.py`. Ties are broken by
`node_id`. Every tile was validated with `ws4 validate` and simulated for 20
years with a 10% schedule of eligible blocks (age class >= 40) at offset 0.
When a tile has fewer than 5,000 eligible blocks the schedule includes all
eligible blocks, matching the pilot convention.

| Province | Node | Fragments | Blocks | Forest ha | Curves | Validate | Sim 20y harvest m3 | Treated ha |
|---|---|---:|---:|---:|---:|---|---:|---:|
| AB | `H7|AB|S11` | 1,952,612 | 747,816 | 175,735.1 | 4 | pass | 4,193,142 | 15,555.1 |
| BC | `G2|BC|S32` | 2,445,603 | 931,483 | 220,104.3 | 4 | pass | 6,647,970 | 18,121.2 |
| MB | `C16|MB|S31` | 1,686,082 | 564,657 | 151,747.4 | 4 | pass | 1,608,745 | 12,538.6 |
| NB | `E30|NB|S12` | 2,597,813 | 939,888 | 233,803.2 | 5 | pass | 2,883,744 | 14,721.7 |
| NL | `J31|NL|S11` | 2,267,140 | 667,681 | 204,042.6 | 4 | pass | 1,563,949 | 19,021.6 |
| NS | `C32|NS|S31` | 2,434,134 | 968,652 | 219,072.1 | 5 | pass | 3,138,631 | 18,761.5 |
| NT | `N6|NT|S13` | 2,590,695 | 1,184,250 | 233,162.5 | 4 | pass | 3,216,356 | 22,135.4 |
| ON | `B25|ON|S11` | 2,451,097 | 634,134 | 220,598.7 | 5 | pass | 3,456,426 | 18,558.8 |
| PE | `E32|PE|S33` | 1,090,053 | 463,089 | 98,104.8 | 4 | pass | 902,300 | 7,083.9 |
| PE | `E31|PE|S33` | 350,615 | 134,789 | 31,555.3 | 4 | pass | 335,027 | 2,111.8 |
| PE | `E32|PE|S32` | 307,679 | 153,280 | 27,691.1 | 4 | pass | 239,057 | 1,807.8 |
| PE | `E32|PE|S22` | 232,566 | 111,346 | 20,930.9 | 4 | pass | 210,119 | 1,562.2 |
| PE | `E32|PE|S31` | 372,693 | 142,406 | 33,542.4 | 4 | pass | 320,461 | 2,471.9 |
| PE | `E32|PE|S21` | 224,005 | 82,463 | 20,160.5 | 4 | pass | 193,201 | 1,481.6 |
| PE | `E32|PE|S23` | 205,267 | 83,470 | 18,474.0 | 4 | pass | 167,550 | 1,325.8 |
| PE | `F32|PE|S13` | 145,228 | 61,728 | 13,070.5 | 4 | pass | 107,614 | 1,031.6 |
| PE | `F33|PE|S11` | 77,427 | 38,983 | 6,968.4 | 4 | pass | 51,901 | 588.9 |
| PE | `E31|PE|S23` | 25,500 | 10,450 | 2,295.0 | 4 | pass | 26,245 | 164.2 |
| PE | `E33|PE|S31` | 9,582 | 4,633 | 862.4 | 4 | pass | 82,906 | 653.2 |
| PE | `F31|PE|S13` | 1,378 | 823 | 124.0 | 4 | pass | 16,109 | 105.2 |
| PE | `E33|PE|S21` | 253 | 128 | 22.8 | 4 | pass | 1,850 | 17.3 |
| QC | `C29|QC|S31` | 2,170,886 | 707,979 | 195,379.7 | 4 | pass | 2,914,716 | 16,121.9 |
| SK | `H10|SK|S13` | 1,371,855 | 512,972 | 123,466.9 | 4 | pass | 1,555,609 | 10,976.9 |
| YT | `O3|YT|S33` | 2,154,768 | 928,237 | 193,929.1 | 4 | pass | 2,528,973 | 17,218.1 |

Selected totals:

- 24 tile datasets; 24 distinct 50-km rectangles.
- 27,164,931 fragments.
- 10,075,337 blocks.
- 2,444,843.79 ha of forest (24,448.44 km2).
- 3,296,519.25 ha of clipped tile geometry.
- 576.06 MB of local storage: 342.43 MB dataset, 10.69 MB schedules,
  222.94 MB 20-year run outputs.
- All 24 `ws4 validate` exit 0; all 24 simulations pass and the simulated
  treated area equals the scheduled block area exactly.

The selected geometry total (3,296,519.25 ha) is smaller than
24 x 250,000 ha because PEI and other tiles are clipped to their province
portion. That is the intended partition, not missing data.

## 4. Full national scale and storage

The national manifest contains all 3,562 candidates with `node_id`, province,
tile, row/col, harvest, geometry area, and selected flag. They cover 3,164
unique 50-km rectangles; 398 rectangles are multi-province and are split into
province-specific datasets.

Arithmetic extrapolation is not a national build. Two projections are reported:

- **Area-scaled (preferred)**: scale by the national 990-m forest area
  (411,250,630.32 ha) divided by the selected-tile forest area
  (2,444,843.79 ha), a factor of 168.21:
  - ~4,569,451,448 fragments;
  - ~1,694,786,681 blocks;
  - ~57.60 GB dataset, ~37.50 GB 20-year run outputs, ~96.90 GB total.
- **Mean-per-selected-tile x 3,562**: ~4,031,728,509 fragments,
  ~1,495,347,933 blocks, ~85.50 GB total. This projection is lower because the
  sample deliberately over-represents small PEI tiles.

The area-scaled projection is the storage-scale statement: a full national
30-m tiled build is roughly 58 GB of parquet datasets plus run outputs
(about 97 GB at the selected 20-year/10% run profile), before versioning and
safety margin. It is a distributed job, not a workstation job.

Exact full-run command:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --all --include-zero-harvest --workers 32 --years 20 \
  --sample-fraction 0.1
```

The staged completion used:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --stage stageN --resume --datasets-only --simulate-sample 5 --workers 32
```

`--all` selects all 3,562 candidates and writes the same
`national_tiled_manifest.json`. The manifest now records
`full_run.status=complete`, `coverage_complete=true`, 3,345 built+validated
tiles, 203 zero-harvest skips, 14 no-forest skips, and 0 remaining.

## 5. Validation and fail-closed behaviour

Every tile build checks:

- no unexpected `D` in the parent or data repository status (checked at
  orchestration boundaries);
- the reconstructed 50-km rectangle is 250,000 ha;
- the clipped geometry matches the committed `subcell_geometry_area_ha`;
- all SCANFI rasters are 30-m aligned, same CRS/transform, 900 m2 pixels;
- every forest pixel has a resolved curve (exact, group, or generic);
- fragment count equals the inventory count and fragment/block area conserve;
- block ids are contiguous from one and every fragment references a block;
- the twelve tables match the canonical schema;
- `ws4 validate` exits 0;
- simulated treated area equals scheduled block area.

A missing curve, missing tile, mismatched geometry, invalid raster, or failed
validation aborts the tile and marks the national manifest failed. The build
does not fabricate a tile, curve, block, or schedule row.

## 6. Limitations

- **No partial harvest in the committed artifacts.** The builder emits THIN,
  but the committed coverage/sample datasets predate it and contain only the
  original natural/managed CC tracks. There is no treatment optimization in
  either case, and THIN claims no thinning yield response.
- **No planting response.** The managed track shares the natural RSYC curve;
  it records the management-state transition, not a yield gain.
- **Curve resolution is the pilot's exact -> group -> generic mapping.** Species
  curves come from the committed RSYC tile library; no new curve is fitted.
- **No operability, tenure, road, mill, or haul model.** The 50-km node is a
  geometric supply allocation, not a cutblock or millshed. Forest area is not
  THLB.
- **Province clipping uses the simplified ADM1 boundary.** It reproduces the
  committed supply-node geometry to <0.01 ha for the selected tiles, but it is
  not a legal boundary survey.
- **The sample is not the national distribution.** It is 12 province-top tiles
  plus PEI; the area-scaled projection is arithmetic, not a statistical
  estimate of variance.
- **Full geometric coverage is staged-complete, not a policy product.**
  All 3,562 candidates are accounted for by 3,345 real built+validated
  datasets or 217 explicit zero-harvest/no-forest skips; the 217 skips are
  not fabricated datasets.
- **Not policy-grade.** `policy_grade=false`, `national_claim=false`,
  `observed=false`; no official OSeCAN or chain v5.1 change.

## 7. Reproduction and evidence

Build the representative sample:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m --workers 8
```

Rebuild the national manifest from existing per-tile summaries:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m --manifest-only
```

Validate one tile directly:

```bash
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/tiled-30m/G2-BC-S32/dataset
```

Tests:

```bash
.venv/bin/python -m pytest tests/test_ws4_tiled_30m.py -q
```

Evidence files:

- `data/derived/ws4/tiled-30m/national_tiled_manifest.json` — candidates,
  selection, per-tile counts/validation/simulation, totals, extrapolation,
  provenance hashes, full-run command.
- `data/derived/ws4/tiled-30m/<node_slug>/inventory_summary.json` — per-tile
  geometry, exclusions, age distribution, curve diagnostics, clique sizes,
  track/treatment rows, validation output, simulation totals, storage.
- `data/derived/ws4/tiled-30m/<node_slug>/dataset/manifest.json` — WS4 dataset
  manifest with source hashes and tiled labels.

The national manifest hashes the age, landcover, and 11 species SCANFI rasters,
the RSYC library, the RSYC tile GeoPackage, the supply-node CSV, and the ADM1
boundary. It also records the WS4 executable/source tree/version.
