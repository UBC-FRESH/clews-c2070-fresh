# QC ↔ CASFRI v5 Schema Crosswalk

| Field | Value |
|-------|-------|
| Crosswalk version | v1 (2026-08-09) |
| Companion documents | `planning/casfri-v5-schema-integration-plan.md` (D2), `metadata/source-reviews/casfri_v5_schema.md` (D1), `metadata/source-reviews/provincial_forest_inventories.md` (Quebec row) |
| Upstream authority | CASFRI v5.3.1 commit `c09a70cc1f90894a1d32c2f20606430b49ae274f` (`github.com/CASFRI/CASFRI`); translation tables cached at `data/raw/casfri_v5/translation_tables/` |
| Producer schema | Québec *Carte écoforestière originale et résultats d'inventaire courants* — Données Québec `a232475f-ec3d-476a-8725-d0c12367b3f0`; CC-BY 4.0; 5e inventaire écoforestier (5E IEQM); 1998–2016 photo vintage in the 2023 release; CRS EPSG:32198 (Québec Lambert) |
| Producer reference sheet | `data/raw/casfri_v5/translation_tables/qc_ini03_*.csv` (3e inventaire), `qc_ini04_*.csv` (4e inventaire), `qc_ipf05_*.csv` (5e inventaire 200k, project `ipf05`), plus `qc_ini03_species_lookup.csv` and `qc_disturbance_lookup.csv` |
| Audience | `fresh_fibre.casfri_qc_reader`, `fresh_fibre.species_canonical`, the `fresh_fibre.bc_qc_mini_2prov` LP variant |

This crosswalk reproduces the styling of
`metadata/license-determinations/nfi-variable-crosswalk.md` (one
machine-readable Markdown table per crosswalk concern, plus gap and
unresolved notes). It complements D2 §4 by going deeper into the QC
inventory geometry of CASFRI v5 (`casfri.cas` / `cst` / `geo` /
`lyr` / `eco` / `dst` / `nfl`) for one open-jurisdiction province.

---

## 1. Provenance pairings (CASFRI v5 ↔ QC inventory input table)

QC GeoPackage (`PRODUITS_IEQM_*.gpkg`) tables in scope for our diagnostic
fixture, paired with their CASFRI v5 target table. Layer naming follows
the official 5e inventaire file dictionary
(`data/raw/qc_inventory/2023-ieqm-eighth/provenance/LISEZ-MOI_carte-eco-ori-resultats_DQ.pdf`)
and CASFRI's `translation/tables/qc_ipf05_*.csv` schema-mapping rules.

