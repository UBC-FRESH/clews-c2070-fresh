# National transformed-curve sustainable supply WS4 v1 — candidate release

**Release tag:** `national-transformed-supply-ws4-v1` (annotated)
**Status:** candidate transformed-curve sustainable supply release
**Evidence labels:** `model_estimate=true`, `observed=false`,
`policy_grade=false`, `national_claim=false`
**Boundary:** not official OSeCAN; frozen chain v5.1 unchanged; no model,
data, UI, chain v5.1, or official OSeCAN change at the documentation/tag
commit

This release consolidates the national RSYC transform v1 curve library, the
transformed national 990 m WS4 dataset, the transformed sustainable 100/119/150
Mm³/yr cases, and the transformed `screen_core30` operable screen into one
tagged evidence pointer. It is a documentation/tag release only. It changes no
model artifact, dataset, UI surface, frozen chain v5.1 artifact, or official
OSeCAN file. The schedules are model estimates, not AACs, policy supply,
tenure plans, or investment cases.

The release is deliberately a shape-repair release, not a new yield model. The
transformed curves preserve RSYC volume at age 100 and replace the implausible
raw curve shape with documented BC natural and QC managed donor shapes. The
full-forest sustainable cases test volume arithmetic on the whole national
estate; the operable cases apply the existing share-based landbase proxy. None
of these results is an official OSeCAN or CLEWS-C2070 supply claim.

## 1. Status and adoption boundary

| Field | Value |
|---|---|
| Release | `national-transformed-supply-ws4-v1` |
| Release class | Documentation/tag only |
| Supply status | Candidate; full-forest sustainable, operable not sustained |
| Curve status | Shape-repaired RSYC total-volume library; V100 preserved |
| Core labels | `model_estimate=true`, `observed=false`, `policy_grade=false`, `national_claim=false` |
| WS4 labels | `ws4_derived=true`, `sustainable_schedule=true`, `transformed_curves=true` |
| Operable labels | `share_based_operable_proxy=true`, `legal_thlb=false`, `slope_screen=false` |
| Official OSeCAN | Unchanged; `official_osecan_changed=false`; 146 files, `80caa91c...` |
| Frozen chain v5.1 | Unchanged; `chain_v51_unchanged=true`; tag `national-facility-chain-v5.1` stays at `546a3d5` |
| Adoption authority | Not official OSeCAN; adoption remains with the Taco/energy team |
| National claim | None; no AAC, policy supply, legal THLB, or investment claim |

The release builds on, but does not move, the earlier
`national-facility-chain-ws4-v1`, `national-forest-estate-ws4-v1.1`, and
`national-sustainable-supply-ws4-v1` tags. The transformed cases are a
successor sensitivity on the same national 990 m estate, not a replacement
release for those tags.

## 2. Why the curves were rebuilt

The raw RSYC tile group curves and the BC VDYP untreated composites are not
similar at the shape level. A direct 1:1 comparison of raw RSYC against BC
VDYP gives a median R² of **−0.41**: the raw curve is worse than a constant
mean prediction. A shape-only 1:1 alignment improves R² only to **0.42**.
The CMAI age medians are **6 vs 122 years**, and the peak-age medians are
**131 vs 204 years**. The fitted transform, not the raw RSYC shape, therefore
does most of the work.

| Comparison | Raw RSYC vs BC VDYP |
|---|---:|
| Median raw 1:1 R² | −0.41 |
| Median shape 1:1 R² | 0.42 |
| Median CMAI age | 6 vs 122 years |
| Median peak age | 131 vs 204 years |

The diagnosis is that RSYC is a national total-volume backbone rescaled by
ecoprovince, not a local yield curve. Its raw b4 coefficient can produce
implausible early or late behaviour. The transform repairs shape while
retaining the published RSYC level at age 100, so the national estate keeps
its original volume anchor and does not inherit a donor's merchantability
ratio.

## 3. BC donor transform

The BC donor transform is a five-parameter scale/shift/truncation/toe/alpha
family applied as `V(x) = k * RSYC((x - s) / alpha)`, with a ramp toe below
the join and the vertical `k` set per curve to preserve the original RSYC
volume at age 100 (or the original peak when V100 is not meaningful). Donor
`k` values are never used as merchantability ratios.

