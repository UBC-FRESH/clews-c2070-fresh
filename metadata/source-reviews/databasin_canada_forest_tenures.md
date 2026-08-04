# Data Basin Canada Forest Tenures - Source Review

**Review date**: 2026-08-04
**Source ID**: `databasin_canada_forest_tenures`
**Status**: `candidate`

## Source Overview

- **Dataset**: Canada - forest tenures (draft)
- **Publisher**: Data Basin / James R. Strittholt
- **License**: Not yet verified
- **Access**: Data Basin gallery
- **Format**: Geospatial gallery (individual dataset format TBD)
- **Gallery URL**: https://databasin.org/galleries/1dd5bbe4e47a4855a2095eae7cf3253a/
- **Likely member dataset URL**: https://databasin.org/datasets/2f20af8c4f7949ad8ab631b77dc733c3/
- **Created**: 2012-07-02 (draft)
- **Temporal**: 2012 draft; currency unknown

## Gallery Notes (from source-inventory.csv)

- Title: "Canada - forest tenures (draft)"
- Description: nationally compiled forest tenures in Canada
- Created by: James R. Strittholt
- Gallery contains 4 datasets
- Likely national member dataset: "Provincial Forest Tenures - Forest Management
  Areas (draft)"

## Key Questions

1. **Member dataset verification**: Is the "Provincial Forest Tenures - Forest
   Management Areas (draft)" dataset actually accessible through the gallery?
2. **Download endpoint**: What is the actual download URL for the member dataset?
   Is it a direct download or does it require interaction?
3. **Source lineage**: Who originally compiled this data? Is it derived from
   provincial tenure records, and if so, which ones?
4. **Spatial unit semantics**: Are the features Forest Management Units (FMUs),
   timber sales areas, or other tenure boundaries? What is the relationship to
   FRESH supply-node boundaries?
5. **License**: What is the license for this Data Basin content? Is it open
   for modelling use or is it rights-managed by the creator?
6. **Update status**: The 2012 draft label suggests the data may be stale.
   Has it been updated since 2012? What is the currency?
7. **Fit to FMU/supply-node boundary contract**: Would this provide a national
   Forest Management Unit boundary layer, or is it too coarse/generalized for
   the FRESH supply-node spatial unit needs?

## Safety Stop Condition

Do not archive or materialize merely because the gallery exists. The 2012 draft
status and unverified license/redownload path make this a candidate only. Keep
in `triage_ready` state until all questions above are resolved.

## Recommended Next Steps

1. Visit the gallery URL to verify member dataset accessibility
2. Identify the actual download endpoint for the tenure boundary layer
3. Check Data Basin terms of service and license for the content
4. Determine source agency and update status for the underlying provincial
   tenure data
5. Assess spatial unit semantics against FRESH FMU/supply-node requirements

## Decision

**Triage status**: `needs_member_dataset_resolution`

Remain in candidate state until member dataset URL, source lineage, license,
and FMU-boundary fit are confirmed.
