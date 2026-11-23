"""Typed contracts for the national tree-list bucking pipeline.

The contracts are deliberately small and strict.  Invalid records fail at
construction time so downstream code never has to defend against half-parsed
rows.  Every output record carries an evidence tier:

``measured_taper``
    Coefficients or measurements tied to the individual stem.
``published_table``
    A published equation or table applied to a measured tree list.
``assumption``
    A clearly labelled planning assumption (density, moisture, chip yield).

The schema is jurisdiction-neutral.  The Phase 0 adapters populate it for
British Columbia only.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "EVIDENCE_TIERS",
    "PRODUCTS",
    "BuckedLog",
    "GradeRule",
    "GradeRuleSet",
    "ResidueLedger",
    "TreeRecord",
]

EVIDENCE_TIERS: tuple[str, ...] = (
    "measured_taper",
    "published_table",
    "assumption",
)

PRODUCTS: tuple[str, ...] = (
    "sawlog",
    "peeler",
    "pulpwood",
    "other",
)


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _require_positive(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive, got {value!r}")
    return number


def _require_non_negative(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative, got {value!r}")
    return number


def _require_fraction(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number < 1.0:
        raise ValueError(f"{name} must be in [0, 1), got {value!r}")
    return number


@dataclass(frozen=True, slots=True)
class TreeRecord:
    """One measured or imputed stem.

    The field set follows the ``fhops`` tree-list contract and adds the BEC
    zone, stand age, site index, and observed volume columns needed by the BC
    pilot.  Optional fields are context, not alternate identities.
    """

    tree_id: str
    site_id: str
    species: str
    dbh_cm: float
    height_m: float
    status: str
    expansion_factor: float
    bec_zone: str
    age: float | None
    provenance: str
    plot_id: str | None = None
    visit_id: str | None = None
    species_group: str | None = None
    bec_subzone: str | None = None
    site_index: float | None = None
    stand_age: float | None = None
    observed_merchantable_m3: float | None = None
    observed_whole_stem_m3: float | None = None
    latitude: float | None = None
    longitude: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _require_text("tree_id", self.tree_id))
        object.__setattr__(self, "site_id", _require_text("site_id", self.site_id))
        object.__setattr__(self, "species", _require_text("species", self.species).upper())
        object.__setattr__(self, "bec_zone", _require_text("bec_zone", self.bec_zone).upper())
        object.__setattr__(self, "provenance", _require_text("provenance", self.provenance))
        object.__setattr__(self, "dbh_cm", _require_positive("dbh_cm", self.dbh_cm))
        object.__setattr__(self, "height_m", _require_positive("height_m", self.height_m))
        object.__setattr__(
            self, "expansion_factor", _require_positive("expansion_factor", self.expansion_factor)
        )
        if self.status not in {"live", "dead"}:
            raise ValueError(f"status must be 'live' or 'dead', got {self.status!r}")
        if self.age is not None:
            object.__setattr__(self, "age", _require_non_negative("age", self.age))
        if self.site_index is not None:
            object.__setattr__(
                self, "site_index", _require_non_negative("site_index", self.site_index)
            )
        if self.stand_age is not None:
            object.__setattr__(
                self, "stand_age", _require_non_negative("stand_age", self.stand_age)
            )
        for name in ("observed_merchantable_m3", "observed_whole_stem_m3"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_non_negative(name, value))

    @property
    def block_id(self) -> str:
        """Alias for the ``fhops`` block/site identifier."""
        return self.site_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BuckedLog:
    """One recovered log.

    ``evidence`` describes the taper/sort evidence, not an official grade.
    ``sort_basis`` is always ``dimension_only`` in Phase 0 and
    ``grade_status`` is ``not_assigned``.
    """

    tree_id: str
    log_index: int
    length_m: float
    large_end_dib_cm: float
    small_end_dib_cm: float
    volume_m3: float
    sort_code: str
    product: str
    price_cad_m3: float | None
    value_cad: float | None
    evidence: str
    site_id: str | None = None
    species: str | None = None
    species_market_group: str | None = None
    sort_basis: str = "dimension_only"
    grade_status: str = "not_assigned"
    price_period: str | None = None
    price_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _require_text("tree_id", self.tree_id))
        object.__setattr__(self, "sort_code", _require_text("sort_code", self.sort_code).upper())
        object.__setattr__(self, "product", _require_text("product", self.product).lower())
        object.__setattr__(self, "evidence", _require_text("evidence", self.evidence))
        if self.evidence not in EVIDENCE_TIERS:
            raise ValueError(f"evidence must be one of {EVIDENCE_TIERS}, got {self.evidence!r}")
        if int(self.log_index) < 1:
            raise ValueError("log_index must be at least 1")
        object.__setattr__(self, "log_index", int(self.log_index))
        object.__setattr__(self, "length_m", _require_positive("length_m", self.length_m))
        object.__setattr__(
            self,
            "large_end_dib_cm",
            _require_non_negative("large_end_dib_cm", self.large_end_dib_cm),
        )
        object.__setattr__(
            self,
            "small_end_dib_cm",
            _require_non_negative("small_end_dib_cm", self.small_end_dib_cm),
        )
        object.__setattr__(self, "volume_m3", _require_non_negative("volume_m3", self.volume_m3))
        if self.small_end_dib_cm > self.large_end_dib_cm + 1e-6:
            raise ValueError("small_end_dib_cm cannot exceed large_end_dib_cm")
        if self.price_cad_m3 is not None:
            object.__setattr__(
                self, "price_cad_m3", _require_non_negative("price_cad_m3", self.price_cad_m3)
            )
        if self.value_cad is not None:
            object.__setattr__(self, "value_cad", _require_non_negative("value_cad", self.value_cad))
        if (self.price_cad_m3 is None) != (self.value_cad is None):
            raise ValueError("price_cad_m3 and value_cad must both be present or both be None")
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be one of {PRODUCTS}, got {self.product!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ResidueLedger:
    """Residue and chip accounting for one stem.

    ``residue_volume_m3`` is the taper total minus recovered log volume.  It
    includes the stump, top, and any unmerchantable sections.  The density,
    moisture, and chip-yield fields are planning assumptions and must be
    labelled as such.
    """

    tree_id: str
    site_id: str
    residue_volume_m3: float
    basic_density_odt_m3: float
    residue_odt: float
    moisture_wet_basis: float
    green_tonnes: float
    chip_yield_fraction: float
    notes: str
    evidence: str = "assumption"
    chip_green_tonnes: float | None = None
    recovered_log_m3: float | None = None
    taper_total_m3: float | None = None
    bucker_residue_m3: float | None = None
    bucker_balance_error_m3: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _require_text("tree_id", self.tree_id))
        object.__setattr__(self, "site_id", _require_text("site_id", self.site_id))
        object.__setattr__(
            self, "residue_volume_m3", _require_non_negative("residue_volume_m3", self.residue_volume_m3)
        )
        object.__setattr__(
            self,
            "basic_density_odt_m3",
            _require_positive("basic_density_odt_m3", self.basic_density_odt_m3),
        )
        object.__setattr__(self, "residue_odt", _require_non_negative("residue_odt", self.residue_odt))
        object.__setattr__(
            self,
            "moisture_wet_basis",
            _require_fraction("moisture_wet_basis", self.moisture_wet_basis),
        )
        object.__setattr__(self, "green_tonnes", _require_non_negative("green_tonnes", self.green_tonnes))
        object.__setattr__(
            self,
            "chip_yield_fraction",
            _require_fraction("chip_yield_fraction", self.chip_yield_fraction),
        )
        object.__setattr__(self, "notes", _require_text("notes", self.notes))
        object.__setattr__(self, "evidence", _require_text("evidence", self.evidence))
        if self.evidence not in EVIDENCE_TIERS:
            raise ValueError(f"evidence must be one of {EVIDENCE_TIERS}, got {self.evidence!r}")
        expected_odt = self.residue_volume_m3 * self.basic_density_odt_m3
        if not math.isclose(self.residue_odt, expected_odt, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("residue_odt must equal residue_volume_m3 * basic_density_odt_m3")
        expected_green = self.residue_odt / (1.0 - self.moisture_wet_basis)
        if not math.isclose(self.green_tonnes, expected_green, rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("green_tonnes must equal residue_odt / (1 - moisture_wet_basis)")
        if self.chip_green_tonnes is not None:
            object.__setattr__(
                self,
                "chip_green_tonnes",
                _require_non_negative("chip_green_tonnes", self.chip_green_tonnes),
            )
            expected_chip = self.green_tonnes * self.chip_yield_fraction
            if not math.isclose(self.chip_green_tonnes, expected_chip, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("chip_green_tonnes must equal green_tonnes * chip_yield_fraction")
        for name in (
            "recovered_log_m3",
            "taper_total_m3",
            "bucker_residue_m3",
            "bucker_balance_error_m3",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _require_non_negative(name, value))
        if self.recovered_log_m3 is not None and self.taper_total_m3 is not None:
            balance = self.recovered_log_m3 + self.residue_volume_m3
            if not math.isclose(balance, self.taper_total_m3, rel_tol=1e-8, abs_tol=1e-8):
                raise ValueError(
                    "recovered_log_m3 + residue_volume_m3 must equal taper_total_m3"
                )
        if (
            self.bucker_balance_error_m3 is not None
            and self.recovered_log_m3 is not None
            and self.bucker_residue_m3 is not None
            and self.taper_total_m3 is not None
        ):
            expected_error = abs(
                self.recovered_log_m3 + self.bucker_residue_m3 - self.taper_total_m3
            )
            if not math.isclose(
                self.bucker_balance_error_m3, expected_error, rel_tol=1e-9, abs_tol=1e-9
            ):
                raise ValueError(
                    "bucker_balance_error_m3 must equal "
                    "abs(recovered_log_m3 + bucker_residue_m3 - taper_total_m3)"
                )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GradeRule:
    """One dimension-based sort rule.

    The rule is a geometric contract.  It does not encode grade, defect,
    sweep, knot, or rot; those are separate evidence problems.
    """

    species_class: str
    sort_code: str
    product: str
    min_top_cm: float
    min_length_m: float
    max_length_m: float | None
    trim_m: float
    priority: int
    price_key: str
    tipsy_group: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "species_class", _require_text("species_class", self.species_class).lower()
        )
        object.__setattr__(self, "sort_code", _require_text("sort_code", self.sort_code).upper())
        object.__setattr__(self, "product", _require_text("product", self.product).lower())
        object.__setattr__(self, "price_key", _require_text("price_key", self.price_key).lower())
        if self.tipsy_group is not None:
            object.__setattr__(
                self, "tipsy_group", _require_text("tipsy_group", self.tipsy_group)
            )
        object.__setattr__(self, "min_top_cm", _require_positive("min_top_cm", self.min_top_cm))
        object.__setattr__(
            self, "min_length_m", _require_positive("min_length_m", self.min_length_m)
        )
        if self.max_length_m is not None:
            max_length = _require_positive("max_length_m", self.max_length_m)
            if max_length < self.min_length_m:
                raise ValueError("max_length_m cannot be less than min_length_m")
            object.__setattr__(self, "max_length_m", max_length)
        object.__setattr__(self, "trim_m", _require_non_negative("trim_m", self.trim_m))
        if self.trim_m >= self.min_length_m:
            raise ValueError("trim_m must be less than min_length_m")
        if int(self.priority) < 0:
            raise ValueError("priority must be non-negative")
        object.__setattr__(self, "priority", int(self.priority))
        if self.product not in PRODUCTS:
            raise ValueError(f"product must be one of {PRODUCTS}, got {self.product!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GradeRuleSet:
    """A versioned, owned rule set."""

    jurisdiction: str
    authority: str
    version: str
    basis: str
    grade_status: str
    rules: tuple[GradeRule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "jurisdiction", _require_text("jurisdiction", self.jurisdiction).upper())
        object.__setattr__(self, "authority", _require_text("authority", self.authority))
        object.__setattr__(self, "version", _require_text("version", self.version))
        object.__setattr__(self, "basis", _require_text("basis", self.basis))
        object.__setattr__(self, "grade_status", _require_text("grade_status", self.grade_status))
        if not self.rules:
            raise ValueError("GradeRuleSet requires at least one rule")
        codes = [rule.sort_code for rule in self.rules]
        if len(codes) != len(set(codes)):
            raise ValueError("sort_code values must be unique within a rule set")

    def for_species_class(self, species_class: str) -> tuple[GradeRule, ...]:
        wanted = _require_text("species_class", species_class).lower()
        return tuple(rule for rule in self.rules if rule.species_class == wanted)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["rules"] = [rule.to_dict() for rule in self.rules]
        return payload
