# Roadmap

## Active Task: Forest-Sector Evidence v2

Governing note: [planning/forest-sector-evidence-v2.md](planning/forest-sector-evidence-v2.md).

Status: evidence collection and the v2d forest-economics evidence layer are
complete. The QC 2022 survey is a procurement **cost spread**, not a net value;
the old QC/M CAD net-value sensitivity is withdrawn. BC net value remains
`not_estimated` because general logging and truck-haul $/m3 are not published;
no combined national net value is computed.

- [x] v2a source review — no public QC mill-settlement ledger; BMMB/MRNF, BC
  log-market, MRDEM, and THLB limits recorded.
- [x] v2b cost/revenue ledger — 2,888 rows (200 observed BC + 2,688 QC model
  input); 2,075 delivered-log rows; observed/model-input separation enforced.
- [x] QC procurement-cost correction — T2 purchase cost vs T20 BGAD is a
  channel cost spread, not net value; `net_value_status=not_estimated`; no
  QC/M CAD sensitivity. Regional rows cite REG `regional_2022`; BMMB primary is
  Crown-only (9 full rates, 74,550 m3, 35.7547 CAD/m3), with teaching and
  all-in series separate.
- [x] BMMB delivered auctions — 18 sales, 13 awarded, 38.29 CAD/m3 all-in
  weighted nominal; Crown-only primary 35.75 CAD/m3.
- [x] QC MRNF 2022 survey — 660 observations; 81.40 / 25.01 / 106.41 CAD/m3
  procurement stack.
- [x] BC log market — 15,705 rows from 505 PDFs, 2019–2026.
- [x] Open-data input publication — exact derived inputs for the corrected BC
  net-value model published under
  `data/derived/national/harvest_basis/open_data_net_value/model_inputs/` with
  SHA-256 manifest, 18 cited raw sources, fail-closed reproducer and
  `input_publication=derived_model_inputs` /
  `raw_source_redistribution=false`; raw PDFs not redistributed. See
  `planning/open-data-policy.md`.
- [x] Operable proxy v2 — core20 99.87 Mha, core30 77.05 Mha; road binding.
- [x] Slope screen — 960 m MRDEM-30; >30% removes 1.51%; core20 98.39 Mha.
- [x] Operable proxy v3 — core20 >30% 98.39 Mha, 150M 1.525 m3/ha/yr; core30
  76.01 Mha, 150M 1.973 m3/ha/yr.
- [x] BC benchmark inputs — IAM/CAM appraisal allowances extracted and mapped
  to road, bridge, culvert, silviculture, specified operations, and TOA;
  general logging, truck haul, and EWB remain unpublished, so a full BC net
  value is not estimable.
- [x] v2d forest-economics evidence layer — exact validated QC procurement
  cost reference, BC log-price ranges, BCTS stumpage, BMMB historical
  reference, and appraisal cost coverage. All summary rows
  `net_value_status=not_estimated`, `policy_grade=false`, and
  `net_value_is_combined_national=false`; no BC partial net value and no
  combined national net value. Old `net_value_v2d/` and
  `net_value_benchmark/` are deprecated.
- [x] National economics v1 scaffold — 13 jurisdictions, 39 low/base/high
  margin rows, explicit donor/proxy tiers, and a volume-weighted national
  diagnostic labelled `model_estimate_not_policy`.
- [x] National economics v2 stumpage-scaled proxies — 8 extracted provincial
  stumpage/dues/royalty/standing-value references times the BC
  delivered/BCTS ratio; 5 donor fallbacks; no rate labelled as a delivered
  price.
- [x] National economics v2.1 base/sensitivity correction — fixed
  `base_scenario=donor_flat` and `sensitivity_scenario=stumpage_scaled`;
  78 margin rows, 28 summary rows, 39 tier-coverage rows. The scaled scenario
  is **not preferred, not observed and not policy grade**; dues are not
  comparable to delivered prices. National base diagnostic 33.1365 CAD/m3;
  scaled sensitivity -16.1220 CAD/m3; both labelled
  `model_estimate_not_policy`; no combined national net value. Negative
  margins remain unclipped.
- [ ] Model/UI wiring — only after validation; no OSeCAN change.

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

### P1.6 QC BFEC/Woodstock Extraction

Governing note: `planning/qc-bfec-full-extraction.md`.

- [x] Extract a five-unit QC BFEC sample and publish normalized derivatives.
- [x] Harden the ws3 parser for BFEC constants, `#CONST` yields, and missing
  theme mask codes.
- [x] Download, verify, extract, and parse every public BFEC model bundle.
- [x] Publish public curve/development-type derivatives plus manifest and
  validation report; keep restricted inventory crosswalks out of git.


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

