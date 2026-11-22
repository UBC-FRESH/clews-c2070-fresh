# National evidence tiers and source replacement order

| Field | Value |
|---|---|
| Document | `planning/national-evidence-tiers.md` |
| Date | 2026-11-22 |
| Status | Active for `national_economics_v1` coverage audits |
| Applies to | `data/derived/national/harvest_basis/national_tier_coverage/` |
| National label | `model_estimate_not_policy` |

## 1. Purpose

This note defines how the national economics scaffold is scored for evidence
coverage and how missing provincial sources are ordered for replacement. It is
a **coverage** rule, not a new price, cost or margin model.

The audit unit is a **coverage cell**: one province (or territory) crossed with
one component:

```
price, logging, haul, road, silviculture
```

Logging and haul are separate coverage cells even though `national_economics_v1`
publishes one combined logging+haul distribution. The audit does not invent a
logging/haul cost split. Both cells inherit the combined distribution's tier and
source, and both are labelled `component_basis=logging_haul_combined_distribution`.

## 2. Tier score

| Tier | Score | Meaning | Counts as direct/reported? |
|---|---:|---|---|
| `reported_average` | 4 | Published provincial average or published CAD/m3 rate used for the province that reports it | Yes |
| `proxy_model` | 3 | Derived model or distribution, not an observed provincial cost | No |
| `donor_proxy` | 2 | Another jurisdiction's value used for the recipient | No |
| `assumption` | 1 | Explicit index, elasticity, conversion rule or placeholder | No |

Only `reported_average` may be called direct or reported evidence. A published
donor value (for example a QC road allowance used in BC) remains
`donor_proxy` for the recipient. `proxy_model` remains a model even when it is
built from published inputs. No audit may promote a donor or proxy to
`reported_average`.

## 3. Aggregation rules

### 3.1 Cell counts and shares

For tier \(t\):

```
cell_count(t)  = number of province x component cells with tier t
cell_share(t)  = cell_count(t) / (13 x 5)
```

There are 65 expected cells. A missing cell, duplicate cell, unresolved source
ID or unsupported tier fails the build. **0 silent gaps** is a hard requirement.

### 3.2 Volume-weighted direct/reported share

Let \(V_p\) be the 119 Mt provincial allocation harvest volume, \(N = 13\), and
\(C = 5\) components. Each cell receives weight \(V_p / (\sum_p V_p \times C)\).
Then:

```
volume_weighted_reported_share
  = sum over reported_average cells of V_p / (sum_p V_p * C)
```

This is the share of province-component volume that is direct/reported. The
denominator deliberately includes all five components, so donor cells cannot be
hidden by averaging only the cells that happen to have evidence.

### 3.3 Volume-weighted evidence score

```
province_score(p) = mean tier score across the five components of p
national_score    = sum_p (V_p / sum_p V_p) * province_score(p)
evidence_ratio    = national_score / 4
```

The five components have equal weight inside a province. No component is
weighted up without a documented reason. `evidence_ratio` is a coverage
indicator, not a probability and not a policy grade.

## 4. Replacement queue order

The replacement queue contains **only** `donor_proxy` and `assumption` cells.
`proxy_model` cells are recorded as second-wave upgrades in the summary but are
not mixed into the donor/assumption queue.

Rows are sorted by:

1. provincial harvest volume, descending (119 Mt allocation);
2. within a province, component order:
   `price`, `logging`, `haul`, `road`, `silviculture`;
3. province code as the final deterministic tie-break.

Priority is volume-based:

| Priority | Rule | Meaning |
|---|---|---|
| `P1` | province share >= 10% of national volume | Replace first |
| `P2` | 2% <= share < 10% | Replace next |
| `P3` | 0 < share < 2% | Replace after P2 |
| `P4` | zero allocation volume | Hold explicit; do not invent a source |

A zero-volume province (NU in the current allocation) still has explicit
coverage cells and queue rows. It ranks last at `P4`; zero volume is not a
reason to delete the cell or claim the gap is closed.

## 5. Required source categories

| Component | Required source category | Minimum acceptable replacement |
|---|---|---|
| `price` | price series | Provincial delivered-log price series with period, basis and grade/species scope |
| `logging` | cost study | Provincial logging cost study with CAD/m3 or a documented CAD/m3 derivation |
| `haul` | cost study | Provincial truck-haul cost study with CAD/m3 or a documented CAD/m3 derivation |
| `road` | road/silviculture rate | Provincial road allowance in CAD/m3; no CAD/km conversion without a published conversion |
| `silviculture` | road/silviculture rate | Provincial silviculture allowance in CAD/m3; no CAD/ha conversion without a published conversion |

A replacement source must resolve to a source ID in the artifact source
register, carry its licence and evidence tier, and preserve the national labels.
Adding a source does not by itself change the national total or upgrade the
result to policy grade.

## 6. Non-negotiable rules

- Do not invent direct evidence. A donor or proxy stays labelled.
- Do not relabel a donor's published value as the recipient province's own data.
- Do not change national margins or recompute them in this audit.
- Do not convert CAD/ha, CAD/km or CAD/each allowances to CAD/m3 without a
  published conversion.
- Do not claim strict-open status for a bundle containing public-reference
  copyrighted inputs.
- Keep `evidence=model_estimate`, `observed=false`, `policy_grade=false`,
  `net_value_status=model_estimate`, `combined_national_net_value=false` and
  the national label `model_estimate_not_policy`.
- Fail closed on any unexpected `D` in parent or `data` git status.

## 7. Reproduction

```bash
.venv/bin/python -m workflows.national.harvest.national_tier_coverage
```

The builder writes:

```
data/derived/national/harvest_basis/national_tier_coverage/
  national_tier_coverage.csv
  national_tier_summary.json
  source_replacement_queue.csv
  TIER_COVERAGE_REPORT.md
  manifest.json
```
