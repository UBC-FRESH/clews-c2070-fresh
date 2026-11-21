# CLEWS/C2070 forestry UI deployment runbook (v1 and v1.2 isolation)

This directory contains templates and operating notes for the forestry scenario
explorer. Nothing here deploys, starts, or authenticates anything by itself.
No credentials belong in this repository.

The deployment is unprivileged systemd services on one host behind a Cloudflare
Tunnel. v1 ships a public read-only instance and a shared analyst instance. v1.2
adds one process and runtime per analyst or team, using
`deploy/run_instance.sh` and the `clews-ui-analyst@.service` template.

| Instance | Bind | Role / flags | Runtime | Ingress |
|---|---|---|---|---|
| Public | `127.0.0.1:8013` | `--role public --read-only` | `/var/lib/clews-ui/public` | Public hostname behind WAF/rate limit, or reviewer Access policy |
| Shared analyst (v1.1) | `127.0.0.1:8014` | `--role analyst --allow-runs --max-concurrent-runs 2` | `/var/lib/clews-ui/analyst` | Analyst Access group only |
| Per-analyst/team (v1.2) | one port per instance, `127.0.0.1:8100-8199` | `--role analyst --allow-runs --max-concurrent-runs N` | `/var/lib/clews-ui/<instance>` | One hostname and Access group/policy per instance or team |

The application has no login form, user table, session cookie, password store,
or token validation. Cloudflare Access is the identity boundary. The role flag
selects allowed actions; it does not authenticate a caller and is not a security
boundary. Do not expose any origin port directly.

**v1.2 deployment isolation is process and filesystem isolation, not in-app
tenancy.** `run_instance.sh` creates a `0700` runtime, takes a per-runtime lock,
checks the port, and starts exactly one server process. The server remains
single-tenant inside one runtime: it has no owner field on scenarios or jobs and
does not authorize callers against each other. Use one instance per trust
boundary, one runtime per instance, and never point two instances at the same
runtime. Database-style per-user accounts, quotas, and a publication service are
not provided.

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

`run_instance.sh` grants `--allow-runs` only to `analyst`. `model_builder`
instances are writable for scenarios but do not spawn solver subprocesses;
`public` and `energy_modeller` instances add `--read-only`. A model rebuild and
publication remain host operations with their own review.

## 2. Ports and instance registry

Use one loopback port and one runtime per instance. Record both in the host
ledger before enabling the unit.

| Port | Use |
|---|---|
| `8013` | Public read-only instance |
| `8014` | Shared analyst instance (v1.1) |
| `8100-8199` | Per-analyst/team instances (v1.2) |
| `1024-65535*` | `run_instance.sh` validation range; use the managed ranges above in production |

The script rejects any port outside `1024-65535`, a port already bound on
`127.0.0.1`, and a runtime that already belongs to a server process. Do not
assign the same port to two instances, even on different hostnames.

## 3. Host prerequisites

- Linux host with systemd and a dedicated unprivileged account. The v1 static
  units use a shared `clews-ui` account:

  ```bash
  sudo useradd --system --home /var/lib/clews-ui --shell /usr/sbin/nologin clews-ui
  ```

  The v1.2 template uses one account per instance (`clews-ui-<instance>`), so a
  `0700` runtime also isolates instances at the Unix-user layer:

  ```bash
  sudo useradd --system --user-group --no-create-home \
    --home-dir /var/lib/clews-ui/<instance> --shell /usr/sbin/nologin \
    clews-ui-<instance>
  ```

  Instance names are 1-20 lowercase letters, digits, or hyphens; they start with
  a letter and do not end with a hyphen. This keeps `clews-ui-<instance>` a
  valid service account and a safe path component.

- A release checkout at `/opt/clews/clews-c2070-fresh` with the repository-local
  `.venv` installed from `.[web]` (see `workflows/ui/README.md`).
- `runtime/osecan_model` readable by the analyst service account. Read-only
  instances do not solve.
- A runtime base outside the repository and the `data` submodule. The base is
  traversable but not writable by instance users; each instance directory is
  `0700` and owned by its service account:

  ```bash
  sudo install -d -o root -g root -m 0711 /var/lib/clews-ui
  sudo install -d -o clews-ui-<instance> -g clews-ui-<instance> -m 0700 \
    /var/lib/clews-ui/<instance>
  ```

  The systemd template also declares `StateDirectory=clews-ui/%i` with
  `StateDirectoryMode=0700`, so `systemctl start` creates the directory with the
  correct owner if it does not exist. `run_instance.sh` fails closed if the
  directory belongs to another user or is a symlink.

