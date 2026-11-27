# WS4 THIN partial-cut treatment and THIN -> CC transition

## Status and non-claims

This is a **`model_estimate` / `sensitivity_only`** model change. It is not
official OSeCAN, does not change frozen chain v5.1, the tiled 30-m chain result
values, the tiled OSeCAN handoff, or the UI, and makes no policy-grade, tenure,
operability, investment, or national supply claim. The full 3,345-tile tiled
30-m coverage now carries THIN metadata; its natural/managed blocks and CC
results are unchanged. No adoption or promotion is claimed here; that is a
separate release step.

**Candidate release:** this THIN evidence is consolidated in
[`docs/national-forest-estate-ws4-v1.md`](national-forest-estate-ws4-v1.md)
under annotated tag `national-forest-estate-ws4-v1`.

The THIN treatment is a **state transition**, not a thinning-response model.
Natural, thinned, and managed tracks share the same RSYC curve. No thinning,
planting, silviculture, fertilization, or genetic yield response is claimed.
THIN harvests volume at the fragment's pre-treatment age and keeps that age.

## 1. Model change

Each curve now carries three tracks:

```text
natural: AU(<p>)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(baseline)+CURVE(<key>)+treatment()
thinned: AU(<p>)+IFM(unmanaged)+ORIGIN(natural)+SILV_STATE(thinned)+CURVE(<key>)+treatment()
managed: AU(<p>)+IFM(managed)+ORIGIN(planted)+SILV_STATE(cc_pl)+CURVE(<key>)+treatment()
```

Treatments:

| From | Treatment | Begin | End | New track | Offset | Retain |
|---|---|---:|---:|---|---:|---:|
| natural | THIN | 20 | 40 | thinned | -1 | 0 |
| natural | CC | 40 | 1000 | managed | 0 | 0 |
| thinned | THIN | 20 | 40 | thinned | -1 | 0 |
| thinned | CC | 40 | 1000 | managed | 0 | 0 |
| managed | THIN | 20 | 40 | managed | -1 | 0 |
| managed | CC | 40 | 1000 | managed | 0 | 0 |

`offset=-1` is the WS4 partial-cut sentinel: the fragment keeps its current
age. A non-negative treatment offset resets the age to that value (the existing
`CC` behaviour). The thinned track is appended after the existing
natural/managed pairs, so existing natural/managed track ids and CC references
remain stable.

Features and products:

```text
feature.Yield.<p>.unmanaged.Total
feature.Yield.<p>.thinned.Total
feature.Yield.<p>.managed.Total

product.HarvestedVolume.<p>.<state>.THIN
product.Treated.<p>.<state>.THIN
product.HarvestedVolume.<p>.<state>.CC
product.Treated.<p>.<state>.CC
```

`<state>` is `unmanaged`, `thinned`, or `managed`. `accounts` and
`protoaccounts` contain one row per unique label, group `_ALL_`, attribute =
account = label, and `sum=1.0`. Every track has one strata row
(`offset=0`, `next_age=300`, `cycle=0`).

## 2. WS4 negative-offset sentinel

The installed WS4 0.2.0 simulator assigned every treatment offset directly,
including negative values, and then advanced the age by one year. A `-1` THIN
offset therefore reset the fragment to age zero instead of retaining its age.
The partial-cut sentinel is implemented as seven guarded assignments in
`ws4/sim/engine.py` (simulation, block-chain, route-chain, and product
helpers): a negative transition offset leaves the current offset unchanged.

The patch is committed in this repository as
`patches/ws4-negative-offset-retain.patch` and is applied to the editable WS4
source at `/srv/shared-data/gep/ws4/ws4-gpu`:

```bash
cd /srv/shared-data/gep/ws4/ws4-gpu
git apply /srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/patches/ws4-negative-offset-retain.patch
```

Without the patch, THIN still validates and runs, but the fragment age resets;
the transition evidence below fail-closes on that behaviour.

## 3. Builders and rebuilt artifacts

`workflows/ws4_instances/build_scanfi_estate_pilot.py` and
`workflows/ws4_instances/build_scanfi_national_estate.py` emit the THIN
tracks/treatments/features/products/accounts. The tiled 30-m builder imports
the same pilot builder, so future tile builds also emit THIN.

Rebuilt in this change:

- national 990-m dataset
  `data/derived/ws4/scanfi-national-990m/dataset/`;
- G2 30-m pilot
  `data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset/`.

Not rebuilt:

- frozen chain v5.1, official OSeCAN, the tiled 30-m chain artifact, and the
  tiled OSeCAN handoff artifact.

The 24-tile representative sample is a subset of the full coverage and
received the same metadata-only THIN patch; its datasets were not rebuilt.

