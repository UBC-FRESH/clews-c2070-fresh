"""DEPRECATED: build the old v2d net-value evidence layer.

This module is retained for audit only. Its output directory
``data/derived/national/harvest_basis/net_value_v2d/`` is deprecated. Use
``workflows.national.harvest.forest_economics_v2d`` and the
``forest_economics_v2d/`` layer instead: every summary row there is
``net_value_status=not_estimated``, ``policy_grade=false``, and
``net_value_is_combined_national=false``.

Original description follows.

The layer keeps evidence classes separate:

* ``survey_benchmark`` -- the validated QC 2022 MRNF residual benchmark;
* ``benchmark_historical`` -- the BMMB 2011-2015 delivered-sale reference;
* ``observed_price`` -- BC published mill-delivered log-market averages;
* ``observed_stumpage`` -- BC BCTS auction results for standing timber;
* ``appraisal_allowance`` -- BC IAM/CAM tabular cost allowances.

It does **not** compute a BC net value.  The IAM and CAM do not publish a
general logging (tree-to-truck) or truck-haul $/m3 allowance; those costs are
embedded in the estimated winning bid.  Subtracting only road, bridge,
culvert, silviculture, specified-operation, or TOA allowances from a log price
would omit logging and haul and is explicitly forbidden.

This module does not read or write the merged cost/revenue ledger, the Forest
LP, the UI, or any OSeCAN surface.  Every generated row is ``policy_grade=false``.
BC rows are ``net_value_status=not_estimated`` with
``net_value_reason=missing_logging_and_haul_cost``.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.net_value_v2d
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
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"

QC_BENCHMARK_DIR = HARVEST_BASIS_DIR / "net_value_benchmark"
BC_LOG_MARKET_DIR = HARVEST_BASIS_DIR / "bc_log_market"
BC_APPRAISAL_DIR = HARVEST_BASIS_DIR / "bc_appraisal_costs"
BCTS_DIR = HARVEST_BASIS_DIR / "bcts_bc"

DEFAULT_OUTPUT_DIR = HARVEST_BASIS_DIR / "net_value_v2d"

SUMMARY_CSV = "v2d_net_value_summary.csv"
BC_REVENUE_CSV = "bc_revenue_benchmark.csv"
BC_STUMPAGE_CSV = "bc_observed_stumpage.csv"
BC_COST_CSV = "bc_cost_coverage.csv"
QC_REFERENCE_CSV = "qc_reference.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NET_VALUE_V2D_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE_SURVEY = "survey_benchmark"
EVIDENCE_HISTORICAL = "benchmark_historical"
EVIDENCE_OBSERVED_PRICE = "observed_price"
EVIDENCE_OBSERVED_STUMPAGE = "observed_stumpage"
EVIDENCE_APPRAISAL = "appraisal_allowance"
EVIDENCE_NOT_AVAILABLE = "not_available"

EVIDENCE_TIERS = frozenset(
    {
        EVIDENCE_SURVEY,
        EVIDENCE_HISTORICAL,
        EVIDENCE_OBSERVED_PRICE,
        EVIDENCE_OBSERVED_STUMPAGE,
        EVIDENCE_APPRAISAL,
        EVIDENCE_NOT_AVAILABLE,
    }
)

BC_NET_VALUE_STATUS = "not_estimated"
BC_NET_VALUE_REASON = "missing_logging_and_haul_cost"
QC_NET_VALUE_STATUS = "benchmark_only"
HISTORICAL_NET_VALUE_STATUS = "benchmark_only"
POLICY_GRADE = "false"

BC_PRICE_MIN_CAD_M3 = 0.0
BC_PRICE_MAX_CAD_M3 = 2000.0
ARITHMETIC_TOLERANCE = 0.011
RATE_TOLERANCE = 0.0001
VOLUME_TOLERANCE = 2.0

BC_PRICE_ROW_COUNT = 15705
BC_REPORT_COUNT = 505
BC_APPRAISAL_ROW_COUNT = 879
BCTS_SALE_COUNT = 51
BCTS_LEDGER_ROW_COUNT = 200

QC_COMPUTED_BENCHMARK_IDS = (
    "qc_2022_qc_sepm_total",
    "qc_2022_qc_feuillus_durs_sciage",
    "qc_2022_qc_peupliers_trituration",
)
QC_REGIONAL_ROW_COUNT = 7
QC_SENSITIVITY_CASE_COUNT = 3

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
    "volume_status",
    "report_total_volume_m3",
    "report_totals_status",
    "source_document",
    "source_url",
    "source_sha256",
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
    "notes",
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
    "source",
    "year",
    "spatial_unit",
    "species",
    "product",
    "volume_m3",
    "unit",
    "value_cad",
    "value_basis",
    "perspective",
    "component",
    "sign",
    "confidence",
    "licence",
    "notes",
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
    "site_volume_m3",
    "results_final",
}

QC_PROVINCIAL_COLUMNS = {
    "benchmark_id",
    "region_code",
    "species_group",
    "product_or_grade",
    "delivered_purchase_cad_m3",
    "procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "procurement_after_transfers_cad_m3",
    "residual_before_transfers_cad_m3",
    "residual_after_transfers_cad_m3",
    "residual_status",
    "unit",
    "price_year",
    "currency_basis",
    "price_basis",
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
    "sample_n",
    "procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "procurement_after_transfers_cad_m3",
    "reference_purchase_cad_m3",
    "residual_before_transfers_cad_m3",
    "residual_after_transfers_cad_m3",
    "unit",
    "price_year",
    "currency_basis",
    "evidence",
    "policy_grade",
    "net_value_status",
    "is_additive",
    "notes",
}

QC_SENSITIVITY_COLUMNS = {
    "case_id",
    "qc_harvest_m3",
    "residual_before_transfers_cad_m3",
    "residual_after_transfers_cad_m3",
    "qc_net_value_before_transfers_mcad",
    "qc_net_value_after_transfers_mcad",
    "evidence",
    "policy_grade",
    "net_value_status",
}

QC_BMMB_COLUMNS = {
    "aggregation_level",
    "sale_year",
    "sale_count",
    "awarded_count",
    "full_rate_sale_count",
    "partial_award_sale_count",
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


class NetValueV2DError(RuntimeError):
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
    summary_rows: tuple[dict[str, str], ...]
    price_rows: tuple[dict[str, str], ...]
    stumpage_rows: tuple[dict[str, str], ...]
    cost_rows: tuple[dict[str, str], ...]
    qc_rows: tuple[dict[str, str], ...]
    checks: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise NetValueV2DError(f"missing input: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def require_columns(
    path: Path, rows: Sequence[Mapping[str, str]], required: Iterable[str]
) -> None:
    if not rows:
        raise NetValueV2DError(f"input has no data rows: {path}")
    missing = set(required) - set(rows[0])
    if missing:
        raise NetValueV2DError(f"{path} is missing columns: {sorted(missing)}")


def parse_finite_float(text: str, *, context: str, minimum: float | None = None) -> float:
    stripped = (text or "").strip()
    if stripped == "" or stripped.lower() in {"na", "n/a", "nan", "inf", "-inf", "none"}:
        raise NetValueV2DError(f"expected a finite number for {context}, got {text!r}")
    value = float(stripped)
    if not math.isfinite(value):
        raise NetValueV2DError(f"non-finite number for {context}: {text!r}")
    if minimum is not None and value < minimum:
        raise NetValueV2DError(f"value below {minimum} for {context}: {value}")
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
        raise NetValueV2DError(f"negative integer for {context}: {value}")
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
        raise NetValueV2DError(f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    outputs = manifest.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise NetValueV2DError(f"{manifest_path} has no output records")
    verified = 0
    for record in outputs:
        name = record.get("path")
        expected = record.get("sha256")
        if not name or not expected:
            raise NetValueV2DError(f"incomplete output record in {manifest_path}")
        path = directory / Path(name).name
        if not path.is_file():
            raise NetValueV2DError(f"manifest output is missing: {path}")
        if sha256_file(path) != expected:
            raise NetValueV2DError(f"manifest hash mismatch: {path}")
        verified += 1
    return verified


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NetValueV2DError(f"refusing to write non-finite value: {value}")
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


def parse_price_cells(path: Path) -> tuple[PriceCell, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, PRICE_CELL_COLUMNS)
    parsed: list[PriceCell] = []
    seen_keys: set[tuple[str, str, str, str]] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        region = row["region"]
        if region not in {"coast", "interior"}:
            raise NetValueV2DError(f"unexpected region for {context}: {region!r}")
        report_basis = row["report_basis"]
        if report_basis not in {"1_month", "3_month"}:
            raise NetValueV2DError(
                f"unexpected report basis for {context}: {report_basis!r}"
            )
        if row["price_basis"] != "mill_delivered":
            raise NetValueV2DError(f"unexpected price basis for {context}: {row['price_basis']!r}")
        if row["observation_type"] != "reported_market_average":
            raise NetValueV2DError(
                f"unexpected observation type for {context}: {row['observation_type']!r}"
            )
        if row["price_status"] != "reported":
            raise NetValueV2DError(f"unexpected price status for {context}: {row['price_status']!r}")

        grade = row["grade"].strip()
        end_use = row["end_use"].strip()
        is_aggregate_flag = row["is_aggregate"]
        if is_aggregate_flag not in {"true", "false"}:
            raise NetValueV2DError(
                f"invalid is_aggregate flag for {context}: {is_aggregate_flag!r}"
            )
        is_aggregate = is_aggregate_flag == "true"
        if region == "coast":
            if not grade or end_use:
                raise NetValueV2DError(
                    f"coast row must carry a grade and no end use for {context}"
                )
        else:
            if grade or not end_use:
                raise NetValueV2DError(
                    f"interior row must carry an end use and no grade for {context}"
                )
        if is_aggregate and (region != "interior" or end_use != "weighted_average"):
            raise NetValueV2DError(
                f"aggregate flag outside the Interior weighted average for {context}"
            )

        price = parse_finite_float(row["price_cad_m3"], context=context, minimum=0.0)
        if price > BC_PRICE_MAX_CAD_M3:
            raise NetValueV2DError(f"price above hard range for {context}: {price}")
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
            raise NetValueV2DError(
                f"plausibility flag mismatch for {context}: "
                f"{row['price_plausibility']!r} vs {expected_flag!r}"
            )

        try:
            period_start = date.fromisoformat(row["period_start"])
            period_end = date.fromisoformat(row["period_end"])
        except ValueError as exc:
            raise NetValueV2DError(f"invalid period date for {context}") from exc
        if period_start > period_end:
            raise NetValueV2DError(f"period start after end for {context}")

        volume = parse_optional_float(row["volume_m3"], context=context)
        if volume is not None and volume <= 0:
            raise NetValueV2DError(f"non-positive volume for {context}: {volume}")
        if is_aggregate and volume is not None:
            raise NetValueV2DError(f"aggregate row carries product volume for {context}")
        report_total_volume = parse_optional_float(
            row["report_total_volume_m3"], context=context
        )

        key = (row["report_id"], row["species"], grade, end_use)
        if key in seen_keys:
            raise NetValueV2DError(f"duplicate price cell key for {context}: {key}")
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
        raise NetValueV2DError(
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
            raise NetValueV2DError(
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
            f"{key.aggregation_level}_{key.species}_{key.grade_or_end_use}"
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
            raise NetValueV2DError(f"unexpected jurisdiction for {context}: {row['jurisdiction']!r}")
        if row["region"] not in {"Coast", "Interior"}:
            raise NetValueV2DError(f"unexpected region for {context}: {row['region']!r}")
        if row["observation_type"] != EVIDENCE_APPRAISAL:
            raise NetValueV2DError(
                f"unexpected observation type for {context}: {row['observation_type']!r}"
            )
        value = parse_finite_float(row["value"], context=context, minimum=0.0)
        if value == 0.0:
            raise NetValueV2DError(f"zero appraisal value for {context}")
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
            raise NetValueV2DError(f"duplicate appraisal key for {context}")
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
        raise NetValueV2DError(
            f"expected {BC_APPRAISAL_ROW_COUNT} appraisal rows, found {len(parsed)}"
        )
    return tuple(parsed)


def parse_appraisal_coverage(path: Path) -> tuple[AppraisalCoverageEntry, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, APPRAISAL_COVERAGE_COLUMNS)
    if len(raw_rows) != 28:
        raise NetValueV2DError(
            f"expected 28 appraisal coverage entries, found {len(raw_rows)}"
        )
    statuses = [row["extraction_status"] for row in raw_rows]
    if statuses.count("not_published") != 2 or statuses.count("partial") != 3:
        raise NetValueV2DError(
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
        raise NetValueV2DError(
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
        raise NetValueV2DError("appraisal manifest does not document missing logging")
    if "General truck hauling is represented only by cycle-time/EWB" not in caveats:
        raise NetValueV2DError("appraisal manifest does not document partial truck haul")

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
            raise NetValueV2DError(f"no appraisal rows for covered component {component}")
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
        raise NetValueV2DError(
            f"expected {BCTS_LEDGER_ROW_COUNT} BCTS ledger rows, found {len(raw_rows)}"
        )
    crown: dict[str, list[dict[str, str]]] = defaultdict(list)
    forest_sector: dict[str, list[dict[str, str]]] = defaultdict(list)
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        if row["jurisdiction"] != "BC":
            raise NetValueV2DError(f"unexpected jurisdiction for {context}")
        if row["unit"] != "CAD_per_m3_log":
            raise NetValueV2DError(f"unexpected unit for {context}: {row['unit']!r}")
        if row["value_basis"] != "observed_sale:standing":
            raise NetValueV2DError(f"unexpected value basis for {context}")
        if row["year"] != "2026":
            raise NetValueV2DError(f"unexpected BCTS year for {context}: {row['year']!r}")
        if row["perspective"] not in {"crown", "forest_sector"}:
            raise NetValueV2DError(f"unexpected perspective for {context}")
        sale_id = row["spatial_unit"]
        if row["perspective"] == "crown":
            crown[sale_id].append(row)
        else:
            forest_sector[sale_id].append(row)
    if set(crown) != set(forest_sector):
        raise NetValueV2DError("BCTS crown and forest-sector sale sets differ")
    return dict(crown), dict(forest_sector), len(raw_rows)


def parse_bcts_results(path: Path) -> dict[str, dict[str, str]]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, BCTS_RESULT_COLUMNS)
    results: dict[str, dict[str, str]] = {}
    for line_number, row in enumerate(raw_rows, start=2):
        licence_id = row["licence_id"]
        if not licence_id:
            raise NetValueV2DError(f"blank licence id at {path.name}:{line_number}")
        if licence_id in results:
            raise NetValueV2DError(f"duplicate BCTS result for {licence_id}")
        results[licence_id] = row
    return results


def build_bcts_sales(
    crown: Mapping[str, Sequence[Mapping[str, str]]],
    forest_sector: Mapping[str, Sequence[Mapping[str, str]]],
    results: Mapping[str, Mapping[str, str]],
    ledger_path: Path,
) -> tuple[tuple[BctsSale, ...], StumpageStats]:
    sales: list[BctsSale] = []
    for sale_id in sorted(crown):
        crown_rows = crown[sale_id]
        mirror_rows = forest_sector[sale_id]
        if len(crown_rows) != len(mirror_rows):
            raise NetValueV2DError(f"unpaired BCTS rows for {sale_id}")
        volumes = {
            parse_finite_float(row["volume_m3"], context=sale_id, minimum=0.0)
            for row in crown_rows
        }
        if len(volumes) != 1:
            raise NetValueV2DError(f"inconsistent BCTS volume within {sale_id}")
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
                raise NetValueV2DError(
                    f"unexpected BCTS component for {sale_id}: {row['component']}"
                )
            if row["sign"] not in {"1", "-1"}:
                raise NetValueV2DError(f"unexpected BCTS sign for {sale_id}")
        if len(base_values) > 1 or len(premium_values) > 1:
            raise NetValueV2DError(f"duplicate BCTS component rows for {sale_id}")
        if not premium_values and not base_values:
            raise NetValueV2DError(f"BCTS sale has no stumpage components: {sale_id}")

        crown_by_component = {row["component"]: row for row in crown_rows}
        mirror_by_component = {row["component"]: row for row in mirror_rows}
        if set(crown_by_component) != set(mirror_by_component):
            raise NetValueV2DError(f"BCTS mirror component set mismatch for {sale_id}")
        for component, crown_row in crown_by_component.items():
            mirror_row = mirror_by_component[component]
            if crown_row["value_cad"] != mirror_row["value_cad"]:
                raise NetValueV2DError(f"BCTS mirror value mismatch for {sale_id}")
            if int(crown_row["sign"]) != -int(mirror_row["sign"]):
                raise NetValueV2DError(f"BCTS mirror sign mismatch for {sale_id}")

        licence_id = sale_id.replace("BC-BCTS_", "")
        result = results.get(licence_id)
        if result is None:
            raise NetValueV2DError(f"missing BCTS result row for {licence_id}")
        if result["status"] != "AWARDED":
            raise NetValueV2DError(
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
            raise NetValueV2DError(f"BCTS result has no bonus for {licence_id}")
        base = base_values[0] if base_values else None
        premium = premium_values[0] if premium_values else 0.0
        total = (base or 0.0) + premium
        if base is None:
            if winning_bid is not None or upset is not None:
                raise NetValueV2DError(
                    f"BCTS missing-base sale unexpectedly reports a full bid: {licence_id}"
                )
            if not math.isclose(premium, bonus, abs_tol=RATE_TOLERANCE):
                raise NetValueV2DError(
                    f"BCTS premium-only lower bound does not match bonus for {licence_id}"
                )
            total_basis = "premium_only_lower_bound"
        else:
            if winning_bid is None:
                raise NetValueV2DError(
                    f"BCTS full sale is missing the reported winning bid: {licence_id}"
                )
            if not math.isclose(base, upset or 0.0, abs_tol=RATE_TOLERANCE):
                raise NetValueV2DError(
                    f"BCTS upset base mismatch for {licence_id}: {base} vs {upset}"
                )
            if not math.isclose(total, winning_bid, abs_tol=RATE_TOLERANCE * 10):
                raise NetValueV2DError(
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
        raise NetValueV2DError(
            f"expected {BCTS_SALE_COUNT} observed BCTS sales, found {len(sales)}"
        )
    full = [sale for sale in sales if sale.total_basis == "full_winning_bid"]
    lower = [sale for sale in sales if sale.total_basis == "premium_only_lower_bound"]
    if len(full) != 49 or len(lower) != 2:
        raise NetValueV2DError(
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


def build_qc_reference(qc_dir: Path) -> tuple[tuple[dict[str, str], ...], dict[str, Any]]:
    manifest_path = qc_dir / "manifest.json"
    if not manifest_path.is_file():
        raise NetValueV2DError(f"missing QC manifest: {manifest_path}")
    qc_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if qc_manifest.get("validation", {}).get("status") != "pass":
        raise NetValueV2DError("QC benchmark manifest validation is not pass")
    if qc_manifest.get("labels", {}).get("policy_grade") is not False:
        raise NetValueV2DError("QC benchmark is not labelled policy_grade=false")
    if qc_manifest.get("labels", {}).get("net_value_status") != QC_NET_VALUE_STATUS:
        raise NetValueV2DError("QC benchmark net-value status changed")
    verified_outputs = verify_manifest_outputs(manifest_path, qc_dir)

    provincial_path = qc_dir / "qc_net_value_benchmark.csv"
    regional_path = qc_dir / "qc_net_value_regional_range.csv"
    sensitivity_path = qc_dir / "qc_net_value_sensitivity.csv"
    bmmb_path = qc_dir / "bmmb_historical_benchmark.csv"

    provincial_rows = read_csv_rows(provincial_path)
    require_columns(provincial_path, provincial_rows, QC_PROVINCIAL_COLUMNS)
    regional_rows = read_csv_rows(regional_path)
    require_columns(regional_path, regional_rows, QC_REGIONAL_COLUMNS)
    sensitivity_rows = read_csv_rows(sensitivity_path)
    require_columns(sensitivity_path, sensitivity_rows, QC_SENSITIVITY_COLUMNS)
    bmmb_rows = read_csv_rows(bmmb_path)
    require_columns(bmmb_path, bmmb_rows, QC_BMMB_COLUMNS)

    provincial_by_id = {row["benchmark_id"]: row for row in provincial_rows}
    if len(provincial_by_id) != len(provincial_rows):
        raise NetValueV2DError("duplicate QC provincial benchmark id")
    missing = [key for key in QC_COMPUTED_BENCHMARK_IDS if key not in provincial_by_id]
    if missing:
        raise NetValueV2DError(f"missing QC computed benchmark rows: {missing}")

    sep_row = provincial_by_id["qc_2022_qc_sepm_total"]
    reference_purchase = parse_finite_float(
        sep_row["delivered_purchase_cad_m3"], context="QC SEPM purchase"
    )

    output_rows: list[dict[str, str]] = []
    validation_details: list[str] = []

    def copy_provincial(row: Mapping[str, str], reference_type: str) -> dict[str, str]:
        for field in (
            "unit",
            "price_year",
            "currency_basis",
            "price_basis",
            "evidence",
            "policy_grade",
            "net_value_status",
            "is_additive",
        ):
            expected = {
                "unit": "CAD/m3",
                "price_year": "2022",
                "currency_basis": "CAD_nominal_2022",
                "price_basis": "delivered_to_mill",
                "evidence": EVIDENCE_SURVEY,
                "policy_grade": "false",
                "net_value_status": QC_NET_VALUE_STATUS,
                "is_additive": "false",
            }[field]
            if row[field] != expected:
                raise NetValueV2DError(
                    f"QC provincial {row['benchmark_id']} has {field}={row[field]!r}, "
                    f"expected {expected!r}"
                )
        purchase = parse_finite_float(
            row["delivered_purchase_cad_m3"], context=f"{row['benchmark_id']} purchase"
        )
        before = parse_finite_float(
            row["procurement_before_transfers_cad_m3"],
            context=f"{row['benchmark_id']} procurement before",
        )
        transfers = parse_finite_float(
            row["transfers_total_cad_m3"], context=f"{row['benchmark_id']} transfers"
        )
        after = parse_finite_float(
            row["procurement_after_transfers_cad_m3"],
            context=f"{row['benchmark_id']} procurement after",
        )
        residual_before = parse_finite_float(
            row["residual_before_transfers_cad_m3"],
            context=f"{row['benchmark_id']} residual before",
        )
        residual_after = parse_finite_float(
            row["residual_after_transfers_cad_m3"],
            context=f"{row['benchmark_id']} residual after",
        )
        if not math.isclose(purchase - before, residual_before, abs_tol=ARITHMETIC_TOLERANCE):
            raise NetValueV2DError(f"QC residual-before mismatch for {row['benchmark_id']}")
        if not math.isclose(purchase - after, residual_after, abs_tol=ARITHMETIC_TOLERANCE):
            raise NetValueV2DError(f"QC residual-after mismatch for {row['benchmark_id']}")
        if not math.isclose(before + transfers, after, abs_tol=ARITHMETIC_TOLERANCE):
            raise NetValueV2DError(f"QC transfer identity mismatch for {row['benchmark_id']}")
        return {
            "reference_id": f"qc_ref_{row['benchmark_id']}",
            "reference_type": reference_type,
            "source_artifact": relative_path(provincial_path),
            "source_record_key": row["benchmark_id"],
            "jurisdiction": "QC",
            "evidence_tier": EVIDENCE_SURVEY,
            "price_year": row["price_year"],
            "currency_basis": row["currency_basis"],
            "unit": row["unit"],
            "delivered_purchase_cad_m3": row["delivered_purchase_cad_m3"],
            "procurement_before_transfers_cad_m3": row["procurement_before_transfers_cad_m3"],
            "transfers_total_cad_m3": row["transfers_total_cad_m3"],
            "procurement_after_transfers_cad_m3": row["procurement_after_transfers_cad_m3"],
            "residual_before_transfers_cad_m3": row["residual_before_transfers_cad_m3"],
            "residual_after_transfers_cad_m3": row["residual_after_transfers_cad_m3"],
            "value_before_transfers_mcad": "",
            "value_after_transfers_mcad": "",
            "weighted_rate_cad_m3": "",
            "median_rate_cad_m3": "",
            "min_rate_cad_m3": "",
            "max_rate_cad_m3": "",
            "sale_count": "",
            "policy_grade": POLICY_GRADE,
            "net_value_status": QC_NET_VALUE_STATUS,
            "validation_status": "validated_copy",
            "validation_detail": (
                "Exact source fields copied; purchase-minus-procurement and "
                "before-plus-transfers identities rechecked."
            ),
            "notes": row.get("notes", ""),
        }

    for benchmark_id in QC_COMPUTED_BENCHMARK_IDS:
        output_rows.append(copy_provincial(provincial_by_id[benchmark_id], "provincial_residual"))
    validation_details.append("provincial residual identities rechecked")

    regional_values = [row for row in regional_rows if row["statistic"] == "region_value"]
    if len(regional_values) != QC_REGIONAL_ROW_COUNT:
        raise NetValueV2DError(
            f"expected {QC_REGIONAL_ROW_COUNT} QC regional rows, found {len(regional_values)}"
        )
    regional_before: list[float] = []
    regional_after: list[float] = []
    for row in regional_values:
        if row["evidence"] != EVIDENCE_SURVEY:
            raise NetValueV2DError("QC regional row has unexpected evidence label")
        if row["policy_grade"] != "false" or row["net_value_status"] != QC_NET_VALUE_STATUS:
            raise NetValueV2DError("QC regional row has unexpected policy/net-value label")
        row_purchase = parse_finite_float(
            row["reference_purchase_cad_m3"], context=f"{row['regional_benchmark_id']} purchase"
        )
        if not math.isclose(row_purchase, reference_purchase, abs_tol=RATE_TOLERANCE):
            raise NetValueV2DError("QC regional reference purchase differs from SEPM purchase")
        before = parse_finite_float(
            row["procurement_before_transfers_cad_m3"], context=row["regional_benchmark_id"]
        )
        transfers = parse_finite_float(
            row["transfers_total_cad_m3"], context=row["regional_benchmark_id"]
        )
        after = parse_finite_float(
            row["procurement_after_transfers_cad_m3"], context=row["regional_benchmark_id"]
        )
        residual_before = parse_finite_float(
            row["residual_before_transfers_cad_m3"], context=row["regional_benchmark_id"]
        )
        residual_after = parse_finite_float(
            row["residual_after_transfers_cad_m3"], context=row["regional_benchmark_id"]
        )
        if not math.isclose(
            reference_purchase - before, residual_before, abs_tol=ARITHMETIC_TOLERANCE
        ):
            raise NetValueV2DError("QC regional residual-before mismatch")
        if not math.isclose(
            reference_purchase - after, residual_after, abs_tol=ARITHMETIC_TOLERANCE
        ):
            raise NetValueV2DError("QC regional residual-after mismatch")
        if not math.isclose(before + transfers, after, abs_tol=ARITHMETIC_TOLERANCE):
            raise NetValueV2DError("QC regional transfer identity mismatch")
        regional_before.append(residual_before)
        regional_after.append(residual_after)
        output_rows.append(
            {
                "reference_id": f"qc_ref_{row['regional_benchmark_id']}",
                "reference_type": "regional_conditional_range",
                "source_artifact": relative_path(regional_path),
                "source_record_key": row["regional_benchmark_id"],
                "jurisdiction": "QC",
                "evidence_tier": EVIDENCE_SURVEY,
                "price_year": row["price_year"],
                "currency_basis": row["currency_basis"],
                "unit": row["unit"],
                "delivered_purchase_cad_m3": row["reference_purchase_cad_m3"],
                "procurement_before_transfers_cad_m3": row[
                    "procurement_before_transfers_cad_m3"
                ],
                "transfers_total_cad_m3": row["transfers_total_cad_m3"],
                "procurement_after_transfers_cad_m3": row[
                    "procurement_after_transfers_cad_m3"
                ],
                "residual_before_transfers_cad_m3": row[
                    "residual_before_transfers_cad_m3"
                ],
                "residual_after_transfers_cad_m3": row[
                    "residual_after_transfers_cad_m3"
                ],
                "value_before_transfers_mcad": "",
                "value_after_transfers_mcad": "",
                "weighted_rate_cad_m3": "",
                "median_rate_cad_m3": "",
                "min_rate_cad_m3": "",
                "max_rate_cad_m3": "",
                "sale_count": "",
                "policy_grade": POLICY_GRADE,
                "net_value_status": QC_NET_VALUE_STATUS,
                "validation_status": "validated_copy",
                "validation_detail": (
                    "Exact source fields copied; conditional residual rechecked against "
                    "the provincial SEPM purchase price."
                ),
                "notes": row.get("notes", ""),
            }
        )
    validation_details.append("regional conditional residuals rechecked")

    if len(sensitivity_rows) != QC_SENSITIVITY_CASE_COUNT:
        raise NetValueV2DError(
            f"expected {QC_SENSITIVITY_CASE_COUNT} QC sensitivity cases, "
            f"found {len(sensitivity_rows)}"
        )
    for row in sensitivity_rows:
        if row["evidence"] != EVIDENCE_SURVEY:
            raise NetValueV2DError("QC sensitivity row has unexpected evidence label")
        if row["policy_grade"] != "false" or row["net_value_status"] != QC_NET_VALUE_STATUS:
            raise NetValueV2DError("QC sensitivity row has unexpected labels")
        volume = parse_finite_float(row["qc_harvest_m3"], context=row["case_id"])
        residual_before = parse_finite_float(
            row["residual_before_transfers_cad_m3"], context=row["case_id"]
        )
        residual_after = parse_finite_float(
            row["residual_after_transfers_cad_m3"], context=row["case_id"]
        )
        value_before = parse_finite_float(
            row["qc_net_value_before_transfers_mcad"], context=row["case_id"]
        )
        value_after = parse_finite_float(
            row["qc_net_value_after_transfers_mcad"], context=row["case_id"]
        )
        if not math.isclose(
            volume * residual_before / 1_000_000.0,
            value_before,
            abs_tol=0.001,
        ):
            raise NetValueV2DError(f"QC sensitivity before-transfer arithmetic mismatch: {row['case_id']}")
        if not math.isclose(
            volume * residual_after / 1_000_000.0,
            value_after,
            abs_tol=0.001,
        ):
            raise NetValueV2DError(f"QC sensitivity after-transfer arithmetic mismatch: {row['case_id']}")
        output_rows.append(
            {
                "reference_id": f"qc_ref_sensitivity_{row['case_id']}",
                "reference_type": "screening_sensitivity",
                "source_artifact": relative_path(sensitivity_path),
                "source_record_key": row["case_id"],
                "jurisdiction": "QC",
                "evidence_tier": EVIDENCE_SURVEY,
                "price_year": "2022",
                "currency_basis": "CAD_nominal_2022",
                "unit": "CAD/m3; M CAD",
                "delivered_purchase_cad_m3": "",
                "procurement_before_transfers_cad_m3": "",
                "transfers_total_cad_m3": "",
                "procurement_after_transfers_cad_m3": "",
                "residual_before_transfers_cad_m3": row[
                    "residual_before_transfers_cad_m3"
                ],
                "residual_after_transfers_cad_m3": row[
                    "residual_after_transfers_cad_m3"
                ],
                "value_before_transfers_mcad": row["qc_net_value_before_transfers_mcad"],
                "value_after_transfers_mcad": row["qc_net_value_after_transfers_mcad"],
                "weighted_rate_cad_m3": "",
                "median_rate_cad_m3": "",
                "min_rate_cad_m3": "",
                "max_rate_cad_m3": "",
                "sale_count": "",
                "policy_grade": POLICY_GRADE,
                "net_value_status": QC_NET_VALUE_STATUS,
                "validation_status": "validated_copy",
                "validation_detail": "Exact source fields copied; M CAD arithmetic rechecked.",
                "notes": row.get("notes", ""),
            }
        )
    validation_details.append("sensitivity arithmetic rechecked")

    bmmb_overall = next(
        (row for row in bmmb_rows if row["aggregation_level"] == "delivered_all_years"),
        None,
    )
    if bmmb_overall is None:
        raise NetValueV2DError("BMMB overall row is missing")
    for field, expected in (
        ("sale_count", "18"),
        ("full_rate_sale_count", "11"),
        ("partial_award_sale_count", "2"),
        ("currency_basis", "CAD_nominal_2011_2015"),
        ("deflator_applied", "false"),
        ("grade_split", "false"),
        ("species_allocation", "false"),
        ("evidence", EVIDENCE_HISTORICAL),
        ("policy_grade", "false"),
        ("net_value_status", QC_NET_VALUE_STATUS),
    ):
        if bmmb_overall[field] != expected:
            raise NetValueV2DError(
                f"BMMB overall {field}={bmmb_overall[field]!r}, expected {expected!r}"
            )
    if not math.isclose(
        parse_finite_float(bmmb_overall["weighted_rate_cad_m3"], context="BMMB weighted"),
        38.2940,
        abs_tol=RATE_TOLERANCE,
    ):
        raise NetValueV2DError("BMMB weighted rate changed")
    if not math.isclose(
        parse_finite_float(bmmb_overall["median_rate_cad_m3"], context="BMMB median"),
        36.6668,
        abs_tol=RATE_TOLERANCE,
    ):
        raise NetValueV2DError("BMMB median rate changed")
    output_rows.append(
        {
            "reference_id": "qc_ref_bmmb_delivered_all_years",
            "reference_type": "historical_revenue_benchmark",
            "source_artifact": relative_path(bmmb_path),
            "source_record_key": bmmb_overall["aggregation_level"],
            "jurisdiction": "QC",
            "evidence_tier": EVIDENCE_HISTORICAL,
            "price_year": bmmb_overall["sale_year"],
            "currency_basis": bmmb_overall["currency_basis"],
            "unit": "CAD/m3",
            "delivered_purchase_cad_m3": "",
            "procurement_before_transfers_cad_m3": "",
            "transfers_total_cad_m3": "",
            "procurement_after_transfers_cad_m3": "",
            "residual_before_transfers_cad_m3": "",
            "residual_after_transfers_cad_m3": "",
            "value_before_transfers_mcad": "",
            "value_after_transfers_mcad": "",
            "weighted_rate_cad_m3": bmmb_overall["weighted_rate_cad_m3"],
            "median_rate_cad_m3": bmmb_overall["median_rate_cad_m3"],
            "min_rate_cad_m3": bmmb_overall["min_rate_cad_m3"],
            "max_rate_cad_m3": bmmb_overall["max_rate_cad_m3"],
            "sale_count": bmmb_overall["sale_count"],
            "policy_grade": POLICY_GRADE,
            "net_value_status": QC_NET_VALUE_STATUS,
            "validation_status": "validated_copy",
            "validation_detail": (
                "Exact source fields copied; nominal 2011-2015 delivered-sale revenue "
                "benchmark only, with no cost stack."
            ),
            "notes": bmmb_overall.get("notes", ""),
        }
    )
    validation_details.append("BMMB historical revenue benchmark rechecked")

    summary = {
        "source_artifact": relative_path(qc_dir),
        "manifest_validation": "pass",
        "verified_outputs": verified_outputs,
        "provincial_rows": len(QC_COMPUTED_BENCHMARK_IDS),
        "regional_rows": len(regional_values),
        "sensitivity_rows": len(sensitivity_rows),
        "historical_rows": 1,
        "reference_purchase_cad_m3": rounded(reference_purchase),
        "regional_residual_before_range_cad_m3": [
            rounded(min(regional_before)),
            rounded(max(regional_before)),
        ],
        "regional_residual_after_range_cad_m3": [
            rounded(min(regional_after)),
            rounded(max(regional_after)),
        ],
        "validation_details": validation_details,
    }
    return tuple(output_rows), summary


def build_summary_rows(
    qc_rows: Sequence[Mapping[str, str]],
    series_summaries: Sequence[SeriesPriceSummary],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
) -> tuple[dict[str, Any], ...]:
    qc_by_key = {row["source_record_key"]: row for row in qc_rows}
    rows: list[dict[str, Any]] = []

    def add_qc_residual(
        *,
        summary_id: str,
        source_key: str,
        revenue_basis: str,
        revenue_measure: str,
        cost_coverage: str,
        cost_detail: str,
        notes: str,
    ) -> None:
        source = qc_by_key[source_key]
        revenue_value = parse_finite_float(
            source["delivered_purchase_cad_m3"], context=source_key
        )
        revenue_values = (revenue_value,)
        residual_before = parse_finite_float(
            source["residual_before_transfers_cad_m3"], context=source_key
        )
        residual_after = parse_finite_float(
            source["residual_after_transfers_cad_m3"], context=source_key
        )
        rows.append(
            {
                "summary_id": summary_id,
                "jurisdiction": "QC",
                "evidence_tier": EVIDENCE_SURVEY,
                "revenue_basis": revenue_basis,
                "revenue_measure": revenue_measure,
                "revenue_min_cad_m3": min(revenue_values),
                "revenue_p25_cad_m3": None,
                "revenue_median_cad_m3": statistics.median(revenue_values),
                "revenue_p75_cad_m3": None,
                "revenue_max_cad_m3": max(revenue_values),
                "revenue_unit": "CAD/m3",
                "revenue_year_basis": "nominal_2022",
                "cost_coverage": cost_coverage,
                "cost_coverage_detail": cost_detail,
                "net_value_status": QC_NET_VALUE_STATUS,
                "net_value_before_transfers_min_cad_m3": residual_before,
                "net_value_before_transfers_max_cad_m3": residual_before,
                "net_value_after_transfers_min_cad_m3": residual_after,
                "net_value_after_transfers_max_cad_m3": residual_after,
                "net_value_reason": "survey_benchmark_not_policy_grade",
                "net_value_is_combined_national": False,
                "policy_grade": False,
                "is_additive": False,
                "source_artifact": source["source_artifact"],
                "notes": notes,
            }
        )

    add_qc_residual(
        summary_id="qc_survey_sepm_total",
        source_key="qc_2022_qc_sepm_total",
        revenue_basis="delivered_to_mill_survey",
        revenue_measure="delivered_wood_purchase",
        cost_coverage="full_survey_procurement_stack",
        cost_detail="harvest + road + haul + hosting + other costs and transfers (survey aggregates)",
        notes="Provincial SEPM survey benchmark; residual before/after transfers. Not an observed transaction.",
    )
    add_qc_residual(
        summary_id="qc_survey_hardwood_sciage",
        source_key="qc_2022_qc_feuillus_durs_sciage",
        revenue_basis="delivered_to_mill_survey",
        revenue_measure="delivered_wood_purchase",
        cost_coverage="full_survey_procurement_stack",
        cost_detail="T20 synthesis procurement for hardwood sciage",
        notes="Product-level survey benchmark; negative after-transfer residual is reported unclipped.",
    )
    add_qc_residual(
        summary_id="qc_survey_poplar_trituration",
        source_key="qc_2022_qc_peupliers_trituration",
        revenue_basis="delivered_to_mill_survey",
        revenue_measure="delivered_wood_purchase",
        cost_coverage="full_survey_procurement_stack",
        cost_detail="T20 synthesis procurement for poplar trituration",
        notes="Product-level survey benchmark; both residuals are negative and reported unclipped.",
    )

    regional = qc_by_key["qc_2022_regional_sepm_R01-R03-R11-R12"]
    regional_rows = [
        row for row in qc_rows if row["reference_type"] == "regional_conditional_range"
    ]
    regional_reference_purchase = parse_finite_float(
        regional["delivered_purchase_cad_m3"], context=regional["reference_id"]
    )
    regional_before = [
        parse_finite_float(row["residual_before_transfers_cad_m3"], context=row["reference_id"])
        for row in regional_rows
    ]
    regional_after = [
        parse_finite_float(row["residual_after_transfers_cad_m3"], context=row["reference_id"])
        for row in regional_rows
    ]
    rows.append(
        {
            "summary_id": "qc_survey_regional_sepm_conditional",
            "jurisdiction": "QC",
            "evidence_tier": EVIDENCE_SURVEY,
            "revenue_basis": "delivered_to_mill_survey",
            "revenue_measure": "provincial_sepm_purchase_held_constant",
            "revenue_min_cad_m3": regional_reference_purchase,
            "revenue_p25_cad_m3": None,
            "revenue_median_cad_m3": regional_reference_purchase,
            "revenue_p75_cad_m3": None,
            "revenue_max_cad_m3": regional_reference_purchase,
            "revenue_unit": "CAD/m3",
            "revenue_year_basis": "nominal_2022",
            "cost_coverage": "regional_sepm_procurement_only",
            "cost_coverage_detail": (
                "seven regional SEPM procurement groups; no regional purchase price is published"
            ),
            "net_value_status": QC_NET_VALUE_STATUS,
            "net_value_before_transfers_min_cad_m3": min(regional_before),
            "net_value_before_transfers_max_cad_m3": max(regional_before),
            "net_value_after_transfers_min_cad_m3": min(regional_after),
            "net_value_after_transfers_max_cad_m3": max(regional_after),
            "net_value_reason": "conditional_regional_range_not_observed_net_value",
            "net_value_is_combined_national": False,
            "policy_grade": False,
            "is_additive": False,
            "source_artifact": regional["source_artifact"],
            "notes": (
                "Conditional range: provincial SEPM purchase price held constant. "
                "Not an observed regional transaction net value."
            ),
        }
    )

    bmmb = next(
        row for row in qc_rows if row["reference_type"] == "historical_revenue_benchmark"
    )
    rows.append(
        {
            "summary_id": "qc_bmmb_delivered_2011_2015",
            "jurisdiction": "QC",
            "evidence_tier": EVIDENCE_HISTORICAL,
            "revenue_basis": "delivered_to_destination_auction",
            "revenue_measure": "bmmb_delivered_sale_rate",
            "revenue_min_cad_m3": parse_finite_float(
                bmmb["min_rate_cad_m3"], context="BMMB min"
            ),
            "revenue_p25_cad_m3": None,
            "revenue_median_cad_m3": parse_finite_float(
                bmmb["median_rate_cad_m3"], context="BMMB median"
            ),
            "revenue_p75_cad_m3": None,
            "revenue_max_cad_m3": parse_finite_float(
                bmmb["max_rate_cad_m3"], context="BMMB max"
            ),
            "revenue_unit": "CAD/m3",
            "revenue_year_basis": "nominal_2011_2015",
            "cost_coverage": "revenue_only_no_cost_stack",
            "cost_coverage_detail": "18 delivered sales, 11 full rates; no species, grade, or cost detail",
            "net_value_status": HISTORICAL_NET_VALUE_STATUS,
            "net_value_before_transfers_min_cad_m3": None,
            "net_value_before_transfers_max_cad_m3": None,
            "net_value_after_transfers_min_cad_m3": None,
            "net_value_after_transfers_max_cad_m3": None,
            "net_value_reason": "revenue_benchmark_only_no_cost_stack",
            "net_value_is_combined_national": False,
            "policy_grade": False,
            "is_additive": False,
            "source_artifact": bmmb["source_artifact"],
            "notes": "Nominal 2011-2015 delivered-sale revenue benchmark; not a net value.",
        }
    )

    for summary in series_summaries:
        price_range = summary.price_range
        rows.append(
            {
                "summary_id": f"bc_price_{summary.region}_{summary.report_basis}_{summary.log_class}",
                "jurisdiction": "BC",
                "evidence_tier": EVIDENCE_OBSERVED_PRICE,
                "revenue_basis": "mill_delivered_published_average",
                "revenue_measure": "all_reported_grades_or_end_uses_and_species",
                "revenue_min_cad_m3": price_range.minimum,
                "revenue_p25_cad_m3": price_range.p25,
                "revenue_median_cad_m3": price_range.median,
                "revenue_p75_cad_m3": price_range.p75,
                "revenue_max_cad_m3": price_range.maximum,
                "revenue_unit": "CAD/m3",
                "revenue_year_basis": f"nominal_{summary.period_label}",
                "cost_coverage": "partial_appraisal_allowance_missing_logging_and_haul",
                "cost_coverage_detail": (
                    "road, bridge, culvert, silviculture, specified operations, and TOA "
                    "allowances exist; logging and general truck haul are missing"
                ),
                "net_value_status": BC_NET_VALUE_STATUS,
                "net_value_before_transfers_min_cad_m3": None,
                "net_value_before_transfers_max_cad_m3": None,
                "net_value_after_transfers_min_cad_m3": None,
                "net_value_after_transfers_max_cad_m3": None,
                "net_value_reason": BC_NET_VALUE_REASON,
                "net_value_is_combined_national": False,
                "policy_grade": False,
                "is_additive": False,
                "source_artifact": relative_path(BC_LOG_MARKET_DIR / "bc_log_market_prices.csv"),
                "notes": (
                    "Cross-grade/cross-species observed range for this published vintage; "
                    "1_month and 3_month are separate and not annualized."
                ),
            }
        )

    rows.append(
        {
            "summary_id": "bc_bcts_observed_stumpage",
            "jurisdiction": "BC",
            "evidence_tier": EVIDENCE_OBSERVED_STUMPAGE,
            "revenue_basis": "standing_timber_stumpage",
            "revenue_measure": "bcts_winning_bid",
            "revenue_min_cad_m3": stumpage_stats.min_observed_cad_m3,
            "revenue_p25_cad_m3": None,
            "revenue_median_cad_m3": stumpage_stats.median_full_bid_cad_m3,
            "revenue_p75_cad_m3": None,
            "revenue_max_cad_m3": stumpage_stats.max_observed_cad_m3,
            "revenue_unit": "CAD/m3_log",
            "revenue_year_basis": "nominal_2026",
            "cost_coverage": "partial_appraisal_allowance_missing_logging_and_haul",
            "cost_coverage_detail": (
                "51 awarded BCTS sales; 49 full winning bids and 2 premium-only lower bounds; "
                "stumpage is standing timber, not delivered revenue"
            ),
            "net_value_status": BC_NET_VALUE_STATUS,
            "net_value_before_transfers_min_cad_m3": None,
            "net_value_before_transfers_max_cad_m3": None,
            "net_value_after_transfers_min_cad_m3": None,
            "net_value_after_transfers_max_cad_m3": None,
            "net_value_reason": BC_NET_VALUE_REASON,
            "net_value_is_combined_national": False,
            "policy_grade": False,
            "is_additive": False,
            "source_artifact": relative_path(BCTS_DIR / "bcts_bid_ledger.csv"),
            "notes": (
                "Observed standing-timber stumpage sample; bid is not delivered revenue, "
                "and no BC full net value is estimated."
            ),
        }
    )

    covered = [row for row in cost_rows if row.covered]
    rows.append(
        {
            "summary_id": "bc_appraisal_cost_coverage",
            "jurisdiction": "BC",
            "evidence_tier": EVIDENCE_APPRAISAL,
            "revenue_basis": "not_applicable",
            "revenue_measure": "none",
            "revenue_min_cad_m3": None,
            "revenue_p25_cad_m3": None,
            "revenue_median_cad_m3": None,
            "revenue_p75_cad_m3": None,
            "revenue_max_cad_m3": None,
            "revenue_unit": "",
            "revenue_year_basis": "effective_2026",
            "cost_coverage": "partial_appraisal_allowance_missing_logging_and_haul",
            "cost_coverage_detail": (
                f"{len(covered)} covered appraisal component families; logging, general truck "
                "haul, and EWB are not published as costs"
            ),
            "net_value_status": BC_NET_VALUE_STATUS,
            "net_value_before_transfers_min_cad_m3": None,
            "net_value_before_transfers_max_cad_m3": None,
            "net_value_after_transfers_min_cad_m3": None,
            "net_value_after_transfers_max_cad_m3": None,
            "net_value_reason": BC_NET_VALUE_REASON,
            "net_value_is_combined_national": False,
            "policy_grade": False,
            "is_additive": False,
            "source_artifact": relative_path(BC_APPRAISAL_DIR / "bc_appraisal_costs.csv"),
            "notes": (
                "Allowances are appraisal inputs, not observed contractor costs; unit bases "
                "differ and no total $/m3 is computed."
            ),
        }
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
    price_cells: Sequence[PriceCell],
    price_summaries: Sequence[PriceGroupSummary],
    price_rows: Sequence[Mapping[str, Any]],
    stumpage_sales: Sequence[BctsSale],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
    qc_rows: Sequence[Mapping[str, str]],
    summary_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    checks: list[dict[str, Any]] = []

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
        "bc_price_observation_labels",
        all(
            cell.region in {"coast", "interior"}
            and cell.report_basis in {"1_month", "3_month"}
            for cell in price_cells
        ),
        "regions and vintages are in the allowed set",
    )
    add_check(
        checks,
        "bc_price_hard_range_and_nonnegative",
        all(BC_PRICE_MIN_CAD_M3 <= cell.price_cad_m3 <= BC_PRICE_MAX_CAD_M3 for cell in price_cells),
        "all prices are within 0-2000 CAD/m3",
    )
    zero_count = sum(1 for cell in price_cells if cell.price_plausibility == "zero_cad_m3")
    below_count = sum(1 for cell in price_cells if cell.price_plausibility == "below_1_cad_m3")
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
        "bc_price_flagged_cells_excluded_from_ranges",
        all(
            summary.price_range.minimum is None or summary.price_range.minimum > 0.0
            for summary in price_summaries
            if summary.ok_cell_count
        ),
        "no zero or negative value enters an ok range",
    )
    add_check(
        checks,
        "bc_price_aggregation_role_separation",
        all(
            (row["cell_role"] == "reported_weighted_average")
            == bool(row["is_aggregate"])
            for row in price_rows
        ),
        "weighted-average rows are separate from product cells",
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
        "bc_price_no_vintage_mixing",
        all(
            row["report_basis"] in {"1_month", "3_month"}
            and "not additive" in row["vintage_handling"]
            for row in price_rows
        ),
        "each range has one published vintage and is marked non-additive",
    )
    annual_groups = [summary for summary in price_summaries if summary.key.aggregation_level == "annual"]
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
        len(aggregate_cells) == 890 and all(cell.region == "interior" for cell in aggregate_cells),
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
            min(sibling_prices) - 0.02 <= cell.price_cad_m3 <= max(sibling_prices) + 0.02
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
        stumpage_stats.full_bid_count == 49 and stumpage_stats.lower_bound_count == 2,
        f"full={stumpage_stats.full_bid_count}, lower_bound={stumpage_stats.lower_bound_count}",
    )
    add_check(
        checks,
        "bc_stumpage_nonnegative_and_labeled",
        all(
            sale.stumpage_total_cad_m3 >= 0
            and sale.total_basis in {"full_winning_bid", "premium_only_lower_bound"}
            for sale in stumpage_sales
        ),
        "all observed stumpage totals are nonnegative and carry a basis label",
    )

    add_check(
        checks,
        "bc_cost_row_count",
        sum(row.row_count for row in cost_rows if row.component != "full_bc_net_value")
        == BC_APPRAISAL_ROW_COUNT,
        f"{sum(row.row_count for row in cost_rows if row.component != 'full_bc_net_value')} "
        f"appraisal rows assigned (expected {BC_APPRAISAL_ROW_COUNT})",
    )
    add_check(
        checks,
        "bc_cost_appraisal_vs_observed",
        all(row.evidence_tier in {EVIDENCE_APPRAISAL, EVIDENCE_NOT_AVAILABLE} for row in cost_rows)
        and all(not row.observed_cost for row in cost_rows),
        "all cost rows are appraisal allowances or explicitly unavailable; none is observed cost",
    )
    missing_components = {
        row.component for row in cost_rows if row.component in {"logging", "truck_haul", "estimated_winning_bid"}
    }
    add_check(
        checks,
        "bc_cost_missing_components",
        missing_components == {"logging", "truck_haul", "estimated_winning_bid"}
        and any(
            row.component == "logging" and row.missing_reason
            for row in cost_rows
        ),
        "logging, general truck haul, and EWB are explicitly missing",
    )
    add_check(
        checks,
        "bc_cost_no_full_net_value",
        any(
            row.component == "full_bc_net_value"
            and row.coverage_status == BC_NET_VALUE_STATUS
            and row.missing_reason == BC_NET_VALUE_REASON
            for row in cost_rows
        ),
        "full BC net value row is explicitly not estimated",
    )

    add_check(
        checks,
        "qc_reference_row_count",
        len(qc_rows) == 14,
        f"{len(qc_rows)} QC references (3 provincial + 7 regional + 3 sensitivity + 1 historical)",
    )
    add_check(
        checks,
        "qc_reference_labels",
        all(
            row["jurisdiction"] == "QC"
            and row["policy_grade"] == "false"
            and row["net_value_status"] in {QC_NET_VALUE_STATUS}
            and row["validation_status"] == "validated_copy"
            for row in qc_rows
        ),
        "all QC references are validated copies with policy_grade=false",
    )
    add_check(
        checks,
        "qc_sepm_values_match_source",
        any(
            row["source_record_key"] == "qc_2022_qc_sepm_total"
            and row["delivered_purchase_cad_m3"] == "107.2200"
            and row["residual_before_transfers_cad_m3"] == "25.8200"
            and row["residual_after_transfers_cad_m3"] == "0.8100"
            for row in qc_rows
        ),
        "SEPM 107.22 purchase and 25.82/0.81 residuals match the QC source",
    )

    bc_summary = [row for row in summary_rows if row["jurisdiction"] == "BC"]
    add_check(
        checks,
        "bc_net_value_reason_present",
        bool(bc_summary)
        and all(
            row["net_value_status"] == BC_NET_VALUE_STATUS
            and row["net_value_reason"] == BC_NET_VALUE_REASON
            for row in bc_summary
        ),
        f"{len(bc_summary)} BC summary rows all not_estimated/missing_logging_and_haul_cost",
    )
    add_check(
        checks,
        "no_combined_national_net_value",
        all(row["net_value_is_combined_national"] is False for row in summary_rows)
        and all(row["jurisdiction"] in {"QC", "BC"} for row in summary_rows),
        "no national combined net-value row exists",
    )
    add_check(
        checks,
        "evidence_tiers_separated",
        all(row["evidence_tier"] in EVIDENCE_TIERS for row in summary_rows)
        and {
            row["evidence_tier"] for row in summary_rows if row["jurisdiction"] == "BC"
        }
        == {EVIDENCE_OBSERVED_PRICE, EVIDENCE_OBSERVED_STUMPAGE, EVIDENCE_APPRAISAL},
        "BC observed price, observed stumpage, and appraisal allowance rows are distinct",
    )
    add_check(
        checks,
        "policy_grade_false",
        all(row["policy_grade"] is False for row in summary_rows)
        and all(row["policy_grade"] == "false" for row in qc_rows),
        "policy_grade=false on every summary and QC reference row",
    )

    add_check(
        checks,
        "no_undocumented_negative_values",
        all(
            row["revenue_min_cad_m3"] is None or row["revenue_min_cad_m3"] >= 0
            for row in summary_rows
        )
        and all(
            (
                row["net_value_before_transfers_min_cad_m3"] is None
                or row["net_value_before_transfers_min_cad_m3"] >= 0
                or row["jurisdiction"] == "QC"
            )
            and (
                row["net_value_after_transfers_min_cad_m3"] is None
                or row["net_value_after_transfers_min_cad_m3"] >= 0
                or row["jurisdiction"] == "QC"
            )
            for row in summary_rows
        )
        and all(
            row["residual_before_transfers_cad_m3"] == ""
            or float(row["residual_before_transfers_cad_m3"]) >= 0
            or row["jurisdiction"] == "QC"
            for row in qc_rows
        ),
        "QC negative residuals are documented; no negative BC price, stumpage, or cost",
    )
    return tuple(checks)


def write_report(
    path: Path,
    *,
    summary_rows: Sequence[Mapping[str, Any]],
    series_summaries: Sequence[SeriesPriceSummary],
    stumpage_stats: StumpageStats,
    cost_rows: Sequence[CostCoverageRow],
    qc_rows: Sequence[Mapping[str, str]],
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

    sep = find_qc("qc_2022_qc_sepm_total")
    hardwood = find_qc("qc_2022_qc_feuillus_durs_sciage")
    poplar = find_qc("qc_2022_qc_peupliers_trituration")
    bmmb = next(
        row for row in qc_rows if row["reference_type"] == "historical_revenue_benchmark"
    )
    regional_rows = [
        row for row in qc_rows if row["reference_type"] == "regional_conditional_range"
    ]
    regional_before = [float(row["residual_before_transfers_cad_m3"]) for row in regional_rows]
    regional_after = [float(row["residual_after_transfers_cad_m3"]) for row in regional_rows]

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
        ["Component", "Coverage", "Rows", "Bases", "Direct CAD/m3 rows", "Usable", "Missing reason"],
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

    comparison_table = table(
        [
            "Dimension",
            "QC 2022 survey benchmark",
            "BC 2026 evidence",
            "Comparability",
        ],
        [
            [
                "Evidence tier",
                "survey_benchmark",
                "observed_price + observed_stumpage + appraisal_allowance",
                "Not comparable: different evidence classes",
            ],
            [
                "Revenue basis",
                "delivered-to-mill survey purchase price, nominal 2022",
                "mill-delivered published averages (2019-2026) and standing-timber BCTS bids (2026)",
                "Not comparable: different price point and year",
            ],
            [
                "Cost coverage",
                "full survey procurement stack: harvest, road, haul, hosting, other, transfers",
                "partial appraisal allowances: road, bridge, culvert, silviculture, specified operations, TOA; missing logging, general truck haul, EWB",
                "Not comparable: BC has no logging or general truck-haul cost",
            ],
            [
                "Net value",
                "benchmark_only: SEPM 25.82 before / 0.81 after transfers CAD/m3; regional conditional 10.15-48.49 / -10.88-28.44",
                "not_estimated; reason missing_logging_and_haul_cost",
                "Not comparable: QC benchmark vs BC partial coverage",
            ],
            [
                "Policy grade",
                "false",
                "false",
                "Neither is policy-grade",
            ],
        ],
    )

    stop = (
        "**Stop rule.** A BC number computed as `log price - road allowance - silviculture allowance` "
        "(or any subset of the published allowances) is **not** net value. It omits logging and "
        "general truck haul, mixes CAD/m3 with CAD/km and CAD/each without site quantities, and "
        "treats appraisal allowances as observed costs."
    )

    evidence_table = table(
        ["Label", "Meaning", "Used for"],
        [
            ["survey_benchmark", "QC 2022 MRNF survey aggregate residual", "QC reference only"],
            ["benchmark_historical", "BMMB 2011-2015 delivered-sale revenue", "QC historical revenue reference only"],
            ["observed_price", "BC published mill-delivered log-market average", "BC revenue/price range only"],
            ["observed_stumpage", "BC BCTS winning bid for standing timber", "BC stumpage reference only; not delivered revenue"],
            ["appraisal_allowance", "BC IAM/CAM tabular cost allowance", "BC partial cost coverage only; not observed cost"],
        ],
    )
    qc_table = table(
        ["QC measure", "Purchase", "Before", "Transfers", "After", "Residual before", "Residual after"],
        [
            ["SEPM total", sep["delivered_purchase_cad_m3"], sep["procurement_before_transfers_cad_m3"], sep["transfers_total_cad_m3"], sep["procurement_after_transfers_cad_m3"], sep["residual_before_transfers_cad_m3"], sep["residual_after_transfers_cad_m3"]],
            ["Hardwood sciage", hardwood["delivered_purchase_cad_m3"], hardwood["procurement_before_transfers_cad_m3"], hardwood["transfers_total_cad_m3"], hardwood["procurement_after_transfers_cad_m3"], hardwood["residual_before_transfers_cad_m3"], hardwood["residual_after_transfers_cad_m3"]],
            ["Poplar trituration", poplar["delivered_purchase_cad_m3"], poplar["procurement_before_transfers_cad_m3"], poplar["transfers_total_cad_m3"], poplar["procurement_after_transfers_cad_m3"], poplar["residual_before_transfers_cad_m3"], poplar["residual_after_transfers_cad_m3"]],
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
            ["Volume-weighted stumpage (CAD/m3)", stumpage_stats.volume_weighted_stumpage_cad_m3],
            ["Median full bid (CAD/m3)", stumpage_stats.median_full_bid_cad_m3],
            ["Full-bid range (CAD/m3)", f"{stumpage_stats.min_full_bid_cad_m3:.4f} to {stumpage_stats.max_full_bid_cad_m3:.4f}"],
        ],
    )
    validation_table = table(
        ["Check", "Status", "Detail"],
        [[check["check"], check["status"], check["detail"]] for check in checks],
    )
    total_cells = sum(summary.cell_count for summary in series_summaries)
    total_reports = BC_REPORT_COUNT

    report = f"""# Net-Value Evidence v2d

