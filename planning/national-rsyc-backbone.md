# National RSYC Backbone: Decision and Architecture Note

| Field | Value |
|---|---|
| Document | `planning/national-rsyc-backbone.md` |
| Date | 2026-11-18 |
| Status | Decision and architecture note; tile-disaggregation test and QC VRI schema/download complete (2026-11-18); no national supply result |
| Audience | PI, national modellers, CLEWS supply-chain maintainers |
| Scope | National yield-curve backbone for the C2070 forest/biomass model |
| Governing decision | User, 2026-11-18: RSYC is the default national backbone; BC/QC/MGM/VRI provincial datasets are calibration and validation only |
| Companion documents | `planning/national-bucking-supply-architecture.md`; `planning/national-representative-stand-batch.md`; `planning/national-gy-data-landscape.md`; `planning/qc-bfec-full-extraction.md` |

## 1. Decision and scope

**Decision.** RSYC is the default national yield-curve backbone for the C2070 forest/biomass model. Every national stand-level volume or AGB curve is selected from the RSYC catalogue under the rules in §2. The BC FAIB, QC BFEC/PEP/VRI, MGM, and remaining provincial datasets are calibration and validation evidence. They are not the primary curve set. The goal is one consistently compiled national model, not a stitch-up of heterogeneous sub-national yield libraries.

| Question | Decision |
|---|---|
| What is the primary national curve set? | RSYC, selected per §2 |
| What is the absolute control total? | The tile-level species curve for each tile × species; see §2.3 |
| What do ecodistrict curves supply? | Relative composition inside a tile, not an absolute level |
| What are provincial curves for? | Calibration and validation only |
| What supplies DBH/height distributions? | The national fit |
| What supplies buck-yield tables? | The national batch |
| Can a provincial curve fill an RSYC gap? | No |
| Can a national supply total be reported now? | No; see §6 and §7 |

**What changes.** The national model stops treating provincial yield libraries as candidate primary curves. The curve layer becomes a single RSYC compilation with one selection rule, explicit fallback, and tile-first control totals: tile-level species curves set the absolute level, ecodistrict species curves supply relative composition, and one scalar per tile × species reconciles the two. The 2026-11-18 tile-disaggregation test (§3.4) is the evidence for this ordering. This replaces the earlier ecodistrict-first default; ecodistrict curves now supply composition only. Provincial yield libraries move to a benchmark role with pre-declared holdouts. The national fit and batch remain the sole suppliers of DBH/height distributions and buck-yield tables.

**What does not change.** RSYC still supplies volume and AGB curves only. It supplies no tree lists, DBH distributions, taper, grades, TPH, or prices; those come from the national fit, the national batch, and jurisdiction-specific taper/grade rules. The current national batch remains a pipeline test, not a supply estimate. The model still does not claim site-index resolution; RSYC has no site-index variable, and `b1` is an implicit productivity scalar, not a measured site index.

Provincial plot records are not excluded from the national fit. BC FAIB and QC PEP tree measurements can inform the national DBH/height distributions; that is a shape input, not a provincial yield curve. The decision restricts the primary curve layer, not the plot evidence used to estimate stand shape.

**Why this stops the hodge-podge.** Provincial libraries differ in age reference, species aggregation, volume definition, management assumption, plot design, and spatial support. A BC VDYP curve, a QC Woodstock table, and an MGM aggregate cannot be averaged into one defensible national curve. Stitching them produces a curve with no single validation frame and no traceable evidence tier. One RSYC selection rule with tile control totals makes every national cell traceable to a published curve and a known evidence tier. It also makes the benchmark question clean: provincial data test the national curve; they do not become it.

## 2. RSYC backbone

### 2.1 What the catalogue provides

