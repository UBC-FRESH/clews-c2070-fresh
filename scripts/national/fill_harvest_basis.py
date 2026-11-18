"""Fill the national harvest basis from observed FSD harvest and 2024 evidence.

The template keeps three separate concepts apart:

* ``actual_harvest_m3``: observed harvest, FSD 2021-2024;
* ``aac_m3``: a legal/planning ceiling or a clearly labelled proxy;
* ``wood_supply_m3``: the 2024 NFD wood-supply estimate, not harvest;
* ``base_harvest_m3``: the 2024 observed harvest held flat from 2025.

National ``CAN`` rows for the base, low-100M, and high-150M paths are appended
after the 600 provincial rows.  The low and high paths are scenario bounds, not
forecasts and not a central case.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HARVEST_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
TEMPLATE_PATH = HARVEST_DIR / "harvest_basis_template.csv"
REFERENCE_PATH = HARVEST_DIR / "harvest_reference_paths.csv"
PROVINCES_PATH = HARVEST_DIR / "provinces.csv"
MANIFEST_PATH = HARVEST_DIR / "manifest.json"
RAW_DIR = REPO_ROOT / "data/raw/nrcan_fsd"
FSD_VOLUME = RAW_DIR / "StatProfile_Management_volume_EN.csv"
FSD_AREA = RAW_DIR / "StatProfile_Management_harvest_EN.csv"

FSD_VOLUME_URL = (
    "https://raw.githubusercontent.com/NRCan/statisticalprofile-datasets/"
    "refs/heads/main/FSD-EN-datasets/StatProfile_Management_volume_EN.csv"
)
FSD_AREA_URL = (
    "https://raw.githubusercontent.com/NRCan/statisticalprofile-datasets/"
    "refs/heads/main/FSD-EN-datasets/StatProfile_Management_harvest_EN.csv"
)
NFD_URL = "https://national-forestry-database.ca/download/data/csv/"
YEARS = tuple(range(2021, 2071))
OBSERVED_YEARS = (2021, 2022, 2023, 2024)
BASE_HOLD_M3 = 118_977_339
LOW_100M_M3 = 100_000_000
HIGH_150M_M3 = 150_000_000

PROVINCE_NAMES = {
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
}
FSD_NAMES = {
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
}

# 2024 observed harvest, m3.  These are the published FSD values used as the
# cross-check and as the fallback if the FSD download is unavailable.
OBSERVED_2024_M3 = {
    "BC": 38_876_568,
    "QC": 25_939_056,
    "AB": 22_260_402,
    "ON": 13_121_645,
    "NB": 9_341_187,
    "SK": 4_224_936,
    "NS": 2_322_413,
    "NL": 1_413_957,
    "MB": 1_103_943,
    "PE": 293_880,
    "YT": 42_715,
    "NT": 36_637,
}

# 2024/2025 AAC or documented proxy.  ``None`` means no admissible numeric AAC.
AAC_2024 = {
    "BC": None,  # keep the repo-parsed BC path; Crown AAC is 58-60M, not this path
    "QC": 34_115_200,
    "AB": 30_100_000,
    "ON": 28_700_000,
    "NB": 6_234_700,
    "SK": 8_940_000,
    "MB": None,  # documented range 7.39-7.99M; a midpoint would be fabricated
    "NS": 1_720_000,  # Crown only; 4.03M private is additional
    "NL": 2_012_233,
    "PE": None,  # no statutory AAC; 40k Crown + 420k private planning
    "YT": 261_000,
    "NT": 36_376,  # 2020-2024 actual mean, explicitly not a legal AAC
}
AAC_BASIS = {
    "BC": "repo_parsed_tsa_aac",
    "QC": "legal_crown_aac",
    "AB": "crown_aac_approx",
    "ON": "crown_aac_approx",
    "NB": "legal_crown_aac",
    "SK": "crown_aac_approx",
    "MB": "documented_range_not_numeric",
    "NS": "crown_aac_excludes_private",
    "NL": "legal_crown_aac",
    "PE": "no_statutory_aac",
    "YT": "legal_crown_aac",
    "NT": "actual_mean_proxy_no_territory_aac",
}
AAC_NOTES = {
    "BC": "Existing repo-parsed TSA AAC path retained; 2024/2025 Crown AAC is reported separately as ~58-60M m3/yr.",
    "QC": "Quebec 2024/2025 Crown AAC.",
    "AB": "Alberta 2024/2025 Crown AAC, reported as ~30.1M m3/yr; approximate.",
    "ON": "Ontario 2024/2025 Crown AAC, reported as ~28.7M m3/yr; approximate.",
    "NB": "New Brunswick 2024/2025 Crown AAC.",
    "SK": "Saskatchewan 2024/2025 Crown AAC, reported as ~8.94M m3/yr; approximate.",
    "MB": "Manitoba 2024/2025 AAC documented only as a range ~7.39-7.99M m3/yr; no single numeric value entered.",
    "NS": "Nova Scotia Crown AAC 1.72M m3/yr; private AAC 4.03M m3/yr is additional and not included.",
    "NL": "Newfoundland and Labrador 2024/2025 Crown AAC.",
    "PE": "No statutory provincial AAC. Planning volumes reported as 40k m3 Crown + 420k m3 private.",
    "YT": "Yukon 2024/2025 Crown AAC.",
    "NT": "No territory-wide AAC. 36,376 m3/yr is the 2020-2024 actual-harvest mean entered as a proxy, not a legal AAC.",
}

WOOD_SUPPLY_2024 = {
    "BC": 63_774_601,
    "QC": 48_467_117,
    "AB": 31_632_061,
    "ON": 28_802_910,
    "NB": 9_590_407,
    "SK": 8_939_765,
    "MB": 7_990_048,
    "NS": 5_750_000,
    "NL": 2_012_233,
    "PE": 460_000,
    "YT": 261_000,
    "NT": None,  # not reported
}
NATIONAL_WOOD_SUPPLY_2024 = 207_680_142

TEMPLATE_FIELDS = (
    "province",
    "year",
    "aac_m3",
    "actual_harvest_m3",
    "wood_supply_m3",
    "base_harvest_m3",
    "harvest_type",
    "scenario",
    "aac_basis",
    "base_basis",
    "source",
    "source_url",
    "evidence_tier",
    "notes",
)
REFERENCE_FIELDS = (
    "year",
    "observed_2021_2024_m3",
    "base_hold_2024_m3",
    "low_100m_m3",
    "high_150m_m3",
    "source",
    "evidence",
    "notes",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_fsd(path: Path) -> dict[int, dict[str, int]]:
    """Read FSD volume rows into ``{year: {jurisdiction: m3}}``."""
    if not path.exists():
        raise FileNotFoundError(
            f"missing FSD volume CSV: {path}.  Download {FSD_VOLUME_URL} first."
        )
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    values: dict[int, dict[str, int]] = {}
    for row in rows:
        year = int(row["Year"])
        values.setdefault(year, {})[row["Jurisdiction"]] = int(
            row["Volume (cubic metres)"]
        )
    return values


def _validate_observed(fsd: Mapping[int, Mapping[str, int]]) -> None:
    """Fail loudly if FSD drifts from the reviewed values."""
    for year in OBSERVED_YEARS:
        if year not in fsd:
            raise ValueError(f"FSD has no {year} rows")
    national_2024 = fsd[2024]["Canada"]
    if national_2024 != BASE_HOLD_M3:
        raise ValueError(
            f"FSD 2024 Canada total {national_2024} != reviewed {BASE_HOLD_M3}"
        )
    for province, expected in OBSERVED_2024_M3.items():
        observed = fsd[2024][FSD_NAMES[province]]
        if observed != expected:
            raise ValueError(
                f"FSD 2024 {province} {observed} != reviewed {expected}"
            )
    summed = sum(fsd[2024][FSD_NAMES[p]] for p in PROVINCE_NAMES)
    if summed != national_2024:
        raise ValueError(
            f"FSD 2024 provincial sum {summed} != Canada total {national_2024}"
        )


def _existing_bc_aac() -> dict[int, float]:
    """Read the retained BC AAC path from the previous template."""
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"missing existing template: {TEMPLATE_PATH}")
    with TEMPLATE_PATH.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    series = {
        int(row["year"]): float(row["aac_m3"])
        for row in rows
        if row["province"] == "BC" and row["aac_m3"]
    }
    if len(series) != 50:
        raise ValueError(f"expected 50 BC AAC years, found {len(series)}")
    return series


def _province_row(
    province: str,
    year: int,
    fsd: Mapping[int, Mapping[str, int]],
    bc_aac: Mapping[int, float],
) -> dict[str, object]:
    is_observed = year in OBSERVED_YEARS
    observed = (
        fsd[year][FSD_NAMES[province]]
        if is_observed
        else OBSERVED_2024_M3[province]
    )

    aac: float | None = None
    harvest_type = "base_hold"
    base_basis = "observed_2024_hold"
    evidence_tier = "derived_2024_hold"
    source = "FSD observed harvest (2021-2024) for the 2024 base; 2024 value held flat 2025-2070"
    source_url = FSD_VOLUME_URL
    notes = (
        "Base path holds the 2024 observed harvest flat; it is a documented "
        "accounting path, not a forecast."
    )

    if province == "BC":
        aac = bc_aac[year]
        harvest_type = "aac" if year <= 2028 else "aac_hold"
        evidence_tier = (
            "published_aac_repo_parsed" if year <= 2028 else "derived_2028_hold"
        )
        source = (
            "TSA Tenure Apportionment V5.xls (2018 BC Fibre Database Benchmark "
            "V3); sum of aac_m3 in "
            "data/derived/national/clews/audit/bc_aac_trajectory_2011_2028.csv "
            "across 37 TSAs"
        )
        source_url = ""
        notes = (
            "Published AAC ceiling from workbook rows 2021-2028; 2029-2070 holds "
            "the 2028 AAC flat.  Not actual harvest.  The observed 2024 harvest "
            "is a separate column."
        )
        if is_observed:
            harvest_type = "actual_harvest"
            evidence_tier = "published_actual_harvest_fsd"
            source = f"{source}; observed harvest from FSD"
    elif is_observed:
        harvest_type = "actual_harvest"
        evidence_tier = "published_actual_harvest_fsd"
        source = "NRCan Statistical Profiles, FSD management volume"
        source_url = FSD_VOLUME_URL
        notes = "Observed provincial harvest volume, FSD 2021-2024."

    if year == 2024:
        aac = AAC_2024[province] if province != "BC" else aac
        if AAC_2024[province] is not None:
            evidence_tier = (
                "published_actual_harvest_fsd+published_aac_jurisdiction_2024_2025"
            )
            notes = f"{notes} {AAC_NOTES[province]}"

    wood_supply = WOOD_SUPPLY_2024[province] if year == 2024 else None
    if wood_supply is not None:
        notes = (
            f"{notes} 2024 NFD wood supply {wood_supply:,} m3 is potential "
            "supply, not harvest."
        )

    return {
        "province": province,
        "year": year,
        "aac_m3": "" if aac is None else repr(float(aac)),
        "actual_harvest_m3": observed if is_observed else "",
        "wood_supply_m3": "" if wood_supply is None else wood_supply,
        "base_harvest_m3": observed if is_observed else base_m3(province),
        "harvest_type": harvest_type,
        "scenario": "observed" if is_observed else "base_hold_2024",
        "aac_basis": (
            AAC_BASIS[province]
            if province == "BC" or year == 2024
            else "not_reported"
        ),
        "base_basis": "observed" if is_observed else base_basis,
        "source": source,
        "source_url": source_url,
        "evidence_tier": evidence_tier,
        "notes": notes,
    }


def base_m3(province: str) -> int:
    """Return the 2024 observed harvest for one province."""
    return OBSERVED_2024_M3[province]


def _national_rows(observed_national: Mapping[int, int]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for scenario, value, evidence in (
        ("base_hold_2024", BASE_HOLD_M3, "derived_2024_hold"),
        ("low_100m", LOW_100M_M3, "scenario_national_bound"),
        ("high_150m", HIGH_150M_M3, "scenario_national_bound"),
    ):
        for year in YEARS:
            observed = observed_national[year] if year in OBSERVED_YEARS else None
            amount = observed if observed is not None else value
            rows.append(
                {
                    "province": "CAN",
                    "year": year,
                    "aac_m3": "",
                    "actual_harvest_m3": observed if observed is not None else "",
                    "wood_supply_m3": "",
                    "base_harvest_m3": amount,
                    "harvest_type": "scenario_national",
                    "scenario": scenario,
                    "aac_basis": "not_applicable",
                    "base_basis": "observed" if observed is not None else evidence,
                    "source": (
                        "National rollup of FSD 2024 observed harvest, held flat"
                        if scenario == "base_hold_2024"
                        else "National scenario bound from the harvest review"
                    ),
                    "source_url": FSD_VOLUME_URL if scenario == "base_hold_2024" else "",
                    "evidence_tier": evidence if observed is None else "published_actual_harvest_fsd",
                    "notes": (
                        "Counterfactual national scenario path; not a forecast and "
                        "not a central case.  The 150M high case is an upper bound."
                        if scenario == "high_150m"
                        else "Counterfactual national scenario path; not a forecast."
                    ),
                }
            )
    return rows


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _write_reference_paths(observed_national: Mapping[int, int]) -> None:
    rows = []
    for year in YEARS:
        observed = observed_national[year] if year in OBSERVED_YEARS else None
        rows.append(
            {
                "year": year,
                "observed_2021_2024_m3": "" if observed is None else observed,
                "base_hold_2024_m3": observed if observed is not None else BASE_HOLD_M3,
                "low_100m_m3": observed if observed is not None else LOW_100M_M3,
                "high_150m_m3": observed if observed is not None else HIGH_150M_M3,
                "source": (
                    "FSD observed harvest 2021-2024; national base/low/high paths "
                    "from the harvest review"
                ),
                "evidence": (
                    "published_actual_harvest_fsd for 2021-2024; "
                    "derived_2024_hold and scenario_national_bound for 2025-2070"
                ),
                "notes": (
                    "Low-100M and high-150M are scenario bounds, not forecasts.  "
                    "The 150M high case must not be presented as the central basis."
                ),
            }
        )
    _write_csv(REFERENCE_PATH, REFERENCE_FIELDS, rows)


def _write_provinces() -> None:
    rows = []
    for province, name in PROVINCE_NAMES.items():
        status = "observed_actual_2021_2024_plus_base_hold"
        aac_status = "aac_2024_present" if AAC_2024[province] is not None else AAC_BASIS[province]
        rows.append(
            {
                "province": province,
                "name": name,
                "type": "province" if province not in {"YT", "NT"} else "territory",
                "template_rows": 50,
                "coverage_status": status,
                "aac_covered_years": "2024 only (2024/2025 value)" if province != "BC" else "2021-2070 (repo AAC path)",
                "actual_harvest_available": "yes (FSD 2021-2024)",
                "source_status": f"{aac_status}; NFD wood supply 2024",
                "notes": AAC_NOTES[province],
            }
        )
    rows.append(
        {
            "province": "NU",
            "name": "Nunavut",
            "type": "territory",
            "template_rows": 0,
            "coverage_status": "not_in_requested_scope",
            "aac_covered_years": "none",
            "actual_harvest_available": "not reported in FSD; no zero assumed",
            "source_status": "not_assessed",
            "notes": (
                "Not in the requested 12-jurisdiction fill list.  FSD has no "
                "Nunavut rows; no commercial harvest is not asserted."
            ),
        }
    )
    _write_csv(PROVINCES_PATH, rows[0].keys(), rows)


def main() -> None:
    fsd = _read_fsd(FSD_VOLUME)
    _validate_observed(fsd)
    bc_aac = _existing_bc_aac()

    provincial = [
        _province_row(province, year, fsd, bc_aac)
        for province in PROVINCE_NAMES
        for year in YEARS
    ]
    observed_national = {year: fsd[year]["Canada"] for year in OBSERVED_YEARS}
    rows = provincial + _national_rows(observed_national)
    _write_csv(TEMPLATE_PATH, TEMPLATE_FIELDS, rows)
    _write_reference_paths(observed_national)
    _write_provinces()

    inputs = [
        {
            "path": str(FSD_VOLUME.relative_to(REPO_ROOT)),
            "role": "FSD provincial harvest volume, 2000-2024",
            "url": FSD_VOLUME_URL,
            "bytes": FSD_VOLUME.stat().st_size,
            "sha256": _sha256(FSD_VOLUME),
        },
        {
            "path": str(FSD_AREA.relative_to(REPO_ROOT)),
            "role": "FSD harvested area, 2000-2024; provenance only",
            "url": FSD_AREA_URL,
            "bytes": FSD_AREA.stat().st_size,
            "sha256": _sha256(FSD_AREA),
        },
    ]
    outputs = [
        {
            "path": TEMPLATE_PATH.name,
            "rows": len(rows),
            "sha256": _sha256(TEMPLATE_PATH),
        },
        {
            "path": REFERENCE_PATH.name,
            "rows": len(YEARS),
            "sha256": _sha256(REFERENCE_PATH),
        },
        {
            "path": PROVINCES_PATH.name,
            "rows": 13,
            "sha256": _sha256(PROVINCES_PATH),
        },
    ]
    manifest = {
        "artifact_version": "0.2.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": "observed_2024_harvest_filled_base_hold_scenarios_documented",
        "scope": "national harvest/AAC/wood-supply basis, 2021-2070",
        "inputs": inputs,
        "outputs": outputs,
        "coverage": {
            "provinces_with_actual_harvest": list(PROVINCE_NAMES),
            "provinces_with_aac_2024": [
                province for province, value in AAC_2024.items() if value is not None
            ],
            "provinces_without_numeric_aac_2024": [
                province for province, value in AAC_2024.items() if value is None
            ],
            "national_rows": 3 * len(YEARS),
            "provincial_rows": len(provincial),
            "national_rollup_included": True,
            "national_rollup_basis": (
                "Sum of 12 provincial FSD 2024 actual values equals 118,977,339 m3; "
                "Nunavut has no FSD row and is not assumed zero."
            ),
        },
        "assumptions": [
            "2021-2024 actual harvest is the FSD published management volume.",
            "2024 observed harvest is held flat from 2025 to 2070 as the base path; it is not a forecast.",
            "The low-100M and high-150M paths are scenario bounds; 150M is an upper bound, not a central case.",
            "2024 NFD wood supply is potential supply, not harvest; NT is not reported.",
            "BC keeps the repo-parsed TSA AAC path for 2021-2028; the 2024 observed harvest does not overwrite it.",
            "Missing AAC values are left blank and labelled; no midpoint or interpolation is fabricated.",
            "NFD live download failed TLS certificate validation; 2024 NFD values are the reviewed compilation values, not a fresh download.",
        ],
        "fallback": {
            "nfd_live_download": "failed_tls_certificate_validation",
            "action": "used FSD CSV for 2021-2024 harvest and the reviewed 2024 NFD values supplied with the task",
        },
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
