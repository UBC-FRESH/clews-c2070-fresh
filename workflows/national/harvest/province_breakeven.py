"""Province break-even delivered price from the national economics v2.3 model.

The break-even price is the central modeled total delivered cost per cubic
metre in ``national_economics_v2_3``:

* BC and QC keep the v2.3 component stack.  Each component enters the total
  exactly once; no additional road or silviculture cost is added.
* The ten aggregate-proxy provinces use the single Statistics Canada NAICS
  11331 industry total-cost proxy.  No road, silviculture or haul add-on is
  applied to it.
* Nunavut keeps the explicit v2.2 donor-total fallback.

The artifact compares that break-even with the ``donor_flat`` price
(115.8280 CAD/m3) and the province-specific ``stumpage_scaled`` price.  It is a
model diagnostic only: ``observed=false``, ``policy_grade=false`` and no
combined national net value is produced.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.province_breakeven
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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = [
    "ARTIFACT_VERSION",
    "MANIFEST_JSON",
    "MODEL_ID",
    "PROVINCE_CSV",
    "REPORT_MD",
    "SUMMARY_JSON",
    "ProvinceBreakevenError",
    "build",
    "main",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
SOURCE_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"
SOURCE_COST_CSV = SOURCE_DIR / "national_cost_inputs.csv"
SOURCE_MARGIN_CSV = SOURCE_DIR / "national_margin_estimates.csv"
SOURCE_SUMMARY_CSV = SOURCE_DIR / "national_margin_summary.csv"
SOURCE_SUMMARY_JSON = SOURCE_DIR / "summary.json"
STATCAN_CSV = (
    HARVEST_BASIS_DIR
    / "statcan_logging_cost_proxy"
    / "provincial_logging_cost_proxy.csv"
)
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "province_breakeven"

PROVINCE_CSV = "province_breakeven.csv"
SUMMARY_JSON = "province_breakeven_summary.json"
REPORT_MD = "PROVINCE_BREAKEVEN_REPORT.md"
MANIFEST_JSON = "manifest.json"

MODEL_ID = "province_breakeven_v1"
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
AGGREGATE_PROVINCES = tuple(
    province for province in PROVINCES if province not in ("BC", "QC", "NU")
)

EXPECTED_DONOR_PRICE_CAD_M3 = 115.8280
PRICE_LIMITED_ABS_CAD_M3 = 15.0
PRICE_LIMITED_RATIO = 0.20
MONEY_TOLERANCE_CAD_M3 = 1e-4
WEIGHTED_TOLERANCE_CAD_M3 = 1e-3

EXPECTED_COST_ROWS = 18
EXPECTED_MARGIN_ROWS = 78
EXPECTED_SUMMARY_ROWS = 26

EXPECTED_COMPONENTS = {
    "BC": ("logging_haul", "road", "silviculture"),
    "QC": ("harvest", "haul", "road", "silviculture"),
    "NU": ("total_cost_fallback",),
}
AGGREGATE_COMPONENT = "industry_aggregate_proxy"

CSV_FIELDS = (
    "province",
    "province_name",
    "cost_basis",
    "cost_tier",
    "cost_components",
    "component_count",
    "road_component_included",
    "silviculture_component_included",
    "aggregate_includes_overhead",
    "fallback_used",
    "harvest_denominator_basis",
    "double_count_guard",
    "break_even_low_cad_m3",
    "break_even_price_cad_m3",
    "break_even_high_cad_m3",
    "break_even_range_cad_m3",
    "donor_price_cad_m3",
    "donor_price_basis",
    "donor_price_proxy_method",
    "donor_margin_cad_m3",
    "donor_margin_pct_of_break_even",
    "donor_gap_to_breakeven_cad_m3",
    "donor_margin_class",
    "scaled_price_cad_m3",
    "scaled_margin_cad_m3",
    "scaled_margin_pct_of_break_even",
    "scaled_gap_to_breakeven_cad_m3",
    "scaled_margin_class",
    "scaled_price_basis",
    "scaled_price_proxy_method",
    "price_change_cad_m3",
    "price_change_pct",
    "classification",
    "classification_reason",
    "price_limited_flag",
    "cost_limited_flag",
    "negative_margin_scenarios",
    "observed_2024_volume_m3",
    "allocation_volume_m3",
    "evidence",
    "observed",
    "policy_grade",
    "source_keys",
    "notes",
)

CAVEATS = (
    (
        "The break-even price is a central model estimate, not an observed "
        "delivered price, mill margin or official net value."
    ),
    (
        "BC and QC break-even values are component stacks. BC road is an "
        "appraisal maintenance allowance, not road construction or "
        "amortization. QC components are survey aggregates."
    ),
    (
        "The ten aggregate-proxy provinces use one StatCan NAICS 11331 total "
        "expense per m3. That total already includes overhead, depreciation, "
        "non-logging activity and any in-plant or in-forest hauling performed "
        "by logging establishments; adding a road, silviculture or haul cost "
        "would double count."
    ),
    (
        "Nunavut uses the explicit v2.2 donor-total fallback because the "
        "StatCan value is blank at zero harvest. No StatCan value is "
        "fabricated."
    ),
    (
        "The low/high break-even range is a cost envelope for BC, QC and NU. "
        "Aggregate-proxy provinces have a point estimate because StatCan "
        "publishes no comparable range."
    ),
    (
        "Stumpage_scaled is a method sensitivity, not the base price and not "
        "an observed price. It can fall below the modeled cost."
    ),
    (
        "Negative margins and negative gaps are retained without clipping. "
        "No value is set to zero to hide a negative result."
    ),
)

NON_CLAIMS = (
    "Not observed; observed=false.",
    "Not policy grade; policy_grade=false.",
    "Not a policy result, policy recommendation or policy frontier.",
    "Not a combined national net value.",
    "Not a Forest LP objective or constraint input.",
    "Not a public OSeCAN input.",
    (
        "No jurisdictional merit ranking; the price-sensitivity list is a "
        "diagnostic ordering by distance to break-even."
    ),
)


class ProvinceBreakevenError(RuntimeError):
    """Fail-closed error for the province break-even artifact."""


def _fail(message: str) -> None:
    raise ProvinceBreakevenError(message)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        _fail(f"missing input CSV: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        _fail(f"empty input CSV: {path}")
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        _fail(f"missing input JSON: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        _fail(f"invalid input JSON {path}: {error}")
    if not isinstance(value, dict):
        _fail(f"input JSON is not an object: {path}")
    return value


def _text(row: Mapping[str, str], field: str, label: str) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        _fail(f"{label}: missing {field}")
    return value


def _number(
    row: Mapping[str, str], field: str, label: str, *, allow_blank: bool = False
) -> float | None:
    raw = (row.get(field) or "").strip()
    if not raw:
        if allow_blank:
            return None
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
    """Halt fail-closed on any worktree or index deletion."""
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


@dataclass(frozen=True)
class CostBasis:
    """Trusted province total-cost basis parsed from the v2.3 cost table."""

    province: str
    province_name: str
    cost_basis: str
    cost_tier: str
    components: tuple[str, ...]
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    includes_overhead: bool
    fallback_used: bool
    harvest_denominator_basis: str
    source_keys: str
    source_urls: str
    notes: str

    def __post_init__(self) -> None:
        if self.province not in PROVINCES:
            raise ProvinceBreakevenError(
                f"unknown province in cost basis: {self.province}"
            )
        if not self.components:
            raise ProvinceBreakevenError(
                f"empty component list for {self.province}"
            )
        if len(set(self.components)) != len(self.components):
            raise ProvinceBreakevenError(
                f"duplicate component for {self.province}: {self.components}"
            )
        if not (
            0.0
            < self.low_cad_m3
            <= self.base_cad_m3
            <= self.high_cad_m3
        ):
            raise ProvinceBreakevenError(
                f"unordered cost basis for {self.province}: "
                f"{self.low_cad_m3}, {self.base_cad_m3}, {self.high_cad_m3}"
            )
        if not math.isfinite(self.base_cad_m3):
            raise ProvinceBreakevenError(
                f"non-finite cost basis for {self.province}"
            )


@dataclass(frozen=True)
class PriceObservation:
    """Trusted central price, cost and margin row from v2.3."""

    province: str
    scenario: str
    price_cad_m3: float
    total_cost_cad_m3: float
    margin_cad_m3: float
    price_basis: str
    price_tier: str
    proxy_method: str
    source_keys: str
    source_urls: str

    def __post_init__(self) -> None:
        if self.province not in PROVINCES:
            raise ProvinceBreakevenError(
                f"unknown province in price row: {self.province}"
            )
        if self.scenario not in SCENARIOS:
            raise ProvinceBreakevenError(
                f"unknown price scenario: {self.scenario}"
            )
        for value, label in (
            (self.price_cad_m3, "price"),
            (self.total_cost_cad_m3, "total cost"),
            (self.margin_cad_m3, "margin"),
        ):
            if not math.isfinite(value):
                raise ProvinceBreakevenError(
                    f"non-finite {label} for {self.province}/{self.scenario}"
                )
        if self.price_cad_m3 <= 0.0:
            raise ProvinceBreakevenError(
                f"non-positive price for {self.province}/{self.scenario}"
            )
        if self.total_cost_cad_m3 <= 0.0:
            raise ProvinceBreakevenError(
                f"non-positive total cost for {self.province}/{self.scenario}"
            )
        if not math.isclose(
            self.price_cad_m3 - self.total_cost_cad_m3,
            self.margin_cad_m3,
            abs_tol=MONEY_TOLERANCE_CAD_M3,
        ):
            raise ProvinceBreakevenError(
                f"margin arithmetic violation for "
                f"{self.province}/{self.scenario}"
            )


def _allowed_derived_d_cells(
    source_summary: Mapping[str, Any],
) -> set[tuple[str, str]]:
    d_status = source_summary.get("statcan_d_status") or {}
    allowed = {
        tuple(cell.split(":", 1))
        for cell in d_status.get("allowed_cells", [])
        if ":" in cell
    }
    return {
        (province, "statcan_status")
        for province, column in allowed
        if column == "industry_worst_quality_status"
    }


def _validate_derived_cost_d(
    cost_rows: Sequence[Mapping[str, str]], source_summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail closed on a D in the derived cost table outside the allowlist."""
    observed = {
        (row.get("province", "").strip(), field)
        for row in cost_rows
        for field, value in row.items()
        if (value or "").strip() == "D"
    }
    allowed = _allowed_derived_d_cells(source_summary)
    unexpected = sorted(observed - allowed)
    if unexpected:
        _fail(f"unexpected D status in derived cost inputs: {unexpected}")
    return {
        "observed_derived_d_cells": sorted(
            f"{province}:{field}" for province, field in observed
        ),
        "allowed_derived_d_cells": sorted(
            f"{province}:{field}" for province, field in allowed
        ),
        "unexpected_derived_d_cells": [],
    }


