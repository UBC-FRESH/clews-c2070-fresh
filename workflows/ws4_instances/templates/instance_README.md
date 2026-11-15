# TSA {{TSA_NUMBER}} — {{TSA_NAME}} WS4 instance

| Field | Value |
|---|---|
| TSA | {{TSA_NUMBER}} — {{TSA_NAME}} |
| Instance | `data/derived/ws4/tsa{{TSA_NUMBER}}` |
| Compiled dataset | `{{DATASET_PATH}}` |
| Dataset manifest SHA-256 | `{{DATASET_SHA256}}` |
| Created (UTC) | {{CREATED_UTC}} |
| WS4 version | {{WS4_VERSION}} |
| Lane | Minimal WS4 lane: build/validate → area control → simulate/report |

## 1. TSA identity

This instance represents one BC Timber Supply Area. It is the unit of
comparison for the one-WS4-instance-per-TSA programme. The instance carries the
compiled dataset, the area-control config, the generated schedule, one
simulation run, and the provenance needed to rebuild or audit the product.

## 2. Data sources and provenance

- Compiled dataset: `{{DATASET_PATH}}`
- Dataset manifest: `dataset/manifest.json` (SHA-256 above)
- FEMIC source bundle: **TODO — record the bundle path**
- Patchworks recipe / ForestModelXML: **TODO — record the recipe path**
- Fragments / THLB clip: **TODO — record the fragment source and clip rule**
- Build command: **TODO — record the exact `ws4 build` command**
- Rebuild status: **TODO — rebuilt / imported from an existing compiled dataset**

Do not rewrite this section from memory. Fill it from the FEMIC run log and the
dataset manifest. A compiled dataset without a recorded recipe is a derived
product, not a reproducible instance.

## 3. AU definition

Analysis units are the `AU(<id>)` tokens in `tracks.name`. The instance config
declares the extraction rule:

```yaml
au_regex: 'AU\(([^)]+)\)'
```

Every managed block must resolve to exactly one AU. The area-control runner
fails if a block spans two AUs, if a managed track has no volume curve, or if a
managed track has no configured treatment row.

## 4. Growth model

The managed volume curve is the feature label declared in
`config/area_control.yaml`:

```yaml
volume_label: feature.Yield.managed.Total
```

For each AU the runner builds an area-weighted managed volume curve from the
managed fragments, computes `MAI(age) = volume(age) / age` for integer ages
`1..max_age`, and sets rotation age `R` to the age of maximum MAI. `R` is
derived from this instance's own curves; it is never borrowed from another TSA
or from a published AAC.

## 5. Landbase and THLB

- Managed area `A` is the sum of fragment area on managed tracks that carry the
  configured treatment.
- `A` is the THLB used by the area-control rule.
- Unmanaged fragments remain in the dataset and continue to grow, but they are
  not harvestable in this lane.
- **TODO — state the THLB mapping assumption.** For FEMIC-derived datasets this
  is usually the `theme1 == 1` managed flag; record the exact rule.

## 6. Account names

The canonical WS4 accounts used by this instance are:

- Managed area: `feature.Area.managed`
- Managed volume: `feature.Yield.managed.Total`
- Harvested volume: `product.HarvestedVolume.managed.Total.CC`
- Treated area: `product.Treated.managed.CC`

Account names are dataset vocabulary, not factory vocabulary. Add species or
product accounts here only when the dataset manifest declares them.

## 7. Treatments

The harvest treatment is declared explicitly:

```yaml
treatment: CC
```

Only the configured treatment is used to decide eligibility and to write the
schedule. A managed track without that treatment row is a hard error.

## 8. Build commands

From the parent repository, with the WS4 environment active:

```bash
# Validate the compiled dataset.
ws4 validate --dataset data/derived/ws4/tsa{{TSA_NUMBER}}/dataset

# Build the area-control schedule.
python -m workflows.ws4_instances.area_control \
  --dataset data/derived/ws4/tsa{{TSA_NUMBER}}/dataset \
  --config data/derived/ws4/tsa{{TSA_NUMBER}}/config/area_control.yaml \
  --out data/derived/ws4/tsa{{TSA_NUMBER}}/schedules \
  --periods 10 \
  --schedule-name area_control.csv

# Replay the schedule.
ws4 simulate \
  --dataset data/derived/ws4/tsa{{TSA_NUMBER}}/dataset \
  --schedule data/derived/ws4/tsa{{TSA_NUMBER}}/schedules/area_control.csv \
  --years 100 \
  --out data/derived/ws4/tsa{{TSA_NUMBER}}/runs/area_control

# Optional diagnostic report against the target file.
ws4 report \
  --run data/derived/ws4/tsa{{TSA_NUMBER}}/runs/area_control \
  --targets data/derived/ws4/tsa{{TSA_NUMBER}}/config/targets.yaml
```

## 9. Validation

- `ws4 validate` must pass on `dataset/`.
- The area-control runner must report one AU per managed block and no missing
  volume curves or treatment rows.
- Every schedule offset must be non-negative and inside the period window that
  produced it.
- No block may be harvested below its treatment `begin` age (except an
  explicitly recorded period-1 priming exception).
- The simulation must replay every schedule row; check `runs/area_control/`
  for `account_values.csv`, `product_values.csv`, and `schedule.csv`.

## 10. Limitations

- This is a **single-entry** schedule: one harvest entry per block. It does not
  build multi-entry chains, and it does not run the WS4 optimizer, FHOPS, or a
  portal.
- The area-control seed is not a volume target and not a claim of feasibility.
  A target minimum above what the area quota can produce is a policy tension,
  not a reason to liquidate the land base.
- With `quota_scope: global`, per-AU `Q_AU` is diagnostic; the schedule uses one
  land-base quota, which is the documented TSA24/TSA29 reference construction.
  Use `quota_scope: per_au` when the instance must enforce an AU-level quota.
- With `eligibility: operable`, a block that misses its first window remains
  eligible later; this is what lets a shortfall be carried. Use
  `eligibility: earliest_only` for a strict first-window reading.
- Blocks whose earliest operable year is at or beyond `horizon_years` are not
  scheduled.
- The simulation is deterministic and uses the compiled curves as-is; it does
  not rebuild the FEMIC recipe.

## 11. Materialization

This directory lives in the `data` DataLad/git-annex submodule. Parquet files
and run outputs are annexed; YAML, CSV diagnostics, and JSON manifests are
small and can be tracked directly.

```bash
# From the parent repository.
git -C data annex enableremote arbutus-s3
datalad get data/derived/ws4/tsa{{TSA_NUMBER}}/dataset
datalad get data/derived/ws4/tsa{{TSA_NUMBER}}/runs/area_control
```

Publish changes with the data-repository runbook (`docs/data-repository-runbook.md`),
then verify with `git annex whereis` and
`git annex find --not --in arbutus-s3 data/derived/ws4/tsa{{TSA_NUMBER}}`.
