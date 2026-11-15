# fresh-fvsca — multi-variant FVS-CA prototype

`fresh-fvsca` is **not** a new Fortran FVS variant. It is the orchestration and
data-adaptation layer around the FVS-CA variants that already exist:

1. a multi-variant orchestrator (`VariantProfile`);
2. a plot-source adapter layer (`PlotSource`);
3. tree-list imputation rules for source species the variant does not carry;
4. a harmonized volume/biomass post-processor for the consumer tables.

The current prototype implements the **BC** path end to end at the
calibration-table boundary. It does not execute FVS, fit a model, or emit a
regulatory volume or biomass estimate.

## Why a multi-variant package

FVS is not one model in Canada. British Columbia, Ontario, and the Acadian
region each have their own calibrated FVS variant, with different stand keys,
tree-list records, and species lists. Alberta, Saskatchewan, Manitoba, Yukon,
and the Northwest Territories have no calibrated FVS variant at all, so an
FVS-only national design would silently misrepresent those regions.

The package records that structure explicitly instead of aliasing every
province to the nearest FVS build.

## Contracts

### `VariantProfile`

`workflows.national.fvsca.profiles.VariantProfile` is the machine-readable
statement of what a variant needs and what it can carry. It declares:

- the FVS variant code and implementation status (`implemented` or `stub`);
- the site-key grammar (`bec`, `site_index_kodfor`, or `ecozone_site`) and the
  v0 columns that carry it;
- the tree-list record format;
- the species scheme, explicit species codes, donor aliases, and calibration
  groups;
- keyword-file and run-bundle defaults;
- the BEC zones the variant resolves and the province/territory coverage;
- explicit caveats for reviewers.

A profile is a contract, not an adapter. Passing a stub profile to the BC
adapter fails immediately:

```
BcAdapterError: variant profile 'on' is 'stub'; only the BC profile has an
adapter in this prototype
```

| Profile | Variant | Site key | Species | Status |
|---|---|---|---|---|
| `BC_PROFILE` | `FVSbc` | BEC zone + subzone + variant | 15 JSP codes + 7 donor aliases | implemented (calibration tables) |
| `ON_PROFILE` | `FVSon` | site index + KODFOR | 72 codes, not carried | stub |
| `ACD_PROFILE` | `FVSacd` | ecozone + site | 108 codes, not carried | stub |

The BC constants mirror the read-only `fresh-fvsbc` reference: twelve resolved
BEC zones (ESSF, ICH, IDF, MS, PP, SBS, SBPS, CWH, CDF, BWBS, CMA, MH), fifteen
JSP species codes (PW, LW, FD, BG, HW, CW, PL, SE, BL, PY, EP, AT, AC, OC, OH),
and seven donor aliases (SS→SE, SB→SE, BA→BG, HM→HW, YC→CW, DR→AT, MB→AC).

### `PlotSource`

`workflows.national.fvsca.plot_sources.PlotSource` is the protocol every plot
source implements. It returns the harmonized v0 column contract:

- `sites()` — one row per source site;
- `visits()` — one row per source measurement event;
- `trees(columns=None)` — one row per tree measurement, optionally column-
  projected;
- `intervals()` — one row per consecutive tree observation.

The first implementation is `HarmonizedV0Source`, which reads
`data/derived/fvsca_calibration/v0/` and filters by source label. The BC
consumer adapter is written against the protocol, so a raw FAIB, MAGPlot, or
provincial adapter can replace the v0 reader without changing the consumer.

## Pipeline

```
plot source -> domain filter -> consumer tables -> FVS run -> harmonized
volume/biomass -> validation
```

| Stage | Current implementation |
|---|---|
| Plot source | `HarmonizedV0Source` over the v0 extract |
| Domain filter | BC BEC zone filter; `--include-unsupported` keeps other zones |
| Consumer tables | `bc.build_bc_tables` / `bc.build_bc_dataset` |
| FVS run | **not in this prototype**; the consumer schema is the hand-off |
| Harmonized volume/biomass | volume is carried from the source visit aggregate; biomass is a declared downstream post-processor, not implemented here |
| Validation | schema, key, referential-integrity, and domain checks in the adapter and tests |

