"""Build the national residue supply-curve sensitivity v5.

Version 5 refreshes the facility supply from v4 to v5, which uses the measured
total-residue ``national_product_chain_v3`` for BC, QC, NB and YT.  The curve
method, RSYC comparison, OSeCAN context, and labels are unchanged.  Process
residue is aggregated by facility province; harvest residue remains
origin-province only, with 0.35/0.60/0.75 recovery cases and no calibrated
single factor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve as _base
from workflows.national.facilities import national_residue_curve_v4 as _v4

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v5"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v5"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
ARTIFACT = "national_residue_curve_v5"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v5 (Total Residue)"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V5.md"
FACILITY_ARTIFACT = "facility_residue_supply_v5"
FACILITY_LABEL = "facility_residue_supply_v5"
FACILITY_SCENARIO = "province_capacity_floor"
BASE_RECOVERY_RATIO_119M = 1.436727117924013
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v5"
TEST_PATH = "tests/test_national_residue_curve_v5.py"

SAVED_NAMES = tuple(_v4.OVERRIDES)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v5 curve while isolating the shared v1/v4 module globals."""

    saved_v4 = {name: getattr(_v4, name) for name in SAVED_NAMES}
    saved_overrides = dict(_v4.OVERRIDES)
    try:
        _v4.OUTPUT_DIR = OUTPUT_DIR
        _v4.FACILITY_DIR = FACILITY_DIR
        _v4.PRODUCT_CHAIN_DIR = PRODUCT_CHAIN_DIR
        _v4.ARTIFACT = ARTIFACT
        _v4.ARTIFACT_VERSION = ARTIFACT_VERSION
        _v4.ARTIFACT_TITLE = ARTIFACT_TITLE
        _v4.REPORT_NAME = REPORT_NAME
        _v4.FACILITY_ARTIFACT = FACILITY_ARTIFACT
        _v4.FACILITY_LABEL = FACILITY_LABEL
        _v4.FACILITY_SCENARIO = FACILITY_SCENARIO
        _v4.BASE_RECOVERY_RATIO_119M = BASE_RECOVERY_RATIO_119M
        _v4.MODULE_PATH = MODULE_PATH
        _v4.TEST_PATH = TEST_PATH
        _v4.OVERRIDES.update(
            {
                "OUTPUT_DIR": OUTPUT_DIR,
                "FACILITY_DIR": FACILITY_DIR,
                "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "FACILITY_ARTIFACT": FACILITY_ARTIFACT,
                "FACILITY_LABEL": FACILITY_LABEL,
                "FACILITY_SCENARIO": FACILITY_SCENARIO,
                "BASE_RECOVERY_RATIO_119M": BASE_RECOVERY_RATIO_119M,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
            }
        )
        summary = _v4.build(output_dir=output_dir)
    finally:
        for name, value in saved_v4.items():
            setattr(_v4, name, value)
        _v4.OVERRIDES.clear()
        _v4.OVERRIDES.update(saved_overrides)
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["residue_components"] = "stem_plus_branch_foliage"
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    return summary


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
                "harvest_060_pj": summary["rsyc_comparison"]["harvest_cases"][
                    "harvest_recovery_060"
                ]["recoverable_pj"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
