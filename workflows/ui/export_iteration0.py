"""Export the CLEWS/C2070 iteration-0 static data contract for the web UI.

The UI is a static SPA.  This module is the only bridge between the solver
artifacts and ``ui/public/data``.  It parses the run artifacts at the boundary,
normalizes the inconsistent ``runs``/``cases`` shapes, and fails loudly when an
artifact no longer matches the contract.

Usage:
    .venv/bin/python workflows/ui/export_iteration0.py
    .venv/bin/python workflows/ui/export_iteration0.py --out /tmp/ui-data
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(_IMPORT_ROOT))

from workflows.ui.feasibility import assess_harvest_feasibility

SCHEMA_VERSION = "iteration0/1"
YEAR_FIRST = 2021
YEAR_LAST = 2070
YEARS = tuple(range(YEAR_FIRST, YEAR_LAST + 1))
YEAR_SET = frozenset(YEARS)

CATEGORY_UNITS = {
    "dispatch": "PJ electricity",
    "power_hydro": "PJ electricity",
    "power_bcw": "PJ electricity",
    "power_bio": "PJ electricity",
    "power_gas": "PJ electricity",
    "power_wind": "PJ electricity",
    "power_uranium": "PJ electricity",
    "supply": "PJ fuel",
    "bio_supply": "PJ fuel",
    "supply_bc_zonal": "PJ fuel",
    "supply_tier": "PJ fuel",
}

SOLVER_STATUSES = {
    "optimal": "optimal",
    "feasible": "feasible",
    "infeasible": "infeasible",
    "unbounded": "unbounded",
    "not_solved": "not_solved",
    "not solved": "not_solved",
    "timeout": "not_solved",
    "timed_out": "not_solved",
    "unknown": "not_solved",
}

HARVEST_LEVELS = {
    "119m": {
        "level": "119m",
        "m3_per_year": 118_977_339,
        "basis": "observed_2024_hold",
    },
    "150m": {
        "level": "150m",
        "m3_per_year": 150_000_000,
        "basis": "scenario_high_bound_not_forecast",
    },
    "100m": {
        "level": "100m",
        "m3_per_year": 100_000_000,
        "basis": "scenario_low_bound_not_forecast",
    },
    "200m": {
        "level": "200m",
        "m3_per_year": 200_000_000,
        "basis": "scenario_200m_log_basis",
    },
}

HARVEST_EXOGENOUS_CAVEAT = (
    "Harvest is exogenous and free in this model; higher harvest lowers cost and "
    "emissions because no harvest cost/THLB/AAC constraint is modeled. This is a "
    "sensitivity ladder, not a policy frontier."
)

ALLOCATION_THLB_CAVEAT = (
    "The managed-landbase intensity screen is applied to the 2016 tenure-union "
    "managed proxy, not a THLB. It has no operability, accessibility, growth, "
    "yield, or economic content. 150 Mm3 fails the 1.0 m3/ha/yr screen by 6.113 "
    "Mm3 and passes at 1.2; 119 Mm3 passes both. The LP allocation is a "
    "comparison/screening result, not a legal AAC, enforced provincial constraint, "
    "or policy frontier."
)

ALLOCATION_COMPARISON_NOTE = (
    "Observed rows keep the 2024 provincial shares; reallocated rows use the "
    "provincial allocation LP under documented caps and 2024-share L1 objective. "
    "Values are screening/compare only and are excluded from the energy Pareto."
)

ALLOCATION_LP_SUMMARY = (
    "data/derived/national/harvest_basis/provincial_allocation/provincial_allocation_summary.json"
)

C2070_ZONES = (
    "A1",
    "A2",
    "B1",
    "B2",
    "L1",
    "M1",
    "N1",
    "O1",
    "O2",
    "Q1",
    "Q2",
    "S1",
    "T1",
)

PWRBIO_CAPS_PJ = {
    "5pct_zone_generation": 162.608451,
    "10pct_zone_generation": 325.216901,
    "5pct_reference_pwrbio": 0.626693,
    "absolute_bio_input_3572": 35.72,
    "zero": 0.0,
}

FAMILY_SPECS: tuple[dict[str, Any], ...] = (
    {
        "family": "fleet_capacity",
        "label": "CER 2021 fleet-corrected capacity (canonical screening)",
        "dir": "fleet_capacity_119m",
        "case_id": "screening_119m_fleet",
        "label_override": "119 Mm3 screening base — fleet-corrected",
        "description_override": (
            "Screening_v0 119 Mm3 base rerun with the CER 2021 capacity fleet. "
            "Screening only; not policy-grade."
        ),
        "post_fix": True,
        "superseded": False,
        "fleet_corrected": True,
        "screening_only": True,
        "policy_grade": False,
        "canonical": True,
        "harvest_exogenous": True,
        "pareto_eligible": True,
        "tradeoff_eligible": True,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
        "hydro": "upper_2021",
        "carbon_price_default": 200.0,
        "pwrbio_cap": "5pct_zone_generation",
        "nuclear_cap_gw": 0.0,
        "wind_cost_usd_per_gj": 3.0,
        "wind_cap": "20pct_zonal",
    },
    {
        "family": "fleet_screening_ladder",
        "label": "Fleet-corrected harvest sensitivity ladder",
        "dir": "fleet_screening_ladder",
        "cases": (
            {
                "case_id": "screening_100m",
                "dir": "100m",
                "harvest": "100m",
                "label": "100 Mm3 harvest sensitivity — fleet-corrected",
            },
            {
                "case_id": "screening_150m",
                "dir": "150m",
                "harvest": "150m",
                "label": "150 Mm3 harvest sensitivity — fleet-corrected",
            },
            {
                "case_id": "screening_200m",
                "dir": "200m",
                "harvest": "200m",
                "label": "200 Mm3 harvest sensitivity — fleet-corrected",
            },
        ),
        "description_override": (
            "Screening_v0 harvest sensitivity solved with the CER 2021 capacity fleet. "
            "Harvest is exogenous and free: no harvest cost, THLB, or AAC constraint is modeled. "
            "Sensitivity only; not a policy frontier and not policy-grade."
        ),
        "post_fix": True,
        "superseded": False,
        "fleet_corrected": True,
        "screening_only": True,
        "policy_grade": False,
        "canonical": False,
        "harvest_exogenous": True,
        "pareto_eligible": False,
        "tradeoff_eligible": False,
        "supply_curve": "tiered_t1_t4",
        "hydro": "upper_2021",
        "carbon_price_default": 200.0,
        "pwrbio_cap": "5pct_zone_generation",
        "nuclear_cap_gw": 0.0,
        "wind_cost_usd_per_gj": 3.0,
        "wind_cap": "20pct_zonal",
    },
    {
        "family": "allocation_screening",
        "label": "Provincial allocation screening (LP reallocation)",
        "dir": "allocation_screening_ladder",
        "cases": (
            {
                "case_id": "lp_119m",
                "dir": "119m",
                "harvest": "119m",
                "label": "119 Mm3 — LP reallocated allocation",
                "allocation": "lp_119m",
                "allocation_role": "reallocated",
                "feasibility_override": {
                    "status": "pass",
                    "notes": (
                        "Provincial allocation LP reallocates the 119 Mm3 national total "
                        "under documented caps. No documented-cap deficit; the 1.0 and 1.2 "
                        "m3/ha/yr managed-proxy screens are both feasible. The proxy is not "
                        "a THLB. Compare only: this variant is not a policy frontier."
                    ),
                },
            },
            {
                "case_id": "lp_150m",
                "dir": "150m",
                "harvest": "150m",
                "label": "150 Mm3 — LP reallocated allocation",
                "allocation": "lp_150m",
                "allocation_role": "reallocated",
                "feasibility_override": {
                    "status": "conditional",
                    "notes": (
                        "Provincial allocation LP reallocates the 150 Mm3 national total "
                        "under documented caps, but the 1.0 m3/ha/yr managed-proxy screen is "
                        "infeasible by 6.113 Mm3; the 1.2 screen is feasible. The proxy is "
                        "not a THLB. Conditional screening/compare only, not a policy frontier."
                    ),
                },
            },
        ),
        "description_override": (
            "Provincial allocation LP applied to the fleet-corrected 119/150 Mm3 national "
            "totals. Observed shares are reallocated under documented provincial caps. "
            "Harvest remains exogenous and free in the energy objective; these runs are "
            "comparison/context only and are never Pareto eligible."
        ),
        "post_fix": True,
        "superseded": False,
        "fleet_corrected": True,
        "screening_only": True,
        "policy_grade": False,
        "canonical": False,
        "harvest_exogenous": True,
        "pareto_eligible": False,
        "tradeoff_eligible": False,
        "allocation_screening": True,
        "supply_curve": "tiered_t1_t4",
        "hydro": "upper_2021",
        "carbon_price_default": 200.0,
        "pwrbio_cap": "5pct_zone_generation",
        "nuclear_cap_gw": 0.0,
        "wind_cost_usd_per_gj": 3.0,
        "wind_cap": "20pct_zonal",
    },
    {
        "family": "emission_factor_fix",
        "label": "Post-fix emission factors (canonical iteration-0)",
        "dir": "emission_factor_fix",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
        "carbon_price_default": 115.0,
    },
    {
        "family": "pwrbio_cap",
        "label": "PWRBIO cap ladder",
        "dir": "pwrbio_cap",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
    },
    {
        "family": "pwrbio_tight",
        "label": "PWRBIO tight absolute caps",
        "dir": "pwrbio_tight",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
    },
    {
        "family": "nuclear_audit",
        "label": "Nuclear cap audit",
        "dir": "nuclear_audit",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
    },
    {
        "family": "wind_integration_audit",
        "label": "Wind integration audit",
        "dir": "wind_integration_audit",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
    },
    {
        "family": "full_constraint",
        "label": "Full constraint set",
        "dir": "full_constraint",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
        "pwrbio_cap": "5pct_zone_generation",
        "nuclear_cap_gw": 0.0,
        "wind_cost_usd_per_gj": 3.0,
    },
    {
        "family": "full_windcap",
        "label": "Full constraint set with 20% zonal wind cap",
        "dir": "full_windcap",
        "post_fix": True,
        "superseded": False,
        "harvest": "119m",
        "supply_curve": "tiered_t1_t4",
        "pwrbio_cap": "5pct_zone_generation",
        "nuclear_cap_gw": 0.0,
        "wind_cost_usd_per_gj": 3.0,
        "wind_cap": "20pct_zonal",
    },
    {
        "family": "harvest_sensitivity_100_200",
        "label": "Harvest sensitivity 100/200 Mm3 (pre-fix)",
        "dir": "harvest_sensitivity_100_200",
        "post_fix": False,
        "superseded": True,
        "supply_curve": "national_flat_SUPBCW",
    },
    {
        "family": "zonal",
        "label": "BC zonal reconciliation (pre-fix)",
        "dir": "zonal",
        "post_fix": False,
        "superseded": True,
        "harvest": "119m",
        "supply_curve": "national_flat_SUPBCW",
        "hydro": "fixed_2021",
        "carbon_price_default": 200.0,
    },
    {
        "family": "reconcile",
        "label": "BC/national cap reconciliation (pre-fix)",
        "dir": "reconcile",
        "post_fix": False,
        "superseded": True,
        "harvest": "119m",
        "supply_curve": "national_flat_SUPBCW",
        "hydro": "fixed_2021",
        "carbon_price_default": 200.0,
    },
    {
        "family": "reconcile_ffcm",
        "label": "FFCM BC cap reconciliation (pre-fix)",
        "dir": "reconcile_ffcm",
        "post_fix": False,
        "superseded": True,
        "harvest": "119m",
        "supply_curve": "national_flat_SUPBCW",
        "hydro": "fixed_2021",
        "carbon_price_default": 200.0,
    },
    {
        "family": "national_supply_curve",
        "label": "National supply curve (pre-fix, 200 Mm3)",
        "dir": "national_supply_curve",
        "post_fix": False,
        "superseded": True,
        "harvest": "200m",
        "supply_curve": "tiered_t1_t4",
        "carbon_price_default": 115.0,
    },
    {
        "family": "national_supply_curve_demand_sensitivity",
        "label": "National demand sensitivity (pre-fix)",
        "dir": "national_supply_curve/demand_sensitivity",
        "post_fix": False,
        "superseded": True,
        "harvest": "200m",
        "supply_curve": "tiered_t1_t4",
        "carbon_price_default": 200.0,
    },
    {
        "family": "national_150m",
        "label": "National 150 Mm3 high bound (pre-fix)",
        "dir": "national_150m",
        "post_fix": False,
        "superseded": True,
        "harvest": "150m",
        "supply_curve": "national_flat_SUPBCW",
    },
    {
        "family": "national_base_119m",
        "label": "National 119 Mm3 base (pre-fix)",
        "dir": "national_base_119m",
        "post_fix": False,
        "superseded": True,
        "harvest": "119m",
        "supply_curve": "national_flat_SUPBCW",
    },
)

# Input artifacts, kept relative to the repository root.
INPUT_PATHS: dict[str, str] = {
    "harvest_template": "data/derived/national/harvest_basis/harvest_basis_template.csv",
    "harvest_reference": "data/derived/national/harvest_basis/harvest_reference_paths.csv",
    "tile_supply": "data/derived/national/clews_supply/rsyc_tile_production/rsyc_tile_supply_by_tile.csv",
    "tile_harvest_sensitivity": "data/derived/national/clews_supply/rsyc_tile_production/rsyc_tile_harvest_sensitivity.csv",
    "tier_definitions": "data/derived/national/clews/osecan/national_supply_curve/tier_definitions.csv",
    "cap_reconciliation": "data/derived/national/clews/osecan/national_supply_curve/national_cap_reconciliation.csv",
    "supply_basis": "data/derived/national/clews/osecan/national_base_119m/supply_basis.json",
    "managed_landbase_province": "data/derived/national/managed_landbase/managed_landbase_by_province.csv",
    "managed_landbase_ecoprovince": "data/derived/national/managed_landbase/managed_landbase_by_ecoprovince.csv",
    "managed_landbase_manifest": "data/derived/national/managed_landbase/manifest.json",
    "provincial_allocation_summary": "data/derived/national/harvest_basis/provincial_allocation/provincial_allocation_summary.json",
    "provincial_allocation_report": "data/derived/national/harvest_basis/provincial_allocation/PROVINCIAL_ALLOCATION_REPORT.md",
    "provincial_allocation_intensity_1_0": "data/derived/national/harvest_basis/provincial_allocation/intensity_1.0/provincial_allocation_summary.json",
    "provincial_allocation_intensity_1_2": "data/derived/national/harvest_basis/provincial_allocation/intensity_1.2/provincial_allocation_summary.json",
    "provinces": "data/raw/boundaries/canada_adm1_geoboundaries_simplified.geojson",
    "rsyc_tiles": "data/raw/national/rsyc/RSYC_tiles.gpkg",
    "rsyc_ecostrat": "data/raw/national/rsyc/RSYC_ecostrat.gpkg",
    "bc_zone_nodes": "data/derived/national/bc_chain/bc_zone_nodes.csv",
}


class ExportError(RuntimeError):
    """Raised when an artifact no longer matches the static data contract."""


@dataclass(frozen=True)
class RunCase:
    family: str
    case_id: str
    entry: dict[str, Any]
    source_dir: Path


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path | str) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _read_json(path: Path, *, label: str) -> Any:
    if not path.is_file():
        raise ExportError(f"{label}: missing JSON artifact {path}")
    try:
        with path.open() as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise ExportError(f"{label}: invalid JSON in {path}: {exc}") from exc


def _read_csv(path: Path, *, label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ExportError(f"{label}: missing CSV artifact {path}")
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ExportError(f"{label}: empty CSV artifact {path}")
    return rows


def _rows_of(path: Path) -> int | None:
    if path.suffix.lower() != ".csv":
        return None
    with path.open(newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def _finite(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportError(f"{label}: expected number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ExportError(f"{label}: non-finite value {value!r}")
    return number


def _number_or_none(value: Any, *, label: str) -> float | None:
    if value is None:
        return None
    return _finite(value, label=label)


def _year_value(value: Any, year: int, *, label: str) -> float | None:
    """Read a year from a {year: value, by_year: {...}} artifact."""
    if not isinstance(value, dict):
        return None
    key = str(year)
    if key in value:
        return _number_or_none(value[key], label=f"{label}[{year}]")
    by_year = value.get("by_year")
    if isinstance(by_year, dict) and key in by_year:
        return _number_or_none(by_year[key], label=f"{label}.by_year[{year}]")
    return None


def normalize_status(value: Any, *, label: str) -> str:
    """Normalize the solver status shapes used across artifact families."""
    if isinstance(value, dict):
        value = value.get("status")
    if not isinstance(value, str):
        raise ExportError(f"{label}: missing solver status")
    normalized = SOLVER_STATUSES.get(value.strip().lower())
    if normalized is None:
        raise ExportError(f"{label}: unknown solver status {value!r}")
    return normalized


def normalize_metrics(entry: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Parse objective/emissions/energy metrics from one run or case entry."""
    if not isinstance(entry, dict):
        raise ExportError(f"{label}: run entry is not an object")

    objective_raw = entry.get("objective_musd", entry.get("objective"))
    objective = _finite(objective_raw, label=f"{label}.objective")

    emissions_total_raw = entry.get("emissions_total_mt")
    if emissions_total_raw is None:
        emissions_total_raw = entry.get("emissions_mt_total")
    if emissions_total_raw is None:
        emissions_mt = entry.get("emissions_mt")
        if isinstance(emissions_mt, dict):
            emissions_total_raw = emissions_mt.get("total")
    emissions_total = _finite(emissions_total_raw, label=f"{label}.emissions_total_mt")

    emissions_2050 = _year_value(entry.get("emissions_mt"), 2050, label=f"{label}.emissions_mt")
    if emissions_2050 is None:
        emissions_2050 = _number_or_none(
            entry.get("emissions_2050_mt"), label=f"{label}.emissions_2050_mt"
        )
    if emissions_2050 is None:
        emissions_2050 = _year_value(
            entry.get("emissions_mt_by_year"),
            2050,
            label=f"{label}.emissions_mt_by_year",
        )
    if emissions_2050 is None:
        raise ExportError(f"{label}: missing 2050 emissions")

    national_bcw_2050 = _year_value(
        entry.get("national_bcw_supply_pj"), 2050, label=f"{label}.national_bcw_supply_pj"
    )
    if national_bcw_2050 is None:
        parts = [
            _year_value(entry.get(field), 2050, label=f"{label}.{field}")
            for field in ("supbcw_pj", "supbcwb1_pj", "supbcwb2_pj")
        ]
        present = [part for part in parts if part is not None]
        national_bcw_2050 = sum(present) if present else None
    if national_bcw_2050 is None:
        supply = entry.get("supply")
        if isinstance(supply, dict):
            national_bcw_2050 = _number_or_none(
                supply.get("national_total_pj_2050"),
                label=f"{label}.supply.national_total_pj_2050",
            )

    pwrbcw_2050 = _year_value(
        entry.get("pwrbcw_total_pj"), 2050, label=f"{label}.pwrbcw_total_pj"
    )
    if pwrbcw_2050 is None:
        parts = [
            _year_value(entry.get(field), 2050, label=f"{label}.{field}")
            for field in ("pwrbcwb1_pj", "pwrbcwb2_pj", "pwrbcw_other_zone_pj")
        ]
        present = [part for part in parts if part is not None]
        pwrbcw_2050 = sum(present) if present else None

    bio_share = _number_or_none(entry.get("bio_share_2050"), label=f"{label}.bio_share_2050")

    return {
        "objective": objective,
        "emissions_total": emissions_total,
        "emissions_2050": emissions_2050,
        "national_bcw_2050": national_bcw_2050,
        "pwrbcw_2050": pwrbcw_2050,
        "bio_share_2050": bio_share,
        "bio_share_basis": "run_summary" if bio_share is not None else None,
    }


