"""Forestry residue handoff to OSeCAN v10 across the 100/119/150 cases.

Version 10 keeps the frozen-in-spirit all-zone proxy weights and reads the
assignment-v10 residue curve v9.  The 119 Mm3 handoff is reproduced unchanged
from handoff v9; a case-level zone summary extends the same province-to-zone
weights to the 100 and 150 Mm3 curve cases so the energy-side handoff is
explicit at all three harvest cases.

The official OSeCAN tree remains read-only and hash-checked before and after
the build.  Transport cost is not part of the handoff or its official tiers.
All values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from workflows.national.facilities import forestry_osecan_handoff as _base
from workflows.national.facilities import forestry_osecan_handoff_v9 as _v9

REPO_ROOT = _v9.REPO_ROOT
DATA_ROOT = _v9.DATA_ROOT
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v9"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
SCENARIO_CURVE = CURVE_DIR / "scenario_residue_curve.csv"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v10"
STEM_DIR = _v9.OUTPUT_DIR
ARTIFACT = "forestry_osecan_handoff_v10"
ARTIFACT_VERSION = "10.0.0"
ARTIFACT_TITLE = "Forestry Residue Handoff to OSeCAN v10 (all cases)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_V10.md"
CURVE_LABEL = "national_residue_curve_v9"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_v10"
TEST_PATH = "tests/test_national_facility_chain_v5_1.py"
VERSION_NOTE = (
    "Version 10 preserves the all-zone v2 proxy weights, reads curve v9 and "
    "adds an explicit 100/119/150 case zone summary."
)
EXPECTED_CASE_TIERS = dict(_v9.EXPECTED_CASE_TIERS)
CASES = ("100m", "119m", "150m")
PRIMARY_CASE = "119m"
CASE_FIELDS = [
    "scenario",
    "zone_id",
    "zone_name",
    "stream",
    "availability_case",
    "zone_mapping_status",
    "provinces_included",
    "residue_odt",
    "pj",
    "national_case_odt",
    "national_case_pj",
    "share_of_national_case",
    "supply_curve_tier_at_national_case",
    "model_estimate",
    "observed",
    "policy_grade",
    "official_osecan_changed",
]
TIER_FIELDS = [
    "scenario",
    "stream",
    "case",
    "national_pj",
    "tier",
    "model_estimate",
    "observed",
    "policy_grade",
    "national_claim",
]
_SAVED_NAMES = (
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
    "VERSION_NOTE",
    "EXPECTED_CASE_TIERS",
)


class ForestryOsecanHandoffV10Error(_base.ForestryOsecanHandoffError):
    """Raised when the all-case handoff extension fails."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ForestryOsecanHandoffV10Error(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, fields: list[str], rows: list[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in rows
        )


def _tier(pj: float, tiers: dict[str, Any]) -> str:
    for tier_id in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[tier_id]["upper_pj"]) + 1e-9:
            return tier_id
    return "above_T4"


def _zone_weights() -> dict[tuple[str, str], tuple[str, float]]:
    rows = _read_rows(OUTPUT_DIR / "zone_allocation_weights.csv")
    return {
        (row["province"].strip().upper(), row["zone_id"].strip().upper()): (
            row["zone_name"].strip(),
            float(row["weight"]),
        )
        for row in rows
    }


