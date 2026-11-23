"""Build the trade-enabled national residue supply-curve sensitivity v5.

Version 5 trade refreshes the facility supply from the assignment-v5
``published_plus_proxy`` trade case with the measured total-residue
``national_product_chain_v3``.  The curve method, RSYC comparison, OSeCAN
context, and labels are unchanged from curve v5.  Process residue is aggregated
by facility province; harvest residue remains origin-province only, with
0.35/0.60/0.75 recovery cases and no calibrated single factor.

This is the trade counterpart of ``national_residue_curve_v5`` (assignment-v6
local floor).  National totals match; the facility-province process
distribution differs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import national_residue_curve as _base
from workflows.national.facilities import national_residue_curve_v4 as _v4

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_residue_curve_v5_trade"
)
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v5_trade"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
ARTIFACT = "national_residue_curve_v5_trade"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = (
    "National Residue Supply-Curve Sensitivity v5 Trade (Total Residue)"
)
REPORT_NAME = "NATIONAL_RESIDUE_CURVE_V5_TRADE.md"
FACILITY_ARTIFACT = "facility_residue_supply_v5_trade"
FACILITY_LABEL = "facility_residue_supply_v5_trade"
FACILITY_SCENARIO = "published_plus_proxy"
BASE_RECOVERY_RATIO_119M = 1.436727117924013
MODULE_PATH = (
    "workflows.national.facilities.national_residue_curve_v5_trade"
)
TEST_PATH = "tests/test_national_residue_curve_v5_trade.py"

SAVED_NAMES = tuple(_v4.OVERRIDES)

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


def _postprocess(summary: dict[str, Any], output_dir: Path) -> None:
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["trade_sensitivity"] = {
        "enabled": True,
        "facility_artifact": FACILITY_ARTIFACT,
        "facility_scenario": FACILITY_SCENARIO,
        "note": (
            "Process residue follows the assignment-v5 published-plus-proxy "
            "trade distribution; harvest residue is origin-province only."
        ),
    }
    summary["residue_components"] = "stem_plus_branch_foliage"
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
            "This curve uses the assignment-v5 published-plus-proxy trade "
            "sensitivity, not the assignment-v6 provincial-floor base. "
            "National totals match the local curve v5."
        ),
    ]
    summary["reproduce"] = f".venv/bin/python -m {MODULE_PATH}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text += (
            "\n## Trade sensitivity\n\n"
            "Process residue is aggregated by facility province from the "
            "assignment-v5 `published_plus_proxy` distribution. National "
            "process and harvest totals match the local curve v5; only the "
            "provincial process distribution differs. This is a screening "
            "counterfactual, not an interprovincial trade forecast.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["trade_sensitivity"] = summary["trade_sensitivity"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _base._sha256(path),
        }
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the trade curve while isolating shared module globals."""

    saved_v4 = {name: getattr(_v4, name) for name in SAVED_NAMES}
    saved_overrides = dict(_v4.OVERRIDES)
    try:
        for name, value in OVERRIDES.items():
            setattr(_v4, name, value)
        _v4.OVERRIDES.update(OVERRIDES)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v4.build(output_dir=target)
    finally:
        for name, value in saved_v4.items():
            setattr(_v4, name, value)
        _v4.OVERRIDES.clear()
        _v4.OVERRIDES.update(saved_overrides)
    _postprocess(summary, target)
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
