# UI deployment v1 — forestry scenario explorer (track 3)

| Field | Value |
|---|---|
| Document | `planning/ui-deployment-v1.md` |
| Date | 2026-11-20 |
| Track | Track 3 — UI deployment |
| Status | Planning plus track 3a/3b scaffolding: deploy templates, per-instance launcher, and a non-authenticating role flag. No service is deployed or started. |
| Scope | `ui/` static explorer and `workflows/ui/` FastAPI runner |
| Audience | FRESH maintainers, platform operators, Taco Niet's energy-policy team, reviewers |
| Decision | systemd host + Cloudflare Tunnel/Access; two instances; no in-app auth |

**Boundary.** This deployment owns the forestry UI and its provenance. It does
not own OSeCAN structure or energy-policy interpretation; Taco Niet's team owns
the energy model and policy signoff, and OSeCAN remains a black-box interface
test. No skin is an energy-policy design surface.

**Decision summary.** Run v1 as an unprivileged systemd service behind
Cloudflare Tunnel/Access on the FRESH house host. Two instances share one
release: public read-only on a curated runtime, and Access-restricted analyst
with `--allow-runs`. Roll out loopback (v1), authenticated single-tenant (v1.1),
then per-user isolation (v1.2). The app gains no login, tenant model, or
invented C2070 geometry.

**Track 3a implementation note (2026-11-20).** `deploy/` now holds the two
systemd unit templates, a Cloudflare Tunnel ingress example, and the operator
runbook. The server accepts `--role` / `CLEWS_UI_ROLE` to select a deployment
action envelope. This is not authentication: Access remains the identity
boundary, and per-user runtime isolation was the remaining v1.2 deliverable
addressed by track 3b below.

**Track 3b implementation note (2026-11-20).** Per-instance deployment assets
now exist: `deploy/run_instance.sh` creates a `0700` runtime, validates the
instance name/port/role, refuses a busy port or existing instance, and starts
one loopback server; `deploy/clews-ui-analyst@.service` runs one instance per
`%i` with a per-instance service account, `EnvironmentFile`, `0700`
`RuntimeDirectory`/`StateDirectory`, and no secrets. These are deploy-level
process/filesystem controls; the server remains single-tenant within one
runtime and has no in-app accounts or authorization. A local two-instance smoke
test passed with distinct temporary runtimes and ports; no host service was
installed or started. Host/edge acceptance remains open.

## 1. Current architecture and constraints

The UI is one FastAPI/uvicorn process serving a no-build static SPA at `/` and a
versioned JSON/SSE API at `/api`. The server never solves inside the request
process: `POST /api/runs` persists a job and spawns
`python -m workflows.ui.run_scenario` as a subprocess. Job and scenario writes
are atomic JSON, and a per-run `events.jsonl` backs SSE and replay. Cancel and
retry operate on the persisted job; retry reuses the immutable scenario
snapshot.

| Surface | Current state |
|---|---|
| Network | Default `127.0.0.1:8013`; no TLS, auth, rate limit, or CSRF token |
| State | `runtime/ui/scenarios/{id}.json` and `runtime/ui/runs/{run_id}/`; no database |
| Execution | Subprocess, `cwd` = repo root, model at `runtime/osecan_model`; default 2 concurrent runs |
| Modes | `--allow-runs`, `--read-only`, `CLEWS_UI_RUNTIME_DIR`; read-only rejects all POST/PUT/PATCH/DELETE with 403 |
| API | `/api/health`, `/api/constraints`, `/api/scenarios`, `/api/runs`, `/api/runs/{id}/events`, `/api/runs/{id}/output`, plus cancel/retry |
| UI | 53 scenarios, 1 Pareto-eligible canonical run, `policy_test_v1` draft selector, feasibility gate, map drill-down, year playback |
| Spatial | 13 province polygons, 36 ecoprovinces, 385 RSYC tiles; 13 zone nodes, B1/B2 anchors, 11 explicit gaps |

Deployment constraints follow from the implementation:

1. **One process per runtime.** `RunStore` and `RunSupervisor` use in-process
   locks and an in-memory pending queue. Two uvicorn workers, or two services
   sharing one runtime, can race and double-start jobs.
2. **Read-only is not access control.** It blocks mutations but still serves
   every stored scenario and run in that runtime. A public instance needs a
   curated runtime, not the analyst's live directory.
