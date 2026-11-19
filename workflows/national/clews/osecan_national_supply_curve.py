"""National rest-of-Canada biomass supply curve for the disposable OSeCAN model.

The single ``SUPBCW`` technology is replaced by four incremental supply tiers
that all produce the national ``BCW`` fuel.  The BC zonal ``SUPBCWB1`` /
``SUPBCWB2`` technologies and their ``BCWB1`` / ``BCWB2`` fuels are unchanged.

The tier stack is defined on the 200M m3/yr log basis:

================  ==============  ==============  ==============
tier              interval (PJ)   incremental cap cost ($/GJ)
================  ==============  ==============  ==============
``SUPBCWT1``      0 - 85.007113   85.007113       2.456978
``SUPBCWT2``      85.007113 - 109.398863  24.391750  2.456978
``SUPBCWT3``      109.398863 - 149.272577 39.873714  3.194071
``SUPBCWT4``      149.272577 - 213.538041 64.265464  3.500000
================  ==============  ==============  ==============

``SUPBCWT4`` is a documented marginal high-cost tier and every output flags it
as a scenario assumption, not an empirical cost curve.

The module writes parameter CSVs and run artifacts into the parent data
repository.  Model execution happens only in a disposable OSeCAN copy supplied
with ``--model-root``; the public https://github.com/deltae/C2070 repository is
never modified.
"""

from __future__ import annotations

import argparse
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

__all__ = [
    "BC_ZONAL_TOTAL_PJ",
    "NATIONAL_TOTAL_PJ",
    "REST_OF_CANADA_PJ",
    "Tier",
    "TIERS",
    "build_dispatch_rows",
    "build_scenario_params",
    "cap_binding",
    "national_cap_series",
    "summarize_run",
    "tier_caps",
    "validate_tier_structure",
    "write_parameter_files",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
OUTPUT_DIR = SUPPLY_DIR / "osecan" / "national_supply_curve"

HORIZON_START = 2021
HORIZON_END = 2070
YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))
KEY_YEARS = (2021, 2022, 2025, 2028, 2030, 2035, 2040, 2050, 2060, 2070)

NATIONAL_TOTAL_PJ = 257.061857
BC_ZONAL_TOTAL_PJ = 43.523816
REST_OF_CANADA_PJ = 213.538041

CARBON_PRICE_PRE_2026 = 95.0
CARBON_PRICE_BASELINE_FROM_2026 = 115.0
CARBON_PRICE_STRESS_FROM_2026 = 200.0
GENERIC_BIO_BASE_COST = 3.0

MODEL_COMMIT = "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44"
MODEL_REPOSITORY = "https://github.com/deltae/C2070"
MODEL_PACKAGE = "osecan 0.1.0"
MODEL_SOLVER = "GLPK via swiglpk 5.0.13"
MODEL_SCENARIO = "Current Measures"
STATUS = "fresh_side_provisional_not_c2070_official"


@dataclass(frozen=True, slots=True)
class Tier:
    """One incremental rest-of-Canada supply tier."""

    technology: str
    label: str
    lower_pj: float
    upper_pj: float
    incremental_cap_pj: float
    variable_cost_usd_per_gj: float
    scenario_flag: str


TIERS = (
    Tier("SUPBCWT1", "T1", 0.0, 85.007113, 85.007113, 2.456978, "base_cost"),
    Tier("SUPBCWT2", "T2", 85.007113, 109.398863, 24.391750, 2.456978, "base_cost"),
    Tier("SUPBCWT3", "T3", 109.398863, 149.272577, 39.873714, 3.194071, "mid_cost"),
    Tier(
        "SUPBCWT4",
        "T4",
        149.272577,
        213.538041,
        64.265464,
        3.500000,
        "scenario_high_cost_marginal_tier",
    ),
)

RUN_SPECS = (
    {
        "name": "baseline",
        "label": "Current Measures baseline",
        "carbon_price_from_2026": CARBON_PRICE_BASELINE_FROM_2026,
        "hydro_mode": "normal",
    },
    {
        "name": "stress",
        "label": "CO2 $200/t from 2026 + hydro capped at 2021",
        "carbon_price_from_2026": CARBON_PRICE_STRESS_FROM_2026,
        "hydro_mode": "upper_2021",
    },
    {
        "name": "stress_fixed_hydro",
        "label": "CO2 $200/t from 2026 + fixed hydro at 2021",
        "carbon_price_from_2026": CARBON_PRICE_STRESS_FROM_2026,
        "hydro_mode": "fixed_2021",
    },
)


# ---------------------------------------------------------------------------
# Pure structure checks
# ---------------------------------------------------------------------------


def validate_tier_structure() -> None:
    """Fail loudly if the tier stack does not tile the adopted supply curve.

    Raises:
        ValueError: If boundaries are discontinuous, an incremental cap does
            not equal its interval width, the caps do not sum to the
            rest-of-Canada total, or variable costs are not non-decreasing.
    """
    if not TIERS:
        raise ValueError("tier stack is empty")
    previous_upper = 0.0
    for tier in TIERS:
        if not math.isfinite(tier.incremental_cap_pj) or tier.incremental_cap_pj <= 0:
            raise ValueError(f"{tier.technology}: non-positive cap {tier.incremental_cap_pj}")
        if not math.isclose(tier.lower_pj, previous_upper, abs_tol=1e-9):
            raise ValueError(
                f"{tier.technology}: lower bound {tier.lower_pj} does not "
                f"continue {previous_upper}"
            )
        width = tier.upper_pj - tier.lower_pj
        if not math.isclose(width, tier.incremental_cap_pj, abs_tol=1e-9):
            raise ValueError(
                f"{tier.technology}: cap {tier.incremental_cap_pj} != interval "
                f"width {width}"
            )
        previous_upper = tier.upper_pj
    total_cap = sum(tier.incremental_cap_pj for tier in TIERS)
    if not math.isclose(total_cap, REST_OF_CANADA_PJ, abs_tol=1e-9):
        raise ValueError(
            f"tier caps sum to {total_cap}, not rest-of-Canada {REST_OF_CANADA_PJ}"
        )
    if not math.isclose(
        REST_OF_CANADA_PJ + BC_ZONAL_TOTAL_PJ, NATIONAL_TOTAL_PJ, abs_tol=1e-9
    ):
        raise ValueError(
            "rest-of-Canada plus BC zonal total does not equal the national total"
        )
    costs = [tier.variable_cost_usd_per_gj for tier in TIERS]
    if any(later < earlier - 1e-12 for earlier, later in zip(costs, costs[1:])):
        raise ValueError(f"tier variable costs are not non-decreasing: {costs}")