| Attribute | Value |
|---|---|
| Curves | 27,208: 13,646 AGB and 13,562 volume |
| Spatial coverage | 385 150-km tiles; 12 ecozones, 36 ecoprovinces, 138 ecoregions, 672 ecodistricts |
| Species | 32 scientific codes plus 3 model groups (`generic`, `coniferous`, `broadleaf`) |
| Equation | `y = b1 * exp(-b4*Age) * (1 - exp(-b2*Age))^b3` |
| Predictor | Age only; no site-index, productivity, height, density, or mixture variable |
| Operational range | 1–150 years; the catalogue bound of 600 is not an operating range |
| Published validation | 60 summary rows; median R2 0.57–0.80; median bias −31.4% to +9.3%; median RMSE 31.3%–53.3% |
| Not supplied | Tree lists, DBH distributions, taper, grades, TPH, prices |

Source: `data/raw/national/rsyc/CONTENTS.md`. The 385 tiles are the tiles with published models, not the full 649-tile source grid. Join ecological layers on `join_key` and models on `strata_id`; apply the one documented ecodistrict-438 spelling correction explicitly.

### 2.2 Curve selection: tile controls first

The target cell is `tile × species × response` for the absolute control total and `ecodistrict × species × response` for composition. Response is volume or AGB and species is one of the 35 RSYC values. The tile is the primary spatial anchor, not a last-resort fallback. The 2026-11-18 test (§3.4) found that species-area weighting of ecodistrict curves carries a median absolute bias of 6.1% and a p90 of 19.0% against the tile species curve; one scalar per tile × species removes the level error and leaves a median relative RMSE of 0.6% at ages 50 and 150. The selection rule follows that evidence.

**Pass 1: absolute control total.** Select one curve per `tile × species × response`.

1. Require `publish_ok = TRUE`, `fit_ok = TRUE`, and `converged = TRUE`.
2. Select the tile-level exact-species curve. It is the absolute control total and carries `rsyc_tile_control`.
3. If no exact-species tile curve exists, select the tile-level group curve (`coniferous` or `broadleaf`). Record `rsyc_group` and `species_resolution = group`.
4. If no tile curve exists at all, walk the ecological hierarchy for an exact-species curve: ecoregion → ecoprovince → ecozone. Record `rsyc_fallback`, `selected_level`, and `fallback_steps`. If none exists, use the coarsest available group curve (`rsyc_group`) or flag `control_total_missing`.
5. Do not use an ecodistrict curve as an absolute control. An ecodistrict curve is composition evidence, even when it is the finest exact-species curve available.
6. Select volume and AGB independently; do not mix levels silently between the two responses.
7. Do not extrapolate outside 1–150 years. Truncate or flag the affected ages and do not publish a curve beyond the range.

**Pass 2: relative composition.** Within a tile, select ecodistrict species curves for the relative pattern across ecodistricts. They carry `rsyc_ecodistrict_composition` and are always reconciled to the tile control under §2.3.

| Order | Layer | Role | Evidence tier |
|---:|---|---|---|
| 1 | Tile exact species | Absolute control total | `rsyc_tile_control` |
| 2 | Tile group (`coniferous` / `broadleaf`) | Control total when species is unresolved | `rsyc_group` |
| 3 | Ecoregion → ecoprovince → ecozone exact species | Control fallback when no tile curve exists | `rsyc_fallback` |
| 4 | Ecodistrict exact species | Relative composition within the tile | `rsyc_ecodistrict_composition` |
| 5 | Ecodistrict group | Composition fallback | `rsyc_group` |
| 6 | Explicit `other` pool | Unresolved species area; no fabricated species weight | `rsyc_fallback` |

The tile is a separate 150-km grid, not a parent ecological stratum. The grid is published for all 385 tiles, and tile-level curves are available within it, though not for every species. It is the absolute control-total layer and the first choice for the absolute species curve. The catalogue also carries `tile_region` curves; they are a regional tile-model stratum, not an ecological level, and are not used in the primary chain. They may be used for diagnostics only.

### 2.3 Tile control totals and reconciliation

The tile-level species curve is the absolute control total for that `tile × species` and response. It sets the level. The ecodistrict species curve sets the relative pattern within the tile. Never reverse the two.

Link the area and curve layers at the 990 m block: each block is assigned to one ecodistrict and one tile. Apply the ecodistrict composition pattern to the block area, reconcile the block total to its tile control, then aggregate to the reporting cell. This avoids double counting where an ecodistrict straddles tiles.

