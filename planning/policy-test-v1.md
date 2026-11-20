# policy_test_v1 — constraint-set design (planning only)

**Status:** design note, not implemented, no solve run. `policy_test_v1` is a
separate constraint family from `screening_v0`. Nothing in this document
changes model behaviour, and no result from this set may be labelled
`policy_grade=true` until the data gates in §5 pass.

**Scope:** OSeCAN FAST / C2070 electricity core, 2021–2070, single OSeMOSYS
`REGION=CAN`, 13 zone-coded nodes.

---

## 1. What the current model actually supports

| Surface | Current state | Consequence for policy_test_v1 |
|---|---|---|
| Spatial structure | One `REGION` (`CAN`); provinces and zones are name-coded; 13 zones | A national reserve margin is natural; a provincial/zone reserve margin is not. |
| Transmission | 24 `TRN<from><to>` technologies, 5% flat loss, 500 $/kW, symmetric ratings, investable | Individual link capacity caps are possible now; a network / simultaneous interchange model is not. |
| Time | `TIMESLICE=ANN`; `YearSplit=1.0` for every year | No peak, season, curtailment, ramping, or unit-commitment representation. Reserve margin can only use an annual peak proxy. |
| Reserve margin | Model file has active `RM3_ReserveMargin_Constraint`; `otoole.yaml` defaults `ReserveMargin=1`, tags `0`; no rows are generated | Native RM3 is currently a no-op. With 13 zonal electricity fuels and one region, native RM3 cannot express a national margin (§2.1). |
| Capacity caps | `TotalAnnualMaxCapacity` and `TotalAnnualMaxCapacityInvestment` are active; `otoole.yaml` defaults both to `-1` (unbounded); runner creates empty files | Build rates and total caps are implementable now with no structural change. |
| Activity caps | `TotalTechnologyAnnualActivityUpperLimit` is active and already used for wind, PWRBIO, BCW, hydro, and tiers | Existing caps can be carried into the policy set. |
| UDC | `UDC` block exists; `UDCMultiplierActivity`, `UDCMultiplierTotalCapacity`, `UDCMultiplierNewCapacity` are active | A national reserve-margin equation, intensity caps, and provincial caps can be added without touching the LP structure. |
| Fleet | Checked `runtime/osecan_model/data/params/ResidualCapacity.csv` has non-zero rows only for `TRN*`; all 130 generator residual rows are `0.0` | The checked copy is a placeholder-fleet copy. A policy-grade reserve margin must not be run until the CER 2021 fleet is materialised. |
| Labels | `workflows/ui/osecan_runner.py` accepts only `set_id=screening_v0`, `status=screening_only`, and writes `screening_only` / `policy_grade=false`; UI badges are hard-coded | A policy set needs a separate loader/runner branch and explicit label logic; it must not reuse the screening gate. |

Evidence paths:

- `runtime/osecan_model/src/osecan/model_structure.py` — single region, zones,
  links, UDC policy dictionary, power archetypes.
- `runtime/osecan_model/resources/osemosys_fast_osecan.txt` — RM3 at the
  “Reserve Margin Constraint” block; UDC1/UDC2 at the “User Defined
  Constraints” block; capacity and activity constraints.
- `runtime/osecan_model/src/osecan/data/build_params.py` — emits
  `CapacityToActivityUnit=31.536`, `TIMESLICE=ANN`, activity caps; does not
  emit `ReserveMargin`, `ReserveMarginTagFuel`,
  `ReserveMarginTagTechnology`, `TotalAnnualMaxCapacity`, or
  `TotalAnnualMaxCapacityInvestment`.
- `runtime/osecan_model/config/otoole.yaml` — reserve-margin defaults and
  capacity defaults.
- `workflows/ui/osecan_runner.py` — canonical-set gate, empty cap-file
  creation, existing scenario caps, hard-coded screening labels.
- `ui/app.js`, `ui/index.html` — hard-coded `screening_only` and
  `policy_grade=false` copy.
- `data/derived/national/clews/osecan/canonical_constraint_set.json` —
  `screening_v0` explicitly omits reserve margin, transmission, curtailment,
  and build rate; `policy_test_v1` is reserved but undefined.

---

## 2. Implementable now, without structural changes (Stage 1)

### 2.1 Planning reserve margin and capacity credits

#### Why not native RM3 as the national constraint

`RM3` is evaluated per fuel:

```text
sum(output activity tagged by fuel) * ReserveMargin
    <= sum(tagged capacity * CapacityToActivityUnit)
```

The model has 13 generation fuels (`ELC<z>01`) and 13 grid fuels
(`ELC<z>02`), but one `REGION`. Tagging all zonal fuels would create 13
separate constraints, each comparing one zone’s load with the **entire
national** capacity sum. The largest zone would drive the result; total
national load would not. That is not a national planning reserve margin. A
grid-fuel tag also includes `TRN` output as well as `PWRTRN` output, so the
native per-fuel sum can double-count delivered energy.

**Decision:** enforce the binding national reserve margin with a UDC, which
already exists in the model:

```text
sum_t  C2A * credit_t(y) * TotalCapacityAnnual_t(y)
  - RM(y) * peak_factor(y) * sum_z Activity(PWRTRN<z>)(y)  <=  0
```

where:

- `C2A = 31.536` PJ per GW-year (`CapacityToActivityUnit`).
- `credit_t(y)` is the capacity credit in `[0,1]` (nameplate de-rating).
- `RM(y)` is the planning reserve margin (e.g. 1.15).
- `peak_factor(y) = peak demand / average demand`. With a single annual
  timeslice, this converts annual energy to a peak-capacity proxy.