| Field | Value |
|---|---|
| Document | `data/derived/national/harvest_basis/net_value_v2d/NET_VALUE_V2D_REPORT.md` |
| Date | 2026-11-21 |
| Status | Evidence layer. No merged-ledger, model, UI, or public OSeCAN change. |
| QC source | `data/derived/national/harvest_basis/net_value_benchmark/` |
| BC sources | `bc_log_market/`, `bcts_bc/`, `bc_appraisal_costs/` |
| Net value | QC `benchmark_only`; BC `not_estimated` |

**Decision summary.** The QC 2022 survey benchmark is referenced, not recomputed.
BC has observed mill-delivered log-price averages, observed BCTS standing-timber
stumpage, and published appraisal cost allowances. BC has **no** published
general logging (tree-to-truck) or truck-haul $/m3: both are embedded in the
estimated winning bid (EWB). Therefore BC `net_value_status=not_estimated` with
reason `missing_logging_and_haul_cost`. No BC partial net value is computed, and
no combined national net value exists.

## 1. Evidence labels

{evidence_table}

## 2. QC reference (not re-derived)

The QC benchmark is validated against its own manifest and source CSVs. Values
below are exact source copies; no QC number is recomputed into a new definition.

{qc_table}

Regional SEPM conditional residual range: **{min(regional_before):.4f} to {max(regional_before):.4f}**
before transfers and **{min(regional_after):.4f} to {max(regional_after):.4f}** after
transfers, holding the provincial SEPM purchase price constant. BMMB is a
separate nominal 2011-2015 delivered-sale revenue benchmark: weighted
**{bmmb["weighted_rate_cad_m3"]}** CAD/m3, median **{bmmb["median_rate_cad_m3"]}**
CAD/m3, {bmmb["sale_count"]} sales. It has no cost stack.

