# FFCM Archive Analysis Plan

## Purpose

This phase is dedicated to unpacking the transferred Forest Fibre Cascade Model
(FFCM/FCM) project archive and turning it into implementation knowledge for the
FRESH forest-fibre-supply component of CLEWs-C2070.

The working hypothesis is that FRESH can reuse the broad FFCM architecture while
replacing closed, proprietary, or stale runtime components. The likely
replacement stack is:

- `ws3` for the forest-estate modelling layer: forest state transitions,
  growth/yield, harvest scheduling, log/assortment generation, and spatial
  supply handoff.
- The existing ws3-compatible LP network-flow model for the forest-sector
  supply-chain layer: facility consumption, transport, conversion, commodity
  flows, product markets, and profit-maximizing response to feedstock scenarios.

The analysis should stay anchored in Ghafghazi et al. 2017, "Estimating Mill
Residue Surplus in Canada: A Spatial Forest Fiber Cascade Modeling Approach",
so that detailed archive work remains connected to the national-scale model
story: forest fibre supply, primary processing, residue generation, transport,
markets, and surplus estimation.

## Data Boundary

The transferred archive currently lives in local ignored `tmp/` space. Do not
commit raw archive payloads, GIS layers, model outputs, spreadsheet workbooks,
or executable/runtime files to the parent repository.

Commit only lightweight planning and metadata products here. If provenance
storage is needed, evaluate annexing the original archive ZIP in the `data`
submodule after reading `docs/data-repository-runbook.md`.

Current lightweight metadata products:

- `metadata/ffcm-archive/archive-manifest.csv`
- `metadata/ffcm-archive/directory-summary.csv`
- `metadata/ffcm-archive/document-index.csv`
- `metadata/ffcm-archive/fcm-evidence-index.csv`
- `metadata/ffcm-archive/architecture-crosswalk.csv`
- `metadata/ffcm-archive/parsed/`
- `metadata/ffcm-archive/lp-forensics/`
- `metadata/ffcm-archive/runtime-dependencies.csv`
- `metadata/ffcm-archive/implementation-boundary.csv`
- `planning/fresh-ffcm-replacement-architecture.md`
- `metadata/ffcm-archive/fresh-interface-contract.csv`
- `metadata/ffcm-archive/data-source-disposition.csv`
- `planning/ffcm-prototype-scope.md`
- `metadata/ffcm-archive/prototype-scope.csv`
- `metadata/ffcm-archive/network-flow-seed/`
- `planning/network-flow-seed-rebuild-plan.md`
- `metadata/ffcm-archive/prototype-inputs/bc-mini/`

Repeatable parser:

- `scripts/ffcm_archive/parse_fcm_configs.py`
- `scripts/ffcm_archive/summarize_lp_models.py`
- `scripts/ffcm_archive/inventory_runtime_dependencies.py`
- `scripts/ffcm_archive/extract_network_flow_seed_notebook.py`
- `scripts/ffcm_archive/build_prototype_graph.py`

## Initial Archive Map

The initial scan found 1,683 files under `tmp/`. Major evidence zones include:

- `tmp/FCM/`: FCM runtime release, presentations, test configs/logs, generated
  report artifacts, and carbon/scenario model directories.
- `tmp/FCM/Carbon/01-Basecase/`: structured model inputs including `FMUs.cfg`,
  `Facilities.cfg`, `Routes.cfg`, `Processinglines.cfg`, `Transporters.cfg`,
  `Markets.cfg`, `Imports.cfg`, `Transload.cfg`, `Groups.cfg`, and related
  definitions.
- `tmp/FCM/Carbon/02-Inc/`: include files for `Commodities.inc`,
  `Constraints.inc`, and `Throughput.inc`.
- `tmp/FCM/Carbon/*/*.lp`: generated LP model artifacts that can be inspected
  to infer variables, constraints, objective terms, and flow-balance logic.
- `tmp/FCM/FCM_Releases/`: Java runtime and solver dependencies, including
  CPLEX and lp_solve jars.
- `tmp/External/CFS/`: project documents, analyses, GIS inputs, result maps,
  Sankey diagrams, scenario outputs, and supporting data.
- `tmp/BC Fibre Database Benchmark 2018 V3  August/`: BC Fibre Database
  benchmark workbooks, regional maps, pulp mill detail module, tenure-to-
  capacity module, and user guides.
- Root PDFs and reports: the Ghafghazi et al. article plus the CIRRELT reports
  documenting the replacement network-flow modelling logic.

## Anchor Documents

Use `metadata/ffcm-archive/document-index.csv` as the curated document index.

Primary architecture anchor:

- Ghafghazi et al. 2017, "Estimating Mill Residue Surplus in Canada: A Spatial
  Forest Fiber Cascade Modeling Approach".

Network-flow and ws3-linkage method anchors:

- CIRRELT-2017-43: disaggregation coefficients for linking long- and short-term
  planning models.
