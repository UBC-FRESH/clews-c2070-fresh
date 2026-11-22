# National Bucking and Commodity Supply Architecture

| Field | Value |
|---|---|
| Document | `planning/national-bucking-supply-architecture.md` |
| Date | 2026-11-15 |
| Status | Architecture note; no implementation or validation claim |
| Audience | PI and collaborating modellers |
| Scope | Canada-scale tree-list bucking and commodity supply from SCANFI/NFI; TSA24 as first validation |
| Companion documents | `planning/national-gy-data-landscape.md`; `workflows/national/fvsca/README.md`; `data/raw/national/bucking/REFERENCES.md` |

## Executive summary

- Canada-scale supply cannot be built from aggregate volume curves. CLEWS needs log grades, diameters, lengths, residue biomass, chip yield, moisture, costs, and revenues. That requires tree lists, taper, and bucking, not just m³/ha.
- RSYC is useful as a national stand-level benchmark and as a gap-filler where plot-based inventory is thin. It does not provide tree lists, diameters, heights, grades, or management response. It must never drive the bucking engine.
- The pieces exist—`nemora` (BC taper and bucking), `fhops` (tree-list contract), `fresh-tipsy` (BC recovery and residues), `femic` (BC grade and value)—but nothing connects them nationally.
- The proposed design is a five-contract pipeline: tree list, taper registry, grade rule set, `BuckedLog`, and `ResidueLedger`, with an evidence tier on every output.
- The first proof is TSA24, gated against VDYP7 volume, TIPSY/FANSIER grade and residue recovery, and RSYC stand AGB/volume. A pass requires all three comparisons, not the best one.
- The main risks are taper licensing, jurisdiction-specific defect rules, and moisture/contamination assumptions for whole-stem chipping. These are evidence problems, not coding problems.

## 1. Purpose

The national design uses SCANFI/NFI raster inventory. It has no BEC classification. Pixel attributes are species percentages, height, biomass, and crown closure (`planning/national-gy-data-landscape.md` §1; `workflows/national/fvsca/README.md`). The BC instance factory—VRI polygons plus BEC plus VDYP/TIPSY—remains a stepping stone and a validation environment. It is not the national design.

CLEWS consumes supply offers, not inventory totals. Two stands with the same aggregate volume can produce different sawlog, pulp, chip, and residue mixes, and therefore different costs and revenues. CLEWS also needs low-grade biomass from harvest residues and whole-stem chipping. Aggregate volume cannot distinguish those streams, and it cannot carry moisture or contamination. The required chain is tree list → taper → bucking → commodity-grade supply with cost and revenue.

This note defines that chain, the contracts between its stages, and the evidence needed before any national result is reported.

## 2. Tompalski RSYC

**Citation.** Tompalski, P., Hermosilla, T., Baral, S.K., Wulder, M.A., White, J.C. 2025/2026. National remote sensing-derived aboveground biomass yield curves for Canada. *Forestry* 99(2): cpaf067. https://doi.org/10.1093/forestry/cpaf067. 2024 boreal predecessor: Tompalski et al., https://doi.org/10.1016/j.foreco.2024.121894. RSYC R package v0.2.0 (CRAN, November 2026; model release `v20260923`), https://github.com/ptompalski/RSYC/releases/tag/v0.2.0.

**Data.** The catalogue contains 27,208 curves: 17,292 national and 9,916 regional; 13,646 aboveground biomass (AGB) and 13,562 volume. Curves cover 385 150-km tiles and four nested ecological levels—12 ecozones, 36 ecoprovinces, 138 ecoregions, and 672 ecodistricts.

**Method.** A 30 m Landsat annual time series for 1984–2021 supplies AGB, stand age, and dominant species. The fitted model is a four-parameter declining Chapman–Richards form, `y = b1 * exp(-b4 * Age) * (1 - exp(-b2 * Age))^b3`, with nonlinear mixed effects. It was validated against MAGPlot, not fitted to NFI or SCANFI directly.

**Limits.** Published tile-level validation is modest:

| Response | Tile R² | Tile bias | Tile RMSE |
|---|---:|---:|---:|
| AGB | 0.59–0.63 | −15.4% to −9.7% | 42.1%–45.1% |
| Volume | 0.57–0.61 | −8.0% to −1.0% | 48.1%–48.7% |

RSYC supplies no diameter distributions, tree lists, heights, management response, or grades. The 150-km tiles are ecologically arbitrary, and finer strata do not automatically improve accuracy; ecozone curves can have higher R² but larger negative bias. The package's operational age range is 1–150 years, despite a catalogue bound of 600.

**Role.** RSYC is a national sanity and bias check and a gap-filler for unmanaged or non-inventoried forest. Aggregate detailed curves to the same tiles or ecological strata and compare like with like. Never drive bucking from RSYC.

**Local archive.** `data/raw/national/rsyc/` contains the model catalogue, tile grid, ecological boundaries, and checksums (`CONTENTS.md`, `ACQUISITION.md`, `PROVENANCE.csv`).

