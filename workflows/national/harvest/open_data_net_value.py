"""Open-data BC modeled margin, not observed profit.

The model combines three public layers:

* published mill-delivered BC log-market averages (Coast and Interior);
* the open-data BC logging and truck-haul cost model (proxy cost);
* the published Interior basic silviculture CAD/m3 allowance by forest zone.

It produces a **modeled margin**:

    gross_margin = mill_delivered_price - logging_cost - haul_cost
    net_model    = gross_margin - Interior_basic_silviculture_CAD_m3_if_available

The silviculture deduction is Interior-only.  Coast basic silviculture is
published in CAD/ha by BEC unit and is not converted to CAD/m3, so Coast
``net_model`` equals ``gross_margin``.

Every row is ``evidence=model_estimate``, ``observed=false`` and
``policy_grade=false``.  The result is not an observed transaction, a mill
profit, an official net value, or a legal/AAC/policy statement.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.open_data_net_value
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"

COST_MODEL_DIR = HARVEST_BASIS_DIR / "bc_open_cost_model"
LOG_MARKET_DIR = HARVEST_BASIS_DIR / "bc_log_market"
APPRAISAL_DIR = HARVEST_BASIS_DIR / "bc_appraisal_costs"
PROCUREMENT_DIR = HARVEST_BASIS_DIR / "procurement_benchmark"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "open_data_net_value"

COST_ESTIMATES_CSV = COST_MODEL_DIR / "bc_open_cost_estimates.csv"
COST_MANIFEST_JSON = COST_MODEL_DIR / "manifest.json"
LOG_MARKET_PRICES_CSV = LOG_MARKET_DIR / "bc_log_market_prices.csv"
LOG_MARKET_MANIFEST_JSON = LOG_MARKET_DIR / "manifest.json"
APPRAISAL_CSV = APPRAISAL_DIR / "bc_appraisal_costs.csv"
APPRAISAL_MANIFEST_JSON = APPRAISAL_DIR / "manifest.json"
PROCUREMENT_CSV = PROCUREMENT_DIR / "qc_procurement_cost_benchmark.csv"
PROCUREMENT_MANIFEST_JSON = PROCUREMENT_DIR / "manifest.json"

BUNDLE_COST_INPUTS_CSV = "bc_cost_inputs_used.csv"
BUNDLE_PRICE_PANEL_CSV = "bc_price_panel_used.csv"
BUNDLE_SILVICULTURE_CSV = "silviculture_inputs_used.csv"
BUNDLE_QC_REFERENCE_CSV = "qc_reference_used.csv"

MODEL_VERSION = "1.1.0"
PRIMARY_PRICE_BASIS = "3_month"
PRIMARY_PRICE_PERIOD_END = "2025-12-31"
SENSITIVITY_PRICE_BASIS = "1_month"
SENSITIVITY_PRICE_PERIOD_END = "2025-12-31"

SILVICULTURE_SERIES = "Interior Basic Silviculture 2026-10"
SILVICULTURE_EFFECTIVE_DATE = "2026-10-01"
SILVICULTURE_COMPONENT = "basic_silviculture"
SILVICULTURE_PRICE_REGION = "Interior"
COAST_SILVICULTURE_COMPONENT = "not_published_cad_m3_for_coast"
COAST_SILVICULTURE_BASIS = "cam_table_5_6_cad_per_ha_only_not_converted"

EXPECTED_COST_ROWS = 945
EXPECTED_ZONES = (
    "South West Zone",
    "North West Zone",
    "North Central Zone",
    "North East Zone",
    "North Fort Nelson Zone",
    "South Central Zone",
    "South East Zone",
)
EXPECTED_SYSTEMS = ("ground", "cable", "heli")
EXPECTED_ZONE_SYSTEM_GROUPS = len(EXPECTED_ZONES) * len(EXPECTED_SYSTEMS)

# The cost model publishes two rate groups for seven forest zones.  Its
# ``appraisal_region`` is the IAM/CAM jurisdiction and the only valid price
# crosswalk.  South West Zone is the Coast zone.  North West Zone is an
# Interior forest zone: the TLA rate guide files it under the
# coastal/northwest group for hourly rates, but it is priced and costed as
# Interior.  The published CAD/m3 silviculture table is Interior-only.
ZONE_PRICE_REGION: dict[str, str] = {
    "South West Zone": "Coast",
    "North West Zone": "Interior",
    "North Central Zone": "Interior",
    "North East Zone": "Interior",
    "North Fort Nelson Zone": "Interior",
    "South Central Zone": "Interior",
    "South East Zone": "Interior",
}

EVIDENCE_MODEL = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
PRICE_BASIS = "mill_delivered_reported_average"
COST_BASIS = "open_proxy_model"
NET_VALUE_STATUS = "model_estimate"
QC_NET_VALUE_STATUS = "separate_reference_not_net_value"
QC_EVIDENCE = "survey_procurement_cost"

LICENSING: dict[str, Any] = {
    "classification": "public_reference_copyrighted",
    "strict_open_licence": False,
    "strict_open_licence_sources": [],
    "public_reference_source_families": [
        "BC Government appraisal manuals and silviculture tables (BC Crown copyright)",
        "BC log-market reports (BC Crown copyright; private study/research use)",
        "TLA/ILA/NWLA/MNP 2026 rate guide (industry publisher copyright)",
        "FPInnovations/FERIC case studies (publisher copyright)",
        "BC CVSE / Commercial Transport Regulations (BC Crown copyright)",
        "Gouvernement du Québec MRNF 2022 procurement survey (public-reference report)",
    ],
    "source_pdfs_redistributed": False,
    "commercial_reuse": "requires_permission_from_rights_holder",
    "statement": (
        "All inputs are public-reference copyrighted sources. None is released "
        "under a strict Open Government Licence, CC-BY, or Statistics Canada "
        "open licence. Source PDFs are not redistributed in this repository. "
        "Commercial reuse or redistribution requires permission from the "
        "rights holder."
    ),
}

PRICE_OUTLIER_CSV = "bc_open_price_outliers.csv"
OUTLIER_FIELDS: tuple[str, ...] = (
    "report_id",
    "region",
    "report_basis",
    "period_end",
    "log_class",
    "species",
    "grade",
    "end_use",
    "price_cad_m3",
    "price_plausibility",
    "is_aggregate",
    "used_in_model",
    "notes",
)
NET_VALUE_CSV = "bc_open_net_value.csv"
SUMMARY_CSV = "bc_open_net_value_summary.csv"
SENSITIVITY_CSV = "bc_open_net_value_sensitivity.csv"
QC_REFERENCE_CSV = "qc_open_reference.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "OPEN_DATA_NET_VALUE_REPORT.md"
MANIFEST_JSON = "manifest.json"


class OpenDataNetValueError(ValueError):
    """Raised when an input or output violates the documented contract."""


@dataclass(frozen=True)
class PricePanel:
    region: str
    low_cad_m3: float
    base_cad_m3: float
    high_cad_m3: float
    method: str
    report_basis: str
    period_end: str
    report_ids: tuple[str, ...]
    log_classes: tuple[str, ...]
    cells_used: int
    report_classes_used: int
    average_basis: str
    report_totals_status: str
    aggregates_excluded: int
    outliers_excluded: int


@dataclass(frozen=True)
class ZoneAllowance:
    zone: str
    value_cad_m3: float
    source_series: str
    effective_date: str
    component: str


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise OpenDataNetValueError(f"missing input file: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _require_columns(
    rows: Sequence[Mapping[str, str]], required: Iterable[str], source: Path
) -> None:
    if not rows:
        raise OpenDataNetValueError(f"empty input table: {source}")
    missing = set(required) - set(rows[0])
    if missing:
        raise OpenDataNetValueError(f"{source} is missing columns: {sorted(missing)}")


def _number(row: Mapping[str, str], column: str, context: str) -> float:
    raw = row.get(column, "")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise OpenDataNetValueError(
            f"non-numeric {column} in {context}: {raw!r}"
        ) from exc
    if not math.isfinite(value):
        raise OpenDataNetValueError(f"non-finite {column} in {context}: {value}")
    return value


def _require_positive(value: float, context: str) -> float:
    if value <= 0.0:
        raise OpenDataNetValueError(f"non-positive value in {context}: {value}")
    return value


def _is_true(raw: str) -> bool:
    return raw.strip().lower() == "true"


def _silviculture_value(row: Mapping[str, str]) -> float:
    """Return 0 for a Coast row that explicitly excludes silviculture."""
    included = row["road_silviculture_included"] == "true"
    raw = row["road_silviculture_allowance_cad_m3"]
    if not included:
        if raw != "":
            raise OpenDataNetValueError(
                f"excluded silviculture allowance is not blank in {row['scenario_id']}"
            )
        return 0.0
    return _number(row, "road_silviculture_allowance_cad_m3", row["scenario_id"])


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _repo_path(path: Path) -> str:
    """Return a repository-relative path when possible, else an absolute path."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def load_cost_estimates(path: Path = COST_ESTIMATES_CSV) -> list[dict[str, str]]:
    """Load and validate the 945-row open cost-model grid."""
    rows = _read_csv(path)
    _require_columns(
        rows,
        (
            "scenario_id",
            "zone",
            "rate_group",
            "appraisal_region",
            "slope_class",
            "slope_pct",
            "system",
            "piece_class",
            "piece_size_m3",
            "volume_class",
            "sale_volume_m3",
            "distance_class",
            "haul_distance_km",
            "logging_low_cad_m3",
            "logging_base_cad_m3",
            "logging_high_cad_m3",
            "haul_low_cad_m3",
            "haul_base_cad_m3",
            "haul_high_cad_m3",
            "total_low_cad_m3",
            "total_base_cad_m3",
            "total_high_cad_m3",
        ),
        path,
    )
    if len(rows) != EXPECTED_COST_ROWS:
        raise OpenDataNetValueError(
            f"expected {EXPECTED_COST_ROWS} cost rows, found {len(rows)}"
        )
    if {row["zone"] for row in rows} != set(EXPECTED_ZONES):
        raise OpenDataNetValueError("cost model zone set does not match the contract")
    if {row["system"] for row in rows} != set(EXPECTED_SYSTEMS):
        raise OpenDataNetValueError("cost model system set does not match the contract")

    seen: set[str] = set()
    for row in rows:
        scenario_id = row["scenario_id"]
        if scenario_id in seen:
            raise OpenDataNetValueError(f"duplicate scenario_id: {scenario_id}")
        seen.add(scenario_id)
        expected_region = ZONE_PRICE_REGION.get(row["zone"])
        if row["appraisal_region"] != expected_region:
            raise OpenDataNetValueError(
                f"appraisal region mismatch in {scenario_id}: "
                f"{row['appraisal_region']!r} vs {expected_region!r}"
            )
        for prefix in ("logging", "haul", "total"):
            low = _number(row, f"{prefix}_low_cad_m3", scenario_id)
            base = _number(row, f"{prefix}_base_cad_m3", scenario_id)
            high = _number(row, f"{prefix}_high_cad_m3", scenario_id)
            if low < 0.0 or base < 0.0 or high < 0.0:
                raise OpenDataNetValueError(
                    f"negative {prefix} cost in {scenario_id}"
                )
            if not low <= base <= high:
                raise OpenDataNetValueError(
                    f"unordered {prefix} cost range in {scenario_id}"
                )
        total_base = sum(
            _number(row, f"{prefix}_base_cad_m3", scenario_id)
            for prefix in ("logging", "haul")
        )
        if abs(total_base - _number(row, "total_base_cad_m3", scenario_id)) > 0.05:
            raise OpenDataNetValueError(
                f"total_base does not equal logging+haul in {scenario_id}"
            )
    return rows


