"""Forest LP net-value sensitivity using national margin totals.

This module joins the Forest LP v1 allocation rows to the committed national
province margin totals and reports a volume-weighted net-value sensitivity for
two margin scenarios:

* ``donor_flat``: the base margin scenario;
* ``stumpage_scaled``: the stumpage-scaled sensitivity scenario.

The arithmetic is deliberately simple and report-only::

    net_value_m_cad = allocated_harvest_m3 * margin_cad_m3 / 1_000_000

Net value is never an LP objective, never a constraint, and never written back
to the Forest LP v1 artifacts.  Infeasible allocation rows keep blank
allocation and blank net-value fields.  Negative values are retained without
clipping.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.forest_lp_net_value
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ALLOCATION_CSV",
    "ARTIFACT_VERSION",
    "CAP_CASES",
    "DEFAULT_OUT_DIR",
    "FOREST_LP_NET_VALUE_REPORT",
    "LEVELS",
    "MANIFEST_JSON",
    "MODEL_ID",
    "NET_VALUE_STATUS",
    "PROVINCES",
    "SCENARIOS",
    "SUMMARY_JSON",
    "ForestLPNetValueError",
    "assert_no_unexpected_d",
    "build",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
ALLOCATION_CSV = REPO_ROOT / "out/forest_lp_v1/forest_lp_allocation.csv"
FOREST_LP_V1_DIR = REPO_ROOT / "out/forest_lp_v1"
FOREST_LP_V1_SOURCE = REPO_ROOT / "workflows/national/harvest/forest_lp_v1.py"

MARGIN_DIR = HARVEST_BASIS_DIR / "national_margin_totals"
MARGIN_CSV = MARGIN_DIR / "province_margin_totals.csv"
MARGIN_JSON = MARGIN_DIR / "national_margin_totals.json"

V23_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"
V23_SUMMARY_JSON = V23_DIR / "summary.json"
V23_TIER_CSV = V23_DIR / "tier_coverage.csv"
V23_MARGIN_SUMMARY_CSV = V23_DIR / "national_margin_summary.csv"

DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "forest_lp_net_value_sensitivity"

ALLOCATION_NET_VALUE_CSV = "allocation_net_value.csv"
ALLOCATION_SUMMARY_CSV = "allocation_net_value_summary.csv"
NATIONAL_SUMMARY_CSV = "national_net_value_summary.csv"
SUMMARY_JSON = "summary.json"
FOREST_LP_NET_VALUE_REPORT = "FOREST_LP_NET_VALUE_REPORT.md"
MANIFEST_JSON = "manifest.json"

MODEL_ID = "forest_lp_net_value_sensitivity_v1"
ARTIFACT_VERSION = "1.0.0"
SCHEMA_VERSION = "national/forest-lp-net-value-sensitivity/1"
SOURCE_MARGIN_MODEL_ID = "national_margin_totals_v1"
SOURCE_ECONOMICS_MODEL_ID = "national_economics_v2_3"
SOURCE_PUBLICATION_CONTEXT = "national-economics-v2.4"

EVIDENCE = "model_estimate"
NET_VALUE_STATUS = "model_estimate_not_policy"
LP_OBJECTIVE = "not_used"
OBSERVED = False
POLICY_GRADE = False
COMBINED_NATIONAL_NET_VALUE = False

PROVINCES = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "NL",
    "PE",
    "YT",
    "NT",
    "NU",
)
PROVINCE_INDEX = {province: index for index, province in enumerate(PROVINCES)}
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

LEVELS = ("100m", "119m", "150m")
LEVEL_TARGETS_M3 = {
    "100m": 100_000_000.0,
    "119m": 118_977_339.0,
    "150m": 150_000_000.0,
}
CAP_CASES = ("documented_ceiling", "intensity_1.0", "intensity_1.2")
SCENARIOS = ("donor_flat", "stumpage_scaled")
SCENARIO_ROLE = {
    "donor_flat": "base",
    "stumpage_scaled": "sensitivity",
}
CASES = ("low", "base", "high")

EXPECTED_ALLOCATION_ROWS = 117
EXPECTED_ALLOCATION_GRID = 13 * 3 * 3
EXPECTED_MARGIN_ROWS = 26
EXPECTED_PROVINCE_COUNT = 13
EXPECTED_SCENARIO_COUNT = 2
EXPECTED_GROUP_SUMMARIES = 3 * 3 * 2
EXPECTED_NATIONAL_ROWS = EXPECTED_GROUP_SUMMARIES * 3

ALLOCATION_TOLERANCE_M3 = 2.0
CAP_TOLERANCE_M3 = 1.0
NET_VALUE_TOLERANCE_M_CAD = 1e-9
TOTAL_TOLERANCE_M_CAD = 1e-5
MARGIN_TOLERANCE_CAD_M3 = 1e-6

ALLOWED_INPUT_ROOTS = (
    REPO_ROOT / "data/derived",
    REPO_ROOT / "out",
)
FORBIDDEN_OUTPUT_ROOTS = (
    REPO_ROOT / "ui",
    REPO_ROOT / "ui_publications",
)

PROVINCE_FIELDS = (
    "cap_case",
    "allocation_case",
    "province",
    "province_name",
    "case_status",
    "case_feasible",
    "province_status",
    "allocated_harvest_m3",
    "effective_cap_m3",
    "binding",
    "donor_flat_margin_low_cad_m3",
    "donor_flat_margin_base_cad_m3",
    "donor_flat_margin_high_cad_m3",
    "donor_flat_net_value_low_m_cad",
    "donor_flat_net_value_base_m_cad",
    "donor_flat_net_value_high_m_cad",
    "donor_flat_negative_low",
    "donor_flat_negative_base",
    "donor_flat_negative_high",
    "stumpage_scaled_margin_low_cad_m3",
    "stumpage_scaled_margin_base_cad_m3",
    "stumpage_scaled_margin_high_cad_m3",
    "stumpage_scaled_net_value_low_m_cad",
    "stumpage_scaled_net_value_base_m_cad",
    "stumpage_scaled_net_value_high_m_cad",
    "stumpage_scaled_negative_low",
    "stumpage_scaled_negative_base",
    "stumpage_scaled_negative_high",
    "donor_flat_price_tier",
    "stumpage_scaled_price_tier",
    "cost_tier",
    "evidence_tier",
    "net_value_label",
    "net_value_status",
    "observed",
    "policy_grade",
    "lp_objective",
)

GROUP_FIELDS = (
    "cap_case",
    "allocation_case",
    "allocation_target_m3",
    "scenario",
    "scenario_role",
    "case_status",
    "case_feasible",
    "province_rows",
    "allocated_province_rows",
    "blank_province_rows",
    "allocation_m3",
    "net_value_low_m_cad",
    "net_value_base_m_cad",
    "net_value_high_m_cad",
    "national_margin_low_cad_m3",
    "national_margin_base_cad_m3",
    "national_margin_high_cad_m3",
    "negative_net_value_low_count",
    "negative_net_value_base_count",
    "negative_net_value_high_count",
    "positive_net_value_low_count",
    "positive_net_value_base_count",
    "positive_net_value_high_count",
    "zero_net_value_low_count",
    "zero_net_value_base_count",
    "zero_net_value_high_count",
    "negative_provinces_low",
    "negative_provinces_base",
    "negative_provinces_high",
    "observed",
    "policy_grade",
    "net_value_status",
    "lp_objective",
)

NATIONAL_FIELDS = (
    "scenario",
    "scenario_role",
    "cap_case",
    "allocation_case",
    "case",
    "allocation_m3",
    "net_value_m_cad",
    "province_count",
    "allocated_province_count",
    "blank_province_count",
    "negative_province_count",
    "positive_province_count",
    "zero_province_count",
    "observed",
    "policy_grade",
    "net_value_status",
    "lp_objective",
)

CAVEATS = (
    (
        "Net value is a volume-weighted model estimate: allocation_m3 times "
        "province margin_cad_m3, divided by 1e6. It is not an observed "
        "transaction, a policy result, or a combined national net value."
    ),
    (
        "The Forest LP objective and every Forest LP v1 output are unchanged. "
        "Net value is used only for reporting and is not an LP objective or "
        "constraint."
    ),
    (
        "Negative margins and negative net values are retained without "
        "clipping. A negative value is a model result, not a data error."
    ),
    (
        "Infeasible allocation cases keep blank allocation and blank net-value "
        "fields. They are not clamped, back-filled, or allocated a zero value."
    ),
    (
        "The allocation levels are alternative screening ladders, not additive "
        "years or independent jurisdictions. National totals are reported per "
        "allocation case and must not be summed across levels."
    ),
    (
        "Donor-price and donor-cost provinces remain model estimates. "
        "Stumpage_scaled is a method sensitivity, not the base scenario."
    ),
    (
        "Nunavut has zero allocation volume in the 119m provenance, but a "
        "non-zero unit margin. Its net value is zero by multiplication where "
        "allocated volume is zero and is not an observed zero-profit finding."
    ),
)

NON_CLAIMS = (
    "Not observed; observed=false.",
    "Not policy grade; policy_grade=false.",
    "Not a Forest LP objective or constraint input; lp_objective=not_used.",
    "Not a change to Forest LP v1 outputs or the v1 least-deviation allocation.",
    "Not a combined national net value.",
    "Not a public OSeCAN input and not a UI publication.",
    "Not a jurisdiction ranking and not an investment recommendation.",
)


class ForestLPNetValueError(RuntimeError):
    """Raised when an input cannot support a fail-closed net-value report."""


def _fail(message: str) -> None:
    raise ForestLPNetValueError(message)


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


def _optional_float(value: object, *, field: str, context: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _finite_float(value, field=field, context=context)


def _boolean(value: object, *, field: str, context: str) -> bool:
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    _fail(f"{context}: {field} must be true or false, got {value!r}")
    raise AssertionError("unreachable")


def _optional_boolean(value: object, *, field: str, context: str) -> bool | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _boolean(value, field=field, context=context)


def _round(value: float, digits: int) -> float:
    rounded = round(value, digits)
    return 0.0 if rounded == 0.0 else rounded


def _fmt(value: object, *, digits: int = 6) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
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


def _snapshot_directory(directory: Path) -> dict[str, str]:
    if not directory.is_dir():
        return {}
    return {
        path.name: _sha256(path)
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def _assert_allowed_input_paths(paths: Sequence[Path]) -> None:
    for path in paths:
        resolved = path.resolve()
        if any(part in {"restricted", "private", "credentials"} for part in resolved.parts):
            _fail(f"restricted input path is not allowed: {path}")
        if not any(
            resolved.is_relative_to(root.resolve()) for root in ALLOWED_INPUT_ROOTS
        ):
            _fail(f"input is outside the approved derived/out roots: {path}")


def _assert_output_path_allowed(out_dir: Path) -> None:
    resolved = out_dir.resolve()
    for forbidden in FORBIDDEN_OUTPUT_ROOTS:
        if resolved.is_relative_to(forbidden.resolve()):
            _fail(f"output must not be written under a UI/publication root: {out_dir}")


def _ensure_allocation_csv(path: Path) -> None:
    if path.is_file():
        return
    if path != ALLOCATION_CSV:
        _fail(f"missing custom allocation CSV: {path}")
    result = subprocess.run(
        [sys.executable, "-m", "workflows.national.harvest.forest_lp_v1"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not path.is_file():
        _fail(
            "Forest LP v1 allocation is missing and regeneration failed: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )


@dataclass(frozen=True)
class AllocationInput:
    cap_case: str
    allocation_case: str
    national_target_m3: float
    province: str
    effective_cap_m3: float
    allocated_harvest_m3: float | None
    binding: bool | None
    case_status: str
    case_feasible: bool
    province_status: str
    province_feasible: bool


@dataclass(frozen=True)
class Margin:
    province: str
    province_name: str
    scenario: str
    scenario_role: str
    margin_low_cad_m3: float
    margin_base_cad_m3: float
    margin_high_cad_m3: float
    price_tier: str
    cost_tier: str


@dataclass(frozen=True)
class NetValueRow:
    allocation: AllocationInput
    margins: Mapping[str, Margin]
    values: Mapping[tuple[str, str], float | None]


def _parse_allocation_rows(
    rows: Sequence[Mapping[str, str]], path: Path
) -> list[AllocationInput]:
    if len(rows) != EXPECTED_ALLOCATION_ROWS:
        _fail(f"{path}: expected {EXPECTED_ALLOCATION_ROWS} rows, got {len(rows)}")
    parsed: list[AllocationInput] = []
    seen: set[tuple[str, str, str]] = set()
    for index, row in enumerate(rows, start=2):
        context = f"{path}:row{index}"
        cap_case = _text(row, "cap_case", context)
        allocation_case = _text(row, "allocation_case", context)
        province = _text(row, "province", context).upper()
        if cap_case not in CAP_CASES:
            _fail(f"{context}: unknown cap_case {cap_case!r}")
        if allocation_case not in LEVELS:
            _fail(f"{context}: unknown allocation_case {allocation_case!r}")
        if province not in PROVINCES:
            _fail(f"{context}: unknown province {province!r}")
        key = (cap_case, allocation_case, province)
        if key in seen:
            _fail(f"{context}: duplicate allocation key {key}")
        seen.add(key)

        target = _finite_float(
            row.get("national_harvest_target_m3"),
            field="national_harvest_target_m3",
            context=context,
        )
        expected_target = LEVEL_TARGETS_M3[allocation_case]
        if abs(target - expected_target) > ALLOCATION_TOLERANCE_M3:
            _fail(
                f"{context}: national target {target} does not match "
                f"{allocation_case} target {expected_target}"
            )
        effective_cap = _finite_float(
            row.get("effective_cap_m3"), field="effective_cap_m3", context=context
        )
        if effective_cap < 0:
            _fail(f"{context}: negative effective cap")
        allocated = _optional_float(
            row.get("allocated_harvest_m3"),
            field="allocated_harvest_m3",
            context=context,
        )
        if allocated is not None and allocated < 0:
            _fail(f"{context}: negative allocation")
        if allocated is not None and allocated > effective_cap + CAP_TOLERANCE_M3:
            _fail(f"{context}: allocation {allocated} exceeds cap {effective_cap}")

        case_feasible = _boolean(
            row.get("case_feasible"), field="case_feasible", context=context
        )
        province_feasible = _boolean(
            row.get("province_feasible"), field="province_feasible", context=context
        )
        case_status = _text(row, "case_status", context)
        province_status = _text(row, "province_status", context)
        binding = _optional_boolean(row.get("binding"), field="binding", context=context)

        if case_feasible and allocated is None:
            _fail(f"{context}: feasible case has a blank allocation")
        if not case_feasible and allocated is not None:
            _fail(f"{context}: infeasible case has a non-blank allocation")
        if province_feasible != (allocated is not None):
            _fail(f"{context}: province_feasible disagrees with allocation presence")
        if case_status != ("optimal" if case_feasible else "infeasible"):
            _fail(f"{context}: case_status {case_status!r} disagrees with feasibility")
        expected_province_status = (
            "allocated" if allocated is not None else "infeasible_no_allocation"
        )
        if province_status != expected_province_status:
            _fail(
                f"{context}: province_status {province_status!r} does not match "
                f"{expected_province_status!r}"
            )
        if allocated is None and binding is not None:
            _fail(f"{context}: blank allocation must have a blank binding flag")

        parsed.append(
            AllocationInput(
                cap_case=cap_case,
                allocation_case=allocation_case,
                national_target_m3=target,
                province=province,
                effective_cap_m3=effective_cap,
                allocated_harvest_m3=allocated,
                binding=binding,
                case_status=case_status,
                case_feasible=case_feasible,
                province_status=province_status,
                province_feasible=province_feasible,
            )
        )

    expected_keys = {
        (cap_case, level, province)
        for cap_case in CAP_CASES
        for level in LEVELS
        for province in PROVINCES
    }
    if seen != expected_keys:
        missing = sorted(expected_keys - seen)
        extra = sorted(seen - expected_keys)
        _fail(f"{path}: allocation grid mismatch; missing={missing}, extra={extra}")
    parsed.sort(
        key=lambda row: (
            CAP_CASES.index(row.cap_case),
            LEVELS.index(row.allocation_case),
            PROVINCE_INDEX[row.province],
        )
    )
    return parsed


def _parse_margins(
    rows: Sequence[Mapping[str, str]], path: Path
) -> dict[tuple[str, str], Margin]:
    if len(rows) != EXPECTED_MARGIN_ROWS:
        _fail(f"{path}: expected {EXPECTED_MARGIN_ROWS} rows, got {len(rows)}")
    margins: dict[tuple[str, str], Margin] = {}
    for index, row in enumerate(rows, start=2):
        context = f"{path}:row{index}"
        province = _text(row, "province", context).upper()
        scenario = _text(row, "scenario", context)
        if province not in PROVINCES:
            _fail(f"{context}: unknown province {province!r}")
        if scenario not in SCENARIOS:
            _fail(f"{context}: unknown scenario {scenario!r}")
        key = (province, scenario)
        if key in margins:
            _fail(f"{context}: duplicate margin key {key}")

        low = _finite_float(
            row.get("margin_low_cad_m3"), field="margin_low_cad_m3", context=context
        )
        base = _finite_float(
            row.get("margin_base_cad_m3"), field="margin_base_cad_m3", context=context
        )
        high = _finite_float(
            row.get("margin_high_cad_m3"), field="margin_high_cad_m3", context=context
        )
        if not low <= base <= high:
            _fail(f"{context}: low/base/high margins are unordered")
        evidence = _text(row, "evidence", context)
        if evidence != EVIDENCE:
            _fail(f"{context}: evidence must be {EVIDENCE}, got {evidence!r}")
        for field in ("observed", "policy_grade"):
            if _boolean(row.get(field), field=field, context=context):
                _fail(f"{context}: {field} must remain false")
        scenario_role = _text(row, "scenario_role", context)
        if scenario_role != SCENARIO_ROLE[scenario]:
            _fail(
                f"{context}: scenario_role {scenario_role!r} does not match "
                f"{SCENARIO_ROLE[scenario]!r}"
            )
        margins[key] = Margin(
            province=province,
            province_name=_text(row, "province_name", context),
            scenario=scenario,
            scenario_role=scenario_role,
            margin_low_cad_m3=low,
            margin_base_cad_m3=base,
            margin_high_cad_m3=high,
            price_tier=_text(row, "price_tier", context),
            cost_tier=_text(row, "cost_tier", context),
        )

    expected_keys = {
        (province, scenario) for province in PROVINCES for scenario in SCENARIOS
    }
    if set(margins) != expected_keys:
        _fail(f"{path}: margin grid does not cover 13 provinces x 2 scenarios")
    return margins


def _load_margin_metadata(margins: Mapping[tuple[str, str], Margin]) -> dict[str, Any]:
    payload = _read_json(MARGIN_JSON)
    if payload.get("model_id") != SOURCE_MARGIN_MODEL_ID:
        _fail(f"{MARGIN_JSON}: unexpected model_id {payload.get('model_id')!r}")
    if payload.get("label") != NET_VALUE_STATUS:
        _fail(f"{MARGIN_JSON}: label must remain {NET_VALUE_STATUS}")
    for field in ("observed", "policy_grade", "combined_national_net_value"):
        if payload.get(field) is not False:
            _fail(f"{MARGIN_JSON}: {field} must remain false")
    provinces = payload.get("provinces")
    if not isinstance(provinces, list) or len(provinces) != EXPECTED_PROVINCE_COUNT:
        _fail(f"{MARGIN_JSON}: expected 13 province records")
    for province_record in provinces:
        if not isinstance(province_record, dict):
            _fail(f"{MARGIN_JSON}: province record is not an object")
        province = str(province_record.get("province") or "")
        if province not in PROVINCES:
            _fail(f"{MARGIN_JSON}: unexpected province {province!r}")
        scenarios = province_record.get("scenarios")
        if not isinstance(scenarios, dict):
            _fail(f"{MARGIN_JSON}: {province}: missing scenarios")
        for scenario in SCENARIOS:
            source = scenarios.get(scenario)
            if not isinstance(source, dict):
                _fail(f"{MARGIN_JSON}: {province}/{scenario}: missing scenario")
            parsed = margins[(province, scenario)]
            for case in CASES:
                source_value = _finite_float(
                    source.get(f"margin_{case}_cad_m3"),
                    field=f"margin_{case}_cad_m3",
                    context=f"{MARGIN_JSON}:{province}/{scenario}",
                )
                computed = getattr(parsed, f"margin_{case}_cad_m3")
                if abs(source_value - computed) > MARGIN_TOLERANCE_CAD_M3:
                    _fail(
                        f"{MARGIN_JSON}:{province}/{scenario}/{case}: margin "
                        f"{source_value} disagrees with CSV {computed}"
                    )
            if (
                scenario == SCENARIOS[0]
                and province_record.get("price_tier") != parsed.price_tier
            ):
                _fail(
                    f"{MARGIN_JSON}:{province}: base price tier disagrees with "
                    "the margin CSV"
                )
        if province_record.get("cost_tier") != margins[(province, SCENARIOS[0])].cost_tier:
            _fail(f"{MARGIN_JSON}:{province}: cost tier disagrees with the margin CSV")

    scenarios_payload = payload.get("scenarios")
    if not isinstance(scenarios_payload, dict):
        _fail(f"{MARGIN_JSON}: missing scenarios")
    national_totals: dict[str, dict[str, float]] = {}
    for scenario in SCENARIOS:
        scenario_payload = scenarios_payload.get(scenario)
        if not isinstance(scenario_payload, dict):
            _fail(f"{MARGIN_JSON}: missing scenario {scenario}")
        national_totals[scenario] = {
            case: _finite_float(
                scenario_payload.get(f"national_total_{case}_m_cad"),
                field=f"national_total_{case}_m_cad",
                context=f"{MARGIN_JSON}:{scenario}",
            )
            for case in CASES
        }

    return {
        "model_id": payload.get("model_id"),
        "artifact_version": payload.get("artifact_version"),
        "source_model_id": payload.get("source_model_id"),
        "source_artifact_version": payload.get("source_artifact_version"),
        "volume_basis": payload.get("volume_basis"),
        "national_totals": national_totals,
    }


def _load_v23_tiers(
    tier_rows: Sequence[Mapping[str, str]],
    margin_summary_rows: Sequence[Mapping[str, str]],
    summary_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if summary_payload.get("model_id") != SOURCE_ECONOMICS_MODEL_ID:
        _fail(f"{V23_SUMMARY_JSON}: unexpected model_id")
    labels = summary_payload.get("labels")
    if not isinstance(labels, dict):
        _fail(f"{V23_SUMMARY_JSON}: missing labels")
    if labels.get("evidence") != EVIDENCE:
        _fail(f"{V23_SUMMARY_JSON}: evidence label changed")
    if labels.get("observed") is not False or labels.get("policy_grade") is not False:
        _fail(f"{V23_SUMMARY_JSON}: source labels must remain unobserved")
    if labels.get("combined_national_net_value") is not False:
        _fail(f"{V23_SUMMARY_JSON}: source must not claim a combined net value")
    if labels.get("national_diagnostic_label") != NET_VALUE_STATUS:
        _fail(f"{V23_SUMMARY_JSON}: national diagnostic label changed")

    d_status = summary_payload.get("statcan_d_status")
    if not isinstance(d_status, dict):
        _fail(f"{V23_SUMMARY_JSON}: missing statcan_d_status")
    if d_status.get("unexpected_cells") not in (None, []):
        _fail(
            f"{V23_SUMMARY_JSON}: upstream reports unexpected StatCan D cells: "
            f"{d_status['unexpected_cells']}"
        )
    allowed_cells = tuple(str(cell) for cell in d_status.get("allowed_cells") or ())

    cost_tiers: dict[str, str] = {}
    for index, row in enumerate(tier_rows, start=2):
        if (row.get("row_type") or "").strip() != "cost_basis_cell":
            continue
        if (row.get("version") or "").strip() != "v2_3":
            continue
        context = f"{V23_TIER_CSV}:row{index}"
        province = _text(row, "province", context).upper()
        if province not in PROVINCES:
            _fail(f"{context}: unexpected province {province!r}")
        tier = _text(row, "tier_after", context)
        if tier != _text(row, "tier", context):
            _fail(f"{context}: tier and tier_after disagree")
        cost_tiers[province] = tier
    if set(cost_tiers) != set(PROVINCES):
        _fail(f"{V23_TIER_CSV}: v2_3 cost tiers do not cover 13 provinces")

    price_tiers: dict[tuple[str, str], str] = {}
    summary_cost_tiers: dict[str, str] = {}
    for index, row in enumerate(margin_summary_rows, start=2):
        if (row.get("row_type") or "").strip() != "province":
            continue
        context = f"{V23_MARGIN_SUMMARY_CSV}:row{index}"
        province = _text(row, "province", context).upper()
        scenario = _text(row, "price_scenario", context)
        if province not in PROVINCES or scenario not in SCENARIOS:
            _fail(f"{context}: unexpected province/scenario")
        price_tiers[(province, scenario)] = _text(row, "price_tier", context)
        summary_cost_tiers[province] = _text(row, "cost_tier", context)
    expected_keys = {
        (province, scenario) for province in PROVINCES for scenario in SCENARIOS
    }
    if set(price_tiers) != expected_keys:
        _fail(f"{V23_MARGIN_SUMMARY_CSV}: price tiers do not cover 13 x 2")
    if set(summary_cost_tiers) != set(PROVINCES):
        _fail(f"{V23_MARGIN_SUMMARY_CSV}: cost tiers do not cover 13 provinces")
    for province in PROVINCES:
        if summary_cost_tiers[province] != cost_tiers[province]:
            _fail(
                f"{V23_DIR}: cost tier disagreement for {province}: "
                f"{summary_cost_tiers[province]!r} vs {cost_tiers[province]!r}"
            )
    return {
        "allowed_d_cells": allowed_cells,
        "price_tiers": price_tiers,
        "cost_tiers": cost_tiers,
        "source_artifact_version": summary_payload.get("artifact_version"),
    }


def _validate_tiers(
    margins: Mapping[tuple[str, str], Margin], v23: Mapping[str, Any]
) -> None:
    price_tiers = v23["price_tiers"]
    cost_tiers = v23["cost_tiers"]
    for (province, scenario), margin in margins.items():
        expected_price = price_tiers[(province, scenario)]
        if margin.price_tier != expected_price:
            _fail(
                f"margin CSV price tier for {province}/{scenario} is "
                f"{margin.price_tier!r}, v2.3 says {expected_price!r}"
            )
        expected_cost = cost_tiers[province]
        if margin.cost_tier != expected_cost:
            _fail(
                f"margin CSV cost tier for {province} is {margin.cost_tier!r}, "
                f"v2.3 says {expected_cost!r}"
            )


def assert_no_unexpected_d(
    csv_inputs: Mapping[Path, Sequence[Mapping[str, str]]]
) -> dict[str, Any]:
    """Fail on any Data-quality ``D`` flag in a direct input CSV.

    The upstream v2.3 StatCan allowlist is carried for provenance.  None of the
    direct inputs for this artifact are the StatCan cost proxy, so any ``D``
    cell in a direct input is unexpected and halts the build.
    """

    unexpected: list[dict[str, object]] = []
    for path, rows in csv_inputs.items():
        for index, row in enumerate(rows, start=2):
            for field, value in row.items():
                if str(value or "").strip() == "D":
                    unexpected.append(
                        {
                            "path": _display_path(path),
                            "row": index,
                            "field": field,
                        }
                    )
    if unexpected:
        _fail(f"unexpected D status cell(s): {unexpected[:5]}")
    return {
        "observed_d_cell_count": 0,
        "unexpected_cells": [],
        "policy": (
            "D is a data-quality flag, not a deletion; no direct input may carry "
            "an unlisted D."
        ),
    }


def _build_net_value_rows(
    allocation_rows: Sequence[AllocationInput],
    margins: Mapping[tuple[str, str], Margin],
) -> list[NetValueRow]:
    rows: list[NetValueRow] = []
    for allocation in allocation_rows:
        province_margins = {
            scenario: margins[(allocation.province, scenario)]
            for scenario in SCENARIOS
        }
        values: dict[tuple[str, str], float | None] = {}
        for scenario, margin in province_margins.items():
            for case, margin_value in (
                ("low", margin.margin_low_cad_m3),
                ("base", margin.margin_base_cad_m3),
                ("high", margin.margin_high_cad_m3),
            ):
                values[(scenario, case)] = (
                    None
                    if allocation.allocated_harvest_m3 is None
                    else allocation.allocated_harvest_m3 * margin_value / 1_000_000.0
                )
        rows.append(
            NetValueRow(
                allocation=allocation,
                margins=province_margins,
                values=values,
            )
        )
    if len(rows) != EXPECTED_ALLOCATION_ROWS:
        _fail(f"built {len(rows)} net-value rows, expected {EXPECTED_ALLOCATION_ROWS}")
    return rows


def _province_rows(net_rows: Sequence[NetValueRow]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for net_row in net_rows:
        allocation = net_row.allocation
        donor = net_row.margins["donor_flat"]
        scaled = net_row.margins["stumpage_scaled"]
        row: dict[str, object] = {
            "cap_case": allocation.cap_case,
            "allocation_case": allocation.allocation_case,
            "province": allocation.province,
            "province_name": donor.province_name,
            "case_status": allocation.case_status,
            "case_feasible": allocation.case_feasible,
            "province_status": allocation.province_status,
            "allocated_harvest_m3": allocation.allocated_harvest_m3,
            "effective_cap_m3": allocation.effective_cap_m3,
            "binding": allocation.binding,
            "donor_flat_price_tier": donor.price_tier,
            "stumpage_scaled_price_tier": scaled.price_tier,
            "cost_tier": donor.cost_tier,
            "evidence_tier": EVIDENCE,
            "net_value_label": EVIDENCE,
            "net_value_status": NET_VALUE_STATUS,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "lp_objective": LP_OBJECTIVE,
        }
        for scenario, margin in (
            ("donor_flat", donor),
            ("stumpage_scaled", scaled),
        ):
            for case in CASES:
                row[f"{scenario}_margin_{case}_cad_m3"] = getattr(
                    margin, f"margin_{case}_cad_m3"
                )
                value = net_row.values[(scenario, case)]
                if value is not None and value == 0.0:
                    value = 0.0
                row[f"{scenario}_net_value_{case}_m_cad"] = value
                row[f"{scenario}_negative_{case}"] = (
                    None if value is None else value < 0.0
                )
        rows.append(row)
    return rows


def _sign_count(values: Sequence[float | None], *, negative: bool) -> int | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    if negative:
        return sum(1 for value in present if value < 0.0)
    return sum(1 for value in present if value == 0.0)


def _build_group_summaries(
    net_rows: Sequence[NetValueRow],
) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[NetValueRow]] = defaultdict(list)
    for net_row in net_rows:
        grouped[
            (net_row.allocation.cap_case, net_row.allocation.allocation_case)
        ].append(net_row)

    summaries: list[dict[str, object]] = []
    for cap_case in CAP_CASES:
        for level in LEVELS:
            group = grouped[(cap_case, level)]
            if len(group) != EXPECTED_PROVINCE_COUNT:
                _fail(
                    f"{cap_case}/{level}: expected 13 province rows, "
                    f"got {len(group)}"
                )
            for scenario in SCENARIOS:
                volumes = [
                    net_row.allocation.allocated_harvest_m3 for net_row in group
                ]
                allocated_volumes = [value for value in volumes if value is not None]
                feasible = all(value is not None for value in volumes)
                if not feasible and allocated_volumes:
                    _fail(f"{cap_case}/{level}/{scenario}: partial allocation group")

                value_by_case: dict[str, list[float | None]] = {
                    case: [
                        net_row.values[(scenario, case)] for net_row in group
                    ]
                    for case in CASES
                }
                for case in CASES:
                    present = [
                        value for value in value_by_case[case] if value is not None
                    ]
                    if feasible and len(present) != EXPECTED_PROVINCE_COUNT:
                        _fail(
                            f"{cap_case}/{level}/{scenario}/{case}: feasible group "
                            "has a blank net value"
                        )
                    if not feasible and present:
                        _fail(
                            f"{cap_case}/{level}/{scenario}/{case}: infeasible group "
                            "has a non-blank net value"
                        )

                allocation_m3 = (
                    math.fsum(allocated_volumes) if allocated_volumes else None
                )
                net_values = {
                    case: (
                        math.fsum(
                            value
                            for value in value_by_case[case]
                            if value is not None
                        )
                        if feasible
                        else None
                    )
                    for case in CASES
                }
                national_margins = {
                    case: (
                        net_values[case] * 1_000_000.0 / allocation_m3
                        if net_values[case] is not None
                        and allocation_m3 is not None
                        and allocation_m3 > 0.0
                        else None
                    )
                    for case in CASES
                }
                negative_provinces = {
                    case: [
                        net_row.allocation.province
                        for net_row in group
                        if net_row.values[(scenario, case)] is not None
                        and (net_row.values[(scenario, case)] or 0.0) < 0.0
                    ]
                    for case in CASES
                }
                summaries.append(
                    {
                        "cap_case": cap_case,
                        "allocation_case": level,
                        "allocation_target_m3": group[
                            0
                        ].allocation.national_target_m3,
                        "scenario": scenario,
                        "scenario_role": SCENARIO_ROLE[scenario],
                        "case_status": group[0].allocation.case_status,
                        "case_feasible": feasible,
                        "province_rows": EXPECTED_PROVINCE_COUNT,
                        "allocated_province_rows": len(allocated_volumes),
                        "blank_province_rows": EXPECTED_PROVINCE_COUNT
                        - len(allocated_volumes),
                        "allocation_m3": allocation_m3,
                        "net_value_low_m_cad": net_values["low"],
                        "net_value_base_m_cad": net_values["base"],
                        "net_value_high_m_cad": net_values["high"],
                        "national_margin_low_cad_m3": national_margins["low"],
                        "national_margin_base_cad_m3": national_margins["base"],
                        "national_margin_high_cad_m3": national_margins["high"],
                        "negative_net_value_low_count": _sign_count(
                            value_by_case["low"], negative=True
                        ),
                        "negative_net_value_base_count": _sign_count(
                            value_by_case["base"], negative=True
                        ),
                        "negative_net_value_high_count": _sign_count(
                            value_by_case["high"], negative=True
                        ),
                        "positive_net_value_low_count": (
                            sum(
                                1
                                for value in value_by_case["low"]
                                if value is not None and value > 0.0
                            )
                            if feasible
                            else None
                        ),
                        "positive_net_value_base_count": (
                            sum(
                                1
                                for value in value_by_case["base"]
                                if value is not None and value > 0.0
                            )
                            if feasible
                            else None
                        ),
                        "positive_net_value_high_count": (
                            sum(
                                1
                                for value in value_by_case["high"]
                                if value is not None and value > 0.0
                            )
                            if feasible
                            else None
                        ),
                        "zero_net_value_low_count": _sign_count(
                            value_by_case["low"], negative=False
                        ),
                        "zero_net_value_base_count": _sign_count(
                            value_by_case["base"], negative=False
                        ),
                        "zero_net_value_high_count": _sign_count(
                            value_by_case["high"], negative=False
                        ),
                        "negative_provinces_low": negative_provinces["low"],
                        "negative_provinces_base": negative_provinces["base"],
                        "negative_provinces_high": negative_provinces["high"],
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "net_value_status": NET_VALUE_STATUS,
                        "lp_objective": LP_OBJECTIVE,
                    }
                )
    if len(summaries) != EXPECTED_GROUP_SUMMARIES:
        _fail(f"built {len(summaries)} group summaries, expected 18")
    return summaries


def _build_national_rows(
    group_summaries: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for summary in group_summaries:
        feasible = bool(summary["case_feasible"])
        for case in CASES:
            net_value = summary[f"net_value_{case}_m_cad"]
            if feasible:
                negative = summary[f"negative_net_value_{case}_count"]
                positive = summary[f"positive_net_value_{case}_count"]
                zero = summary[f"zero_net_value_{case}_count"]
            else:
                negative = None
                positive = None
                zero = None
            rows.append(
                {
                    "scenario": summary["scenario"],
                    "scenario_role": summary["scenario_role"],
                    "cap_case": summary["cap_case"],
                    "allocation_case": summary["allocation_case"],
                    "case": case,
                    "allocation_m3": summary["allocation_m3"],
                    "net_value_m_cad": net_value,
                    "province_count": summary["province_rows"],
                    "allocated_province_count": summary["allocated_province_rows"],
                    "blank_province_count": summary["blank_province_rows"],
                    "negative_province_count": negative,
                    "positive_province_count": positive,
                    "zero_province_count": zero,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "net_value_status": NET_VALUE_STATUS,
                    "lp_objective": LP_OBJECTIVE,
                }
            )
    if len(rows) != EXPECTED_NATIONAL_ROWS:
        _fail(f"built {len(rows)} national rows, expected {EXPECTED_NATIONAL_ROWS}")
    return rows


def _check(
    checks: list[dict[str, object]],
    *,
    check_id: str,
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "name": name,
            "status": "pass" if passed else "fail",
            "detail": detail,
        }
    )
    if not passed:
        _fail(f"{check_id} {name} failed: {detail}")


def _validate_bundle(
    net_rows: Sequence[NetValueRow],
    province_rows: Sequence[Mapping[str, object]],
    group_summaries: Sequence[Mapping[str, object]],
    national_rows: Sequence[Mapping[str, object]],
    tier_validation: Mapping[str, Any],
    v1_snapshot_before: Mapping[str, str],
    v1_snapshot_after: Mapping[str, str],
    v1_source_before: str,
    v1_source_after: str,
) -> dict[str, Any]:
    checks: list[dict[str, object]] = []
    _check(
        checks,
        check_id="allocation-rows",
        name="Forest LP v1 allocation rows preserved",
        passed=len(net_rows) == EXPECTED_ALLOCATION_ROWS,
        detail=f"{len(net_rows)} rows (expected {EXPECTED_ALLOCATION_ROWS})",
    )

    arithmetic_failures = 0
    blank_failures = 0
    complete_failures = 0
    clipping_failures = 0
    negative_values = 0
    for net_row in net_rows:
        allocation = net_row.allocation
        for scenario in SCENARIOS:
            margin = net_row.margins[scenario]
            for case in CASES:
                value = net_row.values[(scenario, case)]
                margin_value = getattr(margin, f"margin_{case}_cad_m3")
                if allocation.allocated_harvest_m3 is None:
                    if value is not None:
                        blank_failures += 1
                    continue
                if value is None:
                    complete_failures += 1
                    continue
                expected = (
                    allocation.allocated_harvest_m3 * margin_value / 1_000_000.0
                )
                if abs(value - expected) > NET_VALUE_TOLERANCE_M_CAD:
                    arithmetic_failures += 1
                if allocation.allocated_harvest_m3 == 0.0:
                    if value != 0.0:
                        clipping_failures += 1
                elif (margin_value < 0.0 and value >= 0.0) or (
                    margin_value > 0.0 and value <= 0.0
                ):
                    clipping_failures += 1
                if value < 0.0:
                    negative_values += 1
    _check(
        checks,
        check_id="net-value-arithmetic",
        name="Net value equals allocation x margin / 1e6",
        passed=arithmetic_failures == 0,
        detail=f"{arithmetic_failures} arithmetic mismatches",
    )
    _check(
        checks,
        check_id="infeasible-blanks",
        name="Infeasible rows keep blank net values",
        passed=blank_failures == 0,
        detail=f"{blank_failures} non-blank values on infeasible rows",
    )
    _check(
        checks,
        check_id="feasible-complete",
        name="Feasible rows carry all six net values",
        passed=complete_failures == 0,
        detail=f"{complete_failures} blank values on feasible rows",
    )
    _check(
        checks,
        check_id="negative-unclipped",
        name="Negative values are retained without clipping",
        passed=clipping_failures == 0 and negative_values > 0,
        detail=(
            f"{negative_values} negative net-value cells; "
            f"{clipping_failures} sign/clipping mismatches"
        ),
    )

    infeasible_rows = [
        row for row in net_rows if row.allocation.allocated_harvest_m3 is None
    ]
    _check(
        checks,
        check_id="infeasible-count",
        name="Infeasible allocation rows are blank",
        passed=len(infeasible_rows) == 26,
        detail=f"{len(infeasible_rows)} infeasible rows",
    )

    _check(
        checks,
        check_id="group-summaries",
        name="Allocation-level summaries cover cap x level x scenario",
        passed=len(group_summaries) == EXPECTED_GROUP_SUMMARIES,
        detail=f"{len(group_summaries)} groups",
    )
    _check(
        checks,
        check_id="national-rows",
        name="National totals cover every group and case",
        passed=len(national_rows) == EXPECTED_NATIONAL_ROWS,
        detail=f"{len(national_rows)} rows",
    )

    total_failures = 0
    for summary in group_summaries:
        if not summary["case_feasible"]:
            continue
        scenario = str(summary["scenario"])
        cap_case = str(summary["cap_case"])
        level = str(summary["allocation_case"])
        group_rows = [
            net_row
            for net_row in net_rows
            if net_row.allocation.cap_case == cap_case
            and net_row.allocation.allocation_case == level
        ]
        for case in CASES:
            computed = math.fsum(
                net_row.values[(scenario, case)] or 0.0 for net_row in group_rows
            )
            stated = summary[f"net_value_{case}_m_cad"]
            if stated is None or abs(computed - float(stated)) > TOTAL_TOLERANCE_M_CAD:
                total_failures += 1
    _check(
        checks,
        check_id="national-equals-province-sum",
        name="National totals equal the province sum",
        passed=total_failures == 0,
        detail=f"{total_failures} total mismatches",
    )

    tier_failures = 0
    for (province, scenario), margin in tier_validation["margins"].items():
        if margin.price_tier != tier_validation["price_tiers"][(province, scenario)]:
            tier_failures += 1
        if margin.cost_tier != tier_validation["cost_tiers"][province]:
            tier_failures += 1
    _check(
        checks,
        check_id="tier-labels",
        name="Price and cost tiers match national_economics_v2_3",
        passed=tier_failures == 0,
        detail=f"{tier_failures} tier mismatches",
    )

    canonical = _canonical_totals(group_summaries)
    margin_total_deltas: dict[str, float] = {}
    for scenario in SCENARIOS:
        for case in CASES:
            computed = canonical[scenario][f"net_value_{case}_m_cad"]
            source = tier_validation["national_totals"][scenario][case]
            margin_total_deltas[f"{scenario}_{case}"] = abs(float(computed) - source)
    maximum_margin_delta = max(margin_total_deltas.values())
    _check(
        checks,
        check_id="margin-total-crosscheck",
        name="Canonical 119m totals match the margin-total diagnostic",
        passed=maximum_margin_delta <= TOTAL_TOLERANCE_M_CAD,
        detail=(
            "max abs delta vs national_margin_totals.json: "
            f"{maximum_margin_delta:.9f} M CAD"
        ),
    )

    _check(
        checks,
        check_id="labels",
        name="Model-estimate labels are carried on every row",
        passed=all(
            row["observed"] is False
            and row["policy_grade"] is False
            and row["net_value_label"] == EVIDENCE
            and row["net_value_status"] == NET_VALUE_STATUS
            and row["lp_objective"] == LP_OBJECTIVE
            for row in province_rows
        )
        and all(
            summary["observed"] is False
            and summary["policy_grade"] is False
            and summary["net_value_status"] == NET_VALUE_STATUS
            and summary["lp_objective"] == LP_OBJECTIVE
            for summary in group_summaries
        )
        and all(
            row["observed"] is False
            and row["policy_grade"] is False
            and row["net_value_status"] == NET_VALUE_STATUS
            and row["lp_objective"] == LP_OBJECTIVE
            for row in national_rows
        ),
        detail="observed=false, policy_grade=false, lp_objective=not_used",
    )
    _check(
        checks,
        check_id="objective-unchanged",
        name="Forest LP objective and v1 outputs are unchanged",
        passed=(
            v1_snapshot_before == v1_snapshot_after
            and v1_source_before == v1_source_after
        ),
        detail=(
            f"{len(v1_snapshot_after)} v1 output files hashed before and after; "
            "forest_lp_v1.py hash unchanged"
        ),
    )
    _check(
        checks,
        check_id="no-restricted-data",
        name="No restricted or raw data is used",
        passed=True,
        detail="all inputs are under data/derived or out/; restricted_data_used=false",
    )
    _check(
        checks,
        check_id="no-public-ui",
        name="No UI or public OSeCAN publication is written",
        passed=True,
        detail="output is a derived data artifact; public_osecan_input=false",
    )

    return {
        "checks": checks,
        "allocation_rows": len(net_rows),
        "expected_allocation_rows": EXPECTED_ALLOCATION_ROWS,
        "group_summaries": len(group_summaries),
        "national_rows": len(national_rows),
        "feasible_rows": EXPECTED_ALLOCATION_ROWS - len(infeasible_rows),
        "infeasible_rows": len(infeasible_rows),
        "negative_net_value_cells": negative_values,
        "national_equals_province_sum": total_failures == 0,
        "net_value_arithmetic": arithmetic_failures == 0,
        "infeasible_blanks": blank_failures == 0,
        "feasible_complete": complete_failures == 0,
        "negative_unclipped": clipping_failures == 0 and negative_values > 0,
        "tier_labels_match_v2_3": tier_failures == 0,
        "margin_total_max_abs_delta_m_cad": maximum_margin_delta,
        "margin_total_deltas_m_cad": margin_total_deltas,
        "labels_model_estimate": True,
        "observed_claim": False,
        "policy_grade_claim": False,
        "lp_objective": LP_OBJECTIVE,
        "forest_lp_objective_changed": False,
        "forest_lp_source_sha256": v1_source_after,
        "v1_outputs_unchanged": v1_snapshot_before == v1_snapshot_after,
        "v1_output_files_hashed": len(v1_snapshot_after),
        "public_osecan_input": False,
        "restricted_data_used": False,
        "unexpected_d_cells": 0,
    }


def _canonical_totals(
    group_summaries: Sequence[Mapping[str, object]],
    *,
    cap_case: str = "documented_ceiling",
    level: str = "119m",
) -> dict[str, dict[str, object]]:
    lookup = {
        (summary["cap_case"], summary["allocation_case"], summary["scenario"]): summary
        for summary in group_summaries
    }
    return {
        scenario: lookup[(cap_case, level, scenario)] for scenario in SCENARIOS
    }


def _build_summary_payload(
    group_summaries: Sequence[Mapping[str, object]],
    national_rows: Sequence[Mapping[str, object]],
    margin_metadata: Mapping[str, Any],
    v23: Mapping[str, Any],
    validation: Mapping[str, Any],
    generated_utc: str,
    d_validation: Mapping[str, Any],
) -> dict[str, Any]:
    feasible_rows = sum(
        1 for summary in group_summaries if bool(summary["case_feasible"])
    )
    scenario_records: dict[str, dict[str, object]] = {}
    for scenario in SCENARIOS:
        scenario_summaries = [
            summary for summary in group_summaries if summary["scenario"] == scenario
        ]
        scenario_records[scenario] = {
            "role": SCENARIO_ROLE[scenario],
            "group_count": len(scenario_summaries),
            "feasible_group_count": sum(
                1 for summary in scenario_summaries if bool(summary["case_feasible"])
            ),
            "canonical_119m_documented_ceiling": next(
                summary
                for summary in scenario_summaries
                if summary["cap_case"] == "documented_ceiling"
                and summary["allocation_case"] == "119m"
            ),
        }

    groups = []
    for summary in group_summaries:
        groups.append(
            {
                **summary,
                "negative_provinces_low": list(summary["negative_provinces_low"]),
                "negative_provinces_base": list(summary["negative_provinces_base"]),
                "negative_provinces_high": list(summary["negative_provinces_high"]),
            }
        )
    national = []
    for row in national_rows:
        national.append(dict(row))

    return {
        "schema_version": SCHEMA_VERSION,
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": NET_VALUE_STATUS,
        "source_margin_model_id": margin_metadata.get("model_id"),
        "source_margin_artifact_version": margin_metadata.get("artifact_version"),
        "source_publication_context": SOURCE_PUBLICATION_CONTEXT,
        "source_economics_model_id": SOURCE_ECONOMICS_MODEL_ID,
        "source_economics_artifact_version": v23.get("source_artifact_version"),
        "labels": {
            "net_value_label": EVIDENCE,
            "evidence_tier": EVIDENCE,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "net_value_status": NET_VALUE_STATUS,
            "lp_objective": LP_OBJECTIVE,
            "forest_lp_objective_changed": False,
            "v1_outputs_changed": False,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            "ranking_performed": False,
            "public_osecan_input": False,
            "restricted_data_used": False,
        },
        "coverage": {
            "allocation_source": _display_path(ALLOCATION_CSV),
            "allocation_rows": validation["allocation_rows"],
            "expected_allocation_rows": EXPECTED_ALLOCATION_ROWS,
            "margin_source": _display_path(MARGIN_CSV),
            "margin_rows": EXPECTED_MARGIN_ROWS,
            "province_count": EXPECTED_PROVINCE_COUNT,
            "scenario_count": EXPECTED_SCENARIO_COUNT,
            "scenarios": list(SCENARIOS),
            "levels": list(LEVELS),
            "cap_cases": list(CAP_CASES),
            "joined_province_scenario_rows": EXPECTED_ALLOCATION_ROWS
            * EXPECTED_SCENARIO_COUNT,
            "feasible_group_count": feasible_rows,
            "infeasible_group_count": EXPECTED_GROUP_SUMMARIES - feasible_rows,
            "infeasible_allocation_rows": validation["infeasible_rows"],
        },
        "scenarios": scenario_records,
        "groups": groups,
        "national_totals": national,
        "canonical": {
            "cap_case": "documented_ceiling",
            "allocation_case": "119m",
            "scenarios": _canonical_totals(group_summaries),
        },
        "validation": dict(validation),
        "statcan_d": dict(d_validation),
        "caveats": list(CAVEATS),
        "non_claims": list(NON_CLAIMS),
    }


def _table_line(cells: Sequence[object]) -> str:
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def _report(
    group_summaries: Sequence[Mapping[str, object]],
    province_rows: Sequence[Mapping[str, object]],
    validation: Mapping[str, Any],
    generated_utc: str,
) -> str:
    canonical = _canonical_totals(group_summaries)
    lines = [
        "# Forest LP net-value sensitivity",
        "",
        f"**Generated:** {generated_utc}",
        "",
        f"**Model:** `{MODEL_ID}` `{ARTIFACT_VERSION}`",
        "",
        (
            "**Status:** `model_estimate_not_policy` · **Observed:** `false` · "
            "**Policy grade:** `false` · **LP objective:** `not_used`"
        ),
        "",
        (
            "This artifact joins the committed Forest LP v1 allocation rows to the "
            "committed national province margin totals and reports a volume-weighted "
            "net-value sensitivity. It does not solve an LP, change the Forest LP "
            "objective, or write back to any Forest LP v1 output."
        ),
        "",
        "## 1. Join and coverage",
        "",
        (
            f"- Forest LP v1 allocation rows: **{validation['allocation_rows']}** "
            "(13 provinces x 3 allocation levels x 3 cap cases)."
        ),
        (
            "- Province margin totals: **26** rows (13 provinces x 2 scenarios), "
            "from the artifact bundled in the `national-economics-v2.4` "
            "publication (underlying model `national_economics_v2_3`)."
        ),
        "- Scenarios: `donor_flat` (base) and `stumpage_scaled` (sensitivity).",
        (
            f"- Feasible allocation rows: **{validation['feasible_rows']}**; "
            f"infeasible and blank: **{validation['infeasible_rows']}**."
        ),
        (
            "- Join key: `province` + `scenario`; the allocation level supplies the "
            "volume and the margin artifact supplies CAD/m3."
        ),
        "",
        "## 2. National totals by allocation case",
        "",
        (
            "Each row is the national sum for one cap case, allocation level, and "
            "scenario. Levels are alternative screening ladders and must not be "
            "summed across rows. Infeasible rows are blank."
        ),
        "",
        _table_line(
            (
                "Scenario",
                "Cap case",
                "Level",
                "Status",
                "Allocation (Mm3)",
                "Low (M CAD)",
                "Base (M CAD)",
                "High (M CAD)",
                "Base (CAD/m3)",
                "Negative base provinces",
            )
        ),
        _table_line(("---", "---", "---", "---", "---:", "---:", "---:", "---:", "---:", "---:")),
    ]
    for summary in group_summaries:
        allocation = summary["allocation_m3"]
        lines.append(
            _table_line(
                (
                    summary["scenario"],
                    summary["cap_case"],
                    summary["allocation_case"],
                    summary["case_status"],
                    "—" if allocation is None else f"{float(allocation) / 1e6:.3f}",
                    "—"
                    if summary["net_value_low_m_cad"] is None
                    else f"{float(summary['net_value_low_m_cad']):.6f}",
                    "—"
                    if summary["net_value_base_m_cad"] is None
                    else f"{float(summary['net_value_base_m_cad']):.6f}",
                    "—"
                    if summary["net_value_high_m_cad"] is None
                    else f"{float(summary['net_value_high_m_cad']):.6f}",
                    "—"
                    if summary["national_margin_base_cad_m3"] is None
                    else f"{float(summary['national_margin_base_cad_m3']):.4f}",
                    ", ".join(summary["negative_provinces_base"]) or "none",
                )
            )
        )

    lines += [
        "",
        "## 3. Canonical 119 Mm3 documented-ceiling totals",
        "",
        (
            "The 119m documented-ceiling case is the committed allocation basis. "
            "The table is a model diagnostic, not an observed net value."
        ),
        "",
        _table_line(
            (
                "Scenario",
                "Role",
                "Allocation (Mm3)",
                "Low (M CAD)",
                "Base (M CAD)",
                "High (M CAD)",
                "Base (CAD/m3)",
                "Negative low/base/high provinces",
            )
        ),
        _table_line(("---", "---", "---:", "---:", "---:", "---:", "---:", "---:")),
    ]
    for scenario in SCENARIOS:
        summary = canonical[scenario]
        lines.append(
            _table_line(
                (
                    scenario,
                    summary["scenario_role"],
                    f"{float(summary['allocation_m3']) / 1e6:.3f}",
                    f"{float(summary['net_value_low_m_cad']):.6f}",
                    f"{float(summary['net_value_base_m_cad']):.6f}",
                    f"{float(summary['net_value_high_m_cad']):.6f}",
                    f"{float(summary['national_margin_base_cad_m3']):.4f}",
                    (
                        f"{summary['negative_net_value_low_count']} / "
                        f"{summary['negative_net_value_base_count']} / "
                        f"{summary['negative_net_value_high_count']}"
                    ),
                )
            )
        )

    lines += [
        "",
        "## 4. Negative rows",
        "",
        (
            "Negative margins and negative net values are retained unclipped. The "
            "table lists every negative base net-value province in the canonical "
            "119m documented-ceiling case."
        ),
        "",
        _table_line(
            (
                "Scenario",
                "Province",
                "Allocation (m3)",
                "Base margin (CAD/m3)",
                "Base net value (M CAD)",
            )
        ),
        _table_line(("---", "---", "---:", "---:", "---:")),
    ]
    province_lookup = {
        (row["cap_case"], row["allocation_case"], row["province"]): row
        for row in province_rows
    }
    for scenario in SCENARIOS:
        for province in canonical[scenario]["negative_provinces_base"]:
            row = province_lookup[("documented_ceiling", "119m", province)]
            lines.append(
            _table_line(
                (
                    scenario,
                    province,
                    f"{float(row['allocated_harvest_m3']):,.1f}",
                    f"{float(row[f'{scenario}_margin_base_cad_m3']):.4f}",
                    f"{float(row[f'{scenario}_net_value_base_m_cad']):.6f}",
                )
            )
        )

    lines += [
        "",
        "## 5. Labels and caveats",
        "",
        f"- `net_value_label={EVIDENCE}`",
        "- `observed=false`",
        "- `policy_grade=false`",
        f"- `net_value_status={NET_VALUE_STATUS}`",
        f"- `lp_objective={LP_OBJECTIVE}`",
        "- `forest_lp_objective_changed=false`",
        "- `v1_outputs_changed=false`",
        "- `public_osecan_input=false`",
        "- `restricted_data_used=false`",
        "",
    ]
    lines += [f"- {item}" for item in CAVEATS]
    lines += [
        "",
        "## 6. Verification",
        "",
        _table_line(("Check", "Status", "Detail")),
        _table_line(("---", "---", "---")),
    ]
    for check in validation["checks"]:
        lines.append(
            _table_line((f"{check['id']}: {check['name']}", check["status"], check["detail"]))
        )
    lines += [
        "",
        "## 7. Reproduction",
        "",
        "```bash",
        ".venv/bin/python -m workflows.national.harvest.forest_lp_net_value",
        "```",
        "",
        (
            "Inputs and outputs are hashed in `manifest.json`. The Forest LP v1 "
            "allocation is read from `out/forest_lp_v1/forest_lp_allocation.csv`; "
            "if it is absent, the generator runs the v1 synthesis first."
        ),
    ]
    return "\n".join(lines) + "\n"


def _write_manifest(
    out_dir: Path,
    payload: Mapping[str, Any],
    validation: Mapping[str, Any],
    d_validation: Mapping[str, Any],
    input_records: Sequence[Mapping[str, object]],
    generated_utc: str,
) -> None:
    outputs = (
        ALLOCATION_NET_VALUE_CSV,
        ALLOCATION_SUMMARY_CSV,
        NATIONAL_SUMMARY_CSV,
        SUMMARY_JSON,
        FOREST_LP_NET_VALUE_REPORT,
    )
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "generated_utc": generated_utc,
        "source_publication_context": SOURCE_PUBLICATION_CONTEXT,
        "parent_commit": _git_commit(REPO_ROOT),
        "data_commit": _git_commit(REPO_ROOT / "data"),
        "observation_type": EVIDENCE,
        "labels": dict(payload["labels"]),
        "status": NET_VALUE_STATUS,
        "objective_used": False,
        "forest_lp_objective_changed": False,
        "v1_outputs_changed": False,
        "public_osecan_input": False,
        "restricted_data_used": False,
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
            **validation,
            "statcan_d": dict(d_validation),
            "all_values_model_estimate": True,
            "net_value_arithmetic": True,
            "infeasible_blanks": True,
            "forest_lp_objective_changed": False,
            "v1_outputs_unchanged": True,
            "restricted_data_used": False,
            "public_osecan_input": False,
            "unexpected_d_cells": 0,
        },
    }
    _write_json(out_dir / MANIFEST_JSON, manifest)


def build(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    check_deletions: bool = True,
    allocation_csv: Path = ALLOCATION_CSV,
) -> dict[str, Any]:
    """Build the net-value sensitivity bundle and return the summary payload."""

    _assert_output_path_allowed(out_dir)
    if check_deletions:
        assert_no_unexpected_deletions()
    _ensure_allocation_csv(allocation_csv)
    _assert_allowed_input_paths(
        (
            allocation_csv,
            MARGIN_CSV,
            MARGIN_JSON,
            V23_SUMMARY_JSON,
            V23_TIER_CSV,
            V23_MARGIN_SUMMARY_CSV,
        )
    )

    v1_snapshot_before = _snapshot_directory(FOREST_LP_V1_DIR)
    v1_source_before = _sha256(FOREST_LP_V1_SOURCE)

    allocation_rows_raw = _read_csv(allocation_csv)
    margin_rows_raw = _read_csv(MARGIN_CSV)
    tier_rows_raw = _read_csv(V23_TIER_CSV)
    v23_margin_rows_raw = _read_csv(V23_MARGIN_SUMMARY_CSV)
    d_validation = assert_no_unexpected_d(
        {
            allocation_csv: allocation_rows_raw,
            MARGIN_CSV: margin_rows_raw,
            V23_TIER_CSV: tier_rows_raw,
            V23_MARGIN_SUMMARY_CSV: v23_margin_rows_raw,
        }
    )

    allocation_rows = _parse_allocation_rows(allocation_rows_raw, allocation_csv)
    margins = _parse_margins(margin_rows_raw, MARGIN_CSV)
    margin_metadata = _load_margin_metadata(margins)
    v23_summary_payload = _read_json(V23_SUMMARY_JSON)
    v23 = _load_v23_tiers(
        tier_rows_raw, v23_margin_rows_raw, v23_summary_payload
    )
    _validate_tiers(margins, v23)
    tier_validation = {
        "margins": margins,
        "price_tiers": v23["price_tiers"],
        "cost_tiers": v23["cost_tiers"],
        "national_totals": margin_metadata["national_totals"],
    }

    net_rows = _build_net_value_rows(allocation_rows, margins)
    province_rows = _province_rows(net_rows)
    group_summaries = _build_group_summaries(net_rows)
    national_rows = _build_national_rows(group_summaries)

    v1_snapshot_after = _snapshot_directory(FOREST_LP_V1_DIR)
    v1_source_after = _sha256(FOREST_LP_V1_SOURCE)
    validation = _validate_bundle(
        net_rows,
        province_rows,
        group_summaries,
        national_rows,
        tier_validation,
        v1_snapshot_before,
        v1_snapshot_after,
        v1_source_before,
        v1_source_after,
    )

    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    payload = _build_summary_payload(
        group_summaries,
        national_rows,
        margin_metadata,
        v23,
        validation,
        generated_utc,
        d_validation,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / ALLOCATION_NET_VALUE_CSV, province_rows, PROVINCE_FIELDS)
    _write_csv(out_dir / ALLOCATION_SUMMARY_CSV, group_summaries, GROUP_FIELDS)
    _write_csv(out_dir / NATIONAL_SUMMARY_CSV, national_rows, NATIONAL_FIELDS)
    _write_json(out_dir / SUMMARY_JSON, payload)
    report = _report(
        group_summaries,
        province_rows,
        validation,
        generated_utc,
    )
    (out_dir / FOREST_LP_NET_VALUE_REPORT).write_text(report, encoding="utf-8")

    input_records = []
    for path, role, row_count in (
        (allocation_csv, "forest_lp_v1_allocation", len(allocation_rows_raw)),
        (MARGIN_CSV, "province_margin_totals", len(margin_rows_raw)),
        (MARGIN_JSON, "national_margin_totals_metadata", None),
        (V23_SUMMARY_JSON, "national_economics_v2_3_summary", None),
        (V23_TIER_CSV, "national_economics_v2_3_tier_labels", len(tier_rows_raw)),
        (
            V23_MARGIN_SUMMARY_CSV,
            "national_economics_v2_3_margin_summary",
            len(v23_margin_rows_raw),
        ),
    ):
        record: dict[str, object] = {
            "path": _display_path(path),
            "role": role,
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        if row_count is not None:
            record["rows"] = row_count
        input_records.append(record)

    _write_manifest(
        out_dir,
        payload,
        validation,
        d_validation,
        input_records,
        generated_utc,
    )
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report a Forest LP net-value sensitivity from margin totals."
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--allocation-csv",
        type=Path,
        default=ALLOCATION_CSV,
        help="Forest LP v1 allocation CSV (default: out/forest_lp_v1/forest_lp_allocation.csv)",
    )
    parser.add_argument(
        "--no-deletion-check",
        action="store_true",
        help="skip the fail-closed git deletion check",
    )
    args = parser.parse_args(argv)
    payload = build(
        args.out_dir,
        check_deletions=not args.no_deletion_check,
        allocation_csv=args.allocation_csv,
    )
    canonical = payload["canonical"]["scenarios"]
    donor = canonical["donor_flat"]
    scaled = canonical["stumpage_scaled"]
    print(
        "forest LP net value (119m documented ceiling): "
        f"donor_flat base={donor['net_value_base_m_cad']:.6f} M CAD, "
        f"stumpage_scaled base={scaled['net_value_base_m_cad']:.6f} M CAD, "
        f"rows={payload['validation']['allocation_rows']}, "
        f"label={payload['status']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
