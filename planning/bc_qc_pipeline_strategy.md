# BC + QC Functional Pipeline → National CASFRI Deployment

| Field | Value |
|-------|-------|
| Document | `planning/bc_qc_pipeline_strategy.md` |
| Phase | 4 (NEW — BC + QC Foundation; replaces the prior closed `phase-4-real-diagnostic-input-materialization` track; distinct governance surface) |
| Status | Draft → active 2026-08-10 (companion to `metadata/source-inventory.csv` `casfri_v5_schema` promotion) |
| Owner | UBC FRESH / CLEWs-C2070 forestry data maintainers |
| Governing issue | `#1` (P1.2 source discovery); subordinate `casfri_v5_schema` child issue `#29` is the framework-side counterpart |
| Pilot evidence | `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/` (LP solves `Optimal`); commits `9c4bbf2` + `cd1ba60` |
| Companion docs | `ROADMAP.md`, `planning/qc_two_province_incremental_memo.md`, `planning/casfri-v5-schema-integration-plan.md`, `planning/p7-recovery-evidence.md`, `planning/phase-7-production-readiness-memo.md` |

---

## 1. Goal

Confidently demonstrate that the BC + QC two-province diagnostic LP pipeline is
the active evidence base for the `casfri_v5_schema` registry promotion, and is
therefore the launch pad for nationwide Tier-3 jurisdictional expansion once
the remaining readiness gates (`calibrated_cost_surfaces`, `nfi_access`,
`species_composition` wall-to-wall) clear their blocking-dataset gates.

## 2. Pipeline status (as of 2026-08-10)

Each row pins the maturity checkpoint that the BC + QC two-province diagnostic
LP fixture (`fresh_fibre.bc_qc_mini_2prov`, 2117 lines) currently satisfies.
Evidence paths point at the in-repo artefact that an audit can re-derive.

| Checkpoint | Status | Evidence (commit / file / test) |
|------------|--------|---------------------------------|
| BC + QC inventory ingest (BC VRI File Geodatabase stub + QC IEQM 5e GeoPackage cells) | ✓ | `bc_qc_mini_2prov.py::run_bc_vri_supply`/`run_qc_supply`; tests `BC VRI stub fixture ingested through fresh_fibre.bc_vri_reader; ws3 handoff bridged via tests/fixtures/bc_vri_sample/vri_to_bc_mini_mapping.csv`; tests `test_full_runner_solves_optimal`, `test_compute_bc_qc_real_supply_quantities` |
| CASFRI-shape supply offer emission (30 +3 provenance cols) | ✓ | `bc_qc_mini_2prov.py::run_bc_vri_supply` writes `supply_offer_bc_vri.csv` per the CASFRI v5 column reference in `metadata/source-crosswalks/qc_casfri_v5_schema.md` §1 |
| Species canonical (FRESH-side: BC_/QC_ prefixes preserved) | ✓ | `fresh_fibre.species_canonical` (`tests/test_species_canonical.py`); prefix policy at `qc_casfri_v5_schema.md` §3.1 |
| Real-coefficient yields (Pothier & Savard for QC softwood / Auger for QC mixedwood / Gauthier 2015 conifer–deciduous for BC) | ✓ | `fresh_fibre.gauthier_yield` Pothier/Savard + Gauthier real-coefficient rows; `compute_bc_qc_real_supply_quantities` (real path now in 80 000–95 000 m³ BC log band, 2 800–3 800 m³ QC log band) |
| Phase 3 transport-cost surface wired to LP | ✓ | `fresh_fibre.transport_cost` (CAG-friendly placeholders, 447 lines); `recompute_transport_link_costs_using_phase3_costs` writes side-by-side `transport_link_phase3_costed.csv` (12 columns) |
| LP solve `Optimal` end-to-end (BC + QC common network) | ✓ | `run_full_two_province_variant` (BC + QC facility counts 5/3; total log m³ ~ 86 500 BC + ~3 200 QC; objective ~ 6.1 M CAD); tests `test_full_pipeline_runs_optimal`, `test_phase3_transport_costs_lp_solver_no_break` |
| Cross-province pathway flows emitted (per-province keying in solution artefacts) | ✓ | `build_pathway_flow` writes `bc_qc_mini_pathway_flow.csv` keyed by `province ∈ {BC, QC}`; tests `test_pathway_flow_artefact_keys_bc_and_qc_separately`, `test_solution_flow_rows_count_matches_arcs` |

