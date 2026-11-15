# Manitoba and Northwest Territories PSP Data Requests

| Field | Value |
|---|---|
| Prepared | 2026-11-14 |
| Contents | Two separate emails: Manitoba PSP (495 sites) and Northwest Territories PSP (471 sites) |
| From | Dr. Gregory Paradis, University of British Columbia |
| Status | Drafts; both contacts unverified; **not sent** |

Send the two messages separately and record them on separate rows in `tracking.csv`. Both request the same variable set and the same derived-products-only commitment. Each email is under 700 words; they are combined here for a single PI review.

## 1. Manitoba

| Field | Value |
|---|---|
| To | `[VERIFY: Manitoba Forestry — data request contact, name, and email]` |
| Subject | Data request and DSA: Manitoba PSP plots for national growth-and-yield calibration |

Hello,

I am writing on behalf of CLEWs-C2070, a research project funded by Natural Resources Canada's Emerging Innovators Program (EIP-NES-147). The contract is held by Simon Fraser University, and UBC FRESH is a sub-grantee. I am the PI for this data request. We are building a national forest growth-and-yield layer for an energy-land-water model and request access to Manitoba's permanent sample plot (PSP) data under a data-sharing agreement. If this message has reached the wrong office, please forward it to the responsible forestry contact for PSP data.

**Purpose.** The project already holds the open MAGPlot v1.2 Tier-1 package, the BC FAIB ground-plot compilations, the Québec PEP, and the NL FAL PSP. The open MAGPlot package contains only two Manitoba sites, and its national tables have no age or site-index fields. Manitoba's PSP network is therefore the only tree-level source available to this project for the Taiga Plains and Taiga Shield. We would use the plots to calibrate species-specific growth, mortality, and site-index relationships, and to test them against held-out plots. This is non-commercial calibration and validation.

**Data requested.** Our inventory records approximately 495 Manitoba PSP sites; please confirm the current count and coverage. We request, for all available remeasurement cycles:

- plot identifier, coordinates, ownership or landbase, and visit dates;
- plot design: BAF or fixed radius, plot area, and expansion factors;
- tree-level records: persistent tree ID, species, DBH, height, live/dead status, crown class, damage and insect or disease codes, ingrowth, and removals;
- site index with base age, stand age, ecosite or ecological site, soil texture, and drainage;
- disturbance and treatment history, including fire, harvest, and insect outbreaks;
- a data dictionary and location-quality flags.

**Format and use.** CSV or Parquet with a data dictionary; coordinates in NAD83. We request the data under Manitoba's data-sharing agreement. We will use them only for this project, publish derived products only, and not redistribute raw records or exact coordinates. Data will be stored on secure project infrastructure with access limited to the named team, retained for the project term, and then destroyed or returned as the agreement requires. Manitoba will be acknowledged in publications and derived products. We are not asking to visit sites.

**Next steps.** Please send the current DSA template, any fee schedule, and the expected delivery format and timeline, or direct us to the correct office.

Regards,

Dr. Gregory Paradis  
Assistant Professor, Forest Resources Management  
University of British Columbia  
`[PI TO FILL: email]` | `[PI TO FILL: phone]`

## 2. Northwest Territories

| Field | Value |
|---|---|
| To | `[VERIFY: Government of the Northwest Territories — forest data request contact and responsible division]` |
| Subject | Data request and DSA: Northwest Territories PSP plots for national growth-and-yield calibration |

Hello,

I am writing on behalf of CLEWs-C2070, a research project funded by Natural Resources Canada's Emerging Innovators Program (EIP-NES-147). The contract is held by Simon Fraser University, and UBC FRESH is a sub-grantee. I am the PI for this data request. We are building a national forest growth-and-yield layer for an energy-land-water model and request access to the Northwest Territories' permanent sample plot (PSP) data under a data-sharing agreement. If this message has reached the wrong office, please forward it to the responsible forest management contact for PSP data.

**Purpose.** The project already holds the open MAGPlot v1.2 Tier-1 package, the BC FAIB ground-plot compilations, the Québec PEP, and the NL FAL PSP. The open MAGPlot package contains 262 Northwest Territories sites, but its national tables have no age or site-index fields. The territorial PSP network is therefore the only tree-level source available to this project for the Taiga Plains and Taiga Shield with the attributes the calibration needs. We would use the plots to calibrate species-specific growth, mortality, and site-index relationships, and to test them against held-out plots. This is non-commercial calibration and validation.

**Data requested.** Our inventory records approximately 471 Northwest Territories PSP sites; please confirm the current count and coverage. We request, for all available remeasurement cycles:

- plot identifier, coordinates, ownership or landbase, and visit dates;
- plot design: BAF or fixed radius, plot area, and expansion factors;
- tree-level records: persistent tree ID, species, DBH, height, live/dead status, crown class, damage and insect or disease codes, ingrowth, and removals;
- site index with base age, stand age, ecosite or ecological site, soil texture, and drainage;
- disturbance and treatment history, including fire, harvest, and insect outbreaks;
- a data dictionary and location-quality flags.

**Format and use.** CSV or Parquet with a data dictionary; coordinates in NAD83. We request the data under the Government of the Northwest Territories' data-sharing agreement. We will use them only for this project, publish derived products only, and not redistribute raw records or exact coordinates. Data will be stored on secure project infrastructure with access limited to the named team, retained for the project term, and then destroyed or returned as the agreement requires. The Government of the Northwest Territories will be acknowledged in publications and derived products. We are not asking to visit sites.

**Next steps.** Please send the current DSA template, any fee schedule, and the expected delivery format and timeline, or direct us to the correct office.

Regards,

Dr. Gregory Paradis  
Assistant Professor, Forest Resources Management  
University of British Columbia  
`[PI TO FILL: email]` | `[PI TO FILL: phone]`

## Send notes

- Confirm both recipient addresses and office names before sending. The landscape plan records no verified MB or NT PSP email.
- Do not attach raw data or coordinates.
- Record the date sent and any response in `tracking.csv`.
