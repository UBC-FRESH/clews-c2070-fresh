"""Strict record contracts for the national bucking Phase 0/2 pilot.

These records extend the Phase 0 contract with the provenance and evidence
columns the national pilot needs.  They are deliberately separate from the BC
``TreeRecord``/``BuckedLog`` classes so a pilot cannot accidentally present
Quebec geometry as BC inventory or vice versa.

Evidence tiers travel with every row.  A row-level ``evidence`` value is the
weakest tier among its components; the component tiers are retained in
``taper_evidence``, ``sort_evidence``, ``value_evidence``, ``tree_evidence``,
``density_evidence``, ``moisture_evidence`` and ``chip_evidence``.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "EVIDENCE_RANK",
    "EVIDENCE_TIERS",
    "PRODUCTS",
    "PilotBuckedLog",
    "PilotResidueLedger",
    "PilotTree",
    "SchemaError",
    "weakest_evidence",
]

EVIDENCE_TIERS: tuple[str, ...] = (
    "measured_taper",
    "published_table",
    "assumption",
)
EVIDENCE_RANK = {tier: index for index, tier in enumerate(EVIDENCE_TIERS)}
PRODUCTS: tuple[str, ...] = ("sawlog", "peeler", "pulpwood", "other")
SPECIES_GROUPS: tuple[str, ...] = ("conifer", "deciduous")


class SchemaError(ValueError):
    """Raised when a pilot record violates the contract."""


def weakest_evidence(tiers: Iterable[str]) -> str:
    values = [str(tier).strip() for tier in tiers if str(tier).strip()]
    if not values:
        raise SchemaError("at least one evidence tier is required")
    for tier in values:
        if tier not in EVIDENCE_TIERS:
            raise SchemaError(f"unsupported evidence tier {tier!r}")
    return max(values, key=lambda tier: EVIDENCE_RANK[tier])


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"{name} must be a non-empty string")
    return value.strip()


def _finite(name: str, value: float) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise SchemaError(f"{name} must be finite, got {value!r}")
    return number


def _positive(name: str, value: float) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise SchemaError(f"{name} must be positive, got {value!r}")
    return number


def _non_negative(name: str, value: float) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise SchemaError(f"{name} must be non-negative, got {value!r}")
    return number


def _fraction(name: str, value: float, *, allow_one: bool = False) -> float:
    number = _finite(name, value)
    upper_ok = number <= 1.0 if allow_one else number < 1.0
    if number < 0.0 or not upper_ok:
        raise SchemaError(f"{name} must be in [0, 1.0), got {value!r}")
    return number


def _evidence(name: str, value: str) -> str:
    tier = _text(name, value)
    if tier not in EVIDENCE_TIERS:
        raise SchemaError(f"{name} must be one of {EVIDENCE_TIERS}, got {tier!r}")
    return tier


@dataclass(frozen=True, slots=True)
class PilotTree:
    """One tree-list row.  Geometry may be measured or synthetic; the
    ``provenance`` and ``evidence`` columns say which."""

    tree_id: str
    site_id: str
    jurisdiction: str
    species: str
    species_group: str
    dbh_cm: float
    height_m: float
    defect_pct: float
    expansion_factor: float
    status: str
    provenance: str
    evidence: str
    taper_id: str

    def __post_init__(self) -> None:
        for name in ("tree_id", "site_id", "provenance", "taper_id"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "jurisdiction", _text("jurisdiction", self.jurisdiction).upper())
        object.__setattr__(self, "species", _text("species", self.species).upper())
        group = _text("species_group", self.species_group).lower()
        if group not in SPECIES_GROUPS:
            raise SchemaError(f"species_group must be one of {SPECIES_GROUPS}, got {group!r}")
        object.__setattr__(self, "species_group", group)
        object.__setattr__(self, "dbh_cm", _positive("dbh_cm", self.dbh_cm))
        object.__setattr__(self, "height_m", _positive("height_m", self.height_m))
        if self.height_m <= 1.3:
            raise SchemaError("height_m must exceed 1.3 m")
        defect = _finite("defect_pct", self.defect_pct)
        if not 0.0 <= defect <= 100.0:
            raise SchemaError("defect_pct must be in [0, 100]")
        object.__setattr__(self, "defect_pct", defect)
        object.__setattr__(
            self, "expansion_factor", _positive("expansion_factor", self.expansion_factor)
        )
        if self.status not in {"live", "dead"}:
            raise SchemaError(f"status must be 'live' or 'dead', got {self.status!r}")
        object.__setattr__(self, "evidence", _evidence("evidence", self.evidence))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PilotBuckedLog:
    """One recovered log with explicit gross, net and defect volumes."""

    tree_id: str
    jurisdiction: str
    log_index: int
    length_m: float
    large_end_dib_cm: float
    small_end_dib_cm: float
    volume_m3: float
    net_volume_m3: float
    defect_deduction_m3: float
    sort_code: str
    product: str
    grade: str
    grade_status: str
    sort_basis: str
    price_cad_m3: float
    value_cad: float
    evidence: str
    taper_evidence: str
    sort_evidence: str
    value_evidence: str
    tree_evidence: str
    taper_id: str
    taper_source: str
    grade_rule_id: str
    price_deck_id: str
    provenance: str
    quality: str = "not_classified"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _text("tree_id", self.tree_id))
        object.__setattr__(self, "jurisdiction", _text("jurisdiction", self.jurisdiction).upper())
        index = int(self.log_index)
        if index < 1:
            raise SchemaError("log_index must be at least 1")
        object.__setattr__(self, "log_index", index)
        object.__setattr__(self, "length_m", _positive("length_m", self.length_m))
        object.__setattr__(
            self, "large_end_dib_cm", _positive("large_end_dib_cm", self.large_end_dib_cm)
        )
        object.__setattr__(
            self, "small_end_dib_cm", _non_negative("small_end_dib_cm", self.small_end_dib_cm)
        )
        if self.small_end_dib_cm > self.large_end_dib_cm + 1e-6:
            raise SchemaError("small_end_dib_cm cannot exceed large_end_dib_cm")
        volume = _non_negative("volume_m3", self.volume_m3)
        net = _non_negative("net_volume_m3", self.net_volume_m3)
        defect = _non_negative("defect_deduction_m3", self.defect_deduction_m3)
        if not math.isclose(net + defect, volume, rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("net_volume_m3 + defect_deduction_m3 must equal volume_m3")
        object.__setattr__(self, "volume_m3", volume)
        object.__setattr__(self, "net_volume_m3", net)
        object.__setattr__(self, "defect_deduction_m3", defect)
        object.__setattr__(self, "sort_code", _text("sort_code", self.sort_code).upper())
        product = _text("product", self.product).lower()
        if product not in PRODUCTS:
            raise SchemaError(f"product must be one of {PRODUCTS}, got {product!r}")
        object.__setattr__(self, "product", product)
        for name in (
            "grade",
            "grade_status",
            "sort_basis",
            "taper_id",
            "taper_source",
            "grade_rule_id",
            "price_deck_id",
            "provenance",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "price_cad_m3", _non_negative("price_cad_m3", self.price_cad_m3))
        object.__setattr__(self, "value_cad", _non_negative("value_cad", self.value_cad))
        quality = _text("quality", self.quality)
        if quality.lower() == "not_classified":
            quality = "not_classified"
        else:
            quality = quality.upper()
        if quality not in {"not_classified", "DER", "F1", "F2", "F3", "F4", "PATE", "AUTRE"}:
            raise SchemaError(f"unsupported quality class {quality!r}")
        object.__setattr__(self, "quality", quality)
        expected = net * self.price_cad_m3
        if not math.isclose(self.value_cad, expected, rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("value_cad must equal net_volume_m3 * price_cad_m3")
        for name in ("evidence", "taper_evidence", "sort_evidence", "value_evidence", "tree_evidence"):
            object.__setattr__(self, name, _evidence(name, getattr(self, name)))
        expected_row = weakest_evidence(
            (self.taper_evidence, self.sort_evidence, self.value_evidence, self.tree_evidence)
        )
        if self.evidence != expected_row:
            raise SchemaError(
                f"row evidence {self.evidence!r} must be the weakest component "
                f"tier {expected_row!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PilotResidueLedger:
    """Residue, chip and hog accounting for one stem."""

    tree_id: str
    jurisdiction: str
    species: str
    residue_volume_m3: float
    basic_density_odt_m3: float
    residue_odt: float
    moisture_wet_basis: float
    green_tonnes: float
    chip_yield_fraction: float
    chip_green_tonnes: float
    hog_yield_fraction: float
    hog_green_tonnes: float
    recovered_log_m3: float
    taper_total_m3: float
    balance_error_m3: float
    evidence: str
    taper_evidence: str
    density_evidence: str
    moisture_evidence: str
    chip_evidence: str
    provenance: str
    notes: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "tree_id", _text("tree_id", self.tree_id))
        object.__setattr__(self, "jurisdiction", _text("jurisdiction", self.jurisdiction).upper())
        object.__setattr__(self, "species", _text("species", self.species).upper())
        volume = _non_negative("residue_volume_m3", self.residue_volume_m3)
        density = _positive("basic_density_odt_m3", self.basic_density_odt_m3)
        odt = _non_negative("residue_odt", self.residue_odt)
        moisture = _fraction("moisture_wet_basis", self.moisture_wet_basis)
        green = _non_negative("green_tonnes", self.green_tonnes)
        chip_fraction = _fraction("chip_yield_fraction", self.chip_yield_fraction, allow_one=True)
        hog_fraction = _fraction("hog_yield_fraction", self.hog_yield_fraction, allow_one=True)
        if chip_fraction + hog_fraction > 1.0 + 1e-12:
            raise SchemaError("chip_yield_fraction + hog_yield_fraction cannot exceed 1")
        chip = _non_negative("chip_green_tonnes", self.chip_green_tonnes)
        hog = _non_negative("hog_green_tonnes", self.hog_green_tonnes)
        recovered = _non_negative("recovered_log_m3", self.recovered_log_m3)
        total = _non_negative("taper_total_m3", self.taper_total_m3)
        for name, value in (
            ("residue_volume_m3", volume),
            ("basic_density_odt_m3", density),
            ("residue_odt", odt),
            ("moisture_wet_basis", moisture),
            ("green_tonnes", green),
            ("chip_yield_fraction", chip_fraction),
            ("chip_green_tonnes", chip),
            ("hog_yield_fraction", hog_fraction),
            ("hog_green_tonnes", hog),
            ("recovered_log_m3", recovered),
            ("taper_total_m3", total),
        ):
            object.__setattr__(self, name, value)
        if not math.isclose(odt, volume * density, rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("residue_odt must equal residue_volume_m3 * basic_density_odt_m3")
        if not math.isclose(green, odt / (1.0 - moisture), rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("green_tonnes must equal residue_odt / (1 - moisture_wet_basis)")
        if not math.isclose(chip, green * chip_fraction, rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("chip_green_tonnes must equal green_tonnes * chip_yield_fraction")
        if not math.isclose(hog, green * hog_fraction, rel_tol=1e-9, abs_tol=1e-9):
            raise SchemaError("hog_green_tonnes must equal green_tonnes * hog_yield_fraction")
        if not math.isclose(recovered + volume, total, rel_tol=1e-8, abs_tol=1e-8):
            raise SchemaError("recovered_log_m3 + residue_volume_m3 must equal taper_total_m3")
        expected_error = abs(recovered + volume - total)
        if not math.isclose(
            float(self.balance_error_m3), expected_error, rel_tol=1e-9, abs_tol=1e-9
        ):
            raise SchemaError("balance_error_m3 must equal abs(recovered + residue - total)")
        object.__setattr__(self, "balance_error_m3", expected_error)
        for name in (
            "evidence",
            "taper_evidence",
            "density_evidence",
            "moisture_evidence",
            "chip_evidence",
        ):
            object.__setattr__(self, name, _evidence(name, getattr(self, name)))
        expected_row = weakest_evidence(
            (
                self.taper_evidence,
                self.density_evidence,
                self.moisture_evidence,
                self.chip_evidence,
            )
        )
        if self.evidence != expected_row:
            raise SchemaError(
                f"row evidence {self.evidence!r} must be the weakest component "
                f"tier {expected_row!r}"
            )
        object.__setattr__(self, "provenance", _text("provenance", self.provenance))
        object.__setattr__(self, "notes", _text("notes", self.notes))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
