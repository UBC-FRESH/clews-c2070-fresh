"""Tests for the Stage B residue supply, demand, allocation, and validation."""

from __future__ import annotations

import unittest
from dataclasses import replace

from workflows.national.assignments.residue import (
    DENSITY_ODT_M3,
    CommodityPrice,
    FacilityLocation,
    ResidueDemandRow,
    ResidueError,
    ResidueSupplyRow,
    compute_line_demand,
    compute_line_residue_supply,
    eligible_residue_types,
)
from workflows.national.assignments.stage_b import (
    StageBLink,
    solve_residue_allocation,
    validate_stage_b,
)


def _line(
    *,
    processing_line: str = "TEST-SAW-1",
    facility: str = "TEST-SAW",
    feedstock: str = "log",
    product: str = "lumber_softwood_west",
    infeed_rate: float = 1000.0,
    infeed_unit: str = "m3/y",
    operating_cost: float = 10.0,
    operating_cost_unit: str = "$/m3",
) -> dict[str, str]:
    return {
        "processing_line": processing_line,
        "facility": facility,
        "feedstock": feedstock,
        "primary_product": product,
        "infeed_rate": str(infeed_rate),
        "infeed_unit": infeed_unit,
        "operating_cost": str(operating_cost),
        "operating_cost_unit": operating_cost_unit,
    }


def _flow(
    line_id: str, commodity: str, value: float, unit: str
) -> dict[str, str]:
    return {
        "processing_line": line_id,
        "flow_attribute": "OutfeedRateByEntity",
        "commodity": commodity,
        "value": str(value),
        "unit": unit,
    }


def _facility(
    facility_id: str = "TEST-PAP",
    facility_class: str = "PAP",
    coord_source: str = "mills_db_2015",
) -> FacilityLocation:
    return FacilityLocation(
        facility_id=facility_id,
        facility_class=facility_class,
        name=facility_id,
        lat=53.9,
        lon=-122.7,
        coord_source=coord_source,
        coord_confidence="high",
    )


def _supply(
    source: str, residue_type: str, odt: float, *, moisture: float = 0.45
) -> ResidueSupplyRow:
    return ResidueSupplyRow(
        source_facility=source,
        residue_type=residue_type,
        volume_m3=odt / DENSITY_ODT_M3,
        odt=odt,
        moisture_wet_basis=moisture,
        unit_basis="test",
        evidence="test",
        processing_line=f"{source}-1",
        outfeed_commodity=residue_type,
        outfeed_value=1.0,
        outfeed_unit="odt",
        infeed_rate=1.0,
        infeed_unit="m3_green",
        ratio_odt_per_infeed_unit=0.0,
        ratio_odt_per_m3_log=0.0,
        stage_a_assigned_m3=0.0,
        density_odt_m3=DENSITY_ODT_M3,
        allocatable=True,
        allocation_note="",
    )


def _demand(
    recipient: str,
    residue_type: str,
    capacity: float,
    *,
    revenue: float = 100.0,
    processing: float = 10.0,
    facility_class: str = "PAP",
) -> ResidueDemandRow:
    return ResidueDemandRow(
        recipient_facility=recipient,
        facility_class=facility_class,
        processing_line=f"{recipient}-1",
        accepted_residue_type=residue_type,
        capacity_odt_y=capacity,
        capacity_m3_y=capacity / DENSITY_ODT_M3,
        capacity_basis="test",
        operating_cost=processing,
        operating_cost_unit="$/odt",
        operating_cost_cad_per_input_odt=processing,
        product="test_product",
        product_output_per_input_odt=1.0,
        product_output_unit="odt",
        product_price=revenue,
        product_price_unit="$/odt",
        product_revenue_cad_per_input_odt=revenue,
        price_status="available",
        coord_source="mills_db_2015",
        coord_confidence="high",
        location_quality="defensible",
        evidence="test",
        status="included",
        status_reason="",
    )


def _link(
    source: str, recipient: str, *, distance: float = 10.0, cost: float = 1.0
) -> StageBLink:
    return StageBLink(
        source_facility=source,
        recipient_facility=recipient,
        distance_km=distance,
        cost_cad_m3=cost,
        path_confidence="real_road_network",
        source_coord_source="mills_db_2015",
        recipient_coord_source="mills_db_2015",
        source_snap_m=1.0,
        recipient_snap_m=1.0,
        source_component=1,
        recipient_component=1,
        network_source="test",
        distance_basis="test",
        cost_basis="test",
        evidence="test",
    )


