"""Synthetic tree lists for stand disaggregation.

The contract is explicit:

* the fitted DBH distribution supplies **shape**;
* the height model supplies **height**;
* species composition supplies **stem allocation**;
* the RSYC/BTC stand target supplies **volume and, when present, TPH**.

Two scaling modes exist:

``volume_only``
    Expansion factors are scaled after the taper pass so the stand's taper
    total equals the target volume.  TPH is an output.
``volume_and_tph``
    Expansion factors are first fixed to sum to the target TPH at the sampled
    relative weights.  A positive diameter multiplier ``k`` is then found by a
    bracketed root search so that the taper total equals the target volume,
    with height recomputed from ``k * DBH`` through the fitted height model.
    Both constraints are therefore honoured; the distribution only supplies
    shape and the displayed ``diameter_scale_factor`` records the adjustment.

The distribution never sets volume or TPH.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.optimize import brentq

from nemora.core import MixtureComponentFit
from nemora.fit.mixture import sample_mixture
from nemora.sampling import sample_distribution, sample_truncated

from workflows.national.bucking.schema import TreeRecord
from workflows.national.bucking.taper import TaperDomainError, TaperRegistry, UnsupportedTaperError

from .height import HeightModelRecord
from .mixtures import (
    MIXTURE_FAMILY_COMPONENTS,
    SPIKE_FAMILIES,
    SPIKE_FAMILY_COMPONENTS,
    sample_spike_mixture,
)

__all__ = [
    "DistributionSpec",
    "SynthesisError",
    "SynthesisTarget",
    "SynthesizedStand",
    "normalize_species_weights",
    "synthesize_tree_list",
]


# Documented dual-constraint root-search bracket.  The first search uses a
# narrow positive range around the sampled diameters.  If the residual does not
# change sign, one wider search is attempted before the synthesis fails loudly.
DIAMETER_SCALE_BRACKET = (0.10, 10.0)
DIAMETER_SCALE_EXPANDED_BRACKET = (0.01, 25.0)
DIAMETER_SCALE_XTOL = 1e-12
DIAMETER_SCALE_RTOL = 1e-14
VOLUME_TOLERANCE_M3 = 1e-6
TPH_TOLERANCE = 1e-9


class SynthesisError(RuntimeError):
    """Raised when a synthetic stand cannot honour the target contract."""


@dataclass(frozen=True, slots=True)
class DistributionSpec:
    """A selected DBH distribution and its truncated support."""

    species: str
    distribution: str
    parameters: dict[str, float]
    support: tuple[float, float]
    component_weights: tuple[float, ...] | None = None
    component_parameters: tuple[dict[str, float], ...] | None = None
    component_names: tuple[str, ...] | None = None
    fit_id: str = ""
    evidence_tier: str = ""

    def __post_init__(self) -> None:
        code = str(self.species).strip().upper()
        if not code:
            raise ValueError("DistributionSpec species is required")
        object.__setattr__(self, "species", code)
        lower, upper = (float(value) for value in self.support)
        if not math.isfinite(lower) or lower < 0.0:
            raise ValueError("distribution support lower bound must be finite and non-negative")
        if not math.isfinite(upper) or upper <= lower:
            raise ValueError("distribution support upper bound must exceed the lower bound")
        object.__setattr__(self, "support", (lower, upper))
        name = str(self.distribution).strip().lower()
        supported = {
            "weibull",
            "gamma",
            "mixture",
            "mix_weibull_2",
            "mix_weibull_gamma",
            "mix_weibull_3",
            "spike_weibull",
            "spike_gamma",
            "spike_weibull_constrained",
            "spike_gamma_constrained",
            "gb1",
            "gb2",
            "gg",
        }
        if name not in supported:
            raise ValueError(f"unsupported synthesis distribution {self.distribution!r}")
        object.__setattr__(self, "distribution", name)
        if name in MIXTURE_FAMILY_COMPONENTS:
            if not self.component_weights or not self.component_parameters:
                raise ValueError("mixture distributions require component weights and parameters")
            if len(self.component_weights) != len(self.component_parameters):
                raise ValueError("mixture component weights and parameters must align")
            names = self.component_names or MIXTURE_FAMILY_COMPONENTS[name]
            if len(names) != len(self.component_weights):
                raise ValueError("mixture component names must align with the weights")
            unknown = sorted(set(names).difference({"weibull", "gamma"}))
            if unknown:
                raise ValueError(f"unsupported mixture component names: {unknown}")
            object.__setattr__(self, "component_names", tuple(str(item) for item in names))
        if name in SPIKE_FAMILIES:
            weight = float(self.parameters.get("spike_weight", math.nan))
            width = float(self.parameters.get("spike_bin_width", math.nan))
            if not math.isfinite(weight) or not 0.0 < weight < 1.0:
                raise ValueError("spike distributions require spike_weight in (0, 1)")
            if not math.isfinite(width) or width <= 0.0:
                raise ValueError("spike distributions require a positive spike_bin_width")
            if lower + width > upper:
                raise ValueError("spike first-bin width exceeds the sampling support")
            component = SPIKE_FAMILY_COMPONENTS[name]
            required = {"a", "beta"} if component == "weibull" else {"p", "beta"}
            missing = sorted(required.difference(self.parameters))
            if missing:
                raise ValueError(
                    f"{name} is missing continuous component parameters: {missing}"
                )


@dataclass(frozen=True, slots=True)
class SynthesisTarget:
    """Stand-level target that the synthetic tree list must honour."""

    target_id: str
    target_level: str
    species_weights: dict[str, float]
    tph_total: float
    volume_m3ha: float
    area_ha: float
    bec_zone: str
    age: float
    dbh_limit_cm: float
    max_dbh_cm: float
    seed: int = 20261115
    n_trees: int = 600
    target_tph: float | None = None
    target_agb_odt_ha: float | None = None
    source: str = ""

    def __post_init__(self) -> None:
        target_id = str(self.target_id).strip()
        if not target_id:
            raise ValueError("target_id is required")
        object.__setattr__(self, "target_id", target_id)
        object.__setattr__(self, "target_level", str(self.target_level).strip() or "stand")
        weights = {
            str(code).strip().upper(): float(value)
            for code, value in self.species_weights.items()
        }
        if not weights:
            raise ValueError("species_weights cannot be empty")
        if any(not math.isfinite(value) or value <= 0.0 for value in weights.values()):
            raise ValueError("species weights must be finite and positive")
        total = float(sum(weights.values()))
        if not math.isclose(total, 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError(f"species weights must sum to one, got {total!r}")
        object.__setattr__(self, "species_weights", weights)
        for name in ("tph_total", "volume_m3ha", "area_ha", "dbh_limit_cm", "max_dbh_cm"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive, got {value!r}")
            object.__setattr__(self, name, value)
        age = float(self.age)
        if not math.isfinite(age) or age < 0.0:
            raise ValueError("age must be finite and non-negative")
        object.__setattr__(self, "age", age)
        if int(self.n_trees) < len(weights):
            raise ValueError("n_trees must be at least the number of species")
        object.__setattr__(self, "n_trees", int(self.n_trees))
        object.__setattr__(self, "seed", int(self.seed))
        if self.target_tph is not None:
            target_tph = float(self.target_tph)
            if not math.isfinite(target_tph) or target_tph <= 0.0:
                raise ValueError("target_tph must be finite and positive")
            object.__setattr__(self, "target_tph", target_tph)
        if self.target_agb_odt_ha is not None:
            agb = float(self.target_agb_odt_ha)
            if not math.isfinite(agb) or agb < 0.0:
                raise ValueError("target_agb_odt_ha must be finite and non-negative")
            object.__setattr__(self, "target_agb_odt_ha", agb)

    @property
    def target_volume_m3(self) -> float:
        return float(self.volume_m3ha) * float(self.area_ha)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_level": self.target_level,
            "species_weights": dict(sorted(self.species_weights.items())),
            "tph_total": self.tph_total,
            "volume_m3ha": self.volume_m3ha,
            "area_ha": self.area_ha,
            "target_volume_m3": self.target_volume_m3,
            "bec_zone": self.bec_zone,
            "age": self.age,
            "dbh_limit_cm": self.dbh_limit_cm,
            "max_dbh_cm": self.max_dbh_cm,
            "seed": self.seed,
            "n_trees": self.n_trees,
            "target_tph": self.target_tph,
            "target_agb_odt_ha": self.target_agb_odt_ha,
            "source": self.source,
        }


@dataclass(frozen=True, slots=True)
class SynthesizedStand:
    """Synthetic tree list plus its volume and stem-density audit."""

    target: SynthesisTarget
    trees: tuple[TreeRecord, ...]
    tree_volumes_m3: tuple[float, ...]
    achieved_taper_volume_m3: float
    target_volume_m3: float
    scale_factor: float
    diameter_scale_factor: float
    diameter_scale_bracket: tuple[float, float] | None
    diameter_scale_residual_m3: float
    root_find_iterations: int
    constraint_mode: str
    tph: float
    basal_area_m2ha: float
    qmd_cm: float
    species_summary: tuple[dict[str, Any], ...]
    distribution_specs: tuple[DistributionSpec, ...]
    height_models: tuple[HeightModelRecord, ...]

    def to_frame(self):
        import pandas as pd

        rows: list[dict[str, Any]] = []
        for tree, volume in zip(self.trees, self.tree_volumes_m3, strict=False):
            rows.append(
                {
                    "tree_id": tree.tree_id,
                    "site_id": tree.site_id,
                    "species": tree.species,
                    "dbh_cm": tree.dbh_cm,
                    "height_m": tree.height_m,
                    "expansion_factor": tree.expansion_factor,
                    "tph": tree.expansion_factor,
                    "bec_zone": tree.bec_zone,
                    "age": tree.age,
                    "taper_volume_m3": volume,
                    "taper_volume_m3ha": volume * tree.expansion_factor,
                    "stem_basal_area_m2": math.pi * (tree.dbh_cm / 200.0) ** 2,
                    "stem_basal_area_m2ha": math.pi
                    * (tree.dbh_cm / 200.0) ** 2
                    * tree.expansion_factor,
                    "provenance": tree.provenance,
                }
            )
        return pd.DataFrame(rows)

    def manifest_record(self) -> dict[str, Any]:
        target_tph = self.target.target_tph
        return {
            "target": self.target.to_dict(),
            "constraint_mode": self.constraint_mode,
            "target_tph": target_tph,
            "achieved_tph": self.tph,
            "tph_relative_error": (
                abs(self.tph - target_tph) / max(target_tph, 1e-12)
                if target_tph is not None
                else None
            ),
            "achieved_taper_volume_m3": self.achieved_taper_volume_m3,
            "target_volume_m3": self.target_volume_m3,
            "volume_relative_error": (
                abs(self.achieved_taper_volume_m3 - self.target_volume_m3)
                / max(self.target_volume_m3, 1e-12)
            ),
            "expansion_factor_scale": self.scale_factor,
            "scale_factor": self.scale_factor,
            "diameter_scale_factor": self.diameter_scale_factor,
            "diameter_scale_bracket": (
                list(self.diameter_scale_bracket)
                if self.diameter_scale_bracket is not None
                else None
            ),
            "diameter_scale_residual_m3": self.diameter_scale_residual_m3,
            "root_find_iterations": self.root_find_iterations,
            "tph": self.tph,
            "basal_area_m2ha": self.basal_area_m2ha,
            "qmd_cm": self.qmd_cm,
            "n_trees": len(self.trees),
            "species_summary": list(self.species_summary),
            "distributions": [
                {
                    "species": spec.species,
                    "distribution": spec.distribution,
                    "parameters": spec.parameters,
                    "component_weights": spec.component_weights,
                    "component_parameters": spec.component_parameters,
                    "component_names": spec.component_names,
                    "support": spec.support,
                    "fit_id": spec.fit_id,
                    "evidence_tier": spec.evidence_tier,
                }
                for spec in self.distribution_specs
            ],
            "height_models": [
                {
                    "species": model.species,
                    "fit_id": model.fit_id,
                    "a": model.a,
                    "b": model.b,
                    "n": model.n,
                    "rmse_m": model.rmse_m,
                    "bias_m": model.bias_m,
                    "evidence_tier": model.evidence_tier,
                }
                for model in self.height_models
            ],
        }


def normalize_species_weights(
    raw_weights: Mapping[str, float],
    *,
    min_share: float = 0.01,
) -> dict[str, float]:
    """Normalize composition weights and drop species below ``min_share``.

    The threshold is a synthesis guardrail, not a statistical test.  Minor
    species are reported separately; they are not silently assigned a shape
    estimated from fewer than the documented fit threshold.
    """
    if not 0.0 <= min_share < 1.0:
        raise ValueError("min_share must be in [0, 1)")
    cleaned: dict[str, float] = {}
    for code, value in raw_weights.items():
        text = str(code).strip().upper()
        number = float(value)
        if not text:
            raise ValueError("species codes cannot be empty")
        if not math.isfinite(number) or number < 0.0:
            raise ValueError(f"species weight for {text!r} must be finite and non-negative")
        cleaned[text] = cleaned.get(text, 0.0) + number
    total = float(sum(cleaned.values()))
    if total <= 0.0:
        raise ValueError("species weights sum to zero")
    normalized = {code: value / total for code, value in cleaned.items() if value > 0.0}
    retained = {
        code: value for code, value in normalized.items() if value >= min_share
    }
    if not retained:
        raise ValueError(f"no species weight reaches min_share={min_share}")
    retained_total = float(sum(retained.values()))
    return {code: value / retained_total for code, value in sorted(retained.items())}


def _allocate_tree_counts(weights: Mapping[str, float], n_trees: int) -> dict[str, int]:
    species = sorted(weights)
    raw = {code: n_trees * float(weights[code]) for code in species}
    allocation = {code: int(math.floor(raw[code])) for code in species}
    remaining = n_trees - sum(allocation.values())
    order = sorted(
        species,
        key=lambda code: (-(raw[code] - allocation[code]), code),
    )
    for code in order[:remaining]:
        allocation[code] += 1
    for code in species:
        if allocation[code] <= 0:
            raise SynthesisError(
                f"allocation gives zero trees to {code}; increase n_trees or drop the species"
            )
    if sum(allocation.values()) != n_trees:
        raise SynthesisError("tree allocation does not sum to n_trees")
    return allocation


def _sample_dbh(spec: DistributionSpec, size: int, rng: np.random.Generator) -> np.ndarray:
    lower, upper = spec.support
    if spec.distribution in SPIKE_FAMILIES:
        width = float(spec.parameters["spike_bin_width"])
        first_bin_upper = min(lower + width, upper)
        continuous = {
            key: float(value)
            for key, value in spec.parameters.items()
            if key not in {"spike_weight", "spike_bin_width"}
        }
        draws = sample_spike_mixture(
            size,
            spike_weight=float(spec.parameters["spike_weight"]),
            component_name=SPIKE_FAMILY_COMPONENTS[spec.distribution],
            component_parameters=continuous,
            support_lower=lower,
            support_upper=upper,
            first_bin_upper=first_bin_upper,
            random_state=rng,
        )
    elif spec.distribution in {"weibull", "gamma"}:
        draws = sample_truncated(
            spec.distribution,
            spec.parameters,
            (lower, upper),
            size,
            random_state=rng,
        )
    elif spec.distribution in MIXTURE_FAMILY_COMPONENTS:
        names = spec.component_names or MIXTURE_FAMILY_COMPONENTS[spec.distribution]
        components = [
            MixtureComponentFit(name=name, weight=float(weight), parameters=dict(params))
            for name, weight, params in zip(
                names,
                spec.component_weights or (),
                spec.component_parameters or (),
                strict=False,
            )
        ]
        draws = sample_mixture(
            size,
            components,
            random_state=rng,
            lower=lower,
            upper=upper,
        )
    else:
        accepted: list[np.ndarray] = []
        accepted_size = 0
        for _attempt in range(100):
            block = sample_distribution(
                spec.distribution,
                spec.parameters,
                max(size * 2, 16),
                random_state=rng,
            )
            valid = block[(block >= lower) & (block <= upper) & np.isfinite(block)]
            if valid.size:
                accepted.append(valid)
                accepted_size += valid.size
            if accepted_size >= size:
                break
        if accepted_size < size:
            raise SynthesisError(
                f"{spec.species}: could not draw {size} DBH values inside "
                f"[{lower:g}, {upper:g}] from {spec.distribution}"
            )
        draws = np.concatenate(accepted)[:size]

    draws = np.asarray(draws, dtype=float)
    if draws.size != size:
        raise SynthesisError(
            f"{spec.species}: sampler returned {draws.size} values, expected {size}"
        )
    if np.any(~np.isfinite(draws)) or np.any(draws <= 0.0):
        raise SynthesisError(f"{spec.species}: sampler produced non-positive DBH")
    if np.any(draws < lower - 1e-9) or np.any(draws > upper + 1e-9):
        raise SynthesisError(
            f"{spec.species}: sampler produced DBH outside the fitted support "
            f"[{lower:g}, {upper:g}]"
        )
    return np.clip(draws, lower, upper)


def _taper_model(registry: TaperRegistry, species: str, bec_zone: str):
    try:
        return registry.resolve("BC", species, bec_zone)
    except UnsupportedTaperError as error:
        raise SynthesisError(
            f"no taper for synthetic species {species!r} in {bec_zone!r}: {error}. "
            "Exclude unsupported species from the composition before synthesis."
        ) from error


def _volumes(
    registry: TaperRegistry,
    species: str,
    bec_zone: str,
    dbh: np.ndarray,
    height: np.ndarray,
) -> np.ndarray:
    model = _taper_model(registry, species, bec_zone)
    volumes = np.empty(dbh.size, dtype=float)
    for index, (diameter, tree_height) in enumerate(zip(dbh, height, strict=False)):
        try:
            volumes[index] = model.total_volume_m3(float(diameter), float(tree_height))
        except TaperDomainError as error:
            raise SynthesisError(
                f"taper domain failure for {species} at DBH {diameter:.3f} cm and "
                f"height {tree_height:.3f} m: {error}"
            ) from error
    if np.any(~np.isfinite(volumes)) or np.any(volumes < 0.0):
        raise SynthesisError(f"taper produced non-finite or negative volume for {species}")
    return volumes


@dataclass(frozen=True, slots=True)
class _SpeciesDraw:
    """One species' sampled stems and the expansion factors they carry."""

    distribution: DistributionSpec
    height_model: HeightModelRecord
    dbh_cm: np.ndarray
    height_m: np.ndarray
    taper_volume_m3: np.ndarray
    expansion_factors: np.ndarray

    @property
    def species(self) -> str:
        return self.distribution.species


