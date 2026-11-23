# Model card — National transport and siting layer v1

| Field | Value |
|---|---|
| Package | National transport/siting layer v1 (road factors, truck cost, node assignment v10, chain v5.1 transport summary, greenfield siting screen, break-even threshold) |
| Freeze tag (documentation) | `national-transport-siting-v1` |
| Date | 2026-11-23 |
| Status | `model_estimate` screening layer; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Transport/siting data commits | assignment v10 `16e979ab06757ef069870fadc851af8ce319a447`; greenfield siting `1f0a02a08fab5e9e1a173d5bfdfe965c5b1623a7`; break-even `5b7d717fb6191b694936a55d88b53a0afbc016e5`; case screen `068787dee4e6c05716ce6f636c758c7807e2a5ce` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Companion chain | `docs/model-card-national-facility-chain.md`; frozen chain tag `national-facility-chain-v5.1` |
| Evidence boundary | Road/route and cost screens, not observed routes or observed costs; no capital, permitting, class-capacity, landbase, investment, UI, or official OSeCAN change |
| Scope boundary | Documentation/tag freeze only; no code, model artifact, UI, or official OSeCAN change at the freeze commit |

## Addendum record

A completed internal addendum consolidates this frozen v1 layer and the
post-freeze class-specific follow-up, and records an explicit pause on further
proxy and siting iteration pending a C2070 scope decision:
[`docs/addendum-national-transport-siting-v1.md`](addendum-national-transport-siting-v1.md)
(annotated tag `national-transport-siting-addendum-v1`). The addendum adds no
model result and changes no number, artifact, or tag in this card.

## 1. Purpose and scope

This card documents the transport and siting layer that sits between the
committed national harvest/allocation inputs and the frozen facility chain. It
answers four screening questions:

1. How far are facilities and supply nodes from one another once a road-network
   factor is applied to straight-line distance?
2. What does truck transport cost along that distance curve?
3. How does a node-level, transport-aware facility assignment (v10) compare
   with centroid and local-first assignments?
4. What do a heuristic greenfield k-median screen and its break-even capital
   threshold imply about the scale of transport savings, without claiming an
   investment case?

It is not an observed route model, a mill-gate logistics plan, a capital or
permitting estimate, a class-capacity design, an interprovincial trade forecast,
an investment recommendation, or an official OSeCAN result.

## 2. Components and stage map

| Component | Artifact | Role | Key output |
|---|---|---|---|
| Road factors | `facility_road_distance_audit_v2` | Fits province road/haversine factors from public OSRM demo routes | 11 factors; 81/81 routes; mean absolute proxy error 8.87% |
| Transport cost | `transport_cost_v1` | Bottom-up 2026 CAD truck cost curve | 11.50 CAD/m³ at 100 km base; 52.5 m³ payload; empty return |
| Supply nodes | `supply_node_distance_v1` | Replaces province centroids with RSYC node origins | 525 nodes; 486 positive-volume; 677.5 km fixed-assignment mean |
| Assignment v10 | `national_facility_assignment_v10` | Node-to-facility transport-aware LP | 118.977 Mm³; 296 facilities; 3.392 B CAD/yr; 270.289 km mean |
| Chain v5.1 | chain v9 artifacts | All-case v10 facility/residue/energy chain | 2.702 / 3.392 / 5.018 B CAD/yr at 100/119/150 Mm³ |
| Greenfield siting | `greenfield_siting_sensitivity_v1(_cases)` | Greedy k-median screen with exact LP re-evaluation | 119 Mm³ k=20: 1.749 B CAD/yr; 131.973 km mean |
| Break-even | `greenfield_breakeven_capital_threshold_v1` | Converts transport savings into an annualized capital threshold | 40.55–59.47 CAD per m³/yr at 119 Mm³ |

All components are static, single-period screens. The greenfield screen is a
separate sensitivity and is **not** part of frozen chain v5.1.

## 3. Road factors and OSRM evidence

`facility_road_distance_audit_v2` samples eligible located facilities by
province and haversine quantile, routes 81 pairs through the public OSRM demo
service, and fits a province-level median road/haversine ratio. The sample is
province- and distance-stratified, not a random national sample; the public demo
is best-effort, and no paid routing key or full national OSRM matrix is used.