| Producer schema field (QC 5E IEQM) | Source CRS / table | CASFRI v5 target column (table.column) | Producer provenance (CASFRI lookup table id) | Transformation note | Open issue / gap |
|-----------------------------------|--------------------|-----------------------------------------|---------------------------------------------|---------------------|------------------|
| `PEUPLEMENT.GEOCODE` (string) | `PEUPLEMENT` (polygon) | `casfri.geo.cas_id`<br>(composed key, see translation rule 1 of `qc_ipf05_cas.csv`) | `qc_ipf05_cas.csv` rule_id=1 (padConcat of 5 sub-fields into `cas_id`) | Composite, jurisdictions-stable. Per-FRI variance inherited from `cas` rule row. | None — composite is registered. |
| `PEUPLEMENT.GEOCODE` → 10-char head + 10-char tail | `PEUPLEMENT` | `casfri.cas.inventory_id` | `qc_ipf05_cas.csv` rule_id=2 (`copyText(inventory_id)`) | Inventory-version tagging. | None — direct. |
| `PEUPLEMENT.NO_PEUPLEMENT` | `PEUPLEMENT` | `casfri.cas.orig_stand_id` | `qc_ipf05_cas.csv` rule_id=3 (`copyText(orig_stand_id)`) | Native QC stand identifier. | None — direct. |
| `PEUPLEMENT.CLE_AGE` (8-class stand structure code) | `PEUPLEMENT` | `casfri.cas.stand_structure` | `qc_ipf05_cas.csv` rule_id=4 (`lookupText(..., qc_standstructure_lookup, …)`) | Lookup via `qc_standstructure_lookup.csv` (`SINGLE_LAYERED`, `MULTI_LAYERED`, `NOT_APPLICABLE`, single/multi). | `cle_age` ↔ `stand_structure` is registered; `cle_age` is the QC fourth-inventory stand structure classification – it's been replaced by `cl_age` in the 5E IEQM. Cross-version drift warrants explicit re-validation per `cle_age`. |
| `PEUPLEMENT.CODE_DEN` (forest-density class 5-bucket code) |  `PEUPLEMENT` | n/a (CASFRI v5 keeps `casfri.lyr.crown_closure_*` percent values, not density classes) | n/a | First-class density class is **not representable** in CASFRI v5 — it must be converted to crown closure percent by an external lookup. | Crown-closure divergence: QC's `CODE_DEN` uses A (×80 %), B (60–80 %), C (40–60 %), D (25–40 %), E (×25 %) buckets; CASFRI expects 0–100 % integer or two-band conif/decid split. Post-CA adapter records `code_den` value policy as `qc_code_den_arb_classification_unresolved` until the binned-percent mapping is signed off. |
| `PEUPLEMENT.AGE` (years) | `PEUPLEMENT` | `casfri.cas.age` | `qc_ipf05_cas.csv` rule_id=`cas.age` (`copyText(age)`) | Direct. | QC ages 1–120 typical; CASFRI convention is 0–500+; QC's 250+ classes map to 251 SENDVT and need explicit confirmation. |
| `PEUPLEMENT.HAUTEUR` (m) | `PEUPLEMENT` | `casfri.cas.height` | `qc_ipf05_cas.csv` rule_id=`cas.height` (`copyText(height)`) | Direct. | QC heights are recorded per stratum top height; fidelity is 1-m bins. |
| `PEUPLEMENT.ESSENCE_1_PCT` … `ESSENCE_5_PCT` (5-slot % per stratum) | `PEUPLEMENT` | `casfri.lyr.species_1_layer2`, `species_per_1_layer2`, … | `qc_ipf05_lyr.csv` (6-slot species translation) | Three-slot or five-slot QC list → two-layer canonical (overstorey + understorey). First 5 entries flow from the upper layer; `ESSENCE_5_PCT` reserved for sub-canopy. | Native QC has up to 5 species compositions per stand; CASFRI has 2 layers × 6 species. Information loss expected (~one species per polygon on average). |
| `PEUPLEMENT.ESSENCE_*` codes themselves | `PEUPLEMENT` | `casfri.lyr.species_*_layer*` (CASFRI normalized to `ABIE_BAL_###` etc.) | `qc_ini03_species_lookup.csv` (and `qc_ipf05_*` equivalents); cross-checked by `species_code_mapping.csv` | Two-step: QC species code (`SAB`, `EPB`, `BOP`, `PEU`, `PRU`, `PET`, `BOJ`, `ERI`, `MEH`, …) → CASFRI species id (`PICE_MAR_###`, `PICE_GLA_###`, `BETU_PAP_###`, …) → FRESH canonical (with jurisdictional prefix). | **Mutation preservation:** we keep the original QC prefix in `species_group_raw` so consumers can trace to the producer schema; see §3. |
| `PEUPLEMENT.CL_DRAINAGE` (7-class drainage code) | `PEUPLEMENT` (deprecated in 5E IEQM, retained in 4E and 3E) | n/a (CASFRI v5 does not encode drainage at the stand level by default) | `qc_ini03_eco.csv` rule 1 (`copyText(drainage_class)`) | Drainage *is* in CASFRI via `casfri.eco.drainage_class` for BC VRI but **not** for QC IEQM in v5.3.1 — gap. | Drainage class becomes a FRESH-only meta-column. Highlight as `casfri_eco_no_drainage_class_for_qc_v5_3_1` until QC 5E eco table is delivered upstream. |
| `PEUPLEMENT.PHOTOS_DATE` (year) | `PEUPLEMENT` | `casfri.cas.photo_year` | n/a (in CASFRI inventory metadata, not per polygon) | Inventory-level (not polygon-level). | Stand-disturbance cells fall under `casfri.dst.year_dist`; not inferred here. |
| `PEUPLEMENT.ESSENCE_RI` (residual-main indicator, binary) | `PEUPLEMENT` | n/a | n/a | No CASFRI consumer; record-only with explicit `qc_essence_ri_dropped_note` for downstream consumers. | Producer-specific; dropped. |
| `STATION_FOR.STATION_ID` | `STATION_FOR` (point lookup) | `casfri.cas.site_class` (or 0–7 site index category, per `qc_ini03_eco.csv` rule 2) | `qc_ini03_eco.csv` | `STATION_FOR` is a related point table; stand-linking is by `PEUPLEMENT.NO_STATION` and requires a join. | Join latency to follow; CASFRI expects polystation, not stationpoint. |
| `COMPILATION_FOR.GEOCODE` + `NO_PEUPLEMENT` | `COMPILATION_FOR` (volume / composition) | Cross-check for `casfri.cas.crown_closure_*` + `casfri.lyr.species_*_layer*` (% disaggregation) | `qc_ini03_dst.csv` + `qc_ini03_lyr.csv` (per-stratum) | `COMPILATION_FOR` recursively aggregates stand-level records and per-stratum forest tables (`STRATE_SUPP_POP3` etc.). | The 14 rules in `qc_ini03_lyr.csv` are layer-by-layer translations for `species_*_`, `species_per_*_`, `crown_closure_*_`, `height_upper_layer_1`. Confirmed by translation table stat: 38 rows in `qc_ini03_lyr.csv`. |
| `COMPILATION_FOR.VOLUME_BLC_1` … `VOLUME_BLC_4` (m³/ha by broadleaved-coniferous class) | `COMPILATION_FOR` | n/a (volume flows downstream via yield policy, not into CASFRI) | n/a | Volume is not a CASFRI column. | Volumes flow into a FRESH-side yield-policy layer. Document as `yield_layer_volume_origin_qc_compilation_for` (not a CASFRI artefact). |
| `PEUPLEMENT.NB_ESSENCES_REG` (count of registered species) | `PEUPLEMENT` | `casfri.lyr.layer_count` (NOT_REPRESENTED — implicit by null count) | implicit | The 6-slot reservation in CASFRI is permissive: empty slots mean single-layered; layered slots mean multi. | Counts derived downstream in adapter. |