- `sum_z Activity(PWRTRN<z>)` is gross generation in PJ/yr: every generator
  injects at `ELC<z>01`, and every `PWRTRN<z>` consumes that bus. It therefore
  includes distribution losses, which generation capacity must also cover.

This is an **annual peak proxy**, not a loss-of-load calculation. It is
implementable now but is not a substitute for hourly reserve adequacy. It can
only be policy-grade if the peak factor and capacity credits are sourced and
the limitation is recorded.

#### Populating the existing OSeMOSYS reserve-margin sets

The native sets should still be populated so that otoole, diagnostics, and
downstream viewers see a conventional reserve-margin block. They are
**subsumed** by the UDC:

| File | Rows | Purpose |
|---|---|---|
| `ReserveMargin.csv` | `CAN, y, 1.0` for every year | Native RM3 remains at 1.0; it is weaker than the UDC and must not be the policy-grade enforcement. |
| `ReserveMarginTagFuel.csv` | `CAN, ELC<z>02, y, 1.0` for all 13 zones and all years | Tags grid-bus demand fuels, not generator fuels. |
| `ReserveMarginTagTechnology.csv` | `CAN, PWR<arch><z>, y, credit` for all 130 generator technologies and all years | Encodes the capacity credit. Omit non-generators; `otoole` default 0 is correct. |
| `UDC` rows | `RM_NATIONAL`, `UDCTag=0`, `UDCConstant=0`; capacity multipliers `C2A*credit`; activity multipliers `-RM*peak_factor` on each `PWRTRN<z>` | The binding national constraint. |

With `ReserveMargin=1.0`, native RM3 checks `grid demand_z <= credited
capacity`. The UDC checks `RM * peak_factor * gross generation <= credited
capacity`, with `RM*peak_factor > 1`; the UDC implies the native check. Native
RM3 therefore cannot add an unexpected constraint, but it also cannot be used
as evidence of policy-grade reserve adequacy on its own.

#### Capacity-credit assumptions (draft, pending source)

| Archetype | Draft credit | Suggested range | Rationale |
|---|---:|---:|---|
| `NGS` | 0.95 | 0.90–1.00 | Dispatchable; small forced-outage derate. |
| `COA` | 0.90 | 0.85–0.95 | Dispatchable; phase-out applies separately. |
| `URN` | 0.90 | 0.85–0.95 | High availability; planned outages. |
| `HYD` | 0.65 | 0.55–0.75 | Energy-limited / winter derate; not firm at nameplate. |
| `BIO`, `BSW`, `BCW` | 0.85 | 0.80–0.90 | Dispatchable, fuel-limited. |
| `WND` | 0.20 | 0.15–0.25 | Effective load-carrying capability, not capacity factor. |
| `SOL` | 0.05 | 0.00–0.10 | Low ELCC at system peak in most Canadian systems. |
| `GEO` | 0.80 | 0.75–0.85 | Dispatchable geothermal. |
| `TRN`, `PWRTRN` | 0.00 | 0.00 | No firm import contract; transmission is not generation. |
| Storage / demand response | not modelled | — | No storage sets, no DR technology. |

`PWRBIO`, `PWRBCW`, and `PWRBSW` inherit the biomass credit. The credit is a
fraction of nameplate capacity, independent of `CapacityFactor`; both are used
in different constraints and must not be conflated.

#### Draft reserve-margin schedule

```text
RM:          2021: 1.00, 2025: 1.00, 2026: 1.15, 2070: 1.15
peak_factor: 2021: 1.55, 2070: 1.55
```

- The repeated `2025`/`2026` keys make this a step, not a ramp: schedules
  interpolate linearly between keys, so a single `2021: 1.00, 2026: 1.15`
  would ramp through 2021–2025.
- `2021–2025` at 1.00 avoids retroactively declaring the observed fleet
  inadequate before the first policy year.
- `1.15` is a conventional planning reserve margin placeholder (NERC-style
  15%); it is not a Canadian legal value.
- `1.55` corresponds to a load factor of about 0.645. It is a placeholder
  until provincial peak data land. A national average hides winter-peaking
  versus summer-peaking provinces.

#### Why `peak_factor` is a hard gate

Without timeslices, `RM` alone compares capacity with annual energy, which is
far weaker than a peak standard. For example, `RM=1.15` on annual average load
would require only about 1.15 × average GW, not 1.15 × peak GW. The
`peak_factor` is therefore part of the policy definition, not a cosmetic
tuning knob. If it is missing or unsourced, `policy_grade` must remain false.

### 2.2 Annual build-rate and capacity caps

`TotalAnnualMaxCapacityInvestment` is the maximum **new capacity in that
year** (GW/yr), not cumulative capacity. `TotalAnnualMaxCapacity` is the
maximum **total installed capacity** (GW) and must be at least
`ResidualCapacity`; the model has an explicit check that fails otherwise.
Both are already active and need no structural change.

Draft build-rate table (provisional; replace with researched values):