- The release checkout should be read-only to service accounts
  (`root:clews-ui`, mode `0755` or stricter). Only each instance runtime is
  writable.
- Outbound HTTPS for `cloudflared`; no inbound `80/443` rule is required.

JupyterHub with `jupyter-server-proxy` is a development-only access path, not
this deployment. Its path prefix breaks the SPA's origin-root `/api` calls and
SSE can be buffered; a user server can also sleep or restart. Use systemd plus
the tunnel for public and analyst service, or make and test a base-path change
before considering a proxy mount.

### 3.1 Launch one instance directly

`deploy/run_instance.sh` is the single launch contract for v1.2. It validates
the instance name, port, role, runtime ownership, lock, and listener before
`exec`:

```bash
sudo -u clews-ui-<instance> \
  CLEWS_UI_BASE_DIR=/var/lib/clews-ui \
  /opt/clews/clews-c2070-fresh/deploy/run_instance.sh <instance> 8100 analyst
```

Optional environment:

| Variable | Purpose |
|---|---|
| `CLEWS_UI_BASE_DIR` | Runtime base; default `/var/lib/clews-ui` |
| `CLEWS_UI_PYTHON` | Python interpreter; default the release `.venv/bin/python` |
| `CLEWS_UI_MAX_CONCURRENT_RUNS` | Analyst solver cap; default `2`, maximum `64` |

The script exports `CLEWS_UI_RUNTIME_DIR`, binds `127.0.0.1`, and passes
`--runtime-dir`. It reads no credentials and writes no secrets.

## 4. Install the services

### 4.1 v1 public and shared analyst units

Copy and edit both units. At minimum, replace `/opt/clews/clews-c2070-fresh`
with the release path and confirm the user, ports, runtime paths, and resource
limits. Install them only when the host and runtime are prepared:

```bash
sudo install -m 0644 deploy/clews-ui-public.service /etc/systemd/system/
sudo install -m 0644 deploy/clews-ui-analyst.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clews-ui-public.service clews-ui-analyst.service
```

The public unit makes its runtime read-only and does not create directories at
startup. The analyst unit allows writes only to its runtime path.

### 4.2 v1.2 per-instance analyst template

Install the template once, then create one root-owned environment file per
instance. The file supplies `PORT` and `ROLE` only; keep it outside the
repository at `/etc/clews-ui/<instance>.env`, mode `0600`:

```bash
sudo install -m 0644 deploy/clews-ui-analyst@.service /etc/systemd/system/
sudo install -d -m 0755 /etc/clews-ui
sudo install -m 0600 deploy/instance-env.example /etc/clews-ui/<instance>.env
sudoedit /etc/clews-ui/<instance>.env   # set PORT=8100, ROLE=analyst
sudo systemctl daemon-reload
sudo systemctl enable --now clews-ui-analyst@<instance>.service
```

The template expands `%i` to the instance, runs as `clews-ui-<instance>`, uses
`EnvironmentFile=/etc/clews-ui/%i.env`, creates `RuntimeDirectory=clews-ui-%i`
and `StateDirectory=clews-ui/%i` with mode `0700`, and hardens the process with
`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`,
`PrivateTmp=true`, an empty capability set, and a loopback-only bind. It carries
no secrets and no solver paths beyond the release checkout.

Both the v1 static units and the v1.2 template run with
`ProtectSystem=strict`, `ProtectHome=true`, `NoNewPrivileges=true`,
`PrivateTmp=true`, an empty capability set, and loopback-only binds.
`TimeoutStopSec=30` gives the process time to mark active jobs interrupted.

Resource limits in the units are placeholders. Size `MemoryMax`, `CPUQuota`,
and `TasksMax` to the host and to `--max-concurrent-runs`. Each process has its
own in-memory queue and locks; never share one runtime between services.

## 5. Cloudflare Tunnel and Access

1. Create a named tunnel and copy
   `cloudflared-config.yml.example` to `/etc/cloudflared/config.yml`.