---

## 2. Standout jurisdictional differences (BC vs. QC vs. CASFRI v5 canonical)

The CASFRI schema `casfri.cas / cst / geo / lyr / dst / eco / nfl` is the
jurisdiction-agnostic harmonisation; **each provincial source has its own
dialect**, and BC and QC dialects are starkly different. This table
documents the cross-cut, not the per-column route. Each row should be
considered a "load-bearing divergence" — the schema crosswalk correctly
creates a CASFRI row, but the *semantic* of the row may differ across
provinces.

| Concern | BC VRI (`bc_vri01_*`) | QC 5E IEQM (`qc_ipf05_*`) | CASFRI v5 canonical | Adjudication policy in adapter |
|---------|-----------------------|---------------------------|---------------------|---------------------------------|
| Stand id | `orig_stand_id` from `MAP_SHEET_ID` × 10 + `STAND_ID` × 7 | `orig_stand_id` from `CLE_PEUPLEMENT` lookups joined with `GEOCODE` | `casfri.cas.orig_stand_id` (string, jurisdiction-stable) | Two-step translate; both dialects keyed to `cas_id` (composed). |
| Species coding | Two-layer canonical native (`FDC`, `AT`, `BL`, …) | Single-class-pivot to 5-slot list (with raw QC `SAB` / `EPB` / `BOP` / `PEU` / `PRU` / `PET` / `BOJ` / `ERI` / `MEH` / `RES_SAB` etc.) | `species_code_mapping.csv` (49-row cross-jurisdiction → CASFRI species ids) | Two-step route: producer-code → CASFRI canonical via per-jurisdiction lookup; CASFRI canonical → FRESH canonical via `metadata/source-crosswalks/species_canonical_policy.md` (planned as §3 below; matched on family/genus, with jurisdictional prefix retained for traceability). |
| Height | Lorey's height per S-class (`S1_AVGHT`, `S2_AVGHT`, `S3_AVGHT`) | Single per-stand `HAUTEUR` (m) | `casfri.cas.height` (per-layer; 2-layer convention) | BC's upper-layer height + lower-layer height map directly; QC's single height defaults to `layer1`. Adapter emits `qc_height_layer2_defaulting`. |
| Crown closure | `S1_CLS_*` + `S2_CLS_*` (per layer) | `CODE_DEN` bucket code (A–E) | `casfri.lyr.crown_closure_layer*` (0–100 %) | QC bucket-to-percent is **lossy**; cert audit record `qc_code_den_arb_class_bucketed` until a signed-off mapping is reviewed. |
| Age | `PROJ_AGE_1` integer | `AGE` integer (-8888 for unknown) | `casfri.cas.age` (integer or `-8888` sentinels) | Direct. QC's `-8888` sentinel is **harmonized** by CASFRI (`-8888` → `NOT_APPLICABLE`). |
| Site index / station | PSP-measured `SITE_INDEX` (m@50) | Line-Point reference table `STATION_FOR` (point lookup) | `casfri.cas.site_index` (integer) | QC's `STATION_FOR`-link requires explicit station-join logic; v5.3.1 does *not* fold STATION_FOR into CASFRI — explicit gap. |
| Disturbance | `casfri.dst` per BC VRI (history per photo-year) | `casfri.dst` carries 5E IEQM disturbance via `STRATE_SUPP_POP2`-style attributes | `casfri.dst.fires_year` (year) | `qc_disturbance_lookup.csv` provides the per-class canonicalisation. |
| Non-forest land (NFL) | `ncfl_<thing>` polygons (separate layer in BC VRI) | `PEUPLEMENT` includes `DESCRIPT` text indicating NFL (`PEUPL_HUM`, `EAU`, …) | `casfri.nfl` (separate layer with `nfl_class` text) | QC's mixed layer requires geometry-partition before CASFRI assignment. |
| Photo year | `INV_YEAR` (year, photo year, stand-level) | `PHOTOS_DATE` (per-photo attribution; stand-level tagged to inventory) | `casfri.cas.photo_year` (string) | Direct. |

