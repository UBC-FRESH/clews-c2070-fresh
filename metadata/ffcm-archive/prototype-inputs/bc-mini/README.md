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

The upstream diagnostic handoff starts from `ws3_harvest_output.csv`, a tiny
synthetic table shaped as normalized ws3 harvest output. It is converted to the
public `supply_offer` contract by `fresh_fibre.ws3_export`; the compatibility
CLI is `scripts/ffcm_archive/export_ws3_supply_offer.py`. The generated audit
copy is `generated/supply_offer_from_ws3.csv`.

Generated graph outputs are written to `generated/` by `fresh_fibre.graph`.

LP audit and solution outputs are written to `generated/` by `fresh_fibre.lp`.
Validate the full generated bundle with `fresh_fibre.validation`.
Typed CSV-row adapters live in `fresh_fibre.schema`; the public fixture tables
remain plain CSV.
Unit and dimensional checks live in `fresh_fibre.units` and can be run with
`python3 -m fresh_fibre.units --include-generated`.
Unit definitions and candidate conversions live in
`metadata/ffcm-archive/unit-registry.csv` and
`metadata/ffcm-archive/unit-conversion-registry.csv`.
LP reporting metadata is sourced from `scenario.csv`, `run_provenance.csv`, and
`network_node.csv`; generated `solution_summary.csv` includes scenario,
provenance, solver, objective, cost, revenue, and commodity-total rows.
Soft-link contract coverage is checked by `fresh_fibre.contract`, which writes
`generated/contract_validation.csv`.
Regression tests for the fixture live in `tests/test_bc_mini_prototype.py` and
can be run with `python3 -m unittest discover -s tests -v`.

Compatibility CLIs remain available under `scripts/ffcm_archive/`.
