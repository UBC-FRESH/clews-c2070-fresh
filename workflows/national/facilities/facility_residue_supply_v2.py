"""Build facility-level residue supply v2 on assignment v5.

Version 2 refreshes the v1 integration from assignment v3
``province_capacity_floor`` to assignment v5 ``published_plus_proxy`` (capacity
v3).  The product-chain coefficients, RSYC basis, class-matching rule, local
first pool, national class fallback, and label contract are unchanged.  Only
the assignment input and therefore the facility distribution of process residue
change.

All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v2"
PRODUCT_CHAIN_DIR = _base.PRODUCT_CHAIN_DIR
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v5"
)
RSYC_SUMMARY = _base.RSYC_SUMMARY

ARTIFACT = "facility_residue_supply_v2"
ARTIFACT_VERSION = "2.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v2"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V2.md"
ASSIGNMENT_VERSION = "v5"
ASSIGNMENT_LABEL = "assignment v5"
ASSIGNMENT_ARTIFACT = "national_facility_assignment_v5"
ASSIGNMENT_SUMMARY_KEY = "assignment_v5"
ALLOCATION_BASIS = "assignment_v5_published_plus_proxy_assigned_m3"
UTILIZATION_FIELD = "assignment_utilization_v5"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v2"
TEST_PATH = "tests/test_facility_residue_supply_v2.py"
SCENARIO = "published_plus_proxy"
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
FacilityResidueSupplyError = _base.FacilityResidueSupplyError

OVERRIDES: dict[str, Any] = {
    "OUTPUT_DIR": OUTPUT_DIR,
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
    """Build the v2 artifact while isolating the shared v1 module globals."""
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
                "proxy_residue_share": summary["concentration"][
                    "proxy_residue_share"
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
