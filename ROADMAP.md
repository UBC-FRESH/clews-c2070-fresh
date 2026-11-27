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
- [x] P7.20 Freeze and document the national facility chain v2. Completed:
  updated `docs/model-card-national-facility-chain.md` for the inventory v1 →
  capacity v3 → assignment v6 → product-chain v3 → residue curve v5 →
  facility residue v5 → chain scenarios v5 → OSeCAN handoff v6 chain. The card
  records the measured BC/QC/NB/YT coefficient replacement (4 of 13
  jurisdictions; 591 measured stems), stem + branch/foliage total residue with
  0.35/0.60/0.75 recovery scenarios and no chosen recovery factor, the
  119 Mm³ results (gross harvest residue 313.640 PJ, reference-recoverable
  195.433 PJ, process residue 514.614 PJ, total gross residue 828.254 PJ),
  OSeCAN harvest tier shifts from T1/T1/T2 to T3/T4/above_T4 with process
  tiers unchanged, taper-coverage and branch/foliage proxy limitations, and
  non-claims. Created annotated tag `national-facility-chain-v2` at parent
  `64a6b83` and recorded data commit `e9fc837`. Documentation and tag only; no
  code/model/UI change, no public OSeCAN change, and no unexpected `D`.
- [x] P7.21 Write the national facility chain v2 handoff and document the
  open-data imputation path. Completed:
  `docs/handoff-national-facility-chain-v2.md` records the frozen tags and
  stage artifacts, the measured BC/QC/NB/YT coverage versus the nine
  non-measured jurisdictions, the 100/119/150 Mm³ results, the stem-only
  versus measured total residue recovery cases at 0.35/0.60/0.75, the OSeCAN
  handoff paths and tier shifts, the 16-run global sensitivity ranking, the
  priority questions for the Taco/energy team (recovery basis, process
  availability, biomass/waste incineration assumptions, industrial feedstock
  demand), and non-claims. Non-open-tree-list provinces are covered by the E2
  open-data imputation path: SCANFI v2 structure, NFI standard-report
  summaries, donor distributions, and explicit imputed/synthetic tree lists
  with E1/E2/E3 evidence tiers. The model is complete without any DUA, request,
  or approval; optional requests only improve evidence.
  `docs/data-request-magplot-tier2.md` is retained as an optional, non-blocking
  note and is **not** a milestone, gate, or dependency. Documentation only; no
  code/model/UI change, no new tag, no public OSeCAN change, and no unexpected
  `D`.
- [x] P7.22 Implement open-data tree-list imputation for the nine
  non-measured provinces. Completed: added
  `workflows/national/bucking/open_data_imputation.py` and
  `tests/test_open_data_imputation.py`; wrote
  `data/derived/national/bucking/open_data_imputation_v1/`. The artifact
  covers 13 provinces: BC/QC/NB/YT remain E1 measured (591 stems), and
  AB/SK/MB/ON/NS/PE/NL/NT/NU are E2 `open_data_imputation` lists (324 stems)
  built from SCANFI v2 province composition, deterministic donor DBH/height
  draws from BC/QC/NB/YT measured stems, and labelled BC Kozak or QC
  Schneider donor tapers. Imputed rows are `measured=false`, `imputed=true`,
  `synthetic=false`, with stem-only residue. Tests enforce 13-province
  coverage, E1/E2 provenance, taper labels, volume/residue balance, no NaN or
  negative core numeric outputs, and deterministic regeneration. Facility
  coefficient and RSYC comparisons flag where donor coefficients and E2
  imputation diverge; no calibration or national claim. No DUA, request,
  restricted data, UI change, or official OSeCAN change; no unexpected `D`.

- [x] P7.23 Integrate E2 imputed coefficients and branch/foliage residue into
  the 13-province facility chain. Completed: added CFS branch/foliage allometry
  for the 324 E2 stems with explicit species/group proxies, and a 13-province
  E1/E2 coefficient table; E1 measured covers BC/QC/NB/YT and E2 imputed
  covers AB/SK/MB/ON/NS/PE/NL/NT/NU with no donor product coefficients.
  Rebuilt the product chain as `national_product_chain_v4` and propagated it
  through `facility_residue_supply_v6`, `national_residue_curve_v6`,
  `national_facility_chain_scenarios_v6`, `forestry_osecan_handoff_v7` and
  `forestry_osecan_scenarios_v6` for the 100/119/150 Mm³ cases and
  0.35/0.60/0.75 recovery scenarios. At 119 Mm³ E1/E2 gives process residue
  505.333 PJ, gross harvest residue 390.418 PJ, reference-recoverable harvest
  residue 234.251 PJ and total gross residue 895.751 PJ; prior donor-product
  v5 gives 514.614 / 313.640 / 195.433 / 828.254 PJ. OSeCAN harvest 60% moves
  from T4 to above_T4; process tiers are unchanged. Labels remain
  `model_estimate=true`, `observed=false`, `policy_grade=false`,
  `national_claim=false`; no DUA, restricted data, UI change, official OSeCAN
  change, or unexpected `D`.

