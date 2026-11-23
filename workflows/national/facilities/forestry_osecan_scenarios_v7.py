"""Build multi-scenario forestry residue PJ for OSeCAN zone handoff v7.

Version 7 refreshes the chain-scenario province totals to
``national_facility_chain_scenarios_v7`` (E1 measured + E2c density-calibrated
coefficients, total residue for all 13 jurisdictions, assignment v6 / capacity
v3 floor), consumes the ``forestry_osecan_handoff_v8`` weights, and reconciles
the 119m cases against ``national_residue_curve_v7``.

The official OSeCAN tree remains read-only and is hash-checked before and
after the build.  Every row keeps ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, and ``official_osecan_changed=false``.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_scenarios_v4 as _v4

REPO_ROOT = _v4.REPO_ROOT
DATA_ROOT = _v4.DATA_ROOT
STEM_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v6"
)
SCENARIO_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_chain_scenarios_v7"
)
SCENARIO_PRODUCT_RESIDUE = SCENARIO_DIR / "scenario_product_residue.csv"
SCENARIO_SUMMARY = SCENARIO_DIR / "scenario_summary.csv"
SCENARIO_SUMMARY_JSON = SCENARIO_DIR / "summary.json"
SCENARIO_MANIFEST = SCENARIO_DIR / "manifest.json"
HANDOFF_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v8"
)
HANDOFF_V2_WEIGHTS = HANDOFF_V2_DIR / "zone_allocation_weights.csv"
HANDOFF_V2_SUMMARY = HANDOFF_V2_DIR / "summary.json"
HANDOFF_V2_MANIFEST = HANDOFF_V2_DIR / "manifest.json"
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v7"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v7"
ARTIFACT = "forestry_osecan_scenarios_v7"
ARTIFACT_VERSION = "7.0.0"
ARTIFACT_TITLE = "Forestry Residue Scenarios to OSeCAN Zones v7 (E1+E2c)"
REPORT_NAME = "FORESTRY_OSECAN_SCENARIOS_V7.md"
CHAIN_LABEL = "national_facility_chain_scenarios_v7"
HANDOFF_LABEL = "forestry_osecan_handoff_v8"
CURVE_LABEL = "national_residue_curve_v7"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_scenarios_v7"
TEST_PATH = "tests/test_forestry_osecan_scenarios_v7.py"
SCENARIO_EVIDENCE_TIER = "scenario_model_estimate|handoff_v7_zone_weight"

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


def _case_tier(pj: float, tiers: dict[str, dict[str, Any]]) -> str:
    for name in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[name]["upper_pj"]) + 1e-9:
            return name
    return "above_T4"


def _tier_comparison(
    stem_summary: dict[str, Any],
    e2_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    tiers = e2_summary["supply_curve"]["tiers"]
    rows: list[dict[str, Any]] = []
    for scenario in e2_summary["scenario_order"]:
        for stream in ("process", "harvest"):
            for case, payload in e2_summary["national_case_totals"][scenario][
                stream
            ].items():
                stem_pj = float(
                    stem_summary["national_case_totals"][scenario][stream][case][
                        "pj"
                    ]
                )
                e2_pj = float(payload["pj"])
                rows.append(
                    {
                        "scenario": scenario,
                        "stream": stream,
                        "case": case,
                        "v3_2_v7_pj": stem_pj,
                        "v4_e2c_pj": e2_pj,
                        "delta_pj": e2_pj - stem_pj,
                        "v3_2_v7_tier": _case_tier(stem_pj, tiers),
                        "v4_e2c_tier": _case_tier(e2_pj, tiers),
                        "tier_changed": (
                            _case_tier(stem_pj, tiers) != _case_tier(e2_pj, tiers)
                        ),
                        "model_estimate": "true",
                        "observed": "false",
                        "policy_grade": "false",
                        "national_claim": "false",
                    }
                )
    return rows


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build scenarios v6 and write the 100/119/150 tier comparison."""

    saved = {name: getattr(_v4, name) for name in _SAVED_NAMES}
    saved_overrides = dict(_v4.BASE_OVERRIDES)
    try:
        for name in _SAVED_NAMES:
            setattr(_v4, name, globals()[name])
        _v4.BASE_OVERRIDES.update(
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
        summary = _v4.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v4, name, value)
        _v4.BASE_OVERRIDES.clear()
        _v4.BASE_OVERRIDES.update(saved_overrides)

    stem_summary = json.loads(
        (STEM_DIR / "summary.json").read_text(encoding="utf-8")
    )
    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    tier_rows = _tier_comparison(stem_summary, summary)
    with (output / "tier_positions_v3_2_vs_v4.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(tier_rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(tier_rows)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["residue_components"] = (
        "E1_measured_plus_E2c_density_calibrated_stem_branch_foliage"
    )
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["tier_positions_v3_2_vs_v4"] = tier_rows
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2c product coefficients replace the frozen v3.2 chain E2 v1 "
            "coefficients for nine jurisdictions. Harvest residue includes CFS "
            "branch and foliage dry mass; products and process residue do not."
        ),
        (
            "0.35/0.60/0.75 recovery cases are carried; no single calibrated "
            "recovery factor is adopted."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 5", "Version 6")
        text += (
            "\n## Frozen v3.2 versus E2c tier positions\n\n"
            "| Scenario | Stream | Case | v3.2 v7 PJ | E2c v8 PJ | "
            "v3.2 tier | E2c tier | Changed |\n"
            "|---|---|---|---:|---:|---|---|---|\n"
            + "\n".join(
                f"| {row['scenario']} | {row['stream']} | {row['case']} | "
                f"{row['v3_2_v7_pj']:.6f} | "
                f"{row['v4_e2c_pj']:.6f} | "
                f"{row['v3_2_v7_tier']} | {row['v4_e2c_tier']} | "
                f"{row['tier_changed']} |"
                for row in tier_rows
            )
            + "\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest_path = output / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
        manifest["tier_positions_v3_2_vs_v4"] = tier_rows
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
                "province_zone_allocation_rows": summary["record_counts"][
                    "province_zone_allocation_rows"
                ],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
