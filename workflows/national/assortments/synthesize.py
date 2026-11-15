"""Volume-constrained synthetic tree lists for stand disaggregation.

The contract is explicit:

* the fitted DBH distribution supplies **shape**;
* the height model supplies **height**;
* species composition supplies **stem allocation**;
* the target volume from RSYC/BTC supplies **scale**.

Expansion factors are scaled after the taper pass so the stand's taper total
equals the target volume exactly.  The distribution never sets the volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

import numpy as np

from nemora.core import MixtureComponentFit
from nemora.fit.mixture import sample_mixture
from nemora.sampling import sample_distribution, sample_truncated

from workflows.national.bucking.schema import TreeRecord
from workflows.national.bucking.taper import TaperDomainError, TaperRegistry, UnsupportedTaperError

from .height import HeightModelRecord
from .mixtures import MIXTURE_FAMILY_COMPONENTS

__all__ = [
    "DistributionSpec",
    "SynthesisError",
    "SynthesisTarget",
    "SynthesizedStand",
    "normalize_species_weights",
    "synthesize_tree_list",
]


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
        return {
            "target": self.target.to_dict(),
            "achieved_taper_volume_m3": self.achieved_taper_volume_m3,
            "target_volume_m3": self.target_volume_m3,
            "volume_relative_error": (
                abs(self.achieved_taper_volume_m3 - self.target_volume_m3)
                / max(self.target_volume_m3, 1e-12)
            ),
            "scale_factor": self.scale_factor,
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
    if spec.distribution in {"weibull", "gamma"}:
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
    return draws


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


def synthesize_tree_list(
    target: SynthesisTarget,
    *,
    distributions: Sequence[DistributionSpec],
    height_models: Sequence[HeightModelRecord],
    taper_registry: TaperRegistry,
) -> SynthesizedStand:
    """Generate and volume-scale a synthetic tree list.

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
    trees: list[TreeRecord] = []
    volumes: list[float] = []
    species_rows: list[dict[str, Any]] = []

    for species in sorted(target.species_weights):
        spec = distribution_by_species[species]
        height_model = height_by_species[species]
        count = allocation[species]
        dbh = _sample_dbh(spec, count, rng)
        height = height_model.predict(dbh)
        volume = _volumes(taper_registry, species, target.bec_zone, dbh, height)
        initial_ef = float(target.tph_total) * float(target.species_weights[species]) / count
        for index, (diameter, tree_height, tree_volume) in enumerate(
            zip(dbh, height, volume, strict=False), start=1
        ):
            tree_id = f"{target.target_id}:{species}:{index:05d}"
            trees.append(
                TreeRecord(
                    tree_id=tree_id,
                    site_id=f"synth:{target.target_id}",
                    species=species,
                    dbh_cm=float(diameter),
                    height_m=float(tree_height),
                    status="live",
                    expansion_factor=initial_ef,
                    bec_zone=target.bec_zone,
                    age=target.age,
                    provenance=(
                        f"synthetic_assortment:{target.target_id}; "
                        f"distribution={spec.fit_id}; height={height_model.fit_id}; "
                        "volume_scale=pending"
                    ),
                    plot_id=None,
                    visit_id=None,
                    species_group=species,
                    stand_age=target.age,
                )
            )
            volumes.append(float(tree_volume))
        species_rows.append(
            {
                "species": species,
                "weight": float(target.species_weights[species]),
                "n_trees": count,
                "initial_tph": initial_ef * count,
                "mean_dbh_cm": float(np.mean(dbh)),
                "mean_height_m": float(np.mean(height)),
                "mean_taper_volume_m3": float(np.mean(volume)),
            }
        )

    raw_total = float(
        sum(volume * tree.expansion_factor for volume, tree in zip(volumes, trees, strict=False))
    )
    if not math.isfinite(raw_total) or raw_total <= 0.0:
        raise SynthesisError("synthetic tree list has non-positive raw taper volume")
    scale = float(target.target_volume_m3) / raw_total
    if not math.isfinite(scale) or scale <= 0.0:
        raise SynthesisError("volume scale factor is not positive and finite")

    scaled_trees = tuple(
        replace(
            tree,
            expansion_factor=tree.expansion_factor * scale,
            provenance=(
                f"synthetic_assortment:{target.target_id}; "
                f"distribution={distribution_by_species[tree.species].fit_id}; "
                f"height={height_by_species[tree.species].fit_id}; "
                f"volume_scale={scale:.9f}"
            ),
        )
        for tree in trees
    )
    achieved = float(
        sum(
            volume * tree.expansion_factor
            for volume, tree in zip(volumes, scaled_trees, strict=False)
        )
    )
    tph = float(sum(tree.expansion_factor for tree in scaled_trees))
    basal_area = float(
        sum(
            math.pi * (tree.dbh_cm / 200.0) ** 2 * tree.expansion_factor
            for tree in scaled_trees
        )
    )
    qmd = 200.0 * math.sqrt(basal_area / (tph * math.pi)) if tph > 0 else math.nan

    for row in species_rows:
        species = row["species"]
        species_trees = [tree for tree in scaled_trees if tree.species == species]
        species_volumes = [
            volume
            for volume, tree in zip(volumes, scaled_trees, strict=False)
            if tree.species == species
        ]
        species_ba = sum(
            math.pi * (tree.dbh_cm / 200.0) ** 2 * tree.expansion_factor
            for tree in species_trees
        )
        species_tph = sum(tree.expansion_factor for tree in species_trees)
        row["tph"] = species_tph
        row["basal_area_m2ha"] = species_ba
        row["qmd_cm"] = (
            200.0 * math.sqrt(species_ba / (species_tph * math.pi))
            if species_tph > 0
            else math.nan
        )
        row["taper_volume_m3"] = sum(
            volume * tree.expansion_factor
            for volume, tree in zip(volumes, scaled_trees, strict=False)
            if tree.species == species
        )
        row["taper_volume_m3ha"] = row["taper_volume_m3"] / float(target.area_ha)
        row["share_of_taper_volume"] = (
            row["taper_volume_m3"] / achieved if achieved > 0 else math.nan
        )

    return SynthesizedStand(
        target=target,
        trees=scaled_trees,
        tree_volumes_m3=tuple(volumes),
        achieved_taper_volume_m3=achieved,
        target_volume_m3=float(target.target_volume_m3),
        scale_factor=scale,
        tph=tph,
        basal_area_m2ha=basal_area,
        qmd_cm=qmd,
        species_summary=tuple(species_rows),
        distribution_specs=tuple(distributions),
        height_models=tuple(height_models),
    )
