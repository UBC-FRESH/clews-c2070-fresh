# Model card — CLEWS-C2070 FRESH forestry/biomass module (v1)

| Field | Value |
|---|---|
| Model | CLEWS-C2070 FRESH forestry and forest-biomass supply module |
| Release | v1 release/handoff package; package version `0.1.0a1` |
| Date | 2026-11-20 |
| Owner | UBC FRESH forestry/biomass module |
| Energy-side owner | Taco Niet and the energy/OSeCAN policy team |
| Status | Screening and interface test; not policy-grade |
| Canonical reference | 119 Mm³ fleet-corrected case: **1,445,655.44 M$**, **424.710127 Mt CO₂** cumulative 2021–2070, **0.0 Mt CO₂** in 2050 |
| Draft interface test | `policy_test_v1`: **1,458,305.16 M$**, **1,024.824 Mt CO₂** cumulative, **0.490203 Mt CO₂** in 2050; `status=draft`, `policy_grade=false` |
| Model source | Public `deltae/C2070` commit `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` plus committed UI patches |
| Data pattern | `data` DataLad submodule; `arbutus-s3` special remote; manifests and SHA-256 hashes |

## 1. Purpose

The module assembles a reproducible national forestry and forest-biomass supply
chain for the CLEWS-C2070 energy model. It covers the forest estate, harvest and
residue supply, provincial allocation, a BC/QC cost-revenue ledger, and a
forestry scenario explorer. Its interface to the energy model is the
`SUPBCW`/`SUPBCWB1`/`SUPBCWB2` supply rows and their provenance.

The model is intended for:

- provenance-tracked screening of forestry and biomass supply assumptions;
- testing whether the `SUPBCW` interface is readable, bounded, costed, and
  traceable inside OSeCAN;
- comparing scenario assumptions with explicit labels and caveats; and
- handing a reproducible evidence package to collaborators and reviewers.

It is **not** intended for policy advice, investment decisions, a legal
allowable annual cut (AAC) or timber harvesting landbase (THLB) determination, a
supply forecast, or an energy-policy recommendation. The `policy_test_v1` result
is an interface test, not energy-policy advice.

## 2. Ownership and interface boundary

| Party | Role | Boundary |
|---|---|---|
| FRESH forestry/biomass module (this repository) | RSYC-based supply evidence, harvest and allocation logic, bucking/residue chain, cost/supply curve, forestry UI | Owns everything upstream of the `SUPBCW`/`B1`/`B2` rows |
| Energy/OSeCAN policy team (PI: Taco Niet) | Energy-policy design, OSeCAN structure and constraints, capacity expansion, transmission/curtailment, carbon price, energy-model source signoff | Consumes the forestry interface; owns the energy model and policy interpretation |

OSeCAN is treated as a **black box**. The FRESH module does not edit OSeCAN
source, structure, policy constraints, or solver setup. It runs a disposable
copy of the public model with the committed interface patches and reads the
summary, dispatch, and manifest. The test asks whether the interface loads,
binds, costs, reconciles, and preserves provenance; it does not validate a
harvest level, residue potential, energy pathway, or policy.

The interface has two loading modes that must not be mixed:

- **National split mode:** `SUPBCW` (rest of Canada) with `SUPBCWB1` and
  `SUPBCWB2` (BC zones); the three caps sum to the national total every year.
- **BC comparison mode:** `SUPBCW` alone as a national rollup; it is not
  additive with `B1`/`B2` in this mode.

Activity limits are PJ/yr and costs are $/GJ. No energy-policy assumption and no
harvest recommendation crosses the interface.

## 3. Model components

