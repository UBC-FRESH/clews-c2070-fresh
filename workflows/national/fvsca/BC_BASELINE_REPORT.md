# BC FAIB PSP FVS-BC baseline (first measured run)

Status: measured baseline; no parameter fitted
Date: 2026-11-15
Runner: read-only `fresh-fvsbc` checkout
`/srv/shared-data/gep/ws4/tmp/fresh-fvsbc` at `f5dce34`
(`v0.21.4-212-gf5dce34`)
Data: `data/derived/fvsca_calibration/bc_baseline/` (data commit `0d577cd`)

This report records the first measured uncalibrated FVS-BC baseline run from
this workspace. It grows each PSP interval from its first measurement to the
second with no cut and no calibration. It is a reference, not a calibration,
and it supports no yield, AAC, legal, or whole-block claim.

The run reproduces the reference D6-corrected all-zone baseline exactly: 139
BEC strata, 139 intervals, 0 skipped, initial BA bias -0.69 / MAE 0.84 m2/ha,
end BA bias -4.17 / MAE 8.65 m2/ha, 1 interval above p99 and 5 above the
observed maximum. Per-BEC bias and MAE agree with the reference
`d6-all-zone-summary.csv` to a maximum absolute difference of `6.9e-13`.

## 1. Reference runner

### CLI

The reference entrypoint is `scripts/run_baseline_backtest.py`:

```bash
python scripts/run_baseline_backtest.py \
  --data-dir runtime/calibration \
  --psp-dir "$FAIB_PSP_DIR" \
  --out-dir runtime/baseline \
  [--per-bec 1] [--tree-age 60] [--dry-run]
```

Required inputs:

- `--data-dir`: `intervals.csv`, `visits.csv`, `bec_envelope.csv` from
  `scripts/build_calibration_dataset.py`.
- `--psp-dir`: the four FAIB CSV files `faib_header.csv`,
  `faib_sample_byvisit.csv`, `faib_plot_header.csv`, and
  `faib_tree_detail.csv` (uncompressed names; the builder does not read
  `.gz` directly).
- `--out-dir`: output directory.

Sampling: `select_intervals(intervals, per_bec=1)` sorts by `BEC`, `TSA_DESC`,
`SITE_IDENTIFIER`, `PLOT`, and `year0`, then takes the first `per_bec`
intervals per BEC. The default is one interval per BEC; it does not enumerate
the 7,341 sites.

Output files: `baseline_backtest.csv` (per interval), `baseline_summary.csv`
(by BEC), `baseline_by_tsa.csv`, `baseline_by_species.csv`,
`baseline_by_dbh.csv`, `baseline_by_ba.csv`, `baseline_skips.csv`,
`baseline_skip_reasons.csv`, `baseline_skipped_becs.csv`,
`baseline_report.md`, and one `runs/<interval_id>/` directory per FVS case.

### Calibration probe

`scripts/run_calibration_probe.py` takes the same data and PSP directories,
plus `--domain`, `--per-bec`, `--candidates` (required JSON list),
`--ingrowth` or `--bundle`, `--test-sites`, `--min-ba0`,
`--max-mortality-fraction`, `--min-qmd`, `--min-dbh`, and `--cycles`. It
writes `probe.csv`, `probe_summary.csv`, `probe_by_bec.csv`,
`probe_skipped.csv`, and `probe.json`. It evaluates fixed candidates; it does
not fit.

### Documented reference numbers

- **W1.4** (2026-11-02, pre-`0.3.13` build): 89 intervals / 89 BEC variants,
  19 variants skipped because the variant did not resolve their subzone;
  initial BA bias -0.87, median -0.64, MAE 0.93 m2/ha; 5 predictions above the
  observed maximum and 1 above p99; `SBPSmc` predicted 11,214 m2/ha against
  30.6 observed and `SBPSdc` 4,705 against 11.2.
