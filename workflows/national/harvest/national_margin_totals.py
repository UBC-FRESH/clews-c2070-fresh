"""Province and national margin totals at the 119 Mm3 allocation.

The national economics v2.3 artifact reports margins in CAD/m3.  This module
multiplies those unit margins by the committed provincial 119 Mm3 allocation
and writes a province ledger plus a national diagnostic in M CAD.

The national total is a volume-weighted model diagnostic.  It is not an
observed value, not policy grade, not a combined national net value, and not a
jurisdictional ranking.  Negative margins and negative totals are retained
without clipping.  Nunavut has zero allocation volume, so its total is zero
even though its unit margin is non-zero.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ARTIFACT_VERSION",
    "MANIFEST_JSON",
    "MODEL_ID",
    "NATIONAL_DIAGNOSTIC_LABEL",
    "PROVINCE_CSV",
    "REPORT_MD",
    "TOTAL_JSON",
    "NationalMarginTotalsError",
    "build",
    "main",
    "write_outputs",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
SOURCE_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"
SOURCE_SUMMARY_CSV = SOURCE_DIR / "national_margin_summary.csv"
SOURCE_SUMMARY_JSON = SOURCE_DIR / "summary.json"
ALLOCATION_CSV = HARVEST_BASIS_DIR / "provincial_allocation/119m.csv"
STATCAN_CSV = (
    HARVEST_BASIS_DIR
    / "statcan_logging_cost_proxy"
    / "provincial_logging_cost_proxy.csv"
)
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_margin_totals"

PROVINCE_CSV = "province_margin_totals.csv"
TOTAL_JSON = "national_margin_totals.json"
REPORT_MD = "NATIONAL_MARGIN_TOTALS_REPORT.md"
MANIFEST_JSON = "manifest.json"

MODEL_ID = "national_margin_totals_v1"
SOURCE_MODEL_ID = "national_economics_v2_3"
ARTIFACT_VERSION = "1.0.0"
EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"

BASE_SCENARIO = "donor_flat"
SENSITIVITY_SCENARIO = "stumpage_scaled"
SCENARIOS = (BASE_SCENARIO, SENSITIVITY_SCENARIO)
SCENARIO_ROLE = {
    BASE_SCENARIO: "base",
    SENSITIVITY_SCENARIO: "sensitivity",
}
CASES = ("low", "base", "high")
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
PROVINCE_ORDER = {province: index for index, province in enumerate(PROVINCES)}

EXPECTED_ALLOCATION_TOTAL_M3 = 118_977_339.1
ALLOCATION_TOLERANCE_M3 = 0.2
TOTAL_TOLERANCE_M_CAD = 1e-5
MARGIN_TOLERANCE_CAD_M3 = 1e-4

CSV_FIELDS = (
    "province",
    "province_name",
    "scenario",
    "scenario_role",
    "allocation_m3",
    "margin_low_cad_m3",
    "margin_base_cad_m3",
    "margin_high_cad_m3",
    "total_low_m_cad",
    "total_base_m_cad",
    "total_high_m_cad",
    "share_of_national_total_low",
    "share_of_national_total_base",
    "share_of_national_total_high",
    "evidence",
    "evidence_tier",
    "price_tier",
    "cost_tier",
    "negative_low",
    "negative_base",
    "negative_high",
    "negative_flag",
    "negative_any_case",
    "total_negative_low",
    "total_negative_base",
    "total_negative_high",
    "zero_volume",
    "observed",
    "policy_grade",
)

CAVEATS = (
    (
        "Totals multiply committed v2.3 CAD/m3 margins by the 119 Mm3 provincial "
        "allocation; observed 2024 harvest is not the multiplier here."
    ),
    (
        "The national total is a volume-weighted model diagnostic labelled "
        "model_estimate_not_policy. It is not a policy result and not a combined "
        "national net value."
    ),
    (
        "Negative margins and negative totals are retained unclipped; they are not "
        "set to zero or omitted."
    ),
    (
        "Nunavut has zero allocation volume, so its total is zero by multiplication. "
        "Its unit margin is still shown and is not treated as observed."
    ),
    (
        "No jurisdiction is ranked. Donor-price and donor-cost provinces remain "
        "model estimates and are not reclassified as observed."
    ),
    (
        "Stumpage_scaled is a method sensitivity, not the base scenario, and is not "
        "observed or policy grade."
    ),
)

NON_CLAIMS = (
    "Not a combined national net value.",
    "Not observed; observed=false.",
    "Not policy grade; policy_grade=false.",
    "Not a policy result or policy frontier.",
    "Not a Forest LP objective or constraint input.",
    "Not a public OSeCAN input.",
    "No margin ranking and no observed reclassification of donor-only provinces.",
)


class NationalMarginTotalsError(RuntimeError):
    """Fail-closed error for the national margin-total artifact."""


def _fail(message: str) -> None:
    raise NationalMarginTotalsError(message)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        _fail(f"missing source CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        _fail(f"empty source CSV: {path}")
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"missing source JSON: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(f"invalid source JSON {path}: {error}")
    if not isinstance(value, dict):
        _fail(f"source JSON is not an object: {path}")
    return value


def _text(row: Mapping[str, str], field: str, label: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        _fail(f"{label}: missing {field}")
    return value


def _number(row: Mapping[str, str], field: str, label: str) -> float:
    raw = (row.get(field) or "").strip()
    if not raw:
        _fail(f"{label}: missing {field}")
    try:
        value = float(raw)
    except ValueError as error:
        _fail(f"{label}: invalid {field}={raw!r}: {error}")
    if not math.isfinite(value):
        _fail(f"{label}: non-finite {field}")
    return value


def _boolean(row: Mapping[str, str], field: str, label: str) -> bool:
    value = _text(row, field, label).lower()
    if value not in {"true", "false"}:
        _fail(f"{label}: invalid boolean {field}={value!r}")
    return value == "true"


def _round(value: float, digits: int) -> float:
    rounded = round(value, digits)
    # Keep a zero from surfacing as negative zero in CSV/JSON text.
    return 0.0 if rounded == 0.0 else rounded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
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


def _validate_statcan_d(
    statcan_rows: Sequence[Mapping[str, str]], summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Treat D as acceptable only inside the known v2.3 allowlist."""
    observed = {
        (row.get("province", "").strip(), field)
        for row in statcan_rows
        for field, value in row.items()
        if (value or "").strip() == "D"
    }
    d_status = summary.get("statcan_d_status") or {}
    allowed = {
        tuple(cell.split(":", 1))
        for cell in d_status.get("allowed_cells", [])
        if ":" in cell
    }
    unexpected = sorted(observed - allowed)
    if d_status.get("unexpected_cells") not in (None, []):
        _fail(
            "v2.3 summary reports unexpected StatCan D cells: "
            f"{d_status['unexpected_cells']}"
        )
    if unexpected:
        _fail(f"unexpected StatCan D status cells: {unexpected}")
    return {
        "allowed_cell_count": len(allowed),
        "observed_d_cell_count": len(observed),
        "unexpected_cells": [],
        "policy": (
            "D is an acceptable-quality flag, not a deletion; unexpected D "
            "cells fail closed."
        ),
    }