| Technology family | `TotalAnnualMaxCapacityInvestment` (GW/yr) | Notes |
|---|---:|---|
| `PWRWND<z>` | 2.0 (2021–2030), 3.0 (2031–2050), 2.0 (2051–2070) | National envelope split across zones; zone allocation is a separate siting choice. |
| `PWRSOL<z>` | 1.5 (2021–2030), 3.0 (2031–2050), 2.0 (2051–2070) | No defensible total GW cap in current data; build rate is the explicit control. |
| `PWRURN<z>` | 0.0 (2021–2030), 0.30 (2031–2070) | New-build rate only. `no_new_nuclear` means 0 GW/yr new build, not a 0 GW total cap; the observed residual fleet must be preserved. A 1 GW class unit every ~5 years is ~0.2 GW/yr average. |
| `PWRBIO<z>`, `PWRBCW<z>`, `PWRBSW<z>` | 0.15 each | Biomass supply and boiler lead times are the binding constraints. |
| `PWRHYD<z>` | 0.25 | Large-hydro construction is slow; 1 GW over 4 years is already aggressive. |
| `PWRGEO<z>` | 0.10 | Small market. |
| `PWRNGS<z>` | 1.0 | If new gas is allowed; policy case may set 0. |
| `PWRCOA<z>` | 0.0 | Federal coal phase-out; residual capacity retires separately. |
| `TRN<from><to>` | 0.0 or link-specific | Stage 1 can cap existing links; new corridors need Stage 2. |

Implementation notes:

- Write one row per technology **and year** for every capped technology. Do
  not rely on `-1` meaning “no cap” for a technology that is meant to be
  capped; that would be a silent fail-open.
- Apply the cap to `NewCapacity`, not to `TotalCapacityAnnual`. A technology
  can have a large existing fleet and a zero build rate.
- `TotalAnnualMaxCapacity` must be checked against `ResidualCapacity` before
  the solve. A cap below the observed stock is an input error, not an
  infeasibility to discover in GLPK.
- If a technology is meant to be unbounded, omit the row and document the
  omission in `notes`. Do not write a huge sentinel value; it hides mistakes.

### 2.3 Explicit caps by technology family

| Family | Instrument | Draft policy-test treatment | Status |
|---|---|---|---|
| Wind (`PWRWND`) | `TotalTechnologyAnnualActivityUpperLimit` | 20% of each zone’s 2050 reference generation, as in `screening_v0`; ceiling is a scenario assumption, not policy. | Existing canonical reference. |
| Solar (`PWRSOL`) | `TotalAnnualMaxCapacityInvestment` only | No total GW cap: no defensible Canadian siting/resource ceiling in current data. Build rate is the explicit control. | Data dependency. |
| Nuclear (`PWRURN`) | `TotalAnnualMaxCapacityInvestment` (+ optional total cap) | Two modes: `no_new_nuclear` (0 GW/yr new build, observed residual preserved) or `build_rate_only` (0.30 GW/yr, no total cap). Mode is a scenario choice, not “policy” by itself. | Data dependency. |
| Bio power (`PWRBIO`) | `TotalTechnologyAnnualActivityUpperLimit` | 5% of each zone’s 2050 reference generation; 162.608451 PJ/yr total in the canonical reference. Scenario assumption, not policy. | Existing canonical reference. |
| Woody residue (`PWRBCW`, `SUPBCWB1/2`) | Activity caps | Zonal BC caps: 43.523816 PJ/yr from 2028 (`SUPBCWB1` + `SUPBCWB2`). Supply potential, not policy. | Existing canonical reference. |
| Hydro (`PWRHYD`) | Activity caps + build rate | `cap_2021` upper bounds per zone from canonical; 0.25 GW/yr build; credit 0.65. `fixed_2021` fixes both upper and lower bounds and is a scenario mode. | Existing reference plus draft build rate. |
| Switchgrass (`PWRBSW`) | Supply cap + build rate | Supply cap from WS3/FRESH; 0.15 GW/yr build. | Data dependency. |
| Coal (`PWRCOA`) | Zero new build + residual retirement | 0 GW/yr build; residual -> 0 by 2030 (or province schedule). Do not set a 0 GW total cap while residual coal remains. | Baseline policy. |
| Gas (`PWRNGS`) | Build rate | 1.0 GW/yr, no total cap; can be set to 0 in a no-new-gas scenario. | Draft. |
| Transmission (`TRN`) | Link `TotalAnnualMaxCapacity` | Cap individual links at researched ratings; no network constraint. | Stage 1 partial / Stage 2 complete. |

Important: the 5% PWRBIO cap, 20% wind cap, 0 GW nuclear cap in
`screening_v0`, and +$3/GJ wind adder inherited from `screening_v0` are
**scenario assumptions**, not policy. `policy_test_v1` must keep that
distinction in `notes` and in the manifest.

### 2.4 Cost and performance overrides

The current `ARCH` table in `build_params.py` is placeholder engineering data.
A policy test can override it without structural change, but only if each
override is sourced. Draft direction (values to be replaced by research):

| Archetype | Parameter | Current placeholder | Draft policy-test | Why |
|---|---|---:|---:|---|
| `NGS` | efficiency (IAR) | 2.70 (37.0%) | 1.85 (54.1%) | Combined-cycle, not single-cycle. |
| `NGS` | capex / fixed / var | 1100 / 25 / 3.0 | 1200 / 25 / 3.0 | 2020 CAD/kW, $/kW-yr, $/GJ. |
| `COA` | efficiency (IAR) | 2.90 (34.5%) | phase-out | No new coal; residual retires. |
| `HYD` | capacity factor | 0.45 | 0.50 | Regional hydro average; still a placeholder. |
| `WND` | capex / CF | 1600 / 0.35 | 1700 / 0.38 | Canadian onshore averages. |
| `SOL` | capex / CF | 1200 / 0.16 | 1100 / 0.18 | Utility PV. |
| `BIO` | IAR / CF | 2.85 / 0.70 | 2.85 / 0.70 | Keep until WS3 moisture data. |
| `BSW`, `BCW` | IAR / CF | 3.72 / 0.70 | 3.20 / 0.70 | Dedicated biomass conversion. |
| `URN` | capex / CF / life | 6000 / 0.90 / 50 | 6000 / 0.90 / 50 | Keep. |
| `GEO` | capex / CF | 4500 / 0.80 | 4500 / 0.80 | Keep. |
| Transmission | line capex / loss | 500 $/kW / 5% | length-dependent | Stage 2; current value is length-blind. |
| Distribution | IAR | 1.11 | 1.11 | Keep until provincial T&D losses are sourced. |