def _validate_statcan_d(
    statcan_rows: Sequence[Mapping[str, str]], source_summary: Mapping[str, Any]
) -> dict[str, Any]:
    """Accept D only inside the v2.3 allowlist; fail on anything new."""
    observed = {
        (row.get("province", "").strip(), field)
        for row in statcan_rows
        for field, value in row.items()
        if (value or "").strip() == "D"
    }
    d_status = source_summary.get("statcan_d_status") or {}
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


def _load_source_summary() -> dict[str, Any]:
    summary = _read_json(SOURCE_SUMMARY_JSON)
    if summary.get("model_id") != SOURCE_MODEL_ID:
        _fail(f"unexpected source model_id {summary.get('model_id')!r}")
    if summary.get("artifact_version") != "2.3.0":
        _fail(f"unexpected source artifact version {summary.get('artifact_version')!r}")
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
    coverage = summary.get("coverage") or {}
    if coverage.get("province_count") != 13 or coverage.get("missing") != []:
        _fail("source coverage must contain all 13 jurisdictions")
    cost_mapping = summary.get("cost_mapping") or {}
    if cost_mapping.get("separate_road_silviculture_haul_added") is not False:
        _fail("v2.3 unexpectedly adds separate road/silviculture/haul cost")
    if cost_mapping.get("includes_overhead_not_logging_haul_breakdown") is not True:
        _fail("v2.3 aggregate overhead flag changed")
    return summary


