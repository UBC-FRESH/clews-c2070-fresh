# Forest-Sector LP v1 — design note

| Field | Value |
|---|---|
| Document | `planning/forest-sector-lp-v1.md` |
| Date | 2026-11-20 |
| Status | Design draft. No model, UI, data, or interface change is implemented by this note. |
| Scope | Provincial/operable-stratum harvest allocation and residue supply accounting for the CLEWS-C2070 forestry-biomass seam |
| Audience | FRESH team; Taco Niet and the energy/OSeCAN policy team |
| Companion documents | `planning/module-boundaries.md`; `planning/harvest-aac-feasibility.md`; `planning/policy-test-v1.md`; `planning/bc-woody-residue-supply-curve.md` |

**Decision summary.** v1 is a provincial/operable-stratum harvest-allocation
LP with a least-deviation objective, a documented-ceiling cap set, a screening
operable-land cap, and a residue accounting layer that emits a weighted PJ cap
and $/GJ cost for `SUPBCW`/`SUPBCWB1`/`SUPBCWB2`. It does **not** optimize net
forest value and does **not** build a marginal cost curve. Those belong to v2
and v3, after the missing cost data exists. v1 is `screening_only`,
`policy_grade=false`, and is not a harvest recommendation.

## 1. Purpose and scope

### 1.1 Purpose

The current energy model receives harvest as an exogenous national ladder and
pays a flat residue cost. It does not see which province supplies the residue,
whether the provincial harvest pattern is admissible, or what the residue
cost basis is. The provincial allocation LP already exists for residue-factor
weighting, but the runner still uses a national cap and does not enforce
provincial AAC/NFD ceilings. This design makes the provincial harvest
allocation, the operable-land screen, and the resulting residue supply a
forest-sector decision, then hands a bounded, costed, traceable supply offer
to OSeCAN.

The LP is a **forest-sector supply model**, not an energy model. It answers:

- How much harvest can be allocated to each province/territory and operable
  stratum under documented ceilings?
- How much residue does that harvest generate at a stated recovery fraction?
- What PJ cap and $/GJ cost should `SUPBCW` and the BC zonal technologies
  carry, with what provenance and labels?

It does not answer which harvest level Canada should choose, what energy
policy should be, or what the socially optimal forest allocation is.

### 1.2 In scope

- Provincial/territorial harvest allocation under documented AAC, NFD, and
  proxy ceilings.
- A screening operable-land cap derived from the operable-proxy workstream,
  clearly separated from a legal THLB.
- Residue generation and recovery accounting by province/stratum.
- A weighted residue cap and cost for the existing OSeCAN supply technologies.
- Reproducible artifacts, provenance, and fail-closed validation.

### 1.3 Out of scope

- Energy-policy design, capacity expansion, reserve margin, transmission,
  curtailment, carbon price, or OSeCAN model structure. Those belong to Taco
  Niet's team per `planning/module-boundaries.md`.
- A legal AAC determination, a sustainable-supply estimate, a forecast, or a
  recommendation to harvest at any particular level.
- A real THLB, slope/access operability, tenure resolution, or silviculture
  response. The operable proxy is a screening land layer, not a THLB.
- A national marginal harvest or residue cost curve in v1.
- New OSeCAN technology names or interface versions without the joint change
  protocol in `planning/module-boundaries.md` §7.

### 1.4 Boundary and non-claims

FRESH owns the forest supply evidence, allocation logic, residue accounting,
and provenance. The energy/OSeCAN team owns the energy model and policy
interpretation. The handoff is the `SUPBCW`/`SUPBCWB1`/`SUPBCWB2` (and, where
used, `SUPBCWT1`–`T4`) rows. v1 does not make any harvest case policy-grade, does
not validate the RSYC chain, and does not establish that any province is
harvesting sustainably. The documented ceilings are a mixed set of statutory
AAC, approximate AAC, a repo-parsed TSA path, planning volumes, and proxies;
their sum is not a national AAC.

## 2. Decision variables

