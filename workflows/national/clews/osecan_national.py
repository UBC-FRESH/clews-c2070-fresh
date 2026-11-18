"""National RSYC-only 150M m3 log/yr supply experiment for OSeCAN.

The experiment wires a national woody-residue cap into OSeCAN without double
counting British Columbia:

* national supply is 150 million m3 of log volume per year, converted to PJ
  through the fixed RSYC tile-production outputs;
* the BC zones B1/B2 keep their existing ``SUPBCWB1``/``SUPBCWB2`` caps and are
  served by the zonal fuels added in the earlier zonal experiment;
* ``SUPBCW`` carries only the rest-of-Canada balance, so
  ``SUPBCW + SUPBCWB1 + SUPBCWB2`` never exceeds the national total.

The module writes the parameter CSVs, builds scenario parameter directories in a
disposable OSeCAN copy, and renders the committed summaries.  It never edits the
public OSeCAN repository.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import subprocess
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from workflows.national.clews.osecan_zonal import (
    BC_ZONES,
    HORIZON_END,
    HORIZON_START,
    OTHER_ZONES,
    SUPPLY_TECHS,
    cap_binding,
    read_csv_rows,
    read_emissions,
    read_production,
    read_status,
)

__all__ = [
    "BC_ZONAL_TOTAL_PJ",
    "COST_BASE_USD_PER_GJ",
    "LHV_GJ_PER_ODT",
    "LOG_SHARE",
    "LOG_VOLUME_M3_PER_YEAR",
    "OUTPUT_DIR",
    "RECOVERY",
    "RUN_SPECS",
    "SUPPLY_DIR",
    "TILE_FACTOR_ODT_PER_M3",
    "RunSpec",
    "SupplyBasis",
    "build_dispatch_rows",
    "build_manifest",
    "build_run_summaries",
    "build_scenario_params",
    "compute_supply_basis",
    "national_cap_series",
    "run_summary_rows",
    "write_parameter_files",
    "write_report",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
TILE_SUPPLY_DIR = Path("data/derived/national/clews_supply/rsyc_tile_production")
OUTPUT_DIR = SUPPLY_DIR / "osecan" / "national_150m"

YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))
KEY_YEARS = (2021, 2022, 2025, 2028, 2030, 2035, 2040, 2050, 2060, 2070)

# RSYC-only v0, no local corrections.  The log share is the diagnostic
# total/log ratio 0.089101080398 / 0.116820637 reproduced from the fixed report;
# the tile factor is the tile-library volume-weighted factor in
# rsyc_tile_supply_summary.json.
LOG_VOLUME_M3_PER_YEAR = 150_000_000.0
LOG_SHARE = 0.762716956451988
LOG_SHARE_TOLERANCE = 1e-9
TILE_FACTOR_ODT_PER_M3 = 0.08831776452936206
LHV_GJ_PER_ODT = 18.5
RECOVERY = {"base": 0.60, "low": 0.35, "high": 0.75}
PWR_BIO_TECHS = tuple(f"PWRBIO{zone}" for zone in (*BC_ZONES, *OTHER_ZONES))

COST_BASE_USD_PER_GJ = 2.456977549
COST_MULTIPLIER = {"base": 1.0, "low": 1.30, "high": 0.80}

# Steady-state BC zonal total from the adopted base caps (B1 3.605475 +
# B2 39.918341 from 2028).  The cap writer recomputes this from the CSVs.
BC_ZONAL_TOTAL_PJ = 43.523816032

CARBON_PRICE_PRE_2026 = 95.0
CARBON_PRICE_BASELINE_FROM_2026 = 115.0
CARBON_PRICE_STRESS_FROM_2026 = 200.0
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
    """One national experiment run."""

    name: str
    label: str
    description: str
    carbon_price_from_2026: float
    hydro_capped: bool
    supply_case: str
    generic_bio_cost_usd_per_gj: float


RUN_SPECS = (
    RunSpec(
        name="baseline",
        label="Current Measures baseline",
        description=(
            "National 150M m3 log/yr supply, base recovery/cost; BC zonal "
            "SUPBCWB1/B2 unchanged; CO2 95 $/t to 2025 and 115 $/t from 2026; "
            "normal hydro."
        ),
        carbon_price_from_2026=CARBON_PRICE_BASELINE_FROM_2026,
        hydro_capped=False,
        supply_case="base",
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
    RunSpec(
        name="stress",
        label="carbon $200/t + hydro capped at 2021 activity",
        description=(
            "The baseline national cap/cost with CO2 at 200 $/t from 2026 and "
            "every PWRHYD<z> capped at its 2021 base-run activity through "
            "TotalTechnologyAnnualActivityUpperLimit (an upper bound, not "
            "fixed dispatch)."
        ),
        carbon_price_from_2026=CARBON_PRICE_STRESS_FROM_2026,
        hydro_capped=True,
        supply_case="base",
        generic_bio_cost_usd_per_gj=GENERIC_BIO_BASE_COST,
    ),
    RunSpec(
        name="baseline_bio",
        label="baseline + generic-BIO cost",
        description=(
            "The baseline run plus RNWBIO VariableCost raised from 3.0 to "
            "4.0 $/GJ (documented biomass-wide bypass sensitivity)."
        ),
        carbon_price_from_2026=CARBON_PRICE_BASELINE_FROM_2026,
        hydro_capped=False,
        supply_case="base",
        generic_bio_cost_usd_per_gj=GENERIC_BIO_SENSITIVITY_COST,
    ),
)


@dataclass(frozen=True, slots=True)
class SupplyBasis:
    """Traceable national residue-supply arithmetic."""

    log_volume_m3_per_year: float
    log_share: float
    total_volume_m3_per_year: float
    tile_factor_odt_per_m3: float
    gross_odt_per_year: float

    @property
    def pj_by_recovery(self) -> dict[str, float]:
        """Recoverable PJ per year for the base/low/high recovery cases."""
        return {
            case: self.gross_odt_per_year * fraction * LHV_GJ_PER_ODT / 1e6
            for case, fraction in RECOVERY.items()
        }


def compute_supply_basis(
    log_volume_m3_per_year: float = LOG_VOLUME_M3_PER_YEAR,
    log_share: float = LOG_SHARE,
    tile_factor_odt_per_m3: float = TILE_FACTOR_ODT_PER_M3,
) -> SupplyBasis:
    """Convert annual log volume to gross residue ODT and recoverable PJ.

    Args:
        log_volume_m3_per_year: Annual log volume harvested, m3.
        log_share: Log volume as a fraction of total harvested volume.
        tile_factor_odt_per_m3: Residue ODT per m3 of total harvested volume.

    Returns:
        The traceable supply basis.

    Raises:
        ValueError: If any input is non-positive or the log share is not a
            fraction.
    """
    if log_volume_m3_per_year <= 0:
        raise ValueError(f"log volume must be positive, got {log_volume_m3_per_year}")
    if not 0 < log_share < 1:
        raise ValueError(f"log share must lie in (0, 1), got {log_share}")
    if tile_factor_odt_per_m3 <= 0:
        raise ValueError(f"tile factor must be positive, got {tile_factor_odt_per_m3}")

    total_volume = log_volume_m3_per_year / log_share
    return SupplyBasis(
        log_volume_m3_per_year=log_volume_m3_per_year,
        log_share=log_share,
        total_volume_m3_per_year=total_volume,
        tile_factor_odt_per_m3=tile_factor_odt_per_m3,
        gross_odt_per_year=total_volume * tile_factor_odt_per_m3,
    )


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    """Write a CSV, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def _read_series(path: Path, technology: str) -> dict[int, float]:
    """Read a technology's annual series from a parameter CSV."""
    _, rows = read_csv_rows(path)
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }
    if not series:
        raise ValueError(f"{technology} has no rows in {path}")
    return dict(sorted(series.items()))