- CIRRELT-2018-23: value-creation potential indicator retrofit logic.
- CIRRELT-2018-24: hybrid simulation-optimization and profit-maximizing network
  flow model. Section 2.1 describes the network-flow optimization model
  formulation and compilation.

Recovered code seed:

- GitHub gist `gparadis/bcc357dbeaab2c8d247776424c51c25a`, containing
  `phase2_ua06451.ipynb`, may be the only surviving copy of the
  ws3-compatible forest-sector supply-chain network-flow optimization model
  code referenced for FRESH.
- The notebook was downloaded to ignored local path
  `tmp/gist-network-flow/phase2_ua06451.ipynb`.
- Code, markdown, and a cell index were extracted to
  `metadata/ffcm-archive/network-flow-seed/` for rebuild planning.
- Function and schema crosswalks were added to support refactoring the notebook
  into a clean FRESH model module.

Supporting forest-estate method anchor:

- CIRRELT-2017-34: diameter distribution models relevant to stand-to-log and
  assortment estimation.

Regional benchmark/context documents:

- BC Fibre Database general, pulp mill detail, and tenure-to-capacity user
  guides.

## Work Packages

### WP1: Architecture Crosswalk

Build a table that maps the FFCM conceptual architecture to local evidence and
FRESH replacement choices.

Initial scaffold: `metadata/ffcm-archive/architecture-crosswalk.csv`.

Minimum columns:

- `ffcm_module`
- `conceptual_role`
- `archive_evidence`
- `candidate_fresh_component`
- `replacement_status`
- `open_questions`

Expected modules:

- Forest estate / log supply
- FMUs and spatial supply nodes
- Transport routes and modes
- Facilities and processing lines
- Commodity cascade and residues
- Markets, imports, and exports
- Scenario definitions
- Calibration/validation outputs
- Reporting/visualization

### WP2: Structured Config Parsing

Parse the FCM basecase `.cfg` and `.inc` files into normalized tables. Prioritize
the carbon/basecase files first because they are compact enough to inspect and
rich enough to reveal the schema.

Initial parse queue: `metadata/ffcm-archive/fcm-evidence-index.csv`.

Initial parse targets:

- `FMUs.cfg`
- `Facilities.cfg`
- `Routes.cfg`
- `Processinglines.cfg`
- `Transporters.cfg`
- `Commodities.inc`
- `Throughput.inc`
- `Constraints.inc`

Initial parse outputs:

- `parse-summary.csv`
- `fmu_supply.csv`
- `facilities.csv`
- `facility_process_groups.csv`
- `facility_minimum_production.csv`
- `processing_lines.csv`
- `processing_line_flows.csv`
- `routes.csv`
- `transporters.csv`
- `transporter_materials.csv`
- `commodities.csv`
- `markets.csv`
- `market_reporting_groups.csv`
- `import_supply.csv`
- `transload_costs.csv`

First parse findings:

- The basecase declares 733 FMU supply entities, each with log and logging-
  residue content, supply-cost, and minimum-supply records.
- `Facilities.cfg` is a network-node declaration file, not only a mill list:
  it includes 315 sawmill entities, 62 pulp/paper entities, 39 bioenergy
  entities, 39 pellet entities, 28 plywood entities, 14 OSB entities, 11 NSP
  entities, plus FMU-like supply nodes, import sources, export sinks, water
  nodes, and transport-link nodes.
- The basecase includes 957 processing lines. The main feedstocks are log,
  woodchips, sawdust/shavings, logging residue, and hog fuel.
- `Commodities.inc` defines 74 commodities across mass-based solid, volumetric
  solid, and energy matter states, with prices, densities, and energy densities
  where available.
- `Routes.cfg` defines 38,390 route entities with 180,004 encoded segment
  classes across road, rail, and water modes.
- `Markets.cfg` exposes six named product markets: primary log, woodchips,
  hog fuel, secondary log, sawdust/shavings, and logging residue.

### WP3: LP Model Forensics

Inspect generated `.lp` files to infer formulation structure without depending
on the Java runtime. Start with the small report examples, then move to the
large basecase LP files.

Questions to answer:

- What are the decision-variable families?
- What commodities and node types appear in flow-balance constraints?
- How are capacity, throughput, mutually exclusive processes, imports, and
  market demand represented?
- Is residue surplus explicit, inferred from unused supply, or reported through
  post-processing?
- Which formulation pieces map directly to the existing downstream LP model?

Initial LP forensics outputs:

- `lp-model-summary.csv`
- `lp-variable-families.csv`

First LP findings:

- The exported LP files are continuous CPLEX LPs in the inspected examples:
  no `Bounds`, `Binaries`, or `Generals` sections were present in the sampled
  small report LPs or the two large basecase LPs.
- `Log_allocation_model.lp` has 34,004 objective variables and 1,837 numbered
  constraints. All objective variables are FMU log flows to sawmills, plywood
  mills, OSB mills, or export sinks. Top product families are softwood lumber,
  hardwood/east lumber, plywood, veneer, OSB, and sawlog export commodities.