| Province | Factor | Province | Factor |
|---|---:|---|---:|
| BC | 1.474 | NB | 1.488 |
| AB | 1.305 | NS | 1.204 |
| SK | 1.397 | PE | 1.110 |
| MB | 1.456 | NL | 2.004 |
| ON | 1.359 | YT | 1.725 |
| QC | 2.418 | NT/NU | no factor fitted |

Against the global 1.3 factor, the province factors lower mean absolute proxy
error from 18.86% to 8.87% and raise the within-10% share from 37.0% to 67.9%
on the 81-route sample. QC is high (2.418) and PE/YT have very low sample sizes;
their confidence and dispersion flags are retained in the artifact. NT and NU
have no eligible located facility and no fabricated factor.

## 4. Truck transport cost v1

The cost component is a bottom-up 2026 CAD model at 52.5 m³ payload with a fully
empty return. At 100 km one-way the base trip costs **604.00 CAD** or
**11.50 CAD/m³**; the six operating components (fuel, driver, maintenance,
tires, capital, insurance) are scaled by overhead. Diesel and wage observations
come from Statistics Canada; the remaining duty-cycle, payload, density, speed,
life, financing, and overhead values are explicit engineering assumptions. The
low/base/high columns are a favourable-to-unfavourable stress envelope, not a
confidence interval.

At the base parameters, transport alone equals the adopted OSeCAN tier variable
costs at **84.9 km** (T1/T2 2.457 $/GJ), **115.0 km** (T3 3.194 $/GJ), and
**127.4 km** (T4 3.500 $/GJ). The comparison treats tier values at face value;
the interface does not document currency or price year, so no FX or deflator is
applied.

The curve excludes feedstock, processing, conversion, handling, storage,
product revenue, backhaul, border delay, customs, ferry, winter-road,
resource-road, queueing, and hours-of-service relay. It is a national-scope
component anchored on Vancouver diesel and BC wages, not a national observed
average.

## 5. Supply nodes and assignment v10

### 5.1 Node construction

`supply_node_distance_v1` replaces province centroids with **525 RSYC
150-km tile × province nodes**. Harvest is assigned in proportion to located
RSYC age-100 standing volume within each province, preserving the 118.977 Mm³
provincial control total. **486 nodes carry positive volume**; the 39 NU nodes
carry zero. RSYC district-fallback area with no province intersection
(16.26 Mm³ of raw production, 0.03%) is excluded rather than given a fabricated
location.

Holding the v9 assignment fixed and moving origins from centroids to nodes
raises the assigned-volume-weighted mean distance from **521.972 km to
677.533 km** (+29.8%) and the base transport cost from **53.64 to
69.18 CAD/m³**.

### 5.2 Assignment v10

V10 solves a bipartite node-to-facility transportation LP (HiGHS) over the 486
positive-volume nodes and 591 eligible operating facilities. It keeps capacity
v3, the 1.2× provincial floor, the 118.977 Mm³ total, and the transport cost v1
curve. Distance is haversine × facility-province road factor; a **500 km
cross-border soft penalty (51.45 CAD/m³)** replaces the local-first rule. No
full OSRM routing is run.

| Metric | V9 centroid | V9 fixed at nodes | V10 node LP |
|---|---:|---:|---:|
| Transport cost (B CAD/yr) | 6.382 | 8.231 | **3.392** |
| Transport cost (CAD/m³) | 53.645 | 69.180 | **28.511** |
| Mean road distance (km) | 521.972 | 677.533 | **270.289** |
| Median / p90 road distance (km) | — | — | **147.217 / 635.737** |
| Share ≥150 km | 83.5% | 92.7% | **48.619%** |
| Share ≥300 km | 49.3% | 79.1% | **30.163%** |
| Facilities used | 203 | 203 | **296** |
| Cross-border volume (Mm³) | 0.036 | 0.036 | **2.325** |
| Proxy share of assigned | 52.9% | 52.9% | **54.08%** |

