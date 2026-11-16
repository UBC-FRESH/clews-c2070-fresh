"""Finite mixtures on left-truncated grouped DBH tallies.

``nemora.fit.mixture.fit_mixture_grouped`` is the default finite-mixture path.
Its gamma M-step is a moments update, which is stable.  Its non-gamma M-step
re-fits ``fit_inventory`` on tallies clipped to ``1e-12``; that forces every
bin into the fit, ignores the plot DBH limit, and produces non-converged
Weibull components with runaway shapes.  This module keeps nemora's component
estimator (``fit_censored_inventory``) but runs the EM on the conditional
left-truncated support and canonicalises the component order after every
update.

The module is a fitting boundary, not a new estimator: every M-step delegates
to a documented nemora fit.  It exists because the assortment fits are
left-censored at the plot DBH limit and the component update must honour that
support.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
from nemora.sampling import sample_truncated
from nemora.workflows.censoring import fit_censored_inventory
from scipy.optimize import minimize
from scipy.special import logsumexp
from scipy.stats import gamma as gamma_dist
from scipy.stats import weibull_min

__all__ = [
    "MIXTURE_FAMILY_COMPONENTS",
    "SPIKE_CONSTRAINED_FAMILIES",
    "SPIKE_CONSTRAINED_SCALE_LOWER",
    "SPIKE_CONSTRAINED_SHAPE_LOWER",
    "SPIKE_DIAGNOSTIC_FAMILIES",
    "SPIKE_FAMILIES",
    "SPIKE_FAMILY_COMPONENTS",
    "SPIKE_UNCONSTRAINED_FAMILIES",
    "MixtureComponent",
    "MixtureFit",
    "MixtureFitError",
    "SpikeMixtureFit",
    "canonical_component_order",
    "component_is_degenerate",
    "fit_spike_mixture",
    "fit_truncated_mixture",
    "sample_spike_mixture",
    "spike_tail_bounds",
    "spike_tail_has_finite_mean",
]

#: Component composition of every supported assortment mixture family.
#:
#: ``mixture`` is the pre-existing two-gamma candidate.  The Weibull families
#: are new; ``mix_weibull_3`` is optional in the report because a three-
#: component fit is only an improvement when the extra component is stable.
MIXTURE_FAMILY_COMPONENTS: dict[str, tuple[str, ...]] = {
    "mixture": ("gamma", "gamma"),
    "mix_weibull_2": ("weibull", "weibull"),
    "mix_weibull_gamma": ("weibull", "gamma"),
    "mix_weibull_3": ("weibull", "weibull", "weibull"),
}

#: Spike (point-mass) family to its single continuous component.
#:
#: A spike family is a two-part model, not a finite mixture on overlapping
#: supports.  The spike carries the first DBH bin ``[dbh_limit,
#: dbh_limit + bin_width)``; the continuous component is left-truncated at the
#: first bin's upper edge.  This is the stable EM solution for tallies whose
#: first bin dominates: the spike has no density to fit, and the continuous
#: component sees only the tail it is meant to describe.
#:
#: ``*_constrained`` variants fit the tail with a grouped multinomial MLE under
#: a finite-mean box constraint (shape >= 1, scale >= 0.1 cm).  The
#: unconstrained variants use the documented two-stage censored fit and can
#: reach a power-law boundary; their untruncated mean can be infinite.  Both
#: are synthesis-eligible because ``sample_spike_mixture`` always draws on the
#: finite fitted support ``(first_bin_upper, support_upper]``; the unconstrained
#: rows carry that caveat in their diagnostics and must not be extrapolated
#: beyond ``support_upper``.
SPIKE_FAMILY_COMPONENTS: dict[str, str] = {
    "spike_weibull": "weibull",
    "spike_gamma": "gamma",
    "spike_weibull_constrained": "weibull",
    "spike_gamma_constrained": "gamma",
}
SPIKE_UNCONSTRAINED_FAMILIES = frozenset({"spike_weibull", "spike_gamma"})
#: Backward-compatible name for the unconstrained audit rows.
SPIKE_DIAGNOSTIC_FAMILIES = SPIKE_UNCONSTRAINED_FAMILIES
SPIKE_CONSTRAINED_FAMILIES = frozenset(
    {"spike_weibull_constrained", "spike_gamma_constrained"}
)
SPIKE_FAMILIES = frozenset(SPIKE_FAMILY_COMPONENTS)

#: Box constraints for the constrained grouped-MLE tail.  The lower bounds make
#: the mean finite (Weibull shape >= 1; gamma shape >= 1) and keep the scale
#: physical.  The upper guards are numerical only: a fit that reaches one is a
#: boundary estimate and is rejected exactly like a lower-bound hit.
SPIKE_CONSTRAINED_SHAPE_LOWER = 1.0
SPIKE_CONSTRAINED_SCALE_LOWER = 0.1
_SPIKE_CONSTRAINED_SHAPE_UPPER = 50.0
_SPIKE_CONSTRAINED_SCALE_UPPER = 500.0
_SPIKE_BOUND_RTOL = 1e-6
_SPIKE_BOUND_ATOL = 1e-9


def spike_tail_bounds(component_name: str) -> dict[str, tuple[float, float]]:
    """Return the constrained-tail box for ``weibull`` or ``gamma``."""
    name = str(component_name).strip().lower()
    if name not in _SUPPORTED_COMPONENTS:
        raise ValueError(f"unsupported spike continuous component {component_name!r}")
    shape_key = "a" if name == "weibull" else "p"
    return {
        shape_key: (SPIKE_CONSTRAINED_SHAPE_LOWER, _SPIKE_CONSTRAINED_SHAPE_UPPER),
        "beta": (SPIKE_CONSTRAINED_SCALE_LOWER, _SPIKE_CONSTRAINED_SCALE_UPPER),
    }


def spike_tail_has_finite_mean(
    component_name: str,
    parameters: Mapping[str, float],
) -> bool:
    """Return whether the continuous tail has a finite distribution mean.

    A Weibull mean is finite only when its shape is at least one.  A gamma mean
    is finite for every positive shape, but the prototype treats shape < 1 as
    the same boundary/degenerate regime because the conditional tail collapses
    onto its lower support and cannot be sampled robustly.  The constrained
    candidates always satisfy the finite-mean side by construction.
    """
    name = str(component_name).strip().lower()
    if name == "weibull":
        shape = float(parameters.get("a", math.nan))
    elif name == "gamma":
        shape = float(parameters.get("p", math.nan))
    else:
        return False
    return math.isfinite(shape) and shape >= SPIKE_CONSTRAINED_SHAPE_LOWER


_SUPPORTED_COMPONENTS = frozenset({"weibull", "gamma"})
_DUPLICATE_RELATIVE_TOL = 1e-3


class MixtureFitError(RuntimeError):
    """Raised when every mixture start is degenerate or fails."""


@dataclass(frozen=True, slots=True)
class MixtureComponent:
    """One fitted mixture component in canonical order."""

    name: str
    weight: float
    parameters: dict[str, float]

    @property
    def beta(self) -> float:
        return float(self.parameters["beta"])


@dataclass(frozen=True, slots=True)
class MixtureFit:
    """A fitted mixture plus the convergence and ordering audit trail."""

    distribution: str
    components: tuple[MixtureComponent, ...]
    log_likelihood: float
    n_parameters: int
    converged: bool
    iterations: int
    n_starts: int
    n_valid_starts: int
    ordering_swaps: int
    selected_start: int
    diagnostics: tuple[dict[str, object], ...] = field(default_factory=tuple)

    @property
    def weights(self) -> tuple[float, ...]:
        return tuple(component.weight for component in self.components)

    @property
    def component_names(self) -> tuple[str, ...]:
        return tuple(component.name for component in self.components)

    @property
    def component_parameters(self) -> tuple[dict[str, float], ...]:
        return tuple(component.parameters for component in self.components)


@dataclass(frozen=True, slots=True)
class SpikeMixtureFit:
    """A fitted spike-plus-continuous two-part DBH model.

    ``parameters`` holds only the continuous component's parameters.  The
    spike weight is separate because it is a probability mass, not a density
    parameter.  ``first_bin_upper`` is the upper edge of the first DBH bin; the
    spike is a point mass at ``support_lower`` in the likelihood and is sampled
    uniformly over ``[support_lower, first_bin_upper)`` so the synthetic stems
    retain a physical diameter inside the observed first bin.
    """

    distribution: str
    spike_weight: float
    component_name: str
    parameters: dict[str, float]
    log_likelihood: float
    n_parameters: int
    converged: bool
    iterations: int
    n_starts: int
    n_valid_starts: int
    first_bin_share: float
    first_bin_upper: float
    n_bins: int
    n_observations: float
    constrained: bool = False
    boundary_status: str = "ok"
    at_bound: tuple[str, ...] = ()
    estimator: str = "closed_form_m_step"
    diagnostics: tuple[dict[str, object], ...] = field(default_factory=tuple)


@dataclass(slots=True)
class _StartResult:
    components: tuple[MixtureComponent, ...]
    log_likelihood: float
    converged: bool
    iterations: int
    ordering_swaps: int
    valid: bool
    reason: str
    m_step_failures: int
    convergence_reason: str = "max_iter"


def _validate_grouped(
    bins: np.ndarray,
    tallies: np.ndarray,
    component_names: Sequence[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Validate and parse the grouped tallies at the fitting boundary."""
    x = np.asarray(bins, dtype=float)
    y = np.asarray(tallies, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size != y.size:
        raise ValueError("bins and tallies must be one-dimensional and aligned")
    if x.size < 3 or int(np.sum(y > 0.0)) < 3:
        raise ValueError("mixture fitting needs at least three populated bins")
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)):
        raise ValueError("bins and tallies must be finite")
    if np.any(y < 0.0):
        raise ValueError("tallies must be non-negative")
    if float(np.sum(y)) <= 0.0:
        raise ValueError("tallies must sum to a positive value")
    if len(component_names) < 2:
        raise ValueError("a finite mixture needs at least two components")
    unknown = sorted(set(component_names).difference(_SUPPORTED_COMPONENTS))
    if unknown:
        raise ValueError(f"unsupported mixture components: {', '.join(unknown)}")
    return x, y