class ResidueSupplyTests(unittest.TestCase):
    def test_outfeed_ratio_converts_to_odt_and_green_m3(self) -> None:
        line = _line()
        flows = [
            _flow("TEST-SAW-1", "woodchips", 160.0, "odt/y"),
            _flow("TEST-SAW-1", "sawdust_shavings", 50.0, "odt/y"),
            _flow("TEST-SAW-1", "hogfuel", 30.0, "odt/y"),
            _flow("TEST-SAW-1", "internally_used_woodfuel", 20.0, "odt/y"),
        ]
        rows, reasons = compute_line_residue_supply(line, flows, 500.0)
        self.assertEqual(reasons, {})
        self.assertAlmostEqual(rows["chips"].ratio_odt_per_infeed_unit, 0.16)
        self.assertAlmostEqual(rows["chips"].odt, 80.0)
        self.assertAlmostEqual(rows["chips"].volume_m3, 80.0 / 0.45)
        self.assertAlmostEqual(rows["sawdust_shavings"].odt, 25.0)
        self.assertAlmostEqual(rows["hogfuel"].odt, 15.0)
        self.assertFalse(rows["other"].allocatable)

    def test_missing_outfeed_ratio_is_flagged_not_invented(self) -> None:
        line = _line()
        flows = [
            _flow("TEST-SAW-1", "woodchips", 160.0, "odt/y"),
        ]
        rows, reasons = compute_line_residue_supply(line, flows, 500.0)
        self.assertNotIn("hogfuel", rows)
        self.assertEqual(reasons["hogfuel"], "missing_outfeed_ratio")
        self.assertEqual(reasons["other"], "missing_outfeed_ratio")
        self.assertIn("chips", rows)

    def test_admt_outfeed_is_rejected_without_declared_moisture(self) -> None:
        line = _line()
        flows = [_flow("TEST-SAW-1", "woodchips", 160.0, "admt/y")]
        with self.assertRaises(ResidueError):
            compute_line_residue_supply(line, flows, 500.0)


class ResidueDemandTests(unittest.TestCase):
    def test_pap_capacity_price_and_processing_per_input_odt(self) -> None:
        line = _line(
            processing_line="TEST-PAP-1",
            facility="TEST-PAP",
            feedstock="woodchips",
            product="kraft_softwood",
            infeed_rate=1000.0,
            infeed_unit="bdmt/y",
            operating_cost=200.0,
            operating_cost_unit="cad/admt",
        )
        flows = [_flow("TEST-PAP-1", "kraft_softwood", 500.0, "admt/y")]
        price = CommodityPrice(
            commodity="kraft_softwood",
            value=800.0,
            unit="$/t",
            unit_type="CostPerMassUnit",
            energy_density=None,
            energy_density_unit="",
        )
        row = compute_line_demand(line, flows, _facility(), price)
        assert row is not None
        self.assertEqual(row.status, "candidate")
        self.assertAlmostEqual(row.capacity_odt_y, 1000.0)
        self.assertAlmostEqual(row.capacity_m3_y, 1000.0 / 0.45)
        self.assertAlmostEqual(row.product_output_per_input_odt, 0.5)
        self.assertAlmostEqual(row.operating_cost_cad_per_input_odt, 100.0)
        self.assertAlmostEqual(row.product_revenue_cad_per_input_odt, 400.0)
        self.assertEqual(row.price_status, "available")

    def test_pellet_and_electricity_price_bases(self) -> None:
        pellet_line = _line(
            processing_line="TEST-PEL-1",
            facility="TEST-PEL",
            feedstock="sawdust_shavings",
            product="pellet",
            infeed_rate=100.0,
            infeed_unit="odt/y",
            operating_cost=48.0,
            operating_cost_unit="$/odt",
        )
        pellet_flows = [_flow("TEST-PEL-1", "pellet", 87.0, "odt/y")]
        pellet_price = CommodityPrice(
            "pellet", 150.0, "$/t", "CostPerMassUnit", None, ""
        )
        pellet = compute_line_demand(
            pellet_line, pellet_flows, _facility("TEST-PEL", "PEL"), pellet_price
        )
        assert pellet is not None
        self.assertAlmostEqual(pellet.product_output_per_input_odt, 0.87)
        self.assertAlmostEqual(pellet.product_revenue_cad_per_input_odt, 130.5)
        self.assertAlmostEqual(pellet.operating_cost_cad_per_input_odt, 41.76)

        bio_line = _line(
            processing_line="TEST-BIO-1",
            facility="TEST-BIO",
            feedstock="hogfuel",
            product="electricity_BC",
            infeed_rate=100.0,
            infeed_unit="odt/y",
            operating_cost=0.028,
            operating_cost_unit="$/kwh",
        )
        bio_flows = [_flow("TEST-BIO-1", "electricity_BC", 74067.6923, "kwh/y")]
        bio_price = CommodityPrice(
            "electricity_BC",
            100.0,
            "$/MWh",
            "CostPerEnergyUnit",
            0.740677,
            "MWh/odt",
        )
        bio = compute_line_demand(
            bio_line, bio_flows, _facility("TEST-BIO", "BIO"), bio_price
        )
        assert bio is not None
        self.assertAlmostEqual(
            bio.product_output_per_input_odt, 740.676923, places=6
        )
        self.assertAlmostEqual(bio.product_revenue_cad_per_input_odt, 74.067692, places=6)
        self.assertAlmostEqual(bio.operating_cost_cad_per_input_odt, 20.738954, places=6)

    def test_admt_infeed_is_excluded(self) -> None:
        line = _line(
            processing_line="TEST-PAP-1",
            facility="TEST-PAP",
            feedstock="woodchips",
            product="kraft_softwood",
            infeed_rate=1000.0,
            infeed_unit="admt/y",
        )
        flows = [_flow("TEST-PAP-1", "kraft_softwood", 500.0, "admt/y")]
        row = compute_line_demand(
            line,
            flows,
            _facility(),
            CommodityPrice("kraft_softwood", 800.0, "$/t", "", None, ""),
        )
        assert row is not None
        self.assertEqual(row.status, "excluded")
        self.assertIn("line_rejected", row.status_reason)

    def test_eligibility_is_class_specific(self) -> None:
        self.assertEqual(eligible_residue_types("PAP"), frozenset({"chips"}))
        self.assertEqual(
            eligible_residue_types("PEL"), frozenset({"sawdust_shavings"})
        )
        self.assertEqual(eligible_residue_types("BIO"), frozenset({"hogfuel"}))
        self.assertEqual(eligible_residue_types("SAW"), frozenset())


