# Handoff — National facility chain v4 (E1 + E2c density-calibrated)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Frozen E2c screening chain; model estimate, not observed, not policy grade |
| Freeze tag | `national-facility-chain-v4` (annotated; points at this documentation commit) |
| Model commit | `60409639b57d3aa6ff614dfba8e4c2bdb8b28247` (E2c chain integration) |
| Data commit | `f0f77a0e6cfeb3fbbcafd685beedcd7324dbc9c8` (`UBC-FRESH/clews-c2070-fresh-data`) |
| Supersedes | `national-facility-chain-v3.2` (E2 v1 product coefficients) |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Data dependency | Open data only; no DUA, request, or approval |
| OSeCAN status | Read-only handoff; `official_osecan_changed=false` |
| Scope boundary | No UI change and no official OSeCAN change |

This chain integrates the E2 v3 density-calibrated coefficients (`E2c`) into
the 13-jurisdiction facility sequence. It keeps the v3.2 assignment v6 /
capacity v3 provincial-floor allocation and the 100/119/150 Mm³ cases. No donor
provincial product coefficient is applied.

## 1. Chain stages

```
inventory v1 -> capacity v3 -> assignment v6 -> product-chain v5
             -> facility residue v7 -> residue curve v7
             -> chain scenarios v7 -> OSeCAN handoff v8
             -> OSeCAN scenarios v7
```

New code:

- `workflows/national/facilities/e1_e2c_coefficients.py`
- `workflows/national/facilities/national_product_chain_v5.py`
- `workflows/national/facilities/facility_residue_supply_v7.py`
- `workflows/national/facilities/national_residue_curve_v7.py`
- `workflows/national/facilities/national_facility_chain_scenarios_v7.py`
- `workflows/national/facilities/forestry_osecan_handoff_v8.py`
- `workflows/national/facilities/forestry_osecan_scenarios_v7.py`

New artifacts:

- `data/derived/national/facilities/e1_e2c_product_coefficients_v1/`
- `data/derived/national/facilities/national_product_chain_v5/`
- `data/derived/national/facilities/facility_residue_supply_v7/`
- `data/derived/national/facilities/national_residue_curve_v7/`
- `data/derived/national/facilities/national_facility_chain_scenarios_v7/`
- `data/derived/national/facilities/forestry_osecan_handoff_v8/`
- `data/derived/national/facilities/forestry_osecan_scenarios_v7/`

## 2. E1/E2c coefficients

E1 measured BC/QC/NB/YT are carried unchanged from the four-province bucking
pilot. AB/SK/MB/ON/NS/PE/NL/NT/NU use E2c rows from
`open_data_imputation_v3`. The merged boundary fails closed on a missing
province, a wrong tier or source, a product-share or residue-conservation
mismatch, a recovery-scenario mismatch, or a failed open-volume check.

| Province | Tier | Solid | Pulp | Other | Total residue ODT/m³ | Density stems/ha | Evidence |
|---|---|---:|---:|---:|---:|---:|---|
| BC | E1 | 0.6953 | 0.2948 | 0.0100 | 0.161622 | — | measured |
| QC | E1 | 0.3420 | 0.6417 | 0.0164 | 0.217507 | — | measured |
| NB | E1 | 0.5238 | 0.4672 | 0.0090 | 0.187426 | — | measured |
| YT | E1 | 0.4246 | 0.5625 | 0.0129 | 0.269689 | — | measured |
| AB | E2c | 0.4077 | 0.5846 | 0.0077 | 0.159765 | 1,000.00 | proxy (AVI class) |
| SK | E2c | 0.0000 | 0.6432 | 0.3568 | 0.634899 | 2,033.65 | open inventory |
| MB | E2c | 0.3919 | 0.5785 | 0.0296 | 0.193961 | 2,050.00 | donor density |
| ON | E2c | 0.4529 | 0.5076 | 0.0396 | 0.210184 | 1,000.00 | proxy |
| NS | E2c | 0.2165 | 0.7695 | 0.0140 | 0.296461 | 1,409.82 | open inventory |
| PE | E2c | 0.2539 | 0.7274 | 0.0188 | 0.237971 | 802.84 | open inventory |
| NL | E2c | 0.5103 | 0.4873 | 0.0024 | 0.220171 | 1,000.00 | proxy |
| NT | E2c | 0.6242 | 0.3589 | 0.0169 | 0.177021 | 1,000.00 | proxy |
| NU | E2c | 0.7051 | 0.2816 | 0.0132 | 0.277046 | 1,000.00 | proxy |