def _component_cdf(name: str, parameters: Mapping[str, float], value: float) -> float:
    if name == "weibull":
        return float(weibull_min.cdf(value, parameters["a"], scale=parameters["beta"]))
    if name == "gamma":
        return float(gamma_dist.cdf(value, parameters["p"], scale=parameters["beta"]))
    raise ValueError(f"no mixture CDF for component {name!r}")


def _component_retained_mass(
    name: str, parameters: Mapping[str, float], support_lower: float
) -> float:
    """Probability mass at or above the truncation point."""
    return max(1.0 - _component_cdf(name, parameters, support_lower), 1e-12)


def _component_pdf(name: str, parameters: Mapping[str, float], values: np.ndarray) -> np.ndarray:
    if name == "weibull":
        density = weibull_min.pdf(values, parameters["a"], scale=parameters["beta"])
    elif name == "gamma":
        density = gamma_dist.pdf(values, parameters["p"], scale=parameters["beta"])
    else:
        raise ValueError(f"no mixture PDF for component {name!r}")
    return np.clip(np.asarray(density, dtype=float), 1e-300, None)


def _moment_parameters(name: str, values: np.ndarray, weights: np.ndarray) -> dict[str, float]:
    """Return a deterministic moment start for a component."""
    total = float(np.sum(weights))
    if total <= 0.0:
        weights = np.ones_like(weights, dtype=float)
        total = float(weights.size)
    mean = float(np.sum(weights * values) / total)
    variance = float(np.sum(weights * (values - mean) ** 2) / total)
    variance = max(variance, 1e-6)
    if name == "gamma":
        return {"p": max(mean**2 / variance, 1e-6), "beta": max(variance / mean, 1e-6)}
    shape = max((mean**2) / variance, 1e-6)
    return {"a": min(max(shape, 1e-3), 20.0), "beta": max(mean, 1e-6)}


def _initial_components(
    x: np.ndarray,
    y: np.ndarray,
    names: Sequence[str],
    support_lower: float,
) -> tuple[tuple[MixtureComponent, ...], int]:
    """Split the weighted support into contiguous chunks and fit each chunk."""
    order = np.argsort(x)
    chunks = np.array_split(order, len(names))
    total = float(np.sum(y))
    components: list[MixtureComponent] = []
    failures = 0
    for name, indices in zip(names, chunks, strict=True):
        chunk_x = x[indices]
        chunk_y = y[indices]
        positive = chunk_y > 0.0
        parameters: dict[str, float] | None = None
        if int(positive.sum()) >= 3:
            try:
                result = fit_censored_inventory(
                    chunk_x[positive],
                    chunk_y[positive],
                    support=(float(support_lower), float("inf")),
                    distributions=(name,),
                )[0]
                parameters = {
                    key: float(value)
                    for key, value in result.parameters.items()
                    if key != "s"
                }
            except Exception:  # noqa: BLE001 - a failed start falls back to moments
                parameters = None
        if parameters is None or component_is_degenerate(name, 1.0, parameters):
            parameters = _moment_parameters(name, chunk_x, chunk_y)
            failures += 1
        weight = float(np.sum(chunk_y) / total) if total > 0.0 else 1.0 / len(names)
        components.append(MixtureComponent(name=name, weight=max(weight, 1e-12), parameters=parameters))
    return _normalise_weights(tuple(components)), failures


