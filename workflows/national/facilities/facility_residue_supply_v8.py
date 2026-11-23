"""Build facility residue supply v8 on the node transport-aware assignment v10.

Version 8 keeps the E1 measured + E2c density-calibrated product chain v5 and
the class-matching residue rule from v7.  The allocation weight changes from the
assignment-v6 province-centroid local-first rows to the assignment-v10
node-to-facility transport-aware LP, aggregated to origin-province / facility
links.  National product and residue totals are invariant; facility and
province distributions and transport cost change.

The artifact also carries the v4-versus-v5 transport and facility-distribution
comparison.  All rows remain ``model_estimate=true``, ``observed=false``,
``policy_grade=false``.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_residue_supply as _base
from workflows.national.facilities import facility_residue_supply_v4 as _v4
from workflows.national.facilities import facility_residue_supply_v7 as _v7
from workflows.national.facilities import national_facility_assignment_v9 as _v9
from workflows.national.facilities import national_facility_assignment_v10 as _v10

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/facility_residue_supply_v8"
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v5"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)
V4_FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v7"
)
V4_ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v6"
)
V10_ASSIGNMENT_CSV = ASSIGNMENT_DIR / "v10_node_assignment.csv"
V10_FACILITY_CSV = ASSIGNMENT_DIR / "v10_facility_utilization.csv"
V10_SUMMARY = ASSIGNMENT_DIR / "summary.json"
V4_FACILITY_CSV = V4_FACILITY_DIR / "facility_residue_supply.csv"
V4_FACILITY_SUMMARY = V4_FACILITY_DIR / "summary.json"
V4_ASSIGNMENT_CSV = V4_ASSIGNMENT_DIR / "facility_assignment.csv"

ARTIFACT = "facility_residue_supply_v8"
ARTIFACT_VERSION = "8.0.0"
ARTIFACT_TITLE = "National Facility Residue Supply v8 (Node Transport-Aware v10)"
REPORT_NAME = "FACILITY_RESIDUE_SUPPLY_V8.md"
MODULE_PATH = "workflows.national.facilities.facility_residue_supply_v8"
TEST_PATH = "tests/test_national_facility_chain_v5.py"
PRODUCT_CHAIN_ARTIFACT = "national_product_chain_v5"
PRODUCT_CHAIN_LABEL = (
    "national_product_chain_v5 E1 measured + E2c density-calibrated "
    "coefficients"
)
ASSIGNMENT_VERSION = "v10"
ASSIGNMENT_LABEL = "assignment v10"
ASSIGNMENT_ARTIFACT = "national_facility_assignment_v10"
ASSIGNMENT_SUMMARY_KEY = "assignment_v10"
ALLOCATION_BASIS = "assignment_v10_node_transport_aware_assigned_m3"
UTILIZATION_FIELD = "assignment_utilization_v10"
SCENARIO = _v10.SCENARIO
PJ_GJ = _base.PJ_GJ
DISTANCE_BANDS: tuple[tuple[str, float, float], ...] = (
    ("le_50_km", 0.0, 50.0),
    ("gt_50_le_100_km", 50.0, 100.0),
    ("gt_100_le_150_km", 100.0, 150.0),
    ("gt_150_le_300_km", 150.0, 300.0),
    ("gt_300_km", 300.0, math.inf),
)

_SAVED_V7 = (
    "OUTPUT_DIR",
    "PRODUCT_CHAIN_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
    "PRODUCT_CHAIN_ARTIFACT",
    "PRODUCT_CHAIN_LABEL",
)
_SAVED_BASE = (
    "ASSIGNMENT_DIR",
    "ASSIGNMENT_VERSION",
    "ASSIGNMENT_LABEL",
    "ASSIGNMENT_ARTIFACT",
    "ASSIGNMENT_SUMMARY_KEY",
    "ASSIGNMENT_CSV_NAME",
    "ASSIGNMENT_UTILIZATION_CSV_NAME",
    "ASSIGNMENT_SUMMARY_NAME",
    "ALLOCATION_BASIS",
    "UTILIZATION_FIELD",
    "UTILIZATION_MODE",
    "SCENARIO",
)
_V4_OVERRIDE_KEYS = (
    "ASSIGNMENT_DIR",
    "ASSIGNMENT_VERSION",
    "ASSIGNMENT_LABEL",
    "ASSIGNMENT_ARTIFACT",
    "ASSIGNMENT_SUMMARY_KEY",
    "ALLOCATION_BASIS",
    "UTILIZATION_FIELD",
    "UTILIZATION_MODE",
    "SCENARIO",
)


def _read_rows(path: Path, required: tuple[str, ...], label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise _base.FacilityResidueSupplyError(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = set(required) - set(reader.fieldnames or ())
        if missing:
            raise _base.FacilityResidueSupplyError(
                f"{label} is missing columns {sorted(missing)}: {path}"
            )
        rows = list(reader)
    if not rows:
        raise _base.FacilityResidueSupplyError(f"{label} is empty: {path}")
    return rows


def _distance_band(distance_km: float) -> str:
    for name, lower, upper in DISTANCE_BANDS:
        if lower <= distance_km < upper:
            return name
    raise _base.FacilityResidueSupplyError(
        f"distance is outside the band design: {distance_km!r}"
    )


def _weighted_quantile(
    pairs: list[tuple[float, float]], quantile: float
) -> float:
    if not pairs:
        return 0.0
    ordered = sorted(pairs)
    total = math.fsum(weight for _, weight in ordered)
    if total <= 0.0:
        return 0.0
    target = quantile * total
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= target:
            return value
    return ordered[-1][0]


def _band_shares(pairs: list[tuple[float, float]]) -> dict[str, float]:
    total = math.fsum(weight for _, weight in pairs)
    if total <= 0.0:
        raise _base.FacilityResidueSupplyError("distance band weight is zero")
    assigned: dict[str, float] = defaultdict(float)
    for distance, weight in pairs:
        assigned[_distance_band(distance)] += weight
    return {
        name: assigned.get(name, 0.0) / total
        for name, _, _ in DISTANCE_BANDS
    }


def _concentration(values: list[float]) -> tuple[float, float, float]:
    total = math.fsum(values)
    if total <= 0.0:
        return 0.0, 0.0, 0.0
    shares = sorted((value / total for value in values), reverse=True)
    hhi = math.fsum(share * share for share in shares)
    return hhi, shares[0], math.fsum(shares[:5])


def _parse_v10_assignment() -> tuple[list[Any], dict[str, Any]]:
    """Parse assignment v10 node rows into validated origin/facility links."""

    required = (
        "scenario",
        "node_province",
        "facility_id",
        "facility_name",
        "facility_class",
        "facility_province",
        "cross_border",
        "capacity_status",
        "capacity_evidence",
        "floor_scale_factor",
        "capacity_m3_y",
        "assigned_m3",
        "distance_km",
        "road_distance_km",
        "transport_cost_cad",
        "transport_cost_cad_low",
        "transport_cost_cad_high",
        "transport_energy_gj",
        "evidence_label",
        "observed",
        "policy_grade",
    )
    rows = _read_rows(V10_ASSIGNMENT_CSV, required, "assignment v10 node rows")
    try:
        summary = json.loads(V10_SUMMARY.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise _base.FacilityResidueSupplyError(
            f"assignment v10 summary is missing: {V10_SUMMARY}"
        ) from exc
    if summary.get("artifact") != ASSIGNMENT_ARTIFACT:
        raise _base.FacilityResidueSupplyError(
            f"unexpected assignment artifact: {summary.get('artifact')!r}"
        )
    labels = summary.get("labels", {})
    if (
        labels.get("observed") is not False
        or labels.get("policy_grade") is not False
        or labels.get("national_claim") is not False
        or labels.get("official_osecan_changed") is not False
    ):
        raise _base.FacilityResidueSupplyError(
            "assignment v10 labels are not screening labels"
        )
    if summary.get("validation", {}).get("all_passed") is not True:
        raise _base.FacilityResidueSupplyError(
            "assignment v10 did not pass validation"
        )

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    facility_totals: dict[str, float] = defaultdict(float)
    facility_meta: dict[str, tuple[Any, ...]] = {}
    for position, row in enumerate(rows, start=2):
        scenario = row["scenario"].strip()
        if scenario != SCENARIO:
            raise _base.FacilityResidueSupplyError(
                f"assignment v10 row {position}: unexpected scenario "
                f"{scenario!r}"
            )
        origin = row["node_province"].strip().upper()
        if origin not in _base.PROVINCES:
            raise _base.FacilityResidueSupplyError(
                f"assignment v10 row {position}: unexpected node province "
                f"{origin!r}"
            )
        facility_class = row["facility_class"].strip().upper()
        if facility_class not in _base.CLASS_ORDER:
            raise _base.FacilityResidueSupplyError(
                f"assignment v10 row {position}: unexpected facility_class "
                f"{facility_class!r}"
            )
        facility_province = row["facility_province"].strip().upper()
        if facility_province not in _base.PROVINCES:
            raise _base.FacilityResidueSupplyError(
                f"assignment v10 row {position}: unexpected facility province "
                f"{facility_province!r}"
            )
        facility_id = row["facility_id"].strip()
        facility_name = row["facility_name"].strip()
        if not facility_id:
            raise _base.FacilityResidueSupplyError(
                f"assignment v10 row {position}: empty facility_id"
            )
        capacity_status = row["capacity_status"].strip().lower()
        if capacity_status not in _base.CAPACITY_STATUSES:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: unexpected capacity status {capacity_status!r}"
            )
        capacity_evidence = row["capacity_evidence"].strip()
        allowed_evidence = set(_base.CAPACITY_EVIDENCE_ALLOWED[capacity_status])
        if capacity_status == _base.PUBLISHED:
            allowed_evidence.update(
                {
                    "published_mills_db_2015_infeed_proxy",
                    "published_capacity_converted",
                    "published_authorized_consumption",
                    "published_sawn_output_converted",
                }
            )
        if capacity_evidence not in allowed_evidence:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: capacity evidence {capacity_evidence!r} "
                f"does not match {capacity_status!r}"
            )
        capacity = _base._finite_nonnegative(
            row["capacity_m3_y"], f"{facility_id} capacity"
        )
        assigned = _base._finite_nonnegative(
            row["assigned_m3"], f"{facility_id} assigned"
        )
        if capacity <= 0.0:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: non-positive capacity"
            )
        if assigned <= 0.0:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: non-positive assigned volume"
            )
        if assigned > capacity * (1.0 + 1e-9) + 1e-6:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: assigned {assigned} exceeds capacity {capacity}"
            )
        scale = _base._finite_nonnegative(
            row["floor_scale_factor"], f"{facility_id} floor_scale_factor"
        )
        if capacity_status == _base.PUBLISHED:
            _base._require_close(scale, 1.0, f"{facility_id} published scale")
        if capacity_status == _base.PROXY:
            _base._require_close(scale, 1.0, f"{facility_id} proxy scale")
        if capacity_status == _base.SCALED_PROXY and scale <= 1.0 + 1e-12:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: scaled proxy must have scale > 1"
            )
        cross_border = _base._parse_bool(
            row["cross_border"], f"{facility_id} cross_border"
        )
        if cross_border != (facility_province != origin):
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: cross-border flag does not match provinces"
            )
        if row["evidence_label"].strip() != _base.EVIDENCE_LABEL:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: unexpected evidence label"
            )
        if _base._parse_bool(row["observed"], f"{facility_id} observed"):
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: assignment row is unexpectedly observed"
            )
        if _base._parse_bool(row["policy_grade"], f"{facility_id} policy_grade"):
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: assignment row is unexpectedly policy grade"
            )
        distance = _base._finite_nonnegative(
            row["distance_km"], f"{facility_id} distance_km"
        )
        road_distance = _base._finite_nonnegative(
            row["road_distance_km"], f"{facility_id} road_distance_km"
        )
        if road_distance + 1e-9 < distance:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: road distance is below haversine distance"
            )
        cost = _base._finite_nonnegative(
            row["transport_cost_cad"], f"{facility_id} transport_cost_cad"
        )
        cost_low = _base._finite_nonnegative(
            row["transport_cost_cad_low"],
            f"{facility_id} transport_cost_cad_low",
        )
        cost_high = _base._finite_nonnegative(
            row["transport_cost_cad_high"],
            f"{facility_id} transport_cost_cad_high",
        )
        energy = _base._finite_nonnegative(
            row["transport_energy_gj"], f"{facility_id} transport_energy_gj"
        )
        if not (cost_low <= cost <= cost_high):
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: transport cost scenarios are not ordered"
            )
        meta = (
            facility_name,
            facility_class,
            facility_province,
            capacity_status,
            capacity_evidence,
            scale,
            capacity,
        )
        previous = facility_meta.get(facility_id)
        if previous is not None and previous != meta:
            raise _base.FacilityResidueSupplyError(
                f"{facility_id}: facility metadata changes across rows"
            )
        facility_meta[facility_id] = meta
        key = (origin, facility_id)
        group = groups.get(key)
        if group is None:
            group = {
                "origin_province": origin,
                "facility_id": facility_id,
                "facility_name": facility_name,
                "facility_class": facility_class,
                "facility_province": facility_province,
                "cross_border": cross_border,
                "capacity_status": capacity_status,
                "capacity_evidence": capacity_evidence,
                "floor_scale_factor": scale,
                "capacity_m3_y": capacity,
                "assigned_m3": 0.0,
                "transport_cost_cad": 0.0,
                "transport_cost_cad_low": 0.0,
                "transport_cost_cad_high": 0.0,
                "transport_energy_gj": 0.0,
                "volume_distance_m3_km": 0.0,
                "distance_rows": [],
            }
            groups[key] = group
        group["assigned_m3"] += assigned
        group["transport_cost_cad"] += cost
        group["transport_cost_cad_low"] += cost_low
        group["transport_cost_cad_high"] += cost_high
        group["transport_energy_gj"] += energy
        group["volume_distance_m3_km"] += assigned * road_distance
        group["distance_rows"].append((road_distance, assigned))
        facility_totals[facility_id] += assigned

    links: list[Any] = []
    for key in sorted(groups):
        group = groups[key]
        assigned = float(group["assigned_m3"])
        capacity = float(group["capacity_m3_y"])
        if assigned > capacity * (1.0 + 1e-9) + 1e-6:
            raise _base.FacilityResidueSupplyError(
                f"{group['facility_id']}: aggregated assigned exceeds capacity"
            )
        utilization = assigned / capacity
        _base._require_between(
            utilization, 0.0, 1.0 + 1e-9, "assignment row utilization"
        )
        links.append(
            _base.AssignmentLink(
                origin_province=group["origin_province"],
                facility_id=group["facility_id"],
                facility_name=group["facility_name"],
                facility_class=group["facility_class"],
                facility_province=group["facility_province"],
                cross_border=group["cross_border"],
                capacity_status=group["capacity_status"],
                capacity_evidence=group["capacity_evidence"],
                floor_scale_factor=group["floor_scale_factor"],
                capacity_m3_y=capacity,
                assigned_m3=assigned,
                utilization=utilization,
                evidence_tier=(
                    f"{SCENARIO}+{group['capacity_evidence']}"
                ),
            )
        )

    assigned_total = math.fsum(link.assigned_m3 for link in links)
    primary = summary.get("v10_primary", {})
    _base._require_close(
        assigned_total,
        float(primary["assigned_m3"]),
        "assignment v10 assigned total",
    )
    facility_values = list(facility_totals.values())
    hhi, top1, top5 = _concentration(facility_values)
    cross_border_total = math.fsum(
        link.assigned_m3 for link in links if link.cross_border
    )
    published = math.fsum(
        link.assigned_m3
        for link in links
        if link.capacity_status == _base.PUBLISHED
    )
    proxy = assigned_total - published
    facilities_used = len(facility_totals)
    if facilities_used != int(primary["facilities_used"]):
        raise _base.FacilityResidueSupplyError(
            f"assignment v10 facilities used {facilities_used} does not match "
            f"summary {primary['facilities_used']}"
        )
    _base._require_close(
        cross_border_total,
        float(primary["cross_border_m3"]),
        "assignment v10 cross-border volume",
    )
    _base._require_close(
        hhi,
        float(primary.get("hhi", hhi)),
        "assignment v10 HHI",
    )
    scaled_facilities = {
        facility_id
        for facility_id, meta in facility_meta.items()
        if meta[3] == _base.SCALED_PROXY
    }
    scenario_metrics = {
        "scenario": SCENARIO,
        "assigned_m3": assigned_total,
        "assigned_share": 1.0,
        "unassigned_m3": 0.0,
        "facilities_used": facilities_used,
        "proxy_share_of_assigned": proxy / assigned_total,
        "cross_border_m3": cross_border_total,
        "cross_border_share": cross_border_total / assigned_total,
        "published_m3": published,
        "proxy_m3": proxy,
        "hhi": hhi,
        "max_facility_share": top1,
        "top5_share": top5,
        "transport_cost_cad": float(primary["transport_cost_cad"]),
        "transport_cost_cad_m3": float(primary["transport_cost_cad_m3"]),
        "transport_cost_cad_gj": float(primary["transport_cost_cad_gj"]),
        "mean_road_distance_km": float(primary["mean_road_distance_km"]),
        "share_ge_150_km": float(primary["share_ge_150_km"]),
        "share_ge_300_km": float(primary["share_ge_300_km"]),
    }
    summary_block = {
        "artifact": ASSIGNMENT_ARTIFACT,
        "artifact_version": summary.get("artifact_version", ""),
        "v10_primary": primary,
        "scenarios": {SCENARIO: scenario_metrics},
        "province_capacity_floor": {
            "scaled_proxy_records": len(scaled_facilities),
            "scaled_provinces": sorted(
                {
                    facility_meta[facility_id][2]
                    for facility_id in scaled_facilities
                }
            ),
        },
        "transport_cost_basis": summary.get("method", {}).get(
            "transport_model", ""
        ),
    }
    return links, summary_block


def _load_transport_parameters() -> dict[str, Any]:
    return _v9.load_transport_parameters()


def _v4_transport_rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = _read_rows(
        V4_ASSIGNMENT_CSV,
        (
            "scenario",
            "province",
            "facility_id",
            "facility_name",
            "facility_class",
            "facility_province",
            "cross_border",
            "capacity_status",
            "assigned_m3",
            "road_distance_km",
        ),
        "assignment v6 rows",
    )
    parameters = _load_transport_parameters()
    selected = [
        row
        for row in rows
        if row["scenario"].strip() == "province_capacity_floor"
    ]
    if not selected:
        raise _base.FacilityResidueSupplyError(
            "assignment v6 has no province_capacity_floor rows"
        )
    metric: dict[str, Any] = {
        "assigned_m3": 0.0,
        "transport_cost_cad": 0.0,
        "transport_cost_cad_low": 0.0,
        "transport_cost_cad_high": 0.0,
        "transport_energy_gj": 0.0,
        "volume_distance_m3_km": 0.0,
    }
    distances: list[tuple[float, float]] = []
    by_province: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "assigned_m3": 0.0,
            "transport_cost_cad": 0.0,
            "transport_energy_gj": 0.0,
            "volume_distance_m3_km": 0.0,
            "cross_border_m3": 0.0,
        }
    )
    by_facility: dict[str, dict[str, Any]] = {}
    for row in selected:
        assigned = _base._finite_nonnegative(
            row["assigned_m3"], "assignment v6 assigned_m3"
        )
        distance = _base._finite_nonnegative(
            row["road_distance_km"], "assignment v6 road_distance_km"
        )
        cost = _v9.transport_metrics(distance, parameters)
        total_cost = assigned * cost["cost_m3_base"]
        metric["assigned_m3"] += assigned
        metric["transport_cost_cad"] += total_cost
        metric["transport_cost_cad_low"] += assigned * cost["cost_m3_low"]
        metric["transport_cost_cad_high"] += assigned * cost["cost_m3_high"]
        metric["transport_energy_gj"] += assigned * _v9.ENERGY_GJ_PER_M3
        metric["volume_distance_m3_km"] += assigned * distance
        distances.append((distance, assigned))
        origin = row["province"].strip().upper()
        province = by_province[origin]
        province["assigned_m3"] += assigned
        province["transport_cost_cad"] += total_cost
        province["transport_energy_gj"] += assigned * _v9.ENERGY_GJ_PER_M3
        province["volume_distance_m3_km"] += assigned * distance
        if row["cross_border"].strip().lower() == "true":
            province["cross_border_m3"] += assigned
        facility_id = row["facility_id"].strip()
        by_facility[facility_id] = {
            "origin_province": origin,
            "facility_id": facility_id,
            "facility_name": row["facility_name"].strip(),
            "facility_class": row["facility_class"].strip().upper(),
            "facility_province": row["facility_province"].strip().upper(),
            "assigned_m3": assigned,
            "transport_cost_cad": total_cost,
            "transport_cost_cad_low": assigned * cost["cost_m3_low"],
            "transport_cost_cad_high": assigned * cost["cost_m3_high"],
            "transport_energy_gj": assigned * _v9.ENERGY_GJ_PER_M3,
            "road_distance_km": distance,
            "cross_border": row["cross_border"].strip().lower() == "true",
            "capacity_status": row["capacity_status"].strip().lower(),
        }
    metric["facilities_used"] = len(by_facility)
    metric["mean_road_distance_km"] = (
        metric["volume_distance_m3_km"] / metric["assigned_m3"]
    )
    metric["transport_cost_cad_m3"] = (
        metric["transport_cost_cad"] / metric["assigned_m3"]
    )
    metric["transport_cost_cad_gj"] = (
        metric["transport_cost_cad"] / metric["transport_energy_gj"]
    )
    metric["weighted_median_road_distance_km"] = _weighted_quantile(
        distances, 0.50
    )
    metric["p90_road_distance_km"] = _weighted_quantile(distances, 0.90)
    bands = _band_shares(distances)
    metric["share_ge_150_km"] = (
        bands["gt_150_le_300_km"] + bands["gt_300_km"]
    )
    metric["share_ge_300_km"] = bands["gt_300_km"]
    metric["distance_bands"] = bands
    metric["cross_border_m3"] = math.fsum(
        item["cross_border_m3"] for item in by_province.values()
    )
    metric["cross_border_share"] = (
        metric["cross_border_m3"] / metric["assigned_m3"]
    )
    metric["published_m3"] = math.fsum(
        item["assigned_m3"]
        for item in by_facility.values()
        if item["capacity_status"] == _base.PUBLISHED
    )
    metric["proxy_m3"] = metric["assigned_m3"] - metric["published_m3"]
    hhi, top1, top5 = _concentration(
        [item["assigned_m3"] for item in by_facility.values()]
    )
    metric["hhi"] = hhi
    metric["max_facility_share"] = top1
    metric["top5_share"] = top5
    metric["province"] = {
        province: {
            **values,
            "mean_road_distance_km": (
                values["volume_distance_m3_km"] / values["assigned_m3"]
            ),
            "transport_cost_cad_m3": (
                values["transport_cost_cad"] / values["assigned_m3"]
            ),
            "transport_cost_cad_gj": (
                values["transport_cost_cad"] / values["transport_energy_gj"]
            ),
            "cross_border_share": (
                values["cross_border_m3"] / values["assigned_m3"]
            ),
        }
        for province, values in by_province.items()
    }
    return metric, list(by_facility.values())


def _v5_transport_from_rows(
    rows: list[dict[str, str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    metric: dict[str, Any] = {
        "assigned_m3": 0.0,
        "transport_cost_cad": 0.0,
        "transport_cost_cad_low": 0.0,
        "transport_cost_cad_high": 0.0,
        "transport_energy_gj": 0.0,
        "volume_distance_m3_km": 0.0,
    }
    distances: list[tuple[float, float]] = []
    by_province: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "assigned_m3": 0.0,
            "transport_cost_cad": 0.0,
            "transport_energy_gj": 0.0,
            "volume_distance_m3_km": 0.0,
            "cross_border_m3": 0.0,
        }
    )
    by_facility: dict[str, dict[str, Any]] = {}
    for row in rows:
        assigned = _base._finite_nonnegative(
            row["assigned_m3"], "assignment v10 assigned_m3"
        )
        distance = _base._finite_nonnegative(
            row["road_distance_km"], "assignment v10 road_distance_km"
        )
        total_cost = _base._finite_nonnegative(
            row["transport_cost_cad"], "assignment v10 transport_cost_cad"
        )
        energy = _base._finite_nonnegative(
            row["transport_energy_gj"], "assignment v10 transport_energy_gj"
        )
        metric["assigned_m3"] += assigned
        metric["transport_cost_cad"] += total_cost
        metric["transport_cost_cad_low"] += _base._finite_nonnegative(
            row["transport_cost_cad_low"], "transport_cost_cad_low"
        )
        metric["transport_cost_cad_high"] += _base._finite_nonnegative(
            row["transport_cost_cad_high"], "transport_cost_cad_high"
        )
        metric["transport_energy_gj"] += energy
        metric["volume_distance_m3_km"] += assigned * distance
        distances.append((distance, assigned))
        origin = row["node_province"].strip().upper()
        province = by_province[origin]
        province["assigned_m3"] += assigned
        province["transport_cost_cad"] += total_cost
        province["transport_energy_gj"] += energy
        province["volume_distance_m3_km"] += assigned * distance
        cross = row["cross_border"].strip().lower() == "true"
        if cross:
            province["cross_border_m3"] += assigned
        facility_id = row["facility_id"].strip()
        item = by_facility.get(facility_id)
        if item is None:
            item = {
                "origin_province": origin,
                "facility_id": facility_id,
                "facility_name": row["facility_name"].strip(),
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["facility_province"].strip().upper(),
                "assigned_m3": 0.0,
                "transport_cost_cad": 0.0,
                "transport_cost_cad_low": 0.0,
                "transport_cost_cad_high": 0.0,
                "transport_energy_gj": 0.0,
                "volume_distance_m3_km": 0.0,
                "cross_border": cross,
                "capacity_status": row["capacity_status"].strip().lower(),
                "capacity_m3_y": _base._finite_nonnegative(
                    row["capacity_m3_y"], "assignment v10 capacity"
                ),
            }
            by_facility[facility_id] = item
        item["assigned_m3"] += assigned
        item["transport_cost_cad"] += total_cost
        item["transport_cost_cad_low"] += _base._finite_nonnegative(
            row["transport_cost_cad_low"], "transport_cost_cad_low"
        )
        item["transport_cost_cad_high"] += _base._finite_nonnegative(
            row["transport_cost_cad_high"], "transport_cost_cad_high"
        )
        item["transport_energy_gj"] += energy
        item["volume_distance_m3_km"] += assigned * distance
    for item in by_facility.values():
        item["road_distance_km"] = (
            item["volume_distance_m3_km"] / item["assigned_m3"]
        )
        item["facility_utilization"] = (
            item["assigned_m3"] / item["capacity_m3_y"]
        )
    metric["facilities_used"] = len(by_facility)
    metric["mean_road_distance_km"] = (
        metric["volume_distance_m3_km"] / metric["assigned_m3"]
    )
    metric["transport_cost_cad_m3"] = (
        metric["transport_cost_cad"] / metric["assigned_m3"]
    )
    metric["transport_cost_cad_gj"] = (
        metric["transport_cost_cad"] / metric["transport_energy_gj"]
    )
    metric["weighted_median_road_distance_km"] = _weighted_quantile(
        distances, 0.50
    )
    metric["p90_road_distance_km"] = _weighted_quantile(distances, 0.90)
    bands = _band_shares(distances)
    metric["share_ge_150_km"] = (
        bands["gt_150_le_300_km"] + bands["gt_300_km"]
    )
    metric["share_ge_300_km"] = bands["gt_300_km"]
    metric["distance_bands"] = bands
    metric["cross_border_m3"] = math.fsum(
        item["cross_border_m3"] for item in by_province.values()
    )
    metric["cross_border_share"] = (
        metric["cross_border_m3"] / metric["assigned_m3"]
    )
    metric["published_m3"] = math.fsum(
        item["assigned_m3"]
        for item in by_facility.values()
        if item["capacity_status"] == _base.PUBLISHED
    )
    metric["proxy_m3"] = metric["assigned_m3"] - metric["published_m3"]
    hhi, top1, top5 = _concentration(
        [item["assigned_m3"] for item in by_facility.values()]
    )
    metric["hhi"] = hhi
    metric["max_facility_share"] = top1
    metric["top5_share"] = top5
    metric["province"] = {
        province: {
            **values,
            "mean_road_distance_km": (
                values["volume_distance_m3_km"] / values["assigned_m3"]
            ),
            "transport_cost_cad_m3": (
                values["transport_cost_cad"] / values["assigned_m3"]
            ),
            "transport_cost_cad_gj": (
                values["transport_cost_cad"] / values["transport_energy_gj"]
            ),
            "cross_border_share": (
                values["cross_border_m3"] / values["assigned_m3"]
            ),
        }
        for province, values in by_province.items()
    }
    return metric, list(by_facility.values())


def _v4_facility_residue() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    rows = _read_rows(
        V4_FACILITY_CSV,
        (
            "province",
            "origin_province",
            "facility_id",
            "facility_class",
            "residue_odt",
            "residue_pj",
        ),
        "facility residue v7 rows",
    )
    summary = json.loads(V4_FACILITY_SUMMARY.read_text(encoding="utf-8"))
    if summary.get("artifact") != "facility_residue_supply_v7":
        raise _base.FacilityResidueSupplyError(
            f"unexpected v4 facility artifact: {summary.get('artifact')!r}"
        )
    by_facility: dict[str, dict[str, Any]] = {}
    for row in rows:
        facility_id = row["facility_id"].strip()
        item = by_facility.get(facility_id)
        if item is None:
            item = {
                "facility_id": facility_id,
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["province"].strip().upper(),
                "process_residue_odt": 0.0,
                "process_residue_pj": 0.0,
            }
            by_facility[facility_id] = item
        item["process_residue_odt"] += _base._finite_nonnegative(
            row["residue_odt"], "v4 residue_odt"
        )
        item["process_residue_pj"] += _base._finite_nonnegative(
            row["residue_pj"], "v4 residue_pj"
        )
    return summary, by_facility


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _fmt(value: float, digits: int = 9) -> str:
    if not math.isfinite(value):
        raise _base.FacilityResidueSupplyError(
            f"refusing to write non-finite value {value!r}"
        )
    return f"{value:.{digits}f}"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _postprocess(
    summary: dict[str, Any],
    output: Path,
    v5_rows: list[dict[str, str]],
) -> None:
    v4, v4_facilities = _v4_facility_residue()
    v4_transport, v4_transport_facilities = _v4_transport_rows()
    v5_transport, v5_transport_facilities = _v5_transport_from_rows(v5_rows)
    v5_residue: dict[str, dict[str, Any]] = {}
    for row in _read_rows(
        output / "facility_residue_supply.csv",
        ("facility_id", "facility_class", "province", "residue_odt", "residue_pj"),
        "facility residue v8 rows",
    ):
        facility_id = row["facility_id"].strip()
        item = v5_residue.get(facility_id)
        if item is None:
            item = {
                "facility_id": facility_id,
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["province"].strip().upper(),
                "process_residue_odt": 0.0,
                "process_residue_pj": 0.0,
            }
            v5_residue[facility_id] = item
        item["process_residue_odt"] += _base._finite_nonnegative(
            row["residue_odt"], "v5 residue_odt"
        )
        item["process_residue_pj"] += _base._finite_nonnegative(
            row["residue_pj"], "v5 residue_pj"
        )

    facility_transport_rows: list[dict[str, Any]] = []
    for facility_item in sorted(
        v5_transport_facilities,
        key=lambda item: (item["facility_province"], item["facility_id"]),
    ):
        facility_transport_rows.append(
            {
                "record_type": "facility_total",
                "facility_id": facility_item["facility_id"],
                "facility_name": facility_item["facility_name"],
                "facility_class": facility_item["facility_class"],
                "facility_province": facility_item["facility_province"],
                "origin_province": "",
                "assigned_m3": _fmt(float(facility_item["assigned_m3"])),
                "facility_assigned_m3": _fmt(
                    float(facility_item["assigned_m3"])
                ),
                "capacity_m3_y": _fmt(float(facility_item["capacity_m3_y"])),
                "facility_utilization": _fmt(
                    float(facility_item["facility_utilization"])
                ),
                "cross_border": _bool_text(bool(facility_item["cross_border"])),
                "road_distance_km_weighted": _fmt(
                    float(facility_item["road_distance_km"])
                ),
                "transport_cost_cad": _fmt(
                    float(facility_item["transport_cost_cad"])
                ),
                "transport_cost_cad_low": _fmt(
                    float(facility_item["transport_cost_cad_low"])
                ),
                "transport_cost_cad_high": _fmt(
                    float(facility_item["transport_cost_cad_high"])
                ),
                "transport_energy_gj": _fmt(
                    float(facility_item["transport_energy_gj"])
                ),
                "transport_cost_cad_m3": _fmt(
                    float(facility_item["transport_cost_cad"])
                    / float(facility_item["assigned_m3"])
                ),
                "transport_cost_cad_gj": _fmt(
                    float(facility_item["transport_cost_cad"])
                    / float(facility_item["transport_energy_gj"])
                ),
                "distance_band": _distance_band(
                    float(facility_item["road_distance_km"])
                ),
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    # Origin/facility rows are reconstructed from the node rows.
    origin_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in v5_rows:
        key = (row["node_province"].strip().upper(), row["facility_id"].strip())
        item = origin_groups.get(key)
        if item is None:
            item = {
                "facility_id": key[1],
                "facility_name": row["facility_name"].strip(),
                "facility_class": row["facility_class"].strip().upper(),
                "facility_province": row["facility_province"].strip().upper(),
                "origin_province": key[0],
                "assigned_m3": 0.0,
                "capacity_m3_y": _base._finite_nonnegative(
                    row["capacity_m3_y"], "v10 capacity"
                ),
                "transport_cost_cad": 0.0,
                "transport_cost_cad_low": 0.0,
                "transport_cost_cad_high": 0.0,
                "transport_energy_gj": 0.0,
                "volume_distance": 0.0,
                "cross_border": row["cross_border"].strip().lower() == "true",
            }
            origin_groups[key] = item
        assigned = _base._finite_nonnegative(row["assigned_m3"], "v10 assigned")
        distance = _base._finite_nonnegative(
            row["road_distance_km"], "v10 road distance"
        )
        item["assigned_m3"] += assigned
        item["transport_cost_cad"] += _base._finite_nonnegative(
            row["transport_cost_cad"], "v10 transport cost"
        )
        item["transport_cost_cad_low"] += _base._finite_nonnegative(
            row["transport_cost_cad_low"], "v10 transport cost low"
        )
        item["transport_cost_cad_high"] += _base._finite_nonnegative(
            row["transport_cost_cad_high"], "v10 transport cost high"
        )
        item["transport_energy_gj"] += _base._finite_nonnegative(
            row["transport_energy_gj"], "v10 transport energy"
        )
        item["volume_distance"] += assigned * distance
    for item in sorted(
        origin_groups.values(),
        key=lambda value: (
            value["facility_province"],
            value["facility_id"],
            value["origin_province"],
        ),
    ):
        road_distance = item["volume_distance"] / item["assigned_m3"]
        facility_transport_rows.append(
            {
                "record_type": "origin_facility",
                "facility_id": item["facility_id"],
                "facility_name": item["facility_name"],
                "facility_class": item["facility_class"],
                "facility_province": item["facility_province"],
                "origin_province": item["origin_province"],
                "assigned_m3": _fmt(item["assigned_m3"]),
                "facility_assigned_m3": "",
                "capacity_m3_y": _fmt(item["capacity_m3_y"]),
                "facility_utilization": "",
                "cross_border": _bool_text(item["cross_border"]),
                "road_distance_km_weighted": _fmt(road_distance),
                "transport_cost_cad": _fmt(item["transport_cost_cad"]),
                "transport_cost_cad_low": _fmt(
                    item["transport_cost_cad_low"]
                ),
                "transport_cost_cad_high": _fmt(
                    item["transport_cost_cad_high"]
                ),
                "transport_energy_gj": _fmt(item["transport_energy_gj"]),
                "transport_cost_cad_m3": _fmt(
                    item["transport_cost_cad"] / item["assigned_m3"]
                ),
                "transport_cost_cad_gj": _fmt(
                    item["transport_cost_cad"] / item["transport_energy_gj"]
                ),
                "distance_band": _distance_band(road_distance),
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    _write_csv(
        output / "facility_transport.csv",
        [
            "record_type",
            "facility_id",
            "facility_name",
            "facility_class",
            "facility_province",
            "origin_province",
            "assigned_m3",
            "facility_assigned_m3",
            "capacity_m3_y",
            "facility_utilization",
            "cross_border",
            "road_distance_km_weighted",
            "transport_cost_cad",
            "transport_cost_cad_low",
            "transport_cost_cad_high",
            "transport_energy_gj",
            "transport_cost_cad_m3",
            "transport_cost_cad_gj",
            "distance_band",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        facility_transport_rows,
    )

    transport_rows: list[dict[str, Any]] = []
    metrics = (
        ("assigned_m3", "m3/yr", "assigned volume"),
        ("transport_cost_cad", "CAD/yr", "model transport cost"),
        ("transport_cost_cad_m3", "CAD/m3", "transport cost per m3"),
        ("transport_cost_cad_gj", "CAD/GJ", "transport cost per GJ"),
        ("mean_road_distance_km", "km", "assigned-volume-weighted mean"),
        (
            "weighted_median_road_distance_km",
            "km",
            "assigned-volume-weighted median",
        ),
        ("p90_road_distance_km", "km", "assigned-volume-weighted p90"),
        ("share_ge_150_km", "share", "distance at or above 150 km"),
        ("share_ge_300_km", "share", "distance at or above 300 km"),
        ("facilities_used", "count", "distinct facilities"),
        ("cross_border_m3", "m3/yr", "cross-border volume"),
        ("cross_border_share", "share", "cross-border share"),
        ("published_m3", "m3/yr", "published-capacity volume"),
        ("proxy_m3", "m3/yr", "proxy-capacity volume"),
        ("hhi", "index_0_1", "facility concentration"),
        ("top5_share", "share", "top five facility share"),
    )
    for scenario, label, values in (
        ("v4_centroid_local_first", "v4 assignment v6 centroid local-first", v4_transport),
        ("v5_node_transport_aware", "v5 assignment v10 node transport-aware", v5_transport),
    ):
        for metric, unit, note in metrics:
            transport_rows.append(
                {
                    "scenario": scenario,
                    "scenario_label": label,
                    "scope": "national",
                    "scope_id": "CA",
                    "metric": metric,
                    "unit": unit,
                    "value": _fmt(float(values[metric])),
                    "note": note,
                    "evidence_label": _base.EVIDENCE_LABEL,
                    "observed": _base.LABEL_FALSE,
                    "policy_grade": _base.LABEL_FALSE,
                }
            )
        for province, payload in sorted(values["province"].items()):
            for metric, unit, note in (
                ("assigned_m3", "m3/yr", "origin-province assigned volume"),
                ("transport_cost_cad", "CAD/yr", "origin-province transport cost"),
                ("transport_cost_cad_m3", "CAD/m3", "transport cost per m3"),
                ("transport_cost_cad_gj", "CAD/GJ", "transport cost per GJ"),
                ("mean_road_distance_km", "km", "assigned-volume-weighted mean"),
                ("cross_border_m3", "m3/yr", "cross-border volume"),
                ("cross_border_share", "share", "cross-border share"),
            ):
                transport_rows.append(
                    {
                        "scenario": scenario,
                        "scenario_label": label,
                        "scope": "province",
                        "scope_id": province,
                        "metric": metric,
                        "unit": unit,
                        "value": _fmt(float(payload[metric])),
                        "note": note,
                        "evidence_label": _base.EVIDENCE_LABEL,
                        "observed": _base.LABEL_FALSE,
                        "policy_grade": _base.LABEL_FALSE,
                    }
                )
        for band, share in values["distance_bands"].items():
            band_assigned = share * values["assigned_m3"]
            transport_rows.append(
                {
                    "scenario": scenario,
                    "scenario_label": label,
                    "scope": "distance_band",
                    "scope_id": band,
                    "metric": "assigned_m3",
                    "unit": "m3/yr",
                    "value": _fmt(band_assigned),
                    "note": "volume in distance band",
                    "evidence_label": _base.EVIDENCE_LABEL,
                    "observed": _base.LABEL_FALSE,
                    "policy_grade": _base.LABEL_FALSE,
                }
            )
            transport_rows.append(
                {
                    "scenario": scenario,
                    "scenario_label": label,
                    "scope": "distance_band",
                    "scope_id": band,
                    "metric": "share",
                    "unit": "share",
                    "value": _fmt(share),
                    "note": "share of assigned volume in distance band",
                    "evidence_label": _base.EVIDENCE_LABEL,
                    "observed": _base.LABEL_FALSE,
                    "policy_grade": _base.LABEL_FALSE,
                }
            )
    _write_csv(
        output / "transport_cost_summary.csv",
        [
            "scenario",
            "scenario_label",
            "scope",
            "scope_id",
            "metric",
            "unit",
            "value",
            "note",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        transport_rows,
    )

    comparison_metrics = (
        ("assigned_m3", "m3/yr"),
        ("transport_cost_cad", "CAD/yr"),
        ("transport_cost_cad_m3", "CAD/m3"),
        ("transport_cost_cad_gj", "CAD/GJ"),
        ("mean_road_distance_km", "km"),
        ("weighted_median_road_distance_km", "km"),
        ("p90_road_distance_km", "km"),
        ("share_ge_150_km", "share"),
        ("share_ge_300_km", "share"),
        ("facilities_used", "count"),
        ("cross_border_m3", "m3/yr"),
        ("cross_border_share", "share"),
        ("published_m3", "m3/yr"),
        ("proxy_m3", "m3/yr"),
        ("hhi", "index_0_1"),
        ("top5_share", "share"),
    )
    comparison_rows = []
    for metric, unit in comparison_metrics:
        before = float(v4_transport[metric])
        after = float(v5_transport[metric])
        comparison_rows.append(
            {
                "metric": metric,
                "unit": unit,
                "v4_value": _fmt(before),
                "v5_value": _fmt(after),
                "delta": _fmt(after - before),
                "delta_pct": _fmt(
                    (after - before) / before * 100.0 if before else 0.0
                ),
                "note": "v5 node transport-aware minus v4 centroid local-first",
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    _write_csv(
        output / "v4_v5_transport_comparison.csv",
        [
            "metric",
            "unit",
            "v4_value",
            "v5_value",
            "delta",
            "delta_pct",
            "note",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        comparison_rows,
    )

    province_rows = []
    for province in _base.PROVINCES:
        before = v4_transport["province"].get(
            province,
            {
                "assigned_m3": 0.0,
                "transport_cost_cad": 0.0,
                "mean_road_distance_km": 0.0,
                "cross_border_m3": 0.0,
            },
        )
        after = v5_transport["province"].get(
            province,
            {
                "assigned_m3": 0.0,
                "transport_cost_cad": 0.0,
                "mean_road_distance_km": 0.0,
                "cross_border_m3": 0.0,
            },
        )
        province_rows.append(
            {
                "province": province,
                "v4_assigned_m3": _fmt(before["assigned_m3"]),
                "v5_assigned_m3": _fmt(after["assigned_m3"]),
                "delta_assigned_m3": _fmt(
                    after["assigned_m3"] - before["assigned_m3"]
                ),
                "v4_transport_cost_cad": _fmt(before["transport_cost_cad"]),
                "v5_transport_cost_cad": _fmt(after["transport_cost_cad"]),
                "delta_transport_cost_cad": _fmt(
                    after["transport_cost_cad"] - before["transport_cost_cad"]
                ),
                "v4_mean_road_distance_km": _fmt(
                    before["mean_road_distance_km"]
                ),
                "v5_mean_road_distance_km": _fmt(
                    after["mean_road_distance_km"]
                ),
                "delta_mean_road_distance_km": _fmt(
                    after["mean_road_distance_km"]
                    - before["mean_road_distance_km"]
                ),
                "v4_cross_border_m3": _fmt(before["cross_border_m3"]),
                "v5_cross_border_m3": _fmt(after["cross_border_m3"]),
                "delta_cross_border_m3": _fmt(
                    after["cross_border_m3"] - before["cross_border_m3"]
                ),
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    _write_csv(
        output / "v4_v5_province_comparison.csv",
        [
            "province",
            "v4_assigned_m3",
            "v5_assigned_m3",
            "delta_assigned_m3",
            "v4_transport_cost_cad",
            "v5_transport_cost_cad",
            "delta_transport_cost_cad",
            "v4_mean_road_distance_km",
            "v5_mean_road_distance_km",
            "delta_mean_road_distance_km",
            "v4_cross_border_m3",
            "v5_cross_border_m3",
            "delta_cross_border_m3",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        province_rows,
    )

    v4_by_facility = {item["facility_id"]: item for item in v4_transport_facilities}
    v5_by_facility = {item["facility_id"]: item for item in v5_transport_facilities}
    facility_rows_out = []
    for facility_id in sorted(set(v4_by_facility) | set(v5_by_facility) | set(v4_facilities) | set(v5_residue)):
        v4_facility = v4_by_facility.get(facility_id, {})
        v5_facility = v5_by_facility.get(facility_id, {})
        v4_residue = v4_facilities.get(facility_id, {})
        v5_residue_item = v5_residue.get(facility_id, {})
        meta = v5_facility or v4_facility or v5_residue_item or v4_residue
        facility_rows_out.append(
            {
                "facility_id": facility_id,
                "facility_name": meta.get("facility_name", ""),
                "facility_class": meta.get("facility_class", ""),
                "facility_province": meta.get("facility_province", ""),
                "v4_assigned_m3": _fmt(float(v4_facility.get("assigned_m3", 0.0))),
                "v5_assigned_m3": _fmt(float(v5_facility.get("assigned_m3", 0.0))),
                "delta_assigned_m3": _fmt(
                    float(v5_facility.get("assigned_m3", 0.0))
                    - float(v4_facility.get("assigned_m3", 0.0))
                ),
                "v4_process_residue_odt": _fmt(
                    float(v4_residue.get("process_residue_odt", 0.0))
                ),
                "v5_process_residue_odt": _fmt(
                    float(v5_residue_item.get("process_residue_odt", 0.0))
                ),
                "delta_process_residue_odt": _fmt(
                    float(v5_residue_item.get("process_residue_odt", 0.0))
                    - float(v4_residue.get("process_residue_odt", 0.0))
                ),
                "v4_transport_cost_cad": _fmt(
                    float(v4_facility.get("transport_cost_cad", 0.0))
                ),
                "v5_transport_cost_cad": _fmt(
                    float(v5_facility.get("transport_cost_cad", 0.0))
                ),
                "delta_transport_cost_cad": _fmt(
                    float(v5_facility.get("transport_cost_cad", 0.0))
                    - float(v4_facility.get("transport_cost_cad", 0.0))
                ),
                "v4_road_distance_km": _fmt(
                    float(v4_facility.get("road_distance_km", 0.0))
                ),
                "v5_road_distance_km": _fmt(
                    float(v5_facility.get("road_distance_km", 0.0))
                ),
                "delta_road_distance_km": _fmt(
                    float(v5_facility.get("road_distance_km", 0.0))
                    - float(v4_facility.get("road_distance_km", 0.0))
                ),
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    _write_csv(
        output / "v4_v5_facility_distribution.csv",
        [
            "facility_id",
            "facility_name",
            "facility_class",
            "facility_province",
            "v4_assigned_m3",
            "v5_assigned_m3",
            "delta_assigned_m3",
            "v4_process_residue_odt",
            "v5_process_residue_odt",
            "delta_process_residue_odt",
            "v4_transport_cost_cad",
            "v5_transport_cost_cad",
            "delta_transport_cost_cad",
            "v4_road_distance_km",
            "v5_road_distance_km",
            "delta_road_distance_km",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        facility_rows_out,
    )

    v4_totals = v4["national_totals"]
    v5_totals = summary["national_totals"]
    for metric_key in (
        "process_residue_odt",
        "process_residue_pj",
        "harvest_residue_gross_odt",
        "harvest_residue_gross_pj",
        "harvest_residue_recoverable_odt",
        "harvest_residue_recoverable_pj",
        "total_feedstock_m3",
    ):
        _base._require_close(
            float(v5_totals[metric_key]),
            float(v4_totals[metric_key]),
            f"v4/v5 invariant {metric_key}",
        )
    facility_comparison = [
        {
            "metric": "facilities_with_positive_residue",
            "unit": "count",
            "v4_value": _fmt(float(v4["concentration"]["facilities_with_positive_residue"])),
            "v5_value": _fmt(float(summary["concentration"]["facilities_with_positive_residue"])),
        },
        {
            "metric": "process_residue_odt",
            "unit": "odt/yr",
            "v4_value": _fmt(float(v4_totals["process_residue_odt"])),
            "v5_value": _fmt(float(v5_totals["process_residue_odt"])),
        },
        {
            "metric": "process_residue_pj",
            "unit": "PJ/yr",
            "v4_value": _fmt(float(v4_totals["process_residue_pj"])),
            "v5_value": _fmt(float(v5_totals["process_residue_pj"])),
        },
        {
            "metric": "cross_border_residue_odt",
            "unit": "odt/yr",
            "v4_value": _fmt(float(v4_totals["cross_border_residue_odt"])),
            "v5_value": _fmt(float(v5_totals["cross_border_residue_odt"])),
        },
        {
            "metric": "cross_border_residue_share",
            "unit": "share",
            "v4_value": _fmt(float(v4_totals["cross_border_residue_share"])),
            "v5_value": _fmt(float(v5_totals["cross_border_residue_share"])),
        },
        {
            "metric": "hhi_process_residue",
            "unit": "index_0_1",
            "v4_value": _fmt(float(v4_totals["hhi_process_residue"])),
            "v5_value": _fmt(float(v5_totals["hhi_process_residue"])),
        },
        {
            "metric": "top1_process_residue_share",
            "unit": "share",
            "v4_value": _fmt(float(v4_totals["top1_process_residue_share"])),
            "v5_value": _fmt(float(v5_totals["top1_process_residue_share"])),
        },
        {
            "metric": "top5_process_residue_share",
            "unit": "share",
            "v4_value": _fmt(float(v4_totals["top5_process_residue_share"])),
            "v5_value": _fmt(float(v5_totals["top5_process_residue_share"])),
        },
        {
            "metric": "proxy_residue_share",
            "unit": "share",
            "v4_value": _fmt(float(v4_totals["proxy_residue_share"])),
            "v5_value": _fmt(float(v5_totals["proxy_residue_share"])),
        },
        {
            "metric": "published_residue_share",
            "unit": "share",
            "v4_value": _fmt(float(v4_totals["published_residue_share"])),
            "v5_value": _fmt(float(v5_totals["published_residue_share"])),
        },
    ]
    facility_comparison_rows = []
    for row in facility_comparison:
        before = float(row["v4_value"])
        after = float(row["v5_value"])
        facility_comparison_rows.append(
            {
                **row,
                "delta": _fmt(after - before),
                "delta_pct": _fmt(
                    (after - before) / before * 100.0 if before else 0.0
                ),
                "note": "v5 node transport-aware minus v4 centroid local-first",
                "evidence_label": _base.EVIDENCE_LABEL,
                "observed": _base.LABEL_FALSE,
                "policy_grade": _base.LABEL_FALSE,
            }
        )
    _write_csv(
        output / "v4_v5_facility_comparison.csv",
        [
            "metric",
            "unit",
            "v4_value",
            "v5_value",
            "delta",
            "delta_pct",
            "note",
            "evidence_label",
            "observed",
            "policy_grade",
        ],
        facility_comparison_rows,
    )

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "Facility-level process residue on assignment v10 "
        "node_transport_aware, plus the v4 centroid/local-first versus v5 "
        "node transport-aware transport and facility-distribution comparison."
    )
    summary["method"]["allocation"] = (
        "per origin province and class, proportional to assignment v10 "
        "node_transport_aware assigned volume among same-class facilities; "
        "node-to-facility flows aggregate to origin/facility links"
    )
    summary["method"]["local_first"] = False
    summary["method"]["transport_aware"] = True
    summary["method"]["assignment"] = "assignment v10 node_transport_aware"
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
    }
    summary["transport"] = {
        "v4": v4_transport,
        "v5": v5_transport,
        "comparison": {
            row["metric"]: {
                "unit": row["unit"],
                "v4_value": float(row["v4_value"]),
                "v5_value": float(row["v5_value"]),
                "delta": float(row["delta"]),
                "delta_pct": float(row["delta_pct"]),
            }
            for row in comparison_rows
        },
        "note": (
            "Transport is a separate truck-cost summary. It is not added to "
            "OSeCAN supply-curve tiers and is not an OSeCAN official input."
        ),
    }
    summary["v4_v5_comparison"] = {
        "v4_assignment": "assignment v6 province_capacity_floor local-first",
        "v5_assignment": "assignment v10 node_transport_aware",
        "transport_csv": "v4_v5_transport_comparison.csv",
        "province_csv": "v4_v5_province_comparison.csv",
        "facility_distribution_csv": "v4_v5_facility_distribution.csv",
        "facility_comparison_csv": "v4_v5_facility_comparison.csv",
        "transport_summary_csv": "transport_cost_summary.csv",
        "national_products_residue_invariant": True,
    }
    summary["caveats"] = list(summary.get("caveats", [])) + [
        (
            "Assignment v10 is a node-to-facility transport-aware LP at the "
            "119 Mm3 reference; it is not a market equilibrium or a route "
            "model for every node-facility pair."
        ),
        (
            "Transport cost is trucking only (52.5 m3 base payload, empty "
            "return, 2026 CAD) and is not part of the official OSeCAN "
            "supply-curve tiers."
        ),
    ]
    summary["inputs"].update(
        {
            str(V10_ASSIGNMENT_CSV.relative_to(REPO_ROOT)): _base._sha256(
                V10_ASSIGNMENT_CSV
            ),
            str(V10_FACILITY_CSV.relative_to(REPO_ROOT)): _base._sha256(
                V10_FACILITY_CSV
            ),
            str(V10_SUMMARY.relative_to(REPO_ROOT)): _base._sha256(V10_SUMMARY),
            str(V4_FACILITY_CSV.relative_to(REPO_ROOT)): _base._sha256(
                V4_FACILITY_CSV
            ),
            str(V4_FACILITY_SUMMARY.relative_to(REPO_ROOT)): _base._sha256(
                V4_FACILITY_SUMMARY
            ),
            str(V4_ASSIGNMENT_CSV.relative_to(REPO_ROOT)): _base._sha256(
                V4_ASSIGNMENT_CSV
            ),
        }
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["transport"] = summary["transport"]
    manifest["v4_v5_comparison"] = summary["v4_v5_comparison"]
    manifest["inputs"] = summary["inputs"]
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_path = output / REPORT_NAME
    if report_path.is_file():
        text = report_path.read_text(encoding="utf-8")
        text = text.replace("Version 7", "Version 8").replace(
            "assignment v6", "assignment v10 node transport-aware"
        ).replace(
            "assignment-v6", "assignment-v10 node transport-aware"
        )
        text += (
            "\n## v4 centroid/local-first versus v5 node transport-aware\n\n"
            "| Metric | Unit | v4 | v5 | Delta | Delta % |\n"
            "|---|---|---:|---:|---:|---:|\n"
            + "\n".join(
                f"| {row['metric']} | {row['unit']} | {row['v4_value']} | "
                f"{row['v5_value']} | {row['delta']} | {row['delta_pct']} |"
                for row in comparison_rows
            )
            + "\n\n"
            "Transport cost is separate from the OSeCAN official tiers. "
            "National product and residue totals are invariant.\n"
        )
        report_path.write_text(text, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"] = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(output.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build facility residue v8 on assignment v10 and add transport comparison."""

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    saved_v7 = {name: getattr(_v7, name) for name in _SAVED_V7}
    saved_base = {name: getattr(_base, name) for name in _SAVED_BASE}
    saved_overrides = dict(_v4.OVERRIDES)
    original_parser = _base._parse_assignment
    try:
        for name in _SAVED_V7:
            setattr(_v7, name, globals()[name])
        _base.ASSIGNMENT_DIR = ASSIGNMENT_DIR
        _base.ASSIGNMENT_VERSION = ASSIGNMENT_VERSION
        _base.ASSIGNMENT_LABEL = ASSIGNMENT_LABEL
        _base.ASSIGNMENT_ARTIFACT = ASSIGNMENT_ARTIFACT
        _base.ASSIGNMENT_SUMMARY_KEY = ASSIGNMENT_SUMMARY_KEY
        _base.ASSIGNMENT_CSV_NAME = V10_ASSIGNMENT_CSV.name
        _base.ASSIGNMENT_UTILIZATION_CSV_NAME = V10_FACILITY_CSV.name
        _base.ASSIGNMENT_SUMMARY_NAME = V10_SUMMARY.name
        _base.ALLOCATION_BASIS = ALLOCATION_BASIS
        _base.UTILIZATION_FIELD = UTILIZATION_FIELD
        _base.UTILIZATION_MODE = "row"
        _base.SCENARIO = SCENARIO
        _v4.OVERRIDES.update(
            {
                "ASSIGNMENT_DIR": ASSIGNMENT_DIR,
                "ASSIGNMENT_VERSION": ASSIGNMENT_VERSION,
                "ASSIGNMENT_LABEL": ASSIGNMENT_LABEL,
                "ASSIGNMENT_ARTIFACT": ASSIGNMENT_ARTIFACT,
                "ASSIGNMENT_SUMMARY_KEY": ASSIGNMENT_SUMMARY_KEY,
                "ALLOCATION_BASIS": ALLOCATION_BASIS,
                "UTILIZATION_FIELD": UTILIZATION_FIELD,
                "UTILIZATION_MODE": "row",
                "SCENARIO": SCENARIO,
            }
        )
        _base._parse_assignment = _parse_v10_assignment
        summary = _v7.build(output_dir=target)
    finally:
        _base._parse_assignment = original_parser
        for name, value in saved_v7.items():
            setattr(_v7, name, value)
        for name, value in saved_base.items():
            setattr(_base, name, value)
        _v4.OVERRIDES.clear()
        _v4.OVERRIDES.update(saved_overrides)

    rows = _read_rows(
        V10_ASSIGNMENT_CSV,
        ("scenario", "assigned_m3", "road_distance_km", "transport_cost_cad"),
        "assignment v10 node rows",
    )
    _postprocess(summary, target, rows)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "scenario": summary["scenario"],
                "process_residue_odt": summary["national_totals"][
                    "process_residue_odt"
                ],
                "process_residue_pj": summary["national_totals"][
                    "process_residue_pj"
                ],
                "transport_cost_cad": summary["transport"]["v5"][
                    "transport_cost_cad"
                ],
                "validation_failures": summary["validation"][
                    "failed_check_count"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
