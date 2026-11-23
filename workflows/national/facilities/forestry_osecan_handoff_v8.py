"""Build the all-zone forestry residue handoff to OSeCAN v8.

Version 8 keeps the v2 province-to-zone proxy weights and allocation hierarchy
but refreshes the province/case totals from ``national_residue_curve_v7``,
which carries E1 measured + E2c density-calibrated product coefficients and
total residue (stem + CFS branch + foliage) for all 13 jurisdictions.

The official OSeCAN tree remains read-only and is hash-checked before and
after the build.  All rows remain model estimates: ``observed=false``,
``policy_grade=false``.  No policy target, national supply claim, UI change or
official OSeCAN change is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_v7 as _v7

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
STEM_DIR = _v7.OUTPUT_DIR
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v7"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v8"
ARTIFACT = "forestry_osecan_handoff_v8"
ARTIFACT_VERSION = "8.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v8 (E1+E2c)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V8.md"
CURVE_LABEL = "national_residue_curve_v7"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v8"
TEST_PATH = "tests/test_forestry_osecan_handoff_v8.py"
VERSION_NOTE = (
    "Version 8 preserves the all-zone v2 proxy weights and refreshes the "
    "province/case totals from the E1+E2c residue curve v7."
)
EXPECTED_CASE_TIERS: dict[tuple[str, str], str] = {
    ("process", "process_availability_000"): "T1",
    ("process", "process_availability_025"): "T3",
    ("process", "process_availability_050"): "above_T4",
    ("process", "process_availability_075"): "above_T4",
    ("harvest", "harvest_recovery_035"): "T4",
    ("harvest", "harvest_recovery_060"): "above_T4",
    ("harvest", "harvest_recovery_075"): "above_T4",
}

_SAVED_MODULE_NAMES = (
    "STEM_DIR",
    "OUTPUT_DIR",
    "CURVE_DIR",
    "PROVINCE_CURVE",
    "NATIONAL_CURVE",
    "CURVE_SUMMARY",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "CURVE_LABEL",
    "MODULE_PATH",
    "TEST_PATH",
    "EXPECTED_CASE_TIERS",
    "VERSION_NOTE",
)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build handoff v8 and compare national tier positions with v7."""

    saved = {name: getattr(_v7, name) for name in _SAVED_MODULE_NAMES}
    try:
        for name in _SAVED_MODULE_NAMES:
            setattr(_v7, name, globals()[name])
        summary = _v7.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v7, name, value)

    stem_summary = json.loads(
        (STEM_DIR / "summary.json").read_text(encoding="utf-8")
    )
    stem_tiers = {
        key: str(value)
        for key, value in stem_summary["supply_curve"]["case_tier"].items()
    }
    e2c_tiers = {
        key: str(value)
        for key, value in summary["supply_curve"]["case_tier"].items()
    }
    tier_rows = [
        {
            "stream": key.split("/", 1)[0],
            "case": key.split("/", 1)[1],
            "v3_2_tier": stem_tiers.get(key, ""),
            "v4_e2c_tier": e2c_tiers.get(key, ""),
            "tier_changed": stem_tiers.get(key) != e2c_tiers.get(key),
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "national_claim": "false",
        }
        for key in sorted(e2c_tiers)
    ]
    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    with (target / "tier_positions_v3_2_vs_v4.csv").open(
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
    summary["v3_2_vs_v4_tiers"] = tier_rows
    for stale in ("tier_positions_donor_vs_e2", "tier_positions_stem_vs_total"):
        summary.pop(stale, None)
    for stale_file in (
        "tier_positions_donor_vs_e2.csv",
        "tier_positions_stem_vs_total.csv",
    ):
        path = target / stale_file
        if path.is_file():
            path.unlink()
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2c density calibration changes stems/ha expansion, not per-m3 "
            "product or residue coefficients. The v8 curve totals differ from "
            "v7 through the E2 v1 to E2c coefficient change only."
        ),
        (
            "0.35/0.60/0.75 recovery cases are carried; 0.60 is a reference "
            "case, not a calibrated recovery factor."
        ),
    ]
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report_path = target / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 7", "Version 8").replace(
            "donor-product v6", "frozen v3.2 v7"
        ).replace("E2 v7", "E2c v8")
        text += (
            "\n## Frozen v3.2 versus E2c tier positions\n\n"
            "| Stream | Case | v3.2 v7 | E2c v8 | Changed |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(
                f"| {row['stream']} | {row['case']} | "
                f"{row['v3_2_tier']} | {row['v4_e2c_tier']} | "
                f"{row['tier_changed']} |"
                for row in tier_rows
            )
            + "\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest_path = target / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = ARTIFACT
        manifest["artifact_version"] = ARTIFACT_VERSION
        manifest["labels"] = summary["labels"]
        manifest["v3_2_vs_v4_tiers"] = tier_rows
        for stale in ("tier_positions_donor_vs_e2", "tier_positions_stem_vs_total"):
            manifest.pop(stale, None)
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
                "handoff_totals": summary["handoff_totals"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
