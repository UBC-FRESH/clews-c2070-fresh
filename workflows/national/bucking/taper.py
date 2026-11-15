"""Taper registry and the BC Kozak adapter.

Phase 0 implements one taper family: the Kozak (2004) Version 6 parameter
table used by ``nemora``.  The registry is keyed by
``(jurisdiction, species, BEC zone)`` and raises an explicit
``UnsupportedTaperError`` rather than silently substituting a curve.

The adapter is a thin, typed boundary around the reference implementation.
It does not refit, smooth, or average coefficients.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .schema import TreeRecord

try:  # pragma: no cover - exercised through the public adapter
    from nemora.workflows.taper import (
        KOZAK_LOCATIONS,
        KOZAK_SOURCE,
        KOZAK_SPECIES,
        KozakParameters,
        KozakTaperError,
        diameter_inside_bark,
        kozak_total_volume,
        load_kozak_parameters,
        params_for,
    )
except ImportError as exc:  # pragma: no cover - environment guard
    raise ImportError(
        "nemora is required for the BC taper adapter. Set NEMORA_ROOT and add "
        "its src directory to PYTHONPATH, or install nemora editable."
    ) from exc

__all__ = [
    "DEFAULT_JURISDICTION",
    "NemoraKozakTaper",
    "TaperDomainError",
    "TaperModel",
    "TaperRegistry",
    "UnsupportedTaperError",
    "default_kozak_parameter_path",
    "default_nemora_root",
]

DEFAULT_JURISDICTION = "BC"


class UnsupportedTaperError(ValueError):
    """Raised when no registered taper covers the requested key."""


class TaperDomainError(ValueError):
    """Raised when a tree falls outside the fitted taper domain."""


@runtime_checkable
class TaperModel(Protocol):
    """Structural contract for a single-species taper."""

    jurisdiction: str
    species: str
    bec_zone: str
    evidence: str
    proxy: bool
    parameters: KozakParameters

    def total_volume_m3(self, dbh_cm: float, height_m: float) -> float:
        """Total inside-bark volume from stump to tip, in cubic metres."""

    def diameter_inside_bark_cm(
        self, dbh_cm: float, height_m: float, height_above_ground_m: float
    ) -> float:
        """Inside-bark diameter at a height above ground, in centimetres."""


def default_nemora_root() -> Path:
    """Locate the external nemora checkout without baking it into outputs."""
    configured = os.environ.get("NEMORA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[3]
    sibling = repo_root.parent.parent / "ws4" / "nemora"
    if sibling.exists():
        return sibling
    raise FileNotFoundError(
        "nemora checkout not found; set NEMORA_ROOT or place it at "
        "<repo-parent>/../ws4/nemora"
    )


def default_kozak_parameter_path() -> Path:
    configured = os.environ.get("NEMORA_KOZAK_PARAMETERS")
    if configured:
        return Path(configured).expanduser().resolve()
    return default_nemora_root() / "config" / "kozak" / "kozak_parameters_v94.csv"


@dataclass(frozen=True, slots=True)
class NemoraKozakTaper:
    """Typed adapter around the nemora Kozak 2004 implementation."""

    jurisdiction: str
    species: str
    bec_zone: str
    parameters: KozakParameters
    proxy: bool = False
    evidence: str = "published_table"
    source: str = KOZAK_SOURCE

    def __post_init__(self) -> None:
        if self.evidence not in {"measured_taper", "published_table", "assumption"}:
            raise ValueError(f"unsupported evidence tier {self.evidence!r}")
        if not self.species or not self.bec_zone:
            raise ValueError("species and bec_zone are required")

    def total_volume_m3(self, dbh_cm: float, height_m: float) -> float:
        dbh = _require_tree_inputs(dbh_cm, height_m)
        try:
            volume = kozak_total_volume(dbh, height_m, self.parameters)
        except KozakTaperError as exc:
            raise TaperDomainError(
                f"{self.species} in {self.bec_zone}: taper domain error for "
                f"DBH {dbh_cm!r} cm and height {height_m!r} m: {exc}"
            ) from exc
        if not math.isfinite(volume) or volume < 0.0:
            raise TaperDomainError(
                f"{self.species} in {self.bec_zone}: non-finite or negative volume "
                f"for DBH {dbh_cm!r} cm and height {height_m!r} m"
            )
        return float(volume)

    def diameter_inside_bark_cm(
        self, dbh_cm: float, height_m: float, height_above_ground_m: float
    ) -> float:
        dbh = _require_tree_inputs(dbh_cm, height_m)
        if not 0.0 <= height_above_ground_m <= height_m + 1e-9:
            raise TaperDomainError("height_above_ground_m must be within [0, height_m]")
        try:
            diameter = float(
                diameter_inside_bark(height_above_ground_m, dbh, height_m, self.parameters)
            )
        except KozakTaperError as exc:
            raise TaperDomainError(
                f"{self.species} in {self.bec_zone}: taper domain error at "
                f"{height_above_ground_m!r} m: {exc}"
            ) from exc
        if not math.isfinite(diameter) or diameter < 0.0:
            raise TaperDomainError(
                f"{self.species} in {self.bec_zone}: non-finite or negative diameter "
                f"at {height_above_ground_m!r} m"
            )
        return diameter

    def manifest_record(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "species": self.species,
            "bec_zone": self.bec_zone,
            "evidence": self.evidence,
            "proxy": self.proxy,
            "source": self.source,
            "kozak_species_code": self.parameters.species,
            "kozak_location": self.parameters.location,
        }


def _require_tree_inputs(dbh_cm: float, height_m: float) -> float:
    dbh = float(dbh_cm)
    height = float(height_m)
    if not math.isfinite(dbh) or dbh <= 0.0:
        raise TaperDomainError(f"dbh_cm must be finite and positive, got {dbh_cm!r}")
    if not math.isfinite(height) or height <= 1.3:
        raise TaperDomainError(f"height_m must be finite and exceed 1.3 m, got {height_m!r}")
    return dbh


class TaperRegistry:
    """Explicit taper dispatcher keyed by jurisdiction, species, and BEC zone."""

    def __init__(
        self,
        parameter_table: dict[tuple[str, str], KozakParameters] | None = None,
        *,
        parameter_path: Path | None = None,
        jurisdiction: str = DEFAULT_JURISDICTION,
    ) -> None:
        self.jurisdiction = _require_jurisdiction(jurisdiction)
        self.parameter_path = Path(parameter_path) if parameter_path is not None else None
        self._parameters = (
            parameter_table
            if parameter_table is not None
            else load_kozak_parameters(self.parameter_path or default_kozak_parameter_path())
        )
        self._cache: dict[tuple[str, str, str], NemoraKozakTaper] = {}

    def resolve(self, jurisdiction: str, species: str, bec_zone: str) -> NemoraKozakTaper:
        """Return the taper for an exact key or fail loudly."""
        jur = _require_jurisdiction(jurisdiction)
        code = str(species).strip().upper()
        bec = str(bec_zone).strip().upper()
        if not code:
            raise UnsupportedTaperError("species is required")
        if not bec:
            raise UnsupportedTaperError("bec_zone is required")
        key = (jur, code, bec)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if jur != DEFAULT_JURISDICTION:
            raise UnsupportedTaperError(
                f"no taper registered for jurisdiction {jur!r}; Phase 0 implements BC only"
            )
        if code not in KOZAK_SPECIES:
            supported = ", ".join(sorted(KOZAK_SPECIES))
            raise UnsupportedTaperError(
                f"no Kozak species mapping for {code!r}; supported BC codes: {supported}"
            )
        if bec not in KOZAK_LOCATIONS:
            supported = ", ".join(sorted(KOZAK_LOCATIONS))
            raise UnsupportedTaperError(
                f"no Kozak location for BEC zone {bec!r}; supported zones: {supported}"
            )
        try:
            parameters = params_for(self._parameters, code, bec)
        except KozakTaperError as exc:
            raise UnsupportedTaperError(
                f"no Kozak parameters for {code!r} in {bec!r}: {exc}"
            ) from exc
        kozak_code, proxy = KOZAK_SPECIES[code]
        model = NemoraKozakTaper(
            jurisdiction=jur,
            species=code,
            bec_zone=bec,
            parameters=parameters,
            proxy=proxy,
            evidence="published_table",
            source=KOZAK_SOURCE,
        )
        self._cache[key] = model
        return model

    def for_tree(self, tree: TreeRecord, jurisdiction: str = DEFAULT_JURISDICTION) -> NemoraKozakTaper:
        return self.resolve(jurisdiction, tree.species, tree.bec_zone)

    @property
    def supported_species(self) -> tuple[str, ...]:
        return tuple(sorted(KOZAK_SPECIES))

    @property
    def supported_bec_zones(self) -> tuple[str, ...]:
        return tuple(sorted(KOZAK_LOCATIONS))

    def manifest_record(self) -> dict[str, Any]:
        return {
            "jurisdiction": self.jurisdiction,
            "taper_family": "Kozak (2004) Version 6",
            "source": KOZAK_SOURCE,
            "parameter_table": "config/kozak/kozak_parameters_v94.csv",
            "supported_species": list(self.supported_species),
            "supported_bec_zones": list(self.supported_bec_zones),
            "cached_keys": [
                {"jurisdiction": key[0], "species": key[1], "bec_zone": key[2]}
                for key in sorted(self._cache)
            ],
        }


def _require_jurisdiction(value: str) -> str:
    text = str(value).strip().upper()
    if not text:
        raise UnsupportedTaperError("jurisdiction is required")
    return text
