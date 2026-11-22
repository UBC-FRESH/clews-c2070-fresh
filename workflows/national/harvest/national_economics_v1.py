"""National provincial economics scaffold, 13 jurisdictions, explicit tiers.

The model is deliberately a **scaffold**, not a provincial cost survey and not
a policy result.  It combines:

* a reported BC mill-delivered log-price panel (2025-12, 3-month reports);
* the open-data BC logging and truck-haul cost distribution;
* published BC Interior basic-silviculture CAD/m3 allowances;
* the published QC 2022 road CAD/m3 allowance;
* provincial terrain and road-access factors derived from published slope and
  road-distance proxies; and
* the provincial allocation volumes from the 119 Mt national harvest case.

Every non-BC price is a BC donor proxy because no provincial delivered-price
series is materialised.  No provincial wage or diesel index is materialised, so
those factors are explicitly 1.00 and labelled as assumptions.  The QC 2022
survey harvest + haul + road values are used as a cross-check, not as a price.

Every margin row is ``evidence=model_estimate``, ``observed=false``,
``policy_grade=false``, ``net_value_status=model_estimate`` and
``combined_national_net_value=false``.  Negative values are not clipped; the
conservative low case is documented.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_v1
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
MANAGED_LANDBASE_DIR = REPO_ROOT / "data/derived/national/managed_landbase"

BC_LOG_MARKET_PRICES_CSV = HARVEST_BASIS_DIR / "bc_log_market/bc_log_market_prices.csv"
BC_LOG_MARKET_MANIFEST_JSON = HARVEST_BASIS_DIR / "bc_log_market/manifest.json"
BC_COST_ESTIMATES_CSV = (
    HARVEST_BASIS_DIR / "bc_open_cost_model/bc_open_cost_estimates.csv"
)
BC_COST_MANIFEST_JSON = HARVEST_BASIS_DIR / "bc_open_cost_model/manifest.json"
BC_APPRAISAL_COSTS_CSV = HARVEST_BASIS_DIR / "bc_appraisal_costs/bc_appraisal_costs.csv"
BC_APPRAISAL_MANIFEST_JSON = HARVEST_BASIS_DIR / "bc_appraisal_costs/manifest.json"
QC_SURVEY_CSV = HARVEST_BASIS_DIR / "qc_cost_survey_2022/qc_cost_revenue_survey.csv"
QC_SURVEY_MANIFEST_JSON = HARVEST_BASIS_DIR / "qc_cost_survey_2022/manifest.json"
QC_PROCUREMENT_CSV = (
    HARVEST_BASIS_DIR / "procurement_benchmark/qc_procurement_cost_benchmark.csv"
)
QC_PROCUREMENT_MANIFEST_JSON = HARVEST_BASIS_DIR / "procurement_benchmark/manifest.json"
BMMB_HISTORICAL_CSV = (
    HARVEST_BASIS_DIR / "procurement_benchmark/bmmb_historical_benchmark.csv"
)
PROVINCIAL_ALLOCATION_CSV = HARVEST_BASIS_DIR / "provincial_allocation/119m.csv"
PROVINCIAL_ALLOCATION_SUMMARY_JSON = (
    HARVEST_BASIS_DIR / "provincial_allocation/provincial_allocation_summary.json"
)
SLOPE_OVERLAY_CSV = (
    MANAGED_LANDBASE_DIR / "slope_screen/slope_operable_overlay_by_province.csv"
)
SLOPE_MANIFEST_JSON = MANAGED_LANDBASE_DIR / "slope_screen/manifest.json"
ROAD_DISTANCE_CSV = MANAGED_LANDBASE_DIR / "operable_proxy_v2/road_distance_classes.csv"
ROAD_MANIFEST_JSON = MANAGED_LANDBASE_DIR / "operable_proxy_v2/manifest.json"

DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_economics_v1"

PROVINCES: tuple[str, ...] = (
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
PROVINCE_NAMES: dict[str, str] = {
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

PRICE_REPORT_BASIS = "3_month"
PRICE_PERIOD_END = "2025-12-31"
PRICE_YEAR = "2025-12"
PRICE_BASIS = "mill_delivered"
PRICE_CURRENCY = "CAD_nominal_2025"
COST_CURRENCY = "mixed_nominal_2022_2026_no_deflator"

TERRAIN_ELASTICITY = 0.25
ROAD_ELASTICITY = 0.15
ROAD_HIGH_UNCERTAINTY = 0.20
WAGE_INDEX = 1.00
DIESEL_INDEX = 1.00
PRICE_INDEX = 1.00

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"
STRICT_OPEN = "false"

PRICE_TIER_OBSERVED = "observed"
PRICE_TIER_REPORTED = "reported_average"
PRICE_TIER_DONOR = "donor_proxy"
PRICE_TIER_ASSUMPTION = "assumption"

COST_TIER_PROXY = "proxy_model"
COST_TIER_DONOR = "donor_proxy"
COST_TIER_REPORTED = "reported_average"

SILVICULTURE_SERIES = "Interior Basic Silviculture 2026-10"

PRICE_CSV = "national_price_proxies.csv"
COST_CSV = "national_cost_proxies.csv"
MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
ASSUMPTIONS_CSV = "assumptions_donor_register.csv"
SOURCES_CSV = "sources.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NATIONAL_ECONOMICS_REPORT.md"
MANIFEST_JSON = "manifest.json"

RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

LICENSING: dict[str, Any] = {
    "classification": "public_reference_copyrighted",
    "strict_open_licence": False,
    "strict_open_licence_sources": [],
    "source_pdfs_redistributed": False,
    "statement": (
        "No strict-open claim is made. Inputs are public-reference copyrighted "
        "sources or this model's own explicit assumptions. Source PDFs are not "
        "redistributed. Commercial reuse requires permission from the rights holder."
    ),
}


class NationalEconomicsError(ValueError):
    """Raised when an input or result violates the documented contract."""


@dataclass(frozen=True)
class PricePanel:
    region: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    volume_m3: float
    report_ids: tuple[str, ...]


@dataclass(frozen=True)
class BcPriceBlend:
    coast: PricePanel
    interior: PricePanel
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    coast_weight: float
    interior_weight: float


@dataclass(frozen=True)
class BcCostDistribution:
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    p10_base_cad_m3: float
    p90_base_cad_m3: float
    by_system_base_median: dict[str, float]
    by_zone_base_median: dict[str, float]


@dataclass(frozen=True)
class Allowance:
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    source: str
    tier: str
    notes: str


@dataclass(frozen=True)
class QcEvidence:
    purchase_cost_cad_m3: float
    cost_spread_after_transfers_cad_m3: float
    bmmb_crown_rate_cad_m3: float
    harvest_total_cad_m3: float
    haul_total_cad_m3: float
    road_before_prccm_cad_m3: float
    road_after_prccm_cad_m3: float


@dataclass(frozen=True)
class PriceProxy:
    province: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    price_tier: str
    donor_province: str
    price_index: float
    price_index_basis: str
    qc_purchase_cost_reference_cad_m3: float | None
    qc_bmmb_crown_benchmark_cad_m3: float | None
    qc_cost_spread_after_transfers_cad_m3: float | None
    notes: str


@dataclass(frozen=True)
class CostProxy:
    province: str
    logging_haul_low_cad_m3: float
    logging_haul_base_cad_m3: float
    logging_haul_high_cad_m3: float
    wage_factor: float
    diesel_factor: float
    terrain_factor: float
    road_factor: float
    combined_cost_index: float
    terrain_share_gt15: float
    road_gt20_share: float
    road_low_cad_m3: float
    road_base_cad_m3: float
    road_high_cad_m3: float
    silviculture_low_cad_m3: float
    silviculture_base_cad_m3: float
    silviculture_high_cad_m3: float
    total_cost_low_cad_m3: float
    total_cost_base_cad_m3: float
    total_cost_high_cad_m3: float
    logging_haul_tier: str
    road_tier: str
    silviculture_tier: str
    cost_tier: str
    qc_direct_harvest_haul_cad_m3: float | None
    qc_direct_road_cad_m3: float | None
    qc_direct_total_cad_m3: float | None
    qc_crosscheck_ratio: float | None
    qc_crosscheck_status: str
    notes: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise NationalEconomicsError(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise NationalEconomicsError(f"empty input table: {path}")
    return rows


def _require_columns(
    rows: Sequence[Mapping[str, str]], required: Iterable[str], source: Path
) -> None:
    missing = set(required) - set(rows[0])
    if missing:
        raise NationalEconomicsError(f"{source} is missing columns: {sorted(missing)}")


def _number(row: Mapping[str, str], column: str, context: str) -> float:
    raw = row.get(column, "")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise NationalEconomicsError(
            f"non-numeric {column} in {context}: {raw!r}"
        ) from exc
    if not math.isfinite(value):
        raise NationalEconomicsError(f"non-finite {column} in {context}: {value}")
    return value


def _optional_number(row: Mapping[str, str], column: str, context: str) -> float | None:
    raw = row.get(column, "").strip()
    if raw == "":
        return None
    return _number(row, column, context)


def _require_positive(value: float, context: str) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise NationalEconomicsError(f"non-positive value in {context}: {value}")
    return value


def _require_ordered(low: float, base: float, high: float, context: str) -> None:
    if not low <= base <= high:
        raise NationalEconomicsError(
            f"unordered low/base/high in {context}: {low}, {base}, {high}"
        )


def _is_true(raw: str) -> bool:
    return raw.strip().lower() == "true"


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def _quantile(values: Sequence[float], q: float) -> float:
    if not values:
        raise NationalEconomicsError("quantile requires a non-empty sample")
    if not 0.0 < q < 1.0:
        raise NationalEconomicsError(f"quantile outside (0, 1): {q}")
    ordered = sorted(values)
    if any(not math.isfinite(value) for value in ordered):
        raise NationalEconomicsError("quantile requires finite values")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def _assert_not_restricted(path: Path) -> None:
    lowered = str(path).lower()
    if any(marker in lowered for marker in RESTRICTED_MARKERS):
        raise NationalEconomicsError(f"restricted input path rejected: {path}")


def _report_volumes(
    rows: Sequence[Mapping[str, str]], region: str
) -> tuple[float, tuple[str, ...]]:
    selected = [
        row
        for row in rows
        if row["region"] == region
        and row["report_basis"] == PRICE_REPORT_BASIS
        and row["period_end"] == PRICE_PERIOD_END
        and row["price_plausibility"] == "ok"
    ]
    if not selected:
        raise NationalEconomicsError(f"no BC price rows for {region}")
    volumes: dict[str, float] = {}
    for row in selected:
        report_id = row["report_id"]
        volume = _optional_number(row, "report_total_volume_m3", report_id)
        if volume is None:
            continue
        if report_id in volumes and abs(volumes[report_id] - volume) > 1e-6:
            raise NationalEconomicsError(
                f"inconsistent report volume for {report_id}: "
                f"{volumes[report_id]} vs {volume}"
            )
        volumes[report_id] = _require_positive(volume, f"{report_id} volume")
    if not volumes:
        raise NationalEconomicsError(f"no report volumes for {region}")
    return sum(volumes.values()), tuple(sorted(volumes))


def load_bc_price_blend() -> BcPriceBlend:
    """Load the 2025-12 3-month BC price panels and volume-weight them."""
    from workflows.national.harvest.open_data_net_value import load_price_panels

    panels = load_price_panels()
    if set(panels) != {"Coast", "Interior"}:
        raise NationalEconomicsError(f"unexpected BC price regions: {sorted(panels)}")
    rows = _read_csv(BC_LOG_MARKET_PRICES_CSV)
    _require_columns(
        rows,
        (
            "report_id",
            "region",
            "report_basis",
            "period_end",
            "price_cad_m3",
            "price_plausibility",
            "report_total_volume_m3",
        ),
        BC_LOG_MARKET_PRICES_CSV,
    )
    coast_volume, coast_reports = _report_volumes(rows, "coast")
    interior_volume, interior_reports = _report_volumes(rows, "interior")
    total_volume = coast_volume + interior_volume
    if total_volume <= 0.0:
        raise NationalEconomicsError("BC price blend has no report volume")
    coast_weight = coast_volume / total_volume
    interior_weight = interior_volume / total_volume

    def _blend(component: str) -> float:
        coast_value = getattr(panels["Coast"], f"{component}_cad_m3")
        interior_value = getattr(panels["Interior"], f"{component}_cad_m3")
        return coast_value * coast_weight + interior_value * interior_weight

    low = _blend("low")
    base = _blend("base")
    high = _blend("high")
    _require_ordered(low, base, high, "BC price blend")
    return BcPriceBlend(
        coast=PricePanel(
            region="Coast",
            low_cad_m3=panels["Coast"].low_cad_m3,
            base_cad_m3=panels["Coast"].base_cad_m3,
            high_cad_m3=panels["Coast"].high_cad_m3,
            volume_m3=coast_volume,
            report_ids=coast_reports,
        ),
        interior=PricePanel(
            region="Interior",
            low_cad_m3=panels["Interior"].low_cad_m3,
            base_cad_m3=panels["Interior"].base_cad_m3,
            high_cad_m3=panels["Interior"].high_cad_m3,
            volume_m3=interior_volume,
            report_ids=interior_reports,
        ),
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        coast_weight=coast_weight,
        interior_weight=interior_weight,
    )


def load_bc_cost_distribution() -> BcCostDistribution:
    """Load the 945-row BC cost grid and summarise its distribution."""
    from workflows.national.harvest.open_data_net_value import load_cost_estimates

    rows = load_cost_estimates()
    low_values: list[float] = []
    base_values: list[float] = []
    high_values: list[float] = []
    by_system: dict[str, list[float]] = {}
    by_zone: dict[str, list[float]] = {}
    for row in rows:
        scenario_id = row["scenario_id"]
        low = _number(row, "total_low_cad_m3", scenario_id)
        base = _number(row, "total_base_cad_m3", scenario_id)
        high = _number(row, "total_high_cad_m3", scenario_id)
        _require_ordered(low, base, high, scenario_id)
        low_values.append(low)
        base_values.append(base)
        high_values.append(high)
        by_system.setdefault(row["system"], []).append(base)
        by_zone.setdefault(row["zone"], []).append(base)
    low = _quantile(low_values, 0.10)
    base = _quantile(base_values, 0.50)
    high = _quantile(high_values, 0.90)
    _require_ordered(low, base, high, "BC cost distribution")
    return BcCostDistribution(
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        p10_base_cad_m3=_quantile(base_values, 0.10),
        p90_base_cad_m3=_quantile(base_values, 0.90),
        by_system_base_median={
            key: statistics.median(values) for key, values in sorted(by_system.items())
        },
        by_zone_base_median={
            key: statistics.median(values) for key, values in sorted(by_zone.items())
        },
    )


def load_slope_context() -> dict[str, dict[str, float]]:
    rows = _read_csv(SLOPE_OVERLAY_CSV)
    _require_columns(
        rows,
        ("province", "share_gt15", "share_gt30", "slope_mean_pct"),
        SLOPE_OVERLAY_CSV,
    )
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        province = row["province"]
        if province not in PROVINCES:
            raise NationalEconomicsError(f"unexpected slope province: {province}")
        result[province] = {
            "share_gt15": _number(row, "share_gt15", province),
            "share_gt30": _number(row, "share_gt30", province),
            "slope_mean_pct": _number(row, "slope_mean_pct", province),
        }
    missing = set(PROVINCES) - set(result)
    if missing:
        raise NationalEconomicsError(
            f"slope context missing provinces: {sorted(missing)}"
        )
    return result


def load_road_context() -> dict[str, dict[str, float]]:
    rows = _read_csv(ROAD_DISTANCE_CSV)
    _require_columns(
        rows,
        ("province", "base_merchantable_ha", "road_gt_20km_ha"),
        ROAD_DISTANCE_CSV,
    )
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        province = row["province"]
        if province not in PROVINCES:
            raise NationalEconomicsError(f"unexpected road province: {province}")
        merchantable = _require_positive(
            _number(row, "base_merchantable_ha", province), f"{province} road area"
        )
        far = _number(row, "road_gt_20km_ha", province)
        if far < 0.0 or far > merchantable * 1.000001:
            raise NationalEconomicsError(
                f"road >20km area outside merchantable area for {province}"
            )
        result[province] = {
            "base_merchantable_ha": merchantable,
            "road_gt_20km_ha": far,
            "road_gt20_share": far / merchantable,
        }
    missing = set(PROVINCES) - set(result)
    if missing:
        raise NationalEconomicsError(
            f"road context missing provinces: {sorted(missing)}"
        )
    return result


def _qc_survey_value(
    rows: Sequence[Mapping[str, str]],
    table: str,
    component: str,
    context: str,
) -> float:
    selected = [
        row
        for row in rows
        if row["source_table"] == table
        and row["region_code"] == "QC"
        and row["species_group"] == "sepm"
        and row["component"] == component
    ]
    if len(selected) != 1:
        raise NationalEconomicsError(
            f"expected one QC survey row for {context}, found {len(selected)}"
        )
    return _require_positive(
        _number(selected[0], "value", context), f"QC survey {context}"
    )


def load_qc_evidence() -> QcEvidence:
    rows = _read_csv(QC_SURVEY_CSV)
    _require_columns(
        rows,
        (
            "source_table",
            "region_code",
            "species_group",
            "component",
            "value",
            "unit",
        ),
        QC_SURVEY_CSV,
    )
    harvest = _qc_survey_value(rows, "T3", "harvest_total", "QC harvest_total")
    haul = _qc_survey_value(rows, "T6", "haul_total", "QC haul_total")
    road_before = _qc_survey_value(
        rows, "T5", "road_total_before_prccm", "QC road before PRCCM"
    )
    road_after = _qc_survey_value(
        rows, "T5", "road_total_after_prccm", "QC road after PRCCM"
    )

    procurement_rows = _read_csv(QC_PROCUREMENT_CSV)
    _require_columns(
        procurement_rows,
        (
            "benchmark_id",
            "purchase_cost_cad_m3",
            "cost_spread_after_transfers_cad_m3",
        ),
        QC_PROCUREMENT_CSV,
    )
    procurement = next(
        (
            row
            for row in procurement_rows
            if row["benchmark_id"] == "qc_2022_sepm_total"
        ),
        None,
    )
    if procurement is None:
        raise NationalEconomicsError("missing QC SEPM procurement benchmark row")

    bmmb_rows = _read_csv(BMMB_HISTORICAL_CSV)
    _require_columns(
        bmmb_rows,
        ("benchmark_id", "weighted_rate_cad_m3"),
        BMMB_HISTORICAL_CSV,
    )
    bmmb = next(
        (
            row
            for row in bmmb_rows
            if row["benchmark_id"] == "bmmb_crown_public_forest_2011_2015"
        ),
        None,
    )
    if bmmb is None:
        raise NationalEconomicsError("missing BMMB Crown historical benchmark row")

    return QcEvidence(
        purchase_cost_cad_m3=_require_positive(
            _number(procurement, "purchase_cost_cad_m3", "QC purchase cost"),
            "QC purchase cost",
        ),
        cost_spread_after_transfers_cad_m3=_number(
            procurement,
            "cost_spread_after_transfers_cad_m3",
            "QC cost spread after transfers",
        ),
        bmmb_crown_rate_cad_m3=_require_positive(
            _number(bmmb, "weighted_rate_cad_m3", "BMMB Crown rate"),
            "BMMB Crown rate",
        ),
        harvest_total_cad_m3=harvest,
        haul_total_cad_m3=haul,
        road_before_prccm_cad_m3=road_before,
        road_after_prccm_cad_m3=road_after,
    )


def load_road_allowance() -> Allowance:
    evidence = load_qc_evidence()
    low = evidence.road_after_prccm_cad_m3
    base = evidence.road_before_prccm_cad_m3
    high = base * (1.0 + ROAD_HIGH_UNCERTAINTY)
    _require_ordered(low, base, high, "road allowance")
    return Allowance(
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        source="qc_cost_survey_2022 T5 road_total",
        tier=COST_TIER_REPORTED,
        notes=(
            "Published QC 2022 road CAD/m3. Low is after-PRCCM; base is "
            "before-PRCCM; high applies an explicit 20% uncertainty. Not "
            "deflated to 2026. Used as the national road donor."
        ),
    )


def load_silviculture_allowance() -> Allowance:
    rows = _read_csv(BC_APPRAISAL_COSTS_CSV)
    _require_columns(
        rows,
        (
            "jurisdiction",
            "region",
            "component_group",
            "component",
            "source_series",
            "cost_basis",
            "value",
            "zone",
        ),
        BC_APPRAISAL_COSTS_CSV,
    )
    selected = [
        row
        for row in rows
        if row["jurisdiction"] == "BC"
        and row["region"] == "Interior"
        and row["component_group"] == "silviculture"
        and row["component"] == "basic_silviculture"
        and row["source_series"] == SILVICULTURE_SERIES
        and row["cost_basis"] == "CAD/m3"
    ]
    if len(selected) != 7:
        raise NationalEconomicsError(
            f"expected 7 BC Interior silviculture rows, found {len(selected)}"
        )
    zones = {row["zone"] for row in selected}
    if len(zones) != len(selected):
        raise NationalEconomicsError("duplicate BC silviculture zone rows")
    values = [
        _require_positive(
            _number(row, "value", f"silviculture {row['zone']}"),
            f"silviculture {row['zone']}",
        )
        for row in selected
    ]
    low = min(values)
    base = statistics.median(values)
    high = max(values)
    _require_ordered(low, base, high, "silviculture allowance")
    return Allowance(
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        source=f"BC appraisal costs {SILVICULTURE_SERIES}",
        tier=COST_TIER_REPORTED,
        notes=(
            "Published BC Interior basic-silviculture CAD/m3 by forest zone. "
            "Low/median/high are the zone range. Coast CAD/ha values are not "
            "converted to CAD/m3."
        ),
    )


def load_allocation_volumes() -> dict[str, float]:
    rows = _read_csv(PROVINCIAL_ALLOCATION_CSV)
    _require_columns(rows, ("province", "harvest_m3"), PROVINCIAL_ALLOCATION_CSV)
    result: dict[str, float] = {}
    for row in rows:
        province = row["province"]
        if province not in PROVINCES:
            raise NationalEconomicsError(f"unexpected allocation province: {province}")
        volume = _number(row, "harvest_m3", province)
        if volume < 0.0:
            raise NationalEconomicsError(f"negative allocation volume: {province}")
        result[province] = volume
    missing = set(PROVINCES) - set(result)
    if missing:
        raise NationalEconomicsError(f"allocation missing provinces: {sorted(missing)}")
    if sum(result.values()) <= 0.0:
        raise NationalEconomicsError("allocation volumes sum to zero")
    return result


def build_price_proxies(
    blend: BcPriceBlend, qc_evidence: QcEvidence
) -> dict[str, PriceProxy]:
    proxies: dict[str, PriceProxy] = {}
    for province in PROVINCES:
        if province == "BC":
            tier = PRICE_TIER_REPORTED
            donor = ""
            basis = (
                "BC 2025-12 3-month report averages blended by published report "
                "volume across Coast and Interior"
            )
            notes = (
                "Direct BC reported mill-delivered average. Coast and Interior "
                "report averages are volume-weighted by their published report "
                "totals; this is a reported average, not a transaction record."
            )
            qc_purchase = None
            qc_bmmb = None
            qc_spread = None
        else:
            tier = PRICE_TIER_DONOR
            donor = "BC"
            basis = (
                "no provincial wage/diesel/price index materialised; BC donor "
                "price index fixed at 1.00 (national-median donor assumption)"
            )
            notes = (
                "BC donor price proxy. No current provincial delivered-log price "
                "series is materialised; no wage/diesel index is applied."
            )
            if province == "QC":
                notes = (
                    "QC has no current delivered-log price in the materialised "
                    "evidence. BC donor price used. The QC 2022 purchase cost "
                    "and BMMB Crown historical rate are references only, not "
                    "price inputs."
                )
                qc_purchase = qc_evidence.purchase_cost_cad_m3
                qc_bmmb = qc_evidence.bmmb_crown_rate_cad_m3
                qc_spread = qc_evidence.cost_spread_after_transfers_cad_m3
            else:
                qc_purchase = None
                qc_bmmb = None
                qc_spread = None
        proxies[province] = PriceProxy(
            province=province,
            low_cad_m3=blend.low_cad_m3 * PRICE_INDEX,
            base_cad_m3=blend.base_cad_m3 * PRICE_INDEX,
            high_cad_m3=blend.high_cad_m3 * PRICE_INDEX,
            price_tier=tier,
            donor_province=donor,
            price_index=PRICE_INDEX,
            price_index_basis=basis,
            qc_purchase_cost_reference_cad_m3=qc_purchase,
            qc_bmmb_crown_benchmark_cad_m3=qc_bmmb,
            qc_cost_spread_after_transfers_cad_m3=qc_spread,
            notes=notes,
        )
    return proxies


def _clamp_factor(value: float, context: str) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise NationalEconomicsError(f"invalid factor in {context}: {value}")
    return min(max(value, 0.75), 1.25)


def build_cost_proxies(
    distribution: BcCostDistribution,
    slope: Mapping[str, Mapping[str, float]],
    road: Mapping[str, Mapping[str, float]],
    road_allowance: Allowance,
    silviculture_allowance: Allowance,
    qc_evidence: QcEvidence,
) -> dict[str, CostProxy]:
    bc_slope = slope["BC"]["share_gt15"]
    bc_road = road["BC"]["road_gt20_share"]
    proxies: dict[str, CostProxy] = {}
    for province in PROVINCES:
        terrain_factor = _clamp_factor(
            1.0 + TERRAIN_ELASTICITY * (slope[province]["share_gt15"] - bc_slope),
            f"{province} terrain factor",
        )
        road_factor = _clamp_factor(
            1.0 + ROAD_ELASTICITY * (road[province]["road_gt20_share"] - bc_road),
            f"{province} road factor",
        )
        combined = WAGE_INDEX * DIESEL_INDEX * terrain_factor * road_factor
        logging_haul_low = distribution.low_cad_m3 * combined
        logging_haul_base = distribution.base_cad_m3 * combined
        logging_haul_high = distribution.high_cad_m3 * combined
        _require_ordered(
            logging_haul_low,
            logging_haul_base,
            logging_haul_high,
            f"{province} logging+haul",
        )
        total_low = (
            logging_haul_low
            + road_allowance.low_cad_m3
            + silviculture_allowance.low_cad_m3
        )
        total_base = (
            logging_haul_base
            + road_allowance.base_cad_m3
            + silviculture_allowance.base_cad_m3
        )
        total_high = (
            logging_haul_high
            + road_allowance.high_cad_m3
            + silviculture_allowance.high_cad_m3
        )
        _require_ordered(total_low, total_base, total_high, f"{province} total cost")
        if province == "BC":
            logging_haul_tier = COST_TIER_PROXY
            cost_tier = COST_TIER_PROXY
            notes = (
                "BC logging+haul is the open proxy cost distribution; "
                "silviculture is a published BC Interior allowance; road is a "
                "QC donor because BC road CAD/m3 is not published."
            )
        elif province == "QC":
            logging_haul_tier = COST_TIER_DONOR
            cost_tier = "donor_proxy_with_reported_road"
            notes = (
                "BC donor logging+haul adjusted by terrain and road factors; "
                "road is the published QC 2022 CAD/m3 allowance. QC direct "
                "harvest+haul is a cross-check, not a separate input."
            )
        else:
            logging_haul_tier = COST_TIER_DONOR
            cost_tier = COST_TIER_DONOR
            notes = (
                "BC donor logging+haul adjusted by terrain and road factors; "
                "road and silviculture are donor allowances. No provincial wage "
                "or diesel index is materialised."
            )
        if province == "QC":
            qc_harvest_haul = (
                qc_evidence.harvest_total_cad_m3 + qc_evidence.haul_total_cad_m3
            )
            qc_direct_total = qc_harvest_haul + qc_evidence.road_before_prccm_cad_m3
            qc_ratio = (
                logging_haul_base + road_allowance.base_cad_m3
            ) / qc_direct_total
            qc_status = (
                "within_2pct"
                if abs(qc_ratio - 1.0) <= 0.02
                else "outside_2pct_documented"
            )
        else:
            qc_harvest_haul = None
            qc_direct_total = None
            qc_ratio = None
            qc_status = "not_applicable"
        proxies[province] = CostProxy(
            province=province,
            logging_haul_low_cad_m3=logging_haul_low,
            logging_haul_base_cad_m3=logging_haul_base,
            logging_haul_high_cad_m3=logging_haul_high,
            wage_factor=WAGE_INDEX,
            diesel_factor=DIESEL_INDEX,
            terrain_factor=terrain_factor,
            road_factor=road_factor,
            combined_cost_index=combined,
            terrain_share_gt15=slope[province]["share_gt15"],
            road_gt20_share=road[province]["road_gt20_share"],
            road_low_cad_m3=road_allowance.low_cad_m3,
            road_base_cad_m3=road_allowance.base_cad_m3,
            road_high_cad_m3=road_allowance.high_cad_m3,
            silviculture_low_cad_m3=silviculture_allowance.low_cad_m3,
            silviculture_base_cad_m3=silviculture_allowance.base_cad_m3,
            silviculture_high_cad_m3=silviculture_allowance.high_cad_m3,
            total_cost_low_cad_m3=total_low,
            total_cost_base_cad_m3=total_base,
            total_cost_high_cad_m3=total_high,
            logging_haul_tier=logging_haul_tier,
            road_tier=COST_TIER_REPORTED if province == "QC" else COST_TIER_DONOR,
            silviculture_tier=COST_TIER_REPORTED
            if province == "BC"
            else COST_TIER_DONOR,
            cost_tier=cost_tier,
            qc_direct_harvest_haul_cad_m3=qc_harvest_haul,
            qc_direct_road_cad_m3=(
                qc_evidence.road_before_prccm_cad_m3 if province == "QC" else None
            ),
            qc_direct_total_cad_m3=qc_direct_total,
            qc_crosscheck_ratio=qc_ratio,
            qc_crosscheck_status=qc_status,
            notes=notes,
        )
    return proxies


def build_margin_rows(
    prices: Mapping[str, PriceProxy],
    costs: Mapping[str, CostProxy],
    allocation: Mapping[str, float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        price = prices[province]
        cost = costs[province]
        scenarios = (
            (
                "low",
                0,
                price.low_cad_m3,
                cost.logging_haul_high_cad_m3,
                cost.road_high_cad_m3,
                cost.silviculture_high_cad_m3,
                "price_low - logging_haul_high - road_high - silviculture_high",
                "Conservative envelope: low price minus high cost.",
            ),
            (
                "base",
                1,
                price.base_cad_m3,
                cost.logging_haul_base_cad_m3,
                cost.road_base_cad_m3,
                cost.silviculture_base_cad_m3,
                "price_base - logging_haul_base - road_base - silviculture_base",
                "Central proxy case.",
            ),
            (
                "high",
                2,
                price.high_cad_m3,
                cost.logging_haul_low_cad_m3,
                cost.road_low_cad_m3,
                cost.silviculture_low_cad_m3,
                "price_high - logging_haul_low - road_low - silviculture_low",
                "Optimistic envelope: high price minus low cost.",
            ),
        )
        for (
            scenario_id,
            scenario_order,
            price_value,
            logging_haul,
            road,
            silviculture,
            formula,
            note,
        ) in scenarios:
            total_cost = logging_haul + road + silviculture
            margin = price_value - total_cost
            rows.append(
                {
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "scenario_id": scenario_id,
                    "scenario_order": scenario_order,
                    "price_cad_m3": _fmt(price_value),
                    "logging_haul_cad_m3": _fmt(logging_haul),
                    "road_cad_m3": _fmt(road),
                    "silviculture_cad_m3": _fmt(silviculture),
                    "total_cost_cad_m3": _fmt(total_cost),
                    "margin_cad_m3": _fmt(margin),
                    "margin_formula": formula,
                    "price_tier": price.price_tier,
                    "logging_haul_tier": cost.logging_haul_tier,
                    "road_tier": cost.road_tier,
                    "silviculture_tier": cost.silviculture_tier,
                    "cost_tier": cost.cost_tier,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "net_value_status": NET_VALUE_STATUS,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "allocation_volume_m3": _fmt(allocation[province]),
                    "volume_weighted_margin_cad_m3": _fmt(
                        margin * allocation[province]
                    ),
                    "margin_status": (
                        "negative_documented" if margin < 0.0 else "non_negative"
                    ),
                    "notes": note,
                }
            )
    return rows


def build_margin_summary(
    margin_rows: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
    prices: Mapping[str, PriceProxy],
    costs: Mapping[str, CostProxy],
) -> list[dict[str, Any]]:
    by_province: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in margin_rows:
        by_province.setdefault(str(row["province"]), {})[str(row["scenario_id"])] = row
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsError("allocation volume sum is zero")
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        scenarios = by_province[province]
        base = scenarios["base"]
        low_margin = float(scenarios["low"]["margin_cad_m3"])
        base_margin = float(base["margin_cad_m3"])
        high_margin = float(scenarios["high"]["margin_cad_m3"])
        if low_margin < 0.0 <= base_margin <= high_margin:
            summary_status = "low_negative_documented"
        elif base_margin < 0.0:
            summary_status = "base_negative_documented"
        else:
            summary_status = "non_negative"
        rows.append(
            {
                "row_type": "province",
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "allocation_volume_m3": _fmt(allocation[province]),
                "volume_share": _fmt(allocation[province] / total_volume, 8),
                "price_base_cad_m3": base["price_cad_m3"],
                "total_cost_base_cad_m3": base["total_cost_cad_m3"],
                "margin_low_cad_m3": scenarios["low"]["margin_cad_m3"],
                "margin_base_cad_m3": base["margin_cad_m3"],
                "margin_high_cad_m3": scenarios["high"]["margin_cad_m3"],
                "margin_status": summary_status,
                "price_tier": prices[province].price_tier,
                "cost_tier": costs[province].cost_tier,
                "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "notes": (
                    "Zero allocation volume; margin is a proxy only and no "
                    "commercial harvest is asserted."
                    if allocation[province] == 0.0
                    else "Allocation-volume weight from the 119 Mt case."
                ),
            }
        )
    weighted = {
        scenario: sum(
            float(by_province[province][scenario]["margin_cad_m3"])
            * allocation[province]
            for province in PROVINCES
        )
        / total_volume
        for scenario in ("low", "base", "high")
    }
    weighted_price = (
        sum(
            prices[province].base_cad_m3 * allocation[province]
            for province in PROVINCES
        )
        / total_volume
    )
    weighted_cost = (
        sum(
            costs[province].total_cost_base_cad_m3 * allocation[province]
            for province in PROVINCES
        )
        / total_volume
    )
    rows.append(
        {
            "row_type": "national",
            "province": "NATIONAL",
            "province_name": "Canada",
            "allocation_volume_m3": _fmt(total_volume),
            "volume_share": "1.00000000",
            "price_base_cad_m3": _fmt(weighted_price),
            "total_cost_base_cad_m3": _fmt(weighted_cost),
            "margin_low_cad_m3": _fmt(weighted["low"]),
            "margin_base_cad_m3": _fmt(weighted["base"]),
            "margin_high_cad_m3": _fmt(weighted["high"]),
            "margin_status": NATIONAL_DIAGNOSTIC_LABEL,
            "price_tier": "mixed_reported_average_and_donor_proxy",
            "cost_tier": "mixed_proxy",
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            "notes": (
                "Volume-weighted across all 13 jurisdictions using the 119 Mt "
                "provincial allocation. Model estimate, not a policy result and "
                "not a combined BC+QC-only total."
            ),
        }
    )
    return rows


def _price_rows(
    prices: Mapping[str, PriceProxy], blend: BcPriceBlend
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        price = prices[province]
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "price_low_cad_m3": _fmt(price.low_cad_m3),
                "price_base_cad_m3": _fmt(price.base_cad_m3),
                "price_high_cad_m3": _fmt(price.high_cad_m3),
                "price_basis": PRICE_BASIS,
                "price_year": PRICE_YEAR,
                "currency_basis": PRICE_CURRENCY,
                "price_tier": price.price_tier,
                "donor_province": price.donor_province,
                "price_index": _fmt(price.price_index),
                "price_index_basis": price.price_index_basis,
                "bc_coast_price_low_cad_m3": _fmt(blend.coast.low_cad_m3),
                "bc_coast_price_base_cad_m3": _fmt(blend.coast.base_cad_m3),
                "bc_coast_price_high_cad_m3": _fmt(blend.coast.high_cad_m3),
                "bc_interior_price_low_cad_m3": _fmt(blend.interior.low_cad_m3),
                "bc_interior_price_base_cad_m3": _fmt(blend.interior.base_cad_m3),
                "bc_interior_price_high_cad_m3": _fmt(blend.interior.high_cad_m3),
                "bc_coast_volume_weight": _fmt(blend.coast_weight, 6),
                "bc_interior_volume_weight": _fmt(blend.interior_weight, 6),
                "qc_purchase_cost_reference_cad_m3": (
                    _fmt(price.qc_purchase_cost_reference_cad_m3)
                    if price.qc_purchase_cost_reference_cad_m3 is not None
                    else ""
                ),
                "qc_bmmb_crown_benchmark_cad_m3": (
                    _fmt(price.qc_bmmb_crown_benchmark_cad_m3)
                    if price.qc_bmmb_crown_benchmark_cad_m3 is not None
                    else ""
                ),
                "qc_cost_spread_after_transfers_cad_m3": (
                    _fmt(price.qc_cost_spread_after_transfers_cad_m3)
                    if price.qc_cost_spread_after_transfers_cad_m3 is not None
                    else ""
                ),
                "qc_price_status": (
                    "reference_only_not_used" if province == "QC" else "not_applicable"
                ),
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
                "notes": price.notes,
            }
        )
    return rows


def _cost_rows(costs: Mapping[str, CostProxy]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        cost = costs[province]
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "logging_haul_low_cad_m3": _fmt(cost.logging_haul_low_cad_m3),
                "logging_haul_base_cad_m3": _fmt(cost.logging_haul_base_cad_m3),
                "logging_haul_high_cad_m3": _fmt(cost.logging_haul_high_cad_m3),
                "wage_factor": _fmt(cost.wage_factor, 6),
                "diesel_factor": _fmt(cost.diesel_factor, 6),
                "terrain_factor": _fmt(cost.terrain_factor, 6),
                "road_factor": _fmt(cost.road_factor, 6),
                "combined_cost_index": _fmt(cost.combined_cost_index, 6),
                "terrain_share_gt15": _fmt(cost.terrain_share_gt15, 8),
                "road_gt20_share": _fmt(cost.road_gt20_share, 8),
                "road_low_cad_m3": _fmt(cost.road_low_cad_m3),
                "road_base_cad_m3": _fmt(cost.road_base_cad_m3),
                "road_high_cad_m3": _fmt(cost.road_high_cad_m3),
                "silviculture_low_cad_m3": _fmt(cost.silviculture_low_cad_m3),
                "silviculture_base_cad_m3": _fmt(cost.silviculture_base_cad_m3),
                "silviculture_high_cad_m3": _fmt(cost.silviculture_high_cad_m3),
                "total_cost_low_cad_m3": _fmt(cost.total_cost_low_cad_m3),
                "total_cost_base_cad_m3": _fmt(cost.total_cost_base_cad_m3),
                "total_cost_high_cad_m3": _fmt(cost.total_cost_high_cad_m3),
                "logging_haul_tier": cost.logging_haul_tier,
                "road_tier": cost.road_tier,
                "silviculture_tier": cost.silviculture_tier,
                "cost_tier": cost.cost_tier,
                "cost_currency": COST_CURRENCY,
                "qc_direct_harvest_haul_cad_m3": (
                    _fmt(cost.qc_direct_harvest_haul_cad_m3)
                    if cost.qc_direct_harvest_haul_cad_m3 is not None
                    else ""
                ),
                "qc_direct_road_cad_m3": (
                    _fmt(cost.qc_direct_road_cad_m3)
                    if cost.qc_direct_road_cad_m3 is not None
                    else ""
                ),
                "qc_direct_total_cad_m3": (
                    _fmt(cost.qc_direct_total_cad_m3)
                    if cost.qc_direct_total_cad_m3 is not None
                    else ""
                ),
                "qc_crosscheck_ratio": (
                    _fmt(cost.qc_crosscheck_ratio, 6)
                    if cost.qc_crosscheck_ratio is not None
                    else ""
                ),
                "qc_crosscheck_status": cost.qc_crosscheck_status,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
                "notes": cost.notes,
            }
        )
    return rows


def _assumption_rows(
    qc_evidence: QcEvidence, road: Allowance, silviculture: Allowance
) -> list[dict[str, str]]:
    return [
        {
            "assumption_id": "price_bc_blend",
            "category": "price",
            "applies_to": "BC",
            "rule": "volume-weight BC Coast and Interior 2025-12 3-month report averages by published report volume",
            "value": "reported_average",
            "unit": "CAD/m3",
            "source": "bc_log_market_prices.csv",
            "tier": PRICE_TIER_REPORTED,
            "notes": "Published averages, not transaction records.",
        },
        {
            "assumption_id": "price_non_bc_donor",
            "category": "price",
            "applies_to": "AB,SK,MB,ON,QC,NB,NS,NL,PE,YT,NT,NU",
            "rule": "BC donor price with price index fixed at 1.00 because no provincial wage/diesel/price index is materialised",
            "value": "1.00",
            "unit": "index",
            "source": "national_economics_v1 assumptions",
            "tier": PRICE_TIER_DONOR,
            "notes": "No provincial delivered-log price series is materialised.",
        },
        {
            "assumption_id": "price_qc_gap",
            "category": "price",
            "applies_to": "QC",
            "rule": "QC has no current delivered-log price; QC purchase cost and BMMB Crown historical rate are references only",
            "value": f"purchase={qc_evidence.purchase_cost_cad_m3:.4f}; bmmb={qc_evidence.bmmb_crown_rate_cad_m3:.4f}",
            "unit": "CAD/m3",
            "source": "qc_cost_survey_2022; bmmb_historical_benchmark.csv",
            "tier": PRICE_TIER_DONOR,
            "notes": "Not used in the price or margin arithmetic.",
        },
        {
            "assumption_id": "cost_bc_distribution",
            "category": "cost",
            "applies_to": "all",
            "rule": "BC 945-row logging+haul cost distribution: low=p10(total_low), base=median(total_base), high=p90(total_high)",
            "value": "p10/median/p90",
            "unit": "CAD/m3",
            "source": "bc_open_cost_estimates.csv",
            "tier": COST_TIER_PROXY,
            "notes": "Open proxy model from published rates and case-study productivity; not observed contractor cost.",
        },
        {
            "assumption_id": "cost_wage_index",
            "category": "cost",
            "applies_to": "all",
            "rule": "provincial wage factor fixed at 1.00 because no provincial wage index is materialised",
            "value": "1.00",
            "unit": "factor",
            "source": "national_economics_v1 assumptions",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "A national-median donor assumption, not an observed provincial wage ratio.",
        },
        {
            "assumption_id": "cost_diesel_index",
            "category": "cost",
            "applies_to": "all",
            "rule": "provincial diesel factor fixed at 1.00 because no provincial diesel index is materialised",
            "value": "1.00",
            "unit": "factor",
            "source": "national_economics_v1 assumptions",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "A national-median donor assumption, not an observed provincial diesel ratio.",
        },
        {
            "assumption_id": "cost_terrain_factor",
            "category": "cost",
            "applies_to": "all",
            "rule": "terrain factor = clamp(1 + 0.25 * (province share_gt15 - BC share_gt15), 0.75, 1.25)",
            "value": "0.25",
            "unit": "elasticity",
            "source": "slope_operable_overlay_by_province.csv",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Explicit engineering assumption; not an estimated cost elasticity.",
        },
        {
            "assumption_id": "cost_road_factor",
            "category": "cost",
            "applies_to": "all",
            "rule": "road factor = clamp(1 + 0.15 * (province road_gt20_share - BC road_gt20_share), 0.75, 1.25)",
            "value": "0.15",
            "unit": "elasticity",
            "source": "road_distance_classes.csv",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Explicit engineering assumption; not an estimated cost elasticity.",
        },
        {
            "assumption_id": "road_allowance_qc_donor",
            "category": "road",
            "applies_to": "all",
            "rule": "QC 2022 road CAD/m3: low=after PRCCM, base=before PRCCM, high=base*1.20; used as the national road donor",
            "value": f"{road.low_cad_m3:.4f}/{road.base_cad_m3:.4f}/{road.high_cad_m3:.4f}",
            "unit": "CAD/m3",
            "source": "qc_cost_survey_2022 T5",
            "tier": COST_TIER_REPORTED,
            "notes": "No CAD/km-to-CAD/m3 conversion is attempted.",
        },
        {
            "assumption_id": "silviculture_allowance_bc_donor",
            "category": "silviculture",
            "applies_to": "all",
            "rule": "BC Interior basic silviculture CAD/m3 zone range: low=min, base=median, high=max; used as the national silviculture donor",
            "value": f"{silviculture.low_cad_m3:.4f}/{silviculture.base_cad_m3:.4f}/{silviculture.high_cad_m3:.4f}",
            "unit": "CAD/m3",
            "source": f"bc_appraisal_costs {SILVICULTURE_SERIES}",
            "tier": COST_TIER_REPORTED,
            "notes": "Coast CAD/ha values are not converted to CAD/m3.",
        },
        {
            "assumption_id": "no_cad_per_km_conversion",
            "category": "road",
            "applies_to": "all",
            "rule": "road, bridge and culvert CAD/km or CAD/each values are never converted to CAD/m3",
            "value": "true",
            "unit": "flag",
            "source": "national_economics_v1 assumptions",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Only published CAD/m3 values enter the margin.",
        },
        {
            "assumption_id": "qc_cost_crosscheck",
            "category": "validation",
            "applies_to": "QC",
            "rule": "QC direct 2022 harvest+haul and road are a cross-check against the donor cost; they are not a separate price or margin input",
            "value": f"harvest+haul={qc_evidence.harvest_total_cad_m3 + qc_evidence.haul_total_cad_m3:.4f}; road={qc_evidence.road_before_prccm_cad_m3:.4f}",
            "unit": "CAD/m3",
            "source": "qc_cost_survey_2022 T3/T5/T6",
            "tier": COST_TIER_REPORTED,
            "notes": "Nominal 2022, not deflated to 2026.",
        },
        {
            "assumption_id": "national_weighting",
            "category": "national",
            "applies_to": "national diagnostic",
            "rule": "volume-weight all 13 provincial base margins by the 119 Mt provincial allocation",
            "value": "119m allocation",
            "unit": "m3/yr",
            "source": "provincial_allocation/119m.csv",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Model estimate, not a policy result; not a BC+QC-only total.",
        },
        {
            "assumption_id": "nu_zero_volume",
            "category": "coverage",
            "applies_to": "NU",
            "rule": "NU allocation volume is zero; a proxy margin is still reported and no commercial harvest is asserted",
            "value": "0",
            "unit": "m3/yr",
            "source": "provincial_allocation/119m.csv",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "No observed harvest row and no not-estimated label.",
        },
        {
            "assumption_id": "negative_margin_documentation",
            "category": "validation",
            "applies_to": "all",
            "rule": "negative margins are unclipped and documented; the low case is a conservative envelope",
            "value": "unclipped",
            "unit": "flag",
            "source": "national_economics_v1 assumptions",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Negative values are not an error by themselves.",
        },
        {
            "assumption_id": "strict_open",
            "category": "licensing",
            "applies_to": "all",
            "rule": "no strict-open claim; inputs are public-reference copyrighted or model assumptions",
            "value": "false",
            "unit": "flag",
            "source": "national_economics_v1 licensing",
            "tier": PRICE_TIER_ASSUMPTION,
            "notes": "Source PDFs are not redistributed.",
        },
    ]


def _source_rows() -> list[dict[str, str]]:
    return [
        {
            "source_key": "bc_log_market_2025_12",
            "title": "BC Coast and Interior log-market reports, 2025-12 3-month",
            "publisher": "BC Timber Pricing Branch, Ministry of Forests",
            "year": "2025",
            "url": "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/timber-pricing",
            "path": _repo_path(BC_LOG_MARKET_PRICES_CSV),
            "sha256": sha256_file(BC_LOG_MARKET_PRICES_CSV),
            "licence": "Public-reference BC Crown copyright; not strict-open.",
            "evidence_tier": "reported_market_average",
            "strict_open": "false",
            "used_for": "BC reported mill-delivered price panel.",
        },
        {
            "source_key": "bc_open_cost_model",
            "title": "Open-data BC logging and truck-haul cost model",
            "publisher": "Derived from TLA/ILA/NWLA/MNP rate guide and FPInnovations case studies",
            "year": "2026",
            "url": "https://www.tla.ca/wp-content/uploads/2026/08/Timber-Harvest-Cost-Model-Rate-Sheet_Aug-10-2026.pdf",
            "path": _repo_path(BC_COST_ESTIMATES_CSV),
            "sha256": sha256_file(BC_COST_ESTIMATES_CSV),
            "licence": "Public-reference copyrighted inputs; derived model; not strict-open.",
            "evidence_tier": "proxy_model",
            "strict_open": "false",
            "used_for": "BC logging+haul cost distribution and donor distribution.",
        },
        {
            "source_key": "bc_appraisal_silviculture_2026_10",
            "title": "BC Interior basic silviculture CAD/m3, 2026-10",
            "publisher": "BC Timber Pricing Branch, Ministry of Forests",
            "year": "2026",
            "url": "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/forestry/timber-pricing/interior-timber-pricing/interior-appraisal-manual/iam_2026_master_b.pdf",
            "path": _repo_path(BC_APPRAISAL_COSTS_CSV),
            "sha256": sha256_file(BC_APPRAISAL_COSTS_CSV),
            "licence": "Public-reference BC Crown copyright; not strict-open.",
            "evidence_tier": "appraisal_allowance",
            "strict_open": "false",
            "used_for": "BC Interior basic-silviculture CAD/m3 range; national silviculture donor.",
        },
        {
            "source_key": "qc_cost_survey_2022",
            "title": "Quebec MRNF 2022 forest cost and revenue survey aggregates",
            "publisher": "Gouvernement du Quebec, MRNF/BMMB",
            "year": "2022",
            "url": "https://www.quebec.ca/",
            "path": _repo_path(QC_SURVEY_CSV),
            "sha256": sha256_file(QC_SURVEY_CSV),
            "licence": "Public-reference QC government report; not strict-open.",
            "evidence_tier": "survey_aggregate",
            "strict_open": "false",
            "used_for": "QC harvest, road and haul cost cross-check and road allowance.",
        },
        {
            "source_key": "qc_procurement_benchmark_2022",
            "title": "QC 2022 procurement cost benchmark and BMMB historical benchmark",
            "publisher": "Gouvernement du Quebec, MRNF/BMMB",
            "year": "2022",
            "url": "https://www.quebec.ca/",
            "path": _repo_path(QC_PROCUREMENT_CSV),
            "sha256": sha256_file(QC_PROCUREMENT_CSV),
            "licence": "Public-reference QC government report; not strict-open.",
            "evidence_tier": "survey_procurement_cost",
            "strict_open": "false",
            "used_for": "QC purchase-cost reference and after-transfer cost spread; not used as price.",
        },
        {
            "source_key": "bmmb_delivered_2011_2015",
            "title": "BMMB delivered-sale historical benchmark, 2011-2015",
            "publisher": "Gouvernement du Quebec, BMMB",
            "year": "2015",
            "url": "https://www.quebec.ca/",
            "path": _repo_path(BMMB_HISTORICAL_CSV),
            "sha256": sha256_file(BMMB_HISTORICAL_CSV),
            "licence": "Public-reference QC government report; not strict-open.",
            "evidence_tier": "benchmark_historical",
            "strict_open": "false",
            "used_for": "QC Crown-only historical reference; not used as a current price.",
        },
        {
            "source_key": "provincial_allocation_119m",
            "title": "Provincial harvest allocation, 119 Mt case",
            "publisher": "This repository, derived from documented AAC/planning values",
            "year": "2026",
            "url": "",
            "path": _repo_path(PROVINCIAL_ALLOCATION_CSV),
            "sha256": sha256_file(PROVINCIAL_ALLOCATION_CSV),
            "licence": "Derived public-reference data; not strict-open.",
            "evidence_tier": "screening_allocation",
            "strict_open": "false",
            "used_for": "National volume weights only.",
        },
        {
            "source_key": "slope_screen_960m",
            "title": "National 960 m slope screen and operable overlay by province",
            "publisher": "Natural Resources Canada MRDEM-30, derived",
            "year": "2026",
            "url": "",
            "path": _repo_path(SLOPE_OVERLAY_CSV),
            "sha256": sha256_file(SLOPE_OVERLAY_CSV),
            "licence": "Open Government Licence - Canada 2.0 for source raster; derived aggregate.",
            "evidence_tier": "open_derived_aggregate",
            "strict_open": "true",
            "used_for": "Provincial steep-slope share for the terrain factor.",
        },
        {
            "source_key": "operable_proxy_v2_road_distance",
            "title": "National Road Network derived road-distance classes",
            "publisher": "Natural Resources Canada NRN, derived",
            "year": "2026",
            "url": "",
            "path": _repo_path(ROAD_DISTANCE_CSV),
            "sha256": sha256_file(ROAD_DISTANCE_CSV),
            "licence": "Open Government Licence - Canada 2.0 for source NRN; derived aggregate.",
            "evidence_tier": "open_derived_aggregate",
            "strict_open": "true",
            "used_for": "Provincial >20 km road-distance share for the road factor.",
        },
        {
            "source_key": "national_economics_v1_assumptions",
            "title": "Explicit donor, factor and coverage assumptions of national_economics_v1",
            "publisher": "This repository",
            "year": "2026",
            "url": "",
            "path": _repo_path(Path(__file__)),
            "sha256": sha256_file(Path(__file__)),
            "licence": "CC0 1.0 Universal public-domain dedication for this model's assumptions.",
            "evidence_tier": "model_assumption",
            "strict_open": "true",
            "used_for": "Wage/diesel index, terrain/road elasticities, donor rules and labels.",
        },
    ]


def _margin_summary_payload(
    margin_rows: Sequence[Mapping[str, Any]], summary_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    base_rows = [row for row in margin_rows if row["scenario_id"] == "base"]
    base_values = [float(row["margin_cad_m3"]) for row in base_rows]
    negative = [
        {
            "province": row["province"],
            "scenario_id": row["scenario_id"],
            "margin_cad_m3": row["margin_cad_m3"],
        }
        for row in margin_rows
        if float(row["margin_cad_m3"]) < 0.0
    ]
    national = next(row for row in summary_rows if row["row_type"] == "national")
    return {
        "base_min_cad_m3": min(base_values),
        "base_max_cad_m3": max(base_values),
        "base_median_cad_m3": statistics.median(base_values),
        "negative_count": len(negative),
        "negative_documented": True,
        "negative_rows": negative,
        "national": {
            "allocation_volume_m3": float(national["allocation_volume_m3"]),
            "margin_low_cad_m3": float(national["margin_low_cad_m3"]),
            "margin_base_cad_m3": float(national["margin_base_cad_m3"]),
            "margin_high_cad_m3": float(national["margin_high_cad_m3"]),
            "label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": False,
        },
    }


def build_summary(
    blend: BcPriceBlend,
    distribution: BcCostDistribution,
    prices: Mapping[str, PriceProxy],
    costs: Mapping[str, CostProxy],
    qc_evidence: QcEvidence,
    road_allowance: Allowance,
    silviculture_allowance: Allowance,
    margin_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
) -> dict[str, Any]:
    price_tiers = Counter(price.price_tier for price in prices.values())
    cost_tiers = Counter(cost.cost_tier for cost in costs.values())
    road_tiers = Counter(cost.road_tier for cost in costs.values())
    silviculture_tiers = Counter(cost.silviculture_tier for cost in costs.values())
    margin_payload = _margin_summary_payload(margin_rows, summary_rows)
    qc_cost = costs["QC"]
    return {
        "model_id": "national_economics_v1",
        "artifact_version": "1.0.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "model_estimate_scaffold",
        "description": (
            "National provincial price, cost and margin scaffold covering all 13 "
            "provinces and territories with explicit donor/proxy tiers."
        ),
        "coverage": {
            "provinces": list(PROVINCES),
            "province_count": len(PROVINCES),
            "missing": [],
            "not_estimated_count": 0,
            "margin_rows": len(margin_rows),
            "scenarios": ["low", "base", "high"],
            "nu_zero_allocation_volume": allocation["NU"] == 0.0,
        },
        "price_panel": {
            "report_basis": PRICE_REPORT_BASIS,
            "period_end": PRICE_PERIOD_END,
            "price_basis": PRICE_BASIS,
            "currency_basis": PRICE_CURRENCY,
            "bc_coast": {
                "low_cad_m3": blend.coast.low_cad_m3,
                "base_cad_m3": blend.coast.base_cad_m3,
                "high_cad_m3": blend.coast.high_cad_m3,
                "volume_m3": blend.coast.volume_m3,
                "report_ids": list(blend.coast.report_ids),
            },
            "bc_interior": {
                "low_cad_m3": blend.interior.low_cad_m3,
                "base_cad_m3": blend.interior.base_cad_m3,
                "high_cad_m3": blend.interior.high_cad_m3,
                "volume_m3": blend.interior.volume_m3,
                "report_ids": list(blend.interior.report_ids),
            },
            "bc_blend": {
                "low_cad_m3": blend.low_cad_m3,
                "base_cad_m3": blend.base_cad_m3,
                "high_cad_m3": blend.high_cad_m3,
                "coast_weight": blend.coast_weight,
                "interior_weight": blend.interior_weight,
                "method": "published_report_volume_weighted",
            },
            "non_bc_rule": "BC donor price, index 1.00, no provincial index materialised",
        },
        "cost_panel": {
            "bc_distribution": {
                "low_cad_m3": distribution.low_cad_m3,
                "base_cad_m3": distribution.base_cad_m3,
                "high_cad_m3": distribution.high_cad_m3,
                "low_method": "p10(total_low)",
                "base_method": "median(total_base)",
                "high_method": "p90(total_high)",
                "p10_base_cad_m3": distribution.p10_base_cad_m3,
                "p90_base_cad_m3": distribution.p90_base_cad_m3,
                "by_system_base_median": distribution.by_system_base_median,
                "by_zone_base_median": distribution.by_zone_base_median,
            },
            "wage_index": WAGE_INDEX,
            "diesel_index": DIESEL_INDEX,
            "terrain_elasticity": TERRAIN_ELASTICITY,
            "road_elasticity": ROAD_ELASTICITY,
            "road_allowance": {
                "low_cad_m3": road_allowance.low_cad_m3,
                "base_cad_m3": road_allowance.base_cad_m3,
                "high_cad_m3": road_allowance.high_cad_m3,
                "source": road_allowance.source,
                "tier": road_allowance.tier,
                "high_uncertainty": ROAD_HIGH_UNCERTAINTY,
            },
            "silviculture_allowance": {
                "low_cad_m3": silviculture_allowance.low_cad_m3,
                "base_cad_m3": silviculture_allowance.base_cad_m3,
                "high_cad_m3": silviculture_allowance.high_cad_m3,
                "source": silviculture_allowance.source,
                "tier": silviculture_allowance.tier,
            },
            "no_cad_per_km_conversion": True,
        },
        "qc_evidence": {
            "purchase_cost_cad_m3": qc_evidence.purchase_cost_cad_m3,
            "cost_spread_after_transfers_cad_m3": (
                qc_evidence.cost_spread_after_transfers_cad_m3
            ),
            "bmmb_crown_rate_cad_m3": qc_evidence.bmmb_crown_rate_cad_m3,
            "harvest_total_cad_m3": qc_evidence.harvest_total_cad_m3,
            "haul_total_cad_m3": qc_evidence.haul_total_cad_m3,
            "road_before_prccm_cad_m3": qc_evidence.road_before_prccm_cad_m3,
            "road_after_prccm_cad_m3": qc_evidence.road_after_prccm_cad_m3,
            "direct_cost_total_cad_m3": (
                qc_evidence.harvest_total_cad_m3
                + qc_evidence.haul_total_cad_m3
                + qc_evidence.road_before_prccm_cad_m3
            ),
            "model_crosscheck_ratio": qc_cost.qc_crosscheck_ratio,
            "model_crosscheck_status": qc_cost.qc_crosscheck_status,
            "price_status": "no_current_delivered_log_price",
            "purchase_cost_used_as_price": False,
            "bmmb_used_as_price": False,
        },
        "national_diagnostic": margin_payload["national"],
        "margins": {
            "base_min_cad_m3": margin_payload["base_min_cad_m3"],
            "base_max_cad_m3": margin_payload["base_max_cad_m3"],
            "base_median_cad_m3": margin_payload["base_median_cad_m3"],
            "negative_count": margin_payload["negative_count"],
            "negative_documented": True,
            "negative_rows": margin_payload["negative_rows"],
        },
        "tiers": {
            "price": dict(price_tiers),
            "cost": dict(cost_tiers),
            "road": dict(road_tiers),
            "silviculture": dict(silviculture_tiers),
        },
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        },
        "licensing": LICENSING,
        "assumptions": [
            "BC price is a reported-average blend of Coast and Interior 2025-12 3-month reports.",
            "All non-BC prices are BC donor proxies with price index 1.00 because no provincial index is materialised.",
            "All costs start from the BC 945-row logging+haul distribution.",
            "Provincial wage and diesel factors are 1.00 assumptions.",
            "Terrain and road factors are explicit elasticities, not estimated cost functions.",
            "QC road and BC Interior silviculture are published CAD/m3 allowances used nationally as donors.",
            "No CAD/km or CAD/each road/bridge/culvert value is converted to CAD/m3.",
            "QC direct 2022 harvest+haul+road is a cross-check only.",
            "National diagnostic uses all 13 jurisdictions and the 119 Mt allocation, not a BC+QC-only total.",
        ],
        "validation": {
            "all_provinces_present": True,
            "no_not_estimated": True,
            "price_ranges_ordered": True,
            "cost_ranges_ordered": True,
            "margin_ranges_ordered": True,
            "no_nan_or_infinite": True,
            "labels_complete": True,
            "negative_margins_documented": True,
            "qc_crosscheck_within_2pct": qc_cost.qc_crosscheck_status == "within_2pct",
            "no_restricted_inputs": True,
            "strict_open_claim": False,
            "no_cad_per_km_conversion": True,
            "combined_national_net_value": False,
        },
        "limitations": [
            "No provincial delivered-log price series exists outside BC in the materialised evidence.",
            "No provincial wage or diesel index is materialised; both factors are 1.00 assumptions.",
            "Terrain and road factors are explicit engineering elasticities, not estimated cost functions.",
            "BC logging+haul is a proxy model, not an observed contractor cost survey.",
            "QC road and BC silviculture are 2022/2026 nominal values; no deflator is applied.",
            "No grade, species, tenure or mill matching is performed.",
            "No CAD/km road, bridge or culvert conversion is attempted.",
            "The national diagnostic is a volume-weighted model estimate, not a policy result.",
            "NU has zero allocation volume and no commercial harvest is asserted.",
        ],
        "non_claims": [
            "Not observed profit.",
            "Not a mill margin.",
            "Not an official net value.",
            "Not a stumpage rate.",
            "Not policy grade.",
            "Not a combined BC+QC-only national total.",
            "Not a strict-open dataset.",
        ],
    }


def render_report(
    summary: Mapping[str, Any],
    margin_summary_rows: Sequence[Mapping[str, Any]],
    price_rows: Sequence[Mapping[str, Any]],
    cost_rows: Sequence[Mapping[str, Any]],
) -> str:
    def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        lines.extend("| " + " | ".join(row) + " |" for row in rows)
        return "\n".join(lines)

    price_table = table(
        ("Province", "Low", "Base", "High", "Tier", "Donor", "Index"),
        [
            (
                row["province"],
                row["price_low_cad_m3"],
                row["price_base_cad_m3"],
                row["price_high_cad_m3"],
                row["price_tier"],
                row["donor_province"] or "self",
                row["price_index"],
            )
            for row in price_rows
        ],
    )
    cost_table = table(
        (
            "Province",
            "Logging+haul low",
            "Base",
            "High",
            "Road base",
            "Silv. base",
            "Total base",
            "Index",
            "Cost tier",
        ),
        [
            (
                row["province"],
                row["logging_haul_low_cad_m3"],
                row["logging_haul_base_cad_m3"],
                row["logging_haul_high_cad_m3"],
                row["road_base_cad_m3"],
                row["silviculture_base_cad_m3"],
                row["total_cost_base_cad_m3"],
                row["combined_cost_index"],
                row["cost_tier"],
            )
            for row in cost_rows
        ],
    )
    margin_table = table(
        (
            "Province",
            "Volume m3",
            "Margin low",
            "Margin base",
            "Margin high",
            "Base status",
        ),
        [
            (
                row["province"],
                row["allocation_volume_m3"],
                row["margin_low_cad_m3"],
                row["margin_base_cad_m3"],
                row["margin_high_cad_m3"],
                row["margin_status"],
            )
            for row in margin_summary_rows
        ],
    )
    validation = summary["validation"]
    validation_table = table(
        ("Check", "Status", "Detail"),
        [
            (
                "All 13 jurisdictions",
                "PASS" if validation["all_provinces_present"] else "FAIL",
                "13 provinces/territories",
            ),
            (
                "No not_estimated",
                "PASS" if validation["no_not_estimated"] else "FAIL",
                "0 rows",
            ),
            (
                "Ranges ordered",
                "PASS" if validation["margin_ranges_ordered"] else "FAIL",
                "low<=base<=high",
            ),
            (
                "Finite outputs",
                "PASS" if validation["no_nan_or_infinite"] else "FAIL",
                "no NaN/Inf",
            ),
            (
                "QC cross-check",
                "PASS" if validation["qc_crosscheck_within_2pct"] else "FAIL",
                str(summary["qc_evidence"]["model_crosscheck_ratio"]),
            ),
            (
                "No restricted inputs",
                "PASS" if validation["no_restricted_inputs"] else "FAIL",
                "aggregate/public-reference only",
            ),
            (
                "No CAD/km conversion",
                "PASS" if validation["no_cad_per_km_conversion"] else "FAIL",
                "CAD/m3 only",
            ),
            ("Strict-open claim", "PASS", "false; public-reference copyrighted inputs"),
        ],
    )
    negative_rows = summary["margins"]["negative_rows"]
    negative_note = (
        f"{summary['margins']['negative_count']} negative margin rows are documented. "
        "The low case is a conservative envelope (low price minus high cost); "
        "negative values are not clipped."
        if negative_rows
        else "No negative margin rows."
    )
    national = summary["national_diagnostic"]
    return f"""# National Economics Scaffold v1