The v1 model is an annual LP. It is solved once per national harvest case and
held constant over the model horizon unless a documented year path exists
(§3.7). `t` is retained in the formulation so v2 can add time paths without
changing the variable names.

| Symbol | Meaning | Unit | Indices | v1 treatment |
|---|---|---|---|---|
| `H[p,s,t]` | Merchantable log harvest | m3 log/yr | province `p`, operable stratum `s`, year `t` | Primary decision variable |
| `G[p,s,t]` | Gross residue generated | ODT/yr | `p,s,t` | Derived: `G = f_gross[p,s] * H` |
| `R[p,s,t]` | Recoverable residue | ODT/yr | `p,s,t` | v1: scenario-closed at `R = rho * G`; v2: free within `R <= rho_max * G` |
| `A[p,s,n,t]` | Residue allocated to supply node | ODT/yr | `p,s`, node `n`, `t` | Optional in v1; required for v2 transport/facility |
| `d_plus[p,t]`, `d_minus[p,t]` | Deviation above/below the observed-share target | m3/yr | `p,t` | Objective slacks, non-negative |
| `CAP_EXOG[n,t]` | Adopted supply-node cap, where one exists | PJ/yr | `n,t` | Parameter from the canonical set; optional |
| `PJ_CAP[n,t]` | v1 computed residue potential by node | PJ/yr | `n,t` | Output from `R`; not a free variable |

**Units and conversions.** `H` is merchantable log volume (m3), not total
aboveground biomass. Residue factors are ODT per m3 of log. The energy
conversion is `1 ODT = 18.5 GJ` and `1 PJ = 1,000,000 GJ`, so
`PJ = ODT * 18.5 / 1,000,000`. Costs are $/GJ at the interface; where a source
is $/ODT, `$/GJ = $/ODT / 18.5`. The BC diagnostics are CAD 2020-equivalent;
the legacy FFCM currency and price year are undocumented. v1 carries the
existing numeric interface values and records the currency/year as reported or
`unspecified_legacy`; it does not apply an exchange rate or deflator and does
not relabel an undocumented value as CAD 2020.

**Operable stratum `s`.** v1 uses one aggregate stratum per province when the
operable-proxy output is not yet validated, and may use the proxy's
height/biomass/closure threshold cases when it is. The proxy is a screening
merchantable-land layer; it is not a legal THLB and has no slope, access,
economic, or silvicultural content. The stratum identifier, threshold case,
area, evidence tier, and proxy version must travel with every row.

## 3. Constraints

### 3.1 National harvest scenario

```text
sum_{p,s} H[p,s,t] = T[t]                                  for every t
```

`T[t]` is the national harvest scenario total (m3/yr), an exogenous
forest-sector scenario input, not an OSeCAN policy lever. v1 uses exact
equality to preserve comparability with the 100/119/150 Mm3 ladder. A total
above the documented model ceiling is infeasible and must fail closed, not be
clamped.

### 3.2 Provincial documented ceiling (AAC/NFD/proxy)

```text
sum_s H[p,s,t] <= U[p,t]                                   for every p,t
```

`U[p,t]` is the minimum of the documented ceilings that apply:

- a statutory or documented AAC where one exists;
- a documented NFD wood-supply value where it is the only or tighter bound;
- a documented planning volume or a flagged range midpoint where no AAC exists;
- zero for Nunavut, which has no FSD harvest, AAC, or managed-landbase row.

`U[p,t]` carries `basis` and `caveat` fields. It is **not** a national AAC and
does not resolve the Crown-versus-total tenure mismatch in NB, NS, and QC, the
BC repo-path versus current-Crown-AAC discrepancy, or the NT actual-mean proxy.
The existing allocation report already records these caveats; v1 must carry
them forward rather than re-label the sum as a legal ceiling.

### 3.3 Operable-proxy / intensity cap

```text
sum_s H[p,s,t] <= I[t] * sum_s OperableArea[p,s]           (optional)
```

