# BC branch and foliage allometry for measured-tree residue

**Status:** pilot model component, 2026-11-23.  `model_estimate=true`,
`observed=false`, `policy_grade=false`, `national_claim=false`.

This note documents the branch and foliage component added to the
`national_bucking_pilot_v2` BC measured-tree residue ledger.  The stem taper
ledger already covers stump, top, and unmerchantable stem sections.  It cannot
represent live branches or foliage, so those components are estimated
separately and kept separate in the data model.

## Source, licence, and domain

| Item | Value |
|---|---|
| Coefficient table | CFS Biomass and Nutrients Calculator, tree-level DBH + height table |
| Equations | Lambert, Ung and Raulier (2005); Ung, Bernier and Guo (2008) |
| Licence | Open Government Licence - Canada (CFS distribution); equations are published scientific relationships |
| Source URL | <https://apps-scf-cfs.rncan.gc.ca/calc/en/biomass-calculator> |
| Vendored table | `workflows/national/bucking/rules/cfs_biomass/tree_level_allometric_equations_dbh_height.csv` |
| Table SHA-256 | `020675c63cb6ef5dbdb20fd76fc0b9180679b3ad7d0d64aee5ee57af9ea7175c` |
| Species map | `workflows/national/bucking/rules/bc_biomass_allometry_map.csv` |
| Code | `workflows/national/bucking/bc_biomass_allometry.py` |

Equation form:

```
Biomass_kg = a * DBH_cm^b * Height_m^c
ODT = Biomass_kg / 1000
```

The equations are applied only to live, measured BC FAIB PSP stems.  The
application guardrail is 1.3-100 cm DBH and 1.3-60 m height.  The observed BC
list is narrower: 2.4-61.3 cm DBH and 2.4-40.4 m height.  The guardrail is a
project safety bound, not a claim that the published equations were fitted over
the full range.  Values outside it fail closed.

### Species mapping

| BC code | Common name | CFS equation species | Species-specific | Proxy |
|---|---|---|---|---|
| AC | black cottonwood | Red alder and Black cottonwood | no | no |
| AT | trembling aspen | Trembling aspen | yes | no |
| BL | subalpine fir | Subalpine fir | yes | no |
| CW | western redcedar | Western red cedar | yes | no |
| EP | paper birch | White birch | yes | no |
| FD | Douglas-fir | Douglas fir | yes | no |
| HM | mountain hemlock | Conifers | no | yes |
| HW | western hemlock | Western hemlock | yes | no |
| PL | lodgepole pine | Lodgepole pine | yes | no |
| SB | black spruce | Black spruce | yes | no |
| SE | Engelmann spruce | Engelmann spruce | yes | no |
| SW | white spruce | White spruce | yes | no |

Mountain hemlock has one stem in the measured list and no species-specific CFS
row; the published conifer group equation is used and flagged
`branch_foliage_proxy=true`.  Black cottonwood uses the published combined red
alder + black cottonwood equation and is flagged non-species-specific.

## Ledger fields

The following fields are added to `residue_ledger.csv`,
`bc_tipsy_residue.csv`, and the BC-only `bc_residue_allometry.csv`:

| Field | Meaning |
|---|---|
| `stem_residue_odt` | Stem taper residue, identical to `residue_odt` |
| `branch_odt` | Live branch oven-dry tonnes |
| `foliage_odt` | Live foliage oven-dry tonnes |
| `branch_foliage_odt` | `branch_odt + foliage_odt` |
| `total_residue_odt` | Gross `stem_residue_odt + branch_foliage_odt` |
| `recovery_factor` | Existing pilot default `0.60` |
| `recoverable_residue_odt` | `total_residue_odt * recovery_factor` |
| `branch_foliage_estimated` | `true` only for BC measured stems |
| `branch_foliage_evidence` | `published_table` for BC; `not_estimated` otherwise |
| `branch_foliage_equation_id` | Branch equation ID + foliage equation ID |
| `branch_foliage_equation_species` | CFS equation species |
| `branch_foliage_reference` | `Lambert 2005` or `Ung 2008` |
| `branch_foliage_species_specific` | Species-level equation flag |
| `branch_foliage_proxy` | Group or proxy equation flag |
| `branch_foliage_source`, `branch_foliage_source_licence` | Provenance |
| `height_evidence` | Source tree-list height basis (`field_measured` or `dbh_estimated`) in `bc_residue_allometry.csv` |
| `residue_component_scope` | `stem_plus_branch_foliage` or `stem_only_branch_foliage_not_estimated` |
| `*_expanded` | Per-stem value times the PHF expansion factor |

