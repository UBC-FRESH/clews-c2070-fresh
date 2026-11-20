# Real 119 Mm³ OSeCAN solve via the live FastAPI UI

Date: 2026-11-20 (UTC)
Repo: `clews-c2070-fresh` at `d82c1f8`
Result: **PASS** — live `POST /api/runs` completed a real GLPK solve and
`GET /api/runs/{id}/output` returned **200** with the expected values.

## Model and server

The rebuilt disposable model was already present and valid:

- `runtime/osecan_model` source commit `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44`
- manifest tree hash `28b598518fda5f70438da2c98650e440aeb0bcdd206b8252625cdcdcac467bbe`
- recomputed tree hash matched the manifest; `resolve_model_source()` selected
  `/tmp/osecan_src/.venv/bin/python` and `/tmp/osecan_src/.venv/bin/otoole`
- source tree reports dirty because the three committed UI patches are applied

Server (loopback, runs enabled, temporary runtime root):

```bash
.venv/bin/python -m workflows.ui.server \
  --port 8014 --allow-runs --runtime-dir /tmp/ui_real_run --log-level info
```

`GET /api/health` returned 200 with `"allow_runs": true`.

## Scenario and run

```bash
curl -sS -o /tmp/scenario_post.json -w '%{http_code}\n' \
  -X POST http://127.0.0.1:8014/api/scenarios \
  -H 'Content-Type: application/json' \
  --data-binary @workflows/ui/examples/screening_119m.json
# 201

curl -sS -X POST http://127.0.0.1:8014/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"scenario_id":"screening_119m"}'
# 202
```

- Scenario: `screening_119m` (119 Mm³, 0.60 recovery, tiered supply)
- Run ID: `20261120t022755z-733826b3`
- Enqueued: `2026-11-20T02:27:55+00:00`
- Finished: `2026-11-20T02:31:37+00:00`
- Wall time: **222 s (3 m 42 s)**; runner exit code 0

SSE subscription:

```bash
curl -sS -N http://127.0.0.1:8014/api/runs/20261120t022755z-733826b3/events
```

The stream delivered `started` (seq 1), `log` (seq 2), and the terminal
`completed` event (seq 3) at `02:31:37`. Heartbeats were also observed.

## Output endpoint

```bash
curl -sS -w '%{http_code} %{time_total}\n' \
  http://127.0.0.1:8014/api/runs/20261120t022755z-733826b3/output
# 200 0.062
```

Response size: 155,547 bytes. Top-level `status` was `completed`,
`runner_status` was `completed`, `dry_run` was false, `screening_only` was true,
and `policy_grade` was false.

Metrics (all as returned by the endpoint):

| Metric | Value | Expected |
|---|---:|---:|
| Objective | **1,411,447.21 M$** | 1,411,447.21 |
| Emissions total | **0.538720 Mt CO₂** | 0.538720 |
| Emissions 2050 | 0.0 Mt CO₂ | — |
| National BCW 2050 | 152.922679 PJ | — |
| PWRBCW 2050 | **41.108217 PJ** | present, plausible |
| PWRBIO 2050 | **162.608568 PJ** | present, plausible |
| Bio share 2050 | **0.050000036** (5.0000036%) | matches 5% PWRBIO cap |
| Solver | `optimal` | optimal/feasible |
| Solver size | 279,451 rows × 237,600 cols | — |

## Dispatch rows and `other` handling

- Raw `dispatch_by_zone.csv`: **16,301 data rows** (16,302 lines including header).
- Raw categories: `other` 12,554; `dispatch` 3,503; `supply` 244.
- Endpoint result: `row_count` **3,747**, `categories` `["dispatch", "supply"]`,
  units `{"dispatch": "PJ electricity", "supply": "PJ fuel"}`.
- The 12,554 `category="other"` rows were skipped without error:
  `16,301 − 12,554 = 3,747`.
- `PWRBCW*` and `PWRBIO*` technologies are present in the parsed output; the
  2050 sums are the metric values above.

No `500`, schema, unit, or unknown-category errors occurred. The only non-200
seen in the server log was an incidental `GET /api/runs/` 404 caused by an empty
run-id variable in a polling command; status and output requests with the real
run ID returned 200.

## Verdict

The critical fix is verified end to end through the live server: real solve,
SSE terminal `completed`, and a 200 output endpoint that handles the 16,301-row
CSV and its 12,554 `other` rows while returning the required objective,
emissions, PWRBCW/PWRBIO, and bio-share values.

No data files were changed. Run artifacts remain under
`/tmp/ui_real_run/runs/20261120t022755z-733826b3/`.
