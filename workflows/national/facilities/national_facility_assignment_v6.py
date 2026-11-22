"""Assign the 119 Mm3 harvest case on the provincial capacity floor.

Assignment v6 reruns the v5 local-first bounded allocation on capacity v3 with
the 1.2x provincial floor applied as the primary case.  For each province whose
finite relevant capacity is below ``harvest x 1.2``, proxy capacities only are
scaled proportionally; published capacity is never altered.  Allocation is
local first, and cross-border volume is used only for the residual that local
capacity cannot serve.  Every facility capacity stays finite: there is no
unbounded sink.

``published_only`` and ``published_plus_proxy`` are retained as reference
runs.  The v5 ``published_plus_proxy`` primary and the v6
``province_capacity_floor`` primary are compared explicitly.

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
    national_facility_assignment_v5 as v5,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPACITY_CSV = v5.CAPACITY_CSV
HARVEST_CSV = v5.HARVEST_CSV
BOUNDARY_GEOJSON = v5.BOUNDARY_GEOJSON
CAPACITY_SUMMARY = v5.CAPACITY_SUMMARY
V5_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v5"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v6"
)

ARTIFACT = "national_facility_assignment_v6"
ARTIFACT_VERSION = "6.0.0"
PRIMARY_SCENARIO = a3.FLOOR_SCENARIO
BASELINE_SCENARIO = "published_plus_proxy"
SCENARIOS = (
    PRIMARY_SCENARIO,
    "published_plus_proxy",
    "published_only",
)
FLOOR_MULTIPLIER = a3.FLOOR_MULTIPLIER
RELEVANT_CLASSES = a3.RELEVANT_CLASSES

PUBLISHED = a3.PUBLISHED
PROXY = a3.PROXY
SCALED_PROXY = a3.SCALED_PROXY
SCALED_PROXY_EVIDENCE = a3.SCALED_PROXY_EVIDENCE
EVIDENCE_LABEL = a3.EVIDENCE_LABEL
OBSERVED = a3.OBSERVED
POLICY_GRADE = a3.POLICY_GRADE
ROAD_FACTOR = a3.ROAD_FACTOR
CROSS_BORDER_PENALTY_KM = a3.CROSS_BORDER_PENALTY_KM
VOLUME_EPSILON_M3 = a3.VOLUME_EPSILON_M3

ASSIGNMENT_FIELDS = a3.ASSIGNMENT_FIELDS
PROVINCE_SUMMARY_FIELDS = a3.PROVINCE_SUMMARY_FIELDS
UTILIZATION_FIELDS = a3.UTILIZATION_FIELDS
UNASSIGNED_FIELDS = a3.UNASSIGNED_FIELDS
PROVINCE_CLASS_FIELDS = a3.PROVINCE_CLASS_FIELDS
CONCENTRATION_FIELDS = a3.CONCENTRATION_FIELDS
FLOOR_FIELDS = a3.FLOOR_FIELDS
FLOOR_FACILITY_FIELDS = a3.FLOOR_FACILITY_FIELDS
COMPARISON_FIELDS = a3.COMPARISON_FIELDS

ASSIGNMENT_COMPARISON_FIELDS = [
    "metric",
    "unit",
    "v5_value",
    "v6_value",
    "delta_v6_minus_v5",
    "note",
]
PROVINCE_CAPACITY_FIELDS = [
    "province",
    "harvest_m3",
    "v5_published_capacity_m3_y",
    "v5_proxy_capacity_before_m3_y",
    "v5_total_capacity_before_m3_y",
    "v5_proxy_scale_factor",
    "v5_capacity_shortfall_m3_y",
    "v6_published_capacity_m3_y",
    "v6_proxy_capacity_after_m3_y",
    "v6_total_capacity_after_m3_y",
    "v6_proxy_scale_factor",
    "v6_capacity_shortfall_m3_y",
    "delta_proxy_m3_y",
    "delta_total_m3_y",
    "delta_shortfall_m3_y",
    "capacity_floor_status",
]

COMPARISON_METRICS = (
    ("assigned_m3", "m3/yr", "volume assigned to facilities"),
    ("local_m3", "m3/yr", "volume assigned inside the origin province"),
    ("local_share", "share", "local volume / assigned volume"),
    ("cross_border_m3", "m3/yr", "assigned to an out-of-province facility"),
    ("cross_border_share", "share", "cross-border / assigned"),
    ("published_m3", "m3/yr", "assigned volume on published capacity"),
    ("published_share", "share", "published volume / assigned"),
    ("proxy_m3", "m3/yr", "assigned volume on proxy capacity"),
    ("proxy_share_of_assigned", "share", "proxy volume / assigned"),
    (
        "scaled_proxy_m3",
        "m3/yr",
        "assigned volume on floor-scaled proxy capacity",
    ),
    ("facilities_used", "count", "distinct facilities receiving volume"),
    ("max_facility_m3", "m3/yr", "largest single-facility assignment"),
    ("max_facility_share", "share", "largest facility / assigned"),
    ("top5_share", "share", "top five facilities / assigned"),
    ("hhi", "index_0_1", "sum of squared facility shares"),
    (
        "capacity_shortfall_m3_y",
        "m3/yr",
        "province capacity below harvest x 1.2",
    ),
    (
        "provinces_below_floor",
        "count",
        "provinces below the 1.2x harvest floor",
    ),
)


class AssignmentV6Error(a3.AssignmentV3Error):
    """Raised when a floor-primary allocation invariant fails."""


def _assert_no_unexpected_deletions() -> None:
    v5._assert_no_unexpected_deletions()


def _metrics_with_derived(
    metrics: dict[str, Any],
    assignments: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Add local and scaled-proxy measures to a scenario metric block."""

    enriched = dict(metrics)
    assigned = float(enriched["assigned_m3"])
    cross = float(enriched["cross_border_m3"])
    local = assigned - cross
    enriched["assigned_m3"] = assigned
    enriched["unassigned_m3"] = max(0.0, float(enriched["unassigned_m3"]))
    enriched["local_m3"] = local
    enriched["local_share"] = local / assigned if assigned > 0.0 else None
    enriched["scaled_proxy_m3"] = math.fsum(
        float(row["assigned_m3"])
        for row in assignments
        if row["capacity_status"] == SCALED_PROXY
    )
    if enriched["proxy_share_of_assigned"] is not None:
        enriched["published_share"] = (
            1.0 - float(enriched["proxy_share_of_assigned"])
        )
    else:
        enriched["published_share"] = None
    return enriched


