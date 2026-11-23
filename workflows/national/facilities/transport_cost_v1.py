"""National truck transport cost component built from open StatCan data.

The component is a transparent bottom-up model of one loaded truck trip with an
empty return.  It produces CAD per m3, CAD per oven-dry tonne (ODT), CAD per
ODT-km and CAD per GJ for 50/100/150/300/500 km one-way road distances and
45/52.5/60 m3 payloads.

Only open or public inputs are used:

* Statistics Canada Open Licence aggregate tables for diesel, wages, CPI,
  machinery/equipment price index, and the for-hire trucking freight index.
  The observed values are read from the existing strict-open BC cost artifact
  rather than re-keyed.
* Public truck weight/axle limits for the mass-limit screen.
* Project-derived road-distance factors, facility assignment context, residue
  LHV, and the adopted OSeCAN supply-curve tiers for comparison.

The result is ``model_estimate`` / ``observed=false`` / ``policy_grade=false``.
It does not change any assignment, makes no national or policy claim, and does
not touch any UI or official OSeCAN surface.  A ``D`` in either git status
column halts the build before any output is written.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/transport_cost_v1"

STRICT_OBS_CSV = (
    DATA_ROOT
    / "derived/national/harvest_basis/bc_open_cost_model_strict"
    / "strict_statcan_observations.csv"
)
STRICT_PARAMS_CSV = (
    DATA_ROOT
    / "derived/national/harvest_basis/bc_open_cost_model_strict"
    / "strict_parameters.csv"
)
ASSIGNMENT_V6_CSV = (
    DATA_ROOT
    / "derived/national/facilities/national_facility_assignment_v6"
    / "facility_assignment.csv"
)
ROAD_FACTORS_CSV = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v1"
    / "province_road_distance_summary.csv"
)
RESIDUE_SUMMARY_JSON = (
    DATA_ROOT
    / "derived/national/facilities/facility_residue_supply_v7"
    / "summary.json"
)
SUPPLY_CURVE_JSON = (
    DATA_ROOT
    / "derived/national/clews/osecan/national_supply_curve"
    / "supply_curve_definition.json"
)

ARTIFACT = "transport_cost_v1"
ARTIFACT_VERSION = "1.0.0"
DISTANCES_KM = (50.0, 100.0, 150.0, 300.0, 500.0)
PAYLOADS_M3 = (45.0, 52.5, 60.0)
COST_SCENARIOS = ("low", "base", "high")
BASE_PAYLOAD_M3 = 52.5

EVIDENCE_LABEL = "model_estimate"
OBSERVED = False
POLICY_GRADE = False
ASSIGNMENT_CHANGE = False
NATIONAL_CLAIM = False

# Public weight/axle reference used only for a binding screen.  The selected
# values are the commonly published Canadian 8-axle B-train envelope: 63,500 kg
# gross vehicle weight and 17,000 kg tridem axle group.  The model never treats
# the mass cap as binding for the modeled residue bulk-density range; it proves
# that it is not binding.
LEGAL_GVW_KG = 63_500.0
TRUCK_TARE_KG = 18_000.0
LEGAL_PAYLOAD_KG = LEGAL_GVW_KG - TRUCK_TARE_KG
AXLE_LIMIT_KG = 17_000.0

PARAMETER_FIELDS = [
    "parameter_id",
    "component",
    "unit",
    "low",
    "base",
    "high",
    "direction",
    "scenario_low_value",
    "scenario_base_value",
    "scenario_high_value",
    "source_id",
    "source_name",
    "licence",
    "evidence",
    "observed",
    "policy_grade",
    "notes",
]
CURVE_FIELDS = [
    "distance_km_one_way",
    "cost_scenario",
    "payload_m3",
    "payload_odt",
    "payload_wet_tonnes",
    "energy_gj",
    "round_trip_km",
    "driving_hours",
    "on_duty_hours",
    "fuel_l",
    "fuel_cost_cad",
    "driver_cost_cad",
    "maintenance_cost_cad",
    "tire_cost_cad",
    "capital_cost_cad",
    "insurance_cost_cad",
    "overhead_cost_cad",
    "total_cost_cad",
    "cost_per_m3_cad",
    "cost_per_odt_cad",
    "cost_per_odt_km_cad",
    "cost_per_gj_cad",
    "fuel_per_m3_cad",
    "driver_per_m3_cad",
    "maintenance_per_m3_cad",
    "tire_per_m3_cad",
    "capital_per_m3_cad",
    "insurance_per_m3_cad",
    "overhead_per_m3_cad",
    "loaded_km_share",
    "mass_utilization",
    "volume_utilization",
    "payload_wet_kg",
    "legal_payload_kg",
    "mass_limit_binding",
    "long_haul_flag",
    "evidence_label",
    "observed",
    "policy_grade",
    "assignment_change",
    "national_claim",
]
COMPARISON_FIELDS = [
    "distance_km_one_way",
    "cost_scenario",
    "payload_m3",
    "transport_cost_cad_per_gj",
    "tier",
    "technology",
    "tier_cost_per_gj",
    "tier_cost_basis",
    "tier_scenario_flag",
    "transport_exceeds_tier",
    "ratio_transport_to_tier",
    "ratio_minus_one",
    "currency_basis",
    "evidence_label",
    "observed",
    "policy_grade",
]
CROSSING_FIELDS = [
    "cost_scenario",
    "payload_m3",
    "tier",
    "technology",
    "tier_cost_per_gj",
    "crossing_distance_km",
    "within_modelled_range",
    "transport_exceeds_at_50km",
    "transport_exceeds_at_100km",
    "transport_exceeds_at_150km",
    "transport_exceeds_at_300km",
    "transport_exceeds_at_500km",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVENANCE_FIELDS = [
    "source_id",
    "role",
    "name",
    "publisher",
    "year",
    "licence",
    "licence_url",
    "url",
    "path",
    "sha256",
    "retrieved_utc",
    "used_for",
    "notes",
]


class TransportCostV1Error(RuntimeError):
    """Raised when a transport-cost v1 invariant fails."""


@dataclass(frozen=True)
class Parameter:
    """A bounded model parameter with a cost-direction rule."""

    parameter_id: str
    component: str
    unit: str
    low: float
    base: float
    high: float
    direction: str
    source_id: str
    notes: str

    def value_for(self, scenario: str) -> float:
        if scenario == "base" or self.direction == "fixed":
            return self.base
        if scenario == "low":
            return self.low if self.direction == "lower_is_better" else self.high
        if scenario == "high":
            return self.high if self.direction == "lower_is_better" else self.low
        raise TransportCostV1Error(
            f"unknown cost scenario {scenario!r}; expected one of {COST_SCENARIOS}"
        )


# ---------------------------------------------------------------------------
# I/O helpers.
# ---------------------------------------------------------------------------

def _format_float(value: float | None, digits: int = 9) -> str:
    if value is None:
        return ""
    if not math.isfinite(value):
        raise TransportCostV1Error(f"refusing to write non-finite value {value!r}")
    return f"{value:.{digits}f}"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise TransportCostV1Error(f"required input missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise TransportCostV1Error(f"required input has no rows: {path}")
    return rows


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]
) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def parse_deletion_lines(status_text: str) -> list[str]:
    """Return porcelain lines with a ``D`` in either status column."""

    return [
        line
        for line in status_text.splitlines()
        if len(line) >= 2 and (line[0] == "D" or line[1] == "D")
    ]


def _git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise TransportCostV1Error(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    return result.stdout


def assert_no_unexpected_deletions(
    repositories: Sequence[Path] = (REPO_ROOT, DATA_ROOT),
    *,
    status_reader: Callable[[Path], str] = _git_status,
) -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""

    incidents: list[str] = []
    for repository in repositories:
        for line in parse_deletion_lines(status_reader(repository)):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise TransportCostV1Error(
            "unexpected deletion status detected; halting: " + " | ".join(incidents)
        )


# ---------------------------------------------------------------------------
# Open observations.
# ---------------------------------------------------------------------------

def _series_rows(rows: Sequence[dict[str, str]], series_id: str) -> list[dict[str, str]]:
    selected = [row for row in rows if row.get("series_id") == series_id]
    if not selected:
        raise TransportCostV1Error(f"no observation rows for series {series_id!r}")
    return selected


def _period_value(
    rows: Sequence[dict[str, str]], series_id: str, period: str
) -> float:
    selected = [
        row
        for row in _series_rows(rows, series_id)
        if row.get("reference_period") == period
    ]
    if len(selected) != 1:
        raise TransportCostV1Error(
            f"expected one row for {series_id!r} at {period!r}; got {len(selected)}"
        )
    return _positive_float(selected[0].get("value"), f"{series_id}@{period}")


def _single_series_value(rows: Sequence[dict[str, str]], series_id: str) -> float:
    selected = _series_rows(rows, series_id)
    if len(selected) != 1:
        raise TransportCostV1Error(
            f"expected one row for {series_id!r}; got {len(selected)}"
        )
    return _positive_float(selected[0].get("value"), series_id)


def _positive_float(raw: Any, label: str) -> float:
    value = _finite_float(raw, label)
    if value <= 0.0:
        raise TransportCostV1Error(f"{label}: expected positive value; got {value}")
    return value


def _finite_float(raw: Any, label: str) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise TransportCostV1Error(f"{label}: not float-coercible: {raw!r}") from exc
    if not math.isfinite(value):
        raise TransportCostV1Error(f"{label}: expected finite value; got {value}")
    return value


def load_open_observations() -> dict[str, float]:
    """Read the committed StatCan observation extract and derive 2026 inputs."""

    rows = _read_csv(STRICT_OBS_CSV)
    diesel = [
        _positive_float(row.get("value"), "diesel")
        / 100.0
        for row in _series_rows(rows, "diesel_vancouver_self_service")
    ]
    if len(diesel) < 12:
        raise TransportCostV1Error(
            f"expected at least 12 monthly diesel observations; got {len(diesel)}"
        )
    cpi_factor = _period_value(rows, "cpi_bc_all_items", "2026-07-01") / _period_value(
        rows, "cpi_bc_all_items", "2025-07-01"
    )
    wage_transport = _single_series_value(
        rows, "wage_bc_trades_transport_operators"
    )
    wage_resource = _single_series_value(
        rows, "wage_bc_natural_resources_production"
    )
    wage_forestry = _single_series_value(rows, "wage_bc_forestry_group")
    ppi_2025 = _period_value(
        rows, "truck_transportation_ppi", "2025-06-01"
    )
    ppi_2026 = _period_value(
        rows, "truck_transportation_ppi", "2026-06-01"
    )
    wage_low = wage_resource * cpi_factor
    wage_base = wage_transport * cpi_factor
    wage_high = wage_forestry * cpi_factor
    if not (wage_low <= wage_base <= wage_high):
        raise TransportCostV1Error(
            "wage anchors are not ordered low <= base <= high: "
            f"{wage_low}, {wage_base}, {wage_high}"
        )
    return {
        "diesel_low": min(diesel),
        "diesel_base": sum(diesel) / len(diesel),
        "diesel_high": max(diesel),
        "diesel_months": float(len(diesel)),
        "cpi_factor": cpi_factor,
        "wage_low": wage_low,
        "wage_base": wage_base,
        "wage_high": wage_high,
        "ppi_2025": ppi_2025,
        "ppi_2026": ppi_2026,
        "ppi_growth": ppi_2026 / ppi_2025 - 1.0,
    }


def load_capital_bounds() -> dict[str, float]:
    """Read the already-escalated tractor + trailer capital bounds."""

    rows = _read_csv(STRICT_PARAMS_CSV)
    by_id = {row["parameter_id"]: row for row in rows}
    result: dict[str, float] = {}
    for bound in ("low", "base", "high"):
        total = 0.0
        for parameter_id in ("capital_truck_tractor", "capital_trailer"):
            if parameter_id not in by_id:
                raise TransportCostV1Error(
                    f"strict parameters missing {parameter_id!r}"
                )
            total += _positive_float(by_id[parameter_id].get(bound), parameter_id)
        result[f"capital_{bound}"] = total
    return result


def load_residue_lhv() -> float:
    if not RESIDUE_SUMMARY_JSON.is_file():
        raise TransportCostV1Error(
            f"required residue summary missing: {RESIDUE_SUMMARY_JSON}"
        )
    payload = json.loads(RESIDUE_SUMMARY_JSON.read_text(encoding="utf-8"))
    lhv = payload.get("lhv", {}).get("value_gj_per_odt")
    return _positive_float(lhv, "residue_lhv_gj_per_odt")


def load_supply_tiers() -> list[dict[str, Any]]:
    if not SUPPLY_CURVE_JSON.is_file():
        raise TransportCostV1Error(
            f"required supply-curve definition missing: {SUPPLY_CURVE_JSON}"
        )
    payload = json.loads(SUPPLY_CURVE_JSON.read_text(encoding="utf-8"))
    tiers = payload.get("tiers")
    if not isinstance(tiers, list) or len(tiers) != 4:
        raise TransportCostV1Error(
            f"expected four supply-curve tiers; got {type(tiers).__name__}"
        )
    parsed: list[dict[str, Any]] = []
    for tier in tiers:
        cost = _positive_float(
            tier.get("variable_cost_usd_per_gj"), f"tier {tier.get('tier')} cost"
        )
        parsed.append(
            {
                "tier": str(tier["tier"]),
                "technology": str(tier["technology"]),
                "cost_per_gj": cost,
                "lower_pj": float(tier["lower_pj"]),
                "upper_pj": float(tier["upper_pj"]),
                "incremental_cap_pj": float(tier["incremental_cap_pj"]),
                "scenario_flag": str(tier.get("scenario_flag", "")),
                "cost_basis": str(tier.get("cost_basis", "adopted_tier")),
            }
        )
    return parsed


# ---------------------------------------------------------------------------
# Parameter register.
# ---------------------------------------------------------------------------

def build_parameters(observations: dict[str, float], capital: dict[str, float]) -> list[Parameter]:
    """Return the bounded parameter register in display order."""

    lhv = load_residue_lhv()
    return [
        Parameter(
            "diesel_cad_per_l", "fuel", "CAD/L",
            observations["diesel_low"], observations["diesel_base"],
            observations["diesel_high"], "lower_is_better",
            "statcan_18100001_diesel",
            "Vancouver self-service retail diesel, 12-month min/mean/max; pump price includes taxes.",
        ),
        Parameter(
            "driver_wage_cad_per_h", "driver", "CAD/h",
            observations["wage_low"], observations["wage_base"],
            observations["wage_high"], "lower_is_better",
            "statcan_14100417_wages_occupation",
            "StatCan BC transport operators (base), natural-resource production (low), forestry grouping (high), CPI to 2026.",
        ),
        Parameter(
            "payroll_burden_fraction", "driver", "fraction of wage",
            0.20, 0.25, 0.30, "lower_is_better", "model_assumptions_cc0",
            "Benefits, payroll taxes and workers' compensation; explicit assumption.",
        ),
        Parameter(
            "payload_m3", "payload", "m3/trip",
            45.0, BASE_PAYLOAD_M3, 60.0, "higher_is_better", "model_assumptions_cc0",
            "Loaded residue volume per trip; the task-specified 45-60 m3 envelope.",
        ),
        Parameter(
            "residue_bulk_density_odt_per_m3", "payload", "ODT/m3",
            0.15, 0.22, 0.30, "higher_is_better", "model_assumptions_cc0",
            "As-loaded residue bulk density; width reflects loose hog/fuel residue, not solid wood.",
        ),
        Parameter(
            "residue_lhv_gj_per_odt", "payload", "GJ/ODT",
            lhv * 0.89, lhv, lhv * 1.11, "higher_is_better",
            "facility_residue_supply_v7",
            "Dry lower heating value from the national residue chain; range is a +/-11% conversion sensitivity.",
        ),
        Parameter(
            "moisture_content_wet_basis", "payload", "fraction",
            0.40, 0.45, 0.50, "fixed", "model_assumptions_cc0",
            "Used only for the wet-mass axle/GVW screen, not for the cost arithmetic.",
        ),
        Parameter(
            "fuel_loaded_l_per_km", "fuel", "L/km",
            0.34, 0.40, 0.48, "lower_is_better", "model_assumptions_cc0",
            "Loaded class-8 tractor-trailer fuel burn; engineering assumption.",
        ),
        Parameter(
            "fuel_empty_l_per_km", "fuel", "L/km",
            0.27, 0.32, 0.38, "lower_is_better", "model_assumptions_cc0",
            "Empty return fuel burn; engineering assumption.",
        ),
        Parameter(
            "speed_loaded_kmh", "driver", "km/h",
            45.0, 55.0, 65.0, "higher_is_better", "model_assumptions_cc0",
            "Loaded road speed including normal stops; engineering assumption.",
        ),
        Parameter(
            "speed_empty_kmh", "driver", "km/h",
            55.0, 65.0, 70.0, "higher_is_better", "model_assumptions_cc0",
            "Empty return road speed; engineering assumption.",
        ),
        Parameter(
            "terminal_hours_per_trip", "driver", "h/trip",
            1.0, 1.5, 2.0, "lower_is_better", "model_assumptions_cc0",
            "Load, unload and queue/delay time paid per trip.",
        ),
        Parameter(
            "maintenance_cad_per_km", "maintenance", "CAD/km",
            0.08, 0.12, 0.16, "lower_is_better", "model_assumptions_cc0",
            "Repairs and maintenance excluding tires.",
        ),
        Parameter(
            "tire_cad_per_km", "tire", "CAD/km",
            0.04, 0.06, 0.08, "lower_is_better", "model_assumptions_cc0",
            "Tire wear and replacement.",
        ),
        Parameter(
            "truck_capital_cad", "capital", "CAD",
            capital["capital_low"], capital["capital_base"], capital["capital_high"],
            "lower_is_better", "bc_open_cost_model_strict",
            "Tractor plus trailer replacement value, 2026 CAD via StatCan MEPI; 2017 base price is an assumption.",
        ),
        Parameter(
            "economic_life_km", "capital", "km",
            1_000_000.0, 1_200_000.0, 1_400_000.0, "higher_is_better",
            "model_assumptions_cc0",
            "Distance-based economic life; engineering assumption.",
        ),
        Parameter(
            "residual_value_fraction", "capital", "fraction of capital",
            0.10, 0.15, 0.20, "higher_is_better", "model_assumptions_cc0",
            "Resale value at end of life; engineering assumption.",
        ),
        Parameter(
            "interest_rate", "capital", "fraction/year",
            0.05, 0.065, 0.08, "lower_is_better", "model_assumptions_cc0",
            "Nominal financing rate on average annual investment.",
        ),
        Parameter(
            "annual_km", "capital", "km/year",
            100_000.0, 100_000.0, 100_000.0, "fixed", "model_assumptions_cc0",
            "Annual distance used to allocate fixed ownership costs.",
        ),
        Parameter(
            "utilization_fraction", "capital", "fraction",
            0.80, 0.85, 0.90, "higher_is_better", "model_assumptions_cc0",
            "Productive share of annual distance; fixed costs are divided by annual_km x utilization.",
        ),
        Parameter(
            "insurance_rate_of_capital_per_year", "insurance", "fraction of capital/year",
            0.025, 0.035, 0.045, "lower_is_better", "model_assumptions_cc0",
            "Insurance, registration and plate cost as a share of capital.",
        ),
        Parameter(
            "overhead_fraction", "overhead", "fraction of subtotal",
            0.08, 0.12, 0.18, "lower_is_better", "model_assumptions_cc0",
            "Administration, dispatch, compliance and margin; excludes fuel taxes already in pump price.",
        ),
        Parameter(
            "backhaul_fraction", "payload", "fraction of return km loaded",
            0.0, 0.0, 0.0, "fixed", "model_assumptions_cc0",
            "No backhaul credit; every return kilometre is empty by design.",
        ),
        Parameter(
            "legal_gvw_kg", "mass_limit", "kg",
            LEGAL_GVW_KG, LEGAL_GVW_KG, LEGAL_GVW_KG, "fixed",
            "ccmta_weights_dimensions",
            "Public 8-axle B-train gross vehicle weight envelope; screening reference, not a permit.",
        ),
        Parameter(
            "truck_tare_kg", "mass_limit", "kg",
            TRUCK_TARE_KG, TRUCK_TARE_KG, TRUCK_TARE_KG, "fixed",
            "model_assumptions_cc0",
            "Tractor, trailer and fuel tare; engineering assumption.",
        ),
        Parameter(
            "legal_payload_kg", "mass_limit", "kg",
            LEGAL_PAYLOAD_KG, LEGAL_PAYLOAD_KG, LEGAL_PAYLOAD_KG, "fixed",
            "model_derived",
            "legal_gvw_kg minus truck_tare_kg.",
        ),
        Parameter(
            "axle_limit_kg", "mass_limit", "kg",
            AXLE_LIMIT_KG, AXLE_LIMIT_KG, AXLE_LIMIT_KG, "fixed",
            "ccmta_weights_dimensions",
            "Public tridem axle-group envelope; reported for context, not applied axle-by-axle.",
        ),
        Parameter(
            "cpi_factor_2025_to_2026", "escalation", "factor",
            observations["cpi_factor"], observations["cpi_factor"],
            observations["cpi_factor"], "fixed", "statcan_18100004_cpi",
            "BC all-items CPI, July 2026 / July 2025; used to express 2025 wage anchors in 2026 CAD.",
        ),
        Parameter(
            "freight_ppi_growth_2025_to_2026", "cross_check", "fraction",
            observations["ppi_growth"], observations["ppi_growth"],
            observations["ppi_growth"], "fixed", "statcan_18100281_trucking_ppi",
            "For-hire trucking freight PPI growth June 2025 to June 2026; plausibility check only.",
        ),
    ]


def _parameter_map(parameters: Sequence[Parameter]) -> dict[str, Parameter]:
    return {parameter.parameter_id: parameter for parameter in parameters}


# ---------------------------------------------------------------------------
# Cost arithmetic.
# ---------------------------------------------------------------------------

def trip_cost(
    distance_km_one_way: float,
    payload_m3: float,
    parameters: dict[str, Parameter],
    scenario: str,
) -> dict[str, float]:
    """Return one loaded trip with an empty return, in 2026 CAD.

    The function is pure: parameters are read, never mutated.  All distances
    are one-way road kilometres; the return leg is the same distance empty.
    """

    if not math.isfinite(distance_km_one_way) or distance_km_one_way < 0.0:
        raise TransportCostV1Error(
            f"distance_km_one_way must be non-negative; got {distance_km_one_way!r}"
        )
    if not math.isfinite(payload_m3) or payload_m3 <= 0.0:
        raise TransportCostV1Error(f"payload_m3 must be positive; got {payload_m3!r}")

    def value(parameter_id: str) -> float:
        try:
            return parameters[parameter_id].value_for(scenario)
        except KeyError as exc:
            raise TransportCostV1Error(
                f"missing parameter {parameter_id!r}"
            ) from exc

    diesel = value("diesel_cad_per_l")
    wage = value("driver_wage_cad_per_h")
    burden = value("payroll_burden_fraction")
    density = value("residue_bulk_density_odt_per_m3")
    lhv = value("residue_lhv_gj_per_odt")
    moisture = value("moisture_content_wet_basis")
    fuel_loaded = value("fuel_loaded_l_per_km")
    fuel_empty = value("fuel_empty_l_per_km")
    speed_loaded = value("speed_loaded_kmh")
    speed_empty = value("speed_empty_kmh")
    terminal_hours = value("terminal_hours_per_trip")
    maintenance_per_km = value("maintenance_cad_per_km")
    tire_per_km = value("tire_cad_per_km")
    capital = value("truck_capital_cad")
    life_km = value("economic_life_km")
    residual = value("residual_value_fraction")
    interest = value("interest_rate")
    annual_km = value("annual_km")
    utilization = value("utilization_fraction")
    insurance_rate = value("insurance_rate_of_capital_per_year")
    overhead = value("overhead_fraction")

    round_trip_km = 2.0 * distance_km_one_way
    driving_hours = (
        distance_km_one_way / speed_loaded + distance_km_one_way / speed_empty
    )
    on_duty_hours = driving_hours + terminal_hours
    fuel_l = distance_km_one_way * fuel_loaded + distance_km_one_way * fuel_empty
    fuel_cost = fuel_l * diesel
    driver_cost = wage * (1.0 + burden) * on_duty_hours
    maintenance_cost = maintenance_per_km * round_trip_km
    tire_cost = tire_per_km * round_trip_km
    depreciation_per_km = capital * (1.0 - residual) / life_km
    average_investment = capital * (1.0 + residual) / 2.0
    productive_annual_km = annual_km * utilization
    interest_per_km = average_investment * interest / productive_annual_km
    capital_cost = (depreciation_per_km + interest_per_km) * round_trip_km
    insurance_cost = (
        capital * insurance_rate / productive_annual_km * round_trip_km
    )
    subtotal = (
        fuel_cost
        + driver_cost
        + maintenance_cost
        + tire_cost
        + capital_cost
        + insurance_cost
    )
    overhead_cost = subtotal * overhead
    total_cost = subtotal + overhead_cost
    payload_odt = payload_m3 * density
    payload_wet_kg = payload_odt / (1.0 - moisture) * 1_000.0
    energy_gj = payload_odt * lhv
    if payload_odt <= 0.0 or energy_gj <= 0.0:
        raise TransportCostV1Error(
            f"non-positive payload energy at payload_m3={payload_m3!r}"
        )
    return {
        "distance_km_one_way": distance_km_one_way,
        "payload_m3": payload_m3,
        "payload_odt": payload_odt,
        "payload_wet_tonnes": payload_wet_kg / 1_000.0,
        "energy_gj": energy_gj,
        "round_trip_km": round_trip_km,
        "driving_hours": driving_hours,
        "on_duty_hours": on_duty_hours,
        "fuel_l": fuel_l,
        "fuel_cost_cad": fuel_cost,
        "driver_cost_cad": driver_cost,
        "maintenance_cost_cad": maintenance_cost,
        "tire_cost_cad": tire_cost,
        "capital_cost_cad": capital_cost,
        "insurance_cost_cad": insurance_cost,
        "overhead_cost_cad": overhead_cost,
        "total_cost_cad": total_cost,
        "cost_per_m3_cad": total_cost / payload_m3,
        "cost_per_odt_cad": total_cost / payload_odt,
        "cost_per_odt_km_cad": total_cost / (payload_odt * distance_km_one_way)
        if distance_km_one_way > 0.0
        else 0.0,
        "cost_per_gj_cad": total_cost / energy_gj,
        "fuel_per_m3_cad": fuel_cost / payload_m3,
        "driver_per_m3_cad": driver_cost / payload_m3,
        "maintenance_per_m3_cad": maintenance_cost / payload_m3,
        "tire_per_m3_cad": tire_cost / payload_m3,
        "capital_per_m3_cad": capital_cost / payload_m3,
        "insurance_per_m3_cad": insurance_cost / payload_m3,
        "overhead_per_m3_cad": overhead_cost / payload_m3,
        "loaded_km_share": 0.5,
        "mass_utilization": payload_wet_kg / LEGAL_PAYLOAD_KG,
        "volume_utilization": payload_m3 / max(PAYLOADS_M3),
        "payload_wet_kg": payload_wet_kg,
        "legal_payload_kg": LEGAL_PAYLOAD_KG,
        "mass_limit_binding": payload_wet_kg > LEGAL_PAYLOAD_KG,
        "long_haul_flag": on_duty_hours > 13.0,
    }


def _curve_row(cost: dict[str, float], scenario: str) -> dict[str, Any]:
    row: dict[str, Any] = {"cost_scenario": scenario}
    for key, value in cost.items():
        if isinstance(value, bool):
            row[key] = _bool_text(value)
        elif isinstance(value, float):
            row[key] = _format_float(value)
        else:
            row[key] = value
    row.update(
        {
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
            "assignment_change": _bool_text(ASSIGNMENT_CHANGE),
            "national_claim": _bool_text(NATIONAL_CLAIM),
        }
    )
    return row


def build_curves(parameters: dict[str, Parameter]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scenario in COST_SCENARIOS:
        for distance in DISTANCES_KM:
            for payload in PAYLOADS_M3:
                rows.append(_curve_row(
                    trip_cost(distance, payload, parameters, scenario), scenario
                ))
    if len(rows) != len(COST_SCENARIOS) * len(DISTANCES_KM) * len(PAYLOADS_M3):
        raise TransportCostV1Error("curve row count does not match the design grid")
    return rows


# ---------------------------------------------------------------------------
# Supply-curve comparison.
# ---------------------------------------------------------------------------

def crossing_distance_km(
    tier_cost_per_gj: float,
    payload_m3: float,
    parameters: dict[str, Parameter],
    scenario: str,
) -> float:
    """Solve the linear trip cost for the distance where cost/GJ equals a tier.

    The trip cost is linear in distance because every component is either a
    per-km rate or a distance-independent terminal term.  The function samples
    the pure cost function at two distances and solves the line, so it cannot
    drift from the published arithmetic.
    """

    if not math.isfinite(tier_cost_per_gj) or tier_cost_per_gj <= 0.0:
        raise TransportCostV1Error(
            f"tier_cost_per_gj must be positive; got {tier_cost_per_gj!r}"
        )
    near = trip_cost(0.0, payload_m3, parameters, scenario)
    far = trip_cost(1_000.0, payload_m3, parameters, scenario)
    slope_per_km = (far["total_cost_cad"] - near["total_cost_cad"]) / 1_000.0
    energy_gj = near["energy_gj"]
    if slope_per_km <= 0.0:
        raise TransportCostV1Error("trip cost is not increasing with distance")
    target_total = tier_cost_per_gj * energy_gj
    return max(0.0, (target_total - near["total_cost_cad"]) / slope_per_km)


def build_supply_comparison(
    parameters: dict[str, Parameter], tiers: Sequence[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparison: list[dict[str, Any]] = []
    crossings: list[dict[str, Any]] = []
    for scenario in COST_SCENARIOS:
        for payload in PAYLOADS_M3:
            at_distances = {
                distance: trip_cost(distance, payload, parameters, scenario)
                for distance in DISTANCES_KM
            }
            for tier in tiers:
                crossing = crossing_distance_km(
                    tier["cost_per_gj"], payload, parameters, scenario
                )
                crossings.append(
                    {
                        "cost_scenario": scenario,
                        "payload_m3": _format_float(payload),
                        "tier": tier["tier"],
                        "technology": tier["technology"],
                        "tier_cost_per_gj": _format_float(tier["cost_per_gj"]),
                        "crossing_distance_km": _format_float(crossing),
                        "within_modelled_range": _bool_text(0.0 <= crossing <= 1_000.0),
                        **{
                            f"transport_exceeds_at_{int(distance)}km": _bool_text(
                                at_distances[distance]["cost_per_gj_cad"]
                                > tier["cost_per_gj"]
                            )
                            for distance in DISTANCES_KM
                        },
                        "evidence_label": EVIDENCE_LABEL,
                        "observed": _bool_text(OBSERVED),
                        "policy_grade": _bool_text(POLICY_GRADE),
                    }
                )
                for distance in DISTANCES_KM:
                    cost = at_distances[distance]
                    ratio = cost["cost_per_gj_cad"] / tier["cost_per_gj"]
                    comparison.append(
                        {
                            "distance_km_one_way": _format_float(distance),
                            "cost_scenario": scenario,
                            "payload_m3": _format_float(payload),
                            "transport_cost_cad_per_gj": _format_float(
                                cost["cost_per_gj_cad"]
                            ),
                            "tier": tier["tier"],
                            "technology": tier["technology"],
                            "tier_cost_per_gj": _format_float(tier["cost_per_gj"]),
                            "tier_cost_basis": tier["cost_basis"],
                            "tier_scenario_flag": tier["scenario_flag"],
                            "transport_exceeds_tier": _bool_text(
                                cost["cost_per_gj_cad"] > tier["cost_per_gj"]
                            ),
                            "ratio_transport_to_tier": _format_float(ratio),
                            "ratio_minus_one": _format_float(ratio - 1.0),
                            "currency_basis": (
                                "transport=2026 CAD/GJ; supply tier=interface "
                                "$/GJ as loaded, no documented currency or "
                                "price-year conversion applied"
                            ),
                            "evidence_label": EVIDENCE_LABEL,
                            "observed": _bool_text(OBSERVED),
                            "policy_grade": _bool_text(POLICY_GRADE),
                        }
                    )
    return comparison, crossings


# ---------------------------------------------------------------------------
# Context loaders.
# ---------------------------------------------------------------------------

def load_facility_context() -> dict[str, Any]:
    """Summarize assignment v6 distance context without changing assignment."""

    if not ASSIGNMENT_V6_CSV.is_file():
        return {
            "status": "unavailable",
            "path": str(ASSIGNMENT_V6_CSV.relative_to(REPO_ROOT)),
            "note": "Assignment v6 CSV not present; no context is fabricated.",
        }
    rows = _read_csv(ASSIGNMENT_V6_CSV)
    distances = sorted(
        _finite_float(row.get("road_distance_km"), "road_distance_km")
        for row in rows
        if row.get("road_distance_km") not in (None, "")
    )
    if any(value < 0.0 for value in distances):
        raise TransportCostV1Error("assignment v6 has a negative road_distance_km")
    if not distances:
        raise TransportCostV1Error("assignment v6 has no road_distance_km values")
    n = len(distances)

    def quantile(fraction: float) -> float:
        position = min(n - 1, max(0, round(fraction * (n - 1))))
        return distances[position]

    bins = [
        ("le_50_km", 0.0, 50.0),
        ("gt_50_le_100_km", 50.0, 100.0),
        ("gt_100_le_150_km", 100.0, 150.0),
        ("gt_150_le_300_km", 150.0, 300.0),
        ("gt_300_le_500_km", 300.0, 500.0),
        ("gt_500_km", 500.0, math.inf),
    ]
    bin_counts = {
        label: sum(1 for value in distances if low < value <= high)
        for label, low, high in bins
    }
    bin_counts["le_50_km"] = sum(1 for value in distances if value <= 50.0)
    return {
        "status": "context_only",
        "path": str(ASSIGNMENT_V6_CSV.relative_to(REPO_ROOT)),
        "n_facility_rows": len(rows),
        "n_unique_facilities": len({row.get("facility_id") for row in rows}),
        "provinces": sorted({row.get("facility_province", "") for row in rows}),
        "scenarios": sorted({row.get("scenario", "") for row in rows}),
        "road_distance_km": {
            "min": min(distances),
            "p25": quantile(0.25),
            "median": quantile(0.50),
            "p75": quantile(0.75),
            "max": max(distances),
        },
        "distance_bin_counts": bin_counts,
        "distance_basis": (
            "Facility-to-province-centroid allocation distance recorded by "
            "assignment v6; it is not an origin-to-facility route and is not "
            "used to change any assignment."
        ),
    }


def load_road_factor_context() -> dict[str, Any]:
    if not ROAD_FACTORS_CSV.is_file():
        return {
            "status": "unavailable",
            "path": str(ROAD_FACTORS_CSV.relative_to(REPO_ROOT)),
            "note": "Road-distance factor summary not present; no factor is fabricated.",
        }
    rows = _read_csv(ROAD_FACTORS_CSV)
    ratios = [
        _positive_float(row.get("median_road_haversine_ratio"), "road factor")
        for row in rows
    ]
    return {
        "status": "context_only",
        "path": str(ROAD_FACTORS_CSV.relative_to(REPO_ROOT)),
        "provinces": [row.get("province", "") for row in rows],
        "n_provinces": len(rows),
        "median_road_haversine_ratio": {
            "min": min(ratios),
            "median": sorted(ratios)[len(ratios) // 2],
            "max": max(ratios),
        },
        "note": (
            "Curves use one-way road kilometres directly.  The province road "
            "factors are context for converting haversine proxies to road km; "
            "they are not applied a second time."
        ),
    }


# ---------------------------------------------------------------------------
# Validation and report.
# ---------------------------------------------------------------------------

def validate(
    *,
    parameter_rows: Sequence[Parameter],
    curves: Sequence[dict[str, Any]],
    comparison: Sequence[dict[str, Any]],
    crossings: Sequence[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, bool]:
    numeric_curve_fields = [
        "payload_m3", "payload_odt", "payload_wet_tonnes", "energy_gj",
        "round_trip_km", "driving_hours", "on_duty_hours", "fuel_l",
        "fuel_cost_cad", "driver_cost_cad", "maintenance_cost_cad",
        "tire_cost_cad", "capital_cost_cad", "insurance_cost_cad",
        "overhead_cost_cad", "total_cost_cad", "cost_per_m3_cad",
        "cost_per_odt_cad", "cost_per_odt_km_cad", "cost_per_gj_cad",
        "fuel_per_m3_cad", "driver_per_m3_cad", "maintenance_per_m3_cad",
        "tire_per_m3_cad", "capital_per_m3_cad", "insurance_per_m3_cad",
        "overhead_per_m3_cad", "loaded_km_share", "mass_utilization",
        "volume_utilization", "payload_wet_kg", "legal_payload_kg",
    ]
    numeric_errors: list[str] = []
    component_errors: list[str] = []
    unit_errors: list[str] = []
    cost_range_errors: list[str] = []
    parameter_range_errors: list[str] = []
    for row in curves:
        label = (
            f"{row['distance_km_one_way']}km/"
            f"{row['payload_m3']}m3/{row['cost_scenario']}"
        )
        for field in numeric_curve_fields:
            value = float(row[field])
            if not math.isfinite(value) or value < 0.0:
                numeric_errors.append(f"{label}:{field}")
        components = math.fsum(
            float(row[field])
            for field in (
                "fuel_cost_cad", "driver_cost_cad", "maintenance_cost_cad",
                "tire_cost_cad", "capital_cost_cad", "insurance_cost_cad",
                "overhead_cost_cad",
            )
        )
        if abs(components - float(row["total_cost_cad"])) > 1e-5:
            component_errors.append(label)
        if abs(
            float(row["total_cost_cad"]) / float(row["payload_m3"])
            - float(row["cost_per_m3_cad"])
        ) > 1e-5:
            unit_errors.append(f"{label}:m3")
        if abs(
            float(row["total_cost_cad"]) / float(row["payload_odt"])
            - float(row["cost_per_odt_cad"])
        ) > 1e-5:
            unit_errors.append(f"{label}:odt")
        if float(row["distance_km_one_way"]) > 0.0 and abs(
            float(row["cost_per_odt_cad"])
            / float(row["distance_km_one_way"])
            - float(row["cost_per_odt_km_cad"])
        ) > 1e-5:
            unit_errors.append(f"{label}:odt_km")
        if abs(
            float(row["total_cost_cad"]) / float(row["energy_gj"])
            - float(row["cost_per_gj_cad"])
        ) > 1e-5:
            unit_errors.append(f"{label}:gj")

    grouped: dict[tuple[float, float], dict[str, float]] = {}
    for row in curves:
        key = (float(row["distance_km_one_way"]), float(row["payload_m3"]))
        grouped.setdefault(key, {})[row["cost_scenario"]] = float(
            row["cost_per_m3_cad"]
        )
    for key, values in grouped.items():
        if not (
            values.get("low", math.inf)
            <= values.get("base", math.inf)
            <= values.get("high", -math.inf)
        ):
            cost_range_errors.append(f"{key}:cost_per_m3")

    for parameter in parameter_rows:
        if not (parameter.low <= parameter.base <= parameter.high):
            parameter_range_errors.append(f"parameter:{parameter.parameter_id}")

    comparison_errors = [
        row
        for row in comparison
        if abs(
            float(row["ratio_transport_to_tier"])
            - float(row["transport_cost_cad_per_gj"])
            / float(row["tier_cost_per_gj"])
        )
        > 1e-6
    ]
    crossing_errors = [
        row
        for row in crossings
        if not math.isfinite(float(row["crossing_distance_km"]))
        or float(row["crossing_distance_km"]) < 0.0
    ]
    return {
        "grid_complete": len(curves)
        == len(COST_SCENARIOS) * len(DISTANCES_KM) * len(PAYLOADS_M3),
        "parameter_bounds": not parameter_range_errors,
        "cost_component_sum": not component_errors,
        "unit_arithmetic": not unit_errors,
        "cost_scenario_order": not cost_range_errors,
        "comparison_arithmetic": not comparison_errors,
        "crossing_finite_non_negative": not crossing_errors,
        "no_nan_or_negative": not numeric_errors,
        "labels": (
            summary["labels"]["evidence_label"] == EVIDENCE_LABEL
            and summary["labels"]["observed"] is False
            and summary["labels"]["policy_grade"] is False
            and summary["labels"]["assignment_change"] is False
            and summary["labels"]["national_claim"] is False
        ),
        "mass_limit_screen_present": all(
            "mass_limit_binding" in row for row in curves
        ),
        "mass_limit_not_binding": all(
            row["mass_limit_binding"] == "false" for row in curves
        ),
        "no_assignment_change": summary["facility_context"]["status"]
        in {"context_only", "unavailable"},
        "no_official_osecan_change": summary["labels"]["official_osecan_changed"]
        is False,
    }


def _report(summary: dict[str, Any], curves: Sequence[dict[str, Any]]) -> str:
    base_by_distance = {
        float(row["distance_km_one_way"]): row
        for row in curves
        if row["cost_scenario"] == "base"
        and abs(float(row["payload_m3"]) - BASE_PAYLOAD_M3) < 1e-9
    }
    low_by_distance = {
        float(row["distance_km_one_way"]): row
        for row in curves
        if row["cost_scenario"] == "low"
        and abs(float(row["payload_m3"]) - BASE_PAYLOAD_M3) < 1e-9
    }
    high_by_distance = {
        float(row["distance_km_one_way"]): row
        for row in curves
        if row["cost_scenario"] == "high"
        and abs(float(row["payload_m3"]) - BASE_PAYLOAD_M3) < 1e-9
    }
    lines = [
        "# National truck transport cost v1",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        (
            "Status: **model estimate**. Open-data bottom-up truck cost component; "
            "`model_estimate`, `observed=false`, `policy_grade=false`. No assignment "
            "change, no national observed claim, no policy claim, and no official "
            "OSeCAN or UI change."
        ),
        "",
        "## Cost components",
        "",
        (
            "Base trip at 100 km one-way and 52.5 m3 payload. The truck returns "
            "empty; every loaded kilometre is paired with an empty kilometre. "
            "Overhead is applied to the six operating components."
        ),
        "",
        "| Component | CAD/trip | CAD/m3 | Share of total | Basis |",
        "|---|---|---|---|---|",
    ]
    base = base_by_distance[100.0]
    total = float(base["total_cost_cad"])
    component_basis = [
        ("Fuel", "fuel_cost_cad", "fuel_per_m3_cad", "StatCan diesel x L/km"),
        ("Driver / labour", "driver_cost_cad", "driver_per_m3_cad",
         "wage x burden x on-duty hours"),
        ("Maintenance", "maintenance_cost_cad", "maintenance_per_m3_cad",
         "repair and maintenance CAD/km"),
        ("Tires", "tire_cost_cad", "tire_per_m3_cad", "tire wear CAD/km"),
        ("Capital", "capital_cost_cad", "capital_per_m3_cad",
         "depreciation + interest on average investment"),
        ("Insurance", "insurance_cost_cad", "insurance_per_m3_cad",
         "annual insurance / productive km"),
        ("Overhead", "overhead_cost_cad", "overhead_per_m3_cad",
         "fraction of operating subtotal"),
    ]
    for name, cost_field, m3_field, basis in component_basis:
        lines.append(
            f"| {name} | {_format_float(float(base[cost_field]), 2)} | "
            f"{_format_float(float(base[m3_field]), 2)} | "
            f"{_format_float(100.0 * float(base[cost_field]) / total, 1)}% | {basis} |"
        )
    lines.extend(
        [
            (
                f"| **Total** | **{_format_float(total, 2)}** | "
                f"**{_format_float(float(base['cost_per_m3_cad']), 2)}** | "
                "**100.0%** | |"
            ),
            "",
            "## $/m3 vs distance",
            "",
            (
                "One-way road km; 52.5 m3 base payload; low/base/high is a "
                "favourable-to-unfavourable stress envelope, not a confidence interval."
            ),
            "",
            "| One-way km | Low CAD/m3 | Base CAD/m3 | High CAD/m3 | Base CAD/ODT | Base CAD/GJ |",
            "|---|---|---|---|---|---|",
        ]
    )
    for distance in DISTANCES_KM:
        low = low_by_distance[distance]
        base_row = base_by_distance[distance]
        high = high_by_distance[distance]
        lines.append(
            f"| {int(distance)} | {_format_float(float(low['cost_per_m3_cad']), 2)} | "
            f"{_format_float(float(base_row['cost_per_m3_cad']), 2)} | "
            f"{_format_float(float(high['cost_per_m3_cad']), 2)} | "
            f"{_format_float(float(base_row['cost_per_odt_cad']), 2)} | "
            f"{_format_float(float(base_row['cost_per_gj_cad']), 2)} |"
        )
    lines.extend(
        [
            "",
            "### Payload sensitivity",
            "",
            "| One-way km | 45 m3 CAD/m3 | 52.5 m3 CAD/m3 | 60 m3 CAD/m3 |",
            "|---|---|---|---|",
        ]
    )
    payload_by_distance: dict[tuple[float, float], float] = {
        (float(row["distance_km_one_way"]), float(row["payload_m3"])): float(
            row["cost_per_m3_cad"]
        )
        for row in curves
        if row["cost_scenario"] == "base"
    }
    for distance in DISTANCES_KM:
        lines.append(
            f"| {int(distance)} | "
            f"{_format_float(payload_by_distance[(distance, 45.0)], 2)} | "
            f"{_format_float(payload_by_distance[(distance, 52.5)], 2)} | "
            f"{_format_float(payload_by_distance[(distance, 60.0)], 2)} |"
        )
    lines.extend(
        [
            "",
            "## Supply-curve comparison",
            "",
            (
                "The comparison is in $/GJ at face value: transport is 2026 CAD/GJ; "
                "the adopted OSeCAN tier values are loaded as `$/GJ` with no "
                "documented currency or price-year conversion. The JSON field is "
                "named `variable_cost_usd_per_gj`, so this is an explicit "
                "parity/undocumented-currency assumption, not an FX conversion. "
                "A CAD-stronger interpretation would raise the tier threshold and "
                "push the crossing distances outward."
            ),
            "",
            (
                "Crossing distance is the one-way road distance at which transport "
                "alone equals the tier variable cost, at 52.5 m3 base payload. "
                "Values above 1,000 km are outside the modelled range."
            ),
            "",
            "| Scenario | T1/T2 2.457 | T3 3.194 | T4 3.500 |",
            "|---|---|---|---|",
        ]
    )
    crossings_by_scenario = {
        (row["cost_scenario"], row["tier"]): float(row["crossing_distance_km"])
        for row in summary["supply_curve"]["crossings"]
        if abs(float(row["payload_m3"]) - BASE_PAYLOAD_M3) < 1e-9
    }
    for scenario in COST_SCENARIOS:
        lines.append(
            f"| {scenario} | "
            f"{_format_float(crossings_by_scenario[(scenario, 'T1')], 1)} | "
            f"{_format_float(crossings_by_scenario[(scenario, 'T3')], 1)} | "
            f"{_format_float(crossings_by_scenario[(scenario, 'T4')], 1)} |"
        )
    lines.extend(
        [
            "",
            (
                "At the base payload and base cost, transport alone exceeds the "
                "T1/T2 adopted tier beyond the crossing shown, the T3 mid-cost "
                "tier beyond its crossing, and the T4 high-cost marginal tier "
                "beyond its crossing. At 50 km the base cost is below T1/T2; at "
                "100 km it is above T1/T2 but below T3; at 150 km it is above "
                "T4. By 500 km every modelled scenario and payload exceeds all "
                "four tiers; at 300 km the most favourable low-cost, high-density, "
                "60 m3 envelope is still below T3/T4. This is a screening "
                "statement about transport alone, not a delivered-cost or policy "
                "conclusion."
            ),
            "",
            "## Facility and road-distance context",
            "",
        ]
    )
    facility = summary["facility_context"]
    if facility["status"] == "context_only":
        lines.extend(
            [
                (
                    f"- Assignment v6 has {facility['n_facility_rows']} rows / "
                    f"{facility['n_unique_facilities']} unique facilities in "
                    f"{len(facility['provinces'])} provinces."
                ),
                (
                    "- Recorded allocation distance quantiles (km): "
                    f"min {_format_float(facility['road_distance_km']['min'], 1)}, "
                    f"p25 {_format_float(facility['road_distance_km']['p25'], 1)}, "
                    f"median {_format_float(facility['road_distance_km']['median'], 1)}, "
                    f"p75 {_format_float(facility['road_distance_km']['p75'], 1)}, "
                    f"max {_format_float(facility['road_distance_km']['max'], 1)}."
                ),
                (
                    "- Distance bins: "
                    + ", ".join(
                        f"{label} {count}"
                        for label, count in facility["distance_bin_counts"].items()
                    )
                    + "."
                ),
                (
                    "- This is facility-to-province-centroid allocation distance, "
                    "not a route; it is context only and changes no assignment."
                ),
            ]
        )
    else:
        lines.append("- " + facility.get("note", "Facility context unavailable."))
    road = summary["road_distance_context"]
    if road["status"] == "context_only":
        lines.append(
            "- Road-distance audit v1 median road/haversine ratios range "
            f"{_format_float(road['median_road_haversine_ratio']['min'], 3)} to "
            f"{_format_float(road['median_road_haversine_ratio']['max'], 3)}; "
            "curves use road km directly and do not apply a second factor."
        )
    else:
        lines.append("- " + road.get("note", "Road-factor context unavailable."))
    lines.extend(
        [
            "",
            "## Parameters and sources",
            "",
            "| Parameter | Unit | Low | Base | High | Source | Licence |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for row in summary["parameters"]:
        lines.append(
            f"| `{row['parameter_id']}` | {row['unit']} | "
            f"{_format_float(row['low'], 4)} | {_format_float(row['base'], 4)} | "
            f"{_format_float(row['high'], 4)} | {row['source_name']} | "
            f"{row['licence']} |"
        )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "```text",
            "round_trip_km = 2 x one_way_km",
            "driving_hours = one_way_km / speed_loaded + one_way_km / speed_empty",
            "on_duty_hours = driving_hours + terminal_hours",
            "fuel = diesel x one_way_km x (loaded_L_per_km + empty_L_per_km)",
            "driver = wage x (1 + burden) x on_duty_hours",
            "maintenance = maintenance_CAD_per_km x round_trip_km",
            "tires = tire_CAD_per_km x round_trip_km",
            "depreciation_per_km = capital x (1 - residual) / life_km",
            "interest_per_km = capital x (1 + residual) / 2 x interest / (annual_km x utilization)",
            "capital = (depreciation_per_km + interest_per_km) x round_trip_km",
            "insurance = capital x insurance_rate / (annual_km x utilization) x round_trip_km",
            "total = (fuel + driver + maintenance + tires + capital + insurance) x (1 + overhead)",
            "CAD_per_m3 = total / payload_m3",
            "CAD_per_ODT = total / (payload_m3 x bulk_density_ODT_per_m3)",
            "CAD_per_GJ = CAD_per_ODT / LHV_GJ_per_ODT",
            "CAD_per_ODT_km = CAD_per_ODT / one_way_km",
            "```",
            "",
            "## Limits",
            "",
            (
                "- This is a national-scope component, not a national observed "
                "average. The diesel price is the Vancouver self-service series; "
                "wages are BC anchors. No province-specific calibration is claimed."
            ),
            (
                "- Bulk density, fuel burn, speeds, terminal time, life, residual, "
                "interest, insurance and overhead are engineering assumptions, not "
                "observations. The low/high columns stack favourable or unfavourable "
                "draws; they are not a statistical confidence interval."
            ),
            (
                "- Mass is screened against a public 63,500 kg GVW / 45,500 kg "
                "payload envelope. The modelled residue loads are volume-limited; "
                "the mass cap is not binding in the stated density range. Axle-group "
                "limits are reported as context, not applied axle-by-axle."
            ),
            (
                "- Empty return is modelled in full. No backhaul, border delay, "
                "customs, ferry, winter road, resource-road, queue at scale, or "
                "hours-of-service relay is costed. Trips over 13 on-duty hours are "
                "flagged."
            ),
            (
                "- The supply-curve comparison treats the loaded tier $/GJ at face "
                "value. The interface does not document currency or price year; no "
                "FX or deflator is applied."
            ),
            (
                "- No assignment, facility capacity, harvest, AAC, tenure, market, "
                "emissions, legal, or operating-status claim is made. No UI or "
                "official OSeCAN input is changed."
            ),
            "",
            "## Provenance",
            "",
        ]
    )
    for source in summary["provenance"]["sources"]:
        location = f"`{source['path']}` " if source["path"] else ""
        lines.append(
            f"- {source['name']}: {location}({source['licence']}); `{source['url']}`"
        )
    lines.extend(
        [
            "",
            (
                "Reproduce: `.venv/bin/python -m "
                "workflows.national.facilities.transport_cost_v1`"
            ),
            "",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Build.
# ---------------------------------------------------------------------------

def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    deletion_checker: Callable[[], None] = assert_no_unexpected_deletions,
) -> dict[str, Any]:
    """Build the parameter register, curves, comparison and manifest."""

    deletion_checker()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    observations = load_open_observations()
    capital = load_capital_bounds()
    parameters = build_parameters(observations, capital)
    parameter_map = _parameter_map(parameters)
    curves = build_curves(parameter_map)
    tiers = load_supply_tiers()
    comparison, crossings = build_supply_comparison(parameter_map, tiers)
    facility_context = load_facility_context()
    road_factor_context = load_road_factor_context()

    parameter_rows: list[dict[str, Any]] = []
    for parameter in parameters:
        source = _source_row(parameter.source_id)
        parameter_rows.append(
            {
                "parameter_id": parameter.parameter_id,
                "component": parameter.component,
                "unit": parameter.unit,
                "low": _format_float(parameter.low),
                "base": _format_float(parameter.base),
                "high": _format_float(parameter.high),
                "direction": parameter.direction,
                "scenario_low_value": _format_float(parameter.value_for("low")),
                "scenario_base_value": _format_float(parameter.value_for("base")),
                "scenario_high_value": _format_float(parameter.value_for("high")),
                "source_id": parameter.source_id,
                "source_name": source["name"],
                "licence": source["licence"],
                "evidence": EVIDENCE_LABEL,
                "observed": _bool_text(OBSERVED),
                "policy_grade": _bool_text(POLICY_GRADE),
                "notes": parameter.notes,
            }
        )

    base_curve = {
        (float(row["distance_km_one_way"]), float(row["payload_m3"])): row
        for row in curves
        if row["cost_scenario"] == "base"
    }
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "assignment_change": ASSIGNMENT_CHANGE,
            "national_claim": NATIONAL_CLAIM,
            "official_osecan_changed": False,
            "ui_changed": False,
            "model_scope": "national_scope_component_not_national_observed_average",
        },
        "scope": {
            "distances_km_one_way": list(DISTANCES_KM),
            "payloads_m3": list(PAYLOADS_M3),
            "cost_scenarios": list(COST_SCENARIOS),
            "return_leg": "empty",
            "loaded_km_share": 0.5,
            "currency": "CAD",
            "price_year": 2026,
            "lhv_gj_per_odt": load_residue_lhv(),
            "bulk_density_odt_per_m3": {
                "low": parameter_map["residue_bulk_density_odt_per_m3"].low,
                "base": parameter_map["residue_bulk_density_odt_per_m3"].base,
                "high": parameter_map["residue_bulk_density_odt_per_m3"].high,
            },
        },
        "open_observations": {
            "diesel_cad_per_l": {
                "low": observations["diesel_low"],
                "base": observations["diesel_base"],
                "high": observations["diesel_high"],
                "months": int(observations["diesel_months"]),
                "series": "Vancouver self-service diesel",
            },
            "wage_cad_per_h_2026": {
                "low": observations["wage_low"],
                "base": observations["wage_base"],
                "high": observations["wage_high"],
            },
            "cpi_factor_2025_to_2026": observations["cpi_factor"],
            "freight_ppi_growth_2025_to_2026": observations["ppi_growth"],
            "capital_cad_2026": capital,
        },
        "parameters": [
            {
                "parameter_id": row["parameter_id"],
                "unit": row["unit"],
                "low": float(row["low"]),
                "base": float(row["base"]),
                "high": float(row["high"]),
                "source_id": row["source_id"],
                "source_name": row["source_name"],
                "licence": row["licence"],
            }
            for row in parameter_rows
        ],
        "curves_summary": {
            "n_rows": len(curves),
            "base_payload_m3": BASE_PAYLOAD_M3,
            "base_by_distance": {
                f"{int(distance)}km": {
                    "cost_per_m3_cad": float(base_curve[(distance, BASE_PAYLOAD_M3)]["cost_per_m3_cad"]),
                    "cost_per_odt_cad": float(base_curve[(distance, BASE_PAYLOAD_M3)]["cost_per_odt_cad"]),
                    "cost_per_odt_km_cad": float(base_curve[(distance, BASE_PAYLOAD_M3)]["cost_per_odt_km_cad"]),
                    "cost_per_gj_cad": float(base_curve[(distance, BASE_PAYLOAD_M3)]["cost_per_gj_cad"]),
                }
                for distance in DISTANCES_KM
            },
            "payload_sensitivity_cost_per_m3": {
                f"{int(distance)}km": {
                    f"{payload:g}m3": float(base_curve[(distance, payload)]["cost_per_m3_cad"])
                    for payload in PAYLOADS_M3
                }
                for distance in DISTANCES_KM
            },
        },
        "supply_curve": {
            "path": str(SUPPLY_CURVE_JSON.relative_to(REPO_ROOT)),
            "currency_basis": (
                "transport=2026 CAD/GJ; tier=interface $/GJ as loaded; "
                "the JSON field name is variable_cost_usd_per_gj but no "
                "currency or price-year conversion is documented; primary "
                "comparison is at parity with no FX applied"
            ),
            "tiers": tiers,
            "comparison_rows": len(comparison),
            "crossing_rows": len(crossings),
            "crossings": crossings,
            "base_payload_exceeds": {
                f"{int(distance)}km": {
                    tier["tier"]: float(base_curve[(distance, BASE_PAYLOAD_M3)]["cost_per_gj_cad"])
                    > tier["cost_per_gj"]
                    for tier in tiers
                }
                for distance in DISTANCES_KM
            },
        },
        "facility_context": facility_context,
        "road_distance_context": road_factor_context,
        "validation": {},
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            "National-scope component, not a national observed average or a national claim.",
            "The low/base/high envelope stacks favourable and unfavourable assumptions; it is not a confidence interval.",
            "Mass cap is a screen only; axle-group limits are not applied axle-by-axle.",
            "No backhaul, border, ferry, winter-road, resource-road, queue, or hours-of-service relay cost is modelled.",
            "Supply-curve dollars are loaded at face value; currency and price year are undocumented in the interface.",
            "No assignment, facility capacity, harvest, market, emissions, legal, or policy claim is made.",
            "No UI or official OSeCAN input is changed.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.transport_cost_v1"
        ),
    }

    validation = validate(
        parameter_rows=parameters,
        curves=curves,
        comparison=comparison,
        crossings=crossings,
        summary=summary,
    )
    summary["validation"] = {
        "checks": validation,
        "all_passed": all(validation.values()),
    }
    if not summary["validation"]["all_passed"]:
        raise TransportCostV1Error(
            f"validation failed: {summary['validation']['checks']}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(output_dir / "parameters.csv", PARAMETER_FIELDS, parameter_rows)
    _write_csv(output_dir / "curves.csv", CURVE_FIELDS, curves)
    _write_csv(
        output_dir / "supply_curve_comparison.csv",
        COMPARISON_FIELDS,
        comparison,
    )
    _write_csv(
        output_dir / "supply_curve_crossings.csv",
        CROSSING_FIELDS,
        crossings,
    )
    sources = _sources(generated_utc, output_dir)
    _write_csv(output_dir / "provenance.csv", PROVENANCE_FIELDS, sources)
    summary["provenance"] = {"sources": sources}
    summary["inputs"] = {
        source["source_id"]: {
            "path": source["path"],
            "sha256": source["sha256"],
            "role": source["role"],
        }
        for source in sources
        if source["role"] in {"input", "context"}
    }
    summary["outputs"] = {
        "parameters_csv": "parameters.csv",
        "curves_csv": "curves.csv",
        "supply_curve_comparison_csv": "supply_curve_comparison.csv",
        "supply_curve_crossings_csv": "supply_curve_crossings.csv",
        "provenance_csv": "provenance.csv",
        "report_md": "TRANSPORT_COST_V1.md",
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "TRANSPORT_COST_V1.md").write_text(
        _report(summary, curves), encoding="utf-8"
    )
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "labels": summary["labels"],
        "inputs": summary["inputs"],
        "outputs": {
            path.name: {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in sorted(output_dir.iterdir())
            if path.is_file() and path.name != "manifest.json"
        },
        "scope": summary["scope"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


# ---------------------------------------------------------------------------
# Source register.
# ---------------------------------------------------------------------------

_SOURCE_TEMPLATES: dict[str, dict[str, Any]] = {
    "statcan_18100001_diesel": {
        "role": "input",
        "name": "Statistics Canada Table 18-10-0001-01: monthly average retail prices for gasoline and fuel oil",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1810000101-eng",
        "path": STRICT_OBS_CSV,
        "used_for": "Vancouver self-service diesel, 12-month low/base/high.",
    },
    "statcan_14100417_wages_occupation": {
        "role": "input",
        "name": "Statistics Canada Table 14-10-0417-01: employee wages by occupation",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1410041701-eng",
        "path": STRICT_OBS_CSV,
        "used_for": "BC transport operator wage base and natural-resource production low anchor.",
    },
    "statcan_14100064_wages_industry": {
        "role": "input",
        "name": "Statistics Canada Table 14-10-0064-01: employee wages by industry",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1410006401-eng",
        "path": STRICT_OBS_CSV,
        "used_for": "BC forestry grouping high wage anchor.",
    },
    "statcan_18100004_cpi": {
        "role": "input",
        "name": "Statistics Canada Table 18-10-0004-01: consumer price index",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1810000401-eng",
        "path": STRICT_OBS_CSV,
        "used_for": "BC all-items CPI, 2025 to 2026 wage escalation.",
    },
    "statcan_18100281_trucking_ppi": {
        "role": "cross_check",
        "name": "Statistics Canada Table 18-10-0281-01: for-hire motor carrier freight services price index",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1810028101-eng",
        "path": STRICT_OBS_CSV,
        "used_for": "Plausibility check on nominal truck cost growth; not a direct multiplier.",
    },
    "statcan_18100283_mepi_commodity": {
        "role": "input",
        "name": "Statistics Canada Table 18-10-0283-01: machinery and equipment price index by commodity",
        "publisher": "Statistics Canada",
        "year": 2026,
        "licence": "Statistics Canada Open Licence",
        "licence_url": "https://www.statcan.gc.ca/en/reference/licence",
        "url": "https://doi.org/10.25318/1810028301-eng",
        "path": STRICT_PARAMS_CSV,
        "used_for": "Escalation of the strict-open tractor and trailer capital bounds to 2026 CAD.",
    },
    "bc_open_cost_model_strict": {
        "role": "input",
        "name": "Strict-open BC logging and truck-haul cost model parameters",
        "publisher": "UBC FRESH (project-derived)",
        "year": 2026,
        "licence": "Project-derived; CC0 assumptions + Statistics Canada Open Licence observations",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "path": STRICT_PARAMS_CSV,
        "used_for": "Tractor and trailer replacement values; labour burden and capital-price context.",
    },
    "facility_residue_supply_v7": {
        "role": "input",
        "name": "National facility residue supply v7 summary",
        "publisher": "UBC FRESH (project-derived)",
        "year": 2026,
        "licence": "Project-derived; see source metadata",
        "licence_url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "path": RESIDUE_SUMMARY_JSON,
        "used_for": "Residue dry LHV (18.5 GJ/ODT); no volume or assignment data are copied.",
    },
    "ccmta_weights_dimensions": {
        "role": "public_reference",
        "name": "Canadian Council of Motor Transport Administrators interprovincial weights and dimensions envelope",
        "publisher": "CCMTA",
        "year": 2026,
        "licence": "Public regulatory reference; not an open-data licence",
        "licence_url": "https://ccmta.ca/en/trucking/weights-and-dimensions",
        "url": "https://ccmta.ca/en/trucking/weights-and-dimensions",
        "path": None,
        "used_for": "63,500 kg GVW and 17,000 kg tridem axle screen; not a permit or legal opinion.",
    },
    "model_derived": {
        "role": "derived_parameter",
        "name": "Parameter derived inside transport cost v1",
        "publisher": "This model",
        "year": 2026,
        "licence": "CC0 1.0 Universal public-domain dedication",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "url": "",
        "path": None,
        "used_for": "Legal payload derived as legal GVW minus truck tare.",
    },
    "model_assumptions_cc0": {
        "role": "model_assumption",
        "name": "Explicit engineering assumptions of transport cost v1",
        "publisher": "This model",
        "year": 2026,
        "licence": "CC0 1.0 Universal public-domain dedication",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "url": "",
        "path": None,
        "used_for": "Fuel burn, speeds, terminal time, life, residual, interest, insurance, overhead, bulk density, tare.",
    },
    "national_facility_assignment_v6": {
        "role": "context",
        "name": "National facility assignment v6",
        "publisher": "UBC FRESH (project-derived)",
        "year": 2026,
        "licence": "Project-derived; see source metadata",
        "licence_url": "https://github.com/UBC-FRESH/clews-c2070-fresh",
        "url": "https://github.com/UBC-FRESH/clews-c2070-fresh",
        "path": ASSIGNMENT_V6_CSV,
        "used_for": "Facility count and recorded allocation-distance context only; no assignment change.",
    },
    "facility_road_distance_audit_v1": {
        "role": "context",
        "name": "Facility road-distance audit v1 province summary",
        "publisher": "UBC FRESH (project-derived)",
        "year": 2026,
        "licence": "Project-derived; OSRM/OpenStreetMap ODbL-1.0 routing",
        "licence_url": "https://www.openstreetmap.org/copyright",
        "url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "path": ROAD_FACTORS_CSV,
        "used_for": "Province road/haversine factor context; curves use road km directly.",
    },
    "national_supply_curve": {
        "role": "comparison",
        "name": "National rest-of-Canada biomass supply curve (adopted OSeCAN tier definitions)",
        "publisher": "UBC FRESH (project-derived)",
        "year": 2026,
        "licence": "Project-derived; see source metadata",
        "licence_url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "url": "https://github.com/UBC-FRESH/clews-c2070-fresh-data",
        "path": SUPPLY_CURVE_JSON,
        "used_for": "Tier variable costs for comparison only; no OSeCAN input is written.",
    },
}


def _source_row(source_id: str) -> dict[str, Any]:
    try:
        template = _SOURCE_TEMPLATES[source_id]
    except KeyError as exc:
        raise TransportCostV1Error(f"unknown source id {source_id!r}") from exc
    return {
        "source_id": source_id,
        **{key: value for key, value in template.items()},
    }


def _sources(generated_utc: str, output_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source_id, template in _SOURCE_TEMPLATES.items():
        path = template["path"]
        rows.append(
            {
                "source_id": source_id,
                "role": template["role"],
                "name": template["name"],
                "publisher": template["publisher"],
                "year": template["year"],
                "licence": template["licence"],
                "licence_url": template["licence_url"],
                "url": template["url"],
                "path": (
                    str(path.relative_to(REPO_ROOT)) if path is not None else ""
                ),
                "sha256": _sha256(path) if path is not None and path.is_file() else "",
                "retrieved_utc": generated_utc,
                "used_for": template["used_for"],
                "notes": (
                    "Observed StatCan extract read from the committed strict-open artifact."
                    if source_id.startswith("statcan_")
                    else "Project-derived or explicit model assumption; see source register."
                ),
            }
        )
    return rows


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "n_curves": summary["curves_summary"]["n_rows"],
                "base_50km_cost_per_m3": summary["curves_summary"][
                    "base_by_distance"
                ]["50km"]["cost_per_m3_cad"],
                "base_500km_cost_per_m3": summary["curves_summary"][
                    "base_by_distance"
                ]["500km"]["cost_per_m3_cad"],
                "validation": summary["validation"]["all_passed"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
