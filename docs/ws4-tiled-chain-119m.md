# Tiled 30-m WS4 119 Mm³ chain

## Status and non-claims

This is a **sidecar verification artifact** that scans the full national
30-m tiled WS4 coverage, selects whole blocks against the 118.9773391 Mm³
provincial control, computes exact fragment-age volumes for the selected
blocks, applies the frozen chain v5.1 per-province coefficients, and rebuilds
the origin-province OSeCAN handoff.

It is a `model_estimate` (`observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`, `tiled_30m=true`). The tiled
datasets, the national 990-m WS4 dataset, frozen chain v5.1, and official
OSeCAN are unchanged (`official_osecan_changed=false`,
`chain_v51_unchanged=true`).

**Candidate release:** this chain is consolidated in
[`docs/national-forest-estate-ws4-v1.md`](national-forest-estate-ws4-v1.md)
under annotated tag `national-forest-estate-ws4-v1`.

The schedule is a whole-block arithmetic verification, not a sustainable
harvest, AAC, policy result, or investment case. No block volume, curve, or
fragment is fabricated.

The committed tiled chain was built before the THIN treatment was added to the
national 990-m dataset. Its recorded national-990m input hash is therefore
stale (`b4638c35084b0462bdd6545a87416551d71928833a263ffd0816ee5ba469dac5`);
the current national manifest hash is
`a1f2dd8d0a506b056ce01a475f8adbaa26c2471bf103e8b819f81e7f0a670ee3`. The tiled
datasets and all tiled chain/OSeCAN results are unchanged. A rerun would
refresh only that input hash and generated metadata; it was not run because the
full 3,345-tile coverage was intentionally not rebuilt.

## Artifacts

| Path | Role |
|---|---|
| `data/derived/national/facilities/ws4_tiled_chain_119m/` | Chain, comparison, selected blocks, scan summaries, report, manifest |
| `.../ws4_tiled_chain_119m/osecan/` | Origin-province zone/tier handoff and tier comparison |
| `workflows/national/facilities/ws4_tiled_chain_119m.py` | Fail-closed two-pass builder |
| `workflows/national/facilities/ws4_tiled_osecan_handoff.py` | OSeCAN rebuild from tiled chain totals |
| `tests/test_ws4_tiled_chain_119m.py` | Scan, selection, conservation, chain, OSeCAN, and simulation tests |

## Coverage

The upstream manifest accounts for all 3,562 candidates:

- **3,345** built and validated tile datasets scanned;
- **203** NU zero-harvest candidates explicitly skipped (no dataset);
- **14** no-forest slivers explicitly skipped (no dataset).

All 3,345 built datasets are scanned. Twelve provinces have tile datasets;
NU has none because every NU candidate is a zero-harvest skip. This is full
coverage of the built tiled estate, not a fabricated dataset for the 217
skipped candidates.

| Province | Tiles scanned |
|---|---:|
| BC | 454 |
| AB | 255 |
| SK | 210 |
| MB | 284 |
| ON | 444 |
| QC | 656 |
| NB | 50 |
| NS | 46 |
| PE | 13 |
| NL | 240 |
| YT | 229 |
| NT | 464 |
| NU | 0 (203 zero-harvest skips) |
| **Total** | **3,345** |

## Two-pass method

### Pass 1 — candidate scan

For each tile, only `blocks`, `tracks`, `features`, and `curves` are read.
Natural/unmanaged tracks are parsed from
`AU(<tile id>)+IFM(unmanaged)+ORIGIN(natural)`. The tiled build carries the
tile/AU code (for example `AU(N5)`), not the province code; the province is
taken from the tile slug and upstream manifest, and the AU code is checked
against the tile id. Each natural track is joined to its
`feature.Yield.*.unmanaged.Total` curve.

Block approximate volume is:

```text
approx_volume_m3 = area_ha × curve(block offset)
```

where `block offset` is the 10-year block age class. Blocks with
`offset >= 40` and positive approximate volume are ranked; the top **20,000
per tile** are written to a candidate Parquet. The scan read
**1,678,150,739** blocks, found **1,431,297,895** eligible blocks with
**39.141 billion m³** of approximate volume, and wrote **62,163,912**
candidates with **13.456 billion m³** of approximate volume.

Per province, the candidate pool is the top **2,000,000** candidates by
approximate volume (or all candidates if smaller), and must exceed the
provincial target by at least 10%. Every province passes; the smallest pool
margin is 41.5× for NB.

