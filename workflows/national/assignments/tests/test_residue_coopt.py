"""Unit tests for the BC residue co-optimisation LP."""

from __future__ import annotations

import math

import pytest

from workflows.national.assignments.residue_coopt import (
    CooptError,
    CooptLink,
    CooptRecipient,
    CooptSource,
    solve_coopt,
    validate_coopt,
)

DENSITY = 0.45


def source(
    source_id: str,
    kind: str,
    residue_type: str,
    odt: float,
    facility: str = "SRC",
) -> CooptSource:
    return CooptSource(
        source_id=source_id,
        source_kind=kind,
        source_facility=facility,
        zone="B1" if kind == "harvest_residue" else "",
        residue_type=residue_type,
        odt=odt,
        volume_m3=odt / DENSITY,
        moisture_wet_basis=0.45,
        baseline_recipient="",
        evidence="test",
    )


def recipient(
    facility_id: str,
    facility_class: str,
    capacity: float,
    revenue: float = 100.0,
    operating: float = 20.0,
) -> CooptRecipient:
    return CooptRecipient(
        facility_id=facility_id,
        facility_class=facility_class,
        capacity_odt_y=capacity,
        capacity_m3_y=capacity / DENSITY,
        product="test_product",
        product_revenue_cad_per_input_odt=revenue,
        operating_cost_cad_per_input_odt=operating,
        price_status="available",
        processing_lines=f"{facility_id}-1",
        coord_source="test",
    )


def link(source_id: str, facility_id: str, cost: float = 10.0) -> CooptLink:
    return CooptLink(
        source_id=source_id,
        recipient_facility=facility_id,
        distance_km=100.0,
        cost_cad_m3=cost,
        path_confidence="real_road_network",
        evidence="test",
    )


def test_shared_capacity_and_source_conservation() -> None:
    harvest = source("H1", "harvest_residue", "harvest_residue", 100.0)
    sawmill = source("S1", "sawmill_residue", "sawdust_shavings", 100.0)
    pel = recipient("PEL-1", "PEL", 150.0)
    links = [link("H1", "PEL-1"), link("S1", "PEL-1")]
    result = solve_coopt([harvest, sawmill], [pel], links, objective_mode="profit_max")
    assert math.isclose(result.assigned_odt, 150.0, abs_tol=1e-6)
    assert math.isclose(
        sum(item.odt for item in result.allocations if item.source.source_id == "H1")
        + sum(item.odt for item in result.allocations if item.source.source_id == "S1"),
        150.0,
        abs_tol=1e-6,
    )
    validation = validate_coopt(result)
    assert validation.passed
    assert any(
        check.name == "shared_capacity" and check.passed for check in validation.checks
    )


def test_service_max_assigns_both_streams_when_capacity_allows() -> None:
    harvest = source("H1", "harvest_residue", "harvest_residue", 100.0)
    sawmill = source("S1", "sawmill_residue", "sawdust_shavings", 100.0)
    pel = recipient("PEL-1", "PEL", 500.0)
    links = [link("H1", "PEL-1"), link("S1", "PEL-1")]
    result = solve_coopt([harvest, sawmill], [pel], links)
    assert math.isclose(result.assigned_odt, 200.0, abs_tol=1e-6)
    assert math.isclose(result.generated_odt, 200.0, abs_tol=1e-6)


def test_residue_type_eligibility_rejects_wrong_class() -> None:
    chips = source("S1", "sawmill_residue", "chips", 100.0)
    pel = recipient("PEL-1", "PEL", 100.0)
    links = [link("S1", "PEL-1")]
    with pytest.raises(CooptError, match="no eligible"):
        solve_coopt([chips], [pel], links)


def test_transport_link_existence_excludes_missing_pair() -> None:
    harvest = source("H1", "harvest_residue", "harvest_residue", 100.0)
    pel = recipient("PEL-1", "PEL", 100.0)
    with pytest.raises(CooptError, match="no eligible"):
        solve_coopt([harvest], [pel], [])


def test_objective_reconciles_to_assignment_components() -> None:
    sawmill = source("S1", "sawmill_residue", "sawdust_shavings", 100.0)
    pel = recipient("PEL-1", "PEL", 100.0, revenue=150.0, operating=30.0)
    links = [link("S1", "PEL-1", cost=9.0)]
    result = solve_coopt([sawmill], [pel], links, objective_mode="profit_max")
    expected = 100.0 * (150.0 - 30.0 - 9.0 / DENSITY)
    assert math.isclose(result.net_contribution_cad, expected, rel_tol=1e-9)
    assert math.isclose(
        result.net_contribution_cad, result.raw_solver_objective_cad, abs_tol=1e-6
    )
    validation = validate_coopt(result)
    assert validation.passed


def test_capacity_binding_flag_uses_relative_tolerance() -> None:
    sawmill = source("S1", "sawmill_residue", "sawdust_shavings", 100.0)
    pel = recipient("PEL-1", "PEL", 100.0)
    result = solve_coopt([sawmill], [pel], [link("S1", "PEL-1")])
    utilization = result.allocations[0].recipient.capacity_odt_y - result.assigned_odt
    assert utilization <= 1e-6
