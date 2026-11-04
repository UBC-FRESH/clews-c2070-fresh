# Advisor Dossier

**Status**: ACTIVE
**Date**: 2026-08-07
**Purpose**: Persistent record of Advisor consultations, accepted advice,
caveats, and rejected/modified advice. Ensures continuity across stateless
Advisor invocations.

---

## Consultation Record: 2026-08-07

### Context

Six heuristic SCANFI v2 2025 raster windows were materialized as diagnostic
fixtures. They pass Phase 5 diagnostic acceptance checks but are not
statistically defensible as inferential evidence. The Coordinator consulted
the Advisor on the recovery architecture.

### Accepted Advice

The following advice was accepted and incorporated into the recovery plan:

1. **SCANFI is a prediction surface, not a census.**
   - The five SCANFI v2 2025 rasters (biomass, height, closure, landcover,
     age) are model predictions with known uncertainty, not ground-truth
     measurements. They require independent validation against NFI plots or
     other accepted benchmarks before inferential use.
   - **Action**: G5 uncertainty model must explicitly model SCANFI prediction
     error, not treat it as ground truth.

2. **Independent validation circularity risk.**
   - Using heuristic windows for both training and validation creates
     circularity. The six diagnostic fixtures cannot serve as independent
     validation data.
   - **Action**: G6 preregistration must specify truly independent validation
     data (e.g., NFI plots, not heuristic windows). No circular validation.

3. **Netdown/conversion/flow gaps.**
   - Netdown factors, volume conversion factors, and sustainable-yield
     calculations are not yet documented or cited. Using invented values would
     undermine the entire pipeline.
   - **Action**: G4 requires literature-sourced citations for all conversion
     factors, with explicit assumption labels where citations are unavailable.

4. **Cross-classification structure.**
   - TSA/FMU reporting units × BEC ecological zones × landbase strata
     cross-classification is needed before scale-up. This provides the
     stratification framework for representative sampling.
   - **Action**: G3 produces cross-classification table with area weights.

5. **Sufficient statistics for pilot.**
   - A single-TSA pilot (G7) requires adequate sample size with documented
     power analysis. Underpowered pilots waste effort and produce misleading
     results.
   - **Action**: G7 preregistration includes power analysis and sample size
     justification.

6. **LP sensitivity before full model.**
   - Cheap LP sensitivity reconnaissance (G2) using ±20% biomass perturbation
     can reveal decision robustness without full uncertainty propagation.
   - **Action**: G2 produces reconnaissance report labeled "not evidence."

7. **Uncertainty-propagation framing.**
   - The publishable claim is about **uncertainty propagation through the
     pipeline**, not point estimates. This is methodologically sound and
     testable without full BC census.
   - **Action**: G0 estimands focus on `U_biomass`, `U_supply`, `S_LP`,
     `R_rank` — all uncertainty/sensitivity measures.

### Caveats

The following caveats were noted but not fully resolved:

1. **SCANFI v2 methodology not yet directly verified.**
   - The SCANFI v2 prediction methodology (machine learning model, training
     data, validation protocol) has not been independently reviewed. We are
     treating it as a black-box prediction surface with unknown error
     structure. This is a known limitation.
   - **Implication**: G5 uncertainty model must include SCANFI prediction
     error as a component, even if poorly characterized.

2. **NFI access still pending.**
   - Independent NFI validation plots require license/access review. This
     blocks G6 independent validation.
   - **Implication**: G6 may need to use alternative independent data sources
     (e.g., published NFI summary statistics, not raw plots).

3. **Climate predictors not yet authorized.**
   - Gauthier climate predictors (temperature/precipitation normals) are
     pending separate authorization. This limits G5 spatial autocorrelation
     modeling.
   - **Implication**: G5 may need to use simpler spatial structure (e.g.,
     distance-based) until climate predictors are available.

### Rejected / Modified Advice

The following Advisor suggestions were considered but modified or rejected:

1. **Rejected: Ban all purposive sampling.**
   - **Original advice**: Ban purposive sampling entirely; require probability
     sampling for all inferential work.
   - **Modified**: Ban **purposive inferential sampling** (selecting windows
     to support a desired conclusion). Allow **probability sampling** and
     **systematic validation sampling** (e.g., random NFI plot selection for
     validation).
   - **Rationale**: Purposive sampling is epistemically dishonest. Probability
     and systematic sampling are methodologically sound.

2. **Modified: LP sensitivity reconnaissance scope.**
   - **Original advice**: Run full LP sensitivity analysis before any model
     building.
   - **Modified**: G2 is **cheap reconnaissance** (±20% perturbation, format
     validation only). Full sensitivity analysis comes after G5 uncertainty
     model is specified.
   - **Rationale**: Full sensitivity analysis is expensive and premature
     without uncertainty model. Reconnaissance is cheap and informative.

3. **Modified: Pilot TSA selection.**
   - **Original advice**: Select pilot TSA based on data availability.
   - **Modified**: Select pilot TSA based on **representativeness** (cross-classification
     coverage) and **independent validation data availability**.
   - **Rationale**: Data availability alone risks selection bias.
     Representativeness ensures pilot results generalize.

### Open Questions for Future Consultation

1. How to handle SCANFI prediction error when methodology is not verified?
2. What alternative independent validation data sources are available if NFI
   access is denied?
3. How to model spatial autocorrelation without climate predictors?
4. What is the minimum acceptable power for G7 pilot?

---

## Usage Notes

- This dossier is **file-based continuity**. Each Advisor invocation reads
  this file to maintain context across stateless sessions.
- Append new consultations below this record, with date and context.
- Do not delete or overwrite accepted advice, even if later consultations
  disagree. Log disagreements as new entries.
- The Coordinator is responsible for maintaining this file.