def _verify_fixed_supply_outputs(root: Path) -> None:
    """Fail loudly if the committed tile supply outputs drift from constants.

    Args:
        root: Repository root.

    Raises:
        FileNotFoundError: If the fixed summary is missing.
        TypeError: If the log-share comparison field is missing or non-numeric.
        ValueError: If a factor differs from the adopted value or is non-finite.
    """
    path = root / TILE_SUPPLY_DIR / "rsyc_tile_supply_summary.json"
    if not path.exists():
        raise FileNotFoundError(f"missing fixed tile supply summary: {path}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    factors = {
        row["scenario"]: float(row["residue_factor_odt_per_m3"])
        for row in summary["harvest_scenarios"]
    }
    if abs(factors["tile_mix"] - TILE_FACTOR_ODT_PER_M3) > 1e-15:
        raise ValueError(
            "tile-mix residue factor drifted from the adopted value: "
            f"{factors['tile_mix']} != {TILE_FACTOR_ODT_PER_M3}"
        )

    comparison = summary.get("comparison")
    if not isinstance(comparison, Mapping):
        raise TypeError(f"{path}: missing comparison block for log-share check")
    raw_factor = comparison.get("factor_total_over_log")
    if isinstance(raw_factor, bool) or not isinstance(raw_factor, (int, float)):
        raise TypeError(
            f"{path}: missing or non-numeric comparison.factor_total_over_log"
        )
    factor_total_over_log = float(raw_factor)
    if not math.isfinite(factor_total_over_log):
        raise ValueError(
            f"{path}: non-finite comparison.factor_total_over_log "
            f"{factor_total_over_log!r}"
        )
    divergence = abs(factor_total_over_log - LOG_SHARE)
    if divergence > LOG_SHARE_TOLERANCE:
        raise ValueError(
            "log share drifted from the fixed tile supply summary: "
            f"{factor_total_over_log} != {LOG_SHARE} "
            f"(tolerance {LOG_SHARE_TOLERANCE})"
        )


def national_cap_series(
    national_total_pj: float,
    zonal_caps: Mapping[str, Mapping[int, float]],
) -> dict[int, float]:
    """Return the rest-of-Canada cap as the national total minus BC zones.

    Args:
        national_total_pj: National recoverable PJ per year.
        zonal_caps: ``SUPBCWB1``/``SUPBCWB2`` cap series.

    Returns:
        Year-to-cap mapping for ``SUPBCW``.

    Raises:
        ValueError: If the zonal caps do not cover the horizon or leave no
            rest-of-Canada supply in any year.
    """
    if national_total_pj <= 0:
        raise ValueError(f"national total must be positive, got {national_total_pj}")
    capped_years = set.intersection(*(set(caps) for caps in zonal_caps.values()))
    if capped_years != set(YEARS):
        missing = sorted(set(YEARS) - capped_years)
        raise ValueError(f"zonal caps do not cover the horizon; missing {missing[:3]}")
    series = {
        year: national_total_pj - sum(float(caps[year]) for caps in zonal_caps.values())
        for year in YEARS
    }
    negative = sorted(year for year, value in series.items() if value < 0)
    if negative:
        raise ValueError(f"zonal caps exceed the national total in {negative}")
    return series


