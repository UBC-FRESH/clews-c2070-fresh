# National Harvest / AAC Basis Inventory

Status: **observed 2021-2024 harvest filled; 2024 base path and scenario bounds
documented**. The national base is the 2024 observed harvest of
**118,977,339 m3/yr**, held flat from 2025. The former `150,000,000 m3/yr`
OSeCAN supply basis is now explicitly an **upper scenario bound**, not the
central case.

Prepared: 2026-11-18  
Scope: CLEWS-C2070 forest/biomass supply, 2021-2070  
Repository: `/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh`

## 1. What changed

The previous inventory contained 600 empty provincial rows and one BC AAC path.
This update fills:

- `actual_harvest_m3` for 2021-2024 for all 12 requested jurisdictions from the
  NRCan Forest Statistics Database (FSD) statistical profiles;
- `aac_m3` for 2024/2025 where a documented legal, planning, or clearly labelled
  proxy value exists;
- `wood_supply_m3` for 2024 from the National Forestry Database (NFD) values
  supplied with the harvest review (the live NFD CSV endpoint failed TLS
  certificate validation, so no fresh NFD download was made);
- `base_harvest_m3` for 2025-2070 as the 2024 observed harvest held flat; and
- `CAN` rows for the national `base_hold_2024`, `low_100m`, and `high_150m`
  paths.

The BC AAC path for 2021-2028 is unchanged. The 2024 BC actual harvest is stored
in `actual_harvest_m3`, not written over `aac_m3`.

## 2. Sources

### 2.1 Primary observed harvest

- Dataset: NRCan Statistical Profiles, FSD-EN-datasets, management volume.
- URL:
  `https://raw.githubusercontent.com/NRCan/statisticalprofile-datasets/refs/heads/main/FSD-EN-datasets/StatProfile_Management_volume_EN.csv`
- Local archive:
  `data/raw/nrcan_fsd/StatProfile_Management_volume_EN.csv`
- Coverage: 2000-2024, provincial/territorial and Canada totals, m3.
- Cross-check: the FSD 2024 Canada total is `118,977,339 m3`, exactly the
  reviewed total, and the 12 provincial values sum to that total.
- Area companion:
  `data/raw/nrcan_fsd/StatProfile_Management_harvest_EN.csv`
  (`StatProfile_Management_harvest_EN.csv`, hectares). It is archived for
  provenance but is not used in the volume basis.

### 2.2 2024 NFD wood supply

- Live endpoint attempted:
  `https://national-forestry-database.ca/download/data/csv/`
- Result: TLS certificate validation failed (`unable to get local issuer
  certificate`). The endpoint was not bypassed.
- Fallback: the reviewed 2024 NFD values supplied with the harvest review,
  recorded as `wood_supply_m3` for 2024 and labelled
  `published_wood_supply_nfd_2024`.
- **Wood supply is potential supply, not harvest.** It is not substituted into
  `actual_harvest_m3` or `base_harvest_m3`.

### 2.3 AAC / allowable cut

- BC: repo-parsed TSA AAC path from
  `data/derived/national/clews/audit/bc_aac_trajectory_2011_2028.csv`, as before.
- Other jurisdictions: the 2024/2025 values supplied with the harvest review.
  Values marked approximate remain approximate; ranges and non-statutory cases
  are left blank rather than midpoint-filled.

## 3. Observed harvest, 2021-2024

National FSD totals:

| Year | Actual harvest (m3) |
|---:|---:|
| 2021 | 139,417,556 |
| 2022 | 130,038,592 |
| 2023 | 110,830,798 |
| 2024 | 118,977,339 |

2024 provincial values and the documented 2024/2025 AAC or proxy:

| Jurisdiction | 2024 actual harvest (m3) | 2024/2025 AAC or proxy (m3) | 2024 NFD wood supply (m3) | AAC status |
|---|---:|---:|---:|---|
| BC | 38,876,568 | 45,223,878 (repo TSA AAC path) | 63,774,601 | repo-parsed TSA AAC; Crown AAC separately ~58-60M |
| QC | 25,939,056 | 34,115,200 | 48,467,117 | legal Crown AAC |
| AB | 22,260,402 | 30,100,000 | 31,632,061 | Crown AAC, approximate |
| ON | 13,121,645 | 28,700,000 | 28,802,910 | Crown AAC, approximate |
| NB | 9,341,187 | 6,234,700 | 9,590,407 | legal Crown AAC |
| SK | 4,224,936 | 8,940,000 | 8,939,765 | Crown AAC, approximate |
| NS | 2,322,413 | 1,720,000 | 5,750,000 | Crown AAC; 4.03M private additional |
| NL | 1,413,957 | 2,012,233 | 2,012,233 | legal Crown AAC |
| MB | 1,103,943 | `missing` | 7,990,048 | documented range 7.39-7.99M only |
| PE | 293,880 | `not_applicable` | 460,000 | no statutory AAC; 40k Crown + 420k private planning |
| YT | 42,715 | 261,000 | 261,000 | legal Crown AAC |
| NT | 36,637 | 36,376 | `missing` | no territory-wide AAC; 2020-2024 actual mean proxy |
| NU | not reported | not assessed | not reported | no FSD rows; zero is not assumed |

