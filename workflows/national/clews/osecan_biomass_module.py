"""OSeCAN availability evidence and reduced biomass-module economics.

The C2070 OSeCAN model is a public ``uv`` project (``osecan``), not a PyPI
distribution.  The model is runnable with the shipped GLPK bindings, but its
current technology vocabulary has a single national ``SUPBCW`` supply
technology.  It has no ``SUPBCWB1`` or ``SUPBCWB2`` code, so the FRESH-side
zonal parameter files cannot be loaded without a model-structure change.

This module records what was actually available and runnable, and builds the
reduced biomass-module analysis that is independent of that model gap:

* per-MWh feedstock cost and simplified LCOE for PWRBCW and PWRBIO;
* the BCW price at which PWRBCW breaks even with generic BIO;
* the BCW price and carbon price at which PWRBCW breaks even with the model's
  placeholder gas plant;
* the maximum electricity that the low/base/high BC caps could support; and
* the cap-binding conclusion, including the generic-BIO bypass.

All plant parameters are the documented OSeCAN v0.3 placeholders:

* ``src/osecan/model_structure.py``: PWRBCW IAR 3.72 (26.9% efficiency),
  PWRBIO IAR 2.85 (35.1%), PWRBSW IAR 3.72, PWRNGS IAR 2.70, PWRHYD IAR 1.00.
* ``src/osecan/data/build_params.py``: capital/fixed/variable costs, life,
  capacity factor, and gas CO2 factor (0.051 Mt/PJ = 0.051 t/GJ).
* ``docs/model-description.md`` and ``README.md``: 2021-2070 horizon, 5%
  discount rate, 2020 CAD, $95/t CO2 in 2026 rising to $115/t in 2030.

The model run evidence is recorded from the actual runs on 2026-11-17 against
``github.com/deltae/C2070`` commit ``40d5b43bb3f8b3f69c16e953144d35eeed5f0f44``.
It is not a substitute for re-running the model; it is kept here so the
reduced analysis and the run report cannot drift apart.

    python -m workflows.national.clews.osecan_biomass_module --root .
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "AVAILABILITY",
    "CASES",
    "DISCOUNT_RATE",
    "HORIZON_END",
    "HORIZON_START",
    "MODEL_RUNS",
    "OUTPUT_DIR",
    "PLANTS",
    "SENSITIVITY_RUNS",
    "SUPPLY_DIR",
    "CaseSupply",
    "Lcoe",
    "Plant",
    "build_all",
    "build_availability",
    "build_bcw_vs_bio_rows",
    "build_cap_binding",
    "build_cap_metrics",
    "build_lcoe_rows",
    "build_manifest",
    "build_sensitivity",
    "build_sensitivity_rows",
    "capital_recovery_factor",
    "load_all_cases",
    "load_case_supply",
    "plant_lcoe",
    "write_artifacts",
]

REPO_ROOT = Path(__file__).resolve().parents[3]
SUPPLY_DIR = Path("data/derived/national/clews")
OUTPUT_DIR = SUPPLY_DIR / "osecan"

HORIZON_START = 2021
HORIZON_END = 2070
YEARS = tuple(range(HORIZON_START, HORIZON_END + 1))
DISCOUNT_RATE = 0.05
HOURS_PER_YEAR = 8760.0
GJ_PER_MWH = 3.6
BCW_EFFICIENCY = 1.0 / 3.72
BIO_EFFICIENCY = 1.0 / 2.85

MODEL_COMMIT = "40d5b43bb3f8b3f69c16e953144d35eeed5f0f44"
MODEL_REPOSITORY = "https://github.com/deltae/C2070"
MODEL_PACKAGE = "osecan 0.1.0"
MODEL_SOLVER = "GLPK via swiglpk 5.0.13"
MODEL_SCENARIO = "Current Measures"

CASES = ("low", "base", "high")


# Documented OSeCAN v0.3 plant parameters.  ``emission_factor`` is in
# t CO2 per GJ of technology activity, which is numerically equal to the
# documented Mt/PJ value.
@dataclass(frozen=True, slots=True)
class Plant:
    """A dispatchable plant's cost and conversion parameters."""

    name: str
    fuel: str
    input_activity_ratio: float
    capex_usd_per_kw: float
    fixed_usd_per_kw_yr: float
    var_usd_per_gj: float
    life_years: int
    capacity_factor: float
    emission_factor_t_per_gj: float = 0.0
    source: str = "OSeCAN v0.3 placeholder"


@dataclass(frozen=True, slots=True)
class Lcoe:
    """A simplified levelised cost of electricity, in 2020 CAD per MWh."""

    capital_fixed_usd_per_mwh: float
    fuel_usd_per_mwh: float
    carbon_usd_per_mwh: float
    var_usd_per_mwh: float
    total_usd_per_mwh: float
    crf: float
    co2_t_per_mwh: float


@dataclass(frozen=True, slots=True)
class CaseSupply:
    """One low/base/high BC woody-residue supply case."""

    case: str
    national_cap_pj: Mapping[int, float]
    national_cost_usd_per_gj: Mapping[int, float]
    b1_cap_pj: Mapping[int, float]
    b2_cap_pj: Mapping[int, float]
    b1_cost_usd_per_gj: Mapping[int, float]
    b2_cost_usd_per_gj: Mapping[int, float]


# The OSeCAN technology vocabulary has only national SUPBCW.  The zonal files
# are retained as evidence and as the source of the national rollup.
_PLANT_DEFINITIONS = {
    "PWRBCW": Plant(
        name="PWRBCW",
        fuel="BCW",
        input_activity_ratio=3.72,
        capex_usd_per_kw=3200.0,
        fixed_usd_per_kw_yr=95.0,
        var_usd_per_gj=0.0,
        life_years=30,
        capacity_factor=0.70,
    ),
    "PWRBIO": Plant(
        name="PWRBIO",
        fuel="BIO",
        input_activity_ratio=2.85,
        capex_usd_per_kw=3000.0,
        fixed_usd_per_kw_yr=90.0,
        var_usd_per_gj=2.5,
        life_years=30,
        capacity_factor=0.70,
    ),
    "PWRBSW": Plant(
        name="PWRBSW",
        fuel="BSW",
        input_activity_ratio=3.72,
        capex_usd_per_kw=3200.0,
        fixed_usd_per_kw_yr=95.0,
        var_usd_per_gj=0.0,
        life_years=30,
        capacity_factor=0.70,
    ),
    "PWRNGS": Plant(
        name="PWRNGS",
        fuel="NGS",
        input_activity_ratio=2.70,
        capex_usd_per_kw=1100.0,
        fixed_usd_per_kw_yr=25.0,
        var_usd_per_gj=3.0,
        life_years=30,
        capacity_factor=0.85,
        emission_factor_t_per_gj=0.051,
    ),
    "PWRHYD": Plant(
        name="PWRHYD",
        fuel="HYD",
        input_activity_ratio=1.00,
        capex_usd_per_kw=3500.0,
        fixed_usd_per_kw_yr=40.0,
        var_usd_per_gj=0.0,
        life_years=60,
        capacity_factor=0.45,
    ),
}
PLANTS = _PLANT_DEFINITIONS

# Fuel prices used by the model: generic BIO is the uncapped RNWBIO supply at
# $3/GJ; BSW is the 50 PJ placeholder at $4/GJ; BCW comes from the case files.
GENERIC_BIO_USD_PER_GJ = 3.0
BSW_USD_PER_GJ = 4.0
GAS_USD_PER_GJ = 3.0
CARBON_PRICE_USD_PER_T = {"2026": 95.0, "2030": 115.0}
PLACEHOLDER_BCW_USD_PER_GJ = 4.0
PLACEHOLDER_BCW_CAP_PJ = 50.0

# Recorded from the actual OSeCAN runs on 2026-11-17.  These are summaries,
# not a re-simulation; the exact solver output is in the run report's command
# log.  No biomass technology dispatched in any run.
MODEL_RUNS: Mapping[str, Mapping[str, object]] = {
    "baseline_placeholder": {
        "description": "50 PJ cap and $4/GJ placeholder on SUPBCW/SUPBSW",
        "status": "optimal",
        "objective_musd": 1_331_287.25,
        "rows": 273_001,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "pwr_bio_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "generation_2025_pj": {"HYD": 2264.877571},
        "generation_2050_pj": {"HYD": 3231.947788, "NGS": 20.221232},
        "emissions_mt": {
            "2021": 0.017136,
            "2025": 0.0,
            "2030": 1.031283,
            "2050": 1.031283,
            "2070": 0.0,
        },
        "emissions_total_mt": 25.749782,
        "cap_binding": False,
    },
    "bc_base_national_rollup": {
        "description": "BC base national SUPBCW rollup (B1+B2)",
        "status": "optimal",
        "objective_musd": 1_331_287.25,
        "rows": 273_001,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "pwr_bio_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "generation_2025_pj": {"HYD": 2264.877571},
        "generation_2050_pj": {"HYD": 3231.947788, "NGS": 20.221232},
        "emissions_mt": {
            "2021": 0.017136,
            "2025": 0.0,
            "2030": 1.031283,
            "2050": 1.031283,
            "2070": 0.0,
        },
        "emissions_total_mt": 25.749782,
        "cap_binding": False,
    },
    "diag_hydro_frozen_base": {
        "description": "BC base plus hydro activity frozen at 2021 (diagnostic)",
        "status": "optimal",
        "objective_musd": 1_346_217.65,
        "rows": 273_651,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "pwr_bio_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "generation_2025_pj": {"HYD": 2229.3802, "NGS": 35.561897},
        "generation_2050_pj": {"HYD": 2230.54127, "NGS": 1021.62775},
        "emissions_mt": {
            "2021": 0.295305,
            "2025": 1.813657,
            "2030": 9.336774,
            "2050": 52.103015,
            "2070": 52.103015,
        },
        "cap_binding": False,
    },
    "diag_hydro_frozen_high": {
        "description": "BC high plus hydro activity frozen at 2021 (diagnostic)",
        "status": "optimal",
        "objective_musd": 1_346_217.65,
        "rows": 273_651,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "pwr_bio_pj": {"total": 0.0, "2025": 0.0, "2050": 0.0},
        "generation_2025_pj": {"HYD": 2229.3802, "NGS": 35.561897},
        "generation_2050_pj": {"HYD": 2230.54127, "NGS": 1021.62775},
        "emissions_mt": {
            "2021": 0.295305,
            "2025": 1.813657,
            "2030": 9.336774,
            "2050": 52.103015,
            "2070": 52.103015,
        },
        "cap_binding": False,
    },
}

