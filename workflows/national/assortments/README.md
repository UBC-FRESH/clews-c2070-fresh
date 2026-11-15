# National assortments: stand-to-tree disaggregation

This package is the prototype contract for turning a stand-level target into a
bucked assortment matrix.  It is deliberately split into five boundaries:

```
RSYC / BTC stand target
  -> DBH distribution by RSYC stratum and species   (distributions.py)
  -> hierarchical height-diameter model             (height.py)
  -> volume-constrained synthetic tree list         (synthesize.py)
  -> existing BC taper + bucking layer              (../bucking/)
  -> assortment matrix + residue / AGB ledger       (assortments.py)
  -> facility feedstock interface                   (facility.py)
```

The TSA24 demonstration is `validate_tsa24.py`.  It writes the prototype
artifacts under `data/derived/national/assortments/tsa24_prototype/`.

## Shape-versus-scale contract

This is the load-bearing rule of the package:

| Input | What it sets | What it must not set |
|---|---|---|
| Fitted DBH distribution | Distribution **shape**, support, relative frequencies | Stand volume, TPH, assortment totals |
| Height-diameter model | **Height** at a given DBH | Diameter distribution or volume |
| Species composition weights | **Stem allocation** across species | Total volume or diameter shape |
| RSYC / BTC target volume | **Scale** of the whole tree list | Distribution shape |
| Bucking rules + taper | Log dimensions, recovery, residue | Stand volume or species composition |

The synthesizer draws a tree list, computes each stem's taper total, then
scales every expansion factor by

```
scale = target_volume_m3 / sum(taper_total_i * expansion_factor_i)
```

so the achieved taper total equals the target exactly.  The distribution can
never make the stand larger or smaller.  The resulting TPH is an **output** of
the target volume and the fitted shape, not an independent input.

## Evidence tiers

Every fit row carries one of:

| Tier | Meaning |
|---|---|
| `stratum_fit` | Direct fit at the finest available RSYC level (normally ecodistrict) with at least 30 trees and 5 plots. |
| `pooled_fit` | Direct fit failed the threshold; the estimate is pooled at ecoregion, ecoprovince, or ecozone. |
| `default` | No spatial pool met the threshold; the estimate is the TSA24 species-only pool. |

The row also records `fit_level`, `n_trees`, `n_plots`, `n_bins`,
`dbh_limit_cm`, and the candidate estimator diagnostics.  A `default` fit is
still a fitted distribution; it is never a hard-coded shape.

## 1. DBH distributions (`distributions.py`)

Input is one row per live tree with DBH, expansion factor, plot id, age, plot
DBH limit, and the RSYC ecological hierarchy.

- **Censoring.** Plots with different `DBH_LIMIT_TAG` values are not mixed in
  one shape fit.  The modal limit class is used and the excluded plot count is
  reported.  This avoids pretending that a 2 cm plot and a 9.1 cm plot observed
  the same low tail.
- **Estimators.** Weibull and gamma use
  `nemora.workflows.censoring.fit_censored_inventory` (the 2sc two-stage
  truncation correction).  Generalized beta (`gb1`, `gb2`) uses
  `nemora.fit.fit_inventory` on grouped tallies.  A two-component gamma mixture
  uses `nemora.fit.mixture.fit_mixture_grouped`.
- **Scoring.** Candidate AIC/AICc/BIC and KS are recomputed on one common,
  support-conditioned binned likelihood.  Native estimator metrics are kept
  beside them.  This is a scoring layer, not a second estimator.
- **Selection.** Synthesis uses the best Weibull, gamma, or gamma mixture.
  Generalized beta is a diagnostic candidate: the registry has no inverse CDF
  for it and a grouped curve fit can land on a numerically valid but
  unsampleable parameter set.  The CSV records `synthesis_compatible` and
  `aic_best` so the choice is auditable.
- **Fail loudly.** If no candidate can be fitted and the species-only default
  is also too thin, `DistributionFitError` is raised.  The TSA24 runner records
  per-stratum failures rather than substituting a shape silently.

## 2. Height models (`height.py`)

`nemora.workflows.height_volume.fit_height_diameter_models` fits the power
form `H = 1.3 + exp(a) * DBH^b`.  The wrapper applies the same ecological
fallback as the DBH fit and records `n`, `rmse_m`, `bias_m`, coefficients,
and evidence tier.  It does not refit or smooth the coefficients.

## 3. Synthetic tree list (`synthesize.py`)

- Species weights are normalized and species below a documented minimum share
  are dropped.  The retained weights sum to one.
- Trees are allocated by largest remainder, so the species weights are honoured
  at the tree-count level.
- DBH is drawn with `nemora.sampling.sample_truncated` (Weibull/gamma) or
  `nemora.fit.mixture.sample_mixture` (mixture), conditioned on the fitted
  support.
