# National bucking contracts and TSA24 pilot

This package is the Phase 0/1 implementation of the national tree-list bucking
pipeline.  It turns measured or imputed stems into dimension-based logs and a
residue ledger, and it validates the result against the first BC reference
case: TSA24, Prince George.

It is **not** a national taper library, a grade model, or an operational supply
model.  It is the contract and adapter layer that those later products must use.

## 1. Pipeline

```
FAIB PSP tree list (measured, live, latest visit)
  -> TreeRecord contract
  -> taper registry keyed by (jurisdiction, species, BEC zone)
  -> BC interior dimension rule set
  -> nemora buck_stem_sorted (dynamic program, objective = value)
  -> BuckedLog rows + ResidueLedger rows
  -> plot summary, benchmark joins, validation report
```

The reference bucking implementation is
`$NEMORA_ROOT/src/nemora/workflows/bucking.py`
(`buck_stem_sorted`).  The reference taper is
`$NEMORA_ROOT/src/nemora/workflows/taper.py`
(`params_for`, `kozak_total_volume`).  Set
`PYTHONPATH=$NEMORA_ROOT/src` or install `nemora` editable.

## 2. Contracts

All contracts live in `schema.py`.  They are frozen dataclasses with
construction-time validation.  Invalid rows fail immediately; there is no
"mostly valid" internal state.

| Contract | Purpose | Key fields |
|---|---|---|
| `TreeRecord` | One measured or imputed stem, following the `fhops` tree-list shape | `tree_id`, `site_id`/`block_id`, `species`, `dbh_cm`, `height_m`, `status`, `expansion_factor`, `bec_zone`, `age`, `provenance` |
| `BuckedLog` | One recovered log | `tree_id`, `log_index`, `length_m`, `large_end_dib_cm`, `small_end_dib_cm`, `volume_m3`, `sort_code`, `product`, `price_cad_m3`, `value_cad`, `evidence` |
| `ResidueLedger` | Residue and chip accounting for one stem | `residue_volume_m3`, `basic_density_odt_m3`, `residue_odt`, `moisture_wet_basis`, `green_tonnes`, `chip_yield_fraction`, `notes` |
| `GradeRule` | One dimension-based sort rule | `species_class`, `sort_code`, `product`, `min_top_cm`, `min_length_m`, `max_length_m`, `trim_m`, `priority`, `price_key` |

`TreeRecord` also carries optional BC pilot context: `plot_id`, `visit_id`,
`species_group`, `bec_subzone`, `site_index`, `stand_age`, observed
merchantable/whole-stem volume, latitude, and longitude.  These fields are
context, not alternate identities.

`ResidueLedger` also records the recovered log volume, taper total, the
reference bucker's residue, and the bucker balance error.  These make the
volume balance auditable without re-running the taper.

### Evidence tiers

Every output row carries one of:

| Tier | Meaning |
|---|---|
| `measured_taper` | Coefficients or measurements tied to the individual stem. |
| `published_table` | A published equation/table applied to a measured tree list. |
| `assumption` | A labelled planning assumption (density, moisture, chip yield). |

Phase 0 uses `published_table` for the Kozak taper and `assumption` for the
residue ledger.  No row carries `measured_taper`; the FAIB PSP data do not
include stem analysis.

## 3. Taper registry

`taper.py` defines:

- `TaperModel`: a structural protocol for `total_volume_m3` and
  `diameter_inside_bark_cm`.
- `NemoraKozakTaper`: the BC adapter around the Kozak (2004) Version 6
  parameter table.
- `TaperRegistry`: keyed by `(jurisdiction, species, BEC zone)`.

Unsupported jurisdictions, species, BEC zones, or missing parameter rows raise
`UnsupportedTaperError` with the supported alternatives.  The registry does not
average, interpolate, or silently substitute a curve.  Species proxies are
marked with `proxy=True` (for example SW -> S).

The default parameter table is
`nemora/config/kozak/kozak_parameters_v94.csv`; override it with
`--kozak-parameters` or `NEMORA_KOZAK_PARAMETERS`.

## 4. BC interior rule set

