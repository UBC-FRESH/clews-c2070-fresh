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

### P1.5 FreshForge Data Materialization Workflow

- [x] Preserve raw/heavy-data boundaries while adding a collaborator
  materialization workflow for the `data` submodule.
- [x] Add a CLEWS-owned FreshForge provider namespace for DataLad/git-annex
  materialization using the FEMIC provider/overlay pattern.
- [x] Add public-safe smoke and executable workflow fixtures that target
  `data/metadata` and `data/manifests`, not `data/raw`.
- [x] Document repo-root `.venv` bootstrap expectations for Python, DataLad,
  and FreshForge, with `git-annex` as an OS-level prerequisite.
- [x] Add regression tests for provider metadata, overlay parsing, command
  generation, workflow planning, and report-only smoke execution.

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
  construction, LP solving, contract checks, prototype validation, typed
  CSV-row adapters, and unit/dimensional checks.
- `metadata/ffcm-archive/unit-registry.csv` and
  `metadata/ffcm-archive/unit-conversion-registry.csv`: lightweight unit
  registry scaffolds for prototype validation and future scaling.
- `tests/`: stdlib regression tests for the BC mini-network prototype.
- `planning/ffcm-prototype-decision-memo.md`: Phase 2 decision-gate memo for
  the BC mini-network prototype.
- `metadata/ffcm-archive/prototype-gap-register.csv`: machine-readable gap
  register for moving from synthetic prototype to diagnostic/production data.

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
- [x] Compare the initial FRESH contract against the existing downstream LP
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
- [x] Add regression tests for the synthetic BC mini-network prototype.
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
- [x] Document gaps that require new data acquisition, license review,
  stakeholder clarification, or model redesign.
- [x] Produce a decision memo: reverse-engineer FFCM architecture, adapt only
  selected schemas, or proceed with an independent FRESH design informed by FFCM.

### P2.6 ws3 Supply Bridge and Diagnostic Data Replacement

- [x] Add a normalized synthetic ws3 harvest-output fixture upstream of
  `supply_offer`.
- [x] Add and test a `fresh_fibre.ws3_export` bridge that writes the current
  `supply_offer` contract.
- [x] Generate an auditable `supply_offer_from_ws3.csv` comparison artifact for
  the BC mini-network.
- [x] Add a tiny real ws3 smoke run from the local editable `../ws3` clone that
  emits the same normalized harvest-output shape.
- [x] Move the smoke-run spatial/supply-node mapping into an explicit,
  validated `ws3_supply_crosswalk.csv` fixture.
- [x] Add a separate `bc-mini-ws3-smoke` LP variant that replaces only
  `supply_offer.csv` with the real ws3 smoke supply export.
- [x] Add generated comparison tables between the stable synthetic BC mini LP
  solve and the ws3-smoke supply variant.
- [x] Select the first diagnostic replacement source path: SCANFI v2 2025
  subset for inventory, NFI standard reports for validation only, yield policy
  unresolved.
- [x] Add a SCANFI v2 2025 diagnostic subset manifest fixture without
  downloading raw rasters.
- [x] Add a tiny SCANFI-derived diagnostic inventory-cell fixture with
  synthetic proxy values and verified SCANFI layer provenance.
- [x] Add a SCANFI diagnostic supply generator and BC mini LP variant that
  converts inventory cells through the ws3-shaped harvest-output boundary.
- [x] Add a three-way comparison surface for the synthetic BC mini, ws3-smoke,
  and SCANFI-diagnostic supply variants.
- [x] Select the Gauthier et al. 2015 method as the primary yield-policy
  reproduction path and add a diagnostic evaluator fixture.
- [ ] Replace one synthetic supply-side data family with accepted diagnostic
  inventory/yield inputs, beginning with SCANFI diagnostic inventory plus
  Gauthier diagnostic log-yield rules. Diagnostic log-yield replacement is now
  implemented; production acceptance remains blocked by residue policy, real
  raster sampling, NFI/climate input access, and validation.