- Preferred control: the tile-level exact-species curve for the same response. If absent, the tile-level group curve. If absent, the ecoregion, ecoprovince, or ecozone exact-species curve. Record `control_total_source`.
- Apply one control-total scalar per control cell (`tile × species`, or `tile × group` when the control is a group) to the ecodistrict composition curves so the area-weighted total matches the tile control at age 100. The test range was 0.68–1.23 (p10 0.84, median 1.00, p90 1.08). Record `control_factor` and the age-wise residual.
- Scaling at age 100 is the reconciliation point because it removed the level error in the test: median relative RMSE fell to 0.6% (max 0.8%) at ages 50 and 150. An age-100 scalar is the default; do not fit an age-varying factor without new evidence.
- Do not tune individual species curves beyond the one scalar. The per-cell scalar preserves the within-species spatial pattern supplied by the ecodistricts while matching the tile control.
- If no control exists, flag `control_total_missing` and do not publish a tile total.
- The control total is a reconciliation constraint, not an observation. It does not upgrade a `rsyc_group` or `rsyc_fallback` curve to `rsyc_tile_control`.

### 2.4 Evidence tiers

| Tier | Definition | Interpretation |
|---|---|---|
| `rsyc_tile_control` | Exact species at the tile level; used as the absolute control total | Strongest spatial anchor; sets the level for the tile × species |
| `rsyc_ecodistrict_composition` | Exact species at the ecodistrict level; used only for relative composition inside a tile and reconciled to the tile control | Pattern evidence; not an absolute level claim and not a substitute for a control total |
| `rsyc_group` | No exact-species curve at the control level; `coniferous`, `broadleaf`, or `generic` group curve | Weakest species resolution; flag the cell and do not claim species-level yield |
| `rsyc_fallback` | Exact species at a coarser ecological level, or an explicit documented fallback including the `other` pool | Spatially or resolution borrowed; `selected_level` and `fallback_steps` record the distance |

The tier is per response and per cell. A stand-level claim uses the weaker of the volume and AGB tiers. A composition tier never becomes a control tier through reconciliation. The evidence tier is never inferred from the number of curves available or from the validation R2 of a different level.

## 3. Disaggregation

### 3.1 Species curves are alternatives, not components

RSYC publishes separate species-specific curves within a stratum. Each curve answers a stand-level question: what does a stand dominated by this species yield at age A in this stratum? It does not answer how much of a mixed stand's volume comes from that species. The curves are alternative stand-level curves, not additive components of one mixed stand. The test measures the error in the alternative: summing all species curves gives about 5.9× the tile generic curve at age 100 (range 2.8–7.4×).

- If a cell has both a species curve and a group curve, use the species curve. Do not add the group curve.
- If two species curves exist for the same cell, they are alternatives for stands of those species. Do not sum them on the same hectare.
- For a mixed stand, allocate area to species and area-weight the stand-level curves. The result is a landscape composition-weighted expectation, not a within-stand additive decomposition.
- Use the tile control total for the tile total; never derive it by summing species curves.
- If composition is unresolved, use the group curve and flag `species_resolution = group`.

### 3.2 Site specificity is ecological, not measured

The only spatial signal in RSYC is the published stratum. The catalogue has no site-index, productivity, height, density, or mixture column; age is the only predictor. `b1` varies between curves while `b2`, `b3`, and `b4` are constant per species. `b1` is therefore an implicit productivity scalar, not a measured site index. Variation among ecodistricts, ecoregions, ecoprovinces, ecozones, and tiles is the available site signal. Do not infer a site index from `b1`, from the asymptote, or from the ranking of curves.

A site-index modifier is a calibrated extension, not RSYC. It may be fitted only on calibration units, tested against BC, QC, and VRI holdouts, and accepted only if it improves holdout error and does not materially degrade another jurisdiction. Label it `site_modifier_calibrated`, report it separately, and never merge it into an `rsyc_*` evidence tier. If it does not improve holdout error, do not use it.

### 3.3 Area weighting and reconciliation

SCANFI v2 allocates forest area to species by normalising the ten species crown-closure layers within each 990 m block. That is an area share, not a volume share and not a within-stand mixture model. Coverage is partial: the species-area layers cover only 9 of the 27 tile species in the test and a median 14% of tile forest area (range 0.2–68%). They are a composition input, not a complete species disaggregation.