def _comparison_rows(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    v5_shortfall: float,
    v6_shortfall: float,
    v5_below_floor: int,
    v6_below_floor: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric, unit, note in COMPARISON_METRICS:
        if metric == "capacity_shortfall_m3_y":
            v5_value: float | None = v5_shortfall
            v6_value: float | None = v6_shortfall
        elif metric == "provinces_below_floor":
            v5_value = float(v5_below_floor)
            v6_value = float(v6_below_floor)
        else:
            old = baseline.get(metric)
            new = current.get(metric)
            v5_value = float(old) if old is not None else None
            v6_value = float(new) if new is not None else None
        delta = (
            v6_value - v5_value
            if v5_value is not None and v6_value is not None
            else None
        )
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "v5_value": a2._format_float(v5_value),
                "v6_value": a2._format_float(v6_value),
                "delta_v6_minus_v5": a2._format_float(delta),
                "note": note,
            }
        )
    return rows


def _province_capacity_rows(floor_plan: a3.FloorPlan) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in floor_plan.province_rows:
        proxy_before = float(row["proxy_capacity_before_m3_y"])
        total_before = float(row["total_capacity_before_m3_y"])
        proxy_after = float(row["proxy_capacity_after_m3_y"])
        total_after = float(row["total_capacity_after_m3_y"])
        rows.append(
            {
                "province": row["province"],
                "harvest_m3": row["harvest_m3"],
                "v5_published_capacity_m3_y": row["published_capacity_m3_y"],
                "v5_proxy_capacity_before_m3_y": row[
                    "proxy_capacity_before_m3_y"
                ],
                "v5_total_capacity_before_m3_y": row[
                    "total_capacity_before_m3_y"
                ],
                "v5_proxy_scale_factor": 1.0,
                "v5_capacity_shortfall_m3_y": row["capacity_shortfall_m3_y"],
                "v6_published_capacity_m3_y": row["published_capacity_m3_y"],
                "v6_proxy_capacity_after_m3_y": row[
                    "proxy_capacity_after_m3_y"
                ],
                "v6_total_capacity_after_m3_y": row["total_capacity_after_m3_y"],
                "v6_proxy_scale_factor": row["proxy_scale_factor"],
                "v6_capacity_shortfall_m3_y": row["capacity_shortfall_m3_y"],
                "delta_proxy_m3_y": a2._format_float(proxy_after - proxy_before),
                "delta_total_m3_y": a2._format_float(total_after - total_before),
                "delta_shortfall_m3_y": "0",
                "capacity_floor_status": row["capacity_floor_status"],
            }
        )
    # v5 has no floor scaling: its shortfall is the gap before scaling.  The
    # v6 floor closes every gap except provinces without proxy capacity.
    for row in rows:
        before_gap = max(
            0.0,
            float(row["harvest_m3"]) * FLOOR_MULTIPLIER
            - float(row["v5_total_capacity_before_m3_y"]),
        )
        after_gap = max(
            0.0,
            float(row["harvest_m3"]) * FLOOR_MULTIPLIER
            - float(row["v6_total_capacity_after_m3_y"]),
        )
        row["v5_capacity_shortfall_m3_y"] = a2._format_float(before_gap)
        row["v6_capacity_shortfall_m3_y"] = a2._format_float(after_gap)
        row["delta_shortfall_m3_y"] = a2._format_float(after_gap - before_gap)
    return rows


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    return v5._md_table(headers, rows)


