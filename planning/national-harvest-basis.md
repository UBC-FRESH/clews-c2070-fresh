# National Harvest Basis for C2070

| Field | Value |
|---|---|
| Document | `planning/national-harvest-basis.md` |
| Date | 2026-11-18 |
| Status | Decision note; base, low, and high harvest cases adopted for C2070 |
| Audience | PI, national modellers, CLEWS supply-chain maintainers |
| Scope | Annual log harvest basis for the C2070 forest/biomass model |
| Governing decision | User, 2026-11-18: the 2024 observed harvest is the C2070 base; 150M is the high case |
| Companion surfaces | `planning/national-rsyc-backbone.md` §4.3; `NATIONAL_HARVEST_BASIS_INVENTORY.md` |

## 1. Decision

The C2070 base is **118,977,339 m3 log/yr**, the 2024 observed national harvest, held flat from 2025 to 2070. It is an accounting path, not a harvest forecast.

The former `150,000,000 m3/yr` assumption is now the **high** case. It is a rounded 2010–2019 mean national harvest and is 26.1% above the 2024 observed harvest. It is an upper bound, not the central case.

The **low** case is `100,000,000 m3/yr`.

| Case | Path ID | Log basis (m3/yr) | Label |
|---|---|---:|---|
| Base | `base_hold_2024` | 118,977,339 | Central C2070 base; 2024 observed harvest held flat 2025–2070 |
| Low | `low_100m` | 100,000,000 | Lower scenario bound |
| High | `high_150m` | 150,000,000 | Upper bound; former central assumption (2010–2019 mean, rounded) |

The existing 150M parameter files, reports, and derived outputs are retained unchanged and are read as the high case. No 150M artifact is rewritten by this decision.

## 2. Observed harvest, 2021–2024

| Year | Observed harvest (m3) |
|---:|---:|
| 2021 | 139,417,556 |
| 2022 | 130,038,592 |
| 2023 | 110,830,798 |
| 2024 | 118,977,339 |

Source: NRCan Forest Statistics Database, statistical profile management volume, national total. The 2024 Canada total equals the sum of the 12 reporting jurisdictions. The series is observed management volume; its tenure, gross/net, and fiscal-year definitions are not re-derived here.

## 3. AAC, wood supply, and actual harvest

| Measure | Reference value (m3) | Interpretation |
|---|---:|---|
| Actual harvest (FSD) | 118,977,339 | Realized removals; adopted as the C2070 base |
| Wood supply (NFD) | 207,680,142 | Potential supply under current management; not harvest and not an AAC |
| AAC / proxy, sum of documented values | 157,343,387 (partial sum) | Mixed statutory AAC, approximate AAC, a repo TSA path, and one actual-mean proxy; not a national total |

- 2024 utilization is **57.3%** (harvest / NFD wood supply); the gap is 88,702,803 m3.
- The AAC/proxy sum covers 10 jurisdictions. It excludes MB (documented range 7.39–7.99M only), PE (no statutory AAC), and NU (not assessed), so it is not a national AAC and is not used as a harvest path.
- AAC is a ceiling where one exists, not a forecast. AAC/proxy and total harvest are not interchangeable: NB and NS report 2024 harvest above their Crown AAC, while BC, QC, AB, ON, SK, NL, and YT report harvest below their AAC or proxy.
- Provincial AAC values are 2024/2025 snapshots. Only BC has a parsed AAC path (2021–2028). No 2025+ national AAC trajectory is adopted.
- The adopted base is below both the documented AAC/proxy sum and the NFD wood supply, so it is a realized-harvest basis, not a capacity or sustainable-supply estimate.

## 4. Why 150M is the high case

- **Origin.** 150,000,000 m3/yr is a rounded 2010–2019 mean national harvest, not a recent observed level.
- **Level.** It is 31,022,661 m3 (+26.1%) above the 2024 observed harvest.
- **Range.** It exceeds every observed year in 2021–2024: 139,417,556; 130,038,592; 110,830,798; and 118,977,339 m3.
- **Status.** It is retained as the high case for comparison. It is not the central case and must not be presented as a harvest statistic or a forecast.

## 5. RSYC-only v0 caps

Conversion for all three cases: `total_m3 = log / 0.762716956451988`; `ODT = total_m3 * 0.08831776452936206`; `PJ = ODT * 0.60 * 18.5 / 1e6`. Recovery is 0.60 and LHV is 18.5 GJ/ODT.

| Case | Log basis (m3/yr) | National (PJ/yr) | BC zonal 2028+ (PJ/yr) | Rest of Canada 2028+ (PJ/yr) |
|---|---:|---:|---:|---:|
| Base | 118,977,339 | 152.922679 | 43.523816 | 109.398863 |
| Low | 100,000,000 | 128.530929 | 43.523816 | 85.007113 |
| High | 150,000,000 | 192.796393 | 43.523816 | 149.272577 |

