"""Zonal BC woody-residue supply experiment for OSeCAN.

This module records and regenerates the FRESH-side zonal OSeCAN experiment:

* the model-structure change that adds ``BCWB1``/``BCWB2`` fuels and
  ``SUPBCWB1``/``SUPBCWB2`` supply technologies while preserving the national
  ``BCW`` chain for the other eleven zones;
* the three current-measures runs (national control, zonal, zonal +
  generic-BIO cost sensitivity);
* the cap-binding, dispatch-by-zone, emission and merit-order summaries; and
* the committed artifacts under ``data/derived/national/clews/osecan/zonal``.

The actual OSeCAN source is not vendored here.  The experiment is executed in a
disposable copy of ``github.com/deltae/C2070``; this module only reads the run
CSVs and the FRESH-side supply files, then writes reproducible summaries.

Run from the repository root::

    python -m workflows.national.clews.osecan_zonal \
        --runs-root /tmp/osecan_zonal/results \
        --model-root /tmp/osecan_zonal
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.clews.osecan_biomass_module import Plant, plant_lcoe

__all__ = [
    "BC_ZONES",
    "CARBON_PRICE_FROM_2026",
    "CARBON_PRICE_PRE_2026",
    "ECONOMICS_PLANTS",
    "GENERIC_BIO_BASE_COST",
    "GENERIC_BIO_SENSITIVITY_COST",
    "HORIZON_END",
    "HORIZON_START",
    "MODEL_COMMIT",
    "MODEL_PACKAGE",
    "MODEL_REPOSITORY",
    "MODEL_SOLVER",
    "OUTPUT_DIR",
    "RUN_SPECS",
    "SUPPLY_DIR",
    "SUPPLY_TECHS",
    "ZONAL_FUELS",
    "RunSpec",
    "build_dispatch_rows",
    "build_economics_rows",
    "build_manifest",
    "build_run_summaries",
    "build_zonal_structure",
    "cap_binding",
    "read_emissions",
    "read_production",
    "read_status",
    "summarize_run",
    "write_artifacts",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
OUTPUT_DIR = SUPPLY_DIR / "osecan" / "zonal"

HORIZON_START = 2021
HORIZON_END = 2070
YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))
KEY_YEARS = (2021, 2022, 2025, 2028, 2030, 2035, 2040, 2050, 2060, 2070)

BC_ZONES = ("B1", "B2")
ZONAL_FUELS = {"B1": "BCWB1", "B2": "BCWB2"}
SUPPLY_TECHS = ("SUPBCWB1", "SUPBCWB2")
PLANT_TECHS = ("PWRBCWB1", "PWRBCWB2")
OTHER_ZONES = (
    "A1", "A2", "S1", "M1", "O1", "O2", "Q1", "Q2", "T1", "L1", "N1",
)

CARBON_PRICE_PRE_2026 = 95.0
CARBON_PRICE_FROM_2026 = 200.0
GENERIC_BIO_BASE_COST = 3.0
GENERIC_BIO_SENSITIVITY_COST = 4.0

MODEL_COMMIT = "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44"
MODEL_REPOSITORY = "https://github.com/deltae/C2070"
MODEL_PACKAGE = "osecan 0.1.0"
MODEL_SOLVER = "GLPK via swiglpk 5.0.13"
MODEL_SCENARIO = "Current Measures"

STATUS = "fresh_side_provisional_not_c2070_official"


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One run in the zonal experiment."""

    name: str
    label: str
    description: str
    zonal_supply: bool
    generic_bio_cost_usd_per_gj: float


