# BC VRI Sample Test Fixture

This directory holds a tiny stub of British Columbia's Vegetation Resources
Inventory (VRI) Forest Vegetation Composite — Rank 1 Layer polygons. The stub
is **only** intended for unit tests of `fresh_fibre.bc_vri_reader` and the BC
side of `fresh_fibre.bc_qc_mini_2prov.run_bc_supply`.

The full BC VRI archive from DataBC (VEG_COMP_LYR_R1_POLY feature class) is
3.9 GB and is housed in the `data` data-submodule under the heavy-payload
DataLad/git-annex storage. The stub here intentionally mirrors only three
diagnostic polygons:

| `POLY_ID` | Region            | Bec zone | Species (rank 1, pct) | Notes                        |
| --------- | ----------------- | -------- | --------------------- | ---------------------------- |
| 100001    | Coast             | CWH vm   | SAB 90 / FDC 10       | Coast second-growth sawlog   |
| 100002    | Interior (dry)    | IDF dk   | FDC 70 / PLI 30       | Interior dry-belt sawlog     |
| 100003    | Interior residue  | SBS mk   | AT 50 / BL 30 / SXS 20| Salvage / logging-residue    |

## Files

- `sample.csv` — UTF-8 CSV dump of the three polygons. The CASFRI-shape
  reader path (`read_bc_vri_csv`) accepts this directly when `fiona` is not
  installed in the test runtime.
- `sample.geojson` — the same three polygons as GeoJSON `Feature`s. Useful
  for geometry-based contract checks (centroid, area-ha, WKT) that need
  planar coordinates rather than the metric `SHAPE_AREA` fast-path.
- `vri_to_bc_mini_mapping.csv` — explicit join table from each stub
  polygon's `POLY_ID` to one FRESH `supply_offer_bc.csv` row. In production,
  this join would be a BSU/FMU yield-rule lookup (see
  `fresh_fibre.scanfi_supply.build_harvest_rows` for the analogous join
  pattern); the stub hardcodes a diagnostic mapping because the
  diagnostic-bounded BC mini network only carries these three supply
  nodes.
- `sample.gdb/` — directory placeholder so the absolute path
  `tests/fixtures/bc_vri_sample/sample.gdb` resolves. Tests should supply
  either `gdb_path=...` (using `fiona`, when present) or
  `csv_fallback_path=sample.csv` to the BC VRI reader. The `.gdb` archive
  is intentionally empty: this repo does not vendor any real Geodatabase.

## Boundaries

- The stub's three polygons emit exactly **3 rows** through
  `fresh_fibre.bc_vri_reader.process_bc_vri`. That is one row each (no FMU
  partitioning). For LP handoff, the thin-wrapper
  `run_bc_supply()` then reads `vri_to_bc_mini_mapping.csv` to collapse
  each polygon into one ws3-shape supply offer row → 3 ws3 rows.
- Species codes (`SAB`, `FDC`, `PLI`, `AT`, `BL`, `SXS`) are producer-
  native; downstream `fresh_fibre.species_canonical` performs the
  cross-jurisdiction canonicalization. This stub does **not** include
  empty `species_cd_*` placeholders — that case is covered by the
  `test_species_resolution_drops_empty_codes` contract test.
- Two of the three polygons have heavy null or absent quantitatives on
  `species_pct_4..6`, mirroring the BC VRI snapshot practice. The
  `safe_float` / `safe_int` helpers keep those columns `None` rather
  than crashing.
