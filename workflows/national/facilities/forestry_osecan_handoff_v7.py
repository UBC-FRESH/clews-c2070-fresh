"""Build the all-zone forestry residue handoff to OSeCAN v7.

Version 7 keeps the v2 province-to-zone proxy weights and allocation hierarchy
but refreshes the province/case totals from ``national_residue_curve_v6``,
which carries E1 measured + E2 imputed product coefficients and total residue
(stem + CFS branch + foliage) for all 13 provinces.  The official OSeCAN tree
remains read-only and is hash-checked before and after the build.

The case tier positions are compared with the prior donor-product handoff v6 so
the effect of replacing donor coefficients and adding E2 branch/foliage dry mass
is explicit.  All rows remain model estimates: ``observed=false``,
``policy_grade=false``.  No policy target or national supply claim is made.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff_v5 as _v5
from workflows.national.facilities import forestry_osecan_handoff_v6 as _v6

REPO_ROOT = _v6.REPO_ROOT
DATA_ROOT = _v6.DATA_ROOT
STEM_DIR = _v6.OUTPUT_DIR
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v6"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v7"
ARTIFACT = "forestry_osecan_handoff_v7"
ARTIFACT_VERSION = "7.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v7 (E1+E2)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V7.md"
CURVE_LABEL = "national_residue_curve_v6"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v7"
TEST_PATH = "tests/test_forestry_osecan_handoff_v7.py"
VERSION_NOTE = (
    "Version 7 preserves the all-zone v2 proxy weights and refreshes the "
    "province/case totals from the E1+E2 residue curve v6."
)
EXPECTED_CASE_TIERS: dict[tuple[str, str], str] = {
    ("process", "process_availability_000"): "T1",
    ("process", "process_availability_025"): "T3",
    ("process", "process_availability_050"): "above_T4",
    ("process", "process_availability_075"): "above_T4",
    ("harvest", "harvest_recovery_035"): "T3",
    ("harvest", "harvest_recovery_060"): "above_T4",
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
    """Build handoff v7 and compare national tier positions with v6."""

    saved = {name: getattr(_v6, name) for name in _SAVED_MODULE_NAMES}
    saved_base = dict(_v5.BASE_OVERRIDES)
    saved_v1 = dict(_v5.V1_OVERRIDES)
    try:
        for name in _SAVED_MODULE_NAMES:
            setattr(_v6, name, globals()[name])
        _v6.BASE_OVERRIDES = {
            **_v5.BASE_OVERRIDES,
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
        _v6.V1_OVERRIDES = {
            **_v5.V1_OVERRIDES,
            "PROVINCE_CURVE": PROVINCE_CURVE,
            "NATIONAL_CURVE": NATIONAL_CURVE,
            "CURVE_SUMMARY": CURVE_SUMMARY,
            "CURVE_DIR": CURVE_DIR,
            "EXPECTED_CASE_TIERS": EXPECTED_CASE_TIERS,
        }
        summary = _v6.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v6, name, value)
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
    e2_tiers = {
        key: str(value)
        for key, value in summary["supply_curve"]["case_tier"].items()
    }
    tier_rows = [
        {
            "stream": key.split("/", 1)[0],
            "case": key.split("/", 1)[1],
            "donor_product_v6_tier": stem_tiers.get(key, ""),
            "e2_v7_tier": e2_tiers.get(key, ""),
            "tier_changed": stem_tiers.get(key) != e2_tiers.get(key),
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "national_claim": "false",
        }
        for key in sorted(e2_tiers)
    ]
    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    output.mkdir(parents=True, exist_ok=True)
    with (output / "tier_positions_donor_vs_e2.csv").open(
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
        "E1_measured_plus_E2_imputed_stem_branch_foliage"
    )
    summary["recovery_scenarios"] = [0.35, 0.60, 0.75]
    summary["calibrated_recovery_factor"] = False
    summary["tier_positions_donor_vs_e2"] = tier_rows
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "E2 product shares replace donor product coefficients for nine "
            "provinces; E2 branch/foliage add dry mass but no measured volume."
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
        text = text.replace("Version 6", "Version 7").replace(
            "stem-only v5", "donor-product v6"
        )
        text += (
            "\n## Prior donor-product versus E2 tier positions\n\n"
            "| Stream | Case | Donor product v6 | E2 v7 | Changed |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(
                f"| {row['stream']} | {row['case']} | "
                f"{row['donor_product_v6_tier']} | {row['e2_v7_tier']} | "
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
        manifest["tier_positions_donor_vs_e2"] = tier_rows
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