The override block must also record `co2` factors and the output-basis
conversion (`EmissionActivityRatio = fuel factor × IAR`). The canonical
`screening_v0` correction (gas 0.1377, coal 0.261 Mt/PJ output) must remain in
force; a policy set must not reintroduce the raw 0.051/0.090 fuel-basis rows.

---

## 3. Requires structural changes

### 3.1 Transmission and interchange

What can be done now:

- Cap an existing `TRN<from><to>` technology with `TotalAnnualMaxCapacity`
  (GW) and a build rate with `TotalAnnualMaxCapacityInvestment`.
- Keep the 5% loss and 500 $/kW placeholder, but mark them as placeholders.

What cannot be done without structural change:

- **Simultaneous import/export and net interchange.** The model has one
  `REGION`; `Trade` is disabled. A pair of directed `TRN` technologies can
  both carry flow, but there is no “net position” or “import at most X while
  exporting Y” constraint.
- **Zonal/nodal network.** No DC power flow, loop flow, voltage/reactive
  limits, contingency constraints, or deliverability.
- **Asymmetric link ratings.** `TransmissionLinks` is a single GW tuple per
  pair; `build_params` writes the same value to both directions. Real
  interties are often asymmetric.
- **Firm vs non-firm capacity.** No contract or reliability class.
- **New corridors.** The link list is hard-coded in `model_structure.py`;
  adding a corridor is a structure/data change, not a parameter row.
- **Provincial reserve sharing.** A provincial reserve margin would need
  separate regions or zonal capacity accounting; the UDC capacity sum is
  national.

Stage 2 must replace the hard-coded symmetric link list with a data-driven,
directional link table, add explicit flow/limit variables if a network is
required, and decide whether multi-region or zonal UDCs are the right
abstraction. Until then, `structural_gaps` includes `transmission_network`.

### 3.2 Curtailment and timeslices

Current model: `TIMESLICE=ANN`, `YearSplit=1.0`, one `MODE_OF_OPERATION`.
`RateOfActivity` is non-negative, so a VRE plant can choose not to generate,
but there is no explicit curtailment accounting, no hourly/seasonal
correlation, no storage, and no reserve margin at peak.

Structural work:

- Add `TIMESLICE`, `SEASON`, `DAYTYPE`, `DAILYTIMEBRACKET`; populate
  `Conversionls`, `Conversionld`, `Conversionlh`, `YearSplit`, `DaySplit`,
  `DaysInDayType`.
- Add per-slice `CapacityFactor` and `SpecifiedDemandProfile`.
- Add a curtailment sink or explicit curtailment variable so spilled VRE is
  measured rather than disappearing.
- Add storage sets if storage is in scope (currently `Limitations` says
  storage sets are not built).
- Rebuild diagnostics and result extraction for multi-slice activity.
- Watch LP size: 13 zones × 50 years × 48+ slices is much larger than the
  current annual LP; GLPK may need a larger solver or a reduced slice set.

Until then, `structural_gaps` includes `timeslices_curtailment`, and the
reserve margin remains an annual proxy.

### 3.3 Unit commitment and ramping

Current model is a linear program with continuous activity, no commitment
binaries, no minimum stable generation, no startup/shutdown costs, no ramp
rates, no minimum up/down times, and no spinning reserve. `NumberOfNewTechnologyUnits`
is an integer variable but is not used to build a commitment formulation.

Structural work:

- Binary `Commit` variables per technology, slice, and year.
- Minimum stable generation and maximum ramp-up/ramp-down.
- Startup/shutdown costs and minimum up/down times.
- Spinning/operating reserve constraints, co-optimised with the reserve
  margin.
- A MILP-capable solver and a tractable temporal reduction (representative
  days/weeks), because a full 8,760-hour MILP over 50 years is not practical
  in this workflow.

Until then, `structural_gaps` includes `unit_commitment_ramping`.

---

## 4. Proposed `policy_test_v1` JSON schema

Use a new schema version because the existing loader is deliberately strict
and `screening_v0` must remain untouched. Proposed:
`schema_version = "clews.constraint_set.v2"`.

### 4.1 Top level

| Field | Type | Rule |
|---|---|---|
| `schema_version` | string | Exactly `clews.constraint_set.v2`. |
| `set_id` | string | Exactly `policy_test_v1`. |
| `label` | string | Human label; non-empty. |
| `status` | enum | `draft`, `policy_test`, `policy_grade`. |
| `policy_grade` | boolean | Must be `false` unless every gate in §5 passes. |
| `policy_grade_reason` | string | Required when `policy_grade=false`; names the first failing gate. |
| `structural_gaps` | array | Any of `transmission_network`, `timeslices_curtailment`, `unit_commitment_ramping`. Must be empty for policy grade. |
| `required_blocks` | array | At minimum: `reserve_margin`, `capacity_credits`, `build_rates`, `tech_caps`, `hydro`, `carbon`. |
| `reserve_margin` | object | §4.2. |
| `capacity_credits` | object | §4.3. |
| `build_rates` | object | §4.4. |
| `tech_caps` | object | §4.5. |
| `hydro` | object | §4.6. |
| `carbon` | object | §4.7. |
| `parameter_overrides` | object | §4.8; optional for `draft`, required for policy grade if any non-default value is used. |
| `sources` | array | §4.9. |
| `notes` | array | Non-empty; must state what is omitted and why. |

