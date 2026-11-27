# WS4 tiled 30-m staged national coverage

Status: **staged national coverage in progress**, `model_estimate` /
`sensitivity_only`. `evidence_label=model_estimate`, `observed=false`,
`policy_grade=false`, `national_claim=false`, `tiled_30m=true`,
`tile_resolution_m=30000`, `inventory_resolution=30m`. No official OSeCAN
change, no chain v5.1 change, no UI change, and no change to the national
990-m WS4 dataset, the G2 30-m pilot, or the existing 24-tile representative
sample.

The full national build is **not complete**. Only the tiles recorded as
built+validated in
`data/derived/ws4/tiled-30m/national_tiled_manifest.json` are real. The
manifest has `full_run.status=staged_in_progress` until all 3,562 candidates
are built and validated.

## 1. Stage plan

Every one of the 3,562 committed 50-km supply nodes is assigned to exactly one
stage by province. Counts below are computed from the committed candidate CSV
`data/derived/national/facilities/supply_node_50km_v1/supply_nodes_50km.csv`,
not inferred from the stage table.

| Stage | Title | Provinces | Candidates | Positive harvest | Zero harvest | No-forest slivers |
|---|---|---|---:|---:|---:|---:|
| `stage1` | Atlantic + territories | NB, NS, PE, NL, YT, NT, NU | 1,254 | 1,051 | 203 (NU) | 9 |
| `stage2` | British Columbia + Alberta | BC, AB | 713 | 713 | 0 | 0 |
| `stage3` | Saskatchewan + Manitoba | SK, MB | 494 | 494 | 0 | 0 |
| `stage4` | Ontario + Quebec | ON, QC | 1,101 | 1,101 | 0 | 0 |
| **Total** | | | **3,562** | **3,359** | **203** | **9** |

The nine Stage 1 no-forest slivers were attempted and failed closed with
`no forest pixels in the 50-km window` (or `50-km rectangle selects no
pixels`). They are tiny clipped slivers, for example `J35|NL|S21`
(1.12 ha committed geometry, 0.06 m3 harvest) and `L30|NL|S31` (3.36 ha,
0.11 m3). They are recorded as `skipped_no_forest` with the exact error; no
empty or synthetic dataset was written.

NU has zero harvest for all 203 candidates in the committed CSV. By default
staged runs skip zero-harvest candidates explicitly and record them as
`skipped_zero_harvest`; they are covered by the stage plan but not fabricated
as harvest-bearing tiles. `--include-zero-harvest` builds them for geometric
completeness.

## 2. Orchestrator contract

`workflows/ws4_instances/build_scanfi_tiled_30m.py` now supports:

| Option | Behaviour |
|---|---|
| `--stage stage1..stage4` | Select the named province group from the documented plan. Mutually exclusive with `--all`. |
| `--resume` | Keep a tile only when its `dataset/manifest.json` hash matches the hash recorded in the national manifest **and** `.venv-ws4/bin/ws4 validate` exits 0. A valid tile is never overwritten. If an old tile has no recorded hash, it is validated and the hash is adopted (this preserves the pre-staging 24-tile sample). |
| `--datasets-only` | Build the 12 canonical WS4 tables + manifest, write the 10% CC schedule, and run `ws4 validate`; skip the 20-year simulation. This is the default evidence mode for coverage stages. |
| `--simulate-sample N` | After the stage build, run the 20-year simulation on up to `N` deterministic high-harvest tiles that have eligible blocks and were not already simulated. Fail closed if harvest or treated area is zero. |
| `--include-zero-harvest` | Also build zero-harvest candidates (NU). The eventual full national command uses this to reach all 3,562 tiles. |
| `--all` | Build all 3,562 candidates; still honours `--resume`, `--datasets-only`, and the zero-harvest policy. |

Resume/search semantics:

- the expected hash is `sha256(dataset/manifest.json)` from the national
  manifest tile row (`dataset_manifest_sha256`);
- a mismatched hash, a missing manifest, or a failed validation causes a
  rebuild of that tile;
- a matching hash plus a passing validation causes the existing summary to be
  reused and `build_action=resumed` is recorded;
- provenance source hashes are re-checked against the existing manifest before
  any staged build; a change aborts rather than mixing vintages.

## 3. Storage plan and gate

The data filesystem is `/dev/sda1` mounted at `/srv/shared-data` (15 TB).
At the Stage 1 start the check reported:

```text
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        15T  9.8T  4.8T  68% /srv/shared-data
free = 5,173.326 GB
required minimum = 120.000 GB
projected stage1 full-profile storage = 30.099 GB
```

The gate fails before any build when free space is below 120 GB or below the
projected stage size. Projections use the national 990-m per-province forest
area share applied to the area-scaled full-national projection (96.90 GB =
57.60 GB dataset + 39.30 GB runs/schedules), and take the larger of the
area-scaled and mean-per-candidate projections:

| Stage | Candidates | Projected full profile | Projected dataset-only |
|---|---:|---:|---:|
| `stage1` | 1,254 | 30.10 GB | 17.89 GB |
| `stage2` | 713 | 24.33 GB | 14.46 GB |
| `stage3` | 494 | 13.07 GB | 7.77 GB |
| `stage4` | 1,101 | 34.90 GB | 20.74 GB |
| **Total** | **3,562** | **96.90 GB** | **57.60 GB** |

