# v1 release and handoff package — CLEWS-C2070 FRESH forestry/biomass module

| Field | Value |
|---|---|
| Package | v1 release/handoff documentation for the forestry/biomass module and forestry UI |
| Date | 2026-11-20 |
| Status | Release/handoff documentation plus v1.2 deployment isolation scaffolding. No service is deployed or started; no public OSeCAN change. |
| Package version | `0.1.0a1` (the “v1” label is the UI/deployment track, not a PyPI version) |
| Recorded source baseline | Parent `a9edcd2`; data submodule `00cba3c` (branch `feature/osecan-zonal-bc-supply`) |
| Model card | [`docs/model-card.md`](model-card.md) |
| Canonical result | 119 Mm³ fleet-corrected: **1,445,655.44 M$**, **424.710127 Mt CO₂** cumulative 2021–2070, **0.0 Mt CO₂** in 2050 |
| Draft interface test | `policy_test_v1`: **1,458,305.16 M$**, **1,024.824 Mt CO₂** cumulative, **0.490203 Mt CO₂** in 2050; `policy_grade=false` |

**Boundary.** This release covers the FRESH forestry/biomass module: RSYC-based
supply evidence, harvest and allocation logic, the bucking/residue chain, the
cost/supply curve, the BC/QC cost-revenue ledger, and the forestry UI. Taco Niet
and the energy/OSeCAN policy team own energy-policy design and OSeCAN structure.
OSeCAN is a **black-box interface test**. Nothing in this package is
energy-policy advice, a legal AAC/THLB determination, or a supply forecast.

## 1. Release scope

**In scope**

- RSYC national yield backbone and the national representative-stand/buck-yield
  chain.
- CER EF2026 2021 capacity fleet (**145.02 GW**) in the disposable OSeCAN model.
- Operable merchantable-forest proxy (**144.69 Mha**), explicitly a screening
  landbase, not a legal THLB.
- Provincial harvest-allocation screening for 100/119/150 Mm³.
- Allocation-weighted residue factors and the national/BC residue supply curve.
- BC/QC cost-revenue ledger (**940 rows**); net value remains `not_estimated`.
- Forestry UI: static Forest Ledger plus the FastAPI live runner.
- `policy_test_v1` draft constraint family and its interface test.

**Out of scope**

- OSeCAN source, structure, policy constraints, reserve margin, capacity
  credits, build rates, transmission, curtailment, or carbon-price policy.
- Legal AAC, legal THLB, sustainable-supply certification, or harvest
  recommendations.
- C2070 zone boundaries or polygons beyond the B1/B2 point anchors.
- Production deployment; in-app authentication; in-app tenant accounts. The
  v1.2 deploy assets isolate processes, runtimes, Unix users, ports, and edge
  policies, but the server remains single-tenant within one runtime.
- Energy-policy claims, investment advice, or a policy-grade result.

## 2. Model card summary

The full model card is in [`docs/model-card.md`](model-card.md). The release
identity is:

| Item | Summary |
|---|---|
| Model | National forestry/biomass supply and residue screening chain linked to OSeCAN |
| Time | Annual, 2021–2070 |
| Space | 13 provinces/territories; 36 ecoprovinces; 385 RSYC tiles; 13 C2070 zone nodes (B1/B2 anchors, 11 gaps) |
| Units | m³ log/yr, ODT, PJ/yr, $/GJ, Mt CO₂, M$ (MUSD) |
| Constraint sets | `screening_v0` (canonical screening) and `policy_test_v1` (draft interface test) |
| Intended use | Provenance-tracked screening, interface testing, collaborator review |
| Non-use | Policy advice, forecast, legal AAC/THLB, investment decision, energy-policy recommendation |

**Reference results**