Stand-level volume or AGB is the area-weighted sum of the selected species curves:

```
y_stand(age) = sum_i(area_i * y_i(age)) / sum_i(area_i)
```

The result is reconciled to the tile × species control total under §2.3 with one scalar per tile × species. The scalar is recorded, not hidden. Do not force species-level area where SCANFI cannot resolve the species; do not borrow a provincial curve; do not use forest area as a species proxy.

| Case | Rule | Flag |
|---|---|---|
| Species area available | Use the SCANFI species area as the composition weight | `weight_source = species_area` |
| Species area absent, species resolvable by group | Use the group curve or group share; do not fabricate a species area | `weight_source = group`; `rsyc_group` |
| Species unresolved by SCANFI | Assign the block to an explicit `other` pool | `weight_source = other_pool`; `rsyc_fallback` |
| Any fallback | Every block must receive a weight; record the rate | `weight_fallback = true`; no uncovered area |
| Forest-area proxy | Not a valid species disaggregation; do not use as a species weight | reject |

Fallback was 24% overall (14% species-area, 33% forest-proxy), with no weight left uncovered. Coverage is complete; species validity is not. The forest-proxy fallback is recorded for coverage only and is never interpreted as a species share. Report the resolved species-area share with every cell; a low share limits the composition claim to the group or `other` pool.

| Rule | Statement |
|---|---|
| Species curve | Alternative stand-level curve, not a mixed-stand component |
| Area weight | SCANFI species area where available; group or explicit `other` pool otherwise |
| Control total | Tile × species at age 100; one scalar per tile × species; coarser control fallback |
| Site index | Not in RSYC; `b1` is an implicit productivity scalar; calibrated extension only |
| Missing species | Group curve or explicit `other` pool; no provincial fill, no forest-area proxy |

### 3.4 Tile disaggregation test (2026-11-18)

The test asks whether ecodistrict species curves can be disaggregated to a tile and reconciled to a tile control. It covers 16 tiles with published models: 4 BC, 4 QC, 3 ON, 3 AB, and 2 SK tiles, for 92 tile × species pairs and 17 species. It is a design test, not a national benchmark.

| Check | Result | Consequence |
|---|---|---|
| Coverage | 16 tiles; 92 tile × species pairs; 17 species; 4 BC, 4 QC, 3 ON, 3 AB, 2 SK | Provincial spread is adequate for a design test; it is not a national census |
| Species-area weighting | Median signed bias +0.3%; median absolute bias 6.1%; median relative RMSE 6.3% across ages 50/100/150; p90 19.0%; max 46.5%; 68% within ±10% and 91% within ±20% at age 100 | Composition weighting is usable but not an absolute control |
| Control-total scalar | 0.68–1.23 (p10 0.84, median 1.00, p90 1.08); age-100 scaling gives median relative RMSE 0.6% (max 0.8%) at ages 50/150 | One scalar per tile × species removes the level error; tile controls are the right anchor |
| Tile vs area-weighted ecodistrict `b1` | r = 0.96; Spearman 0.95; slope 1.01 | Ecodistrict curves carry the same productivity signal; they are safe for relative composition |
| Fallback coverage | 24% overall (14% species-area, 33% forest-proxy); no weight left uncovered | Fallback must be explicit and flagged; complete coverage is not species validity |
| SCANFI species-area coverage | 9 of 27 tile species; median 14% of tile forest area (range 0.2–68%) | Species-area layers cannot carry species disaggregation alone |
| Curve additivity | All species curves sum to about 5.9× the tile generic curve at age 100 (range 2.8–7.4×) | Species curves are alternatives; never sum them |
| RSYC predictors | No site-index, productivity, height, density, or mixture column; age only; `b1` varies and `b2/b3/b4` are constant per species | `b1` is an implicit productivity scalar; no site-index claim |