V10 lowers transport cost by 2.990 B CAD/yr versus the v9 centroid assignment
and by 4.839 B CAD/yr versus the same v9 allocation evaluated at node origins;
the mean distance falls by 407.2 km (60.11%). A diagnostic distance-only LP with
zero local-preference penalty reaches 255.8 km, and the unconstrained
nearest-facility weighted mean is 191.5 km. Facility geography and finite
capacity therefore set the residual distance floor; no facility is moved.

## 6. Transport in frozen chain v5.1

Chain v5.1 applies assignment v10 to all three documented harvest cases. The
100/150 Mm³ node vectors scale each province's 119 Mm³ allocation, so
within-province node shares are unchanged. Products and residue are invariant
at each case; only the facility distribution and transport summary move.

| Metric | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|
| Facilities used | 270 | 296 | 333 |
| Transport cost (B CAD/yr) | 2.702 | 3.392 | 5.018 |
| Transport cost (CAD/m³) | 27.016 | 28.511 | 33.454 |
| Mean road distance (km) | 255.328 | 270.289 | 319.790 |
| Share ≥150 km | 45.670% | 48.619% | 55.970% |
| Cross-border volume (Mm³) | 1.161 | 2.325 | 11.036 |
| Process residue (PJ) | 419.238 | 498.751 | 626.865 |

Against assignment-v6 local-first at the same harvest, v10 lowers cost by
30.73% at 100 Mm³ and 26.13% at 150 Mm³; the 119 Mm³ deltas remain −31.59% cost
and −32.78% distance. Transport cost is a chain summary only: it is
**not** an OSeCAN input (`transport_cost_in_osecan=false`), and all 21 OSeCAN
tier positions are unchanged.

## 7. Greenfield k-median siting screen

### 7.1 Method and setup

The screen holds existing capacity v3 and the 1.2× floor fixed (591 facilities,
273.468 Mm³/yr), keeps the 118.977 Mm³ demand, and allows **421 positive-volume
supply nodes** in provinces with a road factor to host a **generic,
class-agnostic module with unlimited throughput**. NT nodes are demand only
because no NT factor is published; their 0.031% of volume remains in the demand
set.

The method is a greedy forward k-median/facility-location screen: at each k the
shortlist is the top 10 candidates by capacity-relaxed distance savings, and
every trial and every k is re-evaluated with the exact capacitated transport LP
(HiGHS). The selected sets are nested prefixes. This is **not a proven global
optimum**, and no optimality-gap claim is made. The k-series applies no
local-preference penalty.

### 7.2 119 Mm³ tradeoff

| Metric | k=0 (transport-only) | k=20 |
|---|---:|---:|
| Transport cost (B CAD/yr) | 3.220 | **1.749** |
| Transport cost (CAD/m³) | 27.07 | **14.70** |
| Mean road distance (km) | 255.8 | **131.973** |
| Median / p90 road distance (km) | 144.7 / 628.7 | **98.1 / 283.9** |
| Share ≥150 km | 48.4% | **32.1%** |
| Share ≥300 km | 27.7% | **8.7%** |
| Module-served volume share | 0.0% | **30.26%** |
| Existing capacity utilization | 43.5% | **30.34%** |

At k=20 the screen saves **1.471 B CAD/yr (45.70%)** versus the transport-only
k=0 baseline and **1.643 B CAD/yr (48.45%)** versus committed v10. The first k
whose marginal cost reduction is below 1% of the k=0 cost is **k=13**; the
capacity-relaxed lower-bound gap is 9.69%, and that bound ignores existing
capacity, so it is not an optimality gap.

The 20 selected modules are distributed BC 6, QC 4, AB 3, ON 3, NL 2, SK 1, and
NB 1. Module locations are supply-node coordinates, not plant sites.

### 7.3 100/150 Mm³ cases

| Case | k=0 cost / mean | k=20 cost / mean | k=20 module share | First k with marginal <1% |
|---|---:|---:|---:|---:|
| 100 Mm³ | 2.572 B / 242.3 km | 1.428 B / 127.7 km | 30.3% | 13 |
| 119 Mm³ | 3.220 B / 255.8 km | 1.749 B / 132.0 km | 30.3% | 13 |
| 150 Mm³ | 4.728 B / 300.4 km | 2.307 B / 138.8 km | 34.7% | 13 |

