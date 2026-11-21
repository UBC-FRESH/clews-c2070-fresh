"""Build the v2d forest-economics evidence layer.

The layer keeps evidence classes separate and refuses to manufacture a net
value from incomplete data:

* ``survey_procurement_cost`` -- QC 2022 T2 purchase cost and T20 BGAD cost;
  the difference is a procurement-channel cost spread, not revenue and not net
  value;
* ``benchmark_historical`` -- the BMMB 2011-2015 delivered-sale reference;
* ``observed_price`` -- BC published mill-delivered log-market averages;
* ``observed_stumpage`` -- BC BCTS auction results for standing timber;
* ``appraisal_allowance`` -- BC IAM/CAM tabular cost allowances;
* ``not_available`` -- components that are not published.

Neither jurisdiction has a defensible net value.  QC ``107.22`` is a buyer-side
procurement cost, not log-sale revenue.  BC has no public general logging
(tree-to-truck) or truck-haul $/m3 allowance; both are embedded in the
estimated winning bid.  Subtracting only road, bridge, culvert, silviculture,
specified-operation, or TOA allowances from a log price would omit logging and
haul and is explicitly forbidden.

Every summary row carries ``net_value_status=not_estimated``,
``policy_grade=false``, and ``net_value_is_combined_national=false``.  No BC
partial net value is computed and QC and BC monetary totals are never combined.

This module does not read or write the merged cost/revenue ledger, the Forest
LP, the UI, or any OSeCAN surface.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.forest_economics_v2d
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
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"

PROCUREMENT_DIR = HARVEST_BASIS_DIR / "procurement_benchmark"
BC_LOG_MARKET_DIR = HARVEST_BASIS_DIR / "bc_log_market"
BC_APPRAISAL_DIR = HARVEST_BASIS_DIR / "bc_appraisal_costs"
BCTS_DIR = HARVEST_BASIS_DIR / "bcts_bc"

DEFAULT_OUTPUT_DIR = HARVEST_BASIS_DIR / "forest_economics_v2d"

SUMMARY_CSV = "forest_economics_v2d_summary.csv"
QC_REFERENCE_CSV = "qc_procurement_reference.csv"
BC_REVENUE_CSV = "bc_revenue_benchmark.csv"
BC_COST_CSV = "bc_cost_coverage.csv"
BMMB_REFERENCE_CSV = "bmmb_reference.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "FOREST_ECONOMICS_V2D_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE_SURVEY = "survey_procurement_cost"
EVIDENCE_SURVEY_REGIONAL = "survey_regional_procurement_cost"
EVIDENCE_HISTORICAL = "benchmark_historical"
EVIDENCE_OBSERVED_PRICE = "observed_price"
EVIDENCE_OBSERVED_STUMPAGE = "observed_stumpage"
EVIDENCE_APPRAISAL = "appraisal_allowance"
EVIDENCE_NOT_AVAILABLE = "not_available"

EVIDENCE_TIERS = frozenset(
    {
        EVIDENCE_SURVEY,
        EVIDENCE_SURVEY_REGIONAL,
        EVIDENCE_HISTORICAL,
        EVIDENCE_OBSERVED_PRICE,
        EVIDENCE_OBSERVED_STUMPAGE,
        EVIDENCE_APPRAISAL,
        EVIDENCE_NOT_AVAILABLE,
    }
)

NET_VALUE_STATUS = "not_estimated"
BC_NET_VALUE_STATUS = NET_VALUE_STATUS
QC_NET_VALUE_REASON = "qc_procurement_cost_benchmark_not_net_value"
QC_REGIONAL_REASON = "conditional_regional_cost_spread_not_net_value"
HISTORICAL_NET_VALUE_REASON = "revenue_benchmark_only_no_cost_stack"
BC_NET_VALUE_REASON = "missing_logging_and_haul_cost"
COMBINED_NATIONAL_REASON = "no_joint_qc_bc_net_value"
POLICY_GRADE = "false"
PROCUREMENT_INTERPRETATION = "purchased_vs_bgad_procurement_cost_spread"

ARITHMETIC_TOLERANCE = 0.011
RATE_TOLERANCE = 0.0001
VOLUME_TOLERANCE = 2.0

BC_PRICE_ROW_COUNT = 15705
BC_REPORT_COUNT = 505
BC_APPRAISAL_ROW_COUNT = 879
BCTS_SALE_COUNT = 51
BCTS_LEDGER_ROW_COUNT = 200
QC_PROVINCIAL_ROW_COUNT = 9
QC_REGIONAL_ROW_COUNT = 9
QC_REGIONAL_VALUE_COUNT = 7
QC_BMMB_SOURCE_ROW_COUNT = 7
QC_REFERENCE_ROW_COUNT = 18
BMMB_REFERENCE_ROW_COUNT = 3

BC_PRICE_MIN_CAD_M3 = 0.0
BC_PRICE_MAX_CAD_M3 = 2000.0

PRICE_CELL_COLUMNS = {
    "report_id",
    "region",
    "report_basis",
    "period_start",
    "period_end",
    "log_class",
    "species",
    "grade",
    "end_use",
    "price_cad_m3",
    "price_basis",
    "observation_type",
    "is_aggregate",
    "price_status",
    "price_plausibility",
    "volume_m3",
    "report_total_volume_m3",
    "report_totals_status",
    "source_document",
}

APPRAISAL_COLUMNS = {
    "record_id",
    "jurisdiction",
    "region",
    "source_series",
    "component_group",
    "component",
    "subcomponent",
    "cost_basis",
    "value",
    "observation_type",
}

APPRAISAL_COVERAGE_COLUMNS = {
    "region",
    "table_or_section",
    "component_group",
    "extraction_status",
    "notes",
}

BCTS_LEDGER_COLUMNS = {
    "jurisdiction",
    "year",
    "spatial_unit",
    "volume_m3",
    "unit",
    "value_cad",
    "value_basis",
    "perspective",
    "component",
    "sign",
}

BCTS_RESULT_COLUMNS = {
    "licence_id",
    "status",
    "auction_date",
    "winning_bid_cad_m3",
    "upset_cad_m3",
    "bonus_cad_m3",
    "bidders",
    "location",
}

QC_PROVINCIAL_COLUMNS = {
    "benchmark_id",
    "geography",
    "region_code",
    "species_group",
    "product_or_grade",
    "aggregation_level",
    "purchase_cost_cad_m3",
    "bgad_procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "bgad_procurement_after_transfers_cad_m3",
    "cost_spread_before_transfers_cad_m3",
    "cost_spread_after_transfers_cad_m3",
    "spread_status",
    "unit",
    "price_year",
    "currency_basis",
    "purchase_semantics",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "is_additive",
    "notes",
}

QC_REGIONAL_COLUMNS = {
    "regional_benchmark_id",
    "region_code",
    "statistic",
    "aggregation_level",
    "sample_n",
    "three_respondent_flag",
    "bgad_procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "bgad_procurement_after_transfers_cad_m3",
    "reference_purchase_cad_m3",
    "conditional_cost_spread_before_transfers_cad_m3",
    "conditional_cost_spread_after_transfers_cad_m3",
    "spread_price_assumption",
    "columns_are_independent_extrema",
    "unit",
    "price_year",
    "currency_basis",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "is_additive",
    "notes",
}

QC_BMMB_COLUMNS = {
    "benchmark_id",
    "scope_id",
    "tenure_scope",
    "is_primary_benchmark",
    "aggregation_level",
    "sale_count",
    "full_rate_sale_count",
    "partial_award_sale_count",
    "volume_m3",
    "rate_computable_volume_m3",
    "weighted_rate_cad_m3",
    "median_rate_cad_m3",
    "min_rate_cad_m3",
    "max_rate_cad_m3",
    "currency_basis",
    "deflator_applied",
    "grade_split",
    "species_allocation",
    "evidence",
    "policy_grade",
    "net_value_status",
}

QC_PROCUREMENT_SOURCE_FIELDS = (
    "benchmark_id",
    "regional_benchmark_id",
    "geography",
    "region_code",
    "region_label",
    "species_group",
    "species_label",
    "product_or_grade",
    "statistic",
    "aggregation_level",
    "sample_n",
    "confidentiality_flag",
    "three_respondent_flag",
    "purchase_cost_cad_m3",
    "bgad_procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "bgad_procurement_after_transfers_cad_m3",
    "cost_spread_before_transfers_cad_m3",
    "cost_spread_after_transfers_cad_m3",
    "spread_status",
    "reference_purchase_cad_m3",
    "conditional_cost_spread_before_transfers_cad_m3",
    "conditional_cost_spread_after_transfers_cad_m3",
    "spread_price_assumption",
    "unit",
    "price_year",
    "currency_basis",
    "survey_year",
    "purchase_source_table",
    "bgad_source_table",
    "purchase_price_basis",
    "bgad_price_basis",
    "survey_source_id",
    "source_document",
    "columns_are_independent_extrema",
    "three_respondent_extreme_columns",
    "bound_region_codes",
    "purchase_semantics",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "observation_type",
    "is_additive",
    "accounting_caveats",
    "notes",
)

QC_REFERENCE_FIELDS = (
    "reference_id",
    "reference_type",
    "source_artifact",
    "source_record_key",
    "jurisdiction",
    "validation_status",
    "validation_detail",
) + QC_PROCUREMENT_SOURCE_FIELDS

BMMB_SOURCE_FIELDS = (
    "benchmark_id",
    "scope_id",
    "tenure_scope",
    "is_primary_benchmark",
    "aggregation_level",
    "sale_year",
    "price_year",
    "price_year_basis",
    "currency_basis",
    "region_bmmb",
    "sale_count",
    "awarded_count",
    "unsold_count",
    "observed_price_count",
    "full_rate_sale_count",
    "partial_award_sale_count",
    "volume_m3",
    "awarded_volume_m3",
    "observed_value_cad",
    "rate_computable_volume_m3",
    "rate_computable_value_cad",
    "weighted_rate_cad_m3",
    "median_rate_cad_m3",
    "unweighted_mean_rate_cad_m3",
    "min_rate_cad_m3",
    "max_rate_cad_m3",
    "partial_award_value_cad",
    "partial_lower_bound_min_cad_m3",
    "partial_lower_bound_max_cad_m3",
    "reoffer_sale_count",
    "reoffer_overlap_m3",
    "reoffer_sale_ids",
    "deflator_applied",
    "species_scope",
    "quality",
    "grade_split",
    "species_allocation",
    "not_stumpage",
    "destination_reported",
    "service_bundle_published",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "observation_type",
    "notes",
)

class ForestEconomicsV2DError(RuntimeError):
    """Raised when an input or output violates a required invariant."""


@dataclass(frozen=True, slots=True)
class PriceCell:
    report_id: str
    region: str
    report_basis: str
    log_class: str
    period_start: str
    period_end: str
    period_year: int
    species: str
    grade: str
    end_use: str
    price_cad_m3: float
    is_aggregate: bool
    price_plausibility: str
    volume_m3: float | None
    report_total_volume_m3: float | None
    report_totals_status: str
    source_document: str

    @property
    def grade_or_end_use(self) -> str:
        return self.grade if self.grade else self.end_use


@dataclass(frozen=True, slots=True)
class PriceRange:
    minimum: float | None
    p25: float | None
    median: float | None
    p75: float | None
    maximum: float | None


@dataclass(frozen=True, slots=True)
class PriceGroupKey:
    region: str
    report_basis: str
    log_class: str
    aggregation_level: str
    period_year: str
    species: str
    grade_or_end_use: str
    is_aggregate: bool


@dataclass(frozen=True, slots=True)
class PriceGroupSummary:
    key: PriceGroupKey
    period_label: str
    period_start_min: str
    period_end_max: str
    report_count: int
    cell_count: int
    ok_cell_count: int
    flagged_cell_count: int
    zero_cell_count: int
    below_one_cell_count: int
    above_one_thousand_cell_count: int
    price_range: PriceRange


@dataclass(frozen=True, slots=True)
class SeriesPriceSummary:
    region: str
    report_basis: str
    log_class: str
    period_label: str
    period_start_min: str
    period_end_max: str
    report_count: int
    cell_count: int
    ok_cell_count: int
    flagged_cell_count: int
    zero_cell_count: int
    below_one_cell_count: int
    above_one_thousand_cell_count: int
    price_range: PriceRange


@dataclass(frozen=True, slots=True)
class AppraisalCost:
    record_id: str
    region: str
    source_series: str
    component_group: str
    component: str
    cost_basis: str
    value: float
    observation_type: str


@dataclass(frozen=True, slots=True)
class AppraisalCoverageEntry:
    region: str
    section: str
    component_group: str
    extraction_status: str
    notes: str


@dataclass(frozen=True, slots=True)
class CostCoverageRow:
    coverage_id: str
    component: str
    component_group: str
    coverage_status: str
    covered: bool
    evidence_tier: str
    appraisal_allowance: bool
    observed_cost: bool
    row_count: int
    coast_row_count: int
    interior_row_count: int
    cost_bases: str
    direct_cad_m3_row_count: int
    net_value_usable: str
    conversion_requirement: str
    missing_reason: str
    source_artifact: str
    notes: str


@dataclass(frozen=True, slots=True)
class BctsSale:
    sale_id: str
    auction_date: str
    location: str
    volume_m3: float
    stumpage_base_cad_m3: float | None
    stumpage_bid_premium_cad_m3: float
    stumpage_total_cad_m3: float
    total_basis: str
    reported_winning_bid_cad_m3: float | None
    reported_upset_cad_m3: float | None
    reported_bonus_cad_m3: float | None
    bidder_count: int | None


@dataclass(frozen=True, slots=True)
class StumpageStats:
    sale_count: int
    full_bid_count: int
    lower_bound_count: int
    volume_m3: float
    full_bid_volume_m3: float
    volume_weighted_stumpage_cad_m3: float
    median_full_bid_cad_m3: float
    min_full_bid_cad_m3: float
    max_full_bid_cad_m3: float
    min_observed_cad_m3: float
    max_observed_cad_m3: float


@dataclass(frozen=True, slots=True)
class BuildResult:
    output_dir: Path
    summary_rows: tuple[dict[str, Any], ...]
    price_rows: tuple[dict[str, Any], ...]
    cost_rows: tuple[dict[str, Any], ...]
    qc_rows: tuple[dict[str, str], ...]
    bmmb_rows: tuple[dict[str, str], ...]
    checks: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ForestEconomicsV2DError(f"missing input: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def require_columns(
    path: Path, rows: Sequence[Mapping[str, str]], required: Iterable[str]
) -> None:
    if not rows:
        raise ForestEconomicsV2DError(f"input has no data rows: {path}")
    missing = set(required) - set(rows[0])
    if missing:
        raise ForestEconomicsV2DError(f"{path} is missing columns: {sorted(missing)}")


def parse_finite_float(text: str, *, context: str, minimum: float | None = None) -> float:
    stripped = (text or "").strip()
    if stripped == "" or stripped.lower() in {"na", "n/a", "nan", "inf", "-inf", "none"}:
        raise ForestEconomicsV2DError(f"expected a finite number for {context}, got {text!r}")
    value = float(stripped)
    if not math.isfinite(value):
        raise ForestEconomicsV2DError(f"non-finite number for {context}: {text!r}")
    if minimum is not None and value < minimum:
        raise ForestEconomicsV2DError(f"value below {minimum} for {context}: {value}")
    return value


def parse_optional_float(text: str, *, context: str) -> float | None:
    stripped = (text or "").strip()
    if stripped == "":
        return None
    return parse_finite_float(stripped, context=context)


def parse_optional_int(text: str, *, context: str) -> int | None:
    stripped = (text or "").strip()
    if stripped == "":
        return None
    value = int(stripped)
    if value < 0:
        raise ForestEconomicsV2DError(f"negative integer for {context}: {value}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path, *, role: str, rows: int | None = None) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": relative_path(path),
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "role": role,
    }
    if rows is not None:
        record["rows"] = rows
    return record


def verify_manifest_outputs(manifest_path: Path, directory: Path) -> int:
    if not manifest_path.is_file():
        raise ForestEconomicsV2DError(f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise ForestEconomicsV2DError(f"{manifest_path} has no output records")
    verified = 0
    for record in outputs:
        name = record.get("path")
        expected = record.get("sha256")
        if not name or not expected:
            raise ForestEconomicsV2DError(f"incomplete output record in {manifest_path}")
        path = directory / Path(name).name
        if not path.is_file():
            raise ForestEconomicsV2DError(f"manifest output is missing: {path}")
        if sha256_file(path) != expected:
            raise ForestEconomicsV2DError(f"manifest hash mismatch: {path}")
        verified += 1
    return verified


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ForestEconomicsV2DError(f"refusing to write non-finite value: {value}")
        return f"{value:.4f}"
    return str(value)


def write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: format_cell(row.get(name)) for name in fieldnames})


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    weight = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def rounded(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


def summarise_prices(prices: Sequence[float]) -> PriceRange:
    if not prices:
        return PriceRange(None, None, None, None, None)
    return PriceRange(
        minimum=min(prices),
        p25=percentile(prices, 0.25),
        median=percentile(prices, 0.5),
        p75=percentile(prices, 0.75),
        maximum=max(prices),
    )


def parse_price_cells(
    path: Path, *, rows: Sequence[Mapping[str, str]] | None = None
) -> tuple[PriceCell, ...]:
    raw_rows = list(rows) if rows is not None else read_csv_rows(path)
    require_columns(path, raw_rows, PRICE_CELL_COLUMNS)
    parsed: list[PriceCell] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        region = row["region"]
        if region not in {"coast", "interior"}:
            raise ForestEconomicsV2DError(f"unexpected region for {context}: {region!r}")
        report_basis = row["report_basis"]
        if report_basis not in {"1_month", "3_month"}:
            raise ForestEconomicsV2DError(
                f"unexpected report basis for {context}: {report_basis!r}"
            )
        if row["price_basis"] != "mill_delivered":
            raise ForestEconomicsV2DError(f"unexpected price basis for {context}: {row['price_basis']!r}")
        if row["observation_type"] != "reported_market_average":
            raise ForestEconomicsV2DError(
                f"unexpected observation type for {context}: {row['observation_type']!r}"
            )
        if row["price_status"] != "reported":
            raise ForestEconomicsV2DError(f"unexpected price status for {context}: {row['price_status']!r}")

        grade = row["grade"].strip()
        end_use = row["end_use"].strip()
        is_aggregate_flag = row["is_aggregate"]
        if is_aggregate_flag not in {"true", "false"}:
            raise ForestEconomicsV2DError(
                f"invalid is_aggregate flag for {context}: {is_aggregate_flag!r}"
            )
        is_aggregate = is_aggregate_flag == "true"
        if region == "coast":
            if not grade or end_use:
                raise ForestEconomicsV2DError(
                    f"coast row must carry a grade and no end use for {context}"
                )
        else:
            if grade or not end_use:
                raise ForestEconomicsV2DError(
                    f"interior row must carry an end use and no grade for {context}"
                )
        if is_aggregate and (region != "interior" or end_use != "weighted_average"):
            raise ForestEconomicsV2DError(
                f"aggregate flag outside the Interior weighted average for {context}"
            )

        price = parse_finite_float(row["price_cad_m3"], context=context, minimum=0.0)
        if price > BC_PRICE_MAX_CAD_M3:
            raise ForestEconomicsV2DError(f"price above hard range for {context}: {price}")
        expected_flag = (
            "zero_cad_m3"
            if price == 0.0
            else "below_1_cad_m3"
            if price < 1.0
            else "above_1000_cad_m3"
            if price > 1000.0
            else "ok"
        )
        if row["price_plausibility"] != expected_flag:
            raise ForestEconomicsV2DError(
                f"plausibility flag mismatch for {context}: "
                f"{row['price_plausibility']!r} vs {expected_flag!r}"
            )

        try:
            period_start = date.fromisoformat(row["period_start"])
            period_end = date.fromisoformat(row["period_end"])
        except ValueError as exc:
            raise ForestEconomicsV2DError(f"invalid period date for {context}") from exc
        if period_start > period_end:
            raise ForestEconomicsV2DError(f"period start after end for {context}")

        volume = parse_optional_float(row["volume_m3"], context=context)
        if volume is not None and volume <= 0:
            raise ForestEconomicsV2DError(f"non-positive volume for {context}: {volume}")
        if is_aggregate and volume is not None:
            raise ForestEconomicsV2DError(f"aggregate row carries product volume for {context}")
        report_total_volume = parse_optional_float(
            row["report_total_volume_m3"], context=context
        )

        key = (row["report_id"], row["species"], grade, end_use)
        if key in seen_keys:
            raise ForestEconomicsV2DError(f"duplicate price cell key for {context}: {key}")
        seen_keys.add(key)

        parsed.append(
            PriceCell(
                report_id=row["report_id"],
                region=region,
                report_basis=report_basis,
                log_class=row["log_class"],
                period_start=row["period_start"],
                period_end=row["period_end"],
                period_year=period_end.year,
                species=row["species"],
                grade=grade,
                end_use=end_use,
                price_cad_m3=price,
                is_aggregate=is_aggregate,
                price_plausibility=row["price_plausibility"],
                volume_m3=volume,
                report_total_volume_m3=report_total_volume,
                report_totals_status=row["report_totals_status"],
                source_document=row["source_document"],
            )
        )
    if len(parsed) != BC_PRICE_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {BC_PRICE_ROW_COUNT} BC price cells, found {len(parsed)}"
        )
    return tuple(parsed)


def build_price_groups(cells: Sequence[PriceCell]) -> tuple[PriceGroupSummary, ...]:
    grouped: dict[PriceGroupKey, list[PriceCell]] = defaultdict(list)
    for cell in cells:
        for aggregation_level in ("annual", "series_period"):
            period_year = str(cell.period_year) if aggregation_level == "annual" else "all_years"
            key = PriceGroupKey(
                region=cell.region,
                report_basis=cell.report_basis,
                log_class=cell.log_class,
                aggregation_level=aggregation_level,
                period_year=period_year,
                species=cell.species,
                grade_or_end_use=cell.grade_or_end_use,
                is_aggregate=cell.is_aggregate,
            )
            grouped[key].append(cell)

    summaries: list[PriceGroupSummary] = []
    for key in sorted(
        grouped,
        key=lambda item: (
            item.region,
            item.report_basis,
            item.log_class,
            item.aggregation_level,
            item.period_year,
            item.species,
            item.grade_or_end_use,
            str(item.is_aggregate),
        ),
    ):
        group = grouped[key]
        report_ids = {cell.report_id for cell in group}
        if key.aggregation_level == "annual" and len(report_ids) > 12:
            raise ForestEconomicsV2DError(
                f"annual group exceeds 12 reports: {key} has {len(report_ids)}"
            )
        years = sorted({cell.period_year for cell in group})
        period_label = str(years[0]) if len(years) == 1 else f"{years[0]}-{years[-1]}"
        ok_prices = [
            cell.price_cad_m3 for cell in group if cell.price_plausibility == "ok"
        ]
        summaries.append(
            PriceGroupSummary(
                key=key,
                period_label=period_label,
                period_start_min=min(cell.period_start for cell in group),
                period_end_max=max(cell.period_end for cell in group),
                report_count=len(report_ids),
                cell_count=len(group),
                ok_cell_count=len(ok_prices),
                flagged_cell_count=sum(
                    1 for cell in group if cell.price_plausibility != "ok"
                ),
                zero_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "zero_cad_m3"
                ),
                below_one_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "below_1_cad_m3"
                ),
                above_one_thousand_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "above_1000_cad_m3"
                ),
                price_range=summarise_prices(ok_prices),
            )
        )
    return tuple(summaries)


def build_series_summaries(cells: Sequence[PriceCell]) -> tuple[SeriesPriceSummary, ...]:
    grouped: dict[tuple[str, str, str], list[PriceCell]] = defaultdict(list)
    for cell in cells:
        if cell.is_aggregate:
            continue
        grouped[(cell.region, cell.report_basis, cell.log_class)].append(cell)

    summaries: list[SeriesPriceSummary] = []
    for key in sorted(grouped):
        group = grouped[key]
        years = sorted({cell.period_year for cell in group})
        ok_prices = [
            cell.price_cad_m3 for cell in group if cell.price_plausibility == "ok"
        ]
        summaries.append(
            SeriesPriceSummary(
                region=key[0],
                report_basis=key[1],
                log_class=key[2],
                period_label=f"{years[0]}-{years[-1]}",
                period_start_min=min(cell.period_start for cell in group),
                period_end_max=max(cell.period_end for cell in group),
                report_count=len({cell.report_id for cell in group}),
                cell_count=len(group),
                ok_cell_count=len(ok_prices),
                flagged_cell_count=sum(
                    1 for cell in group if cell.price_plausibility != "ok"
                ),
                zero_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "zero_cad_m3"
                ),
                below_one_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "below_1_cad_m3"
                ),
                above_one_thousand_cell_count=sum(
                    1 for cell in group if cell.price_plausibility == "above_1000_cad_m3"
                ),
                price_range=summarise_prices(ok_prices),
            )
        )
    return tuple(summaries)


def price_row_from_summary(summary: PriceGroupSummary) -> dict[str, Any]:
    key = summary.key
    return {
        "benchmark_id": (
            f"bc_{key.region}_{key.report_basis}_{key.log_class}_"
            f"{key.aggregation_level}_{key.period_year}_{key.species}_{key.grade_or_end_use}"
        ),
        "region": key.region,
        "report_basis": key.report_basis,
        "log_class": key.log_class,
        "aggregation_level": key.aggregation_level,
        "period_year": key.period_year,
        "period_label": summary.period_label,
        "period_start_min": summary.period_start_min,
        "period_end_max": summary.period_end_max,
        "species": key.species,
        "grade_or_end_use": key.grade_or_end_use,
        "grade": key.grade_or_end_use if key.region == "coast" else "",
        "end_use": key.grade_or_end_use if key.region == "interior" else "",
        "is_aggregate": key.is_aggregate,
        "cell_role": "reported_weighted_average" if key.is_aggregate else "product_cell",
        "exclude_from_product_cell_aggregation": key.is_aggregate,
        "observation_type": "reported_market_average",
        "price_basis": "mill_delivered",
        "price_unit": "CAD/m3",
        "report_count": summary.report_count,
        "cell_count": summary.cell_count,
        "ok_cell_count": summary.ok_cell_count,
        "flagged_cell_count": summary.flagged_cell_count,
        "zero_cell_count": summary.zero_cell_count,
        "below_one_cell_count": summary.below_one_cell_count,
        "above_one_thousand_cell_count": summary.above_one_thousand_cell_count,
        "price_min_cad_m3": summary.price_range.minimum,
        "price_p25_cad_m3": summary.price_range.p25,
        "price_median_cad_m3": summary.price_range.median,
        "price_p75_cad_m3": summary.price_range.p75,
        "price_max_cad_m3": summary.price_range.maximum,
        "price_statistic": "range_of_published_average_cells",
        "cell_weighting": "equal_cell_weight",
        "range_status": "ok" if summary.ok_cell_count else "no_plausible_cells",
        "annualization_applied": False,
        "vintage_handling": (
            "published monthly average; not annualized and not additive with 3_month"
            if key.report_basis == "1_month"
            else "published rolling three-month average; not annualized, not additive "
            "with 1_month, and overlapping windows are not summed"
        ),
        "is_additive": False,
        "evidence_tier": EVIDENCE_OBSERVED_PRICE,
        "policy_grade": False,
        "source_artifact": relative_path(BC_LOG_MARKET_DIR / "bc_log_market_prices.csv"),
        "notes": (
            "Interior species weighted average as reported; never added to product cells."
            if key.is_aggregate
            else "Published mill-delivered average; blank source cells are missing, not zero."
        ),
    }


def parse_appraisal_costs(path: Path) -> tuple[AppraisalCost, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, APPRAISAL_COLUMNS)
    parsed: list[AppraisalCost] = []
    seen_keys: set[tuple[str, ...]] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        if row["jurisdiction"] != "BC":
            raise ForestEconomicsV2DError(f"unexpected jurisdiction for {context}: {row['jurisdiction']!r}")
        if row["region"] not in {"Coast", "Interior"}:
            raise ForestEconomicsV2DError(f"unexpected region for {context}: {row['region']!r}")
        if row["observation_type"] != EVIDENCE_APPRAISAL:
            raise ForestEconomicsV2DError(
                f"unexpected observation type for {context}: {row['observation_type']!r}"
            )
        value = parse_finite_float(row["value"], context=context, minimum=0.0)
        if value == 0.0:
            raise ForestEconomicsV2DError(f"zero appraisal value for {context}")
        key = (
            row["source_series"],
            row["region"],
            row["component_group"],
            row["component"],
            row["subcomponent"],
            row["cost_basis"],
            row["record_id"],
        )
        if key in seen_keys:
            raise ForestEconomicsV2DError(f"duplicate appraisal key for {context}")
        seen_keys.add(key)
        parsed.append(
            AppraisalCost(
                record_id=row["record_id"],
                region=row["region"],
                source_series=row["source_series"],
                component_group=row["component_group"],
                component=row["component"],
                cost_basis=row["cost_basis"],
                value=value,
                observation_type=row["observation_type"],
            )
        )
    if len(parsed) != BC_APPRAISAL_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {BC_APPRAISAL_ROW_COUNT} appraisal rows, found {len(parsed)}"
        )
    return tuple(parsed)


def parse_appraisal_coverage(path: Path) -> tuple[AppraisalCoverageEntry, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, APPRAISAL_COVERAGE_COLUMNS)
    if len(raw_rows) != 28:
        raise ForestEconomicsV2DError(
            f"expected 28 appraisal coverage entries, found {len(raw_rows)}"
        )
    statuses = [row["extraction_status"] for row in raw_rows]
    if statuses.count("not_published") != 2 or statuses.count("partial") != 3:
        raise ForestEconomicsV2DError(
            "appraisal coverage status counts changed: "
            f"not_published={statuses.count('not_published')}, "
            f"partial={statuses.count('partial')}"
        )
    return tuple(
        AppraisalCoverageEntry(
            region=row["region"],
            section=row["table_or_section"],
            component_group=row["component_group"],
            extraction_status=row["extraction_status"],
            notes=row["notes"],
        )
        for row in raw_rows
    )


def require_appraisal_gap(
    coverage: Sequence[AppraisalCoverageEntry], *, component_group: str, status: str
) -> AppraisalCoverageEntry:
    matches = [
        entry
        for entry in coverage
        if entry.component_group == component_group and entry.extraction_status == status
    ]
    if not matches:
        raise ForestEconomicsV2DError(
            f"appraisal coverage must document {component_group}={status}"
        )
    return matches[0]


def build_cost_coverage(
    costs: Sequence[AppraisalCost],
    coverage: Sequence[AppraisalCoverageEntry],
    appraisal_manifest: Mapping[str, Any],
) -> tuple[CostCoverageRow, ...]:
    logging_gap = require_appraisal_gap(
        coverage, component_group="logging", status="not_published"
    )
    tree_to_truck_gap = require_appraisal_gap(
        coverage, component_group="tree_to_truck", status="not_published"
    )
    hauling_gap = require_appraisal_gap(
        coverage, component_group="hauling", status="partial"
    )
    caveats = " ".join(appraisal_manifest.get("caveats", []))
    if "do not publish a general tree-to-truck or logging" not in caveats:
        raise ForestEconomicsV2DError("appraisal manifest does not document missing logging")
    if "General truck hauling is represented only by cycle-time/EWB" not in caveats:
        raise ForestEconomicsV2DError("appraisal manifest does not document partial truck haul")

    by_group: dict[str, list[AppraisalCost]] = defaultdict(list)
    for cost in costs:
        by_group[cost.component_group].append(cost)

    def covered_row(
        *,
        coverage_id: str,
        component: str,
        component_groups: Sequence[str],
        coverage_status: str,
        net_value_usable: str,
        conversion_requirement: str,
        notes: str,
    ) -> CostCoverageRow:
        rows = [cost for group in component_groups for cost in by_group.get(group, [])]
        if not rows:
            raise ForestEconomicsV2DError(f"no appraisal rows for covered component {component}")
        bases = sorted({cost.cost_basis for cost in rows})
        return CostCoverageRow(
            coverage_id=coverage_id,
            component=component,
            component_group="+".join(component_groups),
            coverage_status=coverage_status,
            covered=True,
            evidence_tier=EVIDENCE_APPRAISAL,
            appraisal_allowance=True,
            observed_cost=False,
            row_count=len(rows),
            coast_row_count=sum(1 for cost in rows if cost.region == "Coast"),
            interior_row_count=sum(1 for cost in rows if cost.region == "Interior"),
            cost_bases=";".join(bases),
            direct_cad_m3_row_count=sum(1 for cost in rows if cost.cost_basis == "CAD/m3"),
            net_value_usable=net_value_usable,
            conversion_requirement=conversion_requirement,
            missing_reason="",
            source_artifact=relative_path(BC_APPRAISAL_DIR / "bc_appraisal_costs.csv"),
            notes=notes,
        )

    rows = [
        covered_row(
            coverage_id="bc_cost_road",
            component="road",
            component_groups=("road_development", "road_management"),
            coverage_status="published_appraisal_allowance",
            net_value_usable="requires_site_conversion",
            conversion_requirement="road length (km) and harvest volume (m3)",
            notes="CAM Table 5-1 and IAM subgrade/stabilizing tables; IAM road maintenance is $/m3. Not an observed contractor cost.",
        ),
        covered_row(
            coverage_id="bc_cost_bridge",
            component="bridge",
            component_groups=("bridge",),
            coverage_status="published_appraisal_allowance",
            net_value_usable="requires_site_conversion",
            conversion_requirement="bridge count/type and harvest volume (m3)",
            notes="CAM Tables 5-2/5-3 and IAM Table 4-4 are $/structure, not $/m3. No interpolation.",
        ),
        covered_row(
            coverage_id="bc_cost_culvert",
            component="culvert",
            component_groups=("culvert",),
            coverage_status="published_appraisal_allowance",
            net_value_usable="requires_site_conversion",
            conversion_requirement="culvert counts/lengths and harvest volume (m3)",
            notes="CAM Table 5-4 and IAM Table 4-3 are $/culvert or $/m; no total $/m3 exists.",
        ),
        covered_row(
            coverage_id="bc_cost_silviculture",
            component="silviculture",
            component_groups=("silviculture",),
            coverage_status="published_appraisal_allowance",
            net_value_usable="partially_direct",
            conversion_requirement="CAD/ha rows require treated area and volume; Interior quarterly rows are CAD/m3",
            notes="CAM Table 5-6 and IAM Table 4-8 are $/ha; the quarterly Interior table is $/m3. Both are allowances.",
        ),
        covered_row(
            coverage_id="bc_cost_specified_operations",
            component="specified_operations",
            component_groups=("specified_operations",),
            coverage_status="published_appraisal_allowance",
            net_value_usable="conditional_route_specific",
            conversion_requirement="route, distance, water/rail/barge mode, and volume",
            notes="CAM 4.4 and IAM 3.3 allowances apply only where the specified operation is required.",
        ),
        covered_row(
            coverage_id="bc_cost_tenure_obligation",
            component="tenure_obligation",
            component_groups=("tenure_obligation",),
            coverage_status="published_appraisal_allowance",
            net_value_usable="partially_direct",
            conversion_requirement="Some TOA rows are CAD/m3; per-hectare rows require area and volume",
            notes="CAM TOA and IAM FMA/TOA allowances; policy allowances, not observed payments.",
        ),
        CostCoverageRow(
            coverage_id="bc_cost_logging",
            component="logging",
            component_group="tree_to_truck",
            coverage_status="not_published",
            covered=False,
            evidence_tier=EVIDENCE_NOT_AVAILABLE,
            appraisal_allowance=False,
            observed_cost=False,
            row_count=0,
            coast_row_count=0,
            interior_row_count=0,
            cost_bases="",
            direct_cad_m3_row_count=0,
            net_value_usable="no",
            conversion_requirement="not convertible without published or authorized logging cost",
            missing_reason="logging_not_published_embedded_in_ewb",
            source_artifact=relative_path(BC_APPRAISAL_DIR / "bc_appraisal_coverage.csv"),
            notes=(
                f"{tree_to_truck_gap.notes}; {logging_gap.notes}. "
                "No public $/m3 logging allowance table exists."
            ),
        ),
        CostCoverageRow(
            coverage_id="bc_cost_truck_haul",
            component="truck_haul",
            component_group="hauling",
            coverage_status="partial_appraisal_allowance",
            covered=False,
            evidence_tier=EVIDENCE_APPRAISAL,
            appraisal_allowance=True,
            observed_cost=False,
            row_count=len(by_group.get("hauling", [])),
            coast_row_count=sum(
                1 for cost in by_group.get("hauling", []) if cost.region == "Coast"
            ),
            interior_row_count=sum(
                1 for cost in by_group.get("hauling", []) if cost.region == "Interior"
            ),
            cost_bases=";".join(
                sorted({cost.cost_basis for cost in by_group.get("hauling", [])})
            ),
            direct_cad_m3_row_count=sum(
                1
                for cost in by_group.get("hauling", [])
                if cost.cost_basis == "CAD/m3"
            ),
            net_value_usable="no",
            conversion_requirement="a general truck-haul $/m3 is not published; rail/water/barge are route-specific",
            missing_reason="general_truck_haul_not_published_embedded_in_ewb",
            source_artifact=relative_path(BC_APPRAISAL_DIR / "bc_appraisal_coverage.csv"),
            notes=(
                f"{hauling_gap.notes}. Rail/water/barge specified operations are captured, "
                "but they do not substitute for general truck haul."
            ),
        ),
        CostCoverageRow(
            coverage_id="bc_cost_ewb",
            component="estimated_winning_bid",
            component_group="stumpage_mechanism",
            coverage_status="not_published_as_cost",
            covered=False,
            evidence_tier=EVIDENCE_NOT_AVAILABLE,
            appraisal_allowance=False,
            observed_cost=False,
            row_count=0,
            coast_row_count=0,
            interior_row_count=0,
            cost_bases="",
            direct_cad_m3_row_count=0,
            net_value_usable="no",
            conversion_requirement="not a cost table; EWB is a stumpage formula",
            missing_reason="ewb_is_stumpage_formula_embedding_logging_and_haul",
            source_artifact=relative_path(BC_APPRAISAL_DIR / "manifest.json"),
            notes=(
                "IAM/CAM embed logging and haul in the EWB equation. The EWB is not a "
                "published cost allowance and is not used here as a cost."
            ),
        ),
        CostCoverageRow(
            coverage_id="bc_cost_full_net_value",
            component="full_bc_net_value",
            component_group="net_value",
            coverage_status=BC_NET_VALUE_STATUS,
            covered=False,
            evidence_tier=EVIDENCE_NOT_AVAILABLE,
            appraisal_allowance=False,
            observed_cost=False,
            row_count=0,
            coast_row_count=0,
            interior_row_count=0,
            cost_bases="",
            direct_cad_m3_row_count=0,
            net_value_usable="no",
            conversion_requirement="logging and general truck haul are required",
            missing_reason=BC_NET_VALUE_REASON,
            source_artifact=relative_path(BC_APPRAISAL_DIR / "README.md"),
            notes=(
                "Deliberately not computed. Do not subtract partial allowances from a log "
                "price and call the result net value."
            ),
        ),
    ]
    return tuple(rows)


def parse_bcts_ledger(
    path: Path,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, list[dict[str, str]]], int]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, BCTS_LEDGER_COLUMNS)
    if len(raw_rows) != BCTS_LEDGER_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {BCTS_LEDGER_ROW_COUNT} BCTS ledger rows, found {len(raw_rows)}"
        )
    crown: dict[str, list[dict[str, str]]] = defaultdict(list)
    forest_sector: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        if row["jurisdiction"] != "BC":
            raise ForestEconomicsV2DError(f"unexpected jurisdiction for {context}")
        if row["unit"] != "CAD_per_m3_log":
            raise ForestEconomicsV2DError(f"unexpected unit for {context}: {row['unit']!r}")
        if row["value_basis"] != "observed_sale:standing":
            raise ForestEconomicsV2DError(f"unexpected value basis for {context}")
        if row["year"] != "2026":
            raise ForestEconomicsV2DError(f"unexpected BCTS year for {context}: {row['year']!r}")
        if row["perspective"] not in {"crown", "forest_sector"}:
            raise ForestEconomicsV2DError(f"unexpected perspective for {context}")
        sale_id = row["spatial_unit"]
        if row["perspective"] == "crown":
            crown[sale_id].append(row)
        else:
            forest_sector[sale_id].append(row)
    if set(crown) != set(forest_sector):
        raise ForestEconomicsV2DError("BCTS crown and forest-sector sale sets differ")
    return dict(crown), dict(forest_sector), len(raw_rows)


def parse_bcts_results(path: Path) -> dict[str, dict[str, str]]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, BCTS_RESULT_COLUMNS)
    results: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(raw_rows, start=2):
        licence_id = row["licence_id"]
        if not licence_id:
            raise ForestEconomicsV2DError(f"blank licence id at {path.name}:{line_number}")
        if licence_id in results:
            raise ForestEconomicsV2DError(f"duplicate BCTS result for {licence_id}")
        results[licence_id] = row
    return results


def build_bcts_sales(
    crown: Mapping[str, Sequence[Mapping[str, str]]],
    forest_sector: Mapping[str, Sequence[Mapping[str, str]]],
    results: Mapping[str, Mapping[str, str]],
) -> tuple[tuple[BctsSale, ...], StumpageStats]:
    sales: list[BctsSale] = []
    for sale_id in sorted(crown):
        crown_rows = crown[sale_id]
        mirror_rows = forest_sector[sale_id]
        if len(crown_rows) != len(mirror_rows):
            raise ForestEconomicsV2DError(f"unpaired BCTS rows for {sale_id}")
        volumes = {
            parse_finite_float(row["volume_m3"], context=sale_id, minimum=0.0)
            for row in crown_rows
        }
        if len(volumes) != 1:
            raise ForestEconomicsV2DError(f"inconsistent BCTS volume within {sale_id}")
        volume = next(iter(volumes))

        base_values: list[float] = []
        premium_values: list[float] = []
        for row in crown_rows:
            value = parse_finite_float(row["value_cad"], context=sale_id, minimum=0.0)
            if row["component"] == "stumpage_base":
                base_values.append(value)
            elif row["component"] == "stumpage_bid_premium":
                premium_values.append(value)
            else:
                raise ForestEconomicsV2DError(
                    f"unexpected BCTS component for {sale_id}: {row['component']}"
                )
            if row["sign"] not in {"1", "-1"}:
                raise ForestEconomicsV2DError(f"unexpected BCTS sign for {sale_id}")
        if len(base_values) > 1 or len(premium_values) > 1:
            raise ForestEconomicsV2DError(f"duplicate BCTS component rows for {sale_id}")
        if not premium_values and not base_values:
            raise ForestEconomicsV2DError(f"BCTS sale has no stumpage components: {sale_id}")

        crown_by_component = {row["component"]: row for row in crown_rows}
        mirror_by_component = {row["component"]: row for row in mirror_rows}
        if set(crown_by_component) != set(mirror_by_component):
            raise ForestEconomicsV2DError(f"BCTS mirror component set mismatch for {sale_id}")
        for component, crown_row in crown_by_component.items():
            mirror_row = mirror_by_component[component]
            if crown_row["value_cad"] != mirror_row["value_cad"]:
                raise ForestEconomicsV2DError(f"BCTS mirror value mismatch for {sale_id}")
            if int(crown_row["sign"]) != -int(mirror_row["sign"]):
                raise ForestEconomicsV2DError(f"BCTS mirror sign mismatch for {sale_id}")

        licence_id = sale_id.replace("BC-BCTS_", "")
        result = results.get(licence_id)
        if result is None:
            raise ForestEconomicsV2DError(f"missing BCTS result row for {licence_id}")
        if result["status"] != "AWARDED":
            raise ForestEconomicsV2DError(
                f"BCTS ledger sale is not awarded in results: {licence_id}"
            )
        winning_bid = parse_optional_float(
            result["winning_bid_cad_m3"], context=f"{licence_id} winning bid"
        )
        upset = parse_optional_float(
            result["upset_cad_m3"], context=f"{licence_id} upset"
        )
        bonus = parse_optional_float(
            result["bonus_cad_m3"], context=f"{licence_id} bonus"
        )
        if bonus is None:
            raise ForestEconomicsV2DError(f"BCTS result has no bonus for {licence_id}")
        base = base_values[0] if base_values else None
        premium = premium_values[0] if premium_values else 0.0
        total = (base or 0.0) + premium
        if base is None:
            if winning_bid is not None or upset is not None:
                raise ForestEconomicsV2DError(
                    f"BCTS missing-base sale unexpectedly reports a full bid: {licence_id}"
                )
            if not math.isclose(premium, bonus, abs_tol=RATE_TOLERANCE):
                raise ForestEconomicsV2DError(
                    f"BCTS premium-only lower bound does not match bonus for {licence_id}"
                )
            total_basis = "premium_only_lower_bound"
        else:
            if winning_bid is None:
                raise ForestEconomicsV2DError(
                    f"BCTS full sale is missing the reported winning bid: {licence_id}"
                )
            if not math.isclose(base, upset or 0.0, abs_tol=RATE_TOLERANCE):
                raise ForestEconomicsV2DError(
                    f"BCTS upset base mismatch for {licence_id}: {base} vs {upset}"
                )
            if not math.isclose(total, winning_bid, abs_tol=RATE_TOLERANCE * 10):
                raise ForestEconomicsV2DError(
                    f"BCTS winning bid mismatch for {licence_id}: {total} vs {winning_bid}"
                )
            total_basis = "full_winning_bid"
        sales.append(
            BctsSale(
                sale_id=sale_id,
                auction_date=result["auction_date"],
                location=result["location"],
                volume_m3=volume,
                stumpage_base_cad_m3=base,
                stumpage_bid_premium_cad_m3=premium,
                stumpage_total_cad_m3=total,
                total_basis=total_basis,
                reported_winning_bid_cad_m3=winning_bid,
                reported_upset_cad_m3=upset,
                reported_bonus_cad_m3=bonus,
                bidder_count=parse_optional_int(
                    result["bidders"], context=f"{licence_id} bidders"
                ),
            )
        )

    if len(sales) != BCTS_SALE_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {BCTS_SALE_COUNT} observed BCTS sales, found {len(sales)}"
        )
    full = [sale for sale in sales if sale.total_basis == "full_winning_bid"]
    lower = [sale for sale in sales if sale.total_basis == "premium_only_lower_bound"]
    if len(full) != 49 or len(lower) != 2:
        raise ForestEconomicsV2DError(
            f"expected 49 full and 2 lower-bound BCTS sales, found {len(full)} and {len(lower)}"
        )
    full_volume = sum(sale.volume_m3 for sale in full)
    full_value = sum(sale.stumpage_total_cad_m3 * sale.volume_m3 for sale in full)
    stats = StumpageStats(
        sale_count=len(sales),
        full_bid_count=len(full),
        lower_bound_count=len(lower),
        volume_m3=sum(sale.volume_m3 for sale in sales),
        full_bid_volume_m3=full_volume,
        volume_weighted_stumpage_cad_m3=full_value / full_volume,
        median_full_bid_cad_m3=statistics.median(
            sale.stumpage_total_cad_m3 for sale in full
        ),
        min_full_bid_cad_m3=min(sale.stumpage_total_cad_m3 for sale in full),
        max_full_bid_cad_m3=max(sale.stumpage_total_cad_m3 for sale in full),
        min_observed_cad_m3=min(sale.stumpage_total_cad_m3 for sale in sales),
        max_observed_cad_m3=max(sale.stumpage_total_cad_m3 for sale in sales),
    )
    return tuple(sales), stats


BMMB_REFERENCE_FIELDS = (
    "reference_id",
    "reference_type",
    "source_artifact",
    "source_record_key",
    "jurisdiction",
    "validation_status",
    "validation_detail",
) + BMMB_SOURCE_FIELDS


def require_not_net_value_row(row: Mapping[str, str], *, context: str) -> None:
    if row["policy_grade"] != POLICY_GRADE:
        raise ForestEconomicsV2DError(
            f"{context} must carry policy_grade=false, got {row['policy_grade']!r}"
        )
    if row["net_value_status"] != NET_VALUE_STATUS:
        raise ForestEconomicsV2DError(
            f"{context} must carry net_value_status=not_estimated, "
            f"got {row['net_value_status']!r}"
        )


def copy_source_row(
    row: Mapping[str, str],
    *,
    fields: Sequence[str],
    reference_id: str,
    reference_type: str,
    source_artifact: Path,
    source_record_key: str,
    validation_detail: str,
) -> dict[str, str]:
    copied = {field: row.get(field, "") for field in fields}
    copied.update(
        {
            "reference_id": reference_id,
            "reference_type": reference_type,
            "source_artifact": relative_path(source_artifact),
            "source_record_key": source_record_key,
            "jurisdiction": "QC",
            "validation_status": "validated_copy",
            "validation_detail": validation_detail,
        }
    )
    return copied


def build_qc_reference(
    procurement_dir: Path,
) -> tuple[tuple[dict[str, str], ...], dict[str, Any]]:
    manifest_path = procurement_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ForestEconomicsV2DError(f"missing procurement manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dataset") != "qc_procurement_cost_benchmark":
        raise ForestEconomicsV2DError("procurement manifest dataset changed")
    if manifest.get("status") != NET_VALUE_STATUS:
        raise ForestEconomicsV2DError("procurement manifest status is not not_estimated")
    labels = manifest.get("labels", {})
    if labels.get("policy_grade") is not False:
        raise ForestEconomicsV2DError("procurement manifest is not policy_grade=false")
    if labels.get("net_value_status") != NET_VALUE_STATUS:
        raise ForestEconomicsV2DError("procurement manifest net-value status changed")
    if labels.get("interpretation") != PROCUREMENT_INTERPRETATION:
        raise ForestEconomicsV2DError("procurement manifest interpretation changed")
    if manifest.get("validation", {}).get("status") != "pass":
        raise ForestEconomicsV2DError("procurement manifest validation is not pass")
    verified_outputs = verify_manifest_outputs(manifest_path, procurement_dir)

    provincial_path = procurement_dir / "qc_procurement_cost_benchmark.csv"
    regional_path = procurement_dir / "qc_procurement_regional_range.csv"
    bmmb_path = procurement_dir / "bmmb_historical_benchmark.csv"

    provincial_rows = read_csv_rows(provincial_path)
    require_columns(provincial_path, provincial_rows, QC_PROVINCIAL_COLUMNS)
    regional_rows = read_csv_rows(regional_path)
    require_columns(regional_path, regional_rows, QC_REGIONAL_COLUMNS)
    bmmb_rows = read_csv_rows(bmmb_path)
    require_columns(bmmb_path, bmmb_rows, QC_BMMB_COLUMNS)

    if len(provincial_rows) != QC_PROVINCIAL_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {QC_PROVINCIAL_ROW_COUNT} provincial procurement rows, "
            f"found {len(provincial_rows)}"
        )
    if len(regional_rows) != QC_REGIONAL_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {QC_REGIONAL_ROW_COUNT} regional procurement rows, "
            f"found {len(regional_rows)}"
        )
    if len(bmmb_rows) != QC_BMMB_SOURCE_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {QC_BMMB_SOURCE_ROW_COUNT} BMMB source rows, "
            f"found {len(bmmb_rows)}"
        )

    provincial_by_id = {row["benchmark_id"]: row for row in provincial_rows}
    if len(provincial_by_id) != len(provincial_rows):
        raise ForestEconomicsV2DError("duplicate provincial procurement benchmark id")

    sep = provincial_by_id.get("qc_2022_sepm_total")
    if sep is None:
        raise ForestEconomicsV2DError("missing QC SEPM procurement row")
    expected_sep = {
        "purchase_cost_cad_m3": 107.22,
        "bgad_procurement_before_transfers_cad_m3": 81.40,
        "transfers_total_cad_m3": 25.01,
        "bgad_procurement_after_transfers_cad_m3": 106.41,
        "cost_spread_before_transfers_cad_m3": 25.82,
        "cost_spread_after_transfers_cad_m3": 0.81,
    }
    for field, expected in expected_sep.items():
        actual = parse_finite_float(sep[field], context=f"QC SEPM {field}")
        if not math.isclose(actual, expected, abs_tol=RATE_TOLERANCE):
            raise ForestEconomicsV2DError(
                f"QC SEPM {field} changed: {actual} vs {expected}"
            )

    reference_rows: list[dict[str, str]] = []
    for row in provincial_rows:
        context = f"QC provincial {row['benchmark_id']}"
        require_not_net_value_row(row, context=context)
        if row["evidence"] != EVIDENCE_SURVEY:
            raise ForestEconomicsV2DError(f"{context} evidence label changed")
        if row["interpretation"] != PROCUREMENT_INTERPRETATION:
            raise ForestEconomicsV2DError(f"{context} interpretation changed")
        if row["purchase_semantics"] != "buyer_side_procurement_cost_not_log_sale_revenue":
            raise ForestEconomicsV2DError(f"{context} purchase semantics changed")
        purchase = parse_finite_float(
            row["purchase_cost_cad_m3"], context=f"{context} purchase"
        )
        if row["spread_status"] == "computed":
            before = parse_finite_float(
                row["bgad_procurement_before_transfers_cad_m3"],
                context=f"{context} BGAD before",
            )
            transfers = parse_finite_float(
                row["transfers_total_cad_m3"], context=f"{context} transfers"
            )
            after = parse_finite_float(
                row["bgad_procurement_after_transfers_cad_m3"],
                context=f"{context} BGAD after",
            )
            spread_before = parse_finite_float(
                row["cost_spread_before_transfers_cad_m3"],
                context=f"{context} spread before",
            )
            spread_after = parse_finite_float(
                row["cost_spread_after_transfers_cad_m3"],
                context=f"{context} spread after",
            )
            if not math.isclose(before + transfers, after, abs_tol=ARITHMETIC_TOLERANCE):
                raise ForestEconomicsV2DError(f"{context} BGAD identity mismatch")
            if not math.isclose(
                purchase - before, spread_before, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(f"{context} spread-before mismatch")
            if not math.isclose(
                purchase - after, spread_after, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(f"{context} spread-after mismatch")
            if not math.isclose(
                spread_before - spread_after, transfers, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(f"{context} transfer identity mismatch")
        else:
            if row["spread_status"] != "not_computable_missing_bgad_synthesis":
                raise ForestEconomicsV2DError(
                    f"{context} has unexpected spread status {row['spread_status']!r}"
                )
            for field in (
                "bgad_procurement_before_transfers_cad_m3",
                "transfers_total_cad_m3",
                "bgad_procurement_after_transfers_cad_m3",
                "cost_spread_before_transfers_cad_m3",
                "cost_spread_after_transfers_cad_m3",
            ):
                if row[field].strip() != "":
                    raise ForestEconomicsV2DError(
                        f"{context} is not computable but carries {field}"
                    )
        reference_rows.append(
            copy_source_row(
                row,
                fields=QC_PROCUREMENT_SOURCE_FIELDS,
                reference_id=f"qc_ref_{row['benchmark_id']}",
                reference_type="provincial_procurement_cost",
                source_artifact=provincial_path,
                source_record_key=row["benchmark_id"],
                validation_detail=(
                    "Exact source fields copied; procurement-cost labels, spread "
                    "identities, and the no-revenue semantics were rechecked."
                ),
            )
        )

    regional_values = [
        row for row in regional_rows if row["statistic"] == "region_value"
    ]
    if len(regional_values) != QC_REGIONAL_VALUE_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {QC_REGIONAL_VALUE_COUNT} regional values, "
            f"found {len(regional_values)}"
        )
    reference_purchase = parse_finite_float(
        regional_values[0]["reference_purchase_cad_m3"],
        context="QC regional reference purchase",
    )
    if not math.isclose(reference_purchase, 107.22, abs_tol=RATE_TOLERANCE):
        raise ForestEconomicsV2DError("QC regional reference purchase changed")
    for row in regional_rows:
        context = f"QC regional {row['regional_benchmark_id']}"
        require_not_net_value_row(row, context=context)
        if row["evidence"] != EVIDENCE_SURVEY_REGIONAL:
            raise ForestEconomicsV2DError(f"{context} evidence label changed")
        if row["interpretation"] != PROCUREMENT_INTERPRETATION:
            raise ForestEconomicsV2DError(f"{context} interpretation changed")
        if row["spread_price_assumption"] != (
            "provincial_sepm_purchase_cost_held_constant"
        ):
            raise ForestEconomicsV2DError(f"{context} purchase assumption changed")
        purchase = parse_finite_float(
            row["reference_purchase_cad_m3"], context=f"{context} purchase"
        )
        if not math.isclose(purchase, reference_purchase, abs_tol=RATE_TOLERANCE):
            raise ForestEconomicsV2DError(f"{context} reference purchase mismatch")
        before = parse_finite_float(
            row["bgad_procurement_before_transfers_cad_m3"],
            context=f"{context} BGAD before",
        )
        transfers = parse_finite_float(
            row["transfers_total_cad_m3"], context=f"{context} transfers"
        )
        after = parse_finite_float(
            row["bgad_procurement_after_transfers_cad_m3"],
            context=f"{context} BGAD after",
        )
        spread_before = parse_finite_float(
            row["conditional_cost_spread_before_transfers_cad_m3"],
            context=f"{context} spread before",
        )
        spread_after = parse_finite_float(
            row["conditional_cost_spread_after_transfers_cad_m3"],
            context=f"{context} spread after",
        )
        if row["statistic"] == "region_value":
            if not math.isclose(
                before + transfers, after, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(f"{context} BGAD identity mismatch")
            if not math.isclose(
                purchase - before, spread_before, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(f"{context} conditional spread mismatch")
            if not math.isclose(
                purchase - after, spread_after, abs_tol=ARITHMETIC_TOLERANCE
            ):
                raise ForestEconomicsV2DError(
                    f"{context} conditional after-spread mismatch"
                )
            sample_n = row["sample_n"].strip()
            if sample_n and int(sample_n) <= 3 and row["three_respondent_flag"] != "true":
                raise ForestEconomicsV2DError(
                    f"{context} has small sample without three-respondent flag"
                )
        else:
            if row["columns_are_independent_extrema"] != "true":
                raise ForestEconomicsV2DError(
                    f"{context} bound row does not declare independent extrema"
                )
            if row["three_respondent_flag"] != "true":
                raise ForestEconomicsV2DError(
                    f"{context} bound row lost the three-respondent flag"
                )
        reference_rows.append(
            copy_source_row(
                row,
                fields=QC_PROCUREMENT_SOURCE_FIELDS,
                reference_id=f"qc_ref_{row['regional_benchmark_id']}",
                reference_type="regional_conditional_cost_spread",
                source_artifact=regional_path,
                source_record_key=row["regional_benchmark_id"],
                validation_detail=(
                    "Exact source fields copied; conditional spread rechecked against "
                    "the provincial SEPM purchase cost. Bound rows are independent "
                    "column-wise extrema, not additive ranges."
                ),
            )
        )

    if len(reference_rows) != QC_REFERENCE_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {QC_REFERENCE_ROW_COUNT} QC procurement references, "
            f"found {len(reference_rows)}"
        )

    regional_before = [
        parse_finite_float(
            row["conditional_cost_spread_before_transfers_cad_m3"],
            context=row["regional_benchmark_id"],
        )
        for row in regional_values
    ]
    regional_after = [
        parse_finite_float(
            row["conditional_cost_spread_after_transfers_cad_m3"],
            context=row["regional_benchmark_id"],
        )
        for row in regional_values
    ]
    summary = {
        "source_artifact": relative_path(procurement_dir),
        "manifest_validation": "pass",
        "verified_outputs": verified_outputs,
        "provincial_rows": len(provincial_rows),
        "regional_rows": len(regional_rows),
        "regional_value_rows": len(regional_values),
        "computed_spread_rows": sum(
            1 for row in provincial_rows if row["spread_status"] == "computed"
        ),
        "not_computable_spread_rows": sum(
            1
            for row in provincial_rows
            if row["spread_status"] != "computed"
        ),
        "reference_purchase_cad_m3": rounded(reference_purchase),
        "regional_conditional_cost_spread_before_range_cad_m3": [
            rounded(min(regional_before)),
            rounded(max(regional_before)),
        ],
        "regional_conditional_cost_spread_after_range_cad_m3": [
            rounded(min(regional_after)),
            rounded(max(regional_after)),
        ],
        "sepm_purchase_cost_cad_m3": rounded(
            parse_finite_float(sep["purchase_cost_cad_m3"], context="QC SEPM purchase")
        ),
        "sepm_cost_spread_before_transfers_cad_m3": rounded(
            parse_finite_float(
                sep["cost_spread_before_transfers_cad_m3"],
                context="QC SEPM spread before",
            )
        ),
        "sepm_cost_spread_after_transfers_cad_m3": rounded(
            parse_finite_float(
                sep["cost_spread_after_transfers_cad_m3"],
                context="QC SEPM spread after",
            )
        ),
        "net_value_status": NET_VALUE_STATUS,
        "interpretation": PROCUREMENT_INTERPRETATION,
    }
    return tuple(reference_rows), summary


def build_bmmb_reference(
    procurement_dir: Path,
) -> tuple[tuple[dict[str, str], ...], dict[str, Any]]:
    path = procurement_dir / "bmmb_historical_benchmark.csv"
    rows = read_csv_rows(path)
    require_columns(path, rows, QC_BMMB_COLUMNS)
    all_years = [row for row in rows if row["aggregation_level"] == "delivered_all_years"]
    if len(all_years) != BMMB_REFERENCE_ROW_COUNT:
        raise ForestEconomicsV2DError(
            f"expected {BMMB_REFERENCE_ROW_COUNT} BMMB all-year rows, "
            f"found {len(all_years)}"
        )
    by_id = {row["benchmark_id"]: row for row in all_years}
    expected = {
        "bmmb_crown_public_forest_2011_2015": {
            "scope_id": "crown_public_forest",
            "tenure_scope": "crown_only",
            "is_primary_benchmark": "true",
            "full_rate_sale_count": "9",
            "rate_computable_volume_m3": 74550.0,
            "weighted_rate_cad_m3": 35.7547,
        },
        "bmmb_teaching_research_forest_2014_2015": {
            "scope_id": "teaching_research_forest",
            "tenure_scope": "teaching_only",
            "is_primary_benchmark": "false",
            "full_rate_sale_count": "2",
            "rate_computable_volume_m3": 15350.0,
            "weighted_rate_cad_m3": 50.6266,
        },
        "bmmb_all_delivered_2011_2015": {
            "scope_id": "all_delivered",
            "tenure_scope": "mixed",
            "is_primary_benchmark": "false",
            "full_rate_sale_count": "11",
            "rate_computable_volume_m3": 89900.0,
            "weighted_rate_cad_m3": 38.2940,
        },
    }
    if set(by_id) != set(expected):
        raise ForestEconomicsV2DError("BMMB all-year benchmark ids changed")
    output_rows: list[dict[str, str]] = []
    for benchmark_id, checks in expected.items():
        row = by_id[benchmark_id]
        context = f"BMMB {benchmark_id}"
        require_not_net_value_row(row, context=context)
        if row["evidence"] != EVIDENCE_HISTORICAL:
            raise ForestEconomicsV2DError(f"{context} evidence label changed")
        if row["currency_basis"] != "CAD_nominal":
            raise ForestEconomicsV2DError(f"{context} is not nominal CAD")
        if row["deflator_applied"] != "false":
            raise ForestEconomicsV2DError(f"{context} carries a deflator")
        if row["grade_split"] != "false" or row["species_allocation"] != "false":
            raise ForestEconomicsV2DError(f"{context} is not a pooled delivered rate")
        for field, expected_value in checks.items():
            if isinstance(expected_value, float):
                actual = parse_finite_float(row[field], context=f"{context} {field}")
                if not math.isclose(
                    actual, expected_value, abs_tol=RATE_TOLERANCE
                ):
                    raise ForestEconomicsV2DError(
                        f"{context} {field} changed: {actual} vs {expected_value}"
                    )
            elif row[field] != expected_value:
                raise ForestEconomicsV2DError(
                    f"{context} {field} changed: {row[field]!r} vs {expected_value!r}"
                )
        output_rows.append(
            copy_source_row(
                row,
                fields=BMMB_SOURCE_FIELDS,
                reference_id=f"qc_ref_{benchmark_id}",
                reference_type="historical_delivered_sale_benchmark",
                source_artifact=path,
                source_record_key=benchmark_id,
                validation_detail=(
                    "Exact source fields copied; nominal historical delivered-sale "
                    "benchmark only, with no cost stack and no net value."
                ),
            )
        )
    summary = {
        "source_artifact": relative_path(path),
        "row_count": len(output_rows),
        "primary_benchmark_id": "bmmb_crown_public_forest_2011_2015",
        "primary_scope": "crown_only",
        "primary_weighted_rate_cad_m3": 35.7547,
        "teaching_weighted_rate_cad_m3": 50.6266,
        "all_in_secondary_weighted_rate_cad_m3": 38.2940,
        "currency_basis": "CAD_nominal",
        "deflator_applied": False,
        "grade_split": False,
        "species_allocation": False,
        "net_value_status": NET_VALUE_STATUS,
    }
    return tuple(output_rows), summary


def build_summary_rows(
    qc_rows: Sequence[Mapping[str, str]],
    bmmb_rows: Sequence[Mapping[str, str]],
    series_summaries: Sequence[SeriesPriceSummary],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
) -> tuple[dict[str, Any], ...]:
    qc_by_key = {row["source_record_key"]: row for row in qc_rows}
    bmmb_by_key = {row["source_record_key"]: row for row in bmmb_rows}
    rows: list[dict[str, Any]] = []

    def base_row(
        *,
        summary_id: str,
        jurisdiction: str,
        evidence_tier: str,
        revenue_basis: str,
        revenue_measure: str,
        cost_coverage: str,
        cost_coverage_detail: str,
        net_value_reason: str,
        source_artifact: str,
        notes: str,
        revenue_range: PriceRange | None = None,
        revenue_unit: str = "CAD/m3",
        revenue_year_basis: str = "",
    ) -> dict[str, Any]:
        return {
            "summary_id": summary_id,
            "jurisdiction": jurisdiction,
            "evidence_tier": evidence_tier,
            "revenue_basis": revenue_basis,
            "revenue_measure": revenue_measure,
            "revenue_min_cad_m3": revenue_range.minimum if revenue_range else None,
            "revenue_p25_cad_m3": revenue_range.p25 if revenue_range else None,
            "revenue_median_cad_m3": revenue_range.median if revenue_range else None,
            "revenue_p75_cad_m3": revenue_range.p75 if revenue_range else None,
            "revenue_max_cad_m3": revenue_range.maximum if revenue_range else None,
            "revenue_unit": revenue_unit,
            "revenue_year_basis": revenue_year_basis,
            "cost_coverage": cost_coverage,
            "cost_coverage_detail": cost_coverage_detail,
            "net_value_status": NET_VALUE_STATUS,
            "net_value_reason": net_value_reason,
            "net_value_is_combined_national": False,
            "policy_grade": False,
            "is_additive": False,
            "source_artifact": source_artifact,
            "notes": notes,
        }

    sep = qc_by_key["qc_2022_sepm_total"]
    rows.append(
        base_row(
            summary_id="qc_procurement_sepm_total",
            jurisdiction="QC",
            evidence_tier=EVIDENCE_SURVEY,
            revenue_basis="not_revenue_buyer_side_procurement_cost",
            revenue_measure="none",
            cost_coverage="full_survey_procurement_stack",
            cost_coverage_detail=(
                "T20 BGAD harvest, road, haul, hosting, other costs and transfers; "
                "a different respondent sample from the T2 purchase cost"
            ),
            net_value_reason=QC_NET_VALUE_REASON,
            source_artifact=sep["source_artifact"],
            notes=(
                "T2 purchase cost 107.22 CAD/m3 is a buyer-side cost, not revenue. "
                "BGAD before/transfers/after are 81.40/25.01/106.41. The 25.82 and "
                "0.81 CAD/m3 differences are procurement-channel cost spreads, not "
                "net value."
            ),
        )
    )
    regional = qc_by_key["qc_2022_regional_sepm_R01-R03-R11-R12"]
    rows.append(
        base_row(
            summary_id="qc_procurement_regional_sepm_conditional",
            jurisdiction="QC",
            evidence_tier=EVIDENCE_SURVEY_REGIONAL,
            revenue_basis="not_revenue_buyer_side_procurement_cost",
            revenue_measure="none",
            cost_coverage="regional_sepm_procurement_only",
            cost_coverage_detail=(
                "seven regional SEPM BGAD groups; no regional purchase cost is "
                "published, so the provincial 107.22 purchase cost is held constant"
            ),
            net_value_reason=QC_REGIONAL_REASON,
            source_artifact=regional["source_artifact"],
            notes=(
                "Conditional regional cost-spread range only; regional purchase "
                "cost, regional revenue, and regional net value are not observed."
            ),
        )
    )

    for benchmark_id, summary_id in (
        ("bmmb_crown_public_forest_2011_2015", "qc_bmmb_crown_primary"),
        ("bmmb_teaching_research_forest_2014_2015", "qc_bmmb_teaching"),
        ("bmmb_all_delivered_2011_2015", "qc_bmmb_all_in_secondary"),
    ):
        source = bmmb_by_key[benchmark_id]
        parse_finite_float(
            source["weighted_rate_cad_m3"], context=f"{benchmark_id} weighted rate"
        )
        median = parse_finite_float(
            source["median_rate_cad_m3"], context=f"{benchmark_id} median rate"
        )
        minimum = parse_finite_float(
            source["min_rate_cad_m3"], context=f"{benchmark_id} min rate"
        )
        maximum = parse_finite_float(
            source["max_rate_cad_m3"], context=f"{benchmark_id} max rate"
        )
        rows.append(
            base_row(
                summary_id=summary_id,
                jurisdiction="QC",
                evidence_tier=EVIDENCE_HISTORICAL,
                revenue_basis="delivered_to_destination_auction",
                revenue_measure="bmmb_delivered_sale_rate",
                cost_coverage="revenue_only_no_cost_stack",
                cost_coverage_detail=(
                    f"{source['sale_count']} delivered sales; "
                    f"{source['full_rate_sale_count']} full rates; "
                    "no species, grade, or cost detail"
                ),
                net_value_reason=HISTORICAL_NET_VALUE_REASON,
                source_artifact=source["source_artifact"],
                notes=(
                    f"Nominal {source['price_year']} BMMB delivered-sale benchmark. "
                    + (
                        "Crown public forest only; primary benchmark."
                        if benchmark_id == "bmmb_crown_public_forest_2011_2015"
                        else "Teaching/research forest lots; not the primary benchmark."
                        if benchmark_id == "bmmb_teaching_research_forest_2014_2015"
                        else "All-in pooled rate mixes Crown and teaching lots; secondary benchmark."
                    )
                ),
                revenue_range=PriceRange(minimum, None, median, None, maximum),
                revenue_year_basis=source["price_year_basis"],
            )
        )

    for summary in series_summaries:
        rows.append(
            base_row(
                summary_id=f"bc_price_{summary.region}_{summary.report_basis}_{summary.log_class}",
                jurisdiction="BC",
                evidence_tier=EVIDENCE_OBSERVED_PRICE,
                revenue_basis="mill_delivered_published_average",
                revenue_measure="all_reported_grades_or_end_uses_and_species",
                cost_coverage="partial_appraisal_allowance_missing_logging_and_haul",
                cost_coverage_detail=(
                    "road, bridge, culvert, silviculture, specified operations, and "
                    "TOA allowances exist; general logging and truck haul are missing"
                ),
                net_value_reason=BC_NET_VALUE_REASON,
                source_artifact=relative_path(
                    BC_LOG_MARKET_DIR / "bc_log_market_prices.csv"
                ),
                notes=(
                    "Cross-grade/cross-species observed average range for this "
                    "published vintage; 1_month and 3_month are separate and not "
                    "annualized. Not a transaction and not revenue net of costs."
                ),
                revenue_range=summary.price_range,
                revenue_year_basis=f"nominal_{summary.period_label}",
            )
        )

    rows.append(
        base_row(
            summary_id="bc_bcts_observed_stumpage",
            jurisdiction="BC",
            evidence_tier=EVIDENCE_OBSERVED_STUMPAGE,
            revenue_basis="standing_timber_stumpage_not_delivered_revenue",
            revenue_measure="bcts_winning_bid",
            cost_coverage="partial_appraisal_allowance_missing_logging_and_haul",
            cost_coverage_detail=(
                "51 awarded BCTS sales; 49 full winning bids and 2 premium-only "
                "lower bounds; stumpage is standing timber"
            ),
            net_value_reason=BC_NET_VALUE_REASON,
            source_artifact=relative_path(BCTS_DIR / "bcts_bid_ledger.csv"),
            notes=(
                "Observed standing-timber stumpage sample. A bid is not "
                "delivered-log revenue, and no BC net value is estimated."
            ),
            revenue_range=PriceRange(
                stumpage_stats.min_observed_cad_m3,
                None,
                stumpage_stats.median_full_bid_cad_m3,
                None,
                stumpage_stats.max_observed_cad_m3,
            ),
            revenue_unit="CAD/m3_log",
            revenue_year_basis="nominal_2026",
        )
    )

    covered = [row for row in cost_rows if row.covered]
    rows.append(
        base_row(
            summary_id="bc_appraisal_cost_coverage",
            jurisdiction="BC",
            evidence_tier=EVIDENCE_APPRAISAL,
            revenue_basis="not_applicable",
            revenue_measure="none",
            cost_coverage="partial_appraisal_allowance_missing_logging_and_haul",
            cost_coverage_detail=(
                f"{len(covered)} covered appraisal component families; logging, "
                "general truck haul, and EWB are not published as costs"
            ),
            net_value_reason=BC_NET_VALUE_REASON,
            source_artifact=relative_path(
                BC_APPRAISAL_DIR / "bc_appraisal_costs.csv"
            ),
            notes=(
                "Allowances are appraisal inputs, not observed contractor costs. "
                "Units differ and no total $/m3 is computed."
            ),
        )
    )
    rows.append(
        base_row(
            summary_id="bc_full_net_value",
            jurisdiction="BC",
            evidence_tier=EVIDENCE_NOT_AVAILABLE,
            revenue_basis="not_estimated",
            revenue_measure="none",
            cost_coverage="missing_logging_and_haul",
            cost_coverage_detail=(
                "general logging/tree-to-truck and general truck haul are missing "
                "from public appraisal tables and are embedded in the EWB"
            ),
            net_value_reason=BC_NET_VALUE_REASON,
            source_artifact=relative_path(BC_APPRAISAL_DIR / "README.md"),
            notes=(
                "Deliberately not computed. Do not subtract road, silviculture, or "
                "any other partial allowance from a log price and call it net value."
            ),
        )
    )
    return tuple(rows)


def add_check(
    checks: list[dict[str, Any]], name: str, passed: bool, detail: str
) -> None:
    checks.append(
        {"check": name, "status": "pass" if passed else "fail", "detail": detail}
    )


def validate_outputs(
    *,
    raw_price_aggregate_tokens: Sequence[str],
    price_cells: Sequence[PriceCell],
    price_summaries: Sequence[PriceGroupSummary],
    price_rows: Sequence[Mapping[str, Any]],
    bmmb_rows: Sequence[Mapping[str, str]],
    stumpage_sales: Sequence[BctsSale],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
    qc_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    checks: list[dict[str, Any]] = []

    add_check(
        checks,
        "procurement_manifest_labels",
        all(
            row["policy_grade"] == POLICY_GRADE
            and row["net_value_status"] == NET_VALUE_STATUS
            and row["interpretation"] == PROCUREMENT_INTERPRETATION
            for row in qc_rows
        ),
        "every QC procurement reference is not_estimated, policy_grade=false, and a cost spread",
    )
    add_check(
        checks,
        "qc_reference_row_count",
        len(qc_rows) == QC_REFERENCE_ROW_COUNT,
        f"{len(qc_rows)} references (9 provincial + 9 regional)",
    )
    sep_reference = next(
        row for row in qc_rows if row["source_record_key"] == "qc_2022_sepm_total"
    )
    expected_sep = {
        "purchase_cost_cad_m3": 107.22,
        "bgad_procurement_before_transfers_cad_m3": 81.40,
        "transfers_total_cad_m3": 25.01,
        "bgad_procurement_after_transfers_cad_m3": 106.41,
        "cost_spread_before_transfers_cad_m3": 25.82,
        "cost_spread_after_transfers_cad_m3": 0.81,
    }
    add_check(
        checks,
        "qc_sepm_values_match_procurement_benchmark",
        all(
            math.isclose(
                parse_finite_float(sep_reference[field], context=f"SEPM {field}"),
                expected,
                abs_tol=RATE_TOLERANCE,
            )
            for field, expected in expected_sep.items()
        ),
        "SEPM 107.22 / 81.40 / 25.01 / 106.41 / 25.82 / 0.81 match the source",
    )
    add_check(
        checks,
        "qc_no_withdrawn_wording",
        all(
            "residual" not in field.lower()
            and "residual" not in str(value).lower()
            for row in qc_rows
            for field, value in row.items()
        ),
        "QC references carry no withdrawn legacy framing terms",
    )
    add_check(
        checks,
        "bmmb_reference_row_count",
        len(bmmb_rows) == BMMB_REFERENCE_ROW_COUNT,
        f"{len(bmmb_rows)} all-year BMMB references",
    )
    add_check(
        checks,
        "bmmb_primary_teaching_secondary",
        {
            row["source_record_key"]: row["weighted_rate_cad_m3"]
            for row in bmmb_rows
        }
        == {
            "bmmb_crown_public_forest_2011_2015": "35.7547",
            "bmmb_teaching_research_forest_2014_2015": "50.6266",
            "bmmb_all_delivered_2011_2015": "38.2940",
        },
        "Crown-only 35.7547 primary, teaching 50.6266, all-in 38.2940 secondary",
    )
    add_check(
        checks,
        "bmmb_nominal_no_net_value",
        all(
            row["currency_basis"] == "CAD_nominal"
            and row["deflator_applied"] == "false"
            and row["policy_grade"] == POLICY_GRADE
            and row["net_value_status"] == NET_VALUE_STATUS
            for row in bmmb_rows
        ),
        "BMMB is nominal historical revenue with no cost stack and no net value",
    )

    add_check(
        checks,
        "bc_price_cell_count",
        len(price_cells) == BC_PRICE_ROW_COUNT,
        f"{len(price_cells)} cells (expected {BC_PRICE_ROW_COUNT})",
    )
    report_ids = {cell.report_id for cell in price_cells}
    add_check(
        checks,
        "bc_price_report_count",
        len(report_ids) == BC_REPORT_COUNT,
        f"{len(report_ids)} reports (expected {BC_REPORT_COUNT})",
    )
    add_check(
        checks,
        "bc_price_hard_range_and_nonnegative",
        all(
            BC_PRICE_MIN_CAD_M3 <= cell.price_cad_m3 <= BC_PRICE_MAX_CAD_M3
            for cell in price_cells
        ),
        "all prices are within 0-2000 CAD/m3",
    )
    zero_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "zero_cad_m3"
    )
    below_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "below_1_cad_m3"
    )
    above_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "above_1000_cad_m3"
    )
    add_check(
        checks,
        "bc_price_flag_counts",
        (zero_count, below_count, above_count) == (16, 7, 17),
        f"zero={zero_count}, below_1={below_count}, above_1000={above_count}",
    )
    add_check(
        checks,
        "bc_price_is_aggregate_lowercase",
        set(raw_price_aggregate_tokens) == {"true", "false"}
        and raw_price_aggregate_tokens.count("true") == 890
        and raw_price_aggregate_tokens.count("false") == 14815,
        "source is_aggregate tokens are lowercase true/false with 890 aggregates",
    )
    add_check(
        checks,
        "bc_price_benchmark_id_unique",
        len({row["benchmark_id"] for row in price_rows}) == len(price_rows),
        "every annual/period price row has a unique benchmark_id",
    )
    add_check(
        checks,
        "bc_price_flagged_cells_excluded_from_ranges",
        all(
            summary.price_range.minimum is not None
            and 1.0 <= summary.price_range.minimum
            and summary.price_range.maximum is not None
            and summary.price_range.maximum <= 1000.0
            for summary in price_summaries
            if summary.ok_cell_count
        ),
        "every ok range stays within the plausible 1.0-1000.0 CAD/m3 band",
    )
    add_check(
        checks,
        "bc_price_product_cells_exclude_aggregate",
        all(
            not row["is_aggregate"] and row["end_use"] != "weighted_average"
            for row in price_rows
            if row["cell_role"] == "product_cell"
        ),
        "no is_aggregate=true row appears as a product cell",
    )
    add_check(
        checks,
        "bc_price_no_double_count",
        sum(1 for row in price_rows if row["is_aggregate"]) == 104
        and sum(1 for row in price_rows if not row["is_aggregate"]) == 1748
        and len(price_rows) == 1852,
        "1,748 product-cell rows and 104 aggregate-reference rows are separate",
    )
    add_check(
        checks,
        "bc_price_no_vintage_mixing",
        all(
            row["report_basis"] in {"1_month", "3_month"}
            and "not additive" in row["vintage_handling"]
            for row in price_rows
        ),
        "each range has one published vintage and is marked non-additive",
    )
    annual_groups = [
        summary
        for summary in price_summaries
        if summary.key.aggregation_level == "annual"
    ]
    add_check(
        checks,
        "bc_price_annual_report_limit",
        all(summary.report_count <= 12 for summary in annual_groups),
        "at most 12 published reports per series-year; three-month windows still overlap",
    )
    cell_keys = {
        (cell.report_id, cell.species, cell.grade, cell.end_use) for cell in price_cells
    }
    add_check(
        checks,
        "bc_price_cell_key_unique",
        len(cell_keys) == len(price_cells),
        f"{len(cell_keys)} unique report/species/grade/end-use keys",
    )
    aggregate_cells = [cell for cell in price_cells if cell.is_aggregate]
    add_check(
        checks,
        "bc_interior_aggregate_rows",
        len(aggregate_cells) == 890
        and all(cell.region == "interior" for cell in aggregate_cells),
        f"{len(aggregate_cells)} Interior weighted-average cells kept outside product sums",
    )
    product_prices = {
        (cell.report_id, cell.species, cell.end_use): cell.price_cad_m3
        for cell in price_cells
        if cell.region == "interior" and not cell.is_aggregate
    }
    aggregate_violations = 0
    for cell in aggregate_cells:
        sibling_prices = [
            price
            for (report_id, species, _), price in product_prices.items()
            if report_id == cell.report_id and species == cell.species
        ]
        if sibling_prices and not (
            min(sibling_prices) - 0.02
            <= cell.price_cad_m3
            <= max(sibling_prices) + 0.02
        ):
            aggregate_violations += 1
    add_check(
        checks,
        "bc_interior_aggregate_within_product_range",
        aggregate_violations == 0,
        f"{len(aggregate_cells)} aggregate cells checked against product siblings",
    )
    volume_by_report: dict[str, float] = defaultdict(float)
    printed_volume: dict[str, float] = {}
    for cell in price_cells:
        if cell.region != "interior":
            continue
        if cell.report_total_volume_m3 is not None:
            printed_volume[cell.report_id] = cell.report_total_volume_m3
        if not cell.is_aggregate and cell.volume_m3 is not None:
            volume_by_report[cell.report_id] += cell.volume_m3
    volume_violations = [
        report_id
        for report_id, printed in printed_volume.items()
        if abs(volume_by_report.get(report_id, 0.0) - printed) > VOLUME_TOLERANCE
    ]
    add_check(
        checks,
        "bc_interior_volume_sum_excludes_aggregate",
        not volume_violations and len(printed_volume) == 169,
        f"{len(printed_volume)} Interior reports reconciled; {len(volume_violations)} violations",
    )

    add_check(
        checks,
        "bc_stumpage_sale_count",
        stumpage_stats.sale_count == BCTS_SALE_COUNT,
        f"{stumpage_stats.sale_count} awarded sales (expected {BCTS_SALE_COUNT})",
    )
    add_check(
        checks,
        "bc_stumpage_full_and_lower_bound_split",
        stumpage_stats.full_bid_count == 49
        and stumpage_stats.lower_bound_count == 2,
        f"full={stumpage_stats.full_bid_count}, lower_bound={stumpage_stats.lower_bound_count}",
    )
    add_check(
        checks,
        "bc_stumpage_nonnegative_and_labeled",
        all(
            sale.stumpage_total_cad_m3 >= 0
            and sale.total_basis
            in {"full_winning_bid", "premium_only_lower_bound"}
            for sale in stumpage_sales
        ),
        "all observed stumpage totals are nonnegative and carry a basis label",
    )

    add_check(
        checks,
        "bc_cost_row_count",
        sum(
            row.row_count
            for row in cost_rows
            if row.component != "full_bc_net_value"
        )
        == BC_APPRAISAL_ROW_COUNT,
        f"{sum(row.row_count for row in cost_rows if row.component != 'full_bc_net_value')} "
        f"appraisal rows assigned (expected {BC_APPRAISAL_ROW_COUNT})",
    )
    add_check(
        checks,
        "bc_cost_appraisal_vs_observed",
        all(
            row.evidence_tier in {EVIDENCE_APPRAISAL, EVIDENCE_NOT_AVAILABLE}
            for row in cost_rows
        )
        and all(not row.observed_cost for row in cost_rows),
        "all cost rows are appraisal allowances or explicitly unavailable; none is observed cost",
    )
    missing_components = {
        row.component
        for row in cost_rows
        if row.component in {"logging", "truck_haul", "estimated_winning_bid"}
    }
    add_check(
        checks,
        "bc_cost_missing_components",
        missing_components == {"logging", "truck_haul", "estimated_winning_bid"}
        and any(row.component == "logging" and row.missing_reason for row in cost_rows),
        "logging, general truck haul, and EWB are explicitly missing",
    )
    add_check(
        checks,
        "bc_cost_no_partial_net_value",
        any(
            row.component == "full_bc_net_value"
            and row.coverage_status == NET_VALUE_STATUS
            and row.missing_reason == BC_NET_VALUE_REASON
            for row in cost_rows
        )
        and all(
            row.net_value_usable != "yes" for row in cost_rows
        ),
        "full BC net value is explicitly not estimated; no component is net-value usable",
    )

    add_check(
        checks,
        "summary_all_not_estimated",
        all(
            row["net_value_status"] == NET_VALUE_STATUS
            and row["net_value_reason"]
            for row in summary_rows
        ),
        f"{len(summary_rows)} summary rows all not_estimated with a reason",
    )
    add_check(
        checks,
        "summary_policy_grade_false",
        all(row["policy_grade"] is False for row in summary_rows),
        "policy_grade=false on every summary row",
    )
    add_check(
        checks,
        "summary_no_combined_national_net_value",
        all(
            row["net_value_is_combined_national"] is False
            and row["jurisdiction"] in {"QC", "BC"}
            for row in summary_rows
        ),
        "no national combined net-value row exists",
    )
    add_check(
        checks,
        "summary_no_net_value_amounts",
        all(
            field
            not in {
                "net_value_before_transfers_min_cad_m3",
                "net_value_before_transfers_max_cad_m3",
                "net_value_after_transfers_min_cad_m3",
                "net_value_after_transfers_max_cad_m3",
                "net_value_cad_m3",
                "residual_cad_m3",
            }
            for row in summary_rows
            for field in row
        ),
        "summary rows have no monetary net-value field",
    )
    add_check(
        checks,
        "summary_qc_revenue_not_reported",
        all(
            row["revenue_min_cad_m3"] is None
            and row["revenue_max_cad_m3"] is None
            for row in summary_rows
            if row["jurisdiction"] == "QC"
            and row["evidence_tier"] in {EVIDENCE_SURVEY, EVIDENCE_SURVEY_REGIONAL}
        ),
        "QC procurement rows do not present buyer-side cost as revenue",
    )
    add_check(
        checks,
        "summary_bc_revenue_nonnegative",
        all(
            row["revenue_min_cad_m3"] is None or row["revenue_min_cad_m3"] >= 0
            for row in summary_rows
        ),
        "no negative BC revenue or stumpage range",
    )
    add_check(
        checks,
        "summary_evidence_tiers_separated",
        {row["evidence_tier"] for row in summary_rows} <= EVIDENCE_TIERS
        and {
            row["evidence_tier"]
            for row in summary_rows
            if row["jurisdiction"] == "BC"
        }
        >= {
            EVIDENCE_OBSERVED_PRICE,
            EVIDENCE_OBSERVED_STUMPAGE,
            EVIDENCE_APPRAISAL,
            EVIDENCE_NOT_AVAILABLE,
        },
        "QC cost, historical, BC price, stumpage, allowance, and unavailable rows are distinct",
    )

    cost_records = tuple(asdict(row) for row in cost_rows)
    add_check(
        checks,
        "no_nonfinite_output_text",
        all(
            value is None
            or str(value).strip().lower() not in {"nan", "inf", "+inf", "-inf"}
            for rows in (summary_rows, price_rows, qc_rows, bmmb_rows, cost_records)
            for row in rows
            for value in row.values()
        ),
        "no NaN or infinity text in generated rows",
    )
    return tuple(checks)


def write_report(
    path: Path,
    *,
    generated_utc: str,
    summary_rows: Sequence[Mapping[str, Any]],
    series_summaries: Sequence[SeriesPriceSummary],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
    qc_rows: Sequence[Mapping[str, str]],
    bmmb_rows: Sequence[Mapping[str, str]],
    qc_summary: Mapping[str, Any],
    bmmb_summary: Mapping[str, Any],
    checks: Sequence[Mapping[str, Any]],
) -> None:
    def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
        lines = [
            "| " + " | ".join(headers) + " |",
            "|" + "|".join("---" for _ in headers) + "|",
        ]
        for row in rows:
            lines.append("| " + " | ".join(format_cell(value) for value in row) + " |")
        return "\n".join(lines)

    def find_qc(key: str) -> Mapping[str, str]:
        return next(row for row in qc_rows if row["source_record_key"] == key)

    sep = find_qc("qc_2022_sepm_total")
    hardwood = find_qc("qc_2022_feuillus_durs_sciage")
    poplar = find_qc("qc_2022_peupliers_trituration")
    provincial_table = table(
        [
            "Scope",
            "Purchase",
            "BGAD before",
            "Transfers",
            "BGAD after",
            "Spread before",
            "Spread after",
            "Status",
        ],
        [
            [
                "SEPM total",
                sep["purchase_cost_cad_m3"],
                sep["bgad_procurement_before_transfers_cad_m3"],
                sep["transfers_total_cad_m3"],
                sep["bgad_procurement_after_transfers_cad_m3"],
                sep["cost_spread_before_transfers_cad_m3"],
                sep["cost_spread_after_transfers_cad_m3"],
                sep["spread_status"],
            ],
            [
                "Hardwood sciage",
                hardwood["purchase_cost_cad_m3"],
                hardwood["bgad_procurement_before_transfers_cad_m3"],
                hardwood["transfers_total_cad_m3"],
                hardwood["bgad_procurement_after_transfers_cad_m3"],
                hardwood["cost_spread_before_transfers_cad_m3"],
                hardwood["cost_spread_after_transfers_cad_m3"],
                hardwood["spread_status"],
            ],
            [
                "Poplar trituration",
                poplar["purchase_cost_cad_m3"],
                poplar["bgad_procurement_before_transfers_cad_m3"],
                poplar["transfers_total_cad_m3"],
                poplar["bgad_procurement_after_transfers_cad_m3"],
                poplar["cost_spread_before_transfers_cad_m3"],
                poplar["cost_spread_after_transfers_cad_m3"],
                poplar["spread_status"],
            ],
        ],
    )
    bmmb_table = table(
        [
            "Scope",
            "Role",
            "Full rates",
            "Rate volume (m3)",
            "Weighted",
            "Median",
            "Min",
            "Max",
            "Basis",
        ],
        [
            [
                row["scope_id"],
                "primary"
                if row["is_primary_benchmark"] == "true"
                else "secondary"
                if row["scope_id"] == "all_delivered"
                else "separate",
                row["full_rate_sale_count"],
                row["rate_computable_volume_m3"],
                row["weighted_rate_cad_m3"],
                row["median_rate_cad_m3"],
                row["min_rate_cad_m3"],
                row["max_rate_cad_m3"],
                row["currency_basis"],
            ]
            for row in bmmb_rows
        ],
    )
    series_table = table(
        [
            "Region",
            "Vintage",
            "Log class",
            "Cells",
            "Reports",
            "Plausible",
            "Flagged",
            "Min",
            "P25",
            "Median",
            "P75",
            "Max",
        ],
        [
            [
                summary.region,
                summary.report_basis,
                summary.log_class,
                summary.cell_count,
                summary.report_count,
                summary.ok_cell_count,
                summary.flagged_cell_count,
                summary.price_range.minimum,
                summary.price_range.p25,
                summary.price_range.median,
                summary.price_range.p75,
                summary.price_range.maximum,
            ]
            for summary in series_summaries
        ],
    )
    cost_table = table(
        [
            "Component",
            "Coverage",
            "Rows",
            "Bases",
            "Direct CAD/m3 rows",
            "Usable",
            "Missing reason",
        ],
        [
            [
                row.component,
                row.coverage_status,
                row.row_count,
                row.cost_bases,
                row.direct_cad_m3_row_count,
                row.net_value_usable,
                row.missing_reason,
            ]
            for row in cost_rows
        ],
    )
    stumpage_table = table(
        ["Measure", "Value"],
        [
            ["Awarded sales", stumpage_stats.sale_count],
            ["Full winning-bid sales", stumpage_stats.full_bid_count],
            ["Premium-only lower bounds", stumpage_stats.lower_bound_count],
            ["Sale volume (m3)", stumpage_stats.volume_m3],
            ["Full-bid volume (m3)", stumpage_stats.full_bid_volume_m3],
            [
                "Volume-weighted stumpage (CAD/m3)",
                stumpage_stats.volume_weighted_stumpage_cad_m3,
            ],
            ["Median full bid (CAD/m3)", stumpage_stats.median_full_bid_cad_m3],
            [
                "Full-bid range (CAD/m3)",
                (
                    f"{stumpage_stats.min_full_bid_cad_m3:.4f} to "
                    f"{stumpage_stats.max_full_bid_cad_m3:.4f}"
                ),
            ],
        ],
    )
    status_table = table(
        [
            "Jurisdiction",
            "Evidence",
            "Revenue measure",
            "Cost coverage",
            "Net value",
            "Reason",
        ],
        [
            [
                row["jurisdiction"],
                row["evidence_tier"],
                row["revenue_measure"],
                row["cost_coverage"],
                row["net_value_status"],
                row["net_value_reason"],
            ]
            for row in summary_rows
        ],
    )
    validation_table = table(
        ["Check", "Status", "Detail"],
        [[check["check"], check["status"], check["detail"]] for check in checks],
    )

    report = f"""# Forest Economics Evidence v2d