`rules/bc_interior.yaml` is adapted from
`nemora/config/log_sorts/tipsy_interior.yaml` (TIPSY 4.5 industrial interior
log dimensions).  It contains:

- conifer sawlog sorts from 38 cm / 13 m down to 12.5 cm / 5.1 m;
- one conifer pulpwood sort (12.5 cm top, 5.0 m minimum);
- deciduous `other` sorts from 30.5 cm down to 15.2 cm top, 2.4 m minimum.

The rules are **geometry/dimension-based, not official BC grade**.  Defect,
sweep, knot, rot, and product quality are not represented.  `max_length_m` and
`trim_m` are contract bounds; the Phase 0 reference bucker selects by minimum
length and does not apply a trim allowance.  `grade_status` is
`not_assigned`.

### Bucking profiles

`profiles.py` adds named, auditable profiles without changing nemora's default
`BuckingProfile(5.0, 7.5, 10.0)`. `--bucking-profile tipsy45` uses the
published TIPSY 4.5 nominal target lengths 13/11/8/6.3/5.1/5.0 m and the
documented 2.5 m deciduous base from `rules/tipsy45_profile.yaml`.
FAN$IER 4.5 Help does not publish numeric default log prices or sort-specific
relativities, so the profile's price scenario is labelled `assumption` and
keeps the published BC Interior Log Market Report product-class prices; no
sort-specific value is invented. The comparison of the default and tipsy45
TSA24 runs is written by `compare_sort_profiles.py`.

## 5. BC pilot scope

`tree_lists.py` adapts the archived FAIB PSP tables under
`data/raw/bc_faib_ground_plots/psp/`:

| FAIB column | Contract field |
|---|---|
| `SITE_IDENTIFIER` | `site_id` |
| `CLSTR_ID` | `plot_id` |
| `VISIT_NUMBER` | `visit_id` component |
| `TREE_NO` | `tree_id` component |
| `SPECIES` | `species` |
| `DBH` | `dbh_cm` |
| `HEIGHT` | `height_m` |
| `LV_D` | `status` (`L` -> live, `D` -> dead) |
| `PHF_TREE` | `expansion_factor` (per-hectare) |
| `VOL_MER` | `observed_merchantable_m3` |
| `VOL_WSV` | `observed_whole_stem_m3` |
| `AGE_TOT` | `age` (plot `PROJ_AGE_1` fallback) |
| `BEC_ZONE` | `bec_zone` (from `faib_header`) |
| `BEC_SBZ` | `bec_subzone` |
| `SI1`/`SI2`/`SI3` | `site_index` (plot maximum from the compiled site-age table) |
| `PROJ_AGE_1` | `stand_age` (latest visit) |

The pilot filters `TSA_DESC` containing `Prince George`, live trees only,
`DBH > 0`, `HEIGHT > 1.3`, and `PHF_TREE > 0`.  It uses the latest visit per
`CLSTR_ID` to avoid mixing remeasurements.

### Sample

`validate_tsa24.py` selects a documented stratified sample:

- up to 20 plots, one plot per site;
- allocation across BEC zone and supported leading species;
- up to 15 trees per plot, sampled across species and DBH quantiles;
- default seed `20261115`.

The selection is recorded in `sample_plots.csv`, `sample_strata.csv`, and
`manifest.json`.  The pilot never calls a subsample "the TSA".

### Residue assumptions

The pilot uses:

| Parameter | Value | Status |
|---|---:|---|
| Basic density | 0.45 ODT/m³ | default assumption; no species-specific BC table applied |
| Moisture, green | 45% wet basis | assumption |
| Moisture, seasoned | 30% wet basis | assumption |
| Chip yield | 0.90 of green residue tonnes | assumption |

Residue is `taper total - recovered logs`; it includes the stump-height
section, top, and unmerchantable sections.  It is not a delivered hog-fuel
estimate and carries no contamination or moisture segregation.

## 6. Outputs

`validate_tsa24.py` writes to
`data/derived/national/bucking/tsa24_pilot/`:

| File | Content |
|---|---|
| `bucked_logs.csv` | One row per recovered log |
| `residue_ledger.csv` | One row per bucked tree |
| `tree_summary.csv` | Per-tree volume, residue, and value audit |
| `plot_summary.csv` | Per-plot observed/bucked/residue/value summary |
| `sample_plots.csv`, `sample_strata.csv` | Selection audit |
| `sort_value_summary.csv` | Sort/code/product/market-group volume and value |
| `product_summary.csv`, `market_group_summary.csv` | Product and market rollups |
| `vdyp_benchmark.csv`, `tipsy_benchmark.csv`, `rsyc_benchmark.csv` | Matched benchmark rows, when the join exists |
| `grade_benchmark.csv` | Plot-level TSA24 TIPSY grade/residue comparison |
| `grade_benchmark_summary.json` | Machine-readable grade/residue benchmark summary |
| `rsyc_curves.csv` | RSYC volume curves at ages 20–140, when available |
| `validation_summary.json` | Machine-readable results and gaps |
| `VALIDATION_REPORT.md` | Human-readable report |
| `manifest.json` | Input/output hashes, config, provenance, sample counts |
| `pilot_row.json` | Row for `metadata/bucking-pilots.csv` |

The runner also upserts the row into `metadata/bucking-pilots.csv` unless
`--metadata-csv ""` is passed.

## 7. TSA24 grade and residue curves

The TSA24 FEMIC instance does not carry grade or residue columns.  Its TIPSY
4.5 `.taf` tables under the local TIPSY database do.  The compile step reads
those tables directly (no BTC runtime required), selects the untreated run
block closest to each AU's planted density, and writes:

```text
data/derived/national/bucking/tsa24_grade_curves/
  managed_au_curves.csv
  managed_au_industrial_sorts.csv
  species_grade_curves.csv
  au_grade_coverage.csv
  manifest.json
```

The compile path now consumes the refreshed TSA24 BTC `/TSR` output.  It reads
`data/tipsy_curves_tsa24.csv` (derived from `data/04_output-tsa24.csv`) and
copies the AU-specific `Logs_Grade_*` and `Residual_*` columns directly.  No
nominal site index, species surrogate, or grade-share rescale is applied.  The
residual-fibre columns are TIPSY/SYLVER mill-processing residues, not harvest
slash.

```bash
.venv/bin/python -m workflows.national.bucking.compile_tsa24_grade_curves \
  --femic-root "$FEMIC_TSA24_ROOT" \
  --out-dir data/derived/national/bucking/tsa24_grade_curves
```

The old `.taf` compile path remains in `tsa24_grade_curves.py` for historical
context, but the CLI no longer calls it and `--tipsy-database` is ignored.

`validate_tsa24.py` reads that directory when present and reports the
BTC grade-share proxy and residue semantic gap.  The industrial-sort share
comparison is marked unavailable when the industrial-logs bank was not
requested.  See `data/derived/national/bucking/tsa24_pilot/TSA24_GRADE_CURVES.md`.

## 8. Usage

```bash
PYTHONPATH=$NEMORA_ROOT/src \
  .venv/bin/python -m workflows.national.bucking.validate_tsa24 \
  --out-dir data/derived/national/bucking/tsa24_pilot \
  --max-plots 20 \
  --max-trees-per-plot 15 \
  --seed 20261115 \
  --grade-curves-dir data/derived/national/bucking/tsa24_grade_curves \
  --timebox-seconds 900
```

The runner fails closed if `git status --porcelain` reports an unexpected
deletion in either the parent repository or the `data` submodule.

Tests:

```bash
PYTHONPATH=$NEMORA_ROOT/src \
  .venv/bin/pytest -q tests/test_bucking.py
```

## 9. Non-goals

- No official BC log grade.  The rule set is dimension-only.
- No national taper set.  Phase 0 implements BC Kozak only.
- No national defect, sweep, knot, or rot model.
- No delivered chip, hog, or contamination model.
- No TSA-wide expansion.  The 300-tree sample is a pilot.
- No claim that the TSA24 BTC output validates grade or residue.  The
  grade/residue benchmark is model output and is reported with
  mill-vs-harvest and no-official-grade caveats.
- No RSYC-driven bucking.  RSYC is a stand-level sanity check at most.

## 10. National representative pilot v1

