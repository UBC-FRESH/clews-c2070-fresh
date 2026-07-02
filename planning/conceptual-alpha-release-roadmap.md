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
