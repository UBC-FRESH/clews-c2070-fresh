# Production Readiness Memo — CLEWs-C2070 FRESH v0.1.0a1 Conceptual Alpha

**Document Status**: Final  
**Date**: 2026-08-09  
**Prepared by**: UBC FRESH / CLEWs-C2070 forestry data maintainers  
**Governance**: This memo supports the v0.1.0a1 Conceptual Alpha release gate (Phase 8) and governs post-alpha work planning.

---

## 1. Executive Summary

The v0.1.0a1 Conceptual Alpha release demonstrates a working diagnostic pipeline for forest-biomass supply modelling in British Columbia. The pipeline constructs a five-layer network-flow graph from supply through processing to markets, solves a linear program (LP) for cost-minimizing allocation, and validates outputs against a contract schema. Four diagnostic supply lanes are implemented and compared:

- **Synthetic BC mini** — fully synthetic baseline
- **ws3-smoke** — real ws3 Woodstock harvest-output bridge
- **SCANFI-diagnostic** — SCANFI v2 2025 inventory with synthetic placeholder yields
- **SCANFI+Gauthier-diagnostic** — SCANFI inventory with Gauthier et al. (2015) method-reproduction log yields

A tiny real-sampled SCANFI diagnostic fixture (6 cells, P6.1–P6.4) was materialized from five authorized SCANFI v2 2025 rasters. All 56 regression tests pass. The codebase is clean (`git diff --check`, `compileall`).

**Critical governance finding**: None of the diagnostic outputs constitute production evidence. All six SCANFI heuristic windows are explicitly quarantined as `SOFTWARE_FIXTURE_NOT_EVIDENCE`. Seven of eight production readiness gates remain **blocked**; one is **in_progress**.

---

## 2. What Works in v0.1.0a1 (Diagnostic Capabilities)

### 2.1 BC Mini-Network Graph Construction and LP Solve
- **Five-layer graph**: source (supply) → disposition → candidate technology → product pool → sink (market)
- **4 diagnostic supply lanes** implemented and solved to optimality:
  | Lane | Supply Source | Yield Policy | Objective (CAD) | Supply (m³) |
  |------|---------------|--------------|-----------------|-------------|
  | Synthetic BC mini | Synthetic proxy cells | Synthetic placeholder | $6,140,832 | 54,160 |
  | ws3-smoke | Real ws3 TSA24 clipped | ws3-native | $75,520 | 1,604 |
  | SCANFI-diagnostic | SCANFI v2 2025 (synthetic proxy) | Synthetic placeholder | $5,795,999 | 104,700 |
  | SCANFI+Gauthier-diagnostic | SCANFI v2 2025 (real-sampled) | Gauthier 2015 method-reproduction | $6,562,205 | 222,825 |
- **Three-way and four-way variant comparison** with objective, supply, flow, and pathway-flow deltas
- **Solver-independent** via PuLP/CBC; reproducible `solution_flow` and `solution_summary` artifacts

### 2.2 fresh_fibre Python Module
- **Schema validation** (`fresh_fibre.schema`): typed CSV-row adapters for all contract tables
- **Unit registry** (`fresh_fibre.units`): canonical commodity units, cost/price bases, graph arc units, technology conversion coefficients; cross-dimension conversion rejection
- **Contract checks** (`fresh_fibre.contract`): validates `fresh-interface-contract.csv` coverage; row-level status (`ok`, `missing_required_fields`, `missing_optional`)
- **Regression tests**: 21 tests covering graph build, LP solve, contract, unit registry, ws3 export, SCANFI/Gauthier adapters, variant comparisons, negative cases

### 2.3 ws3 Export Bridge and supply_offer Contract
- **`fresh_fibre.ws3_export`**: normalizes ws3 harvest-output → `supply_offer` contract
- **Export manifest** with row counts and SHA-256 hashes for normalized input, adapter output, downstream contract
- **Crosswalk validation** (`ws3_supply_crosswalk.csv`): maps ws3 development types → FRESH spatial units / supply nodes
- **`bc-mini-ws3-smoke` LP variant** swaps only `supply_offer.csv` with real ws3 supply

### 2.4 Tiny Real-Sampled SCANFI Diagnostic Fixture (6 cells, P6.1–P6.4)
- **Authorized 5-layer subset**: biomass, height, closure, landcover, age (2025)
- **6 heuristic 100,000-ha windows** covering BC mini spatial units:
  - `coast_wet_forest`, `coast_second_growth`, `interior_dry_forest`, `interior_cold_forest`, `interior_disturbance_mix`, `interior_residue_candidate`
