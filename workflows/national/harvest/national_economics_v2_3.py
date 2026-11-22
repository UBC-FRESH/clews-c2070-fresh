"""National provincial economics v2.3 with StatCan aggregate cost proxies.

The v2.1/v2.2 price method is unchanged: ``donor_flat`` is the base scenario
and ``stumpage_scaled`` is the sensitivity.  Version 2.3 changes the cost
mapping for the eleven jurisdictions without BC/QC component costs:

* BC keeps the v2.2 component stack (open logging/haul proxy, IAM road
  maintenance allowance, Interior silviculture allowance).
* QC keeps the v2.2 component stack (MRNF T20 harvest, haul and road; BC
  silviculture donor).
* Every other province uses the Statistics Canada NAICS 11331 industry
  aggregate expense per m3 as a **total cost proxy**.  No separate road,
  silviculture or haul cost is added to it, which avoids double counting.
  Low/base/high are the same point proxy because StatCan publishes no
  uncertainty range; no range is invented.
* Nunavut has a blank StatCan denominator (zero reported harvest).  It keeps
  an explicit donor-total fallback from v2.2; no StatCan value is fabricated.

The StatCan proxy denominator is ``observed_2024_m3``.  The 119 Mm3 allocation
ratio is carried only as a sensitivity column.  Volume-weighted summaries and
coverage use observed 2024 harvest as the primary denominator and report the
119 Mm3 allocation as a separate sensitivity.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_v2_3
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import subprocess
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.harvest import national_economics_v1 as v1
from workflows.national.harvest import national_economics_v2 as v2
from workflows.national.harvest import national_economics_v2_2 as v2_2

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
V1_DIR = HARVEST_BASIS_DIR / "national_economics_v1"
V21_DIR = HARVEST_BASIS_DIR / "national_economics_v2_1"
V22_DIR = HARVEST_BASIS_DIR / "national_economics_v2_2"
STATCAN_DIR = HARVEST_BASIS_DIR / "statcan_logging_cost_proxy"
STATCAN_CSV = STATCAN_DIR / "provincial_logging_cost_proxy.csv"
STATCAN_SUMMARY_JSON = STATCAN_DIR / "summary.json"
STATCAN_SOURCES_CSV = STATCAN_DIR / "sources.csv"
ALLOCATION_CSV = v1.PROVINCIAL_ALLOCATION_CSV
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_economics_v2_3"

PROVINCES = v1.PROVINCES
PROVINCE_NAMES = v1.PROVINCE_NAMES

MODEL_ID = "national_economics_v2_3"
ARTIFACT_VERSION = "2.3.0"

BASE_SCENARIO = "donor_flat"
SENSITIVITY_SCENARIO = "stumpage_scaled"
SCENARIOS = (BASE_SCENARIO, SENSITIVITY_SCENARIO)
SCENARIO_ROLE = {
    BASE_SCENARIO: "base",
    SENSITIVITY_SCENARIO: "sensitivity",
}
SCENARIO_ORDER = {BASE_SCENARIO: 0, SENSITIVITY_SCENARIO: 1}

COST_INPUTS_CSV = "national_cost_inputs.csv"
MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "v2_2_v2_3_comparison.csv"
TIER_CSV = "tier_coverage.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NATIONAL_ECONOMICS_V2_3_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"
STRICT_OPEN = "false"

STATCAN_SOURCE_KEY = "statcan_16100114"
STATCAN_SOURCE_TITLE = (
    "Statistics Canada Table 16-10-0114-01 NAICS 11331 logging "
    "industry aggregate expenses"
)
STATCAN_SOURCE_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/16100114-eng.zip"
STATCAN_SOURCE_YEAR = "2024"
V22_SOURCE_KEY = "national_economics_v2_2"

TIER_COMPONENT = "component_cost"
TIER_AGGREGATE = "industry_aggregate_proxy"
TIER_DONOR_FALLBACK = "donor_fallback_total"
TIER_BC_MIX = "component_mix_partial_direct"
TIER_QC_MIX = "component_survey_aggregate"

COST_BASIS_COMPONENT = "component_sum"
COST_BASIS_AGGREGATE = "industry_aggregate_proxy"
COST_BASIS_FALLBACK = "donor_fallback_total"

VOLUME_BASIS_OBSERVED = "observed_2024_m3"
VOLUME_BASIS_119M = "119m_allocation_sensitivity"
VOLUME_BASES = (VOLUME_BASIS_OBSERVED, VOLUME_BASIS_119M)

DIRECT_OR_SURVEY = frozenset({TIER_BC_MIX, TIER_QC_MIX})
AGGREGATE_PROVINCES = tuple(
    province for province in PROVINCES if province not in ("BC", "QC", "NU")
)
COMPONENT_PROVINCES = ("BC", "QC")
FALLBACK_PROVINCES = ("NU",)

RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

# D is an acceptable-quality flag, not a deletion.  Every D in the committed
# StatCan proxy is listed here so a new/reclassified D fails closed.
EXPECTED_D_CELLS = frozenset(
    {
        ("AB", "salaries_wages_status"),
        ("AB", "energy_fuel_status"),
        ("AB", "contract_logging_salaries_wages_status"),
        ("AB", "contract_logging_energy_fuel_status"),
        ("AB", "industry_worst_quality_status"),
        ("AB", "contract_worst_quality_status"),
        ("MB", "materials_supplies_status"),
        ("MB", "industry_worst_quality_status"),
        ("ON", "salaries_wages_status"),
        ("ON", "energy_fuel_status"),
        ("ON", "contract_logging_materials_supplies_status"),
        ("ON", "industry_worst_quality_status"),
        ("QC", "salaries_wages_status"),
        ("QC", "contract_logging_salaries_wages_status"),
        ("QC", "contract_logging_materials_supplies_status"),
        ("NB", "contract_logging_salaries_wages_status"),
    }
)

STATCAN_REQUIRED_COLUMNS = (
    "province",
    "province_name",
    "reference_year",
    "statcan_table",
    "statcan_naics_code",
    "statcan_naics_label",
    "harvest_basis",
    "harvest_m3",
    "harvest_is_observed_2024",
    "scenario_119m_harvest_m3",
    "total_expenses_cad",
    "industry_expense_cad_m3",
    "scenario_119m_industry_expense_cad_m3",
    "industry_worst_quality_status",
    "data_quality_flags",
    "proxy_status",
    "fallback_used",
    "observed",
    "policy_grade",
    "price_basis",
    "not_logging_haul_breakdown",
    "caveat",
)


class NationalEconomicsV23Error(ValueError):
    """Raised when an input or result violates the v2.3 contract."""


@dataclass(frozen=True)
class AggregateCost:
    """One total-cost proxy for a province with no component breakdown."""

    province: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    cost_basis: str
    source_key: str
    source: str
    source_url: str
    source_year: str
    tier: str
    includes_overhead: bool
    fallback_used: bool
    harvest_denominator_m3: float
    harvest_denominator_basis: str
    scenario_119m_cad_m3: float | None
    statcan_status: str
    data_quality_flags: str
    notes: str

    def __post_init__(self) -> None:
        if not (
            0.0 < self.low_cad_m3 <= self.base_cad_m3 <= self.high_cad_m3
        ):
            raise NationalEconomicsV23Error(
                f"unordered aggregate cost for {self.province}: "
                f"{self.low_cad_m3}, {self.base_cad_m3}, {self.high_cad_m3}"
            )
        if not math.isfinite(self.base_cad_m3):
            raise NationalEconomicsV23Error(
                f"non-finite aggregate cost for {self.province}"
            )
        if self.harvest_denominator_m3 < 0.0:
            raise NationalEconomicsV23Error(
                f"negative harvest denominator for {self.province}"
            )


@dataclass(frozen=True)
class PriceRow:
    """Parsed price proxy row used by the margin builder."""

    province: str
    scenario: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    row: Mapping[str, Any]


def _fmt(value: float, digits: int = 4) -> str:
    if not math.isfinite(value):
        raise NationalEconomicsV23Error(f"non-finite value: {value}")
    return f"{value:.{digits}f}"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise NationalEconomicsV23Error(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise NationalEconomicsV23Error(f"empty input table: {path}")
    return rows


def _require_columns(
    rows: Sequence[Mapping[str, str]], required: Iterable[str], source: Path
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise NationalEconomicsV23Error(
            f"{source} is missing columns: {sorted(missing)}"
        )


def _number(
    row: Mapping[str, str], column: str, context: str, *, allow_blank: bool = False
) -> float | None:
    raw = row.get(column, "").strip()
    if not raw:
        if allow_blank:
            return None
        raise NationalEconomicsV23Error(f"missing {column} in {context}")
    try:
        value = float(raw)
    except ValueError as error:
        raise NationalEconomicsV23Error(
            f"non-numeric {column} in {context}: {raw!r}"
        ) from error
    if not math.isfinite(value):
        raise NationalEconomicsV23Error(
            f"non-finite {column} in {context}: {value}"
        )
    return value


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise NationalEconomicsV23Error(f"refusing to write empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _deletion_lines(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repository), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise NationalEconomicsV23Error(
            f"git status failed in {repository}: {result.stderr.strip()}"
        )
    return [
        line
        for line in result.stdout.splitlines()
        if line and (line[0] == "D" or line[1] == "D")
    ]


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any unexpected ``D`` in parent or data status."""
    for repository in (REPO_ROOT, REPO_ROOT / "data"):
        deletions = _deletion_lines(repository)
        if deletions:
            raise NationalEconomicsV23Error(
                f"unexpected deletion in {repository}: {deletions[0]}"
            )


