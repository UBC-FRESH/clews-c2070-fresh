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
  `nemora.fit.fit_inventory` on grouped tallies.  The legacy two-component
  gamma mixture (`mixture`) uses `nemora.fit.mixture.fit_mixture_grouped`.
  Three multi-component families are added on the same left-truncated grouped
  support:
  - `mix_weibull_2`: two Weibull components with independent shape `a`,
    scale `beta`, and weights;
  - `mix_weibull_gamma`: one Weibull and one gamma component;
  - `mix_weibull_3`: three Weibulls, attempted only when the two-component
    candidates leave a high-KS stratum (selected KS > 0.15).

  The new families use the assortment-owned `mixtures.py` boundary.  Its EM
  delegates each component M-step to `fit_censored_inventory` (Weibull) or the
  documented gamma moment update; it does not reimplement a density
  estimator.  The legacy gamma mixture is kept unchanged so the comparison
  against the previous run is like-for-like.
- **Spike (first-bin) models.** `spike_weibull` / `spike_gamma` and their
  constrained variants add a point mass at the plot DBH limit plus one
  continuous tail.  The spike carries the first DBH bin
  `[dbh_limit, dbh_limit + bin_width)`; the continuous component is a Weibull
  or gamma left-truncated at the first bin's upper edge.  This is the stable
  two-part limit of a zero-inflated mixture: because the parts have disjoint
  support, the E-step gives first-bin tallies spike responsibility one and the
  M-step sets the spike weight to the observed first-bin share and refits the
  tail once.  There is no weak spike/continuous overlap to collapse.  The
  spike weight counts as one AIC parameter and is sampled uniformly inside the
  first bin so every synthetic stem keeps a physical diameter.  Spike families
  are attempted only when the first bin carries more than 20% of the
  expansion-factor tally or the best single-component fit leaves KS above
  0.15.  The weight must stay inside `(0.001, 0.95)`; a fit outside that range
  is rejected as degenerate.  The spike is a shape device only: it never sets
  stand volume or TPH.
- **Spike tail variants.** `spike_weibull` / `spike_gamma` use the documented
  two-stage censored tail fit.  `spike_weibull_constrained` /
  `spike_gamma_constrained` use a bounded grouped multinomial MLE
  (L-BFGS-B on the exact binned likelihood) with Weibull `a >= 1.0` or gamma
  `p >= 1.0`, and scale `beta >= 0.1` cm.  The constrained lower shape bound
  is the finite-mean boundary; a constrained fit whose shape or scale sits on
  any declared bound is reported as `boundary_reject` in
  `distribution_fits.csv` and is never selected.  Both variants are
  synthesis-eligible when valid, because `sample_spike_mixture` always draws
  the tail on the finite fitted support `(first_bin_upper, support_upper]`.
  The unconstrained tail's *untruncated* mean can be infinite when it reaches
  the `1e-6` cm scale floor with shape below one; it must not be extrapolated
  beyond `support_upper`.  The AIC comparison treats all four families as
  three-parameter models.
- **Mixture stability.** The new fits use deterministic weighted-quantile
  starts plus a seeded jittered restart.  Components are sorted by `beta`,
  then by shape, after every update; weights move with their components.  A
  start is rejected when a weight is below `1e-4`, a parameter is non-finite
  or non-positive, or two components collapse onto the same parameters.
  Convergence is accepted on the likelihood/parameter tolerance or on a
  12-iteration stall with negligible parameter movement.  Unstable fits stay
  in `distribution_fits.csv` for audit but are never selected for synthesis.
- **Scoring.** Candidate AIC/AICc/BIC and KS are recomputed on one common,
  support-conditioned binned likelihood.  Native estimator metrics are kept
  beside them.  This is a scoring layer, not a second estimator.