### 4.2 `reserve_margin`

```json
{
  "method": "udc_national_peak_proxy",
  "enforcement": "udc",
  "schedule": {"2021": 1.0, "2025": 1.0, "2026": 1.15, "2070": 1.15},
  "peak_factor": {"2021": 1.55, "2070": 1.55},
  "native_rm3": {
    "enabled": true,
    "reserve_margin": 1.0,
    "tag_fuels": "ELC<z>02",
    "authoritative": false
  },
  "source_status": "pending",
  "source_ids": []
}
```

Rules: every schedule key is an integer year in `[2021,2070]`; values are
finite and `>= 0`; `peak_factor >= 1`; `method` is `udc_national_peak_proxy`
until a timeslice model exists; `native_rm3.authoritative` must be `false`
unless the native RM3 aggregation problem is fixed. Schedules interpolate
linearly; use repeated boundary keys for a step.

### 4.3 `capacity_credits`

```json
{
  "basis": "fraction_of_nameplate",
  "default": 0.0,
  "by_archetype": {
    "NGS": 0.95, "COA": 0.90, "URN": 0.90, "HYD": 0.65,
    "BIO": 0.85, "BSW": 0.85, "BCW": 0.85, "WND": 0.20,
    "SOL": 0.05, "GEO": 0.80
  },
  "by_technology": {},
  "source_status": "pending",
  "source_ids": []
}
```

Rules: every credit is in `[0,1]`; `by_technology` overrides
`by_archetype`; every technology that can be built and is meant to contribute
must match an archetype or an explicit override. A technology value may be a
scalar or a year schedule using the same interpolation rules as other
schedules. Unlisted technologies get `default` (0), never an implicit 1.0.

### 4.4 `build_rates`

```json
{
  "parameter": "TotalAnnualMaxCapacityInvestment",
  "unit": "GW/yr",
  "by_technology": {
    "PWRWND<z>": {"2021": 2.0, "2030": 2.0, "2031": 3.0, "2050": 3.0, "2051": 2.0, "2070": 2.0},
    "PWRSOL<z>": {"2021": 1.5, "2030": 1.5, "2031": 3.0, "2050": 3.0, "2051": 2.0, "2070": 2.0},
    "PWRURN<z>": {"2021": 0.0, "2030": 0.0, "2031": 0.3, "2070": 0.3},
    "PWRBIO<z>": {"2021": 0.15, "2070": 0.15},
    "PWRBCW<z>": {"2021": 0.15, "2070": 0.15},
    "PWRBSW<z>": {"2021": 0.15, "2070": 0.15},
    "PWRHYD<z>": {"2021": 0.25, "2070": 0.25}
  },
  "source_status": "pending",
  "source_ids": []
}
```

Rules: each `<z>` pattern expands to all 13 zones; a pattern matching zero
technologies is an error; schedules must cover 2021–2070 or explicitly
declare a hold rule; values `>= 0`. Schedules interpolate linearly; the
repeated boundary keys above turn the 2.0/3.0/2.0 wind envelope into steps.

### 4.5 `tech_caps`

```json
{
  "by_technology": {
    "PWRWNDA1": {
      "metric": "activity_pj",
      "parameter": "TotalTechnologyAnnualActivityUpperLimit",
      "schedule": {"2021": 26.882148, "2070": 26.882148},
      "note": "20% of zonal 2050 reference generation; scenario assumption."
    },
    "PWRBIOA1": {
      "metric": "activity_pj",
      "parameter": "TotalTechnologyAnnualActivityUpperLimit",
      "schedule": {"2021": 16.657653, "2070": 16.657653},
      "note": "5% of zonal 2050 reference generation; scenario assumption."
    },
    "PWRSOLA1": {
      "metric": "build_rate_only",
      "parameter": "TotalAnnualMaxCapacityInvestment",
      "schedule": null,
      "note": "No total GW cap; build rate is the explicit control."
    },
    "PWRURNO1": {
      "metric": "build_rate_only",
      "parameter": "TotalAnnualMaxCapacityInvestment",
      "schedule": null,
      "note": "No total cap; 0 GW/yr in no_new_nuclear, 0.30 GW/yr otherwise."
    },
    "PWRHYDQ1": {
      "metric": "activity_pj",
      "parameter": "TotalTechnologyAnnualActivityUpperLimit",
      "schedule": {"2021": 649.900247, "2070": 649.900247},
      "note": "cap_2021 mode; all 13 zones carry their own value."
    }
  },
  "source_status": "pending",
  "source_ids": []
}
```

Rules: `metric` is one of `activity_pj`, `capacity_gw`, `build_rate_only`;
`capacity_gw` schedules must be `>= ResidualCapacity`; `activity_pj` values
must be `>= 0`; `build_rate_only` still needs a `build_rates` entry. The actual
zonal values are generated from the canonical reference and the research
source register, not hand-typed here. The exact-code entries above are
illustrative; production sets expand `<z>` patterns or list every zone.