def _load_statcan_proxy() -> dict[str, dict[str, str]]:
    rows = _read_csv(STATCAN_CSV)
    _require_columns(rows, STATCAN_REQUIRED_COLUMNS, STATCAN_CSV)
    by_province = {row["province"]: row for row in rows}
    if len(rows) != len(PROVINCES) or set(by_province) != set(PROVINCES):
        raise NationalEconomicsV23Error(
            "StatCan proxy must have exactly one row per province"
        )
    _validate_statcan_d_statuses(by_province)
    for province, row in by_province.items():
        if row["statcan_naics_code"] != "11331":
            raise NationalEconomicsV23Error(
                f"unexpected StatCan NAICS for {province}: {row['statcan_naics_code']}"
            )
        if row["harvest_is_observed_2024"] != "true":
            raise NationalEconomicsV23Error(
                f"StatCan proxy for {province} does not use observed 2024 harvest"
            )
        if row["observed"] != "false" or row["policy_grade"] != "false":
            raise NationalEconomicsV23Error(
                f"StatCan proxy label violation for {province}"
            )
        if row["fallback_used"] != "false":
            raise NationalEconomicsV23Error(
                f"StatCan proxy fallback label violation for {province}"
            )
    return by_province


def _validate_statcan_d_statuses(rows: Mapping[str, Mapping[str, str]]) -> None:
    """Fail closed when a D appears in a cell outside the known allowlist."""
    observed = {
        (province, column)
        for province, row in rows.items()
        for column, value in row.items()
        if value == "D"
    }
    unexpected = sorted(observed - EXPECTED_D_CELLS)
    if unexpected:
        raise NationalEconomicsV23Error(
            f"unexpected StatCan D status cells: {unexpected}"
        )


def _load_allocation_reference() -> dict[str, dict[str, str]]:
    rows = _read_csv(ALLOCATION_CSV)
    _require_columns(
        rows, ("province", "harvest_m3", "observed_2024_m3"), ALLOCATION_CSV
    )
    by_province = {row["province"]: row for row in rows}
    missing = set(PROVINCES) - set(by_province)
    if missing:
        raise NationalEconomicsV23Error(
            f"allocation missing provinces: {sorted(missing)}"
        )
    return by_province


def _observed_volumes(
    allocation_rows: Mapping[str, Mapping[str, str]]
) -> dict[str, float]:
    volumes = {
        province: float(
            _number(row, "observed_2024_m3", province) or 0.0
        )
        for province, row in allocation_rows.items()
    }
    if sum(volumes.values()) <= 0.0:
        raise NationalEconomicsV23Error("observed 2024 volume sum is zero")
    return volumes


def _allocation_volumes(
    allocation_rows: Mapping[str, Mapping[str, str]]
) -> dict[str, float]:
    return {
        province: float(_number(row, "harvest_m3", province) or 0.0)
        for province, row in allocation_rows.items()
    }


def _build_aggregate_costs(
    statcan_rows: Mapping[str, Mapping[str, str]],
    observed_volumes: Mapping[str, float],
    fallback_components: Mapping[str, Mapping[str, v2_2.CostComponent]],
) -> dict[str, AggregateCost]:
    aggregate: dict[str, AggregateCost] = {}
    for province in AGGREGATE_PROVINCES:
        row = statcan_rows[province]
        value = _number(
            row, "industry_expense_cad_m3", f"StatCan aggregate {province}"
        )
        if value is None or value <= 0.0:
            raise NationalEconomicsV23Error(
                f"non-positive StatCan aggregate for {province}: {value}"
            )
        if row["proxy_status"] != "available":
            raise NationalEconomicsV23Error(
                f"StatCan aggregate not available for {province}: "
                f"{row['proxy_status']}"
            )
        observed_harvest = _number(
            row, "harvest_m3", f"StatCan harvest denominator {province}"
        )
        if observed_harvest is None or not math.isclose(
            observed_harvest, observed_volumes[province], abs_tol=1e-6
        ):
            raise NationalEconomicsV23Error(
                f"StatCan denominator mismatch for {province}: "
                f"{observed_harvest} vs {observed_volumes[province]}"
            )
        scenario_119m = _number(
            row,
            "scenario_119m_industry_expense_cad_m3",
            f"StatCan 119m sensitivity {province}",
            allow_blank=True,
        )
        aggregate[province] = AggregateCost(
            province=province,
            low_cad_m3=value,
            base_cad_m3=value,
            high_cad_m3=value,
            cost_basis=COST_BASIS_AGGREGATE,
            source_key=STATCAN_SOURCE_KEY,
            source=STATCAN_SOURCE_TITLE,
            source_url=STATCAN_SOURCE_URL,
            source_year=STATCAN_SOURCE_YEAR,
            tier=TIER_AGGREGATE,
            includes_overhead=True,
            fallback_used=False,
            harvest_denominator_m3=observed_harvest,
            harvest_denominator_basis=VOLUME_BASIS_OBSERVED,
            scenario_119m_cad_m3=scenario_119m,
            statcan_status=row["industry_worst_quality_status"],
            data_quality_flags=row["data_quality_flags"],
            notes=(
                "NAICS 11331 total expenses divided by observed 2024 harvest. "
                "Industry aggregate proxy: includes administration, "
                "depreciation, overhead, non-logging activity and any in-forest "
                "hauling performed by logging establishments. It is not a "
                "logging or haul breakdown and does not include a separate "
                "road or silviculture add-on; adding one would double count."
            ),
        )
    nu_row = statcan_rows["NU"]
    if nu_row["industry_expense_cad_m3"].strip():
        raise NationalEconomicsV23Error(
            "Nunavut StatCan aggregate unexpectedly present; review fallback"
        )
    components = fallback_components["NU"]
    low = sum(component.low_cad_m3 for component in components.values())
    base = sum(component.base_cad_m3 for component in components.values())
    high = sum(component.high_cad_m3 for component in components.values())
    aggregate["NU"] = AggregateCost(
        province="NU",
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        cost_basis=COST_BASIS_FALLBACK,
        source_key=V22_SOURCE_KEY,
        source="National economics v2.2 donor total fallback",
        source_url="data/derived/national/harvest_basis/national_economics_v2_2",
        source_year="2026",
        tier=TIER_DONOR_FALLBACK,
        includes_overhead=True,
        fallback_used=True,
        harvest_denominator_m3=0.0,
        harvest_denominator_basis="undefined_zero_harvest_denominator",
        scenario_119m_cad_m3=None,
        statcan_status="undefined_zero_harvest_denominator",
        data_quality_flags=nu_row["data_quality_flags"],
        notes=(
            "StatCan aggregate is blank because both reported expenses and the "
            "2024 harvest denominator are zero. Explicit donor-total fallback "
            "from v2.2 BC logging/haul + QC road + BC silviculture; no StatCan "
            "value is fabricated. NU has zero modelled allocation volume."
        ),
    )
    return aggregate


def _component_case_total(
    component_inputs: Mapping[str, Mapping[str, v2_2.CostComponent]],
    province: str,
    case: str,
) -> float:
    components = component_inputs[province].values()
    return sum(getattr(component, f"{case}_cad_m3") for component in components)


def _component_case_parts(
    component_inputs: Mapping[str, Mapping[str, v2_2.CostComponent]],
    province: str,
    case: str,
) -> dict[str, float]:
    inputs = component_inputs[province]
    if province == "QC":
        harvest = getattr(inputs["harvest"], f"{case}_cad_m3")
        haul = getattr(inputs["haul"], f"{case}_cad_m3")
        return {
            "harvest": harvest,
            "haul": haul,
            "logging_haul": harvest + haul,
            "road": getattr(inputs["road"], f"{case}_cad_m3"),
            "silviculture": getattr(inputs["silviculture"], f"{case}_cad_m3"),
        }
    return {
        "harvest": 0.0,
        "haul": 0.0,
        "logging_haul": getattr(inputs["logging_haul"], f"{case}_cad_m3"),
        "road": getattr(inputs["road"], f"{case}_cad_m3"),
        "silviculture": getattr(inputs["silviculture"], f"{case}_cad_m3"),
    }


def _cost_tier(province: str) -> str:
    if province == "BC":
        return TIER_BC_MIX
    if province == "QC":
        return TIER_QC_MIX
    if province in FALLBACK_PROVINCES:
        return TIER_DONOR_FALLBACK
    return TIER_AGGREGATE