def write_parameter_files(root: Path = REPO_ROOT) -> dict[str, object]:
    """Write the national and sensitivity parameter CSVs.

    Args:
        root: Repository root.

    Returns:
        Metadata describing the cap split and costs.

    Raises:
        FileNotFoundError: If a fixed input is missing.
        ValueError: If the supply arithmetic or cap split is invalid.
    """
    _verify_fixed_supply_outputs(root)
    supply_dir = root / SUPPLY_DIR
    output_dir = root / OUTPUT_DIR

    zonal_caps = {
        technology: _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        for technology in SUPPLY_TECHS
    }
    zonal_total = {
        year: sum(caps[year] for caps in zonal_caps.values()) for year in YEARS
    }
    steady_state = zonal_total[2028]
    if abs(steady_state - BC_ZONAL_TOTAL_PJ) > 1e-6:
        raise ValueError(
            f"BC zonal steady-state total drifted: {steady_state} != {BC_ZONAL_TOTAL_PJ}"
        )

    basis = compute_supply_basis()
    pj = basis.pj_by_recovery
    cases = {
        case: {
            "national_total_pj": pj[case],
            "rest_of_canada_pj": pj[case] - steady_state,
            "cost_usd_per_gj": COST_BASE_USD_PER_GJ * COST_MULTIPLIER[case],
        }
        for case in RECOVERY
    }

    for case in RECOVERY:
        cap = national_cap_series(pj[case], zonal_caps)
        suffix = "" if case == "base" else f"_{case}"
        _write_csv(
            output_dir / f"SUPBCW_activity_limits_national_150m{suffix}.csv",
            ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "YEAR": year,
                    "VALUE": repr(cap[year]),
                }
                for year in YEARS
            ],
        )
        _write_csv(
            output_dir / f"SUPBCW_variable_cost_{case}.csv",
            ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "SUPBCW",
                    "YEAR": year,
                    "VALUE": repr(cases[case]["cost_usd_per_gj"]),
                }
                for year in YEARS
            ],
        )

    payload = {
        "log_volume_m3_per_year": basis.log_volume_m3_per_year,
        "log_share": basis.log_share,
        "total_volume_m3_per_year": basis.total_volume_m3_per_year,
        "tile_factor_odt_per_m3": basis.tile_factor_odt_per_m3,
        "gross_odt_per_year": basis.gross_odt_per_year,
        "recovery_fractions": RECOVERY,
        "national_total_pj": pj,
        "bc_zonal_total_pj": steady_state,
        "bc_zonal_total_by_year": {str(y): round(v, 9) for y, v in zonal_total.items()},
        "rest_of_canada_pj_steady_state": {
            case: cases[case]["rest_of_canada_pj"] for case in RECOVERY
        },
        "costs_usd_per_gj": {
            case: cases[case]["cost_usd_per_gj"] for case in RECOVERY
        },
        "national_cap_pj_by_year": {
            case: {str(y): round(value, 9) for y, value in national_cap_series(pj[case], zonal_caps).items()}
            for case in RECOVERY
        },
    }
    (output_dir / "supply_basis.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


def _upsert_technology(
    path: Path,
    technology: str,
    rows: Sequence[Mapping[str, object]],
) -> None:
    """Replace all rows for ``technology`` in a parameter CSV."""
    fields, existing = read_csv_rows(path)
    kept = [row for row in existing if row["TECHNOLOGY"] != technology]
    _write_csv(path, fields, kept + list(rows))


def _hydro_2021(model_root: Path) -> dict[str, float]:
    """Read the 2021 hydro activity used by the stress hydro cap."""
    path = model_root / "results/base/results_csv/ProductionByTechnologyAnnual.csv"
    _, rows = read_csv_rows(path)
    values = {
        row["TECHNOLOGY"]: float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"].startswith("PWRHYD") and row["YEAR"] == "2021"
    }
    if len(values) != 13:
        raise ValueError(f"expected 13 PWRHYD 2021 values, found {len(values)}")
    return values


def _set_carbon_price(path: Path, from_2026: float) -> None:
    """Write the 95 $/t to 2025, scenario price from 2026 schedule."""
    fields, rows = read_csv_rows(path)
    for row in rows:
        row["VALUE"] = repr(
            CARBON_PRICE_PRE_2026 if int(row["YEAR"]) <= 2025 else from_2026
        )
    _write_csv(path, fields, rows)


def build_scenario_params(
    model_root: Path,
    artifact_dir: Path,
    spec: RunSpec,
) -> Path:
    """Build one scenario parameter directory in the disposable model copy.

    Args:
        model_root: Disposable OSeCAN copy.
        artifact_dir: Directory holding the national parameter CSVs.
        spec: Run definition.

    Returns:
        The scenario parameter directory.

    Raises:
        FileNotFoundError: If a required parameter CSV is missing.
        ValueError: If the run's supply case is unknown.
    """
    if spec.supply_case not in RECOVERY:
        raise ValueError(f"unknown supply case {spec.supply_case!r}")

    target = model_root / "results" / spec.name / "params"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for source in (model_root / "data/params").glob("*.csv"):
        shutil.copyfile(source, target / source.name)

    suffix = "" if spec.supply_case == "base" else f"_{spec.supply_case}"
    national_cap = _read_series(
        artifact_dir / f"SUPBCW_activity_limits_national_150m{suffix}.csv", "SUPBCW"
    )
    national_cost = _read_series(
        artifact_dir / f"SUPBCW_variable_cost_{spec.supply_case}.csv", "SUPBCW"
    )
    _upsert_technology(
        target / "TotalTechnologyAnnualActivityUpperLimit.csv",
        "SUPBCW",
        [
            {"REGION": "CAN", "TECHNOLOGY": "SUPBCW", "YEAR": y, "VALUE": repr(v)}
            for y, v in national_cap.items()
        ],
    )
    _upsert_technology(
        target / "VariableCost.csv",
        "SUPBCW",
        [
            {
                "REGION": "CAN",
                "TECHNOLOGY": "SUPBCW",
                "MODE_OF_OPERATION": "1",
                "YEAR": y,
                "VALUE": repr(v),
            }
            for y, v in national_cost.items()
        ],
    )

    supply_dir = REPO_ROOT / SUPPLY_DIR
    for technology in SUPPLY_TECHS:
        caps = _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        costs = _read_series(
            supply_dir / f"{technology}_variable_cost_base.csv", technology
        )
        _upsert_technology(
            target / "TotalTechnologyAnnualActivityUpperLimit.csv",
            technology,
            [
                {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": y, "VALUE": repr(v)}
                for y, v in caps.items()
            ],
        )
        _upsert_technology(
            target / "VariableCost.csv",
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": y,
                    "VALUE": repr(v),
                }
                for y, v in costs.items()
            ],
        )

    _set_carbon_price(target / "EmissionsPenalty.csv", spec.carbon_price_from_2026)
    if spec.hydro_capped:
        hydro = _hydro_2021(model_root)
        cap_path = target / "TotalTechnologyAnnualActivityUpperLimit.csv"
        for technology, value in hydro.items():
            _upsert_technology(
                cap_path,
                technology,
                [
                    {"REGION": "CAN", "TECHNOLOGY": technology, "YEAR": y, "VALUE": repr(value)}
                    for y in YEARS
                ],
            )
    if spec.generic_bio_cost_usd_per_gj != GENERIC_BIO_BASE_COST:
        _upsert_technology(
            target / "VariableCost.csv",
            "RNWBIO",
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": "RNWBIO",
                    "MODE_OF_OPERATION": "1",
                    "YEAR": y,
                    "VALUE": repr(spec.generic_bio_cost_usd_per_gj),
                }
                for y in YEARS
            ],
        )
    return target