- [x] P7.24 Freeze and document the 13-province open-data facility chain v3.
  Completed: updated `docs/model-card-national-facility-chain.md` for the
  inventory v1 → capacity v3 → assignment v6 → product-chain v4 → facility
  residue v6 → residue curve v6 → chain scenarios v6 → OSeCAN handoff v7
  chain; added `docs/handoff-national-facility-chain-v3.md`. E1 measured
  coefficients cover BC/QC/NB/YT (591 stems) and E2 open-data imputation
  covers AB/SK/MB/ON/NS/PE/NL/NT/NU (324 stems, 36 per province); no donor
  provincial product coefficient is applied, and retained donor within-class
  splits, routing, recovery fraction, and mill conversion ratios are labelled.
  At 119 Mm³ v3 gives solid product 42.291 Mm³ (−10.35% vs donor product),
  pulp 10.824 M odt (+13.62%), process residue 505.333 PJ (−1.80%), gross
  harvest residue 390.418 PJ (+24.48%), recoverable harvest 234.251 PJ
  (+19.86%), and total gross residue 895.751 PJ (+8.15%). OSeCAN harvest 60%
  moves from T4 to above_T4; process tiers are unchanged
  (T1/T3/above_T4/above_T4); all 13 zones are mapped with no `UNMAPPED` bucket
  and the official OSeCAN tree is unchanged. The model is complete on open
  data with no request, DUA, or approval dependency. Created annotated tag
  `national-facility-chain-v3` at parent `029eef1` and recorded data commit
  `d77472c`. Documentation and tag only; no code/model/UI change, no public
  OSeCAN change, and no unexpected `D`.
- [x] P7.25 Publish the national facility chain v3.1 documentation superset and
  correct the v3 publication ordering. Completed: confirmed
  `national-facility-chain-v3` still points at model commit `029eef1` and that
  the v3 documentation commit `e279371` is after it. Ran the v3 artifact
  contract tests (7 passed) and the pre-freeze checks: all nine v3 artifact
  manifests are present; the official OSeCAN tree is unchanged (146 files,
  tree SHA-256 `80caa91c…`); 65 of 66 manifest output entries verify, with one
  known stale `facility_residue_supply_v6/summary.json` entry caused by the v6
  wrapper rewriting the summary after the shared manifest (artifact validation
  is 14/14 pass; no number changed); no unexpected `D`; tracked worktree clean.
  Added the TSA24 sort status to the model card and handoff: 341 of 558
  comparable managed-AU plots, mean per-plot TVD 0.505, pooled TVD 0.433,
  `pass_fail=not_assessed_no_pre_registered_tolerance`, grade
  `not_assigned`, and `official_grade_validation=false`. Documented donor
  tapers in 11 of 13 jurisdictions, E2 donor DBH/height and 1,000 stems/ha
  expansion, CFS allometry proxies, class-median and floor-scaled capacity,
  OSeCAN proxy zone weights, and the NU zero-harvest / NT cross-border cases.
  Recorded that `national-facility-chain-v2.1` and
  `national-chain-sensitivity-v1` are v2-pinned addenda, not rebuilt on v3.
  Added the v3 artifact manifest list. Updated
  `docs/data-request-magplot-tier2.md` to point at the v3 handoff and removed
  the pre-v3 donor-product wording; it remains optional and non-blocking.
  Created annotated tag `national-facility-chain-v3.1` at the documentation
  commit, recording model parent `029eef1` and data commit `d77472c`; v3 was
  not moved. Documentation and tag only; no code/model/UI change, no official
  OSeCAN change, and no unexpected `D`.
- [x] P7.26 Audit open provincial forest inventories for the nine E2
  provinces. Completed: added
  `workflows/national/inventory/provincial_open_inventory_audit.py` and
  `tests/test_provincial_open_inventory_audit.py`, and wrote
  `data/derived/national/inventory/provincial_open_inventory_audit_v1/` for
  AB/SK/MB/ON/NS/PE/NL/NT/NU. The source table records 10 sources (PE has two
  complementary products) with exact URLs, licences, access modes, coverage,
  format, year and stand-vs-cell level, plus field coverage and gaps. Small
  verified samples cover AB AVI Crown (402 polygons; species/height/age, no
  volume), SK FRI Forest Stands (500; height/volume, no explicit species/age),
  MB 1970s FRI (316; species/age, no height/volume), NS Forest Inventory (299;
  species/height/volume, no age), PE CLUI 2010 (500; species/height, no
  volume/age) with PE EFI2020 (500; height/volume, no species/age), and NL Land
  Cover (200; broad class only). ON FRI stand attributes require a LIDS
  account/order, NT exposes only community wooded-area classes, and NU has no
  territorial inventory; those are recorded as access/coverage gaps and were
  not sampled as stand attributes. Sample and full-artifact SHA-256 provenance
  is in `manifest.csv`; no full layer was ingested, no request/DUA/restricted
  data was used, and no synthetic or model attribute is claimed. No unexpected
  `D`.