BC donor classes were fitted to dominant-TSA VDYP untreated composites from
the fresh TSA portal and supplemental bundles. The primary fit window is
10–150 years; the 1–250 sensitivity fit is used when the primary alpha
reaches its upper bound.

| Check | Result |
|---|---|
| BC tile donors | 65 / 65 fitted |
| Primary R² median | 0.998 |
| Primary R² minimum | 0.9977 |
| Poor fits (R² < 0.90) | 0 |
| Applied 10–150 / 1–250 window | 41 / 24 |
| CMAI agreement with VDYP | within 1–4% |

The 5-parameter BC form transfers natural-curve shape well. It does not
transfer directly to managed AAC curves because managed curves need an
explicit post-peak decline or plateau rather than a natural rise to age 200.

## 4. QC donor transform

The BC form was tested on the QC BFEC managed AAC curves and did not transfer:
managed curves require a post-peak decline/plateau term. The QC donor form
therefore adds a selected linear, power, or stretched-exponential decline after
the donor-basis peak, with selection ordered linear → power → stretched.

| Check | Result |
|---|---|
| QC unit donors | 51 / 51 fitted |
| Selected full-curve R² | 0.960–0.999 |
| Selected R² median | 0.9974 |
| Poor fits (R² < 0.90) | 0 |
| Selected decline: linear / power / stretched | 42 / 4 / 5 |
| QC composite, linear | 0.9905 |
| QC composite, power | 0.9978 |
| QC composite, stretched | 0.9994 |

The composite fits use the 51 BFEC unit composites, the province composite,
and six representative curves. The selected decline kind is per donor class,
not per target RSYC curve. The resulting managed curves flatten near the fitted
flat age and do not rise to age 200 even when the underlying RSYC b4
coefficient is negative.

## 5. National transformed library

The national library contains 3,948 transformed curves, 789,600 rows, 13
provinces, 12 species values, and 34 donor classes. Every curve is usable;
`flag` records provenance, chiefly a distant donor, and is not a rejection.

| Quantity | Value |
|---|---:|
| Transformed curves | 3,948 |
| Rows | 789,600 |
| Provinces / species | 13 / 12 |
| Donor classes used | 34 |
| Usable curves | 3,948 / 3,948 (100%) |
| QC pass / flag / fail | 2,383 / 1,565 / 0 |
| Flag reason | `distant_donor` (1,565) |
| Raw 2-year and 24-year CMAI artifacts removed | 2,592 |
| Age-2 / age-24 CMAI curves after transform | 0 / 0 |
| V100 preservation | per curve, original age-100 volume (or peak when V100 not meaningful) |

The raw CMAI age counts were 2,250 curves at age 2 and 342 at age 24, for
2,592 curves whose 2-/24-year artifact was removed. The transformed CMAI age
distribution has no 2- or 24-year modal spike. Donor distance is recorded for
every curve; distant donors are flagged, not dropped.

National transformed CMAI medians by donor style and broad group:

| Donor style | Broad group | Curves | Median CMAI (m³/ha/yr) | Median CMAI age (yr) |
|---|---|---:|---:|---:|
| `managed_extended` | conifer | 1,668 | 7.03 | 13 |
| `managed_extended` | deciduous | 258 | 5.63 | 18 |
| `natural_5param` | conifer | 1,725 | 0.88 | 111 |
| `natural_5param` | deciduous | 297 | 1.10 | 119 |

Managed donor classes are matched by species mix for QC and the eastern
provinces; BC natural classes are matched by ecoprovince, then ecozone, then
species group. YT/NT/NU use BC natural classes. AB/SK/MB use QC managed classes
in Boreal Plains and Boreal Shield ecozones and BC natural classes elsewhere.
These are documented donor/proxy rules, not province-specific fits.

## 6. Transformed WS4 dataset

The original 12-table national 990 m dataset was copied to
`data/derived/ws4/scanfi-national-990m-transformed/` and left untouched.
`CURVE(<model_id>)` was parsed from `tracks.name`, and
`feature.Yield.<province>.<state>.Total` mapped each track to its WS4
`curve_id`.

