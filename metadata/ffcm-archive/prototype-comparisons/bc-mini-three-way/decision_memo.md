# BC Mini Three-Way Variant Comparison

This memo compares the three currently runnable BC mini supply paths:

- `synthetic_bc_mini`: stable synthetic supply fixture.
- `ws3_smoke`: real local ws3 TSA24 smoke run mapped through diagnostic
  crosswalk rows.
- `scanfi_diagnostic`: SCANFI-provenance diagnostic inventory cells with
  synthetic placeholder yield rules.

All three variants solve through the same downstream LP machinery. Current
solver statuses are `Optimal`,
`Optimal`, and
`Optimal`.

The SCANFI branch is now useful as an inventory-provenance bridge: it carries
verified SCANFI layer references through a ws3-shaped harvest-output boundary and
into the downstream LP. It should not be interpreted as calibrated supply. The
yield rules in `diagnostic_yield_rules.csv` are placeholders, and the costs are
diagnostic assumptions.

Decision implication: keep the SCANFI path as the preferred diagnostic
inventory branch, but make the next gate a yield-policy decision rather than a
solver or network-mechanics decision.

The current primary yield-policy gate is the Gauthier et al. (2015)
merchantable-volume method reproduction path documented in
`planning/ws3-yield-policy-decision-gate.md`. The diagnostic Gauthier fixture is
a method spike only; it does not yet replace the placeholder SCANFI diagnostic
yield rules.