National 2024 NFD wood supply (reviewed): **207,680,142 m3**. It exceeds the
2024 actual harvest by 88.7M m3, so wood supply and harvest must not be treated
as interchangeable.

## 4. National reference paths

`harvest_reference_paths.csv` records four national series for 2021-2070:

| Path | 2021-2024 | 2025-2070 | Status |
|---|---|---|---|
| `observed_2021_2024` | FSD actual | blank | observation |
| `base_hold_2024` | FSD actual | 118,977,339 flat | documented base path |
| `low_100m` | FSD actual | 100,000,000 flat | scenario lower bound |
| `high_150m` | FSD actual | 150,000,000 flat | scenario upper bound |

The 100M and 150M paths are counterfactual bounds. They are not forecasts and
must not be presented as a central national harvest basis.

## 5. The 150M finding

The `150,000,000 m3/yr` figure is **above** the observed 2024 national harvest
of 118,977,339 m3 by 31,022,661 m3 (+26.1%). It also exceeds every 2021-2024
observed year (139.4M in 2021, 130.0M in 2022, 110.8M in 2023, 119.0M in 2024).
It is therefore an upper scenario bound, not a central case and not a harvest
statistic.

The C2070 base run in
`data/derived/national/clews/osecan/national_base_119m/` uses the observed 2024
value, not 150M. The 150M high-case directory is retained unchanged for
comparison.

## 6. C2070 base conversion

RSYC-only v0 basis, unchanged from the 150M experiment:

- log share `0.762716956451988`;
- tile factor `0.08831776452936206` ODT/m3 total;
- recovery `0.60`;
- LHV `18.5` GJ/ODT.

| Step | Value |
|---|---:|
| Annual log volume (m3/yr) | 118,977,339 |
| Total volume (m3/yr) | 155,991,469.70 |
| Gross residue (ODT/yr) | 13,776,817.89 |
| National recoverable PJ/yr (base) | 152.922679 |
| BC zonal 2028+ (PJ) | 43.523816 |
| Rest-of-Canada 2028+ (PJ) | 109.398863 |

## 7. Missing and non-applicable values

- MB AAC: no single numeric value; only a documented range 7.39-7.99M m3/yr.
- PE AAC: no statutory AAC.
- NT AAC: no territory-wide AAC; a 36,376 m3/yr actual-mean proxy is labelled
  as a proxy, not a legal ceiling.
- NU: no FSD rows and no documented zero; excluded from the national sum.
- NFD wood supply: NT not reported; all other 2024 values are the reviewed
  compilation, not a fresh download.
- No 2025+ AAC forecast is invented. The base path is a hold rule.

## 8. Files

| File | Rows | Content |
|---|---:|---|
| `harvest_basis_template.csv` | 750 | 600 provincial rows + 150 national path rows |
| `harvest_reference_paths.csv` | 50 | national observed/base/low/high paths, 2021-2070 |
| `provinces.csv` | 13 | jurisdiction coverage and source status |
| `manifest.json` | — | input/output hashes, coverage, assumptions, NFD fallback |

Generator: `scripts/national/fill_harvest_basis.py`.

## 9. Verification

- FSD 2024 Canada total equals 118,977,339 m3.
- The 12 provincial FSD 2024 values sum to the Canada total.
- 2021-2024 actual values are populated for all 12 jurisdictions; NU has no rows.
- The 2025-2070 base path equals each jurisdiction's 2024 observed value.
- National `base_hold_2024` equals 118,977,339 in every year 2025-2070.
- BC `aac_m3` 2021-2028 is unchanged from the previously parsed path.
- 150M is recorded only as `high_150m`, with explicit upper-bound notes.

## 10. Caveats

- The base path is an accounting hold of 2024, **not a harvest forecast**.
- The observed harvest series is FSD management volume; its exact treatment of
  tenure, gross/net volume, and fiscal/calendar year is not re-derived here.
- The managed land base (THLB) and operability filters are still pending; the
  C2070 run remains an RSYC-only v0 supply experiment.
- Provincial AAC values are 2024/2025 snapshots; no provincial AAC trajectory
  beyond BC is documented.
