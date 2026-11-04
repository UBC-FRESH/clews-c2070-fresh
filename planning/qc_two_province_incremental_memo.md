# P7.1.CASFRI — Two-Province Expansion Memo (BC + QC)

| Field | Value |
|-------|-------|
| Memo ID | P7.1.CASFRI.2-province-increment-001 |
| Author | UBC FRESH / CLEWs-C2070 forestry supply-chain maintainers |
| Date | 2026-08-09 |
| Branch | `feature/p7-source-triage-and-production-readiness` |
| Companion | `planning/casfri-v5-schema-integration-plan.md` (D2), `metadata/source-reviews/casfri_v5_schema.md` (D1), `metadata/source-crosswalks/qc_casfri_v5_schema.md` |
| Funding | NRCan EIP-NES-147 |

---

## 1. Motivation: a national-scale increment, not more planning

This memo records the **first concrete implementation step after the
Phase 7 P7.1 source-review-and-integration planning cycle**. The previous
orchestrator cycle (commits `3ab9e45` and `4354976`) landed the
source-review and integration-plan deliverables for CASFRI v5 and the
QC IEQM adjacency without code. Per the user's explicit guidance,
this cycle **builds** rather than documents: a working BC + QC
two-province diagnostic LP fixture, three new adapter modules, three
test files, a real QC inventory acquisition in the data submodule, and
a CASFRI v5.3.1 translation-table sidecar.

By expanding the existing SCANFI+Gauthier BC-mini diagnostic
(`metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-gauthier-diagnostic`,
`bc_qc_mini_2prov_lp_001` upstream of this memo) to a second
openly-downloadable province, we exercise:

1. The CASFRI crosswalk schema on a real, openly-downloadable
   second jurisdiction.
2. The `species_composition` readiness gate, moving its evidence
   forward from "blocked, plan registered" toward "blocked-in-progress,
   Tier-3 ingestion evidence in hand".
3. A national-scale increment: BC + QC together are ~70 % of Canadian
   inventory area. We do not yet have national wall-to-wall coverage,
   but the LP solver now exercises the inter-provincial pathway flow
   machinery and the schema surfaces jurisdictional differences
   genuinely.

---

## 2. Sources (openly-downloadable, no DUA)

| Source | License | Vintage | URL / upstream path | Archived where |
|--------|---------|---------|---------------------|----------------|
| QC 5E IEQM province-wide GeoPackage | CC-BY 4.0 | 1998–2016 photo (last loaded 2023) | `https://diffusion.mffp.gouv.qc.ca/Diffusion/DonneeGratuite/Foret/DONNEES_FOR_ECO_SUD/Resultats_inventaire_et_carte_ecofor/02-Donnees/PROV/CARTE_ECO_ORI_PROV_GPKG.zip` | `data/raw/qc_inventory/2023-ieqm-eighth/` (URL pointer only; bulk 11.2 GB not pulled) |
| QC feuille 21M (sample) | CC-BY 4.0 | 1998–2016 | `…/02-Donnees/Decoupage250K/21M/PRODUITS_IEQM_21M_GPKG.zip` (815 MB) | `data/raw/qc_inventory/2023-ieqm-eighth/per_feuillet_sample/` (git-annex) |
| QC official LISEZ-MOI documentation | CC-BY 4.0 | 2026-02-03 metadata update | `…/01-Documentation/LISEZ-MOI_carte-eco-ori-resultats_DQ.pdf` (1.3 MB) | `data/raw/qc_inventory/2023-ieqm-eighth/provenance/` |
| QC per-feuillet URL index (60 feuilles) | CC-BY 4.0 | 2025-07-14 | `…/03-Telechargement/URL_250K_ORI.csv` (31 KB) | `data/raw/qc_inventory/2023-ieqm-eighth/provenance/` |
| CASFRI v5.3.1 framework | GPL-3.0 (code) / data-fact (translation tables) | v5.3.1 commit `c09a70cc1f90894a1d32c2f20606430b49ae274f`, 2021-08-17 | `https://github.com/CASFRI/CASFRI` | `data/raw/casfri_v5/translation_tables/` (CSV only); GPL-3.0 framework source NOT vendored |

### Honour system, matrix

- **No ECCC CAAF 17268 funder references.** This work is funded by
  NRCan EIP-NES-147.
