# Module boundaries — FRESH forestry biomass and OSeCAN

| Field | Value |
|---|---|
| Document | `planning/module-boundaries.md` |
| Date | 2026-11-20 |
| Status | Operating boundary note. No model, data, or policy change. |
| Scope | FRESH/CLEWS forestry-biomass module and its interface to OSeCAN |
| Audience | FRESH team; Taco Niet and the energy/OSeCAN policy team; third collaborator/team |

**Boundary.** This repository owns the forest supply evidence, the residue
cost/supply curve, and the forestry UI. Taco Niet's team owns the energy-policy
model and its source signoff. The handoff is the
`SUPBCW`/`SUPBCWB1`/`SUPBCWB2` rows and their provenance.

## 1. Purpose

This note records ownership at the forestry-biomass/energy-model boundary. It is
an operating note, not a data-sharing agreement, contract, or policy statement;
a signed agreement or active issue governs if they conflict. Update it when the
interface changes, not when a result changes.

## 2. Three-way context

| Party | Role | Boundary |
|---|---|---|
| FRESH/CLEWS forestry-biomass module (this repository) | RSYC-based supply evidence, harvest and allocation logic, bucking/residue chain, cost/supply curve, forestry UI | Owns everything upstream of the `SUPBCW`/`B1`/`B2` rows |
| Energy/OSeCAN policy team (PI: Taco Niet) | Energy-policy design, OSeCAN structure and constraints, capacity expansion, transmission/curtailment, carbon price, energy-model source signoff | Consumes the forestry interface; owns the energy model and policy interpretation |
| Third collaborator/team | Separate workstream | Internal scope is outside this note; coordinate at the same interface and follow §7 |

## 3. Responsibility split

### 3.1 Our responsibility

| Area | What we own | Caveat |
|---|---|---|
| RSYC yield backbone | Tile/ecodistrict curves, control totals, species composition, representative stands, buck-yield tables | The current national batch is a pipeline test, not a national supply estimate |
| Harvest and AAC/THLB | Observed/hold/low/high paths, AAC/NFD screens, THLB workstream | 119 Mm³ is the 2024 observed hold, not a forecast; the managed-landbase proxy is not a THLB |
| Provincial allocation | Documented ceilings, least-deviation allocation LP, provincial shares | A screening allocation, not a legal determination; the runner still uses a national cap plus BC zonal caps |
| Bucking and residue | Product, sort, and residue volumes; recovery fractions; residue ledger | Taper proxies and grade status must travel with the rows |
| Residue cost/supply curve | National and BC zonal caps, tiered rest-of-Canada caps, variable costs, recovery cases | BC zonal split is an adopted reference; rest-of-Canada tiers are scenario assumptions |
| OSeCAN interface data | `SUPBCW`/`B1`/`B2` activity limits and variable costs, zonal reconciliation, provenance, labels | Versioned; must be loaded in one of two modes (§4) |
| Forestry UI | Static Forest Ledger, live scenario form and API, caveats and labels | Must not imply policy-grade status for a draft or screening run |

### 3.2 Not our responsibility

Energy-policy design (instrument choice, stringency, timing); OSeCAN model
structure or policy constraints (reserve margin, capacity credits, build rates,
UDC); capacity expansion; transmission, intertie, curtailment, or unit-commitment
policy; carbon-price policy; and signoff of energy-model source, assumptions, or
publishable results. These belong to Taco Niet's team. Running OSeCAN does not
transfer ownership or signoff.

## 4. Interface contract

The interface is deliberately narrow: supply technologies, annual activity
limits, variable costs, and the metadata needed to interpret them. Two loading
modes exist and must not be mixed:

- **National split mode:** load `SUPBCW` (rest of Canada) with `SUPBCWB1` and
  `SUPBCWB2` (BC zones). The three caps sum to the national total in every year.
- **BC comparison mode:** load `SUPBCW` alone as a national rollup. Do not load it
  with `B1`/`B2`; it is not additive in this mode.

| Element | Direction | Unit or form | Requirement |
|---|---|---|---|
| `SUPBCW` | Forestry to OSeCAN | PJ/yr cap; $/GJ | Rest-of-Canada supply in split mode; national rollup in comparison mode |
| `SUPBCWB1`, `SUPBCWB2` | Forestry to OSeCAN | PJ/yr; $/GJ | BC zonal supply. Adopted 2028+ reference is 43.523816 PJ/yr, split 3.605475 (B1) + 39.918341 (B2) |
| `SUPBCWT1`–`T4` | Forestry to OSeCAN | PJ/yr; $/GJ | Rest-of-Canada tier caps in split mode; the tier sum must equal the rest-of-Canada cap |
| Zonal reconciliation | Both | PJ/yr | In split mode, `SUPBCW` + `B1` + `B2` equals the national total in every year; no double counting |
| Provenance | Forestry to OSeCAN | Metadata | Scenario id, harvest path, recovery fraction, cost basis, supply-curve mode, source paths, generation date, base/model commits |
| Labels | Both | Enum | `screening_only` or `draft`; `policy_grade=false` unless a separate signed process authorizes otherwise |