## 3. Existing components

| Component | What exists | Limit for national bucking |
|---|---|---|
| `nemora` | Kozak 2004 taper and dynamic-programming bucker, BC BEC keyed; YAML sort/price; `grade_status=not_assigned`. National Ung–Guo–Fortin 2013 scaffold has empty coefficients (`nemora/src/nemora/assortment/taper.py`). | BC-only keying; no national taper set. |
| `fhops` | Tree-list contract with provenance, checksums, defect, `grade_hint`; `StemModel` seam. Planning MILP uses block volumes. https://github.com/UBC-FRESH/fhops | Reference stem model is a toy form-factor model; no operational taper or bucker. |
| `fresh-tipsy` | BC TIPSY product recovery: eight log grades and five residue components in ODT/ha; generic PriceBook and NPV. https://github.com/UBC-FRESH/fresh-tipsy | BC-only and per-hectare; not a tree-level bucking engine. |
| `femic`, `fresh-salvage` | BC grade banks, VLC price matrices, species-grade value accounts; salvage grade transition and decay. `/srv/shared-data/gep/jupyterhub16-projects/femic` | BC-only; no national grade rules. |
| `ws4` | Can carry grade-specific product curves. `data/derived/ws4/tsa24/README.md` | No tree records. |
| Open national tools | BuckR (LGPL-2.1; Dykstra DP), FSvolLib (CC0; log segmentation), biomasscan (MIT; Lambert/Ung AGB), CFSForestTools (LGPL-3; Schneider 2013 taper and MERIS/Petro grade matrices), FVS Canadian variants (public domain in the US; BC Kozak, Ontario Zakrzewski/Honer/R9 Clark). Registry: `data/raw/national/bucking/REFERENCES.md`. | Reference implementations; no integrated national chain. |

The gaps are specific:

- No national taper dispatcher keyed by jurisdiction, species, and domain.
- No national defect or grade rule set. Grade is not a taper output.
- No whole-stem chip or moisture ledger.
- No shared bucked-log schema across `nemora`, `fhops`, WS4, and CLEWS.
- No tree-list connection from WS4 or FHOPS into a bucking stage.

## 4. Proposed architecture

```
SCANFI/NFI raster or VRI
  -> site key (ecozone/climate/SI nationally; BEC only in BC)
  -> growth engine (FVS variants / VDYP / TIPSY / meta-model)
  -> tree list (FVS output or imputation)
  -> taper registry + grade rule set + price deck + bucking DP
  -> BuckedLog (tree_id, log_index, length, large/small-end DIB, volume,
                sort, grade, value, evidence)
  + ResidueLedger (residue volume, ODT, moisture, chip/hog yield,
                   contamination)
  -> product curves for WS4 / PlanningUnit for FHOPS / supply offers for CLEWS
  -> carbon (libCBM)
```

The bucking DP maximizes total log value over feasible cut positions, subject to the active `GradeRuleSet` and price deck; residue is the volume left after merchantable logs.

**Tree list.** Adopt the `fhops` contract (provenance, checksums, defect, `grade_hint`); add species, DBH, height, expansion factor, damage, and an `imputed` flag. Where only raster exists, impute a distribution constrained to SCANFI species shares, with RSYC volume used only as a total-volume check, and mark every stem.

**`BuckedLog`.** One row per log. Required fields: `tree_id`, `log_index`, `length`, `large_end_dib`, `small_end_dib`, `volume`, `sort`, `grade`, `value`, `evidence`, `source_id`, and `price_deck_id`. The price deck is versioned by market, region, and period.

**`GradeRuleSet`.** Jurisdiction, species group, sort definitions, length and diameter limits, defect deductions, quality thresholds, authority, and version. A rule set is a data product with an owner, not a code branch.

**`ResidueLedger`.** Per tree or stand: residue volume, ODT, moisture, chip/hog yield, contamination, harvest system, and evidence. It must reconcile to the `BuckedLog` volume balance.

**Taper registry.** Keyed by jurisdiction and species, with equation form, coefficients, DBH/height domain, authority, and evidence tier. The registry dispatches; it does not average incompatible curves.

**Evidence tiers.** Every row carries `measured_taper`, `published_table`, or `assumption`. No `BuckedLog` or `ResidueLedger` row is valid without one. The tier travels downstream into WS4, FHOPS, and CLEWS supply offers.

**Downstream handoffs.** WS4 receives grade-specific product curves by stratum. FHOPS receives `PlanningUnit` yields with tree-list provenance. CLEWS receives supply offers with commodity, volume, moisture, cost, revenue, and period. libCBM receives product and residue pools for carbon accounting.

## 5. Phased plan

