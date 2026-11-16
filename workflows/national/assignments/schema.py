"""Typed Stage A facility-assignment contract.

Stage A turns an assortment supply ledger and an FFCM-derived facility network
into a linear transportation/allocation problem.  This module is the parsed
boundary: every record is validated once when it is constructed, and the model
and validator then consume trusted objects instead of raw CSV strings.

The Stage B contract remains in :mod:`fresh_fibre.schema` and
:mod:`fresh_fibre.graph`.  :func:`to_stage_b_tables` emits the subset of that
contract that the Stage A pilot can populate without inventing data.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FACILITY_CLASSES",
    "REJECTED_CAPACITY_UNITS",
    "SUPPORTED_CAPACITY_UNITS",
    "Assignment",
    "AssignmentError",
    "AssignmentInputs",
    "AssignmentResult",
    "Contract",
    "FacilityCapacity",
    "FacilityOffer",
    "FeedstockGrade",
    "SupplyOffer",
    "TransportLink",
    "UnassignedSupply",
    "capacity_unit_is_supported",
    "require_supported_capacity_unit",
    "to_stage_b_tables",
]


class AssignmentError(ValueError):
    """Raised when a Stage A record or problem is internally inconsistent."""


FACILITY_CLASSES = frozenset({"SAW", "PAP", "PEL", "BIO"})
SUPPORTED_CAPACITY_UNITS = frozenset({"m3/y", "odt/y"})
REJECTED_CAPACITY_UNITS = frozenset(
    {"bdmt/y", "admt/y", "mbf/y", "kwh/y", "t/y", "kg/y", "gj/y", "mwh/y"}
)
_SUPPORTED_GRADE_UNITS = frozenset({"m3", "odt"})
_SUPPORTED_TRANSPORT_MODES = frozenset({"ROAD"})
_TOLERANCE = 1e-9


def _finite(value: object, name: str, *, minimum: float | None = None) -> float:
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise AssignmentError(f"{name} must be finite")
    if minimum is not None and number < minimum:
        raise AssignmentError(f"{name} must be >= {minimum}")
    return number


def _identifier(value: object, name: str) -> str:
    text = str(value).strip()
    if not text:
        raise AssignmentError(f"{name} is required")
    return text


def _uppercase(value: object, name: str) -> str:
    return _identifier(value, name).upper()


def capacity_unit_is_supported(unit: str) -> bool:
    """Return True only for capacity units the pilot can normalize explicitly."""
    return str(unit).strip().lower() in SUPPORTED_CAPACITY_UNITS


def require_supported_capacity_unit(unit: str) -> str:
    """Fail closed on a mixed or product-specific capacity unit."""
    normalized = str(unit).strip().lower()
    if normalized in SUPPORTED_CAPACITY_UNITS:
        return normalized
    if normalized in REJECTED_CAPACITY_UNITS:
        raise AssignmentError(
            f"capacity unit {unit!r} is rejected; supported units are "
            f"{sorted(SUPPORTED_CAPACITY_UNITS)}"
        )
    raise AssignmentError(
        f"capacity unit {unit!r} is unknown; supported units are "
        f"{sorted(SUPPORTED_CAPACITY_UNITS)}"
    )


@dataclass(frozen=True, slots=True)
class FeedstockGrade:
    """A buyer-facing grade with an explicit quantity and moisture basis."""

    grade_id: str
    product: str
    unit: str
    accepted_sorts: tuple[str, ...]
    density_odt_m3: float
    min_top_cm: float = 0.0
    max_residue_share: float = 1.0
    moisture_wet_basis: float = 0.45
    description: str = ""
    evidence: str = "assumption"

    def __post_init__(self) -> None:
        grade_id = _uppercase(self.grade_id, "grade_id")
        product = _identifier(self.product, "product").lower()
        unit = _identifier(self.unit, "unit").lower()
        if unit not in _SUPPORTED_GRADE_UNITS:
            raise AssignmentError(
                f"grade {grade_id} unit must be one of {sorted(_SUPPORTED_GRADE_UNITS)}"
            )
        sorts = tuple(
            _uppercase(value, "accepted_sort") for value in self.accepted_sorts
        )
        if not sorts:
            raise AssignmentError(f"grade {grade_id} needs at least one accepted sort")
        density = _finite(self.density_odt_m3, "density_odt_m3", minimum=0.0)
        if density <= 0.0:
            raise AssignmentError("density_odt_m3 must be positive")
        min_top = _finite(self.min_top_cm, "min_top_cm", minimum=0.0)
        max_residue = _finite(self.max_residue_share, "max_residue_share", minimum=0.0)
        if max_residue > 1.0:
            raise AssignmentError("max_residue_share cannot exceed 1.0")
        moisture = _finite(self.moisture_wet_basis, "moisture_wet_basis", minimum=0.0)
        if moisture >= 1.0:
            raise AssignmentError("moisture_wet_basis must be in [0, 1)")
        object.__setattr__(self, "grade_id", grade_id)
        object.__setattr__(self, "product", product)
        object.__setattr__(self, "unit", unit)
        object.__setattr__(self, "accepted_sorts", sorts)
        object.__setattr__(self, "density_odt_m3", density)
        object.__setattr__(self, "min_top_cm", min_top)
        object.__setattr__(self, "max_residue_share", max_residue)
        object.__setattr__(self, "moisture_wet_basis", moisture)
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))

    def accepts_sort(self, sort_code: str, top_cm: float) -> bool:
        return (
            _uppercase(sort_code, "sort_code") in self.accepted_sorts
            and float(top_cm) >= self.min_top_cm
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade_id": self.grade_id,
            "product": self.product,
            "unit": self.unit,
            "accepted_sorts": ";".join(self.accepted_sorts),
            "density_odt_m3": self.density_odt_m3,
            "min_top_cm": self.min_top_cm,
            "max_residue_share": self.max_residue_share,
            "moisture_wet_basis": self.moisture_wet_basis,
            "description": self.description,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class SupplyOffer:
    """One assortment stream at one origin and period.

    ``volume_m3`` is always a green-solid-volume basis.  Residue offers carry
    the same basis and expose ``volume_odt`` through the declared density; this
    prevents an implicit m3/ODT conversion inside the LP.
    """

    offer_id: str
    period: str
    grade_id: str
    product: str
    sort_code: str
    top_cm: float
    origin: str
    volume_m3: float
    density_odt_m3: float
    moisture_wet_basis: float
    roadside_cost_cad_m3: float
    evidence: str
    source_ref: str = ""

    def __post_init__(self) -> None:
        offer_id = _identifier(self.offer_id, "offer_id")
        period = _identifier(self.period, "period")
        grade_id = _uppercase(self.grade_id, "grade_id")
        product = _identifier(self.product, "product").lower()
        sort_code = _uppercase(self.sort_code, "sort_code")
        top_cm = _finite(self.top_cm, "top_cm", minimum=0.0)
        origin = _identifier(self.origin, "origin")
        volume = _finite(self.volume_m3, "volume_m3", minimum=0.0)
        if volume <= 0.0:
            raise AssignmentError(f"offer {offer_id} volume_m3 must be positive")
        density = _finite(self.density_odt_m3, "density_odt_m3", minimum=0.0)
        if density <= 0.0:
            raise AssignmentError("density_odt_m3 must be positive")
        moisture = _finite(self.moisture_wet_basis, "moisture_wet_basis", minimum=0.0)
        if moisture >= 1.0:
            raise AssignmentError("moisture_wet_basis must be in [0, 1)")
        cost = _finite(self.roadside_cost_cad_m3, "roadside_cost_cad_m3")
        object.__setattr__(self, "offer_id", offer_id)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "grade_id", grade_id)
        object.__setattr__(self, "product", product)
        object.__setattr__(self, "sort_code", sort_code)
        object.__setattr__(self, "top_cm", top_cm)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "volume_m3", volume)
        object.__setattr__(self, "density_odt_m3", density)
        object.__setattr__(self, "moisture_wet_basis", moisture)
        object.__setattr__(self, "roadside_cost_cad_m3", cost)
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))
        object.__setattr__(self, "source_ref", str(self.source_ref).strip())

    @property
    def volume_odt(self) -> float:
        return self.volume_m3 * self.density_odt_m3

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "period": self.period,
            "grade_id": self.grade_id,
            "product": self.product,
            "sort_code": self.sort_code,
            "top_cm": self.top_cm,
            "origin": self.origin,
            "volume_m3": self.volume_m3,
            "volume_odt": self.volume_odt,
            "density_odt_m3": self.density_odt_m3,
            "moisture_wet_basis": self.moisture_wet_basis,
            "roadside_cost_cad_m3": self.roadside_cost_cad_m3,
            "evidence": self.evidence,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True, slots=True)
class FacilityOffer:
    """One facility's offer to accept one grade at one period.

    ``capacity_m3_y`` is a grade-specific alternative capacity, not an additive
    capacity across grades.  The facility total in :class:`FacilityCapacity`
    remains the binding physical limit.
    """

    offer_id: str
    period: str
    facility_id: str
    facility_name: str
    facility_class: str
    grade_id: str
    primary_product: str
    capacity_m3_y: float
    processing_cost_cad_m3: float
    product_revenue_cad_m3: float
    capacity_basis: str
    cost_basis: str
    revenue_basis: str
    evidence: str

    def __post_init__(self) -> None:
        offer_id = _identifier(self.offer_id, "offer_id")
        period = _identifier(self.period, "period")
        facility_id = _identifier(self.facility_id, "facility_id")
        facility_name = _identifier(self.facility_name, "facility_name")
        facility_class = _uppercase(self.facility_class, "facility_class")
        if facility_class not in FACILITY_CLASSES:
            raise AssignmentError(
                f"facility {facility_id} class {facility_class!r} is outside the "
                f"Stage A pilot scope {sorted(FACILITY_CLASSES)}"
            )
        grade_id = _uppercase(self.grade_id, "grade_id")
        primary_product = _identifier(self.primary_product, "primary_product").lower()
        capacity = _finite(self.capacity_m3_y, "capacity_m3_y", minimum=0.0)
        if capacity <= 0.0:
            raise AssignmentError(
                f"facility offer {offer_id} capacity must be positive"
            )
        processing = _finite(
            self.processing_cost_cad_m3, "processing_cost_cad_m3", minimum=0.0
        )
        revenue = _finite(
            self.product_revenue_cad_m3, "product_revenue_cad_m3", minimum=0.0
        )
        object.__setattr__(self, "offer_id", offer_id)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "facility_id", facility_id)
        object.__setattr__(self, "facility_name", facility_name)
        object.__setattr__(self, "facility_class", facility_class)
        object.__setattr__(self, "grade_id", grade_id)
        object.__setattr__(self, "primary_product", primary_product)
        object.__setattr__(self, "capacity_m3_y", capacity)
        object.__setattr__(self, "processing_cost_cad_m3", processing)
        object.__setattr__(self, "product_revenue_cad_m3", revenue)
        object.__setattr__(
            self, "capacity_basis", _identifier(self.capacity_basis, "capacity_basis")
        )
        object.__setattr__(
            self, "cost_basis", _identifier(self.cost_basis, "cost_basis")
        )
        object.__setattr__(
            self, "revenue_basis", _identifier(self.revenue_basis, "revenue_basis")
        )
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))

    @property
    def net_margin_cad_m3(self) -> float:
        return self.product_revenue_cad_m3 - self.processing_cost_cad_m3

    def to_dict(self) -> dict[str, Any]:
        return {
            "offer_id": self.offer_id,
            "period": self.period,
            "facility_id": self.facility_id,
            "facility_name": self.facility_name,
            "facility_class": self.facility_class,
            "grade_id": self.grade_id,
            "primary_product": self.primary_product,
            "capacity_m3_y": self.capacity_m3_y,
            "processing_cost_cad_m3": self.processing_cost_cad_m3,
            "product_revenue_cad_m3": self.product_revenue_cad_m3,
            "net_margin_cad_m3": self.net_margin_cad_m3,
            "capacity_basis": self.capacity_basis,
            "cost_basis": self.cost_basis,
            "revenue_basis": self.revenue_basis,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class FacilityCapacity:
    """Facility-level nameplate plus grade-specific alternative capacities."""

    facility_id: str
    facility_name: str
    facility_class: str
    nameplate_m3_y: float
    offers: tuple[FacilityOffer, ...]
    evidence: str

    def __post_init__(self) -> None:
        facility_id = _identifier(self.facility_id, "facility_id")
        facility_name = _identifier(self.facility_name, "facility_name")
        facility_class = _uppercase(self.facility_class, "facility_class")
        if facility_class not in FACILITY_CLASSES:
            raise AssignmentError(
                f"facility {facility_id} class {facility_class!r} is outside scope"
            )
        nameplate = _finite(self.nameplate_m3_y, "nameplate_m3_y", minimum=0.0)
        if nameplate <= 0.0:
            raise AssignmentError(f"facility {facility_id} nameplate must be positive")
        offers = tuple(self.offers)
        if not offers:
            raise AssignmentError(
                f"facility {facility_id} needs at least one grade offer"
            )
        grades = [offer.grade_id for offer in offers]
        if len(set(grades)) != len(grades):
            raise AssignmentError(f"facility {facility_id} has duplicate grade offers")
        for offer in offers:
            if offer.facility_id != facility_id:
                raise AssignmentError(
                    f"facility {facility_id} contains offer for {offer.facility_id}"
                )
            if offer.capacity_m3_y > nameplate + _TOLERANCE:
                raise AssignmentError(
                    f"facility {facility_id} grade {offer.grade_id} capacity exceeds nameplate"
                )
        object.__setattr__(self, "facility_id", facility_id)
        object.__setattr__(self, "facility_name", facility_name)
        object.__setattr__(self, "facility_class", facility_class)
        object.__setattr__(self, "nameplate_m3_y", nameplate)
        object.__setattr__(
            self, "offers", tuple(sorted(offers, key=lambda item: item.grade_id))
        )
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))

    @property
    def eligible_grades(self) -> tuple[str, ...]:
        return tuple(offer.grade_id for offer in self.offers)

    def offer_for(self, grade_id: str) -> FacilityOffer | None:
        grade = _uppercase(grade_id, "grade_id")
        return next((offer for offer in self.offers if offer.grade_id == grade), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "facility_id": self.facility_id,
            "facility_name": self.facility_name,
            "facility_class": self.facility_class,
            "nameplate_m3_y": self.nameplate_m3_y,
            "eligible_grades": ";".join(self.eligible_grades),
            "offer_count": len(self.offers),
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class TransportLink:
    """A directed diagnostic transport link with an explicit cost basis."""

    link_id: str
    period: str
    origin: str
    destination: str
    grade_id: str
    mode: str
    distance_km: float
    cost_cad_m3: float
    capacity_m3: float | None
    distance_basis: str
    cost_basis: str
    evidence: str

    def __post_init__(self) -> None:
        link_id = _identifier(self.link_id, "link_id")
        period = _identifier(self.period, "period")
        origin = _identifier(self.origin, "origin")
        destination = _identifier(self.destination, "destination")
        grade_id = str(self.grade_id).strip().upper()
        mode = _uppercase(self.mode, "mode")
        if mode not in _SUPPORTED_TRANSPORT_MODES:
            raise AssignmentError(
                f"transport mode {mode!r} is outside the Stage A road pilot"
            )
        distance = _finite(self.distance_km, "distance_km", minimum=0.0)
        cost = _finite(self.cost_cad_m3, "cost_cad_m3", minimum=0.0)
        capacity = (
            None
            if self.capacity_m3 is None
            else _finite(self.capacity_m3, "capacity_m3", minimum=0.0)
        )
        if capacity is not None and capacity <= 0.0:
            raise AssignmentError(f"link {link_id} capacity_m3 must be positive")
        object.__setattr__(self, "link_id", link_id)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "origin", origin)
        object.__setattr__(self, "destination", destination)
        object.__setattr__(self, "grade_id", grade_id)
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "distance_km", distance)
        object.__setattr__(self, "cost_cad_m3", cost)
        object.__setattr__(self, "capacity_m3", capacity)
        object.__setattr__(
            self, "distance_basis", _identifier(self.distance_basis, "distance_basis")
        )
        object.__setattr__(
            self, "cost_basis", _identifier(self.cost_basis, "cost_basis")
        )
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "period": self.period,
            "origin": self.origin,
            "destination": self.destination,
            "grade_id": self.grade_id,
            "mode": self.mode,
            "distance_km": self.distance_km,
            "cost_cad_m3": self.cost_cad_m3,
            "capacity_m3": "" if self.capacity_m3 is None else self.capacity_m3,
            "distance_basis": self.distance_basis,
            "cost_basis": self.cost_basis,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class Contract:
    """A contract with explicit grade, volume, and gate-cost bounds."""

    contract_id: str
    period: str
    facility_id: str
    accepted_grades: tuple[str, ...]
    min_volume_m3: float
    max_volume_m3: float
    gate_cost_cad_m3: float
    evidence: str
    diagnostic: bool = True
    notes: str = ""

    def __post_init__(self) -> None:
        contract_id = _identifier(self.contract_id, "contract_id")
        period = _identifier(self.period, "period")
        facility_id = _identifier(self.facility_id, "facility_id")
        grades = tuple(
            _uppercase(value, "accepted_grade") for value in self.accepted_grades
        )
        if not grades:
            raise AssignmentError(
                f"contract {contract_id} needs at least one accepted grade"
            )
        minimum = _finite(self.min_volume_m3, "min_volume_m3", minimum=0.0)
        maximum = _finite(self.max_volume_m3, "max_volume_m3", minimum=0.0)
        if maximum <= 0.0:
            raise AssignmentError("max_volume_m3 must be positive")
        if minimum > maximum:
            raise AssignmentError("contract volumes must satisfy 0 <= min <= max")
        gate = _finite(self.gate_cost_cad_m3, "gate_cost_cad_m3", minimum=0.0)
        object.__setattr__(self, "contract_id", contract_id)
        object.__setattr__(self, "period", period)
        object.__setattr__(self, "facility_id", facility_id)
        object.__setattr__(self, "accepted_grades", grades)
        object.__setattr__(self, "min_volume_m3", minimum)
        object.__setattr__(self, "max_volume_m3", maximum)
        object.__setattr__(self, "gate_cost_cad_m3", gate)
        object.__setattr__(self, "evidence", _identifier(self.evidence, "evidence"))
        object.__setattr__(self, "diagnostic", bool(self.diagnostic))
        object.__setattr__(self, "notes", str(self.notes).strip())

    def accepts(self, grade_id: str) -> bool:
        return _uppercase(grade_id, "grade_id") in self.accepted_grades

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "period": self.period,
            "facility_id": self.facility_id,
            "accepted_grades": ";".join(self.accepted_grades),
            "min_volume_m3": self.min_volume_m3,
            "max_volume_m3": self.max_volume_m3,
            "gate_cost_cad_m3": self.gate_cost_cad_m3,
            "diagnostic": self.diagnostic,
            "evidence": self.evidence,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class Assignment:
    """A positive volume assignment from one supply offer to one facility."""

    assignment_id: str
    period: str
    supply_id: str
    origin: str
    grade_id: str
    facility_id: str
    facility_name: str
    facility_class: str
    volume_m3: float
    moisture_wet_basis: float
    supply_cost_cad_m3: float
    transport_cost_cad_m3: float
    gate_cost_cad_m3: float
    processing_cost_cad_m3: float
    product_revenue_cad_m3: float
    transport_link_id: str
    evidence: str

    def __post_init__(self) -> None:
        for name in (
            "assignment_id",
            "period",
            "supply_id",
            "origin",
            "facility_id",
            "facility_name",
            "facility_class",
            "transport_link_id",
            "evidence",
        ):
            _identifier(getattr(self, name), name)
        _uppercase(self.grade_id, "grade_id")
        volume = _finite(self.volume_m3, "volume_m3", minimum=0.0)
        if volume <= 0.0:
            raise AssignmentError(
                f"assignment {self.assignment_id} volume must be positive"
            )
        for name in (
            "supply_cost_cad_m3",
            "transport_cost_cad_m3",
            "gate_cost_cad_m3",
            "processing_cost_cad_m3",
            "product_revenue_cad_m3",
        ):
            _finite(getattr(self, name), name)
        moisture = _finite(self.moisture_wet_basis, "moisture_wet_basis", minimum=0.0)
        if moisture >= 1.0:
            raise AssignmentError("moisture_wet_basis must be in [0, 1)")

    @property
    def net_contribution_cad_m3(self) -> float:
        return (
            self.product_revenue_cad_m3
            - self.supply_cost_cad_m3
            - self.transport_cost_cad_m3
            - self.gate_cost_cad_m3
            - self.processing_cost_cad_m3
        )

    @property
    def contribution_cad(self) -> float:
        return self.volume_m3 * self.net_contribution_cad_m3

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignment_id": self.assignment_id,
            "period": self.period,
            "supply_id": self.supply_id,
            "origin": self.origin,
            "grade_id": self.grade_id,
            "facility_id": self.facility_id,
            "facility_name": self.facility_name,
            "facility_class": self.facility_class,
            "volume_m3": self.volume_m3,
            "moisture_wet_basis": self.moisture_wet_basis,
            "supply_cost_cad_m3": self.supply_cost_cad_m3,
            "transport_cost_cad_m3": self.transport_cost_cad_m3,
            "gate_cost_cad_m3": self.gate_cost_cad_m3,
            "processing_cost_cad_m3": self.processing_cost_cad_m3,
            "product_revenue_cad_m3": self.product_revenue_cad_m3,
            "net_contribution_cad_m3": self.net_contribution_cad_m3,
            "contribution_cad": self.contribution_cad,
            "transport_link_id": self.transport_link_id,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class UnassignedSupply:
    """The unassigned part of a supply offer and why it was left over."""

    supply_id: str
    period: str
    grade_id: str
    origin: str
    volume_m3: float
    reason: str
    penalty_cost_cad_m3: float

    def __post_init__(self) -> None:
        _identifier(self.supply_id, "supply_id")
        _identifier(self.period, "period")
        _uppercase(self.grade_id, "grade_id")
        _identifier(self.origin, "origin")
        volume = _finite(self.volume_m3, "volume_m3", minimum=0.0)
        if volume <= 0.0:
            raise AssignmentError(
                f"unassigned supply {self.supply_id} must be positive"
            )
        if not str(self.reason).strip():
            raise AssignmentError("unassigned reason is required")
        _finite(self.penalty_cost_cad_m3, "penalty_cost_cad_m3", minimum=0.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supply_id": self.supply_id,
            "period": self.period,
            "grade_id": self.grade_id,
            "origin": self.origin,
            "volume_m3": self.volume_m3,
            "reason": self.reason,
            "penalty_cost_cad_m3": self.penalty_cost_cad_m3,
        }


_UNASSIGNED_REASONS = (
    "no_eligible_facility",
    "no_transport_link",
    "capacity_exhausted",
    "contract_maximum",
    "moisture_limit",
    "unassigned_penalty_exceeds_net_value",
)


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    """A solved Stage A allocation plus reporting and validation tables."""

    run_id: str
    scenario_id: str
    period: str
    status: str
    objective_value_cad: float
    assignments: tuple[Assignment, ...]
    unassigned: tuple[UnassignedSupply, ...]
    facility_utilization: tuple[dict[str, Any], ...]
    contract_compliance: tuple[dict[str, Any], ...]
    objective_decomposition: tuple[dict[str, Any], ...]
    solver_name: str
    solver_version: str
    inputs_summary: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _identifier(self.run_id, "run_id")
        _identifier(self.scenario_id, "scenario_id")
        _identifier(self.period, "period")
        _identifier(self.status, "status")
        _finite(self.objective_value_cad, "objective_value_cad")

    @property
    def assigned_volume_m3(self) -> float:
        return float(sum(item.volume_m3 for item in self.assignments))

    @property
    def unassigned_volume_m3(self) -> float:
        return float(sum(item.volume_m3 for item in self.unassigned))

    @property
    def total_supply_m3(self) -> float:
        return self.assigned_volume_m3 + self.unassigned_volume_m3

    def to_rows(self) -> list[dict[str, Any]]:
        rows = [item.to_dict() for item in self.assignments]
        rows.extend(item.to_dict() for item in self.unassigned)
        return rows


@dataclass(frozen=True, slots=True)
class AssignmentInputs:
    """A fully parsed Stage A problem.

    The constructor validates cross-table references once.  Downstream code can
    therefore assume that every referenced period, grade, facility, and link
    exists.
    """

    run_id: str
    scenario_id: str
    periods: tuple[str, ...]
    grades: tuple[FeedstockGrade, ...]
    supplies: tuple[SupplyOffer, ...]
    facilities: tuple[FacilityCapacity, ...]
    transport_links: tuple[TransportLink, ...]
    contracts: tuple[Contract, ...]
    unassigned_penalty_cad_m3: float
    moisture_limit_by_class: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    _grade_index: dict[str, FeedstockGrade] = field(
        init=False, repr=False, compare=False
    )
    _supply_index: dict[str, SupplyOffer] = field(init=False, repr=False, compare=False)
    _facility_index: dict[str, FacilityCapacity] = field(
        init=False, repr=False, compare=False
    )
    _link_index: dict[tuple[str, str, str, str], TransportLink] = field(
        init=False, repr=False, compare=False
    )
    _contract_index: dict[str, tuple[Contract, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        run_id = _identifier(self.run_id, "run_id")
        scenario_id = _identifier(self.scenario_id, "scenario_id")
        periods = tuple(_identifier(value, "period") for value in self.periods)
        if not periods:
            raise AssignmentError("at least one period is required")
        if len(set(periods)) != len(periods):
            raise AssignmentError("periods must be unique")
        period_set = set(periods)

        grades = tuple(self.grades)
        if not grades:
            raise AssignmentError("at least one feedstock grade is required")
        grade_index = {grade.grade_id: grade for grade in grades}
        if len(grade_index) != len(grades):
            raise AssignmentError("grade ids must be unique")

        supplies = tuple(self.supplies)
        if not supplies:
            raise AssignmentError("at least one supply offer is required")
        supply_index = {supply.offer_id: supply for supply in supplies}
        if len(supply_index) != len(supplies):
            raise AssignmentError("supply offer ids must be unique")
        for supply in supplies:
            if supply.period not in period_set:
                raise AssignmentError(
                    f"supply {supply.offer_id} period {supply.period!r} is undeclared"
                )
            if supply.grade_id not in grade_index:
                raise AssignmentError(
                    f"supply {supply.offer_id} references unknown grade {supply.grade_id}"
                )

        facilities = tuple(self.facilities)
        if not facilities:
            raise AssignmentError("at least one facility is required")
        facility_index = {facility.facility_id: facility for facility in facilities}
        if len(facility_index) != len(facilities):
            raise AssignmentError("facility ids must be unique")
        for facility in facilities:
            for offer in facility.offers:
                if offer.period not in period_set:
                    raise AssignmentError(
                        f"facility {facility.facility_id} offer {offer.offer_id} "
                        f"period {offer.period!r} is undeclared"
                    )
                if offer.grade_id not in grade_index:
                    raise AssignmentError(
                        f"facility {facility.facility_id} references unknown grade "
                        f"{offer.grade_id}"
                    )

        supply_origins = {supply.origin for supply in supplies}
        links = tuple(self.transport_links)
        link_index: dict[tuple[str, str, str, str], TransportLink] = {}
        for link in links:
            if link.period not in period_set:
                raise AssignmentError(
                    f"transport link {link.link_id} period {link.period!r} is undeclared"
                )
            if link.origin not in supply_origins:
                raise AssignmentError(
                    f"transport link {link.link_id} origin {link.origin} has no supply"
                )
            if link.destination not in facility_index:
                raise AssignmentError(
                    f"transport link {link.link_id} destination {link.destination} "
                    "is not a facility"
                )
            if link.grade_id and link.grade_id not in grade_index:
                raise AssignmentError(
                    f"transport link {link.link_id} references unknown grade {link.grade_id}"
                )
            key = (link.origin, link.destination, link.grade_id, link.period)
            if key in link_index:
                raise AssignmentError(f"duplicate transport link key {key}")
            link_index[key] = link

        contracts = tuple(self.contracts)
        contract_index: dict[str, list[Contract]] = {}
        contract_ids: set[str] = set()
        for contract in contracts:
            if contract.contract_id in contract_ids:
                raise AssignmentError(f"duplicate contract id {contract.contract_id}")
            contract_ids.add(contract.contract_id)
            if contract.period not in period_set:
                raise AssignmentError(
                    f"contract {contract.contract_id} period {contract.period!r} "
                    "is undeclared"
                )
            if contract.facility_id not in facility_index:
                raise AssignmentError(
                    f"contract {contract.contract_id} references unknown facility "
                    f"{contract.facility_id}"
                )
            for grade_id in contract.accepted_grades:
                if grade_id not in grade_index:
                    raise AssignmentError(
                        f"contract {contract.contract_id} references unknown grade "
                        f"{grade_id}"
                    )
            contract_index.setdefault(contract.facility_id, []).append(contract)

        penalty = _finite(
            self.unassigned_penalty_cad_m3, "unassigned_penalty_cad_m3", minimum=0.0
        )
        limits: dict[str, float] = {}
        for facility_class, limit in self.moisture_limit_by_class.items():
            normalized_class = _uppercase(facility_class, "facility_class")
            value = _finite(
                limit, f"moisture limit for {normalized_class}", minimum=0.0
            )
            if value >= 1.0:
                raise AssignmentError("moisture limits must be in [0, 1)")
            limits[normalized_class] = value

        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "scenario_id", scenario_id)
        object.__setattr__(self, "periods", periods)
        object.__setattr__(
            self, "grades", tuple(sorted(grades, key=lambda item: item.grade_id))
        )
        object.__setattr__(
            self, "supplies", tuple(sorted(supplies, key=lambda item: item.offer_id))
        )
        object.__setattr__(
            self,
            "facilities",
            tuple(sorted(facilities, key=lambda item: item.facility_id)),
        )
        object.__setattr__(
            self, "transport_links", tuple(sorted(links, key=lambda item: item.link_id))
        )
        object.__setattr__(
            self,
            "contracts",
            tuple(sorted(contracts, key=lambda item: item.contract_id)),
        )
        object.__setattr__(self, "unassigned_penalty_cad_m3", penalty)
        object.__setattr__(self, "moisture_limit_by_class", dict(limits))
        object.__setattr__(self, "metadata", dict(self.metadata))
        object.__setattr__(self, "_grade_index", grade_index)
        object.__setattr__(self, "_supply_index", supply_index)
        object.__setattr__(self, "_facility_index", facility_index)
        object.__setattr__(self, "_link_index", link_index)
        object.__setattr__(
            self,
            "_contract_index",
            {facility_id: tuple(rows) for facility_id, rows in contract_index.items()},
        )

    def grade(self, grade_id: str) -> FeedstockGrade:
        return self._grade_index[_uppercase(grade_id, "grade_id")]

    def supply(self, offer_id: str) -> SupplyOffer:
        return self._supply_index[_identifier(offer_id, "offer_id")]

    def facility(self, facility_id: str) -> FacilityCapacity:
        return self._facility_index[_identifier(facility_id, "facility_id")]

    def link(
        self, origin: str, destination: str, grade_id: str, period: str
    ) -> TransportLink | None:
        key = (
            _identifier(origin, "origin"),
            _identifier(destination, "destination"),
            _uppercase(grade_id, "grade_id"),
            _identifier(period, "period"),
        )
        return self._link_index.get(key)

    def contracts_for(self, facility_id: str, period: str) -> tuple[Contract, ...]:
        rows = self._contract_index.get(_identifier(facility_id, "facility_id"), ())
        period_key = _identifier(period, "period")
        return tuple(contract for contract in rows if contract.period == period_key)

    def moisture_limit(self, facility_class: str) -> float | None:
        return self.moisture_limit_by_class.get(
            _uppercase(facility_class, "facility_class")
        )

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario_id": self.scenario_id,
            "periods": list(self.periods),
            "grade_count": len(self.grades),
            "supply_offer_count": len(self.supplies),
            "supply_volume_m3": sum(item.volume_m3 for item in self.supplies),
            "facility_count": len(self.facilities),
            "facility_nameplate_m3_y": sum(
                item.nameplate_m3_y for item in self.facilities
            ),
            "transport_link_count": len(self.transport_links),
            "contract_count": len(self.contracts),
            "unassigned_penalty_cad_m3": self.unassigned_penalty_cad_m3,
            "moisture_limit_by_class": dict(self.moisture_limit_by_class),
        }


def to_stage_b_tables(
    inputs: AssignmentInputs,
    result: AssignmentResult,
) -> dict[str, list[dict[str, Any]]]:
    """Emit the Stage B table subset that Stage A can populate.

    The Stage B graph expects a scenario, commodity, supply_offer, network_node,
    technology, transport_link, and market tables.  A production Stage B run
    also needs demand and market rows; this adapter deliberately emits only the
    three Stage A-owned tables and never fabricates demand.
    """
    commodity_ids = {
        grade.grade_id: f"feedstock_{grade.grade_id.lower()}" for grade in inputs.grades
    }
    supply_rows = [
        {
            "scenario_id": inputs.scenario_id,
            "period": supply.period,
            "spatial_unit_id": supply.origin,
            "supply_node_id": supply.origin,
            "commodity_id": commodity_ids[supply.grade_id],
            "quantity": supply.volume_m3,
            "quantity_unit": "m3",
            "supply_cost": supply.roadside_cost_cad_m3,
            "supply_cost_unit": "CAD_per_m3",
        }
        for supply in inputs.supplies
    ]
    technology_rows = [
        {
            "technology_id": f"{facility.facility_id}__{offer.grade_id}",
            "facility_node_id": facility.facility_id,
            "feedstock_commodity_id": commodity_ids[offer.grade_id],
            "primary_product_id": offer.primary_product,
            "operating_cost": offer.processing_cost_cad_m3,
            "operating_cost_unit": "CAD_per_m3",
            "maximum_capacity": offer.capacity_m3_y,
        }
        for facility in inputs.facilities
        for offer in facility.offers
    ]
    # Stage B transport links are origin-destination-mode records, not
    # grade-specific records.  Stage A has different truck costs for logs and
    # residue, so collapse the grade links to one capacity-weighted row per
    # origin-destination-period; the Stage B schema must gain a grade/commodity
    # dimension before it can preserve the Stage A split.
    transport_weights: dict[tuple[str, str, str], dict[str, float]] = {}
    for link in inputs.transport_links:
        key = (link.origin, link.destination, link.period)
        facility = inputs.facility(link.destination)
        offer = facility.offer_for(link.grade_id)
        weight = offer.capacity_m3_y if offer is not None else facility.nameplate_m3_y
        bucket = transport_weights.setdefault(
            key,
            {"weight": 0.0, "weighted_cost": 0.0, "capacity": facility.nameplate_m3_y},
        )
        bucket["weight"] += weight
        bucket["weighted_cost"] += link.cost_cad_m3 * weight
    transport_rows = [
        {
            "scenario_id": inputs.scenario_id,
            "period": period,
            "origin_node_id": origin,
            "destination_node_id": destination,
            "transport_mode": "ROAD",
            "transport_cost": (
                bucket["weighted_cost"] / bucket["weight"]
                if bucket["weight"] > 0
                else 0.0
            ),
            "transport_cost_unit": "CAD_per_m3",
            "capacity": bucket["capacity"],
        }
        for (origin, destination, period), bucket in sorted(transport_weights.items())
    ]
    return {
        "supply_offer": supply_rows,
        "technology": technology_rows,
        "transport_link": transport_rows,
    }