def _load_allocation() -> tuple[dict[str, dict[str, str]], float]:
    rows = _read_csv(ALLOCATION_CSV)
    by_province = {_text(row, "province", "provincial allocation"): row for row in rows}
    if len(rows) != 13 or set(by_province) != set(PROVINCES):
        _fail("provincial allocation must contain the 13 canonical jurisdictions")
    total = 0.0
    for province in PROVINCES:
        row = by_province[province]
        volume = _number(row, "harvest_m3", f"allocation {province}")
        if volume < 0:
            _fail(f"allocation {province}: negative volume")
        total += volume
    if abs(total - EXPECTED_ALLOCATION_TOTAL_M3) > ALLOCATION_TOLERANCE_M3:
        _fail(
            f"allocation total {total!r} does not match "
            f"{EXPECTED_ALLOCATION_TOTAL_M3!r}"
        )
    if _number(by_province["NU"], "harvest_m3", "allocation NU") != 0.0:
        _fail("Nunavut allocation must remain zero")
    return by_province, total


def _load_margin_summary() -> dict[tuple[str, str], dict[str, str]]:
    rows = [
        row
        for row in _read_csv(SOURCE_SUMMARY_CSV)
        if (row.get("row_type") or "").strip() == "province"
    ]
    if len(rows) != 26:
        _fail(f"margin summary has {len(rows)} province rows, expected 26")
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        province = _text(row, "province", "margin summary")
        scenario = _text(row, "price_scenario", "margin summary")
        if province not in PROVINCES:
            _fail(f"margin summary has unknown province {province!r}")
        if scenario not in SCENARIOS:
            _fail(f"margin summary has unknown scenario {scenario!r}")
        key = (province, scenario)
        if key in by_key:
            _fail(f"duplicate margin summary row: {key}")
        low = _number(row, "margin_low_cad_m3", f"{key} margin")
        base = _number(row, "margin_base_cad_m3", f"{key} margin")
        high = _number(row, "margin_high_cad_m3", f"{key} margin")
        if not low <= base <= high:
            _fail(f"{key}: low/base/high margins are unordered")
        if row.get("evidence", "").strip() != EVIDENCE:
            _fail(f"{key}: evidence must remain {EVIDENCE}")
        for field in ("observed", "policy_grade", "combined_national_net_value"):
            if (row.get(field) or "").strip().lower() != "false":
                _fail(f"{key}: {field} must remain false")
        by_key[key] = row
    expected = {
        (province, scenario) for province in PROVINCES for scenario in SCENARIOS
    }
    if set(by_key) != expected:
        _fail("margin summary does not cover 13 provinces x 2 scenarios")
    return by_key