2. Replace `REPLACE_WITH_TUNNEL_UUID`, the credentials path, and
   `REPLACE_WITH_DOMAIN`. Keep the credentials JSON outside the repository and
   readable only by the tunnel service.
3. Route DNS for the public and analyst hostnames to the tunnel. For v1.2, add
   one hostname per instance or team.
4. Run `cloudflared` as a systemd service, for example:

   ```bash
   sudo cloudflared service install
   sudo systemctl enable --now cloudflared
   ```

5. Configure Access in the Cloudflare dashboard, not in the tunnel config file.
   Map each origin to exactly one policy:

| Origin | Hostname pattern | Access policy |
|---|---|---|
| `127.0.0.1:8013` | `forestry-public.<domain>` | Public bypass with WAF/rate limiting, or reviewer OTP allowlist |
| `127.0.0.1:8014` | `forestry-analyst.<domain>` | Analyst Access group; model-builder group only for trusted operators |
| `127.0.0.1:8100-8199` | `forestry-<instance>.<domain>` | One analyst/team Access group per instance; no shared wildcard policy across teams |

The origin remains `127.0.0.1`; the tunnel is outbound-only. Do not trust an
unvalidated `Cf-Access-Authenticated-User-Email` header. The app does not
validate Access JWTs, so an analyst origin must never be reachable without the
edge policy. Rate-limit and body-limit requests at Cloudflare; there is no CSRF
token in the app. If a separate energy-modeller linkage view is required, run a
read-only instance with `--role energy_modeller` on its own hostname and policy.

## 6. Public runtime publication

The public instance serves a curated runtime; it is not a copy of an analyst's
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
logs. A per-instance writer never publishes directly; promotion goes through
the same release artifact path.

## 7. Backup, restore, and retention

Runtime state is operational, not the source of truth. Back up each runtime
directory separately (scenarios, job records, snapshots, summaries, manifests,
and events) nightly with an encrypted off-host tool. Exclude `output/model/`
and `output/run/` unless an audit requires them. Run a quarterly restore drill
for the public runtime and at least one analyst runtime, and verify
`GET /api/runs/{id}/output` after restore.

Suggested retention:

| Data | Retention |
|---|---|
| Public releases | Until superseded, with a publication ledger |
| Analyst scenarios/runs | 90 days |
| Events and failed/interrupted jobs | 30 days |

Promote before deleting, and record deletions. The app has no log rotation, so
ops must rotate or archive `events.jsonl` and old run directories in every
runtime. Keep backups of different instances separated so a restore cannot
merge analyst/team state.

## 8. Data and S3 boundary

- Keep code, deployment templates, metadata, and publication manifests in this
  parent repository.
- Keep heavy payloads in the `data` DataLad submodule and Arbutus S3 via the
  `arbutus-s3` special remote. Use bucket versioning/lifecycle where available.
- Never commit credentials, local AWS/S3 profiles, tunnel credentials, Access
  service tokens, personal paths, or machine-specific configuration.
- Arbutus alone is not a backup: keep a separate off-host copy of published
  artifacts.

## 9. Smoke tests and handoff

From the host (not through the public edge), verify the v1 instances:

```bash
curl -fsS http://127.0.0.1:8013/api/health
curl -fsS http://127.0.0.1:8013/api/version
curl -fsS http://127.0.0.1:8013/api/roles
curl -fsS http://127.0.0.1:8014/api/health
```

Expected: public reports `role=public`, `read_only=true`, `allow_runs=false`;
analyst reports `role=analyst`, `read_only=false`, `allow_runs=true`; all binds
are loopback only (`ss -ltnp`). A public `POST /api/scenarios` or `POST
/api/runs` must return 403. Check that each analyst hostname is denied without
Access and that the public hostname matches the intended policy.

For a v1.2 two-instance smoke test on temporary directories and ports (no
solver, no system service):

```bash
BASE="$(mktemp -d)"
CLEWS_UI_BASE_DIR="$BASE" ./deploy/run_instance.sh smoke-a 18100 analyst &
CLEWS_UI_BASE_DIR="$BASE" ./deploy/run_instance.sh smoke-b 18101 public &
curl -fsS http://127.0.0.1:18100/api/health
curl -fsS http://127.0.0.1:18101/api/health
stat -c '%a %n' "$BASE/smoke-a" "$BASE/smoke-b"
kill %1 %2
```

