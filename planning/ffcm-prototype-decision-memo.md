# FFCM Prototype Decision Memo

## Purpose

This memo records the Phase 2 decision gate after building the BC mini-network
prototype. The prototype tests whether a ws3-shaped forest-fibre supply table
can pass through the FRESH soft-link contract into a downstream LP
network-flow model.

## Decision

Proceed with an independent FRESH implementation informed by FFCM, not a port
or full reverse engineering of the FCM Java runtime.

Recommended stance:

- Preserve and adapt selected FFCM schema concepts: spatial supply nodes,
  facilities, processing technologies, commodity cascades, transport links,
  market modules, and explicit solution-flow records.
- Preserve selected formulation semantics from the recovered notebook and
  CIRRELT reports: five-layer network graph, feedstock allocation, conversion
  coefficients, residue coproduct flows, market sales, and profit/net-value
  objective terms.
- Replace runtime and data payloads: use ws3 for forest estate supply, the
  `fresh_fibre` LP module for downstream allocation, accepted project data
  sources for production inputs, and open solver tooling for prototypes.
- Continue targeted archaeology only where it resolves a specific schema,
  coefficient, scenario, or reporting question.

## Evidence From Prototype

The BC mini-network prototype now has:

- Synthetic soft-link input tables matching
  `metadata/ffcm-archive/fresh-interface-contract.csv`.
- A reusable `fresh_fibre` module for graph construction, LP solve,
  validation, contract checks, unit checks, and typed CSV-row adapters.
- A PuLP/CBC solve with 37 graph nodes, 45 graph arcs, 28 LP variables, 43
  constraints, and 24 positive solution-flow records.
- An optimal diagnostic objective value of `6,140,832` CAD.
- Generated `solution_flow`, `solution_summary`, objective, variable,
  constraint, graph, and contract-validation tables.
- Regression tests covering the positive end-to-end path plus negative
  contract and unit-registry failures.

## What This Proves

- The proposed FRESH soft-link table family is sufficient for a minimal
  downstream forest-fibre allocation solve.
- The recovered notebook's five-layer graph pattern can be represented with
  explicit CSV tables instead of packed variable names and global notebook
  state.
- The downstream LP can be rebuilt with open Python tooling while avoiding FCM
  Java, CPLEX, lp_solve, runtime jars, database drivers, and visualization
  dependencies.
- Contract, unit, provenance, and solution-flow validation can be made
  repeatable before production data enters the workflow.

## What This Does Not Prove

- It does not reproduce the FFCM national basecase.
- It does not validate any production BC or Canadian facility, supply,
  transport, price, or demand quantity.
- It does not yet connect to a live ws3 forest estate run.
- It does not resolve data reuse rights for FFCM archive payloads or BC Fibre
  Database workbooks.
- It does not establish national-scale performance or solver choice.

## Open Gaps

The machine-readable gap register is
`metadata/ffcm-archive/prototype-gap-register.csv`.

Highest-priority gaps:

- Build the first real ws3-to-`supply_offer` exporter.
- Select accepted diagnostic inventory/yield data for a small real geography.
- Resolve facility/capacity, transport, and price/cost provenance.
- Clarify archive/workbook reuse rights before extracting production values.
- Decide unit-conversion execution policy before accepting non-canonical input
  quantities.

## Near-Term Recommendation

The next implementation phase should not deepen the synthetic LP itself unless
it answers a scaling blocker. Instead, move one step upstream and one step
outward:

- Upstream: produce a tiny ws3 run that emits the current `supply_offer`
  contract.
- Outward: replace one synthetic data family at a time with accepted
  diagnostic inputs, starting with inventory/yield and facility/capacity
  provenance.

This keeps the FRESH path independent, auditable, and grounded in the useful
FFCM evidence without inheriting the closed or stale parts of the original
system.
