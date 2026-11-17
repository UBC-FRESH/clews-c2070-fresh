"""Run the TSA24/BC Stage A facility-assignment pilot.

Examples
--------
Base diagnostic (1,000 ha)::

    .venv/bin/python -m workflows.national.assignments.run_tsa24

Base plus the optional ws4-quota sensitivity::

    .venv/bin/python -m workflows.national.assignments.run_tsa24 --sensitivity-ws4-quota

The base run is a contract and data-shape pilot, not a TSA supply estimate.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .fixtures import (
    Tsa24Fixture,
    build_tsa24_fixture,
    write_fixture_tables,
)
from .model import solve_assignment
from .schema import AssignmentInputs, AssignmentResult, to_stage_b_tables
from .validate import ValidationReport, validate_assignment

__all__ = ["main", "run_pilot"]

CODE_VERSION = "0.1.1"
DEFAULT_OUT_DIR = Path("data/derived/national/assignments/tsa24_pilot")
DEFAULT_AREA_HA = 1000.0
WS4_QUOTA_AREA_HA = 306047.3
DEFAULT_RECOVERED_COORDINATES = (
    DEFAULT_OUT_DIR / "facilities_bc_recovered.csv"
)
DEFAULT_DIAGNOSTIC_CONTRACTS = (
    DEFAULT_OUT_DIR / "contracts_bc_diagnostic.csv"
)

ASSIGNMENT_COLUMNS = [
    "assignment_id",
    "period",
    "supply_id",
    "origin",
    "grade_id",
    "facility_id",
    "facility_name",
    "facility_class",
    "volume_m3",
    "moisture_wet_basis",
    "supply_cost_cad_m3",
    "transport_cost_cad_m3",
    "gate_cost_cad_m3",
    "processing_cost_cad_m3",
    "product_revenue_cad_m3",
    "net_contribution_cad_m3",
    "contribution_cad",
    "transport_link_id",
    "evidence",
]
UTILIZATION_COLUMNS = [
    "facility_id",
    "facility_name",
    "facility_class",
    "nameplate_m3_y",
    "assigned_m3",
    "unused_capacity_m3",
    "utilization",
    "capacity_binding",
    "eligible_grades",
]
GRADE_UTILIZATION_COLUMNS = [
    "facility_id",
    "facility_name",
    "facility_class",
    "grade_id",
    "grade_capacity_m3_y",
    "assigned_m3",
    "utilization",
    "binding",
]
CONTRACT_COLUMNS = [
    "contract_id",
    "facility_id",
    "period",
    "accepted_grades",
    "min_volume_m3",
    "max_volume_m3",
    "assigned_m3",
    "within_minimum",
    "within_maximum",
    "gate_cost_cad_m3",
    "diagnostic",
    "contract_source",
    "evidence",
    "notes",
]
UNASSIGNED_COLUMNS = [
    "supply_id",
    "period",
    "grade_id",
    "origin",
    "volume_m3",
    "reason",
    "penalty_cost_cad_m3",
]
OBJECTIVE_COLUMNS = ["component", "value_cad", "unit", "notes"]
FACILITY_COLUMNS = [
    "facility_id",
    "facility_class",
    "name",
    "lat",
    "lon",
    "coord_source",
    "coord_confidence",
    "coord_evidence",
    "match_method",
    "match_confidence",
    "nameplate_m3_y",
    "capacity_status",
    "capacity_basis",
    "line_count",
    "lines",
    "stage_a_scope",
    "transport_link_status",
]
TRANSPORT_BC_COLUMNS = [
    "origin",
    "destination",
    "grade_id",
    "mode",
    "distance_km",
    "linehaul_cost_cad_m3",
    "transload_cost_cad_m3",
    "cost_cad_m3",
    "capacity_m3",
    "capacity_source",
    "path_confidence",
    "path_nodes",
    "path_legs",
    "transload_nodes",
    "anchor_id",
    "anchor_basis",
    "distance_basis",
    "cost_basis",
    "evidence",
    "snap_m",
    "network_source",
]
TRANSPORT_SCREEN_COLUMNS = [
    "facility_id",
    "facility_class",
    "status",
    "path_confidence",
    "nearest_tl",
    "access_km",
    "facility_leg_km",
    "distance_km",
    "mode",
    "cost_cad_m3_min",
    "cost_cad_m3_max",
    "reason",
]


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fmt(value: float, digits: int = 0) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{float(value):,.{digits}f}"


def _pct(value: float, digits: int = 1) -> str:
    if value is None or not math.isfinite(float(value)):
        return "n/a"
    return f"{100.0 * float(value):.{digits}f}%"


def _markdown_table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(value) for value in row) + " |")
    return "\n".join(lines)


def _stat_line(stats: Mapping[str, Any], digits: int = 1) -> str:
    if not stats or int(stats.get("count", 0)) == 0:
        return "n/a"
    return (
        f"n={stats['count']}; min={_fmt(stats['min'], digits)}; "
        f"p25={_fmt(stats['p25'], digits)}; median={_fmt(stats['median'], digits)}; "
        f"p75={_fmt(stats['p75'], digits)}; max={_fmt(stats['max'], digits)}; "
        f"mean={_fmt(stats['mean'], digits)}"
    )


def _previous_pilot_class_mix(out_dir: Path) -> dict[str, float] | None:
    """Return the prior run's assigned-volume share by class.

    The baseline is captured once, before the new run overwrites
    ``facility_utilization.csv``.  Keeping it as a small CSV makes the
    comparison auditable across reruns.
    """
    baseline_path = out_dir / "previous_pilot_class_mix.csv"
    if baseline_path.exists():
        with baseline_path.open(newline="", encoding="utf-8") as handle:
            return {
                str(row["facility_class"]): float(row["assigned_share"])
                for row in csv.DictReader(handle)
            }
    utilization_path = out_dir / "facility_utilization.csv"
    if not utilization_path.exists():
        return None
    with utilization_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    total = sum(float(row["assigned_m3"]) for row in rows)
    by_class: dict[str, float] = {}
    for row in rows:
        facility_class = str(row["facility_class"]).strip().upper()
        by_class[facility_class] = by_class.get(facility_class, 0.0) + float(
            row["assigned_m3"]
        )
    baseline_rows = [
        {
            "facility_class": facility_class,
            "assigned_m3": volume,
            "assigned_share": volume / total if total > 0 else 0.0,
        }
        for facility_class, volume in sorted(by_class.items())
    ]
    _write_csv(
        baseline_path,
        baseline_rows,
        ["facility_class", "assigned_m3", "assigned_share"],
    )
    return {
        row["facility_class"]: float(row["assigned_share"])
        for row in baseline_rows
    }


def _coverage_tables(config: Mapping[str, Any]) -> str:
    coverage = config.get("facility_coverage", {})
    counts = coverage.get("coordinate_source_counts", {})
    in_scope = coverage.get("coordinate_source_counts_in_scope", {})
    rows = [
        (
            source,
            counts.get(source, 0),
            in_scope.get(source, 0),
            {
                "mills_db_2015": "high/medium",
                "suppliers_dbf": "high",
                "tl_anchor_proxy": "low",
                "missing": "missing",
            }[source],
        )
        for source in (
            "mills_db_2015",
            "suppliers_dbf",
            "tl_anchor_proxy",
            "missing",
        )
    ]
    table = _markdown_table(
        ["coord_source", "All BC facilities", "Stage A scope", "Expected confidence"],
        rows,
    )
    missing_in_scope = coverage.get("missing_coordinates_in_scope", [])
    missing_all = coverage.get("missing_coordinates", [])
    missing_text = (
        f"{len(missing_in_scope)} Stage A facilities have no coordinate; "
        f"{len(missing_all)} of all BC facilities are missing. "
        "Missing coordinates are not replaced with Euclidean guesses."
    )
    missing_list = (
        ", ".join(f"`{item}`" for item in missing_in_scope[:25])
        + (" ..." if len(missing_in_scope) > 25 else "")
        if missing_in_scope
        else "None."
    )
    anchor_rows = [
        (
            row["hub"],
            f"{row['lat']:.4f}",
            f"{row['lon']:.4f}",
            row["geocoded_facility_count"],
            row["geocoded_facilities"],
        )
        for row in coverage.get("anchor_hubs", [])
    ]
    anchor_table = (
        _markdown_table(
            ["Hub", "Lat", "Lon", "Geocoded anchors", "Anchor facilities"],
            anchor_rows,
        )
        if anchor_rows
        else "No TL/water hub had a geocoded facility anchor."
    )
    zero = coverage.get("zero_capacity_facilities", [])
    zero_text = (
        "None; every BC facility has a positive normalized nameplate."
        if not zero
        else f"{len(zero)} zero/blank-capacity facilities kept and flagged: "
        + ", ".join(f"`{item}`" for item in zero)
    )
    method_counts = coverage.get("match_method_counts", {})
    confidence_counts = coverage.get("match_confidence_counts", {})
    match_table = _markdown_table(
        ["Match method", "Facilities"],
        sorted(method_counts.items()) or [("none", 0)],
    )
    confidence_table = _markdown_table(
        ["Match confidence", "Facilities"],
        sorted(confidence_counts.items()) or [("none", 0)],
    )
    return f"""{table}

