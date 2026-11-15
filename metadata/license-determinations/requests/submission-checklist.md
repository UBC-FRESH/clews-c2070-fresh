# Submission Checklist

All nine requests in `tracking.csv` are `not_sent`. This checklist is the ordered procedure for the PI. Nothing here has been submitted, and no access has been granted.

## 1. Freeze and review

- [ ] Read `README.md`, the five request files, and `tracking.csv`.
- [ ] Confirm the scope is nine requests: NFI ground, NFI photo, MAGPlot Tier 3, CASFRI, Alberta, Ontario, Nova Scotia, Manitoba, Northwest Territories.
- [ ] Confirm that no file claims access, approval, or a signed agreement.

## 2. Complete the NFI package

File: `nfi-ground-photo-request.md`.

- [ ] Fill every `[PI TO FILL: ...]`: email, phone, project start and end year, project URL if any, community-consent choice, optional collaborators, signature, date.
- [ ] Confirm or revise the drafted project objectives, deliverables, and study area.
- [ ] Confirm Exact Coordinates = Yes. Read the ±10 km warning and the justification.
- [ ] Check that the 495-character Request Details text fits the form. If it is rejected, remove `BAF/radius, ` (12 characters) and use the shorter version.
- [ ] Re-read the five data-use terms on the live form. If the wording differs from §4 of the request file, stop and update the file before submitting.
- [ ] Sign and date the PI attestation.

## 3. Submit the NFI form

- [ ] Open https://nfi.nfis.org/en/datarequestform.
- [ ] Enter the field values exactly as drafted. Use the subject and project block consistently across all follow-ups.
- [ ] Select Ground Plot, Photo Plot, MAGPlot, and CASFRI. If the form accepts only one data type per submission, submit Ground Plot first, then separate Photo Plot, MAGPlot, and CASFRI submissions with the same project block.
- [ ] Save the confirmation page or confirmation number.
- [ ] If the form has no attachment field, email the signed attestation and the exact-coordinate justification to `nfisupport@nfis.org`, quoting the confirmation number.
- [ ] Record the date sent, confirmation number, and route in `tracking.csv`. Set status to `sent` and response to `pending`.

## 4. Send the provincial and territorial requests

Four destinations (Alberta, Ontario, Nova Scotia, Manitoba/NWT), five messages because Manitoba and NWT are separate. Fill the PI email and phone placeholders in every message. Record each row separately.

- [ ] **Alberta:** confirm `af.fmb-biometrics@gov.ab.ca` is the current PGYI/PSP/MPB intake; send `alberta-pgyi-psp-request.md`.
- [ ] **Ontario:** send `ontario-psp-request.md` to `info.MNRscience@ontario.ca`.
- [ ] **Nova Scotia:** verify the NS DNRR contact; send `nova-scotia-psp-request.md`, or use the route at https://novascotia.ca/natr/forestry/.
- [ ] **Manitoba:** verify the Manitoba Forestry contact; send section 1 of `manitoba-nwt-request.md`.
- [ ] **Northwest Territories:** verify the GNWT forest management contact and responsible division; send section 2 of `manitoba-nwt-request.md`.
- [ ] Do not attach raw data or coordinates. Keep the PI signature block in every message.

## 5. Record dates and follow up

- [ ] Update `tracking.csv` within one business day of every send or reply.
- [ ] Log acknowledgement dates and reviewer names in the project record and, when material, in `CHANGE_LOG.md`.

The intervals below are planning estimates, not service-level commitments.

| Source | Expected first response | Likely follow-up | Action |
|---|---|---|---|
| NFI | 4–6 weeks for acknowledgement (observed; no SLA) | DUA, exact-coordinate review, jurisdictional approvals; 6–18 months observed end-to-end | Log, respond, and keep the request open; do not treat silence as approval |
| Alberta | 2–4 weeks | DSA template, fee schedule, confirmation of counts | Complete the DSA; confirm 2,193 sites and 1960–2024 coverage |
| Ontario | 2–4 weeks | DSA routing, coordinate terms, fees | Ask for the correct DSA owner if Science cannot process it |
| Nova Scotia | 2–4 weeks | Correct office, DSA | Forward to the Forestry Division if routed elsewhere |
| Manitoba | 2–4 weeks | Correct office, DSA | Confirm 495 sites |
| Northwest Territories | 2–4 weeks | Correct office, DSA | Confirm 471 sites |
| CASFRI | 2–6 weeks | Route confirmation, per-jurisdiction licence | Use the NFI form first; only pursue the CASFRI project separately if the form cannot route it |

Follow up once at two weeks for provincial requests and once at four weeks for NFI; then monthly. Avoid duplicate submissions.

## 6. Fallback if NFI is delayed

Activate the fallback when any trigger in `../nfi-fallback-strategy.yaml` is met: the DUA is not initiated by the project's M3 checkpoint, review exceeds six months, the terms are unacceptable, or approved data are delayed more than three months after approval. Operationally, six weeks without acknowledgement is a prompt to escalate, not a formal trigger.

1. Continue with the archived stack: MAGPlot v1.2 (39,130 sites; 20,890 remeasured; 1,573,761 remeasured trees), BC FAIB (7,341 PSP sites and 9,512 non-PSP sites), Québec PEP (12,834 plots), and NL FAL (1,380 plots).
2. Add Alberta, Ontario, Nova Scotia, Manitoba, and Northwest Territories data as they arrive.
3. Treat late NFI data as validation-only; do not restart calibration.
4. Document coverage gaps by ecozone. Do not claim a national calibration where a region has no plots.
5. Flip the `nfi_access` gate only on evidence. Update `../nfi-access.yaml` and `../../readiness-gates.yaml` when a signed agreement or delivered data is in hand.

## 7. Records and prohibitions

- [ ] Keep confirmation numbers, DUA and DSA copies, data dictionaries, and delivery manifests with the project record.
- [ ] Keep raw data and coordinates in the `data` submodule with access controls, not in the parent repository.
- [ ] Do not submit duplicate requests.
- [ ] Do not share exact coordinates outside the named project team.
- [ ] Do not publish raw records or any product from which they could be reconstructed.
- [ ] Do not use the data for a different project without a new request.
- [ ] Do not claim access granted until a signed agreement or delivered data is in hand.

## Sign-off

| Step | Name | Date |
|---|---|---|
| PI review and signature | Dr. Gregory Paradis | `[PI TO FILL: date]` |
| NFI form submitted | `[PI TO FILL: name]` | `[PI TO FILL: date]` |
| Provincial requests sent | `[PI TO FILL: name]` | `[PI TO FILL: date]` |