def _normalise_weights(
    components: Sequence[MixtureComponent],
) -> tuple[MixtureComponent, ...]:
    total = float(sum(component.weight for component in components))
    if not math.isfinite(total) or total <= 0.0:
        raise MixtureFitError("mixture weights do not sum to a positive value")
    return tuple(
        MixtureComponent(
            name=component.name,
            weight=float(component.weight / total),
            parameters=dict(component.parameters),
        )
        for component in components
    )


def canonical_component_order(
    components: Sequence[MixtureComponent],
) -> tuple[tuple[MixtureComponent, ...], int]:
    """Sort components by scale (``beta``), then shape, carrying weights.

    The finite-mixture likelihood is invariant to component labels.  Sorting
    after every M-step makes the output independent of the initial label order
    and prevents label switching from appearing as a spurious parameter jump.
    """
    order = sorted(
        range(len(components)),
        key=lambda index: (
            components[index].parameters.get("beta", math.inf),
            components[index].parameters.get(
                "a", components[index].parameters.get("p", math.inf)
            ),
            components[index].name,
        ),
    )
    swaps = sum(1 for position, index in enumerate(order) if position != index)
    return tuple(components[index] for index in order), swaps


def component_is_degenerate(
    name: str,
    weight: float,
    parameters: Mapping[str, float],
) -> bool:
    """Return whether a component cannot represent a valid density."""
    if name not in _SUPPORTED_COMPONENTS:
        return True
    if not math.isfinite(weight) or weight <= 0.0:
        return True
    if not parameters:
        return True
    for value in parameters.values():
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            return True
    if name == "weibull":
        return "a" not in parameters or "beta" not in parameters
    return "p" not in parameters or "beta" not in parameters


def _components_are_duplicates(components: Sequence[MixtureComponent]) -> bool:
    for left in range(len(components)):
        for right in range(left + 1, len(components)):
            first, second = components[left], components[right]
            if first.name != second.name:
                continue
            if set(first.parameters) != set(second.parameters):
                continue
            differences = [
                abs(first.parameters[key] - second.parameters[key])
                / max(abs(second.parameters[key]), 1e-12)
                for key in second.parameters
            ]
            if max(differences) < _DUPLICATE_RELATIVE_TOL:
                return True
    return False


def _observed_log_likelihood(
    x: np.ndarray,
    y: np.ndarray,
    components: Sequence[MixtureComponent],
    support_lower: float,
) -> float:
    log_density = np.vstack(
        [
            np.log(_component_pdf(component.name, component.parameters, x))
            + math.log(component.weight)
            for component in components
        ]
    )
    log_mixture = logsumexp(log_density, axis=0)
    retained = float(
        sum(
            component.weight
            * _component_retained_mass(
                component.name, component.parameters, support_lower
            )
            for component in components
        )
    )
    if not math.isfinite(retained) or retained <= 0.0:
        return -math.inf
    value = float(np.sum(y * log_mixture) - np.sum(y) * math.log(retained))
    return value if math.isfinite(value) else -math.inf


def _run_em(
    x: np.ndarray,
    y: np.ndarray,
    initial: Sequence[MixtureComponent],
    *,
    support_lower: float,
    max_iter: int,
    tol: float,
    min_weight: float,
    gamma_update: str,
) -> _StartResult:
    components, swaps = canonical_component_order(_normalise_weights(initial))
    total = float(np.sum(y))
    previous_ll = -math.inf
    ll_history: list[float] = []
    iterations = 0
    m_step_failures = 0
    converged = False
    convergence_reason = "max_iter"
    for iteration in range(1, max_iter + 1):
        iterations = iteration
        previous_components = components
        retained = np.array(
            [
                _component_retained_mass(
                    component.name, component.parameters, support_lower
                )
                for component in components
            ],
            dtype=float,
        )
        if np.any(~np.isfinite(retained)) or np.any(retained <= 0.0):
            return _StartResult(
                components=components,
                log_likelihood=-math.inf,
                converged=False,
                iterations=iterations,
                ordering_swaps=swaps,
                valid=False,
                reason="component has no retained mass above the support",
                m_step_failures=m_step_failures,
            )
        log_density = np.vstack(
            [
                np.log(_component_pdf(component.name, component.parameters, x))
                + math.log(component.weight)
                - math.log(retained[index])
                for index, component in enumerate(components)
            ]
        )
        responsibilities = np.exp(log_density - logsumexp(log_density, axis=0))
        expected = responsibilities * y
        posterior = np.sum(expected, axis=1) + 0.1
        normaliser = total + len(components) * 0.1
        weights = np.clip(posterior / normaliser, 1e-12, None)
        weights = weights / float(np.sum(weights))

        updated: list[MixtureComponent] = []
        for index, component in enumerate(components):
            mask = expected[index] > max(total * 1e-6, 1e-12)
            parameters = component.parameters
            if int(mask.sum()) >= 3:
                if (
                    component.name == "gamma"
                    and gamma_update == "moments"
                    and retained[index] >= 0.5
                ):
                    # Match nemora's documented gamma moments update.  The
                    # gamma components in these fits retain almost all their
                    # mass above the plot limit, so the moment update is both
                    # stable and much faster than a censored refit.
                    parameters = _moment_parameters("gamma", x[mask], expected[index][mask])
                else:
                    try:
                        result = fit_censored_inventory(
                            x[mask],
                            expected[index][mask],
                            support=(float(support_lower), float("inf")),
                            distributions=(component.name,),
                        )[0]
                        candidate = {
                            key: float(value)
                            for key, value in result.parameters.items()
                            if key != "s"
                        }
                        if component_is_degenerate(
                            component.name, float(weights[index]), candidate
                        ):
                            raise ValueError("component update is degenerate")
                        parameters = candidate
                    except Exception:  # noqa: BLE001 - retain the previous component
                        m_step_failures += 1
            updated.append(
                MixtureComponent(
                    name=component.name,
                    weight=float(weights[index]),
                    parameters=dict(parameters),
                )
            )
        ordered, step_swaps = canonical_component_order(updated)
        swaps += step_swaps
        components = ordered
        log_likelihood = _observed_log_likelihood(x, y, components, support_lower)
        if not math.isfinite(log_likelihood):
            return _StartResult(
                components=components,
                log_likelihood=-math.inf,
                converged=False,
                iterations=iterations,
                ordering_swaps=swaps,
                valid=False,
                reason="non-finite observed log-likelihood",
                m_step_failures=m_step_failures,
                convergence_reason="invalid",
            )
        weight_delta = max(
            abs(previous.weight - current.weight)
            for previous, current in zip(previous_components, updated, strict=True)
        )
        parameter_delta = max(
            abs(previous.parameters[key] - current.parameters[key])
            / max(abs(previous.parameters[key]), 1e-12)
            for previous, current in zip(previous_components, updated, strict=True)
            for key in current.parameters
        )
        ll_history.append(log_likelihood)
        if (
            abs(log_likelihood - previous_ll) <= tol * (1.0 + abs(log_likelihood))
            and weight_delta <= 1e-8
            and parameter_delta <= 1e-6
        ):
            converged = True
            convergence_reason = "tolerance"
            break
        if len(ll_history) >= 12:
            window = ll_history[-12:]
            stalled = max(window) - min(window) <= 10.0 * tol * (
                1.0 + abs(log_likelihood)
            )
            if stalled and weight_delta <= 1e-4 and parameter_delta <= 1e-3:
                converged = True
                convergence_reason = "stalled"
                break
        previous_ll = log_likelihood

    valid, reason = _validate_result(components, log_likelihood, min_weight)
    return _StartResult(
        components=components,
        log_likelihood=log_likelihood,
        converged=converged,
        iterations=iterations,
        ordering_swaps=swaps,
        valid=valid,
        reason=reason,
        m_step_failures=m_step_failures,
        convergence_reason=convergence_reason,
    )


