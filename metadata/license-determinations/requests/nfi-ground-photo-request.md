# NFI Data Request Form — Ground Plots, Photo Plots, MAGPlot Restricted Tier, and CASFRI

| Field | Value |
|---|---|
| Prepared | 2026-11-14 |
| Target | NFI Data Request Form, https://nfi.nfis.org/en/datarequestform |
| Project | CLEWs-C2070 — Innovative Integration of Climate, Land, and Water into Energy Models for Canada's Net-Zero Commitments |
| Funder | Natural Resources Canada, Emerging Innovators Program (EIP-NES-147); contract held by Simon Fraser University; UBC FRESH is a sub-grantee |
| PI | Dr. Gregory Paradis, Assistant Professor, Forest Resources Management, University of British Columbia |
| Status | Draft for PI review; **not submitted** |

This file is the text to enter in the online form. It covers four data types in one submission: Ground Plot, Photo Plot, MAGPlot, and CASFRI. Fields marked `[PI TO FILL: ...]` cannot be completed from the project record. Narrative fields marked `[PI TO FILL: confirm]` are drafted for the PI's approval. No access has been granted.

## 1. Form field values

Name, title, organization, and affiliation are pre-filled from the project record; the PI confirms them at signature. Contact details and dates are placeholders.

| Form field | Value to enter |
|---|---|
| Data Requestor — First Name | Gregory |
| Data Requestor — Last Name | Paradis |
| Data Requestor — Email | `[PI TO FILL: email]` |
| Data Requestor — Phone | `[PI TO FILL: phone]` |
| Project Lead — First Name | Gregory |
| Project Lead — Last Name | Paradis |
| Project Lead — Email | `[PI TO FILL: email]` |
| Project Lead — Phone | `[PI TO FILL: phone]` |
| Title | Assistant Professor, Forest Resources Management |
| Organization | University of British Columbia |
| Country | Canada |
| Affiliation | University |
| Project Title | CLEWs-C2070 — Innovative Integration of Climate, Land, and Water into Energy Models for Canada's Net-Zero Commitments |
| Project Start Date | `[PI TO FILL: YYYY]` |
| Project End Date | `[PI TO FILL: YYYY]` |
| Project URL | `[PI TO FILL: URL, or leave blank]` |
| Data Type | Ground Plot; Photo Plot; MAGPlot; CASFRI |
| Study Area | Canada; forested ecozones outside the Arctic `[PI TO FILL: confirm; 44 of 50 characters]` |
| Exact Coordinates | Yes `[PI TO FILL: confirm]` |
| Collaborators | `[PI TO FILL: optional; suggested Taco Niet, Simon Fraser University, with his consent]` |
| Join NFI Data Community | `[PI TO FILL: Yes or No]` |

**Project Objectives** (maximum 1500 characters) — `[PI TO FILL: confirm or revise]`

> CLEWs-C2070 integrates climate, land, and water into energy models for Canada's net-zero pathways. The forestry module (WS3) needs a national growth-and-yield layer that represents species-specific stand development across forested ecozones, not only British Columbia. We will: (1) harmonize the open plot archives already held (MAGPlot v1.2, BC FAIB, Québec PEP, NL FAL) into one tree-list schema; (2) fit and test growth, mortality, and site-index relationships against SCANFI v2 30 m attributes; (3) test whether regional FVS variants can be routed by ecozone, with a meta-model fallback where no variant applies. NFI ground plots are the only national tree-level source with site index and age, and the only national reference outside the provinces already covered. Exact coordinates are needed for 30 m pixel linkage and to check whether a plot contributed to SCANFI training, so validation remains independent. Outputs are aggregate calibration statistics and parameter sets; no raw plot data are published. Non-commercial research only. The project is funded by Natural Resources Canada's Emerging Innovators Program (EIP-NES-147); Simon Fraser University holds the contract, and UBC FRESH is a sub-grantee.

**Project Deliverables** (maximum 1000 characters) — `[PI TO FILL: confirm or revise]`

> 1. Harmonized plot-data contract and per-source crosswalks (documentation only; raw records remain within the project). 2. Calibrated growth, mortality, and site-index parameter sets with domain limits and provenance. 3. Independent validation report using held-out NFI plots, with error by ecozone and species group. 4. Open-source model code and derived parameter tables; no raw NFI, provincial, or territorial records. 5. Data-management note covering access control, retention, and destruction under the signed agreement.

**Request Details** (maximum 500 characters; the text below is 495 characters, ASCII only) — copy exactly:

