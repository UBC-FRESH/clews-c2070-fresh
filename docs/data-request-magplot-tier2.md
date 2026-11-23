# Data request — MAGPlot Tier 2/3 and NFI ground plots (AB, SK, MB, ON)

| Field | Value |
|---|---|
| Status | **Draft for PI review; not submitted** |
| Prepared | 2026-11-23 |
| Request route | NFI Data Request Form, https://nfi.nfis.org/en/datarequestform |
| Data types | MAGPlot restricted Tier 2/3; NFI ground plots |
| Target jurisdictions | Alberta, Saskatchewan, Manitoba, Ontario |
| Licence / agreement | Contributor terms for MAGPlot restricted tiers; NRCan Data Use Agreement for NFI ground plots |
| Raw-data handling | Access-controlled project storage only; no raw plot records in the parent repository |
| Companion handoff | `docs/handoff-national-facility-chain-v2.md` |

This is a draft request, not a submitted request. No access has been granted.
No raw MAGPlot or NFI plot records are in the parent repository. The open
MAGPlot v1.2 Tier 1 package and its WFS supplement are already held under
`data/raw/magplot/` and need not be resent.

## 1. Request summary

We request the least restrictive available MAGPlot Tier 2/3 records and NFI
ground-plot records for Alberta, Saskatchewan, Manitoba, and Ontario, with all
available measurement cycles and the fields listed in §4. The purpose is to
replace the BC aggregate donor coefficients for these four jurisdictions in the
national facility chain with province-specific measured coefficients, and to
quantify how much the national result depends on the donor assumption.

The request is for non-commercial research and model calibration/validation
only. It is not a request for operational timber supply, mill capacity, or
policy evaluation data.

## 2. Why these four jurisdictions

The national facility chain covers 13 jurisdictions. BC, QC, NB, and YT use
measured four-province bucking-pilot coefficients; the other nine use the BC
aggregate donor. The open MAGPlot v1.2 package does not close that gap:

| Jurisdiction | Open MAGPlot v1.2 coverage | Gap for the national chain |
|---|---|---|
| AB | No sites | No province-specific product-share or residue coefficient |
| SK | 171 sites in the ZIP package; WFS supplement adds 2,048 sites and tree layers spanning 1949–2019 | ZIP records are single-visit NFM temporary plots; WFS records add tree measurements but no age or site index; exact coordinates and restricted records remain outstanding |
| MB | 2 sites | Single-visit NFM temporary plots; no usable province sample |
| ON | No sites | No province-specific product-share or residue coefficient |

The four priority jurisdictions are a material donor block in the 119 Mm³
case:

| Donor province | Process residue PJ | Harvest gross PJ |
|---|---:|---:|
| AB | 101.204 | 32.281 |
| SK | 19.208 | 6.127 |
| MB | 5.019 | 1.601 |
| ON | 59.656 | 19.028 |
| **Four-province total** | **185.087** | **59.037** |
| Share of national 119 Mm³ | 36.0% of 514.614 | 18.8% of 313.640 |

These are model estimates computed with donor coefficients. They show why donor
uncertainty in these four provinces is material; they are not observations and
are not a reason to treat the donor as correct.

## 3. Requested scope

### 3.1 MAGPlot Tier 2/3

- Records for AB, SK, MB, and ON from the restricted MAGPlot tiers.
- All available measurement cycles for each selected site, not only the latest.
- Tree-level records and the associated site, plot-header, treatment,
  disturbance, and removed-tree tables.
- Exact coordinates where the contributor permits; otherwise the finest
  location and an explicit coordinate-quality flag.
- Source program and contributor attribution for every site.
- If the custodian's tier labels differ from Tier 2/3, provide the least
  restrictive tier that contains the requested fields.

The open Tier 1 package already contains BC, QC, NB, NL, YT, NT, and the limited
SK/MB records. Do not resend Tier 1. The request is for the restricted records
that are absent from the open release, and for the SK/MB records with the
additional fields needed for growth and residue work.

### 3.2 NFI ground plots

- NFI ground plots in AB, SK, MB, and ON, all available cycles.
- Tree-level records with persistent plot and tree identifiers.
- Site index, stand age, ecosite/ecozone, soil, drainage, slope, and aspect
  where available.
