# Phase 5 Closeout (NFI Pivot Era)

| Field | Value |
|-------|-------|
| Document | `planning/phase_5_closeout.md` |
| Phase | 5 (documentation authoring phase; closes) |
| Status | Closed 2026-08-10 |
| Owner | UBC FRESH / CLEWs-C2070 forestry data maintainers |
| Companion Phase-4 framer | `planning/bc_qc_pipeline_strategy.md` |
| Companion Phase-5 framer | `planning/phase-5-decision-memo.md` (Phase-5-to-Phase-6 authorisation memo) |
| Companion QC increment | `planning/qc_two_province_incremental_memo.md` |

---

## 1. Title + summary

Phase 5 closes with the **NFI-pivot reframing** in force. Four planning documents
were authored during this closeout window; together they define the post-Phase-5
trajectory for the BC + QC foundation that landed in commit `06629c3` plus 11
follow-on commits on the P7.1 arc. The pivot: **NFI = nice to have, not gating**.
This doc records the closeout and the forward-next-step candidates the team can
pick from.

## 2. Phase 5 task disposition

| Phase-5 task | Place | Status | Notes |
|--------------|-------|--------|-------|
| **5.1** (NFI plot DUA status) | `planning/nfi_status_tracker.md` (this cycle, File 1) | DONE | Reframed as submit-rhythm tracker; backstop role; not gating |
| **5.2** (NFI plot integration into CASFRI calibration pipeline) | (REMOVED from active scope per directive (b35); see §3 below) | NOT IN SCOPE | Forward revival only if NFI DUA approved AND team gives green light |
| **5.3** (national jurisdictional expansion plan) | `planning/national_expansion_plan.md` (this cycle, File 2) | DONE | 8-jurisdiction ladder by minimum-viable-path; cumulative ~14–18 dev-days |
| **5.4** (national bioenergy scenario simulation design) | `planning/national_bioenergy_scenarios.md` (this cycle, File 3) | DONE | REF/CP/RFS/SCE/WDA; LP overlay vector; 5-gate Tier-1 dependency |
| (this closeout note) | `planning/phase_5_closeout.md` (this cycle, File 4) | DONE | The lightweight meta-document |

Five authored items total; three are substantive forward-trajectory docs, two are
framing/closeout. Per-file line budget respected (see close-out verification
table in §5).

## 3. Pivots