3. **The SPA assumes an origin-root API.** `ui/api.js` sets `BASE = ''` and calls
   `/api/...`; static assets are relative, but API calls are not. A path-prefix
   mount breaks live mode and needs a base-path change plus SSE retest.
4. **Restart is fail-closed, not resumable.** Startup marks every `queued` or
   `running` job `interrupted`; queued work is retried manually. A systemd
   restart is therefore a user-visible event, not a transparent reload.
5. **Hydration and job records expose history.** The SPA loads up to 80
   completed runs and fetches each output; `/api/runs` returns the stored job
   object, including `command`, `scenario_path`, `output_dir`, and `pid`.
   Curate the public runtime and sanitize job records.

## 2. Deployment options

| Option | Fit | Limits | Verdict |
|---|---|---|---|
| JupyterHub local (`jupyter-server-proxy`) | Developer access from a user's Jupyter server | Prefix breaks `/api`; SSE buffering; server sleeps/restarts; no public ingress or ops controls | Development only |
| systemd host | Loopback, journald, restart, cgroup limits; simple single-process contract | Host access and unit maintenance; no built-in tenancy | **Recommended** |
| Container | Reproducible runtime, resource limits, clean rollback | No image today; large model/env; needs volume and ingress; one process per runtime | Defer until an image is maintained |
| Cloudflare Tunnel + Access | Outbound ingress, TLS, WAF/rate limit, CWL/Globus/OTP identity | Edge configuration; not a host; origin must stay loopback | **Recommended ingress** |

**Recommendation.** Use a systemd-managed host behind Cloudflare Tunnel and
Access. This follows the FRESH house pattern — systemd, tunnel, Access,
read-only public mode, lazy-mounted sub-apps, and no auth in the app — without
adding a parallel deployment stack.

Run two units from the same release and never share a runtime between them:

| Instance | Bind | Flags | Runtime | Edge policy |
|---|---|---|---|---|
| Public | `127.0.0.1:8013` | `--read-only` | `/var/lib/clews-ui/public` | Public bypass + WAF/rate limit, or reviewer Access policy |
| Analyst | `127.0.0.1:8014` | `--allow-runs --max-concurrent-runs 2` | `/var/lib/clews-ui/analyst` | Analyst Access group only |

Run as an unprivileged user with the repo read-only, the runtime writable, and
`runtime/osecan_model` readable. Use `ProtectSystem=strict`, `ReadWritePaths=`,
`PrivateTmp=true`, `NoNewPrivileges=true`, `MemoryMax=`/`CPUQuota=` for the
solver, and `PYTHONDONTWRITEBYTECODE=1`. Keep secrets in a root-owned `0600`
environment file, never in the unit or repository.

Give the app its own hostname. The house host may register it as a lazy-mounted
sub-app, but a shared path prefix is not safe today for the reason in §1. If the
platform requires a prefix, make the base-path change and test SSE through
Cloudflare before exposing it.

## 3. Identity, access, and multi-user behavior

### 3.1 Edge identity, no in-app login

Cloudflare Access is the identity layer for restricted hosts: CWL/CILogon,
Globus, or one-time PIN, with groups and policies at the edge. The public host
may be anonymous behind WAF. The app has no user table, login form, session
cookie, role assignment, or password store, and does not validate the Access
JWT. A deployment role flag (`--role` / `CLEWS_UI_ROLE`) selects allowed actions
but does not identify or authorize a caller. Keep origin ports loopback and
firewalled; never expose `8013`/`8014` directly.
Do not trust an unvalidated `Cf-Access-Authenticated-User-Email` header until the
app or an edge shim verifies it.

Access roles below are deployment placeholders; the actual group names and IdP
mappings are configured at deployment:

| Access role / policy | Host | App mode | May mutate |
|---|---|---|---|
| `forestry-public` | Public | Read-only | No |
| `forestry-analyst` | Analyst | Writable | Yes |
| `forestry-model-builder` | Analyst plus host access | Writable plus model rebuild | Yes, via host ops |
| `forestry-energy-modeller` | Read-only host | Read-only | No |
| `forestry-reviewer` | Public | Read-only | No |

### 3.2 Public read-only versus writable analyst