| Field | Value |
|---|---|
| Document | `data/derived/national/harvest_basis/forest_economics_v2d/FOREST_ECONOMICS_V2D_REPORT.md` |
| Date | {generated_utc[:10]} |
| Status | Evidence layer. No merged-ledger, model, UI, or public OSeCAN change. |
| QC source | `data/derived/national/harvest_basis/procurement_benchmark/` |
| BC sources | `bc_log_market/`, `bcts_bc/`, `bc_appraisal_costs/` |
| Net value | QC and BC both `not_estimated` |

**Decision summary.** Neither jurisdiction has a defensible net value. QC
`107.22` is a buyer-side procurement cost, not revenue; `25.82` and `0.81`
CAD/m3 are procurement-channel cost spreads. BC has observed mill-delivered
price averages, observed BCTS standing-timber stumpage, and partial appraisal
allowances, but no public general logging (tree-to-truck) or truck-haul $/m3.
BC `net_value_status=not_estimated` with reason `missing_logging_and_haul_cost`.
No BC partial net value is computed and no combined national net value exists.

## 1. Evidence classes

| Evidence tier | What it is | What it is not |
|---|---|---|
| `survey_procurement_cost` | QC 2022 T2 purchase cost vs T20 BGAD cost | Not revenue, not a transaction, not net value |
| `survey_regional_procurement_cost` | QC 2022 regional conditional cost spread | Not an observed regional price or net value |
| `benchmark_historical` | BMMB 2011-2015 delivered-sale rate, nominal | Not a current price, not a cost stack, not net value |
| `observed_price` | BC published mill-delivered average | Not a transaction, not a complete population |
| `observed_stumpage` | BC BCTS winning bid for standing timber | Not delivered-log revenue |
| `appraisal_allowance` | BC IAM/CAM tabular allowance | Not an observed contractor cost or payment |
| `not_available` | Missing logging, truck haul, or full net value | Not zero |