| Case | Objective (M$) | Cumulative 2021–2070 (Mt CO₂) | 2050 (Mt CO₂) | Labels | Use |
|---|---:|---:|---:|---|---|
| Canonical fleet-corrected 119 Mm³ | 1,445,655.44 | 424.710127 | 0.0 | `screening_only=true`, `policy_grade=false`, `canonical=true`, `fleet_corrected=true` | Canonical screening reference |
| `policy_test_v1` sourced draft | 1,458,305.16 | 1,024.824 | 0.490203 | `status=draft`, `policy_grade=false` | Interface test only; not policy |

The two rows use different constraint families and configurations. Their
difference is **not** a policy effect and must not be reported as one. The draft
does not replace the canonical row.

**Core components**

- **RSYC national backbone:** 27,208 curves; 385 tiles; 12 ecozones, 36
  ecoprovinces, 138 ecoregions, 672 ecodistricts; 35 species values; age-only
  predictor. The national batch is a pipeline test, not a national supply
  estimate.
- **2021 fleet:** 70 non-zero `PWR*` rows totalling 145.02 GW; `PWRBIO` 2.56888
  GW; `PWRBSW`/`PWRBCW` zero; coal retired by 2030.
- **Operable proxy:** 144.69 Mha (87.0% of the 166.28 Mha managed comparison
  proxy). It is a screening landbase with no slope, road-access, age, or
  economic constraint.
- **Provincial allocation:** 100 and 119 Mm³ feasible at 1.0 and 1.2 m³/ha/yr;
  **150 Mm³ infeasible at both screens** (16.296 Mm³ provincial deficit at 1.0;
  4.492 Mm³ at 1.2). Not a legal AAC.
- **Residue factors:** gross 0.09881 / 0.09918 / 0.10018 ODT/m³ log; base
  recoverable 0.05929 / 0.05951 / 0.06011; base supply 109.68 / 130.98 / 166.80
  PJ/yr for 100/119/150 Mm³.
- **BC/QC ledger:** 940 rows (BC 200 observed, QC 740 model input); 100 Crown /
  840 forest-sector; 100 paired mirrors; 0 observed delivered-log revenue; net
  value `not_estimated`.
- **UI:** 53 scenarios, 18 families, 1 Pareto-eligible; live create/queue/SSE,
  cancel/retry, session hydration, Explore/Pareto/Compare, map drill-down/year
  playback, 13-zone table/heatmap, role badges, public/shared-analyst
  deployment units, and per-instance `run_instance.sh`/systemd template
  isolation.
- **`policy_test_v1`:** draft Stage-1 constraint set with a national peak-proxy
  reserve margin, reviewed capacity credits, build rates, and explicit caps.
  `status=draft`, `policy_grade=false`; not energy-policy advice.

## 3. Acceptance record

**Acceptance date:** 2026-11-20. **Result:** PASS for the recorded code, UI, and
runner checks below. The clean-checkout restore drill passed on 2026-11-20; see
[`docs/release-acceptance.md`](release-acceptance.md).

| Area | Check | Recorded result | Evidence |
|---|---|---|---|
| UI tests | Full UI lane | **113 passed** | v1 acceptance run, 2026-11-20 |
| Ledger/LP tests | Cost-revenue, merge, forest LP, provincial allocation | **42 passed** | v1 acceptance run, 2026-11-20 |
| Live API | Real 119 Mm³ solve through `POST /api/runs` | Completed in **3 m 42 s**; exit 0; `GET /api/runs/{id}/output` returned HTTP 200 | `ui/qa/real-run-119m.md` |
| Static UI | Chromium render and interaction | PASS; recorded page/console checks clean apart from the expected static-mode `/api/health` 404 | `ui/qa/QA_REPORT.md` |
| Live UI | Create, queue, SSE, output, cancel, retry, reload hydration | PASS in recorded Chromium passes | `ui/qa/QA_REPORT.md` |
| Scenario bundle | Counts and boot guards | **53 scenarios / 18 families / 1 Pareto-eligible** | `ui/public/data/scenarios.json` |
| Spatial guard | 13 zone rows plus separate `CAN`; 11 gaps; 0 C2070 polygons | PASS | `ui/qa/QA_REPORT.md` |
| Roles | `public` read-only; `analyst` writable; `model_builder`/`energy_modeller` guarded | PASS | `ui/qa/QA_REPORT.md` |
| v1.2 deploy isolation | Two local instances on temporary runtime dirs/ports | PASS: distinct `0700` runtimes; analyst `allow_runs=true`; public `read_only=true`; public POST 403; no solver | Local smoke, 2026-11-20 |
| Repository hygiene | No unexpected `D` status entries in recorded QA/handoff checks | PASS | QA reports |
| Restore drill | Archive checkout, local data proxy, model rebuild, 119 Mm³ solve | PASS: tree hash `61dace92…`, objective 1,445,655.44 M$, 424.710127 Mt, 2050 0.0, dispatch `eeba3efd…` | `docs/release-acceptance.md` |

