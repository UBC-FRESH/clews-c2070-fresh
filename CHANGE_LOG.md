# Change Log

## 2026-06-25

- Added a dedicated FFCM archive reverse-engineering roadmap phase, planning
  brief, and lightweight metadata indexes for the transferred local archive:
  full file manifest, directory summary, curated document index, FCM evidence
  queue, and architecture crosswalk.
- Added a repeatable FCM configuration parser and generated normalized metadata
  tables for FMU supply, facilities, processing lines/flows, routes,
  transporters, commodities, markets, imports, and transload costs.
- Added a repeatable LP forensics summarizer and generated first-pass summaries
  for small report LP examples plus large basecase log and secondary
  log/residue allocation models.
- Added a repeatable FCM runtime dependency inventory and flagged CPLEX,
  lp_solve, database drivers, and GIS/visualization libraries as implementation
  evidence rather than FRESH runtime dependencies.
- Added an implementation-boundary matrix separating FFCM schemas and
  formulation evidence to preserve/adapt from engines, solvers, visualization
  runtime, database drivers, and license-sensitive workbooks to replace or
  avoid.
- Added an initial FRESH replacement architecture note, ws3-to-LP soft-link
  interface contract, and data-source disposition table for FFCM-derived
  prototype seeds versus production rebuild requirements.
- Scoped the first feasibility prototype as a BC diagnostic mini-network using
  synthetic or clearly labelled diagnostic ws3-shaped supply records feeding
  the downstream LP model.
- Downloaded the likely surviving gist notebook seed for the ws3-compatible
  network-flow model, extracted notebook code text/markdown/cell metadata, and
  documented its relationship to CIRRELT-2018-24 section 2.1.
- Added a rebuild plan, function inventory, and schema crosswalk for refactoring
  the recovered notebook's five-layer network-flow model into a clean FRESH
  module.
- Added a synthetic BC mini-network fixture and repeatable solver-independent
  graph builder that emits typed `src`, `dsp`, `cdt`, `prc`, and `snk` node/arc
  tables for the first FRESH soft-link prototype.
- Added a PuLP/CBC BC mini-network LP prototype, generated variable,
  constraint, objective, `solution_flow`, and `solution_summary` tables, and a
  repeatable validation script for bounds/pathway checks.
- Completed first-pass National Road Network triage: inventoried all 13
  province/territory GeoPackage ZIP packages and all 13 shapefile ZIP packages,
  recorded HTTP package sizes and release validity/version notes, accepted NRN
  as a coarse national haul-distance baseline, and flagged jurisdiction-specific
  forest/resource road layers as mandatory follow-up before operational access
  modelling.

## 2026-06-24

- Bootstrapped the CLEWs-C2070 FRESH parent repository with operating notes,
  README, roadmap, data repository runbook, Tyler onboarding brief, and initial
  metadata inventory templates.
- Created and linked the `UBC-FRESH/clews-c2070-fresh-data` DataLad repository
  as the parent repo's `data` submodule, configured the `arbutus-s3` git-annex
  special remote, uploaded a tiny annexed smoke artifact, and verified retrieval
  from a clean clone.
- Added the first national source-discovery triage queue, verified Open
  Government Licence/resource metadata for the highest-priority national layers
  where available, and opened GitHub issue `#1` to govern P1.2 source triage.
- Added `pre_canlad_v2` as a high-priority disturbance candidate after the user
  flagged the recent CanLaD v2 release, and updated the CanLaD triage notes to
  distinguish the 1985-2024 `v1.1` package from the separate pre-1985 `v2`
  companion dataset.
- Completed first-pass SCANFI v2 source triage: inventoried the 420-file
  public directory, recorded the approximate 312 GB package size, generated a
  lightweight file manifest, and recommended a subset-first DataLad mirroring
  strategy.
- Completed first-pass NFI standard reports triage: parsed public report links,
  recorded 270 CSV summary tables across four reporting geographies, and flagged
  the source for license review because NFI notices do not use Open Government
  Licence language.
- Completed first-pass CanLaD and Pre-CanLaD v2 triage: flagged CanLaD for
  access resolution because README-named raster payloads were not publicly
  reachable, and accepted Pre-CanLaD v2 as a manageable companion disturbance
  dataset pending PI approval to mirror.