def _validate_result(
    components: Sequence[MixtureComponent],
    log_likelihood: float,
    min_weight: float,
) -> tuple[bool, str]:
    if not math.isfinite(log_likelihood):
        return False, "non-finite log-likelihood"
    if any(component.weight < min_weight for component in components):
        return False, f"component weight below min_weight={min_weight:g}"
    if any(
        component_is_degenerate(component.name, component.weight, component.parameters)
        for component in components
    ):
        return False, "non-finite or non-positive component parameter"
    if _components_are_duplicates(components):
        return False, "duplicate components"
    if not math.isclose(sum(component.weight for component in components), 1.0, abs_tol=1e-6):
        return False, "component weights do not sum to one"
    return True, "ok"


def _jitter(
    components: Sequence[MixtureComponent],
    rng: np.random.Generator,
) -> tuple[MixtureComponent, ...]:
    weights = rng.dirichlet(np.full(len(components), 5.0))
    jittered = [
        MixtureComponent(
            name=component.name,
            weight=float(weights[index]),
            parameters={
                key: max(value * float(rng.uniform(0.75, 1.30)), 1e-6)
                for key, value in component.parameters.items()
            },
        )
        for index, component in enumerate(components)
    ]
    return _normalise_weights(jittered)


def fit_truncated_mixture(
    bins: Sequence[float],
    tallies: Sequence[float],
    component_names: Sequence[str],
    *,
    support_lower: float,
    n_starts: int = 2,
    max_iter: int = 250,
    tol: float = 1e-6,
    min_weight: float = 1e-4,
    random_state: int = 20261115,
    gamma_update: str = "moments",
) -> MixtureFit:
    """Fit a left-truncated finite mixture to grouped tallies.

    Parameters
    ----------
    bins, tallies:
        Active bin midpoints and expansion-factor tallies.  Zero-tally bins
        are allowed; they are excluded from each component M-step.
    component_names:
        Component family names in start order; ``weibull`` or ``gamma``.
    support_lower:
        Plot DBH limit.  Every M-step and the observed likelihood condition on
        ``DBH >= support_lower``.
    n_starts:
        Number of deterministic starts.  The first is the weighted-quantile
        chunk split; later starts jitter that split with a seeded RNG.
    gamma_update:
        ``moments`` uses the documented nemora gamma moment update when the
        component retains at least half its mass above the limit;
        ``censored`` always uses the censored refit.  Weibull components always
        use the censored refit.
    """
    names = tuple(str(name).strip().lower() for name in component_names)
    x, y = _validate_grouped(np.asarray(bins), np.asarray(tallies), names)
    lower = float(support_lower)
    if not math.isfinite(lower) or lower < 0.0:
        raise ValueError("support_lower must be finite and non-negative")
    if float(np.min(x)) < lower - 1e-9:
        raise ValueError("all bin midpoints must lie at or above support_lower")
    if int(n_starts) < 1:
        raise ValueError("n_starts must be at least one")
    if int(max_iter) < 1:
        raise ValueError("max_iter must be at least one")
    if not 0.0 < float(min_weight) < 1.0:
        raise ValueError("min_weight must be in (0, 1)")
    if float(tol) <= 0.0:
        raise ValueError("tol must be positive")
    if gamma_update not in {"moments", "censored"}:
        raise ValueError("gamma_update must be 'moments' or 'censored'")

    base, base_failures = _initial_components(x, y, names, lower)
    rng = np.random.default_rng(int(random_state))
    starts: list[_StartResult] = []
    for start in range(int(n_starts)):
        initial = base if start == 0 else _jitter(base, rng)
        result = _run_em(
            x,
            y,
            initial,
            support_lower=lower,
            max_iter=int(max_iter),
            tol=float(tol),
            min_weight=float(min_weight),
            gamma_update=gamma_update,
        )
        starts.append(result)

    valid = [result for result in starts if result.valid]
    if not valid:
        details = "; ".join(
            f"start {index}: {result.reason}" for index, result in enumerate(starts)
        )
        raise MixtureFitError(f"all mixture starts are degenerate: {details}")
    converged = [result for result in valid if result.converged]
    accept_unconverged = not converged
    pool = [
        (index, result)
        for index, result in enumerate(starts)
        if result.valid and (result.converged or accept_unconverged)
    ]
    selected_start, selected = max(pool, key=lambda item: item[1].log_likelihood)
    components = selected.components
    n_parameters = sum(len(component.parameters) for component in components) + len(components) - 1
    diagnostics = tuple(
        {
            "start": index,
            "converged": result.converged,
            "valid": result.valid,
            "iterations": result.iterations,
            "log_likelihood": result.log_likelihood,
            "m_step_failures": result.m_step_failures,
            "ordering_swaps": result.ordering_swaps,
            "convergence": result.convergence_reason,
            "reason": result.reason,
            "initial_constraint_failures": base_failures if index == 0 else 0,
        }
        for index, result in enumerate(starts)
    )
    return MixtureFit(
        distribution="mixture",
        components=components,
        log_likelihood=float(selected.log_likelihood),
        n_parameters=n_parameters,
        converged=bool(selected.converged),
        iterations=int(selected.iterations),
        n_starts=len(starts),
        n_valid_starts=len(valid),
        ordering_swaps=int(selected.ordering_swaps),
        selected_start=int(selected_start),
        diagnostics=diagnostics,
    )


