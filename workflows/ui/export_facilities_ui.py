"""Export the read-only national facility ledger for the static portal.

The Facilities view is deliberately single-purpose: it shows the 689-record
inventory/capacity ledger, the 100/119/150 Mm3 local-first assignments, and the
province x class capacity/assignment table.  This module is the only bridge
from the committed CSVs to ``ui/public/data/facilities``.

Every number is read from the source artifacts at export time.  The exporter
fails loudly when an upstream count, capacity total, assignment total, or label
no longer matches the committed contract.

Usage:
    .venv/bin/python workflows/ui/export_facilities_ui.py
    .venv/bin/python workflows/ui/export_facilities_ui.py --check
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "facilities/2"
ARTIFACT = "ui_facilities_v2"
OUTPUT_DIR = Path("ui/public/data/facilities")
OUTPUT_FILE = "facilities.json"
MANIFEST_FILE = "manifest.json"

CAPACITY_DIR = Path("data/derived/national/facilities/national_facility_capacity_v3")
INVENTORY_DIR = Path("data/derived/national/facilities/national_facility_inventory_v1")
ASSIGNMENT_DIR = Path("data/derived/national/facilities/national_facility_assignment_v10")
CHAIN_DIR = Path("data/derived/national/facilities/national_facility_chain_scenarios_v9")

V10_CASE_SUMMARY_CSV = ASSIGNMENT_DIR / "v10_case_summary.csv"
V10_SUMMARY_JSON = ASSIGNMENT_DIR / "summary.json"
V10_MANIFEST = ASSIGNMENT_DIR / "manifest.json"

CHAIN_SUMMARY_CSV = CHAIN_DIR / "scenario_summary.csv"
CHAIN_SUMMARY_JSON = CHAIN_DIR / "summary.json"
CHAIN_MANIFEST = CHAIN_DIR / "manifest.json"
CHAIN_INVARIANCE_CSV = CHAIN_DIR / "v5_v6_product_residue_invariance.csv"

V7_CHAIN_SUMMARY_CSV = (
    Path("data/derived/national/facilities/national_facility_chain_scenarios_v7")
    / "scenario_summary.csv"
)

PRODUCT_CHAIN_DIR = Path("data/derived/national/facilities/national_product_chain_v5")
PRODUCT_CHAIN_SUMMARY = PRODUCT_CHAIN_DIR / "summary.json"
PRODUCT_CHAIN_MANIFEST = PRODUCT_CHAIN_DIR / "manifest.json"
E1_COEFFICIENTS = PRODUCT_CHAIN_DIR / "e1_coefficient_replacement.csv"
E2C_EVIDENCE = PRODUCT_CHAIN_DIR / "e2c_density_evidence.csv"

RESIDUE_DIR = Path("data/derived/national/facilities/facility_residue_supply_v9")
RESIDUE_NATIONAL_CSV = RESIDUE_DIR / "national_residue_summary.csv"
RESIDUE_SUMMARY = RESIDUE_DIR / "summary.json"
RESIDUE_MANIFEST = RESIDUE_DIR / "manifest.json"

CURVE_DIR = Path("data/derived/national/facilities/national_residue_curve_v9")
CURVE_NATIONAL_CSV = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
CURVE_MANIFEST = CURVE_DIR / "manifest.json"

HANDOFF_DIR = Path("data/derived/national/facilities/forestry_osecan_handoff_v10")
HANDOFF_TIERS_CSV = HANDOFF_DIR / "tier_positions_v3_2_vs_v4.csv"
HANDOFF_SUMMARY = HANDOFF_DIR / "summary.json"
HANDOFF_MANIFEST = HANDOFF_DIR / "manifest.json"

SCENARIOS_DIR = Path("data/derived/national/facilities/forestry_osecan_scenarios_v9")
SCENARIOS_TIERS_CSV = SCENARIOS_DIR / "tier_positions_v3_2_vs_v4.csv"
SCENARIOS_SUMMARY = SCENARIOS_DIR / "summary.json"
SCENARIOS_MANIFEST = SCENARIOS_DIR / "manifest.json"

CAPACITY_CSV = CAPACITY_DIR / "national_facility_capacity_v3.csv"
CAPACITY_SUMMARY = CAPACITY_DIR / "summary.json"
CAPACITY_MANIFEST = CAPACITY_DIR / "manifest.json"
INVENTORY_CSV = INVENTORY_DIR / "national_facilities.csv"

EXPECTED_SCENARIOS = ("100m", "119m", "150m")
DEFAULT_SCENARIO = "119m"

CLASS_ORDER = ("SAW", "PULP", "PANEL", "PELLET", "BIO", "OTHER")
CLASS_LABELS = {
    "SAW": "Sawmill",
    "PULP": "Pulp / paper",
    "PANEL": "Panel",
    "PELLET": "Pellet",
    "BIO": "Bioenergy",
    "OTHER": "Other",
}
CLASS_COLORS = {
    "SAW": "#f2b45c",
    "PULP": "#63a7b8",
    "PANEL": "#b58aa6",
    "PELLET": "#e06b3c",
    "BIO": "#8fb37a",
    "OTHER": "#b9b09b",
}
PROVINCE_LABELS = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}

LABELS = {
    "evidence": "model_estimate",
    "observed": False,
    "policy_grade": False,
    "ranking": "none",
    "official_osecan": False,
    "read_only": True,
}

CAVEATS = {
    "capacity": (
        "Published capacity mixes 2015 BC Mills DB infeed proxies, 2026 Quebec "
        "permitted input volume, 2025 finished-pellet bulk volume converted at "
        "650 kg/m3, and converted company sawn output. The bases are not "
        "interchangeable. A permit is a ceiling, not observed throughput."
    ),
    "proxy": (
        "Proxy capacity is a class-median placeholder for blank relevant-class "
        "records. It is not a facility measurement, not observed capacity, and "
        "not evidence of provincial self-sufficiency."
    ),
    "assignment": (
        "Assigned volume is the assignment-v10 node-to-facility transport-aware "
        "planning allocation, not a legal AAC, sustainable-supply determination, "
        "or observed harvest outcome. The eligible facility set, capacity-v3 "
        "bounds and the 1.2x proxy floor are unchanged across the three harvest "
        "cases. Cross-border volume is a screening transport-allocation result, "
        "not a trade forecast."
    ),
    "transport": (
        "Transport cost is trucking only (transport cost v1 applied to "
        "haversine x province-factor screening distances). It excludes feedstock, "
        "processing, conversion, handling, storage, border, ferry and winter-road "
        "costs. It is not an OSeCAN input, not observed, and not policy grade."
    ),
    "utilization": (
        "Table utilization is assigned volume divided by eligible v10 capacity: "
        "located, operating records after the unchanged 1.2x proxy floor. "
        "It is not observed operating utilization and is not a performance "
        "ranking."
    ),
    "status": (
        "Most operating statuses are unknown. A published capacity record does "
        "not prove current operation."
    ),
    "location": (
        "Coordinates are plant or city-centroid locations, not verified plant-gate "
        "geometry. Records without coordinates are retained in the table and are "
        "not mapped; no location is invented."
    ),
    "no_ranking": (
        "This ledger is evidence coverage and accounting only. No facility, "
        "province, or class ranking is performed or implied."
    ),
    "no_osecan": (
        "Read-only. This view is outside the LP and public OSeCAN surfaces and "
        "contains no run or solver controls."
    ),
    "national_chain": (
        "Frozen v5.1 national chain. Assignment v10 node transport-aware flows at "
        "100/119/150 Mm³; capacity v3 and the 1.2x floor are unchanged. E1 "
        "measured coefficients for BC/QC/NB/YT and E2c imputed coefficients for "
        "nine jurisdictions; donor product coefficients = 0. Products and residue "
        "are invariant to assignment at fixed harvest; facility distribution and "
        "truck transport cost are not. Transport cost is not an OSeCAN input. "
        "Harvest 0.60 is a reference recovery scenario, not a calibrated factor. "
        "model_estimate · observed=false · policy_grade=false. No official OSeCAN "
        "change and no new proxy capacity."
    ),
}


class FacilityExportError(SystemExit):
    """Raised when a source artifact breaks the facility export contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise FacilityExportError("export_facilities_ui: " + message)


def _read_csv(path: Path) -> list[dict[str, str]]:
    _require(path.is_file(), f"missing source CSV: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_json(path: Path) -> dict[str, Any]:
    _require(path.is_file(), f"missing source JSON: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise FacilityExportError(f"export_facilities_ui: invalid JSON in {path}: {error}") from error


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: str | None, field: str, context: str, *, required: bool = False) -> float | None:
    if value is None or not str(value).strip():
        _require(not required, f"missing {field} in {context}")
        return None
    try:
        number = float(value)
    except ValueError as error:
        raise FacilityExportError(
            f"export_facilities_ui: non-numeric {field} in {context}: {value!r}"
        ) from error
    _require(math.isfinite(number), f"non-finite {field} in {context}")
    return number


def _integer(value: str | None, field: str, context: str) -> int:
    number = _number(value, field, context, required=True)
    assert number is not None
    _require(number.is_integer(), f"non-integer {field} in {context}")
    return int(number)


def _query(root: Path, relative: Path) -> Path:
    return root / relative


def _source_record(root: Path, relative: Path, role: str, artifact: str | None = None) -> dict[str, Any]:
    path = _query(root, relative)
    _require(path.is_file(), f"missing provenance file: {relative}")
    return {
        "path": relative.as_posix(),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
        **({"artifact": artifact} if artifact else {}),
    }


def _scenario_label(scenario_id: str, harvest_m3: float) -> str:
    return f"{scenario_id.removesuffix('m')} Mm³ harvest · {harvest_m3 / 1e6:.3f} Mm³ assigned"


def _base_rows(capacity_rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, Any]]:
    base: dict[tuple[str, str], dict[str, Any]] = {}
    for row in capacity_rows:
        key = (row["province"], row["facility_class"])
        if key not in base:
            base[key] = {
                "province": row["province"],
                "province_label": PROVINCE_LABELS[row["province"]],
                "facility_class": row["facility_class"],
                "class_label": CLASS_LABELS[row["facility_class"]],
                "record_count": 0,
                "published_count": 0,
                "proxy_count": 0,
                "not_published_count": 0,
                "mapped_count": 0,
                "unlocated_count": 0,
                "published_capacity_m3_y": 0.0,
                "proxy_capacity_m3_y": 0.0,
                "total_capacity_m3_y": 0.0,
            }
        bucket = base[key]
        bucket["record_count"] += 1
        status = row["capacity_status"]
        if status == "published":
            bucket["published_count"] += 1
        elif status == "proxy":
            bucket["proxy_count"] += 1
        elif status == "not_published":
            bucket["not_published_count"] += 1
        capacity = _number(row["capacity_m3_y"], "capacity_m3_y", row["facility_id"]) or 0.0
        if status == "published":
            bucket["published_capacity_m3_y"] += capacity
        elif status == "proxy":
            bucket["proxy_capacity_m3_y"] += capacity
        bucket["total_capacity_m3_y"] += capacity
        if row["lat"].strip() and row["lon"].strip():
            bucket["mapped_count"] += 1
        else:
            bucket["unlocated_count"] += 1
    return base