- **Materialized through DataLad**: raw rasters in `data/raw/scanfi/v2/`, window extracts in `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`, manifests in `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`
- **Parent-repo derived table**: `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv` (6 rows, full provenance columns)
- **All diagnostic acceptance checks pass** (coverage, provenance, range, CRS, checksum, value-policy labels)

### 2.5 CanLaBS v2 Raw dNBR Provenance Extraction
- **10 new provenance columns** added to `diagnostic_inventory_cells.csv`:
  `canlabs_dnbr_mean`, `canlabs_dnbr_min`, `canlabs_dnbr_max`, `canlabs_dnbr_median`, `canlabs_valid_pixels`, `canlabs_nodata_pixels`, `canlabs_total_pixels`, `canlabs_value_policy`, `canlabs_window_bounds`, `canlabs_source_path`
- **Verified fresh transfer**: 1,751,083,497 bytes, SHA-256 `152ba33cca7c473ddc6c7d804d0855581f6d734b50f300637c8f2ee040ac5e8b`, matches server Content-Length
- **Archived** to DataLad at `data/raw/nrcan/canlabs_v2/` and pushed to arbutus-s3

### 2.6 CanLaD Disturbance Layers Acquired and Archived
- **CanLaD** (1985–2025): 42 annual rasters + 5 latest mosaics, HTTP 200 confirmed 2026-08-05
- **NFDB fire polygons** (large-fire subset): 647 MB, acquired and archived 2026-08-05
- **NFDB fire points** (large-fire subset): 65 MB, acquired and archived 2026-08-05
- **NBAC**: archived 2026-05-13
- **Pre-CanLaD v2** (1965–1984): 4 core rasters accepted; mirroring pending PI approval

### 2.7 NFI Standard Reports and NFDP Harvest Tables Promoted
- **NFI standard reports**: license review resolved 2026-08-08 (non-commercial reproduction with attribution, compatible with OGL); promoted to `required-datasets.yaml` with `datalad_mirror.include=yes`
- **NFDP harvest tables**: OGL Canada v2.0 confirmed; promoted to `required-datasets.yaml`

### 2.8 10 Jurisdiction-Specific Forest Road Sources Added as Candidates
| Jurisdiction | Source | Purpose |
|--------------|--------|---------|
| BC | Forest Resource Roads | Operational access modelling |
| AB | Forest Management Unit roads | |
| SK | Forest Resource Roads | |
| MB | Forest Management Boundaries roads | |
| ON | Forest Management Unit roads | |
| QC | Forest Resource Roads | |
| NB | Forest Management Roads | |
| NL | Forest Resource Roads | |
| YT | Forest Resource Roads | |
| NT | Forest Resource Roads | |

These follow up on the NRN coarse national baseline accepted in `required-datasets.yaml`.

### 2.9 FreshForge/DataLad Materialization Workflow
- **CLEWS-owned provider** (`freshforge.providers.clews.materialization`)
- **Public-safe workflows** targeting `data/metadata` and `data/manifests` only
- **Tests** for provider metadata, overlay parsing, command generation, plan, report-only smoke execution
- **Repo-root `.venv` bootstrap** documented for Python, DataLad, FreshForge, git-annex

---

## 3. What Is Explicitly Diagnostic / Quarantined (NOT Production Evidence)

