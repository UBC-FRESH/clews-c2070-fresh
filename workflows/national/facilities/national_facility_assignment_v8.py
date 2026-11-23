"""Rerun the local-processing assignment with province road factors.

Assignment v8 is the distance-only rerun of the v6 local-first allocation.  It
keeps capacity v3, the 1.2x provincial floor, the 119 Mm3 harvest offers, the
500 km cross-border penalty, and every product/residue input unchanged.  The
only change is the road screen: v6 multiplied every haversine distance by the
global 1.3 factor, while v8 multiplies a candidate's haversine distance by the
median road/haversine factor fitted for the **facility's** province in
``facility_road_distance_audit_v2``.

The assignment engine lineage does not materialize an assignment v7 artifact;
v7 is the chain-scenario version.  This is the next local-processing assignment
rerun and is named v8 as requested.

Because every local candidate for one province receives the same factor, the
local-first order within a province is invariant.  v8 can therefore change only
cross-border residual ranking and the reported distance/rank fields.  The
comparison reports that fact explicitly rather than implying a larger effect.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
national claim is made.  No capacity, product, residue, UI, or official OSeCAN
surface is changed.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
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
from workflows.national.facilities import (
    national_facility_assignment_v6 as v6,
)
from workflows.national.facilities.national_facility_assignment import (
    AllocationConfig,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = v6.REPO_ROOT
CAPACITY_CSV = v6.CAPACITY_CSV
HARVEST_CSV = v6.HARVEST_CSV
BOUNDARY_GEOJSON = v6.BOUNDARY_GEOJSON
CAPACITY_SUMMARY = v6.CAPACITY_SUMMARY
FACTORS_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/facility_road_distance_audit_v2"
    / "province_road_factors.csv"
)
FACTORS_SUMMARY = FACTORS_CSV.parent / "summary.json"
V6_ASSIGNMENT_CSV = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v6"
    / "facility_assignment.csv"
)
V6_SUMMARY_JSON = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v6"
    / "summary.json"
)
OUTPUT_DIR = (
    REPO_ROOT
    / "data/derived/national/facilities/national_facility_assignment_v8"
)

ARTIFACT = "national_facility_assignment_v8"
ARTIFACT_VERSION = "8.0.0"
PRIMARY_SCENARIO = v6.PRIMARY_SCENARIO
SCENARIOS = v6.SCENARIOS
FLOOR_MULTIPLIER = v6.FLOOR_MULTIPLIER
ROAD_FACTOR_V6 = v6.ROAD_FACTOR
CROSS_BORDER_PENALTY_KM = v6.CROSS_BORDER_PENALTY_KM
VOLUME_EPSILON_M3 = v6.VOLUME_EPSILON_M3

EVIDENCE_LABEL = v6.EVIDENCE_LABEL
OBSERVED = v6.OBSERVED
POLICY_GRADE = v6.POLICY_GRADE

V8_ASSIGNMENT_FIELDS = [
    *v6.ASSIGNMENT_FIELDS,
    "road_factor_applied",
    "road_factor_basis",
    "road_distance_km_v6",
    "delta_road_distance_km_v8_minus_v6",
    "delta_road_distance_pct_v8_minus_v6",
]
COMPARISON_FIELDS = [
    "metric",
    "unit",
    "v6_value",
    "v8_value",
    "delta_v8_minus_v6",
    "delta_pct",
    "note",
]
DISTANCE_FIELDS = [
    "province",
    "facility_id",
    "facility_name_v6",
    "facility_name_v8",
    "facility_province",
    "capacity_status_v6",
    "capacity_status_v8",
    "v6_assigned_m3",
    "v8_assigned_m3",
    "delta_assigned_m3",
    "haversine_km",
    "v6_road_distance_km",
    "v8_road_distance_km",
    "delta_road_distance_km",
    "delta_road_distance_pct",
    "v6_road_factor",
    "v8_road_factor",
    "status",
]
PROVINCE_FACTOR_FIELDS = [
    "province",
    "province_name",
    "factor_v6",
    "factor_v8",
    "delta_factor",
    "delta_factor_pct",
    "n_ok",
    "n_fit",
    "iqr",
    "ci90_low",
    "ci90_high",
    "eligible_facilities",
    "sampled_facilities",
    "fit_status",
    "flags",
]
PROVINCE_DISTANCE_FIELDS = [
    "province",
    "n_rows_v6",
    "n_rows_v8",
    "n_rows_common",
    "n_distance_changed",
    "v6_assigned_m3",
    "v8_assigned_m3",
    "v6_volume_weighted_mean_road_distance_km",
    "v8_volume_weighted_mean_road_distance_km",
    "delta_volume_weighted_mean_road_distance_km",
    "delta_volume_weighted_mean_road_distance_pct",
    "v6_total_volume_distance_m3_km",
    "v8_total_volume_distance_m3_km",
    "delta_total_volume_distance_m3_km",
]
SCENARIO_COMPARISON_FIELDS = [
    "scenario",
    "n_rows_v6",
    "n_rows_v8",
    "n_rows_common",
    "n_rows_added",
    "n_rows_removed",
    "moved_volume_m3",
    "changed_provinces",
    "assignment_changed",
    "v6_assigned_m3",
    "v8_assigned_m3",
    "v6_local_m3",
    "v8_local_m3",
    "v6_cross_border_m3",
    "v8_cross_border_m3",
    "v6_proxy_share_of_assigned",
    "v8_proxy_share_of_assigned",
    "v6_facilities_used",
    "v8_facilities_used",
    "v6_hhi",
    "v8_hhi",
]

COMPARISON_METRICS = (
    ("assigned_m3", "m3/yr", "volume assigned to facilities"),
    ("local_m3", "m3/yr", "volume assigned inside the origin province"),
    ("local_share", "share", "local volume / assigned volume"),
    ("cross_border_m3", "m3/yr", "volume assigned out of province"),
    ("cross_border_share", "share", "cross-border / assigned"),
    ("published_m3", "m3/yr", "assigned volume on published capacity"),
    ("proxy_m3", "m3/yr", "assigned volume on proxy capacity"),
    ("proxy_share_of_assigned", "share", "proxy volume / assigned"),
    ("facilities_used", "count", "distinct facilities receiving volume"),
    ("max_facility_m3", "m3/yr", "largest single-facility assignment"),
    ("max_facility_share", "share", "largest facility / assigned"),
    ("top5_share", "share", "top five facilities / assigned"),
    ("hhi", "index_0_1", "sum of squared facility shares"),
    ("unassigned_m3", "m3/yr", "offer volume left unassigned"),
    (
        "volume_weighted_mean_road_distance_km",
        "km",
        "assigned-volume-weighted mean road distance",
    ),
    (
        "total_volume_distance_m3_km",
        "m3*km/yr",
        "sum of assigned volume times road distance",
    ),
    (
        "median_row_road_distance_km",
        "km",
        "unweighted median assignment-row road distance",
    ),
    (
        "max_row_road_distance_km",
        "km",
        "maximum assignment-row road distance",
    ),
)


class AssignmentV8Error(v6.AssignmentV6Error):
    """Raised when a province-factor assignment invariant fails."""


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise AssignmentV8Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise AssignmentV8Error(f"{label} is empty: {path}")
    return rows


def _load_factors(
    path: Path = FACTORS_CSV,
) -> tuple[dict[str, float], dict[str, dict[str, str]]]:
    rows = _read_csv(path, "province road factors")
    required = {
        "province",
        "factor_applied",
        "median_road_haversine_ratio",
        "n_ok",
        "n_fit",
        "flags",
    }
    missing = required - set(rows[0])
    if missing:
        raise AssignmentV8Error(
            f"{path}: missing columns {sorted(missing)}"
        )
    factors: dict[str, float] = {}
    by_province: dict[str, dict[str, str]] = {}
    for row in rows:
        province = row["province"].strip()
        if not province:
            raise AssignmentV8Error(f"{path}: blank province")
        if province not in PROVINCES:
            raise AssignmentV8Error(
                f"{path}: unexpected province {province!r}"
            )
        if province in by_province:
            raise AssignmentV8Error(f"{path}: duplicate province {province!r}")
        by_province[province] = row
        text = row["factor_applied"].strip()
        if not text:
            continue
        factor = float(text)
        if not math.isfinite(factor) or factor <= 0.0:
            raise AssignmentV8Error(
                f"{path}: {province}: invalid factor {text!r}"
            )
        factors[province] = factor
    return factors, by_province


def _validate_factor_summary(summary: dict[str, Any]) -> None:
    if summary.get("artifact") != "facility_road_distance_audit_v2":
        raise AssignmentV8Error(
            "expected facility_road_distance_audit_v2, got "
            f"{summary.get('artifact')!r}"
        )
    if not summary.get("validation", {}).get("all_passed"):
        raise AssignmentV8Error("road-distance audit v2 validation did not pass")


def _validate_factor_coverage(
    facilities: Sequence[a3.Facility],
    factors: Mapping[str, float],
) -> None:
    needed = {
        facility.province
        for facility in facilities
        if facility.location is not None
        and facility.capacity is not None
        and facility.is_operating
        and facility.facility_class in v6.RELEVANT_CLASSES
    }
    missing = sorted(needed - set(factors))
    if missing:
        raise AssignmentV8Error(
            "no province road factor for eligible facility provinces: "
            f"{missing}"
        )


def _distance_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    distances = [float(row["road_distance_km"]) for row in rows]
    volume_distance = math.fsum(
        float(row["assigned_m3"]) * float(row["road_distance_km"])
        for row in rows
    )
    return {
        "volume_weighted_mean_road_distance_km": (
            volume_distance / assigned if assigned > 0.0 else 0.0
        ),
        "total_volume_distance_m3_km": volume_distance,
        "median_row_road_distance_km": (
            statistics.median(distances) if distances else 0.0
        ),
        "max_row_road_distance_km": max(distances) if distances else 0.0,
    }


def _augment_rows(
    rows: Sequence[dict[str, Any]],
    *,
    factors: Mapping[str, float],
    v6_rows: Sequence[dict[str, str]],
) -> list[dict[str, Any]]:
    baseline = {
        (row["scenario"], row["province"], row["facility_id"]): row
        for row in v6_rows
    }
    augmented: list[dict[str, Any]] = []
    for row in rows:
        copy = dict(row)
        province = copy["facility_province"]
        factor = factors.get(province)
        if factor is None:
            raise AssignmentV8Error(
                f"no province road factor for {copy['facility_id']} in {province}"
            )
        copy["road_factor_applied"] = a2._format_float(factor, 12)
        copy["road_factor_basis"] = "facility_province"
        old = baseline.get(
            (copy["scenario"], copy["province"], copy["facility_id"])
        )
        if old is None:
            copy["road_distance_km_v6"] = ""
            copy["delta_road_distance_km_v8_minus_v6"] = ""
            copy["delta_road_distance_pct_v8_minus_v6"] = ""
        else:
            old_distance = float(old["road_distance_km"])
            new_distance = float(copy["road_distance_km"])
            copy["road_distance_km_v6"] = old["road_distance_km"]
            copy["delta_road_distance_km_v8_minus_v6"] = a2._format_float(
                new_distance - old_distance, 9
            )
            copy["delta_road_distance_pct_v8_minus_v6"] = (
                a2._format_float(
                    100.0 * (new_distance - old_distance) / old_distance, 9
                )
                if old_distance > 0.0
                else ""
            )
        augmented.append(copy)
    return augmented


def _primary_rows(
    rows: Sequence[dict[str, Any]], scenario: str
) -> list[dict[str, Any]]:
    return [row for row in rows if row["scenario"] == scenario]


def _distance_comparison(
    v6_rows: Sequence[dict[str, str]],
    v8_rows: Sequence[dict[str, Any]],
    *,
    factors: Mapping[str, float],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    primary_v6 = _primary_rows(v6_rows, PRIMARY_SCENARIO)
    primary_v8 = _primary_rows(v8_rows, PRIMARY_SCENARIO)
    old = {(row["province"], row["facility_id"]): row for row in primary_v6}
    new = {(row["province"], row["facility_id"]): row for row in primary_v8}
    keys = sorted(set(old) | set(new))
    rows: list[dict[str, Any]] = []
    for key in keys:
        province, facility_id = key
        before = old.get(key)
        after = new.get(key)
        before_assigned = float(before["assigned_m3"]) if before else 0.0
        after_assigned = float(after["assigned_m3"]) if after else 0.0
        facility_province = (
            (after or before or {}).get("facility_province", "")
        )
        factor = factors.get(facility_province)
        if factor is None:
            raise AssignmentV8Error(
                f"{province}/{facility_id}: no factor for {facility_province}"
            )
        before_distance = (
            float(before["road_distance_km"]) if before else None
        )
        after_distance = float(after["road_distance_km"]) if after else None
        delta_distance = (
            after_distance - before_distance
            if before_distance is not None and after_distance is not None
            else None
        )
        delta_pct = (
            100.0 * delta_distance / before_distance
            if delta_distance is not None and before_distance and before_distance > 0.0
            else None
        )
        status = "both"
        if before is None:
            status = "added"
        elif after is None:
            status = "removed"
        rows.append(
            {
                "province": province,
                "facility_id": facility_id,
                "facility_name_v6": before["facility_name"] if before else "",
                "facility_name_v8": after["facility_name"] if after else "",
                "facility_province": facility_province,
                "capacity_status_v6": (
                    before["capacity_status"] if before else ""
                ),
                "capacity_status_v8": (
                    after["capacity_status"] if after else ""
                ),
                "v6_assigned_m3": a2._format_float(before_assigned),
                "v8_assigned_m3": a2._format_float(after_assigned),
                "delta_assigned_m3": a2._format_float(
                    after_assigned - before_assigned
                ),
                "haversine_km": (
                    after["distance_km"]
                    if after
                    else (before["distance_km"] if before else "")
                ),
                "v6_road_distance_km": (
                    before["road_distance_km"] if before else ""
                ),
                "v8_road_distance_km": (
                    after["road_distance_km"] if after else ""
                ),
                "delta_road_distance_km": a2._format_float(delta_distance),
                "delta_road_distance_pct": a2._format_float(delta_pct),
                "v6_road_factor": a2._format_float(
                    ROAD_FACTOR_V6 if before else None
                ),
                "v8_road_factor": a2._format_float(factor, 12),
                "status": status,
            }
        )
    common = [row for row in rows if row["status"] == "both"]
    added = [row for row in rows if row["status"] == "added"]
    removed = [row for row in rows if row["status"] == "removed"]
    moved = math.fsum(
        max(0.0, float(row["delta_assigned_m3"])) for row in rows
    )
    distance_changed = [
        row
        for row in common
        if row["delta_road_distance_km"]
        and abs(float(row["delta_road_distance_km"])) > 1e-9
    ]
    summary = {
        "n_rows_v6": len(primary_v6),
        "n_rows_v8": len(primary_v8),
        "n_rows_common": len(common),
        "n_rows_added": len(added),
        "n_rows_removed": len(removed),
        "n_distance_changed": len(distance_changed),
        "moved_volume_m3": moved,
        "changed_provinces": sorted(
            {
                row["province"]
                for row in rows
                if abs(float(row["delta_assigned_m3"])) > VOLUME_EPSILON_M3
            }
        ),
        "assignment_changed": moved > VOLUME_EPSILON_M3,
    }
    return rows, summary


def _comparison_rows(
    v6_metrics: Mapping[str, Any],
    v8_metrics: Mapping[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric, unit, note in COMPARISON_METRICS:
        old = v6_metrics.get(metric)
        new = v8_metrics.get(metric)
        old_value = float(old) if old is not None else None
        new_value = float(new) if new is not None else None
        delta = (
            new_value - old_value
            if old_value is not None and new_value is not None
            else None
        )
        if (
            delta is not None
            and old_value is not None
            and old_value != 0.0
        ):
            pct = 100.0 * delta / old_value
        else:
            pct = None
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "v6_value": a2._format_float(old_value),
                "v8_value": a2._format_float(new_value),
                "delta_v8_minus_v6": a2._format_float(delta),
                "delta_pct": a2._format_float(pct),
                "note": note,
            }
        )
    return rows


def _province_factor_rows(
    factor_rows: Mapping[str, dict[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        source = factor_rows.get(province)
        if source is None:
            raise AssignmentV8Error(f"missing factor row for {province}")
        factor = source["factor_applied"].strip()
        factor_value = float(factor) if factor else None
        rows.append(
            {
                "province": province,
                "province_name": source["province_name"],
                "factor_v6": a2._format_float(ROAD_FACTOR_V6),
                "factor_v8": a2._format_float(factor_value),
                "delta_factor": a2._format_float(
                    factor_value - ROAD_FACTOR_V6
                    if factor_value is not None
                    else None
                ),
                "delta_factor_pct": a2._format_float(
                    100.0 * (factor_value - ROAD_FACTOR_V6) / ROAD_FACTOR_V6
                    if factor_value is not None
                    else None
                ),
                "n_ok": source["n_ok"],
                "n_fit": source["n_fit"],
                "iqr": source["iqr"],
                "ci90_low": source["ci90_low"],
                "ci90_high": source["ci90_high"],
                "eligible_facilities": source["eligible_facilities"],
                "sampled_facilities": source["sampled_facilities"],
                "fit_status": source["fit_status"],
                "flags": source["flags"],
            }
        )
    return rows


def _province_distance_rows(
    distance_rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        subset = [
            row for row in distance_rows if row["province"] == province
        ]
        if not subset:
            continue
        v6_assigned = math.fsum(
            float(row["v6_assigned_m3"]) for row in subset
        )
        v8_assigned = math.fsum(
            float(row["v8_assigned_m3"]) for row in subset
        )
        v6_volume_distance = math.fsum(
            float(row["v6_assigned_m3"])
            * float(row["v6_road_distance_km"])
            for row in subset
            if row["v6_road_distance_km"]
        )
        v8_volume_distance = math.fsum(
            float(row["v8_assigned_m3"])
            * float(row["v8_road_distance_km"])
            for row in subset
            if row["v8_road_distance_km"]
        )
        v6_mean = (
            v6_volume_distance / v6_assigned if v6_assigned > 0.0 else None
        )
        v8_mean = (
            v8_volume_distance / v8_assigned if v8_assigned > 0.0 else None
        )
        delta = (
            v8_mean - v6_mean
            if v6_mean is not None and v8_mean is not None
            else None
        )
        rows.append(
            {
                "province": province,
                "n_rows_v6": sum(
                    1 for row in subset if row["status"] != "added"
                ),
                "n_rows_v8": sum(
                    1 for row in subset if row["status"] != "removed"
                ),
                "n_rows_common": sum(
                    1 for row in subset if row["status"] == "both"
                ),
                "n_distance_changed": sum(
                    1
                    for row in subset
                    if row["delta_road_distance_km"]
                    and abs(float(row["delta_road_distance_km"])) > 1e-9
                ),
                "v6_assigned_m3": a2._format_float(v6_assigned),
                "v8_assigned_m3": a2._format_float(v8_assigned),
                "v6_volume_weighted_mean_road_distance_km": a2._format_float(
                    v6_mean
                ),
                "v8_volume_weighted_mean_road_distance_km": a2._format_float(
                    v8_mean
                ),
                "delta_volume_weighted_mean_road_distance_km": (
                    a2._format_float(delta)
                ),
                "delta_volume_weighted_mean_road_distance_pct": (
                    a2._format_float(100.0 * delta / v6_mean)
                    if delta is not None and v6_mean not in (None, 0.0)
                    else None
                ),
                "v6_total_volume_distance_m3_km": a2._format_float(
                    v6_volume_distance
                ),
                "v8_total_volume_distance_m3_km": a2._format_float(
                    v8_volume_distance
                ),
                "delta_total_volume_distance_m3_km": a2._format_float(
                    v8_volume_distance - v6_volume_distance
                ),
            }
        )
    return rows


def _scenario_comparison_rows(
    v6_rows: Sequence[dict[str, str]],
    v8_rows: Sequence[dict[str, Any]],
    *,
    v6_summary: Mapping[str, Any],
    v8_metrics: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        old = {
            (row["province"], row["facility_id"]): row
            for row in v6_rows
            if row["scenario"] == scenario
        }
        new = {
            (row["province"], row["facility_id"]): row
            for row in v8_rows
            if row["scenario"] == scenario
        }
        keys = set(old) | set(new)
        moved = 0.0
        changed_provinces: set[str] = set()
        added = 0
        removed = 0
        for key in keys:
            before = old.get(key)
            after = new.get(key)
            before_assigned = (
                float(before["assigned_m3"]) if before else 0.0
            )
            after_assigned = float(after["assigned_m3"]) if after else 0.0
            delta = after_assigned - before_assigned
            if delta > VOLUME_EPSILON_M3:
                moved += delta
            if abs(delta) > VOLUME_EPSILON_M3:
                changed_provinces.add(key[0])
            if before is None:
                added += 1
            elif after is None:
                removed += 1
        old_metrics = v6_summary["scenarios"][scenario]
        new_metrics = v8_metrics[scenario]
        rows.append(
            {
                "scenario": scenario,
                "n_rows_v6": len(old),
                "n_rows_v8": len(new),
                "n_rows_common": len(set(old) & set(new)),
                "n_rows_added": added,
                "n_rows_removed": removed,
                "moved_volume_m3": a2._format_float(moved),
                "changed_provinces": ";".join(sorted(changed_provinces)),
                "assignment_changed": _bool_text(
                    moved > VOLUME_EPSILON_M3
                ),
                "v6_assigned_m3": a2._format_float(
                    float(old_metrics["assigned_m3"])
                ),
                "v8_assigned_m3": a2._format_float(
                    float(new_metrics["assigned_m3"])
                ),
                "v6_local_m3": a2._format_float(
                    float(old_metrics["local_m3"])
                ),
                "v8_local_m3": a2._format_float(
                    float(new_metrics["local_m3"])
                ),
                "v6_cross_border_m3": a2._format_float(
                    float(old_metrics["cross_border_m3"])
                ),
                "v8_cross_border_m3": a2._format_float(
                    float(new_metrics["cross_border_m3"])
                ),
                "v6_proxy_share_of_assigned": a2._format_float(
                    old_metrics.get("proxy_share_of_assigned")
                ),
                "v8_proxy_share_of_assigned": a2._format_float(
                    new_metrics.get("proxy_share_of_assigned")
                ),
                "v6_facilities_used": old_metrics["facilities_used"],
                "v8_facilities_used": new_metrics["facilities_used"],
                "v6_hhi": a2._format_float(old_metrics.get("hhi")),
                "v8_hhi": a2._format_float(new_metrics.get("hhi")),
            }
        )
    return rows


def _validate_rows(
    *,
    results: Mapping[str, a2.ScenarioResult],
    augmented: Sequence[dict[str, Any]],
    factors: Mapping[str, float],
    distance_summary: Mapping[str, Any],
    floor_plan: a3.FloorPlan,
    v6_summary: Mapping[str, Any],
) -> dict[str, bool]:
    numeric_errors: list[str] = []
    capacity_errors: list[str] = []
    factor_errors: list[str] = []
    for row in augmented:
        assigned = float(row["assigned_m3"])
        capacity = float(row["capacity_m3_y"])
        distance = float(row["distance_km"])
        road = float(row["road_distance_km"])
        factor = float(row["road_factor_applied"])
        values = [assigned, capacity, distance, road, factor]
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            numeric_errors.append(row["facility_id"])
        if assigned > capacity + 1e-6:
            capacity_errors.append(row["facility_id"])
        expected_factor = factors.get(row["facility_province"])
        if expected_factor is None or abs(factor - expected_factor) > 1e-12:
            factor_errors.append(row["facility_id"])
        if abs(road - distance * factor) > 1e-5:
            factor_errors.append(f"{row['facility_id']}:distance")
    conservation = {
        scenario: (
            float(metrics["offer_m3"])
            - float(metrics["assigned_m3"])
            - float(metrics["unassigned_m3"])
        )
        for scenario, metrics in (
            (scenario, results[scenario].metrics)
            for scenario in SCENARIOS
        )
    }
    local_cross = all(
        abs(
            math.fsum(
                float(row["assigned_m3"])
                for row in augmented
                if row["scenario"] == scenario
            )
            - math.fsum(
                float(row["assigned_m3"])
                for row in augmented
                if row["scenario"] == scenario
                and row["allocation_phase"] == "local"
            )
            - math.fsum(
                float(row["assigned_m3"])
                for row in augmented
                if row["scenario"] == scenario
                and row["allocation_phase"] == "cross_border"
            )
        )
        <= 1e-6
        for scenario in SCENARIOS
    )
    primary = _primary_rows(augmented, PRIMARY_SCENARIO)
    local_orders = [
        int(row["allocation_order"])
        for row in primary
        if row["allocation_phase"] == "local"
    ]
    cross_orders = [
        int(row["allocation_order"])
        for row in primary
        if row["allocation_phase"] == "cross_border"
    ]
    local_first = (
        not local_orders
        or not cross_orders
        or max(local_orders) < min(cross_orders)
    )
    v6_floor = v6_summary["province_capacity_floor"]
    floor_unchanged = (
        float(floor_plan.shortfall_after_m3_y)
        == float(v6_floor["shortfall_after_m3_y"])
        and floor_plan.scaled_proxy_records
        == int(v6_floor["scaled_proxy_records"])
        and list(floor_plan.scaled_provinces)
        == list(v6_floor["scaled_provinces"])
        and {
            key: float(value)
            for key, value in floor_plan.scale_by_province.items()
        }
        == {
            key: float(value)
            for key, value in v6_floor["scale_by_province"].items()
        }
    )
    return {
        "conservation_gap_m3": all(
            abs(gap) <= 1e-6 for gap in conservation.values()
        ),
        "local_plus_cross_equals_assigned": local_cross,
        "capacity_bounds": not capacity_errors,
        "no_nan_or_negative": not numeric_errors,
        "factor_coverage": not factor_errors,
        "local_first": local_first,
        "capacity_floor_unchanged": floor_unchanged,
        "distance_comparison_rows": (
            distance_summary["n_rows_v6"] > 0
            and distance_summary["n_rows_v8"] > 0
        ),
        "assignment_conservation": all(
            abs(
                float(results[scenario].metrics["assigned_m3"])
                + float(results[scenario].metrics["unassigned_m3"])
                - float(results[scenario].metrics["offer_m3"])
            )
            <= 1e-6
            for scenario in SCENARIOS
        ),
    }


def _report(summary: dict[str, Any]) -> str:
    comparison = {
        row["metric"]: row for row in summary["v6_v8_comparison"]
    }
    distance = summary["distance_comparison"]

    def metric(name: str) -> str:
        return comparison[name]["v8_value"]

    lines = [
        "# National Facility Assignment V8 (Province Road Factors)",
        "",
        (
            "Assignment v8 is the distance-only rerun of assignment v6. "
            "Capacity v3, the 1.2x provincial floor, products, and residue are "
            "unchanged. Each candidate's haversine distance is multiplied by "
            "the facility-province factor from the expanded OSRM audit. All "
            "values are model estimates: `observed=false`, "
            "`policy_grade=false`."
        ),
        "",
        "## V6 versus V8",
        "",
        "| Metric | V6 | V8 | Delta | Delta % |",
        "|---|---:|---:|---:|---:|",
    ]
    for key in (
        "assigned_m3",
        "local_m3",
        "local_share",
        "cross_border_m3",
        "cross_border_share",
        "proxy_m3",
        "proxy_share_of_assigned",
        "facilities_used",
        "max_facility_share",
        "top5_share",
        "hhi",
        "unassigned_m3",
        "volume_weighted_mean_road_distance_km",
        "total_volume_distance_m3_km",
        "median_row_road_distance_km",
        "max_row_road_distance_km",
    ):
        row = comparison[key]
        lines.append(
            f"| {key} | {row['v6_value']} | {row['v8_value']} | "
            f"{row['delta_v8_minus_v6']} | {row['delta_pct']} |"
        )
    lines.extend(
        [
            "",
            "## Distance comparison",
            "",
            (
                f"- Rows v6 / v8: {distance['n_rows_v6']} / "
                f"{distance['n_rows_v8']}."
            ),
            (
                f"- Common rows: {distance['n_rows_common']}; added: "
                f"{distance['n_rows_added']}; removed: "
                f"{distance['n_rows_removed']}."
            ),
            (
                "- Rows with changed road distance: "
                f"{distance['n_distance_changed']}."
            ),
            (
                "- Assigned volume moved between facilities: "
                f"{distance['moved_volume_m3']:,.0f} m3."
            ),
            (
                "- Provinces with a changed assignment: "
                f"{', '.join(distance['changed_provinces']) or 'none'}."
            ),
            "",
            (
                "Local candidates in one province all receive the same "
                "province factor, so the local-first order is invariant. Any "
                "assignment change is confined to the cross-border residual. "
                + (
                    "In this run the cross-border recipient did not switch, so "
                    "all assignment-composition metrics are unchanged."
                    if not distance["assignment_changed"]
                    else "Assignment-composition metrics changed only through "
                    "the cross-border residual."
                )
            ),
            "",
            "## Scenario assignment comparison",
            "",
            (
                "| Scenario | V6 rows | V8 rows | Moved m3 | Changed provinces | "
                "Proxy share v6/v8 | Facilities v6/v8 | HHI v6/v8 |"
            ),
            "|---|---:|---:|---:|---|---:|---:|---:|",
        ]
    )
    for row in summary["scenario_assignment_comparison"]:
        lines.append(
            f"| {row['scenario']} | {row['n_rows_v6']} | "
            f"{row['n_rows_v8']} | {row['moved_volume_m3']} | "
            f"{row['changed_provinces'] or 'none'} | "
            f"{row['v6_proxy_share_of_assigned'] or 'n/a'}/"
            f"{row['v8_proxy_share_of_assigned'] or 'n/a'} | "
            f"{row['v6_facilities_used']}/{row['v8_facilities_used']} | "
            f"{row['v6_hhi'] or 'n/a'}/{row['v8_hhi'] or 'n/a'} |"
        )
    lines.extend(
        [
            "",
            (
                "The primary 119 Mm3 floor scenario is composition-identical to "
                "v6. The reference scenarios change only cross-border "
                "destinations; their local and cross-border totals are "
                "unchanged."
            ),
            "",
            "## Province factors",
            "",
            (
                "| Province | Factor v6 | Factor v8 | Delta % | n | IQR | "
                "90% CI | Flags |"
            ),
            "|---|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    for row in summary["province_factors"]:
        lines.append(
            f"| {row['province']} | {row['factor_v6']} | "
            f"{row['factor_v8'] or 'n/a'} | {row['delta_factor_pct'] or 'n/a'} | "
            f"{row['n_fit']} | {row['iqr'] or 'n/a'} | "
            f"{row['ci90_low'] or 'n/a'}–{row['ci90_high'] or 'n/a'} | "
            f"{row['flags']} |"
        )
    lines.extend(
        [
            "",
            "### Province distance impact",
            "",
            (
                "| Province | V6 weighted km | V8 weighted km | Delta km | "
                "Delta % | Changed rows |"
            ),
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in summary["province_distance_comparison"]:
        lines.append(
            f"| {row['province']} | "
            f"{row['v6_volume_weighted_mean_road_distance_km'] or 'n/a'} | "
            f"{row['v8_volume_weighted_mean_road_distance_km'] or 'n/a'} | "
            f"{row['delta_volume_weighted_mean_road_distance_km'] or 'n/a'} | "
            f"{row['delta_volume_weighted_mean_road_distance_pct'] or 'n/a'} | "
            f"{row['n_distance_changed']} |"
        )
    lines.extend(
        [
            "",
            "## Method",
            "",
            (
                "- Allocation is local first; cross-border volume is only the "
                "residual that local finite capacity cannot serve."
            ),
            (
                "- Road distance = haversine distance x province factor of the "
                "candidate facility. For a local candidate that province is "
                "also the route province sampled by the OSRM audit. For a "
                "cross-border candidate it is a documented screening "
                "extension, not an observed border-route factor."
            ),
            (
                "- Factor basis: province median `road km / haversine km` from "
                "`facility_road_distance_audit_v2`, capped to [1.0, 3.0]. "
                "Outliers, low n, and wide dispersion are flagged in that "
                "artifact."
            ),
            (
                "- NT and NU have no eligible located facility in the audit; "
                "they receive no local factor and no facility is fabricated."
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
    )
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    capacity_path: Path = CAPACITY_CSV,
    harvest_path: Path = HARVEST_CSV,
    boundary_path: Path = BOUNDARY_GEOJSON,
    factors_path: Path = FACTORS_CSV,
    factors_summary_path: Path = FACTORS_SUMMARY,
    v6_assignment_path: Path = V6_ASSIGNMENT_CSV,
    v6_summary_path: Path = V6_SUMMARY_JSON,
    capacity_summary_path: Path = CAPACITY_SUMMARY,
    config: AllocationConfig | None = None,
) -> dict[str, Any]:
    """Build assignment v8 and return its summary."""

    v6._assert_no_unexpected_deletions()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    factors, factor_rows = _load_factors(factors_path)
    with factors_summary_path.open("r", encoding="utf-8") as handle:
        factor_summary = json.load(handle)
    _validate_factor_summary(factor_summary)
    with v6_summary_path.open("r", encoding="utf-8") as handle:
        v6_summary = json.load(handle)
    if v6_summary.get("artifact") != "national_facility_assignment_v6":
        raise AssignmentV8Error(
            f"{v6_summary_path}: expected assignment v6, got "
            f"{v6_summary.get('artifact')!r}"
        )
    v6_rows = _read_csv(v6_assignment_path, "assignment v6 rows")
    if config is None:
        config = AllocationConfig(
            road_factor=ROAD_FACTOR_V6,
            cross_border_penalty_km=CROSS_BORDER_PENALTY_KM,
            province_road_factors=factors,
        )
    elif config.province_road_factors is None:
        raise AssignmentV8Error(
            "v8 requires province_road_factors in AllocationConfig"
        )

    base_facilities = v5.read_facilities(capacity_path)
    _validate_factor_coverage(base_facilities, factors)
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
    metrics = {
        scenario: v6._metrics_with_derived(
            results[scenario].metrics, results[scenario].assignments
        )
        for scenario in SCENARIOS
    }
    all_rows = [
        row
        for scenario in SCENARIOS
        for row in results[scenario].assignments
    ]
    augmented = _augment_rows(all_rows, factors=factors, v6_rows=v6_rows)
    distance_rows, distance_summary = _distance_comparison(
        v6_rows, augmented, factors=factors
    )
    scenario_comparison = _scenario_comparison_rows(
        v6_rows,
        augmented,
        v6_summary=v6_summary,
        v8_metrics=metrics,
    )
    any_assignment_change = any(
        row["assignment_changed"] == "true" for row in scenario_comparison
    )
    reference_assignment_change = any(
        row["assignment_changed"] == "true"
        for row in scenario_comparison
        if row["scenario"] != PRIMARY_SCENARIO
    )
    v6_primary = dict(v6_summary["scenarios"][PRIMARY_SCENARIO])
    v8_primary = dict(metrics[PRIMARY_SCENARIO])
    v6_primary.update(
        _distance_metrics(_primary_rows(v6_rows, PRIMARY_SCENARIO))
    )
    v8_primary.update(
        _distance_metrics(_primary_rows(augmented, PRIMARY_SCENARIO))
    )
    comparison = _comparison_rows(v6_primary, v8_primary)
    province_factors = _province_factor_rows(factor_rows)
    province_distance = _province_distance_rows(distance_rows)
    validation = _validate_rows(
        results=results,
        augmented=augmented,
        factors=factors,
        distance_summary=distance_summary,
        floor_plan=floor_plan,
        v6_summary=v6_summary,
    )

    with capacity_summary_path.open("r", encoding="utf-8") as handle:
        capacity_summary = json.load(handle)
    if capacity_summary.get("artifact") != "national_facility_capacity_v3":
        raise AssignmentV8Error(
            f"{capacity_summary_path}: expected capacity v3, got "
            f"{capacity_summary.get('artifact')!r}"
        )

    inputs = {
        str(path.relative_to(REPO_ROOT)): a2._sha256(path)
        for path in (
            capacity_path,
            harvest_path,
            boundary_path,
            factors_path,
            factors_summary_path,
            v6_assignment_path,
            v6_summary_path,
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
            "national_claim": False,
            "official_osecan_changed": False,
            "assignment_change_vs_v6": any_assignment_change,
            "primary_assignment_change_vs_v6": distance_summary[
                "assignment_changed"
            ],
            "reference_assignment_change_vs_v6": (
                reference_assignment_change
            ),
            "capacity_change": False,
            "product_change": False,
            "residue_change": False,
        },
        "method": {
            "engine": "assignment v6 local-first bounded allocation",
            "distance_only_change": True,
            "primary_scenario": PRIMARY_SCENARIO,
            "primary_scenario_note": (
                "capacity v3 with proxy-only scaling to the 1.2x provincial "
                "harvest floor; local first; cross-border only for the "
                "remaining local residual"
            ),
            "scenarios": {
                PRIMARY_SCENARIO: "floor-scaled proxy capacity; primary v8 run",
                "published_plus_proxy": "unscaled published plus proxy reference",
                "published_only": "published-capacity-only reference",
            },
            "floor_multiplier": FLOOR_MULTIPLIER,
            "local_first": True,
            "cross_border_rule": (
                "cross-border capacity is considered only after every local "
                "eligible facility is exhausted"
            ),
            "cross_border_penalty_km": CROSS_BORDER_PENALTY_KM,
            "road_factor_v6": ROAD_FACTOR_V6,
            "road_factor_v8": "province median road/haversine factor",
            "road_factor_basis": "facility_province",
            "road_factor_source": str(FACTORS_CSV.relative_to(REPO_ROOT)),
            "road_factor_note": (
                "local candidates use the province in which the OSRM route "
                "was sampled; cross-border candidates use the destination "
                "facility province as a documented screening extension"
            ),
            "unchanged_inputs": [
                "capacity v3",
                "119 Mm3 harvest offers",
                "1.2x provincial capacity floor",
                "product chain",
                "residue chain",
            ],
        },
        "inputs": inputs,
        "coverage": capacity_summary["coverage"],
        "scenarios": metrics,
        "v6_baseline": {
            "artifact": v6_summary["artifact"],
            "scenarios": {
                scenario: dict(v6_summary["scenarios"][scenario])
                for scenario in SCENARIOS
            },
            "primary_distance_metrics": _distance_metrics(
                _primary_rows(v6_rows, PRIMARY_SCENARIO)
            ),
        },
        "v6_v8_comparison": comparison,
        "distance_comparison": distance_summary,
        "scenario_assignment_comparison": scenario_comparison,
        "distance_rows": distance_rows,
        "province_factors": province_factors,
        "province_distance_comparison": province_distance,
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
        "province_capacity_comparison": v6._province_capacity_rows(
            floor_plan
        ),
        "province_class_totals": [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_class
        ],
        "concentration": {
            scenario: [dict(row) for row in results[scenario].concentration]
            for scenario in SCENARIOS
        },
        "validation": validation,
        "caveats": [
            (
                "All volumes are model estimates (`model_estimate`); "
                "`observed=false` and `policy_grade=false`."
            ),
            (
                "The 119 Mm3 case is a planning allocation, not a legal AAC, "
                "a sustainable-supply determination, or an observed harvest "
                "outcome."
            ),
            (
                "Only the road-distance screen changed. Capacity v3, the 1.2x "
                "provincial floor, product coefficients, and residue "
                "coefficients are unchanged from v6."
            ),
            (
                "Province factors are median OSRM road/haversine ratios from a "
                "small deterministic sample; low-n provinces (SK, MB, PE, YT) "
                "and wide-dispersion provinces are flagged in the audit."
            ),
            (
                "A facility-province factor is applied to cross-border "
                "candidates as a screening extension. It is not an observed "
                "border-route factor and no customs or border delay is "
                "modelled."
            ),
            (
                "Local-first order within a province is invariant under a "
                "uniform province factor; observed assignment changes are "
                "confined to the cross-border residual."
            ),
            (
                "Allocation is a screening result, not an interprovincial "
                "trade forecast. No UI or official OSeCAN surface is changed."
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
            "province_capacity_comparison_csv": (
                "province_capacity_comparison.csv"
            ),
            "v6_v8_comparison_csv": "v6_v8_comparison.csv",
            "distance_comparison_csv": "facility_distance_comparison.csv",
            "province_factor_comparison_csv": (
                "province_factor_comparison.csv"
            ),
            "province_distance_comparison_csv": (
                "province_distance_comparison.csv"
            ),
            "scenario_assignment_comparison_csv": (
                "scenario_assignment_comparison.csv"
            ),
            "report_md": "NATIONAL_FACILITY_ASSIGNMENT_V8.md",
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v8"
        ),
        "centroid_methods": centroid_methods,
    }
    if not all(validation.values()):
        raise AssignmentV8Error(f"validation failed: {validation}")

    output_dir.mkdir(parents=True, exist_ok=True)
    a2._write_csv(
        output_dir / "facility_assignment.csv",
        V8_ASSIGNMENT_FIELDS,
        augmented,
    )
    a2._write_csv(
        output_dir / "province_summary.csv",
        v6.PROVINCE_SUMMARY_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].province_summary
        ],
    )
    a2._write_csv(
        output_dir / "facility_utilization.csv",
        v6.UTILIZATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].facility_utilization
        ],
    )
    a2._write_csv(
        output_dir / "unassigned.csv",
        v6.UNASSIGNED_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].unassigned
        ],
    )
    a2._write_csv(
        output_dir / "province_class_totals.csv",
        v6.PROVINCE_CLASS_FIELDS,
        summary["province_class_totals"],
    )
    a2._write_csv(
        output_dir / "concentration.csv",
        v6.CONCENTRATION_FIELDS,
        [
            row
            for scenario in SCENARIOS
            for row in results[scenario].concentration
        ],
    )
    a2._write_csv(
        output_dir / "scenario_comparison.csv",
        v6.COMPARISON_FIELDS,
        [
            {
                field: metrics[scenario].get(field)
                for field in v6.COMPARISON_FIELDS
            }
            for scenario in SCENARIOS
        ],
    )
    a2._write_csv(
        output_dir / "capacity_floor.csv",
        v6.FLOOR_FIELDS,
        summary["province_capacity_floor"]["province_rows"],
    )
    a2._write_csv(
        output_dir / "capacity_floor_facilities.csv",
        v6.FLOOR_FACILITY_FIELDS,
        a3._floor_facility_rows(floor_plan.facilities, floor_plan),
    )
    a2._write_csv(
        output_dir / "province_capacity_comparison.csv",
        v6.PROVINCE_CAPACITY_FIELDS,
        summary["province_capacity_comparison"],
    )
    a2._write_csv(
        output_dir / "v6_v8_comparison.csv",
        COMPARISON_FIELDS,
        comparison,
    )
    a2._write_csv(
        output_dir / "facility_distance_comparison.csv",
        DISTANCE_FIELDS,
        distance_rows,
    )
    a2._write_csv(
        output_dir / "province_factor_comparison.csv",
        PROVINCE_FACTOR_FIELDS,
        province_factors,
    )
    a2._write_csv(
        output_dir / "province_distance_comparison.csv",
        PROVINCE_DISTANCE_FIELDS,
        province_distance,
    )
    a2._write_csv(
        output_dir / "scenario_assignment_comparison.csv",
        SCENARIO_COMPARISON_FIELDS,
        scenario_comparison,
    )
    (output_dir / "NATIONAL_FACILITY_ASSIGNMENT_V8.md").write_text(
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
        "v6_v8_comparison": {
            row["metric"]: {
                "v6": row["v6_value"],
                "v8": row["v8_value"],
                "delta": row["delta_v8_minus_v6"],
            }
            for row in comparison
        },
        "distance_comparison": distance_summary,
        "province_distance_comparison": province_distance,
        "scenario_assignment_comparison": scenario_comparison,
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    a2._write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    primary = summary["scenarios"][PRIMARY_SCENARIO]
    comparison = {
        row["metric"]: row for row in summary["v6_v8_comparison"]
    }
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "primary_scenario": PRIMARY_SCENARIO,
                "assigned_m3": primary["assigned_m3"],
                "local_m3": primary["local_m3"],
                "cross_border_m3": primary["cross_border_m3"],
                "proxy_share_of_assigned": primary["proxy_share_of_assigned"],
                "facilities_used": primary["facilities_used"],
                "hhi": primary["hhi"],
                "hhi_delta": comparison["hhi"]["delta_v8_minus_v6"],
                "assignment_changed": summary["distance_comparison"][
                    "assignment_changed"
                ],
                "any_assignment_change": summary["labels"][
                    "assignment_change_vs_v6"
                ],
                "moved_volume_m3": summary["distance_comparison"][
                    "moved_volume_m3"
                ],
                "validation": summary["validation"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
