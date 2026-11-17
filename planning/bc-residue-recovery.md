# Recoverable-residue supply curve and time path for OSeCAN B1/B2

Status: **FRESH-side provisional**. This planning note governs the
P7.18 recoverable-residue task. It is not a C2070/OSeCAN boundary decision
and not a production-calibrated fuel offer.

## Question

The P7.17 artifact published the **gross** taper residue from the BC
area-weighted national batch: 93.170084 PJ/yr on the 2018 AAC, flat
2021-2070. Two things were missing for OSeCAN:

1. a recovery basis that converts gross taper residue to the roadside residue
   the FFCM model actually supplies; and
2. a time path that uses the AAC forecast instead of holding 2018 flat.

## Inputs

- FFCM FMU ledger: `metadata/ffcm-archive/parsed/fmu_supply.csv`.
- FFCM anchors and facility TSA cache: `metadata/fmu_tl_anchors.csv`,
  `data/derived/national/assignments/tsa24_pilot/bc_facility_tsa.csv`.
- BC area weights and joined batch:
  `data/derived/national/assortments/bc_area_weights/`.
- P7.17 gross residue: `data/derived/national/clews_supply/bc_woody_residue/`.
- AAC trajectory: `tsa_apportionment_2018.csv` and
  `TSA Tenure Apportionment V5.xls` (AAC rows 2011-2028).
- TSA24 biomass component curves:
  `data/derived/national/bucking/tsa24_grade_curves/biomass_au_curves.csv`.
- RSYC age-100 AGB: `national_stands.csv: rsyc_agb_mg_ha`.

## Decisions

### FFCM zone mapping

No defensible FMU -> TSA crosswalk exists in the repo. `FMUs.cfg` carries only
`BCnn` ids; `tmp/FMU.xlsm` has the same id plus a TL hub, not a TSA; TL hubs
have no coordinates and FFCM routes are FMU -> hub, not FMU -> mill. The build
therefore allocates the FFCM BC totals to B1/B2 by the documented 2018 AAC zone
log share (B1 17.399562%, B2 82.600438%) and flags the allocation
`no_fmu_tsa_map_documented_zone_share`. No FMU volume is invented.

### Recovery

FFCM InitialContent is a stock. The implied recovery is computed on the same
log-volume basis: FFCM residue ODT / (FFCM log m3 x zone taper ODT/m3).
B1 is 1.736815 (outside [0.2, 1.0]) and uses the documented 0.60 fallback.
B2 is 0.583931 (inside the bounds) and uses the FFCM-implied value. The
national ratio 0.660180 is comparison only; the national cap is the B1+B2 sum.

### Time path

The workbook AAC is parsed for 2011-2028. The 2021-2028 values are used
directly; the 2028 value is held flat to 2070. The label is
`aac_2028_hold`. No MPB, climate, wildfire, or future-tenure trajectory is
invented.

### AGB sensitivity

The age-100 (Bark + Foliar + Branch) / Above median is 0.246837 (range
0.169403-0.366018 across 16 AUs). Applying it to the RSYC age-100 BC AGB gives
0.111268 ODT/m3 in B1 (3.07x the B1 taper ratio) and 0.126246 ODT/m3 in B2
(1.17x the B2 taper ratio). This is a sensitivity only; the base remains the
taper x recovery curve.

## Result

Base case: B1 3.678381 PJ/yr and B2 46.794140 PJ/yr in 2021, 50.472521 PJ/yr
national; 42.958795 PJ/yr from 2028 to 2070. Base cost is 2.456978 $/GJ
(45.454084659 $/ODT / 18.5 GJ/ODT). Low case 30.193455 PJ/yr at 3.194071
$/GJ in 2021; high case 64.700261 PJ/yr at 1.965582 $/GJ. The 50 PJ/$4
placeholder is close to the base 2021 cap and above the 2028-2070 cap; its
$4/GJ cost is above all three cases.

## Outputs

`data/derived/national/clews/`: `bc_residue_recovery.csv`,
`bc_residue_time_path.csv`, `bc_residue_cases_summary.json`, the 12 zone
parameter files (4 per case), the 6 national comparison parameter files,
`BC_RESIDUE_RECOVERY_REPORT.md`, `manifest.json`, and
`audit/bc_ffcm_fmu_supply.csv`.

## Caveats

1. B1/B2 is the previous FRESH-side provisional crosswalk.
2. No FMU -> TSA/zone crosswalk; the zone split is a documented share.
3. FFCM InitialContent is a stock; the implied recovery is a per-m3 ratio.
4. Cost is a single provincial FFCM value; it excludes collection, grinding,
   bundling, stumpage, and opportunity cost.
5. The 2028 hold is not a forecast.
6. The RSYC national batch is a placeholder equal-area distribution.
7. `SUPBCW` is a comparison rollup; do not load it with B1/B2.