- **Adapter note.** `nemora.synthesis.helpers.StandDBHSampler` is an analytic
  or bootstrap sampler that does not expose a truncation support.  Because the
  TSA24 fits are left-censored at the plot DBH limit, the synthesizer calls the
  documented `nemora.sampling.sample_truncated` and `sample_mixture` samplers
  directly.  This is a thin sampling-boundary adapter, not a reimplementation
  of the fitting or tree-synthesis estimators.
- Height is predicted from the selected hierarchical model.
- Taper total is computed with the existing `TaperRegistry`.
- Expansion factors are scaled to the target volume.  `TreeRecord` output is
  directly consumable by `workflows.national.bucking`.

## 4. Assortment matrix (`assortments.py`)

The existing BC interior rule set and `buck_trees` produce `BuckedLog` and
`ResidueLedger` rows.  The matrix aggregates:

```
species x product x sort_code x top-diameter class x length class
```

and carries:

- recovered log volume, value, and price;
- residue volume, ODT, green tonnes, and chip green tonnes;
- taper total and recovered log volume for the species ledger;
- a wood-only AGB proxy (`taper total x basic density`);
- the optional BTC AGB target, allocated by species share, for context;
- `record_type = log | residue | total`.

Volume conservation is explicit: for every species, recovered logs + residue =
taper total.  The grand-total row is the sum of the two streams.  Negative
volumes, missing tree matches, and mismatched recovered volumes fail loudly.

The BC sorts are **dimension-only**.  `grade_status` is `not_assigned`; defect,
sweep, knot, rot, and product quality are not represented.

## 5. Facility interface (`facility.py`)

`FacilityContract`, `FeedstockOffer`, `FeedstockGrade`, and
`FacilityAssignment` define the buyer-facing contract: grade eligibility,
volume, delivered cost, contract minimum/maximum, duration bounds, and
destination.

`assign_feedstock` is a deterministic eligibility and capacity screen, not the
full MILP.  It never relaxes a grade or volume bound.  `to_clews_lp_contract`
emits the nodes, arcs, and constraints that a future CLEWS five-layer LP /
FHOPS model will consume:

- origins and facilities are nodes;
- feedstock grades are commodities;
- delivered cost and volume are arc attributes;
- contract minima/maxima and grade eligibility are constraints;
- harvest scheduling and routing stay in FHOPS/CLEWS.

## 6. TSA24 prototype

Run from the repository root:

```bash
PYTHONPATH="${NEMORA_ROOT:?set NEMORA_ROOT}/src" \
  .venv/bin/python -m workflows.national.assortments.validate_tsa24 \
  --out-dir data/derived/national/assortments/tsa24_prototype
```

Defaults:

| Parameter | Value |
|---|---|
| TSA | `Prince George` / TSA24 |
| Target AU | BTC `au_id=23000`, `SBS_SX`, site-index class H |
| Age | 100 years |
| Target ecodistrict | Most sampled TSA24 ecodistrict for the AU's leading-species group |
| Target volume | BTC `managed_au_curves.csv` volume at the AU/age |
| Age window | 80-120 years |
| Species minimum share | 0.01 |
| DBH bin width | 2 cm |
| Fit threshold | 30 trees / 5 plots |
| Height threshold | 30 observations |
| Synthetic trees | 600 |
| Seed | 20261115 |

BTC TSA24 exposes a combined `SBS_SX` spruce stratum rather than a separate
`SBS_SW`; the prototype uses `SBS_SX` at site class H and filters its
composition to the spruce-leading plot group (SW/SE/S/SB).  The run does not
claim a species-specific SBS_SW curve that the BTC table does not provide.

The target composition is filtered to plots whose leading species belongs to
the AU's leading-species group (for `SX`: SW/SE/S/SB).  This keeps an SBS_SX
target from being driven by mixed or deciduous-leading plots elsewhere in the
ecodistrict.  The filter and fallback are recorded in `target_stand.json` and
`validation_summary.json`.

## 7. Tests

```bash
PYTHONPATH="${NEMORA_ROOT}/src" \
  .venv/bin/python -m pytest -q workflows/national/assortments/tests/test_assortments.py
```

The tests cover censored Weibull recovery, hierarchical fallback, exact target
volume, conservation, species-weight normalization, unsupported-species
failure, non-negative ledger values, and the facility capacity screen.

## 8. Known limits

- The DBH fit is stand-level and not age-specific in the general table; the
  TSA24 AU uses an explicit 80-120 year window.
- The shape fit uses the modal DBH limit class; plots with other limits are
  excluded and reported.
- The target volume is treated as a taper-total target.  Recovered log volume
  is lower by residue; it is not forced to equal the BTC volume.
- The wood AGB proxy excludes bark, branches, and foliage, so it is not
  expected to equal BTC `agb_odt_ha`.
- The sort mix is a dimension-only recovery, not an official grade
  distribution.
- Facility assignment is a screen.  The MILP and transport routing are not
  implemented here.
