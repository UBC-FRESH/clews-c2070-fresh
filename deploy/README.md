# CLEWS/C2070 forestry UI deployment runbook (v1)

This directory contains templates and operating notes for the v1 deployment of
the forestry scenario explorer. Nothing here deploys, starts, or authenticates
anything by itself. No credentials belong in this repository.

The deployment is two unprivileged systemd services on one host behind a
Cloudflare Tunnel:

| Instance | Bind | Role / flags | Runtime | Ingress |
|---|---|---|---|---|
| Public | `127.0.0.1:8013` | `--role public --read-only` | `/var/lib/clews-ui/public` | Public hostname behind WAF/rate limit, or reviewer Access policy |
| Analyst | `127.0.0.1:8014` | `--role analyst --allow-runs --max-concurrent-runs 2` | `/var/lib/clews-ui/analyst` | Analyst Access group only |

The application has no login form, user table, session cookie, password store,
or token validation. Cloudflare Access is the identity boundary. The role flag
selects allowed actions; it does not authenticate a caller and is not a security
boundary. Do not expose either origin port directly.

**Not in this track:** per-user runtime isolation. The v1 server is
single-tenant: any caller admitted to a writable instance can list, overwrite,
cancel, or retry every stored scenario and run. One process per analyst/team,
`0700` runtimes, and per-instance ports are a v1.2 deliverable and are not
implemented here.

## 1. Deployment roles

`--role` (or `CLEWS_UI_ROLE`) accepts exactly these values. Unknown values fail
at startup. `public` and `energy_modeller` force read-only; combining either
with `--allow-runs` is a startup error. `--read-only` is valid on any role and
also disables runs.

| Role | Read-only | Runs | Scenario edits | Energy linkage |
|---|---|---|---|---|
| `public` | Forced | No | No | Read-only |
| `analyst` | Flag-controlled | Requires `--allow-runs` | Yes when writable | Writable through scenario API |
| `model_builder` | Flag-controlled | Requires `--allow-runs` | Yes when writable | Read-only through the API; model rebuild/publication stays host-operator work |
| `energy_modeller` | Forced | No | No | Read-only OSeCAN linkage view |

`/api/health`, `/api/version`, and `/api/roles` report the active role and
effective `read_only` / `allow_runs` values. `/api/roles` is descriptive only;
it is not an authorization service.

`model_builder` is deliberately not a model-administration API. It may create
and run forestry scenarios like an analyst, but the OSeCAN tree, constraint
sets, and publication flow are not mutated through this server. Those remain
host operations with their own review.

## 2. Host prerequisites

- Linux host with systemd and a dedicated unprivileged account, e.g.
  `clews-ui` (`useradd --system --home /var/lib/clews-ui --shell /usr/sbin/nologin clews-ui`).
- A release checkout at `/opt/clews/clews-c2070-fresh` with the repository-local
  `.venv` installed from `.[web]` (see `workflows/ui/README.md`).
- `runtime/osecan_model` readable by `clews-ui` for analyst runs. The public
  instance does not solve.
- Two runtime directories outside the repository and the `data` submodule:

  ```bash
  install -d -o clews-ui -g clews-ui -m 0750 /var/lib/clews-ui
  install -d -o clews-ui -g clews-ui -m 0750 /var/lib/clews-ui/analyst
  install -d -o clews-ui -g clews-ui -m 0550 /var/lib/clews-ui/public
  ```

- The release checkout should be read-only to the service account
  (`root:clews-ui`, mode `0755` or stricter). Only `/var/lib/clews-ui/analyst`
  is writable by the service.
- Outbound HTTPS for `cloudflared`; no inbound `80/443` rule is required.

JupyterHub with `jupyter-server-proxy` is a development-only access path, not
this deployment. Its path prefix breaks the SPA's origin-root `/api` calls and
SSE can be buffered; a user server can also sleep or restart. Use systemd plus
the tunnel for public and analyst service, or make and test a base-path change
before considering a proxy mount.

## 3. Install the services

Copy and edit both units. At minimum, replace `/opt/clews/clews-c2070-fresh`
with the release path and confirm the user, ports, runtime paths, and resource
limits. Install them only when the host and runtime are prepared:

```bash
sudo install -m 0644 deploy/clews-ui-public.service /etc/systemd/system/
sudo install -m 0644 deploy/clews-ui-analyst.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clews-ui-public.service clews-ui-analyst.service
```

Do not put secrets in the unit files. Optional environment files are referenced
as `/etc/clews-ui/public.env` and `/etc/clews-ui/analyst.env`; keep them
root-owned `0600`, outside the repository, and limited to non-secret runtime
overrides. The roles and bind addresses are also passed as CLI flags so an
environment file cannot silently turn a public instance writable or enable runs
for a read-only role.

Both units run with `ProtectSystem=strict`, `ProtectHome=true`,
`NoNewPrivileges=true`, `PrivateTmp=true`, an empty capability set, and
loopback-only binds. The public unit makes the runtime read-only and does not
create directories at startup. The analyst unit allows writes only to its
runtime path. `TimeoutStopSec=30` gives the process time to mark active jobs
interrupted.

Resource limits in the units are placeholders. Size `MemoryMax`, `CPUQuota`,
and `TasksMax` to the host and to `--max-concurrent-runs`. Each process has its
own in-memory queue and locks; never share one runtime between services.

## 4. Cloudflare Tunnel and Access