def _validate_spike_grouped(
    bins: np.ndarray,
    tallies: np.ndarray,
    component_name: str,
) -> tuple[str, np.ndarray, np.ndarray]:
    """Validate and parse grouped tallies for a spike mixture."""
    name = str(component_name).strip().lower()
    if name not in _SUPPORTED_COMPONENTS:
        raise ValueError(
            f"spike continuous component must be one of "
            f"{', '.join(sorted(_SUPPORTED_COMPONENTS))}, got {component_name!r}"
        )
    x = np.asarray(bins, dtype=float)
    y = np.asarray(tallies, dtype=float)
    if x.ndim != 1 or y.ndim != 1 or x.size != y.size:
        raise ValueError("bins and tallies must be one-dimensional and aligned")
    if x.size < 4:
        raise ValueError("spike fitting needs at least four DBH bins")
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)):
        raise ValueError("bins and tallies must be finite")
    if np.any(y < 0.0):
        raise ValueError("tallies must be non-negative")
    if float(np.sum(y)) <= 0.0:
        raise ValueError("tallies must sum to a positive value")
    order = np.argsort(x)
    return name, x[order], y[order]


def _component_interval_mass(
    name: str,
    parameters: Mapping[str, float],
    lower: float,
    upper: float,
    *,
    support_lower: float,
) -> float:
    """Conditional mass in ``[lower, upper]`` given ``DBH >= support_lower``."""
    retained = _component_retained_mass(name, parameters, support_lower)
    if math.isinf(upper):
        raw = 1.0 - _component_cdf(name, parameters, lower)
    else:
        raw = _component_cdf(name, parameters, upper) - _component_cdf(name, parameters, lower)
    return max(float(raw) / retained, 0.0)


def _spike_bin_edges(
    x: np.ndarray,
    support_lower: float,
    first_bin_upper: float,
) -> np.ndarray:
    """Reconstruct bin edges with the first bin closed at ``support_lower``.

    The fitting boundary receives bin midpoints.  The first bin is pinned to
    ``[support_lower, first_bin_upper)``; later bins are rebuilt from the
    midpoint spacing.  The final edge is open-ended and is folded into the last
    bin by the likelihood.
    """
    width = float(x[1] - x[0])
    if width <= 0.0:
        raise ValueError("bin midpoints must be strictly increasing")
    inner = x[2:] - 0.5 * width if x.size > 2 else np.empty(0, dtype=float)
    edges = np.concatenate(
        [
            np.array([float(support_lower), float(first_bin_upper)], dtype=float),
            inner,
            np.array([float(x[-1] + 0.5 * width)], dtype=float),
        ]
    )
    if np.any(np.diff(edges) <= 0.0):
        raise ValueError("reconstructed bin edges are not strictly increasing")
    return edges


def _spike_bin_probabilities(
    x: np.ndarray,
    spike_weight: float,
    component_name: str,
    parameters: Mapping[str, float],
    *,
    support_lower: float,
    first_bin_upper: float,
) -> np.ndarray:
    """Return the grouped bin probabilities of the spike two-part model."""
    edges = _spike_bin_edges(x, support_lower, first_bin_upper)
    probabilities = np.zeros(x.size, dtype=float)
    probabilities[0] = float(spike_weight)
    continuous_weight = 1.0 - float(spike_weight)
    for index in range(1, x.size):
        upper = math.inf if index == x.size - 1 else float(edges[index + 1])
        mass = _component_interval_mass(
            component_name,
            parameters,
            float(edges[index]),
            upper,
            support_lower=float(first_bin_upper),
        )
        probabilities[index] = continuous_weight * mass
    total = float(np.sum(probabilities))
    if not math.isfinite(total) or total <= 0.0:
        raise MixtureFitError("spike mixture produced degenerate bin probabilities")
    return probabilities / total


def _spike_log_likelihood(
    y: np.ndarray,
    probabilities: np.ndarray,
) -> float:
    """Grouped multinomial log-likelihood (the multinomial coefficient is fixed)."""
    values = y * np.log(np.clip(probabilities, 1e-300, None))
    value = float(np.sum(values))
    return value if math.isfinite(value) else -math.inf


def _component_logcdf(
    name: str,
    parameters: Mapping[str, float],
    values: np.ndarray,
) -> np.ndarray:
    if name == "weibull":
        return np.asarray(
            weibull_min.logcdf(values, parameters["a"], scale=parameters["beta"]),
            dtype=float,
        )
    if name == "gamma":
        return np.asarray(
            gamma_dist.logcdf(values, parameters["p"], scale=parameters["beta"]),
            dtype=float,
        )
    raise ValueError(f"no mixture log-CDF for component {name!r}")


def _component_logsf(
    name: str,
    parameters: Mapping[str, float],
    values: np.ndarray,
) -> np.ndarray:
    if name == "weibull":
        return np.asarray(
            weibull_min.logsf(values, parameters["a"], scale=parameters["beta"]),
            dtype=float,
        )
    if name == "gamma":
        return np.asarray(
            gamma_dist.logsf(values, parameters["p"], scale=parameters["beta"]),
            dtype=float,
        )
    raise ValueError(f"no mixture log-survival for component {name!r}")


def _logdiffexp(log_a: np.ndarray | float, log_b: np.ndarray | float):
    """Return ``log(exp(log_a) - exp(log_b))`` for ``log_a >= log_b``."""
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        return np.asarray(log_a) + np.log1p(-np.exp(np.asarray(log_b) - np.asarray(log_a)))