### 4.6 `hydro`

```json
{
  "mode": "cap_2021",
  "activity_caps_pj": {
    "PWRHYDA1": 197.204587,
    "PWRHYDA2": 106.187162,
    "...": "all 13 zones"
  },
  "build_rate_gw_per_year": {"2021": 0.25, "2070": 0.25},
  "capacity_credit": 0.65,
  "source_status": "pending",
  "source_ids": []
}
```

`mode` is `none`, `cap_2021`, or `fixed_2021`. `cap_2021` writes only the
upper bound; `fixed_2021` writes the same value to the lower and upper bound
and disables new hydro. The `hydro` block must agree with
`tech_caps.PWRHYD<z>`; a mismatch is a schema error.

### 4.7 `carbon`

```json
{
  "price_schedule_usd_per_tco2e": [
    {"year": 2021, "value": 95.0},
    {"year": 2026, "value": 115.0}
  ],
  "national_annual_cap_mt": null,
  "provincial_annual_caps_mt": {},
  "intensity_cap_t_per_gwh": [
    {"year": 2035, "value": 65.0},
    {"year": 2050, "value": 0.0}
  ],
  "source_status": "pending",
  "source_ids": []
}
```

Rules: prices and caps are finite and `>= 0`; intensity caps are in
`t CO2/GWh` and are converted to a UDC over power-plant emissions and
generation; `null` means “not applied”, not “zero”. A carbon price alone is
not a carbon cap; `policy_grade` requires the instrument that the policy
actually uses to be present. Provincial caps use the existing UDC mechanism.

### 4.8 `parameter_overrides`

Optional but policy-grade if used. Shape:

```json
{
  "by_archetype": {
    "NGS": {
      "capex_usd_per_kw": 1200,
      "fixed_usd_per_kw_yr": 25,
      "variable_usd_per_gj": 3.0,
      "life_years": 30,
      "capacity_factor": 0.85,
      "input_activity_ratio": 1.85,
      "co2_fuel_basis_mt_per_pj": 0.051
    }
  },
  "source_status": "pending",
  "source_ids": []
}
```

Rules: the output-basis emission factor is computed, not hand-entered;
`input_activity_ratio` must be `> 0`; `capacity_factor` in `(0,1]`; costs
`>= 0`; `life_years` integer `>= 1`. Unknown keys are rejected.

### 4.9 `sources`

Each source record:

```json
{
  "id": "cer_ef2026_capacity",
  "publisher": "Canada Energy Regulator",
  "title": "Electricity capacity, 2026",
  "url": "https://...",
  "as_of": "2026-11-20",
  "licence": "Open Government Licence - Canada",
  "sha256": "<file hash>",
  "path": "data/external/...",
  "status": "sourced"
}
```

`status` is `sourced`, `pending`, or `rejected`. A `pending` source cannot
support a policy-grade value.

---

## 5. Data dependencies and fail-closed behaviour

### 5.1 Required data from the external research task

The research task must return a source register keyed to the fields below.
No block may be filled with an unsourced “reasonable” number.

| Block | Required data | Minimum granularity | Likely sources |
|---|---|---|---|
| `reserve_margin` | Planning reserve margin; peak/average load factor; capacity credits / ELCC by resource class | National and, if possible, provincial; year schedule | NERC Long-Term Reliability Assessment, NPCC/WECC, AESO, IESO, BC Hydro, Hydro-Québec, provincial IRPs |
| `capacity_credits` | ELCC or accredited capacity by technology; treatment of hydro, wind, solar, biomass, nuclear, interties | Archetype, with provincial overrides where they exist | NERC/utility ELCC studies, provincial reliability reports |
| `build_rates` | Maximum annual additions by technology; historical peak-year additions; interconnection queue and manufacturing constraints | Technology × year | CER EF2026 capacity series, CanREA, provincial interconnection queues, NRCan, utility IRPs |
| `tech_caps` | Siting/resource ceilings; committed projects; phase-outs; hydro 2021 output; biomass zonal supply | Technology × zone × year | Provincial land-use plans, utility IRPs, WS3/FRESH, CER, NRCan |
| `hydro` | 2021 output caps; capacity; build rate; firm/energy-limited credit | Zone × year | BC Hydro IRP, Hydro-Québec, OPG, Manitoba Hydro, CER |
| `carbon` | Carbon price schedule; national/provincial caps; CER intensity standard | Year; province where applicable | ECCC OBPS, CER regulations, provincial carbon-pricing authorities |
| `parameter_overrides` | Capex, fixed O&M, variable O&M, life, CF, efficiency, CO2 factor | Archetype; regional where material | NREL ATB, IEA, ECCC NIR, NRCan, utility IRPs |
| `transmission` (Stage 2) | Directional link ratings, lengths, losses, costs, limits | Link × year | CER electricity interchange 2026, provincial transmission plans, NERC/NPCC |
| `timeslices` (Stage 3) | Load profiles, VRE profiles, storage parameters, curtailment rules | Slice × zone × year | System operators, NRCan, utility data |

The checked model copy currently has zero generator `ResidualCapacity`; the
observed 2021 fleet is also a data dependency. Do not run a policy-grade
reserve margin on a zero-residual placeholder fleet.

### 5.2 Fail-closed rules

1. **No silent fallback.** A missing `policy_test_v1` block must fail the
   loader; it must not fall back to `screening_v0` defaults.