- [x] Use Ghafghazi et al. 2017, "Estimating Mill Residue Surplus in Canada: A
  Spatial Forest Fiber Cascade Modeling Approach", as the high-level model
  narrative and guardrail.
- [x] Extract the FFCM conceptual modules: forest estate/log supply, facility
  network, processing technologies, transport routes, commodity cascade, market
  demand, residue surplus, scenarios, and calibration/validation workflow.
- [x] Draft an initial conceptual-module crosswalk to archive evidence and
  candidate FRESH replacement components.
- [x] Refine each conceptual module against archive evidence, including publications,
  presentations, configuration files, spreadsheets, GIS layers, logs, and
  generated LP files.
- [x] Record assumptions, unknowns, and likely black-box/proprietary boundaries.

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
   that still needs jurisdiction-specific forest/resource road follow-up.
   Next target is `biomass_inventory_business`; the newly flagged
   `databasin_canada_forest_tenures` candidate needs member-dataset,
   provenance, license, and FMU-boundary fit resolution.
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
6. For Phase 3, the SCANFI+Gauthier diagnostic lane replaced log-yield
   placeholders in a runnable BC mini variant. Phase 4 defined the real-input
   materialization gates, and Phase 5 completed the tiny public-safe SCANFI
   real-sampling fixture design. The next model-development gate is Phase 6:
   materialize and validate only the authorized tiny fixture before any broad
   raw raster payloads or private validation inputs are considered.

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

Merged PR: `#25`.

Governing planning note: `planning/phase-5-tiny-real-sampling-fixture.md`.

Goal: convert Phase 4's SCANFI+Gauthier policy gates into a concrete,
public-safe tiny sampling fixture design that can authorize Phase 6
materialization without committing raw rasters, private NFI material, or
production yield assumptions to the parent repository.

- [x] P5.1 Define the tiny BC diagnostic sampling extent and cell keys. Child
  issue: `#20`.
  - [x] Select the minimum BC mini supply-node coverage needed for the first
    real-sampled diagnostic fixture.
  - [x] Define stable sampled-cell identifiers and spatial-unit keys.
  - [x] Record expected DataLad source, metadata, manifest, and derived-output
    paths.
  - [x] Define required provenance fields for source layer, version,
    acquisition state, and value policy.
- [x] P5.2 Define the SCANFI raster-window extraction and manifest contract.
  Child issue: `#21`.
  - [x] Identify required SCANFI source layers for the tiny fixture.
  - [x] Define raw raster-window, manifest, and parent-repo derived-table
    boundaries.
  - [x] Specify checksum, unit, nodata, CRS, resolution, and sampling-method
    metadata.
  - [x] Keep all raw raster payloads in the `data` DataLad/git-annex boundary.
- [x] P5.3 Define validation checks for diagnostic acceptance. Child issue:
  `#22`.
  - [x] Define required-column and parseability checks.
  - [x] Define nonnegative quantity, area accounting, nodata, and expected
    range checks.
  - [x] Define provenance-completeness checks against the extraction contract.
  - [x] Identify which checks are diagnostic acceptance gates versus
    production calibration gates.
- [x] P5.4 Update gap and release-readiness surfaces. Child issue: `#23`.
  - [x] Update `prototype-gap-register.csv` to distinguish planned real
    sampling from production-ready national input.
  - [x] Update the conceptual alpha release roadmap with Phase 5 status.
  - [x] Confirm residue, cost, climate, NFI, and validation gates remain open.
  - [x] Add changelog and issue progress notes.
- [x] P5.5 Close Phase 5 with a decision memo and Phase 6 authorization.
  Child issue: `#24`.
  - [x] State whether Phase 6 may materialize a tiny SCANFI-derived fixture.
  - [x] Identify exactly which `data` paths and parent-repo outputs are
    allowed.
  - [x] Reconcile child issue checklists, roadmap, changelog, and PR body.
  - [x] Run verification and close child issues when complete.

## Phase 6: Tiny Real-Sampled Diagnostic Supply Lane

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: add the first tiny real-sampled diagnostic supply lane while preserving
existing synthetic, ws3-smoke, SCANFI diagnostic, and SCANFI+Gauthier
diagnostic lanes.

**Status**: COMPLETE — diagnostic lane and P6.5 memo complete; production gates remain open

**Workspace note** (2026-08-08): Phase 6 implementation was completed in-session
and preserved on the Phase 7 branch. P6.5 is now complete as a diagnostic-only
validation memo. The six heuristic SCANFI windows are quarantined as software
fixtures, not inferential evidence. Phase 7 parent issue `#26` and recovery task
`#27` govern the carryover; source triage continues under issue `#1`.