**Conclusion.** Species-area weighting alone leaves a long tail (p90 19.0%, max 46.5%). One scalar per tile × species against the tile control reduces relative RMSE at ages 50 and 150 to 0.6% (max 0.8%). The tile-level species curve is therefore the absolute control total; the ecodistrict curve supplies relative composition; the scalar reconciles the two. Where SCANFI cannot resolve species, use a group curve or an explicit `other` pool. The forest-area proxy is not a valid species disaggregation and is not used to force species-level area.

## 4. National chain

### 4.1 Chain and contracts

```
SCANFI v2 forest area (species × ecodistrict; group/other fallback)
  -> RSYC tile control totals (species × tile) and ecodistrict composition (§2)
  -> per-tile × species control scalar (§2.3)
  -> area-weighted volume/AGB by age
  -> national fit DBH distribution and height model
  -> representative-stand synthesis (300 trees; 2 cm DBH bins)
  -> buck-yield table
  -> residue ledger
  -> SUPBCW supply rollup
```

RSYC supplies the volume/AGB scale and age trajectory. The national fit supplies the DBH distribution and height–diameter model. The national batch supplies the buck-yield tables. The chain does not use RSYC for taper, grade, TPH, or price.

| Stage | Input | Output | Evidence and flag |
|---|---|---|---|
| Area | SCANFI v2; 422.5 Mha gross treed; 36 ecoprovinces; 5,800 ecodistrict units | Species area by ecodistrict; group/`other` fallback | `area_basis = scanfi`; `weight_source`; 159 zero-area cells; 767 unmapped-species cells; no THLB or operability |
| Curve | RSYC catalogue | Tile control total and ecodistrict composition per cell | `curve_evidence_tier`; `selected_level`; `control_total_source`; `control_factor`; `weight_source` |
| DBH/height | National fit; 6,545 support-level parameter rows (national through ecoregion); 1,252 direct fits | DBH distribution and height model | `direct_fit`, `pooled_fit`, `pooled_parent`, or `species_national`; 745/1,191 stands use `species_national` |
| Synthesis | Curve, distribution, TPH | Representative stand; 300 trees; 2 cm bins from 2–100 cm | 1,191 stands; 69 non-ok cells; TPH from plot visits or donor level |
| Bucking | Buck-yield tables; 488 tables | Product, sort, and residue volumes | BC taper proxies for national species; `grade_status = not_assigned` |
| Residue | Bucking output | Residue volume and ODT | Residue ledger; proxy density where measured density is absent |
| `SUPBCW` | All stages | Supply rollup | Weakest stage evidence governs the row; no national total until §6 G1 and G2 are decided |

Every row must carry `curve_evidence_tier`, `selected_level`, `selected_model_id`, `control_total_source`, `control_factor`, `weight_source`, `distribution_evidence`, `taper_proxy`, `grade_status`, `area_basis`, and `benchmark_status`. A `SUPBCW` row is only as strong as its weakest stage. Do not publish a row with a missing flag.

### 4.2 Current batch status

The national batch is a Phase 1 pipeline test: 1,191 stands, 488 buck-yield tables, `area_basis = placeholder_equal_area`, BC taper proxies, and 69 non-ok cells. Each representative stand is scaled to the RSYC volume at age 100; an observed-plot volume fallback is flagged. The curve library behind the batch has not been reconciled to tile-first control totals, so the batch cannot test the tile-first architecture. The batch is not a national supply estimate. Its equal-area sum must never be reported as a national total. The area-weighted rerun is the next gate (§7).

## 5. Calibration and validation

### 5.1 Benchmark datasets

| Dataset | What it provides | Benchmark role | Limits |
|---|---|---|---|
| BC FAIB PSP/non-PSP | Tree lists, DBH/height, volume; PSP includes site index | Primary independent benchmark: stand volume, DBH distribution, MAI, rotation | BC only; PSP and non-PSP protocols differ |
| QC BFEC official Woodstock curves | 52 units; about 13.5k yield tables (full-extraction count to be confirmed); conifer and deciduous aggregates | Curve-level benchmark | Aggregate volume; no DBH or TPH; no species-level detail |
| QC PEP | Plots with DBH/height; no volume or site index | Distribution and TPH benchmark | No volume or site-index target |
| QC VRI | Provincial four-layer base map plus one full-schema 21M tile; 60-tile volume/site-index assembly deferred (§5.3) | Composition, height, density, and age-class checks; tile-level volume and site-index calibration only | Provincial package has no volume or site index; one tile is not a provincial benchmark |
| MGM western boreal | Selected FMUs in AB, SK, MB, and BC; aggregate volume | Aggregate-volume benchmark | No YT or NT; no balsam fir or birch; no DBH or TPH |
| ON, NB, PE, NL, NS | ON has zero plots in the current national coverage audit; NB/PE/NL provide DBH/height only; NS has zero | Limited distribution and height checks | No volume benchmark, or no plots |