Status: **model estimate scaffold — not policy grade, not observed, not a
strict-open dataset.** Generated {summary["generated_utc"]}.

## 1. Scope and coverage

All **13 provinces and territories** are covered: {", ".join(summary["coverage"]["provinces"])}.
There are **{summary["coverage"]["margin_rows"]} margin rows** (13 jurisdictions x low/base/high)
and **no `not_estimated`** coverage gaps. NU has zero allocation volume; its
margin is a proxy only and no commercial harvest is asserted.

## 2. Method

### 2.1 Price

- **BC:** published 2025-12 3-month Coast and Interior mill-delivered report
  averages, volume-weighted by the published report totals. This is a
  `reported_average`, not a transaction record.
- **All other jurisdictions:** BC donor price with an explicit price index of
  **1.00**. No provincial wage, diesel or price index is materialised in the
  repository, so the index is a labelled national-median donor assumption.
- **QC:** no current delivered-log price. The QC 2022 purchase cost
  **{summary["qc_evidence"]["purchase_cost_cad_m3"]:.2f}** and BMMB Crown-only
  historical rate **{summary["qc_evidence"]["bmmb_crown_rate_cad_m3"]:.4f}** are
  references only and are **not** used as price inputs.

### 2.2 Cost

- Start from the **945-row BC logging+haul distribution**:
  low = p10(total_low), base = median(total_base), high = p90(total_high).