| Component | Description | Reference facts |
|---|---|---|
| RSYC national backbone | National yield-curve backbone; tile-first control totals with ecodistrict composition; provincial libraries are calibration/validation only | 27,208 curves (13,646 AGB, 13,562 volume); 385 tiles; 12 ecozones, 36 ecoprovinces, 138 ecoregions, 672 ecodistricts; 35 species values; age-only predictor; batch is a pipeline test, not a national supply estimate |
| 2021 capacity fleet | CER EF2026 “Current Measures” 2021 fleet ingested into the disposable OSeCAN model | 70 non-zero `PWR*` rows totalling **145.02 GW** in 2021; `PWRBIO` 2.56888 GW; `PWRBSW`/`PWRBCW` zero; coal retired by 2030 |
| Operable proxy | National merchantable/operable screening landbase from SCANFI v2 and the managed-landbase proxy | **144.69 Mha** (87.0% of the 166.28 Mha managed comparison proxy); sensitivity 383.38 Mha low / 267.94 Mha high; **not a legal THLB**; no slope, road-access, age, or economic constraint |
| Provincial allocation | Screening LP allocating 100/119/150 Mm³ across 12 reporting jurisdictions; NU held at zero | 100 and 119 Mm³ feasible at 1.0 and 1.2 m³/ha/yr; **150 Mm³ infeasible at both screens**: 16.296 Mm³ provincial deficit at 1.0, 4.492 Mm³ at 1.2; not a legal AAC |
| Residue factors | Allocation-weighted provincial residue factors and harvest sensitivity | Gross 0.09881 / 0.09918 / 0.10018 ODT/m³ log; base recoverable 0.05929 / 0.05951 / 0.06011 (100/119/150); base supply 109.68 / 130.98 / 166.80 PJ/yr |
| BC/QC cost-revenue ledger | Two non-additive perspectives: Crown revenue and forest-sector net value | **2,888 rows**: BC 200 observed, QC 2,688 model input; 100 Crown / 2,788 forest-sector; 100 paired mirrors; 0 observed delivered-log revenue; 2,075 model-input delivered-log rows (251 quality-class + 1,824 DBH curves); **net value not estimated** |
| Forestry UI | Static Forest Ledger plus optional FastAPI live runner | **53 scenarios / 18 families / 1 Pareto-eligible**; create/queue/SSE, cancel/retry, session hydration, Explore/Pareto/Compare, map drill-down/year playback, 13-zone table/heatmap, role badges, public/shared-analyst deployment units, per-instance `run_instance.sh`/systemd template isolation |
| `policy_test_v1` | Draft Stage-1 constraint family: national peak-proxy reserve margin, reviewed capacity credits, build rates, explicit caps | `status=draft`, `policy_grade=false`; separate from `screening_v0`; interface test only |

### WS4 national forest-estate candidate release

The WS4 national forest-estate model is a separate candidate sidecar
([`docs/national-forest-estate-ws4-v1.md`](national-forest-estate-ws4-v1.md),
annotated tag `national-forest-estate-ws4-v1`). It builds WS4 datasets from
SCANFI 30-m inventory and RSYC curves at 30-m (G2 pilot and tiled coverage) and
990-m (national estate) fragment resolutions, then applies the frozen chain
v5.1 per-province coefficients to a 119 Mm³ schedule and emits an
origin-province OSeCAN handoff. It is `model_estimate`, `observed=false`,
`policy_grade=false`, `national_claim=false`; it does not change chain v5.1,
the UI, or official OSeCAN. Its earlier demo schedules are not sustainable
harvest.

The full-forest sustainable 100/119/150 Mm³/yr cases and the share-based
`screen_core30` operable rerun are consolidated in
[`docs/national-sustainable-supply-ws4-v1.md`](national-sustainable-supply-ws4-v1.md)
(annotated tag `national-sustainable-supply-ws4-v1`). The full-forest cases
are period-sustained with zero period shortfalls but are not policy supply.
The operable cases are **not** sustained under the frozen provincial shares:
period shortfalls are 160.4216 / 623.2648 / 1,774.8825 Mm³ and the
schedule-only maximum nominal levels are 68.4570 / 67.8543 / 61.4868 Mm³/yr.
The operable screen is an area-share proxy with no legal THLB, slope DEM, or
spatial road overlay; PE and NU have no tenure coverage.

