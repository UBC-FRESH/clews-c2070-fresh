"""Greenfield break-even annualized capital thresholds v1: 100/119/150 Mm3.

This extends ``greenfield_breakeven_capital_threshold_v1`` to the 100 and
150 Mm3 transport-only siting cases.  It introduces no new siting solve,
capital cost, discount rate, asset life, technology or revenue assumption.
For each case and module count ``k`` it reports the transport saving versus
the case ``k=0`` baseline, the module-served annual volume and the
cumulative and marginal break-even annualized capital threshold in 2026 CAD
per (m3/yr):

    cumulative threshold = (cost_k0 - cost_k) / module_served_m3_k

    marginal threshold = (cost_{k-1} - cost_k)
                         / (module_served_m3_k - module_served_m3_{k-1})

The 119 Mm3 case is checked for exact parity against the committed
``greenfield_breakeven_capital_threshold_v1`` table.  Labels are
``model_estimate`` / ``transport_only`` / ``not_investment_advice``.  No
national, policy, UI or official OSeCAN claim or change is made.  A ``D`` in
either status column of the parent or data repository halts the build.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    greenfield_breakeven_capital_threshold_v1 as v1,
)
from workflows.national.facilities import (
    national_facility_assignment_v9 as v9,
)

REPO_ROOT = v1.REPO_ROOT
DATA_ROOT = v1.DATA_ROOT
SOURCE_DIR = (
    DATA_ROOT
    / "derived/national/facilities/greenfield_siting_sensitivity_v1_cases"
)
SOURCE_TRADEOFF_CSV = SOURCE_DIR / "k_tradeoff.csv"
SOURCE_MANIFEST_JSON = SOURCE_DIR / "manifest.json"
SOURCE_SUMMARY_JSON = SOURCE_DIR / "summary.json"
PARITY_DIR = v1.OUTPUT_DIR
PARITY_CSV = PARITY_DIR / "breakeven_thresholds.csv"
PARITY_SUMMARY_JSON = PARITY_DIR / "summary.json"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities"
    / "greenfield_breakeven_capital_threshold_v1_cases"
)

ARTIFACT = "greenfield_breakeven_capital_threshold_v1_cases"
ARTIFACT_VERSION = "1.0.0"
CASES = ("100m", "119m", "150m")
PRIMARY_CASE = "119m"
K_VALUES = v1.K_VALUES
THRESHOLD_FIELDS = ["case", *v1.THRESHOLD_FIELDS]
EVIDENCE_LABEL = "model_estimate"
SENSITIVITY_LABEL = "transport_only"
INVESTMENT_ADVICE_LABEL = "not_investment_advice"

LABELS = {
    "evidence_label": EVIDENCE_LABEL,
    "sensitivity_label": SENSITIVITY_LABEL,
    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
    "investment_advice": False,
    "capital_cost_claim": False,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "policy_claim": False,
    "official_osecan_changed": False,
    "ui_changed": False,
    "transport_only": True,
    "case_extension": True,
}

PARITY_TOLERANCE = 1e-6


class BreakevenCasesError(v1.BreakevenError):
    """Raised when a multi-case break-even invariant fails."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BreakevenCasesError(f"source CSV is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise BreakevenCasesError(f"source CSV is empty: {path}")
    return rows


def _case_groups(
    raw_rows: Sequence[Mapping[str, str]],
) -> dict[str, list[Mapping[str, str]]]:
    groups: dict[str, list[Mapping[str, str]]] = {case: [] for case in CASES}
    for raw in raw_rows:
        case = raw.get("case", "")
        if case not in groups:
            raise BreakevenCasesError(f"unexpected case label: {case!r}")
        groups[case].append(raw)
    missing = [case for case in CASES if not groups[case]]
    if missing:
        raise BreakevenCasesError(f"missing case rows: {missing}")
    return groups


