# Change Log

## 2026-06-25

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