def _taper_total_at_diameter_scale(
    draws: Sequence[_SpeciesDraw],
    multiplier: float,
    taper_registry: TaperRegistry,
    bec_zone: str,
) -> float:
    """Taper total at ``DBH * multiplier`` with height recomputed from DBH."""
    if not math.isfinite(multiplier) or multiplier <= 0.0:
        raise SynthesisError(f"diameter scale must be finite and positive, got {multiplier!r}")
    total = 0.0
    for draw in draws:
        dbh = draw.dbh_cm * multiplier
        height = draw.height_model.predict(dbh)
        volumes = _volumes(taper_registry, draw.species, bec_zone, dbh, height)
        total += float(np.dot(draw.expansion_factors, volumes))
    return total


def _solve_diameter_scale(
    draws: Sequence[_SpeciesDraw],
    *,
    target_volume_m3: float,
    taper_registry: TaperRegistry,
    bec_zone: str,
) -> tuple[float, tuple[float, float], int]:
    """Find the positive diameter multiplier that hits the target volume.

    Expansion factors are already fixed, so the residual is strictly
    ``sum(phf_i * V(k * dbh_i, H(k * dbh_i))) - target_volume``.  The first
    bracket is narrow; if it has no sign change the documented expanded
    bracket is tried once, then synthesis fails loudly.
    """

    def residual(multiplier: float) -> float:
        return (
            _taper_total_at_diameter_scale(
                draws, multiplier, taper_registry, bec_zone
            )
            - target_volume_m3
        )

    bracket_residuals: list[tuple[float, float, float, float]] = []
    for bracket in (DIAMETER_SCALE_BRACKET, DIAMETER_SCALE_EXPANDED_BRACKET):
        low, high = bracket
        low_residual = residual(low)
        high_residual = residual(high)
        if math.isclose(low_residual, 0.0, abs_tol=VOLUME_TOLERANCE_M3):
            return low, bracket, 0
        if math.isclose(high_residual, 0.0, abs_tol=VOLUME_TOLERANCE_M3):
            return high, bracket, 0
        if low_residual < 0.0 < high_residual:
            try:
                root, result = brentq(
                    residual,
                    low,
                    high,
                    xtol=DIAMETER_SCALE_XTOL,
                    rtol=DIAMETER_SCALE_RTOL,
                    maxiter=200,
                    full_output=True,
                    disp=False,
                )
            except ValueError as error:
                raise SynthesisError(
                    "diameter-scale root search failed for bracket "
                    f"[{low:g}, {high:g}]: {error}"
                ) from error
            if not result.converged:
                raise SynthesisError(
                    "diameter-scale root search did not converge in bracket "
                    f"[{low:g}, {high:g}] after {result.iterations} iterations; "
                    f"residual={result.function_calls!r}"
                )
            return float(root), bracket, int(result.iterations)
        bracket_residuals.append((low, high, low_residual, high_residual))

    attempts = "; ".join(
        f"[{low:g}, {high:g}] residual {low_residual:.6g} -> {high_residual:.6g} m3"
        for low, high, low_residual, high_residual in bracket_residuals
    )
    raise SynthesisError(
        "no sign change in the dual-constraint diameter scale: cannot match "
        f"target volume {target_volume_m3:.6f} m3 at fixed TPH; {attempts}. "
        "The target volume is outside what the fitted shape can reach with a "
        "positive diameter multiplier."
    )


