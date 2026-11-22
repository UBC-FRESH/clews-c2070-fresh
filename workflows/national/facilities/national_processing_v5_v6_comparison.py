"""Compare assignment v5 processing with assignment v6 local-processing.

The comparison crosses four artifacts:

* ``national_facility_assignment_v5`` / ``_v6`` for assigned volume, local and
  cross-border shares, proxy share, concentration, and capacity shortfall;
* ``facility_residue_supply_v2`` / ``_v3`` for province process residue;
* ``forestry_osecan_handoff_v3`` / ``_v4`` for the 119 Mm3 zone PJ cases
  (process availability 50%, harvest recovery 60%).  These are the
  facility-basis zone handoffs; the multi-scenario ``_v2`` / ``_v3`` artifacts
  are validated separately because their chain province totals are
  origin-based and therefore do not move with receiving-facility location.

Every value is a model estimate: ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, ``official_osecan_changed=false``.  No official OSeCAN
or UI surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"

ASSIGNMENT_V5_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v5"
)
ASSIGNMENT_V6_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v6"
)
RESIDUE_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v2"
)
RESIDUE_V3_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v3"
)
SCENARIOS_V2_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v2"
)
SCENARIOS_V3_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_scenarios_v3"
)
HANDOFF_V3_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v3"
)
HANDOFF_V4_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v4"
)

OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_processing_v5_v6_comparison"
)
ARTIFACT = "national_processing_v5_v6_comparison"
ARTIFACT_VERSION = "1.0.0"
REPORT_NAME = "NATIONAL_PROCESSING_V5_V6_COMPARISON.md"
MODULE_PATH = (
    "workflows.national.facilities.national_processing_v5_v6_comparison"
)
TEST_PATH = "tests/test_national_processing_v5_v6_comparison.py"

V5_SCENARIO = "published_plus_proxy"
V6_SCENARIO = "province_capacity_floor"
SCENARIO_CASE = "119m"
PROCESS_CASE = "process_availability_050"
HARVEST_CASE = "harvest_recovery_060"
ZONE_ORDER = (
    "B1",
    "B2",
    "A1",
    "A2",
    "S1",
    "M1",
    "O1",
    "O2",
    "Q1",
    "Q2",
    "T1",
    "L1",
    "N1",
)
ASSIGNMENT_FIELDS = [
    "metric",
    "unit",
    "v5_value",
    "v6_value",
    "delta_v6_minus_v5",
    "note",
]
PROVINCE_FIELDS = [
    "province",
    "v5_process_residue_odt",
    "v6_process_residue_odt",
    "delta_odt",
    "v5_process_residue_pj",
    "v6_process_residue_pj",
    "delta_pj",
    "v5_proxy_residue_share",
    "v6_proxy_residue_share",
    "v6_scaled_proxy_residue_share",
]
ZONE_FIELDS = [
    "zone_id",
    "zone_name",
    "stream",
    "availability_case",
    "v5_pj",
    "v6_pj",
    "delta_pj",
    "v5_share_of_national_case",
    "v6_share_of_national_case",
]
SHORTFALL_FIELDS = [
    "province",
    "harvest_m3",
    "v5_published_capacity_m3_y",
    "v6_published_capacity_m3_y",
    "v5_proxy_capacity_m3_y",
    "v6_proxy_capacity_m3_y",
    "v5_proxy_scale_factor",
    "v6_proxy_scale_factor",
    "v5_capacity_shortfall_m3_y",
    "v6_capacity_shortfall_m3_y",
    "delta_shortfall_m3_y",
]


class ProcessingComparisonError(RuntimeError):
    """Raised when a comparison invariant fails."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ProcessingComparisonError(f"missing input: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path, required: set[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ProcessingComparisonError(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ProcessingComparisonError(
                f"{path}: missing columns {sorted(missing)}"
            )
        return [dict(row) for row in reader]


def _number(value: str, context: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ProcessingComparisonError(f"{context}: non-finite value")
    return number


def _format(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.9f}".rstrip("0").rstrip(".") or "0"


def _assert_no_unexpected_deletions() -> None:
    for root in (REPO_ROOT, REPO_ROOT / "data"):
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ProcessingComparisonError(
                f"cannot read git status in {root}: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise ProcessingComparisonError(
                    f"unexpected deletion in {root}: {line!r}; halting"
                )


def _assignment_metrics(
    v5_summary: dict[str, Any],
    v6_summary: dict[str, Any],
    v5_rows: list[dict[str, str]],
    v6_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if v5_summary.get("artifact") != "national_facility_assignment_v5":
        raise ProcessingComparisonError("unexpected v5 assignment artifact")
    if v6_summary.get("artifact") != "national_facility_assignment_v6":
        raise ProcessingComparisonError("unexpected v6 assignment artifact")
    v5 = v5_summary["scenarios"][V5_SCENARIO]
    v6 = v6_summary["scenarios"][V6_SCENARIO]
    v5_assigned = float(v5["assigned_m3"])
    v6_assigned = float(v6["assigned_m3"])
    if abs(v5_assigned - v6_assigned) > 0.01:
        raise ProcessingComparisonError("v5 and v6 assigned totals differ")
    v5_cross = float(v5["cross_border_m3"])
    v6_cross = float(v6["cross_border_m3"])
    v5_local = v5_assigned - v5_cross
    v6_local = v6_assigned - v6_cross
    v5_proxy_share = float(v5["proxy_share_of_assigned"])
    v6_proxy_share = float(v6["proxy_share_of_assigned"])
    v5_scaled = math.fsum(
        _number(row["assigned_m3"], "v5 scaled assignment")
        for row in v5_rows
        if row["scenario"] == V5_SCENARIO
        and row["capacity_status"] == "proxy_harvest_scaled"
    )
    v6_scaled = math.fsum(
        _number(row["assigned_m3"], "v6 scaled assignment")
        for row in v6_rows
        if row["scenario"] == V6_SCENARIO
        and row["capacity_status"] == "proxy_harvest_scaled"
    )
    shortfall_before = float(
        v6_summary["province_capacity_floor"]["shortfall_before_m3_y"]
    )
    shortfall_after = float(
        v6_summary["province_capacity_floor"]["shortfall_after_m3_y"]
    )
    below_before = int(
        v6_summary["province_capacity_floor"]["provinces_below_floor_before"]
    )
    below_after = int(
        v6_summary["province_capacity_floor"]["provinces_below_floor_after"]
    )
    values: dict[str, float] = {
        "assigned_m3": v6_assigned,
        "local_m3": v6_local,
        "local_share": v6_local / v6_assigned,
        "cross_border_m3": v6_cross,
        "cross_border_share": float(v6["cross_border_share"]),
        "published_m3": float(v6["published_m3"]),
        "published_share": 1.0 - v6_proxy_share,
        "proxy_m3": float(v6["proxy_m3"]),
        "proxy_share_of_assigned": v6_proxy_share,
        "scaled_proxy_m3": v6_scaled,
        "facilities_used": float(v6["facilities_used"]),
        "max_facility_m3": float(v6["max_facility_m3"]),
        "max_facility_share": float(v6["max_facility_share"]),
        "top5_share": float(v6["top5_share"]),
        "hhi": float(v6["hhi"]),
        "capacity_shortfall_m3_y": shortfall_after,
        "provinces_below_floor": float(below_after),
    }
    baseline: dict[str, float] = {
        "assigned_m3": v5_assigned,
        "local_m3": v5_local,
        "local_share": v5_local / v5_assigned,
        "cross_border_m3": v5_cross,
        "cross_border_share": float(v5["cross_border_share"]),
        "published_m3": float(v5["published_m3"]),
        "published_share": 1.0 - v5_proxy_share,
        "proxy_m3": float(v5["proxy_m3"]),
        "proxy_share_of_assigned": v5_proxy_share,
        "scaled_proxy_m3": v5_scaled,
        "facilities_used": float(v5["facilities_used"]),
        "max_facility_m3": float(v5["max_facility_m3"]),
        "max_facility_share": float(v5["max_facility_share"]),
        "top5_share": float(v5["top5_share"]),
        "hhi": float(v5["hhi"]),
        "capacity_shortfall_m3_y": shortfall_before,
        "provinces_below_floor": float(below_before),
    }
    notes = {
        "assigned_m3": "volume assigned to facilities",
        "local_m3": "volume assigned inside the origin province",
        "local_share": "local volume / assigned volume",
        "cross_border_m3": "assigned to an out-of-province facility",
        "cross_border_share": "cross-border / assigned",
        "published_m3": "assigned volume on published capacity",
        "published_share": "published volume / assigned",
        "proxy_m3": "assigned volume on proxy capacity",
        "proxy_share_of_assigned": "proxy volume / assigned",
        "scaled_proxy_m3": "assigned on floor-scaled proxy capacity",
        "facilities_used": "distinct facilities receiving volume",
        "max_facility_m3": "largest single-facility assignment",
        "max_facility_share": "largest facility / assigned",
        "top5_share": "top five facilities / assigned",
        "hhi": "sum of squared facility shares",
        "capacity_shortfall_m3_y": (
            "capacity below harvest x 1.2 (v5 unscaled, v6 after floor)"
        ),
        "provinces_below_floor": "provinces below the 1.2x harvest floor",
    }
    units = {
        "assigned_m3": "m3/yr",
        "local_m3": "m3/yr",
        "local_share": "share",
        "cross_border_m3": "m3/yr",
        "cross_border_share": "share",
        "published_m3": "m3/yr",
        "published_share": "share",
        "proxy_m3": "m3/yr",
        "proxy_share_of_assigned": "share",
        "scaled_proxy_m3": "m3/yr",
        "facilities_used": "count",
        "max_facility_m3": "m3/yr",
        "max_facility_share": "share",
        "top5_share": "share",
        "hhi": "index_0_1",
        "capacity_shortfall_m3_y": "m3/yr",
        "provinces_below_floor": "count",
    }
    rows = [
        {
            "metric": metric,
            "unit": units[metric],
            "v5_value": _format(baseline[metric]),
            "v6_value": _format(values[metric]),
            "delta_v6_minus_v5": _format(values[metric] - baseline[metric]),
            "note": notes[metric],
        }
        for metric in values
    ]
    return rows, values


def _province_rows(
    v5_rows: list[dict[str, str]],
    v6_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    def process(frame: list[dict[str, str]]) -> dict[str, dict[str, str]]:
        selected = {
            row["province"]: row
            for row in frame
            if row["residue_type"] == "process_residue"
        }
        if not selected:
            raise ProcessingComparisonError("no process residue rows")
        return selected

    v5 = process(v5_rows)
    v6 = process(v6_rows)
    if set(v5) != set(v6):
        raise ProcessingComparisonError("province process sets differ")
    rows: list[dict[str, Any]] = []
    for province in v5:
        old = v5[province]
        new = v6[province]
        old_odt = _number(old["residue_odt"], f"{province} v5 odt")
        new_odt = _number(new["residue_odt"], f"{province} v6 odt")
        old_pj = _number(old["residue_pj"], f"{province} v5 pj")
        new_pj = _number(new["residue_pj"], f"{province} v6 pj")
        rows.append(
            {
                "province": province,
                "v5_process_residue_odt": _format(old_odt),
                "v6_process_residue_odt": _format(new_odt),
                "delta_odt": _format(new_odt - old_odt),
                "v5_process_residue_pj": _format(old_pj),
                "v6_process_residue_pj": _format(new_pj),
                "delta_pj": _format(new_pj - old_pj),
                "v5_proxy_residue_share": old["proxy_residue_share"],
                "v6_proxy_residue_share": new["proxy_residue_share"],
                "v6_scaled_proxy_residue_share": new[
                    "scaled_proxy_residue_share"
                ],
            }
        )
    return rows


def _zone_rows(
    v5_rows: list[dict[str, str]],
    v6_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    def select(frame: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
        selected = {
            (row["zone_id"], row["stream"]): row
            for row in frame
            if (
                (row["stream"] == "process" and row["availability_case"] == PROCESS_CASE)
                or (
                    row["stream"] == "harvest"
                    and row["availability_case"] == HARVEST_CASE
                )
            )
        }
        expected = {
            (zone, stream)
            for zone in ZONE_ORDER
            for stream in ("process", "harvest")
        }
        if set(selected) != expected:
            raise ProcessingComparisonError(
                "zone case coverage mismatch: "
                f"missing={sorted(expected - set(selected))}, "
                f"extra={sorted(set(selected) - expected)}"
            )
        return selected

    old = select(v5_rows)
    new = select(v6_rows)
    rows: list[dict[str, Any]] = []
    for zone in ZONE_ORDER:
        for stream in ("process", "harvest"):
            key = (zone, stream)
            v5 = old[key]
            v6 = new[key]
            old_pj = _number(v5["pj"], f"{zone}/{stream} v5 pj")
            new_pj = _number(v6["pj"], f"{zone}/{stream} v6 pj")
            rows.append(
                {
                    "zone_id": zone,
                    "zone_name": v6["zone_name"],
                    "stream": stream,
                    "availability_case": v6["availability_case"],
                    "v5_pj": _format(old_pj),
                    "v6_pj": _format(new_pj),
                    "delta_pj": _format(new_pj - old_pj),
                    "v5_share_of_national_case": v5[
                        "share_of_national_case"
                    ],
                    "v6_share_of_national_case": v6[
                        "share_of_national_case"
                    ],
                }
            )
    return rows


def _shortfall_rows(
    v6_capacity_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in v6_capacity_rows:
        rows.append(
            {
                "province": row["province"],
                "harvest_m3": row["harvest_m3"],
                "v5_published_capacity_m3_y": row[
                    "v5_published_capacity_m3_y"
                ],
                "v6_published_capacity_m3_y": row[
                    "v6_published_capacity_m3_y"
                ],
                "v5_proxy_capacity_m3_y": row[
                    "v5_proxy_capacity_before_m3_y"
                ],
                "v6_proxy_capacity_m3_y": row[
                    "v6_proxy_capacity_after_m3_y"
                ],
                "v5_proxy_scale_factor": row["v5_proxy_scale_factor"],
                "v6_proxy_scale_factor": row["v6_proxy_scale_factor"],
                "v5_capacity_shortfall_m3_y": row[
                    "v5_capacity_shortfall_m3_y"
                ],
                "v6_capacity_shortfall_m3_y": row[
                    "v6_capacity_shortfall_m3_y"
                ],
                "delta_shortfall_m3_y": row["delta_shortfall_m3_y"],
            }
        )
    return rows


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _report(summary: dict[str, Any]) -> str:
    assignment = {
        row["metric"]: row for row in summary["assignment_metrics"]
    }
    lines = [
        "# National Processing V5 to V6 Comparison",
        "",
        (
            "Compares assignment v5 `published_plus_proxy` with assignment v6 "
            "`province_capacity_floor`, plus province process residue and the "
            "119 Mm3 OSeCAN zone cases. Every value is a model estimate: "
            "`observed=false`, `policy_grade=false`, "
            "`official_osecan_changed=false`."
        ),
        "",
        "## Assignment metrics",
        "",
        "| Metric | V5 | V6 | Delta v6-v5 |",
        "|---|---|---|---|",
    ]
    for metric in (
        "assigned_m3",
        "local_m3",
        "local_share",
        "cross_border_m3",
        "cross_border_share",
        "proxy_m3",
        "proxy_share_of_assigned",
        "scaled_proxy_m3",
        "hhi",
        "max_facility_m3",
        "max_facility_share",
        "capacity_shortfall_m3_y",
        "provinces_below_floor",
    ):
        row = assignment[metric]
        lines.append(
            f"| {metric} | {row['v5_value']} | {row['v6_value']} | "
            f"{row['delta_v6_minus_v5']} |"
        )
    lines += [
        "",
        "## Province process residue",
        "",
        "| Province | V5 odt | V6 odt | Delta odt | V5 PJ | V6 PJ | Delta PJ |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in summary["province_process_residue"]:
        lines.append(
            f"| {row['province']} | {row['v5_process_residue_odt']} | "
            f"{row['v6_process_residue_odt']} | {row['delta_odt']} | "
            f"{row['v5_process_residue_pj']} | {row['v6_process_residue_pj']} | "
            f"{row['delta_pj']} |"
        )
    lines += [
        "",
        "## 119 Mm3 zone PJ",
        "",
        "| Zone | Stream | V5 PJ | V6 PJ | Delta PJ |",
        "|---|---|---|---|---|",
    ]
    for row in summary["zone_119m_cases"]:
        lines.append(
            f"| {row['zone_id']} | {row['stream']} | {row['v5_pj']} | "
            f"{row['v6_pj']} | {row['delta_pj']} |"
        )
    lines += [
        "",
        "## Capacity shortfall",
        "",
        "| Province | V5 shortfall | V6 shortfall | Delta |",
        "|---|---|---|---|",
    ]
    for row in summary["capacity_shortfall"]:
        lines.append(
            f"| {row['province']} | {row['v5_capacity_shortfall_m3_y']} | "
            f"{row['v6_capacity_shortfall_m3_y']} | "
            f"{row['delta_shortfall_m3_y']} |"
        )
    lines += [
        "",
        "## Validation",
        "",
        *[f"- `{key}`: {value}" for key, value in summary["validation"].items()],
        "",
        "## Caveats",
        "",
        *[f"- {caveat}" for caveat in summary["caveats"]],
        "",
    ]
    return "\n".join(lines)


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    """Build the v5/v6 comparison artifact and return its summary."""
    _assert_no_unexpected_deletions()
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    v5_assignment = _read_json(ASSIGNMENT_V5_DIR / "summary.json")
    v6_assignment = _read_json(ASSIGNMENT_V6_DIR / "summary.json")
    v2_residue = _read_json(RESIDUE_V2_DIR / "summary.json")
    v3_residue = _read_json(RESIDUE_V3_DIR / "summary.json")
    v2_scenarios = _read_json(SCENARIOS_V2_DIR / "summary.json")
    v3_scenarios = _read_json(SCENARIOS_V3_DIR / "summary.json")
    v3_handoff = _read_json(HANDOFF_V3_DIR / "summary.json")
    v4_handoff = _read_json(HANDOFF_V4_DIR / "summary.json")
    if v2_residue.get("artifact") != "facility_residue_supply_v2":
        raise ProcessingComparisonError("unexpected residue v2 artifact")
    if v3_residue.get("artifact") != "facility_residue_supply_v3":
        raise ProcessingComparisonError("unexpected residue v3 artifact")
    if v2_scenarios.get("artifact") != "forestry_osecan_scenarios_v2":
        raise ProcessingComparisonError("unexpected scenarios v2 artifact")
    if v3_scenarios.get("artifact") != "forestry_osecan_scenarios_v3":
        raise ProcessingComparisonError("unexpected scenarios v3 artifact")
    if v3_handoff.get("artifact") != "forestry_osecan_handoff_v3":
        raise ProcessingComparisonError("unexpected handoff v3 artifact")
    if v4_handoff.get("artifact") != "forestry_osecan_handoff_v4":
        raise ProcessingComparisonError("unexpected handoff v4 artifact")

    assignment_rows = _read_rows(
        ASSIGNMENT_V5_DIR / "facility_assignment.csv",
        {
            "scenario",
            "capacity_status",
            "assigned_m3",
        },
    )
    v6_assignment_rows = _read_rows(
        ASSIGNMENT_V6_DIR / "facility_assignment.csv",
        {
            "scenario",
            "capacity_status",
            "assigned_m3",
        },
    )
    assignment_metrics, v6_values = _assignment_metrics(
        v5_assignment, v6_assignment, assignment_rows, v6_assignment_rows
    )
    province_rows = _province_rows(
        _read_rows(
            RESIDUE_V2_DIR / "province_residue_supply.csv",
            {
                "province",
                "residue_type",
                "residue_odt",
                "residue_pj",
                "proxy_residue_share",
            },
        ),
        _read_rows(
            RESIDUE_V3_DIR / "province_residue_supply.csv",
            {
                "province",
                "residue_type",
                "residue_odt",
                "residue_pj",
                "proxy_residue_share",
                "scaled_proxy_residue_share",
            },
        ),
    )
    zone_rows = _zone_rows(
        _read_rows(
            HANDOFF_V3_DIR / "zone_summary.csv",
            {
                "zone_id",
                "zone_name",
                "stream",
                "availability_case",
                "pj",
                "share_of_national_case",
            },
        ),
        _read_rows(
            HANDOFF_V4_DIR / "zone_summary.csv",
            {
                "zone_id",
                "zone_name",
                "stream",
                "availability_case",
                "pj",
                "share_of_national_case",
            },
        ),
    )
    shortfall_rows = _shortfall_rows(
        _read_rows(
            ASSIGNMENT_V6_DIR / "province_capacity_comparison.csv",
            {
                "province",
                "harvest_m3",
                "v5_published_capacity_m3_y",
                "v6_published_capacity_m3_y",
                "v5_proxy_capacity_before_m3_y",
                "v6_proxy_capacity_after_m3_y",
                "v5_proxy_scale_factor",
                "v6_proxy_scale_factor",
                "v5_capacity_shortfall_m3_y",
                "v6_capacity_shortfall_m3_y",
                "delta_shortfall_m3_y",
            },
        )
    )

    v5_process = {
        row["province"]: _number(
            row["v5_process_residue_odt"], f"{row['province']} v5 process odt"
        )
        for row in province_rows
    }
    v6_process = {
        row["province"]: _number(
            row["v6_process_residue_odt"], f"{row['province']} v6 process odt"
        )
        for row in province_rows
    }
    if abs(
        math.fsum(v5_process.values())
        - float(v2_residue["national_totals"]["process_residue_odt"])
    ) > 1e-3:
        raise ProcessingComparisonError("v5 province process total mismatch")
    if abs(
        math.fsum(v6_process.values())
        - float(v3_residue["national_totals"]["process_residue_odt"])
    ) > 1e-3:
        raise ProcessingComparisonError("v6 province process total mismatch")
    if _number(
        next(
            row["v6_value"]
            for row in assignment_metrics
            if row["metric"] == "scaled_proxy_m3"
        ),
        "v6 scaled proxy",
    ) != v6_values["scaled_proxy_m3"]:
        raise ProcessingComparisonError("scaled proxy metric mismatch")

    for stream, case, key in (
        ("process", PROCESS_CASE, "process"),
        ("harvest", HARVEST_CASE, "harvest"),
    ):
        total = math.fsum(
            _number(row["v6_pj"], f"{row['zone_id']}/{stream} v6 pj")
            for row in zone_rows
            if row["stream"] == stream
        )
        expected = float(v4_handoff["handoff_totals"][key][case])
        if abs(total - expected) > 1e-6:
            raise ProcessingComparisonError(
                f"{stream} zone total does not reproduce handoff v4"
            )
        scenario_expected = float(
            v3_scenarios["national_case_totals"][SCENARIO_CASE][key][case]["pj"]
        )
        if abs(expected - scenario_expected) > 1e-6:
            raise ProcessingComparisonError(
                f"{stream} handoff v4 national total differs from scenarios v3"
            )

    def labels_ok(artefact: dict[str, Any]) -> bool:
        labels = artefact.get("labels") or artefact
        return (
            labels.get("observed") is False
            and labels.get("policy_grade") is False
        )

    if not all(
        labels_ok(artefact)
        for artefact in (
            v5_assignment,
            v6_assignment,
            v3_residue,
            v3_scenarios,
            v4_handoff,
        )
    ):
        raise ProcessingComparisonError("an input is not a screening label")

    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            ASSIGNMENT_V5_DIR / "summary.json",
            ASSIGNMENT_V6_DIR / "summary.json",
            RESIDUE_V2_DIR / "province_residue_supply.csv",
            RESIDUE_V3_DIR / "province_residue_supply.csv",
            SCENARIOS_V3_DIR / "summary.json",
            HANDOFF_V3_DIR / "zone_summary.csv",
            HANDOFF_V4_DIR / "zone_summary.csv",
            ASSIGNMENT_V6_DIR / "province_capacity_comparison.csv",
        )
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "generated_utc": generated_utc,
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "official_osecan_changed": False,
        },
        "scenario_case": SCENARIO_CASE,
        "process_availability_case": PROCESS_CASE,
        "harvest_recovery_case": HARVEST_CASE,
        "assignment_metrics": assignment_metrics,
        "province_process_residue": province_rows,
        "zone_119m_cases": zone_rows,
        "zone_source": "forestry_osecan_handoff_v3_vs_v4",
        "capacity_shortfall": shortfall_rows,
        "scenarios_v3": {
            "artifact": v3_scenarios["artifact"],
            "record_counts": v3_scenarios["record_counts"],
            "note": (
                "The 100/119/150 scenarios chain uses origin-province process "
                "totals; v6 changes receiving-facility location, so those "
                "origin totals do not move. The facility-basis zone deltas are "
                "reported from the handoff v3/v4 pair."
            ),
        },
        "inputs": inputs,
        "validation": {
            "assigned_total_equal_v5_v6": True,
            "province_process_v5_conserves": True,
            "province_process_v6_conserves": True,
            "zone_119m_reproduces_handoff_v4": True,
            "handoff_v4_matches_scenarios_v3_national_totals": True,
            "labels": True,
            "no_unexpected_deletions": True,
        },
        "caveats": [
            (
                "All values are model estimates; `observed=false` and "
                "`policy_grade=false`."
            ),
            (
                "The 119 Mm3 case is a planning allocation, not a legal AAC, "
                "a sustainable-supply determination, or observed harvest."
            ),
            (
                "Assignment v6 applies the 1.2x provincial floor by scaling "
                "proxy capacity only. Scaled capacity is not observed capacity."
            ),
            (
                "Process residue totals are invariant; v6 changes their "
                "province/facility distribution through local-first allocation."
            ),
            (
                "Zone PJ splits use the fixed handoff-v4 proxy weights. They "
                "are not observed sub-provincial residue shares."
            ),
            "No UI or public OSeCAN surface is changed.",
        ],
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "assignment_metrics_comparison.csv",
        ASSIGNMENT_FIELDS,
        assignment_metrics,
    )
    _write_csv(
        output_dir / "province_process_residue_comparison.csv",
        PROVINCE_FIELDS,
        province_rows,
    )
    _write_csv(
        output_dir / "zone_119m_case_comparison.csv",
        ZONE_FIELDS,
        zone_rows,
    )
    _write_csv(
        output_dir / "capacity_shortfall_comparison.csv",
        SHORTFALL_FIELDS,
        shortfall_rows,
    )
    (output_dir / REPORT_NAME).write_text(_report(summary), encoding="utf-8")
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": inputs,
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = build()
    assignment = {
        row["metric"]: row for row in summary["assignment_metrics"]
    }
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "cross_border_m3": assignment["cross_border_m3"],
                "local_share": assignment["local_share"],
                "proxy_share_of_assigned": assignment[
                    "proxy_share_of_assigned"
                ],
                "hhi": assignment["hhi"],
                "max_facility_share": assignment["max_facility_share"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
