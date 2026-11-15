# Restricted Plot Data — Request Package

| Field | Value |
|---|---|
| Prepared | 2026-11-14 |
| For | Dr. Gregory Paradis, PI, UBC FRESH |
| Project | CLEWs-C2070 — Innovative Integration of Climate, Land, and Water into Energy Models for Canada's Net-Zero Commitments |
| Funder | Natural Resources Canada, Emerging Innovators Program (EIP-NES-147); contract held by Simon Fraser University; UBC FRESH is a sub-grantee |
| Status | Draft for PI review. **Nothing has been submitted. No access has been granted.** |

This package turns the acquisition plan in `../../../planning/national-gy-data-landscape.md` §6 into send-ready requests. It covers nine requests across four NFI data types and five provincial or territorial sources. Each email request is under 700 words; the NFI file is a field-by-field form guide. Contacts come only from the landscape plan or the PI brief, and every unverified contact is marked `[VERIFY]`.

## 1. Files

| File | Contents | Send route | Status |
|---|---|---|---|
| `nfi-ground-photo-request.md` | NFI online form text for Ground Plot, Photo Plot, MAGPlot restricted tier, and CASFRI; verified NFI data-use terms; exact-coordinate justification; PI attestation | NFI Data Request Form | Draft; PI review and signature required |
| `alberta-pgyi-psp-request.md` | Email and DSA purpose statement for Alberta PGYI/PSP/MPB plots | Email to `af.fmb-biometrics@gov.ab.ca` | Draft; confirm intake address |
| `ontario-psp-request.md` | Email and DSA request for Ontario PSP/PGP/PIP plots | Email to `info.MNRscience@ontario.ca` | Draft; ready for PI review |
| `nova-scotia-psp-request.md` | Email and DSA request for Nova Scotia PSP plots | Email; contact unverified | Draft; contact unverified |
| `manitoba-nwt-request.md` | Two separate emails and DSA requests: Manitoba PSP and Northwest Territories PSP | Email; contacts unverified | Draft; contacts unverified |
| `tracking.csv` | Nine-row tracker with holder, contact, route, date sent, response, status, notes | Internal | All rows `not_sent` |
| `submission-checklist.md` | Ordered send procedure, follow-ups, and fallback path | Internal | Ready to use |

## 2. Status by request

| Dataset | Holder | Route | File | Blocker | Status |
|---|---|---|---|---|---|
| NFI Ground Plots | NRCan / Canadian Forest Service | NFI form; exact coordinates requested | `nfi-ground-photo-request.md` | PI contact details, dates, signature | Draft |
| NFI Photo Plots | NRCan / Canadian Forest Service | Same NFI form | `nfi-ground-photo-request.md` | Same as ground | Draft |
| MAGPlot Tier 3 | NRCan / Canadian Forest Service | Same NFI form; restricted tier | `nfi-ground-photo-request.md` | Same as ground | Draft |
| CASFRI v5 | CASFRI / Université Laval; NFI form lists CASFRI | NFI form; separate route if needed | `nfi-ground-photo-request.md` | CASFRI contact unverified | Draft |
| Alberta PGYI/PSP/MPB | Alberta Forestry | Email + DSA | `alberta-pgyi-psp-request.md` | Confirm intake address | Draft |
| Ontario PSP/PGP/PIP | Ontario MNRF | Email + DSA | `ontario-psp-request.md` | None beyond PI review | Draft |
| Nova Scotia PSP | NS DNRR | Email + DSA | `nova-scotia-psp-request.md` | Contact unverified | Draft |
| Manitoba PSP | Manitoba Forestry | Email + DSA | `manitoba-nwt-request.md` | Contact unverified | Draft |
| Northwest Territories PSP | GNWT (forest management) | Email + DSA | `manitoba-nwt-request.md` | Contact and division unverified | Draft |

Every row in `tracking.csv` is `not_sent`. "Draft" in this table means the text is ready for PI review, not that a request has been sent.

## 3. PI action list

1. Read this package and confirm the nine-row scope in `tracking.csv`.
2. Fill every `[PI TO FILL: ...]` in `nfi-ground-photo-request.md`: email, phone, project dates, project URL, community-consent choice, optional collaborators, signature, date. Fill the PI email and phone placeholders in each provincial request too.
3. Confirm or revise the drafted objectives, deliverables, and study area. Confirm Exact Coordinates = Yes.
4. Sign and date the PI attestation in §5 of the NFI file.
5. Submit the NFI form at https://nfi.nfis.org/en/datarequestform with Ground Plot, Photo Plot, MAGPlot, and CASFRI selected. Save the confirmation.
6. Send the Alberta email after confirming the intake address.
7. Send the Ontario email.
8. Verify and send the Nova Scotia, Manitoba, and Northwest Territories emails.
9. Record every send date, confirmation number, and response in `tracking.csv` within one business day.
10. If NFI is delayed, follow `submission-checklist.md` §6: the archived MAGPlot, BC FAIB, Québec PEP, and NL FAL data remain the working basis.

## 4. Contact verification

| Request | Contact | Verification |
|---|---|---|
| NFI, Photo, MAGPlot | `nfisupport@nfis.org` | Verified in the archived NFI data-request page and documentation records, 2026-11-14 |
| Alberta | `af.fmb-biometrics@gov.ab.ca` | Supplied in the PI brief; confirm it is the current PGYI/PSP/MPB intake |
| Ontario | `info.MNRscience@ontario.ca` | Verified in `../../../planning/national-gy-data-landscape.md` §6 |
| Nova Scotia | `[VERIFY: NS DNRR data request email]` | No address in the landscape plan; route via https://novascotia.ca/natr/forestry/ |
| Manitoba | `[VERIFY: Manitoba Forestry data request contact]` | No address in the landscape plan |
| Northwest Territories | `[VERIFY: GNWT forest data request contact]` | No address in the landscape plan |
| CASFRI | `[VERIFY: CASFRI data request contact]` | No address in the landscape plan; the NFI form lists CASFRI as a data type |

## 5. Placeholder conventions

- `[PI TO FILL: ...]` — the PI must enter or confirm this before sending.
- `[PI TO FILL: confirm]` — the text is drafted but the PI owns the final wording.
- `[VERIFY]` — the contact or route is not confirmed from a primary source in the repository. Verify before sending; do not guess an address.
- Known project facts are pre-filled from `../nfi-targeted-request.md`, `../../../planning/national-gy-data-landscape.md`, and `../../national-plot-data-inventory.csv`.
- No signature, email address, or phone number has been invented.

## 6. Unresolved fields

- PI email and phone (both requestor and project lead blocks).
- Confirmation of the pre-filled name, title, and affiliation.
- Project start and end year.
- Project URL, if the PI wants one listed.
- NFI Data Community consent choice.
- Optional collaborator listing.
- PI signature and date.
- Nova Scotia, Manitoba, Northwest Territories, and CASFRI contacts.
- Alberta intake-address confirmation.
- Ontario DSA owner and any fee, if Science cannot process the request directly.

## 7. Guardrails

- Do not submit, sign, or send anything on the PI's behalf.
- Do not claim that any request has been approved or that any data have been delivered.
- Do not publish raw records, exact coordinates, or any product from which a plot could be reconstructed.
- Use the data only for CLEWs-C2070. A different project requires a new request under the NFI terms, and a new agreement for provincial sources.
- The existing NFI DUA package at `../nfi-targeted-request.md` remains the detailed technical annex. This package does not replace it or modify it.
