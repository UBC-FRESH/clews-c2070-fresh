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

Repeatable parser:

- `scripts/ffcm_archive/parse_fcm_configs.py`

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
  flow model.

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

### WP5: Prototype Decision Gate

Use the architecture crosswalk and parsed evidence to recommend one of three
paths:

- Reverse-engineer the FFCM architecture closely, replacing engines only.
- Adapt selected FFCM schemas and reporting patterns while designing FRESH
  independently.
- Treat FFCM as background evidence only and proceed with a fresh ws3 + LP
  architecture.

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
