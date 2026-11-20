# CLEWS/C2070 live scenario API

Milestone 1a provides the versioned scenario contract and the FastAPI
application skeleton. Milestone 1b adds the real OSeCAN adapter behind the same
CLI contract. Milestone 1c wires the browser UI to this API, adds constraint
defaults and normalized output endpoints, and keeps the static UI usable when no
server responds. Results are labelled `screening_only`; they are not
policy-grade.

The default canonical constraint family is `screening_v0`, stored in
`data/derived/national/clews/osecan/canonical_constraint_set.json`. It has no
reserve-margin, transmission, curtailment, or build-rate constraints. The
Stage-1 draft `policy_test_v1` is a separate set in
`data/derived/national/clews/osecan/policy_test_v1.json` with a national
peak-proxy reserve margin, capacity credits, build rates, and explicit caps;
`status=draft`, `policy_grade=false`, and it is not policy-grade.

## Install and run

```bash
.venv/bin/python -m pip install -e ".[web]"
.venv/bin/python -m workflows.ui.server --port 8013
```

The default bind is `127.0.0.1:8013`. Enqueueing runs is disabled unless the
server is started with `--allow-runs`:

```bash
.venv/bin/python -m workflows.ui.server --port 8013 --allow-runs
```

Open `http://127.0.0.1:8013/` for the existing static forest-ledger UI. The API
is rooted at `/api`; static files never shadow API routes.

Read-only mode rejects all `POST`, `PUT`, `PATCH`, and `DELETE` requests with
HTTP 403:

```bash
CLEWS_UI_READ_ONLY=1 .venv/bin/python -m workflows.ui.server --port 8013
# or
.venv/bin/python -m workflows.ui.server --port 8013 --read-only
```

`CLEWS_UI_RUNTIME_DIR` overrides the default `runtime/ui/` writable root. Runtime
state is local and is not committed (the repository ignores `runtime/`).

## Scenario contract: `clews.scenario.v1`

`workflows/ui/contracts.py` is the source of truth. Every nested model rejects
unknown fields; parsed scenarios are immutable.

| Field | Type / values |
|---|---|
| `scenario_id` | lowercase slug, `^[a-z0-9][a-z0-9_-]{0,63}$` |
| `label` | non-empty string, max 200 chars |
| `description` | string, default `""`, max 4000 chars |
| `schema_version` | literal `clews.scenario.v1` |
| `harvest.level_m3_per_year` | positive m³/year |
| `harvest.path` | `observed`, `hold`, `low`, `high`, `custom` |
| `harvest.recovery_fraction` | `(0, 1]` |
| `harvest.cost_basis` | `adopted_rest_of_canada_curve`, `base`, `low`, `high`, `custom` |
| `harvest.supply_curve` | `unified`, `tiered` |
| `energy.carbon_price_schedule` | non-empty list of `{year, price_usd_per_tco2e}`; years 2021–2070, unique, sorted on parse; prices ≥ 0 |
| `energy.hydro_mode` | `none`, `cap_2021`, `fixed_2021` |
| `energy.pwr_bio_cap_fraction` | `[0, 1]` |
| `energy.nuclear_cap_gw` | ≥ 0 |
| `energy.wind_cost_usd_per_gj` | ≥ 0 |
| `energy.wind_cap_fraction` | `[0, 1]` |
| `energy.t3_cap_pj`, `energy.t4_cap_pj` | ≥ 0 PJ |
| `energy.generic_bio_cost_usd_per_gj` | ≥ 0 |
| `constraints.set_id` | `screening_v0` or `policy_test_v1`; unknown IDs are rejected |
| `provenance.base_data_commit` | non-empty string |
| `provenance.model_commit` | non-empty string |
| `provenance.created_utc` | ISO-8601 timestamp with timezone; normalized to UTC |

The carbon schedule is piecewise constant: each point applies from its `year`
until the next point. Include a 2021 point if 2021–2025 must be explicit.

Example:

```json
{
  "scenario_id": "screening_150m_base",
  "label": "150 Mm³ screening base",
  "description": "Milestone 1a contract example; no solved output.",
  "schema_version": "clews.scenario.v1",
  "harvest": {
    "level_m3_per_year": 150000000.0,
    "path": "high",
    "recovery_fraction": 0.6,
    "cost_basis": "adopted_rest_of_canada_curve",
    "supply_curve": "tiered"
  },
  "energy": {
    "carbon_price_schedule": [
      {"year": 2021, "price_usd_per_tco2e": 95.0},
      {"year": 2026, "price_usd_per_tco2e": 200.0}
    ],
    "hydro_mode": "cap_2021",
    "pwr_bio_cap_fraction": 0.05,
    "nuclear_cap_gw": 0.0,
    "wind_cost_usd_per_gj": 3.33,
    "wind_cap_fraction": 0.2,
    "t3_cap_pj": 200.0,
    "t4_cap_pj": 0.0,
    "generic_bio_cost_usd_per_gj": 3.0
  },
  "constraints": {"set_id": "screening_v0"},
  "provenance": {
    "base_data_commit": "4be339d",
    "model_commit": "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44",
    "created_utc": "2026-11-20T00:00:00+00:00"
  }
}
```

