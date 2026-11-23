"""Greenfield break-even annualized capital thresholds v1.

This artifact converts the committed transport-only greenfield siting
sensitivity (``greenfield_siting_sensitivity_v1``) into a threshold
diagnostic.  It does not estimate capital costs.  For each module count
``k`` it asks how large an annualized capital charge the transport saving
versus the ``k=0`` baseline could support per unit of module-served annual
volume:

    cumulative threshold = (cost_k0 - cost_k) / module_served_m3_k

    marginal threshold = (cost_{k-1} - cost_k)
                         / (module_served_m3_k - module_served_m3_{k-1})

The cumulative threshold is in 2026 CAD per (m3/yr).  It is a break-even
ceiling for an annualized capital charge, not a capital cost estimate.  No
discount rate, lifetime, capex, technology, class capacity, operating cost,
revenue, permitting or landbase assumption is introduced.

Labels: ``model_estimate`` / ``transport_only`` / ``not_investment_advice``.
No national claim, policy claim, UI change or official OSeCAN change is
made.  A ``D`` in either status column of the parent or data repository
halts the build.
"""

from __future__ import annotations

import csv
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import (
    national_facility_assignment_v9 as v9,
)

REPO_ROOT = v9.REPO_ROOT
DATA_ROOT = v9.DATA_ROOT
SOURCE_DIR = (
    DATA_ROOT
    / "derived/national/facilities/greenfield_siting_sensitivity_v1"
)
SOURCE_TRADEOFF_CSV = SOURCE_DIR / "k_tradeoff.csv"
SOURCE_MANIFEST_JSON = SOURCE_DIR / "manifest.json"
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities"
    / "greenfield_breakeven_capital_threshold_v1"
)

ARTIFACT = "greenfield_breakeven_capital_threshold_v1"
ARTIFACT_VERSION = "1.0.0"
EVIDENCE_LABEL = "model_estimate"
SENSITIVITY_LABEL = "transport_only"
INVESTMENT_ADVICE_LABEL = "not_investment_advice"
OBSERVED = False
POLICY_GRADE = False

K_VALUES = tuple(range(21))
ZERO_VOLUME_M3 = 1e-6
# A marginal volume below 0.01% of annual demand is reported but flagged as
# too thin for a stable per-unit marginal threshold.
THIN_MARGINAL_SHARE = 1e-4
COST_TOLERANCE_CAD = 0.01
VOLUME_TOLERANCE_M3 = 1e-3

THRESHOLD_FIELDS = [
    "k",
    "selected_module_count",
    "added_module_id",
    "transport_cost_cad_y",
    "transport_saving_vs_k0_cad_y",
    "transport_saving_vs_k0_b_cad_y",
    "module_served_m3_y",
    "module_served_mm3_y",
    "breakeven_annualized_capital_cad_per_m3_y",
    "marginal_transport_saving_cad_y",
    "marginal_transport_saving_b_cad_y",
    "marginal_module_volume_m3_y",
    "marginal_module_volume_mm3_y",
    "marginal_breakeven_capital_cad_per_m3_y",
    "marginal_threshold_status",
    "evidence_label",
    "sensitivity_label",
    "investment_advice_label",
    "capital_cost_claim",
    "observed",
    "policy_grade",
]

LABELS = {
    "evidence_label": EVIDENCE_LABEL,
    "sensitivity_label": SENSITIVITY_LABEL,
    "investment_advice_label": INVESTMENT_ADVICE_LABEL,
    "investment_advice": False,
    "capital_cost_claim": False,
    "observed": OBSERVED,
    "policy_grade": POLICY_GRADE,
    "national_claim": False,
    "policy_claim": False,
    "official_osecan_changed": False,
    "ui_changed": False,
    "transport_only": True,
}


class BreakevenError(ValueError):
    """Raised when a break-even threshold invariant fails."""


@dataclass(frozen=True)
class TradeoffRow:
    k: int
    transport_cost_cad_y: float
    module_served_m3_y: float
    selected_module_ids: tuple[str, ...]


