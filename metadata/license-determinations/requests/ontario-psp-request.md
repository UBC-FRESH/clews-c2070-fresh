# Ontario PSP / PGP / PIP Data Request

| Field | Value |
|---|---|
| Prepared | 2026-11-14 |
| To | info.MNRscience@ontario.ca |
| From | Dr. Gregory Paradis, University of British Columbia |
| Subject | Data request and DSA: Ontario PSP/PGP/PIP plots for national growth-and-yield calibration |
| Status | Draft; **not sent** |

## Email text

Hello,

I am writing on behalf of CLEWs-C2070, a research project funded by Natural Resources Canada's Emerging Innovators Program (EIP-NES-147). The contract is held by Simon Fraser University, and UBC FRESH is a sub-grantee. I am the PI for this data request. We are building a national forest growth-and-yield layer for an energy-land-water model and request access to Ontario's PSP, PGP, and PIP plot data under a data-sharing agreement.

**Purpose.** The project already holds the open MAGPlot v1.2 Tier-1 package (39,130 sites; 1.57 million remeasured trees), the BC FAIB ground-plot compilations (7,341 PSP sites and 9,512 non-PSP sites), the Québec PEP (12,834 plots), and the NL FAL PSP (1,380 plots). The open MAGPlot package contains no Ontario records. Ontario covers a large share of the Boreal Shield and Mixedwood Plains, both of which are currently unrepresented in our calibration. An Ontario FVS variant (FVS-ON) already exists in the Forest Vegetation Simulator source tree. We are not asking for a new variant; we would use the Ontario plots to validate and extend FVS-ON and to decide where it should be used instead of a meta-model fallback.

**Data requested.** Our inventory records approximately 5,477 Ontario PSP/PGP/PIP sites measured between 1980 and 2023; please confirm the current counts and coverage. We request, for all available remeasurement cycles:

- plot identifier, coordinates, ownership or landbase, and visit dates;
- plot design: BAF or fixed radius, plot area, and expansion factors;
- tree-level records: persistent tree ID, species, DBH, height, live/dead status, crown class, damage and insect or disease codes, ingrowth, and removals;
- site index with base age, stand age, ecosite or ecological land classification, soil texture, and drainage;
- disturbance and treatment history, including fire, harvest, and insect outbreaks;
- a data dictionary and location-quality flags.

We have reviewed the Ontario growth-and-yield data listing at https://data.ontario.ca/dataset/ontario-growth-and-yield-data. If exact coordinates cannot be released, please provide the finest location available and a location-quality flag; we will document the limitation and use the plots only where the location is adequate for 30 m linkage.

**Format and use.** CSV or Parquet with a data dictionary; coordinates in NAD83. We request the data under Ontario's data-sharing agreement. We will use them only for this project, publish derived products only, and not redistribute raw records or exact coordinates. Data will be stored on secure project infrastructure with access limited to the named team, retained for the project term, and then destroyed or returned as the agreement requires. Ontario will be acknowledged in publications and derived products. We are not asking to visit sites.

**Next steps.** Please send the current DSA template, any fee schedule, and the expected delivery format and timeline, or direct us to the correct office.

Regards,

Dr. Gregory Paradis  
Assistant Professor, Forest Resources Management  
University of British Columbia  
`[PI TO FILL: email]` | `[PI TO FILL: phone]`

## Send notes

- Use the subject line above; keep the message text unchanged except for the PI contact block.
- Do not attach raw data or coordinates.
- Record the date sent and any response in `tracking.csv`.
