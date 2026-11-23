"""Build the all-zone forestry residue handoff to OSeCAN v9.

Version 9 keeps the v2 province-to-zone proxy weights and allocation hierarchy
but refreshes the province/case totals from ``national_residue_curve_v8``,
which carries assignment v10 node transport-aware process-residue locations at
the 119 Mm3 reference and the E1 measured + E2c coefficients.

The official OSeCAN tree remains read-only and is hash-checked before and
after the build.  All rows remain model estimates: ``observed=false``,
``policy_grade=false``.  No policy target, national supply claim, UI change or
official OSeCAN change is made.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff as _base
from workflows.national.facilities import forestry_osecan_handoff_v8 as _v8

REPO_ROOT = _v8.REPO_ROOT
DATA_ROOT = _v8.DATA_ROOT
STEM_DIR = _v8.OUTPUT_DIR
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v8"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v9"
ARTIFACT = "forestry_osecan_handoff_v9"
ARTIFACT_VERSION = "9.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v9 (Assignment v10)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V9.md"
CURVE_LABEL = "national_residue_curve_v8"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v9"
TEST_PATH = "tests/test_national_facility_chain_v5.py"
VERSION_NOTE = (
    "Version 9 preserves the all-zone v2 proxy weights and refreshes the "
    "province/case totals from the assignment-v10 residue curve v8."
)
EXPECTED_CASE_TIERS = dict(_v8.EXPECTED_CASE_TIERS)

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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise _base.ForestryOsecanHandoffError(
            "tier comparison is empty"
        )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build handoff v9 and compare tier positions with handoff v8."""

    saved = {name: getattr(_v8, name) for name in _SAVED_MODULE_NAMES}
    try:
        for name in _SAVED_MODULE_NAMES:
            setattr(_v8, name, globals()[name])
        summary = _v8.build(output_dir=output_dir)
    finally:
        for name, value in saved.items():
            setattr(_v8, name, value)

    stem_summary = json.loads(
        (STEM_DIR / "summary.json").read_text(encoding="utf-8")
    )
    tier_rows = []
    for key, tier in summary["supply_curve"]["case_tier"].items():
        stream, case = key.split("/", 1)
        before = str(stem_summary["supply_curve"]["case_tier"].get(key, ""))
        tier_rows.append(
            {
                "stream": stream,
                "case": case,
                "v4_tier": before,
                "v5_tier": str(tier),
                "tier_changed": str(before != str(tier)).lower(),
                "model_estimate": "true",
                "observed": "false",
                "policy_grade": "false",
                "national_claim": "false",
            }
        )
    output = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    _write_csv(output / "tier_positions_v4_v5.csv", tier_rows)

    def _read(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    before_zones = {
        (row["zone_id"], row["stream"], row["availability_case"]): row
        for row in _read(STEM_DIR / "zone_summary.csv")
    }
    after_zones = _read(output / "zone_summary.csv")
    zone_rows: list[dict[str, Any]] = []
    for row in after_zones:
        key = (row["zone_id"], row["stream"], row["availability_case"])
        before_zone = before_zones.get(key)
        if before_zone is None:
            raise _base.ForestryOsecanHandoffError(
                f"v4 zone summary is missing {key}"
            )
        before_pj = float(before_zone["pj"])
        after_pj = float(row["pj"])
        zone_rows.append(
            {
                "zone_id": row["zone_id"],
                "zone_name": row["zone_name"],
                "zone_mapping_status": row["zone_mapping_status"],
                "stream": row["stream"],
                "availability_case": row["availability_case"],
                "v4_pj": f"{before_pj:.9f}",
                "v5_pj": f"{after_pj:.9f}",
                "delta_pj": f"{after_pj - before_pj:.9f}",
                "delta_pct": f"{(after_pj - before_pj) / before_pj * 100.0 if before_pj else 0.0:.9f}",
                "model_estimate": "true",
                "observed": "false",
                "policy_grade": "false",
                "national_claim": "false",
            }
        )
    _write_csv(output / "v4_v5_zone_comparison.csv", zone_rows)

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["assignment"] = "assignment v10 node_transport_aware"
    summary["residue_components"] = (
        "E1_measured_plus_E2c_density_calibrated_stem_branch_foliage"
    )
    summary["tier_positions_v4_v5"] = tier_rows
    summary["v4_v5_zone_comparison"] = {
        "csv": "v4_v5_zone_comparison.csv",
        "rows": len(zone_rows),
        "note": (
            "Assignment v10 shifts process residue between facility provinces "
            "and therefore between OSeCAN zones; national PJ and tiers are "
            "unchanged."
        ),
    }
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "Assignment v10 changes the facility province of some process "
            "residue; national process PJ and the OSeCAN tiers are unchanged."
        ),
        (
            "Transport cost is not part of the OSeCAN handoff or its official "
            "supply-curve tiers."
        ),
    ]
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 8", "Version 9").replace(
            "E2c v8", "assignment v10 v9"
        )
        text += (
            "\n## v4 versus v5 tier positions\n\n"
            "| Stream | Case | v4 | v5 | Changed |\n"
            "|---|---|---|---|---|\n"
            + "\n".join(
                f"| {row['stream']} | {row['case']} | {row['v4_tier']} | "
                f"{row['v5_tier']} | {row['tier_changed']} |"
                for row in tier_rows
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
        manifest["tier_positions_v4_v5"] = tier_rows
        manifest["v4_v5_zone_comparison"] = summary["v4_v5_zone_comparison"]
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
                "official_osecan_changed": summary["labels"][
                    "official_osecan_changed"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