@dataclass(frozen=True)
class ThresholdRow:
    k: int
    selected_module_count: int
    added_module_id: str
    transport_cost_cad_y: float
    transport_saving_cad_y: float
    module_served_m3_y: float
    breakeven_cad_per_m3_y: float | None
    marginal_saving_cad_y: float
    marginal_volume_m3_y: float
    marginal_breakeven_cad_per_m3_y: float | None
    marginal_threshold_status: str


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _finite_float(value: str, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise BreakevenError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(parsed):
        raise BreakevenError(f"{label} is not finite: {value!r}")
    return parsed


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BreakevenError(f"source CSV is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise BreakevenError(f"source CSV is empty: {path}")
    return rows


def _parse_tradeoff(path: Path) -> tuple[TradeoffRow, ...]:
    raw_rows = _read_csv(path)
    required = {
        "k",
        "selected_module_count",
        "selected_module_ids",
        "transport_cost_cad",
        "module_served_m3",
    }
    missing = required.difference(raw_rows[0])
    if missing:
        raise BreakevenError(f"source CSV is missing fields: {sorted(missing)}")

    rows: list[TradeoffRow] = []
    for index, raw in enumerate(raw_rows):
        k = int(_finite_float(raw["k"], f"row {index} k"))
        if k != index:
            raise BreakevenError(
                f"source k grid is not 0..20 at row {index}: {k}"
            )
        module_count = int(
            _finite_float(
                raw["selected_module_count"],
                f"k={k} selected_module_count",
            )
        )
        if module_count != k:
            raise BreakevenError(
                f"k={k} selected_module_count is {module_count}, expected {k}"
            )
        module_ids = (
            tuple(part for part in raw["selected_module_ids"].split(";") if part)
            if raw["selected_module_ids"]
            else ()
        )
        if len(module_ids) != k:
            raise BreakevenError(
                f"k={k} has {len(module_ids)} module ids, expected {k}"
            )
        cost = _finite_float(raw["transport_cost_cad"], f"k={k} cost")
        volume = _finite_float(raw["module_served_m3"], f"k={k} module volume")
        if cost <= 0.0:
            raise BreakevenError(f"k={k} transport cost is not positive: {cost}")
        if volume < 0.0:
            raise BreakevenError(f"k={k} module volume is negative: {volume}")
        rows.append(TradeoffRow(k, cost, volume, module_ids))

    if tuple(row.k for row in rows) != K_VALUES:
        raise BreakevenError("source k grid is not exactly 0..20")
    if rows[0].module_served_m3_y > ZERO_VOLUME_M3:
        raise BreakevenError("k=0 must have zero module-served volume")
    for previous, current in itertools.pairwise(rows):
        if current.transport_cost_cad_y > previous.transport_cost_cad_y:
            raise BreakevenError(
                f"transport cost increases from k={previous.k} "
                f"to k={current.k}"
            )
        if (
            current.module_served_m3_y
            < previous.module_served_m3_y - VOLUME_TOLERANCE_M3
        ):
            raise BreakevenError(
                f"module volume decreases from k={previous.k} "
                f"to k={current.k}"
            )
        if current.k > 0 and current.module_served_m3_y <= 0.0:
            raise BreakevenError(
                f"k={current.k} has no module-served volume"
            )
    return tuple(rows)


def _marginal_threshold(
    marginal_saving_cad_y: float,
    marginal_volume_m3_y: float,
    assigned_m3_y: float,
) -> tuple[float | None, str]:
    if marginal_saving_cad_y < 0.0:
        raise BreakevenError("marginal transport saving is negative")
    if marginal_volume_m3_y <= ZERO_VOLUME_M3:
        return None, "zero_marginal_module_volume"
    threshold = marginal_saving_cad_y / marginal_volume_m3_y
    if marginal_volume_m3_y < THIN_MARGINAL_SHARE * assigned_m3_y:
        return threshold, "thin_marginal_module_volume"
    return threshold, "defined"


def _compute_thresholds(
    rows: Sequence[TradeoffRow],
    assigned_m3_y: float,
) -> tuple[ThresholdRow, ...]:
    baseline = rows[0]
    thresholds: list[ThresholdRow] = []
    previous = baseline
    for current in rows[1:]:
        saving = baseline.transport_cost_cad_y - current.transport_cost_cad_y
        if saving <= 0.0:
            raise BreakevenError(f"k={current.k} has no transport saving")
        threshold = saving / current.module_served_m3_y
        marginal_saving = (
            previous.transport_cost_cad_y - current.transport_cost_cad_y
        )
        marginal_volume = (
            current.module_served_m3_y - previous.module_served_m3_y
        )
        marginal_threshold, status = _marginal_threshold(
            marginal_saving,
            marginal_volume,
            assigned_m3_y,
        )
        thresholds.append(
            ThresholdRow(
                k=current.k,
                selected_module_count=len(current.selected_module_ids),
                added_module_id=current.selected_module_ids[-1],
                transport_cost_cad_y=current.transport_cost_cad_y,
                transport_saving_cad_y=saving,
                module_served_m3_y=current.module_served_m3_y,
                breakeven_cad_per_m3_y=threshold,
                marginal_saving_cad_y=marginal_saving,
                marginal_volume_m3_y=marginal_volume,
                marginal_breakeven_cad_per_m3_y=marginal_threshold,
                marginal_threshold_status=status,
            )
        )
        previous = current
    return tuple(thresholds)


def _csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BreakevenError(f"refusing to write non-finite value {value!r}")
        return f"{value:.9f}"
    return str(value)


def _write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: _csv_value(row.get(field, "")) for field in fields}
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _threshold_text(value: float | None, flagged: bool = False) -> str:
    if value is None:
        return "—"
    text = f"{value:,.2f}"
    return f"{text} †" if flagged else text


def _build_report(
    thresholds: Sequence[ThresholdRow],
    baseline_cost_cad_y: float,
    assigned_m3_y: float,
    source_sha256: str,
) -> str:
    k20 = thresholds[-1]
    minimum = min(
        thresholds,
        key=lambda row: row.breakeven_cad_per_m3_y
        if row.breakeven_cad_per_m3_y is not None
        else math.inf,
    )
    reliable_marginals = sorted(
        row.marginal_breakeven_cad_per_m3_y
        for row in thresholds
        if row.marginal_threshold_status == "defined"
        and row.marginal_breakeven_cad_per_m3_y is not None
    )
    median_marginal = reliable_marginals[len(reliable_marginals) // 2]
    zero_marginal_k = [
        row.k
        for row in thresholds
        if row.marginal_threshold_status == "zero_marginal_module_volume"
    ]
    thin_marginal_k = [
        row.k
        for row in thresholds
        if row.marginal_threshold_status == "thin_marginal_module_volume"
    ]

    lines = [
        "# Greenfield break-even annualized capital thresholds v1",
        "",
        "Status: **threshold diagnostic** — `not_investment_advice`.",
        (
            "Evidence label: `model_estimate`; sensitivity label: "
            "`transport_only`; `observed=false`; `policy_grade=false`."
        ),
        "",
        "This artifact states a break-even **threshold**, not a capital cost",
        "estimate. It does not estimate module capex, operating cost,",
        "technology, class capacity, revenue, permitting, landbase or",
        "facility design. No national or policy claim is made.",
        "",
        "## 1. Definition",
        "",
        "For module count `k`, with transport cost `TC` from",
        "`greenfield_siting_sensitivity_v1` and module-served annual volume",
        "`V`:",
        "",
        "```text",
        "cumulative threshold_k = (TC_0 - TC_k) / V_k",
        "marginal threshold_k = (TC_{k-1} - TC_k) / (V_k - V_{k-1})",
        "```",
        "",
        "Units are 2026 CAD per (m3/yr) of module-served volume. The",
        "threshold is the maximum annualized capital charge that the",
        "transport saving could support at break-even, before any other",
        "cost or revenue. No discount rate, lifetime or capex assumption is",
        "used.",
        "",
        "The saving is the full transport re-optimization effect versus the",
        "transport-only `k=0` baseline, including changes in existing",
        "facility use. It is not attributed solely to module capital.",
        "",
        f"Source: `{SOURCE_TRADEOFF_CSV.relative_to(REPO_ROOT)}`  ",
        f"SHA-256: `{source_sha256}`  ",
        f"Baseline transport cost: {baseline_cost_cad_y / 1e9:,.3f} B CAD/yr  ",
        f"Assigned demand: {assigned_m3_y / 1e6:,.3f} Mm3/yr",
        "",
        "## 2. Threshold table (k = 1..20)",
        "",
        (
            "| k | added module | cost (B CAD/yr) | cumulative saving "
            "(B CAD/yr) | module volume (Mm3/yr) | cumulative threshold "
            "(CAD per m3/yr) | marginal saving (B CAD/yr) | marginal volume "
            "(Mm3/yr) | marginal threshold (CAD per m3/yr) |"
        ),
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in thresholds:
        module = row.added_module_id.replace("|", "\\|")
        lines.append(
            f"| {row.k} | `{module}` "
            f"| {row.transport_cost_cad_y / 1e9:,.3f} "
            f"| {row.transport_saving_cad_y / 1e9:,.3f} "
            f"| {row.module_served_m3_y / 1e6:,.3f} "
            f"| {_threshold_text(row.breakeven_cad_per_m3_y)} "
            f"| {row.marginal_saving_cad_y / 1e9:,.3f} "
            f"| {row.marginal_volume_m3_y / 1e6:,.3f} "
            f"| {_threshold_text(row.marginal_breakeven_cad_per_m3_y, row.marginal_threshold_status == 'thin_marginal_module_volume')} |"
        )
    lines.extend(
        [
            "",
            "`†` marks a thin marginal volume (below 0.01% of annual demand);",
            "the ratio is shown but is not a stable per-unit threshold.",
            "`—` marks a zero marginal module volume, so the marginal",
            "threshold is undefined.",
            "",
            "## 3. Key thresholds",
            "",
            f"- k=1 cumulative threshold: **{thresholds[0].breakeven_cad_per_m3_y:,.2f}** CAD per m3/yr.",
            f"- Minimum cumulative threshold: **{minimum.breakeven_cad_per_m3_y:,.2f}** CAD per m3/yr at k={minimum.k}.",
            f"- k=20 cumulative threshold: **{k20.breakeven_cad_per_m3_y:,.2f}** CAD per m3/yr.",
            f"- k=20 cumulative transport saving: **{k20.transport_saving_cad_y / 1e9:,.3f} B CAD/yr**.",
            f"- k=20 module-served volume: **{k20.module_served_m3_y / 1e6:,.3f} Mm3/yr**.",
            f"- Median defined marginal threshold: **{median_marginal:,.2f}** CAD per m3/yr.",
            f"- Zero marginal module volume at k={', '.join(map(str, zero_marginal_k)) or 'none'}; undefined marginal threshold.",
            f"- Thin marginal volume at k={', '.join(map(str, thin_marginal_k)) or 'none'}; flagged `†`.",
            "",
            "The cumulative threshold is not monotonic across k. The",
            "k-series is a greedy screening prefix, not a global optimum.",
            "The table is not a module ranking or a recommendation.",
            "",
            "## 4. Labels and caveats",
            "",
            "- `model_estimate`; `transport_only`; `not_investment_advice`;",
            "  `observed=false`; `policy_grade=false`.",
            "- This is a break-even threshold, not a capital cost estimate.",
            "- No capex, discount rate, asset life, fixed operating cost,",
            "  feedstock cost, processing cost, product revenue or financing",
            "  assumption is introduced.",
            "- Module capacity is the screened module-served volume at that k;",
            "  it is not a nameplate capacity, class capacity or design size.",
            "- Transport savings include re-routing and existing-facility",
            "  effects. They are not a module-only revenue stream.",
            "- Distances and costs inherit the screening limitations of",
            "  `greenfield_siting_sensitivity_v1`.",
            "- No national claim, policy claim, UI change or official OSeCAN",
            "  change is made.",
            "",
            "## 5. Reproduce",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.facilities.greenfield_breakeven_capital_threshold_v1",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_hash_matches_manifest(source_sha256: str) -> bool:
    if not SOURCE_MANIFEST_JSON.is_file():
        return False
    manifest = json.loads(
        SOURCE_MANIFEST_JSON.read_text(encoding="utf-8")
    )
    entry = manifest.get("outputs", {}).get(SOURCE_TRADEOFF_CSV.name, {})
    return entry.get("sha256") == source_sha256


def _validation_checks(
    rows: Sequence[TradeoffRow],
    thresholds: Sequence[ThresholdRow],
    assigned_m3_y: float,
    source_sha256: str,
) -> tuple[dict[str, bool], list[str]]:
    baseline = rows[0]
    checks = {
        "input_k_grid_0_20": tuple(row.k for row in rows) == K_VALUES,
        "input_cost_monotonic_nonincreasing": all(
            current.transport_cost_cad_y <= previous.transport_cost_cad_y
            for previous, current in itertools.pairwise(rows)
        ),
        "input_module_volume_non_decreasing": all(
            current.module_served_m3_y
            >= previous.module_served_m3_y - VOLUME_TOLERANCE_M3
            for previous, current in itertools.pairwise(rows)
        ),
        "baseline_zero_module_volume": (
            baseline.module_served_m3_y <= ZERO_VOLUME_M3
        ),
        "cumulative_saving_positive": all(
            row.transport_saving_cad_y > 0.0 for row in thresholds
        ),
        "cumulative_saving_monotonic_nondecreasing": all(
            current.transport_saving_cad_y
            >= previous.transport_saving_cad_y - COST_TOLERANCE_CAD
            for previous, current in itertools.pairwise(thresholds)
        ),
        "marginal_saving_nonnegative": all(
            row.marginal_saving_cad_y >= -COST_TOLERANCE_CAD
            for row in thresholds
        ),
        "module_count_matches_k": all(
            row.selected_module_count == row.k for row in thresholds
        ),
        "thresholds_nonnegative": all(
            row.breakeven_cad_per_m3_y is not None
            and row.breakeven_cad_per_m3_y >= 0.0
            for row in thresholds
        ),
        "marginal_thresholds_nonnegative_or_undefined": all(
            row.marginal_breakeven_cad_per_m3_y is None
            or row.marginal_breakeven_cad_per_m3_y >= 0.0
            for row in thresholds
        ),
        "cumulative_threshold_arithmetic": all(
            math.isclose(
                row.breakeven_cad_per_m3_y,
                row.transport_saving_cad_y / row.module_served_m3_y,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for row in thresholds
            if row.breakeven_cad_per_m3_y is not None
        ),
        "marginal_threshold_arithmetic": all(
            (
                row.marginal_breakeven_cad_per_m3_y is None
                and row.marginal_volume_m3_y <= ZERO_VOLUME_M3
            )
            or (
                row.marginal_breakeven_cad_per_m3_y is not None
                and math.isclose(
                    row.marginal_breakeven_cad_per_m3_y,
                    row.marginal_saving_cad_y / row.marginal_volume_m3_y,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            )
            for row in thresholds
        ),
        "zero_marginal_volume_flagged": all(
            (
                row.marginal_threshold_status
                == "zero_marginal_module_volume"
            )
            == (row.marginal_volume_m3_y <= ZERO_VOLUME_M3)
            for row in thresholds
        ),
        "thin_marginal_volume_flagged": all(
            (
                row.marginal_threshold_status
                == "thin_marginal_module_volume"
            )
            == (
                ZERO_VOLUME_M3
                < row.marginal_volume_m3_y
                < THIN_MARGINAL_SHARE * assigned_m3_y
            )
            for row in thresholds
        ),
        "source_manifest_hash_matches": _source_hash_matches_manifest(
            source_sha256
        ),
        "labels": (
            LABELS["evidence_label"] == "model_estimate"
            and LABELS["sensitivity_label"] == "transport_only"
            and LABELS["investment_advice_label"]
            == "not_investment_advice"
            and LABELS["capital_cost_claim"] is False
            and LABELS["national_claim"] is False
            and LABELS["policy_claim"] is False
            and LABELS["observed"] is False
            and LABELS["policy_grade"] is False
        ),
        "no_nan_or_inf": all(
            math.isfinite(value)
            for row in thresholds
            for value in (
                row.transport_cost_cad_y,
                row.transport_saving_cad_y,
                row.module_served_m3_y,
                row.breakeven_cad_per_m3_y,
                row.marginal_saving_cad_y,
                row.marginal_volume_m3_y,
                row.marginal_breakeven_cad_per_m3_y,
            )
            if value is not None
        ),
    }
    failures = [name for name, passed in checks.items() if not passed]
    return checks, failures


def _threshold_record(row: ThresholdRow) -> dict[str, Any]:
    return {
        "k": row.k,
        "selected_module_count": row.selected_module_count,
        "added_module_id": row.added_module_id,
        "transport_cost_cad_y": row.transport_cost_cad_y,
        "transport_saving_vs_k0_cad_y": row.transport_saving_cad_y,
        "transport_saving_vs_k0_b_cad_y": (
            row.transport_saving_cad_y / 1e9
        ),
        "module_served_m3_y": row.module_served_m3_y,
        "module_served_mm3_y": row.module_served_m3_y / 1e6,
        "breakeven_annualized_capital_cad_per_m3_y": (
            row.breakeven_cad_per_m3_y
        ),
        "marginal_transport_saving_cad_y": row.marginal_saving_cad_y,
        "marginal_transport_saving_b_cad_y": (
            row.marginal_saving_cad_y / 1e9
        ),
        "marginal_module_volume_m3_y": row.marginal_volume_m3_y,
        "marginal_module_volume_mm3_y": row.marginal_volume_m3_y / 1e6,
        "marginal_breakeven_capital_cad_per_m3_y": (
            row.marginal_breakeven_cad_per_m3_y
        ),
        "marginal_threshold_status": row.marginal_threshold_status,
        **LABELS,
    }


def build() -> dict[str, Any]:
    generated_utc = _utc_now()
    source_sha256 = v9._sha256(SOURCE_TRADEOFF_CSV)
    rows = _parse_tradeoff(SOURCE_TRADEOFF_CSV)
    if rows[0].module_served_m3_y != 0.0:
        raise BreakevenError("k=0 module volume is not exactly zero")
    # Demand is the assigned volume in the source table, not module volume.
    assigned_m3_y = _assigned_volume_from_source()
    thresholds = _compute_thresholds(rows, assigned_m3_y)
    checks, failures = _validation_checks(
        rows,
        thresholds,
        assigned_m3_y,
        source_sha256,
    )
    if failures:
        raise BreakevenError(f"validation failed: {failures}")

    report = _build_report(
        thresholds,
        rows[0].transport_cost_cad_y,
        assigned_m3_y,
        source_sha256,
    )
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    threshold_rows: list[dict[str, Any]] = [
        {
            "k": 0,
            "selected_module_count": 0,
            "added_module_id": "",
            "transport_cost_cad_y": rows[0].transport_cost_cad_y,
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
    ]
    threshold_rows.extend(_threshold_record(row) for row in thresholds)
    _write_csv(
        OUTPUT_DIR / "breakeven_thresholds.csv",
        THRESHOLD_FIELDS,
        threshold_rows,
    )
    (OUTPUT_DIR / "GREENFIELD_BREAKEVEN_CAPITAL_THRESHOLD_V1.md").write_text(
        report,
        encoding="utf-8",
    )

    k20 = thresholds[-1]
    minimum = min(
        thresholds,
        key=lambda row: row.breakeven_cad_per_m3_y
        if row.breakeven_cad_per_m3_y is not None
        else math.inf,
    )
    reliable_marginals = sorted(
        row.marginal_breakeven_cad_per_m3_y
        for row in thresholds
        if row.marginal_threshold_status == "defined"
        and row.marginal_breakeven_cad_per_m3_y is not None
    )
    summary = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": LABELS,
        "method": {
            "source_artifact": "greenfield_siting_sensitivity_v1",
            "transport_basis": "transport_only_k0_reoptimized_baseline",
            "formula_cumulative": (
                "(transport_cost_k0_cad_y - transport_cost_k_cad_y)"
                " / module_served_m3_y_k"
            ),
            "formula_marginal": (
                "(transport_cost_k_minus_1_cad_y - transport_cost_k_cad_y)"
                " / (module_served_m3_y_k - module_served_m3_y_k_minus_1)"
            ),
            "threshold_units": "CAD per (m3/yr) of module-served volume",
            "assigned_m3_y": assigned_m3_y,
            "zero_marginal_volume_m3": ZERO_VOLUME_M3,
            "thin_marginal_share_of_assigned": THIN_MARGINAL_SHARE,
            "thin_marginal_volume_m3": (
                THIN_MARGINAL_SHARE * assigned_m3_y
            ),
            "capital_cost_estimated": False,
            "discount_rate_assumed": False,
            "asset_life_assumed": False,
            "technology_assumed": False,
        },
        "baseline": {
            "k": 0,
            "transport_cost_cad_y": rows[0].transport_cost_cad_y,
            "module_served_m3_y": 0.0,
            "breakeven_threshold_defined": False,
        },
        "headline": {
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
            "k20_transport_saving_b_cad_y": (
                k20.transport_saving_cad_y / 1e9
            ),
            "k20_module_served_mm3_y": k20.module_served_m3_y / 1e6,
            "median_defined_marginal_threshold_cad_per_m3_y": (
                reliable_marginals[len(reliable_marginals) // 2]
            ),
            "defined_marginal_threshold_count": len(reliable_marginals),
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
        },
        "thresholds": threshold_rows,
        "validation": {
            "all_passed": not failures,
            "checks": checks,
            "failures": failures,
        },
        "inputs": {
            str(SOURCE_TRADEOFF_CSV.relative_to(REPO_ROOT)): source_sha256,
            str(SOURCE_MANIFEST_JSON.relative_to(REPO_ROOT)): (
                v9._sha256(SOURCE_MANIFEST_JSON)
            ),
        },
        "report": report,
        "reproduce": (
            ".venv/bin/python -m workflows.national.facilities."
            "greenfield_breakeven_capital_threshold_v1"
        ),
    }
    _write_json(OUTPUT_DIR / "summary.json", summary)

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
    _write_json(OUTPUT_DIR / "manifest.json", manifest)
    return summary


def _assigned_volume_from_source() -> float:
    raw_rows = _read_csv(SOURCE_TRADEOFF_CSV)
    assigned = _finite_float(raw_rows[0]["assigned_m3"], "k=0 assigned_m3")
    if assigned <= 0.0:
        raise BreakevenError(f"assigned volume is not positive: {assigned}")
    return assigned


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "k20_cumulative_threshold_cad_per_m3_y": summary[
                    "headline"
                ]["k20_cumulative_threshold_cad_per_m3_y"],
                "minimum_cumulative_threshold": summary["headline"][
                    "minimum_cumulative_threshold"
                ],
                "k20_transport_saving_b_cad_y": summary["headline"][
                    "k20_transport_saving_b_cad_y"
                ],
                "validation": summary["validation"]["all_passed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