{missing_text}

Mills DB 2015 recovery method (code matches are corroborated; collisions rejected):

{match_table}

Recovery confidence:

{confidence_table}

Missing Stage A coordinates: {missing_list}

TL/water hub proxies used by `coord_source=tl_anchor_proxy`:

{anchor_table}

Zero/blank-capacity facilities: {zero_text}
"""


def _transport_report(config: Mapping[str, Any]) -> str:
    summary = config.get("transport_summary", {})
    comparison = summary.get("diagnostic_comparison", {})
    confidence = summary.get("confidence_counts", {})
    modes = summary.get("mode_counts", {})
    confidence_table = _markdown_table(
        ["Path confidence", "Facilities"],
        [
            (key, value)
            for key, value in sorted(confidence.items())
        ]
        or [("none", 0)],
    )
    mode_table = _markdown_table(
        ["Mode", "Facilities"],
        [(key, value) for key, value in sorted(modes.items())] or [("none", 0)],
    )
    distance_table = _markdown_table(
        ["Distance set", "Distribution (km)"],
        [
            ("New FFCM graph", _stat_line(comparison.get("new_distance_km", {}))),
            (
                "Previous 150 km diagnostic",
                _stat_line(comparison.get("old_diagnostic_distance_km", {})),
            ),
            ("Delta new - old", _stat_line(comparison.get("delta_km", {}))),
            ("Ratio new / old", _stat_line(comparison.get("ratio", {}), 2)),
        ],
    )
    cost_rows = []
    for grade, values in comparison.get("cost", {}).items():
        cost_rows.append(
            (
                grade,
                _stat_line(values.get("new", {}), 2),
                _stat_line(values.get("old", {}), 2),
                _stat_line(values.get("delta", {}), 2),
            )
        )
    cost_table = (
        _markdown_table(
            ["Grade", "New graph CAD/m3", "Previous diagnostic CAD/m3", "Delta CAD/m3"],
            cost_rows,
        )
        if cost_rows
        else "No comparable cost rows."
    )
    outlier_rows = comparison.get("outliers", [])[:10]
    outlier_table = (
        _markdown_table(
            [
                "Facility",
                "Class",
                "Old km",
                "New km",
                "Delta km",
                "Ratio",
                "Confidence",
            ],
            [
                (
                    row["facility_id"],
                    row["facility_class"],
                    _fmt(row["old_diagnostic_km"], 1),
                    _fmt(row["new_graph_km"], 1),
                    _fmt(row["delta_km"], 1),
                    _fmt(row["ratio"], 2) if row["ratio"] != "" else "n/a",
                    row["path_confidence"],
                )
                for row in outlier_rows
            ],
        )
        if outlier_rows
        else "No distance outliers."
    )
    capacity = summary.get("capacity", {})
    capacity_text = (
        f"{capacity.get('capacity_none_count', 0)} links have "
        f"`capacity=None`; `capacity_source=absent`; route capacity table="
        f"`{capacity.get('route_capacity_table', 'absent')}`."
    )
    real_road = summary.get("real_road", {})
    fallback = summary.get("ffcm_fallback", {})
    real_road_text = (
        f"- Real NRN road links: {real_road.get('link_count', 0)} grade links "
        f"across {real_road.get('facility_count', 0)} facilities "
        f"(`path_confidence=real_road_network`).\n"
        f"- FFCM fallback links: {fallback.get('link_count', 0)} grade links "
        f"across {fallback.get('facility_count', 0)} facilities "
        f"(`status=linked_ffcm_fallback_no_real_road_path`).\n"
        if real_road
        else ""
    )
    return f"""- Accepted links: {summary.get('accepted_link_count', 0)} across
  {summary.get('accepted_facility_count', 0)} facilities.
{real_road_text}- TSA24 anchor: `{summary.get('anchor', {}).get('supply_node_id', '')}` with
  anchor proxy `{summary.get('anchor', {}).get('anchor_id', '')}`.
  Access TL nodes:
  {", ".join(summary.get('anchor', {}).get('access_tl_nodes', [])) or "none"}.