- [x] P7.27 Fix the v3 manifest integrity exception and publish v3.2.
  Completed: corrected the `facility_residue_supply_v6/manifest.json`
  `summary.json` entry to the committed file (23,643 bytes; SHA-256
  `778a02eb…`) in data commit `ef64c90`; no numeric artifact, coefficient,
  validation result, code, UI, or official OSeCAN file changed. All 66 output
  entries across the nine v3 manifests verify (previously 65/66). Focused
  regression tests passed (29 passed):
  `test_e1_e2_facility_chain_v6.py`, `test_open_data_imputation.py`,
  `test_four_province_bucking_pilot.py`, and
  `test_four_province_biomass_allometry.py`. The official OSeCAN tree is
  unchanged (146 files, tree SHA-256 `80caa91c…`); no unexpected `D`. Updated
  the model card, v3 handoff, ROADMAP, and CHANGE_LOG to v3.2 and removed the
  known exception note. Created annotated tag `national-facility-chain-v3.2`
  at the fix commit, recording that commit and data commit `ef64c90`; v3 and
  v3.1 were not moved. Manifest metadata and documentation only; no
  model-value, code, UI, or official OSeCAN change.

- [x] P7.28 Build E2 v2 open-inventory-informed tree lists. Completed: added
  `workflows/national/inventory/provincial_open_inventory_distributions.py`,
  `workflows/national/bucking/open_data_imputation_v2.py`,
  `tests/test_open_data_imputation_v2.py`, and
  `docs/handoff-e2-v2-open-inventory-tree-lists.md`. Derived
  `provincial_open_inventory_distributions_v1` from AB AVI (full FGDB), SK FRI
  (500-stand sample), MB FRI 1970s (full FGDB), NS Forest Inventory (full DBF),
  and PE CLUI/EFI (500-row samples), with area-weighted species composition and
  height/DBH/age/volume quantiles plus SHA-256 provenance. Built 36 stems per
  province for AB/SK/MB/ON/NS/PE/NL/NT/NU (324 total) and tiered them
  `E2b_open_inventory_informed`, with `inventory_informed=true` for the five
  provinces that have local distributions and false for the four donor-only
  provinces. AB DBH is `inferred_dbh` from a fitted log-log DBH-on-height
  equation; SK uses `inventory_qmd` paired marginals; NS uses `local_joint`
  stand rows; PE uses `paired_multi_source` with an algebraic EFI QMD
  inference; MB and the four donor-only provinces use `donor_joint`. Bucking
  and residue use the existing published donor tapers and national sorts. The
  v1→v2 comparison reports species L1 distance, mean height/DBH, solid/pulp
  shares and residue ODT/m3. All checks pass, including 9 provinces × 36 stems,
  provenance/distribution labels, volume/residue conservation, no NaN or
  negatives, and deterministic regeneration. No request/DUA/restricted data,
  no UI/official OSeCAN change and no national claim. No unexpected `D`.
- [x] P7.29 Build E2 v3 density-calibrated tree lists. Completed: added
  `workflows/national/inventory/provincial_open_inventory_density.py`,
  `workflows/national/bucking/open_data_imputation_v3.py`,
  `tests/test_open_data_imputation_v3.py`, and
  `docs/handoff-e2-v3-density-calibrated-tree-lists.md`. Derived
  `provincial_open_inventory_density_v1` with province-specific expansion:
  SK 2,033.65 stems/ha from FRI `BA`/`QMD`-implied total density (`MSTEMS`
  cross-check), NS 1,409.82 from `TOTBA`/`AVDI` (`TOTMRVOL` check), PE 802.84
  from EFI `tphmerch` (`gtv` check), MB 2,050.00 from median measured donor
  plot density, and AB/ON/NL/NT/NU 1,000.00 as labelled AVI-class or
  no-open-density proxies. Built 36 stems per province (324 total) with tier
  `E2c_open_inventory_density_calibrated`, keeping E2 v2 geometry, donor
  tapers and bucking; only expansion changed. SK volume rises 43.6→88.7
  m3/ha-eq against FRI `GBVOL` 100.1; NS 108.1→152.3 against `TOTMRVOL` 105.2;
  PE 145.5→116.8 against EFI `gtv` 170.8; all three are within the ±50%
  screening tolerance. Per-m3 product/residue coefficients are unchanged.
  Added `open_data_imputation_v3/` with tree list, logs, residue ledger,
  coefficients, v2→v3 comparison, density calibration, volume context and
  distribution labels. All 23 focused v2/v3 tests pass, including
  density/expansion, volume/residue conservation, open-volume comparison,
  no NaN/negative and deterministic regeneration. Data commit `3fdb0f2`;
  no request/DUA/restricted data, no UI/official OSeCAN change and no national
  claim. No unexpected `D`.

- [x] P7.30 Integrate E2c density-calibrated coefficients into facility
  chain v4. Completed: added the E1+E2c coefficient boundary and rebuilt
  product-chain v5, facility residue v7, residue curve v7, chain scenarios v7,
  OSeCAN handoff v8 and OSeCAN scenarios v7. The 13-jurisdiction coefficient
  artifact carries 4 E1 measured and 9 E2c rows with zero donor product
  coefficients. The 100/119/150 Mm³ cases keep assignment v6 / capacity v3.
  At 119 Mm³, solid product falls 10.92% (42.291→37.674 Mm³), pulp rises
  11.47% (10.824→12.066 M odt), process residue falls 1.30%
  (505.333→498.751 PJ) and harvest gross rises 13.83% (390.418→444.411 PJ);
  harvest 60% is 266.646 PJ. The frozen v3.2 chain uses E2 v1, not E2 v2;
  E2 v2 and E2c per-m³ coefficients are identical. Open-volume checks pass for
  SK/NS/PE within ±50% and are `not_available` for the other six E2c
  jurisdictions. Handoff v8 covers all 13 OSeCAN zones with no `UNMAPPED`
  bucket and 119 handoff rows; harvest 35% moves T3→T4 while harvest 60/75%
  and all process tiers are unchanged. All 27 focused tests pass; no
  NaN/negative, no UI/official OSeCAN change and no national claim. No
  unexpected `D`.
