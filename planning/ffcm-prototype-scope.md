# FFCM-Informed Prototype Scope

## Purpose

This note scopes the first end-to-end prototype implied by Phase 2. The goal is
not to reproduce the FFCM basecase. The goal is to test whether a ws3-shaped
forest supply table can flow through the proposed FRESH soft-link contract into
the downstream LP network-flow model.

## Prototype Choice

Recommended first prototype: BC diagnostic mini-network.

Rationale:

- The archive contains rich BC evidence across FMUs, sawmills, pulp/paper,
  pellets, bioenergy, plywood, OSB, routes, commodity flows, and BC Fibre
  Database context.
- BC is strategically relevant to UBC FRESH and easier to reason about with
  local forestry domain knowledge.
- A province-scale diagnostic subset is still small enough to make table
  schemas, units, and flow logic visible.

The prototype should use synthetic or clearly labelled diagnostic supply
records shaped like ws3 output. FFCM basecase values can be used to test schema
coverage and variable-family logic, but should not be treated as production
inputs unless provenance and reuse rights are resolved.

## Minimal Network

The first prototype should include:

- 2 to 5 supply nodes
- 2 to 5 sawmill or primary-processing nodes
- 1 pulp/paper or NSP/MDF consumer
- 1 pellet or bioenergy consumer
- Optional export sink
- Log, woodchips, sawdust/shavings, hog fuel, and logging residue commodities
- One scenario and one period

## Required Input Tables

Minimum tables from `metadata/ffcm-archive/fresh-interface-contract.csv`:

- `scenario`
- `spatial_unit`
- `commodity`
- `supply_offer`
- `network_node`
- `technology`
- `technology_flow`
- `transport_link`
- `market`
- `run_provenance`

Optional for the first pass:

- `transporter`
- `import_offer`
- `export_sink`
- `solution_flow`
- `solution_summary`

## Acceptance Criteria

The first prototype is successful if it can:

- Load the soft-link tables without implicit unit assumptions.
- Convert ws3-shaped supply offers into downstream LP input.
- Solve a tiny profit-maximizing allocation model.
- Produce solution-flow records with explicit source node, destination node,
  source commodity, product commodity, quantity, unit, and objective
  contribution fields.
- Compare solved variable families against the FFCM LP-forensics families at a
  qualitative level.

## Known Non-Goals

- Reproduce the FFCM national basecase.
- Run the FCM Java runtime.
- Use CPLEX, lp_solve, or FCM generated LP files as dependencies.
- Commit GIS layers, archive workbooks, runtime jars, or large model outputs.
- Treat BC Fibre Database workbook values as reusable production data without
  rights clarification.

## Immediate Next Actions

1. Rebuild the existing downstream LP model from the recovered gist notebook
   seed and document its current/implied input schema.
2. Crosswalk the recovered LP input schema to
   `metadata/ffcm-archive/fresh-interface-contract.csv`.
3. Create tiny synthetic node, arc, commodity, coefficient, and `supply_offer`
   tables that exercise the five-layer graph.
4. Build or adapt a loader that maps the soft-link contract into the rebuilt LP
   model.
5. Solve the mini-network and write `solution_flow` output.

Status:

- Items 1 and 2 are covered by
  `planning/network-flow-seed-rebuild-plan.md` and
  `metadata/ffcm-archive/network-flow-seed/`.
- Item 3 now has a synthetic fixture at
  `metadata/ffcm-archive/prototype-inputs/bc-mini/`.
- Item 4 has a first solver-independent graph builder at
  `fresh_fibre.graph`, with a compatibility CLI at
  `scripts/ffcm_archive/build_prototype_graph.py`.
- Item 5 has a first PuLP/CBC solve at
  `fresh_fibre.lp`, with validation in `fresh_fibre.validation`.
- The first upstream bridge now starts from synthetic normalized
  `ws3_harvest_output.csv` rows and exports the current `supply_offer`
  contract through `fresh_fibre.ws3_export`.

Next step:

- Use `planning/ffcm-prototype-decision-memo.md` and
  `metadata/ffcm-archive/prototype-gap-register.csv` to drive the next phase:
  replace the synthetic ws3 harvest-output fixture with a tiny real ws3 run and
  then replace one synthetic data family at a time with accepted diagnostic
  inputs.
