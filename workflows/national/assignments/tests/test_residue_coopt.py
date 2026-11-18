"""Unit tests for the BC residue co-optimisation LP."""

from __future__ import annotations

import math
from dataclasses import replace

import pandas as pd
import pytest

from workflows.national.assignments.residue_coopt import (
    CooptError,
    CooptLink,
    CooptRecipient,
    CooptSource,
    _build_harvest_sources,
    _require_text,
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


def link(
    source_id: str,
    facility_id: str,
    cost: float = 10.0,
    path_confidence: str = "real_road_network",
    ferry_km: float = 0.0,
) -> CooptLink:
    return CooptLink(
        source_id=source_id,
        recipient_facility=facility_id,
        distance_km=100.0,
        cost_cad_m3=cost,
        path_confidence=path_confidence,
        evidence="test",
        ferry_distance_km=ferry_km,
        ferry_cost_cad_m3=ferry_km * 0.03,
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


def _stage_a_frame(zone: str, assigned_odt: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "period": "2028",
                "zone": zone,
                "facility_id": "BC-PEL-1",
                "grade": "RESIDUE",
                "volume_m3": assigned_odt / DENSITY,
                "supply_id": f"{zone}-residue-1",
            }
        ]
    )


def _supply_frame(zone: str, recoverable_odt: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "zone": zone,
                "year": "2028",
                "product": "residue",
                "species": "PICE.MAR",
                "residue_recoverable_odt": recoverable_odt,
            }
        ]
    )


def test_harvest_sources_use_recoverable_not_stage_a_assigned() -> None:
    stage_a = _stage_a_frame("B1", assigned_odt=60.0)
    supply = _supply_frame("B1", recoverable_odt=100.0)
    sources = _build_harvest_sources(stage_a, supply, "2028")
    assert len(sources) == 1
    assert sources[0].source_id == "HARVEST::B1"
    assert sources[0].odt == pytest.approx(100.0)
    assert sources[0].stage_a_assigned_odt == pytest.approx(60.0)
    assert sources[0].unassigned_by_stage_a_odt == pytest.approx(40.0)


def test_harvest_conservation_uses_recoverable_total() -> None:
    stage_a = _stage_a_frame("B1", assigned_odt=60.0)
    supply = _supply_frame("B1", recoverable_odt=100.0)
    harvest = _build_harvest_sources(stage_a, supply, "2028")[0]
    pel = recipient("PEL-1", "PEL", 500.0)
    result = solve_coopt([harvest], [pel], [link(harvest.source_id, "PEL-1")])
    assert result.generated_odt == pytest.approx(100.0)
    assert result.assigned_odt == pytest.approx(100.0)
    assert result.assigned_odt + (
        result.generated_odt - result.assigned_odt
    ) == pytest.approx(100.0)
    assert harvest.stage_a_assigned_odt == pytest.approx(60.0)


def test_harvest_source_above_recoverable_fails_closed() -> None:
    stage_a = _stage_a_frame("B1", assigned_odt=120.0)
    supply = _supply_frame("B1", recoverable_odt=100.0)
    with pytest.raises(CooptError, match="above recoverable"):
        _build_harvest_sources(stage_a, supply, "2028")


def test_fallback_link_excluded_by_default_and_allowed_on_request() -> None:
    harvest = source("H1", "harvest_residue", "harvest_residue", 100.0)
    real = recipient("BIO-REAL", "BIO", 40.0)
    fallback = recipient("BIO-FALLBACK", "BIO", 100.0)
    links = [
        link("H1", "BIO-REAL"),
        link(
            "H1",
            "BIO-FALLBACK",
            path_confidence="ffcm_route_fallback_road",
        ),
    ]
    default = solve_coopt([harvest], [real, fallback], links)
    assert {item.recipient.facility_id for item in default.allocations} == {
        "BIO-REAL"
    }
    assert any(
        pair["reason"] == "non_nrn_fallback"
        and pair["recipient_facility"] == "BIO-FALLBACK"
        for pair in default.excluded_pairs
    )
    assert validate_coopt(default).passed

    sensitivity = solve_coopt(
        [harvest], [real, fallback], links, allow_fallback_links=True
    )
    assert sensitivity.assigned_odt == pytest.approx(100.0)
    assert "ffcm_route_fallback_road" in sensitivity.accepted_path_confidence
    assert validate_coopt(sensitivity).passed
    tampered = replace(
        sensitivity, accepted_path_confidence=("real_road_network",)
    )
    assert not validate_coopt(tampered).passed


def test_missing_path_confidence_is_not_defaulted() -> None:
    with pytest.raises(CooptError, match="provenance cannot be defaulted"):
        _require_text(float("nan"), "path_confidence")
    with pytest.raises(CooptError, match="provenance cannot be defaulted"):
        _require_text("", "path_confidence")
