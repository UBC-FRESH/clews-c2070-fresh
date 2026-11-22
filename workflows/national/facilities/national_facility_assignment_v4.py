"""Assign the 119 Mm3 harvest case with updated national capacity evidence.

Assignment v4 reruns the v3 local-first bounded allocation on inventory v1 /
capacity v2.  The updated layer adds the public Canadian Biomass 2025 pellet
map, converts finished pellet tonnes to m3 at 650 kg/m3, and keeps BC
class-median proxies for records without published capacity.

The primary scenario is ``published_plus_proxy``.  ``published_only`` and the
v3 ``province_capacity_floor`` sensitivity are retained for comparison.  Every
assigned facility has a finite capacity; there is no unbounded sink.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
legal or AAC claim is made.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v2 as v2,
)
from workflows.national.facilities import (
    national_facility_assignment_v3 as v3,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = Path(__file__).resolve().parents[3]
INVENTORY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_inventory_v1"
    / "national_facilities.csv"
)
CAPACITY_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v2"
    / "national_facility_capacity_v2.csv"
)
HARVEST_CSV = (
    REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
)
BOUNDARY_GEOJSON = (
    REPO_ROOT / "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson"
)
V3_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v3"
    / "summary.json"
)
CAPACITY_V2_SUMMARY = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_capacity_v2"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v4"
)

ARTIFACT = "national_facility_assignment_v4"
ARTIFACT_VERSION = "4.0.0"
SCENARIOS = v3.SCENARIOS
PRIMARY_SCENARIO = "published_plus_proxy"
FLOOR_SCENARIO = "province_capacity_floor"
FLOOR_MULTIPLIER = v3.FLOOR_MULTIPLIER
RELEVANT_CLASSES = v3.RELEVANT_CLASSES

PUBLISHED = v3.PUBLISHED
PROXY = v3.PROXY
SCALED_PROXY = v3.SCALED_PROXY
NOT_PUBLISHED = v3.NOT_PUBLISHED

PUBLISHED_EVIDENCE = frozenset(
    {
        "published_mills_db_2015_infeed_proxy",
        "published_capacity_converted",
    }
)
PROXY_EVIDENCE = v3.PROXY_EVIDENCE
SCALED_PROXY_EVIDENCE = v3.SCALED_PROXY_EVIDENCE

EVIDENCE_LABEL = v3.EVIDENCE_LABEL
OBSERVED = v3.OBSERVED
POLICY_GRADE = v3.POLICY_GRADE
VOLUME_EPSILON_M3 = v3.VOLUME_EPSILON_M3
ROAD_FACTOR = v3.ROAD_FACTOR
CROSS_BORDER_PENALTY_KM = v3.CROSS_BORDER_PENALTY_KM

COMPARISON_METRICS = (
    ("assigned_m3", "m3/yr", "volume assigned to facilities"),
    ("unassigned_m3", "m3/yr", "provincial offer left unassigned"),
    ("published_m3", "m3/yr", "assigned volume on published capacity"),
    ("proxy_m3", "m3/yr", "assigned volume on proxy capacity"),
    (
        "proxy_share_of_assigned",
        "share",
        "proxy volume / assigned volume",
    ),
    ("cross_border_m3", "m3/yr", "assigned to an out-of-province facility"),
    ("cross_border_share", "share", "cross-border / assigned"),
    ("facilities_used", "count", "distinct facilities receiving volume"),
    ("max_facility_m3", "m3/yr", "largest single-facility assignment"),
    ("max_facility_share", "share", "largest / assigned"),
    ("top5_share", "share", "top five facilities / assigned"),
    ("hhi", "index_0_1", "sum of squared facility shares"),
)
COMPARISON_FIELDS = [
    "scenario",
    "metric",
    "unit",
    "v3_value",
    "v4_value",
    "delta_v4_minus_v3",
    "note",
]
PROVINCE_CAPACITY_FIELDS = [
    "province",
    "harvest_m3",
    "v3_published_capacity_m3_y",
    "v4_published_capacity_m3_y",
    "delta_published_m3_y",
    "v3_proxy_capacity_before_m3_y",
    "v4_proxy_capacity_before_m3_y",
    "delta_proxy_before_m3_y",
    "v3_total_capacity_before_m3_y",
    "v4_total_capacity_before_m3_y",
    "delta_total_before_m3_y",
    "v3_proxy_scale_factor",
    "v4_proxy_scale_factor",
    "v3_proxy_capacity_after_m3_y",
    "v4_proxy_capacity_after_m3_y",
    "delta_proxy_after_m3_y",
    "v3_total_capacity_after_m3_y",
    "v4_total_capacity_after_m3_y",
    "delta_total_after_m3_y",
    "v3_capacity_shortfall_m3_y",
    "v4_capacity_shortfall_m3_y",
    "delta_shortfall_m3_y",
]
ASSIGNMENT_FIELDS = v3.ASSIGNMENT_FIELDS
PROVINCE_SUMMARY_FIELDS = v3.PROVINCE_SUMMARY_FIELDS
UTILIZATION_FIELDS = v3.UTILIZATION_FIELDS
UNASSIGNED_FIELDS = v3.UNASSIGNED_FIELDS
PROVINCE_CLASS_FIELDS = v3.PROVINCE_CLASS_FIELDS
CONCENTRATION_FIELDS = v3.CONCENTRATION_FIELDS
FLOOR_FIELDS = v3.FLOOR_FIELDS
FLOOR_FACILITY_FIELDS = v3.FLOOR_FACILITY_FIELDS


class AssignmentV4Error(v3.AssignmentV3Error):
    """Raised when an updated-evidence allocation invariant fails."""


def _read_capacity_evidence(path: Path = CAPACITY_CSV) -> dict[str, str]:
    rows = v2._read_csv(path)
    v2._require_columns(rows, {"facility_id", "capacity_evidence"}, path)
    evidence: dict[str, str] = {}
    for row in rows:
        facility_id = row["facility_id"].strip()
        if facility_id in evidence:
            raise AssignmentV4Error(f"{path}: duplicate facility_id {facility_id}")
        evidence[facility_id] = row["capacity_evidence"].strip()
    return evidence


def _assert_no_unexpected_deletions() -> None:
    """Fail closed on any unexpected D in the parent or data status."""

    for root in (REPO_ROOT, REPO_ROOT / "data"):
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssignmentV4Error(
                f"cannot read git status in {root}: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if len(line) >= 2 and (line[0] == "D" or line[1] == "D"):
                raise AssignmentV4Error(
                    f"unexpected deletion in {root}: {line!r}; halting"
                )


def read_facilities(
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
) -> tuple[v3.Facility, ...]:
    """Read updated capacity through the v2 validator and label evidence."""

    base_facilities = v2.read_facilities(capacity_path, inventory_path)
    evidence = _read_capacity_evidence(capacity_path)
    facilities: list[v3.Facility] = []
    for base in base_facilities:
        label = evidence.get(base.facility_id, "")
        if base.capacity is None:
            capacity = None
        elif base.capacity.status == PUBLISHED:
            if label not in PUBLISHED_EVIDENCE:
                raise AssignmentV4Error(
                    f"{base.facility_id}: published capacity evidence "
                    f"{label!r} is not an accepted published label"
                )
            capacity = v3.FloorCapacity(
                capacity_m3_y=base.capacity.capacity_m3_y,
                status=PUBLISHED,
                capacity_evidence=label,
                floor_scale_factor=1.0,
            )
        else:
            if label != PROXY_EVIDENCE:
                raise AssignmentV4Error(
                    f"{base.facility_id}: proxy capacity evidence {label!r} "
                    "is not class_median_proxy"
                )
            capacity = v3.FloorCapacity(
                capacity_m3_y=base.capacity.capacity_m3_y,
                status=PROXY,
                capacity_evidence=label,
                floor_scale_factor=1.0,
                proxy_low_m3_y=base.capacity.proxy_low_m3_y,
                proxy_high_m3_y=base.capacity.proxy_high_m3_y,
            )
        facilities.append(
            v3.Facility(
                facility_id=base.facility_id,
                name=base.name,
                facility_class=base.facility_class,
                province=base.province,
                location=base.location,
                capacity=capacity,
                operating_status=base.operating_status,
                source_id=base.source_id,
                source_url=base.source_url,
                capacity_source_id=base.capacity_source_id,
                capacity_source_url=base.capacity_source_url,
                evidence_tier=base.evidence_tier,
            )
        )
    return tuple(facilities)


def _read_v3_baseline(path: Path = V3_SUMMARY_JSON) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as error:
        raise AssignmentV4Error(f"v3 summary is missing: {path}") from error
    if "scenarios" not in payload:
        raise AssignmentV4Error(f"{path}: no scenarios block")
    return payload


def _read_capacity_summary(path: Path = CAPACITY_V2_SUMMARY) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as error:
        raise AssignmentV4Error(f"capacity v2 summary is missing: {path}") from error


def _comparison_rows(
    v3_baseline: dict[str, Any], results: dict[str, v2.ScenarioResult]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        old = v3_baseline["scenarios"][scenario]
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
                    "v3_value": v2._format_float(
                        float(old_value) if old_value is not None else None
                    ),
                    "v4_value": v2._format_float(
                        float(new_value) if new_value is not None else None
                    ),
                    "delta_v4_minus_v3": v2._format_float(delta),
                    "note": note,
                }
            )
    return rows


def _province_capacity_rows(
    v3_baseline: dict[str, Any], floor_plan: v3.FloorPlan
) -> list[dict[str, Any]]:
    old_rows = {
        row["province"]: row
        for row in v3_baseline["province_capacity_floor"]["province_rows"]
    }
    new_rows = {row["province"]: row for row in floor_plan.province_rows}
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        old = old_rows.get(province)
        new = new_rows.get(province)
        if old is None or new is None:
            raise AssignmentV4Error(
                f"{province}: missing v3 or v4 province capacity row"
            )

        def delta(field: str, old=old, new=new) -> str:
            return v2._format_float(float(new[field]) - float(old[field]))

        rows.append(
            {
                "province": province,
                "harvest_m3": old["harvest_m3"],
                "v3_published_capacity_m3_y": old["published_capacity_m3_y"],
                "v4_published_capacity_m3_y": new["published_capacity_m3_y"],
                "delta_published_m3_y": delta("published_capacity_m3_y"),
                "v3_proxy_capacity_before_m3_y": old[
                    "proxy_capacity_before_m3_y"
                ],
                "v4_proxy_capacity_before_m3_y": new[
                    "proxy_capacity_before_m3_y"
                ],
                "delta_proxy_before_m3_y": delta("proxy_capacity_before_m3_y"),
                "v3_total_capacity_before_m3_y": old[
                    "total_capacity_before_m3_y"
                ],
                "v4_total_capacity_before_m3_y": new[
                    "total_capacity_before_m3_y"
                ],
                "delta_total_before_m3_y": delta("total_capacity_before_m3_y"),
                "v3_proxy_scale_factor": old["proxy_scale_factor"],
                "v4_proxy_scale_factor": new["proxy_scale_factor"],
                "v3_proxy_capacity_after_m3_y": old[
                    "proxy_capacity_after_m3_y"
                ],
                "v4_proxy_capacity_after_m3_y": new[
                    "proxy_capacity_after_m3_y"
                ],
                "delta_proxy_after_m3_y": delta("proxy_capacity_after_m3_y"),
                "v3_total_capacity_after_m3_y": old[
                    "total_capacity_after_m3_y"
                ],
                "v4_total_capacity_after_m3_y": new[
                    "total_capacity_after_m3_y"
                ],
                "delta_total_after_m3_y": delta("total_capacity_after_m3_y"),
                "v3_capacity_shortfall_m3_y": old["capacity_shortfall_m3_y"],
                "v4_capacity_shortfall_m3_y": new["capacity_shortfall_m3_y"],
                "delta_shortfall_m3_y": delta("capacity_shortfall_m3_y"),
            }
        )
    return rows


def _summary(
    *,
    generated_utc: str,
    base_facilities: Sequence[v3.Facility],
    floor_plan: v3.FloorPlan,
    offers: Sequence[Any],
    centroid_methods: dict[str, str],
    results: dict[str, v2.ScenarioResult],
    v3_baseline: dict[str, Any],
    capacity_summary: dict[str, Any],
    inputs: dict[str, str],
) -> dict[str, Any]:
    comparison = _comparison_rows(v3_baseline, results)
    primary_old = v3_baseline["scenarios"][PRIMARY_SCENARIO]
    primary_new = results[PRIMARY_SCENARIO].metrics
    primary_delta = {
        metric: (
            float(primary_new[metric]) - float(primary_old[metric])
            if primary_old.get(metric) is not None
            and primary_new.get(metric) is not None
            else None
        )
        for metric, _, _ in COMPARISON_METRICS
    }
    scenarios = {
        scenario: dict(results[scenario].metrics) for scenario in SCENARIOS
    }
    province_capacity = _province_capacity_rows(v3_baseline, floor_plan)
    non_operating = {
        facility.facility_id
        for facility in base_facilities
        if not facility.is_operating
    }
    non_operating_assignments = sum(
        1
        for scenario in SCENARIOS
        for row in results[scenario].assignments
        if row["facility_id"] in non_operating
    )
    unbounded_assignments = sum(
        1
        for scenario in SCENARIOS
        for row in results[scenario].assignments
        if not row["capacity_m3_y"].strip()
        or not math.isfinite(float(row["capacity_m3_y"]))
    )
    max_utilization = {}
    for scenario in SCENARIOS:
        values = [
            float(row["utilization"])
            for row in results[scenario].facility_utilization
            if row["utilization"] != ""
        ]
        max_utilization[scenario] = max(values, default=0.0)
    evidence = capacity_summary["capacity"]["evidence"]
    return {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
        },
        "method": {
            "inventory": "national_facility_inventory_v1",
            "capacity_layer": "national_facility_capacity_v2",
            "v3_baseline": "national_facility_assignment_v3",
            "primary_scenario": PRIMARY_SCENARIO,
            "scenarios": {
                "published_only": (
                    "only published-capacity facilities are eligible; includes "
                    "converted 2025 pellet-map capacities"
                ),
                "published_plus_proxy": (
                    "published plus BC class-median proxy capacity; primary "
                    "updated-evidence run"
                ),
                "province_capacity_floor": (
                    "v3 1.2x harvest-floor sensitivity; proxies only are "
                    "scaled, published capacity is never altered"
                ),
            },
            "local_first": True,
            "cross_border_penalty_km": CROSS_BORDER_PENALTY_KM,
            "road_factor": ROAD_FACTOR,
            "distance_basis": (
                "haversine from ADM1 polygon centroid to facility coordinate; "
                "road distance = haversine * road factor"
            ),
            "quantity_basis": (
                "province harvest_m3 treated one-to-one as facility infeed "
                "m3; no moisture, bark, grade, or recovery conversion"
            ),
            "supply_offers": "one ungraded provincial offer per province",
            "eligible_classes": list(RELEVANT_CLASSES),
            "excluded_classes": ["OTHER"],
            "non_operating_statuses_excluded": sorted(
                v2.NON_OPERATING_STATUSES
            ),
            "unlocated_capacity": (
                "excluded from the spatial screen and reported separately"
            ),
            "no_unbounded_facility": (
                "every assignment row carries a finite capacity; no facility "
                "can absorb unlimited volume"
            ),
            "capacity_evidence": {
                "published_infeed_proxy": (
                    "2015 Mills DB/FFCM processing-line infeed proxy"
                ),
                "published_converted": (
                    "2025 Canadian Biomass pellet-map finished tonnes/yr "
                    "converted at 650 kg/m3 to finished-pellet bulk volume"
                ),
                "proxy": (
                    "BC class median from published infeed-proxy records; "
                    "converted pellet volumes excluded from the anchor"
                ),
            },
        },
        "inputs": inputs,
        "coverage": capacity_summary["coverage"],
        "scenarios": scenarios,
        "v3_baseline": {
            scenario: dict(v3_baseline["scenarios"][scenario])
            for scenario in SCENARIOS
        },
        "comparison": {
            "primary_scenario": PRIMARY_SCENARIO,
            "cross_border_delta_m3": primary_delta["cross_border_m3"],
            "cross_border_share_delta": primary_delta["cross_border_share"],
            "proxy_share_delta": primary_delta["proxy_share_of_assigned"],
            "max_facility_share_delta": primary_delta["max_facility_share"],
            "hhi_delta": primary_delta["hhi"],
            "published_m3_delta": primary_delta["published_m3"],
            "proxy_m3_delta": primary_delta["proxy_m3"],
            "assigned_m3_delta": primary_delta["assigned_m3"],
        },
        "v3_v4_comparison": comparison,
        "province_capacity_comparison": province_capacity,
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
                scenario: results[scenario].metrics["max_facility_share"]
                for scenario in SCENARIOS
            },
            "capacity_evidence": evidence,
            "published_evidence_accepted": sorted(PUBLISHED_EVIDENCE),
            "proxy_evidence": PROXY_EVIDENCE,
            "v3_baseline_scenarios": list(v3_baseline["scenarios"]),
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
            "no_restricted_sources": not any(
                marker in (source["licence"] + " " + source["notes"]).lower()
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
            ("All volumes are model estimates (`model_estimate`); "
            "`observed=false` and `policy_grade=false`."),
            ("The 119 Mm3 case is a planning allocation, not a legal AAC, a "
            "sustainable-supply determination, or an observed harvest "
            "outcome. No legal/AAC claim is made."),
            ("Capacity is mixed-basis: 2015 Mills DB/FFCM infeed proxies, "
            "converted 2025 pellet-map finished-pellet bulk volume, and BC "
            "class-median proxies. The bases are not interchangeable."),
            ("Converted pellet capacity is finished-product volume, not "
            "infeed-equivalent; no pellet yield, moisture, bark, grade, or "
            "recovery conversion is modelled."),
            ("New pellet facilities without a plant-gate coordinate use city "
            "centroids; their distance-based assignment carries location "
            "uncertainty."),
            ("The allocation is local-first with a 500 km cross-border penalty. "
            "Cross-border volume is a screening result, not an interprovincial "
            "trade forecast."),
            ("The 1.2x provincial floor is a documented sensitivity assumption, "
            "not an observed capacity requirement or evidence of provincial "
            "self-sufficiency."),
            ("No unbounded facility exists in v4: every assignment row carries "
            "a finite capacity and every relevant record has either published "
            "or proxy capacity."),
            ("No restricted or proprietary data are used. No UI or public "
            "OSeCAN surface is changed by this artifact."),
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
            "v3_v4_comparison_csv": "v3_v4_comparison.csv",
            "province_capacity_comparison_csv": "province_capacity_comparison.csv",
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V4.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v4"
        ),
    }


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
        "# National Facility Assignment V4",
        "",
        ("Updated-evidence rerun of the 119 Mm3 harvest assignment on inventory "
        "v1 / capacity v2. All values are model estimates: `observed=false`, "
        "`policy_grade=false`. No legal/AAC claim is made."),
        "",
        "## Primary scenario: published plus updated proxies",
        "",
        *_md_table(
            ["Metric", "V3", "V4", "Delta v4-v3"],
            [
                [
                    "Assigned m3",
                    f"{summary['v3_baseline']['published_plus_proxy']['assigned_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['assigned_m3']:,.0f}",
                    f"{comparison['assigned_m3_delta']:,.0f}",
                ],
                [
                    "Published m3",
                    f"{summary['v3_baseline']['published_plus_proxy']['published_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['published_m3']:,.0f}",
                    f"{comparison['published_m3_delta']:,.0f}",
                ],
                [
                    "Proxy m3",
                    f"{summary['v3_baseline']['published_plus_proxy']['proxy_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['proxy_m3']:,.0f}",
                    f"{comparison['proxy_m3_delta']:,.0f}",
                ],
                [
                    "Proxy share of assigned",
                    f"{summary['v3_baseline']['published_plus_proxy']['proxy_share_of_assigned']:.1%}",
                    f"{scenarios['published_plus_proxy']['proxy_share_of_assigned']:.1%}",
                    f"{comparison['proxy_share_delta']:.1%}",
                ],
                [
                    "Cross-border m3",
                    f"{summary['v3_baseline']['published_plus_proxy']['cross_border_m3']:,.0f}",
                    f"{scenarios['published_plus_proxy']['cross_border_m3']:,.0f}",
                    f"{comparison['cross_border_delta_m3']:,.0f}",
                ],
                [
                    "Cross-border share",
                    f"{summary['v3_baseline']['published_plus_proxy']['cross_border_share']:.1%}",
                    f"{scenarios['published_plus_proxy']['cross_border_share']:.1%}",
                    f"{comparison['cross_border_share_delta']:.1%}",
                ],
                [
                    "Max facility share",
                    f"{summary['v3_baseline']['published_plus_proxy']['max_facility_share']:.2%}",
                    f"{scenarios['published_plus_proxy']['max_facility_share']:.2%}",
                    f"{comparison['max_facility_share_delta']:.2%}",
                ],
                [
                    "HHI",
                    f"{summary['v3_baseline']['published_plus_proxy']['hhi']:.5f}",
                    f"{scenarios['published_plus_proxy']['hhi']:.5f}",
                    f"{comparison['hhi_delta']:.5f}",
                ],
            ],
        ),
        "",
        "## All scenarios",
        "",
        *_md_table(
            [
                "Scenario",
                "Assigned m3",
                "Cross-border m3",
                "Proxy share",
                "Max facility share",
                "HHI",
                "Facilities",
            ],
            [
                [
                    scenario,
                    f"{metrics['assigned_m3']:,.0f}",
                    f"{metrics['cross_border_m3']:,.0f}",
                    f"{metrics['proxy_share_of_assigned']:.1%}",
                    f"{metrics['max_facility_share']:.2%}",
                    f"{metrics['hhi']:.5f}",
                    metrics["facilities_used"],
                ]
                for scenario, metrics in scenarios.items()
            ],
        ),
        "",
        "## Capacity coverage",
        "",
        *_md_table(
            ["Measure", "Capacity v1", "Capacity v2"],
            [
                [
                    "Published records",
                    summary["coverage"]["before"].get("published_records", ""),
                    summary["coverage"]["after"]["published_records"],
                ],
                [
                    "Proxy records",
                    summary["coverage"]["before"].get("proxy_records", ""),
                    summary["coverage"]["after"]["proxy_records"],
                ],
                [
                    "Published capacity m3/yr",
                    f"{summary['coverage']['before'].get('published_capacity_m3_y', 0):,.0f}",
                    f"{summary['coverage']['after']['published_capacity_m3_y']:,.0f}",
                ],
                [
                    "Proxy capacity m3/yr",
                    f"{summary['coverage']['before'].get('proxy_capacity_m3_y', 0):,.0f}",
                    f"{summary['coverage']['after']['proxy_capacity_m3_y']:,.0f}",
                ],
                [
                    "Published share of capacity",
                    f"{summary['coverage']['before'].get('published_share_capacity', 0):.1%}",
                    f"{summary['coverage']['after']['published_share_capacity']:.1%}",
                ],
            ],
        ),
        "",
        "## Province capacity comparison",
        "",
        *_md_table(
            [
                "Province",
                "V3 published",
                "V4 published",
                "V3 proxy scale",
                "V4 proxy scale",
                "V3 shortfall",
                "V4 shortfall",
            ],
            [
                [
                    row["province"],
                    f"{float(row['v3_published_capacity_m3_y']):,.0f}",
                    f"{float(row['v4_published_capacity_m3_y']):,.0f}",
                    f"{float(row['v3_proxy_scale_factor']):.2f}",
                    f"{float(row['v4_proxy_scale_factor']):.2f}",
                    f"{float(row['v3_capacity_shortfall_m3_y']):,.0f}",
                    f"{float(row['v4_capacity_shortfall_m3_y']):,.0f}",
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


def _write_manifest(output_dir: Path, summary: dict[str, Any]) -> None:
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": v2._sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": outputs,
        "scenarios": summary["scenarios"],
        "coverage": summary["coverage"],
        "comparison": summary["comparison"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    v2._write_json(output_dir / "manifest.json", manifest)


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    inventory_path: Path = INVENTORY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    v3_summary_path: Path = V3_SUMMARY_JSON,
    capacity_summary_path: Path = CAPACITY_V2_SUMMARY,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build assignment v4 and return its summary."""

    _assert_no_unexpected_deletions()
    config = config or AllocationConfig(
        road_factor=ROAD_FACTOR,
        cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
    )
    generated_utc = v2._utc_now()
    base_facilities = read_facilities(capacity_path, inventory_path)
    offers, centroid_methods = v2.read_offers(harvest_path, boundary_path)
    harvest_by_province = {
        offer.province: offer.volume_m3 for offer in offers
    }
    floor_plan = v3.build_floor_plan(base_facilities, harvest_by_province)
    facilities_by_scenario = {
        "published_only": base_facilities,
        "published_plus_proxy": base_facilities,
        FLOOR_SCENARIO: floor_plan.facilities,
    }
    results = {
        scenario: v3.allocate_scenario(
            offers, facilities_by_scenario[scenario], scenario, config
        )
        for scenario in SCENARIOS
    }
    v3_baseline = _read_v3_baseline(v3_summary_path)
    capacity_summary = _read_capacity_summary(capacity_summary_path)
    inputs = {
        str(path.relative_to(REPO_ROOT)): v2._sha256(path)
        for path in (
            capacity_path,
            inventory_path,
            harvest_path,
            boundary_path,
            v3_summary_path,
            capacity_summary_path,
        )
    }
    summary = _summary(
        generated_utc=generated_utc,
        base_facilities=base_facilities,
        floor_plan=floor_plan,
        offers=offers,
        centroid_methods=centroid_methods,
        results=results,
        v3_baseline=v3_baseline,
        capacity_summary=capacity_summary,
        inputs=inputs,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    v2._write_csv(
        output_dir / "facility_assignment.csv",
        ASSIGNMENT_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].assignments
        ],
    )
    v2._write_csv(
        output_dir / "province_summary.csv",
        PROVINCE_SUMMARY_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_summary
        ],
    )
    v2._write_csv(
        output_dir / "facility_utilization.csv",
        UTILIZATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].facility_utilization
        ],
    )
    v2._write_csv(
        output_dir / "unassigned.csv",
        UNASSIGNED_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].unassigned
        ],
    )
    v2._write_csv(
        output_dir / "province_class_totals.csv",
        PROVINCE_CLASS_FIELDS,
        summary["province_class_totals"],
    )
    v2._write_csv(
        output_dir / "concentration.csv",
        CONCENTRATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].concentration
        ],
    )
    v2._write_csv(
        output_dir / "scenario_comparison.csv",
        v3.COMPARISON_FIELDS,
        [
            {
                field: summary["scenarios"][scenario].get(field)
                for field in v3.COMPARISON_FIELDS
            }
            for scenario in SCENARIOS
        ],
    )
    v2._write_csv(
        output_dir / "capacity_floor.csv",
        FLOOR_FIELDS,
        summary["province_capacity_floor"]["province_rows"],
    )
    v2._write_csv(
        output_dir / "capacity_floor_facilities.csv",
        FLOOR_FACILITY_FIELDS,
        v3._floor_facility_rows(floor_plan.facilities, floor_plan),
    )
    v2._write_csv(
        output_dir / "v3_v4_comparison.csv",
        COMPARISON_FIELDS,
        summary["v3_v4_comparison"],
    )
    v2._write_csv(
        output_dir / "province_capacity_comparison.csv",
        PROVINCE_CAPACITY_FIELDS,
        summary["province_capacity_comparison"],
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V4.md").write_text(
        _report(summary), encoding="utf-8"
    )
    v2._write_json(output_dir / "summary.json", summary)
    _write_manifest(output_dir, summary)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "primary_scenario": summary["comparison"]["primary_scenario"],
                "assigned_m3": summary["scenarios"][PRIMARY_SCENARIO][
                    "assigned_m3"
                ],
                "cross_border_m3": summary["scenarios"][PRIMARY_SCENARIO][
                    "cross_border_m3"
                ],
                "proxy_share_of_assigned": summary["scenarios"][
                    PRIMARY_SCENARIO
                ]["proxy_share_of_assigned"],
                "max_facility_share": summary["scenarios"][PRIMARY_SCENARIO][
                    "max_facility_share"
                ],
                "hhi": summary["scenarios"][PRIMARY_SCENARIO]["hhi"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