- Anchor basis: `{summary.get('anchor', {}).get('anchor_basis', '')}`.
- {capacity_text}

No accepted link uses `access_leg_plus_graph`: the anchor is attached through
parsed route edges from `BC-SAW-506`. That label is reserved for a future
non-route access leg.

{confidence_table}

{mode_table}

{distance_table}

{cost_table}

Distance/cost outliers versus the previous diagnostic (top 10 by delta):

{outlier_table}
"""


def _units_report(config: Mapping[str, Any]) -> str:
    registry = config.get("unit_registry", [])
    table = _markdown_table(
        ["Unit", "Dimension", "Accepted", "Source"],
        [
            (
                row["unit"],
                row["dimension"],
                row["accepted"],
                row["source"],
            )
            for row in registry
        ],
    )
    factors = _markdown_table(
        ["Conversion", "Factor", "Condition/source"],
        [
            ("m3_green -> odt", "0.45", "declared basic density; default ledger density"),
            ("odt -> m3_green", "2.222...", "1 / 0.45; per-grade/species override allowed"),
            ("bdmt -> odt", "1.0", "dry-mass equivalence; FFCM unit registry candidate"),
            ("admt -> odt", "1 - moisture", "requires declared air-dry moisture"),
            ("mbf -> m3_green", "recovery", "requires declared m3 per mbf; rejected otherwise"),
            ("kWh -> MWh", "0.001", "SI"),
            ("MWh -> GJ", "3.6", "SI"),
            ("energy -> volume/mass", "rejected", "no declared energy density"),
        ],
    )
    return f"""{table}

Declared factors:

{factors}
"""


def _class_mix_table(
    config: Mapping[str, Any],
    class_rows: Sequence[Mapping[str, Any]],
    previous_mix: Mapping[str, float] | None,
) -> str:
    ffcm = config.get("ffcm_bc_class_mix", {}).get("classes", {})
    pilot = {row["facility_class"]: row for row in class_rows}
    total = sum(row["assigned_m3"] for row in class_rows)
    table_rows = []
    for facility_class in ("SAW", "PAP", "PEL", "BIO"):
        row = pilot.get(facility_class, {})
        assigned = float(row.get("assigned_m3", 0.0))
        table_rows.append(
            (
                facility_class,
                _fmt(assigned),
                _pct(assigned / total if total > 0 else 0.0),
                _pct(
                    previous_mix.get(facility_class, 0.0)
                    if previous_mix is not None
                    else 0.0
                )
                if previous_mix is not None
                else "n/a",
                _pct(ffcm.get(facility_class, {}).get("nameplate_share", 0.0)),
                _pct(ffcm.get(facility_class, {}).get("facility_share", 0.0)),
            )
        )
    return _markdown_table(
        [
            "Class",
            "Pilot assigned m3",
            "Pilot share",
            "Previous pilot share",
            "FFCM BC nameplate share",
            "FFCM BC facility-count share",
        ],
        table_rows,
    )


def _ffcm_class_mix(repo_root: Path) -> dict[str, float] | None:
    path = repo_root / "metadata/ffcm-archive/lp-forensics/lp-variable-families.csv"
    if not path.exists():
        return None
    counts: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("source_class", "")).strip().lower() != "fmu":
                continue
            destination = str(row.get("destination_class", "")).strip().upper()
            if destination not in {"SAW", "PAP", "PEL", "BIO"}:
                continue
            counts[destination] = counts.get(destination, 0.0) + float(
                row.get("variable_count", 0.0) or 0.0
            )
    total = sum(counts.values())
    if total <= 0:
        return None
    return {key: value / total for key, value in counts.items()}


def _grade_summary(
    inputs: AssignmentInputs, result: AssignmentResult
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for grade in inputs.grades:
        supplies = [item for item in inputs.supplies if item.grade_id == grade.grade_id]
        supplied = sum(item.volume_m3 for item in supplies)
        assigned = sum(
            item.volume_m3
            for item in result.assignments
            if item.grade_id == grade.grade_id
        )
        unassigned = sum(
            item.volume_m3
            for item in result.unassigned
            if item.grade_id == grade.grade_id
        )
        cost_values = [item.roadside_cost_cad_m3 for item in supplies]
        rows.append(
            {
                "grade_id": grade.grade_id,
                "product": grade.product,
                "unit": grade.unit,
                "offer_count": len(supplies),
                "supplied_m3": supplied,
                "supplied_odt": sum(item.volume_odt for item in supplies),
                "assigned_m3": assigned,
                "unassigned_m3": unassigned,
                "assigned_share": assigned / supplied if supplied > 0 else 0.0,
                "unassigned_share": unassigned / supplied if supplied > 0 else 0.0,
                "roadside_cost_min_cad_m3": min(cost_values) if cost_values else 0.0,
                "roadside_cost_max_cad_m3": max(cost_values) if cost_values else 0.0,
                "density_odt_m3": grade.density_odt_m3,
                "moisture_wet_basis": grade.moisture_wet_basis,
                "evidence": grade.evidence,
            }
        )
    return rows


def _class_summary(
    inputs: AssignmentInputs, result: AssignmentResult
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    utilization = {row["facility_id"]: row for row in result.facility_utilization}
    for facility_class in sorted(
        {facility.facility_class for facility in inputs.facilities}
    ):
        facilities = [
            facility
            for facility in inputs.facilities
            if facility.facility_class == facility_class
        ]
        nameplate = sum(facility.nameplate_m3_y for facility in facilities)
        assigned = sum(
            utilization.get(facility.facility_id, {}).get("assigned_m3", 0.0)
            for facility in facilities
        )
        binding = sum(
            1
            for facility in facilities
            if utilization.get(facility.facility_id, {}).get("capacity_binding", False)
        )
        facility_ids = {facility.facility_id for facility in facilities}
        links = sum(
            1 for link in inputs.transport_links if link.destination in facility_ids
        )
        linked_facilities = {
            link.destination
            for link in inputs.transport_links
            if link.destination in facility_ids
        }
        rows.append(
            {
                "facility_class": facility_class,
                "facility_count": len(facilities),
                "linked_facility_count": len(linked_facilities),
                "nameplate_m3_y": nameplate,
                "assigned_m3": assigned,
                "utilization": assigned / nameplate if nameplate > 0 else 0.0,
                "binding_facility_count": binding,
                "transport_link_count": links,
            }
        )
    return rows


def _report_markdown(
    repo_root: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
    previous_mix: Mapping[str, float] | None = None,
) -> str:
    inputs = fixture.inputs
    grade_rows = _grade_summary(inputs, result)
    class_rows = _class_summary(inputs, result)
    ffcm_mix = _ffcm_class_mix(repo_root)
    total_supply = sum(item.volume_m3 for item in inputs.supplies)

    transport_summary = fixture.config.get("transport_summary", {})
    real_road = transport_summary.get("real_road", {})
    fallback = transport_summary.get("ffcm_fallback", {})
    real_facility_count = int(real_road.get("facility_count", 0))
    fallback_facility_count = int(fallback.get("facility_count", 0))
    fallback_facilities = list(fallback.get("facilities", []))
    transport_note = (
        "This version uses the real NRN BC road graph for facilities with a "
        f"snapped road path ({real_facility_count} facilities) and keeps the "
        "parsed FFCM TL graph as a flagged fallback for "
        f"{fallback_facility_count} facilities without a real path."
        if real_facility_count
        else "This version replaces the flat 150 km access leg with the parsed "
        "FFCM TL graph."
    )
    transport_label = (
        "transport (real NRN road + FFCM fallback)"
        if real_facility_count
        else "transport (FFCM TL graph)"
    )
    fallback_list = ", ".join(fallback_facilities[:20]) or "none"
    real_road_note = (
        f"- Real NRN road paths: {real_facility_count} facilities "
        f"({real_road.get('link_count', 0)} grade links).\n"
        f"- FFCM TL fallback: {fallback_facility_count} facilities "
        f"({fallback.get('link_count', 0)} grade links); first 20: "
        f"{fallback_list}.\n"
        if real_facility_count
        else ""
    )

    grade_table = _markdown_table(
        [
            "Grade",
            "Product",
            "Offers",
            "Supply m3",
            "Supply ODT",
            "Assigned m3",
            "Unassigned m3",
            "Assigned share",
            "Roadside CAD/m3",
        ],
        [
            (
                row["grade_id"],
                row["product"],
                row["offer_count"],
                _fmt(row["supplied_m3"]),
                _fmt(row["supplied_odt"], 0) if row["supplied_odt"] > 0 else "0",
                _fmt(row["assigned_m3"]),
                _fmt(row["unassigned_m3"]),
                _pct(row["assigned_share"]),
                (
                    f"{_fmt(row['roadside_cost_min_cad_m3'], 2)}-"
                    f"{_fmt(row['roadside_cost_max_cad_m3'], 2)}"
                ),
            )
            for row in grade_rows
        ],
    )

    class_table = _markdown_table(
        [
            "Class",
            "Facilities",
            "With link",
            "Nameplate m3/y",
            "Assigned m3",
            "Utilization",
            "Binding",
            "Links",
        ],
        [
            (
                row["facility_class"],
                row["facility_count"],
                row["linked_facility_count"],
                _fmt(row["nameplate_m3_y"]),
                _fmt(row["assigned_m3"]),
                _pct(row["utilization"]),
                row["binding_facility_count"],
                row["transport_link_count"],
            )
            for row in class_rows
        ],
    )

    facility_class_rows = []
    for facility_class in ("SAW", "PAP", "PEL", "BIO", "PLY", "OSB", "NSP"):
        records = [
            record
            for record in fixture.facility_table.records
            if record.facility_class == facility_class
        ]
        if not records:
            continue
        in_scope = [record for record in records if record.stage_a_scope]
        facility_class_rows.append(
            (
                facility_class,
                len(records),
                len(in_scope),
                _fmt(sum((record.nameplate_m3_y or 0.0) for record in in_scope)),
                sum(
                    1
                    for record in in_scope
                    if record.transport_link_status.startswith("linked")
                ),
            )
        )
    facility_class_table = _markdown_table(
        [
            "Class",
            "All BC rows",
            "Stage A rows",
            "Stage A nameplate m3/y",
            "Linked",
        ],
        facility_class_rows,
    )

    objective_table = _markdown_table(
        ["Component", "Value CAD", "Unit", "Notes"],
        [
            (row["component"], _fmt(row["value_cad"], 2), row["unit"], row["notes"])
            for row in result.objective_decomposition
        ],
    )

    assigned_volume = sum(item.volume_m3 for item in result.assignments)
    average_components = {
        "product_revenue_cad_m3": 0.0,
        "supply_cost_cad_m3": 0.0,
        "transport_cost_cad_m3": 0.0,
        "gate_cost_cad_m3": 0.0,
        "processing_cost_cad_m3": 0.0,
        "net_contribution_cad_m3": 0.0,
    }
    if assigned_volume > 0.0:
        for item in result.assignments:
            average_components["product_revenue_cad_m3"] += (
                item.volume_m3 * item.product_revenue_cad_m3
            )
            average_components["supply_cost_cad_m3"] += (
                item.volume_m3 * item.supply_cost_cad_m3
            )
            average_components["transport_cost_cad_m3"] += (
                item.volume_m3 * item.transport_cost_cad_m3
            )
            average_components["gate_cost_cad_m3"] += (
                item.volume_m3 * item.gate_cost_cad_m3
            )
            average_components["processing_cost_cad_m3"] += (
                item.volume_m3 * item.processing_cost_cad_m3
            )
            average_components["net_contribution_cad_m3"] += (
                item.volume_m3 * item.net_contribution_cad_m3
            )
        average_components = {
            name: value / assigned_volume for name, value in average_components.items()
        }
    delivered_cost = (
        average_components["supply_cost_cad_m3"]
        + average_components["transport_cost_cad_m3"]
        + average_components["gate_cost_cad_m3"]
        + average_components["processing_cost_cad_m3"]
    )
    delivered_table = _markdown_table(
        ["Component", "CAD/m3", "Notes"],
        [
            (
                "product revenue",
                _fmt(average_components["product_revenue_cad_m3"], 2),
                "assigned volume weighted",
            ),
            (
                "supply (roadside proxy)",
                _fmt(average_components["supply_cost_cad_m3"], 2),
                "assigned volume weighted",
            ),
            (
                transport_label,
                _fmt(average_components["transport_cost_cad_m3"], 2),
                "assigned volume weighted; fallback links may include transload",
            ),
            (
                "gate",
                _fmt(average_components["gate_cost_cad_m3"], 2),
                "assigned volume weighted",
            ),
            (
                "processing",
                _fmt(average_components["processing_cost_cad_m3"], 2),
                "assigned volume weighted",
            ),
            (
                "delivered cost",
                _fmt(delivered_cost, 2),
                "supply + transport + gate + processing",
            ),
            (
                "net contribution",
                _fmt(average_components["net_contribution_cad_m3"], 2),
                "revenue - delivered cost",
            ),
        ],
    )

    validation_table = _markdown_table(
        ["Check", "Pass", "Detail"],
        [
            (check.check_id, "yes" if check.passed else "NO", check.detail)
            for check in validation.checks
        ],
    )

    top_facilities = [
        row
        for row in sorted(
            result.facility_utilization,
            key=lambda row: row["assigned_m3"],
            reverse=True,
        )
        if row["assigned_m3"] > 0.0
    ][:10]
    top_table = _markdown_table(
        ["Facility", "Class", "Assigned m3", "Nameplate m3/y", "Utilization"],
        [
            (
                row["facility_id"],
                row["facility_class"],
                _fmt(row["assigned_m3"]),
                _fmt(row["nameplate_m3_y"]),
                _pct(row["utilization"]),
            )
            for row in top_facilities
        ],
    )

    class_mix_table = _class_mix_table(fixture.config, class_rows, previous_mix)
    if ffcm_mix is not None:
        variable_mix = ", ".join(
            f"{facility_class} {_pct(ffcm_mix.get(facility_class, 0.0))}"
            for facility_class in ("SAW", "PAP", "PEL", "BIO")
        )
        class_mix_note = (
            "FFCM BC nameplate/facility shares come from the 161 in-scope BC "
            "SAW/PAP/PEL/BIO facilities in `facilities.csv`. The directional "
            "FFCM `fmu -> facility` variable-family shares are "
            f"{variable_mix}; that comparison is secondary and not a calibration "
            "target."
        )
    else:
        class_mix_note = (
            "FFCM variable-family table not found; the BC facility-subset "
            "comparison above remains valid."
        )

    contract_rows = result.contract_compliance
    contract_ok = sum(
        1 for row in contract_rows if row["within_minimum"] and row["within_maximum"]
    )
    contract_sources = sorted(
        {str(row.get("contract_source", "")) for row in contract_rows}
    )
    binding_rows = [
        row for row in result.facility_utilization if row["capacity_binding"]
    ]
    binding_table = (
        _markdown_table(
            ["Facility", "Class", "Assigned m3", "Nameplate m3/y", "Utilization"],
            [
                (
                    row["facility_id"],
                    row["facility_class"],
                    _fmt(row["assigned_m3"]),
                    _fmt(row["nameplate_m3_y"]),
                    _pct(row["utilization"]),
                )
                for row in binding_rows[:10]
            ],
        )
        if binding_rows
        else "No facility reached nameplate capacity in this run."
    )

    assumption_rows = fixture.tables["assumptions"]
    assumption_table = _markdown_table(
        ["Category", "Assumption", "Value", "Unit", "Evidence"],
        [
            (
                row["category"],
                row["assumption"],
                row["value"],
                row["unit"],
                row["evidence"],
            )
            for row in assumption_rows
            if row["category"]
            in {
                "scope",
                "supply",
                "residue",
                "facility_capacity",
                "coordinate",
                "transport",
                "contracts",
                "objective",
                "grade",
            }
        ],
    )

    rejected_lines = [
        row
        for row in fixture.tables["line_screen"]
        if row["capacity_status"] == "rejected"
    ]
    rejected_transport = [
        row
        for row in fixture.tables["transport_screen"]
        if str(row["status"]).startswith("missing")
    ]
    rejected_table = (
        _markdown_table(
            ["Facility", "Line", "Reason"],
            [
                (row["facility_id"], row["line_id"], row["reason"])
                for row in rejected_lines[:15]
            ],
        )
        if rejected_lines
        else "No processing line was rejected by the unit contract."
    )
    missing_transport_table = (
        _markdown_table(
            ["Facility", "Status", "Reason"],
            [
                (row["facility_id"], row["status"], row["reason"])
                for row in rejected_transport[:20]
            ],
        )
        if rejected_transport
        else "No Stage A facility was excluded from the transport layer."
    )

    is_sensitivity = float(fixture.config["area_ha"]) > DEFAULT_AREA_HA * 1.01
    sensitivity_note = (
        "> **Period-scale sensitivity.** This run uses the ws4 quota area "
        f"`{_fmt(fixture.config['area_ha'])} ha/period`. It is not an annual "
        "supply forecast and its volume must not be compared directly with "
        "annual facility nameplate without a period normalization.\n"
        if is_sensitivity
        else ""
    )
    report = f"""# TSA24/BC Stage A Facility Assignment Pilot