| Check | Result |
|---|---|
| WS4 curves mapped | 718 / 718 referenced (curve 1 unreferenced) |
| RSYC models mapped | 718 |
| Transformed cell curves aggregated | 3,948 |
| Tracks mapped | 2,799 |
| Curve-to-model conflicts / missing models | 0 / 0 |
| V100 maximum absolute error | 0.000500 m³/ha (tolerance 0.01) |
| Negative or NaN curve values | 0 |
| `ws4 validate` | `Dataset is valid` |
| Original dataset hashes | unchanged |

A WS4 model can represent several transformed cell curves because the donor
class depends on province and species. The model-level curve is the
forest-area-weighted mean of the transformed cell curves. Curve 1 is
unreferenced and unchanged. Ages are 0 = 0, ages 1–200 = transformed volume,
and ages 201–600 = the age-200 value held flat; the hold is outside the
transformed fit and is not a forecast.

## 7. Sustainable 100/119/150 Mm³/yr cases

The scheduler is unchanged: 100 years in ten 10-year periods, whole eligible
blocks selected oldest-first and volume-aware against the frozen provincial
shares, annual longest-processing-time spread, rotation from the maximum MAI
capped to 40–200 years, no under-rotation harvest, no block split or scaling.
The WS4 run is the authority for harvested volume and treated area. Annual
volumes below are Mm³/yr.

| Case | Annual min | Annual max | Annual mean | Annual CV | Period shortfall (Mm³) | Blocks | Area (Mha) | Mean rotation (yr) | Reharvests |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 Mm³ | 100.2499 | 103.1923 | 101.1221 | 0.008491 | 0 | 212,570 | 61.733 | 53.45 | 0 |
| 119 Mm³ | 119.2540 | 121.7776 | 120.0561 | 0.007310 | 0 | 253,476 | 72.388 | 53.97 | 0 |
| 150 Mm³ | 150.5393 | 153.8005 | 151.6402 | 0.007858 | 0 | 313,862 | 93.151 | 53.75 | 541 |

All three cases meet every 10-year period target with zero period shortfall.
The transformed 100 and 150 Mm³ cases keep every annual offset above the
nominal annual rate. The transformed scheduler selects fewer blocks and
hectares than the original-curve 100/150 cases, and mean rotation rises from
40 to 53.45/53.75 years, because transformed volume per hectare at the
rotation cap is higher.

## 8. Chain versus frozen v5.1

Frozen v5.1 per-province product and residue coefficients are applied to the
transformed harvests. The 119 Mm³ row uses the 100-year `sustainable_119m`
basis scaled to exactly 118,977,339.1 m³/yr. The 100 and 150 Mm³ rows show
first-period / achieved deltas.

| Metric | 119 Mm³ (100-yr) | 100 Mm³ (first / achieved) | 150 Mm³ (first / achieved) |
|---|---:|---:|---:|
| Solid product | −0.335% | +0.514% / +0.704% | +1.309% / +0.780% |
| Pulp product | +0.274% | +0.830% / +1.477% | +3.697% / +1.375% |
| Process residue | −0.030% | +0.681% / +1.088% | +2.493% / +1.074% |
| Harvest gross | +0.321% | +0.919% / +1.501% | +3.520% / +1.277% |
| Total gross | +0.135% | +0.793% / +1.283% | +2.984% / +1.171% |

The differences are frozen coefficients applied to a different provincial
harvest mix and a different volume-per-hectare level, not a coefficient or
threshold change. The 119 Mm³ first-period basis is reported separately in
`docs/ws4-transformed-119m.md`.

## 9. OSeCAN handoff

The handoff uses the frozen v5.1 coefficients, frozen handoff-v10
province-to-zone weights, frozen v9 process availability and harvest recovery,
and the official tier thresholds. It uses the origin-province basis, not a
facility-province reallocation.

| Handoff | Frozen v5.1 reference | 100 Mm³ | 119 Mm³ | 150 Mm³ |
|---|---:|---:|---:|---:|
| Sustainable, primary / achieved | 21/21 | 7/7 / 7/7 | 7/7 / 7/7 | 7/7 / 7/7 |
| Operable, primary / achieved | 21/21 | 6/7 / 7/7 | 7/7 / 6/7 | 7/7 / 6/7 |

The 7/7 checks are the 119-tier positions evaluated with the frozen handoff
inputs. A changed position is a finding from the transformed or operable
harvest totals, not an official OSeCAN change. The official OSeCAN tree is
**146 files**, SHA-256
`80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a`,
unchanged before and after every transformed build.