## 3. National-scale readiness valuation

The increment advances the following gates in `metadata/readiness-gates.yaml`:

| Gate | Pre-increment | Post-increment (proposed) | Justification |
|------|---------------|---------------------------|---------------|
| `species_composition` | in_progress (Tier-3 BC + QC diagnostic saturation) | in_progress (BC + QC Tier-3 now exercised; onwarding to NS/PE/YT/NL02 remains) | BC mini + QC mini both go through `fresh_fibre.species_canonical`; jurisdictional prefixes `BC_`/`QC_` preserved per `qc_casfri_v5_schema.md` §3.1; remaining wall-to-wall gap is SCANFI v2 spsCC_* layers |
| `national_scaling` | in_progress (BC + QC two-province LP path runs end-to-end) | in_progress (BC + QC is now the **first** wall-to-wall-able province pair; explicit flip recommended next cycle) | `bc_qc_mini_2prov` solves Optimal; `solution_flow`/`solution_summary` show inter-provincial pathway flows; comparison artefacts at `…/generated/bc_qc_mini_*.csv` |
| `calibrated_cost_surfaces` | blocked (NRN/NRWn vector download deferred to Phase 3.10) | blocked (unchanged — Phase 3 CAG-friendly placeholders are diagnostic, not calibrated) | NRN 2.0 / NRWN bulk GeoGratis download + jurisdiction-specific forest roads still pending; per the source-inventory plan, this gate flips only when the Phase 3.10 workstream lands |

Gates unchanged by this increment: `nfi_access`, `climate_predictors`,
`residue_yield_policy`, `facility_layer`, `disturbance_calibration`.

## 4. Next-phase Tier-3 jurisdictional expansion

The BC + QC fixture opens the door to eight additional openly-downloadable or
DUA-gated jurisdictions per `metadata/source-reviews/casfri_v5_schema.md` §3
(the 49-row truth table). Candidate sequence respects the schema-reviewer's
priority ordering: openly-downloadable first (§3.a), then mixed/jurisdiction-
gated (§3.b), then DUA-only (§3.c). Each candidate is a one-line acquisition
plan plus an evidence-link pointer.

| Priority | Jurisdiction | Openness | One-line acquisition plan | Evidence |
|----------|--------------|----------|---------------------------|----------|
| 1 | ON | Mixed-tier (GeoHub redistribution prohibited; OK for internal model use) | Pull ON FRI Term 2 extracts via signed Ontario GeoHub redistribution-clause request; onboard via `fresh_fibre.casfri_qc_reader` with ON-specific translation tables at `data/raw/casfri_v5/translation_tables/on_*` | source-reviews §3.b ON row |
| 2 | AB | DUA-only (`AEP.Data@gov.ab.ca` Linda Smith) | Open DUA per historical Phase 3 TUA template (`metadata/license-determinations/nfi-targeted-request.md`); ingest only into data submodule | source-reviews §3.c AB row |
| 3 | SK | Mixed (older vintages: SK Standard Unrestricted Use License v2.0; newer BAM/BEACON: DUA Matt Matwishyn 2025 contact) | Pull older-vintage SHP via direct download; queue newer vintages through DUA; record per-vintage split per source-reviews §3.a/§3.c reconciliation note | source-reviews §3.a + §3.c SK rows |
| 4 | MB | Mixed (Nelson River FLI 2011-13 public; MB01/02/04 industry) | Pull Nelson River FLI; coordinate with Manitoba Sustainable Development for industry-gated cycles | source-reviews §3.b MB row |
| 5 | NB | Mixed (NB03/06 open; older admin-gated via Dale Wilson NB NRRED) | Pull NB03 / NB06; DUA older cycles; register per `metadata/source-crosswalks/nb_casfri_v5_schema.md` when added | source-reviews §3.b NB row |
| 6 | NS | Openly-downloadable (per-county SHP) | Pull NS per-county SHP via provincial portal; emit one diagnostic mini per Boreal+Acadian region | source-reviews §3.a NS row |
| 7 | PE | Openly-downloadable (ArcGIS Hub) | Pull PE ArcGIS Hub download; exercise mini LP variant; record Q&A under `fresh_fibre.casfri_qc_reader` | source-reviews §3.a PE row |
| 8 | NL (NL02) | Openly-downloadable (NL GeoHub, Aug 2023) | Pull NL02 GeoHub extract; run SchemaMapper; register the cell under the provincial_forest_inventories row | source-reviews §3.a NL02 row |

