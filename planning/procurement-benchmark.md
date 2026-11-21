# QC procurement cost benchmark — corrected framing

| Field | Value |
|---|---|
| Document | `planning/procurement-benchmark.md` |
| Date | 2026-11-21 |
| Status | Corrected evidence note. No ledger, model, UI, or public OSeCAN change. |
| Scope | QC 2022 purchase-vs-BGAD procurement cost spread; BMMB 2011–2015 delivered-sale benchmark |
| Companion | `planning/net-value-benchmark.md` (deprecated framing); `planning/net-value-v2d.md`; `data/derived/national/harvest_basis/procurement_benchmark/` |

**Decision summary.** The 2022 Quebec MRNF survey does **not** support a QC net
value or a M CAD net-value sensitivity. Table 2 `107.22` is a buyer-side
procurement **cost**, not log-sale revenue. Table 20 BGAD is `81.40` before
transfers, `25.01` transfers, and `106.41` after transfers for a different
respondent sample. The differences `25.82` and `0.81` are
**procurement-channel cost spreads**, not forest net value. Every generated row
is `net_value_status=not_estimated`,
`interpretation=purchased_vs_bgad_procurement_cost_spread`, and
`policy_grade=false`.

## 1. What changed

The earlier `net_value_benchmark` artifact is deprecated and retained only for
audit. It:

- called the T2/T20 difference a “residual” or forest-sector net value;
- published a QC screening-volume sensitivity in M CAD;
- described regional differences as residuals.

The replacement artifact removes the M CAD sensitivity and relabels every
difference as a cost spread. The old directory is not deleted; it contains a
`DEPRECATED.md` pointer.

## 2. Definitions

| Field | Source | Meaning |
|---|---|---|
| `purchase_cost` | T2 `purchase.wood_purchase_delivered` | Buyer-side procurement cost: purchase, State transfers, transport/unloading, and road costs where applicable. Not log-sale revenue. |
| `bgad_before_transfers` | T20 `synthesis_procurement.procurement_total_before_transfers` | Harvest + road + haul + hosting + other costs. Excludes species gain/loss. |
| `transfers_total` | T20 `synthesis_procurement.transfers_total` | Payments to the State. PRCCM road rebates sit here, not in the `road` component. |
| `bgad_after_transfers` | T20 `synthesis_procurement.procurement_total_after_transfers` | BGAD before + transfers. |
| `cost_spread_before_transfers` | derived | `purchase_cost - bgad_before_transfers`. Channel cost spread, not net value. |
| `cost_spread_after_transfers` | derived | `purchase_cost - bgad_after_transfers`. Channel cost spread, not net value. |

Identities:

```text
bgad_before_transfers + transfers_total = bgad_after_transfers
cost_spread_before_transfers - cost_spread_after_transfers = transfers_total
```

These are arithmetic identities on cost aggregates. They do not create a net
value.

## 3. Provincial result

| Species | Product | Purchase cost | BGAD before | Transfers | BGAD after | Spread before | Spread after |
|---|---|---:|---:|---:|---:|---:|---:|
| SEPM | total | 107.22 | 81.40 | 25.01 | 106.41 | 25.82 | 0.81 |
| Hardwood | sciage | 85.42 | 79.73 | 20.22 | 99.95 | 5.69 | -14.53 |
| Poplar | trituration | 65.01 | 72.28 | 2.18 | 74.46 | -7.27 | -9.45 |

Pine, cedar, and species totals without a matching T20 synthesis row are
purchase-cost-only; their spread is not computable. Product rows are not
additive to a species total without published volumes. Negative spreads are
source arithmetic, not clipped, and are not evidence of an error by themselves.

## 4. Regional result

The seven regional SEPM groups come from **REG `regional_2022`**
(`Compilation_donnees_regionalisees_2022.pdf`), not T20. They publish regional
BGAD costs but no regional purchase cost. The conditional spread holds the
provincial SEPM purchase cost of `107.22` constant.

Range: BGAD before `58.73–97.07`, after `78.78–118.10`; conditional spread
before `10.15–48.49`, after `-10.88–28.44` CAD/m3. R09 has `sample_n=3` and
retains a three-respondent flag on its own row and on both min/max bound rows.
Bound rows are independent column-wise extrema; horizontal identities do not
hold across them. Not regional net values.

## 5. BMMB delivered-sale benchmark

BMMB delivered sales remain a separate nominal historical benchmark:

| Scope | Full rates | Volume (m3) | Weighted (CAD/m3) |
|---|---:|---:|---:|
| Crown public forest, 2011–2015 (primary) | 9 | 74,550 | 35.7547 |
| Teaching/research forest, 2014–2015 | 2 | 15,350 | 50.6266 |
| All delivered, 2011–2015 (secondary) | 11 | 89,900 | 38.2940 |

Per-year rows carry per-year counts and per-year price-year bases. The 2012
re-offer overlap is 2 sales / 22,200 m3; it double-counts offered and awarded
volume totals but does not change the full-rate numerator or denominator
because both re-offer sales are excluded from the rate. No deflation, no grade
split, no species allocation, and not stumpage.

## 6. Accounting caveats

- T2 purchase is not observed log-sale revenue or stumpage.
- T20 excludes species gain/loss (reported separately in T22); for SEPM the
  exclusion is larger than the after-transfer spread.
- PRCCM road rebates sit in transfers. Do not combine `T5.road_total_after_prccm`
  with `T10.transfers_total`; that would double-count the rebate.
- Purchase and BGAD are different respondent samples. Their difference is a
  channel cost spread, not an arm’s-length forest-sector net value.
- No volume-scaled M CAD diagnostic is produced. The provincial allocation
  files are screening totals and are not monetised.

## 7. Outputs and reproduction

```bash
.venv/bin/python -m workflows.national.harvest.procurement_benchmark_v1
.venv/bin/python -m pytest tests/test_procurement_benchmark.py -q
.venv/bin/ruff check workflows/national/harvest/procurement_benchmark_v1.py tests/test_procurement_benchmark.py
```

Outputs under `data/derived/national/harvest_basis/procurement_benchmark/`:

```text
qc_procurement_cost_benchmark.csv
qc_procurement_regional_range.csv
bmmb_historical_benchmark.csv
summary.json
PROCUREMENT_BENCHMARK_REPORT.md
manifest.json
```

## 8. Non-claims and use rules

- Not a QC net value, not a national net value, and not `policy_grade`.
- Not an observed transaction, mill margin, stumpage rate, or BMMB current price.
- Do not merge these rows into `forest_cost_revenue.csv`, the Forest LP, or the UI.
- Do not multiply the spread by a harvest volume and call it a monetary net value.
- Do not overwrite the old artifact; use `procurement_benchmark/` and the
  `DEPRECATED.md` pointer for audit.

## 9. What remains for v2d

BC net value remains `not_estimated` with reason
`missing_logging_and_haul_cost`. The QC reference in v2d must now be read as a
procurement cost spread, not a net value. No BC partial net value and no combined
national net value are produced.
