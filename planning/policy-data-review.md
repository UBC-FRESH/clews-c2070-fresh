# Policy data review — narrow source pass

**Status:** desk review for `policy_test_v1`; no model change, no solve, no
`policy_grade` claim. This note supplies the values and the explicit gaps used
in `policy-test-v1.md` §2.1–§2.2. It is **not** a signed source register.

**Review date:** 2026-11-20
**Scope:** national planning reserve margin, capacity credits / ELCC,
historical build rates, 2021 provincial peaks, and intertie ratings.
**Method:** public web sources only. No source payload was archived or hashed.
Values are the reviewed values, not necessarily the exact source cell; where
they differ, the note says so.

## Confidence

| Level | Meaning |
|---|---|
| High | Primary source states the exact value with a clear definition, jurisdiction, and date. |
| Medium | Primary or authoritative secondary source, but the window, column, direction, or definition is ambiguous. |
| Low | Derived, secondary only, or conflicting values unresolved. |
| Gap | No source found; the value must not be used as policy-grade. |

## 1. Reserve margins and reliability criteria

| Item | Value | As-of | Source | Confidence | Note |
|---|---:|---|---|---|---|
| Canada national modelling convention | 15% | 2024–2026 | S1, S2 | Medium | Modelling recommendation, not federal law. NERC's reference margin is 15% for predominantly thermal systems and 10% for hydro; Canadian provinces differ. |
| Ontario (IESO) | 15% | 2024 | S3 | High | 2024 requirement. The 2024–2028 outlook varies 11–16% by year; 15% is not constant. |
| Manitoba (Manitoba Hydro) | 12% | 2023–2025 | S4 | High | Capacity criterion: 12% of forecast winter peak. |
| MISO | 15% | 2024–2025 | S5, S29 | Medium | Rounded convention. MISO's 2025 PRMR was 15.7%; seasonal auction targets have been lower (for example 7.9% for summer 2025/26). Confirm planning year. |
| NERC LOLE | ≤0.1 d/yr | 2024 | S2 | High | One day in ten years. |
| WECC contingency reserve | BAL-002-WECC-3 | 2024 | S6 | High | Contingency reserve standard; not a planning reserve margin. |
| WECC adequacy | 1-in-10 | 2025 | S7 | High | WECC/NERC presentation applies the 1-day-in-10-year criterion. |
| BC, AB, SK, QC, Atlantic | not extracted | — | — | Gap | Provincial standards deferred; do not infer them from the national 15%. |

**Reading:** 15% is a national modelling convention bracketed by Ontario (15%)
and Manitoba (12%). It is not a Canadian legal standard and it is not a
provincial adequacy test. The percentages are margins above peak; the model's
`RM` multiplier is `1 + margin`, so 15% is `RM = 1.15` and 12% is `RM = 1.12`.

## 2. Capacity credits / ELCC

| Resource | Reviewed value | Jurisdiction | Source | Confidence | Note |
|---|---:|---|---|---|---|
| Wind | 19.66% | Ontario | S8 | High value / medium label | IESO CAIP table, `MRI` column. The `ELCC` column is 19.25%. |
| Solar | 19.16% | Ontario | S8 | High / medium | `MRI` column; `ELCC` column is 11.33%. |
| Gas | 92.96% | Ontario | S8 | High / medium | `MRI` column; the 2024 APO UCAP value is 94% (S12). |
| Battery, 4h | 10.09% | Ontario | S8 | High / medium | `MRI` column; `ELCC` column is 50.00%. |
| Battery, 8h | 20.33% | Ontario | S8 | High / medium | `MRI` column; no `ELCC` value. |
| Battery, 12h | 26.27% | Ontario | S8 | High / medium | `MRI` column; no `ELCC` value. |
| Wind | 15% | Quebec | S10, S11 | Medium | Hydro-Québec capacity contribution; secondary sources repeat 15%. |
| Nuclear | firm | Canada | — | Low | No ELCC value found. `policy_test_v1` uses 0.90 as a draft firm derate. |
| Hydro | 0.65 | national draft | — | Gap | No Canadian ELCC source; placeholder. |
| Biomass | 0.85 | national draft | — | Gap | No Canadian ELCC source; placeholder. |
| Oil | missing | — | — | Gap | No capacity credit found; oil is not separately credited. |
| Interties | 0 firm | — | — | Policy | No firm import contract; Stage 2 dependency. |
| Storage / DR | not modelled | — | — | Gap | No storage sets or demand-response technology. |

