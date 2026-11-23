"""Build facility-level residue supply v7 on the E1+E2c product chain.

Version 7 keeps the assignment-v6 ``province_capacity_floor`` distribution and
the v1 class-matching rule.  It refreshes the product-chain input from
``national_product_chain_v4`` (E2 v1 coefficients) to
``national_product_chain_v5`` (E1 measured + E2c density-calibrated
coefficients).

Process residue is facility-assigned; harvest residue remains province-only.
All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply_v6 as _v6

REPO_ROOT = _v6.REPO_ROOT
DATA_ROOT = _v6.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v7"
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
ARTIFACT = "facility_residue_supply_v7"
ARTIFACT_VERSION = "7.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v7 (E1+E2c Coefficients)"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V7.md"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v7"
TEST_PATH = "tests/test_facility_residue_supply_v7.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v5"
PRODUCT_CHAIN_LABEL = (
    "national_product_chain_v5 E1 measured + E2c density-calibrated "
    "coefficients"
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
    """Build supply v7 from the E1/E2c product chain."""

    saved = {name: getattr(_v6, name) for name in _SAVED_NAMES}
    try:
        _v6.OUTPUT_DIR = OUTPUT_DIR
        _v6.PRODUCT_CHAIN_DIR = PRODUCT_CHAIN_DIR
        _v6.ARTIFACT = ARTIFACT
        _v6.ARTIFACT_VERSION = ARTIFACT_VERSION
        _v6.ARTIFACT_TITLE = ARTIFACT_TITLE
        _v6.REPORT_NAME = REPORT_NAME
        _v6.MODULE_PATH = MODULE_PATH
        _v6.TEST_PATH = TEST_PATH
        _v6.PRODUCT_CHAIN_ARTIFACT = PRODUCT_CHAIN_ARTIFACT
        _v6.PRODUCT_CHAIN_LABEL = PRODUCT_CHAIN_LABEL
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v6.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v6, name, value)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["product_chain"] = {
        "artifact": PRODUCT_CHAIN_ARTIFACT,
        "label": PRODUCT_CHAIN_LABEL,
        "measured_provinces": ["BC", "NB", "QC", "YT"],
        "density_calibrated_provinces": [
            "AB",
            "MB",
            "NL",
            "NS",
            "NT",
            "NU",
            "ON",
            "PE",
            "SK",
        ],
        "donor_product_provinces": [],
    }
    summary["residue_components"] = "E1_measured_plus_E2c_density_calibrated"
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["density_calibration"] = {
        "applies_to": "E2c product coefficients",
        "changes": "stems/ha expansion and expanded reference volume only",
        "per_m3_coefficients_unchanged_from_e2_v2": True,
    }
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2c density calibration is extensive and does not change per-m3 "
            "product or residue coefficients; facility residues reflect the "
            "E2 v1 to E2c coefficient change at fixed harvest."
        )
    ]
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = target / "manifest.json"
    if manifest_path.is_file():
        import hashlib

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
        manifest["outputs"] = {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(target.iterdir())
            if path.is_file() and path.name != "manifest.json"
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    report_path = target / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 6", "Version 7").replace(
            "E1+E2 Coefficients", "E1+E2c Coefficients"
        ).replace("E2 imputed", "E2c density-calibrated")
        report_path.write_text(text, encoding="utf-8")
    return summary


def main() -> None:
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
