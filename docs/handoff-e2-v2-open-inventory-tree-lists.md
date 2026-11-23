# E2 v2 open-inventory-informed tree lists

Status: E2b screening artifact, `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`. No request, DUA, restricted data,
UI change or official OSeCAN change is involved.

## Scope

E2 v2 replaces the single SCANFI-plus-donor-geometry fallback for the nine
non-measured jurisdictions with the best open provincial evidence that actually
exists. It keeps the E2 v1 contract: 36 representative stems per province,
1,000 stems/ha expansion, published donor tapers, national pilot dimension
sorts, and CFS branch/foliage allometry.

Artifacts:

- `data/derived/national/inventory/provincial_open_inventory_distributions_v1/`
  — small, committed distribution tables derived from the open layers.
- `data/derived/national/bucking/open_data_imputation_v2/` — nine-province tree
  list, bucked logs, residue ledger, coefficients, v1→v2 comparison and
  distribution labels.

Code:

- `workflows/national/inventory/provincial_open_inventory_distributions.py`
- `workflows/national/bucking/open_data_imputation_v2.py`
- `tests/test_open_data_imputation_v2.py`

## Province sources and fields

| Province | Source | Fields used | Evidence used |
|---|---|---|---|
| AB | AVI Crown full FGDB | SP1..SP5, SP1_PER..SP5_PER, HEIGHT, ORIGIN, PHOTO_YR, Shape_Area | species composition, height, age; DBH inferred |
| SK | FRI Forest Stands 500-stand sample | CSG, HEIGHT, BA, QMD, MSTEMS, GBVOL, GMVOL8, HECTARES | group composition, height, QMD, volume |
| MB | FRI 1970s full FGDB | SPECIES composition string, AGE_YRPHOTO, AREA_HA | species composition, age; donor height/DBH |
| NS | Forest Inventory full DBF | SP1..SP4, SP1P..SP4P, HEIGHT, AVDI, TOTBA, TOTMRVOL, HECTARES | species, height, AVDI, volume joint stand rows |
| PE | CLUI 2010 + EFI2020 500-row samples | COVER/PER, HEIGHT; topht, bamerch, tphmerch, gtv | species, height, inferred QMD, volume |
| ON | no open stand attributes | SCANFI composition + measured donor geometry | donor-only |
| NL | land cover only | SCANFI composition + measured donor geometry | donor-only |
| NT | ATLAS broad vegetation only | SCANFI composition + measured donor geometry | donor-only |
| NU | no territorial inventory | SCANFI composition + measured donor geometry | donor-only |

## Inference and labels

- AB: `dbh_basis=inferred_dbh`. A log-log DBH-on-height equation,
  `ln(DBH_cm)=a+b ln(H_m-1.3)`, is fitted to measured BC/QC/NB/YT donor stems
  by NFI species when at least eight donor stems exist, otherwise by species
  group. Fitted coefficients and RMSE are in
  `dbh_inference_equations.csv`. AVI publishes no DBH or volume.
- SK: `dbh_basis=inventory_qmd`, `joint_basis=paired_local_marginals`. FRI has
  height and QMD marginals but no explicit tree species; CSG group shares
  constrain SCANFI composition, and the two marginals are paired by rank.
- MB: `joint_basis=donor_joint`. Species and age are local; height and DBH are
  absent from the open 1970s attributes, so the measured donor joint is kept.
- NS: `joint_basis=local_joint`. Species, height, AVDI and TOTMRVOL are carried
  from the same stand polygon.
- PE: `dbh_basis=inferred_dbh`, `joint_basis=paired_multi_source`. CLUI supplies
  species and height; EFI supplies
  `QMD=100*sqrt(bamerch/(tphmerch*pi/4))`, basal area and volume. The sources
  are paired by rank, not by location.
- ON/NL/NT/NU: `joint_basis=donor_joint`, `inventory_informed=false`.
- A 5 m / 5 cm merchantable taper-domain floor is applied where a local
  distribution falls below the published taper domain; raw values remain in
  `inventory_height_m` and `inventory_dbh_cm`, and
  `taper_domain_floor_applied` is set.

## E2 v1 to v2 changes

All nine provinces keep 36 stems. The largest changes are local species mix,
height and DBH:

| Province | Height v1→v2 (m) | DBH v1→v2 (cm) | Solid v1→v2 | Residue ODT/m3 v1→v2 |
|---|---:|---:|---:|---:|
| AB | 12.40→14.50 | 13.16→21.83 | 0.623→0.415 | 0.1325→0.1598 |
| SK | 12.14→11.31 | 12.43→9.80 | 0.499→0.357 | 0.1688→0.6349 |
| MB | 11.53→12.13 | 12.15→12.17 | 0.539→0.422 | 0.2181→0.1940 |
| ON | 11.76→11.92 | 12.37→12.59 | 0.492→0.492 | 0.2077→0.2102 |
| NS | 13.77→12.23 | 18.79→14.59 | 0.425→0.230 | 0.1935→0.2965 |
| PE | 14.32→13.04 | 19.15→17.22 | 0.409→0.273 | 0.1841→0.2380 |
| NL | 12.82→12.87 | 16.85→16.91 | 0.513→0.513 | 0.2197→0.2202 |
| NT | 11.61→11.92 | 12.32→12.64 | 0.641→0.641 | 0.1739→0.1770 |
| NU | 11.06→11.24 | 11.83→11.98 | 0.718→0.718 | 0.2742→0.2770 |

SK v2 volume is 43.6 m3/ha-eq against an FRI mean gross volume of 100.1
m3/ha; this is reported as `outside_50pct`, not calibrated. NS and PE are
within 50% of their inventory volume context. AB and MB have no open volume
field, so no volume context is claimed.

## Limits

- E2 v2 is not E1 measurement. Provincial attributes are stand-level
  photo-interpretation or LiDAR model output, not measured tree lists.
- Tapers remain published cross-jurisdiction donors; no local provincial taper
  is claimed.
- SK and PE distribution inputs are checked-in 500-row samples; the full open
  layers are REST services and are not redistributed.
- ON, NL, NT and NU remain donor-only.
- This artifact is not integrated into the frozen national facility chain v3
  and makes no national supply or official grade claim.