`OperableArea` is the screening operable-proxy area in hectares and `I[t]` is
the intensity screen in m3/ha/yr. The current sensitivity is `I = 1.0` and
`1.2`; at `I = 1.0`, the 150 Mm3 case is infeasible by 6.113 Mm3, and at
`I = 1.2` it is feasible. The cap is omitted, with an explicit manifest flag,
when the proxy has not passed its own validation. It must never be described
as a THLB, an AAC, or an operability determination.

### 3.4 Residue mass balance and recovery

```text
G[p,s,t] = f_gross[p,s,t] * H[p,s,t]
0 <= rho[p,s,t] <= 1
R[p,s,t] = rho[p,s,t] * G[p,s,t]              (v1 scenario-closed)
R[p,s,t] <= rho_max[p,s,t] * G[p,s,t]         (v2 free recovery)
```

`f_gross` is the province/stratum residue factor in ODT per m3 log. The
existing recovery cases are low 0.35, base 0.60, and high 0.75. v1 uses one
case per run (base by default) and reports the others as sensitivities; it does
not choose a recovery fraction economically. The mass balance must close before
any PJ conversion. Mill residue, salvage, and whole-stem chip streams are not
added here unless a separate ledger prevents double counting.

### 3.5 Supply-node allocation and caps

```text
A[p,s,n,t] >= 0
sum_n A[p,s,n,t] = R[p,s,t]                    if all recovered residue is assigned
sum_{p,s} A[p,s,n,t] * 18.5 / 1e6 <= CAP_EXOG[n,t]   where an adopted cap exists
PJ_CAP[n,t] = sum_{p,s} A[p,s,n,t] * 18.5 / 1e6
```

In v1, `A` is optional: `PJ_CAP` can be computed directly from `R` by node
mapping. When `A` is used, the mapping is:

- `SUPBCWB1`, `SUPBCWB2`: BC residues assigned by the documented B1/B2
  crosswalk; the two caps sum to the BC total.
- `SUPBCW`: rest-of-Canada residues; BC is excluded to avoid double counting.
- `SUPBCWT1`–`T4`: rest-of-Canada tier caps; the tier sum equals the
  rest-of-Canada cap.
- Optional facility nodes in v2: residue assigned to a facility/transport
  node rather than directly to `SUPBCW`.

### 3.6 Interface reconciliation

```text
split mode:       PJ_CAP[SUPBCW] + PJ_CAP[SUPBCWB1] + PJ_CAP[SUPBCWB2] = PJ_CAP[CAN]
                  PJ_CAP[SUPBCWT1] + ... + PJ_CAP[SUPBCWT4] = PJ_CAP[SUPBCW]
comparison mode:  PJ_CAP[SUPBCW] = PJ_CAP[CAN]   (do not load B1/B2)
```

The two modes must never be mixed, as in `planning/module-boundaries.md` §4.
The current adopted BC zonal reference is 43.523816 PJ/yr from 2028, split
3.605475 PJ (B1) and 39.918341 PJ (B2). A v1 result that differs must carry an
explanation and a new scenario label; it must not silently overwrite the
adopted reference.

### 3.7 Time coverage and hold rule

v1 is a static annual allocation per national harvest case. It applies the
allocation from 2025 to 2070 with an explicit hold rule and keeps 2021–2024
observed. Only BC has a parsed AAC trajectory; other jurisdictions do not have
a documented 2025–2070 path. v1 must not interpolate or extrapolate an AAC
path. A time-varying allocation is a v2 item.

### 3.8 Transport and processing capacity (v2, not v1)

When facility/transport nodes are added:

```text
sum_{p,s} A[p,s,l,t] <= LinkCapacity[l,t]
sum_l A[p,s,l,t] <= FacilityThroughput[f,t]
```

These constraints require the `calibrated_cost_surfaces` gate (NRN/NRWN
vectors and provincial forest resource roads). They are not part of v1 and
must not be approximated with a distance-blind placeholder.

## 4. Objective

### 4.1 Options considered

