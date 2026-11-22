"""Provincial logging-industry cost proxy from Statistics Canada open data.

The proxy divides Statistics Canada Table 16-10-0114-01 (Logging industries,
principal statistics by NAICS) total expenses for NAICS 11331 by a provincial
2024 harvest denominator.  The result is an **industry aggregate proxy**, not
a logging cost curve, not a haul-cost estimate, and not an observed
contractor rate.

Every output row is labelled ``industry_aggregate_proxy``, ``observed=false``
and ``policy_grade=false``; ``not_logging_haul_breakdown=true`` records that
the source cannot separate logging from hauling or other establishment
activity.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.statcan_logging_cost_proxy
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
import tempfile
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "statcan_logging_cost_proxy"
DEFAULT_HARVEST_CSV = HARVEST_BASIS_DIR / "provincial_allocation/119m.csv"

STATCAN_URL = "https://www150.statcan.gc.ca/n1/tbl/csv/16100114-eng.zip"
STATCAN_TABLE = "16-10-0114-01"
STATCAN_ARCHIVE_LAST_MODIFIED = "2025-12-18"
STATCAN_CSV_MEMBER = "16100114.csv"

ARTIFACT_ID = "statcan_logging_cost_proxy"
ARTIFACT_VERSION = "1.0.0"
EVIDENCE_LABEL = "industry_aggregate_proxy"

PROXY_CSV = "provincial_logging_cost_proxy.csv"
SOURCES_CSV = "sources.csv"
SUMMARY_JSON = "summary.json"
README_MD = "README.md"
MANIFEST_JSON = "manifest.json"

STATCAN_NAICS_INDUSTRY = "Logging [11331]"
STATCAN_NAICS_CONTRACT = "Contract Logging [113312]"
NAICS_INDUSTRY_CODE = "11331"
NAICS_CONTRACT_CODE = "113312"
NAICS_REQUESTED = "1133"

INDUSTRY_STATS = {
    "total_expenses": "Total expenses",
    "salaries_wages": "Total salaries and wages, direct and indirect labour",
    "materials_supplies": "Cost of materials and supplies",
    "energy_fuel": "Total cost of energy, water utility and vehicle fuel",
}
DOLLAR_STATS = frozenset(INDUSTRY_STATS.values())

SCALAR_MULTIPLIER = {
    "units": 1.0,
    "thousands": 1_000.0,
    "millions": 1_000_000.0,
    "billions": 1_000_000_000.0,
}
ALLOWED_STATUSES = frozenset({"", "A", "B", "C", "D", "E", "F", "x", ".."})
MISSING_STATUSES = frozenset({"F", "x", ".."})
ALLOWED_SYMBOLS = frozenset({"", "0s", "<LOD"})
STATUS_RANK = {
    "": 0,
    "A": 1,
    "B": 2,
    "C": 3,
    "D": 4,
    "E": 5,
    "F": 6,
    "x": 7,
    "..": 8,
    "not_published": 9,
}

CAVEATS = (
    {
        "id": "industry_aggregate_not_logging_or_haul_cost",
        "text": (
            "Total expenses are a NAICS 11331 establishment aggregate. They "
            "include administration, depreciation, overhead, non-logging "
            "activity and any in-forest log hauling performed by logging "
            "establishments; the table does not publish a logging-only or "
            "haul-only cost."
        ),
    },
    {
        "id": "double_counting_with_contract_and_haul_models",
        "text": (
            "Do not add this aggregate to a separate truck-haul or "
            "silviculture cost without checking overlap. NAICS 11331 is the "
            "parent of 113311 and 113312; summing both child industries would "
            "double count. Payments from 113311 establishments to 113312 "
            "contractors may also appear in materials/supplies, creating "
            "intra-industry subcontract double counting at the parent level."
        ),
    },
    {
        "id": "survey_coverage_bias",
        "text": (
            "The Annual Survey of Manufacturing and Logging Industries covers "
            "employer establishments. Non-employer contractors, owner-operators "
            "and community or informal harvest are not fully represented, "
            "while the harvest denominator includes all provincial harvest. "
            "This can bias CAD/m3 downward, especially in the territories."
        ),
    },
    {
        "id": "year_and_price_basis",
        "text": (
            "Expenses are reference year 2024, the latest year in the "
            "2025-12-18 table release, and are preliminary and subject to "
            "revision. Values are nominal 2024 CAD; no deflator or escalation "
            "is applied. The primary harvest denominator is the 2024 observed "
            "provincial harvest in provincial_allocation/119m.csv; the 119 Mm3 "
            "allocation column is a sensitivity, not the primary basis."
        ),
    },
    {
        "id": "component_detail",
        "text": (
            "Only four expense components are published: total salaries and "
            "wages, materials and supplies, total energy/water utility/vehicle "
            "fuel, and an unpriced residual. Since reference year 2022 the "
            "energy, water and vehicle-fuel detail is no longer published. "
            "Shares use total expenses as the denominator."
        ),
    },
    {
        "id": "quality_symbols",
        "text": (
            "Statistics Canada quality symbols are preserved: A/B/C/D/E are "
            "published estimates (D means acceptable, E use with caution), F "
            "is too unreliable, and x is suppressed. D is a data-quality flag, "
            "not a deletion. Suppressed or unavailable cells are blank, never "
            "zero-filled. Nunavut has a reported zero expense and zero harvest "
            "denominator, so its CAD/m3 is undefined and left blank."
        ),
    },
    {
        "id": "jurisdiction_aggregation",
        "text": (
            "Only the 13 individual provinces and territories are used. "
            "Statistics Canada Atlantic Region and Prairie Region rows are "
            "aggregates and are excluded to avoid double counting. Canada is "
            "used only as national context."
        ),
    },
)


@dataclass(frozen=True)
class ProvinceSpec:
    code: str
    name: str


PROVINCES: tuple[ProvinceSpec, ...] = (
    ProvinceSpec("BC", "British Columbia"),
    ProvinceSpec("AB", "Alberta"),
    ProvinceSpec("SK", "Saskatchewan"),
    ProvinceSpec("MB", "Manitoba"),
    ProvinceSpec("ON", "Ontario"),
    ProvinceSpec("QC", "Quebec"),
    ProvinceSpec("NB", "New Brunswick"),
    ProvinceSpec("NS", "Nova Scotia"),
    ProvinceSpec("NL", "Newfoundland and Labrador"),
    ProvinceSpec("PE", "Prince Edward Island"),
    ProvinceSpec("YT", "Yukon"),
    ProvinceSpec("NT", "Northwest Territories"),
    ProvinceSpec("NU", "Nunavut"),
)


@dataclass(frozen=True)
class StatcanCell:
    value_cad: float | None
    status: str
    symbol: str


@dataclass(frozen=True)
class IndustryPanel:
    total_expenses: StatcanCell
    salaries_wages: StatcanCell
    materials_supplies: StatcanCell
    energy_fuel: StatcanCell

    def cells(self) -> tuple[StatcanCell, ...]:
        return (
            self.total_expenses,
            self.salaries_wages,
            self.materials_supplies,
            self.energy_fuel,
        )


@dataclass(frozen=True)
class HarvestRecord:
    province: str
    harvest_m3: float | None
    scenario_119m_harvest_m3: float | None
    basis: str
    is_fallback: bool


@dataclass(frozen=True)
class SourceArtifacts:
    csv_path: Path
    archive_sha256: str
    csv_sha256: str
    archive_bytes: int
    csv_bytes: int
    used_supplied_csv: bool


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path, required: set[str]) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or ())
        missing = required - fieldnames
        if missing:
            raise ValueError(f"{path}: missing CSV columns {sorted(missing)}")
        return list(reader)


def read_statcan_rows(path: Path) -> list[dict[str, str]]:
    required = {
        "REF_DATE",
        "GEO",
        "Principal statistics",
        "North American Industry Classification System (NAICS)",
        "UOM",
        "SCALAR_FACTOR",
        "VALUE",
        "STATUS",
        "SYMBOL",
    }
    return _read_csv(path, required)


def parse_dollar_cell(row: dict[str, str]) -> StatcanCell:
    status = row["STATUS"].strip()
    symbol = row["SYMBOL"].strip()
    if status not in ALLOWED_STATUSES:
        raise ValueError(
            "Unexpected StatCan STATUS "
            f"{status!r} for {row['REF_DATE']} {row['GEO']} "
            f"{row['Principal statistics']!r}"
        )
    if symbol not in ALLOWED_SYMBOLS:
        raise ValueError(
            "Unexpected StatCan SYMBOL "
            f"{symbol!r} for {row['REF_DATE']} {row['GEO']} "
            f"{row['Principal statistics']!r}"
        )
    if status in MISSING_STATUSES:
        return StatcanCell(None, status, symbol)

    raw_value = row["VALUE"].strip()
    if not raw_value:
        return StatcanCell(None, status or "not_published", symbol)

    if row["UOM"].strip() != "Dollars":
        raise ValueError(
            f"Expected Dollars, found {row['UOM']!r} for {row['GEO']} "
            f"{row['Principal statistics']!r}"
        )
    scalar_factor = row["SCALAR_FACTOR"].strip()
    if scalar_factor not in SCALAR_MULTIPLIER:
        raise ValueError(f"Unexpected SCALAR_FACTOR {scalar_factor!r}")
    try:
        value = float(raw_value) * SCALAR_MULTIPLIER[scalar_factor]
    except ValueError as exc:
        raise ValueError(f"Non-numeric StatCan VALUE {raw_value!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Non-finite StatCan VALUE {raw_value!r}")
    if value < 0.0:
        raise ValueError(
            f"Negative expense value {value!r} for {row['GEO']} "
            f"{row['Principal statistics']!r}"
        )
    return StatcanCell(value, status, symbol)


def build_cell_index(
    rows: list[dict[str, str]],
) -> dict[tuple[str, str, str, str], StatcanCell]:
    index: dict[tuple[str, str, str, str], StatcanCell] = {}
    for row in rows:
        statistic = row["Principal statistics"]
        if statistic not in DOLLAR_STATS:
            continue
        key = (
            row["REF_DATE"].strip(),
            row["GEO"].strip(),
            row["North American Industry Classification System (NAICS)"].strip(),
            statistic,
        )
        if key in index:
            raise ValueError(f"Duplicate StatCan cell {key}")
        index[key] = parse_dollar_cell(row)
    return index


def _cell(
    index: dict[tuple[str, str, str, str], StatcanCell],
    year: int,
    geo: str,
    naics: str,
    statistic: str,
) -> StatcanCell:
    return index.get(
        (str(year), geo, naics, statistic),
        StatcanCell(None, "not_published", ""),
    )


def extract_panel(
    index: dict[tuple[str, str, str, str], StatcanCell],
    year: int,
    geo: str,
    naics: str,
) -> IndustryPanel:
    return IndustryPanel(
        total_expenses=_cell(index, year, geo, naics, INDUSTRY_STATS["total_expenses"]),
        salaries_wages=_cell(index, year, geo, naics, INDUSTRY_STATS["salaries_wages"]),
        materials_supplies=_cell(
            index, year, geo, naics, INDUSTRY_STATS["materials_supplies"]
        ),
        energy_fuel=_cell(index, year, geo, naics, INDUSTRY_STATS["energy_fuel"]),
    )


def select_reference_year(
    index: dict[tuple[str, str, str, str], StatcanCell],
) -> int:
    years = sorted({int(key[0]) for key in index})
    for year in reversed(years):
        canada_total = _cell(
            index,
            year,
            "Canada",
            STATCAN_NAICS_INDUSTRY,
            INDUSTRY_STATS["total_expenses"],
        )
        reported_provinces = sum(
            1
            for spec in PROVINCES
            if _cell(
                index,
                year,
                spec.name,
                STATCAN_NAICS_INDUSTRY,
                INDUSTRY_STATS["total_expenses"],
            ).value_cad
            is not None
        )
        if canada_total.value_cad is not None and reported_provinces >= 10:
            return year
    raise ValueError(
        "No reference year has a Canada total expense and at least 10 "
        "provincial total-expense estimates"
    )


def latest_available_year(
    index: dict[tuple[str, str, str, str], StatcanCell],
    geo: str,
    naics: str,
) -> int | None:
    years = [
        int(key[0])
        for key, cell in index.items()
        if key[1] == geo
        and key[2] == naics
        and key[3] == INDUSTRY_STATS["total_expenses"]
        and cell.value_cad is not None
    ]
    return max(years) if years else None


def _parse_optional_float(raw: str, field: str) -> float | None:
    text = raw.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"Non-numeric {field} value {raw!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"Non-finite {field} value {raw!r}")
    if value < 0.0:
        raise ValueError(f"Negative {field} value {value!r}")
    return value


def read_harvest_records(path: Path) -> dict[str, HarvestRecord]:
    required = {"province", "harvest_m3", "observed_2024_m3"}
    rows = _read_csv(path, required)
    by_province: dict[str, dict[str, str]] = {}
    for row in rows:
        code = row["province"].strip()
        if code in by_province:
            raise ValueError(f"Duplicate harvest province {code!r} in {path}")
        by_province[code] = row

    records: dict[str, HarvestRecord] = {}
    for spec in PROVINCES:
        row = by_province.get(spec.code)
        if row is None:
            records[spec.code] = HarvestRecord(
                spec.code, None, None, "missing_harvest_row", False
            )
            continue
        observed = _parse_optional_float(row["observed_2024_m3"], "observed_2024_m3")
        scenario = _parse_optional_float(row["harvest_m3"], "harvest_m3")
        if observed is not None:
            records[spec.code] = HarvestRecord(
                spec.code,
                observed,
                scenario,
                "provincial_allocation/119m.csv:observed_2024_m3",
                False,
            )
        elif scenario is not None:
            records[spec.code] = HarvestRecord(
                spec.code,
                scenario,
                scenario,
                "provincial_allocation/119m.csv:harvest_m3_fallback",
                True,
            )
        else:
            records[spec.code] = HarvestRecord(
                spec.code, None, scenario, "missing_harvest", False
            )
    return records


def safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None:
        return None
    if denominator <= 0.0 or numerator <= 0.0:
        return None
    ratio = numerator / denominator
    return ratio if math.isfinite(ratio) else None


def component_share(part: float | None, total: float | None) -> float | None:
    if part is None or total is None or total <= 0.0:
        return None
    return part / total


def expense_shares(panel: IndustryPanel) -> dict[str, float | None]:
    total = panel.total_expenses.value_cad
    shares = {
        "salaries_wages": component_share(panel.salaries_wages.value_cad, total),
        "materials_supplies": component_share(
            panel.materials_supplies.value_cad, total
        ),
        "energy_fuel": component_share(panel.energy_fuel.value_cad, total),
    }
    if any(value is None for value in shares.values()):
        shares["other"] = None
        return shares
    residual = 1.0 - sum(value for value in shares.values() if value is not None)
    if residual < -1e-6:
        raise ValueError(
            "Published expense components exceed total expenses by more than rounding"
        )
    shares["other"] = max(0.0, residual)
    return shares


def worst_status(panel: IndustryPanel) -> str:
    return max((cell.status for cell in panel.cells()), key=lambda s: STATUS_RANK[s])


def _status_flags(prefix: str, panel: IndustryPanel) -> list[str]:
    flags = []
    for cell in panel.cells():
        if cell.symbol == "0s":
            flags.append(f"{prefix}_rounded_zero_symbol")
        if cell.status == "D":
            flags.append(f"{prefix}_statcan_status_D_acceptable_quality")
        elif cell.status == "E":
            flags.append(f"{prefix}_statcan_status_E_use_with_caution")
        elif cell.status == "F":
            flags.append(f"{prefix}_statcan_status_F_too_unreliable")
        elif cell.status == "x":
            flags.append(f"{prefix}_statcan_status_x_suppressed")
        elif cell.status == "not_published":
            flags.append(f"{prefix}_cell_not_published")
    return flags


def quality_flags(
    panel: IndustryPanel,
    contract_panel: IndustryPanel,
    harvest: HarvestRecord,
    ratio: float | None,
    national_ratio: float | None,
    reference_year: int,
    source_latest_year: int,
) -> tuple[str, str, list[str]]:
    flags: list[str] = []
    if reference_year == source_latest_year:
        flags.append("preliminary_reference_year")
    if reference_year >= 2022:
        flags.append("energy_water_fuel_detail_not_published_since_2022")
    flags.extend(_status_flags("industry", panel))
    flags.extend(_status_flags("contract", contract_panel))
    if any(cell.value_cad is None for cell in panel.cells()):
        flags.append("industry_component_missing")
    if all(cell.value_cad is None for cell in contract_panel.cells()):
        flags.append("contract_logging_not_available")
    if harvest.harvest_m3 is None:
        flags.append("harvest_denominator_missing")
    elif harvest.harvest_m3 == 0.0:
        flags.append("harvest_denominator_zero")
    if harvest.is_fallback:
        flags.append("harvest_basis_fallback_used")
    if panel.total_expenses.value_cad == 0.0:
        flags.append("reported_zero_total_expenses")
    if (
        ratio is not None
        and national_ratio is not None
        and ratio < 0.10 * national_ratio
    ):
        flags.append("ratio_below_10pct_national_context")
    return worst_status(panel), worst_status(contract_panel), list(dict.fromkeys(flags))


def classify_proxy_status(
    total_expenses: float | None,
    harvest_m3: float | None,
    ratio: float | None,
) -> str:
    if harvest_m3 is None:
        return "missing_harvest_denominator"
    if harvest_m3 <= 0.0:
        return "undefined_zero_harvest_denominator"
    if total_expenses is None:
        return "missing_total_expenses"
    if total_expenses <= 0.0:
        return "reported_zero_expenses_not_used"
    if ratio is None:
        return "unavailable"
    return "available"


def classify_contract_status(
    contract_panel: IndustryPanel,
    harvest_m3: float | None,
    ratio: float | None,
) -> str:
    total = contract_panel.total_expenses.value_cad
    if harvest_m3 is None:
        return "missing_harvest_denominator"
    if harvest_m3 <= 0.0:
        return "undefined_zero_harvest_denominator"
    if total is None:
        statuses = {cell.status for cell in contract_panel.cells()}
        if "x" in statuses:
            return "suppressed"
        if "not_published" in statuses:
            return "not_published"
        return "missing_total_expenses"
    if total <= 0.0:
        return "reported_zero_expenses_not_used"
    if ratio is None:
        return "unavailable"
    return "available"


def _num(value: float | None, digits: int = 6) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise ValueError(f"Non-finite output value {value!r}")
    text = f"{value:.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="raise"
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _download_archive(destination: Path) -> None:
    request = urllib.request.Request(
        STATCAN_URL,
        headers={"User-Agent": "clews-c2070-fresh statcan_logging_cost_proxy"},
    )
    try:
        with (
            urllib.request.urlopen(request, timeout=180) as response,
            destination.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
    except OSError as exc:
        raise RuntimeError(f"Failed to download {STATCAN_URL}: {exc}") from exc
    if destination.stat().st_size < 1_000:
        raise RuntimeError(
            f"Downloaded StatCan archive is implausibly small: {destination}"
        )


def _extract_statcan_csv(archive: Path, destination_dir: Path) -> Path:
    with zipfile.ZipFile(archive) as bundle:
        members = [
            name
            for name in bundle.namelist()
            if name.endswith(".csv") and "MetaData" not in name
        ]
        if STATCAN_CSV_MEMBER in members:
            member = STATCAN_CSV_MEMBER
        elif len(members) == 1:
            member = members[0]
        else:
            raise RuntimeError(f"Unexpected StatCan archive members: {members}")
        destination = destination_dir / STATCAN_CSV_MEMBER
        with bundle.open(member) as source, destination.open("wb") as target:
            shutil.copyfileobj(source, target)
    return destination


def _resolve_source(
    work_dir: Path,
    source_zip: Path | None,
    source_csv: Path | None,
) -> SourceArtifacts:
    if source_zip is not None and source_csv is not None:
        raise ValueError("Pass either source_zip or source_csv, not both")
    if source_csv is not None:
        if not source_csv.is_file():
            raise FileNotFoundError(source_csv)
        return SourceArtifacts(
            csv_path=source_csv,
            archive_sha256="",
            csv_sha256=_sha256_file(source_csv),
            archive_bytes=0,
            csv_bytes=source_csv.stat().st_size,
            used_supplied_csv=True,
        )

    archive = source_zip
    if archive is None:
        archive = work_dir / "16100114-eng.zip"
        _download_archive(archive)
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if not zipfile.is_zipfile(archive):
        raise ValueError(f"Not a zip archive: {archive}")
    extracted = _extract_statcan_csv(archive, work_dir)
    return SourceArtifacts(
        csv_path=extracted,
        archive_sha256=_sha256_file(archive),
        csv_sha256=_sha256_file(extracted),
        archive_bytes=archive.stat().st_size,
        csv_bytes=extracted.stat().st_size,
        used_supplied_csv=False,
    )


def _row_caveat(proxy_status: str) -> str:
    base = (
        "NAICS 11331 industry aggregate; includes non-logging and possible "
        "haul/contract overlap; not a logging or haul cost curve."
    )
    if proxy_status == "available":
        return base
    return f"{base} Proxy status: {proxy_status}."


def proxy_columns() -> list[str]:
    return [
        "province",
        "province_name",
        "reference_year",
        "statcan_table",
        "statcan_naics_code",
        "statcan_naics_label",
        "naics_1133_equivalent",
        "harvest_basis",
        "harvest_year",
        "harvest_m3",
        "harvest_is_observed_2024",
        "scenario_119m_harvest_m3",
        "total_expenses_cad",
        "total_expenses_status",
        "salaries_wages_cad",
        "salaries_wages_status",
        "materials_supplies_cad",
        "materials_supplies_status",
        "energy_fuel_cad",
        "energy_fuel_status",
        "industry_expense_cad_m3",
        "scenario_119m_industry_expense_cad_m3",
        "salaries_wages_share",
        "materials_supplies_share",
        "energy_fuel_share",
        "other_expense_share",
        "contract_logging_reference_year",
        "contract_logging_latest_available_year",
        "contract_logging_total_expenses_cad",
        "contract_logging_total_expenses_status",
        "contract_logging_salaries_wages_cad",
        "contract_logging_salaries_wages_status",
        "contract_logging_materials_supplies_cad",
        "contract_logging_materials_supplies_status",
        "contract_logging_energy_fuel_cad",
        "contract_logging_energy_fuel_status",
        "contract_logging_industry_expense_cad_m3",
        "contract_logging_salaries_wages_share",
        "contract_logging_materials_supplies_share",
        "contract_logging_energy_fuel_share",
        "contract_logging_other_expense_share",
        "contract_logging_proxy_status",
        "industry_worst_quality_status",
        "contract_worst_quality_status",
        "data_quality_flags",
        "proxy_status",
        "fallback_used",
        "evidence",
        "observation_type",
        "not_logging_haul_breakdown",
        "observed",
        "policy_grade",
        "price_basis",
        "caveat",
    ]


def build_proxy_rows(
    index: dict[tuple[str, str, str, str], StatcanCell],
    harvest_by_province: dict[str, HarvestRecord],
    reference_year: int,
    source_latest_year: int,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    canada_panel = extract_panel(
        index, reference_year, "Canada", STATCAN_NAICS_INDUSTRY
    )
    harvest_total = sum(
        record.harvest_m3
        for record in harvest_by_province.values()
        if record.harvest_m3 is not None
    )
    national_ratio = safe_ratio(canada_panel.total_expenses.value_cad, harvest_total)
    rows: list[dict[str, str]] = []
    summary_rows: list[dict[str, Any]] = []

    for spec in PROVINCES:
        panel = extract_panel(index, reference_year, spec.name, STATCAN_NAICS_INDUSTRY)
        contract_panel = extract_panel(
            index, reference_year, spec.name, STATCAN_NAICS_CONTRACT
        )
        harvest = harvest_by_province[spec.code]
        ratio = safe_ratio(panel.total_expenses.value_cad, harvest.harvest_m3)
        scenario_ratio = safe_ratio(
            panel.total_expenses.value_cad, harvest.scenario_119m_harvest_m3
        )
        shares = expense_shares(panel)
        contract_shares = expense_shares(contract_panel)
        contract_ratio = safe_ratio(
            contract_panel.total_expenses.value_cad, harvest.harvest_m3
        )
        industry_worst, contract_worst, flags = quality_flags(
            panel,
            contract_panel,
            harvest,
            ratio,
            national_ratio,
            reference_year,
            source_latest_year,
        )
        proxy_status = classify_proxy_status(
            panel.total_expenses.value_cad, harvest.harvest_m3, ratio
        )
        contract_status = classify_contract_status(
            contract_panel, harvest.harvest_m3, contract_ratio
        )
        contract_latest = latest_available_year(
            index, spec.name, STATCAN_NAICS_CONTRACT
        )

        rows.append(
            {
                "province": spec.code,
                "province_name": spec.name,
                "reference_year": str(reference_year),
                "statcan_table": STATCAN_TABLE,
                "statcan_naics_code": NAICS_INDUSTRY_CODE,
                "statcan_naics_label": STATCAN_NAICS_INDUSTRY,
                "naics_1133_equivalent": "true",
                "harvest_basis": harvest.basis,
                "harvest_year": "2024",
                "harvest_m3": _num(harvest.harvest_m3, 2),
                "harvest_is_observed_2024": _bool(not harvest.is_fallback),
                "scenario_119m_harvest_m3": _num(harvest.scenario_119m_harvest_m3, 2),
                "total_expenses_cad": _num(panel.total_expenses.value_cad, 2),
                "total_expenses_status": panel.total_expenses.status,
                "salaries_wages_cad": _num(panel.salaries_wages.value_cad, 2),
                "salaries_wages_status": panel.salaries_wages.status,
                "materials_supplies_cad": _num(panel.materials_supplies.value_cad, 2),
                "materials_supplies_status": panel.materials_supplies.status,
                "energy_fuel_cad": _num(panel.energy_fuel.value_cad, 2),
                "energy_fuel_status": panel.energy_fuel.status,
                "industry_expense_cad_m3": _num(ratio, 6),
                "scenario_119m_industry_expense_cad_m3": _num(scenario_ratio, 6),
                "salaries_wages_share": _num(shares["salaries_wages"], 6),
                "materials_supplies_share": _num(shares["materials_supplies"], 6),
                "energy_fuel_share": _num(shares["energy_fuel"], 6),
                "other_expense_share": _num(shares["other"], 6),
                "contract_logging_reference_year": str(reference_year),
                "contract_logging_latest_available_year": (
                    "" if contract_latest is None else str(contract_latest)
                ),
                "contract_logging_total_expenses_cad": _num(
                    contract_panel.total_expenses.value_cad, 2
                ),
                "contract_logging_total_expenses_status": (
                    contract_panel.total_expenses.status
                ),
                "contract_logging_salaries_wages_cad": _num(
                    contract_panel.salaries_wages.value_cad, 2
                ),
                "contract_logging_salaries_wages_status": (
                    contract_panel.salaries_wages.status
                ),
                "contract_logging_materials_supplies_cad": _num(
                    contract_panel.materials_supplies.value_cad, 2
                ),
                "contract_logging_materials_supplies_status": (
                    contract_panel.materials_supplies.status
                ),
                "contract_logging_energy_fuel_cad": _num(
                    contract_panel.energy_fuel.value_cad, 2
                ),
                "contract_logging_energy_fuel_status": (
                    contract_panel.energy_fuel.status
                ),
                "contract_logging_industry_expense_cad_m3": _num(contract_ratio, 6),
                "contract_logging_salaries_wages_share": _num(
                    contract_shares["salaries_wages"], 6
                ),
                "contract_logging_materials_supplies_share": _num(
                    contract_shares["materials_supplies"], 6
                ),
                "contract_logging_energy_fuel_share": _num(
                    contract_shares["energy_fuel"], 6
                ),
                "contract_logging_other_expense_share": _num(
                    contract_shares["other"], 6
                ),
                "contract_logging_proxy_status": contract_status,
                "industry_worst_quality_status": industry_worst,
                "contract_worst_quality_status": contract_worst,
                "data_quality_flags": ";".join(flags),
                "proxy_status": proxy_status,
                "fallback_used": _bool(harvest.is_fallback),
                "evidence": EVIDENCE_LABEL,
                "observation_type": EVIDENCE_LABEL,
                "not_logging_haul_breakdown": "true",
                "observed": "false",
                "policy_grade": "false",
                "price_basis": f"nominal_{reference_year}_CAD",
                "caveat": _row_caveat(proxy_status),
            }
        )
        summary_rows.append(
            {
                "province": spec.code,
                "province_name": spec.name,
                "harvest_basis": harvest.basis,
                "harvest_m3": harvest.harvest_m3,
                "harvest_is_observed_2024": not harvest.is_fallback,
                "scenario_119m_harvest_m3": harvest.scenario_119m_harvest_m3,
                "total_expenses_cad": panel.total_expenses.value_cad,
                "total_expenses_status": panel.total_expenses.status,
                "industry_expense_cad_m3": ratio,
                "scenario_119m_industry_expense_cad_m3": scenario_ratio,
                "component_shares": shares,
                "contract_logging_total_expenses_cad": (
                    contract_panel.total_expenses.value_cad
                ),
                "contract_logging_total_expenses_status": (
                    contract_panel.total_expenses.status
                ),
                "contract_logging_industry_expense_cad_m3": contract_ratio,
                "contract_logging_shares": contract_shares,
                "contract_logging_latest_available_year": contract_latest,
                "proxy_status": proxy_status,
                "contract_logging_proxy_status": contract_status,
                "industry_worst_quality_status": industry_worst,
                "contract_worst_quality_status": contract_worst,
                "data_quality_flags": flags,
            }
        )

    sum_provincial_expenses = sum(
        row["total_expenses_cad"] or 0.0 for row in summary_rows
    )
    national = {
        "reference_year": reference_year,
        "total_expenses_cad": canada_panel.total_expenses.value_cad,
        "total_expenses_status": canada_panel.total_expenses.status,
        "salaries_wages_cad": canada_panel.salaries_wages.value_cad,
        "materials_supplies_cad": canada_panel.materials_supplies.value_cad,
        "energy_fuel_cad": canada_panel.energy_fuel.value_cad,
        "component_shares": expense_shares(canada_panel),
        "harvest_m3": harvest_total,
        "harvest_complete": all(
            record.harvest_m3 is not None for record in harvest_by_province.values()
        ),
        "industry_expense_cad_m3": national_ratio,
        "sum_provincial_total_expenses_cad": sum_provincial_expenses,
        "canada_minus_sum_provincial_expenses_cad": (
            None
            if canada_panel.total_expenses.value_cad is None
            else canada_panel.total_expenses.value_cad - sum_provincial_expenses
        ),
        "national_ratio_basis": (
            "Canada NAICS 11331 total expenses divided by the sum of the 13 "
            "provincial 2024 observed harvest denominators"
        ),
    }
    return rows, {"national": national, "provinces": summary_rows}


def _source_rows(
    source: SourceArtifacts,
    harvest_csv: Path,
    harvest_sha256: str,
    retrieved_utc: str,
) -> list[dict[str, str]]:
    statcan_notes = (
        "Table 16-10-0114-01, Logging industries, principal statistics by "
        "NAICS, Statistics Canada Open Licence. Latest reference year 2024 is "
        "preliminary. Data-quality symbols are preserved; D means acceptable "
        "quality, not a deletion. Archive last modified 2025-12-18."
    )
    if source.used_supplied_csv:
        statcan_notes += " Built from a supplied CSV subset, not the archive."
    return [
        {
            "source_key": "statcan_16100114",
            "title": (
                "Logging industries, principal statistics by North American "
                "Industry Classification System (NAICS)"
            ),
            "publisher": "Statistics Canada",
            "reference_period": "2012-2024",
            "publication_date": STATCAN_ARCHIVE_LAST_MODIFIED,
            "retrieved_utc": retrieved_utc,
            "url": STATCAN_URL,
            "sha256": source.archive_sha256 or source.csv_sha256,
            "sha256_secondary": source.csv_sha256,
            "licence": "Statistics Canada Open Licence",
            "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
            "evidence_tier": "official_statistic",
            "used_for": (
                "NAICS 11331 total expenses, salaries and wages, materials and "
                "supplies, and total energy/water utility/vehicle fuel; NAICS "
                "113312 contract logging as a separate panel."
            ),
            "redistributed": "true",
            "notes": statcan_notes,
            "evidence": EVIDENCE_LABEL,
            "observed": "false",
            "policy_grade": "false",
        },
        {
            "source_key": "provincial_allocation_119m",
            "title": "Provincial harvest allocation LP, 119 Mm3 case",
            "publisher": "This repository (derived)",
            "reference_period": "2024 observed harvest; 119 Mm3 scenario",
            "publication_date": "2026-11-20",
            "retrieved_utc": retrieved_utc,
            "url": "",
            "sha256": harvest_sha256,
            "sha256_secondary": "",
            "licence": (
                "Repository-derived allocation; upstream NFD/CFS and "
                "provincial harvest sources"
            ),
            "licence_url": "",
            "evidence_tier": "derived_allocation",
            "used_for": (
                "Primary denominator is observed_2024_m3 (2024 provincial "
                "harvest). The harvest_m3 column is the 119 Mm3 allocation and "
                "is used only as a sensitivity column."
            ),
            "redistributed": "true",
            "notes": (
                "The allocation file is a screening allocation, not a legal "
                "AAC or forecast. Nunavut observed_2024_m3 is zero with no FSD "
                "row; no commercial exhaustion is asserted."
            ),
            "evidence": EVIDENCE_LABEL,
            "observed": "false",
            "policy_grade": "false",
        },
    ]


def _readme(summary: dict[str, Any], source: SourceArtifacts, harvest_csv: Path) -> str:
    national = summary["national"]
    lines = [
        "# Provincial logging-industry cost proxy (StatCan 16-10-0114-01)",
        "",
        (
            "**Status:** industry aggregate proxy. Every row is "
            "`evidence=industry_aggregate_proxy`, `observation_type="
            "industry_aggregate_proxy`, `not_logging_haul_breakdown=true`, "
            "`observed=false`, and `policy_grade=false`. This is **not** an "
            "observed logging cost, a logging cost curve, a haul-cost "
            "estimate, or a policy-grade value."
        ),
        "",
        "## Source and method",
        "",
        (
            f"- Numerator: Statistics Canada Table {STATCAN_TABLE}, NAICS "
            "11331 (`Logging`), latest reference year 2024 (preliminary). "
            "Values are converted from thousands of dollars to nominal 2024 "
            "CAD."
        ),
        (
            f"- Denominator: `{harvest_csv.relative_to(REPO_ROOT)}` "
            "`observed_2024_m3` (2024 observed provincial harvest). The "
            "`harvest_m3` column from the same file is a 119 Mm3 scenario "
            "allocation and is reported only as a sensitivity."
        ),
        "- `industry_expense_cad_m3 = total_expenses_cad / harvest_m3`.",
        (
            "- Component shares use total expenses as the denominator. The "
            "remaining share is `other_expense_share` (administration, "
            "depreciation, overhead, and other unpriced costs)."
        ),
        (
            "- Contract logging (NAICS 113312) is reported separately against "
            "the same harvest denominator; it is not added to the NAICS 11331 "
            "total."
        ),
        "",
        "## Values by province",
        "",
        (
            "| Province | Harvest 2024 (Mm3) | Total expenses (M CAD) | "
            "Aggregate (CAD/m3) | Salaries % | Materials % | Energy % | "
            "Other % | Status |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["provinces"]:
        harvest_m3 = row["harvest_m3"]
        total = row["total_expenses_cad"]
        ratio = row["industry_expense_cad_m3"]
        shares = row["component_shares"]

        def cell(value: float | None, digits: int = 2) -> str:
            return "—" if value is None else f"{value:,.{digits}f}"

        def pct(value: float | None) -> str:
            return "—" if value is None else f"{value * 100:,.1f}%"

        lines.append(
            "| "
            + " | ".join(
                [
                    row["province"],
                    cell(None if harvest_m3 is None else harvest_m3 / 1e6, 3),
                    cell(None if total is None else total / 1e6, 1),
                    cell(ratio, 2),
                    pct(shares["salaries_wages"]),
                    pct(shares["materials_supplies"]),
                    pct(shares["energy_fuel"]),
                    pct(shares["other"]),
                    row["proxy_status"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            (
                "National context: Canada NAICS 11331 total expenses "
                f"{_num(national['total_expenses_cad'], 2)} CAD over "
                f"{_num(national['harvest_m3'], 2)} m3 = "
                f"{_num(national['industry_expense_cad_m3'], 6)} CAD/m3. "
                "Canada is context only; provincial rows are the deliverable."
            ),
            "",
            "## Contract logging (NAICS 113312), same harvest denominator",
            "",
            "| Province | Total expenses (M CAD) | Aggregate (CAD/m3) | Status |",
            "|---|---:|---:|---|",
        ]
    )
    for row in summary["provinces"]:
        total = row["contract_logging_total_expenses_cad"]
        ratio = row["contract_logging_industry_expense_cad_m3"]

        def contract_cell(value: float | None, digits: int = 2) -> str:
            return "—" if value is None else f"{value:,.{digits}f}"

        lines.append(
            "| "
            + " | ".join(
                [
                    row["province"],
                    contract_cell(None if total is None else total / 1e6, 1),
                    contract_cell(ratio, 2),
                    row["contract_logging_proxy_status"],
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            (
                "- Expected jurisdictions: 13; present: "
                f"{summary['coverage']['jurisdictions_present']}."
            ),
            (
                "- Available industry proxies: "
                f"{summary['coverage']['proxy_available']}."
            ),
            (
                "- Missing or undefined: "
                f"{', '.join(summary['coverage']['missing']) or 'none'}."
            ),
            (
                "- Blank cells mean missing/undefined, never zero-filled. "
                "Nunavut has a reported zero expense and zero harvest "
                "denominator, so its CAD/m3 is blank."
            ),
            (
                "- Yukon, Northwest Territories, and Nunavut contract logging "
                "2024 cells are suppressed (`x`) or unavailable; blank, not "
                "zero."
            ),
            "",
            "## Caveats",
            "",
        ]
    )
    for caveat in CAVEATS:
        lines.append(f"- **{caveat['id']}**: {caveat['text']}")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- `provincial_logging_cost_proxy.csv`: 13 jurisdiction rows.",
            "- `sources.csv`: source register with hashes and licence.",
            "- `summary.json`: machine-readable values, coverage, and caveats.",
            "- `manifest.json`: artifact provenance and output hashes.",
            "",
            "## Reproduce",
            "",
            "```bash",
            ".venv/bin/python -m workflows.national.harvest.statcan_logging_cost_proxy",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def build(
    out_dir: Path = DEFAULT_OUT_DIR,
    *,
    source_zip: Path | None = None,
    source_csv: Path | None = None,
    harvest_csv: Path = DEFAULT_HARVEST_CSV,
    retrieved_utc: str | None = None,
) -> dict[str, Any]:
    retrieved = retrieved_utc or _utc_now()
    harvest_csv = harvest_csv.resolve()
    if not harvest_csv.is_file():
        raise FileNotFoundError(harvest_csv)
    harvest_sha256 = _sha256_file(harvest_csv)

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="statcan_16100114_") as work:
        source = _resolve_source(
            Path(work),
            None if source_zip is None else source_zip.resolve(),
            None if source_csv is None else source_csv.resolve(),
        )
        rows = read_statcan_rows(source.csv_path)
        cell_index = build_cell_index(rows)
        reference_year = select_reference_year(cell_index)
        source_latest_year = max(int(key[0]) for key in cell_index)
        harvest_by_province = read_harvest_records(harvest_csv)
        proxy_rows, values = build_proxy_rows(
            cell_index,
            harvest_by_province,
            reference_year,
            source_latest_year,
        )

    available = [
        row["province"]
        for row in values["provinces"]
        if row["proxy_status"] == "available"
    ]
    missing = [
        row["province"]
        for row in values["provinces"]
        if row["proxy_status"] != "available"
    ]
    contract_available = [
        row["province"]
        for row in values["provinces"]
        if row["contract_logging_proxy_status"] == "available"
    ]
    contract_unavailable = {
        row["province"]: row["contract_logging_proxy_status"]
        for row in values["provinces"]
        if row["contract_logging_proxy_status"] != "available"
    }
    status_counts = Counter(
        cell.status
        for spec in PROVINCES
        for naics in (STATCAN_NAICS_INDUSTRY, STATCAN_NAICS_CONTRACT)
        for cell in extract_panel(cell_index, reference_year, spec.name, naics).cells()
    )
    coverage = {
        "jurisdictions_expected": len(PROVINCES),
        "jurisdictions_present": len(values["provinces"]),
        "proxy_available": len(available),
        "missing": missing,
        "missing_reasons": {
            row["province"]: row["proxy_status"]
            for row in values["provinces"]
            if row["proxy_status"] != "available"
        },
        "available": available,
        "contract_logging_available": contract_available,
        "contract_logging_unavailable": contract_unavailable,
        "harvest_complete": values["national"]["harvest_complete"],
        "harvest_bases": sorted({row["harvest_basis"] for row in values["provinces"]}),
        "reference_year": reference_year,
        "source_latest_year": source_latest_year,
        "latest_available_year_by_province": {
            spec.code: {
                NAICS_INDUSTRY_CODE: latest_available_year(
                    cell_index, spec.name, STATCAN_NAICS_INDUSTRY
                ),
                NAICS_CONTRACT_CODE: latest_available_year(
                    cell_index, spec.name, STATCAN_NAICS_CONTRACT
                ),
            }
            for spec in PROVINCES
        },
        "statcan_status_counts": dict(sorted(status_counts.items())),
        "worst_industry_status_by_province": {
            row["province"]: row["industry_worst_quality_status"]
            for row in values["provinces"]
        },
        "worst_contract_status_by_province": {
            row["province"]: row["contract_worst_quality_status"]
            for row in values["provinces"]
        },
    }
    summary: dict[str, Any] = {
        "artifact_id": ARTIFACT_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": retrieved,
        "status": "industry_aggregate_proxy_not_policy_grade",
        "description": (
            "Provincial NAICS 11331 logging-industry total expenses divided by "
            "2024 provincial harvest. Industry aggregate proxy with no "
            "logging/haul separation."
        ),
        "labels": {
            "evidence": EVIDENCE_LABEL,
            "observation_type": EVIDENCE_LABEL,
            "not_logging_haul_breakdown": True,
            "observed": False,
            "policy_grade": False,
            "price_basis": f"nominal_{reference_year}_CAD",
            "geography": "13_individual_provinces_and_territories",
            "naics_requested": NAICS_REQUESTED,
            "statcan_naics_published": NAICS_INDUSTRY_CODE,
            "statcan_contract_naics": NAICS_CONTRACT_CODE,
        },
        "reference_year": reference_year,
        "source": {
            "table": STATCAN_TABLE,
            "title": (
                "Logging industries, principal statistics by North American "
                "Industry Classification System (NAICS)"
            ),
            "publisher": "Statistics Canada",
            "url": STATCAN_URL,
            "licence": "Statistics Canada Open Licence",
            "archive_last_modified": STATCAN_ARCHIVE_LAST_MODIFIED,
            "archive_sha256": source.archive_sha256,
            "csv_sha256": source.csv_sha256,
            "harvest_csv": str(harvest_csv.relative_to(REPO_ROOT)),
            "harvest_csv_sha256": harvest_sha256,
        },
        "coverage": coverage,
        "national": values["national"],
        "provinces": values["provinces"],
        "caveats": list(CAVEATS),
        "not_for": [
            "observed logging contractor cost",
            "logging cost curve by terrain, piece size, or system",
            "truck-haul cost",
            "delivered log cost",
            "marginal cost",
            "policy-grade benchmark",
            "net value or profit",
        ],
    }

    proxy_path = out_dir / PROXY_CSV
    sources_path = out_dir / SOURCES_CSV
    summary_path = out_dir / SUMMARY_JSON
    readme_path = out_dir / README_MD
    manifest_path = out_dir / MANIFEST_JSON

    _write_csv(proxy_path, proxy_columns(), proxy_rows)
    _write_csv(
        sources_path,
        [
            "source_key",
            "title",
            "publisher",
            "reference_period",
            "publication_date",
            "retrieved_utc",
            "url",
            "sha256",
            "sha256_secondary",
            "licence",
            "licence_url",
            "evidence_tier",
            "used_for",
            "redistributed",
            "notes",
            "evidence",
            "observed",
            "policy_grade",
        ],
        _source_rows(source, harvest_csv, harvest_sha256, retrieved),
    )
    _write_json(summary_path, summary)
    readme_path.write_text(_readme(summary, source, harvest_csv), encoding="utf-8")

    output_hashes = {
        name: {
            "sha256": _sha256_file(out_dir / name),
            "bytes": (out_dir / name).stat().st_size,
        }
        for name in (PROXY_CSV, SOURCES_CSV, SUMMARY_JSON, README_MD)
    }
    manifest = {
        "artifact_id": ARTIFACT_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": retrieved,
        "status": "industry_aggregate_proxy_not_policy_grade",
        "labels": summary["labels"],
        "reference_year": reference_year,
        "inputs": {
            "statcan_16100114_archive": {
                "url": STATCAN_URL,
                "publication_date": STATCAN_ARCHIVE_LAST_MODIFIED,
                "sha256": source.archive_sha256,
                "bytes": source.archive_bytes,
            },
            "statcan_16100114_csv": {
                "sha256": source.csv_sha256,
                "bytes": source.csv_bytes,
            },
            "provincial_allocation_119m": {
                "path": str(harvest_csv.relative_to(REPO_ROOT)),
                "sha256": harvest_sha256,
                "bytes": harvest_csv.stat().st_size,
            },
        },
        "outputs": output_hashes,
        "coverage": {
            "jurisdictions_expected": coverage["jurisdictions_expected"],
            "jurisdictions_present": coverage["jurisdictions_present"],
            "proxy_available": coverage["proxy_available"],
            "missing": coverage["missing"],
            "contract_logging_unavailable": coverage["contract_logging_unavailable"],
        },
        "caveats": [caveat["id"] for caveat in CAVEATS],
        "reproduce_command": (
            ".venv/bin/python -m workflows.national.harvest.statcan_logging_cost_proxy"
        ),
        "not_editing": [
            "ui/public OSeCAN artifacts",
            "national economics v2.2 artifacts",
        ],
    }
    _write_json(manifest_path, manifest)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the StatCan 16-10-0114-01 provincial logging-industry cost proxy."
        )
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--source-zip", type=Path, default=None)
    parser.add_argument("--source-csv", type=Path, default=None)
    parser.add_argument("--harvest-csv", type=Path, default=DEFAULT_HARVEST_CSV)
    parser.add_argument("--retrieved-utc", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = build(
        args.out,
        source_zip=args.source_zip,
        source_csv=args.source_csv,
        harvest_csv=args.harvest_csv,
        retrieved_utc=args.retrieved_utc,
    )
    print(
        json.dumps(
            {
                "artifact": summary["artifact_id"],
                "reference_year": summary["reference_year"],
                "coverage": {
                    "jurisdictions": summary["coverage"]["jurisdictions_present"],
                    "available": summary["coverage"]["proxy_available"],
                    "missing": summary["coverage"]["missing"],
                },
                "national_industry_expense_cad_m3": summary["national"][
                    "industry_expense_cad_m3"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
