"""Build multi-scenario forestry residue PJ for OSeCAN zone handoff v3.

Version 3 refreshes the chain-scenario province totals to
``national_facility_chain_scenarios_v3`` (assignment v6 / capacity v3
provincial floor) and consumes the ``forestry_osecan_handoff_v4`` weights,
which preserve the v2 zone allocation hierarchy.  It reconciles the 119m cases
against ``national_residue_curve_v3``.

The official OSeCAN tree remains read-only and is hash-checked before and
after the build.  Every row keeps ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, and ``official_osecan_changed=false``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_scenarios as _base

REPO_ROOT = _base.REPO_ROOT
DATA_ROOT = _base.DATA_ROOT
SCENARIO_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v3"
)
SCENARIO_PRODUCT_RESIDUE = SCENARIO_DIR / "scenario_product_residue.csv"
SCENARIO_SUMMARY = SCENARIO_DIR / "scenario_summary.csv"
SCENARIO_SUMMARY_JSON = SCENARIO_DIR / "summary.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "manifest.json"

HANDOFF_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v4"
)
HANDOFF_V2_WEIGHTS = HANDOFF_V2_DIR / "zone_allocation_weights.csv"
HANDOFF_V2_SUMMARY = HANDOFF_V2_DIR / "summary.json"
HANDOFF_V2_MANIFEST = HANDOFF_V2_DIR / "manifest.json"

CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v3"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"

OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v3"
ARTIFACT = "forestry_osecan_scenarios_v3"
ARTIFACT_VERSION = "3.0.0"
ARTIFACT_TITLE = "Forestry Residue Scenarios to OSeCAN Zones v3"
REPORT_NAME = "FORESTRY_OSECAN_SCENARIOS_V3.md"
CHAIN_LABEL = "national_facility_chain_scenarios_v3"
HANDOFF_LABEL = "forestry_osecan_handoff_v4"
CURVE_LABEL = "national_residue_curve_v3"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_scenarios_v3"
TEST_PATH = "tests/test_forestry_osecan_scenarios_v3.py"
SCENARIO_EVIDENCE_TIER = "scenario_model_estimate|handoff_v4_zone_weight"

SCENARIO_ORDER = _base.SCENARIO_ORDER
PRIMARY_SCENARIO = _base.PRIMARY_SCENARIO
PROCESS_CASES = _base.PROCESS_CASES
HARVEST_CASES = _base.HARVEST_CASES
PROVINCES = _base.PROVINCES
ZONE_ORDER = _base.ZONE_ORDER
EXPECTED_ZONE_WEIGHT_ROWS = _base.EXPECTED_ZONE_WEIGHT_ROWS
EXPECTED_RESIDUE_ROWS = _base.EXPECTED_RESIDUE_ROWS
EXPECTED_ZONE_SUMMARY_ROWS = _base.EXPECTED_ZONE_SUMMARY_ROWS
EXPECTED_CANDIDATE_ROWS = _base.EXPECTED_CANDIDATE_ROWS
ForestryOsecanScenariosError = _base.ForestryOsecanScenariosError

BASE_OVERRIDES: dict[str, Any] = {
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
}


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the v3 scenarios while isolating shared module globals."""
    saved = {name: getattr(_base, name) for name in BASE_OVERRIDES}
    try:
        for name, value in BASE_OVERRIDES.items():
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
                "validation": summary["validation"]["passed"],
                "scenarios": summary["scenario_order"],
                "province_zone_allocation_rows": summary["record_counts"][
                    "province_zone_allocation_rows"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