Use separate services, runtimes, hostnames, and ports. The public instance runs
`--read-only` without `--allow-runs` and serves only published scenarios/runs;
its edge policy may be open with WAF/rate limiting, or an OTP allowlist for
reviewers. The analyst instance runs `--allow-runs`, requires Access, and uses
the working runtime. Read-only startup creates no directories and repairs no
jobs, so the curated runtime must be complete before start. Read-only does not
hide data: it does not filter reads, and the SPA hydrates whatever runs it finds.

### 3.3 Per-user runtime directories and job isolation

The current server is single-tenant within one runtime and has no in-app
multi-user isolation. There is
no owner field on scenarios or jobs; any caller admitted to a writable instance
can list, overwrite, cancel, or retry every scenario and run in that runtime.
`POST /api/scenarios?overwrite=true` can replace another user's scenario, and
cancel/retry accept any run ID. A shared writable instance is suitable only for
a small trusted group in v1.1.

For v1.2, isolate at the process and filesystem layer rather than inventing
in-app accounts. `deploy/run_instance.sh` and
`deploy/clews-ui-analyst@.service` implement this shape:

- one process per analyst or team, with its own
  `CLEWS_UI_RUNTIME_DIR=/var/lib/clews-ui/<instance>` and port;
- one hostname and Access policy per instance, or a validated identity shim;
- `0700` runtime directories and a per-instance Unix service account
  (`clews-ui-<instance>` in the template); the launcher refuses a runtime not
  owned by the current user and takes a per-runtime lock;
- one managed port in `8100-8199` per instance, recorded in the host ledger;
- never share a runtime: the queue and locks are process-local.

The launcher is fail-closed on unsafe instance names, ports outside
`1024-65535`, a busy loopback port, an existing server process for the same
port/runtime, and unsupported roles. It grants `--allow-runs` only to
`analyst`; `model_builder`, `public`, and `energy_modeller` do not get it.

Each process has its own queue and concurrency cap; there is no per-user quota
or per-job cgroup. Size `--max-concurrent-runs` and set `MemoryMax=`/`CPUQuota=`.
Publication, not direct writes, is the only path to the public runtime.

### 3.4 Residual security notes

There is no CSRF token in the app. Keep the writable hostname Access-only and
non-public; add an origin check before broadening access. Access logs the actor;
the app does not. Rate-limit and body-limit at Cloudflare. The tunnel is
outbound-only, so the host needs no inbound 80/443 rule.

## 4. Data, provenance, backup, retention, and audit

### 4.1 Runtime state

| Path under `runtime/ui/` | Content | Durable record |
|---|---|---|
| `scenarios/{id}.json` | Validated `clews.scenario.v1` scenario | Yes, until curated |
| `runs/{run_id}/job.json` | Status, timestamps, command, paths, pid, exit code | Operational |
| `runs/{run_id}/scenario.json` | Immutable snapshot used by the run and retry | Yes |
| `runs/{run_id}/events.jsonl` | Append-only SSE/log events with sequence | Operational |
| `runs/{run_id}/output/run_summaries.json` | Normalized summary and metrics | Yes |
| `runs/{run_id}/output/dispatch_by_zone.csv` | Long-format dispatch and supply | Yes |
| `runs/{run_id}/output/manifest.json` | Scenario, constraint set, model provenance, hashes | Yes |
| `runs/{run_id}/output/supply_caps.json` | Harvest conversion and tier/zonal reconciliation | Yes |
| `runs/{run_id}/output/model/`, `run/` | Disposable model copy, parameter set, solver logs | Rebuildable |

Atomic JSON writes and the persisted job queue survive a normal restart, but
`queued`/`running` jobs become `interrupted`. Retry is the recovery path, and it
uses the snapshot rather than the current scenario file.

### 4.2 Publication to DataLad and Arbutus

Build the public runtime as a release artifact, never a copy. Freeze the
scenario snapshot, summary, dispatch, manifest, supply caps, and checksums;
write a sanitized `publication.json` with run/scenario IDs, constraint set,
commits, labels, hashes, and date. Redact `command`, paths, `pid`, and absolute
paths from job records and event logs; omit `output/model/` and `output/run/`.

Proposed path: `data/derived/national/clews/osecan/ui_publications/<release>/`.
Commit metadata and checksums, annex the payload to `arbutus-s3`, verify with
`git annex whereis`, and restore-test with `datalad get` on a clean checkout.
Runtime directories stay outside the `data` submodule until curated.

### 4.3 Backup and retention

