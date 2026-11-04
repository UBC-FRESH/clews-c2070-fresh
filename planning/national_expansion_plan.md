# National CASFRI Tier-3 Expansion Plan (post-BC + QC)

| Field | Value |
|-------|-------|
| Document | `planning/national_expansion_plan.md` |
| Replaces | Phase-5 task 5.3 (national jurisdictional expansion) |
| Companion source review | `metadata/source-reviews/casfri_v5_schema.md` (49-row truth table §3) |
| Companion policy review | `metadata/source-reviews/provincial_forest_inventories.md` |
| Companion integration plan | `planning/casfri-v5-schema-integration-plan.md` |
| Companion BC + QC framer | `planning/bc_qc_pipeline_strategy.md` |
| Owner | UBC FRESH / CLEWs-C2070 forestry data maintainers |

---

## 1. Goal

Order the next eight openly-downloadable / mixed / DUA-only jurisdictions beyond
the BC + QC foundation such that each acquisition follows the **minimum-viable-path**
pattern already proven by BC VRI + QC IEQM 5e. The plan is intentionally compact:
1–2 days per jurisdiction for a working CASFRI-shape fixture, with the CASFRI
schema being the single coherent surface so per-jurisdiction integration cost does
not compound. Where public plots + CASFRI-shape + Pothier/Savard-class yield curves
are available, the jurisdiction is a Tier-3 candidate; where licence or admin gating
limits raw vector download, the jurisdiction is Tier-3 partial or DUA-only deferred.

Two pillar preconditions:

- The BC + QC two-province LP-plus diagnostic (`planning/bc_qc_pipeline_strategy.md`
  §2, `planning/qc_two_province_incremental_memo.md` §4) proves the integration
  pattern is operational on a real QC GeoPackage (`feuille 21M`, CC-BY 4.0,
  815 MB archived in `data/raw/qc_inventory/2023-ieqm-eighth/`).
- The CASFRI v5 framework (GPL-3.0; `metadata/source-reviews/casfri_v5_schema.md`)
  is reframed in §2 below as the on-ramp schema for every subsequent jurisdiction,
  not a one-BC-vintage integration.

## 2. Synchronisation with `metadata/source-reviews/casfri_v5_schema.md` §3

The 49-row truth table at `metadata/source-reviews/casfri_v5_schema.md` §3 records
every provincial / territorial FRI snapshot CASFRI knows how to translate. The
team's openness assessment (Open / Mixed / DUA-only) is also there. The expansion
ladder below references that table by row index for traceability. **No new
openness claim is introduced here**; the plan borrows the assessment verbatim.

| Truth-table §3 row | Jurisdiction | Status (per §3) | Preferred acquisition pattern | Expected coverage |
|--------------------|--------------|------------------|-------------------------------|-------------------|
| §3.a NS | NS | Open (per-county SHP) | Direct SHP pull from `https://novascotia.ca/natr/meb/shp-meb/maps/shp/ns-forest.zip` per-county mirror | Province-wide (~5.5 M ha forest) |
| §3.a PE | PE | Open (ArcGIS Hub) | ArcGIS Hub download (~150 MB) | Province-wide (~0.3 M ha) |
| §3.a YT (5k 2007–2012) | YT | Open (GeoYukon FTP) | FTP GeoYukon pull; bulk vector download; sub-arctic context; partially analogous to NWT pattern | Yukon's forested zone (~15 M ha) |
| §3.a NL02 (Aug 2023) | NL | Open (NL GeoHub, GeoPackage) | Direct download; modern only | Province-wide (~19 M ha forest land) |
| §3.b NB | NB | Mixed (NB03/NB06 open; older admin-gated) | NB03/NB06 public via NB Open Data; older cycles via Dale Wilson NB NRRED DUA | Partial Tier-3 (open vintages only) |
| §3.b MB | MB | Mixed (Nelson River FLI 2011–13 public; MB01/MB02/MB04 industry-restricted) | Nelson River FLI direct; other cycles Manitoba Sustainable Development DUA | Partial Tier-3 (Nelson River only) |
| §3.b ON | ON | Mixed (Ontario GeoHub redistribution prohibited; ON01 DVD-release licensed) | ON FRI Term 2 extracts via signed Ontario GeoHub redistribution-clause request | Big forest area; largest *potential* national-scale contributor but attribution constrained |
| §3.c AB / SK / NT | AB / SK / NT | DUA-only (per-vintage) | Path-1: NFI-style etiquette template; Path-2: vintage-by-vintage split per `provincial_forest_inventories.md` | Defer to Phase 6 after Tier-3 evidence from above |

