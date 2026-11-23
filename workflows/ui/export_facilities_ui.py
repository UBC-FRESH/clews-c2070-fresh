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

SCHEMA_VERSION = "facilities/1"
ARTIFACT = "ui_facilities_v1"
OUTPUT_DIR = Path("ui/public/data/facilities")
OUTPUT_FILE = "facilities.json"
MANIFEST_FILE = "manifest.json"

CAPACITY_DIR = Path("data/derived/national/facilities/national_facility_capacity_v3")
INVENTORY_DIR = Path("data/derived/national/facilities/national_facility_inventory_v1")
ASSIGNMENT_DIR = Path("data/derived/national/facilities/national_facility_assignment_v6")
CHAIN_DIR = Path("data/derived/national/facilities/national_facility_chain_scenarios_v6")

V4_CHAIN_DIR = Path("data/derived/national/facilities/national_facility_chain_scenarios_v7")
V4_CHAIN_SUMMARY_CSV = V4_CHAIN_DIR / "scenario_summary.csv"
V4_CHAIN_SUMMARY_JSON = V4_CHAIN_DIR / "summary.json"
V4_CHAIN_MANIFEST = V4_CHAIN_DIR / "manifest.json"

V4_PRODUCT_CHAIN_DIR = Path("data/derived/national/facilities/national_product_chain_v5")
V4_PRODUCT_CHAIN_SUMMARY = V4_PRODUCT_CHAIN_DIR / "summary.json"
V4_PRODUCT_CHAIN_MANIFEST = V4_PRODUCT_CHAIN_DIR / "manifest.json"
V4_E1_COEFFICIENTS = V4_PRODUCT_CHAIN_DIR / "e1_coefficient_replacement.csv"
V4_E2C_EVIDENCE = V4_PRODUCT_CHAIN_DIR / "e2c_density_evidence.csv"

V4_RESIDUE_DIR = Path("data/derived/national/facilities/facility_residue_supply_v7")
V4_RESIDUE_NATIONAL_CSV = V4_RESIDUE_DIR / "national_residue_summary.csv"
V4_RESIDUE_SUMMARY = V4_RESIDUE_DIR / "summary.json"
V4_RESIDUE_MANIFEST = V4_RESIDUE_DIR / "manifest.json"

V4_CURVE_DIR = Path("data/derived/national/facilities/national_residue_curve_v7")
V4_CURVE_NATIONAL_CSV = V4_CURVE_DIR / "national_residue_curve.csv"
V4_CURVE_SUMMARY = V4_CURVE_DIR / "summary.json"
V4_CURVE_MANIFEST = V4_CURVE_DIR / "manifest.json"

V4_HANDOFF_DIR = Path("data/derived/national/facilities/forestry_osecan_handoff_v8")
V4_HANDOFF_TIERS_CSV = V4_HANDOFF_DIR / "tier_positions_v3_2_vs_v4.csv"
V4_HANDOFF_SUMMARY = V4_HANDOFF_DIR / "summary.json"
V4_HANDOFF_MANIFEST = V4_HANDOFF_DIR / "manifest.json"

V4_SCENARIOS_DIR = Path("data/derived/national/facilities/forestry_osecan_scenarios_v7")
V4_SCENARIOS_SUMMARY = V4_SCENARIOS_DIR / "summary.json"
V4_SCENARIOS_MANIFEST = V4_SCENARIOS_DIR / "manifest.json"

CAPACITY_CSV = CAPACITY_DIR / "national_facility_capacity_v3.csv"
CAPACITY_SUMMARY = CAPACITY_DIR / "summary.json"
CAPACITY_MANIFEST = CAPACITY_DIR / "manifest.json"
INVENTORY_CSV = INVENTORY_DIR / "national_facilities.csv"
ASSIGNMENT_CSV = ASSIGNMENT_DIR / "facility_assignment.csv"
CHAIN_ASSIGNMENT_CSV = CHAIN_DIR / "scenario_assignment.csv"
CHAIN_SUMMARY_CSV = CHAIN_DIR / "scenario_summary.csv"
CHAIN_MANIFEST = CHAIN_DIR / "manifest.json"