## 2. QC procurement reference

The QC rows are exact validated copies of the procurement benchmark. The
T2/T20 difference is a **cost spread**, not revenue and not net value.

{provincial_table}

Regional conditional range: {qc_summary["regional_conditional_cost_spread_before_range_cad_m3"][0]} to
{qc_summary["regional_conditional_cost_spread_before_range_cad_m3"][1]} CAD/m3 before
transfers and {qc_summary["regional_conditional_cost_spread_after_range_cad_m3"][0]} to
{qc_summary["regional_conditional_cost_spread_after_range_cad_m3"][1]} CAD/m3 after
transfers, holding the provincial SEPM purchase cost constant. Bound rows are
independent column-wise extrema and are not additive ranges.

## 3. BMMB historical delivered-sale reference

{bmmb_table}

The Crown-only row is primary. Teaching/research is separate. The all-in row is
secondary and mixes Crown and teaching lots. All values are nominal 2011-2015
delivered-sale rates with no deflation, grade split, species allocation, or cost
stack. They are not stumpage and not net value.

## 4. BC revenue evidence

{series_table}

Blank source cells are missing, not zero. The 16 exact-zero, 7 below CAD 1/m3,
and 17 above CAD 1,000/m3 cells are retained with flags and excluded from the
plausible ranges; they are not clipped or filled. Interior `weighted_average`
rows (`is_aggregate=true`) are reported separately and never added to product
cells. One-month and three-month vintages are separate; neither is annualized,
and overlapping three-month windows are not summed. Coast old growth is a
published blend of old and mixed growth.