```text
Ground Plot: tree-level remeasurement, all cycles (species, DBH, height, live/dead, BAF/radius, site index, age, disturbance, IDs, dates, exact coordinates). Photo Plot: validation/stratification only, no tree lists. MAGPlot: restricted AB/ON/NS and MB/NT/PEI/private records. CASFRI: vector DB, initial conditions only. Exact coords link plots to 30 m SCANFI pixels for site-index calibration and independent validation; +/-10 km fuzzing cannot resolve a pixel. No visits or coordinate sharing.
```

If the form rejects the text as over length, remove `BAF/radius, ` (12 characters) and resubmit the shorter version.

## 2. Exact-coordinate justification

The form warns that exact-coordinate requests may significantly delay release, and that coordinates are rounded to +/- 10 km by default. This request covers public-land plots; for private-land plots we ask for the finest location the contributor permits.

Use the following text if the form provides a separate justification field, or send it to `nfisupport@nfis.org` after submission, quoting the confirmation number:

> SCANFI v2 is a 30 m product. A +/-10 km displacement spans a 20 km × 20 km box, roughly 400 km², and cannot identify the pixel a plot represents, so pixel-level linkage is impossible without exact plot centres. Exact coordinates are required for three uses: (1) extracting the SCANFI v2 pixel at each plot centre and matching it to the measurement year; (2) calibrating site index against the SCANFI productivity layers at the same pixel; and (3) checking whether a plot contributed to SCANFI training, so that validation plots remain independent. We will not visit plots, will not share coordinates outside the named project team, and will not publish coordinates or any record from which they could be reconstructed. Released products will be derived aggregates only. We accept the additional review time this request may cause.

## 3. Data requirements by type

| Data type | Requested content |
|---|---|
| Ground Plot | All available remeasurement cycles. Tree-level records with persistent tree and plot IDs; species; DBH; height; live/dead/removed status and cause; crown class; increment fields; plot design (BAF or fixed radius/area); site index and base age; stand age; ecosite or ecozone; soil texture; drainage; slope; aspect; disturbance and treatment history; measurement dates; exact coordinates for public-land plots (finest available location and quality flag for private-land plots); jurisdiction identifiers. |
| Photo Plot | Photo-plot attributes for validation and stratification only. No tree lists. |
| MAGPlot | Restricted (Tier 3) records for Alberta, Ontario, and Nova Scotia, plus any restricted Manitoba, Northwest Territories, Prince Edward Island, or private-land records. Exact coordinates where the contributor permits. The open Tier-1 package is already held and need not be resent. |
| CASFRI | Vector database for initial conditions only, not calibration. Per-jurisdiction licence terms apply. |

The full variable-level schedule is in `../nfi-targeted-request.md` §2 and `../nfi-variable-crosswalk.md`. Use those documents if the NFI Projects Office requests a detailed list. The old package's §9 plot-count estimate predates the 2026-11-14 landscape review and is not repeated here.

## 4. Data-use terms — verified text

Quoted verbatim from the NFI Data Request Form, accessed 2026-11-14. The PI accepts these terms by clicking Submit.

> NFI data are intended for ecozone or national-level analysis.
>
> If requesting data for a smaller-scale study (e.g., jurisdiction level), I will use other sources of data alongside NFI data.
>
> I will only publish derived products and no raw data.
>
> The data will be used solely for this project, and a new data request will be submitted if the data is required for a different project.
>
> I shall acknowledge the Canadian National Forest Inventory in any publications or products resulting from this data request.

In practice, the analysis is national and ecozone-level; provincial sources are used alongside NFI data, and NFI is not treated as a substitute for provincial samples. The project will keep raw records within the named team, publish only derived products, submit a new request before using the data for any other project, and cite the Canadian National Forest Inventory in publications and products.

## 5. PI attestation

I have read this request and the five NFI data-use terms above. I confirm the project details and accept the terms on behalf of the project.

| Field | Value |
|---|---|
| Name | Dr. Gregory Paradis |
| Title | Assistant Professor, Forest Resources Management, University of British Columbia |
| Signature | `[PI TO FILL: signature]` |
| Date | `[PI TO FILL: date]` |

## 6. Submission note

Save the confirmation page or number, record the date in `tracking.csv`, and keep the signed attestation with the project record. If the form accepts only one data type, submit Ground Plot first, then separate Photo Plot, MAGPlot, and CASFRI submissions with the same project block. If CASFRI is routed outside the form, use `[VERIFY: CASFRI data request contact]` or https://github.com/CASFRI/CASFRI.