- Apply explicit factors: wage **1.00**, diesel **1.00**, terrain
  `1 + 0.25 * (province share_gt15 - BC share_gt15)`, road
  `1 + 0.15 * (province road_gt20_share - BC road_gt20_share)`, clamped to
  [0.75, 1.25].
- Add a **road allowance** from the published QC 2022 T5 CAD/m3 values
  ({summary["cost_panel"]["road_allowance"]["low_cad_m3"]:.2f} /
  {summary["cost_panel"]["road_allowance"]["base_cad_m3"]:.2f} /
  {summary["cost_panel"]["road_allowance"]["high_cad_m3"]:.2f}).
- Add a **silviculture allowance** from the published BC Interior basic
  silviculture CAD/m3 zone range
  ({summary["cost_panel"]["silviculture_allowance"]["low_cad_m3"]:.2f} /
  {summary["cost_panel"]["silviculture_allowance"]["base_cad_m3"]:.2f} /
  {summary["cost_panel"]["silviculture_allowance"]["high_cad_m3"]:.2f}).
- **No CAD/km or CAD/each road, bridge or culvert value is converted to CAD/m3.**

### 2.3 Margin

```
margin = price - logging_haul - road - silviculture
```

- **low:** low price minus high logging+haul, road and silviculture.
- **base:** base price minus base costs.
- **high:** high price minus low costs.
- Unclipped; negative values are documented, not suppressed.

