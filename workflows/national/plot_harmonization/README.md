# National plot harmonization — FVS-CA calibration v0

This package turns the archived open ground-plot corpora into one
column-oriented calibration extract for `fresh-fvsca`:

- BC FAIB permanent sample plots (`faib_psp`);
- BC FAIB non-PSP ground samples (`faib_non_psp`);
- MAGPlot v1.2 (`magplot`) plus the WFS-only supplement (`magplot_wfs`);
- Québec PEP (`qc_pep`);
- Newfoundland and Labrador FAL permanent sample plots (`nl_fal`).

It does **not** run a calibration and the builder does **not** publish to S3;
release publication is a separate git-annex step. The output is the input to
the calibration workstream.

## Run

From the parent repository root, with the `data` submodule materialized:

```bash
python -m workflows.national.plot_harmonization.build \
  --out data/derived/fvsca_calibration/v0 \
  --sources faib,magplot,qc_pep,nl_fal
```

The project virtual environment is preferred:

```bash
.venv/bin/python -m workflows.national.plot_harmonization.build \
  --out data/derived/fvsca_calibration/v0 \
  --sources faib,magplot,qc_pep,nl_fal
```

The builder extracts `PEP.gpkg` from `data/raw/qc_pep/PEP_GPKG.zip` into a
temporary work directory if `--qc-gpkg` is not supplied. It reads MAGPlot
directly from the archived ZIP; `--magplot-dir` can point at an already
extracted copy of the same package for faster iteration.

Useful development flags:

```bash
--max-sites 200       # per source; deterministic first N sorted site IDs
--sample 200          # alias for --max-sites
--sources faib        # faib, magplot, qc_pep, nl_fal
--no-wfs              # omit the MAGPlot WFS supplement
--no-validate         # skip the hard checks (not recommended)
--work-dir DIR        # extraction cache
--qc-gpkg PATH        # pre-extracted PEP.gpkg
--magplot-dir DIR     # pre-extracted MAGPlot CSVs
```

Dependencies are deliberately light: Python 3.11+, `pandas`, and `numpy`
(installed with pandas). `sqlite3`, `gzip`, `zipfile`, and `json` are standard
library. `geopandas`/`pyarrow` are not required. The NL GeoJSON is read with
the standard `json`/`gzip` modules; the QC GeoPackage is read through
`sqlite3`, not GeoPandas. `pyproj` is optional and only used to transform the
WFS `Sites_Summary` points from EPSG:3978 to lon/lat.

## Output

`data/derived/fvsca_calibration/v0/` contains:

| File | Rows | Purpose |
|---|---:|---|
| `sites.csv` | 72,975 | one row per source site |
| `visits.csv` | 368,133 | one row per source measurement event |
| `trees.csv.gz` | 15,639,840 | one row per tree measurement |
| `intervals.csv.gz` | 7,228,291 | consecutive tree observations, 2–40 years |
| `species_crosswalk.csv` | 355 | source species code → coarse group |
| `manifest.json` | — | hashes, counts, build time, commit, thresholds |
| `QA.md` | — | reconciliation, drops, units, coverage, validation |
| `validation.json` | — | machine-readable hard-check results |

Counts above are from the corrected full 2026-11-15 rebuild; the manifest is
authoritative for a particular run. The previous v0 was invalidated by a QC
plot-area unit error (square metres labelled hectares) and lost MAGPlot
intervals; see `data/derived/fvsca_calibration/v0/REVIEW_FIXES.md`.

## Unified v0 schema

`workflows/national/plot_harmonization/schema.py` is the machine-readable
definition. The public columns are:

### `sites.csv`

`source, site_id, province, eco_zone, eco_subzone, eco_variant, site_index,
site_index_species, plot_design, plot_area_ha, baf, lat, lon, coord_accuracy,
first_year, last_year, n_visits, n_trees`

- `site_id` preserves the source identifier and is never joined across
  sources.