Each acquisition determines whether the new jurisdiction needs a per-province
fresh `metadata/source-crosswalks/<province>_casfri_v5_schema.md` (mirroring
the QC style). The framework source is read at run-time; per-provincial
translation tables are vendored to `data/raw/casfri_v5/translation_tables/`
in the data submodule (per AGENTS.md boundary).

## 5. Phase 4 sub-task transition

This document closes Phase 4.1–4.4 of the **NEW** Phase 4 (BC + QC Foundation
for National CASFRI Model), distinct from the previously closed
`phase-4-real-diagnostic-input-materialization` track.

| Phase 4 sub-task | Status | Closing artefact |
|------------------|--------|------------------|
| 4.1 — Full pipeline (BC + QC LP solves `Optimal`; inter-provincial pathway flows emitted) | DONE 2026-08-09 (P7.1.CASFRI.5 output) | `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/`, tests `tests/test_bc_qc_mini_2prov.py` |
| 4.2 — CASFRI schema consistency (QC + BC ↔ ws3 supply_offer mapper) | DONE 2026-08-09 (P7.1.CASFRI.3 + P7.1.CASFRI.4 output) | `metadata/source-crosswalks/qc_casfri_v5_schema.md`, `fresh_fibre.casfri_qc_reader.py`, `fresh_fibre.species_canonical.py`, `fresh_fibre.qc_ws3_export.py` |
| 4.3 — Registry promotion (`casfri_v5_schema` row: candidate → accepted_subset_recommended) | DONE 2026-08-10 | `metadata/source-inventory.csv` row `casfri_v5_schema`; companion ROADMAP.md edit `P7.1.CASFRI.6`; smoke test `tests/test_bc_qc_mini_2prov.py::TestPhase4RegistryPromotion::test_casfri_v5_schema_status_promoted` |
| 4.4 — National-scale readiness valuation (this doc) | DONE 2026-08-10 | `planning/bc_qc_pipeline_strategy.md` (this file); table at §3 herein; flip recommendation for `national_scaling` recorded as a Phase 7 child-issue candidate |

Phase 4 closes with no remaining in-progress sub-tasks. Phase 5+
chain is: ON/AB/SK Tier-3 DUA campaign → `national_scaling` flip →
`calibrated_cost_surfaces` deblock via Phase 3.10 NRN/NRWn vector
ingest → wall-to-wall SCANFI spsCC_* specimen selection.

## 6. Open verification items

These items remain outside the BC + QC Foundation closure and are tracked
separately to keep the Phase 4 commit footprint narrow.