The 100 and 150 Mm³ case vectors are scaled from the 119 Mm³ provincial
allocation; they are not independent node-level inventories. All three show
diminishing returns beginning at k=13.

### 7.4 Break-even annualized capital thresholds (119 Mm³)

The threshold converts cumulative transport savings into the maximum
annualized capital charge that the saving could support at break-even:

```text
cumulative_threshold_k = (TC_0 - TC_k) / V_k
marginal_threshold_k   = (TC_{k-1} - TC_k) / (V_k - V_{k-1})
```

Units are 2026 CAD per (m³/yr) of module-served volume. At 119 Mm³:

- k=1 cumulative threshold: **59.47** CAD per m³/yr.
- Minimum cumulative threshold: **40.55** CAD per m³/yr at k=4.
- k=20 cumulative threshold: **40.87** CAD per m³/yr.
- Median defined marginal threshold: **41.89** CAD per m³/yr.
- Zero marginal module volume at k=14 and k=19; thin marginal volume at k=18.

The overall range is therefore about **41–59 CAD per m³/yr**. This is a
break-even threshold, **not a capital cost estimate**: no capex, discount rate,
asset life, fixed operating cost, feedstock cost, processing cost, product
revenue, or financing assumption is introduced. The saving includes re-routing
and existing-facility effects, not a module-only revenue stream.

### 7.5 Class-specific follow-up (post-freeze, 2026-11-23)

The frozen v1 screen above is class-agnostic. A separate post-freeze screen,
`greenfield_class_siting_v1`, applies the v5.1 E1/E2c province/class feedstock
shares to the node harvest, partitions the same capacity-v3 facilities by
class, and selects k = 0..5 unlimited-throughput modules **per class**. It
remains a transport-only screen and adds no class capacity, technology,
capital, permitting, landbase, national, policy, UI, or official OSeCAN claim.

At 119 Mm³ the class-specific existing-only cost is **5.181 B CAD/yr** and
420.9 km; at k=5 per class (25 modules) it is **3.179 B CAD/yr**, 252.4 km, with
27.9% module-served volume and a 2.002 B CAD/yr (38.6%) reduction versus k=0.
At the exact 20-module count match (class k=4 versus generic k=20), the
class-specific screen is **3.314 B CAD/yr** and 263.7 km versus **1.749 B
CAD/yr** and 132.0 km for the generic screen. The class restriction therefore
raises transport cost materially at a fixed module count; the comparison is a
screen diagnostic, not an investment or policy result.

## 8. Key results

- **V10 node assignment:** 118.977 Mm³ assigned across 296 facilities;
  **3.392 B CAD/yr**, 28.51 CAD/m³, 7.005 CAD/GJ, **270.289 km** mean road
  distance; 48.62% at ≥150 km and 30.16% at ≥300 km.
- **V10 versus centroid/node fixed:** 6.382 B CAD/yr and 521.972 km at v9
  centroids; 8.231 B CAD/yr and 677.533 km for the same v9 allocation at nodes;
  v10 removes 407.2 km (60.11%) of the node-origin penalty.
- **Chain v5.1:** 2.702 / 3.392 / 5.018 B CAD/yr and 255.3 / 270.3 / 319.8 km
  at 100/119/150 Mm³; products and residue invariant; OSeCAN tiers unchanged.
- **Greenfield 119 Mm³:** k=20 gives **1.749 B CAD/yr** and **131.973 km**,
  saving 45.70% versus k=0; **k=13** is the first diminishing-returns point.
- **Greenfield cases:** 100 Mm³ k=20 1.428 B / 127.7 km; 150 Mm³ k=20
  2.307 B / 138.8 km; k=13 diminishing in all cases.
- **Break-even 119 Mm³:** 40.55–59.47 CAD per m³/yr, with 40.87 at k=20 and a
  41.89 median defined marginal threshold.

## 9. Limitations and non-use

- **Geometry is a screen, not a route.** Node and facility distances are
  haversine × province road factor. Nodes are RSYC tile-province centroids, not
  landings, plant gates, or observed haul origins. The 81-route OSRM sample is
  an external check, not a route for every pair; no full OSRM matrix is run.
  Resource roads, winter roads, ferries, weight limits, log-truck restrictions,
  border delay, and endpoint snap/off-road connectors are not modelled.