def national_cap_series(zonal_caps: Mapping[str, Mapping[int, float]]) -> dict[int, float]:
    """Return the rest-of-Canada cap as national total minus the BC zones.

    Args:
        zonal_caps: ``SUPBCWB1`` / ``SUPBCWB2`` year-to-cap mappings.

    Returns:
        Year-to-cap mapping for the rest-of-Canada stack.

    Raises:
        ValueError: If the zonal caps do not cover the horizon, leave no room,
            or disagree with the adopted steady-state BC total.
    """
    capped_years = set.intersection(*(set(caps) for caps in zonal_caps.values()))
    if capped_years != set(YEARS):
        missing = sorted(set(YEARS) - capped_years)
        raise ValueError(f"zonal caps do not cover the horizon; missing {missing[:3]}")
    steady_state = sum(float(caps[2028]) for caps in zonal_caps.values())
    if abs(steady_state - BC_ZONAL_TOTAL_PJ) > 1e-6:
        raise ValueError(
            f"BC zonal steady-state total drifted: {steady_state} != {BC_ZONAL_TOTAL_PJ}"
        )
    series = {
        year: NATIONAL_TOTAL_PJ - sum(float(caps[year]) for caps in zonal_caps.values())
        for year in YEARS
    }
    negative = sorted(year for year, value in series.items() if value < 0)
    if negative:
        raise ValueError(f"zonal caps exceed the national total in {negative}")
    return series


def tier_caps(zonal_caps: Mapping[str, Mapping[int, float]]) -> dict[str, dict[int, float]]:
    """Split the rest-of-Canada cap into the four incremental tiers.

    T1-T3 keep their adopted incremental caps in every year.  T4 absorbs the
    early-year BC zonal phase-down, so the tier sum equals the rest-of-Canada
    cap in every year and tiers plus BC zones equal the national total.

    Args:
        zonal_caps: ``SUPBCWB1`` / ``SUPBCWB2`` year-to-cap mappings.

    Returns:
        Mapping of tier technology to year-to-cap series.

    Raises:
        ValueError: If the residual T4 cap would be negative.
    """
    validate_tier_structure()
    rest = national_cap_series(zonal_caps)
    fixed_cap = sum(tier.incremental_cap_pj for tier in TIERS[:-1])
    caps: dict[str, dict[int, float]] = {tier.technology: {} for tier in TIERS}
    for year in YEARS:
        remaining = rest[year] - fixed_cap
        if remaining < -1e-9:
            raise ValueError(
                f"{year}: rest-of-Canada cap {rest[year]} is below the T1-T3 "
                f"stack {fixed_cap}"
            )
        for tier in TIERS[:-1]:
            caps[tier.technology][year] = tier.incremental_cap_pj
        caps[TIERS[-1].technology][year] = remaining
    return caps


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _read_series(path: Path, technology: str) -> dict[int, float]:
    _, rows = _read_rows(path)
    series = {
        int(row["YEAR"]): float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"] == technology
    }
    if not series:
        raise ValueError(f"{technology} has no rows in {path}")
    return dict(sorted(series.items()))


def _upsert_rows(
    path: Path,
    technology: str,
    rows: Sequence[Mapping[str, object]],
    *,
    technology_column: str = "TECHNOLOGY",
) -> None:
    fields, existing = _read_rows(path)
    kept = [row for row in existing if row.get(technology_column) != technology]
    _write_csv(path, fields, kept + list(rows))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Artifact writers
# ---------------------------------------------------------------------------