def weighted_quantile(values: Sequence[float], weights: Sequence[float], q: float) -> float:
    """Return a step weighted quantile for 0 < q < 1."""
    if len(values) != len(weights) or not values:
        raise OpenDataNetValueError("weighted_quantile requires equal non-empty inputs")
    if not 0.0 < q < 1.0:
        raise OpenDataNetValueError(f"quantile outside (0, 1): {q}")
    pairs = sorted(zip(values, weights, strict=True))
    if any(weight <= 0.0 or not math.isfinite(weight) for _, weight in pairs):
        raise OpenDataNetValueError("weighted_quantile requires finite positive weights")
    if any(not math.isfinite(value) for value, _ in pairs):
        raise OpenDataNetValueError("weighted_quantile requires finite values")
    total = sum(weight for _, weight in pairs)
    threshold = q * total
    cumulative = 0.0
    for value, weight in pairs:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return pairs[-1][0]


def _select_price_rows(
    rows: Sequence[dict[str, str]], report_basis: str, period_end: str
) -> list[dict[str, str]]:
    selected = [
        row
        for row in rows
        if row["report_basis"] == report_basis and row["period_end"] == period_end
    ]
    if not selected:
        raise OpenDataNetValueError(
            f"no price rows for {report_basis} ending {period_end}"
        )
    if {row["report_basis"] for row in selected} != {report_basis}:
        raise OpenDataNetValueError("price basis pooling detected")
    if {row["period_end"] for row in selected} != {period_end}:
        raise OpenDataNetValueError("price vintage pooling detected")
    return selected


def _coast_price_panel(
    rows: Sequence[dict[str, str]], report_basis: str, period_end: str
) -> PricePanel:
    plausible = [row for row in rows if row["price_plausibility"] == "ok"]
    outliers = len(rows) - len(plausible)
    if not plausible:
        raise OpenDataNetValueError("no plausible Coast price cells")
    if outliers:
        raise OpenDataNetValueError(
            "Coast panel uses PDF report-footer averages and cannot include "
            f"flagged cells: {outliers} outlier(s)"
        )
    by_class: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in plausible:
        if _is_true(row["is_aggregate"]):
            raise OpenDataNetValueError("Coast price row unexpectedly marked aggregate")
        if row["report_totals_status"] != "consistent":
            raise OpenDataNetValueError(
                "Coast report totals are not consistent for "
                f"{row['report_id']}: {row['report_totals_status']!r}"
            )
        by_class[row["log_class"]].append(row)
    if set(by_class) != {"old_growth", "second_growth"}:
        raise OpenDataNetValueError(
            f"unexpected Coast log classes: {sorted(by_class)}"
        )

    class_points: list[tuple[float, float, str, str]] = []
    report_ids: list[str] = []
    for log_class, class_rows in sorted(by_class.items()):
        report_ids_in_class = {row["report_id"] for row in class_rows}
        if len(report_ids_in_class) != 1:
            raise OpenDataNetValueError(
                f"Coast {log_class} has multiple reports in one vintage"
            )
        report_id = next(iter(report_ids_in_class))
        report_ids.append(report_id)
        average = _require_positive(
            _number(class_rows[0], "report_avg_price_cad_m3", report_id),
            f"Coast {log_class} report average",
        )
        volume = _require_positive(
            _number(class_rows[0], "report_total_volume_m3", report_id),
            f"Coast {log_class} report volume",
        )
        class_points.append((average, volume, log_class, report_id))

    total_volume = sum(volume for _, volume, _, _ in class_points)
    base = sum(average * volume for average, volume, _, _ in class_points) / total_volume
    low = min(average for average, _, _, _ in class_points)
    high = max(average for average, _, _, _ in class_points)
    if not low <= base <= high:
        raise OpenDataNetValueError("Coast price panel is unordered")
    return PricePanel(
        region="Coast",
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        method="published_report_average_volume_weighted_across_coast_log_classes",
        report_basis=report_basis,
        period_end=period_end,
        report_ids=tuple(sorted(report_ids)),
        log_classes=tuple(sorted(by_class)),
        cells_used=len(plausible),
        report_classes_used=len(by_class),
        average_basis="pdf_report_footer_average_and_volume",
        report_totals_status="consistent",
        aggregates_excluded=0,
        outliers_excluded=outliers,
    )


def _interior_price_panel(
    rows: Sequence[dict[str, str]], report_basis: str, period_end: str
) -> PricePanel:
    plausible = [row for row in rows if row["price_plausibility"] == "ok"]
    outliers = len(rows) - len(plausible)
    aggregates = [row for row in plausible if _is_true(row["is_aggregate"])]
    products = [row for row in plausible if not _is_true(row["is_aggregate"])]
    if not products:
        raise OpenDataNetValueError("no plausible Interior product price cells")

    report_ids = {row["report_id"] for row in products}
    if len(report_ids) != 1:
        raise OpenDataNetValueError("Interior has multiple reports in one vintage")
    report_id = next(iter(report_ids))
    prices: list[float] = []
    volumes: list[float] = []
    for row in products:
        context = f"Interior {row['species']} {row['end_use']}"
        prices.append(_require_positive(_number(row, "price_cad_m3", context), context))
        volumes.append(_require_positive(_number(row, "volume_m3", context), context))

    total_volume = sum(volumes)
    base = sum(price * volume for price, volume in zip(prices, volumes, strict=True)) / (
        total_volume
    )
    low = weighted_quantile(prices, volumes, 0.10)
    high = weighted_quantile(prices, volumes, 0.90)
    published_average = _number(
        products[0], "report_avg_price_cad_m3", f"Interior {report_id}"
    )
    if abs(published_average - base) > 0.10:
        raise OpenDataNetValueError(
            "Interior product-cell weighted mean does not reconcile to the "
            f"published average: {base:.4f} vs {published_average:.4f}"
        )
    if not low <= base <= high:
        raise OpenDataNetValueError("Interior price panel is unordered")
    return PricePanel(
        region="Interior",
        low_cad_m3=low,
        base_cad_m3=base,
        high_cad_m3=high,
        method="volume_weighted_product_cell_mean_and_p10_p90_excluding_aggregates",
        report_basis=report_basis,
        period_end=period_end,
        report_ids=(report_id,),
        log_classes=("not_reported",),
        cells_used=len(products),
        report_classes_used=1,
        average_basis="product_cell_volume_weighted_mean",
        report_totals_status="not_applicable",
        aggregates_excluded=len(aggregates),
        outliers_excluded=outliers,
    )


