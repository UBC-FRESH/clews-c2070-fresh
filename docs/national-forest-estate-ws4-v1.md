# National forest-estate WS4 v1 — candidate release

**Release:** `national-forest-estate-ws4-v1` (annotated tag)
**Status:** candidate national forest-estate model
**Evidence labels:** `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`
**Boundary:** not official OSeCAN; not a chain v5.1 replacement; no UI change

This document consolidates the WS4 national forest-estate work into one
candidate release. It is a documentation/tag release only. It changes no
model artifact, dataset, UI surface, frozen chain v5.1 artifact, or official
OSeCAN file. The WS4 work is a **candidate sidecar**: it is reproducible and
internally validated, but it is not an observed inventory, not a policy-grade
product, and not an official OSeCAN or CLEWS-C2070 release.

## 1. Architecture

The estate is built from measured 30-m SCANFI inventory and the committed
RSYC tile production library:

```text
SCANFI 30-m age + landcover + species crown closure
        |
        v
forest pixels (landcover 5/6/7, age > 0, no nodata)
        |
        v
WS4 fragments (one pixel at 30 m, or aggregated 990-m cells)
        |
        v
8-neighbour cliques with equal (age class, RSYC curve key) = blocks
        |
        v
WS4 tracks / strata / treatments / features / products / curves
        |
        v
ws4 validate -> ws4 simulate (CC and THIN state transitions)
        |
        v
119 Mm3 chain (frozen chain-v5.1 per-province coefficients)
        |
        v
origin-province OSeCAN handoff (read-only sidecar)
```

The committed datasets use the twelve canonical WS4 tables plus
`manifest.json`. Blocks are inventory cliques, not cutblocks or operable
units. No curve, block, tile, or schedule row is fabricated. Climate, natural
disturbance, harvest-induced age reset, operability, tenure, roads, mills,
haul, cost, and AAC are outside the model.

## 2. Model card

| Field | Value |
|---|---|
| Model | National forest-estate WS4 candidate |
| Release tag | `national-forest-estate-ws4-v1` |
| Release type | Documentation/tag only; no model/data/UI/official artifact change |
| Inventory | SCANFI v2 30 m age, landcover, and species crown closure |
| Curves | Committed RSYC tile production library |
| Fragment resolutions | 30 m (G2 pilot and tiled coverage) and 990 m (national estate) |
| Block rule | 8-neighbour connected component of equal `(age_class, curve_key)` |
| Treatments | Natural/managed CC; natural/thinned/managed THIN state transition |
| Core labels | `model_estimate`, `observed=false`, `policy_grade=false`, `national_claim=false` |
| Official status | Not official OSeCAN; `official_osecan_changed=false`; `chain_v51_unchanged=true` |
| National claim | None; no sustainable harvest, AAC, tenure, investment, or policy claim |

## 3. Evidence summary

### 3.1 30-m G2 pilot

`data/derived/ws4/scanfi-pilot-G2-BC-S32/` is the first real WS4 estate,
built from the highest-harvest BC 50-km supply node `G2|BC|S32`.

| Quantity | Value |
|---|---:|
| Fragments | 2,446,363 |
| Blocks | 932,041 |
| Forest area | 220,172.67 ha |
| Curves | 4 |
| Tracks | 12 (natural + thinned + managed) |
| Treatments | 24 (THIN + CC) |
| `ws4 validate` | pass, exit 0 |
| 50-year CC schedule | 89,895 blocks / 17,844.30 ha |
| 50-year CC harvest | 6,547,384.791 m3 (about 6,547,385 m3) |
| Treated / final managed area | 17,844.30 ha (about 17,844 ha) |

The pilot carries THIN and CC treatments. Natural, thinned, and managed tracks
share the same RSYC curve: THIN is a partial-cut state transition, not a
thinning-response model.

### 3.2 National 990-m model

`data/derived/ws4/scanfi-national-990m/` is the single-dataset national
estate. It keeps 30-m inventory reads and aggregates fragments to 990 m
(33 x 33 SCANFI pixels, 98.01 ha maximum).

