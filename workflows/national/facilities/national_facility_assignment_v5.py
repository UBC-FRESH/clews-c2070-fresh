"""Assign the 119 Mm3 harvest case with capacity v3.

Assignment v5 reruns the v4 local-first bounded allocation on inventory v1 /
capacity v3.  v3 adds Quebec MRNF 2026 permitted mill volumes and five
per-mill Ontario/Alberta company disclosures, so the published-capacity base
is wider and the proxy base is smaller than v4.

Scenarios, distance penalties, the 1.2x provincial floor, and the finite
capacity rule are unchanged from v4.  Every assignment row carries a finite
capacity; there is no unbounded sink.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
legal or AAC claim is made.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v2 as a2,
)
from workflows.national.facilities import (
    national_facility_assignment_v3 as a3,
)
from workflows.national.facilities import (
    national_facility_assignment_v4 as v4,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
    Location,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPACITY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v3"
    / "national_facility_capacity_v3.csv"
)
HARVEST_CSV = (
    REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BOUNDARY_GEOJSON = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
V4_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v4"
    / "summary.json"
)
CAPACITY_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v3"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v5"
)

ARTIFACT = "national_facility_assignment_v5"
ARTIFACT_VERSION = "5.0.0"
SCENARIOS = a3.SCENARIOS
PRIMARY_SCENARIO = "published_plus_proxy"
FLOOR_SCENARIO = "province_capacity_floor"
FLOOR_MULTIPLIER = a3.FLOOR_MULTIPLIER
RELEVANT_CLASSES = a3.RELEVANT_CLASSES

PUBLISHED = a3.PUBLISHED
PROXY = a3.PROXY
SCALED_PROXY = a3.SCALED_PROXY
NOT_PUBLISHED = a3.NOT_PUBLISHED
PROXY_EVIDENCE = a3.PROXY_EVIDENCE
SCALED_PROXY_EVIDENCE = a3.SCALED_PROXY_EVIDENCE
EVIDENCE_LABEL = a3.EVIDENCE_LABEL
OBSERVED = a3.OBSERVED
POLICY_GRADE = a3.POLICY_GRADE
ROAD_FACTOR = a3.ROAD_FACTOR
CROSS_BORDER_PENALTY_KM = a3.CROSS_BORDER_PENALTY_KM

PUBLISHED_EVIDENCE = frozenset(
    {
        "published_mills_db_2015_infeed_proxy",
        "published_capacity_converted",
        "published_authorized_consumption",
        "published_sawn_output_converted",
    }
)

COMPARISON_METRICS = v4.COMPARISON_METRICS
COMPARISON_FIELDS = [
    "scenario",
    "metric",
    "unit",
    "v4_value",
    "v5_value",
    "delta_v5_minus_v4",
    "note",
]
PROVINCE_CAPACITY_FIELDS = [
    "province",
    "harvest_m3",
    "v4_published_capacity_m3_y",
    "v5_published_capacity_m3_y",
    "delta_published_m3_y",
    "v4_proxy_capacity_before_m3_y",
    "v5_proxy_capacity_before_m3_y",
    "delta_proxy_before_m3_y",
    "v4_total_capacity_before_m3_y",
    "v5_total_capacity_before_m3_y",
    "delta_total_before_m3_y",
    "v4_proxy_scale_factor",
    "v5_proxy_scale_factor",
    "v4_proxy_capacity_after_m3_y",
    "v5_proxy_capacity_after_m3_y",
    "delta_proxy_after_m3_y",
    "v4_total_capacity_after_m3_y",
    "v5_total_capacity_after_m3_y",
    "delta_total_after_m3_y",
    "v4_capacity_shortfall_m3_y",
    "v5_capacity_shortfall_m3_y",
    "delta_shortfall_m3_y",
]
ASSIGNMENT_FIELDS = a3.ASSIGNMENT_FIELDS
PROVINCE_SUMMARY_FIELDS = a3.PROVINCE_SUMMARY_FIELDS
UTILIZATION_FIELDS = a3.UTILIZATION_FIELDS
UNASSIGNED_FIELDS = a3.UNASSIGNED_FIELDS
PROVINCE_CLASS_FIELDS = a3.PROVINCE_CLASS_FIELDS
CONCENTRATION_FIELDS = a3.CONCENTRATION_FIELDS
FLOOR_FIELDS = a3.FLOOR_FIELDS
FLOOR_FACILITY_FIELDS = a3.FLOOR_FACILITY_FIELDS


class AssignmentV5Error(a3.AssignmentV3Error):
    """Raised when a capacity-v3 allocation invariant fails."""


def _assert_no_unexpected_deletions() -> None:
    v4._assert_no_unexpected_deletions()


def _optional_float(value: str, *, context: str) -> float | None:
    if value == "":
        return None
    number = float(value)
    if not math.isfinite(number):
        raise AssignmentV5Error(f"{context}: non-finite value {value!r}")
    return number


def read_facilities(path: Path = CAPACITY_CSV) -> tuple[a3.Facility, ...]:
    rows = a2._read_csv(path)
    a2._require_columns(
        rows,
        {
            "facility_id",
            "name",
            "facility_class",
            "province",
            "lat",
            "lon",
            "capacity_m3_y",
            "capacity_status",
            "capacity_evidence",
            "proxy_low_m3_y",
            "proxy_high_m3_y",
            "evidence_tier",
            "source_id",
            "source_url",
            "status",
            "capacity_source_id",
            "capacity_source_url",
        },
        path,
    )
    facilities: list[a3.Facility] = []
    seen: set[str] = set()
    for position, row in enumerate(rows, start=2):
        context = f"{path}:{position}"
        facility_id = row["facility_id"].strip()
        if not facility_id:
            raise AssignmentV5Error(f"{context}: empty facility_id")
        if facility_id in seen:
            raise AssignmentV5Error(f"{context}: duplicate facility_id")
        seen.add(facility_id)
        status = row["capacity_status"]
        capacity_value = _optional_float(row["capacity_m3_y"], context=context)
        if status == PUBLISHED:
            label = row["capacity_evidence"].strip()
            if label not in PUBLISHED_EVIDENCE:
                raise AssignmentV5Error(
                    f"{context}: published evidence {label!r} not accepted"
                )
            if capacity_value is None or capacity_value <= 0.0:
                raise AssignmentV5Error(
                    f"{context}: published capacity must be positive"
                )
            capacity = a3.FloorCapacity(
                capacity_m3_y=capacity_value,
                status=PUBLISHED,
                capacity_evidence=label,
                floor_scale_factor=1.0,
            )
        elif status == PROXY:
            if row["capacity_evidence"].strip() != PROXY_EVIDENCE:
                raise AssignmentV5Error(
                    f"{context}: proxy evidence must be {PROXY_EVIDENCE!r}"
                )
            low = _optional_float(row["proxy_low_m3_y"], context=context)
            high = _optional_float(row["proxy_high_m3_y"], context=context)
            if capacity_value is None or low is None or high is None:
                raise AssignmentV5Error(
                    f"{context}: proxy capacity requires value and range"
                )
            capacity = a3.FloorCapacity(
                capacity_m3_y=capacity_value,
                status=PROXY,
                capacity_evidence=PROXY_EVIDENCE,
                floor_scale_factor=1.0,
                proxy_low_m3_y=low,
                proxy_high_m3_y=high,
            )
        elif status == NOT_PUBLISHED:
            if capacity_value not in (None, 0.0):
                raise AssignmentV5Error(
                    f"{context}: not_published row carries capacity"
                )
            capacity = None
        else:
            raise AssignmentV5Error(
                f"{context}: unexpected capacity_status {status!r}"
            )
        location = None
        if row["lat"].strip() and row["lon"].strip():
            location = Location(lat=float(row["lat"]), lon=float(row["lon"]))
        facilities.append(
            a3.Facility(
                facility_id=facility_id,
                name=row["name"].strip(),
                facility_class=row["facility_class"].strip(),
                province=row["province"].strip(),
                location=location,
                capacity=capacity,
                operating_status=row["status"].strip(),
                source_id=row["source_id"].strip(),
                source_url=row["source_url"].strip(),
                capacity_source_id=row["capacity_source_id"].strip(),
                capacity_source_url=row["capacity_source_url"].strip(),
                evidence_tier=row["evidence_tier"].strip(),
            )
        )
    return tuple(facilities)


def _comparison_rows(
    baseline: dict[str, Any], results: dict[str, a2.ScenarioResult]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        old = baseline["scenarios"][scenario]
        new = results[scenario].metrics
        for metric, unit, note in COMPARISON_METRICS:
            old_value = old.get(metric)
            new_value = new.get(metric)
            delta = (
                float(new_value) - float(old_value)
                if old_value is not None and new_value is not None
                else None
            )
            rows.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    "unit": unit,
                    "v4_value": a2._format_float(
                        float(old_value) if old_value is not None else None
                    ),
                    "v5_value": a2._format_float(
                        float(new_value) if new_value is not None else None
                    ),
                    "delta_v5_minus_v4": a2._format_float(delta),
                    "note": note,
                }
            )
    return rows


def _province_capacity_rows(
    baseline: dict[str, Any], floor_plan: a3.FloorPlan
) -> list[dict[str, Any]]:
    old_rows = {
        row["province"]: row
        for row in baseline["province_capacity_floor"]["province_rows"]
    }
    new_rows = {row["province"]: row for row in floor_plan.province_rows}
    rows: list[dict[str, Any]] = []
    for province in a2.PROVINCES:
        old = old_rows.get(province)
        new = new_rows.get(province)
        if old is None or new is None:
            raise AssignmentV5Error(
                f"{province}: missing v4 or v5 province capacity row"
            )

        def delta(field: str, old=old, new=new) -> str:
            return a2._format_float(float(new[field]) - float(old[field]))

        rows.append(
            {
                "province": province,
                "harvest_m3": old["harvest_m3"],
                "v4_published_capacity_m3_y": old["published_capacity_m3_y"],
                "v5_published_capacity_m3_y": new["published_capacity_m3_y"],
                "delta_published_m3_y": delta("published_capacity_m3_y"),
                "v4_proxy_capacity_before_m3_y": old[
                    "proxy_capacity_before_m3_y"
                ],
                "v5_proxy_capacity_before_m3_y": new[
                    "proxy_capacity_before_m3_y"
                ],
                "delta_proxy_before_m3_y": delta("proxy_capacity_before_m3_y"),
                "v4_total_capacity_before_m3_y": old[
                    "total_capacity_before_m3_y"
                ],
                "v5_total_capacity_before_m3_y": new[
                    "total_capacity_before_m3_y"
                ],
                "delta_total_before_m3_y": delta("total_capacity_before_m3_y"),
                "v4_proxy_scale_factor": old["proxy_scale_factor"],
                "v5_proxy_scale_factor": new["proxy_scale_factor"],
                "v4_proxy_capacity_after_m3_y": old[
                    "proxy_capacity_after_m3_y"
                ],
                "v5_proxy_capacity_after_m3_y": new[
                    "proxy_capacity_after_m3_y"
                ],
                "delta_proxy_after_m3_y": delta("proxy_capacity_after_m3_y"),
                "v4_total_capacity_after_m3_y": old[
                    "total_capacity_after_m3_y"
                ],
                "v5_total_capacity_after_m3_y": new[
                    "total_capacity_after_m3_y"
                ],
                "delta_total_after_m3_y": delta("total_capacity_after_m3_y"),
                "v4_capacity_shortfall_m3_y": old["capacity_shortfall_m3_y"],
                "v5_capacity_shortfall_m3_y": new["capacity_shortfall_m3_y"],
                "delta_shortfall_m3_y": delta("capacity_shortfall_m3_y"),
            }
        )
    return rows


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def _report(summary: dict[str, Any]) -> str:
    scenarios = summary["scenarios"]
    comparison = summary["comparison"]
    lines = [
        "# National Facility Assignment V5",
        "",
        (
            "Rerun of the 119 Mm3 harvest assignment on capacity v3, which "
            "adds Quebec MRNF 2026 permitted mill volumes and five per-mill "
            "Ontario/Alberta company disclosures. All values are model "
            "estimates: `observed=false`, `policy_grade=false`."
        ),
        "",
        "## Primary scenario: published plus proxy",
        "",
        *_md_table(
            ["Metric", "V4", "V5", "Delta v5-v4"],
            [
                [
                    "Assigned m3",
                    f"{summary['v4_baseline']['published_plus_proxy']['assigned_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['assigned_m3']:,.0f}",
                    f"{comparison['assigned_m3_delta']:,.0f}",
                ],
                [
                    "Published m3",
                    f"{summary['v4_baseline']['published_plus_proxy']['published_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['published_m3']:,.0f}",
                    f"{comparison['published_m3_delta']:,.0f}",
                ],
                [
                    "Proxy m3",
                    f"{summary['v4_baseline']['published_plus_proxy']['proxy_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['proxy_m3']:,.0f}",
                    f"{comparison['proxy_m3_delta']:,.0f}",
                ],
                [
                    "Proxy share of assigned",
                    f"{summary['v4_baseline']['published_plus_proxy']['proxy_share_of_assigned']:.1%}",
                    f"{scenarios['published_plus_proxy']['proxy_share_of_assigned']:.1%}",
                    f"{comparison['proxy_share_delta']:+.1%}",
                ],
                [
                    "Cross-border m3",
                    f"{summary['v4_baseline']['published_plus_proxy']['cross_border_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['cross_border_m3']:,.0f}",
                    f"{comparison['cross_border_delta_m3']:,.0f}",
                ],
                [
                    "Cross-border share",
                    f"{summary['v4_baseline']['published_plus_proxy']['cross_border_share']:.1%}",
                    f"{scenarios['published_plus_proxy']['cross_border_share']:.1%}",
                    f"{comparison['cross_border_share_delta']:+.1%}",
                ],
                [
                    "HHI (0-1)",
                    f"{summary['v4_baseline']['published_plus_proxy']['hhi']:.4f}",
                    f"{scenarios['published_plus_proxy']['hhi']:.4f}",
                    f"{comparison['hhi_delta']:+.4f}",
                ],
                [
                    "Largest facility share",
                    f"{summary['v4_baseline']['published_plus_proxy']['max_facility_share']:.2%}",
                    f"{scenarios['published_plus_proxy']['max_facility_share']:.2%}",
                    f"{comparison['max_facility_share_delta']:+.2%}",
                ],
            ],
        ),
        "",
        "## Provincial capacity v4 to v5",
        "",
        *_md_table(
            [
                "Province",
                "V4 published",
                "V5 published",
                "V4 proxy scale",
                "V5 proxy scale",
                "V4 shortfall",
                "V5 shortfall",
            ],
            [
                [
                    row["province"],
                    f"{float(row['v4_published_capacity_m3_y']):,.0f}",
                    f"{float(row['v5_published_capacity_m3_y']):,.0f}",
                    f"{float(row['v4_proxy_scale_factor']):.2f}",
                    f"{float(row['v5_proxy_scale_factor']):.2f}",
                    f"{float(row['v4_capacity_shortfall_m3_y']):,.0f}",
                    f"{float(row['v5_capacity_shortfall_m3_y']):,.0f}",
                ]
                for row in summary["province_capacity_comparison"]
            ],
        ),
        "",
        "## Validation",
        "",
    ]
    for key, value in summary["validation"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Caveats", ""])
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.append("")
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    v4_summary_path: Path = V4_SUMMARY_JSON,
    capacity_summary_path: Path = CAPACITY_SUMMARY,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build assignment v5 and return its summary."""

    _assert_no_unexpected_deletions()
    config = config or AllocationConfig(
        road_factor=ROAD_FACTOR,
        cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
    )
    generated_utc = a2._utc_now()
    base_facilities = read_facilities(capacity_path)
    offers, centroid_methods = a2.read_offers(harvest_path, boundary_path)
    harvest_by_province = {
        offer.province: offer.volume_m3 for offer in offers
    }
    floor_plan = a3.build_floor_plan(base_facilities, harvest_by_province)
    facilities_by_scenario = {
        "published_only": base_facilities,
        "published_plus_proxy": base_facilities,
        FLOOR_SCENARIO: floor_plan.facilities,
    }
    results = {
        scenario: a3.allocate_scenario(
            offers, facilities_by_scenario[scenario], scenario, config
        )
        for scenario in SCENARIOS
    }
    with v4_summary_path.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with capacity_summary_path.open("r", encoding="utf-8") as handle:
        capacity_summary = json.load(handle)
    if baseline.get("artifact") != "national_facility_assignment_v4":
        raise AssignmentV5Error(
            f"{v4_summary_path}: expected v4 baseline, got {baseline.get('artifact')}"
        )
    if capacity_summary.get("artifact") != "national_facility_capacity_v3":
        raise AssignmentV5Error(
            f"{capacity_summary_path}: expected capacity v3, got "
            f"{capacity_summary.get('artifact')}"
        )
    inputs = {
        str(path.relative_to(REPO_ROOT)): a2._sha256(path)
        for path in (
            capacity_path,
            harvest_path,
            boundary_path,
            v4_summary_path,
            capacity_summary_path,
        )
    }

    summary = v4._summary(
        generated_utc=generated_utc,
        base_facilities=base_facilities,
        floor_plan=floor_plan,
        offers=offers,
        centroid_methods=centroid_methods,
        results=results,
        v3_baseline=baseline,
        capacity_summary=capacity_summary,
        inputs=inputs,
    )
    comparison_rows = _comparison_rows(baseline, results)
    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["method"]["capacity_layer"] = "national_facility_capacity_v3"
    summary["method"].pop("v3_baseline", None)
    summary["method"]["v4_baseline"] = "national_facility_assignment_v4"
    summary["method"]["capacity_evidence"] = {
        "published_infeed_proxy": (
            "2015 Mills DB/FFCM processing-line infeed proxy"
        ),
        "published_converted": (
            "2025 Canadian Biomass pellet-map finished tonnes/yr converted at "
            "650 kg/m3 to finished-pellet bulk volume"
        ),
        "published_authorized": (
            "Quebec MRNF 2026 permitted annual softwood plus hardwood "
            "consumption in m3/yr"
        ),
        "published_sawn_output": (
            "Company per-mill sawn-lumber capacity converted at 2.36 m3 per "
            "thousand board feet; output basis, not log intake"
        ),
        "proxy": (
            "BC class median from published infeed-proxy records; converted "
            "pellet volumes excluded from the anchor"
        ),
    }
    summary["v4_baseline"] = summary.pop("v3_baseline")
    summary["v4_v5_comparison"] = comparison_rows
    summary.pop("v3_v4_comparison", None)
    summary["province_capacity_comparison"] = _province_capacity_rows(
        baseline, floor_plan
    )
    summary["coverage"] = capacity_summary["coverage"]
    summary["validation"]["published_evidence_accepted"] = sorted(
        PUBLISHED_EVIDENCE
    )
    summary["validation"].pop("v3_baseline_scenarios", None)
    summary["validation"]["v4_baseline_scenarios"] = list(
        baseline["scenarios"]
    )
    summary["validation"]["comparison_rows"] = len(comparison_rows)
    summary["validation"]["capacity_v3_record_count"] = capacity_summary[
        "counts"
    ]["records_total"]
    summary["caveats"] = [
        (
            "All volumes are model estimates (`model_estimate`); "
            "`observed=false` and `policy_grade=false`."
        ),
        (
            "The 119 Mm3 case is a planning allocation, not a legal AAC, a "
            "sustainable-supply determination, or an observed harvest outcome."
        ),
        (
            "Capacity is mixed-basis: 2015 Mills DB/FFCM infeed proxies, "
            "converted 2025 finished-pellet bulk volume, Quebec 2026 permitted "
            "input volume, converted company sawn-lumber output, and BC "
            "class-median proxies. The bases are not interchangeable."
        ),
        (
            "Quebec permitted consumption is a regulatory ceiling, not "
            "observed throughput. Company sawn-lumber capacities are output, "
            "not log intake, and understate roundwood consumption."
        ),
        (
            "The allocation is local-first with a 500 km cross-border penalty. "
            "Cross-border volume is a screening result, not an interprovincial "
            "trade forecast."
        ),
        (
            "The 1.2x provincial floor is a documented sensitivity assumption, "
            "not an observed capacity requirement or evidence of provincial "
            "self-sufficiency."
        ),
        (
            "No unbounded facility exists in v5: every assignment row carries "
            "a finite capacity and every relevant record has either published "
            "or proxy capacity."
        ),
        (
            "No restricted or proprietary data are used. No UI or public "
            "OSeCAN surface is changed by this artifact."
        ),
    ]
    summary["outputs"] = {
        "facility_assignment_csv": "facility_assignment.csv",
        "province_summary_csv": "province_summary.csv",
        "facility_utilization_csv": "facility_utilization.csv",
        "unassigned_csv": "unassigned.csv",
        "province_class_totals_csv": "province_class_totals.csv",
        "concentration_csv": "concentration.csv",
        "scenario_comparison_csv": "scenario_comparison.csv",
        "capacity_floor_csv": "capacity_floor.csv",
        "capacity_floor_facilities_csv": "capacity_floor_facilities.csv",
        "v4_v5_comparison_csv": "v4_v5_comparison.csv",
        "province_capacity_comparison_csv": "province_capacity_comparison.csv",
        "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V5.md",
    }
    summary["reproduce"] = (
        ".venv/bin/python -m "
        "workflows.national.facilities.national_facility_assignment_v5"
    )
    summary["centroid_methods"] = centroid_methods

    output_dir.mkdir(parents=True, exist_ok=True)
    a2._write_csv(
        output_dir / "facility_assignment.csv",
        ASSIGNMENT_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].assignments
        ],
    )
    a2._write_csv(
        output_dir / "province_summary.csv",
        PROVINCE_SUMMARY_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_summary
        ],
    )
    a2._write_csv(
        output_dir / "facility_utilization.csv",
        UTILIZATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].facility_utilization
        ],
    )
    a2._write_csv(
        output_dir / "unassigned.csv",
        UNASSIGNED_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].unassigned
        ],
    )
    a2._write_csv(
        output_dir / "province_class_totals.csv",
        PROVINCE_CLASS_FIELDS,
        summary["province_class_totals"],
    )
    a2._write_csv(
        output_dir / "concentration.csv",
        CONCENTRATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].concentration
        ],
    )
    a2._write_csv(
        output_dir / "scenario_comparison.csv",
        a3.COMPARISON_FIELDS,
        [
            {
                field: summary["scenarios"][scenario].get(field)
                for field in a3.COMPARISON_FIELDS
            }
            for scenario in SCENARIOS
        ],
    )
    a2._write_csv(
        output_dir / "capacity_floor.csv",
        FLOOR_FIELDS,
        summary["province_capacity_floor"]["province_rows"],
    )
    a2._write_csv(
        output_dir / "capacity_floor_facilities.csv",
        FLOOR_FACILITY_FIELDS,
        a3._floor_facility_rows(floor_plan.facilities, floor_plan),
    )
    a2._write_csv(
        output_dir / "v4_v5_comparison.csv",
        COMPARISON_FIELDS,
        comparison_rows,
    )
    a2._write_csv(
        output_dir / "province_capacity_comparison.csv",
        PROVINCE_CAPACITY_FIELDS,
        summary["province_capacity_comparison"],
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V5.md").write_text(
        _report(summary), encoding="utf-8"
    )
    a2._write_json(output_dir / "summary.json", summary)
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": inputs,
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": a2._sha256(path)}
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "scenarios": summary["scenarios"],
        "coverage": summary["coverage"],
        "comparison": summary["comparison"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    a2._write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "primary_scenario": PRIMARY_SCENARIO,
                "assigned_m3": summary["scenarios"][PRIMARY_SCENARIO][
                    "assigned_m3"
                ],
                "cross_border_m3": summary["scenarios"][PRIMARY_SCENARIO][
                    "cross_border_m3"
                ],
                "proxy_share_of_assigned": summary["scenarios"][
                    PRIMARY_SCENARIO
                ]["proxy_share_of_assigned"],
                "hhi": summary["scenarios"][PRIMARY_SCENARIO]["hhi"],
                "comparison": summary["comparison"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
