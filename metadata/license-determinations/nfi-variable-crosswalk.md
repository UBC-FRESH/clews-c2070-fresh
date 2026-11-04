# Variable Crosswalk: Gauthier Input → NFI Variable → SCANFI v2 Layer

**Purpose:** Maps each Gauthier et al. (2015) yield-curve input to its NFI source variable and corresponding SCANFI v2 pixel layer for direct calibration.

---

## Crosswalk Table

| # | Gauthier Input (Model Role) | NFI Variable (Exact Field Name) | SCANFI v2 Layer (Product Name) | SCANFI Layer ID / Band | Notes |
|---|------------------------------|----------------------------------|--------------------------------|------------------------|-------|
| **Tree-Level** |
| 1 | Species (species-specific coefficients) | `SPECIES` / `TREE_SPECIES` | **Species Composition** (fractional cover per species group) | `species_composition_[group]` | SCANFI provides 8 species groups; map NFI species codes to groups |
| 2 | DBH (cm) — primary size metric | `DBH` / `TREE_DBH` | **Basal Area** (m²/ha) + **Stem Density** (stems/ha) | `basal_area`, `stem_density` | DBH not directly mapped; derive from BA + density + allometry |
| 3 | Height (m) — H-DBH allometry, site index | `HEIGHT` / `TREE_HEIGHT` | **Canopy Height** (m) — dominant/top height | `canopy_height` | SCANFI height = Lorey's height; apply species-specific conversion |
| 4 | Age (years) — yield curve x-axis | `AGE` / `TREE_AGE` / `STAND_AGE` | **Stand Age** (years since disturbance) | `stand_age` | SCANFI age from disturbance history + regeneration delay |
| 5 | Status (live/dead) — mortality, ingrowth | `STATUS` / `TREE_STATUS` | **Forest/Non-forest Mask** + **Disturbance Year** | `forest_mask`, `disturbance_year` | Mortality inferred from disturbance + biomass loss |
| 6 | Crown Class — competition modifier | `CROWN_CLASS` / `TREE_CROWN_CLASS` | **Canopy Cover** (%) + **Canopy Height Model** | `canopy_cover`, `chm` | Crown class proxy: relative height within plot + cover |
| **Stand-Level** |
| 7 | Site Index (m@50yr) — productivity driver | `SITE_INDEX` / `SI` | **Site Index** (m@50yr) — interpolated from PSPs | `site_index` | Direct 1:1 match where SCANFI SI layer available |
| 8 | Ecosite / Ecological Site | `ECOSITE` / `ECO_SITE` | **Ecozone** + **Ecoregion** + **Land Cover** | `ecozone`, `ecoregion`, `land_cover` | SCANFI uses coarser ecological stratification |
| 9 | Soil Type / Texture | `SOIL_TYPE` / `SOIL_TEXTURE` | **Soil Organic Carbon** + **Soil Texture Class** | `soil_organic_carbon`, `soil_texture` | From Soil Landscapes of Canada (SLC) v3.2 integrated in SCANFI |
| 10 | Drainage Class | `DRAINAGE` / `DRAINAGE_CLASS` | **Drainage Class** (7-class) | `drainage_class` | From SLC/CSTC integrated in SCANFI |
| 11 | Slope (%) | `SLOPE` | **Slope** (%) — from DEM | `slope` | Derived from Canadian Digital Elevation Model (CDEM) |
| 12 | Aspect (°) | `ASPECT` | **Aspect** (°) — from DEM | `aspect` | Derived from CDEM |
| **Plot-Level** |
| 13 | Plot Coordinates (lat/long) | `LATITUDE`, `LONGITUDE` / `PLOT_LAT`, `PLOT_LONG` | **Pixel Centroid** (30m grid) | — | Direct spatial join: plot center → SCANFI pixel |
| 14 | Measurement Date | `MEAS_DATE` / `MEASUREMENT_DATE` | **Composite Date** (annual mosaic) | `composite_year` | Match plot year to nearest SCANFI annual composite |
| 15 | Plot Design — BAF / Radius | `BAF` / `PLOT_RADIUS` / `PLOT_DESIGN` | — | — | NFI-only; used for expansion factor calculation |
| 16 | Plot ID (unique, persistent) | `PLOT_ID` / `NFI_PLOT_ID` | **Plot ID** (join key) | — | Primary key for all linkages |
| **Remeasurement** |
| 17 | DBH Increment (cm/yr) | `DBH_INCREMENT` / `DBH_GROWTH` | **Annual Basal Area Increment** (m²/ha/yr) | `basal_area_increment` | Convert BA inc → DBH inc via stand density & allometry |
| 18 | Height Increment (m/yr) | `HEIGHT_INCREMENT` / `HT_GROWTH` | **Annual Height Increment** (m/yr) | `height_increment` | Direct match where available |
| 19 | Mortality Flag / Cause | `MORTALITY_FLAG` / `MORTALITY_CAUSE` | **Disturbance Type** + **Biomass Loss** | `disturbance_type`, `biomass_loss` | Mortality = disturbance (fire, harvest, insects) + biomass drop |
| 20 | Ingrowth Flag | `INGROWTH_FLAG` | **Stem Density Increase** + **Recruitment Layer** | `stem_density`, `recruitment` | Ingrowth = density increase below minimum DBH threshold |
| 21 | Previous DBH / Height | `PREV_DBH`, `PREV_HEIGHT` | **Prior-Year Layers** (t-1 composite) | All layers at t-1 | Access prior SCANFI annual composite for same pixel |

