# Handoff — National chain sensitivity v2 (v4 E1+E2c)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Screening refresh; `model_estimate`, not observed, not policy grade |
| Tag | `national-chain-sensitivity-v2` |
| Artifact | `data/derived/national/facilities/national_chain_global_sensitivity_v2/` |
| Parent module | `workflows/national/facilities/national_chain_global_sensitivity_v2.py` |
| Test | `tests/test_national_chain_global_sensitivity_v2.py` |
| Evidence labels | `model_estimate`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Scope | No UI change; no official OSeCAN change; no policy/national claim |

This refresh rebuilds the same seven-factor, 16-run Plackett-Burman screening
design from sensitivity v1 on the frozen v4 E1 + E2c chain. The design and
response arithmetic are unchanged. The response values use chain v7 / facility
residue supply v7, a derived trade v5 E2c source from supply v7_trade, product
chain v5, economics v2.3 margins, and OSeCAN handoff v8 zone weights.

## 1. Factors

1. Capacity / assignment: local v6 `province_capacity_floor` vs trade v5
   `published_plus_proxy`.
2. Product coefficients: donor v1 vs E1 measured + E2c (product chain v5).
3. Residue basis: stem-only vs stem + branch/foliage.
4. Harvest recovery: 0.35 / 0.60 / 0.75.
5. Process availability: 0 / 25 / 50 / 75%.
6. Price scenario: `donor_flat` vs `stumpage_scaled` (economics v2.3).
7. Zone weights: OSeCAN handoff v8 supply/dispatch vs equal split.

The PB array uses the extreme levels for recovery (0.35/0.75) and availability
(0/75%). The 0.60 and 25/50% cases remain a descriptive one-at-a-time sweep.

## 2. Responses at 119 Mm³

| Response | v4 local E2c total at 60%/50% |
|---|---:|
| Process residue base | 498.751 PJ |
| Process at 50% availability | 249.375 PJ |
| Harvest gross (stem + branch/foliage) | 444.411 PJ |
| Harvest recoverable (60%) | 266.646 PJ |
| Total gross (process at 50% + harvest gross) | 693.786 PJ |
| BC process at 50% availability | 90.705 PJ |
| National modeled margin, `donor_flat` | 5,205.329 M CAD |
| National modeled margin, `stumpage_scaled` | −655.319 M CAD |

E2c stem harvest residue is 127.832 PJ; E2c branch/foliage is 316.579 PJ. The
donor-total hybrid remains partial: donor stem 167.779 PJ plus the measured
four-province branch/foliage increment 185.780 PJ = 353.559 PJ, flagged
`residue_basis_complete=false`.

## 3. Ranking v1 vs v2

The overall order is unchanged:

| Rank | Factor | v1 score | v2 score |
|---:|---|---:|---:|
| 1 | Process availability | 0.5000 | 0.5000 |
| 2 | Residue basis | 0.4127 | 0.4423 |
| 3 | Price scenario | 0.1667 | 0.1672 |
| 4 | Harvest recovery | 0.1571 | 0.1319 |
| 5 | Product coefficients | 0.0933 | 0.0459 |
| 6 | Capacity / assignment | 0.0284 | 0.0437 |
| 7 | Zone weights | 0.0000 | 0.0000 |

Score is the mean normalized absolute main effect across the six required
responses; it is a screening rank, not a variance decomposition.

## 4. What changed and what did not

- v1's `measured_v4` level was the **E1 four-province chain v4**, not E2 v1.
  The task brief's "E2 v1 baseline" label does not match the committed v1
  inputs. This is recorded rather than relabelled.
- The coefficient level is now `e2c` (E1 measured + E2c for 13 provinces).
  Nine jurisdictions use E2c density-calibrated product and total-residue
  coefficients; BC/QC/NB/YT remain measured.
- Local process residue base falls 514.614→498.751 PJ; total harvest gross
  rises 313.640→444.411 PJ at 119 Mm³. The E2c total includes branch/foliage
  for all 13 provinces.
- The trade E2c source is derived from facility residue supply v7_trade.
  National process and harvest totals are assignment-invariant; BC process
  rises 181.409→253.214 PJ.
- OSeCAN handoff v8 has the same BC B1/B2 weights as v6, so zone weights remain
  a zero screen for provincial/national responses.
- Economics v2.3 margin values are unchanged from v1.
- The design remains resolution IV: main effects are clear of two-factor
  interactions; 2FIs alias in triplets. Three-factor interactions are aliased
  with main effects. The price-scenario BC process effect (−0.539 PJ) and the
  assignment effect on harvest recoverable residue (+13.080 PJ) are 3FI
  artifacts, not physical effects.

## 5. Verification

- `.venv/bin/python -m pytest tests/test_national_chain_global_sensitivity_v2.py tests/test_facility_residue_supply_v7_trade.py tests/test_national_chain_global_sensitivity.py -q`
  — 18 passed.
- `ruff check` passes on the new modules and tests.
- `mypy --follow-imports=skip` passes on the new modules.
- Artifact manifest hashes verify; no NaN/infinity/negative physical response;
  all rows carry `model_estimate` / screening labels.
- No unexpected `D` in parent or data repository status.
- No UI or official OSeCAN surface changed.

## 6. Non-claims

All values are model estimates at the 119 Mm³ provincial allocation. The
100/150 Mm³ cases are not screened. This is not an observed harvest, AAC,
sustainable-supply, policy, investment, operating, or interprovincial-trade
result. The trade source is a screening counterfactual, not a trade forecast.
