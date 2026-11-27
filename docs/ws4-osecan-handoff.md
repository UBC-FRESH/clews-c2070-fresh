# WS4-driven OSeCAN handoff (origin-province 119 Mm³)

**Artifact:** `data/derived/national/facilities/forestry_osecan_handoff_ws4/`
**Version:** `1.0.0`
**Status:** pass, read-only screening handoff
**Labels:** `model_estimate=true`, `observed=false`, `policy_grade=false`,
`national_claim=false`, `ws4_derived=true`,
`official_osecan_changed=false`, `chain_v51_unchanged=true`

This is a sidecar handoff built from the WS4-driven 119 Mm³ chain. It does not
replace or mutate frozen chain v5.1, handoff v10, residue curve v9, or the
official OSeCAN tree. No candidate activity limit is written into OSeCAN.

## 1. Basis boundary

The WS4 chain applies the frozen chain-v5.1 per-province product/residue
coefficients to the realised 119 Mm³ WS4 harvest. Its process and harvest
residue are **origin-province** totals.

Handoff v10 process residue is **facility-province** based. The WS4 handoff is
therefore built on the explicit origin-province basis:

- `province_basis=origin_province` on every zone row;
- no direct zone-row comparison against handoff v10;
- comparison only on national residue totals and supply-curve tier positions.

The WS4 chain carries two bases:

| Basis | Purpose | Harvest (Mm³) |
|---|---|---:|
| `scaled_to_119m_control` | Primary control-total basis; one national factor preserves WS4 provincial shares and matches 118.9773391 Mm³ | 118.977339 |
| `ws4_achieved` | Realised WS4 harvest; reported for transparency | 118.977393 |

The realised WS4 harvest overshoots the control total by 53.780 m³ on
1,083,739.05 ha. Both bases are carried in the zone files, case summary, and
tier positions.

## 2. Method

1. Read the WS4 product chain province rows for both bases, excluding the
   `CAN` national row from province sums, and verify province/national
   reconciliation.
2. Validate process-residue and harvest-residue PJ arithmetic with the frozen
   18.5 GJ/ODT lower heating value.
3. Apply the frozen handoff-v10 province-to-zone weights. Each province's
   weights sum to 1; the global sum is 13 because there are 13 provinces and
   13 zones.
4. Apply process availability 0/25/50/75% to the process-residue base.
5. Use the WS4 chain's recoverable harvest-residue columns at 0.35/0.60/0.75;
   the recovery fraction is already embedded in those columns.
6. Evaluate the frozen v5.1 tier thresholds and compare national PJ and tier
   positions with handoff v10.
7. Hash the official OSeCAN tree and the frozen v10/v9/WS4 input trees before
   and after the build; fail on any change. Fail closed on any unexpected `D`.

## 3. National case totals

### `scaled_to_119m_control` (primary)

| Stream | Case | PJ | ODT |
|---|---|---:|---:|
| process | `process_availability_000` | 0.000000 | 0 |
| process | `process_availability_025` | 124.687729 | 6,739,877.228 |
| process | `process_availability_050` | 249.375457 | 13,479,754.455 |
| process | `process_availability_075` | 374.063186 | 20,219,631.683 |
| harvest | `harvest_recovery_035` | 155.543779 | 8,407,771.812 |
| harvest | `harvest_recovery_060` | 266.646477 | 14,413,323.106 |
| harvest | `harvest_recovery_075` | 333.308097 | 18,016,653.883 |

### `ws4_achieved` (secondary)

| Stream | Case | PJ | ODT |
|---|---|---:|---:|
| process | `process_availability_000` | 0.000000 | 0 |
| process | `process_availability_025` | 124.687785 | 6,739,880.274 |
| process | `process_availability_050` | 249.375570 | 13,479,760.548 |
| process | `process_availability_075` | 374.063355 | 20,219,640.822 |
| harvest | `harvest_recovery_035` | 155.543849 | 8,407,775.612 |
| harvest | `harvest_recovery_060` | 266.646598 | 14,413,329.621 |
| harvest | `harvest_recovery_075` | 333.308247 | 18,016,662.026 |

## 4. Zone conservation

The artifact has 13 official zones and 182 zone rows (2 bases × 13 zones × 7
cases). Every case has exactly 13 zone rows. Summed zone PJ and ODT conserve the
national case totals:

- maximum zone/national PJ gap: `1.31e-11` PJ;
- maximum zone/national ODT gap: `7.08e-07` ODT;
- maximum province/zone-back PJ gap: `1.31e-11` PJ.

No `UNMAPPED` zone is introduced, and no zone is fabricated. Zone IDs and names
match `data/derived/national/clews/osecan/zone_crosswalk.csv`.

## 5. Tier comparison

Frozen v5.1 has 21 scenario/stream/case tier positions. Handoff v10's 21
positions match frozen v5.1 exactly: **21/21**.

The WS4 chain is the 119 Mm³ control only. Its 7 case/stream positions are
like-for-like against the 119 Mm³ subset of handoff v10:

| Basis | Positions matched | Tier changes |
|---|---:|---:|
| `scaled_to_119m_control` | 7/7 | 0 |
| `ws4_achieved` | 7/7 | 0 |

The 100/150 Mm³ positions in `tier_comparison_vs_v10.csv` are reference rows
marked `not_available_ws4_chain_119m_only`; no WS4 value is fabricated for
them. The 119 Mm³ exact PJ deltas are:

| Stream | Case | WS4 scaled PJ | WS4 achieved PJ | v10 119m PJ | Scaled delta | Achieved delta |
|---|---|---:|---:|---:|---:|---:|
| process | 25% | 124.687729 | 124.687785 | 124.687729 | -0.000000519 | +0.000055842 |
| process | 50% | 249.375457 | 249.375570 | 249.375458 | -0.000001039 | +0.000111683 |
| process | 75% | 374.063186 | 374.063355 | 374.063188 | -0.000001558 | +0.000167525 |
| harvest | 35% | 155.543779 | 155.543849 | 155.543768 | +0.000010393 | +0.000080702 |
| harvest | 60% | 266.646477 | 266.646598 | 266.646460 | +0.000017817 | +0.000138346 |
| harvest | 75% | 333.308097 | 333.308247 | 333.308075 | +0.000022273 | +0.000172933 |

Process 0% is 0 PJ on both sides and remains T1.

## 6. Integrity evidence

- Official OSeCAN tree: **146 files**, tree SHA-256
  `80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
  **0 changed files**, unchanged before and after.
- Frozen handoff v10, residue curve v9, and WS4 chain: tree hashes unchanged
  before and after.
- No unexpected `D` in the parent or data repository status.
- No UI change, no official OSeCAN change, no frozen chain-v5.1 mutation.
- No candidate activity limit written into OSeCAN.

## 7. Outputs

| File | Content |
|---|---|
| `zone_process_residue.csv` | 104 process zone rows (2 bases × 13 zones × 4 cases) |
| `zone_harvest_residue.csv` | 78 harvest zone rows (2 bases × 13 zones × 3 cases) |
| `case_zone_summary.csv` | 182 combined zone rows (2 bases × 13 zones × 7 cases) |
| `case_tier_positions.csv` | 14 WS4 tier rows (2 bases × 7 cases) |
| `tier_comparison_vs_v10.csv` | 21 frozen-v5.1 reference rows; 7 WS4 comparisons at 119 Mm³ |
| `summary.json` | Totals, conservation, tiers, integrity, validation, caveats |
| `manifest.json` | Input/output hashes and record counts |
| `FORESTRY_OSECAN_HANDOFF_WS4.md` | Artifact report |

## 8. Caveats

- WS4 is a whole-block arithmetic realisation, not a sustainable harvest, AAC,
  policy result, or investment case.
- Origin-province WS4 process residue is not zone-comparable with
  facility-province handoff-v10 process residue. Only national totals and tier
  positions are compared.
- The WS4 chain is 119 Mm³ only; 100/150 Mm³ tier positions are reference-only
  and marked not available.
- Zone weights are frozen screening proxies, not observed sub-provincial
  residue shares and not policy.
- Process and harvest residue remain separate streams and are never summed.
- 0.60 harvest recovery is a reference case, not a calibrated factor.
- No transport, cost, revenue, tenure, capacity-expansion, or policy claim is
  made.

## 9. Reproduce and test

```bash
.venv/bin/python -m workflows.national.facilities.forestry_osecan_handoff_ws4
.venv/bin/python -m pytest tests/test_ws4_osecan_handoff.py -q
```