def _parse_province_cost(
    province: str, rows: Sequence[Mapping[str, str]]
) -> CostBasis:
    if not rows:
        _fail(f"no cost rows for {province}")
    components = tuple(_text(row, "component", f"cost {province}") for row in rows)
    if len(set(components)) != len(components):
        _fail(f"duplicate cost components for {province}: {components}")

    expected = EXPECTED_COMPONENTS.get(province, (AGGREGATE_COMPONENT,))
    if set(components) != set(expected):
        _fail(
            f"unexpected cost components for {province}: {components}; "
            f"expected {expected}"
        )

    expected_basis = {
        "component_sum": ("BC", "QC"),
        "industry_aggregate_proxy": AGGREGATE_PROVINCES,
        "donor_fallback_total": ("NU",),
    }
    for basis, provinces in expected_basis.items():
        if province in provinces:
            cost_basis = basis
            break
    else:
        _fail(f"no cost-basis mapping for {province}")

    first = rows[0]
    province_name = _text(first, "province_name", province)
    cost_tier = _text(first, "cost_tier", province)
    unit = _text(first, "unit", province)
    if unit != "CAD/m3":
        _fail(f"{province}: unexpected cost unit {unit!r}")
    if _text(first, "row_type", province) != "cost_component":
        _fail(f"{province}: unexpected cost row type")

    for row in rows:
        label = f"{province}/{row.get('component', '')}"
        if _text(row, "province", label) != province:
            _fail(f"{label}: province field mismatch")
        if _text(row, "cost_basis", label) != cost_basis:
            _fail(f"{label}: cost basis mismatch")
        if _text(row, "cost_tier", label) != cost_tier:
            _fail(f"{label}: cost tier mismatch")
        for field in (
            "observed",
            "policy_grade",
            "combined_national_net_value",
        ):
            if _text(row, field, label).lower() != "false":
                _fail(f"{label}: {field} must remain false")
        if _text(row, "evidence", label) != EVIDENCE:
            _fail(f"{label}: evidence must remain {EVIDENCE}")

    low = sum(float(_number(row, "low_cad_m3", province) or 0.0) for row in rows)
    base = sum(float(_number(row, "base_cad_m3", province) or 0.0) for row in rows)
    high = sum(float(_number(row, "high_cad_m3", province) or 0.0) for row in rows)

    includes_overhead = _boolean(
        first, "includes_overhead_not_logging_haul_breakdown", province
    )
    fallback_used = _boolean(first, "fallback_used", province)
    expected_overhead = province not in ("BC", "QC")
    if includes_overhead != expected_overhead:
        _fail(f"{province}: unexpected overhead flag")
    if fallback_used != (province == "NU"):
        _fail(f"{province}: unexpected fallback flag")
    for row in rows[1:]:
        if _boolean(
            row, "includes_overhead_not_logging_haul_breakdown", province
        ) != includes_overhead:
            _fail(f"{province}: inconsistent overhead flag")
        if _boolean(row, "fallback_used", province) != fallback_used:
            _fail(f"{province}: inconsistent fallback flag")

    source_keys = ";".join(
        dict.fromkeys(_text(row, "source_key", province) for row in rows)
    )
    source_urls = ";".join(
        dict.fromkeys(_text(row, "source_url", province) for row in rows)
    )
    notes = " ".join(
        dict.fromkeys(_text(row, "notes", province) for row in rows)
    )
    return CostBasis(
        province=province,
        province_name=province_name,
        cost_basis=cost_basis,
        cost_tier=cost_tier,
        components=components,
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        includes_overhead=includes_overhead,
        fallback_used=fallback_used,
        harvest_denominator_basis=(
            _text(first, "harvest_denominator_basis", province)
            if province not in ("BC", "QC")
            else "component_source_basis"
        ),
        source_keys=source_keys,
        source_urls=source_urls,
        notes=notes,
    )


def _load_cost_basis(
    rows: Sequence[Mapping[str, str]],
) -> dict[str, CostBasis]:
    if len(rows) != EXPECTED_COST_ROWS:
        _fail(
            f"v2.3 cost table has {len(rows)} rows, "
            f"expected {EXPECTED_COST_ROWS}"
        )
    grouped: dict[str, list[Mapping[str, str]]] = {p: [] for p in PROVINCES}
    for row in rows:
        province = _text(row, "province", "cost table")
        if province not in grouped:
            _fail(f"unexpected province in cost table: {province!r}")
        grouped[province].append(row)
    missing = [province for province, items in grouped.items() if not items]
    if missing:
        _fail(f"cost table missing provinces: {missing}")
    return {
        province: _parse_province_cost(province, grouped[province])
        for province in PROVINCES
    }


def _load_price_observations() -> dict[tuple[str, str], PriceObservation]:
    rows = _read_csv(SOURCE_MARGIN_CSV)
    base_rows = [
        row
        for row in rows
        if _text(row, "row_type", "margin table") == "margin"
        and _text(row, "scenario_id", "margin table") == "base"
    ]
    expected = len(PROVINCES) * len(SCENARIOS)
    if len(base_rows) != expected:
        _fail(
            f"margin table has {len(base_rows)} central rows, expected {expected}"
        )
    observations: dict[tuple[str, str], PriceObservation] = {}
    for row in base_rows:
        province = _text(row, "province", "margin table")
        scenario = _text(row, "price_scenario", "margin table")
        key = (province, scenario)
        if key in observations:
            _fail(f"duplicate margin row: {key}")
        for field in ("observed", "policy_grade", "combined_national_net_value"):
            if _text(row, field, f"{key}").lower() != "false":
                _fail(f"{key}: {field} must remain false")
        if _text(row, "evidence", f"{key}") != EVIDENCE:
            _fail(f"{key}: evidence must remain {EVIDENCE}")
        if _text(row, "negative_margin_clipped", f"{key}").lower() != "false":
            _fail(f"{key}: negative margins must remain unclipped")
        margin = float(_number(row, "margin_cad_m3", f"{key}") or 0.0)
        expected_status = "negative_documented" if margin < 0.0 else "non_negative"
        if _text(row, "margin_status", f"{key}") != expected_status:
            _fail(f"{key}: margin status mismatch")
        observations[key] = PriceObservation(
            province=province,
            scenario=scenario,
            price_cad_m3=float(_number(row, "price_cad_m3", f"{key}") or 0.0),
            total_cost_cad_m3=float(
                _number(row, "total_cost_cad_m3", f"{key}") or 0.0
            ),
            margin_cad_m3=margin,
            price_basis=_text(row, "price_basis", f"{key}"),
            price_tier=_text(row, "price_tier", f"{key}"),
            proxy_method=_text(row, "proxy_method", f"{key}"),
            source_keys=_text(row, "source_keys", f"{key}"),
            source_urls=_text(row, "source_urls", f"{key}"),
        )
    expected_keys = {
        (province, scenario)
        for province in PROVINCES
        for scenario in SCENARIOS
    }
    if set(observations) != expected_keys:
        _fail("margin table does not cover 13 provinces x 2 scenarios")
    for province in PROVINCES:
        donor = observations[(province, BASE_SCENARIO)]
        if not math.isclose(
            donor.price_cad_m3,
            EXPECTED_DONOR_PRICE_CAD_M3,
            abs_tol=MONEY_TOLERANCE_CAD_M3,
        ):
            _fail(
                f"{province}: donor price {donor.price_cad_m3} is not "
                f"{EXPECTED_DONOR_PRICE_CAD_M3}"
            )
    return observations