> **Diagnostic pilot.** The supply area is `{_fmt(fixture.config["area_ha"])} ha`, a
> contract/data-shape test, not a TSA24 supply estimate. Assortment sorts are
> dimension-only; no BC official grade equivalence is claimed. MKRF is not used.
> This version uses the real NRN BC road graph for facilities with a snapped
> road path and keeps the parsed FFCM TL graph as a flagged fallback elsewhere.
> It adds facility coordinate provenance and routes all capacity conversions
> through `units.py`.
{sensitivity_note}
{transport_note}
{real_road_note}
## 1. Scope and Stage A/B split

Stage A consumes the TSA24 assortment matrix, parsed FFCM BC facility lines,
the real NRN BC road graph plus flagged FFCM TL fallback, and diagnostic
contracts, then solves a
profit-maximising transportation/allocation LP. It emits facility assignment,
utilization, contract, unassigned-supply, objective, and validation tables.
Stage B (`fresh_fibre.graph` / `fresh_fibre.lp`) consumes the Stage B
`supply_offer`, `technology`, and `transport_link` subset written alongside this
report; Stage A does not invent Stage B demand or market rows.

- Scenario: `{inputs.scenario_id}`
- Period: `{inputs.periods[0]}`
- Supply offers: {len(inputs.supplies)}
- Facilities: {len(inputs.facilities)} screened; {len({link.destination for link in inputs.transport_links})} with a transport link
- Transport links: {len(inputs.transport_links)}
- Contracts: {len(inputs.contracts)} (`contract_source={";".join(contract_sources)}`)
- Total supply: {_fmt(total_supply)} m3 green
- Solver: {result.solver_name} {result.solver_version} ({result.status})