- **Selection.** Synthesis uses the best Weibull, gamma, mixture, or spike
  model.  A mixture or spike model replaces a single-component distribution
  only when it improves common AIC by at least two units; smaller gains go to
  the simpler model.  Among flexible models the same two-unit rule prefers
  fewer parameters, so a three-parameter spike beats a five-parameter mixture
  when their AICs are within two units.  New Weibull families and spike
  families must converge, and a constrained spike must be interior, before
  selection.  The legacy two-gamma mixture remains available as the documented
  fallback and carries a ten-unit AIC penalty when it did not converge, so a
  converged alternative wins when the fits are close.  Generalized beta is a
  diagnostic candidate: the registry has no inverse CDF for it and a grouped
  curve fit can land on a numerically valid but unsampleable parameter set.
  The CSV records `synthesis_compatible`, `spike_boundary_status`,
  `spike_at_bound`, `aic_best`, `converged`, and the per-start diagnostics so
  the choice is auditable.
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
  `nemora.fit.mixture.sample_mixture` (all mixture families, including the
  Weibull-containing ones), conditioned on the fitted support.  Mixture
  component names travel with the weights and parameters so a Weibull
  component is never sampled as a gamma.  Spike families use
  `sample_spike_mixture`: a Bernoulli draw selects the spike, and those stems
  are placed uniformly inside the first DBH bin
  `[dbh_limit, dbh_limit + spike_bin_width)`; the rest are drawn from the
  truncated tail.  The uniform first-bin convention preserves the fitted
  first-bin probability while keeping every stem at a physical diameter above
  the plot limit.  All spike draws are clipped to the fitted support.
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
| Mixture families | `mixture`, `mix_weibull_2`, `mix_weibull_gamma`, optional `mix_weibull_3` |
| Spike families | `spike_weibull`, `spike_gamma`, `spike_weibull_constrained`, `spike_gamma_constrained` |
| Spike gating | first bin > 20% of tallies or best simple KS > 0.15 |
| Spike weight range | `(0.001, 0.95)` |
| Constrained tail | grouped MLE; shape >= 1, scale >= 0.1 cm; bound hit = `boundary_reject` |
| Mixture starts | 2 (3-component: 1) |
| Mixture convergence | tolerance `1e-6` or a 12-iteration stall |
| Mixture minimum weight | `1e-4` |
| Selection margin | `2` AIC units |
| Synthetic trees | 600 |
| Seed | 20261115 |

BTC TSA24 exposes a combined `SBS_SX` spruce stratum rather than a separate
`SBS_SW`; the prototype uses `SBS_SX` at site class H and filters its
composition to the spruce-leading plot group (SW/SE/S/SB).  The run does not
claim a species-specific SBS_SW curve that the BTC table does not provide.

### TSA24 mixture results

The 2026-11-15 re-fit added the new families to all 75 TSA24 DBH targets
(68 general + 7 AU).  The selected model changed in 45 strata: 42 improved by
more than 2 AIC and one was worse by more than 2 AIC (`SB` in the AU age
window, +6.7 AIC).  `mix_weibull_gamma` was selected 33 times and
`mix_weibull_2` 12 times; the legacy two-gamma fallback remained selected 22
times, mostly where it was already the best model or where the new fits were
unstable.  `mix_weibull_3` was attempted on three high-KS strata and was not
stable; it was never selected.

The three high-KS rows in the previous report are two unique strata, both
`CW` in the Montane Cordillera.  They remain the only high-KS selections:
the new Weibull mixtures are worse than the single Weibull and the legacy
gamma mixture on those tallies, because the first 2-4 cm bin dominates the
expansion-factor tally.  The `AC` Montane Cordillera fit improved from KS
0.184 to 0.040 and the `DG` Fraser Basin fit from 0.156 to 0.065.

For the target AU (`SBS_SX`, site class H, age 100), six of seven species
switched to a Weibull mixture (`BL`, `EP`, `PL` to `mix_weibull_2`; `FD`,
`SB`, `SW` to `mix_weibull_gamma`); `AT` kept the legacy gamma mixture.
The volume-constrained tree list still hits the 311.6 m3 target and volume
conservation is exact.  The changed shapes raise TPH from 1,062 to 1,225 and
lower QMD from 19.57 to 18.72 cm; recovered logs fall from 259.96 to
252.58 m3 and residue rises from 51.64 to 59.02 m3.

