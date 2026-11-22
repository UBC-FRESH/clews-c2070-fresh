"""Build the all-zone forestry residue handoff to OSeCAN v3.

Version 3 keeps the v2 province-to-zone proxy weights and allocation hierarchy
but refreshes the province/case totals from ``national_residue_curve_v2``,
which is built on ``facility_residue_supply_v2`` / assignment v5
``published_plus_proxy``.  The official OSeCAN tree remains read-only and is
hash-checked before and after the build.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff as v1
from workflows.national.facilities import forestry_osecan_handoff_v2 as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v3"
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v2"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"

ARTIFACT = "forestry_osecan_handoff_v3"
ARTIFACT_VERSION = "3.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v3"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V3.md"
CURVE_LABEL = "national_residue_curve_v2"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v3"
TEST_PATH = "tests/test_forestry_osecan_handoff_v3.py"
VERSION_NOTE = (
    "Version 3 preserves the all-zone v2 proxy weights and refreshes the "
    "province/case totals from the assignment-v5 residue curve."
)

PROVINCES = _base.PROVINCES
PROVINCE_ORDER = _base.PROVINCE_ORDER
ZONE_ORDER = _base.ZONE_ORDER
PROCESS_STREAM = _base.PROCESS_STREAM
HARVEST_STREAM = _base.HARVEST_STREAM
PROCESS_CASES = _base.PROCESS_CASES
HARVEST_CASES = _base.HARVEST_CASES
MULTI_ZONE_PROVINCES = _base.MULTI_ZONE_PROVINCES
SINGLE_ZONE_PROVINCES = _base.SINGLE_ZONE_PROVINCES
ForestryOsecanHandoffV3Error = _base.ForestryOsecanHandoffV2Error

BASE_OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "PROVINCE_CURVE": PROVINCE_CURVE,
    "NATIONAL_CURVE": NATIONAL_CURVE,
    "CURVE_SUMMARY": CURVE_SUMMARY,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "CURVE_LABEL": CURVE_LABEL,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "VERSION_NOTE": VERSION_NOTE,
}
V1_OVERRIDES: dict[str, Any] = {
    "PROVINCE_CURVE": PROVINCE_CURVE,
    "NATIONAL_CURVE": NATIONAL_CURVE,
    "CURVE_SUMMARY": CURVE_SUMMARY,
    "CURVE_DIR": CURVE_DIR,
}


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v3 handoff while isolating shared module globals."""
    saved_base = {name: getattr(_base, name) for name in BASE_OVERRIDES}
    saved_v1 = {name: getattr(v1, name) for name in V1_OVERRIDES}
    try:
        for name, value in BASE_OVERRIDES.items():
            setattr(_base, name, value)
        for name, value in V1_OVERRIDES.items():
            setattr(v1, name, value)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        return _base.build(output_dir=target)
    finally:
        for name, value in saved_base.items():
            setattr(_base, name, value)
        for name, value in saved_v1.items():
            setattr(v1, name, value)


def main() -> None:
    import json

    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "validation": summary["validation"]["passed"],
                "handoff_totals": summary["handoff_totals"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
