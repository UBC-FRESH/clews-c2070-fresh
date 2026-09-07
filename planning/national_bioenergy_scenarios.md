# National Bioenergy Scenario Simulation Design

| Field | Value |
|-------|-------|
| Document | `planning/national_bioenergy_scenarios.md` |
| Replaces | Phase-5 task 5.4 (national scenario simulation framework) |
| Companion BC + QC framer | `planning/bc_qc_pipeline_strategy.md` (national-scale readiness anchor) |
| Companion schema surface | `metadata/source-reviews/casfri_v5_schema.md` §5 (CASFRI → FRESH contract mapping) |
| Companion adapter modules | `fresh_fibre/bc_qc_mini_2prov.py`, `fresh_fibre/species_canonical.py` |
| Owner | UBC FRESH / CLEWs-C2070 forestry data maintainers |

---

## 1. Goal

Define the **national-scale bioenergy policy scenario simulation** that extends the
BC + QC two-province diagnostic LP (`planning/qc_two_province_incremental_memo.md` §5,
`Optimal` solved end-to-end) to all 12 provinces and territories. The framework is
designed so that, when each readiness gate clears (see §5), re-running the LP across
jurisdictions is a single configurable parameter rather than a fresh building exercise
per scenario. Jurisdiction prefix preservation (`BC_*`, `QC_*`, generalised to
`<province>_*` per `metadata/source-crosswalks/qc_casfri_v5_schema.md` §3.1) keeps
scenario outputs traceable to jurisdictional supply offers at the row level.

## 2. Scenario taxonomy

Five canonical scenario paths are recognised. Definitions are intentionally short —
each scenario is just a multiplier / constraint overlay on the merged supply table.

| Tag | Name | Bioenergy policy lever | Multiplier / constraint overlay |
|-----|------|------------------------|---------------------------------|
| **REF** | Reference | Current trajectory; no policy changes | Baseline (default overlay) |
| **CP** | Carbon pricing | Federal backstop + provincial carbon tax trajectory by jurisdiction | Per-jurisdiction carbon-cost adder on `supply_offer.supply_cost`; lifts effective cost of high-emission pathways |
| **RFS** | Renewable fuel standard | Wood pellet / bioenergy fuel share mandate | Demand-side floor on `commodity.commodity_id ∈ {bioenergy, pellets}` market clears |
| **SCE** | Specific carbon-equivalent reduction | Fragmented demand from forest-sector decarbonisation | Per-jurisdiction equivalence-reduction target; LP solver reports marginal cost of compliance at the binding constraint |
| **WDA** | Wildfire-driven disturbance acceleration | Climate-attributable disturbance scenarios (e.g., 1.5 °C / 2 °C pathways) | Per-cell age-class perturbation; Gauthier 2015 multiplicative climate-perturbation multiplier (`fresh_fibre/gauthier_yield`) |

Custom overlays (Tier-1.5 in `bc_qc_pipeline_strategy.md` §5 terminology) may be added
on top — e.g., federal-provincial biorefinery subsidies — without re-architecting,
because each scenario is just a JSON/YAML overlay on the merged supply table.

## 3. LP network-flow design

The LP is a classic network-flow on five node classes, building on
`fresh_fibre/bc_qc_mini_2prov.py::run_full_two_province_variant`.

| Node class | What it represents here | Provenance |
|------------|------------------------|------------|
| **Supply nodes** | One per **jurisdiction × supply_policy genre** | CASFRI-shape `supply_offer.csv` rows in `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/generated/`; generalised to 12 provinces |
| **Processing facilities** | Sawmill / pulp / panel / pellet / cogeneration facilities per jurisdiction | `network_node.csv` (BC + QC); generalise per §6 below |
| **Transport links** | Inter-provincial + intra-provincial routes between supply nodes and processing facilities, and onward to export markets | `transport_link.csv`; `fresh_fibre/transport_cost.py` for cost surface; calibrated cost surfaces still gated |
| **Demand nodes** | Markets + export sinks | `market.csv`; tiered: domestic markets, Atlantic-rim export, Pacific-rim export |
| **Lifecycle assumptions** | Emission factors, yield-curve multipliers, climate-perturbation multipliers | `fresh_fibre/gauthier_yield.py` (Pothier/Savard for QC softwood; Auger for QC mixedwood; Gauthier 2015 conifer–deciduous for BC); `fresh_fibre/canlad_dnbr.py` (CanLaBS dNBR disturbance signal) |

Each scenario is applied as a **single overlay vector** in the LP objective
function:

```
overlay[scenario_tag] := {
  "supply_multiplier": float | None,
  "cost_adder": float | None,
  "demand_floor": float | None,
  "constraint_binding_effort_indicator": str | None,
}
```

The overlay vector is loaded at LP-build time and parameter-merged into the
existing BC + QC supply-graph, leaving the LP solver a single configuration knob.

## 4. CASFRI schema role

CASFRI v5 is the **single coherent schema** across all 12 jurisdictions for
scenario-relative supply reallocation. Two schema-level guarantees:

1. **Jurisdictional-prefix preservation.** Every emitted `supply_offer.source_record_id`,
   `spatial_unit.spatial_unit_id`, `commodity.commodity_id` carries a `BC_` /
   `QC_` / generalised `<province>_*` prefix, so scenario LP solutions across
   provinces retain jurisdictional provenance at the row level. The schema is the
   carrier, not the LP solver — the LP solver is invisible to the schema choice
   because the merged supply table is the only contract surface it reads.
2. **Gauthier multiplicative climate-perturbation multiplier applied per cell.**
   When `WDA` runs, the multiplier is applied at the per-cell (`<province>_<stand>` ×
   `spsCC_*`-adjacent species) layer of the supply table. The CASFRI schema
   preserves per-cell resolution; SCANFI v2's 30 m grid preserves the same per-cell
   resolution (complementary; see `metadata/source-reviews/casfri_v5_schema.md` §2).