---

## 3. Cross-jurisdiction species canonicalisation (FRESH-side)

The CASFRI v5 `species_code_mapping.csv` provides a single
`casfri_species_codes` per `(scientific_name, producer code)`. The FRESH
adapter goes one step further: it produces a **FRESH canonical species**
that downstream yield policy can use without re-reading the producer
dialect. Default policy is per §3.1. The species prefixes (BC, QC, etc.)
are kept as **assertion provenance** but not as part of the canonical
identifier — they live in `species_group_raw`.

### 3.1 Default canonical prefix policy

| Province / framework | Producer raw prefix | CASFRI canonical | FRESH canonical (default) | Provenance prefix in `species_group_raw` |
|----------------------|---------------------|------------------|----------------------------|-----------------------------------------|
| BC VRI | `FDC`, `AT`, `BL` (silvics codes) | `ABIE_LAS_###` etc. | `FDC` (preserved), `AT` (preserved), `BL` (preserved) **before** CASFRI normalisation; provincial trace label = `BC_VRI_01` | `BC_` |
| QC 5E IEQM (third cycle) | `SAB`, `EPB`, `BOP` (informal French pine-3-letter) | e.g. `PICE_MAR_###` (black spruce = `SAB` / `EPB` / `PIM`) | QC keeps raw form for traceability, e.g. `SAB` → `QC_SAB` | `QC_` |
| QC 5E IEQM (5e inventaire) | 5-slot list with raw codes (`PEU`, `PRU`, `PET`, `BOJ`, `RES_SAB`, `MEH`, `ERI`) | `POPU_TRE_###` (trembling aspen) etc. | QC raw + trace = `QC_PEU` etc. | `QC_` |
| CASFRI native | `casfri_species_codes` from `species_code_mapping.csv` | n/a | yield_policy-species-group (`PICE_MAR`, `POPU_TRE`, etc.) | unmodified |

### 3.2 Conflict policy (two producers, same genus)

When two provinces both name a species using the same scientific name,
`fresh_fibre.species_canonical` collapses them on the CASFRI canonical
record (e.g. black spruce = `PICE_MAR_###`). The distinct
provincial-trace prefixes are preserved **separately** in
`species_group_raw` so each producer's data remains traceable. The
canonical-cell signature is `hashlib.sha256(f"{canonical}:{province}:{cell}")`
per the `planning/casfri-v5-schema-integration-plan.md` §5 risk register.

### 3.3 Unknown-species flagging

If a CASFRI translation lookup misses a producer code, the adapter emits
a row with `species_group_raw = "QC_(?)"`, `species_group = "unknown"`,
and `species_value_policy = "qc_species_unmapped_unresolved"`. The same
flag is used for BC. This is the team's idempotent signalling for
upstream-side gap rows; downstream consumers should treat unknown-species
rows as G1 quarantined, not as production data.

---

## 4. Cost and yield policy gaps (NOT in CASFRI; required downstream)

