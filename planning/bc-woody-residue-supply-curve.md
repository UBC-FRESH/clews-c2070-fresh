# BC woody-residue supply curve for OSeCAN B1/B2

Status: implementation note for P7.17 (child issue #31 under phase issue #26).

## Purpose

Produce a documented, all-BC woody-residue supply curve for the C2070/OSeCAN
biomass seam, split into two FRESH-side provisional zones:

- `B1`: South Coast / Lower Mainland (Georgia Depression + Southern Coastal
  Mountains).
- `B2`: BC Interior & North (the remaining BC ecoprovinces, including the BC
  portions of the Montane Cordillera, Boreal Cordillera, Boreal Plains, and
  Taiga Plains).

The seam currently uses one national supply technology `SUPBCW` with a 50 PJ/yr
cap and a 4 $/GJ variable cost. The curve replaces that placeholder with
`SUPBCWB1`/`SUPBCWB2` rows and a national `SUPBCW` comparison rollup.

## Decisions

### Zone crosswalk

C2070 has no published B1/B2 boundary. The crosswalk is a FRESH-side proposal
built from the BC ecoprovince labels already present in
`data/derived/national/assortments/bc_area_weights/`. It follows the
recommended default in the task.

Each TSA is mapped to its THLB-dominant zone, but harvest is apportioned across
the TSA's ecoprovinces by THLB area share. Seven TSAs are material splits
(Bulkley, Cascadia, Fraser, GBR North, Kispiox, Nass, Soo).

### Harvest basis

Primary: **2018 AAC, all-BC including Prince George**.

- Parsed 36-TSA AAC: 44,399,801 m3/y (task-stated total; omits Prince George).
- Prince George 2018 AAC: 8,375,000 m3/y (workbook `Region 2`).
- All-BC AAC: **52,774,801 m3/y**.

Why AAC rather than the 2018 apportionment: the C2070 horizon is 2021–2070, and
the 2018 apportionment is a tenure snapshot that includes MPB-salvage and
non-replaceable volumes above the AAC. Carrying it flat for 50 years would
overstate long-run residue. The apportionment comparison is still emitted:

- Parsed 36-TSA apportionment: 38,726,439 m3/y.
- All-BC apportionment: 58,424,023 m3/y (adds the 2018 class sums for Dawson
  Creek, Fort Nelson, Fort St. John, Okanagan, and Prince George, which the
  generic parser misses).

### Residue ratio

Primary: **area-weighted national batch**, resolved by ecoprovince.

- BC all-species ratio: 0.080872 ODT/m3.
- Ecoprovince range: 0.035732 (Georgia Depression) to 0.364484 (Southern Boreal
  Cordillera).
- Stage B mill-residue ratio: 0.259553 ODT/m3 (reported, not used: mill-residue
  basis; applying it to harvest volume would double count the log input).

### Energy and cost

- ODT is oven-dry; LHV is 18.5 GJ/ODT dry; 1 PJ = 1,000,000 GJ.
- Cost is a Stage B-derived mill-residue delivered-cost proxy: least-cost BIO
  road link per sawmill source, CAD/m3 green / 0.45 ODT/m3, plus the BIO
  operating cost (20.890031 CAD/ODT). It excludes stump-to-roadside
  collection/grinding, so it is a lower bound for harvest residue.
- No CAD-to-$2020 deflator or exchange rate is available; the values are
  carried as CAD 2020-equivalent and flagged.

## Key results

| Zone | Annual log (m3/y) | Residue (ODT/y) | Residue (PJ/y) | Variable cost ($/GJ) |
|---|---:|---:|---:|---:|
| B1 | 9,182,584.381 | 333,082.530 | 6.162027 | 2.994574 |
| B2 | 43,592,216.619 | 4,703,138.247 | 87.008058 | 1.706656 |
| CAN | 52,774,801.000 | 5,036,220.777 | 93.170084 | 1.791835 |

Comparison to the 50 PJ / 4 $/GJ placeholder: the gross taper-residue cap is
1.86× the placeholder, and the national cost is 0.45× the placeholder. The
placeholder is conservative on both dimensions for this definition. A 60%
roadside recovery sensitivity gives 55.902 PJ, close to the 50 PJ placeholder.

## Outputs

Data (in the `data` submodule):

- `data/derived/national/clews_supply/bc_woody_residue/`
  - `bc_zone_crosswalk.csv`
  - `bc_tsa_zone_crosswalk.csv`
  - `bc_annual_residue.csv`
  - `SUPBCWB1_activity_limits.csv`, `SUPBCWB1_variable_cost.csv`
  - `SUPBCWB2_activity_limits.csv`, `SUPBCWB2_variable_cost.csv`
  - `audit/` derivation tables
  - `comparison/` national rollup and alternative scenarios
  - `BC_CLEWS_SUPPLY_REPORT.md`
  - `manifest.json`

Code (parent):

- `workflows/national/clews/bc_residue_supply.py`
- `workflows/national/clews/tests/test_bc_residue_supply.py`
- `workflows/national/clews/README.md`

## Reproduction

```bash
.venv/bin/python -m workflows.national.clews.bc_residue_supply
.venv/bin/python -m pytest -q workflows/national/clews/tests
```

## Open caveats

- B1/B2 is provisional; no C2070 boundary exists.
- THLB-weighted TSA split assumes uniform harvest per THLB hectare within a TSA.
- Batch residue is taper residue, not full AGB-based harvest slash.
- The cap is gross residue; no recovery factor is applied.
- The cost is a mill-residue proxy and a lower bound for harvest residue.
- The profile is static 2018; no future AAC, MPB, climate, wildfire, or
  disturbance path is modelled.
- The national `SUPBCW` rollup must not be loaded alongside `SUPBCWB1`/`B2`.