def _series_summary(series: Mapping[int, float]) -> dict[str, object]:
    """Summarise a year-indexed series for JSON output."""
    return {
        "by_year": {str(y): round(float(v), 6) for y, v in sorted(series.items())},
        "total": round(sum(float(v) for v in series.values()), 6),
        **{str(y): round(float(series.get(y, 0.0)), 6) for y in KEY_YEARS},
    }


def _other_zone_bcw(production: Mapping[str, Mapping[int, float]]) -> dict[str, object]:
    """Summarise PWRBCW dispatch outside B1/B2."""
    by_zone: dict[str, float] = {}
    by_zone_2050: dict[str, float] = {}
    by_year: dict[int, float] = defaultdict(float)
    for technology, series in production.items():
        if not technology.startswith("PWRBCW") or technology in (
            "PWRBCWB1",
            "PWRBCWB2",
        ):
            continue
        zone = technology[-2:]
        by_zone[zone] = round(sum(float(v) for v in series.values()), 6)
        by_zone_2050[zone] = round(float(series.get(2050, 0.0)), 6)
        for year, value in series.items():
            by_year[year] += float(value)
    return {
        "by_year": {str(y): round(v, 6) for y, v in sorted(by_year.items())},
        "total_pj": round(sum(by_zone.values()), 6),
        "by_zone_total_pj": dict(sorted(by_zone.items())),
        "by_zone_2050_pj": dict(sorted(by_zone_2050.items())),
        **_series_summary(by_year),
    }


def sum_pwr_bio(production: Mapping[str, Mapping[int, float]]) -> float:
    """Sum the zonal ``PWRBIO<zone>`` dispatch series.

    OSeCAN reports power-biomass activity by zone, not under a single
    ``PWRBIO`` key.  Summing only that aggregate key silently reports zero.
    """
    return round(
        sum(
            float(value)
            for technology in PWR_BIO_TECHS
            for value in production.get(technology, {}).values()
        ),
        6,
    )


def reconcile_pwr_bio_dispatch(
    summaries: Mapping[str, object],
    dispatch_rows: Sequence[Mapping[str, object]],
) -> None:
    """Check summary PWRBIO totals against the long-format dispatch table."""
    dispatch_totals: dict[str, float] = defaultdict(float)
    for row in dispatch_rows:
        if row.get("technology") in PWR_BIO_TECHS:
            dispatch_totals[str(row["run"])] += float(row["value_pj"])
    for name, run in summaries["runs"].items():
        expected = float(run["pwr_bio_total_pj"])
        observed = round(dispatch_totals.get(name, 0.0), 6)
        if abs(observed - expected) > 1e-6:
            raise ValueError(
                f"{name}: PWRBIO dispatch total {observed} does not reconcile "
                f"to summary total {expected}"
            )


