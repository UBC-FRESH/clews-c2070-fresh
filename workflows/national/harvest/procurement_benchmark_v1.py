"""Build an honest QC procurement-cost benchmark, not a net-value estimate.

The 2022 Quebec MRNF survey supports a comparison between two procurement
channels:

* Table 2 ``purchase.wood_purchase_delivered`` is a buyer-side procurement
  **cost** (purchase, State transfers, transport/unloading, and road costs where
  applicable); it is not observed log-sale revenue.
* Table 20 ``synthesis_procurement`` is the BGAD procurement cost before and
  after payments to the State.

Their difference is a **procurement-channel cost spread across different
respondent samples**.  It is not forest net value.  Every generated row is
therefore ``net_value_status=not_estimated``,
``interpretation=purchased_vs_bgad_procurement_cost_spread`` and
``policy_grade=false``.

The BMMB delivered-sale table is reported separately as a nominal 2011-2015
auction benchmark.  Crown public-forest sales are the primary series; teaching
and research forest sales and the all-in pooled rate are secondary.  The 2012
re-offer overlap is disclosed and is not netted out of the rate because both
re-offer sales are excluded from the rate.

No volume-scaled M CAD diagnostic is produced.  The provincial allocation
files are screening totals and monetising them would manufacture a misleading
"QC net value".  The builder reads only the QC survey aggregates and the BMMB
delivered sales, and writes only ``harvest_basis/procurement_benchmark``.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.procurement_benchmark_v1
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

SURVEY_PATH = HARVEST_BASIS_DIR / "qc_cost_survey_2022/qc_cost_revenue_survey.csv"
BMMB_SALES_PATH = HARVEST_BASIS_DIR / "bmmb_delivered/bmmb_delivered_sales.csv"
DEFAULT_OUTPUT_DIR = HARVEST_BASIS_DIR / "procurement_benchmark"

PROVINCIAL_CSV = "qc_procurement_cost_benchmark.csv"
REGIONAL_CSV = "qc_procurement_regional_range.csv"
BMMB_CSV = "bmmb_historical_benchmark.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "PROCUREMENT_BENCHMARK_REPORT.md"
MANIFEST_JSON = "manifest.json"

NET_VALUE_STATUS = "not_estimated"
INTERPRETATION = "purchased_vs_bgad_procurement_cost_spread"
EVIDENCE_SURVEY = "survey_procurement_cost"
EVIDENCE_REGIONAL = "survey_regional_procurement_cost"
EVIDENCE_HISTORICAL = "benchmark_historical"
CURRENCY_BASIS = "CAD_nominal"
CURRENCY_BASIS_SURVEY = "CAD_nominal_2022"

ARITHMETIC_TOLERANCE = 0.011
RATE_TOLERANCE = 0.0006
SURVEY_ROW_COUNT = 660
BMMB_SALE_COUNT = 18
BMMB_AWARDED_COUNT = 13
BMMB_FULL_RATE_COUNT = 11
BMMB_PARTIAL_COUNT = 2
BMMB_REOFFER_COUNT = 2
BMMB_REOFFER_OVERLAP_M3 = 22_200.0

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
COST_COMPONENTS = ("harvest", "road", "haul", "hosting", "other_costs")

SURVEY_REQUIRED_COLUMNS = {
    "source_id",
    "source_table",
    "survey_year",
    "region_code",
    "region_label",
    "aggregation_level",
    "species_group",
    "species_label",
    "product_or_grade",
    "component_group",
    "component",
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
    "tenure",
    "sale_type_family",
    "status",
    "awarded",
    "volume_m3",
    "total_cad",
    "price_cad_m3",
    "price_cad_m3_lower_bound",
    "partial_award",
    "reoffer_of_sale_id",
    "currency_basis",
    "observed",
    "quality",
    "species_scope",
    "aggregation_level",
}

ALLOWED_BMMB_TENURES = ("Forêt publique", "Forêt d'enseignement et recherche")
CROWN_TENURE = "Forêt publique"
TEACHING_TENURE = "Forêt d'enseignement et recherche"

ACCOUNTING_CAVEATS = (
    "T2 purchase is a buyer-side procurement cost, not observed log-sale "
    "revenue, stumpage, or forest net value. T20 BGAD excludes species "
    "gain/loss (reported separately in T22); for SEPM the exclusion is larger "
    "than the after-transfer spread. PRCCM road rebates sit inside transfers, "
    "so T5 road after PRCCM must not be combined with T10/T20 transfers. "
    "Purchase and BGAD are different respondent samples; their difference is "
    "a procurement-channel cost spread, not net value."
)


class BenchmarkError(RuntimeError):
    """Raised when an input violates a required benchmark invariant."""


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
    survey_year: int
    region_code: str
    region_label: str
    aggregation_level: str
    species_group: str
    species_label: str
    product_or_grade: str
    component_group: str
    component: str
    value: float
    unit: str
    price_basis: str
    sample_n: int | None
    observation_kind: str
    confidentiality_flag: str
    notes: str


@dataclass(frozen=True, slots=True)
class PurchaseCost:
    species_group: str
    species_label: str
    product_or_grade: str
    value: float
    source_table: str
    confidentiality_flag: str
    notes: str


@dataclass(frozen=True, slots=True)
class BgadSynthesis:
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
class ProvincialCostBenchmark:
    scope: SpeciesScope
    purchase_cost: float
    bgad_before_transfers: float | None
    transfers_total: float | None
    bgad_after_transfers: float | None
    cost_spread_before_transfers: float | None
    cost_spread_after_transfers: float | None
    spread_status: str
    confidentiality_flag: str
    three_respondent_flag: bool
    caveats: str
    notes: str


@dataclass(frozen=True, slots=True)
class RegionalCostBenchmark:
    region_code: str
    region_label: str
    sample_n: int
    confidentiality_flag: str
    three_respondent_flag: bool
    bgad_before_transfers: float
    transfers_total: float
    bgad_after_transfers: float
    reference_purchase: float
    conditional_cost_spread_before: float
    conditional_cost_spread_after: float
    notes: str


@dataclass(frozen=True, slots=True)
class BMMBSale:
    sale_id: str
    sale_year: int
    region_bmmb: str
    tenure: str
    awarded: bool
    volume_m3: float
    total_cad: float | None
    price_cad_m3: float | None
    price_cad_m3_lower_bound: float | None
    partial_award: bool
    reoffer_of_sale_id: str
    observed: bool
    quality: str
    sale_type_family: str


@dataclass(frozen=True, slots=True)
class BMMBStats:
    benchmark_id: str
    scope_id: str
    tenure_scope: str
    is_primary_benchmark: bool
    aggregation_level: str
    sale_year: str
    price_year: str
    price_year_basis: str
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
    reoffer_sale_count: int
    reoffer_overlap_m3: float
    reoffer_sale_ids: str


@dataclass(frozen=True, slots=True)
class BuildResult:
    output_dir: Path
    provincial: tuple[ProvincialCostBenchmark, ...]
    regional: tuple[RegionalCostBenchmark, ...]
    bmmb_rows: tuple[BMMBStats, ...]
    checks: tuple[dict[str, Any], ...]
    summary: Mapping[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise BenchmarkError(f"missing input: {path}")
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def require_columns(
    path: Path, rows: Sequence[Mapping[str, str]], required: Iterable[str]
) -> None:
    if not rows:
        raise BenchmarkError(f"input has no data rows: {path}")
    missing = set(required) - set(rows[0])
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


def parse_strict_bool(text: str, *, context: str) -> bool:
    stripped = (text or "").strip()
    if stripped == "1":
        return True
    if stripped == "0":
        return False
    raise BenchmarkError(f"expected strict 0/1 boolean for {context}, got {text!r}")


def sha256_file(path: Path) -> str:
    if not path.is_file():
        raise BenchmarkError(f"missing file for hashing: {path}")
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


def load_json_object(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise BenchmarkError(f"missing provenance manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BenchmarkError(f"invalid JSON manifest: {path}") from exc
    if not isinstance(payload, dict):
        raise BenchmarkError(f"manifest is not a JSON object: {path}")
    return payload


def load_survey_manifest_row_count(manifest_path: Path) -> int:
    manifest = load_json_object(manifest_path)
    validation = manifest.get("validation")
    if not isinstance(validation, dict) or "row_count" not in validation:
        raise BenchmarkError(f"{manifest_path} does not declare validation.row_count")
    try:
        return int(validation["row_count"])
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{manifest_path} has a non-integer validation.row_count") from exc


def load_bmmb_manifest_sale_count(manifest_path: Path) -> int:
    manifest = load_json_object(manifest_path)
    validation = manifest.get("validation")
    if not isinstance(validation, dict) or "delivered_sales" not in validation:
        raise BenchmarkError(f"{manifest_path} does not declare validation.delivered_sales")
    try:
        return int(validation["delivered_sales"])
    except (TypeError, ValueError) as exc:
        raise BenchmarkError(f"{manifest_path} has a non-integer delivered_sales") from exc


def combine_confidentiality_flags(flags: Iterable[str], *, context: str) -> str:
    allowed = {"none", "three_respondents", "suppressed_lt3"}
    unknown = set(flags) - allowed
    if unknown:
        raise BenchmarkError(f"unexpected confidentiality flags for {context}: {sorted(unknown)}")
    flag_set = set(flags)
    if "three_respondents" in flag_set:
        return "three_respondents"
    if "suppressed_lt3" in flag_set:
        return "suppressed_lt3"
    return "none"


def parse_survey_rows(path: Path) -> tuple[SurveyRow, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, SURVEY_REQUIRED_COLUMNS)
    expected = load_survey_manifest_row_count(path.with_name("manifest.json"))
    if len(raw_rows) != expected:
        raise BenchmarkError(
            f"survey row count {len(raw_rows)} does not match manifest {expected}"
        )
    parsed: list[SurveyRow] = []
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        try:
            survey_year = int(row["survey_year"])
        except ValueError as exc:
            raise BenchmarkError(f"invalid survey_year for {context}: {row['survey_year']!r}") from exc
        parsed.append(
            SurveyRow(
                source_id=row["source_id"],
                source_table=row["source_table"],
                survey_year=survey_year,
                region_code=row["region_code"],
                region_label=" ".join(row["region_label"].split()),
                aggregation_level=row["aggregation_level"],
                species_group=row["species_group"],
                species_label=row["species_label"],
                product_or_grade=row["product_or_grade"],
                component_group=row["component_group"],
                component=row["component"],
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


def select_purchase_costs(rows: Sequence[SurveyRow]) -> dict[tuple[str, str], PurchaseCost]:
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
    purchases: dict[tuple[str, str], PurchaseCost] = {}
    for row in selected:
        key = (row.species_group, row.product_or_grade)
        if key in purchases:
            raise BenchmarkError(f"duplicate T2 purchase row for {key}")
        if row.survey_year != 2022:
            raise BenchmarkError(f"unexpected T2 survey year for {key}: {row.survey_year}")
        if row.unit != "CAD/m3" or row.price_basis != "delivered_to_mill":
            raise BenchmarkError(f"unexpected T2 basis for {key}: {row.unit}/{row.price_basis}")
        purchases[key] = PurchaseCost(
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
    def collect(table: str) -> dict[tuple[str, str], float]:
        return {
            (row.species_group, row.product_or_grade): row.value
            for row in rows
            if row.source_table == table
            and row.region_code == "QC"
            and row.aggregation_level == "provincial"
            and row.component == "wood_purchase_delivered"
            and row.survey_year == 2022
            and row.unit == "CAD/m3"
        }

    t2 = collect("T2")
    t21 = collect("T21")
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


def select_bgad_synthesis(
    rows: Sequence[SurveyRow],
) -> dict[tuple[str, str], BgadSynthesis]:
    grouped: dict[tuple[str, str], dict[str, SurveyRow]] = defaultdict(dict)
    for row in rows:
        if row.source_table != "T20":
            continue
        if row.region_code != "QC" or row.aggregation_level != "provincial":
            continue
        if row.component_group != "synthesis_procurement":
            continue
        if row.survey_year != 2022:
            raise BenchmarkError(f"unexpected T20 survey year: {row.survey_year}")
        if row.unit != "CAD/m3":
            raise BenchmarkError(f"unexpected T20 unit for {row.component}: {row.unit}")
        key = (row.species_group, row.product_or_grade)
        if row.component in grouped[key]:
            raise BenchmarkError(f"duplicate T20 synthesis component for {key}: {row.component}")
        grouped[key][row.component] = row

    if len(grouped) != 3:
        raise BenchmarkError(f"expected 3 T20 synthesis groups, found {len(grouped)}")

    synthesis: dict[tuple[str, str], BgadSynthesis] = {}
    for key, components in grouped.items():
        missing = set(PROCUREMENT_COMPONENTS) - set(components)
        if missing:
            raise BenchmarkError(f"T20 group {key} is missing components: {sorted(missing)}")
        values = {name: components[name].value for name in PROCUREMENT_COMPONENTS}
        negative_components = {name: value for name, value in values.items() if value < 0}
        if negative_components:
            raise BenchmarkError(
                f"T20 group {key} has unexpected negative cost components: {negative_components}"
            )
        computed_before = math.fsum(values[name] for name in COST_COMPONENTS)
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
        flag = combine_confidentiality_flags(
            (components[name].confidentiality_flag for name in PROCUREMENT_COMPONENTS),
            context=f"T20 {key}",
        )
        notes = "; ".join(
            sorted(
                {
                    components[name].notes
                    for name in PROCUREMENT_COMPONENTS
                    if components[name].notes
                }
            )
        )
        first = components["harvest"]
        synthesis[key] = BgadSynthesis(
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
    purchases: Mapping[tuple[str, str], PurchaseCost],
    synthesis: Mapping[tuple[str, str], BgadSynthesis],
) -> tuple[ProvincialCostBenchmark, ...]:
    benchmarks: list[ProvincialCostBenchmark] = []
    for scope in PROVINCIAL_SCOPES:
        key = (scope.species_group, scope.product_or_grade)
        purchase = purchases[key]
        bgad = synthesis.get(key)
        if bgad is None:
            note_parts = [
                ("Purchase cost only; no matching T20 BGAD synthesis row exists, so "
                "no procurement-cost spread is computable. Do not substitute "
                "component-table rows.")
            ]
            if purchase.confidentiality_flag == "three_respondents":
                note_parts.append("Three-respondent mean; interpret with caution.")
            benchmarks.append(
                ProvincialCostBenchmark(
                    scope=scope,
                    purchase_cost=purchase.value,
                    bgad_before_transfers=None,
                    transfers_total=None,
                    bgad_after_transfers=None,
                    cost_spread_before_transfers=None,
                    cost_spread_after_transfers=None,
                    spread_status="not_computable_missing_bgad_synthesis",
                    confidentiality_flag=purchase.confidentiality_flag,
                    three_respondent_flag=purchase.confidentiality_flag == "three_respondents",
                    caveats=ACCOUNTING_CAVEATS,
                    notes="; ".join(note_parts),
                )
            )
            continue

        spread_before = purchase.value - bgad.procurement_before_transfers
        spread_after = purchase.value - bgad.procurement_after_transfers
        flag = combine_confidentiality_flags(
            (purchase.confidentiality_flag, bgad.confidentiality_flag),
            context=f"provincial scope {scope.scope_id}",
        )
        note_parts = [
            ("Cost spread = purchase procurement cost - BGAD procurement cost; it is "
            "a channel cost spread, not a mill margin and not forest net value.")
        ]
        if scope.aggregation_level == "species_total":
            note_parts.append(
                "Species total; not additive with product rows because volumes are not published."
            )
        if flag == "three_respondents":
            note_parts.append("Three-respondent mean; interpret with caution.")
        benchmarks.append(
            ProvincialCostBenchmark(
                scope=scope,
                purchase_cost=purchase.value,
                bgad_before_transfers=bgad.procurement_before_transfers,
                transfers_total=bgad.transfers_total,
                bgad_after_transfers=bgad.procurement_after_transfers,
                cost_spread_before_transfers=spread_before,
                cost_spread_after_transfers=spread_after,
                spread_status="computed",
                confidentiality_flag=flag,
                three_respondent_flag=flag == "three_respondents",
                caveats=ACCOUNTING_CAVEATS,
                notes="; ".join(note_parts),
            )
        )
    return tuple(benchmarks)


def select_regional_rows(rows: Sequence[SurveyRow]) -> dict[str, dict[str, SurveyRow]]:
    grouped: dict[str, dict[str, SurveyRow]] = defaultdict(dict)
    for row in rows:
        if row.source_id != "regional_2022":
            continue
        if row.source_table != "REG":
            continue
        if row.survey_year != 2022:
            raise BenchmarkError(f"unexpected REG survey year for {row.region_code}: {row.survey_year}")
        if row.aggregation_level != "regional" or row.species_group != "sepm":
            continue
        if row.product_or_grade != "total":
            continue
        if row.component_group != "synthesis_procurement":
            continue
        if row.unit != "CAD/m3":
            raise BenchmarkError(f"unexpected REG unit for {row.region_code}: {row.unit}")
        if not row.region_label.strip():
            raise BenchmarkError(f"REG row has a blank region_label: {row.region_code}")
        if row.component in grouped[row.region_code]:
            raise BenchmarkError(
                f"duplicate regional synthesis component for {row.region_code}: {row.component}"
            )
        grouped[row.region_code][row.component] = row
    if len(grouped) != 7:
        raise BenchmarkError(f"expected 7 regional groups, found {len(grouped)}")
    for region, components in grouped.items():
        missing = set(PROCUREMENT_COMPONENTS) - set(components)
        if missing:
            raise BenchmarkError(f"regional group {region} is missing: {sorted(missing)}")
        sample_sizes = {
            components[name].sample_n for name in PROCUREMENT_COMPONENTS
        }
        if len(sample_sizes) != 1 or None in sample_sizes:
            raise BenchmarkError(
                f"regional group {region} has inconsistent sample_n: {sorted(sample_sizes, key=str)}"
            )
        sample_n = next(iter(sample_sizes))
        if sample_n is None or sample_n < 1:
            raise BenchmarkError(f"regional group {region} has invalid sample_n: {sample_n}")
    return dict(grouped)


def build_regional_benchmarks(
    regional_rows: Mapping[str, Mapping[str, SurveyRow]],
    *,
    reference_purchase: float,
) -> tuple[RegionalCostBenchmark, ...]:
    benchmarks: list[RegionalCostBenchmark] = []
    for region_code, components in regional_rows.items():
        values = {name: components[name].value for name in PROCUREMENT_COMPONENTS}
        negative_components = {name: value for name, value in values.items() if value < 0}
        if negative_components:
            raise BenchmarkError(
                f"regional group {region_code} has unexpected negative cost components: "
                f"{negative_components}"
            )
        computed_before = math.fsum(values[name] for name in COST_COMPONENTS)
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
        flag = combine_confidentiality_flags(
            (components[name].confidentiality_flag for name in PROCUREMENT_COMPONENTS),
            context=f"REG {region_code}",
        )
        three_respondent_flag = flag == "three_respondents" or sample_n <= 3
        notes = (
            "REG regional_2022 (Compilation_donnees_regionalisees_2022.pdf) SEPM "
            "procurement cost. No regional purchase price is published, so the "
            "conditional spread holds the provincial SEPM purchase cost constant; "
            "it is not an observed regional net value."
        )
        if three_respondent_flag:
            notes += " Three-respondent mean; interpret with caution."
        benchmarks.append(
            RegionalCostBenchmark(
                region_code=region_code,
                region_label=components["harvest"].region_label,
                sample_n=sample_n,
                confidentiality_flag=flag,
                three_respondent_flag=three_respondent_flag,
                bgad_before_transfers=values["procurement_total_before_transfers"],
                transfers_total=values["transfers_total"],
                bgad_after_transfers=values["procurement_total_after_transfers"],
                reference_purchase=reference_purchase,
                conditional_cost_spread_before=reference_purchase
                - values["procurement_total_before_transfers"],
                conditional_cost_spread_after=reference_purchase
                - values["procurement_total_after_transfers"],
                notes=notes,
            )
        )
    return tuple(sorted(benchmarks, key=lambda row: row.region_code))


def parse_bmmb_sales(path: Path) -> tuple[BMMBSale, ...]:
    raw_rows = read_csv_rows(path)
    require_columns(path, raw_rows, BMMB_REQUIRED_COLUMNS)
    expected = load_bmmb_manifest_sale_count(path.with_name("manifest.json"))
    if len(raw_rows) != expected:
        raise BenchmarkError(
            f"BMMB sale count {len(raw_rows)} does not match manifest {expected}"
        )
    sales: list[BMMBSale] = []
    seen_ids: set[str] = set()
    for line_number, row in enumerate(raw_rows, start=2):
        context = f"{path.name}:{line_number}"
        sale_id = row["sale_id"].strip()
        if not sale_id:
            raise BenchmarkError(f"blank BMMB sale_id at {context}")
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
        if row["currency_basis"] != "CAD_nominal":
            raise BenchmarkError(
                f"unexpected BMMB currency_basis for {sale_id}: {row['currency_basis']}"
            )
        if row["species_scope"] != "ALL_SPECIES_MIXED":
            raise BenchmarkError(
                f"unexpected BMMB species_scope for {sale_id}: {row['species_scope']}"
            )
        if row["aggregation_level"] != "sale_total":
            raise BenchmarkError(
                f"unexpected BMMB aggregation_level for {sale_id}: {row['aggregation_level']}"
            )
        if row["tenure"] not in ALLOWED_BMMB_TENURES:
            raise BenchmarkError(f"unexpected BMMB tenure for {sale_id}: {row['tenure']}")
        try:
            sale_year = int(row["sale_year"])
        except ValueError as exc:
            raise BenchmarkError(f"invalid BMMB sale_year for {sale_id}: {row['sale_year']!r}") from exc
        volume = parse_finite_float(row["volume_m3"], context=context)
        if volume <= 0:
            raise BenchmarkError(f"BMMB volume must be positive for {sale_id}: {volume}")
        total_cad = parse_optional_float(row["total_cad"], context=context)
        price = parse_optional_float(row["price_cad_m3"], context=context)
        lower_bound = parse_optional_float(
            row["price_cad_m3_lower_bound"], context=context
        )
        awarded = parse_strict_bool(row["awarded"], context=f"{sale_id}.awarded")
        partial_award = parse_strict_bool(
            row["partial_award"], context=f"{sale_id}.partial_award"
        )
        observed = parse_strict_bool(row["observed"], context=f"{sale_id}.observed")

        if awarded:
            if not observed:
                raise BenchmarkError(f"awarded BMMB sale is not observed: {sale_id}")
            if total_cad is None or total_cad <= 0:
                raise BenchmarkError(f"awarded BMMB sale lacks a positive total_cad: {sale_id}")
        else:
            if any(value is not None for value in (total_cad, price, lower_bound)):
                raise BenchmarkError(f"unsold BMMB sale carries a price or total: {sale_id}")
            if observed:
                raise BenchmarkError(f"unsold BMMB sale is marked observed: {sale_id}")

        if partial_award:
            if not awarded:
                raise BenchmarkError(f"partial-award BMMB sale is not awarded: {sale_id}")
            if price is not None:
                raise BenchmarkError(f"partial-award sale has a full rate: {sale_id}")
            if lower_bound is None or lower_bound <= 0:
                raise BenchmarkError(f"partial-award sale lacks a lower bound: {sale_id}")
            if total_cad is None or not math.isclose(
                total_cad / volume, lower_bound, abs_tol=RATE_TOLERANCE
            ):
                raise BenchmarkError(
                    f"partial-award lower bound does not match total/volume: {sale_id}"
                )
        elif awarded:
            if lower_bound is not None:
                raise BenchmarkError(f"non-partial sale has a lower bound: {sale_id}")
            if price is None or price <= 0:
                raise BenchmarkError(f"full-rate sale lacks a positive rate: {sale_id}")
            if total_cad is None or not math.isclose(
                total_cad / volume, price, abs_tol=RATE_TOLERANCE
            ):
                raise BenchmarkError(f"full rate does not match total/volume: {sale_id}")

        sales.append(
            BMMBSale(
                sale_id=sale_id,
                sale_year=sale_year,
                region_bmmb=row["region_bmmb"],
                tenure=row["tenure"],
                awarded=awarded,
                volume_m3=volume,
                total_cad=total_cad,
                price_cad_m3=price,
                price_cad_m3_lower_bound=lower_bound,
                partial_award=partial_award,
                reoffer_of_sale_id=row["reoffer_of_sale_id"].strip(),
                observed=observed,
                quality=row["quality"],
                sale_type_family=row["sale_type_family"],
            )
        )
    if len(sales) != BMMB_SALE_COUNT:
        raise BenchmarkError(f"expected {BMMB_SALE_COUNT} BMMB delivered sales, found {len(sales)}")
    for sale in sales:
        if not sale.reoffer_of_sale_id:
            continue
        if sale.reoffer_of_sale_id == sale.sale_id:
            raise BenchmarkError(f"BMMB sale re-offers itself: {sale.sale_id}")
        if sale.reoffer_of_sale_id not in seen_ids:
            raise BenchmarkError(
                f"BMMB re-offer points to an unknown sale: {sale.reoffer_of_sale_id}"
            )
    return tuple(sales)


def require_total_cad(sale: BMMBSale) -> float:
    if sale.total_cad is None:
        raise BenchmarkError(f"missing total_cad for awarded BMMB sale: {sale.sale_id}")
    return sale.total_cad


def compute_bmmb_stats(
    sales: Sequence[BMMBSale],
    *,
    benchmark_id: str,
    scope_id: str,
    tenure_scope: str,
    is_primary_benchmark: bool,
    aggregation_level: str,
    sale_year: str,
    price_year: str,
    region_bmmb: str = "all",
) -> BMMBStats:
    full_rate = [
        sale for sale in sales if sale.price_cad_m3 is not None and not sale.partial_award
    ]
    partial = [sale for sale in sales if sale.partial_award]
    awarded = [sale for sale in sales if sale.awarded]
    rates = [sale.price_cad_m3 for sale in full_rate if sale.price_cad_m3 is not None]
    rate_volume = math.fsum(sale.volume_m3 for sale in full_rate)
    rate_value = math.fsum(require_total_cad(sale) for sale in full_rate)
    observed_value = math.fsum(require_total_cad(sale) for sale in awarded)
    partial_value = math.fsum(require_total_cad(sale) for sale in partial)
    lower_bounds = [
        sale.price_cad_m3_lower_bound
        for sale in partial
        if sale.price_cad_m3_lower_bound is not None
    ]
    reoffer_sales = [sale for sale in sales if sale.reoffer_of_sale_id]
    reoffer_overlap = math.fsum(sale.volume_m3 for sale in reoffer_sales)
    return BMMBStats(
        benchmark_id=benchmark_id,
        scope_id=scope_id,
        tenure_scope=tenure_scope,
        is_primary_benchmark=is_primary_benchmark,
        aggregation_level=aggregation_level,
        sale_year=sale_year,
        price_year=price_year,
        price_year_basis=f"nominal_{price_year.replace('-', '_')}",
        region_bmmb=region_bmmb,
        sale_count=len(sales),
        awarded_count=len(awarded),
        unsold_count=len(sales) - len(awarded),
        observed_price_count=sum(sale.observed for sale in sales),
        full_rate_sale_count=len(full_rate),
        partial_award_sale_count=len(partial),
        volume_m3=math.fsum(sale.volume_m3 for sale in sales),
        awarded_volume_m3=math.fsum(sale.volume_m3 for sale in awarded),
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
        reoffer_sale_count=len(reoffer_sales),
        reoffer_overlap_m3=reoffer_overlap,
        reoffer_sale_ids=";".join(sale.sale_id for sale in reoffer_sales),
    )


def build_bmmb_stats(sales: Sequence[BMMBSale]) -> tuple[BMMBStats, ...]:
    crown_sales = [sale for sale in sales if sale.tenure == CROWN_TENURE]
    teaching_sales = [sale for sale in sales if sale.tenure == TEACHING_TENURE]
    if len(crown_sales) + len(teaching_sales) != len(sales):
        raise BenchmarkError("BMMB sales contain an unclassified tenure")

    crown = compute_bmmb_stats(
        crown_sales,
        benchmark_id="bmmb_crown_public_forest_2011_2015",
        scope_id="crown_public_forest",
        tenure_scope="crown_only",
        is_primary_benchmark=True,
        aggregation_level="delivered_all_years",
        sale_year="2011-2015",
        price_year="2011-2015",
    )
    teaching = compute_bmmb_stats(
        teaching_sales,
        benchmark_id="bmmb_teaching_research_forest_2014_2015",
        scope_id="teaching_research_forest",
        tenure_scope="teaching_only",
        is_primary_benchmark=False,
        aggregation_level="delivered_all_years",
        sale_year="2014-2015",
        price_year="2014-2015",
    )
    all_in = compute_bmmb_stats(
        sales,
        benchmark_id="bmmb_all_delivered_2011_2015",
        scope_id="all_delivered",
        tenure_scope="mixed",
        is_primary_benchmark=False,
        aggregation_level="delivered_all_years",
        sale_year="2011-2015",
        price_year="2011-2015",
    )
    years = tuple(
        compute_bmmb_stats(
            [sale for sale in sales if sale.sale_year == year],
            benchmark_id=f"bmmb_all_delivered_{year}",
            scope_id="all_delivered",
            tenure_scope="mixed",
            is_primary_benchmark=False,
            aggregation_level="delivered_year",
            sale_year=str(year),
            price_year=str(year),
        )
        for year in sorted({sale.sale_year for sale in sales})
    )
    if all_in.sale_count != BMMB_SALE_COUNT:
        raise BenchmarkError(f"expected {BMMB_SALE_COUNT} BMMB delivered sales, found {all_in.sale_count}")
    if all_in.awarded_count != BMMB_AWARDED_COUNT:
        raise BenchmarkError(f"expected {BMMB_AWARDED_COUNT} awarded BMMB sales, found {all_in.awarded_count}")
    if all_in.full_rate_sale_count != BMMB_FULL_RATE_COUNT:
        raise BenchmarkError(
            f"expected {BMMB_FULL_RATE_COUNT} full-rate BMMB sales, "
            f"found {all_in.full_rate_sale_count}"
        )
    if all_in.partial_award_sale_count != BMMB_PARTIAL_COUNT:
        raise BenchmarkError(
            f"expected {BMMB_PARTIAL_COUNT} partial-award BMMB sales, "
            f"found {all_in.partial_award_sale_count}"
        )
    if all_in.reoffer_sale_count != BMMB_REOFFER_COUNT or not math.isclose(
        all_in.reoffer_overlap_m3, BMMB_REOFFER_OVERLAP_M3, abs_tol=1.0
    ):
        raise BenchmarkError(
            "BMMB re-offer overlap changed: "
            f"{all_in.reoffer_sale_count} sales / {all_in.reoffer_overlap_m3} m3"
        )
    if crown.full_rate_sale_count != 9 or not math.isclose(
        crown.rate_computable_volume_m3, 74_550.0, abs_tol=1.0
    ):
        raise BenchmarkError("BMMB Crown-only full-rate count or volume changed")
    if teaching.full_rate_sale_count != 2 or not math.isclose(
        teaching.rate_computable_volume_m3, 15_350.0, abs_tol=1.0
    ):
        raise BenchmarkError("BMMB teaching full-rate count or volume changed")
    for stats, expected in (
        (crown, 35.7547),
        (teaching, 50.6266),
        (all_in, 38.2940),
    ):
        if stats.weighted_rate_cad_m3 is None or not math.isclose(
            stats.weighted_rate_cad_m3, expected, abs_tol=0.0001
        ):
            raise BenchmarkError(
                f"{stats.scope_id} BMMB weighted rate changed: {stats.weighted_rate_cad_m3}"
            )
    if all_in.median_rate_cad_m3 is None or not math.isclose(
        all_in.median_rate_cad_m3, 36.6668, abs_tol=0.0001
    ):
        raise BenchmarkError(f"BMMB median rate changed: {all_in.median_rate_cad_m3}")
    year_2012 = next((row for row in years if row.sale_year == "2012"), None)
    if year_2012 is None or year_2012.full_rate_sale_count != 0:
        raise BenchmarkError("BMMB 2012 must have partial awards only")
    return (crown, teaching, all_in, *years)


def add_check(checks: list[dict[str, Any]], name: str, passed: bool, detail: str) -> None:
    checks.append({"check": name, "status": "pass" if passed else "fail", "detail": detail})


def validate_results(
    *,
    survey_rows: Sequence[SurveyRow],
    provincial: Sequence[ProvincialCostBenchmark],
    regional: Sequence[RegionalCostBenchmark],
    bmmb_rows: Sequence[BMMBStats],
    t2_t21_matches: int,
    sep_purchase: float,
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

    computed = [row for row in provincial if row.spread_status == "computed"]
    add_check(
        checks,
        "provincial_spread_count",
        len(computed) == 3,
        f"{len(computed)} computable cost spreads of {len(provincial)} scopes",
    )
    add_check(
        checks,
        "provincial_spread_recompute",
        all(
            math.isclose(
                row.cost_spread_before_transfers,
                row.purchase_cost - row.bgad_before_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.cost_spread_after_transfers,
                row.purchase_cost - row.bgad_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in computed
        ),
        "spread_before = purchase - BGAD before; spread_after = purchase - BGAD after",
    )
    add_check(
        checks,
        "provincial_transfer_identity",
        all(
            math.isclose(
                row.bgad_before_transfers + row.transfers_total,
                row.bgad_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.cost_spread_before_transfers - row.cost_spread_after_transfers,
                row.transfers_total,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in computed
        ),
        "BGAD before + transfers = BGAD after and spread before - spread after = transfers",
    )
    add_check(
        checks,
        "provincial_no_nonfinite",
        all(
            value is None or math.isfinite(value)
            for row in provincial
            for value in (
                row.purchase_cost,
                row.bgad_before_transfers,
                row.transfers_total,
                row.bgad_after_transfers,
                row.cost_spread_before_transfers,
                row.cost_spread_after_transfers,
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
        "regional_reference_purchase",
        all(math.isclose(row.reference_purchase, sep_purchase, abs_tol=RATE_TOLERANCE) for row in regional),
        f"provincial SEPM purchase cost held at {sep_purchase:.4f} CAD/m3",
    )
    add_check(
        checks,
        "regional_spread_recompute",
        all(
            math.isclose(
                row.conditional_cost_spread_before,
                row.reference_purchase - row.bgad_before_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            and math.isclose(
                row.conditional_cost_spread_after,
                row.reference_purchase - row.bgad_after_transfers,
                abs_tol=ARITHMETIC_TOLERANCE,
            )
            for row in regional
        ),
        "regional conditional spreads use the provincial purchase cost",
    )
    add_check(
        checks,
        "regional_three_respondent_flags",
        all(row.three_respondent_flag for row in regional if row.sample_n <= 3)
        and any(row.sample_n == 3 for row in regional),
        "every regional group with sample_n<=3 is flagged",
    )
    add_check(
        checks,
        "regional_source_is_reg",
        all(
            row.notes.startswith("REG regional_2022")
            and "Compilation_donnees_regionalisees_2022.pdf" in row.notes
            for row in regional
        ),
        "regional rows cite REG/regional_2022, not T20",
    )

    add_check(
        checks,
        "bmmb_sale_count",
        len(bmmb_rows) == 7,
        f"{len(bmmb_rows)} BMMB aggregate/per-year rows",
    )
    by_scope = {row.scope_id: row for row in bmmb_rows if row.aggregation_level == "delivered_all_years"}
    crown = by_scope["crown_public_forest"]
    teaching = by_scope["teaching_research_forest"]
    all_in = by_scope["all_delivered"]
    add_check(
        checks,
        "bmmb_counts",
        all_in.sale_count == BMMB_SALE_COUNT
        and all_in.awarded_count == BMMB_AWARDED_COUNT
        and all_in.full_rate_sale_count == BMMB_FULL_RATE_COUNT
        and all_in.partial_award_sale_count == BMMB_PARTIAL_COUNT,
        f"{all_in.sale_count} sales; {all_in.awarded_count} awarded; "
        f"{all_in.full_rate_sale_count} full rates; {all_in.partial_award_sale_count} partial awards",
    )
    add_check(
        checks,
        "bmmb_crown_primary",
        crown.is_primary_benchmark
        and not teaching.is_primary_benchmark
        and not all_in.is_primary_benchmark
        and crown.full_rate_sale_count == 9
        and math.isclose(crown.rate_computable_volume_m3, 74_550.0, abs_tol=1.0),
        "Crown-only 9 full rates / 74,550 m3 is the primary benchmark",
    )
    add_check(
        checks,
        "bmmb_teaching_separate",
        teaching.full_rate_sale_count == 2
        and math.isclose(teaching.rate_computable_volume_m3, 15_350.0, abs_tol=1.0),
        "teaching/research forest 2 full rates / 15,350 m3 is separate",
    )
    add_check(
        checks,
        "bmmb_weighted_rates",
        all(
            stats.weighted_rate_cad_m3 is not None
            and math.isclose(stats.weighted_rate_cad_m3, expected, abs_tol=0.0001)
            for stats, expected in ((crown, 35.7547), (teaching, 50.6266), (all_in, 38.2940))
        ),
        "Crown 35.7547; teaching 50.6266; all-in 38.2940 CAD/m3",
    )
    add_check(
        checks,
        "bmmb_reoffer_overlap",
        all_in.reoffer_sale_count == BMMB_REOFFER_COUNT
        and math.isclose(all_in.reoffer_overlap_m3, BMMB_REOFFER_OVERLAP_M3, abs_tol=1.0),
        f"{all_in.reoffer_sale_count} re-offers / {all_in.reoffer_overlap_m3:,.0f} m3 overlap disclosed",
    )
    add_check(
        checks,
        "bmmb_years_dynamic",
        [row.sale_year for row in bmmb_rows if row.aggregation_level == "delivered_year"]
        == ["2011", "2012", "2014", "2015"]
        and all(
            row.price_year_basis == f"nominal_{row.sale_year}"
            for row in bmmb_rows
            if row.aggregation_level == "delivered_year"
        ),
        "per-year rows carry per-year price_year_basis and counts",
    )
    add_check(
        checks,
        "bmmb_2012_no_full_rate",
        next(
            (row for row in bmmb_rows if row.sale_year == "2012" and row.aggregation_level == "delivered_year"),
            None,
        )
        is not None
        and next(row for row in bmmb_rows if row.sale_year == "2012" and row.aggregation_level == "delivered_year").full_rate_sale_count
        == 0,
        "2012 has partial awards only; no full rate is reported",
    )

    add_check(
        checks,
        "labels_not_net_value",
        all(
            row.spread_status in {"computed", "not_computable_missing_bgad_synthesis"}
            for row in provincial
        )
        and all(row.benchmark_id.startswith("bmmb_") for row in bmmb_rows),
        "provincial spread statuses are explicit and BMMB rows are historical benchmarks",
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


def write_csv(path: Path, columns: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
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
    benchmarks: Sequence[ProvincialCostBenchmark],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in benchmarks:
        rows.append(
            {
                "benchmark_id": f"qc_2022_{row.scope.scope_id.removeprefix('qc_')}",
                "geography": "Quebec",
                "region_code": "QC",
                "species_group": row.scope.species_group,
                "species_label": row.scope.species_label,
                "product_or_grade": row.scope.product_or_grade,
                "aggregation_level": row.scope.aggregation_level,
                "purchase_cost_cad_m3": row.purchase_cost,
                "bgad_procurement_before_transfers_cad_m3": row.bgad_before_transfers,
                "transfers_total_cad_m3": row.transfers_total,
                "bgad_procurement_after_transfers_cad_m3": row.bgad_after_transfers,
                "cost_spread_before_transfers_cad_m3": row.cost_spread_before_transfers,
                "cost_spread_after_transfers_cad_m3": row.cost_spread_after_transfers,
                "spread_status": row.spread_status,
                "unit": "CAD/m3",
                "price_year": 2022,
                "currency_basis": CURRENCY_BASIS_SURVEY,
                "survey_year": 2022,
                "purchase_source_table": "T2",
                "bgad_source_table": "T20" if row.bgad_before_transfers is not None else "",
                "purchase_price_basis": "delivered_to_mill",
                "bgad_price_basis": "delivered_to_mill" if row.bgad_before_transfers is not None else "",
                "sample_n": None,
                "confidentiality_flag": row.confidentiality_flag,
                "three_respondent_flag": row.three_respondent_flag,
                "is_additive": False,
                "purchase_semantics": "buyer_side_procurement_cost_not_log_sale_revenue",
                "interpretation": INTERPRETATION,
                "evidence": EVIDENCE_SURVEY,
                "policy_grade": False,
                "net_value_status": NET_VALUE_STATUS,
                "observation_type": "survey_aggregate",
                "accounting_caveats": row.caveats,
                "notes": row.notes,
            }
        )
    return rows


def regional_csv_rows(
    benchmarks: Sequence[RegionalCostBenchmark],
) -> list[dict[str, Any]]:
    base_columns = {
        "region_label": "",
        "statistic": "region_value",
        "aggregation_level": "regional",
        "sample_n": None,
        "confidentiality_flag": "derived",
        "three_respondent_flag": False,
        "bgad_procurement_before_transfers_cad_m3": None,
        "transfers_total_cad_m3": None,
        "bgad_procurement_after_transfers_cad_m3": None,
        "reference_purchase_cad_m3": None,
        "conditional_cost_spread_before_transfers_cad_m3": None,
        "conditional_cost_spread_after_transfers_cad_m3": None,
        "spread_price_assumption": "provincial_sepm_purchase_cost_held_constant",
        "purchase_source_table": "T2",
        "bgad_source_table": "REG",
        "survey_source_id": "regional_2022",
        "source_document": "Compilation_donnees_regionalisees_2022.pdf",
        "columns_are_independent_extrema": False,
        "three_respondent_extreme_columns": "",
        "bound_region_codes": "",
        "unit": "CAD/m3",
        "price_year": 2022,
        "currency_basis": CURRENCY_BASIS_SURVEY,
        "interpretation": INTERPRETATION,
        "evidence": EVIDENCE_REGIONAL,
        "policy_grade": False,
        "net_value_status": NET_VALUE_STATUS,
        "is_additive": False,
        "notes": "",
    }
    rows: list[dict[str, Any]] = []
    for row in benchmarks:
        payload = dict(base_columns)
        payload.update(
            {
                "regional_benchmark_id": f"qc_2022_regional_sepm_{row.region_code}",
                "region_code": row.region_code,
                "region_label": row.region_label,
                "sample_n": row.sample_n,
                "confidentiality_flag": row.confidentiality_flag,
                "three_respondent_flag": row.three_respondent_flag,
                "bgad_procurement_before_transfers_cad_m3": row.bgad_before_transfers,
                "transfers_total_cad_m3": row.transfers_total,
                "bgad_procurement_after_transfers_cad_m3": row.bgad_after_transfers,
                "reference_purchase_cad_m3": row.reference_purchase,
                "conditional_cost_spread_before_transfers_cad_m3": row.conditional_cost_spread_before,
                "conditional_cost_spread_after_transfers_cad_m3": row.conditional_cost_spread_after,
                "notes": row.notes,
            }
        )
        rows.append(payload)

    def bound_row(kind: str) -> dict[str, Any]:
        picker = min if kind == "min" else max
        picks = {
            "bgad_before": picker(benchmarks, key=lambda item: item.bgad_before_transfers),
            "transfers": picker(benchmarks, key=lambda item: item.transfers_total),
            "bgad_after": picker(benchmarks, key=lambda item: item.bgad_after_transfers),
            "spread_before": picker(
                benchmarks, key=lambda item: item.conditional_cost_spread_before
            ),
            "spread_after": picker(
                benchmarks, key=lambda item: item.conditional_cost_spread_after
            ),
        }
        any_three_respondent = any(item.three_respondent_flag for item in benchmarks)
        three_extreme_columns = [
            f"{name}={item.region_code}"
            for name, item in picks.items()
            if item.three_respondent_flag
        ]
        bound_regions = ";".join(f"{name}={item.region_code}" for name, item in picks.items())
        payload = dict(base_columns)
        payload.update(
            {
                "regional_benchmark_id": f"qc_2022_regional_sepm_{kind}",
                "region_code": f"QCSURVEY7_{kind.upper()}",
                "region_label": f"Seven published regional groups ({kind})",
                "statistic": kind,
                "aggregation_level": "regional_range_bound",
                "three_respondent_flag": any_three_respondent,
                "bgad_procurement_before_transfers_cad_m3": picks["bgad_before"].bgad_before_transfers,
                "transfers_total_cad_m3": picks["transfers"].transfers_total,
                "bgad_procurement_after_transfers_cad_m3": picks["bgad_after"].bgad_after_transfers,
                "reference_purchase_cad_m3": sep_purchase_reference(benchmarks),
                "conditional_cost_spread_before_transfers_cad_m3": picks[
                    "spread_before"
                ].conditional_cost_spread_before,
                "conditional_cost_spread_after_transfers_cad_m3": picks[
                    "spread_after"
                ].conditional_cost_spread_after,
                "columns_are_independent_extrema": True,
                "three_respondent_extreme_columns": ";".join(three_extreme_columns),
                "bound_region_codes": bound_regions,
                "notes": (
                    "Columns are independent column-wise extrema across the seven regional "
                    "groups; horizontal identities do not hold across this bound row. "
                    f"Bound regions: {bound_regions}. "
                    + (
                        f"R09 sample_n=3 sets: {', '.join(three_extreme_columns)}. "
                        if three_extreme_columns
                        else ""
                    )
                    + "Conditional on the provincial purchase cost; not a regional net value."
                ),
            }
        )
        return payload

    rows.extend((bound_row("min"), bound_row("max")))
    return rows


def sep_purchase_reference(benchmarks: Sequence[RegionalCostBenchmark]) -> float:
    reference_values = {row.reference_purchase for row in benchmarks}
    if len(reference_values) != 1:
        raise BenchmarkError("regional rows do not share one reference purchase cost")
    return next(iter(reference_values))


def bmmb_csv_rows(rows: Sequence[BMMBStats]) -> list[dict[str, Any]]:
    payload_rows: list[dict[str, Any]] = []
    for stats in rows:
        sale_word = "sale" if stats.sale_count == 1 else "sales"
        full_rate_word = "full rate" if stats.full_rate_sale_count == 1 else "full rates"
        partial_word = "partial award" if stats.partial_award_sale_count == 1 else "partial awards"
        note = (
            f"Nominal CAD, {stats.price_year}; {stats.sale_count} delivered {sale_word}; "
            f"{stats.full_rate_sale_count} {full_rate_word}; "
            f"{stats.partial_award_sale_count} {partial_word} excluded from the rate; "
            "no species or grade split; no deflation; not a 2022 price; not stumpage "
            "and not a confirmed mill-gate price."
        )
        if stats.tenure_scope == "crown_only":
            note += " Crown public-forest sales only; primary benchmark."
        elif stats.tenure_scope == "teaching_only":
            note += " Teaching/research forest lots only; non-Crown and not the primary benchmark."
        else:
            note += " All-in pooled rate mixes Crown and teaching lots; not the primary benchmark."
        if stats.reoffer_sale_count > 0:
            note += (
                f" Re-offer overlap: {stats.reoffer_sale_count} sales / "
                f"{stats.reoffer_overlap_m3:,.0f} m3 are double-counted in offered and "
                "awarded volume totals; the full-rate numerator and denominator are "
                "unaffected because the re-offer sales are a partial award and an unsold lot."
            )
        payload_rows.append(
            {
                "benchmark_id": stats.benchmark_id,
                "scope_id": stats.scope_id,
                "tenure_scope": stats.tenure_scope,
                "is_primary_benchmark": stats.is_primary_benchmark,
                "aggregation_level": stats.aggregation_level,
                "sale_year": stats.sale_year,
                "price_year": stats.price_year,
                "price_year_basis": stats.price_year_basis,
                "currency_basis": CURRENCY_BASIS,
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
                "reoffer_sale_count": stats.reoffer_sale_count,
                "reoffer_overlap_m3": stats.reoffer_overlap_m3,
                "reoffer_sale_ids": stats.reoffer_sale_ids,
                "deflator_applied": False,
                "species_scope": "ALL_SPECIES_MIXED",
                "quality": "NA",
                "grade_split": False,
                "species_allocation": False,
                "not_stumpage": True,
                "destination_reported": False,
                "service_bundle_published": False,
                "interpretation": "bmmb_delivered_sale_benchmark",
                "evidence": EVIDENCE_HISTORICAL,
                "policy_grade": False,
                "net_value_status": NET_VALUE_STATUS,
                "observation_type": "observed_sale_price",
                "notes": note,
            }
        )
    return payload_rows


def build_summary(
    *,
    generated_utc: str,
    provincial: Sequence[ProvincialCostBenchmark],
    regional: Sequence[RegionalCostBenchmark],
    bmmb_rows: Sequence[BMMBStats],
    checks: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    sep = next((row for row in provincial if row.scope.scope_id == "qc_sepm_total"), None)
    if sep is None or sep.cost_spread_before_transfers is None or sep.cost_spread_after_transfers is None:
        raise BenchmarkError("SEPM cost spread is required for the summary")
    before_min = min(row.bgad_before_transfers for row in regional)
    before_max = max(row.bgad_before_transfers for row in regional)
    after_min = min(row.bgad_after_transfers for row in regional)
    after_max = max(row.bgad_after_transfers for row in regional)
    spread_before_min = min(row.conditional_cost_spread_before for row in regional)
    spread_before_max = max(row.conditional_cost_spread_before for row in regional)
    spread_after_min = min(row.conditional_cost_spread_after for row in regional)
    spread_after_max = max(row.conditional_cost_spread_after for row in regional)
    negative_after = sorted(
        row.region_code for row in regional if row.conditional_cost_spread_after < 0
    )
    three_respondent_regions = sorted(
        row.region_code for row in regional if row.three_respondent_flag
    )
    by_scope = {
        row.scope_id: row for row in bmmb_rows if row.aggregation_level == "delivered_all_years"
    }
    crown = by_scope["crown_public_forest"]
    teaching = by_scope["teaching_research_forest"]
    all_in = by_scope["all_delivered"]
    return {
        "artifact_version": "1.0.0",
        "dataset": "qc_procurement_cost_benchmark",
        "status": NET_VALUE_STATUS,
        "interpretation": INTERPRETATION,
        "generated_utc": generated_utc,
        "scope": {
            "jurisdiction": "QC",
            "survey_year": 2022,
            "purchase_price_basis": "delivered_to_mill",
            "unit": "CAD/m3",
            "currency": "CAD_nominal",
        },
        "definitions": {
            "purchase_cost": (
                "Survey T2 purchase.wood_purchase_delivered: buyer-side procurement "
                "cost including purchase, State transfers, transport/unloading and "
                "road costs where applicable. Not observed log-sale revenue."
            ),
            "bgad_before_transfers": (
                "Survey T20 synthesis_procurement.procurement_total_before_transfers: "
                "harvest + road + haul + hosting + other costs."
            ),
            "transfers_total": "Survey T20 synthesis_procurement.transfers_total: payments to the State.",
            "bgad_after_transfers": (
                "Survey T20 synthesis_procurement.procurement_total_after_transfers: "
                "before-transfer BGAD cost + transfers."
            ),
            "cost_spread_before_transfers": (
                "purchase_cost - bgad_before_transfers. A procurement-channel cost "
                "spread across different respondent samples, not forest net value."
            ),
            "cost_spread_after_transfers": (
                "purchase_cost - bgad_after_transfers. A procurement-channel cost "
                "spread across different respondent samples, not forest net value."
            ),
            "identity": (
                "bgad_before + transfers = bgad_after; cost_spread_before - "
                "cost_spread_after = transfers."
            ),
        },
        "labels": {
            "evidence_values": [EVIDENCE_SURVEY, EVIDENCE_REGIONAL, EVIDENCE_HISTORICAL],
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "interpretation": INTERPRETATION,
            "warning": "No row is a forest net value, an observed transaction, or a policy result.",
        },
        "provincial_cost_benchmark": [
            {
                "scope_id": row.scope.scope_id,
                "species_group": row.scope.species_group,
                "product_or_grade": row.scope.product_or_grade,
                "purchase_cost_cad_m3": round(row.purchase_cost, 4),
                "bgad_before_transfers_cad_m3": (
                    round(row.bgad_before_transfers, 4)
                    if row.bgad_before_transfers is not None
                    else None
                ),
                "transfers_total_cad_m3": (
                    round(row.transfers_total, 4) if row.transfers_total is not None else None
                ),
                "bgad_after_transfers_cad_m3": (
                    round(row.bgad_after_transfers, 4)
                    if row.bgad_after_transfers is not None
                    else None
                ),
                "cost_spread_before_transfers_cad_m3": (
                    round(row.cost_spread_before_transfers, 4)
                    if row.cost_spread_before_transfers is not None
                    else None
                ),
                "cost_spread_after_transfers_cad_m3": (
                    round(row.cost_spread_after_transfers, 4)
                    if row.cost_spread_after_transfers is not None
                    else None
                ),
                "spread_status": row.spread_status,
                "confidentiality_flag": row.confidentiality_flag,
                "three_respondent_flag": row.three_respondent_flag,
            }
            for row in provincial
        ],
        "headline_sepm": {
            "purchase_cost_cad_m3": round(sep.purchase_cost, 4),
            "bgad_before_transfers_cad_m3": round(sep.bgad_before_transfers, 4),
            "transfers_total_cad_m3": round(sep.transfers_total, 4),
            "bgad_after_transfers_cad_m3": round(sep.bgad_after_transfers, 4),
            "cost_spread_before_transfers_cad_m3": round(sep.cost_spread_before_transfers, 4),
            "cost_spread_after_transfers_cad_m3": round(sep.cost_spread_after_transfers, 4),
            "interpretation": INTERPRETATION,
            "net_value_status": NET_VALUE_STATUS,
        },
        "regional_range": {
            "region_count": len(regional),
            "reference_purchase_cad_m3": round(sep.purchase_cost, 4),
            "bgad_before_transfers_min_cad_m3": round(before_min, 4),
            "bgad_before_transfers_max_cad_m3": round(before_max, 4),
            "bgad_after_transfers_min_cad_m3": round(after_min, 4),
            "bgad_after_transfers_max_cad_m3": round(after_max, 4),
            "conditional_cost_spread_before_min_cad_m3": round(spread_before_min, 4),
            "conditional_cost_spread_before_max_cad_m3": round(spread_before_max, 4),
            "conditional_cost_spread_after_min_cad_m3": round(spread_after_min, 4),
            "conditional_cost_spread_after_max_cad_m3": round(spread_after_max, 4),
            "negative_after_transfer_regions": negative_after,
            "three_respondent_regions": three_respondent_regions,
            "columns_are_independent_extrema": True,
            "horizontal_identities_hold": False,
            "caveat": (
                "Regional purchase prices are not published. Conditional cost spreads "
                "hold the provincial SEPM purchase cost constant; bound rows are "
                "independent column-wise extrema and horizontal identities do not hold. "
                "Not regional net values."
            ),
        },
        "bmmb_historical_benchmark": {
            "primary_scope": "crown_public_forest",
            "sale_count": all_in.sale_count,
            "awarded_count": all_in.awarded_count,
            "full_rate_sale_count": all_in.full_rate_sale_count,
            "partial_award_sale_count": all_in.partial_award_sale_count,
            "years": [
                row.sale_year for row in bmmb_rows if row.aggregation_level == "delivered_year"
            ],
            "volume_m3": round(all_in.volume_m3, 4),
            "rate_computable_volume_m3": round(all_in.rate_computable_volume_m3, 4),
            "crown_only": {
                "sale_count": crown.sale_count,
                "full_rate_sale_count": crown.full_rate_sale_count,
                "volume_m3": round(crown.rate_computable_volume_m3, 4),
                "weighted_rate_cad_m3": round(crown.weighted_rate_cad_m3, 4),
                "median_rate_cad_m3": round(crown.median_rate_cad_m3, 4),
            },
            "teaching_research": {
                "sale_count": teaching.sale_count,
                "full_rate_sale_count": teaching.full_rate_sale_count,
                "volume_m3": round(teaching.rate_computable_volume_m3, 4),
                "weighted_rate_cad_m3": round(teaching.weighted_rate_cad_m3, 4),
                "median_rate_cad_m3": round(teaching.median_rate_cad_m3, 4),
            },
            "all_in": {
                "weighted_rate_cad_m3": round(all_in.weighted_rate_cad_m3, 4),
                "median_rate_cad_m3": round(all_in.median_rate_cad_m3, 4),
            },
            "reoffer_sale_count": all_in.reoffer_sale_count,
            "reoffer_overlap_m3": round(all_in.reoffer_overlap_m3, 4),
            "currency_basis": CURRENCY_BASIS,
            "deflator_applied": False,
            "grade_split": False,
            "species_allocation": False,
            "caveat": (
                "Nominal 2011-2015, 18 delivered sales, no grade split, no deflation. "
                f"The 2012 re-offer overlap ({all_in.reoffer_sale_count} sales / "
                f"{all_in.reoffer_overlap_m3:,.0f} m3) is disclosed but not netted "
                "because both re-offer sales are excluded from the full-rate "
                "numerator and denominator."
            ),
        },
        "volume_scaled_diagnostic": {
            "produced": False,
            "reason": (
                "No QC or M CAD net-value sensitivity is produced. The provincial "
                "allocation files are screening totals and monetising them would "
                "manufacture a misleading net-value estimate."
            ),
        },
        "validation": {
            "status": "pass",
            "check_count": len(checks),
            "checks": list(checks),
        },
        "non_claims": [
            "Not a forest net value; net_value_status=not_estimated.",
            "Not an observed transaction.",
            "Not a policy result and not policy-grade.",
            "Not a mill margin; transformation and lumber revenue are excluded.",
            "Not a stumpage rate.",
            "Not a current or representative BMMB price.",
            "Not an estimate of the QC provincial harvest population.",
            "Not merged into the forest cost/revenue ledger, Forest LP, or UI.",
            "Not deflated; BMMB values remain nominal 2011-2015.",
            "Not species- or grade-allocated.",
            "No volume-scaled M CAD diagnostic is produced.",
        ],
        "limitations": [
            "Survey aggregates only; no mill-level or respondent-level values.",
            "Provincial survey means are not volume-weighted to the provincial harvest.",
            "T2 purchase and T20 BGAD are different respondent samples and accounting bases.",
            "T20 excludes species gain/loss; PRCCM rebates sit inside transfers.",
            "Regional data cover SEPM only and publish no regional purchase price.",
            "Three-respondent means are statistically fragile; regional R09 has sample_n=3.",
            "Pine and cedar purchase costs are three-respondent means.",
            "No cost spread is computable for pine, cedar, or species totals without matching T20 BGAD rows.",
            "Hardwood and poplar spreads are product-specific, not a species total.",
            "BMMB delivered sales end in 2015 and do not publish species, grade, destination, or service-bundle detail.",
            f"The 2012 BMMB re-offer overlap double-counts {all_in.reoffer_overlap_m3:,.0f} m3 in offered/awarded volumes.",
        ],
    }


def write_report(
    path: Path,
    *,
    generated_utc: str,
    provincial: Sequence[ProvincialCostBenchmark],
    regional: Sequence[RegionalCostBenchmark],
    bmmb_rows: Sequence[BMMBStats],
    checks: Sequence[Mapping[str, Any]],
) -> None:
    def fmt(value: float | None, digits: int = 2) -> str:
        return "—" if value is None else f"{value:.{digits}f}"

    by_scope = {row.scope.scope_id: row for row in provincial}
    sep = by_scope["qc_sepm_total"]
    if sep.cost_spread_before_transfers is None or sep.cost_spread_after_transfers is None:
        raise BenchmarkError("SEPM spread is required for the report")
    hardwood = by_scope["qc_feuillus_durs_sciage"]
    poplar = by_scope["qc_peupliers_trituration"]

    before_min = min(row.bgad_before_transfers for row in regional)
    before_max = max(row.bgad_before_transfers for row in regional)
    after_min = min(row.bgad_after_transfers for row in regional)
    after_max = max(row.bgad_after_transfers for row in regional)
    spread_before_min = min(row.conditional_cost_spread_before for row in regional)
    spread_before_max = max(row.conditional_cost_spread_before for row in regional)
    spread_after_min = min(row.conditional_cost_spread_after for row in regional)
    spread_after_max = max(row.conditional_cost_spread_after for row in regional)
    negative_after_regions = sorted(
        row.region_code for row in regional if row.conditional_cost_spread_after < 0
    )
    by_bmmb_scope = {
        row.scope_id: row for row in bmmb_rows if row.aggregation_level == "delivered_all_years"
    }
    crown = by_bmmb_scope["crown_public_forest"]
    teaching = by_bmmb_scope["teaching_research_forest"]
    all_in = by_bmmb_scope["all_delivered"]
    year_rows = [row for row in bmmb_rows if row.aggregation_level == "delivered_year"]

    lines = [
        "# QC procurement cost benchmark — 2022 survey and BMMB delivered sales",
        "",
        (f"**Status:** procurement-cost benchmark only. Generated {generated_utc}. "
        f"`net_value_status={NET_VALUE_STATUS}`; `interpretation={INTERPRETATION}`; "
        "`policy_grade=false` on every row."),
        "",
        ("This note replaces the earlier framing of the 2022 survey difference as a "
        "forest-sector residual or net value. It is a **procurement-channel cost "
        "spread across different respondent samples**, not forest net value, not a "
        "mill margin, and not a policy result. The old `net_value_benchmark` artifact "
        "is retained only for audit and is marked deprecated. No QC or M CAD "
        "volume-scaled diagnostic is produced."),
        "",
        "## Definitions",
        "",
        "| Term | Definition |",
        "|---|---|",
        "| Purchase cost | Survey T2 `purchase.wood_purchase_delivered`: buyer-side procurement cost including purchase, State transfers, transport/unloading, and road costs where applicable. It is not observed log-sale revenue. |",
        "| BGAD before transfers | Survey T20 `synthesis_procurement.procurement_total_before_transfers`: harvest + road + haul + hosting + other costs. |",
        "| Transfers | Survey T20 `synthesis_procurement.transfers_total`: payments to the State. |",
        "| BGAD after transfers | Survey T20 `synthesis_procurement.procurement_total_after_transfers`. |",
        "| Cost spread before | `purchase cost - BGAD before transfers` (procurement-channel difference). |",
        "| Cost spread after | `purchase cost - BGAD after transfers` (procurement-channel difference). |",
        "",
        ("The arithmetic identity is `BGAD before + transfers = BGAD after`, so "
        "`cost spread before - cost spread after = transfers`. That identity does not "
        "turn the spread into net value."),
        "",
        "## Accounting caveats",
        "",
        "- T2 purchase is a **buyer-side procurement cost**, not observed log-sale revenue or stumpage.",
        "- T20 BGAD excludes species gain/loss (reported separately in T22); for SEPM the exclusion is larger than the after-transfer spread.",
        "- PRCCM road rebates sit inside T10/T20 `transfers_total`, not in the T20 `road` component. Do not combine `T5.road_total_after_prccm` with `T10.transfers_total`; that would double-count the rebate.",
        "- Purchase and BGAD cover different respondent samples. Their difference is a channel cost spread, not an arm's-length forest-sector net value.",
        "",
        "## Provincial procurement cost benchmark (2022, CAD/m3 nominal)",
        "",
        "| Species | Product | Purchase cost | BGAD before | Transfers | BGAD after | Spread before | Spread after | Status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    species_labels = {
        "sepm": "SEPM",
        "feuillus_durs": "Hardwood",
        "peupliers": "Poplar",
        "pins": "Pine",
        "thuyas": "Cedar",
    }
    for row in provincial:
        lines.append(
            f"| {species_labels.get(row.scope.species_group, row.scope.species_group)} | {row.scope.product_or_grade} | {fmt(row.purchase_cost)} | {fmt(row.bgad_before_transfers)} | {fmt(row.transfers_total)} | {fmt(row.bgad_after_transfers)} | {fmt(row.cost_spread_before_transfers)} | {fmt(row.cost_spread_after_transfers)} | {row.spread_status} |"
        )
    lines.extend(
        [
            "",
            ("Pine and cedar have a purchase cost but no matching T20 BGAD synthesis row, "
            "so their spread is not computable. Hardwood and poplar totals are "
            "purchase-only for the same reason. Product rows are not additive to a "
            "species total without published volumes."),
            "",
            (f"**SEPM headline.** Purchase cost **{fmt(sep.purchase_cost)}**, BGAD before transfers "
            f"**{fmt(sep.bgad_before_transfers)}**, transfers **{fmt(sep.transfers_total)}**, BGAD after transfers **{fmt(sep.bgad_after_transfers)}**. "
            f"The before-transfer channel spread is **{fmt(sep.cost_spread_before_transfers)}** and the after-transfer "
            f"channel spread is **{fmt(sep.cost_spread_after_transfers)}** CAD/m3. The after-transfer spread is small "
            "because nearly all of the pre-transfer difference is accounted for by "
            "transfers; it is still a cost spread, not net value."),
            "",
            ("**Product-level exceptions.** Hardwood sciage spread before transfers "
            f"**{fmt(hardwood.cost_spread_before_transfers)}**, after transfers "
            f"**{fmt(hardwood.cost_spread_after_transfers)}** CAD/m3. Poplar "
            "trituration spread before transfers "
            f"**{fmt(poplar.cost_spread_before_transfers)}**, after transfers "
            f"**{fmt(poplar.cost_spread_after_transfers)}** CAD/m3. Negative spreads "
            "are reported as published arithmetic, not clipped, and are not evidence "
            "of an error by themselves."),
            "",
            "## Regional conditional range (SEPM only, 2022)",
            "",
            ("The seven regional groups come from **REG `regional_2022`**, "
            "`Compilation_donnees_regionalisees_2022.pdf`, not from T20. They publish "
            "regional BGAD procurement costs but no regional purchase cost. The "
            "conditional spread below holds the provincial SEPM purchase cost of "
            f"**{fmt(sep.purchase_cost)}** constant. It is not an observed regional "
            "net value."),
            "",
            "| Region | n | BGAD before | Transfers | BGAD after | Conditional spread before | Conditional spread after |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in regional:
        flag = " (n=3)" if row.three_respondent_flag else ""
        lines.append(
            f"| {row.region_code}{flag} | {row.sample_n} | {fmt(row.bgad_before_transfers)} | "
            f"{fmt(row.transfers_total)} | {fmt(row.bgad_after_transfers)} | "
            f"{fmt(row.conditional_cost_spread_before)} | {fmt(row.conditional_cost_spread_after)} |"
        )
    lines.extend(
        [
            "",
            (f"Range: BGAD before **{fmt(before_min)}–{fmt(before_max)}**, after "
            f"**{fmt(after_min)}–{fmt(after_max)}**; conditional spread before "
            f"**{fmt(spread_before_min)}–{fmt(spread_before_max)}**, after "
            f"**{fmt(spread_after_min)}–{fmt(spread_after_max)}** CAD/m3. "
            "R09 has `sample_n=3` and carries the three-respondent flag. "
            f"Regions with a negative after-transfer conditional spread: "
            f"{', '.join(negative_after_regions)}. The min/max bound rows in the CSV "
            "are independent column-wise extrema across the seven groups, so horizontal "
            "identities such as `before + transfers = after` do not hold across a bound "
            "row. The bound rows also retain the three-respondent flag."),
            "",
            "## BMMB delivered-sale benchmark (nominal CAD)",
            "",
            ("BMMB delivered auction results are a separate historical benchmark. They are "
            "**not** a 2022 price, **not** stumpage, and **not** a confirmed mill-gate "
            "price. **Crown public-forest sales are the primary series.** Teaching and "
            "research forest sales are shown separately; the all-in pooled rate is "
            "secondary."),
            "",
            "| Scope | Sales | Full rates | Partial awards | Offered vol. (m3) | Awarded val. (CAD) | Rate vol. (m3) | Rate val. (CAD) | Weighted (CAD/m3) | Median (CAD/m3) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for stats in (crown, teaching, all_in, *year_rows):
        if stats.aggregation_level == "delivered_year":
            label = f"All delivered, {stats.sale_year}"
        else:
            label = {
                "crown_public_forest": "Crown public forest, 2011–2015 (primary)",
                "teaching_research_forest": "Teaching/research forest, 2014–2015",
                "all_delivered": "All delivered, 2011–2015",
            }[stats.scope_id]
        lines.append(
            f"| {label} | {stats.sale_count} | {stats.full_rate_sale_count} | "
            f"{stats.partial_award_sale_count} | {stats.volume_m3:,.0f} | "
            f"{stats.observed_value_cad:,.2f} | {stats.rate_computable_volume_m3:,.0f} | "
            f"{stats.rate_computable_value_cad:,.2f} | {fmt(stats.weighted_rate_cad_m3, 4)} | "
            f"{fmt(stats.median_rate_cad_m3, 4)} |"
        )
    lines.extend(
        [
            "",
            (f"**Crown-only primary rate:** {crown.rate_computable_value_cad:,.2f} / "
            f"{crown.rate_computable_volume_m3:,.0f} = "
            f"**{fmt(crown.weighted_rate_cad_m3, 4)} CAD/m3** over "
            f"{crown.full_rate_sale_count} full-rate sales. Teaching/research: "
            f"{teaching.rate_computable_value_cad:,.2f} / "
            f"{teaching.rate_computable_volume_m3:,.0f} = "
            f"**{fmt(teaching.weighted_rate_cad_m3, 4)} CAD/m3** over "
            f"{teaching.full_rate_sale_count} sales. All-in: "
            f"{all_in.rate_computable_value_cad:,.2f} / "
            f"{all_in.rate_computable_volume_m3:,.0f} = "
            f"**{fmt(all_in.weighted_rate_cad_m3, 4)} CAD/m3**."),
            "",
            (f"**Re-offer caveat.** The 2012 archive re-offers {all_in.reoffer_sale_count} "
            f"lots ({all_in.reoffer_overlap_m3:,.0f} m3), double-counting offered and "
            "awarded volume totals. The full-rate numerator and denominator are "
            "unaffected because those two sales are excluded: one is a partial award "
            "and one is unsold. Offered volume is not the rate denominator."),
            "",
            ("The per-year rows in the CSV carry per-year counts and per-year price-year "
            "bases. No deflation is applied. The archive does not publish species, "
            "grade, destination, or service-bundle allocation."),
            "",
            "## Validation",
            "",
            (f"All **{len(checks)}** checks pass. The builder recomputes the T20 component "
            "identity, the transfer identity, every provincial and regional cost spread, "
            "the BMMB Crown/teaching/all-in rates, per-year counts, and the re-offer "
            "overlap. It fails closed on missing rows, duplicate keys, unexpected units, "
            "unexpected survey years, non-finite values, non-binary booleans, malformed "
            "rates, and arithmetic mismatches. T2 purchase rows are used once; the "
            "overlapping T21 purchase rows are used only as a cross-check."),
            "",
            "## Non-claims and limitations",
            "",
            "- Not a forest net value; `net_value_status=not_estimated`.",
            "- Not an observed transaction and not a policy result.",
            "- Not `policy_grade`.",
            "- Not a mill margin; transformation and lumber revenue are excluded.",
            "- Not a stumpage rate.",
            "- Not a current or representative BMMB price.",
            "- Not a provincial harvest-population estimate.",
            "- Not merged into the ledger, Forest LP, UI, or OSeCAN.",
            "- Not deflated; BMMB remains nominal 2011–2015.",
            "- Not species- or grade-allocated.",
            "- No volume-scaled M CAD diagnostic is produced.",
            "",
            "## Sources",
            "",
            "- `data/derived/national/harvest_basis/qc_cost_survey_2022/qc_cost_revenue_survey.csv` (T2 purchase cost and T20 BGAD synthesis rows).",
            "- `data/derived/national/harvest_basis/qc_cost_survey_2022/README.md` and `manifest.json` (accounting caveats and provenance).",
            "- `data/derived/national/harvest_basis/qc_cost_survey_2022/qc_cost_revenue_survey.csv` REG `regional_2022` rows from `Compilation_donnees_regionalisees_2022.pdf` (regional costs).",
            "- `data/derived/national/harvest_basis/bmmb_delivered/bmmb_delivered_sales.csv` (delivered-to-destination sales).",
            "",
            "Reproduce with:",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.harvest.procurement_benchmark_v1",
            ".venv/bin/python -m pytest tests/test_procurement_benchmark.py -q",
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
    "survey_year",
    "purchase_source_table",
    "bgad_source_table",
    "purchase_price_basis",
    "bgad_price_basis",
    "sample_n",
    "confidentiality_flag",
    "three_respondent_flag",
    "is_additive",
    "purchase_semantics",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "observation_type",
    "accounting_caveats",
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
    "bgad_procurement_before_transfers_cad_m3",
    "transfers_total_cad_m3",
    "bgad_procurement_after_transfers_cad_m3",
    "reference_purchase_cad_m3",
    "conditional_cost_spread_before_transfers_cad_m3",
    "conditional_cost_spread_after_transfers_cad_m3",
    "spread_price_assumption",
    "purchase_source_table",
    "bgad_source_table",
    "survey_source_id",
    "source_document",
    "columns_are_independent_extrema",
    "three_respondent_extreme_columns",
    "bound_region_codes",
    "unit",
    "price_year",
    "currency_basis",
    "interpretation",
    "evidence",
    "policy_grade",
    "net_value_status",
    "is_additive",
    "notes",
)

BMMB_COLUMNS = (
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


def build(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    survey_path: Path = SURVEY_PATH,
    bmmb_sales_path: Path = BMMB_SALES_PATH,
) -> BuildResult:
    generated_utc = utc_now()
    survey_rows = parse_survey_rows(survey_path)
    purchases = select_purchase_costs(survey_rows)
    t2_t21_matches = validate_t2_t21_purchase_consistency(survey_rows)
    bgad = select_bgad_synthesis(survey_rows)
    provincial = build_provincial_benchmarks(purchases, bgad)

    sep = next((row for row in provincial if row.scope.scope_id == "qc_sepm_total"), None)
    if sep is None:
        raise BenchmarkError("SEPM provincial benchmark is missing")
    regional_rows = select_regional_rows(survey_rows)
    regional = build_regional_benchmarks(
        regional_rows, reference_purchase=sep.purchase_cost
    )

    bmmb_sales = parse_bmmb_sales(bmmb_sales_path)
    bmmb_rows = build_bmmb_stats(bmmb_sales)

    checks = validate_results(
        survey_rows=survey_rows,
        provincial=provincial,
        regional=regional,
        bmmb_rows=bmmb_rows,
        t2_t21_matches=t2_t21_matches,
        sep_purchase=sep.purchase_cost,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    provincial_path = output_dir / PROVINCIAL_CSV
    regional_path = output_dir / REGIONAL_CSV
    bmmb_path = output_dir / BMMB_CSV
    summary_path = output_dir / SUMMARY_JSON
    report_path = output_dir / REPORT_MD
    manifest_path = output_dir / MANIFEST_JSON

    write_csv(provincial_path, PROVINCIAL_COLUMNS, provincial_csv_rows(provincial))
    write_csv(regional_path, REGIONAL_COLUMNS, regional_csv_rows(regional))
    write_csv(bmmb_path, BMMB_COLUMNS, bmmb_csv_rows(bmmb_rows))

    summary = build_summary(
        generated_utc=generated_utc,
        provincial=provincial,
        regional=regional,
        bmmb_rows=bmmb_rows,
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
    ]
    summary["outputs"] = [
        file_record(provincial_path, role="provincial procurement cost benchmark", rows=len(provincial)),
        file_record(regional_path, role="regional conditional range", rows=len(regional) + 2),
        file_record(bmmb_path, role="BMMB historical benchmark", rows=len(bmmb_rows)),
    ]
    write_json(summary_path, summary)
    write_report(
        report_path,
        generated_utc=generated_utc,
        provincial=provincial,
        regional=regional,
        bmmb_rows=bmmb_rows,
        checks=checks,
    )

    manifest = {
        "artifact_version": "1.0.0",
        "dataset": "qc_procurement_cost_benchmark",
        "generated_utc": generated_utc,
        "status": NET_VALUE_STATUS,
        "interpretation": INTERPRETATION,
        "generator": {
            "script": "workflows/national/harvest/procurement_benchmark_v1.py",
            "command": "python -m workflows.national.harvest.procurement_benchmark_v1",
        },
        "labels": {
            "evidence_values": [EVIDENCE_SURVEY, EVIDENCE_REGIONAL, EVIDENCE_HISTORICAL],
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "interpretation": INTERPRETATION,
        },
        "parameters": {
            "survey_year": 2022,
            "price_year": 2022,
            "currency_basis": CURRENCY_BASIS_SURVEY,
            "bmmb_currency_basis": CURRENCY_BASIS,
            "provincial_spread_before": "purchase_cost - bgad_before_transfers",
            "provincial_spread_after": "purchase_cost - bgad_after_transfers",
            "regional_reference_purchase_cad_m3": round(sep.purchase_cost, 4),
            "regional_source_id": "regional_2022",
            "regional_source_table": "REG",
            "regional_source_document": "Compilation_donnees_regionalisees_2022.pdf",
            "bmmb_primary_scope": "crown_public_forest",
            "volume_scaled_diagnostic": False,
        },
        "inputs": summary["inputs"],
        "outputs": [
            file_record(provincial_path, role="provincial procurement cost benchmark", rows=len(provincial)),
            file_record(regional_path, role="regional conditional range", rows=len(regional) + 2),
            file_record(bmmb_path, role="BMMB historical benchmark", rows=len(bmmb_rows)),
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
        bmmb_rows=bmmb_rows,
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
    args = parser.parse_args(argv)
    result = build(
        output_dir=args.output_dir,
        survey_path=args.survey,
        bmmb_sales_path=args.bmmb_sales,
    )
    crown = next(row for row in result.bmmb_rows if row.scope_id == "crown_public_forest")
    print(f"wrote {len(result.provincial)} provincial rows to {args.output_dir}")
    print(f"wrote {len(result.regional)} regional rows plus min/max bounds")
    print(
        "BMMB Crown-only weighted rate "
        f"{crown.weighted_rate_cad_m3:.4f} CAD/m3 (nominal 2011-2015)"
    )
    print(f"validation: {len(result.checks)} checks pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
