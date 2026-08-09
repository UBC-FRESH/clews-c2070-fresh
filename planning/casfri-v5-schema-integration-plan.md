# CASFRI v5 Schema Integration Plan

**Document Status:** Draft v1 (planning-note, not yet a roadmap phase)
**Date**: 2026-08-09
**Prepared by**: UBC FRESH / CLEWs-C2070 forestry data maintainers
**Governance**: Proposed under the existing **governing issue `#1`** (P1.2
national source discovery under `ROADMAP.md`); proposed **child issue `#29`**
is to be created on GitHub by the team when execution starts. See
`metadata/source-reviews/casfri_v5_schema.md` (D1, the companion source review)
for the upstream licence / provenance / four-artifact disambiguation.
**Source-review cross-link**: §4 below references D1 §5 (mapping table); §5
references D1 §6 (licensing posture); §8 references D1 §8 (readiness-gate
impact). The plan is intentionally self-contained but folded into a thin
D1 ↔ D2 pair.

---

## 1. Plan identity

| Field | Value |
|-------|-------|
| Plan | CASFRI v5 PostgreSQL-schema integration into the FRESH pipeline
(via the U. Laval CASFRI/CASFRI conversion framework, v5.3.1, GPL-3.0). |
| Title | `casfri-v5-schema-integration-plan` (matches lowercase-with-hyphen
file-naming rule from `AGENTS.md`). |
| Governing issue | `#1` (P1.2 — national source discovery). |
| Proposed child issue | `#29` — *a proposal*. The team creates the actual GitHub
issue. The existing `planning/source-triage-queue.md` will gain a new line referring
to this plan. |
| Why now | The existing triage queue
(`planning/source-triage-queue.md`) has finished
`biomass_inventory_business` (closed as not_suitable) and is mid-stream on
provincial-fragment sources; CASFRI is the jurisdiction-level harmonization
framework that unlocks per-polygon vector richness that SCANFI v2 cannot
match with its 30 m grid. This is the natural follow-up. |
| Companion source review | `metadata/source-reviews/casfri_v5_schema.md` (D1).
|
| Sequencing vs. Phase 7 | This plan **extends Phase 7 P7.1** (source
triage) without adding a new Phase 9. P7.1 explicitly governs source-triage
continuation under `#1 → #28 → #29`. See §7 below. |
| Companion license counsel | Open (P*.1 prerequisite). GPL-3.0 contamination
risk on adapter. |
| Acceptance criteria | See §6 below. |

## 2. Goal and non-goals

### 2.1 Goal

Integrate the CASFRI v5 PostgreSQL schema + conversion framework into the
FRESH pipeline such that **at least one open-jurisdiction provincial FRI**
is ingested through the CASFRI conversion pathway, the resulting inventory
polygons are mapped to FRESH `supply_offer`, `spatial_unit`, and `commodity`
contract rows, and at least one of the existing readiness gates
(`species_composition` first) is moved into `in_progress` once the BC
diagnostic fixture passes.

### 2.2 Non-goals (explicit)

The following are explicitly **out of scope** for this plan so that it
remains bounded and verifiable; they remain separate work items or remain
blocked:

1. **No bulk polygon download.** CASFRI v5 framework does not bundle the
source FRIs; our job is to integrate the framework, NOT to archive all
provincial FRIs in the parent repo. Polygons belong in `data/` per AGENTS.md.
2. **No DUA work for SK, NT, AB jurisdictions here.** The 49-row truth table
(D1 §3) flags several jurisdictions as DUA-request only; opening those DUAs
is a separate work item that follows the existing NFI DUA pattern (see
`metadata/license-determinations/nfi-targeted-request.md` and
`metadata/license-determinations/nfi-access.yaml` for the working
template). This plan only advances the open-jurisdiction subsets.
3. **No SCANFI replacement.** CASFRI and SCANFI are complementary (D1 §2).
SCANFI v2 wall-to-wall 30 m rasters remain the national screening layer;
CASFRI v5 vector polygons layer on top for jurisdiction-level polygon
richness. Do not propose replacing SCANFI inputs in `fresh_fibre.scanfi_supply`.
4. **No production yield-policy redesign.** Gauthier et al. (2015) yield
curves remain the primary method-reproduction pathway; CASFRI is consumed
in `fresh_fibre.gauthier_yield` upstream of the supply generator, not
replacing it.
5. **No GPL-3.0 fork of CASFRI.** The framework is referenced as a sibling
submodule (or sidecar imaging); we do **not** vendor it into the parent
repo to avoid GPL-3.0 contamination of the adapter (see §5 risk register).
6. **No ETS delivery timetable.** ETS season is November–March; the
provincial FRI cycles do not align with ETS monitoring, and we do not
prioritize against ETS scheduling here.

