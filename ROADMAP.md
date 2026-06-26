# Roadmap

## Phase 1: Foundation and Model Development

Target window: May 2026 to March 2027.

Primary goal: build the national forest data foundation required for
forest-biomass supply modelling before model implementation work depends on it.

### P1.1 Repository Bootstrap

- [x] Add parent-repo operating notes and data-boundary guidance.
- [x] Add DataLad/submodule runbook for the future `data` repository.
- [x] Add Tyler onboarding brief and initial work package.
- [x] Add initial source inventory and curated dataset registry templates.
- [x] Create or confirm `UBC-FRESH/clews-c2070-fresh-data`.
- [x] Initialize the data repository as a DataLad dataset with git-annex.
- [x] Configure and smoke-test the `arbutus-s3` special remote.
- [x] Link the data repository as the parent repo's `data` submodule.

### P1.2 National Source Discovery

- Governing issue: `#1`

- [ ] Review candidate source families in `metadata/source-inventory.csv`.
- [ ] For each source, record publisher, access URL, license, spatial coverage,
  temporal coverage, update cadence, data format, and acquisition status.
- [ ] Promote accepted sources into `metadata/required-datasets.yaml`.
- [ ] Open issues for sources needing access requests, license review, or
  manual acquisition.

### P1.3 Data Archive and Provenance

- [x] Define the `data` repository directory contract.
- [ ] Archive raw source payloads with checksums and acquisition manifests.
- [ ] Track preprocessing scripts or notebooks in the parent repo when they are
  source code, and write outputs to the DataLad dataset.
- [x] Validate a fresh clone can retrieve at least one small annex-backed
  artifact from Arbutus S3.

### P1.4 First Derived Products

- [ ] Create a national administrative/provincial boundary baseline layer.
- [ ] Create a source crosswalk for inventory, disturbance, transport,
  industrial facility, protected-area, and carbon/climate layers.
- [ ] Produce the first modelling-readiness assessment for WS3/FEMIC expansion.

## Phase 2: FFCM Archive Reverse Engineering

Target window: June 2026 to October 2026.

Primary goal: unpack the transferred Forest Fibre Cascade Model project archive
well enough to decide whether the FRESH forest-fibre-supply implementation can
reuse the FFCM architecture while replacing closed or proprietary model engines
with ws3 for forest-estate dynamics and the existing ws3-compatible LP
network-flow model for downstream forest-sector supply-chain response.

Governing planning note: `planning/ffcm-archive-analysis.md`.

Metadata surfaces:

- `metadata/ffcm-archive/archive-manifest.csv`: generated file-level manifest
  for the local `tmp/` archive copy.
- `metadata/ffcm-archive/directory-summary.csv`: generated scan-level summary
  by archive zone and major directory group.
- `metadata/ffcm-archive/document-index.csv`: curated index of anchor
  publications and user guides.
- `metadata/ffcm-archive/fcm-evidence-index.csv`: curated parse/forensics
  queue for core FCM configuration, generated LP, and runtime evidence.
- `metadata/ffcm-archive/architecture-crosswalk.csv`: initial crosswalk from
  FFCM conceptual modules to archive evidence and candidate FRESH components.
- `metadata/ffcm-archive/parsed/`: generated normalized CSV tables parsed from
  core FCM configuration and include files.
- `metadata/ffcm-archive/lp-forensics/`: generated summaries of selected FCM
  LP model exports and their variable families.
- `metadata/ffcm-archive/runtime-dependencies.csv`: generated inventory of FCM
  Java runtime jars and dependency-boundary categories.
- `metadata/ffcm-archive/implementation-boundary.csv`: curated preserve/adapt/
  replace/avoid matrix for FFCM evidence and FRESH implementation choices.
- `planning/fresh-ffcm-replacement-architecture.md`: initial FRESH architecture
  note translating FFCM evidence into a ws3 plus LP replacement design.
