"""DEPRECATED: build the old QC 2022 net-value benchmark and sensitivity tables.

This artifact is withdrawn and retained only for audit. Its "residual" and M CAD
net-value framing is superseded by ``procurement_benchmark_v1.py`` (QC
procurement cost spread plus BMMB historical benchmark) and
``forest_economics_v2d.py`` (separated forest-economics evidence layer, all net
values ``not_estimated``). Do not publish, cite, or rebuild this dataset as
current evidence.

The benchmark is deliberately narrow:

* the delivered wood purchase price comes from the 2022 Quebec MRNF survey
  Table 2 (``purchase.wood_purchase_delivered``);
* procurement costs come from the survey Table 20 synthesis rows, never from
  the overlapping component tables;
* the residual is computed twice, before and after payments to the State;
* BMMB delivered auction results are reported only as a separate nominal
  2011-2015 historical benchmark.

This module does not read or write the merged cost/revenue ledger, the Forest
LP, or the UI.  Every generated row is labelled ``evidence=survey_benchmark``
or ``evidence=benchmark_historical``, ``policy_grade=false`` and
``net_value_status=benchmark_only``.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.net_value_benchmark_v1
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

SURVEY_PATH = (
    HARVEST_BASIS_DIR / "qc_cost_survey_2022/qc_cost_revenue_survey.csv"
)
BMMB_SALES_PATH = (
    HARVEST_BASIS_DIR / "bmmb_delivered/bmmb_delivered_sales.csv"
)
ALLOCATION_DIR = HARVEST_BASIS_DIR / "provincial_allocation"

DEFAULT_OUTPUT_DIR = HARVEST_BASIS_DIR / "net_value_benchmark"

PROVINCIAL_CSV = "qc_net_value_benchmark.csv"
REGIONAL_CSV = "qc_net_value_regional_range.csv"
BMMB_CSV = "bmmb_historical_benchmark.csv"
SENSITIVITY_CSV = "qc_net_value_sensitivity.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NET_VALUE_BENCHMARK_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE_SURVEY = "survey_benchmark"
EVIDENCE_HISTORICAL = "benchmark_historical"
NET_VALUE_STATUS = "benchmark_only"
CURRENCY_BASIS_2022 = "CAD_nominal_2022"
CURRENCY_BASIS_BMMB = "CAD_nominal_2011_2015"

ARITHMETIC_TOLERANCE = 0.011
RATE_TOLERANCE = 0.0001

SURVEY_ROW_COUNT = 660
BMMB_SALE_COUNT = 18
BMMB_FULL_RATE_COUNT = 11
BMMB_PARTIAL_COUNT = 2

PROCUREMENT_COMPONENTS = (
    "harvest",
    "road",
    "haul",
    "hosting",
    "other_costs",
    "procurement_total_before_transfers",
    "transfers_total",
    "procurement_total_after_transfers",
)
COST_COMPONENTS = (
    "harvest",
    "road",
    "haul",
    "hosting",
    "other_costs",
)
REGIONAL_COMPONENTS = PROCUREMENT_COMPONENTS

ALLOCATION_CASES = (
    ("100m", 100_000_000.0),
    ("119m", 118_977_339.0),
    ("150m", 150_000_000.0),
)

SURVEY_REQUIRED_COLUMNS = {
    "source_id",
    "source_table",
    "survey_year",
    "region_code",
    "aggregation_level",
    "species_group",
    "species_label",
    "product_or_grade",
    "component_group",
    "component",
    "subcomponent",
    "value",
    "unit",
    "price_basis",
    "sample_n",
    "observation_kind",
    "confidentiality_flag",
    "notes",
}

BMMB_REQUIRED_COLUMNS = {
    "sale_id",
    "sale_year",
    "region_bmmb",
    "sale_type_family",
    "status",
    "awarded",
    "volume_m3",
    "total_cad",
    "price_cad_m3",
    "price_cad_m3_lower_bound",
    "partial_award",
    "currency_basis",
    "observed",
    "quality",
    "species_scope",
    "aggregation_level",
}


class BenchmarkError(RuntimeError):
    """Raised when the benchmark inputs violate a required invariant."""


@dataclass(frozen=True, slots=True)
class SpeciesScope:
    scope_id: str
    species_group: str
    species_label: str
    product_or_grade: str
    aggregation_level: str


PROVINCIAL_SCOPES = (
    SpeciesScope("qc_sepm_total", "sepm", "Sapin, épinettes, pin gris et mélèzes (SEPM)", "total", "species_total"),
    SpeciesScope("qc_feuillus_durs_sciage", "feuillus_durs", "Feuillus durs", "sciage", "species_product"),
    SpeciesScope("qc_feuillus_durs_trituration", "feuillus_durs", "Feuillus durs", "trituration", "species_product"),
    SpeciesScope("qc_feuillus_durs_total", "feuillus_durs", "Feuillus durs", "total", "species_total"),
    SpeciesScope("qc_peupliers_sciage", "peupliers", "Peupliers", "sciage", "species_product"),
    SpeciesScope("qc_peupliers_trituration", "peupliers", "Peupliers", "trituration", "species_product"),
    SpeciesScope("qc_peupliers_total", "peupliers", "Peupliers", "total", "species_total"),
    SpeciesScope("qc_pins_total", "pins", "Pins", "total", "species_total"),
    SpeciesScope("qc_thuyas_total", "thuyas", "Thuyas", "total", "species_total"),
)


@dataclass(frozen=True, slots=True)
class SurveyRow:
    source_id: str
    source_table: str
    region_code: str
    region_label: str
    aggregation_level: str
    species_group: str
    species_label: str
    product_or_grade: str
    component_group: str
    component: str
    subcomponent: str
    value: float
    unit: str
    price_basis: str
    sample_n: int | None
    observation_kind: str
    confidentiality_flag: str
    notes: str


@dataclass(frozen=True, slots=True)
class PurchasePrice:
    species_group: str
    species_label: str
    product_or_grade: str
    value: float
    source_table: str
    confidentiality_flag: str
    notes: str


@dataclass(frozen=True, slots=True)
class ProcurementSynthesis:
    species_group: str
    species_label: str
    product_or_grade: str
    harvest: float
    road: float
    haul: float
    hosting: float
    other_costs: float
    procurement_before_transfers: float
    transfers_total: float
    procurement_after_transfers: float
    source_table: str
    confidentiality_flag: str
    notes: str


@dataclass(frozen=True, slots=True)
class ProvincialBenchmark:
    scope: SpeciesScope
    delivered_purchase: float
    procurement_before_transfers: float | None
    transfers_total: float | None
    procurement_after_transfers: float | None
    residual_before_transfers: float | None
    residual_after_transfers: float | None
    residual_status: str
    confidentiality_flag: str
    three_respondent_flag: bool
    notes: str


@dataclass(frozen=True, slots=True)
class RegionalBenchmark:
    region_code: str
    region_label: str
    sample_n: int
    confidentiality_flag: str
    three_respondent_flag: bool
    procurement_before_transfers: float
    transfers_total: float
    procurement_after_transfers: float
    reference_purchase: float
    residual_before_transfers: float
    residual_after_transfers: float
    notes: str


@dataclass(frozen=True, slots=True)
class BMMBSale:
    sale_id: str
    sale_year: int
    region_bmmb: str
    awarded: bool
    volume_m3: float
    total_cad: float | None
    price_cad_m3: float | None
    price_cad_m3_lower_bound: float | None
    partial_award: bool
    observed: bool
    quality: str
    sale_type_family: str


@dataclass(frozen=True, slots=True)
class BMMBStats:
    aggregation_level: str
    sale_year: str
    region_bmmb: str
    sale_count: int
    awarded_count: int
    unsold_count: int
    observed_price_count: int
    full_rate_sale_count: int
    partial_award_sale_count: int
    volume_m3: float
    awarded_volume_m3: float
    observed_value_cad: float
    rate_computable_volume_m3: float
    rate_computable_value_cad: float
    weighted_rate_cad_m3: float | None
    median_rate_cad_m3: float | None
    unweighted_mean_rate_cad_m3: float | None
    min_rate_cad_m3: float | None
    max_rate_cad_m3: float | None
    partial_award_value_cad: float
    partial_lower_bound_min_cad_m3: float | None
    partial_lower_bound_max_cad_m3: float | None


@dataclass(frozen=True, slots=True)
class AllocationCase:
    case_id: str
    national_total_m3: float
    qc_harvest_m3: float
    observed_2024_qc_m3: float
    source_path: str


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    case: AllocationCase
    residual_before_transfers: float
    residual_after_transfers: float
    net_value_before_transfers_mcad: float
    net_value_after_transfers_mcad: float


@dataclass(frozen=True, slots=True)
class BuildResult:
    output_dir: Path
    provincial: tuple[ProvincialBenchmark, ...]
    regional: tuple[RegionalBenchmark, ...]
    bmmb_overall: BMMBStats
    bmmb_years: tuple[BMMBStats, ...]
    sensitivity: tuple[SensitivityRow, ...]
    checks: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BenchmarkError(f"missing input: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def require_columns(path: Path, rows: Sequence[Mapping[str, str]], required: Iterable[str]) -> None:
    if not rows:
        raise BenchmarkError(f"input has no data rows: {path}")
    present = set(rows[0])
    missing = set(required) - present
    if missing:
        raise BenchmarkError(f"{path} is missing columns: {sorted(missing)}")


def parse_finite_float(text: str, *, context: str) -> float:
    stripped = (text or "").strip()
    if stripped == "" or stripped.lower() in {"na", "n/a", "nan", "inf", "-inf", "none"}:
        raise BenchmarkError(f"expected a finite number for {context}, got {text!r}")
    value = float(stripped)
    if not math.isfinite(value):
        raise BenchmarkError(f"non-finite number for {context}: {text!r}")
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
        raise BenchmarkError(f"negative sample size for {context}: {value}")
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


def load_manifest_row_count(path: Path) -> int:
    if not path.is_file():
        raise BenchmarkError(f"missing provenance manifest: {path}")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    try:
        count = int(manifest["validation"]["row_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BenchmarkError(f"{path} does not declare validation.row_count") from exc
    return count


def parse_survey_rows(path: Path) -> tuple[SurveyRow, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, SURVEY_REQUIRED_COLUMNS)
    expected = load_manifest_row_count(path.with_name("manifest.json"))
    if len(raw_rows) != expected:
        raise BenchmarkError(
            f"survey row count {len(raw_rows)} does not match manifest {expected}"
        )
    parsed: list[SurveyRow] = []
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        parsed.append(
            SurveyRow(
                source_id=row["source_id"],
                source_table=row["source_table"],
                region_code=row["region_code"],
                region_label=row["region_label"],
                aggregation_level=row["aggregation_level"],
                species_group=row["species_group"],
                species_label=row["species_label"],
                product_or_grade=row["product_or_grade"],
                component_group=row["component_group"],
                component=row["component"],
                subcomponent=row["subcomponent"],
                value=parse_finite_float(row["value"], context=context),
                unit=row["unit"],
                price_basis=row["price_basis"],
                sample_n=parse_optional_int(row["sample_n"], context=context),
                observation_kind=row["observation_kind"],
                confidentiality_flag=row["confidentiality_flag"],
                notes=row["notes"],
            )
        )
    return tuple(parsed)


def select_purchase_prices(rows: Sequence[SurveyRow]) -> dict[tuple[str, str], PurchasePrice]:
    selected = [
        row
        for row in rows
        if row.source_table == "T2"
        and row.region_code == "QC"
        and row.aggregation_level == "provincial"
        and row.component == "wood_purchase_delivered"
    ]
    if len(selected) != len(PROVINCIAL_SCOPES):
        raise BenchmarkError(
            f"expected {len(PROVINCIAL_SCOPES)} T2 purchase rows, found {len(selected)}"
        )
    purchases: dict[tuple[str, str], PurchasePrice] = {}
    for row in selected:
        key = (row.species_group, row.product_or_grade)
        if key in purchases:
            raise BenchmarkError(f"duplicate T2 purchase row for {key}")
        if row.unit != "CAD/m3" or row.price_basis != "delivered_to_mill":
            raise BenchmarkError(
                f"unexpected T2 basis for {key}: {row.unit}/{row.price_basis}"
            )
        purchases[key] = PurchasePrice(
            species_group=row.species_group,
            species_label=row.species_label,
            product_or_grade=row.product_or_grade,
            value=row.value,
            source_table=row.source_table,
            confidentiality_flag=row.confidentiality_flag,
            notes=row.notes,
        )
    expected_keys = {(scope.species_group, scope.product_or_grade) for scope in PROVINCIAL_SCOPES}
    missing = expected_keys - set(purchases)
    if missing:
        raise BenchmarkError(f"missing T2 purchase rows: {sorted(missing)}")
    return purchases


def validate_t2_t21_purchase_consistency(rows: Sequence[SurveyRow]) -> int:
    t2 = {
        (row.species_group, row.product_or_grade): row.value
        for row in rows
        if row.source_table == "T2"
        and row.region_code == "QC"
        and row.aggregation_level == "provincial"
        and row.component == "wood_purchase_delivered"
    }
    t21 = {
        (row.species_group, row.product_or_grade): row.value
        for row in rows
        if row.source_table == "T21"
        and row.region_code == "QC"
        and row.aggregation_level == "provincial"
        and row.component == "wood_purchase_delivered"
    }
    overlapping = set(t2) & set(t21)
    if not overlapping:
        raise BenchmarkError("no overlapping T2/T21 wood purchase rows to reconcile")
    mismatches = {
        key: (t2[key], t21[key])
        for key in overlapping
        if not math.isclose(t2[key], t21[key], abs_tol=RATE_TOLERANCE)
    }
    if mismatches:
        raise BenchmarkError(f"T2/T21 purchase mismatch: {mismatches}")
    return len(overlapping)


def select_procurement_synthesis(
    rows: Sequence[SurveyRow],
) -> dict[tuple[str, str], ProcurementSynthesis]:
    grouped: dict[tuple[str, str], dict[str, SurveyRow]] = defaultdict(dict)
    for row in rows:
        if row.source_table != "T20":
            continue
        if row.region_code != "QC" or row.aggregation_level != "provincial":
            continue
        if row.component_group != "synthesis_procurement":
            continue
        key = (row.species_group, row.product_or_grade)
        if row.component in grouped[key]:
            raise BenchmarkError(f"duplicate T20 synthesis component for {key}: {row.component}")
        grouped[key][row.component] = row

    if len(grouped) != 3:
        raise BenchmarkError(f"expected 3 T20 synthesis groups, found {len(grouped)}")

    synthesis: dict[tuple[str, str], ProcurementSynthesis] = {}
    for key, components in grouped.items():
        missing = set(PROCUREMENT_COMPONENTS) - set(components)
        if missing:
            raise BenchmarkError(f"T20 group {key} is missing components: {sorted(missing)}")
        values = {name: components[name].value for name in PROCUREMENT_COMPONENTS}
        negative_components = {
            name: value for name, value in values.items() if value < 0
        }
        if negative_components:
            raise BenchmarkError(
                f"T20 group {key} has unexpected negative cost components: "
                f"{negative_components}"
            )
        computed_before = sum(values[name] for name in COST_COMPONENTS)
        if not math.isclose(
            computed_before,
            values["procurement_total_before_transfers"],
            abs_tol=ARITHMETIC_TOLERANCE,
        ):
            raise BenchmarkError(
                f"T20 group {key} cost components sum to {computed_before}, "
                f"not {values['procurement_total_before_transfers']}"
            )
        computed_after = (
            values["procurement_total_before_transfers"] + values["transfers_total"]
        )
        if not math.isclose(
            computed_after,
            values["procurement_total_after_transfers"],
            abs_tol=ARITHMETIC_TOLERANCE,
        ):
            raise BenchmarkError(
                f"T20 group {key} before + transfers = {computed_after}, "
                f"not {values['procurement_total_after_transfers']}"
            )
        flags = {components[name].confidentiality_flag for name in PROCUREMENT_COMPONENTS}
        flag = "three_respondents" if "three_respondents" in flags else "none"
        notes = "; ".join(
            sorted({components[name].notes for name in PROCUREMENT_COMPONENTS if components[name].notes})
        )
        first = components["harvest"]
        synthesis[key] = ProcurementSynthesis(
            species_group=first.species_group,
            species_label=first.species_label,
            product_or_grade=first.product_or_grade,
            harvest=values["harvest"],
            road=values["road"],
            haul=values["haul"],
            hosting=values["hosting"],
            other_costs=values["other_costs"],
            procurement_before_transfers=values["procurement_total_before_transfers"],
            transfers_total=values["transfers_total"],
            procurement_after_transfers=values["procurement_total_after_transfers"],
            source_table="T20",
            confidentiality_flag=flag,
            notes=notes,
        )
    return synthesis


def build_provincial_benchmarks(
    purchases: Mapping[tuple[str, str], PurchasePrice],
    synthesis: Mapping[tuple[str, str], ProcurementSynthesis],
) -> tuple[ProvincialBenchmark, ...]:
    benchmarks: list[ProvincialBenchmark] = []
    for scope in PROVINCIAL_SCOPES:
        key = (scope.species_group, scope.product_or_grade)
        purchase = purchases[key]
        procurement = synthesis.get(key)
        if procurement is None:
            note_parts = [
                ("Purchase price only; no matching T20 synthesis procurement row exists, "
                "so no residual is computable. Do not substitute component-table rows.")
            ]
            if purchase.confidentiality_flag == "three_respondents":
                note_parts.append("Three-respondent mean; interpret with caution.")
            benchmarks.append(
                ProvincialBenchmark(
                    scope=scope,
                    delivered_purchase=purchase.value,
                    procurement_before_transfers=None,
                    transfers_total=None,
                    procurement_after_transfers=None,
                    residual_before_transfers=None,
                    residual_after_transfers=None,
                    residual_status="not_computable_missing_synthesis_procurement",
                    confidentiality_flag=purchase.confidentiality_flag,
                    three_respondent_flag=purchase.confidentiality_flag
                    == "three_respondents",
                    notes="; ".join(note_parts),
                )
            )
            continue

        residual_before = purchase.value - procurement.procurement_before_transfers
        residual_after = purchase.value - procurement.procurement_after_transfers
        flags = {purchase.confidentiality_flag, procurement.confidentiality_flag}
        if "three_respondents" in flags:
            flag = "three_respondents"
        elif "suppressed_lt3" in flags:
            flag = "suppressed_lt3"
        else:
            flag = "none"
        note_parts = [
            "Residual = delivered purchase price - procurement total; not a mill margin.",
        ]
        if scope.aggregation_level == "species_total":
            note_parts.append(
                "Species total; not additive with product rows because volumes are not published."
            )
        if residual_before < 0 or residual_after < 0:
            note_parts.append(
                "Negative residual is reported as published arithmetic; it is not clipped to zero."
            )
        if flag == "three_respondents":
            note_parts.append("Three-respondent mean; interpret with caution.")
        benchmarks.append(
            ProvincialBenchmark(
                scope=scope,
                delivered_purchase=purchase.value,
                procurement_before_transfers=procurement.procurement_before_transfers,
                transfers_total=procurement.transfers_total,
                procurement_after_transfers=procurement.procurement_after_transfers,
                residual_before_transfers=residual_before,
                residual_after_transfers=residual_after,
                residual_status="computed",
                confidentiality_flag=flag,
                three_respondent_flag=flag == "three_respondents",
                notes="; ".join(note_parts),
            )
        )
    return tuple(benchmarks)


def select_regional_rows(rows: Sequence[SurveyRow]) -> dict[str, dict[str, SurveyRow]]:
    grouped: dict[str, dict[str, SurveyRow]] = defaultdict(dict)
    for row in rows:
        if row.source_id != "regional_2022":
            continue
        if row.aggregation_level != "regional" or row.species_group != "sepm":
            continue
        if row.component_group != "synthesis_procurement":
            continue
        if row.component in grouped[row.region_code]:
            raise BenchmarkError(
                f"duplicate regional synthesis component for {row.region_code}: {row.component}"
            )
        grouped[row.region_code][row.component] = row
    if len(grouped) != 7:
        raise BenchmarkError(f"expected 7 regional groups, found {len(grouped)}")
    for region, components in grouped.items():
        missing = set(REGIONAL_COMPONENTS) - set(components)
        if missing:
            raise BenchmarkError(f"regional group {region} is missing: {sorted(missing)}")
    return dict(grouped)


def build_regional_benchmarks(
    regional_rows: Mapping[str, Mapping[str, SurveyRow]],
    reference_purchase: float,
) -> tuple[RegionalBenchmark, ...]:
    benchmarks: list[RegionalBenchmark] = []
    for region_code, components in regional_rows.items():
        values = {name: components[name].value for name in REGIONAL_COMPONENTS}
        negative_components = {
            name: value for name, value in values.items() if value < 0
        }
        if negative_components:
            raise BenchmarkError(
                f"regional group {region_code} has unexpected negative cost "
                f"components: {negative_components}"
            )
        computed_before = sum(values[name] for name in COST_COMPONENTS)
        if not math.isclose(
            computed_before,
            values["procurement_total_before_transfers"],
            abs_tol=ARITHMETIC_TOLERANCE,
        ):
            raise BenchmarkError(
                f"regional group {region_code} components sum to {computed_before}, "
                f"not {values['procurement_total_before_transfers']}"
            )
        computed_after = (
            values["procurement_total_before_transfers"] + values["transfers_total"]
        )
        if not math.isclose(
            computed_after,
            values["procurement_total_after_transfers"],
            abs_tol=ARITHMETIC_TOLERANCE,
        ):
            raise BenchmarkError(
                f"regional group {region_code} before + transfers = {computed_after}, "
                f"not {values['procurement_total_after_transfers']}"
            )
        sample_n = components["harvest"].sample_n
        if sample_n is None:
            raise BenchmarkError(f"regional group {region_code} is missing sample_n")
        flags = {components[name].confidentiality_flag for name in REGIONAL_COMPONENTS}
        if "three_respondents" in flags:
            flag = "three_respondents"
        else:
            flag = "none"
        three_respondent_flag = flag == "three_respondents" or sample_n == 3
        benchmarks.append(
            RegionalBenchmark(
                region_code=region_code,
                region_label=components["harvest"].region_label,
                sample_n=sample_n,
                confidentiality_flag=flag,
                three_respondent_flag=three_respondent_flag,
                procurement_before_transfers=values["procurement_total_before_transfers"],
                transfers_total=values["transfers_total"],
                procurement_after_transfers=values["procurement_total_after_transfers"],
                reference_purchase=reference_purchase,
                residual_before_transfers=reference_purchase
                - values["procurement_total_before_transfers"],
                residual_after_transfers=reference_purchase
                - values["procurement_total_after_transfers"],
                notes=(
                    "Regional procurement is observed SEPM only; no regional delivered "
                    "purchase price is published. Residual holds the provincial SEPM "
                    "2022 purchase price constant and is a conditional range, not an "
                    "observed regional transaction net value."
                ),
            )
        )
    return tuple(benchmarks)


def parse_bmmb_sales(path: Path) -> tuple[BMMBSale, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, BMMB_REQUIRED_COLUMNS)
    sales: list[BMMBSale] = []
    seen_ids: set[str] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        sale_id = row["sale_id"]
        if sale_id in seen_ids:
            raise BenchmarkError(f"duplicate BMMB sale_id: {sale_id}")
        seen_ids.add(sale_id)
        if row["sale_type_family"] != "delivered_to_destination":
            raise BenchmarkError(
                f"non-delivered sale in delivered table: {sale_id} "
                f"({row['sale_type_family']})"
            )
        if row["quality"] != "NA":
            raise BenchmarkError(f"unexpected BMMB quality grade for {sale_id}: {row['quality']}")
        total_cad = parse_optional_float(row["total_cad"], context=context)
        price = parse_optional_float(row["price_cad_m3"], context=context)
        lower_bound = parse_optional_float(
            row["price_cad_m3_lower_bound"], context=context
        )
        partial_award = row["partial_award"] == "1"
        if price is not None and partial_award:
            raise BenchmarkError(f"partial-award sale has a full rate: {sale_id}")
        if lower_bound is not None and not partial_award:
            raise BenchmarkError(f"non-partial sale has a lower bound: {sale_id}")
        sales.append(
            BMMBSale(
                sale_id=sale_id,
                sale_year=int(row["sale_year"]),
                region_bmmb=row["region_bmmb"],
                awarded=row["awarded"] == "1",
                volume_m3=parse_finite_float(row["volume_m3"], context=context),
                total_cad=total_cad,
                price_cad_m3=price,
                price_cad_m3_lower_bound=lower_bound,
                partial_award=partial_award,
                observed=row["observed"] == "1",
                quality=row["quality"],
                sale_type_family=row["sale_type_family"],
            )
        )
    if len(sales) != BMMB_SALE_COUNT:
        raise BenchmarkError(f"expected {BMMB_SALE_COUNT} BMMB delivered sales, found {len(sales)}")
    return tuple(sales)


def compute_bmmb_stats(
    sales: Sequence[BMMBSale], *, aggregation_level: str, sale_year: str, region_bmmb: str
) -> BMMBStats:
    full_rate = [
        sale
        for sale in sales
        if sale.price_cad_m3 is not None and not sale.partial_award
    ]
    partial = [sale for sale in sales if sale.partial_award]
    rates = [sale.price_cad_m3 for sale in full_rate if sale.price_cad_m3 is not None]
    rate_volume = sum(sale.volume_m3 for sale in full_rate)
    rate_value = sum(sale.total_cad or 0.0 for sale in full_rate)
    awarded = [sale for sale in sales if sale.awarded]
    observed_value = sum(sale.total_cad or 0.0 for sale in awarded)
    partial_value = sum(sale.total_cad or 0.0 for sale in partial)
    lower_bounds = [
        sale.price_cad_m3_lower_bound
        for sale in partial
        if sale.price_cad_m3_lower_bound is not None
    ]
    return BMMBStats(
        aggregation_level=aggregation_level,
        sale_year=sale_year,
        region_bmmb=region_bmmb,
        sale_count=len(sales),
        awarded_count=len(awarded),
        unsold_count=len(sales) - len(awarded),
        observed_price_count=sum(sale.observed for sale in sales),
        full_rate_sale_count=len(full_rate),
        partial_award_sale_count=len(partial),
        volume_m3=sum(sale.volume_m3 for sale in sales),
        awarded_volume_m3=sum(sale.volume_m3 for sale in awarded),
        observed_value_cad=observed_value,
        rate_computable_volume_m3=rate_volume,
        rate_computable_value_cad=rate_value,
        weighted_rate_cad_m3=(rate_value / rate_volume) if rate_volume > 0 else None,
        median_rate_cad_m3=statistics.median(rates) if rates else None,
        unweighted_mean_rate_cad_m3=statistics.mean(rates) if rates else None,
        min_rate_cad_m3=min(rates) if rates else None,
        max_rate_cad_m3=max(rates) if rates else None,
        partial_award_value_cad=partial_value,
        partial_lower_bound_min_cad_m3=min(lower_bounds) if lower_bounds else None,
        partial_lower_bound_max_cad_m3=max(lower_bounds) if lower_bounds else None,
    )


def build_bmmb_stats(sales: Sequence[BMMBSale]) -> tuple[BMMBStats, tuple[BMMBStats, ...]]:
    overall = compute_bmmb_stats(
        sales,
        aggregation_level="delivered_all_years",
        sale_year="2011-2015",
        region_bmmb="all",
    )
    years = tuple(
        compute_bmmb_stats(
            [sale for sale in sales if sale.sale_year == year],
            aggregation_level="delivered_year",
            sale_year=str(year),
            region_bmmb="all",
        )
        for year in sorted({sale.sale_year for sale in sales})
    )
    if overall.full_rate_sale_count != BMMB_FULL_RATE_COUNT:
        raise BenchmarkError(
            f"expected {BMMB_FULL_RATE_COUNT} full-rate BMMB sales, "
            f"found {overall.full_rate_sale_count}"
        )
    if overall.partial_award_sale_count != BMMB_PARTIAL_COUNT:
        raise BenchmarkError(
            f"expected {BMMB_PARTIAL_COUNT} partial-award BMMB sales, "
            f"found {overall.partial_award_sale_count}"
        )
    if overall.weighted_rate_cad_m3 is None or not math.isclose(
        overall.weighted_rate_cad_m3, 38.2940, abs_tol=RATE_TOLERANCE
    ):
        raise BenchmarkError(
            f"BMMB weighted rate changed: {overall.weighted_rate_cad_m3}"
        )
    if overall.median_rate_cad_m3 is None or not math.isclose(
        overall.median_rate_cad_m3, 36.6668, abs_tol=RATE_TOLERANCE
    ):
        raise BenchmarkError(f"BMMB median rate changed: {overall.median_rate_cad_m3}")
    return overall, years


def load_allocation_cases(directory: Path) -> tuple[AllocationCase, ...]:
    cases: list[AllocationCase] = []
    for case_id, national_total in ALLOCATION_CASES:
        path = directory / f"{case_id}.csv"
        rows = read_csv_rows(path)
        if "province" not in rows[0] or "harvest_m3" not in rows[0]:
            raise BenchmarkError(f"{path} lacks province/harvest_m3 columns")
        qc_rows = [row for row in rows if row["province"] == "QC"]
        if len(qc_rows) != 1:
            raise BenchmarkError(f"{path} must contain exactly one QC row")
        qc = qc_rows[0]
        qc_harvest = parse_finite_float(qc["harvest_m3"], context=f"{path}:QC harvest")
        observed = parse_finite_float(
            qc["observed_2024_m3"], context=f"{path}:QC observed 2024"
        )
        if qc_harvest <= 0 or observed <= 0:
            raise BenchmarkError(f"{path} QC volumes must be positive")
        provincial_sum = sum(
            parse_finite_float(row["harvest_m3"], context=f"{path}:{row['province']}")
            for row in rows
        )
        if not math.isclose(provincial_sum, national_total, abs_tol=1.0):
            raise BenchmarkError(
                f"{path} provincial harvest does not sum to {national_total}"
            )
        cases.append(
            AllocationCase(
                case_id=case_id,
                national_total_m3=national_total,
                qc_harvest_m3=qc_harvest,
                observed_2024_qc_m3=observed,
                source_path=relative_path(path),
            )
        )
    return tuple(cases)


def build_sensitivity(
    cases: Sequence[AllocationCase], sep_benchmark: ProvincialBenchmark
) -> tuple[SensitivityRow, ...]:
    if sep_benchmark.residual_before_transfers is None:
        raise BenchmarkError("SEPM residual is required for the QC sensitivity")
    if sep_benchmark.residual_after_transfers is None:
        raise BenchmarkError("SEPM after-transfer residual is required for the QC sensitivity")
    rows: list[SensitivityRow] = []
    for case in cases:
        rows.append(
            SensitivityRow(
                case=case,
                residual_before_transfers=sep_benchmark.residual_before_transfers,
                residual_after_transfers=sep_benchmark.residual_after_transfers,
                net_value_before_transfers_mcad=(
                    case.qc_harvest_m3 * sep_benchmark.residual_before_transfers / 1_000_000.0
                ),
                net_value_after_transfers_mcad=(
                    case.qc_harvest_m3 * sep_benchmark.residual_after_transfers / 1_000_000.0
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


def validate_results(
    *,
    survey_rows: Sequence[SurveyRow],
    provincial: Sequence[ProvincialBenchmark],
    regional: Sequence[RegionalBenchmark],
    bmmb_sales: Sequence[BMMBSale],
    bmmb_overall: BMMBStats,
    bmmb_years: Sequence[BMMBStats],
    sensitivity: Sequence[SensitivityRow],
    t2_t21_matches: int,
) -> tuple[dict[str, Any], ...]:
    checks: list[dict[str, Any]] = []

    add_check(
        checks,
        "survey_row_count",
        len(survey_rows) == SURVEY_ROW_COUNT,
        f"{len(survey_rows)} rows (expected {SURVEY_ROW_COUNT})",
    )
    add_check(
        checks,
        "t2_t21_purchase_consistency",
        t2_t21_matches >= 8,
        f"{t2_t21_matches} overlapping purchase rows agree",
    )

    computed = [row for row in provincial if row.residual_status == "computed"]
    add_check(
        checks,
        "provincial_residual_count",
        len(computed) == 3,
        f"{len(computed)} computable residuals of {len(provincial)} scopes",
    )
    add_check(
        checks,
        "provincial_residual_recompute",
        all(
            math.isclose(
                row.residual_before_transfers,
                row.delivered_purchase - row.procurement_before_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.residual_after_transfers,
                row.delivered_purchase - row.procurement_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in computed
        ),
        "residual_before = purchase - procurement_before; residual_after = purchase - procurement_after",
    )
    add_check(
        checks,
        "provincial_transfer_identity",
        all(
            math.isclose(
                row.procurement_before_transfers + row.transfers_total,
                row.procurement_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.residual_before_transfers - row.residual_after_transfers,
                row.transfers_total,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in computed
        ),
        "procurement_before + transfers = procurement_after and residual_before - residual_after = transfers",
    )
    negative = {
        row.scope.scope_id
        for row in computed
        if (row.residual_before_transfers or 0.0) < 0
        or (row.residual_after_transfers or 0.0) < 0
    }
    add_check(
        checks,
        "negative_residuals_documented",
        negative == {"qc_peupliers_trituration", "qc_feuillus_durs_sciage"},
        f"negative residuals: {sorted(negative)}",
    )
    add_check(
        checks,
        "provincial_no_nonfinite",
        all(
            value is None or math.isfinite(value)
            for row in provincial
            for value in (
                row.delivered_purchase,
                row.procurement_before_transfers,
                row.transfers_total,
                row.procurement_after_transfers,
                row.residual_before_transfers,
                row.residual_after_transfers,
            )
        ),
        "all provincial values are finite or explicitly blank",
    )

    add_check(
        checks,
        "regional_group_count",
        len(regional) == 7,
        f"{len(regional)} regional groups",
    )
    add_check(
        checks,
        "regional_reference_price",
        all(
            math.isclose(row.reference_purchase, 107.22, abs_tol=RATE_TOLERANCE)
            for row in regional
        ),
        "provincial SEPM purchase price held at 107.22 CAD/m3",
    )
    add_check(
        checks,
        "regional_residual_recompute",
        all(
            math.isclose(
                row.residual_before_transfers,
                row.reference_purchase - row.procurement_before_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.residual_after_transfers,
                row.reference_purchase - row.procurement_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in regional
        ),
        "regional residual uses the same reference purchase price",
    )
    add_check(
        checks,
        "regional_three_respondent_flag",
        any(row.three_respondent_flag for row in regional),
        "sample_n=3 regional rows retain a three-respondent flag",
    )

    add_check(
        checks,
        "bmmb_sale_count",
        bmmb_overall.sale_count == BMMB_SALE_COUNT,
        f"{bmmb_overall.sale_count} delivered sales",
    )
    add_check(
        checks,
        "bmmb_full_rate_count",
        bmmb_overall.full_rate_sale_count == BMMB_FULL_RATE_COUNT,
        f"{bmmb_overall.full_rate_sale_count} full-rate sales",
    )
    add_check(
        checks,
        "bmmb_partial_award_count",
        bmmb_overall.partial_award_sale_count == BMMB_PARTIAL_COUNT,
        f"{bmmb_overall.partial_award_sale_count} partial-award sales excluded from the rate",
    )
    add_check(
        checks,
        "bmmb_weighted_rate",
        bmmb_overall.weighted_rate_cad_m3 is not None
        and math.isclose(
            bmmb_overall.weighted_rate_cad_m3, 38.2940, abs_tol=RATE_TOLERANCE
        ),
        f"{bmmb_overall.weighted_rate_cad_m3:.4f} CAD/m3",
    )
    add_check(
        checks,
        "bmmb_median_rate",
        bmmb_overall.median_rate_cad_m3 is not None
        and math.isclose(
            bmmb_overall.median_rate_cad_m3, 36.6668, abs_tol=RATE_TOLERANCE
        ),
        f"{bmmb_overall.median_rate_cad_m3:.4f} CAD/m3",
    )
    add_check(
        checks,
        "bmmb_no_species_or_grade_allocation",
        all(
            sale.quality == "NA"
            and sale.sale_type_family == "delivered_to_destination"
            for sale in bmmb_sales
        ),
        "all delivered sale prices are lot totals; no species/grade split exists",
    )
    add_check(
        checks,
        "bmmb_year_rows",
        len(bmmb_years) == 4,
        f"{len(bmmb_years)} sale years (2011, 2012, 2014, 2015)",
    )
    add_check(
        checks,
        "bmmb_2012_no_full_rate",
        next(row for row in bmmb_years if row.sale_year == "2012").full_rate_sale_count
        == 0,
        "2012 has partial awards only; no full rate is reported",
    )

    add_check(
        checks,
        "sensitivity_case_count",
        len(sensitivity) == len(ALLOCATION_CASES),
        f"{len(sensitivity)} allocation cases",
    )
    add_check(
        checks,
        "sensitivity_arithmetic",
        all(
            math.isclose(
                row.net_value_before_transfers_mcad,
                row.case.qc_harvest_m3 * row.residual_before_transfers / 1_000_000.0,
                abs_tol=0.0001,
            )
            and math.isclose(
                row.net_value_after_transfers_mcad,
                row.case.qc_harvest_m3 * row.residual_after_transfers / 1_000_000.0,
                abs_tol=0.0001,
            )
            for row in sensitivity
        ),
        "M CAD = QC m3 x CAD/m3 / 1,000,000",
    )

    add_check(
        checks,
        "labels",
        all(
            row.residual_status in {"computed", "not_computable_missing_synthesis_procurement"}
            for row in provincial
        ),
        "provincial residual statuses are explicit",
    )
    failed = [check for check in checks if check["status"] != "pass"]
    if failed:
        details = "; ".join(f"{check['check']}: {check['detail']}" for check in failed)
        raise BenchmarkError(f"benchmark validation failed: {details}")
    return tuple(checks)


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if not math.isfinite(value):
            raise BenchmarkError(f"refusing to write non-finite value: {value}")
        return f"{value:.4f}"
    return str(value)


def write_csv(
    path: Path,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            unexpected = set(row) - set(columns)
            if unexpected:
                raise BenchmarkError(f"{path.name} row has unexpected fields: {sorted(unexpected)}")
            writer.writerow({column: format_cell(row.get(column)) for column in columns})


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def provincial_csv_rows(
    benchmarks: Sequence[ProvincialBenchmark],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in benchmarks:
        rows.append(
            {
                "benchmark_id": f"qc_2022_{row.scope.scope_id}",
                "geography": "Quebec",
                "region_code": "QC",
                "species_group": row.scope.species_group,
                "species_label": row.scope.species_label,
                "product_or_grade": row.scope.product_or_grade,
                "aggregation_level": row.scope.aggregation_level,
                "delivered_purchase_cad_m3": row.delivered_purchase,
                "procurement_before_transfers_cad_m3": row.procurement_before_transfers,
                "transfers_total_cad_m3": row.transfers_total,
                "procurement_after_transfers_cad_m3": row.procurement_after_transfers,
                "residual_before_transfers_cad_m3": row.residual_before_transfers,
                "residual_after_transfers_cad_m3": row.residual_after_transfers,
                "residual_status": row.residual_status,
                "unit": "CAD/m3",
                "price_year": 2022,
                "currency_basis": CURRENCY_BASIS_2022,
                "survey_year": 2022,
                "purchase_source_table": "T2",
                "procurement_source_table": "T20" if row.procurement_before_transfers is not None else "",
                "price_basis": "delivered_to_mill",
                "sample_n": None,
                "confidentiality_flag": row.confidentiality_flag,
                "three_respondent_flag": row.three_respondent_flag,
                "is_additive": False,
                "evidence": EVIDENCE_SURVEY,
                "policy_grade": False,
                "net_value_status": NET_VALUE_STATUS,
                "observation_type": "survey_aggregate",
                "notes": row.notes,
            }
        )
    return rows


def regional_csv_rows(
    benchmarks: Sequence[RegionalBenchmark],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in benchmarks:
        rows.append(
            {
                "regional_benchmark_id": f"qc_2022_regional_sepm_{row.region_code}",
                "region_code": row.region_code,
                "region_label": row.region_label,
                "statistic": "region_value",
                "aggregation_level": "regional",
                "sample_n": row.sample_n,
                "confidentiality_flag": row.confidentiality_flag,
                "three_respondent_flag": row.three_respondent_flag,
                "procurement_before_transfers_cad_m3": row.procurement_before_transfers,
                "transfers_total_cad_m3": row.transfers_total,
                "procurement_after_transfers_cad_m3": row.procurement_after_transfers,
                "reference_purchase_cad_m3": row.reference_purchase,
                "residual_before_transfers_cad_m3": row.residual_before_transfers,
                "residual_after_transfers_cad_m3": row.residual_after_transfers,
                "residual_price_assumption": "provincial_sepm_delivered_purchase_held_constant",
                "unit": "CAD/m3",
                "price_year": 2022,
                "currency_basis": CURRENCY_BASIS_2022,
                "evidence": EVIDENCE_SURVEY,
                "policy_grade": False,
                "net_value_status": NET_VALUE_STATUS,
                "is_additive": False,
                "notes": row.notes,
            }
        )

    def bound_row(kind: str) -> dict[str, Any]:
        picker = min if kind == "min" else max
        before = picker(benchmarks, key=lambda item: item.procurement_before_transfers)
        after = picker(benchmarks, key=lambda item: item.procurement_after_transfers)
        residual_before = picker(benchmarks, key=lambda item: item.residual_before_transfers)
        residual_after = picker(benchmarks, key=lambda item: item.residual_after_transfers)
        return {
            "regional_benchmark_id": f"qc_2022_regional_sepm_{kind}",
            "region_code": f"QCSURVEY7_{kind.upper()}",
            "region_label": f"Seven published regional groups ({kind})",
            "statistic": kind,
            "aggregation_level": "regional_range_bound",
            "sample_n": None,
            "confidentiality_flag": "derived",
            "three_respondent_flag": False,
            "procurement_before_transfers_cad_m3": before.procurement_before_transfers,
            "transfers_total_cad_m3": picker(
                benchmarks, key=lambda item: item.transfers_total
            ).transfers_total,
            "procurement_after_transfers_cad_m3": after.procurement_after_transfers,
            "reference_purchase_cad_m3": 107.22,
            "residual_before_transfers_cad_m3": residual_before.residual_before_transfers,
            "residual_after_transfers_cad_m3": residual_after.residual_after_transfers,
            "residual_price_assumption": "provincial_sepm_delivered_purchase_held_constant",
            "unit": "CAD/m3",
            "price_year": 2022,
            "currency_basis": CURRENCY_BASIS_2022,
            "evidence": EVIDENCE_SURVEY,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "is_additive": False,
            "notes": (
                f"Bound over seven regional SEPM groups; procurement-before bound region "
                f"{before.region_code}, procurement-after bound region {after.region_code}. "
                "Residual is conditional on the provincial purchase price."
            ),
        }

    rows.extend((bound_row("min"), bound_row("max")))
    return rows


def bmmb_csv_rows(
    overall: BMMBStats, years: Sequence[BMMBStats]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for stats in (overall, *years):
        rows.append(
            {
                "benchmark_id": f"bmmb_delivered_{stats.aggregation_level}_{stats.sale_year}",
                "aggregation_level": stats.aggregation_level,
                "sale_year": stats.sale_year,
                "region_bmmb": stats.region_bmmb,
                "sale_count": stats.sale_count,
                "awarded_count": stats.awarded_count,
                "unsold_count": stats.unsold_count,
                "observed_price_count": stats.observed_price_count,
                "full_rate_sale_count": stats.full_rate_sale_count,
                "partial_award_sale_count": stats.partial_award_sale_count,
                "volume_m3": stats.volume_m3,
                "awarded_volume_m3": stats.awarded_volume_m3,
                "observed_value_cad": stats.observed_value_cad,
                "rate_computable_volume_m3": stats.rate_computable_volume_m3,
                "rate_computable_value_cad": stats.rate_computable_value_cad,
                "weighted_rate_cad_m3": stats.weighted_rate_cad_m3,
                "median_rate_cad_m3": stats.median_rate_cad_m3,
                "unweighted_mean_rate_cad_m3": stats.unweighted_mean_rate_cad_m3,
                "min_rate_cad_m3": stats.min_rate_cad_m3,
                "max_rate_cad_m3": stats.max_rate_cad_m3,
                "partial_award_value_cad": stats.partial_award_value_cad,
                "partial_lower_bound_min_cad_m3": stats.partial_lower_bound_min_cad_m3,
                "partial_lower_bound_max_cad_m3": stats.partial_lower_bound_max_cad_m3,
                "price_year_basis": "nominal_2011_2015",
                "currency_basis": CURRENCY_BASIS_BMMB,
                "deflator_applied": False,
                "species_scope": "ALL_SPECIES_MIXED",
                "quality": "NA",
                "grade_split": False,
                "species_allocation": False,
                "not_stumpage": True,
                "destination_reported": False,
                "service_bundle_published": False,
                "evidence": EVIDENCE_HISTORICAL,
                "policy_grade": False,
                "net_value_status": NET_VALUE_STATUS,
                "observation_type": "observed_sale_price",
                "notes": (
                    "Nominal CAD, 2011-2015; 18 delivered sales; 11 full rates; "
                    "2 partial awards excluded from the rate; no species or grade split; "
                    "no deflation; not a 2022 price; not stumpage and not a confirmed mill-gate price."
                ),
            }
        )
    return rows


def sensitivity_csv_rows(rows: Sequence[SensitivityRow]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": row.case.case_id,
            "national_total_m3": row.case.national_total_m3,
            "qc_harvest_m3": row.case.qc_harvest_m3,
            "observed_2024_qc_m3": row.case.observed_2024_qc_m3,
            "volume_source": row.case.source_path,
            "volume_mapping": f"{row.case.case_id}.csv:QC:harvest_m3",
            "volume_basis": "total_provincial_harvest_screening_allocation",
            "species_mix_assumption": "all_qc_volume_at_sepm_survey_residual",
            "residual_before_transfers_cad_m3": row.residual_before_transfers,
            "residual_after_transfers_cad_m3": row.residual_after_transfers,
            "qc_net_value_before_transfers_mcad": row.net_value_before_transfers_mcad,
            "qc_net_value_after_transfers_mcad": row.net_value_after_transfers_mcad,
            "evidence": EVIDENCE_SURVEY,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "notes": (
                "Sensitivity only. The allocation volume is a screening total, not an "
                "observed QC harvest or a forecast; no species mix is available, and the "
                "survey is not volume-weighted to the provincial population."
            ),
        }
        for row in rows
    ]


def _provincial_dict(row: ProvincialBenchmark) -> dict[str, Any]:
    return {
        "scope_id": row.scope.scope_id,
        "species_group": row.scope.species_group,
        "product_or_grade": row.scope.product_or_grade,
        "delivered_purchase_cad_m3": round(row.delivered_purchase, 4),
        "procurement_before_transfers_cad_m3": (
            round(row.procurement_before_transfers, 4)
            if row.procurement_before_transfers is not None
            else None
        ),
        "transfers_total_cad_m3": (
            round(row.transfers_total, 4) if row.transfers_total is not None else None
        ),
        "procurement_after_transfers_cad_m3": (
            round(row.procurement_after_transfers, 4)
            if row.procurement_after_transfers is not None
            else None
        ),
        "residual_before_transfers_cad_m3": (
            round(row.residual_before_transfers, 4)
            if row.residual_before_transfers is not None
            else None
        ),
        "residual_after_transfers_cad_m3": (
            round(row.residual_after_transfers, 4)
            if row.residual_after_transfers is not None
            else None
        ),
        "residual_status": row.residual_status,
        "confidentiality_flag": row.confidentiality_flag,
        "three_respondent_flag": row.three_respondent_flag,
    }


def build_summary(
    *,
    generated_utc: str,
    provincial: Sequence[ProvincialBenchmark],
    regional: Sequence[RegionalBenchmark],
    bmmb_overall: BMMBStats,
    bmmb_years: Sequence[BMMBStats],
    sensitivity: Sequence[SensitivityRow],
    checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    before_min = min(row.procurement_before_transfers for row in regional)
    before_max = max(row.procurement_before_transfers for row in regional)
    after_min = min(row.procurement_after_transfers for row in regional)
    after_max = max(row.procurement_after_transfers for row in regional)
    residual_before_values = [row.residual_before_transfers for row in regional]
    residual_after_values = [row.residual_after_transfers for row in regional]
    negative_after = sorted(
        row.region_code for row in regional if row.residual_after_transfers < 0
    )
    three_respondent_regions = sorted(
        row.region_code for row in regional if row.three_respondent_flag
    )
    return {
        "artifact_version": "1.0.0",
        "dataset": "qc_net_value_benchmark",
        "status": "benchmark_only",
        "generated_utc": generated_utc,
        "scope": {
            "jurisdiction": "QC",
            "survey_year": 2022,
            "price_basis": "delivered_to_mill",
            "unit": "CAD/m3",
            "currency": "CAD_nominal",
        },
        "definitions": {
            "delivered_purchase": "Survey T2 purchase.wood_purchase_delivered: delivered wood purchase price to mill, CAD/m3.",
            "procurement_before_transfers": "Survey T20 synthesis_procurement.procurement_total_before_transfers: harvest + road + haul + hosting + other costs.",
            "transfers_total": "Survey T20 synthesis_procurement.transfers_total: payments to the State.",
            "procurement_after_transfers": "Survey T20 synthesis_procurement.procurement_total_after_transfers: before-transfer procurement + transfers.",
            "residual_before_transfers": "delivered_purchase - procurement_before_transfers. Pre-Crown surplus, not a mill margin.",
            "residual_after_transfers": "delivered_purchase - procurement_after_transfers. Forest-sector residual after Crown transfers.",
            "identity": "pre-Crown surplus = forest-sector residual + transfers; equivalently residual_before = residual_after + transfers_total.",
        },
        "labels": {
            "evidence_values": [EVIDENCE_SURVEY, EVIDENCE_HISTORICAL],
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "warning": "No row is an observed transaction net value or a policy result.",
        },
        "provincial_benchmark": [_provincial_dict(row) for row in provincial],
        "regional_range": {
            "region_count": len(regional),
            "reference_purchase_cad_m3": 107.22,
            "procurement_before_transfers_min_cad_m3": round(before_min, 4),
            "procurement_before_transfers_max_cad_m3": round(before_max, 4),
            "procurement_after_transfers_min_cad_m3": round(after_min, 4),
            "procurement_after_transfers_max_cad_m3": round(after_max, 4),
            "residual_before_transfers_min_cad_m3": round(min(residual_before_values), 4),
            "residual_before_transfers_max_cad_m3": round(max(residual_before_values), 4),
            "residual_after_transfers_min_cad_m3": round(min(residual_after_values), 4),
            "residual_after_transfers_max_cad_m3": round(max(residual_after_values), 4),
            "negative_after_transfer_regions": negative_after,
            "three_respondent_regions": three_respondent_regions,
            "caveat": "Regional purchase prices are not published. Residuals hold the provincial SEPM purchase price constant and are conditional, not observed regional net values.",
        },
        "bmmb_historical_benchmark": {
            "sale_count": bmmb_overall.sale_count,
            "awarded_count": bmmb_overall.awarded_count,
            "full_rate_sale_count": bmmb_overall.full_rate_sale_count,
            "partial_award_sale_count": bmmb_overall.partial_award_sale_count,
            "years": [row.sale_year for row in bmmb_years],
            "volume_m3": round(bmmb_overall.volume_m3, 4),
            "rate_computable_volume_m3": round(bmmb_overall.rate_computable_volume_m3, 4),
            "weighted_rate_cad_m3": round(bmmb_overall.weighted_rate_cad_m3, 4),
            "median_rate_cad_m3": round(bmmb_overall.median_rate_cad_m3, 4),
            "min_rate_cad_m3": round(bmmb_overall.min_rate_cad_m3, 4),
            "max_rate_cad_m3": round(bmmb_overall.max_rate_cad_m3, 4),
            "currency_basis": CURRENCY_BASIS_BMMB,
            "deflator_applied": False,
            "grade_split": False,
            "species_allocation": False,
            "caveat": "Nominal 2011-2015, 18 delivered sales, no grade split. Do not deflate or compare as a 2022 price.",
        },
        "qc_total_sensitivity": {
            "computed": True,
            "reason": "QC harvest volumes are clearly mapped from the provincial allocation screening cases; the survey SEPM residual is applied to each candidate total.",
            "cases": [
                {
                    "case_id": row.case.case_id,
                    "national_total_m3": row.case.national_total_m3,
                    "qc_harvest_m3": row.case.qc_harvest_m3,
                    "observed_2024_qc_m3": row.case.observed_2024_qc_m3,
                    "volume_source": row.case.source_path,
                    "qc_net_value_before_transfers_mcad": round(
                        row.net_value_before_transfers_mcad, 4
                    ),
                    "qc_net_value_after_transfers_mcad": round(
                        row.net_value_after_transfers_mcad, 4
                    ),
                }
                for row in sensitivity
            ],
            "before_transfers_range_mcad": [
                round(min(row.net_value_before_transfers_mcad for row in sensitivity), 4),
                round(max(row.net_value_before_transfers_mcad for row in sensitivity), 4),
            ],
            "after_transfers_range_mcad": [
                round(min(row.net_value_after_transfers_mcad for row in sensitivity), 4),
                round(max(row.net_value_after_transfers_mcad for row in sensitivity), 4),
            ],
            "caveat": "Sensitivity only: all QC volume is assumed at the SEPM survey residual, with no species mix, no volume weighting, and a screening allocation total rather than an observed harvest.",
        },
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "non_claims": [
            "Not an observed transaction net value.",
            "Not a policy result and not policy-grade.",
            "Not a mill margin; transformation and lumber revenue are excluded.",
            "Not a stumpage rate.",
            "Not a current or representative BMMB price.",
            "Not an estimate of the QC provincial harvest population.",
            "Not merged into the forest cost/revenue ledger, Forest LP, or UI.",
            "Not deflated; BMMB values remain nominal 2011-2015.",
            "Not species- or grade-allocated.",
        ],
        "limitations": [
            "Survey aggregates only; no mill-level or respondent-level values.",
            "Provincial survey means are not volume-weighted to the provincial harvest.",
            "Regional data cover SEPM only and publish no regional purchase price.",
            "Three-respondent means are statistically fragile; regional R09 has sample_n=3.",
            "Pine and cedar purchase prices are three-respondent means.",
            "No residual is computable for pine, cedar, or species totals without matching T20 synthesis rows.",
            "Hardwood and poplar residuals are product-specific, not a species total.",
            "The poplar trituration residual is negative and is reported unclipped.",
            "The QC sensitivity assumes all volume is SEPM and uses screening allocation volumes, not observed harvest.",
            "BMMB delivered sales end in 2015 and do not publish species, grade, destination, or service-bundle detail.",
        ],
    }


def write_report(
    path: Path,
    *,
    generated_utc: str,
    provincial: Sequence[ProvincialBenchmark],
    regional: Sequence[RegionalBenchmark],
    bmmb_overall: BMMBStats,
    bmmb_years: Sequence[BMMBStats],
    sensitivity: Sequence[SensitivityRow],
    checks: Sequence[Mapping[str, Any]],
) -> None:
    def fmt(value: float | None, digits: int = 2) -> str:
        return "—" if value is None else f"{value:.{digits}f}"

    by_scope = {row.scope.scope_id: row for row in provincial}
    sep = by_scope["qc_sepm_total"]
    hardwood = by_scope["qc_feuillus_durs_sciage"]
    poplar = by_scope["qc_peupliers_trituration"]

    before_min = min(row.procurement_before_transfers for row in regional)
    before_max = max(row.procurement_before_transfers for row in regional)
    after_min = min(row.procurement_after_transfers for row in regional)
    after_max = max(row.procurement_after_transfers for row in regional)
    residual_before_min = min(row.residual_before_transfers for row in regional)
    residual_before_max = max(row.residual_before_transfers for row in regional)
    residual_after_min = min(row.residual_after_transfers for row in regional)
    residual_after_max = max(row.residual_after_transfers for row in regional)
    negative_after_regions = sorted(
        row.region_code for row in regional if row.residual_after_transfers < 0
    )

    lines = [
        "# QC net-value benchmark — 2022 survey and BMMB historical check",
        "",
        (f"**Status:** benchmark only. Generated {generated_utc}. "
        "`evidence=survey_benchmark` or `benchmark_historical`; "
        "`policy_grade=false`; `net_value_status=benchmark_only`."),
        "",
        ("This note reports a **forest-sector residual** at the mill gate, using the "
        "2022 Quebec MRNF survey synthesis rows. It is not an observed transaction "
        "net value and not a policy result. It does not touch the merged ledger, "
        "the Forest LP objective, or the UI."),
        "",
        "## Definitions",
        "",
        "| Term | Definition |",
        "|---|---|",
        "| Delivered purchase | Survey `purchase.wood_purchase_delivered`, T2, CAD/m3 delivered to mill. |",
        "| Procurement before transfers | Survey `synthesis_procurement.procurement_total_before_transfers`, T20 = harvest + road + haul + hosting + other. |",
        "| Transfers | Survey `synthesis_procurement.transfers_total`, T20 = payments to the State. |",
        "| Procurement after transfers | Survey `synthesis_procurement.procurement_total_after_transfers`, T20. |",
        "| Residual before transfers | `delivered purchase - procurement before transfers` (pre-Crown surplus). |",
        "| Residual after transfers | `delivered purchase - procurement after transfers` (forest-sector residual). |",
        "",
        ("The two residuals are linked by `residual_before = residual_after + transfers`. "
        "This is the ledger identity `pre-Crown surplus = forest-sector net value + Crown revenue` "
        "on the survey's 2022 basis."),
        "",
        ("Transformation, lumber, and by-product revenue are **not** netted against log "
        "costs. That would produce a mill margin, not forest net value."),
        "",
        "## Provincial benchmark (2022, CAD/m3 nominal)",
        "",
        "| Species | Product | Purchase | Procurement before | Transfers | Procurement after | Residual before | Residual after | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    labels = {
        "sepm": "SEPM",
        "feuillus_durs": "Hardwood",
        "peupliers": "Poplar",
        "pins": "Pine",
        "thuyas": "Cedar",
    }
    for row in provincial:
        lines.append(
            f"| {labels.get(row.scope.species_group, row.scope.species_group)} | {row.scope.product_or_grade} | {fmt(row.delivered_purchase)} | {fmt(row.procurement_before_transfers)} | {fmt(row.transfers_total)} | {fmt(row.procurement_after_transfers)} | {fmt(row.residual_before_transfers)} | {fmt(row.residual_after_transfers)} | {row.residual_status} |"
        )
    lines.extend(
        [
            "",
            ("Pine and cedar have a purchase price but no matching T20 synthesis row, so "
            "their residual is not computable. Hardwood and poplar totals are purchase-only "
            "for the same reason. Product rows are not additive to a species total without "
            "published volumes."),
            "",
            ("**Headline SEPM.** Delivered purchase **107.22**, procurement before transfers "
            f"**81.40**, transfers **25.01**, procurement after transfers **106.41**. "
            f"Residual before transfers **{fmt(sep.residual_before_transfers)}**; residual "
            f"after transfers **{fmt(sep.residual_after_transfers)}** CAD/m3. The after-transfer "
            "residual is small because nearly all of the pre-Crown surplus is transferred to the State."),
            "",
            ("**Product-level exceptions.** Hardwood sciage residual before transfers "
            f"**{fmt(hardwood.residual_before_transfers)}**, after transfers "
            f"**{fmt(hardwood.residual_after_transfers)}** CAD/m3 (negative after transfers). "
            "Poplar trituration residual before transfers "
            f"**{fmt(poplar.residual_before_transfers)}**, after transfers "
            f"**{fmt(poplar.residual_after_transfers)}** CAD/m3 (negative on both definitions). "
            "Negative values are reported as published arithmetic, not clipped."),
            "",
            "## Regional range (SEPM only, 2022)",
            "",
            ("Seven published regional groups have T20 synthesis procurement rows. They do "
            "not publish a regional purchase price, so the residual range below holds the "
            "provincial SEPM purchase price of **107.22** constant. It is a conditional "
            "procurement-cost range, not an observed regional net value."),
            "",
            "| Region | n | Procurement before | Transfers | Procurement after | Residual before | Residual after |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in regional:
        flag = " ⚠" if row.three_respondent_flag else ""
        lines.append(
            f"| {row.region_code}{flag} | {row.sample_n} | {fmt(row.procurement_before_transfers)} | "
            f"{fmt(row.transfers_total)} | {fmt(row.procurement_after_transfers)} | "
            f"{fmt(row.residual_before_transfers)} | {fmt(row.residual_after_transfers)} |"
        )
    lines.extend(
        [
            "",
            (f"Range: procurement before **{fmt(before_min)}–{fmt(before_max)}**, after "
            f"**{fmt(after_min)}–{fmt(after_max)}**; residual before "
            f"**{fmt(residual_before_min)}–{fmt(residual_before_max)}**, after "
            f"**{fmt(residual_after_min)}–{fmt(residual_after_max)}** CAD/m3. "
            "R09 has `sample_n=3` and retains a three-respondent flag. "
            f"Five regions have a negative after-transfer residual under the constant "
            f"provincial purchase price: {', '.join(negative_after_regions)}. "
            "This does not mean their costs are wrong; it means the reported procurement "
            "total exceeds the provincial reference price for those groups."),
            "",
            "## BMMB historical benchmark (nominal 2011–2015)",
            "",
            ("BMMB delivered auction results are a separate historical benchmark. They are "
            "**not** a 2022 price, **not** stumpage, and **not** a confirmed mill-gate price."),
            "",
            "| Scope | Sales | Awarded | Full rates | Partial awards | Volume (m3) | Value (CAD) | Weighted (CAD/m3) | Median (CAD/m3) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for stats in (bmmb_overall, *bmmb_years):
        label = "All years" if stats.aggregation_level == "delivered_all_years" else stats.sale_year
        lines.append(
            f"| {label} | {stats.sale_count} | {stats.awarded_count} | "
            f"{stats.full_rate_sale_count} | {stats.partial_award_sale_count} | "
            f"{stats.volume_m3:,.0f} | {stats.observed_value_cad:,.2f} | "
            f"{fmt(stats.weighted_rate_cad_m3, 4)} | {fmt(stats.median_rate_cad_m3, 4)} |"
        )
    lines.extend(
        [
            "",
            ("**Caveat:** nominal, 18 sales, no grade split. Eleven full rates cover "
            "89,900 m3 and CAD 3,442,633.50; two 2012 partial awards (CAD 436,325) are "
            "excluded from the rate and retained only as lower bounds. No deflation is "
            "applied, so this table must not be compared with the 2022 survey as if it "
            "were the same price year. The archive does not publish species, grade, "
            "destination, or service-bundle allocation."),
            "",
            "## QC total sensitivity (screening, M CAD)",
            "",
            ("QC allocation volumes are clearly mapped from the provincial allocation "
            "screening files. The survey SEPM residual is applied to each candidate total. "
            "This is a **sensitivity**, not an observed provincial total: it assumes all "
            "QC volume is SEPM, uses no species mix, and is not volume-weighted to the "
            "survey population."),
            "",
            "| Case | QC volume (Mm3) | Volume source | Before transfers (M CAD) | After transfers (M CAD) |",
            "|---|---:|---|---:|---:|",
        ]
    )
    for row in sensitivity:
        lines.append(
            f"| {row.case.case_id} | {row.case.qc_harvest_m3 / 1_000_000.0:.3f} | "
            f"`{row.case.source_path}` | {row.net_value_before_transfers_mcad:.1f} | "
            f"{row.net_value_after_transfers_mcad:.1f} |"
        )
    before_values = [row.net_value_before_transfers_mcad for row in sensitivity]
    after_values = [row.net_value_after_transfers_mcad for row in sensitivity]
    lines.extend(
        [
            "",
            (f"Sensitivity range: **{min(before_values):.1f}–{max(before_values):.1f} M CAD** "
            f"before transfers and **{min(after_values):.1f}–{max(after_values):.1f} M CAD** "
            "after transfers. The after-transfer range is narrow because the SEPM "
            "after-transfer residual is only 0.81 CAD/m3."),
            "",
            "## Validation",
            "",
            (f"All **{len(checks)}** checks pass. The builder recomputes the T20 component "
            "identity, the transfer identity, every residual, the regional range, the BMMB "
            "weighted/median rate, and the sensitivity arithmetic. It fails closed on a "
            "missing row, duplicate key, unexpected unit, non-finite value, or arithmetic "
            "mismatch. T2 purchase rows are used once; the overlapping T21 purchase rows "
            "are used only as a cross-check."),
            "",
            "## Non-claims and limitations",
            "",
            "- Not an observed transaction net value.",
            "- Not a policy result and not `policy_grade`.",
            "- Not a mill margin; transformation and lumber revenue are excluded.",
            "- Not a stumpage rate.",
            "- Not a current or representative BMMB price.",
            "- Not a provincial harvest-population estimate.",
            "- Not merged into the ledger, Forest LP, or UI.",
            "- Not deflated; BMMB remains nominal 2011–2015.",
            "- Not species- or grade-allocated.",
            "- Regional residuals are conditional on a constant provincial purchase price.",
            "- The QC sensitivity uses screening allocation volumes, not observed harvest.",
            "",
            "## Sources",
            "",
            "- `data/derived/national/harvest_basis/qc_cost_survey_2022/qc_cost_revenue_survey.csv` (T2 and T20 synthesis rows).",
            "- `data/derived/national/harvest_basis/bmmb_delivered/bmmb_delivered_sales.csv` (delivered-to-destination sales).",
            "- `data/derived/national/harvest_basis/provincial_allocation/{100m,119m,150m}.csv` (QC screening volumes).",
            "- `planning/forest-cost-revenue-ledger.md` (definitions and identity).",
            "",
            "Reproduce with:",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.harvest.net_value_benchmark_v1",
            ".venv/bin/python -m pytest tests/test_net_value_benchmark.py -q",
            "```",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


PROVINCIAL_COLUMNS = (
    "benchmark_id",
    "geography",
    "region_code",
    "species_group",
    "species_label",
    "product_or_grade",
    "aggregation_level",
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
    "survey_year",
    "purchase_source_table",
    "procurement_source_table",
    "price_basis",
    "sample_n",
    "confidentiality_flag",
    "three_respondent_flag",
    "is_additive",
    "evidence",
    "policy_grade",
    "net_value_status",
    "observation_type",
    "notes",
)

REGIONAL_COLUMNS = (
    "regional_benchmark_id",
    "region_code",
    "region_label",
    "statistic",
    "aggregation_level",
    "sample_n",
    "confidentiality_flag",
    "three_respondent_flag",
    "procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "procurement_after_transfers_cad_m3",
    "reference_purchase_cad_m3",
    "residual_before_transfers_cad_m3",
    "residual_after_transfers_cad_m3",
    "residual_price_assumption",
    "unit",
    "price_year",
    "currency_basis",
    "evidence",
    "policy_grade",
    "net_value_status",
    "is_additive",
    "notes",
)

BMMB_COLUMNS = (
    "benchmark_id",
    "aggregation_level",
    "sale_year",
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
    "price_year_basis",
    "currency_basis",
    "deflator_applied",
    "species_scope",
    "quality",
    "grade_split",
    "species_allocation",
    "not_stumpage",
    "destination_reported",
    "service_bundle_published",
    "evidence",
    "policy_grade",
    "net_value_status",
    "observation_type",
    "notes",
)

SENSITIVITY_COLUMNS = (
    "case_id",
    "national_total_m3",
    "qc_harvest_m3",
    "observed_2024_qc_m3",
    "volume_source",
    "volume_mapping",
    "volume_basis",
    "species_mix_assumption",
    "residual_before_transfers_cad_m3",
    "residual_after_transfers_cad_m3",
    "qc_net_value_before_transfers_mcad",
    "qc_net_value_after_transfers_mcad",
    "evidence",
    "policy_grade",
    "net_value_status",
    "notes",
)


def build(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    survey_path: Path = SURVEY_PATH,
    bmmb_sales_path: Path = BMMB_SALES_PATH,
    allocation_dir: Path = ALLOCATION_DIR,
) -> BuildResult:
    generated_utc = utc_now()
    survey_rows = parse_survey_rows(survey_path)
    purchases = select_purchase_prices(survey_rows)
    t2_t21_matches = validate_t2_t21_purchase_consistency(survey_rows)
    procurement = select_procurement_synthesis(survey_rows)
    provincial = build_provincial_benchmarks(purchases, procurement)

    sep_benchmark = next(
        row for row in provincial if row.scope.scope_id == "qc_sepm_total"
    )
    if sep_benchmark.delivered_purchase is None:
        raise BenchmarkError("SEPM purchase price is missing")
    regional_rows = select_regional_rows(survey_rows)
    regional = build_regional_benchmarks(
        regional_rows, reference_purchase=sep_benchmark.delivered_purchase
    )

    bmmb_sales = parse_bmmb_sales(bmmb_sales_path)
    bmmb_overall, bmmb_years = build_bmmb_stats(bmmb_sales)

    cases = load_allocation_cases(allocation_dir)
    sensitivity = build_sensitivity(cases, sep_benchmark)

    checks = validate_results(
        survey_rows=survey_rows,
        provincial=provincial,
        regional=regional,
        bmmb_sales=bmmb_sales,
        bmmb_overall=bmmb_overall,
        bmmb_years=bmmb_years,
        sensitivity=sensitivity,
        t2_t21_matches=t2_t21_matches,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    provincial_path = output_dir / PROVINCIAL_CSV
    regional_path = output_dir / REGIONAL_CSV
    bmmb_path = output_dir / BMMB_CSV
    sensitivity_path = output_dir / SENSITIVITY_CSV
    summary_path = output_dir / SUMMARY_JSON
    report_path = output_dir / REPORT_MD
    manifest_path = output_dir / MANIFEST_JSON

    write_csv(provincial_path, PROVINCIAL_COLUMNS, provincial_csv_rows(provincial))
    write_csv(regional_path, REGIONAL_COLUMNS, regional_csv_rows(regional))
    write_csv(bmmb_path, BMMB_COLUMNS, bmmb_csv_rows(bmmb_overall, bmmb_years))
    write_csv(sensitivity_path, SENSITIVITY_COLUMNS, sensitivity_csv_rows(sensitivity))

    summary = build_summary(
        generated_utc=generated_utc,
        provincial=provincial,
        regional=regional,
        bmmb_overall=bmmb_overall,
        bmmb_years=bmmb_years,
        sensitivity=sensitivity,
        checks=checks,
    )
    summary["inputs"] = [
        file_record(survey_path, role="2022 QC survey aggregates"),
        file_record(
            survey_path.with_name("manifest.json"),
            role="2022 QC survey provenance manifest",
        ),
        file_record(bmmb_sales_path, role="BMMB delivered auction sales"),
        file_record(
            bmmb_sales_path.with_name("manifest.json"),
            role="BMMB delivered provenance manifest",
        ),
        *[
            file_record(allocation_dir / f"{case_id}.csv", role="QC screening allocation volume")
            for case_id, _ in ALLOCATION_CASES
        ],
    ]
    summary["outputs"] = [
        file_record(provincial_path, role="provincial benchmark", rows=len(provincial)),
        file_record(regional_path, role="regional range", rows=len(regional) + 2),
        file_record(bmmb_path, role="BMMB historical benchmark", rows=1 + len(bmmb_years)),
        file_record(sensitivity_path, role="QC total sensitivity", rows=len(sensitivity)),
    ]
    write_json(summary_path, summary)
    write_report(
        report_path,
        generated_utc=generated_utc,
        provincial=provincial,
        regional=regional,
        bmmb_overall=bmmb_overall,
        bmmb_years=bmmb_years,
        sensitivity=sensitivity,
        checks=checks,
    )

    manifest = {
        "artifact_version": "1.0.0",
        "dataset": "qc_net_value_benchmark",
        "generated_utc": generated_utc,
        "status": "benchmark_only",
        "generator": {
            "script": "workflows/national/harvest/net_value_benchmark_v1.py",
            "command": "python -m workflows.national.harvest.net_value_benchmark_v1",
        },
        "labels": {
            "evidence_values": [EVIDENCE_SURVEY, EVIDENCE_HISTORICAL],
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
        },
        "parameters": {
            "survey_year": 2022,
            "price_year": 2022,
            "currency_basis": CURRENCY_BASIS_2022,
            "bmmb_currency_basis": CURRENCY_BASIS_BMMB,
            "residual_before_transfers": "delivered_purchase - procurement_before_transfers",
            "residual_after_transfers": "delivered_purchase - procurement_after_transfers",
            "regional_reference_purchase_cad_m3": 107.22,
            "qc_allocation_cases": [case_id for case_id, _ in ALLOCATION_CASES],
        },
        "inputs": summary["inputs"],
        "outputs": [
            file_record(provincial_path, role="provincial benchmark", rows=len(provincial)),
            file_record(regional_path, role="regional range", rows=len(regional) + 2),
            file_record(bmmb_path, role="BMMB historical benchmark", rows=1 + len(bmmb_years)),
            file_record(sensitivity_path, role="QC total sensitivity", rows=len(sensitivity)),
            file_record(summary_path, role="machine-readable summary"),
            file_record(report_path, role="human-readable report"),
        ],
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "non_claims": summary["non_claims"],
    }
    write_json(manifest_path, manifest)

    return BuildResult(
        output_dir=output_dir,
        provincial=provincial,
        regional=regional,
        bmmb_overall=bmmb_overall,
        bmmb_years=bmmb_years,
        sensitivity=sensitivity,
        checks=checks,
        summary=summary,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="directory for the benchmark outputs",
    )
    parser.add_argument("--survey", type=Path, default=SURVEY_PATH)
    parser.add_argument("--bmmb-sales", type=Path, default=BMMB_SALES_PATH)
    parser.add_argument("--allocation-dir", type=Path, default=ALLOCATION_DIR)
    args = parser.parse_args(argv)
    result = build(
        output_dir=args.output_dir,
        survey_path=args.survey,
        bmmb_sales_path=args.bmmb_sales,
        allocation_dir=args.allocation_dir,
    )
    print(f"wrote {len(result.provincial)} provincial rows to {args.output_dir}")
    print(f"wrote {len(result.regional)} regional rows plus min/max bounds")
    print(
        "BMMB weighted rate "
        f"{result.bmmb_overall.weighted_rate_cad_m3:.4f} CAD/m3 (nominal 2011-2015)"
    )
    print(f"QC sensitivity cases: {len(result.sensitivity)}")
    print(f"validation: {len(result.checks)} checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