def _parse_case(
    case: str, raw_rows: Sequence[Mapping[str, str]]
) -> tuple[tuple[v1.TradeoffRow, ...], float]:
    required = {
        "k",
        "selected_module_count",
        "selected_module_ids",
        "transport_cost_cad",
        "module_served_m3",
        "assigned_m3",
    }
    missing = required.difference(raw_rows[0])
    if missing:
        raise BreakevenCasesError(
            f"{case}: source CSV is missing fields: {sorted(missing)}"
        )
    rows: list[v1.TradeoffRow] = []
    assigned_values: set[float] = set()
    for index, raw in enumerate(raw_rows):
        k = int(v1._finite_float(raw["k"], f"{case} row {index} k"))
        if k != index:
            raise BreakevenCasesError(
                f"{case}: k grid is not 0..20 at row {index}: {k}"
            )
        count = int(
            v1._finite_float(
                raw["selected_module_count"],
                f"{case} k={k} selected_module_count",
            )
        )
        if count != k:
            raise BreakevenCasesError(
                f"{case} k={k}: selected_module_count {count} != {k}"
            )
        module_ids = (
            tuple(part for part in raw["selected_module_ids"].split(";") if part)
            if raw["selected_module_ids"]
            else ()
        )
        if len(module_ids) != k:
            raise BreakevenCasesError(
                f"{case} k={k}: {len(module_ids)} module ids, expected {k}"
            )
        cost = v1._finite_float(
            raw["transport_cost_cad"], f"{case} k={k} cost"
        )
        volume = v1._finite_float(
            raw["module_served_m3"], f"{case} k={k} volume"
        )
        assigned = v1._finite_float(
            raw["assigned_m3"], f"{case} k={k} assigned"
        )
        if cost <= 0.0:
            raise BreakevenCasesError(
                f"{case} k={k}: transport cost is not positive: {cost}"
            )
        if volume < 0.0:
            raise BreakevenCasesError(
                f"{case} k={k}: module volume is negative: {volume}"
            )
        if assigned <= 0.0:
            raise BreakevenCasesError(
                f"{case} k={k}: assigned volume is not positive: {assigned}"
            )
        assigned_values.add(assigned)
        rows.append(v1.TradeoffRow(k, cost, volume, module_ids))

    if tuple(row.k for row in rows) != K_VALUES:
        raise BreakevenCasesError(f"{case}: k grid is not exactly 0..20")
    if len(assigned_values) != 1:
        raise BreakevenCasesError(
            f"{case}: assigned volume varies across k: {sorted(assigned_values)}"
        )
    if rows[0].module_served_m3_y > v1.ZERO_VOLUME_M3:
        raise BreakevenCasesError(f"{case}: k=0 module volume is not zero")
    for previous, current in itertools.pairwise(rows):
        if current.transport_cost_cad_y > previous.transport_cost_cad_y:
            raise BreakevenCasesError(
                f"{case}: transport cost increases from k={previous.k} "
                f"to k={current.k}"
            )
        if (
            current.module_served_m3_y
            < previous.module_served_m3_y - v1.VOLUME_TOLERANCE_M3
        ):
            raise BreakevenCasesError(
                f"{case}: module volume decreases from k={previous.k} "
                f"to k={current.k}"
            )
        if current.k > 0 and current.module_served_m3_y <= 0.0:
            raise BreakevenCasesError(
                f"{case}: k={current.k} has no module-served volume"
            )
    return tuple(rows), assigned_values.pop()


def _baseline_row(case: str, row: v1.TradeoffRow) -> dict[str, Any]:
    return {
        "case": case,
        "k": 0,
        "selected_module_count": 0,
        "added_module_id": "",
        "transport_cost_cad_y": row.transport_cost_cad_y,
        "transport_saving_vs_k0_cad_y": 0.0,
        "transport_saving_vs_k0_b_cad_y": 0.0,
        "module_served_m3_y": 0.0,
        "module_served_mm3_y": 0.0,
        "breakeven_annualized_capital_cad_per_m3_y": None,
        "marginal_transport_saving_cad_y": 0.0,
        "marginal_transport_saving_b_cad_y": 0.0,
        "marginal_module_volume_m3_y": 0.0,
        "marginal_module_volume_mm3_y": 0.0,
        "marginal_breakeven_capital_cad_per_m3_y": None,
        "marginal_threshold_status": "baseline",
        **LABELS,
    }