- `metadata/ffcm-archive/fresh-interface-contract.csv`: initial soft-link table
  contract between ws3 outputs and downstream LP inputs/outputs.
- `metadata/ffcm-archive/data-source-disposition.csv`: initial disposition of
  archive evidence into prototype seeds, production rebuilds, and boundaries.
- `planning/ffcm-prototype-scope.md`: initial Phase 2 prototype decision-gate
  scope.
- `metadata/ffcm-archive/prototype-scope.csv`: compact prototype component
  choices and evidence links.
- `metadata/ffcm-archive/network-flow-seed/`: extracted code, markdown, and
  semantic notes from the recovered gist notebook seed for the ws3-compatible
  network-flow model.
- `planning/network-flow-seed-rebuild-plan.md`: refactoring plan for rebuilding
  the recovered notebook seed into a clean FRESH network-flow module.
- `metadata/ffcm-archive/prototype-inputs/bc-mini/`: synthetic BC diagnostic
  soft-link inputs plus generated five-layer graph, LP audit, and solution
  tables for the first feasibility prototype.
- `fresh_fibre/`: initial reusable Python module home for FRESH graph
  construction, LP solving, prototype validation, typed CSV-row adapters, and
  unit/dimensional checks.
- `metadata/ffcm-archive/unit-registry.csv` and
  `metadata/ffcm-archive/unit-conversion-registry.csv`: lightweight unit
  registry scaffolds for prototype validation and future scaling.

### P2.1 Archive Stabilization and Triage

- [x] Confirm the transferred archive remains local under ignored `tmp/`.
- [x] Generate an initial file-level manifest with sizes, extensions, inferred
  roles, relevance tags, and checksums.
- [x] Generate a directory-level scan summary to identify major evidence zones.
- [x] Create initial document, FCM evidence, and architecture-crosswalk indexes
  to guide reverse engineering.
- [ ] Decide whether the original archive ZIP should be annexed in the `data`
  repository for provenance-preserving cold storage.
- [ ] Create a repeatable manifest-refresh script if the archive contents change
  or a second copy arrives.

### P2.2 Bird's-Eye Architecture Reconstruction

- [ ] Use Ghafghazi et al. 2017, "Estimating Mill Residue Surplus in Canada: A
  Spatial Forest Fiber Cascade Modeling Approach", as the high-level model
  narrative and guardrail.
- [ ] Extract the FFCM conceptual modules: forest estate/log supply, facility
  network, processing technologies, transport routes, commodity cascade, market
  demand, residue surplus, scenarios, and calibration/validation workflow.
- [x] Draft an initial conceptual-module crosswalk to archive evidence and
  candidate FRESH replacement components.
- [ ] Refine each conceptual module against archive evidence, including publications,
  presentations, configuration files, spreadsheets, GIS layers, logs, and
  generated LP files.
- [ ] Record assumptions, unknowns, and likely black-box/proprietary boundaries.

### P2.3 FFCM Implementation Evidence Index

- [x] Parse FCM/Carbon basecase `.cfg` and `.inc` files into structured tables
  for FMUs, facilities, routes, transporters, processing lines, commodities,
  throughput, markets, imports, constraints, groups, and transload nodes.
- [x] Add a repeatable parser for the core FCM configuration files under
  `scripts/ffcm_archive/`.
- [x] Generate first-pass `.lp` summaries for the small report examples and
  large basecase log/residue allocation models.
- [x] Infer first-pass objective term, decision-variable, source commodity,
  destination class, product, and constraint-sense families from generated LPs.
- [ ] Reconstruct detailed constraint families by joining numbered LP
  constraints back to config entities, facility capacities, supply bounds,
  market requirements, and process throughput records.
- [x] Inventory FCM runtime dependencies, especially CPLEX, lp_solve, Java
  libraries, database drivers, and GIS/visualization dependencies.
- [x] Separate reusable data schema and model logic from runtime-specific or
  proprietary executable components.

### P2.4 FRESH Replacement Architecture

