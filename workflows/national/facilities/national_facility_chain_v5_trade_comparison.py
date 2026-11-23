"""Compare the local assignment-v6 chain v5 with the trade assignment-v5 v5.

The local chain is the frozen ``province_capacity_floor`` base.  The trade
chain is the rebuilt assignment-v5 ``published_plus_proxy`` sensitivity with
the same measured ``national_product_chain_v3`` coefficients and total-residue
scenarios.  This artifact compares assignment, chain products/residue,
facility-province process residue, curve cases, and the OSeCAN handoff.

All values are screening model estimates: ``observed=false``,
``policy_grade=false``.  The comparison is not an interprovincial trade
forecast, a policy target, or an official OSeCAN result.  No UI or public
OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    facility_residue_supply_v5 as _facility_local,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v5 as _chain_local,
)
from workflows.national.facilities import (
    national_facility_chain_scenarios_v5_trade as _chain_trade,
)
from workflows.national.facilities import (
    national_residue_curve_v5 as _curve_local,
)

REPO_ROOT = _facility_local.REPO_ROOT
DATA_ROOT = _facility_local.DATA_ROOT
FACILITIES = DATA_ROOT / "derived/national/facilities"
ASSIGNMENT_LOCAL_DIR = FACILITIES / "national_facility_assignment_v6"
ASSIGNMENT_TRADE_DIR = FACILITIES / "national_facility_assignment_v5"
CHAIN_LOCAL_DIR = _chain_local.OUTPUT_DIR
CHAIN_TRADE_DIR = _chain_trade.OUTPUT_DIR
FACILITY_LOCAL_DIR = _facility_local.OUTPUT_DIR
FACILITY_TRADE_DIR = (
    FACILITIES / "facility_residue_supply_v5_trade"
)
CURVE_LOCAL_DIR = _curve_local.OUTPUT_DIR
CURVE_TRADE_DIR = FACILITIES / "national_residue_curve_v5_trade"
HANDOFF_LOCAL_DIR = FACILITIES / "forestry_osecan_handoff_v6"
HANDOFF_TRADE_DIR = FACILITIES / "forestry_osecan_handoff_v6_trade"
OUTPUT_DIR = FACILITIES / "national_facility_chain_v5_trade_comparison"

ARTIFACT = "national_facility_chain_v5_trade_comparison"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "Local v6 versus Trade v5 Measured Chain Comparison"
REPORT_NAME = "LOCAL_V6_VS_TRADE_V5_COMPARISON.md"
MODULE_PATH = (
    "workflows.national.facilities.national_facility_chain_v5_trade_comparison"
)
TEST_PATH = "tests/test_national_facility_chain_v5_trade_comparison.py"

LOCAL_LABEL = "local_v6_province_capacity_floor"
TRADE_LABEL = "trade_v5_published_plus_proxy"

ASSIGNMENT_METRICS = (
    ("assigned_m3", "m3/yr", "assigned volume"),
    ("local_m3", "m3/yr", "assigned inside the origin province"),
    ("local_share", "share", "local volume / assigned"),
    ("cross_border_m3", "m3/yr", "assigned to an out-of-province facility"),
    ("cross_border_share", "share", "cross-border / assigned"),
    ("published_m3", "m3/yr", "assigned on published capacity"),
    ("published_share", "share", "published volume / assigned"),
    ("proxy_m3", "m3/yr", "assigned on proxy capacity"),
    ("proxy_share_of_assigned", "share", "proxy volume / assigned"),
    ("scaled_proxy_m3", "m3/yr", "assigned on floor-scaled proxy capacity"),
    ("facilities_used", "count", "distinct facilities receiving volume"),
    ("max_facility_m3", "m3/yr", "largest single-facility assignment"),
    ("max_facility_share", "share", "largest facility / assigned"),
    ("top5_share", "share", "top five facilities / assigned"),
    ("hhi", "index_0_1", "sum of squared facility shares"),
    (
        "capacity_shortfall_before_floor_m3_y",
        "m3/yr",
        "province capacity below harvest x 1.2 before floor scaling",
    ),
    (
        "capacity_shortfall_after_floor_m3_y",
        "m3/yr",
        "province capacity below harvest x 1.2 after floor scaling",
    ),
    (
        "provinces_below_floor_before",
        "count",
        "provinces below harvest x 1.2 before floor scaling",
    ),
    (
        "provinces_below_floor_after",
        "count",
        "provinces below harvest x 1.2 after floor scaling",
    ),
)

CHAIN_METRICS = (
    ("assigned_m3", "m3", "assigned volume"),
    ("cross_border_m3", "m3", "cross-border assigned volume"),
    ("proxy_share_of_assigned", "share", "proxy share of assigned"),
    ("published_m3", "m3", "assigned on published capacity"),
    ("proxy_m3", "m3", "assigned on proxy capacity"),
    ("sawlog_product_m3", "m3", "sawlog product"),
    ("panel_product_m3", "m3", "panel product"),
    ("pulp_product_odt", "odt", "pulp product"),
    ("pellet_product_odt", "odt", "pellet product"),
    ("bio_product_mwh", "MWh", "bio product"),
    ("process_residue_pj", "PJ", "facility process residue"),
    ("harvest_residue_gross_pj", "PJ", "gross harvest residue"),
    ("harvest_residue_recoverable_pj", "PJ", "recoverable harvest residue"),
    ("total_residue_gross_pj", "PJ", "process + gross harvest residue"),
    (
        "total_residue_recoverable_pj",
        "PJ",
        "process + recoverable harvest residue",
    ),
)

FACILITY_METRICS = (
    ("process_residue_odt", "odt", "facility-province process residue"),
    ("process_residue_pj", "PJ", "facility-province process residue"),
    ("origin_process_residue_pj", "PJ", "origin-province process residue"),
    ("facility_count", "count", "facilities in province"),
    ("facility_count_positive_residue", "count", "positive-residue facilities"),
    ("published_residue_share", "share", "published share of process residue"),
    ("proxy_residue_share", "share", "proxy share of process residue"),
    (
        "scaled_proxy_residue_share",
        "share",
        "floor-scaled proxy share of process residue",
    ),
)

PROCESS_CASES = (
    "process_availability_000",
    "process_availability_025",
    "process_availability_050",
    "process_availability_075",
)
HARVEST_CASES = (
    "harvest_recovery_035",
    "harvest_recovery_060",
    "harvest_recovery_075",
)


class ChainTradeComparisonError(RuntimeError):
    """Raised when the local/trade comparison invariant fails."""


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    fields: list[str] | tuple[str, ...],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(fields), lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        f"{row[field]:.12g}"
                        if isinstance(row.get(field), float)
                        else row.get(field, "")
                    )
                    for field in fields
                }
            )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _number(value: Any, context: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ChainTradeComparisonError(f"{context}: non-finite {value!r}")
    return number


def _assignment_metrics(
    local_summary: dict[str, Any],
    trade_summary: dict[str, Any],
) -> dict[str, float]:
    local = local_summary["scenarios"]["province_capacity_floor"]
    trade = trade_summary["scenarios"]["published_plus_proxy"]
    local_floor = local_summary["province_capacity_floor"]
    trade_floor = trade_summary["province_capacity_floor"]
    local_assigned = _number(local["assigned_m3"], "local assigned")
    trade_assigned = _number(trade["assigned_m3"], "trade assigned")
    local_cross = _number(local["cross_border_m3"], "local cross-border")
    trade_cross = _number(trade["cross_border_m3"], "trade cross-border")
    local_published = _number(local["published_m3"], "local published")
    trade_published = _number(trade["published_m3"], "trade published")
    return {
        "assigned_m3": local_assigned,
        "local_m3": local_assigned - local_cross,
        "local_share": (local_assigned - local_cross) / local_assigned,
        "cross_border_m3": local_cross,
        "cross_border_share": local_cross / local_assigned,
        "published_m3": local_published,
        "published_share": local_published / local_assigned,
        "proxy_m3": _number(local["proxy_m3"], "local proxy"),
        "proxy_share_of_assigned": _number(
            local["proxy_share_of_assigned"], "local proxy share"
        ),
        "scaled_proxy_m3": _number(
            local.get("scaled_proxy_m3", 0.0), "local scaled proxy"
        ),
        "facilities_used": _number(local["facilities_used"], "local facilities"),
        "max_facility_m3": _number(
            local["max_facility_m3"], "local max facility"
        ),
        "max_facility_share": _number(
            local["max_facility_share"], "local max facility share"
        ),
        "top5_share": _number(local["top5_share"], "local top5"),
        "hhi": _number(local["hhi"], "local hhi"),
        "capacity_shortfall_before_floor_m3_y": _number(
            local_floor["shortfall_before_m3_y"], "local shortfall"
        ),
        "capacity_shortfall_after_floor_m3_y": _number(
            local_floor["shortfall_after_m3_y"], "local shortfall after"
        ),
        "provinces_below_floor_before": _number(
            local_floor["provinces_below_floor_before"],
            "local provinces below floor",
        ),
        "provinces_below_floor_after": _number(
            local_floor["provinces_below_floor_after"],
            "local provinces below floor after",
        ),
        "trade_assigned_m3": trade_assigned,
        "trade_local_m3": trade_assigned - trade_cross,
        "trade_local_share": (trade_assigned - trade_cross) / trade_assigned,
        "trade_cross_border_m3": trade_cross,
        "trade_cross_border_share": trade_cross / trade_assigned,
        "trade_published_m3": trade_published,
        "trade_published_share": trade_published / trade_assigned,
        "trade_proxy_m3": _number(trade["proxy_m3"], "trade proxy"),
        "trade_proxy_share_of_assigned": _number(
            trade["proxy_share_of_assigned"], "trade proxy share"
        ),
        "trade_scaled_proxy_m3": _number(
            trade.get("scaled_proxy_m3", 0.0), "trade scaled proxy"
        ),
        "trade_facilities_used": _number(
            trade["facilities_used"], "trade facilities"
        ),
        "trade_max_facility_m3": _number(
            trade["max_facility_m3"], "trade max facility"
        ),
        "trade_max_facility_share": _number(
            trade["max_facility_share"], "trade max facility share"
        ),
        "trade_top5_share": _number(trade["top5_share"], "trade top5"),
        "trade_hhi": _number(trade["hhi"], "trade hhi"),
        "trade_capacity_shortfall_before_floor_m3_y": _number(
            trade_floor["shortfall_before_m3_y"], "trade shortfall"
        ),
        "trade_capacity_shortfall_after_floor_m3_y": _number(
            trade_floor["shortfall_after_m3_y"], "trade shortfall after"
        ),
        "trade_provinces_below_floor_before": _number(
            trade_floor["provinces_below_floor_before"],
            "trade provinces below floor",
        ),
        "trade_provinces_below_floor_after": _number(
            trade_floor["provinces_below_floor_after"],
            "trade provinces below floor after",
        ),
    }


def _assignment_rows(metrics: dict[str, float]) -> list[dict[str, Any]]:
    pairs = {
        "assigned_m3": "trade_assigned_m3",
        "local_m3": "trade_local_m3",
        "local_share": "trade_local_share",
        "cross_border_m3": "trade_cross_border_m3",
        "cross_border_share": "trade_cross_border_share",
        "published_m3": "trade_published_m3",
        "published_share": "trade_published_share",
        "proxy_m3": "trade_proxy_m3",
        "proxy_share_of_assigned": "trade_proxy_share_of_assigned",
        "scaled_proxy_m3": "trade_scaled_proxy_m3",
        "facilities_used": "trade_facilities_used",
        "max_facility_m3": "trade_max_facility_m3",
        "max_facility_share": "trade_max_facility_share",
        "top5_share": "trade_top5_share",
        "hhi": "trade_hhi",
        "capacity_shortfall_before_floor_m3_y": (
            "trade_capacity_shortfall_before_floor_m3_y"
        ),
        "capacity_shortfall_after_floor_m3_y": (
            "trade_capacity_shortfall_after_floor_m3_y"
        ),
        "provinces_below_floor_before": "trade_provinces_below_floor_before",
        "provinces_below_floor_after": "trade_provinces_below_floor_after",
    }
    rows = []
    for metric, unit, note in ASSIGNMENT_METRICS:
        local = metrics[metric]
        trade = metrics[pairs[metric]]
        rows.append(
            {
                "metric": metric,
                "unit": unit,
                "local_v6_value": local,
                "trade_v5_value": trade,
                "delta_trade_minus_local": trade - local,
                "note": note,
            }
        )
    return rows


def _chain_rows(
    local_summary: dict[str, Any],
    trade_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for scenario in ("100m", "119m", "150m"):
        local = local_summary["scenarios"][scenario]
        trade = trade_summary["scenarios"][scenario]
        for metric, unit, note in CHAIN_METRICS:
            local_value = _number(
                local[metric], f"local chain {scenario} {metric}"
            )
            trade_value = _number(
                trade[metric], f"trade chain {scenario} {metric}"
            )
            rows.append(
                {
                    "scenario": scenario,
                    "metric": metric,
                    "unit": unit,
                    "local_v6_value": local_value,
                    "trade_v5_value": trade_value,
                    "delta_trade_minus_local": trade_value - local_value,
                    "note": note,
                }
            )
    return rows


def _facility_rows(
    local_summary: dict[str, Any],
    trade_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    local = {row["province"]: row for row in local_summary["province_totals"]}
    trade = {row["province"]: row for row in trade_summary["province_totals"]}
    if set(local) != set(trade):
        raise ChainTradeComparisonError(
            f"facility province sets differ: {sorted(set(local) ^ set(trade))}"
        )
    rows = []
    for province in local:
        for metric, unit, note in FACILITY_METRICS:
            if metric == "published_residue_share":
                local_value = 1.0 - _number(
                    local[province]["proxy_residue_share"],
                    f"local {province} proxy share",
                )
                trade_value = 1.0 - _number(
                    trade[province]["proxy_residue_share"],
                    f"trade {province} proxy share",
                )
            else:
                local_value = _number(
                    local[province][metric], f"local {province} {metric}"
                )
                trade_value = _number(
                    trade[province][metric], f"trade {province} {metric}"
                )
            rows.append(
                {
                    "province": province,
                    "metric": metric,
                    "unit": unit,
                    "local_v6_value": local_value,
                    "trade_v5_value": trade_value,
                    "delta_trade_minus_local": trade_value - local_value,
                    "note": note,
                }
            )
    return rows


def _curve_rows(
    local_summary: dict[str, Any],
    trade_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = []
    for case in PROCESS_CASES:
        local = _number(
            local_summary["scenario_totals"]["process"][case]["residue_pj"],
            f"local {case}",
        )
        trade = _number(
            trade_summary["scenario_totals"]["process"][case]["residue_pj"],
            f"trade {case}",
        )
        rows.append(
            {
                "scope": "national",
                "province": "ALL",
                "stream": "process",
                "case": case,
                "unit": "PJ",
                "local_v6_value": local,
                "trade_v5_value": trade,
                "delta_trade_minus_local": trade - local,
            }
        )
    for case in HARVEST_CASES:
        local = _number(
            local_summary["rsyc_comparison"]["harvest_cases"][case][
                "recoverable_pj"
            ],
            f"local {case}",
        )
        trade = _number(
            trade_summary["rsyc_comparison"]["harvest_cases"][case][
                "recoverable_pj"
            ],
            f"trade {case}",
        )
        rows.append(
            {
                "scope": "national",
                "province": "ALL",
                "stream": "harvest",
                "case": case,
                "unit": "PJ",
                "local_v6_value": local,
                "trade_v5_value": trade,
                "delta_trade_minus_local": trade - local,
            }
        )
    local_prov = local_summary["province_totals"]
    trade_prov = trade_summary["province_totals"]
    if set(local_prov) != set(trade_prov):
        raise ChainTradeComparisonError("curve province sets differ")
    for province in local_prov:
        for case in PROCESS_CASES:
            local = _number(
                local_prov[province][f"{case}_pj"], f"local {province} {case}"
            )
            trade = _number(
                trade_prov[province][f"{case}_pj"], f"trade {province} {case}"
            )
            rows.append(
                {
                    "scope": "province",
                    "province": province,
                    "stream": "process",
                    "case": case,
                    "unit": "PJ",
                    "local_v6_value": local,
                    "trade_v5_value": trade,
                    "delta_trade_minus_local": trade - local,
                }
            )
        for ratio, case in zip((0.35, 0.60, 0.75), HARVEST_CASES):
            key = f"harvest_recovery_{round(ratio * 100):03d}_pj"
            local = _number(
                local_prov[province][key], f"local {province} {key}"
            )
            trade = _number(
                trade_prov[province][key], f"trade {province} {key}"
            )
            rows.append(
                {
                    "scope": "province",
                    "province": province,
                    "stream": "harvest",
                    "case": case,
                    "unit": "PJ",
                    "local_v6_value": local,
                    "trade_v5_value": trade,
                    "delta_trade_minus_local": trade - local,
                }
            )
    return rows


def _handoff_totals(path: Path) -> dict[tuple[str, str, str], float]:
    totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in _read_rows(path):
        if str(row["residue_class"]).strip() not in {"TOTAL", "ALL"}:
            continue
        stream = str(row["stream"]).strip()
        case = str(row["availability_case"]).strip()
        zone = str(row["zone_id"]).strip()
        totals[(zone, stream, case)] += _number(
            row["pj"], f"handoff {path.name} {zone} {stream} {case}"
        )
    return totals


def _handoff_rows(
    local_path: Path,
    trade_path: Path,
) -> list[dict[str, Any]]:
    local = _handoff_totals(local_path)
    trade = _handoff_totals(trade_path)
    if set(local) != set(trade):
        raise ChainTradeComparisonError(
            f"handoff keys differ: {sorted(set(local) ^ set(trade))[:5]}"
        )
    rows = []
    for zone, stream, case in sorted(local):
        local_value = local[(zone, stream, case)]
        trade_value = trade[(zone, stream, case)]
        rows.append(
            {
                "zone_id": zone,
                "stream": stream,
                "case": case,
                "unit": "PJ",
                "local_v6_value": local_value,
                "trade_v5_value": trade_value,
                "delta_trade_minus_local": trade_value - local_value,
            }
        )
    return rows


def _fmt(value: float, digits: int = 3) -> str:
    return f"{value:,.{digits}f}"


def _report(
    assignment_rows: list[dict[str, Any]],
    chain_rows: list[dict[str, Any]],
    facility_rows: list[dict[str, Any]],
    curve_rows: list[dict[str, Any]],
    handoff_rows: list[dict[str, Any]],
    checks: list[dict[str, Any]],
) -> str:
    lines = [
        "# Local v6 versus Trade v5 Measured Chain Comparison",
        "",
        ("The local case is the assignment-v6 `province_capacity_floor` chain v5: "
        "capacity v3, the 1.2x provincial floor as primary, local-first "
        "allocation, and cross-border only for the local residual. The trade "
        "case is the assignment-v5 `published_plus_proxy` sensitivity rebuilt "
        "with the same measured `national_product_chain_v3` coefficients and "
        "total-residue scenarios. Both use 18.5 GJ/odt."),
        "",
        ("All values are model estimates: `model_estimate=true`, "
        "`observed=false`, `policy_grade=false`. The trade case is a "
        "trade-permissive counterfactual, not a forecast and not evidence of "
        "provincial self-sufficiency. No UI or public OSeCAN surface is "
        "changed."),
        "",
        "## Assignment at 119 Mm3",
        "",
        "| Metric | Unit | Local v6 | Trade v5 | Delta trade-local |",
        "|---|---|---:|---:|---:|",
    ]
    for row in assignment_rows:
        lines.append(
            f"| {row['metric']} | {row['unit']} | "
            f"{_fmt(float(row['local_v6_value']))} | "
            f"{_fmt(float(row['trade_v5_value']))} | "
            f"{_fmt(float(row['delta_trade_minus_local']))} |"
        )
    lines += [
        "",
        ("The capacity shortfall columns are the province shortfall before and "
        "after the 1.2x floor; the trade case runs before floor scaling, which "
        "is why `capacity_shortfall_before_floor` matches while "
        "`after_floor` describes the local case."),
        "",
        "## Chain products and residue",
        "",
        "| Case | Metric | Local v6 | Trade v5 | Delta |",
        "|---|---|---:|---:|---:|",
    ]
    for row in chain_rows:
        lines.append(
            f"| {row['scenario']} | {row['metric']} | "
            f"{_fmt(float(row['local_v6_value']))} | "
            f"{_fmt(float(row['trade_v5_value']))} | "
            f"{_fmt(float(row['delta_trade_minus_local']))} |"
        )
    lines += [
        "",
        ("National products and residue are unchanged by construction; the "
        "trade case moves the same national totals across facilities and "
        "provinces."),
        "",
        "## Process residue by facility province (119 Mm3)",
        "",
        "| Province | Local PJ | Trade PJ | Delta PJ | Local odt | Trade odt |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    by_province: dict[str, dict[str, Any]] = {}
    for row in facility_rows:
        by_province.setdefault(str(row["province"]), {})[str(row["metric"])] = row
    for province in by_province:
        pj = by_province[province]["process_residue_pj"]
        odt = by_province[province]["process_residue_odt"]
        lines.append(
            f"| {province} | {_fmt(float(pj['local_v6_value']))} | "
            f"{_fmt(float(pj['trade_v5_value']))} | "
            f"{_fmt(float(pj['delta_trade_minus_local']))} | "
            f"{_fmt(float(odt['local_v6_value']), 0)} | "
            f"{_fmt(float(odt['trade_v5_value']), 0)} |"
        )
    lines += [
        "",
        "## Residue curve cases",
        "",
        "| Stream | Case | Local v6 PJ | Trade v5 PJ | Delta PJ |",
        "|---|---|---:|---:|---:|",
    ]
    for row in curve_rows:
        if row["scope"] == "national":
            lines.append(
                f"| {row['stream']} | {row['case']} | "
                f"{_fmt(float(row['local_v6_value']))} | "
                f"{_fmt(float(row['trade_v5_value']))} | "
                f"{_fmt(float(row['delta_trade_minus_local']))} |"
            )
    lines += [
        "",
        "The province-level curve table is in `curve_case_comparison.csv`.",
        "",
        "## OSeCAN handoff",
        "",
        ("Process-residue zone PJ differs; harvest-residue zone PJ is identical "
        "because harvest residue is origin-province only."),
        "",
        "| Zone | Stream | Case | Local v6 PJ | Trade v5 PJ | Delta PJ |",
        "|---|---|---|---:|---:|---:|",
    ]
    for row in handoff_rows:
        if abs(float(row["delta_trade_minus_local"])) < 1e-9:
            continue
        lines.append(
            f"| {row['zone_id']} | {row['stream']} | {row['case']} | "
            f"{_fmt(float(row['local_v6_value']))} | "
            f"{_fmt(float(row['trade_v5_value']))} | "
            f"{_fmt(float(row['delta_trade_minus_local']))} |"
        )
    lines += [
        "",
        "### BC zonal process comparison (PJ)",
        "",
        "| Case | Local v6 | Trade v5 | Delta |",
        "|---|---:|---:|---:|",
    ]
    bc_totals: dict[tuple[str, str, str], float] = defaultdict(float)
    for row in handoff_rows:
        if str(row["zone_id"]).startswith("B"):
            key = (str(row["stream"]), str(row["case"]))
            bc_totals[(key[0], key[1], "local")] += float(
                row["local_v6_value"]
            )
            bc_totals[(key[0], key[1], "trade")] += float(
                row["trade_v5_value"]
            )
    for case in PROCESS_CASES:
        local_value = bc_totals.get(("process", case, "local"), 0.0)
        trade_value = bc_totals.get(("process", case, "trade"), 0.0)
        lines.append(
            f"| {case} | {_fmt(local_value)} | {_fmt(trade_value)} | "
            f"{_fmt(trade_value - local_value)} |"
        )
    lines += [
        "",
        "## Validation",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        lines.append(
            f"| {check['name']} | {check['status']} | {check['detail']} |"
        )
    lines += [
        "",
        "## Labels and caveats",
        "",
        ("- `model_estimate=true`, `observed=false`, `policy_grade=false`; no "
        "national supply, policy, AAC, tenure, or investment claim."),
        ("- The trade case is an upper-bound cross-border counterfactual, not a "
        "trade forecast."),
        ("- Process and harvest residue remain separate streams; no combined "
        "OSeCAN row is written."),
        ("- Capacity status mixes published and class-median proxy capacity; "
        "the bases are not interchangeable."),
        "- The official OSeCAN tree is read-only and unchanged.",
        "",
        ("Run `.venv/bin/python -m "
        f"{MODULE_PATH}` to reproduce the comparison."),
        "",
        "No policy/AAC claim is made.",
        "",
    ]
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the local-versus-trade comparison and return the summary."""

    local_assignment = _read_json(ASSIGNMENT_LOCAL_DIR / "summary.json")
    trade_assignment = _read_json(ASSIGNMENT_TRADE_DIR / "summary.json")
    local_chain = _read_json(CHAIN_LOCAL_DIR / "summary.json")
    trade_chain = _read_json(CHAIN_TRADE_DIR / "summary.json")
    local_facility = _read_json(FACILITY_LOCAL_DIR / "summary.json")
    trade_facility = _read_json(FACILITY_TRADE_DIR / "summary.json")
    local_curve = _read_json(CURVE_LOCAL_DIR / "summary.json")
    trade_curve = _read_json(CURVE_TRADE_DIR / "summary.json")
    local_handoff = _read_json(HANDOFF_LOCAL_DIR / "summary.json")
    trade_handoff = _read_json(HANDOFF_TRADE_DIR / "summary.json")

    for label, summary in (
        ("local assignment", local_assignment),
        ("trade assignment", trade_assignment),
        ("local chain", local_chain),
        ("trade chain", trade_chain),
        ("local facility", local_facility),
        ("trade facility", trade_facility),
        ("local curve", local_curve),
        ("trade curve", trade_curve),
        ("local handoff", local_handoff),
        ("trade handoff", trade_handoff),
    ):
        validation = summary.get("validation", {})
        if label.endswith("assignment"):
            violations = int(validation.get("known_capacity_violations", -1))
            gaps = validation.get("conservation_gap_m3", {})
            gap_ok = all(abs(float(value)) < 1e-6 for value in gaps.values())
            if (
                violations != 0
                or not gap_ok
                or validation.get("no_unbounded_facility") is not True
            ):
                raise ChainTradeComparisonError(
                    f"{label} validation is not pass"
                )
            continue
        if str(summary.get("status")) != "pass":
            raise ChainTradeComparisonError(f"{label} is not pass")
        if validation.get("passed") is not True:
            raise ChainTradeComparisonError(f"{label} validation is not pass")

    metrics = _assignment_metrics(local_assignment, trade_assignment)
    assignment_rows = _assignment_rows(metrics)
    chain_rows = _chain_rows(local_chain, trade_chain)
    facility_rows = _facility_rows(local_facility, trade_facility)
    curve_rows = _curve_rows(local_curve, trade_curve)
    handoff_rows = _handoff_rows(
        HANDOFF_LOCAL_DIR / "forestry_residue_handoff.csv",
        HANDOFF_TRADE_DIR / "forestry_residue_handoff.csv",
    )

    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "PASS" if passed else "FAIL",
                "detail": detail,
            }
        )

    check(
        "assigned_volume_equal",
        abs(metrics["assigned_m3"] - metrics["trade_assigned_m3"]) < 1e-6,
        "local and trade assignment totals match",
    )
    check(
        "cross_border_trade_higher",
        metrics["trade_cross_border_m3"] > metrics["cross_border_m3"],
        (
            f"trade {metrics['trade_cross_border_m3']:,.0f} m3 > local "
            f"{metrics['cross_border_m3']:,.0f} m3"
        ),
    )
    national_chain = [
        row
        for row in chain_rows
        if row["metric"]
        in {
            "sawlog_product_m3",
            "panel_product_m3",
            "pulp_product_odt",
            "pellet_product_odt",
            "bio_product_mwh",
            "process_residue_pj",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_pj",
            "total_residue_gross_pj",
            "total_residue_recoverable_pj",
        }
    ]
    check(
        "national_products_residue_equal",
        all(
            abs(float(row["delta_trade_minus_local"])) < 1e-6
            for row in national_chain
        ),
        "all three harvest cases share national products and residue totals",
    )
    process_total = sum(
        float(row["delta_trade_minus_local"])
        for row in facility_rows
        if row["metric"] == "process_residue_pj"
    )
    check(
        "province_process_conserves",
        abs(process_total) < 1e-6,
        f"sum of province process deltas = {process_total:.12g} PJ",
    )
    harvest_handoff = [
        row
        for row in handoff_rows
        if row["stream"] == "harvest"
    ]
    check(
        "harvest_handoff_identical",
        all(
            abs(float(row["delta_trade_minus_local"])) < 1e-9
            for row in harvest_handoff
        ),
        "harvest-residue handoff is origin-province only and unchanged",
    )
    process_zone_total = sum(
        float(row["delta_trade_minus_local"])
        for row in handoff_rows
        if row["stream"] == "process"
    )
    check(
        "process_handoff_conserves",
        abs(process_zone_total) < 1e-6,
        f"sum of process zone deltas = {process_zone_total:.12g} PJ",
    )
    check(
        "osecan_tree_unchanged",
        local_handoff["osecan_integrity"]["unchanged"] is True
        and trade_handoff["osecan_integrity"]["unchanged"] is True,
        "official OSeCAN tree hash unchanged before and after both builds",
    )
    check(
        "labels",
        local_handoff["labels"]["official_osecan_changed"] is False
        and trade_handoff["labels"]["official_osecan_changed"] is False,
        "no official OSeCAN change is claimed",
    )

    failed = [check for check in checks if check["status"] != "PASS"]
    if failed:
        raise ChainTradeComparisonError(
            "comparison validation failed: "
            + " | ".join(check["name"] for check in failed)
        )

    target = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    target.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target / "assignment_comparison.csv",
        assignment_rows,
        [
            "metric",
            "unit",
            "local_v6_value",
            "trade_v5_value",
            "delta_trade_minus_local",
            "note",
        ],
    )
    _write_csv(
        target / "chain_comparison.csv",
        chain_rows,
        [
            "scenario",
            "metric",
            "unit",
            "local_v6_value",
            "trade_v5_value",
            "delta_trade_minus_local",
            "note",
        ],
    )
    _write_csv(
        target / "facility_province_comparison.csv",
        facility_rows,
        [
            "province",
            "metric",
            "unit",
            "local_v6_value",
            "trade_v5_value",
            "delta_trade_minus_local",
            "note",
        ],
    )
    _write_csv(
        target / "curve_case_comparison.csv",
        curve_rows,
        [
            "scope",
            "province",
            "stream",
            "case",
            "unit",
            "local_v6_value",
            "trade_v5_value",
            "delta_trade_minus_local",
        ],
    )
    _write_csv(
        target / "osecan_handoff_comparison.csv",
        handoff_rows,
        [
            "zone_id",
            "stream",
            "case",
            "unit",
            "local_v6_value",
            "trade_v5_value",
            "delta_trade_minus_local",
        ],
    )
    (target / REPORT_NAME).write_text(
        _report(
            assignment_rows,
            chain_rows,
            facility_rows,
            curve_rows,
            handoff_rows,
            checks,
        ),
        encoding="utf-8",
    )

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "status": "pass",
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "national_claim": False,
            "official_osecan_changed": False,
        },
        "local_case": {
            "label": LOCAL_LABEL,
            "assignment_artifact": "national_facility_assignment_v6",
            "chain_artifact": "national_facility_chain_scenarios_v5",
            "facility_artifact": "facility_residue_supply_v5",
            "curve_artifact": "national_residue_curve_v5",
            "handoff_artifact": "forestry_osecan_handoff_v6",
        },
        "trade_case": {
            "label": TRADE_LABEL,
            "assignment_artifact": "national_facility_assignment_v5",
            "chain_artifact": "national_facility_chain_scenarios_v5_trade",
            "facility_artifact": "facility_residue_supply_v5_trade",
            "curve_artifact": "national_residue_curve_v5_trade",
            "handoff_artifact": "forestry_osecan_handoff_v6_trade",
        },
        "assignment_119m": {
            "local": {
                key: value
                for key, value in metrics.items()
                if not key.startswith("trade_")
            },
            "trade": {
                key.removeprefix("trade_"): value
                for key, value in metrics.items()
                if key.startswith("trade_")
            },
        },
        "chain_119m_national": {
            row["metric"]: {
                "unit": row["unit"],
                "local_v6_value": float(row["local_v6_value"]),
                "trade_v5_value": float(row["trade_v5_value"]),
                "delta_trade_minus_local": float(
                    row["delta_trade_minus_local"]
                ),
            }
            for row in chain_rows
            if row["scenario"] == "119m"
        },
        "validation": {
            "passed": True,
            "failed_check_count": 0,
            "checks": checks,
        },
        "caveats": [
            ("The trade case is an upper-bound cross-border counterfactual, not "
            "an interprovincial trade forecast."),
            ("The local case remains the frozen base because the 1.2x floor is "
            "the documented primary assumption."),
            ("Process and harvest residue remain separate; no combined OSeCAN "
            "row is written."),
            "The official OSeCAN tree is read-only and unchanged.",
        ],
        "outputs": {},
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }
    outputs = {}
    for path in sorted(target.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
    summary["outputs"] = outputs
    (target / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "labels": summary["labels"],
        "local_case": summary["local_case"],
        "trade_case": summary["trade_case"],
        "outputs": {
            path.name: {
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in sorted(target.iterdir())
            if path.is_file()
        },
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    (target / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "local": summary["local_case"]["label"],
                "trade": summary["trade_case"]["label"],
                "validation": summary["validation"]["passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