`fresh_fibre/gauthier_yield.py`'s Pothier/Savard + Gauthier real-coefficient rows
feed the supply-yield side. `fresh_fibre/species_canonical.py`'s
`canonicalize()` and `merge_decisions()` are reused per cell to keep species
identity stable across scenarios.

## 5. Readiness-gate dependency

A **Tier-1 national scenario** runs end-to-end only when the following five gates
simultaneously clear to `resolved`:

| Gate | Status (2026-08-09) | Required for | Blocked by |
|------|---------------------|--------------|------------|
| `species_composition` | `in_progress` (BC + QC Tier-3 evidence in hand) | per-cell species yield breakdown | SCANFI v2 `spsCC_*` (~123 GB) for wall-to-wall (national-scale resolution) |
| `national_scaling` | `in_progress` (BC + QC two-province LP fixture) | inter-provincial pathway flows | provincial yield curves + 12-province facility layer + calibrated cost |
| `calibrated_cost_surfaces` | `blocked` | LP cost minimisation objective | NRN 2.0 + NRWN bulk vector + jurisdiction-specific forest roads (Phase 3.10) |
| `climate_predictors` | `blocked` | `WDA` scenario multiplicative perturbation | ERA5 reanalysis authorisation + variable selection |
| `facility_layer` | `blocked` | demand-side processing capacity constraints | forest_facilities_2013 update + license resolution |

**Current state (2026-08-09):** 2 gates `in_progress` (`species_composition`,
`national_scaling`); 3 gates `blocked` (`calibrated_cost_surfaces`,
`climate_predictors`, `facility_layer`). **None of the five is `resolved` yet.**
Tier-1 national scenarios cannot run **today**; Tier-2/3 (defined in §6) can run
as partial-axis overlays once any subset is cleared.

`residue_yield_policy`, `disturbance_calibration`, `nfi_access` are not gates for
**Tier-1** scenario execution — they gate calibration and validation quality, not
scenario mechanics. They are surfaced in the validation policy (§7) but are not
scheduling blockers for the first national-scenario run.

## 6. Tier-1 vs Tier-2 vs Tier-3 scenarios

Scenario tiers are independent of the five-gate dependency matrix: a Tier-1 scenario
may run on a partial subset if all five gates cleared in priority order; Tier-2
extends with the next-most-impactful axis; Tier-3 enables the calibration-grade run.

| Tier | What it includes | When runnable |
|------|------------------|---------------|
| **Tier-1** | REF / CP / RFS / SCE overlays on cleared gates; jurisdiction × supply_policy genre × processing facility network | When all five gating gates are `resolved` |
| **Tier-2** | Tier-1 + ERA5 climate perturbation (the `WDA` overlay) | When `climate_predictors` flips to `resolved` |
| **Tier-3** | Tier-2 + NFI plot cross-check on residue YF + biomass regenerate from Gauthier (backstop role per `planning/nfi_status_tracker.md` §2) | When NFI plots materialise AND `residue_yield_policy` flips to `resolved` |

Each tier inherits the simple-on-top-of-Tier-1 design rule: add an overlay vector;
do not re-solve the LP from scratch.

## 7. Validation policy

Scenario outputs are cross-checked at three orthogonal layers:

- **CanLaBS dNBR disturbance signal cross-check.** `fresh_fibre/canlad_dnbr.py`
  emits per-cell dNBR values from the CanLaBS v2 archive. A scenario run that
  contradicts the historical dNBR signal (e.g., WDA scenario emits 30-year acreage
  reductions inconsistent with observed fire history) triggers a manual review.
- **Census of Canadian forestry industry production statistics.** Annual log
  consumption and lumber/pulp exports from NRCan / Statistics Canada are benchmarked
  against scenario output at the FFPP (Forest Products Price) coverage level.
- **Provincial ministry reports (FESBC, MRN, CFB, ACQ, BCFPA, etc.).** Per
  jurisdiction, scenario outputs are spot-checked against ministry-published
  ARFP / annual reports; mismatches surface as a `coverage_correction` item in
  the prototype-gap register.

Validation does NOT replace the gating system — a successful cross-check does not
flip `species_composition` or `national_scaling` from `in_progress` to `resolved`
on its own.

## 8. Cross-reference

- **BC + QC LP-plus precedent (the operational baseline this expands):**
  `planning/bc_qc_pipeline_strategy.md` §3 ("national-scale readiness valuation").
- **CASFRI schema review (607-line document governing the schema authority):**
  `metadata/source-reviews/casfri_v5_schema.md` §5 mapping table; §2 four-artifact
  disambiguation; §6 licensing posture.
- **Scenario framework authority radius:** the framework here is intentionally
  bounded to running LP overlays; climate, disturbance, and epidemiological
  projections are referenced but not built until the named gates flip.
- **Tier-3 jurisdictional evidence (sole driver of new supply rows beyond BC + QC):**
  `planning/national_expansion_plan.md` (`planning/national_expansion_plan.md`).
- **Phase 4 integration plan (source of adapter modules):**
  `planning/casfri-v5-schema-integration-plan.md` §4 schema-mapping sketch.
- **Phase-5 framer (companion to this doc):**
  `planning/phase_5_closeout.md` §6 (forward-next-step candidate (d)).
- **Readiness gate authority:**
  `metadata/readiness-gates.yaml` (8 gates; section §5 above cross-references the
  relevant five).

---

*End of National Bioenergy Scenario Simulation Design. Five canonical scenarios
defined; LP overlay-vector design; one coherent schema; five gates required for
Tier-1 execution; Tier-2 / Tier-3 layered on top.*
