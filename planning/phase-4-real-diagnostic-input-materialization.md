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

## Phase Close-Out Criteria

- The tiny real-input contract is explicit and machine-readable.
- FreshForge/DataLad materialization paths are documented and dry-run checked.
- Gauthier predictor source/access decisions are recorded.
- Residue, cost, and validation policies are separated from log-yield method
  reproduction.
- A decision memo states whether the next implementation phase can sample real
  rasters or remains blocked by access/licensing.
