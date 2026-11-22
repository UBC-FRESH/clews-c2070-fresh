"""Build multi-scenario national facility product/residue chain v3.

Version 3 reruns the 100/119/150 Mm3 documented-ceiling assignment on the
capacity-v3 provincial floor used by assignment v6.  For every harvest case the
1.2x provincial floor is recomputed from that case's harvest; proxy capacities
only are scaled, published capacity is never altered.  Product-chain
coefficients, class matching, local-first allocation, finite capacity, and
labels are unchanged.
"""

from __future__ import annotations

import json
import math
import types
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v3 as a3,
)
from workflows.national.facilities import (
    national_facility_assignment_v5 as v5,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios as _base,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
)

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v3"
)
CAPACITY_CSV = v5.CAPACITY_CSV
INVENTORY_CSV = v5.CAPACITY_CSV
BOUNDARY_GEOJSON = v5.BOUNDARY_GEOJSON
PRODUCT_CHAIN_DIR = _base.PRODUCT_CHAIN_DIR
ALLOCATION_DIR = _base.ALLOCATION_DIR
HARVEST_CASES = _base.HARVEST_CASES
SCENARIO_ORDER = _base.SCENARIO_ORDER
PRIMARY_SCENARIO = _base.PRIMARY_SCENARIO
CAPACITY_CASE = "province_capacity_floor"
INTENSITY_CASES = _base.INTENSITY_CASES

ARTIFACT = "national_facility_chain_scenarios_v3"
ARTIFACT_VERSION = "3.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V3"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V3.md"
CAPACITY_LABEL = "inventory v1 / capacity v3 with 1.2x provincial floor"
ASSIGNMENT_LABEL = "assignment-v6"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v3"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v3.py"
ScenarioChainError = _base.ScenarioChainError


def _read_facilities(_path: Path, _inventory: Path) -> tuple[Any, ...]:
    """Adapt the v5 single-path reader to the two-path call signature."""
    return v5.read_facilities(v5.CAPACITY_CSV)


_V4_SHIM = types.SimpleNamespace(
    read_facilities=_read_facilities,
    PUBLISHED_EVIDENCE=v5.PUBLISHED_EVIDENCE,
    CAPACITY_CSV=CAPACITY_CSV,
    INVENTORY_CSV=INVENTORY_CSV,
    BOUNDARY_GEOJSON=BOUNDARY_GEOJSON,
)

BASE_OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "CAPACITY_CSV": CAPACITY_CSV,
    "INVENTORY_CSV": INVENTORY_CSV,
    "BOUNDARY_GEOJSON": BOUNDARY_GEOJSON,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CAPACITY_LABEL": CAPACITY_LABEL,
    "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
    "CAPACITY_CASE": CAPACITY_CASE,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "v4": _V4_SHIM,
}
ORIGINAL_CAPACITY_BASIS = _base.capacity_v2_basis


def _capacity_basis_with_floor(
    facilities: tuple[Any, ...],
) -> dict[str, Any]:
    basis = ORIGINAL_CAPACITY_BASIS(facilities)
    basis["capacity_layer"] = "national_facility_capacity_v3"
    basis["floor_multiplier"] = a3.FLOOR_MULTIPLIER
    basis["floor_applied_per_harvest_case"] = True
    return basis


BASE_OVERRIDES["capacity_v2_basis"] = _capacity_basis_with_floor