The 2026-11-16 spike re-fit added the four spike families to the same 75
targets.  Spike fits are attempted on 29 gated strata (116 candidate fits);
28 constrained tails are rejected at a declared shape/scale bound.  Fifteen
selected fits (14 unique `scope`/`fit_id` pairs) are spikes: eight constrained
and seven unconstrained.  The two CW high-KS strata both switch from the
legacy gamma mixture to `spike_weibull`: `ecozone:Montane Cordillera` at
spike weight 0.657 (KS 0.221 -> 0.034, AIC -795.2) and
`ecoprovince:Columbia Montane Cordillera` at 0.564 (KS 0.217 -> 0.036, AIC
-271.7).  General high-KS selections (>0.20) fall from 3 to 0; the maximum
selected general KS is 0.146.  The other switches are `BL`, `SW`, `SB`, `PL`,
`AC`, and `HW` strata where the first bin exceeds the 20% trigger or the
simple fit leaves KS above 0.15.  One switch (`ecodistrict:Hart Ranges|SW`)
is 8.6 raw AIC worse than the previous legacy mixture but 1.4 better after the
documented ten-unit non-convergence penalty; its KS improves from 0.046 to
0.023, and the row is retained for audit.

For the target AU only `BL` changes: `mix_weibull_2` ->
`spike_weibull_constrained`, spike weight 0.315, AIC -389.9, KS 0.0229 ->
0.0100.  The 311.6 m3 target and volume conservation remain exact; TPH falls
from 1,225 to 1,189, QMD rises from 18.72 to 18.84 cm, recovered logs rise
from 252.58 to 253.16 m3, and residue falls from 59.02 to 58.44 m3.  The
unconstrained CW tails reach the two-stage scale floor and must not be
extrapolated beyond `support_upper`; the constrained variants remain the
finite-mean alternative where they are interior.

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
failure, non-negative ledger values, the facility capacity screen, bimodal
two-Weibull recovery, mixture component ordering, degenerate-component
rejection, mixed Weibull/gamma fitting, truncated mixture sampling, spike
weight recovery for the Weibull and gamma variants, degenerate spike
rejection, first-bin/KS gating, spike support bounds, constrained-tail box
bounds, boundary rejection and non-selection, first-bin weight equality,
truncated spike sampling, finite-mean synthetic spike+Weibull recovery, and
volume plus assortment conservation after spike synthesis.

## 8. Known limits

- The DBH fit is stand-level and not age-specific in the general table; the
  TSA24 AU uses an explicit 80-120 year window.
- The shape fit uses the modal DBH limit class; plots with other limits are
  excluded and reported.
- The additive mixture families fit the untruncated component densities but
  condition every M-step and the observed likelihood on `DBH >= DBH limit`.
  They do not mix truncation points in one fit.
- `mix_weibull_3` is the least stable family.  It is only attempted after the
  two-component fits leave a KS above 0.15, and a non-converged three-component
  fit is never selected.  A collapsed extra component is rejected rather than
  reported as an improvement.
- A two-Weibull fit can be worse than a single Weibull when the first DBH bin
  dominates the tally (for example, a 2 cm plot limit with most stems in the
  2-4 cm bin).  The spike families target that case directly; continuous
  mixtures are still retained in the candidate table, and a spike model only
  wins when it improves AIC by at least two units.  The spike weight is a
  first-bin share, not a free shape parameter identified from the tail.
- The spike tail is fitted from the first bin upper edge, so the continuous
  component describes only DBH above the first bin.  It is not comparable to
  a full-support Weibull parameterization outside this binned likelihood.
- The constrained finite-mean tail is a real restriction.  In the CW
  first-bin-dominated strata the grouped MLE lands on the Weibull `a = 1` /
  gamma `p = 1` boundary (the exponential limit), which is marked
  `boundary_reject` and is never selected.  The unconstrained two-stage spike
  fits the same tail with a power-law-like Weibull (`a` around 0.13-0.17, scale
  at the `1e-6` cm floor) and is selected because it improves both AIC and KS;
  its untruncated mean is infinite, so it is valid only on the finite fitted
  support and must not be extrapolated beyond `support_upper`.  A future
  Pareto or generalized-gamma tail could remove the boundary estimate.
- A smooth truncated Weibull sample can be represented by a spike within about
  three AIC units because the spike absorbs the lower-truncation pile-up; the
  two-unit rule may select it.  The simple Weibull candidate remains in the
  table for audit.
- The target volume is treated as a taper-total target.  Recovered log volume
  is lower by residue; it is not forced to equal the BTC volume.
- The wood AGB proxy excludes bark, branches, and foliage, so it is not
  expected to equal BTC `agb_odt_ha`.
- The sort mix is a dimension-only recovery, not an official grade
  distribution.
- Facility assignment is a screen.  The MILP and transport routing are not
  implemented here.
