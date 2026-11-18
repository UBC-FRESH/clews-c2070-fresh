# QC BFEC/Woodstock 5-Unit Sample Report

**Date:** 2026-11-18  
**Source index:** `https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/`  
**Parser:** ws3 `1.0.5` at `92f16a8` (patched from `ae9da72`)  
**Scope:** 5 BFEC management units with `Modeles_compresses/*.zip`, at least
two regions, including unit `03451`.

## 1. Parser fixes

Two blockers were fixed in the sibling `ws3` repository:

- **Latin-1/CP1252 model files.** Added `ws3.forest.read_model_text()` and an
  optional `encoding=` argument to the model readers (`import_outputs_section`,
  `import_landscape_section`, `import_areas_section`,
  `import_constants_section`, `import_yields_section`,
  `import_actions_section`, `import_transitions_section`,
  `import_schedule_section`). With no explicit encoding, files decode as
  UTF-8 (BOM stripped), then CP1252, then Latin-1. Every fallback is
  byte-preserving; an explicit `encoding=` remains strict and raises
  `UnicodeDecodeError`.
- **`_SUBTRACT` complex yields.** Added
  `DevelopmentType._resolver_subtract` and registered it as `SUBTRACT`
  alongside `SUM`, `MULTIPLY`, `DIVIDE`, etc. This fixes
  `ValueError: Problem compiling complex yield: yv_g_gautres,
  _SUBTRACT(YV_S,YV_G_gSEPM)`.

Focused tests added in `tests/test_forest.py`:

- CP1252 fallback on a landscape file containing accented text;
- strict failure when `encoding='utf-8'` is requested for CP1252 bytes;
- `_SUBTRACT` produces the expected age/volume curve.

Verification: `pytest tests/test_forest.py tests/test_core.py tests/test_common.py`
→ **14 passed**.

## 2. Units and batch results

Five of the smallest representative units were downloaded to
`/tmp/bfec_batch/downloads` (never added to a repository), size-checked against
the directory listing, and SHA-256 hashed. All five parsed with the patched
parser.

| Unit | Region | Zip bytes | Model | Runtime | Polygons | Inventory DTs | Yield tables | Curve rows | Inventory ha |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 03451 | R12 | 8,280,329 | `PC_4759_U03451_4FF_V10` | 7.0 s | 13,325 | 304 | 16 | 656 | 24,064.7 |
| 02651 | R04 | 36,193,271 | `PC_4514_U02651_3FF_v06` | 18.5 s | 39,390 | 3,069 | 20 | 820 | 221,555.2 |
| 03152 | R03 | 45,594,968 | `PC_4975_U03152_4FF_V09` | 26.2 s | 76,958 | 9,325 | 57 | 2,337 | 169,386.9 |
| 03551 | R12 | 33,271,997 | `PC_5075_U03551_4FF_FINAL` | 29.6 s | 45,105 | 4,280 | 71 | 2,911 | 114,187.6 |
| 03151 | R03 | 49,337,618 | `PC_4976_U03151_4FF_V09` | 41.5 s | 85,416 | 15,096 | 72 | 2,952 | 170,864.8 |
| **Total** | 3 regions | **172,678,183** | | **125.9 s** | **260,194** | **32,074** | **236** | **9,676** | **700,059.2** |

Batch wall time including model import, shapefile scan, curve evaluation and
CSV writing: **2 min 06 s**. Download time is additional.

`03451` has two compressed bundles; the 2022 revision
`03451_Diffusion2013-18_modif2022.zip` was used as the latest. Other selected
units had a single bundle.

## 3. Outputs

Written to `data/derived/national/qc_bfec_sample/`:

| File | Bytes | Description |
|---|---:|---|
| `qc_bfec_curves.csv` | 526,360 | 9,676 table-level curve rows; `development_type='*'`, `species_group='ALL'` |
| `qc_bfec_development_types.csv` | 8,969,471 | 32,074 DT mappings with theme codes, polygon count/area, stratum labels, table id |
| `qc_bfec_inventory_crosswalk.csv` | 41,357,607 | 260,194 polygons, THEME1..20, STRATE, area, CRS; no geometry |
| `qc_bfec_sample_manifest.json` | — | URLs, SHA-256, counts, runtimes, validation and failure flags |

Schema notes:

- Curves are normalized by `table_id` because most development types share a
  yield table (236 unique tables for 32,074 inventory DTs). The
  `qc_bfec_development_types.csv` `table_id` column provides the DT mapping.
- `volume_conifer` is `yV_G_Gr` (résineux group), `volume_total` is `yV_S`,
  `volume_deciduous` is `max(volume_total - volume_conifer, 0)`, and `height`
  is `yH_S`.
- Ages are sampled every 5 years from 0 to 200, matching the 5-year model
  period. Source curves are piecewise linear.

## 4. Validation

