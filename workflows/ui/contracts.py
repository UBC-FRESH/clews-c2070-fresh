"""Versioned scenario contract for the CLEWS/C2070 live runner.

The contract is deliberately closed: every nested model rejects unknown
fields, so a typo in a scenario payload fails at the API boundary instead of
silently changing a model assumption downstream.

``clews.scenario.v1`` is the only supported schema version.  The runner CLI in
:mod:`workflows.ui.run_scenario` parses scenarios through this module; the real
OSeCAN adapter in milestone 1b must do the same.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

__all__ = [
    "ALLOWED_CONSTRAINT_SET_IDS",
    "SCENARIO_ID_PATTERN",
    "SCHEMA_VERSION",
    "YEAR_MAX",
    "YEAR_MIN",
    "CarbonPricePoint",
    "ConstraintSet",
    "CostBasis",
    "EnergySpec",
    "HarvestAllocation",
    "HarvestPath",
    "HarvestSpec",
    "HydroMode",
    "Provenance",
    "Scenario",
    "SupplyCurveMode",
    "load_scenario",
    "parse_scenario",
    "validate_scenario_id",
]

SCHEMA_VERSION = "clews.scenario.v1"
ALLOWED_CONSTRAINT_SET_IDS = frozenset({"screening_v0", "policy_test_v1"})
YEAR_MIN = 2021
YEAR_MAX = 2070
SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ID_MAX_LENGTH = 64
_LABEL_MAX_LENGTH = 200
_DESCRIPTION_MAX_LENGTH = 4000
_COMMIT_MAX_LENGTH = 128

def _reject_boolean_number(value: Any) -> Any:
    """Reject ``true``/``false`` where the contract requires a number."""

    if isinstance(value, bool):
        raise ValueError(  # noqa: TRY004 - pydantic converts this to a field error
            "boolean values are not valid numbers in a scenario"
        )
    return value


_FiniteFloat = Annotated[FiniteFloat, BeforeValidator(_reject_boolean_number)]
_PositiveFloat = Annotated[_FiniteFloat, Field(gt=0)]
_NonNegativeFloat = Annotated[_FiniteFloat, Field(ge=0)]
_UnitFraction = Annotated[_FiniteFloat, Field(gt=0, le=1)]


class _StrictModel(BaseModel):
    """Reject unknown fields and make parsed scenarios immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class HarvestPath(StrEnum):
    """Reference harvest trajectory applied from 2025 onward."""

    OBSERVED = "observed"
    HOLD = "hold"
    LOW = "low"
    HIGH = "high"
    CUSTOM = "custom"


class HarvestAllocation(StrEnum):
    """Provincial harvest allocation used to weight national residue factors.

    ``none`` (and an absent field) preserves the area-share national factor.
    The ``lp_*`` values select the screening provincial-allocation LP artifacts;
    ``observed`` weights by 2024 observed provincial harvest.
    """

    OBSERVED = "observed"
    LP_100M = "lp_100m"
    LP_119M = "lp_119m"
    LP_150M = "lp_150m"
    NONE = "none"


class SupplyCurveMode(StrEnum):
    """How recoverable biomass is offered to the electricity model."""

    UNIFIED = "unified"
    TIERED = "tiered"


class HydroMode(StrEnum):
    """Hydro treatment; ``cap_2021`` is an upper bound, not fixed dispatch."""

    NONE = "none"
    CAP_2021 = "cap_2021"
    FIXED_2021 = "fixed_2021"


class CostBasis(StrEnum):
    """Documented FRESH supply-cost variants."""

    ADOPTED_REST_OF_CANADA_CURVE = "adopted_rest_of_canada_curve"
    BASE = "base"
    LOW = "low"
    HIGH = "high"
    CUSTOM = "custom"


class CarbonPricePoint(_StrictModel):
    """One step of a piecewise-constant national carbon price schedule."""

    year: int = Field(strict=True, ge=YEAR_MIN, le=YEAR_MAX)
    price_usd_per_tco2e: _NonNegativeFloat


class HarvestSpec(_StrictModel):
    """Harvest volume and residue-recovery assumptions for one scenario."""

    level_m3_per_year: _PositiveFloat
    path: HarvestPath
    recovery_fraction: _UnitFraction
    cost_basis: CostBasis
    supply_curve: SupplyCurveMode
    allocation: HarvestAllocation | None = None