**Column semantics — do not skip.** The requested values are the second
numeric column in the IESO table, labelled `MRI` (S8; repeated in the feedback
form, S9). The first column is `ELCC`. The review uses “capacity credit” as the
umbrella term. Do not describe 19.66% / 19.16% / 10.09% as ELCC without IESO
confirmation; the ELCC values are different (wind 19.25%, solar 11.33%, 4h
battery 50.00%). This is gap 4 below.

## 3. Historical build rates

| Technology | Reviewed rate | Window | Source | Confidence | Note |
|---|---:|---|---|---|---|
| Wind | ~1.9 GW (2023–24); 2–6 GW/yr forward | 2023–2050 | S13, S14, S15 | Medium | 1.9 GW is the review figure. CanREA/IEA report 1.4–1.7 GW/yr in 2023–24, so the window or scope differs. The 2–6 GW/yr range is the CER EF2026 scenario envelope, not a historical maximum. |
| Solar | 0.22–0.36 GW/yr observed; 0.2–1.0 review envelope | 2022–2050 | S15, S16 | Medium | 2024: 314 MW; 2023: about 360 MW. The upper end of the review envelope is a scenario value, not an observed rate. |
| Gas | +2.02 GW | 2023–24 | S17 | Low | Review figure from the CER EF2026 capacity series; exact year window not extracted. |
| Nuclear | 0 net | 2023–24 | S17 | Medium | Refurbishments, no new units; net capacity change about zero. |
| Hydro | no rate | — | — | Gap | No build-rate series extracted. |
| Bio / geothermal | no rate | — | — | Gap | No build-rate series extracted. |
| Lead times | missing | — | — | Gap | No commissioning lag, queue, or manufacturing constraint data. |

**Reading:** only solar has a clean annual observed range. Wind has a
forward-looking scenario range, not a historical maximum. Gas and nuclear are
observed net changes, not build-rate caps.

## 4. 2021 provincial peaks and the peak factor

| Province | 2021 peak (MW) | Date | Source | Confidence | Note |
|---|---:|---|---|---|---|
| Ontario | 22,986 | 2021-08-24 | S18 | High | 133.84 TWh in 2021 → 15,279 MW average → peak/average 1.50. |
| Alberta | 11,729 | 2021-02-09 | S19 | High | Average AIL 9,728 MW → peak/average 1.21. |
| British Columbia | 10,787 | 2021-12-27 | S20 | High | All-time winter record at the time. |
| Quebec | 40,537 | 2022-01-11 | S21 | High | Winter 2021–22 peak, not calendar 2021. |
| Manitoba | 4,915 | 2021-02-11 | S22 | Medium | Record winter peak; news report, not a utility filing. |
| Sum of five | 90,954 | non-coincident | derived | Medium | Five provinces only; not a national coincident peak. |
| Canada generation | 625.7 TWh | 2021 | S23 | High | 71.4 GW average. |

**Peak-factor implication.** The former `peak_factor = 1.55` is withdrawn.
Against 625.7 TWh of 2021 generation (71.4 GW average, gross-generation
basis), 1.55 implies a national peak of about 110.7 GW. The five-province
non-coincident sum is already only 1.27 times the national average load, and a
national coincident peak would be lower. An average-demand denominator would
be smaller by T&D losses and would raise the factor, but not to 1.55. There is
no support for 1.55.

Provincial factors also differ materially: Ontario is about 1.50, Alberta about
1.21, and Manitoba about 1.44 on a generation basis. A single national factor
hides winter-peaking Quebec/BC/Manitoba and summer-peaking Ontario/Alberta.
A defensible, coincidence-adjusted dry-run bound is **1.20–1.30**, not a point
value. A policy-grade set needs a sourced national coincident peak and
provincial annual demand.

## 5. Interties

| Link | Reviewed capability (MW) | Direction | Source | Confidence | Note |
|---|---:|---|---|---|---|
| Ontario–Quebec | 2,700 | Quebec → Ontario | S24, S25, S26 | Medium | IESO 2024 table: QC→ON 2,730; ON→QC 2,135. The 2,700 value is a rounded import figure; Ontario's 2024 release gives about 2,775 MW combined. |
| Ontario–New York | 2,150 | Ontario → New York at Niagara | S26 | High | Same table: ON→NY Niagara 2,150; NY→ON Niagara 1,835; St. Lawrence 435/475. |
| Ontario–Michigan | >3,010 | bidirectional | S26 | Low | IESO 2024 table: ON→MI 1,500; MI→ON 1,650. 3,010 is not a single interface rating; unresolved. |
| Ontario–Manitoba | 250 | Ontario → Manitoba | S26 | High | Capability can be reduced in some operating conditions. |
| BC–Alberta | 1,200 / 1,000 | BC→AB / AB→BC | S27 | High | Asymmetric path rating. |
| BC–US | 3,150 / 2,000 | BC→US / US→BC | S28 | High | Asymmetric path rating; 3,150 export, 2,000 import. |