EXPECTED_SCENARIOS = ("100m", "119m", "150m")
DEFAULT_SCENARIO = "119m"
BASE_ASSIGNMENT_CASE = "province_capacity_floor"

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
        "Assigned volume is a local-first planning allocation, not a legal AAC, "
        "sustainable-supply determination, or observed harvest outcome. The 1.2x "
        "provincial floor scales proxy capacity only; published capacity is never "
        "altered. Cross-border volume is only the local residual and is not a "
        "trade forecast."
    ),
    "utilization": (
        "Table utilization is assigned volume divided by eligible capacity: "
        "located, operating records after the scenario's proxy floor scaling. "
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
        "Frozen v4 national chain. E1 measured coefficients for BC/QC/NB/YT and "
        "E2c imputed coefficients for nine jurisdictions; donor product "
        "coefficients = 0. Rows 100/119/150 Mm³ are planning allocations, not "
        "AACs. Harvest 0.60 is a reference recovery scenario, not a calibrated "
        "factor. model_estimate · observed=false · policy_grade=false. No "
        "official OSeCAN change and no new proxy capacity."
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


def _assignment_rows(rows: list[dict[str, str]], scenario: str) -> dict[str, dict[str, str]]:
    selected: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("scenario") != scenario:
            continue
        facility_id = row["facility_id"]
        _require(facility_id not in selected, f"duplicate assignment for {facility_id} in {scenario}")
        selected[facility_id] = row
    return selected


def _build_scenarios(
    capacity_rows: list[dict[str, str]],
    base: dict[tuple[str, str], dict[str, Any]],
    chain_rows: list[dict[str, str]],
    chain_summary_rows: list[dict[str, str]],
    assignment_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    capacity_by_id = {row["facility_id"]: row for row in capacity_rows}
    summary_by_id = {row["scenario"]: row for row in chain_summary_rows}
    _require(set(summary_by_id) == set(EXPECTED_SCENARIOS), "chain scenario summary must cover 100m/119m/150m")

    scenarios: list[dict[str, Any]] = []
    for scenario_id in EXPECTED_SCENARIOS:
        assignments = _assignment_rows(chain_rows, scenario_id)
        _require(bool(assignments), f"no assignment rows for {scenario_id}")
        summary = summary_by_id[scenario_id]
        scale_by_province: dict[str, float] = {}
        for assignment in assignments.values():
            if not assignment["capacity_status"].startswith("proxy"):
                continue
            scale = _number(
                assignment.get("floor_scale_factor"),
                "floor_scale_factor",
                assignment["facility_id"],
                required=True,
            )
            assert scale is not None
            province = assignment["facility_province"]
            if province in scale_by_province:
                _require(
                    abs(scale_by_province[province] - scale) <= 1e-9,
                    f"{scenario_id} has inconsistent proxy floor scales for {province}",
                )
            scale_by_province[province] = scale

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
        for capacity_row in capacity_rows:
            if not capacity_row["lat"].strip() or not capacity_row["lon"].strip():
                continue
            if capacity_row["status"] in {"disused", "temporarily_closed"}:
                continue
            capacity = _number(
                capacity_row["capacity_m3_y"], "capacity_m3_y", capacity_row["facility_id"]
            ) or 0.0
            if capacity_row["capacity_status"] == "proxy":
                capacity *= scale_by_province.get(capacity_row["province"], 1.0)
            key = (capacity_row["province"], capacity_row["facility_class"])
            rows[key]["eligible_capacity_m3_y"] += capacity
        assigned_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
        for facility_id, assignment in assignments.items():
            _require(facility_id in capacity_by_id, f"assignment references unknown facility {facility_id}")
            capacity = capacity_by_id[facility_id]
            key = (capacity["province"], capacity["facility_class"])
            _require(key in rows, f"assignment class/province missing from capacity rows: {key}")
            assigned = _number(assignment["assigned_m3"], "assigned_m3", facility_id, required=True)
            assert assigned is not None
            _require(assigned >= 0, f"negative assigned volume for {facility_id}")
            row = rows[key]
            row["assigned_m3_y"] += assigned
            if assignment.get("cross_border") == "true":
                row["cross_border_m3_y"] += assigned
            else:
                row["local_assigned_m3_y"] += assigned
            if assignment["capacity_status"].startswith("published"):
                row["published_assigned_m3_y"] += assigned
            else:
                row["proxy_assigned_m3_y"] += assigned
            assignment_capacity = _number(
                assignment["capacity_m3_y"], "capacity_m3_y", facility_id, required=True
            )
            assert assignment_capacity is not None
            _require(assignment_capacity > 0, f"non-positive assignment capacity for {facility_id}")
            row["assignment_capacity_m3_y"] += assignment_capacity
            assigned_ids[key].add(facility_id)

        total_records = 0
        total_assigned = 0.0
        local_assigned = 0.0
        cross_border = 0.0
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

        _require(total_records == len(capacity_rows), f"{scenario_id} table rows do not conserve records")
        expected_assigned = _number(summary["assigned_m3"], "assigned_m3", scenario_id, required=True)
        assert expected_assigned is not None
        _require(
            abs(total_assigned - expected_assigned) <= 1e-3,
            f"{scenario_id} assigned total {total_assigned} does not match chain summary {expected_assigned}",
        )
        expected_harvest = _number(summary["harvest_m3"], "harvest_m3", scenario_id, required=True)
        assert expected_harvest is not None
        expected_facilities = _integer(summary["facilities_used"], "facilities_used", scenario_id)
        _require(
            len(assignments) == expected_facilities,
            f"{scenario_id} facility count {len(assignments)} does not match chain summary {expected_facilities}",
        )
        eligible_total = sum(row["eligible_capacity_m3_y"] for row in rows.values())
        expected_utilization = _number(
            summary["capacity_utilization"], "capacity_utilization", scenario_id, required=True
        )
        assert expected_utilization is not None
        _require(
            abs((total_assigned / eligible_total) - expected_utilization) <= 1e-6,
            f"{scenario_id} utilization does not reproduce the chain summary",
        )

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
                "unassigned_m3": _number(summary["unassigned_m3"], "unassigned_m3", scenario_id) or 0.0,
                "capacity_utilization": _number(
                    summary["capacity_utilization"], "capacity_utilization", scenario_id, required=True
                ),
                "facilities_used": expected_facilities,
                "capacity_case": summary["capacity_case"],
                "source_artifact": "national_facility_chain_scenarios_v6",
                "rows": rows_sorted,
            }
        )

    # Assignment v6 is the canonical 119 Mm3 local-processing base.  It must
    # reproduce the 119m chain-assignment facility set and volume exactly.
    base_assignments = _assignment_rows(assignment_rows, BASE_ASSIGNMENT_CASE)
    chain_119 = _assignment_rows(chain_rows, DEFAULT_SCENARIO)
    _require(
        set(base_assignments) == set(chain_119),
        "assignment v6 province_capacity_floor facility set does not match chain v6 119m",
    )
    for facility_id, base_row in base_assignments.items():
        chain_row = chain_119[facility_id]
        _require(
            abs(float(base_row["assigned_m3"]) - float(chain_row["assigned_m3"])) <= 1e-3,
            f"assignment v6 / chain v6 119m mismatch for {facility_id}",
        )
    return scenarios, [
        {
            "id": DEFAULT_SCENARIO,
            "artifact": "national_facility_assignment_v6",
            "capacity_case": BASE_ASSIGNMENT_CASE,
            "facilities": len(base_assignments),
            "assigned_m3": sum(float(row["assigned_m3"]) for row in base_assignments.values()),
        }
    ]