| Option | Form | Strengths | Problems with current data | Verdict |
|---|---|---|---|---|
| A. Least deviation from observed allocation | `min sum_p (d_plus[p] + d_minus[p])`, with `d_plus - d_minus = sum_s H[p,s] - target[p]` | Transparent; matches the implemented provincial allocation LP; no invented economics; preserves observed behavior; easy to test | Status-quo bias; no incentive to recover residue; the observed 2024 mix itself exceeds the NB Crown AAC, so it is not a legal baseline | **Recommended for v1** |
| B. Maximize net value | `max sum (log revenue - harvest - transport - silviculture cost)` | Economically meaningful; makes harvest and recovery endogenous | No national log price deck; no stumpage, harvest/collection, road, or silviculture cost; legacy FFCM values are flat, composition-undocumented costs, not a curve | Defer to v2/v3 |
| C. Multi-objective | Weighted combination of A/B plus residue or employment | Can represent trade-offs explicitly | Weights are arbitrary without cost/revenue data; hides value judgments; hard to audit | Defer to v3 |

### 4.2 Recommended v1 objective

Use the existing lexicographic least-deviation rule:

```text
d_plus[p,t] - d_minus[p,t] = sum_s H[p,s,t] - target[p,t]
d_plus[p,t], d_minus[p,t] >= 0
target[p,t] = observed_2024[p] * T[t] / 118,977,339

primary:    minimize sum_p (d_plus[p,t] + d_minus[p,t])
secondary:  after fixing the primary optimum within 1 m3, minimize the maximum
            proportional increase above the observed-share target; cap-forced
            decreases are fixed by the ceilings and excluded
```

This is the same objective already implemented in
`workflows/national/harvest/provincial_allocation.py`, so v1 extends a tested
rule rather than inventing a new one. It is defensible because the inputs that
a net-value objective needs do not exist. It is explicitly a **screening
allocation**, not an economic equilibrium, a forecast, or a sustainable-supply
rule.

### 4.3 Why not net value in v1

A net-value objective would require, at minimum, a log price deck by
species/grade/region, stumpage/royalty, harvest/collection cost by system and
terrain, road and haul cost, and silviculture/regeneration cost. None exists as
an operational national input. The legacy FFCM `SupplyCost` is a
province-flat, component-undocumented cost with no annual volume slope; the BC
Stage B values are transport and processing diagnostics, not a harvest cost.
Attaching the full log cost to residue is wrong by an order of magnitude: at
18.5 GJ/ODT and about 0.053 ODT recoverable residue per m3 log, a $1/m3 harvest
cost is about $1.02/GJ of residue, and the legacy $36.85–43.06/m3 log cost maps
to roughly $37.6–43.9/GJ, 15–18× the current 2.457 $/GJ. v1 must not manufacture
that number.

### 4.4 Residue in the v1 objective

Residue is an **output** of the v1 harvest allocation at the scenario recovery
fraction, not an optimized quantity. The LP does not choose between leaving
residue on site and recovering it; that choice needs a recovery cost and a
demand signal. v1 reports the potential and its cost basis. v2 makes `R` a
decision variable with recovery cost; v3 adds a full marginal cost curve.

## 5. Cost data

### 5.1 What exists

