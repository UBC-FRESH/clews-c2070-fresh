# Phase 5 Tiny Real-Sampling Design And Fixture Skeleton

## Purpose

Phase 5 converts the Phase 4 SCANFI+Gauthier real-input gates into a concrete
tiny fixture design. The phase prepares the first public-safe real-sampling
path for the BC mini diagnostic lane, but it does not commit raw rasters,
private NFI material, large derived rasters, or production yield assumptions.

GitHub parent issue: `#19`.

Release milestone: `v0.1.0a1 Conceptual Alpha`.

Active branch: `feature/p5-tiny-real-sampling-fixture`.

Draft PR: `#25`.

## Data Boundary

The parent repo may track planning notes, extraction contracts, metadata
schemas, tiny public-safe fixture skeletons, validation expectations, and
derived audit summaries. Raw SCANFI rasters and any private or license-sensitive
inputs must remain in the `data` DataLad/git-annex submodule or ignored local
workspaces.

## Phase Tasks

- P5.1 defines the tiny BC diagnostic sampling extent and cell keys. Child
  issue: `#20`.
- P5.2 defines the SCANFI raster-window extraction and manifest contract. Child
  issue: `#21`.
- P5.3 defines the validation checks for units, nodata, area, ranges, and
  provenance. Child issue: `#22`.
- P5.4 updates gap and release-readiness surfaces for diagnostic versus
  production status. Child issue: `#23`.
- P5.5 closes the phase with a decision memo and Phase 6 authorization. Child
  issue: `#24`.

## P5.1 Sampling Extent Decision

The fixture should use the smallest BC mini extent that can exercise the
existing downstream network: one or more cells mapped to each active BC mini
supply node, with stable cell identifiers, spatial-unit keys, and DataLad path
expectations. The phase should prefer metadata-only definitions until raw
SCANFI sampling is explicitly authorized.

P5.1 implementation table:
`metadata/ffcm-archive/tiny-real-sampling-fixture-cells.csv`.

The first fixture skeleton uses six planned SCANFI raster windows copied from
the existing diagnostic proxy-cell design. This is the minimum useful design
because it covers all three BC mini spatial units and all four active
SCANFI+Gauthier downstream supply nodes: coast log, first interior log, second
interior log, and interior logging-residue candidate. The rows are explicitly
marked `real_sampling_fixture_design_not_sampled` and
`planned_not_materialized`; they define cell keys and expected DataLad paths
only, not real sampled values.

## P5.2 Extraction Contract Decision

The extraction contract should identify required SCANFI source layers,
expected raster-window paths in `data`, derived parent-repo table names,
checksum/provenance fields, unit fields, and value-policy labels. It should be
usable by a later implementation script without committing the raw raster
window itself to the parent repository.

P5.2 implementation table:
`metadata/ffcm-archive/tiny-real-sampling-extraction-contract.csv`.

The contract covers the five selected SCANFI v2 2025 layers required by the
P5.1 fixture cells: aboveground biomass, height, canopy closure, NFI
landcover, and median age. Raw source rasters are referenced only under
`data/raw/scanfi/v2/`; tiny raster-window outputs and their manifests are
planned under `data/derived/scanfi-diagnostic/tiny-bc-real-sampling/windows/`
and `data/manifests/scanfi-diagnostic/tiny-bc-real-sampling/`. The only
parent-repo output boundary is the future derived diagnostic inventory table
for the `bc-mini-scanfi-real-sampled` lane. The north extrapolation mask stays
as a missing follow-up source, not a required Phase 6 extraction input.

## P5.3 Validation Decision

Validation should cover parseability, required columns, nonnegative sampled
quantities, unit consistency, nodata policy, cell-area accounting, expected
diagnostic value ranges, and complete source/provenance references. These
checks are diagnostic acceptance gates, not production calibration gates.

P5.3 implementation table:
`metadata/ffcm-archive/tiny-real-sampling-validation-contract.csv`.

The validation contract separates checks needed before a future Phase 6
sampled fixture can feed the diagnostic lane from checks that remain production
calibration gates. Diagnostic acceptance covers schema parseability, fixture
and layer coverage, nonnegative quantities, nodata accounting, broad
SCANFI-value sanity ranges, landcover logic, CRS/resolution consistency,
checksums, provenance, and policy labels. Residue yield, cost calibration,
climate predictors, NFI access, and benchmark validation remain explicitly
marked as production gates and do not block the tiny diagnostic fixture design.

## P5.4 Gap And Release Decision

The gap register should distinguish four states: implemented diagnostic
prototype, real-sampling fixture planned, tiny real-sampled lane implemented,
and production-ready national input. Phase 5 can move SCANFI real sampling from
open concept to planned fixture, but it must leave production gates open until
access, validation, and calibration decisions are complete.

## P5.5 Close-Out Decision

Phase 5 should close with a short decision memo that says whether Phase 6 is
authorized to materialize a tiny SCANFI-derived fixture, which paths may be
materialized, which public-safe parent-repo outputs may be committed, and which
inputs remain blocked or opt-in.