Governing planning notes:
- `planning/phase-6-scanfi-acquisition-plan.md` - acquisition plan (COMPLETED)
- `planning/phase-6-validation-memo.md` - validation memo skeleton
- `data/manifests/scanfi-v2-2025-acquisition.csv` - acquisition manifest
- `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/` - fixture

- [x] P6.0 Resolve SCANFI source availability and prepare acquisition plan. Child
  issue: `#24` (implicit).
  - [x] Check data submodule for authorized SCANFI v2 2025 rasters.
  - [x] Confirm files absent (fresh scaffold at commit `731667f`).
  - [x] Create `planning/phase-6-scanfi-acquisition-plan.md` with URLs, sizes,
    license, and acquisition steps.
  - [x] Await explicit authorization before CDN download (authorized 2026-02-04).
  - [x] Download all 5 authorized SCANFI v2 2025 rasters from ftp.maps.canada.ca.
  - [x] Verify SHA-256 checksums and record in acquisition manifest.
  - [x] Add files to git-annex at `data/raw/scanfi/v2/`.
  - [x] Publish annex content to arbutus-s3 (~20.6 GB, all 5 files verified).
- [x] P6.1 Materialize only the minimal approved SCANFI-derived fixture through
  the `data` DataLad submodule.
  - [x] Extract 5 SCANFI v2 2025 raster layers (biomass, height, closure,
    landcover, age) for `tiny_bc_real_sample_interior_dry_001` cell.
- [x] P6.2 Generate a public-safe derived inventory table consumed by the
  parent repository.
  - [x] Create `bc-mini-scanfi-real-sampled/` directory and skeleton
    `diagnostic_inventory_cells.csv` with six fixture cell rows and correct
    column schema.
  - [x] Populate with real sampled values: sampled_area_ha=66219.9,
    biomass_t_per_ha=53.23, height_m=9.97, closure_pct=56.6, landcover_class=6,
    age_yr=median after P6.1 materialization.
- [x] P6.3 Build a ws3-shaped supply output from real-sampled SCANFI diagnostic
  inventory plus existing Gauthier diagnostic log-yield rules.
  - [x] Emit `ws3_harvest_output.csv` (49,665 m³) and `supply_offer.csv` with
    real-sampled provenance.
  - [x] Key insight: graph tables (`graph_arcs.csv`) must be rebuilt after
    replacing `supply_offer.csv` to sync constraint arcs with updated supply
    quantities and costs.
- [x] P6.4 Solve the downstream LP variant and emit `ws3_harvest_output`,
  `supply_offer`, `solution_flow`, `solution_summary`, and comparison tables.
  - [x] LP solve produces consistent results after graph rebuild:
    objective_value=$2,350,244 CAD, total_supply_cost=$2,483,246 CAD (49,665 m³
    × $50/m³ exact match).
- [x] P6.5 Write a validation memo that clearly labels unresolved residue,
  cost, climate, NFI, and production-calibration assumptions.
  - [x] Create `planning/phase-6-validation-memo.md` skeleton with check
    tables and completion criteria.
  - [x] Complete memo with findings: yield still diagnostic (0.75 m³/ha
    placeholder), heuristic fixtures quarantined, no multi-rotation WS3, and
    no species×grade disaggregation.

## Phase 7: Source Triage And Production Readiness Gates

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: reconnect the model prototype to the broader source-discovery roadmap and
decide what blocks production readiness after the conceptual alpha.