The 3 m 42 s live API run used the `screening_119m` example before the fleet
correction (objective 1,411,447.21 M$). It is **runner acceptance evidence**, not
the canonical reference. The canonical reference is the committed
`fleet_capacity_119m` artifact with 1,445,655.44 M$.

## 4. Runbook

### 4.1 Prerequisites and release freeze

- Linux host for deployment; Python ≥ 3.11 (QA used 3.12.3); `git`,
  `git-annex`, and DataLad. `git-annex` is an OS-level prerequisite; DataLad is
  installed from `.[workflow]`.
- The `data` submodule and the `arbutus-s3` special remote for heavy payloads.
  Keep credentials outside the repository.
- A public `deltae/C2070` checkout and its `osecan` Python environment for the
  disposable model setup. The runner uses `runtime/osecan_model`; there is no
  `/tmp` fallback.
- Freeze and record: parent commit, data commit, model source commit, model
  manifest hash, UI bundle counts, test/QA results, and publication ledger state.
  Do not capture screenshots from a live analyst runtime for a public release.

### 4.2 Clean checkout and environment

```bash
git clone https://github.com/UBC-FRESH/clews-c2070-fresh.git
cd clews-c2070-fresh
RELEASE_REF="${RELEASE_REF:-a9edcd2}"  # override with the release tag when one exists
git checkout "$RELEASE_REF"
git submodule update --init --recursive

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e ".[workflow,web]"
.venv/bin/datalad --version
git annex version
```

Retrieve the data metadata and canonical artifact:

```bash
git -C data annex enableremote arbutus-s3
cd data
datalad get metadata manifests
datalad get derived/national/clews/osecan/fleet_capacity_119m
cd ..
git -C data annex whereis derived/national/clews/osecan/fleet_capacity_119m/dispatch_by_zone.csv
sha256sum data/derived/national/clews/osecan/fleet_capacity_119m/dispatch_by_zone.csv
```

The canonical dispatch SHA-256 is
`eeba3efda2a81f112418a656a5ed12af478f57f0c2bd73e0080c26cfb8d4da96`.

### 4.3 Model setup

```bash
.venv/bin/python -m workflows.ui.setup_osecan_model \
  --base /path/to/C2070 \
  --out runtime/osecan_model \
  --python /path/to/osecan/.venv/bin/python \
  --external-data /path/to/C2070/data/external
```

Omit `--base` to clone `https://github.com/deltae/C2070.git`; the script checks
out `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44`, applies the four committed UI
patches in order, verifies patch hashes, runs the OSeCAN cleaners and
harmonizer, and writes `model_manifest.json`.

Verify the manifest before solving:

```bash
python -m json.tool runtime/osecan_model/model_manifest.json
```

Check `capacity.summary.observed_gw` = `145.02`, coal phase-out by 2030,
`PWRBIO` biomass capacity, zero `PWRBSW`/`PWRBCW`, and the recorded tree hash.
The setup fails closed on a commit mismatch, patch-hash mismatch, missing
`SUPBCWT1`–`T4` or `SUPBCWB1`–`B2` structure, raw fuel-basis emission factor,
missing 2021–2070 residual capacity, a 2021 fleet total other than 145.02 GW, or
harmonization failure.

