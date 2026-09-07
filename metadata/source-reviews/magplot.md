# MAGPlot Source Review

Review date: 2026-08-06.

## Source

- Dataset: Multi-Agency Ground Plot (MAGPlot) Database v1.2
- Publisher: National Forest Inventory Canada (Natural Resources Canada)
- Open Canada record: https://open.canada.ca/data/en/dataset/1a73441d-cbe9-485b-bc91-84e07f97193f
- Data download: https://ca.nfis.org/fss/fss?command=retrieveByName&fileName=MAGPlot_latest_data_package.zip&fileNameSpace=magplot&format=xml&promptToSave=true
- Data dictionary: https://ca.nfis.org/fss/fss?command=retrieveByName&fileName=MAGPlot_DB_Data_Dictionary_En.pdf&fileNameSpace=magplot&format=xml&promptToSave=true

## License and Provenance

- **License:** Open Government Licence - Canada (ca-ogl-lgo)
- **License URL:** https://open.canada.ca/en/open-government-licence-canada
- **Provenance:** Pan-Canadian synthesis of forest ground plot data from NFI and 12 jurisdictions (AB, BC, MB, NB, NL, NS, NT, ON, PE, QC, SK, YT)
- **Contact:** support@nfis.org, 250-363-0600

## Coverage

- **Temporal:** Historical and contemporary; current as of 2026 (v1.2)
- **Spatial:** Pan-Canadian (all provinces and territories)
- **Content:** Forest ground plot measurements including site info, treatments, disturbances, and tree measurements

## Data Structure (8 tables)

1. `magp_site_sources` — Source attribution
2. `magp_sites` — Site information (MAGP_SITE_ID, coordinates, plot type)
3. `magp_treatment` — Stand treatments
4. `magp_disturbance` — Disturbance events
5. `magp_tree_header` — Tree measurement session headers
6. `magp_trees` — Tree measurements (DBH, height, species, crown class, stems/ha)
7. `magp_tree_condition` — Tree condition codes
8. `magp_tree_removed` — Removed trees

**Key measurement fields:**
- `meas_est_dbh` — Diameter at breast height (cm)
- `height` — Tree height (m)
- `genus`, `species`, `variety` — Taxonomy
- `crown_class` — Crown classification
- `stem_ha` — Stems per hectare
- `stem_cond` — Stem condition

## Data Access Tiers

Five tiers based on contributor data-sharing conditions:

- **Tier 1 (Open):** Exact coordinates — QC_PSP, YT_PSP, NB_CLI plots on public land
- **Tier 1 (Open with fuzzing):** BC_FAIBGP plots
- **Tiers 2-5:** Restricted per contributor protocols (require approval/request)

The "Data Package" on the FGP page includes Tier 1 open-access data.

## Package Size and Contents

- **Latest data package:** ZIP file (URL confirmed returning valid ZIP; exact size not verified due to download timeout)
- **Data dictionary PDF:** 912 KB (downloaded and inspected)
- **Historical dictionaries:** Available as separate ZIP
- **Supplementary material:** Available as separate ZIP

## Suitability for Gauthier Calibration

**Assessment: ACCEPTED for generalized yield assumptions.**

- Provides pan-Canadian ground plot measurements with DBH, height, species, and stand attributes
- Standardized and harmonized to NFI protocols
- Open Government Licence - Canada permits use, adaptation, and redistribution with attribution
- Tier 1 open data provides immediate access to a defensible subset
- Relational structure supports aggregation by jurisdiction, plot type, species, and age class
- Directly relevant to yield curve development and validation

## Recommended Inventory Status

- **Status:** `accepted`
- **Access path:** Direct download of Tier 1 data package from ca.nfis.org
- **Next action:** Download Tier 1 data package, validate schema against data dictionary, extract jurisdiction-level summaries for yield modeling

## Risks and Blockers

- Full dataset download timed out during this review; exact package size unknown
- Tier 2-5 data requires separate access requests per contributor protocols
- MAGP_SITE_ID may change across versions (per data dictionary v1.1→v1.2 notes)
- Coordinate fuzzing applied to some jurisdictions (BC_FAIBGP)

## Evidence

- Open Canada API response: `/tmp/magplot_metadata.json` (29,950 bytes)
- Data dictionary PDF: `tmp/magplot_triage/MAGPlot_Data_Dictionary_En.pdf` (912 KB)
- Data dictionary text extraction: tables, fields, access tiers confirmed