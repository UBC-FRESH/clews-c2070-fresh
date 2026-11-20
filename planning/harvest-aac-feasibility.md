# Harvest AAC / wood-supply feasibility for the C2070 fleet ladder

| Field | Value |
|---|---|
| Document | `planning/harvest-aac-feasibility.md` |
| Date | 2026-11-20 |
| Status | Feasibility screen implemented 2026-11-20: runner gate at 150,000,000 m3/yr, `--allow-infeasible` diagnostic override, feasibility labels, and Pareto exclusion for the exogenous ladder. Provincial harvest-allocation LP implemented 2026-11-20 as a screening milestone: least-deviation allocation under documented ceilings is feasible at 100/119/150 Mm3/yr and infeasible at 200 Mm3/yr by 30.48 Mm3. THLB, the harvest cost curve, and the endogenous forest-sector LP remain later milestones. |
| Scope | 100 / 119 / 150 / 200 Mm3/yr national harvest ladder |
| Trigger | The fleet-corrected ladder shows 119 Mm3 dominated by 150 / 200 Mm3; harvest is exogenous and unpriced in the energy objective |
| Recommendation | Cap the exposed ladder at 150M; reject 200M; treat harvest as exogenous and exclude the ladder from the energy Pareto; defer the cost curve and forest-sector LP |

## 1. Finding

The fleet-corrected ladder is internally consistent but is not a feasible harvest frontier:

| Case | Harvest (m3/yr) | Objective (M$) | Cumulative emissions (Mt CO2) | 2050 emissions (Mt) | Feasibility (this note) | Dominated by |
|---|---:|---:|---:|---:|---|---|
| 100M | 100,000,000 | 1,447,160.18 | 423.050600 | 0.0 | National AAC/NFD pass; NB/NS Crown-only caveats | 150M, 200M |
| 119M | 118,977,339 | 1,445,655.44 | 424.710127 | 0.0 | National pass; observed base; NB/NS Crown-only caveats | 150M, 200M |
| 150M | 150,000,000 | 1,443,635.15 | 422.690612 | 0.0 | National pass; status-quo mix fails NB NFD / BC repo AAC | 200M |
| 200M | 200,000,000 | 1,440,837.42 | 421.029794 | 0.0 | Fail: above documented AAC; fails provincial NFD | — |

All four solves returned `optimal`; the problem is the scenario definition, not solver failure. The objective falls monotonically with harvest; emissions rise slightly from 100M to 119M and then fall. The 100M/119M emissions ordering is a re-optimization effect and does not affect the dominance result; both are dominated by 150M and 200M. 200M has both the lowest objective and the lowest cumulative emissions. 150M strictly dominates 100M and 119M; 200M strictly dominates all three. The 119M canonical case is dominated by 150M by 2,020.29 M$ (0.14%) and 2.019515 Mt (0.48%), and by 200M by 4,818.02 M$ (0.33%) and 3.680333 Mt (0.87%).

This is an accounting result, not a harvest recommendation. The energy model receives harvest as an exogenous input. It pays for residue supply but not for the harvest itself, log value, silviculture, road building, or forest-sector opportunity cost. More harvest therefore means more residue at a low, largely flat supply cost, which lowers energy-system cost and displaces fossil generation. The ladder compares conditional energy outcomes; it cannot establish that 200M is a feasible or desirable forest-sector path.

The immediate question is therefore not "which harvest level wins?" but "which harvest levels are admissible at all?" This note screens the four levels against the documented provincial AAC, NFD wood-supply, and managed-landbase evidence.

## 2. Definitions and basis

| Term | Meaning in this note | Treatment |
|---|---|---|
| Actual harvest | NRCan FSD management volume, 2021–2024; 2024 = 118,977,339 m3 | Observed reference; not a sustainable-supply estimate |
| AAC | Provincial/territorial allowable annual cut, or the documented proxy | A ceiling where one exists; not a forecast |
| NFD wood supply | 2024 National Forestry Database potential supply under current management | An upper-bound potential supply; not harvest and not an AAC |
| Managed landbase | 2016 tenure-union proxy after protected-area exclusion | An area proxy, not a THLB and not an AAC |
| Harvest ladder | National log-volume cases 100 / 119 / 150 / 200 Mm3/yr | Exogenous scenario dimension |

Documented national context:

| Measure | Value (m3) | Status |
|---|---:|---|
| 2024 actual harvest | 118,977,339 | Sum of 12 reporting jurisdictions; Nunavut has no FSD row |
| AAC / proxy sum | 157,343,387 | **Partial and mixed**: 10 jurisdictions; excludes MB (range 7.39–7.99M), PE (no statutory AAC), NU (not assessed); BC is a repo-parsed TSA path, AB/SK/ON are approximate, NT is an actual-mean proxy |
| NFD wood supply | 207,680,142 | Potential supply, not harvest; NT not reported |
| 2024 harvest / NFD | 57.3% | Gap to NFD = 88,702,803 m3 |

The 157.34 Mm3 figure must not be reported as a national AAC. It is a partial sum of heterogeneous ceilings. The 207.68 Mm3 NFD figure must not be reported as a sustainable harvest. Both are used here only as screens.

### 2.1 Provincial allocation used in this note

The runner has one national harvest cap and two BC zonal caps. A screening provincial LP allocation now exists for residue-factor weighting (`harvest.allocation`), but the runner still does not enforce provincial AAC/NFD caps. The harvest-basis template contains provincial harvest values only for observed 2021–2024 and the 2024 hold; non-BC AAC is a 2024/2025 snapshot. To screen provincial ceilings, this note scales the 2024 observed provincial harvest shares to each national level:

```text
province_harvest(level) = 2024_province_harvest × level / 118,977,339
```

This is an indicative "same provincial mix" counterfactual, not a model output and not a forecast. It is the most transparent allocation available because the 2024 values are observed and sum exactly to the 119M base.

The RSYC residue table also carries `share_of_national_log`, but that is a species-resolved SCANFI **area-share apportionment of standing inventory**, not a harvest allocation. Applying it would place 59.0 Mm3 in BC, 35.8 in QC, 31.4 in ON, 25.2 in AB, and only 4.2 in NB at 200M; it would mask the NB NFD issue, soften the NS Crown-only comparison, and imply implausible harvest in YT/NT. It is not used for the feasibility screen. See `data/derived/national/clews_supply/rsyc_backbone/NATIONAL_RESIDUE_FACTOR_REPORT.md` §2 and §5.

## 3. Provincial feasibility screen

### 3.1 Documented ceilings and 2024 status

Managed proxy is the 2016 tenure-union forest area after protected-area exclusion. It is not a THLB and is missing for PE and NU. The 2024 intensity column is actual harvest / managed proxy; it is an indicator, not an allowable cut.

| Province | 2024 harvest (Mm3) | AAC 2024 (Mm3) | AAC basis | NFD 2024 (Mm3) | Managed proxy (Mha) | 2024 harvest / AAC | 2024 intensity (m3/ha/yr) |
|---|---:|---:|---|---:|---:|---:|---:|
| BC | 38.88 | 45.22 | repo-parsed TSA AAC | 63.77 | 54.37 | 0.86 | 0.72 |
| AB | 22.26 | 30.10 | Crown AAC, approximate | 31.63 | 21.79 | 0.74 | 1.02 |
| SK | 4.22 | 8.94 | Crown AAC, approximate | 8.94 | 7.49 | 0.47 | 0.56 |
| MB | 1.10 | — | documented range only (7.39–7.99) | 7.99 | 6.90 | n/a | 0.16 |
| ON | 13.12 | 28.70 | Crown AAC, approximate | 28.80 | 33.81 | 0.46 | 0.39 |
| QC | 25.94 | 34.12 | legal Crown AAC | 48.47 | 28.27 | 0.76 | 0.92 |
| NB | 9.34 | 6.23 | legal Crown AAC | 9.59 | 2.56 | 1.50 | 3.65 |
| NS | 2.32 | 1.72 | Crown AAC; excludes private 4.03 | 5.75 | 1.10 | 1.35 | 2.12 |
| NL | 1.41 | 2.01 | legal Crown AAC | 2.01 | 1.11 | 0.70 | 1.28 |
| PE | 0.29 | — | no statutory AAC | 0.46 | — | n/a | n/a |
| YT | 0.04 | 0.26 | legal Crown AAC | 0.26 | 8.07 | 0.16 | 0.005 |
| NT | 0.04 | 0.04 | 2020–2024 actual-mean proxy, not legal AAC | — | 0.82 | 1.01 | 0.045 |
| **CAN** | **118.98** | **157.34** | partial mixed sum | **207.68** | **166.28** | 0.76 | 0.72 |

Sources: `data/derived/national/harvest_basis/harvest_basis_template.csv`; `provinces.csv`; `data/derived/national/managed_landbase/managed_landbase_province_summary.csv`.

Two tenure caveats matter before reading the AAC flags:

- NB, NS, and QC report **Crown** AAC. The FSD harvest is total provincial harvest and can include private wood. A total-harvest / Crown-AAC ratio above 1 is therefore not automatically an AAC breach. NS documents private AAC of 4.03 Mm3/yr in addition to the 1.72 Crown AAC, so its combined ceiling is 5.75 Mm3/yr. NB private supply is not documented in the table.
- BC's 45.22 Mm3 is the repo-parsed 2024 TSA sum. The same path holds 43.37 Mm3 from 2028 onward, while `provinces.csv` records a separately reported 2024/2025 Crown AAC of roughly 58–60 Mm3/yr. The screen below uses 45.22 for consistency with the template and notes the alternatives where they change the conclusion. Substituting the 43.37 2028 hold for BC reduces the partial AAC sum to 155.49 Mm3; 150M remains 5.49 Mm3 below it, so the national 150M conclusion is unchanged.

### 3.2 Indicative harvest by level

Values are Mm3/yr. `AAC` / `NFD` flags mean the indicative allocation exceeds that ceiling. Flags are diagnostic, not legal determinations.

| Province | 2024 | 100M | 119M | 150M | 200M | 150M flags | 200M flags |
|---|---:|---:|---:|---:|---:|---|---|
| BC | 38.88 | 32.68 | 38.88 | 49.01 | 65.35 | AAC* | AAC*, NFD |
| AB | 22.26 | 18.71 | 22.26 | 28.06 | 37.42 | — | AAC, NFD |
| SK | 4.22 | 3.55 | 4.22 | 5.33 | 7.10 | — | — |
| MB | 1.10 | 0.93 | 1.10 | 1.39 | 1.86 | — | — |
| ON | 13.12 | 11.03 | 13.12 | 16.54 | 22.06 | — | — |
| QC | 25.94 | 21.80 | 25.94 | 32.70 | 43.60 | — | AAC† |
| NB | 9.34 | 7.85 | 9.34 | 11.78 | 15.70 | AAC†, NFD | AAC†, NFD |
| NS | 2.32 | 1.95 | 2.32 | 2.93 | 3.90 | AAC‡ | AAC‡ |
| NL | 1.41 | 1.19 | 1.41 | 1.78 | 2.38 | — | AAC, NFD |
| PE | 0.29 | 0.25 | 0.29 | 0.37 | 0.49 | — | NFD |
| YT | 0.04 | 0.04 | 0.04 | 0.05 | 0.07 | — | — |
| NT | 0.04 | 0.03 | 0.04 | 0.05 | 0.06 | AAC§ | AAC§ |
| **CAN** | **118.98** | **100.00** | **118.98** | **150.00** | **200.00** | — | **AAC¶** |

\* BC flag is against the repo-parsed 2024 TSA AAC (45.22), not the reported 58–60 Mm3 current Crown AAC and not the 43.37 Mm3 2028+ hold. See §3.3.
† NB/QC AAC is Crown-only; total harvest may include private wood. NB also exceeds NFD at 150M and 200M.
‡ NS flag is against Crown AAC only; combined Crown + private is 5.75 Mm3, so NS is under the documented combined ceiling at every level.
§ NT AAC is a 2020–2024 actual-mean proxy (36,376 m3), not a legal AAC; exceedance is 0.01 Mm3 at 150M and 0.03 Mm3 at 200M.
¶ National 200M exceeds the partial AAC sum by 42.66 Mm3 (27.1%) and is below the NFD sum by 7.68 Mm3, but fails provincial NFD in five jurisdictions.

### 3.3 Quantified findings

| Level | Result | Quantification |
|---|---|---|
| 100M | Below national ceilings; inherited tenure caveats only | 57.34 Mm3 below the partial AAC sum; 107.68 Mm3 below NFD. NB is 7.85 vs 9.59 NFD (under) but 1.62 above Crown AAC; NS is 0.23 above Crown AAC but under combined 5.75. No NFD exceedance. |
| 119M | Below national ceilings; 119M is the observed base, not a sustainable-supply case | 38.37 Mm3 below the partial AAC sum; 88.70 Mm3 below NFD. NB is 9.34 vs 9.59 NFD (under by 0.25) and 3.11 above Crown AAC; NS is 0.60 above Crown AAC but under combined 5.75. NT is 0.0003 above a non-legal proxy. No NFD exceedance. |
| 150M | Nationally admissible as a scenario bound, but not as a status-quo provincial mix | 7.34 Mm3 below the partial AAC sum (4.7% headroom); 57.68 Mm3 below NFD (27.8% headroom). Status-quo mix exceeds BC repo AAC by 3.79 (5.64 against the 43.37 2028+ hold; under the separately reported 58–60 current Crown AAC, BC is not exceeded), NB Crown AAC by 5.54 and NB NFD by 2.19, NS Crown AAC by 1.21 (under combined 5.75), and NT proxy by 0.01. A reallocation away from BC/NB could in principle satisfy the national AAC sum; no allocation model exists. |
| 200M | Not defensible against documented AAC; fails provincial NFD | 42.66 Mm3 above the partial AAC sum (27.1%); 34.2–34.8 Mm3 above the documented ceiling even with MB/PE; 15.40 Mm3 above the most generous documented ceiling; 7.68 Mm3 below the NFD aggregate but exceeds NFD in BC, AB, NB, NL, and PE; NT NFD is not reported. |