Interface rules:

- An artifact without provenance or a label is not interface-valid.
- Activity limits are PJ per year and costs are $/GJ; do not convert units inside
  OSeCAN.
- No energy-policy assumption and no harvest recommendation crosses the
  interface. Constraint sets use `clews.constraint_set.v2`; scenarios use
  `clews.scenario.v1`; a schema change is an interface change (§7).

## 5. OSeCAN as a black-box integration test

We treat OSeCAN as a black box. We do not edit its source, structure, policy
constraints, or solver setup. We run a disposable copy of the public model with
the committed interface patches and read the summary, dispatch, and manifest.

The test asks whether the `SUPBCW`/`B1`/`B2` interface is readable, bounded,
costed, and traceable:

- Do the caps load at the intended PJ/yr and bind when expected?
- Do costs flow at $/GJ without an implicit unit or currency change?
- Does the zonal split reconcile without double counting?
- Do provenance and labels survive into the summary and manifest?
- Does a forestry-side change produce a predictable, explainable response?

The purpose is to verify the interface and inform iterative design, not to
validate a harvest level, residue potential, energy pathway, or policy. When a
result looks wrong, classify first: forestry data, interface mapping, or energy
model. The default fix is forestry-side; request an energy-side change only when
a valid forestry input cannot be represented or the contract is wrong.

## 6. `policy_test_v1` status

`policy_test_v1` is a **draft interface test**, not a policy deliverable. It has
`status=draft` and `policy_grade=false`. It is a separate constraint family from
`screening_v0`; results from the two are not interchangeable.

The set exercises the forestry interface under a national peak-proxy reserve
margin, reviewed capacity credits, build rates, and explicit caps. It is **not**
an energy-policy recommendation and must not be presented as energy-policy
advice.

The current set is a Stage-1 sourced draft built on a narrow desk review, not a
signed source register. The peak factor, several capacity credits, and provincial
standards remain draft or reviewed; transmission, curtailment/timeslices, and unit
commitment remain structural gaps. Any partner or public use must carry `draft`
and `policy_grade=false`. See `planning/policy-test-v1.md` §5 and §9.

## 7. Change protocol

| Change | Owner | Route | Record |
|---|---|---|---|
| Forestry data/method or UI (RSYC, AAC, bucking, residue, allocation, curve, labels) | FRESH module | This repository | `planning/`, `CHANGE_LOG.md`, `ui/README.md`, `workflows/ui/README.md` |
| Energy-policy design, OSeCAN structure, reserve margin, credits, caps, transmission, carbon price | Taco Niet's team | Energy/OSeCAN repository or agreed branch | Their record; notify FRESH |
| Interface names, units, direction, zonal split, labels | Joint | Both sides | This note and the energy-side equivalent; bump the interface version |
| Third collaborator inputs | Third team | Their workstream | Notify both sides before an interface change |

Rules:

1. No energy-side change is made in this repository; no forestry-side assumption
   is changed inside OSeCAN merely to make a test pass.
2. An interface change is complete only when both sides record it and the version
   is bumped. Every reported run records scenario id, constraint set id, model
   commit, and date.
3. If a run disagrees, classify before changing: forestry data, interface
   mapping, or energy model. Do not adjust the other side's assumptions to hide
   an unexplained difference.

## 8. Current reference points

| Case | Objective (M$) | Cumulative 2021–2070 (Mt CO₂) | 2050 (Mt CO₂) | Labels | Use |
|---|---:|---:|---:|---|---|
| Canonical fleet-corrected 119 Mm³ | 1,445,655.44 | 424.710127 | 0.0 | `screening_only=true`, `policy_grade=false`, `canonical=true`, `fleet_corrected=true` | Canonical screening reference |
| `policy_test_v1` sourced draft | 1,458,305.16 | 1,024.824 | 0.490203 | `status=draft`, `policy_grade=false` | Interface test only; not policy |

Metric order is objective, cumulative emissions, then 2050 emissions. The two
rows use different constraint families and configurations; their difference is
not a policy effect and must not be reported as one. The draft row does not
replace the canonical row.

## 9. Non-claims

- This note does not assign funding, data licences, publication rights, or author
  order, and does not replace a signed collaboration agreement.
- It does not make `policy_test_v1` policy-grade or validate the RSYC chain, the
  residue curve, or OSeCAN.
- It does not define the third collaborator's internal scope.

## 10. References

- `planning/policy-test-v1.md` — constraint-set design, data gates, and non-claims.
- `planning/harvest-aac-feasibility.md` — harvest/AAC screen and provincial allocation caveats.
- `planning/bc-woody-residue-supply-curve.md` — B1/B2 split and comparison-rollup rule.
- `planning/fleet-correction-canonicalization.md` — canonical 119 Mm³ numbers and supersession.
- `ui/README.md` — forestry UI views, labels, and caveats.
- `workflows/ui/README.md` — scenario contract, runner, model setup, and interface generation.