- [x] Add provenance fields or manifests needed to distinguish ws3 runtime
  output, normalized adapter output, and downstream LP input.

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
6. For Phase 3, the SCANFI+Gauthier diagnostic lane now replaces log-yield
   placeholders in a runnable BC mini variant. Phase 4 defined the real-input
   materialization gates. The next model-development gate is Phase 5: design a
   tiny public-safe SCANFI real-sampling fixture skeleton before any raw raster
   payloads or private validation inputs are materialized.

## Phase 4: Real Diagnostic SCANFI+Gauthier Input Materialization

GitHub parent issue: `#12`.

Active branch: `feature/p4-real-diagnostic-input-materialization`

Governing planning note:
`planning/phase-4-real-diagnostic-input-materialization.md`.

Goal: move the SCANFI+Gauthier BC mini diagnostic lane from synthetic
method-reproduction fixtures toward a tiny, provenance-tracked real-input
materialization path while preserving the parent-repo data boundary and using
FreshForge/DataLad workflows for reproducibility.

- [x] P4.1 Define the real diagnostic input contract. Child issue: `#13`.
  - [x] Define required SCANFI raster fields, spatial sampling keys, and
    fixture extent.
  - [x] Define required Gauthier predictor fields.
  - [x] Define parent-repo metadata outputs versus DataLad submodule payload
    outputs.
  - [x] Add a machine-readable input-contract/gate table.
  - [x] Update gap register and planning note.
- [x] P4.2 Add a FreshForge/DataLad materialization plan for the tiny SCANFI
  fixture. Child issue: `#14`.
  - [x] Define lightweight manifest paths under `data/metadata` or
    `data/manifests`.
  - [x] Keep raw raster payloads opt-in and outside the parent repo.
  - [x] Add or update FreshForge overlay examples for the tiny diagnostic
    fixture.
  - [x] Document required `CLEWS_EXTERNAL_DATA_ROOT` behavior.
  - [x] Add tests or dry-run validation for the workflow metadata.
- [x] P4.3 Resolve Gauthier predictor/access gates for the diagnostic fixture.
  Child issue: `#15`.
  - [x] Inventory accepted or candidate sources for NFI reference pixels/plots.
  - [x] Select climate predictor source candidates for temperature and
    precipitation.
  - [x] Define conifer/deciduous composition source and aggregation rules.
  - [x] Record license/access state for each required predictor source.
  - [x] Identify which predictors can be represented by public-safe tiny
    fixtures.
- [x] P4.4 Define residue, cost, and validation policy for production
  acceptance. Child issue: `#16`.
  - [x] Define candidate residue-yield policy paths.
  - [x] Define cost-calibration source requirements and placeholder
    boundaries.
  - [x] Define validation benchmarks, including NFI standard reports where
    license permits.
  - [x] Update prototype gap register with production acceptance states.
  - [x] Produce a short decision memo for unresolved versus accepted policies.
- [x] P4.5 Close the phase with a decision memo and PR hygiene. Child issue:
  `#17`.
  - [x] Reconcile all child issue checklists.
  - [x] Add a phase decision memo.
  - [x] Update `ROADMAP.md`, `CHANGE_LOG.md`, and GitHub issue comments.
  - [x] Run required verification commands.
  - [x] Open or update the phase PR and close child issues when complete.

Status: Phase 4 implementation complete and merged.

## Phase 5: Tiny Real-Sampling Design And Fixture Skeleton

GitHub parent issue: `#19`.

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Active branch: `feature/p5-tiny-real-sampling-fixture`.

Governing planning note: `planning/phase-5-tiny-real-sampling-fixture.md`.

Goal: convert Phase 4's SCANFI+Gauthier policy gates into a concrete,
public-safe tiny sampling fixture design that can authorize Phase 6
materialization without committing raw rasters, private NFI material, or
production yield assumptions to the parent repository.

- [ ] P5.1 Define the tiny BC diagnostic sampling extent and cell keys. Child
  issue: `#20`.
  - [ ] Select the minimum BC mini supply-node coverage needed for the first
    real-sampled diagnostic fixture.
  - [ ] Define stable sampled-cell identifiers and spatial-unit keys.
  - [ ] Record expected DataLad source, metadata, manifest, and derived-output
    paths.
  - [ ] Define required provenance fields for source layer, version,
    acquisition state, and value policy.