- [x] P7.31 Freeze and tag the E2c 13-jurisdiction chain v4. Completed:
  updated `docs/model-card-national-facility-chain.md` for the inventory v1 →
  capacity v3 → assignment v6 → product-chain v5 → facility residue v7 →
  residue curve v7 → chain scenarios v7 → OSeCAN handoff v8 → OSeCAN
  scenarios v7 chain, and updated `docs/handoff-national-facility-chain-v4.md`
  with the freeze record. Recorded that v3.2 froze E2 v1, E2 v2 was built but
  never integrated, and v4 uses E2c (E2 v3); E2 v2 and E2c per-m³ coefficients
  are identical (54/54 rows) and density calibration is extensive. At
  119 Mm³, E2c changes solid product 42.291→37.674 Mm³ (−10.92%), pulp
  10.824→12.066 M odt (+11.47%), process residue 505.333→498.751 PJ (−1.30%),
  harvest gross 390.418→444.411 PJ (+13.83%), and total gross 895.751→943.162
  PJ (+5.29%). Open-volume checks pass for SK/NS/PE within ±50%; AB, MB, ON,
  NL, NT and NU are `not_available`. OSeCAN handoff v8 covers 13 zones with
  119 rows and no `UNMAPPED`; harvest 35% moves T3→T4 and all other tiers are
  unchanged. Pre-freeze checks: 27 focused tests pass; all seven v4 manifests
  (47 entries) and three upstream manifests (25 entries) verify, 72/72; the
  official OSeCAN tree hash is unchanged (146 files, `80caa91c…`); a clean
  rebuild reproduces every numeric CSV byte-for-byte; no unexpected `D`.
  Created annotated tag `national-facility-chain-v4` at the documentation
  commit, recording model commit `6040963` and data commit `f0f77a0`.
  Documentation and tag only; no code/model/UI change and no official OSeCAN
  change.
- [x] P7.32 Draft the v4 energy-team handoff memo and email. Completed: added
  `docs/energy-handoff-v4.md` as the decision-oriented companion to the v4
  technical handoff. Covers 13/13 jurisdictions (4 E1 measured BC/QC/NB/YT plus
  9 E2c density-calibrated AB/SK/MB/ON/NS/PE/NL/NT/NU), zero donor product
  coefficients, and no request/DUA dependency. At 119 Mm³: solid product
  37.674 Mm³, pulp 12.066 M odt, process residue 498.751 PJ, harvest gross
  444.411 PJ, recoverable 155.544/266.646/333.308 PJ at 0.35/0.60/0.75, and
  total gross 943.162 PJ. Records OSeCAN handoff v8 tiers (process
  T1/T3/above_T4/above_T4; harvest T4/above_T4/above_T4), the unchanged
  official OSeCAN tree (146 files, `80caa91c…`, 0 changed files), four
  decisions for the Taco/energy team (branch/foliage recovery basis,
  process-residue availability, biomass/waste incineration assumptions,
  industrial chemical feedstock versus energy demand), limitations
  (`not_assigned` grade, donor tapers/capacity/zone weights, `not_available`
  provinces), non-claims, and an 8-line draft email. Documentation only; no
  code/model/UI change, no official OSeCAN change, no request/DUA, no new tag.
  No unexpected `D`.
- [x] P7.33 Refresh the national chain Plackett-Burman sensitivity on v4
  E1+E2c. Completed: added
  `workflows/national/facilities/national_chain_global_sensitivity_v2.py`,
  `facility_residue_supply_v7_trade.py`,
  `tests/test_national_chain_global_sensitivity_v2.py`,
  `tests/test_facility_residue_supply_v7_trade.py`, and
  `docs/handoff-national-chain-sensitivity-v2.md`. Rebuilt the same 7-factor
  16-run PB design on chain v7/supply v7 with donor v1 vs E2c, local v6 vs
  trade v5, stem vs total residue, 0.35/0.75 recovery, 0/75% availability,
  economics v2.3 price cases, and OSeCAN handoff v8 zone weights. At 119 Mm³
  local E2c total: process base 498.751 PJ, harvest gross 444.411 PJ,
  recoverable 60% 266.646 PJ, BC process at 50% 90.705 PJ; the derived trade
  E2c BC process is 253.214 PJ. Overall v1/v2 ranks are unchanged
  (availability 1, residue 2, price 3, recovery 4, coefficients 5,
  assignment 6, zone 7), but scores move for coefficients (0.0933→0.0459),
  residue (0.4127→0.4423), recovery (0.1571→0.1319) and assignment
  (0.0284→0.0437). Documented that v1's `measured_v4` was the E1
  four-province chain, not E2 v1; that E2c-total uses full 13-province
  branch/foliage while donor-total remains a measured-four-province partial
  lower bound; and that the price BC and assignment recoverable effects are
  three-factor aliasing artifacts. 18 focused tests pass; ruff and mypy
  (skip imports) pass on new files; manifests verify; no UI/official OSeCAN
  change and no policy/national claim. Created tag
  `national-chain-sensitivity-v2`. No unexpected `D`.