The 200M result is robust to the main data ambiguities:

- **BC:** 65.35 allocated. Against 45.22 repo AAC = +20.13; against 43.37 2028+ hold = +21.98; against 58–60 reported current AAC = +5.35 to +7.35; against NFD 63.77 = +1.58.
- **MB and PE:** adding MB's full 7.39–7.99 range and PE's 0.46 planning volume gives a documented ceiling of 165.19–165.79 Mm3, so 200M is 34.2–34.8 Mm3 above it.
- **MB, PE, and NS private:** even granting MB the top of its range, PE 0.46, and NS private 4.03, 200M remains ~30.2–30.8 Mm3 above the documented sum. Granting BC 60 as well still leaves ~15.4 Mm3 above.

150M is a different case. It is below the partial AAC sum and can be defended as the already-adopted high scenario bound, but the status-quo provincial mix is not feasible at 150M because of NB NFD and the BC repo-path/NB Crown-AAC flags. A 150M path that reallocates volume toward provinces with AAC headroom is plausible in principle; a 150M path that simply scales 2024 shares is not.

### 3.4 Managed-landbase screen

The managed-landbase report is explicit: the proxy has no growth, yield, operability, accessibility, or economic constraints and "is not a legal timber harvesting landbase (THLB), not an AAC landbase, and is not applied to the 150 Mt supply estimate" (`NATIONAL_MANAGED_LANDBASE_REPORT.md`). It cannot decide feasibility. It can only show whether an implied harvest intensity is plausible.

| Case | National harvest (Mm3) | Implied intensity on 166.28 Mha proxy (m3/ha/yr) | Relative to 2024 (0.716) |
|---|---:|---:|---:|
| 2024 | 118.98 | 0.716 | 1.00× |
| 100M | 100.00 | 0.601 | 0.84× |
| 119M | 118.98 | 0.716 | 1.00× |
| 150M | 150.00 | 0.902 | 1.26× |
| 200M | 200.00 | 1.203 | 1.68× |

Provincial intensity is much less reliable than the national aggregate because the tenure proxy is not a harvest landbase. NB (3.65 m3/ha/yr in 2024) and NS (2.12) are already several times the 0.72 national average on their proxies, which shows the proxy is missing private/other tenure, not that the provinces are harvesting unsustainably. At 200M, the indicative intensity rises to 1.20 in BC, 1.72 in AB, 1.54 in QC, 6.14 in NB, and 3.56 in NS. Those values reinforce the implausibility of the 200M mix but are not a hard constraint. The only defensible statement is: the managed landbase cannot certify 200M, and the intensity screen is consistent with the AAC/NFD finding.

## 4. Harvest cost data availability

### 4.1 What exists

