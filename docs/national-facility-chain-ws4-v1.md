# National facility chain WS4 v1 — in-repo release

**Release tag:** `national-facility-chain-ws4-v1` (annotated)
**Estate tag:** `national-forest-estate-ws4-v1.1` (annotated; full THIN
coverage and adopted-candidate marker)
**Status:** new in-repo WS4-derived national chain release; candidate for
adoption
**adopted_candidate:** `true`
**Evidence labels:** `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`
**Boundary:** not official OSeCAN; not a frozen chain v5.1 replacement; no
model, data, or UI change

This release promotes the WS4-driven national chain and origin-province OSeCAN
handoff from sidecar evidence to a named in-repo release. The national
forest-estate model is marked `adopted_candidate=true`: it is the adopted
in-repo candidate for the national chain/OSeCAN lane, not an official OSeCAN
adoption.

The release does **not** overwrite or move frozen
`national-facility-chain-v5.1`; that tag and its artifacts remain valid. The
official OSeCAN tree is unchanged. Adoption by the Taco/energy team is still
required before any official OSeCAN integration. This document is a
documentation/tag release only: no model artifact, dataset, UI surface, frozen
chain v5.1 artifact, or official OSeCAN file is changed.

**Sustainable/operable successor evidence:** the full-forest sustainable
100/119/150 Mm³/yr cases and the `screen_core30` share-based operable rerun are
consolidated in
[`docs/national-sustainable-supply-ws4-v1.md`](national-sustainable-supply-ws4-v1.md)
(annotated tag `national-sustainable-supply-ws4-v1`). That release does not
move this tag, frozen chain v5.1, or official OSeCAN.

## 1. Status and adoption boundary

| Field | Value |
|---|---|
| In-repo release | `national-facility-chain-ws4-v1` |
| Estate release | `national-forest-estate-ws4-v1.1` |
| Estate candidate tag | `national-forest-estate-ws4-v1` stays at `fae49ab` |
| Adoption status | `adopted_candidate=true`; candidate for adoption |
| Frozen chain v5.1 | Tag and artifacts remain valid; not overwritten or moved |
| Official OSeCAN | Unchanged; no official integration |
| Adoption authority | Taco/energy team; still required before official OSeCAN integration |
| Change class | Documentation/tag only |

The earlier `national-forest-estate-ws4-v1` tag remains the candidate-release
marker at `fae49ab`. The new `national-forest-estate-ws4-v1.1` tag marks the
same documentation commit as this chain release and records the completed
full-coverage THIN propagation plus the adopted-candidate status.

## 2. Input

| Input | State used by this release |
|---|---|
| Full 30-m tiled coverage | THIN-patched, 3,345 built datasets; 3,562 candidates accounted for (203 zero-harvest NU skips, 14 no-forest skips) |
| National 990-m estate | National estate with the THIN state transition |
| G2 30-m pilot | 30-m pilot with THIN and CC evidence |

The tiled input is the complete built coverage, not a sample. All 3,345
datasets carry the THIN metadata patch and pass `ws4 validate`; the 217
skipped candidates are explicit zero-harvest or no-forest cases with no
fabricated dataset.

## 3. Tiled 119 Mm³ chain

The chain scans the THIN-patched tiled coverage, selects whole blocks against
the 118.9773391 Mm³ provincial control, computes exact fragment-age volumes,
and applies the frozen chain-v5.1 per-province coefficients.

| Quantity | Value |
|---|---:|
| Selected blocks | 427 |
| Contributing tiles | 126 |
| Selected area | 1,063,108.89 ha |
| Exact WS4 harvest | 118,988,870.593 m³ |
| Control target | 118,977,339.1 m³ |
| Overshoot | +11,531.493 m³ (+0.0097%) |
| Scaled harvest to control | 118,977,339.1 m³ (exact) |

Scaled products and residue versus frozen v5.1:

| Metric | Unit | WS4 scaled | Frozen v5.1 | Delta |
|---|---|---:|---:|---:|
| Solid product | m³ | 37,673,172.685 | 37,673,626.761 | -454.076 |
| Pulp product | odt | 12,066,424.418 | 12,066,311.270 | +113.148 |
| Process residue | PJ | 498.750367 | 498.750917 | -0.000550 |
| Harvest gross | PJ | 444.416608 | 444.410766 | +0.005842 |
| Harvest 60% | PJ | 266.649965 | 266.646460 | +0.003505 |
| Total gross | PJ | 943.166975 | 943.161683 | +0.005292 |

The scaled basis applies one national factor to preserve the tiled provincial
shares while matching the control total exactly. The schedule is a whole-block
arithmetic verification, not a sustainable harvest, AAC, policy result, or
investment case.

## 4. OSeCAN handoff

The handoff uses the explicit origin-province basis and frozen handoff-v10
zone weights. It writes no candidate activity limit into OSeCAN.

