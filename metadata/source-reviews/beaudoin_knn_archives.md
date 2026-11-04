# Source Review: Beaudoin et al. 2014/2017 kNN National Forest Attribute Archives

Review date: 2026-08-09.
Author: UBC FRESH / CLEWs-C2070 forestry data maintainers.
Status: candidate (proposed source-triage entry; not promoted until DataLad
acquisition evidence exists per the team's existing discipline).
Governance: cross-references readiness-gate impact in
`metadata/readiness-gates.yaml` (gates `species_composition` and
`residue_yield_policy`).

## Cross-references

- Companion inventory row: `metadata/source-inventory.csv` row
  `beaudoin_knn_attribute_archives_2014_2017`
- Companion SAForaH2 harvest script: `scripts/discovery/saforah2_csw_harvest.py`
  (this archive set surfaces in CSW keyword `kNN` with ~56 hits as of the
  2026-08-09 probe).
- Companion SCANFI source review: `metadata/source-reviews/scanfi_v2.md` —
  SCANFI v2 is the federally-derived *rasterized* descendant of the same
  kNN methodology developed in Beaudoin et al. 2014; this review covers the
  *underlying* pre-rasterization kNN archive set.
- Companion provincial FRIs source review:
  `metadata/source-reviews/provincial_forest_inventories.md` — the kNN
  approach is statistically discontinuity-free across provincial boundaries,
  complementary to (not a substitute for) provincial FRIs.

## 1. Document identity

| Field | Value |
|-------|-------|
| Dataset family | **Beaudoin et al. 2014/2017 kNN national forest attribute archives** — pre-rasterization, per-pixel kNN imputation deliverables from NRCan/Canadian Forest Service |
| Public landing page | `http://tree.pfc.forestry.ca/` (the SAFORAH 2.0 download index) |
| Mirror landing page | Open Canada mirror `ec9e2659-1c29-4ddb-87a2-6aced147a990` (kNN map products, OGL Canada); note the Open Canada mirror re-packages some layers at different counts than SAFORAH — see §6 |
| Release lineage | Beaudoin et al. 2014 *Canadian Journal of Forest Research* (kNN method paper); Beaudoin et al. 2017 *Canadian Journal of Forest Research* (species attribute updates); release lineage runs from 2010 pilot → 2014 national → 2017 species expansion → SAFORAH 2.0 site iteration through ~2024 with URL pattern unchanged since 2016-01-15 per Internet Archive Wayback Machine |
| Publisher | Natural Resources Canada — Canadian Forest Service (NRCan/CFS) |
| Point of contact | NCC_NFI@cfs.nrcan.gc.ca (open verification: see §7) |
| License | Open Government Licence - Canada (OGL Canada); cite Beaudoin et al. 2014 CJFR and Beaudoin et al. 2017 CJFR when used |
| Update cadence | Static release (2001 base year mosaic grouped by category) |
| Native format | GeoTIFF in `.tar` archives (per-archive) |

## 2. What the archive set covers

Per the SAFORAH 2.0 landing page snapshot of 2026-08-09, the archive family
breaks down into three groups: (a) the **kNN attribute mosaic** (9 archives),
(b) the **Forest Fragmentation** series (16 archives, 2010), and (c) the
**EOSD** Earth Observation for Sustainable Development series (16 NTS tile
`.tar` archives, 2006–2010, plus 25/100/250 m Canada mosaics).

### 2.a kNN attribute mosaic (9 archives)

| Archive | Layers | Size (approx.) | What it carries |
|---------|--------|----------------|-----------------|
| `kNN-Species.tar` | 109 | 2.4 GB | Per-species % composition (full 109-species layer set; the dominant in the middle of the stack covers the most common commercial genera) |
| `kNN-StructureBiomass.tar` | 6 | 2.6 GB | Total above-ground biomass + stem wood + stem bark + branches + foliage + (a) dead-tree biomass analogue |
| `kNN-Soils.tar` | 10 | 2.9 GB | Soil carbon, soil texture, and ancillary soil covariates used as kNN predictors |
| `kNN-LandCover.tar` | 4 | 621 MB | Land-cover classes intersecting forested land, at the kNN attribution grid |
| `kNN-SpeciesDominant.tar` | 9 | 1.3 GB | Single dominant species per pixel + 8 dominant-group companions (conifer / deciduous / mixed-class) |
| `kNN-SpeciesGroups.tar` | 18 | 2.0 GB | Species-group rollups (e.g. spruces, pines, poplars) — convenient for jurisdictional yield aggregation |
| `kNN-Genus.tar` | 34 | 1.6 GB | Genus-level rollups |
| `kNN-StructureStandVolume.tar` | 5 | 1.7 GB | Stand volume (m³/ha) totalled and component-decomposed |
| `kNN-EcozonesDomain.zip` | 1 | 1.6 MB | Ecozone domain mask (single layer) |

### 2.b Forest Fragmentation (16 archives)

Forest Fragmentation series delivered as `.fr-*.tar.gz` (file pattern matches
2010 vintage): 16 archives, used in disturbance and fragmentation analyses.
Decomposition by landscape unit, ecozone, or terrestrial eco-region needs
self-discovery against the standardized `.fr-*` prefix.

### 2.c EOSD Earth Observation for Sustainable Development

- EOSD NTS tile series: 16 `.tar` archives, by National Topographic System
  tile (province / territory grouped), 2006–2010 vintages. Tiled by NTS grid
  cell, not by eco-boundary.
- EOSD Canada mosaics: at three resolutions — 25 m, 100 m, 250 m. Wall-to-wall
  Land Cover Convention (LCC)-style classification backbone of the early
  2000s federal land-cover product line (precursor to NLCC; see
  `metadata/source-inventory.csv` row `nlcc_landcover_2005_250m`).

## 3. Schema overview

The kNN attribute mosaic is composed of compact GeoTIFFs in `.tar` archives,
one GeoTIFF per layer (table-style row count rather than single multi-band
file). Layer naming convention: layer order is alphabetical/archive-class
within each `.tar`.

- `kNN-Species` (109 layers): per-species percent composition; one band per
  tree species — *the middle of the archive carries the species most
  frequently coded as dominant*. Layers are essentially categorical percent
  maps expressed on a percent-by-pixel basis (not pixel counts). The 109
  layers cover the full NRCan/Canadian Forest Service species code set.
- `kNN-StructureBiomass` (6 layers): total above-ground biomass (AGB) plus
  stem wood / stem bark / branches / foliage components, plus a dead-tree
  biomass analogue layer. Units: tonnes per hectare.
- `kNN-Soils` (10 layers): soil carbon density and texture covariates that
  served as kNN predictors internally. Provided for downstream carbon and
  soil-productivity work, not for growing-stock estimation.
- `kNN-LandCover` (4 layers): land-cover class attribution aligned to the
  kNN grid (intersecting forested land at the same pixel as the species
  mosaic).
- `kNN-SpeciesDominant` (9 layers): single dominant species plus dominant
  group companions.
- `kNN-SpeciesGroups` (18 layers): species-group rollups matching the
  commercial-genus groups used in Gauthier et al. yield-context work.
- `kNN-Genus` (34 layers): genus-level rollups.
- `kNN-StructureStandVolume` (5 layers): stand-level volume (m³/ha).

> **Note (load-bearing):** the layer-schema inventory above is derived from
> the SAFORAH 2.0 public landing page metadata as observed 2026-08-09. A
> Tier-3 ingestion must re-verify layer count and band naming by reading
> each `.tar`'s internal manifest before downstream model binding.

## 4. Readiness-gate impact

Two readiness gates in `metadata/readiness-gates.yaml` benefit from this
archive set:

- **`species_composition`** (currently `in_progress`) — `kNN-Species.tar`
  resolves the national wall-to-wall per-species composition signal directly,
  *bypassing* the SCANFI v2 species crown-closure intermediate
  (which the team currently defers pending authorization because of its
  123 GB total size). Once acquired through DataLad, per-species supply
  allocation can be derived without a separate provincial-FRI harmonization
  cycle for this gate.
- **`residue_yield_policy`** (currently `blocked`) — `kNN-StructureBiomass.tar`
  carries total AGB plus stem/branch/foliage components and a dead-tree
  biomass analogue. Once acquired, merchantable-vs-residue splitting can be
  derived from a stem-wood-vs-total-AGB ratio per pixel, reducing the
  provincial-residue-survey dependency for first-pass calibration.

Both gates close the *data-side* constraint; gate status remains as currently
recorded (`in_progress` and `blocked` respectively) per the team's
discipline — gates are not flipped on source registration alone.

**Conceptual edge (statistical discontinuity):** the kNN approach developed
in Beaudoin et al. 2014 is *statistically discontinuity-free across
provincial boundaries* because the predictor covariates are not jurisdiction-
specific. This contrasts with provincial FRIs (CASFRI pipeline), which carry
methodological seams at provincial borders. The two are complementary, not
substitutes.

## 5. Acquisition strategy

Direct HTTP download from `http://tree.pfc.forestry.ca/`. Uniform filename
pattern across the archive set; no API; no metadata query — each archive
links from a static download index.

1. **DataLad intake** — pull archives via DataLad `add-url` into the
   `clews-c2070-fresh-data` data submodule under
   `data/external/nrcan_saforah/` (per the team's existing
   `data/manifests/` index pattern, see `metadata/source-reviews/scanfi_v2.md`
   §`Acquisition Reconciliation`). **Never** import the payload into the
   parent repo.
2. **Heavy payload special remote** — publish to DRAC Arbutus S3 (special
   remote name `arbutus-s3`); the kNN archive family exceeds 15 GB total
   and the family should be exposed to collaborators through the data
   submodule, not parent-repo sidecars.
3. **Provenance** — capture per-archive SHA-256, byte count, response
   metadata, and acquisition date; follow the `canlabs_v2` pattern (see
   `metadata/source-reviews/canlabs_v2.md` and the `data/manifests/`
   index convention).
4. **Tier-1 vs Tier-3** — Tier 1 candidate for `species_composition`
   (kNN-Species); Tier 3 for downstream provincial biomass harmonization
   (kNN-StructureBiomass paired with provincial FRI Scala-A volume
   estimates).

## 6. References

- Beaudoin, A., Bernier, P. Y., Guindon, L., Villemaire, P., Guo, X. T.,
  Stinson, G., ... & Hall, R. J. (2014). Mapping attributes of Canada's
  forests at moderate resolution through k NN and Landsat imagery.
  *Canadian Journal of Forest Research*, 44(8), 521–532.
- Beaudoin, A., Guindon, L., Villemaire, P., Guo, X. T., Stinson, G.,
  Bergeron, T., ... & Hall, R. J. (2017). National forest attributes
  maps at moderate resolution – Canada 2001. *Canadian Journal of Forest
  Research*, 47(11), 1483–1515.
- NRCan/Open Canada kNN map mirror: dataset
  `ec9e2659-1c29-4ddb-87a2-6aced147a990` (Open Government Licence - Canada).
  The Open Canada mirror re-packages a subset of the SAFORAH archives with
  different layer counts than the SAFORAH landing page — verify layer count
  against the SAFORAH-side count before binding any layer name to a model
  contract.
- Internet Archive Wayback Machine snapshots confirm the SAFORAH
  `/<archive>.tar` URL pattern unchanged since 2016-01-15 (snapshot
  2026-08-09).

## 7. Open verification items

- `beaudoin.team.missing.point_of_contact` — the SAFORAH 2.0 public landing
  page does not list a single point-of-contact email; the team's working
  assumption is NCC_NFI@cfs.nrcan.gc.ca based on the legacy NFI mail
  pattern, but this should be confirmed via a direct probe (a Tier-0
  email-check is in scope before any organizational contact).
- `beaudoin.license.redistribution_clause` — OGL Canada is permissive but
  the team has not opened a written counsel determination on the
  redistribution clause for downstream model outputs derived from these
  layers ("per-species crown closure" vs "per-species forest share" vs
  "per-species supply allocation"). Recommend license counsel review
  for production-tier outputs prior to public release.
- `beaudoin.layer_count_drift_check` — the SAFORAH-landed layer count for
  `kNN-Species.tar` (109 layers) should be verified against the Open Canada
  kNN mirror's layer count before lock-in. Drift, if any, would force a
  re-inventory.
- `beaudoin.eco-projection_unknown_at_intake` — the EcozonesDomain layer
  is named but its CRS / projection is not disclosed on the SAFORAH
  public landing page; verify before downstream spatial joins.