All rows carry `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`. The coefficient artifact reports
`donor_product_coefficients=0`.

### 2.1 Baseline correction: v3.2 used E2 v1, not E2 v2

The frozen v3.2 `national_product_chain_v4` reads
`open_data_imputation_v1/product_coefficients.csv`. E2 v2 was never integrated
into a facility chain. Therefore the primary comparison in this handoff is
**v3.2 (E2 v1) → v4 (E2c v3)**. A separate coefficient check confirms E2 v2 and
E2c share identical per-m³ solid/pulp/other shares and residue coefficients:
all 54 coefficient rows are unchanged (`changed=false`). Density calibration is
an extensive change (stems/ha and expanded reference volume), not an intensive
per-m³ change.

This is recorded rather than silently relabelled: calling v3.2 an "E2 v2"
baseline would be wrong.

## 3. Product and residue change, v3.2 → v4

Assignment and harvest are identical across the two chains, so every delta is
a coefficient effect. At the 119 Mm³ reference:

| Metric | v3.2 (E2 v1) | v4 (E2c) | Delta | Delta % |
|---|---:|---:|---:|---:|
| Sawlog product Mm³ | 38.119 | 33.957 | −4.162 | −10.92% |
| Panel product Mm³ | 4.172 | 3.717 | −0.456 | −10.92% |
| **Solid product Mm³** | **42.291** | **37.674** | **−4.617** | **−10.92%** |
| Pulp product M odt | 10.824 | 12.066 | +1.242 | +11.47% |
| Pellet product M odt | 4.356 | 5.230 | +0.874 | +20.06% |
| Bio product GWh | 775.4 | 930.9 | +155.5 | +20.06% |
| Process residue PJ | 505.333 | 498.751 | −6.582 | −1.30% |
| Harvest gross PJ | 390.418 | 444.411 | +53.993 | +13.83% |
| Harvest recoverable PJ (60%) | 234.251 | 266.646 | +32.396 | +13.83% |
| Total gross PJ | 895.751 | 943.162 | +47.411 | +5.29% |
| Total recoverable PJ (60%) | 739.584 | 765.397 | +25.814 | +3.49% |

The solid decrease and pulp increase reflect the E2c province shares, not the
density expansion. Harvest residue rises because the E2c total-residue
coefficients differ from E2 v1; process residue falls slightly because the
class mix changes while class residue ratios stay donor assumptions.

All three documented cases (Mm³):

| Case | Solid Mm³ | Pulp M odt | Process PJ | Harvest gross PJ | Harvest 60% PJ | Total gross PJ |
|---|---:|---:|---:|---:|---:|---:|
| 100 | 31.693 | 10.137 | 419.238 | 373.244 | 223.946 | 792.482 |
| 119 | 37.674 | 12.066 | 498.751 | 444.411 | 266.646 | 943.162 |
| 150 | 46.137 | 15.551 | 626.865 | 573.125 | 343.875 | 1,199.991 |

Products and residue order 100 < 119 < 150. The 150 Mm³ case remains the
documented provincial ceiling table, not the operable-proxy intensity screen.

At 119 Mm³ the OSeCAN zone cases are:

- Process availability 0/25/50/75%: 0 / 124.688 / 249.375 / 374.063 PJ.
- Harvest recovery 35/60/75%: 155.544 / 266.646 / 333.308 PJ.

The 0.60 factor is a reference scenario, not a calibrated recovery factor.

## 4. OSeCAN handoff v8 and scenarios v7

Handoff v8 maps the curve v7 totals to all 13 OSeCAN zones with no `UNMAPPED`
bucket. The official OSeCAN tree is hash-checked before and after; the build
reports 0 changed files and `official_osecan_changed=false`.

Tier positions at 119 Mm³:

| Stream | Case | PJ | Tier |
|---|---|---:|---|
| Process | 0% | 0.000 | T1 |
| Process | 25% | 124.688 | T3 |
| Process | 50% | 249.375 | above_T4 |
| Process | 75% | 374.063 | above_T4 |
| Harvest | 35% | 155.544 | T4 |
| Harvest | 60% | 266.646 | above_T4 |
| Harvest | 75% | 333.308 | above_T4 |

The harvest 35% case moves from T3 in the v3.2 handoff to T4. Harvest 60% and
75% remain above T4; all process tiers are unchanged.

Zone results at 119 Mm³ (PJ):

- Process 50%: B2 83.191, Q1 35.553, A1 32.714, O1 23.383, Q2 22.663,
  T1 18.516, A2 18.101, B1 7.514, O2 4.680, L1 1.871, M1 1.044,
  S1 0.107, N1 0.040.
