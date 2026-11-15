# WS4 TSA instance factory

Status: standard for BC TSA-level WS4 instances under CLEWS-C2070.

One TSA gets one instance. The instance carries the same AU logic, growth
logic, account vocabulary, directory layout, and documentation across TSAs.
The factory deliberately stops at a small, auditable lane: a compiled WS4
dataset, an area-control schedule, one deterministic simulation, and a manifest.
It is not a scenario optimiser and it is not a replacement for the FEMIC build.

## Directory contract

```text
data/derived/ws4/tsa<N>/
  README.md                     # instance record (from templates/instance_README.md)
  dataset/                      # compiled WS4 tables + manifest.json
  config/
    instance.yaml               # TSA identity, dataset hash, FEMIC provenance
    area_control.yaml           # explicit AU / volume / treatment contract
    targets.yaml                # optional diagnostic targets
    run_profile.yaml            # FEMIC build provenance
  schedules/
    area_control.csv            # block_id,treatment,offset
    diagnostics.csv             # per AU/period A,R,Q,MAI,selection,shortfall
    summary.json                # A,R,Q, counts, hashes, warnings
  runs/
    area_control/               # ws4 simulate output
      account_values.csv
      product_values.csv
      report_accounts.csv
      report_targets.csv
  manifest.json                 # input/config/output hashes and row counts
```

## Pipeline

1. **FEMIC run.** Produce the TSA model-input bundle and the Patchworks
   ForestModel XML recipe from the FEMIC case. Record the bundle path, recipe
   path, and command in `config/run_profile.yaml`.
2. **Patchworks export.** Export the fragment table with AU, age, area, and
   managed flag. The current FEMIC mapping is `theme2 -> AU`, `age -> age`,
   `area -> AREA_HA`, and `theme1 == 1 -> IFM managed`.
3. **THLB / fragment clip.** Clip the inventory to the TSA and to the THLB.
   Unmanaged fragments stay in the dataset for growth accounting but are not
   harvestable.
4. **WS4 build and validate.** Compile the XML recipe plus fragments into the
   canonical parquet dataset and run `ws4 validate`.
5. **Area-control schedule.** Run
   `python -m workflows.ws4_instances.area_control` with the explicit
   `config/area_control.yaml` contract.
6. **Simulate and report.** Replay `schedules/area_control.csv` with
   `ws4 simulate`, then write the optional `ws4 report` diagnostic.
7. **Instance docs and manifest.** Fill the README from the run log and write
   `manifest.json` with input, config, and output hashes.

## Minimal lane

The lane is:

```text
ws4 build/validate -> area control -> ws4 simulate -> ws4 report
```

It does **not** run `ws4 optimize`, FHOPS, the portal, carbon, routes, or
spatial topology. Those are separate scenarios that can consume the same
compiled dataset and area-control seed. Keeping them out makes the instance
cheap to rebuild and easy to compare across TSAs.

The area-control seed is **not** a volume target. It answers “which area can be
harvested in each period under the model's own rotation age?” A target minimum
above that capacity is recorded as a tension, not solved by harvesting
under-age blocks or by liquidating period 1.

## Config contract

`config/area_control.yaml` is explicit because WS4 compiled datasets do not
carry canonical AU or volume keys.

| Key | Meaning | Default |
|---|---|---|
| `au_regex` | One capture group in `tracks.name`, e.g. `AU\(([^)]+)\)` | required |
| `volume_label` | Feature label carrying the managed volume curve | required |
| `treatment` | Harvest treatment, e.g. `CC` | required |
| `managed_token` | Literal token in a managed track name, e.g. `IFM(managed)` | required |
| `period_width` | Accounting period width in years | `10` |
| `horizon_years` | Planning/simulation horizon in years | `100` |
| `quota_factor` | Multiplier on `Q = A * P / R` | `1.0` |
| `min_age` | Optional policy floor on harvest age | `null` |
| `quota_scope` | `global` or `per_au` | `global` |
| `eligibility` | `operable` or `earliest_only` | `operable` |

The runner validates the regex, the feature label, the treatment, and every
managed block against the dataset. Unknown keys are rejected so a typo cannot
silently fall back to a default.

`quota_scope: global` reproduces the documented TSA24/TSA29 reference: one
land-base quota `Q = A * P / R`, oldest-first across all AUs. Per-AU `Q_AU` is
still reported diagnostically. `quota_scope: per_au` enforces `Q_AU` on each
analysis unit.

`eligibility: operable` keeps a block eligible after its earliest treatment
year, which is what allows an unfilled quota to be carried into later periods.
`eligibility: earliest_only` implements the strict first-window reading.

## Commands

```bash
# Scaffold a new instance from a compiled dataset.
python -m workflows.ws4_instances.make_instance \
  --tsa-number 24 \
  --name "Prince George" \
  --dataset /path/to/compiled \
  --out data/derived/ws4/tsa24

# Build the schedule.
python -m workflows.ws4_instances.area_control \
  --dataset data/derived/ws4/tsa24/dataset \
  --config data/derived/ws4/tsa24/config/area_control.yaml \
  --out data/derived/ws4/tsa24/schedules \
  --periods 10 \
  --schedule-name area_control.csv

# Replay it.
ws4 simulate \
  --dataset data/derived/ws4/tsa24/dataset \
  --schedule data/derived/ws4/tsa24/schedules/area_control.csv \
  --years 100 \
  --out data/derived/ws4/tsa24/runs/area_control

# Optional diagnostic report.
ws4 report \
  --run data/derived/ws4/tsa24/runs/area_control \
  --targets data/derived/ws4/tsa24/config/targets.yaml
```

## Tests

```bash
python -m pytest -q tests/test_area_control.py
```

The fixture writes a two-AU synthetic dataset with known MAI peaks at 80 and
120 years. It checks `A`, `R`, `Q`, `MAI(R)`, oldest-first order, quota stop,
shortfall carry, period windows, and the no-under-age rule.

## DataLad storage contract

Heavy payloads live in the `data` submodule, backed by the `arbutus-s3`
special remote. The instance follows the repository's existing split:

- **Annexed:** `dataset/*.parquet`, `runs/area_control/*.csv` (large),
  `runs/area_control/*.json` (when large).
- **Direct Git:** `README.md`, `config/*.yaml`, `schedules/*.csv` and
  `schedules/*.json`, `manifest.json`.

Materialize with:

```bash
git -C data annex enableremote arbutus-s3
datalad get data/derived/ws4/tsa24/dataset
datalad get data/derived/ws4/tsa24/runs/area_control
```

Publish with `~/bin/git-annex-s3 copy --to arbutus-s3 data/derived/ws4/tsa24`,
then verify `git annex whereis` and
`git annex find --not --in arbutus-s3 data/derived/ws4/tsa24` returns nothing.
Do not commit credentials or local S3 profiles.

## What this lane does not claim

- No multi-entry harvest chains.
- No optimization, even-flow solve, or target feasibility claim.
- No FEMIC rebuild in the pilot unless the run profile records one.
- No claim that the compiled recipe is the latest TSA inventory.
- No claim that a published AAC is an area-control quota.

Those are recorded as gaps in the instance README and manifest, not hidden in
the seed.