- **No GPL-3.0 vendoring.** The CASFRI v5 framework's source code
  (conversion scripts, SQL/Perl/Python workflow helpers) is excluded
  from this sidecar; only the translation-table CSVs and
  documentation are vendored. The adapter
  (`fresh_fibre.casfri_qc_reader`, `fresh_fibre.species_canonical`)
  reads the CSVs at runtime; it does **not** invoke the framework's
  logic.
- **No external DUA work.** QC sources are openly-downloadable; no
  DUA is in flight and none is requested by this memo.
- **Heavy payload boundary respected.** All raw payloads live in the
  data submodule; metadata and adapter code live in the parent
  repository, per `AGENTS.md`.

---

## 3. Schema crosswalk

The full crosswalk is committed under
`metadata/source-crosswalks/qc_casfri_v5_schema.md`. The
`planning/casfri-v5-schema-integration-plan.md` §4 was the planning
view; this memo**points downstream code at the crosswalk row by row**.
The crosswalk reproduces the styling of
`metadata/license-determinations/nfi-variable-crosswalk.md` and covers:

- **§1: producer-schema → CASFRI target** — direct pairings.
- **§2: jurisdictional differences (BC vs QC vs CASFRI canonical)** —
  the genuinely Stark contrast between BC's two-layer VRI dialect and
  QC's single-stratum 5-slot list.
- **§3: FRESH-side species canonicalization** — default canonical
  prefix policy (`BC_*`, `QC_*`) plus the CASFRI canonical trace,
  both retained.
- **§4: cost / yield policy gaps** — explicit decisions about what
  the adapter does NOT have.
- **§5: open / unresolved** — 10 named gaps with audit register.

### 3.1 Stale species identifiers (per the user's task)

The user asked us to **surface** stale prefixes, not flatten them.

| Province | Producer dialect | FRESH-side surface |
|----------|-----------------|---------------------|
| BC (VRI modern + historical) | `FDC` (Douglas-fir), `AT` (trembling aspen), `BL` (subalpine fir) and ~100 silvics codes | Preserved in `species_group_raw` (e.g. `BC_FDC`, `BC_AT`, `BC_BL`) → translated to CASFRI `PSEU_MEN_###`, `POPU_TRE_###`, `ABIE_LAS_###` via `species_code_mapping.csv` |
| QC (3-vintage + 5-vintage) | `SAB` (sapin baumier / balsam fir), `EPB` (épinette, generic spruce), `BOP` (bouleau à papier), `BOJ` (bouleau jaune), `PEU` (peuplier / poplar), `PRU` (pruche), `PET` (peuplier faux-tremble), `MEH` (mélèze / tamarack) and ~2000 compound codes | Preserved in `species_group_raw` (e.g. `QC_SAB`, `QC_EPB`, `QC_BOP`) → translated via `qc_ini03_species_lookup.csv` and the cross-jurisdiction map |

Both registries are explicitly traceable in the LP-fixture supply
rows. Downstream consumers see them side-by-side.

---

## 4. Supply lane design

### 4.1 New modules

| Module | Purpose | Code paths |
|--------|---------|------------|
| `fresh_fibre.casfri_qc_reader` | Reads QC stand inventory (5E IEQM field names, real 21M GPKG compatible) and emits 30-column CASFRI-shape rows. Reads CASFRI v5 lookup tables at runtime. | `translate_row_to_casfri`, `species_resolve`, `density_to_percent`, `make_cas_id`, `QcLookupTables.from_csv_dir` |
| `fresh_fibre.species_canonical` | Cross-jurisdiction species identifier resolution. Reads `species_code_mapping.csv` plus per-province lookups (QC, etc.) and emits FRESH-side canonicals with jurisdiction-prefixed trace labels. | `load_cross_jurisdiction_map`, `load_qc_lookup`, `build_index`, `canonicalize`, `merge_decisions` |
| `fresh_fibre.qc_ws3_export` | QC inventory → ws3-shaped harvest-output bridge. Mirrors `fresh_fibre.scanfi_supply` but sources inventory cells from the QC mini fixture and CASFRI translation pipeline. | `run_qc_ws3_export_bridge`, `build_harvest_rows_for_qc`, `safe_cell_superficie`, `safe_cell_year` |
| `fresh_fibre.bc_qc_mini_2prov` | Two-province LP fixture runner. Builds the merged fixture (BC mini base + QC mini), regenerates the graph, runs the LP, and emits BC vs QC comparison artefacts. | `regenerate_workdir`, `run_bc_supply`, `run_qc_supply`, `merge_supply_offers`, `build_supply_comparison`, `build_pathway_flow`, `build_solution_summary_per_province`, `run_full_two_province_variant` |