def write_parameter_files(
    root: Path = REPO_ROOT,
) -> dict[str, object]:
    """Write tier parameter CSVs and the cap reconciliation.

    Args:
        root: Repository root.

    Returns:
        Payload describing the tier stack, caps, and reconciliation.

    Raises:
        FileNotFoundError: If an adopted BC zonal cap file is missing.
        ValueError: If the tier or cap arithmetic fails validation.
    """
    validate_tier_structure()
    supply_dir = root / SUPPLY_DIR
    output_dir = root / OUTPUT_DIR

    zonal_caps = {
        technology: _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        for technology in ("SUPBCWB1", "SUPBCWB2")
    }
    zonal_costs = {
        technology: _read_series(
            supply_dir / f"{technology}_variable_cost_base.csv", technology
        )
        for technology in ("SUPBCWB1", "SUPBCWB2")
    }
    caps = tier_caps(zonal_caps)
    rest = national_cap_series(zonal_caps)

    _write_csv(
        output_dir / "tier_definitions.csv",
        [
            "tier",
            "technology",
            "lower_pj",
            "upper_pj",
            "incremental_cap_pj",
            "variable_cost_usd_per_gj",
            "cost_basis",
            "scenario_flag",
        ],
        [
            {
                "tier": tier.label,
                "technology": tier.technology,
                "lower_pj": repr(tier.lower_pj),
                "upper_pj": repr(tier.upper_pj),
                "incremental_cap_pj": repr(tier.incremental_cap_pj),
                "variable_cost_usd_per_gj": repr(tier.variable_cost_usd_per_gj),
                "cost_basis": "adopted_rest_of_canada_curve",
                "scenario_flag": tier.scenario_flag,
            }
            for tier in TIERS
        ],
    )

    limit_rows = [
        {
            "REGION": "CAN",
            "TECHNOLOGY": tier.technology,
            "YEAR": year,
            "VALUE": repr(caps[tier.technology][year]),
        }
        for tier in TIERS
        for year in YEARS
    ]
    limit_rows.extend(
        {
            "REGION": "CAN",
            "TECHNOLOGY": "SUPBCW",
            "YEAR": year,
            "VALUE": "0.0",
        }
        for year in YEARS
    )
    _write_csv(
        output_dir / "tier_activity_limits.csv",
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        limit_rows,
    )
    _write_csv(
        output_dir / "tier_variable_costs.csv",
        ["REGION", "TECHNOLOGY", "MODE_OF_OPERATION", "YEAR", "VALUE"],
        [
            {
                "REGION": "CAN",
                "TECHNOLOGY": tier.technology,
                "MODE_OF_OPERATION": "1",
                "YEAR": year,
                "VALUE": repr(tier.variable_cost_usd_per_gj),
            }
            for tier in TIERS
            for year in YEARS
        ],
    )

    zonal_rows = []
    for technology in ("SUPBCWB1", "SUPBCWB2"):
        for year in YEARS:
            zonal_rows.append(
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": year,
                    "VALUE": repr(zonal_caps[technology][year]),
                    "VARIABLE_COST_USD_PER_GJ": repr(zonal_costs[technology][year]),
                }
            )
    _write_csv(
        output_dir / "bc_zonal_activity_limits.csv",
        [
            "REGION",
            "TECHNOLOGY",
            "YEAR",
            "VALUE",
            "VARIABLE_COST_USD_PER_GJ",
        ],
        zonal_rows,
    )

    reconciliation = []
    for year in YEARS:
        tier_sum = sum(caps[tier.technology][year] for tier in TIERS)
        bc_total = zonal_caps["SUPBCWB1"][year] + zonal_caps["SUPBCWB2"][year]
        national = tier_sum + bc_total
        reconciliation.append(
            {
                "year": year,
                "T1_pj": repr(caps["SUPBCWT1"][year]),
                "T2_pj": repr(caps["SUPBCWT2"][year]),
                "T3_pj": repr(caps["SUPBCWT3"][year]),
                "T4_pj": repr(caps["SUPBCWT4"][year]),
                "tier_sum_pj": repr(tier_sum),
                "rest_of_canada_pj": repr(rest[year]),
                "SUPBCWB1_pj": repr(zonal_caps["SUPBCWB1"][year]),
                "SUPBCWB2_pj": repr(zonal_caps["SUPBCWB2"][year]),
                "national_total_pj": repr(national),
                "tier_sum_equals_rest": abs(tier_sum - rest[year]) < 1e-6,
                "national_equals_200m_total": abs(national - NATIONAL_TOTAL_PJ) < 1e-6,
            }
        )
    _write_csv(
        output_dir / "national_cap_reconciliation.csv",
        list(reconciliation[0]),
        reconciliation,
    )

    payload = {
        "schema_version": "0.1.0",
        "basis": {
            "harvest_m3_per_year": 200_000_000.0,
            "national_total_pj": NATIONAL_TOTAL_PJ,
            "bc_zonal_total_pj": BC_ZONAL_TOTAL_PJ,
            "rest_of_canada_pj": REST_OF_CANADA_PJ,
            "bc_zonal_total_by_year": {
                str(year): round(
                    zonal_caps["SUPBCWB1"][year] + zonal_caps["SUPBCWB2"][year], 9
                )
                for year in YEARS
            },
            "rest_of_canada_by_year": {
                str(year): round(value, 9) for year, value in rest.items()
            },
        },
        "tiers": [
            {
                "tier": tier.label,
                "technology": tier.technology,
                "lower_pj": tier.lower_pj,
                "upper_pj": tier.upper_pj,
                "incremental_cap_pj": tier.incremental_cap_pj,
                "variable_cost_usd_per_gj": tier.variable_cost_usd_per_gj,
                "scenario_flag": tier.scenario_flag,
            }
            for tier in TIERS
        ],
        "checks": {
            "tier_cap_sum_pj": round(
                sum(tier.incremental_cap_pj for tier in TIERS), 9
            ),
            "tier_cap_sum_equals_rest": abs(
                sum(tier.incremental_cap_pj for tier in TIERS) - REST_OF_CANADA_PJ
            )
            < 1e-9,
            "national_total_equals_200m": abs(
                REST_OF_CANADA_PJ + BC_ZONAL_TOTAL_PJ - NATIONAL_TOTAL_PJ
            )
            < 1e-9,
            "all_years_reconcile": all(
                row["tier_sum_equals_rest"] and row["national_equals_200m_total"]
                for row in reconciliation
            ),
            "SUPBCW_cap_disabled": True,
        },
        "caveats": [
            "Tier costs are adopted scenario values, not a fitted marginal-cost curve.",
            "T4 is a documented marginal high-cost tier, flagged as scenario.",
            "This is an RSYC-only curve: no AAC, THLB, operability, or local correction applies.",
        ],
    }
    _json(output_dir / "supply_curve_definition.json", payload)
    return payload


# ---------------------------------------------------------------------------
# Disposable model copy
# ---------------------------------------------------------------------------


def _venv_python(model_root: Path) -> Path:
    candidate = model_root / ".venv/bin/python"
    return candidate if candidate.exists() else Path(sys.executable)