def _parameter_set_sha256(path: Path) -> str:
    """Hash a parameter directory by sorted CSV names and contents."""
    files = sorted(path.glob("*.csv"))
    if not files:
        raise ValueError(f"{path}: no parameter CSVs to hash")
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def collect_harmonize_evidence(
    model_root: Path,
    runs_root: Path,
    *,
    verified_utc: str | None = None,
) -> dict[str, object]:
    """Run ``osecan harmonize`` on each parameter set and retain the evidence.

    Args:
        model_root: Disposable OSeCAN copy containing ``SETs`` and ``data/params``.
        runs_root: Directory containing the scenario ``params`` directories.
        verified_utc: Optional timestamp override for deterministic tests.

    Returns:
        A payload with one check per invocation: exit status, parameter-set
        SHA-256, and the tool's final message.  ``all_passed`` is true only
        when all four invocations exited zero.

    Raises:
        FileNotFoundError: If a parameter directory is missing.
    """
    model_root = Path(model_root)
    runs_root = Path(runs_root)
    sets_dir = model_root / "SETs"
    executable = model_root / ".venv/bin/python"
    if not executable.exists():
        executable = Path(sys.executable)
    parameter_sets = [
        ("base", model_root / "data/params"),
        *((spec.name, runs_root / spec.name / "params") for spec in RUN_SPECS),
    ]
    checks: list[dict[str, object]] = []
    for name, param_dir in parameter_sets:
        if not param_dir.is_dir():
            raise FileNotFoundError(f"missing parameter set {name}: {param_dir}")
        completed = subprocess.run(
            [
                str(executable),
                "-m",
                "osecan.cli",
                "harmonize",
                "--sets",
                str(sets_dir),
                "--params",
                str(param_dir),
            ],
            cwd=model_root,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        message = (
            stdout.splitlines()[-1]
            if stdout
            else (stderr.splitlines()[-1] if stderr else "")
        )
        checks.append(
            {
                "parameter_set": name,
                "params": str(param_dir),
                "exit_code": completed.returncode,
                "parameter_set_sha256": _parameter_set_sha256(param_dir),
                "message": message,
            }
        )
    all_passed = len(checks) == len(parameter_sets) and all(
        check["exit_code"] == 0 for check in checks
    )
    return {
        "tool": "python -m osecan.cli harmonize",
        "sets": str(sets_dir),
        "all_passed": all_passed,
        "checks": checks,
        "verified_utc": verified_utc or datetime.now(UTC).isoformat(timespec="seconds"),
        "public_repo_modified": False,
        "note": (
            "Recorded in the disposable model copy; the public "
            "github.com/deltae/C2070 clone was not modified."
        ),
    }


def harmonize_evidence_is_complete(evidence: Mapping[str, object] | None) -> bool:
    """Return true only when four named, zero-exit, hashed checks are present."""
    if not evidence or not evidence.get("all_passed"):
        return False
    checks = evidence.get("checks")
    if not isinstance(checks, Sequence) or isinstance(checks, (str, bytes)):
        return False
    expected_names = {"base", *(spec.name for spec in RUN_SPECS)}
    observed_names = {
        str(check.get("parameter_set"))
        for check in checks
        if isinstance(check, Mapping)
    }
    if len(checks) != len(expected_names) or observed_names != expected_names:
        return False
    for check in checks:
        if not isinstance(check, Mapping) or check.get("exit_code") != 0:
            return False
        digest = check.get("parameter_set_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            return False
        try:
            int(digest, 16)
        except ValueError:
            return False
    return True


def harmonize_report_line(evidence: Mapping[str, object] | None) -> str:
    """Render harmonize status from recorded evidence, not from assumption."""
    suffix = (
        "  `PWRBCWB1`/`PWRBCWB2` consume `BCWB1`/`BCWB2`; the other eleven "
        "`PWRBCW<z>` plants consume the national `BCW` pool."
    )
    if not harmonize_evidence_is_complete(evidence):
        checks = (
            evidence.get("checks", []) if isinstance(evidence, Mapping) else []
        )
        observed = ", ".join(
            f"{check.get('parameter_set', '?')} exit {check.get('exit_code', '?')}"
            for check in checks
            if isinstance(check, Mapping)
        ) or "no recorded checks"
        return (
            "`osecan harmonize` success is **not asserted**: evidence is "
            f"missing or failed ({observed}).{suffix}"
        )
    details = ", ".join(
        f"{check['parameter_set']} exit 0 "
        f"(SHA-256 {str(check['parameter_set_sha256'])[:12]}…)"
        for check in evidence["checks"]
    )
    return (
        f"`osecan harmonize` evidence recorded: {details}.  "
        "**HARMONIZED — all params reference valid SET codes.**"
        f"{suffix}"
    )


def summarize_run(
    run_dir: Path,
    caps: Mapping[str, Mapping[int, float]],
    spec: RunSpec,
) -> dict[str, object]:
    """Summarise one solved national run."""
    production = read_production(run_dir)
    emissions = read_emissions(run_dir)
    status = read_status(run_dir)
    summary: dict[str, object] = {
        "run": spec.name,
        "label": spec.label,
        "description": spec.description,
        "supply_case": spec.supply_case,
        "carbon_price_from_2026_usd_per_t": spec.carbon_price_from_2026,
        "hydro_capped_at_2021": spec.hydro_capped,
        "pwr_hyd_b2_2021_pj": round(
            float(production.get("PWRHYDB2", {}).get(2021, 0.0)), 6
        ),
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
        "pwr_bio_total_pj": sum_pwr_bio(production),
        "emissions_mt": {
            "by_year": {str(y): round(v, 6) for y, v in emissions.items()},
            "total": round(sum(emissions.values()), 6),
            **_series_summary(emissions),
        },
    }
    summary["cap_binding"] = {
        technology: cap_binding(
            production.get(technology, {}),
            caps[technology],
            technology,
            violation_tolerance=1e-3,
        )
        for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2")
    }
    return summary


def build_run_summaries(
    runs_root: Path,
    artifact_dir: Path,
    root: Path = REPO_ROOT,
    harmonize_evidence: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build run summaries and cross-run comparisons from solved runs.

    Args:
        runs_root: Directory containing the solved run directories.
        artifact_dir: Directory holding the national parameter CSVs.
        root: Repository root.
        harmonize_evidence: Recorded ``collect_harmonize_evidence`` payload.
            Without it the report renders success as unverified.
    """
    caps = {
        "SUPBCW": _read_series(
            artifact_dir / "SUPBCW_activity_limits_national_150m.csv", "SUPBCW"
        )
    }
    for technology in SUPPLY_TECHS:
        caps[technology] = _read_series(
            root / SUPPLY_DIR / f"{technology}_activity_limits_base.csv", technology
        )
    runs = {
        spec.name: summarize_run(runs_root / spec.name, caps, spec)
        for spec in RUN_SPECS
    }
    baseline = runs["baseline"]
    comparison = {}
    for name in ("stress", "baseline_bio"):
        run = runs[name]
        comparison[name] = {
            "objective_delta_musd": round(
                float(run["objective_musd"]) - float(baseline["objective_musd"]), 2
            ),
            "emissions_delta_mt": round(
                float(run["emissions_mt"]["total"])
                - float(baseline["emissions_mt"]["total"]),
                6,
            ),
            "supbcw_delta_pj": round(
                float(run["supbcw_pj"]["total"]) - float(baseline["supbcw_pj"]["total"]),
                6,
            ),
            "bc_zonal_delta_pj": round(
                (
                    float(run["supbcwb1_pj"]["total"])
                    + float(run["supbcwb2_pj"]["total"])
                )
                - (
                    float(baseline["supbcwb1_pj"]["total"])
                    + float(baseline["supbcwb2_pj"]["total"])
                ),
                6,
            ),
        }
    payload: dict[str, object] = {
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
                "baseline_2026-2070": CARBON_PRICE_BASELINE_FROM_2026,
                "stress_2026-2070": CARBON_PRICE_STRESS_FROM_2026,
            },
            "hydro": {
                "baseline": "normal (no cap)",
                "stress": (
                    "every PWRHYD<z> capped at 2021 base activity through "
                    "TotalTechnologyAnnualActivityUpperLimit (upper bound, "
                    "not fixed dispatch)"
                ),
            },
            "generic_bio_cost_usd_per_gj": {
                spec.name: spec.generic_bio_cost_usd_per_gj for spec in RUN_SPECS
            },
        },
        "caps": {
            technology: {str(y): round(v, 9) for y, v in series.items()}
            for technology, series in caps.items()
        },
        "runs": runs,
        "comparison": comparison,
        "harmonize": (
            dict(harmonize_evidence)
            if harmonize_evidence is not None
            else {
                "all_passed": False,
                "checks": [],
                "note": "No harmonize evidence supplied.",
            }
        ),
    }
    reconcile_pwr_bio_dispatch(payload, build_dispatch_rows(runs_root))
    return payload


def build_dispatch_rows(runs_root: Path) -> list[dict[str, object]]:
    """Build the long-format dispatch-by-zone table."""
    zones = (*BC_ZONES, *OTHER_ZONES)
    technologies = [
        "SUPBCW",
        "SUPBCWB1",
        "SUPBCWB2",
        *(f"PWRBCW{zone}" for zone in zones),
        *(f"PWRBIO{zone}" for zone in zones),
    ]
    rows: list[dict[str, object]] = []
    for spec in RUN_SPECS:
        production = read_production(runs_root / spec.name)
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
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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
    artifact_dir: Path,
    outputs: Sequence[Path],
    generated_utc: str,
) -> dict[str, object]:
    """Build the artifact manifest with SHA-256 digests."""
    inputs = [
        root / TILE_SUPPLY_DIR / "rsyc_tile_supply_summary.json",
        root / TILE_SUPPLY_DIR / "rsyc_tile_harvest_sensitivity.csv",
        root / SUPPLY_DIR / "SUPBCWB1_activity_limits_base.csv",
        root / SUPPLY_DIR / "SUPBCWB1_variable_cost_base.csv",
        root / SUPPLY_DIR / "SUPBCWB2_activity_limits_base.csv",
        root / SUPPLY_DIR / "SUPBCWB2_variable_cost_base.csv",
        *(artifact_dir / name for name in (
            "SUPBCW_activity_limits_national_150m.csv",
            "SUPBCW_activity_limits_national_150m_low.csv",
            "SUPBCW_activity_limits_national_150m_high.csv",
            "SUPBCW_variable_cost_base.csv",
            "SUPBCW_variable_cost_low.csv",
            "SUPBCW_variable_cost_high.csv",
            "supply_basis.json",
        )),
    ]
    input_rows = [
        {
            "path": _relative(path, root),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in inputs
        if path.exists()
    ]
    output_rows = [
        {
            "path": _relative(path, root),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in outputs
    ]
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
            "RSYC-only v0: no local corrections, benchmark correction factors withheld.",
            (
                "The 150M m3 log/yr supply is converted through the fixed tile-mix "
                f"factor {TILE_FACTOR_ODT_PER_M3} ODT/m3 total and log share "
                f"{LOG_SHARE}; no adopted AAC, THLB, or operability filter is applied."
            ),
            (
                "The BC zonal SUPBCWB1/B2 caps and costs are unchanged; SUPBCW "
                "carries only the rest-of-Canada balance, so the three caps sum "
                "to the national total."
            ),
            (
                "The stress hydro constraint is a TotalTechnologyAnnualActivityUpperLimit "
                "cap at every PWRHYD<z>'s 2021 base-run activity; it is an upper "
                "bound, not fixed dispatch, and a sensitivity probe, not a forecast."
            ),
            (
                "The generic-BIO sensitivity raises RNWBIO VariableCost to 4.0 $/GJ "
                "for all consumers; it is not a dedicated power-biomass supply curve."
            ),
        ],
        "inputs": input_rows,
        "outputs": output_rows,
        "run_evidence": {
            "runs_root": "/tmp/osecan_national/results",
            "public_repo_modified": False,
            "note": (
                "Run summaries are derived from the solved CSVs in the disposable "
                "copy; only the summaries and report are committed."
            ),
        },
    }


def _fmt(value: object, digits: int = 2) -> str:
    """Format a numeric value for a markdown table."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{float(value):,.{digits}f}"


def write_report(
    artifact_dir: Path,
    supply: Mapping[str, object],
    summaries: Mapping[str, object],
) -> Path:
    """Render the committed markdown report from the artifacts."""
    runs = summaries["runs"]
    baseline = runs["baseline"]
    stress = runs["stress"]
    baseline_bio = runs["baseline_bio"]
    comparison = summaries["comparison"]
    caps = summaries["caps"]

    def series_row(run: Mapping[str, object], key: str, label: str, total_key: str = "total") -> str:
        series = run[key]
        return (
            f"| {label} | {_fmt(series['2021'], 3)} | {_fmt(series['2025'], 3)} | "
            f"{_fmt(series['2028'], 3)} | {_fmt(series['2030'], 3)} | "
            f"{_fmt(series['2050'], 3)} | {_fmt(series['2070'], 3)} | "
            f"{_fmt(series[total_key], 3)} |"
        )

    lines = [
        "# National RSYC 150M m3 log/yr supply in OSeCAN",
        "",
        (
            "Status: **FRESH-side provisional** — not an official C2070 result. "
            f"The experiment uses a disposable copy of public `deltae/C2070` "
            f"commit `{MODEL_COMMIT[:12]}`; the public clone was not modified."
        ),
        "",
        "## Supply math (150M log -> total m3 -> ODT -> PJ; BC/rest split)",
        "",
        "| Step | Value |",
        "|---|---:|",
        f"| Annual log volume (m3/yr) | {_fmt(supply['log_volume_m3_per_year'], 0)} |",
        f"| Log share of total volume | {supply['log_share']:.9f} |",
        f"| Total volume (m3/yr) | {_fmt(supply['total_volume_m3_per_year'], 0)} |",
        f"| Tile-mix factor (ODT/m3 total) | {supply['tile_factor_odt_per_m3']:.12f} |",
        f"| Gross residue (ODT/yr) | {_fmt(supply['gross_odt_per_year'], 2)} |",
        "",
        "| Case | Recovery | National PJ/yr | BC zonal PJ/yr | Rest-of-Canada PJ/yr | Cost ($/GJ) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in ("base", "low", "high"):
        lines.append(
            f"| {case} | {supply['recovery_fractions'][case]:.2f} | "
            f"{_fmt(supply['national_total_pj'][case], 3)} | "
            f"{_fmt(supply['bc_zonal_total_pj'], 3)} | "
            f"{_fmt(supply['rest_of_canada_pj_steady_state'][case], 3)} | "
            f"{_fmt(supply['costs_usd_per_gj'][case], 6)} |"
        )
    lines += [
        "",
        (
            "The BC zonal total is the 2028+ sum of the unchanged `SUPBCWB1` "
            f"({_fmt(caps['SUPBCWB1']['2028'], 6)} PJ) and `SUPBCWB2` "
            f"({_fmt(caps['SUPBCWB2']['2028'], 6)} PJ) caps.  The `SUPBCW` cap is "
            "recomputed year by year as national total minus the two zonal caps, "
            "which removes the 2021-2027 zonal phase-down from the national pool "
            "and keeps the three caps equal to the national total in every year."
        ),
        "",
        "## Parameter files/harmonize",
        "",
        "| File | Content |",
        "|---|---|",
        "| `SUPBCW_activity_limits_national_150m.csv` | base rest-of-Canada cap, 2021-2070 |",
        "| `SUPBCW_activity_limits_national_150m_low.csv` | low-recovery rest-of-Canada cap |",
        "| `SUPBCW_activity_limits_national_150m_high.csv` | high-recovery rest-of-Canada cap |",
        "| `SUPBCW_variable_cost_{base,low,high}.csv` | 2.456978, 3.194071, 1.965582 $/GJ |",
        "| `supply_basis.json` | full conversion and cap-split record |",
        "",
        harmonize_report_line(summaries.get("harmonize")),
        "",
        "## Baseline (objective, dispatch, binding, emissions)",
        "",
        (
            f"`Current Measures`, 2021-2070, CO2 95 $/t to 2025 and 115 $/t "
            f"thereafter, normal hydro, national base cap "
            f"{_fmt(caps['SUPBCW']['2028'], 3)} PJ (2028+)."
        ),
        "",
        "| Metric | Baseline | Stress $200/hydro-capped | Δ stress |",
        "|---|---:|---:|---:|",
        (
            f"| Solver status | {baseline['solver_status']} | {stress['solver_status']} | — |"
        ),
        (
            f"| Objective (M$) | {_fmt(baseline['objective_musd'], 2)} | "
            f"{_fmt(stress['objective_musd'], 2)} | "
            f"{_fmt(comparison['stress']['objective_delta_musd'], 2)} |"
        ),
        (
            f"| Emissions total (Mt) | {_fmt(baseline['emissions_mt']['total'], 3)} | "
            f"{_fmt(stress['emissions_mt']['total'], 3)} | "
            f"{_fmt(comparison['stress']['emissions_delta_mt'], 3)} |"
        ),
        (
            f"| `PWRHYDB2` 2021 dispatch (PJ) | "
            f"{_fmt(baseline['pwr_hyd_b2_2021_pj'], 6)} | "
            f"{_fmt(stress['pwr_hyd_b2_2021_pj'], 6)} | "
            f"{_fmt(float(stress['pwr_hyd_b2_2021_pj']) - float(baseline['pwr_hyd_b2_2021_pj']), 6)} |"
        ),
        "",
        "### SUP activity by year (PJ)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        series_row(baseline, "supbcw_pj", "baseline / `SUPBCW`"),
        series_row(baseline, "supbcwb1_pj", "baseline / `SUPBCWB1`"),
        series_row(baseline, "supbcwb2_pj", "baseline / `SUPBCWB2`"),
        series_row(stress, "supbcw_pj", "stress / `SUPBCW`"),
        series_row(stress, "supbcwb1_pj", "stress / `SUPBCWB1`"),
        series_row(stress, "supbcwb2_pj", "stress / `SUPBCWB2`"),
        "",
        "### PWR dispatch by year (PJ electricity)",
        "",
        "| Run / technology | 2021 | 2025 | 2028 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        series_row(baseline, "pwrbcwb1_pj", "baseline / `PWRBCWB1`"),
        series_row(baseline, "pwrbcwb2_pj", "baseline / `PWRBCWB2`"),
        series_row(baseline, "pwrbcw_other_zone_pj", "baseline / `PWRBCW` other zones", "total_pj"),
        series_row(stress, "pwrbcwb1_pj", "stress / `PWRBCWB1`"),
        series_row(stress, "pwrbcwb2_pj", "stress / `PWRBCWB2`"),
        series_row(stress, "pwrbcw_other_zone_pj", "stress / `PWRBCW` other zones", "total_pj"),
        "",
        "### Cap binding",
        "",
        "| Run | Technology | Cap binds | Binding years | Count | Max utilisation |",
        "|---|---|---|---|---:|---:|",
    ]
    for run_name in ("baseline", "stress", "baseline_bio"):
        for technology in ("SUPBCW", "SUPBCWB1", "SUPBCWB2"):
            binding = runs[run_name]["cap_binding"][technology]
            lines.append(
                f"| {run_name} | `{technology}` | {_fmt(binding['binding'])} | "
                f"{binding['binding_years'] or '—'} | {binding['binding_year_count']} | "
                f"{_fmt(binding['max_utilisation'], 3)} |"
            )
    lines += [
        "",
        "### PWRBCW dispatch outside B1/B2 (PJ, total 2021-2070)",
        "",
        "| Zone | Baseline | Stress |",
        "|---|---:|---:|",
    ]
    zones = sorted(
        set(baseline["pwrbcw_other_zone_pj"]["by_zone_total_pj"])
        | set(stress["pwrbcw_other_zone_pj"]["by_zone_total_pj"])
        | set(OTHER_ZONES)
    )
    for zone in zones:
        lines.append(
            f"| {zone} | "
            f"{_fmt(baseline['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} | "
            f"{_fmt(stress['pwrbcw_other_zone_pj']['by_zone_total_pj'].get(zone, 0.0), 3)} |"
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
        "## Stress $200/hydro-capped",
        "",
        (
            "The stress run raises the 2026+ carbon price from 115 to 200 $/t.  "
            "Its only hydro constraint is a `TotalTechnologyAnnualActivityUpperLimit` "
            "at each `PWRHYD<z>`'s 2021 base activity; it is a **cap at the 2021 "
            "activity, not fixed dispatch**.  `PWRHYDB2` dispatches "
            f"{_fmt(stress['pwr_hyd_b2_2021_pj'], 6)} PJ in 2021 in the stress run "
            f"versus {_fmt(baseline['pwr_hyd_b2_2021_pj'], 6)} PJ in the baseline.  "
            "A true fixed-dispatch hydro run is deferred.  The run keeps the same "
            "national/zonal caps and costs as the baseline."
        ),
        "",
        (
            f"Objective {_fmt(stress['objective_musd'], 2)} M$ vs baseline "
            f"{_fmt(baseline['objective_musd'], 2)} M$ "
            f"({_fmt(comparison['stress']['objective_delta_musd'], 2)} M$); "
            f"cumulative CO2 {_fmt(stress['emissions_mt']['total'], 6)} Mt vs "
            f"{_fmt(baseline['emissions_mt']['total'], 6)} Mt "
            f"({_fmt(comparison['stress']['emissions_delta_mt'], 6)} Mt vs baseline).  "
            "The −23.114 Mt figure is relative to the BC-only stress run "
            "(61.659 Mt), not the national baseline.  In the stress run "
            f"`SUPBCW` binds in {stress['cap_binding']['SUPBCW']['binding_years']} "
            f"({stress['cap_binding']['SUPBCW']['binding_year_count']} years), "
            f"`SUPBCWB1` in {stress['cap_binding']['SUPBCWB1']['binding_years']} "
            f"and `SUPBCWB2` in {stress['cap_binding']['SUPBCWB2']['binding_years']}."
        ),
        "",
        "### Comparison to the 50 PJ placeholder and the BC zonal 43.5238 PJ run",
        "",
        "| Run | Forcing | Objective (M$) | Emissions (Mt) | BCW supply (PJ) |",
        "|---|---|---:|---:|---:|",
        (
            "| 50 PJ placeholder | baseline (95/115 $/t, normal hydro) | "
            "1,331,287.25 | 25.750 | 0.000 |"
        ),
        (
            f"| National 150M baseline | baseline (95/115 $/t, normal hydro) | "
            f"{_fmt(baseline['objective_musd'], 2)} | "
            f"{_fmt(baseline['emissions_mt']['total'], 3)} | 0.000 |"
        ),
        (
            "| BC zonal 43.5238 PJ | stress (200 $/t, hydro capped at 2021 activity) | "
            "1,371,599.09 | 61.659 | 1,872.589 |"
        ),
        (
            f"| National 150M stress | stress (200 $/t, hydro capped at 2021 activity) | "
            f"{_fmt(stress['objective_musd'], 2)} | "
            f"{_fmt(stress['emissions_mt']['total'], 3)} | "
            f"{_fmt(float(stress['supbcw_pj']['total']) + float(stress['supbcwb1_pj']['total']) + float(stress['supbcwb2_pj']['total']), 3)} |"
        ),
        "",
        (
            "At the baseline carbon schedule gas outcompetes biomass, so all three "
            "national biomass supply technologies are zero and the national cap "
            "is slack.  The 50 PJ placeholder and the national 150M baseline "
            "differ by 2.27 M$ (1,331,289.52 vs 1,331,287.25 M$); this delta is "
            "an **unexplained controlled comparison** and is not attributed to "
            "`SUPBCW`, whose baseline activity and variable-cost contribution are "
            "both zero.  Under the stress forcing the national cap binds and "
            "supplies about 8.8 EJ over 2021-2070, versus 1.87 EJ in the BC-only "
            "zonal run.  Relative to that BC-only stress run, emissions fall from "
            "61.659 to 38.545 Mt; relative to the national baseline they rise by "
            "12.829929 Mt (25.714753 to 38.544682 Mt)."
        ),
        "",
        "## Generic-BIO sensitivity",
        "",
        (
            f"The `baseline_bio` run raises `RNWBIO` VariableCost from "
            f"{_fmt(GENERIC_BIO_BASE_COST, 2)} to {_fmt(GENERIC_BIO_SENSITIVITY_COST, 2)} $/GJ "
            "on the baseline forcing.  This is a documented biomass-wide bypass "
            "sensitivity, not a dedicated power-biomass supply curve."
        ),
        "",
        "| Metric | Baseline | Baseline + BIO | Δ |",
        "|---|---:|---:|---:|",
        (
            f"| Objective (M$) | {_fmt(baseline['objective_musd'], 2)} | "
            f"{_fmt(baseline_bio['objective_musd'], 2)} | "
            f"{_fmt(comparison['baseline_bio']['objective_delta_musd'], 2)} |"
        ),
        (
            f"| Emissions total (Mt) | {_fmt(baseline['emissions_mt']['total'], 3)} | "
            f"{_fmt(baseline_bio['emissions_mt']['total'], 3)} | "
            f"{_fmt(comparison['baseline_bio']['emissions_delta_mt'], 3)} |"
        ),
        (
            f"| `SUPBCW` total (PJ) | {_fmt(baseline['supbcw_pj']['total'], 3)} | "
            f"{_fmt(baseline_bio['supbcw_pj']['total'], 3)} | "
            f"{_fmt(comparison['baseline_bio']['supbcw_delta_pj'], 3)} |"
        ),
        (
            f"| BC zonal total (PJ) | "
            f"{_fmt(float(baseline['supbcwb1_pj']['total']) + float(baseline['supbcwb2_pj']['total']), 3)} | "
            f"{_fmt(float(baseline_bio['supbcwb1_pj']['total']) + float(baseline_bio['supbcwb2_pj']['total']), 3)} | "
            f"{_fmt(comparison['baseline_bio']['bc_zonal_delta_pj'], 3)} |"
        ),
        (
            f"| `PWRBIO` total (PJ) | {_fmt(baseline['pwr_bio_total_pj'], 3)} | "
            f"{_fmt(baseline_bio['pwr_bio_total_pj'], 3)} | "
            f"{_fmt(float(baseline_bio['pwr_bio_total_pj']) - float(baseline['pwr_bio_total_pj']), 3)} |"
        ),
        "",
        "## Tests/commits/publish/status",
        "",
        (
            "Tests cover the cap split (`SUPBCW + SUPBCWB1 + SUPBCWB2 = national "
            "total` in every year) and the 150M log -> total m3 -> ODT -> PJ "
            "conversion against the fixed tile-supply outputs."
        ),
        "",
        "## Caveats (RSYC-only, no AAC/THLB, B25, group curves)",
        "",
        "- RSYC-only v0: no local corrections; benchmark correction factors withheld.",
        "- No adopted national AAC, THLB, or operability filter is applied.",
        "- B25 is flagged over capacity in the tile library (1.216 ratio) and is not clipped.",
        "- Group cells (BROADLEAF, CONIFER) use group RSYC curves; generic cells use the tile generic curve.",
        "- The 150M log basis is an annual supply assumption, not a harvest forecast.",
        "- The BC zonal total is the 2028+ steady state; 2021-2027 zonal caps phase down from historic levels.",
        "",
    ]
    path = artifact_dir / "NATIONAL_150M_OSECAN_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_summary_rows(summaries: Mapping[str, object]) -> list[dict[str, object]]:
    """Flatten run objectives and supply totals for the report table."""
    rows = []
    for name, run in summaries["runs"].items():
        rows.append(
            {
                "run": name,
                "objective_musd": run["objective_musd"],
                "emissions_mt": run["emissions_mt"]["total"],
                "supbcw_total_pj": run["supbcw_pj"]["total"],
                "supbcwb1_total_pj": run["supbcwb1_pj"]["total"],
                "supbcwb2_total_pj": run["supbcwb2_pj"]["total"],
            }
        )
    return rows