class EnergySpec(_StrictModel):
    """Electric-system levers exposed by the live scenario runner."""

    carbon_price_schedule: Annotated[
        tuple[CarbonPricePoint, ...], Field(min_length=1, max_length=50)
    ]
    hydro_mode: HydroMode
    pwr_bio_cap_fraction: Annotated[_FiniteFloat, Field(ge=0, le=1)]
    nuclear_cap_gw: _NonNegativeFloat
    wind_cost_usd_per_gj: _NonNegativeFloat
    wind_cap_fraction: Annotated[_FiniteFloat, Field(ge=0, le=1)]
    t3_cap_pj: _NonNegativeFloat
    t4_cap_pj: _NonNegativeFloat
    generic_bio_cost_usd_per_gj: _NonNegativeFloat

    @field_validator("carbon_price_schedule", mode="before")
    @classmethod
    def _require_schedule_array(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)):
            raise ValueError(  # noqa: TRY004 - pydantic wraps ValueError as a field error
                "carbon_price_schedule must be a list of "
                "{year, price_usd_per_tco2e} objects"
            )
        return value

    @model_validator(mode="after")
    def _sort_and_deduplicate_schedule(self) -> EnergySpec:
        ordered = sorted(self.carbon_price_schedule, key=lambda point: point.year)
        years = [point.year for point in ordered]
        if len(years) != len(set(years)):
            raise ValueError("carbon_price_schedule years must be unique")
        return self.model_copy(update={"carbon_price_schedule": tuple(ordered)})


class ConstraintSet(_StrictModel):
    """Named constraint family accepted by the live runner."""

    set_id: str = Field(
        min_length=1,
        max_length=_ID_MAX_LENGTH,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$",
    )

    @field_validator("set_id")
    @classmethod
    def _require_supported_set(cls, value: str) -> str:
        if value not in ALLOWED_CONSTRAINT_SET_IDS:
            supported = ", ".join(sorted(ALLOWED_CONSTRAINT_SET_IDS))
            raise ValueError(
                f"unsupported constraint set {value!r}; supported sets: {supported}"
            )
        return value


class Provenance(_StrictModel):
    """Reproducibility pointers for a scenario file."""

    base_data_commit: str = Field(min_length=1, max_length=_COMMIT_MAX_LENGTH)
    model_commit: str = Field(min_length=1, max_length=_COMMIT_MAX_LENGTH)
    created_utc: datetime

    @field_validator("created_utc")
    @classmethod
    def _require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_utc must include a timezone offset")
        return value.astimezone(UTC)

    @field_validator("base_data_commit", "model_commit")
    @classmethod
    def _reject_padding(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("commit identifiers must not be blank")
        return stripped


class Scenario(_StrictModel):
    """A complete, validated ``clews.scenario.v1`` payload."""

    scenario_id: str = Field(pattern=SCENARIO_ID_PATTERN.pattern)
    label: str = Field(min_length=1, max_length=_LABEL_MAX_LENGTH)
    description: str = Field(default="", max_length=_DESCRIPTION_MAX_LENGTH)
    schema_version: Literal["clews.scenario.v1"]
    harvest: HarvestSpec
    energy: EnergySpec
    constraints: ConstraintSet
    provenance: Provenance

    @field_validator("label")
    @classmethod
    def _require_visible_label(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("label must not be blank")
        return stripped

    @field_validator("description")
    @classmethod
    def _strip_description(cls, value: str) -> str:
        return value.strip()


def validate_scenario_id(value: str) -> str:
    """Return a safe scenario identifier or raise ``ValueError``.

    The identifier doubles as a filename stem, so only lowercase ASCII
    letters, digits, underscores, and hyphens are accepted.  Path separators
    and ``..`` are impossible by construction.
    """

    if not SCENARIO_ID_PATTERN.fullmatch(value):
        raise ValueError("scenario_id must match ^[a-z0-9][a-z0-9_-]{0,63}$")
    return value


def parse_scenario(payload: Any) -> Scenario:
    """Parse an untrusted mapping into a trusted :class:`Scenario`."""

    return Scenario.model_validate(payload)


def load_scenario(path: Path) -> Scenario:
    """Read and validate a scenario JSON file."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read scenario {path}: {exc}") from exc
    return parse_scenario(payload)