The transformed-curve successor release is
[`docs/national-transformed-supply-ws4-v1.md`](national-transformed-supply-ws4-v1.md)
(annotated tag `national-transformed-supply-ws4-v1`). It applies the national
RSYC transform v1 library (3,948 curves, 34 donor classes, V100 preserved) to
the national 990 m WS4 dataset and reruns the sustainable 100/119/150 Mm³/yr
cases and the `screen_core30` operable screen. The full-forest cases are
sustained with zero period shortfalls (annual means 101.1221 / 120.0561 /
151.6402 Mm³/yr); the operable cases are not sustained. The release is
`model_estimate`, `observed=false`, `policy_grade=false`,
`national_claim=false`; it does not move frozen chain v5.1 and does not change
official OSeCAN. The curves repair shape with documented BC natural and QC
managed donors; they are not a calibrated yield model and the schedules are
not policy supply.

## 4. Reference results

| Case | Objective (M$) | Cumulative 2021–2070 (Mt CO₂) | 2050 (Mt CO₂) | Labels | Use |
|---|---:|---:|---:|---|---|
| Canonical fleet-corrected 119 Mm³ | 1,445,655.44 | 424.710127 | 0.0 | `screening_only=true`, `policy_grade=false`, `canonical=true`, `fleet_corrected=true` | Canonical screening reference |
| `policy_test_v1` sourced draft | 1,458,305.16 | 1,024.824 | 0.490203 | `status=draft`, `policy_grade=false` | Interface test only; not policy |

The two rows use different constraint families and configurations. Their
difference is **not** a policy effect and must not be reported as one. The draft
row does not replace the canonical row.

## 5. Validation and evidence

| Check | Recorded result |
|---|---|
| UI tests | **113 passed** |
| Ledger/LP tests | **42 passed** |
| Real 119 Mm³ API run | Completed through the live FastAPI runner in **3 m 42 s**; output endpoint returned HTTP 200 |
| Chromium QA | Static and live passes recorded with 0 page/console errors in the recorded runs; screenshots and machine-readable results under `ui/qa/` |
| Scenario bundle | 53 scenarios, 18 families, 1 Pareto-eligible; boot guards pass |
| v1.2 deploy isolation | Two local instances on temporary runtime dirs/ports: distinct `0700` runtimes, analyst `allow_runs=true`, public `read_only=true`, public POST 403, no solver |
| Spatial guard | 13 zone rows plus a separate `CAN` row; 11 explicit C2070 zone gaps; 0 invented C2070 polygons |
| Repository hygiene | Recorded QA/handoff checks report no unexpected `D` status entries |

The 3 m 42 s live API run used the `screening_119m` example before the fleet
correction. It is runner acceptance evidence, not the canonical reference. The
canonical reference is the committed `fleet_capacity_119m` artifact.

## 6. Limitations and non-use

- **No observed delivered-log revenue or full production costs.** The BC/QC
  ledger records 0 observed delivered-log revenue and 0 observed QC harvest
  volume; every province-year keeps `net_value_status=not_estimated`. Do not
  report net value, return on investment, or a benefit-cost result.
- **No legal THLB.** The 144.69 Mha operable proxy is a screening landbase. It is
  not a legal timber harvesting landbase and carries no slope, road-access, age,
  or economic constraint. The 150 Mm³ case is conditional at the AAC/NFD gate and
  infeasible under the 1.0 and 1.2 m³/ha/yr intensity screens.
- **No C2070 zone polygons.** The map has 13 province polygons, 36 ecoprovinces,
  and 385 RSYC tiles. `spatial/zone_nodes.json` has 13 C2070 zone nodes, 2
  authoritative B1/B2 point anchors, and 11 explicit gaps. The 13-zone table is a
  reporting fallback, not a boundary. Do not draw, interpolate, or centroid a
  C2070 zone without an authoritative boundary and a documented crosswalk.