# Policy sensitivity runs recorded on 2026-11-17.  Both were run in a disposable
# copy of the public repository at ``/tmp/osecan_sens``; the public clone was not
# modified.  The forcing conditions are deliberately narrow:
#
# * ``carbon_200``: EmissionsPenalty $200/t from 2026 onward; 2021-2025 keep the
#   original $95/t placeholder.  All other parameters are the BC-base rollup.
# * ``carbon_200_hydro_frozen``: the same carbon price plus a
#   TotalTechnologyAnnualActivityUpperLimit on every PWRHYD<zone> fixed at its
#   2021 base-run activity.  This is the previous hydro-frozen diagnostic with
#   the carbon price raised.
SENSITIVITY_RUNS: Mapping[str, Mapping[str, object]] = {
    "carbon_200": {
        "description": "BC base with a $200/t CO2 penalty from 2026",
        "forcing": (
            "EmissionsPenalty 95 $/t for 2021-2025 and 200 $/t from 2026; "
            "BC-base national SUPBCW rollup unchanged."
        ),
        "status": "optimal",
        "objective_musd": 1_331_527.92,
        "objective_delta_musd": 240.67,
        "rows": 273_001,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {
            "total": 0.0,
            "2021": 0.0,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
        },
        "pwr_bio_pj": {"total": 0.0},
        "pwr_hyd_pj": {
            "2021": 2230.744902,
            "2025": 2265.089874,
            "2030": 2413.615279,
            "2050": 3252.16902,
            "2070": 3252.16902,
        },
        "cap_binding": False,
        "cap_binding_years": "",
        "max_utilisation_fraction": 0.0,
        "emissions_mt": {
            "2021": 0.009171,
            "2025": 0.0,
            "2030": 0.0,
            "2050": 0.0,
            "2070": 0.0,
            "total": 1.856899,
        },
        "generation_2050_pj": {"HYD": 3252.16902},
    },
    "carbon_200_hydro_frozen": {
        "description": "BC base with $200/t CO2 and hydro frozen at 2021 activity",
        "forcing": (
            "EmissionsPenalty 95 $/t for 2021-2025 and 200 $/t from 2026; "
            "every PWRHYD<zone> activity upper limit fixed at its 2021 base-run "
            "activity in all years."
        ),
        "status": "optimal",
        "objective_musd": 1_371_553.35,
        "objective_delta_musd": 40_266.10,
        "rows": 273_651,
        "cols": 234_900,
        "supbcw_pj": {
            "total": 2104.9812,
            "2021": 0.0,
            "2022": 42.9588,
            "2025": 42.9588,
            "2028": 42.9588,
            "2030": 42.9588,
            "2040": 42.9588,
            "2050": 42.9588,
            "2060": 42.9588,
            "2070": 42.9588,
        },
        "supbsw_pj": {"total": 0.0},
        "pwrbcw_pj": {
            "total": 565.855166,
            "2021": 0.0,
            "2025": 11.548065,
            "2030": 11.548065,
            "2040": 11.548065,
            "2050": 11.548065,
            "2060": 11.548064,
            "2070": 11.548064,
        },
        "pwr_bio_pj": {"total": 0.0},
        "pwr_hyd_pj": {
            "2021": 2230.416314,
            "2025": 2230.299272,
            "2030": 2230.54127,
            "2050": 2230.54127,
            "2070": 2230.54127,
        },
        "pwrbcw_2050_by_zone_pj": {
            "A1": 0.048224,
            "A2": 0.532557,
            "S1": 2.786811,
            "Q1": 3.381588,
            "Q2": 2.033405,
            "T1": 2.588555,
            "L1": 0.176925,
        },
        "cap_binding": True,
        "cap_binding_years": "2028-2070",
        "max_utilisation_fraction": 1.0,
        "emissions_mt": {
            "2021": 0.023274,
            "2025": 0.42273,
            "2030": 1.647079,
            "2050": 1.669525,
            "2070": 0.0,
            "total": 43.990577,
        },
        "generation_2050_pj": {
            "HYD": 2230.54127,
            "WND": 977.343911,
            "NGS": 32.735776,
            "BCW": 11.548065,
        },
    },
}

# Recorded availability checks.  These are deliberately explicit: the package
# is not on PyPI, and the GitHub repository is public but has no zonal supply
# technology.
AVAILABILITY: Mapping[str, object] = {
    "checked_utc": "2026-11-17T19:15:00+00:00",
    "pypi": {
        "package": "osecan",
        "json_url": "https://pypi.org/pypi/osecan/json",
        "json_http_status": 404,
        "pip_index_versions": "ERROR: No matching distribution found for osecan",
        "pip_download": "ERROR: No matching distribution found for osecan",
        "pip_install": "ERROR: No matching distribution found for osecan",
        "available": False,
        "note": (
            "No PyPI distribution. The installable artifact is the public "
            "GitHub uv project, not a pip package."
        ),
    },
    "github": {
        "repositories": [
            {
                "url": "https://github.com/deltae/C2070",
                "status": "public",
                "head": MODEL_COMMIT,
                "checked_with": "git ls-remote",
            },
            {
                "url": "https://github.com/eliasinul/CLEWsCAN2070",
                "status": "public, redirects to deltae/C2070",
                "head": MODEL_COMMIT,
                "checked_with": "git ls-remote",
            },
            {
                "url": "https://github.com/UBC-FRESH/CLEWsCAN2070",
                "status": "not found (404)",
                "checked_with": "git ls-remote",
            },
        ],
        "private_repository_not_accessed": "one private repository matching 'osecan'",
        "note": (
            "The private repository was not cloned or read. The public "
            "deltae/C2070 repository was cloned to /tmp for the run."
        ),
    },
    "site": {
        "base": "https://deltae.github.io/C2070/",
        "pages": {
            "index": {"status": 200},
            "site/model.html": {"status": 200},
            "site/api.html": {"status": 200},
            "site/data.html": {"status": 200},
            "site/model-description.html": {"status": 200},
            "site/results.html": {"status": 200},
            "site/policies.html": {"status": 200},
            "site/diagnostics.html": {"status": 200},
            "site/index.html": {"status": 404},
        },
        "documented_install": (
            "git clone https://github.com/eliasinul/CLEWsCAN2070.git; "
            "cd CLEWsCAN2070; uv sync"
        ),
        "documented_run": [
            "uv run osecan build-sets",
            "uv run osecan build-params",
            "uv run osecan retrieve",
            'uv run osecan clean-demand "Current Measures"',
            "uv run osecan harmonize",
            "uv run osecan solve --run results/base",
            "uv run osecan report",
        ],
        "resources": [
            "resources/osemosys_fast_osecan.txt",
            "resources/preprocess_data.py",
            "resources/otoole.yaml",
            "config/otoole.yaml",
            "config/data_sources.yaml",
            "config/capacity.yaml",
        ],
    },
    "install": {
        "method": "uv sync --frozen",
        "uv_version": "0.13.9",
        "python": "3.12",
        "package_version": "0.1.0",
        "solver": MODEL_SOLVER,
        "status": "success",
        "cli_entry_point": "uv run osecan info -> success",
    },
    "model_vocabulary": {
        "bcw_supply_technologies": ["SUPBCW"],
        "zonal_bcw_supply_technologies": [],
        "zonal_bcw_plant_technologies": ["PWRBCWB1", "PWRBCWB2"],
        "note": (
            "PWRBCWB1/B2 exist, but they consume the national BCW fuel. "
            "There is no SUPBCWB1/B2 supply technology and no BCWB1/BCWB2 "
            "fuel, so the zonal caps cannot be loaded."
        ),
    },
    "run": {
        "status": "optimal",
        "scenario": "Current Measures",
        "demand_2025_pj": 9328.0016,
        "objective_musd": 1_331_287.25,
        "model_commit": MODEL_COMMIT,
        "commands": [
            "uv sync --frozen",
            "uv run python -m osecan.cli build-sets",
            "uv run python -m osecan.cli build-params",
            "uv run python -m osecan.cli retrieve",
            'uv run python -m osecan.cli clean-demand "Current Measures"',
            "uv run python -m osecan.cli harmonize",
            "python -m osecan.pipeline.assemble --out results/base/input",
            "otoole convert csv datafile results/base/input results/base/osecan.txt config/otoole.yaml",
            "python -m osecan.pipeline.solve --run results/base",
            "python -m osecan.cli report --run results/base --out /tmp/osecan_report.html",
        ],
        "report": {
            "status": "success",
            "path": "/tmp/osecan_report.html",
            "bytes": 871305,
        },
        "run_directory": "/tmp/osecan_src/results/base (temporary clone)",
        "blocker": (
            "None for the national model. The zonal SUPBCWB1/B2 files are "
            "rejected because those codes are not in the TECHNOLOGY set."
        ),
    },
}


def capital_recovery_factor(rate: float, years: int) -> float:
    """Return the capital recovery factor for a constant annual payment.

    Args:
        rate: Discount rate as a fraction (0.05 for 5%).
        years: Asset life in years.

    Returns:
        The capital recovery factor.

    Raises:
        ValueError: If the rate is negative or the life is non-positive.
    """
    if rate < 0:
        raise ValueError(f"discount rate must be non-negative, got {rate}")
    if years <= 0:
        raise ValueError(f"asset life must be positive, got {years}")
    if rate == 0:
        return 1.0 / years
    factor = (1.0 + rate) ** years
    return rate * factor / (factor - 1.0)