def _case_headline(
    case: str,
    thresholds: Sequence[v1.ThresholdRow],
    assigned_m3_y: float,
    baseline_cost_cad_y: float,
) -> dict[str, Any]:
    k20 = thresholds[-1]
    minimum = min(
        thresholds,
        key=lambda row: (
            row.breakeven_cad_per_m3_y
            if row.breakeven_cad_per_m3_y is not None
            else math.inf
        ),
    )
    reliable = sorted(
        row.marginal_breakeven_cad_per_m3_y
        for row in thresholds
        if row.marginal_threshold_status == "defined"
        and row.marginal_breakeven_cad_per_m3_y is not None
    )
    return {
        "case": case,
        "assigned_m3_y": assigned_m3_y,
        "assigned_mm3_y": assigned_m3_y / 1e6,
        "baseline_transport_cost_cad_y": baseline_cost_cad_y,
        "k1_cumulative_threshold_cad_per_m3_y": (
            thresholds[0].breakeven_cad_per_m3_y
        ),
        "minimum_cumulative_threshold": {
            "k": minimum.k,
            "cad_per_m3_y": minimum.breakeven_cad_per_m3_y,
        },
        "k20_cumulative_threshold_cad_per_m3_y": (
            k20.breakeven_cad_per_m3_y
        ),
        "k20_transport_saving_b_cad_y": k20.transport_saving_cad_y / 1e9,
        "k20_module_served_mm3_y": k20.module_served_m3_y / 1e6,
        "median_defined_marginal_threshold_cad_per_m3_y": (
            reliable[len(reliable) // 2] if reliable else None
        ),
        "defined_marginal_threshold_count": len(reliable),
        "zero_marginal_module_volume_k": [
            row.k
            for row in thresholds
            if row.marginal_threshold_status
            == "zero_marginal_module_volume"
        ],
        "thin_marginal_module_volume_k": [
            row.k
            for row in thresholds
            if row.marginal_threshold_status
            == "thin_marginal_module_volume"
        ],
    }


def _load_parity_rows() -> dict[int, Mapping[str, str]]:
    if not PARITY_CSV.is_file():
        raise BreakevenCasesError(f"parity CSV is missing: {PARITY_CSV}")
    return {int(row["k"]): row for row in _read_csv(PARITY_CSV)}


def _check_parity(
    case_rows: Sequence[Mapping[str, Any]],
    parity_rows: Mapping[int, Mapping[str, str]],
) -> list[str]:
    failures: list[str] = []
    for row in case_rows:
        k = int(row["k"])
        expected = parity_rows[k]
        for field in v1.THRESHOLD_FIELDS:
            actual = row[field]
            reference = expected[field]
            if actual is None:
                if reference != "":
                    failures.append(f"119m k={k} {field}: None != {reference!r}")
                continue
            if isinstance(actual, bool):
                expected_bool = reference == "true"
                if actual is not expected_bool:
                    failures.append(
                        f"119m k={k} {field}: {actual} != {expected_bool}"
                    )
                continue
            if isinstance(actual, float):
                try:
                    parsed = float(reference)
                except ValueError:
                    failures.append(
                        f"119m k={k} {field}: non-numeric {reference!r}"
                    )
                    continue
                if not math.isclose(
                    actual, parsed, abs_tol=PARITY_TOLERANCE
                ):
                    failures.append(
                        f"119m k={k} {field}: {actual} != {parsed}"
                    )
                continue
            if str(actual) != reference:
                failures.append(
                    f"119m k={k} {field}: {actual!r} != {reference!r}"
                )
    return failures


def _build_report(
    case_rows: Sequence[Mapping[str, Any]],
    case_headlines: Sequence[Mapping[str, Any]],
    source_sha256: str,
) -> str:
    lines = [
        (
            "# Greenfield break-even annualized capital thresholds v1: "
            "100/119/150 Mm3"
        ),
        "",
        "Status: **threshold diagnostic** — `not_investment_advice`.",
        (
            "Evidence label: `model_estimate`; sensitivity label: "
            "`transport_only`; `observed=false`; `policy_grade=false`."
        ),
        "",
        "This artifact states a break-even **threshold**, not a capital cost",
        "estimate, for each transport-only siting case. It does not estimate",
        "module capex, operating cost, technology, class capacity, revenue,",
        "permitting, landbase or facility design. No national or policy",
        "claim is made.",
        "",
        "## 1. Definition",
        "",
        "For case `c` and module count `k`, with transport cost `TC` and",
        "module-served annual volume `V`:",
        "",
        "```text",
        "cumulative threshold_k = (TC_0 - TC_k) / V_k",
        "marginal threshold_k = (TC_{k-1} - TC_k) / (V_k - V_{k-1})",
        "```",
        "",
        "Units are 2026 CAD per (m3/yr) of module-served volume. The",
        "threshold is the maximum annualized capital charge that the",
        "transport saving versus the case `k=0` baseline could support at",
        "break-even, before any other cost or revenue. No discount rate,",
        "lifetime or capex assumption is used.",
        "",
        f"Source: `{SOURCE_TRADEOFF_CSV.relative_to(REPO_ROOT)}`  ",
        f"SHA-256: `{source_sha256}`  ",
        (
            "The 119 Mm3 rows are checked for parity against "
            "`greenfield_breakeven_capital_threshold_v1`."
        ),
        "",
        "## 2. Case headline",
        "",
        (
            "| case | assigned (Mm3/yr) | k=1 cumulative | minimum cumulative "
            "| k=20 cumulative | k=20 saving (B CAD/yr) | k=20 volume "
            "(Mm3/yr) | median defined marginal |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for headline in case_headlines:
        lines.append(
            f"| {headline['case']} "
            f"| {headline['assigned_mm3_y']:,.3f} "
            f"| {headline['k1_cumulative_threshold_cad_per_m3_y']:,.2f} "
            f"| {headline['minimum_cumulative_threshold']['cad_per_m3_y']:,.2f}"
            f" (k={headline['minimum_cumulative_threshold']['k']}) "
            f"| {headline['k20_cumulative_threshold_cad_per_m3_y']:,.2f} "
            f"| {headline['k20_transport_saving_b_cad_y']:,.3f} "
            f"| {headline['k20_module_served_mm3_y']:,.3f} "
            f"| {headline['median_defined_marginal_threshold_cad_per_m3_y']:,.2f} |"
        )
    lines.extend(
        [
            "",
            "All threshold values are CAD per (m3/yr).",
            "",
            "## 3. Combined threshold table (k = 0..20)",
            "",
            (
                "| case | k | added module | cost (B CAD/yr) | cumulative "
                "saving (B CAD/yr) | module volume (Mm3/yr) | cumulative "
                "threshold (CAD per m3/yr) | marginal saving (B CAD/yr) "
                "| marginal volume (Mm3/yr) | marginal threshold "
                "(CAD per m3/yr) |"
            ),
            "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in case_rows:
        module = str(row["added_module_id"]).replace("|", "\\|")
        cumulative = (
            ""
            if row["breakeven_annualized_capital_cad_per_m3_y"] is None
            else f"{row['breakeven_annualized_capital_cad_per_m3_y']:,.2f}"
        )
        marginal = ""
        if row["marginal_breakeven_capital_cad_per_m3_y"] is not None:
            marginal = f"{row['marginal_breakeven_capital_cad_per_m3_y']:,.2f}"
            if row["marginal_threshold_status"] == "thin_marginal_module_volume":
                marginal += " †"
            elif row["marginal_threshold_status"] == "zero_marginal_module_volume":
                marginal = "—"
        elif row["marginal_threshold_status"] == "zero_marginal_module_volume":
            marginal = "—"
        lines.append(
            f"| {row['case']} | {row['k']} | `{module}` "
            f"| {row['transport_cost_cad_y'] / 1e9:,.3f} "
            f"| {row['transport_saving_vs_k0_cad_y'] / 1e9:,.3f} "
            f"| {row['module_served_m3_y'] / 1e6:,.3f} "
            f"| {cumulative} "
            f"| {row['marginal_transport_saving_cad_y'] / 1e9:,.3f} "
            f"| {row['marginal_module_volume_m3_y'] / 1e6:,.3f} "
            f"| {marginal} |"
        )
    lines.extend(
        [
            "",
            "`†` marks a thin marginal volume (below 0.01% of that case's",
            "annual demand); the ratio is shown but is not a stable per-unit",
            "threshold. `—` marks a zero marginal module volume, so the",
            "marginal threshold is undefined. An empty cumulative cell marks",
            "the `k=0` baseline.",
            "",
            "## 4. Key thresholds and caveats",
            "",
        ]
    )
    for headline in case_headlines:
        case = headline["case"]
        zero = ", ".join(
            map(str, headline["zero_marginal_module_volume_k"])
        ) or "none"
        thin = ", ".join(
            map(str, headline["thin_marginal_module_volume_k"])
        ) or "none"
        lines.append(
            f"- {case}: k=1 **"
            f"{headline['k1_cumulative_threshold_cad_per_m3_y']:,.2f}**; "
            f"minimum **"
            f"{headline['minimum_cumulative_threshold']['cad_per_m3_y']:,.2f}** "
            f"at k={headline['minimum_cumulative_threshold']['k']}; "
            f"k=20 **"
            f"{headline['k20_cumulative_threshold_cad_per_m3_y']:,.2f}** "
            "CAD per m3/yr; zero marginal volume at "
            f"k={zero}; thin marginal volume at k={thin}."
        )
    lines.extend(
        [
            "",
            "The cumulative threshold is not monotonic across k. Each k-series",
            "is a greedy screening prefix, not a global optimum. The table is",
            "not a module ranking or a recommendation.",
            "",
            "- `model_estimate`; `transport_only`; `not_investment_advice`;",
            "  `observed=false`; `policy_grade=false`.",
            "- This is a break-even threshold, not a capital cost estimate.",
            "- No capex, discount rate, asset life, fixed operating cost,",
            "  feedstock cost, processing cost, product revenue or financing",
            "  assumption is introduced.",
            "- Transport savings include re-routing and existing-facility",
            "  effects; they are not a module-only revenue stream.",
            "- Distances and costs inherit the screening limitations of",
            "  `greenfield_siting_sensitivity_v1_cases`.",
            "- No national claim, policy claim, UI change or official OSeCAN",
            "  change is made.",
            "",
            "## 5. Reproduce",
            "",
            "```bash",
            (
                ".venv/bin/python -m workflows.national.facilities."
                "greenfield_breakeven_capital_threshold_v1_cases"
            ),
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_hash_matches_manifest(source_sha256: str) -> bool:
    if not SOURCE_MANIFEST_JSON.is_file():
        return False
    manifest = json.loads(SOURCE_MANIFEST_JSON.read_text(encoding="utf-8"))
    entry = manifest.get("outputs", {}).get(SOURCE_TRADEOFF_CSV.name, {})
    return entry.get("sha256") == source_sha256


def _case_totals_from_summary() -> dict[str, float]:
    if not SOURCE_SUMMARY_JSON.is_file():
        raise BreakevenCasesError(
            f"case summary is missing: {SOURCE_SUMMARY_JSON}"
        )
    payload = json.loads(SOURCE_SUMMARY_JSON.read_text(encoding="utf-8"))
    if payload.get("artifact") != "greenfield_siting_sensitivity_v1_cases":
        raise BreakevenCasesError("unexpected case summary artifact")
    if not payload.get("validation", {}).get("all_passed"):
        raise BreakevenCasesError("case summary did not pass validation")
    return {
        case: float(payload["cases"][case]["harvest_case_m3"])
        for case in CASES
    }


def build() -> dict[str, Any]:
    v9.assert_no_unexpected_deletions()
    generated_utc = _utc_now()
    source_sha256 = v9._sha256(SOURCE_TRADEOFF_CSV)
    raw_rows = _read_csv(SOURCE_TRADEOFF_CSV)
    groups = _case_groups(raw_rows)
    case_totals = _case_totals_from_summary()
    parity_rows = _load_parity_rows()

    combined_rows: list[dict[str, Any]] = []
    case_headlines: list[dict[str, Any]] = []
    all_thresholds: dict[str, tuple[v1.ThresholdRow, ...]] = {}
    assigned_by_case: dict[str, float] = {}
    for case in CASES:
        rows, assigned = _parse_case(case, groups[case])
        if abs(assigned - case_totals[case]) > 1.0:
            raise BreakevenCasesError(
                f"{case}: assigned {assigned!r} != harvest total "
                f"{case_totals[case]!r}"
            )
        thresholds = v1._compute_thresholds(rows, assigned)
        all_thresholds[case] = thresholds
        assigned_by_case[case] = assigned
        combined_rows.append(_baseline_row(case, rows[0]))
        combined_rows.extend(
            {"case": case, **v1._threshold_record(row)}
            for row in thresholds
        )
        case_headlines.append(
            _case_headline(
                case,
                thresholds,
                assigned,
                rows[0].transport_cost_cad_y,
            )
        )

    parity_failures = _check_parity(
        [row for row in combined_rows if row["case"] == PRIMARY_CASE],
        parity_rows,
    )

    checks = {
        "source_manifest_hash_matches": _source_hash_matches_manifest(
            source_sha256
        ),
        "case_count": len(all_thresholds) == len(CASES),
        "k_grid_per_case": all(
            tuple(row.k for row in all_thresholds[case]) == K_VALUES[1:]
            for case in CASES
        ),
        "k0_zero_volume_per_case": all(
            row["module_served_m3_y"] == 0.0
            for row in combined_rows
            if row["k"] == 0
        ),
        "cumulative_saving_positive": all(
            row["transport_saving_vs_k0_cad_y"] > 0.0
            for row in combined_rows
            if row["k"] > 0
        ),
        "cumulative_threshold_arithmetic": all(
            math.isclose(
                row["breakeven_annualized_capital_cad_per_m3_y"],
                row["transport_saving_vs_k0_cad_y"]
                / row["module_served_m3_y"],
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for row in combined_rows
            if row["k"] > 0
        ),
        "marginal_threshold_arithmetic": all(
            (
                row["marginal_breakeven_capital_cad_per_m3_y"] is None
                and row["marginal_threshold_status"]
                in {"baseline", "zero_marginal_module_volume"}
            )
            or math.isclose(
                row["marginal_breakeven_capital_cad_per_m3_y"],
                row["marginal_transport_saving_cad_y"]
                / row["marginal_module_volume_m3_y"],
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for row in combined_rows
        ),
        "marginal_saving_nonnegative": all(
            row["marginal_transport_saving_cad_y"] >= -v1.COST_TOLERANCE_CAD
            for row in combined_rows
        ),
        "labels_exact": all(
            row["evidence_label"] == EVIDENCE_LABEL
            and row["sensitivity_label"] == SENSITIVITY_LABEL
            and row["investment_advice_label"] == INVESTMENT_ADVICE_LABEL
            and row["capital_cost_claim"] is False
            and row["observed"] is False
            and row["policy_grade"] is False
            for row in combined_rows
        ),
        "primary_case_parity_with_v1": not parity_failures,
        "no_nan_or_inf": all(
            math.isfinite(value)
            for row in combined_rows
            for value in row.values()
            if isinstance(value, float)
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    if parity_failures:
        failures.append(f"parity_detail:{parity_failures[:5]}")
    if failures:
        raise BreakevenCasesError(f"validation failed: {failures}")

    report = _build_report(combined_rows, case_headlines, source_sha256)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    v1._write_csv(
        OUTPUT_DIR / "breakeven_thresholds.csv",
        THRESHOLD_FIELDS,
        combined_rows,
    )
    (OUTPUT_DIR / "GREENFIELD_BREAKEVEN_CAPITAL_THRESHOLD_V1_CASES.md").write_text(
        report, encoding="utf-8"
    )

    summary = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": LABELS,
        "method": {
            "source_artifact": "greenfield_siting_sensitivity_v1_cases",
            "parity_artifact": "greenfield_breakeven_capital_threshold_v1",
            "transport_basis": "transport_only_k0_reoptimized_baseline_per_case",
            "formula_cumulative": (
                "(transport_cost_k0_cad_y - transport_cost_k_cad_y)"
                " / module_served_m3_y_k"
            ),
            "formula_marginal": (
                "(transport_cost_k_minus_1_cad_y - transport_cost_k_cad_y)"
                " / (module_served_m3_y_k - module_served_m3_y_k_minus_1)"
            ),
            "threshold_units": "CAD per (m3/yr) of module-served volume",
            "assigned_m3_y_by_case": assigned_by_case,
            "thin_marginal_share_of_assigned": v1.THIN_MARGINAL_SHARE,
            "capital_cost_estimated": False,
            "discount_rate_assumed": False,
            "asset_life_assumed": False,
            "technology_assumed": False,
        },
        "case_headline": case_headlines,
        "thresholds": combined_rows,
        "validation": {
            "all_passed": not failures,
            "checks": checks,
            "failures": [],
        },
        "inputs": {
            str(SOURCE_TRADEOFF_CSV.relative_to(REPO_ROOT)): source_sha256,
            str(SOURCE_MANIFEST_JSON.relative_to(REPO_ROOT)): (
                v9._sha256(SOURCE_MANIFEST_JSON)
            ),
            str(SOURCE_SUMMARY_JSON.relative_to(REPO_ROOT)): (
                v9._sha256(SOURCE_SUMMARY_JSON)
            ),
            str(PARITY_CSV.relative_to(REPO_ROOT)): v9._sha256(PARITY_CSV),
            str(PARITY_SUMMARY_JSON.relative_to(REPO_ROOT)): (
                v9._sha256(PARITY_SUMMARY_JSON)
            ),
        },
        "report": report,
        "reproduce": (
            ".venv/bin/python -m workflows.national.facilities."
            "greenfield_breakeven_capital_threshold_v1_cases"
        ),
    }
    v1._write_json(OUTPUT_DIR / "summary.json", summary)

    outputs: dict[str, Any] = {}
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.is_file() and path.name != "manifest.json":
            outputs[path.name] = {
                "bytes": path.stat().st_size,
                "sha256": v9._sha256(path),
            }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": LABELS,
        "inputs": summary["inputs"],
        "outputs": outputs,
        "reproduce": summary["reproduce"],
    }
    v1._write_json(OUTPUT_DIR / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "case_headline": summary["case_headline"],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