def install_tier_structure(model_root: Path, params_dir: Path) -> None:
    """Add the four tier technologies to the disposable OSeCAN model.

    Args:
        model_root: Disposable OSeCAN copy.
        params_dir: Parameter directory to receive the base tier rows.

    Raises:
        FileNotFoundError: If an expected model set is missing.
        ValueError: If a tier technology already exists with conflicting rows.
    """
    technology_path = model_root / "SETs/TECHNOLOGY.csv"
    output_path = model_root / "SETs/OutputActivityRatio.csv"
    if not technology_path.exists() or not output_path.exists():
        raise FileNotFoundError("OSeCAN SETs/TECHNOLOGY.csv or OutputActivityRatio.csv missing")

    fields, technologies = _read_rows(technology_path)
    tier_names = {tier.technology for tier in TIERS}
    technologies = [row for row in technologies if row["VALUE"] not in tier_names]
    _write_csv(
        technology_path,
        fields,
        technologies + [{"VALUE": tier.technology} for tier in TIERS],
    )

    ratio_fields, ratios = _read_rows(output_path)
    ratios = [row for row in ratios if row["TECHNOLOGY"] not in tier_names]
    new_ratios = [
        {
            "REGION": "CAN",
            "TECHNOLOGY": tier.technology,
            "FUEL": "BCW",
            "MODE_OF_OPERATION": "1",
            "YEAR": year,
            "VALUE": "1",
        }
        for tier in TIERS
        for year in YEARS
    ]
    _write_csv(output_path, ratio_fields, ratios + new_ratios)

    for path, rows in (
        (
            params_dir / "OperationalLife.csv",
            [
                {"REGION": "CAN", "TECHNOLOGY": tier.technology, "VALUE": "1"}
                for tier in TIERS
            ],
        ),
        (
            params_dir / "CapacityToActivityUnit.csv",
            [
                {"REGION": "CAN", "TECHNOLOGY": tier.technology, "VALUE": "1.0"}
                for tier in TIERS
            ],
        ),
    ):
        for row in rows:
            _upsert_rows(path, row["TECHNOLOGY"], [row])


def _hydro_2021(run_dir: Path) -> dict[str, float]:
    path = run_dir / "results_csv/ProductionByTechnologyAnnual.csv"
    _, rows = _read_rows(path)
    values = {
        row["TECHNOLOGY"]: float(row["VALUE"])
        for row in rows
        if row["TECHNOLOGY"].startswith("PWRHYD") and row["YEAR"] == "2021"
    }
    if len(values) != 13:
        raise ValueError(f"expected 13 PWRHYD 2021 values, found {len(values)}")
    return values


def _set_carbon_price(path: Path, from_2026: float) -> None:
    fields, rows = _read_rows(path)
    for row in rows:
        row["VALUE"] = repr(
            CARBON_PRICE_PRE_2026 if int(row["YEAR"]) <= 2025 else from_2026
        )
    _write_csv(path, fields, rows)


def build_scenario_params(
    model_root: Path,
    artifact_dir: Path,
    spec: Mapping[str, object],
    *,
    hydro_2021: Mapping[str, float] | None = None,
) -> Path:
    """Build a scenario parameter directory in the disposable model copy.

    Args:
        model_root: Disposable OSeCAN copy.
        artifact_dir: Directory holding the committed tier CSVs.
        spec: Run specification.
        hydro_2021: Optional 2021 PWRHYD activity for capped hydro runs.

    Returns:
        The scenario parameter directory.

    Raises:
        FileNotFoundError: If a required CSV is missing.
        ValueError: If a hydro-capped run lacks 2021 hydro activity.
    """
    name = str(spec["name"])
    target = model_root / "results" / name / "params"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for source in (model_root / "data/params").glob("*.csv"):
        shutil.copyfile(source, target / source.name)

    for tier in TIERS:
        _upsert_rows(
            target / "TotalTechnologyAnnualActivityUpperLimit.csv",
            tier.technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": tier.technology,
                    "YEAR": str(year),
                    "VALUE": _tier_cap_from_artifact(artifact_dir, tier.technology, year),
                }
                for year in YEARS
            ],
        )
        _upsert_rows(
            target / "VariableCost.csv",
            tier.technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": tier.technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": str(year),
                    "VALUE": repr(tier.variable_cost_usd_per_gj),
                }
                for year in YEARS
            ],
        )

    _upsert_rows(
        target / "TotalTechnologyAnnualActivityUpperLimit.csv",
        "SUPBCW",
        [
            {"REGION": "CAN", "TECHNOLOGY": "SUPBCW", "YEAR": str(year), "VALUE": "0.0"}
            for year in YEARS
        ],
    )

    supply_dir = REPO_ROOT / SUPPLY_DIR
    for technology in ("SUPBCWB1", "SUPBCWB2"):
        caps = _read_series(
            supply_dir / f"{technology}_activity_limits_base.csv", technology
        )
        costs = _read_series(
            supply_dir / f"{technology}_variable_cost_base.csv", technology
        )
        _upsert_rows(
            target / "TotalTechnologyAnnualActivityUpperLimit.csv",
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "YEAR": str(year),
                    "VALUE": repr(caps[year]),
                }
                for year in YEARS
            ],
        )
        _upsert_rows(
            target / "VariableCost.csv",
            technology,
            [
                {
                    "REGION": "CAN",
                    "TECHNOLOGY": technology,
                    "MODE_OF_OPERATION": "1",
                    "YEAR": str(year),
                    "VALUE": repr(costs[year]),
                }
                for year in YEARS
            ],
        )

    _set_carbon_price(
        target / "EmissionsPenalty.csv", float(spec["carbon_price_from_2026"])
    )

    hydro_mode = str(spec["hydro_mode"])
    if hydro_mode != "normal":
        if not hydro_2021:
            raise ValueError(f"{name}: hydro cap requested without 2021 activity")
        for technology, value in hydro_2021.items():
            _upsert_rows(
                target / "TotalTechnologyAnnualActivityUpperLimit.csv",
                technology,
                [
                    {
                        "REGION": "CAN",
                        "TECHNOLOGY": technology,
                        "YEAR": str(year),
                        "VALUE": repr(float(value)),
                    }
                    for year in YEARS
                ],
            )
            if hydro_mode == "fixed_2021":
                lower_path = target / "TotalTechnologyAnnualActivityLowerLimit.csv"
                if not lower_path.exists():
                    _write_csv(
                        lower_path,
                        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
                        [],
                    )
                _upsert_rows(
                    lower_path,
                    technology,
                    [
                        {
                            "REGION": "CAN",
                            "TECHNOLOGY": technology,
                            "YEAR": str(year),
                            "VALUE": repr(float(value)),
                        }
                        for year in YEARS
                    ],
                )
    return target


def _tier_cap_from_artifact(
    artifact_dir: Path, technology: str, year: int
) -> str:
    path = artifact_dir / "tier_activity_limits.csv"
    _, rows = _read_rows(path)
    for row in rows:
        if row["TECHNOLOGY"] == technology and int(row["YEAR"]) == year:
            return row["VALUE"]
    raise ValueError(f"{path}: no cap row for {technology} {year}")