## 10. Operable screen

`screen_core30` is the existing share-based national operable proxy: SCANFI
median age ≥ 30 years, NBAC 2020 burned pixels excluded, and block centre
within 10 km of a public NRN road. It is not a legal THLB: no national slope
DEM and no spatial road/tenure overlay are applied. The transformed mask is
block-for-block identical to the original-curve mask because only yield curves
changed.

| Quantity | Value |
|---|---:|
| Mask blocks | 370,752 |
| Mask area | 75,374,046 ha |
| Share of WS4 forest | 18.328% of 411,250,630.32 ha |
| Published `screen_core30` area | 77,050,071.64 ha |
| Denominator difference | −2.175% |
| PE / NU operable coverage | none (no 2016 tenure coverage) |

All three operable cases keep their frozen provincial shares, restrict
eligibility to mask blocks at or above the 40–200 year MAI rotation, disable the
cross-province fallback, and run WS4 simulate for 100 years. None is
sustained.

| Case | Annual min (Mm³/yr) | Period shortfall (Mm³) | Province shortfall (Mm³) | Max nominal (Mm³/yr) | Max operable (Mm³/yr) | Sustained |
|---|---:|---:|---:|---:|---:|---|
| 100 Mm³ | 98.2434 | 34.6651 | 67.2620 | 76.0498 | 77.1195 | no |
| 119 Mm³ | 102.6644 | 377.7192 | 457.3300 | 76.2780 | 77.0942 | no |
| 150 Mm³ | 104.4241 | 1,645.1650 | 1,761.3301 | 68.4448 | 70.3013 | no |

Shortfall provinces:

| Case | Shortfall provinces (100-year total, Mm³) |
|---|---|
| 100 Mm³ | PE 25.13, NL 23.94, SK 16.93, NB 1.26 |
| 119 Mm³ | QC 287.32, SK 89.36, NL 45.83, PE 30.22, NS 4.32, NB 0.28 |
| 150 Mm³ | QC 1,022.35, SK 294.99, AB 178.28, NS 118.04, NL 102.36, PE 44.94, NB 0.38 |

Every 10-year period (0–9) contains at least one provincial shortfall in all
three cases. The union is QC, SK, AB, NS, NL, PE, and NB. QC is the binding
province in the 119 and 150 Mm³ cases. No rotation is relaxed and no block is
harvested outside the mask; PE/NU targets are recorded as shortfalls rather
than substituted with harvest.

The maximum sustainable level is a schedule-only diagnostic with the same
provincial shares and mask. It excludes jurisdictions without operable
coverage from the adequacy test and is not a WS4-simulated or policy-tested
level.

| Case | Max nominal (Mm³/yr) | Max operable (Mm³/yr) | Versus original curves: max nominal / max operable | Period shortfall reduction (Mm³) |
|---|---:|---:|---:|---:|
| 100 Mm³ | 76.0498 | 77.1195 | +7.5928 / +7.7757 | 125.7566 |
| 119 Mm³ | 76.2780 | 77.0942 | +8.4237 / +7.8640 | 245.5456 |
| 150 Mm³ | 68.4448 | 70.3013 | +6.9580 / +7.5959 | 129.7174 |

The transformed operable cases therefore improve the schedule-only maximum by
+7.59/+8.42/+6.96 Mm³/yr and reduce period shortfalls by
125.76/245.55/129.72 Mm³ versus the original curves, while still failing the
strict all-province targets.

## 11. Limitations and non-claims

- **Total volume vs merchantable volume.** RSYC is a total-volume curve. VDYP
  and BFEC are merchantable-volume references. The transform repairs shape; it
  does not convert total volume to merchantable volume.
- **Donor/proxy classes.** BC classes are matched by ecoprovince/ecozone/species
  group; QC and eastern classes by species mix. Distant donors are flagged
  (`distant_donor`) but retained. These are not province-specific fitted yield
  curves where no BC/QC reference exists.
- **Shape repair is not a calibrated yield model.** The transform is a donor
  shape transfer with V100 preserved. It is not a species-specific, site-index,
  silviculture, or climate-calibrated yield model.
- **National 990 m only.** The release re-curves the national 990 m estate. The
  full 3,345-tile 30 m coverage is not re-curved in this release.