1. Create a named tunnel and copy
   `cloudflared-config.yml.example` to `/etc/cloudflared/config.yml`.
2. Replace `REPLACE_WITH_TUNNEL_UUID`, the credentials path, and
   `REPLACE_WITH_DOMAIN`. Keep the credentials JSON outside the repository and
   readable only by the tunnel service.
3. Route DNS for the public and analyst hostnames to the tunnel.
4. Run `cloudflared` as a systemd service, for example:

   ```bash
   sudo cloudflared service install
   sudo systemctl enable --now cloudflared
   ```

5. Configure Access in the Cloudflare dashboard, not in the tunnel config file:
   - Public host: public bypass with WAF and rate limiting, or a reviewer
     one-time-PIN allowlist.
   - Analyst host: allow only the analyst Access group; add the model-builder
     group only for trusted operators.
   - Energy modeller: if a named linkage view is required, run a separate
     read-only instance with `--role energy_modeller` and restrict its hostname
     to that group. The shipped public unit is the anonymous/reviewer surface.

The origin remains `127.0.0.1`; the tunnel is outbound-only. Do not trust an
unvalidated `Cf-Access-Authenticated-User-Email` header. The app does not
validate Access JWTs, so the analyst origin must never be reachable without the
edge policy. Rate-limit and body-limit requests at Cloudflare; there is no CSRF
token in the app.

## 5. Public runtime publication

The public instance serves a curated runtime; it is not a copy of the analyst's
working directory. Build it as a release artifact:

1. Freeze the approved scenario snapshots, `run_summaries.json`,
   `dispatch_by_zone.csv`, `manifest.json`, `supply_caps.json`, and checksums.
2. Write a sanitized `publication.json` with run/scenario IDs, constraint set,
   release date, labels, and hashes. Redact `command`, absolute paths, `pid`,
   and event logs; omit `output/model/` and `output/run/`.
3. Publish to DataLad under
   `data/derived/national/clews/osecan/ui_publications/<release>/`; commit
   metadata and checksums, annex the payload to `arbutus-s3`, verify with
   `git annex whereis`, and restore-test with `datalad get` on a clean checkout.
4. Populate `/var/lib/clews-ui/public` from the verified release, then start or
   restart the public unit. Read-only startup creates no directories and repairs
   no jobs, so a missing or incomplete runtime is a deployment error.

Publication, not direct writes, is the only path into the public runtime. The
public release must not contain analyst job paths, commands, pids, or event
logs.

## 6. Backup, restore, and retention

Runtime state is operational, not the source of truth. Back up scenarios, job
records, snapshots, summaries, manifests, and events nightly with an encrypted
off-host tool. Exclude `output/model/` and `output/run/` unless an audit
requires them. Run a quarterly restore drill and verify
`GET /api/runs/{id}/output`.

Suggested retention:

| Data | Retention |
|---|---|
| Public releases | Until superseded, with a publication ledger |
| Analyst scenarios/runs | 90 days |
| Events and failed/interrupted jobs | 30 days |

Promote before deleting, and record deletions. The app has no log rotation, so
ops must rotate or archive `events.jsonl` and old run directories.

## 7. Data and S3 boundary

- Keep code, deployment templates, metadata, and publication manifests in this
  parent repository.
- Keep heavy payloads in the `data` DataLad submodule and Arbutus S3 via the
  `arbutus-s3` special remote. Use bucket versioning/lifecycle where available.
- Never commit credentials, local AWS/S3 profiles, tunnel credentials, Access
  service tokens, personal paths, or machine-specific configuration.
- Arbutus alone is not a backup: keep a separate off-host copy of published
  artifacts.

## 8. Smoke tests and handoff

From the host (not through the public edge), verify:

```bash
curl -fsS http://127.0.0.1:8013/api/health
curl -fsS http://127.0.0.1:8013/api/version
curl -fsS http://127.0.0.1:8013/api/roles
curl -fsS http://127.0.0.1:8014/api/health
```

Expected: public reports `role=public`, `read_only=true`, `allow_runs=false`;
analyst reports `role=analyst`, `read_only=false`, `allow_runs=true`; both bind
loopback only (`ss -ltnp`). A public `POST /api/scenarios` or `POST /api/runs`
must return 403. Check that the analyst hostname is denied without Access and
that the public hostname matches the intended policy.

Before handoff, capture the release commit, model manifest, data commit,
publication ledger, test output, and known caveats. Do not capture screenshots
from a live analyst runtime for a public release.

## 9. Restart, rollback, and incidents

- Restart marks `queued`/`running` jobs `interrupted`; it is not a transparent
  reload. Retry from the immutable snapshot after the service is healthy.
- To roll back, point the unit's `WorkingDirectory` and `ExecStart` back to the
  previous release path (or restore the previous symlink), reload, restart, and
  repeat the smoke tests. Keep the runtime directories in place.
- On suspected credential exposure, rotate the tunnel credential and Access
  tokens at the edge; do not place replacements in this repository.
- On unexpected origin access, stop the affected unit and remove the edge route
  before investigating.

## 10. Explicit non-claims

This runbook does not provide authentication, per-user authorization, tenant
isolation, a CSRF token, a database, or high availability. It does not make
`policy_test_v1` policy-grade, validate a harvest or energy result, or authorize
publication of any artifact. Per-user runtime isolation is v1.2 and is not
implemented by the v1 server or these units.
