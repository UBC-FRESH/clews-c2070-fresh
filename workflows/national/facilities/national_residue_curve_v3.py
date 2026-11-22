"""Build the national residue supply-curve sensitivity v3.

Version 3 replaces ``facility_residue_supply_v2`` (assignment v5
``published_plus_proxy``) with ``facility_residue_supply_v3`` (assignment v6
``province_capacity_floor``).  The curve method, RSYC basis, OSeCAN context,
and labels are unchanged.  Process residue is aggregated by facility province;
harvest residue remains origin-province only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v3"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v3"
)
PRODUCT_CHAIN_DIR = _base.PRODUCT_CHAIN_DIR
RSYC_SUMMARY = _base.RSYC_SUMMARY
OSECAN_DISPATCH = _base.OSECAN_DISPATCH
OSECAN_MANIFEST = _base.OSECAN_MANIFEST

ARTIFACT = "national_residue_curve_v3"
ARTIFACT_VERSION = "3.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v3"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V3.md"
FACILITY_ARTIFACT = "facility_residue_supply_v3"
FACILITY_LABEL = "facility_residue_supply_v3"
FACILITY_SCENARIO = "province_capacity_floor"
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v3"
TEST_PATH = "tests/test_national_residue_curve_v3.py"

PROCESS_AVAILABILITY_CASES = _base.PROCESS_AVAILABILITY_CASES
HARVEST_RECOVERY_CASES = _base.HARVEST_RECOVERY_CASES
PROVINCES = _base.PROVINCES
CLASS_ORDER = _base.CLASS_ORDER
NationalResidueCurveError = _base.NationalResidueCurveError

OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "FACILITY_DIR": FACILITY_DIR,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "FACILITY_ARTIFACT": FACILITY_ARTIFACT,
    "FACILITY_LABEL": FACILITY_LABEL,
    "FACILITY_SCENARIO": FACILITY_SCENARIO,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
}


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v3 curve while isolating the shared v1 module globals."""
    saved = {name: getattr(_base, name) for name in OVERRIDES}
    try:
        for name, value in OVERRIDES.items():
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
                "process_residue_pj": summary["scenario_totals"]["process"][
                    "process_availability_050"
                ]["residue_pj"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