def plant_lcoe(
    plant: Plant,
    fuel_price_usd_per_gj: float,
    carbon_price_usd_per_t: float = 0.0,
    discount_rate: float = DISCOUNT_RATE,
) -> Lcoe:
    """Compute a simplified LCOE for one plant.

    The calculation includes annualised capital, fixed O&M, fuel, a separate
    CO2 cost for plants with an emission factor, and variable O&M.  It excludes
    transmission, backup, and financing outside the discount rate.

    Args:
        plant: Plant parameters.
        fuel_price_usd_per_gj: Delivered fuel price in 2020 CAD per GJ.
        carbon_price_usd_per_t: CO2 price in 2020 CAD per tonne.
        discount_rate: Discount rate as a fraction.

    Returns:
        The levelised cost components and total.

    Raises:
        ValueError: If a cost or physical parameter is invalid.
    """
    if plant.capacity_factor <= 0 or plant.capacity_factor > 1:
        raise ValueError(
            f"{plant.name} capacity factor must be in (0, 1], "
            f"got {plant.capacity_factor}"
        )
    if plant.capex_usd_per_kw < 0 or plant.fixed_usd_per_kw_yr < 0:
        raise ValueError(f"{plant.name} has a negative capital or fixed cost")
    if plant.var_usd_per_gj < 0 or fuel_price_usd_per_gj < 0:
        raise ValueError(f"{plant.name} has a negative fuel or variable cost")
    if carbon_price_usd_per_t < 0:
        raise ValueError(
            f"carbon price must be non-negative, got {carbon_price_usd_per_t}"
        )

    crf = capital_recovery_factor(discount_rate, plant.life_years)
    mwh_per_kw_year = plant.capacity_factor * HOURS_PER_YEAR / 1000.0
    capital_fixed = (
        plant.capex_usd_per_kw * crf + plant.fixed_usd_per_kw_yr
    ) / mwh_per_kw_year
    fuel = GJ_PER_MWH * plant.input_activity_ratio * fuel_price_usd_per_gj
    carbon = GJ_PER_MWH * plant.emission_factor_t_per_gj * carbon_price_usd_per_t
    variable = GJ_PER_MWH * plant.var_usd_per_gj
    return Lcoe(
        capital_fixed_usd_per_mwh=capital_fixed,
        fuel_usd_per_mwh=fuel,
        carbon_usd_per_mwh=carbon,
        var_usd_per_mwh=variable,
        total_usd_per_mwh=capital_fixed + fuel + carbon + variable,
        crf=crf,
        co2_t_per_mwh=GJ_PER_MWH * plant.emission_factor_t_per_gj,
    )


