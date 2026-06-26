# BC Mini-Network Prototype Fixture

This directory contains tiny synthetic tables for testing the FRESH
forest-fibre soft-link contract. The records are diagnostic only: they are not
production estimates, are not copied from the FFCM archive, and should not be
used as calibrated BC supply-chain data.

The fixture exercises the five-layer network-flow pattern recovered from the
ws3-compatible notebook seed:

- `src`: ws3-shaped supply offers.
- `dsp`: dispatch nodes associated with supply offers.
- `cdt`: commodity pool nodes.
- `prc`: processing technologies.
- `snk`: market/export sink nodes.

Generated graph outputs are written to `generated/` by `fresh_fibre.graph`.

LP audit and solution outputs are written to `generated/` by `fresh_fibre.lp`.
Validate the full generated bundle with `fresh_fibre.validation`.

Compatibility CLIs remain available under `scripts/ffcm_archive/`.
