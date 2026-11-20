# v1 clean-checkout restore drill and release acceptance

| Field | Value |
|---|---|
| Release label | `clews-c2070-fresh-v1` |
| Drill date | 2026-11-20 |
| Operator | Automated release-drill subagent |
| Parent baseline | `18a11163853aa2156d501ee0c6a4709b0bd6a364` |
| Data baseline | `00cba3c50966a6b1ccd3519bfafece0c03cc46ef` |
| OSeCAN source | `deltae/C2070` @ `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` |
| Drill tree | `/tmp/clews_release_drill` from `git archive HEAD` (no `.git`) |
| Archive SHA-256 | `6a9c7574d967e7dd6ba9fa4dcd428309e1fdbe957718157aa6b89cdec344da15` |
| Result | **PASS** for the required restore gates, with the caveats below |
| Publication ledger | `data/derived/national/clews/release/publication.json` |

This record supersedes the open restore-drill gate in
[`docs/release-v1.md`](release-v1.md) §4.7. It is not a deployment-host, edge,
or curated public-runtime acceptance. No public OSeCAN repository was modified,
committed, or pushed during the drill. No git tag was created.

## 1. Clean checkout and data materialization

The drill used a source archive of the recorded parent commit, extracted into a
new directory with no `.git` directory:

```bash
git archive HEAD | tar -x -C /tmp/clews_release_drill
ln -s <main-repo>/data/derived /tmp/clews_release_drill/data/derived
```

`data/derived` was a **local materialization proxy**. The drill did not run
`datalad get` against the `arbutus-s3` special remote. The canonical artifact,
UI bundle, canonical constraint set, and policy set were read from the existing
local data submodule working tree. The data submodule was clean before and after
the drill. The code was imported from the archive (the drill directory was the
working directory and first import path); the existing project virtualenv was
reused rather than installing a fresh environment from the archive.

## 2. Model rebuild

The disposable model was rebuilt from the local C2070 clone at the pinned
public commit, the four committed UI patches, and the pinned CER Energy Futures
2026 demand/capacity files:

```bash
python -m workflows.ui.setup_osecan_model \
  --base /tmp/osecan_src \
  --out runtime/osecan_model \
  --python /tmp/osecan_src/.venv/bin/python \
  --external-data /tmp/osecan_src/data/external
```

| Check | Result |
|---|---|
| Exit / elapsed | 0 / **4.34 s** |
| Source commit | `40d5b43bb3f8b3f69c16e953144d35eeed5f0f44` |
| Model tree SHA-256 | `61dace92f89d6b66db7627fc7512d19a4ef69bc5f7dfea986bc417118fdf1fb0` |
| Tree hash equals existing runtime | **Yes** |
| Patches | 4 applied and SHA-256 verified (`f89dc522…`, `2fa35791…`, `54b466ae…`, `5b3bdd09…`) |
| Solver ready | `true` (otoole + swiglpk) |
| Demand provenance | CER EF2026 Current Measures, SHA-256 `ec178ec02c3f5560b98056232d1fc9e705f243a31744d557e8a2442a290d70f0` |
| Capacity provenance | CER EF2026 Current Measures, SHA-256 `8db7b8140765621b764af640248551741dbbb62200b6bce770b897863faf5fcc` |
| 2021 observed fleet | **145.02 GW**; 70 non-zero PWR rows; residual years 2021–2070 |
| PWRBIO 2021 | 2.56888 GW; PWRBSW/PWRBCW 0 GW |
| Coal phase-out | Encoded by 2030 |
| Demand 2050 | 10,300.8871 PJ |
| Generated manifest SHA-256 | `d35111f24cbe14e1d6961a5d3d215b48610b6d09db7a2a21822b4dd9b022a166` |

The generated manifest hash is timestamp-bearing and therefore differs from the
existing runtime manifest (`a8dc6dce…`); the model tree hash, source commit,
patch hashes, and provenance hashes match.

## 3. 119 Mm³ dry run

```bash
python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out out/screening_119m_dry --dry-run \
  --model-root runtime/osecan_model
```

Exit 0 in **1.54 s**. Summary: `status=completed`, `dry_run=true`,
`constraint_status=screening_only`, `screening_only=true`,
`policy_grade=false`, `solver_status=null`, no dispatch file, and harmonize
message `HARMONIZED — all params reference valid SET codes.`

The runner also emits `feasibility_status=conditional` for the `hold` reference
path because its 2021 hold value is 139.4 Mm³, above the observed 118.98 Mm³
threshold. That is the documented national harvest screen serialized by the
current runner; the committed canonical artifact predates that summary field.
It did not affect the required solve metrics or dispatch.

## 4. 119 Mm³ real solve

```bash
python -m workflows.ui.run_scenario \
  --scenario workflows/ui/examples/screening_119m.json \
  --out out/screening_119m_real \
  --model-root runtime/osecan_model
```

Exit 0 in **185.28 s** (3 min 5 s). Required acceptance values:

| Metric | Required | Observed | Match |
|---|---:|---:|---|
| Solver status | `optimal` | `optimal` | Yes |
| Objective | 1,445,655.44 M$ | **1,445,655.44** | Yes |
| Cumulative 2021–2070 emissions | 424.710127 Mt CO₂ | **424.710127** | Yes |
| 2050 emissions | 0 Mt CO₂ | **0.0** | Yes |

Additional provenance:

- Dispatch SHA-256 `eeba3efda2a81f112418a656a5ed12af478f57f0c2bd73e0080c26cfb8d4da96`
  matches the committed canonical dispatch exactly.
- Model copy tree SHA-256 `4f2c1826dcab91f51c917a9787b3f76bdc395f13b895e9d5b98c6cae4f41e992`
  matches the committed canonical manifest.
- 2050 national BCW 152.922679 PJ; tier/zonal reconciliation checks pass.
- Run summary SHA-256 `cd0a342e…`; manifest SHA-256 `8220a7c6…`;
  supply-caps SHA-256 repeated from the restored run. Summary/manifest hashes
  differ from the committed copies only because generation timestamps and
  source-root paths are serialized.

## 5. FastAPI server and static UI smoke

Temporary loopback services were started from the archive:

```bash
python -m workflows.ui.server --port 8918 --runtime-dir runtime/ui-public \
  --role public --read-only
python -m workflows.ui.server --port 8919 --runtime-dir runtime/ui-analyst \
  --role analyst --allow-runs --max-concurrent-runs 1
python3 -m http.server 8917 --bind 127.0.0.1 -d ui
```

| Check | Public | Analyst |
|---|---|---|
| `/api/health` | 200, `role=public`, `read_only=true`, `allow_runs=false` | 200, `role=analyst`, `read_only=false`, `allow_runs=true` |
| `/api/version` | 200 (`clews-ui` 0.1.0, API v1) | 200 |
| `/api/roles` | 200, public forced read-only | 200, writable role flags |
| `GET /api/scenarios` | 200 | 200 |
| `POST /api/scenarios` | **403** | not exercised |
| `POST /api/runs` | **403** | not exercised |
| UI root | 200 | 200 |

Read-only startup created no public runtime directory, as designed; the analyst
runtime directory was created. Both binds were loopback-only. Server logs
contained no exceptions.

Static UI checks:

- `/` returned 200 and `/public/data/scenarios.json` returned 200.
- All seven directly referenced assets (`api.js`, `app.js`, `styles.css`,
  ECharts, fonts, MapLibre CSS/JS) returned 200.
- The bundle reports **53 scenarios / 18 families / 1 Pareto-eligible** family;
  `scenarios.json` SHA-256 `25ac243781d419770a7bce3025eb944656a9f0122f6b33b720f70865d1444547`.
- All **63** files in `ui/public/data/manifest.json` were present with matching
  SHA-256; zero missing files and zero hash mismatches. The manifest reports
  `scenario_count=53` and `output_count=53`.
- The static server log contains no 404s or other non-200/304 responses.

## 6. Tests

Executed from the archive with the shared project environment:

```bash
python -m pytest -q tests/test_ui_app_guards.py tests/test_ui_export_iteration0.py \
  tests/test_ui_harvest_feasibility.py tests/test_ui_osecan_model_setup.py \
  tests/test_ui_osecan_runner.py tests/test_ui_policy_runner.py \
  tests/test_ui_scenario_api.py
python -m pytest -q tests/test_cost_revenue_merge.py tests/test_forest_cost_revenue.py \
  tests/test_forest_lp_v1.py tests/test_provincial_allocation.py \
  tests/test_residue_factor_allocation.py
```

| Lane | Result | Elapsed |
|---|---:|---:|
| UI | **142 passed** | 27.65 s |
| Ledger / LP / allocation | **50 passed** | 8.31 s |
| Failures | **0** | — |

These are the current drill counts; earlier release notes recorded 113 and 42
respectively before later test additions.

## 7. Deviations, caveats, and open gates

- **Local data proxy:** `data/derived` was symlinked from the main local data
  submodule. A remote `datalad get` restore from `arbutus-s3` was not exercised.
- **Shared environment:** the project virtualenv was reused. A fresh
  `pip install -e ".[workflow,web]"` from the archive was not repeated.
- **Local OSeCAN base:** the public base was a local clone at the pinned commit,
  not a fresh network clone. Commit identity and pinned CER file hashes were
  verified.
- **HTTP-level UI smoke:** the static UI was checked by HTTP status, bundle
  manifest hashes, and asset references. A Chromium interaction pass was not
  repeated in this drill.
- **No live API run:** the 119 Mm³ solve was executed through the CLI runner;
  no job was enqueued through `POST /api/runs` during the drill.
- **Harvest feasibility label:** see §3. The required objective, emissions,
  dispatch hash, and model copy hash all pass.
- **Deployment host/edge smoke:** systemd, Cloudflare Tunnel/Access, host
  firewall, and one-Access-policy-per-instance checks remain open.
- **Curated public runtime:** this ledger records release acceptance artifacts.
  It does not publish an analyst runtime payload to `ui_publications/`.
- **Tag/sign-off:** no tag was created. A signed tag and final author sign-off
  remain release-manager actions.

## 8. Conclusion

The recorded release is reconstructible from the parent archive, the pinned
OSeCAN source commit plus committed patches, and the locally materialized
canonical data. The rebuilt model reproduces the canonical tree hash and the
canonical 119 Mm³ objective, cumulative emissions, 2050 zero, and dispatch hash.
The public/analyst API role checks and static bundle checks pass.

**Restore-drill gate: PASS.** Remaining release gates are the data-remote
restore, deployment-host/edge smoke, curated public runtime publication, and
tag/sign-off.