## 3. BC revenue/price benchmark

- {total_cells} period product cells from {total_reports} source reports; the detailed CSV
  carries annual and period rows and is not a total.
- Blank source cells are missing, not zero.
- 16 exact-zero, 7 below CAD 1/m3, and 17 above CAD 1,000/m3 cells are retained
  with flags and excluded from the primary ranges; they are not clipped or filled.
- Interior `weighted_average` rows (`is_aggregate=true`) are reported separately
  and never added to product cells.
- One-month and three-month vintages are separate series. Neither is annualized,
  and overlapping three-month windows are not summed with monthly reports.
- Coast old growth is a published blend of old and mixed growth.

### 3.1 Period price ranges by published vintage

{series_table}

Cross-grade/cross-species ranges are wide by construction. The detailed CSV
carries annual and period rows by region, grade or end use, and species.

### 3.2 BCTS observed stumpage

{stumpage_table}

Stumpage is a bid for standing timber, not delivered-log revenue. It is kept
separate from the mill-delivered log-price averages.

## 4. BC cost coverage and gap

{cost_table}

All 879 cost rows are `appraisal_allowance`, not observed contractor costs. The
covered components use different units (CAD/km, CAD/each, CAD/ha, CAD/m3) and
cannot be summed into a single $/m3 without road length, structure counts,
treated area, and harvest volume.