### 4.1 BCTS observed stumpage

{stumpage_table}

Stumpage is a bid for standing timber, not delivered-log revenue, and is kept
separate from the mill-delivered price averages.

## 5. BC cost coverage

{cost_table}

All 879 cost rows are appraisal allowances, not observed contractor costs. The
covered components use different units (CAD/km, CAD/each, CAD/ha, CAD/m3) and
cannot be summed into one $/m3 without road length, structure counts, treated
area, and harvest volume.

**Stop rule.** A number computed as `log price - road allowance - silviculture
allowance` (or any other subset of the published allowances) is **not** net
value. It omits general logging and truck haul, mixes units without site
quantities, and treats appraisal allowances as observed costs.

## 6. Summary statuses

{status_table}

## 7. Validation

All {len(checks)} checks passed:

{validation_table}

## 8. Non-claims

- Not an observed transaction net value.
- Not policy-grade; `policy_grade=false` on every summary row.
- Not a BC full net value and not a BC partial net value.
- Not a combined national net value; QC and BC monetary totals are never added.
- Not a mill margin; transformation and by-product revenue are excluded.
- Not a QC stumpage rate and not a BCTS delivered-log price.
- Not an observed contractor cost; appraisal allowances are policy inputs.
- Not merged into `forest_cost_revenue.csv`, the Forest LP, the UI, or OSeCAN.
- Not deflated; years and currency bases remain separate.
- Not a legal AAC, sustainable-supply estimate, or forecast.