| Component | Coverage | Value / unit | Status |
|---|---|---|---|
| Legacy FFCM FMU `SupplyCost` for logs | 733 FMUs in 9 provinces (BC 467, QC 147, ON 46, AB 20, SK 20, NB 11, NS 13, MB tagged `MN` 5, NL 4); PE/YT/NT/NU absent | 36.85–43.06 $/m3 log; flat within each province: BC 36.85, ON 40.305, AB/SK/MB 40.572, QC 42.469, NB/NS/NL 43.062 | Legacy FCM Basecase (LP filenames dated 2019-05-14); composition undocumented; not a stumpage rate and not a marginal cost |
| Legacy FFCM FMU `SupplyCost` for logging residue | Same 733 FMUs | 45.454084659 $/odt everywhere; equals 2.456978 $/GJ at 18.5 GJ/odt | This is the source of the CLEWS base residue cost; flat, no collection/processing breakdown |
| BC Stage A road transport | BC only; 298 zone-facility-grade links; 2018 NRN graph | Link cost 1.54–68.56 CAD/m3, mean 32.31, median 35.09; total transport 1,190.6 M CAD for 48.60 Mm3 | Diagnostic; ferry cost is a flagged placeholder (0.03 CAD/m3/km), not a BC Ferries tariff |
| BC Stage A roadside / stumpage | BC only | `supply_cost` in the objective decomposition is **-0.0** | Not modelled; BC_CHAIN_REPORT says "roadside/stumpage cost is not modelled" |
| BC Stage B residue transport and processing | BC only | Transport 274.77 M CAD / 6.856 M odt = 40.08 CAD/odt; processing 714.04 M CAD = 104.15 CAD/odt; net contribution 870.13 M CAD | Diagnostic; not a harvest cost |
| BC residue cost derivation | BC zonal | B1 55.40 CAD/odt = 2.995 $/GJ; B2 31.57 CAD/odt = 1.707 $/GJ | Transport + processing only; excludes collection, grinding, bundling, stumpage, opportunity cost |
| CLEWS national residue cost tiers | Rest of Canada | 2.456978 / 3.194071 / 3.500000 $/GJ for T1/T2, T3, T4 | **Scenario values, not a fitted marginal-cost curve**; T4 is a documented high-cost tier; no harvest cost |
| Silviculture | None | — | Missing nationally |
| Provincial stumpage tables | None | — | Missing as a separate table; the legacy FMU `SupplyCost` may embed stumpage but its composition is undocumented. No stumpage gate. The separate `calibrated_cost_surfaces` gate covers transport/processing and is blocked on NRN/NRWN vectors and provincial forest resource roads (`metadata/readiness-gates.yaml`) |

The key point is that the current CLEWS energy model has **no log harvest cost at all**. The 2.456978 $/GJ base is the legacy FFCM residue supply cost (45.454 $/odt), not a stumpage, road, or silviculture cost. The BC chain, the best-covered province, sets the roadside/supply cost component to zero and models only transport plus facility processing/revenue.

### 4.2 Provincial coverage

"Missing" means not found in the repository; it is not a zero. The only multi-province cost values are the legacy FFCM `SupplyCost` entries; BC also has the Stage A/B diagnostics described above. The FFCM values are flat within province, undated, and component-undocumented.

| Province | Legacy log `SupplyCost` ($/m3) | Legacy residue `SupplyCost` ($/odt) | Stumpage | Harvest / collection | Road / transport | Silviculture |
|---|---:|---:|---|---|---|---|
| BC | 36.85 | 45.454 | missing | missing | BC Stage A/B transport diagnostic; roadside cost = 0 | missing |
| AB | 40.572 | 45.454 | missing | missing | missing | missing |
| SK | 40.572 | 45.454 | missing | missing | missing | missing |
| MB | 40.572 (`MN`) | 45.454 | missing | missing | missing | missing |
| ON | 40.305 | 45.454 | missing | missing | missing | missing |
| QC | 42.469 | 45.454 | missing | missing | missing | missing |
| NB | 43.062 | 45.454 | missing | missing | missing | missing |
| NS | 43.062 | 45.454 | missing | missing | missing | missing |
| NL | 43.062 | 45.454 | missing | missing | missing | missing |
| PE | missing | missing | missing | missing | missing | missing |
| YT | missing | missing | missing | missing | missing | missing |
| NT | missing | missing | missing | missing | missing | missing |
| NU | missing | missing | missing | missing | missing | missing |

Even in the nine covered provinces, the table has one log cost and one residue cost per province. It cannot support a provincial marginal cost curve, and it cannot separate stumpage from harvest, haul, or silviculture.

### 4.3 Can a marginal harvest cost curve be built?

Not defensibly with the current data. The RSYC harvest sensitivity table is linear and flat: for each recovery case it applies one `variable_cost_usd_per_gj` to every harvest level and states that it does not model a supply curve or cost escalation (`rsyc_tile_harvest_sensitivity.csv`; `NATIONAL_RESIDUE_FACTOR_REPORT.md` §4). The legacy FMU table has only five distinct log-cost levels, all flat within a province, and no volume-cost slope: every FMU in a province has the same `SupplyCost`, `MinimumSupply` is zero, and `InitialContent` is a standing stock, not annual supply. Sorting FMUs by cost therefore produces a five-step provincial average-cost table, not a marginal curve. It also has no year dimension, no silviculture, no road distance, no tenure/operability, and no PE/YT/NT/NU coverage.

A genuine marginal curve needs, at minimum:

1. FMU or stand-level annual volume offered at each cost (a cost-volume step, not a single average).
2. Stumpage/royalty by tenure and species/product.
3. Harvest/collection cost by system, slope, and piece size.
4. Road construction, maintenance, and haul distance/truck cost.
5. Silviculture and regeneration obligations.
6. AAC/THLB/operability constraints so the curve cannot offer volume that is not legally or physically available.

None of items 2–6 exists as an operational national input in this repository. The transport/processing component is the separate `calibrated_cost_surfaces` gate, blocked on `nrn`, `forest_resource_roads`, `nrn_vectors`, and `nrwn_vectors`; NFI is explicitly not the blocker. Stumpage, harvest/collection, and silviculture have no equivalent gate or source in the repository.

### 4.4 Why a naive harvest-cost adder is not a small fix

If the full log supply cost were allocated to residue, the scale would be wrong. One m3 of log yields about 0.0883 odt gross residue, 0.0530 odt recoverable at 0.60, or 0.980 GJ at 18.5 GJ/odt. A $1/m3 harvest cost is therefore about $1.02/GJ of residue; the legacy $36.85–43.06/m3 log cost would map to $37.6–43.9/GJ, 15–18× the current 2.457 $/GJ. Conversely, the current residue cost is equivalent to only about $2.41/m3 of harvest. That does not mean harvest costs should be added at that scale: most of the log cost is borne for the logs, which have product value outside the energy model. The correct marginal cost is the cost of **recovering additional residue**, not the cost of harvesting the log. Attaching a full harvest cost to residue would either double-count or arbitrarily suppress bioenergy. Because harvest is exogenous, a flat harvest cost added to the total objective would be a fixed cost and would not change dispatch; to affect the energy trade-off it must be attached to residue supply or to a new harvest decision variable. This is why option (b) below is a later sensitivity, not the immediate fix.

## 5. Options

| Option | What it fixes | Data / effort | Verdict |
|---|---|---|---|
| (a) Hard AAC / wood-supply cap | Rejects physically or legally infeasible cases before solve; removes the 200M artifact | Low for a national cap; high for a provincial cap because no allocation model exists | **Adopt now at the national scenario bound**; add documented AAC/NFD assertions. Provincial cap is later. |
| (b) Harvest cost adder to residue supply cost | Could make additional harvest less attractive and create a smoother trade-off | High: no defensible marginal cost curve; current costs are flat legacy values; naive allocation is 15–18× wrong | **Defer.** Use only as a clearly labelled sensitivity if a residue-recovery cost basis is built. |
| (c) Treat harvest as an exogenous forest-sector dimension, excluded from the energy Pareto | Removes the false "more harvest is better" frontier; matches the model's actual structure | Low: UI/export labels and eligibility rules | **Adopt now.** Keep the ladder as a conditional sensitivity, not a Pareto frontier. |
| (d) Multi-objective forest-sector LP | Represents provincial AAC, NFD, harvest cost, residue supply, and forest-sector objectives jointly | High: provincial allocation, cost surfaces, THLB, solver/model build | **Later milestone.** The correct long-run home for this question. |

The options are complementary, not substitutes. (c) fixes the interpretation; (a) fixes admissibility; (b) and (d) are the long-run economics.

## 6. Recommendation

### 6.1 Smallest defensible fix for the UI/runner now

1. **Cap the exposed harvest ladder at 150,000,000 m3/yr.** 150M is the already-adopted high scenario bound (`planning/national-harvest-basis.md`) and is below the partial AAC sum of 157,343,387. Reject 200M and any custom value above 150M in the runner with a descriptive `RunnerError`. Do not silently clamp; fail closed.
2. **Add documented-ceiling assertions to the run manifest/summary.** Record `documented_aac_partial_sum_m3 = 157,343,387`, `nfd_wood_supply_sum_m3 = 207,680,142`, `max_exposed_harvest_m3 = 150,000,000`, and the provincial flags from §3.2. The assertion `harvest <= 157,343,387` is documentation and a second guard; it is not a legal national AAC.
3. **Remove 200M from the UI run selector and lever calculator.** In `ui/index.html`, the current option `200 — supply-curve basis` should not be selectable for an energy scenario. If a diagnostic is needed, expose it only through a separate, clearly labelled diagnostic path, not the normal run form or Pareto export. Existing 200M artifacts and the 200M supply-curve definition remain as historical diagnostics; do not rewrite them, and do not export them as selectable scenarios.
4. **Exclude the 100/150/200 harvest-ladder points from the energy Pareto / trade-off ledger.** Keep them as conditional forest-sector sensitivities; keep 119M only as the reference/canonical case. Do not plot the ladder as an objective-vs-emissions frontier, because the objective excludes harvest cost and log value and the points are ordered by the exogenous harvest assumption, not by a modeled trade-off. The UI should say: "Harvest is an exogenous forest-sector input; this is a conditional energy result, not an energy-forest trade-off."
5. **Keep 119M as the observed reference and canonical screening case.** Do not promote 150M or 200M to "best." The 119M case is the observed 2024 base, not a Pareto-optimal point; its dominance by 150M is an artifact of unpriced harvest.
6. **Attach the provincial caveat to 150M.** At the status-quo mix, 150M exceeds the BC repo AAC (3.79; 5.64 against the 2028+ hold), NB Crown AAC (5.54) and NB NFD (2.19), NS Crown AAC (1.21, but under combined Crown+private 5.75), and the NT proxy (0.01). If the UI shows 150M as an allowed case, it must also show "national bound; provincial allocation not modelled."