- [x] P7.34 Expand the OSRM road-distance audit and rerun the local-processing
  assignment with province factors. Completed: added
  `facility_road_distance_audit_v2.py`, `national_facility_assignment_v8.py`,
  their focused tests, and the two data artifacts. The audit sampled 81 pairs
  across 11 jurisdictions with eligible located facilities (10 per province
  where available; SK/MB/PE/YT sampled in full; NT/NU have no eligible located
  facility) at deterministic equal-frequency haversine quantiles, with 81/81
  OSRM demo routes at 1.2 s spacing and no paid key. Eleven province median
  road/haversine factors were fitted (BC 1.474, AB 1.305, SK 1.397, MB 1.456,
  ON 1.359, QC 2.418, NB 1.488, NS 1.204, PE 1.110, NL 2.004, YT 1.725),
  with n, IQR, 90% bootstrap CI, physical/Tukey outlier flags and a [1.0, 3.0]
  cap reported. Mean absolute proxy error improves from 18.86% (global 1.3) to
  8.87% (province factors); within-10% improves from 37.0% to 67.9%. V8 keeps
  capacity v3, the 1.2x floor, products and residue unchanged. At 119 Mm3 it
  assigns 118.977 Mm3 with 118.941 Mm3 local, 36,376 m3 cross-border, 39.89%
  proxy, 208 facilities and HHI 0.01287, identical to v6 because a uniform
  province factor cannot reorder local candidates; the NT residual recipient
  did not switch. The two reference scenarios change only cross-border
  destinations (NT in `published_plus_proxy`, MB in `published_only`); their
  local/cross-border totals are unchanged. Volume-weighted mean road distance
  rises 402.12 to 558.54 km (+38.9%). Labels remain `model_estimate` /
  `observed=false` / `policy_grade=false`; no national single factor, no
  national claim, no UI or official OSeCAN change. Fifteen focused tests pass;
  111 existing assignment/audit/UI tests pass. No unexpected `D`.

- [x] P7.35 Integrate transport-aware assignment v10 into facility chain v5 and
  freeze v5. Completed: added `facility_residue_supply_v8`,
  `national_residue_curve_v8`, `national_facility_chain_scenarios_v8`,
  `forestry_osecan_handoff_v9`, `forestry_osecan_scenarios_v8`,
  `tests/test_national_facility_chain_v5.py`, and five data artifacts. The
  119 Mm3 reference uses assignment v10 node transport-aware flows; the 100
  and 150 Mm3 sensitivity cases remain assignment-v6 local-first because v10
  is solved on the 119 Mm3 allocation. National products and residue are
  invariant: process residue 498.751 PJ, harvest gross 444.411 PJ, harvest
  recoverable 60% 266.646 PJ, total gross 943.162 PJ. Assignment v10 uses 296
  facilities versus 208, cross-border volume rises 0.036 to 2.325 Mm3
  (0.031% to 1.954%), proxy share rises 39.89% to 54.08%, transport cost falls
  4.958 to 3.392 billion CAD/yr (-31.59%), mean road distance falls 402.12 to
  270.29 km (-32.78%), the >=150 km share falls 83.83% to 48.62% (-35.21 pp)
  and the >=300 km share falls 52.65% to 30.16% (-22.49 pp). Facility
  process-residue distribution uses 271 positive-residue facilities versus
  186; cross-border residue rises 5.22% to 8.50%, HHI falls 0.028873 to
  0.023211 and top-5 share falls 29.51% to 24.67%. OSeCAN handoff v9 and
  scenarios v8 keep all 21 tier positions unchanged; process-residue PJ
  shifts between zones (process 50%: Q1 -5.505, Q2 -3.509, O1 -0.878,
  L1 +6.708, T1 +2.634 PJ), while harvest zone PJ is unchanged. Transport
  cost is reported only in the facility chain and is not in OSeCAN official
  tiers. Pre-freeze checks: 8 focused v5 tests and 37 existing tests pass (45
  total); five v5 manifests (46 entries) plus assignment v10, product-chain
  v5 and capacity v3 manifests (27 entries) verify 73/73; the official
  OSeCAN tree is unchanged (146 files, `80caa91c...`, 0 changed files); no
  unexpected `D`. Labels remain `model_estimate` / `observed=false` /
  `policy_grade=false` with `national_claim=false`; no UI or official OSeCAN
  change. Created annotated tag `national-facility-chain-v5` at the
  documentation commit, recording model commit `0943b12` and data commit
  `86ad555`. Updated the model card and added
  `docs/handoff-national-facility-chain-v5.md`.