def _constrained_tail_log_probabilities(
    name: str,
    parameters: Mapping[str, float],
    edges: np.ndarray,
    first_bin_upper: float,
) -> np.ndarray:
    """Log conditional bin probabilities above the first DBH bin.

    The vector has one entry per tail bin.  It is the exact grouped likelihood
    used by the constrained MLE and matches ``_component_interval_mass``'s
    conditioning on ``DBH >= first_bin_upper``.  CDF or survival differences are
    selected by which tail is numerically stable; the open-ended last bin uses
    the survival function directly.
    """
    lower = np.asarray(edges[1:-1], dtype=float)
    upper = np.asarray(edges[2:], dtype=float)
    if lower.size != upper.size or lower.size < 1:
        raise ValueError("constrained tail needs at least one bin above the first bin")
    upper[-1] = math.inf
    log_sf1 = float(_component_logsf(name, parameters, np.array([first_bin_upper]))[0])
    if not math.isfinite(log_sf1):
        return np.full(lower.size, -math.inf)

    finite = np.isfinite(upper)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        log_cdf_lower = _component_logcdf(name, parameters, lower)
        log_cdf_upper = _component_logcdf(name, parameters, upper[finite])
        log_sf_lower = _component_logsf(name, parameters, lower)
        log_sf_upper = _component_logsf(name, parameters, upper[finite])
        cdf_branch = _logdiffexp(log_cdf_upper, log_cdf_lower[finite])
        sf_branch = _logdiffexp(log_sf_lower[finite], log_sf_upper)
        finite_log = np.where(log_cdf_upper < -math.log(2.0), cdf_branch, sf_branch)

    finite_log = np.nan_to_num(
        np.asarray(finite_log, dtype=float),
        nan=-math.inf,
        posinf=0.0,
        neginf=-math.inf,
    )
    last_log = float(_component_logsf(name, parameters, np.array([lower[-1]]))[0]) - log_sf1
    log_probabilities = np.empty(lower.size, dtype=float)
    log_probabilities[:-1] = finite_log - log_sf1
    log_probabilities[-1] = last_log
    return np.minimum(log_probabilities, 0.0)


def _tail_bound_hits(
    component_name: str,
    parameters: Mapping[str, float],
) -> tuple[str, ...]:
    """Return parameter names whose values sit on a constrained-tail bound."""
    hits: list[str] = []
    for key, (lower, upper) in spike_tail_bounds(component_name).items():
        value = float(parameters[key])
        if not math.isfinite(value):
            hits.append(key)
            continue
        if math.isclose(value, lower, rel_tol=_SPIKE_BOUND_RTOL, abs_tol=_SPIKE_BOUND_ATOL):
            hits.append(key)
        elif math.isclose(
            value, upper, rel_tol=_SPIKE_BOUND_RTOL, abs_tol=_SPIKE_BOUND_ATOL
        ):
            hits.append(key)
    return tuple(hits)


def _constrained_tail_objective(
    theta: Sequence[float],
    component_name: str,
    edges: np.ndarray,
    tail_tallies: np.ndarray,
    first_bin_upper: float,
) -> float:
    """Negative grouped multinomial log-likelihood for the tail."""
    shape_key = "a" if component_name == "weibull" else "p"
    parameters = {
        shape_key: float(theta[0]),
        "beta": float(theta[1]),
    }
    if (
        not math.isfinite(parameters[shape_key])
        or not math.isfinite(parameters["beta"])
        or parameters[shape_key] <= 0.0
        or parameters["beta"] <= 0.0
    ):
        return 1e100
    log_probabilities = _constrained_tail_log_probabilities(
        component_name, parameters, edges, first_bin_upper
    )
    safe = np.where(tail_tallies > 0.0, log_probabilities, 0.0)
    if np.any(~np.isfinite(safe)):
        return 1e100
    value = -float(np.dot(tail_tallies, safe))
    return value if math.isfinite(value) else 1e100


def _constrained_tail_starts(
    component_name: str,
    edges: np.ndarray,
    tail_tallies: np.ndarray,
    first_bin_upper: float,
) -> tuple[tuple[float, float], ...]:
    """Deterministic starting grid for the bounded tail MLE."""
    populated = tail_tallies > 0.0
    if int(np.sum(populated)) < 3:
        raise MixtureFitError(
            "constrained spike mixture needs at least three populated tail bins"
        )
    mids = 0.5 * (edges[:-1] + edges[1:])
    tail_mids = mids[1:]
    weights = tail_tallies[populated]
    mean_tail = float(np.dot(tail_mids[populated], weights) / max(float(weights.sum()), 1e-12))
    mean_tail = max(mean_tail, float(first_bin_upper) + 0.5)
    scale0 = max(float(first_bin_upper) * 0.5, mean_tail * 0.6, 1.0)
    shape_lower = SPIKE_CONSTRAINED_SHAPE_LOWER
    return (
        (shape_lower + 1e-6, scale0),
        (1.2, scale0),
        (1.6, scale0 * 0.8),
        (2.5, scale0 * 0.5),
        (4.0, scale0 * 0.35),
    )


def _fit_constrained_tail(
    component_name: str,
    edges: np.ndarray,
    tail_tallies: np.ndarray,
    first_bin_upper: float,
) -> tuple[
    dict[str, float],
    float,
    int,
    int,
    int,
    tuple[str, ...],
    tuple[dict[str, object], ...],
]:
    """Fit the continuous tail with a bounded grouped multinomial MLE.

    ``fit_censored_inventory`` is a two-stage estimator: its stage-one scaling
    factor is fixed before the shape/scale fit and it does not expose the exact
    grouped likelihood under a box constraint.  The spike tail therefore uses a
    small, documented local MLE at this boundary: L-BFGS-B on the exact grouped
    bin likelihood, with a deterministic start grid.  This is the estimator
    layer for the constrained two-part model, not a replacement for nemora's
    unconditional fits.
    """
    name = str(component_name).strip().lower()
    bounds_map = spike_tail_bounds(name)
    shape_key = "a" if name == "weibull" else "p"
    bounds = [bounds_map[shape_key], bounds_map["beta"]]
    starts = _constrained_tail_starts(
        name, edges, tail_tallies, first_bin_upper
    )
    results: list[dict[str, object]] = []
    valid: list[tuple[float, object]] = []
    for start_index, start in enumerate(starts):
        result = minimize(
            _constrained_tail_objective,
            np.array(start, dtype=float),
            args=(name, edges, tail_tallies, first_bin_upper),
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 5000, "ftol": 1e-12, "gtol": 1e-8, "maxls": 80},
        )
        parameters = {
            shape_key: float(result.x[0]),
            "beta": float(result.x[1]),
        }
        at_bound = _tail_bound_hits(name, parameters)
        results.append(
            {
                "start": start_index,
                "initial": [float(value) for value in start],
                "parameters": parameters,
                "at_bound": list(at_bound),
                "log_likelihood": float(-result.fun) if math.isfinite(result.fun) else -math.inf,
                "converged": bool(result.success),
                "iterations": int(getattr(result, "nit", 0)),
                "message": str(result.message),
            }
        )
        if result.success and math.isfinite(result.fun):
            valid.append((float(result.fun), result))

    if not valid:
        messages = "; ".join(
            f"start {item['start']}: {item['message']}" for item in results
        )
        raise MixtureFitError(
            f"constrained {name} tail did not converge: {messages}"
        )

    best_fun, best = min(valid, key=lambda item: item[0])
    parameters = {
        shape_key: float(best.x[0]),
        "beta": float(best.x[1]),
    }
    at_bound = _tail_bound_hits(name, parameters)
    return (
        parameters,
        -float(best_fun),
        int(getattr(best, "nit", 0)),
        len(starts),
        len(valid),
        at_bound,
        tuple(results),
    )