- [ ] P5.2 Define the SCANFI raster-window extraction and manifest contract.
  Child issue: `#21`.
  - [ ] Identify required SCANFI source layers for the tiny fixture.
  - [ ] Define raw raster-window, manifest, and parent-repo derived-table
    boundaries.
  - [ ] Specify checksum, unit, nodata, CRS, resolution, and sampling-method
    metadata.
  - [ ] Keep all raw raster payloads in the `data` DataLad/git-annex boundary.
- [ ] P5.3 Define validation checks for diagnostic acceptance. Child issue:
  `#22`.
  - [ ] Define required-column and parseability checks.
  - [ ] Define nonnegative quantity, area accounting, nodata, and expected
    range checks.
  - [ ] Define provenance-completeness checks against the extraction contract.
  - [ ] Identify which checks are diagnostic acceptance gates versus
    production calibration gates.
- [ ] P5.4 Update gap and release-readiness surfaces. Child issue: `#23`.
  - [ ] Update `prototype-gap-register.csv` to distinguish planned real
    sampling from production-ready national input.
  - [ ] Update the conceptual alpha release roadmap with Phase 5 status.
  - [ ] Confirm residue, cost, climate, NFI, and validation gates remain open.
  - [ ] Add changelog and issue progress notes.
- [ ] P5.5 Close Phase 5 with a decision memo and Phase 6 authorization.
  Child issue: `#24`.
  - [ ] State whether Phase 6 may materialize a tiny SCANFI-derived fixture.
  - [ ] Identify exactly which `data` paths and parent-repo outputs are
    allowed.
  - [ ] Reconcile child issue checklists, roadmap, changelog, and PR body.
  - [ ] Run verification and close child issues when complete.

## Phase 6: Tiny Real-Sampled Diagnostic Supply Lane

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: add the first tiny real-sampled diagnostic supply lane while preserving
existing synthetic, ws3-smoke, SCANFI diagnostic, and SCANFI+Gauthier
diagnostic lanes.

- [ ] P6.1 Materialize only the minimal approved SCANFI-derived fixture through
  the `data` DataLad submodule.
- [ ] P6.2 Generate a public-safe derived inventory table consumed by the
  parent repository.
- [ ] P6.3 Build a ws3-shaped supply output from real-sampled SCANFI diagnostic
  inventory plus existing Gauthier diagnostic log-yield rules.
- [ ] P6.4 Solve the downstream LP variant and emit `ws3_harvest_output`,
  `supply_offer`, `solution_flow`, `solution_summary`, and comparison tables.
- [ ] P6.5 Write a validation memo that clearly labels unresolved residue,
  cost, climate, NFI, and production-calibration assumptions.

## Phase 7: Source Triage And Production Readiness Gates

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: reconnect the model prototype to the broader source-discovery roadmap and
decide what blocks production readiness after the conceptual alpha.

- [ ] P7.1 Finish open source-triage issue `#1`, beginning with
  `biomass_inventory_business`.
- [ ] P7.2 Promote accepted sources into `metadata/required-datasets.yaml` only
  when license, provenance, and access status are clear.
- [ ] P7.3 Add explicit readiness gates for NFI access, climate predictors,
  residue yield policy, calibrated cost surfaces, species composition, and
  national scaling.
- [ ] P7.4 Update roadmap, changelog, and gap surfaces so diagnostic alpha and
  production model states are visibly separate.
- [ ] P7.5 Produce a production-readiness memo for post-`v0.1.0a1` work.

## Phase 8: Conceptual Alpha Release Preparation

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: make the repository understandable, reproducible, and announceable as the
first conceptual alpha release.

- [ ] P8.1 Update README and planning surfaces with a compact "what works,
  what is diagnostic, what is blocked" status section.
- [ ] P8.2 Bump the package version to `0.1.0a1` and prepare tag
  `v0.1.0a1`.
- [ ] P8.3 Run full verification from the repo-root `.venv`, including unit
  tests, script compilation, FreshForge validate/plan checks, CSV parse checks,
  and `git diff --check`.