**Direction matters.** These are directional transfer capabilities, not a
single symmetric rating. `policy_test_v1` cannot express asymmetry today
(§3.1), so the values are evidence for Stage 2, not parameters for the current
symmetric `TRN` links.

## 6. Conflicts and reconciliation

| Item | Reviewed value | Conflicting source value | Disposition |
|---|---|---|---|
| National peak factor | 1.55 (old placeholder) | 1.27 upper bound from five peaks and 625.7 TWh | Withdraw 1.55; use 1.20–1.30 only as a dry-run bound. |
| IESO credit column | 19.66 / 19.16 / 10.09 | ELCC column: 19.25 / 11.33 / 50.00 | Label as `MRI`; do not call ELCC until confirmed. |
| Ontario–Michigan | >3,010 | IESO 2024: 1,500 / 1,650 | Unresolved; do not use in a cap. |
| Ontario–Quebec | 2,700 | IESO 2024: 2,730 / 2,135 | Use 2,700 as a rounded import figure; direction-specific values for Stage 2. |
| Ontario–New York | 2,150 | IESO 2024: 2,150 / 1,835 plus 435 / 475 | 2,150 is ON→NY at Niagara; do not treat as total. |
| Wind build | 1.9 GW (2023–24) | CanREA/IEA: 1.4–1.7 GW/yr | Window or scope differs; do not use as a maximum. |
| Gas build | +2.02 GW | No independent match | Window unresolved; low confidence. |
| Quebec peak | 2021 peak | Recorded 2022-01-11 (winter 2021–22) | Label as 2021/22 winter peak. |

## 7. Explicit gaps

1. **Source signoff.** URLs only; no archived payloads, hashes, licence review,
   or named reviewer. `source_status` must remain `pending`.
2. **National peak factor.** No national coincident peak and no provincial
   annual demand series. The 1.55 placeholder is withdrawn; the dry-run bound
   is 1.20–1.30.
3. **Provincial reserve standards.** BC, Alberta, Saskatchewan, Quebec, and
   Atlantic standards were not extracted. The 15% value is national only.
4. **IESO column semantics.** `MRI` versus `ELCC` is unresolved. The requested
   values are the `MRI` column.
5. **Hydro, biomass, nuclear, oil.** No Canadian ELCC source for 0.65 / 0.85 /
   0.90 / oil. These are drafts, not sourced credits.
6. **Storage and demand response.** The 4h / 8h / 12h credits cannot be used
   until storage sets exist. No DR technology.
7. **Build lead times.** No commissioning lag, queue, or manufacturing data.
   The model can build at the annual cap in the same year.
8. **Transmission.** ON–MI is unresolved; ratings are directional; the model
   is symmetric and has no simultaneous import/export or network constraint.
9. **Curtailment and timeslices.** No hourly VRE, peak, or curtailment data;
   the reserve margin remains an annual proxy.
10. **Post-2050 and licence.** No sourced extrapolation rule after 2050 and no
    licence review for the cited URLs.

## 8. Source register

All URLs accessed 2026-11-20. Source types are noted; none is archived.