{stop}

## 5. QC + BC comparison (explicitly non-comparable)

{comparison_table}

## 6. Validation

All {len(checks)} checks passed:

{validation_table}

## 7. Non-claims

- Not an observed transaction net value.
- Not policy-grade; `policy_grade=false` on every row.
- Not a BC full net value and not a BC partial net value.
- Not a combined national net value.
- Not a mill margin; transformation and by-product revenue are excluded.
- Not a stumpage rate for QC and not a delivered-log price for BCTS.
- Not an observed contractor cost; appraisal allowances are policy inputs.
- Not merged into `forest_cost_revenue.csv`, the Forest LP, the UI, or OSeCAN.
- Not deflated; years and currency bases remain separate.
- Not a legal AAC, sustainable-supply estimate, or forecast.

## 8. What would unlock BC net value

1. A public or authorized general logging/tree-to-truck $/m3 and a general
   truck-haul $/m3, or a documented decomposition of the EWB into its cost
   components. The restricted ILCR/CCTMCS microdata were not used.
2. Site quantities to convert allowances: road km, bridge and culvert counts,
   treated hectares, and harvest volume by appraisal zone.
3. A region/grade/volume weighting for the log-price series and a matching
   cost vintage. Until then, only revenue, stumpage, and partial allowance
   coverage are reported.