Actual Stage 1 storage after the datasets-only build is **15.053 GB** for the
1,042 built Stage 1 tiles (1,147,599,965 fragments; 442,911,757 blocks;
103,283,996.9 ha forest). The full-profile projection is larger because it
includes 20-year run outputs, which Stage 1 did not build except for the small
simulation sample.

## 4. Stage 1 results

Stage 1 covered 1,254 candidates:

- 1,042 tile datasets built and validated (`ws4 validate` exit 0);
- 203 NU zero-harvest candidates explicitly skipped;
- 9 no-forest sliver candidates explicitly skipped with the fail-closed error;
- 0 Stage 1 candidates remaining.

Stage 1 totals for the 1,042 built tiles:

- 1,147,599,965 fragments (30-m SCANFI forest pixels);
- 442,911,757 blocks (8-neighbour equal `(age_class, curve_key)` cliques);
- 103,283,996.9 ha forest;
- 15.053 GB local storage in dataset + schedule + manifest files;
- all 12 canonical WS4 tables per tile, plus `manifest.json`;
- every tile has its own `dataset_manifest_sha256` in the national manifest,
  and every validation status is `pass`.

The sample simulation ran the 20-year, 10% CC schedule on five newly built
Stage 1 tiles (`E30|NB|S13`, `S22`, `S23`, `S33`, `S21`):

- 5/5 simulations pass;
- 14,064,633.471 m3 harvested over 20 years;
- 72,883.71 ha treated/managed, exactly equal to 72,883.71 ha scheduled;
- each tile has nonzero harvest and nonzero managed area.

These totals are transition evidence only and are not extrapolated to the
stage or nation. They are recorded in `sample_simulation` in the national
manifest.

An independent post-build validation sweep re-ran
`.venv-ws4/bin/ws4 validate` on all 1,042 Stage 1 datasets with 32 parallel
workers: **1,042 pass, 0 fail**.

The completed-tile totals in the manifest (1,048 tiles, including the six
pre-existing Stage 2-4 sample tiles) are 1,159,678,100 fragments,
447,010,798 blocks, 104,371,029.0 ha forest, and 15.364 GB local storage.

## 5. Coverage and remaining work

After Stage 1:

- 1,048 tiles are built and validated: the 1,042 Stage 1 tiles plus the six
  pre-existing Stage 2-4 representative-sample tiles that already validate
  (BC, AB, SK, MB, ON, QC);
- 203 zero-harvest NU candidates and 9 no-forest slivers are explicitly
  skipped;
- 2,302 candidates remain, in Stages 2-4.

| Stage | Candidates | Built | Remaining | Status |
|---|---:|---:|---:|---|
| `stage1` | 1,254 | 1,042 | 0 | complete (with documented skips) |
| `stage2` | 713 | 2 | 711 | not started |
| `stage3` | 494 | 2 | 492 | not started |
| `stage4` | 1,101 | 2 | 1,099 | not started |

The remaining full-profile storage projection is **87.03 GB** (per-stage
projections minus actual used bytes) and is recorded as
`storage.projected_remaining_bytes` in the manifest. The project does **not**
claim national coverage: 2,302 candidates are not built.

## 6. Commands

Run one stage, resumable, datasets + validation only, with a five-tile
transition sample:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --stage stage1 --resume --datasets-only --simulate-sample 5 --workers 32
```

Run the next stage:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --stage stage2 --resume --datasets-only --simulate-sample 5 --workers 32
```

Refresh the cumulative manifest from on-disk summaries without rebuilding:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --stage stage1 --manifest-only
```

Eventual full national run (all 3,562 candidates, including zero-harvest NU):

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --all --include-zero-harvest --workers 32 --years 20 \
  --sample-fraction 0.1
```

Use `--resume` with the full command after a partial run. Do not start a stage
when the storage gate reports less than 120 GB free or less free space than the
projected stage size.

## 7. Validation, labels, and limitations

Every built tile is validated by `.venv-ws4/bin/ws4 validate`. The build is
fail-closed on unexpected `D` in the parent or data repository status, missing
curves, geometry mismatch, raster misalignment, fragment/block area
non-conservation, schema mismatch, failed validation, or simulation treated
area not equal to scheduled area.

Labels remain `model_estimate` / `observed=false` / `policy_grade=false` /
`national_claim=false` / `tiled_30m=true` / `tile_resolution_m=30000` /
`inventory_resolution=30m`; `official_osecan_changed=false`,
`chain_v51_unchanged=true`. The staged build is not a completed national
dataset, not official OSeCAN, and not a chain v5.1 change.

Known limitations:

- one CC treatment, no planting yield response, and no operability/tenure/road/
  mill model;
- province clipping uses the simplified ADM1 boundary and reproduces the
  committed geometry to <0.01 ha, not a legal survey;
- zero-harvest NU and the nine no-forest slivers are covered by the stage plan
  but not built by default;
- the sample simulation is transition evidence, not a stage or national
  harvest estimate.