| ID | Source | URL | Type |
|---|---|---|---|
| S1 | NERC, BAL-502-RF-03, Planning Resource Adequacy Analysis | https://nerc.com/globalassets/standards/reliability-standards/bal/bal-502-rf-03.pdf | Standard |
| S2 | NERC, 2024 Long-Term Reliability Assessment | https://www.nerc.com/globalassets/our-work/assessments/2024-ltra_corrected_july_2025.pdf | Assessment |
| S3 | IESO, Ontario Reserve Margin Requirements 2024–2028 | https://ieso.ca/-/media/Files/IESO/Document-Library/planning-forecasts/reliability-outlook/2023-ORMR-2024-2028.ashx | Reliability outlook |
| S4 | Manitoba Hydro, IRP 2023 Appendix 4, Analysis Approach | https://www.hydro.mb.ca/docs/corporate/irp/irp-2023-a4-analysis-approach.pdf | IRP appendix |
| S5 | MISO, Resource Adequacy and Capacity Planning | https://misoenergy.org/planning/resource-adequacy2/resource-adequacy | Planning page |
| S6 | NERC/WECC, BAL-002-WECC-3, Contingency Reserve | https://nerc.com/globalassets/standards/reliability-standards/bal/bal-002-wecc-3.pdf | Standard |
| S7 | WECC/NERC, Resource Adequacy presentation, September 2025 | https://utc.wa.gov/sites/default/files/2025-09/WECC%20NERC%20September%2022%20RA%20Presentation.pdf | Presentation |
| S8 | IESO, Capacity Accreditation for IESO, 2026-10-22 | https://www.ieso.ca/-/media/Files/IESO/Document-Library/engage/CAIP/CAI-20261022-Presentation.pdf | Presentation |
| S9 | IESO, Capacity Accreditation feedback form, 2026-10-30 | https://www.ieso.ca/-/media/Files/IESO/Document-Library/engage/CAIP/CAI-20261030-feedback-form-ORA.pdf | Feedback form |
| S10 | Hydro-Québec, Action Plan 2035 | https://www.hydroquebec.com/about/publications-reports/action-plan-2035.html | Plan |
| S11 | Hydro-Québec, Overview of Energy Resources, 2027–2041 supply plan | https://hydroquebec.com/data/achats-electricite-quebec/pdf/overview-of-hydro-quebec-energy-resources-highlights-of-the-electricity-supply-plan-2027-2041.pdf | Supply plan |
| S12 | IESO, 2024 Annual Planning Outlook: Resource Costs and Trends | https://ieso.ca/-/media/Files/IESO/Document-Library/planning-forecasts/apo/Mar2024/Resource-Costs-and-Trends.pdf | APO module |
| S13 | CER, Canada's Energy Future 2026: Executive Summary | https://cer-rec.gc.ca/en/data-analysis/canada-energy-future/2026/executive-summary | Outlook |
| S14 | CER, Wind power to drive Canada's renewable energy growth through 2030 | https://www.cer-rec.gc.ca/en/about/news-room/news-releases/2025/wind-power-drive-canada-renewable-energy-growth-through-2030.html | News release |
| S15 | CanREA, 2023 wind, solar and storage data | https://renewablesassociation.ca/news-release-new-2023-data-shows-11-2-growth-for-wind-solar-energy-storage | Industry release |
| S16 | pv magazine, Canada deploys 314 MW of solar in 2024 | https://pv-magazine.com/2025/03/10/canada-deploys-314-mw-of-solar-in-2024 | Trade press |
| S17 | CER, Canada's Energy Future 2026: Results | https://cer-rec.gc.ca/en/data-analysis/canada-energy-future/2026/results | Outlook |
| S18 | IESO, 2021 Year-End Data | https://www.ieso.ca/en/Corporate-IESO/Media/Year-End-Data/2021 | System data |
| S19 | AESO, 2021 Annual Market Statistics | https://aeso.ca/assets/Uploads/market-and-system-reporting/2021-Annual-Market-Stats-Final.pdf | Market report |
| S20 | BC Hydro, 2021 Demand Report | https://bchydro.com/content/dam/BCHydro/customer-portal/documents/news-and-features/2021-demand-report.pdf | Utility report |
| S21 | Hydro-Québec, Form 18-K 2021 | https://hydroquebec.com/relations-investisseurs/pdf/18K-2021.pdf | Regulatory filing |
| S22 | CTV News, Manitoba breaks electricity-use record | https://www.ctvnews.ca/winnipeg/article/manitoba-breaks-electricity-use-record-amid-cold-snap/ | News |
| S23 | CER, Canada energy profile | https://www.cer-rec.gc.ca/en/data-analysis/energy-markets/province-territory-energy-profiles/canada.html | Profile |
| S24 | IESO, Ontario–Quebec Interconnection Capability: A Technical Review | https://ieso.ca/-/media/files/ieso/document-library/power-data/supply/intertiereport-20170508.pdf | Technical review |
| S25 | Ontario, Governments of Ontario and Quebec Support New Electricity Trade Agreement | https://news.ontario.ca/en/release/1003444/the-governments-of-ontario-and-quebec-support-new-electricity-trade-agreement | News release |
| S26 | IESO, 2024 APO: Ontario's Transmission Interfaces and Interties | https://ieso.ca/-/media/Files/IESO/Document-Library/planning-forecasts/apo/Mar2024/Ontario-Transmission-Interfaces-and-Interties-Overview.pdf | APO module |
| S27 | BC Hydro, BC–Alberta Intertie Workshop, January 2025 | https://bchydro.com/content/dam/BCHydro/customer-portal/documents/corporate/regulatory-planning-documents/regulatory-matters/bc-alberta-intertie-workshop-presentation.pdf | Presentation |
| S28 | BC Hydro, TTC/ATC bulletin, February 2011 | https://www.bchydro.com/content/dam/BCHydro/customer-portal/documents/corporate/suppliers/transmission-scheduling/bulletins/2010/TTCATCFebruary12011.pdf | Transmission bulletin |
| S29 | ReliabilityFirst, 2025 Summer Reliability Assessment | https://rfirst.org/resource-center/reliabilityfirst-2025-summer-reliability-assessment | Assessment |