- [x] P7.36 Extend node transport-aware assignment v10 to 100 and 150 Mm3 and
  freeze v5.1. Completed: added `national_facility_assignment_v10_cases`
  (v10 case extension case files; 119 primary CSVs byte-identical), `facility_residue_supply_v9`,
  `national_residue_curve_v9`, `national_facility_chain_scenarios_v9`,
  `forestry_osecan_handoff_v10`, `forestry_osecan_scenarios_v9`, and six data
  artifacts. All three cases use the same 525 supply nodes, capacity-v3
  facilities/1.2x floor and transport cost v1 curve; the 100/150 node vectors
  scale each province's 119 Mm3 allocation. Transport cost is 2.702 / 3.392 /
  5.018 billion CAD/yr at 100/119/150 Mm3 versus 3.900 / 4.958 / 6.793 for
  assignment-v6 local-first, and mean road distance is 255.33 / 270.29 /
  319.79 km versus 375.33 / 402.12 / 438.28 km. Facilities used are 270 / 296
  / 333; cross-border volume 1.161 / 2.325 / 11.036 Mm3; proxy share 54.13 /
  54.08 / 52.97%. National products and residue are invariant at each case:
  process residue 419.238 / 498.751 / 626.865 PJ, harvest gross 373.244 /
  444.411 / 573.125 PJ, harvest recoverable 60% 223.946 / 266.646 / 343.875
  PJ. Facility process-residue HHI falls from 0.032562 to 0.023893 at 100 Mm3
  and 0.028928 to 0.019132 at 150 Mm3; positive-residue facilities rise from
  164 to 248 and 215 to 303. Curve v9 keeps 119 files byte-identical and adds
  all-case rows. Handoff v10 adds 100/119/150 case zone summaries and
  reproduces handoff v9 at 119; all 21 OSeCAN tier positions are unchanged and
  `transport_cost_in_osecan=false`. Pre-freeze checks: six v5.1 manifests
  verify 68/68 entries; 119 assignment/chain/facility/curve/handoff/scenario
  invariance guards pass; all 30 product/residue invariance rows have zero
  delta; the official OSeCAN tree is unchanged (146 files,
  `80caa91c...`); no unexpected `D`. Labels remain `model_estimate` /
  `observed=false` / `policy_grade=false` with `national_claim=false`; no UI
  or official OSeCAN change. Created annotated tag
  `national-facility-chain-v5.1` at the documentation commit, recording model
  commit `2775317` and data commit `0d3b2ff`, and added
  `docs/handoff-national-facility-chain-v5.1.md`.

- [x] P7.37 Update the Facilities view to v5.1 transport-aware assignment.
  Completed: the 100/119/150 scenario selector, province x class ledger and
  facility popups now read `national_facility_assignment_v10` per-case
  facility utilization/province CSVs instead of chain v6; capacity-v3 record
  counts, published/proxy badges and capacity columns stay unchanged. The
  national chain summary reads `national_facility_chain_scenarios_v9` and adds
  transport cost (2.702 / 3.392 / 5.018 billion CAD/yr) and mean distance
  (255.3 / 270.3 / 319.8 km); product/residue rows stay invariant. Coverage &
  evidence adds transport cost, mean distance and >=150/>=300 km shares.
  Static export schema is `facilities/2` / `ui_facilities_v2`, generated from
  33 committed CSVs with no hardcoded values. Added v10 reproduction,
  three-scenario transport, no-stale-v6 and read-only tests; all 17 Facilities
  tests and 30 UI guard/export tests pass. Desktop/mobile plus chain/table
  screenshots captured under `tmp/facilities-v51-*.png` with no horizontal
  overflow at 390 px. No static publication and no official OSeCAN change.

- [x] P7.38 Document and freeze the national transport/siting layer v1.
  Completed: added `docs/model-card-national-transport-siting.md` covering the
  11 province road factors and 81/81 public OSRM sample, transport cost v1
  (604 CAD/trip and 11.50 CAD/m3 at 100 km base), 525 supply nodes / 486
  positive, assignment v10 (118.977 Mm3, 296 facilities, 3.392 B CAD/yr,
  270.289 km mean), chain v5.1 transport (2.702 / 3.392 / 5.018 B CAD/yr at
  100/119/150 Mm3), greenfield k-median (k=20 1.749 B CAD/yr and 131.973 km
  at 119 Mm3; 1.428 / 1.749 / 2.307 B at 100/119/150), k=13 diminishing returns
  in all three cases, and the 119 Mm3 break-even threshold (40.55-59.47 CAD
  per m3/yr; 40.87 at k=20). Added the companion-card cross-reference and
  updated the v5.1 transport limitation in the facility-chain model card.
  Updated ROADMAP and CHANGE_LOG. Seven transport/siting manifests verify 64/64
  entries; no unexpected `D`. Created annotated tag
  `national-transport-siting-v1` at the documentation commit, recording data
  commits `16e979a` (assignment v10), `1f0a02a` (greenfield siting),
  `5b7d717` (break-even), and `068787d` (case screen). No code, model artifact,
  UI, or official OSeCAN change.