def _cost_input_rows(
    component_inputs: Mapping[str, Mapping[str, v2_2.CostComponent]],
    aggregate: Mapping[str, AggregateCost],
) -> list[dict[str, Any]]:
    order = {"harvest": 0, "haul": 1, "logging_haul": 1, "road": 2, "silviculture": 3}
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        if province in COMPONENT_PROVINCES:
            components = sorted(
                component_inputs[province].values(),
                key=lambda item: (order[item.component], item.component),
            )
            for component in components:
                rows.append(
                    {
                        "row_type": "cost_component",
                        "province": province,
                        "province_name": PROVINCE_NAMES[province],
                        "component": component.component,
                        "unit": "CAD/m3",
                        "low_cad_m3": _fmt(component.low_cad_m3),
                        "base_cad_m3": _fmt(component.base_cad_m3),
                        "high_cad_m3": _fmt(component.high_cad_m3),
                        "cost_basis": COST_BASIS_COMPONENT,
                        "cost_tier": _cost_tier(province),
                        "source_key": component.source_key,
                        "source": component.source,
                        "source_url": component.source_url,
                        "source_year": component.source_year,
                        "tier": component.tier,
                        "direct_series_status": component.direct_series_status,
                        "donor_province": component.donor_province,
                        "replacement_status": "component_retained_v2_2",
                        "includes_overhead_not_logging_haul_breakdown": "false",
                        "fallback_used": "false",
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "evidence": EVIDENCE,
                        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                        "reference_year": component.source_year,
                        "harvest_denominator_m3": "",
                        "harvest_denominator_basis": "",
                        "statcan_status": "",
                        "statcan_data_quality_flags": "",
                        "statcan_119m_sensitivity_cad_m3": "",
                        "notes": component.notes,
                    }
                )
            continue
        cost = aggregate[province]
        rows.append(
            {
                "row_type": "cost_component",
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "component": (
                    "total_cost_fallback"
                    if province in FALLBACK_PROVINCES
                    else "industry_aggregate_proxy"
                ),
                "unit": "CAD/m3",
                "low_cad_m3": _fmt(cost.low_cad_m3),
                "base_cad_m3": _fmt(cost.base_cad_m3),
                "high_cad_m3": _fmt(cost.high_cad_m3),
                "cost_basis": cost.cost_basis,
                "cost_tier": cost.tier,
                "source_key": cost.source_key,
                "source": cost.source,
                "source_url": cost.source_url,
                "source_year": cost.source_year,
                "tier": cost.tier,
                "direct_series_status": (
                    "explicit_donor_fallback_statcan_blank"
                    if province in FALLBACK_PROVINCES
                    else "industry_aggregate_proxy_not_direct"
                ),
                "donor_province": "BC;QC" if province in FALLBACK_PROVINCES else "",
                "replacement_status": (
                    "explicit_fallback_retained"
                    if province in FALLBACK_PROVINCES
                    else "aggregate_proxy_upgraded"
                ),
                "includes_overhead_not_logging_haul_breakdown": (
                    "true" if cost.includes_overhead else "false"
                ),
                "fallback_used": str(cost.fallback_used).lower(),
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "evidence": EVIDENCE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "reference_year": cost.source_year,
                "harvest_denominator_m3": (
                    _fmt(cost.harvest_denominator_m3)
                    if cost.harvest_denominator_m3 > 0.0
                    else ""
                ),
                "harvest_denominator_basis": cost.harvest_denominator_basis,
                "statcan_status": cost.statcan_status,
                "statcan_data_quality_flags": cost.data_quality_flags,
                "statcan_119m_sensitivity_cad_m3": (
                    _fmt(cost.scenario_119m_cad_m3)
                    if cost.scenario_119m_cad_m3 is not None
                    else ""
                ),
                "notes": cost.notes,
            }
        )
    return rows


def _price_rows() -> list[dict[str, Any]]:
    blend = v1.load_bc_price_blend()
    refs = v2._references()
    ratio = v2._ratio(blend)
    rows = v2_2._price_rows(v2.build_prices(refs, blend, ratio))
    for row in rows:
        row["row_type"] = "price_proxy"
        row["notes"] = str(row["notes"]).replace("v2.2", "v2.3")
    return rows


def _margin_rows(
    price_rows: Sequence[Mapping[str, Any]],
    component_inputs: Mapping[str, Mapping[str, v2_2.CostComponent]],
    aggregate: Mapping[str, AggregateCost],
    allocation: Mapping[str, float],
    observed: Mapping[str, float],
) -> list[dict[str, Any]]:
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in price_rows
    }
    refs = v2._references()
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        for scenario in SCENARIOS:
            price = price_by_key[(province, scenario)]
            for scenario_id, order, price_key, cost_case, note in (
                (
                    "low",
                    0,
                    "price_low_cad_m3",
                    "high",
                    "Conservative envelope: low price proxy minus high cost.",
                ),
                ("base", 1, "price_base_cad_m3", "base", "Central proxy case."),
                (
                    "high",
                    2,
                    "price_high_cad_m3",
                    "low",
                    "Optimistic envelope: high price proxy minus low cost.",
                ),
            ):
                price_value = float(price[price_key])
                if province in COMPONENT_PROVINCES:
                    parts = _component_case_parts(
                        component_inputs, province, cost_case
                    )
                    total_cost = (
                        parts["logging_haul"]
                        + parts["road"]
                        + parts["silviculture"]
                    )
                    aggregate_value = None
                    formula = (
                        f"{price_key} - logging_haul_{cost_case} - "
                        f"road_{cost_case} - silviculture_{cost_case}"
                    )
                else:
                    cost = aggregate[province]
                    parts = {
                        "harvest": 0.0,
                        "haul": 0.0,
                        "logging_haul": 0.0,
                        "road": 0.0,
                        "silviculture": 0.0,
                    }
                    total_cost = getattr(cost, f"{cost_case}_cad_m3")
                    aggregate_value = total_cost
                    formula = f"{price_key} - {cost.cost_basis}_{cost_case}"
                margin = price_value - total_cost
                if not math.isfinite(margin):
                    raise NationalEconomicsV23Error(
                        f"non-finite margin for {province}/{scenario}/{scenario_id}"
                    )
                if province in COMPONENT_PROVINCES:
                    cost_basis = COST_BASIS_COMPONENT
                else:
                    cost_basis = aggregate[province].cost_basis
                rows.append(
                    {
                        "row_type": "margin",
                        "province": province,
                        "province_name": PROVINCE_NAMES[province],
                        "price_scenario": scenario,
                        "scenario_id": scenario_id,
                        "scenario_order": order,
                        "price_cad_m3": _fmt(price_value),
                        "harvest_cad_m3": (
                            _fmt(parts["harvest"])
                            if province in COMPONENT_PROVINCES
                            else ""
                        ),
                        "haul_cad_m3": (
                            _fmt(parts["haul"])
                            if province in COMPONENT_PROVINCES
                            else ""
                        ),
                        "logging_haul_cad_m3": (
                            _fmt(parts["logging_haul"])
                            if province in COMPONENT_PROVINCES
                            else ""
                        ),
                        "road_cad_m3": (
                            _fmt(parts["road"])
                            if province in COMPONENT_PROVINCES
                            else ""
                        ),
                        "silviculture_cad_m3": (
                            _fmt(parts["silviculture"])
                            if province in COMPONENT_PROVINCES
                            else ""
                        ),
                        "industry_aggregate_proxy_cad_m3": (
                            _fmt(aggregate_value)
                            if aggregate_value is not None
                            else ""
                        ),
                        "total_cost_cad_m3": _fmt(total_cost),
                        "margin_cad_m3": _fmt(margin),
                        "margin_formula": formula,
                        "margin_status": (
                            "negative_documented"
                            if margin < 0.0
                            else "non_negative"
                        ),
                        "price_tier": price["price_tier"],
                        "price_basis": price["price_basis"],
                        "proxy_method": price["proxy_method"],
                        "stumpage_rate_type": refs[province].rate_type,
                        "cost_basis": cost_basis,
                        "cost_tier": _cost_tier(province),
                        "includes_overhead_not_logging_haul_breakdown": (
                            "true" if province not in COMPONENT_PROVINCES else "false"
                        ),
                        "fallback_used": (
                            "true" if province in FALLBACK_PROVINCES else "false"
                        ),
                        "harvest_denominator_basis": (
                            VOLUME_BASIS_OBSERVED
                            if province not in COMPONENT_PROVINCES
                            else "component_source_basis"
                        ),
                        "allocation_volume_m3": _fmt(allocation[province]),
                        "observed_2024_volume_m3": _fmt(observed[province]),
                        "volume_weighted_margin_observed_cad_m3": _fmt(
                            margin * observed[province]
                        ),
                        "volume_weighted_margin_119m_cad_m3": _fmt(
                            margin * allocation[province]
                        ),
                        "negative_margin_clipped": "false",
                        "source_keys": (
                            f"{price['source_keys']};{STATCAN_SOURCE_KEY}"
                            if province not in COMPONENT_PROVINCES
                            else price["source_keys"]
                        ),
                        "source": price["source_keys"],
                        "source_urls": (
                            f"{price['source_urls']};{STATCAN_SOURCE_URL}"
                            if province not in COMPONENT_PROVINCES
                            else price["source_urls"]
                        ),
                        "evidence": EVIDENCE,
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "net_value_status": NET_VALUE_STATUS,
                        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                        "notes": note,
                    }
                )
    return rows


def _weighted(
    values: Mapping[str, float], volumes: Mapping[str, float]
) -> float:
    total = sum(volumes.values())
    if total <= 0.0:
        raise NationalEconomicsV23Error("volume denominator sum is zero")
    return sum(values[province] * volumes[province] for province in PROVINCES) / total