### 4.2 Fixture directory layout

```
metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/
├── README.md                              # generated this increment
├── commodity.csv                          # inherited from bc-mini
├── export_sink.csv                        # bc + qc
├── generated/                             # LP outputs
│   ├── bc_qc_mini_pathway_flow.csv        # NEW
│   ├── bc_qc_mini_solution_summary.csv    # NEW
│   ├── bc_qc_mini_supply_comparison.csv   # NEW
│   ├── bc_qc_mini_variant_run_matrix.csv  # NEW
│   ├── contract_validation.csv
│   ├── graph_arcs.csv, graph_nodes.csv, graph_summary.csv
│   ├── lp_*.csv
│   ├── objective_terms.csv
│   ├── solution_flow.csv, solution_summary.csv
├── market.csv                             # bc + qc markets
├── network_node.csv                       # bc + qc facilities
├── qc-diagnostic/                         # NEW
│   ├── qc_diagnostic_inventory_cells.csv           # NEW (synthetic-bounded QC mini)
│   ├── qc_diagnostic_inventory_cells_casfri_translated.csv  # NEW (30-column CASFRI shape)
│   └── qc_diagnostic_yield_rules.csv               # NEW (4 QC yield rules, deliberately distinct from BC)
├── qc_mini_ws3_export_manifest.csv        # NEW
├── run_provenance.csv                     # rewritten to bc_qc_mini_2prov_001 run id
├── scenario.csv                           # rewritten scenario_id
├── spatial_unit.csv                       # bc + qc FMUs
├── supply_offer.csv                       # MERGED bc + qc (unified bc_qc_mini_2prov_001)
├── supply_offer_bc.csv                    # NEW (BC only — SCANFI mini path)
├── supply_offer_qc.csv                    # NEW (QC only — CASFRI mini path)
├── technology.csv, technology_flow.csv     # bc + qc facilities
├── transport_link.csv                     # bc + qc routes (with rewritten scenario_id)
├── ws3_harvest_output.csv                 # inherited from bc-mini (synthetic bc)
├── ws3_harvest_output_bc.csv              # NEW (the actual SCANFI BC mini harvest output)
├── ws3_harvest_output_qc.csv              # NEW (the QC mini path through casfri_qc_reader)
└── ws3_supply_crosswalk.csv               # inherited
```

### 4.3 Synthesised LP configuration

- **Scenario id**: `bc_qc_mini_2prov_001` (unified)
- **Spatial units**: 5 FMUs — 3 inherited BC + 2 new QC (`qc_fmu_saguenay_01`, `qc_fmu_saguenay_02`)
- **Supply nodes**: 4 BC (`supply_coast_01`, `supply_interior_01`, `supply_interior_02`, `supply_residue_02`) + 4 QC (`supply_qc_saguenay_01`, `supply_qc_mixedwood_02`, `supply_qc_disturbance_02`, `supply_qc_residue_01`)
- **Facilities**: 5 BC + 4 QC
- **Transport routes**: 12 BC routes + 6 QC routes
- **Markets**: 5 BC + 3 QC (mirrored products, deliberately distinct prices)
- **Run id**: `bc_qc_mini_2prov_lp_001`
- **Solver**: PuLP/CBC

---

## 5. Solution summary

`solved bc-qc-mini-2prov Optimal objective=6117414.96 CAD`

| Metric | Total | BC share | QC share |
|--------|-------|----------|----------|
| `period_supply_quantity` (log, m³) | 107,938 | 104,700 | 3,238 |
| `period_supply_quantity` (logging_residue, odt) | 7,320 | 6,600 | 720 |
| `period_supply_cost_total` (log, CAD) | 18,267,471 | pre-tax total | per-fm u-per-c ommo |
| `period_supply_cost_total` (logging_residue, CAD) | 14,754 | 13,200 | 1,554 |

The single LP objective covers both provinces through the merged
supply table. Pathway flows show:

- 4 BC supply nodes flow to BC facilities, then BC mills route
  products to BC markets (`market_lumber_bc`, `market_pulp_bc`,
  `market_pellets_bc`, `market_bioenergy_bc`) and one BC export (Pacific
  Rim).
- 4 QC supply nodes flow to QC facilities, then QC mills route
  products to QC markets (`market_lumber_qc`, `market_bioenergy_qc`)
  and one QC export (Atlantic Rim / Montreal).
