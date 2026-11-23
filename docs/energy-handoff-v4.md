# Energy-team handoff — national facility chain v4 (E1 + E2c)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Audience | Taco Niet and the energy/OSeCAN policy team |
| Status | Read-only screening handoff; model estimate, not observed, not policy grade |
| Model | `national-facility-chain-v4`; model commit `6040963`, data commit `f0f77a0` |
| Coverage | 13/13 jurisdictions: 4 E1 measured + 9 E2c density-calibrated |
| Data dependency | Open data only; no request, DUA, restricted data, or approval |
| OSeCAN status | Handoff v8 read-only; `official_osecan_changed=false` |
| Scope boundary | Documentation only; no code, model, UI, or official OSeCAN change |

This memo is the short decision-oriented companion to
`docs/handoff-national-facility-chain-v4.md`. It is for energy-team review of
the v4 forestry-residue interface. It is not an official OSeCAN result, an
operating plan, or a policy target.

## 1. Bottom line

The v4 chain covers all 13 jurisdictions with measured E1 coefficients where
they exist and E2c open-inventory/density-calibrated coefficients elsewhere.
It has no request or DUA dependency: everything used here is open data or a
documented donor/proxy assumption. At the 119 Mm³ reference case, forestry
products and residues are large enough to matter to the energy model, but the
energy side must choose four definitions before any single residue value is
treated as the interface value.

| Group | Jurisdictions | Basis |
|---|---|---|
| E1 measured | BC, QC, NB, YT | Four-province bucking pilot, 591 measured stems; measured product shares and total residue |
| E2c density-calibrated | AB, SK, MB, ON, NS, PE, NL, NT, NU | Open-data imputation v3; SK/NS/PE use open inventory density, MB uses donor median density, AB/ON/NL/NT/NU retain a labelled 1,000 stems/ha proxy |

The coefficient artifact has zero donor product coefficients. All rows carry
`model_estimate=true`, `observed=false`, `policy_grade=false`, and
`national_claim=false`. E2c is an open-data density calibration, not measured
inventory. Density calibration changes stems/ha expansion and the expanded
reference volume; it does not change the per-m³ product or residue
coefficients.

Relative to the frozen v3.2 baseline, v4 uses E2c (E2 v3), not E2 v2. The
v3.2 chain used E2 v1. E2 v2 and E2c per-m³ coefficients are identical (54/54
rows unchanged); the v3.2→v4 product-share change is E2 v1→E2c, while the
density calibration is extensive. This matters for interpreting the deltas,
not for the 119 Mm³ values below.

## 2. 119 Mm³ reference case

The 119 Mm³ case is a planning allocation, not a legal AAC or a sustainable
supply determination. The exact assigned harvest is 118,977,339 m³. All values
are model estimates.

| Metric | Value | Unit |
|---|---:|---|
| Sawlog product | 33.957 | Mm³ |
| Panel product | 3.717 | Mm³ |
| **Solid product** | **37.674** | **Mm³** |
| **Pulp product** | **12.066** | **M odt** |
| Pellet product | 5.230 | M odt |
| Bio product | 930.9 | GWh |
| **Process residue** | **498.751** | **PJ** |
| **Harvest residue, gross** | **444.411** | **PJ** |
| Harvest residue, recoverable 35% | 155.544 | PJ |
| Harvest residue, recoverable 60% (reference) | 266.646 | PJ |
| Harvest residue, recoverable 75% | 333.308 | PJ |
| **Total gross** (process at 100% + harvest gross) | **943.162** | **PJ** |

Notes:

- Solid product is sawlog + panel. Process residue is the facility-assigned
  mill stream. Harvest residue is origin-province only and includes CFS branch
  and foliage dry mass.
- The 0.35/0.60/0.75 harvest cases are equal-status scenarios. 0.60 is a
  reference value, not a calibrated recovery factor.
- Total gross is shown for scale only. It is not a combined OSeCAN row:
  handoff v8 keeps process and harvest as separate streams and never sums them.
- At other process-availability cases, process residue is 124.688 PJ (25%),
  249.375 PJ (50%), and 374.063 PJ (75%). The 100% quantity is 498.751 PJ.

## 3. OSeCAN handoff v8

Handoff v8 maps the v4 totals to all 13 OSeCAN zones: 119 handoff rows, no
`UNMAPPED` bucket. The official OSeCAN tree is read-only. The build checks the
tree before and after: 146 files, tree SHA-256
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
0 changed files, and `official_osecan_changed=false`.

| Stream | Case | PJ | Tier |
|---|---|---:|---|
| Process | 0% availability | 0.000 | T1 |
| Process | 25% availability | 124.688 | T3 |
| Process | 50% availability | 249.375 | above_T4 |
| Process | 75% availability | 374.063 | above_T4 |
| Harvest | 35% recovery | 155.544 | T4 |
| Harvest | 60% recovery | 266.646 | above_T4 |
| Harvest | 75% recovery | 333.308 | above_T4 |

