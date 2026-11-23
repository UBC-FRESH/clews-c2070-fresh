"""Build the all-zone forestry residue handoff to OSeCAN v6.

Version 6 keeps the v2 province-to-zone proxy weights and allocation hierarchy
but refreshes the province/case totals from ``national_residue_curve_v5``,
which carries measured total harvest residue (stem + branch + foliage) for
BC/QC/NB/YT.  The official OSeCAN tree remains read-only and is hash-checked
before and after the build.

The case tier positions are compared with the stem-only handoff v5 so the
effect of adding branch and foliage dry mass is explicit.  All rows remain
model estimates: ``observed=false``, ``policy_grade=false``.  No policy target
or national supply claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_v5 as _v5

REPO_ROOT = _v5.REPO_ROOT
DATA_ROOT = _v5.DATA_ROOT
STEM_DIR = _v5.OUTPUT_DIR
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v5"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v6"
ARTIFACT = "forestry_osecan_handoff_v6"
ARTIFACT_VERSION = "6.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v6 (Total Residue)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V6.md"
CURVE_LABEL = "national_residue_curve_v5"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v6"
TEST_PATH = "tests/test_forestry_osecan_handoff_v6.py"
VERSION_NOTE = (
    "Version 6 preserves the all-zone v2 proxy weights and refreshes the "
    "province/case totals from the total-residue curve v5."
)
EXPECTED_CASE_TIERS = {
    ("process", "process_availability_000"): "T1",
    ("process", "process_availability_025"): "T3",
    ("process", "process_availability_050"): "above_T4",
    ("process", "process_availability_075"): "above_T4",
    ("harvest", "harvest_recovery_035"): "T3",
    ("harvest", "harvest_recovery_060"): "T4",
    ("harvest", "harvest_recovery_075"): "above_T4",
}

_SAVED_MODULE_NAMES = (
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
    """Build handoff v6 and compare national tier positions with v5."""

    saved = {name: getattr(_v5, name) for name in _SAVED_MODULE_NAMES}
    saved_base = dict(_v5.BASE_OVERRIDES)
    saved_v1 = dict(_v5.V1_OVERRIDES)
    try:
        for name in _SAVED_MODULE_NAMES:
            setattr(_v5, name, globals()[name])
        _v5.BASE_OVERRIDES.update(
            {
                "OUTPUT_DIR": OUTPUT_DIR,
                "PROVINCE_CURVE": PROVINCE_CURVE,
                "NATIONAL_CURVE": NATIONAL_CURVE,
                "CURVE_SUMMARY": CURVE_SUMMARY,
                "ARTIFACT": ARTIFACT,
                "ARTIFACT_VERSION": ARTIFACT_VERSION,
                "ARTIFACT_TITLE": ARTIFACT_TITLE,
                "REPORT_NAME": REPORT_NAME,
                "CURVE_LABEL": CURVE_LABEL,
                "MODULE_PATH": MODULE_PATH,
                "TEST_PATH": TEST_PATH,
                "VERSION_NOTE": VERSION_NOTE,
                "EXPECTED_CASE_TIERS": EXPECTED_CASE_TIERS,
            }
        )
        _v5.V1_OVERRIDES.update(
            {
                "PROVINCE_CURVE": PROVINCE_CURVE,
                "NATIONAL_CURVE": NATIONAL_CURVE,
                "CURVE_SUMMARY": CURVE_SUMMARY,
                "CURVE_DIR": CURVE_DIR,
                "EXPECTED_CASE_TIERS": EXPECTED_CASE_TIERS,
            }
        )
        summary = _v5.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v5, name, value)
        _v5.BASE_OVERRIDES.clear()
        _v5.BASE_OVERRIDES.update(saved_base)
        _v5.V1_OVERRIDES.clear()
        _v5.V1_OVERRIDES.update(saved_v1)

    stem_summary = json.loads(
        (STEM_DIR / "summary.json").read_text(encoding="utf-8")
    )
    stem_tiers = {
        key: str(value)
        for key, value in stem_summary["supply_curve"]["case_tier"].items()
    }
    total_tiers = {
        key: str(value)
        for key, value in summary["supply_curve"]["case_tier"].items()
    }
    tier_rows = [
        {
            "stream": key.split("/", 1)[0],
            "case": key.split("/", 1)[1],
            "stem_only_v5_tier": stem_tiers.get(key, ""),
            "total_residue_v6_tier": total_tiers.get(key, ""),
            "tier_changed": stem_tiers.get(key) != total_tiers.get(key),
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "national_claim": "false",
        }
        for key in sorted(total_tiers)
    ]
    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    with (output / "tier_positions_stem_vs_total.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(tier_rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(tier_rows)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["residue_components"] = "stem_plus_branch_foliage"
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["tier_positions_stem_vs_total"] = tier_rows
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "Branch and foliage add dry mass but no measured volume. The "
            "handoff PJ totals therefore rise without changing mill products "
            "or process residue."
        ),
        (
            "0.35/0.60/0.75 recovery cases are carried; 0.60 is a reference "
            "case, not a calibrated recovery factor."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 5", "Version 6").replace(
            "stem-only v5", "stem-only v5"
        )
        text += (
            "\n## Stem-only versus total residue tier positions\n\n"
            "| Stream | Case | Stem-only v5 | Total residue v6 | Changed |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(
                f"| {row['stream']} | {row['case']} | "
                f"{row['stem_only_v5_tier']} | {row['total_residue_v6_tier']} | "
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
        manifest["tier_positions_stem_vs_total"] = tier_rows
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
                "handoff_totals": summary["handoff_totals"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
