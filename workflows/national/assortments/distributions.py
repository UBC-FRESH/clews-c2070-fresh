"""RSYC-stratified DBH distribution fitting for stand disaggregation.

The estimator layer is deliberately a thin boundary around ``nemora``:

* Weibull and gamma use ``fit_censored_inventory`` so the plot DBH limit is
  carried as an explicit truncation support.
* Generalized beta candidates use ``nemora.fit.fit_inventory`` with the
  documented grouped-data path.
* The legacy two-component gamma mixture uses ``fit_mixture_grouped``.
* ``mix_weibull_2``, ``mix_weibull_gamma``, and ``mix_weibull_3`` use the
  assortment-owned ``mixtures.fit_truncated_mixture`` boundary, which delegates
  each component M-step to ``fit_censored_inventory`` (Weibull) or the
  documented gamma moment update and conditions on the same support.

The functions here do not re-implement any estimator.  They only prepare
grouped tallies, call the documented APIs, and score the returned candidates
on one common, support-conditioned likelihood so AIC/KS comparisons are
meaningful across families.

Shape-versus-scale contract
---------------------------
A fitted distribution supplies the *shape* of the DBH distribution.  It never
supplies the stand volume.  The synthesis layer scales expansion factors so
the taper total equals the target volume taken from RSYC/BTC.  See
``synthesize.py``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid
from scipy.stats import gamma as gamma_dist
from scipy.stats import weibull_min

from nemora.core import InventorySpec
from nemora.distributions import get_distribution
from nemora.fit import fit_inventory
from nemora.fit.mixture import (
    MixtureComponentSpec,
    fit_mixture_grouped,
    mixture_cdf,
    mixture_pdf,
)
from nemora.workflows.censoring import fit_censored_inventory

from .mixtures import (
    MIXTURE_FAMILY_COMPONENTS,
    SPIKE_CONSTRAINED_FAMILIES,
    SPIKE_CONSTRAINED_SCALE_LOWER,
    SPIKE_CONSTRAINED_SHAPE_LOWER,
    SPIKE_FAMILIES,
    SPIKE_FAMILY_COMPONENTS,
    MixtureComponent,
    MixtureFitError,
    SpikeMixtureFit,
    canonical_component_order,
    component_is_degenerate,
    fit_spike_mixture,
    fit_truncated_mixture,
    spike_tail_bounds,
)

__all__ = [
    "DEFAULT_LEVELS",
    "DistributionFit",
    "DistributionFitError",
    "FitCandidate",
    "SPIKE_FIRST_BIN_SHARE_TRIGGER",
    "SPIKE_KS_TRIGGER",
    "fit_dbh_distributions",
    "fit_distribution_for_target",
]

DEFAULT_LEVELS: tuple[str, ...] = (
    "ecodistrict",
    "ecoregion",
    "ecoprovince",
    "ecozone",
)

_CANDIDATE_ORDER = {
    "weibull": 0,
    "gamma": 1,
    "spike_weibull": 2,
    "spike_gamma": 3,
    "spike_weibull_constrained": 4,
    "spike_gamma_constrained": 5,
    "mixture": 6,
    "mix_weibull_2": 7,
    "mix_weibull_gamma": 8,
    "mix_weibull_3": 9,
    "gb1": 10,
    "gb2": 11,
}

#: Minimum common AIC improvement required before a mixture replaces a
#: single-component distribution.  An improvement below two AIC units is
#: treated as negligible and the simpler model is kept.
_MIXTURE_AIC_GAIN = 2.0

#: A non-converged legacy gamma mixture remains available as the documented
#: fallback, but pays this AIC penalty so a converged alternative wins when
#: the two are close.
_NONCONVERGED_AIC_PENALTY = 10.0

#: Mixture families introduced by the Weibull-extension work.  Unlike the
#: legacy two-gamma fallback, these are never selected when the EM did not
#: converge.
_NEW_MIXTURE_FAMILIES = {"mix_weibull_2", "mix_weibull_gamma", "mix_weibull_3"}

#: A three-component Weibull family is only attempted when the two-component
#: candidates leave a high-KS stratum, because it is the least stable family.
_THREE_COMPONENT_KS_TRIGGER = 0.15

#: Spike families are only attempted when the first DBH bin carries more than
#: this share of the expansion-factor tally, or when the best single-component
#: fit still leaves a high KS.  The first condition targets the CW
#: first-bin-dominated case directly; the second catches a first-bin problem
#: that a pooled threshold would miss.
SPIKE_FIRST_BIN_SHARE_TRIGGER = 0.20
SPIKE_KS_TRIGGER = 0.15


class DistributionFitError(RuntimeError):
    """Raised when no candidate distribution can be fitted for a stratum."""


@dataclass(frozen=True, slots=True)
class FitCandidate:
    """One candidate distribution fit, scored on a common likelihood."""

    distribution: str
    parameters: dict[str, float] = field(default_factory=dict)
    component_weights: tuple[float, ...] | None = None
    component_parameters: tuple[dict[str, float], ...] | None = None
    component_names: tuple[str, ...] | None = None
    n_parameters: int = 0
    aic: float = math.nan
    aicc: float = math.nan
    bic: float = math.nan
    ks: float = math.nan
    native_aic: float = math.nan
    native_aicc: float = math.nan
    native_bic: float = math.nan
    native_ks: float = math.nan
    rss: float = math.nan
    log_likelihood: float = math.nan
    method: str = ""
    fit_status: str = "ok"
    converged: bool = True
    iterations: int = 0
    n_starts: int = 0
    n_valid_starts: int = 0
    ordering_swaps: int = 0
    fit_diagnostics: str = ""
    spike_weight: float = math.nan
    spike_component: str = ""
    spike_constrained: bool = False
    spike_boundary_status: str = ""
    spike_at_bound: str = ""
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.fit_status == "ok" and math.isfinite(self.aic)

    @property
    def synthesis_compatible(self) -> bool:
        """Whether the prototype can sample this candidate reliably.

        Generalized beta is kept as a diagnostic candidate.  The nemora
        registry does not expose an inverse CDF for it, and the grouped
        curve-fit can land on a numerically valid but practically unsampleable
        parameter set.  Synthesis therefore uses Weibull, gamma, the finite
        mixtures, or a spike-plus-tail family.  Both spike variants are
        eligible: the sampler always draws the tail on the finite fitted
        support, so an unconstrained power-law tail is used only inside
        ``support_upper`` and is never extrapolated.  A constrained spike whose
        tail parameters sit on a declared bound is marked ``boundary_reject``
        and is not a successful candidate.
        """
        if not self.is_success:
            return False
        if self.distribution in SPIKE_FAMILIES:
            return self.spike_boundary_status in {"", "ok"}
        supported = {"weibull", "gamma"} | set(MIXTURE_FAMILY_COMPONENTS)
        return self.distribution in supported

    def parameters_json(self) -> str:
        if self.component_parameters is not None:
            names = self.component_names or ("gamma",) * len(self.component_parameters)
            components = [
                {
                    "name": name,
                    "weight": float(weight),
                    "parameters": {key: float(value) for key, value in params.items()},
                }
                for name, weight, params in zip(
                    names,
                    self.component_weights or (),
                    self.component_parameters,
                    strict=False,
                )
            ]
            return json.dumps({"components": components}, sort_keys=True)
        return json.dumps({key: float(value) for key, value in self.parameters.items()}, sort_keys=True)

    def component_weights_json(self) -> str:
        if self.component_weights is None:
            return ""
        return json.dumps([float(value) for value in self.component_weights])


def _aic_key(candidate: FitCandidate) -> tuple[float, int]:
    return (candidate.aic, _CANDIDATE_ORDER.get(candidate.distribution, 99))


def _selection_aic(candidate: FitCandidate) -> float:
    """Return AIC with a stability penalty for a non-converged fallback."""
    return candidate.aic + (0.0 if candidate.converged else _NONCONVERGED_AIC_PENALTY)


def _select_mixture(candidates: Sequence[FitCandidate]) -> FitCandidate | None:
    """Choose a mixture, preferring converged and simpler fits within 2 AIC."""
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            _selection_aic(candidate),
            candidate.n_parameters,
            _CANDIDATE_ORDER.get(candidate.distribution, 99),
        ),
    )
    best = ranked[0]
    for candidate in ranked[1:]:
        if _selection_aic(candidate) - _selection_aic(best) >= _MIXTURE_AIC_GAIN:
            break
        if candidate.n_parameters < best.n_parameters:
            best = candidate
    return best


@dataclass(frozen=True, slots=True)
class DistributionFit:
    """One stratum fit with every candidate and the selected distribution."""

    scope: str
    target_level: str
    target_id: str
    fit_level: str
    fit_id: str
    species: str
    ecodistrict: str | None
    ecoregion: str | None
    ecoprovince: str | None
    ecozone: str | None
    age_window: tuple[float, float] | None
    n_trees: int
    n_plots: int
    n_bins: int
    dbh_limit_cm: float
    support_upper_cm: float
    evidence_tier: str
    candidates: tuple[FitCandidate, ...]

    @property
    def best_aic(self) -> FitCandidate:
        successes = [candidate for candidate in self.candidates if candidate.is_success]
        if not successes:
            raise DistributionFitError(
                f"no successful candidate for {self.fit_id}; "
                f"failures: {[c.error for c in self.candidates if c.error]}"
            )
        return min(
            successes,
            key=lambda candidate: (
                candidate.aic,
                _CANDIDATE_ORDER.get(candidate.distribution, 99),
            ),
        )

    @property
    def selected(self) -> FitCandidate:
        """Return the synthesis-selected candidate.

        Selection uses the common-support AIC.  A mixture or spike model
        replaces a single-component distribution only when it improves AIC by
        at least two units; smaller gains are treated as negligible and the
        simpler model wins.  Among flexible models, the same two-AIC-unit rule
        prefers fewer parameters, so a three-parameter spike beats a
        five-parameter mixture when their AICs are within two units.  A
        non-converged legacy gamma mixture remains available as the documented
        fallback with a ten-unit stability penalty; new Weibull families and
        constrained spike families must converge before they can be selected.
        Unconstrained spike rows and constrained rows marked ``boundary_reject``
        are excluded by ``synthesis_compatible``; a rejected constrained spike
        leaves the best non-spike model in force.
        """
        compatible = [
            candidate
            for candidate in self.candidates
            if candidate.is_success and candidate.synthesis_compatible
        ]
        if not compatible:
            raise DistributionFitError(
                f"no synthesis-compatible candidate for {self.fit_id}; "
                f"candidates: {[c.distribution for c in self.candidates if c.is_success]}"
            )
        simple = [
            candidate
            for candidate in compatible
            if candidate.distribution in {"weibull", "gamma"}
        ]
        flexible = [
            candidate
            for candidate in compatible
            if (
                candidate.distribution in MIXTURE_FAMILY_COMPONENTS
                or candidate.distribution in SPIKE_FAMILIES
            )
            and (candidate.converged or candidate.distribution == "mixture")
        ]
        best_simple = min(simple, key=_aic_key, default=None)
        best_flexible = _select_mixture(flexible)
        if best_simple is None:
            if best_flexible is None:
                raise DistributionFitError(
                    f"no usable candidate for {self.fit_id}; "
                    f"mixtures: {[c.distribution for c in flexible]}"
                )
            return best_flexible
        if (
            best_flexible is not None
            and _selection_aic(best_flexible)
            <= best_simple.aic - _MIXTURE_AIC_GAIN
        ):
            return best_flexible
        return best_simple

    @property
    def selected_distribution(self) -> str:
        return self.selected.distribution

    def to_rows(self) -> list[dict[str, Any]]:
        selected = self.selected
        rows: list[dict[str, Any]] = []
        for candidate in self.candidates:
            rows.append(
                {
                    "scope": self.scope,
                    "target_level": self.target_level,
                    "target_id": self.target_id,
                    "fit_level": self.fit_level,
                    "fit_id": self.fit_id,
                    "species": self.species,
                    "ecodistrict": self.ecodistrict,
                    "ecoregion": self.ecoregion,
                    "ecoprovince": self.ecoprovince,
                    "ecozone": self.ecozone,
                    "age_window": (
                        f"{self.age_window[0]:g}-{self.age_window[1]:g}"
                        if self.age_window is not None
                        else "all"
                    ),
                    "n_trees": self.n_trees,
                    "n_plots": self.n_plots,
                    "n_bins": self.n_bins,
                    "dbh_limit_cm": self.dbh_limit_cm,
                    "support_upper_cm": self.support_upper_cm,
                    "evidence_tier": self.evidence_tier,
                    "distribution": candidate.distribution,
                    "fit_status": candidate.fit_status,
                    "parameters_json": candidate.parameters_json(),
                    "component_weights_json": candidate.component_weights_json(),
                    "component_names_json": (
                        json.dumps(list(candidate.component_names))
                        if candidate.component_names is not None
                        else ""
                    ),
                    "n_parameters": candidate.n_parameters,
                    "aic": candidate.aic,
                    "aicc": candidate.aicc,
                    "bic": candidate.bic,
                    "ks": candidate.ks,
                    "native_aic": candidate.native_aic,
                    "native_aicc": candidate.native_aicc,
                    "native_bic": candidate.native_bic,
                    "native_ks": candidate.native_ks,
                    "rss": candidate.rss,
                    "log_likelihood": candidate.log_likelihood,
                    "method": candidate.method,
                    "converged": candidate.converged,
                    "iterations": candidate.iterations,
                    "n_starts": candidate.n_starts,
                    "n_valid_starts": candidate.n_valid_starts,
                    "ordering_swaps": candidate.ordering_swaps,
                    "fit_diagnostics": candidate.fit_diagnostics,
                    "spike_weight": candidate.spike_weight,
                    "spike_component": candidate.spike_component,
                    "spike_constrained": candidate.spike_constrained,
                    "spike_boundary_status": candidate.spike_boundary_status,
                    "spike_at_bound": candidate.spike_at_bound,
                    "selected": candidate is selected,
                    "selected_distribution": selected.distribution,
                    "synthesis_compatible": candidate.synthesis_compatible,
                    "aic_best": candidate is self.best_aic,
                    "fit_quality_flag": (
                        "high_ks" if candidate is selected and candidate.ks > 0.20 else "ok"
                    ),
                    "error": candidate.error,
                }
            )
        return rows


@dataclass(slots=True)
class _Attempt:
    n_trees: int
    n_plots: int
    n_bins: int
    dbh_limit_cm: float
    support_upper_cm: float
    candidates: list[FitCandidate] = field(default_factory=list)
    error: str | None = None

    @property
    def has_success(self) -> bool:
        return any(candidate.is_success for candidate in self.candidates)


def _require_columns(frame: pd.DataFrame) -> None:
    required = {
        "species",
        "dbh_cm",
        "expansion_factor",
        "plot_id",
        "age",
        "dbh_limit_cm",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"observations missing required columns: {', '.join(missing)}")


def _clean_observations(frame: pd.DataFrame, levels: Sequence[str]) -> pd.DataFrame:
    _require_columns(frame)
    data = frame.copy()
    for column in ("dbh_cm", "expansion_factor", "age", "dbh_limit_cm"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["species"] = data["species"].astype(str).str.strip().str.upper()
    for level in levels:
        if level not in data.columns:
            data[level] = "UNKNOWN"
        data[level] = data[level].astype(str).str.strip()
    data = data[
        (data["dbh_cm"] > 0.0)
        & (data["expansion_factor"] > 0.0)
        & (data["dbh_limit_cm"] > 0.0)
        & data["species"].ne("")
        & data["plot_id"].notna()
    ].copy()
    if data.empty:
        raise ValueError("no live, valid trees remain after observation cleaning")
    return data


def _modal_limit(subset: pd.DataFrame) -> float:
    """Return the plot DBH limit shared by most plots in the subset.

    Mixing plots with different truncation limits biases the low tail.  The
    modal limit is the documented compromise: the fit uses the largest
    homogeneous group of plots and records the excluded limit classes.
    """
    per_plot = subset.drop_duplicates("plot_id")["dbh_limit_cm"].round(3)
    if per_plot.empty:
        raise DistributionFitError("cannot choose a DBH limit without plots")
    counts = per_plot.value_counts()
    best = float(counts.index[0])
    tied = [float(value) for value, count in counts.items() if count == counts.iloc[0]]
    return min(tied) if len(tied) > 1 else best


def _bin_edges(limit_cm: float, max_dbh_cm: float, width_cm: float) -> np.ndarray:
    if width_cm <= 0:
        raise ValueError("bin width must be positive")
    if max_dbh_cm <= limit_cm:
        raise DistributionFitError(
            f"maximum DBH {max_dbh_cm:.3f} cm is not above the DBH limit {limit_cm:.3f} cm"
        )
    upper = max(limit_cm + width_cm, float(max_dbh_cm) + width_cm * 0.5)
    edges = np.arange(float(limit_cm), upper + width_cm * 0.5, float(width_cm))
    if edges.size < 4:
        raise DistributionFitError(
            f"DBH range {limit_cm:.3f}-{max_dbh_cm:.3f} cm is too narrow for "
            f"{width_cm:g} cm bins"
        )
    return edges


def _prepare_grouped(
    subset: pd.DataFrame,
    *,
    width_cm: float,
    max_dbh_cm: float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float, float]:
    """Return active bin midpoints, tallies, edges, the limit, and the cap."""
    limit = _modal_limit(subset)
    subset = subset[np.isclose(subset["dbh_limit_cm"], limit, atol=1e-6)]
    if subset.empty:
        raise DistributionFitError(f"no trees at the modal DBH limit {limit:g} cm")
    observed_max = float(subset["dbh_cm"].max())
    cap = float(max_dbh_cm) if max_dbh_cm is not None else observed_max * 1.25
    cap = max(cap, limit + 10.0)
    edges = _bin_edges(limit, observed_max, width_cm)
    counts, _ = np.histogram(
        subset["dbh_cm"].to_numpy(dtype=float),
        bins=edges,
        weights=subset["expansion_factor"].to_numpy(dtype=float),
    )
    populated = counts > 0.0
    if int(populated.sum()) < 3:
        raise DistributionFitError(
            f"only {int(populated.sum())} populated DBH bins at limit {limit:g} cm"
        )
    mids = 0.5 * (edges[:-1] + edges[1:])
    return mids, counts, edges, limit, min(cap, 200.0)


def _pdf_values(
    distribution: str,
    parameters: Mapping[str, float],
    component_weights: Sequence[float] | None,
    component_parameters: Sequence[Mapping[str, float]] | None,
    component_names: Sequence[str] | None,
    points: np.ndarray,
) -> np.ndarray:
    values = np.asarray(points, dtype=float)
    if distribution in MIXTURE_FAMILY_COMPONENTS:
        components = [
            _mixture_component(name, weight, params)
            for name, weight, params in zip(
                component_names or (),
                component_weights or (),
                component_parameters or (),
                strict=False,
            )
        ]
        return np.clip(mixture_pdf(values, components), 1e-12, None)
    definition = get_distribution(distribution)
    return np.clip(definition.pdf(values, dict(parameters)), 1e-12, None)


def _mixture_component(name: str, weight: float, parameters: Mapping[str, float]):
    from nemora.core import MixtureComponentFit

    return MixtureComponentFit(
        name=name,
        weight=float(weight),
        parameters={key: float(value) for key, value in parameters.items()},
    )


def _spike_parameters(
    distribution: str,
    parameters: Mapping[str, float],
) -> tuple[float, str, dict[str, float], float]:
    """Split a spike candidate into weight, component, parameters, and width."""
    component = SPIKE_FAMILY_COMPONENTS[distribution]
    try:
        weight = float(parameters["spike_weight"])
        width = float(parameters["spike_bin_width"])
    except KeyError as error:
        raise DistributionFitError(
            f"{distribution} requires spike_weight and spike_bin_width"
        ) from error
    if not math.isfinite(weight) or not 0.0 < weight < 1.0:
        raise DistributionFitError(
            f"{distribution} spike_weight must be in (0, 1), got {weight!r}"
        )
    if not math.isfinite(width) or width <= 0.0:
        raise DistributionFitError(
            f"{distribution} spike_bin_width must be positive, got {width!r}"
        )
    continuous = {
        key: float(value)
        for key, value in parameters.items()
        if key not in {"spike_weight", "spike_bin_width"}
    }
    if component_is_degenerate(component, 1.0 - weight, continuous):
        raise DistributionFitError(
            f"{distribution} has a degenerate continuous component: {continuous!r}"
        )
    return weight, component, continuous, width


def _continuous_conditional_cdf(
    name: str,
    parameters: Mapping[str, float],
    support_lower: float,
    points: np.ndarray,
) -> np.ndarray:
    """CDF of a Weibull/gamma component conditioned on ``DBH >= support_lower``."""
    values = np.asarray(points, dtype=float)
    if name == "weibull":
        shape = float(parameters["a"])
        scale = float(parameters["beta"])
        lower_mass = float(weibull_min.cdf(support_lower, shape, scale=scale))
        raw = weibull_min.cdf(values, shape, scale=scale)
    elif name == "gamma":
        shape = float(parameters["p"])
        scale = float(parameters["beta"])
        lower_mass = float(gamma_dist.cdf(support_lower, shape, scale=scale))
        raw = gamma_dist.cdf(values, shape, scale=scale)
    else:
        raise DistributionFitError(f"no continuous conditional CDF for {name!r}")
    return np.clip((raw - lower_mass) / max(1.0 - lower_mass, 1e-12), 0.0, 1.0)


def _conditional_cdf(
    distribution: str,
    parameters: Mapping[str, float],
    component_weights: Sequence[float] | None,
    component_parameters: Sequence[Mapping[str, float]] | None,
    component_names: Sequence[str] | None,
    support_lower: float,
    points: np.ndarray,
    *,
    upper_cap: float,
) -> np.ndarray:
    """CDF conditioned on ``DBH >= support_lower``.

    Weibull and gamma use closed forms.  The finite mixture uses
    ``mixture_cdf``.  Generalized beta has no registry CDF, so its conditional
    CDF is integrated on a deterministic grid; this is a diagnostic, not a
    second estimator.
    """
    values = np.asarray(points, dtype=float)
    if distribution in SPIKE_FAMILIES:
        weight, component, continuous, width = _spike_parameters(
            distribution, parameters
        )
        first_bin_upper = float(support_lower) + width
        tail = _continuous_conditional_cdf(
            component, continuous, first_bin_upper, values
        )
        # The spike is a point mass at the lower bound.  Return its left limit
        # (zero) at the bound so the first bin's probability includes the atom.
        cdf = np.where(
            values <= float(support_lower),
            0.0,
            weight + (1.0 - weight) * tail,
        )
        return np.clip(cdf, 0.0, 1.0)
    if distribution == "weibull":
        shape = float(parameters["a"])
        scale = float(parameters["beta"])
        lower_mass = float(weibull_min.cdf(support_lower, shape, scale=scale))
        raw = weibull_min.cdf(values, shape, scale=scale)
        return np.clip((raw - lower_mass) / max(1.0 - lower_mass, 1e-12), 0.0, 1.0)
    if distribution == "gamma":
        shape = float(parameters["p"])
        scale = float(parameters["beta"])
        lower_mass = float(gamma_dist.cdf(support_lower, shape, scale=scale))
        raw = gamma_dist.cdf(values, shape, scale=scale)
        return np.clip((raw - lower_mass) / max(1.0 - lower_mass, 1e-12), 0.0, 1.0)
    if distribution in MIXTURE_FAMILY_COMPONENTS:
        components = [
            _mixture_component(name, weight, params)
            for name, weight, params in zip(
                component_names or (),
                component_weights or (),
                component_parameters or (),
                strict=False,
            )
        ]
        lower_mass = float(mixture_cdf(np.array([support_lower]), components)[0])
        raw = mixture_cdf(values, components)
        return np.clip((raw - lower_mass) / max(1.0 - lower_mass, 1e-12), 0.0, 1.0)

    grid = np.linspace(support_lower, max(upper_cap, float(values.max()) + 1.0), 512)
    densities = _pdf_values(
        distribution,
        parameters,
        component_weights,
        component_parameters,
        component_names,
        grid,
    )
    cdf_grid = cumulative_trapezoid(densities, grid, initial=0.0)
    if not math.isfinite(float(cdf_grid[-1])) or float(cdf_grid[-1]) <= 0.0:
        raise DistributionFitError(
            f"{distribution} produced a degenerate numerical CDF over "
            f"{support_lower:g}-{grid[-1]:g} cm"
        )
    cdf_grid = cdf_grid / float(cdf_grid[-1])
    return np.clip(np.interp(values, grid, cdf_grid), 0.0, 1.0)


def _common_metrics(
    *,
    distribution: str,
    parameters: Mapping[str, float],
    component_weights: Sequence[float] | None,
    component_parameters: Sequence[Mapping[str, float]] | None,
    component_names: Sequence[str] | None,
    tallies: np.ndarray,
    edges: np.ndarray,
    support_lower: float,
    upper_cap: float,
    n_parameters: int,
) -> tuple[float, float, float, float, float]:
    """Return common AIC, AICc, BIC, KS, and log-likelihood."""
    total = float(np.sum(tallies))
    n_bins = int(tallies.size)
    cdf = _conditional_cdf(
        distribution,
        parameters,
        component_weights,
        component_parameters,
        component_names,
        support_lower,
        edges,
        upper_cap=upper_cap,
    )
    probabilities = np.diff(cdf)
    if probabilities.size != n_bins:
        raise DistributionFitError(
            f"{distribution} produced {probabilities.size} bin probabilities for "
            f"{n_bins} tallies"
        )
    # The final bin is open-ended: fold the conditional tail into it so the
    # grouped likelihood covers the whole truncated support.
    probabilities = np.clip(probabilities, 1e-12, None)
    probabilities[-1] += max(0.0, 1.0 - float(cdf[-1]))
    probabilities = probabilities / max(float(np.sum(probabilities)), 1e-12)
    expected = np.clip(total * probabilities, 1e-12, None)
    log_likelihood = float(np.sum(tallies * np.log(expected) - expected))

    k = max(int(n_parameters), 1)
    aic = 2.0 * k - 2.0 * log_likelihood
    aicc = math.nan
    if n_bins - k - 1 > 0:
        aicc = aic + (2.0 * k * (k + 1.0)) / (n_bins - k - 1.0)
    bic = k * math.log(n_bins) - 2.0 * log_likelihood

    observed_cdf = np.cumsum(tallies) / max(total, 1e-12)
    ks = float(np.max(np.abs(observed_cdf - cdf[1:])))
    return float(aic), float(aicc), float(bic), ks, log_likelihood


def _candidate_from_fit(
    result,
    *,
    method: str,
    tallies: np.ndarray,
    edges: np.ndarray,
    support_lower: float,
    upper_cap: float,
) -> FitCandidate:
    parameters = {key: float(value) for key, value in result.parameters.items()}
    n_parameters = len(parameters)
    aic, aicc, bic, ks, log_likelihood = _common_metrics(
        distribution=result.distribution,
        parameters=parameters,
        component_weights=None,
        component_parameters=None,
        component_names=None,
        tallies=tallies,
        edges=edges,
        support_lower=support_lower,
        upper_cap=upper_cap,
        n_parameters=n_parameters,
    )
    return FitCandidate(
        distribution=result.distribution,
        parameters=parameters,
        n_parameters=n_parameters,
        aic=aic,
        aicc=aicc,
        bic=bic,
        ks=ks,
        native_aic=float(result.gof.get("aic", math.nan)),
        native_aicc=float(result.gof.get("aicc", math.nan)),
        native_bic=float(result.gof.get("bic", math.nan)),
        native_ks=float(result.gof.get("ks", math.nan)),
        rss=float(result.gof.get("rss", math.nan)),
        log_likelihood=log_likelihood,
        method=str(result.diagnostics.get("method", method)),
    )


def _canonicalise_mixture(
    names: Sequence[str],
    weights: Sequence[float],
    parameters: Sequence[Mapping[str, float]],
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[dict[str, float], ...]]:
    components = [
        MixtureComponent(name=str(name), weight=float(weight), parameters=dict(params))
        for name, weight, params in zip(names, weights, parameters, strict=True)
    ]
    ordered, _ = canonical_component_order(components)
    return (
        tuple(component.name for component in ordered),
        tuple(component.weight for component in ordered),
        tuple(component.parameters for component in ordered),
    )


def _candidate_from_mixture(
    result,
    *,
    family: str,
    tallies: np.ndarray,
    edges: np.ndarray,
    support_lower: float,
    upper_cap: float,
    method: str = "mixture-em",
) -> FitCandidate:
    names, weights, components = _canonicalise_mixture(
        [component.name for component in result.components],
        [component.weight for component in result.components],
        [component.parameters for component in result.components],
    )
    n_parameters = sum(len(params) for params in components) + max(len(components) - 1, 0)
    aic, aicc, bic, ks, log_likelihood = _common_metrics(
        distribution=family,
        parameters={},
        component_weights=weights,
        component_parameters=components,
        component_names=names,
        tallies=tallies,
        edges=edges,
        support_lower=support_lower,
        upper_cap=upper_cap,
        n_parameters=n_parameters,
    )
    converged = bool(getattr(result, "converged", True))
    raw_diagnostics = getattr(result, "diagnostics", None)
    if hasattr(result, "n_starts"):
        diagnostics_payload = raw_diagnostics
    else:
        diagnostics_payload = {
            "iterations": int(getattr(result, "iterations", 0)),
            "converged": converged,
        }
    return FitCandidate(
        distribution=family,
        component_weights=weights,
        component_parameters=components,
        component_names=names,
        n_parameters=n_parameters,
        aic=aic,
        aicc=aicc,
        bic=bic,
        ks=ks,
        native_aic=2.0 * n_parameters - 2.0 * float(result.log_likelihood),
        native_aicc=math.nan,
        native_bic=math.nan,
        native_ks=math.nan,
        rss=math.nan,
        log_likelihood=log_likelihood,
        method=method,
        converged=converged,
        iterations=int(getattr(result, "iterations", 0)),
        n_starts=int(getattr(result, "n_starts", 0)),
        n_valid_starts=int(getattr(result, "n_valid_starts", 0)),
        ordering_swaps=int(getattr(result, "ordering_swaps", 0)),
        fit_diagnostics=(
            json.dumps(diagnostics_payload, sort_keys=True, default=str)
            if diagnostics_payload
            else ""
        ),
    )


def _candidate_from_spike(
    result: SpikeMixtureFit,
    *,
    tallies: np.ndarray,
    edges: np.ndarray,
    support_lower: float,
    upper_cap: float,
) -> FitCandidate:
    """Build a scored candidate from a spike-plus-tail fit."""
    parameters = {
        "spike_weight": float(result.spike_weight),
        "spike_bin_width": float(result.first_bin_upper - support_lower),
        **{key: float(value) for key, value in result.parameters.items()},
    }
    n_parameters = int(result.n_parameters)
    aic, aicc, bic, ks, log_likelihood = _common_metrics(
        distribution=result.distribution,
        parameters=parameters,
        component_weights=None,
        component_parameters=None,
        component_names=None,
        tallies=tallies,
        edges=edges,
        support_lower=support_lower,
        upper_cap=upper_cap,
        n_parameters=n_parameters,
    )
    diagnostics = {
        "spike_weight": float(result.spike_weight),
        "continuous_component": result.component_name,
        "continuous_parameters": {
            key: float(value) for key, value in result.parameters.items()
        },
        "first_bin_share": float(result.first_bin_share),
        "first_bin_upper_cm": float(result.first_bin_upper),
        "n_bins": int(result.n_bins),
        "n_observations": float(result.n_observations),
        "converged": bool(result.converged),
        "iterations": int(result.iterations),
        "native_log_likelihood": float(result.log_likelihood),
        "constrained": bool(result.constrained),
        "boundary_status": result.boundary_status,
        "at_bound": list(result.at_bound),
        "tail_bounds": {
            key: [float(low), float(high)]
            for key, (low, high) in spike_tail_bounds(result.component_name).items()
        },
        "estimator": result.estimator,
        "start_diagnostics": list(result.diagnostics),
    }
    boundary_reject = result.boundary_status != "ok"
    error = None
    if boundary_reject:
        bounds = {
            key: [float(low), float(high)]
            for key, (low, high) in spike_tail_bounds(result.component_name).items()
        }
        selected_bounds = {key: bounds[key] for key in result.at_bound}
        error = (
            f"boundary_reject: tail parameters {result.parameters!r} sit on "
            f"the {', '.join(result.at_bound) or 'declared'} bound "
            f"{selected_bounds!r}"
        )
    return FitCandidate(
        distribution=result.distribution,
        parameters=parameters,
        n_parameters=n_parameters,
        aic=aic,
        aicc=aicc,
        bic=bic,
        ks=ks,
        native_aic=2.0 * n_parameters - 2.0 * float(result.log_likelihood),
        native_aicc=math.nan,
        native_bic=math.nan,
        native_ks=math.nan,
        rss=math.nan,
        log_likelihood=log_likelihood,
        method=(
            "spike-constrained-grouped-mle"
            if result.constrained
            else "spike-truncated-mstep"
        ),
        fit_status="boundary_reject" if boundary_reject else "ok",
        converged=bool(result.converged),
        iterations=int(result.iterations),
        n_starts=int(result.n_starts),
        n_valid_starts=int(result.n_valid_starts),
        ordering_swaps=0,
        fit_diagnostics=json.dumps(diagnostics, sort_keys=True, default=str),
        spike_weight=float(result.spike_weight),
        spike_component=result.component_name,
        spike_constrained=bool(result.constrained),
        spike_boundary_status=result.boundary_status,
        spike_at_bound=",".join(result.at_bound),
        error=error,
    )


def _failed_candidate(name: str, error: Exception) -> FitCandidate:
    return FitCandidate(
        distribution=name,
        fit_status="failed",
        error=f"{type(error).__name__}: {error}",
        method="failed",
        converged=False,
    )


def _fit_candidates(
    mids: np.ndarray,
    tallies: np.ndarray,
    edges: np.ndarray,
    *,
    support_lower: float,
    upper_cap: float,
    seed: int,
) -> list[FitCandidate]:
    candidates: list[FitCandidate] = []

    try:
        censored = fit_censored_inventory(
            mids,
            tallies,
            support=(float(support_lower), float("inf")),
            distributions=("weibull", "gamma"),
        )
    except Exception as error:  # noqa: BLE001 - report the documented failure
        for name in ("weibull", "gamma"):
            candidates.append(_failed_candidate(name, error))
    else:
        for result in censored:
            candidates.append(
                _candidate_from_fit(
                    result,
                    method="censored-two-stage",
                    tallies=tallies,
                    edges=edges,
                    support_lower=support_lower,
                    upper_cap=upper_cap,
                )
            )

    inventory = InventorySpec(
        name="assortment-dbh",
        sampling="grouped",
        bins=mids,
        tallies=tallies,
        metadata={"grouped": True, "support": [float(support_lower), float("inf")]},
    )
    for name in ("gb1", "gb2"):
        try:
            result = fit_inventory(inventory, [name], {})[0]
        except Exception as error:  # noqa: BLE001 - report the documented failure
            candidates.append(_failed_candidate(name, error))
            continue
        candidates.append(
            _candidate_from_fit(
                result,
                method="grouped-mle",
                tallies=tallies,
                edges=edges,
                support_lower=support_lower,
                upper_cap=upper_cap,
            )
        )

    try:
        mixture = fit_mixture_grouped(
            mids,
            tallies,
            [MixtureComponentSpec("gamma"), MixtureComponentSpec("gamma")],
            max_iter=200,
            tol=1e-6,
            random_state=seed,
        )
        candidates.append(
            _candidate_from_mixture(
                mixture,
                family="mixture",
                tallies=tallies,
                edges=edges,
                support_lower=support_lower,
                upper_cap=upper_cap,
            )
        )
    except Exception as error:  # noqa: BLE001 - report the documented failure
        candidates.append(_failed_candidate("mixture", error))

    for family in ("mix_weibull_2", "mix_weibull_gamma"):
        candidates.append(
            _fit_truncated_family(
                family,
                mids,
                tallies,
                edges,
                support_lower=support_lower,
                upper_cap=upper_cap,
                seed=seed,
                n_starts=2,
                max_iter=250,
            )
        )

    best_mixture_ks = min(
        (
            candidate.ks
            for candidate in candidates
            if candidate.is_success
            and candidate.distribution in MIXTURE_FAMILY_COMPONENTS
        ),
        default=math.inf,
    )
    if best_mixture_ks > _THREE_COMPONENT_KS_TRIGGER:
        candidates.append(
            _fit_truncated_family(
                "mix_weibull_3",
                mids,
                tallies,
                edges,
                support_lower=support_lower,
                upper_cap=upper_cap,
                seed=seed,
                n_starts=1,
                max_iter=150,
            )
        )

    total_tally = float(np.sum(tallies))
    first_bin_share = float(tallies[0] / total_tally) if total_tally > 0.0 else 0.0
    best_simple_ks = min(
        (
            candidate.ks
            for candidate in candidates
            if candidate.is_success
            and candidate.distribution in {"weibull", "gamma"}
        ),
        default=math.inf,
    )
    if tallies.size > 1 and tallies[0] > 0.0 and (
        first_bin_share > SPIKE_FIRST_BIN_SHARE_TRIGGER
        or best_simple_ks > SPIKE_KS_TRIGGER
    ):
        for family in (
            "spike_weibull",
            "spike_gamma",
            "spike_weibull_constrained",
            "spike_gamma_constrained",
        ):
            candidates.append(
                _fit_spike_family(
                    family,
                    mids,
                    tallies,
                    edges,
                    support_lower=support_lower,
                    upper_cap=upper_cap,
                )
            )

    return candidates


def _fit_spike_family(
    family: str,
    mids: np.ndarray,
    tallies: np.ndarray,
    edges: np.ndarray,
    *,
    support_lower: float,
    upper_cap: float,
) -> FitCandidate:
    """Fit one spike-plus-tail family and convert failures to audit rows."""
    component = SPIKE_FAMILY_COMPONENTS[family]
    constrained = family in SPIKE_CONSTRAINED_FAMILIES
    try:
        result = fit_spike_mixture(
            mids,
            tallies,
            component,
            support_lower=support_lower,
            first_bin_upper=float(edges[1]),
            constrained=constrained,
        )
    except Exception as error:  # noqa: BLE001 - keep the failed family for audit
        return _failed_candidate(family, error)
    return _candidate_from_spike(
        result,
        tallies=tallies,
        edges=edges,
        support_lower=support_lower,
        upper_cap=upper_cap,
    )


def _fit_truncated_family(
    family: str,
    mids: np.ndarray,
    tallies: np.ndarray,
    edges: np.ndarray,
    *,
    support_lower: float,
    upper_cap: float,
    seed: int,
    n_starts: int,
    max_iter: int,
) -> FitCandidate:
    """Fit one new mixture family, retrying gamma moments with censored fits."""
    names = MIXTURE_FAMILY_COMPONENTS[family]
    updates = ["moments", "censored"] if "gamma" in names else ["moments"]
    last_error: Exception | None = None
    for gamma_update in updates:
        try:
            result = fit_truncated_mixture(
                mids,
                tallies,
                names,
                support_lower=support_lower,
                n_starts=n_starts,
                max_iter=max_iter,
                tol=1e-6,
                min_weight=1e-4,
                random_state=seed,
                gamma_update=gamma_update,
            )
        except Exception as error:  # noqa: BLE001 - try the documented fallback
            last_error = error
            continue
        return _candidate_from_mixture(
            result,
            family=family,
            tallies=tallies,
            edges=edges,
            support_lower=support_lower,
            upper_cap=upper_cap,
            method=f"truncated-em-{gamma_update}",
        )
    return _failed_candidate(
        family,
        last_error or MixtureFitError(f"no {family} fit was produced"),
    )


def _attempt_fit(
    subset: pd.DataFrame,
    *,
    width_cm: float,
    max_dbh_cm: float | None,
    seed: int,
) -> _Attempt:
    if subset.empty:
        return _Attempt(0, 0, 0, math.nan, math.nan, error="no observations")
    plot_count = int(subset["plot_id"].nunique())
    tree_count = int(len(subset))
    if plot_count < 1 or tree_count < 3:
        return _Attempt(
            tree_count,
            plot_count,
            0,
            math.nan,
            math.nan,
            error="fewer than three trees or no plots",
        )
    try:
        mids, tallies, edges, limit, cap = _prepare_grouped(
            subset,
            width_cm=width_cm,
            max_dbh_cm=max_dbh_cm,
        )
    except DistributionFitError as error:
        return _Attempt(
            tree_count,
            plot_count,
            0,
            math.nan,
            math.nan,
            error=str(error),
        )
    candidates = _fit_candidates(
        mids,
        tallies,
        edges,
        support_lower=limit,
        upper_cap=cap,
        seed=seed,
    )
    attempt = _Attempt(
        n_trees=tree_count,
        n_plots=plot_count,
        n_bins=int((tallies > 0.0).sum()),
        dbh_limit_cm=limit,
        support_upper_cm=cap,
        candidates=candidates,
    )
    if not attempt.has_success:
        attempt.error = "all candidate estimators failed"
    return attempt


def _resolve_target(
    target: Mapping[str, Any],
    observations: pd.DataFrame,
    levels: Sequence[str],
) -> dict[str, str]:
    species = str(target.get("species", "")).strip().upper()
    if not species:
        raise ValueError("each target must define species")
    resolved: dict[str, str] = {"species": species}
    mask = observations["species"].eq(species)
    for level in levels:
        value = target.get(level)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        text = str(value).strip()
        resolved[level] = text
        mask &= observations[level].astype(str).str.strip().eq(text)
    match = observations.loc[mask]
    if match.empty:
        raise DistributionFitError(
            f"no observations match target species {species!r} and levels "
            f"{ {level: resolved.get(level) for level in levels} }"
        )
    first = match.iloc[0]
    for level in levels:
        resolved.setdefault(level, str(first[level]).strip())
    if "target_id" in target:
        resolved["target_id"] = str(target["target_id"])
    return resolved


def fit_distribution_for_target(
    observations: pd.DataFrame,
    target: Mapping[str, Any],
    *,
    scope: str,
    age_window: tuple[float, float] | None = None,
    min_trees: int = 30,
    min_plots: int = 5,
    min_plot_trees: int = 10,
    bin_width_cm: float = 2.0,
    max_dbh_cm: float | None = None,
    random_state: int = 20261115,
    levels: Sequence[str] = DEFAULT_LEVELS,
) -> DistributionFit:
    """Fit one target through the ecodistrict -> ecozone -> default hierarchy.

    The fit stops at the first level that has at least ``min_trees`` trees and
    ``min_plots`` plots and at least three populated bins.  A fit that reaches
    the species-only TSA24 pool is labelled ``default``.
    """
    if min_trees < 3 or min_plots < 1:
        raise ValueError("min_trees must be at least 3 and min_plots at least 1")
    data = _clean_observations(observations, levels)
    if age_window is not None:
        low, high = float(age_window[0]), float(age_window[1])
        if low > high:
            raise ValueError("age_window lower bound cannot exceed upper bound")
        data = data[(data["age"] >= low) & (data["age"] <= high)].copy()
        if data.empty:
            raise DistributionFitError(
                f"no observations in age window {low:g}-{high:g} for {target}"
            )
    resolved = _resolve_target(target, data, levels)
    species = resolved["species"]
    species_data = data[data["species"].eq(species)].copy()
    if species_data.empty:
        raise DistributionFitError(f"no observations for species {species!r}")

    last_attempt: _Attempt | None = None
    for level in levels:
        key = resolved.get(level)
        if key is None:
            continue
        subset = species_data[species_data[level].astype(str).str.strip().eq(key)]
        attempt = _attempt_fit(
            subset,
            width_cm=bin_width_cm,
            max_dbh_cm=max_dbh_cm,
            seed=random_state,
        )
        last_attempt = attempt
        enough = (
            attempt.n_trees >= min_trees
            and attempt.n_plots >= min_plots
            and attempt.n_bins >= 3
        )
        if enough and attempt.has_success:
            evidence = "stratum_fit" if level == levels[0] else "pooled_fit"
            return _build_fit(
                resolved=resolved,
                scope=scope,
                target=target,
                fit_level=level,
                fit_id=f"{level}:{key}|{species}",
                evidence_tier=evidence,
                attempt=attempt,
                age_window=age_window,
            )

    # Species-only default.  This is still a fitted distribution; it is never
    # a silent hard-coded shape.
    attempt = _attempt_fit(
        species_data,
        width_cm=bin_width_cm,
        max_dbh_cm=max_dbh_cm,
        seed=random_state,
    )
    last_attempt = attempt
    if attempt.n_trees < min_plot_trees or attempt.n_bins < 3 or not attempt.has_success:
        detail = attempt.error or "below the default reporting threshold"
        raise DistributionFitError(
            f"no fit is possible for species {species!r} in {scope}: {detail}; "
            f"last attempt n_trees={attempt.n_trees}, n_plots={attempt.n_plots}, "
            f"n_bins={attempt.n_bins}"
        )
    return _build_fit(
        resolved=resolved,
        scope=scope,
        target=target,
        fit_level="tsa24_species",
        fit_id=f"tsa24_species:{species}",
        evidence_tier="default",
        attempt=attempt,
        age_window=age_window,
    )


def _build_fit(
    *,
    resolved: Mapping[str, str],
    scope: str,
    target: Mapping[str, Any],
    fit_level: str,
    fit_id: str,
    evidence_tier: str,
    attempt: _Attempt,
    age_window: tuple[float, float] | None,
) -> DistributionFit:
    target_id = resolved.get(
        "target_id",
        (
            f"{fit_id}|age:{age_window[0]:g}-{age_window[1]:g}"
            if age_window
            else fit_id
        ),
    )
    return DistributionFit(
        scope=scope,
        target_level=str(target.get("target_level", fit_level)),
        target_id=str(target_id),
        fit_level=fit_level,
        fit_id=fit_id,
        species=resolved["species"],
        ecodistrict=resolved.get("ecodistrict"),
        ecoregion=resolved.get("ecoregion"),
        ecoprovince=resolved.get("ecoprovince"),
        ecozone=resolved.get("ecozone"),
        age_window=age_window,
        n_trees=attempt.n_trees,
        n_plots=attempt.n_plots,
        n_bins=attempt.n_bins,
        dbh_limit_cm=attempt.dbh_limit_cm,
        support_upper_cm=attempt.support_upper_cm,
        evidence_tier=evidence_tier,
        candidates=tuple(attempt.candidates),
    )


def fit_dbh_distributions(
    observations: pd.DataFrame,
    *,
    scope: str,
    targets: Iterable[Mapping[str, Any]] | None = None,
    species: Iterable[str] | None = None,
    age_window: tuple[float, float] | None = None,
    min_trees: int = 30,
    min_plots: int = 5,
    min_plot_trees: int = 10,
    bin_width_cm: float = 2.0,
    max_dbh_cm: float | None = None,
    random_state: int = 20261115,
    levels: Sequence[str] = DEFAULT_LEVELS,
    failures: list[dict[str, Any]] | None = None,
) -> list[DistributionFit]:
    """Fit DBH distributions for explicit targets or all ecodistrict groups.

    Parameters
    ----------
    observations:
        One row per live tree.  Required columns: ``species``, ``dbh_cm``,
        ``height_m``, ``expansion_factor``, ``plot_id``, ``age``,
        ``dbh_limit_cm``, and the ecological level columns.
    scope:
        Label written to every output row, for example ``tsa24_all_ages``.
    targets:
        Optional mappings with at least ``species`` and the finest level key.
        Missing higher-level keys are filled from the first matching row.
    species:
        Optional species filter when generating targets automatically.
    """
    data = _clean_observations(observations, levels)
    if age_window is not None:
        low, high = float(age_window[0]), float(age_window[1])
        data = data[(data["age"] >= low) & (data["age"] <= high)].copy()
        if data.empty:
            raise DistributionFitError(
                f"no observations in age window {low:g}-{high:g} for scope {scope!r}"
            )

    requested_species = {str(code).strip().upper() for code in (species or ())}
    if targets is None:
        group_columns = [levels[0], "species"]
        grouped = (
            data.groupby(group_columns, dropna=False)
            .size()
            .reset_index(name="n_trees")
            .sort_values(group_columns)
        )
        generated: list[dict[str, Any]] = []
        for row in grouped.itertuples(index=False):
            if int(row.n_trees) < min_plot_trees:
                continue
            code = str(row.species).strip().upper()
            if requested_species and code not in requested_species:
                continue
            key_value = getattr(row, levels[0])
            if key_value is None or str(key_value).strip() in {"", "nan", "UNKNOWN"}:
                continue
            generated.append(
                {
                    "species": code,
                    levels[0]: str(key_value).strip(),
                    "target_level": levels[0],
                }
            )
        targets = generated

    fits: list[DistributionFit] = []
    for target in targets:
        try:
            fits.append(
                fit_distribution_for_target(
                    data,
                    target,
                    scope=scope,
                    age_window=age_window,
                    min_trees=min_trees,
                    min_plots=min_plots,
                    min_plot_trees=min_plot_trees,
                    bin_width_cm=bin_width_cm,
                    max_dbh_cm=max_dbh_cm,
                    random_state=random_state,
                    levels=levels,
                )
            )
        except DistributionFitError as error:
            if failures is None:
                raise
            failures.append(
                {
                    "scope": scope,
                    "target": dict(target),
                    "age_window": (
                        f"{age_window[0]:g}-{age_window[1]:g}" if age_window else "all"
                    ),
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    return fits