§3.d (the federal NFI Ground Plot path) is intentionally NOT listed above; it is
the **NFI backstop**, see `planning/nfi_status_tracker.md` §2.

## 3. Eight-jurisdiction expansion ladder

Ordered by minimum-viable path forward; estimate 1–2 days per jurisdiction for a
working CASFRI-shape fixture; cumulative ~14–18 days wall-clock calendar across
all eight jurisdictions performed sequentially.

### 3.1 Nova Scotia (`provincial_forest_inventories` NS row; CC-BY equivalent)

NS row in `metadata/source-reviews/casfri_v5_schema.md` §3.a. Per-county SHP from
the province's NR-themed open data portal; CC-BY attribution. **Acquisition pattern
identical to BC/QC** (open per-county SHP duplicated to a `data/raw/ns_inventory/`
submodule sidecar; CASFRI-shape column emission through `fresh_fibre/ns_vri_reader.py`;
12-test unit suite mirroring the 11-test BC VRI reader plus 1 fixture-loading test).
Expect lowest integration cost because the schema already speaks CASFRI columns and
the per-county SHP granularity is small.

### 3.2 Prince Edward Island (PEI GIS Data Catalogue / ArcGIS Hub)

PE row in §3.a. Approximately 150 MB provincial download; CC-BY attribution.
Similar pattern to NS: similar open licence shape, similar small inventory area.
Boreal–Acadian transition zone; useful regional map for the Atlantic ¦/Maritimes
bridging work lead out of the BC + QC span.

### 3.3 Yukon (GeoYukon Forest Resource Roads 50k WMS + bulk vector data)

YT row in §3.a. FTP GeoYukon pull implementing the 5k 2007–2012 vintage; Yukon
government open licence; sub-arctic context. Partially analogous to NWT pattern.
WMS alignment required because the source-of-truth vector data is delivered through
GeoYukon's WMS for some derivatives (see `metadata/source-reviews/provincial_transport.md`
for the existing WMS review).

### 3.4 Newfoundland and Labrador — NL02 (Aug 2023)

NL02 row in §3.a (only the **modern** NL02 vintage — older vintages are
mixed/DUA). NL GeoHub delivery as GeoPackage; download-and-flag pattern. Forest
land near 19 M ha; boreal–Acadian transition.

### 3.5 New Brunswick (Mixed)

NB row in §3.b. NB03 (2003 vintage) and NB06 (2006 vintage) both openly downloadable
via NB Open Data; **older cycles remain admin-gated via Dale Wilson NB NRRED
(Kyle Schmidt)**. Partial Tier-3 evidence on the open vintages; older cycles
require a DUA process collinear with `metadata/license-determinations/nfi-targeted-request.md`
etiquette.

### 3.6 Manitoba (Mixed)

MB row in §3.b. Nelson River FLI 2011–13 is publicly downloadable. MB01/MB02/MB04
industrially restricted (industry partner coordination required). Partial Tier-3
evidence on the Nelson River vintage only.

### 3.7 Ontario (Mixed tier with redistribution-clause limitation)

ON row in §3.b. Ontario GeoHub hosts FRI Term 2 (2018–2028) extracts but
**redistribution is prohibited** from the GeoHub mirror; ON01 (2009 DVD release)
requires a signed redistribution-clause licence. ON is the largest *potential*
national-scale contributor in terms of forest area. Tier-3 acquisition must use
**best-effort attribution**: ON cells may circulate inside parent-repo
diagnostic fixtures only when (a) the GeoHub redistribution-clause language is
resolved and (b) license counsel signs off on the redistribution envelope. **ON is
not a same-day integration candidate** even though ranked at 7 by openness alone.

### 3.8 Alberta / Saskatchewan / Northwest Territories (DUA-only; defer)

Defer to Phase 6 alongside the BC / SK / NT Tier-3 DUA campaign summarised in
`planning/bc_qc_pipeline_strategy.md` §4 (rows AB, SK, NT01–NT03, NT04). Per the
49-row truth table at §3.c, these jurisdictions depend on DUA prerequisites.
Per-vintage splits must be re-verified immediately before any DUA is opened per
the table's closing "honest limit" sentence. AB27 / AB31 / AB32 (Tolko / ALPAC
industry-restricted cycles) and BC07 (CANFOR) follow the same DUA path.