def _zone_rows(
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    weights = _zone_weights()
    curve = _read_rows(SCENARIO_CURVE)
    tiers = summary["supply_curve"]["tiers"]
    case_rows: list[dict[str, Any]] = []
    tier_rows: list[dict[str, Any]] = []
    for case in CASES:
        for stream, cases in (
            (
                "process",
                (
                    "process_availability_000",
                    "process_availability_025",
                    "process_availability_050",
                    "process_availability_075",
                ),
            ),
            (
                "harvest",
                (
                    "harvest_recovery_035",
                    "harvest_recovery_060",
                    "harvest_recovery_075",
                ),
            ),
        ):
            for availability_case in cases:
                province_rows = [
                    row
                    for row in curve
                    if row["scenario"] == case
                    and row["geography_level"] == "province"
                    and row["stream"] == stream
                    and row["availability_case"] == availability_case
                ]
                national = [
                    row
                    for row in curve
                    if row["scenario"] == case
                    and row["geography_level"] == "national"
                    and row["stream"] == stream
                    and row["availability_case"] == availability_case
                ]
                if len(national) != 1:
                    raise ForestryOsecanHandoffV10Error(
                        f"{case}/{stream}/{availability_case}: national row"
                    )
                national_pj = float(national[0]["residue_pj"])
                national_odt = float(national[0]["residue_odt"])
                zone_values: dict[str, list[float]] = {}
                for province_row in province_rows:
                    province = province_row["province"].strip().upper()
                    odt = float(province_row["residue_odt"])
                    pj = float(province_row["residue_pj"])
                    for (weight_province, zone_id), (
                        zone_name,
                        weight,
                    ) in weights.items():
                        if weight_province != province:
                            continue
                        entry = zone_values.setdefault(
                            zone_id, [0.0, 0.0, zone_name]
                        )
                        entry[0] += odt * weight
                        entry[1] += pj * weight
                for zone_id in sorted(zone_values):
                    odt, pj, zone_name = zone_values[zone_id]
                    case_rows.append(
                        {
                            "scenario": case,
                            "zone_id": zone_id,
                            "zone_name": zone_name,
                            "stream": stream,
                            "availability_case": availability_case,
                            "zone_mapping_status": "allocated",
                            "provinces_included": "|".join(
                                sorted(
                                    {
                                        row["province"]
                                        for row in province_rows
                                        if (row["province"], zone_id) in weights
                                    }
                                )
                            ),
                            "residue_odt": f"{odt:.9f}",
                            "pj": f"{pj:.9f}",
                            "national_case_odt": f"{national_odt:.9f}",
                            "national_case_pj": f"{national_pj:.9f}",
                            "share_of_national_case": f"{pj / national_pj:.12f}"
                            if national_pj
                            else "0.000000000000",
                            "supply_curve_tier_at_national_case": _tier(national_pj, tiers),
                            "model_estimate": "true",
                            "observed": "false",
                            "policy_grade": "false",
                            "official_osecan_changed": "false",
                        }
                    )
                tier_rows.append(
                    {
                        "scenario": case,
                        "stream": stream,
                        "case": availability_case,
                        "national_pj": f"{national_pj:.9f}",
                        "tier": _tier(national_pj, tiers),
                        "model_estimate": "true",
                        "observed": "false",
                        "policy_grade": "false",
                        "national_claim": "false",
                    }
                )
    return case_rows, tier_rows


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build handoff v10 and its 100/119/150 case zone summary."""

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    saved = {name: getattr(_v9, name) for name in _SAVED_NAMES}
    try:
        for name in _SAVED_NAMES:
            setattr(_v9, name, globals()[name])
        summary = _v9.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v9, name, value)

    case_rows, tier_rows = _zone_rows(summary)
    _write_csv(target / "case_zone_summary.csv", CASE_FIELDS, case_rows)
    _write_csv(target / "case_tier_positions.csv", TIER_FIELDS, tier_rows)

    # 119 case-level guard against handoff v9.
    v9_zones = {
        (row["zone_id"], row["stream"], row["availability_case"]): float(row["pj"])
        for row in _read_rows(_v9.OUTPUT_DIR / "zone_summary.csv")
    }
    max_gap = 0.0
    for row in case_rows:
        if row["scenario"] != PRIMARY_CASE:
            continue
        if row["availability_case"] == "process_availability_000":
            continue
        key = (
            row["zone_id"],
            row["stream"],
            row["availability_case"],
        )
        max_gap = max(max_gap, abs(float(row["pj"]) - v9_zones[key]))
    if max_gap > 1e-6:
        raise ForestryOsecanHandoffV10Error(
            f"119 case zone summary does not reproduce v9: {max_gap!r} PJ"
        )

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["assignment"] = "assignment v10 node_transport_aware (all cases)"
    summary["case_zone_summary"] = {
        "csv": "case_zone_summary.csv",
        "tier_csv": "case_tier_positions.csv",
        "cases": list(CASES),
        "primary_case": PRIMARY_CASE,
        "rows": len(case_rows),
        "tier_rows": len(tier_rows),
        "note": (
            "The same province-to-zone proxy weights are applied to the "
            "100/119/150 Mm3 curve. The 119 Mm3 zone totals reproduce "
            "handoff v9."
        ),
    }
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "transport_cost_in_osecan": False,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "The 100/150 Mm3 case zone totals use the same province-to-zone "
            "weights as 119 Mm3; they are not observed sub-provincial shares."
        ),
        (
            "Transport cost is not part of the OSeCAN handoff or its official "
            "supply-curve tiers."
        ),
    ]
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = target / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 9", "Version 10")
        text += (
            "\n## 100/119/150 case zone handoff\n\n"
            "The same all-zone province weights are applied to curve v9 at "
            "each harvest case. The 119 Mm3 zone totals reproduce handoff v9. "
            "Transport cost is not an OSeCAN input.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest_path = target / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["case_zone_summary"] = summary["case_zone_summary"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(target.iterdir())
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
                "case_zone_rows": summary["case_zone_summary"]["rows"],
                "official_osecan_changed": summary["labels"][
                    "official_osecan_changed"
                ],
                "validation": summary["validation"]["passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