Every row is `evidence=model_estimate`, `observed=false`,
`policy_grade=false`, `net_value_status=model_estimate` and
`combined_national_net_value=false`.

## 3. Price proxies (CAD/m3 nominal 2025)

{price_table}

## 4. Cost proxies (CAD/m3, mixed nominal 2022-2026, no deflator)

{cost_table}

## 5. Provincial margins (CAD/m3)

{margin_table}

**Negative margin note:** {negative_note}

## 6. National diagnostic

The national row is a volume-weighted average across **all 13 jurisdictions**
using the 119 Mt provincial allocation:

| Item | Value |
|---|---:|
| Allocation volume (m3/yr) | {national["allocation_volume_m3"]:.2f} |
| Weighted margin low | {national["margin_low_cad_m3"]:.4f} |
| Weighted margin base | {national["margin_base_cad_m3"]:.4f} |
| Weighted margin high | {national["margin_high_cad_m3"]:.4f} |
| Label | `{national["label"]}` |
| Combined national net value | `false` |

This is **not** a BC+QC-only total and not a policy result.

## 7. Observed vs proxy

| Layer | BC | QC | Other jurisdictions |
|---|---|---|---|
| Price | reported average | donor proxy (no delivered price) | donor proxy |
| Logging+haul | proxy model | donor proxy + QC cross-check | donor proxy |
| Road | QC donor | reported average (2022 CAD/m3) | QC donor |
| Silviculture | reported BC allowance | BC donor | BC donor |
| Margin | model estimate | model estimate | model estimate |