## 4. Per-jurisdiction acquisition template

Each jurisdiction's integration follows this 6-step checklist (mapped 1:1 to a
parent-repo PR through which the data submodule change is referenced):

1. **Confirm `source_url` reachable.** `curl -I $source_url` returns `HTTP/2 200`
   or equivalent. Archive the headers + connection log at
   `data/raw/<jurisdiction>_inventory/<vintage>/ACQUISITION.md` (parent-repo
   metadata file referencing the data submodule).
2. **Add `metadata/source-inventory.csv` row.** `id=new_<jurisdiction>_<vintage>`,
   `theme=forest_inventory`, `status=candidate` initially; flip to
   `accepted_subset_recommended` once Tier-3 evidence passes.
3. **Author `metadata/source-reviews/<jurisdiction>_fri.md`.** Cap at 200 lines;
   mirror the structure of `metadata/source-reviews/provincial_forest_inventories.md`
   with the openness assessment line referencing the §3 row above.
4. **Wire the CASFRI-shape reader module.**
   `fresh_fibre/<jurisdiction>_vri_reader.py` following `fresh_fibre/bc_vri_reader.py`
   pattern. Module must read the jurisdiction's raw vector (SHP / GeoPackage / GDB)
   and emit the same 30-column CASFRI-shape output the BC VRI test fixture emits.
5. **Tests in `tests/test_<jurisdiction>_vri_reader.py`.** 12 tests, mirroring the
   11-test BC VRI reader suite + 1 fixture-loading test that proves the raw payload
   is on disk before the unit suite runs.
6. **Wire CASFRI-shape row emission through `species_canonical`.** Use the
   existing `fresh_fibre/species_canonical.py` cross-jurisdiction prefix policy
   (`BC_*`, `QC_*` already in code; add `<NEW>_*` per-jurisdiction canonicals
   from the existing per-province lookup CSV). Jurisdictional-prefix preservation
   is load-bearing for traceability through to `run_provenance` rows.
7. **4 phase-matched readiness-gate updates.** For each of
   `species_composition`, `national_scaling`, `residue_yield_policy`,
   `calibrated_cost_surfaces`, update the YAML `notes` line only **after** the
   new jurisdiction's Tier-3 diagnostic fixture has passed; flip the gate's
   `status` only when the **cumulative** evidence across jurisdictions warrants,
   not jurisdiction-by-jurisdiction.

## 5. Gantt-aligned surrogate Gantt chart

A surrogate Gantt (table-form for text-readable diffability) showing the
deliberately-sequenced expansion. Day 0 = end-of-Phase-4 reference (`06629c3 + 1c43177`).

| Week (T+?) | Jurisdictions moved forward | Effort (dev-days) | Cumulative (dev-days) | Deliverable |
|------------|-----------------------------|-------------------|------------------------|-------------|
| T+0 | (already land / scaffold; ref point) | – | 0 | `planning/national_expansion_plan.md` (this doc) committed |
| T+0 days 1–2 | NS | 1–2 | 1–2 | `fresh_fibre/ns_vri_reader.py` + 12-test suite + `data/raw/ns_inventory/` sidecar |
| T+2 days 3–4 | PE | 1–2 | 3–4 | `fresh_fibre/pe_vri_reader.py` + test suite |
| T+4 days 5–6 | YT | 1–2 | 5–8 | `fresh_fibre/yt_vri_reader.py` + WMS alignment reference in source review |
| T+8 days 9–10 | NL02 | 1–2 | 9–12 | `fresh_fibre/nl_vri_reader.py` + test suite |
| T+12 days 11–12 | NB (NB03+N B06 only; older cycles deferred) | 1–2 | 13–16 | `fresh_fibre/nb_vri_reader.py` + test suite; DUA back-burner opened for older cycles |
| T+16 days 13–14 | MB (Nelson River only) | 1–2 | 17–19 | `fresh_fibre/mb_vri_reader.py` + test suite; industry-vintage coordination parked |
| T+20 days 15–16 | ON (terms-conditioned; counsel review open) | 1–2 (best case) | 20–24 | Conditional; depends on redistribution-clause resolution |
| T+24 days 17–18 | AB / SK / NT DUA campaign (DUA-only; **deferred to Phase 6**) | n/a | n/a | DUA pack engineered for vintage-by-vintage split per §3.c (Phase 6 work) |