def load_price_panels(
    path: Path = LOG_MARKET_PRICES_CSV,
    *,
    report_basis: str = PRIMARY_PRICE_BASIS,
    period_end: str = PRIMARY_PRICE_PERIOD_END,
) -> dict[str, PricePanel]:
    """Build one Coast and one Interior panel from a single price vintage."""
    rows = _read_csv(path)
    _require_columns(
        rows,
        (
            "report_id",
            "region",
            "report_basis",
            "period_end",
            "log_class",
            "price_cad_m3",
            "price_basis",
            "observation_type",
            "is_aggregate",
            "price_status",
            "price_plausibility",
            "report_total_volume_m3",
            "report_avg_price_cad_m3",
            "report_totals_status",
        ),
        path,
    )
    selected = _select_price_rows(rows, report_basis, period_end)
    for row in selected:
        if row["price_basis"] != "mill_delivered":
            raise OpenDataNetValueError(
                f"unexpected price basis for {row['report_id']}: {row['price_basis']!r}"
            )
        if row["price_status"] != "reported":
            raise OpenDataNetValueError(
                f"unexpected price status for {row['report_id']}: {row['price_status']!r}"
            )
    coast = _coast_price_panel(
        [row for row in selected if row["region"] == "coast"],
        report_basis,
        period_end,
    )
    interior = _interior_price_panel(
        [row for row in selected if row["region"] == "interior"],
        report_basis,
        period_end,
    )
    if coast.report_basis != report_basis or interior.report_basis != report_basis:
        raise OpenDataNetValueError("price panel basis mismatch")
    if coast.period_end != period_end or interior.period_end != period_end:
        raise OpenDataNetValueError("price panel vintage mismatch")
    return {"Coast": coast, "Interior": interior}


def load_silviculture_allowances(
    path: Path = APPRAISAL_CSV,
) -> dict[str, ZoneAllowance]:
    """Load the published Interior basic silviculture CAD/m3 by forest zone."""
    rows = _read_csv(path)
    _require_columns(
        rows,
        (
            "region",
            "source_series",
            "component_group",
            "component",
            "zone_basis",
            "zone",
            "effective_date",
            "cost_basis",
            "value",
            "observation_type",
        ),
        path,
    )
    selected = [
        row
        for row in rows
        if row.get("loaded_into_model", "true").strip().lower() != "false"
        and row["source_series"] == SILVICULTURE_SERIES
        and row["component_group"] == "silviculture"
        and row["component"] == SILVICULTURE_COMPONENT
        and row["cost_basis"] == "CAD/m3"
        and row["zone_basis"] == "forest_zone"
    ]
    if len(selected) != len(EXPECTED_ZONES):
        raise OpenDataNetValueError(
            f"expected {len(EXPECTED_ZONES)} silviculture zone rows, "
            f"found {len(selected)}"
        )
    allowances: dict[str, ZoneAllowance] = {}
    for row in selected:
        zone = row["zone"]
        if zone in allowances:
            raise OpenDataNetValueError(f"duplicate silviculture zone: {zone}")
        if row["region"] != SILVICULTURE_PRICE_REGION:
            raise OpenDataNetValueError(
                f"silviculture row for {zone} is not Interior: {row['region']!r}"
            )
        if row["effective_date"] != SILVICULTURE_EFFECTIVE_DATE:
            raise OpenDataNetValueError(
                f"unexpected silviculture effective date for {zone}: "
                f"{row['effective_date']}"
            )
        value = _require_positive(
            _number(row, "value", f"silviculture {zone}"), f"silviculture {zone}"
        )
        allowances[zone] = ZoneAllowance(
            zone=zone,
            value_cad_m3=value,
            source_series=row["source_series"],
            effective_date=row["effective_date"],
            component=row["component"],
        )
    if set(allowances) != set(EXPECTED_ZONES):
        raise OpenDataNetValueError("silviculture zone set does not match the cost grid")
    return allowances


def price_region_for_zone(zone: str) -> str:
    if zone not in ZONE_PRICE_REGION:
        raise OpenDataNetValueError(f"unmapped zone: {zone}")
    return ZONE_PRICE_REGION[zone]


def build_net_value_rows(
    cost_rows: Sequence[dict[str, str]],
    price_panels: Mapping[str, PricePanel],
    allowances: Mapping[str, ZoneAllowance],
) -> list[dict[str, str]]:
    """Combine one price panel, one cost row and one allowance into a row.

    The published Interior basic-silviculture CAD/m3 allowance is applied only
    to Interior appraisal rows.  Coast net_model equals gross margin: the CAM
    publishes Coast basic silviculture in CAD/ha only, so no CAD/m3 value is
    invented and the allowance cell stays blank.
    """
    rows: list[dict[str, str]] = []
    for cost in cost_rows:
        scenario_id = cost["scenario_id"]
        zone = cost["zone"]
        price_region = price_region_for_zone(zone)
        if cost["appraisal_region"] != price_region:
            raise OpenDataNetValueError(
                f"appraisal-region mismatch for {scenario_id}: "
                f"{cost['appraisal_region']} vs {price_region}"
            )
        panel = price_panels[price_region]

        logging_low = _number(cost, "logging_low_cad_m3", scenario_id)
        logging_base = _number(cost, "logging_base_cad_m3", scenario_id)
        logging_high = _number(cost, "logging_high_cad_m3", scenario_id)
        haul_low = _number(cost, "haul_low_cad_m3", scenario_id)
        haul_base = _number(cost, "haul_base_cad_m3", scenario_id)
        haul_high = _number(cost, "haul_high_cad_m3", scenario_id)

        cost_low = logging_low + haul_low
        cost_base = logging_base + haul_base
        cost_high = logging_high + haul_high

        gross_low = panel.low_cad_m3 - cost_high
        gross_base = panel.base_cad_m3 - cost_base
        gross_high = panel.high_cad_m3 - cost_low
        includes_silviculture = price_region == SILVICULTURE_PRICE_REGION
        if includes_silviculture:
            allowance = allowances[zone]
            allowance_value = allowance.value_cad_m3
            allowance_cell = _fmt(allowance_value)
            component = "interior_basic_silviculture"
            source_series = allowance.source_series
            effective_date = allowance.effective_date
            basis = "published_CAD_m3_all_species_average_by_forest_zone"
        else:
            allowance_value = 0.0
            allowance_cell = ""
            component = COAST_SILVICULTURE_COMPONENT
            source_series = ""
            effective_date = ""
            basis = COAST_SILVICULTURE_BASIS
        net_low = gross_low - allowance_value
        net_base = gross_base - allowance_value
        net_high = gross_high - allowance_value

        rows.append(
            {
                "scenario_id": scenario_id,
                "zone": zone,
                "system": cost["system"],
                "slope_class": cost["slope_class"],
                "slope_pct": cost["slope_pct"],
                "piece_class": cost["piece_class"],
                "piece_size_m3": cost["piece_size_m3"],
                "volume_class": cost["volume_class"],
                "sale_volume_m3": cost["sale_volume_m3"],
                "distance_class": cost["distance_class"],
                "haul_distance_km": cost["haul_distance_km"],
                "cost_appraisal_region": cost["appraisal_region"],
                "price_region": price_region,
                "price_low_cad_m3": _fmt(panel.low_cad_m3),
                "price_base_cad_m3": _fmt(panel.base_cad_m3),
                "price_high_cad_m3": _fmt(panel.high_cad_m3),
                "logging_low_cad_m3": _fmt(logging_low),
                "logging_base_cad_m3": _fmt(logging_base),
                "logging_high_cad_m3": _fmt(logging_high),
                "haul_low_cad_m3": _fmt(haul_low),
                "haul_base_cad_m3": _fmt(haul_base),
                "haul_high_cad_m3": _fmt(haul_high),
                "road_silviculture_allowance_cad_m3": allowance_cell,
                "gross_margin_low_cad_m3": _fmt(gross_low),
                "gross_margin_base_cad_m3": _fmt(gross_base),
                "gross_margin_high_cad_m3": _fmt(gross_high),
                "net_model_low_cad_m3": _fmt(net_low),
                "net_model_base_cad_m3": _fmt(net_base),
                "net_model_high_cad_m3": _fmt(net_high),
                "margin_range_basis": (
                    "low=price_low-cost_high; base=price_base-cost_base; "
                    "high=price_high-cost_low"
                ),
                "road_silviculture_included": "true" if includes_silviculture else "false",
                "road_silviculture_component": component,
                "road_silviculture_source_series": source_series,
                "road_silviculture_effective_date": effective_date,
                "road_silviculture_basis": basis,
                "price_basis": PRICE_BASIS,
                "price_report_basis": panel.report_basis,
                "price_period_end": panel.period_end,
                "price_method": panel.method,
                "price_report_ids": "|".join(panel.report_ids),
                "cost_basis": COST_BASIS,
                "evidence": EVIDENCE_MODEL,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "notes": (
                    "Modeled margin, not observed profit. Published price average "
                    "minus proxy logging/haul cost"
                    + (
                        " minus a published Interior silviculture allowance."
                        if includes_silviculture
                        else "; Coast silviculture is not deducted because CAM "
                        "publishes CAD/ha only."
                    )
                    + " No legal, AAC, tenure or policy claim."
                ),
            }
        )
    return rows