## 3. Phasing — proposed as a Phase 9 *candidate* or as an extension to Phase 7 P7.1

### 3.1 Phase placement

Both placements are defensible. The chosen default position is:

> **Default recommendation:** extend Phase 7 P7.1 (source triage) with a
> `P7.1.CASFRI` continuation entry. Create child issue `#29` under
> governing `#1`, no new phase is created. This preserves the existing
> triage discipline and matches the AGENTS.md "at most three issue levels"
> rule (phase → task → subtask).

A **Phase 9 candidate** section is reserved at the bottom of this plan (§9)
in case the team prefers a fully scoped new phase for visibility. Either
path produces the same deliverables; the disambiguation only changes how
the GitHub issue tree looks.

### 3.2 Subtask plan (P7.1.CASFRI *)

| Subtask | Description | Acceptance |
|---------|-------------|-----------|
| **P7.1.CASFRI.1** *Framework pull* | Pull
`github.com/CASFRI/CASFRI@v5.3.1` into the `data` submodule as a sibling
sidecar (`data/external/casfri-casfri/`) — **NOT vendored into the parent
repo per AGENTS.md**. Inventory the published PostgreSQL DDL and translation
tables. Record the SHA-256 of `casfri_schema*.sql`, the v5.3.1 tag, and the
last-modified timestamp. | `data/metadata/casfri/schema_pulled_at.txt`,
`data/metadata/casfri/inventory_metadata_v5_3_1.csv` (49-row). |
| **P7.1.CASFRI.2** *Acquisition matrix* | Build per-jurisdiction
acquisition matrix (Tier 3 → Tier 4 — see D1 §7) covering BC, QC modern
vintage, NS, PE, YT, NL02 as Tier-3 candidates and the mixed-tier
jurisdictions (MB, NB, ON) as Tier-4 follow-ups. Document DUA pathway and
verified openness. | `metadata/source-crosswalks/casfri_v5_acquisition_matrix.md`;
rows added to `metadata/source-inventory.csv`. |
| **P7.1.CASFRI.3** *Schema mapper* | Build `cas → ws3 contract`
column crosswalk. The mapping table **lives in D1 §5** but a friendly
markdown companion is committed under
`metadata/source-crosswalks/casfri_v5_schema.md` for offline reference and
test-data generation. | `metadata/source-crosswalks/casfri_v5_schema.md`
plus a regression test that asserts the mapper is deterministic on a
representative BC VRI extract. |
| **P7.1.CASFRI.4** *Adapter module* | Add `fresh_fibre.casfri_import.py`
that emits `supply_offer.csv`, `spatial_unit_id`, and `commodity_id` rows
from CASFRI-converted PG-extracted CSV. Stays in the parent repo;
**thin wrapper, NO GPL-3.0 code vendored.** | New module file with
typed adapters over `fresh_fibre.schema`, contract coverage, and tests in
`tests/`. |
| **P7.1.CASFRI.5** *BC diagnostic fixture* | Build a `bc-casfri-diagnostic`
fixture mirroring the existing `bc-mini-scanfi-diagnostic` shape: a tiny BC
VRI subset ingested through CASFRI's conversion pathway, ws3-shaped harvest
output, and 5-layer LP variant. | A new fixture dir
`metadata/ffcm-archive/prototype-inputs/bc-mini-casfri-diagnostic/` with
the fixture manifest + a solved LP variant + three-way comparison
(synthetic / ws3-smoke / SCANFI-diagnostic / SCANFI+Gauthier / BC+CASFRI
diagnostic). |
| **P7.1.CASFRI.6** *Registry promotion* | Once P*.5 admits a
diagnostic-tier evidence, flip `casfri_v5_schema` in
`metadata/source-inventory.csv` status from `candidate` to
`accepted_subset_recommended` and add a curated entry to
`metadata/required-datasets.yaml` with `preferred: true` flag for the
framework and `preferred: false` flags for any rediscretized source FRI
data. | Status row flip; new entry in registry following the format of
`scanfi_v2`. |
| **P7.1.CASFRI.7** *Readiness-gate update* | When BC fixture passes,
flip `metadata/readiness-gates.yaml` `species_composition` status from
`blocked` to `in_progress` with a notes line documenting the CASFRI
contribution. National scaling remains blocked. | YAML updated; notes
explicitly cite this plan. |