- **Road factors are province medians with weak cells.** QC is high (2.418);
  PE and YT have 1–2 routes; NT/NU have no factor. The sample is stratified and
  not a random national sample. A province factor cannot represent within-
  province road geography.
- **Siting is heuristic.** The k-median screen is a greedy forward prefix with a
  top-10 shortlist and exact LP re-evaluation, not a global optimum. The
  capacity-relaxed gap is a bound diagnostic only.
- **Modules are generic in the frozen screen.** The v1 candidate is
  class-agnostic, has unlimited throughput, and asserts no technology, product,
  residue, cost, or facility design. The separate class-specific follow-up v1
  partitions the chain by class but still asserts no class capacity,
  technology, capital, or facility design.
- **No capital or permitting.** The break-even threshold is not a capex,
  operating, landbase, procurement, permitting, financing, or investment
  estimate. No revenue or net-value result is produced. The screen is
  `not_investment_advice`.
- **No observed cost.** Diesel and wages are observed statistics, but duty
  cycle, payload, density, speeds, life, financing, insurance, and overhead are
  assumptions. The low/high envelope is not a confidence interval. The tier
  comparison is face-value parity, not an FX conversion.
- **No delivery or market claim.** Transport cost excludes feedstock,
  processing, conversion, handling, storage, product revenue, backhaul, border,
  ferry, winter-road, and queueing effects. It is not a delivered-cost or
  market forecast.
- **No greenfield result in chain v5.1.** The siting screen changes no chain
  product, residue, facility or OSeCAN total. It is a separate sensitivity.
- **No official OSeCAN or UI change.** Transport cost is not in official
  OSeCAN tiers; the handoff is read-only; no public UI or official OSeCAN file
  changes. Every row is `model_estimate`, `observed=false`,
  `policy_grade=false`, `national_claim=false`.
- **No legal or policy determination.** The 100/119/150 Mm³ cases are planning
  allocations, not AACs or sustainable-supply determinations. No national,
  policy, or investment conclusion may be drawn.

## 10. Reproduce and verify

```bash
.venv/bin/python -m workflows.national.facilities.facility_road_distance_audit_v2
.venv/bin/python -m workflows.national.facilities.transport_cost_v1
.venv/bin/python -m workflows.national.facilities.supply_node_distance_v1
.venv/bin/python -m workflows.national.facilities.national_facility_assignment_v10
.venv/bin/python -m workflows.national.facilities.greenfield_siting_sensitivity_v1
.venv/bin/python -m workflows.national.facilities.greenfield_breakeven_capital_threshold_v1
.venv/bin/python -m workflows.national.facilities.greenfield_siting_sensitivity_v1_cases
```

At this freeze the seven transport/siting manifests verify **64/64 output
entries with 0 mismatches** (assignment v10 20, transport cost v1 7, road audit
v2 6, supply nodes v1 12, greenfield siting 7, break-even 3, case screen 9).
The artifact reports are the source of record for all tables above.

## 11. Non-claims and freeze record

**Non-claims.** This package does not claim: an observed route, haul cost, or
national transport average; a facility or module capital cost; a permitting,
landbase, tenure, procurement, or investment outcome; class-specific facility
capacity or design; an optimal siting solution; an interprovincial trade
forecast; a delivered feedstock cost or market price; an official OSeCAN
scenario or a public UI release; or a legal AAC or sustainable harvest level.
It is a model-estimate screening layer, not policy-grade evidence.

**Freeze record.** Annotated tag `national-transport-siting-v1` marks this
documentation commit. It records transport/siting data commits assignment v10
`16e979ab06757ef069870fadc851af8ce319a447`, greenfield siting
`1f0a02a08fab5e9e1a173d5bfdfe965c5b1623a7`, break-even
`5b7d717fb6191b694936a55d88b53a0afbc016e5`, and case screen
`068787dee4e6c05716ce6f636c758c7807e2a5ce` in
`UBC-FRESH/clews-c2070-fresh-data`. The tag does not move
`national-facility-chain-v5.1` or any earlier tag. The freeze commit is
documentation and tag only; no code, model artifact, UI, or official OSeCAN
file changes at that commit.