## 2. BC facilities (coordinate coverage, sources, missing)

One row per BC facility is written to `facilities_bc.csv`. Coordinates are
resolved from the recovered Mills DB 2015 crosswalk (`mills_db_2015`), then an
FFCM TL/water hub anchor, then `missing`.  Exact facility codes are only a
candidate: the 2015 and 2018 code systems collide, so every code match must be
corroborated by name, city, or coordinate evidence.  The hub anchor is the
median coordinate of geocoded facilities directly attached to the same
TL/water node; it is a low-confidence proxy, not a Euclidean guess.

{_coverage_tables(fixture.config)}

{facility_class_table}

### 2.1 Capacity normalization

Capacity is normalized to green m3/y through `units.py`: `m3/y` directly,
`odt/y` and `bdmt/y` at the declared 0.45 ODT/m3 density, `admt/y` only with a
declared air-dry moisture, `mbf/y` only with a declared recovery factor, and
energy units never to volume. Facility nameplate is the sum of the maximum
normalized capacity per primary product. Rejected processing lines (first 15):

{rejected_table}

### 2.2 Transport screening

Facilities without a defensible TL path are excluded rather than given a
fabricated distance. Rejected facilities (first 20):

{missing_transport_table}

## 3. Transport (paths, modes, distance/cost vs 150 km, confidence)

{_transport_report(fixture.config)}

## 4. Units (accepted/rejected, factors)

{_units_report(fixture.config)}

## 5. Pilot results

### 5.1 Assigned versus unassigned by grade

{grade_table}

### 5.2 Facility utilization by class

{class_table}

### 5.3 Capacity binding

{binding_table}

### 5.4 Largest assignments

{top_table}

### 5.5 Delivered cost per m3

{delivered_table}

### 5.6 Objective decomposition

{objective_table}

### 5.7 Class mix versus FFCM BC subset and previous pilot

{class_mix_table}

{class_mix_note}

## 6. Contracts and capacity gaps

{contract_ok} of {len(contract_rows)} diagnostic contracts satisfy both bounds.
Contract sources in this run: `{";".join(contract_sources)}`.
The BC Fibre Database diagnostic table contributes real TSA-level 2018
apportionment volumes (no facility identifier) and dimensionless residue
allocation shares (no volume).  It does **not** contribute a facility-level
contract volume, so Stage A keeps `min=0`, `max=nameplate` and
`contract_source=diagnostic_absent` for every facility.  These are diagnostic
and project-legacy bounds, not real supply agreements.  Transport links carry
`capacity=None` and `capacity_source=absent`; route and transload capacities
are not present in the parsed tables. These are data gaps, not modelled
constraints.

## 7. Validation

{validation_table}

Overall validation: **{"PASS" if validation.passed else "FAIL"}**.

## 8. Assumptions and unit conversions

{assumption_table}

The full assumption list, including rejected lines and rejected transport
links, is written to `assumptions.csv`, `line_screen.csv`, and
`transport_screen.csv`.

## 9. Gaps and next steps

- Replace the Prince George mill proxy with a real TSA24 supply-node or FMU
  anchor once the AU-to-FMU crosswalk exists. The current anchor is documented
  and low-confidence, not a production route.
- Extend real-road coverage beyond the NRN BC 2018 layer and 5 km facility snap
  tolerance; facilities without a real path still use flagged FFCM TL fallback.
- Populate route and transload capacity before interpreting congestion or
  mode-shift limits.