def _summary_rows(
    margins: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
    observed: Mapping[str, float],
) -> list[dict[str, Any]]:
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    total_observed = sum(observed.values())
    total_allocation = sum(allocation.values())
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for province in PROVINCES:
            low = by_key[(province, scenario, "low")]
            base = by_key[(province, scenario, "base")]
            high = by_key[(province, scenario, "high")]
            rows.append(
                {
                    "row_type": "province",
                    "volume_basis": VOLUME_BASIS_OBSERVED,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "observed_2024_volume_m3": _fmt(observed[province]),
                    "allocation_volume_m3": _fmt(allocation[province]),
                    "volume_share_observed": _fmt(observed[province] / total_observed, 8),
                    "volume_share_119m": _fmt(
                        allocation[province] / total_allocation, 8
                    ),
                    "price_low_cad_m3": low["price_cad_m3"],
                    "price_base_cad_m3": base["price_cad_m3"],
                    "price_high_cad_m3": high["price_cad_m3"],
                    "price_basis": base["price_basis"],
                    "price_tier": base["price_tier"],
                    "cost_basis": base["cost_basis"],
                    "cost_tier": base["cost_tier"],
                    "total_cost_base_cad_m3": base["total_cost_cad_m3"],
                    "margin_low_cad_m3": low["margin_cad_m3"],
                    "margin_base_cad_m3": base["margin_cad_m3"],
                    "margin_high_cad_m3": high["margin_cad_m3"],
                    "margin_status": (
                        "negative_documented"
                        if float(base["margin_cad_m3"]) < 0.0
                        else "non_negative"
                    ),
                    "includes_overhead_not_logging_haul_breakdown": base[
                        "includes_overhead_not_logging_haul_breakdown"
                    ],
                    "fallback_used": base["fallback_used"],
                    "national_diagnostic_label": "",
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "Province row. Volume shares use observed 2024 harvest; "
                        "the 119 Mm3 allocation is reported separately as a "
                        "sensitivity."
                    ),
                }
            )
        for volume_basis, volumes in (
            (VOLUME_BASIS_OBSERVED, observed),
            (VOLUME_BASIS_119M, allocation),
        ):
            values = {
                case: _weighted(
                    {
                        province: float(
                            by_key[(province, scenario, case)]["margin_cad_m3"]
                        )
                        for province in PROVINCES
                    },
                    volumes,
                )
                for case in ("low", "base", "high")
            }
            costs = _weighted(
                {
                    province: float(
                        by_key[(province, scenario, "base")]["total_cost_cad_m3"]
                    )
                    for province in PROVINCES
                },
                volumes,
            )
            rows.append(
                {
                    "row_type": "national",
                    "volume_basis": volume_basis,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": "NATIONAL",
                    "province_name": "Canada",
                    "observed_2024_volume_m3": _fmt(sum(observed.values())),
                    "allocation_volume_m3": _fmt(sum(allocation.values())),
                    "volume_share_observed": "1.00000000",
                    "volume_share_119m": "1.00000000",
                    "price_low_cad_m3": "",
                    "price_base_cad_m3": "",
                    "price_high_cad_m3": "",
                    "price_basis": "volume_weighted",
                    "price_tier": "mixed",
                    "cost_basis": "mixed_component_and_aggregate",
                    "cost_tier": "mixed",
                    "total_cost_base_cad_m3": _fmt(costs),
                    "margin_low_cad_m3": _fmt(values["low"]),
                    "margin_base_cad_m3": _fmt(values["base"]),
                    "margin_high_cad_m3": _fmt(values["high"]),
                    "margin_status": (
                        "negative_documented"
                        if values["base"] < 0.0
                        else "non_negative"
                    ),
                    "includes_overhead_not_logging_haul_breakdown": "mixed",
                    "fallback_used": "mixed",
                    "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "Volume-weighted diagnostic on observed 2024 harvest "
                        "(primary). The 119 Mm3 allocation row is a sensitivity."
                        if volume_basis == VOLUME_BASIS_OBSERVED
                        else "Volume-weighted sensitivity using the 119 Mm3 "
                        "screening allocation; not the primary denominator."
                    ),
                }
            )
    return rows


def _comparison_rows(
    margins: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
    observed: Mapping[str, float],
) -> list[dict[str, Any]]:
    before_rows = _read_csv(V22_DIR / MARGIN_CSV)
    before = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in before_rows
    }
    current = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for province in PROVINCES:
            before_margin = {
                case: float(before[(province, scenario, case)]["margin_cad_m3"])
                for case in ("low", "base", "high")
            }
            current_margin = {
                case: float(current[(province, scenario, case)]["margin_cad_m3"])
                for case in ("low", "base", "high")
            }
            before_cost = float(
                before[(province, scenario, "base")]["total_cost_cad_m3"]
            )
            current_cost = float(
                current[(province, scenario, "base")]["total_cost_cad_m3"]
            )
            if province == "BC":
                reason = (
                    "BC component costs are unchanged from v2.2; the delta is "
                    "zero. No StatCan aggregate is applied to BC."
                )
            elif province == "QC":
                reason = (
                    "QC component costs are unchanged from v2.2; the delta is "
                    "zero. No StatCan aggregate is applied to QC."
                )
            elif province == "NU":
                reason = (
                    "NU StatCan is blank; the explicit v2.2 donor-total "
                    "fallback is retained and the delta is zero."
                )
            else:
                reason = (
                    "Donor cost stack replaced by the StatCan NAICS 11331 "
                    "industry aggregate total-cost proxy. This is a cost-method "
                    "change, not an observed market change; no road, "
                    "silviculture or haul add-on is applied."
                )
            rows.append(
                {
                    "row_type": "province",
                    "volume_basis": VOLUME_BASIS_OBSERVED,
                    "price_scenario": scenario,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "v2_2_margin_low_cad_m3": _fmt(before_margin["low"]),
                    "v2_2_margin_base_cad_m3": _fmt(before_margin["base"]),
                    "v2_2_margin_high_cad_m3": _fmt(before_margin["high"]),
                    "v2_3_margin_low_cad_m3": _fmt(current_margin["low"]),
                    "v2_3_margin_base_cad_m3": _fmt(current_margin["base"]),
                    "v2_3_margin_high_cad_m3": _fmt(current_margin["high"]),
                    "margin_low_delta_cad_m3": _fmt(
                        current_margin["low"] - before_margin["low"]
                    ),
                    "margin_base_delta_cad_m3": _fmt(
                        current_margin["base"] - before_margin["base"]
                    ),
                    "margin_high_delta_cad_m3": _fmt(
                        current_margin["high"] - before_margin["high"]
                    ),
                    "v2_2_total_cost_base_cad_m3": _fmt(before_cost),
                    "v2_3_total_cost_base_cad_m3": _fmt(current_cost),
                    "total_cost_base_delta_cad_m3": _fmt(
                        current_cost - before_cost
                    ),
                    "comparison_status": "cost_method_change_not_observed",
                    "source_keys": (
                        f"{STATCAN_SOURCE_KEY};{V22_SOURCE_KEY};bc_log_market_2025_12"
                    ),
                    "source": (
                        f"{STATCAN_SOURCE_KEY};{V22_SOURCE_KEY};bc_log_market_2025_12"
                    ),
                    "source_urls": (
                        f"{STATCAN_SOURCE_URL};"
                        "data/derived/national/harvest_basis/"
                        "national_economics_v2_2"
                    ),
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": reason,
                }
            )
        for volume_basis, volumes in (
            (VOLUME_BASIS_OBSERVED, observed),
            (VOLUME_BASIS_119M, allocation),
        ):
            before_margin = {
                case: _weighted(
                    {
                        province: float(
                            before[(province, scenario, case)]["margin_cad_m3"]
                        )
                        for province in PROVINCES
                    },
                    volumes,
                )
                for case in ("low", "base", "high")
            }
            current_margin = {
                case: _weighted(
                    {
                        province: float(
                            current[(province, scenario, case)]["margin_cad_m3"]
                        )
                        for province in PROVINCES
                    },
                    volumes,
                )
                for case in ("low", "base", "high")
            }
            before_cost = _weighted(
                {
                    province: float(
                        before[(province, scenario, "base")]["total_cost_cad_m3"]
                    )
                    for province in PROVINCES
                },
                volumes,
            )
            current_cost = _weighted(
                {
                    province: float(
                        current[(province, scenario, "base")]["total_cost_cad_m3"]
                    )
                    for province in PROVINCES
                },
                volumes,
            )
            rows.append(
                {
                    "row_type": "national",
                    "volume_basis": volume_basis,
                    "price_scenario": scenario,
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "province": "NATIONAL",
                    "province_name": "Canada",
                    "v2_2_margin_low_cad_m3": _fmt(before_margin["low"]),
                    "v2_2_margin_base_cad_m3": _fmt(before_margin["base"]),
                    "v2_2_margin_high_cad_m3": _fmt(before_margin["high"]),
                    "v2_3_margin_low_cad_m3": _fmt(current_margin["low"]),
                    "v2_3_margin_base_cad_m3": _fmt(current_margin["base"]),
                    "v2_3_margin_high_cad_m3": _fmt(current_margin["high"]),
                    "margin_low_delta_cad_m3": _fmt(
                        current_margin["low"] - before_margin["low"]
                    ),
                    "margin_base_delta_cad_m3": _fmt(
                        current_margin["base"] - before_margin["base"]
                    ),
                    "margin_high_delta_cad_m3": _fmt(
                        current_margin["high"] - before_margin["high"]
                    ),
                    "v2_2_total_cost_base_cad_m3": _fmt(before_cost),
                    "v2_3_total_cost_base_cad_m3": _fmt(current_cost),
                    "total_cost_base_delta_cad_m3": _fmt(
                        current_cost - before_cost
                    ),
                    "comparison_status": "cost_method_change_not_observed",
                    "source_keys": (
                        f"{STATCAN_SOURCE_KEY};{V22_SOURCE_KEY};bc_log_market_2025_12"
                    ),
                    "source": (
                        f"{STATCAN_SOURCE_KEY};{V22_SOURCE_KEY};bc_log_market_2025_12"
                    ),
                    "source_urls": (
                        f"{STATCAN_SOURCE_URL};"
                        "data/derived/national/harvest_basis/"
                        "national_economics_v2_2"
                    ),
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "Volume-weighted diagnostic on observed 2024 harvest "
                        "(primary). The 119 Mm3 row is a sensitivity."
                        if volume_basis == VOLUME_BASIS_OBSERVED
                        else "Volume-weighted 119 Mm3 allocation sensitivity."
                    ),
                }
            )
    return rows