- [x] P7.39 Add class-specific greenfield siting screen v1.
  Completed: added `greenfield_class_siting_v1`, its focused test, planning
  note, and `data/derived/national/facilities/greenfield_class_siting_v1/`.
  The screen applies v5.1 E1/E2c province/class feedstock shares to the
  118.977 Mm3 node harvest (SAW 45.617, PULP 52.680, PANEL 7.467, PELLET
  10.925, BIO 2.287 Mm3), partitions the 591 capacity-v3 facilities by class
  (405/55/32/54/45), and runs k=0..5 unlimited-throughput class modules per
  class with exact capacitated transport-LP re-evaluation (255 solves).
  Candidate nodes are 392/421/392/421/421. Aggregate k=0 is 5.181 B CAD/yr at
  420.9 km; k=5 (25 modules) is 3.179 B at 252.4 km, with 27.87% module
  share, 63.33% >=150 km and 29.51% >=300 km, saving 2.002 B (38.64%) versus
  k=0. At the exact 20-module count match (class k=4), cost is 3.314 B at
  263.7 km versus generic k=20 1.749 B at 132.0 km (+89.51%); class k=0 is
  60.90% above generic k=0. All 129 validation checks pass; 9 new tests and
  29 existing greenfield tests pass; ruff passes. Labels remain
  `model_estimate` / `transport_only_siting_screen` / `not_investment_advice`;
  no capital, permitting, technology, landbase, class-capacity, national,
  policy, UI or official OSeCAN claim. No unexpected `D`.

- [x] P7.40 Freeze the transport/siting addendum and scope-decision pause.
  Completed: added `docs/addendum-national-transport-siting-v1.md` as a
  completed, frozen internal addendum recording the road-factor/OSRM audit v2
  (81 pairs, 11 jurisdictions; proxy error 18.86% -> 8.87%), 525 supply nodes,
  assignment v10 at 119 Mm3 (3.392 B CAD/yr, 270.289 km, 296 facilities,
  1.95% cross-border, 54.08% proxy share), chain v5.1 all cases (2.702 / 3.392
  / 5.018 B CAD/yr at 100/119/150 Mm3), transport cost v1 (11.50 CAD/m3 and
  2.83 CAD/GJ at 100 km; 51.45 CAD/m3 at 500 km; tier crossings about
  85/115/127 km at face value), generic greenfield k-median (k=20 1.749 B /
  132.0 km versus existing-only 3.220 B / 255.8 km, -45.7%, diminishing after
  k=13; 1.428 / 1.749 / 2.307 B at 100/119/150), break-even thresholds
  (37.14-63.00 / 40.55-59.47 / 45.31-74.09 CAD per m3/yr; k=20
  37.80/40.87/46.47), and class-specific siting (k=5 per class 3.179 B /
  252.4 km versus generic 20 modules 1.749 B / 132.0 km, +81.8%). Added
  pointers in both model cards and updated this roadmap and CHANGE_LOG.
  Recorded the explicit stop rule: no further proxy or siting iteration until
  Taco and the energy team confirm facility siting/transport is in C2070
  scope. Created annotated tag `national-transport-siting-addendum-v1`; no
  earlier tag moved. Documentation only; no code, model artifact, UI, or
  official OSeCAN change; no unexpected `D`.

- [x] P7.41 Add harvest-residue component breakdown provenance v1.
  Completed: added `harvest_residue_component_breakdown_v1`, its focused
  regression test, and
  `data/derived/national/facilities/harvest_residue_component_breakdown_v1/`.
  The artifact splits the 119 Mm3 chain gross harvest residue into stem
  127.832 PJ, branch 215.895 PJ and foliage 100.684 PJ; total 444.411 PJ and
  branch+foliage 316.579 PJ. E1 measured ratios cover BC/QC/NB/YT; E2c
  synthetic 36-tree ratios cover AB/SK/MB/ON/NS/PE/NL/NT; NU is zero. Tops are
  not separately modelled and no tops number is created. Recovery scenarios
  are 155.544 / 266.646 / 333.308 PJ at 0.35/0.60/0.75; no environmental or
  technical recovery constraint is modelled. Conservation, national totals,
  labels and model-card §5.3 cross-checks pass; focused tests and ruff pass.
  No UI or official OSeCAN change; no unexpected `D`.

- [x] P7.42 Build the first real WS4 forest-estate pilot from SCANFI pixels
  and RSYC curves. Completed: selected the highest-harvest BC 50-km node
  `G2|BC|S32` (228,408.1768 m3; tile G2, row 3, col 2), reconstructed its
  50-km rectangle from the parent RSYC tile, transformed it into the SCANFI
  custom Lambert CRS, and compiled 2,446,363 forest fragments (220,172.67 ha)
  into 932,041 8-neighbour blocks using four G2 RSYC curves. Published the
  12-table WS4 dataset at
  `data/derived/ws4/scanfi-pilot-G2-BC-S32/dataset/` with a hashed manifest,
  89,895-row CC schedule (17,844.30 ha), and a 50-year run. `ws4 validate`
  exits 0; simulation harvests 6,547,384.791 m3 and establishes 17,844.30 ha
  of managed area, with final managed yield 5,915,085.904 m3. The managed
  track shares the natural RSYC curve; there is no planting-response claim.
  Added `workflows/ws4_instances/build_scanfi_estate_pilot.py`, focused
  tests, and `docs/ws4-national-estate-pilot.md`. Labels remain
  `model_estimate` / `sensitivity_only`; no UI, official OSeCAN, or chain
  v5.1 change; no unexpected `D`.