- **D5** (2026-11-03, `fresh-fvsbc-pyfvs 0.4.0`): 139 BEC strata / 139
  intervals / 0 skipped; initial BA bias -0.69, MAE 0.84; end BA excluding
  SBPS and envelope-flagged rows bias -5.27, MAE 8.45 (n=130); 1 above p99 and
  7 above the observed maximum; `SBPSmc` predicted 457,348 m2/ha against 30.65
  observed, with `SBPSdc` and `SBPSmk` also above max.
- **D6 correction** (2026-11-03, `0.4.1`): the SBPS explosion was a `REGENT`
  model-selection defect, not a density or mortality parameter. The corrected
  all-zone run is 139 intervals, 0 skipped, initial BA bias -0.69 / MAE 0.84,
  end BA bias -4.17 / MAE 8.65, 1 above p99 and 5 above max.

## 2. Environment

An existing environment was not present (`fresh_fvsbc` was not importable;
`fresh-fvsbc` and `fresh-fvsbc-pyfvs` were not installed). A repository-local
environment was created at
`/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh/.venv-fvsca`
with the local checkout and the `fvs` extra:

```bash
python3 -m venv .venv-fvsca
.venv-fvsca/bin/pip install --upgrade pip setuptools wheel
.venv-fvsca/bin/pip install -e "/srv/shared-data/gep/ws4/tmp/fresh-fvsbc[fvs]"
```

Installed pins:

| Component | Pin |
|---|---|
| `fresh-fvsbc` | 0.21.4, editable from checkout `f5dce34` (`v0.21.4-212-gf5dce34`) |
| `fresh-fvsbc-pyfvs` | 0.4.1 |
| PyFVS commit recorded by the wrapper | `e95172b` |
| FVS source commit recorded by the wrapper | `ef5fa847` |
| Python | 3.12.3 |
| pandas | 3.1.1 |
| numpy | 2.5.4 |
| six | 1.17.0 |

The BC extension loads:

```text
pyfvs.list_variants() -> {'BC': {'lib': 'pyfvs.fvsbc', 'status': 'OK'}}
pyfvs/fvsbc.cpython-312-x86_64-linux-gnu.so
```

The reference checkout has no tracked modifications. The wrapper version
string is `0.21.4` even though the checkout is 212 commits past the `v0.21.4`
tag; the baseline path in this checkout is the one that produced the D6
numbers, and the run below reproduces the D6 summary exactly.

## 3. Input snapshot

The raw archive is
`data/raw/bc_faib_ground_plots/psp/` (10 gzipped CSVs). The four inputs used
by the builder were decompressed to the temporary shim `/tmp/fvsca_psp/`; the
archive was not modified. The decompressed SHA-256 values match the reference
`runtime/calibration/manifest.json`, so this is the same FAIB snapshot used by
W1.4, D5, and D6:

| File | Compressed SHA-256 | Decompressed bytes | Decompressed SHA-256 |
|---|---|---:|---|
| `faib_header.csv` | `c385452c...5e5bf19d` | 1,406,012 | `f029e21c...079ea8b2` |
| `faib_sample_byvisit.csv` | `9e8488ed...e0599461` | 3,186,768 | `9b6db291...52fece60` |
| `faib_plot_header.csv` | `cacc06c5...8bf56e9f` | 2,815,639 | `dcb206e5...b45514ae` |
| `faib_tree_detail.csv` | `9ed7b0a2...76d614e8` | 740,181,676 | `d9f609cd...3a4c910` |

Full values are in the data manifest. The dataset build
(`scripts/build_calibration_dataset.py`, default `include_unsupported=False`)
produced 7,341 header rows, 23,956 visits, 24,962 plot-visit summaries, 17,151
intervals, 139 BEC units, and 1,814,527 tree-survival rows in 40.4 s (peak RSS
1.53 GiB). The 17,151 intervals differ from the W3 report's 9,186 because the
current wrapper resolves all twelve BEC zones; the W3 dataset predates
five-zone support. The current build is row-for-row identical to the reference
`runtime/all-zone-d6` manifest.

## 4. Sample design

