"""Build multi-scenario forestry residue PJ for OSeCAN zone handoff v9.

Version 9 carries the assignment-v10 node transport-aware chain v9 at all
three harvest cases.  The province-to-zone weights are unchanged from handoff
v10; the 119 Mm3 cases still reconcile against curve v9, and the 100/150 Mm3
cases use the same chain-scenario province totals.  The official OSeCAN tree
stays read-only and hash-checked.

Transport cost is not part of this artifact or any official OSeCAN tier.  All
values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_scenarios_v8 as _v8

REPO_ROOT = _v8.REPO_ROOT
DATA_ROOT = _v8.DATA_ROOT
SCENARIO_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v9"
)
SCENARIO_PRODUCT_RESIDUE = SCENARIO_DIR / "scenario_product_residue.csv"
SCENARIO_SUMMARY = SCENARIO_DIR / "scenario_summary.csv"
SCENARIO_SUMMARY_JSON = SCENARIO_DIR / "summary.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "manifest.json"
HANDOFF_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v10"
)
HANDOFF_V2_WEIGHTS = HANDOFF_V2_DIR / "zone_allocation_weights.csv"
HANDOFF_V2_SUMMARY = HANDOFF_V2_DIR / "summary.json"
HANDOFF_V2_MANIFEST = HANDOFF_V2_DIR / "manifest.json"
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v9"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v9"
ARTIFACT = "forestry_osecan_scenarios_v9"
ARTIFACT_VERSION = "9.0.0"
ARTIFACT_TITLE = "Forestry Residue Scenarios to OSeCAN Zones v9 (all cases)"
REPORT_NAME = "FORESTRY_OSECAN_SCENARIOS_V9.md"
CHAIN_LABEL = "national_facility_chain_scenarios_v9"
HANDOFF_LABEL = "forestry_osecan_handoff_v10"
CURVE_LABEL = "national_residue_curve_v9"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_scenarios_v9"
TEST_PATH = "tests/test_national_facility_chain_v5_1.py"
SCENARIO_EVIDENCE_TIER = "scenario_model_estimate|handoff_v10_zone_weight"

_SAVED_NAMES = (
    "SCENARIO_DIR",
    "SCENARIO_PRODUCT_RESIDUE",
    "SCENARIO_SUMMARY",
    "SCENARIO_SUMMARY_JSON",
    "SCENARIO_MANIFEST",
    "HANDOFF_V2_DIR",
    "HANDOFF_V2_WEIGHTS",
    "HANDOFF_V2_SUMMARY",
    "HANDOFF_V2_MANIFEST",
    "CURVE_DIR",
    "NATIONAL_CURVE",
    "PROVINCE_CURVE",
    "CURVE_SUMMARY",
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "CHAIN_LABEL",
    "HANDOFF_LABEL",
    "CURVE_LABEL",
    "MODULE_PATH",
    "TEST_PATH",
    "SCENARIO_EVIDENCE_TIER",
)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build OSeCAN scenarios v9 from chain v9 and handoff v10."""

    saved = {name: getattr(_v8, name) for name in _SAVED_NAMES}
    saved_overrides = dict(_v8._v7._v4.BASE_OVERRIDES)
    try:
        for name in _SAVED_NAMES:
            setattr(_v8, name, globals()[name])
        _v8._v7._v4.BASE_OVERRIDES.update(
            {
                "SCENARIO_DIR": SCENARIO_DIR,
                "SCENARIO_PRODUCT_RESIDUE": SCENARIO_PRODUCT_RESIDUE,
                "SCENARIO_SUMMARY": SCENARIO_SUMMARY,
                "SCENARIO_SUMMARY_JSON": SCENARIO_SUMMARY_JSON,
                "SCENARIO_MANIFEST": SCENARIO_MANIFEST,
                "HANDOFF_V2_DIR": HANDOFF_V2_DIR,
                "HANDOFF_V2_WEIGHTS": HANDOFF_V2_WEIGHTS,
                "HANDOFF_V2_SUMMARY": HANDOFF_V2_SUMMARY,
                "HANDOFF_V2_MANIFEST": HANDOFF_V2_MANIFEST,
                "CURVE_DIR": CURVE_DIR,
                "NATIONAL_CURVE": NATIONAL_CURVE,
                "PROVINCE_CURVE": PROVINCE_CURVE,
                "CURVE_SUMMARY": CURVE_SUMMARY,
                "OUTPUT_DIR": OUTPUT_DIR,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "CHAIN_LABEL": CHAIN_LABEL,
                "HANDOFF_LABEL": HANDOFF_LABEL,
                "CURVE_LABEL": CURVE_LABEL,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
                "SCENARIO_EVIDENCE_TIER": SCENARIO_EVIDENCE_TIER,
                "ALLOWED_ZERO_FEEDSTOCK_CLASSES": {
                    "SK": frozenset({"SAW", "PANEL"})
                },
            }
        )
        summary = _v8.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v8, name, value)
        _v8._v7._v4.BASE_OVERRIDES.clear()
        _v8._v7._v4.BASE_OVERRIDES.update(saved_overrides)

    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["assignment"] = (
        "assignment v10 node transport-aware at 100m, 119m and 150m"
    )
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "transport_cost_in_osecan": False,
    }
    summary["transport_cost_in_osecan"] = False
    summary["case_zone_summary"] = {
        "csv": "case_zone_summary.csv",
        "source": "forestry_osecan_handoff_v10/case_zone_summary.csv",
        "cases": ["100m", "119m", "150m"],
        "note": (
            "Handoff v10 supplies the explicit all-case zone summary; this "
            "artifact keeps the scenario/stream/availability table and tier "
            "positions."
        ),
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "All three harvest cases now use assignment v10 node "
            "transport-aware flows; national process and harvest PJ are "
            "invariant to assignment."
        ),
        (
            "Transport cost is reported only in the facility chain; it is not "
            "included in OSeCAN official tiers or any OSeCAN input."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 8", "Version 9")
        text += (
            "\n## All-case assignment v10\n\n"
            "The 100/119/150 Mm3 chain cases all use assignment v10 node "
            "transport-aware flows. National process/harvest PJ and the 21 "
            "tier positions are invariant. Transport cost is not an OSeCAN "
            "input.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["case_zone_summary"] = summary["case_zone_summary"]
    manifest["transport_cost_in_osecan"] = False
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "scenarios": summary["scenario_order"],
                "validation": summary["validation"]["passed"],
                "transport_cost_in_osecan": summary["transport_cost_in_osecan"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