2. **No unsourced policy-grade values.** If any required block has
   `source_status != "sourced"`, force `policy_grade=false` and put the first
   failure in `policy_grade_reason`. If a caller tries to set
   `policy_grade=true` while a block is pending, the loader must raise.
3. **No nulls in required numeric fields.** `null` is allowed only where the
   schema explicitly says “not applied” (for example `national_annual_cap_mt`).
4. **No unmatched patterns.** A technology pattern that expands to zero
   technologies is an error, not a no-op.
5. **No cap below the observed fleet.** Preflight
   `TotalAnnualMaxCapacity >= ResidualCapacity` for every capped technology
   and year; fail with the technology and year in the message.
6. **No negative or non-finite values.** Reject before writing CSVs and again
   on load.
7. **No incomplete year coverage.** Every schedule must cover 2021–2070 after
   interpolation; a missing first/last year is an error unless a documented
   hold rule is present.
8. **No hidden native RM3.** If `native_rm3.enabled` is true, the UDC
   `RM_NATIONAL` must also be present and its multipliers must be non-empty;
   otherwise fail. Native RM3 alone is not policy-grade.
9. **No label laundering.** The runner must derive labels from the parsed set,
   not from a hard-coded constant. A `policy_test_v1` run cannot inherit
   `screening_only`; a `screening_v0` run cannot inherit `policy_grade`.
10. **No solver masking.** If the reserve margin or caps make the model
    infeasible, report the failing constraint and year; do not relax the cap.

---

## 6. Acceptance criteria and tests

### 6.1 Reserve margin binds

- Build a “tight capacity” case: disable or sharply limit new build, keep
  demand at the reference level, solve with and without `RM_NATIONAL`.
- Check, for at least one year, that
  `sum(C2A * credit * TotalCapacityAnnual) ≈ RM * peak_factor * gross_generation`
  within a small tolerance (for example 1e-3 relative).
- Check that the constrained objective is greater than the unconstrained
  objective and that total generation is unchanged or served at higher cost.
- Check that `RM_NATIONAL` UDC rows exist for every year, with
  `UDCMultiplierTotalCapacity` on generators and `UDCMultiplierActivity` on
  all `PWRTRN<z>`.
- Negative test: remove `RM_NATIONAL` but keep `ReserveMargin>1` and tags;
  the loader must reject the set as non-authoritative.

### 6.2 Build rate binds

- Choose a technology and year where the unconstrained optimal
  `NewCapacity` exceeds the draft cap (for example wind at 2030).
- Solve the capped case; assert `NewCapacity[t,y] <= cap + 1e-6` for every
  year and that at least one year is at the cap.
- Assert the objective is non-decreasing relative to the uncapped case.
- Repeat for a second family (solar or nuclear) to prove the writer is not
  applying the cap to only one technology.

### 6.3 Explicit caps bind

For each of `PWRWND`, `PWRBIO`, `SUPBCWB1/2`, `PWRHYD`, and `PWRURN`:

- Run a stress case that makes the unconstrained quantity exceed the cap
  (high carbon price, low cost, or a deliberately tightened cap).
- Assert activity or capacity is at or below the cap and that at least one
  year is at the cap.
- Assert the cap is not silently ignored because the technology code pattern
  expanded to nothing.
- For `PWRURN` test both modes: `no_new_nuclear` (0 GW/yr new build,
  observed residual preserved) and `build_rate_only` (no total cap).

### 6.4 No negative or NaN values

Scan generated parameters and results:

- Every numeric CSV value is finite and `>= 0`.
- `ReserveMarginTagTechnology` and `ReserveMarginTagFuel` are in `[0,1]`.
- `TotalAnnualMaxCapacity >= ResidualCapacity` for every row.
- `TotalAnnualMaxCapacityInvestment >= 0` for every row.
- `YearSplit` sums to 1.0 within 1e-9.
- Schedule keys are unique, in `[2021,2070]`, and monotonic.
- No duplicate `(REGION,TECHNOLOGY,YEAR)` rows; no technology/year gaps
  inside a capped schedule.
- Result CSVs: no negative generation, capacity, emissions, or activity; no
  NaN/inf; emissions accounting closes.

### 6.5 Labels and gates

- `screening_v0` still loads, still says `screening_only`, still
  `policy_grade=false`; its notes still mention reserve-margin, transmission,
  curtailment, and build-rate.
- `policy_test_v1` with a missing `reserve_margin` block fails to load.
- `policy_test_v1` with `reserve_margin.source_status="pending"` loads only
  as `draft` / `policy_test`; `policy_grade` is false with a reason.
- A payload that sets `policy_grade=true` while any required block is pending
  is rejected.
- A payload with all blocks sourced and `structural_gaps=[]` may set
  `policy_grade=true`; the runner summary and manifest must echo
  `status="policy_grade"`.
- The UI `/api/constraints` response and run badges must show the parsed
  status, not a hard-coded `screening_only`.
- Negative UI test: a policy run is never displayed as
  “publishable/post-fix”; a screening run is never displayed as
  “policy-grade”.

### 6.6 Regression and evidence

- `screening_v0` regression: same scenario, same objective and emissions as
  before the policy code is added (within solver tolerance).
- `policy_test_v1` dry run: `osecan harmonize` passes; all required CSVs are
  present; no solve required in the fast test lane.
- One real `policy_test_v1` solve is run manually and recorded with the same
  evidence bundle as `screening_v0` (`status.json`, solver log, parameter
  hashes, model tree hash).
- Constraint identity: a base-vs-policy diff must show at least one binding
  constraint. If every added constraint is slack, the test fails with
  “constraints did not bind”; a slack policy set is not evidence.
