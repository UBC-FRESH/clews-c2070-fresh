# NFI Status Tracker — Calibration Backstop, Not Gating

| Field | Value |
|-------|-------|
| Document | `planning/nfi_status_tracker.md` |
| Replaces | Phase-5 task 5.1 (NFI plot-into-CASFRI calibration pipeline) per directive (b35); doc keeps the **submit-rhythm** surface but reframes NFI as a low-priority backstop |
| Companion package | `metadata/license-determinations/nfi-targeted-request.md` (PRE-SUBMISSION READY); `metadata/license-determinations/nfi-variable-crosswalk.md`; `metadata/license-determinations/nfi-access.yaml` |
| Status | Submit-when-you-can; **NOT blocking** BC + QC functional pipeline or national Tier-3 expansion |
| Phase 4 closure | `06629c3` closed Phase 4 without NFI access; NFI is demonstrably **not** a precondition for the BC + QC LP-plus diagnostic |
| Owner | UBC FRESH / CLEWs-C2070 forestry data maintainers |

---

## 1. Status header

The NFI DUA request at `metadata/license-determinations/nfi-targeted-request.md` is
**PRE-SUBMISSION READY**. Three placeholder lines require real-name team fill before
the document is forwarded to the federal NFI office:

| Placeholder line (file `metadata/license-determinations/nfi-targeted-request.md`) | Field |
|---|---|
| line 9 | `Contact: [Email], [Phone]` (PI-side correspondence address) |
| line 182 | `Technical Lead: [Name], [Title], [Email], [Phone]` |
| line 183 | `Data Manager: [Name], [Title], [Email], [Phone]` |

Everything else in the 201-line request is filled with concrete project identification
(NRCan EIP-NES-147 funded; PI Dr. Gregory Paradis; non-correspondence lead Dr. Taco
Niet; SCANFI v2 pixel-linkage requirement specified; 15-ecozone stratification; ~9,450
plot-estimate volume; CSV/Parquet delivery, GPL/MIT output, raw plot data never
redistributed) — verifiable in §1–§11 of the request file.

**Posture:** submit-when-you-can. No deadline. No SLA from the NFI office (typical
acknowledgement turnaround is 4–6 weeks on prior team correspondence, but the NFI
office disclaims any timing commitment). The team fills the three placeholders, the
PI signs, and submission happens at whatever cadence the UBC FRESH side chooses.

**What this doc is NOT.** This is not a project plan to integrate NFI plots. The
plot-into-CASFRI integration previously planned under Phase 5 task 5.2 is **removed
from active scope** per directive (b35); see `planning/phase_5_closeout.md` §3.
This tracker only keeps the submission-rhythm surface alive.

## 2. What NFI does and doesn't gate

NFI plot data is a **calibration backstop**, not a readiness-gate. The previous
framing (NFI = precondition for species_composition resolved; NFI = precondition
for national_scaling resolved; NFI = blocking by itself) is retired. Per-gate posture
below; status values are taken from `metadata/readiness-gates.yaml` as of 2026-08-09:

| Gate | Pre-NFI reframing (retired) | New posture (this doc) | Status (2026-08-09) |
|------|----------------------------|------------------------|---------------------|
| `species_composition` | "blocked until NFI plots land" | Validates Gauthier/SCANFI species aggregation; **adds evidence, doesn't gate** | `in_progress` on BC + QC Tier-3 (CASFRI mini paths) |
| `national_scaling` | "blocked until NFI plots land" | Backstop for inter-provincial aggregate calibration; **adds evidence, doesn't gate** | `in_progress` on BC + QC two-province LP fixture |
| `residue_yield_policy` | "needs NFI to unlock per-ha residue factors" | NFI supports residue factor calibration but residue policy is gated by **provincial residue survey** + **provincial yield curves** (see §6 below) | `blocked` on **provincial data**, NOT on NFI |
| `calibrated_cost_surfaces` | "needs NFI stumpage validation" | NFI Provincial Reports give ranges; per-ha stumpage surfaces remain **NRN/NRWn vector** + **provincial forest resource roads** | `blocked` on **vector ingest** (Phase 3.10), NOT on NFI |
| `nfi_access` | (this is the gate itself) | Remains `blocked` but its role downgraded from "gate the project" to "backstop the calibration" | `blocked`, **documented non-gating** |
| `biomass calibration` (implicit) | "NFI = precondition for any biomass number" | NFI supports biomass cross-check; the BC + QC LP fixture already emits biomass-quantity rows without NFI | backstop role; not listed as a YAML gate |