| Concern | CASFRI v5 has it? | Source-of-truth after CASFRI translation | Adapter behaviour |
|---------|-------------------|-----------------------------------------|--------------------|
| `supply_offer.supply_cost` (CAD/m³) | No | Province-level stumpage tariffs, MoF/MRNF documents, calibrated_cost_surfaces gate | Adapter emits `supply_cost = 0.0` and `supply_cost_unit = "CAD_per_m3"` with `supply_cost_value_policy = "qc_supply_cost_not_calibrated"` (placeholder) until the cost gate resolves. |
| `supply_offer.delivered_cost_proxy` (CAD/m³ using haul + mill) | No | Forest/wide transport tables | Adapter emits per-region placeholder costs (BC: $22–38 /m³; QC: $25–42 /m³) with `delivered_cost_proxy_value_policy = "qc_delivered_cost_proxy_estimated_synthetic"`. |
| `supply_offer.quantity` (m³ of merchantable log) | No | `compilation_for` × `vol_bloc_*` × diagnostic yield rule | Adapter emits `compilation_for` derived volume with `inventory_value_policy = "qc_compilation_for_synthetic_polygon_volume_uncalibrated"`. |
| `commodity_id` (`log` vs. `logging_residue` vs. byproducts) | Yes (sort of, via species/structure) | Yield adapter | Adapter maps species → `log` commodity and group of logging-residue-eligible cells → `logging_residue`. |

---

## 5. Open / unresolved (audit register)

1. **Crown closure bucket → percent** (`CODE_DEN` A/E → 0–100 %): not
   signed. Adapter emits `qc_code_den_arb_class_bucketed`.
2. **Height second-layer defaulting**: QC `HAUTEUR` single-valued →
   CASFRI second-layer = `null / NOT_REPRESENTED` OR defaulting to the
   upper height. Adapter emits `qc_height_layer2_defaulting` until the
   decision is ratified.
3. **Logging-residue policy** (does QC have a primary-residue polygon
   layer?): no — fall back to `compilation_for` granulometry. Adapter
   emits `qc_residue_policy_unresolved`.
4. **Cost surface**: not in CASFRI; no province-level stumpage table
   for QC. Adapter emits `qc_supply_cost_not_calibrated`.
5. **Stumpage legacy** (legacy `STATION_FOR` join logic): STATION_FOR
   join to PEUPLEMENT is a stand-by-stand link. CASFRI v5 does **not**
   deliver station-join out of the box. Adapter emits
   `qc_station_join_unresolved`.
6. **Non-forest land (NFL)** split: QC uses `DESCRIPT` text to flag NFL
   polygons; CASFRI has an explicit `casfri.nfl` layer. Adapter emits
   `qc_nfl_descriptive_split_pending`.
7. **Disturbance lookup**: `qc_disturbance_lookup.csv` is supplied by
   upstream; QC's `STRATE_SUPP_POP2`-style disturbance layer is not yet
   in this dataset (5E IEQM 2023 vintage is the latest). Adapter emits
   `qc_disturbance_5e_ieqm_unresolved`.
8. **5E IEQM `ipf05` translation tables** are present upstream; we will
   add them to `data/raw/casfri_v5/translation_tables/` if needed but
   3e inventaire (`qc_ini03_*`) is the registered QS reference for
   this increment.
9. **Verification of the live 21M feuille's actual GPKG schema** against
   the `qc_ini03_*` translation rule rows: the live file's column set
   is the validation target, mirrored in
   `tests/test_casfri_qc_reader.py::test_against_real_feuille_21m_geometry`.
10. **The `qc_ipf05` lookup vs the `qc_ini03` lookup**: cross-check
    understands that `qc_ipf05` (5e inventaire 1998–2016 photo year) is
    the 2023 release's effective schema; `qc_ini03` is the 3e inventaire
    (often retired in 2026 archival). Adapter uses `qc_ipf05` rules
    where overlap exists.

---

## 6. Continuous-improvement pointers

- This crosswalk is **load-bearing** for the
  `fresh_fibre.casfri_qc_reader` unit-test fixtures.
- Each row in §1 is asserted by name in
  `tests/test_casfri_qc_reader.py::test_field_mapping_dispatch` to
  prevent silent unmapped fields.
- The `unknown-species` flag (§3.3) is asserted by
  `tests/test_species_canonical.py::test_unknown_species_flag`.

---

*Crosswalk Version* **1.0** — *2026-08-09* — *For* **CASFRI v5.3.1 / QC
5E IEQM / FRESH v0.1.0a1+**.