- Infeasibility test: set RM absurdly high; the solve must fail with a clear
  diagnostic, not hang or silently drop the constraint.

---

## 7. Staged implementation plan

### Stage 1 — reserve margin, caps, build rates (no structural change)

1. Add `data/derived/national/clews/osecan/policy_test_v1.json` using the
   schema in §4. Start with `status="draft"`, `policy_grade=false`.
2. Add a strict loader for `clews.constraint_set.v2`; keep
   `load_canonical_set` for `screening_v0` unchanged.
3. Extend `workflows/ui/osecan_runner.py` to:
   - accept `set_id=policy_test_v1` without weakening the screening gate;
   - write `ReserveMargin`, `ReserveMarginTagFuel`,
     `ReserveMarginTagTechnology`, and `UDC` `RM_NATIONAL` rows;
   - write `TotalAnnualMaxCapacityInvestment` and
     `TotalAnnualMaxCapacity` rows from `build_rates` / `tech_caps`;
   - write hydro, carbon, and parameter overrides;
   - preflight cap-vs-residual and schedule coverage;
   - propagate `status`/`policy_grade` from the parsed set.
4. Add tests from §6.1–§6.5 (fast, no full solve where possible; one manual
   solve for evidence).
5. Run a base-vs-policy comparison on the reference harvest case; record
   objective, emissions, capacity, and binding years.
6. Update UI labels and `/api/constraints` defaults; keep `screening_v0` as
   the default set.
7. Update `canonical_constraint_set.json` `future_sets.policy_test_v1` to
   point at the new file and status, without relabelling `screening_v0`.

**Stage 1 exit criteria:** schema validates; dry run harmonizes; all
Stage-1 constraints are present; at least reserve margin and one build rate
bind in a real solve; `screening_v0` regression passes; `policy_grade` is
still false.

### Stage 2 — transmission and interchange

1. Replace the hard-coded `TransmissionLinks` tuple list with a directional,
   data-driven link table (from, to, rating, cost, loss, year).
2. Add explicit link caps and, if required, flow/limit variables or a
   multi-region representation.
3. Decide whether provincial reserve margins need separate regions or zonal
   capacity accounting; if yes, this is a larger structural change.
4. Add tests: link cap binds, flow conservation, loss accounting, islanded
   zone (N1) behaviour, no unserved load.
5. Remove `transmission_network` from `structural_gaps` only after the network
   representation is tested.

### Stage 3 — timeslices, curtailment, unit commitment

1. Add a reduced timeslice set (for example 4 seasons × 2 day types × 6
   blocks = 48 slices) with `Conversionls/ld/lh`, `YearSplit`, `DaySplit`,
   `DaysInDayType`, and per-slice demand/CF profiles.
2. Add a curtailment sink or variable and report curtailed energy by zone and
   year.
3. Replace the annual peak proxy with a peak-slice reserve margin.
4. If required, add MILP unit commitment/ramping: binaries, min stable
   generation, ramp limits, startup/shutdown, reserve. Benchmark solver time
   before committing to a full 8,760-hour formulation.
5. Remove `timeslices_curtailment` and `unit_commitment_ramping` from
   `structural_gaps` only after the tests pass.
6. Only then may a fully sourced set set `policy_grade=true`.

---

## 8. Open questions

1. **What is `policy_test_v1` testing?** A current-law baseline, a stated
   policy package (carbon price + CER + provincial targets), or a stress
   test? The answer changes whether new nuclear is allowed, whether gas can
   be built, and which carbon instrument is required.
2. **National or provincial reserve margin?** The current single-region
   structure supports national only. A provincial standard needs Stage 2 or a
   zonal capacity accounting method.
3. **Capacity credits:** should interties contribute firm capacity? If yes,
   the credit must be tied to link ratings and firm contracts, which is a
   Stage 2 dependency.
4. **Hydro credit:** 0.65 is a placeholder. Is hydro treated as energy-limited
   (low ELCC) or firm (high ELCC)? This materially changes the reserve margin.
5. **Biomass caps:** the 5% PWRBIO and BCW caps are supply/scenario
   assumptions, not policy. Should they be in `tech_caps` (model envelope) or
   in a separate `supply_assumptions` block?
6. **Schema version:** is `clews.constraint_set.v2` acceptable, or should the
   new blocks be optional extensions of v1? This design recommends v2 to keep
   the screening loader strict.
7. **Data ownership:** who signs off the external research register, and what
   is the review SLA before a value can be marked `sourced`?
8. **Solver:** is GLPK acceptable for Stage 2/3, or should the project plan
   for CBC/Gurobi before timeslices and MILP?
9. **Post-2050:** most policy data stop at 2050. What is the documented
   extrapolation rule for 2051–2070? The current model holds the last
   schedule value; that must be explicit in the policy set.
10. **Observed fleet:** when will the CER 2021 generator residual capacity be
    materialised in `runtime/osecan_model`? Until then, reserve margin results
    are not interpretable.

---

## 9. Non-claims

- This document does **not** define final policy values. The numbers marked
  “draft” are placeholders to make the schema and tests concrete.
- `policy_test_v1` is **not** policy-grade in Stage 1. It has no network, no
  timeslices, no curtailment, and no unit commitment.
- Do not relabel `screening_v0` as `policy_test_v1` or as policy-grade.
- Do not publish a `policy_test_v1` result with `policy_grade=true` until
  every required block is sourced, the structural gaps are closed, and the
  acceptance tests in §6 pass.
