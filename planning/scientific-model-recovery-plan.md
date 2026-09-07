# Scientific Model Recovery Plan

**Status**: APPROVED FOR G0/G1 ONLY — developer approved 2026-08-08.
**G2–G8 are NOT approved.** No computational work beyond G0/G1 may proceed.
**Date**: 2026-08-07 (approved 2026-08-08)
**Governing issue**: Heuristic SCANFI windows rejected as inferential evidence
**Supervisor gate**: No heavy raster/LP work occurs beyond the approved G0/G1 scope.
**Scope**: G0 (claim/estimands memo) and G1 (quarantine/relabel ontology) only.
No heavy computation, no LP solves, no scale-up.

---

## 1. Problem Statement

Six heuristic SCANFI v2 2025 raster windows were materialized as diagnostic
fixtures in `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`. They
cover the six BC mini spatial units (coast_wet_forest, coast_second_growth,
interior_dry_forest, interior_cold_forest, interior_disturbance_mix,
interior_residue_candidate) and pass the Phase 6 diagnostic acceptance checks
(`phase-6-validation-memo.md`).

However, these windows are **heuristic sampling points**, not a statistically
defensible census or inference surface. Using them as inferential evidence for
BC-scale supply, yield, or LP decisions would be methodologically unsound. They
may remain only as **software smoke fixtures** — proving the pipeline runs end
to end, not supporting population-level claims.

## 2. Quarantined Artifacts

The following artifacts are quarantined from inferential use. They may be used
only for software integration testing, pipeline smoke checks, and format
validation.

