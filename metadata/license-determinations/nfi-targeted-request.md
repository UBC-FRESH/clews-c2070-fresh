# NFI Data Request — Targeted Request for Gauthier et al. 2015 Yield Curve Calibration with SCANFI v2

**Request Type:** Research / Calibration (Non-commercial, federal DUA required)
**Submission Date:** 2026-08-09
**Project:** CLEWs-C2070 — Innovative Integration of Climate, Land, and Water into Energy Models for Canada's Net-Zero Commitments
**Funder:** Natural Resources Canada — Emerging Innovators Program (EIP-NES-147); contract held by Simon Fraser University; UBC FRESH Lab is a sub-grantee.
**PI for this NFI request:** Dr. Gregory Paradis, Assistant Professor, Forest Resources Management, UBC
**Project lead (non-correspondence):** Dr. Taco Niet, Simon Fraser University
**Contact:** [Email], [Phone]   *(leave as placeholder — the team will fill in)*

> **Change note (2026-08-09):** Project identification block rewritten to align with the NRCan 2025 FPP-2 proposal record. Earlier draft used an invented subtitle ("National Forest Carbon Accounting Enhancement") that does not appear in either the FPP-2 or the modelling workplan, and listed overall project lead and PI as ambiguous placeholders. See planning cross-reference at `eccc-caaf-2026/planning/clews-c2070-fpp2-summary.md`.

---

## 1. Purpose Statement

This request seeks access to **National Forest Inventory (NFI) plot data** for the explicit purpose of **calibrating and validating Gauthier et al. (2015) yield curves** parameterized by **SCANFI v2** satellite-derived forest attributes. The calibrated yield curves will support the WS3 forestry module of the energy–land–water integration model under the CLEWs-C2070 project (NRCan EIP-NES-147), specifically by enabling the species-specific yield coefficients used in scenario runs to 2070. **This request is for non-commercial research/calibration only**; aggregate calibration outputs may feed the open-source WS3 model under its existing MIT licence, but raw NFI plot data will not be redistributed.

> **Key linkage:** We require NFI plot measurements that correspond to **SCANFI v2 reference pixels** (geolocated plot centers) to enable direct pixel-to-plot calibration. SCANFI v2 DOI: https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf

---

## 2. Scope — Variables Required for Gauthier Yield Methodology

Only variables directly consumed by the Gauthier et al. (2015) yield-curve generation workflow are requested. No ancillary or administrative variables are included.

### 2.1 Tree-Level Variables (per tree measurement)

| Variable | Gauthier Role | NFI Variable Name (Reference) |
|----------|---------------|-------------------------------|
| Species | Species-specific yield coefficients | `SPECIES` / `TREE_SPECIES` |
| DBH (cm) | Primary size metric for volume/biomass | `DBH` / `TREE_DBH` |
| Height (m) | Height-DBH allometry, site index | `HEIGHT` / `TREE_HEIGHT` |
| Age (years) | Stand age for yield curve x-axis | `AGE` / `TREE_AGE` / `STAND_AGE` |
| Status | Live/dead for mortality & ingrowth | `STATUS` / `TREE_STATUS` |
| Crown Class | Competition, growth modifiers | `CROWN_CLASS` / `TREE_CROWN_CLASS` |

### 2.2 Stand-Level Variables (per plot/stand)

| Variable | Gauthier Role | NFI Variable Name (Reference) |
|----------|---------------|-------------------------------|
| Site Index (m@50yr) | Productivity driver for yield curves | `SITE_INDEX` / `SI` |
| Ecosite / Ecological Site | Stratification, species-suitability | `ECOSITE` / `ECO_SITE` |
| Soil Type / Texture | Water/nutrient availability | `SOIL_TYPE` / `SOIL_TEXTURE` |
| Drainage Class | Moisture regime modifier | `DRAINAGE` / `DRAINAGE_CLASS` |
| Slope (%) | Topographic modifier | `SLOPE` |
| Aspect (°) | Solar radiation modifier | `ASPECT` |

### 2.3 Plot-Level Variables (per measurement event)

| Variable | Gauthier Role | NFI Variable Name (Reference) |
|----------|---------------|-------------------------------|
| Plot Coordinates (lat/long, NAD83) | SCANFI pixel extraction, spatial join | `LATITUDE`, `LONGITUDE` / `PLOT_LAT`, `PLOT_LONG` |
| Measurement Date | Temporal alignment, growth period | `MEAS_DATE` / `MEASUREMENT_DATE` |
| Plot Design — BAF (m²/ha) or Radius (m) | Expansion factor, plot area | `BAF` / `PLOT_RADIUS` / `PLOT_DESIGN` |
| Plot ID (unique, persistent) | Linking remeasurements, SCANFI join | `PLOT_ID` / `NFI_PLOT_ID` |

### 2.4 Remeasurement Variables (per tree per cycle)

| Variable | Gauthier Role | NFI Variable Name (Reference) |
|----------|---------------|-------------------------------|
| DBH Increment (cm/yr) | Diameter growth validation | `DBH_INCREMENT` / `DBH_GROWTH` |
| Height Increment (m/yr) | Height growth validation | `HEIGHT_INCREMENT` / `HT_GROWTH` |
| Mortality Flag (Y/N, cause) | Mortality sub-model | `MORTALITY_FLAG` / `MORTALITY_CAUSE` |
| Ingrowth Flag (Y/N) | Ingrowth sub-model | `INGROWTH_FLAG` |
| Previous DBH / Height | Growth reconstruction | `PREV_DBH`, `PREV_HEIGHT` |

---

## 3. Spatial Scale & Stratification

