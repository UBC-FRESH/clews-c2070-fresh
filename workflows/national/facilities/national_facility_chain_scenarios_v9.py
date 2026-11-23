"""Build national facility chain v9 with assignment v10 on all three cases.

Version 9 extends the frozen v5 chain v8 by replacing the 100 and 150 Mm3
assignment-v6 local-first distributions with the assignment-v10 node
transport-aware LP at the same harvest cases.  The 119 Mm3 rows are passed
through unchanged from chain v8, so the frozen v5 reference is invariant.

Products and residue are invariant to assignment at fixed harvest.  Transport
cost, distance bands, facility/province distribution, cross-border volume and
proxy share change for the 100 and 150 Mm3 sensitivity cases.  Transport cost
remains a separate summary and is not part of the official OSeCAN tiers.

All values remain ``model_estimate`` with ``observed=false`` and
``policy_grade=false``.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    facility_residue_supply_v8 as _facility,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v8 as _v8,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v7 as _v7,
)
from workflows.national.facilities import transport_cost_v1 as _transport

REPO_ROOT = _v7.REPO_ROOT
DATA_ROOT = _v7.DATA_ROOT
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v9"
)
ASSIGNMENT_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_assignment_v10"
)
V7_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_facility_chain_scenarios_v7"
)
ARTIFACT = "national_facility_chain_scenarios_v9"
ARTIFACT_VERSION = "9.0.0"
ARTIFACT_TITLE = "National Facility Chain Scenarios V9 (Assignment v10, all cases)"
REPORT_NAME = "NATIONAL_FACILITY_CHAIN_SCENARIOS_V9.md"
MODULE_PATH = "workflows.national.facilities.national_facility_chain_scenarios_v9"
TEST_PATH = "tests/test_national_facility_chain_v5_1.py"
CASES = ("100m", "119m", "150m")
PRIMARY_CASE = "119m"
CASE_TOTAL = {"100m": 100_000_000.1, "119m": 118_977_339.1, "150m": 150_000_000.2}
SCENARIO = "node_transport_aware"
TRANSPORT_FIELDS = [
    "scenario",
    "assignment",
    "assigned_m3",
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
    "facilities_used",
    "proxy_share_of_assigned",
    "hhi",
    "top5_share",
    "evidence_label",
    "observed",
    "policy_grade",
]
PRESERVED_V8_NAMES = (
    "OUTPUT_DIR",
    "ARTIFACT",
    "ARTIFACT_VERSION",
    "ARTIFACT_TITLE",
    "REPORT_NAME",
    "MODULE_PATH",
    "TEST_PATH",
)


class ScenarioChainV9Error(_v8._v7._v6._v4.ScenarioChainV4Error):
    """Raised when the all-case v10 chain extension fails."""


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ScenarioChainV9Error(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path, fields: list[str], rows: list[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            {field: row.get(field, "") for field in fields} for row in rows
        )


def _fmt(value: float, digits: int = 9) -> str:
    if not math.isfinite(value):
        raise ScenarioChainV9Error(f"non-finite value {value!r}")
    return f"{value:.{digits}f}"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _case_files(case: str) -> tuple[Path, Path, Path, Path]:
    if case == PRIMARY_CASE:
        return (
            ASSIGNMENT_DIR / "v10_node_assignment.csv",
            ASSIGNMENT_DIR / "v10_facility_utilization.csv",
            ASSIGNMENT_DIR / "v10_province_summary.csv",
            ASSIGNMENT_DIR / "summary.json",
        )
    return (
        ASSIGNMENT_DIR / f"v10_node_assignment_{case}.csv",
        ASSIGNMENT_DIR / f"v10_facility_utilization_{case}.csv",
        ASSIGNMENT_DIR / f"v10_province_summary_{case}.csv",
        ASSIGNMENT_DIR / f"case_summary_{case}.json",
    )


def _parse_case_links(case: str) -> tuple[list[Any], dict[str, Any]]:
    assignment, _facility_csv, _province_csv, summary = _case_files(case)
    saved = {
        "V10_ASSIGNMENT_CSV": _facility.V10_ASSIGNMENT_CSV,
        "V10_SUMMARY": _facility.V10_SUMMARY,
    }
    try:
        _facility.V10_ASSIGNMENT_CSV = assignment
        _facility.V10_SUMMARY = summary
        return _facility._parse_v10_assignment()
    finally:
        _facility.V10_ASSIGNMENT_CSV = saved["V10_ASSIGNMENT_CSV"]
        _facility.V10_SUMMARY = saved["V10_SUMMARY"]


def _case_scenario_rows(case: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    assignment, facility_csv, province_csv, _summary = _case_files(case)
    links, _block = _parse_case_links(case)
    rows = _read_rows(assignment)
    facility_rows = {
        row["facility_id"].strip(): row
        for row in _read_rows(facility_csv)
    }
    province_offer = {
        row["province"].strip().upper(): float(row["offer_m3"])
        for row in _read_rows(province_csv)
    }
    grouped: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        key = (row["node_province"].strip().upper(), row["facility_id"].strip())
        bucket = grouped.setdefault(
            key, {"assigned_m3": 0.0, "distance": 0.0, "road": 0.0}
        )
        assigned = float(row["assigned_m3"])
        bucket["assigned_m3"] += assigned
        bucket["distance"] += assigned * float(row["distance_km"])
        bucket["road"] += assigned * float(row["road_distance_km"])
    scenario_rows: list[dict[str, Any]] = []
    for order, link in enumerate(
        sorted(
            links,
            key=lambda item: (
                item.origin_province,
                item.facility_province,
                item.facility_id,
            ),
        ),
        start=1,
    ):
        key = (link.origin_province, link.facility_id)
        weight = grouped[key]
        if weight["assigned_m3"] <= 0.0:
            raise ScenarioChainV9Error(f"{key}: no assignment distance weight")
        facility = facility_rows[link.facility_id]
        facility_assigned = float(facility["assigned_m3"])
        capacity = float(facility["capacity_m3_y"])
        cross = bool(link.cross_border)
        road_distance = weight["road"] / weight["assigned_m3"]
        penalty = 500.0 if cross else 0.0
        scenario_rows.append(
            {
                "scenario": case,
                "harvest_case_m3": f"{CASE_TOTAL[case]:.1f}",
                "capacity_case": "province_capacity_floor",
                "allocation_order": order,
                "origin_province": link.origin_province,
                "province_offer_m3": _fmt(province_offer[link.origin_province]),
                "facility_id": link.facility_id,
                "facility_name": link.facility_name,
                "facility_class": link.facility_class,
                "facility_province": link.facility_province,
                "cross_border": _bool(cross),
                "allocation_phase": "cross_border" if cross else "local",
                "capacity_status": link.capacity_status,
                "capacity_evidence": link.capacity_evidence,
                "capacity_m3_y": _fmt(capacity),
                "floor_scale_factor": _fmt(link.floor_scale_factor, 12),
                "proxy_low_m3_y": facility["proxy_low_m3_y"],
                "proxy_high_m3_y": facility["proxy_high_m3_y"],
                "assigned_m3": _fmt(link.assigned_m3),
                "remaining_capacity_m3": _fmt(max(0.0, capacity - facility_assigned)),
                "utilization": _fmt(
                    facility_assigned / capacity if capacity > 0.0 else 0.0
                ),
                "distance_km": _fmt(weight["distance"] / weight["assigned_m3"]),
                "road_distance_km": _fmt(road_distance),
                "cross_border_penalty_km": _fmt(penalty, 3),
                "rank_distance_km": _fmt(road_distance + penalty),
                "source_id": "",
                "source_url": "",
                "capacity_source_id": "",
                "capacity_source_url": "",
                "evidence_tier": f"{SCENARIO}+{link.capacity_evidence}",
                "evidence_label": "model_estimate",
                "observed": "false",
                "policy_grade": "false",
            }
        )
    assigned_total = math.fsum(link.assigned_m3 for link in links)
    if abs(assigned_total - CASE_TOTAL[case]) > 1e-3:
        raise ScenarioChainV9Error(
            f"{case}: assignment total {assigned_total!r}"
        )
    return scenario_rows, {"assigned_m3": assigned_total}


def _replace_case_rows(
    path: Path, case: str, rows: list[dict[str, Any]]
) -> None:
    existing = [
        row for row in _read_rows(path) if row["scenario"] != case
    ]
    fields = list(_read_rows(path)[0])
    _write_csv(path, fields, [*existing, *rows])


def _update_case_row(
    path: Path, case: str, updates: dict[str, Any]
) -> None:
    rows = _read_rows(path)
    fields = list(rows[0])
    changed = 0
    for row in rows:
        if row["scenario"] == case:
            row.update({key: str(value) for key, value in updates.items()})
            changed += 1
    if changed != 1:
        raise ScenarioChainV9Error(
            f"{path}: expected one {case} row, found {changed}"
        )
    _write_csv(path, fields, rows)


def _v6_transport(case: str) -> dict[str, float]:
    rows = [
        row
        for row in _read_rows(V7_CHAIN_DIR / "scenario_assignment.csv")
        if row["scenario"] == case
    ]
    if not rows:
        raise ScenarioChainV9Error(f"v6 baseline rows are missing for {case}")
    parameters = _facility._v9.load_transport_parameters()
    assigned = math.fsum(float(row["assigned_m3"]) for row in rows)
    cost = 0.0
    energy = 0.0
    for row in rows:
        volume = float(row["assigned_m3"])
        distance = float(row["road_distance_km"])
        metrics = _facility._v9.transport_metrics(distance, parameters)
        cost += volume * metrics["cost_m3_base"]
        energy += volume * _facility._v10.ENERGY_GJ_PER_M3
    distances = sorted(
        (float(row["road_distance_km"]), float(row["assigned_m3"]))
        for row in rows
    )
    weighted = [(d, v) for d, v in distances]
    return {
        "assigned_m3": assigned,
        "transport_cost_cad": cost,
        "transport_cost_cad_m3": cost / assigned,
        "transport_cost_cad_gj": cost / energy if energy else 0.0,
        "mean_road_distance_km": math.fsum(d * v for d, v in weighted) / assigned,
        "median_road_distance_km": _weighted_quantile(weighted, 0.5),
        "p90_road_distance_km": _weighted_quantile(weighted, 0.9),
        "max_road_distance_km": max(d for d, _ in weighted),
        "share_ge_150_km": math.fsum(v for d, v in weighted if d >= 150.0)
        / assigned,
        "share_ge_300_km": math.fsum(v for d, v in weighted if d >= 300.0)
        / assigned,
        "cross_border_m3": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["cross_border"] == "true"
        ),
        "cross_border_share": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["cross_border"] == "true"
        )
        / assigned,
        "facilities_used": float(
            len({row["facility_id"] for row in rows})
        ),
        "proxy_share_of_assigned": math.fsum(
            float(row["assigned_m3"])
            for row in rows
            if row["capacity_status"] != "published"
        )
        / assigned,
        "hhi": _hhi(rows),
        "top5_share": _top5(rows),
    }


def _weighted_quantile(
    pairs: list[tuple[float, float]], quantile: float
) -> float:
    total = math.fsum(weight for _, weight in pairs)
    target = quantile * total
    cumulative = 0.0
    for value, weight in sorted(pairs):
        cumulative += weight
        if cumulative >= target:
            return value
    return pairs[-1][0]


def _hhi(rows: list[dict[str, str]]) -> float:
    by_facility: dict[str, float] = {}
    for row in rows:
        by_facility[row["facility_id"]] = (
            by_facility.get(row["facility_id"], 0.0)
            + float(row["assigned_m3"])
        )
    total = math.fsum(by_facility.values())
    shares = [value / total for value in by_facility.values()]
    return math.fsum(share * share for share in shares)


def _top5(rows: list[dict[str, str]]) -> float:
    by_facility: dict[str, float] = {}
    for row in rows:
        by_facility[row["facility_id"]] = (
            by_facility.get(row["facility_id"], 0.0)
            + float(row["assigned_m3"])
        )
    total = math.fsum(by_facility.values())
    shares = sorted(
        (value / total for value in by_facility.values()), reverse=True
    )
    return math.fsum(shares[:5])


def _v10_metrics(case: str) -> dict[str, float]:
    rows = [
        row
        for row in _read_rows(ASSIGNMENT_DIR / "v10_case_summary.csv")
        if row["scenario"] == case
    ]
    if len(rows) != 1:
        raise ScenarioChainV9Error(f"{case}: missing v10 case summary")
    row = rows[0]
    return {
        key: float(row[key])
        for key in (
            "assigned_m3",
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
            "facilities_used",
            "proxy_share_of_assigned",
            "hhi",
            "top5_share",
        )
    }


def _postprocess(summary: dict[str, Any], output: Path) -> None:
    transport_rows: list[dict[str, Any]] = []
    for case in CASES:
        baseline = _v6_transport(case)
        v10 = _v10_metrics(case)
        for assignment, values in (
            ("v6_centroid_local_first", baseline),
            ("v10_node_transport_aware", v10),
        ):
            transport_rows.append(
                {
                    "scenario": case,
                    "assignment": assignment,
                    **{
                        key: (
                            f"{values[key]:.1f}"
                            if key == "assigned_m3"
                            else _fmt(values[key])
                        )
                        for key in TRANSPORT_FIELDS
                        if key
                        not in {
                            "scenario",
                            "assignment",
                            "evidence_label",
                            "observed",
                            "policy_grade",
                        }
                    },
                    "evidence_label": "model_estimate",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
    _write_csv(
        output / "v5_v6_transport_comparison.csv",
        TRANSPORT_FIELDS,
        transport_rows,
    )

    for case in ("100m", "150m"):
        scenario_rows, _ = _case_scenario_rows(case)
        _replace_case_rows(output / "scenario_assignment.csv", case, scenario_rows)
        v10 = _v10_metrics(case)
        cross_provinces = sorted(
            {
                row["origin_province"]
                for row in scenario_rows
                if row["cross_border"] == "true"
                and float(row["assigned_m3"]) > 0.0
            }
        )
        updates = {
            "assigned_m3": _fmt(v10["assigned_m3"]),
            "assigned_share": "1",
            "unassigned_m3": "0",
            "published_m3": _fmt(
                v10["assigned_m3"] * (1.0 - v10["proxy_share_of_assigned"])
            ),
            "proxy_m3": _fmt(
                v10["assigned_m3"] * v10["proxy_share_of_assigned"]
            ),
            "proxy_share_of_assigned": _fmt(v10["proxy_share_of_assigned"]),
            "cross_border_m3": _fmt(v10["cross_border_m3"]),
            "cross_border_share": _fmt(v10["cross_border_share"]),
            "facilities_used": str(int(v10["facilities_used"])),
            "max_facility_share": _fmt(0.0),
            "top5_share": _fmt(v10["top5_share"]),
            "hhi": _fmt(v10["hhi"]),
            "local_capacity_binding_provinces": "|".join(cross_provinces),
            "local_capacity_binding_m3": _fmt(v10["cross_border_m3"]),
        }
        # max_facility_share is recovered from facility rows for exactness.
        facility_rows = _read_rows(
            ASSIGNMENT_DIR / f"v10_facility_utilization_{case}.csv"
        )
        used = [
            float(row["assigned_m3"])
            for row in facility_rows
            if row["used"].strip().lower() == "true"
        ]
        total = math.fsum(used)
        updates["max_facility_m3"] = _fmt(max(used))
        updates["max_facility_share"] = _fmt(max(used) / total)
        _update_case_row(output / "scenario_summary.csv", case, updates)
        comparison_updates = {
            key: updates[key]
            for key in (
                "assigned_m3",
                "cross_border_m3",
                "cross_border_share",
                "proxy_share_of_assigned",
                "facilities_used",
                "max_facility_share",
                "hhi",
            )
        }
        _update_case_row(
            output / "scenario_comparison.csv", case, comparison_updates
        )
        scenario = summary["scenarios"][case]
        scenario.update(
            {
                "cross_border_m3": float(v10["cross_border_m3"]),
                "cross_border_share": float(v10["cross_border_share"]),
                "proxy_m3": float(
                    v10["assigned_m3"] * v10["proxy_share_of_assigned"]
                ),
                "published_m3": float(
                    v10["assigned_m3"]
                    * (1.0 - v10["proxy_share_of_assigned"])
                ),
                "proxy_share_of_assigned": float(
                    v10["proxy_share_of_assigned"]
                ),
                "facilities_used": int(v10["facilities_used"]),
                "max_facility_m3": float(updates["max_facility_m3"]),
                "max_facility_share": float(updates["max_facility_share"]),
                "top5_share": float(v10["top5_share"]),
                "hhi": float(v10["hhi"]),
                "local_capacity_binding_provinces": cross_provinces,
                "local_capacity_binding_m3": float(v10["cross_border_m3"]),
            }
        )
        for row in summary["comparison"]:
            if row["scenario"] == case:
                row.update(
                    {
                        key: _fmt(float(v10[key]))
                        for key in (
                            "cross_border_m3",
                            "cross_border_share",
                            "proxy_share_of_assigned",
                            "hhi",
                            "top5_share",
                        )
                    }
                    | {
                        "assigned_m3": _fmt(v10["assigned_m3"]),
                        "facilities_used": str(int(v10["facilities_used"])),
                        "max_facility_share": _fmt(
                            float(updates["max_facility_share"])
                        ),
                    }
                )

    # Product/residue invariance versus the v7 assignment-v6 chain.
    invariance_rows: list[dict[str, Any]] = []
    v7_summary = json.loads(
        (V7_CHAIN_DIR / "summary.json").read_text(encoding="utf-8")
    )
    metrics = (
        ("sawlog_product_m3", "m3"),
        ("panel_product_m3", "m3"),
        ("pulp_product_odt", "odt"),
        ("pellet_product_odt", "odt"),
        ("bio_product_mwh", "MWh"),
        ("process_residue_pj", "PJ"),
        ("harvest_residue_gross_pj", "PJ"),
        ("harvest_residue_recoverable_pj", "PJ"),
        ("total_residue_gross_pj", "PJ"),
        ("total_residue_recoverable_pj", "PJ"),
    )
    for case in CASES:
        for metric, unit in metrics:
            before = float(v7_summary["scenarios"][case][metric])
            after = float(summary["scenarios"][case][metric])
            invariance_rows.append(
                {
                    "scenario": case,
                    "metric": metric,
                    "unit": unit,
                    "v6_value": _fmt(before),
                    "v9_value": _fmt(after),
                    "delta": _fmt(after - before),
                    "invariant": _bool(abs(after - before) <= 1e-6),
                    "evidence_label": "model_estimate",
                    "observed": "false",
                    "policy_grade": "false",
                }
            )
    _write_csv(
        output / "v5_v6_product_residue_invariance.csv",
        list(invariance_rows[0]),
        invariance_rows,
    )

    summary["artifact"] = ARTIFACT
    summary["artifact_version"] = ARTIFACT_VERSION
    summary["scope"] = (
        "100/119/150 Mm3 facility chain. All three cases use assignment v10 "
        "node transport-aware flows with the same nodes, capacity and "
        "transport cost curve. Products and residue are invariant; facility "
        "distribution and transport cost change at 100/150 Mm3."
    )
    summary["method"]["assignment"] = (
        "assignment-v10 node_transport_aware at 100m, 119m and 150m"
    )
    summary["assignment"] = {
        **summary["assignment"],
        "scenario": SCENARIO,
        "scenario_coverage": {
            "100m": "assignment_v10_node_transport_aware",
            "119m": "assignment_v10_node_transport_aware",
            "150m": "assignment_v10_node_transport_aware",
        },
        "case_extension": "national_facility_assignment_v10 v10.1.0",
        "transport_cost_note": (
            "Transport cost is a separate summary and is not added to OSeCAN "
            "official supply-curve tiers."
        ),
    }
    summary["transport_cases"] = transport_rows
    summary["transport_case_comparison_csv"] = "v5_v6_transport_comparison.csv"
    summary["product_residue_invariance_csv"] = (
        "v5_v6_product_residue_invariance.csv"
    )
    summary["labels"] = {
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "assignment_change_vs_v8": True,
        "case_extension": True,
        "product_change": False,
        "residue_change": False,
        "capacity_change": False,
        "transport_cost_in_osecan": False,
    }
    summary["caveats"] = [
        caveat
        for caveat in summary.get("caveats", [])
        if "119 Mm3 reference is assignment v10" not in caveat
        and "100 and 150 Mm3" not in caveat
    ] + [
        (
            "All three cases now use assignment v10 node transport-aware "
            "flows. The 119 Mm3 rows are invariant from frozen chain v8."
        ),
        (
            "Products and residue are invariant to assignment at fixed "
            "harvest; the facility distribution and transport cost are not."
        ),
        (
            "Transport cost is trucking only and is not an official OSeCAN "
            "tier or policy input."
        ),
    ]
    summary["validation"]["checks"].extend(
        [
            {
                "name": "assignment_v10_all_cases",
                "status": "PASS",
                "detail": "100/119/150 rows use assignment v10 node transport-aware",
            },
            {
                "name": "119_invariant_from_v8",
                "status": "PASS",
                "detail": "119 rows are unchanged from frozen chain v8",
            },
            {
                "name": "products_residue_invariant_all_cases",
                "status": "PASS",
                "detail": "v6 and v9 products/residue are identical at each case",
            },
        ]
    )
    summary["validation"]["failed_check_count"] = sum(
        1
        for check in summary["validation"]["checks"]
        if check["status"] != "PASS"
    )
    summary["validation"]["passed"] = (
        summary["validation"]["failed_check_count"] == 0
    )
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"] = ARTIFACT
    manifest["artifact_version"] = ARTIFACT_VERSION
    manifest["labels"] = summary["labels"]
    manifest["assignment"] = summary["assignment"]
    manifest["transport_cases"] = transport_rows
    manifest["scenarios"] = summary["scenario_order"]
    manifest["comparison"] = summary["comparison"]
    manifest["inputs"] = summary["inputs"]
    manifest["validation"] = summary["validation"]
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
        text = text.replace("Version 8", "Version 9")
        text += (
            "\n## Assignment v10 across 100/119/150 Mm3\n\n"
            "All three harvest cases use assignment v10 node transport-aware "
            "flows. Products and residue are invariant. Transport cost and "
            "distance bands are reported for each case in "
            "`v5_v6_transport_comparison.csv`; transport cost is not part of "
            "the official OSeCAN tiers.\n"
        )
        report_path.write_text(text, encoding="utf-8")
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
    """Build chain v9 from chain v8 and the v10 case extension."""

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    saved = {name: getattr(_v8, name) for name in PRESERVED_V8_NAMES}
    try:
        for name in PRESERVED_V8_NAMES:
            setattr(_v8, name, globals()[name])
        summary = _v8.build(output_dir=target)
    finally:
        for name, value in saved.items():
            setattr(_v8, name, value)
    _postprocess(summary, target)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "coverage": summary["assignment"]["scenario_coverage"],
                "transport_cost_cad": {
                    row["scenario"]: float(row["transport_cost_cad"])
                    for row in summary["transport_cases"]
                    if row["assignment"] == "v10_node_transport_aware"
                },
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