Runtime is operational state, not the source of truth. Take an encrypted
nightly off-host backup of scenarios, jobs, summaries, manifests, and events;
exclude `output/model/` and `output/run/` unless an audit requires them. Run a
quarterly restore drill and verify `GET /api/runs/{id}/output`. Keep published
artifacts in DataLad plus an off-host copy; Arbutus alone is not a backup. Use
bucket versioning/lifecycle and keep credentials out of the repo.

Proposed retention: public releases until superseded, with a ledger; analyst
scenarios/runs 90 days; events and failed/interrupted jobs 30 days. Promote
before deleting and log deletions. The app has no log rotation, so ops must
rotate or archive `events.jsonl` and old run directories.

### 4.4 Audit

`job.json` provides timestamps, exit code, runner status, and event count;
`events.jsonl` provides sequence, timestamp, level, and message. Journald captures
service lifecycle, and Access logs the identity. The app records no actor, so
the ledger pairs run/scenario IDs, artifact hashes, date, and the edge identity
or operator. Treat raw job records, solver logs, and paths as internal until
reviewed.

## 5. Role skins

Skins are landing views and permitted actions, not a security boundary. The v1
app exposes a deployment role flag but no authenticated role switch; v1.1
separates public and analyst instances with Access groups, and v1.2 adds
per-user instances. Hiding a button is not authorization, and setting a role is
not authentication.

| Skin | Entry | Sees | Can do | Cannot do |
|---|---|---|---|---|
| Analyst | Analyst host | Full ledger, map, Run, Explore | Create scenarios, queue runs, cancel/retry, compare, export | Rebuild the model, publish a release, change constraint sets |
| Model Builder | Analyst host plus host ops | Model manifest, constraint sets, run audits | Rebuild/verify the OSeCAN copy, prepare releases, publish with ops | Make energy-policy claims; bypass publication review |
| Energy Modeller | Read-only host | `SUPBCW`/`B1`/`B2` rows, provenance, `policy_test_v1` status | Inspect, download interface artifacts, compare published runs | Edit energy levers, run OSeCAN, change reserve margin, credits, build rates, or policy |
| Public Reviewer | Public host | Curated scenarios, dispatch, trade-off, map gaps | Browse, filter, download curated artifacts | Create, run, cancel, mutate, or see analyst runtime data |

The Energy Modeller skin is a read-only linkage surface. Existing scenario
energy fields (carbon price, hydro mode, nuclear/wind caps, tier caps) remain
screening inputs for analysts, not policy controls, and must not be presented as
policy choices. No skin adds reserve-margin, transmission, curtailment, or
unit-commitment controls.

## 6. Spatial display

The map currently exposes 13 province/territory polygons, 36 ecoprovinces, 385
RSYC tiles, and a Canada → province → ecoprovince → RSYC drill-down with a
shared year control. `spatial/zone_nodes.json` has 13 C2070 zone nodes, 2 authoritative
B1/B2 anchors, and 11 explicit gaps. Dispatch remains national; selections
without a documented zone mapping say so.

The v1 fallback is a 13-zone table/heatmap driven by
`output/dispatch_by_zone.csv`: one row per zone, year and metric columns, a
separate `CAN` national row, and no summing of `CAN` with zone rows. It must
reconcile to the dispatch totals and show the 11 gaps as gaps, not zeros or
inferred values. The current UI has a zone filter and dispatch snapshot table;
the dedicated zone table/heatmap is a v1 release item.

Do not draw a C2070 zone polygon, centroid, or interpolation until an
authoritative boundary and a documented crosswalk exist. Record the source,
version, licence, and date when they arrive; until then the map shows only
B1/B2 anchors and explicit gap labels for C2070 zones. The 13-zone table is a
fallback for reporting, not a substitute for a boundary.

## 7. Release and handoff package

| Deliverable | Content |
|---|---|
| Model card | Scope, model and base commits, `screening_v0` and draft `policy_test_v1`, labels, metrics, intended use, non-use, spatial limits, owners |
| Ops runbook | Host prerequisites, service units, environment file, tunnel/Access setup, model rebuild, smoke tests, rollback, incident, backup/restore |
| API and UI docs | Existing `workflows/ui/README.md` and `ui/README.md`, frozen `clews.scenario.v1`, endpoint/error/SSE notes, OpenAPI |
| Screenshots | Overview, dispatch, trade-off, Explore, map gap, 13-zone table, run queue; captured from the tagged release |
| Acceptance record | Automated test output plus the manual checks in §8, with date, commit, and operator |
| Known caveats | No app auth or tenant isolation, draft policy set, one Pareto case, 11 spatial gaps, no restart resume, model rebuild gap, no log rotation |
| Publication ledger | Run/scenario IDs, hashes, labels, publication dates, actor source |
| Handoff bundle | Release tag, commit, model manifest, data commit, ledger, contacts; no credentials or personal paths |