### 4.4 Dry run and real 119 Mm³ run

```bash
.venv/bin/python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out /tmp/screening_119m_dry --dry-run

.venv/bin/python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out /tmp/screening_119m_real \
  --model-root runtime/osecan_model
```

The dry run writes a completed summary with `dry_run: true`, the parameter set,
and harmonize evidence, but no dispatch or solver status. The real run writes
`run_summaries.json`, `dispatch_by_zone.csv`, `manifest.json`, `supply_caps.json`,
and the disposable `model/` and `run/` directories.

Expected canonical values in `run_summaries.json`:

- `objective_musd`: **1,445,655.44**
- `emissions_total_mt`: **424.710127**
- `emissions_2050_mt`: **0.0**
- solver status: `optimal`
- 2050 national BCW: 152.922679 PJ; PWRBCW 2050: 41.108217 PJ

The observed wall time for the live API acceptance run was **3 m 42 s**. Treat
that as an observed host result, not a service-level target.

Optional interface check (no policy claim):

```bash
.venv/bin/python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/policy_119m.json \
  --out /tmp/policy_119m_dry --dry-run
```

### 4.5 UI static and live

Static fallback:

```bash
python3 -m http.server 8000 -d ui
# open http://localhost:8000/
```

Expect the **Static mode · no backend** badge, 53 scenarios / 18 families / 1
Pareto-eligible, 13 zone rows plus a separate `CAN` row, 11 C2070 zone gaps, and
no C2070 polygons. `policy_test_v1` is disabled in static mode.

Live runner:

```bash
.venv/bin/python -m workflows.ui.server --port 8013 --allow-runs
# open http://127.0.0.1:8013/
```

Exercise create → queue → SSE → output, cancel a queued and a running job, retry
from the immutable snapshot, and reload the page to confirm session hydration.
`/api/health`, `/api/version`, and `/api/roles` report the active role and
effective `read_only` / `allow_runs` values.

Public and analyst shapes:

```bash
.venv/bin/python -m workflows.ui.server --port 8013 --role public --read-only
.venv/bin/python -m workflows.ui.server --port 8014 --role analyst --allow-runs --max-concurrent-runs 2
```

### 4.6 Deployment templates

`deploy/` contains templates only; nothing there starts a service. On a prepared
host:

```bash
sudo install -m 0644 deploy/clews-ui-public.service /etc/systemd/system/
sudo install -m 0644 deploy/clews-ui-analyst.service /etc/systemd/system/
sudo install -m 0644 deploy/clews-ui-analyst@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clews-ui-public.service clews-ui-analyst.service
```

The public unit binds `127.0.0.1:8013` with `--role public --read-only`; the
shared analyst unit binds `127.0.0.1:8014` with `--role analyst --allow-runs
--max-concurrent-runs 2`. Keep all origins loopback-only. Cloudflare Tunnel is
the only ingress; Cloudflare Access or the configured edge policy is the identity
boundary. The application has no login form, user table, session cookie,
password store, or token validation. The role flag selects allowed actions; it
is **not** authentication.

For v1.2, one process/runtime per analyst or team uses
`deploy/run_instance.sh` and `clews-ui-analyst@.service`:

```bash
# systemd template: one instance account, one root-owned 0600 env file
sudo install -m 0600 deploy/instance-env.example /etc/clews-ui/<instance>.env
sudoedit /etc/clews-ui/<instance>.env   # PORT=8100, ROLE=analyst
sudo systemctl enable --now clews-ui-analyst@<instance>.service

# direct launch (same contract; useful for a temporary smoke test)
CLEWS_UI_BASE_DIR=/var/lib/clews-ui \
  ./deploy/run_instance.sh <instance> 8100 analyst
```

