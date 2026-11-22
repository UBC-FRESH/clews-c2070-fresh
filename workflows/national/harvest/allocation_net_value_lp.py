"""Provincial harvest-allocation net-value LP sensitivity using v2.3 margins.

The canonical provincial allocation in
``workflows.national.harvest.provincial_allocation`` remains a least-deviation
allocation from observed 2024 shares.  This module adds a separate, report-only
sensitivity: for each national harvest ceiling and each v2.3 price scenario it
maximizes modeled net value

    maximize  sum_p harvest_p * margin_p

subject to the same provincial caps, national-total equality, and optional
operable-proxy intensity caps.  The v2.3 margin table provides low, central
(``base``), and high margins.  The LP is optimized on the central margin and
the same allocation is re-priced at the low and high margins to produce an
uncertainty envelope.

The least-deviation canonical allocation is retained as the baseline.  This
module does not change it, does not write to the canonical output directory,
does not feed the UI, and is not a public OSeCAN input.

Net value here is a unit-margin times harvest diagnostic in million CAD.  It is
not observed profit, not a mill margin, not a discounted cash flow, and not
policy grade.  Negative margins are retained unclipped.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.allocation_net_value_lp
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pulp

from workflows.national.harvest import provincial_allocation as baseline_model

__all__ = [
    "ALLOCATION_CSV",
    "ALLOCATION_DELTA_CSV",
    "ARTIFACT_VERSION",
    "CAP_CASES",
    "DEFAULT_OUT_DIR",
    "LEVELS",
    "MANIFEST_JSON",
    "MODEL_ID",
    "REPORT_MD",
    "SCENARIOS",
    "SUMMARY_JSON",
    "AllocationNetValueError",
    "assert_no_unexpected_deletions",
    "build",
    "load_margins",
    "main",
    "solve_net_value_harvest",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
MARGIN_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"
MARGIN_SUMMARY_PATH = MARGIN_DIR / "national_margin_summary.csv"
MARGIN_SUMMARY_JSON_PATH = MARGIN_DIR / "summary.json"
MARGIN_MANIFEST_PATH = MARGIN_DIR / "manifest.json"
CANONICAL_ALLOCATION_DIR = HARVEST_BASIS_DIR / "provincial_allocation"
BASELINE_SOURCE_PATH = REPO_ROOT / "workflows/national/harvest/provincial_allocation.py"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "allocation_net_value_lp"

ALLOCATION_CSV = "allocation_net_value_lp.csv"
ALLOCATION_DELTA_CSV = "allocation_delta.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "ALLOCATION_NET_VALUE_LP_REPORT.md"
MANIFEST_JSON = "manifest.json"

MODEL_ID = "provincial_allocation_net_value_lp_v1"
ARTIFACT_VERSION = "1.0.0"
SCHEMA_VERSION = "national/provincial-allocation-net-value-lp/1"
SOURCE_ECONOMICS_MODEL_ID = "national_economics_v2_3"
SOURCE_ALLOCATION_MODEL_ID = "provincial_allocation_lp"

EVIDENCE = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
LP_OBJECTIVE = "net_value_sensitivity"
CANONICAL_CHANGED = False
COMBINED_NATIONAL_NET_VALUE = False
PUBLIC_OSECAN_INPUT = False
BASELINE_OBJECTIVE = "least_deviation_from_observed_2024_shares"

SCENARIOS = ("donor_flat", "stumpage_scaled")
SCENARIO_ROLE = {
    "donor_flat": "base",
    "stumpage_scaled": "sensitivity",
}
MARGIN_CASES = ("low", "base", "high")
MARGIN_FIELD = {
    "low": "margin_low_cad_m3",
    "base": "margin_base_cad_m3",
    "high": "margin_high_cad_m3",
}
OPTIMIZATION_MARGIN_CASE = "base"
LEVELS = ("100m", "119m", "150m")
LEVEL_TOTALS_M3 = dict(baseline_model.LADDER_TOTALS)
CAP_CASES = ("documented_ceiling", "intensity_1.0", "intensity_1.2")
CAP_CASE_ROLE = {
    "documented_ceiling": "documented provincial ceilings",
    "intensity_1.0": "operable-proxy intensity screen, I=1.0 m3/ha/yr",
    "intensity_1.2": "operable-proxy intensity screen, I=1.2 m3/ha/yr",
}
CAP_INTENSITY = {
    "documented_ceiling": None,
    "intensity_1.0": 1.0,
    "intensity_1.2": 1.2,
}

PROVINCE_ORDER = baseline_model.PROVINCE_ORDER
PROVINCE_NAMES = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}

CAP_TOLERANCE_M3 = baseline_model.CAP_TOLERANCE_M3
TOTAL_TOLERANCE_M3 = 2.0
NET_VALUE_TOLERANCE_M_CAD = 0.01
SHARE_TOLERANCE = 1e-6
CANONICAL_MATCH_TOLERANCE_M3 = 2.0

ALLOWED_INPUT_ROOTS = (
    REPO_ROOT / "data/derived",
    REPO_ROOT / "planning",
    REPO_ROOT / "workflows",
)
FORBIDDEN_OUTPUT_ROOTS = (
    REPO_ROOT / "ui",
    REPO_ROOT / "ui_publications",
)

DONOR_PRICE_CAVEAT = (
    "donor_flat is a donor/flat price proxy, not an observed provincial price; "
    "stumpage_scaled is a method sensitivity. Both are model_estimate, "
    "observed=false, policy_grade=false, and not policy-grade net value."
)
NEGATIVE_MARGIN_CAVEAT = (
    "Negative margins are retained unclipped. When total harvest exceeds the "
    "capacity of positive-margin provinces, the LP necessarily allocates to "
    "negative-margin provinces; it chooses the least-negative available margin."
)
INTENSITY_CAVEAT = baseline_model.INTENSITY_PROXY_CAVEAT

CAVEATS = (
    DONOR_PRICE_CAVEAT,
    NEGATIVE_MARGIN_CAVEAT,
    INTENSITY_CAVEAT,
    (
        "The objective is a static unit-margin times harvest sum. It has no "
        "demand, mill capacity, tenure, haul-distance, species, employment, "
        "silviculture, or discounting content."
    ),
    (
        "The net-value allocation is a sensitivity, not a policy allocation, "
        "not a forecast, and not a legal AAC. The canonical least-deviation "
        "allocation is unchanged and remains the model of record."
    ),
    (
        "The documented-cap sum is a mixed-basis ceiling set, not a national "
        "AAC. PE and NU have no operable proxy and are flagged "
        "operable_missing, not assigned zero area."
    ),
    (
        "Net value low/base/high re-prices the central-margin allocation at "
        "the v2.3 low/base/high margins; low and high are uncertainty bounds, "
        "not separately optimized allocations."
    ),
)

NON_CLAIMS = (
    "Not observed; observed=false.",
    "Not policy grade; policy_grade=false.",
    "Not a canonical provincial-allocation change; canonical_changed=false.",
    "Not a UI or public OSeCAN input; no public OSeCAN.",
    "Not observed profit, net income, mill margin, or official net value.",
    "Not a ranking or investment recommendation.",
)


class AllocationNetValueError(RuntimeError):
    """Raised when the net-value allocation sensitivity cannot be reproduced."""


def _fail(message: str) -> None:
    raise AllocationNetValueError(message)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        _fail(f"missing required CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        _fail(f"required CSV has no data rows: {path}")
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"missing required JSON: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(f"invalid JSON {path}: {error}")
    if not isinstance(payload, dict):
        _fail(f"expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _git_commit(repository: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def _deletion_lines(status_text: str) -> list[str]:
    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions(repo_root: Path = REPO_ROOT) -> None:
    """Halt on any worktree or index deletion in the parent or data repo."""

    repositories = [repo_root]
    data_root = repo_root / "data"
    if (data_root / ".git").exists():
        repositories.append(data_root)
    for repository in repositories:
        result = subprocess.run(
            ["git", "-C", str(repository), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            _fail(f"git status failed in {repository}: {result.stderr.strip()}")
        deletions = _deletion_lines(result.stdout)
        if deletions:
            _fail(
                f"unexpected deletion in {repository}: {deletions[0]!r}; "
                "halting fail-closed"
            )


def _text(row: Mapping[str, str], field: str, context: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        _fail(f"{context}: missing {field}")
    return value


def _finite_float(value: object, *, field: str, context: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        _fail(f"{context}: missing {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        _fail(f"{context}: {field} is not numeric: {value!r}")
        raise AssertionError("unreachable") from error
    if not math.isfinite(number):
        _fail(f"{context}: {field} is not finite: {value!r}")
    return number


def _boolean(value: object, *, field: str, context: str) -> bool:
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    _fail(f"{context}: {field} must be true or false, got {value!r}")
    raise AssertionError("unreachable")


def _round(value: float, digits: int = 6) -> float:
    rounded = round(value, digits)
    return 0.0 if rounded == 0.0 else rounded


def _fmt(value: object, *, digits: int = 9) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == 0.0:
            return "0"
        text = f"{value:.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, (list, tuple)):
        return ";".join(str(item) for item in value)
    return str(value)


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, object]],
    fieldnames: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _fmt(row.get(field)) for field in fieldnames})


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _snapshot_tree(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {
        path.relative_to(directory).as_posix(): _sha256(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


def _assert_input_path(path: Path) -> None:
    resolved = path.resolve()
    if any(part in {"restricted", "private", "credentials"} for part in resolved.parts):
        _fail(f"restricted input path is not allowed: {path}")
    if not any(resolved.is_relative_to(root.resolve()) for root in ALLOWED_INPUT_ROOTS):
        _fail(f"input is outside the approved derived/planning/workflow roots: {path}")


def _assert_output_path_allowed(out_dir: Path) -> None:
    resolved = out_dir.resolve()
    for forbidden in FORBIDDEN_OUTPUT_ROOTS:
        if resolved.is_relative_to(forbidden.resolve()):
            _fail(f"output must not be written under a UI/publication root: {out_dir}")


@dataclass(frozen=True)
class ProvinceMargin:
    province: str
    scenario: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    margin_status: str
    price_basis: str
    cost_basis: str
    observed_2024_m3: float

    def by_case(self, margin_case: str) -> float:
        return {
            "low": self.low_cad_m3,
            "base": self.base_cad_m3,
            "high": self.high_cad_m3,
        }[margin_case]


@dataclass(frozen=True)
class NetValueSolve:
    status: str
    harvest_m3: dict[str, float]
    capacity_m3: float
    deficit_m3: float
    lp_optimum_m_cad: float | None
    achieved_m_cad: float | None
    optimality_gap_m_cad: float | None

    @property
    def is_optimal(self) -> bool:
        return self.status == "optimal"


@dataclass(frozen=True)
class AllocationComparisonRow:
    province: str
    observed_share: float
    baseline_harvest_m3: float
    optimized_harvest_m3: float
    baseline_share: float
    optimized_share: float
    delta_m3: float
    delta_share: float
    net_value_low_m_cad: float
    net_value_base_m_cad: float
    net_value_high_m_cad: float
    baseline_net_value_low_m_cad: float
    baseline_net_value_base_m_cad: float
    baseline_net_value_high_m_cad: float
    delta_net_value_base_m_cad: float
    upper_bound_m3: float
    binding: bool
    baseline_binding: bool
    basis: str
    margin_low_cad_m3: float
    margin_base_cad_m3: float
    margin_high_cad_m3: float
    margin_status: str
    price_basis: str
    cost_basis: str

    @property
    def direction(self) -> str:
        if self.delta_m3 > CAP_TOLERANCE_M3:
            return "gain"
        if self.delta_m3 < -CAP_TOLERANCE_M3:
            return "loss"
        return "unchanged"


@dataclass(frozen=True)
class NetValueCase:
    scenario: str
    cap_case: str
    level: str
    status: str
    national_total_m3: float
    capacity_m3: float
    deficit_m3: float
    rows: tuple[AllocationComparisonRow, ...]
    baseline_status: str
    baseline_harvest_sum_m3: float | None
    baseline_l1_deviation_m3: float | None
    net_value_low_m_cad: float | None
    net_value_base_m_cad: float | None
    net_value_high_m_cad: float | None
    baseline_net_value_low_m_cad: float | None
    baseline_net_value_base_m_cad: float | None
    baseline_net_value_high_m_cad: float | None
    delta_net_value_low_m_cad: float | None
    delta_net_value_base_m_cad: float | None
    delta_net_value_high_m_cad: float | None
    lp_optimum_m_cad: float | None
    achieved_m_cad: float | None
    optimality_gap_m_cad: float | None
    binding_provinces: tuple[str, ...]
    baseline_binding_provinces: tuple[str, ...]

    @property
    def is_optimal(self) -> bool:
        return self.status == "optimal"

    @property
    def harvest_sum_m3(self) -> float:
        return sum(row.optimized_harvest_m3 for row in self.rows)

    @property
    def gaining_provinces(self) -> tuple[str, ...]:
        return tuple(row.province for row in self.rows if row.direction == "gain")

    @property
    def losing_provinces(self) -> tuple[str, ...]:
        return tuple(row.province for row in self.rows if row.direction == "loss")

    @property
    def unchanged_provinces(self) -> tuple[str, ...]:
        return tuple(row.province for row in self.rows if row.direction == "unchanged")


def _cap_basis(
    base_basis: Sequence[baseline_model.ProvinceBasis], cap_case: str
) -> tuple[baseline_model.ProvinceBasis, ...]:
    if cap_case not in CAP_CASES:
        _fail(f"unknown cap case: {cap_case}")
    intensity = CAP_INTENSITY[cap_case]
    if intensity is None:
        return tuple(base_basis)
    return baseline_model.with_operable_intensity_cap(base_basis, intensity)


def load_margins(
    basis: Sequence[baseline_model.ProvinceBasis],
) -> dict[str, dict[str, ProvinceMargin]]:
    """Read v2.3 province margins and enforce the model-estimate labels."""

    rows = _read_csv(MARGIN_SUMMARY_PATH)
    province_rows = [row for row in rows if row.get("row_type") == "province"]
    expected = len(SCENARIOS) * len(PROVINCE_ORDER)
    if len(province_rows) != expected:
        _fail(
            f"margin summary must have {expected} province rows, "
            f"got {len(province_rows)}"
        )

    observed_by_province = {row.province: row.observed_2024_m3 for row in basis}
    margins: dict[str, dict[str, ProvinceMargin]] = {
        scenario: {} for scenario in SCENARIOS
    }
    for row in province_rows:
        province = _text(row, "province", "margin summary")
        scenario = _text(row, "price_scenario", f"margin summary {province}")
        if province not in PROVINCE_ORDER:
            _fail(f"margin summary has unexpected province {province!r}")
        if scenario not in SCENARIOS:
            _fail(f"margin summary has unexpected price scenario {scenario!r}")
        context = f"margin summary {scenario}/{province}"
        if _text(row, "evidence", context) != EVIDENCE:
            _fail(f"{context}: evidence must be {EVIDENCE!r}")
        if _boolean(row.get("observed"), field="observed", context=context):
            _fail(f"{context}: observed must be false")
        if _boolean(row.get("policy_grade"), field="policy_grade", context=context):
            _fail(f"{context}: policy_grade must be false")
        if _boolean(
            row.get("combined_national_net_value"),
            field="combined_national_net_value",
            context=context,
        ):
            _fail(f"{context}: combined_national_net_value must be false")
        observed = _finite_float(
            row.get("observed_2024_volume_m3"),
            field="observed_2024_volume_m3",
            context=context,
        )
        if abs(observed - observed_by_province[province]) > 1.0:
            _fail(
                f"{context}: observed volume {observed:,.1f} does not match "
                f"allocation basis {observed_by_province[province]:,.1f}"
            )
        margins[scenario][province] = ProvinceMargin(
            province=province,
            scenario=scenario,
            low_cad_m3=_finite_float(
                row.get("margin_low_cad_m3"),
                field="margin_low_cad_m3",
                context=context,
            ),
            base_cad_m3=_finite_float(
                row.get("margin_base_cad_m3"),
                field="margin_base_cad_m3",
                context=context,
            ),
            high_cad_m3=_finite_float(
                row.get("margin_high_cad_m3"),
                field="margin_high_cad_m3",
                context=context,
            ),
            margin_status=_text(row, "margin_status", context),
            price_basis=_text(row, "price_basis", context),
            cost_basis=_text(row, "cost_basis", context),
            observed_2024_m3=observed,
        )

    for scenario in SCENARIOS:
        missing = [p for p in PROVINCE_ORDER if p not in margins[scenario]]
        if missing:
            _fail(f"{scenario}: margin summary is missing {', '.join(missing)}")
    return margins


def solve_net_value_harvest(
    caps_m3: Mapping[str, float],
    margins_cad_m3: Mapping[str, float],
    national_total_m3: float,
    *,
    label: str = "net_value",
) -> NetValueSolve:
    """Maximize modeled net value subject to caps and a national-total equality.

    Unit margins are distinct across provinces in both v2.3 scenarios, so the
    optimal allocation is unique and no secondary tie-break is needed.
    """

    if not caps_m3:
        _fail(f"{label}: caps are empty")
    if set(caps_m3) != set(margins_cad_m3):
        _fail(f"{label}: caps and margins have different province sets")
    if national_total_m3 <= 0.0:
        _fail(f"{label}: national total must be positive")

    caps = {p: float(caps_m3[p]) for p in caps_m3}
    margins = {p: float(margins_cad_m3[p]) for p in margins_cad_m3}
    for province, cap in caps.items():
        if not math.isfinite(cap) or cap < 0.0:
            _fail(f"{label}/{province}: cap must be finite and non-negative")
    for province, margin in margins.items():
        if not math.isfinite(margin):
            _fail(f"{label}/{province}: margin must be finite")

    capacity = sum(caps.values())
    if national_total_m3 > capacity + CAP_TOLERANCE_M3:
        return NetValueSolve(
            status="infeasible",
            harvest_m3={},
            capacity_m3=capacity,
            deficit_m3=national_total_m3 - capacity,
            lp_optimum_m_cad=None,
            achieved_m_cad=None,
            optimality_gap_m_cad=None,
        )

    scale = 1_000_000.0
    provinces = tuple(caps)
    problem = pulp.LpProblem(
        "net_value_" + "".join(ch if ch.isalnum() else "_" for ch in label),
        pulp.LpMaximize,
    )
    harvest = {
        province: pulp.LpVariable(
            f"h_{province}",
            lowBound=0.0,
            upBound=caps[province] / scale,
        )
        for province in provinces
    }
    net_value = pulp.lpSum(
        harvest[province] * margins[province] for province in provinces
    )
    problem += pulp.lpSum(harvest.values()) == national_total_m3 / scale
    problem += net_value

    solver = pulp.PULP_CBC_CMD(msg=False, threads=1)
    status = problem.solve(solver)
    if status != pulp.LpStatusOptimal:
        _fail(f"{label}: net-value solve failed with status {pulp.LpStatus[status]}")
    lp_optimum = float(pulp.value(net_value))

    raw_values = {province: harvest[province].value() for province in provinces}
    if any(
        value is None or not math.isfinite(float(value))
        for value in raw_values.values()
    ):
        _fail(f"{label}: solver returned a non-finite harvest value")
    allocated = {
        province: float(value) * scale
        for province, value in raw_values.items()
        if value is not None
    }
    if any(value < -CAP_TOLERANCE_M3 for value in allocated.values()):
        _fail(f"{label}: solver returned a negative harvest value")
    for province, value in allocated.items():
        if value > caps[province] + CAP_TOLERANCE_M3:
            _fail(
                f"{label}/{province}: harvest {value:,.3f} exceeds cap "
                f"{caps[province]:,.3f}"
            )

    achieved = (
        sum(allocated[province] * margins[province] for province in provinces)
        / 1_000_000.0
    )
    gap = lp_optimum - achieved
    if gap > NET_VALUE_TOLERANCE_M_CAD:
        _fail(
            f"{label}: achieved net value {achieved:,.6f} falls short of "
            f"optimum {lp_optimum:,.6f} by {gap:,.6f} M CAD"
        )
    return NetValueSolve(
        status="optimal",
        harvest_m3={province: max(0.0, value) for province, value in allocated.items()},
        capacity_m3=capacity,
        deficit_m3=0.0,
        lp_optimum_m_cad=lp_optimum,
        achieved_m_cad=achieved,
        optimality_gap_m_cad=gap,
    )


def _net_value(
    harvest_m3: Mapping[str, float],
    margins: Mapping[str, ProvinceMargin],
    margin_case: str,
) -> float:
    return (
        sum(
            harvest_m3[province] * margins[province].by_case(margin_case)
            for province in harvest_m3
        )
        / 1_000_000.0
    )


def _solve_case(
    basis: Sequence[baseline_model.ProvinceBasis],
    baseline: baseline_model.CaseResult,
    margins: Mapping[str, ProvinceMargin],
    *,
    scenario: str,
    cap_case: str,
    level: str,
) -> NetValueCase:
    national_total = LEVEL_TOTALS_M3[level]
    caps = {row.province: row.upper_bound_m3 for row in basis}
    margin_values = {
        province: margins[province].base_cad_m3 for province in PROVINCE_ORDER
    }

    if baseline.status != "optimal":
        capacity = sum(caps.values())
        return NetValueCase(
            scenario=scenario,
            cap_case=cap_case,
            level=level,
            status="infeasible",
            national_total_m3=national_total,
            capacity_m3=capacity,
            deficit_m3=max(0.0, national_total - capacity),
            rows=(),
            baseline_status=baseline.status,
            baseline_harvest_sum_m3=None,
            baseline_l1_deviation_m3=None,
            net_value_low_m_cad=None,
            net_value_base_m_cad=None,
            net_value_high_m_cad=None,
            baseline_net_value_low_m_cad=None,
            baseline_net_value_base_m_cad=None,
            baseline_net_value_high_m_cad=None,
            delta_net_value_low_m_cad=None,
            delta_net_value_base_m_cad=None,
            delta_net_value_high_m_cad=None,
            lp_optimum_m_cad=None,
            achieved_m_cad=None,
            optimality_gap_m_cad=None,
            binding_provinces=(),
            baseline_binding_provinces=baseline.binding_provinces,
        )

    baseline_harvest = {row.province: row.harvest_m3 for row in baseline.rows}
    solved = solve_net_value_harvest(
        caps,
        margin_values,
        national_total,
        label=f"{scenario}_{cap_case}_{level}",
    )
    if not solved.is_optimal:
        return NetValueCase(
            scenario=scenario,
            cap_case=cap_case,
            level=level,
            status=solved.status,
            national_total_m3=national_total,
            capacity_m3=solved.capacity_m3,
            deficit_m3=solved.deficit_m3,
            rows=(),
            baseline_status=baseline.status,
            baseline_harvest_sum_m3=baseline.harvest_sum_m3,
            baseline_l1_deviation_m3=baseline.optimal_l1_deviation_m3,
            net_value_low_m_cad=None,
            net_value_base_m_cad=None,
            net_value_high_m_cad=None,
            baseline_net_value_low_m_cad=_net_value(baseline_harvest, margins, "low"),
            baseline_net_value_base_m_cad=_net_value(baseline_harvest, margins, "base"),
            baseline_net_value_high_m_cad=_net_value(baseline_harvest, margins, "high"),
            delta_net_value_low_m_cad=None,
            delta_net_value_base_m_cad=None,
            delta_net_value_high_m_cad=None,
            lp_optimum_m_cad=None,
            achieved_m_cad=None,
            optimality_gap_m_cad=None,
            binding_provinces=(),
            baseline_binding_provinces=baseline.binding_provinces,
        )

    baseline_by_province = {row.province: row for row in baseline.rows}
    comparison_rows: list[AllocationComparisonRow] = []
    for row in basis:
        baseline_row = baseline_by_province[row.province]
        optimized = solved.harvest_m3[row.province]
        margin = margins[row.province]
        baseline_share = baseline_row.harvest_m3 / national_total
        optimized_share = optimized / national_total
        delta_m3 = optimized - baseline_row.harvest_m3
        net_low = optimized * margin.low_cad_m3 / 1_000_000.0
        net_base = optimized * margin.base_cad_m3 / 1_000_000.0
        net_high = optimized * margin.high_cad_m3 / 1_000_000.0
        baseline_net_low = baseline_row.harvest_m3 * margin.low_cad_m3 / 1_000_000.0
        baseline_net_base = baseline_row.harvest_m3 * margin.base_cad_m3 / 1_000_000.0
        baseline_net_high = baseline_row.harvest_m3 * margin.high_cad_m3 / 1_000_000.0
        comparison_rows.append(
            AllocationComparisonRow(
                province=row.province,
                observed_share=row.observed_2024_m3
                / baseline_model.NATIONAL_OBSERVED_2024_M3,
                baseline_harvest_m3=baseline_row.harvest_m3,
                optimized_harvest_m3=optimized,
                baseline_share=baseline_share,
                optimized_share=optimized_share,
                delta_m3=delta_m3,
                delta_share=optimized_share - baseline_share,
                net_value_low_m_cad=net_low,
                net_value_base_m_cad=net_base,
                net_value_high_m_cad=net_high,
                baseline_net_value_low_m_cad=baseline_net_low,
                baseline_net_value_base_m_cad=baseline_net_base,
                baseline_net_value_high_m_cad=baseline_net_high,
                delta_net_value_base_m_cad=net_base - baseline_net_base,
                upper_bound_m3=row.upper_bound_m3,
                binding=row.upper_bound_m3 > 0.0
                and row.upper_bound_m3 - optimized <= CAP_TOLERANCE_M3,
                baseline_binding=baseline_row.binding,
                basis=row.basis,
                margin_low_cad_m3=margin.low_cad_m3,
                margin_base_cad_m3=margin.base_cad_m3,
                margin_high_cad_m3=margin.high_cad_m3,
                margin_status=margin.margin_status,
                price_basis=margin.price_basis,
                cost_basis=margin.cost_basis,
            )
        )

    rows = tuple(comparison_rows)
    net_low = sum(row.net_value_low_m_cad for row in rows)
    net_base = sum(row.net_value_base_m_cad for row in rows)
    net_high = sum(row.net_value_high_m_cad for row in rows)
    baseline_net_low = sum(row.baseline_net_value_low_m_cad for row in rows)
    baseline_net_base = sum(row.baseline_net_value_base_m_cad for row in rows)
    baseline_net_high = sum(row.baseline_net_value_high_m_cad for row in rows)
    return NetValueCase(
        scenario=scenario,
        cap_case=cap_case,
        level=level,
        status="optimal",
        national_total_m3=national_total,
        capacity_m3=solved.capacity_m3,
        deficit_m3=0.0,
        rows=rows,
        baseline_status=baseline.status,
        baseline_harvest_sum_m3=baseline.harvest_sum_m3,
        baseline_l1_deviation_m3=baseline.optimal_l1_deviation_m3,
        net_value_low_m_cad=net_low,
        net_value_base_m_cad=net_base,
        net_value_high_m_cad=net_high,
        baseline_net_value_low_m_cad=baseline_net_low,
        baseline_net_value_base_m_cad=baseline_net_base,
        baseline_net_value_high_m_cad=baseline_net_high,
        delta_net_value_low_m_cad=net_low - baseline_net_low,
        delta_net_value_base_m_cad=net_base - baseline_net_base,
        delta_net_value_high_m_cad=net_high - baseline_net_high,
        lp_optimum_m_cad=solved.lp_optimum_m_cad,
        achieved_m_cad=solved.achieved_m_cad,
        optimality_gap_m_cad=solved.optimality_gap_m_cad,
        binding_provinces=tuple(row.province for row in rows if row.binding),
        baseline_binding_provinces=baseline.binding_provinces,
    )


def _canonical_path(cap_case: str, level: str) -> Path | None:
    if cap_case == "documented_ceiling":
        return CANONICAL_ALLOCATION_DIR / f"{level}.csv"
    intensity = CAP_INTENSITY[cap_case]
    if intensity is None:
        return None
    return CANONICAL_ALLOCATION_DIR / f"intensity_{intensity:.1f}" / f"{level}.csv"


def _check_canonical_baselines(
    baselines: Mapping[str, Mapping[str, baseline_model.CaseResult]],
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    for cap_case in CAP_CASES:
        for level in LEVELS:
            result = baselines[cap_case][level]
            path = _canonical_path(cap_case, level)
            exists = path is not None and path.is_file()
            if result.is_optimal:
                if not exists:
                    _fail(
                        f"canonical baseline is missing for feasible "
                        f"{cap_case}/{level}: {path}"
                    )
                rows = _read_csv(path)
                canonical = {
                    row["province"]: _finite_float(
                        row.get("harvest_m3"),
                        field="harvest_m3",
                        context=str(path),
                    )
                    for row in rows
                }
                diffs = {
                    row.province: abs(row.harvest_m3 - canonical[row.province])
                    for row in result.rows
                }
                max_diff = max(diffs.values())
                if max_diff > CANONICAL_MATCH_TOLERANCE_M3:
                    worst = max(diffs, key=diffs.get)
                    _fail(
                        f"canonical baseline {cap_case}/{level} is not "
                        f"reproduced: {worst} differs by {max_diff:,.3f} m3"
                    )
                checks.append(
                    {
                        "cap_case": cap_case,
                        "level": level,
                        "status": "passed",
                        "baseline_status": result.status,
                        "canonical_path": _display_path(path),
                        "canonical_rows": len(rows),
                        "max_abs_harvest_diff_m3": _round(max_diff, 6),
                    }
                )
                continue
            if exists:
                _fail(
                    f"canonical baseline exists for infeasible "
                    f"{cap_case}/{level}: {path}"
                )
            checks.append(
                {
                    "cap_case": cap_case,
                    "level": level,
                    "status": "passed",
                    "baseline_status": result.status,
                    "canonical_path": None,
                    "canonical_rows": 0,
                    "max_abs_harvest_diff_m3": None,
                }
            )
    return checks


def _case_payload(case: NetValueCase) -> dict[str, Any]:
    return {
        "scenario": case.scenario,
        "scenario_role": SCENARIO_ROLE[case.scenario],
        "cap_case": case.cap_case,
        "cap_case_role": CAP_CASE_ROLE[case.cap_case],
        "level": case.level,
        "status": case.status,
        "feasible": case.is_optimal,
        "national_total_m3": _round(case.national_total_m3, 6),
        "capacity_m3": _round(case.capacity_m3, 6),
        "deficit_m3": _round(case.deficit_m3, 6),
        "harvest_sum_m3": _round(case.harvest_sum_m3, 6) if case.is_optimal else None,
        "baseline_status": case.baseline_status,
        "baseline_harvest_sum_m3": case.baseline_harvest_sum_m3,
        "baseline_l1_deviation_m3": case.baseline_l1_deviation_m3,
        "margin_case_optimized": OPTIMIZATION_MARGIN_CASE,
        "net_value_low_m_cad": _round(case.net_value_low_m_cad, 6)
        if case.net_value_low_m_cad is not None
        else None,
        "net_value_base_m_cad": _round(case.net_value_base_m_cad, 6)
        if case.net_value_base_m_cad is not None
        else None,
        "net_value_high_m_cad": _round(case.net_value_high_m_cad, 6)
        if case.net_value_high_m_cad is not None
        else None,
        "baseline_net_value_low_m_cad": _round(case.baseline_net_value_low_m_cad, 6)
        if case.baseline_net_value_low_m_cad is not None
        else None,
        "baseline_net_value_base_m_cad": _round(case.baseline_net_value_base_m_cad, 6)
        if case.baseline_net_value_base_m_cad is not None
        else None,
        "baseline_net_value_high_m_cad": _round(case.baseline_net_value_high_m_cad, 6)
        if case.baseline_net_value_high_m_cad is not None
        else None,
        "delta_net_value_low_m_cad": _round(case.delta_net_value_low_m_cad, 6)
        if case.delta_net_value_low_m_cad is not None
        else None,
        "delta_net_value_base_m_cad": _round(case.delta_net_value_base_m_cad, 6)
        if case.delta_net_value_base_m_cad is not None
        else None,
        "delta_net_value_high_m_cad": _round(case.delta_net_value_high_m_cad, 6)
        if case.delta_net_value_high_m_cad is not None
        else None,
        "lp_optimum_m_cad": _round(case.lp_optimum_m_cad, 9)
        if case.lp_optimum_m_cad is not None
        else None,
        "achieved_m_cad": _round(case.achieved_m_cad, 9)
        if case.achieved_m_cad is not None
        else None,
        "optimality_gap_m_cad": _round(case.optimality_gap_m_cad, 12)
        if case.optimality_gap_m_cad is not None
        else None,
        "binding_provinces": list(case.binding_provinces),
        "baseline_binding_provinces": list(case.baseline_binding_provinces),
        "gaining_provinces": list(case.gaining_provinces),
        "losing_provinces": list(case.losing_provinces),
        "unchanged_provinces": list(case.unchanged_provinces),
        "max_gain_m3": _round(
            max(
                (row.delta_m3 for row in case.rows if row.delta_m3 > 0.0),
                default=0.0,
            ),
            6,
        ),
        "max_loss_m3": _round(
            max(
                (-row.delta_m3 for row in case.rows if row.delta_m3 < 0.0),
                default=0.0,
            ),
            6,
        ),
    }


def _allocation_rows(cases: Sequence[NetValueCase]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case in cases:
        if not case.is_optimal:
            continue
        for row in case.rows:
            rows.append(
                {
                    "scenario": case.scenario,
                    "scenario_role": SCENARIO_ROLE[case.scenario],
                    "cap_case": case.cap_case,
                    "cap_case_role": CAP_CASE_ROLE[case.cap_case],
                    "level": case.level,
                    "national_total_m3": _round(case.national_total_m3, 6),
                    "margin_case_optimized": OPTIMIZATION_MARGIN_CASE,
                    "province": row.province,
                    "province_name": PROVINCE_NAMES[row.province],
                    "margin_low_cad_m3": _round(row.margin_low_cad_m3, 6),
                    "margin_base_cad_m3": _round(row.margin_base_cad_m3, 6),
                    "margin_high_cad_m3": _round(row.margin_high_cad_m3, 6),
                    "observed_share": _round(row.observed_share, 9),
                    "baseline_harvest_m3": _round(row.baseline_harvest_m3, 6),
                    "baseline_share": _round(row.baseline_share, 9),
                    "optimized_harvest_m3": _round(row.optimized_harvest_m3, 6),
                    "optimized_share": _round(row.optimized_share, 9),
                    "delta_m3": _round(row.delta_m3, 6),
                    "delta_share": _round(row.delta_share, 9),
                    "delta_share_pp": _round(row.delta_share * 100.0, 9),
                    "net_value_low_m_cad": _round(row.net_value_low_m_cad, 6),
                    "net_value_base_m_cad": _round(row.net_value_base_m_cad, 6),
                    "net_value_high_m_cad": _round(row.net_value_high_m_cad, 6),
                    "baseline_net_value_low_m_cad": _round(
                        row.baseline_net_value_low_m_cad, 6
                    ),
                    "baseline_net_value_base_m_cad": _round(
                        row.baseline_net_value_base_m_cad, 6
                    ),
                    "baseline_net_value_high_m_cad": _round(
                        row.baseline_net_value_high_m_cad, 6
                    ),
                    "delta_net_value_base_m_cad": _round(
                        row.delta_net_value_base_m_cad, 6
                    ),
                    "upper_bound_m3": _round(row.upper_bound_m3, 6),
                    "binding": row.binding,
                    "baseline_binding": row.baseline_binding,
                    "direction": row.direction,
                    "margin_status": row.margin_status,
                    "price_basis": row.price_basis,
                    "cost_basis": row.cost_basis,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "lp_objective": LP_OBJECTIVE,
                    "canonical_changed": CANONICAL_CHANGED,
                    "donor_price_caveat": DONOR_PRICE_CAVEAT,
                }
            )
    return rows


def _delta_rows(cases: Sequence[NetValueCase]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for case in cases:
        if not case.is_optimal:
            continue
        for row in case.rows:
            rows.append(
                {
                    "scenario": case.scenario,
                    "scenario_role": SCENARIO_ROLE[case.scenario],
                    "cap_case": case.cap_case,
                    "level": case.level,
                    "province": row.province,
                    "province_name": PROVINCE_NAMES[row.province],
                    "margin_base_cad_m3": _round(row.margin_base_cad_m3, 6),
                    "baseline_harvest_m3": _round(row.baseline_harvest_m3, 6),
                    "optimized_harvest_m3": _round(row.optimized_harvest_m3, 6),
                    "delta_m3": _round(row.delta_m3, 6),
                    "baseline_share": _round(row.baseline_share, 9),
                    "optimized_share": _round(row.optimized_share, 9),
                    "delta_share": _round(row.delta_share, 9),
                    "delta_share_pp": _round(row.delta_share * 100.0, 9),
                    "direction": row.direction,
                    "upper_bound_m3": _round(row.upper_bound_m3, 6),
                    "binding": row.binding,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "lp_objective": LP_OBJECTIVE,
                    "canonical_changed": CANONICAL_CHANGED,
                    "donor_price_caveat": DONOR_PRICE_CAVEAT,
                }
            )
    return rows


def _validate_cases(cases: Sequence[NetValueCase]) -> dict[str, object]:
    feasible = 0
    infeasible = 0
    negative_margin_rows = 0
    negative_margin_allocated_rows = 0
    for case in cases:
        context = f"{case.scenario}/{case.cap_case}/{case.level}"
        if case.national_total_m3 <= 0.0:
            _fail(f"{context}: non-positive national total")
        if not case.is_optimal:
            infeasible += 1
            if case.rows:
                _fail(f"{context}: infeasible case has allocation rows")
            expected_deficit = max(0.0, case.national_total_m3 - case.capacity_m3)
            if abs(case.deficit_m3 - expected_deficit) > CAP_TOLERANCE_M3:
                _fail(f"{context}: inconsistent infeasibility deficit")
            continue
        feasible += 1
        harvest_sum = case.harvest_sum_m3
        if abs(harvest_sum - case.national_total_m3) > TOTAL_TOLERANCE_M3:
            _fail(
                f"{context}: harvest sum {harvest_sum:,.3f} does not match "
                f"total {case.national_total_m3:,.3f}"
            )
        share_sum = sum(row.optimized_share for row in case.rows)
        if abs(share_sum - 1.0) > SHARE_TOLERANCE:
            _fail(f"{context}: optimized shares sum to {share_sum:.12f}")
        baseline_share_sum = sum(row.baseline_share for row in case.rows)
        if abs(baseline_share_sum - 1.0) > SHARE_TOLERANCE:
            _fail(f"{context}: baseline shares sum to {baseline_share_sum:.12f}")
        net_sum = sum(row.net_value_base_m_cad for row in case.rows)
        if abs(net_sum - (case.net_value_base_m_cad or 0.0)) > 0.01:
            _fail(f"{context}: net-value arithmetic does not reconcile")
        if (
            case.achieved_m_cad is None
            or case.lp_optimum_m_cad is None
            or case.optimality_gap_m_cad is None
        ):
            _fail(f"{context}: feasible case is missing objective values")
        if abs(case.achieved_m_cad - case.net_value_base_m_cad) > 0.01:
            _fail(f"{context}: achieved objective differs from row arithmetic")
        if case.optimality_gap_m_cad > NET_VALUE_TOLERANCE_M_CAD:
            _fail(f"{context}: net-value optimality gap is too large")
        if (
            abs(
                (case.delta_net_value_base_m_cad or 0.0)
                - (
                    (case.net_value_base_m_cad or 0.0)
                    - (case.baseline_net_value_base_m_cad or 0.0)
                )
            )
            > 0.01
        ):
            _fail(f"{context}: net-value delta does not reconcile")
        for row in case.rows:
            if row.optimized_harvest_m3 < -CAP_TOLERANCE_M3:
                _fail(f"{context}/{row.province}: negative optimized harvest")
            if row.optimized_harvest_m3 > row.upper_bound_m3 + CAP_TOLERANCE_M3:
                _fail(f"{context}/{row.province}: cap exceeded")
            if (
                abs(row.optimized_harvest_m3 - (row.baseline_harvest_m3 + row.delta_m3))
                > 0.01
            ):
                _fail(f"{context}/{row.province}: delta arithmetic mismatch")
            if (
                abs(
                    row.net_value_base_m_cad
                    - row.optimized_harvest_m3 * row.margin_base_cad_m3 / 1e6
                )
                > 0.01
            ):
                _fail(f"{context}/{row.province}: net-value arithmetic mismatch")
            if row.margin_base_cad_m3 < 0.0:
                negative_margin_rows += 1
                if row.optimized_harvest_m3 > 0.0:
                    negative_margin_allocated_rows += 1
        if case.binding_provinces != tuple(
            row.province for row in case.rows if row.binding
        ):
            _fail(f"{context}: binding-province list does not reconcile")
    if negative_margin_rows == 0:
        _fail("expected negative base-margin rows; none were found")
    if negative_margin_allocated_rows == 0:
        _fail(
            "expected at least one negative-margin province to receive volume "
            "when positive-margin capacity is insufficient"
        )
    return {
        "case_count": len(cases),
        "feasible_case_count": feasible,
        "infeasible_case_count": infeasible,
        "negative_base_margin_rows": negative_margin_rows,
        "negative_base_margin_rows_with_positive_allocation": (
            negative_margin_allocated_rows
        ),
        "negative_margins_clipped": False,
        "baseline_preserved": True,
        "canonical_changed": False,
    }


def _build_summary(
    cases: Sequence[NetValueCase],
    baseline_checks: Sequence[Mapping[str, object]],
    validation: Mapping[str, object],
    generated_utc: str,
    canonical_unchanged: bool,
) -> dict[str, Any]:
    case_payloads = [_case_payload(case) for case in cases]
    by_key = {(case.scenario, case.cap_case, case.level): case for case in cases}
    documented = [
        by_key[(scenario, "documented_ceiling", level)]
        for scenario in SCENARIOS
        for level in LEVELS
    ]
    scenario_payloads: dict[str, Any] = {}
    for scenario in SCENARIOS:
        scenario_cases = [case for case in cases if case.scenario == scenario]
        scenario_payloads[scenario] = {
            "role": SCENARIO_ROLE[scenario],
            "case_count": len(scenario_cases),
            "feasible_case_count": sum(1 for case in scenario_cases if case.is_optimal),
            "documented_ceiling": {
                level: _case_payload(by_key[(scenario, "documented_ceiling", level)])
                for level in LEVELS
            },
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": (
            "model_estimate screening sensitivity; not observed, not policy "
            "grade, and not a canonical allocation change"
        ),
        "source_economics_model_id": SOURCE_ECONOMICS_MODEL_ID,
        "source_allocation_model_id": SOURCE_ALLOCATION_MODEL_ID,
        "labels": {
            "evidence": EVIDENCE,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "lp_objective": LP_OBJECTIVE,
            "canonical_changed": CANONICAL_CHANGED,
            "baseline_objective": BASELINE_OBJECTIVE,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            "public_osecan_input": PUBLIC_OSECAN_INPUT,
            "restricted_data_used": False,
        },
        "objective": {
            "optimized": (
                "maximize sum_p harvest_p * margin_base_p over provincial "
                "harvest within documented or intensity caps"
            ),
            "constraints": [
                "sum_p harvest_p = national_total",
                "0 <= harvest_p <= upper_bound_p",
                "upper_bound_p = documented cap or min(documented cap, I * operable_proxy_ha)",
            ],
            "tie_break": (
                "none required: base-margin unit values are distinct across "
                "provinces in each scenario, so the optimum is unique"
            ),
            "margin_case_optimized": OPTIMIZATION_MARGIN_CASE,
            "low_high_reporting": (
                "the central-margin allocation is re-priced at the v2.3 low and "
                "high margins; low/high are uncertainty bounds, not separate optima"
            ),
            "solver": {
                "name": "PuLP",
                "version": pulp.__version__,
                "backend": "CBC (bundled)",
                "threads": 1,
            },
        },
        "coverage": {
            "scenarios": list(SCENARIOS),
            "levels": list(LEVELS),
            "cap_cases": list(CAP_CASES),
            "margin_cases_reported": list(MARGIN_CASES),
            "province_count": len(PROVINCE_ORDER),
            "case_count": len(cases),
            "feasible_case_count": sum(1 for case in cases if case.is_optimal),
            "infeasible_case_count": sum(1 for case in cases if not case.is_optimal),
            "documented_ceiling_case_count": len(documented),
        },
        "scenarios": scenario_payloads,
        "cases": case_payloads,
        "national_totals": [
            {
                key: payload[key]
                for key in (
                    "scenario",
                    "scenario_role",
                    "cap_case",
                    "level",
                    "status",
                    "feasible",
                    "national_total_m3",
                    "capacity_m3",
                    "deficit_m3",
                    "harvest_sum_m3",
                    "net_value_low_m_cad",
                    "net_value_base_m_cad",
                    "net_value_high_m_cad",
                    "baseline_net_value_low_m_cad",
                    "baseline_net_value_base_m_cad",
                    "baseline_net_value_high_m_cad",
                    "delta_net_value_low_m_cad",
                    "delta_net_value_base_m_cad",
                    "delta_net_value_high_m_cad",
                    "binding_provinces",
                    "gaining_provinces",
                    "losing_provinces",
                )
            }
            for payload in case_payloads
        ],
        "baseline": {
            "objective": BASELINE_OBJECTIVE,
            "canonical_directory": _display_path(CANONICAL_ALLOCATION_DIR),
            "canonical_changed": not canonical_unchanged,
            "checks": [dict(check) for check in baseline_checks],
            "note": (
                "The canonical least-deviation outputs were re-solved in memory "
                "and compared to the committed CSVs. They were not overwritten."
            ),
        },
        "validation": {
            **dict(validation),
            "canonical_snapshot_unchanged": canonical_unchanged,
            "unexpected_d_cells": 0,
            "all_values_model_estimate": True,
            "donor_price_caveat_present": True,
        },
        "caveats": list(CAVEATS),
        "non_claims": list(NON_CLAIMS),
    }


def _table(cells: Sequence[object]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _format_m(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _format_m3(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value / 1e6:,.{digits}f}"


def _report(cases: Sequence[NetValueCase], generated_utc: str) -> str:
    by_key = {(case.scenario, case.cap_case, case.level): case for case in cases}
    lines = [
        "# Provincial allocation net-value LP sensitivity",
        "",
        (
            "**Status: model estimate, screening sensitivity, not policy grade. "
            "The canonical least-deviation allocation is unchanged.**"
        ),
        "",
        f"Generated: {generated_utc}",
        "",
        (
            "Generated by `workflows/national/harvest/allocation_net_value_lp.py` "
            "using PuLP/CBC and the `national_economics_v2_3` province margins."
        ),
        "",
        "## Method",
        "",
        (
            "The canonical allocation remains the least-deviation allocation from "
            "observed 2024 provincial shares. This sensitivity keeps that baseline "
            "and solves a separate LP for each price scenario and national ceiling:"
        ),
        "",
        "```text",
        "maximize  sum_p h_p * margin_base_p",
        "subject to sum_p h_p = T",
        "           0 <= h_p <= upper_bound_p",
        "```",
        "",
        (
            "The documented-cap cases use the canonical documented ceilings. The "
            "intensity cases use the canonical operable-proxy screen "
            "`upper_bound_p = min(documented cap, I * operable_proxy_ha)`. Base-margin "
            "unit values are distinct across provinces in each scenario, so the "
            "net-value optimum is unique; no secondary tie-break is needed. The "
            "allocation is optimized on the central (`base`) margin; the same "
            "allocation is then re-priced at the low and high margins. Low/high are "
            "uncertainty bounds, not separately optimized allocations."
        ),
        "",
        "## Labels",
        "",
        "- `evidence=model_estimate`",
        "- `observed=false`",
        "- `policy_grade=false`",
        "- `lp_objective=net_value_sensitivity`",
        "- `canonical_changed=false`",
        "- " + DONOR_PRICE_CAVEAT,
        "",
        "## Documented-ceiling results",
        "",
        (
            "Net value is million nominal CAD; it is unit margin times harvest, not "
            "observed profit or a discounted value."
        ),
        "",
        _table(
            (
                "Scenario",
                "Ceiling",
                "Status",
                "Harvest (Mm3)",
                "Net value low",
                "Net value base",
                "Net value high",
                "Baseline base",
                "Delta base",
                "Binding provinces",
            )
        ),
        _table(
            ("---", "---", "---", "---:", "---:", "---:", "---:", "---:", "---:", "---")
        ),
    ]
    for scenario in SCENARIOS:
        for level in LEVELS:
            case = by_key[(scenario, "documented_ceiling", level)]
            if case.is_optimal:
                lines.append(
                    _table(
                        (
                            scenario,
                            level,
                            "optimal",
                            f"{case.harvest_sum_m3 / 1e6:,.3f}",
                            _format_m(case.net_value_low_m_cad),
                            _format_m(case.net_value_base_m_cad),
                            _format_m(case.net_value_high_m_cad),
                            _format_m(case.baseline_net_value_base_m_cad),
                            _format_m(case.delta_net_value_base_m_cad),
                            ", ".join(case.binding_provinces) or "none",
                        )
                    )
                )
            else:
                lines.append(
                    _table(
                        (
                            scenario,
                            level,
                            "infeasible",
                            "—",
                            "—",
                            "—",
                            "—",
                            "—",
                            "—",
                            f"deficit {case.deficit_m3 / 1e6:,.3f} Mm3",
                        )
                    )
                )

    lines.extend(["", "## Provincial shifts", ""])
    for scenario in SCENARIOS:
        lines.extend(
            [
                f"### {scenario} ({SCENARIO_ROLE[scenario]})",
                "",
                "| Ceiling | Province | Baseline (Mm3) | Optimized (Mm3) | Delta (Mm3) | Baseline share | Optimized share | Delta (pp) | Margin (CAD/m3) | Cap (Mm3) | Binding |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
            ]
        )
        for level in LEVELS:
            case = by_key[(scenario, "documented_ceiling", level)]
            if not case.is_optimal:
                continue
            for row in case.rows:
                if row.direction == "unchanged" and abs(row.delta_m3) < 1.0:
                    continue
                lines.append(
                    _table(
                        (
                            level,
                            row.province,
                            f"{row.baseline_harvest_m3 / 1e6:,.3f}",
                            f"{row.optimized_harvest_m3 / 1e6:,.3f}",
                            f"{row.delta_m3 / 1e6:+,.3f}",
                            f"{row.baseline_share:.3%}",
                            f"{row.optimized_share:.3%}",
                            f"{row.delta_share * 100.0:+.3f}",
                            f"{row.margin_base_cad_m3:,.3f}",
                            f"{row.upper_bound_m3 / 1e6:,.3f}",
                            "yes" if row.binding else "",
                        )
                    )
                )
        lines.append("")
        for level in LEVELS:
            case = by_key[(scenario, "documented_ceiling", level)]
            if not case.is_optimal:
                continue
            lines.append(
                f"- **{level}**: gains {', '.join(case.gaining_provinces) or 'none'}; "
                f"losses {', '.join(case.losing_provinces) or 'none'}; "
                f"binding {', '.join(case.binding_provinces) or 'none'}."
            )
        lines.append("")

    lines.extend(
        [
            "## Intensity screen",
            "",
            "| Scenario | Ceiling | Intensity | Status | Harvest (Mm3) | Capacity (Mm3) | Deficit (Mm3) | Binding |",
            "|---|---|---:|---|---:|---:|---:|---|",
        ]
    )
    for scenario in SCENARIOS:
        for cap_case in ("intensity_1.0", "intensity_1.2"):
            for level in LEVELS:
                case = by_key[(scenario, cap_case, level)]
                lines.append(
                    _table(
                        (
                            scenario,
                            level,
                            f"{CAP_INTENSITY[cap_case]:.1f}",
                            case.status,
                            f"{case.harvest_sum_m3 / 1e6:,.3f}"
                            if case.is_optimal
                            else "—",
                            f"{case.capacity_m3 / 1e6:,.3f}",
                            f"{case.deficit_m3 / 1e6:,.3f}"
                            if case.deficit_m3 > 0.0
                            else "—",
                            ", ".join(case.binding_provinces) or "—",
                        )
                    )
                )

    lines.extend(
        [
            "",
            (
                "The intensity cases are landbase screens, not legal THLB constraints. "
                "PE and NU have no operable proxy and retain their documented caps "
                "with `operable_missing`."
            ),
            "",
            "## Net value totals",
            "",
            "| Scenario | Cap case | Ceiling | Net value low (M CAD) | Net value base (M CAD) | Net value high (M CAD) | Baseline base (M CAD) | Delta base (M CAD) |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for scenario in SCENARIOS:
        for cap_case in CAP_CASES:
            for level in LEVELS:
                case = by_key[(scenario, cap_case, level)]
                lines.append(
                    _table(
                        (
                            scenario,
                            cap_case,
                            level,
                            _format_m(case.net_value_low_m_cad),
                            _format_m(case.net_value_base_m_cad),
                            _format_m(case.net_value_high_m_cad),
                            _format_m(case.baseline_net_value_base_m_cad),
                            _format_m(case.delta_net_value_base_m_cad),
                        )
                    )
                )

    lines.extend(
        [
            "",
            (
                "Negative margins are retained unclipped. Where the positive-margin "
                "provinces cannot absorb the national total, the LP allocates the "
                "remainder to the least-negative margins; the low net-value columns "
                "can therefore be negative even when the central total is positive."
            ),
            "",
            "## Binding caps and infeasibility",
            "",
            (
                "- The documented-cap envelope is reported in the canonical "
                "allocation summary; 100M, 119M, and 150M are feasible at the "
                "documented ceilings."
            ),
            (
                "- The 150M intensity cases are infeasible under the per-province "
                "min-cap formula. No allocation is forced; the deficit is reported."
            ),
            "- Binding provinces are those within 1 m3 of their effective cap.",
            "",
            "## Baseline preservation",
            "",
            (
                "The canonical 100M, 119M, 150M, and intensity_1.0/intensity_1.2 "
                "CSVs were re-solved in memory and compared to the committed files. "
                "The canonical directory was hashed before and after this build and "
                "was not modified. `canonical_changed=false`."
            ),
            "",
            "## Caveats",
            "",
            *[f"- {caveat}" for caveat in CAVEATS],
            "",
            "## Non-claims",
            "",
            *[f"- {claim}" for claim in NON_CLAIMS],
            "",
            "## Outputs",
            "",
            (
                "- `allocation_net_value_lp.csv`: per-province optimized and baseline "
                "allocations, margins, net-value contributions, and labels."
            ),
            (
                "- `allocation_delta.csv`: province-level gains and losses versus the "
                "baseline."
            ),
            (
                "- `summary.json`: national totals, case statuses, binding sets, "
                "baseline checks, labels, and caveats."
            ),
            "- `manifest.json`: input/output hashes, commits, and validation.",
            "",
            "## Reproduction",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.harvest.allocation_net_value_lp",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _input_records(
    paths: Sequence[tuple[Path, str]],
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for path, role in paths:
        _assert_input_path(path)
        records.append(
            {
                "path": _display_path(path),
                "role": role,
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    return records


def _write_manifest(
    out_dir: Path,
    summary: Mapping[str, Any],
    validation: Mapping[str, Any],
    input_records: Sequence[Mapping[str, object]],
    generated_utc: str,
) -> None:
    outputs = (ALLOCATION_CSV, ALLOCATION_DELTA_CSV, SUMMARY_JSON, REPORT_MD)
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_utc": generated_utc,
        "parent_commit": _git_commit(REPO_ROOT),
        "data_commit": _git_commit(REPO_ROOT / "data"),
        "observation_type": EVIDENCE,
        "labels": dict(summary["labels"]),
        "status": summary["status"],
        "objective": summary["objective"],
        "inputs": [dict(record) for record in input_records],
        "outputs": [
            {
                "path": name,
                "sha256": _sha256(out_dir / name),
                "bytes": (out_dir / name).stat().st_size,
            }
            for name in outputs
        ],
        "validation": {
            **dict(validation),
            "canonical_changed": False,
            "canonical_snapshot_unchanged": True,
            "unexpected_d_cells": 0,
            "all_values_model_estimate": True,
            "donor_price_caveat_present": True,
            "public_osecan_input": False,
        },
        "caveats": list(CAVEATS),
        "non_claims": list(NON_CLAIMS),
    }
    _write_json(out_dir / MANIFEST_JSON, manifest)


def build(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    check_deletions: bool = True,
) -> dict[str, Any]:
    """Build the net-value allocation sensitivity bundle and return its summary."""

    _assert_output_path_allowed(out_dir)
    if check_deletions:
        assert_no_unexpected_deletions()

    canonical_before = _snapshot_tree(CANONICAL_ALLOCATION_DIR)
    base_basis = baseline_model.load_province_basis()
    margins = load_margins(base_basis)
    cap_bases = {cap_case: _cap_basis(base_basis, cap_case) for cap_case in CAP_CASES}
    baselines: dict[str, dict[str, baseline_model.CaseResult]] = {}
    for cap_case in CAP_CASES:
        baselines[cap_case] = {
            level: baseline_model.solve_allocation(
                cap_bases[cap_case],
                LEVEL_TOTALS_M3[level],
                label=f"baseline_{cap_case}_{level}",
            )
            for level in LEVELS
        }
    baseline_checks = _check_canonical_baselines(baselines)

    cases: list[NetValueCase] = []
    for scenario in SCENARIOS:
        for cap_case in CAP_CASES:
            for level in LEVELS:
                cases.append(
                    _solve_case(
                        cap_bases[cap_case],
                        baselines[cap_case][level],
                        margins[scenario],
                        scenario=scenario,
                        cap_case=cap_case,
                        level=level,
                    )
                )

    validation = _validate_cases(cases)
    canonical_after = _snapshot_tree(CANONICAL_ALLOCATION_DIR)
    canonical_unchanged = canonical_before == canonical_after
    if not canonical_unchanged:
        changed = sorted(set(canonical_before) | set(canonical_after))
        _fail(
            "canonical allocation directory changed during build: "
            + ", ".join(
                path
                for path in changed
                if canonical_before.get(path) != canonical_after.get(path)
            )
        )

    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    summary = _build_summary(
        cases, baseline_checks, validation, generated_utc, canonical_unchanged
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    allocation_rows = _allocation_rows(cases)
    delta_rows = _delta_rows(cases)
    _write_csv(out_dir / ALLOCATION_CSV, allocation_rows, ALLOCATION_FIELDS)
    _write_csv(out_dir / ALLOCATION_DELTA_CSV, delta_rows, DELTA_FIELDS)
    _write_json(out_dir / SUMMARY_JSON, summary)
    (out_dir / REPORT_MD).write_text(_report(cases, generated_utc), encoding="utf-8")

    input_paths: list[tuple[Path, str]] = [
        (baseline_model.TEMPLATE_PATH, "harvest_basis_template"),
        (baseline_model.PROVINCES_PATH, "province_metadata"),
        (baseline_model.MANAGED_LANDBASE_PATH, "managed_landbase_proxy"),
        (baseline_model.OPERABLE_PROXY_PATH, "operable_proxy"),
        (baseline_model.OPERABLE_PROXY_SUMMARY_PATH, "operable_proxy_summary"),
        (baseline_model.FEASIBILITY_DOC_PATH, "aac_feasibility_note"),
        (BASELINE_SOURCE_PATH, "canonical_allocation_lp_source"),
        (MARGIN_SUMMARY_PATH, "national_economics_v2_3_margin_summary"),
        (MARGIN_SUMMARY_JSON_PATH, "national_economics_v2_3_summary"),
        (MARGIN_MANIFEST_PATH, "national_economics_v2_3_manifest"),
    ]
    for cap_case in CAP_CASES:
        for level in LEVELS:
            path = _canonical_path(cap_case, level)
            if path is not None and path.is_file():
                input_paths.append((path, f"canonical_baseline_{cap_case}_{level}"))

    input_records = _input_records(input_paths)
    _write_manifest(out_dir, summary, validation, input_records, generated_utc)
    if check_deletions:
        assert_no_unexpected_deletions()
    return summary


ALLOCATION_FIELDS = (
    "scenario",
    "scenario_role",
    "cap_case",
    "cap_case_role",
    "level",
    "national_total_m3",
    "margin_case_optimized",
    "province",
    "province_name",
    "margin_low_cad_m3",
    "margin_base_cad_m3",
    "margin_high_cad_m3",
    "observed_share",
    "baseline_harvest_m3",
    "baseline_share",
    "optimized_harvest_m3",
    "optimized_share",
    "delta_m3",
    "delta_share",
    "delta_share_pp",
    "net_value_low_m_cad",
    "net_value_base_m_cad",
    "net_value_high_m_cad",
    "baseline_net_value_low_m_cad",
    "baseline_net_value_base_m_cad",
    "baseline_net_value_high_m_cad",
    "delta_net_value_base_m_cad",
    "upper_bound_m3",
    "binding",
    "baseline_binding",
    "direction",
    "margin_status",
    "price_basis",
    "cost_basis",
    "evidence",
    "observed",
    "policy_grade",
    "lp_objective",
    "canonical_changed",
    "donor_price_caveat",
)

DELTA_FIELDS = (
    "scenario",
    "scenario_role",
    "cap_case",
    "level",
    "province",
    "province_name",
    "margin_base_cad_m3",
    "baseline_harvest_m3",
    "optimized_harvest_m3",
    "delta_m3",
    "baseline_share",
    "optimized_share",
    "delta_share",
    "delta_share_pp",
    "direction",
    "upper_bound_m3",
    "binding",
    "evidence",
    "observed",
    "policy_grade",
    "lp_objective",
    "canonical_changed",
    "donor_price_caveat",
)


def main() -> int:
    summary = build()
    for scenario in SCENARIOS:
        for level in LEVELS:
            case = summary["scenarios"][scenario]["documented_ceiling"][level]
            print(
                f"{scenario} {level}: {case['status']} "
                f"net_value_base={case['net_value_base_m_cad']} M CAD "
                f"binding={','.join(case['binding_provinces']) or '-'}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