def _build_trees(
    target: SynthesisTarget,
    draws: Sequence[_SpeciesDraw],
    *,
    expansion_scale: float,
    diameter_scale: float,
    constraint_mode: str,
) -> tuple[TreeRecord, ...]:
    trees: list[TreeRecord] = []
    for draw in draws:
        for index, (diameter, tree_height, expansion_factor) in enumerate(
            zip(
                draw.dbh_cm,
                draw.height_m,
                draw.expansion_factors,
                strict=True,
            ),
            start=1,
        ):
            trees.append(
                TreeRecord(
                    tree_id=f"{target.target_id}:{draw.species}:{index:05d}",
                    site_id=f"synth:{target.target_id}",
                    species=draw.species,
                    dbh_cm=float(diameter),
                    height_m=float(tree_height),
                    status="live",
                    expansion_factor=float(expansion_factor),
                    bec_zone=target.bec_zone,
                    age=target.age,
                    provenance=(
                        f"synthetic_assortment:{target.target_id}; "
                        f"distribution={draw.distribution.fit_id}; "
                        f"height={draw.height_model.fit_id}; "
                        f"constraint={constraint_mode}; "
                        f"expansion_scale={expansion_scale:.9f}; "
                        f"diameter_scale={diameter_scale:.9f}"
                    ),
                    plot_id=None,
                    visit_id=None,
                    species_group=draw.species,
                    stand_age=target.age,
                )
            )
    return tuple(trees)