| Quantity | Value |
|---|---:|
| Inventory pixels scanned | 21,246,666,606 |
| Included forest pixels | 4,569,451,448 |
| Fragments | 6,807,192 |
| Blocks | 1,984,017 |
| Forest area | 411,250,630.32 ha (411.25M ha) |
| Used RSYC curves | 718 of 770 resolved |
| Province-curve pairs | 933 |
| Tracks before THIN | 1,866 natural/managed |
| Tracks after THIN | 2,799 |
| `ws4 validate` | pass, exit 0 |
| 20-year demo schedule | 33,284 blocks / 7,514,902.98 ha |
| 20-year demo harvest | 968,900,774.756 m3 (968.9M m3) |
| Treated / final managed area | 7,514,902.98 ha (7.51M ha) |

The 20-year schedule is a **demonstration schedule, not a sustainable
harvest**. It selects 2.0% of eligible blocks at offset 0 and applies one CC
treatment. It is not an AAC, a harvest forecast, or a policy result.

### 3.3 Full national 30-m tiled coverage

`data/derived/ws4/tiled-30m/` builds one dataset per committed 50-km supply
node. The national manifest records `full_run.status=complete` and
`coverage_complete=true`.

| Quantity | Value |
|---|---:|
| Candidates | 3,562 |
| Built and validated datasets | 3,345 |
| Zero-harvest NU skips | 203 |
| No-forest sliver skips | 14 |
| Fragments | 4,560,930,641 |
| Blocks | 1,678,150,739 |
| Forest area | 410,483,757.69 ha |
| Local storage (dataset + schedule + manifest) | 58.548 GB |
| `ws4 validate` | 3,345 / 3,345 pass |

All 3,562 candidates are accounted for; the 217 skipped candidates are listed
with an explicit reason and have no fabricated dataset. This is full 30-m
geometric coverage of the built estate.

**Coverage caveat:** the committed 3,345 tile datasets were built **CC-baseline**
and predated the THIN builder change. THIN metadata has since been propagated
to all 3,345 tiles in place (12,892 thinned tracks; 3,345/3,345 `ws4 validate`
pass). Fragments, blocks, curves, groups, messages, natural/managed track ids,
and CC rows are unchanged, so the full-coverage CC results are unchanged. The
national 990-m and G2-pilot THIN evidence and the dedicated transition run
below remain the behavioural evidence; no adoption or promotion is claimed.

### 3.4 Tiled-aggregate 119 Mm3 chain

`data/derived/national/facilities/ws4_tiled_chain_119m/` scans all 3,345
built tiles, selects whole blocks against the 118.9773391 Mm3 provincial
control, and computes exact fragment-age volumes before applying the frozen
chain-v5.1 per-province coefficients.

| Quantity | Value |
|---|---:|
| Selected blocks | 427 |
| Tiles contributing | 126 |
| Selected area | 1,063,108.89 ha |
| Exact WS4 harvest | 118,988,870.593 m3 |
| Control target | 118,977,339.1 m3 |
| Overshoot | +11,531.493 m3 (+0.0097%) |
| Scale factor to control | 0.9999030876323556 |

Scaled national products/residue versus frozen v5.1:

| Metric | Unit | Scaled WS4 | Frozen v5.1 | Delta |
|---|---|---:|---:|---:|
| Solid product | m3 | 37,673,172.685 | 37,673,626.761 | -454.076 |
| Pulp product | odt | 12,066,424.418 | 12,066,311.270 | +113.148 |
| Process residue | PJ | 498.750367 | 498.750917 | -0.000550 |
| Harvest gross | PJ | 444.416608 | 444.410766 | +0.005842 |
| Harvest 60% | PJ | 266.649965 | 266.646460 | +0.003505 |
| Total gross | PJ | 943.166975 | 943.161683 | +0.005292 |

The `scaled_to_119m_control` basis is one national factor preserving tiled
provincial shares; it is not per-province target scaling. The schedule is a
whole-block arithmetic verification, **not a sustainable harvest**, AAC,
policy result, or investment case.

### 3.5 OSeCAN handoff

`data/derived/national/facilities/forestry_osecan_handoff_ws4/` and the nested
tiled handoff under `.../ws4_tiled_chain_119m/osecan/` use the explicit
**origin-province** basis. Handoff v10 process residue is facility-province
based, so zone rows are not compared directly; only national totals and tier
positions are compared.

| Check | Result |
|---|---|
| Origin-province basis | yes on every zone row |
| 119 Mm3 tier positions, `scaled_to_119m_control` | 7 / 7 match |
| 119 Mm3 tier positions, `ws4_achieved` | 7 / 7 match |
| Frozen v5.1 reference positions | 21 / 21 match |
| Official OSeCAN tree | 146 files, SHA-256 `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`, unchanged |

