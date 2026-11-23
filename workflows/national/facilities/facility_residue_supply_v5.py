"""Build facility-level residue supply v5 on the measured total-residue chain.

Version 5 keeps the assignment-v6 ``province_capacity_floor`` distribution and
the v1 class-matching rule.  It refreshes the product-chain input from v2 to
v3, so BC/QC/NB/YT harvest residue is the measured stem + branch + foliage
total while products and process residue are unchanged.

Process residue is facility-assigned; harvest residue remains province-only.
All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v5"
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
ARTIFACT = "facility_residue_supply_v5"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v5 (Total Residue)"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V5.md"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v5"
TEST_PATH = "tests/test_facility_residue_supply_v5.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v3"
PRODUCT_CHAIN_LABEL = (
    "national_product_chain_v3 measured total harvest residue"
)

_SAVED_NAMES = (
    "OUTPUT_DIR",
    "PRODUCT_CHAIN_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
    "PRODUCT_CHAIN_ARTIFACT",
    "PRODUCT_CHAIN_LABEL",
)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build supply v5 while isolating shared v4 module state."""

    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    saved_overrides = dict(_v4.OVERRIDES)
    try:
        _v4.OUTPUT_DIR = OUTPUT_DIR
        _v4.PRODUCT_CHAIN_DIR = PRODUCT_CHAIN_DIR
        _v4.ARTIFACT = ARTIFACT
        _v4.ARTIFACT_VERSION = ARTIFACT_VERSION
        _v4.ARTIFACT_TITLE = ARTIFACT_TITLE
        _v4.REPORT_NAME = REPORT_NAME
        _v4.MODULE_PATH = MODULE_PATH
        _v4.TEST_PATH = TEST_PATH
        _v4.PRODUCT_CHAIN_ARTIFACT = PRODUCT_CHAIN_ARTIFACT
        _v4.PRODUCT_CHAIN_LABEL = PRODUCT_CHAIN_LABEL
        _v4.OVERRIDES.update(
            {
                "OUTPUT_DIR": OUTPUT_DIR,
                "PRODUCT_CHAIN_DIR": PRODUCT_CHAIN_DIR,
                "PRODUCT_CHAIN_ARTIFACT": PRODUCT_CHAIN_ARTIFACT,
                "PRODUCT_CHAIN_LABEL": PRODUCT_CHAIN_LABEL,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
            }
        )
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v4.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v4, name, value)
        _v4.OVERRIDES.clear()
        _v4.OVERRIDES.update(saved_overrides)
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
                "scenario": summary["scenario"],
                "process_residue_odt": summary["national_totals"][
                    "process_residue_odt"
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