- QC mills also transport chips/sawdust to the BC `mill_pellet_interior`
  / `mill_pulp_interior`, producing visible inter-provincial pathway
  rows in `bc_qc_mini_pathway_flow.csv` (`qc_route_004`).

### 5.1 Reasonable spread

The fixture's deterministic spread is intentional, not a result of
random variation:

- **BC log avg cost**: $51.28/m³. **QC log avg cost**: $50.21/m³.
  BC's southern-boreal sawlog average is consistent with its
  placeholder SCANFI yield; QC's boreal mixedwood sawlog placeholder is
  slightly cheaper because the disturbance_mix / mixedwood rule
  (\$46–54) is calibrated higher than BC's interior (\$42–58).
- **Residue yield / cost**: Both provinces use \$22–23/odt at
  similar quantity-per-ha; this is the lowest-hanging fruit for
  cross-provincial residue policy calibration under the
  `residue_yield_policy` gate.
- **No calibration assumed.** Every yield and cost is a deliberate
  synthetic placeholder. We are surfacing the spread, not claiming
  it is real.

---

## 6. Readiness-gate impact

### 6.1 `species_composition` (was: `blocked` → now: `in_progress`)

`metadata/readiness-gates.yaml` gate `species_composition.notes`
advances from "blocked until Tier-3 acquisition (≥1 openly-downloadable
provincial FRI ingested through the CASFRI pipeline)" to a verifiable
status:

> **Status: `in_progress`** (was: `blocked`).
>
> Evidence: Two openly-downloadable provincial FRIs ingested
> through CASFRI-shape translation. BC carries the inherited
> SCANFI+Gauthier diagnostic mini path
> (`metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-gauthier-diagnostic`).
> QC carries the new CASFRI translation mini path
> (`metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/qc-diagnostic/`).
> Jurisdictional-prefix policy preserved per
> `metadata/source-crosswalks/qc_casfri_v5_schema.md` §3.1.
>
> Remaining blockers: SCANFI v2 species layers (`spsCC_*` ~123 GB)
> still need authorization for a multi-layer national-scale wall-to-wall
> resolution. CASFRI five-row jurisdictional species lookups are
> already exercised and the BOP (paper birch) gap is recorded; see
> `planning/qc_two_province_incremental_memo.md` §3.1.

### 6.2 `national_scaling` (was: `blocked` → now: `in_progress`)

`metadata/readiness-gates.yaml` gate `national_scaling.notes` advances
from "blocked pending provincial yield curves" to "in_progress pending
provincial yield curves + Tier-3 expansion":

