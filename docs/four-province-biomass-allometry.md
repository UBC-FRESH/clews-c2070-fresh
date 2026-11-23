# Four-province branch and foliage allometry

Status: pilot model estimate. `observed=false`, `policy_grade=false`,
`national_claim=false`. No UI or official OSeCAN surface is changed.

## Scope

`four_province_bucking_pilot_v0` measures 591 stems: BC 347, QC 85, NB 106 and
YT 53. The stem taper ledger is extended with live branch and live foliage
biomass for every measured stem, so harvest residue can be reported as:

```
total_residue = stem_residue + branch + foliage
```

Branch and foliage dry mass is never added to stem taper volume. It has no
measured volume, so the facility chain's m3 feedstock routing remains the stem
residue; products and process residue are unchanged.

## Equations and mapping

Coefficients are the unmodified CFS Biomass and Nutrients Calculator tree-level
DBH + height table (vendored under `rules/cfs_biomass/`, SHA-256 in that
directory's README). The equation is:

```
Biomass_kg = a * DBH_cm^b * Height_m^c
```

References: Lambert, Ung and Raulier (2005); Ung, Bernier and Guo (2008).
Source licence: Open Government Licence - Canada.

`rules/four_province_biomass_allometry_map.csv` maps each measured species code
and NFI code to one CFS equation. Exact NFI/species matches are used first; a
missing or ambiguous mapping halts the build. BC rows repeat
`bc_biomass_allometry_map.csv` and the tests cross-check the coefficient
equations. Documented proxies:

| Province | Measured code | NFI | CFS equation | Label |
|---|---|---|---|---|
| BC | HM mountain hemlock | HM | Conifers | `proxy=true`; no species equation |
| QC/NB | BOP birch | BETU.ALL | White birch | `proxy=true`; Betula.ALL aggregate |
| QC/NB | BOP birch | BETU.PAP | White birch | species-specific |
| QC/NB | PET poplar | POPU.TRE / POPU.BAL | Trembling aspen / Balsam poplar | NFI authorises exact species |
| YT | S spruce | PICE.MAR | Black spruce | NFI identifies black spruce |

All other QC/NB/YT codes resolve to published species equations: white spruce,
black spruce, red spruce, balsam fir, eastern white cedar, trembling aspen,
balsam poplar, subalpine fir, tamarack larch and lodgepole pine. Observed DBH
2.4-99.1 cm and height 2.4-40.4 m are inside the 1.3-100 cm and 1.3-60 m
application guardrail.

## Recovery scenarios

No single calibrated recovery factor is adopted. Gross total residue is
reported with recoverable values at 0.35, 0.60 and 0.75. The 0.60 value is the
existing reference scenario only. Province rates (ODT per recovered m3):

| Province | Stem | Branch+foliage | Gross total | Rec. 0.35 | Rec. 0.60 | Rec. 0.75 |
|---|---:|---:|---:|---:|---:|---:|
| BC | 0.040577 | 0.121045 | 0.161622 | 0.056568 | 0.096973 | 0.121216 |
| QC | 0.059410 | 0.158097 | 0.217507 | 0.076127 | 0.130504 | 0.163130 |
| NB | 0.030341 | 0.157085 | 0.187426 | 0.065599 | 0.112456 | 0.140570 |
| YT | 0.123046 | 0.146642 | 0.269689 | 0.094391 | 0.161813 | 0.202267 |

## Artifacts

- `data/derived/national/bucking/four_province_bucking_pilot_v0/`
  - `residue_ledger.csv` carries per-tree components, gross total and all three
    recoverable scenario values.
  - `allometry_coverage.csv` records species/NFI equation mapping, proxy labels,
    observed ranges and the equation domain.
  - `residue_recovery_scenarios.csv` gives province gross and scenario rates.
  - `coefficient_comparison.csv` carries stem, branch/foliage and total residue
    coefficients with the three recovery scenarios.
- `data/derived/national/facilities/national_product_chain_v3/` replaces the
  four-province stem residue with total residue; products equal v2.
- `national_facility_chain_scenarios_v5`, `facility_residue_supply_v5`,
  `national_residue_curve_v5`, `forestry_osecan_handoff_v6` and
  `forestry_osecan_scenarios_v5` propagate the same total residue through the
  100/119/150 Mm3 cases and the OSeCAN zone handoff.

## Caveats

- Branch and foliage are model estimates from published equations, not
  measured component weights.
- The allometry equations describe live trees. The measured tree lists are
  live standing stems.
- The pilot is four provinces, not a national coefficient set.
- Harvest residue and facility process residue remain separate streams.
- OSeCAN tier positions shift because harvest gross PJ rises; the official
  OSeCAN tree is read-only and hash-checked.