The launcher validates the instance name and port, creates a `0700` runtime,
takes a per-runtime lock, and refuses a busy port or existing server. It passes
`--allow-runs` only for `analyst`; `model_builder` gets no solver runs, and
`public`/`energy_modeller` are explicitly read-only. Instance names are 1-20
lowercase letters/digits/hyphens, starting with a letter and not ending with a
hyphen. The systemd template uses `%i` in `EnvironmentFile`, `RuntimeDirectory`,
and `StateDirectory`, with per-instance account `clews-ui-<instance>` and no
secrets in the unit. Map each instance hostname to its own Access policy.

Smoke tests from the host:

```bash
curl -fsS http://127.0.0.1:8013/api/health
curl -fsS http://127.0.0.1:8013/api/version
curl -fsS http://127.0.0.1:8013/api/roles
curl -fsS http://127.0.0.1:8014/api/health
```

Expect public: `role=public`, `read_only=true`, `allow_runs=false`; analyst:
`role=analyst`, `read_only=false`, `allow_runs=true`. A public `POST
/api/scenarios` or `POST /api/runs` must return 403. Verify `ss -ltnp` shows
loopback binds only, and that each analyst hostname is denied without Access.

The recorded local v1.2 deploy smoke started two instances on temporary ports
and runtime dirs, checked `/api/health`, `/api/version`, and `/api/roles` on
both, confirmed distinct `0700` runtime directories and analyst/public mode
flags, confirmed public POST 403, and stopped both without running a solver. It
is implementation evidence, not host/edge acceptance.

### 4.7 Restore drill

Run this in a clean directory before public publication. **Executed
2026-11-20:** PASS for the archive checkout, local data materialization proxy,
model rebuild, 119 Mm³ dry run, 119 Mm³ solve, static bundle, and public/analyst
API role smoke. The run record, hashes, and remaining sub-gates are in
[`docs/release-acceptance.md`](release-acceptance.md). A remote `datalad get`
restore, a fresh virtualenv install, and deployment-host/edge checks were not
repeated in that drill.

1. **Clean checkout:** clone the release commit to a new directory, initialize
   the `data` submodule, create `.venv`, install `.[workflow,web]`, and retrieve
   `data/metadata`, `data/manifests`, and the canonical artifact. Verify
   `git annex whereis` and the dispatch SHA-256.
2. **Model setup:** run `setup_osecan_model` into a fresh
   `runtime/osecan_model`; verify the source commit, patch hashes, 145.02 GW
   fleet, and tree hash.
3. **119 Mm³ dry run:** run `screening_119m.json` with `--dry-run`; confirm the
   summary, parameter set, and harmonize evidence, with no dispatch.
4. **119 Mm³ real run:** run with `--model-root runtime/osecan_model`; confirm
   `optimal`, 1,445,655.44 M$, 424.710127 Mt CO₂, 0.0 Mt CO₂ in 2050, and the
   canonical dispatch hash.
5. **UI static:** serve `ui/` and confirm 53/18/1, the static badge, 13 zone rows
   plus `CAN`, 11 gaps, and no C2070 polygons.
6. **UI live:** start the server with `--allow-runs`; create, queue, stream,
   fetch output, cancel, retry, and reload. Confirm public role mutations return
   403.
7. **Publication restore:** on a restored runtime, verify
   `GET /api/runs/{id}/output` returns the published summary and dispatch with
   matching hashes.
8. **Record:** date, operator, parent commit, data commit, model manifest hash,
   run IDs, artifact hashes, and any deviation. A drill that misses a hash or a
   canonical metric is a failed drill, not a rounding difference.

### 4.8 Publication

Public runtime content is a release artifact, not a copy of the analyst working
directory. Freeze the scenario snapshots, `run_summaries.json`,
`dispatch_by_zone.csv`, `manifest.json`, `supply_caps.json`, and checksums. Write
a sanitized `publication.json` with run/scenario IDs, constraint set, commits,
labels, hashes, and date. Redact `command`, absolute paths, `pid`, and event
logs; omit `output/model/` and `output/run/`.

Publish under:

```text
data/derived/national/clews/osecan/ui_publications/<release>/
```