Freeze the release before capturing screenshots. Do not mix screenshots from a
live analyst runtime with the curated public release.

## 8. Acceptance criteria and staged rollout

| Stage | Scope | Exit criteria |
|---|---|---|
| v1 local | Loopback only, one analyst, `runtime/ui`, `--allow-runs` | A1–A6, A10, A13 pass; docs and caveats complete |
| v1.1 authenticated | systemd + Tunnel + Access; public read-only and analyst writable, separate runtimes | A7–A9, A12 pass; restore drill complete |
| v1.2 multi-user | One instance/runtime per analyst or team; per-instance limits; publication workflow | A11 pass; isolation review complete; `run_instance.sh`/template smoke-tested on the host |

The local v1.2 deploy smoke (two temporary runtimes/ports, distinct `0700`
directories, analyst `allow_runs=true`, public `read_only=true`, public POST
403) is implementation evidence, not host acceptance. A11 still requires a
host test with per-instance service accounts and one Access policy per
instance; that has not been run here.

- **A1. Bind:** `ss -ltnp` shows `127.0.0.1` only; the host has no inbound
  80/443 rule; the tunnel is the only ingress.
- **A2. Health and modes:** `/api/health` reports the expected `read_only` and
  `allow_runs`; every mutating method on the public instance returns 403.
- **A3. Public isolation:** the public instance serves only curated scenarios
  and runs; no analyst scenario, job path, command, pid, or event log is exposed.
- **A4. Analyst end-to-end:** create a scenario, enqueue a run, receive SSE
  events, fetch normalized output, cancel a queued and a running run, and retry
  from the snapshot.
- **A5. Restart:** queued/running jobs become `interrupted`; no job file is lost;
  retry succeeds; no stale `running` status remains.
- **A6. Concurrency:** excess runs stay `queued`; output is 409 while active and
  404 when absent; path traversal and symlink escapes are rejected.
- **A7. Access:** unauthenticated requests are denied at the edge; the analyst
  group is allowed; direct origin access is blocked; the public policy matches
  the release intent.
- **A8. Publication:** `publication.json` hashes verify; no absolute paths or
  credentials appear; `datalad get` restores the payload on a clean checkout.
- **A9. Backup:** a restored runtime serves the expected scenario and run output
  with matching hashes.
- **A10. Spatial:** 13 zone rows plus a separate `CAN` row, 11 gaps, 0 zone
  polygons; the table reconciles to dispatch; B1/B2 anchors are the only zone
  geometry.
- **A11. Tenant isolation (v1.2):** analyst A cannot list, read, cancel, or
  retry analyst B's scenarios/runs; runtimes and ports are separate.
- **A12. Role skins:** Energy Modeller is read-only and cannot edit energy
  levers; Public Reviewer has no mutation path; Analyst and Model Builder
  actions match §5.
- **A13. UI guards:** static fallback works without the API; 53 scenarios and one
  Pareto-eligible case remain guarded; `policy_test_v1` is labelled draft with
  `policy_grade=false`; no C2070 zone polygon is drawn.

## Non-claims

This note does not deploy anything, add authentication, change code or data, or
authorize publication of any artifact. It does not make `policy_test_v1`
policy-grade, validate a harvest or energy result, define C2070 boundaries, or
claim that Cloudflare Access provides per-user authorization inside the app. A
signed agreement or active issue governs if it conflicts with this note.

## References

- `workflows/ui/README.md` — API, runner, roles, model setup, tests.
- `deploy/README.md` — systemd, tunnel/Access, publication, backup, retention.
- `ui/README.md` — views, data contract, spatial gaps, labels.
- `planning/module-boundaries.md` — forestry/OSeCAN ownership and interface rules.
- `planning/policy-test-v1.md` — draft constraint set and non-claims.
- `docs/data-repository-runbook.md` — DataLad and `arbutus-s3` operations.
- `AGENTS.md` — repository data boundary and planning workflow.