def summarize_by_zone_system(
    rows: Sequence[Mapping[str, str]],
) -> list[dict[str, str]]:
    groups: dict[tuple[str, str], list[Mapping[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["zone"], row["system"])].append(row)
    if len(groups) != EXPECTED_ZONE_SYSTEM_GROUPS:
        raise OpenDataNetValueError(
            f"expected {EXPECTED_ZONE_SYSTEM_GROUPS} zone-system groups, "
            f"found {len(groups)}"
        )

    summaries: list[dict[str, str]] = []
    for (zone, system), group in sorted(groups.items()):
        net_base = [float(row["net_model_base_cad_m3"]) for row in group]
        gross_base = [float(row["gross_margin_base_cad_m3"]) for row in group]
        net_low = [float(row["net_model_low_cad_m3"]) for row in group]
        net_high = [float(row["net_model_high_cad_m3"]) for row in group]
        negative = sum(1 for value in net_base if value < 0.0)
        summaries.append(
            {
                "zone": zone,
                "system": system,
                "price_region": group[0]["price_region"],
                "n_rows": str(len(group)),
                "price_low_cad_m3": group[0]["price_low_cad_m3"],
                "price_base_cad_m3": group[0]["price_base_cad_m3"],
                "price_high_cad_m3": group[0]["price_high_cad_m3"],
                "logging_base_median_cad_m3": _fmt(
                    statistics.median(float(row["logging_base_cad_m3"]) for row in group)
                ),
                "haul_base_median_cad_m3": _fmt(
                    statistics.median(float(row["haul_base_cad_m3"]) for row in group)
                ),
                "logging_plus_haul_base_median_cad_m3": _fmt(
                    statistics.median(
                        float(row["logging_base_cad_m3"])
                        + float(row["haul_base_cad_m3"])
                        for row in group
                    )
                ),
                "road_silviculture_allowance_cad_m3": group[0][
                    "road_silviculture_allowance_cad_m3"
                ],
                "gross_margin_base_median_cad_m3": _fmt(statistics.median(gross_base)),
                "gross_margin_base_min_cad_m3": _fmt(min(gross_base)),
                "gross_margin_base_max_cad_m3": _fmt(max(gross_base)),
                "net_model_low_median_cad_m3": _fmt(statistics.median(net_low)),
                "net_model_base_median_cad_m3": _fmt(statistics.median(net_base)),
                "net_model_high_median_cad_m3": _fmt(statistics.median(net_high)),
                "net_model_base_min_cad_m3": _fmt(min(net_base)),
                "net_model_base_max_cad_m3": _fmt(max(net_base)),
                "net_model_envelope_min_cad_m3": _fmt(min(net_low)),
                "net_model_envelope_max_cad_m3": _fmt(max(net_high)),
                "negative_net_base_count": str(negative),
                "negative_net_base_share": _fmt(negative / len(group)),
                "road_silviculture_included": group[0]["road_silviculture_included"],
                "price_basis": group[0]["price_basis"],
                "cost_basis": group[0]["cost_basis"],
                "evidence": group[0]["evidence"],
                "observed": group[0]["observed"],
                "policy_grade": group[0]["policy_grade"],
                "net_value_status": group[0]["net_value_status"],
                "interpretation": "model_estimate_not_observed_profit",
            }
        )
    return summaries


def _case_row(
    case_id: str,
    case_group: str,
    description: str,
    values: Sequence[float],
    notes: str,
) -> dict[str, str]:
    if not values:
        raise OpenDataNetValueError(f"sensitivity case {case_id} has no values")
    return {
        "case_id": case_id,
        "case_group": case_group,
        "description": description,
        "n_rows": str(len(values)),
        "median_cad_m3": _fmt(statistics.median(values)),
        "min_cad_m3": _fmt(min(values)),
        "max_cad_m3": _fmt(max(values)),
        "notes": notes,
    }


def build_sensitivity(
    rows: Sequence[Mapping[str, str]],
    price_panels: Mapping[str, PricePanel],
    sensitivity_panels: Mapping[str, PricePanel],
) -> list[dict[str, str]]:
    """One-at-a-time price, cost, allowance and vintage sensitivities."""
    base: list[float] = []
    price_low: list[float] = []
    price_high: list[float] = []
    cost_low: list[float] = []
    cost_high: list[float] = []
    gross_base: list[float] = []
    net_low: list[float] = []
    net_high: list[float] = []
    one_month_base: list[float] = []
    one_month_low: list[float] = []
    one_month_high: list[float] = []

    for row in rows:
        allowance = _silviculture_value(row)
        logging_low = float(row["logging_low_cad_m3"])
        logging_base = float(row["logging_base_cad_m3"])
        logging_high = float(row["logging_high_cad_m3"])
        haul_low = float(row["haul_low_cad_m3"])
        haul_base = float(row["haul_base_cad_m3"])
        haul_high = float(row["haul_high_cad_m3"])
        price = price_panels[row["price_region"]]
        alt = sensitivity_panels[row["price_region"]]

        base_net = price.base_cad_m3 - logging_base - haul_base - allowance
        base.append(base_net)
        price_low.append(price.low_cad_m3 - logging_base - haul_base - allowance)
        price_high.append(price.high_cad_m3 - logging_base - haul_base - allowance)
        cost_low.append(price.base_cad_m3 - logging_low - haul_low - allowance)
        cost_high.append(price.base_cad_m3 - logging_high - haul_high - allowance)
        gross_base.append(price.base_cad_m3 - logging_base - haul_base)
        net_low.append(float(row["net_model_low_cad_m3"]))
        net_high.append(float(row["net_model_high_cad_m3"]))
        one_month_base.append(
            alt.base_cad_m3 - logging_base - haul_base - allowance
        )
        one_month_low.append(alt.low_cad_m3 - logging_base - haul_base - allowance)
        one_month_high.append(alt.high_cad_m3 - logging_base - haul_base - allowance)

    return [
        _case_row(
            "base",
            "base",
            "3-month 2025-12 price, base logging and haul, published Interior silviculture where applicable",
            base,
            "Primary model case; Coast rows exclude silviculture because CAM publishes CAD/ha only.",
        ),
        _case_row(
            "price_low",
            "price",
            "primary price low with base logging and haul",
            price_low,
            "Coast low is the lower published log-class average; Interior low is "
            "the volume-weighted P10 product price.",
        ),
        _case_row(
            "price_high",
            "price",
            "primary price high with base logging and haul",
            price_high,
            "Coast high is the higher published log-class average; Interior high "
            "is the volume-weighted P90 product price.",
        ),
        _case_row(
            "cost_low",
            "cost",
            "base price with low logging and low haul",
            cost_low,
            "Low end of the published-cost proxy range.",
        ),
        _case_row(
            "cost_high",
            "cost",
            "base price with high logging and high haul",
            cost_high,
            "High end of the published-cost proxy range.",
        ),
        _case_row(
            "gross_margin_road_silviculture_excluded",
            "road_silviculture",
            "base price minus base logging and haul, no silviculture allowance",
            gross_base,
            "Gross modeled margin only.",
        ),
        _case_row(
            "net_model_road_silviculture_included",
            "road_silviculture",
            "base case with published Interior basic silviculture allowance where applicable",
            base,
            "Same arithmetic as the primary base case; shown separately for the "
            "included/excluded comparison.",
        ),
        _case_row(
            "envelope_low",
            "combined",
            "low price minus high logging and high haul minus allowance",
            net_low,
            "Conservative end of the primary modeled range.",
        ),
        _case_row(
            "envelope_high",
            "combined",
            "high price minus low logging and low haul minus allowance",
            net_high,
            "Optimistic end of the primary modeled range.",
        ),
        _case_row(
            "price_1month_base",
            "price_vintage",
            "1-month 2025-12 price with base logging and haul",
            one_month_base,
            "Separate vintage; not pooled with the 3-month primary panel.",
        ),
        _case_row(
            "price_1month_low",
            "price_vintage",
            "1-month 2025-12 low price with base logging and haul",
            one_month_low,
            "Separate vintage; not pooled with the 3-month primary panel.",
        ),
        _case_row(
            "price_1month_high",
            "price_vintage",
            "1-month 2025-12 high price with base logging and haul",
            one_month_high,
            "Separate vintage; not pooled with the 3-month primary panel.",
        ),
    ]


def load_qc_reference(path: Path = PROCUREMENT_CSV) -> dict[str, str]:
    """Load the QC purchased-wood channel reference, kept separate from BC."""
    rows = _read_csv(path)
    _require_columns(
        rows,
        (
            "benchmark_id",
            "geography",
            "species_group",
            "product_or_grade",
            "purchase_cost_cad_m3",
            "bgad_procurement_before_transfers_cad_m3",
            "transfers_total_cad_m3",
            "bgad_procurement_after_transfers_cad_m3",
            "cost_spread_before_transfers_cad_m3",
            "cost_spread_after_transfers_cad_m3",
            "spread_status",
            "price_year",
            "currency_basis",
            "evidence",
            "interpretation",
        ),
        path,
    )
    matches = [row for row in rows if row["benchmark_id"] == "qc_2022_sepm_total"]
    if len(matches) != 1:
        raise OpenDataNetValueError(
            f"expected one qc_2022_sepm_total row, found {len(matches)}"
        )
    source = matches[0]
    expected = {
        "purchase_cost_cad_m3": 107.22,
        "bgad_procurement_before_transfers_cad_m3": 81.40,
        "transfers_total_cad_m3": 25.01,
        "bgad_procurement_after_transfers_cad_m3": 106.41,
        "cost_spread_before_transfers_cad_m3": 25.82,
        "cost_spread_after_transfers_cad_m3": 0.81,
    }
    for column, value in expected.items():
        observed = _number(source, column, f"QC {column}")
        if abs(observed - value) > 0.01:
            raise OpenDataNetValueError(
                f"QC reference changed: {column} expected {value}, found {observed}"
            )
    if source["spread_status"] != "computed":
        raise OpenDataNetValueError("QC SEPM spread is not computed")
    if source["evidence"] != QC_EVIDENCE:
        raise OpenDataNetValueError(
            f"QC evidence changed: expected {QC_EVIDENCE!r}, found {source['evidence']!r}"
        )
    return {
        "reference_id": "qc_2022_sepm_purchased_wood_channel",
        "geography": "QC",
        "species_group": source["species_group"],
        "product_or_grade": source["product_or_grade"],
        "price_year": source["price_year"],
        "currency_basis": source["currency_basis"],
        "purchase_cost_cad_m3": source["purchase_cost_cad_m3"],
        "bgad_before_transfers_cad_m3": source[
            "bgad_procurement_before_transfers_cad_m3"
        ],
        "transfers_total_cad_m3": source["transfers_total_cad_m3"],
        "bgad_after_transfers_cad_m3": source[
            "bgad_procurement_after_transfers_cad_m3"
        ],
        "cost_spread_before_transfers_cad_m3": source[
            "cost_spread_before_transfers_cad_m3"
        ],
        "cost_spread_after_transfers_cad_m3": source[
            "cost_spread_after_transfers_cad_m3"
        ],
        "evidence": source["evidence"],
        "observed": "false",
        "policy_grade": "false",
        "net_value_status": QC_NET_VALUE_STATUS,
        "interpretation": "purchased_vs_bgad_procurement_cost_spread",
        "notes": (
            "QC purchased-wood channel cost spread, not forest net value, not a "
            "mill margin, and not mixed with BC results or any national total."
        ),
    }


def build_price_outlier_rows(path: Path = LOG_MARKET_PRICES_CSV) -> list[dict[str, str]]:
    """Return a separate register of flagged price cells; none are model inputs."""
    rows = _read_csv(path)
    outliers: list[dict[str, str]] = []
    for row in rows:
        if row["price_plausibility"] == "ok":
            continue
        outliers.append(
            {
                "report_id": row["report_id"],
                "region": row["region"],
                "report_basis": row["report_basis"],
                "period_end": row["period_end"],
                "log_class": row["log_class"],
                "species": row["species"],
                "grade": row["grade"],
                "end_use": row["end_use"],
                "price_cad_m3": row["price_cad_m3"],
                "price_plausibility": row["price_plausibility"],
                "is_aggregate": row["is_aggregate"],
                "used_in_model": "false",
                "notes": "Flagged price cell retained for audit; excluded from model panels.",
            }
        )
    return outliers


def _resolve_input_tables(inputs_dir: Path | None) -> dict[str, Path]:
    """Resolve the four model tables from the corpus or a published bundle."""
    if inputs_dir is None:
        return {
            "cost": COST_ESTIMATES_CSV,
            "prices": LOG_MARKET_PRICES_CSV,
            "appraisal": APPRAISAL_CSV,
            "procurement": PROCUREMENT_CSV,
        }
    if not inputs_dir.is_dir():
        raise OpenDataNetValueError(f"input bundle is not a directory: {inputs_dir}")
    resolved = {
        "cost": inputs_dir / BUNDLE_COST_INPUTS_CSV,
        "prices": inputs_dir / BUNDLE_PRICE_PANEL_CSV,
        "appraisal": inputs_dir / BUNDLE_SILVICULTURE_CSV,
        "procurement": inputs_dir / BUNDLE_QC_REFERENCE_CSV,
    }
    for role, path in resolved.items():
        if not path.is_file():
            raise OpenDataNetValueError(f"missing bundle {role} table: {path}")
    return resolved


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return "\n".join(lines)


def render_report(
    rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, str]],
    sensitivity: Sequence[Mapping[str, str]],
    price_panels: Mapping[str, PricePanel],
    allowances: Mapping[str, ZoneAllowance],
    qc: Mapping[str, str],
    validation: Mapping[str, Any],
) -> str:
    net_base = [float(row["net_model_base_cad_m3"]) for row in rows]
    negative = sum(1 for value in net_base if value < 0.0)
    best = sorted(
        summary_rows,
        key=lambda row: float(row["net_model_base_median_cad_m3"]),
        reverse=True,
    )[:5]
    worst = sorted(
        summary_rows, key=lambda row: float(row["net_model_base_median_cad_m3"])
    )[:5]

    price_table = _md_table(
        ("Region", "Low", "Base", "High", "Method", "Vintage"),
        [
            (
                region,
                _fmt(panel.low_cad_m3),
                _fmt(panel.base_cad_m3),
                _fmt(panel.high_cad_m3),
                panel.method,
                f"{panel.report_basis} to {panel.period_end}",
            )
            for region, panel in sorted(price_panels.items())
        ],
    )
    allowance_table = _md_table(
        ("Forest zone", "CAD/m3", "Applied in model", "Source"),
        [
            (
                zone,
                _fmt(allowance.value_cad_m3),
                (
                    "Interior-priced rows"
                    if any(
                        row["zone"] == zone
                        and row["price_region"] == SILVICULTURE_PRICE_REGION
                        for row in rows
                    )
                    else "not applied (Coast-priced; CAM publishes CAD/ha only)"
                ),
                allowance.source_series,
            )
            for zone, allowance in sorted(allowances.items())
        ],
    )
    summary_table = _md_table(
        (
            "Zone",
            "System",
            "Price region",
            "Price base",
            "Logging+haul base (median)",
            "Silviculture",
            "Net base median",
            "Net base range",
            "Negative rows",
        ),
        [
            (
                row["zone"],
                row["system"],
                row["price_region"],
                row["price_base_cad_m3"],
                row["logging_plus_haul_base_median_cad_m3"],
                row["road_silviculture_allowance_cad_m3"] or "excluded",
                row["net_model_base_median_cad_m3"],
                f"{row['net_model_base_min_cad_m3']} to {row['net_model_base_max_cad_m3']}",
                f"{row['negative_net_base_count']}/{row['n_rows']}",
            )
            for row in summary_rows
        ],
    )
    sensitivity_table = _md_table(
        ("Case", "Group", "Median", "Min", "Max"),
        [
            (
                row["case_id"],
                row["case_group"],
                row["median_cad_m3"],
                row["min_cad_m3"],
                row["max_cad_m3"],
            )
            for row in sensitivity
        ],
    )
    best_table = _md_table(
        ("Zone", "System", "Net base median"),
        [
            (row["zone"], row["system"], row["net_model_base_median_cad_m3"])
            for row in best
        ],
    )
    worst_table = _md_table(
        ("Zone", "System", "Net base median"),
        [
            (row["zone"], row["system"], row["net_model_base_median_cad_m3"])
            for row in worst
        ],
    )

    return f"""# Open-data BC modeled margin

**Status:** `model_estimate` only. This is a **modeled margin**, not observed
profit, not a mill settlement, not an official net value, and not a legal, AAC,
tenure, or policy result. Generated from public data only.

## Headline

- Rows: **{len(rows)}** (7 forest zones x 5 slope-system scenarios [2 ground, 2 cable, 1 heli] x 3 piece sizes x 3 sale volumes x 3 haul distances).
- Zone-system groups: **{len(summary_rows)}**.
- Median base `net_model`: **{_fmt(statistics.median(net_base))} CAD/m3**.
- Base `net_model` range: **{_fmt(min(net_base))} to {_fmt(max(net_base))} CAD/m3**.
- Rows with a negative base `net_model`: **{negative} / {len(rows)}**. Negative values are reported modeled losses, not errors or NaN.
- `road_silviculture_included=true` only on Interior-priced rows using the published Interior basic silviculture CAD/m3 allowance. Coast rows are `false` because CAM publishes Coast basic silviculture in CAD/ha only.
- No BC+QC combined national total exists in this artifact.

## Price/cost mapping

The primary price panel uses **one vintage only**: `3_month` reports ending
**{PRIMARY_PRICE_PERIOD_END}**. The `1_month` vintage is kept separate as a
sensitivity and is never pooled with the primary panel.

{price_table}

Coast base is the volume-weighted average of the published old-growth and
second-growth report averages. Coast low/high are those two published class
averages. The two report classes come from the PDF report footers; `cells_used`
counts the 79 plausible underlying cells used for the outlier audit, not 79
independent panel observations. Interior base is the volume-weighted mean of
plausible product cells; low/high are the volume-weighted P10/P90 product
prices. Interior `is_aggregate=true` weighted-average rows are excluded from all
product sums. Flagged price cells are not used and are written separately to
`bc_open_price_outliers.csv`.

The cost layer is `open_proxy_model`: published 2026 BC machine rates divided by
public case-study productivity for logging, plus a published-appraisal/engineering
truck-cycle haul model. It is not an observed contractor cost. Haul and ground
extraction follow the cost model's `appraisal_region`: North West Zone is
appraisal-Interior and uses the IAM-derived Interior haul method and a grapple
skidder, even though its hourly rates come from the coastal/northwest rate group.

{allowance_table}

Road construction, bridges, and culverts are **not** included: the public
allowances are CAD/km or CAD/each, and converting them to CAD/m3 without site
quantities would invent data. Road maintenance is also excluded because no
zone-to-road-group mapping exists. The included allowance is the published
Interior basic silviculture all-species average, CAD/m3, by forest zone,
effective {SILVICULTURE_EFFECTIVE_DATE}. It is applied **only** to
Interior-priced rows. The Interior table's `South West Zone` value is therefore
not applied to the Coast-priced South West Zone: CAM Table 5-6 publishes Coast
basic silviculture only in CAD/ha, and that value is not converted here.

## BC margin results by zone and system

Base `net_model` = price base - logging base - haul base - silviculture
allowance where an Interior CAD/m3 allowance exists; on Coast rows net_model
equals gross margin. The range columns are the min/max of the scenario rows in
each zone-system group (27 helicopter rows, 54 ground or cable rows). The
low/high envelope is conservative/optimistic:
low uses price low and cost high; high uses price high and cost low.

{summary_table}

**Highest median base net_model (model estimates):**

{best_table}

**Lowest median base net_model (model estimates):**

{worst_table}

## QC reference

The QC purchased-wood channel reference is **separate** and is not a net value,
not a mill margin, and not combined with BC.

| Item | Value |
|---|---:|
| Geography | {qc['geography']} |
| Year | {qc['price_year']} |
| Purchase cost (CAD/m3) | {qc['purchase_cost_cad_m3']} |
| BGAD before transfers (CAD/m3) | {qc['bgad_before_transfers_cad_m3']} |
| Transfers (CAD/m3) | {qc['transfers_total_cad_m3']} |
| BGAD after transfers (CAD/m3) | {qc['bgad_after_transfers_cad_m3']} |
| Cost spread before transfers (CAD/m3) | {qc['cost_spread_before_transfers_cad_m3']} |
| Cost spread after transfers (CAD/m3) | **{qc['cost_spread_after_transfers_cad_m3']}** |

The 0.81 CAD/m3 figure is the published SEPM purchased-versus-BGAD procurement
cost spread after transfers. It is a channel cost spread, not forest net value.
The upstream `survey_procurement_cost` evidence label is preserved.

## Sensitivity

{sensitivity_table}

Price low/high is the published price distribution within the single primary
vintage. Cost low/high is the open cost model's published-rate/productivity
range. The combined envelope combines the conservative price and cost ends
(low price minus high cost, high price minus low cost); it does not multiply
them. The 1-month price cases are a separate vintage and are not pooled with the
3-month primary panel. Road/silviculture excluded versus included is the
gross-margin versus net-model comparison; Coast rows have no CAD/m3
silviculture deduction in either case.

## Licensing

All inputs are **public-reference copyrighted sources**, not strict
OGL/CC-BY/Statistics Canada open-licence data. The BC appraisal manuals,
silviculture table, log-market reports, and CVSE material are BC Crown
copyright; the rate guide is TLA/ILA/NWLA/MNP copyright; FPInnovations/FERIC
case studies are publisher copyright; the QC survey is a Government of Québec
public-reference report. Source PDFs are not redistributed in this repository.
Commercial reuse or redistribution requires permission from the rights holder.

## Caveats and non-claims

- Published averages, not observed transactions. The price is a reported
  mill-delivered market average, not a transaction record or a mill settlement.
- Proxy productivity and proxy system assignment. The public cost model uses
  case-study productivity and maps seven forest zones to two rate groups, with
  an explicit appraisal region that selects haul and ground-extraction method.
- North West Zone is appraisal-Interior: it is priced Interior and uses the
  Interior haul method and a grapple skidder, while its hourly machine rates
  come from the coastal/northwest rate group. South West Zone is the only
  Coast-priced zone.
- Coast basic silviculture is not deducted: CAM Table 5-6 publishes CAD/ha by
  BEC unit, not a CAD/m3 allowance, and that value is not converted.
- Slope, piece-size and sale-volume adjustments are published IAM estimated-
  winning-bid coefficients used as **cost proxies**, not measured cost elasticities.
- Helicopter logging uses a published IAM specified-operation allowance as a
  total cost proxy.
- Logging and haul are model estimates, not observed contractor costs.
- The silviculture allowance is an appraisal/policy allowance, not an observed
  reforestation invoice. It is applied only to Interior-priced rows.
- Public-reference copyrighted sources, not strict open-licence data; source
  PDFs are not redistributed and commercial reuse needs rights-holder permission.
- Nominal CAD. Prices are nominal dollars of their report period; no deflator is applied.
- No grade, species, end-use, or mill-specific matching is possible. Prices are
  regional averages applied uniformly within a price region.
- No legal, AAC, tenure, stumpage, policy, or investment claim is made.
- No restricted ILCR/CCTMCS, FPInterface, mill-settlement, bidder-level, or
  confidential input was used.
- No combined BC+QC national total is produced.

## Validation

- Cost rows: {validation['cost_rows']} / {EXPECTED_COST_ROWS}.
- Net-value rows: {validation['net_value_rows']}.
- Ordered low <= base <= high on all cost, gross-margin, and net-model rows: {validation['ranges_ordered']}.
- No NaN or infinite values in numeric outputs: {validation['finite_outputs']}.
- Primary price panels use one report basis and one period: {validation['single_price_vintage']}.
- Coast report totals are consistent and price basis/status are mill_delivered/reported: {validation['coast_price_contract']}.
- Interior aggregate rows excluded from product sums: {validation['interior_aggregates_excluded']}.
- `road_silviculture_included=true` on Interior rows only, `false` and blank allowance on Coast rows: {validation['silviculture_interior_only']}.
- All rows carry `evidence=model_estimate`, `observed=false`, `policy_grade=false`, `net_value_status=model_estimate`: {validation['labels_complete']}.
- Unexpected `D` (deleted paths) check is performed before commit; see the task report.
"""


