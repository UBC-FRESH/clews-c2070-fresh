# CFS tree-level biomass equations

These two CSV files are the unmodified tree-level allometric-equation
coefficient tables distributed by the Canadian Forest Service (CFS) Biomass
and Nutrients Calculator.

- Source page: <https://apps-scf-cfs.rncan.gc.ca/calc/en/biomass-calculator>
- DBH + height table:
  <https://apps-scf-cfs.rncan.gc.ca/calc/static/files/data/Tree-level%20allometric%20equations%20dbh-height.csv>
- DBH-only table:
  <https://apps-scf-cfs.rncan.gc.ca/calc/static/files/data/Tree-level%20allometric%20equations%20dbh.csv>
- Retrieved: 2026-11-23 (ISO date)
- Source licence: Open Government Licence - Canada. The parent Open Canada
  record is
  <https://open.canada.ca/data/en/dataset/fbad665e-8ac9-4635-9f84-e4fd53a6253c>.
- References printed in the tables:
  - Lambert, M.-C., Ung, C.-H., Raulier, F. 2005. Canadian national tree
    aboveground biomass equations. *Canadian Journal of Forest Research*
    35: 1996-2018. <https://doi.org/10.1139/x05-112>
  - Ung, C.-H., Bernier, P., Guo, X.-J. 2008. Canadian national biomass
    equations: new parameter estimates that include British Columbia data.
    *Canadian Journal of Forest Research* 38: 1123-1132.
    <https://doi.org/10.1139/X07-224>

## Equation form

The DBH + height table uses:

```
Biomass_kg = a * DBH_cm^b * Height_m^c
```

The DBH-only table uses:

```
Biomass_kg = a * DBH_cm^b
```

Both tables report oven-dry mass in kilograms for four components: bark,
branches, foliage, and stem wood. This pilot consumes only the `Branches` and
`Foliage` rows with the DBH + height form. No coefficient is transcribed or
re-fitted here.

## Licences of related open implementations

`tesera/biomasscan` (MIT) and `CFSForestTools` (LGPL-3.0) implement the same
national equations. No source code from either package is copied into this
repository; the tables above are used directly, so their licences do not
propagate to this code. The equations themselves are published scientific
relationships; the CFS distribution is OGL-Canada.

## SHA-256

```
f902e8bb0236d49e27458cb430523c51b440e196bb4a5bd5469e3e36a4485d49  tree_level_allometric_equations_dbh.csv
020675c63cb6ef5dbdb20fd76fc0b9180679b3ad7d0d64aee5ee57af9ea7175c  tree_level_allometric_equations_dbh_height.csv
```