Non-BC synthetic rows are left empty for branch/foliage/total values.  An
unestimated component is never written as zero biomass.

## Gross versus recoverable residue

Both quantities are reported; neither is silently selected:

- `total_residue_odt` is the gross stem + branch + foliage mass.
- `recoverable_residue_odt` applies the existing pilot recovery factor `0.60`
  from `validate_tsa24_btc.DEFAULT_RECOVERY_FACTOR`.
- The `0.60` factor is a pilot assumption and is **not** calibrated to the
  facility coefficient.

## Results

BC measured list, 347 stems, expansion-weighted:

| Basis | Stem ODT | Branch+foliage ODT | Gross total ODT | Gross ODT/m3 | Recoverable ODT/m3 (0.60) |
|---|---:|---:|---:|---:|---:|
| National pilot sorts | 6,700.357 | 19,987.862 | 26,688.219 | 0.161622 | 0.096973 |
| BC interior TIPSY sorts | 13,922.853 | 19,987.862 | 33,910.715 | 0.232703 | 0.139622 |

The branch and foliage component is identical across sort rule sets because it
is a tree-level allometry result; the stem residue differs because the two
sort sets recover different log volumes.

### Facility and prior comparison

| Reference | ODT/m3 |
|---|---:|
| Facility `harvest_residue_gross_odt / harvest_m3` | 0.0762255 |
| Facility recoverable (`harvest_residue_recoverable_odt / harvest_m3`) | 0.0542448 |
| Prior v2 national-sort stem-only residue | 0.0405769 |

| Model variant | ODT/m3 | Gap vs facility gross | Gap vs prior stem-only |
|---|---:|---:|---:|
| National sorts, stem only (prior) | 0.040577 | -46.77% | 0.00% |
| National sorts, gross total | 0.161622 | +112.03% | +298.31% |
| National sorts, recoverable total (0.60) | 0.096973 | +27.22% | +138.99% |
| TIPSY sorts, stem only | 0.095542 | +25.34% | +135.46% |
| TIPSY sorts, gross total | 0.232703 | +205.28% | +473.49% |
| TIPSY sorts, recoverable total (0.60) | 0.139622 | +83.17% | +244.09% |

Interpretation: the facility `0.0762` coefficient is a gross harvest-residue
stream.  The model gross total includes all live branch and foliage biomass and
is expected to be higher.  The 0.60-recoverable total is closer to the facility
gross coefficient but still about 27% higher on the national-sort basis.  The
TIPSY basis is not directly comparable because its sort rules recover more
sawlog volume and leave more stem residue.

These are screening comparisons, not calibration.  Harvest residue and facility
process residue are different streams.

## Reproduction and tests

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v2 \
  --out-dir data/derived/national/bucking/national_bucking_pilot_v2
.venv/bin/python -m pytest tests/test_bc_biomass_allometry.py \
  tests/test_national_bucking_pilot_v2.py -q
```

The tests cover equation values, all-species positive values, group fallback,
domain guards, ledger conservation, gross/recoverable separation, evidence and
label flags, summary gaps, manifest hashes, and recomputation from the committed
measured tree list.

## Limitations

- Branch and foliage are model estimates from published national equations, not
  measured slash at the TSA24 plots.
- Some heights are `dbh_estimated` in the source tree list; branch and foliage
  estimates inherit that height basis.  The height evidence is retained in the
  tree list, not upgraded by this component.
- The equations predict live biomass; dead branches, cones, and fruits are
  excluded by the published definitions.
- Mountain hemlock uses a conifer group proxy; black cottonwood uses a combined
  species group equation.
- The 0.60 recovery factor is not calibrated to the facility coefficient.
- No national, operational, policy-grade, AAC, investment, UI, or public
  OSeCAN claim is made.