def _tier_before(province: str) -> str:
    if province == "BC":
        return TIER_BC_MIX
    if province == "QC":
        return TIER_QC_MIX
    return "donor_proxy"


def _tier_after(province: str) -> str:
    if province == "BC":
        return TIER_BC_MIX
    if province == "QC":
        return TIER_QC_MIX
    if province in FALLBACK_PROVINCES:
        return TIER_DONOR_FALLBACK
    return TIER_AGGREGATE


def _coverage_rows(
    allocation: Mapping[str, float], observed: Mapping[str, float]
) -> list[dict[str, Any]]:
    total_observed = sum(observed.values())
    total_allocation = sum(allocation.values())
    rows: list[dict[str, Any]] = []
    for version, tier_getter in (("v2_2", _tier_before), ("v2_3", _tier_after)):
        for province in PROVINCES:
            tier = tier_getter(province)
            rows.append(
                {
                    "row_type": "cost_basis_cell",
                    "version": version,
                    "volume_basis": VOLUME_BASIS_OBSERVED,
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "tier": tier,
                    "tier_before": _tier_before(province),
                    "tier_after": _tier_after(province),
                    "tier_changed": str(
                        version == "v2_3"
                        and _tier_before(province) != _tier_after(province)
                    ).lower(),
                    "upgrade_kind": (
                        "cost_proxy_upgrade"
                        if version == "v2_3"
                        and _tier_before(province) == "donor_proxy"
                        and _tier_after(province) == TIER_AGGREGATE
                        else ""
                    ),
                    "observed_2024_volume_m3": _fmt(observed[province]),
                    "allocation_volume_m3": _fmt(allocation[province]),
                    "volume_share_observed": _fmt(
                        observed[province] / total_observed, 8
                    ),
                    "volume_share_119m": _fmt(
                        allocation[province] / total_allocation, 8
                    ),
                    "cell_count": "1",
                    "aggregate_proxy_cells": "",
                    "donor_proxy_cells": "",
                    "donor_fallback_cells": "",
                    "component_cells": "",
                    "aggregate_proxy_volume_share": "",
                    "donor_proxy_volume_share": "",
                    "donor_fallback_volume_share": "",
                    "component_volume_share": "",
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "One province-level cost-basis cell. The aggregate "
                        "proxy is not direct and does not provide a logging or "
                        "haul breakdown."
                    ),
                }
            )
    for version, tier_getter in (("v2_2", _tier_before), ("v2_3", _tier_after)):
        for volume_basis, volumes, total in (
            (VOLUME_BASIS_OBSERVED, observed, total_observed),
            (VOLUME_BASIS_119M, allocation, total_allocation),
        ):
            tiers = {province: tier_getter(province) for province in PROVINCES}
            counts = Counter(tiers.values())
            share = {
                tier: sum(volumes[p] for p in PROVINCES if tiers[p] == tier)
                / total
                for tier in counts
            }
            rows.append(
                {
                    "row_type": "coverage_summary",
                    "version": version,
                    "volume_basis": volume_basis,
                    "province": "",
                    "province_name": "",
                    "tier": "mixed",
                    "tier_before": "",
                    "tier_after": "",
                    "tier_changed": "",
                    "upgrade_kind": "",
                    "observed_2024_volume_m3": _fmt(sum(observed.values())),
                    "allocation_volume_m3": _fmt(sum(allocation.values())),
                    "volume_share_observed": "1.00000000",
                    "volume_share_119m": "1.00000000",
                    "cell_count": str(len(PROVINCES)),
                    "aggregate_proxy_cells": str(counts.get(TIER_AGGREGATE, 0)),
                    "donor_proxy_cells": str(counts.get("donor_proxy", 0)),
                    "donor_fallback_cells": str(
                        counts.get(TIER_DONOR_FALLBACK, 0)
                    ),
                    "component_cells": str(
                        counts.get(TIER_BC_MIX, 0) + counts.get(TIER_QC_MIX, 0)
                    ),
                    "aggregate_proxy_volume_share": _fmt(
                        share.get(TIER_AGGREGATE, 0.0), 8
                    ),
                    "donor_proxy_volume_share": _fmt(
                        share.get("donor_proxy", 0.0), 8
                    ),
                    "donor_fallback_volume_share": _fmt(
                        share.get(TIER_DONOR_FALLBACK, 0.0), 8
                    ),
                    "component_volume_share": _fmt(
                        share.get(TIER_BC_MIX, 0.0)
                        + share.get(TIER_QC_MIX, 0.0),
                        8,
                    ),
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "notes": (
                        "Primary volume basis is observed 2024 harvest. The "
                        "119 Mm3 allocation row is a sensitivity. Component "
                        "tiers are not all direct; BC keeps a logging/haul "
                        "proxy and QC keeps survey aggregates."
                    ),
                }
            )
    for province in AGGREGATE_PROVINCES:
        rows.append(
            {
                "row_type": "cost_proxy_upgrade",
                "version": "v2_2_to_v2_3",
                "volume_basis": VOLUME_BASIS_OBSERVED,
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "tier": TIER_AGGREGATE,
                "tier_before": "donor_proxy",
                "tier_after": TIER_AGGREGATE,
                "tier_changed": "true",
                "upgrade_kind": "cost_proxy_upgrade",
                "observed_2024_volume_m3": _fmt(observed[province]),
                "allocation_volume_m3": _fmt(allocation[province]),
                "volume_share_observed": _fmt(
                    observed[province] / total_observed, 8
                ),
                "volume_share_119m": _fmt(
                    allocation[province] / total_allocation, 8
                ),
                "cell_count": "1",
                "aggregate_proxy_cells": "",
                "donor_proxy_cells": "",
                "donor_fallback_cells": "",
                "component_cells": "",
                "aggregate_proxy_volume_share": "",
                "donor_proxy_volume_share": "",
                "donor_fallback_volume_share": "",
                "component_volume_share": "",
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "notes": (
                    "Donor cost stack replaced by the StatCan industry "
                    "aggregate total-cost proxy. This is a cost-proxy upgrade, "
                    "not direct coverage."
                ),
            }
        )
    for volume_basis in VOLUME_BASES:
        before = next(
            row
            for row in rows
            if row["row_type"] == "coverage_summary"
            and row["version"] == "v2_2"
            and row["volume_basis"] == volume_basis
        )
        after = next(
            row
            for row in rows
            if row["row_type"] == "coverage_summary"
            and row["version"] == "v2_3"
            and row["volume_basis"] == volume_basis
        )
        rows.append(
            {
                "row_type": "coverage_delta",
                "version": "v2_2_to_v2_3",
                "volume_basis": volume_basis,
                "province": "",
                "province_name": "",
                "tier": "mixed",
                "tier_before": "donor_proxy",
                "tier_after": TIER_AGGREGATE,
                "tier_changed": "true",
                "upgrade_kind": "cost_proxy_upgrade",
                "observed_2024_volume_m3": _fmt(sum(observed.values())),
                "allocation_volume_m3": _fmt(sum(allocation.values())),
                "volume_share_observed": "",
                "volume_share_119m": "",
                "cell_count": "1",
                "aggregate_proxy_cells": "",
                "donor_proxy_cells": "",
                "donor_fallback_cells": "",
                "component_cells": "",
                "aggregate_proxy_volume_share": "",
                "donor_proxy_volume_share": "",
                "donor_fallback_volume_share": "",
                "component_volume_share": "",
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "notes": (
                    "v2.2-to-v2.3 aggregate-proxy coverage change: "
                    f"{before['aggregate_proxy_volume_share']} to "
                    f"{after['aggregate_proxy_volume_share']} volume-weighted."
                ),
            }
        )
    return rows