**Cumulative to Tier-3 evidence across all eight jurisdictions: ~14–18 dev-days**
(the AB/SK/NT row is Phase 6 deferred). This estimate is consistent with the QC
mini diagnostic landing in roughly the same wall-clock budget
(`planning/qc_two_province_incremental_memo.md` §4).

## 6. Risk register

Per `planning/casfri-v5-schema-integration-plan.md` §5 risk-1: **license counsel sign-off
on the GPL-3.0 contamination question** is a prerequisite before any per-jurisdiction
adapter publishes derived cells in the parent repo. The sign-off can be deferred if
license-counsel feedback remains pending: each per-jurisdiction reader stays inside
the `fresh_fibre/<jurisdiction>_vri_reader.py` module, reads the CASFRI translation
table at run-time, and emits only CASFRI-shape rows to `data/raw/<jurisdiction>_inventory/`
+ provenance pointers in the parent repo. The default mitigation posture applies:

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|---------|------------|
| 1 | GPL-3.0 contamination on the per-jurisdiction adapter | medium | high | Reference CASFRI v5.3.1 via sibling-sidecar submodule (`data/external/casfri-casfri/`) not vendored; counsel sign-off prerequisite |
| 2 | ON redistribution-clause breach | medium | high | ON cells never published in parent repo unless clause resolved; diagnostic fixtures only on attribution-permitted vintages |
| 3 | Per-vintage DUA lapse between provinces | medium | medium | Per-jurisdiction re-verification immediately before each DUA opened (mirrors §6 of the source review's "honest limit" caveat) |
| 4 | WMS-only delivery channel masquerading as bulk-downloadable WFS (a known trap per §2 of the source review) | medium | medium | Verify `curl -I $source_url` returns the expected content-type at step 4.1 |
| 5 | Jurisdictional-prefix collision in `species_canonical` cross-jurisdiction map | low | medium | Existing prefix policy `BC_*`/`QC_*` generalised with a deterministic `<province>_<species>` lookup; regression test enforces uniqueness |

**Cross-doc bridge:** model risk register style after
`planning/casfri-v5-schema-integration-plan.md` §5; only 5 entries retained here
because the integration plan already covers the lower-effort risks.

## 7. Cross-reference

- **49-row truth table (canonical for openness assertions):**
  `metadata/source-reviews/casfri_v5_schema.md` §3 (rows cited by index in §2 above).
- **Companion rows in the source registry:**
  `metadata/source-inventory.csv` rows `provincial_forest_inventories` (parent
  row), `casfri_v5_schema` (CASFRI schema row), per-jurisdiction candidate rows
  pending per §4 step 2.
- **Companion provincial FRIs source review:**
  `metadata/source-reviews/provincial_forest_inventories.md` lines 61–68
  (2026-08-06 snapshot); this is the per-vintage split record.
- **Companion readiness-gate surface:**
  `metadata/readiness-gates.yaml` (gates `species_composition`,
  `national_scaling`, `residue_yield_policy`, `calibrated_cost_surfaces` —
  none is blocked **because** a jurisdiction above is un-integrated, but
  each gains `<jurisdiction>_<vintage>` evidence-line entries as Tier-3
  fixtures pass).
- **Phase-5 framer:**
  `planning/phase_5_closeout.md` §6 (forward-next-step candidate (a)).
- **Phase 4 foundation (BC + QC precedent):**
  `planning/bc_qc_pipeline_strategy.md` §4 (eight-jurisdiction priority table
  ordered differently — this file supersedes that ordering by minimum-viable-path);
  `planning/qc_two_province_incremental_memo.md` §4 (LP-fixture runner pattern to
  reuse per jurisdiction).
- **CASFRI integration plan:**
  `planning/casfri-v5-schema-integration-plan.md` (D2; subtype already authorised
  for BC + QC; this doc specialises the Tier-3 expansion plan without re-doing
  the integration-plan-level acceptance criteria).

---

*End of National CASFRI Tier-3 Expansion Plan. Eight jurisdictions listed by
minimum-viable-path; sequential; ~14–18 dev-days cumulative; ON contingent on
counsel; AB/SK/NT deferred to Phase 6.*
