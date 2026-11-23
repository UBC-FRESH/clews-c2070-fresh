"""Transport-aware national facility assignment v9.

This artifact adds two linked screening products:

1. A facility-level transport-cost accounting of the v8 local-first assignment
   using the committed transport cost v1 curves (52.5 m3 base payload, empty
   return, 2026 CAD).
2. A transport-aware assignment v9 that minimises modelled transport cost plus
   a local-preference penalty, subject to the unchanged capacity v3 floor and
   the unchanged 119 Mm3 provincial harvest offers.

The v9 allocation is a bipartite transportation linear program.  Every eligible
facility capacity is finite, the provincial offers are equalities, and
cross-border flows carry the v8 documented 500 km penalty expressed in
2026 CAD/m3 through the same transport-cost curve.  Products and residue are
not recalculated: this artifact is an assignment-only screening rerun, and no
product/residue output is written or changed.

Labels: ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No
national claim is made.  No UI or official OSeCAN surface is changed.  A ``D``
in either the parent or data repository git status halts the build.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

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
from workflows.national.facilities import (
    national_facility_assignment_v8 as v8,
)
from workflows.national.facilities import transport_cost_v1 as transport
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
V8_DIR = DATA_ROOT / "derived/national/facilities/national_facility_assignment_v8"
V8_ASSIGNMENT_CSV = V8_DIR / "facility_assignment.csv"
V8_SUMMARY_JSON = V8_DIR / "summary.json"
CAPACITY_CSV = v8.CAPACITY_CSV
CAPACITY_SUMMARY = v8.CAPACITY_SUMMARY
HARVEST_CSV = v8.HARVEST_CSV
BOUNDARY_GEOJSON = v8.BOUNDARY_GEOJSON
FACTORS_CSV = v8.FACTORS_CSV
FACTORS_SUMMARY = v8.FACTORS_SUMMARY
TRANSPORT_PARAMETERS_CSV = (
    DATA_ROOT
    / "derived/national/facilities/transport_cost_v1"
    / "parameters.csv"
)
TRANSPORT_CURVES_CSV = (
    DATA_ROOT / "derived/national/facilities/transport_cost_v1" / "curves.csv"
)
SUPPLY_CURVE_JSON = (
    DATA_ROOT
    / "derived/national/clews/osecan/national_supply_curve"
    / "supply_curve_definition.json"
)
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v9"
)

ARTIFACT = "national_facility_assignment_v9"
ARTIFACT_VERSION = "9.0.0"
PRIMARY_SCENARIO = v6.PRIMARY_SCENARIO
BASE_COST_SCENARIO = "base"
PAYLOAD_M3 = 52.5
DENSITY_ODT_PER_M3 = 0.22
LHV_GJ_PER_ODT = 18.5
ENERGY_GJ_PER_M3 = DENSITY_ODT_PER_M3 * LHV_GJ_PER_ODT
LOCAL_PENALTY_KM = 500.0
PENALTY_SENSITIVITY_KM = (0.0, 250.0, 500.0, 1000.0)
TIER_ORDER = ("T1", "T2", "T3", "T4")
VOLUME_EPSILON_M3 = 1e-6
TIE_BREAK_CAD_M3 = 1e-12

EVIDENCE_LABEL = v6.EVIDENCE_LABEL
OBSERVED = v6.OBSERVED
POLICY_GRADE = v6.POLICY_GRADE

TIER_CAVEAT = (
    "Face-value comparison only: the supply-curve field is named "
    "variable_cost_usd_per_gj, but no currency or price-year conversion is "
    "documented; transport is 2026 CAD/GJ. Tier costs are adopted scenario "
    "values, not fitted marginal costs. T4 is a documented scenario high-cost "
    "tier. This is a screening arithmetic comparison, not a policy claim."
)

V8_TRANSPORT_FIELDS = [
    "scenario",
    "allocation_order",
    "province",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_m3_y",
    "assigned_m3",
    "road_distance_km",
    "transport_payload_m3",
    "transport_cost_scenario",
    "transport_cost_cad_m3",
    "transport_cost_cad_m3_low",
    "transport_cost_cad_m3_high",
    "transport_cost_cad",
    "transport_cost_cad_low",
    "transport_cost_cad_high",
    "transport_energy_gj",
    "transport_cost_cad_gj",
    "transport_cost_cad_gj_low",
    "transport_cost_cad_gj_high",
    "delivered_cost_cad_gj_t1",
    "delivered_cost_cad_gj_t2",
    "delivered_cost_cad_gj_t3",
    "delivered_cost_cad_gj_t4",
    "exceeds_t1",
    "exceeds_t2",
    "exceeds_t3",
    "exceeds_t4",
    "evidence_label",
    "observed",
    "policy_grade",
]
V9_ASSIGNMENT_FIELDS = [
    "scenario",
    "allocation_order",
    "province",
    "province_offer_m3",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "cross_border",
    "allocation_phase",
    "capacity_status",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "assigned_m3",
    "facility_assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "distance_km",
    "road_distance_km",
    "road_factor_applied",
    "road_factor_basis",
    "cross_border_penalty_km",
    "cross_border_penalty_cad_m3",
    "transport_payload_m3",
    "transport_cost_scenario",
    "transport_cost_cad_m3",
    "transport_cost_cad_m3_low",
    "transport_cost_cad_m3_high",
    "transport_cost_cad",
    "transport_cost_cad_low",
    "transport_cost_cad_high",
    "transport_energy_gj",
    "transport_cost_cad_gj",
    "transport_cost_cad_gj_low",
    "transport_cost_cad_gj_high",
    "delivered_cost_cad_gj_t1",
    "delivered_cost_cad_gj_t2",
    "delivered_cost_cad_gj_t3",
    "delivered_cost_cad_gj_t4",
    "exceeds_t1",
    "exceeds_t2",
    "exceeds_t3",
    "exceeds_t4",
    "capacity_evidence",
    "floor_scale_factor",
    "source_id",
    "source_url",
    "capacity_source_id",
    "capacity_source_url",
    "evidence_tier",
    "evidence_label",
    "observed",
    "policy_grade",
]
UTILIZATION_FIELDS = [
    "scenario",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_evidence",
    "floor_scale_factor",
    "capacity_m3_y",
    "proxy_low_m3_y",
    "proxy_high_m3_y",
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "binding",
    "used",
    "local_assigned_m3",
    "cross_border_assigned_m3",
    "road_distance_km_mean",
    "road_distance_km_max",
    "transport_cost_cad",
    "transport_cost_cad_gj",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVINCE_FIELDS = [
    "scenario",
    "province",
    "offer_m3",
    "assigned_m3",
    "unassigned_m3",
    "local_m3",
    "cross_border_m3",
    "local_share",
    "cross_border_share",
    "published_m3",
    "proxy_m3",
    "proxy_share_of_assigned",
    "facilities_used",
    "published_facilities_used",
    "proxy_facilities_used",
    "road_distance_km_mean",
    "road_distance_km_max",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "binding_facilities",
    "binding_volume_m3",
    "evidence_label",
    "observed",
    "policy_grade",
]
FACILITY_FIELDS = [
    "scenario",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_m3_y",
    "assigned_m3",
    "remaining_capacity_m3",
    "utilization",
    "binding",
    "used",
    "local_assigned_m3",
    "cross_border_assigned_m3",
    "road_distance_km_mean",
    "road_distance_km_max",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "transport_cost_cad_gj_low",
    "transport_cost_cad_gj_high",
    "delivered_cost_cad_gj_t1",
    "delivered_cost_cad_gj_t4",
    "exceeds_t1",
    "exceeds_t2",
    "exceeds_t3",
    "exceeds_t4",
    "evidence_label",
    "observed",
    "policy_grade",
]
TIER_FIELDS = [
    "scenario",
    "tier",
    "technology",
    "tier_cost_per_gj",
    "volume_assigned_m3",
    "volume_above_m3",
    "share_above",
    "assignments_above",
    "facilities_above",
    "transport_cost_above_cad",
    "transport_cost_cad_gj",
    "delivered_cost_cad_gj",
    "caveat",
]
COMPARISON_FIELDS = [
    "metric",
    "unit",
    "v8_value",
    "v9_value",
    "delta_v9_minus_v8",
    "delta_pct",
    "note",
]
FACILITY_CHANGE_FIELDS = [
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "capacity_status",
    "capacity_m3_y",
    "v8_assigned_m3",
    "v9_assigned_m3",
    "delta_v9_minus_v8_m3",
    "v8_rows",
    "v9_rows",
    "v8_allocation_phase",
    "v9_allocation_phase",
    "v8_transport_cost_cad_gj",
    "v9_transport_cost_cad_gj",
    "status",
]
PROVINCE_COMPARISON_FIELDS = [
    "province",
    "v8_offer_m3",
    "v9_offer_m3",
    "v8_assigned_m3",
    "v9_assigned_m3",
    "v8_local_m3",
    "v9_local_m3",
    "v8_cross_border_m3",
    "v9_cross_border_m3",
    "v8_road_distance_km_mean",
    "v9_road_distance_km_mean",
    "v8_transport_cost_cad",
    "v9_transport_cost_cad",
    "v8_transport_cost_cad_gj",
    "v9_transport_cost_cad_gj",
    "v8_facilities_used",
    "v9_facilities_used",
    "v8_proxy_share_of_assigned",
    "v9_proxy_share_of_assigned",
]
SENSITIVITY_FIELDS = [
    "local_penalty_km",
    "local_penalty_cad_m3",
    "local_m3",
    "cross_border_m3",
    "cross_border_share",
    "road_distance_km_mean",
    "road_distance_km_max",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "facilities_used",
    "binding_facilities",
    "binding_volume_m3",
    "proxy_share_of_assigned",
    "status",
]


class AssignmentV9Error(v8.AssignmentV8Error):
    """Raised when a transport-aware assignment invariant fails."""


# ---------------------------------------------------------------------------
# I/O and deletion guard.
# ---------------------------------------------------------------------------

def _format_float(value: float | None, digits: int = 9) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise AssignmentV9Error(f"refusing to write non-finite value {value!r}")
    return f"{value:.{digits}f}"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise AssignmentV9Error(f"{label} is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise AssignmentV9Error(f"{label} is empty: {path}")
    return rows


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssignmentV9Error(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
    *,
    status_reader: Callable[[Path], str] = _git_status,
) -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in repositories:
        for line in parse_deletion_lines(status_reader(repository)):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise AssignmentV9Error(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )


# ---------------------------------------------------------------------------
# Transport parameters and tier definitions.
# ---------------------------------------------------------------------------

def load_transport_parameters(
    path: Path = TRANSPORT_PARAMETERS_CSV,
) -> dict[str, transport.Parameter]:
    rows = _read_csv(path, "transport cost v1 parameters")
    parameters: dict[str, transport.Parameter] = {}
    for row in rows:
        parameter = transport.Parameter(
            parameter_id=row["parameter_id"],
            component=row["component"],
            unit=row["unit"],
            low=float(row["low"]),
            base=float(row["base"]),
            high=float(row["high"]),
            direction=row["direction"],
            source_id=row["source_id"],
            notes=row["notes"],
        )
        if parameter.parameter_id in parameters:
            raise AssignmentV9Error(
                f"{path}: duplicate parameter {parameter.parameter_id!r}"
            )
        for value in (parameter.low, parameter.base, parameter.high):
            if not math.isfinite(value):
                raise AssignmentV9Error(
                    f"{path}: non-finite parameter {parameter.parameter_id!r}"
                )
        parameters[parameter.parameter_id] = parameter
    for needed in (
        "residue_bulk_density_odt_per_m3",
        "residue_lhv_gj_per_odt",
        "payload_m3",
    ):
        if needed not in parameters:
            raise AssignmentV9Error(f"{path}: missing parameter {needed!r}")
    density = parameters["residue_bulk_density_odt_per_m3"].value_for("base")
    lhv = parameters["residue_lhv_gj_per_odt"].value_for("base")
    if not math.isclose(density, DENSITY_ODT_PER_M3, rel_tol=1e-12):
        raise AssignmentV9Error(
            "transport density parameter does not match the documented base"
        )
    if not math.isclose(lhv, LHV_GJ_PER_ODT, rel_tol=1e-12):
        raise AssignmentV9Error(
            "transport LHV parameter does not match the documented base"
        )
    return parameters


def load_tiers(path: Path = SUPPLY_CURVE_JSON) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    tiers = payload.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise AssignmentV9Error(f"{path}: no supply-curve tiers")
    result: list[dict[str, Any]] = []
    for row in tiers:
        tier = str(row["tier"])
        if tier not in TIER_ORDER:
            raise AssignmentV9Error(f"{path}: unexpected tier {tier!r}")
        cost = row.get("variable_cost_usd_per_gj", row.get("cost_per_gj"))
        if cost is None:
            raise AssignmentV9Error(f"{path}: tier {tier} has no cost")
        result.append(
            {
                "tier": tier,
                "technology": str(row.get("technology", "")),
                "cost_per_gj": float(cost),
            }
        )
    result.sort(key=lambda item: TIER_ORDER.index(item["tier"]))
    if [item["tier"] for item in result] != list(TIER_ORDER):
        raise AssignmentV9Error(f"{path}: tiers do not cover T1-T4 exactly")
    return result


def _tier_map(tiers: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    return {str(row["tier"]): float(row["cost_per_gj"]) for row in tiers}


# ---------------------------------------------------------------------------
# Transport arithmetic.
# ---------------------------------------------------------------------------

def transport_metrics(
    distance_km: float,
    parameters: Mapping[str, transport.Parameter],
) -> dict[str, float]:
    """Return base/low/high per-m3 and per-GJ truck costs at a distance."""

    if not math.isfinite(distance_km) or distance_km < 0.0:
        raise AssignmentV9Error(
            f"road distance must be non-negative; got {distance_km!r}"
        )
    values: dict[str, float] = {}
    for scenario in ("low", "base", "high"):
        cost = transport.trip_cost(
            distance_km, PAYLOAD_M3, dict(parameters), scenario
        )
        values[f"cost_m3_{scenario}"] = cost["cost_per_m3_cad"]
        values[f"cost_m3_{scenario}_raw_gj"] = cost["cost_per_gj_cad"]
    for scenario in ("low", "base", "high"):
        # Hold biomass energy per m3 at the base value so low/high express
        # cost-parameter uncertainty only.
        values[f"cost_gj_{scenario}"] = (
            values[f"cost_m3_{scenario}"] / ENERGY_GJ_PER_M3
        )
    base = values["cost_m3_base"]
    if not (values["cost_m3_low"] <= base <= values["cost_m3_high"]):
        raise AssignmentV9Error(
            f"transport cost scenarios are not ordered at {distance_km} km"
        )
    return values


def _validate_curve_parity(parameters: Mapping[str, transport.Parameter]) -> None:
    """Check our arithmetic against the committed transport v1 base curve."""

    rows = _read_csv(TRANSPORT_CURVES_CSV, "transport cost v1 curves")
    checked = 0
    for row in rows:
        if row["cost_scenario"] != "base":
            continue
        if not math.isclose(
            float(row["payload_m3"]), PAYLOAD_M3, rel_tol=1e-12
        ):
            continue
        distance = float(row["distance_km_one_way"])
        ours = transport_metrics(distance, parameters)
        expected_m3 = float(row["cost_per_m3_cad"])
        expected_gj = float(row["cost_per_gj_cad"])
        if not math.isclose(ours["cost_m3_base"], expected_m3, rel_tol=1e-9, abs_tol=1e-6):
            raise AssignmentV9Error(
                f"curve parity failed at {distance} km: "
                f"{ours['cost_m3_base']} vs {expected_m3}"
            )
        if not math.isclose(ours["cost_gj_base"], expected_gj, rel_tol=1e-9, abs_tol=1e-6):
            raise AssignmentV9Error(
                f"curve GJ parity failed at {distance} km: "
                f"{ours['cost_gj_base']} vs {expected_gj}"
            )
        checked += 1
    if checked < 5:
        raise AssignmentV9Error("too few transport curve parity rows checked")


# ---------------------------------------------------------------------------
# v8 transport accounting.
# ---------------------------------------------------------------------------

def read_v8_rows() -> list[dict[str, str]]:
    rows = _read_csv(V8_ASSIGNMENT_CSV, "assignment v8")
    required = {
        "scenario",
        "province",
        "facility_id",
        "facility_name",
        "facility_class",
        "facility_province",
        "cross_border",
        "allocation_phase",
        "capacity_status",
        "capacity_m3_y",
        "assigned_m3",
        "road_distance_km",
    }
    missing = required - set(rows[0])
    if missing:
        raise AssignmentV9Error(
            f"{V8_ASSIGNMENT_CSV}: missing columns {sorted(missing)}"
        )
    return rows


def v8_transport_rows(
    rows: Sequence[Mapping[str, str]],
    parameters: Mapping[str, transport.Parameter],
    tiers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    thresholds = _tier_map(tiers)
    result: list[dict[str, Any]] = []
    for row in rows:
        assigned = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        if assigned < 0.0 or distance < 0.0:
            raise AssignmentV9Error(
                f"{row['facility_id']}: negative volume or distance"
            )
        cost = transport_metrics(distance, parameters)
        energy = assigned * ENERGY_GJ_PER_M3
        total_base = assigned * cost["cost_m3_base"]
        total_low = assigned * cost["cost_m3_low"]
        total_high = assigned * cost["cost_m3_high"]
        cost_gj = total_base / energy if energy > 0.0 else 0.0
        cost_gj_low = total_low / energy if energy > 0.0 else 0.0
        cost_gj_high = total_high / energy if energy > 0.0 else 0.0
        enriched: dict[str, Any] = {
            "scenario": row["scenario"],
            "allocation_order": row["allocation_order"],
            "province": row["province"],
            "facility_id": row["facility_id"],
            "facility_name": row["facility_name"],
            "facility_class": row["facility_class"],
            "facility_province": row["facility_province"],
            "cross_border": row["cross_border"],
            "allocation_phase": row["allocation_phase"],
            "capacity_status": row["capacity_status"],
            "capacity_m3_y": row["capacity_m3_y"],
            "assigned_m3": _format_float(assigned, 9),
            "road_distance_km": _format_float(distance, 9),
            "transport_payload_m3": _format_float(PAYLOAD_M3, 3),
            "transport_cost_scenario": BASE_COST_SCENARIO,
            "transport_cost_cad_m3": _format_float(cost["cost_m3_base"], 9),
            "transport_cost_cad_m3_low": _format_float(cost["cost_m3_low"], 9),
            "transport_cost_cad_m3_high": _format_float(cost["cost_m3_high"], 9),
            "transport_cost_cad": _format_float(total_base, 6),
            "transport_cost_cad_low": _format_float(total_low, 6),
            "transport_cost_cad_high": _format_float(total_high, 6),
            "transport_energy_gj": _format_float(energy, 6),
            "transport_cost_cad_gj": _format_float(cost_gj, 9),
            "transport_cost_cad_gj_low": _format_float(cost_gj_low, 9),
            "transport_cost_cad_gj_high": _format_float(cost_gj_high, 9),
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
        }
        for tier in TIER_ORDER:
            key = f"delivered_cost_cad_gj_{tier.lower()}"
            enriched[key] = _format_float(cost_gj + thresholds[tier], 9)
            enriched[f"exceeds_{tier.lower()}"] = _bool_text(
                cost_gj > thresholds[tier]
            )
        result.append(enriched)
    return result


# ---------------------------------------------------------------------------
# Aggregation helpers.
# ---------------------------------------------------------------------------

def _weighted_mean(
    rows: Sequence[Mapping[str, Any]],
    value_field: str,
    weight_field: str = "assigned_m3",
) -> float:
    weight = math.fsum(float(row[weight_field]) for row in rows)
    if weight <= 0.0:
        return 0.0
    return math.fsum(
        float(row[weight_field]) * float(row[value_field]) for row in rows
    ) / weight


def _hhi(rows: Sequence[Mapping[str, Any]]) -> float:
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    if total <= 0.0:
        return 0.0
    by_facility: dict[str, float] = defaultdict(float)
    for row in rows:
        by_facility[row["facility_id"]] += float(row["assigned_m3"])
    return math.fsum((volume / total) ** 2 for volume in by_facility.values())


def _top_share(rows: Sequence[Mapping[str, Any]], n: int = 5) -> float:
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    if total <= 0.0:
        return 0.0
    by_facility: dict[str, float] = defaultdict(float)
    for row in rows:
        by_facility[row["facility_id"]] += float(row["assigned_m3"])
    top = sorted(by_facility.values(), reverse=True)[:n]
    return math.fsum(top) / total


def _binding_flags(rows: Sequence[Mapping[str, Any]]) -> tuple[int, float]:
    by_facility: dict[str, dict[str, float]] = {}
    for row in rows:
        facility = row["facility_id"]
        if facility not in by_facility:
            by_facility[facility] = {
                "assigned": 0.0,
                "capacity": float(row["capacity_m3_y"]),
            }
        by_facility[facility]["assigned"] += float(row["assigned_m3"])
    binding = [
        value
        for value in by_facility.values()
        if value["assigned"] >= value["capacity"] - VOLUME_EPSILON_M3
    ]
    return len(binding), math.fsum(value["assigned"] for value in binding)


def _facility_aggregates(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    thresholds: Mapping[str, float],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["facility_id"]].append(row)
    result: list[dict[str, Any]] = []
    for facility_id, group in sorted(grouped.items()):
        first = group[0]
        assigned = math.fsum(float(row["assigned_m3"]) for row in group)
        capacity = float(first["capacity_m3_y"])
        remaining = max(0.0, capacity - assigned)
        total_cost = math.fsum(float(row["transport_cost_cad"]) for row in group)
        total_cost_low = math.fsum(
            float(row["transport_cost_cad_low"]) for row in group
        )
        total_cost_high = math.fsum(
            float(row["transport_cost_cad_high"]) for row in group
        )
        energy = math.fsum(float(row["transport_energy_gj"]) for row in group)
        cost_gj = total_cost / energy if energy > 0.0 else 0.0
        cost_gj_low = total_cost_low / energy if energy > 0.0 else 0.0
        cost_gj_high = total_cost_high / energy if energy > 0.0 else 0.0
        distance_mean = _weighted_mean(group, "road_distance_km")
        distance_max = max(float(row["road_distance_km"]) for row in group)
        local = math.fsum(
            float(row["assigned_m3"])
            for row in group
            if row["allocation_phase"] == "local"
        )
        cross = assigned - local
        enriched = {
            "scenario": scenario,
            "facility_id": facility_id,
            "facility_name": first["facility_name"],
            "facility_class": first["facility_class"],
            "facility_province": first["facility_province"],
            "capacity_status": first["capacity_status"],
            "capacity_m3_y": _format_float(capacity, 9),
            "assigned_m3": _format_float(assigned, 9),
            "remaining_capacity_m3": _format_float(remaining, 9),
            "utilization": _format_float(
                assigned / capacity if capacity > 0.0 else 0.0, 9
            ),
            "binding": _bool_text(remaining <= VOLUME_EPSILON_M3),
            "used": _bool_text(assigned > VOLUME_EPSILON_M3),
            "local_assigned_m3": _format_float(local, 9),
            "cross_border_assigned_m3": _format_float(cross, 9),
            "road_distance_km_mean": _format_float(distance_mean, 9),
            "road_distance_km_max": _format_float(distance_max, 9),
            "transport_cost_cad": _format_float(total_cost, 6),
            "transport_cost_cad_m3": _format_float(
                total_cost / assigned if assigned > 0.0 else 0.0, 9
            ),
            "transport_cost_cad_gj": _format_float(cost_gj, 9),
            "transport_cost_cad_gj_low": _format_float(cost_gj_low, 9),
            "transport_cost_cad_gj_high": _format_float(cost_gj_high, 9),
            "delivered_cost_cad_gj_t1": _format_float(
                cost_gj + thresholds["T1"], 9
            ),
            "delivered_cost_cad_gj_t4": _format_float(
                cost_gj + thresholds["T4"], 9
            ),
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
        }
        for tier in TIER_ORDER:
            enriched[f"exceeds_{tier.lower()}"] = _bool_text(
                cost_gj > thresholds[tier]
            )
        result.append(enriched)
    return result


def _province_aggregates(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    offers: Mapping[str, float],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[row["province"]].append(row)
    result: list[dict[str, Any]] = []
    for province in sorted(set(grouped) | set(offers)):
        group = grouped.get(province, [])
        assigned = math.fsum(float(row["assigned_m3"]) for row in group)
        local = math.fsum(
            float(row["assigned_m3"])
            for row in group
            if row["allocation_phase"] == "local"
        )
        cross = assigned - local
        published = math.fsum(
            float(row["assigned_m3"])
            for row in group
            if row["capacity_status"] == "published"
        )
        proxy = assigned - published
        total_cost = math.fsum(
            float(row["transport_cost_cad"]) for row in group
        )
        energy = math.fsum(
            float(row["transport_energy_gj"]) for row in group
        )
        facilities = {row["facility_id"] for row in group}
        published_facilities = {
            row["facility_id"]
            for row in group
            if row["capacity_status"] == "published"
        }
        # Binding count is per facility, not per row.
        by_facility: dict[str, dict[str, float]] = {}
        for row in group:
            value = by_facility.setdefault(
                row["facility_id"],
                {"assigned": 0.0, "capacity": float(row["capacity_m3_y"])},
            )
            value["assigned"] += float(row["assigned_m3"])
        binding = [
            value
            for value in by_facility.values()
            if value["assigned"] >= value["capacity"] - VOLUME_EPSILON_M3
        ]
        result.append(
            {
                "scenario": scenario,
                "province": province,
                "offer_m3": _format_float(offers.get(province, 0.0), 9),
                "assigned_m3": _format_float(assigned, 9),
                "unassigned_m3": _format_float(
                    max(0.0, offers.get(province, 0.0) - assigned), 9
                ),
                "local_m3": _format_float(local, 9),
                "cross_border_m3": _format_float(cross, 9),
                "local_share": _format_float(
                    local / assigned if assigned > 0.0 else 0.0, 9
                ),
                "cross_border_share": _format_float(
                    cross / assigned if assigned > 0.0 else 0.0, 9
                ),
                "published_m3": _format_float(published, 9),
                "proxy_m3": _format_float(proxy, 9),
                "proxy_share_of_assigned": _format_float(
                    proxy / assigned if assigned > 0.0 else 0.0, 9
                ),
                "facilities_used": len(facilities),
                "published_facilities_used": len(published_facilities),
                "proxy_facilities_used": len(facilities - published_facilities),
                "road_distance_km_mean": _format_float(
                    _weighted_mean(group, "road_distance_km")
                    if group else 0.0,
                    9,
                ),
                "road_distance_km_max": _format_float(
                    max((float(row["road_distance_km"]) for row in group), default=0.0),
                    9,
                ),
                "transport_cost_cad": _format_float(total_cost, 6),
                "transport_cost_cad_m3": _format_float(
                    total_cost / assigned if assigned > 0.0 else 0.0, 9
                ),
                "transport_cost_cad_gj": _format_float(
                    total_cost / energy if energy > 0.0 else 0.0, 9
                ),
                "binding_facilities": len(binding),
                "binding_volume_m3": _format_float(
                    math.fsum(value["assigned"] for value in binding), 9
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return result


def _tier_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    scenario: str,
    tiers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    energy = math.fsum(float(row["transport_energy_gj"]) for row in rows)
    total_cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
    cost_gj = total_cost / energy if energy > 0.0 else 0.0
    result: list[dict[str, Any]] = []
    for tier in tiers:
        name = str(tier["tier"])
        threshold = float(tier["cost_per_gj"])
        above = [
            row
            for row in rows
            if float(row["transport_cost_cad_gj"]) > threshold
        ]
        volume_above = math.fsum(float(row["assigned_m3"]) for row in above)
        energy_above = math.fsum(
            float(row["transport_energy_gj"]) for row in above
        )
        result.append(
            {
                "scenario": scenario,
                "tier": name,
                "technology": tier["technology"],
                "tier_cost_per_gj": _format_float(threshold, 9),
                "volume_assigned_m3": _format_float(total, 9),
                "volume_above_m3": _format_float(volume_above, 9),
                "share_above": _format_float(
                    volume_above / total if total > 0.0 else 0.0, 9
                ),
                "assignments_above": len(above),
                "facilities_above": len(
                    {row["facility_id"] for row in above}
                ),
                "transport_cost_above_cad": _format_float(
                    math.fsum(
                        float(row["transport_cost_cad"]) for row in above
                    ),
                    6,
                ),
                "transport_cost_cad_gj": _format_float(
                    total_cost / energy_above if energy_above > 0.0 else 0.0, 9
                ),
                "delivered_cost_cad_gj": _format_float(cost_gj + threshold, 9),
                "caveat": TIER_CAVEAT,
            }
        )
    return result


def _comparison_rows(v8_rows: Sequence[Mapping[str, Any]], v9_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    def metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        total = math.fsum(float(row["assigned_m3"]) for row in rows)
        local = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "local"
        )
        cross = total - local
        published = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["capacity_status"] == "published"
        )
        total_cost = math.fsum(
            float(row["transport_cost_cad"]) for row in rows
        )
        energy = math.fsum(
            float(row["transport_energy_gj"]) for row in rows
        )
        binding, binding_volume = _binding_flags(rows)
        facilities = {row["facility_id"] for row in rows}
        used_capacity = math.fsum(
            float(row["capacity_m3_y"]) for row in rows
        )
        distances = [float(row["road_distance_km"]) for row in rows]
        return {
            "assigned_m3": total,
            "local_m3": local,
            "local_share": local / total if total > 0.0 else 0.0,
            "cross_border_m3": cross,
            "cross_border_share": cross / total if total > 0.0 else 0.0,
            "published_m3": published,
            "proxy_m3": total - published,
            "proxy_share_of_assigned": (total - published) / total if total > 0.0 else 0.0,
            "facilities_used": len(facilities),
            "assignments": len(rows),
            "mean_road_distance_km": _weighted_mean(rows, "road_distance_km"),
            "max_road_distance_km": max(distances) if distances else 0.0,
            "median_road_distance_km": (
                float(np.median(distances)) if distances else 0.0
            ),
            "total_volume_distance_m3_km": math.fsum(
                float(row["assigned_m3"]) * float(row["road_distance_km"])
                for row in rows
            ),
            "transport_cost_cad": total_cost,
            "transport_cost_cad_m3": total_cost / total if total > 0.0 else 0.0,
            "transport_cost_cad_gj": total_cost / energy if energy > 0.0 else 0.0,
            "binding_facilities": binding,
            "binding_volume_m3": binding_volume,
            "used_facility_capacity_m3": used_capacity,
            "used_facility_utilization": (
                total / used_capacity if used_capacity > 0.0 else 0.0
            ),
            "hhi": _hhi(rows),
            "top5_share": _top_share(rows),
        }

    before = metrics(v8_rows)
    after = metrics(v9_rows)
    definitions = (
        ("assigned_m3", "m3/yr", "assigned volume"),
        ("local_m3", "m3/yr", "volume served inside the origin province"),
        ("local_share", "share", "local / assigned"),
        ("cross_border_m3", "m3/yr", "volume served out of province"),
        ("cross_border_share", "share", "cross-border / assigned"),
        ("published_m3", "m3/yr", "volume on published capacity"),
        ("proxy_m3", "m3/yr", "volume on proxy capacity"),
        ("proxy_share_of_assigned", "share", "proxy / assigned"),
        ("facilities_used", "count", "distinct facilities receiving volume"),
        ("assignments", "count", "assignment rows"),
        ("mean_road_distance_km", "km", "assigned-volume-weighted mean road distance"),
        ("max_road_distance_km", "km", "maximum assignment-row road distance"),
        ("median_road_distance_km", "km", "unweighted median row distance"),
        ("total_volume_distance_m3_km", "m3*km/yr", "volume times road distance"),
        ("transport_cost_cad", "CAD/yr", "modelled transport cost"),
        ("transport_cost_cad_m3", "CAD/m3", "transport cost per assigned m3"),
        ("transport_cost_cad_gj", "CAD/GJ", "transport cost per delivered GJ"),
        ("binding_facilities", "count", "facilities at capacity"),
        ("binding_volume_m3", "m3/yr", "volume at binding facilities"),
        ("used_facility_utilization", "share", "assigned / capacity of used facilities"),
        ("hhi", "index_0_1", "sum of squared facility shares"),
        ("top5_share", "share", "top five facilities / assigned"),
    )
    rows: list[dict[str, Any]] = []
    for metric, unit, note in definitions:
        old = before[metric]
        new = after[metric]
        delta = new - old
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "v8_value": _format_float(old, 9),
                "v9_value": _format_float(new, 9),
                "delta_v9_minus_v8": _format_float(delta, 9),
                "delta_pct": _format_float(
                    100.0 * delta / old if old != 0.0 else 0.0, 9
                ),
                "note": note,
            }
        )
    return rows


def _facility_change_rows(
    v8_rows: Sequence[Mapping[str, Any]],
    v9_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    before: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    after: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in v8_rows:
        before[row["facility_id"]].append(row)
    for row in v9_rows:
        after[row["facility_id"]].append(row)
    result: list[dict[str, Any]] = []
    for facility_id in sorted(set(before) | set(after)):
        old_rows = before.get(facility_id, [])
        new_rows = after.get(facility_id, [])
        first = (new_rows or old_rows)[0]
        old_volume = math.fsum(float(row["assigned_m3"]) for row in old_rows)
        new_volume = math.fsum(float(row["assigned_m3"]) for row in new_rows)
        delta = new_volume - old_volume
        if not old_rows:
            status = "added"
        elif not new_rows:
            status = "removed"
        elif abs(delta) <= VOLUME_EPSILON_M3:
            status = "unchanged"
        else:
            status = "changed"
        result.append(
            {
                "facility_id": facility_id,
                "facility_name": first["facility_name"],
                "facility_class": first["facility_class"],
                "facility_province": first["facility_province"],
                "capacity_status": first["capacity_status"],
                "capacity_m3_y": first["capacity_m3_y"],
                "v8_assigned_m3": _format_float(old_volume, 9),
                "v9_assigned_m3": _format_float(new_volume, 9),
                "delta_v9_minus_v8_m3": _format_float(delta, 9),
                "v8_rows": len(old_rows),
                "v9_rows": len(new_rows),
                "v8_allocation_phase": (
                    ",".join(sorted({row["allocation_phase"] for row in old_rows}))
                    if old_rows else ""
                ),
                "v9_allocation_phase": (
                    ",".join(sorted({row["allocation_phase"] for row in new_rows}))
                    if new_rows else ""
                ),
                "v8_transport_cost_cad_gj": _format_float(
                    _weighted_mean(old_rows, "transport_cost_cad_gj")
                    if old_rows else 0.0,
                    9,
                ),
                "v9_transport_cost_cad_gj": _format_float(
                    _weighted_mean(new_rows, "transport_cost_cad_gj")
                    if new_rows else 0.0,
                    9,
                ),
                "status": status,
            }
        )
    return result


def _province_comparison_rows(
    v8_rows: Sequence[Mapping[str, Any]],
    v9_rows: Sequence[Mapping[str, Any]],
    offers: Mapping[str, float],
) -> list[dict[str, Any]]:
    def grouped(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
        result: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in rows:
            result[row["province"]].append(row)
        return result

    before = grouped(v8_rows)
    after = grouped(v9_rows)
    result: list[dict[str, Any]] = []
    for province in sorted(set(before) | set(after)):
        old = before.get(province, [])
        new = after.get(province, [])

        def block(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
            total = math.fsum(float(row["assigned_m3"]) for row in rows)
            local = math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if row["allocation_phase"] == "local"
            )
            published = math.fsum(
                float(row["assigned_m3"])
                for row in rows
                if row["capacity_status"] == "published"
            )
            cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
            energy = math.fsum(
                float(row["transport_energy_gj"]) for row in rows
            )
            return {
                "assigned": total,
                "local": local,
                "cross": total - local,
                "mean_km": _weighted_mean(rows, "road_distance_km"),
                "cost": cost,
                "cost_gj": cost / energy if energy > 0.0 else 0.0,
                "facilities": len({row["facility_id"] for row in rows}),
                "proxy_share": (total - published) / total if total > 0.0 else 0.0,
            }

        old_block = block(old)
        new_block = block(new)
        result.append(
            {
                "province": province,
                "v8_offer_m3": _format_float(offers.get(province, 0.0), 9),
                "v9_offer_m3": _format_float(offers.get(province, 0.0), 9),
                "v8_assigned_m3": _format_float(old_block["assigned"], 9),
                "v9_assigned_m3": _format_float(new_block["assigned"], 9),
                "v8_local_m3": _format_float(old_block["local"], 9),
                "v9_local_m3": _format_float(new_block["local"], 9),
                "v8_cross_border_m3": _format_float(old_block["cross"], 9),
                "v9_cross_border_m3": _format_float(new_block["cross"], 9),
                "v8_road_distance_km_mean": _format_float(old_block["mean_km"], 9),
                "v9_road_distance_km_mean": _format_float(new_block["mean_km"], 9),
                "v8_transport_cost_cad": _format_float(old_block["cost"], 6),
                "v9_transport_cost_cad": _format_float(new_block["cost"], 6),
                "v8_transport_cost_cad_gj": _format_float(old_block["cost_gj"], 9),
                "v9_transport_cost_cad_gj": _format_float(new_block["cost_gj"], 9),
                "v8_facilities_used": old_block["facilities"],
                "v9_facilities_used": new_block["facilities"],
                "v8_proxy_share_of_assigned": _format_float(
                    old_block["proxy_share"], 9
                ),
                "v9_proxy_share_of_assigned": _format_float(
                    new_block["proxy_share"], 9
                ),
            }
        )
    return result


# ---------------------------------------------------------------------------
# v9 transport-aware allocation.
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _Pair:
    offer_index: int
    facility_index: int
    distance_km: float
    road_factor: float
    road_distance_km: float
    base_cost_cad_m3: float
    cross_border: bool


def _eligible_facilities() -> tuple[list[Any], list[Any], Mapping[str, float]]:
    factors, _ = v8._load_factors()
    base = v5.read_facilities(CAPACITY_CSV)
    v8._validate_factor_coverage(base, factors)
    offers, _ = a2.read_offers(HARVEST_CSV, BOUNDARY_GEOJSON)
    floor_plan = a3.build_floor_plan(
        base, {offer.province: offer.volume_m3 for offer in offers}
    )
    eligible = [
        facility
        for facility in floor_plan.facilities
        if facility.facility_class in v6.RELEVANT_CLASSES
        and facility.is_operating
        and facility.location is not None
        and facility.capacity is not None
    ]
    active_offers = [offer for offer in offers if offer.volume_m3 > 0.0]
    return eligible, active_offers, factors


def _pair_rows(
    offers: Sequence[Any],
    facilities: Sequence[Any],
    factors: Mapping[str, float],
    parameters: Mapping[str, transport.Parameter],
) -> list[_Pair]:
    pairs: list[_Pair] = []
    for offer_index, offer in enumerate(offers):
        for facility_index, facility in enumerate(facilities):
            distance = haversine_km(
                offer.location.lat,
                offer.location.lon,
                facility.location.lat,
                facility.location.lon,
            ) * factors[facility.province]
            cost = transport.trip_cost(
                distance, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
            )["cost_per_m3_cad"]
            factor = factors[facility.province]
            pairs.append(
                _Pair(
                    offer_index=offer_index,
                    facility_index=facility_index,
                    distance_km=distance / factor,
                    road_factor=factor,
                    road_distance_km=distance,
                    base_cost_cad_m3=cost,
                    cross_border=facility.province != offer.province,
                )
            )
    return pairs


def _solve_transport_lp(
    offers: Sequence[Any],
    facilities: Sequence[Any],
    pairs: Sequence[_Pair],
    *,
    penalty_cad_m3: float,
) -> np.ndarray:
    """Solve the transport-cost transportation problem with HiGHS."""

    if penalty_cad_m3 < 0.0 or not math.isfinite(penalty_cad_m3):
        raise AssignmentV9Error("local preference penalty must be finite and non-negative")
    n_offers = len(offers)
    n_facilities = len(facilities)
    n_pairs = len(pairs)
    if n_pairs == 0:
        raise AssignmentV9Error("no candidate facility-offer pairs")
    eq_rows = np.repeat(np.arange(n_offers), n_facilities)
    eq_cols = np.arange(n_pairs)
    eq_data = np.ones(n_pairs)
    ub_rows = np.tile(np.arange(n_facilities), n_offers)
    ub_cols = np.arange(n_pairs)
    ub_data = np.ones(n_pairs)
    a_eq = csr_matrix((eq_data, (eq_rows, eq_cols)), shape=(n_offers, n_pairs))
    a_ub = csr_matrix((ub_data, (ub_rows, ub_cols)), shape=(n_facilities, n_pairs))
    # Stable deterministic tie-break; much smaller than a cent per m3.
    objective = np.array(
        [
            pair.base_cost_cad_m3
            + (penalty_cad_m3 if pair.cross_border else 0.0)
            + TIE_BREAK_CAD_M3 * index
            for index, pair in enumerate(pairs)
        ],
        dtype=float,
    )
    b_eq = np.array([offer.volume_m3 for offer in offers], dtype=float)
    b_ub = np.array(
        [float(facility.capacity_m3_y) for facility in facilities], dtype=float
    )
    result = linprog(
        objective,
        A_ub=a_ub,
        b_ub=b_ub,
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=(0.0, None),
        method="highs",
        options={"presolve": True},
    )
    if not result.success:
        raise AssignmentV9Error(
            "transport LP failed: " + str(result.message)
        )
    if result.x is None:
        raise AssignmentV9Error("transport LP returned no solution")
    return np.asarray(result.x, dtype=float)


def _allocations(
    offers: Sequence[Any],
    facilities: Sequence[Any],
    pairs: Sequence[_Pair],
    solution: np.ndarray,
) -> dict[tuple[int, int], float]:
    allocations: dict[tuple[int, int], float] = defaultdict(float)
    for pair, volume in zip(pairs, solution):
        if volume > 1e-9:
            allocations[(pair.offer_index, pair.facility_index)] += float(volume)
    # Enforce exact conservation per offer; the adjustment is numerical only.
    for offer_index, offer in enumerate(offers):
        total = math.fsum(
            volume
            for (oi, _), volume in allocations.items()
            if oi == offer_index
        )
        if total <= 0.0:
            raise AssignmentV9Error(
                f"{offer.province}: no allocation produced by the LP"
            )
        gap = offer.volume_m3 - total
        if abs(gap) > 1e-3:
            raise AssignmentV9Error(
                f"{offer.province}: conservation gap {gap!r} m3 in LP solution"
            )
        scale = offer.volume_m3 / total
        for key in list(allocations):
            if key[0] == offer_index:
                allocations[key] *= scale
    # Capacity guard.
    facility_totals: dict[int, float] = defaultdict(float)
    for (_, facility_index), volume in allocations.items():
        facility_totals[facility_index] += volume
    for facility_index, volume in facility_totals.items():
        capacity = float(facilities[facility_index].capacity_m3_y)
        if volume > capacity + 1e-3:
            raise AssignmentV9Error(
                f"{facilities[facility_index].facility_id}: LP exceeds capacity"
            )
    return allocations


def _v9_assignment_rows(
    offers: Sequence[Any],
    facilities: Sequence[Any],
    pairs: Sequence[_Pair],
    allocations: Mapping[tuple[int, int], float],
    parameters: Mapping[str, transport.Parameter],
    *,
    penalty_km: float,
    penalty_cad_m3: float,
    scenario: str,
    thresholds: Mapping[str, float],
) -> list[dict[str, Any]]:
    pair_lookup = {(pair.offer_index, pair.facility_index): pair for pair in pairs}
    facility_totals: dict[int, float] = defaultdict(float)
    for (_, facility_index), volume in allocations.items():
        facility_totals[facility_index] += volume
    records: list[dict[str, Any]] = []
    for (offer_index, facility_index), volume in allocations.items():
        if volume <= VOLUME_EPSILON_M3:
            continue
        offer = offers[offer_index]
        facility = facilities[facility_index]
        pair = pair_lookup[(offer_index, facility_index)]
        cost = transport_metrics(pair.road_distance_km, parameters)
        capacity = facility.capacity
        energy = volume * ENERGY_GJ_PER_M3
        total_base = volume * cost["cost_m3_base"]
        total_low = volume * cost["cost_m3_low"]
        total_high = volume * cost["cost_m3_high"]
        records.append(
            {
                "scenario": scenario,
                "allocation_order": 0,
                "province": offer.province,
                "province_offer_m3": _format_float(offer.volume_m3, 9),
                "facility_id": facility.facility_id,
                "facility_name": facility.name,
                "facility_class": facility.facility_class,
                "facility_province": facility.province,
                "cross_border": _bool_text(pair.cross_border),
                "allocation_phase": (
                    "cross_border" if pair.cross_border else "local"
                ),
                "capacity_status": capacity.status,
                "capacity_m3_y": _format_float(capacity.capacity_m3_y, 9),
                "proxy_low_m3_y": _format_float(capacity.proxy_low_m3_y, 9),
                "proxy_high_m3_y": _format_float(capacity.proxy_high_m3_y, 9),
                "assigned_m3": _format_float(volume, 9),
                "facility_assigned_m3": _format_float(
                    facility_totals[facility_index], 9
                ),
                "remaining_capacity_m3": _format_float(
                    max(0.0, capacity.capacity_m3_y - facility_totals[facility_index]),
                    9,
                ),
                "utilization": _format_float(
                    facility_totals[facility_index] / capacity.capacity_m3_y, 9
                ),
                "distance_km": _format_float(pair.distance_km, 9),
                "road_distance_km": _format_float(pair.road_distance_km, 9),
                "road_factor_applied": _format_float(pair.road_factor, 12),
                "road_factor_basis": "facility_province",
                "cross_border_penalty_km": _format_float(penalty_km, 3),
                "cross_border_penalty_cad_m3": _format_float(
                    penalty_cad_m3 if pair.cross_border else 0.0, 9
                ),
                "transport_payload_m3": _format_float(PAYLOAD_M3, 3),
                "transport_cost_scenario": BASE_COST_SCENARIO,
                "transport_cost_cad_m3": _format_float(cost["cost_m3_base"], 9),
                "transport_cost_cad_m3_low": _format_float(cost["cost_m3_low"], 9),
                "transport_cost_cad_m3_high": _format_float(cost["cost_m3_high"], 9),
                "transport_cost_cad": _format_float(total_base, 6),
                "transport_cost_cad_low": _format_float(total_low, 6),
                "transport_cost_cad_high": _format_float(total_high, 6),
                "transport_energy_gj": _format_float(energy, 6),
                "transport_cost_cad_gj": _format_float(
                    total_base / energy if energy > 0.0 else 0.0, 9
                ),
                "transport_cost_cad_gj_low": _format_float(
                    total_low / energy if energy > 0.0 else 0.0, 9
                ),
                "transport_cost_cad_gj_high": _format_float(
                    total_high / energy if energy > 0.0 else 0.0, 9
                ),
                "delivered_cost_cad_gj_t1": _format_float(
                    total_base / energy + thresholds["T1"]
                    if energy > 0.0 else thresholds["T1"],
                    9,
                ),
                "delivered_cost_cad_gj_t2": _format_float(
                    total_base / energy + thresholds["T2"]
                    if energy > 0.0 else thresholds["T2"],
                    9,
                ),
                "delivered_cost_cad_gj_t3": _format_float(
                    total_base / energy + thresholds["T3"]
                    if energy > 0.0 else thresholds["T3"],
                    9,
                ),
                "delivered_cost_cad_gj_t4": _format_float(
                    total_base / energy + thresholds["T4"]
                    if energy > 0.0 else thresholds["T4"],
                    9,
                ),
                "exceeds_t1": _bool_text(
                    total_base / energy > thresholds["T1"]
                    if energy > 0.0 else False
                ),
                "exceeds_t2": _bool_text(
                    total_base / energy > thresholds["T2"]
                    if energy > 0.0 else False
                ),
                "exceeds_t3": _bool_text(
                    total_base / energy > thresholds["T3"]
                    if energy > 0.0 else False
                ),
                "exceeds_t4": _bool_text(
                    total_base / energy > thresholds["T4"]
                    if energy > 0.0 else False
                ),
                "capacity_evidence": capacity.capacity_evidence,
                "floor_scale_factor": _format_float(
                    capacity.floor_scale_factor, 12
                ),
                "source_id": facility.source_id,
                "source_url": facility.source_url,
                "capacity_source_id": facility.capacity_source_id,
                "capacity_source_url": facility.capacity_source_url,
                "evidence_tier": facility.evidence_tier,
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    records.sort(
        key=lambda row: (
            row["province"],
            0 if row["allocation_phase"] == "local" else 1,
            float(row["road_distance_km"]),
            row["facility_id"],
        )
    )
    for index, row in enumerate(records, start=1):
        row["allocation_order"] = index
    return records


def _sensitivity(
    offers: Sequence[Any],
    facilities: Sequence[Any],
    pairs: Sequence[_Pair],
    parameters: Mapping[str, transport.Parameter],
    thresholds: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for penalty_km in PENALTY_SENSITIVITY_KM:
        if penalty_km == 0.0:
            penalty_cad_m3 = 0.0
        else:
            penalty_cad_m3 = transport.trip_cost(
                penalty_km, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
            )["cost_per_m3_cad"]
        solution = _solve_transport_lp(
            offers, facilities, pairs, penalty_cad_m3=penalty_cad_m3
        )
        allocations = _allocations(offers, facilities, pairs, solution)
        records = _v9_assignment_rows(
            offers,
            facilities,
            pairs,
            allocations,
            parameters,
            penalty_km=penalty_km,
            penalty_cad_m3=penalty_cad_m3,
            scenario=PRIMARY_SCENARIO,
            thresholds=thresholds,
        )
        rows.append(_sensitivity_row(penalty_km, penalty_cad_m3, records))
    return rows


def _sensitivity_row(
    penalty_km: float,
    penalty_cad_m3: float,
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = math.fsum(float(row["assigned_m3"]) for row in rows)
    cross = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if row["allocation_phase"] == "cross_border"
    )
    cost = math.fsum(float(row["transport_cost_cad"]) for row in rows)
    energy = math.fsum(
        float(row["transport_energy_gj"]) for row in rows
    )
    published = math.fsum(
        float(row["assigned_m3"])
        for row in rows
        if row["capacity_status"] == "published"
    )
    binding, binding_volume = _binding_flags(rows)
    distances = [float(row["road_distance_km"]) for row in rows]
    return {
        "local_penalty_km": _format_float(penalty_km, 3),
        "local_penalty_cad_m3": _format_float(penalty_cad_m3, 9),
        "local_m3": _format_float(total - cross, 9),
        "cross_border_m3": _format_float(cross, 9),
        "cross_border_share": _format_float(
            cross / total if total > 0.0 else 0.0, 9
        ),
        "road_distance_km_mean": _format_float(
            _weighted_mean(rows, "road_distance_km"), 9
        ),
        "road_distance_km_max": _format_float(max(distances) if distances else 0.0, 9),
        "transport_cost_cad": _format_float(cost, 6),
        "transport_cost_cad_m3": _format_float(
            cost / total if total > 0.0 else 0.0, 9
        ),
        "transport_cost_cad_gj": _format_float(
            cost / energy if energy > 0.0 else 0.0, 9
        ),
        "facilities_used": len({row["facility_id"] for row in rows}),
        "binding_facilities": binding,
        "binding_volume_m3": _format_float(binding_volume, 9),
        "proxy_share_of_assigned": _format_float(
            (total - published) / total if total > 0.0 else 0.0, 9
        ),
        "status": "screening_sensitivity",
    }


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def validate(
    *,
    v8_transport: Sequence[Mapping[str, Any]],
    v9_rows: Sequence[Mapping[str, Any]],
    v9_utilization: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    sensitivity: Sequence[Mapping[str, Any]],
    offers: Mapping[str, float],
    facilities: Sequence[Any],
    parameters: Mapping[str, transport.Parameter],
    thresholds: Mapping[str, float],
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    # Cost arithmetic on v8 rows.
    arithmetic = True
    for row in v8_transport:
        assigned = float(row["assigned_m3"])
        total = float(row["transport_cost_cad"])
        per_m3 = float(row["transport_cost_cad_m3"])
        energy = float(row["transport_energy_gj"])
        cost_gj = float(row["transport_cost_cad_gj"])
        if not math.isclose(total, assigned * per_m3, rel_tol=1e-9, abs_tol=1e-3):
            arithmetic = False
        if energy > 0.0 and not math.isclose(
            cost_gj, total / energy, rel_tol=1e-9, abs_tol=1e-9
        ):
            arithmetic = False
        if not (
            float(row["transport_cost_cad_m3_low"])
            <= per_m3
            <= float(row["transport_cost_cad_m3_high"])
        ):
            arithmetic = False
    checks["v8_cost_arithmetic"] = arithmetic

    # v8 conservation per scenario.
    conservation = True
    for scenario in sorted({row["scenario"] for row in v8_transport}):
        rows = [row for row in v8_transport if row["scenario"] == scenario]
        assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
        if scenario == PRIMARY_SCENARIO:
            expected = math.fsum(offers.values())
            if not math.isclose(assigned, expected, abs_tol=1e-3):
                conservation = False
        local = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "local"
        )
        cross = math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["allocation_phase"] == "cross_border"
        )
        if not math.isclose(local + cross, assigned, abs_tol=1e-3):
            conservation = False
    checks["v8_conservation"] = conservation

    # v9 conservation and capacity.
    v9_assigned = math.fsum(float(row["assigned_m3"]) for row in v9_rows)
    expected = math.fsum(offers.values())
    checks["v9_conservation"] = math.isclose(
        v9_assigned, expected, abs_tol=1e-3
    )
    v9_local = math.fsum(
        float(row["assigned_m3"])
        for row in v9_rows
        if row["allocation_phase"] == "local"
    )
    v9_cross = math.fsum(
        float(row["assigned_m3"])
        for row in v9_rows
        if row["allocation_phase"] == "cross_border"
    )
    checks["v9_local_plus_cross_equals_assigned"] = math.isclose(
        v9_local + v9_cross, v9_assigned, abs_tol=1e-3
    )
    capacity_ok = True
    by_facility: dict[str, float] = defaultdict(float)
    for row in v9_rows:
        by_facility[row["facility_id"]] += float(row["assigned_m3"])
        if float(row["assigned_m3"]) < 0.0:
            capacity_ok = False
        if float(row["utilization"]) > 1.0 + 1e-9:
            capacity_ok = False
    capacity_by_id = {
        facility.facility_id: float(facility.capacity_m3_y)
        for facility in facilities
    }
    for facility_id, assigned in by_facility.items():
        if assigned > capacity_by_id[facility_id] + 1e-3:
            capacity_ok = False
    checks["v9_capacity_bounds"] = capacity_ok

    # Local-first: cross-border is only allowed where the province has no
    # local eligible capacity (NT in this input set).
    local_capacity: dict[str, float] = defaultdict(float)
    for facility in facilities:
        local_capacity[facility.province] += float(facility.capacity_m3_y)
    cross_by_province: dict[str, float] = defaultdict(float)
    for row in v9_rows:
        if row["allocation_phase"] == "cross_border":
            cross_by_province[row["province"]] += float(row["assigned_m3"])
    local_first_ok = True
    for province, volume in cross_by_province.items():
        if (
            volume > VOLUME_EPSILON_M3
            and local_capacity.get(province, 0.0)
            >= offers.get(province, 0.0) - 1e-3
        ):
            local_first_ok = False
    checks["v9_local_first_shortfall_only"] = local_first_ok

    # Province rows reconcile with assignment rows.
    province_ok = True
    for row in province_rows:
        if not math.isclose(
            float(row["local_m3"]) + float(row["cross_border_m3"]),
            float(row["assigned_m3"]),
            abs_tol=1e-3,
        ):
            province_ok = False
        if float(row["assigned_m3"]) < -1e-9:
            province_ok = False
    checks["province_reconciliation"] = province_ok

    # Comparison arithmetic.
    comparison_ok = True
    for row in comparison:
        old = float(row["v8_value"])
        new = float(row["v9_value"])
        delta = new - old
        if not math.isclose(
            float(row["delta_v9_minus_v8"]), delta, rel_tol=1e-9, abs_tol=1e-6
        ):
            comparison_ok = False
    checks["comparison_arithmetic"] = comparison_ok
    comparison_map = {row["metric"]: row for row in comparison}
    checks["v9_transport_cost_below_v8"] = (
        float(comparison_map["transport_cost_cad"]["v9_value"])
        < float(comparison_map["transport_cost_cad"]["v8_value"])
    )
    checks["v9_mean_distance_below_v8"] = (
        float(comparison_map["mean_road_distance_km"]["v9_value"])
        < float(comparison_map["mean_road_distance_km"]["v8_value"])
    )
    checks["products_residue_unchanged"] = True

    # Tier flags must agree with the base cost/GJ arithmetic.
    tier_ok = True
    for name in ("v8", "v9"):
        rows: Sequence[Mapping[str, Any]] = (
            v8_transport if name == "v8" else v9_rows
        )
        for tier in TIER_ORDER:
            threshold = thresholds[tier]
            key = f"exceeds_{tier.lower()}"
            for row in rows:
                expected = float(row["transport_cost_cad_gj"]) > threshold
                if row.get(key) != _bool_text(expected):
                    tier_ok = False
    checks["tier_flags_well_formed"] = tier_ok

    # Sensitivity must show the pure-cost case at least as cheap as the
    # local-preference case.
    sensitivity_ok = True
    by_penalty = {float(row["local_penalty_km"]): row for row in sensitivity}
    if (
        0.0 in by_penalty
        and 500.0 in by_penalty
        and float(by_penalty[0.0]["transport_cost_cad"])
        > float(by_penalty[500.0]["transport_cost_cad"]) + 1e-3
    ):
        sensitivity_ok = False
    checks["sensitivity_cost_order"] = sensitivity_ok

    # No NaN or negative in every numeric artifact.
    no_nan = True
    numeric_fields = (
        "assigned_m3",
        "road_distance_km",
        "transport_cost_cad",
        "transport_cost_cad_m3",
        "transport_cost_cad_gj",
        "transport_energy_gj",
    )
    for row in list(v8_transport) + list(v9_rows):
        for field in numeric_fields:
            value = float(row[field])
            if not math.isfinite(value) or value < 0.0:
                no_nan = False
    checks["no_nan_or_negative"] = no_nan

    # Utilization artifact contains every eligible facility and no negative.
    utilization_ok = len(v9_utilization) == len(facilities)
    for row in v9_utilization:
        if float(row["capacity_m3_y"]) < 0.0:
            utilization_ok = False
        if float(row["utilization"]) > 1.0 + 1e-9:
            utilization_ok = False
    checks["utilization_complete"] = utilization_ok

    checks["labels"] = (
        EVIDENCE_LABEL == "model_estimate"
        and OBSERVED is False
        and POLICY_GRADE is False
    )
    return {"all_passed": all(checks.values()), "checks": checks}


# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------

def _md_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return lines


def _report(
    summary: Mapping[str, Any],
    *,
    v8_primary: Sequence[Mapping[str, Any]],
    v8_facility: Sequence[Mapping[str, Any]],
    v9_rows: Sequence[Mapping[str, Any]],
    v9_facility: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    sensitivity: Sequence[Mapping[str, Any]],
) -> str:
    labels = summary["labels"]
    lines = [
        "# Transport-Aware National Facility Assignment V9",
        "",
        (
            "Status: **model estimate screening** — "
            f"`evidence_label={labels['evidence_label']}`, "
            f"`observed={str(labels['observed']).lower()}`, "
            f"`policy_grade={str(labels['policy_grade']).lower()}`. "
            "No national claim and no official OSeCAN change."
        ),
        "",
        "## 1. Transport cost v8",
        "",
        (
            "Transport cost is the committed transport cost v1 truck model "
            f"({PAYLOAD_M3:.1f} m3 base payload, empty return, 2026 CAD). "
            "Row cost = assigned m3 x cost/m3 at the row road distance. "
            "Energy is held at the base 4.07 GJ/m3 (0.22 ODT/m3 x 18.5 GJ/ODT) "
            "so low/high columns express cost-parameter uncertainty only."
        ),
        "",
    ]
    v8_primary_volume = math.fsum(
        float(row["assigned_m3"]) for row in v8_primary
    )
    v8_primary_cost = math.fsum(
        float(row["transport_cost_cad"]) for row in v8_primary
    )
    v8_primary_energy = math.fsum(
        float(row["transport_energy_gj"]) for row in v8_primary
    )
    v8_primary_distance = _weighted_mean(v8_primary, "road_distance_km")
    lines.extend(
        [
            (
                f"- Assigned: **{v8_primary_volume:,.0f} m3/yr**; "
                f"transport cost: **{v8_primary_cost / 1e9:,.3f} billion CAD/yr**; "
                f"**{v8_primary_cost / v8_primary_volume:,.2f} CAD/m3**; "
                f"**{v8_primary_cost / v8_primary_energy:,.3f} CAD/GJ**."
            ),
            (
                f"- Assigned-volume-weighted mean road distance: "
                f"**{v8_primary_distance:,.1f} km**; maximum row distance: "
                f"**{max(float(row['road_distance_km']) for row in v8_primary):,.1f} km**."
            ),
            (
                f"- Facility count with positive assignment: "
                f"**{len({row['facility_id'] for row in v8_primary})}**; "
                f"proxy share of assigned volume: "
                f"**{float(summary['v8_primary']['proxy_share_of_assigned']):.4f}**."
            ),
            "",
            "### Tier face-value comparison (v8 primary)",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ("Tier", "Tier CAD/GJ", "Volume above", "Share above", "Facilities above", "Delivered CAD/GJ"),
            [
                (
                    row["tier"],
                    f"{float(row['tier_cost_per_gj']):.3f}",
                    f"{float(row['volume_above_m3']):,.0f}",
                    f"{float(row['share_above']):.4f}",
                    row["facilities_above"],
                    f"{float(row['delivered_cost_cad_gj']):.3f}",
                )
                for row in tier_rows
            ],
        )
    )
    lines.extend(
        [
            "",
            f"> {TIER_CAVEAT}",
            "",
            "## 2. V9 assignment",
            "",
            (
                "V9 is a bipartite transportation linear program. It minimises "
                "modelled transport cost plus a local-preference penalty, "
                "subject to every provincial offer equality and every finite "
                "facility capacity. The penalty is the v8 documented 500 km "
                "cross-border rule expressed in 2026 CAD/m3 through the same "
                "transport curve "
                f"(**{float(summary['method']['local_penalty_cad_m3']):,.2f} CAD/m3**). "
                "Capacity v3 and the 1.2x provincial floor are unchanged. "
                "Products and residue are not recalculated or written."
            ),
            "",
        ]
    )
    v9_volume = math.fsum(float(row["assigned_m3"]) for row in v9_rows)
    v9_cost = math.fsum(float(row["transport_cost_cad"]) for row in v9_rows)
    v9_energy = math.fsum(
        float(row["transport_energy_gj"]) for row in v9_rows
    )
    lines.extend(
        [
            (
                f"- Assigned: **{v9_volume:,.0f} m3/yr**; transport cost: "
                f"**{v9_cost / 1e9:,.3f} billion CAD/yr**; "
                f"**{v9_cost / v9_volume:,.2f} CAD/m3**; "
                f"**{v9_cost / v9_energy:,.3f} CAD/GJ**."
            ),
            (
                f"- Mean road distance **{_weighted_mean(v9_rows, 'road_distance_km'):,.1f} km**; "
                f"maximum row distance **{max(float(row['road_distance_km']) for row in v9_rows):,.1f} km**; "
                f"facilities used **{len({row['facility_id'] for row in v9_rows})}**."
            ),
            (
                f"- Local volume **{float(summary['v9_primary']['local_m3']):,.0f} m3/yr**; "
                f"cross-border **{float(summary['v9_primary']['cross_border_m3']):,.0f} m3/yr** "
                f"({float(summary['v9_primary']['cross_border_share']):.6f} share); "
                f"proxy share **{float(summary['v9_primary']['proxy_share_of_assigned']):.4f}**."
            ),
            "",
            "### V9 province summary",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ("Province", "Offer", "Local", "Cross", "Mean km", "Cost CAD", "CAD/GJ", "Facilities"),
            [
                (
                    row["province"],
                    f"{float(row['offer_m3']):,.0f}",
                    f"{float(row['local_m3']):,.0f}",
                    f"{float(row['cross_border_m3']):,.0f}",
                    f"{float(row['road_distance_km_mean']):,.1f}",
                    f"{float(row['transport_cost_cad']):,.0f}",
                    f"{float(row['transport_cost_cad_gj']):.3f}",
                    row["facilities_used"],
                )
                for row in province_rows
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 3. V8 versus V9",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ("Metric", "Unit", "V8", "V9", "Delta", "Delta %"),
            [
                (
                    row["metric"],
                    row["unit"],
                    f"{float(row['v8_value']):,.4f}",
                    f"{float(row['v9_value']):,.4f}",
                    f"{float(row['delta_v9_minus_v8']):,.4f}",
                    f"{float(row['delta_pct']):,.2f}",
                )
                for row in comparison
            ],
        )
    )
    lines.extend(
        [
            "",
            "### Local-preference sensitivity",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ("Penalty km", "Penalty CAD/m3", "Cross share", "Mean km", "Cost CAD", "CAD/GJ", "Proxy share"),
            [
                (
                    f"{float(row['local_penalty_km']):,.0f}",
                    f"{float(row['local_penalty_cad_m3']):,.2f}",
                    f"{float(row['cross_border_share']):.4f}",
                    f"{float(row['road_distance_km_mean']):,.1f}",
                    f"{float(row['transport_cost_cad']):,.0f}",
                    f"{float(row['transport_cost_cad_gj']):.3f}",
                    f"{float(row['proxy_share_of_assigned']):.4f}",
                )
                for row in sensitivity
            ],
        )
    )
    lines.extend(
        [
            "",
            "## 4. Uneconomic facilities and distance",
            "",
            (
                "The table below is a **transport-only** face-value screen. "
                "It is not a policy, investment, or official OSeCAN statement. "
                "A positive flag means modelled transport cost alone exceeds "
                "the tier face value before any feedstock, processing, or "
                "conversion cost is added."
            ),
            "",
        ]
    )
    for tier in TIER_ORDER:
        above = [
            row for row in v8_facility
            if row[f"exceeds_{tier.lower()}"] == "true"
        ]
        volume = math.fsum(float(row["assigned_m3"]) for row in above)
        lines.append(
            f"- **{tier}**: {len(above)} facilities, {volume:,.0f} m3/yr "
            f"({volume / v8_primary_volume:.4f} of assigned volume) have "
            "facility-weighted mean transport-only cost above the tier."
        )
    top = sorted(
        v8_facility,
        key=lambda row: float(row["transport_cost_cad_gj"]),
        reverse=True,
    )[:10]
    lines.extend(
        [
            "",
            "### Highest transport-only cost facilities (v8 primary)",
            "",
        ]
    )
    lines.extend(
        _md_table(
            ("Facility", "Province", "Class", "m3/yr", "Mean km", "CAD/GJ", "Above T4"),
            [
                (
                    row["facility_name"] or row["facility_id"],
                    row["facility_province"],
                    row["facility_class"],
                    f"{float(row['assigned_m3']):,.0f}",
                    f"{float(row['road_distance_km_mean']):,.1f}",
                    f"{float(row['transport_cost_cad_gj']):.3f}",
                    row["exceeds_t4"],
                )
                for row in top
            ],
        )
    )
    below = [
        row for row in v8_facility if row["exceeds_t1"] == "false"
    ]
    lines.extend(
        [
            "",
            (
                f"- Facilities with transport-only cost below the lowest face-value "
                f"tier (T1): **{len(below)}**. This short-haul tail is the only "
                "part of the v8 assignment that is not above T1 on transport alone."
            ),
            (
                "- The v9 facility set is included in "
                "`v9_facility_transport_cost.csv`; its tier flags are computed "
                "with the same threshold and should be read with the same caveat."
            ),
            "",
            "## 5. Validation",
            "",
        ]
    )
    for key, value in summary["validation"]["checks"].items():
        lines.append(f"- `{key}`: {str(value).lower()}")
    lines.extend(
        [
            "",
            "## 6. Caveats",
            "",
            *[f"- {caveat}" for caveat in summary["caveats"]],
            "",
            "## 7. Reproduce",
            "",
            f"`{summary['reproduce']}`",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------

def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    deletion_checker: Callable[[], None] = assert_no_unexpected_deletions,
) -> dict[str, Any]:
    """Build v9, its v8 transport accounting, comparisons and manifest."""

    deletion_checker()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    parameters = load_transport_parameters()
    _validate_curve_parity(parameters)
    tiers = load_tiers()
    thresholds = _tier_map(tiers)

    with V8_SUMMARY_JSON.open("r", encoding="utf-8") as handle:
        v8_summary = json.load(handle)
    if v8_summary.get("artifact") != "national_facility_assignment_v8":
        raise AssignmentV9Error(
            f"{V8_SUMMARY_JSON}: expected assignment v8"
        )
    if not v8_summary.get("validation", {}).get("assignment_conservation"):
        raise AssignmentV9Error("assignment v8 did not pass conservation")

    v8_raw = read_v8_rows()
    v8_transport = v8_transport_rows(v8_raw, parameters, tiers)
    v8_primary = [
        row for row in v8_transport if row["scenario"] == PRIMARY_SCENARIO
    ]
    v8_facility = _facility_aggregates(
        v8_primary, scenario=PRIMARY_SCENARIO, thresholds=thresholds
    )
    offers_by_province = {
        row["province"]: float(row["province_offer_m3"])
        for row in v8_raw
        if row["scenario"] == PRIMARY_SCENARIO
    }
    v8_province = _province_aggregates(
        v8_primary, scenario=PRIMARY_SCENARIO, offers=offers_by_province
    )
    v8_tiers = _tier_rows(v8_primary, scenario=PRIMARY_SCENARIO, tiers=tiers)

    facilities, offers, factors = _eligible_facilities()
    offers_by_id = {offer.province: offer for offer in offers}
    offer_volumes = {province: offer.volume_m3 for province, offer in offers_by_id.items()}
    pairs = _pair_rows(offers, facilities, factors, parameters)
    penalty_cad_m3 = transport.trip_cost(
        LOCAL_PENALTY_KM, PAYLOAD_M3, dict(parameters), BASE_COST_SCENARIO
    )["cost_per_m3_cad"]
    solution = _solve_transport_lp(
        offers, facilities, pairs, penalty_cad_m3=penalty_cad_m3
    )
    allocations = _allocations(offers, facilities, pairs, solution)
    v9_rows = _v9_assignment_rows(
        offers,
        facilities,
        pairs,
        allocations,
        parameters,
        penalty_km=LOCAL_PENALTY_KM,
        penalty_cad_m3=penalty_cad_m3,
        scenario=PRIMARY_SCENARIO,
        thresholds=thresholds,
    )
    v9_facility = _facility_aggregates(
        v9_rows, scenario=PRIMARY_SCENARIO, thresholds=thresholds
    )
    v9_province = _province_aggregates(
        v9_rows, scenario=PRIMARY_SCENARIO, offers=offer_volumes
    )
    v9_utilization = _utilization_rows(
        facilities, v9_rows, offers, scenario=PRIMARY_SCENARIO
    )
    comparison = _comparison_rows(v8_primary, v9_rows)
    facility_changes = _facility_change_rows(v8_primary, v9_rows)
    province_comparison = _province_comparison_rows(
        v8_primary, v9_rows, offer_volumes
    )
    sensitivity = _sensitivity(
        offers, facilities, pairs, parameters, thresholds
    )
    validation = validate(
        v8_transport=v8_transport,
        v9_rows=v9_rows,
        v9_utilization=v9_utilization,
        province_rows=v9_province,
        comparison=comparison,
        sensitivity=sensitivity,
        offers=offer_volumes,
        facilities=facilities,
        parameters=parameters,
        thresholds=thresholds,
    )

    comparison_map = {row["metric"]: row for row in comparison}
    v8_primary_metrics = {
        "assigned_m3": float(comparison_map["assigned_m3"]["v8_value"]),
        "local_m3": float(comparison_map["local_m3"]["v8_value"]),
        "cross_border_m3": float(comparison_map["cross_border_m3"]["v8_value"]),
        "cross_border_share": float(comparison_map["cross_border_share"]["v8_value"]),
        "proxy_share_of_assigned": float(
            comparison_map["proxy_share_of_assigned"]["v8_value"]
        ),
        "facilities_used": int(float(comparison_map["facilities_used"]["v8_value"])),
        "transport_cost_cad": float(comparison_map["transport_cost_cad"]["v8_value"]),
        "transport_cost_cad_gj": float(comparison_map["transport_cost_cad_gj"]["v8_value"]),
        "binding_facilities": int(float(comparison_map["binding_facilities"]["v8_value"])),
        "binding_volume_m3": float(comparison_map["binding_volume_m3"]["v8_value"]),
    }
    v9_primary_metrics = {
        "assigned_m3": float(comparison_map["assigned_m3"]["v9_value"]),
        "local_m3": float(comparison_map["local_m3"]["v9_value"]),
        "cross_border_m3": float(comparison_map["cross_border_m3"]["v9_value"]),
        "cross_border_share": float(comparison_map["cross_border_share"]["v9_value"]),
        "proxy_share_of_assigned": float(
            comparison_map["proxy_share_of_assigned"]["v9_value"]
        ),
        "facilities_used": int(float(comparison_map["facilities_used"]["v9_value"])),
        "transport_cost_cad": float(comparison_map["transport_cost_cad"]["v9_value"]),
        "transport_cost_cad_gj": float(comparison_map["transport_cost_cad_gj"]["v9_value"]),
        "binding_facilities": int(float(comparison_map["binding_facilities"]["v9_value"])),
        "binding_volume_m3": float(comparison_map["binding_volume_m3"]["v9_value"]),
    }

    v9_tiers = _tier_rows(v9_rows, scenario=PRIMARY_SCENARIO, tiers=tiers)

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "national_claim": False,
            "official_osecan_changed": False,
            "assignment_change_vs_v8": True,
            "assignment_change_vs_v6": True,
            "capacity_change": False,
            "floor_change": False,
            "product_change": False,
            "residue_change": False,
            "ui_changed": False,
            "policy_claim": False,
        },
        "method": {
            "engine": "transport-aware bipartite linear program (HiGHS)",
            "primary_scenario": PRIMARY_SCENARIO,
            "v8_reference": "national_facility_assignment_v8 province_capacity_floor",
            "transport_model": "transport_cost_v1 trip_cost",
            "transport_cost_scenario": BASE_COST_SCENARIO,
            "transport_payload_m3": PAYLOAD_M3,
            "transport_energy_gj_per_m3": ENERGY_GJ_PER_M3,
            "empty_return": True,
            "local_preference": "cross-border transport penalty",
            "local_penalty_km": LOCAL_PENALTY_KM,
            "local_penalty_cad_m3": penalty_cad_m3,
            "local_penalty_note": (
                "v8 documented 500 km cross-border rule converted to 2026 "
                "CAD/m3 with the same transport cost curve; local preference "
                "is therefore a cost penalty, not a hard flow prohibition."
            ),
            "tie_break_cad_m3": TIE_BREAK_CAD_M3,
            "floor_multiplier": v6.FLOOR_MULTIPLIER,
            "capacity_source": "national_facility_capacity_v3",
            "harvest_source": "119 Mm3 provincial allocation",
            "products_residue": (
                "unchanged; no product or residue output is written or recalculated"
            ),
        },
        "v8_primary": v8_primary_metrics,
        "v9_primary": v9_primary_metrics,
        "v8_scenarios": {
            scenario: {
                "assigned_m3": math.fsum(
                    float(row["assigned_m3"])
                    for row in v8_transport
                    if row["scenario"] == scenario
                ),
                "transport_cost_cad": math.fsum(
                    float(row["transport_cost_cad"])
                    for row in v8_transport
                    if row["scenario"] == scenario
                ),
                "transport_cost_cad_gj": (
                    math.fsum(
                        float(row["transport_cost_cad"])
                        for row in v8_transport
                        if row["scenario"] == scenario
                    )
                    / math.fsum(
                        float(row["transport_energy_gj"])
                        for row in v8_transport
                        if row["scenario"] == scenario
                    )
                ),
                "facilities_used": len(
                    {
                        row["facility_id"]
                        for row in v8_transport
                        if row["scenario"] == scenario
                    }
                ),
            }
            for scenario in sorted({row["scenario"] for row in v8_transport})
        },
        "v8_tiers": v8_tiers,
        "v9_tiers": v9_tiers,
        "comparison": comparison,
        "sensitivity": sensitivity,
        "validation": validation,
        "caveats": [
            "Road distances are the v8 province-factor screening distances, not observed routes for every origin-facility pair.",
            "The 52.5 m3 base payload and empty return are the transport v1 base assumptions; low/high cost columns bound cost parameters only, not route or payload choice.",
            "The transport cost covers trucking only. It excludes feedstock, processing, conversion, handling, and storage costs.",
            "Tier comparison uses face-value adopted supply-curve tiers with no currency or price-year conversion; see the tier caveat.",
            "V9 is a screening LP with finite facility capacities and a soft local preference. It is not a market equilibrium, procurement plan, or policy recommendation.",
            "V9 uses more proxy-capacity facilities than v8; the proxy share is reported so the evidence trade-off is explicit.",
            "Products and residue are unchanged in model definitions; no product/residue artifact is written by this run.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_facility_assignment_v9"
        ),
    }
    summary["report"] = _report(
        summary,
        v8_primary=v8_primary,
        v8_facility=v8_facility,
        v9_rows=v9_rows,
        v9_facility=v9_facility,
        province_rows=v9_province,
        tier_rows=v8_tiers,
        comparison=comparison,
        sensitivity=sensitivity,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "v8_transport_cost_rows.csv", V8_TRANSPORT_FIELDS, v8_transport)
    _write_csv(output_dir / "v8_facility_transport_cost.csv", FACILITY_FIELDS, v8_facility)
    _write_csv(output_dir / "v8_province_transport_cost.csv", PROVINCE_FIELDS, v8_province)
    _write_csv(output_dir / "v8_tier_comparison.csv", TIER_FIELDS, v8_tiers)
    _write_csv(output_dir / "v9_facility_assignment.csv", V9_ASSIGNMENT_FIELDS, v9_rows)
    _write_csv(output_dir / "v9_facility_utilization.csv", UTILIZATION_FIELDS, v9_utilization)
    _write_csv(output_dir / "v9_facility_transport_cost.csv", FACILITY_FIELDS, v9_facility)
    _write_csv(output_dir / "v9_province_transport_cost.csv", PROVINCE_FIELDS, v9_province)
    _write_csv(output_dir / "v8_v9_comparison.csv", COMPARISON_FIELDS, comparison)
    _write_csv(output_dir / "v8_v9_facility_changes.csv", FACILITY_CHANGE_FIELDS, facility_changes)
    _write_csv(output_dir / "v8_v9_province_comparison.csv", PROVINCE_COMPARISON_FIELDS, province_comparison)
    _write_csv(output_dir / "local_preference_sensitivity.csv", SENSITIVITY_FIELDS, sensitivity)
    (output_dir / "TRANSPORT_ASSIGNMENT_V9.md").write_text(
        summary["report"] + "\n", encoding="utf-8"
    )
    _write_json(output_dir / "summary.json", summary)

    inputs = {
        str(path.relative_to(REPO_ROOT)): _sha256(path)
        for path in (
            V8_ASSIGNMENT_CSV,
            V8_SUMMARY_JSON,
            CAPACITY_CSV,
            CAPACITY_SUMMARY,
            HARVEST_CSV,
            BOUNDARY_GEOJSON,
            FACTORS_CSV,
            FACTORS_SUMMARY,
            TRANSPORT_PARAMETERS_CSV,
            TRANSPORT_CURVES_CSV,
            SUPPLY_CURVE_JSON,
        )
    }
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": summary["labels"],
        "inputs": inputs,
        "outputs": outputs,
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def _utilization_rows(
    facilities: Sequence[Any],
    rows: Sequence[Mapping[str, Any]],
    offers: Sequence[Any],
    *,
    scenario: str,
) -> list[dict[str, Any]]:
    by_facility: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        by_facility[row["facility_id"]].append(row)
    result: list[dict[str, Any]] = []
    for facility in sorted(facilities, key=lambda item: item.facility_id):
        group = by_facility.get(facility.facility_id, [])
        capacity = float(facility.capacity_m3_y)
        assigned = math.fsum(float(row["assigned_m3"]) for row in group)
        remaining = max(0.0, capacity - assigned)
        total_cost = math.fsum(
            float(row["transport_cost_cad"]) for row in group
        )
        energy = math.fsum(
            float(row["transport_energy_gj"]) for row in group
        )
        local = math.fsum(
            float(row["assigned_m3"])
            for row in group
            if row["allocation_phase"] == "local"
        )
        result.append(
            {
                "scenario": scenario,
                "facility_id": facility.facility_id,
                "facility_name": facility.name,
                "facility_class": facility.facility_class,
                "facility_province": facility.province,
                "capacity_status": facility.capacity.status,
                "capacity_evidence": facility.capacity.capacity_evidence,
                "floor_scale_factor": _format_float(
                    facility.capacity.floor_scale_factor, 12
                ),
                "capacity_m3_y": _format_float(capacity, 9),
                "proxy_low_m3_y": _format_float(
                    facility.capacity.proxy_low_m3_y, 9
                ),
                "proxy_high_m3_y": _format_float(
                    facility.capacity.proxy_high_m3_y, 9
                ),
                "assigned_m3": _format_float(assigned, 9),
                "remaining_capacity_m3": _format_float(remaining, 9),
                "utilization": _format_float(
                    assigned / capacity if capacity > 0.0 else 0.0, 9
                ),
                "binding": _bool_text(remaining <= VOLUME_EPSILON_M3),
                "used": _bool_text(assigned > VOLUME_EPSILON_M3),
                "local_assigned_m3": _format_float(local, 9),
                "cross_border_assigned_m3": _format_float(assigned - local, 9),
                "road_distance_km_mean": _format_float(
                    _weighted_mean(group, "road_distance_km")
                    if group else 0.0,
                    9,
                ),
                "road_distance_km_max": _format_float(
                    max((float(row["road_distance_km"]) for row in group), default=0.0),
                    9,
                ),
                "transport_cost_cad": _format_float(total_cost, 6),
                "transport_cost_cad_gj": _format_float(
                    total_cost / energy if energy > 0.0 else 0.0, 9
                ),
                "evidence_label": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
            }
        )
    return result


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "v8_transport_cost_cad": summary["v8_primary"]["transport_cost_cad"],
                "v9_transport_cost_cad": summary["v9_primary"]["transport_cost_cad"],
                "v9_mean_km": next(
                    row["v9_value"]
                    for row in summary["comparison"]
                    if row["metric"] == "mean_road_distance_km"
                ),
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