def _validate(
    cost_rows: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
    aggregate: Mapping[str, AggregateCost],
) -> None:
    if len(cost_rows) != 18:
        raise NationalEconomicsV23Error(
            f"expected 18 cost input rows, found {len(cost_rows)}"
        )
    for row in cost_rows:
        if row["unit"] != "CAD/m3":
            raise NationalEconomicsV23Error(f"cost unit violation: {row}")
        if not (
            float(row["low_cad_m3"])
            <= float(row["base_cad_m3"])
            <= float(row["high_cad_m3"])
        ):
            raise NationalEconomicsV23Error(f"unordered cost row: {row}")
        if row["observed"] != OBSERVED or row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV23Error(f"cost label violation: {row}")
    if len(margins) != len(PROVINCES) * len(SCENARIOS) * 3:
        raise NationalEconomicsV23Error("margin row count mismatch")
    by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    for province in PROVINCES:
        for scenario in SCENARIOS:
            for case in ("low", "base", "high"):
                row = by_key[(province, scenario, case)]
                margin = float(row["margin_cad_m3"])
                total = float(row["total_cost_cad_m3"])
                if not math.isclose(
                    margin, float(row["price_cad_m3"]) - total, abs_tol=2e-3
                ):
                    raise NationalEconomicsV23Error(
                        f"margin arithmetic violation: {province}/{scenario}/{case}"
                    )
                if row["negative_margin_clipped"] != "false":
                    raise NationalEconomicsV23Error(f"clipped margin: {row}")
                if province not in COMPONENT_PROVINCES:
                    if row["logging_haul_cad_m3"] != "":
                        raise NationalEconomicsV23Error(
                            f"aggregate province has logging/haul value: {row}"
                        )
                    if row["industry_aggregate_proxy_cad_m3"] == "":
                        raise NationalEconomicsV23Error(
                            f"aggregate province missing total proxy: {row}"
                        )
            low = by_key[(province, scenario, "low")]
            base = by_key[(province, scenario, "base")]
            high = by_key[(province, scenario, "high")]
            if not (
                float(low["margin_cad_m3"])
                <= float(base["margin_cad_m3"])
                <= float(high["margin_cad_m3"])
            ):
                raise NationalEconomicsV23Error(
                    f"unordered margins: {province}/{scenario}"
                )
    if len(summary_rows) != len(PROVINCES) * len(SCENARIOS) + 2 * len(SCENARIOS):
        raise NationalEconomicsV23Error("summary row count mismatch")
    if len(comparison) != (len(PROVINCES) + 2) * len(SCENARIOS):
        raise NationalEconomicsV23Error("comparison row count mismatch")
    comparison_by_key = {
        (row["province"], row["price_scenario"], row["volume_basis"]): row
        for row in comparison
    }
    for province in ("BC", "QC", "NU"):
        for scenario in SCENARIOS:
            row = comparison_by_key[(province, scenario, VOLUME_BASIS_OBSERVED)]
            if not math.isclose(
                float(row["margin_base_delta_cad_m3"]), 0.0, abs_tol=1e-6
            ):
                raise NationalEconomicsV23Error(
                    f"unexpected margin change for {province}: {row}"
                )
    for province in ("AB", "ON"):
        row = comparison_by_key[(province, BASE_SCENARIO, VOLUME_BASIS_OBSERVED)]
        if math.isclose(
            float(row["margin_base_delta_cad_m3"]), 0.0, abs_tol=1e-6
        ):
            raise NationalEconomicsV23Error(
                f"expected aggregate cost change for {province}: {row}"
            )
    for province in AGGREGATE_PROVINCES:
        cost_row = next(
            row
            for row in cost_rows
            if row["province"] == province
            and row["component"] == "industry_aggregate_proxy"
        )
        if not math.isclose(
            float(cost_row["base_cad_m3"]),
            aggregate[province].base_cad_m3,
            abs_tol=5e-4,
        ):
            raise NationalEconomicsV23Error(
                f"aggregate cost mismatch for {province}"
            )
        if (
            cost_row["includes_overhead_not_logging_haul_breakdown"] != "true"
            or cost_row["fallback_used"] != "false"
        ):
            raise NationalEconomicsV23Error(
                f"aggregate label violation for {province}"
            )
    nu_cost = next(
        row
        for row in cost_rows
        if row["province"] == "NU" and row["component"] == "total_cost_fallback"
    )
    if nu_cost["fallback_used"] != "true" or nu_cost["source_key"] != V22_SOURCE_KEY:
        raise NationalEconomicsV23Error("NU fallback label violation")
    upgrades = [row for row in tiers if row["row_type"] == "cost_proxy_upgrade"]
    if len(upgrades) != len(AGGREGATE_PROVINCES):
        raise NationalEconomicsV23Error(
            f"expected {len(AGGREGATE_PROVINCES)} proxy upgrades, "
            f"found {len(upgrades)}"
        )
    labels = " ".join(
        str(value)
        for row in (*cost_rows, *margins, *summary_rows, *comparison, *tiers)
        for value in row.values()
    ).lower()
    if "not_estimated" in labels:
        raise NationalEconomicsV23Error("not_estimated marker present")