**Pivot (b35):** Phase-5 task 5.2 ("NFI plot integration into CASFRI calibration
pipeline") is **REMOVED FROM ACTIVE SCOPE**. This includes the previous notion that
NFI data is a precondition for `species_composition` or `national_scaling` to flip
to `resolved`. Forward-revival conditions: (a) the NFI DUA is approved and a
materialised payload lands in the data submodule, AND (b) the UBC FRESH team
explicitly affirms that the calibration gain outweighs the implementation effort.
Neither condition is on a calendar event; both are team decisions.

**Pivot (general):** the prior framing of NFI as a readiness-gate **itself** has
been downgraded. `nfi_access` remains `blocked` in `metadata/readiness-gates.yaml`
but is now documented as a **backstop gate**, not a project gate. See
`planning/nfi_status_tracker.md` §2 for the gate-by-gate reframe.

## 4. Branch + commits referenced

The 12-commit P7.1 arc that delivers the BC + QC foundation:

```
06629c3 + db77d43 + 1c43177 + 8109b4a + d53f27c + b5c53f2
       + 0cddb66 + fa0edbc + cd1ba60 + 9c4bbf2 + 4354976 + 3ab9e45
```

Anchors:

- **`06629c3`** — Phase 4 close; national-scale readiness valuation gate-table
  fabricated for the BC + QC foundation.
- **`db77d43`, `1c43177`** — Phase 2b SAFORAH2 CSW discovery + GeoGratis catalogue
  walk that materialised `nrn_vectors` / `nrwn_vectors` as blocking-dataset
  candidates.
- **`8109b4a`, `d53f27c`, `b5c53f2`, `0cddb66`, `fa0edbc`, `cd1ba60`, `9c4bbf2`** —
  Phase 7 P7.1 cascade: source review (D1 → D2 → D3 → D4 → D5 → D6); CASFRI v5
  integration plan; QC + BC bridge.
- **`4354976`, `3ab9e45`** — Phase 4.4 quarterly increment: BC + QC two-province
  LP fixture, the actual `Optimal`-solving artefact at
  `metadata/ffcm-archive/prototype-inputs/bc-qc-mini-2prov/` (matches
  `planning/qc_two_province_incremental_memo.md` commit history).

The Phase-5 commitment foot-print (this closeout window) is documentation-only:
four planning docs under `planning/`; no production code on this branch beyond what
the BC + QC foundation already provides.

## 5. Reframed readiness-gate trajectory

Per `metadata/readiness-gates.yaml` (status as of 2026-08-09). The "NFI ≠ gate"
shift is the load-bearing change this closeout represents.

| Gate | Status (2026-08-09) | Pre-Phase-5 framing | Post-Phase-5 framing |
|------|---------------------|---------------------|----------------------|
| `species_composition` | `in_progress` | "blocked until NFI plots land" | Validates Gauthier/SCANFI; **adds evidence, doesn't gate** |
| `national_scaling` | `in_progress` | "blocked until NFI plots land" | Backstop on inter-provincial calibration; **adds evidence, doesn't gate** |
| `nfi_access` | `blocked` | "gate the project" | Backstop calibration role; **non-gating** posture |
| `residue_yield_policy` | `blocked` | "needs NFI" | Gated by **provincial residue survey** + **provincial yield curves**, NOT NFI |
| `calibrated_cost_surfaces` | `blocked` | "needs NFI for stumpage" | Gated by **NRN 2.0 + NRWN bulk vector** + **provincial forest resource roads**, NOT NFI |
| `climate_predictors` | `blocked` | not coupled to NFI | `ERA5` climate authorisation (unchanged) |
| `facility_layer` | `blocked` | not coupled to NFI | `forest_facilities_2013` update (unchanged) |
| `disturbance_calibration` | `in_progress` | not coupled to NFI | CanLaD / NFDB / NBAC continue (unchanged) |

## 6. Forward-next-step candidates

Four directions live beyond Phase 5. The team picks when ready; none is on a Phase
calendar event.

| ID | Direction | Reference doc | Effort | Blocked by |
|----|-----------|---------------|--------|------------|
| (a) | **Tier-3 jurisdictional expansion** beyond BC + QC (8 jurisdictions per `planning/national_expansion_plan.md` §3) | `planning/national_expansion_plan.md` | ~14–18 dev-days cumulative | `provincial_forest_inventories` candidate-row status; ON contingent on counsel; AB/SK/NT DUA deferred to Phase 6 |
| (b) | **National NRN/NRWn vector materialization (Phase 2b.4 / 3b.5, deferred)** | `metadata/source-reviews/nrn_vectors.md`, `metadata/source-reviews/nrwn_vectors.md` | n/a — Phase 3.10 scope | `calibrated_cost_surfaces` gate; SAFORAH2 CSW discovery already done in `1c43177` |
| (c) | **NFI DUA submission when team ready** | `planning/nfi_status_tracker.md` | placeholder fill + PI sign + send (sub-day) | UBC FRESH team decision + 3 placeholder lines filled |
| (d) | **National-scale bioenergy scenario simulation** (this closeout's File 3) | `planning/national_bioenergy_scenarios.md` | LP-overlay-vector implementation: small; gate clearing: large (5 gates) | 5-of-8 `metadata/readiness-gates.yaml` gates crossing `resolved` boundary |

These four directions are **independent**, in the sense that team bandwidth on one
does not delay another. Direction (a) is the next-most-effective use of the BC + QC
foundation the team has in hand; direction (b) is the team's standing
`calibrated_cost_surfaces` lane; direction (c) is the backstop calibration sub-task;
direction (d) is the policy-scenario deliverable.

## 7. Cross-reference

- **Orchestrator-owned plan surface (read-only; NOT edited by this Phase):**
  the project's `.opencode/plan.md` (or equivalent orchestrator plan file) — the
  Phase-5 closeout tracking row is referenced as task T5.5 / equiv. on that
  orchestrator surface; this closeout doc aligns with that row and provides the
  corresponding forwarding content.
- **Phase-4 BC + QC framer:**
  `planning/bc_qc_pipeline_strategy.md`.
- **Phase-5-to-Phase-6 authorisation framer:**
  `planning/phase-5-decision-memo.md` (Phase-5 closes; Phase-6 executes against the
  small-real-sampled diagnostic lane).
- **Phase-7 production-readiness memo (anchor for national-scale readiness):**
  `planning/phase-7-production-readiness-memo.md`.
- **Companion docs authored during this Phase-5 closeout window (bundle):**
  `planning/nfi_status_tracker.md`, `planning/national_expansion_plan.md`,
  `planning/national_bioenergy_scenarios.md`, and this file.
- **Readiness-gate authority:**
  `metadata/readiness-gates.yaml` (8 gates; table at §5 above is a working
  snapshot — the YAML itself remains the authority and is updated at the gate-flip
  moment).

---

*End of Phase 5 Closeout. NFI remains a calibration backstop; not a gate.
Three forward-trajectory docs + this closeout note close the pivot.*