The BC zonal total is the unchanged 2028+ sum of `SUPBCWB1` (3.605475 PJ) and `SUPBCWB2` (39.918341 PJ). The `SUPBCW` cap is recomputed year by year as the national total minus the two zonal caps, so `SUPBCW` + `SUPBCWB1` + `SUPBCWB2` equals the national total in every year without double counting BC.

## 6. Stress results

Stress definition: `Current Measures`, 2021–2070, CO2 at 95 $/t to 2025 and 200 $/t from 2026; every `PWRHYD<z>` is upper-capped at its 2021 activity. This is an upper cap, not fixed dispatch. The baseline (115 $/t from 2026) is zero-biomass, so the stress run is the informative supply case. All results are FRESH-side provisional OSeCAN runs on a disposable copy of public C2070 commit `40d5b43bb3f8`; they are not official C2070 results.

| Case | Objective (M$) | Emissions total (Mt CO2) | BCW supply, 2021–2070 (PJ) | `SUPBCW` binds |
|---|---:|---:|---:|---|
| Base 119M | 1,371,124.38 | 38.544682 | 7,086.539 | 2027–2070 |
| Low 100M | 1,371,219.04 | 39.685119 | 5,972.421 | 2024–2070 |
| High 150M | 1,370,983.13 | 38.544682 | 8,819.496 | 2028–2070 |

- `BCW` supply is the sum of `SUPBCW`, `SUPBCWB1`, and `SUPBCWB2` activity.
- The objective is nearly flat across the range: 235.91 M$ (about 0.017%) separates the low and high cases.
- Emissions are 1.140 Mt higher in the low case than in the base and high cases; the base and high cases both total 38.544682 Mt.
- `SUPBCW` binds earliest in the low case (2024) and latest in the high case (2028). The BC zonal caps bind in all three cases: `SUPBCWB1` 2026–2070 and `SUPBCWB2` 2032–2070.

## 7. Caveats

- The 2024 hold is an accounting path, **not a harvest forecast** and not a sustainable-supply estimate.
- No THLB or operability filter is applied. The SCANFI gross treed area (422.5 Mha, 1.145× the NFI Table 4.0 forest-land benchmark) is not a managed landbase.
- The caps are RSYC-only v0: no benchmark corrections, group/generic curves where species are unresolved, and BC taper proxies downstream.
- 150M is an upper bound; 100M is a lower bound. Neither is a forecast.
- The observed series is FSD management volume. Its exact treatment of tenure, gross/net volume, and fiscal/calendar year is not re-derived here.
- The 2024 NFD wood supply is the reviewed compilation supplied with the harvest review; the live NFD endpoint failed TLS validation and was not bypassed.
- Provincial AAC values are 2024/2025 snapshots with MB, PE, and NU gaps; no provincial AAC trajectory beyond BC is documented.
- The hydro stress is an upper cap at 2021 activity, not fixed dispatch. A true fixed-hydro run remains open.
- The managed-landbase proxy is still pending; no sustainable-supply claim can be made until the landbase and AAC path are decided.

## 8. Next steps

1. **Managed landbase.** Define and apply a THLB and operability filter to the SCANFI area, then rerun the caps and stress. Replace the gross-area proxy with a managed-landbase proxy before any sustainable-supply claim.
2. **AAC trajectory.** Build provincial AAC paths beyond BC with explicit legal/planning/proxy labels, decide whether a forecast path is added alongside the 2024 hold, and keep AAC distinct from harvest and from wood supply.
3. **True fixed hydro.** Rerun the stress with lower and upper hydro bounds at 2021 dispatch, instead of the current upper cap, and report the biomass response separately.

## 9. Provenance

| Item | Record |
|---|---|
| Observed harvest | `data/raw/nrcan_fsd/StatProfile_Management_volume_EN.csv`; `data/derived/national/harvest_basis/harvest_reference_paths.csv` |
| 2024 wood supply | Reviewed NFD 2024 values, stored as `wood_supply_m3` and labelled `published_wood_supply_nfd_2024` |
| Base caps and run | `data/derived/national/clews/osecan/national_base_119m/`; `supply_basis.json`; `NATIONAL_BASE_119M_OSECAN_REPORT.md` |
| High caps and run | `data/derived/national/clews/osecan/national_150m/`; `NATIONAL_150M_OSECAN_REPORT.md` |
| Low run | `data/derived/national/clews/osecan/harvest_sensitivity_100_200/`; `HARVEST_SENSITIVITY_REPORT.md` |
| Data commits | Base 119M `4be339d` (parent `d0d04af`); 150M `5eab87a` (parent `3015f1a`); sensitivity `8f0ce1c` (parent `928a74e`) |
| Model copy | Disposable copy of public `deltae/C2070` commit `40d5b43bb3f8`; public repository not modified |
