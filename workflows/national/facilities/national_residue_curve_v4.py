"""Build the national residue supply-curve sensitivity v4.

Version 4 refreshes the parent facility residue supply from ``v3`` to ``v4``,
which uses the measured-coefficient ``national_product_chain_v2`` for BC, QC,
NB and YT.  The curve method, RSYC basis, OSeCAN context, and labels are
unchanged.  Process residue is aggregated by facility province; harvest
residue remains origin-province only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v4"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v4"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v2"
)
RSYC_SUMMARY = _base.RSYC_SUMMARY
OSECAN_DISPATCH = _base.OSECAN_DISPATCH
OSECAN_MANIFEST = _base.OSECAN_MANIFEST

ARTIFACT = "national_residue_curve_v4"
ARTIFACT_VERSION = "4.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v4"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V4.md"
FACILITY_ARTIFACT = "facility_residue_supply_v4"
FACILITY_LABEL = "facility_residue_supply_v4"
FACILITY_SCENARIO = "province_capacity_floor"
BASE_RECOVERY_RATIO_119M = 0.5857036666069207
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v4"
TEST_PATH = "tests/test_national_residue_curve_v4.py"

PROCESS_AVAILABILITY_CASES = _base.PROCESS_AVAILABILITY_CASES
HARVEST_RECOVERY_CASES = _base.HARVEST_RECOVERY_CASES
PROVINCES = _base.PROVINCES
CLASS_ORDER = _base.CLASS_ORDER
NationalResidueCurveV4Error = _base.NationalResidueCurveError

OVERRIDES: dict[str, Any] = {
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


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v4 curve while isolating the shared v1 module globals."""
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