| Component | Status | Rationale |
|-----------|--------|-----------|
| **All 6 SCANFI heuristic windows** | `SOFTWARE_FIXTURE_NOT_EVIDENCE` | Heuristic 100,000-ha squares; not statistically defensible sample of BC. Quarantine labels applied 2026-08-08 per `scientific-model-recovery-plan.md` G1. |
| **Yield policy (Gauthier)** | Diagnostic only — 0.75 m³/ha placeholder | Method-reproduction from Gauthier et al. (2015) coefficients only; no calibration to BC inventory, no species×grade disaggregation, no multi-rotation WS3 workflow. |
| **Residue yield** | No calibrated policy; placeholder only | `RESIDUE_VALUE_POLICY` = `placeholder_residue_not_calibrated`; candidate paths documented in `prototype-gap-register.csv` but no provincial residue survey or economic availability study acquired. |
| **Cost surfaces** | NRN coarse baseline only | NRN accepted as national haul-distance baseline; 10 jurisdiction forest road candidates identified but **not acquired or operational**. No delivered-cost calibration. |
| **Climate predictors** | None acquired; ERA5 candidate only | Gauthier temperature/precipitation normals unresolved; ERA5 reanalysis identified but licensing and variable selection pending. |
| **Species composition** | SCANFI spsCC_* layers deferred (123 GB) | Species crown-closure layers not in authorized subset; requires separate authorization and provenance resolution. |
| **NFI ground plots** | Not accessible | Standard reports summary validation only; ground plot access subject to NFI approval. NFI independence from SCANFI training unverified (circularity risk). |
| **National scaling** | BC mini only | No provincial inventories, provincial yield curves, full transport network, or current facility layer. |
| **Facility layer** | Legacy 2013 database only | No current authoritative national sawmill/pulp/pellet facility source identified; 2013 database requires update and license/provenance resolution. |
| **CanLaBS v2 provenance gate** | Open | Local/server size mismatch must be resolved by fresh transfer and checksum comparison before model use or DataLad archiving (though fresh transfer verified 2026-08-08, publisher hash not advertised). |

---

## 4. Readiness Gates Status (from `metadata/readiness-gates.yaml`)

| Gate ID | Name | Status | Required For | Blocking Datasets |
|---------|------|--------|--------------|-------------------|
| `nfi_access` | NFI Ground Plot & Reference Pixel Access | **blocked** | national biomass calibration, composition validation, age class validation | `nfi_ground_plot_data` |
| `climate_predictors` | Climate Predictor Variables | **blocked** | Gauthier yield climate response, ws3 growth transitions, disturbance-climate coupling | `era5_climate` |
| `residue_yield_policy` | Residue Yield Policy Calibration | **blocked** | residue supply node quantities, biomass availability, harvest cost allocation | *(none — policy work)* |
| `calibrated_cost_surfaces` | Calibrated Transport & Processing Cost Surfaces | **blocked** | LP network-flow cost minimization, delivered feedstock cost screening, facility location optimization | `nrn`, `forest_resource_roads` |
| `species_composition` | Species Composition Layers | **blocked** | SCANFI species crown-closure, Gauthier conifer/deciduous yield curves, product-grade disaggregation | `scanfi_v2` (spsCC_*) |
| `national_scaling` | National Scaling Beyond BC Mini | **blocked** | national biomass supply modelling, inter-provincial trade, national carbon accounting | `scanfi_v2`, `provincial_forest_inventories`, `provincial_yield_curves` |
| `facility_layer` | Forest Industry Facility Layer | **blocked** | demand node definition, processing capacity constraints, commodity cascade routing | `forest_facilities_2013` |
| `disturbance_calibration` | Disturbance History Calibration | **in_progress** | ws3 forest estate initialization, age class distribution, disturbance return intervals | `canlad`, `cwfis_nfdb_fire_polygons`, `cwfis_nfdb_fire_points`, `nbac` |

**Summary**: 7 blocked, 1 in_progress, 0 resolved.

---

## 5. Post-v0.1.0a1 Work Required for Production Readiness (Gate-by-Gate)

### Gate 1: `nfi_access` — NFI Ground Plot & Reference Pixel Access
**Status**: Blocked  
**Dependencies**: None (independent access request)  
**Work Required**:
- Submit formal NFI data request for ground plots and reference pixels
- Resolve license terms for model calibration/validation use (beyond standard reports)
- Verify SCANFI training independence from NFI plots (circularity check)
- Define sampling protocol for validation (probability/systematic, not purposive)
**Estimated Effort**: 6–18 months (external approval process)  
**Blocker**: NFI approval timeline; circularity risk if SCANFI trained on NFI plots

### Gate 2: `climate_predictors` — Climate Predictor Variables
**Status**: Blocked  
**Dependencies**: `nfi_access` (for validation), Gauthier method input spec  
**Work Required**:
- Confirm ERA5 licensing for derived climate normals (temperature, precipitation)
- Select variables and temporal normals period matching Gauthier requirements
- Acquire and preprocess ERA5 or alternative (ClimateNA, Daymet) for Canada
- Validate against independent station data
**Estimated Effort**: 2–4 months  
**Blocker**: Licensing clarity; variable selection must align with Gauthier coefficient expectations

