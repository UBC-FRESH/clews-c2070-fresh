# Energy decision-impact matrix v1

| Field | Value |
|---|---|
| Status | Model/screening matrix; not observed, not policy grade |
| Reference case | 119 Mm3 planning allocation |
| Artifact | `data/derived/national/facilities/energy_decision_matrix_v1/` |
| Module | `workflows/national/facilities/energy_decision_matrix_v1.py` |
| Test | `tests/test_energy_decision_matrix_v1.py` |
| Scope | Read-only; no UI, no official OSeCAN change |

This is the short decision-oriented companion to
`docs/energy-handoff-v4.md`. For each open decision it gives the
reconciled quantity, OSeCAN tier, interpretation, and a screening
default where one is useful. The defaults are not policy and not
preferred cases.

## Decision table

| ID | Decision | Numeric surface | Screening default |
|---|---|---|---|
| D1 | Harvest recovery basis | 0.35 / 0.60 / 0.75 | 0.60 reference-only |
| D2 | Process-residue availability | 0 / 25 / 50 / 75% | 25% conservative non-zero |
| D3 | Biomass/waste incineration | Qualitative, Rubino storage-mix context | No addition until aligned |
| D4 | Industrial feedstock vs energy | Qualitative, Pehl competition context | Report gross, flag un-netted |

## D1 - Harvest recovery basis

| Recovery | Harvest PJ | OSeCAN tier | Relation to T4 |
|---:|---:|---|---|
| 35% | 155.544 | T4 | Within T4 |
| 60% | 266.646 | above_T4 | 53.108 PJ above T4 |
| 75% | 333.308 | above_T4 | 119.770 PJ above T4 |

Harvest gross is 444.411 PJ. The 35% case moved T3 to T4 in v4 via the
coefficient change. The 0.60 factor is a reference value, not a
calibrated recovery factor.

## D2 - Process-residue availability

| Availability | Process PJ | OSeCAN tier | Relation to T4 |
|---|---:|---|---|
| 0% | 0.000 | T1 | Zero anchor |
| 25% | 124.688 | T3 | Within T3 |
| 50% | 249.375 | above_T4 | 35.837 PJ above T4 |
| 75% | 374.063 | above_T4 | 160.525 PJ above T4 |

Process residue at 100% is 498.751 PJ, shown for scale only and not a
matrix row. Process and harvest are separate streams and are never summed.

## D3 - Biomass and waste incineration

Qualitative only. If OSeCAN includes municipal solid waste, agricultural
residue, construction/demolition wood, or black liquor in the same
biomass/waste stack, forestry tier positions are not directly comparable
unless feedstock and technology denominators are aligned. The storage mix
can shift, and mill process residue counted both as supply and internal
mill energy would double count. No quantity is invented.

## D4 - Industrial feedstock versus energy

Qualitative only. Chemicals, pulp, panel, pellet, liquid fuels, and other
industrial demands can compete for the same residue. The facility chain
does not net out internal mill consumption or existing industrial or
chemical contracts. Report process residue gross and flag it as un-netted
until the OSeCAN demand stack and accounting boundary are supplied. No
quantity is invented.

## Recommendation and labels

Use the full ranges first. If a single screening case is required, use
0.60 for D1 as a reference-only case and 25% for D2 as a conservative
non-zero case. Both are model/screening conveniences, not policy
recommendations, not preferred cases, and not calibrated values.

All rows carry `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`, and
`official_osecan_changed=false`. The 119 Mm3 case is a planning
allocation, not a legal AAC, sustainable-supply, or policy determination.
Economics v2.3 is context only; no cost-based ranking is applied.

## Artifact

The machine-readable matrix, summary, manifest, and full report are in
`data/derived/national/facilities/energy_decision_matrix_v1/`.
Reproduce with:

```bash
.venv/bin/python -m workflows.national.facilities.energy_decision_matrix_v1
.venv/bin/python -m pytest tests/test_energy_decision_matrix_v1.py -q
```

The full report is reproduced below for convenience.

---

# Energy decision-impact matrix v1 (v4 handoff)

| Field | Value |
|---|---|
| Prepared | 2026-11-23 |
| Status | Model/screening matrix; `model_estimate`, not observed, not policy grade |
| Reference case | 119 Mm3 planning allocation; harvest 118,977,339 m3 |
| Source | `forestry_osecan_handoff_v8`, `national_residue_curve_v7`, `national_facility_chain_scenarios_v7` |
| Evidence labels | `model_estimate=true`; `observed=false`; `policy_grade=false`; `national_claim=false` |
| Scope | Read-only; no UI, no official OSeCAN change, no policy target |

This matrix turns the four open questions in `docs/energy-handoff-v4.md`
into a decision-impact table. It does not choose a policy case. D1 and D2
reconcile exactly to the v4 handoff at the 119 Mm3 planning allocation.
D3 and D4 are qualitative because the required OSeCAN feedstock and demand
denominators are not in the handoff; no number is invented for them.

## 1. Decision summary

| ID | Decision | Numeric surface | Screening default | Role |
|---|---|---|---|---|
| D1 | Harvest recovery basis | 0.35 / 0.60 / 0.75 | 0.60 reference-only | Not policy; not calibrated |
| D2 | Process-residue availability | 0 / 25 / 50 / 75% | 25% conservative non-zero | Not policy; not an availability estimate |
| D3 | Biomass/waste incineration | Qualitative | No addition to forestry matrix | Rubino storage-mix context |
| D4 | Industrial feedstock vs energy | Qualitative | Report gross, flag un-netted | Pehl competition context |

The defaults are screening conveniences, not preferred cases. The v4
handoff says to report full ranges until the definitions are answered.