The default reference sample was used: **139 intervals, one per BEC variant**
(139 BEC units), selected deterministically by `BEC`, `TSA_DESC`,
`SITE_IDENTIFIER`, `PLOT`, and `year0`. Interval lengths are 3 to 27 years,
mean 10.76. This is the documented stratified baseline design, not a full
enumeration of 7,341 sites.

Why not the full set: each interval is a separate FVS child process. The
measured throughput is about 0.55 s per interval (139 intervals in 80 s wall),
so the full 17,151-interval table would take roughly 2.6 hours, outside this
run's window. The 139-interval sample is the reference's own baseline sample
and the one used by D5/D6.

Skips:

- 0 intervals skipped.
- 2 intervals have trees whose species do not resolve to one of BC's 15:
  `ESSFwc4` (19 trees) and `PPxh2` (4 trees), 23 trees total. They are
  counted, not forced into another species.

## 5. Command and runtime

```bash
.venv-fvsca/bin/python \
  /srv/shared-data/gep/ws4/tmp/fresh-fvsbc/scripts/run_baseline_backtest.py \
  --data-dir /tmp/fvsca_calibration \
  --psp-dir /tmp/fvsca_psp \
  --out-dir /tmp/fvsca_baseline \
  --per-bec 1 \
  --tree-age 60
```

Runtime: **80.1 s wall**, 474.9 s CPU (FVS child processes), **452,880 KB
peak RSS** (about 442 MiB). The output is 139 compared intervals and 0 skipped.
The aggregate tables (about 350 KB) are committed under
`data/derived/fvsca_calibration/bc_baseline/`; the per-interval table and the
`runs/` directories stay under `/tmp/fvsca_baseline/` because `interval_id`
carries `SITE_IDENTIFIER`, matching the reference's data-boundary practice.

## 6. Baseline results

### Overall, n = 139 intervals

| Metric | Mean observed | Mean predicted | Bias | Median bias | MAE | RMSE |
|---|---:|---:|---:|---:|---:|---:|
| Initial BA (m2/ha) | 37.020 | 36.331 | -0.689 | -0.487 | 0.836 | 1.367 |
| End BA (m2/ha) | 39.420 | 35.252 | -4.168 | -3.831 | 8.648 | 11.410 |
| End TPH (stems/ha) | 2,201.605 | 1,809.755 | -391.850 | -88.945 | 781.732 | 1,996.456 |
| End QMD (cm) | 18.676 | 19.578 | 0.901 | -0.222 | 2.478 | 3.874 |
| End top height (m) | 27.525 | 22.705 | -4.820 | -4.400 | 4.958 | 5.747 |
| End volume (m3/ha, FVS `MCuM`) | 302.444 | 248.554 | -53.890 | -46.006 | 84.242 | 112.375 |
| Net BA rate (m2/ha/yr) | 0.284 | -0.122 | -0.406 | -0.438 | 0.794 | 1.005 |
| Net TPH rate (stems/ha/yr) | 9.608 | -26.090 | -35.698 | -8.900 | 73.501 | 204.367 |

The initial stand is reproduced closely. The end-of-interval BA is
underpredicted on average by 4.17 m2/ha. The end TPH mean is dominated by a
small number of large residuals; the median bias (-88.9 stems/ha) is the more
useful central tendency.

### By BEC zone

| Zone | Intervals | BA bias | BA MAE | TPH bias | TPH MAE | Above p99 | Above max |
|---|---:|---:|---:|---:|---:|---:|---:|
| CWH | 21 | -9.46 | 11.75 | -151.53 | 262.47 | 0 | 0 |
| BWBS | 5 | -7.93 | 7.93 | -275.74 | 333.49 | 0 | 0 |
| CDF | 1 | -18.85 | 18.85 | -143.46 | 143.46 | 0 | 0 |
| CMA | 1 | -3.46 | 3.46 | -14.20 | 14.20 | 0 | 0 |
| MH | 3 | -11.60 | 11.60 | -335.63 | 335.63 | 0 | 0 |
| ICH | 26 | -3.03 | 6.02 | -586.28 | 841.10 | 0 | 0 |
| IDF | 18 | -3.27 | 3.90 | -1,067.97 | 1,149.75 | 0 | 1 |
| MS | 10 | -7.72 | 7.76 | -624.40 | 739.35 | 0 | 0 |
| ESSF | 30 | -8.09 | 10.94 | -734.31 | 1,065.89 | 0 | 0 |
| SBS | 18 | 6.19 | 8.38 | 770.18 | 971.66 | 1 | 3 |
| SBPS | 4 | 13.75 | 13.75 | 1.53 | 132.96 | 0 | 1 |
| PP | 2 | 3.44 | 10.09 | 64.47 | 180.48 | 0 | 0 |