class AllocationTests(unittest.TestCase):
    def test_capacity_binds_and_balance_holds(self) -> None:
        supplies = [
            _supply("SAW-A", "chips", 100.0),
            _supply("SAW-B", "chips", 50.0),
        ]
        demand = [_demand("PAP-1", "chips", 120.0)]
        links = [
            _link("SAW-A", "PAP-1", cost=0.0),
            _link("SAW-B", "PAP-1", cost=0.0),
        ]
        result = solve_residue_allocation(supplies, demand, links)
        self.assertEqual(result.status, "Optimal")
        self.assertAlmostEqual(result.assigned_odt, 120.0, places=6)
        leftover = sum(item.odt for item in result.unassigned)
        self.assertAlmostEqual(leftover, 30.0, places=6)
        self.assertEqual(
            {item.reason for item in result.unassigned}, {"capacity_exhausted"}
        )
        validation = validate_stage_b(supplies, demand, links, result)
        self.assertTrue(validation.passed, validation.to_dict())

    def test_ineligible_residue_is_unassigned(self) -> None:
        supplies = [_supply("SAW-A", "sawdust_shavings", 40.0)]
        demand = [_demand("PAP-1", "chips", 100.0)]
        links = [_link("SAW-A", "PAP-1")]
        result = solve_residue_allocation(supplies, demand, links)
        self.assertEqual(result.allocations, ())
        self.assertEqual(len(result.unassigned), 1)
        self.assertEqual(result.unassigned[0].reason, "no_eligible_recipient")

    def test_cost_min_mode_assigns_the_service_maximum(self) -> None:
        supplies = [_supply("SAW-A", "chips", 100.0)]
        demand = [
            replace(
                _demand("PAP-1", "chips", 100.0),
                price_status="missing",
                product_revenue_cad_per_input_odt=0.0,
            )
        ]
        links = [_link("SAW-A", "PAP-1", cost=2.0)]
        result = solve_residue_allocation(
            supplies, demand, links, objective_mode="cost_min"
        )
        self.assertAlmostEqual(result.assigned_odt, 100.0, delta=1e-4)
        self.assertAlmostEqual(
            result.objective_value_cad,
            100.0 * (10.0 + 2.0 / DENSITY_ODT_M3),
            delta=1e-3,
        )

    def test_validator_catches_capacity_overflow(self) -> None:
        supplies = [_supply("SAW-A", "chips", 200.0)]
        demand = [_demand("PAP-1", "chips", 100.0)]
        links = [_link("SAW-A", "PAP-1")]
        result = solve_residue_allocation(supplies, demand, links)
        self.assertAlmostEqual(result.assigned_odt, 100.0, places=6)
        over_capacity = replace(
            result,
            allocations=(
                replace(result.allocations[0], odt=150.0),
            ),
        )
        validation = validate_stage_b(supplies, demand, links, over_capacity)
        failed = {check.check_id for check in validation.failed_checks}
        self.assertIn("recipient_capacity", failed)

    def test_moisture_limit_is_enforced(self) -> None:
        supplies = [_supply("SAW-A", "chips", 10.0, moisture=0.50)]
        demand = [_demand("PAP-1", "chips", 10.0)]
        links = [_link("SAW-A", "PAP-1")]
        result = solve_residue_allocation(
            supplies,
            demand,
            links,
            moisture_limit_wet_basis=0.40,
        )
        self.assertEqual(result.allocations, ())
        self.assertEqual(len(result.unassigned), 1)
        self.assertEqual(result.unassigned[0].reason, "moisture_limit")

    def test_assignment_volume_matches_odt(self) -> None:
        supplies = [_supply("SAW-A", "chips", 10.0)]
        demand = [_demand("PAP-1", "chips", 10.0)]
        links = [_link("SAW-A", "PAP-1")]
        result = solve_residue_allocation(supplies, demand, links)
        allocation = result.allocations[0]
        self.assertAlmostEqual(
            allocation.volume_m3, allocation.odt / DENSITY_ODT_M3, places=9
        )
        self.assertAlmostEqual(
            allocation.net_contribution_cad,
            allocation.product_revenue_cad
            - allocation.transport_cost_cad
            - allocation.processing_cost_cad,
            places=9,
        )


if __name__ == "__main__":
    unittest.main()