- [x] P7.1 Continue source triage under child issue `#28` and governing issue `#1`, continuing from the local queue after `biomass_inventory_business` was reviewed as not suitable for forest biomass. Completed: NFI standard reports → `accepted`, NFDP harvest tables → `accepted`, 10 jurisdiction forest roads added as candidates.
  - [x] **P7.1.CASFRI.1 — framework pull**: CASFRI v5.3.1 (commit `c09a70cc1f90894a1d32c2f20606430b49ae274f`, 2021-08-17) translation tables vendored into the `data/` submodule sidecar at `data/raw/casfri_v5/translation_tables/` (129 CSV files); GPL-3.0 framework source intentionally NOT vendored. Provenance at `data/raw/casfri_v5/PROVENANCE.md`. **DONE 2026-08-09.**
  - [x] **P7.1.CASFRI.2 — acquisition matrix** (per-jurisdiction Tier 3 → Tier 4). DONE 2026-08-10 via Phase 2b source-tracking commit (1c43177). nrn_vectors + nrwn_vectors + cbmt_wms + sk_highways_wms + nwt_highways_and_resource_roads_wms + yk_forest_resource_roads_50k registered in metadata/source-inventory.csv (rows 47–52). Source-review docs at metadata/source-reviews/nrn_vectors.md, nrwn_vectors.md, cbmt_wms.md, provincial_transport.md document cutoffs, OGL-Canada license posture, and forward-ref to fresh_fibre.transport_cost.py. readiness-gates.yaml calibrated_cost_surfaces.blocking_datasets extended. Status: source-tracking complete; vector downloads and materialization (NRCan GeoGratis bulk) deferred to post-Phase 3.11. SK jurisdictional-row reconciliation (D1 §3.a vs. §3.c per-vintage split) remains a sub-step before any further jurisdiction expansion.
  - [x] **P7.1.CASFRI.3 — schema mapper**: `metadata/source-crosswalks/qc_casfri_v5_schema.md` committed; regression tests in `tests/test_casfri_qc_reader.py::test_field_mapping_dispatch`. **DONE 2026-08-09** (QC side; BC side covered by inheritance).
  - [x] **P7.1.CASFRI.4 — adapter module**: `fresh_fibre.casfri_qc_reader.py`, `fresh_fibre.species_canonical.py`, `fresh_fibre.qc_ws3_export.py` committed; all three call into the read-at-runtime translation tables, never into the framework's GPL-3.0 logic. Regression coverage in `tests/test_casfri_qc_reader.py` and `tests/test_species_canonical.py`. **DONE 2026-08-09.**
  - [x] **P7.1.CASFRI.5 — BC + QC two-province diagnostic fixture**: `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/` committed; LP solves `Optimal` (objective `~6.1 M CAD`); inter-provincial pathway flows emit; comparison artefacts at `…/generated/bc_qc_mini_*.csv`; tests in `tests/test_bc_qc_mini_2prov.py` (8 tests pass). **DONE 2026-08-09.**
  - [x] **P7.1.CASFRI.6 — registry promotion**: DONE 2026-08-10. Status flips from `candidate` → `accepted_subset_recommended` in `metadata/source-inventory.csv`. The BC + QC two-province diagnostic LP fixture (`fresh_fibre.bc_qc_mini_2prov` LP solves `Optimal`; commits `9c4bbf2` + `cd1ba60`) is the calibration-grade **non-framework-vendoring (NC Tier-3)** evidence. Per the user-corrected framing in (b25) — "CASFRI is the unified national schema; license-noise = framework *vendoring* not schema/sources" — the schema promotion is the load-bearing decision and Tier-3 evidence is now in hand. GPL-3.0 framework source **vendoring** remains a separate per-repo governance question and is **NOT a blocker** for schema acceptance per the AGENTS.md disclosure path. Companion memo: `planning/bc_qc_pipeline_strategy.md`.
  - [x] **P7.1.CASFRI.7 — readiness-gate update**: `species_composition` and `national_scaling` gates advanced from `blocked` to `in_progress` per `metadata/readiness-gates.yaml`; `planning/qc_two_province_incremental_memo.md` documents the evidence and remaining blockers. **DONE 2026-08-09.**
  - [x] P7.1.CASFRI.12: geographic crosswalk - facilities geocoded via Mills DB + suppliers.dbf; FMU->TL transport anchors; tenure area rollup (GFW 4.44M km^2 / databasin 2.10M km^2).
  - [x] P7.1.CASFRI.13: SCANFI v2 2025 species crown-closure layers (10 species, ~14.6 GB) acquired and annexed.
  - [x] P7.1.CASFRI.14: ERA5 1991-2020 climate normals acquired (9 NetCDF: t2m/tp/d2m x 3 decade chunks); 48 derived bioclim COGs (36 monthly normals + MAT/MAP/d2m_annual + 9 bioclim variables), Canada-clipped to 173x357 grid, EPSG:4326; annexed in data submodule c346518 (parent commit 01803fe).