def _write_report(
    out_dir: Path,
    summary: Mapping[str, Any],
    cost_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
) -> None:
    national = {
        (row["price_scenario"], row["volume_basis"]): row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    provinces = [row for row in summary_rows if row["row_type"] == "province"]
    by_province = {
        (row["province"], row["price_scenario"]): row for row in provinces
    }
    negatives = [row for row in margins if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = Counter(row["price_scenario"] for row in negatives)
    base_before = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_2"
        and row["volume_basis"] == VOLUME_BASIS_OBSERVED
    )
    base_after = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == VOLUME_BASIS_OBSERVED
    )
    alloc_before = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_2"
        and row["volume_basis"] == VOLUME_BASIS_119M
    )
    alloc_after = next(
        row
        for row in tiers
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == VOLUME_BASIS_119M
    )
    lines = [
        "# National Economics v2.3 - StatCan industry aggregate cost proxy",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        "## What changed",
        "",
        (
            "Version 2.3 keeps the v2.1/v2.2 price scenarios unchanged. The "
            "base price scenario remains `donor_flat`; `stumpage_scaled` remains "
            "a sensitivity. Costs change only for the eleven jurisdictions "
            "outside BC and QC, which now use the Statistics Canada NAICS 11331 "
            "industry aggregate expense per m3 as a total-cost proxy."
        ),
        "",
        "## Cost mapping",
        "",
        "| Province group | Cost basis | Low | Base | High | Tier |",
        "|---|---|---:|---:|---:|---|",
    ]
    bc_total = sum(
        float(row["base_cad_m3"])
        for row in cost_rows
        if row["province"] == "BC"
    )
    qc_total = sum(
        float(row["base_cad_m3"])
        for row in cost_rows
        if row["province"] == "QC"
    )
    lines += [
        (
            f"| BC | v2.2 component stack | "
            f"{_fmt(sum(float(r['low_cad_m3']) for r in cost_rows if r['province']=='BC'))} | "
            f"{_fmt(bc_total)} | "
            f"{_fmt(sum(float(r['high_cad_m3']) for r in cost_rows if r['province']=='BC'))} | "
            f"`{TIER_BC_MIX}` |"
        ),
        (
            f"| QC | v2.2 component stack | "
            f"{_fmt(sum(float(r['low_cad_m3']) for r in cost_rows if r['province']=='QC'))} | "
            f"{_fmt(qc_total)} | "
            f"{_fmt(sum(float(r['high_cad_m3']) for r in cost_rows if r['province']=='QC'))} | "
            f"`{TIER_QC_MIX}` |"
        ),
        (
            "| AB, SK, MB, ON, NB, NS, NL, PE, YT, NT | StatCan NAICS 11331 "
            "aggregate total cost | point | point | point | "
            f"`{TIER_AGGREGATE}` |"
        ),
        (
            "| NU | explicit v2.2 donor-total fallback | "
            f"{_fmt(sum(float(r['low_cad_m3']) for r in cost_rows if r['province']=='NU'))} | "
            f"{_fmt(sum(float(r['base_cad_m3']) for r in cost_rows if r['province']=='NU'))} | "
            f"{_fmt(sum(float(r['high_cad_m3']) for r in cost_rows if r['province']=='NU'))} | "
            f"`{TIER_DONOR_FALLBACK}` |"
        ),
        "",
        (
            "StatCan values are total expenses divided by `observed_2024_m3`. "
            "The 119 Mm3 ratio is retained only as a sensitivity column and is "
            "not used in the margins. The aggregate includes overheads and "
            "does not provide a logging/haul breakdown; no road, silviculture "
            "or haul component is added to it."
        ),
        "",
        "## Margins and coverage delta",
        "",
        "| Province | Base donor_flat low | Base donor_flat central | Base donor_flat high | Sensitivity scaled central |",
        "|---|---:|---:|---:|---:|",
    ]
    for province in PROVINCES:
        base = by_province[(province, BASE_SCENARIO)]
        scaled = by_province[(province, SENSITIVITY_SCENARIO)]
        lines.append(
            f"| {province} | {base['margin_low_cad_m3']} | "
            f"{base['margin_base_cad_m3']} | {base['margin_high_cad_m3']} | "
            f"{scaled['margin_base_cad_m3']} |"
        )
    lines += [
        "",
        "| Scenario | Volume basis | Low | Base | High | Label | Combined net value |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    for scenario in SCENARIOS:
        for basis in VOLUME_BASES:
            row = national[(scenario, basis)]
            lines.append(
                f"| {scenario} | {basis} | {row['margin_low_cad_m3']} | "
                f"{row['margin_base_cad_m3']} | {row['margin_high_cad_m3']} | "
                f"`{NATIONAL_DIAGNOSTIC_LABEL}` | false |"
            )
    lines += [
        "",
        (
            "Observed 2024 harvest is the primary volume denominator. The "
            "119 Mm3 allocation is a sensitivity, not the primary ratio."
        ),
        "",
        "### Cost-proxy coverage before and after",
        "",
        "| Volume basis | Version | Aggregate-proxy cells | Donor-proxy cells | Donor fallback cells | Component cells | Aggregate-proxy volume share | Donor-proxy volume share |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, before, after in (
        ("observed 2024", base_before, base_after),
        ("119 Mm3 sensitivity", alloc_before, alloc_after),
    ):
        for version, row in (("v2.2", before), ("v2.3", after)):
            lines.append(
                f"| {label} | {version} | {row['aggregate_proxy_cells']} | "
                f"{row['donor_proxy_cells']} | {row['donor_fallback_cells']} | "
                f"{row['component_cells']} | "
                f"{row['aggregate_proxy_volume_share']} | "
                f"{row['donor_proxy_volume_share']} |"
            )
    lines += [
        "",
        (
            f"Cost-proxy upgrades: **{len(summary['cost_proxy_coverage']['upgrades'])}** "
            "province cells changed from donor costs to the StatCan aggregate "
            "proxy. This is not direct-cost coverage: the aggregate is a "
            "NAICS 11331 industry total, not an observed logging or haul cost."
        ),
        "",
        "## ON and AB results",
        "",
        (
            "Alberta and Ontario are the two largest aggregate-proxy "
            "replacements after BC/QC. Their donor stacks were identical to "
            "the v2.2 AB/ON donor values; the central cost changes are shown "
            "in `v2_2_v2_3_comparison.csv`."
        ),
        "",
    ]
    for province in ("AB", "ON"):
        row = next(
            item
            for item in summary_rows
            if item["row_type"] == "province"
            and item["province"] == province
            and item["price_scenario"] == BASE_SCENARIO
        )
        comp = next(
            item
            for item in summary["comparison"]["rows"]
            if item["province"] == province
            and item["price_scenario"] == BASE_SCENARIO
            and item["volume_basis"] == VOLUME_BASIS_OBSERVED
        )
        lines.append(
            f"- **{province}**: aggregate proxy {row['total_cost_base_cad_m3']} "
            f"CAD/m3; central donor_flat margin {row['margin_base_cad_m3']} "
            f"CAD/m3; v2.2-to-v2.3 margin delta "
            f"{comp['margin_base_delta_cad_m3']} CAD/m3."
        )
    lines += [
        "",
        "## Caveats",
        "",
        (
            "- **Employer-establishment coverage**: the Annual Survey of "
            "Manufacturing and Logging Industries covers employer "
            "establishments. Non-employer contractors, owner-operators and "
            "informal or community harvest are not fully represented while the "
            "harvest denominator includes all provincial harvest; this can bias "
            "CAD/m3 downward, especially in the territories."
        ),
        (
            "- **2024 preliminary**: reference year 2024 is the latest "
            "available and is preliminary and subject to revision. Values are "
            "nominal 2024 CAD; no deflator or escalation is applied."
        ),
        (
            "- **Materials double-count risk**: the aggregate includes "
            "materials and supplies, which may include payments to contract "
            "loggers (NAICS 113312). Contract logging is a child industry and "
            "must not be added to the parent aggregate; doing so would double "
            "count."
        ),
        (
            "- **Not a logging/haul breakdown**: total expenses include "
            "administration, depreciation, overhead, non-logging activity and "
            "any in-forest hauling performed by logging establishments. They "
            "are not a logging-only or haul-only cost curve."
        ),
        (
            "- **No separate haul add-on**: the aggregate already includes any "
            "hauling performed by logging establishments. Do not add a separate "
            "truck-haul, road or silviculture cost to it without checking "
            "overlap; v2.3 adds none."
        ),
        (
            "- **NU blank**: Nunavut has zero reported harvest and blank "
            "StatCan CAD/m3. The explicit v2.2 donor-total fallback is retained; "
            "no StatCan value is fabricated."
        ),
        "",
        "## Negative margins and labels",
        "",
        (
            f"{len(negatives)} of {len(margins)} margin rows are negative and "
            "are **unclipped**."
        ),
        (
            f"- `donor_flat`: {negative_by_scenario[BASE_SCENARIO]} negative rows."
        ),
        (
            f"- `stumpage_scaled`: "
            f"{negative_by_scenario[SENSITIVITY_SCENARIO]} negative rows."
        ),
        "",
        (
            "No value is observed: every row carries "
            "`evidence=model_estimate`, `observed=false`, `policy_grade=false` "
            "and `combined_national_net_value=false`."
        ),
        "",
        "## Non-claims",
        "",
        "- Not observed profit, not a mill margin and not official net value.",
        "- The StatCan aggregate is not observed and is not an observed logging cost or a policy-grade value.",
        "- No combined national net value is computed.",
        "- No strict-open licence claim is made; source documents are not redistributed.",
        "- No restricted, confidential or microdata input is used.",
        "",
        "## Reproduction",
        "",
        "```bash",
        ".venv/bin/python -m workflows.national.harvest.national_economics_v2_3",
        "```",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(
    out_dir: Path, outputs: Sequence[Path], summary: Mapping[str, Any]
) -> None:
    inputs = []
    for path in (
        STATCAN_CSV,
        STATCAN_SUMMARY_JSON,
        STATCAN_SOURCES_CSV,
        V22_DIR / MARGIN_CSV,
        V22_DIR / SUMMARY_JSON,
        ALLOCATION_CSV,
        v1.BC_COST_ESTIMATES_CSV,
        v1.BC_LOG_MARKET_PRICES_CSV,
        v1.QC_SURVEY_CSV,
        v1.BC_APPRAISAL_COSTS_CSV,
    ):
        if not path.is_file():
            raise NationalEconomicsV23Error(f"missing manifest input: {path}")
        if any(marker in str(path).lower() for marker in RESTRICTED_MARKERS):
            raise NationalEconomicsV23Error(f"restricted input path: {path}")
        inputs.append(
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "observation_type": EVIDENCE,
        "base_scenario": BASE_SCENARIO,
        "sensitivity_scenario": SENSITIVITY_SCENARIO,
        "stumpage_scaled_observed": False,
        "combined_national_net_value": False,
        "restricted_inputs_used": [],
        "labels": summary["labels"],
        "cost_mapping": summary["cost_mapping"],
        "cost_proxy_coverage": summary["cost_proxy_coverage"],
        "national_diagnostic": summary["national_diagnostic"],
        "inputs": inputs,
        "outputs": [
            {"path": path.name, "sha256": _sha256(path)} for path in outputs
        ],
    }
    (out_dir / MANIFEST_JSON).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def build(
    out_dir: Path = DEFAULT_OUT_DIR, *, check_deletions: bool = True
) -> dict[str, Any]:
    if check_deletions:
        assert_no_unexpected_deletions()
    out_dir.mkdir(parents=True, exist_ok=True)
    slope = v1.load_slope_context()
    road = v1.load_road_context()
    road_allowance = v1.load_road_allowance()
    silviculture = v1.load_silviculture_allowance()
    costs = v1.build_cost_proxies(
        v1.load_bc_cost_distribution(),
        slope,
        road,
        road_allowance,
        silviculture,
        v1.load_qc_evidence(),
    )
    allocation_rows = _load_allocation_reference()
    allocation = _allocation_volumes(allocation_rows)
    observed = _observed_volumes(allocation_rows)
    statcan_rows = _load_statcan_proxy()

    qc = v2_2._load_qc_direct_costs()
    bc_road, _ = v2_2._load_bc_road_allowance()
    bc_silviculture, donor_silviculture = v2_2._silviculture_components()
    component_inputs = v2_2._build_component_inputs(
        costs, qc, bc_road, bc_silviculture, donor_silviculture
    )
    aggregate = _build_aggregate_costs(
        statcan_rows, observed, component_inputs
    )
    cost_rows = _cost_input_rows(component_inputs, aggregate)
    price_rows = _price_rows()
    margin_rows = _margin_rows(
        price_rows, component_inputs, aggregate, allocation, observed
    )
    summary_rows = _summary_rows(margin_rows, allocation, observed)
    comparison_rows = _comparison_rows(margin_rows, allocation, observed)
    tier_rows = _coverage_rows(allocation, observed)

    _validate(
        cost_rows,
        margin_rows,
        summary_rows,
        comparison_rows,
        tier_rows,
        aggregate,
    )

    files = {
        COST_INPUTS_CSV: cost_rows,
        MARGIN_CSV: margin_rows,
        SUMMARY_CSV: summary_rows,
        COMPARISON_CSV: comparison_rows,
        TIER_CSV: tier_rows,
    }
    for filename, rows in files.items():
        _write_csv(out_dir / filename, rows)

    negatives = [row for row in margin_rows if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = {
        scenario: sum(
            1 for row in negatives if row["price_scenario"] == scenario
        )
        for scenario in SCENARIOS
    }
    national = {
        (row["price_scenario"], row["volume_basis"]): row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    before_observed = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_2"
        and row["volume_basis"] == VOLUME_BASIS_OBSERVED
    )
    after_observed = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == VOLUME_BASIS_OBSERVED
    )
    before_119m = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_2"
        and row["volume_basis"] == VOLUME_BASIS_119M
    )
    after_119m = next(
        row
        for row in tier_rows
        if row["row_type"] == "coverage_summary"
        and row["version"] == "v2_3"
        and row["volume_basis"] == VOLUME_BASIS_119M
    )
    upgrades = [
        {
            "province": row["province"],
            "tier_before": row["tier_before"],
            "tier_after": row["tier_after"],
            "observed_2024_volume_m3": float(
                row["observed_2024_volume_m3"]
            ),
            "allocation_volume_m3": float(row["allocation_volume_m3"]),
            "volume_share_observed": float(row["volume_share_observed"]),
            "volume_share_119m": float(row["volume_share_119m"]),
        }
        for row in tier_rows
        if row["row_type"] == "cost_proxy_upgrade"
    ]
    generated = datetime.now(UTC).replace(microsecond=0).isoformat()
    summary = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "model_estimate_statcan_aggregate_cost_proxy",
        "description": (
            "13-jurisdiction margins with donor_flat as the base scenario and "
            "stumpage_scaled as the sensitivity. BC and QC keep v2.2 component "
            "costs; ten other provinces use the StatCan NAICS 11331 industry "
            "aggregate total-cost proxy; NU keeps an explicit donor fallback."
        ),
        "decision": {
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "stumpage_scaled_observed": False,
            "stumpage_scaled_policy_grade": False,
            "reason": (
                "The v2.1/v2.2 price decision is retained. v2.3 changes only "
                "the cost basis outside BC and QC and does not add separate "
                "road, silviculture or haul costs to the StatCan aggregate."
            ),
        },
        "cost_mapping": {
            "method": (
                "StatCan NAICS 11331 total expenses / observed_2024_m3 used as "
                "a total-cost proxy outside BC and QC"
            ),
            "aggregate_proxy_provinces": list(AGGREGATE_PROVINCES),
            "component_provinces": list(COMPONENT_PROVINCES),
            "fallback_provinces": list(FALLBACK_PROVINCES),
            "denominator_basis": VOLUME_BASIS_OBSERVED,
            "scenario_119m_usage": "sensitivity_only",
            "includes_overhead_not_logging_haul_breakdown": True,
            "separate_road_silviculture_haul_added": False,
            "observed": False,
            "policy_grade": False,
        },
        "cost_proxy_coverage": {
            "before": {
                "observed_2024_m3": {
                    "aggregate_proxy_cells": int(
                        before_observed["aggregate_proxy_cells"]
                    ),
                    "donor_proxy_cells": int(before_observed["donor_proxy_cells"]),
                    "donor_fallback_cells": int(
                        before_observed["donor_fallback_cells"]
                    ),
                    "component_cells": int(before_observed["component_cells"]),
                    "aggregate_proxy_volume_share": float(
                        before_observed["aggregate_proxy_volume_share"]
                    ),
                    "donor_proxy_volume_share": float(
                        before_observed["donor_proxy_volume_share"]
                    ),
                    "donor_fallback_volume_share": float(
                        before_observed["donor_fallback_volume_share"]
                    ),
                    "component_volume_share": float(
                        before_observed["component_volume_share"]
                    ),
                },
                "119m_allocation_sensitivity": {
                    "aggregate_proxy_cells": int(
                        before_119m["aggregate_proxy_cells"]
                    ),
                    "donor_proxy_cells": int(before_119m["donor_proxy_cells"]),
                    "donor_fallback_cells": int(
                        before_119m["donor_fallback_cells"]
                    ),
                    "component_cells": int(before_119m["component_cells"]),
                    "aggregate_proxy_volume_share": float(
                        before_119m["aggregate_proxy_volume_share"]
                    ),
                    "donor_proxy_volume_share": float(
                        before_119m["donor_proxy_volume_share"]
                    ),
                    "donor_fallback_volume_share": float(
                        before_119m["donor_fallback_volume_share"]
                    ),
                    "component_volume_share": float(
                        before_119m["component_volume_share"]
                    ),
                },
            },
            "after": {
                "observed_2024_m3": {
                    "aggregate_proxy_cells": int(
                        after_observed["aggregate_proxy_cells"]
                    ),
                    "donor_proxy_cells": int(after_observed["donor_proxy_cells"]),
                    "donor_fallback_cells": int(
                        after_observed["donor_fallback_cells"]
                    ),
                    "component_cells": int(after_observed["component_cells"]),
                    "aggregate_proxy_volume_share": float(
                        after_observed["aggregate_proxy_volume_share"]
                    ),
                    "donor_proxy_volume_share": float(
                        after_observed["donor_proxy_volume_share"]
                    ),
                    "donor_fallback_volume_share": float(
                        after_observed["donor_fallback_volume_share"]
                    ),
                    "component_volume_share": float(
                        after_observed["component_volume_share"]
                    ),
                },
                "119m_allocation_sensitivity": {
                    "aggregate_proxy_cells": int(
                        after_119m["aggregate_proxy_cells"]
                    ),
                    "donor_proxy_cells": int(after_119m["donor_proxy_cells"]),
                    "donor_fallback_cells": int(
                        after_119m["donor_fallback_cells"]
                    ),
                    "component_cells": int(after_119m["component_cells"]),
                    "aggregate_proxy_volume_share": float(
                        after_119m["aggregate_proxy_volume_share"]
                    ),
                    "donor_proxy_volume_share": float(
                        after_119m["donor_proxy_volume_share"]
                    ),
                    "donor_fallback_volume_share": float(
                        after_119m["donor_fallback_volume_share"]
                    ),
                    "component_volume_share": float(
                        after_119m["component_volume_share"]
                    ),
                },
            },
            "upgrade_count": len(upgrades),
            "upgrades": upgrades,
            "full_direct_coverage_claimed": False,
        },
        "coverage": {
            "provinces": list(PROVINCES),
            "province_count": len(PROVINCES),
            "missing": [],
            "not_estimated_count": 0,
            "cost_input_rows": len(cost_rows),
            "margin_rows": len(margin_rows),
            "summary_rows": len(summary_rows),
            "comparison_rows": len(comparison_rows),
            "tier_rows": len(tier_rows),
            "scenario_count": len(SCENARIOS),
            "scenarios": list(SCENARIOS),
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "nu_zero_allocation_volume": allocation["NU"] == 0.0,
        },
        "national_diagnostic": {
            "allocation_volume_m3": sum(allocation.values()),
            "observed_2024_volume_m3": sum(observed.values()),
            "primary_volume_basis": VOLUME_BASIS_OBSERVED,
            "sensitivity_volume_basis": VOLUME_BASIS_119M,
            "donor_flat": {
                "observed_2024_m3": {
                    "margin_low_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_low_cad_m3"
                        ]
                    ),
                    "margin_base_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_base_cad_m3"
                        ]
                    ),
                    "margin_high_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_high_cad_m3"
                        ]
                    ),
                },
                "119m_allocation_sensitivity": {
                    "margin_low_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_low_cad_m3"
                        ]
                    ),
                    "margin_base_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_base_cad_m3"
                        ]
                    ),
                    "margin_high_cad_m3": float(
                        national[(BASE_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_high_cad_m3"
                        ]
                    ),
                },
            },
            "stumpage_scaled_sensitivity": {
                "observed_2024_m3": {
                    "margin_low_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_low_cad_m3"
                        ]
                    ),
                    "margin_base_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_base_cad_m3"
                        ]
                    ),
                    "margin_high_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_OBSERVED)][
                            "margin_high_cad_m3"
                        ]
                    ),
                },
                "119m_allocation_sensitivity": {
                    "margin_low_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_low_cad_m3"
                        ]
                    ),
                    "margin_base_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_base_cad_m3"
                        ]
                    ),
                    "margin_high_cad_m3": float(
                        national[(SENSITIVITY_SCENARIO, VOLUME_BASIS_119M)][
                            "margin_high_cad_m3"
                        ]
                    ),
                },
            },
            "label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": False,
        },
        "margins": {
            "negative_row_count": len(negatives),
            "negative_by_scenario": negative_by_scenario,
            "negative_central_case_count": {
                scenario: sum(
                    1
                    for row in negatives
                    if row["price_scenario"] == scenario
                    and row["scenario_id"] == "base"
                )
                for scenario in SCENARIOS
            },
            "negative_documented": True,
            "negative_margin_clipped": False,
        },
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "strict_open": False,
        },
        "caveats": [
            "Employer-establishment coverage: non-employer contractors, owner-operators and informal harvest are not fully represented; CAD/m3 may be biased downward.",
            "Reference year 2024 is preliminary and subject to revision; nominal 2024 CAD with no deflator.",
            "Materials and supplies may include payments to contract loggers (NAICS 113312); do not add contract logging to the parent aggregate.",
            "The aggregate is not a logging-only or haul-only cost and includes administration, depreciation, overhead and non-logging activity.",
            "Do not add a separate haul, road or silviculture cost to the aggregate without checking overlap; v2.3 adds none.",
            "Nunavut has a blank StatCan ratio because both expenses and harvest are zero; an explicit v2.2 donor-total fallback is retained.",
        ],
        "statcan_d_status": {
            "allowed_cells": sorted(
                f"{province}:{column}" for province, column in EXPECTED_D_CELLS
            ),
            "unexpected_cells": [],
            "policy": "D is an acceptable-quality flag, not a deletion; unexpected D cells fail closed.",
        },
        "validation": {
            "all_provinces_present": True,
            "all_rows_model_estimate": True,
            "observed_claim": False,
            "policy_grade_claim": False,
            "strict_open_claim": False,
            "low_base_high_ordered": True,
            "negatives_unclipped": True,
            "combined_national_net_value_computed": False,
            "bc_qc_component_costs_retained": True,
            "statcan_aggregate_used_for_ten_provinces": True,
            "separate_haul_road_silviculture_added": False,
            "nu_statcan_value_fabricated": False,
            "observed_denominator_used": True,
            "scenario_119m_only_sensitivity": True,
            "full_direct_coverage_claimed": False,
        },
        "source_retrieved_utc": "2026-11-22T15:26:12Z",
        "summary_rows": summary_rows,
        "comparison": {"rows": comparison_rows},
    }
    (out_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(
        out_dir, summary, cost_rows, summary_rows, margin_rows, tier_rows
    )
    output_paths = [out_dir / name for name in files] + [
        out_dir / SUMMARY_JSON,
        out_dir / REPORT_MD,
    ]
    _write_manifest(out_dir, output_paths, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--skip-deletion-check",
        action="store_true",
        help="skip the fail-closed git deletion preflight",
    )
    args = parser.parse_args(argv)
    summary = build(args.out, check_deletions=not args.skip_deletion_check)
    print(
        json.dumps(
            {
                "out_dir": str(args.out),
                "coverage": summary["coverage"],
                "national_diagnostic": summary["national_diagnostic"],
                "cost_proxy_coverage": summary["cost_proxy_coverage"],
                "negative_margins": summary["margins"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