| Check | Result |
|---|---|
| 119 Mm³ tier positions, `scaled_to_119m_control` | 7 / 7 match |
| 119 Mm³ tier positions, `ws4_achieved` | 7 / 7 match |
| Frozen v5.1 reference positions | 21 / 21 match |
| Official OSeCAN tree | 146 files, SHA-256 `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a` |
| Official OSeCAN changed files | 0 |

The 100 and 150 Mm³ positions are marked not available for this 119 Mm³
release rather than fabricated. Official OSeCAN remains unchanged and outside
this release until Taco/energy adoption and integration.

## 5. Full-coverage THIN

`workflows/ws4_instances/propagate_thin_tiled_30m.py` patched all built tile
datasets in place. It changed no fragment, block, curve, group, or message
table.

| Check | Result |
|---|---:|
| Tiles patched and validated | 3,345 / 3,345 |
| Thinned tracks added | 12,892 |
| Treatment rows added | 51,568 |
| CC contract hashes changed | 0 |
| Pre/post chain rescan mismatches | 0 |

Natural/managed track ids, existing CC treatments, features, products, and CC
rows are unchanged. The full-coverage CC results are therefore unchanged and
were not replaced. THIN remains a state transition, not a thinning-response
model.

## 6. THIN evidence

The national/G2 THIN transition evidence used 40 blocks in 13 provinces:

| Quantity | Value |
|---|---:|
| THIN harvest at offset 0 | 588,608 m³ |
| THIN treated area | 6,273.54 ha |
| Age after THIN | retained (pre-treatment age) |
| CC harvest at offset 10 | 671,252 m³ |
| Final managed value at offset 19 | 195,071 m³ |

Age retention is the WS4 negative-offset partial-cut sentinel. The installed
WS4 0.2.0 simulator requires the external
`patches/ws4-negative-offset-retain.patch`; without that patch the THIN
transition evidence is not reproducible.

## 7. Limitations and non-claims

- **Labels.** Every result is `model_estimate=true`, `observed=false`,
  `policy_grade=false`, and `national_claim=false`.
- **Not a sustainable harvest or AAC.** The tiled 119 Mm³ schedule and the G2
  and national demonstration schedules are arithmetic or demonstration runs,
  not sustainable harvest levels, AACs, or policy results.
- **No operability, tenure, or cost.** No THLB, tenure, road, slope, riparian,
  cutblock, green-up, harvest-flow, mill, haul, cost, or investment constraint
  is modelled. Forest area is not THLB.
- **990-m group curves.** The national estate uses RSYC tile
  coniferous/broadleaf group curves with a generic fallback; it does not use
  species-specific curves nationally.
- **Mixed curve resolution in the full 30-m coverage.** The full 30-m coverage
  uses exact, group, and generic curves where that is how the tile library
  resolves the stand; it is not uniformly species-specific.
- **One CC/THIN treatment set.** THIN and CC are one state-transition set.
  Natural, thinned, and managed tracks share one RSYC curve; no thinning,
  planting, silviculture, fertilization, or genetic yield response is claimed.
- **Not official OSeCAN.** This is an in-repo candidate release, not official
  OSeCAN, not an official energy-model scenario, and not a chain v5.1
  replacement. Official OSeCAN adoption remains with the Taco/energy team.
- **No UI or model/data change.** This release adds documentation and tags
  only.

## 8. Commits and tags

| Milestone | Parent/commit | Data commit |
|---|---|---|
| Estate v1 candidate release (`national-forest-estate-ws4-v1` tag) | `fae49ab` | — |
| THIN propagation to the full tiled coverage | `0acc076` | `ee12167f` |
| Full 30-m tiled coverage completion | `a4455b5` | `60d748e` |
| Tiled 119 Mm³ chain refresh on THIN-patched coverage | `51d6979` | `c4e0a78ad` |
| Prior tiled 119 Mm³ chain | `cd6156f` | `9f87baf` |
| National 990-m estate | `cf03878` | `038400f` |
| G2 30-m pilot | `62d4f93` | `6f23f22` |
| WS4 chain and OSeCAN handoff | `45f1c57` | `4ce5cd8` |

The data commits are in `UBC-FRESH/clews-c2070-fresh-data`; the parent commits
are in `UBC-FRESH/clews-c2070-fresh`.

This documentation commit is tagged `national-facility-chain-ws4-v1` and
`national-forest-estate-ws4-v1.1`. The existing
`national-forest-estate-ws4-v1` tag remains at `fae49ab`; frozen
`national-facility-chain-v5.1` is not moved.

## 9. Release verification

- [x] Documentation/tag only; only docs, ROADMAP, and CHANGE_LOG changed.
- [x] Frozen chain v5.1 tag and artifacts remain valid and unmodified.
- [x] Official OSeCAN 146-file tree hash unchanged.
- [x] No model, data, or UI mutation.
- [x] No unexpected `D` in the parent or data repository.
- [x] Estate model marked `adopted_candidate=true`.
- [ ] Official OSeCAN adoption and integration — remains with the Taco/energy
  team.