def synthesize_tree_list(
    target: SynthesisTarget,
    *,
    distributions: Sequence[DistributionSpec],
    height_models: Sequence[HeightModelRecord],
    taper_registry: TaperRegistry,
) -> SynthesizedStand:
    """Generate a synthetic tree list that honours the target contract.

    When ``target.target_tph`` is set, expansion factors are fixed to sum to
    that TPH and a positive diameter multiplier is solved so the taper total
    equals the target volume.  When it is absent, the legacy volume-only scale
    is applied to the expansion factors.

    The input distributions and height models must cover exactly the target's
    species set.  All records are created as live ``TreeRecord`` rows so they
    can be passed directly to the bucking layer.
    """
    target_species = set(target.species_weights)
    distribution_by_species = {spec.species: spec for spec in distributions}
    height_by_species = {model.species: model for model in height_models}
    missing_distributions = sorted(target_species.difference(distribution_by_species))
    missing_heights = sorted(target_species.difference(height_by_species))
    if missing_distributions:
        raise SynthesisError(
            f"missing DBH distributions for species: {', '.join(missing_distributions)}"
        )
    if missing_heights:
        raise SynthesisError(f"missing height models for species: {', '.join(missing_heights)}")
    extra_distributions = sorted(set(distribution_by_species).difference(target_species))
    if extra_distributions:
        raise SynthesisError(
            f"distributions supplied for non-target species: {', '.join(extra_distributions)}"
        )

    rng = np.random.default_rng(int(target.seed))
    allocation = _allocate_tree_counts(target.species_weights, target.n_trees)
    draws: list[_SpeciesDraw] = []

    for species in sorted(target.species_weights):
        spec = distribution_by_species[species]
        height_model = height_by_species[species]
        count = allocation[species]
        dbh = _sample_dbh(spec, count, rng)
        height = height_model.predict(dbh)
        volumes = _volumes(taper_registry, species, target.bec_zone, dbh, height)
        initial_ef = float(target.tph_total) * float(target.species_weights[species]) / count
        draws.append(
            _SpeciesDraw(
                distribution=spec,
                height_model=height_model,
                dbh_cm=dbh,
                height_m=height,
                taper_volume_m3=volumes,
                expansion_factors=np.full(count, initial_ef, dtype=float),
            )
        )

    initial_tph = float(sum(draw.expansion_factors.sum() for draw in draws))
    if not math.isfinite(initial_tph) or initial_tph <= 0.0:
        raise SynthesisError("synthetic tree list has non-positive initial TPH")

    target_volume = float(target.target_volume_m3)
    bracket: tuple[float, float] | None = None
    root_iterations = 0

    if target.target_tph is None:
        raw_total = float(
            sum(
                np.dot(draw.expansion_factors, draw.taper_volume_m3)
                for draw in draws
            )
        )
        if not math.isfinite(raw_total) or raw_total <= 0.0:
            raise SynthesisError("synthetic tree list has non-positive raw taper volume")
        expansion_scale = target_volume / raw_total
        if not math.isfinite(expansion_scale) or expansion_scale <= 0.0:
            raise SynthesisError("volume scale factor is not positive and finite")
        draws = [
            replace(
                draw,
                expansion_factors=draw.expansion_factors * expansion_scale,
            )
            for draw in draws
        ]
        diameter_scale = 1.0
        constraint_mode = "volume_only"
    else:
        expansion_scale = float(target.target_tph) / initial_tph
        if not math.isfinite(expansion_scale) or expansion_scale <= 0.0:
            raise SynthesisError("TPH scale factor is not positive and finite")
        draws = [
            replace(
                draw,
                expansion_factors=draw.expansion_factors * expansion_scale,
            )
            for draw in draws
        ]
        achieved_tph = float(sum(draw.expansion_factors.sum() for draw in draws))
        if not math.isclose(
            achieved_tph,
            float(target.target_tph),
            rel_tol=TPH_TOLERANCE,
            abs_tol=TPH_TOLERANCE,
        ):
            raise SynthesisError(
                f"expansion factors sum to {achieved_tph!r}, not target TPH "
                f"{target.target_tph!r}"
            )
        diameter_scale, bracket, root_iterations = _solve_diameter_scale(
            draws,
            target_volume_m3=target_volume,
            taper_registry=taper_registry,
            bec_zone=target.bec_zone,
        )
        scaled_draws: list[_SpeciesDraw] = []
        for draw in draws:
            scaled_dbh = draw.dbh_cm * diameter_scale
            scaled_height = draw.height_model.predict(scaled_dbh)
            scaled_volume = _volumes(
                taper_registry,
                draw.species,
                target.bec_zone,
                scaled_dbh,
                scaled_height,
            )
            scaled_draws.append(
                replace(
                    draw,
                    dbh_cm=scaled_dbh,
                    height_m=scaled_height,
                    taper_volume_m3=scaled_volume,
                )
            )
        draws = scaled_draws
        constraint_mode = "volume_and_tph"

    trees = _build_trees(
        target,
        draws,
        expansion_scale=expansion_scale,
        diameter_scale=diameter_scale,
        constraint_mode=constraint_mode,
    )
    achieved = float(
        sum(np.dot(draw.expansion_factors, draw.taper_volume_m3) for draw in draws)
    )
    tph = float(sum(tree.expansion_factor for tree in trees))
    basal_area = float(
        sum(
            math.pi * (tree.dbh_cm / 200.0) ** 2 * tree.expansion_factor
            for tree in trees
        )
    )
    qmd = 200.0 * math.sqrt(basal_area / (tph * math.pi)) if tph > 0 else math.nan

    if abs(achieved - target_volume) > max(
        VOLUME_TOLERANCE_M3, 1e-9 * abs(target_volume)
    ):
        raise SynthesisError(
            f"synthetic taper total {achieved:.9f} m3 does not match target "
            f"{target_volume:.9f} m3 in {constraint_mode} mode"
        )
    if target.target_tph is not None and not math.isclose(
        tph,
        float(target.target_tph),
        rel_tol=TPH_TOLERANCE,
        abs_tol=TPH_TOLERANCE,
    ):
        raise SynthesisError(
            f"synthetic TPH {tph:.9f} does not match target {target.target_tph:.9f}"
        )

    species_rows: list[dict[str, Any]] = []
    for draw in draws:
        species = draw.species
        weight = float(target.species_weights[species])
        species_tph = float(draw.expansion_factors.sum())
        species_ba = float(
            np.sum(
                math.pi
                * (draw.dbh_cm / 200.0) ** 2
                * draw.expansion_factors
            )
        )
        species_volume = float(np.dot(draw.expansion_factors, draw.taper_volume_m3))
        species_rows.append(
            {
                "species": species,
                "weight": weight,
                "n_trees": int(draw.dbh_cm.size),
                "target_tph": (
                    float(target.target_tph) * weight
                    if target.target_tph is not None
                    else None
                ),
                "initial_tph": float(target.tph_total) * weight,
                "tph": species_tph,
                "achieved_weight": species_tph / tph if tph > 0 else math.nan,
                "expansion_factor_sum": species_tph,
                "basal_area_m2ha": species_ba,
                "qmd_cm": (
                    200.0 * math.sqrt(species_ba / (species_tph * math.pi))
                    if species_tph > 0
                    else math.nan
                ),
                "mean_dbh_cm": float(np.mean(draw.dbh_cm)),
                "mean_height_m": float(np.mean(draw.height_m)),
                "mean_taper_volume_m3": float(np.mean(draw.taper_volume_m3)),
                "taper_volume_m3": species_volume,
                "taper_volume_m3ha": species_volume / float(target.area_ha),
                "share_of_taper_volume": (
                    species_volume / achieved if achieved > 0 else math.nan
                ),
            }
        )

    return SynthesizedStand(
        target=target,
        trees=trees,
        tree_volumes_m3=tuple(
            float(volume)
            for draw in draws
            for volume in draw.taper_volume_m3
        ),
        achieved_taper_volume_m3=achieved,
        target_volume_m3=target_volume,
        scale_factor=expansion_scale,
        diameter_scale_factor=diameter_scale,
        diameter_scale_bracket=bracket,
        diameter_scale_residual_m3=achieved - target_volume,
        root_find_iterations=root_iterations,
        constraint_mode=constraint_mode,
        tph=tph,
        basal_area_m2ha=basal_area,
        qmd_cm=qmd,
        species_summary=tuple(species_rows),
        distribution_specs=tuple(distributions),
        height_models=tuple(height_models),
    )