| Artifact | Path | Quarantine Reason |
|----------|------|-------------------|
| `tiny_bc_real_sample_coast_wet_001` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_coast_wet_001.csv` | Heuristic window, not census |
| `tiny_bc_real_sample_coast_second_002` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_coast_second_002.csv` | Heuristic window, not census |
| `tiny_bc_real_sample_interior_dry_001` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_interior_dry_001.csv` | Heuristic window, not census |
| `tiny_bc_real_sample_interior_cold_002` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_interior_cold_002.csv` | Heuristic window, not census |
| `tiny_bc_real_sample_interior_salvage_001` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_interior_salvage_001.csv` | Heuristic window, not census |
| `tiny_bc_real_sample_interior_residue_001` | `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/tiny_bc_real_sample_interior_residue_001.csv` | Heuristic window, not census |
| `diagnostic_inventory_cells.csv` | `metadata/ffcm-archive/prototype-inputs/bc-mini-scanfi-real-sampled/` | Real SCANFI predictions with heuristic spatial support; quarantined from inference |
| `phase-6-validation-memo.md` | `planning/phase-6-validation-memo.md` | Diagnostic-only claims |

**Label convention**: All six fixture cells retain their existing IDs.
`value_policy_status_labels` is a new column added to each manifest CSV with
value `SOFTWARE_FIXTURE_NOT_EVIDENCE`. The parent
`diagnostic_inventory_cells.csv` `value_policy` and `sampling_status` fields
are updated to `quarantined`. Production status remains **BLOCKED_OPEN** for
all calibration gates.

**Label-timing resolution**: The plan originally referenced a single
`value_policy_status_labels` column. The repository convention uses two
separate columns (`value_policy`, `sampling_status`) in the parent inventory
and fixture-cells contract. For the six manifest CSVs, which lacked this
column, we add `value_policy_status_labels` as a new trailing column with the
value `SOFTWARE_FIXTURE_NOT_EVIDENCE`. For the parent
`diagnostic_inventory_cells.csv`, we update the existing `value_policy` and
`sampling_status` columns from `real_sampled` to `quarantined`.

**Timing**: These quarantine labels are applied **after** this developer
approval (2026-08-08) and **before** any further gate work (G2–G8). They do
not authorize any downstream computation; they only mark the artifacts as
non-inferential fixtures.

## 3. Proposed Claim and Estimands

### Candidate Claim (approved for G0; novelty review still pending)

> **Quantify propagation of uncertainty in SCANFI predictions, landbase
> eligibility, conversion factors, sustainable flow, and costs into BC LP
> allocations, objectives, and scenario ranking.**

This is a **candidate** methodological claim. G0 approval is recorded below;
the claim remains pending novelty review against published
uncertainty-propagation literature in forest economics and LP-based supply
chain optimization.

The claim concerns **how uncertainty flows through the pipeline**, not point
estimates of BC-scale biomass or supply. It is testable without a full BC
census.

**Status**: Candidate. G0 is approved; the claim is not accepted as a research
contribution until novelty review is complete.

### Estimands (G0)

Target quantities are defined with units and spatial support. Benchmarks may
include BC VRI, independent PSPs, published TSR/AAC, harvest billing/scale
data, or spatially held-out data proven not used in SCANFI training.

| Estimand | Symbol | Definition | Units / Spatial Support | Benchmark Data |
|----------|--------|------------|------------------------|----------------|
| SCANFI prediction error | `ε_SCANFI` | Difference between SCANFI v2 2025 prediction and independent ground truth | Mg ha⁻¹; pixel-level | BC VRI, independent PSPs, spatially held-out data (proven not in SCANFI training) |
| Landbase eligibility uncertainty | `U_land` | Fraction of THLB area whose eligibility classification is sensitive to input perturbations | Dimensionless fraction; TSA/TMUs | BC VRI landcover, BEC maps (authoritative source/version/CRS/vintage) |
| Conversion factor uncertainty | `U_conv` | Range of biomass→merchantable volume conversion factors from literature | Dimensionless ratio; per species/stock class | Published conversion studies, harvest billing data |
| Sustainable-flow uncertainty | `U_flow` | Propagated uncertainty in annual allowable cut / sustainable yield | m³ yr⁻¹; TSA-level | Published TSR/AAC, harvest billing/scale data |
| LP allocation sensitivity | `S_LP` | Fraction of LP-optimal assignments that flip under perturbed inputs | Dimensionless fraction; scenario-level | BC LP model with perturbed inputs |
| Scenario ranking robustness | `R_rank` | Kendall's τ between base and perturbed scenario rankings | Dimensionless; [-1, 1] | Multiple LP solves |

**NFI circularity warning**: NFI plots **may be circular** as a benchmark
because SCANFI's training provenance has not yet been verified. If SCANFI was
trained on or near NFI plots, using NFI for validation would be circular.
NFI independence must be established before NFI is used as a benchmark.
Until provenance is verified, NFI is **not** assumed independent.

### Hard Prohibitions (G0)

- **No estimand without a defined measurement procedure.** Every estimand
  above must have a stated data source, units, spatial support, and
  computation.
- **No invented netdown.** Netdown factors must come from published literature
  or explicitly labeled as assumptions with stated provenance.
- **No independent-pixel CIs from heuristic windows.** Confidence intervals
  require truly independent validation data.
- **No post-hoc tolerance adjustments.** Tolerance thresholds must be
  preregistered before testing.
- **No LP before conversion contract.** LP variants require a documented
  conversion factor contract before solving.
- **No full-BC scale-up before pilot passes.** Provincial claims require pilot
  success.
- **No purposive inferential selection of validation windows.** Selection of
  validation data must follow probability or systematic sampling protocols,
  not purposive selection to support a desired conclusion.

## 4. Gates G0–G8

**Ordering constraint**: G0 and G1 must be approved by the supervisor before
any heavy computational work begins. G2 (cheap LP sensitivity reconnaissance)
comes after G0/G1 and before landbase precision work. G7 (one-TSA pilot) is
the first substantive computation. G8 (BC scale-up) is conditional on G7
passing.

### G0 — Paper-Only Claim / Estimands Memo

**Artifact**: This document, Section 3 (claim candidate + estimand table).
**Nature**: Paper-only. No code, no data manipulation, no LP solves.
**Approval**: Developer approval of claim candidate and estimand definitions
recorded 2026-08-08.
**Stop criterion**: No estimand proceeds without a defined measurement
procedure, units, spatial support, and data source.

### G1 — Quarantine / Relabel Ontology

**Artifact**: Updated `value_policy_status_labels` on all six fixture cells
marking them as `SOFTWARE_FIXTURE_NOT_EVIDENCE` (not `production`).
Schema/format validation checks on all quarantine artifacts.
**Approval**: Developer approval recorded 2026-08-08; quarantine labels and
schema checks are complete.
**Stop criterion**: Any cell labeled `production` without passing G2–G8.

### G2 — Cheap LP Sensitivity Reconnaissance

**Artifact**: LP sensitivity report showing how scenario rankings shift under
perturbation, using current diagnostic fixtures as *format validators only*.
**Scope**: Literature- and benchmark-supported plausible ranges for biomass
perturbation (not arbitrary ±20%). Ranges must be justified by published
conversion studies, harvest billing data, or comparable literature.
**Approval**: Supervisor reviews sensitivity report; no production claims made.
**Stop criterion**: Report must be explicitly labeled "reconnaissance, not
evidence." No LP solve uses heuristic windows as inferential input. This is
**not inferential evidence**.

### G3 — TSA/FMU Reporting Units Crossed with Strata

**Artifact**: Cross-classification table of TSA/FMU reporting units × BEC
ecological zones × landcover × eligible landbase, with area weights.
**Contract**: Authoritative source, version, CRS, and vintage for each layer.
**Approval**: Ecological and landcover classifications verified against
published authoritative maps.
**Stop criterion**: Cross-classification must use official reporting boundaries
and authoritative source metadata, not heuristic windows.

### G4 — Citable THLB / Netdown Contract

**Artifact**: Literature-sourced THLB (timber harvestable land base) and
netdown factor citations with per-exclusion areas documented.
**Contract**: Each factor must have a citable source with stated provenance,
or be explicitly labeled as assumption. Per-exclusion areas must be
quantified.
**Stop criterion**: **Invented netdown is a hard stop.** No factor may be
used in LP without a citation or explicit assumption label with provenance.

### G5 — Measurement / Conversion / Error Contract

**Artifact**: Documented contracts for:
- SCANFI prediction error characterization
- Spatial autocorrelation structure
- Biomass → merchantable volume conversion
- Stock → sustainable flow conversion
**Approval**: All contracts preregistered before fitting. Independent
validation data identified and provenance verified.
**Stop criterion**: No circular validation (training and testing on same
heuristic windows). Spatial autocorrelation must be explicitly modeled, not
ignored. **No independent-pixel CIs from heuristic windows.**

### G6 — Preregistered Independent Validation Protocol

**Artifact**: Preregistration document specifying:
- Independent validation data source (BC VRI, independent PSPs, published
  TSR/AAC, harvest billing/scale data, or spatially held-out data proven not
  used in SCANFI training)
- Validation metrics and tolerance thresholds (preregistered, not post-hoc)
- Acceptance criteria for pilot success
**Approval**: Supervisor signs off on preregistration before any validation
testing.
**Stop criterion**: **No benchmark proven independent = hard stop.**
Validation on heuristic windows alone is not independent. Circularity is a
hard stop.

### G7 — One-TSA Pilot

**Artifact**: Pilot study in a single TSA with:
- Adequate sample size (power analysis documented)
- Independent validation data (provenance verified)
- Passed G6 acceptance criteria
- Explicit uncertainty bounds on all estimates
**Approval**: Pilot must pass G6 before any scale-up.
**Stop criterion**: No pilot passes without independent validation data and
preregistered acceptance criteria.

### G8 — BC Scale-Up (Conditional on G7)

**Artifact**: Provincial-scale uncertainty propagation results, only if G7
pilot passes.
**Approval**: Supervisor approval required before scale-up.
**Stop criterion**: If G7 pilot fails, BC scale-up is aborted. No partial
scale-up.

**No heavy work before supervisor approves G0 and required contracts.**
G0 and G1 are paper-only. G2 is cheap reconnaissance. G3–G5 are contract
documentation. G6 is preregistration. G7 is the first substantive computation.
All computational work requires explicit supervisor gate approval at each
stage.

## 5. Hard Stops (Absolute)

The following are **never** permitted, regardless of downstream benefits:

1. **No estimand without a defined measurement procedure**, units, and spatial
   support.
2. **No purposive inferential selection of validation windows.** Use probability
   or systematic sampling.
3. **No independent benchmark without independent data.** Heuristic windows
   cannot serve as validation data. **No benchmark proven independent = hard
   stop.**
4. **No invented netdown.** All netdown factors must be cited or explicitly
   labeled as assumptions with provenance. Per-exclusion areas must be
   documented.
5. **No independent-pixel CIs from heuristic windows.** Confidence intervals
   require truly independent validation data.
6. **No post-hoc tolerances.** All tolerance thresholds must be preregistered.
7. **No LP before conversion contract.** Documented conversion factors required
   before any LP solve.
8. **No full-BC before pilot.** Provincial claims require pilot success.
9. **No heavy computational work before supervisor approves G0 and required
   contracts.**

## 6. Immediate Next Bounded Action

**Action**: Completed. Developer approved the G0 claim/estimands memo and G1
quarantine ontology on 2026-08-08.

**Evidence**: This plan records the approval date, the six manifest labels,
the parent inventory status fields, and the validation results below.

**Next step after approval**: Completed. The six diagnostic fixture manifests
now carry `SOFTWARE_FIXTURE_NOT_EVIDENCE`, and the parent inventory carries
`value_policy=quarantined` and `sampling_status=quarantined`. These labels are
post-approval markings and do not authorize further gate work (G2–G8).

**Time box**: Completed in one developer turn.

## 6b. Explicit Gate Status

| Gate | Status | Notes |
|------|--------|-------|
| G0 | APPROVED | Developer approved 2026-08-08 |
| G1 | COMPLETED | Labels applied post-approval |
| G2 | NOT APPROVED | No LP sensitivity work permitted |
| G3 | NOT APPROVED | No cross-classification work permitted |
| G4 | NOT APPROVED | No netdown contract work permitted |
| G5 | NOT APPROVED | No measurement contracts permitted |
| G6 | NOT APPROVED | No preregistration permitted |
| G7 | NOT APPROVED | No pilot study permitted |
| G8 | NOT APPROVED | No scale-up permitted |

**No computational work beyond G0/G1 may proceed without explicit developer
approval for each subsequent gate.**

---

## 6a. G0/G1 Execution Record (Developer Approved 2026-08-08)

**Developer approval**: Explicit on 2026-08-08. Six heuristic SCANFI samples
are software fixtures, not inferential evidence.

**G0 status**: APPROVED. Claim candidate and estimand table accepted as written.
Paper-only. No code, no data manipulation, no LP solves.

**G1 status**: COMPLETED. All six manifest CSVs updated with
`value_policy_status_labels=SOFTWARE_FIXTURE_NOT_EVIDENCE`. Parent
`diagnostic_inventory_cells.csv` updated with
`value_policy=quarantined` and `sampling_status=quarantined` for all six rows.
Schema/format validation passed.

**Scope boundary**: G0 and G1 only. No heavy computation, no LP solves,
no scale-up, no G2–G8 work performed.

## 7. Explicit Statement: No Heavy Work Before Approval

**No heavy raster processing, LP solving, or uncertainty model fitting occurs
beyond the approved G0/G1 scope without explicit approval for a later gate.**
After G0/G1 approval, the only work permitted before a later gate is:

- Reading and documenting existing artifacts (this plan).
- Recording and validating the completed quarantine labels (G1).
- Literature review for G4 (netdown/THLB citations).

All computational work (LP solves, uncertainty propagation, model fitting)
requires explicit supervisor gate approval at each stage.

---

## Appendix: Relationship to Existing Planning

| Existing File | Relationship |
|--------------|--------------|
| `phase-6-scanfi-acquisition-plan.md` | Acquisition is complete; this plan addresses inferential use |
| `phase-6-validation-memo.md` | Diagnostic acceptance checks pass; production gates remain BLOCKED_OPEN |
| `phase-5-tiny-real-sampling-fixture.md` | Fixture design is sound; inferential use is the new concern |
| `ffcm-prototype-decision-memo.md` | Prototype scope unchanged; recovery plan adds inferential rigor |