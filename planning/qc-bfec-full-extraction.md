# QC BFEC/Woodstock Full Extraction

**Status:** active implementation note  
**Date:** 2026-11-18  
**Source index:** `https://diffusion.mffp.gouv.qc.ca/diffusion/fec/diffusion/`  
**Parser:** sibling `ws3` repository, `dev` branch  
**Governing roadmap item:** P1.6 QC BFEC/Woodstock extraction

## Scope

Extract normalized, non-spatial yield curves and development-type mappings for
every BFEC management unit that publishes a public Woodstock model bundle.
The index exposes 52 units with `Modeles_compresses/*.zip` plus two
notice-bearing units (`08462`, `08562`) whose model bundles sit at the unit
root. The 13 remaining units publish only confidential cartography and are not
downloaded.

## Data boundary

- Raw ZIP bundles and extracted model directories stay outside the repository
  in external scratch storage.
- Public derivatives contain no geometry: curves, development-type aggregates,
  manifest, and report.
- The per-polygon inventory crosswalk and all inventory-derived tables for
  confidentiality-notice units stay in a `restricted/` directory that is not
  committed or pushed.
- Notice-bearing units are flagged `notice_present=true`; public curves carry
  the source notice provenance.

## Parser hardening required

1. Tolerate text-valued constants and comma decimal separators in `.con`.
2. Resolve `#CONSTANT` references in tabular and row-based yield cells.
3. Treat undefined theme mask codes as explicit skips with per-unit warnings.
4. Skip unsupported `FOREACH` yield macro blocks with explicit warnings.
5. Record every unit-level parse anomaly in the extraction manifest.

## Deliverables

- `qc_bfec_curves.csv`
- `qc_bfec_development_types.csv`
- `qc_bfec_inventory_crosswalk.csv` (public units only)
- `restricted/qc_bfec_inventory_crosswalk.csv` (notice units only)
- `qc_bfec_manifest.json`
- `QC_BFEC_EXTRACTION_REPORT.md`

## Validation gates

- All expected public model units parsed, or an exact failure is recorded.
- Inventory `SUPERFICIE` sum agrees with the `.are` model area per unit.
- Every inventory development type maps to a model yield table.
- Curve split identity holds; no negative volumes.
- Zero-volume inventory types are explained as non-forest or flagged.
- Post-peak monotonicity violations are counted and characterized.