### 5.2 Metrics and protocol

Report bias, MAE, RMSE, relative RMSE, MAI, and rotation age at a fixed volume. Report the sample size, mean observed, mean predicted, and residual standard deviation. Compare at the benchmark's native support; aggregate the national curve to the benchmark unit rather than comparing a tree-level benchmark with a 150-km curve.

1. Declare holdout units before calibration: whole BC TSAs, QC BFEC units, and MGM FMUs. Do not fit and validate on the same unit.
2. Keep at least one holdout per jurisdiction and per major species group.
3. Use RSYC's published validation envelope as the reference, not as a target to beat: median R2 0.57–0.80, median bias −31.4% to +9.3%, median RMSE 31.3%–53.3% across 60 summary rows. Tile-level volume is R2 0.57–0.61, bias −8.0% to −1.0%, RMSE 48.1%–48.7%. Do not claim point accuracy better than this without independent evidence.
4. Apply a correction factor only where the bias is systematic (same sign in most holdout units) and material (larger than the benchmark's own uncertainty). Apply it at the coarsest level that removes the bias, report before and after, and reject it if it does not generalize to holdouts.
5. Never use a correction factor to hide a species-resolution gap. If the error comes from using a group curve, report the gap; do not calibrate it away.

A site modifier is a separate calibrated extension (§3.2). Test it on BC, QC, and VRI holdouts and use it only if it improves holdout error without materially degrading another jurisdiction. Report it as `site_modifier_calibrated`; it never changes an `rsyc_*` tier.

Provincial datasets never become the primary national curve. No concatenation, averaging, or per-province library is permitted. A provincial curve can inform a correction factor only under the rule above.

### 5.3 QC VRI acquisition result and volume decision (2026-11-18)

The QC VRI schema and calibration-set acquisition work is complete. This supersedes the earlier "schema and download in progress" status and closes the question of whether the province-wide VRI volume or site-index layers should be acquired.

| Result | Value |
|---|---|
| Provincial package | `CARTE_ECO_ORI_PROV_GPKG.zip`, 11,244,667,626 B; GPKG 33.2 GB; downloaded, extracted, and queried; SHA-256 verified |
| Provincial schema | Four layers only: `pee_ori_prov` 8,387,062; `meta_ori_prov` 8,387,062; `etage_ori_prov` 6,465,021; `essence_ori_prov` 19,113,947; 63,320,147.8 ha; EPSG:32198 |
| Missing from the provincial package | No `dendro_*` (volume), `productivite_*` (site index), `stations_for_*`, `biomasse_*`, `contraintes_*`, or `proprietes_bois_*` layers |
| Full-schema reference tile | 21M: 29 layers; 256,992 stands; 1,747,151.9 ha |
| Codebooks | 183 inventory rows; 132 PSP rows; 76,151 inventory domains; 2,836 PSP domains; 97-row species crosswalk; under `external/qc_vri/meta/` (external data root); working copy in `/tmp/qc_vri/` |

**Province-wide volume and site index are not available from the provincial package.** Supplying them requires the 60 per-1:250,000 `PRODUITS_IEQM_*_GPKG.zip` tiles: 40.03 GB zipped, roughly 120–140 GB uncompressed. The 16 agency packages (6.02 GB) cover only their agency perimeters; one probe (`A091`) confirmed the same 29-layer schema, but the set is not a provincial substitute.

**Decision.** Do not download the 60 tiles solely to fill a QC VRI volume or site-index layer. Assembling 60 tile compilations into a provincial VRI product is the heterogeneous sub-national stitching that the RSYC pivot rejects (§1, §8). The QC VRI acquisition stops at the declared calibration set:

- Calibration and validation: the 21M full-schema tile, the BFEC official curves from the 52-unit extraction (in progress), and the QC PEP plots.
- Composition checks only: the four-layer provincial package, for province-wide area, species, age-class, height, and density patterns.
- RSYC remains the national yield and AGB backbone. VRI, BFEC, and PEP remain calibration and validation evidence; none becomes a national curve.

**VRI semantics to carry into any VRI use.** `dendro_pee_tiges_ori.vmb_ha` is gross merchantable volume for live commercial stems with DBH ≥9.1 cm, bark included and before defect deductions; there is no net or total volume. `productivite_pee_ori.iqs_pot` is potential site index, dominant height in metres at base age 50, by species. `cl_age` is a 20-year age class, not a numeric stand age.

`etage_ori.hauteur` and `etage_ori.densite` are photo-interpreted storey height and cover; `essence_ori` supplies composition. `TOT` volume rows have `co_cmp = NULL`, so a `co_cmp IS NOT NULL` filter silently drops the stand total. `essence_ori` uses two-letter map codes; `dendro_*` and `productivite_*` use three-letter compilation codes. Join through the 97-row crosswalk, not by code equality.

## 6. Open gaps

| ID | Gap | Consequence | Decision needed |
|---|---|---|---|
| G1 | National AAC and harvest scenario; annualization | RSYC gives m3/ha by age, not a harvest level. Annual supply cannot be derived from RSYC alone | Adopt a national AAC/harvest scenario and an annualization rule; keep yield and harvest level separate |
| G2 | THLB and operability | SCANFI's 422.5 Mha is gross treed area with no THLB or operability filter; it is 1.145× the NFI Table 4.0 forest-land benchmark | Define the national land-base filter, or report gross and THLB separately; no national total until decided |
| G3 | Jurisdiction taper and grade | BC taper proxies are used for all national species; sorts are dimension-only and `grade_status = not_assigned` | Set jurisdiction taper and grade rule sets and their acquisition order |
| G4 | Species resolution | 32 species plus 3 groups; SCANFI has 10 species layers plus aggregates; broadleaf is an aggregate; white/red pine is split 50/50; 767 unmapped-species cells; species-area covers 9 of 27 tile species; 24% overall fallback | Approve the species crosswalk and group/`other` fallback; report the species-resolution tier; do not claim unsupported splits and do not use forest-area proxy as a species weight |
| G5 | RSYC age range and validation bias | Operational range 1–150 years; negative bias up to −31.4%; RMSE up to 53.3%; no site index; `b1` is an implicit productivity scalar | Set the truncation/flag rule beyond 150 years; decide whether jurisdiction correction factors are required; no site-index claim; a site modifier is a calibrated extension tested against BC/QC/VRI holdouts |

All five are decisions, not acquisition wishes. The model can proceed with explicit flags, but it cannot publish a national supply total until G1 and G2 are decided.

## 7. Implementation status

Status as of 2026-11-18. No result is reported for a pending or in-progress item.

| Work item | Status | Evidence | Claim allowed |
|---|---|---|---|
| Area-weighted batch smoke | Pending; blocked on tile-first curve reconciliation | `data/derived/national/assortments/national_area_weights/NATIONAL_AREA_REPORT.md` (2026-11-18); batch still `placeholder_equal_area` | Area weights only; no area-weighted supply result |
| Tile-first curve reconciliation | Pending | Existing curve library has not been reconciled to tile control totals; §2.2–§2.3 define the required controls | No tile-first curve set yet |
| RSYC benchmark | Pending | Protocol in §5; no benchmark report yet | No benchmark result yet |
| Tile-disaggregation test | Complete (2026-11-18) | §3.4; 16 tiles, 92 tile × species pairs, 17 species; one scalar per tile × species cuts median relative RMSE to 0.6% at ages 50/150 | Tile-first control architecture supported; no national supply result |
| QC/BFEC benchmark acquisition | In progress | 5-unit BFEC sample parsed (`data/derived/national/qc_bfec_sample/QC_BFEC_SAMPLE_REPORT.md`); full 52-unit extraction active (`planning/qc-bfec-full-extraction.md`) | Sample parse only; no full benchmark |
| QC VRI benchmark | Schema and download complete; 60-tile assembly deferred by decision (§5.3) | Provincial four-layer GPKG (63.32 Mha) verified; 21M full-schema tile (256,992 stands; 1.75 Mha); codebooks in `external/qc_vri/meta/` | Schema and acquisition only; no provincial volume or site-index benchmark |
| MGM join | Pending | All candidate rows carry `mgm_join_pending = true` | No result yet |

Foundation artifacts available as inputs: the national fit (6,545 parameter rows; 1,252 direct fits), the national batch (1,191 stands; 488 buck-yield tables), and the SCANFI area weights (422.5 Mha gross treed). The tile-disaggregation test (§3.4) is a completed design test, not a supply result. These are inputs, not supply results. None is a national supply estimate.

## 8. What we will not do

- No per-province primary curve libraries and no stitched national curve. Provincial curves are benchmarks, not the backbone.
- No ecodistrict-first absolute control. Tile-level species curves set the level; ecodistrict curves supply composition only.
- No summed species curves. Species curves are alternatives, not additive components; the sum is about 5.9× the tile generic curve at age 100.
- No forest-area proxy presented as a species disaggregation. Use a group curve or an explicit `other` pool and flag it.
- No waiting for new PSP access (NFI, Alberta, Ontario, Nova Scotia) to start. RSYC is the operating backbone now; new PSP data improve benchmarks later.
- No claim of site-index resolution from RSYC. The curve is age-only; `b1` is an implicit productivity scalar, not site index. Any site modifier is a calibrated extension with its own label, tested against BC/QC/VRI holdouts.
- No placeholder-area national totals. The 1 ha equal-area batch is a pipeline test, not a supply number.
- No RSYC-driven taper, grade, TPH, or price. Those come from the national fit, the national batch, and jurisdiction rules.
- No extrapolation beyond 150 years without an explicit flag and an alternative curve.
- No 60-tile QC VRI assembly to manufacture a provincial volume or site-index layer. The 21M tile, BFEC curves, and PEP plots are the QC calibration set; the provincial package is composition only.
- No provincial curve used to fill a missing RSYC cell.
- No calibration to RSYC as if it were observed truth. RSYC is the backbone; benchmarks are independent.

**Review trigger.** Revisit this decision after the tile-first curve reconciliation, the area-weighted batch smoke, and the RSYC benchmark, or when the G1/G2 decisions are made.

## Source documents

- `data/raw/national/rsyc/CONTENTS.md` — catalogue, spatial layers, validation envelope, operating range.
- `data/derived/national/assortments/national_batch/NATIONAL_BATCH_REPORT.md` — 1,191 stands, 488 tables, placeholder area, 69 non-ok cells.
- `data/derived/national/assortments/national_area_weights/NATIONAL_AREA_REPORT.md` — SCANFI v2 area, 422.5 Mha, 5,800 ecodistrict units, 159 zero-area and 767 unmapped-species cells.
- `data/derived/national/assortments/national_fit/NATIONAL_FIT_REPORT.md` — 6,545 parameter rows, 1,252 direct fits, evidence tiers.
- `data/derived/national/assortments/national_coverage/coverage_summary.json` — plot coverage by level and source.
- `data/derived/national/qc_bfec_sample/QC_BFEC_SAMPLE_REPORT.md` — five-unit BFEC sample parse.
- `planning/qc-bfec-full-extraction.md` — 52-unit BFEC extraction scope.
- `external/qc_vri/meta/qc_vri_report.md` (external, untracked) — QC VRI schema and acquisition result, provincial layer gap, 21M full-schema tile, codebooks, and 60-tile/agency-package sizing for §5.3.
- `planning/national-bucking-supply-architecture.md` — bucking contracts and RSYC limits.
- `planning/national-representative-stand-batch.md` — batch contract and non-claims.
- `planning/national-gy-data-landscape.md` — provincial plot and inventory landscape.
