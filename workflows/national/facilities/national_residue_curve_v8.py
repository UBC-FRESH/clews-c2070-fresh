"""Build the national residue supply-curve sensitivity v8.

Version 8 refreshes the facility supply from v7 to v8, which uses assignment
v10 node transport-aware facilities at the 119 Mm3 reference while keeping the
E1 measured + E2c product-chain v5 coefficients.  Process residue is aggregated
by facility province; harvest residue remains origin-province only.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve_v7 as _v7

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v8"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v8"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
ARTIFACT = "national_residue_curve_v8"
ARTIFACT_VERSION = "8.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v8 (Assignment v10)"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V8.md"
FACILITY_ARTIFACT = "facility_residue_supply_v8"
FACILITY_LABEL = "facility_residue_supply_v8 assignment v10"
FACILITY_SCENARIO = "node_transport_aware"
BASE_RECOVERY_RATIO_119M = _v7.BASE_RECOVERY_RATIO_119M
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v8"
TEST_PATH = "tests/test_national_facility_chain_v5.py"

_SAVED_NAMES = (
    "OUTPUT_DIR",
    "FACILITY_DIR",
    "PRODUCT_CHAIN_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "FACILITY_ARTIFACT",
    "FACILITY_LABEL",
    "FACILITY_SCENARIO",
    "BASE_RECOVERY_RATIO_119M",
    "MODULE_PATH",
    "TEST_PATH",
)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build curve v8 from the assignment-v10 facility residue chain."""

    saved = {name: getattr(_v7, name) for name in _SAVED_NAMES}
    try:
        for name in _SAVED_NAMES:
            setattr(_v7, name, globals()[name])
        summary = _v7.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v7, name, value)
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["residue_components"] = (
        "E1_measured_plus_E2c_density_calibrated_stem_branch_foliage"
    )
    summary["assignment"] = "assignment v10 node_transport_aware"
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = target / "manifest.json"
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
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
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
