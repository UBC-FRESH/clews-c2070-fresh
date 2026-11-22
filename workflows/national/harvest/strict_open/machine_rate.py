"""Machine-rate identities from Miyata (1980) and Sessions, Berry & Han (2021).

The equations are expressed per productive machine hour (PMH).  Scheduled
machine hour (SMH) cost is the PMH cost multiplied by utilization.  This makes
the utilization adjustment explicit and keeps productivity and cost in the same
denominator.

Two methods are implemented:

``modified``
    Sessions et al. (2021): extend economic life in years as utilization falls so
    cumulative productive hours over the machine life are unchanged.
``traditional``
    Miyata-Brinker-Ackerman: keep economic life in years fixed and spread fixed
    cost over fewer productive hours when utilization falls.

Both methods use Miyata's average-value-of-yearly-investment identity.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

METHODS = ("modified", "traditional")


class MachineRateError(ValueError):
    """A machine-rate input or output violates the documented contract."""


def _positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise MachineRateError(f"{name} must be a positive finite number: {value!r}")
    return value


def _non_negative(name: str, value: float) -> float:
    if not math.isfinite(value) or value < 0.0:
        raise MachineRateError(f"{name} must be a non-negative finite number: {value!r}")
    return value


def _fraction(name: str, value: float) -> float:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise MachineRateError(f"{name} must be a fraction in [0, 1]: {value!r}")
    return value


@dataclass(frozen=True)
class MachineSpec:
    """Capital, operating and labour assumptions for one machine."""

    machine_id: str
    capital_price_cad: float
    economic_life_hours: float
    scheduled_hours_per_year: float
    utilization: float
    interest_rate: float
    insurance_tax_storage_rate: float
    fuel_litres_per_pmh: float
    fuel_price_cad_per_litre: float
    lube_fraction_of_fuel: float
    repair_fraction_of_depreciation: float
    tire_fraction_of_depreciation: float
    operator_wage_cad_per_smh: float
    labour_burden_fraction: float
    overhead_profit_fraction: float
    salvage_fraction: float = 0.20
    fuel_lube_override_cad_per_pmh: float | None = None
    tire_override_cad_per_pmh: float | None = None

    def __post_init__(self) -> None:
        if not self.machine_id:
            raise MachineRateError("machine_id must not be empty")
        _positive("capital_price_cad", self.capital_price_cad)
        _positive("economic_life_hours", self.economic_life_hours)
        _positive("scheduled_hours_per_year", self.scheduled_hours_per_year)
        _fraction("utilization", self.utilization)
        if self.utilization == 0.0:
            raise MachineRateError("utilization must be greater than zero")
        _non_negative("interest_rate", self.interest_rate)
        _non_negative("insurance_tax_storage_rate", self.insurance_tax_storage_rate)
        _non_negative("fuel_litres_per_pmh", self.fuel_litres_per_pmh)
        _non_negative("fuel_price_cad_per_litre", self.fuel_price_cad_per_litre)
        _fraction("lube_fraction_of_fuel", self.lube_fraction_of_fuel)
        _fraction(
            "repair_fraction_of_depreciation", self.repair_fraction_of_depreciation
        )
        _fraction("tire_fraction_of_depreciation", self.tire_fraction_of_depreciation)
        _non_negative("operator_wage_cad_per_smh", self.operator_wage_cad_per_smh)
        _fraction("labour_burden_fraction", self.labour_burden_fraction)
        _fraction("overhead_profit_fraction", self.overhead_profit_fraction)
        _fraction("salvage_fraction", self.salvage_fraction)
        if self.fuel_lube_override_cad_per_pmh is not None:
            _non_negative(
                "fuel_lube_override_cad_per_pmh",
                self.fuel_lube_override_cad_per_pmh,
            )
        if self.tire_override_cad_per_pmh is not None:
            _non_negative("tire_override_cad_per_pmh", self.tire_override_cad_per_pmh)


@dataclass(frozen=True)
class MachineRate:
    """Hourly cost decomposition in 2026 CAD."""

    machine_id: str
    method: str
    depreciation_cad_per_pmh: float
    interest_insurance_tax_cad_per_pmh: float
    fuel_lube_cad_per_pmh: float
    repair_cad_per_pmh: float
    tire_cad_per_pmh: float
    labour_cad_per_pmh: float
    overhead_profit_cad_per_pmh: float
    total_cad_per_pmh: float
    total_cad_per_smh: float
    effective_life_years: float
    annual_pmh: float
    utilization: float

    def __post_init__(self) -> None:
        for field_name, value in self.__dict__.items():
            if field_name in {"machine_id", "method"}:
                continue
            if not math.isfinite(value) or value < 0.0:
                raise MachineRateError(
                    f"{field_name} must be a non-negative finite number: {value!r}"
                )
        if not 0.0 < self.utilization <= 1.0:
            raise MachineRateError(f"invalid utilization: {self.utilization!r}")
        components = (
            self.depreciation_cad_per_pmh
            + self.interest_insurance_tax_cad_per_pmh
            + self.fuel_lube_cad_per_pmh
            + self.repair_cad_per_pmh
            + self.tire_cad_per_pmh
            + self.labour_cad_per_pmh
            + self.overhead_profit_cad_per_pmh
        )
        if not math.isclose(components, self.total_cad_per_pmh, rel_tol=1e-9, abs_tol=1e-9):
            raise MachineRateError(
                f"component sum {components!r} != total {self.total_cad_per_pmh!r}"
            )
        if not math.isclose(
            self.total_cad_per_pmh * self.utilization,
            self.total_cad_per_smh,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise MachineRateError(
                "total_cad_per_smh must equal total_cad_per_pmh x utilization"
            )


def average_annual_investment(
    capital_price_cad: float, salvage_value_cad: float, life_years: float
) -> float:
    """Miyata (1980) average value of yearly investment (AVI).

    AVI = P - (P - S) * (N - 1) / (2N), which is algebraically identical to
    (P - S)(N + 1)/(2N) + S.
    """
    _positive("capital_price_cad", capital_price_cad)
    _non_negative("salvage_value_cad", salvage_value_cad)
    _positive("life_years", life_years)
    if salvage_value_cad > capital_price_cad:
        raise MachineRateError("salvage value cannot exceed capital price")
    return capital_price_cad - (capital_price_cad - salvage_value_cad) * (
        life_years - 1.0
    ) / (2.0 * life_years)


def machine_rate(spec: MachineSpec, method: str = "modified") -> MachineRate:
    """Return the machine-rate decomposition in CAD per productive machine hour.

    Raises:
        MachineRateError: on an unknown method, invalid spec, or non-finite
            output.
    """
    if method not in METHODS:
        raise MachineRateError(f"unknown machine-rate method: {method!r}")

    utilization = spec.utilization
    annual_pmh = spec.scheduled_hours_per_year * utilization
    if method == "modified":
        # Sessions et al. (2021): cumulative PMH life is unchanged, so the
        # economic life in years scales with 1 / utilization.
        life_years = spec.economic_life_hours / annual_pmh
    else:
        life_years = spec.economic_life_hours / spec.scheduled_hours_per_year

    salvage_value = spec.capital_price_cad * spec.salvage_fraction
    depreciable = spec.capital_price_cad - salvage_value
    depreciation = depreciable / (life_years * annual_pmh)
    average_investment = average_annual_investment(
        spec.capital_price_cad, salvage_value, life_years
    )
    interest_insurance_tax = (
        spec.interest_rate + spec.insurance_tax_storage_rate
    ) * average_investment / annual_pmh

    if spec.fuel_lube_override_cad_per_pmh is None:
        fuel = spec.fuel_litres_per_pmh * spec.fuel_price_cad_per_litre
        fuel_lube = fuel * (1.0 + spec.lube_fraction_of_fuel)
    else:
        fuel_lube = spec.fuel_lube_override_cad_per_pmh
    repair = spec.repair_fraction_of_depreciation * depreciation
    if spec.tire_override_cad_per_pmh is None:
        tire = spec.tire_fraction_of_depreciation * depreciation
    else:
        tire = spec.tire_override_cad_per_pmh

    # The operator is paid for scheduled hours, so per-PMH labour is the
    # scheduled wage divided by utilization.
    labour = (
        spec.operator_wage_cad_per_smh
        * (1.0 + spec.labour_burden_fraction)
        / utilization
    )

    subtotal = (
        depreciation
        + interest_insurance_tax
        + fuel_lube
        + repair
        + tire
        + labour
    )
    overhead_profit = subtotal * spec.overhead_profit_fraction
    total_per_pmh = subtotal + overhead_profit
    total_per_smh = total_per_pmh * utilization

    return MachineRate(
        machine_id=spec.machine_id,
        method=method,
        depreciation_cad_per_pmh=depreciation,
        interest_insurance_tax_cad_per_pmh=interest_insurance_tax,
        fuel_lube_cad_per_pmh=fuel_lube,
        repair_cad_per_pmh=repair,
        tire_cad_per_pmh=tire,
        labour_cad_per_pmh=labour,
        overhead_profit_cad_per_pmh=overhead_profit,
        total_cad_per_pmh=total_per_pmh,
        total_cad_per_smh=total_per_smh,
        effective_life_years=life_years,
        annual_pmh=annual_pmh,
        utilization=utilization,
    )


def sessions_example_spec(utilization: float) -> MachineSpec:
    """Sessions et al. (2021) Table 3 example, 20% salvage, no overhead."""
    return MachineSpec(
        machine_id="sessions_2021_example",
        capital_price_cad=500_000.0,
        economic_life_hours=10_000.0,
        scheduled_hours_per_year=2_000.0,
        utilization=utilization,
        interest_rate=0.08,
        insurance_tax_storage_rate=0.0,
        fuel_litres_per_pmh=0.0,
        fuel_price_cad_per_litre=0.0,
        lube_fraction_of_fuel=0.0,
        repair_fraction_of_depreciation=0.875,
        tire_fraction_of_depreciation=0.0,
        operator_wage_cad_per_smh=40.0,
        labour_burden_fraction=0.0,
        overhead_profit_fraction=0.0,
        salvage_fraction=0.20,
        fuel_lube_override_cad_per_pmh=12.0,
    )


def miyata_example_spec() -> MachineSpec:
    """Miyata (1980) Example 11 rubber-tired skidder, converted to PMH life.

    P = 46,790, S = 9,358, N = 3 years, SH = 2,000 h/year, U = 67%,
    H = 1,340 h/year.  The economic life is therefore 3 x 1,340 = 4,020 PMH.
    """
    return MachineSpec(
        machine_id="miyata_1980_example_11",
        capital_price_cad=46_790.0,
        economic_life_hours=3.0 * 1_340.0,
        scheduled_hours_per_year=2_000.0,
        utilization=0.67,
        interest_rate=0.12,
        insurance_tax_storage_rate=0.06,
        fuel_litres_per_pmh=0.0,
        fuel_price_cad_per_litre=0.0,
        lube_fraction_of_fuel=0.0,
        repair_fraction_of_depreciation=0.60,
        tire_fraction_of_depreciation=0.0,
        operator_wage_cad_per_smh=7.0,
        labour_burden_fraction=0.0,
        overhead_profit_fraction=0.0,
        salvage_fraction=0.20,
        fuel_lube_override_cad_per_pmh=2.97,
        tire_override_cad_per_pmh=2.68,
    )