def _build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build the v3 chain with a per-case provincial floor and return summary."""

    _base.assert_no_unexpected_deletions()
    generated_utc = _base._utc_now()
    cases = _base.harvest_cases()
    references, product_chain_summary = _base.read_reference_chains()
    lhv_gj_per_odt, lhv_source = _base.read_lhv()
    intensity_note = _base.read_intensity_note()
    base_facilities = v5.read_facilities(CAPACITY_CSV)
    if not _base.eligible_facilities(base_facilities):
        raise ScenarioChainError(
            f"no eligible facilities under {CAPACITY_LABEL}"
        )

    results = {}
    chains_by_scenario = {}
    assignments_by_scenario = {}
    allocation_rows: list[dict[str, Any]] = []
    product_rows: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    offer_totals: dict[str, float] = {}
    floor_plans: dict[str, dict[str, Any]] = {}
    centroid_methods: dict[str, str] = {}
    config = AllocationConfig(
        road_factor=a3.ROAD_FACTOR,
        cross_border_penalty_km=a3.CROSS_BORDER_PENALTY_KM,
    )
    for case in cases:
        harvest = _base.read_harvest_case(case)
        offers, centroid_methods = _base.read_offers(harvest)
        floor_plan = a3.build_floor_plan(base_facilities, harvest)
        facilities = floor_plan.facilities
        result = a3.allocate_scenario(
            offers,
            facilities,
            CAPACITY_CASE,
            config,
        )
        links = _base._assignment_links(result)
        chains = _base.build_scenario_chains(references, harvest)
        allocations, fallback_pools = _base.allocate_class_feedstock(chains, links)
        results[case.scenario] = result
        chains_by_scenario[case.scenario] = chains
        assignments_by_scenario[case.scenario] = links
        allocation_rows.extend(_base._scenario_assignment_rows(case, result))
        product_rows.extend(
            _base._product_residue_rows(
                case,
                chains,
                allocations,
                lhv_gj_per_odt,
            )
        )
        summaries[case.scenario] = _base._scenario_summary_row(
            case,
            result,
            chains,
            facilities,
            links,
            fallback_pools,
            lhv_gj_per_odt,
        )
        summaries[case.scenario]["floor_proxy_records_scaled"] = (
            floor_plan.scaled_proxy_records
        )
        summaries[case.scenario]["floor_shortfall_after_m3_y"] = (
            floor_plan.shortfall_after_m3_y
        )
        offer_totals[case.scenario] = math.fsum(harvest.values())
        floor_plans[case.scenario] = {
            "multiplier": a3.FLOOR_MULTIPLIER,
            "scaled_provinces": list(floor_plan.scaled_provinces),
            "scaled_proxy_records": floor_plan.scaled_proxy_records,
            "shortfall_before_m3_y": floor_plan.shortfall_before_m3_y,
            "shortfall_after_m3_y": floor_plan.shortfall_after_m3_y,
            "scale_by_province": floor_plan.scale_by_province,
            "province_rows": list(floor_plan.province_rows),
        }

    comparison_rows = _base._comparison_rows(summaries)
    checks = _base._validation_checks(
        summaries,
        allocation_rows,
        product_rows,
        chains_by_scenario,
        intensity_note,
    )
    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise ScenarioChainError(
            "scenario validation failed: "
            + " | ".join(check["name"] for check in failed)
        )

    inputs = {
        str(path.relative_to(REPO_ROOT)): _base._sha256(path)
        for path in (
            CAPACITY_CSV,
            INVENTORY_CSV,
            BOUNDARY_GEOJSON,
            _base.PRODUCT_CHAIN_CSV,
            _base.PRODUCT_CHAIN_SUMMARY,
            _base.RSYC_SUMMARY,
            *(case.path for case in cases),
            *(
                _base.ALLOCATION_DIR
                / f"intensity_{intensity}"
                / "provincial_allocation_summary.json"
                for intensity in INTENSITY_CASES
            ),
        )
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": _base.EVIDENCE_LABEL,
            "observed": _base.OBSERVED,
            "policy_grade": _base.POLICY_GRADE,
            "facility_assignment": _base.FACILITY_ASSIGNMENT,
        },
        "scenario_order": list(SCENARIO_ORDER),
        "primary_scenario": PRIMARY_SCENARIO,
        "capacity_case": CAPACITY_CASE,
        "scenarios": summaries,
        "comparison": comparison_rows,
        "offer_totals_m3": offer_totals,
        "centroid_methods": centroid_methods,
        "capacity_basis": {
            **ORIGINAL_CAPACITY_BASIS(base_facilities),
            "capacity_layer": "national_facility_capacity_v3",
            "floor_multiplier": a3.FLOOR_MULTIPLIER,
            "floor_applied_per_harvest_case": True,
            "no_unbounded_sink": True,
        },
        "floor_plans": floor_plans,
        "product_chain": {
            "artifact": product_chain_summary.get("artifact"),
            "reference_harvest_m3": 118_977_339.1,
            "conversion": (
                "product_chain_v1 effective province/class product and "
                "process-residue ratios, scaled by province harvest ratio"
            ),
            "harvest_residue_scope": "province_only",
            "process_residue_scope": "facility_assigned",
        },
        "lhv": {
            "value_gj_per_odt": lhv_gj_per_odt,
            "source": lhv_source,
        },
        "intensity_note": intensity_note,
        "validation": {
            "passed": not failed,
            "failed_check_count": len(failed),
            "checks": checks,
        },
        "caveats": [
            (
                "All values are model estimates; `observed=false` and "
                "`policy_grade=false`."
            ),
            (
                "The 100/119/150 Mm3 cases are planning allocations, not legal "
                "AACs, sustainable-supply determinations, or observed harvest "
                "outcomes."
            ),
            (
                "The 1.2x provincial floor is recomputed from each case's "
                "harvest. Proxy capacities only are scaled; published capacity "
                "is never altered, and scaled proxies are not observed capacity."
            ),
            (
                "Capacity v3 mixes published mill records and class-median "
                "proxy capacity. The bases are not interchangeable."
            ),
            (
                "The allocation is local first; cross-border volume is only the "
                "residual that local finite capacity cannot serve."
            ),
            (
                "Product and residue quantities apply product-chain-v1 "
                "province/class coefficients to assigned class feedstock. They "
                "are not measured mill output."
            ),
            (
                "Process residue is facility-assigned and separate from "
                "provincial harvest residue; harvest residue is never assigned "
                "to a facility."
            ),
            (
                "The 150m documented ceiling is not the 1.0 or 1.2 m3/ha/yr "
                "operable-proxy intensity screen. Both intensity screens are "
                "infeasible at 150 Mm3 and are reported separately."
            ),
            "No UI or public OSeCAN surface is changed by this artifact.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
        "inputs": inputs,
        "method": {
            "assignment": (
                f"{ASSIGNMENT_LABEL} province_capacity_floor, local-first, "
                "finite capacity, 500 km cross-border penalty"
            ),
            "product_residue": (
                "product-chain-v1 effective province/class ratios applied to "
                "assigned class feedstock"
            ),
            "harvest_residue": "province-only; never facility-assigned",
            "labels": "model_estimate / observed=false / policy_grade=false",
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _base.v2._write_csv(
        output_dir / "scenario_assignment.csv",
        _base.SCENARIO_ASSIGNMENT_FIELDS,
        allocation_rows,
    )
    _base.v2._write_csv(
        output_dir / "scenario_product_residue.csv",
        _base.SCENARIO_PRODUCT_RESIDUE_FIELDS,
        product_rows,
    )
    _base.v2._write_csv(
        output_dir / "scenario_summary.csv",
        _base.SCENARIO_SUMMARY_FIELDS,
        _base._summary_csv_rows(summaries),
    )
    _base.v2._write_csv(
        output_dir / "scenario_comparison.csv",
        _base.SCENARIO_COMPARISON_FIELDS,
        comparison_rows,
    )
    report = _base._report(summary, summaries).replace(
        "### Capacity v2 basis",
        "### Capacity v3 basis and provincial floor",
    )
    (output_dir / REPORT_NAME).write_text(report, encoding="utf-8")
    _base.v2._write_json(output_dir / "summary.json", summary)
    _base._write_manifest(output_dir, summary)
    return summary


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build v3 while isolating the shared base module globals."""
    saved = {name: getattr(_base, name) for name in BASE_OVERRIDES}
    try:
        for name, value in BASE_OVERRIDES.items():
            setattr(_base, name, value)
        return _build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "validation": summary["validation"]["passed"],
                "scenarios": {
                    key: {
                        "assigned_m3": value["assigned_m3"],
                        "cross_border_m3": value["cross_border_m3"],
                        "proxy_share": value["proxy_share_of_assigned"],
                        "process_residue_pj": value["process_residue_pj"],
                        "floor_proxy_records_scaled": value[
                            "floor_proxy_records_scaled"
                        ],
                    }
                    for key, value in summary["scenarios"].items()
                },
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