| Item | Owner | Status (as of 2026-08-10) |
|------|-------|---------------------------|
| **Tier-3 DUA status for SK** (older vintages: open; newer BAM/BEACON: DUA-only Matt Matwishyn 2025 contact) | UBC FRESH maintainers | Per-vintage split validated against `provincial_forest_inventories.md` lines 61–68 (2026-08-06 snapshot); re-verify before any new DUA is opened |
| **Tier-3 DUA status for AB** (`AEP.Data@gov.ab.ca` Linda Smith; historical FRI Phases 1-3 PDF/raster only) | UBC FRESH maintainers | Open; etiquette template uses NFI DUA style at `metadata/license-determinations/nfi-targeted-request.md` |
| **Tier-3 DUA status for NT01-03** (expired licences) and NT04 (NFIS FSS path) | UBC FRESH maintainers | Pending; NT04 is open-with-DUA |
| **National NRN 2.0 + NRWN vector download** (parallel to BC + QC two-province diagnostic but at national scale) | Phase 3.10 workstream | Deferred; `metadata/source-reviews/nrn_vectors.md` and `nrwn_vectors.md` are the reference; SAFORAH2 CSW discovery + GeoGratis catalogue walk completed in Phase 2b (1c43177) |
| **SCANFI v2 `spsCC_*` species layers authorization** (123 GB total) | NRCan / UBC FRESH | Deferred; authorisation check pending; species_composition gate stays in_progress |
| **Clean-registry self-audit** of `metadata/source-inventory.csv` | UBC FRESH maintainers | Open audit item: walk every `accepted_subset_recommended` row and confirm `next_action` is not stale; visible drift surfaces in `tests/test_bc_qc_mini_2prov.py::TestPhase4RegistryPromotion` smoke class |
| **Source-side translation-table vendoring decision** (P*.1 of the framework-pull plan) | UBC FRESH / counsel | Unresolved; current default is **NOT vendor, NOT fork** per `planning/casfri-v5-schema-integration-plan.md` |

## 7. References

### Source reviews (Phase 2b, 4 docs)
- `metadata/source-reviews/nrn_vectors.md` (NRN 2.0 GeoGratis)
- `metadata/source-reviews/nrwn_vectors.md` (NRWN GeoGratis)
- `metadata/source-reviews/cbmt_wms.md` (Canada Base Map WMS)
- `metadata/source-reviews/provincial_transport.md` (SK/NWT/YT WMS candidates)

### CASFRI review + plan (D1/D2)
- `metadata/source-reviews/casfri_v5_schema.md` (49-row truth table at §3)
- `metadata/source-crosswalks/qc_casfri_v5_schema.md` (QC ↔ CASFRI v5 schema)
- `planning/casfri-v5-schema-integration-plan.md` (D2 integration plan)

### Phase plans + memos (this Phase 4 + linking materials)
- `ROADMAP.md` (Phase 7 parent issue §`P7.1.CASFRI.1–7`, P7.1.CASFRI.6 flipped 2026-08-10)
- `planning/qc_two_province_incremental_memo.md` (QC + BC two-province incremental evidence, 352 lines)
- `planning/p7-recovery-evidence.md`
- `planning/phase-7-production-readiness-memo.md`
- `planning/phase-4-real-diagnostic-input-materialization.md` (prior closed Phase 4 — distinct from this Phase 4)

### Source registry + readiness surfaces
- `metadata/source-inventory.csv` (row `casfri_v5_schema` promoted 2026-08-10)
- `metadata/readiness-gates.yaml` (8 gates; `species_composition` and `national_scaling` in_progress; flipping recommendation deck §3)

### Orchestrator-owned plan surface (read-only, NOT edited by this Phase)
- `.opencode/plan.md` ("BC+QC Foundation for National CASFRI Model" plan)

### Source code references
- `fresh_fibre/bc_qc_mini_2prov.py` (BC + QC LP runner, 2117 lines)
- `fresh_fibre/transport_cost.py` (Phase 3 cost surface, 447 lines)
- `fresh_fibre/gauthier_yield.py` (Pothier/Savard + Gauthier real-coefficient rows)
- `fresh_fibre/casfri_qc_reader.py` (QC adapter)
- `fresh_fibre/species_canonical.py` (cross-jurisdiction species mapper)
- `fresh_fibre/qc_ws3_export.py` (QC ws3 export bridge)

### Tests
- `tests/test_bc_qc_mini_2prov.py` (11 baseline + 12 new = 23 tests, all passing on this branch)
- `tests/test_casfri_qc_reader.py` (field-mapping dispatch coverage)
- `tests/test_species_canonical.py` (unknown-species flagging)
- `tests/test_transport_cost.py` (8 test classes, 22 methods; haversine symmetry / rail vs road parity / segment-cost arithmetic)

---

*Document closes Phase 4 (BC + QC Foundation). Next-chain work is the
ON/AB/SK Tier-3 DUA campaign (subject to separate per-repository governance
review under the AGENTS.md disclosure path).*
