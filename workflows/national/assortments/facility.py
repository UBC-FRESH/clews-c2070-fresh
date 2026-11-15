"""Facility-assignment interface for assortment supply.

This module defines the contract between an assortment matrix and a facility
network.  It intentionally implements only a deterministic eligibility and
capacity screen, not the full mixed-integer linear program.

Mapping to the CLEWS five-layer LP / FHOPS
-----------------------------------------
* feedstock grades are commodities;
* each origin (stand / supply area) and facility is a node;
* an ``arc`` carries volume, delivered cost, moisture, and evidence;
* contract minima, maxima, and grade eligibility are constraints;
* stand-level harvest scheduling and transport routing stay in FHOPS/CLEWS.

The screen below is a contract test and a data-shape adapter.  A future MILP
must consume the same records and may not silently relax a contract bound.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

__all__ = [
    "FacilityAssignment",
    "FacilityAssignmentBundle",
    "FacilityContract",
    "FacilityError",
    "FeedstockGrade",
    "FeedstockOffer",
    "assign_feedstock",
    "to_clews_lp_contract",
]


class FacilityError(ValueError):
    """Raised when a facility contract or offer is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class FeedstockGrade:
    """A buyer-facing feedstock grade derived from assortment rows."""

    grade_id: str
    product: str
    accepted_sorts: tuple[str, ...]
    min_top_cm: float
    max_residue_share: float = 1.0
    description: str = ""

    def __post_init__(self) -> None:
        grade = str(self.grade_id).strip().upper()
        if not grade:
            raise FacilityError("grade_id is required")
        object.__setattr__(self, "grade_id", grade)
        object.__setattr__(self, "product", str(self.product).strip().lower())
        object.__setattr__(
            self,
            "accepted_sorts",
            tuple(str(value).strip().upper() for value in self.accepted_sorts),
        )
        if not self.accepted_sorts:
            raise FacilityError(f"grade {grade} needs at least one accepted sort")
        for name in ("min_top_cm", "max_residue_share"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise FacilityError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, value)
        if self.max_residue_share > 1.0:
            raise FacilityError("max_residue_share cannot exceed 1.0")

    def accepts_sort(self, sort_code: str, top_cm: float) -> bool:
        return (
            str(sort_code).strip().upper() in self.accepted_sorts
            and float(top_cm) >= self.min_top_cm
        )