### 3.1 Full tiled 30-m coverage (metadata-only patch)

The 3,345 built+validated tile datasets predated the shared builder change.
They were patched in place by
`workflows/ws4_instances/propagate_thin_tiled_30m.py` without rebuilding
fragments, blocks, or curves:

- one `SILV_STATE(thinned)` track appended per natural baseline track;
- one strata row (`offset=0`, `next_age=300`, `cycle=0`) per thinned track;
- natural THIN, thinned THIN, thinned CC, and managed THIN treatments;
- thinned feature, THIN/CC products for every state, and the matching accounts;
- natural/managed track ids, CC treatments, fragments, blocks, curves, groups,
  and messages preserved.

Cumulative added metadata:

| Table | Rows added |
|---|---:|
| Tracks | 12,892 |
| Strata | 12,892 |
| Treatments | 51,568 |
| Features | 12,892 |
| Products | 103,136 |
| Accounts / protoaccounts | 30,105 each |

The patch is idempotent: a complete tile is skipped, and a partial tile is
repaired by removing only THIN-derived rows before rebuilding them. Per-tile
records live at `data/derived/ws4/tiled-30m/<tile>/thin_patch.json`; the
cumulative report is
`data/derived/ws4/tiled-30m/thin_propagation.json`.

```bash
.venv-ws4/bin/python -m workflows.ws4_instances.propagate_thin_tiled_30m \
  --workers 32
```

The command also runs the independent 32-worker validation sweep and the THIN
smoke transition. It does not commit, push, or claim adoption.

## 4. Validation

Both rebuilt datasets pass `ws4 validate` (exit 0):

```bash
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset
```

Dataset shape:

| Quantity | National 990 m | G2 30 m pilot |
|---|---:|---:|
| Fragments | 6,807,192 | 2,446,363 |
| Blocks | 1,984,017 | 932,041 |
| Tracks | 2,799 | 12 |
| Strata | 2,799 | 12 |
| Treatments | 5,598 | 24 |
| Features | 2,799 | 12 |
| Products | 11,196 | 48 |
| Accounts / protoaccounts | 195 | 15 |
| `ws4 validate` | exit 0 | exit 0 |

The national track count is 3 x 933 province-curve pairs; the pilot is 3 x 4
curves. The national dataset has 2,799 THIN treatment rows (one natural ->
thinned, one thinned self, and one managed self per pair), all `begin=20`,
`end=40`, `offset=-1`, `retain=0`. The pilot has 12 THIN rows with the same
values. The pilot CC simulation is unchanged at 6,547,384.791 m3 harvested and
17,844.30 ha treated, because natural/managed track ids and the CC schedule
are unchanged by the appended thinned tracks.

The full tiled coverage was validated independently after the metadata patch:

- **3,345 / 3,345 tiles pass** `.venv-ws4/bin/ws4 validate` (32 workers,
  0 failures);
- a four-curve tile now carries 12 tracks, 12 strata, 24 treatments,
  12 features, 48 products, and 15 account labels, matching the G2 pilot shape;
- cumulative totals are 12,892 thinned tracks, 51,568 added treatment rows,
  12,892 thinned features, 103,136 added product rows, and 30,105 added
  account rows per account table;
- natural fragments, blocks, curves, groups, and messages are byte-identical
  to the pre-patch files; every per-tile record has equal CC contract hashes
  before and after the patch.

Validation counts and per-tile patch hashes are recorded in
`data/derived/ws4/tiled-30m/national_tiled_manifest.json` and
`data/derived/ws4/tiled-30m/thin_propagation.json`.

## 5. THIN -> CC transition evidence

Artifact: `data/derived/ws4/thin-transition-national-990m/`.

Selection: 40 whole blocks with block offset in `[20, 40]`, spread across all
13 provinces/territories (AB, BC, MB, NB, NL, NS, NT, NU, ON, PE, QC, SK, YT).
The schedule applies `THIN` at offset 0 and `CC` to the same blocks at offset
10; `ws4 simulate --years 20` ran to completion.

| Evidence | Value |
|---|---:|
| Selected blocks / fragments | 40 / 127 |
| Scheduled area | 6,273.540 ha |
| THIN harvested volume | 588,608.208 m3 |
| THIN treated area | 6,273.540 ha |
| `feature.Yield.<p>.thinned.Total` at offset 0 | 588,608.208 (= pre-treatment curve volume) |
| Expected pre-treatment volume from fragment ages | 588,608.208 m3 |
| Thinned CC harvest at offset 10 | 671,252.422 m3 |
| Expected age+10 curve volume | 671,252.422 m3 |
| Thinned CC treated area (moved to managed) | 6,273.540 ha |
| Final managed value at offset 19 | 195,071.522 m3 |
| Final thinned value at offset 19 | 0.0 m3 |

