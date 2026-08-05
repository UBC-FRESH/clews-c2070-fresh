# NFI Standard Reports Source Review

Review date: 2026-06-25.
Updated: 2026-08-05 — key CSVs downloaded to `data/raw/nfi/standard_reports/t1/`.

## Source

- Dataset family: National Forest Inventory standard reports.
- Publisher: National Forest Inventory Canada.
- Landing page: `https://nfi.nfis.org/en/standardreports`.
- Notices page: `https://nfi.nfis.org/en/notices`.

## Public Report Inventory

The public standard reports page was parsed on 2026-06-25.

- Total parsed report links: 810.
- CSV links: 270.
- PDF links: 270.
- HTML links: 270.
- Reporting geographies:
  - `CA`: Canada, 270 links.
  - `NFI`: NFI reporting units, 270 links.
  - `BORE`: Boreal Zone, 135 links.
  - `MF2`: forest management regions, 135 links.
- Report period exposed in parsed links: `t1`, described on the page as first
  remeasurement reports for 2007-2017.
- Forest change estimate reports are also exposed under the `t1` report tree.

The parsed link manifest is tracked at:

- `metadata/source-file-manifests/nfi_standard_reports_links.csv`

## Relevant Tables

The reports include small CSV summary tables relevant to early model validation:

- land-cover area;
- forest and non-forest land area;
- forest area by forest type and age class;
- forest area by IUCN category and age class;
- forest area by ownership;
- forest area by species group and age class;
- tree volume by forest type, species group, and age class;
- above-ground biomass by forest type, species group, and age class;
- carbon summaries;
- land-use area; and
- annual change estimates for corresponding variables.

Example checked CSVs:

- `ca_t17_forage20_biom_en.csv`: total above-ground biomass on forest land by
  forest type and age class in Canada.
- `ca_t17_forage20_biom_change_en.csv`: annual change in total above-ground
  biomass on forest land by forest type and age class in Canada.

## Access And Licensing

The public standard reports expose direct CSV/PDF/HTML links and do not require
login for those report outputs.

The NFI data request page states that requests for custom estimates, map
products, and raw data are subject to approval by the NFI. It also points users
to separate kNN map datasets on Open Canada.

The NFI notices page does not present Open Government Licence language. It says
materials may be reproduced for personal and public non-commercial use with
source attribution and accuracy diligence, but commercial redistribution of
multiple copies requires written permission from the Government of Canada's
copyright administrator.

Because CLEWs-C2070 has explicit open-data commitments, treat this source as
`needs_license_review` before re-hosting report snapshots in the public DataLad
archive.

## Recommendation

Use NFI standard reports as validation and calibration summaries, not as a
primary spatial modelling layer.

Recommended near-term policy:

- Keep the parsed report-link manifest in the parent repository.
- Do not download and re-host all CSV/PDF report outputs until the licence and
  redistribution position is confirmed.
- If used in analysis before license review is resolved, record source URLs and
  retrieval date in derived-product metadata, and keep only derived QA summaries
  in the parent repo.
- Create a separate candidate entry for NFI kNN map datasets if those are needed
  as spatial inputs, because they are distributed through Open Canada rather
  than the NFI standard reports page.