## API

| Method | Path | Behaviour |
|---|---|---|
| `GET` | `/api/health` | status, schema version, read-only and allow-runs flags |
| `GET` | `/api/constraints` | both `screening_v0` and draft `policy_test_v1` with `status`, `policy_grade`, availability, notes, and shared UI `defaults` |
| `GET` | `/api/scenarios` | `{"scenarios": [...]}` |
| `GET` | `/api/scenarios/{id}` | one scenario; 404 if missing, 400 for an unsafe ID |
| `POST` | `/api/scenarios` | validate and atomically write `runtime/ui/scenarios/{id}.json`; 201 create, 200 overwrite, 409 duplicate unless `?overwrite=true`, 422 contract error |
| `GET` | `/api/runs` | `{"runs": [...]}`, newest first |
| `POST` | `/api/runs` | body `{"scenario_id": "..."}`; 202 enqueue, 404 unknown scenario, 403 unless `--allow-runs`, 422 bad body |
| `GET` | `/api/runs/{id}` | persisted job record |
| `GET` | `/api/runs/{id}/events` | SSE stream of `started`, `log`, terminal status events, with heartbeat comments |
| `GET` | `/api/runs/{id}/output` | normalized columnar output from `run_summaries.json` + `dispatch_by_zone.csv`; 404 if absent, 409 while queued/running |

`/api/runs/{id}/output` returns the static UI column shape (`technology`,
`category`, `zone`, `year`, `value_pj` column arrays, `categories`,
`units_by_category`) plus `summary`, `metrics`, `screening_only`, and
`policy_grade`. It never invents rows for a failed or dry run: absent dispatch is
HTTP 404.

For tests and QA, the server accepts an explicit runner command while keeping
the scenario snapshot contract:

```bash
.venv/bin/python -m workflows.ui.server --port 8013 --allow-runs \
  --runner-command "/path/to/.venv/bin/python /tmp/fake_runner.py"
```

The server still appends `--scenario {run}/scenario.json --out {run}/output`, so
a fake runner sees the immutable snapshot exactly like the real
`workflows.ui.run_scenario` command.

Scenario writes use a sibling temporary file, `fsync`, and `os.replace()`. Job
writes use the same atomic JSON helper. A run directory is
`runtime/ui/runs/{run_id}/` containing `job.json`, `events.jsonl`, and an
`output/` directory passed to the runner.

At server construction/startup, any job left `queued` or `running` is marked
`interrupted`, because no child process survives a server restart. Job terminal
states are `completed`, `failed`, `interrupted`, and `not_implemented`.

`POST /api/runs` only spawns:

```bash
python -m workflows.ui.run_scenario \
  --scenario runtime/ui/scenarios/{id}.json \
  --out runtime/ui/runs/{run_id}/output
```

`stdout` and `stderr` (merged) are streamed into `events.jsonl`; the process is
never solved inside the request process.

## Runner contract (milestone 1b)

`workflows/ui/run_scenario.py` validates `clews.scenario.v1`, builds a
disposable OSeCAN parameter set, runs `osecan harmonize`, and optionally solves.
The CLI preserves:

- arguments `--scenario PATH` and `--out DIR`, plus `--dry-run` and
  `--model-root PATH`;
- summary keys `schema_version`, `status`, `scenario_id`, `scenario_path`,
  `generated_utc`, `message`;
- exit code `0` for a completed job (including a dry run) and non-zero on
  operational failure;
- no solved output unless the real OSeCAN solve actually ran.

The server derives the job terminal state from the summary `status` on exit 0
(`completed` and `not_implemented` are accepted); a non-zero exit is always
`failed`.

Example dry run and real screening run:

```bash
.venv/bin/python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out /tmp/screening_119m_dry --dry-run

.venv/bin/python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out /tmp/screening_119m_real --model-root runtime/osecan_model
```

### Reproducible model rebuild

The public `deltae/C2070` tree is not vendored. Build the disposable copy from
the public base commit and the committed patches:

```bash
.venv/bin/python -m workflows.ui.setup_osecan_model \
  --base /path/to/C2070 \
  --out runtime/osecan_model \
  --python /path/to/osecan/.venv/bin/python \
  --external-data /path/to/C2070/data/external
```

Omit `--base` to clone `https://github.com/deltae/C2070.git`. The script checks
out `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44`, applies
`workflows/ui/patches/01_emission_factors.patch`,
`02_zonal_bc.patch`, `03_tiered_supply.patch`, and `04_capacity_bounds.patch`
in order, verifies each patch SHA-256, runs `osecan build-sets`,
`osecan build-params`, `osecan clean-demand
"Current Measures"`, `osecan clean-capacity --scenario "Current Measures"
--base-year 2021`, and `osecan harmonize`, and writes `model_manifest.json`
with the source commit, patch hashes, demand/capacity provenance, and a
deterministic model-tree hash.

The CER Energy Futures 2026 end-use demand file is pinned by SHA-256. It is
copied from `--external-data` when that cache exists and otherwise downloaded
from the public CER URL. `--skip-demand` keeps the public base's 10 PJ
placeholder demand for structure-only checks.