Tier positions compare each case with the adopted national supply-curve T1–T4
thresholds. `above_T4` means the case exceeds the T4 upper bound; it is not a
statement that the volume is undeliverable. Relative to the v3.2 handoff,
harvest 35% moves T3→T4; harvest 60%/75% and all process tiers are unchanged.

The zone allocation uses the v2 proxy weights: BC uses OSeCAN 2050 zonal
supply dispatch, AB/ON/QC use 2050 zonal dispatch activity as a proxy, and
single-zone/aggregate memberships elsewhere. Every multi-zone weight is
`proxy_share=true`; no observed sub-provincial residue split is used.

## 4. Four decisions for the Taco / energy team

Until these are answered, report the full ranges and do not label any case as
preferred.

### D1. Recovery basis for branch and foliage

**Decision needed.** Should branch and foliage count as energy feedstock at
all, and if so on what recovery basis? Options include gross total residue × a
uniform recovery factor (0.35/0.60/0.75), the product-chain
reference-recoverable definition, or stem-only residue with branch/foliage
excluded. Is 0.60 a calibrated value or only a reference? Should the basis vary
by province, species, or stream?

**Why it matters.** Branch and foliage add dry mass but no measured volume. At
119 Mm³, harvest gross is 444.411 PJ and recoverable is 155.544/266.646/333.308
PJ at 0.35/0.60/0.75. The 35% case sits at T4; 60% and 75% are above T4. The
choice changes both the quantity and the tier position.

**What would resolve it.** A documented definition, the evidence grade (survey,
mill trial, literature, or assumption), the geographic and temporal scope, and
whether the factor is province-, species-, or stream-specific.

### D2. Process-residue availability

**Decision needed.** What fraction of mill process residue is actually
available to the energy system in the OSeCAN 2050 context? Are the
0/25/50/75% screening cases acceptable, or is there an OSeCAN-specific
availability parameter? Is availability applied uniformly or by residue class
(SAW, PULP, PANEL, PELLET, BIO)? Is it a technical availability or an economic
dispatch outcome?

**Why it matters.** Process residue is the larger stream: 498.751 PJ at 100%,
124.688 PJ at 25%, 249.375 PJ at 50%, and 374.063 PJ at 75%. The 25% case is
T3; 50% and 75% are above T4. One availability assumption can dominate the
comparison with the 2050 energy context.

**What would resolve it.** The OSeCAN parameter or source, its units and
denominator, whether it varies by zone or year, and whether it is technical or
economic availability.

### D3. Biomass and waste incineration assumptions

**Decision needed.** Does OSeCAN include biomass or waste incineration beyond
forestry residue — for example municipal solid waste, agricultural residue,
construction/demolition wood, or black liquor? If so, does forestry residue
compete with those feedstocks for the same combustion capacity? Which
technologies consume solid biomass, and what heat rate, lifetime, and emission
assumptions apply? How is internal mill energy use treated?

**Why it matters.** The forestry handoff reports process and harvest residue as
separate streams and never combines them. If OSeCAN's biomass stack also
contains non-forestry waste or black liquor, the forestry tiers are not
directly comparable unless the denominators and technology sets are aligned.
Double counting is possible if mill process residue is counted both as a
forestry supply and as an internal mill energy input.

**What would resolve it.** The OSeCAN biomass/waste technology list, feedstock
definitions, capacity/activity limits, and the treatment of internal mill
energy use.

### D4. Industrial feedstock demand (chemicals) versus energy

**Decision needed.** Which industrial demands are in the OSeCAN demand stack —
chemicals, pulp, panel, pellet, liquid fuels, or other? Do they compete with
the energy system for the same residue? Should the facility-chain process
residue be net of internal mill use and already-committed chemical/industrial
feedstock? Are the chain's SAW/PULP/PANEL/PELLET/BIO classes consistent with
OSeCAN's demand sectors?

**Why it matters.** The facility chain assigns process residue to facilities by
class and province; it does not net out internal mill consumption or existing
industrial/chemical contracts. If OSeCAN assumes those volumes are already
committed, the available energy residue is lower than the handoff total.
Conversely, if OSeCAN adds new chemical or biofuel demand, the same residue may
be double counted.

**What would resolve it.** The OSeCAN industrial/chemical demand quantities,
price assumptions, and the accounting boundary between mill self-use, existing
industrial/chemical demand, and new energy demand.

### Requested response format

For each decision, please provide the chosen definition or parameter, the
evidence grade, the geographic and temporal scope, and whether it is a model
assumption or an observed value.

## 5. Limitations

- **Grade is not assigned.** `grade_status=not_assigned`,
  `pass_fail=not_assessed_no_pre_registered_tolerance`, and
  `official_grade_validation=false`. The sort comparison is dimension-only
  against TIPSY model output; it carries no defect, sweep, knot, rot, or
  product-quality information. It is not an official log-grade validation.