- [ ] P8.4 Create the GitHub release notes summarizing Phases 1 through 8.
- [ ] P8.5 Enable GitHub Discussions if still disabled and post a progress
  announcement linked to the release.

## Phase 3: Diagnostic ws3 Supply and Yield Replacement

GitHub parent issue: `#2`.

Active branch: `feature/p2-ffcm-ws3-diagnostic-yield`

Governing planning note: `planning/phase-3-diagnostic-ws3-supply-yield.md`.

Goal: replace the synthetic BC mini supply path with a diagnostic, provenance
tracked ws3/SCANFI/Gauthier lane while keeping raw data and private inputs out
of the parent repository.

- [x] P3.1 Establish ws3 harvest-output bridge and smoke-run baseline. Child
  issue: `#3`.
  - [x] Add normalized ws3-shaped harvest-output fixture.
  - [x] Add `fresh_fibre.ws3_export` adapter to the `supply_offer` contract.
  - [x] Add editable local `../ws3` smoke-run bridge.
  - [x] Add explicit smoke-run crosswalk validation.
  - [x] Add ws3-smoke LP variant and regression coverage.
- [x] P3.2 Build and compare BC mini supply variants. Child issue: `#4`.
  - [x] Add synthetic-vs-ws3 comparison tables.
  - [x] Add SCANFI diagnostic LP variant outputs to comparison scope.
  - [x] Add three-way variant run matrix.
  - [x] Add objective, supply, solution-summary, and pathway-flow comparisons.
  - [x] Add comparison decision memo.
- [x] P3.3 Select and fixture SCANFI diagnostic inventory source. Child issue:
  `#5`.
  - [x] Select SCANFI v2 2025 subset as first diagnostic inventory source.
  - [x] Generate lightweight SCANFI subset manifest without raw rasters.
  - [x] Generate synthetic diagnostic inventory cells tied to SCANFI layer
    provenance.
  - [x] Record missing north extrapolation mask status.
  - [x] Update gap register and replacement planning notes.
- [x] P3.4 Generate SCANFI diagnostic supply and LP variant. Child issue:
  `#6`.
  - [x] Add synthetic diagnostic yield-rule table.
  - [x] Generate ws3-shaped SCANFI diagnostic harvest output.
  - [x] Aggregate inventory-cell contributions at supply-node/commodity
    boundary.
  - [x] Solve `bc-mini-scanfi-diagnostic` LP variant.
  - [x] Add provenance and regression tests.
- [x] P3.5 Define Gauthier yield-policy gate and diagnostic evaluator. Child
  issue: `#7`.
  - [x] Install and verify `pdftotext` for Gauthier paper extraction.
  - [x] Select Gauthier et al. (2015) as primary yield-method reproduction
    path.
  - [x] Encode published coniferous/deciduous coefficients and Duan factors.
  - [x] Generate synthetic diagnostic curve, maturity, and candidate yield-rule
    outputs.
  - [x] Record required real-data inputs and access/license gaps.
  - [x] Add regression tests for the diagnostic evaluator.
- [x] P3.6 Close phase with issue, branch, and PR hygiene. Child issue:
  `#8`.
  - [x] Create GitHub parent issue for Phase 3.
  - [x] Create child issues for P3.1 through P3.6.
  - [x] Update roadmap task lines with issue numbers.
  - [x] Check child issue checklist items against completed work.
  - [x] Push phase branch and open a PR to `main` (`#9`).
- [x] P3.7 Gauthier-to-SCANFI diagnostic yield integration. Child issue:
  `#11`.
  - [x] Update CLEWS agent workflow notes to match UBC-FRESH
    roadmap/issue/branch discipline.
  - [x] Add a Gauthier-to-SCANFI yield-rule adapter.
  - [x] Add the `bc-mini-scanfi-gauthier-diagnostic` solved variant.
  - [x] Extend BC mini variant comparison outputs from three lanes to four
    lanes.
  - [x] Update roadmap, planning, gap register, changelog, parent issue, and
    PR `#9`.
  - [x] Add regression tests and run verification commands.

Status: P3.7 implementation complete; PR `#9` needs final review/merge before
closing parent issue `#2`.