## 9. What would unlock a real net value

1. A public or authorized general logging/tree-to-truck $/m3, general truck-haul
   $/m3, and a documented decomposition of the EWB into cost components.
2. Site quantities to convert allowances: road km, bridge and culvert counts,
   treated hectares, and harvest volume by appraisal zone.
3. A region/grade/volume weighting for the log-price series and a cost stack from
   a matching vintage.
4. For QC, an observed log-sale revenue series at the same basis and vintage as
   the cost stack; purchase cost alone cannot supply revenue.

## 10. Reproduction

```bash
.venv/bin/python -m workflows.national.harvest.forest_economics_v2d
.venv/bin/python -m pytest tests/test_forest_economics_v2d.py -q
.venv/bin/ruff check workflows/national/harvest/forest_economics_v2d.py tests/test_forest_economics_v2d.py
```

Outputs: `{SUMMARY_CSV}`, `{QC_REFERENCE_CSV}`, `{BC_REVENUE_CSV}`,
`{BC_COST_CSV}`, `{BMMB_REFERENCE_CSV}`, `{SUMMARY_JSON}`, `{MANIFEST_JSON}`.
"""
    path.write_text(report, encoding="utf-8")


def build(output_dir: Path = DEFAULT_OUTPUT_DIR) -> BuildResult:
    output_dir.mkdir(parents=True, exist_ok=True)

    price_path = BC_LOG_MARKET_DIR / "bc_log_market_prices.csv"
    raw_price_rows = read_csv_rows(price_path)
    raw_price_aggregate_tokens = [row["is_aggregate"] for row in raw_price_rows]
    price_cells = parse_price_cells(price_path, rows=raw_price_rows)
    price_summaries = build_price_groups(price_cells)
    series_summaries = build_series_summaries(price_cells)
    price_rows = tuple(price_row_from_summary(summary) for summary in price_summaries)

    appraisal_costs = parse_appraisal_costs(BC_APPRAISAL_DIR / "bc_appraisal_costs.csv")
    appraisal_coverage = parse_appraisal_coverage(
        BC_APPRAISAL_DIR / "bc_appraisal_coverage.csv"
    )
    appraisal_manifest = json.loads(
        (BC_APPRAISAL_DIR / "manifest.json").read_text(encoding="utf-8")
    )
    if appraisal_manifest.get("row_counts", {}).get("total") != BC_APPRAISAL_ROW_COUNT:
        raise ForestEconomicsV2DError("appraisal manifest row count changed")
    if appraisal_manifest.get("stats", {}).get("rows") != BC_APPRAISAL_ROW_COUNT:
        raise ForestEconomicsV2DError("appraisal manifest stats row count changed")
    if appraisal_manifest.get("observation_type") != EVIDENCE_APPRAISAL:
        raise ForestEconomicsV2DError("appraisal manifest observation type changed")
    if not all(
        spot.get("status") == "pass"
        for spot in appraisal_manifest.get("spot_checks", [])
    ):
        raise ForestEconomicsV2DError("appraisal spot checks are not all pass")
    cost_rows = build_cost_coverage(
        appraisal_costs, appraisal_coverage, appraisal_manifest
    )

    crown_rows, forest_sector_rows, ledger_row_count = parse_bcts_ledger(
        BCTS_DIR / "bcts_bid_ledger.csv"
    )
    if ledger_row_count != BCTS_LEDGER_ROW_COUNT:
        raise ForestEconomicsV2DError("BCTS ledger row count changed")
    bcts_results = parse_bcts_results(BCTS_DIR / "bcts_bid_results.csv")
    stumpage_sales, stumpage_stats = build_bcts_sales(
        crown_rows,
        forest_sector_rows,
        bcts_results,
    )

    qc_rows, qc_summary = build_qc_reference(PROCUREMENT_DIR)
    bmmb_rows, bmmb_summary = build_bmmb_reference(PROCUREMENT_DIR)
    summary_rows = build_summary_rows(
        qc_rows, bmmb_rows, series_summaries, stumpage_stats, cost_rows
    )

    checks = validate_outputs(
        raw_price_aggregate_tokens=raw_price_aggregate_tokens,
        price_cells=price_cells,
        price_summaries=price_summaries,
        price_rows=price_rows,
        bmmb_rows=bmmb_rows,
        stumpage_sales=stumpage_sales,
        stumpage_stats=stumpage_stats,
        cost_rows=cost_rows,
        qc_rows=qc_rows,
        summary_rows=summary_rows,
    )
    failed = [check for check in checks if check["status"] != "pass"]
    if failed:
        raise ForestEconomicsV2DError(f"validation failed: {failed}")

    summary_fields = (
        "summary_id",
        "jurisdiction",
        "evidence_tier",
        "revenue_basis",
        "revenue_measure",
        "revenue_min_cad_m3",
        "revenue_p25_cad_m3",
        "revenue_median_cad_m3",
        "revenue_p75_cad_m3",
        "revenue_max_cad_m3",
        "revenue_unit",
        "revenue_year_basis",
        "cost_coverage",
        "cost_coverage_detail",
        "net_value_status",
        "net_value_reason",
        "net_value_is_combined_national",
        "policy_grade",
        "is_additive",
        "source_artifact",
        "notes",
    )
    price_fields = tuple(price_rows[0]) if price_rows else ()
    cost_fields = (
        "coverage_id",
        "component",
        "component_group",
        "coverage_status",
        "covered",
        "evidence_tier",
        "appraisal_allowance",
        "observed_cost",
        "row_count",
        "coast_row_count",
        "interior_row_count",
        "cost_bases",
        "direct_cad_m3_row_count",
        "net_value_usable",
        "conversion_requirement",
        "missing_reason",
        "source_artifact",
        "notes",
    )
    cost_records = tuple(
        {
            "coverage_id": row.coverage_id,
            "component": row.component,
            "component_group": row.component_group,
            "coverage_status": row.coverage_status,
            "covered": row.covered,
            "evidence_tier": row.evidence_tier,
            "appraisal_allowance": row.appraisal_allowance,
            "observed_cost": row.observed_cost,
            "row_count": row.row_count,
            "coast_row_count": row.coast_row_count,
            "interior_row_count": row.interior_row_count,
            "cost_bases": row.cost_bases,
            "direct_cad_m3_row_count": row.direct_cad_m3_row_count,
            "net_value_usable": row.net_value_usable,
            "conversion_requirement": row.conversion_requirement,
            "missing_reason": row.missing_reason,
            "source_artifact": row.source_artifact,
            "notes": row.notes,
        }
        for row in cost_rows
    )

    write_csv(output_dir / SUMMARY_CSV, summary_fields, summary_rows)
    write_csv(output_dir / QC_REFERENCE_CSV, QC_REFERENCE_FIELDS, qc_rows)
    write_csv(output_dir / BC_REVENUE_CSV, price_fields, price_rows)
    write_csv(output_dir / BC_COST_CSV, cost_fields, cost_records)
    write_csv(output_dir / BMMB_REFERENCE_CSV, BMMB_REFERENCE_FIELDS, bmmb_rows)

    output_files = (
        (SUMMARY_CSV, len(summary_rows), "jurisdiction summary with separated evidence tiers"),
        (QC_REFERENCE_CSV, len(qc_rows), "validated QC procurement cost references"),
        (BC_REVENUE_CSV, len(price_rows), "BC annual and period price ranges"),
        (BC_COST_CSV, len(cost_rows), "BC appraisal allowance coverage and gaps"),
        (BMMB_REFERENCE_CSV, len(bmmb_rows), "BMMB nominal historical delivered-sale reference"),
    )
    output_records = [
        file_record(output_dir / name, role=role, rows=rows)
        for name, rows, role in output_files
    ]

    input_records = [
        file_record(PROCUREMENT_DIR / "qc_procurement_cost_benchmark.csv", role="QC procurement cost benchmark"),
        file_record(PROCUREMENT_DIR / "qc_procurement_regional_range.csv", role="QC regional procurement cost range"),
        file_record(PROCUREMENT_DIR / "bmmb_historical_benchmark.csv", role="BMMB historical benchmark"),
        file_record(PROCUREMENT_DIR / "manifest.json", role="QC procurement provenance"),
        file_record(BC_LOG_MARKET_DIR / "bc_log_market_prices.csv", role="BC observed price cells"),
        file_record(BC_LOG_MARKET_DIR / "bc_log_market_coverage.csv", role="BC price cell coverage"),
        file_record(BC_LOG_MARKET_DIR / "manifest.json", role="BC price provenance"),
        file_record(BCTS_DIR / "bcts_bid_ledger.csv", role="BC observed BCTS stumpage ledger"),
        file_record(BCTS_DIR / "bcts_bid_results.csv", role="BC BCTS auction results"),
        file_record(BC_APPRAISAL_DIR / "bc_appraisal_costs.csv", role="BC appraisal allowances"),
        file_record(BC_APPRAISAL_DIR / "bc_appraisal_coverage.csv", role="BC appraisal coverage"),
        file_record(BC_APPRAISAL_DIR / "manifest.json", role="BC appraisal provenance"),
    ]

    price_series_summary = [
        {
            "region": summary.region,
            "report_basis": summary.report_basis,
            "log_class": summary.log_class,
            "cell_count": summary.cell_count,
            "report_count": summary.report_count,
            "ok_cell_count": summary.ok_cell_count,
            "flagged_cell_count": summary.flagged_cell_count,
            "price_min_cad_m3": rounded(summary.price_range.minimum),
            "price_p25_cad_m3": rounded(summary.price_range.p25),
            "price_median_cad_m3": rounded(summary.price_range.median),
            "price_p75_cad_m3": rounded(summary.price_range.p75),
            "price_max_cad_m3": rounded(summary.price_range.maximum),
        }
        for summary in series_summaries
    ]
    zero_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "zero_cad_m3"
    )
    below_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "below_1_cad_m3"
    )
    above_count = sum(
        1 for cell in price_cells if cell.price_plausibility == "above_1000_cad_m3"
    )

    summary_payload: dict[str, Any] = {
        "artifact_version": "1.0.0",
        "dataset": "forest_economics_v2d",
        "status": NET_VALUE_STATUS,
        "generated_utc": utc_now(),
        "scope": {
            "jurisdictions": ["QC", "BC"],
            "qc": "2022 MRNF survey procurement cost benchmark, referenced",
            "bc": "2019-2026 log-market averages, 2026 BCTS stumpage, 2026 appraisal allowances",
            "combined_national_net_value": False,
        },
        "definitions": {
            "observed_price": "BC published mill-delivered log-market average, CAD/m3; a reported average, not a transaction.",
            "observed_stumpage": "BC BCTS winning bid for standing timber, CAD/m3 log; not delivered-log revenue.",
            "appraisal_allowance": "BC IAM/CAM tabular cost allowance; not an observed contractor cost, winning bid, or payment.",
            "survey_procurement_cost": "QC T2 purchase cost and T20 BGAD cost; their difference is a procurement-channel cost spread, not revenue or net value.",
            "net_value": "Revenue minus a complete production cost stack at a consistent basis. No such stack exists for QC or BC here.",
            "bc_net_value_status": NET_VALUE_STATUS,
            "bc_net_value_reason": BC_NET_VALUE_REASON,
            "prohibited": "Do not subtract road/bridge/culvert/silviculture/specified-operation/TOA allowances from a log price and call it BC net value.",
        },
        "labels": {
            "evidence_tiers": sorted(EVIDENCE_TIERS),
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "net_value_is_combined_national": False,
            "partial_bc_net_value_computed": False,
            "qc_cost_presented_as_revenue": False,
            "no_zero_fill": True,
            "one_month_three_month_additive": False,
        },
        "qc_reference": qc_summary,
        "bmmb_reference": bmmb_summary,
        "bc_revenue": {
            "cell_count": len(price_cells),
            "report_count": len({cell.report_id for cell in price_cells}),
            "region_counts": {
                "coast": sum(1 for cell in price_cells if cell.region == "coast"),
                "interior": sum(1 for cell in price_cells if cell.region == "interior"),
            },
            "vintage_counts": {
                basis: sum(1 for cell in price_cells if cell.report_basis == basis)
                for basis in ("1_month", "3_month")
            },
            "flagged_counts": {
                "zero_cad_m3": zero_count,
                "below_1_cad_m3": below_count,
                "below_1_including_zero": sum(
                    1 for cell in price_cells if cell.price_cad_m3 < 1.0
                ),
                "above_1000_cad_m3": above_count,
            },
            "price_range_cad_m3": {
                "min_ok": min(
                    cell.price_cad_m3
                    for cell in price_cells
                    if cell.price_plausibility == "ok"
                ),
                "max_ok": max(
                    cell.price_cad_m3
                    for cell in price_cells
                    if cell.price_plausibility == "ok"
                ),
                "min_reported": min(cell.price_cad_m3 for cell in price_cells),
                "max_reported": max(cell.price_cad_m3 for cell in price_cells),
            },
            "series": price_series_summary,
            "interior_aggregate_cell_count": sum(
                1 for cell in price_cells if cell.is_aggregate
            ),
            "interior_volume_reconciled_reports": 169,
            "annualization_applied": False,
            "vintages_separate": True,
            "blank_cells_are_zero": False,
        },
        "bc_observed_stumpage": {
            "sale_count": stumpage_stats.sale_count,
            "full_bid_count": stumpage_stats.full_bid_count,
            "lower_bound_count": stumpage_stats.lower_bound_count,
            "volume_m3": rounded(stumpage_stats.volume_m3),
            "full_bid_volume_m3": rounded(stumpage_stats.full_bid_volume_m3),
            "volume_weighted_stumpage_cad_m3": rounded(
                stumpage_stats.volume_weighted_stumpage_cad_m3
            ),
            "median_full_bid_cad_m3": rounded(stumpage_stats.median_full_bid_cad_m3),
            "min_full_bid_cad_m3": rounded(stumpage_stats.min_full_bid_cad_m3),
            "max_full_bid_cad_m3": rounded(stumpage_stats.max_full_bid_cad_m3),
            "min_observed_cad_m3": rounded(stumpage_stats.min_observed_cad_m3),
            "max_observed_cad_m3": rounded(stumpage_stats.max_observed_cad_m3),
            "currency_basis": "CAD_nominal_2026",
            "price_basis": "standing_timber",
            "bid_is_not_delivered_revenue": True,
            "coverage": "selected_auction_sample",
            "ledger_row_count": ledger_row_count,
        },
        "bc_cost_coverage": {
            "appraisal_row_count": BC_APPRAISAL_ROW_COUNT,
            "covered_component_count": sum(1 for row in cost_rows if row.covered),
            "missing_components": [
                row.component
                for row in cost_rows
                if row.component
                in {"logging", "truck_haul", "estimated_winning_bid"}
            ],
            "components": [
                {
                    "component": row.component,
                    "coverage_status": row.coverage_status,
                    "covered": row.covered,
                    "evidence_tier": row.evidence_tier,
                    "appraisal_allowance": row.appraisal_allowance,
                    "observed_cost": row.observed_cost,
                    "row_count": row.row_count,
                    "cost_bases": row.cost_bases,
                    "direct_cad_m3_row_count": row.direct_cad_m3_row_count,
                    "net_value_usable": row.net_value_usable,
                    "missing_reason": row.missing_reason,
                }
                for row in cost_rows
            ],
            "full_net_value_possible": False,
            "total_cost_cad_m3_computed": False,
        },
        "net_value": {
            "qc_status": NET_VALUE_STATUS,
            "qc_reason": QC_NET_VALUE_REASON,
            "bc_status": NET_VALUE_STATUS,
            "bc_reason": BC_NET_VALUE_REASON,
            "combined_national": False,
            "combined_national_reason": COMBINED_NATIONAL_REASON,
            "partial_bc_computed": False,
        },
        "qc_bc_comparison": {
            "comparable": False,
            "reason": "Different jurisdiction, year, evidence tier, price point, aggregation, and cost coverage.",
            "qc": "buyer-side procurement cost and BGAD cost spreads, not revenue",
            "bc": "partial appraisal coverage and revenue observations, not_estimated",
            "combined_national_net_value": False,
        },
        "non_claims": [
            "Not an observed transaction net value.",
            "Not policy-grade; policy_grade=false on every summary row.",
            "Not a BC full net value and not a BC partial net value.",
            "Not a combined national net value; QC and BC totals are never added.",
            "Not a mill margin; transformation and by-product revenue are excluded.",
            "Not a QC stumpage rate and not a BCTS delivered-log price.",
            "Not an observed contractor cost; appraisal allowances are policy inputs.",
            "Not merged into the forest cost/revenue ledger, Forest LP, UI, or OSeCAN.",
            "Not deflated; years and currency bases remain separate.",
            "Not a legal AAC, sustainable-supply estimate, or forecast.",
        ],
        "limitations": [
            "BC log prices are published averages, not transactions; Coast grade volumes are unavailable.",
            "Coast old-growth is a published old/mixed-growth blend.",
            "One-month and three-month vintages are not reconciled; three-month windows overlap.",
            "Interior weighted-average rows are reported aggregates and are excluded from product-cell aggregation.",
            "Blank price and volume cells are missing, not zero.",
            "BCTS is a selected 2026 auction sample; two sales provide premium-only lower bounds.",
            "Appraisal allowances differ by effective date, zone, and unit; they are not observed costs.",
            "IAM/CAM publish no general logging or truck-haul $/m3; both are embedded in the EWB.",
            "No site quantities are available to convert CAD/km, CAD/each, or CAD/ha allowances to $/m3.",
            "QC purchase cost is not log-sale revenue.",
            "QC and BC evidence are not comparable and are not combined.",
        ],
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "inputs": input_records,
    }

    write_report(
        output_dir / REPORT_MD,
        generated_utc=summary_payload["generated_utc"],
        summary_rows=summary_rows,
        series_summaries=series_summaries,
        stumpage_stats=stumpage_stats,
        cost_rows=cost_rows,
        qc_rows=qc_rows,
        bmmb_rows=bmmb_rows,
        qc_summary=qc_summary,
        bmmb_summary=bmmb_summary,
        checks=checks,
    )
    report_record = file_record(
        output_dir / REPORT_MD, role="human-readable evidence report"
    )
    summary_payload["outputs"] = output_records + [report_record]
    summary_payload["manifest_path"] = relative_path(output_dir / MANIFEST_JSON)
    write_json(output_dir / SUMMARY_JSON, summary_payload)
    summary_record = file_record(output_dir / SUMMARY_JSON, role="summary payload")

    manifest_payload = {
        "artifact_version": "1.0.0",
        "dataset": "forest_economics_v2d",
        "status": NET_VALUE_STATUS,
        "generated_utc": summary_payload["generated_utc"],
        "generator": {
            "script": "workflows/national/harvest/forest_economics_v2d.py",
            "command": "python -m workflows.national.harvest.forest_economics_v2d",
        },
        "labels": summary_payload["labels"],
        "inputs": input_records,
        "outputs": output_records + [report_record, summary_record],
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "net_value": {
            "qc_status": NET_VALUE_STATUS,
            "qc_reason": QC_NET_VALUE_REASON,
            "bc_status": NET_VALUE_STATUS,
            "bc_reason": BC_NET_VALUE_REASON,
            "combined_national": False,
            "combined_national_reason": COMBINED_NATIONAL_REASON,
            "partial_bc_computed": False,
        },
    }
    write_json(output_dir / MANIFEST_JSON, manifest_payload)

    return BuildResult(
        output_dir=output_dir,
        summary_rows=tuple(summary_rows),
        price_rows=tuple(price_rows),
        cost_rows=tuple(cost_records),
        qc_rows=tuple(qc_rows),
        bmmb_rows=tuple(bmmb_rows),
        checks=tuple(checks),
        summary=summary_payload,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"output directory (default: {relative_path(DEFAULT_OUTPUT_DIR)})",
    )
    args = parser.parse_args(argv)
    result = build(output_dir=args.output_dir)
    print(f"wrote {len(result.summary_rows)} summary rows")
    print(f"wrote {len(result.qc_rows)} QC procurement reference rows")
    print(f"wrote {len(result.price_rows)} BC price range rows")
    print(f"wrote {len(result.cost_rows)} BC cost coverage rows")
    print(f"wrote {len(result.bmmb_rows)} BMMB reference rows")
    print(f"validation: {len(result.checks)} checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