None of this makes a harvest case policy-grade; `planning/policy-test-v1.md` is a separate constraint family, and this note does not change its status or the `screening_only` gate. Items 1 and 3 are the code minimum; items 2, 4, 5, and 6 are metadata/copy and do not change the solve. This is the smallest change that removes the false frontier and the clearly infeasible 200M point without inventing a provincial AAC or a harvest cost curve. The runner guard should run before the model copy, ideally in `run_scenario` after scenario parsing; `resolve_harvest_log_m3` is a fallback but is reached later, after the model copy. The cap arithmetic remains in `compute_supply_caps` in `workflows/ui/osecan_runner.py`. The UI labels belong in `ui/index.html` and `ui/app.js`; the exported scenario metadata belongs in `workflows/ui/export_iteration0.py` and `ui/public/data`.

### 6.2 Later milestones

1. **Provincial harvest allocation and AAC cap.** Build a documented provincial allocation rule (historical shares, tenure, or a forest-sector LP) and enforce provincial AAC/NFD. This is required before any 150M case can be called feasible under the status-quo mix, and before any 150–157.3M band is exposed. **Screening LP implemented 2026-11-20** (`data/derived/national/harvest_basis/provincial_allocation/`): the least-deviation allocation is feasible at 100/119/150 Mm3/yr and infeasible at 200 Mm3/yr, but it remains non-legal and is not a substitute for THLB, cost, or tenure resolution.
2. **Marginal harvest/residue cost curve.** Acquire stumpage, harvest/collection, road/transport, and silviculture data; build cost-volume steps by FMU/stand; separate log cost from residue-recovery cost. Gate on `calibrated_cost_surfaces`.
3. **Forest-sector LP (option d).** Couple harvest, assortments, residues, facilities, and energy with provincial AAC/NFD and cost curves. Only then can harvest be an endogenous forest-sector decision.
4. **Managed THLB/operability.** Replace the tenure-union proxy with a real THLB and operability filter before making sustainable-supply claims.
5. **2025+ AAC trajectory.** Only BC has a parsed path; MB/PE/NU gaps remain. Do not extrapolate.
6. **Re-run the ladder.** Re-run 100/119/150 under the cap and with feasibility labels; keep 200M only as a rejected diagnostic, if at all.

### 6.3 What not to do

- Do not report 157,343,387 as a national AAC; it is a partial, mixed-basis sum with MB, PE, and NU missing.
- Do not add a flat harvest cost to residue and call it a marginal cost; the scale is wrong and the accounting is unresolved.
- Do not use the managed-landbase proxy as a THLB or an AAC.
- Do not treat the 200M point as a feasible scenario because the NFD aggregate is 207.68 Mm3; NFD is potential supply, not AAC, and the provincial distribution fails.
- Do not promote 150M or 200M to the canonical/default case on the basis of the current ladder.

## 7. Sources and paths