def fit_spike_mixture(
    bins: Sequence[float],
    tallies: Sequence[float],
    component_name: str,
    *,
    support_lower: float,
    first_bin_upper: float | None = None,
    min_spike_weight: float = 0.001,
    max_spike_weight: float = 0.95,
    constrained: bool = False,
    random_state: int = 20261115,
) -> SpikeMixtureFit:
    """Fit a spike at the lower DBH bound plus one truncated continuous tail.

    The model is the stable two-part (hurdle) limit of a zero-inflated mixture:

    * the spike carries the first bin ``[dbh_limit, first_bin_upper)`` as a
      point mass at ``dbh_limit`` for likelihood purposes;
    * the continuous component is left-truncated at ``first_bin_upper`` and
      fitted to the remaining tallies with ``fit_censored_inventory``.

    Because the two parts have disjoint support, the E-step is exact:
    first-bin tallies have spike responsibility one and all later bins have
    continuous responsibility one.  The M-step therefore sets the spike
    weight to the observed first-bin share (the multinomial MLE for a
    disjoint-support spike) and refits the continuous component once.  This
    avoids the weak identifiability of a spike and a continuous component that
    both cover the first bin, which is what made earlier continuous mixtures
    collapse onto a boundary spike.

    With ``constrained=True`` the tail is fitted only to bins above the first
    bin by a grouped multinomial MLE under the finite-mean box

    * Weibull ``a >= 1.0`` and ``beta >= 0.1`` cm,
    * gamma ``p >= 1.0`` and ``beta >= 0.1`` cm,

    with generous upper guards used only to detect a degenerate runaway.  A
    fit whose tail parameters sit on any bound is returned with
    ``boundary_status="boundary_reject"`` and ``at_bound`` naming the
    parameters; callers must not select it.  AIC counts the spike weight plus
    the two continuous parameters, so the constrained and unconstrained
    variants are comparable at three parameters.

    Parameters
    ----------
    bins, tallies:
        Active bin midpoints and expansion-factor tallies.  The first bin must
        be populated and must start at ``support_lower``.
    component_name:
        ``weibull`` or ``gamma`` for the continuous tail.
    support_lower:
        Plot DBH limit, the spike location and lower support bound.
    first_bin_upper:
        Upper edge of the first bin.  Defaults to the first midpoint plus half
        the first midpoint spacing.
    min_spike_weight, max_spike_weight:
        Admissible spike range.  A solution outside the range is rejected as
        degenerate rather than clipped silently.
    constrained:
        Use the bounded grouped MLE for a finite-mean tail instead of the
        unconstrained two-stage censored fit.  The unconstrained variant is
        retained for audit because its power-law tail can have an infinite
        mean and is not synthesis-safe.
    random_state:
        Retained for API parity with the finite-mixture fitter; this fit is
        deterministic.
    """
    del random_state  # the two-part M-step is deterministic
    name, x, y = _validate_spike_grouped(np.asarray(bins), np.asarray(tallies), component_name)
    lower = float(support_lower)
    if not math.isfinite(lower) or lower < 0.0:
        raise ValueError("support_lower must be finite and non-negative")
    if float(x[0]) < lower - 1e-9:
        raise ValueError("the first bin midpoint must lie at or above support_lower")
    if float(x[0]) - lower > float(x[1] - x[0]):
        raise ValueError("the first bin midpoint is more than one bin above support_lower")

    width = float(x[1] - x[0])
    edge1 = float(first_bin_upper) if first_bin_upper is not None else float(x[0] + 0.5 * width)
    if not (lower < edge1 <= float(x[1]) + 1e-9):
        raise ValueError(
            "first_bin_upper must lie above support_lower and no higher than the "
            "second bin midpoint"
        )
    if not 0.0 < float(min_spike_weight) < float(max_spike_weight) < 1.0:
        raise ValueError("spike weight bounds must satisfy 0 < min < max < 1")

    total = float(np.sum(y))
    if float(y[0]) <= 0.0:
        raise MixtureFitError("spike mixture needs a populated first bin")
    first_bin_share = float(y[0] / total)
    if not (float(min_spike_weight) < first_bin_share < float(max_spike_weight)):
        raise MixtureFitError(
            f"spike weight {first_bin_share:.6f} is outside "
            f"({min_spike_weight:g}, {max_spike_weight:g}); rejecting degenerate spike"
        )

    tail_x = x[1:]
    tail_y = y[1:]
    populated = tail_y > 0.0
    if int(np.sum(populated)) < 3:
        raise MixtureFitError(
            "spike mixture needs at least three populated bins above the first bin"
        )

    at_bound: tuple[str, ...] = ()
    start_diagnostics: tuple[dict[str, object], ...]
    if constrained:
        parameters, tail_log_likelihood, iterations, n_starts, n_valid_starts, at_bound, start_diagnostics = (
            _fit_constrained_tail(name, _spike_bin_edges(x, lower, edge1), tail_y, edge1)
        )
        boundary_status = "boundary_reject" if at_bound else "ok"
        estimator = "grouped_mle_lbfgsb"
        converged = True
    else:
        try:
            result = fit_censored_inventory(
                tail_x[populated],
                tail_y[populated],
                support=(edge1, float("inf")),
                distributions=(name,),
            )[0]
        except Exception as error:  # noqa: BLE001 - report the documented boundary failure
            raise MixtureFitError(
                f"continuous {name} tail fit failed above {edge1:g} cm: {error}"
            ) from error
        parameters = {
            key: float(value) for key, value in result.parameters.items() if key != "s"
        }
        tail_log_likelihood = math.nan
        iterations = 1
        n_starts = 1
        n_valid_starts = 1
        boundary_status = "ok"
        estimator = "closed_form_m_step"
        converged = True
        start_diagnostics = (
            {
                "start": 0,
                "converged": True,
                "valid": True,
                "iterations": 1,
                "log_likelihood": math.nan,
                "m_step_failures": 0,
                "ordering_swaps": 0,
                "convergence": "closed_form_m_step",
                "reason": "ok",
            },
        )

    continuous_weight = 1.0 - first_bin_share
    if component_is_degenerate(name, continuous_weight, parameters):
        raise MixtureFitError(
            f"continuous {name} tail is degenerate: {parameters!r}"
        )
    retained = _component_retained_mass(name, parameters, edge1)
    if not math.isfinite(retained) or retained <= 1e-12:
        raise MixtureFitError(
            f"continuous {name} tail has no retained mass above {edge1:g} cm"
        )
    first_tail_mass = _component_interval_mass(
        name,
        parameters,
        edge1,
        edge1 + width,
        support_lower=edge1,
    )
    boundary_reject = boundary_status == "boundary_reject"
    if first_tail_mass > 0.98 and not boundary_reject:
        raise MixtureFitError(
            "continuous component collapses onto the first tail bin; "
            "the two-part model is not identifiable"
        )

    probabilities = _spike_bin_probabilities(
        x,
        first_bin_share,
        name,
        parameters,
        support_lower=lower,
        first_bin_upper=edge1,
    )
    log_likelihood = _spike_log_likelihood(y, probabilities)
    if not math.isfinite(log_likelihood):
        raise MixtureFitError("spike mixture produced a non-finite log-likelihood")
    diagnostics = tuple(
        {
            "start": item.get("start", 0),
            "converged": bool(item.get("converged", True)),
            "valid": bool(item.get("valid", True)),
            "iterations": int(item.get("iterations", iterations)),
            "log_likelihood": item.get("log_likelihood", log_likelihood),
            "m_step_failures": int(item.get("m_step_failures", 0)),
            "ordering_swaps": int(item.get("ordering_swaps", 0)),
            "convergence": item.get("convergence", estimator),
            "reason": item.get("reason", "ok"),
            "n_bins": int(x.size),
            "n_observations": total,
            "n_populated_tail_bins": int(np.sum(populated)),
            "first_bin_share": first_bin_share,
            "first_bin_upper_cm": edge1,
            "continuous_retained_mass": retained,
            "continuous_first_tail_bin_mass": first_tail_mass,
            "estimator": estimator,
            "constrained": bool(constrained),
            "tail_bounds": {
                key: [float(low), float(high)]
                for key, (low, high) in spike_tail_bounds(name).items()
            },
            "at_bound": list(at_bound),
            "boundary_status": boundary_status,
            "tail_log_likelihood": float(tail_log_likelihood),
        }
        for item in start_diagnostics
    )
    return SpikeMixtureFit(
        distribution=(
            f"spike_{name}_constrained" if constrained else f"spike_{name}"
        ),
        spike_weight=first_bin_share,
        component_name=name,
        parameters=parameters,
        log_likelihood=log_likelihood,
        n_parameters=3,
        converged=converged,
        iterations=iterations,
        n_starts=n_starts,
        n_valid_starts=n_valid_starts,
        first_bin_share=first_bin_share,
        first_bin_upper=edge1,
        n_bins=int(x.size),
        n_observations=total,
        constrained=bool(constrained),
        boundary_status=boundary_status,
        at_bound=at_bound,
        estimator=estimator,
        diagnostics=diagnostics,
    )