| Component | Coverage | Value / unit | Status and v1 use |
|---|---|---|---|
| Legacy FFCM FMU `SupplyCost` for logs | 733 FMUs in 9 provinces; PE/YT/NT/NU absent | 36.85–43.06 $/m3 log, flat within province (BC 36.85; ON 40.305; AB/SK/MB 40.572; QC 42.469; NB/NS/NL 43.062) | Legacy 2019 basecase; composition undocumented; not a stumpage rate and not a marginal cost. Do not use as a residue cost or a net-value revenue. |
| Legacy FFCM FMU `SupplyCost` for logging residue | Same 733 FMUs | 45.454084659 $/odt everywhere = 2.456978 $/GJ at 18.5 GJ/odt | Source of the current flat residue cost; no collection/processing breakdown. Use as the non-BC default only with a `legacy_ffcm_flat` basis label. |
| BC Stage A road transport | BC; 298 zone–facility–grade links, 2018 NRN graph | 1.54–68.56 CAD/m3; mean 32.31, median 35.09 | Diagnostic; ferry cost is a placeholder. v2 transport input only. |
| BC Stage B residue transport and processing | BC | Transport 40.08 CAD/odt; processing 104.15 CAD/odt; net contribution 870.13 M CAD | Diagnostic; not a harvest cost. v2 facility input. |
| BC zonal residue cost derivation | BC B1/B2 | B1 55.40 CAD/odt = 2.995 $/GJ; B2 31.57 CAD/odt = 1.707 $/GJ | Transport + processing only; excludes collection, grinding, bundling, stumpage, opportunity cost. Use for B1/B2 with that caveat. |
| CLEWS national residue cost tiers | Rest of Canada | 2.456978 / 3.194071 / 3.500000 $/GJ for T1/T2, T3, T4 | Scenario values, not a fitted marginal-cost curve. Keep as the v1 tier set; do not interpolate a curve from them. |
| Silviculture | None nationally | — | Missing. Do not set to zero. |
| Provincial stumpage tables | None as a separate table | — | Missing. The legacy FMU cost may embed stumpage, but its composition is undocumented; do not call it stumpage. |
| National road/transport cost | BC diagnostic only | — | Missing nationally; `calibrated_cost_surfaces` gate blocked. |
| National marginal cost curve | None | — | Do not fabricate from the five-step legacy values. |

### 5.2 What v1 uses, and what it refuses to invent

v1 uses the existing interface values where they have a documented basis:

- BC B1/B2: the Stage B-derived 2.995 / 1.707 $/GJ, flagged as transport +
  processing only.
- Rest of Canada: the existing 2.457 $/GJ base, with 3.194 / 3.5 $/GJ as tier
  sensitivities. The legacy value is flat, so v1 must not claim province-level
  cost variation where none exists.
- Where a province has no cost row, v1 uses the documented fallback tier and
  labels it `fallback_tier`, not a province-specific estimate.

v1 does not add stumpage, harvest/collection, silviculture, road, or
opportunity cost to the residue price. Missing is missing, not zero. No
"reasonable" placeholder becomes a cost row. No marginal curve is fitted to
the five flat FFCM levels, and no full log cost is allocated to residue.

### 5.3 Currency and basis

The current interface is `$/GJ`; the BC diagnostics are `CAD`; the legacy FFCM
currency and price year are undocumented. v1 records a single `cost_basis_id`
per row with currency, price year, and source, using `unspecified_legacy`
where the source does not state them. It does not apply an exchange rate or
deflator because none is documented. A row without a currency-year basis is
not interface-valid. If a future source is USD, it must be converted
explicitly and the conversion recorded; silent numeric mixing is a test
failure.

## 6. Integration with OSeCAN

### 6.1 Interface outputs

| Technology | v1 cap | v1 cost | Mapping |
|---|---|---|---|
| `SUPBCW` | Rest-of-Canada recoverable residue, PJ/yr | Residue-weighted average of non-BC province costs, $/GJ | BC excluded in split mode |
| `SUPBCWB1` | BC South Coast / Lower Mainland residue, PJ/yr | 2.995 $/GJ (Stage B basis) or updated documented value | BC crosswalk |
| `SUPBCWB2` | BC Interior & North residue, PJ/yr | 1.707 $/GJ (Stage B basis) or updated documented value | BC crosswalk |
| `SUPBCWT1`–`T4` | Rest-of-Canada tier caps, PJ/yr | 2.457 / 3.194 / 3.5 $/GJ | Tier definitions; sum equals `SUPBCW` |
| `PJ_CAP[CAN]` | National total, PJ/yr | — | Comparison rollup only; not additive with B1/B2 |

Cost weighting:

```text
c[n,t] = sum_{p,s} R[p,s,n,t] * c[p,n] / sum_{p,s} R[p,s,n,t]
```

with a fail-closed check that the denominator is positive and that `c[n,t]`
lies within `[min c[p,n], max c[p,n]]` for the contributing provinces. A
weighted average is not a marginal cost; it is the cost of the average unit of
residue in that node, and it is labelled as such.

### 6.2 Caps and reconciliation

```text
PJ_CAP[n,t] = sum_{p,s in n} R[p,s,t] * 18.5 / 1e6
```

