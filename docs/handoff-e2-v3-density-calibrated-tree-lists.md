# E2 v3 density-calibrated tree lists

Status: E2c screening artifact, `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`. No request, DUA, restricted data,
UI change or official OSeCAN change is involved.

## Scope

E2 v3 keeps the E2 v2 geometry, species distribution, donor tapers, national
pilot sorts and CFS branch/foliage allometry. It replaces the uniform
1,000 stems/ha expansion with province-specific open-inventory density where a
numeric density field exists. The nine provinces still have 36 representative
stems each (324 total).

Artifacts:

- `data/derived/national/inventory/provincial_open_inventory_density_v1/` —
  small density evidence tables, AB AVI density/structure class shares, and
  source hashes. Built by
  `workflows/national/inventory/provincial_open_inventory_density.py`.
- `data/derived/national/bucking/open_data_imputation_v3/` — density-calibrated
  tree list, bucked logs, residue ledger, product coefficients, v2→v3
  comparison, density calibration, volume context and distribution labels.
  Built by `workflows/national/bucking/open_data_imputation_v3.py`.
- Tests: `tests/test_open_data_imputation_v3.py`.

Data commit: `3fdb0f2` (pushed to
`feature/osecan-zonal-bc-supply`).

## Density sources

| Province | Stems/ha | Evidence | Basis |
|---|---:|---|---|
| AB | 1,000.00 | proxy | AVI `DENSITY` and `STRUC` are classes, not numeric stems/ha; AVI class shares are recorded in `ab_density_structure.csv` |
| SK | 2,033.65 | open inventory | FRI `BA`/`QMD`-implied arithmetic stem density, hectare-weighted median; `MSTEMS` mean 648.05/ha retained as merchantable-stem cross-check |
| MB | 2,050.00 | donor | median measured donor plot stem density (393 plots); MB 1970s FRI has species/age but no density |
| NS | 1,409.82 | open inventory | `TOTBA`/`AVDI`-implied arithmetic stem density, hectare-weighted median; `TOTMRVOL` is the volume check |
| PE | 802.84 | open inventory | EFI2020 `tphmerch`, grid-cell-area-weighted median; `ba`, `bamerch` and `gtv` are supporting/volume fields |
| ON | 1,000.00 | proxy | no open stand density field; labelled 1,000 stems/ha proxy |
| NL | 1,000.00 | proxy | no open stand density field; labelled 1,000 stems/ha proxy |
| NT | 1,000.00 | proxy | no open stand density field; labelled 1,000 stems/ha proxy |
| NU | 1,000.00 | proxy | no open stand density field; labelled 1,000 stems/ha proxy |

SK density uses `N = BA / (pi/4 * (QMD/100)^2)` per stand. `MSTEMS` is a
merchantable-stem subset and is retained as a cross-check, not as whole-tree
density. NS uses `N = TOTBA / (pi/4 * (AVDI/100)^2)` per polygon. PE uses the
published EFI merchantable trees/ha directly. All three use the area-weighted
median stand/cell density so a few very dense or very sparse stands do not set
the provincial expansion.

## Volume comparison v2→v3

| Province | Density v2 | Density v3 | Volume v2 (m3/ha-eq) | Volume v3 (m3/ha-eq) | v3/v2 | Open volume (m3/ha) | v3/open | Status |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| AB | 1,000.00 | 1,000.00 | 409.09 | 409.09 | 1.0000 | — | — | not available |
| SK | 1,000.00 | 2,033.65 | 43.61 | 88.69 | 2.0336 | 100.13 | 0.8858 | within 50% |
| MB | 1,000.00 | 2,050.00 | 122.71 | 251.57 | 2.0500 | — | — | not available |
| ON | 1,000.00 | 1,000.00 | 122.91 | 122.91 | 1.0000 | — | — | not available |
| NS | 1,000.00 | 1,409.82 | 108.06 | 152.34 | 1.4098 | 105.24 | 1.4476 | within 50% |
| PE | 1,000.00 | 802.84 | 145.48 | 116.79 | 0.8028 | 170.82 | 0.6837 | within 50% |
| NL | 1,000.00 | 1,000.00 | 178.40 | 178.40 | 1.0000 | — | — | not available |
| NT | 1,000.00 | 1,000.00 | 146.15 | 146.15 | 1.0000 | — | — | not available |
| NU | 1,000.00 | 1,000.00 | 102.73 | 102.73 | 1.0000 | — | — | not available |

Per-m3 product and residue coefficients are unchanged from E2 v2 because only
the expansion factor changed. Totals scale with density. SK moves from 43.6 to
88.7 m3/ha-eq against FRI `GBVOL` 100.1; NS moves from 108.1 to 152.3 against
`TOTMRVOL` 105.2; PE moves from 145.5 to 116.8 against EFI `gtv` 170.8. All
three open-volume comparisons are within the ±50% screening tolerance.

## Remaining gaps

- AB AVI publishes crown-density and structure classes, not numeric stems/ha.
  The 1,000 stems/ha target is a labelled proxy; no AVI density calibration is
  claimed.
- MB has no open density field. The median measured donor plot density is
  cross-jurisdiction donor evidence, not a Manitoba inventory density.
- ON, NL, NT and NU remain donor-only with a labelled 1,000 stems/ha proxy.
- Tapers remain published cross-jurisdiction donors; no local provincial taper
  is claimed.
- This is not E1 measurement, not operational supply and not a national claim.

## Verification

- 23 focused tests passed: `test_open_data_imputation_v2.py` and
  `test_open_data_imputation_v3.py`.
- E2 v3 checks: nine provinces, 36 stems each, E2c provenance, density labels,
  expansion totals equal density targets, volume/residue conservation, volume
  scaling with density, open-volume comparison, no NaN/negative, deterministic
  regeneration.
- Manifest hashes verify for both new data artifacts.
- No unexpected `D` in either repository.