@dataclass(frozen=True, slots=True)
class FeedstockOffer:
    """One assortment stream offered to the facility network."""

    offer_id: str
    grade: str
    product: str
    volume_m3: float
    moisture_wet_basis: float
    delivered_cost_cad_m3: float
    origin: str
    evidence: str = "assumption"

    def __post_init__(self) -> None:
        for name in ("offer_id", "grade", "product", "origin"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise FacilityError(f"{name} is required")
            object.__setattr__(self, name, value.upper() if name == "grade" else value)
        volume = float(self.volume_m3)
        if not math.isfinite(volume) or volume < 0.0:
            raise FacilityError("offer volume must be finite and non-negative")
        object.__setattr__(self, "volume_m3", volume)
        moisture = float(self.moisture_wet_basis)
        if not math.isfinite(moisture) or not 0.0 <= moisture < 1.0:
            raise FacilityError("moisture_wet_basis must be in [0, 1)")
        object.__setattr__(self, "moisture_wet_basis", moisture)
        cost = float(self.delivered_cost_cad_m3)
        if not math.isfinite(cost):
            raise FacilityError("delivered cost must be finite")
        object.__setattr__(self, "delivered_cost_cad_m3", cost)


@dataclass(frozen=True, slots=True)
class FacilityContract:
    """A buyer contract with grade, volume, cost, and duration bounds."""

    facility_id: str
    name: str
    accepted_grades: tuple[str, ...]
    min_volume_m3: float
    max_volume_m3: float
    min_contract_years: int
    max_contract_years: int
    gate_cost_cad_m3: float
    destination: str
    notes: str = ""
    evidence: str = "assumption"

    def __post_init__(self) -> None:
        facility_id = str(self.facility_id).strip()
        if not facility_id:
            raise FacilityError("facility_id is required")
        object.__setattr__(self, "facility_id", facility_id)
        object.__setattr__(self, "name", str(self.name).strip() or facility_id)
        grades = tuple(str(value).strip().upper() for value in self.accepted_grades)
        if not grades:
            raise FacilityError(f"{facility_id} needs at least one accepted grade")
        object.__setattr__(self, "accepted_grades", grades)
        minimum = float(self.min_volume_m3)
        maximum = float(self.max_volume_m3)
        if not math.isfinite(minimum) or not math.isfinite(maximum):
            raise FacilityError("contract volumes must be finite")
        if minimum < 0.0 or maximum <= 0.0 or minimum > maximum:
            raise FacilityError("contract volumes must satisfy 0 <= min <= max")
        object.__setattr__(self, "min_volume_m3", minimum)
        object.__setattr__(self, "max_volume_m3", maximum)
        if int(self.min_contract_years) < 0:
            raise FacilityError("min_contract_years cannot be negative")
        if int(self.max_contract_years) < int(self.min_contract_years):
            raise FacilityError("max_contract_years cannot be less than min_contract_years")
        object.__setattr__(self, "min_contract_years", int(self.min_contract_years))
        object.__setattr__(self, "max_contract_years", int(self.max_contract_years))
        cost = float(self.gate_cost_cad_m3)
        if not math.isfinite(cost):
            raise FacilityError("gate cost must be finite")
        object.__setattr__(self, "gate_cost_cad_m3", cost)
        object.__setattr__(self, "destination", str(self.destination).strip())

    def accepts(self, offer: FeedstockOffer) -> bool:
        return offer.grade in self.accepted_grades

    @property
    def lifetime_volume_m3(self) -> float:
        """Maximum contract volume over its declared maximum duration."""
        return float(self.max_volume_m3) * float(self.max_contract_years)


@dataclass(frozen=True, slots=True)
class FacilityAssignment:
    facility_id: str
    offer_id: str
    grade: str
    volume_m3: float
    delivered_cost_cad_m3: float
    feasible: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "facility_id": self.facility_id,
            "offer_id": self.offer_id,
            "grade": self.grade,
            "volume_m3": self.volume_m3,
            "delivered_cost_cad_m3": self.delivered_cost_cad_m3,
            "feasible": self.feasible,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class FacilityAssignmentBundle:
    assignments: tuple[FacilityAssignment, ...]
    unassigned: tuple[FacilityAssignment, ...]
    contract_totals: tuple[dict[str, Any], ...]

    @property
    def assigned_volume_m3(self) -> float:
        return float(sum(item.volume_m3 for item in self.assignments if item.feasible))

    def to_rows(self) -> list[dict[str, Any]]:
        rows = [item.to_dict() for item in self.assignments]
        rows.extend(item.to_dict() for item in self.unassigned)
        return rows


def assign_feedstock(
    offers: Sequence[FeedstockOffer],
    contracts: Sequence[FacilityContract],
) -> FacilityAssignmentBundle:
    """Greedy grade/capacity screen; explicitly not the full MILP.

    Offers are considered in descending volume and then offer id.  Each offer
    goes to the eligible contract with the lowest delivered cost that still has
    nameplate capacity.  Partial allocation is not performed: a contract either
    takes the whole offer or records why it cannot.  This keeps the prototype
    conservative and auditable.
    """
    if not contracts:
        raise FacilityError("at least one facility contract is required")
    remaining = {contract.facility_id: contract.max_volume_m3 for contract in contracts}
    assignments: list[FacilityAssignment] = []
    unassigned: list[FacilityAssignment] = []
    totals: dict[str, float] = {contract.facility_id: 0.0 for contract in contracts}

    for offer in sorted(offers, key=lambda item: (-item.volume_m3, item.offer_id)):
        candidates = [contract for contract in contracts if contract.accepts(offer)]
        if not candidates:
            unassigned.append(
                FacilityAssignment(
                    facility_id="",
                    offer_id=offer.offer_id,
                    grade=offer.grade,
                    volume_m3=offer.volume_m3,
                    delivered_cost_cad_m3=offer.delivered_cost_cad_m3,
                    feasible=False,
                    reason="no contract accepts this grade",
                )
            )
            continue
        candidates.sort(
            key=lambda contract: (
                offer.delivered_cost_cad_m3 + contract.gate_cost_cad_m3,
                contract.facility_id,
            )
        )
        chosen = next(
            (contract for contract in candidates if remaining[contract.facility_id] >= offer.volume_m3),
            None,
        )
        if chosen is None:
            best = candidates[0]
            unassigned.append(
                FacilityAssignment(
                    facility_id=best.facility_id,
                    offer_id=offer.offer_id,
                    grade=offer.grade,
                    volume_m3=offer.volume_m3,
                    delivered_cost_cad_m3=offer.delivered_cost_cad_m3,
                    feasible=False,
                    reason="remaining contract capacity is below the offer volume",
                )
            )
            continue
        remaining[chosen.facility_id] -= offer.volume_m3
        totals[chosen.facility_id] += offer.volume_m3
        assignments.append(
            FacilityAssignment(
                facility_id=chosen.facility_id,
                offer_id=offer.offer_id,
                grade=offer.grade,
                volume_m3=offer.volume_m3,
                delivered_cost_cad_m3=offer.delivered_cost_cad_m3
                + chosen.gate_cost_cad_m3,
                feasible=True,
                reason="eligible grade and available nameplate capacity",
            )
        )

    contract_totals = []
    for contract in contracts:
        assigned = totals[contract.facility_id]
        contract_totals.append(
            {
                "facility_id": contract.facility_id,
                "name": contract.name,
                "assigned_volume_m3": assigned,
                "min_volume_m3": contract.min_volume_m3,
                "max_volume_m3": contract.max_volume_m3,
                "remaining_volume_m3": remaining[contract.facility_id],
                "above_minimum": assigned >= contract.min_volume_m3,
                "within_maximum": assigned <= contract.max_volume_m3,
                "destination": contract.destination,
            }
        )
    return FacilityAssignmentBundle(
        assignments=tuple(assignments),
        unassigned=tuple(unassigned),
        contract_totals=tuple(contract_totals),
    )


def to_clews_lp_contract(
    offers: Sequence[FeedstockOffer],
    contracts: Sequence[FacilityContract],
    assignments: FacilityAssignmentBundle,
) -> dict[str, list[dict[str, Any]]]:
    """Return the LP-facing rows implied by the screen.

    The output is deliberately generic.  It is a data contract for a future
    CLEWS/FHOPS MILP, not a solved network.
    """
    nodes = [
        {"node_id": offer.origin, "node_type": "origin", "label": offer.origin}
        for offer in offers
    ]
    nodes.extend(
        {
            "node_id": contract.facility_id,
            "node_type": "facility",
            "label": contract.name,
            "destination": contract.destination,
        }
        for contract in contracts
    )
    arcs = [
        {
            "arc_id": f"{item.offer_id}->{item.facility_id}",
            "origin": next(
                (offer.origin for offer in offers if offer.offer_id == item.offer_id),
                item.offer_id,
            ),
            "destination": item.facility_id,
            "grade": item.grade,
            "volume_m3": item.volume_m3,
            "delivered_cost_cad_m3": item.delivered_cost_cad_m3,
            "feasible": item.feasible,
            "reason": item.reason,
        }
        for item in assignments.assignments
    ]
    constraints: list[dict[str, Any]] = []
    for contract in contracts:
        constraints.append(
            {
                "constraint_id": f"{contract.facility_id}:min_volume",
                "facility_id": contract.facility_id,
                "kind": "min_volume",
                "value": contract.min_volume_m3,
                "unit": "m3",
            }
        )
        constraints.append(
            {
                "constraint_id": f"{contract.facility_id}:max_volume",
                "facility_id": contract.facility_id,
                "kind": "max_volume",
                "value": contract.max_volume_m3,
                "unit": "m3",
            }
        )
        constraints.append(
            {
                "constraint_id": f"{contract.facility_id}:grade_eligibility",
                "facility_id": contract.facility_id,
                "kind": "allowed_grades",
                "value": ";".join(contract.accepted_grades),
                "unit": "grade",
            }
        )
        constraints.append(
            {
                "constraint_id": f"{contract.facility_id}:duration",
                "facility_id": contract.facility_id,
                "kind": "contract_years",
                "value": f"{contract.min_contract_years}-{contract.max_contract_years}",
                "unit": "years",
            }
        )
    return {
        "nodes": nodes,
        "arcs": arcs,
        "constraints": constraints,
        "contract_totals": list(assignments.contract_totals),
    }