`national_bucking_pilot_v1.py` extends the pilot contract to all 13
jurisdictions.  It builds ten deterministic representative trees per province
from open NFI coverage composition and fitted DBH/height statistics, then
dispatches Quebec Schneider (2013), BC Kozak (2004), or a labelled
cross-jurisdiction donor taper.  Alberta, Ontario, Nova Scotia and Nunavut use
explicit documented composition assumptions because the open NFI coverage
table has no provincial rows for them.  Products use one national
dimension-only sort set; `grade=not_assigned`.  Scope is a representative
synthetic pilot, not inventory and not a national supply estimate.  Outputs are
written under
`data/derived/national/bucking/national_bucking_pilot_v1/`.

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v1 \
  --out-dir data/derived/national/bucking/national_bucking_pilot_v1
.venv/bin/pytest -q tests/test_national_bucking_pilot_v1.py
```

## 11. National pilot v2 — measured BC tree list

`national_bucking_pilot_v2.py` replaces the ten synthetic BC trees in pilot
v1 with a post-stratified aggregate of measured FAIB PSP TSA24 stems.  The
source is `bc_psp_validation_v0/selected_trees.csv` at the latest live visit
per site.  Trees that v0 could not buck (unsupported taper species or a
reference-bucker balance failure) are excluded and counted.

Within each species x BEC zone x 2.5 cm DBH stratum, one measured stem at the
PHF-weighted median is retained and assigned the stratum's summed `PHF_TREE`
expansion factor.  The selected list preserves the source expansion-factor
total exactly; DBH and height are the measured retained values, not synthetic
geometry.  Selection is audited in `bc_tree_list_selection.csv`.

BC is bucked twice: through the BC interior TIPSY 4.5 dimension sorts
(`bc_tipsy_*`) and through the national pilot dimension sort set inside the
13-jurisdiction pilot.  The exact TIPSY rule file used is copied into the
artifact as `bc_tipsy_sort_rules.yaml` and `bc_tipsy_sort_rules.csv`, so the
run is auditable even while the live rule file is revised.  The other twelve
jurisdictions keep the v1 synthetic
tree lists and local/donor taper dispatch.  `grade=not_assigned`,
`sort_basis=dimension_only`, `observed=false`, `policy_grade=false`,
`national_claim=false`.  The BC tree geometry is labelled
`measured_tree_list`; the other twelve are `representative_synthetic`.
Absolute volumes are not comparable between expansion-weighted BC and unit
weight synthetic provinces; shares and per-tree metrics are the comparison
basis.

```bash
.venv/bin/python -m workflows.national.bucking.national_bucking_pilot_v2 \
  --out-dir data/derived/national/bucking/national_bucking_pilot_v2
.venv/bin/pytest -q tests/test_national_bucking_pilot_v2.py
```

Outputs are under
`data/derived/national/bucking/national_bucking_pilot_v2/`.

## 12. Quebec pilot grade-rule rerun v1

`qc_grade_pilot.py` reruns the ten QC trees from the v1 tree list through two
sort sets: `rules/qc_pilot_sorts.csv` (dimension-only) and
`rules/qc_pilot_grade_sorts.csv` (pilot grade rules).  The grade set maps MERIS
quality classes DER, F1-F4, PATE and AUTRE onto top-diameter, target-length and
tree-defect thresholds and prices them with a MERIS current-deck proxy.  Every
grade is `pilot_<class>` with `grade_status=pilot_assumption`; it is **not** an
official Quebec grade, MERIS classification or Petro prediction.  Dimensions
carry `published_table` evidence; the quality mapping and prices carry
`assumption`.

The rerun writes `BuckedLog`, `ResidueLedger`, per-tree reconciliation, grade
distribution, product summary, scenario comparison, volume reconciliation,
`summary.json`, report and a checksummed manifest under
`data/derived/national/bucking/qc_grade_pilot_v1/`.

```bash
.venv/bin/python -m workflows.national.bucking.qc_grade_pilot \
  --out-dir data/derived/national/bucking/qc_grade_pilot_v1
.venv/bin/pytest -q tests/test_qc_grade_pilot.py
```