In split mode, `SUPBCW` + B1 + B2 must equal the national total in every year.
The current adopted BC reference is 43.523816 PJ/yr from 2028 (3.605475 B1 +
39.918341 B2). v1 may produce a different BC potential because it uses a
different allocation/recovery case; the difference must be reported as a
scenario, with the adopted reference retained. In comparison mode, `SUPBCW` is
the national rollup and `B1`/`B2` must not be loaded.

The existing runner's national arithmetic (one national residue factor, then
BC zonal caps from the canonical set) remains the regression baseline. v1 adds
province-level accounting; it does not change the canonical reference in
place.

### 6.3 Provenance and labels

Every interface artifact must carry:

| Field | Requirement |
|---|---|
| `scenario_id` | Unique; includes national harvest case and allocation case |
| `national_harvest_m3` | Scenario total and year basis |
| `allocation_case` | `observed`, `lp_100m`, `lp_119m`, `lp_150m`, or v1 id |
| `recovery_fraction` | Low/base/high case and numeric value |
| `cost_basis_id` | Source and component coverage (for example `bc_stage_b_transport_processing`, `legacy_ffcm_flat`) |
| `cost_currency`, `cost_year` | Explicit; no implicit FX or deflator |
| `source_paths` | Input artifacts and SHA-256 hashes |
| `generated_at_utc`, `model_commit` | Reproducibility |
| `interface_version` | Bumped only under the joint change protocol |
| `status`, `policy_grade` | `screening_only` or `draft`; `false` unless a separate signed process authorizes otherwise |
| `caveats` | Non-claims, proxy status, tenure issues, cost gaps |

An artifact without provenance or a label is not interface-valid. The labels
`draft` and `screening_only` are not interchangeable with `policy_grade`; a
forestry-side change must not relabel an OSeCAN result.

### 6.4 Ownership and change protocol

FRESH owns everything upstream of the `SUPBCW`/`B1`/`B2` rows: allocation,
residue factors, costs, provenance, and labels. The energy team owns the
energy model, policy interpretation, and source signoff. New province-level
technology rows, a changed zonal split, or a changed cap/cost definition are
interface changes under `planning/module-boundaries.md` §7; they require both
sides to record the change and a version bump. Running OSeCAN does not
transfer ownership or signoff.

## 7. Staged implementation

| Stage | Scope | Inputs | Outputs | Exit criterion |
|---|---|---|---|---|
| **v1** | Provincial allocation + operable cap + flat/province cost + weighted interface | Existing harvest basis, provincial allocation, residue factors, operable proxy (if validated), legacy/BC costs | Province/stratum allocation; residue potential; weighted PJ caps and $/GJ costs; manifest and labels | With the operable cap disabled, 100/119/150 feasible under documented ceilings and 200 infeasible; with `I=1.0`, 150 infeasible; conservation/cap/cost/interface tests pass; labels present |
| **v2** | Transport and facility layer | BC Stage A/B links, NRN/NRWN vectors, forest resource roads, facility throughput/cost | Residue allocation to links/facilities; link and facility capacities; transport/processing cost by node | BC pilot traces residue from zone to facility with conservation and no double counting; `calibrated_cost_surfaces` gate passes for the covered area |
| **v3** | Marginal cost curve and net-value/multi-objective | Stumpage, harvest/collection, road, silviculture, log price deck; cost-volume steps by FMU/stratum | Cost-volume curve; endogenous harvest/recovery; net-value or multi-objective frontier | Curve is reproducible, signed off, and free of flat-cost or full-log-cost artifacts; interface change is agreed |

### 7.1 v1 implementation steps

1. Extend the existing provincial allocation runner or add a v1 wrapper that
   keeps the same PuLP/CBC formulation and the same documented-ceiling loader.
   Do not replace the tested allocation with a new solver or objective.
2. Add the stratum dimension only when the operable-proxy artifact passes its
   own validation; otherwise run province-only and record
   `stratum_basis=province_only` in the manifest.
