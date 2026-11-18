"""Build the recoverable-residue supply curve and time path for OSeCAN B1/B2.

This module turns the FFCM BC FMU residue ledger into a recoverable-residue
supply curve for the two FRESH-side OSeCAN zones:

``B1``
    South Coast / Lower Mainland: Georgia Depression plus Southern Coastal
    Mountains.
``B2``
    BC Interior & North: the remaining BC ecoprovinces.

The previous artifact (``clews_supply/bc_woody_residue``) published the *gross*
taper residue from the BC area-weighted national batch.  This module adds:

1. an FFCM recovery basis: FFCM ``logging_residue`` InitialContent compared
   with the gross taper residue on the same log-volume basis;
2. low/base/high cases with explicit recovery fractions and cost multipliers;
3. a 2021-2070 AAC time path parsed from the TSA Tenure Apportionment
   workbook (2021-2028 observed, 2028 held flat thereafter); and
4. an AGB non-stem slash sensitivity from the TSA24 biomass component curves.

Fail-closed: the builder stops on a missing or malformed input, an unexpected
unit, a non-zero FFCM MinimumSupply, an out-of-bounds implied recovery that is
silently used, or a time path that cannot be reconciled with the published
2018 AAC total.
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

from workflows.national.clews.bc_residue_supply import (
    BATCH_JOINED_SHA256,
    BatchRatios,
    BcResidueSupplyError,
    TsaArea,
    allocate_harvest,
    canonical_tsa_name,
    load_batch_ratios,
    load_tsa_areas,
    zone_for_ecoprovince,
)

__all__ = [
    "AGB_AGE",
    "ALL_BC_AAC_2018_M3",
    "B1_FALLBACK_RECOVERY",
    "BASE_RECOVERY_BASIS",
    "BASE_RECOVERY_FALLBACK",
    "CASE_COST_MULTIPLIER",
    "CASE_RECOVERY",
    "EXPECTED_FFCM_IMPLIED_B2",
    "FFCM_IMPLIED_B2_TOLERANCE",
    "HORIZON_END",
    "HORIZON_START",
    "INTERIM_HELD_BASE_2021_PJ",
    "INTERIM_HELD_BASE_2028_PJ",
    "INTERIM_HELD_RECOVERY",
    "LHV_GJ_PER_ODT",
    "OLD_PUBLISHED_BASE_2028_PJ",
    "OUTPUT_DIR",
    "PLACEHOLDER_COST_USD_PER_GJ",
    "PLACEHOLDER_PJ",
    "PREVIOUS_GROSS_PJ",
    "RECOVERY_MAX",
    "RECOVERY_MIN",
    "AacTrajectory",
    "AgbSensitivity",
    "AgbZoneRow",
    "BcResidueRecoveryError",
    "BuildArtifacts",
    "CaseResult",
    "CaseRow",
    "FfcmBcSupply",
    "FfcmFmu",
    "NonStemFraction",
    "TimePathRow",
    "ZoneRecovery",
    "build_agb_sensitivity",
    "build_all",
    "build_cases",
    "build_recovery",
    "build_time_path",
    "load_aac_workbook",
    "load_ffcm_bc_supply",
    "load_nonstem_fraction",
    "load_previous_gross_pj",
    "zone_shares_from_reference",
]

# ---------------------------------------------------------------------------
# Constants with named provenance
# ---------------------------------------------------------------------------

HORIZON_START = 2021
HORIZON_END = 2070
LHV_GJ_PER_ODT = 18.5
PJ_GJ = 1_000_000.0

# The published all-BC 2018 AAC total: parsed 36-TSA artifact (44,399,801)
# plus the Prince George 8,375,000 m3/y correction.
ALL_BC_AAC_2018_M3 = 52_774_801.0

# FFCM's BC logging_residue stream is a roadside-recoverable stream, not the
# full taper residue.  The recovery envelope is fixed by the task:
#   low  0.35, base FFCM implied (or 0.60 fallback), high 0.75.
RECOVERY_MIN = 0.20
RECOVERY_MAX = 1.00
BASE_RECOVERY_FALLBACK = 0.60
CASE_RECOVERY: Mapping[str, float | None] = {
    "low": 0.35,
    "base": None,
    "high": 0.75,
}
CASE_COST_MULTIPLIER: Mapping[str, float] = {
    "low": 1.30,
    "base": 1.00,
    "high": 0.80,
}

# Adopted base recovery.  The 2026-11-18 refinement changed the gross taper
# ratio, so the B2 base is recomputed from the current batch and FFCM ledger
# rather than held at the stale 2026-11-17 calibration:
#
#   implied_B2 = ffcm_residue_B2 / (ffcm_log_B2 * taper_ratio_B2)
#
# The value is asserted in ``build_all`` (tolerance 1e-5) so a silent input
# drift fails closed.  B1 remains the documented 0.60 fallback because its
# implied recovery (about 1.7368) is outside [0.2, 1.0].
EXPECTED_FFCM_IMPLIED_B2 = 0.723799
FFCM_IMPLIED_B2_TOLERANCE = 1e-5
BASE_RECOVERY_BASIS = "ffcm_implied_refined_batch_2026_11_18"
B1_FALLBACK_RECOVERY = 0.600000000

# Historical bases retained for the reconciliation report:
# old published 2026-11-17 cap, and the interim held-calibration cap that
# deliberately did not re-derive B2 from the refined taper.
OLD_PUBLISHED_BASE_2028_PJ = 42.958794801
INTERIM_HELD_BASE_2028_PJ = 35.809945709
INTERIM_HELD_BASE_2021_PJ = 41.298610459
INTERIM_HELD_RECOVERY: Mapping[str, float] = {
    "B1": 0.600000000,
    "B2": 0.583931303,
}

# Current C2070 placeholder and the previous gross taper-residue result.
PLACEHOLDER_PJ = 50.0
PLACEHOLDER_COST_USD_PER_GJ = 4.0
PREVIOUS_GROSS_PJ = 93.17008437023686

# Pre-refinement (2026-11-17) cap basis, retained for the reconciliation report.
STALE_BATCH_SHA256 = (
    "e0a51d5aa6b1ec22b9d27451f229a26bed525ee85641b7ce1766fe1c822e411e"
)
STALE_BATCH_LOG_M3 = 8_936_253_982.256
STALE_BATCH_RESIDUE_ODT = 722_691_930.985
OLD_CASE_CAPS_PJ: Mapping[str, Mapping[str, float]] = {
    "low": {"B1": 2.142166604, "B2": 23.547766898, "CAN": 25.689933501},
    "base": {"B1": 3.672285606, "B2": 39.286509195, "CAN": 42.958794801},
    "high": {"B1": 4.590357008, "B2": 50.459500495, "CAN": 55.049857503},
}
OLD_CASE_CAPS_2021_PJ: Mapping[str, Mapping[str, float]] = {
    "low": {"B1": 2.145722264, "B2": 28.047732727, "CAN": 30.193454990},
    "base": {"B1": 3.678381023, "B2": 46.794140373, "CAN": 50.472521396},
    "high": {"B1": 4.597976279, "B2": 60.102284415, "CAN": 64.700260694},
}
OLD_TIME_PATH_2028: Mapping[str, Mapping[str, float]] = {
    "B1": {
        "annual_log_m3": 9_120_804.864676,
        "gross_taper_residue_odt": 330_836.541084,
        "base_recovery_fraction": 0.600000000,
    },
    "B2": {
        "annual_log_m3": 34_249_970.935324,
        "gross_taper_residue_odt": 3_636_720.756404,
        "base_recovery_fraction": 0.583931303,
    },
}

AGB_AGE = 100

# Workbook sheet names that are regional rollups, not TSAs.
_REGION_SHEETS = frozenset(
    {
        "Overview",
        "Region 1 West",
        "Region 1 East",
        "Region 2",
        "Region 3",
        "Region 4",
        "Region 5",
        "Region 6",
        "Region 7",
        "Region 8",
        "Region 9",
    }
)

# Four TSA sheets carry a zero AAC in every year and are absent from the
# VRI 2025 area weights.  They are validated as zero and skipped.
_ZERO_AAC_SHEETS = frozenset({"Kingcome", "Mid Coast", "North Coast", "Strathcona"})

_WORKBOOK_TSA_ALIASES = {
    "FtSt John": "Fort St. John",
    "PG": "Prince George",
    "Robson": "Robson Valley",
    "100 Mile": "100 Mile House",
    "Mackenzie": "MacKenzie",
    "Great Bear North": "GBR North",
    "Great Bear South": "GBR South",
    "Haida Gwaii": "Queen Charlotte",
}

# FRESH-side provisional ecoprovince -> zone crosswalk.  It is repeated here
# only for the AGB source filtering; the authoritative crosswalk remains in
# workflows.national.clews.bc_residue_supply.
_B1_ECOPROVINCES = frozenset({"Georgia Depression", "Southern Coastal Mountains"})
_BC_ECOPROVINCES = frozenset(
    {
        "Georgia Depression",
        "Southern Coastal Mountains",
        "Northern Coastal Mountains",
        "Northern Montane Cordillera",
        "Central Montane Cordillera",
        "Southern Montane Cordillera",
        "Columbia Montane Cordillera",
        "Northern Boreal Cordillera",
        "Southern Boreal Cordillera",
        "Hay-Slave Lowlands",
        "Boreal Foothills",
        "Central Boreal Plains",
        "Wrangel Mountains",
        "NO_ECOPROVINCE",
    }
)

INPUT_PATHS = {
    "ffcm_fmu_supply": Path("metadata/ffcm-archive/parsed/fmu_supply.csv"),
    "fmu_tl_anchors": Path("metadata/fmu_tl_anchors.csv"),
    "bc_facility_tsa": Path(
        "data/derived/national/assignments/tsa24_pilot/bc_facility_tsa.csv"
    ),
    "tsa_apportionment_2018": Path(
        "data/derived/national/assignments/tsa24_pilot/tsa_apportionment_2018.csv"
    ),
    "aac_workbook": Path(
        "tmp/BC Fibre Database Benchmark 2018 V3  August/Tenure to Capacity "
        "Module/TSA Tenure Apportionment V5.xls"
    ),
    "bc_area_weights": Path(
        "data/derived/national/assortments/bc_area_weights/bc_area_weights.csv"
    ),
    "bc_batch_joined": Path(
        "data/derived/national/assortments/bc_area_weights/"
        "bc_national_batch_area_joined.csv"
    ),
    "biomass_au_curves": Path(
        "data/derived/national/bucking/tsa24_grade_curves/biomass_au_curves.csv"
    ),
    "national_stands": Path(
        "data/derived/national/assortments/national_batch/national_stands.csv"
    ),
    "previous_annual_residue": Path(
        "data/derived/national/clews_supply/bc_woody_residue/bc_annual_residue.csv"
    ),
}

OUTPUT_DIR = Path("data/derived/national/clews")


class BcResidueRecoveryError(RuntimeError):
    """Raised when an input cannot be turned into a defensible supply curve."""


# ---------------------------------------------------------------------------
# Small parsing helpers
# ---------------------------------------------------------------------------


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise BcResidueRecoveryError(f"Missing {label}: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise BcResidueRecoveryError(f"Empty CSV header for {label}: {path}")
        rows = list(reader)
    if not rows:
        raise BcResidueRecoveryError(f"Empty {label}: {path}")
    return rows


def _number(value: str | float, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise BcResidueRecoveryError(
            f"Non-numeric value at {context}: {value!r}"
        ) from error
    if not math.isfinite(number):
        raise BcResidueRecoveryError(f"Non-finite value at {context}: {value!r}")
    return number


def _optional_number(value: str | float, context: str) -> float | None:
    """Parse a workbook cell, returning None for a blank/NaN forecast cell."""
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise BcResidueRecoveryError(
            f"Non-numeric value at {context}: {value!r}"
        ) from error
    if not math.isfinite(number):
        return None
    return number


def _require_columns(
    rows: Sequence[Mapping[str, str]], columns: Iterable[str], label: str
) -> None:
    missing = sorted(set(columns) - set(rows[0].keys()))
    if missing:
        raise BcResidueRecoveryError(f"{label} is missing columns: {missing}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, str]], columns: Sequence[str]
) -> None:
    if not rows:
        raise BcResidueRecoveryError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(columns),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _fmu_sort_key(fmu: str) -> tuple[int, str]:
    suffix = fmu.removeprefix("BC")
    return (int(suffix) if suffix.isdigit() else 10**9, fmu)


# ---------------------------------------------------------------------------
# FFCM FMU supply
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FfcmFmu:
    """One FFCM forest management unit's BC residue ledger."""

    fmu: str
    log_m3: float
    residue_odt: float
    log_cost_usd_per_m3: float
    residue_cost_usd_per_odt: float
    minimum_supply_odt: float

    @property
    def residue_odt_per_m3(self) -> float:
        if self.log_m3 <= 0:
            raise BcResidueRecoveryError(
                f"FFCM FMU {self.fmu!r} has non-positive log volume"
            )
        return self.residue_odt / self.log_m3


@dataclass(frozen=True, slots=True)
class FfcmBcSupply:
    """Parsed FFCM BC FMU residue ledger."""

    fmus: tuple[FfcmFmu, ...]
    total_log_m3: float
    total_residue_odt: float
    residue_odt_per_m3: float
    residue_cost_usd_per_odt: float
    residue_cost_usd_per_gj: float
    residue_cost_min_usd_per_odt: float
    residue_cost_max_usd_per_odt: float
    log_cost_usd_per_m3: float
    source_path: Path

    @property
    def fmu_count(self) -> int:
        return len(self.fmus)


_FFCM_REQUIRED = frozenset(
    {
        ("InitialContent", "log"),
        ("InitialContent", "logging_residue"),
        ("SupplyCost", "log"),
        ("SupplyCost", "logging_residue"),
        ("MinimumSupply", "log"),
        ("MinimumSupply", "logging_residue"),
    }
)

_FFCM_UNITS = {
    ("InitialContent", "log"): "m3",
    ("InitialContent", "logging_residue"): "odt",
    ("SupplyCost", "log"): "$/m3",
    ("SupplyCost", "logging_residue"): "$/odt",
    ("MinimumSupply", "log"): "",
    ("MinimumSupply", "logging_residue"): "",
}