BA units are m2/ha; TPH units are stems/ha.

### Envelope flags and largest residuals

- Above p99: `SBSwk1` (predicted 62.0 against 59.46 observed).
- Above the observed maximum (5): `IDFxx2` (45.0 vs 39.86), `SBPSxc` (53.0 vs
  34.39), `SBSmc3` (57.0 vs 26.40), `SBSun` (17.0 vs 14.32), and `SBSwk3`
  (48.0 vs 39.64).
- Largest absolute end-BA residuals: `ESSFmv2` -34.43, `ESSFmv4` -33.39,
  `SBSdk` +30.74, `SBSmc3` +30.60, `MSdk` -24.14, `CWHvm` +24.11, `ESSFmv3`
  +24.09, `ESSFdc2` -23.41, `ESSFxv2` -22.63, and `CWHws2` -20.64 m2/ha.

The SBPS rows are now physical. The D5 `457,348 m2/ha` row is absent, as
expected on the `0.4.1` build with the `REGENT` fix.

## 7. Comparison to W1.4 and D5

| Run | Build | Intervals | Skipped | Initial BA bias / MAE | End BA bias / MAE | p99 / max flags |
|---|---|---:|---:|---:|---:|---:|
| W1.4 | pre-`0.3.13` | 89 | 19 | -0.87 / 0.93 | not reported | 1 / 5 |
| D5 | `0.4.0` | 139 | 0 | -0.69 / 0.84 | -5.27 / 8.45 (n=130, excl. SBPS+flags) | 1 / 7 |
| D6 corrected | `0.4.1` | 139 | 0 | -0.69 / 0.84 | -4.17 / 8.65 (n=139) | 1 / 5 |
| **This run** | **`0.4.1`** | **139** | **0** | **-0.689 / 0.836** | **-4.168 / 8.648 (n=139)** | **1 / 5** |

The like-for-like D5 filter (exclude SBPS and envelope-flagged rows) on this
run gives bias -5.259 and MAE 8.445 at n=130, effectively identical to D5's
-5.27 / 8.45. The all-row end-BA numbers are also identical to the D6
correction (-4.17 / 8.65). Per-BEC values match `d6-all-zone-summary.csv` to
floating-point precision.

Differences and why they are not contradictions:

- **Build generation.** W1.4 ran before the BEC resolver fix (`0.3.13`), so 19
  subzones were skipped and `SBPSmc`/`SBPSdc` exploded. D5 ran the pre-fix
  five-zone `0.4.0`, so `SBPSmc` reached 457,348 m2/ha. This run uses `0.4.1`,
  which includes the `REGENT` SBPS small-tree fix; SBPS is physical and above
  max only for `SBPSxc`.
- **Sample size and coverage.** W1.4 had 89 BEC variants because the subzone
  resolver did not cover the W2.3 units. The current dataset resolves all
  twelve zones and has 139 BEC units, the D5/D6 sample.
- **Snapshot.** The four input SHA-256 values are identical to the reference
  `runtime/calibration` manifest, so the difference is not a newer data
  snapshot.
- **DBH limits.** Both the builder and this run use the default `min_dbh=0.0`;
  no 4 cm cross-program limit was applied. The calibration probe likewise
  used the default.
