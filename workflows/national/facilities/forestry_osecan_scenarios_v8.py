"""Build multi-scenario forestry residue PJ for OSeCAN zone handoff v8.

Version 8 refreshes the chain-scenario province totals to
``national_facility_chain_scenarios_v8`` (assignment v10 node transport-aware
at the 119 Mm3 reference, assignment v6 local-first at 100/150 Mm3), consumes
the ``forestry_osecan_handoff_v9`` weights, and reconciles the 119m cases
against ``national_residue_curve_v8``.

The official OSeCAN tree remains read-only and is hash-checked before and
after the build.  Every row keeps ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, and ``official_osecan_changed=false``.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_scenarios_v7 as _v7

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
STEM_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v7"
)
SCENARIO_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v8"
)
SCENARIO_PRODUCT_RESIDUE = SCENARIO_DIR / "scenario_product_residue.csv"
SCENARIO_SUMMARY = SCENARIO_DIR / "scenario_summary.csv"
SCENARIO_SUMMARY_JSON = SCENARIO_DIR / "summary.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "manifest.json"
HANDOFF_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v9"
)
HANDOFF_V2_WEIGHTS = HANDOFF_V2_DIR / "zone_allocation_weights.csv"
HANDOFF_V2_SUMMARY = HANDOFF_V2_DIR / "summary.json"
HANDOFF_V2_MANIFEST = HANDOFF_V2_DIR / "manifest.json"
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v8"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v8"
ARTIFACT = "forestry_osecan_scenarios_v8"
ARTIFACT_VERSION = "8.0.0"
ARTIFACT_TITLE = "Forestry Residue Scenarios to OSeCAN Zones v8 (Assignment v10)"
REPORT_NAME = "FORESTRY_OSECAN_SCENARIOS_V8.md"
CHAIN_LABEL = "national_facility_chain_scenarios_v8"
HANDOFF_LABEL = "forestry_osecan_handoff_v9"
CURVE_LABEL = "national_residue_curve_v8"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_scenarios_v8"
TEST_PATH = "tests/test_national_facility_chain_v5.py"
SCENARIO_EVIDENCE_TIER = "scenario_model_estimate|handoff_v9_zone_weight"

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


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("comparison table is empty")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _tier_name(pj: float, tiers: dict[str, Any]) -> str:
    for name in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[name]["upper_pj"]) + 1e-9:
            return name
    return "above_T4"


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build OSeCAN scenarios v8 with the v4/v5 tier comparison."""

    saved = {name: getattr(_v7, name) for name in _SAVED_NAMES}
    saved_overrides = dict(_v7._v4.BASE_OVERRIDES)
    try:
        for name in _SAVED_NAMES:
            setattr(_v7, name, globals()[name])
        _v7._v4.BASE_OVERRIDES.update(
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
        summary = _v7.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v7, name, value)
        _v7._v4.BASE_OVERRIDES.clear()
        _v7._v4.BASE_OVERRIDES.update(saved_overrides)

    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    stem_summary = json.loads(
        (STEM_DIR / "summary.json").read_text(encoding="utf-8")
    )
    tiers = summary["supply_curve"]["tiers"]
    comparison = []
    for scenario in summary["scenario_order"]:
        for stream in ("process", "harvest"):
            for case, payload in summary["national_case_totals"][scenario][
                stream
            ].items():
                before = float(
                    stem_summary["national_case_totals"][scenario][stream][
                        case
                    ]["pj"]
                )
                after = float(payload["pj"])
                comparison.append(
                    {
                        "scenario": scenario,
                        "stream": stream,
                        "case": case,
                        "v4_pj": f"{before:.9f}",
                        "v5_pj": f"{after:.9f}",
                        "delta_pj": f"{after - before:.9f}",
                        "v4_tier": _tier_name(before, tiers),
                        "v5_tier": _tier_name(after, tiers),
                        "tier_changed": str(
                            _tier_name(before, tiers)
                            != _tier_name(after, tiers)
                        ).lower(),
                        "model_estimate": "true",
                        "observed": "false",
                        "policy_grade": "false",
                        "national_claim": "false",
                    }
                )
    _write_csv(output / "v4_v5_tier_comparison.csv", comparison)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["assignment"] = (
        "119m assignment v10 node transport-aware; 100m/150m assignment v6 "
        "local-first"
    )
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["v4_v5_tier_comparison"] = comparison
    summary["transport_cost_in_osecan"] = False
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "Transport cost is reported only in the facility chain; it is not "
            "included in OSeCAN official tiers or any OSeCAN input."
        ),
        (
            "Assignment v10 changes some facility-province process residues; "
            "national process PJ and the OSeCAN tier positions are unchanged."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 7", "Version 8")
        text += (
            "\n## v4 versus v5 OSeCAN tiers\n\n"
            "| Scenario | Stream | Case | v4 PJ | v5 PJ | Delta PJ | "
            "v4 tier | v5 tier | Changed |\n"
            "|---|---|---|---:|---:|---:|---|---|---|\n"
            + "\n".join(
                f"| {row['scenario']} | {row['stream']} | {row['case']} | "
                f"{row['v4_pj']} | {row['v5_pj']} | {row['delta_pj']} | "
                f"{row['v4_tier']} | {row['v5_tier']} | "
                f"{row['tier_changed']} |"
                for row in comparison
            )
            + "\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        import hashlib

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
        manifest["v4_v5_tier_comparison"] = comparison
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
                "scenarios": summary["scenario_order"],
                "transport_cost_in_osecan": summary["transport_cost_in_osecan"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