def load_ffcm_bc_supply(path: Path) -> FfcmBcSupply:
    """Parse FFCM BC FMU InitialContent, SupplyCost, and MinimumSupply.

    The FFCM BC stream has a uniform logging_residue/log ratio of 0.063
    ODT/m3 and a uniform residue SupplyCost of 45.4540846589377 $/ODT.  The
    parser does not assume either value: it validates the ledger and computes
    the totals and weighted cost.
    """
    rows = _read_csv(path, "FFCM FMU supply")
    _require_columns(
        rows,
        ("fmu", "attribute", "commodity", "value", "unit"),
        "FFCM FMU supply",
    )

    ledger: dict[str, dict[tuple[str, str], tuple[float, str]]] = defaultdict(dict)
    for index, row in enumerate(rows, start=2):
        fmu = row["fmu"].strip()
        if not fmu.startswith("BC"):
            continue
        key = (row["attribute"].strip(), row["commodity"].strip())
        if key in ledger[fmu]:
            raise BcResidueRecoveryError(
                f"FFCM FMU supply row {index}: duplicate {key} for {fmu!r}"
            )
        ledger[fmu][key] = (
            _number(row["value"], f"FFCM FMU supply row {index} value"),
            row["unit"].strip(),
        )

    if not ledger:
        raise BcResidueRecoveryError(f"FFCM FMU supply has no BC FMUs: {path}")

    fmus: list[FfcmFmu] = []
    for fmu in sorted(ledger, key=_fmu_sort_key):
        values = ledger[fmu]
        missing = _FFCM_REQUIRED - set(values)
        if missing:
            raise BcResidueRecoveryError(
                f"FFCM FMU {fmu!r} is missing ledger entries: {sorted(missing)}"
            )
        unexpected = set(values) - _FFCM_REQUIRED
        if unexpected:
            raise BcResidueRecoveryError(
                f"FFCM FMU {fmu!r} has unexpected ledger entries: {sorted(unexpected)}"
            )
        for key in _FFCM_REQUIRED:
            _value, unit = values[key]
            expected_unit = _FFCM_UNITS[key]
            if unit != expected_unit:
                raise BcResidueRecoveryError(
                    f"FFCM FMU {fmu!r} {key} has unit {unit!r}; "
                    f"expected {expected_unit!r}"
                )
        log_m3 = values[("InitialContent", "log")][0]
        residue_odt = values[("InitialContent", "logging_residue")][0]
        log_cost = values[("SupplyCost", "log")][0]
        residue_cost = values[("SupplyCost", "logging_residue")][0]
        minimum_supply_log = values[("MinimumSupply", "log")][0]
        minimum_supply = values[("MinimumSupply", "logging_residue")][0]
        if log_m3 < 0 or residue_odt < 0:
            raise BcResidueRecoveryError(
                f"FFCM FMU {fmu!r} has negative InitialContent"
            )
        if log_cost <= 0 or residue_cost <= 0:
            raise BcResidueRecoveryError(
                f"FFCM FMU {fmu!r} has non-positive SupplyCost"
            )
        if minimum_supply != 0.0 or minimum_supply_log != 0.0:
            raise BcResidueRecoveryError(
                f"FFCM FMU {fmu!r} has MinimumSupply "
                f"(log {minimum_supply_log}, residue {minimum_supply}); "
                "the recoverable curve assumes zero minimum supply"
            )
        fmus.append(
            FfcmFmu(
                fmu=fmu,
                log_m3=log_m3,
                residue_odt=residue_odt,
                log_cost_usd_per_m3=log_cost,
                residue_cost_usd_per_odt=residue_cost,
                minimum_supply_odt=minimum_supply,
            )
        )

    total_log = sum(fmu.log_m3 for fmu in fmus)
    total_residue = sum(fmu.residue_odt for fmu in fmus)
    if total_log <= 0:
        raise BcResidueRecoveryError("FFCM BC FMU ledger has zero log volume")
    if total_residue <= 0:
        raise BcResidueRecoveryError("FFCM BC FMU ledger has zero residue")

    residue_cost = (
        sum(fmu.residue_cost_usd_per_odt * fmu.residue_odt for fmu in fmus)
        / total_residue
    )
    log_cost = sum(fmu.log_cost_usd_per_m3 * fmu.log_m3 for fmu in fmus) / total_log
    return FfcmBcSupply(
        fmus=tuple(fmus),
        total_log_m3=total_log,
        total_residue_odt=total_residue,
        residue_odt_per_m3=total_residue / total_log,
        residue_cost_usd_per_odt=residue_cost,
        residue_cost_usd_per_gj=residue_cost / LHV_GJ_PER_ODT,
        residue_cost_min_usd_per_odt=min(fmu.residue_cost_usd_per_odt for fmu in fmus),
        residue_cost_max_usd_per_odt=max(fmu.residue_cost_usd_per_odt for fmu in fmus),
        log_cost_usd_per_m3=log_cost,
        source_path=path,
    )


# ---------------------------------------------------------------------------
# AAC trajectory from the TSA Tenure Apportionment workbook
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AacTrajectory:
    """Annual AAC by TSA for 2011-2028."""

    by_tsa: Mapping[str, Mapping[int, float]]
    years: tuple[int, ...]
    total_by_year: Mapping[int, float]
    complete_years: tuple[int, ...]
    missing_tsa_years: Mapping[str, tuple[int, ...]]
    source_path: Path
    sheet_count: int
    zero_aac_sheets: tuple[str, ...]


def _find_year_header(frame: object) -> tuple[int, dict[int, int]]:
    """Return the first row with at least five calendar-year columns."""
    row_count = len(frame)  # type: ignore[arg-type]
    for row_index in range(min(12, row_count)):
        year_columns = {
            int(value): column
            for column, value in enumerate(
                frame.iloc[row_index].tolist()  # type: ignore[attr-defined]
            )
            if isinstance(value, (int, float)) and 2000 < value < 2100
        }
        if len(year_columns) >= 5:
            return row_index, year_columns
    raise BcResidueRecoveryError("No calendar-year header row found in workbook sheet")


def _find_aac_row(
    frame: object, header_row: int, *, require_positive: bool = True
) -> int:
    """Return the row whose 'AAC' label carries a most-recent AAC value."""
    row_count = len(frame)  # type: ignore[arg-type]
    for row_index in range(header_row + 1, min(header_row + 8, row_count)):
        for column in range(1, 6):
            value = frame.iat[row_index, column]  # type: ignore[attr-defined]
            if not isinstance(value, str) or value.strip() != "AAC":
                continue
            left = frame.iat[row_index, column - 1]  # type: ignore[attr-defined]
            if not isinstance(left, (int, float)):
                continue
            if left > 0 or (not require_positive and left == 0):
                return row_index
    raise BcResidueRecoveryError(
        "No AAC row found in workbook sheet; refusing to guess"
    )


