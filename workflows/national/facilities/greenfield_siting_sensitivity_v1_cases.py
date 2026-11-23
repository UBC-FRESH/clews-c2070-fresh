"""Transport-only greenfield siting sensitivity v1: 100/119/150 Mm3 cases.

This extends ``greenfield_siting_sensitivity_v1`` to the documented 100 and
150 Mm3 harvest cases.  The engine, candidate universe, capacity v3 bounds,
generic module assumption and transport cost curve are unchanged from v1:

* existing facilities stay fixed with their v3 capacity and the documented
  1.2x provincial floor;
* a greenfield module may be placed at any positive-volume supply node in a
  province with a published road factor;
* each module is a class-agnostic generic transport sink with unlimited
  throughput in the screen;
* for k = 0..20 modules, choose the module set and the node-to-facility flow
  that minimise 2026 CAD truck transport cost.

The 100 and 150 Mm3 node harvest vectors are the same provincial allocations
and within-province scaling used by the committed v10 case extension.  The
committed 119 Mm3 v1 artifact is loaded unchanged for the primary case, so the
case series is directly comparable.

Labels: ``transport_only_siting_screen`` / ``not_investment_advice`` /
``model_estimate`` / ``observed=false`` / ``policy_grade=false``.  No capital,
permitting, landbase, class-capacity, national, policy, UI or official OSeCAN
claim is made.  A ``D`` in either the parent or data repository git status
halts the build.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

from workflows.national.facilities import (
    greenfield_siting_sensitivity_v1 as v1,
)
from workflows.national.facilities import (
    national_facility_assignment_v10 as v10,
)
from workflows.national.facilities import (
    national_facility_assignment_v10_cases as v10_cases,
)

REPO_ROOT = v1.REPO_ROOT
DATA_ROOT = v1.DATA_ROOT
V10_DIR = v1.V10_DIR
V1_DIR = v1.OUTPUT_DIR
V1_SUMMARY_JSON = V1_DIR / "summary.json"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/greenfield_siting_sensitivity_v1_cases"
)

ARTIFACT = "greenfield_siting_sensitivity_v1_cases"
ARTIFACT_VERSION = "1.0.0"
CASES = (
    ("100m", 100_000_000.1),
    ("119m", 118_977_339.1),
    ("150m", 150_000_000.2),
)
CASE_ORDER = tuple(case for case, _ in CASES)
CASE_TOTALS = dict(CASES)
PRIMARY_CASE = "119m"
TOTAL_TOLERANCE_M3 = 1.0
SNAPSHOT_K = (0, 5, 10, 15, 20)

TRADEOFF_FIELDS = ["case", *v1.TRADEOFF_FIELDS]
MODULE_FIELDS = ["case", *v1.MODULE_FIELDS]
NODE_ASSIGNMENT_FIELDS = ["case", *v1.NODE_ASSIGNMENT_FIELDS]
EXISTING_SERVICE_FIELDS = ["case", *v1.EXISTING_SERVICE_FIELDS]
COMPARISON_FIELDS = [
    "case",
    "k",
    "harvest_case_m3",
    "assigned_m3",
    "transport_cost_cad",
    "transport_cost_cad_m3",
    "mean_road_distance_km",
    "median_road_distance_km",
    "p90_road_distance_km",
    "max_road_distance_km",
    "share_ge_150_km",
    "share_ge_300_km",
    "module_served_m3",
    "module_served_share",
    "modules_serving",
    "existing_served_share",
    "existing_utilization",
    "delta_transport_cost_vs_119_cad",
    "delta_transport_cost_vs_119_pct",
    "delta_mean_road_distance_vs_119_km",
    "delta_share_ge_150_vs_119",
    "delta_share_ge_300_vs_119",
    "delta_module_served_share_vs_119",
    "evidence_label",
    "observed",
    "policy_grade",
]
HEADLINE_FIELDS = [
    "case",
    "harvest_case_m3",
    "k20_transport_cost_cad",
    "k20_transport_cost_cad_m3",
    "k20_mean_road_distance_km",
    "k20_median_road_distance_km",
    "k20_p90_road_distance_km",
    "k20_share_ge_150_km",
    "k20_share_ge_300_km",
    "k20_module_served_share",
    "k20_modules_serving",
    "k20_existing_utilization",
    "k20_existing_served_share",
    "cost_reduction_vs_k0_cad",
    "cost_reduction_vs_k0_pct",
    "mean_reduction_vs_k0_km",
    "delta_transport_cost_vs_119_cad",
    "delta_transport_cost_vs_119_pct",
    "delta_mean_road_distance_vs_119_km",
    "delta_share_ge_150_vs_119",
    "delta_share_ge_300_vs_119",
    "delta_module_served_share_vs_119",
    "first_k_marginal_below_1pct",
    "evidence_label",
    "observed",
    "policy_grade",
]


_num = v1._num
_pct = v1._pct
_billions = v1._billions


class GreenfieldSitingCasesError(v1.GreenfieldSitingError):
    """Raised when a multi-case siting invariant fails."""


# ---------------------------------------------------------------------------
# I/O and row helpers.
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise GreenfieldSitingCasesError(f"missing input: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _with_case(
    rows: Sequence[Mapping[str, Any]], case: str
) -> list[dict[str, Any]]:
    return [{"case": case, **dict(row)} for row in rows]


def _case_tradeoff(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(row) for row in payload["k_tradeoff"]]


def _row_by_k(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    return {int(row["k"]): row for row in rows}


def _case_total(payload: Mapping[str, Any]) -> float:
    return float(payload["harvest_case_m3"])


def _first_diminishing_k(
    tradeoff: Sequence[Mapping[str, Any]],
) -> int | None:
    k0_cost = float(tradeoff[0]["transport_cost_cad"])
    for row in tradeoff[1:]:
        if float(row["marginal_transport_cost_reduction_cad"]) < 0.01 * k0_cost:
            return int(row["k"])
    return None


# ---------------------------------------------------------------------------
# Baselines and the committed 119 Mm3 case.
# ---------------------------------------------------------------------------

def _load_v1_artifact() -> dict[str, Any]:
    if not V1_SUMMARY_JSON.is_file():
        raise GreenfieldSitingCasesError(
            f"v1 siting summary is missing: {V1_SUMMARY_JSON}"
        )
    summary = json.loads(V1_SUMMARY_JSON.read_text(encoding="utf-8"))
    if summary.get("artifact") != "greenfield_siting_sensitivity_v1":
        raise GreenfieldSitingCasesError("unexpected v1 siting artifact")
    if summary.get("artifact_version") != "1.0.0":
        raise GreenfieldSitingCasesError("unexpected v1 siting version")
    if summary.get("status") != "complete":
        raise GreenfieldSitingCasesError("v1 siting status is not complete")
    if not summary.get("validation", {}).get("all_passed"):
        raise GreenfieldSitingCasesError("v1 siting did not pass validation")
    return summary


def _v10_baseline(case: str, v1_summary: Mapping[str, Any]) -> dict[str, Any]:
    if case == PRIMARY_CASE:
        metrics = dict(v1_summary["baselines"]["v10_existing_only"]["metrics"])
    else:
        path = V10_DIR / f"case_summary_{case}.json"
        if not path.is_file():
            raise GreenfieldSitingCasesError(f"missing v10 case summary: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("artifact") != "national_facility_assignment_v10":
            raise GreenfieldSitingCasesError(
                f"{path}: unexpected v10 artifact"
            )
        if payload.get("artifact_version") not in {"10.0.0", "10.1.0"}:
            raise GreenfieldSitingCasesError(
                f"{path}: unexpected v10 version"
            )
        if payload.get("harvest_case") != case:
            raise GreenfieldSitingCasesError(
                f"{path}: unexpected harvest case"
            )
        if not payload.get("validation", {}).get("all_passed"):
            raise GreenfieldSitingCasesError(
                f"{path}: v10 case did not validate"
            )
        metrics = dict(payload["v10_primary"])
    assigned = float(metrics["assigned_m3"])
    if abs(assigned - CASE_TOTALS[case]) > TOTAL_TOLERANCE_M3:
        raise GreenfieldSitingCasesError(
            f"{case}: v10 baseline volume {assigned!r} does not match "
            f"{CASE_TOTALS[case]!r}"
        )
    metrics["modules_serving"] = 0.0
    metrics["module_served_m3"] = 0.0
    metrics["module_served_share"] = 0.0
    metrics["existing_served_m3"] = assigned
    return metrics


def _primary_payload(v1_summary: Mapping[str, Any]) -> dict[str, Any]:
    counts = v1_summary["counts"]
    tradeoff = _with_case(v1_summary["k_tradeoff"], PRIMARY_CASE)
    modules = _with_case(v1_summary["module_locations"], PRIMARY_CASE)
    k0 = _with_case(_read_csv(V1_DIR / "node_assignment_k0.csv"), PRIMARY_CASE)
    k20 = _with_case(
        _read_csv(V1_DIR / "node_assignment_k20.csv"), PRIMARY_CASE
    )
    existing = _with_case(
        _read_csv(V1_DIR / "existing_capacity_service_k0_k20.csv"),
        PRIMARY_CASE,
    )
    return {
        "case": PRIMARY_CASE,
        "harvest_case_m3": CASE_TOTALS[PRIMARY_CASE],
        "source": str(V1_SUMMARY_JSON.relative_to(REPO_ROOT)),
        "k_tradeoff": tradeoff,
        "module_locations": modules,
        "node_assignment_k0": k0,
        "node_assignment_k20": k20,
        "existing_capacity_service": existing,
        "baseline_v10": _v10_baseline(PRIMARY_CASE, v1_summary),
        "counts": {
            "node_count": int(counts["input_nodes"]),
            "positive_nodes": int(counts["positive_nodes"]),
            "candidate_modules": int(counts["candidate_modules"]),
            "eligible_facilities": int(counts["eligible_facilities"]),
            "excluded_candidate_provinces": int(
                counts["excluded_candidate_provinces"]
            ),
            "lp_solves": int(counts["lp_solves"]),
        },
        "existing_capacity_m3_y": float(
            v1_summary["method"]["existing_capacity_m3_y"]
        ),
        "excluded_candidates": v1_summary["method"][
            "excluded_candidate_provinces"
        ],
        "engine": "committed_greenfield_siting_sensitivity_v1",
    }


# ---------------------------------------------------------------------------
# Case engine.
# ---------------------------------------------------------------------------

def _scaled_positive_nodes(
    case: str,
) -> tuple[tuple[v10.SupplyNode, ...], float, float]:
    nodes = v1._load_nodes()
    target, case_total = v10_cases._case_harvest(case)
    scaled, node_total = v10_cases._scaled_nodes(nodes, target)
    if abs(node_total - case_total) > TOTAL_TOLERANCE_M3:
        raise GreenfieldSitingCasesError(
            f"{case}: scaled node total {node_total!r} != {case_total!r}"
        )
    positive = tuple(node for node in scaled if node.harvest_m3 > 0.0)
    base_positive = tuple(node for node in nodes if node.harvest_m3 > 0.0)
    if len(positive) != len(base_positive):
        raise GreenfieldSitingCasesError(
            f"{case}: positive node count changed"
        )
    if [node.node_id for node in positive] != [
        node.node_id for node in base_positive
    ]:
        raise GreenfieldSitingCasesError(
            f"{case}: positive node identities changed"
        )
    return positive, case_total, math.fsum(node.harvest_m3 for node in positive)


def _run_case(case: str) -> dict[str, Any]:
    if case == PRIMARY_CASE:
        raise GreenfieldSitingCasesError(
            "the primary case is loaded from the committed v1 artifact"
        )
    v1_summary = _load_v1_artifact()
    v10_reference = v1._load_v10_reference()
    nodes = v1._load_nodes()
    positive_base = tuple(node for node in nodes if node.harvest_m3 > 0.0)
    facilities, factors = v1._load_facilities(v10_reference)
    candidates, excluded = v1._build_candidates(positive_base, factors)
    parameters = v1.v9.load_transport_parameters()
    line = v10._cost_line(parameters)
    existing_distance, module_distance = v1._distance_matrices(
        positive_base, facilities, candidates, factors
    )
    capacities = np.array(
        [float(facility.capacity.capacity_m3_y) for facility in facilities],
        dtype=float,
    )
    existing_capacity_m3_y = math.fsum(float(value) for value in capacities)
    positive, case_total, total_harvest_m3 = _scaled_positive_nodes(case)
    harvest = np.array([node.harvest_m3 for node in positive], dtype=float)
    results, lp_solves = v1._greedy_select(
        positive,
        facilities,
        candidates,
        existing_distance,
        module_distance,
        capacities,
        harvest,
        factors,
        line,
        total_harvest_m3,
        existing_capacity_m3_y,
    )
    lower_bounds = v1._build_lower_bounds(
        results,
        existing_distance,
        module_distance,
        harvest,
        line,
    )
    baseline = _v10_baseline(case, v1_summary)
    tradeoff = _with_case(
        v1._build_tradeoff_rows(results, baseline, lower_bounds), case
    )
    modules = _with_case(v1._build_module_rows(results, candidates), case)
    existing = _with_case(
        v1._build_existing_service_rows(results, facilities), case
    )
    k0 = _with_case(v1._build_node_assignment_rows(results[0]), case)
    k20 = _with_case(v1._build_node_assignment_rows(results[-1]), case)
    return {
        "case": case,
        "harvest_case_m3": case_total,
        "source": "greenfield_siting_sensitivity_v1_cases engine",
        "k_tradeoff": tradeoff,
        "module_locations": modules,
        "node_assignment_k0": k0,
        "node_assignment_k20": k20,
        "existing_capacity_service": existing,
        "baseline_v10": baseline,
        "counts": {
            "node_count": len(nodes),
            "positive_nodes": len(positive),
            "candidate_modules": len(candidates),
            "eligible_facilities": len(facilities),
            "excluded_candidate_provinces": len(excluded),
            "lp_solves": lp_solves,
        },
        "existing_capacity_m3_y": existing_capacity_m3_y,
        "excluded_candidates": excluded,
        "engine": (
            "greedy k-median/facility-location screening with exact "
            "capacitated transport LP re-evaluation (HiGHS)"
        ),
    }


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------

def _validate_case(
    payload: Mapping[str, Any],
    *,
    line: v10.CostLine,
    expected_facilities: int,
    expected_candidates: int,
    expected_capacity_m3_y: float,
) -> dict[str, Any]:
    case = str(payload["case"])
    total = CASE_TOTALS[case]
    tradeoff = payload["k_tradeoff"]
    module_rows = payload["module_locations"]
    k0_rows = payload["node_assignment_k0"]
    k20_rows = payload["node_assignment_k20"]
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks[f"{case}:{name}"] = bool(condition)
        if not condition:
            failures.append(f"{case}:{name}: {detail}" if detail else name)

    by_k = _row_by_k(tradeoff)
    check(
        "k_grid",
        sorted(by_k) == list(v1.K_VALUES) and len(tradeoff) == len(v1.K_VALUES),
        "k values do not cover 0..20",
    )
    check(
        "case_total",
        abs(float(payload["harvest_case_m3"]) - total)
        <= TOTAL_TOLERANCE_M3,
        "case harvest total does not match",
    )
    check(
        "node_set",
        int(payload["counts"]["positive_nodes"]) == v1.EXPECTED_POSITIVE_NODE_COUNT
        and int(payload["counts"]["node_count"]) == v1.EXPECTED_NODE_COUNT,
        "node universe changed",
    )
    check(
        "fixed_universe",
        int(payload["counts"]["eligible_facilities"]) == expected_facilities
        and int(payload["counts"]["candidate_modules"]) == expected_candidates
        and abs(
            float(payload["existing_capacity_m3_y"])
            - expected_capacity_m3_y
        )
        <= 1e-6,
        "facility or candidate universe changed",
    )
    check(
        "conservation",
        all(
            abs(float(row["assigned_m3"]) - total) <= TOTAL_TOLERANCE_M3
            and abs(
                float(row["existing_served_m3"])
                + float(row["module_served_m3"])
                - float(row["assigned_m3"])
            )
            <= v1.CONSERVATION_TOLERANCE_M3
            for row in tradeoff
        ),
        "assigned volume is not conserved",
    )
    check(
        "capacity",
        all(
            float(row["existing_served_m3"])
            <= float(row["existing_capacity_m3_y"])
            + v1.CAPACITY_TOLERANCE_M3
            for row in tradeoff
        ),
        "existing capacity is exceeded",
    )
    check(
        "k0_existing_only",
        abs(float(by_k[0]["module_served_m3"])) <= 1e-9
        and abs(
            float(by_k[0]["existing_served_m3"])
            - float(by_k[0]["assigned_m3"])
        )
        <= v1.CONSERVATION_TOLERANCE_M3,
        "k=0 is not existing-only",
    )
    costs = [float(by_k[k]["transport_cost_cad"]) for k in v1.K_VALUES]
    means = [float(by_k[k]["mean_road_distance_km"]) for k in v1.K_VALUES]
    share_150 = [float(by_k[k]["share_ge_150_km"]) for k in v1.K_VALUES]
    share_300 = [float(by_k[k]["share_ge_300_km"]) for k in v1.K_VALUES]
    check(
        "k_monotonicity",
        all(
            costs[index + 1] <= costs[index] + 1e-6
            for index in range(len(costs) - 1)
        )
        and all(
            means[index + 1] <= means[index] + 1e-9
            for index in range(len(means) - 1)
        )
        and all(
            share_150[index + 1] <= share_150[index] + 1e-9
            for index in range(len(share_150) - 1)
        )
        and all(
            share_300[index + 1] <= share_300[index] + 1e-9
            for index in range(len(share_300) - 1)
        ),
        "cost, mean distance or threshold share increased with k",
    )
    selected_sets = {
        k: {
            module_id
            for module_id in str(by_k[k]["selected_module_ids"]).split(";")
            if module_id
        }
        for k in v1.K_VALUES
    }
    check(
        "module_count",
        all(
            int(by_k[k]["selected_module_count"]) == k
            and len(selected_sets[k]) == k
            and int(by_k[k]["modules_serving"]) <= k
            for k in v1.K_VALUES
        )
        and all(
            selected_sets[k - 1] <= selected_sets[k]
            for k in v1.K_VALUES[1:]
        ),
        "selected module count is inconsistent",
    )
    check(
        "module_rows",
        all(
            abs(
                math.fsum(
                    float(row["module_served_m3"])
                    for row in module_rows
                    if int(row["k"]) == k
                )
                - float(by_k[k]["module_served_m3"])
            )
            <= v1.CONSERVATION_TOLERANCE_M3
            for k in v1.K_VALUES
        )
        and len(
            [row for row in module_rows if int(row["k"]) == 20]
        )
        == 20,
        "module location rows are inconsistent",
    )
    check(
        "module_capacity_unbounded",
        all(
            row["capacity_status"] == v1.MODULE_CAPACITY_STATUS
            and row["capacity_m3_y"] in {"", None}
            and row["module_class"] == v1.GENERIC_MODULE_CLASS
            and str(row["class_agnostic"]) in {"True", "true"}
            for row in module_rows
        ),
        "module rows invent a class capacity",
    )
    check(
        "cost_arithmetic",
        all(
            abs(
                float(by_k[k]["transport_cost_cad"])
                - (
                    line.intercept_cad_m3 * float(by_k[k]["assigned_m3"])
                    + line.slope_cad_m3_km
                    * float(by_k[k]["road_distance_m3_km"])
                )
            )
            <= max(
                v1.COST_TOLERANCE_CAD,
                v1.COST_RELATIVE_TOLERANCE
                * float(by_k[k]["transport_cost_cad"]),
            )
            for k in v1.K_VALUES
        ),
        "transport cost arithmetic is inconsistent",
    )
    assignment_totals = {
        "k0": math.fsum(float(row["assigned_m3"]) for row in k0_rows),
        "k20": math.fsum(float(row["assigned_m3"]) for row in k20_rows),
    }
    check(
        "assignment_conservation",
        all(
            abs(value - total) <= TOTAL_TOLERANCE_M3
            for value in assignment_totals.values()
        ),
        "node assignment volume is not conserved",
    )
    labels = {
        str(row["evidence_label"])
        for row in [*tradeoff, *module_rows]
    }
    check(
        "labels",
        labels == {v1.EVIDENCE_LABEL}
        and all(
            row["sensitivity_label"] == v1.SENSITIVITY_LABEL
            and row["investment_advice_label"] == v1.INVESTMENT_ADVICE_LABEL
            and str(row["observed"]) in {"False", "false"}
            and str(row["policy_grade"]) in {"False", "false"}
            for row in [*tradeoff, *module_rows]
        ),
        "one or more output rows carry unexpected labels",
    )
    check(
        "shares_and_utilization_bounds",
        all(
            0.0 <= float(row["existing_served_share"]) <= 1.0
            and 0.0 <= float(row["module_served_share"]) <= 1.0
            and 0.0 <= float(row["share_ge_150_km"]) <= 1.0
            and 0.0 <= float(row["share_ge_300_km"]) <= 1.0
            and 0.0 <= float(row["existing_utilization"]) <= 1.0
            for row in tradeoff
        ),
        "share or utilization is outside [0, 1]",
    )
    return {
        "all_passed": not failures,
        "checks": checks,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Comparison, headline and report.
# ---------------------------------------------------------------------------

def _comparison_rows(payloads: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    base = _row_by_k(payloads[PRIMARY_CASE]["k_tradeoff"])
    for case in CASE_ORDER:
        by_k = _row_by_k(payloads[case]["k_tradeoff"])
        for k in SNAPSHOT_K:
            row = by_k[k]
            reference = base[k]
            cost = float(row["transport_cost_cad"])
            base_cost = float(reference["transport_cost_cad"])
            mean = float(row["mean_road_distance_km"])
            base_mean = float(reference["mean_road_distance_km"])
            rows.append(
                {
                    "case": case,
                    "k": k,
                    "harvest_case_m3": float(payloads[case]["harvest_case_m3"]),
                    "assigned_m3": float(row["assigned_m3"]),
                    "transport_cost_cad": cost,
                    "transport_cost_cad_m3": float(
                        row["transport_cost_cad_m3"]
                    ),
                    "mean_road_distance_km": mean,
                    "median_road_distance_km": float(
                        row["median_road_distance_km"]
                    ),
                    "p90_road_distance_km": float(
                        row["p90_road_distance_km"]
                    ),
                    "max_road_distance_km": float(
                        row["max_road_distance_km"]
                    ),
                    "share_ge_150_km": float(row["share_ge_150_km"]),
                    "share_ge_300_km": float(row["share_ge_300_km"]),
                    "module_served_m3": float(row["module_served_m3"]),
                    "module_served_share": float(
                        row["module_served_share"]
                    ),
                    "modules_serving": int(row["modules_serving"]),
                    "existing_served_share": float(
                        row["existing_served_share"]
                    ),
                    "existing_utilization": float(
                        row["existing_utilization"]
                    ),
                    "delta_transport_cost_vs_119_cad": cost - base_cost,
                    "delta_transport_cost_vs_119_pct": (
                        100.0 * (cost / base_cost - 1.0)
                        if base_cost > 0.0
                        else 0.0
                    ),
                    "delta_mean_road_distance_vs_119_km": mean - base_mean,
                    "delta_share_ge_150_vs_119": float(
                        row["share_ge_150_km"]
                    )
                    - float(reference["share_ge_150_km"]),
                    "delta_share_ge_300_vs_119": float(
                        row["share_ge_300_km"]
                    )
                    - float(reference["share_ge_300_km"]),
                    "delta_module_served_share_vs_119": float(
                        row["module_served_share"]
                    )
                    - float(reference["module_served_share"]),
                    "evidence_label": v1.EVIDENCE_LABEL,
                    "observed": v1.OBSERVED,
                    "policy_grade": v1.POLICY_GRADE,
                }
            )
    return rows


def _headline_rows(payloads: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    base = _row_by_k(payloads[PRIMARY_CASE]["k_tradeoff"])[20]
    rows: list[dict[str, Any]] = []
    for case in CASE_ORDER:
        by_k = _row_by_k(payloads[case]["k_tradeoff"])
        k0 = by_k[0]
        k20 = by_k[20]
        k0_cost = float(k0["transport_cost_cad"])
        k20_cost = float(k20["transport_cost_cad"])
        base_cost = float(base["transport_cost_cad"])
        rows.append(
            {
                "case": case,
                "harvest_case_m3": float(payloads[case]["harvest_case_m3"]),
                "k20_transport_cost_cad": k20_cost,
                "k20_transport_cost_cad_m3": float(
                    k20["transport_cost_cad_m3"]
                ),
                "k20_mean_road_distance_km": float(
                    k20["mean_road_distance_km"]
                ),
                "k20_median_road_distance_km": float(
                    k20["median_road_distance_km"]
                ),
                "k20_p90_road_distance_km": float(
                    k20["p90_road_distance_km"]
                ),
                "k20_share_ge_150_km": float(k20["share_ge_150_km"]),
                "k20_share_ge_300_km": float(k20["share_ge_300_km"]),
                "k20_module_served_share": float(
                    k20["module_served_share"]
                ),
                "k20_modules_serving": int(k20["modules_serving"]),
                "k20_existing_utilization": float(
                    k20["existing_utilization"]
                ),
                "k20_existing_served_share": float(
                    k20["existing_served_share"]
                ),
                "cost_reduction_vs_k0_cad": k0_cost - k20_cost,
                "cost_reduction_vs_k0_pct": (
                    100.0 * (k0_cost - k20_cost) / k0_cost
                    if k0_cost > 0.0
                    else 0.0
                ),
                "mean_reduction_vs_k0_km": float(
                    k0["mean_road_distance_km"]
                )
                - float(k20["mean_road_distance_km"]),
                "delta_transport_cost_vs_119_cad": k20_cost - base_cost,
                "delta_transport_cost_vs_119_pct": (
                    100.0 * (k20_cost / base_cost - 1.0)
                    if base_cost > 0.0
                    else 0.0
                ),
                "delta_mean_road_distance_vs_119_km": float(
                    k20["mean_road_distance_km"]
                )
                - float(base["mean_road_distance_km"]),
                "delta_share_ge_150_vs_119": float(k20["share_ge_150_km"])
                - float(base["share_ge_150_km"]),
                "delta_share_ge_300_vs_119": float(k20["share_ge_300_km"])
                - float(base["share_ge_300_km"]),
                "delta_module_served_share_vs_119": float(
                    k20["module_served_share"]
                )
                - float(base["module_served_share"]),
                "first_k_marginal_below_1pct": _first_diminishing_k(
                    payloads[case]["k_tradeoff"]
                ),
                "evidence_label": v1.EVIDENCE_LABEL,
                "observed": v1.OBSERVED,
                "policy_grade": v1.POLICY_GRADE,
            }
        )
    return rows


def _report(
    summary: Mapping[str, Any],
    payloads: Mapping[str, Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    headline: Sequence[Mapping[str, Any]],
) -> str:
    lines: list[str] = [
        "# Transport-Only Greenfield Siting Sensitivity V1: 100/119/150 Mm3",
        "",
        "Status: **transport_only_siting_screen** — `not_investment_advice`.",
        "Evidence label: `model_estimate`; `observed=false`; `policy_grade=false`.",
        "",
        "This is a transport-cost screening exercise. It is not investment advice,",
        "not a capital or permitting estimate, and not a landbase or facility-design",
        "claim. No national, policy, UI or official OSeCAN surface is changed.",
        "",
        "## 1. Setup",
        "",
        "- **Demand cases:** 100.000, 118.977 and 150.000 Mm3/yr. The 100 and",
        "  150 Mm3 node vectors use the documented provincial allocations and",
        "  the same within-province scaling as the committed v10 case extension.",
        "- **Existing facilities:** capacity v3 with the documented 1.2x provincial",
        f"  floor, held fixed; {summary['counts']['eligible_facilities']} eligible",
        f"  facilities, {_num(summary['method']['existing_capacity_m3_y'] / 1e6, 3)}",
        "  Mm3/yr of capacity.",
        "- **Greenfield candidates:** positive-volume supply nodes in provinces with",
        "  a published road factor. The generic module is class-agnostic and has",
        "  unlimited throughput in the screen; no class capacity, technology, cost,",
        "  product or residue is asserted.",
        f"- **Candidate count:** {summary['counts']['candidate_modules']} nodes.",
        "  NT positive nodes are not module hosts because no NT road factor is",
        "  published; they remain demand nodes.",
        "- **Objective:** minimise 2026 CAD truck transport cost from the committed",
        "  transport cost v1 base curve (52.5 m3 payload, empty return). No",
        "  local-preference penalty is applied in the k-series.",
        "- **Distances:** haversine x province road factor from",
        "  `facility_road_distance_audit_v2`; no full OSRM routing.",
        "- **Method:** greedy forward k-median/facility-location screening. At each",
        "  k the shortlist is the top 10 candidates by capacity-relaxed distance",
        "  savings, and every trial and every k is re-evaluated with the exact",
        "  capacitated transport LP (HiGHS). This is not a proven global optimum.",
        "- **119 Mm3 source:** the committed v1 siting artifact is loaded unchanged.",
        "",
        "## 2. k tradeoff by case",
        "",
        "| case | k | cost (B CAD) | CAD/m3 | mean km | median km | p90 km | >=150 km | >=300 km | module share | existing util |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in CASE_ORDER:
        for row in payloads[case]["k_tradeoff"]:
            lines.append(
                "| {case} | {k} | {cost} | {cost_m3} | {mean} | {median} | "
                "{p90} | {ge150} | {ge300} | {module_share} | {util} |".format(
                    case=case,
                    k=row["k"],
                    cost=_billions(float(row["transport_cost_cad"])),
                    cost_m3=_num(float(row["transport_cost_cad_m3"]), 2),
                    mean=_num(float(row["mean_road_distance_km"]), 1),
                    median=_num(float(row["median_road_distance_km"]), 1),
                    p90=_num(float(row["p90_road_distance_km"]), 1),
                    ge150=_pct(float(row["share_ge_150_km"]), 1),
                    ge300=_pct(float(row["share_ge_300_km"]), 1),
                    module_share=_pct(float(row["module_served_share"]), 1),
                    util=_pct(float(row["existing_utilization"]), 1),
                )
            )
    lines.extend(
        [
            "",
            "## 3. Comparison with 119 Mm3",
            "",
            "| case | k | cost (B CAD) | mean km | >=150 km | >=300 km | module share | delta cost vs 119 | delta mean vs 119 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparison:
        lines.append(
            "| {case} | {k} | {cost} | {mean} | {ge150} | {ge300} | "
            "{module_share} | {delta_cost} | {delta_mean} |".format(
                case=row["case"],
                k=row["k"],
                cost=_billions(float(row["transport_cost_cad"])),
                mean=_num(float(row["mean_road_distance_km"]), 1),
                ge150=_pct(float(row["share_ge_150_km"]), 1),
                ge300=_pct(float(row["share_ge_300_km"]), 1),
                module_share=_pct(float(row["module_served_share"]), 1),
                delta_cost=(
                    f"{float(row['delta_transport_cost_vs_119_cad']) / 1e9:+,.3f} B"
                ),
                delta_mean=(
                    f"{float(row['delta_mean_road_distance_vs_119_km']):+,.1f} km"
                ),
            )
        )
    lines.extend(
        [
            "",
            "## 4. k=20 headline",
            "",
            "| case | cost (B CAD) | CAD/m3 | mean km | median km | p90 km | >=150 km | >=300 km | module share | modules | existing util |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in headline:
        lines.append(
            "| {case} | {cost} | {cost_m3} | {mean} | {median} | {p90} | "
            "{ge150} | {ge300} | {module_share} | {modules} | {util} |".format(
                case=row["case"],
                cost=_billions(float(row["k20_transport_cost_cad"])),
                cost_m3=_num(float(row["k20_transport_cost_cad_m3"]), 2),
                mean=_num(float(row["k20_mean_road_distance_km"]), 1),
                median=_num(float(row["k20_median_road_distance_km"]), 1),
                p90=_num(float(row["k20_p90_road_distance_km"]), 1),
                ge150=_pct(float(row["k20_share_ge_150_km"]), 1),
                ge300=_pct(float(row["k20_share_ge_300_km"]), 1),
                module_share=_pct(
                    float(row["k20_module_served_share"]), 1
                ),
                modules=int(row["k20_modules_serving"]),
                util=_pct(float(row["k20_existing_utilization"]), 1),
            )
        )
    lines.extend(
        [
            "",
            "## 5. Module locations at k=20",
            "",
            "The selected sets are nested prefixes: the module selected at k is",
            "retained at every larger k.",
            "",
            "| case | module | province | first k | served (Mm3) | share of assigned | nodes served | mean km |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for case in CASE_ORDER:
        for row in payloads[case]["module_locations"]:
            if int(row["k"]) != 20:
                continue
            lines.append(
                "| {case} | {module} | {province} | {first_k} | {served} | "
                "{share} | {nodes} | {mean} |".format(
                    case=case,
                    module=str(row["module_id"]).replace("|", "\\|"),
                    province=row["province"],
                    first_k=row["first_selected_k"],
                    served=_num(float(row["module_served_m3"]) / 1e6, 3),
                    share=_pct(
                        float(row["module_served_share_of_assigned"]), 2
                    ),
                    nodes=row["nodes_served"],
                    mean=_num(float(row["mean_road_distance_km"]), 1),
                )
            )
    lines.extend(
        [
            "",
            "## 6. Diminishing returns and screening diagnostics",
            "",
            "| case | first k with marginal < 1% of k=0 cost | k=20 capacity-relaxed gap | k=20 existing utilization |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in headline:
        k20 = _row_by_k(payloads[row["case"]]["k_tradeoff"])[20]
        lines.append(
            "| {case} | {first} | {gap} | {util} |".format(
                case=row["case"],
                first=row["first_k_marginal_below_1pct"],
                gap=_pct(float(k20["capacity_relaxed_gap_pct"]) / 100.0, 2),
                util=_pct(float(k20["existing_utilization"]), 2),
            )
        )
    lines.extend(
        [
            "",
            "The capacity-relaxed gap is against a bound that ignores existing",
            "capacity; it is not an optimality gap. The selected sets are greedy",
            "prefixes, not a global optimum.",
            "",
            "## 7. Labels and caveats",
            "",
            "- `transport_only_siting_screen`; `not_investment_advice`;",
            "  `model_estimate`; `observed=false`; `policy_grade=false`.",
            "- No capital, operating, permitting, landbase, procurement or",
            "  facility-design claim is made.",
            "- The generic module is a class-agnostic transport sink with unlimited",
            "  throughput in the screen. No class-specific capacity is invented.",
            "- Distances are haversine x province-factor screening distances, not",
            "  observed routes for every node-module pair.",
            "- Transport cost excludes feedstock, processing, conversion, handling,",
            "  storage and product revenue.",
            "- The k-series has no local-preference penalty. Cross-border flows are",
            "  therefore unpenalised and are reported, not restricted.",
            "- Existing capacity v3 and the 1.2x provincial floor are unchanged.",
            "- The 100 and 150 Mm3 cases scale each province's node harvest vector",
            "  from the 119 Mm3 allocation; within-province node shares are",
            "  unchanged.",
            "- NT positive nodes are not candidate module hosts because no NT road",
            "  factor is published; their volume remains in the demand set.",
            "- No national claim, policy claim, UI change or official OSeCAN change",
            "  is made.",
            "",
            "## 8. Reproduce",
            "",
            "```bash",
            (
                ".venv/bin/python -m "
                "workflows.national.facilities.greenfield_siting_sensitivity_v1_cases"
            ),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------

def _write_outputs(
    output_dir: Path,
    payloads: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    v1._write_csv(
        output_dir / "k_tradeoff.csv",
        TRADEOFF_FIELDS,
        [row for case in CASE_ORDER for row in payloads[case]["k_tradeoff"]],
    )
    v1._write_csv(
        output_dir / "module_locations.csv",
        MODULE_FIELDS,
        [
            row
            for case in CASE_ORDER
            for row in payloads[case]["module_locations"]
        ],
    )
    v1._write_csv(
        output_dir / "node_assignment_k0.csv",
        NODE_ASSIGNMENT_FIELDS,
        [
            row
            for case in CASE_ORDER
            for row in payloads[case]["node_assignment_k0"]
        ],
    )
    v1._write_csv(
        output_dir / "node_assignment_k20.csv",
        NODE_ASSIGNMENT_FIELDS,
        [
            row
            for case in CASE_ORDER
            for row in payloads[case]["node_assignment_k20"]
        ],
    )
    v1._write_csv(
        output_dir / "existing_capacity_service_k0_k20.csv",
        EXISTING_SERVICE_FIELDS,
        [
            row
            for case in CASE_ORDER
            for row in payloads[case]["existing_capacity_service"]
        ],
    )
    v1._write_csv(
        output_dir / "case_comparison.csv",
        COMPARISON_FIELDS,
        summary["comparison"],
    )
    v1._write_csv(
        output_dir / "case_headline.csv",
        HEADLINE_FIELDS,
        summary["headline"],
    )


def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    workers: int = 2,
) -> dict[str, Any]:
    """Build the 100/119/150 Mm3 transport-only greenfield siting screen."""

    v1.v9.assert_no_unexpected_deletions()
    generated_utc = v1._utc_now()
    v1_summary = _load_v1_artifact()
    payloads: dict[str, dict[str, Any]] = {
        PRIMARY_CASE: _primary_payload(v1_summary)
    }
    cases_to_run = [case for case in CASE_ORDER if case != PRIMARY_CASE]
    if workers > 1:
        with ProcessPoolExecutor(max_workers=min(workers, len(cases_to_run))) as pool:
            for payload in pool.map(_run_case, cases_to_run):
                payloads[str(payload["case"])] = payload
    else:
        for case in cases_to_run:
            payloads[case] = _run_case(case)
    if set(payloads) != set(CASE_ORDER):
        raise GreenfieldSitingCasesError("case payload set is incomplete")

    line = v10._cost_line(v1.v9.load_transport_parameters())
    expected_facilities = int(
        v1_summary["counts"]["eligible_facilities"]
    )
    expected_candidates = int(v1_summary["counts"]["candidate_modules"])
    expected_capacity = float(v1_summary["method"]["existing_capacity_m3_y"])
    for case in CASE_ORDER:
        payload = payloads[case]
        validation = _validate_case(
            payload,
            line=line,
            expected_facilities=expected_facilities,
            expected_candidates=expected_candidates,
            expected_capacity_m3_y=expected_capacity,
        )
        payload["validation"] = validation
        if not validation["all_passed"]:
            raise GreenfieldSitingCasesError(
                f"{case}: validation failed: {validation['failures']}"
            )

    # The primary case must remain byte-equivalent at the metric level to v1.
    v1_tradeoff = v1_summary["k_tradeoff"]
    primary_tradeoff = payloads[PRIMARY_CASE]["k_tradeoff"]
    if len(v1_tradeoff) != len(primary_tradeoff):
        raise GreenfieldSitingCasesError("primary tradeoff row count changed")
    for expected, actual in zip(v1_tradeoff, primary_tradeoff, strict=True):
        for key, value in expected.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not math.isclose(
                    float(actual[key]),
                    float(value),
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            ):
                raise GreenfieldSitingCasesError(
                    f"primary tradeoff {key} differs from v1"
                )
    v1_modules = v1_summary["module_locations"]
    primary_modules = payloads[PRIMARY_CASE]["module_locations"]
    if len(v1_modules) != len(primary_modules):
        raise GreenfieldSitingCasesError("primary module row count changed")
    for expected, actual in zip(v1_modules, primary_modules, strict=True):
        for key, value in expected.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and not math.isclose(
                    float(actual[key]),
                    float(value),
                    rel_tol=1e-12,
                    abs_tol=1e-9,
                )
            ):
                raise GreenfieldSitingCasesError(
                    f"primary module {key} differs from v1"
                )

    comparison = _comparison_rows(payloads)
    headline = _headline_rows(payloads)
    labels = {
        "sensitivity_label": v1.SENSITIVITY_LABEL,
        "investment_advice_label": v1.INVESTMENT_ADVICE_LABEL,
        "evidence_label": v1.EVIDENCE_LABEL,
        "observed": v1.OBSERVED,
        "policy_grade": v1.POLICY_GRADE,
        "national_claim": False,
        "official_osecan_changed": False,
        "ui_changed": False,
        "capital_cost_claim": False,
        "permitting_claim": False,
        "landbase_claim": False,
        "class_specific_capacity_claim": False,
        "investment_advice": False,
        "policy_claim": False,
        "greenfield_siting": True,
        "siting_optimization": "screening_heuristic",
        "module_class": v1.GENERIC_MODULE_CLASS,
        "module_capacity": v1.MODULE_CAPACITY_STATUS,
        "case_extension": True,
    }
    method = {
        "engine": (
            "greedy k-median/facility-location screening with exact "
            "capacitated transport LP re-evaluation (HiGHS)"
        ),
        "k_values": list(v1.K_VALUES),
        "cases": {
            case: {
                "harvest_case_m3": CASE_TOTALS[case],
                "node_basis": (
                    "committed greenfield siting v1 node vector"
                    if case == PRIMARY_CASE
                    else "provincial allocation scaled from the 119 Mm3 "
                    "node vector; within-province shares unchanged"
                ),
                "source": payloads[case]["source"],
                "lp_solves": payloads[case]["counts"]["lp_solves"],
            }
            for case in CASE_ORDER
        },
        "candidate_basis": (
            "positive-volume supply nodes in provinces with a published "
            "road factor"
        ),
        "candidate_modules": expected_candidates,
        "excluded_candidate_provinces": payloads[PRIMARY_CASE][
            "excluded_candidates"
        ],
        "existing_facilities": expected_facilities,
        "existing_capacity_m3_y": expected_capacity,
        "capacity_basis": (
            "national_facility_capacity_v3 with the documented 1.2x "
            "provincial floor, held fixed"
        ),
        "module_basis": (
            "class-agnostic generic module at a candidate supply node; "
            "unlimited throughput in the screen; no class capacity, "
            "technology, cost, product or residue asserted"
        ),
        "objective": (
            "minimise 2026 CAD truck transport cost from transport_cost_v1 "
            "base curve (52.5 m3 payload, empty return); no local-preference "
            "penalty in the k-series"
        ),
        "distance_method": (
            "haversine x province road factor from "
            "facility_road_distance_audit_v2; candidate module uses its node "
            "province factor; no full OSRM routing"
        ),
        "full_osrm": False,
        "local_preference_penalty_km": 0.0,
        "transport_payload_m3": v10.PAYLOAD_M3,
        "transport_energy_gj_per_m3": v10.ENERGY_GJ_PER_M3,
        "heuristic": (
            "greedy forward selection; at each k the shortlist is the top "
            f"{v1.SHORTLIST_SIZE} candidates by capacity-relaxed distance "
            "savings; every trial and every k is re-evaluated with the exact "
            "capacitated transport LP"
        ),
        "shortlist_size": v1.SHORTLIST_SIZE,
        "optimality": (
            "heuristic screening result; not a proven global optimum; no "
            "optimality-gap claim"
        ),
        "lp_solver": "scipy.optimize.linprog(method='highs', presolve=False)",
        "v10_existing_only_baseline": (
            "committed v10 primary assignment for 119 Mm3 and committed v10 "
            "case summary for 100/150 Mm3, including their 500 km "
            "cross-border preference penalties"
        ),
        "k0_existing_only_baseline": (
            "existing-only re-solve with the same transport curve and "
            "capacity but zero local-preference penalty"
        ),
        "primary_case": PRIMARY_CASE,
        "primary_source": str(V1_SUMMARY_JSON.relative_to(REPO_ROOT)),
    }
    counts = {
        "cases": len(CASE_ORDER),
        "input_nodes": v1.EXPECTED_NODE_COUNT,
        "positive_nodes": v1.EXPECTED_POSITIVE_NODE_COUNT,
        "candidate_modules": expected_candidates,
        "eligible_facilities": expected_facilities,
        "k_values": len(v1.K_VALUES),
        "tradeoff_rows": sum(
            len(payloads[case]["k_tradeoff"]) for case in CASE_ORDER
        ),
        "module_location_rows": sum(
            len(payloads[case]["module_locations"]) for case in CASE_ORDER
        ),
        "node_assignment_rows_k0": sum(
            len(payloads[case]["node_assignment_k0"]) for case in CASE_ORDER
        ),
        "node_assignment_rows_k20": sum(
            len(payloads[case]["node_assignment_k20"]) for case in CASE_ORDER
        ),
        "existing_service_rows": sum(
            len(payloads[case]["existing_capacity_service"])
            for case in CASE_ORDER
        ),
        "lp_solves": sum(
            payloads[case]["counts"]["lp_solves"] for case in CASE_ORDER
        ),
    }
    caveats = [
        (
            "Transport-only screening: no capital, operating, permitting, "
            "landbase, procurement or facility-design claim."
        ),
        (
            "The generic module is class-agnostic with unlimited throughput "
            "in the screen; no class-specific capacity, technology, cost, "
            "product or residue is asserted."
        ),
        (
            "The k-series is a greedy heuristic, not a globally optimal "
            "siting solution; no optimality-gap claim is made."
        ),
        (
            "Distances are haversine x province-factor screening distances, "
            "not observed routes for every node-module pair; no full OSRM "
            "routing."
        ),
        (
            "Transport cost excludes feedstock, processing, conversion, "
            "handling, storage and product revenue."
        ),
        (
            "The k-series has no local-preference penalty, so cross-border "
            "flows are unpenalised and are reported, not restricted."
        ),
        (
            "Existing capacity v3 and the 1.2x provincial floor are "
            "unchanged; the existing facility set is fixed."
        ),
        (
            "The 100 and 150 Mm3 cases scale each province's node harvest "
            "vector from the 119 Mm3 allocation; within-province node shares "
            "are unchanged."
        ),
        (
            "NT positive nodes are not candidate module hosts because no NT "
            "road factor is published; their volume remains in the demand "
            "set."
        ),
        (
            "No national claim, policy claim, UI change or official OSeCAN "
            "change is made."
        ),
        "This artifact is not investment advice.",
    ]
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": labels,
        "method": method,
        "counts": counts,
        "cases": {
            case: {
                "harvest_case_m3": payloads[case]["harvest_case_m3"],
                "source": payloads[case]["source"],
                "engine": payloads[case]["engine"],
                "counts": payloads[case]["counts"],
                "baseline_v10": payloads[case]["baseline_v10"],
                "k_tradeoff": payloads[case]["k_tradeoff"],
                "module_locations": payloads[case]["module_locations"],
                "validation": payloads[case]["validation"],
            }
            for case in CASE_ORDER
        },
        "comparison": comparison,
        "headline": headline,
        "validation": {
            "all_passed": all(
                payloads[case]["validation"]["all_passed"]
                for case in CASE_ORDER
            ),
            "primary_parity_with_v1": True,
            "cases": {
                case: payloads[case]["validation"]
                for case in CASE_ORDER
            },
        },
        "caveats": caveats,
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.greenfield_siting_sensitivity_v1_cases"
        ),
    }
    summary["report"] = _report(summary, payloads, comparison, headline)

    _write_outputs(output_dir, payloads, summary)
    (output_dir / "GREENFIELD_SITING_SENSITIVITY_V1_CASES.md").write_text(
        summary["report"] + "\n", encoding="utf-8"
    )
    v1._write_json(output_dir / "summary.json", summary)

    input_paths = (
        V1_SUMMARY_JSON,
        V1_DIR / "k_tradeoff.csv",
        V1_DIR / "module_locations.csv",
        V1_DIR / "node_assignment_k0.csv",
        V1_DIR / "node_assignment_k20.csv",
        V1_DIR / "existing_capacity_service_k0_k20.csv",
        V10_DIR / "case_summary_100m.json",
        V10_DIR / "case_summary_150m.json",
        v1.SUPPLY_NODES_CSV,
        v1.CAPACITY_CSV,
        v1.CAPACITY_SUMMARY,
        v1.HARVEST_CSV,
        v1.FACTORS_CSV,
        v1.TRANSPORT_PARAMETERS_CSV,
        v1.TRANSPORT_CURVES_CSV,
        v1.DATA_ROOT
        / "derived/national/harvest_basis/provincial_allocation/100m.csv",
        v1.DATA_ROOT
        / "derived/national/harvest_basis/provincial_allocation/150m.csv",
    )
    inputs = {
        str(path.relative_to(REPO_ROOT)): v1.v9._sha256(path)
        for path in input_paths
    }
    outputs: dict[str, Any] = {}
    for path in sorted(output_dir.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": v1.v9._sha256(path),
            }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": labels,
        "inputs": inputs,
        "outputs": outputs,
        "reproduce": summary["reproduce"],
    }
    v1._write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "cases": {
                    row["case"]: {
                        "k20_transport_cost_cad": row[
                            "k20_transport_cost_cad"
                        ],
                        "k20_mean_road_distance_km": row[
                            "k20_mean_road_distance_km"
                        ],
                        "k20_module_served_share": row[
                            "k20_module_served_share"
                        ],
                        "delta_transport_cost_vs_119_cad": row[
                            "delta_transport_cost_vs_119_cad"
                        ],
                    }
                    for row in summary["headline"]
                },
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