| Phase | Work | Exit criterion |
|---|---|---|
| 0 | Schema and configuration: `BuckedLog`, `GradeRuleSet`, `ResidueLedger`, taper registry; adopt the `fhops` tree contract. | Schemas and fixtures validate on a TSA24 sample; no model run. |
| 1 | BC validation: TSA24 pilot; `nemora` Kozak taper, TIPSY interior/coast sorts, FEMIC VLM prices. Three-way gate: VDYP7 volume; TIPSY/FANSIER grade, value, and residues; RSYC stand AGB/volume at the relevant tiles and ecodistricts. | Pre-registered gate memo reports all three comparisons. Tolerances are fixed before the run and not tuned afterward. |
| 2 | National proof: one non-BC taper (Ung 2013 or Schneider 2013/Zakrzewski) and one non-BC grade rule set, using the same DP and schema. | End-to-end `BuckedLog` for one non-BC jurisdiction with evidence tiers on every row. |
| 3 | Product curves into WS4 and yields into FHOPS; replace the hard-coded 70/30 sawlog/pulp split. | WS4 and FHOPS consume versioned curves; the 70/30 assumption is removed from the supply path. |
| 4 | Residue, chip, and moisture ledger; whole-stem chipping. | `ResidueLedger` populated; CLEWS supply offers carry moisture and chip yield with evidence tiers. |

Phase 1 is a validation gate, not a demonstration. VDYP7 checks volume, TIPSY/FANSIER checks grade/value/residues, and RSYC checks stand AGB/volume. All three must agree; one is not enough.

## 6. Risks

- **Taper parameters.** Ung et al. (2013) and Li et al. (2012) are paywalled; Schneider 2013 is available through LGPL-3 code. Confirm licence terms before vendoring. Prefer published equations, an external service, or clean-room implementation with legal review.
- **Defect and grade rules.** Rules are jurisdiction-specific, not derivable from taper. FVS tree lists lack sweep, knot, and rot. A `GradeRuleSet` must state its authority and evidence tier; where evidence is absent, report a range or `not_assigned`, not a point grade.
- **Whole-stem chipping and moisture.** Moisture, contamination, and chip/hog yield can move biomass supply more than taper choice. Use measured values where available and scenario ranges otherwise. Do not publish a single-point residue offer from an assumption.
- **RSYC overreach.** RSYC validation is stand-level only. Do not calibrate bucking to 150-km curves or claim grade accuracy from an AGB fit.
- **TSA24 assumptions.** The recipe and THLB assumptions live in `data/derived/ws4/tsa24`; there is no FEMIC rebuild yet. Freeze and document them before Phase 1; treat a rebuild as a separate change.
- **Licensed inputs.** Price decks, TIPSY/FANSIER outputs, and provincial grade rules may be licensed. Keep licences, code, and redistributed data separate; record restrictions in the source registry.

## 7. Immediate next actions

1. **Archive RSYC (done, 2026-11-15).** Verify the three SHA-256 values in `data/raw/national/rsyc/PROVENANCE.csv` and record the model release. Owner: CLEWS data.
2. **Write the Phase 0 schemas.** Define `BuckedLog`, `GradeRuleSet`, `ResidueLedger`, and the taper registry; adopt the `fhops` tree-list contract. Owner: CLEWS modeller. Output: schema note plus validation fixtures.
3. **Implement the TSA24 bucking prototype.** Run `nemora` Kozak taper with TIPSY interior/coast sorts and FEMIC VLM prices; emit `BuckedLog` and a first `ResidueLedger`. Owner: BC pilot modeller.
4. **Run the three-way validation.** Compare VDYP7 volume, TIPSY/FANSIER grade/value/residues, and RSYC stand AGB/volume. Pre-register tolerances and publish the gate memo, including failures. Owner: PI with validation reviewer.
5. **Build the national taper adapter.** Add the first non-BC taper (Ung 2013 or Schneider 2013/Zakrzewski) and one non-BC grade rule set behind the same DP and schema. Owner: national modeller.

## 8. Pilot implementation status (2026-11-22)

- Implemented `workflows/national/bucking/national_bucking_pilot.py` and the
  supporting `schneider2013.py`, `pilot_schema.py`, taper registry
  (`rules/qc_schneider2013_taper.csv`), and pilot sort set
  (`rules/qc_pilot_sorts.csv`).
- Jurisdiction: Quebec. Taper: Schneider (2013) fixed-effect tree model from
  the CFSForestTools parameter tables (LGPL-3.0), eight species. No Ung/Li
  coefficients were used.
- Input: 24-tree synthetic Quebec species/DBH/height grid with explicit
  `synthetic_pilot_v0` provenance. It is not Quebec inventory.
- Outputs: `data/derived/national/bucking/national_bucking_pilot_v0/` with
  schema documentation, tree list, `BuckedLog`, `ResidueLedger`,
  reconciliation, manifest, and tests.
- Evidence: taper geometry is `published_table`; synthetic tree geometry,
  dimension sorts, prices, density, moisture, chip and hog yields are
  `assumption`. Row-level `evidence` is the weakest component.
- Scope: Phase 0/2 pilot only. No national aggregation, official grade
  assignment, or operational supply claim is made.