- **Merchantable QMD/SPH conversion pending.** The merchantable volume, QMD,
  and SPH conversion is not implemented here; see
  `planning/rsyc-merchantable-volume-conversion.md`.
- **Demo schedules are not policy supply.** The full-forest cases use the whole
  411.25 Mha estate with no operability, tenure, road, slope, or mill-capacity
  screen. The operable cases use an area-share proxy with no legal THLB, no
  national slope DEM, and no spatial road/tenure overlay. Neither is an AAC,
  policy supply, or investment case.
- **Not official OSeCAN.** `model_estimate=true`, `observed=false`,
  `policy_grade=false`, `national_claim=false`. No national supply claim is
  made.

## 12. Commits and artifacts

Parent commits are in this repository; data commits are in the data submodule
(`UBC-FRESH/clews-c2070-fresh-data`).

| Evidence | Parent commit(s) | Data commit(s) | Documentation |
|---|---|---|---|
| National RSYC transform v1 library | `ab90784` | `5685f038b` | `data/derived/national/rsyc_transform_v1/report.md` |
| Transformed national 990 m dataset and 119 Mm³ case | `cd02ea1`, `a594179` | `1d39f9eb4`, `f6dfd8edb` | `docs/ws4-transformed-119m.md` |
| Transformed sustainable 100/150 and operable 100/119/150 cases | `fd12cd1` | `4dd708e11` | `docs/ws4-transformed-cases.md` |
| This documentation/tag release | this commit | — | this document |

Artifacts:

- Curve library: `data/derived/national/rsyc_transform_v1/`.
- Transformed dataset: `data/derived/ws4/scanfi-national-990m-transformed/`.
- Transformed 119 Mm³ schedule/chain:
  `data/derived/ws4/scanfi-national-990m-transformed/sustainable-119m/` and
  `data/derived/national/facilities/ws4_transformed_119m/`.
- Transformed sustainable 100/150 and operable 100/119/150 schedules/chains:
  `data/derived/national/facilities/ws4_transformed_cases/`.
- Official OSeCAN tree: `data/derived/national/clews/osecan/` (146 files;
  `80caa91c...`; read-only).

## 13. Reproduce

Build the curve library:

```bash
python scripts/national/build_rsyc_transform_v1.py
.venv/bin/python -m pytest tests/test_rsyc_transform_v1.py -q
```

Build the transformed WS4 dataset and rerun the cases:

```bash
.venv-ws4/bin/python -m workflows.national.facilities.ws4_transformed_dataset
.venv-ws4/bin/python -m workflows.national.facilities.ws4_transformed_119m
.venv-ws4/bin/python -m workflows.national.facilities.ws4_transformed_cases
```

Test the WS4 transformed cases:

```bash
.venv/bin/python -m pytest tests/test_ws4_transformed_119m.py \
  tests/test_ws4_transformed_cases.py -q
```

The builds validate the WS4 dataset, hash the official OSeCAN tree and frozen
chain v5.1 before and after, and refuse to overwrite a run whose schedule
differs. Any official OSeCAN or chain v5.1 change aborts the build.

## 14. Release verification

- [x] Documentation/tag only; only docs, ROADMAP, and CHANGE_LOG changed.
- [x] Frozen chain v5.1 tag and artifacts remain valid and unmodified.
- [x] Official OSeCAN 146-file tree hash unchanged.
- [x] No model, data, UI, chain v5.1, or official OSeCAN mutation.
- [x] No unexpected deletion (`D`) in parent or data.
- [x] Annotated tag `national-transformed-supply-ws4-v1` peels to this docs
  commit.
- [x] Branch and tag pushed.

## 15. Related documentation

- `docs/ws4-transformed-119m.md` — transformed 119 Mm³ case.
- `docs/ws4-transformed-cases.md` — transformed sustainable 100/150 and
  operable 100/119/150 cases.
- `docs/national-sustainable-supply-ws4-v1.md` — original-curve sustainable and
  operable release.
- `docs/national-facility-chain-ws4-v1.md` — WS4-derived chain/OSeCAN release.
- `docs/national-forest-estate-ws4-v1.md` — national forest-estate candidate.
- `planning/rsyc-merchantable-volume-conversion.md` — pending merchantable
  QMD/SPH conversion.