Commit metadata and checksums, annex the payload to `arbutus-s3`, verify with
`git annex whereis`, and restore-test with `datalad get` on a clean checkout.
Populate `/var/lib/clews-ui/public` only from the verified release. Read-only
startup creates no directories and repairs no jobs, so a missing or incomplete
runtime is a deployment error.

## 5. Release checklist

- [x] Parent and data commits recorded (`a9edcd2`, `00cba3c`).
- [x] Model source commit recorded (`40d5b43bb3f8b3f69c16e953144d35eeed5f0f44`).
- [x] Canonical artifact and dispatch hash recorded
  (`fleet_capacity_119m`, `eeba3efd…4da96`).
- [x] UI bundle counts verified (53 scenarios / 18 families / 1 Pareto-eligible).
- [x] 113 UI tests and 42 ledger/LP tests recorded as passing.
- [x] Real 119 Mm³ live API run recorded (3 m 42 s; output HTTP 200).
- [x] Chromium static/live QA recorded; spatial and role guards pass.
- [x] Recorded QA/handoff checks show no unexpected `D` entries.
- [x] Documentation package prepared (`docs/model-card.md`,
  `docs/release-v1.md`); commit and tag are separate release actions.
- [x] v1.2 per-instance launcher/template added; two-instance local smoke passed
  with distinct `0700` runtimes, distinct ports, role flags, and public 403.
- [x] Clean-checkout restore drill executed and recorded
  (`docs/release-acceptance.md`; local data proxy and shared environment).
- [ ] Deployment-host smoke tests pass (systemd, tunnel, Access, public 403).
- [ ] Host/edge v1.2 acceptance (A11) with per-instance service accounts and
  one Access policy per instance.
- [x] First publication ledger entry created with artifact hashes and labels
  (`data/derived/national/clews/release/publication.json`; acceptance ledger,
  not a curated public runtime publication).
- [ ] Release tag and final sign-off recorded, if the project authorizes a tag.

## 6. Publication ledger

No curated public runtime publication has been made from this package. The v1
restore-drill acceptance ledger is recorded at
`data/derived/national/clews/release/publication.json`; it is a release
acceptance/publication ledger, not a curated runtime payload. Create a runtime
row under `ui_publications/<release>/` only after the restore drill and
deployment smoke tests pass. One row per published artifact; do not publish
analyst job paths, commands, pids, or event logs.

| Release | Date | Artifact path | Run/scenario IDs | SHA-256 | Label | Actor source | Notes |
|---|---|---|---|---|---|---|---|
| `clews-c2070-fresh-v1` | 2026-11-20 | `data/derived/national/clews/release/publication.json` | `screening_119m`; `policy_119m` (draft) | canonical dispatch `eeba3efd…4da96` | `screening_only`; `policy_grade=false` | automated release drill | Acceptance ledger only; curated runtime publication still gated |

Ledger rules:

- Record the run/scenario IDs, constraint set, canonical artifact path, and the
  SHA-256 of the summary and dispatch files.
- Record `screening_only` or `draft` and `policy_grade=false`; never relabel a
  draft as policy-grade.
- Record the edge identity or operator source; the application itself records no
  actor.
- Keep Arbutus as the special-remote store, not the only backup. Retain a
  separate off-host copy of published artifacts.
- Promote before deleting; record deletions. The app has no log rotation, so
  rotate or archive `events.jsonl` and old run directories.

## 7. Known caveats and non-claims

