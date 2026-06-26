# Network-Flow Seed Rebuild Plan

## Purpose

The recovered gist notebook `phase2_ua06451.ipynb` is the seed for rebuilding
the ws3-compatible forest-sector supply-chain network-flow optimization model.
The notebook should be treated as archaeological source code: it captures model
semantics, but it is not a clean module and should not be run directly as a
production dependency.

The formulation narrative is CIRRELT-2018-24 section 2.1.

## Architecture Distinction

There are two related but distinct optimization-model evidence streams in this
archive-analysis phase:

- FFCM generated LPs: national-scale FCM/FCM-style flow evidence for logs,
  residues, facilities, commodities, and markets.
- Recovered ws3-compatible network-flow notebook: Quebec UA 064-51 model that
  links a ws3/Woodstock harvest solution to a five-layer LP graph.

FRESH should use the notebook as the seed for the downstream LP model rebuild,
and use FFCM LP/config evidence as schema and validation context.

## Recovered Model Core

The notebook model uses five node layers:

- `src`: source nodes mapped to harvest decisions / management-area action
  records.
- `dsp`: dispatch nodes, one per source node.
- `cdt`: commodity nodes, using MERIS commodity classes.
- `prc`: processor nodes, using mill identifiers.
- `snk`: sink nodes, one per processor.

The main arc families are:

- `src -> dsp`: harvested area, bounded by available area.
- `dsp -> cdt`: conversion from harvested area to commodity volume.
- `cdt -> prc`: merchantable fibre allocation to processors.
- `prc -> snk`: processor capacity/sink flow.
- `prc -> prc`: hardwood chip coproduct flows from selected processors to chip
  consumers.

The core objective is net value-creation potential. The notebook keeps
component metrics for fixed cost, harvest cost, silvicultural credit, stumpage,
transport, product value, guaranteed-allocation/rente charges, and subsidies.

## Rebuild Strategy

1. Preserve semantics, not notebook structure.
2. Replace global variables and hard-coded local files with explicit dataframe
   inputs matching `fresh-interface-contract.csv`.
3. Replace packed node strings with typed tables for nodes, arcs, commodities,
   technologies, costs, capacities, and scenarios.
4. Split `compile_base_model` into pure steps:
   - validate input tables
   - build graph tables
   - build coefficient tables
   - build solver model
   - attach scenario modifiers
5. Split solution compilation into:
   - `solution_flow`
   - `solution_summary`
   - objective-component tables
6. Keep Gurobi as a possible backend if available, but design the model builder
   so a solver abstraction can be added.

## Module Sketch

Proposed module names for a future implementation:

- `fresh_fibre.schema`: typed dataclasses or dataframe schemas.
- `fresh_fibre.graph`: node and arc construction.
- `fresh_fibre.coefficients`: cost, value, conversion, and capacity
  coefficients.
- `fresh_fibre.model`: solver-model construction.
- `fresh_fibre.scenarios`: area sweeps, subsidies, capacity changes, and policy
  switches.
- `fresh_fibre.solution`: solution extraction and summary metrics.
- `fresh_fibre.validation`: schema checks and small network tests.

## Immediate Next Actions

- [x] Use `metadata/ffcm-archive/network-flow-seed/rebuild-function-inventory.csv`
  to decide the minimum functions to refactor first.
- [x] Use `metadata/ffcm-archive/network-flow-seed/rebuild-schema-crosswalk.csv`
  to revise `fresh-interface-contract.csv` where the five-layer graph requires
  fields not yet represented.
- [x] Create tiny synthetic input tables for the BC diagnostic mini-network.
- [x] Implement a solver-independent graph/table builder before wiring any
  solver.
- [x] Convert generated graph arcs into coefficient tables for a tiny LP solve.
- [x] Add a solver backend for the mini-network and write `solution_flow` /
  `solution_summary` outputs.
- [ ] Refactor graph building and LP solving out of archive-analysis scripts
  into a reusable FRESH module with typed inputs.
- [ ] Replace synthetic fixture quantities with accepted diagnostic or
  production data sources after provenance and rights review.

## BC Mini-Network Graph Fixture

The first solver-independent prototype fixture lives under
`metadata/ffcm-archive/prototype-inputs/bc-mini/`. It contains one synthetic
scenario and period, three diagnostic supply areas, primary log supply,
logging-residue supply, sawmill technologies, pulp/pellet/bioenergy consumers,
an export option, transport links, markets, and provenance metadata.

`scripts/ffcm_archive/build_prototype_graph.py` validates the input tables and
writes generated graph tables to the fixture's `generated/` directory. The
current graph has 37 nodes and 45 arcs across the recovered five-layer pattern:
`src`, `dsp`, `cdt`, `prc`, and `snk`.

`scripts/ffcm_archive/solve_bc_mini_lp.py` uses PuLP/CBC to solve the first
tiny profit-maximizing downstream LP prototype. It writes variable,
constraint, objective-term, `solution_flow`, and `solution_summary` tables to
the same generated directory. The current solve is optimal with 28 variables,
43 constraints, and 24 positive solution-flow records.

`scripts/ffcm_archive/validate_bc_mini_solution.py` checks graph counts, ID
uniqueness, solver status, nonnegative flows, supply/route/technology/market
bounds, and the expected positive pathways for sawmills, residues, bioenergy,
and profitable log export.