### Selection

Whole blocks are selected by a volume-aware greedy in descending volume. The
greedy fills while the next block fits and then chooses the unselected block
that minimises overshoot. A block is never selected twice. Because tiled
blocks can be large contiguous cliques, exact volumes are computed for the
top **50,000** provincial candidates by approximate volume (expanded in
50,000-block batches if the target is not reached or the overshoot exceeds
`max(1,000 m³, 0.05% of target)`), and the greedy is re-run on exact volumes.
The final schedule is a subset of those exact-computed blocks.

### Pass 2 — exact volume

For candidate blocks, the tile's `fragments` are read and block volume is:

```text
exact_volume_m3 = Σ fragment area_ha × curve(fragment age)
```

at the exact integer fragment age. Fragment area must equal block area within
1e-6 ha, and every fragment must belong to the block's natural track. No
block is split, scaled, or fabricated. The exact/approximate total ratio
ranges from 1.0036 (NT) to 1.0619 (AB), which is why exact re-selection is
required.

## Exact harvest by province

| Province | Target Mm³ | Exact Mm³ | Delta m³ | Overshoot | Area ha | Blocks | Tiles | Exact/approx |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BC | 39.978577 | 39.979843 | 1,266.312 | 0.0032% | 255,377.25 | 29 | 26 | 1.035342 |
| AB | 22.891404 | 22.893881 | 2,477.065 | 0.0108% | 212,312.79 | 11 | 10 | 1.061935 |
| SK | 4.344698 | 4.345555 | 857.476 | 0.0197% | 64,280.97 | 6 | 6 | 1.039005 |
| MB | 1.135236 | 1.135894 | 657.881 | 0.0580% | 9,264.06 | 3 | 3 | 1.010946 |
| ON | 13.493596 | 13.494425 | 828.572 | 0.0061% | 67,809.06 | 5 | 5 | 1.009090 |
| QC | 26.674334 | 26.676208 | 1,873.514 | 0.0070% | 362,592.81 | 22 | 20 | 1.016830 |
| NB | 6.234700 | 6.235446 | 746.141 | 0.0120% | 35,912.34 | 240 | 31 | 1.031574 |
| NS | 2.388245 | 2.388791 | 546.054 | 0.0229% | 23,190.48 | 9 | 8 | 1.012224 |
| PE | 0.302210 | 0.302285 | 74.484 | 0.0246% | 2,124.90 | 93 | 9 | 1.024781 |
| NL | 1.454038 | 1.454944 | 906.059 | 0.0623% | 29,808.45 | 5 | 4 | 1.004871 |
| YT | 0.043926 | 0.044371 | 445.636 | 1.0145% | 294.57 | 2 | 2 | 1.047636 |
| NT | 0.036376 | 0.037228 | 852.302 | 2.3430% | 141.21 | 2 | 2 | 1.003608 |
| NU | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| **National** | **118.977339** | **118.988871** | **11,531.493** | **0.0097%** | **1,063,108.89** | **427** | **126** | |

The national overshoot is 11,531 m³ (0.0097%). YT and NT have the largest
percentage overshoots because their targets are smaller than one or two large
whole blocks; their absolute overshoots are under 1,000 m³. NU has a zero
control target and no selected block.

## Chain v5.1 comparison

| Metric | Unit | Frozen v5.1 | Tiled achieved | Delta | Scaled | Delta |
|---|---|---:|---:|---:|---:|---:|
| Harvest | m³ | 118,977,339.100 | 118,988,870.593 | +11,531.493 | 118,977,339.100 | 0.000 |
| Solid product | m³ | 37,673,626.761 | 37,676,824.035 | +3,197.274 | 37,673,172.685 | −454.076 |
| Pulp product | odt | 12,066,311.270 | 12,067,593.917 | +1,282.647 | 12,066,424.418 | +113.148 |
| Process residue | PJ | 498.750917 | 498.798706 | +0.047789 | 498.750367 | −0.000550 |
| Harvest gross | PJ | 444.410766 | 444.459682 | +0.048916 | 444.416608 | +0.005842 |
| Harvest 60% | PJ | 266.646460 | 266.675809 | +0.029349 | 266.649965 | +0.003505 |
| Total gross | PJ | 943.161683 | 943.258388 | +0.096705 | 943.166975 | +0.005292 |

