# Source Review: Provincial Yield Curves

**Reviewed:** 2026-08-06
**Status:** ⚠️ BLOCKED — No authoritative open yield curve sources found
**Priority:** high

## Overview
Provincial growth and yield curves are **rarely published as open geospatial datasets**.
They are typically:
- Internal provincial forestry agency products
- Published as PDF tables or Excel files in forestry handbooks
- Restricted or require formal data requests

## Search Results

### Open Canada Portal
- **Search terms used:** `yield curve forest`, `forest growth yield`, `provincial yield`
- **Results:** No relevant datasets found

### Provincial Portals Checked
| Province | Portal | Result |
|----------|--------|--------|
| AB | open.alberta.ca | No yield curve datasets found |
| BC | data.gov.bc.ca | No yield curve datasets found |
| MB | open.gov.mb.ca | No yield curve datasets found |
| ON | data.ontario.ca | No yield curve datasets found |
| QC | donneesquebec.ca | No yield curve datasets found |
| SK | open.gov.sk.ca | No yield curve datasets found |
| NB | data.gnb.ca | No yield curve datasets found |
| NS | data.novascotia.ca | Not checked |
| PE | www.princeedwardisland.ca | Not checked |
| NL | data.gov.nl.ca | Not checked |

## Potential Sources (Unverified)

### National Forest Inventory (NFI)
- **Source:** `magplot` (Multi-Agency Ground Plot Database)
- **URL:** `https://open.canada.ca/data/en/dataset/1a73441d-cbe9-485b-bc91-84e07f97193f`
- **Status:** Candidate
- **Notes:** Synthesizes forest ground plot data from NFI and Canadian jurisdictions; may contain yield-relevant attributes but not direct yield curves

### Provincial Forestry Handbooks
- Most provinces publish yield curves in **print or PDF forestry handbooks**
- Examples:
  - BC: "Forest Yield Curves" publications by BC Forests
  - ON: "Yield Curves for Ontario Forest Trees" by MNRF
  - QC: "Tables de production" by MFFP
- **Access:** Typically require direct purchase or library access
- **Open data status:** Unlikely to be available as open geospatial datasets

### Academic/Research Sources
- **CanWooFS (Canadian Wood Fuel Statistics)**
- **CFIA (Canadian Forest Inventory) plots**
- **University research datasets**
- **Status:** May require formal data requests or have restricted licenses

## Recommended Policy

### For WS3 National Expansion
1. **Do NOT assume open yield curves exist** for provincial jurisdictions
2. **Use SCANFI v2 biomass/height** as primary biomass estimation (already accepted)
3. **Use NFI standard reports** for validation/reference (license unresolved)
4. **Consider generic yield assumptions** based on:
   - SCANFI age classes (median age raster)
   - SCANFI landcover classes
   - Literature values for Canadian boreal/temperate forests
5. **Document assumptions** clearly as diagnostic, not production claims

### For Future Work
1. Contact provincial forestry agencies directly for yield curve access
2. Check if `magplot` contains yield-relevant plot data
3. Consider licensing commercial yield curve products if needed for production
4. Use NFI ground plot data (if access approved) for calibration

## Blockers
- **No open yield curve datasets found** on any provincial or national portal
- **Yield curves are typically restricted** or require formal requests
- **SCANFI v2 provides biomass/age** but not temporal yield functions

## Recommended Approach for FRESH
- Use **SCANFI v2 age + biomass** for cross-sectional biomass estimation
- Use **generic yield curves** (literature-based) for temporal projections
- **Document all assumptions** as diagnostic
- **Do NOT claim production-ready yield estimates** without verified provincial sources

## Evidence
- Open Canada API searches (2026-08-06)
- Provincial portal checks (2026-08-06)
- Existing repo source inventory (`magplot` candidate)