The handoff writes no candidate activity limit into OSeCAN. The 100 and 150
Mm3 positions are marked not available for the WS4 119 Mm3 chain rather than
fabricated.

## 4. THIN treatment evidence

The shared builder emits natural, thinned, and managed tracks per curve. THIN
is `begin=20,end=40,offset=-1,retain=0`; the negative offset is the WS4
partial-cut sentinel that keeps the fragment's current age. Thinned CC is
`begin=40,end=1000,new_track=managed,offset=0`; managed THIN and managed CC
remain available.

The national/G2 THIN transition run
`data/derived/ws4/thin-transition-national-990m/` used 40 blocks in 13
provinces:

| Quantity | Value |
|---|---:|
| THIN harvest at offset 0 | 588,608.208 m3 |
| THIN treated area | 6,273.54 ha |
| Age after THIN | retained (pre-treatment age) |
| CC harvest at offset 10 | 671,252.422 m3 |
| CC treated area moved to managed | 6,273.54 ha |
| Final managed value at offset 19 | 195,071.522 m3 |

The thinned feature value at offset 0 equals the pre-treatment curve volume,
which demonstrates age retention. No thinning, planting, silviculture,
fertilization, or genetic yield response is claimed: the tracks share one RSYC
curve.

The installed WS4 0.2.0 simulator resets age on every treatment offset. Age
retention therefore requires the external patch
`patches/ws4-negative-offset-retain.patch`, applied from the WS4 repository
root. Without that patch, THIN does not retain age and the transition evidence
is not reproducible.

## 5. Component links

| Component | Documentation | Artifact |
|---|---|---|
| 30-m G2 pilot | [`docs/ws4-national-estate-pilot.md`](ws4-national-estate-pilot.md) | `data/derived/ws4/scanfi-pilot-G2-BC-S32/` |
| National 990-m estate | [`docs/ws4-national-estate-990m.md`](ws4-national-estate-990m.md) | `data/derived/ws4/scanfi-national-990m/` |
| Tiled 30-m architecture | [`docs/ws4-tiled-30m-national.md`](ws4-tiled-30m-national.md) | `data/derived/ws4/tiled-30m/` |
| Staged tiled coverage | [`docs/ws4-tiled-30m-staged-national.md`](ws4-tiled-30m-staged-national.md) | `data/derived/ws4/tiled-30m/national_tiled_manifest.json` |
| National 990-m 119 Mm3 chain | [`docs/ws4-chain-119m.md`](ws4-chain-119m.md) | `data/derived/national/facilities/ws4_chain_119m/` |
| Tiled 119 Mm3 chain | [`docs/ws4-tiled-chain-119m.md`](ws4-tiled-chain-119m.md) | `data/derived/national/facilities/ws4_tiled_chain_119m/` |
| OSeCAN handoff | [`docs/ws4-osecan-handoff.md`](ws4-osecan-handoff.md) | `data/derived/national/facilities/forestry_osecan_handoff_ws4/` |
| THIN treatment | [`docs/ws4-thin-treatment.md`](ws4-thin-treatment.md) | `data/derived/ws4/thin-transition-national-990m/` |

## 6. Limitations

- **THIN metadata coverage.** The full 30-m tiled coverage carries THIN
  metadata after an in-place, metadata-only patch. Natural fragments, blocks,
  curves, groups, messages, track ids, and CC rows are unchanged; the
  full-coverage CC results were not rerun or replaced. THIN remains a state
  transition, not a thinning-response model.
- **State transition, not response.** THIN and planting record management
  state only. Natural, thinned, and managed tracks share one RSYC curve; no
  thinning, planting, silviculture, fertilization, or genetic yield response is
  modeled.
- **Group curves in the national 990-m estate.** The national basis uses RSYC
  tile coniferous/broadleaf group curves, with the tile generic curve as the
  broadleaf fallback. Species-specific curves are used only where the 30-m
  pilot resolves them.
- **No operability or economics.** No tenure, THLB, road, slope, riparian,
  cutblock, green-up, patch-size, harvest-flow, mill, haul, cost, or AAC
  constraint. Forest area is not THLB.
