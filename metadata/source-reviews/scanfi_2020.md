# Source Review: scanfi_2020

**Reviewed:** 2026-08-06
**Status:** ⚠️ REFERENCE_ONLY (superseded by SCANFI v2)
**Priority:** high

## Publisher & License
- **Publisher:** Natural Resources Canada, Canadian Forest Service
- **License:** Open Government Licence - Canada
- **Access mode:** Direct download (FTP)

## Source URL
- Open Canada: `https://open.canada.ca/data/en/dataset/18e6a919-53fd-41ce-b4e2-44a9707c52dc`
- DOI: `https://doi.org/10.23687/18e6a919-53fd-41ce-b4e2-44a9707c52dc`
- FTP root: `https://ftp.maps.canada.ca/pub/nrcan_rncan/Forests_Foret/SCANFI/v1/`

## Product Purpose
30m resolution national wall-to-wall forest attribute rasters for **2020 only**:
- Broad land cover type (NFI-compatible)
- Forest canopy height (m)
- Crown closure (%)
- Aboveground tree biomass (tonnes/ha)
- Tree species cover (%) for 11 species

## Coverage
- **Spatial:** Canada (non-Arctic landmass)
- **Temporal:** Single vintage — 2020
- **Resolution:** 30m raster
- **Projection:** NAD83 Canada Lambert Conformal Conic

## Comparison with SCANFI v2

| Aspect | SCANFI 2020 (v1) | SCANFI v2 |
|--------|------------------|-----------|
| **Vintage** | 2020 only | 1985–2025 (5-year intervals) |
| **Status** | ❌ Superseded | ✅ Current canonical |
| **Layers** | Same families (biomass, height, closure, landcover, species) | Same + north extrapolation mask |
| **Methodology** | k-NN + random forest imputation | Updated k-NN + random forest |
| **License** | OGL Canada | OGL Canada |
| **Limitations** | Same 5 limitations apply | Same limitations + updated validation |

## Official Notice
The SCANFI 2020 dataset page explicitly states:
> **"Attention: there is a new version of this product (SCANFI v2)"**
> SCANFI v2 can be found here: https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf

## Decision: Reference Only
- **Do NOT download or mirror** SCANFI 2020 rasters
- **Do NOT use as canonical source** — SCANFI v2 supersedes it
- **Retain as reference** only if historical 2020 comparison is needed
- **Canonical source:** SCANFI v2 (already accepted in `required-datasets.yaml`)

## Limitations (same as v2)
1. Pest-disturbed areas not comprehensively represented in training set
2. Open stand classes (shrub, herbs, rock, bryoid) less reliable
3. Tree species cover predictions have relatively high uncertainty
4. Yukon exhibits notably lower R² values
5. Urban areas/roads classified as rock per 2020 AAFC land-use map

## Recommended Policy
- **Canonical:** SCANFI v2 (2025 vintage preferred for current modelling)
- **Deferred:** SCANFI 2020 — no action required unless historical comparison needed
- **Reference:** This review documents the supersession relationship

## Evidence
- Open Canada dataset page metadata (fetched 2026-08-06)
- SCANFI v2 dataset page confirms v2 as current version
- FTP directory structure: v1/ (legacy) vs v2/ (current)