The offset-0 thinned feature equals the volume at the fragment's original age,
not zero: the THIN transition retained age. The offset-10 CC harvest equals the
curve at `age + 10`, not `age 10`, and the whole treated area moves to managed.
No NaN appears in the account or product values. A second run on the G2 pilot
(12 blocks, 12.15 ha) reproduces the same pattern: THIN 3,660.277 m3, CC
4,059.951 m3, final managed 1,201.670 m3, final thinned 0.

### 5.1 Tiled 30-m smoke

The same check ran on the patched tile `A24-ON-S11` (20 blocks, 2.34 ha):

| Evidence | Value |
|---|---:|
| THIN harvested volume | 261.765593 m3 |
| THIN treated area | 2.34 ha |
| `feature.Yield.thinned.Total` at offset 0 | 261.765593 m3 |
| Expected pre-treatment volume | 261.765593 m3 |
| Thinned CC harvest at offset 10 | 311.964353 m3 |
| Thinned CC treated area | 2.34 ha |
| Final managed value at offset 19 | 66.354348 m3 |
| Final thinned value at offset 19 | 0.0 m3 |

The retained thinned value equals the pre-treatment volume to machine
precision, so the negative-offset sentinel works on the patched tiled tables.
Artifact: `data/derived/ws4/thin-transition-tiled-30m-smoke/`.

Run command:

```bash
.venv-ws4/bin/python -m workflows.ws4_instances.run_ws4_thin_transition \
  --dataset data/derived/ws4/tiled-30m/A24-ON-S11/dataset \
  --out data/derived/ws4/thin-transition-tiled-30m-smoke \
  --max-blocks 20 --years 20
```

## 6. Chain and OSeCAN hash references

Rebuilding the national 990-m dataset changes its manifest hash from
`b4638c35084b0462bdd6545a87416551d71928833a263ffd0816ee5ba469dac5` to
`a1f2dd8d0a506b056ce01a475f8adbaa26c2471bf103e8b819f81e7f0a670ee3`.
The national WS4 119 Mm3 chain and the national WS4 OSeCAN handoff were rerun.
Their numeric results are bit-for-bit unchanged:

- national harvest 118,977,392.87970258 m3, 1,083,739.05 ha, 45 schedule rows;
- all provincial harvest and product/residue values unchanged;
- OSeCAN handoff status pass, official tree `80caa91c...` unchanged, 21/21
  frozen reference tiers, 7/7 WS4 tiers on both bases.

The tiled THIN metadata patch changed the tiled national manifest hash from
`bb1f831b7ba5814d68d457f496a72bddc9e342aa49e50730858d8a8d5ba418eb` to
`2f2d55a2f30eaf185c2ab50a9260a6bf2f092070cbad4eeb2b97242b003e1509`;
`data/derived/ws4/tiled-30m/thin_propagation.json` has SHA-256
`45d158b3659330b8cbc75d89afdb9978d7a17ee1744e1deb6d11c0c14ed9b7f0`.
Those changes are metadata only: the tiled chain, its schedule, and the
OSeCAN handoff were not rerun and their result values are unchanged.

The tiled 30-m chain artifact records the national 990-m manifest as a
read-only input. Its recorded hash is now stale
(`b4638c35084b0462bdd6545a87416551d71928833a263ffd0816ee5ba469dac5`);
its own tiled coverage, schedule, and results are unchanged. A tiled-chain
rerun would update only that input hash and the generated timestamp; it is not
required for the tiled results and was not run because the full 30-m coverage
was intentionally not rebuilt.

## 7. Reproduce

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_estate_pilot --run-ws4
.venv/bin/python -m workflows.ws4_instances.build_scanfi_national_estate --run-ws4
.venv/bin/python -m workflows.ws4_instances.propagate_thin_tiled_30m \
  --workers 32
.venv/bin/python -m workflows.ws4_instances.run_ws4_thin_transition \
  --max-blocks 40 --years 20
.venv/bin/python -m workflows.national.facilities.ws4_chain_119m
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_ws4
```

The tiled propagation command patches metadata only; it does not rebuild the
3,345 datasets, does not rerun the CC simulations, and does not change the
tiled chain or OSeCAN handoff. A rerun skips complete tiles, so it is safe to
run after a partial patch or a validation failure.

The national rebuild resets its output directory, so the WS4 119 Mm3 schedule
and run live under that directory and must be regenerated by the chain command
shown above. The builder's final `D` guard correctly fails closed if the old
chain files are deleted before the chain is rerun; restore them with
`git -C data checkout -- <paths>` or rerun the chain, then run the guard again.
