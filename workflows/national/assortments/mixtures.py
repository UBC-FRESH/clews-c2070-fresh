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
from nemora.workflows.censoring import fit_censored_inventory
from scipy.special import logsumexp
from scipy.stats import gamma as gamma_dist
from scipy.stats import weibull_min

__all__ = [
    "MIXTURE_FAMILY_COMPONENTS",
    "MixtureComponent",
    "MixtureFit",
    "MixtureFitError",
    "canonical_component_order",
    "component_is_degenerate",
    "fit_truncated_mixture",
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
