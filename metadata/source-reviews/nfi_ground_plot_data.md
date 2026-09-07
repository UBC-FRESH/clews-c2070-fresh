# NFI Ground Plot Data Source Review

Review date: 2026-08-06.

## Source

- Dataset: National Forest Inventory ground plot data (raw plot-level measurements)
- Publisher: National Forest Inventory Canada
- Data request page: https://nfi.nfis.org/en/datarequest
- Data request form: https://nfi.nfis.org/en/datarequestform
- Contact: nfisupport@nfis.org

## License and Access Posture

- **Public data:** NFI standard reports (CSV/PDF/HTML summaries) are publicly accessible without login
- **kNN map products:** Available for download on Open Canada (dataset ec9e2659-1c29-4ddb-87a2-6aced147a990), Open Government Licence - Canada
- **Raw ground plot data:** Requires submission of NFI Data Request Form
- **Approval:** "All requests are subject to approval by the NFI"
- **Usage tracking:** Submitted information used to track NFI data use and impact; stored securely for internal use only
- **Redistribution:** Not explicitly stated for raw plot data; assume restricted until written confirmation received

## Public vs. Request-Only

| Content | Access | Notes |
|---------|--------|-------|
| Standard reports (CSV/PDF/HTML) | Public | Already downloaded in `data/raw/nfi/standard_reports/t1/` |
| kNN map datasets (2001, 2011) | Public (Open Canada) | Downloadable ZIP files |
| Raw ground plot measurements | Request-only | Requires NFI Data Request Form + approval |
| Custom estimates | Request-only | Requires NFI Data Request Form + approval |
| Map products (custom) | Request-only | Requires NFI Data Request Form + approval |

## Recommended Inventory Status

- **Status:** `needs_license_review`
- **Access path:** Submit NFI Data Request Form for raw plot data; await approval
- **Next action:** Do not submit form in this phase; document access requirement; consider whether MAGPlot Tier 1 data suffices for initial calibration work

## Risks and Blockers

- **Blocker:** Raw NFI ground plot data not publicly available; access requires approval
- **Uncertainty:** Redistribution terms for approved raw data not explicitly documented
- **Timeline:** Approval process timeline unknown; may delay calibration work
- **Mitigation:** MAGPlot Tier 1 data provides immediate pan-Canadian coverage under OGL Canada

## Evidence

- NFI data request page HTML: `/tmp/nfi_datarequest.html` (24,074 bytes)
- Key quote: "Requests for custom estimates, map products, and raw data can be made by submitting a NFI Data Request Form. ... All requests are subject to approval by the NFI."
- Open Canada kNN maps dataset: ec9e2659-1c29-4ddb-87a2-6aced147a990 (license: OGL Canada)