## 9. Reproduction

```bash
.venv/bin/python -m workflows.national.harvest.net_value_v2d
.venv/bin/python -m pytest tests/test_net_value_v2d.py -q
.venv/bin/ruff check workflows/national/harvest/net_value_v2d.py tests/test_net_value_v2d.py
```

Outputs: `{SUMMARY_CSV}`, `{BC_REVENUE_CSV}`, `{BC_STUMPAGE_CSV}`,
`{BC_COST_CSV}`, `{QC_REFERENCE_CSV}`, `{SUMMARY_JSON}`, `{MANIFEST_JSON}`.
"""
    path.write_text(report, encoding="utf-8")


def build(output_dir: Path = DEFAULT_OUTPUT_DIR) -> BuildResult:
    output_dir.mkdir(parents=True, exist_ok=True)

    price_cells = parse_price_cells(BC_LOG_MARKET_DIR / "bc_log_market_prices.csv")
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
        raise NetValueV2DError("appraisal manifest row count changed")
    if appraisal_manifest.get("stats", {}).get("rows") != BC_APPRAISAL_ROW_COUNT:
        raise NetValueV2DError("appraisal manifest stats row count changed")
    if appraisal_manifest.get("observation_type") != EVIDENCE_APPRAISAL:
        raise NetValueV2DError("appraisal manifest observation type changed")
    if not all(
        spot.get("status") == "pass" for spot in appraisal_manifest.get("spot_checks", [])
    ):
        raise NetValueV2DError("appraisal spot checks are not all pass")
    cost_rows = build_cost_coverage(appraisal_costs, appraisal_coverage, appraisal_manifest)

    crown_rows, forest_sector_rows, ledger_row_count = parse_bcts_ledger(
        BCTS_DIR / "bcts_bid_ledger.csv"
    )
    bcts_results = parse_bcts_results(BCTS_DIR / "bcts_bid_results.csv")
    stumpage_sales, stumpage_stats = build_bcts_sales(
        crown_rows,
        forest_sector_rows,
        bcts_results,
        BCTS_DIR / "bcts_bid_ledger.csv",
    )
    stumpage_rows = tuple(
        {
            "stumpage_id": f"bc_bcts_{sale.sale_id.replace('BC-BCTS_', '')}",
            "sale_id": sale.sale_id,
            "auction_date": sale.auction_date,
            "location": sale.location,
            "region": "not_reported",
            "volume_m3": sale.volume_m3,
            "stumpage_base_cad_m3": sale.stumpage_base_cad_m3,
            "stumpage_bid_premium_cad_m3": sale.stumpage_bid_premium_cad_m3,
            "stumpage_total_cad_m3": sale.stumpage_total_cad_m3,
            "total_basis": sale.total_basis,
            "reported_winning_bid_cad_m3": sale.reported_winning_bid_cad_m3,
            "reported_upset_cad_m3": sale.reported_upset_cad_m3,
            "reported_bonus_cad_m3": sale.reported_bonus_cad_m3,
            "bidder_count": sale.bidder_count,
            "evidence_tier": EVIDENCE_OBSERVED_STUMPAGE,
            "price_basis": "standing_timber",
            "observation_type": "observed_auction_result",
            "currency_basis": "CAD_nominal_2026",
            "coverage": "selected_auction_sample",
            "is_additive": False,
            "policy_grade": False,
            "net_value_status": BC_NET_VALUE_STATUS,
            "net_value_reason": BC_NET_VALUE_REASON,
            "source_artifact": relative_path(BCTS_DIR / "bcts_bid_ledger.csv"),
            "notes": (
                "BCTS winning bid; standing timber, not delivered revenue. "
                + (
                    "Full upset plus bonus."
                    if sale.total_basis == "full_winning_bid"
                    else "Upset missing; premium-only lower bound."
                )
            ),
        }
        for sale in stumpage_sales
    )

    qc_rows, qc_summary = build_qc_reference(QC_BENCHMARK_DIR)
    summary_rows = build_summary_rows(qc_rows, series_summaries, stumpage_stats, cost_rows)

    checks = validate_outputs(
        price_cells=price_cells,
        price_summaries=price_summaries,
        price_rows=price_rows,
        stumpage_sales=stumpage_sales,
        stumpage_stats=stumpage_stats,
        cost_rows=cost_rows,
        qc_rows=qc_rows,
        summary_rows=summary_rows,
    )
    failed = [check for check in checks if check["status"] != "pass"]
    if failed:
        raise NetValueV2DError(f"validation failed: {failed}")

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
        "net_value_before_transfers_min_cad_m3",
        "net_value_before_transfers_max_cad_m3",
        "net_value_after_transfers_min_cad_m3",
        "net_value_after_transfers_max_cad_m3",
        "net_value_reason",
        "net_value_is_combined_national",
        "policy_grade",
        "is_additive",
        "source_artifact",
        "notes",
    )
    price_fields = tuple(price_rows[0]) if price_rows else ()
    stumpage_fields = (
        "stumpage_id",
        "sale_id",
        "auction_date",
        "location",
        "region",
        "volume_m3",
        "stumpage_base_cad_m3",
        "stumpage_bid_premium_cad_m3",
        "stumpage_total_cad_m3",
        "total_basis",
        "reported_winning_bid_cad_m3",
        "reported_upset_cad_m3",
        "reported_bonus_cad_m3",
        "bidder_count",
        "evidence_tier",
        "price_basis",
        "observation_type",
        "currency_basis",
        "coverage",
        "is_additive",
        "policy_grade",
        "net_value_status",
        "net_value_reason",
        "source_artifact",
        "notes",
    )
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
    qc_fields = tuple(qc_rows[0]) if qc_rows else ()

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
    write_csv(output_dir / BC_REVENUE_CSV, price_fields, price_rows)
    write_csv(output_dir / BC_STUMPAGE_CSV, stumpage_fields, stumpage_rows)
    write_csv(output_dir / BC_COST_CSV, cost_fields, cost_records)
    write_csv(output_dir / QC_REFERENCE_CSV, qc_fields, qc_rows)

    output_files = (
        (SUMMARY_CSV, len(summary_rows), "jurisdiction summary with separated evidence tiers"),
        (BC_REVENUE_CSV, len(price_rows), "annual and period BC price ranges"),
        (BC_STUMPAGE_CSV, len(stumpage_rows), "observed BCTS standing-timber stumpage"),
        (BC_COST_CSV, len(cost_rows), "BC appraisal allowance coverage and gaps"),
        (QC_REFERENCE_CSV, len(qc_rows), "validated QC benchmark references"),
    )
    output_records = [
        file_record(output_dir / name, role=role, rows=rows)
        for name, rows, role in output_files
    ]

    qc_manifest_inputs = [
        QC_BENCHMARK_DIR / "qc_net_value_benchmark.csv",
        QC_BENCHMARK_DIR / "qc_net_value_regional_range.csv",
        QC_BENCHMARK_DIR / "qc_net_value_sensitivity.csv",
        QC_BENCHMARK_DIR / "bmmb_historical_benchmark.csv",
        QC_BENCHMARK_DIR / "manifest.json",
    ]
    input_records = [
        file_record(path, role="QC reference source") for path in qc_manifest_inputs
    ] + [
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

    summary_payload: dict[str, Any] = {
        "artifact_version": "1.0.0",
        "dataset": "net_value_v2d",
        "status": "evidence_layer",
        "generated_utc": utc_now(),
        "scope": {
            "jurisdictions": ["QC", "BC"],
            "qc": "2022 MRNF survey benchmark, referenced",
            "bc": "2019-2026 log-market averages, 2026 BCTS stumpage, 2026 appraisal allowances",
            "combined_national_net_value": False,
        },
        "definitions": {
            "observed_price": "BC published mill-delivered log-market average, CAD/m3; a reported average, not a transaction.",
            "observed_stumpage": "BC BCTS winning bid for standing timber, CAD/m3 log; not delivered-log revenue.",
            "appraisal_allowance": "BC IAM/CAM tabular cost allowance; not an observed contractor cost, winning bid, or payment.",
            "survey_benchmark": "QC 2022 MRNF survey aggregate used as a benchmark; not an observed transaction.",
            "net_value": "Revenue minus a complete production cost stack at a consistent basis. No BC full stack exists.",
            "bc_net_value_status": BC_NET_VALUE_STATUS,
            "bc_net_value_reason": BC_NET_VALUE_REASON,
            "prohibited": "Do not subtract road/bridge/culvert/silviculture/specified-operation/TOA allowances from a log price and call it BC net value.",
        },
        "labels": {
            "evidence_tiers": sorted(EVIDENCE_TIERS),
            "policy_grade": False,
            "qc_net_value_status": QC_NET_VALUE_STATUS,
            "bc_net_value_status": BC_NET_VALUE_STATUS,
            "bc_net_value_reason": BC_NET_VALUE_REASON,
            "combined_national_net_value": False,
            "partial_bc_net_value_computed": False,
            "no_zero_fill": True,
            "one_month_three_month_additive": False,
        },
        "qc_reference": qc_summary,
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
                "zero_cad_m3": sum(1 for cell in price_cells if cell.price_plausibility == "zero_cad_m3"),
                "below_1_cad_m3": sum(1 for cell in price_cells if cell.price_plausibility == "below_1_cad_m3"),
                "below_1_including_zero": sum(
                    1 for cell in price_cells if cell.price_cad_m3 < 1.0
                ),
                "above_1000_cad_m3": sum(1 for cell in price_cells if cell.price_plausibility == "above_1000_cad_m3"),
            },
            "price_range_cad_m3": {
                "min_ok": min(
                    cell.price_cad_m3 for cell in price_cells if cell.price_plausibility == "ok"
                ),
                "max_ok": max(
                    cell.price_cad_m3 for cell in price_cells if cell.price_plausibility == "ok"
                ),
                "min_reported": min(cell.price_cad_m3 for cell in price_cells),
                "max_reported": max(cell.price_cad_m3 for cell in price_cells),
            },
            "series": price_series_summary,
            "interior_aggregate_cell_count": sum(1 for cell in price_cells if cell.is_aggregate),
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
                if row.component in {"logging", "truck_haul", "estimated_winning_bid"}
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
            "qc_status": QC_NET_VALUE_STATUS,
            "bc_status": BC_NET_VALUE_STATUS,
            "bc_reason": BC_NET_VALUE_REASON,
            "combined_national": False,
            "partial_bc_computed": False,
        },
        "qc_bc_comparison": {
            "comparable": False,
            "reason": "Different jurisdiction, year, evidence tier, price point, aggregation, and cost coverage.",
            "qc": "full 2022 survey procurement benchmark, benchmark_only",
            "bc": "partial appraisal coverage and revenue-only observations, not_estimated",
            "combined_national_net_value": False,
        },
        "non_claims": [
            "Not an observed transaction net value.",
            "Not policy-grade; policy_grade=false on every row.",
            "Not a BC full net value and not a BC partial net value.",
            "Not a combined national net value.",
            "Not a mill margin; transformation and by-product revenue are excluded.",
            "Not a stumpage rate for QC and not a delivered-log price for BCTS.",
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
            "QC and BC evidence are not comparable and are not combined.",
        ],
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "inputs": input_records,
        "outputs": output_records,
    }

    write_report(
        output_dir / REPORT_MD,
        summary_rows=summary_rows,
        series_summaries=series_summaries,
        stumpage_stats=stumpage_stats,
        cost_rows=cost_rows,
        qc_rows=qc_rows,
        checks=checks,
    )
    report_record = file_record(
        output_dir / REPORT_MD, role="human-readable evidence report"
    )
    summary_payload["outputs"] = output_records + [report_record]
    summary_payload["manifest_path"] = relative_path(output_dir / MANIFEST_JSON)
    write_json(output_dir / SUMMARY_JSON, summary_payload)
    summary_record = file_record(
        output_dir / SUMMARY_JSON, role="summary payload"
    )

    manifest_payload = {
        "artifact_version": "1.0.0",
        "dataset": "net_value_v2d",
        "status": "evidence_layer",
        "generated_utc": summary_payload["generated_utc"],
        "generator": {
            "script": "workflows/national/harvest/net_value_v2d.py",
            "command": "python -m workflows.national.harvest.net_value_v2d",
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
            "qc_status": QC_NET_VALUE_STATUS,
            "bc_status": BC_NET_VALUE_STATUS,
            "bc_reason": BC_NET_VALUE_REASON,
            "combined_national": False,
            "partial_bc_computed": False,
        },
    }
    write_json(output_dir / MANIFEST_JSON, manifest_payload)

    return BuildResult(
        output_dir=output_dir,
        summary_rows=tuple(summary_rows),
        price_rows=tuple(price_rows),
        stumpage_rows=tuple(stumpage_rows),
        cost_rows=tuple(cost_rows),
        qc_rows=tuple(qc_rows),
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
    print(f"wrote {len(result.price_rows)} BC price range rows")
    print(f"wrote {len(result.stumpage_rows)} BCTS stumpage rows")
    print(f"wrote {len(result.cost_rows)} BC cost coverage rows")
    print(f"wrote {len(result.qc_rows)} QC reference rows")
    print(f"validation: {len(result.checks)} checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