**Bottom line:** none of the four YAML-blocked gates (`calibrated_cost_surfaces`,
`climate_predictors`, `residue_yield_policy`, `facility_layer`) is blocked **because
NFI is missing**. Each gate is blocked by a separate, independently materialisable
dataset (NRN/NRWn vectors, ERA5, provincial residue surveys, forest_facilities_2013).
NFI is "nice to have" validation, not "must have" gating.

## 3. Submit rhythm

This section captures the cadence the team uses when the three placeholder lines get
filled and an actual submission happens. Cadence is deliberately lightweight.

| Trigger | Action |
|---------|--------|
| Dr. Paradis + UBC team clears the three placeholders (lines 9, 182, 183) | PI signs the request; UBC FRESH scans + version-controls at `metadata/license-determinations/nfi-targeted-request.md` |
| UBC FRESH main submitter (TBD) forwards the signed PDF | E-mail to `nfi.datarequests@canada.ca` (per request form at `https://nfi.nfis.org/en/datarequestform`) |
| NFI office acknowledges | typical 4–6 weeks, but no SLA; team logs acknowledgement date in `CHANGE_LOG.md` |
| NFI reviews substantive request | typical 6–18 months end-to-end per historical Canadian federal DUA pattern; not blocking any active Phase |
| Approval lands | DUA signed; data delivered per §7 of `metadata/license-determinations/nfi-targeted-request.md` |

**No deadline means:** the team does not need to schedule `nfi_access` gate flips in
the Phase 6 / Phase 7 / Phase 9 sequencing. NFI is "submit when ready, integrate when
delivered" — a backstop, not a calendar event.

**Contact strategy note.** The pre-submission-ready package already names the PI
(Dr. Gregory Paradis, UBC Forest Resources Management) and non-correspondence lead
(Dr. Taco Niet, SFU). The three placeholder lines are operational contacts only:
one research-side PI office contact, one technical implementer, one data manager.
The team decides whether to fill all three or to consolidate. There is no
prerequisite for naming all three before submission; one contact is sufficient to
make the submission land. Multiple contacts reduce the chance of an unanswered
clarification request from the NFI office.

## 4. When NFI would slot in

When (and only when) NFI approval lands AND the team actually materialises plot data
into a local-analyst workspace, integration proceeds along **two forward-flagged
adapter hooks** that do not yet exist:

```
NFI plot CSV / Parquet                  fresh_fibre adapter surface              CASFRI backstop usage
  (delivered payloads)                    (forward-flagged, NOT yet coded)         (where it attaches)
──────────────────────────────────────  ───────────────────────────────────────  ────────────────────────────────────
row-per-tree + row-per-plot metadata  →  fresh_fibre.species_canonical           →  species_composition upgrade-to-resolved
                                               .NFI_PLOT_ADAPTER                       (BACKSTOP role; does not gate)
DBH / height / age / STATUS / CROWN  →  fresh_fibre.gauthier_yield              →  yield-curve regression cross-check
                                               .NFI_CALIBRATION_HOOK (optional)         (BACKSTOP only)
plot-area expansion factor / plot_id  →  fresh_fibre.casfri_qc_reader            →  residue_yield_policy upgrade-to-resolved
                                               .NFI_CALIBRATION_HOOK                      (BACKSTOP role; still gated by provincial data)
```

