# National Representative-Stand Batch (Phase 1)

| Field | Value |
|---|---|
| Document | `planning/national-representative-stand-batch.md` |
| Date | 2026-11-17 |
| Status | Phase 1 pipeline test; not a national supply estimate |
| Scope | Ecoprovince × species cells with a valid national DBH distribution and height model |
| Inputs | `data/derived/national/assortments/national_fit/`, `national_coverage/coverage.csv`, `data/derived/fvsca_calibration/v0/`, `data/raw/national/rsyc/RSYC_models.zip` |
| Outputs | `data/derived/national/assortments/national_batch/`; `metadata/national-batch.csv` |

## Contract

1. One representative stand per eligible `ecoprovince × species` cell.
2. Stand volume scale is the RSYC volume at age 100 from the national fit.
   When absent, the observed cell plot volume is used and flagged.
3. Stand stem density is the expansion-weighted mean live TPH from the
   cell's plot visits.  Borrowed distribution cells (`pooled_parent`,
   `species_national`) use the donor level's species mean TPH.  Missing TPH is
   never imputed.
4. Area is a **1 ha placeholder** per cell (`area_basis=placeholder_equal_area`).
   Summed cell totals are a pipeline test, not a national supply estimate.
5. Each stand uses the fitted DBH distribution for shape, the fitted
   height-diameter model, and the existing dual volume + TPH scaling in
   `synthesize.py`.
6. Bucking uses the existing BC interior dimension sorts and Kozak taper.
   National species are dispatched to the published Kozak taper group through
   an explicit, flagged proxy table.  The BEC zone is an ecozone-level proxy;
   no national BEC classification is claimed.
7. A per-species/height-model buck-yield table over 2 cm DBH bins replaces
   per-tree bucking for the full stand.  Every cell validates the table against
   a 20-tree exact bucking sample.
8. Cell partials make the run resumable.  A timeboxed run reports exactly which
   cells ran and which remain.

## Explicit non-claims

- The equal-area sum is not a national timber-supply estimate.
- Taper and sort rules are BC interior proxies for non-BC species and
  ecoprovinces.
- Sorts are dimension-only; `grade_status=not_assigned`.
- Residue density is a proxy species default, not a national density product.