---

## SCANFI v2 Layer Inventory (Reference)

| Layer Category | SCANFI v2 Products (Annual, 30m, 1984–present) |
|----------------|------------------------------------------------|
| **Structure** | Canopy Height, Canopy Cover, Basal Area, Stem Density, Biomass (AGB, BGB, Total) |
| **Composition** | Species Composition (8 groups: Spruce, Pine, Fir, Poplar, Birch, Maple, Other Conifer, Other Broadleaf) |
| **Productivity** | Site Index, Net Primary Productivity (NPP), Gross Primary Productivity (GPP) |
| **Disturbance** | Disturbance Year, Disturbance Type (Fire, Harvest, Insect, Other), Recovery Trajectory |
| **Environment** | Ecozone, Ecoregion, Soil Texture, Drainage, Slope, Aspect, Climate Normals |
| **Derived** | Basal Area Increment, Height Increment, Biomass Increment, Mortality Probability |

> **Full layer catalog:** See SCANFI v2 metadata at https://doi.org/10.23687/07653869-f303-46c2-a04e-9ab479b73cbf

---

## Mapping Notes & Caveats

| Issue | Resolution |
|-------|------------|
| **Species granularity mismatch** | NFI: ~50+ species codes → SCANFI: 8 groups. Map via crosswalk table (provided separately). |
| **DBH not directly in SCANFI** | Compute: `DBH ≈ 2 * sqrt(BA / (π * density))` using species-specific form factor. |
| **Crown class proxy** | `Relative_Height = Tree_Height / Plot_Max_Height`; classify: Dominant (>0.9), Codominant (0.7–0.9), Intermediate (0.5–0.7), Suppressed (<0.5). |
| **Site Index reference age** | SCANFI SI = m@50yr (breast height age). Confirm NFI SI uses same base age; convert if needed. |
| **Temporal alignment** | SCANFI composites = nominal year (e.g., 2020 = growing season 2020). Match NFI measurement date to nearest composite year. |
| **Plot-to-pixel representativeness** | NFI plots = 400m² (BAF) or 0.04ha fixed radius; SCANFI pixel = 900m² (30m). Use plot expansion factor to compute per-hectare values for comparison. |
| **Missing SCANFI layers** | Some ecozones (Arctic, Taiga) have partial SCANFI coverage. Flag plots without SCANFI match. |

---

## Calibration Workflow (Summary)

```
NFI Plot (tree list + stand attrs + coords + dates)
       │
       ├─→ Expand to per-hectare (BAF / radius)
       │
       ├─→ Spatial join: Plot centroid → SCANFI pixel (30m)
       │
       ├─→ Temporal join: Meas. date → SCANFI composite year
       │
       ├─→ Extract SCANFI layers at pixel × year
       │
       └─→ Calibrate Gauthier coefficients:
           • Species-specific: a, b in H = a * DBH^b (using SCANFI H, NFI DBH)
           • Site index adjustment: SI_NFI = f(SI_SCANFI, ecozone, soil)
           • Growth increments: ΔDBH_NFI = f(ΔBA_SCANFI, density, species)
           • Mortality: Pmort_NFI = f(disturbance_type, biomass_loss_SCANFI)
```

---

## Deliverables for Calibration Team

1. **This crosswalk table** (machine-readable CSV)
2. **Species code mapping**: NFI species code → SCANFI species group
3. **Ecozone mapping**: NFI ecozone code → SCANFI ecozone ID
4. **Plot-to-pixel join key**: `PLOT_ID`, `LAT`, `LON`, `MEAS_YEAR` → `SCANFI_PIXEL_ID`, `COMPOSITE_YEAR`
5. **QA flags**: Plots with missing SCANFI match, temporal mismatch >2 years, expansion factor anomalies

---

*Crosswalk Version 1.0 — 2026-08-09 — For CLEWs-C2070 NFI Data Request*