Capacity is always ingested. The pinned CER EF2026 electricity-capacity file
(SHA-256 `8db7b814...f5fcc`) supplies the observed 2021 fleet only: the cleaner
writes `ResidualCapacity.csv` for every year 2021-2070 and the four
`TotalAnnual*Capacity*` bounds. The generated base has 70 non-zero `PWR*` rows
in 2021 totalling 145.02 GW, biomass carried by `PWRBIO` with `PWRBSW`/`PWRBCW`
at zero, coal phased out by 2030, and explicit build-rate rows for hydro, solar,
wind, and nuclear. Dropped oil (6.0 GW) and battery (0.1 GW) capacity is printed
by the cleaner and recorded in `capacity.dropped_report` in the manifest rather
than silently discarded.

The setup fails closed on a commit mismatch, patch-hash mismatch, unexpected
patched file, missing `SUPBCWT1`-`T4` / `SUPBCWB1`-`B2` structure, raw
fuel-basis emission factor, placeholder demand when demand was requested,
missing 2021-2070 residual-capacity coverage, a 2021 fleet total other than
145.02 GW, missing capacity-limit files, or harmonization failure. The runner
also verifies the manifest tree hash when one is present.

### Model source

The runner never edits its source model. It selects:

1. `--model-root PATH`;
2. `runtime/osecan_model` (the default).

There is no `/tmp` fallback. If the default tree is missing or fails its
manifest check, the runner fails with a setup instruction; it never fabricates
dispatch or objective values. A selected copy must contain the zonal/tier SETs,
the corrected output-basis emission factors, `resources/osemosys_fast_osecan.txt`,
and a usable OSeCAN Python environment. The manifest records the interpreter and
`otoole` path used by the setup, and the runner reads them from there.

### Generated parameters

Supply is generated from the scenario harvest path and the adopted RSYC basis:

```text
total_m3 = log_m3 / 0.762716956451988
ODT      = total_m3 * 0.08831776452936206
PJ       = ODT * recovery_fraction * 18.5 / 1e6
```

The BC zonal caps come from the adopted `SUPBCWB1` / `SUPBCWB2` CSVs. The
rest-of-Canada cap is `national - BC zonal`; it is split into `SUPBCWT1`-`T4`
incremental caps and the runner fails closed unless the tier sum equals the
rest-of-Canada cap in every year. `energy.t3_cap_pj` and `energy.t4_cap_pj`
limit the two upper tiers. `harvest.path` `observed`, `hold`, `low`, and `high`
use the committed reference paths; `custom` uses `level_m3_per_year`.

The runner also emits `PWRBIO<z>` caps from the documented 2050 zonal reference,
`PWRWND<z>` caps and variable costs, `PWRURN<z>` capacity caps, hydro upper/lower
bounds, `RNWBIO` cost, and the carbon-price schedule. `nuclear_cap_gw` caps new
build through `TotalAnnualMaxCapacityInvestment`; `TotalAnnualMaxCapacity` is
floored at the exogenous 2021 residual fleet so a zero new-build cap retires
nuclear by attrition instead of making the observed units infeasible.

### Outputs

A successful run writes:

- `{out}/run_summaries.json` — normalized summary and metrics;
- `{out}/dispatch_by_zone.csv` — long-format dispatch, supply, and other rows;
- `{out}/manifest.json` — scenario, constraint set, model provenance, and
  artifact hashes;
- `{out}/supply_caps.json` — harvest conversion and tier/zonal reconciliation;
- `{out}/model/` — disposable model copy;
- `{out}/run/` — parameter set, solver status, result CSVs, and logs.

`--dry-run` writes the summary, manifest, parameter set, and harmonize evidence
but no dispatch or solver status. Its summary has `dry_run: true`.

### Reproducibility gap

The source model is a disposable copy of public `deltae/C2070` commit
`40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` with two local changes that are not
committed upstream:

1. the corrected emission-factor patch in
   `data/derived/national/clews/osecan/emission_factor_fix/emission_factor_fix.patch`;
2. the zonal BC and `SUPBCWT1`-`T4` structure installed in the model SETs.

`manifest.json` records the source commit, dirty flag, and a SHA-256 hash of the
copied reduced tree. This is enough to identify the copy but not to rebuild the
public model from a clean clone; the model patch/setup remains a reproducibility
gap until it is published as a deterministic preparation script or branch.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_ui_scenario_api.py tests/test_ui_osecan_runner.py
```

Tests use FastAPI `TestClient`, temporary runtime directories, and a fake
runner subprocess. They cover contract rejection, atomic create/overwrite,
duplicate 409, enqueue/status/SSE, read-only 403, restart interruption, path
traversal rejection, constraints/`screening_v0` + `policy_test_v1` defaults,
harvest feasibility gate, normalized output
(including 409 active and 404 absent cases), the CLI `--runner-command` override
preserving the scenario snapshot, canonical-set validation, 100/119/150/200 Mm3
cap math, tier-sum/no-double-count checks, and a real-model dry run when a
disposable OSeCAN copy is present. A full 119 Mm3 solve is run manually, not in
the fast test lane.