| Purpose | Path |
|---|---|
| Harvest basis template (provincial + national rows) | `data/derived/national/harvest_basis/harvest_basis_template.csv` |
| Harvest reference paths | `data/derived/national/harvest_basis/harvest_reference_paths.csv` |
| Provincial coverage/basis notes | `data/derived/national/harvest_basis/provinces.csv` |
| Harvest basis manifest | `data/derived/national/harvest_basis/manifest.json` |
| BC AAC trajectory (2021–2028 + hold) | `data/derived/national/clews/audit/bc_aac_trajectory_2011_2028.csv` |
| Fleet ladder summaries | `data/derived/national/clews/osecan/fleet_screening_ladder/{100m,119m,150m,200m}/run_summaries.json` |
| Fleet ladder manifests (supply caps) | same directories, `manifest.json` |
| Harvest decision note | `planning/national-harvest-basis.md` |
| RSYC backbone decision note | `planning/national-rsyc-backbone.md` |
| RSYC residue factors by province | `data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv` |
| RSYC residue factor report (allocation caveats) | `data/derived/national/clews_supply/rsyc_backbone/NATIONAL_RESIDUE_FACTOR_REPORT.md` |
| Harvest sensitivity table | `data/derived/national/clews_supply/rsyc_tile_production/rsyc_tile_harvest_sensitivity.csv` |
| Managed landbase summary | `data/derived/national/managed_landbase/managed_landbase_province_summary.csv` |
| Managed landbase report | `data/derived/national/managed_landbase/NATIONAL_MANAGED_LANDBASE_REPORT.md` |
| National supply-curve report (tier costs) | `data/derived/national/clews/osecan/national_supply_curve/NATIONAL_SUPPLY_CURVE_REPORT.md` |
| BC chain report | `data/derived/national/bc_chain/BC_CHAIN_REPORT.md` |
| BC stage A objective decomposition | `data/derived/national/bc_chain/bc_stage_a_summary.json` |
| BC residue cost derivation | `data/derived/national/clews_supply/bc_woody_residue/audit/bc_cost_derivation.csv` |
| Legacy FFCM FMU supply costs | `metadata/ffcm-archive/parsed/fmu_supply.csv` |
| Cost-surface readiness gate | `metadata/readiness-gates.yaml` (`calibrated_cost_surfaces`) |
| BC residue recovery caveats | `planning/bc-residue-recovery.md` |
| Policy-test v1 design note | `planning/policy-test-v1.md` |
| Runner harvest/cap logic | `workflows/ui/osecan_runner.py` (`resolve_harvest_log_m3`, `compute_supply_caps`) |
| UI default harvest levels | `workflows/ui/server.py` (`harvest_levels_m3_per_year`) |
| UI harvest selector / Pareto | `ui/index.html`, `ui/app.js` |
| Export metadata | `workflows/ui/export_iteration0.py` |
| Fleet ladder change record | `CHANGE_LOG.md` (2026-11-20 entry) |

## 8. Gaps

- **No national AAC.** The 157.34 Mm3 sum excludes MB, PE, and NU and mixes statutory AAC, approximate AAC, a repo TSA path, and an actual-mean proxy. It cannot support a legal cap or a "Canada AAC" label.
- **Provincial allocation is a residue-weighting input, not an enforced cap.** The screening LP allocation can weight national residue factors, but the runner remains national and does not enforce provincial AAC/NFD. A provincial allocation is still prerequisite to a provincial AAC cap.
- **AAC tenure mismatch.** NB, NS, and QC AAC are Crown-only; FSD harvest is total. NB's private supply is not documented; NS's private AAC (4.03) is documented separately. BC's current reported Crown AAC (58–60) differs from the repo path (45.22 in 2024, 43.37 from 2028).
- **NFD is not AAC.** The 207.68 Mm3 NFD sum is potential supply; NT NFD is missing. It cannot certify 200M.
- **Managed landbase is not a THLB.** It is a 2016 tenure-union proxy with no growth, yield, operability, or AAC. PE and NU are missing; BC is an upper bound; NL and NT are low-coverage.
- **No marginal harvest cost curve.** The legacy FMU table is flat within province, covers 9 provinces, has no annual volume, and has no stumpage/silviculture/road separation. `calibrated_cost_surfaces` remains blocked.
- **No harvest cost in the energy objective.** The ladder's objective is an energy-system cost conditional on exogenous harvest; it excludes log value, harvest cost, and forest-sector impacts. It is not a social cost-benefit ranking.
- **200M is a scenario bound, not a forecast.** The 200M artifacts are useful as a stress test of the energy model but should not be presented as a feasible harvest path.

## 9. Bottom line

For the UI/runner now: cap the exposed ladder at 150M, reject 200M, keep 119M as the observed reference, and stop treating the harvest ladder as an energy Pareto frontier. For the forest-sector later: build a provincial allocation, a real harvest/residue cost curve, and a forest-sector LP before making any claim about harvest levels above 150M or about the 150M provincial mix.

**Decision requested:** approve the 150M runner cap and the Pareto exclusion as the immediate fix; leave the 150–157.3M band, the harvest cost curve, and the forest-sector LP to later milestones.
