# National sustainable/operable supply WS4 v1 — candidate release

**Release tag:** `national-sustainable-supply-ws4-v1` (annotated)
**Status:** candidate sustainable/operable supply release
**Evidence labels:** `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`
**Boundary:** not official OSeCAN; not a chain v5.1 replacement; frozen chain
v5.1 unchanged; no model, data, or UI change at the documentation/tag commit

This release consolidates the full-forest sustainable 100/119/150 Mm³/yr WS4
cases and the share-based operable landbase screen into one tagged evidence
pointer. It is a documentation/tag release only. It changes no model artifact,
dataset, UI surface, frozen chain v5.1 artifact, or official OSeCAN file.

The full-forest cases are sustained at the 10-year period level on the
national 990 m WS4 estate. The operable screen says the same frozen provincial
shares are **not** sustained once the landbase is restricted to
`screen_core30`. That contrast is the release finding: the full-forest cases
are a volume-arithmetic sustainability diagnostic, and the operable cases are a
share-based screening diagnostic, not policy supply.

## 1. Status and adoption boundary

| Field | Value |
|---|---|
| Release | `national-sustainable-supply-ws4-v1` |
| Release class | Documentation/tag only |
| Supply status | Candidate; full-forest sustained, operable not sustained |
| Core labels | `model_estimate=true`, `observed=false`, `policy_grade=false`, `national_claim=false` |
| WS4 labels | `ws4_derived=true`, `sustainable_schedule=true` |
| Operable labels | `share_based_operable_proxy=true`, `legal_thlb=false`, `slope_screen=false` |
| Official OSeCAN | Unchanged; `official_osecan_changed=false` |
| Frozen chain v5.1 | Unchanged; `chain_v51_unchanged=true`; tag `national-facility-chain-v5.1` not moved |
| Adoption authority | Not official OSeCAN; adoption remains with the Taco/energy team |
| National claim | None; no AAC, policy supply, legal THLB, or investment claim |

## 2. Full-forest sustainable cases

Inputs are the national 990 m WS4 dataset
(`data/derived/ws4/scanfi-national-990m/dataset`): 1,984,017 blocks /
411,250,630.32 ha, 6,807,192 fragments, 2,799 tracks. The horizon is 100 years
in ten 10-year periods. Rotation is the integer MAI peak capped to the 40–200
year band and flagged when the raw peak is outside it; it is never relaxed.
Whole blocks are selected oldest-first and volume-aware against the frozen
`allocated_share` provincial controls. Selected blocks are spread over the ten
annual offsets of each period by longest-processing-time (LPT) balancing.
Annual volumes below are Mm³/yr.

| Case | Annual min | Annual max | Annual mean | Period shortfalls | Result |
|---|---:|---:|---:|---:|---|
| 100 Mm³ | 100.0968 | 102.9553 | 101.1050 | 0 | sustained 100 years |
| 119 Mm³ | 119.0433 | 122.1610 | 119.8839 | 0 | sustained 100 years |
| 150 Mm³ | 149.9970 | 153.4957 | 151.2477 | 0 | sustained at period level |

All three cases meet every 10-year period target with zero period shortfall.
The 150 Mm³ case has one annual offset 2,972.8 m³ (0.0020%) below the nominal
annual rate while its period is above target; that LPT spread residual is
reported, not forced. The 119 Mm³ case is control-exact (118,977,339.1 m³/yr)
on its `sustainable_119m` basis.

### 2.1 Rotation

Every selected block has a raw RSYC MAI peak below 40 years (peaks at 1/2/13/24
years). The 40-year floor is therefore used and flagged for 100% of selected
blocks. The cap is an operability floor, not a yield claim; the raw peak is
retained in `rotation_distribution.csv`.

### 2.2 Chain and OSeCAN

Frozen chain v5.1 per-province coefficients are applied to the full-forest
harvests. Deltas below are on the primary basis (first-period annual average
for 100/150; control-exact 100-year average for 119).

| Case | Solid | Pulp | Process residue |
|---|---:|---:|---:|
| 100 Mm³ | +0.13% | +0.21% | +0.17% |
| 119 Mm³ | −0.36% | +0.27% | −0.03% |
| 150 Mm³ | +1.31% | +3.19% | +2.24% |

