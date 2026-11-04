# CASFRI v5 Schema Source Review

Review date: 2026-08-09.
Author: UBC FRESH / CLEWs-C2070 forestry data maintainers.
Status: candidate (proposed integration; not promoted until Tier 3 ingestion
evidence exists per `planning/casfri-v5-schema-integration-plan.md`).
Governance: this review links to governing issue `#1` (P1.2 source triage under
`ROADMAP.md`) and the proposed child issue under that parent.

## Cross-references

- Companion integration plan: `planning/casfri-v5-schema-integration-plan.md`.
- Readiness-gate impact: `metadata/readiness-gates.yaml`, gate
  `species_composition` (and dependencies `national_scaling`,
  `facility_layer`).
- Companion provincial FRIs row: `metadata/source-inventory.csv` row
  `provincial_forest_inventories` (CASFRI is an alternate acquisition path for
  the same underlying provincial source data; see §4).
- Companion SCANFI source review: `metadata/source-reviews/scanfi_v2.md`
  (CASFRI provides vector polygon richness that SCANFI v2 does not).
- Companion SCANFI 2020 review: `metadata/source-reviews/scanfi_2020.md`
  (covers the previous-generation federally-derived vector layer).

## 1. Source identity

| Field | Value |
|-------|-------|
| Dataset | **CASFRI v5 PostgreSQL conversion framework** (Canadian Attribute
Sample FRI) — the open-source translation framework that converts heterogeneous
provincial Forest Resource Inventory (FRI) polygon layers into a single,
harmonized PostgreSQL schema. |
| Repository | `https://github.com/CASFRI/CASFRI` |
| Latest tag | `v5.3.1`, dated 2021-08-17 (no releases since; v5 has been
development-stable for ~five years). |
| Authors / maintainers | Pierre Racine, Jean-François Houle, Grant Edwards
(Université Laval, Centre d'étude de la forêt). |
| License | **GPL-3.0** for code, schema, and translation tables. The source
FRIs flowing through the pipeline each retain their own per-jurisdiction
licences — see §6. |
| What the README explicitly says it does NOT include | *"...forest resource
inventories are not provided with this project. They are produced, validated
and managed by the appropriate jurisdictions."* Source:
`https://github.com/CASFRI/CASFRI`. |
| Schema publication | PostgreSQL DDL (`casfri.*` tables, indices, comments) and
SQL translation tables under `casfri/`. |
| Conversion scripts | Per-jurisdiction translation flow scripts that take a
raw provincial FRI extract and translate its native schema (e.g. BC VRI,
QC PFI) into the harmonized CASFRI schema. |
| Inventory metadata | `metadata/inventory_metadata.csv` — a 49-row truth table
listing every provincial/territorial FRI version the framework understands,
with one row per FRI snapshot. See §3. |
| Scope of integration | This review covers only the **framework and schema**,
not the source FRIs themselves. We are integrating the harmonization engine
into the FRESH pipeline; per-jurisdiction FRIs are acquired separately per
the team's existing provincial-forest-inventories triage. |

## 2. What this source IS vs. what it is NOT

There are **four artifacts sharing the "CASFRI" name** in the Canadian forestry
data landscape. Failure to disambiguate them produces repeated confusion in
source-triage conversations. The four are listed in the table below.

| # | Artifact | Identity | License | Bulk-downloadable? | What we are
doing with it |
|---|----------|----------|---------|--------------------|------------------------------|
| 1 | **CASFRI conversion framework**
(`github.com/CASFRI/CASFRI`, U. Laval / Racine /
Edwards / Houle) | Translates 49 provincial/territorial FRI snapshots into a
harmonized PostgreSQL schema (`casfri.cas`, `casfri.cst`, `casfri.geo`,
`casfri.nfl`, translation tables). | **GPL-3.0** | **Yes — code/schema only.
The repository explicitly does NOT bundle the source FRIs.** | **Integrating
this.** Adding the schema, the conversion framework, and one open-jurisdiction
diagnostic ingest into the FRESH pipeline. |
| 2 | NRCan/NFIS raster product labelled "CASFRIv5"
(Open Canada record `0177f4ed-…`) | Two 50 m raster layers:
`dominant_genus_casfri` and `tree_type_casfri` (genus-dominant / tree-type
classifications) derived from CASFRI v5 by NRCan. | Open Government Licence –
Canada | **No — WMS image only. WFS explicitly NOT provided,** see the Open
Canada WMS-only disclaimer at
`https://open.canada.ca/data/en/dataset/0177f4ed-123c-43a8-83ed-44dc12e1af6e`. | **Not integrating.** A WMS tile is a display service, not an ingestible
data source for ws3 / LP supply modelling. |
| 3 | The **49 provincial/territorial source FRIs** themselves (BC VRI, QB
PFI, etc.) — i.e., the inputs the framework converts. | The actual
jurisdiction-level forest attribute polygon layers. | Mixed: per-jurisdiction
licences, see §3. | **Varies by jurisdiction** — some are openly downloadable,
others require DUAs, others are license-gated. | **Not the primary integral
on its own.** These are acquired and ingested through the CASFRI framework
(if accepted), making the framework the boundary object and the provincial
FRIs the data behind it. |
| 4 | **SCANFI v2** (Spatialized Canadian National Forest Inventory) —
often confused with CASFRI. | Federally gridded 30 m national attributes from
NRCan (Beaudoin et al. 2023);
`https://open.canada.ca/data/en/dataset/07653869-f303-46c2-a04e-9ab479b73cbf`. | Open Government Licence – Canada | **Yes — bulk raster download**, ~312 GB
total, authorized five-file 2025 subset already archived at
`data/raw/scanfi/v2/`. | **Already integrated** under `scanfi_v2` (see
`metadata/source-reviews/scanfi_v2.md`). **CASFRI does not replace SCANFI**;
the two are complementary:

- SCANFI = wall-to-wall raster (national, 30 m, federated schema).
- CASFRI v5 harmonized polygons = jurisdiction-specific vector richness where
open FRIs exist, allowing finer-scale species, age, and stand-structure
disaggregation at the inventory polygon level where SCANFI's 30 m grid loses
it.

**Conceptual edge (load-bearing):** *Open Government Licence – Canada ≠ openly
downloadable as bulk.* A licence may be openly permissive while the publisher
delivers only a WMS or limited tile service. The team's existing discipline
(see `metadata/source-reviews/canlad_and_pre_canlad_v2.md`) separates legal
permissiveness from technical delivery channel; CASFRI preserves that
distinction. The NRCan/NFIS "CASFRIv5" raster is WMS-only on a permissive
licence. The conversion framework is GPL-3.0 code that is admittedly openly
downloadable. The provincial FRIs vary.

## 3. 49-row truth table (provincial/territorial FRIs the framework understands)

**Source of record:**
`https://raw.githubusercontent.com/CASFRI/CASFRI/master/metadata/inventory_metadata.csv`
— 49 rows, one per provincial/territorial FRI snapshot. The framework defines
one translation flow per row. We reproduce all 49 rows below with the team's
current openness assessment, sourced from `metadata/source-reviews/provincial_forest_inventories.md`
and the 2026-08 series of provincial-source reviews.

### 3.a Openly downloadable jurisdictions (Tier 1 / Tier 2 candidates)

| Province | Source | Access | Notes |
|----------|--------|--------|-------|
| BC | BC Forest Inventory (VRI) | BC Data Catalogue File Geodatabase (OGL BC) |
Use for the first open-jurisdiction diagnostic ingest. Recommended as the
trigger jurisdiction for `P*.5` (BC-only diagnostic fixture) in the
integration plan. |
| QC (modern, 2023 vintage) | Données Québec GeoPackage /
Carte forestière 2023 | Direct download; CC-BY attribution. |
| NS | Per-county shapefile | Direct download; provincial terms. |
| PE | ArcGIS Hub | Direct download. |
| YT | FTP GeoYukon, 5k 2007–2012 | Direct download; Yukon terms. |
| NL02 (Aug 2023) | NL GeoHub | Direct download; NL terms. |
| SK (older vintages) | Provincial Forest Boundary Polygon; FRI: Forest stands;
SK Standard Unrestricted Use Data License v2.0; data.gov.sk.ca | Open
(`source-reviews/provincial_forest_inventories.md` lines 61–68, 2026-08-06
snapshot, ✅ Authoritative) |

> **Note:** Newer SK BAM/BEACON-derived FRI products remain DUA-only — see
> §3.c. The SK row in §3.c reflects the same per-vintage split.

### 3.b Mixed / partial inventories

| Province | Source | Status | Notes |
|----------|--------|--------|-------|
| MB | Nelson River FLI 2011–13 is public; MB01, MB02, MB04 are
industry-restricted. | Mixed. | Coordinate with Manitoba Sustainable
Development; some FRI cycles are D-gated. |
| NB | NB03 / NB06 are open; older cycles admin-gated via Dale Wilson
(NB NRRED) / Kyle Schmidt. | Mixed. | Several cycles are DUA-request. |
| ON | Ontario GeoHub hosts the FRI Term 2 (2018–2028) extracts but
**redistribution is prohibited** from the GeoHub mirror; ON01 (2009 DVD
release) requires a signed licence. | Mixed. | License permitting
**internal model use** but **not redistribution** must be resolved before
publishing derived inventory cells in the parent repo. |

### 3.c Request-only / DUA jurisdictions

| Province | Source | Contact / path | Notes |
|----------|--------|----------------|-------|
| AB | AEP.Data@gov.ab.ca; contact Linda Smith. Historical FRI Phases 1–3
are PDF/raster only. | DUA request required for vector inventories. |
| BC07 | CANFOR industry-restricted cycle. | DUA. |
| AB27 | ALPAC industry-restricted cycle. | DUA. |
| AB31 / AB32 | Tolko industry-restricted cycles. | DUA. |
| SK | Older vintages: SK Provincial Forest Boundary Polygon + FRI Forest stands are openly licensed under SK Standard Unrestricted Use Data License v2.0 (per `provincial_forest_inventories.md` lines 61–68, 2026-08-06 snapshot). Newer BAM/BEACON-derived FRI products historically request-only (Matt Matwishyn, SK Ministry of Environment, 2025 contact). Per-vintage split must be re-verified immediately before any DUA is opened. | DUA for newer; **Open (Standard Unrestricted Use) for older vintages** |
| NT01–NT03 | Licences EXPIRED for several cycles; requires re-DUA. |
| NT04 | Available via NFIS FSS (`https://ca.nfis.org/fss/fss`); licence
shape validated by NRCan. | Open-with-DUA path. |

### 3.d Federal fallback path

A complementary federal path exists outside the CASFRI 49-row table: the
**NFI Ground Plot Data** request via
`https://nfi.nfis.org/en/datarequestform` — already in flight under the
team's existing NFI DUA work (see `metadata/license-determinations/nfi-targeted-request.md`).
CASFRI is the **vector polygon** equivalent; NFI ground plots are
**discrete plot measurements**. They are not substitutes; CASFRI complements
the validation path but does not replace it.

**Honest limit:** the openness assessment above is current as of the
2026-08-06 review of `provincial_forest_inventories.md`. Per-jurisdiction
licence templates evolve. Any single row above may need to be re-verified
immediately before the matching DUA is opened. The team must not cite the
table as a substitute for an active licence check.

## 4. Schema inventory — to verify, per the framework repository

**Status:** the CASFRI repository has not been pulled into the parent repo as
of 2026-08-09. The team has not exercised the conversion pipeline locally
yet. The schema inventory below is derived from the published documentation
in the repository README and the
`https://github.com/CASFRI/CASFRI/tree/master/casfri` source tree, and is
**load-bearing for the `Schema mapping sketch` table** in
`planning/casfri-v5-schema-integration-plan.md`. Reserving a `to verify` tag
because no schema.sql has been parsed under our local cache yet.

| CASFRI table | Description (from repo README) | Usage in FRESH (proposed) |
|--------------|--------------------------------|---------------------------|
| `casfri.cas` | Common Attribute Schema — most FRI polygon attributes after
translation (species, age, height, crown closure, etc.). | Primary source for
the species / age composition surface that drives Gauthier yield aggregation
and product-grade disaggregation. |
| `casfri.cst` | Common Stand Table — also holds inventory-level stand
descriptors for FRI polygons. | Cross-reference for `spatial_unit` rows in
the FRESH contract; alternative to per-polygon rasterization. |
| `casfri.geo` | Geometry rows — the polygon footprints themselves, one
row per CASFRI stand polygon. | Provides FRESH `spatial_unit.geometry_ref`
references via standard spatial joins. |
| `casfri.nfl` | Non-forest Land table — non-forested land polygons intermixed
in FRI extracts (water, settlement, agriculture). | Federal forest area
mask; complement to `forest_management_canada` and CPCAD exclusion masks. |
| `casfri.dly` | Disturbance layer (where present per-jurisdiction). |
Potential CanLaD/NFDB crosswalk for disturbance-aware age disaggregation. |
| Translation tables (`translation_tables/<province>/*.csv`) | Per-column
codes and lookups (species, age class, site index) per source FRI. | Used by
the Postgres translation flows; reproduce locally under
`data/metadata/casfri/translation_tables/` once the framework is pulled. |
| `metadata/inventory_metadata.csv` | The 49-row truth table — provenance,
licence, custodian, vintage for every supported FRI snapshot. | **Already**
available at the published URL above; may be vendored under
`metadata/source-file-manifests/casfri_inventory_metadata.csv` for offline
reference. |

**Verification plan:** P*.1 of the integration plan pulls the framework and
parses `casfri/casfri_schema_5.3.1.sql` (or the equivalent DDL on the
v5.3.1 tag) into a local-only `data/metadata/casfri/schema.sql` snapshot
**without committing it to the parent repo** per AGENTS.md. A friendlier
extracted column reference (markdown-formatted) is committed as the
schema-mapping source-of-truth under
`metadata/source-crosswalks/casfri_v5_schema.md` so downstream code can cite
it without pulling GPL-3.0 code into the parent repo.

## 5. Mapping to clews-c2070-fresh internal contract

**Target contract:** `metadata/ffcm-archive/fresh-interface-contract.csv`,
specifically the `supply_offer`, `spatial_unit`, `commodity`, and `scenario`
rows. The crosswalk is the integration core. Open rows are explicitly listed
in `planning/casfri-v5-schema-integration-plan.md` §4.

| CASFRI column (or translation
table lookup) | ws3 supply contract column | Transformation note | Open issue |
|------------------------------|-----------------------------|---------------------|-----------|
| `cas.stand_id` (CASFRI primary
key on `cas`) | `supply_offer.source_record_id` | Passthrough identifier;
not part of LP contract but preserved for traceability via the
`run_provenance` row. | None. |
| `cas.inv_id` / FRI inventory
snapshot ID (joins to `inventory_metadata.csv` row) | `spatial_unit.source_dataset_id` | Required by the contract: every spatial unit must reference a
source dataset. | Tier-3 ingestion must populate this field. |
| `geo.geometry` (polygon) | `spatial_unit.geometry_ref` (geometry stays
in data submodule; parent repo holds ref/path only) | Per AGENTS.md data
boundary. | Derive path canonicalization; coordinate with `data/` submodule
Datalad dataset. |
| `cas.species` (raw code, e.g.
BC VRI `SPCS_CD_1`) | `commodity.commodity_id` (after translation through
the CASFRI translation table to the FRESH canonical commodity) | Two-step:
CASFRI translation table → FRESH canonical commodity lookup (the
FRESH-side canonicalization does NOT yet exist as a single registered
table; P*.3 of the integration plan will create `metadata/source-crosswalks/casfri_v5_schema.md` and extend the existing `metadata/ffcm-archive/fresh-interface-contract.csv` commodity row accordingly). | Translation table ownership (vendored under GPL-3.0 versus re-derived from published spec) — see §8 risk register. |
| `cas.age` (years OR proxy age
class) | (LP has no explicit age column; consumed by the
yield-policy layer, not supply_offer direct) | Pass age into the Gauthier
yield-curve adapter (`fresh_fibre.gauthier_yield`) — not into LP contract.
| None; existing Gauthier adapter path. |
| `cas.height` (m), `cas.closure`
(%), `cas.biomass` (t/ha, optional per jurisdiction) | (LP has no explicit
yield-side columns; consumed upstream by the yield adapter) | Pass through
the diagnostic supply generator (`fresh_fibre.scanfi_supply`) — already
present for SCANFI v2, will generalize for CASFRI. |
| (derived) → `supply_offer.quantity` (m³) | After yield-policy application
via Gauthier or jurisdiction-specific adapter. | Quantity arises from
yield curve, NOT from CASFRI directly. |
| (derived) → `supply_offer.quantity_unit` | The yield-policy output unit
(must match `fresh_fibre.units` canonical `m3` for accepted commodity). |
Cross-checked by `fresh_fibre.units`. |
| (cost placeholder) →
`supply_offer.supply_cost`,
`supply_cost_unit` | COST NOT IN CASFRI. Province-level stumpage rates or
**NFI Provincial Reports** are the source. | **Open:** must identify a
canonical cost surface; current `calibrated_cost_surfaces` gate is unrelated
to CASFRI but blocks downstream completion. |
| (translation
`inventory_metadata.casfri_release`) → standalone provenance | Rename and
mirror into `run_provenance.code_version` per FRESH contract. |

## 6. Licensing posture

The CASFRI v5 conversion framework is **GPL-3.0**. The provincial/territorial
source FRIs are **per-jurisdiction** licences, mostly **Open Government
Licence – Canada** or equivalent, with explicit redistribution prohibitions
in some provinces (Ont. redistribution from GeoHub is prohibited; several
mixed-jurisdiction rows require DUAs).

| Layer | Licence | Implication for clews-c2070-fresh |
|-------|---------|-----------------------------------|
| Conversion framework code (SQL, Perl/Python
conversion scripts, translation tables produced by the framework) | **GPL-3.0
** | If we **statically link** the framework into our adapter (e.g., vendor
its translation-table files into the repo, or call its code as part of the
adapter), the adapter itself may be required to be GPL-3.0 (or compatible).
We avoid this by: (a) **not vendoring** the framework in the parent repo —
the framework is referenced via `git submodule` or transient Docker image;
(b) keeping the adapter in a `LGPL-2.1`-or-compatible module style. **Counsel
review pending** — see §8 risk register row 1. |
| PostgreSQL schema (DDL) | **GPL-3.0** (same licence as the framework) |
A schema itself is typically not subject to copyright in Canada, but a
DDL-script may be. Using the DDL as a reference rather than a verbatim copy
in our adapter is the safest posture. |
| Translation tables (per-jurisdiction column maps) | **GPL-3.0** in the CASFRI
repo | Generated by the framework; consumers normally re-derive rather than
copy. Our adapter reads them at run-time from the vendored framework. |
| Source FRI polygon data (BC VRI, QC PFI, etc.) | **Per-jurisdiction** |
Separate licensing chain; covered by the existing provincial-source reviews.
The 49-row truth table in §3 records which jurisdictions are openly
downloadable, mixed, or DUA-gated. |
| WS3-harvested or transformed output | Our ws3 export + FRESH contract |
Not a CASFRI artefact; the GPL-3.0 does not retroactively extend to the LP
output. |

**Combined posture:** the framework is GPL-3.0; the data flowing through it
has its own per-jurisdiction licence; the FRESH pipeline consumes the
CASFRI-converted output and emits `supply_offer` rows that are not bound to
GPL-3.0. The exposure point is the **adapter** that calls into the
framework. Mitigation = keep the adapter thin, do NOT vendor framework code
into the parent repo, and have a separate license-counsel sign-off before
any GPL-adjacent reuse. See §8.

## 7. Acquisition strategy

| Tier | Action | Status (as of 2026-08-09) |
|------|--------|--------------------------|
| 1 | **WMS image of NRCan/NFIS CASFRIv5 raster** — useful for
visualization and qualitative cross-checks only. No bulk WFS. **Visible
provenance via the Open Canada record.** | **Available.** Already
inspectable as basemap; not part of the ingest pipeline. |
| 2 | **Source-of-truth framework** — pull the `v5.3.1` tagged
`CASFRI/CASFRI` repository into the `data` submodule (or a sibling sidecar
repo) and read schema/translation references from there. | **Open.**
P*.1 of the integration plan covers this. |
| 3 | **First openly-downloadable provincial FRI ingested through the
pipeline** (BC VRI recommended). | **Open.** P*.5 covers this with a
`BC-clean` diagnostic fixture. |
| 4 | **Multi-provincial expansion** (QC, NS, PE, YT, NL02). | **Open.**
Sequenced after Tier 3 passes. |
| 5 | **NFI Ground Plot DUA + CASFRI attribution** — the team's existing NFI
DUA package
(`metadata/license-determinations/nfi-targeted-request.md`) provides
ground-plot validation; CASFRI provides vector polygons. They are
**complementary** validation paths. | **NFI Ground Plot DUA is in flight
under the existing triage** (governing issue `#1` → child issue `#28`; see
`metadata/license-determinations/nfi-targeted-request.md` and
`planning/source-triage-queue.md`). |

## 8. Readiness-gate impact

Which of the existing production readiness gates (see
`metadata/readiness-gates.yaml`) become `in_progress` or `resolved` once
CASFRI is integrated? The table below updates the impact statement without
flipping any gate status; the flips happen only after P*.5 (Tier 3 ingestion)
and the adapter tests.

| Gate (existing) | Pre-CASFRI
status | Post-CASFRI (proposed) status (after P*.5) | Reason |
|-----------------|-----------------------------|-------------------------------------------|--------|
| `species_composition` | blocked (SCANFI spsCC_* layers deferred 123 GB) |
`in_progress` after Tier-3 BC CASFRI ingest | CASFRI jurisdictions
provide per-polygon species and stand-age data at higher fidelity than
SCANFI 30 m grid; sufficient to drive the Gauthier conifer/deciduous
aggregation in Tier 3. |
| `national_scaling` | blocked | blocked (still depends on
provincial yield curves and full transport network and calibration gates) |
CASFRI unlocks the inventory-side; transport / yield / cost / facility
gates still block national scaling. |
| `facility_layer` | blocked | unchanged | CASFRI does not provide
the facility layer. The legacy 2013 facility database remains the blocking
candidate. |
| `nfi_access` | blocked | unchanged | Independent of CASFRI; CASFRI
is the polygon-rich alternative; NFI is the plot ground-truth. |
| `disturbance_calibration` | in_progress | in_progress | CASFRI does
not include disturbance history directly; CanLaD/NFDB/NBAC remain the
appropriate sources. |

**Note:** CASFRI primarily unblocks `species_composition` because it provides
jurisdiction-level vector polygons with species and stand-structure fields
that SCANFI does not fully resolve at its 30 m resolution. CASFRI does NOT
substantially advance the other gates without separate work programs
(provincial yield curves for `national_scaling`, calibrated cost data for
`calibrated_cost_surfaces`, etc.).

## 9. Open questions / verification items left for the team

These are the open items that **the integration plan (`D2`)** must resolve
before this source can flip from `candidate` to `accepted`. The team — not
the schema-ingester — owns each item.

1. **GPL-3.0 contamination risk on the adapter.** Confirm with licence
counsel whether the adapter (`fresh_fibre.casfri_import` proposed in
`planning/casfri-v5-schema-integration-plan.md` P*.4) is required to be
GPL-3.0 if it merely reads translation tables from a git submodule.
2. **Translation-table ownership.** Decide whether to vendor the framework
translation tables as a sidecar repo under DataLad or read them at run-time
from the upstream repo. Vendoring risks stale copies; runtime-read risks
upstream churn (none since 2021-08-17). P*.1 records the decision.
3. **Jurisdiction priority.** BC is recommended (open Data Géoportail
Geodatabase, OGL BC, FCADM-aligned layers). Confirm with the team whether
to anchor Tier 3 in BC only or to also open ON with redistribution-clause
limitations noted.
4. **Provincial yield curves.** CASFRI provides polygon richness but does
not provide jurisdiction-specific yield curves; the existing
`provincial_yield_curves` blocking-dataset row
(`metadata/source-inventory.csv`) must be advanced in parallel rather than
treated as a CASFRI by-product.
5. **Spatial-unit key crosswalk.** CASFRI's `geo.stand_id` → FRESH
`spatial_unit.spatial_unit_id` is straightforward for BC VRI but requires
per-province mapping. The crosswalk should be testable on the BC diagnostic
fixture (P*.5) and extensible to other open jurisdictions.
6. **Child issue #29 proposal.** Per the integration plan, the proposed
child issue number is `#29` (next-up after the active triage child `#28`).
This is **a proposal**; the actual issue is created on GitHub by the team.
7. **Vendor or fork the framework.** `planning/casfri-v5-schema-integration-plan.md`
records the choice: the current plan is **NOT vendor, NOT fork** — reference
the framework as a sibling submodule to avoid licence contamination of the
adapter.

## 10. References

- CASFRI conversion framework repository:
  `https://github.com/CASFRI/CASFRI` (latest tag `v5.3.1` 2021-08-17).
- Inventory metadata CSV (49 rows, source of truth for §3):
  `https://raw.githubusercontent.com/CASFRI/CASFRI/master/metadata/inventory_metadata.csv`.
- NRCan/NFIS raster WMS only (NOT integrated — context only):
  `https://open.canada.ca/data/en/dataset/0177f4ed-123c-43a8-83ed-44dc12e1af6e`.
- Companion provincial-FRIs source review (where openness is decided per
row):
  `metadata/source-reviews/provincial_forest_inventories.md`.
- Companion SCANFI v2 source review:
  `metadata/source-reviews/scanfi_v2.md`.
- Companion SCANFI 2020 source review:
  `metadata/source-reviews/scanfi_2020.md`.
- Companion integration plan (D2):
  `planning/casfri-v5-schema-integration-plan.md`.
- Companion readiness gates:
  `metadata/readiness-gates.yaml`.
- FRESH internal contract target:
  `metadata/ffcm-archive/fresh-interface-contract.csv`.
- ws3 supply_offer column reference:
  `fresh_fibre.ws3_export.SUPPLY_OFFER_COLUMNS`.
- Provincial FRIs row (parent row grouping 49 jurisdictions):
  `metadata/source-inventory.csv` row `provincial_forest_inventories`.
- NFI Ground Plot DUA package (Tier 5 in §7 — adjacent validation path):
  `metadata/license-determinations/nfi-targeted-request.md`.

## Honesty / uncertainty summary

- **NOT locally cached:** the CASFRI framework has NOT been pulled into the
parent repo or the data submodule as of 2026-08-09. Section 4 (schema
inventory) is therefore declared `to verify`; the column reference in §5 may
require column-name corrections once a real DDL is parsed.
- **Reproduction of the 49-row truth table** depends on the standalone CSV at
the URL above. We do not row-by-row check the open-vs-DUA assessment in §3
against the latest provincial-portal state; the existing
`provincial_forest_inventories.md` review covers a 2026-08-06 snapshot.
- **GPL-3.0 adapter contamination** is not yet counsel-reviewed; §6 and §8
defer the final posture to a separate licence check.
- **Child issue number `#29`** is a *proposal*; the actual child issue is
created on GitHub by the team when work begins.

---

*End of CASFRI v5 Schema Source Review.*