- [x] P7.2 Promote accepted sources into `metadata/required-datasets.yaml` only when license, provenance, and access status are clear. Completed: `nfi_standard_reports` and `nfdp_harvest_tables` promoted.
- [x] P7.3 Add explicit readiness gates for NFI access, climate predictors, residue yield policy, calibrated cost surfaces, species composition, and national scaling. Completed: Created `metadata/readiness-gates.yaml` with 8 gates (7 blocked, 1 in_progress).
- [x] P7.4 Update roadmap, changelog, and gap surfaces so diagnostic alpha and production model states are visibly separate. Completed: Updated CHANGE_LOG.md and this ROADMAP.md.
- [x] P7.5 Produce a production-readiness memo for post-`v0.1.0a1` work. Completed: Created `planning/phase-7-production-readiness-memo.md` documenting diagnostic-only boundary, 7 blocked gates, 1 in_progress gate, gate-by-gate post-alpha work program, and v0.1.0a1 release criteria met.
- [x] P7.6 Extend the RSYC-stratified TSA24 DBH fits with multi-component Weibull and mixed Weibull/gamma candidates. Completed: added `mix_weibull_2`, `mix_weibull_gamma`, and optional `mix_weibull_3`; fitted them on the same left-truncated grouped support with two deterministic starts, canonical component ordering, degenerate-component rejection, and convergence diagnostics; re-ran TSA24 (45 of 75 targets switched to a new Weibull mixture, 42 improved by >2 AIC, one worse by >2 AIC; the two `CW` high-KS strata remain unresolved and the legacy two-gamma mixture remains the fallback); republished the comparison artifacts.
- [x] P7.7 Add constrained finite-mean spike tails and re-run the TSA24 fits. Completed: added `spike_weibull_constrained` / `spike_gamma_constrained` with a bounded grouped MLE (shape >= 1, scale >= 0.1 cm), `boundary_reject` status, first-bin-share spike weights, and truncated sampling; unconstrained spike rows remain audit-only. Re-ran 68 general + 7 AU targets: 8 constrained spikes selected, 0 unconstrained, 28 constrained boundary rejects; the two `CW` strata hit the exponential shape bound and retain the legacy two-gamma selection (KS 0.221 / 0.217), with five documented >2-AIC fallbacks. AU synthesis/conservation remains exact; refreshed `distribution_fits.csv`, `dbh_mixture_comparison.csv`, `validation_summary.json`, `manifest.json`, `VALIDATION_REPORT.md`, plots, and the `--working` plot generator.
- [x] P7.8 Match the synthetic tree list to both target volume and target TPH. Completed: added `SynthesisTarget.target_tph`, fixed expansion factors at the target TPH, and solved a positive diameter multiplier with a bracketed `brentq` search while recomputing height from the scaled DBH; volume-only scaling remains the default when `target_tph` is absent. TSA24 (`SBS_SX`, H, age 100) matches 1,463.5502 TPH and 311.6 m3 exactly (`k = 0.929782671`, QMD 17.52 cm, BA 35.28 m2/ha), with 240.74 m3 recovered logs + 70.86 m3 residue and exact conservation. Added dual-scaling, volume-only regression, no-solution, species-weight, and conservation tests; refreshed synthetic/assortment artifacts, metadata, report, manifest, and plots.
- [x] P7.9 Build the national plot-to-RSYC-stratum coverage audit. Completed: added `workflows/national/assortments/national_coverage.py`; mapped 67,477 of 72,975 sites (92.5%) to 301 of 672 ecodistricts; classified 30,065 stratum × species cells across national/ecodistrict/ecoregion/ecoprovince/ecozone levels; 2,001 direct / 768 pooled / 20,751 no_data at ecodistrict level (2,001/768/7,766 in data-bearing districts); documented 240 unresolved source species codes (376,636 live trees), 371 plotless ecodistricts, and the AB/ON/NS/NU gap; published `data/derived/national/assortments/national_coverage/` and `metadata/national-coverage.csv`.
- [x] P7.10 Build and run the first national hierarchical DBH/height fit. Completed: added `workflows/national/assortments/national_fit.py` with cell-level resume, an 8-worker timeboxed dispatch, direct/pooled local fits, parent/species-national fallback, RSYC reference-age volume/AGB scale priors, and evidence outputs; added an optional candidate-family filter to `distributions.py` so the national pass uses the specified Weibull/gamma, `mix_weibull_2`, `mix_weibull_gamma`, and constrained-spike set. Ecoprovince pass: 1,260 target cells, 319 direct / 29 pooled / 132 parent / 761 species-national / 19 no_fit, 321.6 s, 0 timebox-pending. Ecoregion pass (resumed 560 coarser cells): 4,830 target cells, 757 / 154 / 1,142 / 2,705 / 72, 525.2 s, 0 timebox-pending. 98 deterministic local failures remain (largely pooled cells below the fit floor or cells whose trees lack usable expansion factors); no borrowed shape is labelled local. Published `data/derived/national/assortments/national_fit/` and `metadata/national-fit.csv`; the ecoregion report carries 1,543 resumable cell partials.
- [x] P7.11 Upgrade the Stage A TSA24/BC facility, transport, and unit inputs and re-run the pilot. Completed: added `units.py`, `facilities.py`, and `transport.py`; published `facilities_bc.csv` (179 BC rows; coordinate sources 8 Mills DB / 0 suppliers.dbf / 126 TL-hub proxy / 45 missing) and `transport_links_bc.csv` (210 grade links across 129 facilities; 61 direct / 68 graph-shortest; 31 no-TL-route + 1 anchor-self excluded; capacity=None). Replaced the 150 km access leg with the FFCM TL graph from the documented Prince George mill proxy; median distance 920.6 km versus 261.2 km, SAWLOG median transport CAD 61.39/m3 versus 14.23. Re-ran the 1,000 ha base (311,600 m3 assigned, objective -CAD 1.624M) and ws4-quota sensitivity (77.25M m3 assigned, 18.11M unassigned); contracts remain `diagnostic_absent`. Added unit/facility/transport tests; 27 tests pass.
- [x] P7.12 Build the real NRN BC road-network transport pilot and re-run Stage A. Completed: added `build_road_network.py`; processed the full BC NRN `ROADSEG` layer (263,584 segments; 244,224 haulable), projected to EPSG:3005, snapped endpoints to 1 m, and built a 203,511-node / 242,059-edge graph in 11.5 s (peak 579 MB). Facility snapping (5 km): 79 snapped / 55 too_far / 45 missing_coord; 77 real paths, with BC-SAW-383 on a separate Vancouver Island component. Published `transport_links_bc_real.csv` (98 grade links across 67 in-scope facilities), `snap_diagnostics.csv`, `road_network_summary.json`, `distance_comparison.png`, and `NETWORK_REPORT.md`; preserved the prior FFCM table as `transport_links_bc_ffcm.csv`. Stage A uses real links for 67 in-scope facilities and flagged FFCM fallback for 67. Base unchanged (311,600 m3 assigned; -CAD 1.624M; all assigned facilities fallback); ws4 quota 79.48M assigned / 15.89M unassigned (-CAD 18.449B) versus 77.25M / 18.11M (-CAD 21.346B). Added graph/snap/cost tests; 34 tests pass.
- [x] P7.13 Recover BC facility coordinates and build the BC Fibre Database diagnostic contract table, then re-run Stage A. Completed: added `bc_fibre.py` with corroborated Mills DB 2015 matching (no forced matches); rejected the 8 legacy exact-code collisions and published `bc_facility_match_conflicts.csv`. Coverage improved from 8 Mills DB / 126 TL-hub proxy / 45 missing to 98 / 65 / 16 across all BC facilities (Stage A scope 8/113/41 -> 88/58/15). Parsed 596 residue allocation shares from `Sawmill Fibre Allocation Input V3.xls` and 146 positive 2018 TSA apportionment-class rows (38,726,439 m3/y) from `TSA Tenure Apportionment V5.xls`; the workbooks have no facility-level volume, so all 161 Stage A contracts remain `diagnostic_absent`. Rebuilt the NRN links from recovered coordinates (156/163 snapped; 134 real paths; 196 grade links) and re-ran Stage A: real links for 118 facilities, FFCM fallback for 11, 65 fallback -> real, 10 real -> fallback, 5 real -> missing. Corrected the TSA24 anchor from `BC-SAW-488` (actually FFCM Canfor Houston) to `BC-SAW-506` (Canfor Prince George). Base 1,000 ha: 311,600 m3 assigned, objective +CAD 4.960M, delivered CAD 125.35/m3. ws4 quota: 77.46M assigned / 17.90M unassigned, objective -CAD 19.234B, delivered CAD 150.21/m3. Added matching/contract tests; 43 tests pass.
- [x] P7.14 Build the BC mill-to-tenure crosswalk from the TSA apportionment workbook and re-run Stage A with apportionment-derived maximum bounds. Completed: added `tenure_crosswalk.py` with legal-suffix normalization, an explicit major-holder alias table, a FADM TSA point-in-polygon join cached in `bc_facility_tsa.csv`, and exact -> alias -> unique -> ambiguous matching. Parsed 52 workbook sheets (40 TSA sheets), 146 positive 2018 apportionment rows across 32 TSAs (38,726,439 m3/y), and 152 positive licence rows across 33 TSAs / 77 holders (18,438,022 m3/y); 130 zero/negative 2018 rows were rejected. Matched 28 licence rows to 20 facilities (8,134,098 m3/y; 21.0% of apportionment-class volume and 44.1% of licence-detail volume); 50 rows / 7.38M m3/y stayed ambiguous and 74 rows / 2.93M m3/y had no candidate. TL-hub proxy coordinates are retained for audit but never used to disambiguate a holder match. Built `contracts_bc_apportionment.csv` with 20 maximum entitlements (16 Stage A, 4 out-of-scope PLY; 4 exceed nameplate); unmatched facilities keep `diagnostic_absent` nameplate caps. Re-ran Stage A: base unchanged at 311,600 m3 / 0 unassigned / +CAD 4.960M / CAD 125.35/m3 because no entitlement binds at 1,000 ha; ws4 quota fell to 70.63M m3 assigned / 24.74M unassigned (was 77.46M / 17.90M), objective -CAD 25.943B (was -CAD 19.234B), delivered CAD 146.83/m3 (was 150.20), utilization 77.5% (was 85.0%), with 14 of 16 apportionment contracts binding and 115 nameplate-binding facilities. Class mix SAW/PAP/PEL/BIO 45.4/33.9/9.7/10.9% versus 50.2/30.9/8.9/10.0% previously and FFCM nameplate 49.6/33.8/7.5/9.1%. Added normalization, alias, ambiguous-rejection, contract-bound, and loader tests; 65 tests pass.
- [x] P7.15 Build and run the Stage B sawmill-residue and bioenergy allocation pilot for TSA24/BC. Completed: added `residue.py` and `stage_b.py`; parsed the FFCM sawmill outfeed/infeed ratios for 94 ws4-quota SAW sources (32,076,713 m3 of assigned sawlogs), built 376 residue supply rows (8,325,597.703 ODT generated), screened 41 PAP/PEL/BIO candidates to 24 located and road-connected recipient lines, routed 2,026 source-recipient links on the NRN BC road graph (1,150 real, 782 real from proxy-located sources, 94 explicit hub-and-spoke FFCM road fallbacks), and solved the profit-maximising allocation LP. Assigned 6,501,439.981 ODT (78.09%) and left 1,824,157.722 ODT unassigned: 1,048,992.210 chips (capacity/transport), 140,390.240 sawdust, 24,231.402 hogfuel, and 610,543.832 `other` (no consumer class). 15 of 24 included recipient facilities are capacity-binding. Bioenergy is 10.12% of generated residue and 12.96% of assigned residue, producing 624,018.634 MWh. Objective +CAD 759,507,703.72 (revenue 1,502,041,517.98 - transport 201,593,954.16 - processing 540,939,860.09). Published `data/derived/national/assignments/tsa24_pilot/stage_b/` with supply, demand, transport, assignment, utilization, balance, bioenergy, objective, validation, report, and manifest tables. 43 of 94 sawmills use low-confidence TL-hub proxies; proxy-coordinate recipients and island recipients without a road path are excluded, never guessed. Added 13 Stage B tests (69 total pass).