### Gate 3: `residue_yield_policy` — Residue Yield Policy Calibration
**Status**: Blocked  
**Dependencies**: Provincial residue survey data; economic availability study  
**Work Required**:
- Acquire provincial harvest residue survey data (BC, AB, ON, QC at minimum)
- Commission or adopt economic availability model (collection, transport, processing cost thresholds)
- Define residue yield factors by species, region, harvest system
- Resolve allocation between merchantable and residue streams in cost accounting
**Estimated Effort**: 6–12 months  
**Blocker**: No candidate dataset in `required-datasets.yaml`; requires new data acquisition partnerships

### Gate 4: `calibrated_cost_surfaces` — Calibrated Transport & Processing Cost Surfaces
**Status**: Blocked  
**Dependencies**: `nrn` (baseline), 10 jurisdiction forest road sources (candidates), facility layer  
**Work Required**:
- Acquire and integrate 10 jurisdiction-specific forest/resource road layers
- Build spatial network with road class, surface, speed, jurisdiction attributes
- Calibrate log haul cost functions ($/m³/km) by road class and truck configuration
- Calibrate residue transport cost functions (chip van, walking floor, rail)
- Calibrate processing costs by facility type (sawmill, pulp, panel, pellet, cogeneration)
- Validate against published freight rate surveys and mill gate cost studies
**Estimated Effort**: 8–16 months  
**Blocker**: 10 road sources are candidates only — not acquired; license/access review needed per jurisdiction

### Gate 5: `species_composition` — Species Composition Layers
**Status**: Blocked  
**Dependencies**: `scanfi_v2` spsCC_* authorization; `national_scaling` inventory  
**Work Required**:
- Obtain authorization for SCANFI v2 species crown-closure layers (123 GB)
- Download, verify, and archive species layers via DataLad
- Define coniferous/deciduous aggregation rules matching Gauthier yield curves
- Define product-grade disaggregation rules (lumber, pulp, panel) by species group
- Validate against provincial forest inventories and NFI species summaries
**Estimated Effort**: 4–8 months (post-authorization)  
**Blocker**: 123 GB payload; separate NRCan authorization required; storage/compute for national species layers

### Gate 6: `national_scaling` — National Scaling Beyond BC Mini
**Status**: Blocked  
**Dependencies**: **ALL OTHER GATES** (this is the integration gate)  
**Work Required**:
- Accept national wall-to-wall inventory (SCANFI v2 full or provincial inventories)
- Accept provincial yield curves for all jurisdictions
- Complete calibrated cost surfaces (Gate 4)
- Complete facility layer (Gate 7)
- Complete species composition (Gate 5)
- Build national five-layer graph with all spatial units
- Solve and validate national LP variants
**Estimated Effort**: 12–24 months (after all dependencies resolved)  
**Blocker**: Cascade dependency — cannot start until Gates 1–5, 7 substantially advanced

### Gate 7: `facility_layer` — Forest Industry Facility Layer
**Status**: Blocked  
**Dependencies**: Current authoritative facility data source identification  
**Work Required**:
- Identify current authoritative national facility database (NRCan, CFS, provincial rolls, industry associations)
- Resolve license for model use and redistribution
- Acquire facility locations, capacities, types, operational status
- Crosswalk to FRESH commodity cascade (log → lumber/pulp/panel/residue/pellet/energy)
- Validate against NFDP capacity reports and industry publications
**Estimated Effort**: 4–8 months  
**Blocker**: No current authoritative source identified; 2013 legacy database only

### Gate 8: `disturbance_calibration` — Disturbance History Calibration
**Status**: In Progress  
**Dependencies**: CanLaD, NFDB, NBAC, Pre-CanLaD v2 (all acquired)  
**Work Required**:
- **Calibrate harvest vs. fire vs. insect separation** using CanLaD disturbance types + NFDB fire perimeters + NBAC
- Validate CanLaD annual disturbance area against NFDP harvest tables and NFDB fire statistics
- Resolve CanLaBS v2 severity integration for fire disturbance characterization
- Define disturbance return intervals by TSA/ecozone for ws3 estate initialization
- Document calibration methodology and uncertainty
**Estimated Effort**: 3–6 months  
**Blocker**: Calibration methodology design; CanLaBS provenance gate; Pre-CanLaD v2 mirroring pending PI approval

---

### Priority Ordering and Dependencies

