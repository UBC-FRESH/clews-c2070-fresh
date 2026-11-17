"""Validation for the Stage A facility-assignment pilot.

The validator recomputes every conserved quantity from the solved assignments
instead of trusting the report tables.  A failed check is a hard failure; the
pilot driver writes the report and exits non-zero.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from . import units
from .schema import (
    PATH_CONFIDENCE_VALUES,
    REJECTED_CAPACITY_UNITS,
    SUPPORTED_CAPACITY_UNITS,
    AssignmentError,
    AssignmentInputs,
    AssignmentResult,
)

__all__ = [
    "AssignmentValidationError",
    "ValidationCheck",
    "ValidationReport",
    "validate_assignment",
]

_TOLERANCE = 1e-6
_REASONS = {
    "no_eligible_facility",
    "no_transport_link",
    "capacity_exhausted",
    "contract_maximum",
    "moisture_limit",
    "unassigned_penalty_exceeds_net_value",
}


class AssignmentValidationError(AssignmentError):
    """Raised when a validation report contains a failed check."""


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    check_id: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ValidationReport:
    checks: tuple[ValidationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failed_checks(self) -> tuple[ValidationCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failed_check_count": len(self.failed_checks),
            "checks": [check.to_dict() for check in self.checks],
        }

    def raise_if_failed(self) -> None:
        if self.passed:
            return
        details = "; ".join(
            f"{check.check_id}: {check.detail}" for check in self.failed_checks
        )
        raise AssignmentValidationError(f"assignment validation failed: {details}")


def _close(left: float, right: float, tolerance: float = _TOLERANCE) -> bool:
    return abs(left - right) <= tolerance * max(1.0, abs(left), abs(right))


def _finite(value: object) -> bool:
    return math.isfinite(float(value))  # type: ignore[arg-type]


def _exceeds(value: float, limit: float, tolerance: float = _TOLERANCE) -> bool:
    """Allow solver-scale numerical slack on large capacities."""
    return value > limit + tolerance * max(1.0, abs(limit))


def _component_map(result: AssignmentResult) -> dict[str, float]:
    return {
        str(row["component"]): float(row["value_cad"])
        for row in result.objective_decomposition
        if "component" in row and "value_cad" in row
    }


def validate_assignment(
    inputs: AssignmentInputs,
    result: AssignmentResult,
    *,
    tolerance: float = _TOLERANCE,
) -> ValidationReport:
    """Recompute conservation, capacity, eligibility, contract, and objective checks."""
    checks: list[ValidationCheck] = []

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append(
            ValidationCheck(check_id=check_id, passed=bool(passed), detail=detail)
        )

    add("solver_status", result.status == "Optimal", f"status={result.status}")

    supply_by_id = {supply.offer_id: supply for supply in inputs.supplies}
    facility_by_id = {facility.facility_id: facility for facility in inputs.facilities}
    supplied: dict[str, float] = defaultdict(float)
    slack: dict[str, float] = defaultdict(float)
    for assignment in result.assignments:
        supplied[assignment.supply_id] += assignment.volume_m3
    for leftover in result.unassigned:
        slack[leftover.supply_id] += leftover.volume_m3

    balance_errors: list[str] = []
    for supply in inputs.supplies:
        assigned = supplied.get(supply.offer_id, 0.0)
        unassigned = slack.get(supply.offer_id, 0.0)
        if not _close(assigned + unassigned, supply.volume_m3, tolerance):
            balance_errors.append(
                f"{supply.offer_id}: assigned {assigned:.6f} + unassigned "
                f"{unassigned:.6f} != supply {supply.volume_m3:.6f}"
            )
        if assigned < -tolerance or unassigned < -tolerance:
            balance_errors.append(
                f"{supply.offer_id}: negative assigned or unassigned volume"
            )
    add(
        "volume_conservation",
        not balance_errors,
        "assigned + unassigned equals supply for every offer"
        if not balance_errors
        else "; ".join(balance_errors[:5]),
    )

    unknown_supplies = sorted((set(supplied) | set(slack)) - set(supply_by_id))
    add(
        "supply_reference_integrity",
        not unknown_supplies,
        "all assignment rows reference a declared supply offer"
        if not unknown_supplies
        else f"unknown supply ids: {unknown_supplies[:5]}",
    )

    eligibility_errors: list[str] = []
    capacity_errors: list[str] = []
    facility_assigned: dict[str, float] = defaultdict(float)
    grade_assigned: dict[tuple[str, str], float] = defaultdict(float)
    for item in result.assignments:
        supply_row = supply_by_id.get(item.supply_id)
        facility_row = facility_by_id.get(item.facility_id)
        if supply_row is None or facility_row is None:
            eligibility_errors.append(
                f"{item.assignment_id}: missing supply or facility"
            )
            continue
        facility = facility_row
        supply = supply_row
        offer = facility.offer_for(item.grade_id)
        if offer is None:
            eligibility_errors.append(
                f"{item.assignment_id}: grade {item.grade_id} is not eligible at "
                f"{facility.facility_id}"
            )
            continue
        facility_assigned[facility.facility_id] += item.volume_m3
        grade_assigned[(facility.facility_id, item.grade_id)] += item.volume_m3
        if _exceeds(item.volume_m3, offer.capacity_m3_y, tolerance):
            capacity_errors.append(
                f"{item.assignment_id}: grade capacity {offer.capacity_m3_y:.3f} "
                f"< assigned {item.volume_m3:.3f}"
            )
        if _exceeds(item.volume_m3, facility.nameplate_m3_y, tolerance):
            capacity_errors.append(
                f"{item.assignment_id}: facility nameplate {facility.nameplate_m3_y:.3f} "
                f"< assigned {item.volume_m3:.3f}"
            )
        # The assignment must carry the same cost basis as the declared link.
        link = inputs.link(
            supply.origin, facility.facility_id, item.grade_id, item.period
        )
        if link is None:
            eligibility_errors.append(
                f"{item.assignment_id}: transport link is missing"
            )
        elif not _close(item.transport_cost_cad_m3, link.cost_cad_m3, tolerance):
            eligibility_errors.append(
                f"{item.assignment_id}: transport cost {item.transport_cost_cad_m3} "
                f"!= link {link.cost_cad_m3}"
            )
    add(
        "grade_eligibility",
        not eligibility_errors,
        "every assignment has a declared eligible grade and transport link"
        if not eligibility_errors
        else "; ".join(eligibility_errors[:5]),
    )
    add(
        "capacity",
        not capacity_errors,
        "no assignment exceeds a facility or grade capacity"
        if not capacity_errors
        else "; ".join(capacity_errors[:5]),
    )

    nameplate_errors = [
        f"{facility.facility_id}: assigned {facility_assigned[facility.facility_id]:.3f} "
        f"> nameplate {facility.nameplate_m3_y:.3f}"
        for facility in inputs.facilities
        if _exceeds(
            facility_assigned[facility.facility_id], facility.nameplate_m3_y, tolerance
        )
    ]
    add(
        "facility_nameplate",
        not nameplate_errors,
        "aggregate facility assignments stay within nameplate"
        if not nameplate_errors
        else "; ".join(nameplate_errors[:5]),
    )

    contract_errors: list[str] = []
    for contract in inputs.contracts:
        assigned = sum(
            item.volume_m3
            for item in result.assignments
            if item.facility_id == contract.facility_id
            and item.period == contract.period
            and contract.accepts(item.grade_id)
        )
        if assigned < contract.min_volume_m3 - tolerance * max(
            1.0, abs(contract.min_volume_m3)
        ):
            contract_errors.append(
                f"{contract.contract_id}: assigned {assigned:.3f} < min "
                f"{contract.min_volume_m3:.3f}"
            )
        if _exceeds(assigned, contract.max_volume_m3, tolerance):
            contract_errors.append(
                f"{contract.contract_id}: assigned {assigned:.3f} > max "
                f"{contract.max_volume_m3:.3f}"
            )
    add(
        "contract_bounds",
        not contract_errors,
        "all contracts satisfy min/max bounds"
        if not contract_errors
        else "; ".join(contract_errors[:5]),
    )

    reason_errors = [
        f"{item.supply_id}: invalid reason {item.reason!r}"
        for item in result.unassigned
        if not item.reason or item.reason not in _REASONS
    ]
    add(
        "unassigned_reasons",
        not reason_errors,
        "every unassigned row has a documented reason"
        if not reason_errors
        else "; ".join(reason_errors[:5]),
    )

    moisture_errors: list[str] = []
    for facility in inputs.facilities:
        limit = inputs.moisture_limit(facility.facility_class)
        if limit is None:
            continue
        for period in inputs.periods:
            rows = [
                item
                for item in result.assignments
                if item.facility_id == facility.facility_id and item.period == period
            ]
            volume = sum(item.volume_m3 for item in rows)
            if volume <= tolerance:
                continue
            weighted = sum(item.volume_m3 * item.moisture_wet_basis for item in rows)
            average = weighted / volume
            if average > limit + tolerance:
                moisture_errors.append(
                    f"{facility.facility_id} {period}: moisture {average:.4f} > limit {limit:.4f}"
                )
    add(
        "moisture_blend",
        not moisture_errors,
        "facility moisture blends respect declared limits"
        if not moisture_errors
        else "; ".join(moisture_errors[:5]),
    )

    unit_errors: list[str] = []
    for grade in inputs.grades:
        try:
            canonical = units.canonical_unit(grade.unit)
        except units.UnitError as error:
            unit_errors.append(
                f"grade {grade.grade_id} has unsupported unit {grade.unit}: {error}"
            )
            continue
        if canonical not in {"m3_green", "odt"}:
            unit_errors.append(
                f"grade {grade.grade_id} canonical unit {canonical} is not a "
                "Stage A model basis"
            )
    for facility in inputs.facilities:
        if facility.facility_class not in {"SAW", "PAP", "PEL", "BIO"}:
            unit_errors.append(f"facility {facility.facility_id} has unsupported class")
        for offer in facility.offers:
            tokens = {
                token.strip()
                for token in offer.capacity_basis.replace(";", "|").split("|")
                if token.strip()
            }
            if not tokens:
                unit_errors.append(f"{offer.offer_id}: empty capacity basis")
            for token in tokens:
                if token in SUPPORTED_CAPACITY_UNITS:
                    continue
                if token == "archive_m3_y":
                    continue
                if token.startswith("units_") and "_m3_" in token:
                    continue
                if any(rejected in token for rejected in REJECTED_CAPACITY_UNITS):
                    unit_errors.append(
                        f"{offer.offer_id}: rejected capacity basis token {token!r}"
                    )
                else:
                    unit_errors.append(
                        f"{offer.offer_id}: undocumented capacity basis token {token!r}"
                    )
    add(
        "unit_consistency",
        not unit_errors,
        "all quantities use declared m3/odt conversions and every capacity basis is documented"
        if not unit_errors
        else "; ".join(unit_errors[:5]),
    )

    transport_errors: list[str] = []
    for link in inputs.transport_links:
        if link.path_confidence not in PATH_CONFIDENCE_VALUES:
            transport_errors.append(
                f"{link.link_id}: unknown path confidence {link.path_confidence!r}"
            )
        if link.capacity_m3 is None and link.capacity_source != "absent":
            transport_errors.append(
                f"{link.link_id}: capacity is absent but capacity_source is "
                f"{link.capacity_source!r}"
            )
        if link.capacity_m3 is not None and link.capacity_source == "absent":
            transport_errors.append(
                f"{link.link_id}: capacity exists but capacity_source is absent"
            )
        if not link.distance_basis:
            transport_errors.append(f"{link.link_id}: distance basis is empty")
    add(
        "transport_provenance",
        not transport_errors,
        "every transport link declares path confidence, capacity source, and distance basis"
        if not transport_errors
        else "; ".join(transport_errors[:5]),
    )

    allowed_diagnostic_sources = {
        "diagnostic_absent",
        "bc_fibre_db_2018_diagnostic",
        "apportionment_derived_diagnostic",
    }
    contract_source_errors = [
        f"{contract.contract_id}: diagnostic contract has contract_source "
        f"{contract.contract_source!r}"
        for contract in inputs.contracts
        if contract.diagnostic
        and contract.contract_source not in allowed_diagnostic_sources
    ]
    add(
        "contract_source",
        not contract_source_errors,
        "diagnostic contracts declare a known diagnostic source"
        if not contract_source_errors
        else "; ".join(contract_source_errors[:5]),
    )

    nonfinite = [
        item.assignment_id
        for item in result.assignments
        if not all(
            _finite(value)
            for value in (
                item.volume_m3,
                item.supply_cost_cad_m3,
                item.transport_cost_cad_m3,
                item.gate_cost_cad_m3,
                item.processing_cost_cad_m3,
                item.product_revenue_cad_m3,
            )
        )
    ]
    nonfinite.extend(
        item.supply_id for item in result.unassigned if not _finite(item.volume_m3)
    )
    add(
        "finite_values",
        not nonfinite,
        "all assignment and slack values are finite"
        if not nonfinite
        else f"non-finite rows: {nonfinite[:5]}",
    )

    revenue = sum(
        item.volume_m3 * item.product_revenue_cad_m3 for item in result.assignments
    )
    supply_cost = sum(
        item.volume_m3 * item.supply_cost_cad_m3 for item in result.assignments
    )
    transport_cost = sum(
        item.volume_m3 * item.transport_cost_cad_m3 for item in result.assignments
    )
    gate_cost = sum(
        item.volume_m3 * item.gate_cost_cad_m3 for item in result.assignments
    )
    processing_cost = sum(
        item.volume_m3 * item.processing_cost_cad_m3 for item in result.assignments
    )
    penalty = sum(
        item.volume_m3 * item.penalty_cost_cad_m3 for item in result.unassigned
    )
    expected = {
        "product_revenue": revenue,
        "supply_cost": -supply_cost,
        "transport_cost": -transport_cost,
        "gate_cost": -gate_cost,
        "processing_cost": -processing_cost,
        "unassigned_penalty": -penalty,
        "net_objective": revenue
        - supply_cost
        - transport_cost
        - gate_cost
        - processing_cost
        - penalty,
    }
    actual = _component_map(result)
    component_errors = [
        f"{name}: expected {value:.6f}, reported {actual.get(name, float('nan')):.6f}"
        for name, value in expected.items()
        if name not in actual or not _close(value, actual[name], tolerance)
    ]
    if not _close(result.objective_value_cad, expected["net_objective"], tolerance):
        component_errors.append(
            f"solver objective {result.objective_value_cad:.6f} != recomputed "
            f"{expected['net_objective']:.6f}"
        )
    add(
        "objective_reconciliation",
        not component_errors,
        "objective decomposition reconciles to the solver objective"
        if not component_errors
        else "; ".join(component_errors[:5]),
    )

    facility_report = {row["facility_id"]: row for row in result.facility_utilization}
    utilization_errors: list[str] = []
    for facility in inputs.facilities:
        row = facility_report.get(facility.facility_id)
        if row is None:
            utilization_errors.append(
                f"{facility.facility_id}: missing utilization row"
            )
            continue
        assigned = facility_assigned[facility.facility_id]
        if not _close(float(row["assigned_m3"]), assigned, tolerance):
            utilization_errors.append(
                f"{facility.facility_id}: utilization assigned "
                f"{row['assigned_m3']} != {assigned}"
            )
        reported_binding = bool(row["capacity_binding"])
        computed_binding = assigned >= facility.nameplate_m3_y - tolerance * max(
            1.0, abs(facility.nameplate_m3_y)
        )
        if reported_binding != computed_binding:
            utilization_errors.append(
                f"{facility.facility_id}: binding flag {reported_binding} != {computed_binding}"
            )
    add(
        "utilization_reconciliation",
        not utilization_errors,
        "facility utilization rows reconcile to assignments"
        if not utilization_errors
        else "; ".join(utilization_errors[:5]),
    )

    contract_report = {row["contract_id"]: row for row in result.contract_compliance}
    contract_report_errors = [
        f"{contract.contract_id}: missing compliance row"
        for contract in inputs.contracts
        if contract.contract_id not in contract_report
    ]
    add(
        "contract_report_completeness",
        not contract_report_errors,
        "every contract has a compliance row"
        if not contract_report_errors
        else "; ".join(contract_report_errors[:5]),
    )

    return ValidationReport(checks=tuple(checks))
