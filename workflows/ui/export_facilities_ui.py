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
