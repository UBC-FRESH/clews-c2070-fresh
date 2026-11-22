"""Strict-open BC logging and truck-haul cost model.

All inputs are explicit assumptions or strict-open public statistics.  The model
is a cost proxy, not an observed cost ledger and not a BC empirical calibration.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Mapping
from dataclasses import dataclass, replace

from workflows.national.harvest.strict_open.machine_rate import (
    MachineSpec,
    machine_rate,
    miyata_example_spec,
    sessions_example_spec,
)
from workflows.national.harvest.strict_open.sources import (
    AREA_WEIGHTS_PATH,
    CALIBRATION_CLAIM,
    COST_BASIS,
    DIESEL_BASE,
    DIESEL_HIGH,
    DIESEL_LOW,
    EVIDENCE,
    HAUL_WAGE_BASE,
    HAUL_WAGE_HIGH,
    HAUL_WAGE_LOW,
    LICENCE_TIER,
    MEPI_ESCALATION_2026,
    OBSERVED,
    POLICY_GRADE,
    ROAD_CLASSES_PATH,
    SLOPE_OVERLAY_PATH,
    WAGE_BASE,
    WAGE_HIGH,
    WAGE_LOW,
)

MODEL_ID = "bc_open_cost_model_strict"
CAD_M3 = "CAD/m3"
CAD_SMH = "CAD/SMH"
CAD_PMH = "CAD/PMH"
M3_PMH = "m3/PMH"

ZONES: tuple[tuple[str, str], ...] = (
    ("South West Zone", "Coast"),
    ("North West Zone", "Interior"),
    ("North Central Zone", "Interior"),
    ("North East Zone", "Interior"),
    ("North Fort Nelson Zone", "Interior"),
    ("South Central Zone", "Interior"),
    ("South East Zone", "Interior"),
)
REGIONS = ("Coast", "Interior")
SYSTEMS = ("ground", "cable")

SLOPE_SCENARIOS: tuple[tuple[str, float, str], ...] = (
    ("gentle", 10.0, "ground"),
    ("moderate", 25.0, "ground"),
    ("steep", 40.0, "cable"),
    ("very_steep", 55.0, "cable"),
)
PIECE_SCENARIOS: tuple[tuple[str, float], ...] = (
    ("small", 0.30),
    ("medium", 0.60),
    ("large", 1.00),
)
VOLUME_SCENARIOS: tuple[tuple[str, float], ...] = (
    ("small_sale", 5_000.0),
    ("medium_sale", 20_000.0),
    ("large_sale", 100_000.0),
)
DISTANCE_SCENARIOS: tuple[tuple[str, float], ...] = (
    ("short_haul", 50.0),
    ("medium_haul", 100.0),
    ("long_haul", 150.0),
)

PHASES_BY_SYSTEM: dict[str, tuple[str, ...]] = {
    "ground": ("feller_buncher", "grapple_skidder", "processor", "loader"),
    "cable": ("feller_buncher", "yarder", "processor", "loader"),
}
SLOPE_SENSITIVE_PHASES = frozenset(
    {"feller_buncher", "grapple_skidder", "forwarder", "yarder"}
)

# 2017 replacement-price assumptions in 2017 CAD.  They are escalated with the
# Statistics Canada MEPI commodity index for logging/construction/mining
# machinery.  These are assumptions, not manufacturer quotes.
MACHINE_2017_PRICE: dict[str, float] = {
    "feller_buncher": 450_000.0,
    "grapple_skidder": 320_000.0,
    "forwarder": 420_000.0,
    "processor": 520_000.0,
    "loader": 380_000.0,
    "yarder": 1_300_000.0,
    "truck_tractor": 190_000.0,
    "trailer": 65_000.0,
}

MACHINE_CONFIG: dict[str, dict[str, float]] = {
    "feller_buncher": {
        "economic_life_hours": 12_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.75,
        "fuel_litres_per_pmh": 40.0,
        "repair_fraction": 0.60,
        "tire_fraction": 0.10,
        "wage_premium": 1.00,
    },
    "grapple_skidder": {
        "economic_life_hours": 12_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.72,
        "fuel_litres_per_pmh": 32.0,
        "repair_fraction": 0.55,
        "tire_fraction": 0.12,
        "wage_premium": 1.00,
    },
    "forwarder": {
        "economic_life_hours": 15_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.75,
        "fuel_litres_per_pmh": 35.0,
        "repair_fraction": 0.55,
        "tire_fraction": 0.10,
        "wage_premium": 1.00,
    },
    "processor": {
        "economic_life_hours": 12_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.72,
        "fuel_litres_per_pmh": 38.0,
        "repair_fraction": 0.60,
        "tire_fraction": 0.08,
        "wage_premium": 1.00,
    },
    "loader": {
        "economic_life_hours": 15_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.70,
        "fuel_litres_per_pmh": 30.0,
        "repair_fraction": 0.55,
        "tire_fraction": 0.08,
        "wage_premium": 1.00,
    },
    "yarder": {
        "economic_life_hours": 15_000.0,
        "scheduled_hours_per_year": 1_800.0,
        "utilization": 0.65,
        "fuel_litres_per_pmh": 55.0,
        "repair_fraction": 0.65,
        "tire_fraction": 0.02,
        "wage_premium": 1.10,
    },
    "truck_tractor": {
        "economic_life_hours": 15_000.0,
        "scheduled_hours_per_year": 2_200.0,
        "utilization": 0.85,
        "fuel_litres_per_pmh": 38.0,
        "repair_fraction": 0.45,
        "tire_fraction": 0.12,
        "wage_premium": 1.05,
    },
    "trailer": {
        "economic_life_hours": 15_000.0,
        "scheduled_hours_per_year": 2_200.0,
        "utilization": 0.85,
        "fuel_litres_per_pmh": 0.0,
        "repair_fraction": 0.30,
        "tire_fraction": 0.15,
        "wage_premium": 0.0,
    },
}

REGION_MODIFIERS: dict[str, dict[str, float]] = {
    "Coast": {"capital": 1.12, "wage": 1.08, "utilization_delta": -0.03},
    "Interior": {"capital": 1.00, "wage": 1.00, "utilization_delta": 0.0},
}

LABOUR_BURDEN = 0.25
LUBE_FRACTION = 0.05
SUPPORT_FRACTION = 0.08
PLANNING_SUPERVISION_CAD_M3 = 1.20
MOBILIZATION_CAD = {"ground": 25_000.0, "cable": 45_000.0}
IN_BLOCK_KM = 1.0

COAST_ECOPROVINCES = frozenset(
    {"Georgia Depression", "Southern Coastal Mountains", "Northern Coastal Mountains"}
)


class StrictCostModelError(ValueError):
    """A strict-open model input or output violates the documented contract."""


@dataclass(frozen=True)
class CostRange:
    low: float
    base: float
    high: float
    unit: str = CAD_M3

    def __post_init__(self) -> None:
        values = (self.low, self.base, self.high)
        if any(not math.isfinite(value) for value in values):
            raise StrictCostModelError(f"non-finite cost range: {values}")
        if any(value < 0.0 for value in values):
            raise StrictCostModelError(f"negative cost range: {values}")
        if not self.low <= self.base <= self.high:
            raise StrictCostModelError(f"unordered cost range: {values}")

    def scaled(self, factor: float) -> CostRange:
        if not math.isfinite(factor) or factor <= 0.0:
            raise StrictCostModelError(f"invalid cost scale: {factor!r}")
        return CostRange(self.low * factor, self.base * factor, self.high * factor)

    def merged(self, other: CostRange) -> CostRange:
        return CostRange(
            self.low + other.low, self.base + other.base, self.high + other.high
        )


@dataclass(frozen=True)
class ParameterSet:
    """One coherent parameter draw for low, base or high."""

    label: str
    wage: float
    haul_wage: float
    diesel: float
    capital_factor: float
    utilization_delta: float
    productivity_factor: float
    interest_rate: float
    insurance_rate: float
    repair_factor: float
    overhead_fraction: float
    salvage_fraction: float
    road_loaded_kmh: float
    road_empty_kmh: float
    in_block_loaded_kmh: float
    in_block_empty_kmh: float
    delay_hours: float
    load_unload_hours: float
    payload_m3: float
    haul_support_fraction: float

    def __post_init__(self) -> None:
        for name in ("wage", "haul_wage", "diesel", "capital_factor", "payload_m3"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise StrictCostModelError(f"{name} must be positive: {value!r}")
        if not 0.0 < self.productivity_factor <= 2.0:
            raise StrictCostModelError(
                f"productivity_factor out of range: {self.productivity_factor!r}"
            )
        if not 0.0 <= self.overhead_fraction <= 0.5:
            raise StrictCostModelError(
                f"overhead_fraction out of range: {self.overhead_fraction!r}"
            )
        for name in ("road_loaded_kmh", "road_empty_kmh", "in_block_loaded_kmh", "in_block_empty_kmh"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise StrictCostModelError(f"{name} must be positive: {value!r}")


BASE_PARAMS = ParameterSet(
    label="base",
    wage=WAGE_BASE,
    haul_wage=HAUL_WAGE_BASE,
    diesel=DIESEL_BASE,
    capital_factor=1.00,
    utilization_delta=0.00,
    productivity_factor=1.00,
    interest_rate=0.06,
    insurance_rate=0.02,
    repair_factor=1.00,
    overhead_fraction=0.15,
    salvage_fraction=0.20,
    road_loaded_kmh=55.0,
    road_empty_kmh=65.0,
    in_block_loaded_kmh=10.0,
    in_block_empty_kmh=15.0,
    delay_hours=1.30,
    load_unload_hours=0.50,
    payload_m3=50.0,
    haul_support_fraction=0.08,
)
LOW_PARAMS = replace(
    BASE_PARAMS,
    label="low",
    wage=WAGE_LOW,
    haul_wage=HAUL_WAGE_LOW,
    diesel=DIESEL_LOW,
    capital_factor=0.85,
    utilization_delta=0.05,
    productivity_factor=1.15,
    interest_rate=0.04,
    insurance_rate=0.015,
    repair_factor=0.85,
    overhead_fraction=0.10,
    salvage_fraction=0.25,
    road_loaded_kmh=60.0,
    road_empty_kmh=70.0,
    in_block_loaded_kmh=12.0,
    in_block_empty_kmh=17.0,
    delay_hours=1.00,
    load_unload_hours=0.40,
    payload_m3=55.0,
    haul_support_fraction=0.03,
)
HIGH_PARAMS = replace(
    BASE_PARAMS,
    label="high",
    wage=WAGE_HIGH,
    haul_wage=HAUL_WAGE_HIGH,
    diesel=DIESEL_HIGH,
    capital_factor=1.20,
    utilization_delta=-0.10,
    productivity_factor=0.80,
    interest_rate=0.08,
    insurance_rate=0.03,
    repair_factor=1.25,
    overhead_fraction=0.20,
    salvage_fraction=0.15,
    road_loaded_kmh=45.0,
    road_empty_kmh=55.0,
    in_block_loaded_kmh=8.0,
    in_block_empty_kmh=12.0,
    delay_hours=1.50,
    load_unload_hours=0.60,
    payload_m3=42.0,
    haul_support_fraction=0.15,
)
BOUND_PARAMS = {"low": LOW_PARAMS, "base": BASE_PARAMS, "high": HIGH_PARAMS}


@dataclass(frozen=True)
class Scenario:
    """One zone x slope x piece-size x sale-volume x haul-distance case."""

    zone: str
    region: str
    slope_class: str
    slope_pct: float
    system: str
    piece_class: str
    piece_size_m3: float
    volume_class: str
    sale_volume_m3: float
    distance_class: str
    haul_distance_km: float

    def __post_init__(self) -> None:
        if self.region not in REGIONS:
            raise StrictCostModelError(f"unknown region: {self.region!r}")
        if self.system not in SYSTEMS:
            raise StrictCostModelError(f"unknown system: {self.system!r}")
        if self.system == "ground" and self.slope_pct > 35.0:
            raise StrictCostModelError(
                f"ground system not permitted at {self.slope_pct}% slope"
            )
        if not 0.0 <= self.slope_pct <= 100.0:
            raise StrictCostModelError(f"invalid slope_pct: {self.slope_pct!r}")
        for name in ("piece_size_m3", "sale_volume_m3", "haul_distance_km"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0.0:
                raise StrictCostModelError(f"{name} must be positive: {value!r}")

    @property
    def scenario_id(self) -> str:
        return (
            f"{self.zone.replace(' ', '_')}__{self.slope_class}"
            f"__{self.piece_class}__{self.volume_class}__{self.distance_class}"
        )

    @property
    def labels(self) -> dict[str, str]:
        return {
            "evidence": EVIDENCE,
            "cost_basis": COST_BASIS,
            "licence_tier": LICENCE_TIER,
            "policy_grade": POLICY_GRADE,
            "observed": OBSERVED,
            "calibration_claim": CALIBRATION_CLAIM,
            "bc_empirical_calibration": "false",
        }


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def machine_spec(
    machine_id: str, region: str, params: ParameterSet
) -> MachineSpec:
    """Build one machine specification from assumptions and the parameter draw."""
    if machine_id not in MACHINE_CONFIG:
        raise StrictCostModelError(f"unknown machine: {machine_id!r}")
    if region not in REGION_MODIFIERS:
        raise StrictCostModelError(f"unknown region: {region!r}")
    config = MACHINE_CONFIG[machine_id]
    modifier = REGION_MODIFIERS[region]
    capital_price = (
        MACHINE_2017_PRICE[machine_id]
        * MEPI_ESCALATION_2026
        * modifier["capital"]
        * params.capital_factor
    )
    utilization = _clip(
        config["utilization"] + modifier["utilization_delta"] + params.utilization_delta,
        0.45,
        0.95,
    )
    wage = (
        params.haul_wage if machine_id in {"truck_tractor", "trailer"} else params.wage
    ) * modifier["wage"] * config["wage_premium"]
    return MachineSpec(
        machine_id=machine_id,
        capital_price_cad=capital_price,
        economic_life_hours=config["economic_life_hours"],
        scheduled_hours_per_year=config["scheduled_hours_per_year"],
        utilization=utilization,
        interest_rate=params.interest_rate,
        insurance_tax_storage_rate=params.insurance_rate,
        fuel_litres_per_pmh=config["fuel_litres_per_pmh"],
        fuel_price_cad_per_litre=params.diesel,
        lube_fraction_of_fuel=LUBE_FRACTION,
        repair_fraction_of_depreciation=min(
            1.5, config["repair_fraction"] * params.repair_factor
        ),
        tire_fraction_of_depreciation=config["tire_fraction"],
        operator_wage_cad_per_smh=wage,
        labour_burden_fraction=LABOUR_BURDEN,
        overhead_profit_fraction=params.overhead_fraction,
        salvage_fraction=params.salvage_fraction,
    )


def phase_productivity(
    phase: str,
    piece_size_m3: float,
    slope_pct: float,
    system: str,
    params: ParameterSet,
) -> float:
    """Return engineering productivity in m3 per productive machine hour.

    The cycle-time equations are explicit assumptions.  They are not fitted to
    BC contractor observations and are not a BC empirical calibration.
    """
    if phase not in {"feller_buncher", "grapple_skidder", "forwarder", "processor", "loader", "yarder"}:
        raise StrictCostModelError(f"unknown productivity phase: {phase!r}")
    if system not in SYSTEMS:
        raise StrictCostModelError(f"unknown system: {system!r}")
    piece = piece_size_m3
    if not math.isfinite(piece) or piece <= 0.0:
        raise StrictCostModelError(f"invalid piece size: {piece!r}")

    if phase == "feller_buncher":
        cycle_seconds = 14.0 + 26.0 * piece
        payload = piece
        efficiency = 0.80
        productivity = payload * 3600.0 / cycle_seconds * efficiency
    elif phase == "grapple_skidder":
        payload = min(7.0, 4.0 + 4.0 * piece)
        cycle_hours = 0.24 - 0.06 * min(piece, 1.0)
        productivity = payload / cycle_hours * 0.85
    elif phase == "forwarder":
        payload = min(18.0, 10.0 + 10.0 * piece)
        cycle_hours = 0.50 - 0.10 * min(piece, 1.0)
        productivity = payload / cycle_hours * 0.85
    elif phase == "processor":
        cycle_seconds = 16.0 + 30.0 * piece
        payload = piece
        efficiency = 0.80
        productivity = payload * 3600.0 / cycle_seconds * efficiency
    elif phase == "loader":
        payload = 2.2
        cycle_hours = 0.025
        productivity = payload / cycle_hours * 0.90
    else:
        payload = min(6.0, 3.5 + 3.0 * piece)
        cycle_hours = 0.16 - 0.03 * min(piece, 1.0)
        productivity = payload / cycle_hours * 0.75

    if phase in SLOPE_SENSITIVE_PHASES:
        if system == "ground":
            slope_factor = 1.0 / (1.0 + 0.018 * max(0.0, slope_pct - 10.0))
        else:
            slope_factor = 1.0 / (1.0 + 0.010 * max(0.0, slope_pct - 35.0))
        productivity *= slope_factor

    productivity *= params.productivity_factor
    if not math.isfinite(productivity) or productivity <= 0.0:
        raise StrictCostModelError(
            f"non-positive productivity for {phase} at piece {piece}: {productivity!r}"
        )
    return productivity


def logging_cost(
    region: str,
    slope_pct: float,
    piece_size_m3: float,
    sale_volume_m3: float,
    system: str,
    params: ParameterSet,
) -> CostRange:
    """Tree-to-truck logging cost for one system and parameter draw."""
    if system not in PHASES_BY_SYSTEM:
        raise StrictCostModelError(f"unknown system: {system!r}")
    phase_costs: list[float] = []
    for phase in PHASES_BY_SYSTEM[system]:
        rate = machine_rate(machine_spec(phase, region, params)).total_cad_per_pmh
        productivity = phase_productivity(
            phase, piece_size_m3, slope_pct, system, params
        )
        phase_costs.append(rate / productivity)
    subtotal = sum(phase_costs)
    support = subtotal * SUPPORT_FRACTION
    mobilization = MOBILIZATION_CAD[system] / sale_volume_m3
    total = subtotal + support + mobilization + PLANNING_SUPERVISION_CAD_M3
    return CostRange(total, total, total)


def cycle_hours(distance_km: float, params: ParameterSet) -> float:
    """Round-trip truck cycle hours including in-block travel and delays."""
    if not math.isfinite(distance_km) or distance_km <= 0.0:
        raise StrictCostModelError(f"invalid haul distance: {distance_km!r}")
    in_block = min(IN_BLOCK_KM, distance_km)
    road = max(0.0, distance_km - in_block)
    return (
        in_block / params.in_block_loaded_kmh
        + in_block / params.in_block_empty_kmh
        + road / params.road_loaded_kmh
        + road / params.road_empty_kmh
        + params.delay_hours
        + params.load_unload_hours
    )


def haul_cost(region: str, distance_km: float, params: ParameterSet) -> CostRange:
    """Truck-haul cost per m3 for one parameter draw."""
    truck = machine_rate(machine_spec("truck_tractor", region, params)).total_cad_per_pmh
    trailer = machine_rate(machine_spec("trailer", region, params)).total_cad_per_pmh
    cycle = cycle_hours(distance_km, params)
    cost = (truck + trailer) * cycle / params.payload_m3
    cost *= 1.0 + params.haul_support_fraction
    return CostRange(cost, cost, cost)


def _combine_bounds(bounds: Mapping[str, CostRange]) -> CostRange:
    low = bounds["low"].base
    base = bounds["base"].base
    high = bounds["high"].base
    if not low <= base <= high:
        raise StrictCostModelError(
            f"base bound is not between low and high: {low!r}, {base!r}, {high!r}"
        )
    return CostRange(low, base, high)


def scenario_costs(scenario: Scenario) -> dict[str, CostRange]:
    """Return logging, haul and total low/base/high ranges for a scenario."""
    logging_bounds = {
        label: logging_cost(
            scenario.region,
            scenario.slope_pct,
            scenario.piece_size_m3,
            scenario.sale_volume_m3,
            scenario.system,
            params,
        )
        for label, params in BOUND_PARAMS.items()
    }
    haul_bounds = {
        label: haul_cost(scenario.region, scenario.haul_distance_km, params)
        for label, params in BOUND_PARAMS.items()
    }
    logging = _combine_bounds(logging_bounds)
    haul = _combine_bounds(haul_bounds)
    return {"logging": logging, "haul": haul, "total": logging.merged(haul)}


def estimate_scenario(scenario: Scenario) -> dict[str, str]:
    """Return one auditable estimate row with strict-open labels."""
    costs = scenario_costs(scenario)
    logging = costs["logging"]
    haul = costs["haul"]
    total = costs["total"]
    row = {
        "scenario_id": scenario.scenario_id,
        "zone": scenario.zone,
        "region": scenario.region,
        "slope_class": scenario.slope_class,
        "slope_pct": f"{scenario.slope_pct:.1f}",
        "system": scenario.system,
        "piece_class": scenario.piece_class,
        "piece_size_m3": f"{scenario.piece_size_m3:.2f}",
        "volume_class": scenario.volume_class,
        "sale_volume_m3": f"{scenario.sale_volume_m3:.0f}",
        "distance_class": scenario.distance_class,
        "haul_distance_km": f"{scenario.haul_distance_km:.0f}",
        "logging_low_cad_m3": f"{logging.low:.2f}",
        "logging_base_cad_m3": f"{logging.base:.2f}",
        "logging_high_cad_m3": f"{logging.high:.2f}",
        "haul_low_cad_m3": f"{haul.low:.2f}",
        "haul_base_cad_m3": f"{haul.base:.2f}",
        "haul_high_cad_m3": f"{haul.high:.2f}",
        "total_low_cad_m3": f"{total.low:.2f}",
        "total_base_cad_m3": f"{total.base:.2f}",
        "total_high_cad_m3": f"{total.high:.2f}",
        "logging_basis": "miyata_sessions_machine_rate_x_engineering_cycle",
        "haul_basis": "machine_rate_truck_cycle_payload",
        "wage_basis": "statcan_wage_anchors",
        "fuel_basis": "statcan_diesel_vancouver",
        "capital_basis": "mepi_escalated_2017_assumption",
        "productivity_basis": "explicit_engineering_cycle_time",
        "calibration_claim": CALIBRATION_CLAIM,
        "bc_empirical_calibration": "false",
        "price_series_used": "false",
        **scenario.labels,
    }
    return row


def build_scenarios() -> list[Scenario]:
    """Build the full zone x slope x piece x volume x distance grid."""
    scenarios: list[Scenario] = []
    for zone, region in ZONES:
        for slope_class, slope_pct, system in SLOPE_SCENARIOS:
            for piece_class, piece_size in PIECE_SCENARIOS:
                for volume_class, sale_volume in VOLUME_SCENARIOS:
                    for distance_class, distance in DISTANCE_SCENARIOS:
                        scenarios.append(
                            Scenario(
                                zone=zone,
                                region=region,
                                slope_class=slope_class,
                                slope_pct=slope_pct,
                                system=system,
                                piece_class=piece_class,
                                piece_size_m3=piece_size,
                                volume_class=volume_class,
                                sale_volume_m3=sale_volume,
                                distance_class=distance_class,
                                haul_distance_km=distance,
                            )
                        )
    return scenarios


def _base_scenario() -> Scenario:
    return Scenario(
        zone="South Central Zone",
        region="Interior",
        slope_class="moderate",
        slope_pct=25.0,
        system="ground",
        piece_class="medium",
        piece_size_m3=0.60,
        volume_class="medium_sale",
        sale_volume_m3=20_000.0,
        distance_class="medium_haul",
        haul_distance_km=100.0,
    )


def sensitivity_rows() -> list[dict[str, str]]:
    """One-at-a-time sensitivity cases around the interior base scenario."""
    base_scenario = _base_scenario()
    base_costs = scenario_costs(base_scenario)["total"].base
    cases: list[tuple[str, str, Scenario, ParameterSet]] = [
        ("base", "interior moderate/medium/base", base_scenario, BASE_PARAMS),
        (
            "slope_gentle",
            "slope 10%, ground",
            replace(base_scenario, slope_class="gentle", slope_pct=10.0, system="ground"),
            BASE_PARAMS,
        ),
        (
            "slope_steep",
            "slope 40%, cable",
            replace(base_scenario, slope_class="steep", slope_pct=40.0, system="cable"),
            BASE_PARAMS,
        ),
        (
            "slope_very_steep",
            "slope 55%, cable",
            replace(
                base_scenario,
                slope_class="very_steep",
                slope_pct=55.0,
                system="cable",
            ),
            BASE_PARAMS,
        ),
        (
            "piece_0p30",
            "piece 0.30 m3",
            replace(base_scenario, piece_class="small", piece_size_m3=0.30),
            BASE_PARAMS,
        ),
        (
            "piece_1p00",
            "piece 1.00 m3",
            replace(base_scenario, piece_class="large", piece_size_m3=1.00),
            BASE_PARAMS,
        ),
        (
            "volume_5k",
            "sale 5,000 m3",
            replace(base_scenario, volume_class="small_sale", sale_volume_m3=5_000.0),
            BASE_PARAMS,
        ),
        (
            "volume_100k",
            "sale 100,000 m3",
            replace(base_scenario, volume_class="large_sale", sale_volume_m3=100_000.0),
            BASE_PARAMS,
        ),
        (
            "haul_50km",
            "haul 50 km",
            replace(base_scenario, distance_class="short_haul", haul_distance_km=50.0),
            BASE_PARAMS,
        ),
        (
            "haul_150km",
            "haul 150 km",
            replace(base_scenario, distance_class="long_haul", haul_distance_km=150.0),
            BASE_PARAMS,
        ),
        (
            "wage_low",
            "wage low (StatCan natural-resources production)",
            base_scenario,
            replace(BASE_PARAMS, wage=WAGE_LOW, haul_wage=HAUL_WAGE_LOW),
        ),
        (
            "wage_high",
            "wage high (StatCan BC forestry grouping)",
            base_scenario,
            replace(BASE_PARAMS, wage=WAGE_HIGH, haul_wage=HAUL_WAGE_HIGH),
        ),
        (
            "diesel_low",
            "diesel 12-month minimum",
            base_scenario,
            replace(BASE_PARAMS, diesel=DIESEL_LOW),
        ),
        (
            "diesel_high",
            "diesel 12-month maximum",
            base_scenario,
            replace(BASE_PARAMS, diesel=DIESEL_HIGH),
        ),
        (
            "capital_low",
            "capital -15%",
            base_scenario,
            replace(BASE_PARAMS, capital_factor=0.85),
        ),
        (
            "capital_high",
            "capital +20%",
            base_scenario,
            replace(BASE_PARAMS, capital_factor=1.20),
        ),
        (
            "productivity_low",
            "productivity -20%",
            base_scenario,
            replace(BASE_PARAMS, productivity_factor=0.80),
        ),
        (
            "productivity_high",
            "productivity +15%",
            base_scenario,
            replace(BASE_PARAMS, productivity_factor=1.15),
        ),
        (
            "utilization_low",
            "utilization -10 percentage points",
            base_scenario,
            replace(BASE_PARAMS, utilization_delta=-0.10),
        ),
        (
            "utilization_high",
            "utilization +5 percentage points",
            base_scenario,
            replace(BASE_PARAMS, utilization_delta=0.05),
        ),
        (
            "overhead_low",
            "overhead/profit 10%",
            base_scenario,
            replace(BASE_PARAMS, overhead_fraction=0.10),
        ),
        (
            "overhead_high",
            "overhead/profit 20%",
            base_scenario,
            replace(BASE_PARAMS, overhead_fraction=0.20),
        ),
    ]
    rows: list[dict[str, str]] = []
    for case_id, change, scenario, params in cases:
        logging = logging_cost(
            scenario.region,
            scenario.slope_pct,
            scenario.piece_size_m3,
            scenario.sale_volume_m3,
            scenario.system,
            params,
        )
        haul = haul_cost(scenario.region, scenario.haul_distance_km, params)
        total = logging.merged(haul)
        rows.append(
            {
                "case_id": case_id,
                "change": change,
                "scenario_id": scenario.scenario_id,
                "logging_base_cad_m3": f"{logging.base:.2f}",
                "haul_base_cad_m3": f"{haul.base:.2f}",
                "total_base_cad_m3": f"{total.base:.2f}",
                "delta_total_cad_m3": f"{total.base - base_costs:+.2f}",
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
                "notes": "One-at-a-time change from the interior moderate/medium base case; not a statistical confidence interval.",
            }
        )
    return rows


def region_slope_context() -> dict[str, dict[str, float]]:
    """Compute BC regional slope shares from OGL-Canada and OGL-BC aggregates.

    The ecoprovince forest areas come from the BC VRI derived aggregate.  The
    slope shares come from the NRCan MRDEM-30 derived screening surface.
    """
    for path in (AREA_WEIGHTS_PATH, SLOPE_OVERLAY_PATH, ROAD_CLASSES_PATH):
        if not path.is_file():
            raise StrictCostModelError(f"missing strict-open input: {path}")

    area_by_ecoprovince: dict[str, float] = {}
    with AREA_WEIGHTS_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            area = float(row["area_ha"])
            if not math.isfinite(area) or area < 0.0:
                raise StrictCostModelError(f"invalid area in {AREA_WEIGHTS_PATH}: {area}")
            area_by_ecoprovince[row["ecoprovince"]] = (
                area_by_ecoprovince.get(row["ecoprovince"], 0.0) + area
            )

    slope_by_ecoprovince: dict[str, dict[str, float]] = {}
    with SLOPE_OVERLAY_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["province"] != "BC":
                continue
            slope_by_ecoprovince[row["ecoprovince"]] = {
                "mean_pct": float(row["slope_mean_pct"]),
                "share_gt15": float(row["share_gt15"]),
                "share_gt30": float(row["share_gt30"]),
            }

    context: dict[str, dict[str, float]] = {}
    for region in REGIONS:
        if region == "Coast":
            ecoprovinces = [
                name for name in slope_by_ecoprovince if name in COAST_ECOPROVINCES
            ]
        else:
            ecoprovinces = [
                name for name in slope_by_ecoprovince if name not in COAST_ECOPROVINCES
            ]
        ecoprovinces = [
            name for name in ecoprovinces if area_by_ecoprovince.get(name, 0.0) > 0.0
        ]
        total_area = sum(area_by_ecoprovince[name] for name in ecoprovinces)
        if total_area <= 0.0:
            raise StrictCostModelError(f"no forest area for region {region}")
        mean_slope = sum(
            area_by_ecoprovince[name] * slope_by_ecoprovince[name]["mean_pct"]
            for name in ecoprovinces
        ) / total_area
        share_gt15 = sum(
            area_by_ecoprovince[name] * slope_by_ecoprovince[name]["share_gt15"]
            for name in ecoprovinces
        ) / total_area
        share_gt30 = sum(
            area_by_ecoprovince[name] * slope_by_ecoprovince[name]["share_gt30"]
            for name in ecoprovinces
        ) / total_area
        # Only two slope thresholds are published.  Split the >30% area 90/10
        # between the steep and very-steep scenario classes.  This split is an
        # explicit assumption, not an observed distribution.
        gentle = 1.0 - share_gt15
        moderate = share_gt15 - share_gt30
        steep = share_gt30 * 0.90
        very_steep = share_gt30 * 0.10
        shares = {
            "gentle": gentle,
            "moderate": moderate,
            "steep": steep,
            "very_steep": very_steep,
        }
        if not math.isclose(sum(shares.values()), 1.0, rel_tol=1e-9, abs_tol=1e-9):
            raise StrictCostModelError(f"slope shares do not sum to one: {shares}")
        context[region] = {
            "forest_area_ha": total_area,
            "mean_slope_pct": mean_slope,
            "share_gt15": share_gt15,
            "share_gt30": share_gt30,
            **shares,
        }

    road_shares: dict[str, float] = {}
    with ROAD_CLASSES_PATH.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["province"] != "BC":
                continue
            values = [
                float(row["road_0_5km_ha"]),
                float(row["road_5_10km_ha"]),
                float(row["road_10_20km_ha"]),
                float(row["road_gt_20km_ha"]),
            ]
            total = sum(values)
            if total <= 0.0:
                raise StrictCostModelError("BC road-distance classes sum to zero")
            road_shares = {
                "road_0_5km": values[0] / total,
                "road_5_10km": values[1] / total,
                "road_10_20km": values[2] / total,
                "road_gt_20km": values[3] / total,
            }
    context["BC_road_access"] = road_shares
    return context


def region_slope_summary() -> list[dict[str, str]]:
    """Representative medium piece/volume/distance costs by region and slope."""
    context = region_slope_context()
    rows: list[dict[str, str]] = []
    for region in REGIONS:
        for slope_class, slope_pct, system in SLOPE_SCENARIOS:
            scenario = Scenario(
                zone="all_zones_in_region",
                region=region,
                slope_class=slope_class,
                slope_pct=slope_pct,
                system=system,
                piece_class="medium",
                piece_size_m3=0.60,
                volume_class="medium_sale",
                sale_volume_m3=20_000.0,
                distance_class="medium_haul",
                haul_distance_km=100.0,
            )
            costs = scenario_costs(scenario)
            rows.append(
                {
                    "region": region,
                    "slope_class": slope_class,
                    "slope_pct": f"{slope_pct:.1f}",
                    "system": system,
                    "bc_slope_share": f"{context[region][slope_class]:.6f}",
                    "logging_low_cad_m3": f"{costs['logging'].low:.2f}",
                    "logging_base_cad_m3": f"{costs['logging'].base:.2f}",
                    "logging_high_cad_m3": f"{costs['logging'].high:.2f}",
                    "haul_low_cad_m3": f"{costs['haul'].low:.2f}",
                    "haul_base_cad_m3": f"{costs['haul'].base:.2f}",
                    "haul_high_cad_m3": f"{costs['haul'].high:.2f}",
                    "total_low_cad_m3": f"{costs['total'].low:.2f}",
                    "total_base_cad_m3": f"{costs['total'].base:.2f}",
                    "total_high_cad_m3": f"{costs['total'].high:.2f}",
                    "slope_weight_basis": "OGL-Canada MRDEM-30 area-weighted; >30% split 90/10",
                    "evidence": EVIDENCE,
                    "cost_basis": COST_BASIS,
                    "licence_tier": LICENCE_TIER,
                    "policy_grade": POLICY_GRADE,
                    "observed": OBSERVED,
                    "calibration_claim": CALIBRATION_CLAIM,
                }
            )
    return rows


def zone_summary() -> list[dict[str, str]]:
    """Area-weighted slope expectation per zone at medium piece/volume/distance."""
    context = region_slope_context()
    rows: list[dict[str, str]] = []
    for zone, region in ZONES:
        totals = {"low": 0.0, "base": 0.0, "high": 0.0}
        logging = {"low": 0.0, "base": 0.0, "high": 0.0}
        haul = {"low": 0.0, "base": 0.0, "high": 0.0}
        for slope_class, slope_pct, system in SLOPE_SCENARIOS:
            share = context[region][slope_class]
            scenario = Scenario(
                zone=zone,
                region=region,
                slope_class=slope_class,
                slope_pct=slope_pct,
                system=system,
                piece_class="medium",
                piece_size_m3=0.60,
                volume_class="medium_sale",
                sale_volume_m3=20_000.0,
                distance_class="medium_haul",
                haul_distance_km=100.0,
            )
            costs = scenario_costs(scenario)
            for label in ("low", "base", "high"):
                totals[label] += share * getattr(costs["total"], label)
                logging[label] += share * getattr(costs["logging"], label)
                haul[label] += share * getattr(costs["haul"], label)
        rows.append(
            {
                "zone": zone,
                "region": region,
                "slope_weighting": "OGL-Canada MRDEM-30 regional slope shares",
                "logging_low_cad_m3": f"{logging['low']:.2f}",
                "logging_base_cad_m3": f"{logging['base']:.2f}",
                "logging_high_cad_m3": f"{logging['high']:.2f}",
                "haul_low_cad_m3": f"{haul['low']:.2f}",
                "haul_base_cad_m3": f"{haul['base']:.2f}",
                "haul_high_cad_m3": f"{haul['high']:.2f}",
                "total_low_cad_m3": f"{totals['low']:.2f}",
                "total_base_cad_m3": f"{totals['base']:.2f}",
                "total_high_cad_m3": f"{totals['high']:.2f}",
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
            }
        )
    return rows


def validation_payload(estimates: list[dict[str, str]]) -> dict[str, bool | int | str]:
    """Return the fail-closed validation record for the estimate grid."""
    expected = (
        len(ZONES) * len(SLOPE_SCENARIOS) * len(PIECE_SCENARIOS)
        * len(VOLUME_SCENARIOS) * len(DISTANCE_SCENARIOS)
    )
    if len(estimates) != expected:
        raise StrictCostModelError(
            f"estimate count {len(estimates)} != expected {expected}"
        )
    for row in estimates:
        for column in (
            "logging_low_cad_m3",
            "logging_base_cad_m3",
            "logging_high_cad_m3",
            "haul_low_cad_m3",
            "haul_base_cad_m3",
            "haul_high_cad_m3",
            "total_low_cad_m3",
            "total_base_cad_m3",
            "total_high_cad_m3",
        ):
            value = float(row[column])
            if not math.isfinite(value) or value < 0.0:
                raise StrictCostModelError(f"invalid {column} in {row['scenario_id']}")
        if not (
            float(row["total_low_cad_m3"])
            <= float(row["total_base_cad_m3"])
            <= float(row["total_high_cad_m3"])
        ):
            raise StrictCostModelError(f"unordered total in {row['scenario_id']}")
        if row["evidence"] != EVIDENCE or row["licence_tier"] != LICENCE_TIER:
            raise StrictCostModelError(f"bad labels in {row['scenario_id']}")
    return {
        "estimate_rows": len(estimates),
        "expected_rows": expected,
        "no_nan_or_infinite": True,
        "non_negative": True,
        "total_low_base_high_ordered": True,
        "all_zones_present": len({row["zone"] for row in estimates}) == len(ZONES),
        "all_regions_present": len({row["region"] for row in estimates}) == len(REGIONS),
        "all_slopes_present": len({row["slope_class"] for row in estimates})
        == len(SLOPE_SCENARIOS),
        "all_pieces_present": len({row["piece_class"] for row in estimates})
        == len(PIECE_SCENARIOS),
        "all_volumes_present": len({row["volume_class"] for row in estimates})
        == len(VOLUME_SCENARIOS),
        "all_distances_present": len({row["distance_class"] for row in estimates})
        == len(DISTANCE_SCENARIOS),
        "labels_strict_open": True,
        "policy_grade_false": all(row["policy_grade"] == "false" for row in estimates),
        "observed_false": all(row["observed"] == "false" for row in estimates),
        "bc_empirical_calibration_false": all(
            row["bc_empirical_calibration"] == "false" for row in estimates
        ),
    }


def worked_examples() -> list[dict[str, str]]:
    """Recompute the Sessions et al. (2021) and Miyata (1980) examples."""
    sessions_100 = machine_rate(sessions_example_spec(1.00), method="modified")
    sessions_50_traditional = machine_rate(
        sessions_example_spec(0.50), method="traditional"
    )
    sessions_50_modified = machine_rate(sessions_example_spec(0.50), method="modified")
    miyata = machine_rate(miyata_example_spec(), method="modified")
    examples = (
        (
            "sessions_2021_100pct",
            "Sessions et al. (2021) Table 3, U=100%, modified/traditional coincide",
            140.60,
            sessions_100.total_cad_per_pmh,
            "sessions_berry_han_2021",
        ),
        (
            "sessions_2021_50pct_traditional",
            "Sessions et al. (2021) Table 3, U=50%, Miyata-Brinker-Ackerman",
            269.20,
            sessions_50_traditional.total_cad_per_pmh,
            "sessions_berry_han_2021",
        ),
        (
            "sessions_2021_50pct_modified",
            "Sessions et al. (2021) Table 3, U=50%, utilization-adjusted life",
            192.60,
            sessions_50_modified.total_cad_per_pmh,
            "sessions_berry_han_2021",
        ),
        (
            "miyata_1980_example_11",
            "Miyata (1980) Example 11, rubber-tired skidder, fixed+operating+labour",
            35.61,
            miyata.total_cad_per_pmh,
            "miyata_1980",
        ),
    )
    rows: list[dict[str, str]] = []
    for check_id, description, expected, computed, source_key in examples:
        if abs(computed - expected) > 0.02:
            raise StrictCostModelError(
                f"worked example {check_id} failed: expected {expected}, got {computed}"
            )
        rows.append(
            {
                "check_id": check_id,
                "description": description,
                "expected_cad_per_pmh": f"{expected:.2f}",
                "computed_cad_per_pmh": f"{computed:.2f}",
                "absolute_error": f"{abs(computed - expected):.4f}",
                "source_key": source_key,
                "status": "pass",
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
            }
        )
    return rows


def parameter_rows() -> list[dict[str, str]]:
    """Return the auditable assumption and parameter register."""
    rows: list[dict[str, str]] = []

    def add(
        parameter_id: str,
        component: str,
        equation: str,
        unit: str,
        low: str,
        base: str,
        high: str,
        source_key: str,
        notes: str,
    ) -> None:
        rows.append(
            {
                "parameter_id": parameter_id,
                "component": component,
                "equation_or_value": equation,
                "unit": unit,
                "low": low,
                "base": base,
                "high": high,
                "source_key": source_key,
                "notes": notes,
                "evidence": EVIDENCE,
                "cost_basis": COST_BASIS,
                "licence_tier": LICENCE_TIER,
                "policy_grade": POLICY_GRADE,
                "observed": OBSERVED,
                "calibration_claim": CALIBRATION_CLAIM,
            }
        )

    for machine_id, config in MACHINE_CONFIG.items():
        add(
            f"capital_{machine_id}",
            f"capital_{machine_id}",
            "2017 replacement price x MEPI 2026/100 x regional capital modifier",
            "CAD",
            f"{MACHINE_2017_PRICE[machine_id] * MEPI_ESCALATION_2026 * 0.85:.0f}",
            f"{MACHINE_2017_PRICE[machine_id] * MEPI_ESCALATION_2026:.0f}",
            f"{MACHINE_2017_PRICE[machine_id] * MEPI_ESCALATION_2026 * 1.20:.0f}",
            "model_assumptions_cc0",
            "2017 price is an assumption; MEPI is the StatCan escalation. Not a dealer quote.",
        )
        add(
            f"life_{machine_id}",
            f"machine_life_{machine_id}",
            "economic life hours; modified method scales years by 1/utilization",
            "hours",
            f"{config['economic_life_hours'] * 0.85:.0f}",
            f"{config['economic_life_hours']:.0f}",
            f"{config['economic_life_hours'] * 1.20:.0f}",
            "model_assumptions_cc0",
            "Sessions et al. (2021) modified-life convention; life range is an assumption.",
        )
        add(
            f"utilization_{machine_id}",
            f"utilization_{machine_id}",
            "PMH / SMH; base plus region and bound deltas",
            "fraction",
            f"{_clip(config['utilization'] - 0.10, 0.45, 0.95):.2f}",
            f"{config['utilization']:.2f}",
            f"{_clip(config['utilization'] + 0.05, 0.45, 0.95):.2f}",
            "model_assumptions_cc0",
            "Assumption anchored to Miyata (1980) utilization table ranges, not BC observations.",
        )
        add(
            f"fuel_{machine_id}",
            f"fuel_{machine_id}",
            "litres per PMH; cost = litres x StatCan diesel price",
            "L/PMH",
            f"{config['fuel_litres_per_pmh'] * 0.85:.1f}",
            f"{config['fuel_litres_per_pmh']:.1f}",
            f"{config['fuel_litres_per_pmh'] * 1.20:.1f}",
            "model_assumptions_cc0",
            "Fuel burn is an engineering assumption; diesel price is StatCan 18-10-0001-01.",
        )
        add(
            f"repair_{machine_id}",
            f"repair_{machine_id}",
            "repair and maintenance as a fraction of hourly depreciation",
            "fraction of D",
            f"{config['repair_fraction'] * 0.85:.2f}",
            f"{config['repair_fraction']:.2f}",
            f"{config['repair_fraction'] * 1.25:.2f}",
            "model_assumptions_cc0",
            "Miyata (1980) tabulation ranges; explicit central assumption.",
        )

    add(
        "machine_rate_identity",
        "machine_rate",
        "PMH cost = D + interest/insurance/tax + fuel/lube + repair + tires + labour + overhead; SMH cost = PMH cost x U",
        "CAD/PMH",
        "",
        "",
        "",
        "miyata_1980",
        "Miyata average annual investment and fixed/operating identities; Sessions et al. modified life.",
    )
    add(
        "wage_low",
        "labour",
        "StatCan BC natural-resources production occupations, 2025, CPI to 2026",
        "CAD/hour",
        f"{WAGE_LOW:.2f}",
        f"{WAGE_LOW:.2f}",
        f"{WAGE_LOW:.2f}",
        "statcan_14100417_wages_occupation",
        "Lower wage anchor.",
    )
    add(
        "wage_base",
        "labour",
        "midpoint of StatCan logging production-worker wage and BC forestry grouping",
        "CAD/hour",
        f"{WAGE_LOW:.2f}",
        f"{WAGE_BASE:.2f}",
        f"{WAGE_HIGH:.2f}",
        "statcan_16100114_logging_industry",
        "Central wage is a midpoint assumption, not an observed BC logging wage.",
    )
    add(
        "wage_high",
        "labour",
        "StatCan BC forestry/fishing/mining/quarrying/oil and gas grouping, 2025, CPI to 2026",
        "CAD/hour",
        f"{WAGE_HIGH:.2f}",
        f"{WAGE_HIGH:.2f}",
        f"{WAGE_HIGH:.2f}",
        "statcan_14100064_wages_industry",
        "Upper wage anchor; broad industry grouping.",
    )
    add(
        "diesel_price",
        "fuel",
        "StatCan Vancouver self-service diesel, 12-month low/base/high, cents/L to CAD/L",
        "CAD/litre",
        f"{DIESEL_LOW:.4f}",
        f"{DIESEL_BASE:.4f}",
        f"{DIESEL_HIGH:.4f}",
        "statcan_18100001_diesel",
        "Retail pump price includes taxes; a conservative fuel input.",
    )
    add(
        "labour_burden",
        "labour",
        "payroll burden applied to base wage",
        "fraction",
        "0.20",
        f"{LABOUR_BURDEN:.2f}",
        "0.30",
        "model_assumptions_cc0",
        "Benefits, payroll taxes and workers' compensation; explicit assumption.",
    )
    add(
        "support_fraction",
        "logging_support",
        "supervision, mechanical support and system balance add-on",
        "fraction of phase cost",
        "0.05",
        f"{SUPPORT_FRACTION:.2f}",
        "0.12",
        "model_assumptions_cc0",
        "Explicit assumption.",
    )
    add(
        "mobilization_ground",
        "logging_mobilization",
        "fixed move-in/move-out cost divided by sale volume",
        "CAD",
        "20000",
        f"{MOBILIZATION_CAD['ground']:.0f}",
        "35000",
        "model_assumptions_cc0",
        "Explicit assumption; creates sale-volume sensitivity without appraisal coefficients.",
    )
    add(
        "mobilization_cable",
        "logging_mobilization",
        "fixed move-in/move-out cost divided by sale volume",
        "CAD",
        "35000",
        f"{MOBILIZATION_CAD['cable']:.0f}",
        "60000",
        "model_assumptions_cc0",
        "Explicit assumption.",
    )
    add(
        "planning_supervision",
        "logging_planning",
        "planning and supervision allowance",
        "CAD/m3",
        "0.80",
        f"{PLANNING_SUPERVISION_CAD_M3:.2f}",
        "1.80",
        "model_assumptions_cc0",
        "Explicit assumption.",
    )
    add(
        "slope_productivity_ground",
        "slope_sensitivity",
        "productivity factor = 1/(1 + 0.018 x max(0, slope-10))",
        "factor",
        "",
        "0.018 per percentage point above 10%",
        "",
        "model_assumptions_cc0",
        "Engineering proxy informed by the OGL-Canada MRDEM slope distribution; not an estimated cost elasticity.",
    )
    add(
        "slope_productivity_cable",
        "slope_sensitivity",
        "productivity factor = 1/(1 + 0.010 x max(0, slope-35))",
        "factor",
        "",
        "0.010 per percentage point above 35%",
        "",
        "model_assumptions_cc0",
        "Engineering proxy; cable is already the steep-terrain system.",
    )
    add(
        "haul_cycle",
        "truck_haul",
        "CYCLE = 1 km in-block loaded/empty + road loaded/empty + delay + load/unload; cost = (truck+trailer PMH) x CYCLE / payload x (1+support)",
        "hours",
        "45-60 km/h road; 1.0-1.5 h delay",
        "55/65 km/h road; 1.30 h delay; 0.50 h load/unload; 50 m3 payload",
        "42-55 m3 payload",
        "model_assumptions_cc0",
        "Public-road speed and payload are explicit assumptions; no CVSE or proprietary truck specification is used.",
    )
    add(
        "haul_support",
        "truck_haul",
        "dispatch, road management and schedule support add-on",
        "fraction",
        "0.03",
        "0.08",
        "0.15",
        "model_assumptions_cc0",
        "Explicit assumption.",
    )
    add(
        "capital_escalation",
        "capital_price",
        "MEPI logging/construction/mining machinery, July 2026, 2017=100",
        "index",
        f"{MEPI_ESCALATION_2026 * 0.89:.4f}",
        f"{MEPI_ESCALATION_2026:.4f}",
        f"{MEPI_ESCALATION_2026 * 1.06:.4f}",
        "statcan_18100283_mepi_commodity",
        "Index is a StatCan aggregate; the 2017 base price is an assumption.",
    )
    add(
        "productivity_equations",
        "productivity",
        "Felling: piece x 3600/(14+26 x piece) x 0.80; skidder: payload/turn x turns/hour x 0.85; processor: piece x 3600/(16+30 x piece) x 0.80; yarder: payload/turn x turns/hour x 0.75; loader: 2.2 m3/0.025 h x 0.90",
        "m3/PMH",
        "",
        "",
        "",
        "model_assumptions_cc0",
        "Engineering cycle-time assumptions, not BC empirical calibration.",
    )
    return rows
