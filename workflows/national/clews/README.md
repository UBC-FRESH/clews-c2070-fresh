# CLEWS supply-curve builders

This package holds the FRESH-side builders that turn derived forest-fibre
artifacts into CLEWs/OSeCAN parameter rows.

## `bc_residue_supply`

`bc_residue_supply.py` builds the all-BC woody-residue supply curve for the
provisional OSeCAN zones B1 (South Coast / Lower Mainland) and B2 (BC Interior
& North).

Run from the repository root:

```bash
.venv/bin/python -m workflows.national.clews.bc_residue_supply
.venv/bin/python -m workflows.national.clews.bc_residue_supply --dry-run
.venv/bin/python -m pytest -q workflows/national/clews/tests
```

Outputs go to `data/derived/national/clews_supply/bc_woody_residue/` by default.
The report is `BC_CLEWS_SUPPLY_REPORT.md` in that directory.

### Design rules

- B1/B2 is a **FRESH-side provisional** crosswalk. C2070 has no published
  boundary.
- Harvest basis is the 2018 AAC, all-BC including Prince George. The parsed
  36-TSA artifact is preserved as a comparison.
- Residue ratio is the BC area-weighted national batch, resolved by
  ecoprovince. The BC all-species aggregate and the Stage B mill-residue ratio
  are reported as comparisons.
- Energy is 18.5 GJ/ODT dry; no moisture credit.
- The annual profile is flat 2021–2070 (`static_aac`); no time path is
  invented.
- Costs are Stage B-derived delivered-cost proxies, not a harvest-slash
  collection cost. They are a lower bound and are flagged as such.
- Every input is read from a named derived artifact; missing or unexpected
  values fail loudly with `BcResidueSupplyError`.

### Output contract

The OSeCAN files use the otoole CSV index
`REGION,TECHNOLOGY,YEAR,VALUE` with `REGION=CAN` and 50 rows for 2021–2070.
`SUPBCWB1` applies to B1 and `SUPBCWB2` to B2. The national `SUPBCW` rows are
under `comparison/` and must not be loaded alongside the zone technologies.
