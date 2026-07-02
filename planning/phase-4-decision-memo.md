# Phase 4 Decision Memo

## Decision

Phase 4 establishes the real-input materialization gate for the
SCANFI+Gauthier diagnostic lane, but it does not yet authorize production yield
or cost assumptions. The next implementation phase can proceed to build a tiny
real sampled fixture only for explicitly public-safe metadata, manifests, and
approved sampled derivatives.

## Ready For Implementation

- The tiny real-input contract is defined in
  `metadata/ffcm-archive/real-diagnostic-input-contract.csv`.
- The FreshForge/DataLad workflow for SCANFI diagnostic metadata and manifests
  is defined in `examples/freshforge/scanfi_diagnostic_materialization_workflow.yaml`.
- Gauthier predictor access gates are separated in
  `metadata/ffcm-archive/gauthier-predictor-access-gates.csv`.
- Residue, cost, market-price, and validation gates are separated in
  `metadata/ffcm-archive/residue-cost-validation-policy-gates.csv`.

## Still Blocked For Production

- NFI reference pixels and any private plot/pixel data require access and reuse
  approval before materialization.
- Measured-volume scaling remains unresolved unless a SCANFI biomass proxy
  policy is explicitly accepted.
- Climate predictor source and normal period remain open.
- Coniferous/deciduous aggregation rules remain open until selected SCANFI
  species layers or an accepted substitute are chosen.
- Logging-residue yield and all cost/price assumptions remain diagnostic gates.
- NFI standard reports remain validation-only until license/re-hosting review
  clears snapshot archiving.

## Recommended Next Phase

Start a tiny real-sampling implementation phase that materializes only
diagnostic metadata/manifests first, then samples a minimal SCANFI window or
public-safe derived fixture under the `data` submodule. The parent repo should
track only manifests, extraction code, tiny public-safe derived tables, and
validation summaries.