def _read_csv(path: Path, label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Missing {label}: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"Empty CSV header for {label}: {path}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"Empty {label}: {path}")
    return rows


def _number(value: str | float, context: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Non-numeric value at {context}: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"Non-finite value at {context}: {value!r}")
    return number


def _parameter_rows(path: Path, technology: str) -> dict[int, float]:
    """Read a single-technology 2021-2070 parameter file."""
    rows = _read_csv(path, f"parameter file for {technology}")
    values: dict[int, float] = {}
    for index, row in enumerate(rows, start=2):
        if row["REGION"] != "CAN":
            raise ValueError(f"{path}: expected REGION=CAN at row {index}")
        if row["TECHNOLOGY"] != technology:
            raise ValueError(
                f"{path}: expected TECHNOLOGY={technology} at row {index}, "
                f"got {row['TECHNOLOGY']!r}"
            )
        year = int(row["YEAR"])
        if year in values:
            raise ValueError(f"{path}: duplicate year {year}")
        values[year] = _number(row["VALUE"], f"{path} row {index}")
    if tuple(sorted(values)) != YEARS:
        raise ValueError(
            f"{path}: expected years {HORIZON_START}-{HORIZON_END}, "
            f"got {min(values)}-{max(values)} with {len(values)} rows"
        )
    return values


def load_case_supply(supply_dir: Path, case: str) -> CaseSupply:
    """Load one low/base/high case and verify its national rollup.

    Args:
        supply_dir: Directory containing the case CSV files.
        case: One of ``low``, ``base``, ``high``.

    Returns:
        The parsed and reconciled case.

    Raises:
        ValueError: If the case is unknown or the B1+B2 rollup does not match
            the national ``SUPBCW`` file.
    """
    if case not in CASES:
        raise ValueError(f"unknown case {case!r}; expected one of {CASES}")
    national_cap = _parameter_rows(
        supply_dir / f"comparison/SUPBCW_activity_limits_{case}.csv", "SUPBCW"
    )
    national_cost = _parameter_rows(
        supply_dir / f"comparison/SUPBCW_variable_cost_{case}.csv", "SUPBCW"
    )
    b1_cap = _parameter_rows(
        supply_dir / f"SUPBCWB1_activity_limits_{case}.csv", "SUPBCWB1"
    )
    b2_cap = _parameter_rows(
        supply_dir / f"SUPBCWB2_activity_limits_{case}.csv", "SUPBCWB2"
    )
    b1_cost = _parameter_rows(
        supply_dir / f"SUPBCWB1_variable_cost_{case}.csv", "SUPBCWB1"
    )
    b2_cost = _parameter_rows(
        supply_dir / f"SUPBCWB2_variable_cost_{case}.csv", "SUPBCWB2"
    )
    for year in YEARS:
        if not math.isclose(
            b1_cap[year] + b2_cap[year], national_cap[year], rel_tol=0.0, abs_tol=1e-6
        ):
            raise ValueError(
                f"{case} {year}: B1+B2 cap {b1_cap[year] + b2_cap[year]} "
                f"does not equal SUPBCW cap {national_cap[year]}"
            )
        if not math.isclose(
            b1_cost[year], national_cost[year], rel_tol=0.0, abs_tol=1e-9
        ) or not math.isclose(
            b2_cost[year], national_cost[year], rel_tol=0.0, abs_tol=1e-9
        ):
            raise ValueError(
                f"{case} {year}: zonal costs do not equal the national cost"
            )
    return CaseSupply(
        case=case,
        national_cap_pj=national_cap,
        national_cost_usd_per_gj=national_cost,
        b1_cap_pj=b1_cap,
        b2_cap_pj=b2_cap,
        b1_cost_usd_per_gj=b1_cost,
        b2_cost_usd_per_gj=b2_cost,
    )


def load_all_cases(supply_dir: Path) -> dict[str, CaseSupply]:
    """Load and cross-check all three cases.

    The cases are required to be monotone in every year: low < base < high for
    the cap and low > base > high for the cost.  A silent inversion is a data
    error, not a scenario.

    Args:
        supply_dir: Directory containing the case CSV files.

    Returns:
        Mapping of case name to parsed case.

    Raises:
        ValueError: If the cases are not monotone.
    """
    cases = {case: load_case_supply(supply_dir, case) for case in CASES}
    for year in YEARS:
        low, base, high = (cases[case] for case in CASES)
        if not (
            low.national_cap_pj[year]
            < base.national_cap_pj[year]
            < high.national_cap_pj[year]
        ):
            raise ValueError(f"{year}: caps are not monotone low < base < high")
        if not (
            low.national_cost_usd_per_gj[year]
            > base.national_cost_usd_per_gj[year]
            > high.national_cost_usd_per_gj[year]
        ):
            raise ValueError(f"{year}: costs are not monotone low > base > high")
    return cases


def _row(
    *,
    case: str,
    plant: str,
    fuel: str,
    fuel_price: float,
    carbon_price: float,
    economics: Lcoe,
    plant_params: Plant,
) -> dict[str, object]:
    return {
        "case": case,
        "plant": plant,
        "fuel": fuel,
        "input_activity_ratio": plant_params.input_activity_ratio,
        "efficiency": 1.0 / plant_params.input_activity_ratio,
        "fuel_price_usd_per_gj": fuel_price,
        "carbon_price_usd_per_t": carbon_price,
        "capex_usd_per_kw": plant_params.capex_usd_per_kw,
        "fixed_usd_per_kw_yr": plant_params.fixed_usd_per_kw_yr,
        "var_usd_per_gj": plant_params.var_usd_per_gj,
        "life_years": plant_params.life_years,
        "capacity_factor": plant_params.capacity_factor,
        "discount_rate": DISCOUNT_RATE,
        "crf": economics.crf,
        "capital_fixed_usd_per_mwh": economics.capital_fixed_usd_per_mwh,
        "fuel_usd_per_mwh": economics.fuel_usd_per_mwh,
        "carbon_usd_per_mwh": economics.carbon_usd_per_mwh,
        "var_usd_per_mwh": economics.var_usd_per_mwh,
        "lcoe_usd_per_mwh": economics.total_usd_per_mwh,
        "co2_t_per_mwh": economics.co2_t_per_mwh,
        "source": plant_params.source,
    }


def build_lcoe_rows(cases: Mapping[str, CaseSupply]) -> list[dict[str, object]]:
    """Build the plant-by-case LCOE table.

    Args:
        cases: Parsed low/base/high cases.

    Returns:
        Rows suitable for ``lcoe_by_plant.csv``.
    """
    rows: list[dict[str, object]] = []
    bcw = PLANTS["PWRBCW"]
    for case in CASES:
        economics = plant_lcoe(bcw, cases[case].national_cost_usd_per_gj[HORIZON_START])
        rows.append(
            _row(
                case=case,
                plant="PWRBCW",
                fuel="BCW",
                fuel_price=cases[case].national_cost_usd_per_gj[HORIZON_START],
                carbon_price=0.0,
                economics=economics,
                plant_params=bcw,
            )
        )
    placeholder = plant_lcoe(bcw, PLACEHOLDER_BCW_USD_PER_GJ)
    rows.append(
        _row(
            case="placeholder",
            plant="PWRBCW",
            fuel="BCW",
            fuel_price=PLACEHOLDER_BCW_USD_PER_GJ,
            carbon_price=0.0,
            economics=placeholder,
            plant_params=bcw,
        )
    )
    bio = PLANTS["PWRBIO"]
    rows.append(
        _row(
            case="generic",
            plant="PWRBIO",
            fuel="BIO",
            fuel_price=GENERIC_BIO_USD_PER_GJ,
            carbon_price=0.0,
            economics=plant_lcoe(bio, GENERIC_BIO_USD_PER_GJ),
            plant_params=bio,
        )
    )
    bsw = PLANTS["PWRBSW"]
    rows.append(
        _row(
            case="placeholder",
            plant="PWRBSW",
            fuel="BSW",
            fuel_price=BSW_USD_PER_GJ,
            carbon_price=0.0,
            economics=plant_lcoe(bsw, BSW_USD_PER_GJ),
            plant_params=bsw,
        )
    )
    ngs = PLANTS["PWRNGS"]
    for label, carbon_price in CARBON_PRICE_USD_PER_T.items():
        rows.append(
            _row(
                case=f"carbon_{label}",
                plant="PWRNGS",
                fuel="NGS",
                fuel_price=GAS_USD_PER_GJ,
                carbon_price=carbon_price,
                economics=plant_lcoe(ngs, GAS_USD_PER_GJ, carbon_price),
                plant_params=ngs,
            )
        )
    hyd = PLANTS["PWRHYD"]
    rows.append(
        _row(
            case="generic",
            plant="PWRHYD",
            fuel="HYD",
            fuel_price=0.0,
            carbon_price=0.0,
            economics=plant_lcoe(hyd, 0.0),
            plant_params=hyd,
        )
    )
    return rows


def _lcoe_lookup(rows: Sequence[Mapping[str, object]]) -> dict[tuple[str, str], float]:
    lookup: dict[tuple[str, str], float] = {}
    for row in rows:
        lookup[(str(row["case"]), str(row["plant"]))] = float(row["lcoe_usd_per_mwh"])
    return lookup


def build_bcw_vs_bio_rows(
    cases: Mapping[str, CaseSupply], lcoe_rows: Sequence[Mapping[str, object]]
) -> list[dict[str, object]]:
    """Compare each BCW case with generic BIO, gas, and hydro.

    Args:
        cases: Parsed low/base/high cases.
        lcoe_rows: Output of :func:`build_lcoe_rows`.

    Returns:
        Rows suitable for ``bcw_vs_bio.csv``.
    """
    lookup = _lcoe_lookup(lcoe_rows)
    bio_lcoe = lookup[("generic", "PWRBIO")]
    gas_95 = lookup[("carbon_2026", "PWRNGS")]
    gas_115 = lookup[("carbon_2030", "PWRNGS")]
    hydro_lcoe = lookup[("generic", "PWRHYD")]
    bcw_capital_fixed = plant_lcoe(PLANTS["PWRBCW"], 0.0).capital_fixed_usd_per_mwh
    bcw_fuel_slope = GJ_PER_MWH * PLANTS["PWRBCW"].input_activity_ratio
    if bcw_fuel_slope <= 0:
        raise AssertionError("PWRBCW fuel slope must be positive")

    rows: list[dict[str, object]] = []
    for case in CASES:
        bcw_lcoe = lookup[(case, "PWRBCW")]
        bcw_cost = cases[case].national_cost_usd_per_gj[HORIZON_START]
        breakeven_bio = (bio_lcoe - bcw_capital_fixed) / bcw_fuel_slope
        breakeven_gas_95 = (gas_95 - bcw_capital_fixed) / bcw_fuel_slope
        breakeven_gas_115 = (gas_115 - bcw_capital_fixed) / bcw_fuel_slope
        carbon_parity = 95.0 + (bcw_lcoe - gas_95) / (
            GJ_PER_MWH * PLANTS["PWRNGS"].emission_factor_t_per_gj
        )
        rows.append(
            {
                "case": case,
                "bcw_cost_usd_per_gj": bcw_cost,
                "bcw_fuel_usd_per_mwh": GJ_PER_MWH
                * PLANTS["PWRBCW"].input_activity_ratio
                * bcw_cost,
                "bcw_lcoe_usd_per_mwh": bcw_lcoe,
                "bio_fuel_usd_per_mwh": GJ_PER_MWH
                * PLANTS["PWRBIO"].input_activity_ratio
                * GENERIC_BIO_USD_PER_GJ,
                "bio_lcoe_usd_per_mwh": bio_lcoe,
                "lcoe_delta_vs_bio_usd_per_mwh": bcw_lcoe - bio_lcoe,
                "breakeven_bcw_vs_bio_usd_per_gj": breakeven_bio,
                "cheaper_than_bio": bcw_lcoe < bio_lcoe,
                "lcoe_delta_vs_gas95_usd_per_mwh": bcw_lcoe - gas_95,
                "lcoe_delta_vs_gas115_usd_per_mwh": bcw_lcoe - gas_115,
                "breakeven_bcw_vs_gas95_usd_per_gj": breakeven_gas_95,
                "breakeven_bcw_vs_gas115_usd_per_gj": breakeven_gas_115,
                "breakeven_bcw_vs_hydro_usd_per_gj": (hydro_lcoe - bcw_capital_fixed)
                / bcw_fuel_slope,
                "carbon_price_for_parity_with_gas_usd_per_t": carbon_parity,
                "cheaper_than_gas95": bcw_lcoe < gas_95,
                "cheaper_than_gas115": bcw_lcoe < gas_115,
                "cheaper_than_hydro": bcw_lcoe < hydro_lcoe,
            }
        )
    return rows


def _cap_row(case: str, case_supply: CaseSupply) -> dict[str, object]:
    cap_2021 = case_supply.national_cap_pj[HORIZON_START]
    cap_2028 = case_supply.national_cap_pj[2028]
    cap_2070 = case_supply.national_cap_pj[HORIZON_END]
    return {
        "case": case,
        "cap_2021_pj": cap_2021,
        "cap_2028_pj": cap_2028,
        "cap_2070_pj": cap_2070,
        "cap_2028_flat_pj": cap_2028,
        "max_electricity_2021_pj": cap_2021 * BCW_EFFICIENCY,
        "max_electricity_2028_pj": cap_2028 * BCW_EFFICIENCY,
        "max_electricity_2021_twh": cap_2021 * BCW_EFFICIENCY / GJ_PER_MWH,
        "max_electricity_2028_twh": cap_2028 * BCW_EFFICIENCY / GJ_PER_MWH,
        "b1_cap_2028_pj": case_supply.b1_cap_pj[2028],
        "b2_cap_2028_pj": case_supply.b2_cap_pj[2028],
    }


def build_cap_metrics(cases: Mapping[str, CaseSupply]) -> dict[str, object]:
    """Build the cap-to-electricity conversion and placeholder comparison.

    Args:
        cases: Parsed low/base/high cases.

    Returns:
        A JSON-ready cap-metrics payload.
    """
    payload: dict[str, object] = {
        "efficiency_pwrbcw": BCW_EFFICIENCY,
        "gj_per_mwh": GJ_PER_MWH,
        "placeholder": {
            "case": "placeholder",
            "cap_pj": PLACEHOLDER_BCW_CAP_PJ,
            "max_electricity_pj": PLACEHOLDER_BCW_CAP_PJ * BCW_EFFICIENCY,
            "max_electricity_twh": PLACEHOLDER_BCW_CAP_PJ * BCW_EFFICIENCY / GJ_PER_MWH,
            "note": (
                "The 50 PJ placeholder is applied per SUP technology in "
                "build_params.py. SUPBCW and SUPBSW each get 50 PJ, so the "
                "combined placeholder feedstock cap is 100 PJ, not 50 PJ."
            ),
        },
        "baseline_2025_hydro_generation_pj": 2264.877571,
    }
    for case in CASES:
        payload[case] = _cap_row(case, cases[case])
    base_2028 = payload["base"]["max_electricity_2028_pj"]  # type: ignore[index]
    hydro = payload["baseline_2025_hydro_generation_pj"]
    payload["base_2028_share_of_2025_hydro_generation_pct"] = base_2028 / hydro * 100.0
    return payload


def build_cap_binding(
    cases: Mapping[str, CaseSupply],
    cap_metrics: Mapping[str, object],
    comparison_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Assemble the cap-binding analysis and its conclusion.

    Args:
        cases: Parsed low/base/high cases.
        cap_metrics: Output of :func:`build_cap_metrics`.
        comparison_rows: Output of :func:`build_bcw_vs_bio_rows`.

    Returns:
        A JSON-ready cap-binding payload.
    """
    comparison = {str(row["case"]): row for row in comparison_rows}
    checks = []
    for case in CASES:
        row = comparison[case]
        checks.append(
            {
                "case": case,
                "bcw_lcoe_usd_per_mwh": row["bcw_lcoe_usd_per_mwh"],
                "bcw_cost_usd_per_gj": row["bcw_cost_usd_per_gj"],
                "cap_2028_pj": cases[case].national_cap_pj[2028],
                "utilisation_pj": 0.0,
                "utilisation_fraction": 0.0,
                "cap_binding": False,
                "reason": (
                    "PWRBCW is more expensive than the model's hydro and "
                    "placeholder gas plants in every run."
                ),
            }
        )
    return {
        "schema_version": "1.0",
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "scenario": MODEL_SCENARIO,
            "horizon": f"{HORIZON_START}-{HORIZON_END}",
            "region": "CAN",
            "zones": [
                "B1",
                "B2",
                "A1",
                "A2",
                "S1",
                "M1",
                "O1",
                "O2",
                "Q1",
                "Q2",
                "T1",
                "L1",
                "N1",
            ],
        },
        "runs": dict(MODEL_RUNS),
        "zonal_compatibility": {
            "status": "rejected",
            "technologies": ["SUPBCWB1", "SUPBCWB2"],
            "reason": (
                "The generated TECHNOLOGY set has SUPBCW, not SUPBCWB1/B2. "
                "The model also has no BCWB1/BCWB2 fuel; PWRBCWB1/B2 consume "
                "the national BCW fuel."
            ),
            "harmonize_exit": 1,
            "harmonize_issues": [
                "TotalTechnologyAnnualActivityUpperLimit.csv: TECHNOLOGY not in SETS: ['SUPBCWB1', 'SUPBCWB2']",
                "VariableCost.csv: TECHNOLOGY not in SETS: ['SUPBCWB1', 'SUPBCWB2']",
            ],
        },
        "cap_metrics": dict(cap_metrics),
        "case_checks": checks,
        "breakevens": {
            "bcw_vs_bio_usd_per_gj": comparison["base"][
                "breakeven_bcw_vs_bio_usd_per_gj"
            ],
            "bcw_vs_gas_at_carbon_95_usd_per_gj": comparison["base"][
                "breakeven_bcw_vs_gas95_usd_per_gj"
            ],
            "bcw_vs_gas_at_carbon_115_usd_per_gj": comparison["base"][
                "breakeven_bcw_vs_gas115_usd_per_gj"
            ],
            "bcw_vs_hydro_usd_per_gj": comparison["base"][
                "breakeven_bcw_vs_hydro_usd_per_gj"
            ],
            "carbon_price_for_low_bcw_parity_usd_per_t": comparison["low"][
                "carbon_price_for_parity_with_gas_usd_per_t"
            ],
            "carbon_price_for_base_bcw_parity_usd_per_t": comparison["base"][
                "carbon_price_for_parity_with_gas_usd_per_t"
            ],
            "carbon_price_for_high_bcw_parity_usd_per_t": comparison["high"][
                "carbon_price_for_parity_with_gas_usd_per_t"
            ],
        },
        "generic_bio_bypass": {
            "status": "present",
            "description": (
                "PWRBIO consumes BIO from the uncapped RNWBIO supply at "
                "$3/GJ. A BCW cap therefore cannot cap biomass electricity; "
                "it only shifts feedstock from BCW to generic BIO."
            ),
            "pwr_bio_lcoe_usd_per_mwh": comparison["base"]["bio_lcoe_usd_per_mwh"],
            "pwr_bio_delta_vs_gas95_usd_per_mwh": comparison["base"][
                "bio_lcoe_usd_per_mwh"
            ]
            - (
                comparison["base"]["bcw_lcoe_usd_per_mwh"]
                - comparison["base"]["lcoe_delta_vs_gas95_usd_per_mwh"]
            ),
        },
        "conclusion": (
            "The 50 PJ placeholder and the 43-50 PJ BC base cap do not bind "
            "in any baseline or diagnostic run. The model dispatches zero BCW "
            "because unconstrained hydro is cheaper in the base model, and the "
            "placeholder gas plant is cheaper than PWRBCW even when hydro is "
            "frozen. The generic-BIO bypass means a BCW cap would not cap "
            "biomass electricity in any case. The policy sensitivity recorded "
            "in the run report shows the cap can bind when the CO2 price is "
            "$200/t and hydro is frozen at 2021 activity."
        ),
    }


def build_sensitivity() -> dict[str, object]:
    """Return the recorded policy-sensitivity payload.

    The payload is evidence transcribed from the actual solves, not a
    re-simulation.  It is written to ``sensitivity/sensitivity_runs.json`` and
    summarised in the run report.

    Returns:
        A JSON-ready sensitivity payload.
    """
    baseline = MODEL_RUNS["bc_base_national_rollup"]
    return {
        "schema_version": "1.0",
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
            "scenario": MODEL_SCENARIO,
            "horizon": f"{HORIZON_START}-{HORIZON_END}",
            "region": "CAN",
        },
        "baseline": {
            "run": "bc_base_national_rollup",
            "objective_musd": baseline["objective_musd"],
            "supbcw_total_pj": baseline["supbcw_pj"]["total"],
            "pwrbcw_total_pj": baseline["pwrbcw_pj"]["total"],
            "pwr_bio_total_pj": baseline["pwr_bio_pj"]["total"],
            "cap_binding": baseline["cap_binding"],
            "emissions_total_mt": baseline["emissions_total_mt"],
        },
        "forcing_conditions": {
            "carbon_200": (
                "EmissionsPenalty 95 $/t for 2021-2025 and 200 $/t from 2026; "
                "all other parameters from the BC-base national rollup."
            ),
            "carbon_200_hydro_frozen": (
                "The carbon_200 forcing plus a TotalTechnologyAnnualActivity"
                "UpperLimit on every PWRHYD<zone> equal to its 2021 base-run "
                "activity in all years."
            ),
        },
        "runs": dict(SENSITIVITY_RUNS),
        "conditions_for_bcw_dispatch": [
            (
                "The CO2 price must exceed the BCW-vs-gas parity price: about "
                "160 $/t for the base case, 124 $/t for the high case, and "
                "214 $/t for the low case."
            ),
            (
                "A cheaper clean firm resource must be constrained. With "
                "unconstrained hydro, even $200/t leaves BCW at zero because "
                "hydro expands to 3,252 PJ in 2050."
            ),
            (
                "With $200/t and hydro frozen at 2021 activity, SUPBCW reaches the "
                "42.959 PJ cap and PWRBCW produces 11.548 PJ/yr of electricity."
            ),
        ],
        "limitations": [
            (
                "The model has a national SUPBCW cap only. Zonal BC caps "
                "(SUPBCWB1/B2) cannot be represented without a model-structure "
                "change."
            ),
            (
                "BCW is a national fuel. In the hydro-frozen run PWRBCW dispatches "
                "in A1/A2/S1/Q1/Q2/T1/L1, not in B1/B2; the model does not allocate "
                "BC residue to BC."
            ),
            (
                "PWRBIO stays at zero in both sensitivity runs because wind is "
                "cheaper than generic BIO; the generic-BIO bypass is latent, not "
                "active."
            ),
        ],
    }


def build_availability() -> dict[str, object]:
    """Return the recorded availability evidence with a build timestamp."""
    payload = json.loads(json.dumps(AVAILABILITY))
    payload["generated_utc"] = datetime.now(UTC).isoformat(timespec="seconds")
    return payload


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


def _input_paths(supply_dir: Path) -> list[Path]:
    paths = [
        supply_dir / "bc_residue_cases_summary.json",
        supply_dir / "manifest.json",
    ]
    for case in CASES:
        for prefix in ("SUPBCWB1", "SUPBCWB2"):
            paths.append(supply_dir / f"{prefix}_activity_limits_{case}.csv")
            paths.append(supply_dir / f"{prefix}_variable_cost_{case}.csv")
        paths.append(supply_dir / f"comparison/SUPBCW_activity_limits_{case}.csv")
        paths.append(supply_dir / f"comparison/SUPBCW_variable_cost_{case}.csv")
    return paths


def build_manifest(
    root: Path,
    output_dir: Path,
    inputs: Sequence[Path],
    outputs: Sequence[Path],
    generated_utc: str,
) -> dict[str, object]:
    """Build the artifact manifest with SHA-256 digests.

    Args:
        root: Repository root.
        output_dir: Directory containing the new artifacts.
        inputs: Input files that exist.
        outputs: Output files to hash, excluding the manifest itself.
        generated_utc: ISO-8601 build timestamp.

    Returns:
        A JSON-ready manifest.
    """
    input_rows = []
    for path in inputs:
        if not path.exists():
            raise FileNotFoundError(f"Missing manifest input: {path}")
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
            raise FileNotFoundError(f"Missing manifest output: {path}")
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
        "status": "fresh_side_provisional_not_c2070_official",
        "model": {
            "repository": MODEL_REPOSITORY,
            "commit": MODEL_COMMIT,
            "package": MODEL_PACKAGE,
            "solver": MODEL_SOLVER,
        },
        "assumptions": [
            "OSeCAN plant parameters are the documented v0.3 placeholders, not Canadian fleet averages.",
            "PWRBCW and PWRBIO use the documented input activity ratios 3.72 and 2.85 (26.9% and 35.1% efficiency).",
            "LCOE is a simplified busbar calculation at 5% discount; it excludes transmission, backup, and financing outside the discount rate.",
            "Gas LCOE is the model's placeholder gas plant (capex 1100 $/kW, fuel $3/GJ, CO2 0.051 t/GJ, carbon $95 or $115/t), not a market forecast.",
            "The BC zonal caps are FRESH-side provisional, not a C2070 official boundary.",
            "The model has no SUPBCWB1/B2 technology; the BC base run is the national SUPBCW rollup.",
            "The diagnostic hydro-frozen runs are sensitivity probes, not scenarios.",
        ],
        "inputs": input_rows,
        "outputs": output_rows,
    }


def _fmt(value: object, digits: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def build_sensitivity_rows(
    sensitivity: Mapping[str, object],
) -> list[dict[str, object]]:
    """Flatten the sensitivity payload into one CSV row per run.

    Args:
        sensitivity: Output of :func:`build_sensitivity`.

    Returns:
        Rows suitable for ``sensitivity/sensitivity_summary.csv``.
    """
    baseline = sensitivity["baseline"]
    rows: list[dict[str, object]] = [
        {
            "run": "bc_base_national_rollup",
            "forcing": "documented 95-115 $/t carbon schedule",
            "status": "optimal",
            "objective_musd": baseline["objective_musd"],
            "objective_delta_musd": 0.0,
            "supbcw_total_pj": baseline["supbcw_total_pj"],
            "pwrbcw_total_pj": baseline["pwrbcw_total_pj"],
            "pwr_bio_total_pj": baseline["pwr_bio_total_pj"],
            "cap_binding": baseline["cap_binding"],
            "cap_binding_years": "",
            "emissions_total_mt": baseline["emissions_total_mt"],
        }
    ]
    for name in ("carbon_200", "carbon_200_hydro_frozen"):
        run = sensitivity["runs"][name]
        rows.append(
            {
                "run": name,
                "forcing": run["forcing"],
                "status": run["status"],
                "objective_musd": run["objective_musd"],
                "objective_delta_musd": run["objective_delta_musd"],
                "supbcw_total_pj": run["supbcw_pj"]["total"],
                "pwrbcw_total_pj": run["pwrbcw_pj"]["total"],
                "pwr_bio_total_pj": run["pwr_bio_pj"]["total"],
                "cap_binding": run["cap_binding"],
                "cap_binding_years": run["cap_binding_years"],
                "emissions_total_mt": run["emissions_mt"]["total"],
            }
        )
    return rows


def _sensitivity_markdown(sensitivity: Mapping[str, object]) -> list[str]:
    runs = sensitivity["runs"]
    baseline = sensitivity["baseline"]
    lines = [
        "## 4. Policy sensitivity",
        "",
        "Two forcing cases were run on 2026-11-17 in a disposable copy of the",
        "public model at `/tmp/osecan_sens`; the public clone was not modified.",
        "Both use the BC-base national rollup (42.959 PJ cap from 2028,",
        "2.456978 $/GJ). The only model change is the CO2 penalty function, plus",
        "the hydro upper limits in the second case.",
        "",
        "| Run | Forcing | Status | Objective (M$) | SUPBCW total (PJ) | PWRBCW total (PJ) | Cap binds | Emissions total (Mt) |",
        "|---|---|---|---:|---:|---:|---|---:|",
        (
            f"| BC base | documented 95-115 $/t | optimal | "
            f"{_fmt(baseline['objective_musd'])} | "
            f"{_fmt(baseline['supbcw_total_pj'], 3)} | "
            f"{_fmt(baseline['pwrbcw_total_pj'], 3)} | no | "
            f"{_fmt(baseline['emissions_total_mt'], 3)} |"
        ),
    ]
    for name in ("carbon_200", "carbon_200_hydro_frozen"):
        run = runs[name]
        lines.append(
            f"| {name} | {run['description']} | {run['status']} | "
            f"{_fmt(run['objective_musd'])} | "
            f"{_fmt(run['supbcw_pj']['total'], 3)} | "
            f"{_fmt(run['pwrbcw_pj']['total'], 3)} | "
            f"{'yes' if run['cap_binding'] else 'no'} | "
            f"{_fmt(run['emissions_mt']['total'], 3)} |"
        )
    lines += [
        "",
        "`SUPBSW` and `PWRBIO` are zero in both sensitivity runs.",
        "",
        "### SUPBCW activity by year (PJ)",
        "",
        "| Run | 2021 | 2025 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("carbon_200", "carbon_200_hydro_frozen"):
        sup = runs[name]["supbcw_pj"]
        lines.append(
            f"| {name} | {_fmt(sup['2021'], 3)} | {_fmt(sup['2025'], 3)} | "
            f"{_fmt(sup['2030'], 3)} | {_fmt(sup['2050'], 3)} | "
            f"{_fmt(sup['2070'], 3)} | {_fmt(sup['total'], 3)} |"
        )
    lines += [
        "",
        "### PWRBCW electricity by year (PJ)",
        "",
        "| Run | 2021 | 2025 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name in ("carbon_200", "carbon_200_hydro_frozen"):
        pwr = runs[name]["pwrbcw_pj"]
        lines.append(
            f"| {name} | {_fmt(pwr['2021'], 3)} | {_fmt(pwr['2025'], 3)} | "
            f"{_fmt(pwr['2030'], 3)} | {_fmt(pwr['2050'], 3)} | "
            f"{_fmt(pwr['2070'], 3)} | {_fmt(pwr['total'], 3)} |"
        )
    lines += [
        "",
        "At $200/t without a hydro constraint, biomass stays at zero: hydro",
        "expands to 3,252 PJ in 2050 and the objective rises by only 240.67 M$",
        "while cumulative emissions fall to 1.86 Mt. The carbon price changes",
        "the merit order against gas, but not against hydro.",
        "",
        "With $200/t and hydro frozen at 2021 activity, SUPBCW reaches",
        "42.959 PJ in every year from 2022 and the cap binds from 2028 through",
        "2070 (100.0% utilisation). PWRBCW produces 11.548 PJ/yr of electricity",
        "(3.21 TWh), or 0.35% of 2050 generation. Wind supplies 977.3 PJ in",
        "2050, gas 32.7 PJ, and PWRBIO remains zero. The objective rises by",
        "40,266.10 M$.",
        "",
        "### Conditions for BCW dispatch",
        "",
    ]
    for item in sensitivity["conditions_for_bcw_dispatch"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "The cap that binds is the national `SUPBCW` cap. The model has no",
        "zonal BC supply technology, so a zonal cap cannot be tested here.",
        "",
        "The documented 95-115 $/t schedule is below the base-case parity",
        "price of about 160 $/t even with hydro frozen, which is why the",
        "earlier hydro-frozen diagnostic also had zero BCW.",
        "",
        "### Sensitivity limitations",
        "",
    ]
    for item in sensitivity["limitations"]:
        lines.append(f"- {item}")
    lines += [
        "",
        "Reproduction (disposable copy; the public clone is unchanged):",
        "",
        "```bash",
        "cd /tmp/osecan_sens",
        "# build_params.py: return 95.0 if y < 2026 else 200.0",
        ".venv/bin/python -m osecan.cli build-params --out data/params",
        '.venv/bin/python -m osecan.cli clean-demand "Current Measures"',
        ".venv/bin/python apply_bc_base.py",
        ".venv/bin/python -m osecan.pipeline.assemble --out results/<run>/input",
        ".venv/bin/otoole convert csv datafile results/<run>/input results/<run>/osecan.txt config/otoole.yaml",
        ".venv/bin/python -m osecan.pipeline.solve --run results/<run>",
        "```",
        "",
        "For the hydro-frozen case, append the 650 `PWRHYD<zone>` rows from",
        "`results/diag_hydro_frozen/input/TotalTechnologyAnnualActivityUpperLimit.csv`",
        "to the assembled input before `otoole convert`.",
        "",
    ]
    return lines


def _markdown_report(
    lcoe_rows: Sequence[Mapping[str, object]],
    comparison_rows: Sequence[Mapping[str, object]],
    cap_binding: Mapping[str, object],
    sensitivity: Mapping[str, object],
    availability: Mapping[str, object],
) -> str:
    lookup = {(str(r["case"]), str(r["plant"])): r for r in lcoe_rows}
    comparison = {str(r["case"]): r for r in comparison_rows}
    cap_metrics = cap_binding["cap_metrics"]
    breakevens = cap_binding["breakevens"]
    lines = [
        "# OSeCAN availability and reduced biomass-module analysis",
        "",
        "Status: **FRESH-side provisional**. This is not an official C2070",
        "result. The model was run from the public `deltae/C2070` repository;",
        "the reduced analysis uses the documented OSeCAN v0.3 placeholders.",
        "",
        "## 1. Availability",
        "",
        "| Item | Result |",
        "|---|---|",
        f"| PyPI `osecan` JSON | HTTP {availability['pypi']['json_http_status']} (not published) |",
        f"| `pip index versions osecan` | `{availability['pypi']['pip_index_versions']}` |",
        f"| `pip download/install osecan` | `{availability['pypi']['pip_install']}` |",
        f"| `github.com/deltae/C2070` | public, HEAD `{MODEL_COMMIT[:12]}` |",
        "| `github.com/eliasinul/CLEWsCAN2070` | public, redirects to `deltae/C2070` |",
        "| `github.com/UBC-FRESH/CLEWsCAN2070` | 404 |",
        "| C2070 site | `/site/model.html`, `/site/api.html`, `/site/data.html` all HTTP 200 |",
        f"| Install | `uv sync --frozen` succeeded with uv {availability['install']['uv_version']}, Python {availability['install']['python']} |",
        f"| Solver | {MODEL_SOLVER}; no Gurobi licence needed |",
        "",
        "The documented install is `git clone https://github.com/eliasinul/CLEWsCAN2070.git`",
        "then `uv sync`. The package is a `uv` project, not a PyPI wheel.",
        "The site documents the same command sequence that was run.",
        "",
        "## 2. Model run",
        "",
        f"Scenario: **{MODEL_SCENARIO}**. Horizon 2021-2070, single `REGION=CAN`,",
        "13 fixed-width zones. The sequence was:",
        "",
        "```bash",
        "uv sync --frozen",
        "uv run python -m osecan.cli build-sets",
        "uv run python -m osecan.cli build-params",
        "uv run python -m osecan.cli retrieve",
        'uv run python -m osecan.cli clean-demand "Current Measures"',
        "uv run python -m osecan.cli harmonize",
        "python -m osecan.pipeline.assemble --out results/base/input",
        "otoole convert csv datafile results/base/input results/base/osecan.txt config/otoole.yaml",
        "python -m osecan.pipeline.solve --run results/base",
        "python -m osecan.cli report --run results/base --out /tmp/osecan_report.html",
        "```",
        "",
        "The site's seven-command summary omits `assemble` and `otoole convert`;",
        "both are required to turn the CSV parameter set into `results/base/osecan.txt`.",
        "`solve` preprocesses the datafile and runs GLPK through `swiglpk`;",
        "`report` produced an 851 KB HTML dashboard. `clean-demand` produced",
        "9,328 PJ of 2025 national end-use demand, matching the model README's",
        "9,328 PJ. Runs were executed in a temporary clone at `/tmp/osecan_src`;",
        "only the summaries are retained in this repository.",
        "",
        "The baseline and BC-base runs are both optimal:",
        "",
        "| Run | Status | Objective (M$ discounted) | LP size |",
        "|---|---|---:|---|",
        f"| Baseline 50 PJ/$4 placeholder | optimal | {_fmt(1331287.25)} | 273,001 x 234,900 |",
        f"| BC base national rollup | optimal | {_fmt(1331287.25)} | 273,001 x 234,900 |",
        f"| Hydro-frozen base diagnostic | optimal | {_fmt(1346217.65)} | 273,651 x 234,900 |",
        f"| Hydro-frozen high diagnostic | optimal | {_fmt(1346217.65)} | 273,651 x 234,900 |",
        "",
        "The baseline and BC-base objectives are identical to the cent. That is",
        "the first signal that the BC supply parameters do not enter the solution.",
        "The BC-base datafile was verified before assembly: SUPBCW cap is",
        "50.473 PJ in 2021 and 42.959 PJ in 2028-2070, and variable cost is",
        "2.456978 $/GJ. The zero dispatch is an economic result, not a failed",
        "file substitution.",
        "",
        "### SUPBCW activity",
        "",
        "The model has no `SUPBCWB1` or `SUPBCWB2` technology. Its only BC woody",
        "residue supply code is the national `SUPBCW`. Activity is zero in every",
        "year and every run:",
        "",
        "| Run | 2021 | 2025 | 2030 | 2050 | 2070 | Total |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for run, payload in cap_binding["runs"].items():
        sup = payload["supbcw_pj"]
        lines.append(
            f"| {run} | {_fmt(sup['2021'], 3)} | {_fmt(sup['2025'], 3)} | "
            f"{_fmt(sup['2030'], 3)} | {_fmt(sup['2050'], 3)} | "
            f"{_fmt(sup['2070'], 3)} | {_fmt(sup['total'], 3)} |"
        )
    lines += [
        "",
        "`SUPBSW`, `PWRBCW`, and `PWRBIO` are also zero in every run.",
        "The model meets electricity demand from hydro in the base runs and",
        "from gas in the hydro-frozen diagnostics.",
        "",
        "### Emissions and dispatch",
        "",
        "| Run | 2025 (Mt CO2) | 2030 (Mt CO2) | 2050 (Mt CO2) | 2050 generation |",
        "|---|---:|---:|---:|---|",
    ]
    for run, payload in cap_binding["runs"].items():
        gen = payload["generation_2050_pj"]
        gen_text = ", ".join(f"{key} {_fmt(value, 1)} PJ" for key, value in gen.items())
        lines.append(
            f"| {run} | {_fmt(payload['emissions_mt']['2025'], 3)} | "
            f"{_fmt(payload['emissions_mt']['2030'], 3)} | "
            f"{_fmt(payload['emissions_mt']['2050'], 3)} | {gen_text} |"
        )
    lines += [
        "",
        "The base model is a hydro-expansion model: 2050 hydro generation is",
        "3,231.95 PJ and gas is 20.22 PJ. The hydro-frozen diagnostic is a",
        "sensitivity probe that removes the unconstrained-hydro dominance;",
        "gas fills the gap and biomass still does not dispatch.",
        "",
        "### Zonal-file compatibility",
        "",
        "The zonal files were tested directly:",
        "",
        "```text",
        "[FAIL] TotalTechnologyAnnualActivityUpperLimit.csv",
        "        - TECHNOLOGY not in SETS: ['SUPBCWB1', 'SUPBCWB2']",
        "[FAIL] VariableCost.csv",
        "        - TECHNOLOGY not in SETS: ['SUPBCWB1', 'SUPBCWB2']",
        "6 harmonization issue(s) found.",
        "```",
        "",
        "`PWRBCWB1` and `PWRBCWB2` exist, but they consume the national `BCW`",
        "fuel. There is no `SUPBCWB1/B2` supply technology and no `BCWB1/BCWB2`",
        "fuel. Loading the zonal files would require a model-structure change,",
        "not a parameter substitution. The BC base run therefore uses the",
        "national `comparison/SUPBCW_*` rollup, which is exactly B1+B2.",
        "B1 and B2 have identical costs in every case and feed the same national",
        "BCW pool, so adding separate zonal supply technologies would change the",
        "reported split, not the aggregate BCW use or the cap-binding conclusion.",
        "",
        "## 3. Reduced biomass-module economics",
        "",
        "### Plant parameters",
        "",
        "| Plant | Fuel | IAR | Efficiency | Capex ($/kW) | Fixed ($/kW-yr) | Var ($/GJ) | Life (yr) | CF | CO2 (t/GJ) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        "| PWRBCW | BCW | 3.72 | 26.9% | 3,200 | 95 | 0 | 30 | 0.70 | 0 |",
        "| PWRBIO | BIO | 2.85 | 35.1% | 3,000 | 90 | 2.5 | 30 | 0.70 | 0 |",
        "| PWRBSW | BSW | 3.72 | 26.9% | 3,200 | 95 | 0 | 30 | 0.70 | 0 |",
        "| PWRNGS | NGS | 2.70 | 37.0% | 1,100 | 25 | 3.0 | 30 | 0.85 | 0.051 |",
        "| PWRHYD | HYD | 1.00 | 100% | 3,500 | 40 | 0 | 60 | 0.45 | 0 |",
        "",
        "The exact PWRBCW input activity ratio 3.72 gives 26.88% efficiency;",
        "the table rounds it to the documented 26.9%.",
        "",
        "### LCOE (2020 CAD/MWh, 5% discount)",
        "",
        "| Plant | Case | Fuel ($/GJ) | Carbon ($/t) | Capital+fixed | Fuel | Carbon | Var O&M | LCOE |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in lcoe_rows:
        lines.append(
            f"| {row['plant']} | {row['case']} | "
            f"{_fmt(row['fuel_price_usd_per_gj'], 3)} | "
            f"{_fmt(row['carbon_price_usd_per_t'], 0)} | "
            f"{_fmt(row['capital_fixed_usd_per_mwh'])} | "
            f"{_fmt(row['fuel_usd_per_mwh'])} | "
            f"{_fmt(row['carbon_usd_per_mwh'])} | "
            f"{_fmt(row['var_usd_per_mwh'])} | "
            f"**{_fmt(row['lcoe_usd_per_mwh'])}** |"
        )
    lines += [
        "",
        (
            "The PWRBCW low/base/high LCOEs are "
            f"**{_fmt(lookup[('low', 'PWRBCW')]['lcoe_usd_per_mwh'])}**, "
            f"**{_fmt(lookup[('base', 'PWRBCW')]['lcoe_usd_per_mwh'])}**, and "
            f"**{_fmt(lookup[('high', 'PWRBCW')]['lcoe_usd_per_mwh'])} $/MWh**."
        ),
        f"Generic BIO is {_fmt(lookup[('generic', 'PWRBIO')]['lcoe_usd_per_mwh'])} $/MWh.",
        f"The model's gas plant is {_fmt(lookup[('carbon_2026', 'PWRNGS')]['lcoe_usd_per_mwh'])} $/MWh",
        f"at $95/t and {_fmt(lookup[('carbon_2030', 'PWRNGS')]['lcoe_usd_per_mwh'])} $/MWh at $115/t.",
        f"Hydro is {_fmt(lookup[('generic', 'PWRHYD')]['lcoe_usd_per_mwh'])} $/MWh.",
        "",
        "### BCW versus generic BIO and gas",
        "",
        "| Case | BCW $/GJ | BCW fuel $/MWh | BCW LCOE | BIO LCOE | Delta vs BIO | Breakeven vs BIO | Delta vs gas $95 | Delta vs gas $115 | Carbon parity vs gas |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison_rows:
        lines.append(
            f"| {row['case']} | {_fmt(row['bcw_cost_usd_per_gj'], 3)} | "
            f"{_fmt(row['bcw_fuel_usd_per_mwh'])} | "
            f"{_fmt(row['bcw_lcoe_usd_per_mwh'])} | "
            f"{_fmt(row['bio_lcoe_usd_per_mwh'])} | "
            f"{_fmt(row['lcoe_delta_vs_bio_usd_per_mwh'], 2)} | "
            f"{_fmt(row['breakeven_bcw_vs_bio_usd_per_gj'], 3)} | "
            f"{_fmt(row['lcoe_delta_vs_gas95_usd_per_mwh'], 2)} | "
            f"{_fmt(row['lcoe_delta_vs_gas115_usd_per_mwh'], 2)} | "
            f"{_fmt(row['carbon_price_for_parity_with_gas_usd_per_t'], 1)} |"
        )
    lines += [
        "",
        "The per-MWh feedstock costs are:",
        "",
        (
            f"- PWRBCW base: 3.6 x 3.72 x {_fmt(comparison['base']['bcw_cost_usd_per_gj'], 3)} = "
            f"**{_fmt(comparison['base']['bcw_fuel_usd_per_mwh'])} $/MWh**."
        ),
        (
            "- PWRBIO: 3.6 x 2.85 x 3.00 = "
            f"**{_fmt(comparison['base']['bio_fuel_usd_per_mwh'])} $/MWh**."
        ),
        (
            "- Break-even BCW price against generic BIO: "
            f"**{_fmt(breakevens['bcw_vs_bio_usd_per_gj'], 3)} $/GJ**."
        ),
        "",
        (
            "The base and high cases beat generic BIO"
            f" (base by {_fmt(-comparison['base']['lcoe_delta_vs_bio_usd_per_mwh'])} $/MWh, "
            f"high by {_fmt(-comparison['high']['lcoe_delta_vs_bio_usd_per_mwh'])} $/MWh)."
        ),
        "The low case loses to BIO. All three cases lose to the model's gas",
        "plant at the documented $95-115/t carbon schedule.",
        "",
        "### Cap metrics",
        "",
        "| Case | 2021 cap (PJ) | 2028 cap (PJ) | Max electricity 2021 (PJ) | Max electricity 2028 (PJ) | Max electricity 2028 (TWh) |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in ("low", "base", "high"):
        row = cap_metrics[case]
        lines.append(
            f"| {case} | {_fmt(row['cap_2021_pj'], 3)} | "
            f"{_fmt(row['cap_2028_pj'], 3)} | "
            f"{_fmt(row['max_electricity_2021_pj'], 3)} | "
            f"{_fmt(row['max_electricity_2028_pj'], 3)} | "
            f"{_fmt(row['max_electricity_2028_twh'], 3)} |"
        )
    lines += [
        "",
        f"The 50 PJ placeholder is {_fmt(cap_metrics['placeholder']['max_electricity_pj'], 3)} PJ",
        f"of electricity ({_fmt(cap_metrics['placeholder']['max_electricity_twh'], 3)} TWh).",
        f"The base 2028 cap is {_fmt(cap_metrics['base']['max_electricity_2028_pj'], 3)} PJ",
        f"({_fmt(cap_metrics['base']['max_electricity_2028_twh'], 3)} TWh), or",
        f"{_fmt(cap_metrics['base_2028_share_of_2025_hydro_generation_pct'], 2)}% of the model's",
        "2025 hydro generation. The cap is small relative to the power",
        "system; the binding constraint is economics, not the cap.",
        "",
        "The 50 PJ placeholder is applied per SUP technology in",
        "`build_params.py`: `SUPBCW` and `SUPBSW` each get 50 PJ, so the",
        "combined placeholder feedstock cap is 100 PJ, not 50 PJ. There is no",
        "combined biomass feedstock cap.",
        "",
        "### Does the cap bind?",
        "",
        "**No** in every baseline and diagnostic run: `SUPBCW` activity is zero.",
        "The cap is an upper bound that is never approached. The reasons are:",
        "",
        f"1. In the base model, unconstrained hydro is {_fmt(lookup[('generic', 'PWRHYD')]['lcoe_usd_per_mwh'])} $/MWh,",
        f"   below PWRBCW low/base/high ({_fmt(lookup[('low', 'PWRBCW')]['lcoe_usd_per_mwh'])} /",
        f"   {_fmt(lookup[('base', 'PWRBCW')]['lcoe_usd_per_mwh'])} /",
        f"   {_fmt(lookup[('high', 'PWRBCW')]['lcoe_usd_per_mwh'])} $/MWh). Hydro expands to meet demand.",
        "2. When hydro is frozen at 2021 activity as a diagnostic, the model's",
        f"   gas plant is {_fmt(lookup[('carbon_2026', 'PWRNGS')]['lcoe_usd_per_mwh'])}-{_fmt(lookup[('carbon_2030', 'PWRNGS')]['lcoe_usd_per_mwh'])} $/MWh;",
        "   even the high BCW case is above the $115/t gas LCOE.",
        (
            "3. The break-even BCW prices are "
            f"**{_fmt(breakevens['bcw_vs_gas_at_carbon_95_usd_per_gj'], 3)} $/GJ** at $95/t and "
            f"**{_fmt(breakevens['bcw_vs_gas_at_carbon_115_usd_per_gj'], 3)} $/GJ** at $115/t."
        ),
        "   The base case is $2.457/GJ; the high case is $1.966/GJ. Both are above",
        "   the $115/t break-even.",
        (
            "4. Carbon-price parity with gas is reached at "
            f"**{_fmt(breakevens['carbon_price_for_high_bcw_parity_usd_per_t'], 1)} $/t** for the high case, "
            f"**{_fmt(breakevens['carbon_price_for_base_bcw_parity_usd_per_t'], 1)} $/t** for the base case, and "
            f"**{_fmt(breakevens['carbon_price_for_low_bcw_parity_usd_per_t'], 1)} $/t** for the low case."
        ),
        "   The model's schedule is $95/t in 2026 and $115/t in 2030, so none",
        "   of the cases reach parity.",
        "",
        "### Generic-BIO bypass",
        "",
        "The cap constrains `SUPBCW`, not biomass electricity. `PWRBIO` consumes",
        "`BIO` from the uncapped `RNWBIO` supply at $3/GJ. If biomass power were",
        "economic, the model could meet any biomass electricity target with",
        "`PWRBIO` after the BCW cap is exhausted. The BCW cap would only shift",
        "feedstock from BCW to generic BIO; it would not cap biomass generation.",
        "In the current runs the bypass is latent because PWRBIO is also above",
        f"the gas LCOE ({_fmt(lookup[('generic', 'PWRBIO')]['lcoe_usd_per_mwh'])} vs",
        f"{_fmt(lookup[('carbon_2030', 'PWRNGS')]['lcoe_usd_per_mwh'])} $/MWh at $115/t).",
        "",
        *_sensitivity_markdown(sensitivity),
        "## 5. Assumptions and limitations",
        "",
        "- LCOE is a simplified busbar calculation. It excludes transmission,",
        "  backup, balancing, and financing outside the 5% discount rate.",
        "- PWRBCW and PWRBIO use the documented v0.3 input activity ratios and",
        "  placeholder costs. They are not Canadian fleet averages.",
        "- The gas comparator is the model's placeholder plant (capex 1,100 $/kW,",
        "  fuel $3/GJ, CO2 0.051 t/GJ, carbon $95-115/t). A market gas price or",
        "  a different capex would move the break-even.",
        "- The BC zonal caps are FRESH-side provisional and not a C2070 boundary.",
        "- The hydro-frozen diagnostic is a sensitivity probe, not a scenario.",
        "- The model has no `SUPBCWB1/B2`; the BC base run is the national",
        "  `SUPBCW` rollup. Zonal dispatch cannot be reported from this model.",
        "- The model's `TotalDiscountedCost.csv` extraction is empty in this",
        "  version; the objective is read from the GLPK solution instead.",
        "",
        "## 6. Verification",
        "",
        "- Four original OSeCAN solves returned `optimal` (two base, two diagnostics).",
        "- The BC-base control replication and both policy sensitivity solves",
        "  returned `optimal`; the control reproduced 1,331,287.25 M$.",
        "- The zonal `harmonize` probe returned exit 1 with the expected",
        "  `TECHNOLOGY not in SETS` errors.",
        "- The reduced analysis is re-built by",
        "  `python -m workflows.national.clews.osecan_biomass_module`.",
        "- The focused test suite is",
        "  `python -m pytest -q workflows/national/clews/tests/test_osecan_biomass_module.py`.",
        "",
        "## 7. Gaps and next steps",
        "",
        "1. Add `SUPBCWB1/B2` (and `BCWB1/B2` fuels) to `model_structure.py` if",
        "   zonal BC supply constraints are required. The current model cannot",
        "   represent them.",
        "2. Add a hydro resource constraint or a provincial policy that makes",
        "   biomass compete. The $200/t sensitivity shows that the carbon price",
        "   alone is not enough while hydro is unconstrained; with hydro frozen,",
        "   BCW reaches the 42.959 PJ cap at $200/t.",
        "3. Replace the generic `RNWBIO` $3/GJ uncapped supply with a WS3/FABLE",
        "   biomass supply curve, or add a biomass-wide cap. Otherwise the BCW",
        "   cap is bypassed by generic BIO.",
        "4. Replace the placeholder gas capex/fuel/carbon assumptions with",
        "   Canadian values before drawing policy conclusions from the merit",
        "   order.",
        "5. Re-run the model after those changes; this report's run evidence is",
        "   for the public v0.3 placeholder model only.",
        "",
        f"Generated: {availability['generated_utc']}",
        "",
    ]
    return "\n".join(lines)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=columns,
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


def build_all(root: Path = REPO_ROOT) -> dict[str, object]:
    """Build every artifact in memory.

    Args:
        root: Repository root.

    Returns:
        Mapping with the availability, LCOE rows, comparison rows, cap
        binding, and manifest payloads.
    """
    supply_dir = root / SUPPLY_DIR
    cases = load_all_cases(supply_dir)
    lcoe_rows = build_lcoe_rows(cases)
    comparison_rows = build_bcw_vs_bio_rows(cases, lcoe_rows)
    cap_metrics = build_cap_metrics(cases)
    cap_binding = build_cap_binding(cases, cap_metrics, comparison_rows)
    sensitivity = build_sensitivity()
    availability = build_availability()
    generated_utc = str(availability["generated_utc"])
    return {
        "cases": cases,
        "lcoe_rows": lcoe_rows,
        "comparison_rows": comparison_rows,
        "cap_metrics": cap_metrics,
        "cap_binding": cap_binding,
        "sensitivity": sensitivity,
        "availability": availability,
        "generated_utc": generated_utc,
    }


def write_artifacts(root: Path = REPO_ROOT) -> dict[str, object]:
    """Build and write the OSeCAN reduced-analysis artifacts.

    Args:
        root: Repository root.

    Returns:
        Mapping with output paths and the build payload.
    """
    built = build_all(root)
    output_dir = root / OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    availability_path = output_dir / "availability.json"
    lcoe_path = output_dir / "lcoe_by_plant.csv"
    comparison_path = output_dir / "bcw_vs_bio.csv"
    cap_path = output_dir / "cap_binding_analysis.json"
    report_path = output_dir / "OSECAN_RUN_REPORT.md"
    runs_path = output_dir / "model_runs.json"
    sensitivity_path = output_dir / "sensitivity" / "sensitivity_runs.json"
    sensitivity_csv_path = output_dir / "sensitivity" / "sensitivity_summary.csv"
    manifest_path = output_dir / "manifest.json"

    _write_json(availability_path, built["availability"])
    _write_csv(lcoe_path, built["lcoe_rows"])
    _write_csv(comparison_path, built["comparison_rows"])
    _write_json(cap_path, built["cap_binding"])
    _write_json(
        runs_path,
        {
            "schema_version": "1.0",
            "model_commit": MODEL_COMMIT,
            "runs": built["cap_binding"]["runs"],
        },
    )
    _write_json(sensitivity_path, built["sensitivity"])
    _write_csv(sensitivity_csv_path, build_sensitivity_rows(built["sensitivity"]))
    report_path.write_text(
        _markdown_report(
            built["lcoe_rows"],
            built["comparison_rows"],
            built["cap_binding"],
            built["sensitivity"],
            built["availability"],
        ),
        encoding="utf-8",
    )

    outputs = [
        availability_path,
        lcoe_path,
        comparison_path,
        cap_path,
        report_path,
        runs_path,
        sensitivity_path,
        sensitivity_csv_path,
    ]
    manifest = build_manifest(
        root,
        output_dir,
        _input_paths(root / SUPPLY_DIR),
        outputs,
        str(built["generated_utc"]),
    )
    _write_json(manifest_path, manifest)
    return {
        "output_dir": output_dir,
        "paths": [*outputs, manifest_path],
        "manifest": manifest,
        "cap_binding": built["cap_binding"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Run the reduced-analysis builder from the command line.

    Args:
        argv: Optional command-line arguments, excluding the executable name.

    Returns:
        ``0`` after writing the artifacts.
    """
    parser = argparse.ArgumentParser(
        description="Build OSeCAN availability and reduced biomass-module artifacts."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root (default: the module's repository root).",
    )
    args = parser.parse_args(argv)
    result = write_artifacts(args.root)
    print(f"wrote {len(result['paths'])} artifacts to {result['output_dir']}")
    for path in result["paths"]:
        print(f"  {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