3. Compute residue per province/stratum from the province residue factors and
   the allocation, at the selected recovery case, and reconcile to the national
   total.
4. Map BC residue to B1/B2 using the documented crosswalk; map the rest to
   `SUPBCW`; keep tier definitions consistent with the canonical set.
5. Compute weighted caps and costs; write a manifest with input hashes, solver
   version, objective, binding provinces, and caveats.
6. Emit interface rows in the existing `SUPBCW`/`B1`/`B2`/`T1`–`T4` names. Do
   not change the canonical reference in place; produce a new scenario id.
7. Run the regression and interface tests in §8 before any UI exposure.

### 7.2 v2 implementation steps

1. Add facility and link nodes with capacity, cost, and loss parameters.
2. Add residue allocation `A` to links/facilities and the transport/processing
   constraints in §3.8.
3. Populate BC Stage A/B links first; keep national coverage explicitly
   incomplete until the data gate passes.
4. Add cost basis labels for every link/facility and a no-double-counting
   check against the province residue ledger.

### 7.3 v3 implementation steps

1. Acquire and register stumpage, harvest/collection, road, and silviculture
   data with source hashes and evidence tiers.
2. Build cost-volume steps by FMU/stratum; do not interpolate from the five
   legacy FFCM levels.
3. Add log revenue/price deck and a net-value objective, or a multi-objective
   frontier with explicit weights.
4. Re-run the allocation and compare to v1; report the difference as an
   economic result, not a correction.

## 8. Acceptance criteria and tests

| ID | Test | Assertion | Fail behaviour |
|---|---|---|---|
| FS-LP-01 | Harvest conservation | `abs(sum H - T) <= 1 m3/yr` for every case | Error; do not rescale silently |
| FS-LP-02 | Provincial caps | `sum_s H[p,s] <= U[p] + 1 m3/yr`; no province above its documented ceiling | Error; report province and basis |
| FS-LP-03 | Operable cap | If enabled, `sum_s H[p,s] <= I * OperableArea[p] + 1 m3/yr`; 150M at `I=1.0` reports infeasible with a 6.113 Mm3 deficit, not a clamp | Error/infeasible; no fallback to no cap |
| FS-LP-04 | Residue mass balance | `G = f_gross * H`; `R = rho * G`; `0 <= rho <= 1`; no negative or NaN | Error |
| FS-LP-05 | PJ conversion | `PJ = ODT * 18.5 / 1e6`; caps sum to the national total in split mode; tiers sum to `SUPBCW` | Error on mismatch > 1e-6 PJ |
| FS-LP-06 | Cost signs and bounds | Every `c >= 0`; weighted `c` within `[min, max]` of contributors; cost basis/currency present | Error; no missing-as-zero |
| FS-LP-07 | Objective | L1 optimum matches the solver; secondary tie-break does not worsen L1 beyond 1 m3; binding set is reported | Error; no hidden objective |
| FS-LP-08 | Reproducibility | Same inputs, solver, and tolerances produce the same allocation within 1 m3; manifest hashes match | Error; no unversioned run |
| FS-LP-09 | Fail-closed inputs | Missing province, missing factor, unknown province, allocation sum mismatch, missing label/provenance, or unsupported cost basis raises a descriptive error | Error; no default row |
| FS-LP-10 | Interface split/comparison | Split mode reconciles `SUPBCW + B1 + B2 = CAN`; comparison mode loads `SUPBCW` alone; no double counting | Error; no mixed mode |
| FS-LP-11 | Labels | v1 artifacts are `screening_only`/`draft`, `policy_grade=false`; a policy-grade flag cannot be inherited | Error |
| FS-LP-12 | Regression anchors | With the operable cap disabled, reproduce the current allocation anchors: 100M L1 3.233 Mm3, 119M 6.213, 150M 18.683, 200M deficit 30.477. With `I=1.0`, the 150M deficit is 6.113 | Error; record any intended change as a new scenario |

**No fake curve checks.** The suite must also assert that no v1 cost row is
derived by interpolating the five FFCM flat levels, that no full log cost is
attached to residue, and that every cost row names its component coverage.
These are review gates as well as tests.