| Case | Primary tier matches | Achieved tier matches | Frozen v5.1 reference |
|---|---:|---:|---:|
| 100 Mm³ | 7/7 | 7/7 | 21/21 |
| 119 Mm³ | 7/7 | 7/7 | 21/21 |
| 150 Mm³ | 7/7 | 7/7 | 21/21 |

The 7/7 checks are the 119-tier positions evaluated with the frozen handoff-v10
zone weights, v9 process/harvest definitions, and official tier thresholds. The
21/21 frozen reference matches handoff v10. The official OSeCAN tree is
**146 files**, SHA-256
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
unchanged. The differences are frozen coefficients applied to a different
provincial harvest mix, not a coefficient or threshold change.

## 3. Operable landbase screen

`screen_core30` is the national operable proxy v2 case with SCANFI median age
>= 30 years, NBAC 2020 burned pixels excluded, and block centre within 10 km of
a public NRN road. It is a **share-based proxy**, not a legal THLB: no national
slope DEM and no spatial road/tenure overlay are applied.

| Quantity | Value |
|---|---:|
| Mask blocks | 370,752 |
| Mask area | 75,374,046 ha |
| Share of WS4 estate | 18.328% of 411,250,630.32 ha |
| Published `screen_core30` area | 77,050,071.64 ha |
| Denominator difference | −2.175% |
| PE / NU operable coverage | none (no 2016 tenure coverage) |

The mask is built per province by ranking whole blocks on a stable
`blake2b(province:block_id)` key and taking the prefix that best matches the
published `operable_share_of_treed` applied to the WS4 block-forest area. The
published absolute area is a cross-check, not a forced denominator. The
−2.175% difference is the gap between the WS4 block-forest denominator and the
SCANFI treed denominator, not a loss of mask coverage.

The 100/119/150 cases keep their frozen provincial shares, restrict
eligibility to mask blocks at or above the 40–200 year MAI rotation, disable
the cross-province fallback, and run WS4 simulate for 100 years. None is
sustained:

| Case | Annual min Mm³/yr | Period shortfall Mm³ | Max nominal Mm³/yr | Max operable Mm³/yr | Result |
|---|---:|---:|---:|---:|---|
| 100 Mm³ | 94.4141 | 160.4216 | 68.4570 | 69.3438 | not sustained |
| 119 Mm³ | 97.9475 | 623.2648 | 67.8543 | 69.2302 | not sustained |
| 150 Mm³ | 111.6734 | 1,774.8825 | 61.4868 | 62.7054 | not sustained |

`Max nominal` is the schedule-only maximum nominal control total that keeps
every jurisdiction with operable coverage at its period target; `max operable`
is the corresponding actual operable harvest. Both exclude PE and NU, which
have no operable blocks. They are schedule-only diagnostics, not
WS4-simulated policy levels.

### 3.1 Provincial shortfalls

| Case | Shortfall provinces |
|---|---|
| 100 Mm³ | QC, NB, NL, PE, NS |
| 119 Mm³ | QC, NB, NL, SK, PE, NS |
| 150 Mm³ | QC, SK, NS, NL, NB, PE |

The union is QC, SK, NB, NL, PE, and NS. QC is the binding province in every
case. Every 10-year period (0–9) contains at least one provincial shortfall in
all three cases. No rotation is relaxed and no block is harvested outside the
mask; PE/NU targets are recorded as shortfalls rather than substituted with
harvest.

### 3.2 Operable OSeCAN

| Case | Primary tier matches | Achieved tier matches | Frozen v5.1 reference |
|---|---:|---:|---:|
| 100 Mm³ | 6/7 | 7/7 | 21/21 |
| 119 Mm³ | 7/7 | 6/7 | 21/21 |
| 150 Mm³ | 7/7 | 6/7 | 21/21 |

The changed positions are findings from the operable residue totals, not
official OSeCAN changes. The official 146-file tree and its
`80caa91c...` SHA-256 are unchanged.

## 4. Limits and non-claims

- **Share-based proxy, not a legal THLB.** No legal tenure footprint, no
  national slope DEM, and no spatial road overlay. Selected blocks are not
  intersected with tenure, burn, road, or slope rasters.
- **PE and NU have no tenure coverage.** Their operable area is missing, not
  zero; their targets are recorded as shortfalls, and they are excluded from
  the schedule-only maximum test.
- **Maximum sustainable levels are schedule-only diagnostics.** They are not
  WS4-simulated policy levels, not AACs, and not investment cases.