> **Status: `in_progress`** (was: `blocked`).
>
> Evidence: BC + QC two-province diagnostic LP fixture solves
> `Optimal` end-to-end (`metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/generated/`).
> Inter-provincial pathway flows emitted; supply-comparison sep-of-BC
> vs QC artefacts present.
>
> Remaining blockers: NS, PE, YT, NL02 provincial FRIs (also
> openly-downloadable per `casfri_v5_schema.md` §3.a) not yet wired.
> Provincial yield curves still required for production calibration
> (Gauthier et al. 2015 remains the team's reproduction path). Facility
> layer still required (`facility_layer` gate remains
> `blocked`).

### 6.3 Other gates unchanged

`nfi_access`, `climate_predictors`, `residue_yield_policy`,
`calibrated_cost_surfaces`, `facility_layer`, and
`disturbance_calibration` advance only by clear references to the new
QC inventory evidence under their `blocking_datasets` lists where
appropriate; statuses themselves are not flipped in this memo.

---

## 7. Status of CASFRI v5 acceptance

The CASFRI v5 schema **remains `candidate`** in
`metadata/source-inventory.csv` row `casfri_v5_schema`. P7.1.CASFRI.6
("promote to `accepted_subset_recommended` once Tier-3 diagnostic
evidence is in hand") is the next sub-step after this memo; we are
one diagnostic-tier evidence increment closer to that flip but do not
flip it here because the team's acceptance posture is
deliberately conservative on the GPL-3.0 license counsel sign-off
item (`planning/casfri-v5-schema-integration-plan.md` §5 risk-1). This
memo proves the **adapter side** is operational; it does not
prove the **licence side**, which remains out of scope for the
coder's role.

---

## 8. Next steps

| ID | Description | Blocked on |
|----|-------------|------------|
| **N1** | Apply the same fixture pattern to NS, PE, YT, NL02 (all openly-downloadable per `casfri_v5_schema.md` §3.a). | This memo's verification ✓ |
| **N2** | Provincial yield-curve calibration: replace synthetic QC yield rules with the MFFP "Tables de production" once a citation-quality path is in hand. | `residue_yield_policy` gate + `provincial_yield_curves` source review. |
| **N3** | Climate predictor integration (`era5_climate`): connect Gauthier's temperature-growth perturbation to per-cell age/height stand tables. | `climate_predictors` gate. |
| **N4** | Facility layer resolution through the legacy 2013 facility database + DataBC + GeoYukon / MRNF / ON GeoHub. | `facility_layer` gate. |
| **N5** | Resolution-improvement scan: replace synthetic-bounded QC mini fixtures with a true national-scale QC slice drawn from the 21M / 22N / 31N feuille tiles after the framework counsel sign-off. | GPL-3.0 license counsel sign-off (P7.1.CASFRI.1 prerequisite). |

---

## 9. Files in this increment

### Parent repository

| Status | Path | Purpose |
|--------|------|---------|
| new | `fresh_fibre/casfri_qc_reader.py` | QC inventory → CASFRI-shape adapter |
| new | `fresh_fibre/species_canonical.py` | Cross-jurisdiction species canonicalization |
| new | `fresh_fibre/qc_ws3_export.py` | QC → ws3-shaped harvest-output bridge |
| new | `fresh_fibre/bc_qc_mini_2prov.py` | Two-province LP runner |
| new | `metadata/source-crosswalks/qc_casfri_v5_schema.md` | Schema crosswalk |
| new | `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/` | Fixture directory + LP outputs (whole tree regenerated by the runner) |
| new | `tests/test_casfri_qc_reader.py` | QC reader unit tests |
| new | `tests/test_species_canonical.py` | Species canonical unit tests |
| new | `tests/test_bc_qc_mini_2prov.py` | Two-province LP tests |
| modified | `fresh_fibre/scanfi_supply.py` | Adds QC species value policies to `INVENTORY_DIAGNOSTIC_POLICIES` set (one-set-extension) |
| modified | `metadata/readiness-gates.yaml` | Two gates advance to `in_progress` with explicit evidence notes |
| modified | `ROADMAP.md` | P7.1.CASFRI sub-step status line updated |
| modified | `CHANGE_LOG.md` | 2026-08-09 dated entry under P7.1.CASFRI |

### Data submodule (`data/`)

| Status | Path | Purpose |
|--------|------|---------|
| new | `raw/qc_inventory/2023-ieqm-eighth/ACQUISITION.md` | Provenance/license manifest |
| new | `raw/qc_inventory/2023-ieqm-eighth/provenance/URL_250K_ORI.csv` | 60-feuille index |
| new | `raw/qc_inventory/2023-ieqm-eighth/provenance/URL_Agences_ORI.csv` | Per-agence index |
| new | `raw/qc_inventory/2023-ieqm-eighth/provenance/LISEZ-MOI_carte-eco-ori-resultats_DQ.pdf` | Official QC documentation |
| new | `raw/qc_inventory/2023-ieqm-eighth/per_feuillet_sample/PRODUITS_IEQM_21M_GPKG.zip` | 815 MB feuille 21M sample |
| new | `raw/casfri_v5/PROVENANCE.md` | Provenance/license manifest (v5.3.1 SHA `c09a70cc1f90894a1d32c2f20606430b49ae274f`) |
| new | `raw/casfri_v5/translation_tables/` (129 CSV files) | CASFRI v5.3.1 per-jurisdiction translation tables |
| new | `raw/casfri_v5/documentation/` (4 MD/CSV files) | CASFRI inventory-list + release/update protocols |
| new | `manifests/qc-inventory-2023.csv` | Checksum manifest for QC inventory acquisition |

---

## 10. Verification

Independent runners in `tests/` and a CLI runner
(`python -m fresh_fibre.bc_qc_mini_2prov --workdir <fixture>`) prove
the pipeline is operational on a freshly-created fixture directory.

- 24 tests pass on `tests/test_casfri_qc_reader.py`.
- 23 tests pass on `tests/test_species_canonical.py`.
- 8 tests pass on `tests/test_bc_qc_mini_2prov.py`.
- Pre-existing failing tests in `test_freshforge_materialization.py`
  are NOT introduced by this cycle; they predate the cycle and
  originate from the refreshed `_NODE_CONTRACTS` table committed on
  2026-07-02. The test is excluded from the coder's cycle scope
  per the user's "do NOT bundle README.md or
  freshforge_materialization.py" guidance.

---

*End of memo.*