## 2. D1 - Harvest recovery basis

Harvest residue is origin-province only and includes CFS branch and
foliage dry mass. Harvest gross is 444.411 PJ at 119 Mm3.

| Recovery | Harvest PJ | OSeCAN tier | Relation to T4 | Interpretation |
|---:|---:|---|---|---|
| 35% recovery | 155.544 | T4 | Within the adopted T4 band. | Lower screening case. Within T4 after the v3.2-to-v4 move from T3; the change is a coefficient effect, not a new recovery measurement. |
| 60% recovery | 266.646 | above_T4 | Exceeds T4 upper bound (213.538 PJ) by 53.108 PJ. | Reference case only. Exceeds the T4 upper bound; 0.60 is not a calibrated recovery factor and has no measured provenance. |
| 75% recovery | 333.308 | above_T4 | Exceeds T4 upper bound (213.538 PJ) by 119.770 PJ. | Upper screening case. Exceeds T4 by the widest margin; useful to show how much the branch/foliage and recovery definition can move the interface value. |

The 35% case is T4; 60% and 75% are above T4. The v3.2-to-v4 move for
35% (T3 to T4) is a coefficient effect, not a recovery measurement.

## 3. D2 - Process-residue availability

Process residue is the larger stream: 498.751 PJ at 100% availability.
The matrix carries the four screening cases below.

| Availability | Process PJ | OSeCAN tier | Relation to T4 | Interpretation |
|---|---:|---|---|---|
| 0% availability | 0.000 | T1 | Within the adopted T1 band. | Degenerate zero case. It is not a physical process-residue estimate and only anchors the availability sweep at T1. |
| 25% availability | 124.688 | T3 | Within the adopted T3 band. | Lowest non-zero case and the only non-zero process case inside the adopted T1-T4 range (T3). |
| 50% availability | 249.375 | above_T4 | Exceeds T4 upper bound (213.538 PJ) by 35.837 PJ. | Midpoint screening case. Exceeds the T4 upper bound; one availability assumption dominates the comparison with the 2050 energy context. |
| 75% availability | 374.063 | above_T4 | Exceeds T4 upper bound (213.538 PJ) by 160.525 PJ. | Upper screening case. Exceeds T4 by the widest margin; the quantity is a model estimate, not observed mill availability. |

The 25% case is T3; 50% and 75% are above T4. One availability
assumption can dominate the comparison with the 2050 energy context.

## 4. D3 - Biomass and waste incineration (qualitative)

If municipal solid waste, agricultural residue, construction and demolition wood, or black liquor share the same combustion or storage capacity, the forestry tier positions are not directly comparable. Adding those feedstocks can change the storage mix and dilute forestry residue's share; counting mill process residue both as supply and as internal mill energy would double count. No quantity is assigned here because the OSeCAN feedstock and technology denominators are not in the handoff.

**Screening boundary.** Keep non-forestry feedstocks outside the forestry matrix until the OSeCAN biomass/waste technology list, feedstock definitions, and internal mill-use boundary are supplied. Do not add a forestry tier to a mixed biomass/waste stack.

## 5. D4 - Industrial feedstock versus energy (qualitative)

Chemicals, pulp, panel, pellet, liquid fuels, and other industrial demands can compete with energy for the same residue. The facility chain assigns process residue by class and province but does not net out internal mill consumption or existing industrial/chemical contracts. If OSeCAN treats those volumes as committed, available energy residue is lower than the handoff total; if OSeCAN adds new chemical or biofuel demand, the same residue may be counted twice. No quantity is assigned here.

**Screening boundary.** Report process residue gross and flag industrial/chemical demand as un-netted until the OSeCAN demand stack and mill self-use boundary are supplied. Do not double count the same residue as both industrial feedstock and energy supply.

## 6. Reconciliation to the v4 handoff

| Check | Result |
|---|---|
| D1 values | 155.544 / 266.646 / 333.308 PJ |
| D1 tiers | T4 / above_T4 / above_T4 |
| D2 values | 0.000 / 124.688 / 249.375 / 374.063 PJ |
| D2 tiers | T1 / T3 / above_T4 / above_T4 |
| Harvest gross | 444.411 PJ |
| Process at 100% (not a matrix row) | 498.751 PJ |
| T4 upper bound | 213.538 PJ |

Process and harvest remain separate streams and are never summed in this
matrix. Total gross (943.162 PJ) is not an OSeCAN row.

## 7. Labels, limitations, and non-claims

- All rows are `model_estimate=true`, `observed=false`, `policy_grade=false`,
  `national_claim=false`, and `official_osecan_changed=false`.
- The 119 Mm3 case is a planning allocation, not a legal AAC or a
  sustainable-supply determination.
- F1/D1 recovery factors are equal-status scenarios; 0.60 is a reference
  value only. D2 availability is a screening range, not an OSeCAN parameter.
- Economics v2.3 is read as context only; no cost, margin, or investment
  ranking is applied to any decision row.
- D3 and D4 are qualitative. No non-forestry feedstock quantity, storage
  volume, industrial demand quantity, price, or technology share is
  invented.
- This is not an official OSeCAN result, an operating plan, a policy target,
  or an endorsement by the energy team. No official OSeCAN file was changed.

## 8. Reproduce

```bash
.venv/bin/python -m workflows.national.facilities.energy_decision_matrix_v1
.venv/bin/python -m pytest tests/test_energy_decision_matrix_v1.py -q
```

Artifact directory: `data/derived/national/facilities/energy_decision_matrix_v1/`.
Companion memo: `docs/energy-decision-matrix.md`.
