"""Synthetic tests for the Stage A facility-assignment contract."""

from __future__ import annotations

import unittest
from dataclasses import replace

from workflows.national.assignments.model import AssignmentSolveError, solve_assignment
from workflows.national.assignments.schema import (
    AssignmentError,
    AssignmentInputs,
    Contract,
    FacilityCapacity,
    FacilityOffer,
    FeedstockGrade,
    SupplyOffer,
    TransportLink,
    capacity_unit_is_supported,
    require_supported_capacity_unit,
)
from workflows.national.assignments.validate import validate_assignment

PERIOD = "1"
ORIGIN = "TSA24_TEST"


def _grade(grade_id: str, product: str, density: float = 0.40) -> FeedstockGrade:
    return FeedstockGrade(
        grade_id=grade_id,
        product=product,
        unit="m3",
        accepted_sorts=(grade_id,),
        density_odt_m3=density,
        moisture_wet_basis=0.45,
        evidence="test",
    )


def _offer(
    facility_id: str,
    facility_class: str,
    grade_id: str,
    capacity: float,
    processing: float,
    revenue: float,
) -> FacilityOffer:
    return FacilityOffer(
        offer_id=f"{facility_id}::{grade_id}",
        period=PERIOD,
        facility_id=facility_id,
        facility_name=facility_id,
        facility_class=facility_class,
        grade_id=grade_id,
        primary_product="test_product",
        capacity_m3_y=capacity,
        processing_cost_cad_m3=processing,
        product_revenue_cad_m3=revenue,
        capacity_basis="archive_m3_y",
        cost_basis="test",
        revenue_basis="test",
        evidence="test",
    )


def _supply(
    offer_id: str,
    grade_id: str,
    volume: float,
    cost: float,
    moisture: float = 0.45,
) -> SupplyOffer:
    return SupplyOffer(
        offer_id=offer_id,
        period=PERIOD,
        grade_id=grade_id,
        product="test_product",
        sort_code=grade_id,
        top_cm=10.0,
        origin=ORIGIN,
        volume_m3=volume,
        density_odt_m3=0.45 if grade_id == "RESIDUE" else 0.40,
        moisture_wet_basis=moisture,
        roadside_cost_cad_m3=cost,
        evidence="test",
    )


def _link(facility_id: str, grade_id: str, cost: float) -> TransportLink:
    return TransportLink(
        link_id=f"{ORIGIN}->{facility_id}::{grade_id}",
        period=PERIOD,
        origin=ORIGIN,
        destination=facility_id,
        grade_id=grade_id,
        mode="ROAD",
        distance_km=100.0,
        cost_cad_m3=cost,
        capacity_m3=None,
        distance_basis="test",
        cost_basis="test",
        evidence="test",
    )


def build_problem(
    *,
    saw_volume: float = 120.0,
    residue_volume: float = 50.0,
    saw_capacity: float = 100.0,
    residue_capacity: float = 80.0,
    saw_processing: float = 10.0,
    saw_revenue: float = 50.0,
    saw_supply_cost: float = 20.0,
    saw_transport: float = 5.0,
    residue_processing: float = 5.0,
    residue_revenue: float = 30.0,
    residue_transport: float = 3.0,
    contract_max: float = 100.0,
    contract_min: float = 0.0,
    penalty: float = 1000.0,
    moisture_limit: float | None = None,
    saw_moisture: float = 0.45,
    include_pulp: bool = False,
) -> AssignmentInputs:
    grades = [_grade("SAWLOG", "sawlog"), _grade("RESIDUE", "residue", 0.45)]
    if include_pulp:
        grades.append(_grade("PULP", "pulpwood"))
    facilities = (
        FacilityCapacity(
            facility_id="F1",
            facility_name="F1",
            facility_class="SAW",
            nameplate_m3_y=saw_capacity,
            offers=(
                _offer(
                    "F1", "SAW", "SAWLOG", saw_capacity, saw_processing, saw_revenue
                ),
            ),
            evidence="test",
        ),
        FacilityCapacity(
            facility_id="F2",
            facility_name="F2",
            facility_class="PEL",
            nameplate_m3_y=residue_capacity,
            offers=(
                _offer(
                    "F2",
                    "PEL",
                    "RESIDUE",
                    residue_capacity,
                    residue_processing,
                    residue_revenue,
                ),
            ),
            evidence="test",
        ),
    )
    supplies = [
        _supply("S1", "SAWLOG", saw_volume, saw_supply_cost, saw_moisture),
        _supply("S2", "RESIDUE", residue_volume, 0.0),
    ]
    links = [
        _link("F1", "SAWLOG", saw_transport),
        _link("F2", "RESIDUE", residue_transport),
    ]
    contracts = [
        Contract(
            contract_id="C1",
            period=PERIOD,
            facility_id="F1",
            accepted_grades=("SAWLOG",),
            min_volume_m3=contract_min,
            max_volume_m3=contract_max,
            gate_cost_cad_m3=0.0,
            evidence="test",
            diagnostic=True,
        ),
        Contract(
            contract_id="C2",
            period=PERIOD,
            facility_id="F2",
            accepted_grades=("RESIDUE",),
            min_volume_m3=0.0,
            max_volume_m3=residue_capacity,
            gate_cost_cad_m3=0.0,
            evidence="test",
            diagnostic=True,
        ),
    ]
    if include_pulp:
        supplies.append(_supply("S3", "PULP", 10.0, 0.0))
    moisture = {"SAW": moisture_limit} if moisture_limit is not None else {}
    return AssignmentInputs(
        run_id="test_run",
        scenario_id="test_scenario",
        periods=(PERIOD,),
        grades=tuple(grades),
        supplies=tuple(supplies),
        facilities=facilities,
        transport_links=tuple(links),
        contracts=tuple(contracts),
        unassigned_penalty_cad_m3=penalty,
        moisture_limit_by_class=moisture,
        metadata={"test": True},
    )