- Plot design (fixed area or BAF/radius), expansion fields, and measurement
  dates.
- Exact coordinates for public-land plots, subject to NFI approval and the
  exact-coordinate justification in §6.
- Disturbance and treatment history, ownership/land status, and jurisdiction
  identifiers.

NFI ground plots are the national reference with site index and age; MAGPlot
restricted records provide the harmonized provincial tree lists. The two are
complementary, not substitutes.

### 3.3 Not requested

- Photo-plot interpretation data, unless needed later for stratification.
- CASFRI vector polygons or inventory attributes; CASFRI is an initial-condition
  source, not a tree-level calibration source.
- Mill production, capacity, or shipment records.
- Any data that the contributor does not permit to be used for model
  calibration.

## 4. Fields needed

### 4.1 Identifiers and provenance

| Field | Why needed |
|---|---|
| `magp_site_id` | Persistent site key across tables |
| `plot_id`, `plot_type_id` | Persistent plot key; plot type distinguishes design |
| `tree_id`, `tree_id_meas`, `tree_num` | Tree identity across remeasurement; tracked versus untracked trees |
| `meas_num` | Measurement cycle |
| `source_plot`, `sources` | Contributor/source-program attribution |
| `province` | Jurisdiction assignment |
| `gpp_type`, `flag_sites` | Ground-plot program and QA flags |

### 4.2 Location and site

| Field | Why needed |
|---|---|
| `latitude`, `longitude` | Province assignment and, where permitted, SCANFI pixel linkage |
| Coordinate type / quality flag | Distinguish exact, fuzzed, and approximate locations |
| `elevation` | Site covariate |
| `aspect_class`, `slope_class`, `slope_position` | Site covariates for stratification and taper/allometry domain checks |
| Ownership / land status | Public/private distinction and coordinate-release conditions |
| Ecozone / ecodistrict / ecosite | Stratification and transfer of taper/allometry rules |

### 4.3 Tree measurements

| Field | Why needed |
|---|---|
| `species_gsv` (and genus/species/variety) | Species-specific taper and biomass equations; crosswalk to the project species list |
| `dbh` or `meas_est_dbh` (cm) | Stem size; taper and CFS biomass equations |
| `height` or `meas_est_height` (m) | Taper, volume, and CFS branch/foliage equations |
| `tree_status` | Live standing, dead standing, fallen, missing; restrict to live standing for the measured-stem coefficient |
| `stem_cond` | Condition codes that may disqualify a stem |
| `crown_class` | Competition and stand-structure covariate |
| `stem_ha` | Expansion to per-hectare values; must be usable or reconstructable |
| `tree_removed.reason` | Mortality, harvest, or loss between cycles |
| `meas_plot_size`, `taglimit_dbh`, `taglimit_height` | Plot design and tree inclusion rules |
| BAF, plot radius, or expansion factor | Required where the plot is variable-radius; if absent, flag the plot as non-expandable |

For the bucking and total-residue work, the minimum tree-level set is species,
DBH, height, live/dead status, and a usable expansion factor. If measured stem
sections, top diameters, or bucking records exist, include them; otherwise the
project will apply its documented taper equations and report the resulting
domain limits. Do not substitute a modelled height for a missing measured height
without an explicit flag.

### 4.4 Stand, treatment, and disturbance

| Field | Why needed |
|---|---|
| Stand age / total age | Yield and stand-development context |
| Site index and base age | Productivity covariate |
| Soil texture, drainage | Site covariates |
| `treatment_type`, `treatment_year`, `treatment_pct` | Harvest, thinning, planting, and site preparation history |
| `disturbance_type`, `disturbance_year`, `disturbance_pct`, `mortality_pct` | Fire, insect, wind, and harvest disturbance history |
| Measurement year/month or full date | Temporal alignment; growth intervals |

### 4.5 NFI equivalents

Where the NFI schema differs from MAGPlot, provide the equivalent fields:

| Need | NFI-style field examples |
|---|---|
| Species | `SPECIES` / `TREE_SPECIES` |
| DBH | `DBH` / `TREE_DBH` |
| Height | `HEIGHT` / `TREE_HEIGHT` |
| Age | `AGE` / `STAND_AGE` |
| Site index | `SITE_INDEX` / `SI` |
| Plot design | `BAF` / `PLOT_RADIUS` / fixed plot area |
| Plot identity | `PLOT_ID` / `NFI_PLOT_ID` |
| Location | `LATITUDE`, `LONGITUDE` (NAD83) |
| Status | `TREE_STATUS` |
| Crown class | `TREE_CROWN_CLASS` |
| Expansion | `STEMS_PER_HA` / expansion factor |
| Disturbance / treatment | Disturbance and treatment history fields |

A data dictionary mapping the delivered field names to these concepts is
required.

## 5. Purpose and use

The requested records will be used to:

1. Compute province-specific solid (sawlog), pulp (pulpwood), other-roundwood,
   and gross harvest-residue coefficients for AB, SK, MB, and ON, following the
   measured four-province bucking-pilot method.
2. Compute gross total residue (stem taper plus live branch and foliage) from
   the CFS tree-level equations and report the 0.35/0.60/0.75 recovery range.
3. Replace the BC donor coefficients for these four jurisdictions in a
   diagnostic national chain run and compare the result with the donor case.
4. Quantify the donor bias by province and by national total, and report
   taper-coverage and species-domain limits.
5. Validate the national chain against independent plot information where the
   plot can be linked to the chain's spatial units.

The work is diagnostic. It will not produce a legal AAC, a sustainable harvest
level, a mill operating plan, a facility investment case, or an official OSeCAN
scenario.

## 6. Licence, DUA, and data handling

### 6.1 MAGPlot

- The open MAGPlot v1.2 Tier 1 package is published under the Open Government
  Licence – Canada and is already archived. That licence does not automatically
  extend to restricted Tier 2/3 records.
- Restricted MAGPlot tiers are governed by contributor data-sharing conditions.
  We request the applicable terms in writing before transfer and will accept a
  contributor-specific data-sharing agreement or DUA where required.
- We will not redistribute restricted records. Derived aggregate tables and
  model coefficients may be published only if the contributor's terms permit
  it; otherwise only aggregate statistics with no reconstructable plot record
  will be released.
- Exact coordinates will be used only by the named project team, will not be
  published, and will not be used to visit sites.

### 6.2 NFI ground plots

- NFI ground-plot access requires the NFI Data Request Form and a signed Data
  Use Agreement with NRCan.
- The request is non-commercial research use. Raw NFI plot data will not be
  redistributed. Aggregated or derived products will be released only with NFI
  approval and attribution.
- We accept the NFI data-use terms, including use solely for this project and a
  new request before any different use.
- Exact coordinates may delay release. The justification is that SCANFI v2 is a
  30 m product; a ±10 km displacement spans roughly 400 km² and cannot identify
  the pixel a plot represents. Exact centres are needed to extract the matching
  SCANFI pixel, calibrate site index, and check whether a plot contributed to
  SCANFI training so validation remains independent.
- We will not visit plots, will not share coordinates outside the named team,
  and will not publish coordinates or any record from which they could be
  reconstructed.
- Retention and destruction will follow the signed agreement or project
  records schedule, whichever is sooner.

### 6.3 Provincial contributors

AB, SK, MB, and ON records may carry provincial contributor terms in addition to
the federal terms. We will accept per-jurisdiction conditions, including
coordinate restrictions, sensitive-site exclusions, and publication review. We
will not assume that an open provincial licence applies to a restricted MAGPlot
record.

### 6.4 Storage boundary

Raw records will be stored in access-controlled project storage outside the
parent git repository, consistent with the repository's data boundary. The
parent repository will contain only derived aggregate tables, a manifest, and
documentation. No raw plot records, exact coordinates, or contributor-restricted
attributes will be committed to the parent repository.

## 7. Delivery format