- **Demo schedules are not sustainable harvest.** The G2 50-year schedule and
  the national 990-m 20-year schedule are demonstration/verification runs.
  The tiled 119 Mm3 schedule is a whole-block arithmetic verification.
- **Static inventory.** Age and landcover are the SCANFI 2025 snapshot; no
  natural disturbance, harvest-induced age reset, climate change, or future
  landcover transition is modeled.
- **Sidecar/candidate, not official.** `model_estimate`, `observed=false`,
  `policy_grade=false`, `national_claim=false`. Not official OSeCAN, not a
  chain v5.1 replacement, and not a policy, investment, tenure, or national
  supply product.

## 7. Evidence pointers

| Milestone | Parent commit | Data commit |
|---|---|---|
| 30-m G2 pilot | `62d4f93` | `6f23f22` |
| National 990-m model | `cf03878` | `038400f` |
| Tiled 30-m coverage | `a4455b5` | `60d748e` |
| Tiled 119 Mm3 chain | `cd6156f` | `9f87baf` |
| THIN treatment | `a023eeb` | `f90620d9` |
| National 990-m 119 Mm3 chain | `ab288b1` | `867351e` |
| OSeCAN handoff | `45f1c57` | `4ce5cd8` |

The data commits are in the `UBC-FRESH/clews-c2070-fresh-data` repository.
The parent commits are in `UBC-FRESH/clews-c2070-fresh`.

## 8. Reproduce

The WS4 simulator is `.venv-ws4/bin/ws4` (installed WS4 0.2.0). Build or
validate a dataset with:

```bash
# 30-m G2 pilot
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset

.venv-ws4/bin/ws4 simulate \
  --dataset data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset \
  --schedule data/derived/ws4/scanfi-pilot-G2-BC-S32/schedules/pilot_cc.csv \
  --years 50 \
  --out data/derived/ws4/scanfi-pilot-G2-BC-S32/runs/pilot_cc_50y

# National 990-m estate
.venv-ws4/bin/ws4 validate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset

.venv-ws4/bin/ws4 simulate \
  --dataset data/derived/ws4/scanfi-national-990m/dataset \
  --schedule data/derived/ws4/scanfi-national-990m/schedules/national_cc_sample.csv \
  --years 20 \
  --out data/derived/ws4/scanfi-national-990m/runs/national_cc_20y
```

Rebuild the datasets and evidence:

```bash
.venv/bin/python -m workflows.ws4_instances.build_scanfi_estate_pilot --run-ws4
.venv/bin/python -m workflows.ws4_instances.build_scanfi_national_estate --run-ws4
.venv/bin/python -m workflows.ws4_instances.build_scanfi_tiled_30m \
  --all --include-zero-harvest --workers 32 --years 20 \
  --sample-fraction 0.1
.venv/bin/python -m workflows.ws4_instances.run_ws4_thin_transition \
  --max-blocks 40 --years 20
```

Rebuild the 119 Mm3 chains and OSeCAN handoffs:

```bash
.venv/bin/python -m workflows.national.facilities.ws4_chain_119m
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_ws4
.venv/bin/python -m workflows.national.facilities.ws4_tiled_chain_119m --workers 32
```

Run the focused tests:

```bash
.venv/bin/python -m pytest -q \
  tests/test_scanfi_estate_pilot.py \
  tests/test_scanfi_national_estate.py \
  tests/test_ws4_chain_119m.py \
  tests/test_ws4_osecan_handoff.py \
  tests/test_ws4_tiled_chain_119m.py
```

The tiled coverage build is a distributed-scale job (58.548 GB of datasets
and schedules at the committed stage profile); it is not a workstation smoke
test. The committed tiled coverage and 24-tile sample are CC-baseline and were
not rebuilt for THIN. See the component documents for exact per-stage commands
and fail-closed guards.

## 9. Release checklist

- [x] Model and chain evidence consolidated under one candidate release.
- [x] `model_estimate`, `observed=false`, `policy_grade=false`,
  `national_claim=false` stated on the release.
- [x] Official OSeCAN, frozen chain v5.1, UI, and model/data artifacts
  explicitly unchanged.
- [x] Full 30-m coverage and its CC-baseline/THIN caveat stated.
- [x] Demo schedules labelled not sustainable harvest.
- [x] WS4 0.2.0 negative-offset patch requirement recorded.
- [x] Data/parent commit pointers recorded.
- [ ] Official OSeCAN adoption — not requested and not claimed.