## 9. Open questions

| # | Question | Why it matters | v1 default | Owner |
|---|---|---|---|---|
| 1 | Is least-deviation acceptable as the v1 objective, or should v1 wait for a net-value objective? | It determines whether v1 is a screening allocation or an economic model | Least deviation, explicitly screening | FRESH PI |
| 2 | Is the national harvest total exogenous, or should the LP choose it within AAC? | Endogenous national harvest would couple forestry to energy demand and cross the boundary | Exogenous scenario total; provincial allocation endogenous | FRESH + energy team |
| 3 | Should OSeCAN see one weighted `SUPBCW` cost or province-level rows? | Weighted cost hides heterogeneity; province rows are an interface change | Weighted existing rows; no new technology names | Joint interface owners |
| 4 | What is the cost currency/year and deflator/FX rule? | Current values mix CAD diagnostics and `$` interface units | Carry numeric value; label CAD 2020-equivalent where sourced; no conversion | FRESH cost review |
| 5 | Who acquires stumpage, harvest/collection, road, and silviculture data? | Without them v2/v3 cannot price harvest or recovery | Missing, not zero | FRESH + partners |
| 6 | What is the operable proxy's validation status and legal meaning? | It is a screening land layer, not a THLB; using it as a cap could be misread | Province-only if unvalidated; optional intensity screen if validated | FRESH landbase lead |
| 7 | Who owns the low/base/high recovery fractions and the residue definition? | 0.35/0.60/0.75 change the PJ cap materially; taper residue differs from AGB/slash | Base 0.60; low/high sensitivity | FRESH residue lead |
| 8 | How should BC B1/B2 reconcile with the provincial allocation? | BC is one province but two supply zones; the adopted 43.523816 PJ reference must not be overwritten | Report both; retain adopted reference | FRESH + energy team |
| 9 | What is the 2025–2070 AAC path outside BC? | Only BC has a parsed trajectory; extrapolation would be invented | Hold 2024/2025 values; no interpolation | FRESH |
| 10 | Is the v2 facility/transport layer BC-only or national? | National coverage needs NRN/NRWN and forest-road data that are not ready | BC pilot first; national later | FRESH + data team |
| 11 | Who can mark a forest-sector artifact `policy_grade=true`? | The label is a signoff, not a code flag | No one in v1; keep false | FRESH PI + energy signoff |
| 12 | Can residue be supplied independently of harvest (salvage, mill residue)? | Independent streams could double count or bypass the harvest allocation | No; harvest-linked only in v1 | FRESH residue lead |

## References

- `planning/module-boundaries.md` — ownership, interface contract, split/comparison modes, labels.
- `planning/harvest-aac-feasibility.md` — AAC/NFD/proxy screen, cost-data inventory, 150M/200M findings.
- `planning/policy-test-v1.md` — constraint-set design and fail-closed conventions; not a forestry result.
- `planning/bc-woody-residue-supply-curve.md` — B1/B2 crosswalk, BC residue ratio, Stage B cost basis.
- `planning/national-bucking-supply-architecture.md` — residue ledger and commodity-chain context.
- `workflows/national/harvest/provincial_allocation.py` — implemented least-deviation allocation LP.
- `workflows/national/harvest/residue_factor_allocation.py` — harvest-weighted residue factors.
- `data/derived/national/harvest_basis/provincial_allocation/` — allocation CSVs, report, summary, manifest.
- `data/derived/national/harvest_basis/residue_factor_by_allocation/` — weighted residue-factor artifacts.
- `data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv` — province residue factors.
- `data/derived/national/clews_supply/bc_woody_residue/` — BC zonal caps, costs, crosswalk.
- `metadata/ffcm-archive/parsed/fmu_supply.csv` — legacy FMU log/residue `SupplyCost`.
- `metadata/readiness-gates.yaml` — `calibrated_cost_surfaces` gate.
- `workflows/ui/osecan_runner.py` — current supply-cap arithmetic and interface generation.
- `data/derived/national/clews/osecan/national_supply_curve/` — tier definitions and national supply-curve report.
