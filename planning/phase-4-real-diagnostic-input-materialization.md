# Phase 4 Real Diagnostic SCANFI+Gauthier Input Materialization

## Purpose

Phase 4 moves the SCANFI+Gauthier BC mini diagnostic lane from synthetic
method-reproduction fixtures toward a tiny, provenance-tracked real-input
materialization path. The phase keeps raw/heavy payloads in the `data` DataLad
submodule, keeps parent-repo artifacts lightweight, and uses FreshForge workflow
records as the reproducibility surface.

## Data Boundary

The parent repo may track planning notes, source reviews, manifests, tiny
metadata fixtures, code, tests, and compact generated CSV audits. Raw SCANFI
rasters, NFI reference pixels/plots, climate rasters, large derived rasters,
and private access-request material must remain in ignored `tmp/` or in the
`data` submodule.

## Phase Tasks

- P4.1 defines the minimum real diagnostic input contract.
- P4.2 adds the FreshForge/DataLad materialization plan for the tiny SCANFI
  fixture.
- P4.3 resolves Gauthier predictor/access gates for the diagnostic fixture.
- P4.4 defines residue, cost, and validation policy gates.
- P4.5 closes the phase with a decision memo and PR hygiene.

## P4.1 Contract Decision

The first real diagnostic fixture should be deliberately tiny: a handful of BC
mini cells sufficient to exercise SCANFI raster sampling, Gauthier predictor
assembly, and downstream LP handoff. The fixture should replace synthetic cell
values only after each row in
`metadata/ffcm-archive/real-diagnostic-input-contract.csv` has an accepted
source, access state, license position, and DataLad materialization target.

The current decision is to treat SCANFI v2 2025 as the first inventory source,
Gauthier et al. (2015) as the log-yield method path, and NFI standard reports
as validation summaries only until license/re-hosting policy is resolved.
Residue yield, cost calibration, climate predictor source, and NFI reference
pixel access remain separate gates.

## P4.2 Materialization Decision

The first Phase 4 materialization workflow targets only lightweight DataLad
paths:

- `data/metadata/scanfi-diagnostic`
- `data/manifests/scanfi-diagnostic`

The workflow is defined by
`examples/freshforge/scanfi_diagnostic_materialization_workflow.yaml` and uses
`examples/freshforge/scanfi_diagnostic_materialization_overlay.yaml`. It is
intended to validate and plan the collaborator materialization path before any
raw SCANFI raster payloads or raster windows are requested.

## P4.3 Predictor Access Decision

`metadata/ffcm-archive/gauthier-predictor-access-gates.csv` records the current
access and substitution state for each Gauthier predictor. The phase can use
SCANFI age and BC mini spatial units for the tiny diagnostic fixture, and it can
continue using the published Gauthier coefficients and maturity threshold.

The phase must not promote NFI reference pixels, measured-volume scaling,
climate predictors, species composition, or vegetated-fraction proxies to
production status until their access/license position and substitution policy
are explicit. For now, those rows remain open gates or parent-repo metadata-only
decisions.

## P4.4 Residue, Cost, And Validation Decision

`metadata/ffcm-archive/residue-cost-validation-policy-gates.csv` records the
remaining non-log-yield policy gates. The current decision is conservative:
logging-residue yield, mill-residue conversion provenance, log supply costs,
roadside costs, delivered-cost proxies, transport costs, and market prices are
not production assumptions. They remain diagnostic placeholders or open gates
until source, year, currency, unit basis, and validation policy are recorded.

Validation is split into three lanes: NFI standard reports as license-pending
validation summaries, SCANFI internal/range checks once real raster sampling is
implemented, and LP solution sanity checks for compact diagnostic scenarios.

## Phase Close-Out Criteria

- The tiny real-input contract is explicit and machine-readable.
- FreshForge/DataLad materialization paths are documented and dry-run checked.
- Gauthier predictor source/access decisions are recorded.
- Residue, cost, and validation policies are separated from log-yield method
  reproduction.
- A decision memo states whether the next implementation phase can sample real
  rasters or remains blocked by access/licensing.

Closeout memo: `planning/phase-4-decision-memo.md`.