- Populate real contract minima, maxima, and gate costs.
- Normalize `admt/y` and `mbf/y` capacity only with declared moisture/recovery
  factors; the current pilot rejects them rather than guessing.
- Add mill-residue supply (sawdust/woodchips/hogfuel) if Stage A scope expands
  beyond logging residue.
- Add multi-period inventory, demand/market clearing, and line-level `y`
  variables only when the data support them.
- Do not interpret the 1,000 ha result as a TSA24 supply estimate. The optional
  ws4-quota sensitivity is a period-scale diagnostic and must be labeled as
  such.
"""
    return report


def _manifest(
    out_dir: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
    report_path: Path,
) -> dict[str, Any]:
    output_paths = sorted(
        [
            path
            for path in out_dir.iterdir()
            if path.is_file() and path.name != "manifest.json"
        ]
    )
    outputs = {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in output_paths
    }
    return {
        "pilot": "tsa24_bc_stage_a_facility_assignment",
        "code_version": CODE_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(),
        "status": "pass" if validation.passed else "fail",
        "run": {
            "run_id": result.run_id,
            "scenario_id": result.scenario_id,
            "period": result.period,
            "solver": result.solver_name,
            "solver_version": result.solver_version,
            "solver_status": result.status,
            "objective_value_cad": result.objective_value_cad,
        },
        "config": dict(fixture.config),
        "counts": result.inputs_summary,
        "source_hashes": dict(fixture.source_hashes),
        "outputs": outputs,
        "report": report_path.name,
        "assumptions": list(fixture.tables["assumptions"]),
        "limitations": [
            "1,000 ha base area is diagnostic, not a TSA24 supply estimate.",
            "Assortment sorts are dimension-only; no BC official grade equivalence.",
            "TSA24 supply anchor is a geocoded Prince George mill proxy (FFCM BC-SAW-506); no TSA24 FMU exists in the FFCM graph. The legacy BC-SAW-488 anchor was a code collision with Canfor Houston.",
            "Transport distances use the real NRN BC road graph where available and flagged FFCM TL fallback otherwise; neither is a production route.",
            "Only BC SAW/PAP/PEL/BIO facilities are in the LP; PLY/OSB/NSP rows are retained in facilities_bc.csv but out of scope.",
            "BC coordinates use a corroborated Mills DB 2015 crosswalk; exact facility codes alone are not trusted because the 2015 and 2018 code systems collide.",
            "Unmatched BC facilities keep a low-confidence TL hub proxy or missing; coordinates are never guessed.",
            "bdmt/y capacity converts at the declared 0.45 ODT/m3 density; admt/y and mbf/y are rejected without declared factors.",
            "Contracts are diagnostic min=0/max=nameplate with contract_source=diagnostic_absent; the BC Fibre Database diagnostic table has no facility-level volumes.",
            "Transport links are uncapped: capacity=None and capacity_source=absent; transload costs are applied at TL nodes.",
            "One period; no multi-period inventory dynamics.",
            "No MKRF input or output is used.",
        ],
    }


def _write_outputs(
    out_dir: Path,
    fixture: Tsa24Fixture,
    result: AssignmentResult,
    validation: ValidationReport,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        out_dir / "facility_assignment.csv",
        [item.to_dict() for item in result.assignments],
        ASSIGNMENT_COLUMNS,
    )
    _write_csv(
        out_dir / "facility_utilization.csv",
        result.facility_utilization,
        UTILIZATION_COLUMNS,
    )
    grade_rows = [
        {
            "facility_id": row["facility_id"],
            "facility_name": row["facility_name"],
            "facility_class": row["facility_class"],
            "grade_id": grade["grade_id"],
            "grade_capacity_m3_y": grade["capacity_m3_y"],
            "assigned_m3": grade["assigned_m3"],
            "utilization": grade["utilization"],
            "binding": grade["binding"],
        }
        for row in result.facility_utilization
        for grade in row["grade_rows"]
    ]
    _write_csv(
        out_dir / "facility_grade_utilization.csv",
        grade_rows,
        GRADE_UTILIZATION_COLUMNS,
    )
    _write_csv(
        out_dir / "contract_compliance.csv",
        result.contract_compliance,
        CONTRACT_COLUMNS,
    )
    _write_csv(
        out_dir / "unassigned_supply.csv",
        [item.to_dict() for item in result.unassigned],
        UNASSIGNED_COLUMNS,
    )
    _write_csv(
        out_dir / "objective_decomposition.csv",
        result.objective_decomposition,
        OBJECTIVE_COLUMNS,
    )
    write_fixture_tables(fixture, out_dir)
    stage_b = to_stage_b_tables(fixture.inputs, result)
    for name, rows in stage_b.items():
        if not rows:
            continue
        columns = list(rows[0].keys())
        _write_csv(out_dir / f"stage_b_{name}.csv", rows, columns)

    validation_payload = {
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "period": result.period,
        "status": "pass" if validation.passed else "fail",
        "validation": validation.to_dict(),
        "inputs": result.inputs_summary,
        "config": dict(fixture.config),
        "data_gaps": [
            "no TSA24 supply-node-to-transload route in the parsed FFCM graph; a geocoded Prince George mill is the documented anchor proxy",
            (
                "BC coordinate recovery: "
                f"{fixture.config['facility_coverage']['coordinate_source_counts'].get('mills_db_2015', 0)} "
                "facilities use a corroborated Mills DB 2015 coordinate; "
                f"{fixture.config['facility_coverage']['coordinate_source_counts'].get('tl_anchor_proxy', 0)} "
                "use a low-confidence TL hub proxy and "
                f"{fixture.config['facility_coverage']['coordinate_source_counts'].get('missing', 0)} "
                "are missing"
            ),
            "route capacity absent from routes.csv; all transport links use capacity=None and capacity_source=absent",
            (
                "BC Fibre Database has TSA apportionment volumes without facility IDs and residue allocation shares without volumes; "
                "no facility-level contract volume is available"
            ),
            "admt/y and mbf/y capacity units require declared moisture/recovery factors and are rejected without them",
            "no hogfuel supply in the TSA24 assortment matrix",
            "no mill-residue (sawdust/woodchips) supply in this pilot",
        ],
        "assumptions": list(fixture.tables["assumptions"]),
    }
    (out_dir / "validation_summary.json").write_text(
        json.dumps(validation_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_pilot(
    repo_root: Path,
    out_dir: Path,
    *,
    area_ha: float,
    period: str,
    scenario_id: str,
    sensitivity_label: str = "",
    real_road_links: Path | None = None,
    recovered_coordinates: Path | None = None,
    diagnostic_contracts: Path | None = None,
) -> tuple[AssignmentResult, ValidationReport, Tsa24Fixture]:
    """Build, solve, validate, and write one Stage A pilot run."""
    out_dir.mkdir(parents=True, exist_ok=True)
    previous_mix = _previous_pilot_class_mix(out_dir)
    fixture = build_tsa24_fixture(
        repo_root,
        area_ha=area_ha,
        period=period,
        scenario_id=scenario_id,
        real_road_links=real_road_links,
        recovered_coordinates=recovered_coordinates,
        diagnostic_contracts=diagnostic_contracts,
    )
    result = solve_assignment(fixture.inputs)
    validation = validate_assignment(fixture.inputs, result)
    result = replace(result, validation=validation.to_dict())

    report_text = _report_markdown(
        repo_root, fixture, result, validation, previous_mix=previous_mix
    )
    report_path = out_dir / "ASSIGNMENT_REPORT.md"
    report_path.write_text(report_text, encoding="utf-8")
    _write_outputs(out_dir, fixture, result, validation)

    manifest = _manifest(out_dir, fixture, result, validation, report_path)
    if sensitivity_label:
        manifest["sensitivity_label"] = sensitivity_label
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result, validation, fixture


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (defaults to the package location).",
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--area-ha", type=float, default=DEFAULT_AREA_HA)
    parser.add_argument("--period", default="2026")
    parser.add_argument("--scenario-id", default="tsa24_bc_stage_a_pilot")
    parser.add_argument(
        "--sensitivity-ws4-quota",
        action="store_true",
        help="Also run the documented ws4 quota area as a period-scale diagnostic.",
    )
    parser.add_argument(
        "--real-road-links",
        type=Path,
        default=None,
        help=(
            "Optional transport_links_bc_real.csv. Real NRN road paths replace "
            "FFCM paths per facility; facilities without a real path keep the "
            "FFCM path and are flagged as fallback."
        ),
    )
    parser.add_argument(
        "--recovered-coordinates",
        type=Path,
        default=DEFAULT_RECOVERED_COORDINATES,
        help=(
            "facilities_bc_recovered.csv. When present, its Mills DB 2015 "
            "crosswalk replaces the legacy facility_geocoding.csv BC matches."
        ),
    )
    parser.add_argument(
        "--diagnostic-contracts",
        type=Path,
        default=DEFAULT_DIAGNOSTIC_CONTRACTS,
        help=(
            "contracts_bc_diagnostic.csv. Only rows with a matched facility "
            "and a volume can replace the diagnostic_absent fallback."
        ),
    )
    parser.add_argument("--msg", action="store_true", help="Show CBC solver output.")
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    out_dir = args.out_dir
    if not out_dir.is_absolute():
        out_dir = (Path.cwd() / out_dir).resolve()

    recovered = args.recovered_coordinates
    if recovered is not None and not recovered.is_absolute():
        recovered = (Path.cwd() / recovered).resolve()
    diagnostic = args.diagnostic_contracts
    if diagnostic is not None and not diagnostic.is_absolute():
        diagnostic = (Path.cwd() / diagnostic).resolve()

    result, validation, fixture = run_pilot(
        repo_root,
        out_dir,
        area_ha=args.area_ha,
        period=args.period,
        scenario_id=args.scenario_id,
        real_road_links=args.real_road_links,
        recovered_coordinates=recovered,
        diagnostic_contracts=diagnostic,
    )
    print(
        f"base: status={result.status} objective={result.objective_value_cad:,.2f} CAD; "
        f"assigned={result.assigned_volume_m3:,.0f} m3; "
        f"unassigned={result.unassigned_volume_m3:,.0f} m3; "
        f"validation={'PASS' if validation.passed else 'FAIL'}; "
        f"facilities={len(fixture.inputs.facilities)}; links={len(fixture.inputs.transport_links)}; "
        f"coverage={fixture.facility_table.coverage_in_scope}; "
        f"paths={fixture.transport_table.summary['confidence_counts']}"
    )

    exit_code = 0 if validation.passed else 1
    if args.sensitivity_ws4_quota:
        sensitivity_dir = out_dir / "sensitivity_ws4_quota"
        sensitivity_result, sensitivity_validation, sensitivity_fixture = run_pilot(
            repo_root,
            sensitivity_dir,
            area_ha=WS4_QUOTA_AREA_HA,
            period=args.period,
            scenario_id=f"{args.scenario_id}_ws4_quota",
            sensitivity_label="ws4_quota_306047.3_ha_period_not_annual",
            real_road_links=args.real_road_links,
            recovered_coordinates=recovered,
            diagnostic_contracts=diagnostic,
        )
        print(
            f"sensitivity: status={sensitivity_result.status} "
            f"objective={sensitivity_result.objective_value_cad:,.2f} CAD; "
            f"assigned={sensitivity_result.assigned_volume_m3:,.0f} m3; "
            f"unassigned={sensitivity_result.unassigned_volume_m3:,.0f} m3; "
            f"validation={'PASS' if sensitivity_validation.passed else 'FAIL'}; "
            f"facilities={len(sensitivity_fixture.inputs.facilities)}"
        )
        if not sensitivity_validation.passed:
            exit_code = 1
    if not validation.passed:
        for check in validation.failed_checks:
            print(f"FAILED {check.check_id}: {check.detail}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
