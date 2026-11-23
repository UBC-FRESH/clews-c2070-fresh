"""Build the national residue supply-curve sensitivity v7.

Version 7 refreshes the facility supply from v6 to v7, which uses
``national_product_chain_v5`` E1 measured + E2c density-calibrated
coefficients for all 13 jurisdictions.  The curve method, 0.35/0.60/0.75
recovery cases, RSYC comparison, OSeCAN context and labels are unchanged.

Process residue is aggregated by facility province; harvest residue remains
origin-province only.  No calibrated recovery factor is introduced.  All rows
are model estimates with ``observed=false`` and ``policy_grade=false``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve_v6 as _v6

REPO_ROOT = _v6.REPO_ROOT
DATA_ROOT = _v6.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v7"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v7"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
ARTIFACT = "national_residue_curve_v7"
ARTIFACT_VERSION = "7.0.0"
ARTIFACT_TITLE = "National Residue Supply-Curve Sensitivity v7 (E1+E2c)"
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V7.md"
FACILITY_ARTIFACT = "facility_residue_supply_v7"
FACILITY_LABEL = "facility_residue_supply_v7"
FACILITY_SCENARIO = "province_capacity_floor"
BASE_RECOVERY_RATIO_119M = 2.035762111673332
MODULE_PATH = "workflows.national.facilities.national_residue_curve_v7"
TEST_PATH = "tests/test_national_residue_curve_v7.py"

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
    """Build curve v7 from the E1/E2c facility residue chain."""

    saved = {name: getattr(_v6, name) for name in _SAVED_NAMES}
    try:
        for name in _SAVED_NAMES:
            setattr(_v6, name, globals()[name])
        summary = _v6.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v6, name, value)
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["residue_components"] = (
        "E1_measured_plus_E2c_density_calibrated_stem_branch_foliage"
    )
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2c density calibration changes stems/ha expansion, not per-m3 "
            "product or residue coefficients; the v7 curve differs from v6 "
            "only through the E2 v1 to E2c coefficient change and the fixed "
            "100/119/150 Mm3 harvest basis."
        )
    ]
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = target / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 6", "Version 7").replace(
            "E1+E2", "E1+E2c"
        ).replace("E2 imputed", "E2c density-calibrated")
        report_path.write_text(text, encoding="utf-8")
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
    return summary


def main() -> None:
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