- **Donor tapers remain material.** Only BC and QC have local tapers. The other
  11 jurisdictions, including E1-measured NB and YT, use published
  cross-jurisdiction donor tapers. Unsupported species and out-of-domain stems
  are excluded, so the measured coefficients do not represent full provincial
  species coverage; hardwood limitations remain.
- **Capacity is mixed and partly proxy.** Capacity v3 combines published mill
  records with class-median proxy capacity and applies a 1.2× provincial floor.
  The 100/119/150 Mm³ cases are planning allocations, not legal harvest levels.
  Proxy capacity remains material to the assignment.
- **Zone weights are proxies.** BC uses OSeCAN 2050 zonal supply dispatch,
  AB/ON/QC use 2050 zonal dispatch activity as an activity proxy, and
  single-zone/aggregate memberships are used elsewhere. Every multi-zone weight
  is `proxy_share=true`; no observed sub-provincial residue split exists.
- **Open-volume checks are incomplete.** SK, NS, and PE pass the ±50% screening
  check. AB, MB, ON, NL, NT, and NU are `not_available`. MB uses donor median
  plot density, not Manitoba inventory density; AB/ON/NL/NT/NU retain a
  labelled 1,000 stems/ha proxy.
- **Branch and foliage are model estimates.** They use published CFS equations
  with documented proxies, not weighed components. They add dry mass and
  energy but no measured volume.
- **Recovery is not calibrated.** The 0.35/0.60/0.75 cases are equal-status
  scenarios; 0.60 is a reference value only.
- **Energy-side context is limited.** No transport cost, annual dynamics,
  economics, trade, mill operating status, or policy target is modelled. The
  2050 OSeCAN values included in the artifacts are context, not targets.

## 6. Non-claims

- Not observed and not policy grade: `observed=false`,
  `policy_grade=false`, `national_claim=false`.
- Not a national supply estimate, forecast, legal AAC, sustainable harvest
  level, tenure decision, or operating plan.
- Not an official OSeCAN result. No official OSeCAN file or public surface was
  changed, and this memo does not imply OSeCAN/energy-team endorsement.
- No request, DUA, restricted data, or approval is required for this handoff;
  the evidence is open data plus documented donor/proxy assumptions.
- No claim that E2c is measured inventory, that branch/foliage is measured, or
  that recovery is calibrated.
- No claim about costs, economics, trade, mill operating status, or
  deliverability.
- This memo is documentation only; it makes no code, model, UI, or official
  OSeCAN change.

## 7. References and artifact paths

- `docs/handoff-national-facility-chain-v4.md` — full technical handoff and
  freeze record.
- `docs/model-card-national-facility-chain.md` — authoritative method card.
- `docs/handoff-e2-v3-density-calibrated-tree-lists.md` — E2c density
  calibration.
- `data/derived/national/facilities/e1_e2c_product_coefficients_v1/`
- `data/derived/national/facilities/national_product_chain_v5/`
- `data/derived/national/facilities/facility_residue_supply_v7/`
- `data/derived/national/facilities/national_residue_curve_v7/`
- `data/derived/national/facilities/national_facility_chain_scenarios_v7/`
- `data/derived/national/facilities/forestry_osecan_handoff_v8/`
- `data/derived/national/facilities/forestry_osecan_scenarios_v7/`

The data artifacts are in the `UBC-FRESH/clews-c2070-fresh-data` submodule at
data commit `f0f77a0`. The handoff v8 report is
`data/derived/national/facilities/forestry_osecan_handoff_v8/FORESTRY_OSECAN_HANDOFF_V8.md`.

## 8. Draft email to Taco

**Subject:** FRESH v4 forestry-residue handoff — 119 Mm³ and four decisions

```text
Hi Taco,
The v4 memo is `docs/energy-handoff-v4.md`; technical detail is `docs/handoff-national-facility-chain-v4.md`; artifacts are under `data/derived/national/facilities/forestry_osecan_handoff_v8/` and `data/derived/national/facilities/national_facility_chain_scenarios_v7/`.
Coverage is 13/13 jurisdictions (4 E1 measured + 9 E2c open-inventory/density), with no request or DUA dependency.
At 119 Mm³: 37.674 Mm³ solid, 12.066 M odt pulp, 498.751 PJ process residue, 444.411 PJ harvest gross (155.544/266.646/333.308 PJ at 0.35/0.60/0.75), and 943.162 PJ total gross.
OSeCAN handoff v8 is read-only and all-zone; no official OSeCAN files were changed.
Four decisions needed: branch/foliage recovery basis; process-residue availability; biomass/waste incineration assumptions; industrial chemical feedstock vs energy demand.
Please send the chosen definition/parameter, evidence grade, and scope for each.
Thanks, FRESH
```

The email deliberately makes no promise to change official OSeCAN. Any change
to the official OSeCAN tree would be a separate energy-team decision and a
separate change record.