- **Dead-endpoint handling.** The start tree list uses `LV_D == "L"` and
  `PHF_TREE > 0`; the observed next visit is built from live trees only.
  Dead and missing endpoints are retained in the tree-survival table and are
  not treated as live. This is the same code path as the reference.
- **Placeholders.** `SITECODE` is `FD,20`, the site series is the diagnostic
  `/01`, and tree age is 60 years because a PSP interval has no single age.
- **Flag identity.** The p99/max counts match W1.4 and D6, but the flagged BECs
  differ because the SBPS fix and the 139-unit sample change which intervals
  cross the empirical envelope.

The initial BA MAE of 0.836 is slightly better than W1.4's 0.93 because the
sample is the 139-unit D5/D6 sample, not the 89-unit W1.4 sample; it is not a
tree-list change.

## 8. Optional calibration probe (IDFdk)

The baseline finished in 80 s, so the optional probe was run. The reference
bundle path (`--bundle d7-idfdk-bundle.json`) fails in the current probe
script:

```text
fresh_fvsbc.bundle.BundleError: idfdk-ingrowth-pilot-2026-11-03 renders
BAMAX, and the keyword set does not carry them; render the bundle into the
set before the run
```

The cause is a field-format mismatch: the probe's `parameter_keywords()`
renders `BAMAX 53.0` as `53`, while the bundle renders `53.0`, and
`run_case` compares keyword fields exactly. The reference checkout is
read-only, so the documented `--ingrowth` path was used instead.

### D7.4 ingrowth pilot (documented path)

Command (output under `/tmp/fvsca_probe_idfdk`):

```bash
.venv-fvsca/bin/python scripts/run_calibration_probe.py \
  --data-dir /tmp/fvsca_calibration --psp-dir /tmp/fvsca_psp \
  --out-dir /tmp/fvsca_probe_idfdk --domain IDFdk --per-bec 5 \
  --candidates notes/reference/calibration/d7-idfdk-ingrowth-candidates.json \
  --ingrowth notes/reference/calibration/d7-idfdk-ingrowth.json \
  --test-sites 4024432,4024433,4024439,4025039,4025040
```

The sample has 29 intervals (IDFdk4 has only four eligible intervals; every
other label contributes five), with 7 in the 100 Mile House spatial holdout.

| Fold | n | BA bias | BA MAE | BA % | TPH bias | TPH MAE | TPH % |
|---|---:|---:|---:|---:|---:|---:|---:|
| train | 22 | -0.08 | 3.23 | -0.3% | +602.74 | 674.43 | +37.4% |
| test | 7 | -1.76 | 1.79 | -5.6% | +204.65 | 327.16 | +8.6% |

This reproduces the **D9.24 corrected** numbers (test BA -5.6%, TPH +8.6%;
train TPH +37.4%), not the stale D7.4 table (test TPH -8.7%). The installed
`0.21.4` wrapper carries the corrected TPH-to-trees-per-acre conversion. The
test BA absolute value differs from the old -1.90 because the old table
predates the correction; the percentage is the comparable quantity and it
matches.

### D7.3 candidate ranking (four-candidate subset)

To exercise the candidate ranking within the time box, the four candidates
named in the readiness report were run without ingrowth: `bamax53`,
`bamax55`, `bamax60`, and `bamax60-mort0.3` (116 rows, 67.6 s). The test-fold
numbers reproduce the documented D7.3 table exactly:

| Candidate | BA bias | BA MAE | TPH bias | TPH MAE | Documented? |
|---|---:|---:|---:|---:|---|
| `bamax60-mort0.3` | +0.10 | 1.68 | -352.92 | 432.20 | exact |
| `bamax60` | -1.61 | 1.65 | -468.49 | 505.44 | exact |
| `bamax55` | -1.76 | 1.79 | -481.78 | 514.72 | exact |
| `bamax53` | -1.90 | 1.93 | -487.78 | 518.44 | exact |