def sample_spike_mixture(
    size: int,
    *,
    spike_weight: float,
    component_name: str,
    component_parameters: Mapping[str, float],
    support_lower: float,
    support_upper: float,
    first_bin_upper: float,
    random_state: np.random.Generator | int | None = None,
) -> np.ndarray:
    """Draw DBH values from a fitted spike-plus-tail model.

    Spike draws are uniform over ``[support_lower, first_bin_upper)``.  This is
    the documented reporting convention for a point mass that represents a
    binned first interval: it preserves the fitted first-bin probability while
    keeping every synthetic stem at a physical diameter above the plot limit.
    Continuous draws use the existing truncated sampler on
    ``(first_bin_upper, support_upper]``.
    """
    count = int(size)
    if count < 0:
        raise ValueError("size must be non-negative")
    weight = float(spike_weight)
    if not math.isfinite(weight) or not 0.0 < weight < 1.0:
        raise ValueError("spike_weight must be finite and in (0, 1)")
    name = str(component_name).strip().lower()
    if name not in _SUPPORTED_COMPONENTS:
        raise ValueError(f"unsupported spike continuous component {component_name!r}")
    low = float(support_lower)
    high = float(support_upper)
    edge1 = float(first_bin_upper)
    if not math.isfinite(low) or low < 0.0:
        raise ValueError("support_lower must be finite and non-negative")
    if not math.isfinite(high) or high <= low:
        raise ValueError("support_upper must be finite and above support_lower")
    if not (low < edge1 <= high):
        raise ValueError("first_bin_upper must lie inside the sampling support")
    if count == 0:
        return np.empty(0, dtype=float)

    if isinstance(random_state, np.random.Generator):
        rng = random_state
    elif isinstance(random_state, (int, np.integer)):
        rng = np.random.default_rng(int(random_state))
    else:
        rng = np.random.default_rng()

    spike_count = int(rng.binomial(count, weight))
    spike_draws = rng.uniform(low, edge1, spike_count)
    tail_draws = sample_truncated(
        name,
        dict(component_parameters),
        (edge1, high),
        count - spike_count,
        random_state=rng,
    )
    draws = np.concatenate([spike_draws, tail_draws])
    rng.shuffle(draws)
    draws = np.clip(np.asarray(draws, dtype=float), low, high)
    if draws.size != count:
        raise MixtureFitError(
            f"spike sampler returned {draws.size} values, expected {count}"
        )
    return draws