- Harvest 60%: B2 65.780, Q1 39.330, S1 30.619, O1 26.231, A1 26.135,
  Q2 25.071, T1 21.628, A2 14.460, B1 5.941, O2 5.250, L1 3.554,
  M1 2.444, N1 0.203.

Scenarios v7 carries all three harvests and all seven cases to the 13 zones.
Zone weights remain the v2 proxy weights: BC 2050 zonal supply dispatch, AB/ON/QC
2050 zonal dispatch activity, single-zone/aggregate membership elsewhere. Every
multi-zone weight is `proxy_share=true`.

## 5. Open-volume checks

| Province | Density stems/ha | Expanded volume m³/ha | Open inventory m³/ha | Ratio | Status |
|---|---:|---:|---:|---:|---|
| SK | 2,033.65 | 88.692 | 100.126 | 0.8858 | within_50pct |
| NS | 1,409.82 | 152.342 | 105.239 | 1.4476 | within_50pct |
| PE | 802.84 | 116.794 | 170.824 | 0.6837 | within_50pct |
| AB | 1,000.00 | 409.088 | — | — | not_available |
| MB | 2,050.00 | 251.565 | — | — | not_available |
| ON | 1,000.00 | 122.913 | — | — | not_available |
| NL | 1,000.00 | 178.398 | — | — | not_available |
| NT | 1,000.00 | 146.149 | — | — | not_available |
| NU | 1,000.00 | 102.728 | — | — | not_available |

SK, NS and PE pass the ±50% screening check. AB, ON, NL, NT and NU have no open
volume field; `not_available` is not a pass. MB uses donor median plot density,
which is cross-jurisdiction donor evidence, not Manitoba inventory density. AB
AVI publishes density classes, not numeric stems/ha.

## 6. Verification and status

- Focused tests: `tests/test_e1_e2c_facility_chain_v4.py` plus the existing
  `tests/test_e1_e2_facility_chain_v6.py` and
  `tests/test_open_data_imputation_v3.py` — 27 passed.
- Contracts: 13 jurisdictions; E1/E2c source and zero donor product
  coefficients; product, feedstock and residue conservation; 100/119/150
  scenarios; 0.35/0.60/0.75 cases; v3.2→v4 and E2 v2→E2c comparisons;
  open-volume checks; OSeCAN labels and zone coverage; no NaN/infinity/negative.
- Manifests: all seven v4 artifact manifests verify (47 output entries), as do
  the three unchanged upstream manifests (inventory v1, capacity v3,
  assignment v6; 25 entries). Total 72/72 entries, 0 mismatches.
- Official OSeCAN tree unchanged: 146 files, tree SHA-256
  `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
  identical to the v3.2 and v4 handoff before/after checks; 0 changed files.
- Determinism: a clean rebuild of all seven v4 stages to a scratch directory
  reproduced every numeric CSV byte-for-byte; only `generated_utc`-derived
  summary/manifest hashes differed.
- No request, DUA, restricted data, UI change, or official OSeCAN change.
- No national supply, policy, operating, or grade-validation claim.
- No unexpected `D` in either repository.

## 7. Limitations

- Nine jurisdictions are E2c, not measured. Three have open density evidence,
  one has donor density, and five retain a 1,000 stems/ha proxy.
- Tapers remain published cross-jurisdiction donors in 11 of 13 jurisdictions;
  only BC and QC have local tapers.
- Branch and foliage are CFS model estimates with documented proxies. They add
  dry mass and energy but no measured volume.
- Within-class SAW/PANEL and PELLET/BIO splits, class conversion ratios and
  stem-residue m³ routing remain donor assumptions.
- Recovery is not calibrated; 0.35/0.60/0.75 are equal-status scenarios.
- Proxy capacity remains material. The 100/119/150 Mm³ cases are planning
  allocations, not legal harvest levels.
- No transport cost, annual dynamics, economics, trade, or mill operating
  status is modelled.

## 8. Freeze record

- Annotated tag `national-facility-chain-v4` marks the documentation commit
  carrying this handoff and the v4 model card.
- The tag records model commit
  `60409639b57d3aa6ff614dfba8e4c2bdb8b28247` and data commit
  `f0f77a0e6cfeb3fbbcafd685beedcd7324dbc9c8`.
- It does not move `national-facility-chain-v3`, `-v3.1`, or `-v3.2`.
- The freeze is documentation and tag only; no code, model artifact, UI, or
  official OSeCAN file changed at the freeze commit.