| Unit | Inventory ha | `.are` ha | Rel. diff | DT area coverage | Productive coverage | Zero-volume area | Monotonicity flags (max rise) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 03451 | 24,064.7 | 24,064.7 | 0.0 | 100% | 89.7% | 2,467.0 ha | 2 (2.4 m³/ha) |
| 02651 | 221,555.2 | 221,555.2 | 0.0 | 100% | 73.2% | 59,468.4 ha | 0 |
| 03152 | 169,386.9 | 169,386.9 | 0.0 | 100% | 92.3% | 13,092.3 ha | 1 (0.1 m³/ha) |
| 03551 | 114,187.6 | 114,187.6 | 0.0 | 100% | 91.6% | 9,613.1 ha | 4 (7.6 m³/ha) |
| 03151 | 170,864.8 | 170,864.8 | 0.0 | 100% | 91.3% | 14,773.4 ha | 16 (7.5 m³/ha) |

- **Area:** inventory `SUPERFICIE` sums match `.are` sums exactly for all five
  units (relative difference 0.0).
- **DT coverage:** 100% of inventory area maps to a model development type
  with a yield table.
- **Zero-volume curves:** all zero-volume area is non-forest `GS0007`
  (`Non_Forestier`) in 02651; other units contain small non-forest/`EAU`
  inclusions. These are legitimate, not silent parser failures. Every
  productive table has a positive `volume_total` somewhere on the age grid.
- **Curve integrity:** `volume_conifer + volume_deciduous - volume_total` is
  exactly 0 in all rows. No negative volume tables.
- **Age/volume monotonicity:** 23 source tables show small post-peak wiggles
  (maximum 7.6 m³/ha over 5 years). These are source-curve shape artifacts,
  not parser corruption; they are flagged per unit in the manifest.
- **Inventory CRS:** 03451 and 03152 are `EPSG:32187`; 03551 and 03151 are
  `EPSG:32187`; 02651 is `EPSG:32189`. All are projected NAD83 MTM metres, so
  `SUPERFICIE` hectares are directly usable.

## 5. Confidentiality and missing units

- 15 unit pages advertise a `Lisez_moi.pdf`/`Lisez_moi.txt`: 02661–02666,
  08462, 08562, 08663–08666, 08762–08764. **None of these were selected.**
- The five selected bundles and their unit pages contain no `Lisez_moi` or
  confidentiality notice. Raw bundles remain only under `/tmp/bfec_batch`.
- Three additional small units were attempted and **excluded with exact
  errors** (not fabricated):
  - `05151` (R05): `ValueError: could not convert string to float:
    #COUT_CNOUVAAA` in a tabular yield row.
  - `06151` (R15): same `#COUT_CNOUVAAA` error.
  - `09551` (R09, latest file named `09951_Diffusion2013-18_modif2022.zip`):
    `KeyError: 'fc0017'` while expanding a yield mask.
  - `02651` also required a tolerant constants fallback because its `.con`
    file contains text-valued constants such as `partiel_avec_economique yV_S`;
    this is recorded as a warning in the manifest. Target volume curves are
    tabular and unaffected.

## 6. Commits and publish status

- **ws3:** commit `92f16a8` on `dev` — “Support Latin-1 model files and
  _SUBTRACT yields”. Working tree clean.
- **CLEWS parent:** commit records `scripts/qc_bfec_sample_batch.py` and this
  report.
- **CLEWS data:** commit records the four derived artifacts under
  `data/derived/national/qc_bfec_sample/`.
- Raw BFEC zip bundles are **not** committed; only URL, byte size and SHA-256
  are recorded in `qc_bfec_sample_manifest.json`.
- Both CLEWS branches are pushed. `git status --porcelain` is reported
  separately after the push; any unexpected `D` would have halted the session
  under the fail-closed collateral rule. No `D` was present.

## 7. Scale-up plan for all 52 units

**Inventory size.** The 52 units with `Modeles_compresses` total **9.30 GB**
compressed. Using the observed 2.2× expansion, expect ~21 GB extracted.
The five sampled units produced ~49 MB of derived text for 700,059 ha and
260,194 polygons; a full 52-unit run is likely **0.4–0.7 GB** of CSVs
(dominated by the polygon crosswalk), with curves and DT tables remaining
small.

**Runtime.** The 5-unit run averaged ~25 s/unit sequential, but the sample
excluded the largest bundles. Budget 40–90 s/unit for import and polygon
scanning. Sequential estimate: 35–80 min. With 6 parse workers and 4 download
streams: **15–30 min wall**, plus 5–10 min for 9.3 GB of downloads.

**Storage/workers.**

- Scratch: 30 GB under `/tmp/bfec_batch` or an S3/DataLad staging prefix.
- 6–8 CPU workers, 16 GB RAM, 4 parallel downloads.
- Keep zips and extracted bundles out of git; commit only CSVs, manifest and
  report. Put the crosswalk in the DataLad `data` repository if it approaches
  or exceeds ~100 MB.
- Run per-unit transactions: write a unit manifest immediately, so a failed
  unit can be skipped without losing completed work.

**Parser hardening before full run.**

1. Tolerate non-numeric constants in `.con` (text-valued entries are common in
   newer BFEC bundles).
2. Accept `#CONST` references in tabular yield cells instead of forcing
   `float()`.
3. Treat missing theme mask codes (e.g. `fc0017`) as an explicit skip/flag
   rather than a `KeyError`.
4. Record every skipped unit with its exact error in the batch manifest.

With those three hardening items, the three attempted-but-skipped units
(`05151`, `06151`, `09551`) are the first candidates for a second pass; no
data should be inferred for them until they parse cleanly.