TRANSPORT_FIELDS = (
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "transport_cost_cad_gj",
    "mean_road_distance_km",
    "median_road_distance_km",
    "p90_road_distance_km",
    "max_road_distance_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "cross_border_share",
    "proxy_share_of_assigned",
)


def _case_path(directory: Path, stem: str, case: str) -> Path:
    if case == DEFAULT_SCENARIO:
        return directory / f"{stem}.csv"
    return directory / f"{stem}_{case}.csv"


def _build_scenarios(
    root: Path,
    capacity_rows: list[dict[str, str]],
    base: dict[tuple[str, str], dict[str, Any]],
    chain_summary_rows: list[dict[str, str]],
    case_summaries: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    capacity_by_id = {row["facility_id"]: row for row in capacity_rows}
    summary_by_id = {row["scenario"]: row for row in chain_summary_rows}
    _require(set(summary_by_id) == set(EXPECTED_SCENARIOS), "chain scenario summary must cover 100m/119m/150m")
    case_summary_by_id = {row["scenario"]: row for row in case_summaries}
    _require(set(case_summary_by_id) == set(EXPECTED_SCENARIOS), "assignment v10 summary must cover 100m/119m/150m")

    scenarios: list[dict[str, Any]] = []
    for scenario_id in EXPECTED_SCENARIOS:
        utilization_rows = _read_csv(
            _query(root, _case_path(ASSIGNMENT_DIR, "v10_facility_utilization", scenario_id))
        )
        province_rows = _read_csv(
            _query(root, _case_path(ASSIGNMENT_DIR, "v10_province_summary", scenario_id))
        )
        case_summary = case_summary_by_id[scenario_id]
        assignments: dict[str, dict[str, str]] = {}
        for utilization_row in utilization_rows:
            facility_id = utilization_row["facility_id"]
            _require(
                facility_id not in assignments,
                f"duplicate v10 utilization row for {facility_id} in {scenario_id}",
            )
            assignments[facility_id] = utilization_row
        _require(bool(assignments), f"no v10 utilization rows for {scenario_id}")

        rows = {
            key: {
                **{name: value for name, value in bucket.items()},
                "assigned_m3_y": 0.0,
                "local_assigned_m3_y": 0.0,
                "cross_border_m3_y": 0.0,
                "published_assigned_m3_y": 0.0,
                "proxy_assigned_m3_y": 0.0,
                "assignment_capacity_m3_y": 0.0,
                "eligible_capacity_m3_y": 0.0,
                "assigned_facility_count": 0,
                "utilization": None,
            }
            for key, bucket in base.items()
        }
        assigned_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        for facility_id, utilization_row in assignments.items():
            _require(
                facility_id in capacity_by_id,
                f"v10 assignment references unknown facility {facility_id}",
            )
            capacity_row = capacity_by_id[facility_id]
            key = (capacity_row["province"], capacity_row["facility_class"])
            _require(key in rows, f"assignment class/province missing from capacity rows: {key}")
            assigned = _number(
                utilization_row["assigned_m3"], "assigned_m3", facility_id, required=True
            )
            assert assigned is not None
            _require(assigned >= 0, f"negative assigned volume for {facility_id}")
            capacity = _number(
                utilization_row["capacity_m3_y"], "capacity_m3_y", facility_id, required=True
            )
            assert capacity is not None
            _require(capacity >= 0, f"negative v10 eligible capacity for {facility_id}")
            if assigned > 0:
                _require(capacity > 0, f"assigned volume without v10 capacity for {facility_id}")
                _require(
                    assigned <= capacity * 1.000001,
                    f"assigned volume exceeds v10 capacity for {facility_id}",
                )
            row = rows[key]
            row["assigned_m3_y"] += assigned
            row["local_assigned_m3_y"] += _number(
                utilization_row["local_assigned_m3"], "local_assigned_m3", facility_id
            ) or 0.0
            row["cross_border_m3_y"] += _number(
                utilization_row["cross_border_assigned_m3"],
                "cross_border_assigned_m3",
                facility_id,
            ) or 0.0
            if utilization_row["capacity_status"].startswith("published"):
                row["published_assigned_m3_y"] += assigned
            else:
                row["proxy_assigned_m3_y"] += assigned
            row["assignment_capacity_m3_y"] += capacity
            row["eligible_capacity_m3_y"] += capacity
            if utilization_row.get("used") == "true" or assigned > 0:
                assigned_ids[key].add(facility_id)

        total_records = 0
        total_assigned = 0.0
        local_assigned = 0.0
        cross_border = 0.0
        total_eligible = 0.0
        for key, row in rows.items():
            row["assigned_facility_count"] = len(assigned_ids[key])
            row["utilization"] = (
                row["assigned_m3_y"] / row["eligible_capacity_m3_y"]
                if row["eligible_capacity_m3_y"] > 0
                else None
            )
            _require(
                row["utilization"] is None or row["utilization"] <= 1.000001,
                f"{scenario_id} utilization above 100% for {key}",
            )
            total_records += row["record_count"]
            total_assigned += row["assigned_m3_y"]
            local_assigned += row["local_assigned_m3_y"]
            cross_border += row["cross_border_m3_y"]
            total_eligible += row["eligible_capacity_m3_y"]

        _require(total_records == len(capacity_rows), f"{scenario_id} table rows do not conserve records")
        expected_assigned = _number(
            case_summary["assigned_m3"], "assigned_m3", scenario_id, required=True
        )
        assert expected_assigned is not None
        _require(
            abs(total_assigned - expected_assigned) <= 1e-3,
            f"{scenario_id} assigned total {total_assigned} does not match assignment v10 {expected_assigned}",
        )
        expected_harvest = _number(
            case_summary["harvest_case_m3"], "harvest_case_m3", scenario_id, required=True
        )
        assert expected_harvest is not None
        expected_facilities = _integer(
            case_summary["facilities_used"], "facilities_used", scenario_id
        )
        _require(
            len(assigned_ids.keys()) > 0 and sum(len(ids) for ids in assigned_ids.values()) == expected_facilities,
            f"{scenario_id} facility count does not match assignment v10 {expected_facilities}",
        )
        expected_used = _integer(
            case_summary["facilities_used"], "facilities_used", scenario_id
        )
        _require(
            sum(1 for row in assignments.values() if row.get("used") == "true") == expected_used,
            f"{scenario_id} used facility rows do not match assignment v10 {expected_used}",
        )
        _require(total_eligible > 0, f"{scenario_id} eligible v10 capacity is not positive")
        expected_utilization = total_assigned / total_eligible
        chain_summary = summary_by_id[scenario_id]
        chain_assigned = _number(
            chain_summary["assigned_m3"], "assigned_m3", scenario_id, required=True
        )
        assert chain_assigned is not None
        _require(
            abs(total_assigned - chain_assigned) <= 1e-3,
            f"{scenario_id} assigned total does not match chain v9 summary",
        )
        _require(
            _integer(chain_summary["facilities_used"], "facilities_used", scenario_id)
            == expected_facilities,
            f"{scenario_id} chain v9 facility count does not match assignment v10",
        )
        province_assigned = sum(
            _number(row["assigned_m3"], "assigned_m3", row["province"], required=True) or 0.0
            for row in province_rows
        )
        province_capacity = sum(
            _number(row["capacity_m3_y"], "capacity_m3_y", row["province"], required=True) or 0.0
            for row in province_rows
        )
        _require(
            abs(province_assigned - total_assigned) <= 1e-3,
            f"{scenario_id} province summary assigned volume does not conserve",
        )
        _require(
            abs(province_capacity - total_eligible) <= 1e-3,
            f"{scenario_id} province summary eligible capacity does not conserve",
        )

        transport = {
            field: _number(case_summary[field], field, scenario_id, required=True)
            for field in TRANSPORT_FIELDS
        }
        for field, value in transport.items():
            assert value is not None
            _require(math.isfinite(value) and value >= 0, f"{scenario_id} has invalid {field}")
        _require(
            transport["transport_cost_cad"] > 0 and transport["mean_road_distance_km"] > 0,
            f"{scenario_id} transport cost/distance is not positive",
        )
        for field in ("share_ge_150_km", "share_ge_300_km", "cross_border_share", "proxy_share_of_assigned"):
            assert transport[field] is not None
            _require(0 <= transport[field] <= 1, f"{scenario_id} {field} is outside [0, 1]")

        rows_sorted = sorted(
            rows.values(),
            key=lambda row: (row["province_label"], CLASS_ORDER.index(row["facility_class"])),
        )
        scenarios.append(
            {
                "id": scenario_id,
                "label": _scenario_label(scenario_id, expected_harvest),
                "harvest_m3": expected_harvest,
                "assigned_m3": total_assigned,
                "local_assigned_m3": local_assigned,
                "cross_border_m3": cross_border,
                "unassigned_m3": max(0.0, expected_harvest - total_assigned),
                "capacity_utilization": expected_utilization,
                "facilities_used": expected_facilities,
                "capacity_case": chain_summary.get("capacity_case") or "province_capacity_floor",
                "source_artifact": "national_facility_assignment_v10",
                "transport": {field: float(value) for field, value in transport.items()},
                **{field: float(value) for field, value in transport.items()},
                "rows": rows_sorted,
            }
        )

    primary = next(item for item in scenarios if item["id"] == DEFAULT_SCENARIO)
    return scenarios, [
        {
            "id": DEFAULT_SCENARIO,
            "artifact": "national_facility_assignment_v10",
            "capacity_case": primary["capacity_case"],
            "facilities": primary["facilities_used"],
            "assigned_m3": primary["assigned_m3"],
            "transport_cost_cad": primary["transport_cost_cad"],
            "mean_road_distance_km": primary["mean_road_distance_km"],
        }
    ]


def _require_model_labels(labels: dict[str, Any], context: str) -> None:
    _require(
        labels.get("model_estimate") is True
        or labels.get("evidence_label") == "model_estimate",
        f"{context} must be a model estimate",
    )
    _require(labels.get("observed") is False, f"{context} observed must be false")
    _require(labels.get("policy_grade") is False, f"{context} policy_grade must be false")
    _require(labels.get("national_claim") is False, f"{context} national_claim must be false")
    _require(
        labels.get("official_osecan_changed", False) is False,
        f"{context} must not change official OSeCAN",
    )


def _require_artifact(document: dict[str, Any], artifact: str, version: str, context: str) -> None:
    _require(document.get("artifact") == artifact, f"{context} artifact is not {artifact}")
    _require(document.get("artifact_version") == version, f"{context} artifact_version is not {version}")
    if "status" in document:
        _require(
            document.get("status") in {"pass", "complete"},
            f"{context} status is not pass/complete",
        )


def _close(actual: float, expected: float, tolerance: float = 1e-6) -> bool:
    return abs(actual - expected) <= tolerance * max(1.0, abs(expected))


def _curve_national_row(
    rows: list[dict[str, str]],
    residue_type: str,
    residue_class: str,
    stream: str,
    case: str,
) -> dict[str, str]:
    matches = [
        row
        for row in rows
        if row["geography_level"] == "national"
        and row["residue_type"] == residue_type
        and row["residue_class"] == residue_class
        and row["stream"] == stream
        and row["availability_case"] == case
    ]
    _require(len(matches) == 1, f"expected one national curve row for {residue_type}/{case}, got {len(matches)}")
    return matches[0]


def _build_national_chain(root: Path) -> dict[str, Any]:
    chain_summary = _load_json(_query(root, CHAIN_SUMMARY_JSON))
    chain_manifest = _load_json(_query(root, CHAIN_MANIFEST))
    v10_summary = _load_json(_query(root, V10_SUMMARY_JSON))
    v10_manifest = _load_json(_query(root, V10_MANIFEST))
    product_summary = _load_json(_query(root, PRODUCT_CHAIN_SUMMARY))
    product_manifest = _load_json(_query(root, PRODUCT_CHAIN_MANIFEST))
    residue_summary = _load_json(_query(root, RESIDUE_SUMMARY))
    residue_manifest = _load_json(_query(root, RESIDUE_MANIFEST))
    curve_summary = _load_json(_query(root, CURVE_SUMMARY))
    curve_manifest = _load_json(_query(root, CURVE_MANIFEST))
    handoff_summary = _load_json(_query(root, HANDOFF_SUMMARY))
    handoff_manifest = _load_json(_query(root, HANDOFF_MANIFEST))
    scenarios_summary = _load_json(_query(root, SCENARIOS_SUMMARY))
    scenarios_manifest = _load_json(_query(root, SCENARIOS_MANIFEST))

    _require_artifact(
        chain_summary,
        "national_facility_chain_scenarios_v9",
        "9.0.0",
        "national chain v9 summary",
    )
    _require_artifact(
        chain_manifest,
        "national_facility_chain_scenarios_v9",
        "9.0.0",
        "national chain v9 manifest",
    )
    _require_artifact(
        v10_summary,
        "national_facility_assignment_v10",
        "10.0.0",
        "assignment v10 summary",
    )
    _require_artifact(
        v10_manifest,
        "national_facility_assignment_v10",
        "10.0.0",
        "assignment v10 manifest",
    )
    _require_artifact(
        product_summary,
        "national_product_chain_v5",
        "5.0.0",
        "national product chain v5 summary",
    )
    _require_artifact(
        product_manifest,
        "national_product_chain_v5",
        "5.0.0",
        "national product chain v5 manifest",
    )
    _require_artifact(
        residue_summary,
        "facility_residue_supply_v9",
        "9.0.0",
        "facility residue supply v9 summary",
    )
    _require_artifact(
        residue_manifest,
        "facility_residue_supply_v9",
        "9.0.0",
        "facility residue supply v9 manifest",
    )
    _require_artifact(
        curve_summary,
        "national_residue_curve_v9",
        "9.0.0",
        "national residue curve v9 summary",
    )
    _require_artifact(
        curve_manifest,
        "national_residue_curve_v9",
        "9.0.0",
        "national residue curve v9 manifest",
    )
    _require_artifact(
        handoff_summary,
        "forestry_osecan_handoff_v10",
        "10.0.0",
        "OSeCAN handoff v10 summary",
    )
    _require_artifact(
        handoff_manifest,
        "forestry_osecan_handoff_v10",
        "10.0.0",
        "OSeCAN handoff v10 manifest",
    )
    _require_artifact(
        scenarios_summary,
        "forestry_osecan_scenarios_v9",
        "9.0.0",
        "OSeCAN scenarios v9 summary",
    )
    _require_artifact(
        scenarios_manifest,
        "forestry_osecan_scenarios_v9",
        "9.0.0",
        "OSeCAN scenarios v9 manifest",
    )

    labels = chain_summary.get("labels") or {}
    _require_model_labels(labels, "national chain v9 summary")
    _require(labels.get("transport_cost_in_osecan") is False, "national chain v9 must keep transport outside OSeCAN")
    _require(labels.get("capacity_change") is False, "national chain v9 must not change capacity")
    _require(labels.get("product_change") is False, "national chain v9 must not change products")
    _require(labels.get("residue_change") is False, "national chain v9 must not change residue")
    for context, document in (
        ("national chain v9 manifest", chain_manifest),
        ("assignment v10 summary", v10_summary),
        ("assignment v10 manifest", v10_manifest),
        ("national product chain v5 summary", product_summary),
        ("national product chain v5 manifest", product_manifest),
        ("facility residue supply v9 summary", residue_summary),
        ("facility residue supply v9 manifest", residue_manifest),
        ("national residue curve v9 summary", curve_summary),
        ("national residue curve v9 manifest", curve_manifest),
        ("OSeCAN handoff v10 summary", handoff_summary),
        ("OSeCAN handoff v10 manifest", handoff_manifest),
        ("OSeCAN scenarios v9 summary", scenarios_summary),
        ("OSeCAN scenarios v9 manifest", scenarios_manifest),
    ):
        _require_model_labels(document.get("labels") or {}, context)
        _require(
            document.get("labels", {}).get("transport_cost_in_osecan", False) is False,
            f"{context} must keep transport outside OSeCAN",
        )
    _require(
        v10_summary.get("labels", {}).get("capacity_change") is False
        and v10_summary.get("labels", {}).get("floor_change") is False,
        "assignment v10 must not change capacity or the 1.2x floor",
    )
    _require(
        chain_summary.get("scenario_order") == list(EXPECTED_SCENARIOS),
        "national chain v9 scenario order is not 100m/119m/150m",
    )
    _require(chain_summary.get("primary_scenario") == DEFAULT_SCENARIO, "national chain v9 reference is not 119m")
    _require(
        list(v10_summary.get("cases") or {}) == list(EXPECTED_SCENARIOS),
        "assignment v10 cases are not 100m/119m/150m",
    )
    _require(
        (v10_summary.get("case_coverage") or {}).get("cases") == list(EXPECTED_SCENARIOS),
        "assignment v10 case coverage is not 100m/119m/150m",
    )
    _require(
        (v10_summary.get("case_coverage") or {}).get("capacity_change") is False
        and (v10_summary.get("case_coverage") or {}).get("transport_curve_change") is False,
        "assignment v10 case extension must keep capacity and transport curve unchanged",
    )

    product_binding = chain_summary.get("product_chain") or {}
    _require(
        product_binding.get("artifact") == "national_product_chain_v5",
        "national chain v9 does not bind product chain v5",
    )
    _require(product_binding.get("donor_product_provinces") == [], "donor product provinces must stay empty")
    _require(
        product_binding.get("recovery_scenarios") == [0.35, 0.6, 0.75],
        "national chain v9 recovery scenarios changed",
    )
    measured_provinces = product_summary.get("measured_provinces")
    imputed_provinces = product_summary.get("imputed_provinces")
    _require(
        isinstance(measured_provinces, list) and len(measured_provinces) == 4,
        "E1 measured province count is not four",
    )
    _require(
        isinstance(imputed_provinces, list) and len(imputed_provinces) == 9,
        "E2c imputed province count is not nine",
    )
    _require(set(measured_provinces).isdisjoint(imputed_provinces), "E1 and E2c province sets overlap")
    _require(
        set(measured_provinces) | set(imputed_provinces) == set(PROVINCE_LABELS),
        "E1/E2c province sets do not cover all 13 jurisdictions",
    )
    _require(product_summary.get("donor_provinces") == [], "product chain v5 donor provinces must stay empty")
    _require(
        set(product_binding.get("measured_provinces") or []) == set(measured_provinces),
        "chain v9 measured provinces do not match product chain v5",
    )
    _require(
        set(product_binding.get("density_calibrated_provinces") or []) == set(imputed_provinces),
        "chain v9 E2c provinces do not match product chain v5",
    )
    coefficient_tiers = product_summary.get("coefficient_tiers") or {}
    _require(
        coefficient_tiers.get("measured_bucking_pilot") == len(measured_provinces),
        "product chain v5 measured coefficient tier count changed",
    )
    _require(
        coefficient_tiers.get("E2c_open_inventory_density_calibrated") == len(imputed_provinces),
        "product chain v5 E2c coefficient tier count changed",
    )
    _require(
        coefficient_tiers.get("donor_product_coefficients") == 0,
        "product chain v5 must apply zero donor product coefficients",
    )
    _require(
        chain_summary.get("coefficient_tiers") == coefficient_tiers,
        "chain v9 coefficient tiers do not match product chain v5",
    )

    e1_rows = _read_csv(_query(root, E1_COEFFICIENTS))
    e1_provinces = {row["province"] for row in e1_rows}
    _require(e1_provinces == set(measured_provinces), "E1 coefficient rows do not cover the measured provinces")
    _require(
        all(row["tier"] == "measured_bucking_pilot" for row in e1_rows),
        "E1 coefficient rows carry an unexpected tier",
    )
    e2c_rows = _read_csv(_query(root, E2C_EVIDENCE))
    e2c_provinces = {row["province"] for row in e2c_rows}
    _require(e2c_provinces == set(imputed_provinces), "E2c evidence rows do not cover the imputed provinces")
    _require(
        all(
            row["coefficient_tier"] == "E2c_open_inventory_density_calibrated"
            for row in e2c_rows
        ),
        "E2c evidence rows carry an unexpected tier",
    )

    chain_rows = _read_csv(_query(root, CHAIN_SUMMARY_CSV))
    _require(
        [row["scenario"] for row in chain_rows] == list(EXPECTED_SCENARIOS),
        "national chain v9 CSV must carry 100m/119m/150m in order",
    )
    scenario_details = chain_summary.get("scenarios") or {}
    _require(
        list(scenario_details) == list(EXPECTED_SCENARIOS),
        "national chain v9 summary scenarios are not 100m/119m/150m",
    )
    v10_case_rows = _read_csv(_query(root, V10_CASE_SUMMARY_CSV))
    _require(
        [row["scenario"] for row in v10_case_rows] == list(EXPECTED_SCENARIOS),
        "assignment v10 case summary must carry 100m/119m/150m in order",
    )
    v7_rows = {
        row["scenario"]: row for row in _read_csv(_query(root, V7_CHAIN_SUMMARY_CSV))
    }
    _require(set(v7_rows) == set(EXPECTED_SCENARIOS), "v7 invariance baseline is missing a harvest case")

    rows: list[dict[str, Any]] = []
    for source_row, v10_row in zip(chain_rows, v10_case_rows, strict=True):
        scenario_id = source_row["scenario"]
        detail = scenario_details[scenario_id]
        for field in (
            "harvest_m3",
            "sawlog_product_m3",
            "panel_product_m3",
            "pulp_product_odt",
            "pellet_product_odt",
            "bio_product_mwh",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
            "total_residue_gross_pj",
            "harvest_residue_gross_odt",
            "harvest_residue_recoverable_odt",
        ):
            csv_value = _number(source_row.get(field), field, scenario_id, required=True)
            summary_value = _number(str(detail.get(field, "")), field, scenario_id, required=True)
            assert csv_value is not None and summary_value is not None
            _require(
                _close(csv_value, summary_value),
                f"national chain v9 CSV/summary mismatch for {scenario_id}.{field}",
            )
        for field in (
            "harvest_m3",
            "sawlog_product_m3",
            "panel_product_m3",
            "pulp_product_odt",
            "pellet_product_odt",
            "bio_product_mwh",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
            "total_residue_gross_pj",
        ):
            v7_value = _number(v7_rows[scenario_id].get(field), field, f"v7 {scenario_id}", required=True)
            v9_value = _number(source_row.get(field), field, scenario_id, required=True)
            assert v7_value is not None and v9_value is not None
            _require(
                _close(v7_value, v9_value),
                f"national chain v9 {scenario_id}.{field} is not invariant from v7",
            )

        sawlog = _number(source_row["sawlog_product_m3"], "sawlog_product_m3", scenario_id, required=True)
        panel = _number(source_row["panel_product_m3"], "panel_product_m3", scenario_id, required=True)
        pulp = _number(source_row["pulp_product_odt"], "pulp_product_odt", scenario_id, required=True)
        process = _number(source_row["process_residue_pj"], "process_residue_pj", scenario_id, required=True)
        harvest_gross = _number(
            source_row["harvest_residue_gross_pj"],
            "harvest_residue_gross_pj",
            scenario_id,
            required=True,
        )
        harvest_recoverable = _number(
            source_row["harvest_residue_recoverable_pj"],
            "harvest_residue_recoverable_pj",
            scenario_id,
            required=True,
        )
        total_gross = _number(
            source_row["total_residue_gross_pj"],
            "total_residue_gross_pj",
            scenario_id,
            required=True,
        )
        harvest_gross_odt = _number(
            source_row["harvest_residue_gross_odt"],
            "harvest_residue_gross_odt",
            scenario_id,
            required=True,
        )
        harvest_recoverable_odt = _number(
            source_row["harvest_residue_recoverable_odt"],
            "harvest_residue_recoverable_odt",
            scenario_id,
            required=True,
        )
        harvest = _number(source_row["harvest_m3"], "harvest_m3", scenario_id, required=True)
        assert sawlog is not None and panel is not None and pulp is not None
        assert process is not None and harvest_gross is not None and harvest_recoverable is not None
        assert total_gross is not None and harvest_gross_odt is not None
        assert harvest_recoverable_odt is not None and harvest is not None
        solid = sawlog + panel
        _require(
            solid > 0 and pulp > 0 and process >= 0 and harvest_gross > 0,
            f"{scenario_id} chain value is not positive",
        )
        _require(
            harvest_recoverable >= 0 and total_gross > 0 and harvest > 0,
            f"{scenario_id} chain total is not positive",
        )
        _require(
            _close(total_gross, process + harvest_gross),
            f"{scenario_id} total gross PJ is not process plus harvest gross",
        )

        expected_assigned = _number(
            v10_row["assigned_m3"], "assigned_m3", f"v10 {scenario_id}", required=True
        )
        expected_facilities = _integer(
            v10_row["facilities_used"], "facilities_used", f"v10 {scenario_id}"
        )
        summary_case = (v10_summary.get("cases") or {}).get(scenario_id) or {}
        summary_metrics = summary_case.get("metrics") or {}
        _require(bool(summary_metrics), f"assignment v10 summary is missing {scenario_id} metrics")
        for field in ("assigned_m3", "facilities_used", "transport_cost_cad", "mean_road_distance_km"):
            value = _number(
                v10_row[field], field, f"v10 {scenario_id}", required=True
            )
            summary_value = _number(
                str(summary_metrics.get(field, "")), field, f"v10 {scenario_id}", required=True
            )
            assert value is not None and summary_value is not None
            _require(
                _close(value, summary_value),
                f"assignment v10 CSV/summary mismatch for {scenario_id}.{field}",
            )
        _require(
            abs(harvest - (expected_assigned or 0.0)) <= 1.0,
            f"{scenario_id} v10 assignment does not conserve the harvest case",
        )
        _require(
            (expected_facilities) > 0,
            f"{scenario_id} v10 facilities used is not positive",
        )
        for field in (
            "transport_cost_cad",
            "transport_cost_cad_m3",
            "transport_cost_cad_gj",
            "mean_road_distance_km",
            "median_road_distance_km",
            "p90_road_distance_km",
            "max_road_distance_km",
            "share_ge_150_km",
            "share_ge_300_km",
            "cross_border_m3",
            "cross_border_share",
            "proxy_share_of_assigned",
        ):
            value = _number(v10_row[field], field, f"v10 {scenario_id}", required=True)
            assert value is not None
            _require(math.isfinite(value) and value >= 0, f"{scenario_id} has invalid v10 {field}")
        for field in ("share_ge_150_km", "share_ge_300_km", "cross_border_share", "proxy_share_of_assigned"):
            value = _number(v10_row[field], field, f"v10 {scenario_id}", required=True)
            assert value is not None
            _require(0 <= value <= 1, f"{scenario_id} v10 {field} is outside [0, 1]")
        _require(
            (_number(v10_row["transport_cost_cad"], "transport_cost_cad", scenario_id, required=True) or 0)
            > 0,
            f"{scenario_id} v10 transport cost is not positive",
        )
        _require(
            (_number(v10_row["mean_road_distance_km"], "mean_road_distance_km", scenario_id, required=True) or 0)
            > 0,
            f"{scenario_id} v10 mean distance is not positive",
        )

        rows.append(
            {
                "id": scenario_id,
                "label": f"{scenario_id.removesuffix('m')} Mm³",
                "harvest_m3": harvest,
                "solid_m3": solid,
                "pulp_odt": pulp,
                "process_pj": process,
                "harvest_gross_pj": harvest_gross,
                "harvest_recoverable_pj": harvest_recoverable,
                "total_gross_pj": total_gross,
                "transport_cost_cad": _number(
                    v10_row["transport_cost_cad"], "transport_cost_cad", scenario_id, required=True
                ),
                "transport_cost_cad_m3": _number(
                    v10_row["transport_cost_cad_m3"], "transport_cost_cad_m3", scenario_id, required=True
                ),
                "transport_cost_cad_gj": _number(
                    v10_row["transport_cost_cad_gj"], "transport_cost_cad_gj", scenario_id, required=True
                ),
                "mean_road_distance_km": _number(
                    v10_row["mean_road_distance_km"], "mean_road_distance_km", scenario_id, required=True
                ),
                "median_road_distance_km": _number(
                    v10_row["median_road_distance_km"], "median_road_distance_km", scenario_id, required=True
                ),
                "p90_road_distance_km": _number(
                    v10_row["p90_road_distance_km"], "p90_road_distance_km", scenario_id, required=True
                ),
                "max_road_distance_km": _number(
                    v10_row["max_road_distance_km"], "max_road_distance_km", scenario_id, required=True
                ),
                "share_ge_150_km": _number(
                    v10_row["share_ge_150_km"], "share_ge_150_km", scenario_id, required=True
                ),
                "share_ge_300_km": _number(
                    v10_row["share_ge_300_km"], "share_ge_300_km", scenario_id, required=True
                ),
                "cross_border_m3": _number(
                    v10_row["cross_border_m3"], "cross_border_m3", scenario_id, required=True
                ),
                "cross_border_share": _number(
                    v10_row["cross_border_share"], "cross_border_share", scenario_id, required=True
                ),
                "proxy_share_of_assigned": _number(
                    v10_row["proxy_share_of_assigned"], "proxy_share_of_assigned", scenario_id, required=True
                ),
                "facilities_used": expected_facilities,
            }
        )

    _require(
        rows[0]["solid_m3"] < rows[1]["solid_m3"] < rows[2]["solid_m3"],
        "national chain solid product does not order 100 < 119 < 150",
    )
    _require(
        rows[0]["pulp_odt"] < rows[1]["pulp_odt"] < rows[2]["pulp_odt"],
        "national chain pulp product does not order 100 < 119 < 150",
    )
    _require(
        rows[0]["process_pj"] < rows[1]["process_pj"] < rows[2]["process_pj"],
        "national chain process residue does not order 100 < 119 < 150",
    )
    _require(
        rows[0]["harvest_gross_pj"] < rows[1]["harvest_gross_pj"] < rows[2]["harvest_gross_pj"],
        "national chain harvest residue does not order 100 < 119 < 150",
    )
    _require(
        rows[0]["transport_cost_cad"] < rows[1]["transport_cost_cad"] < rows[2]["transport_cost_cad"],
        "national chain transport cost does not order 100 < 119 < 150",
    )

    invariance_rows = _read_csv(_query(root, CHAIN_INVARIANCE_CSV))
    _require(len(invariance_rows) == 30, "chain v9 product/residue invariance table must carry 30 rows")
    for row in invariance_rows:
        _require(row["invariant"] == "true", f"chain v9 invariance failed for {row['scenario']}.{row['metric']}")
        delta = _number(row["delta"], "delta", f"invariance {row['scenario']}.{row['metric']}", required=True)
        assert delta is not None
        _require(abs(delta) <= 1e-9, f"chain v9 invariance delta is nonzero for {row['scenario']}.{row['metric']}")

    residue_rows = _read_csv(_query(root, RESIDUE_NATIONAL_CSV))
    residue_totals: dict[str, float] = {}
    for row in residue_rows:
        if row["scenario"] != DEFAULT_SCENARIO:
            continue
        _require(
            row["assignment"] == "v10_node_transport_aware",
            "facility residue v9 119m must use assignment v10",
        )
        metric = row["metric"]
        _require(metric not in residue_totals, f"duplicate residue metric {metric}")
        value = _number(row["value"], "value", f"residue metric {metric}", required=True)
        assert value is not None
        residue_totals[metric] = value
    reference = rows[1]
    for metric, field in (
        ("process_residue_pj", "process_pj"),
        ("harvest_residue_gross_pj", "harvest_gross_pj"),
        ("harvest_residue_recoverable_pj", "harvest_recoverable_pj"),
        ("combined_process_plus_harvest_gross_pj", "total_gross_pj"),
    ):
        _require(metric in residue_totals, f"facility residue v9 is missing {metric}")
        _require(
            _close(residue_totals[metric], reference[field]),
            f"facility residue v9 {metric} does not match the 119 Mm³ chain",
        )

    curve_rows = _read_csv(_query(root, CURVE_NATIONAL_CSV))
    process_curve = _curve_national_row(
        curve_rows,
        "process_residue",
        "TOTAL",
        "process",
        "process_availability_050",
    )
    harvest_curve = _curve_national_row(
        curve_rows,
        "harvest_residue_recoverable",
        "ALL",
        "harvest",
        "harvest_recovery_060",
    )
    process_fraction = _number(
        process_curve["availability_fraction"],
        "availability_fraction",
        "process_availability_050",
        required=True,
    )
    harvest_fraction = _number(
        harvest_curve["availability_fraction"],
        "availability_fraction",
        "harvest_recovery_060",
        required=True,
    )
    assert process_fraction is not None and harvest_fraction is not None
    _require(0 < process_fraction <= 1, "process availability fraction is outside (0, 1]")
    _require(0 < harvest_fraction <= 1, "harvest recovery fraction is outside (0, 1]")
    process_curve_pj = _number(
        process_curve["residue_pj"], "residue_pj", "process_availability_050", required=True
    )
    harvest_curve_pj = _number(
        harvest_curve["residue_pj"], "residue_pj", "harvest_recovery_060", required=True
    )
    assert process_curve_pj is not None and harvest_curve_pj is not None
    _require(
        _close(process_curve_pj, reference["process_pj"] * process_fraction),
        "residue curve v9 process 50% does not apply the reference availability fraction",
    )
    _require(
        _close(harvest_curve_pj, reference["harvest_recoverable_pj"]),
        "residue curve v9 harvest 60% does not match the 119 Mm³ chain",
    )
    for curve_row, context in (
        (process_curve, "process_availability_050"),
        (harvest_curve, "harvest_recovery_060"),
    ):
        _require(curve_row["model_estimate"] == "true", f"residue curve v9 {context} is not a model estimate")
        _require(curve_row["observed"] == "false", f"residue curve v9 {context} observed must be false")
        _require(curve_row["policy_grade"] == "false", f"residue curve v9 {context} policy_grade must be false")
    for row in rows:
        harvest_gross_odt = _number(
            next(source["harvest_residue_gross_odt"] for source in chain_rows if source["scenario"] == row["id"]),
            "harvest_residue_gross_odt",
            row["id"],
            required=True,
        )
        harvest_recoverable_odt = _number(
            next(
                source["harvest_residue_recoverable_odt"]
                for source in chain_rows
                if source["scenario"] == row["id"]
            ),
            "harvest_residue_recoverable_odt",
            row["id"],
            required=True,
        )
        assert harvest_gross_odt is not None and harvest_recoverable_odt is not None
        _require(harvest_gross_odt > 0, f"{row['id']} harvest gross odt is not positive")
        _require(
            _close(harvest_recoverable_odt, harvest_gross_odt * harvest_fraction),
            f"{row['id']} harvest recoverable does not apply the reference recovery fraction",
        )
        _require(
            _close(row["harvest_recoverable_pj"], row["harvest_gross_pj"] * harvest_fraction),
            f"{row['id']} harvest recoverable PJ does not apply the reference recovery fraction",
        )

    handoff_tier_rows = _read_csv(_query(root, HANDOFF_TIERS_CSV))
    _require(len(handoff_tier_rows) == 7, "OSeCAN handoff v10 tier table must carry seven cases")
    handoff_tiers = {(row["stream"], row["case"]): row for row in handoff_tier_rows}
    _require(len(handoff_tiers) == len(handoff_tier_rows), "duplicate case in OSeCAN handoff v10 tier table")
    supply_curve = handoff_summary.get("supply_curve") or {}
    case_tier = supply_curve.get("case_tier") or {}
    upper_pj = _number(str(supply_curve.get("upper_pj", "")), "upper_pj", "OSeCAN handoff v10", required=True)
    assert upper_pj is not None and upper_pj > 0
    tier_specs = (
        ("process", "process_availability_050", "process_pj", process_fraction),
        ("harvest", "harvest_recovery_060", "harvest_recoverable_pj", harvest_fraction),
    )
    tier_case_values = {
        "process_availability_050": process_curve_pj,
        "harvest_recovery_060": harvest_curve_pj,
    }
    tier_badges: list[dict[str, Any]] = []
    for stream, case, field, fraction in tier_specs:
        handoff_row = handoff_tiers.get((stream, case))
        _require(handoff_row is not None, f"OSeCAN handoff v10 is missing tier case {case}")
        _require(handoff_row["model_estimate"] == "true", f"OSeCAN handoff v10 {case} is not a model estimate")
        _require(handoff_row["observed"] == "false", f"OSeCAN handoff v10 {case} observed must be false")
        _require(handoff_row["policy_grade"] == "false", f"OSeCAN handoff v10 {case} policy_grade must be false")
        tier = handoff_row["v4_e2c_tier"]
        _require(bool(tier), f"OSeCAN handoff v10 {case} has no tier")
        _require(
            case_tier.get(f"{stream}/{case}") == tier,
            f"OSeCAN handoff v10 {case} tier is inconsistent",
        )
        if tier == "above_T4":
            _require(
                tier_case_values[case] > upper_pj,
                f"OSeCAN handoff v10 {case} says above_T4 but the 119 Mm³ case value is not",
            )
        tier_badges.append(
            {
                "stream": stream,
                "case": case,
                "fraction": fraction,
                "tier": tier,
                "label": tier.replace("_", " "),
            }
        )

    scenarios_tiers = _read_csv(_query(root, SCENARIOS_TIERS_CSV))
    _require(len(scenarios_tiers) == 21, "OSeCAN scenarios v9 must carry 21 tier positions")
    scenario_tier_index: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in scenarios_tiers:
        key = (row["scenario"], row["stream"], row["case"])
        _require(key not in scenario_tier_index, f"duplicate OSeCAN scenario tier position {key}")
        _require(row["model_estimate"] == "true", f"OSeCAN scenarios v9 {key} is not a model estimate")
        _require(row["observed"] == "false", f"OSeCAN scenarios v9 {key} observed must be false")
        _require(row["policy_grade"] == "false", f"OSeCAN scenarios v9 {key} policy_grade must be false")
        scenario_tier_index[key] = row
    row_by_id = {row["id"]: row for row in rows}
    for scenario_id in EXPECTED_SCENARIOS:
        row = row_by_id[scenario_id]
        for stream, fraction in (("process", 0.25), ("process", 0.5), ("process", 0.75), ("harvest", 0.35), ("harvest", 0.6), ("harvest", 0.75)):
            case = (
                f"process_availability_{round(fraction * 100):03d}"
                if stream == "process"
                else f"harvest_recovery_{round(fraction * 100):03d}"
            )
            scenario_tier = scenario_tier_index.get((scenario_id, stream, case))
            _require(
                scenario_tier is not None,
                f"OSeCAN scenarios v9 is missing {scenario_id}/{stream}/{case}",
            )
            scenario_pj = _number(
                str(scenario_tier.get("v4_e2c_pj", "")),
                "v4_e2c_pj",
                f"{scenario_id}/{case}",
                required=True,
            )
            assert scenario_pj is not None
            expected_case_pj = (
                row["process_pj"] * fraction
                if stream == "process"
                else row["harvest_gross_pj"] * fraction
            )
            _require(
                _close(scenario_pj, expected_case_pj),
                f"OSeCAN scenarios v9 {scenario_id}/{case} does not match the chain",
            )
            _require(
                bool(scenario_tier.get("v4_e2c_tier")),
                f"OSeCAN scenarios v9 {scenario_id}/{case} has no tier",
            )
    for stream, case, _field, _fraction in tier_specs:
        reference_tier = scenario_tier_index[(DEFAULT_SCENARIO, stream, case)]["v4_e2c_tier"]
        handoff_tier = handoff_tiers[(stream, case)]["v4_e2c_tier"]
        _require(
            reference_tier == handoff_tier,
            f"OSeCAN scenarios v9 119 Mm³ {case} tier disagrees with handoff v10",
        )

    return {
        "artifact": chain_summary["artifact"],
        "artifact_version": chain_summary["artifact_version"],
        "generated_utc": chain_summary["generated_utc"],
        "labels": {
            "model_estimate": labels["model_estimate"],
            "observed": labels["observed"],
            "policy_grade": labels["policy_grade"],
            "national_claim": labels["national_claim"],
            "official_osecan_changed": labels["official_osecan_changed"],
            "transport_cost_in_osecan": labels["transport_cost_in_osecan"],
        },
        "assignment": {
            "artifact": v10_summary["artifact"],
            "scenario": "node_transport_aware",
            "cases": list(EXPECTED_SCENARIOS),
            "transport_cost_in_osecan": False,
        },
        "coefficient_provenance": {
            "e1": {
                "label": "E1 measured",
                "tier": "measured_bucking_pilot",
                "provinces": measured_provinces,
                "count": len(measured_provinces),
            },
            "e2c": {
                "label": "E2c imputed",
                "tier": "E2c_open_inventory_density_calibrated",
                "provinces": imputed_provinces,
                "count": len(imputed_provinces),
            },
            "donor_product_coefficients": coefficient_tiers["donor_product_coefficients"],
        },
        "reference": {
            "scenario": DEFAULT_SCENARIO,
            "process_case": "process_availability_050",
            "process_availability_fraction": process_fraction,
            "harvest_case": "harvest_recovery_060",
            "harvest_recovery_fraction": harvest_fraction,
            "transport_cost_cad": reference["transport_cost_cad"],
            "mean_road_distance_km": reference["mean_road_distance_km"],
            "tiers": tier_badges,
        },
        "columns": [
            {
                "id": "solid",
                "field": "solid_m3",
                "label": "Solid",
                "unit": "Mm³",
                "scale": 1_000_000,
                "decimals": 3,
            },
            {
                "id": "pulp",
                "field": "pulp_odt",
                "label": "Pulp",
                "unit": "M odt",
                "scale": 1_000_000,
                "decimals": 3,
            },
            {
                "id": "process",
                "field": "process_pj",
                "label": "Process",
                "unit": "PJ",
                "scale": 1,
                "decimals": 3,
                "tier_stream": "process",
            },
            {
                "id": "harvest_gross",
                "field": "harvest_gross_pj",
                "label": "Harvest gross",
                "unit": "PJ",
                "scale": 1,
                "decimals": 3,
            },
            {
                "id": "harvest_recoverable",
                "field": "harvest_recoverable_pj",
                "label": "Harvest recoverable",
                "unit": "PJ",
                "scale": 1,
                "decimals": 3,
                "tier_stream": "harvest",
            },
            {
                "id": "total_gross",
                "field": "total_gross_pj",
                "label": "Total gross",
                "unit": "PJ",
                "scale": 1,
                "decimals": 3,
            },
            {
                "id": "transport_cost",
                "field": "transport_cost_cad",
                "label": "Transport",
                "unit": "B CAD/yr",
                "scale": 1_000_000_000,
                "decimals": 3,
            },
            {
                "id": "mean_distance",
                "field": "mean_road_distance_km",
                "label": "Mean distance",
                "unit": "km",
                "scale": 1,
                "decimals": 1,
            },
        ],
        "rows": rows,
    }


def build_payload(root: Path) -> dict[str, Any]:
    root = root.resolve()
    capacity_manifest = _load_json(_query(root, CAPACITY_MANIFEST))
    chain_manifest = _load_json(_query(root, CHAIN_MANIFEST))
    _require(capacity_manifest.get("labels", {}).get("observed") is False, "capacity labels must be model-only")
    _require(capacity_manifest.get("labels", {}).get("policy_grade") is False, "capacity policy_grade must be false")
    _require(chain_manifest.get("labels", {}).get("observed") is False, "chain labels must be model-only")
    _require(chain_manifest.get("labels", {}).get("policy_grade") is False, "chain policy_grade must be false")

    capacity_rows = _read_csv(_query(root, CAPACITY_CSV))
    counts = capacity_manifest["counts"]
    _require(len(capacity_rows) == counts["records_total"], "capacity record count does not match manifest")
    _require(
        len({row["facility_id"] for row in capacity_rows}) == len(capacity_rows),
        "duplicate facility_id in capacity CSV",
    )

    inventory_rows = _read_csv(_query(root, INVENTORY_CSV))
    inventory_ids = {row["facility_id"] for row in inventory_rows}
    _require(len(inventory_rows) == 519, "inventory v1 record count changed")
    _require(inventory_ids.issubset({row["facility_id"] for row in capacity_rows}), "inventory IDs not carried into capacity v3")

    class_counts: dict[str, int] = defaultdict(int)
    province_counts: dict[str, int] = defaultdict(int)
    class_published: dict[str, int] = defaultdict(int)
    class_proxy: dict[str, int] = defaultdict(int)
    province_published: dict[str, int] = defaultdict(int)
    province_proxy: dict[str, int] = defaultdict(int)
    published_capacity = 0.0
    proxy_capacity = 0.0
    class_published_capacity: dict[str, float] = defaultdict(float)
    class_proxy_capacity: dict[str, float] = defaultdict(float)
    province_published_capacity: dict[str, float] = defaultdict(float)
    province_proxy_capacity: dict[str, float] = defaultdict(float)
    mapped_by_class: dict[str, int] = defaultdict(int)
    mapped_by_province: dict[str, int] = defaultdict(int)
    facilities: list[dict[str, Any]] = []

    for row in capacity_rows:
        facility_id = row["facility_id"]
        facility_class = row["facility_class"]
        province = row["province"]
        _require(facility_class in CLASS_ORDER, f"unexpected facility class {facility_class}")
        _require(province in PROVINCE_LABELS, f"unexpected province {province}")
        status = row["capacity_status"]
        _require(status in {"published", "proxy", "not_published"}, f"unexpected capacity status {status}")
        capacity = _number(row["capacity_m3_y"], "capacity_m3_y", facility_id, required=status != "not_published")
        lat = _number(row["lat"], "lat", facility_id)
        lon = _number(row["lon"], "lon", facility_id)
        _require((lat is None) == (lon is None), f"partial coordinates for {facility_id}")
        mapped = lat is not None and lon is not None
        if mapped:
            assert lat is not None and lon is not None
            _require(-141.0 <= lon <= -52.0, f"longitude outside Canada for {facility_id}")
            _require(41.0 <= lat <= 84.0, f"latitude outside Canada for {facility_id}")
        if capacity is not None:
            _require(capacity >= 0, f"negative capacity for {facility_id}")
            if status == "published":
                published_capacity += capacity
                class_published_capacity[facility_class] += capacity
                province_published_capacity[province] += capacity
            elif status == "proxy":
                proxy_capacity += capacity
                class_proxy_capacity[facility_class] += capacity
                province_proxy_capacity[province] += capacity
        class_counts[facility_class] += 1
        province_counts[province] += 1
        if status == "published":
            class_published[facility_class] += 1
            province_published[province] += 1
        elif status == "proxy":
            class_proxy[facility_class] += 1
            province_proxy[province] += 1
        if mapped:
            mapped_by_class[facility_class] += 1
            mapped_by_province[province] += 1
        facilities.append(
            {
                "id": facility_id,
                "name": row["name"],
                "facility_class": facility_class,
                "province": province,
                "lat": lat,
                "lon": lon,
                "mapped": mapped,
                "capacity_m3_y": capacity,
                "capacity_status": status,
                "capacity_evidence": row["capacity_evidence"] or None,
                "capacity_basis": row["capacity_basis"] or None,
                "status": row["status"] or "unknown",
                "evidence_tier": row["evidence_tier"] or None,
                "source_id": row["source_id"] or None,
                "source_url": row["source_url"] or None,
                "capacity_source_id": row["capacity_source_id"] or None,
                "capacity_source_url": row["capacity_source_url"] or None,
            }
        )

    _require(len(facilities) == counts["records_total"], "facility rows do not conserve")
    _require(dict(class_counts) == counts["records_by_class"], "class counts do not match capacity manifest")
    _require(
        {key: value for key, value in province_counts.items() if value},
        {key: value for key, value in counts["records_by_province"].items() if value},
    )
    coverage_after = capacity_manifest["coverage"]["after"]
    _require(
        abs(published_capacity - coverage_after["published_capacity_m3_y"]) <= 1e-6,
        "published capacity total does not match capacity manifest",
    )
    _require(
        abs(proxy_capacity - coverage_after["proxy_capacity_m3_y"]) <= 1e-6,
        "proxy capacity total does not match capacity manifest",
    )
    _require(
        abs((published_capacity + proxy_capacity) - coverage_after["total_capacity_m3_y"]) <= 1e-6,
        "total capacity does not match capacity manifest",
    )

    base = _base_rows(capacity_rows)
    chain_summary_rows = _read_csv(_query(root, CHAIN_SUMMARY_CSV))
    case_summaries = _read_csv(_query(root, V10_CASE_SUMMARY_CSV))
    scenarios, base_assignment = _build_scenarios(
        root, capacity_rows, base, chain_summary_rows, case_summaries
    )

    classes = []
    for class_id in CLASS_ORDER:
        classes.append(
            {
                "id": class_id,
                "label": CLASS_LABELS[class_id],
                "color": CLASS_COLORS[class_id],
                "record_count": class_counts[class_id],
                "published_count": class_published[class_id],
                "proxy_count": class_proxy[class_id],
                "not_published_count": class_counts[class_id] - class_published[class_id] - class_proxy[class_id],
                "mapped_count": mapped_by_class[class_id],
                "unlocated_count": class_counts[class_id] - mapped_by_class[class_id],
                "published_capacity_m3_y": class_published_capacity[class_id],
                "proxy_capacity_m3_y": class_proxy_capacity[class_id],
            }
        )
    provinces = []
    for province in sorted(PROVINCE_LABELS, key=lambda code: PROVINCE_LABELS[code]):
        if not province_counts[province]:
            continue
        provinces.append(
            {
                "id": province,
                "label": PROVINCE_LABELS[province],
                "record_count": province_counts[province],
                "published_count": province_published[province],
                "proxy_count": province_proxy[province],
                "not_published_count": province_counts[province]
                - province_published[province]
                - province_proxy[province],
                "mapped_count": mapped_by_province[province],
                "unlocated_count": province_counts[province] - mapped_by_province[province],
                "published_capacity_m3_y": province_published_capacity[province],
                "proxy_capacity_m3_y": province_proxy_capacity[province],
            }
        )

    scenario_facilities: dict[str, dict[str, dict[str, Any]]] = {}
    for scenario in scenarios:
        utilization_rows = _read_csv(
            _query(root, _case_path(ASSIGNMENT_DIR, "v10_facility_utilization", scenario["id"]))
        )
        selected: dict[str, dict[str, Any]] = {}
        for row in utilization_rows:
            assigned = _number(row["assigned_m3"], "assigned_m3", row["facility_id"], required=True)
            assert assigned is not None
            if row.get("used") != "true" and assigned <= 0:
                continue
            selected[row["facility_id"]] = {
                "assigned_m3_y": assigned,
                "capacity_m3_y": _number(
                    row["capacity_m3_y"], "capacity_m3_y", row["facility_id"], required=True
                ),
                "capacity_status": row["capacity_status"],
                "utilization": _number(row["utilization"], "utilization", row["facility_id"]),
                "cross_border": (
                    _number(
                        row["cross_border_assigned_m3"],
                        "cross_border_assigned_m3",
                        row["facility_id"],
                    )
                    or 0.0
                )
                > 0,
                "local_assigned_m3_y": _number(
                    row["local_assigned_m3"], "local_assigned_m3", row["facility_id"]
                )
                or 0.0,
                "floor_scale_factor": _number(
                    row.get("floor_scale_factor"), "floor_scale_factor", row["facility_id"]
                ),
            }
        _require(
            len(selected) == scenario["facilities_used"],
            f"{scenario['id']} facility assignment rows do not match facilities used",
        )
        scenario_facilities[scenario["id"]] = selected
    for scenario in scenarios:
        scenario["facility_assignments"] = scenario_facilities[scenario["id"]]

    mapped_count = sum(1 for facility in facilities if facility["mapped"])
    unlocated = [facility for facility in facilities if not facility["mapped"]]
    _require(all(facility["province"] == "BC" for facility in unlocated), "only BC records may lack coordinates")
    _require(mapped_count + len(unlocated) == len(facilities), "mapped/unlocated counts do not conserve")

    published_count = sum(1 for facility in facilities if facility["capacity_status"] == "published")
    proxy_count = sum(1 for facility in facilities if facility["capacity_status"] == "proxy")
    not_published_count = sum(1 for facility in facilities if facility["capacity_status"] == "not_published")
    _require(published_count == counts["published_records"], "published record count does not match manifest")
    _require(proxy_count == counts["proxy_records"], "proxy record count does not match manifest")
    _require(not_published_count == counts["not_published_records"], "not-published record count does not match manifest")

    source_files = [
        _source_record(root, CAPACITY_CSV, "record and capacity ledger", "national_facility_capacity_v3"),
        _source_record(root, CAPACITY_SUMMARY, "capacity summary and coverage", "national_facility_capacity_v3"),
        _source_record(root, CAPACITY_MANIFEST, "capacity manifest", "national_facility_capacity_v3"),
        _source_record(root, INVENTORY_CSV, "inventory v1 source ledger", "national_facility_inventory_v1"),
        _source_record(
            root,
            V10_CASE_SUMMARY_CSV,
            "100/119/150 Mm3 assignment v10 totals, transport cost and distance",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            V10_SUMMARY_JSON,
            "assignment v10 summary, labels and case metrics",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            V10_MANIFEST,
            "assignment v10 manifest",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_facility_utilization", "100m"),
            "100m v10 facility assignment and utilization",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_facility_utilization", "119m"),
            "119m v10 facility assignment and utilization",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_facility_utilization", "150m"),
            "150m v10 facility assignment and utilization",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_province_summary", "100m"),
            "100m v10 province assignment and capacity totals",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_province_summary", "119m"),
            "119m v10 province assignment and capacity totals",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            _case_path(ASSIGNMENT_DIR, "v10_province_summary", "150m"),
            "150m v10 province assignment and capacity totals",
            "national_facility_assignment_v10",
        ),
        _source_record(
            root,
            CHAIN_SUMMARY_CSV,
            "v5.1 national chain scenario products and residue",
            "national_facility_chain_scenarios_v9",
        ),
        _source_record(
            root,
            CHAIN_SUMMARY_JSON,
            "v5.1 national chain summary, labels and coefficient tiers",
            "national_facility_chain_scenarios_v9",
        ),
        _source_record(
            root,
            CHAIN_MANIFEST,
            "v5.1 national chain manifest",
            "national_facility_chain_scenarios_v9",
        ),
        _source_record(
            root,
            CHAIN_INVARIANCE_CSV,
            "v5.1 product/residue invariance rows",
            "national_facility_chain_scenarios_v9",
        ),
        _source_record(
            root,
            PRODUCT_CHAIN_SUMMARY,
            "product chain coefficient tiers and labels",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            PRODUCT_CHAIN_MANIFEST,
            "product chain manifest",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            E1_COEFFICIENTS,
            "E1 measured coefficient rows",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            E2C_EVIDENCE,
            "E2c imputed coefficient evidence",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            RESIDUE_NATIONAL_CSV,
            "v5.1 119 Mm3 national residue totals",
            "facility_residue_supply_v9",
        ),
        _source_record(
            root,
            RESIDUE_SUMMARY,
            "v5.1 facility residue summary and labels",
            "facility_residue_supply_v9",
        ),
        _source_record(
            root,
            RESIDUE_MANIFEST,
            "v5.1 facility residue manifest",
            "facility_residue_supply_v9",
        ),
        _source_record(
            root,
            CURVE_NATIONAL_CSV,
            "v5.1 national residue curve rows",
            "national_residue_curve_v9",
        ),
        _source_record(
            root,
            CURVE_SUMMARY,
            "v5.1 national residue curve summary and labels",
            "national_residue_curve_v9",
        ),
        _source_record(
            root,
            CURVE_MANIFEST,
            "v5.1 national residue curve manifest",
            "national_residue_curve_v9",
        ),
        _source_record(
            root,
            HANDOFF_TIERS_CSV,
            "v5.1 OSeCAN tier positions",
            "forestry_osecan_handoff_v10",
        ),
        _source_record(
            root,
            HANDOFF_SUMMARY,
            "v5.1 OSeCAN handoff summary, labels and supply curve",
            "forestry_osecan_handoff_v10",
        ),
        _source_record(
            root,
            HANDOFF_MANIFEST,
            "v5.1 OSeCAN handoff manifest",
            "forestry_osecan_handoff_v10",
        ),
        _source_record(
            root,
            SCENARIOS_TIERS_CSV,
            "v5.1 OSeCAN scenario tier positions",
            "forestry_osecan_scenarios_v9",
        ),
        _source_record(
            root,
            SCENARIOS_SUMMARY,
            "v5.1 OSeCAN scenario summary and labels",
            "forestry_osecan_scenarios_v9",
        ),
        _source_record(
            root,
            SCENARIOS_MANIFEST,
            "v5.1 OSeCAN scenario manifest",
            "forestry_osecan_scenarios_v9",
        ),
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": ARTIFACT,
        "generated_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "labels": LABELS,
        "caveats": CAVEATS,
        "default_scenario": DEFAULT_SCENARIO,
        "coverage": {
            "record_count": len(facilities),
            "mapped_count": mapped_count,
            "unlocated_count": len(unlocated),
            "unlocated_provinces": sorted({facility["province"] for facility in unlocated}),
            "province_count": len(provinces),
            "class_count": len(classes),
            "published_count": published_count,
            "proxy_count": proxy_count,
            "not_published_count": not_published_count,
            "published_capacity_m3_y": published_capacity,
            "proxy_capacity_m3_y": proxy_capacity,
            "total_capacity_m3_y": published_capacity + proxy_capacity,
            "source_file_count": len(source_files),
        },
        "classes": classes,
        "provinces": provinces,
        "scenarios": scenarios,
        "base_assignment": base_assignment[0],
        "national_chain": _build_national_chain(root),
        "facilities": facilities,
        "source_files": source_files,
    }


def _validation_checks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    coverage = payload["coverage"]
    checks = [
        {"name": "record_count", "ok": coverage["record_count"] == 689, "detail": coverage["record_count"]},
        {
            "name": "mapped_plus_unlocated",
            "ok": coverage["mapped_count"] + coverage["unlocated_count"] == coverage["record_count"],
            "detail": {"mapped": coverage["mapped_count"], "unlocated": coverage["unlocated_count"]},
        },
        {
            "name": "class_counts",
            "ok": sum(item["record_count"] for item in payload["classes"]) == coverage["record_count"],
            "detail": [item["record_count"] for item in payload["classes"]],
        },
        {
            "name": "province_counts",
            "ok": sum(item["record_count"] for item in payload["provinces"]) == coverage["record_count"],
            "detail": [item["record_count"] for item in payload["provinces"]],
        },
        {
            "name": "scenarios",
            "ok": [item["id"] for item in payload["scenarios"]] == list(EXPECTED_SCENARIOS),
            "detail": [item["id"] for item in payload["scenarios"]],
        },
    ]
    for scenario in payload["scenarios"]:
        checks.append(
            {
                "name": f"table_conservation:{scenario['id']}",
                "ok": sum(row["record_count"] for row in scenario["rows"]) == coverage["record_count"]
                and abs(sum(row["assigned_m3_y"] for row in scenario["rows"]) - scenario["assigned_m3"]) <= 1e-3,
                "detail": scenario["assigned_m3"],
            }
        )
        checks.append(
            {
                "name": f"utilization_bounds:{scenario['id']}",
                "ok": all(
                    row["utilization"] is None or 0 <= row["utilization"] <= 1.000001
                    for row in scenario["rows"]
                ),
                "detail": max(
                    (row["utilization"] for row in scenario["rows"] if row["utilization"] is not None),
                    default=None,
                ),
            }
        )
        checks.append(
            {
                "name": f"transport_values:{scenario['id']}",
                "ok": scenario["transport_cost_cad"] > 0
                and scenario["mean_road_distance_km"] > 0
                and 0 <= scenario["share_ge_150_km"] <= 1
                and 0 <= scenario["share_ge_300_km"] <= 1,
                "detail": {
                    "transport_cost_cad": scenario["transport_cost_cad"],
                    "mean_road_distance_km": scenario["mean_road_distance_km"],
                    "share_ge_150_km": scenario["share_ge_150_km"],
                    "share_ge_300_km": scenario["share_ge_300_km"],
                },
            }
        )
    checks.append(
        {
            "name": "labels",
            "ok": payload["labels"]
            == {
                "evidence": "model_estimate",
                "observed": False,
                "policy_grade": False,
                "ranking": "none",
                "official_osecan": False,
                "read_only": True,
            },
            "detail": payload["labels"],
        }
    )
    chain = payload["national_chain"]
    checks.extend(
        [
            {
                "name": "national_chain_rows",
                "ok": [row["id"] for row in chain["rows"]] == list(EXPECTED_SCENARIOS)
                and len(chain["rows"]) == 3,
                "detail": [row["id"] for row in chain["rows"]],
            },
            {
                "name": "national_chain_total_gross",
                "ok": all(
                    abs(row["total_gross_pj"] - (row["process_pj"] + row["harvest_gross_pj"])) <= 1e-6
                    for row in chain["rows"]
                ),
                "detail": [row["total_gross_pj"] for row in chain["rows"]],
            },
            {
                "name": "national_chain_labels",
                "ok": chain["labels"]
                == {
                    "model_estimate": True,
                    "observed": False,
                    "policy_grade": False,
                    "national_claim": False,
                    "official_osecan_changed": False,
                    "transport_cost_in_osecan": False,
                },
                "detail": chain["labels"],
            },
            {
                "name": "national_chain_transport",
                "ok": len(chain["columns"]) == 8
                and all(
                    column["field"] in chain["rows"][0]
                    for column in chain["columns"]
                )
                and all(
                    row["transport_cost_cad"] > 0 and row["mean_road_distance_km"] > 0
                    for row in chain["rows"]
                ),
                "detail": {
                    "columns": [column["id"] for column in chain["columns"]],
                    "transport_cost_cad": [row["transport_cost_cad"] for row in chain["rows"]],
                    "mean_road_distance_km": [row["mean_road_distance_km"] for row in chain["rows"]],
                },
            },
            {
                "name": "national_chain_coefficients",
                "ok": chain["coefficient_provenance"]["e1"]["count"] == 4
                and chain["coefficient_provenance"]["e2c"]["count"] == 9
                and chain["coefficient_provenance"]["donor_product_coefficients"] == 0,
                "detail": chain["coefficient_provenance"],
            },
            {
                "name": "national_chain_tiers",
                "ok": {tier["stream"] for tier in chain["reference"]["tiers"]} == {"process", "harvest"}
                and all(tier["tier"] for tier in chain["reference"]["tiers"]),
                "detail": chain["reference"]["tiers"],
            },
        ]
    )
    return checks


def _dump_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def build_bundle(root: Path, out_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = root.resolve()
    out_dir = out_dir.resolve()
    payload = build_payload(root)
    payload_text = _dump_json(payload)
    payload_bytes = payload_text.encode("utf-8")
    checks = _validation_checks(payload)
    payload["validation"] = {"ok": all(check["ok"] for check in checks), "checks": checks}
    _require(payload["validation"]["ok"], "facility export validation failed")
    payload_text = _dump_json(payload)
    payload_bytes = payload_text.encode("utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / OUTPUT_FILE).write_text(payload_text, encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "artifact": ARTIFACT,
        "generated_utc": payload["generated_utc"],
        "generated": [
            {
                "path": f"facilities/{OUTPUT_FILE}",
                "bytes": len(payload_bytes),
                "sha256": _sha256_bytes(payload_bytes),
            }
        ],
        "sources": payload["source_files"],
        "labels": payload["labels"],
        "validation": payload["validation"],
    }
    (out_dir / MANIFEST_FILE).write_text(_dump_json(manifest), encoding="utf-8")
    return payload, manifest


def check_bundle(root: Path, out_dir: Path) -> dict[str, Any]:
    root = root.resolve()
    out_dir = out_dir.resolve()
    payload_path = out_dir / OUTPUT_FILE
    manifest_path = out_dir / MANIFEST_FILE
    _require(payload_path.is_file(), f"missing exported payload: {payload_path}")
    _require(manifest_path.is_file(), f"missing export manifest: {manifest_path}")
    committed = json.loads(payload_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    regenerated = build_payload(root)
    committed_core = {key: value for key, value in committed.items() if key != "validation"}
    regenerated_core = {key: value for key, value in regenerated.items() if key != "validation"}
    committed_core.pop("generated_utc", None)
    regenerated_core.pop("generated_utc", None)
    _require(committed_core == regenerated_core, "exported facilities.json is stale versus source CSVs")
    generated = manifest.get("generated", [])
    _require(len(generated) == 1 and generated[0].get("path") == f"facilities/{OUTPUT_FILE}", "manifest generated record is invalid")
    payload_bytes = payload_path.read_bytes()
    _require(generated[0]["bytes"] == len(payload_bytes), "manifest payload byte count mismatch")
    _require(generated[0]["sha256"] == _sha256_bytes(payload_bytes), "manifest payload hash mismatch")
    _require(manifest.get("validation", {}).get("ok") is True, "manifest validation is not ok")
    return {"ok": True, "bytes": len(payload_bytes), "sha256": generated[0]["sha256"]}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root, help="Repository root")
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / OUTPUT_DIR,
        help="Facilities export directory",
    )
    parser.add_argument("--check", action="store_true", help="Verify the committed export against source CSVs")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.check:
        result = check_bundle(args.root, args.out)
        print(json.dumps(result, indent=2))
        return 0
    payload, manifest = build_bundle(args.root, args.out)
    print(
        json.dumps(
            {
                "records": payload["coverage"]["record_count"],
                "mapped": payload["coverage"]["mapped_count"],
                "unlocated": payload["coverage"]["unlocated_count"],
                "scenarios": [item["id"] for item in payload["scenarios"]],
                "bytes": manifest["generated"][0]["bytes"],
                "sha256": manifest["generated"][0]["sha256"],
                "out": str(args.out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