- `Secondary_Log_and_Residue_allocation_model.lp` has 20,065 objective
  variables and 3,857 numbered constraints. Source commodities include
  woodchips, sawdust/shavings, logging residue, logs, and hog fuel. Destination
  classes include pulp/paper, pellet, bioenergy, NSP/MDF, export sinks, and a
  small import/network-node class.
- Both large basecase LPs contain positive and negative objective coefficients,
  which supports interpreting the objective as net value/profit after revenue,
  processing, transport, and supply-cost terms.
- Small report LPs are useful pedagogically but use simplified variable names
  that do not encode the full commodity vocabulary.
- Constraint senses are visible, but constraint names are exported as numeric
  `cN` labels. Detailed constraint-family reconstruction must therefore join
  LP rows back to config evidence rather than relying on semantic LP labels.

### WP4: Replacement Interface Design

Define a clean soft-link contract from ws3 to the downstream LP network-flow
model.

Minimum fields to specify:

- Time period and scenario identifiers
- Spatial supply keys: FMU, supply node, transport node, or equivalent
- Species group, product/assortment class, grade, and residue class
- Volume, mass, moisture, energy, and conversion units
- Roadside, delivered, and mill-gate cost fields
- Transport distance/time/cost fields
- Provenance fields linking records back to source inventory and model run

Initial replacement design outputs:

- `planning/fresh-ffcm-replacement-architecture.md`
- `metadata/ffcm-archive/fresh-interface-contract.csv`
- `metadata/ffcm-archive/data-source-disposition.csv`

Initial table families:

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

First replacement-design findings:

- The ws3 side should own forest state, growth/yield transitions, harvest
  scheduling, assortment/log generation, logging-residue generation, and supply
  provenance.
- The LP side should own industrial allocation, facility/process capacities,
  market response, transport, conversion, imports/exports, and net-value
  optimization.
- The soft-link should be a tabular supply-offer contract, not a packed FCM
  variable-name convention. Source node, commodity, assortment, period, unit,
  cost, and provenance should be explicit fields.
- FFCM basecase values can seed diagnostic prototypes, but production FRESH
  values should be rebuilt or validated from accepted national data sources.

## Runtime Boundary Notes

The FCM release bundle under `tmp/FCM/FCM_Releases/` appears to be a Java
application/runtime distribution. The main `FCM-1908.jar` manifest points to
`ima.IMA` as the main class and contains JaamSim classes, suggesting FCM was
implemented on or packaged with the JaamSim simulation framework.

The dependency inventory flags these boundaries:

- CPLEX Java API: proprietary solver dependency and a hard no-port boundary for
  FRESH.
- lp_solve Java binding: legacy/open solver evidence, but not a preferred FRESH
  dependency if the existing downstream LP model already has a modern solver
  stack.
- PostgreSQL and MongoDB Java drivers: evidence that the FCM runtime may read
  from relational and/or document stores.
- NASA WorldWind plus JOGL/GlueGen native jars: GIS/3D visualization runtime
  evidence rather than core model logic to preserve.
- Apache commons and json-simple jars: generic Java utility dependencies.

The practical FRESH stance is to preserve schemas, data contracts, and
formulation evidence from the archive, but not to depend on the FCM Java
runtime, CPLEX jar, visualization stack, or database drivers.

See `metadata/ffcm-archive/implementation-boundary.csv` for the current
preserve/adapt/rebuild/avoid matrix.

### WP5: Prototype Decision Gate

Use the architecture crosswalk and parsed evidence to recommend one of three
paths:

- Reverse-engineer the FFCM architecture closely, replacing engines only.
- Adapt selected FFCM schemas and reporting patterns while designing FRESH
  independently.
- Treat FFCM as background evidence only and proceed with a fresh ws3 + LP
  architecture.

Initial prototype scope:

- Recommended first prototype is a BC diagnostic mini-network with synthetic or
  clearly labelled diagnostic ws3-shaped supply records.
- Minimum network is 2 to 5 supply nodes, 2 to 5 primary-processing nodes, one
  pulp/paper or NSP/MDF consumer, one pellet or bioenergy consumer, optional
  export sink, and one scenario/period.
- Minimum commodities are log, woodchips, sawdust/shavings, hog fuel, and
  logging residue.
- Immediate blocker is not FFCM evidence; it is locating and crosswalking the
  recovered downstream LP model's current/implied input schema.

## Open Questions

- Which FCM components are proprietary, licensed, or otherwise unsuitable for
  reuse beyond metadata-level inspection?
- Was the national forest estate model stored in this archive, or only the
  downstream FCM/supply-chain side plus supporting CFS data?
- Are the root `Saeed_data.zip` and `FMU.xlsm` files source-of-truth inputs,
  snapshots, or handoff artifacts?
- Which FFCM spatial units should FRESH preserve: FMUs, transport nodes,
  facility nodes, province/region aggregates, or a new ws3-native geography?
- Which FFCM calibration targets are essential for credibility in CLEWs-C2070:
  2013 production, mill residue availability, commodity flows, facility
  capacity utilization, transport distances, or regional surplus estimates?