def _load_v23_summary() -> dict[str, Any]:
    summary = _read_json(SOURCE_SUMMARY_JSON)
    if summary.get("model_id") != SOURCE_MODEL_ID:
        _fail(f"unexpected source model_id {summary.get('model_id')!r}")
    labels = summary.get("labels") or {}
    if labels.get("evidence") != EVIDENCE:
        _fail("source evidence label changed")
    if labels.get("observed") is not False or labels.get("policy_grade") is not False:
        _fail("source labels must remain unobserved and not policy grade")
    if labels.get("combined_national_net_value") is not False:
        _fail("source must not claim a combined national net value")
    diagnostic = summary.get("national_diagnostic") or {}
    if diagnostic.get("label") != NATIONAL_DIAGNOSTIC_LABEL:
        _fail("source national diagnostic label changed")
    if diagnostic.get("combined_national_net_value") is not False:
        _fail("source national diagnostic must not be a combined net value")
    coverage = summary.get("coverage") or {}
    if coverage.get("province_count") != 13 or coverage.get("missing") != []:
        _fail("source coverage must contain all 13 jurisdictions")
    return summary


def _build_rows(
    allocation: Mapping[str, Mapping[str, str]],
    margins: Mapping[tuple[str, str], Mapping[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        allocation_row = allocation[province]
        volume = _number(allocation_row, "harvest_m3", f"allocation {province}")
        province_name = _text(allocation_row, "province", f"allocation {province}")
        # The allocation CSV carries the canonical name in the 119m source.
        source_name = _text(
            margins[(province, BASE_SCENARIO)],
            "province_name",
            f"margin {province}",
        )
        if province_name != province:
            _fail(f"allocation province field mismatch for {province}")
        for scenario in SCENARIOS:
            source = margins[(province, scenario)]
            margin_low = _number(source, "margin_low_cad_m3", f"{province}/{scenario}")
            margin_base = _number(
                source, "margin_base_cad_m3", f"{province}/{scenario}"
            )
            margin_high = _number(
                source, "margin_high_cad_m3", f"{province}/{scenario}"
            )
            totals = {
                case: _round(volume * value / 1_000_000.0, 6)
                for case, value in (
                    ("low", margin_low),
                    ("base", margin_base),
                    ("high", margin_high),
                )
            }
            if not totals["low"] <= totals["base"] <= totals["high"]:
                _fail(f"{province}/{scenario}: total low/base/high unordered")
            rows.append(
                {
                    "province": province,
                    "province_name": source_name,
                    "scenario": scenario,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "allocation_m3": _round(volume, 1),
                    "margin_low_cad_m3": _round(margin_low, 4),
                    "margin_base_cad_m3": _round(margin_base, 4),
                    "margin_high_cad_m3": _round(margin_high, 4),
                    "total_low_m_cad": totals["low"],
                    "total_base_m_cad": totals["base"],
                    "total_high_m_cad": totals["high"],
                    "share_of_national_total_low": None,
                    "share_of_national_total_base": None,
                    "share_of_national_total_high": None,
                    "evidence": EVIDENCE,
                    "evidence_tier": EVIDENCE,
                    "price_tier": _text(source, "price_tier", province),
                    "cost_tier": _text(source, "cost_tier", province),
                    "negative_low": margin_low < 0.0,
                    "negative_base": margin_base < 0.0,
                    "negative_high": margin_high < 0.0,
                    "negative_flag": margin_base < 0.0,
                    "negative_any_case": any(
                        value < 0.0 for value in (margin_low, margin_base, margin_high)
                    ),
                    "total_negative_low": totals["low"] < 0.0,
                    "total_negative_base": totals["base"] < 0.0,
                    "total_negative_high": totals["high"] < 0.0,
                    "zero_volume": volume == 0.0,
                    "observed": False,
                    "policy_grade": False,
                }
            )
    if len(rows) != 26:
        _fail(f"built {len(rows)} province-scenario rows, expected 26")
    return rows


def _scenario_summaries(
    rows: Sequence[Mapping[str, Any]], allocation_total: float
) -> dict[str, dict[str, Any]]:
    summaries: dict[str, dict[str, Any]] = {}
    for scenario in SCENARIOS:
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        if len(scenario_rows) != 13:
            _fail(f"{scenario}: expected 13 province rows")
        totals = {
            case: _round(
                sum(float(row[f"total_{case}_m_cad"]) for row in scenario_rows),
                6,
            )
            for case in CASES
        }
        per_m3 = {
            case: _round(totals[case] * 1_000_000.0 / allocation_total, 4)
            for case in CASES
        }
        for case in CASES:
            if not totals["low"] <= totals["base"] <= totals["high"]:
                _fail(f"{scenario}: national totals unordered")
        shares: dict[str, dict[str, float]] = {}
        for row in scenario_rows:
            province = row["province"]
            shares[province] = {}
            for case in CASES:
                national_total = totals[case]
                share = (
                    float(row[f"total_{case}_m_cad"]) / national_total
                    if national_total != 0.0
                    else 0.0
                )
                shares[province][case] = _round(share, 10)
                row[f"share_of_national_total_{case}"] = shares[province][case]
        counts = {
            case: Counter(
                "positive"
                if float(row[f"total_{case}_m_cad"]) > 0.0
                else ("negative" if float(row[f"total_{case}_m_cad"]) < 0.0 else "zero")
                for row in scenario_rows
            )
            for case in CASES
        }
        summaries[scenario] = {
            "role": SCENARIO_ROLE[scenario],
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "combined_national_net_value": False,
            "national_total_low_m_cad": totals["low"],
            "national_total_base_m_cad": totals["base"],
            "national_total_high_m_cad": totals["high"],
            "national_margin_low_cad_m3": per_m3["low"],
            "national_margin_base_cad_m3": per_m3["base"],
            "national_margin_high_cad_m3": per_m3["high"],
            "positive_province_count": counts["base"]["positive"],
            "negative_province_count": counts["base"]["negative"],
            "zero_total_province_count": counts["base"]["zero"],
            "counts_by_case": {
                case: {
                    "positive": counts[case]["positive"],
                    "negative": counts[case]["negative"],
                    "zero": counts[case]["zero"],
                }
                for case in CASES
            },
            "negative_provinces_by_case": {
                case: [
                    row["province"]
                    for row in scenario_rows
                    if float(row[f"total_{case}_m_cad"]) < 0.0
                ]
                for case in CASES
            },
            "zero_volume_provinces": [
                row["province"] for row in scenario_rows if row["zero_volume"]
            ],
        }
    return summaries


def _validate_against_source(
    scenario_summaries: Mapping[str, Mapping[str, Any]],
    source_summary: Mapping[str, Any],
) -> dict[str, Any]:
    diagnostic = source_summary.get("national_diagnostic") or {}
    source_scenario_key = {
        BASE_SCENARIO: "donor_flat",
        SENSITIVITY_SCENARIO: "stumpage_scaled_sensitivity",
    }
    deltas: dict[str, float] = {}
    for scenario in SCENARIOS:
        source_values = (diagnostic.get(source_scenario_key[scenario]) or {}).get(
            "119m_allocation_sensitivity"
        ) or {}
        for case in CASES:
            source_value = float(source_values.get(f"margin_{case}_cad_m3", math.nan))
            if not math.isfinite(source_value):
                _fail(f"source 119m diagnostic missing {scenario}/{case}")
            deltas[f"{scenario}_{case}"] = _round(
                abs(
                    float(
                        scenario_summaries[scenario][f"national_margin_{case}_cad_m3"]
                    )
                    - source_value
                ),
                6,
            )
    maximum = max(deltas.values())
    if maximum > MARGIN_TOLERANCE_CAD_M3:
        _fail(
            "computed 119m national margins diverge from the v2.3 diagnostic: "
            f"max delta {maximum}"
        )
    return {
        "source_volume_basis": "119m_allocation_sensitivity",
        "max_abs_delta_cad_m3": maximum,
        "deltas_cad_m3": deltas,
        "checked": True,
    }


def _build_payload(
    rows: Sequence[Mapping[str, Any]],
    scenario_summaries: Mapping[str, Mapping[str, Any]],
    allocation_total: float,
    source_summary: Mapping[str, Any],
    generated_utc: str,
) -> dict[str, Any]:
    source_name = {row["province"]: row["province_name"] for row in rows}
    provinces: list[dict[str, Any]] = []
    for province in PROVINCES:
        province_rows = {
            row["scenario"]: row for row in rows if row["province"] == province
        }
        base = province_rows[BASE_SCENARIO]
        provinces.append(
            {
                "province": province,
                "province_name": source_name[province],
                "allocation_m3": base["allocation_m3"],
                "zero_volume": base["zero_volume"],
                "evidence": EVIDENCE,
                "evidence_tier": EVIDENCE,
                "price_tier": base["price_tier"],
                "cost_tier": base["cost_tier"],
                "observed": False,
                "policy_grade": False,
                "negative_flag": base["negative_flag"],
                "scenarios": {
                    scenario: {
                        "role": SCENARIO_ROLE[scenario],
                        "evidence": EVIDENCE,
                        "margin_low_cad_m3": province_rows[scenario][
                            "margin_low_cad_m3"
                        ],
                        "margin_base_cad_m3": province_rows[scenario][
                            "margin_base_cad_m3"
                        ],
                        "margin_high_cad_m3": province_rows[scenario][
                            "margin_high_cad_m3"
                        ],
                        "total_low_m_cad": province_rows[scenario]["total_low_m_cad"],
                        "total_base_m_cad": province_rows[scenario]["total_base_m_cad"],
                        "total_high_m_cad": province_rows[scenario]["total_high_m_cad"],
                        "share_of_national_total_low": province_rows[scenario][
                            "share_of_national_total_low"
                        ],
                        "share_of_national_total_base": province_rows[scenario][
                            "share_of_national_total_base"
                        ],
                        "share_of_national_total_high": province_rows[scenario][
                            "share_of_national_total_high"
                        ],
                        "negative_flag": province_rows[scenario]["negative_flag"],
                        "negative_any_case": province_rows[scenario][
                            "negative_any_case"
                        ],
                        "total_negative_low": province_rows[scenario][
                            "total_negative_low"
                        ],
                        "total_negative_base": province_rows[scenario][
                            "total_negative_base"
                        ],
                        "total_negative_high": province_rows[scenario][
                            "total_negative_high"
                        ],
                        "zero_volume": province_rows[scenario]["zero_volume"],
                        "observed": False,
                        "policy_grade": False,
                    }
                    for scenario in SCENARIOS
                },
            }
        )
    if len(provinces) != 13:
        _fail("province payload does not contain 13 jurisdictions")

    cross_check = _validate_against_source(scenario_summaries, source_summary)
    province_shares = {
        province["province"]: {
            scenario: province["scenarios"][scenario]["share_of_national_total_base"]
            for scenario in SCENARIOS
        }
        for province in provinces
    }
    national_diagnostic = {
        "label": NATIONAL_DIAGNOSTIC_LABEL,
        "not_policy_result": True,
        "observed": False,
        "policy_grade": False,
        "combined_national_net_value": False,
        "ranking_performed": False,
        "volume_basis": "119m_allocation",
        "allocation_total_m3": _round(allocation_total, 1),
        "donor_flat": {
            key: scenario_summaries[BASE_SCENARIO][key]
            for key in (
                "national_total_low_m_cad",
                "national_total_base_m_cad",
                "national_total_high_m_cad",
                "national_margin_low_cad_m3",
                "national_margin_base_cad_m3",
                "national_margin_high_cad_m3",
                "positive_province_count",
                "negative_province_count",
                "zero_total_province_count",
            )
        },
        "stumpage_scaled": {
            key: scenario_summaries[SENSITIVITY_SCENARIO][key]
            for key in (
                "national_total_low_m_cad",
                "national_total_base_m_cad",
                "national_total_high_m_cad",
                "national_margin_low_cad_m3",
                "national_margin_base_cad_m3",
                "national_margin_high_cad_m3",
                "positive_province_count",
                "negative_province_count",
                "zero_total_province_count",
            )
        },
        "v2_3_119m_cross_check": cross_check,
        "warning": (
            "National totals are volume-weighted model diagnostics at the "
            "119 Mm3 allocation. They are not observed, not policy grade, not "
            "a policy result and not a combined national net value."
        ),
    }
    return {
        "schema_version": "national/margin-totals/1",
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "source_model_id": SOURCE_MODEL_ID,
        "source_artifact_version": source_summary.get("artifact_version"),
        "generated_utc": generated_utc,
        "status": NATIONAL_DIAGNOSTIC_LABEL,
        "label": NATIONAL_DIAGNOSTIC_LABEL,
        "observed": False,
        "policy_grade": False,
        "combined_national_net_value": False,
        "not_policy_result": True,
        "ranking_performed": False,
        "volume_basis": "119m_allocation",
        "allocation_total_m3": _round(allocation_total, 1),
        "province_count": len(provinces),
        "scenario_count": len(SCENARIOS),
        "scenario_order": list(SCENARIOS),
        "scenarios": {
            scenario: dict(scenario_summaries[scenario]) for scenario in SCENARIOS
        },
        "national_diagnostic": national_diagnostic,
        "province_shares": province_shares,
        "provinces": provinces,
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "not_policy_result": True,
            "combined_national_net_value": False,
            "ranking_performed": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "forest_lp_input": False,
            "public_osecan_input": False,
            "strict_open": False,
        },
        "caveats": list(CAVEATS),
        "non_claims": list(NON_CLAIMS),
    }


def _validate_payload(
    payload: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    if payload.get("province_count") != 13:
        _fail("payload province_count must be 13")
    if len(payload.get("provinces", [])) != 13:
        _fail("payload must contain 13 province records")
    if payload.get("scenario_count") != 2:
        _fail("payload must contain two scenarios")
    if payload.get("combined_national_net_value") is not False:
        _fail("payload must not claim a combined national net value")
    if payload.get("observed") is not False or payload.get("policy_grade") is not False:
        _fail("payload labels must remain false")
    if payload.get("national_diagnostic", {}).get("label") != (
        NATIONAL_DIAGNOSTIC_LABEL
    ):
        _fail("national diagnostic label must remain model_estimate_not_policy")

    for scenario in SCENARIOS:
        summary = payload["scenarios"][scenario]
        scenario_rows = [row for row in rows if row["scenario"] == scenario]
        for case in CASES:
            computed = sum(float(row[f"total_{case}_m_cad"]) for row in scenario_rows)
            stated = float(summary[f"national_total_{case}_m_cad"])
            if abs(computed - stated) > TOTAL_TOLERANCE_M_CAD:
                _fail(
                    f"{scenario}/{case}: national total does not equal the province sum"
                )
            per_m3 = stated * 1_000_000.0 / float(payload["allocation_total_m3"])
            if abs(per_m3 - float(summary[f"national_margin_{case}_cad_m3"])) > (
                MARGIN_TOLERANCE_CAD_M3
            ):
                _fail(f"{scenario}/{case}: national per-m3 is not weighted")
        share_sum = sum(
            float(row["scenarios"][scenario]["share_of_national_total_base"])
            for row in payload["provinces"]
        )
        if abs(share_sum - 1.0) > 1e-6:
            _fail(f"{scenario}: province shares of the base total do not sum to 1")
        negative = [
            row["province"]
            for row in payload["provinces"]
            if float(row["scenarios"][scenario]["total_base_m_cad"]) < 0.0
        ]
        if summary["negative_province_count"] != len(negative):
            _fail(f"{scenario}: negative province count mismatch")
    nu = next(row for row in payload["provinces"] if row["province"] == "NU")
    if nu["allocation_m3"] != 0.0 or not nu["zero_volume"]:
        _fail("Nunavut must remain a zero-volume province")
    for row in payload["provinces"]:
        for scenario in SCENARIOS:
            values = row["scenarios"][scenario]
            if not (
                values["total_low_m_cad"]
                <= values["total_base_m_cad"]
                <= values["total_high_m_cad"]
            ):
                _fail(f"{row['province']}/{scenario}: totals unordered")
    return {
        "province_count": payload["province_count"],
        "scenario_count": payload["scenario_count"],
        "allocation_total_m3": payload["allocation_total_m3"],
        "national_equals_province_sum": True,
        "shares_sum_to_one": True,
        "negative_totals_retained": True,
        "nu_zero_volume": True,
        "observed_claim": False,
        "policy_grade_claim": False,
        "combined_national_net_value": False,
        "ranking_performed": False,
    }


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    field: (
                        str(row[field]).lower()
                        if isinstance(row[field], bool)
                        else row[field]
                    )
                    for field in CSV_FIELDS
                }
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_report(
    out_dir: Path,
    payload: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    cross_check: Mapping[str, Any],
) -> None:
    by_key = {(row["province"], row["scenario"]): row for row in rows}
    lines = [
        "# National margin totals at the 119 Mm3 allocation",
        "",
        f"Generated: {payload['generated_utc']}",
        "",
        "## Purpose and labels",
        "",
        (
            "This artifact multiplies the committed v2.3 model margins by the "
            "committed 119 Mm3 provincial allocation. It is a "
            "`model_estimate_not_policy` diagnostic: `observed=false`, "
            "`policy_grade=false`, and `combined_national_net_value=false`. "
            "It is not a policy result."
        ),
        "",
        "## National totals",
        "",
        "| Scenario | Role | Low (M CAD) | Base (M CAD) | High (M CAD) | Base CAD/m3 | Positive / negative / zero provinces (base) |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for scenario in SCENARIOS:
        summary = payload["scenarios"][scenario]
        lines.append(
            f"| {scenario} | {summary['role']} | "
            f"{summary['national_total_low_m_cad']:.6f} | "
            f"{summary['national_total_base_m_cad']:.6f} | "
            f"{summary['national_total_high_m_cad']:.6f} | "
            f"{summary['national_margin_base_cad_m3']:.4f} | "
            f"{summary['positive_province_count']} / "
            f"{summary['negative_province_count']} / "
            f"{summary['zero_total_province_count']} |"
        )
    lines += [
        "",
        (
            f"Allocation total: **{payload['allocation_total_m3']:,.1f} m3**. "
            "Nunavut is held at zero volume and therefore zero total in both "
            "scenarios; its unit margin is not clipped or reclassified."
        ),
        "",
        "## Province totals and shares",
        "",
    ]
    for scenario in SCENARIOS:
        lines += [
            f"### {scenario} ({SCENARIO_ROLE[scenario]})",
            "",
            "| Province | Allocation (m3) | Margin base (CAD/m3) | Total low | Total base | Total high | Base share | Negative base |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
        for province in PROVINCES:
            row = by_key[(province, scenario)]
            lines.append(
                f"| {province} | {row['allocation_m3']:,.1f} | "
                f"{row['margin_base_cad_m3']:.4f} | "
                f"{row['total_low_m_cad']:.6f} | "
                f"{row['total_base_m_cad']:.6f} | "
                f"{row['total_high_m_cad']:.6f} | "
                f"{row['share_of_national_total_base']:.6f} | "
                f"{str(row['negative_flag']).lower()} |"
            )
        negative_by_case = payload["scenarios"][scenario]["negative_provinces_by_case"]
        lines += [
            "",
            (
                "Negative provinces by case: "
                f"low {', '.join(negative_by_case['low']) or 'none'}; "
                f"base {', '.join(negative_by_case['base']) or 'none'}; "
                f"high {', '.join(negative_by_case['high']) or 'none'}."
            ),
            "",
        ]
    lines += [
        "## Negative and zero-volume handling",
        "",
        (
            "Negative margins and negative totals are retained unclipped. "
            "`negative_flag` marks a negative base unit margin; the case flags "
            "remain available in the CSV. Nunavut's zero allocation makes its "
            "total zero, which is a multiplication result, not an observed "
            "zero-profit finding."
        ),
        "",
        "## Cross-check against v2.3",
        "",
        (
            "The computed 119 Mm3 national unit margins match the v2.3 "
            "`119m_allocation_sensitivity` diagnostic. Maximum absolute "
            f"difference: **{cross_check['max_abs_delta_cad_m3']:.6f} CAD/m3**."
        ),
        "",
        "## Caveats",
        "",
    ]
    lines += [f"- {item}" for item in payload["caveats"]]
    lines += [
        "",
        "## Non-claims",
        "",
    ]
    lines += [f"- {item}" for item in payload["non_claims"]]
    lines += [
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python -m workflows.national.harvest.national_margin_totals",
        "```",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(
    out_dir: Path,
    payload: Mapping[str, Any],
    validation: Mapping[str, Any],
    cross_check: Mapping[str, Any],
    generated_utc: str,
) -> None:
    outputs = [PROVINCE_CSV, TOTAL_JSON, REPORT_MD]
    inputs = [
        SOURCE_SUMMARY_CSV,
        SOURCE_SUMMARY_JSON,
        ALLOCATION_CSV,
        STATCAN_CSV,
    ]
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "source_model_id": SOURCE_MODEL_ID,
        "source_artifact_version": payload.get("source_artifact_version"),
        "generated_utc": generated_utc,
        "parent_commit": _git_commit(REPO_ROOT),
        "data_commit": _git_commit(REPO_ROOT / "data"),
        "observation_type": EVIDENCE,
        "observed": False,
        "policy_grade": False,
        "combined_national_net_value": False,
        "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        "not_policy_result": True,
        "ranking_performed": False,
        "inputs": [
            {
                "path": _display_path(path),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in inputs
        ],
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
            "cross_check_v2_3": cross_check,
            "all_values_model_estimate": True,
            "statcan_unexpected_d_cells": 0,
        },
    }
    _write_json(out_dir / MANIFEST_JSON, manifest)


def write_outputs(
    rows: Sequence[Mapping[str, Any]],
    payload: Mapping[str, Any],
    out_dir: Path = DEFAULT_OUT_DIR,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / PROVINCE_CSV, rows)
    _write_json(out_dir / TOTAL_JSON, payload)


def build(
    out_dir: Path = DEFAULT_OUT_DIR, *, check_deletions: bool = True
) -> dict[str, Any]:
    """Build the national margin totals bundle and return its JSON payload."""
    if check_deletions:
        assert_no_unexpected_deletions()
    source_summary = _load_v23_summary()
    statcan_d = _validate_statcan_d(_read_csv(STATCAN_CSV), source_summary)
    allocation, allocation_total = _load_allocation()
    margins = _load_margin_summary()
    rows = _build_rows(allocation, margins)
    scenario_summaries = _scenario_summaries(rows, allocation_total)
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    payload = _build_payload(
        rows,
        scenario_summaries,
        allocation_total,
        source_summary,
        generated_utc,
    )
    validation = _validate_payload(payload, rows)
    validation["statcan_d"] = statcan_d
    cross_check = payload["national_diagnostic"]["v2_3_119m_cross_check"]

    out_dir.mkdir(parents=True, exist_ok=True)
    write_outputs(rows, payload, out_dir)
    _write_report(out_dir, payload, rows, cross_check)
    _write_manifest(out_dir, payload, validation, cross_check, generated_utc)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build province and national margin totals at 119 Mm3."
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--no-deletion-check",
        action="store_true",
        help="skip the fail-closed git deletion check",
    )
    args = parser.parse_args(argv)
    payload = build(args.out_dir, check_deletions=not args.no_deletion_check)
    donor = payload["scenarios"][BASE_SCENARIO]
    scaled = payload["scenarios"][SENSITIVITY_SCENARIO]
    print(
        "national margin totals: "
        f"donor_flat={donor['national_total_base_m_cad']:.6f} M CAD "
        f"({donor['positive_province_count']}+/"
        f"{donor['negative_province_count']}-), "
        f"stumpage_scaled={scaled['national_total_base_m_cad']:.6f} M CAD "
        f"({scaled['positive_province_count']}+/"
        f"{scaled['negative_province_count']}-), "
        f"label={payload['label']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
