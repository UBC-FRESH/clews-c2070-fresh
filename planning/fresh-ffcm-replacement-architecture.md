# FRESH FFCM Replacement Architecture

## Purpose

This note translates the first FFCM archive-analysis pass into an initial FRESH
implementation architecture. It assumes FRESH should preserve the useful FFCM
model concepts while replacing the closed or stale runtime stack:

- Replace the upstream forest-estate/log-supply engine with `ws3`.
- Replace the FCM Java/CPLEX/lp_solve supply-chain runtime by rebuilding the
  ws3-compatible LP network-flow model from the recovered notebook seed.
- Preserve reusable FFCM schema concepts, commodity vocabulary clues, market
  decomposition, and generated-LP formulation evidence.

## Architecture Spine

The proposed FRESH spine is:

1. National source data foundation
2. `ws3` forest-estate model
3. `ws3` output normalization and disaggregation layer
4. LP-ready supply/commodity/network/technology input tables
5. Downstream profit-maximizing LP network-flow model
6. Scenario, provenance, validation, and reporting layer

The clean handoff is step 3 to step 4. That handoff is the soft-link contract
documented in `metadata/ffcm-archive/fresh-interface-contract.csv`.

## ws3-Side Replacement

The ws3-side model should be responsible for forest state and supply:

- Spatial modelling units: stand, polygon, FMU, TSA/management unit, or a
  project-specific crosswalk to LP supply nodes.
- Stand state variables: species composition, age, yield stratum/site class,
  disturbance history, management eligibility, protected/access status, and
  carbon/climate attributes where needed by CLEWs.
- Dynamics: growth/yield transitions, disturbance transitions where modelled,
  harvest scheduling, silvicultural treatments, and period-to-period inventory
  updates.
- Outputs: available and selected harvest volumes, assortment/log classes,
  logging residue, roadside supply cost, spatial supply node, time period, and
  scenario metadata.

The ws3 output should not try to mimic the FCM Java runtime. It should emit
LP-ready supply records with explicit units and provenance.

## Disaggregation and Value Layer

The CIRRELT reports are the method anchor for the bridge between long-term
forest supply and short-term industrial allocation:

- CIRRELT-2017-43: disaggregation coefficients for linking planning scales.
- CIRRELT-2018-23: value-creation potential indicators and retrofit logic.
- CIRRELT-2018-24: downstream network-flow simulation/optimization.

For FRESH, this layer should convert ws3 output into supply offers by
commodity/assortment class and attach any required cost, value, density, or
conversion metadata before LP solve.

## Downstream LP Replacement

The downstream LP should preserve these FFCM concepts where useful:

- Product-market split: primary logs; woodchips; hog fuel; secondary logs;
  sawdust/shavings; logging residue.
- Network entities: FMU/supply nodes, facilities, processing lines, import
  sources, export sinks, transload/water/transport nodes, and markets.
- Technology records: feedstock, primary product, infeed rate, outfeed or
  byproduct flows, throughput/capacity, and operating cost.
- Transport records: origin, destination, mode, distance, speed or travel time,
  cost, and handled commodity classes.
- Objective semantics: profit or net value, with positive revenue/value terms
  and negative supply, processing, and transport cost terms.

The generated FCM LPs should be treated as formulation evidence, not as a model
artifact to run or reproduce verbatim.

The likely surviving code seed for the ws3-compatible downstream model is the
GitHub gist `gparadis/bcc357dbeaab2c8d247776424c51c25a`, which contains
`phase2_ua06451.ipynb`. The notebook has been downloaded locally under ignored
`tmp/` space and extracted into
`metadata/ffcm-archive/network-flow-seed/`. The network-flow model formulation
is described in section 2.1 of `tmp/CIRRELT-2018-24.pdf`.

The seed notebook's downstream model uses a five-layer graph
(`src -> dsp -> cdt -> prc -> snk`) with optional processor-to-processor
coproduct arcs. See `planning/network-flow-seed-rebuild-plan.md` for the
rebuild plan and `metadata/ffcm-archive/network-flow-seed/` for function and
schema crosswalks.

## Initial Table Contract

The initial contract is a set of table families:

- `scenario`
- `spatial_unit`
- `commodity`
- `supply_offer`
- `network_node`
- `technology`
- `technology_flow`
- `transport_link`
- `transporter`
- `market`
- `import_offer`
- `export_sink`
- `run_provenance`
- `solution_flow`
- `solution_summary`

See `metadata/ffcm-archive/fresh-interface-contract.csv` for grain, required
fields, optional fields, evidence sources, and unit notes.

## Data Disposition

The FFCM archive is useful for architecture and prototyping, but not all data
should be treated as reusable production input. The initial disposition is in
`metadata/ffcm-archive/data-source-disposition.csv`.

High-level stance:

- Prototype seed: parsed FCM schemas, generated LP summaries, small synthetic
  subsets, and possibly selected basecase records if licensing permits.
- Rebuild: national inventory, disturbance, roads, facilities, cost tables,
  market/prices, and transport distances from accepted project sources.
- Avoid as dependency: FCM Java runtime, CPLEX jar, visualization/runtime jars,
  and proprietary or license-unclear workbook payloads.

## Next Design Checks

- Recover the downstream LP model from the gist notebook seed and compare its
  implied input schema against `fresh-interface-contract.csv`.
- Decide whether FRESH needs one national commodity vocabulary or separate
  forestry, bioenergy, and CLEWs-accounting vocabularies linked by crosswalks.
- Choose the first prototype geography and time step.
- Define unit-conversion conventions for `m3`, `odt`, `bdmt`, `t`, `kg`, `GJ`,
  `MWh`, and product-specific board/area units.
- Extend the current unit checks to explicit conversion registries for `bdmt`,
  `t`, `kg`, `MWh`, and product-specific board/area units before scaling beyond
  the synthetic fixture.