```
Priority 1 (Immediate, Independent):
  ├─ disturbance_calibration (in_progress — CanLaD/NFDB/NBAC acquired)
  └─ nfi_access (start request process immediately — long external lead time)

Priority 2 (Parallel, Dependent on Priority 1):
  ├─ climate_predictors (ERA5 licensing + variable selection)
  ├─ residue_yield_policy (provincial survey partnerships)
  └─ facility_layer (source identification)

Priority 3 (Dependent on Priority 2):
  ├─ calibrated_cost_surfaces (needs forest roads + facility layer)
  └─ species_composition (needs SCANFI spsCC_* authorization)

Priority 4 (Integration Gate — Dependent on ALL):
  └─ national_scaling
```

**Critical Path**: `nfi_access` → `climate_predictors` + `residue_yield_policy` + `facility_layer` → `calibrated_cost_surfaces` + `species_composition` → `national_scaling`

---

## 6. Release Criteria for v0.1.0a1 (Already Met)

| Criterion | Status | Evidence |
|-----------|--------|----------|
| All 56 tests pass | ✅ MET | `pytest tests/ -q` — 56 passed (21 BC mini prototype + 35 supporting) |
| `git diff --check` clean | ✅ MET | Parent repo and `data` submodule both clean |
| `compileall` clean | ✅ MET | `python -m compileall fresh_fibre tests` — no errors |
| Branch pushed to origin | ✅ MET | Phase 7 branch pushed; PR opened for v0.1.0a1 release |
| `CHANGE_LOG.md` current | ✅ MET | Updated through 2026-08-08 with all Phase 6/7 completions |
| `ROADMAP.md` current | ✅ MET | Phases 1–8 status reconciled; P7.5 (this memo) complete |

---

## 7. Governance Statement

This memo formally documents the **diagnostic-only boundary** of the v0.1.0a1 Conceptual Alpha release. No artifact, metric, or claim from the diagnostic lanes may be used to support:

- BC-scale or national biomass supply estimates
- Yield, cost, or economic calibrations
- Policy, investment, or operational decisions
- Inferential statements about forest carbon, harvest sustainability, or facility economics

The seven **blocked** readiness gates and one **in_progress** gate define the mandatory work program for any future production release. Work on Gates G2–G8 of the Scientific Model Recovery Plan remains **unapproved** and must not proceed without explicit developer approval for each gate.

**Approval**: This memo is part of the v0.1.0a1 release package and must be referenced in the GitHub release notes and announcement discussion.

---

## Appendix A: Key File References

| Artifact | Path |
|----------|------|
| Readiness gates registry | `metadata/readiness-gates.yaml` |
| Curated dataset registry | `metadata/required-datasets.yaml` |
| Phase 6 validation memo | `planning/phase-6-validation-memo.md` |
| Scientific model recovery plan | `planning/scientific-model-recovery-plan.md` |
| Conceptual alpha roadmap | `planning/conceptual-alpha-release-roadmap.md` |
| ROADMAP (all phases) | `ROADMAP.md` |
| CHANGE_LOG | `CHANGE_LOG.md` |
| Diagnostic fixture manifests | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/` |
| Parent inventory (quarantined) | `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/diagnostic_inventory_cells.csv` |
| Prototype gap register | `metadata/ffcm-archive/prototype-gap-register.csv` |
| Unit registry | `metadata/ffcm-archive/unit-registry.csv` |
| Conversion registry | `metadata/ffcm-archive/unit-conversion-registry.csv` |

---

## Appendix B: Gate-to-Dataset Traceability

| Gate | Primary Blocking Dataset(s) | Secondary / Policy Work |
|------|----------------------------|-------------------------|
| `nfi_access` | `nfi_ground_plot_data` | NFI data request, circularity verification |
| `climate_predictors` | `era5_climate` | Licensing, variable selection, normals period |
| `residue_yield_policy` | *(none)* | Provincial residue surveys, economic availability study |
| `calibrated_cost_surfaces` | `nrn`, `forest_resource_roads` (10 jurisdictions) | Haul/transport/processing cost calibration |
| `species_composition` | `scanfi_v2` (spsCC_* layers) | Authorization, 123 GB download, aggregation rules |
| `national_scaling` | `scanfi_v2`, `provincial_forest_inventories`, `provincial_yield_curves` | All other gates must be substantially resolved |
| `facility_layer` | `forest_facilities_2013` | Current source identification, license resolution |
| `disturbance_calibration` | `canlad`, `cwfis_nfdb_fire_polygons`, `cwfis_nfdb_fire_points`, `nbac` | Harvest/fire/insect separation calibration |

---

*End of Production Readiness Memo*