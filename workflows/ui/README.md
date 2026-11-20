# CLEWS/C2070 live scenario API

Milestone 1a provides the versioned scenario contract and the FastAPI
application skeleton for the live scenario runner. It does **not** run OSeCAN.
The current runner only validates a scenario and writes
`status: "not_implemented"`; milestone 1b replaces the runner body.

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
| `constraints.set_id` | non-empty constraint family, e.g. `screening_v0` |
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
| `GET` | `/api/scenarios` | `{"scenarios": [...]}` |
| `GET` | `/api/scenarios/{id}` | one scenario; 404 if missing, 400 for an unsafe ID |
| `POST` | `/api/scenarios` | validate and atomically write `runtime/ui/scenarios/{id}.json`; 201 create, 200 overwrite, 409 duplicate unless `?overwrite=true`, 422 contract error |
| `GET` | `/api/runs` | `{"runs": [...]}`, newest first |
| `POST` | `/api/runs` | body `{"scenario_id": "..."}`; 202 enqueue, 404 unknown scenario, 403 unless `--allow-runs`, 422 bad body |
| `GET` | `/api/runs/{id}` | persisted job record |
| `GET` | `/api/runs/{id}/events` | SSE stream of `started`, `log`, terminal status events, with heartbeat comments |

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

## Runner contract for milestone 1b

`workflows/ui/run_scenario.py` already validates `clews.scenario.v1` and writes
`{out}/run_summaries.json` atomically. Milestone 1b must preserve:

- arguments `--scenario PATH` and `--out DIR`;
- summary keys `schema_version`, `status`, `scenario_id`, `scenario_path`,
  `generated_utc`, `message`;
- exit code `0` for a completed job and non-zero on operational failure;
- no solved output unless the real OSeCAN solve actually ran.

The server derives the job terminal state from the summary `status` on exit 0
(`completed` and `not_implemented` are accepted); a non-zero exit is always
`failed`.

## Tests

```bash
.venv/bin/python -m pytest -q tests/test_ui_scenario_api.py
```

Tests use FastAPI `TestClient`, temporary runtime directories, and a fake
runner subprocess. They cover contract rejection, atomic create/overwrite,
duplicate 409, enqueue/status/SSE, read-only 403, restart interruption, and path
traversal rejection.