def _has_metrics(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    has_objective = entry.get("objective_musd") is not None or entry.get("objective") is not None
    has_emissions = any(
        entry.get(field) is not None
        for field in ("emissions_total_mt", "emissions_mt_total", "emissions_mt")
    )
    return has_objective and has_emissions


def select_case_table(summaries: dict[str, Any], *, family: str) -> tuple[str, dict[str, Any]]:
    """Pick ``runs`` or ``cases``; the two names are normalized here."""
    for key in ("runs", "cases"):
        table = summaries.get(key)
        if isinstance(table, dict) and any(_has_metrics(value) for value in table.values()):
            return key, table
    raise ExportError(f"{family}: neither runs nor cases contains objective/emissions metrics")


def discover_runs(artifacts_root: Path) -> list[RunCase]:
    runs: list[RunCase] = []
    for spec in FAMILY_SPECS:
        cases = spec.get("cases")
        if cases:
            for case in cases:
                source_dir = artifacts_root / spec["dir"] / case["dir"]
                summaries = _read_json(
                    source_dir / "run_summaries.json", label=spec["family"]
                )
                if not _has_metrics(summaries):
                    raise ExportError(
                        f"{spec['family']}/{case['case_id']}: flat summary has no metrics"
                    )
                entry = dict(summaries)
                entry["harvest_level"] = case["harvest"]
                entry["label"] = case["label"]
                for optional in ("allocation", "allocation_role", "feasibility_override"):
                    if optional in case:
                        entry[optional] = case[optional]
                runs.append(
                    RunCase(
                        family=spec["family"],
                        case_id=case["case_id"],
                        entry=entry,
                        source_dir=source_dir,
                    )
                )
            continue

        family_dir = artifacts_root / spec["dir"]
        summaries = _read_json(family_dir / "run_summaries.json", label=spec["family"])
        if _has_metrics(summaries):
            case_id = spec.get("case_id") or summaries.get("scenario_id")
            if not isinstance(case_id, str) or not case_id:
                raise ExportError(f"{spec['family']}: flat summary has no scenario_id")
            runs.append(
                RunCase(
                    family=spec["family"],
                    case_id=case_id,
                    entry=summaries,
                    source_dir=family_dir,
                )
            )
            continue
        table_key, table = select_case_table(summaries, family=spec["family"])
        for case_id, entry in table.items():
            runs.append(
                RunCase(
                    family=spec["family"],
                    case_id=case_id,
                    entry=entry,
                    source_dir=family_dir,
                )
            )
        if table_key not in ("runs", "cases"):
            raise ExportError(f"{spec['family']}: impossible case table {table_key}")
    if not runs:
        raise ExportError("no scenario runs discovered")
    return runs


def scenario_id(family: str, case_id: str) -> str:
    return f"{family}__{case_id}"


def dispatch_run_id(entry: dict[str, Any], case_id: str) -> str:
    run = entry.get("run")
    if isinstance(run, str) and run:
        return run
    scenario = entry.get("scenario_id")
    return scenario if isinstance(scenario, str) and scenario else case_id


def _pwrbio_cap_for(spec: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    basis: str | None = None
    if re.search(r"pwrbio_cap10", case_id):
        basis = "10pct_zone_generation"
    elif re.search(r"pwrbio_cap5ofbio", case_id):
        basis = "5pct_reference_pwrbio"
    elif re.search(r"pwrbio_cap5", case_id):
        basis = "5pct_zone_generation"
    elif re.search(r"pwrbio_cap3572", case_id):
        basis = "absolute_bio_input_3572"
    elif re.search(r"pwrbio_cap0", case_id):
        basis = "zero"
    elif re.search(r"pwrbio5", case_id):
        basis = "5pct_zone_generation"
    elif spec.get("pwrbio_cap"):
        basis = spec["pwrbio_cap"]
    if basis is None:
        return None
    return {"basis": basis, "total_pj": PWRBIO_CAPS_PJ[basis]}


def _nuclear_cap_for(spec: dict[str, Any], case_id: str) -> float | None:
    if re.search(r"nuclear_cap10gw", case_id):
        return 10.0
    if re.search(r"nuclear_cap0", case_id):
        return 0.0
    return spec.get("nuclear_cap_gw")


def _wind_for(spec: dict[str, Any], case_id: str) -> dict[str, Any]:
    cap = "none"
    integration: str | None = None
    cost = float(spec.get("wind_cost_usd_per_gj", 0.0))
    if re.search(r"wind_?cap20", case_id) or spec.get("wind_cap") == "20pct_zonal":
        cap = "20pct_zonal"
    elif re.search(r"wind_?int3", case_id):
        integration = "3pct"
        cost = 3.0
    elif re.search(r"wind_?int5", case_id):
        integration = "5pct"
        cost = 3.0
    elif re.search(r"wind3", case_id):
        cost = 3.0
    return {
        "cap": cap,
        "integration_limit": integration,
        "cost_usd_per_gj": cost,
    }


def _carbon_price_for(
    spec: dict[str, Any], summaries: dict[str, Any], entry: dict[str, Any], case_id: str
) -> float:
    direct = entry.get("carbon_price_from_2026_usd_per_t")
    if direct is not None:
        return _finite(direct, label=f"{case_id}.carbon_price_from_2026_usd_per_t")
    match = re.search(r"carbon_(\d{3})", case_id)
    if match:
        return float(match.group(1))
    tier_match = re.search(r"tier_(\d{3})", case_id)
    if tier_match:
        return float(tier_match.group(1))
    if "stress" in case_id.lower():
        return 200.0
    top_level = summaries.get("carbon_price_from_2026_usd_per_t")
    if isinstance(top_level, (int, float)) and not isinstance(top_level, bool):
        return float(top_level)
    default = spec.get("carbon_price_default")
    if default is not None:
        return float(default)
    forcing = summaries.get("forcing")
    if isinstance(forcing, dict):
        price = forcing.get("carbon_price_usd_per_t")
        if isinstance(price, (int, float)) and not isinstance(price, bool):
            return float(price)
        if isinstance(price, dict):
            values = [value for value in price.values() if isinstance(value, (int, float))]
            if values:
                return float(values[-1])
    raise ExportError(f"{case_id}: missing carbon price")


def _hydro_mode_for(
    spec: dict[str, Any], entry: dict[str, Any], summaries_forcing: dict[str, Any]
) -> str:
    if entry.get("hydro_capped_at_2021") is True or entry.get("hydro_2021_upper_cap") is True:
        return "upper_2021"
    if spec.get("hydro") == "upper_2021":
        return "upper_2021"
    if entry.get("hydro_fixed_lower_and_upper") is True or spec.get("hydro") == "fixed_2021":
        return "fixed_2021"
    label = str(entry.get("label", "")).lower()
    if "hydro fixed" in label:
        return "fixed_2021"
    if "hydro capped" in label or "hydro cap" in label:
        return "upper_2021"
    forcing = summaries_forcing.get("hydro")
    if isinstance(forcing, str):
        lowered = forcing.lower()
        if "fixed" in lowered:
            return "fixed_2021"
        if "cap" in lowered:
            return "upper_2021"
    return "normal"


def _recovery_for(entry: dict[str, Any], summaries: dict[str, Any]) -> float | None:
    supply_case = entry.get("supply_case")
    if supply_case == "low":
        return 0.35
    if supply_case == "base":
        return 0.6
    if supply_case == "high":
        return 0.75
    forcing = summaries.get("forcing")
    if isinstance(forcing, dict) and forcing.get("supply_recovery") is not None:
        return float(forcing["supply_recovery"])
    return 0.6


def _bio_cost_for(entry: dict[str, Any], summaries: dict[str, Any]) -> float | None:
    direct = entry.get("generic_bio_cost_usd_per_gj")
    if direct is not None:
        return _finite(direct, label="generic_bio_cost_usd_per_gj")
    forcing = summaries.get("forcing")
    if isinstance(forcing, dict):
        value = forcing.get("generic_bio_cost_usd_per_gj")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 3.0


def _summary_harvest_m3(entry: dict[str, Any], *, label: str) -> float | None:
    supply = entry.get("supply")
    if not isinstance(supply, dict):
        return None
    raw = supply.get("harvest_level_m3_per_year")
    if raw is None:
        return None
    value = _finite(raw, label=f"{label}.supply.harvest_level_m3_per_year")
    if value <= 0:
        raise ExportError(f"{label}: harvest volume must be positive, got {value}")
    return value


def load_thlb_intensity_screen(root: Path) -> dict[str, Any]:
    """Read the LP intensity-screen metadata (deficits by case and intensity)."""
    path = root / ALLOCATION_LP_SUMMARY
    summary = _read_json(path, label="provincial allocation summary")
    screen = summary.get("thlb_intensity_screen")
    if not isinstance(screen, dict):
        raise ExportError(f"{path}: thlb_intensity_screen block is missing")
    values = screen.get("intensity_values_m3_per_ha_yr")
    deficits = screen.get("deficits_m3")
    if not isinstance(values, list) or not isinstance(deficits, dict):
        raise ExportError(f"{path}: thlb_intensity_screen is incomplete")
    return screen


def thlb_intensity_for(screen: dict[str, Any], level: str) -> dict[str, Any]:
    """Return intensity status/deficit for one harvest level."""
    deficits = screen["deficits_m3"].get(level)
    if not isinstance(deficits, dict):
        raise ExportError(f"thlb intensity screen has no deficits for {level}")
    result: dict[str, Any] = {}
    for value in screen["intensity_values_m3_per_ha_yr"]:
        key = f"{float(value):.1f}"
        if key not in deficits:
            raise ExportError(f"thlb intensity screen has no {key} deficit for {level}")
        deficit = _finite(deficits[key], label=f"thlb_intensity.{level}.{key}")
        result[key] = {
            "intensity_m3_per_ha_yr": float(value),
            "status": "pass" if deficit <= 0.0 else "fail",
            "deficit_m3": deficit,
        }
    return result


def build_dimensions(
    spec: dict[str, Any],
    summaries: dict[str, Any],
    entry: dict[str, Any],
    case_id: str,
) -> dict[str, Any]:
    harvest_level = entry.get("harvest_level")
    if harvest_level is None and case_id in HARVEST_LEVELS:
        harvest_level = case_id
    if harvest_level is None:
        harvest_level = spec.get("harvest")
    if harvest_level not in HARVEST_LEVELS:
        raise ExportError(f"{case_id}: missing harvest level")
    harvest = dict(HARVEST_LEVELS[harvest_level])
    summary_harvest = _summary_harvest_m3(entry, label=case_id)
    if summary_harvest is not None:
        if abs(summary_harvest - harvest["m3_per_year"]) > 0.5:
            raise ExportError(
                f"{case_id}: summary harvest {summary_harvest} does not match "
                f"the {harvest_level} basis {harvest['m3_per_year']}"
            )
        harvest["m3_per_year"] = summary_harvest

    feasibility = assess_harvest_feasibility(harvest["m3_per_year"])
    harvest["feasibility_status"] = feasibility.status
    harvest["feasibility_notes"] = list(feasibility.notes)
    override = entry.get("feasibility_override")
    if override is not None:
        status = override.get("status")
        notes = override.get("notes")
        if status not in ("pass", "conditional", "fail"):
            raise ExportError(f"{case_id}: invalid feasibility override {status!r}")
        if isinstance(notes, str) and notes.strip():
            notes = [notes]
        if not isinstance(notes, list) or not notes:
            raise ExportError(f"{case_id}: feasibility override notes are missing")
        harvest["feasibility_status"] = status
        harvest["feasibility_notes"] = list(notes)

    return {
        "harvest": harvest,
        "harvest_m3_per_year": harvest["m3_per_year"],
        "carbon_price_usd_per_t": _carbon_price_for(spec, summaries, entry, case_id),
        "bio_cost_usd_per_gj": _bio_cost_for(entry, summaries),
        "recovery_fraction": _recovery_for(entry, summaries),
        "supply_curve": spec["supply_curve"],
        "hydro_mode": _hydro_mode_for(spec, entry, summaries.get("forcing", {})),
        "pwrbio_cap": _pwrbio_cap_for(spec, case_id),
        "nuclear_cap_gw": _nuclear_cap_for(spec, case_id),
        "wind": _wind_for(spec, case_id),
        "zero_tiers": _zero_tiers_for(case_id),
    }


def _zero_tiers_for(case_id: str) -> list[str]:
    if "t3t4zero" in case_id:
        return ["T3", "T4"]
    if "t4zero" in case_id:
        return ["T4"]
    return []


def build_scenario(spec: dict[str, Any], summaries: dict[str, Any], case_id: str, entry: dict[str, Any]) -> dict[str, Any]:
    scenario_key = scenario_id(spec["family"], case_id)
    metrics = normalize_metrics(entry, label=scenario_key)
    dimensions = build_dimensions(spec, summaries, entry, case_id)
    metrics["harvest_m3_per_year"] = dimensions["harvest_m3_per_year"]
    solver = normalize_status(
        entry.get("solver_status", entry.get("status")), label=scenario_key
    )
    label = spec.get("label_override") or entry.get("label") or entry.get("scenario_label") or case_id
    is_diagnostic = (
        "diagnostic" in str(label).lower()
        or case_id.startswith("diag_")
        or entry.get("kind") == "diagnostic"
    )
    superseded = bool(spec["superseded"])
    fleet_corrected = bool(spec.get("fleet_corrected"))
    superseded_for_fleet = not fleet_corrected
    publishable = (
        solver == "optimal"
        and not superseded
        and not is_diagnostic
        and not superseded_for_fleet
    )
    harvest_exogenous = bool(spec.get("harvest_exogenous"))
    feasibility = dimensions["harvest"]
    feasibility_status = feasibility["feasibility_status"]
    pareto_eligible = (
        bool(spec.get("pareto_eligible"))
        and publishable
        and feasibility_status == "pass"
    )
    tradeoff_eligible = pareto_eligible
    allocation = entry.get("allocation")
    allocation_role = entry.get("allocation_role")
    thlb_intensity = None
    if allocation is not None:
        if not isinstance(allocation, str) or not allocation.startswith("lp_"):
            raise ExportError(f"{scenario_key}: invalid allocation {allocation!r}")
        if allocation_role != "reallocated":
            raise ExportError(f"{scenario_key}: allocation role must be 'reallocated'")
        screen = load_thlb_intensity_screen(Path(spec["repo_root"]))
        thlb_intensity = thlb_intensity_for(screen, str(entry["harvest_level"]))

    return {
        "scenario_id": scenario_key,
        "family": spec["family"],
        "family_label": spec["label"],
        "label": label,
        "description": spec.get("description_override") or entry.get("description"),
        "feasibility_status": feasibility_status,
        "feasibility_notes": list(feasibility["feasibility_notes"]),
        "pareto_eligible": pareto_eligible,
        "harvest_exogenous": harvest_exogenous,
        "allocation": allocation,
        "allocation_role": allocation_role,
        "thlb_intensity": thlb_intensity,
        "source_run": case_id,
        "dispatch_run": dispatch_run_id(entry, case_id),
        "sources": [
            _relative(Path(spec["source_dir"]) / "run_summaries.json", spec["repo_root"]),
            _relative(Path(spec["source_dir"]) / "manifest.json", spec["repo_root"]),
            _relative(Path(spec["source_dir"]) / "dispatch_by_zone.csv", spec["repo_root"]),
        ],
        "dimensions": dimensions,
        "metrics": metrics,
        "flags": {
            "solver": solver,
            "publishable": publishable,
            "superseded_for_emissions": superseded,
            "superseded_for_fleet": superseded_for_fleet,
            "fleet_corrected": fleet_corrected,
            "canonical": bool(spec.get("canonical")) and publishable,
            "screening_only": bool(spec.get("screening_only")),
            "policy_grade": bool(spec.get("policy_grade")),
            "harvest_exogenous": harvest_exogenous,
            "feasibility_status": feasibility_status,
            "pareto_eligible": pareto_eligible,
            "tradeoff_eligible": tradeoff_eligible,
            "allocation_screening": bool(spec.get("allocation_screening")),
            "diagnostic": is_diagnostic or feasibility_status == "fail",
            "post_fix_emissions": bool(spec["post_fix"]),
        },
    }


def _province_allocation_rows(root: Path, level: str) -> list[dict[str, Any]]:
    path = (
        root
        / "data/derived/national/harvest_basis/provincial_allocation"
        / f"{level}.csv"
    )
    rows = _read_csv(path, label=f"provincial allocation {level}")
    totals = {"119m": 118_977_339.0, "150m": 150_000_000.0}
    total = totals[level]
    payload: list[dict[str, Any]] = []
    for row in rows:
        if row["province"] == "NU":
            continue
        observed_m3 = _finite(float(row["observed_2024_m3"]), label=f"{level}.observed_2024_m3")
        harvest_m3 = _finite(float(row["harvest_m3"]), label=f"{level}.harvest_m3")
        upper_bound_m3 = _finite(float(row["upper_bound"]), label=f"{level}.upper_bound")
        payload.append(
            {
                "province": row["province"],
                "observed_2024_m3": observed_m3,
                "harvest_m3": harvest_m3,
                "delta_m3": harvest_m3 - float(row["observed_share"]) * total,
                "upper_bound_m3": upper_bound_m3,
                "binding": row["binding"] == "true",
            }
        )
    return payload


def build_allocation_comparison(
    root: Path, scenarios: list[dict[str, Any]]
) -> dict[str, Any]:
    """Pair observed 119/150 runs with their LP-reallocated allocation variants."""
    by_id = {scenario["scenario_id"]: scenario for scenario in scenarios}
    pairs: list[dict[str, Any]] = []
    for level, observed_id, reallocated_id in (
        ("119m", "fleet_capacity__screening_119m_fleet", "allocation_screening__lp_119m"),
        ("150m", "fleet_screening_ladder__screening_150m", "allocation_screening__lp_150m"),
    ):
        observed = by_id.get(observed_id)
        reallocated = by_id.get(reallocated_id)
        if observed is None or reallocated is None:
            raise ExportError(
                f"allocation comparison is missing {observed_id!r} or {reallocated_id!r}"
            )
        intensity = reallocated.get("thlb_intensity")
        if not isinstance(intensity, dict) or not intensity:
            raise ExportError(f"{reallocated_id}: thlb intensity block is missing")
        pairs.append(
            {
                "harvest_m3": float(reallocated["dimensions"]["harvest_m3_per_year"]),
                "observed_scenario_id": observed_id,
                "reallocated_scenario_id": reallocated_id,
                "observed_status": observed["feasibility_status"],
                "reallocated_status": reallocated["feasibility_status"],
                "intensity": intensity,
                "province_rows": _province_allocation_rows(root, level),
            }
        )
    return {
        "status": "comparison/context only; excluded from the energy Pareto",
        "note": ALLOCATION_COMPARISON_NOTE,
        "thlb_caveat": ALLOCATION_THLB_CAVEAT,
        "intensity_values_m3_per_ha_yr": sorted(
            float(value)
            for value in next(iter(pairs))["intensity"].keys()
        ),
        "pairs": pairs,
    }


def load_dispatch_rows(path: Path, *, run_id: str, scenario_key: str) -> list[dict[str, Any]]:
    rows = _read_csv(path, label=scenario_key)
    selected = [row for row in rows if row.get("run") == run_id]
    if not selected:
        available = sorted({row.get("run", "") for row in rows})
        raise ExportError(
            f"{scenario_key}: no dispatch rows for run {run_id!r}; available runs: {available}"
        )
    return selected


def build_output(
    scenario_key: str,
    rows: list[dict[str, Any]],
    *,
    source_path: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    technologies: list[str] = []
    categories: list[str] = []
    zones: list[str] = []
    years: list[int] = []
    values: list[float] = []
    checks: list[dict[str, Any]] = []
    excluded: Counter[str] = Counter()

    for index, row in enumerate(rows):
        label = f"{scenario_key}: dispatch row {index + 2}"
        category = row.get("category")
        if category == "other":
            # The fleet run records demand/audit rows under ``other``.  The
            # static bundle is a supply-side ledger only, so keep them out of
            # the mixed-unit arrays and report the exclusion explicitly.
            excluded["other"] += 1
            continue
        if category not in CATEGORY_UNITS:
            raise ExportError(f"{label}: unknown dispatch category {category!r}")
        year = row.get("year")
        if year is None:
            raise ExportError(f"{label}: missing year")
        try:
            year_number = int(year)
        except (TypeError, ValueError) as exc:
            raise ExportError(f"{label}: invalid year {year!r}") from exc
        if year_number not in YEAR_SET:
            raise ExportError(f"{label}: year {year_number} outside {YEAR_FIRST}-{YEAR_LAST}")
        technology = row.get("technology")
        zone = row.get("zone")
        if not technology or not zone:
            raise ExportError(f"{label}: missing technology or zone")
        value = _finite(float(row["value_pj"]), label=f"{label}.value_pj")

        technologies.append(technology)
        categories.append(category)
        zones.append(zone)
        years.append(year_number)
        values.append(round(value, 6))

    distinct_years = sorted(set(years))
    if distinct_years != list(YEARS):
        raise ExportError(
            f"{scenario_key}: expected {YEAR_FIRST}-{YEAR_LAST} ({len(YEARS)} years), "
            f"got {len(distinct_years)} years {distinct_years[:3]}...{distinct_years[-3:]}"
        )
    if any(not math.isfinite(value) for value in values):
        raise ExportError(f"{scenario_key}: non-finite dispatch value")

    payload = {
        "scenario_id": scenario_key,
        "source": source_path,
        "source_row_count": len(rows),
        "row_count": len(values),
        "excluded_categories": dict(excluded),
        "technology": technologies,
        "category": categories,
        "zone": zones,
        "year": years,
        "value_pj": values,
        "units_by_category": {
            category: CATEGORY_UNITS[category] for category in sorted(set(categories))
        },
    }
    checks.append({"name": "dispatch_rows", "ok": len(values) > 0, "detail": len(values)})
    return payload, checks


def derive_bio_share_2050(output: dict[str, Any]) -> float | None:
    """PWRBIO electricity share in 2050, or None when the output has no generation."""
    numerator = 0.0
    denominator = 0.0
    for technology, category, year, value in zip(
        output["technology"],
        output["category"],
        output["year"],
        output["value_pj"],
        strict=True,
    ):
        if year != 2050 or output["units_by_category"].get(category) != "PJ electricity":
            continue
        if technology.startswith("PWRBIO"):
            numerator += value
        if technology.startswith("PWR") and not technology.startswith("PWRTRN"):
            denominator += value
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)


def derive_pwrbcw_2050(output: dict[str, Any]) -> float | None:
    """PWRBCW electricity in 2050, or None when the dispatch has no PWRBCW rows."""
    total = 0.0
    seen = False
    for technology, category, year, value in zip(
        output["technology"],
        output["category"],
        output["year"],
        output["value_pj"],
        strict=True,
    ):
        if (
            year == 2050
            and output["units_by_category"].get(category) == "PJ electricity"
            and technology.startswith("PWRBCW")
        ):
            total += value
            seen = True
    return round(total, 6) if seen else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(payload, handle, indent=None, separators=(",", ":"), allow_nan=False)
        handle.write("\n")


def _clear_generated_files(out_dir: Path) -> None:
    """Remove prior generated payloads so reruns cannot leave stale scenarios."""
    for pattern in ("outputs/*.json", "inputs/*.json", "spatial/*.json", "spatial/*.geojson"):
        for path in out_dir.glob(pattern):
            path.unlink()


def _series(rows: list[dict[str, str]], field: str) -> list[float | None]:
    return [
        float(row[field]) if row.get(field) not in (None, "") else None
        for row in rows
    ]


def build_harvest_basis(root: Path) -> dict[str, Any]:
    template_path = root / INPUT_PATHS["harvest_template"]
    reference_path = root / INPUT_PATHS["harvest_reference"]
    template_rows = _read_csv(template_path, label="harvest template")
    reference_rows = _read_csv(reference_path, label="harvest reference paths")

    template_years = sorted({int(row["year"]) for row in template_rows})
    reference_years = [int(row["year"]) for row in reference_rows]
    if reference_years != list(YEARS):
        raise ExportError("harvest reference paths do not cover exactly 2021-2070")

    return {
        "schema_version": SCHEMA_VERSION,
        "template": {
            "path": INPUT_PATHS["harvest_template"],
            "rows": len(template_rows),
            "provinces": sorted({row["province"] for row in template_rows}),
            "years": template_years,
            "harvest_types": dict(Counter(row["harvest_type"] for row in template_rows)),
            "scenarios": dict(Counter(row["scenario"] for row in template_rows)),
        },
        "reference_paths": {
            "path": INPUT_PATHS["harvest_reference"],
            "rows": len(reference_rows),
            "years": reference_years,
            "series": {
                "observed_2021_2024_m3": _series(reference_rows, "observed_2021_2024_m3"),
                "base_hold_2024_m3": _series(reference_rows, "base_hold_2024_m3"),
                "low_100m_m3": _series(reference_rows, "low_100m_m3"),
                "high_150m_m3": _series(reference_rows, "high_150m_m3"),
            },
        },
        "units": {"volume": "m3/year"},
        "caveats": [
            "Low-100M and high-150M are scenario bounds, not forecasts.",
            "The 150M high case must not be presented as the central basis.",
        ],
    }


def build_supply_curve(root: Path) -> dict[str, Any]:
    tier_rows = _read_csv(root / INPUT_PATHS["tier_definitions"], label="tier definitions")
    recon_rows = _read_csv(root / INPUT_PATHS["cap_reconciliation"], label="cap reconciliation")
    supply_basis = _read_json(root / INPUT_PATHS["supply_basis"], label="supply basis")

    recon_years = [int(row["year"]) for row in recon_rows]
    if recon_years != list(YEARS):
        raise ExportError("national cap reconciliation does not cover exactly 2021-2070")

    tiers: list[dict[str, Any]] = [
        {
            "tier": row["tier"],
            "technology": row["technology"],
            "lower_pj": _finite(float(row["lower_pj"]), label="tier.lower_pj"),
            "upper_pj": _finite(float(row["upper_pj"]), label="tier.upper_pj"),
            "incremental_cap_pj": _finite(
                float(row["incremental_cap_pj"]), label="tier.incremental_cap_pj"
            ),
            "variable_cost_usd_per_gj": _finite(
                float(row["variable_cost_usd_per_gj"]), label="tier.variable_cost_usd_per_gj"
            ),
            "cost_basis": row["cost_basis"],
            "scenario_flag": row["scenario_flag"],
        }
        for row in tier_rows
    ]
    tier_sum = sum(tier["incremental_cap_pj"] for tier in tiers)
    tier_upper_max = max(tier["upper_pj"] for tier in tiers)

    tier_sum_series = _series(recon_rows, "tier_sum_pj")
    rest_series = _series(recon_rows, "rest_of_canada_pj")
    national_series = _series(recon_rows, "national_total_pj")
    tier_residuals = [
        abs(float(tier) - float(rest))
        for tier, rest in zip(tier_sum_series, rest_series, strict=True)
        if tier is not None and rest is not None
    ]
    max_tier_residual = max(tier_residuals) if tier_residuals else math.inf
    flags_true = all(
        row.get("tier_sum_equals_rest") == "True"
        and row.get("national_equals_200m_total") == "True"
        for row in recon_rows
    )
    if not flags_true:
        raise ExportError("national cap reconciliation contains a failed boolean check")
    if abs(tier_sum - tier_upper_max) > 1e-6:
        raise ExportError(f"tier sum {tier_sum} does not equal terminal upper bound {tier_upper_max}")

    cap_basis = supply_basis.get("national_cap_pj_by_year", {}).get("base", {})
    if sorted(int(year) for year in cap_basis) != list(YEARS):
        raise ExportError("supply_basis national cap base series does not cover 2021-2070")

    return {
        "schema_version": SCHEMA_VERSION,
        "tiers": tiers,
        "tier_sum_pj": round(tier_sum, 6),
        "tier_terminal_upper_pj": round(tier_upper_max, 6),
        "reconciliation": {
            "path": INPUT_PATHS["cap_reconciliation"],
            "rows": len(recon_rows),
            "years": recon_years,
            "tier_sum_pj": tier_sum_series,
            "rest_of_canada_pj": rest_series,
            "national_total_pj": national_series,
            "max_abs_tier_residual_pj": round(max_tier_residual, 9),
            "all_tier_sum_equals_rest": True,
            "all_national_equals_200m_total": True,
        },
        "supply_basis": {
            "path": INPUT_PATHS["supply_basis"],
            "superseded_for_emissions": True,
            "note": "Physical supply basis only; the source run directory is superseded for emissions/objective.",
            "harvest_basis": supply_basis.get("harvest_basis"),
            "costs_usd_per_gj": supply_basis.get("costs_usd_per_gj"),
            "log_share": supply_basis.get("log_share"),
            "gross_odt_per_year": supply_basis.get("gross_odt_per_year"),
            "bc_zonal_total_pj": supply_basis.get("bc_zonal_total_pj"),
            "national_cap_pj_by_year": {
                "base": [cap_basis[str(year)] for year in YEARS],
            },
        },
        "units": {
            "cap": "PJ fuel",
            "cost": "USD/GJ",
            "volume": "m3/year",
        },
    }


def build_managed_landbase(root: Path) -> dict[str, Any]:
    province_rows = _read_csv(root / INPUT_PATHS["managed_landbase_province"], label="managed landbase")
    ecoprovince_rows = _read_csv(
        root / INPUT_PATHS["managed_landbase_ecoprovince"], label="managed landbase ecoprovince"
    )
    manifest = _read_json(root / INPUT_PATHS["managed_landbase_manifest"], label="managed landbase manifest")

    totals: dict[str, dict[str, float]] = {}
    for row in province_rows:
        province = row["province"]
        bucket = totals.setdefault(
            province, {"forest_area_ha": 0.0, "managed_proxy_ha": 0.0, "tenure_footprint_ha": 0.0}
        )
        bucket["forest_area_ha"] += float(row["forest_area_ha"] or 0.0)
        bucket["managed_proxy_ha"] += float(row["managed_proxy_ha"] or 0.0)
        bucket["tenure_footprint_ha"] += float(row["tenure_footprint_ha"] or 0.0)

    by_province: list[dict[str, Any]] = []
    for province in sorted(totals):
        bucket = totals[province]
        forest_area = bucket["forest_area_ha"]
        share = bucket["managed_proxy_ha"] / forest_area if forest_area > 0 else None
        by_province.append(
            {
                "province": province,
                "forest_area_ha": round(forest_area, 1),
                "managed_proxy_ha": round(bucket["managed_proxy_ha"], 1),
                "managed_share": round(share, 6) if share is not None else None,
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_status": manifest.get("status"),
        "status": "screening_proxy_not_legal_thlb",
        "rows": {"by_province": len(province_rows), "by_ecoprovince": len(ecoprovince_rows)},
        "by_province": by_province,
        "ecoprovinces": sorted({row["ecoprovince"] for row in ecoprovince_rows}),
        "units": {"area": "ha", "share": "fraction"},
        "caveats": [
            "500 m grid overlay; 2016 tenure vintage; screening proxy only.",
            "NOT a legal THLB or AAC landbase.",
        ],
    }


def _file_link(path: Path, root: Path, *, role: str) -> dict[str, Any]:
    if not path.is_file():
        raise ExportError(f"missing input artifact {path}")
    return {
        "role": role,
        "path": _relative(path, root),
        "bytes": path.stat().st_size,
        "rows": _rows_of(path),
        "sha256": _sha256(path),
    }


def build_osecan_links(root: Path, artifacts_root: Path) -> dict[str, Any]:
    family_links: list[dict[str, Any]] = []
    for spec in FAMILY_SPECS:
        family_root = artifacts_root / spec["dir"]
        cases = spec.get("cases")
        scenario_dirs = (
            [family_root / case["dir"] for case in cases]
            if cases
            else [family_root]
        )
        files = [
            _file_link(scenario_dir / name, root, role=name)
            for scenario_dir in scenario_dirs
            for name in ("run_summaries.json", "manifest.json", "dispatch_by_zone.csv")
        ]
        family_links.append(
            {
                "family": spec["family"],
                "source_dir": _relative(family_root, root),
                "scenario_dirs": [
                    _relative(scenario_dir, root) for scenario_dir in scenario_dirs
                ],
                "scenario_count": len(scenario_dirs),
                "post_fix_emissions": bool(spec["post_fix"]),
                "superseded_for_emissions": bool(spec["superseded"]),
                "superseded_for_fleet": not bool(spec.get("fleet_corrected")),
                "fleet_corrected": bool(spec.get("fleet_corrected")),
                "screening_only": bool(spec.get("screening_only")),
                "policy_grade": bool(spec.get("policy_grade")),
                "harvest_exogenous": bool(spec.get("harvest_exogenous")),
                "pareto_eligible": bool(spec.get("pareto_eligible")),
                "tradeoff_eligible": bool(spec.get("pareto_eligible")),
                "files": files,
            }
        )

    input_files = [
        _file_link(root / path, root, role=role)
        for role, path in INPUT_PATHS.items()
    ]
    clews_dir = root / "data/derived/national/clews"
    supbcw_paths = sorted(clews_dir.glob("SUPBCW*.csv")) + sorted(
        (clews_dir / "comparison").glob("SUPBCW*.csv")
    )
    supbcw_inputs = [
        _file_link(path, root, role=f"supbcw:{path.name}") for path in supbcw_paths
    ]

    recon_rows = _read_csv(root / INPUT_PATHS["cap_reconciliation"], label="cap reconciliation")
    tier_sum_ok = all(row.get("tier_sum_equals_rest") == "True" for row in recon_rows)
    national_ok = all(row.get("national_equals_200m_total") == "True" for row in recon_rows)
    if not (tier_sum_ok and national_ok):
        raise ExportError("national_cap_reconciliation checks failed")
    if len(recon_rows) != len(YEARS):
        raise ExportError("national_cap_reconciliation is not a 50-year series")

    return {
        "schema_version": SCHEMA_VERSION,
        "families": family_links,
        "input_files": input_files,
        "supbcw_inputs": supbcw_inputs,
        "validation": {
            "national_cap_reconciliation": {
                "path": INPUT_PATHS["cap_reconciliation"],
                "rows": len(recon_rows),
                "years": [int(row["year"]) for row in recon_rows],
                "all_tier_sum_equals_rest": tier_sum_ok,
                "all_national_equals_200m_total": national_ok,
                "max_abs_tier_residual_pj": max(
                    abs(float(row["tier_sum_pj"]) - float(row["rest_of_canada_pj"]))
                    for row in recon_rows
                ),
            }
        },
    }


def _round_geometry(value: Any, digits: int = 5) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, tuple):
        return [_round_geometry(item, digits) for item in value]
    if isinstance(value, list):
        return [_round_geometry(item, digits) for item in value]
    if isinstance(value, dict):
        return {key: _round_geometry(item, digits) for key, item in value.items()}
    return value


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def build_feature_collection(
    frame: Any,
    *,
    prop_map: dict[str, str],
    tolerance: float,
    name: str,
) -> dict[str, Any]:
    from shapely.geometry import mapping  # type: ignore[import-untyped]

    features: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            continue
        simplified = geometry.simplify(tolerance, preserve_topology=True)
        if simplified.is_empty:
            simplified = geometry
        properties = {
            target: _clean_scalar(row[source]) for source, target in prop_map.items()
        }
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": _round_geometry(mapping(simplified)),
            }
        )
    if not features:
        raise ExportError(f"spatial layer {name} produced no features")
    return {
        "type": "FeatureCollection",
        "name": name,
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }


def build_spatial(root: Path, out_dir: Path) -> dict[str, Any]:
    import geopandas as gpd  # type: ignore[import-untyped]

    provinces = gpd.read_file(root / INPUT_PATHS["provinces"])
    if provinces.crs is None or provinces.crs.to_epsg() != 4326:
        provinces = provinces.to_crs(4326)
    provinces_payload = build_feature_collection(
        provinces,
        prop_map={"shapeName": "name", "shapeISO": "iso"},
        tolerance=0.05,
        name="canada_adm1",
    )

    ecoprovinces = gpd.read_file(root / INPUT_PATHS["rsyc_ecostrat"], layer="ecoprovinces")
    ecoprovinces = ecoprovinces.to_crs(4326)
    ecoprovinces_payload = build_feature_collection(
        ecoprovinces,
        prop_map={
            "ecoprovince_id": "ecoprovince_id",
            "ecoprovince": "ecoprovince",
            "ecozone": "ecozone",
        },
        tolerance=0.02,
        name="rsyc_ecoprovinces",
    )

    tiles = gpd.read_file(root / INPUT_PATHS["rsyc_tiles"], layer="RSYC_tiles")
    tiles = tiles.to_crs(4326)
    supply_rows = _read_csv(root / INPUT_PATHS["tile_supply"], label="tile supply")
    supply_by_id = {row["tile_id"]: row for row in supply_rows}
    tile_ids = [str(value) for value in tiles["TileID"]]
    missing = sorted(set(tile_ids) - set(supply_by_id))
    if missing:
        raise ExportError(f"spatial join missing {len(missing)} tile supply rows: {missing[:5]}")
    tiles = tiles.copy()
    tiles["tile_id"] = tile_ids
    tiles["provinces"] = [supply_by_id[tile]["provinces"] for tile in tile_ids]
    tiles["area_ha"] = [float(supply_by_id[tile]["area_ha"]) for tile in tile_ids]
    tiles["volume_m3"] = [float(supply_by_id[tile]["volume_m3"]) for tile in tile_ids]
    tiles["recoverable_odt_base"] = [
        float(supply_by_id[tile]["recoverable_odt_base"]) for tile in tile_ids
    ]
    tiles["recoverable_pj_base"] = [
        float(supply_by_id[tile]["recoverable_pj_base"]) for tile in tile_ids
    ]
    tiles["over_capacity"] = [
        supply_by_id[tile]["over_capacity"].lower() == "true" for tile in tile_ids
    ]
    tiles_payload = build_feature_collection(
        tiles,
        prop_map={
            "tile_id": "tile_id",
            "provinces": "provinces",
            "area_ha": "area_ha",
            "volume_m3": "volume_m3",
            "recoverable_odt_base": "recoverable_odt_base",
            "recoverable_pj_base": "recoverable_pj_base",
            "over_capacity": "over_capacity",
        },
        tolerance=0.01,
        name="rsyc_tiles",
    )

    node_rows = _read_csv(root / INPUT_PATHS["bc_zone_nodes"], label="bc zone nodes")
    anchors = {row["zone_id"]: row for row in node_rows}
    zones = []
    for zone in C2070_ZONES:
        anchor = anchors.get(zone)
        if anchor is None:
            zones.append(
                {
                    "zone_id": zone,
                    "anchor": False,
                    "gap": True,
                    "gap_reason": "No authoritative C2070 zone anchor in repository; do not invent geometry.",
                }
            )
            continue
        zones.append(
            {
                "zone_id": zone,
                "anchor": True,
                "gap": False,
                "node_id": anchor["node_id"],
                "lon": round(float(anchor["lon"]), 6),
                "lat": round(float(anchor["lat"]), 6),
                "x_m": round(float(anchor["x_m"]), 3),
                "y_m": round(float(anchor["y_m"]), 3),
                "crs": "EPSG:4326",
                "weight_basis": anchor["weight_basis"],
            }
        )
    zone_payload = {
        "schema_version": SCHEMA_VERSION,
        "zone_count": len(zones),
        "anchor_count": sum(1 for zone in zones if zone["anchor"]),
        "gap_count": sum(1 for zone in zones if zone["gap"]),
        "zones": zones,
        "caveats": [
            "Only B1/B2 have authoritative node anchors in this repository.",
            "The other 11 C2070 zones carry explicit gaps; no polygons are invented.",
        ],
    }

    spatial_files = {
        "provinces.geojson": provinces_payload,
        "ecoprovinces.geojson": ecoprovinces_payload,
        "rsyc_tiles.geojson": tiles_payload,
        "zone_nodes.json": zone_payload,
    }
    for filename, payload in spatial_files.items():
        _write_json(out_dir / filename, payload)

    total_bytes = sum((out_dir / filename).stat().st_size for filename in spatial_files)
    if total_bytes >= 5 * 1024 * 1024:
        raise ExportError(f"spatial bundle is {total_bytes} bytes, limit is 5 MiB")

    return {
        "files": list(spatial_files),
        "total_bytes": total_bytes,
        "province_count": len(provinces_payload["features"]),
        "ecoprovince_count": len(ecoprovinces_payload["features"]),
        "tile_count": len(tiles_payload["features"]),
        "tile_missing_supply": len(missing),
        "zone_count": zone_payload["zone_count"],
        "anchor_count": zone_payload["anchor_count"],
        "gap_count": zone_payload["gap_count"],
    }