- **The UI is provisional, not C2070 official.** Keep that label on the explorer
  and its exports; cite the source artifacts and scenario flags.
- **`policy_test_v1` is draft.** It has `status=draft` and `policy_grade=false`.
  Its peak factor, several capacity credits, and provincial standards remain
  draft or reviewed; transmission, curtailment/timeslices, and unit commitment
  remain structural gaps. It must not be presented as energy-policy advice.
- **The UI is single-tenant within one runtime.** There is no in-app login, user
  table, session cookie, token validation, or owner field on scenarios/jobs.
  Cloudflare Access or the configured edge policy is the identity boundary at
  deployment; the role flag selects actions but does not authenticate a caller.
  The v1.2 deploy assets (`run_instance.sh`, `clews-ui-analyst@.service`) provide
  one process, port, `0700` runtime, and per-instance service account per
  analyst/team. That is deployment-level isolation, not in-app tenancy: one
  instance must not be shared across mutually untrusted callers.
- **No restart resume.** A server restart marks queued/running jobs
  `interrupted`; retry reuses the immutable scenario snapshot.
- **Model rebuild is patch-dependent.** The disposable model is the public
  `deltae/C2070` commit plus local patches. The source tree reports dirty by
  design; the setup manifest records the source commit, patch hashes, capacity
  provenance, and copied-tree hash. Do not claim a clean public rebuild without
  reproducing that manifest.
- **Pre-fleet scenarios are historical audit.** Legacy scenarios carry
  `superseded_for_fleet=true`; they remain visible but are not comparable with
  the canonical fleet-corrected result. The former 26-scenario ledger has not
  been rerun under the fleet model.
- **No national supply forecast.** The RSYC national batch is a pipeline test,
  not a national supply estimate. The 119 Mm³ case is a 2024 observed hold, not a
  forecast.

## 7. Provenance

- Parent repository: `clews-c2070-fresh`; data payloads live in the `data`
  submodule (`UBC-FRESH/clews-c2070-fresh-data`).
- Heavy data: DataLad/git-annex with the `arbutus-s3` special remote on DRAC
  Arbutus S3.
- Canonical artifact:
  `data/derived/national/clews/osecan/fleet_capacity_119m/` (summary, dispatch,
  manifest only; raw model trees and solver directories are not committed).
- Run manifest records the constraint set, scenario, model source commit and
  dirty flag, artifact SHA-256 hashes, solver status, and supply reconciliation.
  The model setup manifest records the patch hashes and copied-tree hash.
- Recorded source baseline for this package: parent `a9edcd2`; data submodule
  `00cba3c` (branch `feature/osecan-zonal-bc-supply`). Record the final release
  tag/commit in the handoff ledger.
- No public OSeCAN repository change is part of this release.

## 8. References

- `docs/release-v1.md` — release checklist, runbook, acceptance record, restore
  drill, publication ledger, and caveats.
- `planning/module-boundaries.md` — ownership and interface rules.
- `planning/policy-test-v1.md` — draft constraint-set design and non-claims.
- `planning/fleet-correction-canonicalization.md` — canonical 119 Mm³ result and
  supersession.
- `planning/national-rsyc-backbone.md` — RSYC backbone decisions and limits.
- `planning/harvest-aac-feasibility.md` — harvest/AAC screen and allocation
  caveats.
- `planning/forest-cost-revenue-ledger.md` — ledger perspectives and
  `not_estimated` rule.
- `ui/README.md` and `workflows/ui/README.md` — UI views, data contract, runner,
  roles, and model setup.
- `deploy/README.md` — systemd, Cloudflare Tunnel/Access, publication, backup,
  and retention.
- `docs/data-repository-runbook.md` — DataLad and `arbutus-s3` operations.