Both `fresh_fibre.species_canonical.NFI_PLOT_ADAPTER` and
`fresh_fibre.casfri_qc_reader.NFI_CALIBRATION_HOOK` are **forward-flagged**, **NOT in
scope now**, and **NOT scheduled**. They are sketched here only so that the path
from delivered plot data to backstop calibration is mechanically visible. Building
them is a separate work item triggered by (a) NFI approval coming back and (b) the
team deciding the calibration gain outweighs the implementation effort.

**Critical caveat:** even with this adapter pair in place, `species_composition` is
expected to flip `resolved` (not just `in_progress`) only **after** SCANFI v2
`spsCC_*` species layers become authorised (~123 GB pending per
`metadata/readiness-gates.yaml` gate `species_composition.notes`). NFI plot data adds
**validation evidence**; it does not substitute for the SCANFI wall-to-wall
species strata. The same applies to `national_scaling` and `residue_yield_policy`.

## 5. Cross-reference

- **Submission package (read-only; do NOT edit by orchestrator or by this task):**
  `metadata/license-determinations/nfi-targeted-request.md` (201 lines; the canonical
  federal DUA package).
- **Variable crosswalk (technical reference for the 9,450-plot payload):**
  `metadata/license-determinations/nfi-variable-crosswalk.md` — NFI variable names →
  Gauthier consumer roles.
- **Access control posture:**
  `metadata/license-determinations/nfi-access.yaml` — internal-use-only; raw plot
  data never redistributed per the request §8.
- **Phase-5 framer (companion to this doc):**
  `planning/phase_5_closeout.md` §3 ("5.2 removed from scope").
- **Readiness-gate surface:**
  `metadata/readiness-gates.yaml` — five blocked/in_progress gates, none of which
  is gated on NFI standalone.
- **BC + QC foundation (proves NFI is not a precondition):**
  `planning/bc_qc_pipeline_strategy.md` (Phase 4 closure, 156 lines) +
  `planning/qc_two_province_incremental_memo.md` (Phase 4.4 BC + QC diagnostic memo).

## 6. Open verification items

| # | Item | Owner | Cadence |
|---|------|-------|---------|
| 1 | Three placeholder lines filled in `metadata/license-determinations/nfi-targeted-request.md` (lines 9, 182, 183) with real names, titles, e-mail, phone | UBC FRESH PI-side | At submit time; **not now** |
| 2 | PI-side review that the request's project identification block (lines 5–11) still aligns with the NRCan 2025 FPP-2 proposal record (see revision log entry 2026-08-09 in the request package) | Dr. Paradis / Taco Niet | At submit time; last verified 2026-08-09 |
| 3 | NFI office acknowledgement logged in `CHANGE_LOG.md` with date and reviewer-name when it lands | UBC FRESH at receipt | At receipt |
| 4 | Periodic re-confirmation that `metadata/license-determinations/nfi-access.yaml` matches the freshest published NFIS / NFI Variable Dictionary (typical cadence: per delivery receipt) | UBC FRESH data maintainer | When payload arrives |
| 5 | If/when `fresh_fibre.species_canonical.NFI_PLOT_ADAPTER` is built, ensure it preserves the `BC_*`/`QC_*` jurisdictional-prefix policy that already exists in the module's CASFRI path (see `metadata/source-crosswalks/qc_casfri_v5_schema.md` §3.1) | UBC FRESH adapter maintainer | When implementation begins (not now) |

**DUA-protocol timing honesty:** typical NFI office acknowledgement is **4–6 weeks**
based on prior Canadian federal research DUA patterns; substantive review often
takes **6–18 months**. These are team-observed norms, not committed SLAs. The NFI
office disclaims timing. Plan accordingly: do not gate project sequencing on
expecting NFI delivery within any specific window.

---

*End of NFI Status Tracker. NFI remains a calibration backstop; not a project gate.*
