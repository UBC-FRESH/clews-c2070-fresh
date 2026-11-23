"""Build facility-level residue supply v4 on the measured product chain v2.

Version 4 keeps the assignment-v6 ``province_capacity_floor`` distribution and
the v1 class-matching rule.  It refreshes the product-chain input from
``national_product_chain_v1`` to ``national_product_chain_v2``, which replaces
the BC/QC/NB/YT product coefficients with measured bucking-pilot shares.  The
other nine jurisdictions keep the donor coefficients.

All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v4"
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v2"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v6"
)
RSYC_SUMMARY = _base.RSYC_SUMMARY

ARTIFACT = "facility_residue_supply_v4"
ARTIFACT_VERSION = "4.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v4 (Measured Coefficients)"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V4.md"
ASSIGNMENT_VERSION = "v6"
ASSIGNMENT_LABEL = "assignment v6"
ASSIGNMENT_ARTIFACT = "national_facility_assignment_v6"
ASSIGNMENT_SUMMARY_KEY = "assignment_v6"
ALLOCATION_BASIS = "assignment_v6_province_capacity_floor_assigned_m3"
UTILIZATION_FIELD = "assignment_utilization_v6"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v4"
TEST_PATH = "tests/test_facility_residue_supply_v4.py"
SCENARIO = "province_capacity_floor"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v2"
PRODUCT_CHAIN_LABEL = "national_product_chain_v2 measured four-province coefficients"

PJ_GJ = _base.PJ_GJ
PROVINCES = _base.PROVINCES
CLASS_ORDER = _base.CLASS_ORDER
CLASS_FEEDSTOCK_COLUMN = _base.CLASS_FEEDSTOCK_COLUMN
CLASS_PRODUCT_COLUMN = _base.CLASS_PRODUCT_COLUMN
CLASS_RESIDUE_COLUMN = _base.CLASS_RESIDUE_COLUMN
CAPACITY_STATUSES = _base.CAPACITY_STATUSES
PUBLISHED = _base.PUBLISHED
PROXY = _base.PROXY
SCALED_PROXY = _base.SCALED_PROXY
EVIDENCE_LABEL = _base.EVIDENCE_LABEL
LABEL_TRUE = _base.LABEL_TRUE
LABEL_FALSE = _base.LABEL_FALSE
FacilityResidueSupplyV4Error = _base.FacilityResidueSupplyError

OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
    "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
    "PRODUCT_CHAIN_ARTIFACT": PRODUCT_CHAIN_ARTIFACT,
    "PRODUCT_CHAIN_LABEL": PRODUCT_CHAIN_LABEL,
    "ASSIGNMENT_DIR": ASSIGNMENT_DIR,
    "ARTIFACT": ARTIFACT,
    "ARTIFACT_VERSION": ARTIFACT_VERSION,
    "ARTIFACT_TITLE": ARTIFACT_TITLE,
    "REPORT_NAME": REPORT_NAME,
    "ASSIGNMENT_VERSION": ASSIGNMENT_VERSION,
    "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
    "ASSIGNMENT_ARTIFACT": ASSIGNMENT_ARTIFACT,
    "ASSIGNMENT_SUMMARY_KEY": ASSIGNMENT_SUMMARY_KEY,
    "ALLOCATION_BASIS": ALLOCATION_BASIS,
    "UTILIZATION_FIELD": UTILIZATION_FIELD,
    "UTILIZATION_MODE": "cumulative",
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "SCENARIO": SCENARIO,
    "CAPACITY_EVIDENCE_ALLOWED": {
        **_base.CAPACITY_EVIDENCE_ALLOWED,
        _base.PUBLISHED: frozenset(
            {
                "published_mills_db_2015_infeed_proxy",
                "published_capacity_converted",
                "published_authorized_consumption",
                "published_sawn_output_converted",
            }
        ),
    },
}


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v4 artifact while isolating the shared v1 module globals."""
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
                "scenario": summary["scenario"],
                "process_residue_odt": summary["national_totals"][
                    "process_residue_odt"
                ],
                "process_residue_pj": summary["national_totals"][
                    "process_residue_pj"
                ],
                "validation_failures": summary["validation"][
                    "failed_check_count"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