- `eco_zone`/`eco_subzone`/`eco_variant` are BC BEC for FAIB, Québec
  ecological region/type for PEP, `INTERPSITE` for NL, and null for MAGPlot
  (the open package has no ecological classification).
- `site_index` is only populated where the source provides it: FAIB PSP
  90.3% of visits and FAIB non-PSP 78.0%; QC, NL and MAGPlot are null.
- `plot_area_ha` and `baf` are source values or source-derived values; blank
  means the source does not supply a defensible number.

### `visits.csv`

`source, site_id, visit_id, year, n_trees, ba_m2ha, tph, qmd_cm,
top_height_m, volume_m3ha, site_index, age`

- The visit is the source's measurement event. FAIB uses `CLSTR_ID`; QC uses
  `id_pe_mes`; NL uses `PLOTKEY|REMEASUREMENT`; MAGPlot uses
  `plot_id|meas_num` because one site measurement can contain several nested
  plot types.
- `n_trees` counts retained tree records. `ba_m2ha` and `tph` use live tally
  trees with a positive expansion factor. A visit with live tally trees but no
  expansion factor gets null BA/TPH rather than a fabricated value.
- `qmd_cm = 100 * sqrt(BA/TPH * 4/pi)`.
- `volume_m3ha` is the sum of source per-hectare volumes where available; it
  is null where the source has no volume field (MAGPlot, QC, NL).

### `trees.csv.gz`

`source, site_id, visit_id, tree_id, species_code, species_group, dbh_cm,
height_m, status, crown_class, expansion_factor, damage_code, is_remeasured`

- `status` is always `live`, `dead`, or `missing`; missing is not dead.
- `species_group` is `conifer`, `deciduous`, or `unknown`.
- `expansion_factor` is the source's per-tree stems-per-hectare factor:
  FAIB `PHF_TREE * TREE_WT`, MAGPlot `stem_ha`, QC `tige_ha`, NL null (the
  source has no tree expansion field).
- `is_remeasured` is true when the tree appears at more than one visit.

### `intervals.csv.gz`

`source, site_id, tree_id, visit_id0, visit_id1, year0, year1, years,
dbh0_cm, dbh1_cm, status_end, dead, known, exposure_years`

An interval is retained only when:

- the tree was live at the start;
- its end status is known (`live` or `dead`, not missing);
- the two observations are on consecutive source visits;
- `2 <= years <= 40`.

`dead` and `known` are 0/1 indicators; `exposure_years = years * known`.
`visit_id0`/`visit_id1` are the start/end rows in `visits.csv`, so intervals
remain joinable when a site carries several MAGPlot nested plots in the same
year. `dbh1_cm` may be null when `status_end = dead` (downed or broken trees
cannot always be measured); `dbh0_cm` is always present.

## Source mapping decisions

### BC FAIB

- `visit_id = CLSTR_ID`; `tree_id = PLOT-TREE_NO`.
- `PHF_TREE` is the per-hectare expansion factor; `TREE_WT` is applied
  defensively.
- `LV_D`: `L` → live, `D` → dead, blank/other → missing.
- Site index and age come from `faib_compiled_spcsmries_siteage`: the maximum
  species-level SI/age within a visit.
- `V_BAF` is retained for variable-radius non-PSP plots; PSP is fixed-area so
  BAF is normally null.
- Five PSP tree rows and 308 non-PSP rows have blank `PLOT`/`TREE_NO` and are
  dropped.

### MAGPlot

- ZIP visits are `plot_id|meas_num`; WFS visits are
  `plot_id|year|month`.
- Tagged trees (`tree_num < 8,000,000`) are `plot_id|tree_num`; untracked
  8M/9M records append `meas_num` (ZIP) or `year` (WFS) because their number is
  reassigned.
- `tree_status`: `LS`/`L` → live, `DS` → dead, `LF` (live fallen) → live,
  `-1` → missing.
