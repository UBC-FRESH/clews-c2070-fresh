# Addendum — National transport and siting layer v1

| Field | Value |
|---|---|
| Document | National transport/siting addendum v1 |
| Status | **Completed internal addendum; frozen; awaiting C2070 scope decision on facility siting/transport** |
| Freeze tag | `national-transport-siting-addendum-v1` (this documentation commit) |
| Date | 2026-11-23 |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false`; `transport_only`; `not_investment_advice` |
| Scope | Documentation consolidation and scope-decision pause only; no code, UI, model artifact, or official OSeCAN change |
| Companion card | [`docs/model-card-national-transport-siting.md`](model-card-national-transport-siting.md) |
| Companion chain card | [`docs/model-card-national-facility-chain.md`](model-card-national-facility-chain.md) |

## 1. Purpose and status

This addendum consolidates the completed national transport and siting
analysis into a single frozen internal record and converts the work into an
explicit scope-decision pause. It is written for the project team, not for
external release, and it does not replace the model cards or artifact
manifests.

The analysis answers a narrow screening question: how much does modelled
truck transport change under alternative facility assignments and greenfield
module locations, holding the committed harvest, product, residue, and
capacity-v3 facility universe fixed? It is a transport-only screen. It is not
a siting decision, an investment case, a technology choice, a permitting
path, or an official OSeCAN result.

Status terms used here:

- **Completed** — the road audit, transport-cost curve, supply-node set,
  assignment v10, chain v5.1 cases, generic greenfield screen, break-even
  thresholds, and class-specific follow-up have all been run and documented.
- **Frozen** — the artifacts and numbers in this addendum are the reference
  record. No further iteration is part of the current scope.
- **Awaiting scope decision** — no additional proxy refinement, greenfield
  siting, class-capacity design, or transport-model expansion should start
  until the C2070 scope owner confirms that facility siting and transport are
  in scope (see §11).

## 2. Evidence base and frozen references

| Reference | Repository | Commit or tag | Role in this addendum |
|---|---|---|---|
| Chain v5.1 | parent | tag `national-facility-chain-v5.1` | Frozen all-case facility-chain context |
| Chain v5.1 data | `UBC-FRESH/clews-c2070-fresh-data` | `0d3b2ff4c27f18bad2f93fd6c269c180c4dbcadc` | Chain data commit recorded by the tag |
| Transport/siting v1 | parent | tag `national-transport-siting-v1` | Frozen transport/siting model-card documentation |
| Assignment v10 | `UBC-FRESH/clews-c2070-fresh-data` | `16e979ab06757ef069870fadc851af8ce319a447` | Node-to-facility transport-aware LP at 119 Mm³ |
| Greenfield siting | `UBC-FRESH/clews-c2070-fresh-data` | `1f0a02a08fab5e9e1a173d5bfdfe965c5b1623a7` | Generic k-median sensitivity |
| Break-even threshold | `UBC-FRESH/clews-c2070-fresh-data` | `5b7d717fb6191b694936a55d88b53a0afbc016e5` | Annualized capital threshold at 119 Mm³ |
| Case screen | `UBC-FRESH/clews-c2070-fresh-data` | `068787dee4e6c05716ce6f636c758c7807e2a5ce` | Greenfield 100/150 Mm³ cases |
| Class-specific siting | `UBC-FRESH/clews-c2070-fresh-data` | `742591ba268c000370c9a36d07b53af1095fc8ef` | Post-freeze class-partitioned follow-up |
| This addendum | parent | tag `national-transport-siting-addendum-v1` | Documentation consolidation and pause |

The addendum tag marks this documentation commit only. It does not move
`national-facility-chain-v5.1`, `national-transport-siting-v1`, or any earlier
tag, and it does not change the data commits above.

## 3. Road factors and OSRM audit v2

`facility_road_distance_audit_v2` fits province-level road/haversine factors
from public OSRM demo routes. The sample contains **81 route pairs across 11
jurisdictions**; it is stratified by province and haversine distance quantile,
not a random national sample. The public demo service is best-effort, and no
paid routing key or full national OSRM matrix is used.

| Province | Factor | Province | Factor |
|---|---:|---|---:|
| BC | 1.474 | NB | 1.488 |
| AB | 1.305 | NS | 1.204 |
| SK | 1.397 | PE | 1.110 |
| MB | 1.456 | NL | 2.004 |
| ON | 1.359 | YT | 1.725 |
| QC | 2.418 | NT/NU | no factor fitted |

Against a single global 1.3 factor, the province factors reduce mean absolute
proxy error from **18.86% to 8.87%** and raise the within-10% share from
**37.0% to 67.9%** on the same 81-route sample. QC is high (2.418); PE and YT
have very small samples; NT and NU have no fitted factor rather than a
fabricated one. Dispersion and confidence flags remain in the audit artifact.

The factor is a screening multiplier. It cannot represent within-province road
geometry, resource roads, winter roads, ferries, weight limits, or endpoint
connectors, and a factor fitted from a small province sample may not transfer
to every facility or supply node.

## 4. Supply nodes and assignment v10

`supply_node_distance_v1` replaces province centroids with **525 RSYC
tile-province supply nodes**. Harvest is distributed in proportion to located
RSYC age-100 standing volume within each province, preserving the 118.977 Mm³
provincial control total. **486 nodes carry positive volume**; the 39 NU nodes
carry zero. RSYC district-fallback area with no province intersection is
excluded rather than assigned a fabricated location.

Assignment v10 solves a bipartite node-to-facility transportation LP (HiGHS)
over the positive-volume nodes and 591 eligible operating facilities. It keeps
capacity v3, the 1.2× provincial floor, the 118.977 Mm³ total, and the
transport cost v1 curve. Distances are haversine × facility-province road
factor; a 500 km cross-border soft penalty (51.45 CAD/m³) replaces the
local-first rule. No full OSRM routing is run.

At 119 Mm³, assignment v10 produces:

| Metric | Value |
|---|---:|
| Assigned volume | 118.977 Mm³/yr |
| Facilities used | 296 |
| Transport cost | 3.392 B CAD/yr |
| Transport cost intensity | 28.511 CAD/m³ |
| Mean road distance | 270.289 km |
| Median / p90 road distance | 147.2 / 635.7 km |
| Cross-border volume | 2.325 Mm³ (1.95% of assigned) |
| Proxy share of assigned volume | 54.08% |

V10 lowers transport cost by 2.990 B CAD/yr versus the v9 centroid assignment
and by 4.839 B CAD/yr versus the same v9 allocation re-evaluated at node
origins. Mean distance falls by 407.2 km (60.1%). A diagnostic distance-only
LP with no local-preference penalty reaches 255.8 km, and the unconstrained
nearest-facility weighted mean is 191.5 km; facility geography and finite
capacity therefore set the residual distance floor. No facility is moved.

## 5. Chain v5.1 — all cases

Chain v5.1 applies assignment v10 to the 100, 119, and 150 Mm³ documented
harvest cases. The 100 and 150 Mm³ node vectors scale each province's 119 Mm³
allocation, so within-province node shares are unchanged. Products and residue
are invariant at each case; only the facility distribution and transport
summary move.

| Metric | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|
| Facilities used | 270 | 296 | 333 |
| Transport cost (B CAD/yr) | 2.702 | 3.392 | 5.018 |
| Transport cost (CAD/m³) | 27.016 | 28.511 | 33.454 |
| Mean road distance (km) | 255.3 | 270.3 | 319.8 |
| Share ≥150 km | 45.67% | 48.62% | 55.97% |
| Share ≥300 km | 27.36% | 30.16% | 33.99% |
| Process residue (PJ) | 419.238 | 498.751 | 626.865 |

All 21 OSeCAN tier positions are unchanged. Transport cost is a chain summary
only and is **not** an OSeCAN input (`transport_cost_in_osecan=false`).

## 6. Transport cost v1

Transport cost v1 is a bottom-up **2026 CAD** truck-cost curve at a 52.5 m³
payload with a fully empty return. At 100 km one-way the base trip costs
604.00 CAD, or **11.50 CAD/m³ (2.83 CAD/GJ)**. At 500 km the base curve is
**51.45 CAD/m³ (12.64 CAD/GJ)**. The six operating components — fuel, driver,
maintenance, tires, capital, and insurance — are scaled by overhead. Diesel
and wage observations come from Statistics Canada; duty cycle, payload,
density, speeds, asset life, financing, and overhead are explicit engineering
assumptions.

At base parameters, transport cost alone equals the adopted OSeCAN tier
variable costs at approximately:

| Tier | Tier variable cost (face value) | Transport-only crossing |
|---|---:|---:|
| T1/T2 | 2.457 CAD/GJ | **85 km** (84.9 km) |
| T3 | 3.194 CAD/GJ | **115 km** (115.0 km) |
| T4 | 3.500 CAD/GJ | **127 km** (127.4 km) |

The comparison treats tier values at face value. The interface does not
document the tier currency or price year, so no FX conversion or deflator is
applied. The crossings are not a claim that transport cost replaces tier
costs in an OSeCAN run; they are a scale check only.

The curve excludes feedstock, processing, conversion, handling, storage,
product revenue, backhaul, border delay, customs, ferry, winter-road,
resource-road, queueing, and hours-of-service relay. It is a national-scope
component anchored on Vancouver diesel and BC wages, not a national observed
average. The low/base/high columns are a favourable-to-unfavourable stress
envelope, not a confidence interval.

## 7. Generic greenfield k-median screen

The generic screen holds existing capacity v3 and the 1.2× floor fixed (591
facilities, 273.468 Mm³/yr), keeps 118.977 Mm³ demand, and allows 421
positive-volume supply nodes in provinces with a road factor to host a
**generic, class-agnostic module with unlimited throughput**. It is a greedy
forward k-median/facility-location screen with a top-10 shortlist and exact
capacitated transport-LP re-evaluation at every trial and every k. Selected
sets are nested prefixes. It is **not a proven global optimum**.

At 119 Mm³:

| Metric | k=0 existing only | k=20 generic modules |
|---|---:|---:|
| Transport cost (B CAD/yr) | 3.220 | **1.749** |
| Transport cost (CAD/m³) | 27.07 | **14.70** |
| Mean road distance (km) | 255.8 | **132.0** |
| Module-served volume share | 0.0% | **30.26%** |

The k=20 screen saves **1.471 B CAD/yr (45.7%)** versus the transport-only
k=0 baseline and **1.643 B CAD/yr (48.4%)** versus committed v10. The first k
whose marginal cost reduction is below 1% of the k=0 cost is **k=13**;
diminishing returns therefore begin after k=13. The capacity-relaxed
lower-bound gap is 9.69%, and that bound ignores existing capacity, so it is
not an optimality gap.

The three-case k=20 results are:

| Case | k=0 cost / mean | k=20 cost / mean | k=20 module share | First k with marginal <1% |
|---|---:|---:|---:|---:|
| 100 Mm³ | 2.572 B / 242.3 km | **1.428 B / 127.7 km** | 30.3% | 13 |
| 119 Mm³ | 3.220 B / 255.8 km | **1.749 B / 132.0 km** | 30.3% | 13 |
| 150 Mm³ | 4.728 B / 300.4 km | **2.307 B / 138.8 km** | 34.7% | 13 |

The 100 and 150 Mm³ vectors are scaled from the 119 Mm³ provincial allocation;
they are not independent node-level inventories. Module locations are supply
node coordinates, not plant sites, and the screen asserts no technology,
product, residue, cost, or facility design.

## 8. Break-even annualized capital thresholds

The break-even threshold converts cumulative transport savings into the
maximum annualized capital charge that the saving could support:

```text
cumulative_threshold_k = (TC_0 - TC_k) / V_k
marginal_threshold_k   = (TC_{k-1} - TC_k) / (V_k - V_{k-1})
```

Units are **2026 CAD per (m³/yr)** of module-served volume. The cumulative
ranges and k=20 values are:

| Case | Cumulative range | Minimum | k=20 cumulative |
|---|---:|---:|---:|
| 100 Mm³ | 37.14–63.00 | 37.14 at k=19 | **37.80** |
| 119 Mm³ | 40.55–59.47 | 40.55 at k=4 | **40.87** |
| 150 Mm³ | 45.31–74.09 | 45.31 at k=18 | **46.47** |

At 119 Mm³ the median defined marginal threshold is 41.89 CAD per m³/yr;
marginal module volume is zero at k=14 and k=19 and thin at k=18. The range is
a **break-even threshold, not a capital cost estimate**. No capex, discount
rate, asset life, fixed operating cost, feedstock cost, processing cost,
product revenue, or financing assumption is introduced. The saving includes
re-routing and existing-facility effects, not a module-only revenue stream.

## 9. Class-specific siting follow-up

The class-specific screen applies the v5.1 E1/E2c province/class feedstock
shares to the node harvest and partitions the same capacity-v3 facilities by
class. At 119 Mm³ the class demand is:

| Class | Demand (Mm³/yr) |
|---|---:|
| SAW | 45.617 |
| PULP | 52.680 |
| PANEL | 7.467 |
| PELLET | 10.925 |
| BIO | 2.287 |

It selects k = 0..5 unlimited-throughput modules **per class** with exact
capacitated transport-LP re-evaluation. Existing-only class-partitioned k=0
is 5.181 B CAD/yr at 420.9 km. At **k=5 per class (25 modules total)** the
screen gives **3.179 B CAD/yr and 252.4 km**, with 27.9% module-served volume
and a 2.002 B CAD/yr (38.6%) reduction versus k=0.

Against the generic screen's **20 modules, 1.749 B CAD/yr, and 132.0 km**, the
class-constrained result is **+81.8%** in cost (25 versus 20 modules). At the
exact 20-module count match (class k=4 versus generic k=20), the class screen
is 3.314 B CAD/yr at 263.7 km, or +89.5%. The class restriction therefore
raises transport cost materially at a similar module count. This is a screen
diagnostic only: the screen adds no class capacity, technology, capital,
permitting, landbase, or facility-design claim.

## 10. Limitations and non-claims

- **Transport-only.** The screen changes no harvest, product, residue,
  capacity-v3 facility, or OSeCAN total. It is not a delivered-cost, market,
  or net-value model.
- **2026 CAD.** All dollar values are 2026 CAD model estimates. The tier
  comparison is face-value parity; tier currency and price year are
  undocumented.
- **Screening geometry.** Distances are haversine × province road factor, not
  observed routes. Nodes are RSYC tile-province centroids, not landings or
  plant gates. No full OSRM matrix is run.
- **Heuristic siting.** The k-median screen is a greedy forward prefix with a
  top-10 shortlist and exact LP re-evaluation, not a global optimum. No
  optimality-gap claim is made.
- **Unlimited-throughput modules.** Both generic and class-specific modules
  are generic, unlimited-throughput placeholders. They assert no technology,
  product, residue, capital cost, operating cost, or facility design.
- **No capital, permitting, landbase, technology, class-capacity, national,
  or policy claim.** The break-even threshold is not a capex or investment
  estimate. No revenue or net-value result is produced. The work is
  `not_investment_advice`.
- **Not official OSeCAN.** No official OSeCAN file, tier position, UI surface,
  or public release is changed. Every row remains `model_estimate`,
  `observed=false`, `policy_grade=false`, and `national_claim=false`.

## 11. Scope-decision stop rule

**Stop rule.** No further proxy or siting iteration is authorized under this
package until **Taco and the energy team confirm that facility siting and
transport are in C2070 scope**. This includes new or refined road factors,
additional OSRM sampling, new supply-node constructions, assignment variants,
greenfield k-median runs, break-even extensions, class-capacity designs, and
any associated model artifact.

Permitted while paused: typo fixes, link repairs, and factual corrections to
this documentation that do not change the frozen numbers, artifacts, or
claims.

If the scope decision is **out of scope**, this frozen addendum remains the
terminal record and no further work is implied. If the scope decision is
**in scope**, a new approved task and plan must precede any iteration, with an
explicit question, data boundary, and acceptance test. The existence of this
addendum is not evidence that the work is in C2070 scope.

## 12. Freeze and verification record

At this documentation freeze:

- Seven transport/siting manifests verify **64/64 output entries with 0
  mismatches** (assignment v10 20, transport cost v1 7, road audit v2 6,
  supply nodes v1 12, greenfield siting 7, break-even 3, case screen 9).
- The post-freeze class-specific screen verifies **129 validation checks**;
  its 9 focused tests and the 29 existing greenfield tests pass.
- The parent worktree has **no unexpected `D`** in either status column.
- Only documentation changes are made at this freeze: this addendum, pointers
  in the two model cards, and P7.40 entries in `ROADMAP.md` and
  `CHANGE_LOG.md`.
- Annotated tag `national-transport-siting-addendum-v1` marks the
  documentation commit. It does not move any earlier tag.

The artifact reports and manifests remain the source of record for all
numbers in this addendum. Reproduction commands are listed in
[`docs/model-card-national-transport-siting.md`](model-card-national-transport-siting.md#10-reproduce-and-verify).