- **Rotation cap 40 years.** The raw RSYC MAI peaks at 1/2/13/24 years are
  implausible rotations; the 40-year floor is applied and flagged, never
  relaxed to meet a target.
- **Full-forest cases are not policy supply.** They use the whole 411.25 Mha
  estate with no operability, tenure, road, slope, or mill-capacity screen.
  They are volume-arithmetic sustainability tests.
- **The 150 Mm³ case is period-sustained only.** One annual offset is 0.0020%
  below the nominal annual rate; the 10-year period is the control unit.
- **Not official OSeCAN and not policy grade.** `model_estimate=true`,
  `observed=false`, `policy_grade=false`, `national_claim=false`. No national
  supply claim, AAC, or policy recommendation is made.

## 5. Evidence, commits, and tags

Parent commits are in this repository; data commits are in the data submodule
(`UBC-FRESH/clews-c2070-fresh-data`).

| Evidence | Parent commit | Data commit | Documentation |
|---|---|---|---|
| Sustainable 119 Mm³ schedule and chain | `ffea298` | `ce1670c62` | `docs/ws4-sustainable-119m.md` |
| Sustainable 100/150 Mm³ build | `21bf4bf` | `64edb5f38` | `docs/ws4-sustainable-cases.md` |
| Sustainable 100/150 manifest ordering | `c8af906` | `f0e298097` | `docs/ws4-sustainable-cases.md` |
| Operable 100/119/150 Mm³ cases | `b9e86a0` | `ba4a87aec` | `docs/ws4-operable-cases.md` |
| Chain/OSeCAN release tag | `c363468` | — | `docs/national-facility-chain-ws4-v1.md` |
| Estate release tag | `c363468` | — | `docs/national-forest-estate-ws4-v1.md` |

Tags:

- `national-sustainable-supply-ws4-v1` — annotated tag at this documentation
  commit.
- `national-facility-chain-ws4-v1` and `national-forest-estate-ws4-v1.1` —
  annotated tags at `c363468` for the WS4-derived chain/OSeCAN and full-coverage
  estate release.
- `national-forest-estate-ws4-v1` — earlier candidate tag at `fae49ab`; not
  moved.
- `national-facility-chain-v5.1` — frozen chain v5.1 at `546a3d5`; not moved.
  Frozen chain v5.1 artifacts remain valid.

Artifacts:

- Full-forest schedules: `data/derived/ws4/sustainable-{100m,119m,150m}/`.
- Full-forest chains/OSeCAN:
  `data/derived/national/facilities/ws4_sustainable_119m/` and
  `data/derived/national/facilities/ws4_sustainable_cases/`.
- Operable mask: `data/derived/ws4/operable-mask/`.
- Operable schedules/runs: `data/derived/ws4/operable-{100m,119m,150m}/`.
- Operable chains/OSeCAN:
  `data/derived/national/facilities/ws4_operable_cases/`.
- Official OSeCAN tree: `data/derived/national/clews/osecan/` (146 files;
  `80caa91c...`; read-only).

## 6. Reproduce

Full-forest sustainable cases:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_sustainable_119m
.venv-ws4/bin/python -m workflows.national.facilities.ws4_sustainable_osecan_handoff
.venv-ws4/bin/python -m workflows.national.facilities.ws4_sustainable_cases
.venv/bin/python -m pytest tests/test_ws4_sustainable_119m.py \
  tests/test_ws4_sustainable_cases.py -q
```

Operable cases:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_operable_cases
.venv/bin/python -m pytest tests/test_ws4_operable_cases.py -q
```

The builds validate the WS4 dataset, hash the official OSeCAN tree and frozen
chain v5.1 before and after, and refuse to overwrite a run whose schedule
differs. Any official OSeCAN or chain v5.1 change aborts the build.

## 7. Related documentation

- `docs/national-facility-chain-ws4-v1.md` — WS4-derived chain/OSeCAN release.
- `docs/national-forest-estate-ws4-v1.md` — national forest-estate candidate.
- `docs/ws4-sustainable-119m.md` — sustainable 119 Mm³ case.
- `docs/ws4-sustainable-cases.md` — sustainable 100/150 Mm³ cases.
- `docs/ws4-operable-cases.md` — operable shortfall result.
- `docs/operable-proxy-report.md` — national operable proxy context.