**P*.2 additional acceptance criterion (added per reviewer finding m3):**

- [ ] SK row in `metadata/source-inventory.csv` reconciled against
      `metadata/source-reviews/provincial_forest_inventories.md` snapshot —
      per-vintage split documented.

### 3.3 Branching and review pattern

Per `AGENTS.md`'s "phase → task → subtask" rule:

- Feature branch: `feature/p7-source-triage-casfri-v5-schema`.
- One parent PR for the **planning documents** (D1 + D2 + D3 + D4 +
D5 + D6). Per `AGENTS.md`, "commit early and thematically, ideally at
roadmap task or coherent subtask granularity" — but documentation-only
changes can be coherently bundled.
- Follow-up PRs: per-subtask P7.1.CASFRI.1 through P7.1.CASFRI.7, each with
its own verification surface.

## 4. Schema-mapping sketch

The full crosswalk lives in **D1 §5**. This section captures enough of it to
be self-contained for planning purposes; reviewers should treat D1 as the
canonical reference. We preserve the four-table focus: `casfri.cas`,
`casfri.cst`, `casfri.geo`, `translation_tables/<province>/*.csv`.

| CASFRI source | FRESH contract (column(s) in `fresh-interface-contract.csv`) |
Transformation note | Open unknown |
|----------------|------------------------------------------------------------|--------------------------|-------------|
| `cas.stand_id` (PK) | `supply_offer.source_record_id` for
traceability through `run_provenance`; **not** part of `supply_offer`
required_fields. | Passthrough identifier preserved. | None. |
| `cas.inv_id` (links to `inventory_metadata.csv`) | `spatial_unit.source_dataset_id` | Required by the contract; per AGENTS.md every row references
the upstream source. | Vendor the matching inventory-metadata row. |
| `geo.geometry` (Polygon, EPSG:3005 for BC VRI EPSG:4326 variants per
province) | `spatial_unit.geometry_ref` (parent repo holds path only) |
Per AGENTS.md data boundary; live in `data/` submodule. | Coordinate with
submodule path canonicalization; ensure DataLad git-annex tracks the
'extracted geometry' (CASFRI separates geometry from attributes). |
| `cas.species` (raw code, e.g. BC VRI `SPCS_CD_1`) → translation table →
canonical species (FRESH-side canonicalization does not yet exist as a
single registered table; planned under `metadata/source-crosswalks/casfri_v5_schema.md`
in P*.3, with species-group fall-back to `fresh_fibre.gauthier_yield`
species groups where the canonical table is not yet populated) | `commodity.commodity_id` | Two-step translation: CASFRI per-jurisdiction translation
table, then a FRESH canonical-agreement lookup. **Open:** the second-step
table must be created or extended as part of the work; today it does not
exist in a uniform form across jurisdictions. |
| `cas.age` (years or proxy age class per jurisdiction) | (LP has no
explicit age column) | Consumed upstream by `fresh_fibre.gauthier_yield` |
polygon-age → stand-age-class bucket for Gauthier aggregation. | None; the
yield-policy side is already exercised in P3.7 diagnostic. |
| `cas.height`, `cas.closure`, `cas.biomass` (where present per
jurisdiction) | (LP has no direct yield-side columns) | Consumed by
Gauthier yield layer via the SCANFI diagnostic supply generator pattern. |
None; reuse `fresh_fibre.scanfi_supply` adapter shape. |
| (derived by yield policy) → `supply_offer.quantity` (m³) | After yield
policy, NOT in CASFRI itself. | Two-step: CASFRI attributes + Gauthier
yield curve → ws3 harvest output → supply_offer. |
| (cost surface) → `supply_offer.supply_cost`,
`supply_cost_unit` | NOT in CASFRI. Stumpage or NFI Provincial Reports are
the source — feed from `calibrated_cost_surfaces` readiness gate work. |
Inherits the `calibrated_cost_surfaces` gate; flagged but not unblocked. |
| (translation `inventory_metadata.casfri_release`) → standalone provenance
row | Rename and mirror into `run_provenance.code_version` per FRESH
contract. | None. |