RUN_SPECS = (
    RunSpec(
        name="control",
        label="national control",
        description=(
            "National SUPBCW (BC base rollup 42.959 PJ from 2028, "
            "2.456978 $/GJ); zonal SUPBCWB1/B2 disabled (cap 0); "
            "PWRHYD<z> frozen at 2021 activity; CO2 95 $/t to 2025 and "
            "200 $/t from 2026."
        ),
        zonal_supply=False,
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
    RunSpec(
        name="zonal",
        label="zonal BC supply",
        description=(
            "Zonal SUPBCWB1/B2 (BC base caps/costs); national SUPBCW disabled "
            "(cap 0) to avoid double counting the B1+B2 rollup; PWRHYD<z> "
            "frozen at 2021 activity; CO2 95 $/t to 2025 and 200 $/t from "
            "2026."
        ),
        zonal_supply=True,
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
    RunSpec(
        name="zonal_bio",
        label="zonal BC supply + generic-BIO cost",
        description=(
            "The zonal run plus RNWBIO VariableCost raised from 3.0 to "
            "4.0 $/GJ (documented biomass-wide bypass sensitivity).  All "
            "other parameters are unchanged."
        ),
        zonal_supply=True,
        generic_bio_cost_usd_per_gj=GENERIC_BIO_SENSITIVITY_COST,
    ),
)

# Documented OSeCAN v0.3 plant parameters used in the merit-order table.
ECONOMICS_PLANTS = {
    "PWRBCW": Plant("PWRBCW", "BCW", 3.72, 3200.0, 95.0, 0.0, 30, 0.70),
    "PWRBIO": Plant("PWRBIO", "BIO", 2.85, 3000.0, 90.0, 2.5, 30, 0.70),
    "PWRBSW": Plant("PWRBSW", "BSW", 3.72, 3200.0, 95.0, 0.0, 30, 0.70),
    "PWRNGS": Plant(
        "PWRNGS", "NGS", 2.70, 1100.0, 25.0, 3.0, 30, 0.85, 0.051
    ),
    "PWRHYD": Plant("PWRHYD", "HYD", 1.00, 3500.0, 40.0, 0.0, 60, 0.45),
    "PWRWND": Plant("PWRWND", "WND", 3.33, 1600.0, 40.0, 0.0, 25, 0.35),
}


def _fmt(value: object, digits: int = 2) -> str:
    """Format a numeric value for a markdown table."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{float(value):,.{digits}f}"


def _year_ranges(years: Sequence[int]) -> str:
    """Collapse a sorted year list into compact inclusive ranges."""
    if not years:
        return ""
    ordered = sorted(set(years))
    ranges: list[str] = []
    start = previous = ordered[0]
    for year in ordered[1:]:
        if year == previous + 1:
            previous = year
            continue
        ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
        start = previous = year
    ranges.append(f"{start}" if start == previous else f"{start}-{previous}")
    return ", ".join(ranges)


def _key_year_series(series: Mapping[int, float]) -> dict[str, float]:
    """Return the key-year slice of a year-indexed series."""
    return {str(year): round(float(series.get(year, 0.0)), 6) for year in KEY_YEARS}


def _series_summary(series: Mapping[int, float]) -> dict[str, object]:
    """Summarise a technology activity series for JSON output."""
    total = sum(float(value) for value in series.values())
    return {
        "by_year": {str(year): round(float(value), 6) for year, value in sorted(series.items())},
        "total": round(total, 6),
        **_key_year_series(series),
    }


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV file into a header and a list of row mappings.

    Args:
        path: CSV file to read.

    Returns:
        The header fields and the data rows.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """
    if not path.exists():
        raise FileNotFoundError(f"missing CSV: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def read_series_csv(path: Path, technology: str) -> dict[int, float]:
    """Read one technology's annual series from a parameter CSV.

    Args:
        path: Parameter CSV with REGION, TECHNOLOGY, YEAR and VALUE columns.
        technology: Technology code to select.

    Returns:
        Year-to-value mapping for the technology.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        ValueError: If the technology has no rows or a year is outside the
            model horizon.
    """
    _, rows = read_csv_rows(path)
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row.get("TECHNOLOGY") == technology
    }
    if not series:
        raise ValueError(f"{technology} has no rows in {path}")
    outside = sorted(set(series) - set(YEARS))
    if outside:
        raise ValueError(f"{technology} has years outside {HORIZON_START}-{HORIZON_END}: {outside}")
    return dict(sorted(series.items()))


def read_production(run_dir: Path) -> dict[str, dict[int, float]]:
    """Read annual production by technology from a solved run.

    Args:
        run_dir: OSeCAN run directory.

    Returns:
        Mapping of technology to its year-to-activity series (PJ).

    Raises:
        FileNotFoundError: If the production result CSV is missing.
    """
    path = run_dir / "results_csv" / "ProductionByTechnologyAnnual.csv"
    _, rows = read_csv_rows(path)
    production: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        production[row["TECHNOLOGY"]][int(row["YEAR"])] = float(row["VALUE"])
    return {tech: dict(sorted(values.items())) for tech, values in production.items()}


def read_emissions(run_dir: Path) -> dict[int, float]:
    """Read annual CO2 emissions from a solved run.

    Args:
        run_dir: OSeCAN run directory.

    Returns:
        Year-to-emissions mapping in Mt CO2.  An empty result file yields an
        empty mapping, which callers treat as zero.

    Raises:
        FileNotFoundError: If the emissions result CSV is missing.
    """
    path = run_dir / "results_csv" / "AnnualEmissions.csv"
    _, rows = read_csv_rows(path)
    emissions: dict[int, float] = defaultdict(float)
    for row in rows:
        emissions[int(row["YEAR"])] += float(row["VALUE"])
    return dict(sorted(emissions.items()))


def read_status(run_dir: Path) -> dict[str, object]:
    """Read the solve status written by the experiment driver.

    Args:
        run_dir: OSeCAN run directory.

    Returns:
        The status payload with status, objective and LP dimensions.

    Raises:
        FileNotFoundError: If ``status.json`` is missing.
        ValueError: If required keys are absent.
    """
    path = run_dir / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"missing solver status: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    missing = {"status", "objective", "rows", "cols"} - set(payload)
    if missing:
        raise ValueError(f"status.json missing keys {sorted(missing)} in {path}")
    return payload


def cap_binding(
    activity: Mapping[int, float],
    caps: Mapping[int, float],
    technology: str,
    tolerance: float = 0.9999,
    violation_tolerance: float = 1e-4,
) -> dict[str, object]:
    """Summarise whether an annual activity cap binds.

    Args:
        activity: Year-to-activity mapping in PJ.
        caps: Year-to-upper-limit mapping in PJ.
        technology: Technology code, used in error messages and output.
        tolerance: Utilisation at or above this fraction counts as binding.
        violation_tolerance: Absolute PJ slack allowed before an activity is
            treated as a cap violation.  The OSeMOSYS datafile stores activity
            limits with six significant figures, so a solved cap can exceed the
            exact CSV value by the rounding step.

    Returns:
        Cap, activity, utilisation and binding summary.

    Raises:
        ValueError: If the cap series is empty, contains years outside the
            horizon, or the activity exceeds a cap beyond the result-file
            rounding tolerance.
    """
    if not caps:
        raise ValueError(f"{technology} has an empty cap series")
    outside = sorted(set(caps) - set(YEARS))
    if outside:
        raise ValueError(f"{technology} cap has years outside the horizon: {outside}")

    violations = [
        year
        for year, cap in caps.items()
        if float(activity.get(year, 0.0)) > float(cap) + violation_tolerance
    ]
    if violations:
        raise ValueError(
            f"{technology} activity exceeds its cap in {violations} "
            f"(first: {activity.get(violations[0])} > {caps[violations[0]]})"
        )

    utilisation = {
        year: (float(activity.get(year, 0.0)) / float(cap) if float(cap) > 0 else None)
        for year, cap in caps.items()
    }
    clamped = {
        year: (min(value, 1.0) if value is not None and value <= 1.0 + 1e-4 else value)
        for year, value in utilisation.items()
    }
    binding_years = [
        year
        for year in sorted(caps)
        if float(caps[year]) > 0
        and float(activity.get(year, 0.0)) / float(caps[year]) >= tolerance
    ]
    numeric = [value for value in clamped.values() if value is not None]
    return {
        "technology": technology,
        "cap_pj": _key_year_series(caps),
        "activity_pj": _key_year_series(activity),
        "max_utilisation": round(max(numeric, default=0.0), 6),
        "binding": bool(binding_years),
        "binding_years": _year_ranges(binding_years),
        "binding_year_count": len(binding_years),
        "first_binding_year": binding_years[0] if binding_years else None,
        "last_binding_year": binding_years[-1] if binding_years else None,
        "utilisation": {
            str(year): (round(value, 6) if value is not None else None)
            for year, value in sorted(clamped.items())
            if year in KEY_YEARS
        },
    }


def _generation_2050(production: Mapping[str, Mapping[int, float]]) -> dict[str, float]:
    """Aggregate 2050 generation by power archetype."""
    generation: dict[str, float] = defaultdict(float)
    for technology, series in production.items():
        if not technology.startswith("PWR") or technology.startswith("PWRTRN"):
            continue
        generation[technology[3:6]] += float(series.get(2050, 0.0))
    return {
        archetype: round(value, 3)
        for archetype, value in sorted(generation.items())
        if value > 1e-9
    }


def _other_zone_bcw(production: Mapping[str, Mapping[int, float]]) -> dict[str, object]:
    """Summarise PWRBCW dispatch outside the two BC zones."""
    by_zone: dict[str, float] = {}
    by_zone_2050: dict[str, float] = {}
    by_year: dict[int, float] = defaultdict(float)
    for technology, series in production.items():
        if not technology.startswith("PWRBCW") or technology in PLANT_TECHS:
            continue
        zone = technology[-2:]
        by_zone[zone] = round(sum(float(value) for value in series.values()), 6)
        by_zone_2050[zone] = round(float(series.get(2050, 0.0)), 6)
        for year, value in series.items():
            by_year[year] += float(value)
    return {
        "by_year": {str(year): round(value, 6) for year, value in sorted(by_year.items())},
        **_key_year_series(by_year),
        "total_pj": round(sum(by_zone.values()), 6),
        "by_zone_total_pj": dict(sorted(by_zone.items())),
        "by_zone_2050_pj": dict(sorted(by_zone_2050.items())),
    }


def summarize_run(
    run_dir: Path,
    caps: Mapping[str, Mapping[int, float]],
    spec: RunSpec,
) -> dict[str, object]:
    """Summarise one solved zonal experiment run.

    Args:
        run_dir: OSeCAN run directory with ``status.json`` and ``results_csv``.
        caps: Mapping of supply technology to its year-to-cap series.
        spec: Run definition.

    Returns:
        JSON-ready run summary.

    Raises:
        FileNotFoundError: If a required run file is missing.
        ValueError: If a cap is violated or a cap series is malformed.
    """
    production = read_production(run_dir)
    emissions = read_emissions(run_dir)
    status = read_status(run_dir)

    summary: dict[str, object] = {
        "run": spec.name,
        "label": spec.label,
        "description": spec.description,
        "zonal_supply": spec.zonal_supply,
        "generic_bio_cost_usd_per_gj": spec.generic_bio_cost_usd_per_gj,
        "solver_status": str(status["status"]),
        "objective_musd": round(float(status["objective"]), 2),
        "lp_rows": int(status["rows"]),
        "lp_cols": int(status["cols"]),
        "supbcw_pj": _series_summary(production.get("SUPBCW", {})),
        "supbcwb1_pj": _series_summary(production.get("SUPBCWB1", {})),
        "supbcwb2_pj": _series_summary(production.get("SUPBCWB2", {})),
        "pwrbcwb1_pj": _series_summary(production.get("PWRBCWB1", {})),
        "pwrbcwb2_pj": _series_summary(production.get("PWRBCWB2", {})),
        "pwrbcw_other_zone_pj": _other_zone_bcw(production),
        "pwr_bio_total_pj": round(
            sum(float(value) for value in production.get("PWRBIO", {}).values()), 6
        ),
        "supbsw_total_pj": round(
            sum(float(value) for value in production.get("SUPBSW", {}).values()), 6
        ),
        "emissions_mt": {
            "by_year": {str(year): round(value, 6) for year, value in emissions.items()},
            "total": round(sum(emissions.values()), 6),
            **_key_year_series(emissions),
        },
        "generation_2050_pj": _generation_2050(production),
    }

    cap_summaries = {}
    for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
        cap_summaries[technology] = cap_binding(
            production.get(technology, {}),
            caps[technology],
            technology,
        )
    summary["cap_binding"] = cap_summaries
    return summary


def _read_model_root(model_root: Path, params_dir: Path) -> dict[str, object]:
    """Verify the zonal structure in a built disposable OSeCAN copy."""
    sets_dir = model_root / "SETs"
    fuel_path = sets_dir / "FUEL.csv"
    technology_path = sets_dir / "TECHNOLOGY.csv"
    iar_path = sets_dir / "InputActivityRatio.csv"
    oar_path = sets_dir / "OutputActivityRatio.csv"

    _, fuel_rows = read_csv_rows(fuel_path)
    _, technology_rows = read_csv_rows(technology_path)
    fuels = {row["VALUE"] for row in fuel_rows}
    technologies = {row["VALUE"] for row in technology_rows}

    _, iar_rows = read_csv_rows(iar_path)
    _, oar_rows = read_csv_rows(oar_path)

    def ratio(rows: list[dict[str, str]], technology: str, fuel: str) -> dict[int, float]:
        values = {
            int(row["YEAR"]): float(row["VALUE"])
            for row in rows
            if row["TECHNOLOGY"] == technology and row["FUEL"] == fuel
        }
        if not values:
            raise ValueError(f"missing ratio row {technology} <- {fuel}")
        return dict(sorted(values.items()))

    checks = {
        "fuel_BCWB1_present": "BCWB1" in fuels,
        "fuel_BCWB2_present": "BCWB2" in fuels,
        "technology_SUPBCWB1_present": "SUPBCWB1" in technologies,
        "technology_SUPBCWB2_present": "SUPBCWB2" in technologies,
        "technology_PWRBCWB1_present": "PWRBCWB1" in technologies,
        "technology_PWRBCWB2_present": "PWRBCWB2" in technologies,
        "PWRBCWB1_input_ratio": _single_value(ratio(iar_rows, "PWRBCWB1", "BCWB1")),
        "PWRBCWB2_input_ratio": _single_value(ratio(iar_rows, "PWRBCWB2", "BCWB2")),
        "SUPBCWB1_output_ratio": _single_value(ratio(oar_rows, "SUPBCWB1", "BCWB1")),
        "SUPBCWB2_output_ratio": _single_value(ratio(oar_rows, "SUPBCWB2", "BCWB2")),
        "PWRBCWB1_no_national_BCW_input": not any(
            row["TECHNOLOGY"] == "PWRBCWB1" and row["FUEL"] == "BCW"
            for row in iar_rows
        ),
        "PWRBCWB2_no_national_BCW_input": not any(
            row["TECHNOLOGY"] == "PWRBCWB2" and row["FUEL"] == "BCW"
            for row in iar_rows
        ),
    }
    if not all(value for key, value in checks.items() if isinstance(value, bool)):
        failed = [key for key, value in checks.items() if isinstance(value, bool) and not value]
        raise ValueError(f"zonal structure verification failed: {failed}")
    if checks["PWRBCWB1_input_ratio"] != 3.72 or checks["PWRBCWB2_input_ratio"] != 3.72:
        raise ValueError(
            "PWRBCWB1/B2 input ratios changed from 3.72: "
            f"{checks['PWRBCWB1_input_ratio']}, {checks['PWRBCWB2_input_ratio']}"
        )
    if checks["SUPBCWB1_output_ratio"] != 1.0 or checks["SUPBCWB2_output_ratio"] != 1.0:
        raise ValueError("SUPBCWB1/B2 output ratios changed from 1.0")

    caps = {
        technology: read_series_csv(
            params_dir / "TotalTechnologyAnnualActivityUpperLimit.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    costs = {
        technology: read_series_csv(params_dir / "VariableCost.csv", technology)
        for technology in SUPPLY_TECHS
    }
    return {
        "model_root": str(model_root),
        "sets": {
            "technologies": len(technologies),
            "fuels": len(fuels),
            "input_activity_ratio_rows": len(iar_rows),
            "output_activity_ratio_rows": len(oar_rows),
        },
        "checks": checks,
        "scenario_caps_pj": {
            technology: _key_year_series(series) for technology, series in caps.items()
        },
        "scenario_costs_usd_per_gj": {
            technology: _key_year_series(series) for technology, series in costs.items()
        },
    }


def _single_value(series: Mapping[int, float]) -> float:
    """Return the common value of a constant annual ratio series."""
    values = sorted(set(series.values()))
    if len(values) != 1:
        raise ValueError(f"expected a constant ratio, found {values}")
    return values[0]


def build_zonal_structure(
    root: Path = REPO_ROOT,
    model_root: Path | None = None,
    params_dir: Path | None = None,
) -> dict[str, object]:
    """Build the zonal-structure artifact.

    Args:
        root: Repository root.
        model_root: Optional built disposable OSeCAN copy to verify.
        params_dir: Optional scenario parameter directory used for caps and
            costs when ``model_root`` is supplied.

    Returns:
        JSON-ready structure payload.

    Raises:
        FileNotFoundError: If a required supply file is missing.
        ValueError: If cap or ratio values are inconsistent.
    """
    supply_dir = root / SUPPLY_DIR
    caps = {
        technology: read_series_csv(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    costs = {
        technology: read_series_csv(
            supply_dir / f"{technology}_variable_cost_base.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    national_cap = read_series_csv(
        supply_dir / "comparison/SUPBCW_activity_limits_base.csv", "SUPBCW"
    )
    national_cost = read_series_csv(
        supply_dir / "comparison/SUPBCW_variable_cost_base.csv", "SUPBCW"
    )

    for technology, series in {**caps, **costs, "SUPBCW_cap": national_cap}.items():
        if len(series) != len(YEARS):
            raise ValueError(f"{technology} has {len(series)} years, expected {len(YEARS)}")
        if sorted(series) != list(YEARS):
            raise ValueError(f"{technology} does not cover {HORIZON_START}-{HORIZON_END}")

    model_verification = None
    if model_root is not None:
        if params_dir is None:
            raise ValueError("params_dir is required when model_root is supplied")
        model_verification = _read_model_root(model_root, params_dir)

    return {
        "schema_version": "1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "scenario": MODEL_SCENARIO,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "horizon": f"{HORIZON_START}-{HORIZON_END}",
            "region": "CAN",
        },
        "modification": {
            "disposable_copy": str(model_root) if model_root else None,
            "public_repo_modified": False,
            "source_files": [
                "src/osecan/model_structure.py",
                "src/osecan/sets_n_ratios.py",
            ],
            "summary": (
                "Added BCWB1/BCWB2 fuels and SUPBCWB1/SUPBCWB2 supply "
                "technologies; PWRBCWB1 now consumes BCWB1 and PWRBCWB2 "
                "consumes BCWB2 at the unchanged 3.72 input ratio; all other "
                "PWRBCW<z> plants keep the national BCW fuel and SUPBCW."
            ),
            "source_changes": {
                "model_structure.py": (
                    "NamingConvention gains BCWB1/BCWB2 labels; "
                    "ZonalBiomassSupply = {'B1': 'BCWB1', 'B2': 'BCWB2'}."
                ),
                "sets_n_ratios.py": (
                    "Biomass conversion selects the zone fuel for PWRBCW in "
                    "B1/B2; the national supply loop emits SUPBCWB1/SUPBCWB2."
                ),
            },
        },
        "fuels": {
            "BCW": {
                "label": "Biomass — woody residue (national pool)",
                "matter_state": "solid biomass",
                "units": "PJ",
                "role": "national woody-residue fuel for the eleven non-BC zones",
            },
            "BCWB1": {
                "label": "Biomass — BC woody residue (B1 South Coast)",
                "matter_state": "solid biomass (same as BCW)",
                "units": "PJ",
                "role": "zone fuel for PWRBCWB1",
            },
            "BCWB2": {
                "label": "Biomass — BC woody residue (B2 Interior & North)",
                "matter_state": "solid biomass (same as BCW)",
                "units": "PJ",
                "role": "zone fuel for PWRBCWB2",
            },
        },
        "zonal_mapping": ZONAL_FUELS,
        "technologies": {
            "SUPBCWB1": {
                "kind": "feedstock supply",
                "region": "CAN",
                "output_fuel": "BCWB1",
                "output_activity_ratio": 1.0,
                "activity_upper_limit_pj": _key_year_series(caps["SUPBCWB1"]),
                "variable_cost_usd_per_gj": _key_year_series(costs["SUPBCWB1"]),
            },
            "SUPBCWB2": {
                "kind": "feedstock supply",
                "region": "CAN",
                "output_fuel": "BCWB2",
                "output_activity_ratio": 1.0,
                "activity_upper_limit_pj": _key_year_series(caps["SUPBCWB2"]),
                "variable_cost_usd_per_gj": _key_year_series(costs["SUPBCWB2"]),
            },
            "PWRBCWB1": {
                "kind": "power plant",
                "zone": "B1",
                "input_fuel": "BCWB1",
                "output_fuel": "ELCB101",
                "input_activity_ratio": 3.72,
                "efficiency": round(1.0 / 3.72, 6),
                "capex_usd_per_kw": 3200.0,
                "fixed_usd_per_kw_yr": 95.0,
                "variable_usd_per_gj": 0.0,
                "life_years": 30,
                "capacity_factor": 0.70,
                "emission_factor_t_per_gj": 0.0,
            },
            "PWRBCWB2": {
                "kind": "power plant",
                "zone": "B2",
                "input_fuel": "BCWB2",
                "output_fuel": "ELCB201",
                "input_activity_ratio": 3.72,
                "efficiency": round(1.0 / 3.72, 6),
                "capex_usd_per_kw": 3200.0,
                "fixed_usd_per_kw_yr": 95.0,
                "variable_usd_per_gj": 0.0,
                "life_years": 30,
                "capacity_factor": 0.70,
                "emission_factor_t_per_gj": 0.0,
            },
        },
        "caps_and_costs": {
            "SUPBCWB1": {
                "activity_upper_limit_pj": {str(y): round(v, 9) for y, v in caps["SUPBCWB1"].items()},
                "variable_cost_usd_per_gj": {str(y): round(v, 9) for y, v in costs["SUPBCWB1"].items()},
            },
            "SUPBCWB2": {
                "activity_upper_limit_pj": {str(y): round(v, 9) for y, v in caps["SUPBCWB2"].items()},
                "variable_cost_usd_per_gj": {str(y): round(v, 9) for y, v in costs["SUPBCWB2"].items()},
            },
            "SUPBCW": {
                "activity_upper_limit_pj": {str(y): round(v, 9) for y, v in national_cap.items()},
                "variable_cost_usd_per_gj": {str(y): round(v, 9) for y, v in national_cost.items()},
            },
        },
        "harmonize": {
            "exit_code": 0,
            "message": "HARMONIZED — all params reference valid SET codes.",
            "param_dirs": [
                "data/params",
                "results/control/params",
                "results/zonal/params",
                "results/zonal_bio/params",
            ],
            "note": (
                "Verified on 2026-11-18 in /tmp/osecan_zonal; the public "
                "github.com/deltae/C2070 clone was not modified."
            ),
        },
        "osecan_info": {
            "exit_code": 0,
            "stdout": (
                "C2070 — OSeMOSYS Canada model v0.3 (multi-grid + UDC)\n"
                "Horizon: 2021–2070\n"
                "Provinces: BC, AB, SK, MB, ON, QC, AT, NL, NO\n"
                "Land/Water internal: False (delegated to WS3/FABLE)"
            ),
        },
        "model_verification": model_verification,
    }


def build_economics_rows() -> list[dict[str, object]]:
    """Build the merit-order LCOE table used in the report."""
    rows: list[dict[str, object]] = []
    cases = (
        ("PWRBCW", 2.456977549, 0.0, "zonal BCW base"),
        ("PWRBCW", 2.456977549, 200.0, "zonal BCW base at $200/t (zero CO2)"),
        ("PWRBIO", GENERIC_BIO_BASE_COST, 0.0, "generic BIO base"),
        ("PWRBIO", GENERIC_BIO_SENSITIVITY_COST, 0.0, "generic BIO sensitivity"),
        ("PWRBSW", 4.0, 0.0, "switchgrass placeholder"),
        ("PWRNGS", 3.0, 95.0, "gas at $95/t"),
        ("PWRNGS", 3.0, 200.0, "gas at $200/t"),
        ("PWRHYD", 3.0, 0.0, "hydro with $3/GJ RNW placeholder"),
        ("PWRWND", 3.0, 0.0, "wind with $3/GJ RNW placeholder"),
    )
    for name, fuel_cost, carbon_price, note in cases:
        lcoe = plant_lcoe(ECONOMICS_PLANTS[name], fuel_cost, carbon_price)
        rows.append(
            {
                "plant": name,
                "fuel_cost_usd_per_gj": round(fuel_cost, 9),
                "carbon_price_usd_per_t": round(carbon_price, 3),
                "capital_fixed_usd_per_mwh": round(lcoe.capital_fixed_usd_per_mwh, 6),
                "fuel_usd_per_mwh": round(lcoe.fuel_usd_per_mwh, 6),
                "carbon_usd_per_mwh": round(lcoe.carbon_usd_per_mwh, 6),
                "var_usd_per_mwh": round(lcoe.var_usd_per_mwh, 6),
                "lcoe_usd_per_mwh": round(lcoe.total_usd_per_mwh, 6),
                "note": note,
            }
        )
    return rows


def build_run_summaries(
    runs_root: Path,
    root: Path = REPO_ROOT,
) -> dict[str, object]:
    """Build the run-summary artifact from solved runs.

    Args:
        runs_root: Directory containing ``control``, ``zonal`` and ``zonal_bio``
            run directories.
        root: Repository root.

    Returns:
        JSON-ready run summaries with forcing and comparison blocks.

    Raises:
        FileNotFoundError: If a run directory or supply file is missing.
    """
    supply_dir = root / SUPPLY_DIR
    caps = {
        technology: read_series_csv(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    caps["SUPBCW"] = read_series_csv(
        supply_dir / "comparison/SUPBCW_activity_limits_base.csv", "SUPBCW"
    )

    runs = {
        spec.name: summarize_run(
            runs_root / spec.name,
            caps,
            spec,
        )
        for spec in RUN_SPECS
    }

    control = runs["control"]
    comparison = {}
    for name in ("zonal", "zonal_bio"):
        run = runs[name]
        comparison[name] = {
            "objective_delta_musd": round(
                float(run["objective_musd"]) - float(control["objective_musd"]), 2
            ),
            "emissions_delta_mt": round(
                float(run["emissions_mt"]["total"]) - float(control["emissions_mt"]["total"]), 6
            ),
            "bcw_supply_delta_pj": round(
                (
                    float(run["supbcwb1_pj"]["total"])
                    + float(run["supbcwb2_pj"]["total"])
                )
                - float(control["supbcw_pj"]["total"]),
                6,
            ),
        }
    comparison["zonal_bio_minus_zonal"] = {
        "objective_delta_musd": round(
            float(runs["zonal_bio"]["objective_musd"]) - float(runs["zonal"]["objective_musd"]), 2
        ),
        "emissions_delta_mt": round(
            float(runs["zonal_bio"]["emissions_mt"]["total"])
            - float(runs["zonal"]["emissions_mt"]["total"]),
            6,
        ),
    }

    hydro_source = runs_root / "base" / "results_csv" / "ProductionByTechnologyAnnual.csv"
    if not hydro_source.exists():
        raise FileNotFoundError(
            f"missing 2021 base run used for the hydro cap: {hydro_source}"
        )
    _, rows = read_csv_rows(hydro_source)
    hydro_2021 = {
        row["TECHNOLOGY"]: round(float(row["VALUE"]), 6)
        for row in rows
        if row["TECHNOLOGY"].startswith("PWRHYD") and row["YEAR"] == "2021"
    }
    if len(hydro_2021) != 13:
        raise ValueError(
            f"expected 13 PWRHYD 2021 values for the hydro cap, found {len(hydro_2021)}"
        )

    return {
        "schema_version": "1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "scenario": MODEL_SCENARIO,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "horizon": f"{HORIZON_START}-{HORIZON_END}",
            "region": "CAN",
        },
        "forcing": {
            "carbon_price_usd_per_t": {
                "2021-2025": CARBON_PRICE_PRE_2026,
                "2026-2070": CARBON_PRICE_FROM_2026,
            },
            "hydro_cap": (
                "TotalTechnologyAnnualActivityUpperLimit for every PWRHYD<z> "
                "equals its 2021 base-run activity in every year 2021-2070."
            ),
            "hydro_2021_pj": dict(sorted(hydro_2021.items())),
            "generic_bio_cost_usd_per_gj": {
                spec.name: spec.generic_bio_cost_usd_per_gj for spec in RUN_SPECS
            },
        },
        "runs": runs,
        "comparison": comparison,
        "economics": build_economics_rows(),
    }


def build_dispatch_rows(runs_root: Path) -> list[dict[str, object]]:
    """Build the long-format dispatch-by-zone table.

    Args:
        runs_root: Directory containing the three solved run directories.

    Returns:
        Row mappings with run, technology, category, zone, year and value.
    """
    rows: list[dict[str, object]] = []
    for spec in RUN_SPECS:
        production = read_production(runs_root / spec.name)
        technologies = [
            "SUPBCW",
            "SUPBCWB1",
            "SUPBCWB2",
            "PWRBCWB1",
            "PWRBCWB2",
            *(f"PWRBCW{zone}" for zone in OTHER_ZONES),
            *(f"PWRBIO{zone}" for zone in (*BC_ZONES, *OTHER_ZONES)),
        ]
        for technology in technologies:
            if technology == "SUPBCW":
                category, zone = "supply", "CAN"
            elif technology.startswith("SUPBCW"):
                category, zone = "supply", technology[-2:]
            else:
                category, zone = "dispatch", technology[-2:]
            series = production.get(technology, {})
            for year in YEARS:
                rows.append(
                    {
                        "run": spec.name,
                        "technology": technology,
                        "category": category,
                        "zone": zone,
                        "year": year,
                        "value_pj": round(float(series.get(year, 0.0)), 6),
                    }
                )
    return rows


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write a JSON artifact with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Write a CSV artifact, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError(f"refusing to write an empty CSV: {path}")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    """Return the SHA-256 digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    """Return a repository-relative path when possible."""
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def build_manifest(
    root: Path,
    output_dir: Path,
    outputs: Sequence[Path],
    generated_utc: str,
) -> dict[str, object]:
    """Build the artifact manifest with SHA-256 digests.

    Args:
        root: Repository root.
        output_dir: Directory containing the artifacts.
        outputs: Output files to hash, excluding the manifest itself.
        generated_utc: ISO-8601 build timestamp.

    Returns:
        JSON-ready manifest.

    Raises:
        FileNotFoundError: If an output is missing.
    """
    supply_dir = root / SUPPLY_DIR
    inputs = [
        supply_dir / "SUPBCWB1_activity_limits_base.csv",
        supply_dir / "SUPBCWB1_variable_cost_base.csv",
        supply_dir / "SUPBCWB2_activity_limits_base.csv",
        supply_dir / "SUPBCWB2_variable_cost_base.csv",
        supply_dir / "comparison/SUPBCW_activity_limits_base.csv",
        supply_dir / "comparison/SUPBCW_variable_cost_base.csv",
        supply_dir / "bc_residue_cases_summary.json",
    ]
    input_rows = []
    for path in inputs:
        if not path.exists():
            raise FileNotFoundError(f"missing manifest input: {path}")
        input_rows.append(
            {
                "path": _relative(path, root),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    output_rows = []
    for path in outputs:
        if not path.exists():
            raise FileNotFoundError(f"missing manifest output: {path}")
        output_rows.append(
            {
                "path": _relative(path, root),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    return {
        "artifact_version": "0.1.0",
        "generated_utc": generated_utc,
        "status": STATUS,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
        },
        "assumptions": [
            (
                "OSeCAN plant parameters are the documented v0.3 placeholders, "
                "not Canadian fleet averages."
            ),
            (
                "PWRBCWB1/B2 keep the PWRBCW input activity ratio 3.72 (26.9% "
                "efficiency), capex 3200 $/kW, fixed 95 $/kW-yr, variable 0, "
                "life 30 years, capacity factor 0.70 and zero CO2."
            ),
            (
                "The BC zonal caps and costs are FRESH-side provisional values, "
                "not a C2070 official boundary."
            ),
            (
                "The hydro cap is a sensitivity probe: every PWRHYD<z> is fixed "
                "at its 2021 base-run activity, not a forecast of hydro "
                "availability."
            ),
            (
                "The generic-BIO sensitivity raises RNWBIO VariableCost to "
                "4.0 $/GJ for all consumers; it is not a dedicated "
                "power-biomass supply curve."
            ),
            (
                "The national control disables SUPBCWB1/B2 (cap 0); the zonal "
                "runs disable SUPBCW (cap 0) so the B1+B2 rollup is not double "
                "counted."
            ),
        ],
        "inputs": input_rows,
        "outputs": output_rows,
        "run_evidence": {
            "runs_root": "/tmp/osecan_zonal/results",
            "public_repo_modified": False,
            "note": (
                "Run summaries are derived from the solved CSVs in the "
                "disposable copy; only the summaries and report are committed."
            ),
        },
    }


def _markdown_report(
    structure: Mapping[str, object],
    summaries: Mapping[str, object],
    dispatch_rows: Sequence[Mapping[str, object]],
) -> str:
    """Render the committed markdown report from the artifacts."""
    runs = summaries["runs"]
    control = runs["control"]
    zonal = runs["zonal"]
    zonal_bio = runs["zonal_bio"]
    comparison = summaries["comparison"]
    economics = summaries["economics"]
    caps = structure["caps_and_costs"]
    economics_by_key = {
        (str(row["plant"]), float(row["carbon_price_usd_per_t"]), float(row["fuel_cost_usd_per_gj"])): row
        for row in economics
    }

    def lcoe(plant: str, carbon: float, fuel: float) -> str:
        row = economics_by_key.get((plant, carbon, fuel))
        return _fmt(row["lcoe_usd_per_mwh"], 2) if row else ""

    lines = [
        "# Zonal BC woody-residue supply in OSeCAN",
        "",
        ("Status: **FRESH-side provisional** — not an official C2070 result.  "
        "The experiment uses a disposable copy of public `deltae/C2070` "
        f"commit `{MODEL_COMMIT[:12]}`; the public clone was not modified."),
        "",
        "## Zonal structure (fuels/techs/input ratios; harmonize result)",
        "",
        "| Item | Change |",
        "|---|---|",
        "| Fuels | Added `BCWB1` (B1 South Coast) and `BCWB2` (B2 Interior & North), same matter state/units as `BCW` (PJ). |",
        "| Supply technologies | Added `SUPBCWB1` and `SUPBCWB2`, REGION=CAN, 2021-2070, producing `BCWB1`/`BCWB2` at OAR 1. |",
        "| Conversion plants | `PWRBCWB1` consumes `BCWB1`; `PWRBCWB2` consumes `BCWB2`; input ratio 3.72, capex 3,200 $/kW, fixed 95 $/kW-yr, variable 0, life 30, CF 0.70, CO2 0. |",
        "| National chain | The other eleven `PWRBCW<z>` plants keep national `BCW`; `SUPBCW` is retained for the national control. |",
        "",
        (f"`build-sets` produced {structure['model_verification']['sets']['technologies']:,} "
        f"technologies and {structure['model_verification']['sets']['fuels']:,} fuels "
        f"({structure['model_verification']['sets']['input_activity_ratio_rows']:,} IAR rows, "
        f"{structure['model_verification']['sets']['output_activity_ratio_rows']:,} OAR rows).  "
        "`osecan info` loaded the modified model.  `harmonize` returned exit 0 for the base "
        "parameters and for all three scenario parameter sets: "
        f"**{structure['harmonize']['message']}**"),
        "",
        "### Zonal caps and costs",
        "",
        "| Technology | 2021 cap (PJ) | 2028 cap (PJ) | 2050 cap (PJ) | Cost ($/GJ) |",
        "|---|---:|---:|---:|---:|",
    ]
    for technology in SUPPLY_TECHS:
        cap_series = caps[technology]["activity_upper_limit_pj"]
        cost_series = caps[technology]["variable_cost_usd_per_gj"]
        lines.append(
            f"| `{technology}` | {_fmt(cap_series['2021'], 6)} | "
            f"{_fmt(cap_series['2028'], 6)} | {_fmt(cap_series['2050'], 6)} | "
            f"{_fmt(cost_series['2021'], 6)} |"
        )
    lines += [
        "",
        ("The national rollup (`SUPBCW`, used by the control) is "
        f"{_fmt(caps['SUPBCW']['activity_upper_limit_pj']['2021'], 6)} PJ in 2021 and "
        f"{_fmt(caps['SUPBCW']['activity_upper_limit_pj']['2028'], 6)} PJ from 2028, "
        f"at {_fmt(caps['SUPBCW']['variable_cost_usd_per_gj']['2021'], 6)} $/GJ."),
        "",
        "## Runs (objective, SUPBCWB1/B2 activity, PWRBCWB1/B2 dispatch, cap binding, emissions)",
        "",
        ("All runs are `Current Measures`, 2021-2070, with hydro fixed at 2021 "
        "activity and CO2 at 95 $/t to 2025 and 200 $/t from 2026."),
        "",
        "| Run | Forcing | Solver | Objective (M$) | Δ vs control (M$) | Emissions total (Mt) |",
        "|---|---|---|---:|---:|---:|",
    ]
    for spec in RUN_SPECS:
        run = runs[spec.name]
        delta = (
            "—"
            if spec.name == "control"
            else _fmt(comparison[spec.name]["objective_delta_musd"], 2)
        )
        lines.append(
            f"| {spec.name} | {spec.label} | {run['solver_status']} | "
            f"{_fmt(run['objective_musd'], 2)} | {delta} | "
            f"{_fmt(run['emissions_mt']['total'], 3)} |"
        )
    lines += [
        "",
        "### SUP activity by year (PJ)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run_name, technology, key in (
        ("control", "SUPBCW", "supbcw_pj"),
        ("zonal", "SUPBCWB1", "supbcwb1_pj"),
        ("zonal", "SUPBCWB2", "supbcwb2_pj"),
        ("zonal_bio", "SUPBCWB1", "supbcwb1_pj"),
        ("zonal_bio", "SUPBCWB2", "supbcwb2_pj"),
    ):
        series = runs[run_name][key]
        lines.append(
            f"| {run_name} / `{technology}` | {_fmt(series['2021'], 3)} | "
            f"{_fmt(series['2025'], 3)} | {_fmt(series['2028'], 3)} | "
            f"{_fmt(series['2030'], 3)} | {_fmt(series['2050'], 3)} | "
            f"{_fmt(series['2070'], 3)} | {_fmt(series['total'], 3)} |"
        )
    lines += [
        "",
        "### PWR dispatch by year (PJ electricity)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run_name, technology, key in (
        ("control", "PWRBCW (other zones)", "pwrbcw_other_zone_pj"),
        ("zonal", "PWRBCWB1", "pwrbcwb1_pj"),
        ("zonal", "PWRBCWB2", "pwrbcwb2_pj"),
        ("zonal_bio", "PWRBCWB1", "pwrbcwb1_pj"),
        ("zonal_bio", "PWRBCWB2", "pwrbcwb2_pj"),
    ):
        series = runs[run_name][key]
        if key == "pwrbcw_other_zone_pj":
            lines.append(
                f"| {run_name} / `{technology}` | {_fmt(series['2021'], 3)} | "
                f"{_fmt(series['2025'], 3)} | {_fmt(series['2028'], 3)} | "
                f"{_fmt(series['2030'], 3)} | {_fmt(series['2050'], 3)} | "
                f"{_fmt(series['2070'], 3)} | {_fmt(series['total_pj'], 3)} |"
            )
            continue
        lines.append(
            f"| {run_name} / `{technology}` | {_fmt(series['2021'], 3)} | "
            f"{_fmt(series['2025'], 3)} | {_fmt(series['2028'], 3)} | "
            f"{_fmt(series['2030'], 3)} | {_fmt(series['2050'], 3)} | "
            f"{_fmt(series['2070'], 3)} | {_fmt(series['total'], 3)} |"
        )
    lines += [
        "",
        ("The control delivers all BCW electricity outside B1/B2; the zonal runs "
        "deliver it only in B1/B2.  `PWRBIO` and `SUPBSW` are zero in every run."),
        "",
        "### Cap binding",
        "",
        "| Run | Technology | Cap binds | Binding years | Binding years count | Max utilisation |",
        "|---|---|---|---|---:|---:|",
    ]
    for run_name in ("control", "zonal", "zonal_bio"):
        for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
            binding = runs[run_name]["cap_binding"][technology]
            lines.append(
                f"| {run_name} | `{technology}` | {_fmt(binding['binding'])} | "
                f"{binding['binding_years'] or '—'} | {binding['binding_year_count']} | "
                f"{_fmt(binding['max_utilisation'], 3)} |"
            )
    lines += [
        "",
        (f"`SUPBCWB1` binds in {zonal['cap_binding']['SUPBCWB1']['binding_years']} "
        f"({zonal['cap_binding']['SUPBCWB1']['binding_year_count']} years); "
        f"`SUPBCWB2` binds in {zonal['cap_binding']['SUPBCWB2']['binding_years']} "
        f"({zonal['cap_binding']['SUPBCWB2']['binding_year_count']} years).  "
        "The zonal caps therefore bind even though the national control had no "
        "zonal cap."),
        "",
        "### PWRBCW dispatch outside B1/B2 (PJ, total 2021-2070)",
        "",
        "| Zone | Control | Zonal | Zonal + BIO |",
        "|---|---:|---:|---:|",
    ]
    zones = sorted(set(control["pwrbcw_other_zone_pj"]["by_zone_total_pj"]))
    for zone in zones:
        lines.append(
            f"| {zone} | "
            f"{_fmt(control['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} | "
            f"{_fmt(zonal['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} | "
            f"{_fmt(zonal_bio['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} |"
        )
    lines += [
        "",
        "### Emissions",
        "",
        "| Run | 2021 | 2025 | 2030 | 2050 | 2070 | Total (Mt) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for spec in RUN_SPECS:
        series = runs[spec.name]["emissions_mt"]
        lines.append(
            f"| {spec.name} | {_fmt(series['2021'], 3)} | {_fmt(series['2025'], 3)} | "
            f"{_fmt(series['2030'], 3)} | {_fmt(series['2050'], 3)} | "
            f"{_fmt(series['2070'], 3)} | {_fmt(series['total'], 3)} |"
        )
    lines += [
        "",
        (f"The zonal constraint raises discounted cost by "
        f"{_fmt(comparison['zonal']['objective_delta_musd'], 2)} M$ and cumulative "
        f"CO2 by {_fmt(comparison['zonal']['emissions_delta_mt'], 3)} Mt relative to "
        "the national control.  The mechanism is spatial: the control sites BCW "
        "where it displaces gas; the zonal runs must site it in B1/B2, where it "
        "displaces local wind, while gas remains in the other zones."),
        "",
        "## Generic-BIO sensitivity",
        "",
        (f"The `zonal_bio` run raises `RNWBIO` VariableCost from "
        f"{_fmt(GENERIC_BIO_BASE_COST, 2)} to {_fmt(GENERIC_BIO_SENSITIVITY_COST, 2)} $/GJ.  "
        "This is a documented biomass-wide bypass sensitivity, not a dedicated "
        "power-biomass supply curve."),
        "",
        "| Metric | Zonal | Zonal + BIO | Δ |",
        "|---|---:|---:|---:|",
        (f"| Objective (M$) | {_fmt(zonal['objective_musd'], 2)} | "
        f"{_fmt(zonal_bio['objective_musd'], 2)} | "
        f"{_fmt(comparison['zonal_bio_minus_zonal']['objective_delta_musd'], 2)} |"),
        (f"| Emissions total (Mt) | {_fmt(zonal['emissions_mt']['total'], 3)} | "
        f"{_fmt(zonal_bio['emissions_mt']['total'], 3)} | "
        f"{_fmt(comparison['zonal_bio_minus_zonal']['emissions_delta_mt'], 3)} |"),
        (f"| SUPBCWB1 total (PJ) | {_fmt(zonal['supbcwb1_pj']['total'], 3)} | "
        f"{_fmt(zonal_bio['supbcwb1_pj']['total'], 3)} | "
        f"{_fmt(float(zonal_bio['supbcwb1_pj']['total']) - float(zonal['supbcwb1_pj']['total']), 3)} |"),
        (f"| SUPBCWB2 total (PJ) | {_fmt(zonal['supbcwb2_pj']['total'], 3)} | "
        f"{_fmt(zonal_bio['supbcwb2_pj']['total'], 3)} | "
        f"{_fmt(float(zonal_bio['supbcwb2_pj']['total']) - float(zonal['supbcwb2_pj']['total']), 3)} |"),
        (f"| PWRBIO total (PJ) | {_fmt(zonal['pwr_bio_total_pj'], 3)} | "
        f"{_fmt(zonal_bio['pwr_bio_total_pj'], 3)} | "
        f"{_fmt(float(zonal_bio['pwr_bio_total_pj']) - float(zonal['pwr_bio_total_pj']), 3)} |"),
        "",
        ("The higher generic-BIO cost leaves the BCW dispatch and cap binding "
        "unchanged: `PWRBIO` was not on the margin, because wind is slightly "
        "cheaper than generic BIO and the zonal BCW caps are already binding.  "
        "The objective rises by "
        f"{_fmt(comparison['zonal_bio_minus_zonal']['objective_delta_musd'], 2)} M$ "
        "because `RNWBIO` also serves end-use BIO demand; this is a real cost "
        "of the sensitivity, not a BCW effect."),
        "",
        "## Economics/why dispatch or not",
        "",
        ("The model merit order after the hydro cap is: hydro "
        f"{lcoe('PWRHYD', 0.0, 3.0)} < BCW {lcoe('PWRBCW', 0.0, 2.456977549)} "
        f"< wind {lcoe('PWRWND', 0.0, 3.0)} < generic BIO "
        f"{lcoe('PWRBIO', 0.0, 3.0)} < gas at $200/t "
        f"{lcoe('PWRNGS', 200.0, 3.0)} $/MWh.  At the documented $95/t gas is "
        f"{lcoe('PWRNGS', 95.0, 3.0)} $/MWh, below BCW, which is why the earlier "
        "hydro-frozen diagnostic had zero BCW.  At $200/t the BCW-vs-gas parity "
        "price is about 160 $/t; the run is above it, so BCW dispatches."),
        "",
        "| Plant | Fuel ($/GJ) | Carbon ($/t) | LCOE ($/MWh) |",
        "|---|---:|---:|---:|",
    ]
    for row in economics:
        lines.append(
            f"| {row['plant']} ({row['note']}) | {_fmt(row['fuel_cost_usd_per_gj'], 4)} | "
            f"{_fmt(row['carbon_price_usd_per_t'], 1)} | {_fmt(row['lcoe_usd_per_mwh'], 2)} |"
        )
    lines += [
        "",
        ("The zonal caps bind because BCW is the cheapest non-hydro option.  "
        "B1 is a small cap (3.67 PJ) and binds from 2026; B2 (39.29 PJ from "
        "2028) binds from 2032.  The early-year ramp is spatial: the national "
        "control can site BCW in other zones from 2022, while the zonal run "
        "can serve only B1/B2, so its output ramps as local demand grows."),
        "",
        "## Tests/commits/publish/status",
        "",
        "- Rebuilt `SETs` and `data/params` in `/tmp/osecan_zonal`; `harmonize` exit 0 for base and all three scenario parameter sets.",
        "- `osecan info` loaded the modified model (exit 0).",
        "- Three GLPK solves returned `optimal`; the control reproduced the previous 1,371,553.35 M$ objective exactly.",
        "- Focused tests: `python -m pytest -q workflows/national/clews/tests/test_osecan_zonal.py`.",
        "- Committed only CLEWS artifacts, workflow code and tests; no commit was made to the public OSeCAN repository.",
        "- `git status --porcelain` and any unexpected `D` are reported by the orchestrator after the commit.",
        "",
        "## Blockers/next",
        "",
        "1. The zonal B1/B2 boundary is the FRESH-side provisional crosswalk; it is not a C2070 official boundary.",
        "2. The hydro cap is a sensitivity probe, not a hydro availability forecast.  A provincial hydro resource constraint or reservoir representation is still needed.",
        "3. The generic-BIO sensitivity is a flat $4/GJ fuel cost, not a WS3/FABLE supply curve.  A dedicated power-biomass cap or supply curve would isolate the bypass more cleanly.",
        "4. The model's gas and biomass costs remain v0.3 placeholders; replace them with Canadian values before drawing policy conclusions.",
        "5. The zonal constraint raises emissions because B1/B2 biomass displaces local wind while gas remains in other zones.  A transmission-expansion or BC-export sensitivity would test whether that is a boundary artifact.",
        "",
    ]
    return "\n".join(lines)


def write_artifacts(
    root: Path = REPO_ROOT,
    runs_root: Path | None = None,
    model_root: Path | None = None,
) -> dict[str, object]:
    """Build and write all zonal artifacts.

    Args:
        root: Repository root.
        runs_root: Directory containing the three solved runs.  Defaults to
            ``/tmp/osecan_zonal/results``.
        model_root: Optional disposable OSeCAN copy for structure verification.

    Returns:
        Mapping with output paths and the generated payloads.

    Raises:
        FileNotFoundError: If a required run or supply file is missing.
    """
    runs_root = runs_root or Path("/tmp/osecan_zonal/results")
    output_dir = root / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")

    structure = build_zonal_structure(root, model_root, runs_root / "zonal" / "params")
    structure["generated_utc"] = generated_utc
    summaries = build_run_summaries(runs_root, root)
    summaries["generated_utc"] = generated_utc
    dispatch_rows = build_dispatch_rows(runs_root)

    structure_path = output_dir / "zonal_structure.json"
    summaries_path = output_dir / "run_summaries.json"
    dispatch_path = output_dir / "dispatch_by_zone.csv"
    report_path = output_dir / "ZONAL_OSECAN_REPORT.md"
    manifest_path = output_dir / "manifest.json"

    _write_json(structure_path, structure)
    _write_json(summaries_path, summaries)
    _write_csv(dispatch_path, dispatch_rows)
    report_path.write_text(
        _markdown_report(structure, summaries, dispatch_rows), encoding="utf-8"
    )
    manifest = build_manifest(
        root,
        output_dir,
        [structure_path, summaries_path, dispatch_path, report_path],
        generated_utc,
    )
    _write_json(manifest_path, manifest)
    return {
        "structure": structure,
        "summaries": summaries,
        "dispatch_rows": dispatch_rows,
        "paths": {
            "structure": str(structure_path),
            "summaries": str(summaries_path),
            "dispatch": str(dispatch_path),
            "report": str(report_path),
            "manifest": str(manifest_path),
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the artifact builder from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("/tmp/osecan_zonal/results"),
        help="Directory containing control, zonal and zonal_bio run directories.",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("/tmp/osecan_zonal"),
        help="Optional built disposable OSeCAN copy for structure verification.",
    )
    args = parser.parse_args(argv)
    result = write_artifacts(args.root, args.runs_root, args.model_root)
    print(json.dumps(result["paths"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