def build(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    inputs_dir: Path | None = None,
) -> dict[str, Any]:
    """Build every artifact and return the manifest payload.

    ``inputs_dir`` points at the self-contained published model-input bundle.
    When omitted, the full parent corpus tables are used.
    """
    if out_dir.exists() and not out_dir.is_dir():
        raise OpenDataNetValueError(f"output path is not a directory: {out_dir}")

    input_tables = _resolve_input_tables(inputs_dir)
    cost_rows = load_cost_estimates(input_tables["cost"])
    price_panels = load_price_panels(input_tables["prices"])
    sensitivity_panels = load_price_panels(
        input_tables["prices"],
        report_basis=SENSITIVITY_PRICE_BASIS,
        period_end=SENSITIVITY_PRICE_PERIOD_END,
    )
    allowances = load_silviculture_allowances(input_tables["appraisal"])
    rows = build_net_value_rows(cost_rows, price_panels, allowances)
    summary_rows = summarize_by_zone_system(rows)
    sensitivity = build_sensitivity(rows, price_panels, sensitivity_panels)
    qc = load_qc_reference(input_tables["procurement"])
    outliers = build_price_outlier_rows(input_tables["prices"])

    if len(rows) != EXPECTED_COST_ROWS:
        raise OpenDataNetValueError(
            f"expected {EXPECTED_COST_ROWS} net-value rows, found {len(rows)}"
        )

    numeric_columns = (
        "price_low_cad_m3",
        "price_base_cad_m3",
        "price_high_cad_m3",
        "logging_low_cad_m3",
        "logging_base_cad_m3",
        "logging_high_cad_m3",
        "haul_low_cad_m3",
        "haul_base_cad_m3",
        "haul_high_cad_m3",
        "road_silviculture_allowance_cad_m3",
        "gross_margin_low_cad_m3",
        "gross_margin_base_cad_m3",
        "gross_margin_high_cad_m3",
        "net_model_low_cad_m3",
        "net_model_base_cad_m3",
        "net_model_high_cad_m3",
    )
    for row in rows:
        for column in numeric_columns:
            if column == "road_silviculture_allowance_cad_m3" and (
                row["road_silviculture_included"] == "false"
            ):
                if row[column] != "":
                    raise OpenDataNetValueError(
                        f"excluded silviculture allowance is not blank in "
                        f"{row['scenario_id']}"
                    )
                continue
            if not math.isfinite(float(row[column])):
                raise OpenDataNetValueError(
                    f"non-finite {column} in {row['scenario_id']}"
                )
        for prefix in ("price", "logging", "haul", "gross_margin", "net_model"):
            low = float(row[f"{prefix}_low_cad_m3"])
            base = float(row[f"{prefix}_base_cad_m3"])
            high = float(row[f"{prefix}_high_cad_m3"])
            if not low <= base <= high:
                raise OpenDataNetValueError(
                    f"unordered {prefix} range in {row['scenario_id']}"
                )
        allowance = _silviculture_value(row)
        for level in ("low", "base", "high"):
            gross = float(row[f"gross_margin_{level}_cad_m3"])
            net = float(row[f"net_model_{level}_cad_m3"])
            if abs(net - (gross - allowance)) > 0.011:
                raise OpenDataNetValueError(
                    f"net_model {level} does not equal gross minus allowance in "
                    f"{row['scenario_id']}"
                )

    negative_net_base = sum(
        1 for row in rows if float(row["net_model_base_cad_m3"]) < 0.0
    )
    labels_complete = all(
        row["evidence"] == EVIDENCE_MODEL
        and row["observed"] == OBSERVED
        and row["policy_grade"] == POLICY_GRADE
        and row["price_basis"] == PRICE_BASIS
        and row["cost_basis"] == COST_BASIS
        and row["net_value_status"] == NET_VALUE_STATUS
        for row in rows
    )
    if not labels_complete:
        raise OpenDataNetValueError("a net-value row is missing a required label")

    silviculture_interior_rows = sum(
        1 for row in rows if row["road_silviculture_included"] == "true"
    )
    silviculture_coast_rows = len(rows) - silviculture_interior_rows
    silviculture_interior_only = all(
        (
            row["price_region"] == SILVICULTURE_PRICE_REGION
            and row["road_silviculture_included"] == "true"
            and row["road_silviculture_allowance_cad_m3"] != ""
        )
        or (
            row["price_region"] != SILVICULTURE_PRICE_REGION
            and row["road_silviculture_included"] == "false"
            and row["road_silviculture_allowance_cad_m3"] == ""
        )
        for row in rows
    )
    if not silviculture_interior_only:
        raise OpenDataNetValueError(
            "silviculture allowance must be Interior-only and blank on Coast rows"
        )
    coast_panel = price_panels["Coast"]
    coast_price_contract = (
        coast_panel.report_totals_status == "consistent"
        and coast_panel.report_classes_used == 2
        and coast_panel.average_basis == "pdf_report_footer_average_and_volume"
        and coast_panel.outliers_excluded == 0
    )
    if not coast_price_contract:
        raise OpenDataNetValueError("Coast price panel contract is not satisfied")

    validation: dict[str, Any] = {
        "cost_rows": len(cost_rows),
        "net_value_rows": len(rows),
        "zone_system_groups": len(summary_rows),
        "ranges_ordered": True,
        "finite_outputs": True,
        "single_price_vintage": all(
            panel.report_basis == PRIMARY_PRICE_BASIS
            and panel.period_end == PRIMARY_PRICE_PERIOD_END
            for panel in price_panels.values()
        )
        and all(
            panel.report_basis == SENSITIVITY_PRICE_BASIS
            and panel.period_end == SENSITIVITY_PRICE_PERIOD_END
            for panel in sensitivity_panels.values()
        ),
        "coast_price_contract": coast_price_contract,
        "interior_aggregates_excluded": price_panels[
            "Interior"
        ].aggregates_excluded
        > 0,
        "labels_complete": labels_complete,
        "negative_net_base_rows": negative_net_base,
        "negative_net_values_documented": True,
        "silviculture_interior_only": silviculture_interior_only,
        "silviculture_interior_rows": silviculture_interior_rows,
        "silviculture_coast_rows_excluded": silviculture_coast_rows,
        "no_restricted_inputs": True,
        "combined_bc_qc_national_total": False,
        "price_outlier_register_rows": len(outliers),
        "price_outliers_used_in_model": 0,
    }
    if not validation["interior_aggregates_excluded"]:
        raise OpenDataNetValueError("expected Interior aggregate rows to be excluded")

    out_dir.mkdir(parents=True, exist_ok=True)
    net_value_path = out_dir / NET_VALUE_CSV
    summary_path = out_dir / SUMMARY_CSV
    sensitivity_path = out_dir / SENSITIVITY_CSV
    qc_path = out_dir / QC_REFERENCE_CSV
    outliers_path = out_dir / PRICE_OUTLIER_CSV
    report_path = out_dir / REPORT_MD
    summary_json_path = out_dir / SUMMARY_JSON
    manifest_path = out_dir / MANIFEST_JSON

    _write_csv(net_value_path, rows, tuple(rows[0]))
    _write_csv(summary_path, summary_rows, tuple(summary_rows[0]))
    _write_csv(sensitivity_path, sensitivity, tuple(sensitivity[0]))
    _write_csv(qc_path, [qc], tuple(qc))
    _write_csv(outliers_path, outliers, OUTLIER_FIELDS, allow_empty=True)
    report_path.write_text(
        render_report(
            rows, summary_rows, sensitivity, price_panels, allowances, qc, validation
        ),
        encoding="utf-8",
    )

    if inputs_dir is None:
        input_paths = (
            COST_ESTIMATES_CSV,
            COST_MANIFEST_JSON,
            LOG_MARKET_PRICES_CSV,
            LOG_MARKET_MANIFEST_JSON,
            APPRAISAL_CSV,
            APPRAISAL_MANIFEST_JSON,
            PROCUREMENT_CSV,
            PROCUREMENT_MANIFEST_JSON,
        )
    else:
        input_paths = tuple(input_tables.values())
    output_paths = (
        net_value_path,
        summary_path,
        sensitivity_path,
        qc_path,
        outliers_path,
        report_path,
    )

    summary_payload: dict[str, Any] = {
        "artifact_version": MODEL_VERSION,
        "dataset": "open_data_net_value",
        "status": "open_data_model_estimate",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "scope": {
            "jurisdictions": ["BC", "QC"],
            "bc": "modeled mill-delivered margin with open proxy logging/haul costs",
            "qc": "separate purchased-wood channel reference; not a net value",
            "combined_national_net_value": False,
            "model_ui_oscan_read_or_written": False,
            "restricted_inputs_used": [],
        },
        "definitions": {
            "gross_margin": "mill_delivered_price - logging_cost - haul_cost",
            "net_model": (
                "gross_margin - road_silviculture_allowance_if_available"
            ),
            "margin_range": (
                "low=price_low-cost_high; base=price_base-cost_base; "
                "high=price_high-cost_low"
            ),
            "modeled_margin": (
                "A residual from published averages and proxy costs, not an "
                "observed profit or a legal/policy net value."
            ),
        },
        "labels": {
            "evidence": EVIDENCE_MODEL,
            "observed": False,
            "policy_grade": False,
            "price_basis": PRICE_BASIS,
            "cost_basis": COST_BASIS,
            "net_value_status": NET_VALUE_STATUS,
            "qc_net_value_status": QC_NET_VALUE_STATUS,
            "combined_bc_qc_national_total": False,
        },
        "licensing": LICENSING,
        "price_panel": {
            "primary_report_basis": PRIMARY_PRICE_BASIS,
            "primary_period_end": PRIMARY_PRICE_PERIOD_END,
            "sensitivity_report_basis": SENSITIVITY_PRICE_BASIS,
            "sensitivity_period_end": SENSITIVITY_PRICE_PERIOD_END,
            "vintages_pooled": False,
            "interior_aggregate_rows_excluded": price_panels[
                "Interior"
            ].aggregates_excluded,
            "regions": {
                region: {
                    "low_cad_m3": round(panel.low_cad_m3, 2),
                    "base_cad_m3": round(panel.base_cad_m3, 2),
                    "high_cad_m3": round(panel.high_cad_m3, 2),
                    "method": panel.method,
                    "report_basis": panel.report_basis,
                    "period_end": panel.period_end,
                    "report_ids": list(panel.report_ids),
                    "log_classes": list(panel.log_classes),
                    "cells_used": panel.cells_used,
                    "report_classes_used": panel.report_classes_used,
                    "average_basis": panel.average_basis,
                    "report_totals_status": panel.report_totals_status,
                    "aggregates_excluded": panel.aggregates_excluded,
                    "outliers_excluded": panel.outliers_excluded,
                }
                for region, panel in sorted(price_panels.items())
            },
            "sensitivity_1month_regions": {
                region: {
                    "low_cad_m3": round(panel.low_cad_m3, 2),
                    "base_cad_m3": round(panel.base_cad_m3, 2),
                    "high_cad_m3": round(panel.high_cad_m3, 2),
                    "period_end": panel.period_end,
                }
                for region, panel in sorted(sensitivity_panels.items())
            },
            "flagged_outliers_in_primary_vintage": sum(
                panel.outliers_excluded for panel in price_panels.values()
            ),
        },
        "cost_panel": {
            "source": "data/derived/national/harvest_basis/bc_open_cost_model",
            "basis": COST_BASIS,
            "rows": len(cost_rows),
            "zones": len(EXPECTED_ZONES),
            "appraisal_regions": {
                region: sorted(
                    {row["zone"] for row in cost_rows if row["appraisal_region"] == region}
                )
                for region in ("Coast", "Interior")
            },
            "systems": list(EXPECTED_SYSTEMS),
            "logging_range_cad_m3": {
                "min": min(float(row["logging_base_cad_m3"]) for row in cost_rows),
                "max": max(float(row["logging_base_cad_m3"]) for row in cost_rows),
            },
            "haul_range_cad_m3": {
                "min": min(float(row["haul_base_cad_m3"]) for row in cost_rows),
                "max": max(float(row["haul_base_cad_m3"]) for row in cost_rows),
            },
        },
        "road_silviculture_panel": {
            "included": "Interior_priced_rows_only",
            "component": "Interior basic silviculture",
            "cost_basis": "CAD/m3",
            "source_series": SILVICULTURE_SERIES,
            "effective_date": SILVICULTURE_EFFECTIVE_DATE,
            "zone_values_cad_m3": {
                zone: round(allowance.value_cad_m3, 2)
                for zone, allowance in sorted(allowances.items())
            },
            "applies_to_price_region": {
                zone: ZONE_PRICE_REGION[zone] for zone in sorted(allowances)
            },
            "coast_included": False,
            "coast_component": COAST_SILVICULTURE_COMPONENT,
            "coast_basis": COAST_SILVICULTURE_BASIS,
            "coast_note": (
                "CAM Table 5-6 publishes Coast basic silviculture in CAD/ha by "
                "BEC unit only; no CAD/m3 conversion is invented."
            ),
            "excluded_components": [
                "road_development_CAD_per_km",
                "bridge_CAD_per_each",
                "culvert_CAD_per_m_or_each",
                "road_maintenance_without_zone_to_road_group_mapping",
            ],
            "conversion_from_CAD_per_km_or_each_invented": False,
        },
        "results": {
            "rows": len(rows),
            "zone_system_groups": len(summary_rows),
            "median_base_net_model_cad_m3": round(
                statistics.median(
                    float(row["net_model_base_cad_m3"]) for row in rows
                ),
                2,
            ),
            "min_base_net_model_cad_m3": round(
                min(float(row["net_model_base_cad_m3"]) for row in rows), 2
            ),
            "max_base_net_model_cad_m3": round(
                max(float(row["net_model_base_cad_m3"]) for row in rows), 2
            ),
            "negative_net_base_rows": negative_net_base,
            "negative_net_values_documented": True,
            "best_zone_systems": [
                {
                    "zone": row["zone"],
                    "system": row["system"],
                    "net_model_base_median_cad_m3": float(
                        row["net_model_base_median_cad_m3"]
                    ),
                }
                for row in sorted(
                    summary_rows,
                    key=lambda item: float(item["net_model_base_median_cad_m3"]),
                    reverse=True,
                )[:5]
            ],
            "worst_zone_systems": [
                {
                    "zone": row["zone"],
                    "system": row["system"],
                    "net_model_base_median_cad_m3": float(
                        row["net_model_base_median_cad_m3"]
                    ),
                }
                for row in sorted(
                    summary_rows,
                    key=lambda item: float(item["net_model_base_median_cad_m3"]),
                )[:5]
            ],
        },
        "qc_reference": qc,
        "sensitivity": sensitivity,
        "validation": validation,
        "caveats": [
            "Published averages, not observed transactions.",
            "Proxy productivity from public case studies, not a BC-wide survey.",
            "Proxy system assignment; seven forest zones map to two rate groups plus explicit appraisal regions.",
            "North West Zone is appraisal-Interior: Interior price, haul method and grapple skidder, with coastal/northwest hourly rates.",
            "South West Zone is Coast-priced; Coast silviculture is excluded because CAM publishes CAD/ha only.",
            "Slope, piece-size and sale-volume IAM coefficients are cost proxies.",
            "Helicopter logging is a specified-operation allowance proxy.",
            "The silviculture allowance is an appraisal/policy value, not an invoice.",
            "Nominal CAD; no deflator is applied across report periods.",
            "No grade/species/end-use matching to specific mills or tenures.",
            "Road/bridge/culvert CAD/km or CAD/each allowances are not converted to CAD/m3.",
            "Public-reference copyrighted sources, not strict OGL/CC-BY/StatCan open licences; PDFs not redistributed and commercial reuse requires permission.",
            "No observed transactions, mill profit, legal, AAC, tenure, or policy claim.",
        ],
        "non_claims": [
            "Not observed profit.",
            "Not a mill margin.",
            "Not an official net value.",
            "Not a stumpage rate.",
            "Not policy grade.",
            "Not a combined BC+QC national total.",
        ],
    }

    summary_json_path.write_text(
        json.dumps(summary_payload, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    manifest: dict[str, Any] = {
        "artifact_version": MODEL_VERSION,
        "generated_utc": summary_payload["generated_utc"],
        "dataset": "open_data_net_value",
        "description": (
            "Open-data BC modeled mill-delivered margin combining public log-market "
            "averages, open proxy logging/haul costs, and a published silviculture "
            "CAD/m3 allowance."
        ),
        "restricted_inputs_used": [],
        "licensing": LICENSING,
        "inputs": [
            {
                "path": _repo_path(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in input_paths
        ],
        "outputs": {
            path.name: {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in (*output_paths, summary_json_path)
        },
        "selection": {
            "price_basis": PRIMARY_PRICE_BASIS,
            "price_period_end": PRIMARY_PRICE_PERIOD_END,
            "sensitivity_price_basis": SENSITIVITY_PRICE_BASIS,
            "sensitivity_price_period_end": SENSITIVITY_PRICE_PERIOD_END,
            "price_regions": dict(ZONE_PRICE_REGION),
            "appraisal_regions": {
                region: sorted(
                    zone for zone, price_region in ZONE_PRICE_REGION.items()
                    if price_region == region
                )
                for region in ("Coast", "Interior")
            },
            "silviculture_series": SILVICULTURE_SERIES,
            "silviculture_effective_date": SILVICULTURE_EFFECTIVE_DATE,
            "silviculture_applies_to_price_region": SILVICULTURE_PRICE_REGION,
            "coast_silviculture_basis": COAST_SILVICULTURE_BASIS,
        },
        "validation": validation,
        "caveats": summary_payload["caveats"],
        "non_claims": summary_payload["non_claims"],
    }

    manifest_path.write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return manifest


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fieldnames: Sequence[str],
    *,
    allow_empty: bool = False,
) -> None:
    if not fieldnames:
        raise OpenDataNetValueError(f"refusing to write a table without columns: {path}")
    if not rows and not allow_empty:
        raise OpenDataNetValueError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory for the net-value CSV, summary, report and manifest.",
    )
    args = parser.parse_args(argv)
    manifest = build(args.out_dir)
    print(
        "wrote open-data net-value artifacts: "
        f"{manifest['validation']['net_value_rows']} rows, "
        f"{manifest['validation']['zone_system_groups']} zone-system groups"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