- [x] P7.43 Build the national WS4 forest estate at 990-m fragments.
  Completed: changed the WS4 fragment resolution from the pilot's 30-m pixel
  to 990 m (33 × 33 SCANFI pixels) while retaining 30-m inventory reads.
  Chunk-scanned 21,246,666,606 aligned SCANFI pixels once (499 s), mapped
  7,234,331 990-m block centres through the true 50-km node allocation
  geometry, and compiled 6,807,192 forest fragments (411,250,630.32 ha) into
  1,984,017 8-neighbour blocks. Used 718 RSYC tile group curves across 933
  province-curve pairs (1,866 natural/managed tracks); broadleaf falls back to
  the tile generic curve and no species-specific curve is used nationally.
  Published the 12-table WS4 dataset at
  `data/derived/ws4/scanfi-national-990m/dataset/` with resolution labels
  `inventory_resolution=30m` and `ws4_fragment_resolution=990m`, a hashed
  manifest, a 33,284-row province-stratified CC schedule (7,514,902.98 ha),
  and a 20-year run. `ws4 validate` exits 0; simulation harvests
  968,900,774.756 m3, establishes 7,514,902.98 ha of managed area, and reports
  final managed yield 448,784,158.666 m3. Forest outside the RSYC
  tile-province allocation is reported, not imputed (4,874,088.24 ha).
  Added `workflows/ws4_instances/build_scanfi_national_estate.py`, focused
  tests, and `docs/ws4-national-estate-990m.md`. Labels remain
  `model_estimate` / `sensitivity_only`; `national_scope=true` but
  `national_claim=false`; no UI, official OSeCAN, or chain v5.1 change; no
  unexpected `D`.

- [x] P7.44 Build a WS4-driven 119 Mm3 harvest schedule and chain comparison
  from the national 990-m estate. Completed: selected 45 whole natural/
  unmanaged blocks (offset >= 40) against the 118.9773391 Mm3 provincial
  control, without scaling block volumes, and simulated one CC year at offset
  0. Achieved 118,977,392.880 m3 (+53.780 m3 overshoot) on 1,083,739.05 ha;
  every province meets or exceeds its target, NU is zero. `ws4 validate`
  exits 0 and the one-year run reproduces selected block volume and area
  exactly. Applied the frozen v5.1 per-province coefficients on
  `ws4_achieved` and `scaled_to_119m_control` bases: solid
  37,673,641.463 / 37,673,624.433 m3, pulp 12,066,317.395 / 12,066,311.941
  odt, process residue 498.751140 / 498.750915 PJ, harvest gross 444.410997 /
  444.410796 PJ, harvest 60% 266.646598 / 266.646477 PJ, total gross
  943.162137 / 943.161711 PJ versus frozen 37,673,626.761 m3, 12,066,311.270
  odt, 498.750917 / 444.410766 / 266.646460 / 943.161683 PJ. Added
  `workflows/national/facilities/ws4_chain_119m.py`, focused tests,
  `docs/ws4-chain-119m.md`, the schedule, run, and
  `data/derived/national/facilities/ws4_chain_119m/`. The residue curve and
  OSeCAN handoff were not rebuilt because the existing workflows are frozen to
  the v5.1 facility assignment and zone weights; no tier position is claimed.
  Labels remain `model_estimate` / `observed=false` / `policy_grade=false` /
  `national_claim=false`; no UI, official OSeCAN, frozen chain v5.1, or
  national WS4 dataset change; no unexpected `D`.

- [x] P7.45 Rebuild the OSeCAN handoff from the WS4-driven 119 Mm3 chain.
  Completed: added
  `workflows/national/facilities/forestry_osecan_handoff_ws4.py`,
  `tests/test_ws4_osecan_handoff.py`, `docs/ws4-osecan-handoff.md`, and
  `data/derived/national/facilities/forestry_osecan_handoff_ws4/`.
  Applied the frozen handoff-v10 13-zone weights on the explicit
  origin-province WS4 basis for both `scaled_to_119m_control` (primary) and
  `ws4_achieved`; process availability 0/25/50/75%, harvest recovery
  0.35/0.60/0.75%, LHV 18.5 GJ/ODT. Scaled national PJ: process 0 /
  124.687729 / 249.375457 / 374.063186, harvest 155.543779 / 266.646477 /
  333.308097; achieved: 0 / 124.687785 / 249.375570 / 374.063355 and
  155.543849 / 266.646598 / 333.308247. All 13 zone weights sum to one per
  province; 182 zone rows conserve national totals (max PJ gap 1.31e-11).
  Frozen v5.1 21/21 tier positions match handoff v10; WS4 119 Mm3 matches
  7/7 per basis with zero tier changes, and the 100/150 positions are marked
  not available rather than fabricated. Official OSeCAN tree unchanged
  (146 files, `80caa91c...`, 0 changed files); frozen v10/v9/WS4 input trees
  unchanged; no unexpected `D`. Labels remain `model_estimate` /
  `observed=false` / `policy_grade=false` / `national_claim=false` /
  `ws4_derived=true`; no UI, official OSeCAN, candidate-limit, or chain v5.1
  change.

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
