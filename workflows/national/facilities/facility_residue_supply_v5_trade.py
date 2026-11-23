"""Build facility-level residue supply v5 trade on measured total residue.

This variant keeps the assignment-v5 ``published_plus_proxy`` facility
distribution (the trade-permissive sensitivity) and the v1 class-matching rule,
and consumes the measured total-residue ``national_product_chain_v3``.  It is
the trade counterpart of ``facility_residue_supply_v5``; national process
residue and harvest residue match, while the facility province distribution
differs with cross-border feedstock.

Process residue is facility-assigned; harvest residue remains province-only.
All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v5_trade"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v3"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v5"
)
ARTIFACT = "facility_residue_supply_v5_trade"
ARTIFACT_VERSION = "5.0.0"
ARTIFACT_TITLE = (
    "National Facility Residue Supply v5 Trade (Measured Total Residue)"
)
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V5_TRADE.md"
MODULE_PATH = (
    "workflows.national.facilities.facility_residue_supply_v5_trade"
)
TEST_PATH = "tests/test_facility_residue_supply_v5_trade.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v3"
PRODUCT_CHAIN_LABEL = (
    "national_product_chain_v3 measured total harvest residue"
)
ASSIGNMENT_VERSION = "v5"
ASSIGNMENT_LABEL = "assignment v5"
ASSIGNMENT_ARTIFACT = "national_facility_assignment_v5"
ASSIGNMENT_SUMMARY_KEY = "assignment_v5"
ALLOCATION_BASIS = "assignment_v5_published_plus_proxy_assigned_m3"
UTILIZATION_FIELD = "assignment_utilization_v5"
UTILIZATION_MODE = "cumulative"
SCENARIO = "published_plus_proxy"

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
    "UTILIZATION_MODE": UTILIZATION_MODE,
    "MODULE_PATH": MODULE_PATH,
    "TEST_PATH": TEST_PATH,
    "SCENARIO": SCENARIO,
}

_SAVED_NAMES = tuple(name for name in OVERRIDES if hasattr(_v4, name))


def _postprocess(summary: dict[str, Any], output_dir: Path) -> None:
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["trade_sensitivity"] = {
        "enabled": True,
        "scenario": SCENARIO,
        "assignment_artifact": ASSIGNMENT_ARTIFACT,
        "note": (
            "Facility process residue follows the trade-permissive assignment "
            "v5 distribution; national totals match the local chain v5."
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
            "This artifact uses the assignment-v5 published-plus-proxy trade "
            "sensitivity, not the assignment-v6 provincial-floor base. "
            "Cross-border volume is a screening counterfactual, not a trade "
            "forecast."
        ),
    ]
    summary["reproduce"] = f".venv/bin/python -m {MODULE_PATH}"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text += (
            "\n## Trade sensitivity\n\n"
            "Facility process residue is computed from the assignment-v5 "
            "`published_plus_proxy` distribution. National process residue and "
            "provincial harvest residue match the local chain v5; the "
            "facility-province process distribution differs. This is a "
            "screening counterfactual, not an interprovincial trade forecast.\n"
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
    manifest["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "facility_assignment": True,
        "ui_or_public_osecan_changed": False,
    }
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _v4._base._sha256(path),
        }
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest["trade_sensitivity"] = summary["trade_sensitivity"]
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the trade facility residue while isolating shared globals."""

    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    saved_overrides = dict(_v4.OVERRIDES)
    try:
        for name in _SAVED_NAMES:
            setattr(_v4, name, OVERRIDES[name])
        _v4.OVERRIDES.update(OVERRIDES)
        target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
        summary = _v4.build(output_dir=target)
    finally:
        for name, value in saved.items():
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