### 3.1 Primary Stratification: Canadian Ecozones (15 zones)
All requested plots must be tagged with their **Canadian ecozone** (per Ecological Stratification Working Group 1995 / CCRN 2023):
1. Arctic Cordillera
2. Northern Arctic
3. Southern Arctic
4. Taiga Plains
5. Taiga Shield
6. Boreal Shield
7. Atlantic Maritime
8. Mixedwood Plains
9. Boreal Plains
10. Prairies
11. Taiga Cordillera
12. Boreal Cordillera
13. Pacific Maritime
14. Montane Cordillera
15. Hudson Plains

### 3.2 Secondary Stratification (where available): Management Units
- **Timber Supply Areas (TSA)** — British Columbia
- **Forest Management Units (FMU)** — Ontario, Quebec, Alberta, etc.
- **Forest Management Agreement Areas (FMA)** — Saskatchewan, Manitoba
- Other provincial/territorial management unit designations

**Delivery preference:** Plots delivered with both ecozone and management unit attributes populated where available.

---

## 4. Temporal Scope

| Priority | Description |
|----------|-------------|
| **Highest** | Most recent measurement cycle (all plots measured in latest cycle) |
| **High** | Plots with **≥2 remeasurement cycles** (enables growth increment calculation) |
| **Medium** | Plots with **longest continuous time series** (≥3 cycles, spanning ≥20 years) |
| **Standard** | All available cycles for requested plots |

**Note:** We require the **full remeasurement history** for any plot included, not just the latest cycle. This is essential for calculating observed growth increments to validate Gauthier-predicted increments.

---

## 5. SCANFI v2 Pixel Linkage — Critical Requirement

To enable direct calibration of Gauthier yield curves against SCANFI v2 attributes, we require:

1. **Geolocated plot centers** (latitude/longitude in NAD83 / WGS84) for all requested plots
2. **Measurement dates** for each plot visit to enable temporal matching with SCANFI acquisition dates
3. **Plot design parameters** (BAF or fixed-radius) to compute per-hectare expansion factors

With these, we will:
- Extract SCANFI v2 pixel values at each plot center (30m resolution)
- Match plot measurement date to nearest SCANFI composite date
- Calibrate Gauthier species-specific coefficients using SCANFI-derived predictors (height, canopy cover, biomass, species composition layers)

> **SCANFI v2 Reference:** Beaudoin et al. 2023. *SCANFI v2: Spatially Continuous Annual National Forest Inventory*. DOI: https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf

---

## 6. Geographic Coverage

**National extent** — all forested ecozones (1–15 above) where NFI plots exist and overlap with SCANFI v2 coverage.

**Exclusions:** Non-forested plots (tundra, grassland, urban, water) unless they serve as calibration anchors for treeline dynamics.

---

## 7. Data Delivery Format

| Format | Specification |
|--------|---------------|
| Primary | **CSV or Parquet** (one row per tree-measurement) |
| Spatial | **GeoPackage / Shapefile** with plot centroids (optional but preferred) |
| Metadata | **Data dictionary** mapping NFI variable codes to definitions |
| Encoding | UTF-8, ISO 8601 dates (YYYY-MM-DD) |
| Coordinate System | NAD83 (EPSG:4269) or WGS84 (EPSG:4326) — specify in metadata |

---

## 8. License & Use Restrictions

- **Use case:** Research / model calibration / validation only (non-commercial)
- **Publication:** Aggregated results only; no plot-level data disclosure
- **Sharing:** Derived yield curves and model coefficients may be shared; raw NFI plot data will not be redistributed
- **DUA:** Accept custom Data Use Agreement per jurisdiction (federal + provincial/territorial)
- **Retention:** Data destroyed upon project completion or per DUA terms, whichever is sooner

---

## 9. Estimated Volume

| Stratum | Approx. Plots (est.) | Notes |
|---------|---------------------|-------|
| Boreal Shield | ~2,500 | Highest plot density |
| Boreal Plains | ~1,800 | |
| Montane Cordillera | ~1,200 | |
| Pacific Maritime | ~900 | |
| Atlantic Maritime | ~800 | |
| Taiga Shield | ~600 | |
| Boreal Cordillera | ~500 | |
| Mixedwood Plains | ~400 | |
| Hudson Plains | ~300 | |
| Taiga Plains | ~250 | |
| Taiga Cordillera | ~150 | |
| Northern/Southern Arctic | ~50 | Treeline plots |
| **Total (est.)** | **~9,450 plots** | Across all remeasurement cycles |

---

## 10. Point of Contact for Clarifications

**Technical Lead:** [Name], [Title], [Email], [Phone]
**Data Manager:** [Name], [Title], [Email], [Phone]

---

## 11. References

1. Gauthier, S., Bernier, P., Burton, P.J., Edwards, J., Isaac, K., Isabel, N., ... & Nelson, E.A. (2015). *Climate change vulnerability and adaptation in the managed Canadian boreal forest*. Environmental Reviews, 23(4), 432–451.
2. Beaudoin, A., Bernier, P.Y., Guindon, L., Villemaire, P., Guo, X.J., Stinson, G., ... & Magnussen, S. (2023). *SCANFI v2: Spatially Continuous Annual National Forest Inventory*. Natural Resources Canada. DOI: https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf
3. Ecological Stratification Working Group (1995). *A National Ecological Framework for Canada*. Agriculture and Agri-Food Canada.

---

*End of Request — Ready for submission to NFI Data Request Form at https://nfi.nfis.org/en/datarequestform*

---

## Revision Log

- **2026-08-09 (this revision):** Project identification, funder reference, PI identification, scope framing corrected against NRCan 2025 FPP-2 and modelling workplan. Operational sections (§§2–11) unchanged. See `eccc-caaf-2026/planning/clews-c2070-fpp2-summary.md` for the NRCan proposal summary this fix was grounded against.