class AssignmentTests(unittest.TestCase):
    def test_volume_conservation_and_capacity_binding(self) -> None:
        inputs = build_problem()
        result = solve_assignment(inputs)
        report = validate_assignment(inputs, result)

        self.assertTrue(report.passed, report.to_dict())
        assigned = {item.supply_id: item.volume_m3 for item in result.assignments}
        self.assertAlmostEqual(assigned["S1"], 100.0, places=6)
        self.assertAlmostEqual(assigned["S2"], 50.0, places=6)
        self.assertAlmostEqual(result.assigned_volume_m3, 150.0, places=6)
        self.assertAlmostEqual(result.unassigned_volume_m3, 20.0, places=6)
        self.assertEqual([item.supply_id for item in result.unassigned], ["S1"])
        self.assertEqual(result.unassigned[0].reason, "capacity_exhausted")

        utilization = {row["facility_id"]: row for row in result.facility_utilization}
        self.assertTrue(utilization["F1"]["capacity_binding"])
        self.assertFalse(utilization["F2"]["capacity_binding"])
        self.assertAlmostEqual(utilization["F1"]["utilization"], 1.0, places=6)
        self.assertAlmostEqual(utilization["F2"]["utilization"], 50.0 / 80.0, places=6)

    def test_objective_reconciliation(self) -> None:
        inputs = build_problem()
        result = solve_assignment(inputs)
        report = validate_assignment(inputs, result)

        self.assertTrue(report.passed, report.to_dict())
        components = {
            row["component"]: row["value_cad"] for row in result.objective_decomposition
        }
        expected_net = (
            components["product_revenue"]
            + components["supply_cost"]
            + components["transport_cost"]
            + components["gate_cost"]
            + components["processing_cost"]
            + components["unassigned_penalty"]
        )
        self.assertAlmostEqual(components["net_objective"], expected_net, places=4)
        self.assertAlmostEqual(result.objective_value_cad, expected_net, places=4)
        self.assertEqual(result.metadata["binary_variables"], 0)

    def test_contract_maximum_binds(self) -> None:
        inputs = build_problem(contract_max=40.0)
        result = solve_assignment(inputs)
        report = validate_assignment(inputs, result)

        self.assertTrue(report.passed, report.to_dict())
        assigned = {item.supply_id: item.volume_m3 for item in result.assignments}
        self.assertAlmostEqual(assigned["S1"], 40.0, places=6)
        self.assertAlmostEqual(result.unassigned_volume_m3, 80.0, places=6)

    def test_contract_minimum_is_enforced(self) -> None:
        inputs = build_problem(contract_min=130.0, contract_max=200.0)
        with self.assertRaises(AssignmentSolveError):
            solve_assignment(inputs)

    def test_grade_eligibility_leaves_unassigned(self) -> None:
        inputs = build_problem(include_pulp=True)
        result = solve_assignment(inputs)
        report = validate_assignment(inputs, result)

        self.assertTrue(report.passed, report.to_dict())
        pulp = [item for item in result.unassigned if item.grade_id == "PULP"]
        self.assertEqual(len(pulp), 1)
        self.assertEqual(pulp[0].reason, "no_eligible_facility")
        self.assertAlmostEqual(pulp[0].volume_m3, 10.0, places=6)

    def test_unassigned_penalty_switch(self) -> None:
        weak = build_problem(
            saw_volume=100.0,
            saw_processing=10.0,
            saw_revenue=10.0,
            saw_supply_cost=20.0,
            saw_transport=5.0,
            penalty=1.0,
        )
        weak_result = solve_assignment(weak)
        weak_report = validate_assignment(weak, weak_result)
        self.assertTrue(weak_report.passed, weak_report.to_dict())
        self.assertEqual([item.supply_id for item in weak_result.unassigned], ["S1"])

        strong = replace(weak, unassigned_penalty_cad_m3=1000.0)
        strong_result = solve_assignment(strong)
        strong_report = validate_assignment(strong, strong_result)
        self.assertTrue(strong_report.passed, strong_report.to_dict())
        self.assertNotIn("S1", [item.supply_id for item in strong_result.unassigned])

    def test_moisture_limit_forces_unassigned(self) -> None:
        inputs = build_problem(moisture_limit=0.40, saw_moisture=0.50)
        result = solve_assignment(inputs)
        report = validate_assignment(inputs, result)

        self.assertTrue(report.passed, report.to_dict())
        saw = [item for item in result.unassigned if item.grade_id == "SAWLOG"]
        self.assertEqual(len(saw), 1)
        self.assertEqual(saw[0].reason, "moisture_limit")
        self.assertAlmostEqual(saw[0].volume_m3, 120.0, places=6)

    def test_capacity_unit_rejection(self) -> None:
        self.assertTrue(capacity_unit_is_supported("m3/y"))
        self.assertTrue(capacity_unit_is_supported("odt/y"))
        self.assertTrue(capacity_unit_is_supported("bdmt/y"))
        for unit in ("admt/y", "mbf/y", "kwh/y"):
            with self.assertRaises(AssignmentError):
                require_supported_capacity_unit(unit)

    def test_transport_cost_is_recomputed(self) -> None:
        inputs = build_problem()
        result = solve_assignment(inputs)
        for assignment in result.assignments:
            link = inputs.link(
                assignment.origin,
                assignment.facility_id,
                assignment.grade_id,
                assignment.period,
            )
            self.assertIsNotNone(link)
            self.assertAlmostEqual(
                assignment.transport_cost_cad_m3, link.cost_cad_m3, places=9
            )


if __name__ == "__main__":
    unittest.main()
