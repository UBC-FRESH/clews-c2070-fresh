# Fleet-correction canonicalization (2026-11-20)

## Decision

The CER 2021 capacity-fleet rerun `screening_119m` is the canonical screening
result. It is exported as `fleet_capacity__screening_119m_fleet` from the data
artifact `data/derived/national/clews/osecan/fleet_capacity_119m/`:

- objective: **1,445,655.44 M$**
- cumulative emissions (2021–2070): **424.710127 Mt CO2**
- 2050 emissions: **0.0 Mt CO2**
- 2050 dispatch output: supplied by the same run's `dispatch_by_zone.csv`

Provenance is limited to `run_summaries.json`, `dispatch_by_zone.csv`, and
`manifest.json`. Raw model trees and `run/results_csv` are intentionally not
committed.

## Supersession

Every scenario exported before this artifact — all 15 legacy families,
including the emission-factor-fix, PWRBIO, full-constraint, and national supply
curve runs — carries `superseded_for_fleet=true`. These rows remain visible in
the static UI and source ledger as historical audit, but they are excluded from
default trade-off/Pareto eligibility and must not be compared with the
fleet-corrected run. `emission_factor_fix` rows retain their existing
`superseded_for_emissions=false` status; fleet supersession is a separate flag.

The canonical row is:

- `fleet_capacity__screening_119m_fleet`
- `fleet_corrected=true`, `canonical=true`
- `publishable=true`, `screening_only=true`, `policy_grade=false`
- sole default trade-off/Pareto member

## Schema mapping

The fleet `run_summaries.json` is a flat summary rather than a `runs`/`cases`
table. The exporter treats a metrics-bearing flat summary as a single run:

- `objective_musd` → `metrics.objective`
- `emissions_total_mt` → `metrics.emissions_total`
- `emissions_2050_mt` → `metrics.emissions_2050`
- `supply.national_total_pj_2050` → `metrics.national_bcw_2050`
- `PWRBCW*` 2050 dispatch → `metrics.pwrbcw_2050`
- `PWRBIO* / PWR*` 2050 dispatch → `metrics.bio_share_2050`
- summary `scenario_id` (`screening_119m`) → dispatch run key
- artifact case key → `screening_119m_fleet`

The fleet dispatch records 12,977 demand/audit rows with `category=other`.
Those rows are excluded from the supply-side columnar output (4,215 rows
remain: 3,965 dispatch + 250 supply) and the exclusion count is reported in
`outputs/*.json` as `excluded_categories`.

## Open gaps

- Rerun the 26 formerly publishable/pre-fleet scenarios under the fleet model;
  they remain historical until then.
- Stage 1 / `policy_test_v1` has not been run; this result remains
  `screening_only=true` and `policy_grade=false`.