**Self-vs-D1 note:** D1 §5 contains the full column-level table (~12 rows)
with the full CC-BY / LGPL framing; this section's 10-row table is the
lean planning view.

## 5. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| 1 | **GPL-3.0 contamination on the adapter.** The conversion framework
is GPL-3.0; if we statically link the framework into
`fresh_fibre.casfri_import` (e.g., vendor translation tables or call
framework code as part of import path), the adapter itself may need to be
GPL-3.0-compatible. | medium | high (forces licence upgrade of the parent
repo's adapter module) | **Do NOT vendor framework code in the parent
repo.** Reference the framework via sibling-sidecar submodule
(`data/external/casfri-casfri/`) and call it through subprocess or SQL
import rather than as an in-process Python module. Have license counsel
explicitly sign off on the path before P*.4 implementation. |
| 2 | **Per-jurisdiction DUA blocking.** Mixed-tier jurisdictions (MB,
NB, ON) request DUA-first; some ON GeoHub flavours prohibit redistribution
of derived products. | high | medium (delays Tier-4 expansion; BC only is
the safe Boundary) | Tier-3 anchor in BC only; ON requires explicit
redistribution-clause analysis before any fixture that contains ON
polygons may be published. Tier-4 jurisdictions opt in only when the DUA
process yields a redistribution-friendly clause. |
| 3 | **Schema staleness.** Schema is `v5.3.1` from 2021-08-17; no
releases since. Disconnect between the schema and the latest provincial
FRI cycles may surface as column-name mismatches. | medium | medium | Lock
to v5.3.1 in the fixture; record schema SHA-256 in
`data/metadata/casfri/`. If a fresh provincial snapshot cannot be
converted, document the gap explicitly in the gap register
(`metadata/ffcm-archive/prototype-gap-register.csv`). |
| 4 | **Translation-table ownership drift.** Per-jurisdiction translation
tables produced by the framework are subject to GPL-3.0; if a downstream
consumer generates a derivative work, the licence may extend. | medium | medium | The FRESH adapter reads the translation tables at run-time rather
than copying them into the parent repo. |
| 5 | **Spatial-unit key mismatch across CASFRI and FRESH contract.**
Per-jurisdiction `inventory_id` IDs may collide or reuse values across
provinces. | medium | medium | Prepend province code (`BC_`, `QC_`) to all
derived `spatial_unit_id` values. Add a deterministic `hashlib.sha256` of
`{province}:{cas.stand_id}` as a defensive cross-check in tests. |
| 6 | **Cost proxy missing.** CASFRI does not include stumpage; the
adapter must NOT silently leave `supply_cost` empty. | medium | high
(supply_offer schema check may fail) | Default `supply_cost="0"` and
`supply_cost_unit="CAD"` only after explicit unit check in
`fresh_fibre.units`; downstream consumers MUST tag this as
`placeholder_cost_not_calibrated`. Document the placeholder in the fixture
README. |
| 7 | **Yield-policy fragmentation.** Gauthier et al. (2015) yields are
the team's primary reproduction path; CASFRI's per-polygon disaggregation
may tempt scope-creep into a jurisdiction-specific yield curve. | medium | medium | This plan explicitly *does not* introduce jurisdiction-specific
yield curves; P3.7-disaggregated diagnostic lanes remain the test bed. |
| 8 | **Open-jurisdiction attribution drift.** Several open jurisdictions
require specific attribution when republishing derived products. | medium | low | Add an `attribution` field to fixture README; require explicit citations
in the diagnostic fixture manifest. |
| 9 | **External (submodule) version churn.** Reference to CASFRI at
v5.3.1 is stable but a future upstream migration could complicate the
adapter. | low | medium | Pin the SHA-256 of the `v5.3.1` tag; check
periodically (>=6-monthly) by re-running the diagnostic fixture. |

## 6. Acceptance criteria

Integration is "complete" when **all** of the following are true:

1. ✅ `metadata/source-reviews/casfri_v5_schema.md` (D1) is committed and
referenced from `metadata/source-inventory.csv` row `casfri_v5_schema`.
2. ✅ `planning/casfri-v5-schema-integration-plan.md` (this plan, D2) is
committed and referenced from `ROADMAP.md` and the source-triage queue.
3. ✅ `metadata/source-inventory.csv` row `casfri_v5_schema` exists with
status `candidate` (NOT `accepted`); notes link to D1 and D2.
4. ✅ `metadata/readiness-gates.yaml` `species_composition.notes` line
mentions CASFRI and the proposed child issue. **Status remains `blocked`**
until P*.5 admits diagnostic evidence (per the team's existing discipline).
5. ✅ `CHANGE_LOG.md` contains the dated entry under 2026-08-09.
6. ✅ `metadata/source-crosswalks/casfri_v5_schema.md` and a TSV/CSV
artefact table committed; deterministic cross-check regression test
passes.
7. ✅ `fresh_fibre.casfri_import.py` exists; tests in `tests/` pass.
8. ✅ BC diagnostic fixture
(`metadata/ffcm-archive/prototype-inputs/bc-mini-casfri-diagnostic/`)
materializes, the comparison surface lists the new lane with explicit
`value_policy=diagnostic` and matches the SCANFI Gauthier lane shape.
9. ✅ `pytest tests/ -q` passes; `git diff --check` is clean; full
verification per `ROADMAP.md` Phase 8 closure runs.
10. ✅ License-counsel sign-off recorded for the GPL-3.0 contamination
question (P*.1 prerequisite).
11. ✅ Child issue `#29` is created on GitHub (team-owned action), linked
from `#1` body, and the parent PR description references this plan.

## 7. Sequencing relative to other phases

This plan sits inside an active project and must not collide with in-flight
work.

### 7.1 vs. Phase 7 P7.1 (source triage under `#1 → #28`)

- Phase 7 P7.1 is the parent source-triage work-stream, governed by issue
`#1`; the active child is `#28`.
- This plan proposes extending Phase 7 P7.1 with `P7.1.CASFRI` continuation
entries. No new Phase 9 is created (default position); a candidate Phase 9
stub is reserved at the end.
- The proposed child issue number **`#29`** is next-up after `#28`. Team
must create the actual GH issue before P*.1 work begins.

### 7.2 vs. NFI Ground Plot DUA (in flight under `#28`)

The team's pre-submission-ready NFI package lives at
`metadata/license-determinations/nfi-targeted-request.md`. CASFRI and NFI
ground plots are **complementary validation paths**:

- NFI Ground Plot → plot-level truth for yield calibration.
- CASFRI v5 → polygon-level inventory richness for jurisdiction coverage.

They are sequenced as **parallel**: Tier 5 of the acquisition strategy
(D1 §7) is NFI; Tier 3/4 is CASFRI. The NFI DUA's approval timeline (6–18
months per `planning/phase-7-production-readiness-memo.md` §5 Gate 1)
should not block CASFRI Tier-3 work because CASFRI Tier-3 uses open-data
BC VRI (OGL BC).

### 7.3 vs. Phase 7 P7.3 (readiness gates)

This plan produces a `notes` line in `species_composition` (D4). It does
not flip the gate from `blocked` to `in_progress` until BC CASFRI fixture
(P*.5) reaches the same acceptance threshold as the existing SCANFI
diagnostic lane. The flip is a separate commit / change-log entry when the
evidence is in.

### 7.4 vs. Phase 6 SCANFI acquisition plan

The Phase 6 plan (`planning/phase-6-scanfi-acquisition-plan.md`) is
**completed** for the authorized 5-file 2025 SCANFI subset. CASFRI is a
follow-on. We deliberately do **not** re-open the broader SCANFI
authorization (full 312 GB, spsCC_*, north extrapolation mask) as part of
this plan; that is a separate acquisition plan.

### 7.5 vs. Scientific Model Recovery Plan

Any future work to validate CASFRI-derived wall-to-wall national inventory
against NFI ground plots or SCANFI v2 wall-to-wall covers **Gates G5–G8**
of the recovery plan, which remain **unapproved** per
`planning/scientific-model-recovery-plan.md`. P*.5 explicitly produces
**diagnostic-only** evidence; it is not G5–G8 work.

## 8. Open questions for the team

The integration only progresses once these are answered:

1. **GPL-3.0 counsel review: confirm the adapter path is safe.**
2. **Tier-3 jurisdiction priority: BC only, or BC + ON-without-redistribution
   flag?**
3. **Translation-table ownership: read-at-runtime vs. cache in `data/`.**
4. **Provincial yield curves: which jurisdictions to ingest first in
   parallel with CASFRI Tier-3, given that CASFRI does not itself provide
   yield curves?**
5. **Spatial-unit key crosswalk: prefix-with-province, or replicate
   per-province catalogues?**
6. **Whether the FRESH-side species canonicalization (planned under
   `metadata/source-crosswalks/casfri_v5_schema.md`, not yet existing as
   a single registered table) needs to be extended jurisdictionally,
   and who owns that extension.**

## 9. Phase 9 *candidate* (not authorized)

If the team prefers a fully scoped Phase 9 for visibility and reporting
discipline, the placeholder looks like this. **NO WORK IS AUTHORIZED** until
the team approves CASFRI as Phase 9.

```markdown
## Phase 9: CASFRI v5 Schema Integration (CANDIDATE — unapproved)

Governing issue: `#1` (P1.2 source discovery).
Parent issue: TBD after team approves Phase 9 promotion.
Release milestone: post-v0.1.0a1 (no version assigned yet).

- [ ] P9.1 Pull CASFRI/conversion framework (CASFRI/CASFRI@v5.3.1) into
  data/external sidecar submodule.
- [ ] P9.2 Build per-jurisdiction acquisition matrix (Tier 3 → Tier 4).
- [ ] P9.3 Build CASFRI → ws3 supply contract schema mapper.
- [ ] P9.4 Add fresh_fibre.casfri_import adapter with regression tests.
- [ ] P9.5 BC diagnostic fixture (casfri_v5, BC VRI subset).
- [ ] P9.6 Promote CASFRI v5 schema source to required-datasets.yaml.
- [ ] P9.7 Update readiness-gates.yaml species_composition status from
  blocked to in_progress.
- [ ] P9.8 Close-out: changelog, roadmap, evidence-log, child issue PR.
```

**Decision note:** this candidate exists so the team can promote CASFRI to
a fully scoped new phase if the existing P7.1 approach is judged too narrow.
The default in §3.1 is to extend P7.1.

---

*End of CASFRI v5 Schema Integration Plan.*
