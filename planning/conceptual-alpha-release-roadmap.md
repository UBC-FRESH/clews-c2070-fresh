# Conceptual Alpha Release Roadmap

## Purpose

The first CLEWS-C2070-FRESH repository release should be a conceptual and
diagnostic alpha, tagged `v0.1.0a1`. The release should checkpoint the current
FFCM rebuild path, runnable BC mini supply-chain prototypes,
SCANFI+Gauthier diagnostic supply logic, and FreshForge/DataLad
materialization scaffolding without claiming production data readiness.

## Release Milestone

- GitHub milestone: `v0.1.0a1 Conceptual Alpha`
- Release tag: `v0.1.0a1`
- Package version target: `0.1.0a1`
- Announcement surface: GitHub Discussions, enabled during release
  preparation if still disabled

## Phase Sequence

Phase 5 turns the Phase 4 real-input gates into a tiny public-safe sampling
fixture design. Phase 6 uses that design to add the first tiny real-sampled
diagnostic supply lane. Phase 7 reconnects source triage and production
readiness gates to the model path. Phase 8 packages the result as the
conceptual alpha release.

The release remains diagnostic unless all production blockers are resolved:
NFI access and reuse, climate predictor selection, coniferous/deciduous
aggregation, residue-yield policy, calibrated cost/price assumptions, and
validation against accepted reference summaries.

Phase 5 closeout authorizes Phase 6 only for the tiny real-sampled diagnostic
fixture described in `planning/phase-5-decision-memo.md` and
`metadata/ffcm-archive/phase-5-phase-6-authorization.csv`. This authorization
does not change the production-readiness boundary.

## Release Acceptance

- Existing BC mini variants still solve and compare successfully.
- Any real-sampled diagnostic lane added before release is public-safe,
  provenance-tracked, and clearly labelled diagnostic.
- FreshForge materialization workflows validate and plan without requiring
  broad `data/raw` materialization.
- README and planning notes state what works, what remains diagnostic, and
  what blocks production readiness.
- All release-bound phase issues are reconciled against `ROADMAP.md`,
  `CHANGE_LOG.md`, PR bodies, and commits.
- `main` is clean before tagging, the GitHub release exists, and the
  announcement discussion links back to the release.

## Current Readiness

Machine-readable readiness surface:
`metadata/ffcm-archive/conceptual-alpha-readiness.csv`.

Current conceptual-alpha readiness is diagnostic only. The BC mini LP
prototype, ws3 smoke bridge, SCANFI diagnostic lane, and SCANFI+Gauthier
diagnostic lane are implemented diagnostic components. The Phase 5 tiny
real-sampling fixture cells, extraction contract, and validation contract are
designed but not materialized. The tiny real-sampled lane remains a Phase 6
implementation target and is not yet available. The national production model
remains blocked by open residue-yield, cost-calibration, climate-predictor,
NFI-access, benchmark-validation, and national-scaling gates.