Ranking: `bamax60-mort0.3` is the only candidate inside the 15% TPH threshold
(-14.9%), but its `MORTMULT 0.3` is below the protocol's 0.5 soft bound, so it
is not a releasable set. `bamax60` has the best BA MAE. The default `bamax53`
is the weakest of the four on both BA and TPH. This agrees with the D7.3
conclusion: the TPH deficit is not a capacity problem, and the fix is
ingrowth, not a global mortality multiplier.

Probe aggregates are committed under
`data/derived/fvsca_calibration/bc_baseline/probe_idfdk/`; per-interval probe
rows and run directories stay in `/tmp` because they carry site identifiers.

## 9. Caveats

- One interval per BEC. This is a coverage and failure-mode reference, not a
  province-wide mean or a random sample of BC stands.
- The empirical envelope is computed from the same PSP snapshot; it is not an
  independent biological maximum.
- Site index is not joined. `SITECODE` is `FD,20`, the site series is `/01`,
  and tree age is the placeholder 60.
- Species outside BC's 15 and outside the donor alias map are skipped and
  counted; they are not forced into a group.
- FVS `MCuM` merchantable volume and the PSP `VOL_MER` gross merchantable
  volume are not guaranteed to use the same utilization limits; volume
  residuals should be read with that definitional caveat.
- The reference checkout is post-`v0.21.4` (`f5dce34`). The baseline code path
  reproduces the D6 summary exactly, but the checkout is not a tagged release.
- The per-interval table and FVS run directories are not committed; they carry
  site identifiers. Only aggregate tables, the reference report, the metrics
  summary, and the probe aggregates are in the data repository.
- No calibration was fitted, and no parameter default changed.

## 10. Next steps

1. **Calibration probe, bundle-aware.** Fix the `BAMAX` field-format mismatch
   in `run_calibration_probe.py` (or normalize keyword fields in
   `run_case`) so `--bundle d7-idfdk-bundle.json` runs directly, then repeat
   the IDFdk holdout from the bundle rather than the raw ingrowth JSON.
2. **Deepen the baseline.** Run five intervals per BEC for the 139 units (695
   cases) to separate interval noise from a BEC-level residual; the measured
   throughput puts this at about 6-7 minutes.
3. **Residual follow-up.** `ESSFmv2`, `ESSFmv4`, `MSdk`, `ESSFdc2`, and
   `ESSFxv2` are large BA underpredictions; `SBSdk`, `SBSmc3`, and `CWHvm` are
   large overpredictions. Check whether these are species-mix, site-index, or
   small-tree density effects before fitting a multiplier.
4. **ON adapter and TSA factory.** The Ontario profile is still a stub. The
   next multi-variant step is the ON plot adapter and a TSA-level factory that
   runs the same baseline/probe contracts on a second jurisdiction.
5. **Full-domain design.** Extend the baseline to every W2-resolved domain and
   publish a residual atlas by BEC, TSA, species group, DBH class, and BA
   class, with the envelope flags retained as an oracle.

## 11. Files, commits, and publication

- Parent report: `workflows/national/fvsca/BC_BASELINE_REPORT.md`.
- Data outputs: `data/derived/fvsca_calibration/bc_baseline/`, data commit
  `0d577cd` (`Add first measured FVS-BC baseline on FAIB PSP`).
- Data manifest: `data/derived/fvsca_calibration/bc_baseline/manifest.json`
  (input hashes, environment pins, command, runtime, sample, output hashes,
  caveats).
- Full run artifacts: `/tmp/fvsca_baseline/` (per-interval table and 139 run
  directories, withheld from the data commit by the site-identifier rule).
- Probe artifacts: `/tmp/fvsca_probe_idfdk/` and
  `/tmp/fvsca_probe_idfdk_d73/`; aggregate summaries are in the data
  directory.
- Publication: the data commit is pushed to `origin/main`; `git-annex-s3` is
  run for the data path. The committed baseline tables are text CSVs, so no
  new annex object is expected; the check is
  `git -C data annex find --not --in arbutus-s3`.
- Parent commit and branch push are recorded in `CHANGE_LOG.md` and
  `EVIDENCE_LOG.md` at the same time as this report.