Expected: health reports analyst `allow_runs=true`, public `read_only=true` and
`allow_runs=false`; both runtime directories are `700`; the two origins are
distinct and loopback-only. A `POST /api/scenarios` to the public instance
returns 403. Stop both processes after the test and do not point the smoke test
at a production runtime.

Before handoff, capture the release commit, model manifest, data commit,
publication ledger, test output, and known caveats. Do not capture screenshots
from a live analyst runtime for a public release.

## 10. v1.2 acceptance

The deployment-level v1.2 acceptance record must show:

- one process, one loopback port, and one `0700` runtime per instance;
- distinct runtime directories and ports for two test instances, with no shared
  queue or lock;
- public `POST`/`PUT`/`PATCH`/`DELETE` returns 403 and analyst `--allow-runs`
  reports true only on the analyst instance;
- each instance maps to one Cloudflare Access policy or an explicitly reviewed
  public/reviewer policy;
- no instance runtime is readable by another instance's Unix user;
- backup and restore are exercised per runtime;
- publication to the public runtime still goes through the release artifact
  path, not a direct copy.

This is A11 in `planning/ui-deployment-v1.md`. It is a deployment acceptance
record, not a claim that the application has per-user accounts or in-app
authorization.

## 11. Restart, rollback, and incidents

- Restart marks `queued`/`running` jobs `interrupted`; it is not a transparent
  reload. Retry from the immutable snapshot after the service is healthy.
- To roll back, point the unit's `WorkingDirectory` and `ExecStart` back to the
  previous release path (or restore the previous symlink), reload, restart, and
  repeat the smoke tests. Keep the runtime directories in place.
- To retire an instance, stop and disable its unit, preserve or archive its
  runtime according to the retention rule, then remove its Access policy and
  tunnel route. Do not reuse its port or runtime for a different team without
  an explicit wipe and ledger entry.
- On suspected credential exposure, rotate the tunnel credential and Access
  tokens at the edge; do not place replacements in this repository.
- On unexpected origin access, stop the affected unit and remove the edge route
  before investigating.

## 12. Explicit non-claims

This runbook does not provide in-app authentication, per-user authorization,
owner fields on scenarios or jobs, a CSRF token, a database, or high
availability. Deployment-level v1.2 isolation separates processes, runtimes,
Unix users, ports, and edge policies; it does not make one instance safe for
mutually untrusted callers. It does not make `policy_test_v1` policy-grade,
validate a harvest or energy result, or authorize publication of any artifact.

## 13. Static v1.0.0 ledger origin (port 8131)

`clews-ledger-static.service` serves the immutable
`ui_publications/v1.0.0/` bundle with Python's static HTTP server. It is not an
application runtime: there is no runner, writable state, analyst API, or
authentication. The bundle's `publication-mode.js` also rejects every
same-origin `/api` request, so the UI stays in static mode even if a future
origin happens to answer `/api`.

| Item | Value |
|---|---|
| Unit | `deploy/clews-ledger-static.service` |
| User / group | `gep:gep` |
| Bundle | `/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/ui_publications/v1.0.0` |
| Bind | `0.0.0.0:8131` (container network only) |
| Root-host origin | `http://10.232.253.73:8131` |

The origin binds the container interface, not loopback, because the tunnel
runs on the root LXD host and must reach the container address. It must not be
published outside the container bridge, and the root host must not forward the
port directly; Cloudflare Tunnel/Access remains the edge boundary. Do not add
an analyst route to this origin.

Install and verify:

```bash
sudo install -m 0644 deploy/clews-ledger-static.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clews-ledger-static.service
systemctl is-active clews-ledger-static.service
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8131/
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8131/public/data/scenarios.json
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8131/vendor/echarts.min.js
curl -fsS -o /dev/null -w '%{http_code}\n' http://10.232.253.73:8131/
```

The static server returns `404` for `/api/...`; that is expected. To route a
root-host hostname to this origin, add exactly one ingress rule on the root
host, ahead of the catch-all, and configure DNS/Access separately:

```yaml
  - hostname: REPLACE_WITH_LEDGER_HOSTNAME
    service: http://10.232.253.73:8131
```

This repository does not modify the root-host tunnel, DNS, or Access policy.
Rollback is `sudo systemctl disable --now clews-ledger-static.service`; the
bundle and unit remain on disk for the next release.
