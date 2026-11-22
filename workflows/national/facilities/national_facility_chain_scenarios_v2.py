"""Build multi-scenario national facility product/residue chain v2.

Version 2 reruns the 100/119/150 Mm3 documented-ceiling assignment on capacity
v3 / assignment v5 ``published_plus_proxy`` instead of capacity v2 / assignment
v4.  Product-chain coefficients, class matching, local-first allocation, finite
capacity, and labels are unchanged.
"""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v5 as v5,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios as _base,
)

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v2"
)
CAPACITY_CSV = v5.CAPACITY_CSV
INVENTORY_CSV = v5.CAPACITY_CSV
BOUNDARY_GEOJSON = v5.BOUNDARY_GEOJSON
PRODUCT_CHAIN_DIR = _base.PRODUCT_CHAIN_DIR
ALLOCATION_DIR = _base.ALLOCATION_DIR
HARVEST_CASES = _base.HARVEST_CASES
SCENARIO_ORDER = _base.SCENARIO_ORDER
PRIMARY_SCENARIO = _base.PRIMARY_SCENARIO
CAPACITY_CASE = _base.CAPACITY_CASE
INTENSITY_CASES = _base.INTENSITY_CASES

ARTIFACT = "national_facility_chain_scenarios_v2"
ARTIFACT_VERSION = "2.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V2"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V2.md"
CAPACITY_LABEL = "inventory v1 / capacity v3"
ASSIGNMENT_LABEL = "assignment-v5"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v2"
TEST_PATH = "tests/test_national_facility_chain_scenarios_v2.py"
ScenarioChainError = _base.ScenarioChainError


def _read_facilities(_path: Path, _inventory: Path) -> tuple[Any, ...]:
    """Adapt the v5 single-path reader to the chain-scenario two-path call."""
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
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "v4": _V4_SHIM,
}


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v2 chain while isolating shared module globals."""
    saved = {name: getattr(_base, name) for name in BASE_OVERRIDES}
    try:
        for name, value in BASE_OVERRIDES.items():
            setattr(_base, name, value)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        return _base.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_base, name, value)


def main() -> None:
    import json

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
                        "proxy_share": value["proxy_share_of_assigned"],
                        "process_residue_pj": value["process_residue_pj"],
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