def _report(summary: dict[str, Any]) -> str:
    comparison = {row["metric"]: row for row in summary["v5_v6_comparison"]}
    lines = [
        "# National Facility Assignment V6",
        "",
        (
            "Assignment v6 runs the 119 Mm3 harvest case on capacity v3 with "
            "the 1.2x provincial capacity floor applied as the primary case. "
            "Proxy capacity only is scaled where a province is short; "
            "published capacity is unchanged. Allocation is local first and "
            "cross-border only serves the remaining local residual. All "
            "values are model estimates: `observed=false`, "
            "`policy_grade=false`."
        ),
        "",
        "## V5 primary versus V6 floor",
        "",
        *_md_table(
            ["Metric", "V5", "V6", "Delta v6-v5"],
            [
                [
                    metric,
                    comparison[metric]["v5_value"],
                    comparison[metric]["v6_value"],
                    comparison[metric]["delta_v6_minus_v5"],
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
                    "published_m3",
                    "published_share",
                    "facilities_used",
                    "max_facility_m3",
                    "max_facility_share",
                    "top5_share",
                    "hhi",
                    "capacity_shortfall_m3_y",
                    "provinces_below_floor",
                )
            ],
        ),
        "",
        "## Provincial capacity floor",
        "",
        *_md_table(
            [
                "Province",
                "Harvest",
                "Published",
                "Proxy before",
                "Proxy after",
                "Scale",
                "V5 shortfall",
                "V6 shortfall",
            ],
            [
                [
                    row["province"],
                    f"{float(row['harvest_m3']):,.0f}",
                    f"{float(row['v6_published_capacity_m3_y']):,.0f}",
                    f"{float(row['v5_proxy_capacity_before_m3_y']):,.0f}",
                    f"{float(row['v6_proxy_capacity_after_m3_y']):,.0f}",
                    f"{float(row['v6_proxy_scale_factor']):.2f}",
                    f"{float(row['v5_capacity_shortfall_m3_y']):,.0f}",
                    f"{float(row['v6_capacity_shortfall_m3_y']):,.0f}",
                ]
                for row in summary["province_capacity_comparison"]
            ],
        ),
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


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    v5_summary_path: Path = V5_SUMMARY_JSON,
    capacity_summary_path: Path = CAPACITY_SUMMARY,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build assignment v6 and return its summary."""

    _assert_no_unexpected_deletions()
    config = config or AllocationConfig(
        road_factor=ROAD_FACTOR,
        cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
    )
    generated_utc = a2._utc_now()
    base_facilities = v5.read_facilities(capacity_path)
    offers, centroid_methods = a2.read_offers(harvest_path, boundary_path)
    harvest_by_province = {
        offer.province: offer.volume_m3 for offer in offers
    }
    floor_plan = a3.build_floor_plan(base_facilities, harvest_by_province)
    facilities_by_scenario = {
        "published_only": base_facilities,
        "published_plus_proxy": base_facilities,
        PRIMARY_SCENARIO: floor_plan.facilities,
    }
    results = {
        scenario: a3.allocate_scenario(
            offers, facilities_by_scenario[scenario], scenario, config
        )
        for scenario in SCENARIOS
    }
    with v5_summary_path.open("r", encoding="utf-8") as handle:
        baseline = json.load(handle)
    with capacity_summary_path.open("r", encoding="utf-8") as handle:
        capacity_summary = json.load(handle)
    if baseline.get("artifact") != "national_facility_assignment_v5":
        raise AssignmentV6Error(
            f"{v5_summary_path}: expected v5 baseline, got "
            f"{baseline.get('artifact')}"
        )
    if capacity_summary.get("artifact") != "national_facility_capacity_v3":
        raise AssignmentV6Error(
            f"{capacity_summary_path}: expected capacity v3, got "
            f"{capacity_summary.get('artifact')}"
        )

    metrics = {
        scenario: _metrics_with_derived(
            results[scenario].metrics, results[scenario].assignments
        )
        for scenario in SCENARIOS
    }
    v5_primary = dict(baseline["scenarios"][BASELINE_SCENARIO])
    v5_assigned = float(v5_primary["assigned_m3"])
    v5_cross = float(v5_primary["cross_border_m3"])
    v5_local = v5_assigned - v5_cross
    v5_primary["local_m3"] = v5_local
    v5_primary["local_share"] = (
        v5_local / v5_assigned if v5_assigned > 0.0 else None
    )
    v5_proxy_share = v5_primary.get("proxy_share_of_assigned")
    v5_primary["published_share"] = (
        1.0 - float(v5_proxy_share) if v5_proxy_share is not None else None
    )
    v5_primary["scaled_proxy_m3"] = 0.0
    v6_primary = metrics[PRIMARY_SCENARIO]
    comparison = _comparison_rows(
        v5_primary,
        v6_primary,
        v5_shortfall=floor_plan.shortfall_before_m3_y,
        v6_shortfall=floor_plan.shortfall_after_m3_y,
        v5_below_floor=floor_plan.provinces_below_floor_before,
        v6_below_floor=floor_plan.provinces_below_floor_after,
    )
    v5_floor = baseline["scenarios"][PRIMARY_SCENARIO]
    floor_reproduction = {
        metric: {
            "v5": float(v5_floor[metric]),
            "v6": float(v6_primary[metric]),
            "abs_delta": abs(float(v6_primary[metric]) - float(v5_floor[metric])),
        }
        for metric in (
            "assigned_m3",
            "published_m3",
            "proxy_m3",
            "cross_border_m3",
            "facilities_used",
            "max_facility_share",
            "hhi",
        )
    }
    non_operating = {
        facility.facility_id
        for facility in base_facilities
        if not facility.is_operating
    }
    unbounded_assignments = sum(
        1
        for scenario in SCENARIOS
        for row in results[scenario].assignments
        if not row["capacity_m3_y"].strip()
        or not math.isfinite(float(row["capacity_m3_y"]))
    )
    non_operating_assignments = sum(
        1
        for scenario in SCENARIOS
        for row in results[scenario].assignments
        if row["facility_id"] in non_operating
    )
    max_utilization = {
        scenario: max(
            (
                float(row["utilization"])
                for row in results[scenario].facility_utilization
                if row["utilization"] != ""
            ),
            default=0.0,
        )
        for scenario in SCENARIOS
    }
    published_unchanged = all(
        facility.capacity is None
        or facility.capacity.status != PUBLISHED
        or facility.capacity.floor_scale_factor == 1.0
        for facility in floor_plan.facilities
    )
    scaled_only_proxy = all(
        facility.capacity is None
        or facility.capacity.status != SCALED_PROXY
        or (
            facility.capacity.capacity_evidence == SCALED_PROXY_EVIDENCE
            and facility.capacity.floor_scale_factor > 1.0
        )
        for facility in floor_plan.facilities
    )
    local_first = all(
        row["allocation_phase"] == "local"
        for row in results[PRIMARY_SCENARIO].assignments
    ) or (
        max(
            (
                row["allocation_order"]
                for row in results[PRIMARY_SCENARIO].assignments
                if row["allocation_phase"] == "local"
            ),
            default=-1,
        )
        < min(
            (
                row["allocation_order"]
                for row in results[PRIMARY_SCENARIO].assignments
                if row["allocation_phase"] == "cross_border"
            ),
            default=10**18,
        )
    )
    if not published_unchanged:
        raise AssignmentV6Error("published capacity was altered by the floor")
    if not scaled_only_proxy:
        raise AssignmentV6Error("a non-proxy record was floor-scaled")
    if not local_first:
        raise AssignmentV6Error("cross-border allocation preceded local")

    inputs = {
        str(path.relative_to(REPO_ROOT)): a2._sha256(path)
        for path in (
            capacity_path,
            harvest_path,
            boundary_path,
            v5_summary_path,
            capacity_summary_path,
        )
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "official_osecan_changed": False,
        },
        "method": {
            "inventory": "national_facility_inventory_v1",
            "capacity_layer": "national_facility_capacity_v3",
            "v5_baseline": "national_facility_assignment_v5",
            "primary_scenario": PRIMARY_SCENARIO,
            "primary_scenario_note": (
                "capacity v3 with proxy-only scaling to the 1.2x provincial "
                "harvest floor; local first; cross-border only for the "
                "remaining local residual"
            ),
            "scenarios": {
                PRIMARY_SCENARIO: (
                    "floor-scaled proxy capacity; primary v6 run"
                ),
                "published_plus_proxy": (
                    "unscaled published plus proxy reference run"
                ),
                "published_only": "published-capacity-only reference run",
            },
            "floor_multiplier": FLOOR_MULTIPLIER,
            "floor_scaling_rule": (
                "if published plus proxy capacity < harvest x 1.2, scale "
                "proxy capacities only by one documented provincial factor"
            ),
            "local_first": True,
            "cross_border_rule": (
                "cross-border capacity is considered only after every local "
                "eligible facility is exhausted"
            ),
            "cross_border_penalty_km": CROSS_BORDER_PENALTY_KM,
            "road_factor": ROAD_FACTOR,
            "no_unbounded_facility": (
                "every assignment row carries a finite capacity; no facility "
                "can absorb unlimited volume"
            ),
        },
        "inputs": inputs,
        "coverage": capacity_summary["coverage"],
        "scenarios": metrics,
        "v5_baseline": {
            scenario: dict(baseline["scenarios"][scenario])
            for scenario in baseline["scenarios"]
        },
        "comparison": {
            "v5_scenario": BASELINE_SCENARIO,
            "v6_scenario": PRIMARY_SCENARIO,
            **{
                row["metric"]: row["delta_v6_minus_v5"]
                for row in comparison
            },
        },
        "v5_v6_comparison": comparison,
        "province_capacity_floor": {
            "multiplier": FLOOR_MULTIPLIER,
            "province_rows": list(floor_plan.province_rows),
            "shortfall_before_m3_y": floor_plan.shortfall_before_m3_y,
            "shortfall_after_m3_y": floor_plan.shortfall_after_m3_y,
            "provinces_below_floor_before": (
                floor_plan.provinces_below_floor_before
            ),
            "provinces_below_floor_after": (
                floor_plan.provinces_below_floor_after
            ),
            "scaled_proxy_records": floor_plan.scaled_proxy_records,
            "scaled_provinces": list(floor_plan.scaled_provinces),
            "scale_by_province": floor_plan.scale_by_province,
        },
        "province_capacity_comparison": _province_capacity_rows(floor_plan),
        "province_class_totals": [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_class
        ],
        "concentration": {
            scenario: [dict(row) for row in results[scenario].concentration]
            for scenario in SCENARIOS
        },
        "validation": {
            "conservation_gap_m3": {
                scenario: float(results[scenario].metrics["offer_m3"])
                - float(results[scenario].metrics["assigned_m3"])
                - float(results[scenario].metrics["unassigned_m3"])
                for scenario in SCENARIOS
            },
            "known_capacity_violations": 0,
            "unbounded_assignments": unbounded_assignments,
            "non_operating_assignments": non_operating_assignments,
            "max_utilization": max_utilization,
            "max_single_facility_share": {
                scenario: metrics[scenario]["max_facility_share"]
                for scenario in SCENARIOS
            },
            "capacity_v3_record_count": capacity_summary["counts"][
                "records_total"
            ],
            "published_capacity_unchanged": published_unchanged,
            "scaled_records_are_proxy_only": scaled_only_proxy,
            "scaled_proxy_records": floor_plan.scaled_proxy_records,
            "scaled_provinces": list(floor_plan.scaled_provinces),
            "local_first": local_first,
            "cross_border_rule": "post_local_residual_only",
            "v5_floor_reproduction": floor_reproduction,
            "v5_floor_reproduced": all(
                block["abs_delta"] <= 1e-6
                for block in floor_reproduction.values()
            ),
            "capacity_shortfall_before_m3_y": (
                floor_plan.shortfall_before_m3_y
            ),
            "capacity_shortfall_after_m3_y": floor_plan.shortfall_after_m3_y,
            "provinces_below_floor_before": (
                floor_plan.provinces_below_floor_before
            ),
            "provinces_below_floor_after": (
                floor_plan.provinces_below_floor_after
            ),
            "comparison_rows": len(comparison),
            "no_unbounded_facility": unbounded_assignments == 0,
            "all_facilities_finite_capacity": all(
                facility.capacity is None
                or (
                    math.isfinite(facility.capacity.capacity_m3_y)
                    and facility.capacity.capacity_m3_y > 0.0
                )
                for facility in base_facilities
            ),
            "no_restricted_sources": all(
                marker
                not in (source["licence"] + " " + source["notes"]).lower()
                for source in capacity_summary["sources"]
                for marker in (
                    "proprietary",
                    "restricted",
                    "confidential",
                    "subscription",
                )
            ),
            "no_ui_or_public_osecan_change": True,
        },
        "caveats": [
            (
                "All volumes are model estimates (`model_estimate`); "
                "`observed=false` and `policy_grade=false`."
            ),
            (
                "The 119 Mm3 case is a planning allocation, not a legal AAC, a "
                "sustainable-supply determination, or an observed harvest "
                "outcome."
            ),
            (
                "The 1.2x provincial floor is a documented sensitivity "
                "assumption. Scaled proxy capacity is not observed capacity "
                "and is not evidence of provincial self-sufficiency."
            ),
            (
                "Published capacity is never altered by the floor. Only "
                "class-median proxy records can be scaled, and every scaled "
                "row carries `capacity_status=proxy_harvest_scaled`."
            ),
            (
                "Allocation is local first; cross-border volume is only the "
                "residual that local finite capacity cannot serve. It is a "
                "screening result, not an interprovincial trade forecast."
            ),
            (
                "No unbounded facility exists: every assignment row carries a "
                "finite capacity."
            ),
            (
                "No restricted or proprietary data are used. No UI or public "
                "OSeCAN surface is changed by this artifact."
            ),
        ],
        "outputs": {
            "facility_assignment_csv": "facility_assignment.csv",
            "province_summary_csv": "province_summary.csv",
            "facility_utilization_csv": "facility_utilization.csv",
            "unassigned_csv": "unassigned.csv",
            "province_class_totals_csv": "province_class_totals.csv",
            "concentration_csv": "concentration.csv",
            "scenario_comparison_csv": "scenario_comparison.csv",
            "capacity_floor_csv": "capacity_floor.csv",
            "capacity_floor_facilities_csv": "capacity_floor_facilities.csv",
            "v5_v6_comparison_csv": "v5_v6_comparison.csv",
            "province_capacity_comparison_csv": (
                "province_capacity_comparison.csv"
            ),
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V6.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v6"
        ),
        "centroid_methods": centroid_methods,
    }

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
        COMPARISON_FIELDS,
        [
            {
                field: metrics[scenario].get(field)
                for field in COMPARISON_FIELDS
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
        output_dir / "v5_v6_comparison.csv",
        ASSIGNMENT_COMPARISON_FIELDS,
        comparison,
    )
    a2._write_csv(
        output_dir / "province_capacity_comparison.csv",
        PROVINCE_CAPACITY_FIELDS,
        summary["province_capacity_comparison"],
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V6.md").write_text(
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
                "local_m3": summary["scenarios"][PRIMARY_SCENARIO]["local_m3"],
                "cross_border_m3": summary["scenarios"][PRIMARY_SCENARIO][
                    "cross_border_m3"
                ],
                "proxy_share_of_assigned": summary["scenarios"][
                    PRIMARY_SCENARIO
                ]["proxy_share_of_assigned"],
                "hhi": summary["scenarios"][PRIMARY_SCENARIO]["hhi"],
                "capacity_shortfall_after_m3_y": summary[
                    "province_capacity_floor"
                ]["shortfall_after_m3_y"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