def _record(checks: list[dict[str, Any]], name: str, ok: bool, detail: Any) -> None:
    checks.append({"name": name, "ok": bool(ok), "detail": detail})
    if not ok:
        raise ExportError(f"validation failed: {name}: {detail}")


def build_bundle(root: Path, out_dir: Path) -> dict[str, Any]:
    artifacts_root = root / "data/derived/national/clews/osecan"
    if not artifacts_root.is_dir():
        raise ExportError(f"missing artifacts root {artifacts_root}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outputs").mkdir(exist_ok=True)
    (out_dir / "inputs").mkdir(exist_ok=True)
    (out_dir / "spatial").mkdir(exist_ok=True)
    _clear_generated_files(out_dir)

    checks: list[dict[str, Any]] = []
    runs = discover_runs(artifacts_root)
    _record(checks, "scenario_count", len(runs) > 0, len(runs))

    scenarios: list[dict[str, Any]] = []
    output_checks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for run in runs:
        spec = next(item for item in FAMILY_SPECS if item["family"] == run.family)
        spec = dict(spec)
        spec["source_dir"] = str(run.source_dir)
        spec["repo_root"] = str(root)
        summaries = _read_json(
            run.source_dir / "run_summaries.json", label=run.family
        )
        scenario = build_scenario(spec, summaries, run.case_id, run.entry)
        key = scenario["scenario_id"]
        if key in seen_ids:
            raise ExportError(f"duplicate scenario_id {key}")
        seen_ids.add(key)
        scenarios.append(scenario)
        _record(
            checks,
            f"metrics:{key}",
            all(
                scenario["metrics"][field] is not None
                for field in ("objective", "emissions_total", "emissions_2050")
            ),
            scenario["metrics"],
        )

        dispatch_path = run.source_dir / "dispatch_by_zone.csv"
        rows = load_dispatch_rows(
            dispatch_path, run_id=scenario["dispatch_run"], scenario_key=key
        )
        output, row_checks = build_output(
            key,
            rows,
            source_path=_relative(dispatch_path, root),
        )
        _write_json(out_dir / "outputs" / f"{key}.json", output)
        output_checks.extend(row_checks)
        if scenario["metrics"]["bio_share_2050"] is None:
            derived_share = derive_bio_share_2050(output)
            scenario["metrics"]["bio_share_2050"] = derived_share
            scenario["metrics"]["bio_share_basis"] = (
                "derived_from_dispatch_2050" if derived_share is not None else None
            )
        if scenario["metrics"]["pwrbcw_2050"] is None:
            scenario["metrics"]["pwrbcw_2050"] = derive_pwrbcw_2050(output)
        _record(
            checks,
            f"dispatch_years:{key}",
            len(set(output["year"])) == len(YEARS),
            len(set(output["year"])),
        )

    _record(checks, "unique_scenario_ids", len(seen_ids) == len(scenarios), len(seen_ids))
    _record(checks, "dispatch_outputs", len(output_checks) == len(scenarios), len(output_checks))

    superseded_in_tradeoff = [
        scenario["scenario_id"]
        for scenario in scenarios
        if scenario["flags"]["superseded_for_emissions"] and scenario["flags"]["publishable"]
    ]
    _record(
        checks,
        "no_superseded_publishable",
        not superseded_in_tradeoff,
        superseded_in_tradeoff,
    )

    canonical_ids = [
        scenario["scenario_id"]
        for scenario in scenarios
        if scenario["flags"]["canonical"]
    ]
    _record(checks, "single_canonical_scenario", len(canonical_ids) == 1, canonical_ids)

    pareto_scenarios = [
        scenario for scenario in scenarios if scenario["pareto_eligible"]
    ]
    sensitivity_scenarios = [
        scenario
        for scenario in scenarios
        if scenario["flags"]["publishable"]
        and scenario["family"] == "fleet_screening_ladder"
        and not scenario["pareto_eligible"]
    ]
    allocation_scenarios = [
        scenario
        for scenario in scenarios
        if scenario["family"] == "allocation_screening"
    ]
    tradeoff = {
        "rule": (
            "publishable fleet-corrected scenario with pareto_eligible=true and "
            "feasibility_status='pass'; the exogenous harvest ladder and the "
            "allocation-LP family are sensitivity/compare only and are never "
            "Pareto eligible"
        ),
        "canonical_scenario_id": canonical_ids[0],
        "canonical_role": "canonical/base",
        "harvest_exogenous_caveat": HARVEST_EXOGENOUS_CAVEAT,
        "allocation_caveat": ALLOCATION_THLB_CAVEAT,
        "feasibility_caveat": (
            "200 Mm3 is not admissible under documented AAC/NFD; 150 Mm3 is "
            "conditional (national bound only; the LP reallocates it but the "
            "1.0 m3/ha/yr THLB-proxy screen fails by 6.113 Mm3); harvest is "
            "exogenous and free. The default energy Pareto contains only the "
            "canonical 119 Mm3 fleet scenario. Allocation-LP variants are "
            "comparison/context only."
        ),
        "scenario_ids": [scenario["scenario_id"] for scenario in pareto_scenarios],
        "sensitivity_scenario_ids": [
            scenario["scenario_id"] for scenario in sensitivity_scenarios
        ],
        "allocation_scenario_ids": [
            scenario["scenario_id"] for scenario in allocation_scenarios
        ],
        "metrics": [
            {
                "scenario_id": scenario["scenario_id"],
                "family": scenario["family"],
                "harvest_exogenous": scenario["flags"]["harvest_exogenous"],
                "canonical": scenario["flags"]["canonical"],
                "feasibility_status": scenario["feasibility_status"],
                "pareto_eligible": scenario["pareto_eligible"],
                **scenario["metrics"],
            }
            for scenario in pareto_scenarios
        ],
    }
    _record(
        checks,
        "tradeoff_eligibility_count",
        len(tradeoff["scenario_ids"]) == 1,
        tradeoff["scenario_ids"],
    )
    _record(
        checks,
        "pareto_only_canonical",
        [scenario["scenario_id"] for scenario in pareto_scenarios] == canonical_ids,
        [scenario["scenario_id"] for scenario in pareto_scenarios],
    )
    _record(
        checks,
        "ladder_not_pareto",
        all(not scenario["pareto_eligible"] for scenario in sensitivity_scenarios)
        and all(
            scenario["flags"]["harvest_exogenous"] for scenario in sensitivity_scenarios
        ),
        [scenario["scenario_id"] for scenario in sensitivity_scenarios],
    )
    _record(
        checks,
        "ladder_feasibility_labels",
        {
            scenario["dimensions"]["harvest_m3_per_year"]: scenario["feasibility_status"]
            for scenario in sensitivity_scenarios
        }
        == {
            100_000_000: "pass",
            150_000_000: "conditional",
            200_000_000: "fail",
        },
        {
            scenario["dimensions"]["harvest_m3_per_year"]: scenario["feasibility_status"]
            for scenario in sensitivity_scenarios
        },
    )
    _record(
        checks,
        "allocation_family_count",
        len(allocation_scenarios) == 2,
        [scenario["scenario_id"] for scenario in allocation_scenarios],
    )
    _record(
        checks,
        "allocation_not_pareto",
        all(
            not scenario["pareto_eligible"]
            and not scenario["flags"]["tradeoff_eligible"]
            and scenario["flags"]["allocation_screening"]
            for scenario in allocation_scenarios
        ),
        [scenario["scenario_id"] for scenario in allocation_scenarios],
    )
    allocation_comparison = build_allocation_comparison(root, scenarios)
    deficit_150_1_0 = next(
        pair for pair in allocation_comparison["pairs"] if pair["harvest_m3"] == 150_000_000.0
    )["intensity"]["1.0"]["deficit_m3"]
    _record(
        checks,
        "allocation_150m_intensity_deficit",
        abs(deficit_150_1_0 - 6_113_051.149) < 100.0,
        deficit_150_1_0,
    )
    superseded_ids = {
        scenario["scenario_id"]
        for scenario in scenarios
        if scenario["flags"]["superseded_for_emissions"]
    }
    _record(
        checks,
        "tradeoff_excludes_superseded",
        superseded_ids.isdisjoint(tradeoff["scenario_ids"]),
        sorted(superseded_ids & set(tradeoff["scenario_ids"])),
    )
    fleet_superseded_ids = {
        scenario["scenario_id"]
        for scenario in scenarios
        if scenario["flags"]["superseded_for_fleet"]
    }
    _record(
        checks,
        "tradeoff_excludes_pre_fleet",
        fleet_superseded_ids.isdisjoint(tradeoff["scenario_ids"]),
        sorted(fleet_superseded_ids & set(tradeoff["scenario_ids"])),
    )

    scenarios_payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": utc_now(),
        "source_root": _relative(artifacts_root, root),
        "units": {
            "objective": "MUSD",
            "emissions": "Mt CO2",
            "energy": "PJ",
            "energy_electricity": "PJ electricity",
            "energy_fuel": "PJ fuel",
            "share": "fraction",
        },
        "families": [
            {
                "family": spec["family"],
                "label": spec["label"],
                "source_dir": _relative(artifacts_root / spec["dir"], root),
                "post_fix_emissions": bool(spec["post_fix"]),
                "superseded_for_emissions": bool(spec["superseded"]),
                "superseded_for_fleet": not bool(spec.get("fleet_corrected")),
                "fleet_corrected": bool(spec.get("fleet_corrected")),
                "screening_only": bool(spec.get("screening_only")),
                "policy_grade": bool(spec.get("policy_grade")),
                "canonical": bool(spec.get("canonical")),
                "harvest_exogenous": bool(spec.get("harvest_exogenous")),
                "pareto_eligible": bool(spec.get("pareto_eligible")),
                "tradeoff_eligible": bool(spec.get("pareto_eligible")),
                "allocation_screening": bool(spec.get("allocation_screening")),
                "scenario_count": sum(1 for run in runs if run.family == spec["family"]),
            }
            for spec in FAMILY_SPECS
        ],
        "scenarios": scenarios,
        "tradeoff": tradeoff,
        "allocation_screening": allocation_comparison,
    }
    _write_json(out_dir / "scenarios.json", scenarios_payload)

    inputs = {
        "harvest_basis.json": build_harvest_basis(root),
        "supply_curve.json": build_supply_curve(root),
        "managed_landbase.json": build_managed_landbase(root),
        "osecan_links.json": build_osecan_links(root, artifacts_root),
    }
    for filename, payload in inputs.items():
        _write_json(out_dir / "inputs" / filename, payload)

    spatial = build_spatial(root, out_dir / "spatial")
    _record(checks, "spatial_total_bytes", spatial["total_bytes"] < 5 * 1024 * 1024, spatial["total_bytes"])
    _record(checks, "spatial_tile_join", spatial["tile_missing_supply"] == 0, spatial["tile_missing_supply"])
    _record(checks, "spatial_tiles", spatial["tile_count"] == 385, spatial["tile_count"])
    _record(checks, "spatial_zone_gaps", spatial["gap_count"] == 11, spatial["gap_count"])

    manifest_files: list[dict[str, Any]] = []
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        manifest_files.append(
            {
                "path": path.relative_to(out_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    total_bytes = sum(item["bytes"] for item in manifest_files)
    _record(checks, "manifest_files", len(manifest_files) > 0, len(manifest_files))
    _record(
        checks,
        "manifest_sha256",
        all(len(item["sha256"]) == 64 for item in manifest_files),
        len(manifest_files),
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": utc_now(),
        "scenario_count": len(scenarios),
        "output_count": len(scenarios),
        "files": manifest_files,
        "total_bytes": total_bytes,
        "validation": {
            "ok": all(check["ok"] for check in checks),
            "checks": checks,
        },
        "spatial": spatial,
    }
    _write_json(out_dir / "manifest.json", manifest)
    return manifest


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=repo_root, help="Repository root")
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "ui/public/data",
        help="Static bundle output directory",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    manifest = build_bundle(args.root.resolve(), args.out.resolve())
    print(
        json.dumps(
            {
                "scenarios": manifest["scenario_count"],
                "files": len(manifest["files"]),
                "total_bytes": manifest["total_bytes"],
                "spatial_bytes": manifest["spatial"]["total_bytes"],
                "validation_ok": manifest["validation"]["ok"],
                "out": str(args.out),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