- [x] P7.16 Build the all-BC forest/THLB area-weight table and join it to the national batch. Completed: read 5,057,731 VRI 2025 R1 polygons on the documented Crown management/vegetated/treed filter, allocated species by normalised percentages through the CASFRI crosswalk, and produced 2,266 `(TSA, ecoprovince, ecoregion, RSYC species)` rows covering 56,428,146 ha forest and 18,653,774 ha THLB. THLB is the per-polygon label-point sample of `misc.thlb.tif` (raster total 20,032,336 ha on 32.6% valid grid); 4,953,977 ha of forest has no THLB attribution and is kept at zero. 11,007,317 ha (19.5%) is `UNMAPPED_BC`, led by `SX` (7.74M ha); it is reported, not redistributed. Joined 429 BC-relevant cells: 192 matched, 237 `no_area` with zero totals and no 1 ha placeholder. Published `data/derived/national/assortments/bc_area_weights/`; runtime 123.8 s; 6 tests pass.
- [x] P7.17 Build the all-BC woody-residue supply curve for OSeCAN zones B1/B2. Child issue: `#31`. Completed: added `workflows/national/clews/bc_residue_supply.py` with a FRESH-side provisional crosswalk (B1 = Georgia Depression + Southern Coastal Mountains; B2 = the remaining BC ecoprovinces), a THLB-weighted TSA->ecoprovince harvest allocation, and a flat 2021-2070 annual residue profile. Primary basis is the all-BC 2018 AAC (52,774,801 m3/y = parsed 44,399,801 + Prince George 8,375,000); the parsed 36-TSA AAC and both apportionment totals (38,726,439 parsed; 58,424,023 all-BC) are retained as comparison scenarios. Residue ratio is the area-weighted national batch, resolved by ecoprovince (BC all-species 0.080872 ODT/m3; range 0.035732-0.364484); the TSA24 Stage B mill-residue ratio (0.259553) is reported and rejected as a different mass basis. Energy is 18.5 GJ/ODT dry. Result: B1 6.162027 PJ/yr at 2.994574 $/GJ; B2 87.008058 PJ/yr at 1.706656 $/GJ; national 93.170084 PJ/yr at 1.791835 $/GJ. The 50 PJ/$4 placeholder is conservative on both quantity and cost; a 60% recovery sensitivity gives 55.902 PJ. Published `data/derived/national/clews_supply/bc_woody_residue/` with zone/TSA crosswalks, annual residue, OSeCAN activity-limit and variable-cost rows, audit/comparison tables, `BC_CLEWS_SUPPLY_REPORT.md`, and `manifest.json`; 22 focused tests pass.
- [x] P7.18 Build the recoverable-residue curve and 2021-2070 time path for OSeCAN B1/B2. Completed: added `workflows/national/clews/bc_residue_recovery.py` and its focused tests. FFCM has no FMU->TSA/zone crosswalk; the 467 FMU ledger (134,745,540.345 m3 log; 8,488,969.042 ODT residue; 0.063 ODT/m3; 45.454084659 $/ODT) is allocated to B1/B2 by the documented 2018 AAC zone log share and flagged. The same-log-basis implied recovery is 1.736815 for B1 (out of bounds, 0.60 fallback) and 0.583931 for B2. The AAC workbook supplies 37 positive TSAs for 2011-2028 and reconciles exactly to 52,774,801 m3/y in 2018; the time path uses 2021-2028 and holds 2028 to 2070 (`aac_2028_hold`). Result: base 50.472521 PJ/yr in 2021 (B1 3.678381 at 0.60; B2 46.794140 at 0.583931) and 42.958795 PJ/yr from 2028, at 2.456978 $/GJ; low 30.193455 PJ/yr at 3.194071 $/GJ; high 64.700261 PJ/yr at 1.965582 $/GJ. The AGB non-stem sensitivity is 3.07x the B1 taper base and 1.17x the B2 taper base. Published `data/derived/national/clews/` with recovery, time-path, case-summary, 12 zone + 6 national parameter files, report, manifest, and FMU audit; 14 focused tests pass. **Superseded 2026-11-18:** the refined batch changes the B2 gross taper ratio, so the adopted base recovery is the recomputed FFCM-implied 0.723799161 (not 0.583931303); B1 remains the 0.60 fallback. Rebuilt base 2028+ caps are B1 3.605475, B2 39.918341, total 43.523816 PJ; the chain/co-opt and the zonal OSeCAN were rerun. See `BC_RESIDUE_RECONCILIATION.md` and `osecan/reconcile_ffcm/`.
- [x] P7.19 Freeze and document the national facility chain v1. Completed:
  published `docs/model-card-national-facility-chain.md` covering the inventory
  v1 → capacity v3 → assignment v6 → product-chain v1 → residue curve v3 →
  facility residue v3 → chain scenarios v3 → OSeCAN handoff v4 chain, data
  sources and capacity evidence tiers, the 100/119/150 Mm3 cases, the v6
  local-processing base versus the v5 `published_plus_proxy` trade sensitivity,
  limitations, run commands, and non-claims. The 119 Mm3 v6 base assigns
  118.977 Mm3 with 118.941 Mm3 local (99.97%), 36,376 m3 cross-border (0.03%),
  47.463 Mm3 proxy (39.89%), 208 facilities, HHI 0.01287, and largest-facility
  share 6.80%; the v5 sensitivity has 21.902 Mm3 cross-border (18.41%) and
  40.319 Mm3 proxy (33.89%). The 100/119/150 total gross residue is 583.12 /
  693.78 / 874.68 PJ and total recoverable residue is 542.46 / 645.40 /
  813.69 PJ. OSeCAN handoff v4 maps all 13 zones with no `UNMAPPED` bucket:
  119 Mm3 process 50% is 263.003 PJ and harvest 60% is 100.667 PJ, against a
  2050 context of 152.923 PJ national / 43.524 PJ BC. Created annotated tag
  `national-facility-chain-v1` at parent `94c7501` and recorded data commit
  `6ca272e`. No code/model/UI change, no public OSeCAN change, and no
  unexpected `D`.

## Phase 8: Conceptual Alpha Release Preparation

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Goal: make the repository understandable, reproducible, and announceable as the
first conceptual alpha release.

- [x] P8.1 Update README and planning surfaces with a compact "what works,
  what is diagnostic, what is blocked" status section. Completed: Added "v0.1.0a1 Conceptual Alpha Release Status" section to README.md with diagnostic capabilities, quarantined components, 8 readiness gates table, verification checklist, and governance statement.
- [x] P8.2 Bump the package version to `0.1.0a1` and prepare tag
  `v0.1.0a1`. Completed: Bumped pyproject.toml version to 0.1.0a1, created annotated tag v0.1.0a1.
- [x] P8.3 Run full verification from the repo-root `.venv`, including unit
  tests, script compilation, FreshForge validate/plan checks, CSV parse checks,
  and `git diff --check`. Completed: All 56 tests pass, git diff --check clean (parent + data), compileall clean, branch at HEAD bc7274e.
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

Status: P3.7 implementation complete, PR `#9` merged, and parent issue `#2`
closed.