`solid_product_m3` is sawlog product plus panel product. Process residue and
harvest residue remain separate streams; PJ values use the frozen
18.5 GJ/ODT lower heating value. The scaled basis uses one national factor
`0.999903087632` preserving tiled provincial shares and matching the
118.9773391 Mm³ control total.

The scaled basis differs from frozen v5.1 because the tiled block selection
changes provincial harvest shares slightly relative to the provincial control
allocation. The differences are small (solid −0.0012%, pulp +0.00094%,
process residue −0.00011%, total gross +0.00056%).

## OSeCAN handoff

The origin-province handoff was rebuilt from the tiled chain totals using the
frozen handoff-v10 province-to-zone weights and the official OSeCAN tier
definitions:

- 182 zone rows (2 bases × 13 zones × 7 cases) conserve national totals;
- frozen v5.1 reference positions match handoff v10 **21/21**;
- tiled 119 Mm³ primary (`scaled_to_119m_control`) tier positions match
  handoff v10 **7/7**;
- tiled 119 Mm³ achieved tier positions match handoff v10 **7/7**;
- official OSeCAN tree unchanged: **146 files**,
  `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
  0 changed files.

Scaled national PJ: process 0 / 124.687592 / 249.375183 / 374.062775 at
0/25/50/75%; harvest 155.545813 / 266.649965 / 333.312456 at 0.35/0.60/0.75.
The 100/150 Mm³ positions are marked not available rather than fabricated.

## WS4 sample simulation

Three selected tiles were independently validated and simulated for one year
with `CC` at offset 0:

| Tile | Province | Blocks | Exact m³ | Simulated m³ | Delta m³ |
|---|---|---:|---:|---:|---:|
| E30-NB-S13 | NB | 30 | 934,650.983 | 934,650.983 | −1.2e-10 |
| F33-NS-S32 | NS | 1 | 284,530.135 | 284,530.135 | 0 |
| T6-NT-S22 | NT | 2 | 901.457 | 901.457 | −1.1e-13 |

Simulated treated area equals exact fragment area within 1e-9 relative
tolerance. Full 3,345-tile simulation is not required and was not run.

## Verification

| Check | Result |
|---|---|
| Candidate scan coverage | 3,345/3,345 built tiles; 1,678,150,739 blocks read |
| Candidate pool margin | Every positive-target province ≥ 10% margin |
| Block uniqueness | 427 selected rows; no duplicate `(tile, block)` |
| Block eligibility | All natural/unmanaged, offset ≥ 40, exact volume > 0 |
| Exact area conservation | Max block/fragment area gap 0 ha |
| Target/overshoot | National +11,531.493 m³ (0.0097%) |
| Chain coefficients | Both bases reproduce frozen per-m³ coefficients |
| Scaled control total | 118,977,339.100 m³ |
| OSeCAN tiers | 7/7 primary, 7/7 achieved, 21/21 frozen reference |
| Official OSeCAN | 146 files, hash `80caa91c...`, unchanged |
| No NaN/negative | all numeric artifact values finite; residue values non-negative |
| WS4 sample simulation | 3/3 pass, volume and area match exact |
| Unexpected `D` | none in parent or data repository |

Reproduce:

```bash
.venv/bin/python -m workflows.national.facilities.ws4_tiled_chain_119m --workers 32
.venv/bin/pytest -q tests/test_ws4_tiled_chain_119m.py
```

## Caveats

- The schedule is a whole-block arithmetic verification, not a sustainable
  harvest, AAC, policy result, or investment case.
- Pass 1 uses `area_ha × curve(block offset)`; pass 2 uses exact fragment ages.
  The exact/approximate ratio is reported per province.
- The candidate pool is a documented top-per-tile volume screen, not a claim
  that smaller blocks are inoperable.
- 30-m forest-area fragments and group RSYC curves are inventory
  realisations, not operable or tenure-feasible areas.
- One `CC` treatment at offset 0 is simulated on the sample tiles. Planting
  response, silviculture, fertilization, and genetic gain are not modelled.
- Product and residue values apply frozen v5.1 coefficients by origin
  province. Facility assignment, mill capacity, transport, and
  interprovincial trade are not re-run.
- The scaled basis is one national factor preserving tiled shares, not
  per-province target scaling.
- 0.60 harvest recovery is a reference case, not a calibrated recovery factor.
- No official OSeCAN file, frozen chain v5.1 artifact, national 990-m dataset,
  or tiled dataset is changed.
