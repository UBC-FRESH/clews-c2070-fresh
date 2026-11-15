"""Hierarchical height-diameter models for synthetic tree lists.

The estimator is ``nemora.workflows.height_volume.fit_height_diameter_models``
(power form ``H = 1.3 + exp(a) * DBH^b``).  This module only supplies the
hierarchical fallback and the audit record.  It does not refit or smooth
coefficients.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from nemora.workflows.height_volume import fit_height_diameter_models

from .distributions import DEFAULT_LEVELS

__all__ = [
    "HeightModelBundle",
    "HeightModelError",
    "HeightModelRecord",
    "fit_height_models",
]


class HeightModelError(RuntimeError):
    """Raised when no height model exists for a requested target."""


@dataclass(frozen=True, slots=True)
class HeightModelRecord:
    """One fitted height model with its fallback provenance."""

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
    n: int
    a: float
    b: float
    rmse_m: float
    bias_m: float
    evidence_tier: str

    def predict(self, dbh_cm: np.ndarray | float) -> np.ndarray:
        dbh = np.asarray(dbh_cm, dtype=float)
        if np.any(~np.isfinite(dbh)) or np.any(dbh <= 0.0):
            raise HeightModelError(
                f"height model {self.fit_id} received non-positive or non-finite DBH"
            )
        heights = 1.3 + math.exp(self.a) * np.power(dbh, self.b)
        if np.any(~np.isfinite(heights)) or np.any(heights <= 1.3):
            raise HeightModelError(
                f"height model {self.fit_id} produced a non-finite or sub-1.3 m height"
            )
        return heights

    def to_row(self) -> dict[str, Any]:
        return {
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
            "n": self.n,
            "a": self.a,
            "b": self.b,
            "rmse_m": self.rmse_m,
            "bias_m": self.bias_m,
            "evidence_tier": self.evidence_tier,
            "fit_status": "ok",
            "error": None,
        }


@dataclass(frozen=True, slots=True)
class HeightModelBundle:
    models: tuple[HeightModelRecord, ...]
    failures: tuple[dict[str, Any], ...]

    def by_species(self) -> dict[str, HeightModelRecord]:
        return {record.species: record for record in self.models}

    def to_rows(self) -> list[dict[str, Any]]:
        rows = [record.to_row() for record in self.models]
        for failure in self.failures:
            rows.append(
                {
                    "scope": failure.get("scope"),
                    "target_level": failure.get("target_level"),
                    "target_id": failure.get("target_id"),
                    "fit_level": None,
                    "fit_id": None,
                    "species": failure.get("species"),
                    "ecodistrict": failure.get("ecodistrict"),
                    "ecoregion": failure.get("ecoregion"),
                    "ecoprovince": failure.get("ecoprovince"),
                    "ecozone": failure.get("ecozone"),
                    "age_window": failure.get("age_window", "all"),
                    "n": failure.get("n", 0),
                    "a": math.nan,
                    "b": math.nan,
                    "rmse_m": math.nan,
                    "bias_m": math.nan,
                    "evidence_tier": None,
                    "fit_status": "failed",
                    "error": failure.get("error"),
                }
            )
        return rows


def _clean(frame: pd.DataFrame, levels: Sequence[str]) -> pd.DataFrame:
    required = {"species", "dbh_cm", "height_m", "age", "bec_zone", "plot_id"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"height observations missing columns: {', '.join(missing)}")
    data = frame.copy()
    for column in ("dbh_cm", "height_m", "age"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data["species"] = data["species"].astype(str).str.strip().str.upper()
    data["bec_zone"] = data["bec_zone"].astype(str).str.strip().str.upper()
    for level in levels:
        if level not in data.columns:
            data[level] = "UNKNOWN"
        data[level] = data[level].astype(str).str.strip()
    data = data[
        (data["dbh_cm"] > 0.0)
        & (data["height_m"] > 1.3)
        & data["species"].ne("")
        & data["bec_zone"].ne("")
        & data["bec_zone"].ne("NAN")
    ].copy()
    if data.empty:
        raise ValueError("no valid height observations remain")
    return data


def _resolve_target(
    target: Mapping[str, Any], data: pd.DataFrame, levels: Sequence[str]
) -> dict[str, str]:
    species = str(target.get("species", "")).strip().upper()
    if not species:
        raise ValueError("each height target must define species")
    resolved: dict[str, str] = {"species": species}
    mask = data["species"].eq(species)
    for level in levels:
        value = target.get(level)
        if value is None or (isinstance(value, float) and math.isnan(value)):
            continue
        text = str(value).strip()
        resolved[level] = text
        mask &= data[level].astype(str).str.strip().eq(text)
    match = data.loc[mask]
    if match.empty:
        raise HeightModelError(
            f"no height observations match species {species!r} and target {dict(target)}"
        )
    first = match.iloc[0]
    for level in levels:
        resolved.setdefault(level, str(first[level]).strip())
    return resolved


def _model_record(
    model,
    *,
    scope: str,
    target: Mapping[str, Any],
    resolved: Mapping[str, str],
    fit_level: str,
    fit_id: str,
    evidence_tier: str,
    age_window: tuple[float, float] | None,
) -> HeightModelRecord:
    target_id = str(
        target.get(
            "target_id",
            f"{fit_level}:{fit_id}|{resolved['species']}|"
            f"{age_window[0]:g}-{age_window[1]:g}" if age_window else f"{fit_level}:{fit_id}",
        )
    )
    return HeightModelRecord(
        scope=scope,
        target_level=str(target.get("target_level", fit_level)),
        target_id=target_id,
        fit_level=fit_level,
        fit_id=fit_id,
        species=resolved["species"],
        ecodistrict=resolved.get("ecodistrict"),
        ecoregion=resolved.get("ecoregion"),
        ecoprovince=resolved.get("ecoprovince"),
        ecozone=resolved.get("ecozone"),
        age_window=age_window,
        n=int(model.n),
        a=float(model.a),
        b=float(model.b),
        rmse_m=float(model.rmse_m),
        bias_m=float(model.bias_m),
        evidence_tier=evidence_tier,
    )


def fit_height_models(
    observations: pd.DataFrame,
    *,
    scope: str,
    targets: Iterable[Mapping[str, Any]] | None = None,
    species: Iterable[str] | None = None,
    age_window: tuple[float, float] | None = None,
    min_n: int = 30,
    levels: Sequence[str] = DEFAULT_LEVELS,
) -> HeightModelBundle:
    """Fit height models with ecodistrict -> ecozone -> species fallback."""
    if min_n < 3:
        raise ValueError("min_n must be at least 3")
    data = _clean(observations, levels)
    if age_window is not None:
        low, high = float(age_window[0]), float(age_window[1])
        if low > high:
            raise ValueError("age_window lower bound cannot exceed upper bound")
        data = data[(data["age"] >= low) & (data["age"] <= high)].copy()
        if data.empty:
            raise HeightModelError(
                f"no height observations in age window {low:g}-{high:g}"
            )

    requested_species = {str(code).strip().upper() for code in (species or ())}
    if targets is None:
        grouped = (
            data.groupby([levels[0], "species"], dropna=False)
            .size()
            .reset_index(name="n")
            .sort_values([levels[0], "species"])
        )
        generated: list[dict[str, Any]] = []
        for row in grouped.itertuples(index=False):
            if int(row.n) < min_n:
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

    models: list[HeightModelRecord] = []
    failures: list[dict[str, Any]] = []
    for target in targets:
        resolved = _resolve_target(target, data, levels)
        species_data = data[data["species"].eq(resolved["species"])].copy()
        fitted = False
        last_n = 0
        for level in levels:
            key = resolved.get(level)
            if key is None:
                continue
            subset = species_data[species_data[level].astype(str).str.strip().eq(key)]
            last_n = max(last_n, len(subset))
            if len(subset) < min_n:
                continue
            level_models = fit_height_diameter_models(
                subset,
                min_n=min_n,
                group_columns=("species", level),
            )
            model = level_models.get((resolved["species"], key))
            if model is None:
                continue
            evidence = "stratum_fit" if level == levels[0] else "pooled_fit"
            models.append(
                _model_record(
                    model,
                    scope=scope,
                    target=target,
                    resolved=resolved,
                    fit_level=level,
                    fit_id=f"{level}:{key}|{resolved['species']}",
                    evidence_tier=evidence,
                    age_window=age_window,
                )
            )
            fitted = True
            break
        if fitted:
            continue

        species_models = fit_height_diameter_models(
            species_data,
            min_n=min_n,
            group_columns=("species",),
        )
        model = species_models.get((resolved["species"],))
        if model is not None:
            models.append(
                _model_record(
                    model,
                    scope=scope,
                    target=target,
                    resolved=resolved,
                    fit_level="tsa24_species",
                    fit_id=f"tsa24_species:{resolved['species']}",
                    evidence_tier="default",
                    age_window=age_window,
                )
            )
            continue

        failures.append(
            {
                "scope": scope,
                "target_level": target.get("target_level", levels[0]),
                "target_id": target.get("target_id"),
                "species": resolved["species"],
                "ecodistrict": resolved.get("ecodistrict"),
                "ecoregion": resolved.get("ecoregion"),
                "ecoprovince": resolved.get("ecoprovince"),
                "ecozone": resolved.get("ecozone"),
                "age_window": (
                    f"{age_window[0]:g}-{age_window[1]:g}" if age_window else "all"
                ),
                "n": int(last_n),
                "error": f"no group reached min_n={min_n}",
            }
        )

    return HeightModelBundle(models=tuple(models), failures=tuple(failures))