- `stem_ha` is the expansion factor. BC variable-radius VRI/SUP/VLT plots have
  `meas_plot_size = -9` and `stem_ha = -9`; BA/TPH are null rather than using a
  fabricated BAF.
- The WFS supplement is restricted to sites absent from the ZIP package. It
  adds 2,778 sites (2,048 SK, 691 PE, 39 NL) and 1,298,474 tree rows after
  deduplicating 10,699 repeated NL features. The 1,900,092 WFS feature count
  includes 590,919 features on NL sites already represented by the ZIP; using
  both would double-count them. WFS site coordinates are the EPSG:3978 WKT
  points transformed to lon/lat when `pyproj` is available; otherwise they are
  null and `coord_accuracy = missing`.

### Québec PEP

- `visit_id = id_pe_mes`; `tree_id = id_arbre`.
- `dhp` is millimetres → centimetres (`/10`).
- `tige_ha` is the stems-per-hectare expansion factor; `st_ha` is basal area
  per hectare. `plot_area_ha = 1 / median(tige_ha)` per visit (e.g.
  `tige_ha = 25` is a 0.04 ha plot).
- `etat` live codes: 10, 12, 30, 40, 42, 50, 52, 58; documented dead codes are
  dead; removed/intruder, sapling and missing codes are `missing`.
- `haut_arbre` is decimetres → metres (`/10`); `hauteurdef` is already metres.
- No numeric site index is stored; it is null.
- `eco_zone` is `classi_eco_pe.reg_eco`; `eco_subzone` is the visit-level
  `station_pe.type_eco` mode.

### Newfoundland and Labrador FAL

- `visit_id = PLOTKEY|REMEASUREMENT`; `tree_id = TREENUMBER`.
- Visit year comes from `remeasurement_crew_date.ORIGINAL_DATE`, not from the
  visit sequence number.
- `TREESTATUS`: 0/3/4/6/7/8 → live, 1/2/5 → dead, 9 → missing.
- Species are joined from the permanent-tree lists.
- `PLOTSIZE` is interpreted as square metres per the archived contents note;
  `plot_area_ha = PLOTSIZE / 10000`. The source has no per-tree expansion
  factor, so BA/TPH are null. `PLOTSIZE` has no coded domain and the
  mature/immature interpretation should be revisited before using NL BA/TPH.
- `TREEHEIGHT = 99` is treated as the documented sentinel and nulled.
- Three exact duplicate tree records are dropped; 168 tallies do not match a
  site-layer `PLOTKEY` and are dropped; two visits have no crew-date year.

## Validation

`validate.py` enforces:

- exact required columns and non-empty required tables;
- no duplicate site/visit/tree/interval keys;
- visit→site, tree→visit, interval→tree, and interval→visit referential
  integrity;
- DBH 0–500 cm; height 0–100 m or null; non-null years 1900–2027; interval
  2–40 years;
- interval arithmetic and status/flag consistency;
- site `n_visits`/`n_trees` and visit `n_trees` agreement;
- manifest counts; a missing `manifest.json` is fatal;
- the 0.5% source reconciliation rule: a larger count difference must be
  covered by the manifest's exact `explained_drops` row count, and null-only
  reasons such as `tree_dbh_out_of_range` never explain a row-count change;
- output SHA-256, byte size, and row counts against `manifest["outputs"]`;
- `species_crosswalk.csv` columns, non-emptiness, source coverage, and group
  domain.

Run the checks alone:

```bash
python -m workflows.national.plot_harmonization.validate \
  --out data/derived/fvsca_calibration/v0
```

## Reproducibility

`manifest.json` records:

- SHA-256 and byte size for every raw input file used;
- source-level input, selected, output, and dropped counts;
- build parameters and interval/DBH/height/year thresholds;
- the parent-repository commit and dirty flag at build time;
- SHA-256, byte size, and row count for every output.

The build is deterministic given the same inputs and flags except for
`built_utc` and the commit/dirty fields. Raw payloads are never modified.