def harmonize(model_root: Path, params_dir: Path) -> dict[str, object]:
    """Run ``osecan harmonize`` and return the evidence payload.

    Args:
        model_root: Disposable OSeCAN copy.
        params_dir: Parameter directory to validate.

    Returns:
        Evidence with exit code, hashes, and the final tool message.

    Raises:
        RuntimeError: If harmonization does not pass.
    """
    python = _venv_python(model_root)
    completed = subprocess.run(
        [
            str(python),
            "-m",
            "osecan.cli",
            "harmonize",
            "--sets",
            "SETs",
            "--params",
            str(params_dir),
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
    payload = {
        "params": str(params_dir),
        "exit_code": completed.returncode,
        "message": message,
        "params_sha256": _parameter_set_sha256(params_dir),
    }
    if completed.returncode != 0:
        raise RuntimeError(f"osecan harmonize failed for {params_dir}: {message}")
    return payload


def _parameter_set_sha256(path: Path) -> str:
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


def run_model(model_root: Path, params_dir: Path, run_name: str) -> dict[str, object]:
    """Assemble, convert, and solve one run in the disposable model copy.

    Args:
        model_root: Disposable OSeCAN copy.
        params_dir: Scenario parameter directory.
        run_name: Result directory name below ``results``.

    Returns:
        Solver status payload.

    Raises:
        subprocess.CalledProcessError: If assembly, conversion, or solve fails.
    """
    python = _venv_python(model_root)
    run_dir = model_root / "results" / run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    input_dir = run_dir / "input"
    log_path = run_dir / "pipeline.log"

    # A copied model tree can contain a stale preprocessed datafile from the
    # source run.  solve.solve reuses osecan_pp.txt when it exists, so stale
    # files would silently solve the wrong cap set.
    for stale in (
        "input",
        "osecan.txt",
        "osecan_pp.txt",
        "results_csv",
        "status.json",
        "solution.sol",
        "model.lp",
    ):
        target = run_dir / stale
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()

    commands = (
        [
            str(python),
            "-m",
            "osecan.pipeline.assemble",
            "--sets",
            "SETs",
            "--params",
            str(params_dir),
            "--config",
            "config/otoole.yaml",
            "--out",
            str(input_dir),
        ],
        [
            str(model_root / ".venv/bin/otoole"),
            "convert",
            "csv",
            "datafile",
            str(input_dir),
            str(run_dir / "osecan.txt"),
            str(model_root / "config/otoole.yaml"),
        ],
        [
            str(python),
            "-c",
            (
                "import json, sys; from pathlib import Path; "
                "from osecan.pipeline import solve; "
                "run = Path(sys.argv[1]); "
                "result = solve.solve(str(run), str(run / 'params')); "
                "(run / 'status.json').write_text(json.dumps(result, indent=2) + "
                "chr(10))"
            ),
            str(run_dir),
        ],
    )
    with log_path.open("w", encoding="utf-8") as log:
        for command in commands:
            log.write("$ " + " ".join(command) + "\n")
            completed = subprocess.run(
                command,
                cwd=model_root,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                raise subprocess.CalledProcessError(completed.returncode, command)
    return read_status(run_dir)


# ---------------------------------------------------------------------------
# Result readers
# ---------------------------------------------------------------------------


def read_status(run_dir: Path) -> dict[str, object]:
    path = run_dir / "status.json"
    if not path.exists():
        raise FileNotFoundError(f"missing solver status: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def read_production(run_dir: Path) -> dict[str, dict[int, float]]:
    path = run_dir / "results_csv/ProductionByTechnologyAnnual.csv"
    _, rows = _read_rows(path)
    production: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        production[row["TECHNOLOGY"]][int(row["YEAR"])] = float(row["VALUE"])
    return dict(production)


def read_emissions(run_dir: Path) -> dict[int, float]:
    path = run_dir / "results_csv/AnnualEmissions.csv"
    _, rows = _read_rows(path)
    emissions: dict[int, float] = defaultdict(float)
    for row in rows:
        emissions[int(row["YEAR"])] += float(row["VALUE"])
    return dict(sorted(emissions.items()))


def cap_binding(
    production: Mapping[str, Mapping[int, float]],
    caps: Mapping[str, Mapping[int, float]],
    *,
    tolerance: float = 1e-6,
    relative_tolerance: float = 1e-5,
) -> dict[str, object]:
    """Report tier utilisation and binding years.

    The result CSVs round activity to six decimals, so binding uses a small
    relative tolerance in addition to an absolute floor.

    Args:
        production: Technology year-to-activity mapping.
        caps: Technology year-to-cap mapping.
        tolerance: Absolute PJ tolerance floor.
        relative_tolerance: Fraction of the cap treated as binding.

    Returns:
        Per-technology activity, cap, utilisation, and binding years.
    """
    payload: dict[str, object] = {}
    for technology, cap_series in caps.items():
        activity = production.get(technology, {})
        utilisation = {}
        binding_years = []
        for year in sorted(cap_series):
            cap = float(cap_series[year])
            value = float(activity.get(year, 0.0))
            utilisation[str(year)] = round(value / cap, 6) if cap > 0 else 0.0
            threshold = max(tolerance, cap * relative_tolerance)
            if cap > 0 and value >= cap - threshold:
                binding_years.append(year)
        payload[technology] = {
            "activity_pj": {str(y): round(float(activity.get(y, 0.0)), 6) for y in KEY_YEARS},
            "cap_pj": {str(y): round(float(cap_series[y]), 6) for y in KEY_YEARS},
            "utilisation": {y: utilisation[y] for y in map(str, KEY_YEARS)},
            "binding": bool(binding_years),
            "binding_years": binding_years,
            "first_binding_year": binding_years[0] if binding_years else None,
            "last_binding_year": binding_years[-1] if binding_years else None,
            "max_utilisation": max(utilisation.values(), default=0.0),
        }
    return payload


# ---------------------------------------------------------------------------
# Summaries, dispatch, and report
# ---------------------------------------------------------------------------


def _series_summary(series: Mapping[int, float]) -> dict[str, object]:
    return {
        "by_year": {str(y): round(float(v), 6) for y, v in sorted(series.items())},
        "total": round(sum(float(v) for v in series.values()), 6),
        **{str(y): round(float(series.get(y, 0.0)), 6) for y in KEY_YEARS},
    }


def summarize_run(
    name: str,
    run_dir: Path,
    tier_cap_series: Mapping[str, Mapping[int, float]],
) -> dict[str, object]:
    """Build the JSON summary for one solved run."""
    status = read_status(run_dir)
    if status.get("status") != "optimal":
        raise RuntimeError(f"{name}: solver status {status.get('status')!r}")
    production = read_production(run_dir)
    emissions = read_emissions(run_dir)

    tier_activity = {
        tier.technology: {
            year: float(production.get(tier.technology, {}).get(year, 0.0))
            for year in YEARS
        }
        for tier in TIERS
    }
    total_bcw = {
        year: sum(tier_activity[tier.technology][year] for tier in TIERS)
        for year in YEARS
    }
    bc_zonal = {
        year: sum(
            float(production.get(technology, {}).get(year, 0.0))
            for technology in ("SUPBCWB1", "SUPBCWB2")
        )
        for year in YEARS
    }
    national_bcw = {
        year: total_bcw[year] + bc_zonal[year] for year in YEARS
    }
    pwrbcw = {
        technology: series
        for technology, series in production.items()
        if technology.startswith("PWRBCW")
    }
    pwrbcw_other = {
        technology: series
        for technology, series in pwrbcw.items()
        if technology not in ("PWRBCWB1", "PWRBCWB2")
    }
    pwrbcw_total = {
        year: sum(series.get(year, 0.0) for series in pwrbcw.values())
        for year in YEARS
    }
    pwrbcw_other_total = {
        year: sum(series.get(year, 0.0) for series in pwrbcw_other.values())
        for year in YEARS
    }
    pwrbcw_fuel_input = {
        year: 3.72 * pwrbcw_total[year] for year in YEARS
    }
    balance_residual = {
        year: national_bcw[year] - pwrbcw_fuel_input[year] for year in YEARS
    }
    binding = cap_binding(production, tier_cap_series)

    return {
        "label": next(spec["label"] for spec in RUN_SPECS if spec["name"] == name),
        "status": status,
        "objective": status.get("objective"),
        "tier_activity_pj": {
            tier.technology: _series_summary(tier_activity[tier.technology])
            for tier in TIERS
        },
        "total_tier_activity_pj": _series_summary(total_bcw),
        "bc_zonal_activity_pj": _series_summary(bc_zonal),
        "national_bcw_supply_pj": _series_summary(national_bcw),
        "pwrbcw_fuel_input_pj": _series_summary(pwrbcw_fuel_input),
        "bcw_balance": {
            "residual_pj": {
                str(year): round(balance_residual[year], 6) for year in YEARS
            },
            "max_abs_residual_pj": round(
                max(abs(value) for value in balance_residual.values()), 6
            ),
        },
        "pwrbcw_dispatch_pj": {
            technology: _series_summary(series)
            for technology, series in sorted(pwrbcw.items())
        },
        "pwrbcw_total_pj": _series_summary(pwrbcw_total),
        "pwrbcw_non_bc_total_pj": _series_summary(pwrbcw_other_total),
        "cap_binding": binding,
        "emissions_mt": _series_summary(emissions),
        "emissions_total_mt": round(sum(emissions.values()), 6),
    }


def build_dispatch_rows(
    name: str,
    run_dir: Path,
) -> list[dict[str, object]]:
    """Return long-format dispatch rows for supply and BCW power technologies."""
    production = read_production(run_dir)
    rows: list[dict[str, object]] = []
    for technology, series in sorted(production.items()):
        if technology in {tier.technology for tier in TIERS}:
            category, zone = "supply_tier", "CAN"
        elif technology in ("SUPBCWB1", "SUPBCWB2"):
            category, zone = "supply_bc_zonal", technology[-2:]
        elif technology.startswith("PWRBCW"):
            category, zone = "power_bcw", technology[-2:]
        elif technology.startswith("PWRBIO"):
            category, zone = "power_bio", technology[-2:]
        elif technology.startswith("PWRHYD"):
            category, zone = "power_hydro", technology[-2:]
        else:
            continue
        for year, value in sorted(series.items()):
            rows.append(
                {
                    "run": name,
                    "technology": technology,
                    "category": category,
                    "zone": zone,
                    "year": year,
                    "value_pj": round(float(value), 6),
                }
            )
    return rows


def _fmt(value: object, digits: int = 6) -> str:
    return f"{float(value):.{digits}f}"


def _series_cell(series: Mapping[str, object], year: int) -> str:
    return _fmt(series.get(str(year), 0.0))


def write_report(
    output_dir: Path,
    summaries: Mapping[str, object],
    definition: Mapping[str, object],
    comparison: Mapping[str, object],
) -> None:
    """Write the human-readable national supply-curve report."""
    tiers = definition["tiers"]
    runs = summaries["runs"]

    lines = [
        "# National rest-of-Canada biomass supply curve (OSeCAN)",
        "",
        "**Status:** FRESH-side provisional, not an official C2070 result.  ",
        "**Basis:** RSYC-only v0 at 200M m3/yr log volume, no AAC/THLB/local corrections.",
        "",
        "## 1. Tier structure",
        "",
        "| Tier | Technology | Interval (PJ) | Incremental cap (PJ) | Cost ($/GJ) | Flag |",
        "|---|---|---|---|---|---|",
    ]
    for tier in tiers:
        lines.append(
            f"| {tier['tier']} | `{tier['technology']}` | "
            f"{_fmt(tier['lower_pj'])}–{_fmt(tier['upper_pj'])} | "
            f"{_fmt(tier['incremental_cap_pj'])} | "
            f"{_fmt(tier['variable_cost_usd_per_gj'])} | {tier['scenario_flag']} |"
        )
    lines.extend(
        [
            "",
            "The tier caps sum to the rest-of-Canada cap "
            f"({_fmt(REST_OF_CANADA_PJ)} PJ).  The BC zonal `SUPBCWB1` / "
            f"`SUPBCWB2` caps are unchanged and sum to "
            f"{_fmt(BC_ZONAL_TOTAL_PJ)} PJ from 2028.  Tiers plus BC zones "
            f"equal the national total {_fmt(NATIONAL_TOTAL_PJ)} PJ.  The single "
            "`SUPBCW` cap is set to zero so the four tiers cannot double count "
            "the rest-of-Canada supply.",
            "",
            "T1-T3 keep their adopted incremental caps in every year.  T4 absorbs "
            "the existing 2021-2027 BC zonal phase-down, so the tier sum equals "
            "the rest-of-Canada cap in every year.",
            "",
            "## 2. Results",
            "",
            "| Run | Status | Objective | Tier activity 2050 (PJ) | BCW supply 2050 (PJ) | PWRBCW 2050 (PJ) | Emissions 2050 (Mt) |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for name, run in runs.items():
        lines.append(
            f"| {name} | {run['status'].get('status')} | "
            f"{run['objective']} | "
            f"{_series_cell(run['total_tier_activity_pj']['by_year'], 2050)} | "
            f"{_series_cell(run['national_bcw_supply_pj']['by_year'], 2050)} | "
            f"{_series_cell(run['pwrbcw_total_pj']['by_year'], 2050)} | "
            f"{_series_cell(run['emissions_mt']['by_year'], 2050)} |"
        )
    lines.extend(["", "### Tier dispatch", ""])
    for name, run in runs.items():
        lines.extend([f"#### {name}", ""])
        lines.append(
            "| Year | T1 | T2 | T3 | T4 | Total tiers | PWRBCW total | Emissions |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for year in KEY_YEARS:
            lines.append(
                f"| {year} | "
                f"{_series_cell(run['tier_activity_pj']['SUPBCWT1']['by_year'], year)} | "
                f"{_series_cell(run['tier_activity_pj']['SUPBCWT2']['by_year'], year)} | "
                f"{_series_cell(run['tier_activity_pj']['SUPBCWT3']['by_year'], year)} | "
                f"{_series_cell(run['tier_activity_pj']['SUPBCWT4']['by_year'], year)} | "
                f"{_series_cell(run['total_tier_activity_pj']['by_year'], year)} | "
                f"{_series_cell(run['pwrbcw_total_pj']['by_year'], year)} | "
                f"{_series_cell(run['emissions_mt']['by_year'], year)} |"
            )
        lines.append("")
        lines.append("**Cap binding:**")
        for technology, binding in run["cap_binding"].items():
            years = binding["binding_years"]
            span = f"{years[0]}–{years[-1]}" if years else "none"
            lines.append(
                f"- `{technology}`: binding={binding['binding']}, "
                f"years={span}, max utilisation={binding['max_utilisation']:.6f}"
            )
        lines.append("")

    lines.extend(
        [
            "## 3. Comparison with single-cap cases",
            "",
            "| Case | CO2 from 2026 | Rest cap 2028 (PJ) | Objective | SUPBCW 2050 (PJ) | PWRBCW 2050 (PJ) |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in comparison["rows"]:
        lines.append(
            f"| {row['case']} | {row['carbon_price']} | {row['cap_2028_pj']} | "
            f"{row['objective']} | {row['supbcw_2050_pj']} | {row['pwrbcw_2050_pj']} |"
        )
    lines.extend(
        [
            "",
            "The single-cap 200M baseline is not present in the retained run set; "
            "the 200M comparison is the stress case only.  The tiered curve uses "
            "the same caps but a higher marginal cost above 109.4 PJ, so it is "
            "expected to differ from the flat-cost single-cap case.",
            "",
        ]
    )
    stress_run = runs.get("stress")
    if stress_run:
        bound = [
            technology
            for technology, binding in stress_run["cap_binding"].items()
            if binding["binding"]
        ]
        idle = [
            technology
            for technology, binding in stress_run["cap_binding"].items()
            if not binding["binding"]
        ]
        lines.extend(
            [
                "In the $200/t hydro-capped stress the binding tiers are "
                f"{', '.join(f'`{t}`' for t in bound) if bound else 'none'}.  "
                f"The non-binding tiers are "
                f"{', '.join(f'`{t}`' for t in idle) if idle else 'none'}.  "
                "The model stops at the 109.4 PJ boundary because the next tier "
                "costs 3.194 $/GJ, above the 3.0 $/GJ generic RNWBIO substitute; "
                "this is an economic result of the adopted scenario costs, not a "
                "cap or structural exclusion.",
                "",
            ]
        )
    lines.extend(
        [
            "## 4. Caveats",
            "",
            "- Tier costs are scenario values, not a fitted marginal-cost curve.  "
            "T4 at 3.50 $/GJ is a documented high-cost marginal tier.",
            "- The curve is RSYC-only: no AAC, THLB, operability, or local correction "
            "factor is applied.",
            "- The hydro stress is an annual upper bound at 2021 activity.  The "
            "fixed-hydro variant additionally sets the annual lower bound equal "
            "to that activity; neither is a forecast.",
            "- Results are for the disposable model copy and do not modify the "
            "public OSeCAN repository.",
            "",
        ]
    )
    (output_dir / "NATIONAL_SUPPLY_CURVE_REPORT.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def build_manifest(
    output_dir: Path,
    definition: Mapping[str, object],
    summaries: Mapping[str, object],
    harmonize_evidence: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build and write the artifact manifest."""
    inputs = [
        REPO_ROOT / SUPPLY_DIR / name
        for name in (
            "SUPBCWB1_activity_limits_base.csv",
            "SUPBCWB1_variable_cost_base.csv",
            "SUPBCWB2_activity_limits_base.csv",
            "SUPBCWB2_variable_cost_base.csv",
        )
    ]
    output_names = (
        "tier_definitions.csv",
        "tier_activity_limits.csv",
        "tier_variable_costs.csv",
        "bc_zonal_activity_limits.csv",
        "national_cap_reconciliation.csv",
        "supply_curve_definition.json",
        "run_summaries.json",
        "dispatch_by_zone.csv",
        "NATIONAL_SUPPLY_CURVE_REPORT.md",
    )
    manifest = {
        "artifact_version": "0.1.0",
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "status": STATUS,
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "scenario": MODEL_SCENARIO,
        },
        "inputs": [
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in inputs
        ],
        "outputs": [
            {
                "path": name,
                "bytes": (output_dir / name).stat().st_size,
                "sha256": _sha256(output_dir / name),
            }
            for name in output_names
        ],
        "tier_structure": definition["tiers"],
        "checks": definition["checks"],
        "harmonize": list(harmonize_evidence),
        "runs": {
            name: {
                "status": run["status"].get("status"),
                "objective": run["objective"],
                "emissions_total_mt": run["emissions_total_mt"],
            }
            for name, run in summaries["runs"].items()
        },
        "caveats": definition["caveats"]
        + [
            "The hydro cap is an annual upper bound, not fixed dispatch; the "
            "fixed-hydro variant is a separate sensitivity.",
            "The public OSeCAN repository was not modified.",
        ],
    }
    _json(output_dir / "manifest.json", manifest)
    return manifest


# ---------------------------------------------------------------------------
# Comparison cases
# ---------------------------------------------------------------------------


def _comparison_case(
    case: str,
    run_dir: Path,
    *,
    carbon_price: str,
) -> dict[str, object]:
    status = read_status(run_dir)
    production = read_production(run_dir)
    cap_path = run_dir / "input/TotalTechnologyAnnualActivityUpperLimit.csv"
    caps = _read_series(cap_path, "SUPBCW")
    pwrbcw = sum(
        series.get(2050, 0.0)
        for technology, series in production.items()
        if technology.startswith("PWRBCW")
    )
    return {
        "case": case,
        "carbon_price": carbon_price,
        "cap_2028_pj": round(caps[2028], 6),
        "objective": status.get("objective"),
        "supbcw_2050_pj": round(production.get("SUPBCW", {}).get(2050, 0.0), 6),
        "pwrbcw_2050_pj": round(pwrbcw, 6),
    }


def build_comparison() -> dict[str, object]:
    """Read retained single-cap comparison runs, when available."""
    rows = []
    candidates = (
        (
            "119M baseline",
            Path("/tmp/osecan_national/results/baseline_119m"),
            "95/115",
        ),
        (
            "150M baseline",
            Path("/tmp/osecan_national/results/baseline"),
            "95/115",
        ),
        (
            "119M stress",
            Path("/tmp/osecan_national/results/stress_119m"),
            "95/200",
        ),
        (
            "150M stress",
            Path("/tmp/osecan_national/results/stress"),
            "95/200",
        ),
        (
            "200M stress",
            Path("/tmp/osecan_sens_100_200/results/harvest_200m_stress"),
            "95/200",
        ),
    )
    for case, run_dir, carbon_price in candidates:
        if (run_dir / "status.json").exists():
            rows.append(
                _comparison_case(case, run_dir, carbon_price=carbon_price)
            )
    return {"rows": rows}


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build and optionally run the national rest-of-Canada supply curve."
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        required=True,
        help="Disposable OSeCAN copy used for execution.",
    )
    parser.add_argument(
        "--runs",
        nargs="*",
        default=["baseline", "stress", "stress_fixed_hydro"],
        help="Run names to execute.",
    )
    parser.add_argument(
        "--skip-runs",
        action="store_true",
        help="Write parameter artifacts only; do not solve.",
    )
    parser.add_argument(
        "--reuse-runs",
        action="store_true",
        help="Rebuild summaries from existing solved runs without re-solving.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / OUTPUT_DIR,
        help="Artifact output directory.",
    )
    args = parser.parse_args(argv)

    model_root = args.model_root.resolve()
    if not (model_root / "SETs/TECHNOLOGY.csv").exists():
        raise SystemExit(f"not an OSeCAN copy: {model_root}")

    definition = write_parameter_files()
    install_tier_structure(model_root, model_root / "data/params")
    harmonize_evidence = [harmonize(model_root, model_root / "data/params")]

    if args.skip_runs:
        print(f"parameter artifacts -> {args.output_dir}")
        return 0

    summaries: dict[str, object] = {"runs": {}}
    dispatch_rows: list[dict[str, object]] = []
    caps = tier_caps(
        {
            technology: _read_series(
                REPO_ROOT
                / SUPPLY_DIR
                / f"{technology}_activity_limits_base.csv",
                technology,
            )
            for technology in ("SUPBCWB1", "SUPBCWB2")
        }
    )
    hydro_2021 = None
    for name in args.runs:
        spec = next((spec for spec in RUN_SPECS if spec["name"] == name), None)
        if spec is None:
            raise SystemExit(f"unknown run {name!r}")
        run_dir = model_root / "results" / name
        if args.reuse_runs:
            read_status(run_dir)
            harmonize_evidence.append(harmonize(model_root, run_dir / "params"))
        else:
            params_dir = build_scenario_params(
                model_root,
                args.output_dir,
                spec,
                hydro_2021=hydro_2021,
            )
            harmonize_evidence.append(harmonize(model_root, params_dir))
            run_model(model_root, params_dir, name)
        summaries["runs"][name] = summarize_run(name, run_dir, caps)
        dispatch_rows.extend(build_dispatch_rows(name, run_dir))
        if name == "baseline":
            hydro_2021 = _hydro_2021(run_dir)

    _json(args.output_dir / "run_summaries.json", summaries)
    _write_csv(
        args.output_dir / "dispatch_by_zone.csv",
        ["run", "technology", "category", "zone", "year", "value_pj"],
        dispatch_rows,
    )
    comparison = build_comparison()
    write_report(args.output_dir, summaries, definition, comparison)
    build_manifest(args.output_dir, definition, summaries, harmonize_evidence)
    print(json.dumps({name: run["objective"] for name, run in summaries["runs"].items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