The BC adapter emits three consumer tables:

- `visits.csv` — one row per plot visit, with live BA, TPH, QMD, volume, site
  index, and age;
- `intervals.csv` — one row per consecutive plot-visit pair, with start stand
  density and dead basal area / mortality over the interval;
- `tree_survival.csv.gz` — one row per live tree at the start of an interval,
  with end status, damage flag, and exposure.

It also writes `species_crosswalk_fvsbc.csv` and `manifest.json`. The manifest
records input and output SHA-256 hashes, row counts, build parameters, and
warnings.

## Current status

| Region | Calibrated FVS variant | Status |
|---|---|---|
| BC | `FVSbc` | Implemented to the calibration-table boundary |
| ON | `FVSon` | Profile stub; no plot adapter, species table, or keyword bundle |
| NB / NS / PE | `FVSacd` | Profile stub; no plot adapter, species table, or keyword bundle |
| AB | none | Needs a non-FVS backend (candidate: GYPSY), not implemented |
| SK | none | Needs a non-FVS backend (candidate: MGM or a meta-model), not implemented |
| MB | none | Needs a non-FVS backend (candidate: MGM or a meta-model), not implemented |
| YT | none | Needs a non-FVS meta-model or a documented BC transfer, not implemented |
| NT | none | Needs a non-FVS meta-model or a documented BC transfer, not implemented |

The BC adapter's deliberate differences from
`fresh_fvsbc.calibration.build_dataset` are measured by `parity.py` and
explained in `PARITY_REPORT.md`.

## CLI

From the parent repository root:

```bash
.venv/bin/python -m workflows.national.fvsca.build \
  --variant bc --source faib_psp \
  --v0-root data/derived/fvsca_calibration/v0 \
  --raw-root data/raw \
  --out data/derived/fvsca_calibration/bc_faib_fvsbc_schema
```

Options:

```
--variant {bc,on,acd}       required; only bc has an adapter
--source LABEL[,LABEL...]   v0 source labels; default faib_psp
--v0-root DIR               harmonized v0 extract
--raw-root DIR              raw data root, used only for TSA_DESC enrichment
--out DIR                   output dataset directory
--include-unsupported       keep sites outside the twelve resolved BEC zones
--min-dbh CM                lower DBH limit; default 0
--strict-species            fail if a calibration-row species code is unmapped
```

The builder exits non-zero on a source or adapter contract error and prints the
error to stderr. It never fabricates a value to make a build pass.

Run the parity comparison after a build:

```bash
.venv/bin/python -m workflows.national.fvsca.parity \
  --raw-root data/raw \
  --v0-root data/derived/fvsca_calibration/v0
```

It materializes a temporary shim directory under `/tmp` with the four
decompressed FAIB PSP CSVs, runs the read-only `fresh_fvsbc.calibration`
builder and this adapter, compares the tables, and rewrites
`PARITY_REPORT.md`. Set `FRESH_FVSBC_SRC` when the read-only `fresh-fvsbc`
checkout is not at the default WS4 path.

## Outputs

The build writes:

| File | Rows | Purpose |
|---|---:|---|
| `visits.csv` | per plot visit | live BA/TPH/QMD, volume, site index, age |
| `intervals.csv` | per consecutive plot-visit pair | start density, dead BA, mortality |
| `tree_survival.csv.gz` | per live start tree | end status, damage, exposure |
| `species_crosswalk_fvsbc.csv` | per observed source species | JSP resolution and counts |
| `manifest.json` | — | hashes, counts, parameters, warnings |

A multi-plot visit carries a null `volume` with
`volume_basis = cluster_multi_plot_not_apportioned`: the v0 visit volume is a
site-visit aggregate and cannot be split across plots without per-tree volume.

## Explicit non-goals

- This prototype does **not** execute FVS or any other growth-and-yield model.
- It does **not** fit, calibrate, or validate a model.
- It does **not** make regulatory, operational, or timber-supply claims.
- It does **not** invent a cross-source plot join or a species mapping outside
  the declared profile.
- It does **not** publish data; git-annex publication is a separate step.
