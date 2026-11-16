"""PuLP transportation/allocation model for the Stage A facility pilot.

Formulation
-----------
Sets
  S  supply offers (one grade, origin, period, volume, roadside cost)
  F  facilities with grade-specific alternative capacities
  G  feedstock grades
  T  periods
  L  transport links (origin, facility, grade, period)

Variables
  x[s,f,g,t] >= 0  volume assigned from supply s to facility f
  u[s,g,t] >= 0    unassigned supply slack

The pilot has no binaries.  ``y[f,l,t]`` line-activity variables are not needed
because the fixture aggregates each facility's alternative feedstock lines into
one grade-specific capacity; the facility nameplate remains the total cap.

Objective (maximise)
  sum (product revenue - roadside supply - transport - gate - processing) * x
  - unassigned penalty * u

Constraints
  supply balance, facility nameplate, grade capacity, contract min/max,
  optional moisture blend, optional transport-link capacity.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

try:
    import pulp
except ImportError as error:  # pragma: no cover - dependency guard
    raise RuntimeError(
        "PuLP is required for the Stage A assignment model. Install "
        "requirements-prototype.txt in the repository virtual environment."
    ) from error

from .schema import (
    Assignment,
    AssignmentError,
    AssignmentInputs,
    AssignmentResult,
    Contract,
    FacilityCapacity,
    FacilityOffer,
    SupplyOffer,
    TransportLink,
    UnassignedSupply,
)

__all__ = ["AssignmentSolveError", "solve_assignment"]

_TOLERANCE = 1e-6


class AssignmentSolveError(AssignmentError):
    """Raised when the LP is infeasible or does not return an optimal solution."""


def _clean(value: object) -> str:
    return "".join(char if char.isalnum() else "_" for char in str(value))


def _value(variable: pulp.LpVariable) -> float:
    value = variable.varValue
    return 0.0 if value is None else float(value)


def _contract_for(
    inputs: AssignmentInputs,
    facility: FacilityCapacity,
    grade_id: str,
    period: str,
) -> Contract | None:
    contracts = inputs.contracts_for(facility.facility_id, period)
    for contract in contracts:
        if contract.accepts(grade_id):
            return contract
    return None


def _at_capacity(
    assigned: float, capacity: float, tolerance: float = _TOLERANCE
) -> bool:
    """Capacity binding test with solver-scale relative slack."""
    return assigned >= capacity - tolerance * max(1.0, abs(capacity))


def _build_variables(
    inputs: AssignmentInputs,
) -> tuple[
    pulp.LpProblem,
    dict[tuple[str, str, str, str], pulp.LpVariable],
    dict[tuple[str, str, str], pulp.LpVariable],
    dict[
        tuple[str, str, str, str],
        tuple[SupplyOffer, FacilityCapacity, FacilityOffer, TransportLink],
    ],
]:
    problem = pulp.LpProblem("stage_a_facility_assignment", pulp.LpMaximize)
    x: dict[tuple[str, str, str, str], pulp.LpVariable] = {}
    u: dict[tuple[str, str, str], pulp.LpVariable] = {}
    lookup: dict[
        tuple[str, str, str, str],
        tuple[SupplyOffer, FacilityCapacity, FacilityOffer, TransportLink],
    ] = {}

    for supply in inputs.supplies:
        for facility in inputs.facilities:
            offer = facility.offer_for(supply.grade_id)
            if offer is None:
                continue
            link = inputs.link(
                supply.origin, facility.facility_id, supply.grade_id, supply.period
            )
            if link is None:
                continue
            x_key = (
                supply.offer_id,
                facility.facility_id,
                supply.grade_id,
                supply.period,
            )
            variable_name = "x__" + _clean("__".join(x_key))
            x[x_key] = pulp.LpVariable(variable_name, lowBound=0.0)
            lookup[x_key] = (supply, facility, offer, link)

    for supply in inputs.supplies:
        u_key = (supply.offer_id, supply.grade_id, supply.period)
        variable_name = "u__" + _clean("__".join(u_key))
        u[u_key] = pulp.LpVariable(variable_name, lowBound=0.0)

    return problem, x, u, lookup


def _add_constraints(
    problem: pulp.LpProblem,
    inputs: AssignmentInputs,
    x: Mapping[tuple[str, str, str, str], pulp.LpVariable],
    u: Mapping[tuple[str, str, str], pulp.LpVariable],
    lookup: Mapping[
        tuple[str, str, str, str],
        tuple[SupplyOffer, FacilityCapacity, FacilityOffer, TransportLink],
    ],
) -> None:
    supply_terms: dict[tuple[str, str, str], list[pulp.LpVariable]] = defaultdict(list)
    facility_terms: dict[tuple[str, str], list[pulp.LpVariable]] = defaultdict(list)
    grade_terms: dict[tuple[str, str, str], list[pulp.LpVariable]] = defaultdict(list)
    contract_terms: dict[str, list[pulp.LpVariable]] = defaultdict(list)
    moisture_terms: dict[tuple[str, str], list[tuple[float, pulp.LpVariable]]] = (
        defaultdict(list)
    )

    for x_key, variable in x.items():
        supply, facility, offer, _link = lookup[x_key]
        supply_key = (supply.offer_id, supply.grade_id, supply.period)
        facility_key = (facility.facility_id, supply.period)
        grade_key = (facility.facility_id, supply.grade_id, supply.period)
        supply_terms[supply_key].append(variable)
        facility_terms[facility_key].append(variable)
        grade_terms[grade_key].append(variable)
        moisture_terms[facility_key].append((supply.moisture_wet_basis, variable))
        contract = _contract_for(inputs, facility, supply.grade_id, supply.period)
        if contract is not None:
            contract_terms[contract.contract_id].append(variable)

    for supply in inputs.supplies:
        balance_key = (supply.offer_id, supply.grade_id, supply.period)
        problem += (
            pulp.lpSum(supply_terms.get(balance_key, [])) + u[balance_key]
            == supply.volume_m3,
            "supply_balance__" + _clean("__".join(balance_key)),
        )

    for facility in inputs.facilities:
        for period in inputs.periods:
            terms = facility_terms.get((facility.facility_id, period), [])
            problem += (
                pulp.lpSum(terms) <= facility.nameplate_m3_y,
                "facility_capacity__" + _clean(f"{facility.facility_id}__{period}"),
            )
            limit = inputs.moisture_limit(facility.facility_class)
            if limit is not None:
                weighted = moisture_terms.get((facility.facility_id, period), [])
                problem += (
                    pulp.lpSum(value * variable for value, variable in weighted)
                    <= limit * pulp.lpSum(variable for _value, variable in weighted),
                    "moisture_blend__" + _clean(f"{facility.facility_id}__{period}"),
                )

    for facility in inputs.facilities:
        for offer in facility.offers:
            for period in inputs.periods:
                terms = grade_terms.get(
                    (facility.facility_id, offer.grade_id, period), []
                )
                problem += (
                    pulp.lpSum(terms) <= offer.capacity_m3_y,
                    "grade_capacity__"
                    + _clean(f"{facility.facility_id}__{offer.grade_id}__{period}"),
                )

    for contract in inputs.contracts:
        terms = contract_terms.get(contract.contract_id, [])
        problem += (
            pulp.lpSum(terms) >= contract.min_volume_m3,
            "contract_min__" + _clean(contract.contract_id),
        )
        problem += (
            pulp.lpSum(terms) <= contract.max_volume_m3,
            "contract_max__" + _clean(contract.contract_id),
        )

    for link in inputs.transport_links:
        if link.capacity_m3 is None:
            continue
        for key, variable in x.items():
            supply, facility, offer, candidate = lookup[key]
            if candidate.link_id != link.link_id:
                continue
            if (
                supply.origin != link.origin
                or facility.facility_id != link.destination
                or offer.grade_id != link.grade_id
                or supply.period != link.period
            ):
                continue
            problem += (
                variable <= link.capacity_m3,
                "transport_capacity__" + _clean("__".join(key)),
            )


def _add_objective(
    problem: pulp.LpProblem,
    inputs: AssignmentInputs,
    x: Mapping[tuple[str, str, str, str], pulp.LpVariable],
    u: Mapping[tuple[str, str, str], pulp.LpVariable],
    lookup: Mapping[
        tuple[str, str, str, str],
        tuple[SupplyOffer, FacilityCapacity, FacilityOffer, TransportLink],
    ],
) -> None:
    terms: list[pulp.LpAffineExpression] = []
    for key, variable in x.items():
        supply, facility, offer, link = lookup[key]
        contract = _contract_for(inputs, facility, supply.grade_id, supply.period)
        gate = contract.gate_cost_cad_m3 if contract is not None else 0.0
        margin = (
            offer.product_revenue_cad_m3
            - supply.roadside_cost_cad_m3
            - link.cost_cad_m3
            - gate
            - offer.processing_cost_cad_m3
        )
        if not math.isfinite(margin):
            raise AssignmentSolveError(f"non-finite objective margin for {key}")
        terms.append(margin * variable)
    terms.append(-inputs.unassigned_penalty_cad_m3 * pulp.lpSum(u.values()))
    problem += pulp.lpSum(terms), "net_value"


def _unassigned_reason(
    inputs: AssignmentInputs,
    supply: SupplyOffer,
    assigned_by_facility: Mapping[str, float],
) -> str:
    eligible = [
        facility
        for facility in inputs.facilities
        if facility.offer_for(supply.grade_id) is not None
    ]
    if not eligible:
        return "no_eligible_facility"
    linked = [
        facility
        for facility in eligible
        if inputs.link(
            supply.origin, facility.facility_id, supply.grade_id, supply.period
        )
        is not None
    ]
    if not linked:
        return "no_transport_link"
    moisture_limited: list[bool] = []
    for facility in linked:
        limit = inputs.moisture_limit(facility.facility_class)
        moisture_limited.append(limit is not None and supply.moisture_wet_basis > limit)
    if all(moisture_limited):
        return "moisture_limit"
    if all(
        _at_capacity(
            assigned_by_facility.get(facility.facility_id, 0.0), facility.nameplate_m3_y
        )
        for facility in linked
    ):
        return "capacity_exhausted"
    contract_limited = []
    for facility in linked:
        contracts = [
            contract
            for contract in inputs.contracts_for(facility.facility_id, supply.period)
            if contract.accepts(supply.grade_id)
        ]
        if not contracts:
            continue
        assigned = assigned_by_facility.get(facility.facility_id, 0.0)
        contract_limited.append(
            all(
                _at_capacity(assigned, contract.max_volume_m3) for contract in contracts
            )
        )
    if contract_limited and all(contract_limited):
        return "contract_maximum"
    for facility in linked:
        offer = facility.offer_for(supply.grade_id)
        if offer is None:
            continue
        if _at_capacity(
            assigned_by_facility.get(facility.facility_id, 0.0), offer.capacity_m3_y
        ):
            continue
        return "unassigned_penalty_exceeds_net_value"
    return "capacity_exhausted"


def solve_assignment(
    inputs: AssignmentInputs,
    *,
    solver: pulp.LpSolver | None = None,
    msg: bool = False,
) -> AssignmentResult:
    """Build and solve the Stage A LP, then return a reporting-ready result."""
    problem, x, u, lookup = _build_variables(inputs)
    if not x:
        raise AssignmentSolveError(
            "no eligible supply-facility-transport combination exists; "
            "check grade eligibility and transport links"
        )
    _add_constraints(problem, inputs, x, u, lookup)
    _add_objective(problem, inputs, x, u, lookup)

    active_solver = solver or pulp.PULP_CBC_CMD(msg=msg)
    status_code = problem.solve(active_solver)
    status = pulp.LpStatus.get(status_code, "Unknown")
    if status != "Optimal":
        raise AssignmentSolveError(f"LP did not solve optimally: status={status}")

    assignments: list[Assignment] = []
    assigned_by_facility: dict[str, float] = defaultdict(float)
    assigned_by_facility_grade: dict[tuple[str, str], float] = defaultdict(float)
    for key, variable in x.items():
        volume = _value(variable)
        if volume <= _TOLERANCE:
            continue
        supply, facility, offer, link = lookup[key]
        contract = _contract_for(inputs, facility, supply.grade_id, supply.period)
        gate = contract.gate_cost_cad_m3 if contract is not None else 0.0
        assignment = Assignment(
            assignment_id=f"{supply.offer_id}->{facility.facility_id}",
            period=supply.period,
            supply_id=supply.offer_id,
            origin=supply.origin,
            grade_id=supply.grade_id,
            facility_id=facility.facility_id,
            facility_name=facility.facility_name,
            facility_class=facility.facility_class,
            volume_m3=volume,
            moisture_wet_basis=supply.moisture_wet_basis,
            supply_cost_cad_m3=supply.roadside_cost_cad_m3,
            transport_cost_cad_m3=link.cost_cad_m3,
            gate_cost_cad_m3=gate,
            processing_cost_cad_m3=offer.processing_cost_cad_m3,
            product_revenue_cad_m3=offer.product_revenue_cad_m3,
            transport_link_id=link.link_id,
            evidence="lp_optimal;diagnostic_fixture",
        )
        assignments.append(assignment)
        assigned_by_facility[facility.facility_id] += volume
        assigned_by_facility_grade[(facility.facility_id, supply.grade_id)] += volume

    unassigned: list[UnassignedSupply] = []
    for supply in inputs.supplies:
        slack = _value(u[(supply.offer_id, supply.grade_id, supply.period)])
        if slack <= _TOLERANCE:
            continue
        reason = _unassigned_reason(inputs, supply, assigned_by_facility)
        unassigned.append(
            UnassignedSupply(
                supply_id=supply.offer_id,
                period=supply.period,
                grade_id=supply.grade_id,
                origin=supply.origin,
                volume_m3=slack,
                reason=reason,
                penalty_cost_cad_m3=inputs.unassigned_penalty_cad_m3,
            )
        )

    assignments.sort(key=lambda item: (item.facility_id, item.grade_id, item.supply_id))
    unassigned.sort(key=lambda item: (item.grade_id, item.supply_id))
    return _build_result(
        inputs,
        problem,
        status,
        tuple(assignments),
        tuple(unassigned),
        len(x),
        len(u),
        assigned_by_facility,
        assigned_by_facility_grade,
    )


def _build_result(
    inputs: AssignmentInputs,
    problem: pulp.LpProblem,
    status: str,
    assignments: tuple[Assignment, ...],
    unassigned: tuple[UnassignedSupply, ...],
    variable_count: int,
    slack_count: int,
    assigned_by_facility: Mapping[str, float],
    assigned_by_facility_grade: Mapping[tuple[str, str], float],
) -> AssignmentResult:
    facility_rows: list[dict[str, Any]] = []
    for facility in inputs.facilities:
        assigned = float(assigned_by_facility.get(facility.facility_id, 0.0))
        utilization = (
            assigned / facility.nameplate_m3_y if facility.nameplate_m3_y > 0 else 0.0
        )
        grade_rows = []
        for offer in facility.offers:
            grade_assigned = float(
                assigned_by_facility_grade.get(
                    (facility.facility_id, offer.grade_id), 0.0
                )
            )
            grade_rows.append(
                {
                    "grade_id": offer.grade_id,
                    "capacity_m3_y": offer.capacity_m3_y,
                    "assigned_m3": grade_assigned,
                    "utilization": (
                        grade_assigned / offer.capacity_m3_y
                        if offer.capacity_m3_y > 0
                        else 0.0
                    ),
                    "binding": _at_capacity(grade_assigned, offer.capacity_m3_y),
                }
            )
        facility_rows.append(
            {
                "facility_id": facility.facility_id,
                "facility_name": facility.facility_name,
                "facility_class": facility.facility_class,
                "nameplate_m3_y": facility.nameplate_m3_y,
                "assigned_m3": assigned,
                "unused_capacity_m3": max(0.0, facility.nameplate_m3_y - assigned),
                "utilization": utilization,
                "capacity_binding": _at_capacity(assigned, facility.nameplate_m3_y),
                "eligible_grades": ";".join(facility.eligible_grades),
                "grade_rows": grade_rows,
            }
        )

    contract_rows: list[dict[str, Any]] = []
    for contract in inputs.contracts:
        assigned = float(
            sum(
                item.volume_m3
                for item in assignments
                if item.facility_id == contract.facility_id
                and item.period == contract.period
                and contract.accepts(item.grade_id)
            )
        )
        contract_rows.append(
            {
                "contract_id": contract.contract_id,
                "facility_id": contract.facility_id,
                "period": contract.period,
                "accepted_grades": ";".join(contract.accepted_grades),
                "min_volume_m3": contract.min_volume_m3,
                "max_volume_m3": contract.max_volume_m3,
                "assigned_m3": assigned,
                "within_minimum": assigned
                >= contract.min_volume_m3
                - _TOLERANCE * max(1.0, abs(contract.min_volume_m3)),
                "within_maximum": assigned
                <= contract.max_volume_m3
                + _TOLERANCE * max(1.0, abs(contract.max_volume_m3)),
                "gate_cost_cad_m3": contract.gate_cost_cad_m3,
                "diagnostic": contract.diagnostic,
                "evidence": contract.evidence,
                "notes": contract.notes,
            }
        )

    revenue = sum(item.volume_m3 * item.product_revenue_cad_m3 for item in assignments)
    supply_cost = sum(item.volume_m3 * item.supply_cost_cad_m3 for item in assignments)
    transport_cost = sum(
        item.volume_m3 * item.transport_cost_cad_m3 for item in assignments
    )
    gate_cost = sum(item.volume_m3 * item.gate_cost_cad_m3 for item in assignments)
    processing_cost = sum(
        item.volume_m3 * item.processing_cost_cad_m3 for item in assignments
    )
    penalty = sum(item.volume_m3 * item.penalty_cost_cad_m3 for item in unassigned)
    net = revenue - supply_cost - transport_cost - gate_cost - processing_cost - penalty
    solver_objective = float(pulp.value(problem.objective) or 0.0)
    decomposition = (
        {
            "component": "product_revenue",
            "value_cad": revenue,
            "unit": "CAD",
            "notes": "assigned volume x facility product revenue",
        },
        {
            "component": "supply_cost",
            "value_cad": -supply_cost,
            "unit": "CAD",
            "notes": "assigned volume x roadside cost proxy",
        },
        {
            "component": "transport_cost",
            "value_cad": -transport_cost,
            "unit": "CAD",
            "notes": "assigned volume x diagnostic road cost",
        },
        {
            "component": "gate_cost",
            "value_cad": -gate_cost,
            "unit": "CAD",
            "notes": "assigned volume x contract gate cost",
        },
        {
            "component": "processing_cost",
            "value_cad": -processing_cost,
            "unit": "CAD",
            "notes": "assigned volume x line processing cost",
        },
        {
            "component": "unassigned_penalty",
            "value_cad": -penalty,
            "unit": "CAD",
            "notes": "unassigned volume x diagnostic penalty",
        },
        {
            "component": "net_objective",
            "value_cad": net,
            "unit": "CAD",
            "notes": f"solver objective {solver_objective:.6f}",
        },
    )

    return AssignmentResult(
        run_id=inputs.run_id,
        scenario_id=inputs.scenario_id,
        period="|".join(inputs.periods),
        status=status,
        objective_value_cad=solver_objective,
        assignments=assignments,
        unassigned=unassigned,
        facility_utilization=tuple(facility_rows),
        contract_compliance=tuple(contract_rows),
        objective_decomposition=decomposition,
        solver_name="PULP_CBC_CMD",
        solver_version=pulp.__version__,
        inputs_summary={
            **inputs.summary(),
            "assignment_variable_count": variable_count,
            "unassigned_variable_count": slack_count,
            "assignment_row_count": len(assignments),
            "unassigned_row_count": len(unassigned),
        },
        metadata={
            **dict(inputs.metadata),
            "objective": "maximise product revenue minus supply, transport, gate, processing and unassigned penalty",
            "binary_variables": 0,
            "line_activity_variables": 0,
        },
    )