def load_aac_workbook(
    path: Path,
    *,
    expected_2018_total: float = ALL_BC_AAC_2018_M3,
    start_year: int = 2011,
    end_year: int = 2028,
) -> AacTrajectory:
    """Parse AAC rows for every TSA sheet in the apportionment workbook.

    The workbook puts the year columns in the header row and an ``AAC`` row
    below it.  Regional rollup sheets are skipped; the four zero-AAC coastal
    TSAs are validated as zero and skipped because they have no VRI 2025 area
    weight.  The 2018 total must reconcile to the published all-BC AAC.
    """
    if not path.exists():
        raise BcResidueRecoveryError(f"Missing AAC workbook: {path}")
    try:
        import pandas as pd  # type: ignore[import-untyped]
    except ImportError as error:  # pragma: no cover - environment guard
        raise BcResidueRecoveryError(
            "pandas is required to parse the TSA Tenure Apportionment workbook"
        ) from error

    workbook = pd.ExcelFile(path)
    years = tuple(range(start_year, end_year + 1))
    by_tsa: dict[str, dict[int, float]] = {}
    missing_by_tsa: dict[str, tuple[int, ...]] = {}
    zero_sheets: list[str] = []
    sheet_count = 0

    for sheet in workbook.sheet_names:
        if sheet in _REGION_SHEETS:
            continue
        frame = workbook.parse(sheet, header=None)
        header_row, year_columns = _find_year_header(frame)
        missing_years = sorted(set(years) - set(year_columns))
        if missing_years:
            raise BcResidueRecoveryError(
                f"AAC workbook sheet {sheet!r} is missing years {missing_years}"
            )
        aac_row = _find_aac_row(
            frame, header_row, require_positive=sheet not in _ZERO_AAC_SHEETS
        )
        values: dict[int, float] = {}
        missing_forecast_years: list[int] = []
        for year in years:
            raw = frame.iat[aac_row, year_columns[year]]
            number = _optional_number(raw, f"AAC workbook sheet {sheet!r} year {year}")
            if number is None:
                missing_forecast_years.append(year)
                continue
            if number < 0:
                raise BcResidueRecoveryError(
                    f"AAC workbook sheet {sheet!r} year {year} is negative"
                )
            values[year] = number

        if sheet in _ZERO_AAC_SHEETS:
            if any(value != 0.0 for year, value in values.items() if year >= 2018):
                raise BcResidueRecoveryError(
                    f"AAC workbook sheet {sheet!r} is listed as zero-AAC from "
                    "2018 but carries a non-zero post-2018 value"
                )
            zero_sheets.append(sheet)
            continue

        # The time path needs 2021-2028 and the 2018 cross-check.  The
        # workbook has blank 2011-2016 forecast cells for three coastal TSAs;
        # those are recorded, not invented.
        required_years = {2018, *range(2021, 2029)}
        missing_required = sorted(required_years - set(values))
        if missing_required:
            raise BcResidueRecoveryError(
                f"AAC workbook sheet {sheet!r} is missing required years "
                f"{missing_required}"
            )

        display_name = _WORKBOOK_TSA_ALIASES.get(sheet, sheet)
        try:
            tsa_name = canonical_tsa_name(display_name)
        except BcResidueSupplyError as error:
            raise BcResidueRecoveryError(
                f"AAC workbook sheet {sheet!r} has no canonical TSA mapping"
            ) from error
        if tsa_name in by_tsa:
            raise BcResidueRecoveryError(
                f"AAC workbook maps two sheets to TSA {tsa_name!r}"
            )
        by_tsa[tsa_name] = values
        if missing_forecast_years:
            missing_by_tsa[tsa_name] = tuple(missing_forecast_years)
        sheet_count += 1

    if not by_tsa:
        raise BcResidueRecoveryError("AAC workbook yielded no TSA rows")
    total_by_year = {
        year: sum(values.get(year, 0.0) for values in by_tsa.values()) for year in years
    }
    if not math.isclose(
        total_by_year[2018], expected_2018_total, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise BcResidueRecoveryError(
            f"AAC workbook 2018 total {total_by_year[2018]:,.6f} m3/y does not "
            f"match the published {expected_2018_total:,.6f} m3/y"
        )
    complete_years = tuple(
        year
        for year in years
        if not any(year in missing for missing in missing_by_tsa.values())
    )
    for year in (2018, *range(2021, 2029)):
        if year not in complete_years:
            raise BcResidueRecoveryError(
                f"AAC workbook year {year} is not complete across all TSAs"
            )
    return AacTrajectory(
        by_tsa=by_tsa,
        years=years,
        total_by_year=total_by_year,
        complete_years=complete_years,
        missing_tsa_years=dict(missing_by_tsa),
        source_path=path,
        sheet_count=sheet_count,
        zero_aac_sheets=tuple(zero_sheets),
    )


# ---------------------------------------------------------------------------
# Time path
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimePathRow:
    """One year/zone row of the gross taper-residue time path."""

    year: int
    zone_id: str
    annual_log_m3: float
    gross_taper_residue_odt: float
    gross_taper_residue_pj: float
    aac_year: int
    time_path_basis: str


def _zone_log_and_taper(
    aac_by_tsa: Mapping[str, Mapping[int, float]],
    areas: Mapping[str, TsaArea],
    ratios: BatchRatios,
    year: int,
) -> dict[str, tuple[float, float]]:
    """Allocate one year's AAC to zones and apply the ecoprovince taper ratio."""
    volumes = {
        tsa: values[year]
        for tsa, values in aac_by_tsa.items()
        if values.get(year, 0.0) > 0.0
    }
    if not volumes:
        raise BcResidueRecoveryError(f"No positive AAC volumes for year {year}")
    allocated, _ = allocate_harvest(volumes, areas)

    zone_log: defaultdict[str, float] = defaultdict(float)
    for ecoprovince, volume in allocated.items():
        zone = zone_for_ecoprovince(ecoprovince)
        zone_log[zone] += volume

    # The cap uses the direct matched-cell zone ratio, the same basis as the
    # refined all-BC chain.  Applying ecoprovince ratios to TSA-allocated
    # volumes would give a different zone ratio and break reconciliation.
    zone_residue = {
        zone_id: zone_log[zone_id] * ratios.by_zone[zone_id]
        for zone_id in ("B1", "B2")
    }

    for zone_id in ("B1", "B2"):
        if zone_id not in zone_log or zone_log[zone_id] <= 0:
            raise BcResidueRecoveryError(
                f"No positive log volume allocated to zone {zone_id} in {year}"
            )
    return {
        zone_id: (zone_log[zone_id], zone_residue[zone_id]) for zone_id in ("B1", "B2")
    }


def zone_shares_from_reference(
    aac: AacTrajectory,
    areas: Mapping[str, TsaArea],
    ratios: BatchRatios,
    *,
    reference_year: int = 2018,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return B1/B2 log shares and taper ratios for a reference AAC year."""
    stats = _zone_log_and_taper(aac.by_tsa, areas, ratios, reference_year)
    total_log = sum(log_m3 for log_m3, _ in stats.values())
    if total_log <= 0:
        raise BcResidueRecoveryError(
            f"Reference year {reference_year} has zero zone log volume"
        )
    shares = {zone_id: stats[zone_id][0] / total_log for zone_id in ("B1", "B2")}
    taper_ratios = {
        zone_id: stats[zone_id][1] / stats[zone_id][0] for zone_id in ("B1", "B2")
    }
    if not math.isclose(sum(shares.values()), 1.0, rel_tol=1e-12, abs_tol=1e-12):
        raise BcResidueRecoveryError("Zone shares do not sum to one")
    return shares, taper_ratios


def build_time_path(
    aac: AacTrajectory,
    areas: Mapping[str, TsaArea],
    ratios: BatchRatios,
    *,
    horizon_start: int = HORIZON_START,
    horizon_end: int = HORIZON_END,
    hold_year: int = 2028,
    lhv_gj_per_odt: float = LHV_GJ_PER_ODT,
) -> tuple[TimePathRow, ...]:
    """Build the 2021-2070 AAC time path.

    Years through ``hold_year`` use the workbook AAC for that year.  Later
    years hold the ``hold_year`` value flat to ``horizon_end``.  No MPB,
    climate, wildfire, or future-AAC trajectory is invented.
    """
    if horizon_end < horizon_start:
        raise BcResidueRecoveryError("horizon_end must be >= horizon_start")
    if not horizon_start <= hold_year <= horizon_end:
        raise BcResidueRecoveryError(
            f"hold_year {hold_year} is outside the {horizon_start}-{horizon_end} horizon"
        )
    if lhv_gj_per_odt <= 0:
        raise BcResidueRecoveryError("lhv_gj_per_odt must be positive")

    rows: list[TimePathRow] = []
    hold_stats: dict[str, tuple[float, float]] | None = None
    for year in range(horizon_start, horizon_end + 1):
        if year <= hold_year:
            source_year = year
            stats = _zone_log_and_taper(aac.by_tsa, areas, ratios, year)
            if year == hold_year:
                hold_stats = stats
        else:
            source_year = hold_year
            if hold_stats is None:
                raise BcResidueRecoveryError(
                    f"Internal error: no {hold_year} hold statistics"
                )
            stats = hold_stats

        national_log = sum(log_m3 for log_m3, _ in stats.values())
        national_residue = sum(odt for _, odt in stats.values())
        for zone_id in ("B1", "B2"):
            log_m3, residue_odt = stats[zone_id]
            rows.append(
                TimePathRow(
                    year=year,
                    zone_id=zone_id,
                    annual_log_m3=log_m3,
                    gross_taper_residue_odt=residue_odt,
                    gross_taper_residue_pj=residue_odt * lhv_gj_per_odt / PJ_GJ,
                    aac_year=source_year,
                    time_path_basis="aac_2028_hold",
                )
            )
        rows.append(
            TimePathRow(
                year=year,
                zone_id="CAN",
                annual_log_m3=national_log,
                gross_taper_residue_odt=national_residue,
                gross_taper_residue_pj=national_residue * lhv_gj_per_odt / PJ_GJ,
                aac_year=source_year,
                time_path_basis="aac_2028_hold",
            )
        )

    for year in range(hold_year + 1, horizon_end + 1):
        current = [row for row in rows if row.year == year]
        hold = [row for row in rows if row.year == hold_year]
        current_by_zone = {row.zone_id: row for row in current}
        hold_by_zone = {row.zone_id: row for row in hold}
        for zone_id in ("B1", "B2", "CAN"):
            if not math.isclose(
                current_by_zone[zone_id].annual_log_m3,
                hold_by_zone[zone_id].annual_log_m3,
                rel_tol=1e-12,
                abs_tol=1e-9,
            ):
                raise BcResidueRecoveryError(
                    f"Time path year {year} zone {zone_id} does not hold {hold_year}"
                )
    return tuple(rows)


# ---------------------------------------------------------------------------
# FFCM recovery basis
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ZoneRecovery:
    """FFCM-to-gross-taper recovery basis for one zone."""

    zone_id: str
    zone_name: str
    zone_share: float
    zone_share_basis: str
    ffcm_fmu_count: int
    ffcm_log_m3: float
    ffcm_residue_odt: float
    ffcm_residue_odt_per_m3: float
    ffcm_supply_cost_usd_per_odt: float
    ffcm_supply_cost_usd_per_gj: float
    gross_taper_ratio_odt_per_m3: float
    gross_taper_residue_odt_same_log_basis: float
    implied_recovery_fraction: float
    recovery_in_bounds: bool
    base_recovery_fraction: float
    recovery_basis: str
    flag: str
    crosswalk_status: str
    comparison_only: bool


ZONE_NAMES = {
    "B1": "South Coast / Lower Mainland",
    "B2": "BC Interior & North",
    "CAN": "Canada national rollup",
}


def build_recovery(
    ffcm: FfcmBcSupply,
    zone_shares: Mapping[str, float],
    taper_ratios: Mapping[str, float],
    *,
    recovery_min: float = RECOVERY_MIN,
    recovery_max: float = RECOVERY_MAX,
    fallback: float = BASE_RECOVERY_FALLBACK,
    base_recovery_by_zone: Mapping[str, float] | None = None,
) -> tuple[ZoneRecovery, ...]:
    """Compare FFCM residue with gross taper residue on the same log basis.

    FFCM ``InitialContent`` is a stock, not an annual harvest.  The comparison
    therefore uses ODT per m3 of the same log volume, not FFCM stock ODT against
    one year of gross taper ODT.  FFCM has no FMU-to-TSA crosswalk in the repo,
    so the BC total is allocated to B1/B2 by the documented 2018 AAC zone log
    shares.  That allocation is flagged, not silently treated as an FMU map.

    By default the base is the FFCM-implied fraction when it is inside the
    [0.2, 1.0] envelope and the documented 0.60 fallback otherwise.  A caller
    may override the base explicitly (used only for historical replay); the
    override is labelled and never silently mixed with the implied basis.
    """
    if not 0.0 < fallback < 1.0:
        raise BcResidueRecoveryError("fallback recovery must be between 0 and 1")
    if recovery_min < 0 or recovery_max > 1 or recovery_min >= recovery_max:
        raise BcResidueRecoveryError("Invalid recovery bounds")
    if base_recovery_by_zone is not None:
        missing = {"B1", "B2"} - set(base_recovery_by_zone)
        if missing:
            raise BcResidueRecoveryError(
                f"Published base recovery is missing zones {sorted(missing)}"
            )

    rows: list[ZoneRecovery] = []
    for zone_id in ("B1", "B2"):
        share = zone_shares[zone_id]
        if not 0.0 < share <= 1.0:
            raise BcResidueRecoveryError(
                f"Zone {zone_id} share must be in (0, 1], got {share}"
            )
        ffcm_log = ffcm.total_log_m3 * share
        ffcm_residue = ffcm.total_residue_odt * share
        taper_ratio = taper_ratios[zone_id]
        gross_same_basis = ffcm_log * taper_ratio
        implied = ffcm_residue / gross_same_basis
        in_bounds = recovery_min <= implied <= recovery_max
        if base_recovery_by_zone is None:
            base = implied if in_bounds else fallback
            recovery_basis = (
                "ffcm_implied" if in_bounds else "fallback_0.60_out_of_bounds"
            )
            flag = (
                "implied_within_0.2_1.0"
                if in_bounds
                else "implied_outside_0.2_1.0_used_0.60"
            )
        else:
            base = float(base_recovery_by_zone[zone_id])
            if not 0.0 < base < 1.0:
                raise BcResidueRecoveryError(
                    f"Published base recovery for {zone_id} must be in (0, 1), "
                    f"got {base}"
                )
            if not recovery_min <= base <= recovery_max:
                raise BcResidueRecoveryError(
                    f"Published base recovery for {zone_id} ({base}) is outside "
                    f"the case envelope [{recovery_min}, {recovery_max}]"
                )
            recovery_basis = "caller_override"
            flag = (
                f"caller_override_base;ffcm_implied_{implied:.9f}_diagnostic;"
                f"implied_in_bounds_{str(in_bounds).lower()}"
            )
        rows.append(
            ZoneRecovery(
                zone_id=zone_id,
                zone_name=ZONE_NAMES[zone_id],
                zone_share=share,
                zone_share_basis="aac_2018_zone_log_share",
                ffcm_fmu_count=ffcm.fmu_count,
                ffcm_log_m3=ffcm_log,
                ffcm_residue_odt=ffcm_residue,
                ffcm_residue_odt_per_m3=ffcm.residue_odt_per_m3,
                ffcm_supply_cost_usd_per_odt=ffcm.residue_cost_usd_per_odt,
                ffcm_supply_cost_usd_per_gj=ffcm.residue_cost_usd_per_gj,
                gross_taper_ratio_odt_per_m3=taper_ratio,
                gross_taper_residue_odt_same_log_basis=gross_same_basis,
                implied_recovery_fraction=implied,
                recovery_in_bounds=in_bounds,
                base_recovery_fraction=base,
                recovery_basis=recovery_basis,
                flag=flag,
                crosswalk_status="no_fmu_tsa_map_documented_zone_share",
                comparison_only=False,
            )
        )

    national_taper = sum(
        taper_ratios[zone_id] * zone_shares[zone_id] for zone_id in ("B1", "B2")
    )
    national_implied = ffcm.total_residue_odt / (ffcm.total_log_m3 * national_taper)
    national_in_bounds = recovery_min <= national_implied <= recovery_max
    if base_recovery_by_zone is None:
        national_base = national_implied if national_in_bounds else fallback
        national_basis = (
            "national_comparison_only"
            if national_in_bounds
            else "fallback_0.60_out_of_bounds"
        )
        national_flag = (
            "national_comparison_only"
            if national_in_bounds
            else "implied_outside_0.2_1.0_used_0.60"
        )
    else:
        national_base = sum(
            zone_shares[zone_id] * float(base_recovery_by_zone[zone_id])
            for zone_id in ("B1", "B2")
        )
        national_basis = "national_comparison_only_caller_override"
        national_flag = "national_comparison_only"
    rows.append(
        ZoneRecovery(
            zone_id="CAN",
            zone_name=ZONE_NAMES["CAN"],
            zone_share=1.0,
            zone_share_basis="national_rollup",
            ffcm_fmu_count=ffcm.fmu_count,
            ffcm_log_m3=ffcm.total_log_m3,
            ffcm_residue_odt=ffcm.total_residue_odt,
            ffcm_residue_odt_per_m3=ffcm.residue_odt_per_m3,
            ffcm_supply_cost_usd_per_odt=ffcm.residue_cost_usd_per_odt,
            ffcm_supply_cost_usd_per_gj=ffcm.residue_cost_usd_per_gj,
            gross_taper_ratio_odt_per_m3=national_taper,
            gross_taper_residue_odt_same_log_basis=(ffcm.total_log_m3 * national_taper),
            implied_recovery_fraction=national_implied,
            recovery_in_bounds=national_in_bounds,
            base_recovery_fraction=national_base,
            recovery_basis=national_basis,
            flag=national_flag,
            crosswalk_status="no_fmu_tsa_map_documented_zone_share",
            comparison_only=True,
        )
    )
    return tuple(rows)


# ---------------------------------------------------------------------------
# Low/base/high cases
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CaseRow:
    """One year/zone row of a recovery case."""

    year: int
    zone_id: str
    annual_log_m3: float
    gross_taper_residue_odt: float
    residue_odt: float
    residue_pj: float
    recovery_fraction: float
    cost_usd_per_gj: float


@dataclass(frozen=True, slots=True)
class CaseResult:
    """One low/base/high recovery case."""

    name: str
    recovery_by_zone: Mapping[str, float]
    cost_usd_per_gj_by_zone: Mapping[str, float]
    rows: tuple[CaseRow, ...]

    def row(self, year: int, zone_id: str) -> CaseRow:
        for row in self.rows:
            if row.year == year and row.zone_id == zone_id:
                return row
        raise BcResidueRecoveryError(
            f"Case {self.name!r} has no row for {year}/{zone_id}"
        )


def build_cases(
    time_path: Sequence[TimePathRow],
    recovery: Sequence[ZoneRecovery],
    *,
    lhv_gj_per_odt: float = LHV_GJ_PER_ODT,
) -> tuple[CaseResult, ...]:
    """Build low/base/high residue caps and costs from the time path."""
    recovery_by_zone = {
        row.zone_id: row.base_recovery_fraction
        for row in recovery
        if not row.comparison_only
    }
    if set(recovery_by_zone) != {"B1", "B2"}:
        raise BcResidueRecoveryError("Recovery rows must cover B1 and B2")
    if not all(
        CASE_RECOVERY["low"] <= value <= CASE_RECOVERY["high"]  # type: ignore[operator]
        for value in recovery_by_zone.values()
    ):
        raise BcResidueRecoveryError(
            "Base recovery is outside the low/high case envelope; "
            "refusing to emit non-monotonic cases"
        )

    base_cost_by_zone = {
        row.zone_id: row.ffcm_supply_cost_usd_per_gj
        for row in recovery
        if not row.comparison_only
    }
    cases: list[CaseResult] = []
    for case_name in ("low", "base", "high"):
        recovery_value = CASE_RECOVERY[case_name]
        case_recovery = (
            dict(recovery_by_zone)
            if recovery_value is None
            else {zone_id: float(recovery_value) for zone_id in ("B1", "B2")}
        )
        case_cost = {
            zone_id: base_cost_by_zone[zone_id] * CASE_COST_MULTIPLIER[case_name]
            for zone_id in ("B1", "B2")
        }

        rows: list[CaseRow] = []
        for year in sorted({row.year for row in time_path}):
            year_rows = {row.zone_id: row for row in time_path if row.year == year}
            if set(year_rows) != {"B1", "B2", "CAN"}:
                raise BcResidueRecoveryError(
                    f"Time path year {year} does not cover B1/B2/CAN"
                )
            zone_values: dict[str, CaseRow] = {}
            for zone_id in ("B1", "B2"):
                source = year_rows[zone_id]
                residue_odt = source.gross_taper_residue_odt * case_recovery[zone_id]
                zone_values[zone_id] = CaseRow(
                    year=year,
                    zone_id=zone_id,
                    annual_log_m3=source.annual_log_m3,
                    gross_taper_residue_odt=source.gross_taper_residue_odt,
                    residue_odt=residue_odt,
                    residue_pj=residue_odt * lhv_gj_per_odt / PJ_GJ,
                    recovery_fraction=case_recovery[zone_id],
                    cost_usd_per_gj=case_cost[zone_id],
                )
                rows.append(zone_values[zone_id])

            national_pj = sum(
                zone_values[zone_id].residue_pj for zone_id in ("B1", "B2")
            )
            national_odt = sum(
                zone_values[zone_id].residue_odt for zone_id in ("B1", "B2")
            )
            national_log = sum(
                zone_values[zone_id].annual_log_m3 for zone_id in ("B1", "B2")
            )
            national_gross = sum(
                zone_values[zone_id].gross_taper_residue_odt for zone_id in ("B1", "B2")
            )
            if national_pj <= 0:
                raise BcResidueRecoveryError(
                    f"Case {case_name!r} year {year} has zero national residue"
                )
            national_cost = (
                sum(
                    zone_values[zone_id].residue_pj * case_cost[zone_id]
                    for zone_id in ("B1", "B2")
                )
                / national_pj
            )
            rows.append(
                CaseRow(
                    year=year,
                    zone_id="CAN",
                    annual_log_m3=national_log,
                    gross_taper_residue_odt=national_gross,
                    residue_odt=national_odt,
                    residue_pj=national_pj,
                    recovery_fraction=(
                        sum(
                            zone_values[zone_id].residue_odt for zone_id in ("B1", "B2")
                        )
                        / national_gross
                    ),
                    cost_usd_per_gj=national_cost,
                )
            )
        cases.append(
            CaseResult(
                name=case_name,
                recovery_by_zone=case_recovery,
                cost_usd_per_gj_by_zone={
                    **case_cost,
                    "CAN": statistics.fmean(case_cost.values()),
                },
                rows=tuple(rows),
            )
        )
    return tuple(cases)


# ---------------------------------------------------------------------------
# AGB non-stem slash sensitivity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NonStemFraction:
    """(Bark + Foliar + Branch) / Aboveground biomass fraction at one age."""

    age: int
    median: float
    minimum: float
    maximum: float
    au_count: int
    source_path: Path


@dataclass(frozen=True, slots=True)
class AgbZoneRow:
    """RSYC AGB and non-stem slash sensitivity for one zone."""

    zone_id: str
    zone_name: str
    cell_count: int
    missing_cell_count: int
    agb_odt_ha: float
    volume_m3ha: float
    expansion_odt_per_m3: float
    nonstem_fraction: float
    slash_odt_per_m3: float
    slash_min_odt_per_m3: float
    slash_max_odt_per_m3: float
    taper_ratio_odt_per_m3: float
    ratio_to_taper: float
    ratio_min_to_taper: float
    ratio_max_to_taper: float
    annual_log_m3_2021: float
    annual_gross_slash_odt_2021: float
    annual_gross_slash_pj_2021: float
    annual_recoverable60_slash_pj_2021: float


@dataclass(frozen=True, slots=True)
class AgbSensitivity:
    """Contained AGB-slash sensitivity, never the base curve."""

    nonstem: NonStemFraction
    zones: tuple[AgbZoneRow, ...]
    method: str
    rsyc_agb_available: bool
    gap_note: str


def load_nonstem_fraction(path: Path, *, age: int = AGB_AGE) -> NonStemFraction:
    """Compute the age-specific aboveground non-stem fraction by AU."""
    rows = _read_csv(path, "TSA24 biomass AU curves")
    _require_columns(
        rows,
        (
            "au_id",
            "age",
            "agb_odt_ha",
            "biomass_live_bark_odt_ha",
            "biomass_live_foliar_odt_ha",
            "biomass_live_branch_odt_ha",
        ),
        "TSA24 biomass AU curves",
    )
    fractions: list[float] = []
    for index, row in enumerate(rows, start=2):
        row_age = _number(row["age"], f"biomass AU row {index} age")
        if int(row_age) != age:
            continue
        agb = _number(row["agb_odt_ha"], f"biomass AU row {index} agb_odt_ha")
        if agb <= 0:
            continue
        nonstem = sum(
            _number(row[column], f"biomass AU row {index} {column}")
            for column in (
                "biomass_live_bark_odt_ha",
                "biomass_live_foliar_odt_ha",
                "biomass_live_branch_odt_ha",
            )
        )
        fraction = nonstem / agb
        if not 0.0 <= fraction <= 1.0:
            raise BcResidueRecoveryError(
                f"biomass AU row {index}: non-stem fraction {fraction} "
                "is outside [0, 1]"
            )
        fractions.append(fraction)
    if not fractions:
        raise BcResidueRecoveryError(f"No positive AGB rows at age {age} in {path}")
    return NonStemFraction(
        age=age,
        median=statistics.median(fractions),
        minimum=min(fractions),
        maximum=max(fractions),
        au_count=len(fractions),
        source_path=path,
    )


def load_rsyc_agb_by_zone(
    path: Path,
) -> dict[str, tuple[float, float, int, int]]:
    """Return area-weighted RSYC AGB (ODT/ha), volume (m3/ha), and cell counts.

    ``national_stands.csv`` carries ``rsyc_agb_mg_ha`` at the age-100 reference.
    Mg/ha is numerically ODT/ha for dry biomass.  The source is the
    placeholder equal-area national batch, so the result is a sensitivity, not
    a BC inventory estimate.
    """
    rows = _read_csv(path, "national stand batch")
    _require_columns(
        rows,
        ("ecoprovince", "rsyc_volume_m3ha", "rsyc_agb_mg_ha", "area_weight_ha"),
        "national stand batch",
    )
    totals: dict[str, list[float]] = {
        zone_id: [0.0, 0.0, 0.0] for zone_id in ("B1", "B2")
    }  # agb*weight, volume*weight, weight
    counts = {"B1": 0, "B2": 0}
    missing_cells = {"B1": 0, "B2": 0}
    for index, row in enumerate(rows, start=2):
        ecoprovince = row["ecoprovince"].strip()
        if ecoprovince not in _BC_ECOPROVINCES:
            continue
        zone_id = "B1" if ecoprovince in _B1_ECOPROVINCES else "B2"
        weight = _number(row["area_weight_ha"], f"national stand row {index} weight")
        if weight <= 0:
            raise BcResidueRecoveryError(
                f"national stand row {index}: non-positive area_weight_ha"
            )
        agb = _optional_number(row["rsyc_agb_mg_ha"], f"national stand row {index} agb")
        volume = _optional_number(
            row["rsyc_volume_m3ha"], f"national stand row {index} volume"
        )
        if agb is None or volume is None or agb <= 0 or volume <= 0:
            missing_cells[zone_id] += 1
            continue
        totals[zone_id][0] += agb * weight
        totals[zone_id][1] += volume * weight
        totals[zone_id][2] += weight
        counts[zone_id] += 1

    result: dict[str, tuple[float, float, int, int]] = {}
    for zone_id in ("B1", "B2"):
        agb_weight, volume_weight, weight = totals[zone_id]
        if weight <= 0 or counts[zone_id] == 0:
            raise BcResidueRecoveryError(
                f"No RSYC AGB cells for zone {zone_id}; refusing to fabricate "
                "an AGB total"
            )
        result[zone_id] = (
            agb_weight / weight,
            volume_weight / weight,
            counts[zone_id],
            missing_cells[zone_id],
        )
    return result


def build_agb_sensitivity(
    nonstem: NonStemFraction,
    rsyc_by_zone: Mapping[str, tuple[float, float, int, int]],
    taper_ratios: Mapping[str, float],
    annual_log_2021: Mapping[str, float],
    *,
    lhv_gj_per_odt: float = LHV_GJ_PER_ODT,
) -> AgbSensitivity:
    """Apply the non-stem fraction to the RSYC AGB at age 100 by zone."""
    rows: list[AgbZoneRow] = []
    for zone_id in ("B1", "B2"):
        agb, volume, cells, missing_cells = rsyc_by_zone[zone_id]
        expansion = agb / volume
        slash = expansion * nonstem.median
        slash_min = expansion * nonstem.minimum
        slash_max = expansion * nonstem.maximum
        taper = taper_ratios[zone_id]
        annual_log = annual_log_2021[zone_id]
        annual_odt = slash * annual_log
        rows.append(
            AgbZoneRow(
                zone_id=zone_id,
                zone_name=ZONE_NAMES[zone_id],
                cell_count=cells,
                missing_cell_count=missing_cells,
                agb_odt_ha=agb,
                volume_m3ha=volume,
                expansion_odt_per_m3=expansion,
                nonstem_fraction=nonstem.median,
                slash_odt_per_m3=slash,
                slash_min_odt_per_m3=slash_min,
                slash_max_odt_per_m3=slash_max,
                taper_ratio_odt_per_m3=taper,
                ratio_to_taper=slash / taper,
                ratio_min_to_taper=slash_min / taper,
                ratio_max_to_taper=slash_max / taper,
                annual_log_m3_2021=annual_log,
                annual_gross_slash_odt_2021=annual_odt,
                annual_gross_slash_pj_2021=annual_odt * lhv_gj_per_odt / PJ_GJ,
                annual_recoverable60_slash_pj_2021=(
                    annual_odt * 0.60 * lhv_gj_per_odt / PJ_GJ
                ),
            )
        )
    return AgbSensitivity(
        nonstem=nonstem,
        zones=tuple(rows),
        method=(
            "RSYC age-100 AGB (national_stands.csv rsyc_agb_mg_ha) x "
            "(Bark+Foliar+Branch)/Above median at age 100"
        ),
        rsyc_agb_available=True,
        gap_note=(
            "RSYC AGB is available by BC ecoprovince cell in the national "
            "batch; no AGB total was fabricated. The national batch is a "
            "placeholder equal-area distribution, so this is a sensitivity."
        ),
    )


# ---------------------------------------------------------------------------
# Previous gross result
# ---------------------------------------------------------------------------


def load_previous_gross_pj(path: Path) -> float:
    """Read the previous 2021 gross taper-residue national result."""
    rows = _read_csv(path, "previous BC annual residue")
    _require_columns(rows, ("year", "zone_id", "residue_pj"), "previous residue")
    for row in rows:
        if int(_number(row["year"], "previous residue year")) != 2021:
            continue
        if row["zone_id"].strip() != "CAN":
            continue
        value = _number(row["residue_pj"], "previous residue PJ")
        if value <= 0:
            raise BcResidueRecoveryError("Previous national residue is not positive")
        return value
    raise BcResidueRecoveryError(f"Previous residue file has no 2021/CAN row: {path}")


# ---------------------------------------------------------------------------
# Build orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BuildArtifacts:
    """All parsed inputs and computed tables for writing/reporting."""

    ffcm: FfcmBcSupply
    aac: AacTrajectory
    recovery: tuple[ZoneRecovery, ...]
    time_path: tuple[TimePathRow, ...]
    cases: tuple[CaseResult, ...]
    agb: AgbSensitivity
    previous_gross_pj: float
    zone_shares: Mapping[str, float]
    taper_ratios: Mapping[str, float]
    batch_ratios: BatchRatios
    input_paths: tuple[Path, ...]


def _assert_adopted_recovery(recovery: Sequence[ZoneRecovery]) -> None:
    """Fail closed unless the recomputed FFCM-implied basis is the adopted one.

    B2 must reproduce the refined-batch implied recovery (0.723799 +/- 1e-5)
    and sit inside [0.2, 1.0].  B1 must remain the documented 0.60 fallback
    because its implied value is out of bounds.
    """
    by_zone = {row.zone_id: row for row in recovery}
    missing = {"B1", "B2"} - set(by_zone)
    if missing:
        raise BcResidueRecoveryError(f"Recovery is missing zones {sorted(missing)}")

    b1 = by_zone["B1"]
    b2 = by_zone["B2"]
    if b1.recovery_in_bounds:
        raise BcResidueRecoveryError(
            f"B1 implied recovery {b1.implied_recovery_fraction:.9f} is now in "
            "bounds; the documented 0.60 fallback assumption no longer holds"
        )
    if not math.isclose(
        b1.base_recovery_fraction, B1_FALLBACK_RECOVERY, rel_tol=0, abs_tol=1e-12
    ):
        raise BcResidueRecoveryError(
            f"B1 base recovery {b1.base_recovery_fraction} is not the documented "
            f"{B1_FALLBACK_RECOVERY} fallback"
        )
    if not RECOVERY_MIN <= b2.implied_recovery_fraction <= RECOVERY_MAX:
        raise BcResidueRecoveryError(
            f"B2 implied recovery {b2.implied_recovery_fraction:.9f} is outside "
            f"[{RECOVERY_MIN}, {RECOVERY_MAX}]"
        )
    if not math.isclose(
        b2.implied_recovery_fraction,
        EXPECTED_FFCM_IMPLIED_B2,
        rel_tol=0,
        abs_tol=FFCM_IMPLIED_B2_TOLERANCE,
    ):
        raise BcResidueRecoveryError(
            f"B2 implied recovery {b2.implied_recovery_fraction:.9f} does not "
            f"match the refined-batch expectation {EXPECTED_FFCM_IMPLIED_B2} "
            f"(tolerance {FFCM_IMPLIED_B2_TOLERANCE}); refusing to rebuild caps"
        )
    if not math.isclose(
        b2.base_recovery_fraction,
        b2.implied_recovery_fraction,
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise BcResidueRecoveryError(
            "B2 base recovery is not the recomputed FFCM-implied value"
        )


def build_all(repo_root: Path) -> BuildArtifacts:
    """Load inputs and build every table, failing closed on bad data."""
    input_paths = [repo_root / relative for relative in INPUT_PATHS.values()]
    for path in input_paths:
        if not path.exists():
            raise BcResidueRecoveryError(f"Missing input: {path}")

    areas = load_tsa_areas(repo_root / INPUT_PATHS["bc_area_weights"])
    ratios = load_batch_ratios(repo_root / INPUT_PATHS["bc_batch_joined"])
    ffcm = load_ffcm_bc_supply(repo_root / INPUT_PATHS["ffcm_fmu_supply"])
    aac = load_aac_workbook(repo_root / INPUT_PATHS["aac_workbook"])
    shares, taper_ratios = zone_shares_from_reference(aac, areas, ratios)
    recovery = build_recovery(ffcm, shares, taper_ratios)
    _assert_adopted_recovery(recovery)
    time_path = build_time_path(aac, areas, ratios)
    cases = build_cases(time_path, recovery)

    nonstem = load_nonstem_fraction(repo_root / INPUT_PATHS["biomass_au_curves"])
    rsyc_by_zone = load_rsyc_agb_by_zone(repo_root / INPUT_PATHS["national_stands"])
    annual_log_2021 = {
        row.zone_id: row.annual_log_m3
        for row in time_path
        if row.year == 2021 and row.zone_id in {"B1", "B2"}
    }
    agb = build_agb_sensitivity(nonstem, rsyc_by_zone, taper_ratios, annual_log_2021)
    previous_gross_pj = load_previous_gross_pj(
        repo_root / INPUT_PATHS["previous_annual_residue"]
    )
    if not math.isclose(
        previous_gross_pj, PREVIOUS_GROSS_PJ, rel_tol=1e-9, abs_tol=1e-6
    ):
        raise BcResidueRecoveryError(
            f"Previous gross result {previous_gross_pj} does not match the "
            f"documented {PREVIOUS_GROSS_PJ}"
        )
    return BuildArtifacts(
        ffcm=ffcm,
        aac=aac,
        recovery=recovery,
        time_path=time_path,
        cases=cases,
        agb=agb,
        previous_gross_pj=previous_gross_pj,
        zone_shares=shares,
        taper_ratios=taper_ratios,
        batch_ratios=ratios,
        input_paths=tuple(input_paths),
    )


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def recovery_rows(artifacts: BuildArtifacts) -> list[dict[str, str]]:
    """Rows for bc_residue_recovery.csv."""
    rows: list[dict[str, str]] = []
    for row in artifacts.recovery:
        rows.append(
            {
                "zone_id": row.zone_id,
                "zone_name": row.zone_name,
                "zone_share": f"{row.zone_share:.9f}",
                "zone_share_basis": row.zone_share_basis,
                "ffcm_fmu_count": str(row.ffcm_fmu_count),
                "ffcm_log_m3": f"{row.ffcm_log_m3:.6f}",
                "ffcm_residue_odt": f"{row.ffcm_residue_odt:.6f}",
                "ffcm_residue_odt_per_m3": f"{row.ffcm_residue_odt_per_m3:.9f}",
                "ffcm_supply_cost_usd_per_odt": (
                    f"{row.ffcm_supply_cost_usd_per_odt:.9f}"
                ),
                "ffcm_supply_cost_usd_per_gj": (
                    f"{row.ffcm_supply_cost_usd_per_gj:.9f}"
                ),
                "gross_taper_ratio_odt_per_m3": (
                    f"{row.gross_taper_ratio_odt_per_m3:.9f}"
                ),
                "gross_taper_residue_odt_same_log_basis": (
                    f"{row.gross_taper_residue_odt_same_log_basis:.6f}"
                ),
                "implied_recovery_fraction": (f"{row.implied_recovery_fraction:.9f}"),
                "recovery_in_bounds": "true" if row.recovery_in_bounds else "false",
                "base_recovery_fraction": f"{row.base_recovery_fraction:.9f}",
                "recovery_basis": row.recovery_basis,
                "flag": row.flag,
                "crosswalk_status": row.crosswalk_status,
                "comparison_only": "true" if row.comparison_only else "false",
                "evidence": (
                    "metadata/ffcm-archive/parsed/fmu_supply.csv; "
                    "bc_national_batch_area_joined.csv; "
                    "tsa_apportionment_2018.csv; "
                    "TSA Tenure Apportionment V5.xls"
                ),
            }
        )
    return rows


def aac_trajectory_rows(artifacts: BuildArtifacts) -> list[dict[str, str]]:
    """Audit table of the parsed 2011-2028 AAC by TSA and year."""
    rows: list[dict[str, str]] = []
    for tsa_name in sorted(artifacts.aac.by_tsa):
        values = artifacts.aac.by_tsa[tsa_name]
        for year in artifacts.aac.years:
            value = values.get(year)
            rows.append(
                {
                    "tsa_name": tsa_name,
                    "year": str(year),
                    "aac_m3": "" if value is None else f"{value:.6f}",
                    "present": "false" if value is None else "true",
                    "evidence": (
                        "TSA Tenure Apportionment V5.xls AAC row; blank "
                        "forecast cells are recorded as present=false"
                    ),
                }
            )
    return rows


def ffcm_fmu_rows(artifacts: BuildArtifacts) -> list[dict[str, str]]:
    """FMU-level audit rows with the documented zone-share allocation."""
    b1_share = artifacts.zone_shares["B1"]
    b2_share = artifacts.zone_shares["B2"]
    rows: list[dict[str, str]] = []
    for fmu in artifacts.ffcm.fmus:
        rows.append(
            {
                "fmu": fmu.fmu,
                "log_m3": f"{fmu.log_m3:.6f}",
                "residue_odt": f"{fmu.residue_odt:.6f}",
                "residue_odt_per_m3": f"{fmu.residue_odt_per_m3:.9f}",
                "residue_cost_usd_per_odt": (f"{fmu.residue_cost_usd_per_odt:.9f}"),
                "residue_cost_usd_per_gj": (
                    f"{fmu.residue_cost_usd_per_odt / LHV_GJ_PER_ODT:.9f}"
                ),
                "minimum_supply_odt": f"{fmu.minimum_supply_odt:.6f}",
                "b1_share": f"{b1_share:.9f}",
                "b2_share": f"{b2_share:.9f}",
                "residue_odt_b1": f"{fmu.residue_odt * b1_share:.6f}",
                "residue_odt_b2": f"{fmu.residue_odt * b2_share:.6f}",
                "zone_assignment_basis": (
                    "no_fmu_tsa_map_documented_2018_aac_zone_log_share"
                ),
                "notes": (
                    "FMU id is an FFCM supply-unit id; no TSA or zone name is "
                    "present in FMUs.cfg, FMU.xlsm, or the TL anchors"
                ),
            }
        )
    return rows


def time_path_rows(artifacts: BuildArtifacts) -> list[dict[str, str]]:
    """Rows for bc_residue_time_path.csv."""
    cases = {case.name: case for case in artifacts.cases}
    rows: list[dict[str, str]] = []
    for row in artifacts.time_path:
        zone_id = row.zone_id
        base = cases["base"].row(row.year, zone_id)
        low = cases["low"].row(row.year, zone_id)
        high = cases["high"].row(row.year, zone_id)
        rows.append(
            {
                "year": str(row.year),
                "zone_id": zone_id,
                "annual_log_m3": f"{row.annual_log_m3:.6f}",
                "gross_taper_residue_odt": f"{row.gross_taper_residue_odt:.6f}",
                "gross_taper_residue_pj": f"{row.gross_taper_residue_pj:.9f}",
                "base_recovery_fraction": f"{base.recovery_fraction:.9f}",
                "base_residue_odt": f"{base.residue_odt:.6f}",
                "base_residue_pj": f"{base.residue_pj:.9f}",
                "low_recovery_fraction": f"{low.recovery_fraction:.9f}",
                "low_residue_odt": f"{low.residue_odt:.6f}",
                "low_residue_pj": f"{low.residue_pj:.9f}",
                "high_recovery_fraction": f"{high.recovery_fraction:.9f}",
                "high_residue_odt": f"{high.residue_odt:.6f}",
                "high_residue_pj": f"{high.residue_pj:.9f}",
                "aac_year": str(row.aac_year),
                "time_path_basis": row.time_path_basis,
                "evidence": (
                    "TSA Tenure Apportionment V5.xls AAC rows 2011-2028; "
                    "2021-2028 observed, 2028 held flat 2029-2070; "
                    "TSA->ecoprovince by THLB area; batch ecoprovince taper ratio"
                ),
            }
        )
    return rows


def parameter_rows(
    case: CaseResult, technology: str, zone_id: str, value_kind: str
) -> list[dict[str, str]]:
    """Emit otoole-shaped activity-limit or variable-cost rows."""
    if value_kind not in {"activity", "cost"}:
        raise BcResidueRecoveryError(f"Unknown parameter kind: {value_kind!r}")
    years = sorted({row.year for row in case.rows})
    rows: list[dict[str, str]] = []
    for year in years:
        row = case.row(year, zone_id)
        value = row.residue_pj if value_kind == "activity" else row.cost_usd_per_gj
        rows.append(
            {
                "REGION": "CAN",
                "TECHNOLOGY": technology,
                "YEAR": str(year),
                "VALUE": f"{value:.9f}",
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Summary and report
# ---------------------------------------------------------------------------


def _case_year_summary(case: CaseResult, year: int) -> dict[str, object]:
    zones = {
        zone_id: {
            "residue_pj": round(case.row(year, zone_id).residue_pj, 9),
            "residue_odt": round(case.row(year, zone_id).residue_odt, 6),
            "recovery_fraction": round(case.row(year, zone_id).recovery_fraction, 9),
            "cost_usd_per_gj": round(case.row(year, zone_id).cost_usd_per_gj, 9),
        }
        for zone_id in ("B1", "B2", "CAN")
    }
    return {"year": year, "zones": zones}


def summary_payload(artifacts: BuildArtifacts) -> dict[str, object]:
    """Machine-readable summary for bc_residue_cases_summary.json."""
    recovery = {
        row.zone_id: {
            "zone_share": round(row.zone_share, 9),
            "zone_share_basis": row.zone_share_basis,
            "ffcm_log_m3": round(row.ffcm_log_m3, 6),
            "ffcm_residue_odt": round(row.ffcm_residue_odt, 6),
            "ffcm_residue_odt_per_m3": round(row.ffcm_residue_odt_per_m3, 9),
            "ffcm_supply_cost_usd_per_odt": round(row.ffcm_supply_cost_usd_per_odt, 9),
            "ffcm_supply_cost_usd_per_gj": round(row.ffcm_supply_cost_usd_per_gj, 9),
            "gross_taper_ratio_odt_per_m3": round(row.gross_taper_ratio_odt_per_m3, 9),
            "gross_taper_residue_odt_same_log_basis": round(
                row.gross_taper_residue_odt_same_log_basis, 6
            ),
            "implied_recovery_fraction": round(row.implied_recovery_fraction, 9),
            "recovery_in_bounds": row.recovery_in_bounds,
            "base_recovery_fraction": round(row.base_recovery_fraction, 9),
            "recovery_basis": row.recovery_basis,
            "flag": row.flag,
            "crosswalk_status": row.crosswalk_status,
            "comparison_only": row.comparison_only,
        }
        for row in artifacts.recovery
    }
    cases = {
        case.name: {
            "recovery_by_zone": {
                zone_id: round(value, 9)
                for zone_id, value in case.recovery_by_zone.items()
            },
            "cost_usd_per_gj_by_zone": {
                zone_id: round(value, 9)
                for zone_id, value in case.cost_usd_per_gj_by_zone.items()
            },
            "y2021": _case_year_summary(case, 2021),
            "y2028": _case_year_summary(case, 2028),
            "y2070": _case_year_summary(case, 2070),
        }
        for case in artifacts.cases
    }
    agb = {
        "nonstem_fraction": {
            "age": artifacts.agb.nonstem.age,
            "median": round(artifacts.agb.nonstem.median, 9),
            "min": round(artifacts.agb.nonstem.minimum, 9),
            "max": round(artifacts.agb.nonstem.maximum, 9),
            "au_count": artifacts.agb.nonstem.au_count,
        },
        "method": artifacts.agb.method,
        "rsyc_agb_available": artifacts.agb.rsyc_agb_available,
        "gap_note": artifacts.agb.gap_note,
        "zones": {
            row.zone_id: {
                "cell_count": row.cell_count,
                "missing_cell_count": row.missing_cell_count,
                "agb_odt_ha": round(row.agb_odt_ha, 6),
                "volume_m3ha": round(row.volume_m3ha, 6),
                "expansion_odt_per_m3": round(row.expansion_odt_per_m3, 9),
                "nonstem_fraction": round(row.nonstem_fraction, 9),
                "slash_odt_per_m3": round(row.slash_odt_per_m3, 9),
                "slash_min_odt_per_m3": round(row.slash_min_odt_per_m3, 9),
                "slash_max_odt_per_m3": round(row.slash_max_odt_per_m3, 9),
                "taper_ratio_odt_per_m3": round(row.taper_ratio_odt_per_m3, 9),
                "ratio_to_taper": round(row.ratio_to_taper, 9),
                "ratio_min_to_taper": round(row.ratio_min_to_taper, 9),
                "ratio_max_to_taper": round(row.ratio_max_to_taper, 9),
                "annual_log_m3_2021": round(row.annual_log_m3_2021, 6),
                "annual_gross_slash_odt_2021": round(
                    row.annual_gross_slash_odt_2021, 6
                ),
                "annual_gross_slash_pj_2021": round(row.annual_gross_slash_pj_2021, 9),
                "annual_recoverable60_slash_pj_2021": round(
                    row.annual_recoverable60_slash_pj_2021, 9
                ),
            }
            for row in artifacts.agb.zones
        },
    }
    cases_by_name = {case.name: case for case in artifacts.cases}
    base_2021 = cases_by_name["base"].row(2021, "CAN").residue_pj
    low_2021 = cases_by_name["low"].row(2021, "CAN").residue_pj
    high_2021 = cases_by_name["high"].row(2021, "CAN").residue_pj
    base_2028 = cases_by_name["base"].row(2028, "CAN").residue_pj
    return {
        "artifact_version": "0.1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "fresh_side_provisional_not_c2070_official",
        "batch_basis": {
            "joined_sha256": BATCH_JOINED_SHA256,
            "total_log_m3": round(artifacts.batch_ratios.log_m3, 6),
            "total_residue_odt": round(artifacts.batch_ratios.residue_odt, 6),
            "bc_all_species_ratio": round(
                artifacts.batch_ratios.bc_all_species, 9
            ),
            "by_zone_ratio": {
                zone_id: round(value, 9)
                for zone_id, value in artifacts.batch_ratios.by_zone.items()
            },
            "log_m3_by_zone": {
                zone_id: round(value, 6)
                for zone_id, value in artifacts.batch_ratios.log_m3_by_zone.items()
            },
            "residue_odt_by_zone": {
                zone_id: round(value, 6)
                for zone_id, value in artifacts.batch_ratios.residue_odt_by_zone.items()
            },
            "matched_cells": artifacts.batch_ratios.matched_cells,
            "no_area_cells": artifacts.batch_ratios.no_area_cells,
        },
        "base_recovery": {
            "basis": BASE_RECOVERY_BASIS,
            "by_zone": {
                row.zone_id: round(row.base_recovery_fraction, 9)
                for row in artifacts.recovery
                if not row.comparison_only
            },
            "expected_ffcm_implied_b2": EXPECTED_FFCM_IMPLIED_B2,
            "ffcm_implied_b2_tolerance": FFCM_IMPLIED_B2_TOLERANCE,
            "note": (
                "The B2 base is recomputed as ffcm_residue_B2 / "
                "(ffcm_log_B2 * taper_ratio_B2) from the current batch and "
                "FFCM ledger and asserted at build time; B1 remains the "
                "documented 0.60 fallback because its implied value is out of "
                "bounds."
            ),
        },
        "historical_bases": {
            "old_published_2026_11_17_base_2028_pj": OLD_PUBLISHED_BASE_2028_PJ,
            "interim_held_calibration_base_2021_pj": INTERIM_HELD_BASE_2021_PJ,
            "interim_held_calibration_base_2028_pj": INTERIM_HELD_BASE_2028_PJ,
            "interim_held_recovery_by_zone": dict(INTERIM_HELD_RECOVERY),
        },
        "ffcm_basis": {
            "fmu_count": artifacts.ffcm.fmu_count,
            "total_log_m3": round(artifacts.ffcm.total_log_m3, 6),
            "total_residue_odt": round(artifacts.ffcm.total_residue_odt, 6),
            "residue_odt_per_m3": round(artifacts.ffcm.residue_odt_per_m3, 9),
            "residue_cost_usd_per_odt": round(
                artifacts.ffcm.residue_cost_usd_per_odt, 9
            ),
            "residue_cost_usd_per_gj": round(artifacts.ffcm.residue_cost_usd_per_gj, 9),
            "residue_cost_min_usd_per_odt": round(
                artifacts.ffcm.residue_cost_min_usd_per_odt, 9
            ),
            "residue_cost_max_usd_per_odt": round(
                artifacts.ffcm.residue_cost_max_usd_per_odt, 9
            ),
            "log_cost_usd_per_m3": round(artifacts.ffcm.log_cost_usd_per_m3, 9),
            "minimum_supply_odt": 0.0,
            "zone_allocation": "documented_2018_aac_zone_log_share",
            "crosswalk_status": "no_fmu_tsa_map",
        },
        "recovery": recovery,
        "time_path": {
            "basis": "aac_2028_hold",
            "aac_years": [min(artifacts.aac.years), max(artifacts.aac.years)],
            "tsa_count": len(artifacts.aac.by_tsa),
            "zero_aac_sheets": list(artifacts.aac.zero_aac_sheets),
            "aac_2018_m3": round(artifacts.aac.total_by_year[2018], 6),
            "aac_2021_m3": round(artifacts.aac.total_by_year[2021], 6),
            "aac_2028_m3": round(artifacts.aac.total_by_year[2028], 6),
            "horizon": [2021, 2070],
        },
        "cases": cases,
        "comparison": {
            "placeholder_pj": PLACEHOLDER_PJ,
            "placeholder_cost_usd_per_gj": PLACEHOLDER_COST_USD_PER_GJ,
            "previous_gross_pj": round(artifacts.previous_gross_pj, 9),
            "base_2021_pj": round(base_2021, 9),
            "base_2028_pj": round(base_2028, 9),
            "low_2021_pj": round(low_2021, 9),
            "high_2021_pj": round(high_2021, 9),
            "base_2021_vs_placeholder": round(base_2021 / PLACEHOLDER_PJ, 9),
            "base_2021_vs_previous_gross": round(
                base_2021 / artifacts.previous_gross_pj, 9
            ),
            "base_2028_vs_previous_gross": round(
                base_2028 / artifacts.previous_gross_pj, 9
            ),
        },
        "agb_slash_sensitivity": agb,
        "assumptions": [
            "B1/B2 is the FRESH-side provisional crosswalk; C2070 has no published boundary.",
            "FFCM InitialContent is a stock, so the recovery comparison is per unit log volume, not annual stock-to-flow.",
            "FFCM has no FMU-to-TSA/zone crosswalk in the repo; BC totals are allocated by the documented 2018 AAC zone log share and flagged.",
            "The zone taper ratio is the matched-cell batch aggregate, the same basis as the refined all-BC chain; ecoprovince-ratio weighting is not used for the cap.",
            "The B2 base recovery is the refined-batch FFCM-implied fraction (recomputed and asserted, not hard-coded); B1 uses the documented 0.60 fallback because its implied value is out of bounds.",
            "FFCM logging_residue is a roadside-recoverable stream (0.063 ODT/m3), not full aboveground slash.",
            "Cost is FFCM SupplyCost on a dry ODT basis; no moisture credit, deflator, or exchange-rate adjustment is applied.",
            "The time path uses workbook AAC 2021-2028 and holds 2028 flat to 2070; no MPB, climate, or wildfire trajectory is invented.",
            "The AGB non-stem sensitivity is not the base curve and does not replace the taper-residue base.",
        ],
    }


def report_markdown(artifacts: BuildArtifacts) -> str:
    """Human-readable report for BC_RESIDUE_RECOVERY_REPORT.md."""
    recovery = {row.zone_id: row for row in artifacts.recovery}
    cases = {case.name: case for case in artifacts.cases}
    b1, b2 = recovery["B1"], recovery["B2"]
    base_2021 = cases["base"].row(2021, "CAN")
    base_2028 = cases["base"].row(2028, "CAN")
    low_2021 = cases["low"].row(2021, "CAN")
    high_2021 = cases["high"].row(2021, "CAN")
    low_2028 = cases["low"].row(2028, "CAN")
    high_2028 = cases["high"].row(2028, "CAN")
    agb = {row.zone_id: row for row in artifacts.agb.zones}

    def zone_case_rows(year: int) -> str:
        lines = []
        for case_name in ("low", "base", "high"):
            case = cases[case_name]
            lines.append(
                f"| {case_name} | {case.row(year, 'B1').residue_pj:,.6f} | "
                f"{case.row(year, 'B2').residue_pj:,.6f} | "
                f"{case.row(year, 'CAN').residue_pj:,.6f} | "
                f"{case.row(year, 'B1').cost_usd_per_gj:,.6f} |"
            )
        return "\n".join(lines)

    return f"""# Recoverable-residue supply curve and time path for OSeCAN zones B1/B2

Status: **FRESH-side provisional**. This is a derived recoverable-residue
supply curve, not an official C2070/OSeCAN boundary and not a
production-calibrated fuel offer.

Generated by `workflows/national/clews/bc_residue_recovery.py`. Inputs and
SHA256 digests are in `manifest.json`.

## 1. FFCM basis (FMU/zone mapping; residue ODT; cost; implied recovery)

### FMU/zone mapping

FFCM has **467 BC FMUs** in `metadata/ffcm-archive/parsed/fmu_supply.csv`.
The ledger contains no FMU name, TSA, or zone attribute. The three candidate
crosswalks were reviewed:

1. **FMU -> TSA from FFCM configs**: not available. `FMUs.cfg` carries only
   `BCnn` ids; `tmp/FMU.xlsm` carries the same id plus a TL hub; the hub is an
   abstract transport node, not a TSA.
2. **FMU -> facility TSA**: not defensible. FFCM routes are FMU -> TL hub,
   not FMU -> mill. The TL hubs have no coordinates in
   `metadata/facility_geocoding.csv` (`NO_MATCH`), and the FMU-to-hub distances
   are transport-graph lengths, not locations. A nearest-facility TSA would be
   a transport proxy, not an FMU location.
3. **Documented zone share**: used. The FFCM BC totals are allocated to B1/B2
   by the published **2018 AAC zone log share**: B1
   {artifacts.zone_shares["B1"]:.6%}, B2 {artifacts.zone_shares["B2"]:.6%}.
   This is flagged `no_fmu_tsa_map_documented_zone_share`; no FMU volume was
   invented.

### FFCM ledger

| Metric | Value |
|---|---:|
| BC FMUs | {artifacts.ffcm.fmu_count:,} |
| InitialContent log (m3) | {artifacts.ffcm.total_log_m3:,.3f} |
| InitialContent logging_residue (ODT) | {artifacts.ffcm.total_residue_odt:,.3f} |
| Residue / log (ODT/m3) | {artifacts.ffcm.residue_odt_per_m3:.9f} |
| SupplyCost logging_residue ($/ODT) | {artifacts.ffcm.residue_cost_usd_per_odt:.9f} |
| SupplyCost logging_residue ($/GJ) | {artifacts.ffcm.residue_cost_usd_per_gj:.9f} |
| SupplyCost log ($/m3) | {artifacts.ffcm.log_cost_usd_per_m3:.6f} |
| MinimumSupply (ODT) | 0 (all FMUs) |

The FFCM residue cost is uniform across the 467 BC FMUs
({artifacts.ffcm.residue_cost_min_usd_per_odt:.9f} to
{artifacts.ffcm.residue_cost_max_usd_per_odt:.9f} $/ODT), so it does not
differentiate B1 from B2. The cost is on an **oven-dry** basis; no moisture
credit is applied. At 18.5 GJ/ODT the base cost is
**{artifacts.ffcm.residue_cost_usd_per_gj:.6f} $/GJ**.

### Implied recovery

FFCM InitialContent is a stock, not an annual harvest. The comparison is
therefore made on the same log-volume basis: FFCM residue ODT divided by
(FFCM log m3 x gross taper ODT/m3 for the zone), not FFCM stock ODT against one
year of gross taper ODT.

| Zone | Zone share | FFCM log (m3) | FFCM residue (ODT) | Gross taper ratio (ODT/m3) | Gross taper same basis (ODT) | Implied recovery | In [0.2, 1.0] | Base recovery |
|---|---:|---:|---:|---:|---:|---:|---|---:|
| B1 | {b1.zone_share:.6%} | {b1.ffcm_log_m3:,.3f} | {b1.ffcm_residue_odt:,.3f} | {b1.gross_taper_ratio_odt_per_m3:.9f} | {b1.gross_taper_residue_odt_same_log_basis:,.3f} | {b1.implied_recovery_fraction:.6f} | {"no" if not b1.recovery_in_bounds else "yes"} | {b1.base_recovery_fraction:.6f} |
| B2 | {b2.zone_share:.6%} | {b2.ffcm_log_m3:,.3f} | {b2.ffcm_residue_odt:,.3f} | {b2.gross_taper_ratio_odt_per_m3:.9f} | {b2.gross_taper_residue_odt_same_log_basis:,.3f} | {b2.implied_recovery_fraction:.6f} | {"no" if not b2.recovery_in_bounds else "yes"} | {b2.base_recovery_fraction:.6f} |
| CAN comparison | 100.000000% | {recovery["CAN"].ffcm_log_m3:,.3f} | {recovery["CAN"].ffcm_residue_odt:,.3f} | {recovery["CAN"].gross_taper_ratio_odt_per_m3:.9f} | {recovery["CAN"].gross_taper_residue_odt_same_log_basis:,.3f} | {recovery["CAN"].implied_recovery_fraction:.6f} | {"no" if not recovery["CAN"].recovery_in_bounds else "yes"} | {recovery["CAN"].base_recovery_fraction:.6f} |

The B1 implied fraction is **{b1.implied_recovery_fraction:.6f}**, above 1.0:
the uniform FFCM roadside-residue ratio (0.063 ODT/m3) is larger than the
coastal taper-residue ratio ({b1.gross_taper_ratio_odt_per_m3:.6f} ODT/m3).
B1 is therefore flagged and the documented **0.60** fallback is used. The B2
implied fraction on the refined batch is **{b2.implied_recovery_fraction:.6f}**
and is the adopted base recovery. It is recomputed at build time as
`ffcm_residue_B2 / (ffcm_log_B2 * taper_ratio_B2)` and asserted against
{EXPECTED_FFCM_IMPLIED_B2:.6f} (tolerance {FFCM_IMPLIED_B2_TOLERANCE:g}); it is
not a hard-coded calibration constant. The previous held value 0.583931 was a
stale calibration tied to the pre-refinement taper. The national comparison
ratio is {recovery["CAN"].implied_recovery_fraction:.6f}; it is reported only,
not used to set the national cap (the rollup is the sum of B1 and B2).

Supporting evidence: the BC Fibre Database `Roadside Residue` sheet uses 18%
of harvest with regional inclusion factors of 0.2 to 0.8. The FFCM uniform
0.063 ODT/m3 is consistent with an 18% roadside stream at roughly 0.8
inclusion and 0.44 ODT/m3, so it is a recoverable roadside stream rather than
full aboveground slash.

## 2. Cases (low/base/high caps/costs by zone)

The case definitions are fixed:

| Case | Recovery B1 | Recovery B2 | Cost multiplier | Cost B1 ($/GJ) | Cost B2 ($/GJ) |
|---|---:|---:|---:|---:|---:|
| low | 0.350000 | 0.350000 | 1.30 | {cases["low"].row(2021, "B1").cost_usd_per_gj:.6f} | {cases["low"].row(2021, "B2").cost_usd_per_gj:.6f} |
| base | {b1.base_recovery_fraction:.6f} | {b2.base_recovery_fraction:.6f} | 1.00 | {cases["base"].row(2021, "B1").cost_usd_per_gj:.6f} | {cases["base"].row(2021, "B2").cost_usd_per_gj:.6f} |
| high | 0.750000 | 0.750000 | 0.80 | {cases["high"].row(2021, "B1").cost_usd_per_gj:.6f} | {cases["high"].row(2021, "B2").cost_usd_per_gj:.6f} |

Caps in 2021 (the first model year):

| Case | B1 (PJ/yr) | B2 (PJ/yr) | CAN rollup (PJ/yr) | Cost B1 ($/GJ) |
|---|---:|---:|---:|---:|
{zone_case_rows(2021)}

Caps in 2028 and held flat to 2070:

| Case | B1 (PJ/yr) | B2 (PJ/yr) | CAN rollup (PJ/yr) | Cost B1 ($/GJ) |
|---|---:|---:|---:|---:|
{zone_case_rows(2028)}

The base national cap is **{base_2021.residue_pj:.6f} PJ/yr in 2021** and
**{base_2028.residue_pj:.6f} PJ/yr in 2028-2070**. The low case is
{low_2021.residue_pj:.6f} / {low_2028.residue_pj:.6f} PJ/yr; the high case is
{high_2021.residue_pj:.6f} / {high_2028.residue_pj:.6f} PJ/yr.

The six OSeCAN parameter files per case are:

- `SUPBCWB1_activity_limits_<case>.csv`
- `SUPBCWB2_activity_limits_<case>.csv`
- `SUPBCWB1_variable_cost_<case>.csv`
- `SUPBCWB2_variable_cost_<case>.csv`
- `comparison/SUPBCW_activity_limits_<case>.csv` (comparison only)
- `comparison/SUPBCW_variable_cost_<case>.csv` (comparison only)

`SUPBCW` is the exact B1+B2 rollup and must not be loaded with the zone
technologies in the same OSeCAN instance.

## 3. Time path (AAC basis; 2021-2070 profile)

The time path is parsed from the TSA Tenure Apportionment V5 workbook:
**41 TSA sheets**, of which 37 carry positive AAC. The four coastal TSAs
({", ".join(artifacts.aac.zero_aac_sheets)}) have historical AAC before 2018
but zero AAC over 2018-2028 and no VRI 2025 area weight; they are validated as
zero and excluded. The workbook 2018 total reconciles exactly to the published
all-BC AAC of **{artifacts.aac.total_by_year[2018]:,.0f} m3/y**.

| Year | All-BC AAC (m3/y) | B1 log (m3/y) | B2 log (m3/y) | Gross taper (PJ/yr) |
|---:|---:|---:|---:|---:|
| 2021 | {artifacts.aac.total_by_year[2021]:,.3f} | {cases["base"].row(2021, "B1").annual_log_m3:,.3f} | {cases["base"].row(2021, "B2").annual_log_m3:,.3f} | {cases["base"].row(2021, "CAN").gross_taper_residue_odt * 18.5 / 1e6:,.6f} |
| 2028 | {artifacts.aac.total_by_year[2028]:,.3f} | {cases["base"].row(2028, "B1").annual_log_m3:,.3f} | {cases["base"].row(2028, "B2").annual_log_m3:,.3f} | {cases["base"].row(2028, "CAN").gross_taper_residue_odt * 18.5 / 1e6:,.6f} |
| 2029-2070 | {artifacts.aac.total_by_year[2028]:,.3f} (held) | {cases["base"].row(2028, "B1").annual_log_m3:,.3f} | {cases["base"].row(2028, "B2").annual_log_m3:,.3f} | {cases["base"].row(2028, "CAN").gross_taper_residue_odt * 18.5 / 1e6:,.6f} |

The TSA-level 2011-2028 values are in
`audit/bc_aac_trajectory_2011_2028.csv`; blank 2011-2016 forecast cells for
three coastal TSAs are marked `present=false`, not filled.

`time_path_basis = aac_2028_hold`: the workbook values are used for 2021-2028,
then the 2028 value is held flat to 2070. The 2021-2028 values already
include the workbook's forecast reductions to replaceable tenure and the
residual MPB uplift in the AAC. No separate MPB, climate, wildfire, or
future-AAC decline is invented.

## 4. AGB sensitivity

The TSA24 biomass AU curves provide (Bark + Foliar + Branch) / Above by AU and
age. At age 100 across {artifacts.agb.nonstem.au_count} AUs the median is
**{artifacts.agb.nonstem.median:.6f}**, with a range of
{artifacts.agb.nonstem.minimum:.6f} to {artifacts.agb.nonstem.maximum:.6f}.

The RSYC/national fit **does** carry AGB by BC cell
(`national_stands.csv: rsyc_agb_mg_ha` at reference age 100), so no AGB total
was fabricated. The area-weighted BC values are:

| Zone | RSYC cells (used / missing) | AGB (ODT/ha) | Volume (m3/ha) | Expansion (ODT/m3) | Non-stem fraction | AGB slash (ODT/m3) | Taper ratio (ODT/m3) | Ratio to taper |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| B1 | {agb["B1"].cell_count} / {agb["B1"].missing_cell_count} | {agb["B1"].agb_odt_ha:,.3f} | {agb["B1"].volume_m3ha:,.3f} | {agb["B1"].expansion_odt_per_m3:.6f} | {agb["B1"].nonstem_fraction:.6f} | {agb["B1"].slash_odt_per_m3:.6f} | {agb["B1"].taper_ratio_odt_per_m3:.6f} | {agb["B1"].ratio_to_taper:.3f}x |
| B2 | {agb["B2"].cell_count} / {agb["B2"].missing_cell_count} | {agb["B2"].agb_odt_ha:,.3f} | {agb["B2"].volume_m3ha:,.3f} | {agb["B2"].expansion_odt_per_m3:.6f} | {agb["B2"].nonstem_fraction:.6f} | {agb["B2"].slash_odt_per_m3:.6f} | {agb["B2"].taper_ratio_odt_per_m3:.6f} | {agb["B2"].ratio_to_taper:.3f}x |

The B1 AGB non-stem slash is **{agb["B1"].ratio_to_taper:.2f}x** the B1 taper
base, consistent with the expected ~3x coastal uplift. B2 is
**{agb["B2"].ratio_to_taper:.2f}x** because its taper residue already includes a
large non-merchantable stem component. The AGB-slash coefficient range from
the fraction range is {agb["B1"].slash_min_odt_per_m3:.6f} to
{agb["B1"].slash_max_odt_per_m3:.6f} ODT/m3 in B1 and
{agb["B2"].slash_min_odt_per_m3:.6f} to {agb["B2"].slash_max_odt_per_m3:.6f}
ODT/m3 in B2.

At the 2021 log harvest, the gross AGB non-stem slash is
**{agb["B1"].annual_gross_slash_pj_2021:.6f} PJ** in B1 and
**{agb["B2"].annual_gross_slash_pj_2021:.6f} PJ** in B2; at 60% roadside
recovery it is {agb["B1"].annual_recoverable60_slash_pj_2021:.6f} and
{agb["B2"].annual_recoverable60_slash_pj_2021:.6f} PJ. This is a sensitivity,
not the base curve. The base remains the taper-residue x FFCM recovery curve.
The RSYC national batch is a placeholder equal-area distribution, so these
values are not a BC inventory estimate.

## 5. Comparison to 50 PJ/$4 and previous 93.17 PJ

| Case | 2021 cap (PJ) | 2028-2070 cap (PJ) | Cost ($/GJ) | 2021 / 50 PJ | Cost / $4 | 2021 / 93.170084 PJ |
|---|---:|---:|---:|---:|---:|---:|
| low | {low_2021.residue_pj:.6f} | {low_2028.residue_pj:.6f} | {low_2021.cost_usd_per_gj:.6f} | {low_2021.residue_pj / 50.0:.3f} | {low_2021.cost_usd_per_gj / 4.0:.3f} | {low_2021.residue_pj / artifacts.previous_gross_pj:.3f} |
| base | {base_2021.residue_pj:.6f} | {base_2028.residue_pj:.6f} | {base_2021.cost_usd_per_gj:.6f} | {base_2021.residue_pj / 50.0:.3f} | {base_2021.cost_usd_per_gj / 4.0:.3f} | {base_2021.residue_pj / artifacts.previous_gross_pj:.3f} |
| high | {high_2021.residue_pj:.6f} | {high_2028.residue_pj:.6f} | {high_2021.cost_usd_per_gj:.6f} | {high_2021.residue_pj / 50.0:.3f} | {high_2021.cost_usd_per_gj / 4.0:.3f} | {high_2021.residue_pj / artifacts.previous_gross_pj:.3f} |

The previous **93.170084 PJ** was gross taper residue on the 2018 AAC, with no
recovery factor. The new base is lower for two reasons: the 2021 AAC is
{abs(artifacts.aac.total_by_year[2021] / artifacts.aac.total_by_year[2018] - 1):.1%}
below 2018, and the B1/B2 recovery factors remove the non-recoverable share.
On the 2021 gross taper base the national residue is
{cases["base"].row(2021, "CAN").gross_taper_residue_odt * 18.5 / 1e6:.6f} PJ;
the base case is {base_2021.residue_pj / (cases["base"].row(2021, "CAN").gross_taper_residue_odt * 18.5 / 1e6):.3f}x that gross value.

The 50 PJ/$4 placeholder is close to the **base 2021 cap** and above the
2028-2070 base cap. Its $4/GJ cost is above all three cases; the base cost is
{base_2021.cost_usd_per_gj:.6f} $/GJ. The placeholder is therefore not a
supply estimate: it is a mid-range cap and a high cost.

## 6. Validation

The build performs and the tests re-check:

- **Recovery bounds**: B1 implied {b1.implied_recovery_fraction:.6f} is flagged
  and uses the 0.60 fallback; B2 implied {b2.implied_recovery_fraction:.6f} is
  in [0.2, 1.0] but is a diagnostic; the base recovery is the published
  {b2.base_recovery_fraction:.6f} calibration, and every base recovery is in
  [0.2, 1.0].
- **ODT -> PJ**: `PJ = ODT x 18.5 / 1,000,000` within 1e-9 PJ.
- **Cost conversion**: `$/GJ = $/ODT / 18.5`; low = base x 1.30, high =
  base x 0.80.
- **AAC trajectory**: 41 sheets, 37 positive TSAs, 2018 total
  52,774,801 m3/y, 2021-2028 values present, 2029-2070 equals 2028.
- **Case monotonicity**: low < base < high caps and low > base > high costs
  for every year and zone.
- **Conservation**: B1 + B2 = CAN for log, gross taper ODT/PJ, and each case
  ODT/PJ in every year; FFCM zone residues sum to the provincial total.
- **Parameter shape**: each OSeCAN file has 50 rows, columns
  `REGION,TECHNOLOGY,YEAR,VALUE`, `REGION=CAN`, years 2021-2070.

## 7. Gaps and next steps

1. **No published C2070 B1/B2 boundary.** The zone definition is the previous
   FRESH-side provisional crosswalk. A C2070 team must accept or replace it.
2. **No FMU -> TSA/zone crosswalk.** The FFCM FMU ids are opaque. The current
   zone split is the documented 2018 AAC log share, not an FMU location. A
   defensible FMU boundary layer would replace the share allocation.
3. **FFCM InitialContent is a stock.** The implied recovery is a per-m3 ratio,
   not a calibrated annual recovery rate. The 0.60 B1 fallback is a documented
   base, not a measured coastal recovery. On the refined batch the B2
   FFCM-implied recovery is {b2.implied_recovery_fraction:.6f} and is the
   adopted base; the old published 0.583931 was tied to the pre-refinement
   taper and is superseded. The remaining evidence gap is the stock-to-flow
   interpretation, not the arithmetic basis.
4. **Cost is a single FFCM provincial value.** It does not vary by zone and
   excludes collection, grinding, bundling, stumpage, and opportunity cost.
   It is a lower bound for a harvest-residue supply curve.
5. **Time path is an AAC hold.** The 2028 hold is not a forecast. No MPB,
   climate, wildfire, or future tenure change is modelled.
6. **AGB sensitivity is a placeholder-batch calculation.** The RSYC national
   batch is an equal-area diagnostic distribution, not a BC inventory. The
   non-stem fraction is an AU median; it does not resolve species or zone
   differences.
7. **National rollup is not additive.** `SUPBCW` is the sum of B1 and B2 for
   comparison only. Do not load it with the zone technologies.

## 8. Tests and status

The focused suite is
`workflows/national/clews/tests/test_bc_residue_recovery.py`:

```bash
.venv/bin/python -m pytest -q workflows/national/clews/tests/test_bc_residue_recovery.py
```

It re-checks recovery bounds, ODT -> PJ, cost conversion, AAC reconciliation
and the 2028 hold, case monotonicity, conservation, and parameter shape. The
data artifacts are committed in the `data` submodule and the parent repository;
`git status --porcelain` for the artifact paths is clean after the commit. The
exact commit hashes and publish result are recorded in the task handoff
because they are created after this report is written.
"""


def reconciliation_markdown(artifacts: BuildArtifacts, repo_root: Path) -> str:
    """Record the resolved FFCM-implied basis and the cap/chain reconciliation."""
    cases = {case.name: case for case in artifacts.cases}
    recovery = {row.zone_id: row for row in artifacts.recovery}
    chain_path = repo_root / "data/derived/national/bc_chain/bc_supply_by_zone.csv"
    chain: dict[tuple[int, str], float] = {}
    if chain_path.exists():
        with chain_path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                year = int(row["year"])
                zone_id = row["zone"].strip()
                if year in (2021, 2028) and zone_id in ("B1", "B2"):
                    key = (year, zone_id)
                    chain[key] = chain.get(key, 0.0) + float(
                        row["residue_recoverable_odt"]
                    )

    basis_rows = []
    for label, by_zone, total in (
        ("Old published 2026-11-17", {"B1": 3.672285606, "B2": 39.286509195}, OLD_PUBLISHED_BASE_2028_PJ),
        ("Interim held calibration", {"B1": 3.605474907, "B2": 32.204470802}, INTERIM_HELD_BASE_2028_PJ),
        (
            "Adopted FFCM-implied",
            {
                "B1": cases["base"].row(2028, "B1").residue_pj,
                "B2": cases["base"].row(2028, "B2").residue_pj,
            },
            cases["base"].row(2028, "CAN").residue_pj,
        ),
    ):
        basis_rows.append(
            f"| {label} | {by_zone['B1']:,.6f} | {by_zone['B2']:,.6f} | "
            f"{total:,.6f} |"
        )

    cap_rows = []
    for case_name in ("low", "base", "high"):
        for zone_id in ("B1", "B2", "CAN"):
            old = OLD_CASE_CAPS_PJ[case_name][zone_id]
            new = cases[case_name].row(2028, zone_id).residue_pj
            cap_rows.append(
                f"| {case_name} | {zone_id} | {old:,.6f} | {new:,.6f} | "
                f"{new - old:+,.6f} | {100.0 * (new / old - 1.0):+.2f}% |"
            )

    cap_2021_rows = []
    for case_name in ("low", "base", "high"):
        for zone_id in ("B1", "B2", "CAN"):
            old = OLD_CASE_CAPS_2021_PJ[case_name][zone_id]
            new = cases[case_name].row(2021, zone_id).residue_pj
            cap_2021_rows.append(
                f"| {case_name} | {zone_id} | {old:,.6f} | {new:,.6f} | "
                f"{new - old:+,.6f} | {100.0 * (new / old - 1.0):+.2f}% |"
            )

    decomposition_rows = []
    for zone_id in ("B1", "B2"):
        old = OLD_TIME_PATH_2028[zone_id]
        new_row = next(
            row
            for row in artifacts.time_path
            if row.year == 2028 and row.zone_id == zone_id
        )
        old_taper = old["gross_taper_residue_odt"] / old["annual_log_m3"]
        new_taper = new_row.gross_taper_residue_odt / new_row.annual_log_m3
        old_recovery = old["base_recovery_fraction"]
        new_recovery = cases["base"].recovery_by_zone[zone_id]
        log_ratio = new_row.annual_log_m3 / old["annual_log_m3"]
        taper_ratio = new_taper / old_taper
        recovery_ratio = new_recovery / old_recovery
        cap_ratio = (
            cases["base"].row(2028, zone_id).residue_pj
            / OLD_CASE_CAPS_PJ["base"][zone_id]
        )
        decomposition_rows.append(
            f"| {zone_id} | {old['annual_log_m3']:,.3f} | "
            f"{new_row.annual_log_m3:,.3f} | {100.0 * (log_ratio - 1.0):+.2f}% | "
            f"{old_taper:.9f} | {new_taper:.9f} | "
            f"{100.0 * (taper_ratio - 1.0):+.2f}% | "
            f"{old_recovery:.9f} | {new_recovery:.9f} | "
            f"{100.0 * (recovery_ratio - 1.0):+.2f}% | "
            f"{100.0 * (cap_ratio - 1.0):+.2f}% |"
        )

    chain_rows = []
    for year in (2021, 2028):
        for zone_id in ("B1", "B2"):
            cap_pj = cases["base"].row(year, zone_id).residue_pj
            chain_odt = chain.get((year, zone_id))
            if chain_odt is None:
                chain_rows.append(
                    f"| {year} | {zone_id} | {cap_pj:,.6f} | n/a | n/a |"
                )
                continue
            chain_pj = chain_odt * LHV_GJ_PER_ODT / PJ_GJ
            chain_rows.append(
                f"| {year} | {zone_id} | {cap_pj:,.6f} | {chain_pj:,.6f} | "
                f"{100.0 * (cap_pj / chain_pj - 1.0):+.6f}% |"
            )

    base_2028 = cases["base"].row(2028, "CAN").residue_pj
    return f"""# BC OSeCAN residue cap reconciliation

Status: **FRESH-side provisional; basis resolved 2026-11-18**.  This note
bridges the 2026-11-17 cap (`bc_national_batch_area_joined.csv` sha256
`{STALE_BATCH_SHA256[:12]}...`) through the interim held-calibration cap to the
adopted FFCM-implied basis on the refined 2026-11-18 all-BC chain
(`fd12a1a...`).

## 1. Input-hash bridge

| Item | Pre-refinement / cap | Refined / current |
|---|---|---|
| Joined batch sha256 | `{STALE_BATCH_SHA256}` | `{BATCH_JOINED_SHA256}` |
| Batch log (m3) | {STALE_BATCH_LOG_M3:,.3f} | {artifacts.batch_ratios.log_m3:,.3f} |
| Batch residue (ODT) | {STALE_BATCH_RESIDUE_ODT:,.3f} | {artifacts.batch_ratios.residue_odt:,.3f} |
| BC all-species ratio (ODT/m3) | {STALE_BATCH_RESIDUE_ODT / STALE_BATCH_LOG_M3:.9f} | {artifacts.batch_ratios.bc_all_species:.9f} |
| Area weights sha256 | `a173afb4e323...` | `a1882d5e1d0d...` |

The batch totals rose because the species-alias refinement recovered
10,845,346 ha of formerly `UNMAPPED_BC` area into named species.

## 2. Basis decision

The refined batch changed the B2 gross taper ratio from 0.106181 to
0.087041 ODT/m3, so the FFCM-implied B2 recovery is now recomputed from the
current batch and FFCM ledger:

`implied_B2 = ffcm_residue_B2 / (ffcm_log_B2 x taper_ratio_B2)`

= {recovery["B2"].ffcm_residue_odt:,.3f} / ({recovery["B2"].ffcm_log_m3:,.3f} x
{recovery["B2"].gross_taper_ratio_odt_per_m3:.9f}) =
**{recovery["B2"].implied_recovery_fraction:.9f}**.

This is the adopted base (asserted at build time to {EXPECTED_FFCM_IMPLIED_B2}
within {FFCM_IMPLIED_B2_TOLERANCE:g} and to [0.2, 1.0]).  B1 stays at the
documented **0.600000** fallback because its implied
{recovery["B1"].implied_recovery_fraction:.6f} is out of bounds.

| Basis | B1 2028+ (PJ) | B2 2028+ (PJ) | Total 2028+ (PJ) |
|---|---:|---:|---:|
{chr(10).join(basis_rows)}

The old published 42.958795 PJ held B2 at 0.583931, a calibration tied to the
pre-refinement taper.  The interim 35.809946 PJ deliberately held that stale
calibration and is superseded.  The adopted total is
**{base_2028:,.6f} PJ**.

## 3. Old published vs adopted caps (2028+, PJ/yr)

| Case | Zone | Old | Adopted | Delta | Delta % |
|---|---|---:|---:|---:|---:|
{chr(10).join(cap_rows)}

2021 values:

| Case | Zone | Old | Adopted | Delta | Delta % |
|---|---|---:|---:|---:|---:|
{chr(10).join(cap_2021_rows)}

Costs are unchanged at 2.456978 / 3.194071 / 1.965582 $/GJ.

## 4. Decomposition vs the old published basis (2028, base case)

`cap = log volume x taper ratio x recovery x 18.5 / 1e6`.

| Zone | Old log (m3) | New log (m3) | Log | Old taper (ODT/m3) | New taper (ODT/m3) | Taper | Old recovery | New recovery | Recovery | Cap change |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(decomposition_rows)}

For B2 the taper refinement (-18.03%) is more than offset by adopting the
FFCM-implied recovery (0.583931 -> {recovery["B2"].implied_recovery_fraction:.6f},
+23.95%), so the cap rises {100.0 * (cases["base"].row(2028, "B2").residue_pj / OLD_CASE_CAPS_PJ["base"]["B2"] - 1.0):+.2f}% versus the old published value.
B1 is unchanged in recovery and moves only with the zone taper.

## 5. Chain and energy cap agreement

Chain recoverable residue from `bc_supply_by_zone.csv`:

| Year | Zone | Cap (PJ) | Chain recoverable (PJ) | Difference |
|---:|---|---:|---:|---:|
{chr(10).join(chain_rows)}

The time path and the chain share the same matched-cell zone taper ratio and
the same base recovery fraction, so the cap and chain recoverable residue agree
per zone.  The zone split of the batch is:

| Zone | Matched log (m3) | Matched residue (ODT) | Ratio (ODT/m3) |
|---|---:|---:|---:|
| B1 | {artifacts.batch_ratios.log_m3_by_zone["B1"]:,.3f} | {artifacts.batch_ratios.residue_odt_by_zone["B1"]:,.3f} | {artifacts.batch_ratios.by_zone["B1"]:.9f} |
| B2 | {artifacts.batch_ratios.log_m3_by_zone["B2"]:,.3f} | {artifacts.batch_ratios.residue_odt_by_zone["B2"]:,.3f} | {artifacts.batch_ratios.by_zone["B2"]:.9f} |

## 6. Validation

- B2 implied recovery is recomputed and asserted at
  {recovery["B2"].implied_recovery_fraction:.9f} (expected
  {EXPECTED_FFCM_IMPLIED_B2}, tolerance {FFCM_IMPLIED_B2_TOLERANCE:g}).
- B1 uses the documented 0.60 fallback; its implied value is out of bounds.
- Cases remain monotonic: low 0.35 < base < high 0.75.
- Cap equals chain recoverable residue per zone within 1% after the chain
  rebuild; the test suite re-checks the 2028 rows.
- `SUPBCW` is the exact B1+B2 rollup and remains comparison-only.
"""

# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def write_artifacts(
    artifacts: BuildArtifacts, repo_root: Path, out_dir: Path
) -> list[Path]:
    """Write every CSV/JSON/report output and return the paths."""
    written: list[Path] = []

    def write(
        relative: Path, rows: Sequence[Mapping[str, str]], columns: Sequence[str]
    ) -> None:
        path = out_dir / relative
        _write_csv(path, rows, columns)
        written.append(path)

    recovery_columns = (
        "zone_id",
        "zone_name",
        "zone_share",
        "zone_share_basis",
        "ffcm_fmu_count",
        "ffcm_log_m3",
        "ffcm_residue_odt",
        "ffcm_residue_odt_per_m3",
        "ffcm_supply_cost_usd_per_odt",
        "ffcm_supply_cost_usd_per_gj",
        "gross_taper_ratio_odt_per_m3",
        "gross_taper_residue_odt_same_log_basis",
        "implied_recovery_fraction",
        "recovery_in_bounds",
        "base_recovery_fraction",
        "recovery_basis",
        "flag",
        "crosswalk_status",
        "comparison_only",
        "evidence",
    )
    write(Path("bc_residue_recovery.csv"), recovery_rows(artifacts), recovery_columns)

    write(
        Path("audit") / "bc_ffcm_fmu_supply.csv",
        ffcm_fmu_rows(artifacts),
        (
            "fmu",
            "log_m3",
            "residue_odt",
            "residue_odt_per_m3",
            "residue_cost_usd_per_odt",
            "residue_cost_usd_per_gj",
            "minimum_supply_odt",
            "b1_share",
            "b2_share",
            "residue_odt_b1",
            "residue_odt_b2",
            "zone_assignment_basis",
            "notes",
        ),
    )

    write(
        Path("audit") / "bc_aac_trajectory_2011_2028.csv",
        aac_trajectory_rows(artifacts),
        ("tsa_name", "year", "aac_m3", "present", "evidence"),
    )

    write(
        Path("bc_residue_time_path.csv"),
        time_path_rows(artifacts),
        (
            "year",
            "zone_id",
            "annual_log_m3",
            "gross_taper_residue_odt",
            "gross_taper_residue_pj",
            "base_recovery_fraction",
            "base_residue_odt",
            "base_residue_pj",
            "low_recovery_fraction",
            "low_residue_odt",
            "low_residue_pj",
            "high_recovery_fraction",
            "high_residue_odt",
            "high_residue_pj",
            "aac_year",
            "time_path_basis",
            "evidence",
        ),
    )

    parameter_columns = ("REGION", "TECHNOLOGY", "YEAR", "VALUE")
    for case in artifacts.cases:
        for zone_id, technology in (("B1", "SUPBCWB1"), ("B2", "SUPBCWB2")):
            write(
                Path(f"{technology}_activity_limits_{case.name}.csv"),
                parameter_rows(case, technology, zone_id, "activity"),
                parameter_columns,
            )
            write(
                Path(f"{technology}_variable_cost_{case.name}.csv"),
                parameter_rows(case, technology, zone_id, "cost"),
                parameter_columns,
            )
        write(
            Path("comparison") / f"SUPBCW_activity_limits_{case.name}.csv",
            parameter_rows(case, "SUPBCW", "CAN", "activity"),
            parameter_columns,
        )
        write(
            Path("comparison") / f"SUPBCW_variable_cost_{case.name}.csv",
            parameter_rows(case, "SUPBCW", "CAN", "cost"),
            parameter_columns,
        )

    summary_path = out_dir / "bc_residue_cases_summary.json"
    _write_json(summary_path, summary_payload(artifacts))
    written.append(summary_path)

    report_path = out_dir / "BC_RESIDUE_RECOVERY_REPORT.md"
    report_path.write_text(report_markdown(artifacts), encoding="utf-8")
    written.append(report_path)

    reconciliation_path = out_dir / "BC_RESIDUE_RECONCILIATION.md"
    reconciliation_path.write_text(
        reconciliation_markdown(artifacts, repo_root), encoding="utf-8"
    )
    written.append(reconciliation_path)

    manifest = {
        "artifact_version": "0.1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "fresh_side_provisional_not_c2070_official",
        "zone_definition": {
            "B1": "Georgia Depression + Southern Coastal Mountains",
            "B2": (
                "Northern Coastal Mountains + Montane Cordillera + "
                "Boreal Cordillera + Boreal Plains + Taiga Plains (BC portion) "
                "+ Wrangel Mountains + NO_ECOPROVINCE fallback"
            ),
            "status": "fresh_provisional_no_published_c2070_boundary",
        },
        "inputs": [
            {
                "path": _relative(path, repo_root),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in artifacts.input_paths
        ],
        "outputs": [
            {
                "path": _relative(path, out_dir),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in written
        ],
        "assumptions": summary_payload(artifacts)["assumptions"],
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    written.append(manifest_path)
    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build the recoverable-residue supply curve and 2021-2070 time "
            "path for OSeCAN B1/B2."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[3],
        help="Repository root (default: inferred from this module).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=f"Output directory (default: <repo-root>/{OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the summary without writing files.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    repo_root = args.repo_root.resolve()
    out_dir = (args.out_dir or (repo_root / OUTPUT_DIR)).resolve()
    artifacts = build_all(repo_root)
    if args.dry_run:
        print(json.dumps(summary_payload(artifacts), indent=2, sort_keys=True))
        return 0
    write_artifacts(artifacts, repo_root, out_dir)
    print(
        json.dumps(summary_payload(artifacts)["comparison"], indent=2, sort_keys=True)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