def _load_summary_rows() -> dict[tuple[str, str], dict[str, str]]:
    rows = [
        row
        for row in _read_csv(SOURCE_SUMMARY_CSV)
        if _text(row, "row_type", "margin summary") == "province"
    ]
    if len(rows) != EXPECTED_SUMMARY_ROWS:
        _fail(
            f"margin summary has {len(rows)} province rows, "
            f"expected {EXPECTED_SUMMARY_ROWS}"
        )
    by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        province = _text(row, "province", "margin summary")
        scenario = _text(row, "price_scenario", "margin summary")
        key = (province, scenario)
        if key in by_key:
            _fail(f"duplicate margin summary row: {key}")
        for field in (
            "evidence",
            "observed",
            "policy_grade",
            "combined_national_net_value",
        ):
            if _text(row, field, f"{key}").lower() != "false":
                if field == "evidence":
                    if _text(row, field, f"{key}") != EVIDENCE:
                        _fail(f"{key}: evidence must remain {EVIDENCE}")
                else:
                    _fail(f"{key}: {field} must remain false")
        margin = float(_number(row, "margin_base_cad_m3", f"{key}") or 0.0)
        expected_status = "negative_documented" if margin < 0.0 else "non_negative"
        if _text(row, "margin_status", f"{key}") != expected_status:
            _fail(f"{key}: margin summary status mismatch")
        by_key[key] = row
    expected_keys = {
        (province, scenario)
        for province in PROVINCES
        for scenario in SCENARIOS
    }
    if set(by_key) != expected_keys:
        _fail("margin summary does not cover 13 provinces x 2 scenarios")
    return by_key


def _close_enough(margin: float, break_even: float) -> bool:
    return (
        abs(margin) <= PRICE_LIMITED_ABS_CAD_M3
        or abs(margin) / break_even <= PRICE_LIMITED_RATIO
    )


def _margin_class(margin: float, break_even: float) -> str:
    if margin < -PRICE_LIMITED_ABS_CAD_M3:
        return "negative_margin"
    if _close_enough(margin, break_even):
        return "price_limited"
    return "positive_margin"


def _classification(
    donor_margin: float, scaled_margin: float, break_even: float
) -> str:
    if donor_margin < 0.0:
        return "negative_margin"
    if _close_enough(donor_margin, break_even) or _close_enough(
        scaled_margin, break_even
    ):
        return "price_limited"
    if scaled_margin < -PRICE_LIMITED_ABS_CAD_M3:
        return "cost_limited"
    return "positive_margin"


def _classification_reason(classification: str) -> str:
    if classification == "negative_margin":
        return "Donor price is below the modeled break-even."
    if classification == "price_limited":
        return (
            "At least one scenario margin is within "
            f"{PRICE_LIMITED_ABS_CAD_M3:.1f} CAD/m3 or "
            f"{PRICE_LIMITED_RATIO:.0%} of break-even, so a small price "
            "move flips the sign."
        )
    if classification == "cost_limited":
        return (
            "Scaled price is below break-even while the donor price covers "
            "cost; the cost side keeps the scaled case negative."
        )
    return (
        "Both donor and scaled prices exceed break-even outside the "
        "price-limited band."
    )


def _negative_scenarios(
    margins: Mapping[str, float],
) -> str:
    names = [
        scenario
        for scenario in SCENARIOS
        if margins[scenario] < 0.0
    ]
    return ";".join(names) if names else "none"


def _double_count_guard(province: str) -> str:
    if province in ("BC", "QC"):
        return "component_stack_summed_once"
    if province == "NU":
        return "donor_total_fallback_no_addons"
    return "single_industry_aggregate_no_addons"


def _road_included(province: str) -> bool:
    return province in ("BC", "QC", "NU")


def _silviculture_included(province: str) -> bool:
    return province in ("BC", "QC", "NU")