def _require_model_labels(labels: dict[str, Any], context: str) -> None:
    _require(labels.get("model_estimate") is True, f"{context} must be a model estimate")
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
        _require(document.get("status") == "pass", f"{context} status is not pass")


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
    chain_summary = _load_json(_query(root, V4_CHAIN_SUMMARY_JSON))
    chain_manifest = _load_json(_query(root, V4_CHAIN_MANIFEST))
    product_summary = _load_json(_query(root, V4_PRODUCT_CHAIN_SUMMARY))
    product_manifest = _load_json(_query(root, V4_PRODUCT_CHAIN_MANIFEST))
    residue_summary = _load_json(_query(root, V4_RESIDUE_SUMMARY))
    residue_manifest = _load_json(_query(root, V4_RESIDUE_MANIFEST))
    curve_summary = _load_json(_query(root, V4_CURVE_SUMMARY))
    curve_manifest = _load_json(_query(root, V4_CURVE_MANIFEST))
    handoff_summary = _load_json(_query(root, V4_HANDOFF_SUMMARY))
    handoff_manifest = _load_json(_query(root, V4_HANDOFF_MANIFEST))
    scenarios_summary = _load_json(_query(root, V4_SCENARIOS_SUMMARY))
    scenarios_manifest = _load_json(_query(root, V4_SCENARIOS_MANIFEST))

    _require_artifact(
        chain_summary,
        "national_facility_chain_scenarios_v7",
        "7.0.0",
        "national chain v7 summary",
    )
    _require_artifact(
        chain_manifest,
        "national_facility_chain_scenarios_v7",
        "7.0.0",
        "national chain v7 manifest",
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
        "facility_residue_supply_v7",
        "7.0.0",
        "facility residue supply v7 summary",
    )
    _require_artifact(
        residue_manifest,
        "facility_residue_supply_v7",
        "7.0.0",
        "facility residue supply v7 manifest",
    )
    _require_artifact(
        curve_summary,
        "national_residue_curve_v7",
        "7.0.0",
        "national residue curve v7 summary",
    )
    _require_artifact(
        curve_manifest,
        "national_residue_curve_v7",
        "7.0.0",
        "national residue curve v7 manifest",
    )
    _require_artifact(
        handoff_summary,
        "forestry_osecan_handoff_v8",
        "8.0.0",
        "OSeCAN handoff v8 summary",
    )
    _require_artifact(
        handoff_manifest,
        "forestry_osecan_handoff_v8",
        "8.0.0",
        "OSeCAN handoff v8 manifest",
    )
    _require_artifact(
        scenarios_summary,
        "forestry_osecan_scenarios_v7",
        "7.0.0",
        "OSeCAN scenarios v7 summary",
    )
    _require_artifact(
        scenarios_manifest,
        "forestry_osecan_scenarios_v7",
        "7.0.0",
        "OSeCAN scenarios v7 manifest",
    )

    labels = chain_summary.get("labels") or {}
    _require_model_labels(labels, "national chain v7 summary")
    for context, document in (
        ("national chain v7 manifest", chain_manifest),
        ("national product chain v5 summary", product_summary),
        ("national product chain v5 manifest", product_manifest),
        ("facility residue supply v7 summary", residue_summary),
        ("facility residue supply v7 manifest", residue_manifest),
        ("national residue curve v7 summary", curve_summary),
        ("national residue curve v7 manifest", curve_manifest),
        ("OSeCAN handoff v8 summary", handoff_summary),
        ("OSeCAN handoff v8 manifest", handoff_manifest),
        ("OSeCAN scenarios v7 summary", scenarios_summary),
        ("OSeCAN scenarios v7 manifest", scenarios_manifest),
    ):
        _require_model_labels(document.get("labels") or {}, context)

    _require(
        chain_summary.get("scenario_order") == list(EXPECTED_SCENARIOS),
        "national chain v7 scenario order is not 100m/119m/150m",
    )
    _require(chain_summary.get("primary_scenario") == DEFAULT_SCENARIO, "national chain v7 reference is not 119m")
    product_binding = chain_summary.get("product_chain") or {}
    _require(
        product_binding.get("artifact") == "national_product_chain_v5",
        "national chain v7 does not bind product chain v5",
    )
    _require(product_binding.get("donor_product_provinces") == [], "donor product provinces must stay empty")
    _require(
        product_binding.get("recovery_scenarios") == [0.35, 0.6, 0.75],
        "national chain v7 recovery scenarios changed",
    )

    measured_provinces = product_summary.get("measured_provinces")
    imputed_provinces = product_summary.get("imputed_provinces")
    _require(isinstance(measured_provinces, list) and len(measured_provinces) == 4, "E1 measured province count is not four")
    _require(isinstance(imputed_provinces, list) and len(imputed_provinces) == 9, "E2c imputed province count is not nine")
    _require(set(measured_provinces).isdisjoint(imputed_provinces), "E1 and E2c province sets overlap")
    _require(
        set(measured_provinces) | set(imputed_provinces) == set(PROVINCE_LABELS),
        "E1/E2c province sets do not cover all 13 jurisdictions",
    )
    _require(product_summary.get("donor_provinces") == [], "product chain v5 donor provinces must stay empty")
    _require(
        set(product_binding.get("measured_provinces") or []) == set(measured_provinces),
        "chain v7 measured provinces do not match product chain v5",
    )
    _require(
        set(product_binding.get("density_calibrated_provinces") or []) == set(imputed_provinces),
        "chain v7 E2c provinces do not match product chain v5",
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
        "chain v7 coefficient tiers do not match product chain v5",
    )

    e1_rows = _read_csv(_query(root, V4_E1_COEFFICIENTS))
    e1_provinces = {row["province"] for row in e1_rows}
    _require(e1_provinces == set(measured_provinces), "E1 coefficient rows do not cover the measured provinces")
    _require(
        all(row["tier"] == "measured_bucking_pilot" for row in e1_rows),
        "E1 coefficient rows carry an unexpected tier",
    )
    e2c_rows = _read_csv(_query(root, V4_E2C_EVIDENCE))
    e2c_provinces = {row["province"] for row in e2c_rows}
    _require(e2c_provinces == set(imputed_provinces), "E2c evidence rows do not cover the imputed provinces")
    _require(
        all(
            row["coefficient_tier"] == "E2c_open_inventory_density_calibrated"
            for row in e2c_rows
        ),
        "E2c evidence rows carry an unexpected tier",
    )

    chain_rows = _read_csv(_query(root, V4_CHAIN_SUMMARY_CSV))
    _require(
        [row["scenario"] for row in chain_rows] == list(EXPECTED_SCENARIOS),
        "national chain v7 CSV must carry 100m/119m/150m in order",
    )
    scenario_details = chain_summary.get("scenarios") or {}
    _require(
        list(scenario_details) == list(EXPECTED_SCENARIOS),
        "national chain v7 summary scenarios are not 100m/119m/150m",
    )
    rows: list[dict[str, Any]] = []
    for source_row in chain_rows:
        scenario_id = source_row["scenario"]
        detail = scenario_details[scenario_id]
        for field in (
            "harvest_m3",
            "sawlog_product_m3",
            "panel_product_m3",
            "pulp_product_odt",
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
                f"national chain v7 CSV/summary mismatch for {scenario_id}.{field}",
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
        _require(solid > 0 and pulp > 0 and process >= 0 and harvest_gross > 0, f"{scenario_id} chain value is not positive")
        _require(harvest_recoverable >= 0 and total_gross > 0 and harvest > 0, f"{scenario_id} chain total is not positive")
        _require(
            _close(total_gross, process + harvest_gross),
            f"{scenario_id} total gross PJ is not process plus harvest gross",
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
                "_harvest_gross_odt": harvest_gross_odt,
                "_harvest_recoverable_odt": harvest_recoverable_odt,
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

    residue_rows = _read_csv(_query(root, V4_RESIDUE_NATIONAL_CSV))
    residue_totals: dict[str, float] = {}
    for row in residue_rows:
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
        _require(metric in residue_totals, f"facility residue v7 is missing {metric}")
        _require(
            _close(residue_totals[metric], reference[field]),
            f"facility residue v7 {metric} does not match the 119 Mm³ chain",
        )

    curve_rows = _read_csv(_query(root, V4_CURVE_NATIONAL_CSV))
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
        "residue curve v7 process 50% does not apply the reference availability fraction",
    )
    _require(
        _close(harvest_curve_pj, reference["harvest_recoverable_pj"]),
        "residue curve v7 harvest 60% does not match the 119 Mm³ chain",
    )
    for curve_row, context in (
        (process_curve, "process_availability_050"),
        (harvest_curve, "harvest_recovery_060"),
    ):
        _require(curve_row["model_estimate"] == "true", f"residue curve v7 {context} is not a model estimate")
        _require(curve_row["observed"] == "false", f"residue curve v7 {context} observed must be false")
        _require(curve_row["policy_grade"] == "false", f"residue curve v7 {context} policy_grade must be false")
    for row in rows:
        gross_odt = row["_harvest_gross_odt"]
        recoverable_odt = row["_harvest_recoverable_odt"]
        _require(gross_odt > 0, f"{row['id']} harvest gross odt is not positive")
        _require(
            _close(recoverable_odt, gross_odt * harvest_fraction),
            f"{row['id']} harvest recoverable does not apply the reference recovery fraction",
        )
        _require(
            _close(row["harvest_recoverable_pj"], row["harvest_gross_pj"] * harvest_fraction),
            f"{row['id']} harvest recoverable PJ does not apply the reference recovery fraction",
        )

    handoff_tier_rows = _read_csv(_query(root, V4_HANDOFF_TIERS_CSV))
    _require(len(handoff_tier_rows) == 7, "OSeCAN handoff v8 tier table must carry seven cases")
    handoff_tiers = {row["case"]: row for row in handoff_tier_rows}
    _require(len(handoff_tiers) == len(handoff_tier_rows), "duplicate case in OSeCAN handoff v8 tier table")
    supply_curve = handoff_summary.get("supply_curve") or {}
    case_tier = supply_curve.get("case_tier") or {}
    upper_pj = _number(str(supply_curve.get("upper_pj", "")), "upper_pj", "OSeCAN handoff v8", required=True)
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
        handoff_row = handoff_tiers.get(case)
        _require(handoff_row is not None, f"OSeCAN handoff v8 is missing tier case {case}")
        _require(handoff_row["stream"] == stream, f"OSeCAN handoff v8 {case} stream mismatch")
        _require(handoff_row["model_estimate"] == "true", f"OSeCAN handoff v8 {case} is not a model estimate")
        _require(handoff_row["observed"] == "false", f"OSeCAN handoff v8 {case} observed must be false")
        _require(handoff_row["policy_grade"] == "false", f"OSeCAN handoff v8 {case} policy_grade must be false")
        tier = handoff_row["v4_e2c_tier"]
        _require(bool(tier), f"OSeCAN handoff v8 {case} has no tier")
        _require(case_tier.get(f"{stream}/{case}") == tier, f"OSeCAN handoff v8 {case} tier is inconsistent")
        if tier == "above_T4":
            _require(
                tier_case_values[case] > upper_pj,
                f"OSeCAN handoff v8 {case} says above_T4 but the 119 Mm³ case value is not",
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

    scenarios_tiers = scenarios_summary.get("tier_positions_v3_2_vs_v4") or []
    _require(len(scenarios_tiers) == 21, "OSeCAN scenarios v7 must carry 21 tier positions")
    scenario_tier_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in scenarios_tiers:
        key = (row["scenario"], row["stream"], row["case"])
        _require(key not in scenario_tier_index, f"duplicate OSeCAN scenario tier position {key}")
        scenario_tier_index[key] = row
    for row in rows:
        for stream, case, field, fraction in tier_specs:
            scenario_tier = scenario_tier_index.get((row["id"], stream, case))
            _require(scenario_tier is not None, f"OSeCAN scenarios v7 is missing {row['id']}/{stream}/{case}")
            scenario_pj = _number(
                str(scenario_tier.get("v4_e2c_pj", "")),
                "v4_e2c_pj",
                f"{row['id']}/{case}",
                required=True,
            )
            assert scenario_pj is not None
            expected_case_pj = row[field] * fraction if stream == "process" else row[field]
            _require(
                _close(scenario_pj, expected_case_pj),
                f"OSeCAN scenarios v7 {row['id']}/{case} does not match the chain",
            )
            _require(bool(scenario_tier.get("v4_e2c_tier")), f"OSeCAN scenarios v7 {row['id']}/{case} has no tier")
    for stream, case, _field, _fraction in tier_specs:
        reference_tier = scenario_tier_index[(DEFAULT_SCENARIO, stream, case)]["v4_e2c_tier"]
        handoff_tier = handoff_tiers[case]["v4_e2c_tier"]
        _require(
            reference_tier == handoff_tier,
            f"OSeCAN scenarios v7 119 Mm³ {case} tier disagrees with handoff v8",
        )

    row_payloads = []
    for row in rows:
        row_payloads.append(
            {
                key: value
                for key, value in row.items()
                if not key.startswith("_")
            }
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
        ],
        "rows": row_payloads,
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
    chain_rows = _read_csv(_query(root, CHAIN_ASSIGNMENT_CSV))
    chain_summary_rows = _read_csv(_query(root, CHAIN_SUMMARY_CSV))
    assignment_rows = _read_csv(_query(root, ASSIGNMENT_CSV))
    scenarios, base_assignment = _build_scenarios(
        capacity_rows, base, chain_rows, chain_summary_rows, assignment_rows
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
        assignments = _assignment_rows(chain_rows, scenario["id"])
        scenario_facilities[scenario["id"]] = {
            facility_id: {
                "assigned_m3_y": _number(row["assigned_m3"], "assigned_m3", facility_id, required=True),
                "capacity_status": row["capacity_status"],
                "utilization": _number(row["utilization"], "utilization", facility_id),
                "cross_border": row.get("cross_border") == "true",
                "floor_scale_factor": _number(row.get("floor_scale_factor"), "floor_scale_factor", facility_id),
            }
            for facility_id, row in assignments.items()
        }
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
        _source_record(root, ASSIGNMENT_CSV, "119 Mm3 local-processing assignment", "national_facility_assignment_v6"),
        _source_record(root, CHAIN_ASSIGNMENT_CSV, "100/119/150 Mm3 assignments", "national_facility_chain_scenarios_v6"),
        _source_record(root, CHAIN_SUMMARY_CSV, "scenario utilization and totals", "national_facility_chain_scenarios_v6"),
        _source_record(root, CHAIN_MANIFEST, "chain scenario manifest", "national_facility_chain_scenarios_v6"),
        _source_record(
            root,
            V4_CHAIN_SUMMARY_CSV,
            "v4 national chain scenario products and residue",
            "national_facility_chain_scenarios_v7",
        ),
        _source_record(
            root,
            V4_CHAIN_SUMMARY_JSON,
            "v4 national chain summary, labels and coefficient tiers",
            "national_facility_chain_scenarios_v7",
        ),
        _source_record(
            root,
            V4_CHAIN_MANIFEST,
            "v4 national chain manifest",
            "national_facility_chain_scenarios_v7",
        ),
        _source_record(
            root,
            V4_PRODUCT_CHAIN_SUMMARY,
            "v4 product chain coefficient tiers and labels",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            V4_PRODUCT_CHAIN_MANIFEST,
            "v4 product chain manifest",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            V4_E1_COEFFICIENTS,
            "v4 E1 measured coefficient rows",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            V4_E2C_EVIDENCE,
            "v4 E2c imputed coefficient evidence",
            "national_product_chain_v5",
        ),
        _source_record(
            root,
            V4_RESIDUE_NATIONAL_CSV,
            "v4 119 Mm3 national residue totals",
            "facility_residue_supply_v7",
        ),
        _source_record(
            root,
            V4_RESIDUE_SUMMARY,
            "v4 facility residue summary and labels",
            "facility_residue_supply_v7",
        ),
        _source_record(
            root,
            V4_RESIDUE_MANIFEST,
            "v4 facility residue manifest",
            "facility_residue_supply_v7",
        ),
        _source_record(
            root,
            V4_CURVE_NATIONAL_CSV,
            "v4 national residue curve rows",
            "national_residue_curve_v7",
        ),
        _source_record(
            root,
            V4_CURVE_SUMMARY,
            "v4 national residue curve summary and labels",
            "national_residue_curve_v7",
        ),
        _source_record(
            root,
            V4_CURVE_MANIFEST,
            "v4 national residue curve manifest",
            "national_residue_curve_v7",
        ),
        _source_record(
            root,
            V4_HANDOFF_TIERS_CSV,
            "v4 OSeCAN tier positions",
            "forestry_osecan_handoff_v8",
        ),
        _source_record(
            root,
            V4_HANDOFF_SUMMARY,
            "v4 OSeCAN handoff summary, labels and supply curve",
            "forestry_osecan_handoff_v8",
        ),
        _source_record(
            root,
            V4_HANDOFF_MANIFEST,
            "v4 OSeCAN handoff manifest",
            "forestry_osecan_handoff_v8",
        ),
        _source_record(
            root,
            V4_SCENARIOS_SUMMARY,
            "v4 OSeCAN scenario tier positions and labels",
            "forestry_osecan_scenarios_v7",
        ),
        _source_record(
            root,
            V4_SCENARIOS_MANIFEST,
            "v4 OSeCAN scenario manifest",
            "forestry_osecan_scenarios_v7",
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
                },
                "detail": chain["labels"],
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