The QC direct 2022 harvest+haul+road total is
**{summary["qc_evidence"]["direct_cost_total_cad_m3"]:.2f} CAD/m3**; the model's
QC logging+haul+road base is compared at a ratio of
**{summary["qc_evidence"]["model_crosscheck_ratio"]:.4f}** ({summary["qc_evidence"]["model_crosscheck_status"]}).
This is a cross-check, not a second input.

## 8. Validation

{validation_table}

Negative margins: {summary["margins"]["negative_count"]} rows, documented in
`summary.json` and `national_margin_estimates.csv`.

## 9. Licensing and non-claims

**No strict-open claim.** Inputs are public-reference copyrighted sources or
this model's own explicit assumptions. Source PDFs are not redistributed.
Commercial reuse requires permission from the rights holder.

Non-claims: not observed profit; not a mill margin; not an official net value;
not a stumpage rate; not policy grade; not a combined BC+QC-only national
total; not a strict-open dataset.

## 10. Files

| File | Content |
|---|---|
| `national_price_proxies.csv` | Province price low/base/high and tiers |
| `national_cost_proxies.csv` | Province logging+haul, road, silviculture and total cost |
| `national_margin_estimates.csv` | 39 province x scenario margin rows |
| `national_margin_summary.csv` | Province summary plus national weighted diagnostic |
| `assumptions_donor_register.csv` | Every donor, factor and coverage assumption |
| `sources.csv` | Source register with hashes and licences |
| `summary.json` | Machine-readable summary, validation and labels |
| `manifest.json` | Input/output hashes and coverage |
"""


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _validate_outputs(
    margin_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    prices: Mapping[str, PriceProxy],
    costs: Mapping[str, CostProxy],
) -> None:
    if set(prices) != set(PROVINCES):
        raise NationalEconomicsError("price proxy coverage mismatch")
    if set(costs) != set(PROVINCES):
        raise NationalEconomicsError("cost proxy coverage mismatch")
    for province in PROVINCES:
        price = prices[province]
        cost = costs[province]
        _require_ordered(
            price.low_cad_m3, price.base_cad_m3, price.high_cad_m3, f"{province} price"
        )
        _require_ordered(
            cost.logging_haul_low_cad_m3,
            cost.logging_haul_base_cad_m3,
            cost.logging_haul_high_cad_m3,
            f"{province} logging+haul",
        )
        _require_ordered(
            cost.total_cost_low_cad_m3,
            cost.total_cost_base_cad_m3,
            cost.total_cost_high_cad_m3,
            f"{province} total cost",
        )
        province_rows = [row for row in margin_rows if row["province"] == province]
        if len(province_rows) != 3:
            raise NationalEconomicsError(
                f"expected 3 margin scenarios for {province}, found {len(province_rows)}"
            )
        by_scenario = {row["scenario_id"]: row for row in province_rows}
        low = float(by_scenario["low"]["margin_cad_m3"])
        base = float(by_scenario["base"]["margin_cad_m3"])
        high = float(by_scenario["high"]["margin_cad_m3"])
        _require_ordered(low, base, high, f"{province} margin")
    for row in margin_rows:
        for column in (
            "price_cad_m3",
            "logging_haul_cad_m3",
            "road_cad_m3",
            "silviculture_cad_m3",
            "total_cost_cad_m3",
            "margin_cad_m3",
            "allocation_volume_m3",
            "volume_weighted_margin_cad_m3",
        ):
            value = float(row[column])
            if not math.isfinite(value):
                raise NationalEconomicsError(
                    f"non-finite {column} in margin row: {row}"
                )
        if row["evidence"] != EVIDENCE:
            raise NationalEconomicsError(f"bad evidence label: {row}")
        if row["observed"] != OBSERVED:
            raise NationalEconomicsError(f"bad observed label: {row}")
        if row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsError(f"bad policy_grade label: {row}")
        if row["net_value_status"] != NET_VALUE_STATUS:
            raise NationalEconomicsError(f"bad net_value_status label: {row}")
        if row["combined_national_net_value"] != COMBINED_NATIONAL_NET_VALUE:
            raise NationalEconomicsError(f"bad combined label: {row}")
        if row["price_tier"] not in {
            PRICE_TIER_OBSERVED,
            PRICE_TIER_REPORTED,
            PRICE_TIER_DONOR,
            PRICE_TIER_ASSUMPTION,
        }:
            raise NationalEconomicsError(f"bad price tier: {row}")
        for value in row.values():
            if str(value).strip().lower() == "not_estimated":
                raise NationalEconomicsError(f"not_estimated value found: {row}")
    if len(summary_rows) != len(PROVINCES) + 1:
        raise NationalEconomicsError(
            "margin summary must have 13 provinces plus national"
        )
    national = next(row for row in summary_rows if row["row_type"] == "national")
    if national["combined_national_net_value"] != COMBINED_NATIONAL_NET_VALUE:
        raise NationalEconomicsError("national row must not be combined net value")


def build(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Any]:
    """Build the national scaffold and write all artifacts."""
    for path in (
        BC_LOG_MARKET_PRICES_CSV,
        BC_COST_ESTIMATES_CSV,
        BC_APPRAISAL_COSTS_CSV,
        QC_SURVEY_CSV,
        QC_PROCUREMENT_CSV,
        BMMB_HISTORICAL_CSV,
        PROVINCIAL_ALLOCATION_CSV,
        SLOPE_OVERLAY_CSV,
        ROAD_DISTANCE_CSV,
    ):
        _assert_not_restricted(path)

    blend = load_bc_price_blend()
    distribution = load_bc_cost_distribution()
    slope = load_slope_context()
    road_context = load_road_context()
    qc_evidence = load_qc_evidence()
    road_allowance = load_road_allowance()
    silviculture_allowance = load_silviculture_allowance()
    allocation = load_allocation_volumes()
    prices = build_price_proxies(blend, qc_evidence)
    costs = build_cost_proxies(
        distribution,
        slope,
        road_context,
        road_allowance,
        silviculture_allowance,
        qc_evidence,
    )
    margin_rows = build_margin_rows(prices, costs, allocation)
    summary_rows = build_margin_summary(margin_rows, allocation, prices, costs)
    _validate_outputs(margin_rows, summary_rows, prices, costs)

    summary = build_summary(
        blend,
        distribution,
        prices,
        costs,
        qc_evidence,
        road_allowance,
        silviculture_allowance,
        margin_rows,
        summary_rows,
        allocation,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    price_rows = _price_rows(prices, blend)
    cost_rows = _cost_rows(costs)
    assumption_rows = _assumption_rows(
        qc_evidence, road_allowance, silviculture_allowance
    )
    source_rows = _source_rows()

    _write_csv(
        out_dir / PRICE_CSV,
        (
            "province",
            "province_name",
            "price_low_cad_m3",
            "price_base_cad_m3",
            "price_high_cad_m3",
            "price_basis",
            "price_year",
            "currency_basis",
            "price_tier",
            "donor_province",
            "price_index",
            "price_index_basis",
            "bc_coast_price_low_cad_m3",
            "bc_coast_price_base_cad_m3",
            "bc_coast_price_high_cad_m3",
            "bc_interior_price_low_cad_m3",
            "bc_interior_price_base_cad_m3",
            "bc_interior_price_high_cad_m3",
            "bc_coast_volume_weight",
            "bc_interior_volume_weight",
            "qc_purchase_cost_reference_cad_m3",
            "qc_bmmb_crown_benchmark_cad_m3",
            "qc_cost_spread_after_transfers_cad_m3",
            "qc_price_status",
            "evidence",
            "observed",
            "policy_grade",
            "net_value_status",
            "combined_national_net_value",
            "strict_open",
            "notes",
        ),
        price_rows,
    )
    _write_csv(
        out_dir / COST_CSV,
        (
            "province",
            "province_name",
            "logging_haul_low_cad_m3",
            "logging_haul_base_cad_m3",
            "logging_haul_high_cad_m3",
            "wage_factor",
            "diesel_factor",
            "terrain_factor",
            "road_factor",
            "combined_cost_index",
            "terrain_share_gt15",
            "road_gt20_share",
            "road_low_cad_m3",
            "road_base_cad_m3",
            "road_high_cad_m3",
            "silviculture_low_cad_m3",
            "silviculture_base_cad_m3",
            "silviculture_high_cad_m3",
            "total_cost_low_cad_m3",
            "total_cost_base_cad_m3",
            "total_cost_high_cad_m3",
            "logging_haul_tier",
            "road_tier",
            "silviculture_tier",
            "cost_tier",
            "cost_currency",
            "qc_direct_harvest_haul_cad_m3",
            "qc_direct_road_cad_m3",
            "qc_direct_total_cad_m3",
            "qc_crosscheck_ratio",
            "qc_crosscheck_status",
            "evidence",
            "observed",
            "policy_grade",
            "net_value_status",
            "combined_national_net_value",
            "strict_open",
            "notes",
        ),
        cost_rows,
    )
    _write_csv(
        out_dir / MARGIN_CSV,
        (
            "province",
            "province_name",
            "scenario_id",
            "scenario_order",
            "price_cad_m3",
            "logging_haul_cad_m3",
            "road_cad_m3",
            "silviculture_cad_m3",
            "total_cost_cad_m3",
            "margin_cad_m3",
            "margin_formula",
            "price_tier",
            "logging_haul_tier",
            "road_tier",
            "silviculture_tier",
            "cost_tier",
            "evidence",
            "observed",
            "policy_grade",
            "net_value_status",
            "combined_national_net_value",
            "allocation_volume_m3",
            "volume_weighted_margin_cad_m3",
            "margin_status",
            "notes",
        ),
        margin_rows,
    )
    _write_csv(
        out_dir / SUMMARY_CSV,
        (
            "row_type",
            "province",
            "province_name",
            "allocation_volume_m3",
            "volume_share",
            "price_base_cad_m3",
            "total_cost_base_cad_m3",
            "margin_low_cad_m3",
            "margin_base_cad_m3",
            "margin_high_cad_m3",
            "margin_status",
            "price_tier",
            "cost_tier",
            "national_diagnostic_label",
            "combined_national_net_value",
            "notes",
        ),
        summary_rows,
    )
    _write_csv(
        out_dir / ASSUMPTIONS_CSV,
        (
            "assumption_id",
            "category",
            "applies_to",
            "rule",
            "value",
            "unit",
            "source",
            "tier",
            "notes",
        ),
        assumption_rows,
    )
    _write_csv(
        out_dir / SOURCES_CSV,
        (
            "source_key",
            "title",
            "publisher",
            "year",
            "url",
            "path",
            "sha256",
            "licence",
            "evidence_tier",
            "strict_open",
            "used_for",
        ),
        source_rows,
    )

    summary_path = out_dir / SUMMARY_JSON
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    report_path = out_dir / REPORT_MD
    report_path.write_text(
        render_report(summary, summary_rows, price_rows, cost_rows), encoding="utf-8"
    )

    input_paths = (
        BC_LOG_MARKET_PRICES_CSV,
        BC_LOG_MARKET_MANIFEST_JSON,
        BC_COST_ESTIMATES_CSV,
        BC_COST_MANIFEST_JSON,
        BC_APPRAISAL_COSTS_CSV,
        BC_APPRAISAL_MANIFEST_JSON,
        QC_SURVEY_CSV,
        QC_SURVEY_MANIFEST_JSON,
        QC_PROCUREMENT_CSV,
        QC_PROCUREMENT_MANIFEST_JSON,
        BMMB_HISTORICAL_CSV,
        PROVINCIAL_ALLOCATION_CSV,
        PROVINCIAL_ALLOCATION_SUMMARY_JSON,
        SLOPE_OVERLAY_CSV,
        SLOPE_MANIFEST_JSON,
        ROAD_DISTANCE_CSV,
        ROAD_MANIFEST_JSON,
    )
    output_paths = (
        out_dir / PRICE_CSV,
        out_dir / COST_CSV,
        out_dir / MARGIN_CSV,
        out_dir / SUMMARY_CSV,
        out_dir / ASSUMPTIONS_CSV,
        out_dir / SOURCES_CSV,
        summary_path,
        report_path,
    )
    manifest = {
        "model_id": "national_economics_v1",
        "artifact_version": "1.0.0",
        "generated_utc": summary["generated_utc"],
        "observation_type": "model_estimate",
        "status": "model_estimate_scaffold",
        "restricted_inputs_used": [],
        "labels": summary["labels"],
        "coverage": summary["coverage"],
        "validation": summary["validation"],
        "sources": source_rows,
        "assumptions": summary["assumptions"],
        "limitations": summary["limitations"],
        "inputs": [
            {
                "path": _repo_path(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in input_paths
        ],
        "outputs": [
            {
                "path": _repo_path(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "rows": (
                    len(margin_rows)
                    if path.name == MARGIN_CSV
                    else len(summary_rows)
                    if path.name == SUMMARY_CSV
                    else len(price_rows)
                    if path.name == PRICE_CSV
                    else len(cost_rows)
                    if path.name == COST_CSV
                    else len(assumption_rows)
                    if path.name == ASSUMPTIONS_CSV
                    else len(source_rows)
                    if path.name == SOURCES_CSV
                    else None
                ),
            }
            for path in output_paths
        ],
    }
    (out_dir / MANIFEST_JSON).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    summary = build(args.out_dir)
    print(
        "national_economics_v1: "
        f"{summary['coverage']['province_count']} provinces, "
        f"{summary['coverage']['margin_rows']} margin rows, "
        f"national base margin {summary['national_diagnostic']['margin_base_cad_m3']:.4f} CAD/m3, "
        f"negative rows {summary['margins']['negative_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
