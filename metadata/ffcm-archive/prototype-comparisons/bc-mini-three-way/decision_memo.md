# BC Mini Variant Comparison

This memo compares the four currently runnable BC mini supply paths:

- `synthetic_bc_mini`: stable synthetic supply fixture.
- `ws3_smoke`: real local ws3 TSA24 smoke run mapped through diagnostic
  crosswalk rows.
- `scanfi_diagnostic`: SCANFI-provenance diagnostic inventory cells with
  synthetic placeholder yield rules.
- `scanfi_gauthier_diagnostic`: SCANFI-provenance diagnostic inventory cells
  with Gauthier et al. (2015) diagnostic method-reproduction log yield rules.

All four variants solve through the same downstream LP machinery. Current
solver statuses are `Optimal`,
`Optimal`,
`Optimal`, and
`Optimal`.

The SCANFI branch is now useful as an inventory-provenance bridge: it carries
verified SCANFI layer references through a ws3-shaped harvest-output boundary and
into the downstream LP. The Gauthier branch replaces log yield placeholders with
method-reproduction diagnostic quantities, but it should not be interpreted as
calibrated supply. Residue yield and costs remain diagnostic assumptions.

Decision implication: keep the SCANFI+Gauthier path as the preferred diagnostic
inventory/yield branch for log supply, but keep production yield calibration,
residue policy, and validation as open gates.