def _build_rows(
    costs: Mapping[str, CostBasis],
    observations: Mapping[tuple[str, str], PriceObservation],
    summary_rows: Mapping[tuple[str, str], Mapping[str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        cost = costs[province]
        donor = observations[(province, BASE_SCENARIO)]
        scaled = observations[(province, SENSITIVITY_SCENARIO)]
        for observation in (donor, scaled):
            if not math.isclose(
                observation.total_cost_cad_m3,
                cost.base_cad_m3,
                abs_tol=MONEY_TOLERANCE_CAD_M3,
            ):
                _fail(
                    f"{province}/{observation.scenario}: margin-table total "
                    "cost does not match the parsed cost basis"
                )
        summary = summary_rows[(province, BASE_SCENARIO)]
        summary_cost = float(
            _number(summary, "total_cost_base_cad_m3", province) or 0.0
        )
        summary_margin = float(
            _number(summary, "margin_base_cad_m3", province) or 0.0
        )
        if not math.isclose(
            summary_cost, cost.base_cad_m3, abs_tol=MONEY_TOLERANCE_CAD_M3
        ):
            _fail(f"{province}: margin summary total cost mismatch")
        if not math.isclose(
            summary_margin, donor.margin_cad_m3, abs_tol=MONEY_TOLERANCE_CAD_M3
        ):
            _fail(f"{province}: margin summary central margin mismatch")

        break_even = cost.base_cad_m3
        donor_margin = donor.price_cad_m3 - break_even
        scaled_margin = scaled.price_cad_m3 - break_even
        classification = _classification(
            donor_margin, scaled_margin, break_even
        )
        donor_class = _margin_class(donor_margin, break_even)
        scaled_class = _margin_class(scaled_margin, break_even)
        observed_volume = float(
            _number(summary, "observed_2024_volume_m3", province) or 0.0
        )
        allocation_volume = float(
            _number(summary, "allocation_volume_m3", province) or 0.0
        )
        rows.append(
            {
                "province": province,
                "province_name": cost.province_name,
                "cost_basis": cost.cost_basis,
                "cost_tier": cost.cost_tier,
                "cost_components": ";".join(cost.components),
                "component_count": len(cost.components),
                "road_component_included": _road_included(province),
                "silviculture_component_included": _silviculture_included(
                    province
                ),
                "aggregate_includes_overhead": cost.includes_overhead,
                "fallback_used": cost.fallback_used,
                "harvest_denominator_basis": cost.harvest_denominator_basis,
                "double_count_guard": _double_count_guard(province),
                "break_even_low_cad_m3": _round(cost.low_cad_m3, 4),
                "break_even_price_cad_m3": _round(break_even, 4),
                "break_even_high_cad_m3": _round(cost.high_cad_m3, 4),
                "break_even_range_cad_m3": _round(
                    cost.high_cad_m3 - cost.low_cad_m3, 4
                ),
                "donor_price_cad_m3": _round(donor.price_cad_m3, 4),
                "donor_price_basis": donor.price_basis,
                "donor_price_proxy_method": donor.proxy_method,
                "donor_margin_cad_m3": _round(donor_margin, 4),
                "donor_margin_pct_of_break_even": _round(
                    donor_margin / break_even * 100.0, 4
                ),
                "donor_gap_to_breakeven_cad_m3": _round(-donor_margin, 4),
                "donor_margin_class": donor_class,
                "scaled_price_cad_m3": _round(scaled.price_cad_m3, 4),
                "scaled_margin_cad_m3": _round(scaled_margin, 4),
                "scaled_margin_pct_of_break_even": _round(
                    scaled_margin / break_even * 100.0, 4
                ),
                "scaled_gap_to_breakeven_cad_m3": _round(-scaled_margin, 4),
                "scaled_margin_class": scaled_class,
                "scaled_price_basis": scaled.price_basis,
                "scaled_price_proxy_method": scaled.proxy_method,
                "price_change_cad_m3": _round(
                    scaled.price_cad_m3 - donor.price_cad_m3, 4
                ),
                "price_change_pct": _round(
                    (scaled.price_cad_m3 - donor.price_cad_m3)
                    / donor.price_cad_m3
                    * 100.0,
                    4,
                ),
                "classification": classification,
                "classification_reason": _classification_reason(
                    classification
                ),
                "price_limited_flag": classification == "price_limited",
                "cost_limited_flag": classification == "cost_limited",
                "negative_margin_scenarios": _negative_scenarios(
                    {
                        BASE_SCENARIO: donor_margin,
                        SENSITIVITY_SCENARIO: scaled_margin,
                    }
                ),
                "observed_2024_volume_m3": _round(observed_volume, 4),
                "allocation_volume_m3": _round(allocation_volume, 4),
                "evidence": EVIDENCE,
                "observed": False,
                "policy_grade": False,
                "source_keys": cost.source_keys,
                "notes": cost.notes,
            }
        )
    if len(rows) != len(PROVINCES):
        _fail(f"built {len(rows)} province rows, expected {len(PROVINCES)}")
    return rows


def _weighted_margin(
    rows: Sequence[Mapping[str, Any]],
    scenario: str,
    volume_field: str,
) -> float:
    numerator = 0.0
    denominator = 0.0
    for row in rows:
        volume = float(row[volume_field])
        numerator += float(row[f"{_scenario_margin_field(scenario)}"]) * volume
        denominator += volume
    if denominator <= 0.0:
        _fail(f"zero volume denominator for {scenario}/{volume_field}")
    return numerator / denominator


def _scenario_margin_field(scenario: str) -> str:
    if scenario == BASE_SCENARIO:
        return "donor_margin_cad_m3"
    if scenario == SENSITIVITY_SCENARIO:
        return "scaled_margin_cad_m3"
    _fail(f"unknown scenario: {scenario}")


def _scenario_summary(
    rows: Sequence[Mapping[str, Any]], scenario: str
) -> dict[str, Any]:
    field = _scenario_margin_field(scenario)
    margins = {row["province"]: float(row[field]) for row in rows}
    classes = Counter(row[f"{_margin_class_field(scenario)}"] for row in rows)
    negative = [
        province for province in PROVINCES if margins[province] < 0.0
    ]
    positive = [
        province for province in PROVINCES if margins[province] > 0.0
    ]
    zero = [
        province for province in PROVINCES if margins[province] == 0.0
    ]
    price_basis_counts = Counter(
        (
            row["scaled_price_basis"]
            if scenario == SENSITIVITY_SCENARIO
            else row["donor_price_basis"]
        )
        for row in rows
    )
    return {
        "role": SCENARIO_ROLE[scenario],
        "price_scenario": scenario,
        "province_count": len(rows),
        "positive_margin_count": len(positive),
        "negative_margin_count": len(negative),
        "zero_margin_count": len(zero),
        "price_limited_count": classes.get("price_limited", 0),
        "margin_class_counts": {
            "positive_margin": classes.get("positive_margin", 0),
            "negative_margin": classes.get("negative_margin", 0),
            "price_limited": classes.get("price_limited", 0),
        },
        "min_margin_cad_m3": _round(min(margins.values()), 4),
        "max_margin_cad_m3": _round(max(margins.values()), 4),
        "mean_margin_cad_m3": _round(
            sum(margins.values()) / len(margins), 4
        ),
        "median_margin_cad_m3": _round(
            sorted(margins.values())[len(margins) // 2], 4
        ),
        "negative_provinces": negative,
        "price_basis_counts": dict(sorted(price_basis_counts.items())),
        "volume_weighted_margin_observed_cad_m3": _round(
            _weighted_margin(rows, scenario, "observed_2024_volume_m3"), 4
        ),
        "volume_weighted_margin_119m_cad_m3": _round(
            _weighted_margin(rows, scenario, "allocation_volume_m3"), 4
        ),
    }


def _margin_class_field(scenario: str) -> str:
    if scenario == BASE_SCENARIO:
        return "donor_margin_class"
    if scenario == SENSITIVITY_SCENARIO:
        return "scaled_margin_class"
    _fail(f"unknown scenario: {scenario}")


def _classification_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(row["classification"] for row in rows)
    return {
        "positive_margin": counts.get("positive_margin", 0),
        "negative_margin": counts.get("negative_margin", 0),
        "price_limited": counts.get("price_limited", 0),
        "cost_limited": counts.get("cost_limited", 0),
    }


def _price_sensitivity(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    buffer = []
    for row in rows:
        break_even = float(row["break_even_price_cad_m3"])
        candidates = {
            BASE_SCENARIO: float(row["donor_margin_cad_m3"]),
            SENSITIVITY_SCENARIO: float(row["scaled_margin_cad_m3"]),
        }
        closest_scenario = min(
            candidates, key=lambda scenario: abs(candidates[scenario])
        )
        closest_margin = candidates[closest_scenario]
        buffer.append(
            {
                "province": row["province"],
                "closest_scenario": closest_scenario,
                "closest_margin_cad_m3": _round(closest_margin, 4),
                "absolute_margin_cad_m3": _round(abs(closest_margin), 4),
                "absolute_margin_pct_of_break_even": _round(
                    abs(closest_margin) / break_even * 100.0, 4
                ),
                "classification": row["classification"],
            }
        )
    buffer.sort(
        key=lambda item: (
            item["absolute_margin_pct_of_break_even"],
            item["absolute_margin_cad_m3"],
            PROVINCE_ORDER[item["province"]],
        )
    )
    swing = sorted(
        (
            {
                "province": row["province"],
                "scaled_price_change_cad_m3": row["price_change_cad_m3"],
                "absolute_price_change_cad_m3": _round(
                    abs(float(row["price_change_cad_m3"])), 4
                ),
                "margin_change_cad_m3": _round(
                    float(row["scaled_margin_cad_m3"])
                    - float(row["donor_margin_cad_m3"]),
                    4,
                ),
                "scaled_margin_cad_m3": row["scaled_margin_cad_m3"],
                "classification": row["classification"],
            }
            for row in rows
        ),
        key=lambda item: (
            -item["absolute_price_change_cad_m3"],
            PROVINCE_ORDER[item["province"]],
        ),
    )
    return {
        "metric": (
            "absolute margin as a share of break-even, using the closer of "
            "donor_flat and stumpage_scaled for each province"
        ),
        "diagnostic_order_not_policy_ranking": True,
        "closest_to_break_even": buffer,
        "largest_scaled_price_reduction": swing,
        "interpretation": (
            "Provinces near the top have the smallest relative buffer to "
            "break-even and are therefore the most exposed to a small price "
            "movement. The list is a diagnostic ordering, not a merit or "
            "policy ranking."
        ),
    }


def _cross_check(
    source_summary: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    observations: Mapping[tuple[str, str], PriceObservation],
) -> dict[str, Any]:
    cost_deltas = {
        province: abs(
            float(rows[PROVINCE_ORDER[province]]["break_even_price_cad_m3"])
            - observations[(province, BASE_SCENARIO)].total_cost_cad_m3
        )
        for province in PROVINCES
    }
    margin_deltas = {
        province: abs(
            float(
                rows[PROVINCE_ORDER[province]]["donor_margin_cad_m3"]
            )
            - observations[(province, BASE_SCENARIO)].margin_cad_m3
        )
        for province in PROVINCES
    }
    diagnostic = source_summary.get("national_diagnostic") or {}
    weighted = {
        "donor_flat_observed_2024_m3": (
            _weighted_margin(rows, BASE_SCENARIO, "observed_2024_volume_m3"),
            float(
                diagnostic["donor_flat"]["observed_2024_m3"][
                    "margin_base_cad_m3"
                ]
            ),
        ),
        "donor_flat_119m_allocation_sensitivity": (
            _weighted_margin(rows, BASE_SCENARIO, "allocation_volume_m3"),
            float(
                diagnostic["donor_flat"]["119m_allocation_sensitivity"][
                    "margin_base_cad_m3"
                ]
            ),
        ),
        "stumpage_scaled_observed_2024_m3": (
            _weighted_margin(
                rows, SENSITIVITY_SCENARIO, "observed_2024_volume_m3"
            ),
            float(
                diagnostic["stumpage_scaled_sensitivity"]["observed_2024_m3"][
                    "margin_base_cad_m3"
                ]
            ),
        ),
        "stumpage_scaled_119m_allocation_sensitivity": (
            _weighted_margin(
                rows, SENSITIVITY_SCENARIO, "allocation_volume_m3"
            ),
            float(
                diagnostic["stumpage_scaled_sensitivity"][
                    "119m_allocation_sensitivity"
                ]["margin_base_cad_m3"]
            ),
        ),
    }
    weighted_deltas = {
        key: abs(computed - stated)
        for key, (computed, stated) in weighted.items()
    }
    maximum_cost = max(cost_deltas.values())
    maximum_margin = max(margin_deltas.values())
    maximum_weighted = max(weighted_deltas.values())
    if maximum_cost > MONEY_TOLERANCE_CAD_M3:
        _fail(f"break-even diverges from v2.3 total cost: {maximum_cost}")
    if maximum_margin > MONEY_TOLERANCE_CAD_M3:
        _fail(f"margin diverges from v2.3: {maximum_margin}")
    if maximum_weighted > WEIGHTED_TOLERANCE_CAD_M3:
        _fail(f"weighted margin diverges from v2.3: {maximum_weighted}")
    return {
        "v2_3_total_cost_match": True,
        "max_abs_total_cost_delta_cad_m3": _round(maximum_cost, 6),
        "max_abs_margin_delta_cad_m3": _round(maximum_margin, 6),
        "max_abs_weighted_margin_delta_cad_m3": _round(maximum_weighted, 6),
        "weighted_margin_values": {
            key: {
                "computed_cad_m3": _round(computed, 4),
                "v2_3_cad_m3": _round(stated, 4),
            }
            for key, (computed, stated) in weighted.items()
        },
        "cost_row_counts": {
            "total": EXPECTED_COST_ROWS,
            "component": 7,
            "aggregate": len(AGGREGATE_PROVINCES),
            "fallback": 1,
        },
        "double_count_guards": {
            "bc_qc_component_sums_used_once": True,
            "aggregate_has_no_separate_road": True,
            "aggregate_has_no_separate_silviculture": True,
            "aggregate_has_no_separate_haul": True,
            "nu_no_addons": True,
        },
    }


def _build_payload(
    rows: Sequence[Mapping[str, Any]],
    source_summary: Mapping[str, Any],
    cross_check: Mapping[str, Any],
    statcan_d: Mapping[str, Any],
    derived_cost_d: Mapping[str, Any],
    generated_utc: str,
) -> dict[str, Any]:
    scenarios = {
        scenario: _scenario_summary(rows, scenario) for scenario in SCENARIOS
    }
    provinces = [
        {
            key: row[key]
            for key in CSV_FIELDS
            if key not in ("evidence",)
        }
        for row in rows
    ]
    for province in provinces:
        province["evidence"] = EVIDENCE
        province["observed"] = False
        province["policy_grade"] = False
    return {
        "schema_version": "national/province-breakeven/1",
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "source_model_id": SOURCE_MODEL_ID,
        "source_artifact_version": source_summary.get("artifact_version"),
        "generated_utc": generated_utc,
        "status": "model_estimate",
        "label": NATIONAL_DIAGNOSTIC_LABEL,
        "observed": False,
        "policy_grade": False,
        "combined_national_net_value": False,
        "not_policy_result": True,
        "ranking_performed": False,
        "province_count": len(rows),
        "scenario_count": len(SCENARIOS),
        "base_scenario": BASE_SCENARIO,
        "sensitivity_scenario": SENSITIVITY_SCENARIO,
        "donor_price_cad_m3": EXPECTED_DONOR_PRICE_CAD_M3,
        "break_even_definition": (
            "Central modeled total delivered cost per m3 from "
            "national_economics_v2_3. BC/QC component stacks are summed once; "
            "aggregate provinces use one total-cost proxy; NU uses the donor "
            "fallback total. No additional cost is added to the central total."
        ),
        "gap_sign_convention": (
            "margin = price - break_even; gap_to_breakeven = "
            "break_even - price = -margin"
        ),
        "classification_rule": {
            "categories": [
                "positive_margin",
                "negative_margin",
                "price_limited",
                "cost_limited",
            ],
            "price_limited_absolute_cad_m3": PRICE_LIMITED_ABS_CAD_M3,
            "price_limited_ratio_of_break_even": PRICE_LIMITED_RATIO,
            "negative_margin_threshold_cad_m3": 0.0,
            "description": (
                "negative_margin when the donor margin is below zero; "
                "price_limited when either scenario margin is within 15 "
                "CAD/m3 or 20% of break-even; cost_limited when the scaled "
                "margin is below -15 CAD/m3 while the donor margin is "
                "non-negative; otherwise positive_margin."
            ),
        },
        "scenarios": scenarios,
        "classification_counts": _classification_counts(rows),
        "negative_margin_provinces": {
            scenario: scenarios[scenario]["negative_provinces"]
            for scenario in SCENARIOS
        },
        "provinces": provinces,
        "price_sensitivity": _price_sensitivity(rows),
        "cross_checks": {
            **cross_check,
            "statcan_unexpected_d_cells": statcan_d["unexpected_cells"],
            "statcan_observed_d_cell_count": statcan_d[
                "observed_d_cell_count"
            ],
            "statcan_allowed_d_cell_count": statcan_d["allowed_cell_count"],
            "derived_cost_d": dict(derived_cost_d),
        },
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
        _fail("province_count must be 13")
    if len(payload.get("provinces", [])) != 13:
        _fail("payload must contain 13 province records")
    if payload.get("scenario_count") != 2:
        _fail("payload must contain two scenarios")
    if payload.get("observed") is not False:
        _fail("observed must remain false")
    if payload.get("policy_grade") is not False:
        _fail("policy_grade must remain false")
    if payload.get("combined_national_net_value") is not False:
        _fail("combined_national_net_value must remain false")
    if payload.get("not_policy_result") is not True:
        _fail("not_policy_result must remain true")
    if payload.get("ranking_performed") is not False:
        _fail("ranking_performed must remain false")
    if payload.get("label") != NATIONAL_DIAGNOSTIC_LABEL:
        _fail("label must remain model_estimate_not_policy")

    for row in rows:
        province = row["province"]
        break_even = float(row["break_even_price_cad_m3"])
        donor_price = float(row["donor_price_cad_m3"])
        donor_margin = float(row["donor_margin_cad_m3"])
        scaled_margin = float(row["scaled_margin_cad_m3"])
        if not math.isclose(
            donor_price - break_even,
            donor_margin,
            abs_tol=MONEY_TOLERANCE_CAD_M3,
        ):
            _fail(f"{province}: donor margin arithmetic failed")
        if not math.isclose(
            -donor_margin,
            float(row["donor_gap_to_breakeven_cad_m3"]),
            abs_tol=MONEY_TOLERANCE_CAD_M3,
        ):
            _fail(f"{province}: donor gap arithmetic failed")
        if not math.isclose(
            -scaled_margin,
            float(row["scaled_gap_to_breakeven_cad_m3"]),
            abs_tol=MONEY_TOLERANCE_CAD_M3,
        ):
            _fail(f"{province}: scaled gap arithmetic failed")
        if not (
            float(row["break_even_low_cad_m3"])
            <= break_even
            <= float(row["break_even_high_cad_m3"])
        ):
            _fail(f"{province}: break-even range is unordered")
        if row["classification"] not in (
            "positive_margin",
            "negative_margin",
            "price_limited",
            "cost_limited",
        ):
            _fail(f"{province}: unknown classification")
        if row["negative_margin_scenarios"] != "none" and not any(
            value < 0.0 for value in (donor_margin, scaled_margin)
        ):
            _fail(f"{province}: negative scenario label without a negative margin")
        if row["observed"] is not False or row["policy_grade"] is not False:
            _fail(f"{province}: labels must remain false")

    negative_scaled = [
        row
        for row in rows
        if float(row["scaled_margin_cad_m3"]) < 0.0
    ]
    stated_negative = payload["scenarios"][SENSITIVITY_SCENARIO][
        "negative_margin_count"
    ]
    if len(negative_scaled) != stated_negative:
        _fail("scaled negative-margin count does not match the scenario summary")
    if not negative_scaled:
        _fail("expected retained negative scaled margins in the v2.3 model")
    for row in rows:
        has_negative = any(
            float(row[field]) < 0.0
            for field in ("donor_margin_cad_m3", "scaled_margin_cad_m3")
        )
        labelled_negative = row["negative_margin_scenarios"] != "none"
        if has_negative != labelled_negative:
            _fail(f"{row['province']}: negative scenario label mismatch")

    labels = " ".join(
        str(value)
        for row in payload["provinces"]
        for value in row.values()
    ).lower()
    if "not_estimated" in labels:
        _fail("not_estimated marker present in province output")
    if sum(payload["classification_counts"].values()) != len(rows):
        _fail("classification counts do not cover all provinces")
    return {
        "province_count": 13,
        "scenario_count": 2,
        "all_rows_model_estimate": True,
        "observed_claim": False,
        "policy_grade_claim": False,
        "combined_national_net_value": False,
        "ranking_performed": False,
        "negative_scaled_margin_count": len(negative_scaled),
        "negative_margins_retained": True,
        "cost_basis_matches_v2_3": True,
        "double_count_checked": True,
        "all_13_present": True,
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
) -> None:
    by_province = {row["province"]: row for row in rows}
    lines = [
        "# Province break-even delivered price",
        "",
        f"Generated: {payload['generated_utc']}",
        "",
        "## Purpose and labels",
        "",
        (
            "This artifact computes a central break-even delivered price for "
            "the 13 Canadian jurisdictions from the committed v2.3 national "
            "cost model. It is a `model_estimate_not_policy` diagnostic: "
            "`observed=false`, `policy_grade=false` and "
            "`combined_national_net_value=false`. It is not a policy result."
        ),
        "",
        (
            "Break-even = central modeled total delivered cost in CAD/m3. "
            "Margin = price - break-even. Gap to break-even = "
            "break-even - price. Negative values are retained unclipped."
        ),
        "",
        "## Cost basis",
        "",
        "| Province | Cost basis | Components | Low | Central | High | Double-count guard |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for province in PROVINCES:
        row = by_province[province]
        lines.append(
            f"| {province} | `{row['cost_basis']}` | "
            f"{row['cost_components']} | "
            f"{row['break_even_low_cad_m3']:.4f} | "
            f"{row['break_even_price_cad_m3']:.4f} | "
            f"{row['break_even_high_cad_m3']:.4f} | "
            f"`{row['double_count_guard']}` |"
        )
    lines += [
        "",
        (
            "BC and QC sum their v2.3 component stacks once. The ten "
            "aggregate-proxy provinces use one StatCan NAICS 11331 total-cost "
            "proxy and receive no separate road, silviculture or haul add-on. "
            "Nunavut uses the explicit v2.2 donor-total fallback."
        ),
        "",
        "## Break-even and price comparison",
        "",
        "| Province | Break-even | Donor price | Donor margin | Donor gap | Donor class | Scaled price | Scaled margin | Scaled gap | Scaled class | Classification |",
        "|---|---:|---:|---:|---:|---|---:|---:|---:|---|---|",
    ]
    for province in PROVINCES:
        row = by_province[province]
        lines.append(
            f"| {province} | {row['break_even_price_cad_m3']:.4f} | "
            f"{row['donor_price_cad_m3']:.4f} | "
            f"{row['donor_margin_cad_m3']:.4f} | "
            f"{row['donor_gap_to_breakeven_cad_m3']:.4f} | "
            f"`{row['donor_margin_class']}` | "
            f"{row['scaled_price_cad_m3']:.4f} | "
            f"{row['scaled_margin_cad_m3']:.4f} | "
            f"{row['scaled_gap_to_breakeven_cad_m3']:.4f} | "
            f"`{row['scaled_margin_class']}` | "
            f"**`{row['classification']}`** |"
        )
    counts = payload["classification_counts"]
    lines += [
        "",
        (
            f"Classification counts: positive_margin "
            f"{counts['positive_margin']}, negative_margin "
            f"{counts['negative_margin']}, price_limited "
            f"{counts['price_limited']}, cost_limited "
            f"{counts['cost_limited']}. The price-limited band is "
            f"{PRICE_LIMITED_ABS_CAD_M3:.1f} CAD/m3 or "
            f"{PRICE_LIMITED_RATIO:.0%} of break-even. A province can be "
            "negative under one scenario and still be classified by its "
            "binding constraint; the negative-margin province lists below "
            "use the raw margin signs."
        ),
        "",
        "## Donor versus scaled",
        "",
        (
            "The donor_flat price is 115.8280 CAD/m3 for every province. The "
            "stumpage_scaled sensitivity is province-specific; it equals the "
            "donor price where no provincial stumpage rate is available and "
            "uses the BC delivered/BCTS ratio otherwise. The largest scaled "
            "price reductions are:"
        ),
        "",
        "| Province | Scaled price | Price change vs donor | Margin change | Scaled margin | Classification |",
        "|---|---:|---:|---:|---:|---|",
    ]
    sensitivity = payload["price_sensitivity"]
    for item in sensitivity["largest_scaled_price_reduction"][:5]:
        row = by_province[item["province"]]
        lines.append(
            f"| {item['province']} | {row['scaled_price_cad_m3']:.4f} | "
            f"{item['scaled_price_change_cad_m3']:.4f} | "
            f"{item['margin_change_cad_m3']:.4f} | "
            f"{item['scaled_margin_cad_m3']:.4f} | "
            f"`{item['classification']}` |"
        )
    lines += [
        "",
        (
            "Negative-margin provinces under the scaled sensitivity: "
            f"{', '.join(payload['negative_margin_provinces'][SENSITIVITY_SCENARIO]) or 'none'}. "
            "Negative-margin provinces under the donor price: "
            f"{', '.join(payload['negative_margin_provinces'][BASE_SCENARIO]) or 'none'}."
        ),
        "",
        "## Price sensitivity",
        "",
        (
            "The diagnostic orders provinces by the smallest absolute margin "
            "as a share of break-even across the two scenarios. A province at "
            "the top has the least buffer, so a small price movement can flip "
            "its sign. This is not a merit or policy ranking."
        ),
        "",
        "| Diagnostic order | Province | Closest scenario | Absolute margin | % of break-even | Classification |",
        "|---:|---|---|---:|---:|---|",
    ]
    for index, item in enumerate(
        sensitivity["closest_to_break_even"][:8], start=1
    ):
        lines.append(
            f"| {index} | {item['province']} | "
            f"`{item['closest_scenario']}` | "
            f"{item['absolute_margin_cad_m3']:.4f} | "
            f"{item['absolute_margin_pct_of_break_even']:.2f}% | "
            f"`{item['classification']}` |"
        )
    cross = payload["cross_checks"]
    lines += [
        "",
        "## Arithmetic and v2.3 cross-checks",
        "",
        (
            "Every province break-even is recomputed from "
            "`national_cost_inputs.csv`, not copied from the margin summary. "
            "The recomputed central cost matches the v2.3 margin-table total "
            "cost with a maximum absolute difference of "
            f"**{cross['max_abs_total_cost_delta_cad_m3']:.6f} CAD/m3**. "
            "The donor/scaled margins match with a maximum absolute "
            f"difference of **{cross['max_abs_margin_delta_cad_m3']:.6f} "
            "CAD/m3**."
        ),
        "",
        (
            "The volume-weighted central margins also match the v2.3 national "
            "diagnostic: donor_flat on observed 2024 harvest "
            f"**{cross['weighted_margin_values']['donor_flat_observed_2024_m3']['computed_cad_m3']:.4f}** "
            "CAD/m3; stumpage_scaled on observed 2024 harvest "
            f"**{cross['weighted_margin_values']['stumpage_scaled_observed_2024_m3']['computed_cad_m3']:.4f}** "
            "CAD/m3. The 119 Mm3 sensitivity values are reported separately "
            "in the JSON."
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
        ".venv/bin/python -m workflows.national.harvest.province_breakeven",
        "```",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(
    out_dir: Path,
    payload: Mapping[str, Any],
    validation: Mapping[str, Any],
    generated_utc: str,
) -> None:
    outputs = [PROVINCE_CSV, SUMMARY_JSON, REPORT_MD]
    inputs = [
        SOURCE_COST_CSV,
        SOURCE_MARGIN_CSV,
        SOURCE_SUMMARY_CSV,
        SOURCE_SUMMARY_JSON,
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
            "all_values_model_estimate": True,
            "cost_basis_matches_v2_3": True,
            "negative_margins_unclipped": True,
            "statcan_unexpected_d_cells": 0,
        },
    }
    _write_json(out_dir / MANIFEST_JSON, manifest)


def build(
    out_dir: Path = DEFAULT_OUT_DIR, *, check_deletions: bool = True
) -> dict[str, Any]:
    """Build the province break-even bundle and return its JSON payload."""
    if check_deletions:
        assert_no_unexpected_deletions()
    source_summary = _load_source_summary()
    statcan_d = _validate_statcan_d(_read_csv(STATCAN_CSV), source_summary)
    cost_input_rows = _read_csv(SOURCE_COST_CSV)
    derived_cost_d = _validate_derived_cost_d(cost_input_rows, source_summary)
    costs = _load_cost_basis(cost_input_rows)
    observations = _load_price_observations()
    summary_rows = _load_summary_rows()
    rows = _build_rows(costs, observations, summary_rows)
    cross_check = _cross_check(source_summary, rows, observations)
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    payload = _build_payload(
        rows,
        source_summary,
        cross_check,
        statcan_d,
        derived_cost_d,
        generated_utc,
    )
    validation = _validate_payload(payload, rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / PROVINCE_CSV, rows)
    _write_json(out_dir / SUMMARY_JSON, payload)
    _write_report(out_dir, payload, rows)
    _write_manifest(out_dir, payload, validation, generated_utc)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build province break-even delivered prices from v2.3."
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--no-deletion-check",
        action="store_true",
        help="skip the fail-closed git deletion check",
    )
    args = parser.parse_args(argv)
    payload = build(args.out_dir, check_deletions=not args.no_deletion_check)
    closest = payload["price_sensitivity"]["closest_to_break_even"][0]
    print(
        "province break-even: "
        f"donor={payload['scenarios'][BASE_SCENARIO]['negative_margin_count']} "
        "negative, "
        f"scaled={payload['scenarios'][SENSITIVITY_SCENARIO]['negative_margin_count']} "
        "negative, "
        f"closest={closest['province']} "
        f"({closest['absolute_margin_pct_of_break_even']:.2f}% buffer), "
        f"label={payload['label']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