| Item | Specification |
|---|---|
| Primary tables | CSV or Parquet, one row per tree measurement and one row per plot visit |
| Site/plot tables | CSV or Parquet keyed by `magp_site_id` / `plot_id` |
| Spatial | Optional GeoPackage or CSV with plot centroids; coordinates in NAD83 (EPSG:4269) or WGS84 (EPSG:4326), stated explicitly |
| Metadata | Data dictionary mapping delivered fields to the concepts in §4 |
| Encoding | UTF-8; ISO 8601 dates (YYYY-MM-DD) |
| Provenance | Contributor/source program, version/date, coordinate-quality flag, and any QA flags |
| Cycles | All available measurement cycles for each selected site |

## 8. Volume and selection

No plot-count estimate is asserted in this draft. The open ZIP package contains
171 SK sites and 2 MB sites and no AB or ON sites; the WFS supplement adds 2,048
SK sites and 691 PE sites. The restricted population is known only to the
custodians. We request all available records that meet the scope, with the
custodian's recommended selection if a full extract is impractical. If a subset
is necessary, prioritize:

1. sites with at least two measurement cycles;
2. live standing trees with species, DBH, height, and a usable expansion
   factor;
3. public-land sites with the finest available coordinates;
4. sites that span the province's main forest ecozones and species groups.

## 9. Submission block (placeholders)

| Field | Value |
|---|---|
| Requestor | Dr. Gregory Paradis, Assistant Professor, Forest Resources Management, University of British Columbia |
| Project | CLEWs-C2070 — Innovative Integration of Climate, Land, and Water into Energy Models for Canada's Net-Zero Commitments |
| Funder | Natural Resources Canada, Emerging Innovators Program (EIP-NES-147); contract held by Simon Fraser University; UBC FRESH is a sub-grantee |
| Project lead (non-correspondence) | Dr. Taco Niet, Simon Fraser University |
| Email / phone | `[PI TO FILL]` |
| Project start / end | `[PI TO FILL]` |
| Data types | Ground Plot; MAGPlot |
| Study area | AB, SK, MB, ON; forested ecozones within those provinces |
| Exact coordinates | Yes, where the contributor permits `[PI TO CONFIRM]` |
| Collaborators | `[PI TO FILL: optional; suggested Taco Niet with consent]` |
| Signature / date | `[PI TO FILL]` |

This block is a draft. The PI must confirm the project dates, contact details,
collaborators, and exact-coordinate request before submission.

## 10. Open questions to confirm with the custodians

1. Which MAGPlot tier contains the AB, SK, MB, and ON records with tree-level
   measurements, and what terms apply to each tier?
2. Are exact coordinates available for any of these records, and under what
   conditions?
3. Do the restricted MAGPlot records include age and site index, or must those
   come from NFI ground plots or provincial sources?
4. Does the NFI ground-plot extract include plots in all four provinces, and
   which cycles are available?
5. Are there separate provincial agreements required in addition to the NFI
   DUA?
6. What publication review or aggregate-disclosure rules apply to derived
   coefficients?

## 11. Non-claims

This draft does not claim that:

- access will be granted, or that any tier contains the requested records;
- the open MAGPlot package is sufficient for AB, SK, MB, or ON;
- restricted MAGPlot records are covered by the Open Government Licence –
  Canada;
- NFI ground plots can be obtained without a DUA;
- the requested data will produce a policy-grade, legal, or operational result;
- any raw record, coordinate, or restricted attribute may be redistributed;
- the national facility chain is production-ready.

No request has been sent. No data have been received. No official OSeCAN, UI,
or model change is associated with this draft.

## 12. References

- `docs/handoff-national-facility-chain-v2.md` — chain handoff and donor
  coverage.
- `docs/model-card-national-facility-chain.md` — frozen chain method card.
- `metadata/source-reviews/magplot.md` — MAGPlot source review and access tiers.
- `metadata/source-reviews/nfi_ground_plot_data.md` — NFI ground-plot access
  posture.
- `metadata/license-determinations/nfi-access.yaml` — licence/DUA registry.
- `metadata/license-determinations/requests/nfi-ground-photo-request.md` —
  existing broader NFI request draft.
- `data/raw/magplot/MAGPlot_CONTENTS.md` — open-package table inventory and
  coverage.
- `data/raw/magplot/wfs/ACQUISITION.md` — WFS supplement coverage.