| Caveat | Consequence | Handling |
|---|---|---|
| No observed delivered-log revenue or full production costs; net value `not_estimated` | Cannot report net value, ROI, or benefit-cost | Report ledger coverage and `not_estimated`; do not insert zero or a proxy |
| Operable proxy is not a legal THLB | 150 Mm³ is conditional/infeasible under intensity screens; no legal AAC claim | Label 144.69 Mha as a screening landbase; keep 1.0/1.2 deficits visible |
| 11 C2070 zones have no authoritative polygons | No spatial zone map beyond B1/B2 anchors | Show gaps, not invented geometry, centroids, or interpolation; the 13-zone table is a reporting fallback, not a boundary |
| UI is provisional, not C2070 official | Do not present the explorer or its exports as an official C2070 product | Keep the provisional label; cite source artifacts and flags |
| `policy_test_v1` is draft | Not policy-grade; difference from canonical is not a policy effect | Keep `draft` and `policy_grade=false`; do not present as energy-policy advice |
| UI is single-tenant within one runtime; no in-app auth | Any caller admitted to one writable instance can list, overwrite, cancel, or retry that runtime's stored runs | Keep origins loopback; use the edge policy; run one instance/runtime per trust boundary with `0700` and a per-instance account; in-app tenancy is not provided |
| Restart is fail-closed | Queued/running jobs become `interrupted` | Retry from the immutable snapshot; do not claim transparent resume |
| Model rebuild is patch-dependent | Public base plus local patches; source tree reports dirty by design | Record source commit, patch hashes, and tree hash; use the setup manifest |
| Pre-fleet scenarios are historical | Legacy rows are not comparable with the canonical fleet run; the former 26-scenario ledger has not been rerun under the fleet model | Keep `superseded_for_fleet=true`; exclude from default Pareto/trade-off |
| No national supply forecast | RSYC batch is a pipeline test; 119 Mm³ is an observed hold | Do not report as a forecast or sustainable-supply estimate |

**Non-claims.** This package does not assign funding, data licences, publication
rights, or author order; it does not replace a signed collaboration agreement.
It does not make `policy_test_v1` policy-grade, validate the RSYC chain, the
residue curve, or OSeCAN, define C2070 boundaries, or authorize publication of
any artifact. A signed agreement or active issue governs if it conflicts with
this document.

## 8. Handoff contents and next actions

Give the receiving team:

- the release tag/commit, parent and data commits, and model manifest;
- this package and `docs/model-card.md`;
- the canonical summary, dispatch, manifest, supply caps, and hashes;
- test and Chromium QA evidence;
- the known-caveat list and the non-claims above;
- the publication ledger, with no credentials or personal paths.

Next actions:

1. Record the restore-drill sign-off (`docs/release-acceptance.md`) and close
   the remaining remote-data, deployment-host, and runtime-publication gates.
2. Smoke-test the public/analyst deployment on the prepared host.
3. Publish the first curated runtime and create the first ledger entry.
4. Review the `SUPBCW`/`B1`/`B2` interface with Taco Niet's team; classify any
   disagreement as forestry data, interface mapping, or energy model before
   changing either side.
5. Keep `policy_test_v1` draft until a separate signed process authorizes
   policy-grade use.
6. Treat in-app tenancy, C2070 boundaries, and observed delivered-log
   revenue/full production costs as open work, not v1 claims. The v1.2
   deployment assets isolate instances; they do not add in-app accounts.

## 9. References

- `docs/model-card.md` — model card.
- `planning/module-boundaries.md` — ownership, interface contract, and reference
  results.
- `planning/policy-test-v1.md` — draft constraint-set design and non-claims.
- `planning/fleet-correction-canonicalization.md` — canonical 119 Mm³ result.
- `planning/harvest-aac-feasibility.md` — harvest/AAC screen and allocation
  caveats.
- `planning/forest-cost-revenue-ledger.md` — ledger perspectives and
  `not_estimated` rule.
- `planning/ui-deployment-v1.md` — deployment roles, acceptance criteria, and
  staged rollout.
- `ui/README.md`, `workflows/ui/README.md` — UI contract, runner, roles, model
  setup, and reproducibility gap.
- `ui/qa/QA_REPORT.md`, `ui/qa/real-run-119m.md` — QA and live-run evidence.
- `deploy/README.md` — systemd, Cloudflare Tunnel/Access, publication, backup,
  retention, and incident notes.
- `docs/data-repository-runbook.md` — DataLad and `arbutus-s3` operations.