- [x] Specify the ws3-side forest-estate replacement: input datasets, stand
  state variables, growth/yield transitions, harvest scheduling, assortment/log
  outputs, and FMU/transport-node handoff.
- [x] Specify the downstream LP network-flow replacement using the CIRRELT
  reports as method documentation for disaggregation, value-creation potential,
  and profit-maximizing industrial fibre consumption.
- [x] Define the soft-link contract between ws3 outputs and the network-flow
  input port, including units, time steps, species/assortment classes, spatial
  keys, and scenario metadata.
- [x] Identify which FFCM data inputs can seed FRESH prototypes and which must
  be rebuilt from accepted national data sources.
- [ ] Compare the initial FRESH contract against the existing downstream LP
  model input schema and update field names/types accordingly.
- [x] Recover the likely surviving notebook seed for the ws3-compatible
  network-flow model from the GitHub gist and extract code/markdown for rebuild
  planning.
- [x] Inventory recovered notebook functions and crosswalk old graph concepts
  to the FRESH soft-link contract.
- [x] Promote the first recovered-notebook-inspired graph/LP prototype subset
  into a clean Python module that consumes the FRESH soft-link contract.
- [x] Add typed input adapters around the current CSV-row dictionaries without
  changing the public soft-link tables.
- [x] Add unit and dimensional consistency checks around the synthetic BC
  mini-network graph/LP module.
- [x] Refactor first-pass scenario/provenance/reporting semantics into the
  reusable FRESH module.
- [x] Extend unit checks to explicit conversion registries for `bdmt`, `t`,
  `kg`, `MWh`, and board/area units before national scaling.
- [ ] Continue refactoring remaining recovered notebook scenario and reporting
  semantics into the reusable FRESH module where still relevant.
- [x] Choose the first prototype geography, time step, commodity subset, and
  minimal scenario set.
- [x] Add a first open-source LP solver path for the synthetic BC mini-network.

### P2.5 Feasibility Prototype and Decision Gate

- [x] Scope a minimal BC diagnostic mini-network prototype using synthetic or
  clearly labelled diagnostic ws3-shaped supply records.
- [x] Create tiny synthetic soft-link input tables and generated five-layer
  graph node/arc tables for the BC diagnostic mini-network.
- [x] Build a minimal end-to-end prototype using a small region or synthetic
  national subset: ws3-style supply output into the LP network-flow model.
- [x] Compare prototype data structures against FFCM evidence tables and the
  Ghafghazi et al. architecture.
- [ ] Document gaps that require new data acquisition, license review,
  stakeholder clarification, or model redesign.
- [ ] Produce a decision memo: reverse-engineer FFCM architecture, adapt only
  selected schemas, or proceed with an independent FRESH design informed by FFCM.

## Detailed Next Steps Notes

1. Tyler should work through `planning/source-triage-queue.md`. `scanfi_v2`
   has first-pass review complete with a subset-first recommendation,
   `nfi_standard_reports` has first-pass review complete with a license-review
   flag, `canlad` / `pre_canlad_v2` have first-pass disturbance review
   complete, and `nrn` is accepted as a coarse national haul-distance baseline
   that still needs jurisdiction-specific forest/resource road follow-up. Next
   target is `biomass_inventory_business`.
2. Tyler's first pass should update `metadata/source-inventory.csv`, not download
   large payloads directly into the parent repo.
3. Once candidate sources are reviewed, promote only accepted datasets into
   `metadata/required-datasets.yaml`.
4. When the first real dataset is accepted, acquire it under `data/raw/`,
   record the manifest under `data/manifests/`, copy annexed payloads to
   `arbutus-s3`, and update the parent repo's curated registry.
5. In parallel with source triage, begin Phase 2 archive analysis from
   `planning/ffcm-archive-analysis.md`: preserve the Ghafghazi et al. 2017
   paper as the architectural overview, then work inward through FCM configs,
   generated LP artifacts, and the CIRRELT network-flow reports.
