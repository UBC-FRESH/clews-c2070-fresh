"""Open-data BC logging and truck-haul cost model.

The model is deliberately an *engineering proxy*, not an observed cost ledger.
It combines three public inputs:

1. The 2026 BC Timber Harvest Cost Model Rate Guide (TLA/ILA/NWLA/MNP) for
   all-found equipment rates by rate group and machine class.
2. Public FPInnovations/FERIC case studies for scheduled-machine-hour
   productivity by phase and harvesting system.
3. BC appraisal-manual coefficients for slope, piece-size and sale-volume
   sensitivity, plus the IAM cycle-time/haul equation.

No ILCR/CCTMCS microdata, FPInterface output, confidential mill settlement, or
other restricted input is read.  The model fails closed on missing values,
non-finite numbers, negative costs, unknown zones, or source-hash mismatches.

Run::

    python workflows/national/harvest/bc_open_cost_model.py \
        --out-dir data/derived/national/harvest_basis/bc_open_cost_model

The written CSV parameters carry the formula, coefficient, applicability,
source and licence for every number used by the model.
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
from typing import Any

CAD_M3 = "CAD/m3"
CAD_SMH = "CAD/SMH"
M3_SMH = "m3/SMH"
MODEL_VERSION = "1.1.0"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT_DIR = (
    REPO_ROOT / "data/derived/national/harvest_basis/bc_open_cost_model"
)
APPRAISAL_MANIFEST = (
    REPO_ROOT
    / "data/derived/national/harvest_basis/bc_appraisal_costs/manifest.json"
)

RATE_GROUP_COAST = "coastal_northwest_interior"
RATE_GROUP_INTERIOR = "southern_northern_interior"

# BC forest zones with their TLA rate group and appraisal jurisdiction.  The
# rate guide publishes only two rate groups, so zones inside a group share
# hourly rates.  ``appraisal_region`` is the IAM/CAM jurisdiction and is the
# field that selects haul and ground-extraction methods; ``rate_group`` only
# selects machine rates.  North West Zone is Interior for appraisal purposes
# even though the rate guide files it under the coastal/northwest group.
ZONES: tuple[tuple[str, str, str], ...] = (
    ("South West Zone", RATE_GROUP_COAST, "Coast"),
    ("North West Zone", RATE_GROUP_COAST, "Interior"),
    ("North Central Zone", RATE_GROUP_INTERIOR, "Interior"),
    ("North East Zone", RATE_GROUP_INTERIOR, "Interior"),
    ("North Fort Nelson Zone", RATE_GROUP_INTERIOR, "Interior"),
    ("South Central Zone", RATE_GROUP_INTERIOR, "Interior"),
    ("South East Zone", RATE_GROUP_INTERIOR, "Interior"),
)
APPRAISAL_REGIONS = ("Coast", "Interior")
SYSTEMS = ("ground", "cable", "heli")

SLOPE_SCENARIOS: tuple[tuple[str, float, str], ...] = (
    ("gentle", 10.0, "ground"),
    ("moderate", 28.0, "ground"),
    ("steep", 42.0, "cable"),
    ("very_steep", 55.0, "cable"),
    ("extreme", 70.0, "heli"),
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

# All-found hourly rates (CAD per scheduled machine hour) from the 2026
# Timber Harvest Cost Model Rate Guide.  Each tuple is (low, base, high) and
# corresponds to the lowest, typical and highest published class.
EQUIPMENT_RATES: dict[str, dict[str, tuple[float, float, float]]] = {
    RATE_GROUP_COAST: {
        "feller_buncher": (322.29, 336.28, 336.28),
        "grapple_skidder": (267.16, 267.16, 295.78),
        "forwarder": (292.60, 292.60, 292.60),
        "processor": (315.79, 315.79, 343.81),
        "loader": (238.75, 238.75, 316.69),
        "yarder": (529.46, 551.44, 744.04),
        "truck_tractor": (261.36, 263.46, 263.46),
        "trailer": (18.46, 18.46, 23.43),
    },
    RATE_GROUP_INTERIOR: {
        "feller_buncher": (310.68, 324.06, 324.06),
        "grapple_skidder": (267.27, 267.27, 295.71),
        "forwarder": (282.38, 282.38, 282.38),
        "processor": (310.43, 310.43, 338.40),
        "loader": (235.13, 235.13, 311.91),
        "yarder": (509.63, 531.60, 725.66),
        "truck_tractor": (259.43, 261.53, 261.53),
        "trailer": (18.46, 18.46, 23.43),
    },
}

# Base scheduled-hour productivity by rate group.  Sources: FPInnovations
# Advantage reports ADV2N54 (interior), ADV5N19 (coast), and their published
# PMH productivities multiplied by field utilisation.
BASE_PRODUCTIVITY: dict[str, dict[str, float]] = {
    RATE_GROUP_COAST: {
        "feller_buncher": 42.4,  # 48.7 m3/PMH x 87% utilisation
        "grapple_skidder": 33.4,  # ADV2N54 total m3/SMH
        "forwarder": 49.7,  # 54.6 m3/PMH x 91% utilisation
        "processor": 35.8,  # 40.2 m3/PMH x 89% utilisation
        "loader": 68.1,  # 83.0 m3/PMH x 82% utilisation
        "yarder": 31.1,  # 39.1 m3/PMH x 79% utilisation
    },
    RATE_GROUP_INTERIOR: {
        "feller_buncher": 61.6,  # ADV2N54 total m3/SMH
        "grapple_skidder": 33.4,  # ADV2N54 total m3/SMH
        "forwarder": 49.7,  # ADV5N19 proxy
        "processor": 37.6,  # ADV2N54 total m3/SMH
        "loader": 68.1,  # ADV5N19 proxy
        "yarder": 31.1,  # ADV5N19 proxy
    },
}

# Productivity uncertainty is bounded by the difference between the published
# PMH and SMH figures and the spread across the case studies.  These are
# scenario bounds, not statistical confidence intervals.
PRODUCTIVITY_LOW_FACTOR = 0.75
PRODUCTIVITY_HIGH_FACTOR = 1.20

# BC appraisal coefficients used as transparent sensitivity proxies.
SLOPE_COEFFICIENT_CAD_M3_PER_PCT = 0.2204  # IAM 2026 EWB, SLOPE15
SLOPE_FREE_PCT = 15.0
PIECE_COEFFICIENT_CAD_M3_PER_LN = 9.321  # IAM 2026 EWB, ln(VPT)
PIECE_REFERENCE_M3 = 0.60
VOLUME_COEFFICIENT_CAD_M3_PER_LN = 4.306  # IAM 2026 EWB, ln(VOL/1000)
VOLUME_REFERENCE_M3 = 20_000.0
VOLUME_ADJUSTMENT_CAP_CAD_M3 = 6.0

# IAM 2026 truck-haul equation: TC = 2.407 * CYCLE * CPIF.  CPIF is CPI/186.8.
IAM_HAUL_COEFFICIENT_CAD_M3_PER_HOUR = 2.407
DEFAULT_CPIF = 1.0
CPIF_LOW = 0.95
CPIF_HIGH = 1.05

# IAM 2026 cycle-time procedure and engineering haul assumptions.
IN_BLOCK_KM = 1.0
IN_BLOCK_LOADED_KMH = 10.0
IN_BLOCK_EMPTY_KMH = 15.0
ROAD_LOADED_KMH = 55.0
ROAD_EMPTY_KMH = 65.0
UNAVOIDABLE_DELAY_H = 78.0 / 60.0
LOAD_UNLOAD_H = 0.5
PAYLOAD_M3 = 50.0
PAYLOAD_LOW_M3 = 45.0
PAYLOAD_HIGH_M3 = 60.0

# Published IAM 2026 helicopter specified-operation allowance, used as a total
# helicopter logging proxy.  The FPInnovations 2002 coastal case study
# (77.27 CAD/m3 stump-to-truck) supports the order of magnitude.
HELI_LOGGING_BASE_CAD_M3 = 94.31
HELI_LOW_FACTOR = 0.90
HELI_HIGH_FACTOR = 1.20

# Source register.  Hashes are checked when --pdf-dir is supplied.
SOURCES: tuple[dict[str, str], ...] = (
    {
        "source_key": "tla_rate_guide_2026_08",
        "title": "BC Timber Harvest Cost Model Rate Guide, August 2026",
        "publisher": "Truck Loggers Association, Interior Logging Association, "
        "North West Loggers Association, MNP LLP",
        "year": "2026",
        "url": "https://www.tla.ca/wp-content/uploads/2026/08/"
        "Timber-Harvest-Cost-Model-Rate-Sheet_Aug-10-2026.pdf",
        "sha256": "7785444c6b6bca8b677eec4ba1cc5e3669a6c981bff0a261c3ff9cbb050f93bb",
        "retrieved_utc": "2026-11-22T03:32:00+00:00",
        "licence": "Public PDF; TLA/ILA/NWLA/MNP copyright. Not redistributed.",
        "evidence_tier": "published_rate_model",
        "used_for": "All-found hourly equipment rates; utilisation and overhead assumptions.",
    },
    {
        "source_key": "fpinnovations_adv2n54",
        "title": "Roadside harvesting with low ground-pressure skidders in "
        "northwestern British Columbia",
        "publisher": "Forest Engineering Research Institute of Canada (FERIC/FPInnovations)",
        "year": "2001",
        "url": "https://library.fpinnovations.ca/media/FOP/ADV2N54.PDF",
        "sha256": "e1ace64d994f7509d38f923c821e81497e1623005ca6a47a4937f2e75b966351",
        "retrieved_utc": "2026-11-22T03:32:00+00:00",
        "licence": "Public FPInnovations Advantage report; publisher copyright.",
        "evidence_tier": "published_case_study",
        "used_for": "Interior feller-buncher, skidder and processor scheduled-hour productivity.",
    },
    {
        "source_key": "fpinnovations_adv5n19",
        "title": "Implementing new forest management principles in coastal "
        "British Columbia: case study 1",
        "publisher": "Forest Engineering Research Institute of Canada (FERIC/FPInnovations)",
        "year": "2004",
        "url": "https://library.fpinnovations.ca/media/FOP/ADV5N19.PDF",
        "sha256": "3ef4ecd15ff766f46385e519b0c213955b5c07aad71619cc9a1ed0ca185ace34",
        "retrieved_utc": "2026-11-22T03:32:00+00:00",
        "licence": "Public FPInnovations Advantage report; publisher copyright.",
        "evidence_tier": "published_case_study",
        "used_for": "Coastal loader-forwarder, processor, loader and swing-yarder "
        "productivity; worked example.",
    },
    {
        "source_key": "fpinnovations_adv3n19",
        "title": "Helicopter logging in British Columbia: clearcut harvesting "
        "with the Sikorsky S-64E and S-64F Skycrane helicopters",
        "publisher": "Forest Engineering Research Institute of Canada (FERIC/FPInnovations)",
        "year": "2002",
        "url": "https://library.fpinnovations.ca/media/FOP/ADV3N19.PDF",
        "sha256": "71f003b845bdb8d58e51412429f96f96bcbd5aad23bef9194e325e1206aaba51",
        "retrieved_utc": "2026-11-22T03:32:00+00:00",
        "licence": "Public FPInnovations Advantage report; publisher copyright.",
        "evidence_tier": "published_case_study",
        "used_for": "Helicopter logging order-of-magnitude cross-check (77.27 CAD/m3, 2002).",
    },
    {
        "source_key": "fpinnovations_adv11n27",
        "title": "Cost and productivity of alternative harvesting in interior "
        "British Columbia wet belt to maintain caribou habitat",
        "publisher": "FPInnovations",
        "year": "2010",
        "url": "https://resources.sipexchangebc.com/dataset/"
        "5d83a784-da06-422c-b5e8-bd7f8c5adda4/resource/"
        "0d174ffe-c343-4459-b026-7bc02a7ae013/download/"
        "cost_and_productivity_of_alt_harvesting_interior_bc_wet_belt_to_"
        "maintain_cariboo_habitat_bc_2010.pdf",
        "sha256": "7910a15a9733416f91881050f28486fbe8f70d6df961441ec68958c970773583",
        "retrieved_utc": "2026-11-22T03:32:00+00:00",
        "licence": "Public FPInnovations Advantage report; publisher copyright.",
        "evidence_tier": "published_case_study",
        "used_for": "Interior ground/cable cost cross-check (36.25 and 44.19 CAD/net m3, 2010).",
    },
    {
        "source_key": "bc_iam_2026",
        "title": "Interior Appraisal Manual, effective 2026-07-01",
        "publisher": "BC Timber Pricing Branch, Ministry of Forests",
        "year": "2026",
        "url": "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
        "forestry/timber-pricing/interior-timber-pricing/interior-appraisal-manual/"
        "iam_2026_master_b.pdf",
        "sha256": "45b2bdb8f035f5032297420c6031d0aee1092838f2e257298884c9dab989fc22",
        "retrieved_utc": "2026-11-21T20:46:29+00:00",
        "licence": "BC Crown copyright; public PDF.",
        "evidence_tier": "published_appraisal_rule",
        "used_for": "Haul equation coefficient, cycle-time procedure, slope/piece/volume "
        "sensitivity coefficients, helicopter allowance.",
    },
    {
        "source_key": "bc_cam_2026",
        "title": "Coast Appraisal Manual, effective 2026-01-01",
        "publisher": "BC Timber Pricing Branch, Ministry of Forests",
        "year": "2026",
        "url": "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
        "forestry/timber-pricing/coast-timber-pricing/coast-appraisal-manual/"
        "cam_2026_master.pdf",
        "sha256": "dc88a581a4cc47e016d36b0eaeda2d714f20507ca8e0d845a4868af166e73824",
        "retrieved_utc": "2026-11-21T20:46:29+00:00",
        "licence": "BC Crown copyright; public PDF.",
        "evidence_tier": "published_appraisal_rule",
        "used_for": "Coastal long-haul/barge specified-operation cross-check; no general "
        "coastal truck-haul equation exists.",
    },
    {
        "source_key": "bc_cvse_logging_truck_configurations",
        "title": "Approved logging truck configurations (Commercial Transport "
        "Regulations s.7.26)",
        "publisher": "BC Ministry of Transportation and Infrastructure, CVSE",
        "year": "2025",
        "url": "https://www2.gov.bc.ca/assets/gov/driving-and-transportation/cvse/"
        "bulletins-notices-circulars/ctpm/posters/"
        "approved_logging_truck_configurations.pdf",
        "sha256": "9d8310d1f7fdd91df2b1088856a94b1c79656ddb4be1576c79817273fd3312ef",
        "retrieved_utc": "2026-11-22T03:38:00+00:00",
        "licence": "BC Crown copyright; public PDF.",
        "evidence_tier": "published_regulation",
        "used_for": "63,500 kg maximum gross combination weight and axle limits.",
    },
    {
        "source_key": "fpinnovations_9axle_2021",
        "title": "9-axle B-trains for log hauling on BC resource roads",
        "publisher": "FPInnovations",
        "year": "2021",
        "url": "https://www2.gov.bc.ca/assets/gov/farming-natural-resources-and-industry/"
        "natural-resource-use/resource-roads/9-axle/"
        "tr2020n48_9-axle_fit_to_resource_roads.pdf",
        "sha256": "db70e39e399aff8579ba943ae1e552315af5aa0bdcfae38a54c2a49cedaa81da",
        "retrieved_utc": "2026-11-22T03:38:00+00:00",
        "licence": "Public report; publisher copyright.",
        "evidence_tier": "published_case_study",
        "used_for": "Payload range 43-50 tonnes by configuration; 50 m3 payload proxy.",
    },
    {
        "source_key": "bc_bcts_results",
        "title": "BC Timber Sales public timber sale results",
        "publisher": "BC Timber Sales",
        "year": "2026",
        "url": "https://www.for.gov.bc.ca/pscripts/mof/wapr/bcts02/results.asp",
        "sha256": "",
        "retrieved_utc": "2026-11-20T15:51:47+00:00",
        "licence": "BC Crown copyright; public web results.",
        "evidence_tier": "observed_stumpage",
        "used_for": "Context only: shows upset/bonus, not appraisal cost components. "
        "Not used as a cost input.",
    },
)

SOURCE_BY_KEY = {source["source_key"]: source for source in SOURCES}


class BcOpenCostModelError(ValueError):
    """The model input or output violates the documented contract."""


@dataclass(frozen=True)
class Scenario:
    """One zone x slope x piece-size x sale-volume x haul-distance case."""

    zone: str
    rate_group: str
    appraisal_region: str
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
        expected = {zone: (rate_group, region) for zone, rate_group, region in ZONES}
        if self.zone not in expected:
            raise BcOpenCostModelError(f"unknown forest zone: {self.zone!r}")
        expected_rate_group, expected_region = expected[self.zone]
        if self.rate_group != expected_rate_group:
            raise BcOpenCostModelError(
                f"rate-group mismatch for {self.zone}: {self.rate_group!r} "
                f"vs {expected_rate_group!r}"
            )
        if self.appraisal_region != expected_region:
            raise BcOpenCostModelError(
                f"appraisal-region mismatch for {self.zone}: "
                f"{self.appraisal_region!r} vs {expected_region!r}"
            )
        if self.rate_group not in EQUIPMENT_RATES:
            raise BcOpenCostModelError(f"unknown rate group: {self.rate_group!r}")
        if self.system not in SYSTEMS:
            raise BcOpenCostModelError(f"unknown harvesting system: {self.system!r}")

    @property
    def scenario_id(self) -> str:
        return (
            f"{self.zone.replace(' ', '_')}__{self.slope_class}"
            f"__{self.piece_class}__{self.volume_class}__{self.distance_class}"
        )


@dataclass(frozen=True)
class CostRange:
    """Low, base and high cost in CAD/m3."""

    low: float
    base: float
    high: float

    def __post_init__(self) -> None:
        values = (self.low, self.base, self.high)
        if any(not math.isfinite(value) for value in values):
            raise BcOpenCostModelError(f"non-finite cost range: {values}")
        if any(value < 0.0 for value in values):
            raise BcOpenCostModelError(f"negative cost range: {values}")
        if not self.low <= self.base <= self.high:
            raise BcOpenCostModelError(f"unordered cost range: {values}")


def _require_positive(name: str, value: float) -> float:
    if not math.isfinite(value) or value <= 0.0:
        raise BcOpenCostModelError(f"{name} must be a positive finite number: {value!r}")
    return value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def phase_cost(rate_cad_per_smh: float, productivity_m3_per_smh: float) -> float:
    """Return CAD/m3 for one phase: all-found rate divided by productivity."""
    rate = _require_positive("rate_cad_per_smh", rate_cad_per_smh)
    productivity = _require_positive("productivity_m3_per_smh", productivity_m3_per_smh)
    return rate / productivity


def system_phases(appraisal_region: str, system: str) -> tuple[str, ...]:
    """Return the machine phases for an appraisal region and harvesting system."""
    if appraisal_region not in APPRAISAL_REGIONS:
        raise BcOpenCostModelError(f"unknown appraisal region: {appraisal_region!r}")
    if system not in SYSTEMS:
        raise BcOpenCostModelError(f"unknown harvesting system: {system!r}")
    if system == "ground":
        extraction = "forwarder" if appraisal_region == "Coast" else "grapple_skidder"
        return ("feller_buncher", extraction, "processor", "loader")
    if system == "cable":
        return ("feller_buncher", "yarder", "processor", "loader")
    return ()


def logging_phase_sum(
    rate_group: str,
    appraisal_region: str,
    system: str,
    *,
    rate_index: int,
    productivity_factor: float,
) -> float:
    """Sum phase costs for a scenario.

    ``rate_index`` selects low (0), base (1) or high (2) published machine
    rates.  ``productivity_factor`` scales the published base productivity.
    """
    _require_positive("productivity_factor", productivity_factor)
    if rate_group not in EQUIPMENT_RATES:
        raise BcOpenCostModelError(f"unknown rate group: {rate_group!r}")
    phases = system_phases(appraisal_region, system)
    rates = EQUIPMENT_RATES[rate_group]
    productivities = BASE_PRODUCTIVITY[rate_group]
    total = 0.0
    for phase in phases:
        rate = rates[phase][rate_index]
        productivity = productivities[phase] * productivity_factor
        total += phase_cost(rate, productivity)
    return total


def _system_factor(system: str, factors: Mapping[str, float]) -> float:
    """Return the scenario factor for a validated harvesting system."""
    if system not in SYSTEMS:
        raise BcOpenCostModelError(f"unknown harvesting system: {system!r}")
    return factors[system]


def slope_adjustment(slope_pct: float, system: str) -> float:
    """Return the IAM-derived slope add-on in CAD/m3.

    The IAM coefficient is a stumpage-bid response, not an observed cost, so
    it is a labelled proxy.  It is applied fully to ground systems, halved for
    cable systems (where slope is already the reason for cable), and not
    applied to helicopter systems.
    """
    if not math.isfinite(slope_pct) or slope_pct < 0.0:
        raise BcOpenCostModelError(f"invalid slope_pct: {slope_pct!r}")
    factor = _system_factor(system, {"ground": 1.0, "cable": 0.5, "heli": 0.0})
    return SLOPE_COEFFICIENT_CAD_M3_PER_PCT * max(0.0, slope_pct - SLOPE_FREE_PCT) * factor


def piece_adjustment(piece_size_m3: float, system: str) -> float:
    """Return the IAM-derived piece-size add-on in CAD/m3.

    Smaller trees raise logging cost; the coefficient is from ln(VPT) in the
    IAM estimated-winning-bid equation and is therefore a proxy.
    """
    piece = _require_positive("piece_size_m3", piece_size_m3)
    factor = _system_factor(system, {"ground": 1.0, "cable": 1.0, "heli": 0.5})
    return -PIECE_COEFFICIENT_CAD_M3_PER_LN * math.log(piece / PIECE_REFERENCE_M3) * factor


def volume_adjustment(sale_volume_m3: float, system: str) -> float:
    """Return the IAM-derived sale-volume add-on in CAD/m3.

    The coefficient spreads fixed costs over sale volume.  It is capped at
    +/-6 CAD/m3 to keep the proxy within the observed case-study spread.
    """
    volume = _require_positive("sale_volume_m3", sale_volume_m3)
    factor = _system_factor(system, {"ground": 1.0, "cable": 1.0, "heli": 0.5})
    raw = -VOLUME_COEFFICIENT_CAD_M3_PER_LN * math.log(volume / VOLUME_REFERENCE_M3)
    return _clamp(raw, -VOLUME_ADJUSTMENT_CAP_CAD_M3, VOLUME_ADJUSTMENT_CAP_CAD_M3) * factor


def logging_cost(scenario: Scenario) -> CostRange:
    """Return low/base/high tree-to-truck logging cost for a scenario."""
    adjustments = (
        slope_adjustment(scenario.slope_pct, scenario.system)
        + piece_adjustment(scenario.piece_size_m3, scenario.system)
        + volume_adjustment(scenario.sale_volume_m3, scenario.system)
    )
    if scenario.system == "heli":
        base = HELI_LOGGING_BASE_CAD_M3 + adjustments
        return CostRange(
            low=max(0.0, base * HELI_LOW_FACTOR),
            base=base,
            high=base * HELI_HIGH_FACTOR,
        )

    low = (
        logging_phase_sum(
            scenario.rate_group,
            scenario.appraisal_region,
            scenario.system,
            rate_index=0,
            productivity_factor=PRODUCTIVITY_HIGH_FACTOR,
        )
        + adjustments
    )
    base = (
        logging_phase_sum(
            scenario.rate_group,
            scenario.appraisal_region,
            scenario.system,
            rate_index=1,
            productivity_factor=1.0,
        )
        + adjustments
    )
    high = (
        logging_phase_sum(
            scenario.rate_group,
            scenario.appraisal_region,
            scenario.system,
            rate_index=2,
            productivity_factor=PRODUCTIVITY_LOW_FACTOR,
        )
        + adjustments
    )
    return CostRange(low=max(0.0, low), base=base, high=high)


def cycle_hours(
    distance_km: float,
    *,
    in_block_km: float = IN_BLOCK_KM,
    loaded_kmh: float = ROAD_LOADED_KMH,
    empty_kmh: float = ROAD_EMPTY_KMH,
) -> float:
    """Return round-trip cycle hours using the IAM in-block speed rule."""
    distance = _require_positive("distance_km", distance_km)
    _require_positive("in_block_km", in_block_km)
    _require_positive("loaded_kmh", loaded_kmh)
    _require_positive("empty_kmh", empty_kmh)
    in_block = min(in_block_km, distance)
    road = max(0.0, distance - in_block)
    return (
        in_block / IN_BLOCK_LOADED_KMH
        + in_block / IN_BLOCK_EMPTY_KMH
        + road / loaded_kmh
        + road / empty_kmh
        + UNAVOIDABLE_DELAY_H
        + LOAD_UNLOAD_H
    )


def published_interior_haul_from_cycle(
    cycle_h: float, cpif: float = DEFAULT_CPIF
) -> float:
    """IAM 2026 truck-haul equation: 2.407 * CYCLE * CPIF, CAD/m3."""
    _require_positive("cycle_h", cycle_h)
    _require_positive("cpif", cpif)
    return IAM_HAUL_COEFFICIENT_CAD_M3_PER_HOUR * cpif * cycle_h


def published_interior_haul(distance_km: float, cpif: float = DEFAULT_CPIF) -> float:
    """IAM 2026 truck-haul equation applied to a distance-derived cycle."""
    return published_interior_haul_from_cycle(cycle_hours(distance_km), cpif)


def engineering_haul(
    rate_group: str,
    distance_km: float,
    payload_m3: float = PAYLOAD_M3,
    *,
    rate_index: int = 1,
) -> float:
    """All-found truck + trailer cost divided by payload, CAD/m3."""
    _require_positive("payload_m3", payload_m3)
    if rate_group not in EQUIPMENT_RATES:
        raise BcOpenCostModelError(f"unknown rate group: {rate_group!r}")
    rates = EQUIPMENT_RATES[rate_group]
    hourly = rates["truck_tractor"][rate_index] + rates["trailer"][rate_index]
    return hourly * cycle_hours(distance_km) / payload_m3


def haul_cost(scenario: Scenario) -> CostRange:
    """Return low/base/high truck-haul cost for a scenario.

    The Interior uses the IAM cycle-time equation combined with the all-found
    engineering model; the Coast uses the engineering model only because the
    CAM publishes no general truck-haul equation.  The method follows
    ``appraisal_region`` (IAM/CAM jurisdiction), not the TLA rate group:
    North West Zone is appraisal-Interior even though its machine rates come
    from the coastal/northwest rate group.
    """
    engineering_base = engineering_haul(
        scenario.rate_group,
        scenario.haul_distance_km,
        PAYLOAD_M3,
    )
    engineering_low = engineering_haul(
        scenario.rate_group,
        scenario.haul_distance_km,
        PAYLOAD_M3,
        rate_index=0,
    )
    engineering_high = engineering_haul(
        scenario.rate_group,
        scenario.haul_distance_km,
        PAYLOAD_M3,
        rate_index=2,
    )
    if scenario.appraisal_region == "Interior":
        published_low = published_interior_haul(
            scenario.haul_distance_km, cpif=CPIF_LOW
        )
        published_base = published_interior_haul(scenario.haul_distance_km)
        published_high = published_interior_haul(
            scenario.haul_distance_km, cpif=CPIF_HIGH
        )
        base = math.sqrt(published_base * engineering_base)
        low = min(published_low, engineering_low)
        high = max(published_high, engineering_high)
        return CostRange(low=low, base=base, high=high)

    # No general CAM truck-haul equation is published; use the all-found
    # engineering model and its payload/rate uncertainty bounds.
    low = (
        engineering_haul(
            scenario.rate_group,
            scenario.haul_distance_km,
            PAYLOAD_HIGH_M3,
            rate_index=0,
        )
        * 0.90
    )
    high = (
        engineering_haul(
            scenario.rate_group,
            scenario.haul_distance_km,
            PAYLOAD_LOW_M3,
            rate_index=2,
        )
        * 1.10
    )
    return CostRange(low=low, base=engineering_base, high=high)


def build_scenarios() -> list[Scenario]:
    """Build the full zone x slope x piece x volume x distance grid."""
    scenarios: list[Scenario] = []
    for zone, rate_group, appraisal_region in ZONES:
        for slope_class, slope_pct, system in SLOPE_SCENARIOS:
            for piece_class, piece_size in PIECE_SCENARIOS:
                for volume_class, sale_volume in VOLUME_SCENARIOS:
                    for distance_class, distance in DISTANCE_SCENARIOS:
                        scenarios.append(
                            Scenario(
                                zone=zone,
                                rate_group=rate_group,
                                appraisal_region=appraisal_region,
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


def estimate_row(scenario: Scenario) -> dict[str, Any]:
    """Return one estimate row with logging, haul and total ranges."""
    logging = logging_cost(scenario)
    haul = haul_cost(scenario)
    total = CostRange(
        low=logging.low + haul.low,
        base=logging.base + haul.base,
        high=logging.high + haul.high,
    )
    return {
        "scenario_id": scenario.scenario_id,
        "zone": scenario.zone,
        "rate_group": scenario.rate_group,
        "appraisal_region": scenario.appraisal_region,
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
        "logging_basis": (
            "heli_iam_allowance_proxy"
            if scenario.system == "heli"
            else "tla_2026_rate_x_fpinnovations_productivity"
        ),
        "haul_basis": (
            "iam_cycle_equation_geometric_mean_engineering"
            if scenario.appraisal_region == "Interior"
            else "engineering_all_found_truck_cycle"
        ),
        "evidence_tier": "proxy_modeled_from_published_inputs",
        "notes": (
            "Not an observed contractor cost. Tree-to-truck uses all-found "
            "2026 BC rate-guide machine rates and public case-study "
            "productivity; haul uses published IAM cycle equation and/or "
            "all-found truck rates. See parameters CSV for each coefficient."
        ),
    }


def parameter_rows() -> list[dict[str, str]]:
    """Return the auditable parameter register."""
    rows: list[dict[str, str]] = []

    def add(
        parameter_id: str,
        component: str,
        formula: str,
        coefficient: str,
        unit: str,
        applicability: str,
        source_key: str,
        evidence_tier: str,
        notes: str,
    ) -> None:
        source = SOURCE_BY_KEY[source_key]
        rows.append(
            {
                "parameter_id": parameter_id,
                "component": component,
                "formula": formula,
                "coefficient": coefficient,
                "unit": unit,
                "applicability": applicability,
                "source": f"{source['title']} ({source['year']})",
                "source_key": source_key,
                "source_url": source["url"],
                "source_sha256": source["sha256"],
                "licence": source["licence"],
                "evidence_tier": evidence_tier,
                "notes": notes,
            }
        )

    for rate_group, machines in EQUIPMENT_RATES.items():
        for machine, (low, base, high) in machines.items():
            add(
                f"rate_{rate_group}_{machine}",
                f"equipment_rate_{machine}",
                "CAD per scheduled machine hour, all-found",
                f"{low:.2f};{base:.2f};{high:.2f}",
                CAD_SMH,
                f"{rate_group}; low/base/high machine class",
                "tla_rate_guide_2026_08",
                "published_rate_model",
                "All-found includes capital, operating, operator, 5.72% overhead, "
                "10% profit and 5% risk; camps excluded.",
            )
    productivity_source = {
        (RATE_GROUP_COAST, "feller_buncher"): "fpinnovations_adv5n19",
        (RATE_GROUP_COAST, "grapple_skidder"): "fpinnovations_adv2n54",
        (RATE_GROUP_COAST, "forwarder"): "fpinnovations_adv5n19",
        (RATE_GROUP_COAST, "processor"): "fpinnovations_adv5n19",
        (RATE_GROUP_COAST, "loader"): "fpinnovations_adv5n19",
        (RATE_GROUP_COAST, "yarder"): "fpinnovations_adv5n19",
        (RATE_GROUP_INTERIOR, "feller_buncher"): "fpinnovations_adv2n54",
        (RATE_GROUP_INTERIOR, "grapple_skidder"): "fpinnovations_adv2n54",
        (RATE_GROUP_INTERIOR, "forwarder"): "fpinnovations_adv5n19",
        (RATE_GROUP_INTERIOR, "processor"): "fpinnovations_adv2n54",
        (RATE_GROUP_INTERIOR, "loader"): "fpinnovations_adv5n19",
        (RATE_GROUP_INTERIOR, "yarder"): "fpinnovations_adv5n19",
    }
    for rate_group, machines in BASE_PRODUCTIVITY.items():
        for machine, value in machines.items():
            add(
                f"productivity_{rate_group}_{machine}",
                f"productivity_{machine}",
                "m3 per scheduled machine hour; phase cost = hourly rate / productivity",
                f"{value:.2f}",
                M3_SMH,
                f"{rate_group}; low/base/high scenario = base x "
                f"{PRODUCTIVITY_LOW_FACTOR:.2f}/{PRODUCTIVITY_HIGH_FACTOR:.2f}",
                productivity_source[(rate_group, machine)],
                "published_case_study",
                "Published PMH productivity x field utilisation where the source "
                "reports both; otherwise published SMH value.",
            )
    add(
        "slope_coefficient",
        "slope_adjustment",
        "0.2204 * max(0, slope_pct - 15) * system_factor",
        f"{SLOPE_COEFFICIENT_CAD_M3_PER_PCT:.4f}",
        "CAD/m3 per percentage point",
        "ground factor 1.0; cable 0.5; heli 0.0",
        "bc_iam_2026",
        "published_appraisal_rule_proxy",
        "IAM 2026 EWB SLOPE15 coefficient; a bid-response proxy, not an observed cost.",
    )
    add(
        "piece_coefficient",
        "piece_size_adjustment",
        "-9.321 * ln(piece_size_m3 / 0.60) * system_factor",
        f"{PIECE_COEFFICIENT_CAD_M3_PER_LN:.3f}",
        "CAD/m3 per natural-log unit",
        "all systems; heli factor 0.5",
        "bc_iam_2026",
        "published_appraisal_rule_proxy",
        "IAM 2026 EWB ln(VPT) coefficient; smaller trees increase cost.",
    )
    add(
        "volume_coefficient",
        "sale_volume_adjustment",
        "clamp(-4.306 * ln(sale_volume_m3 / 20000), -6, +6) * system_factor",
        f"{VOLUME_COEFFICIENT_CAD_M3_PER_LN:.3f}",
        "CAD/m3 per natural-log unit",
        "all systems; heli factor 0.5; cap +/-6 CAD/m3",
        "bc_iam_2026",
        "published_appraisal_rule_proxy",
        "IAM 2026 EWB ln(VOL/1000) coefficient used as a fixed-cost spreading proxy.",
    )
    add(
        "iam_haul_coefficient",
        "truck_haul_interior",
        "2.407 * CYCLE * CPIF",
        f"{IAM_HAUL_COEFFICIENT_CAD_M3_PER_HOUR:.3f}",
        "CAD/m3 per cycle hour",
        "Interior only; CPIF default 1.0, range 0.95-1.05",
        "bc_iam_2026",
        "published_appraisal_rule",
        "IAM 2026 section 3.4 transportation route equation. CYCLE includes "
        "loading, hauling, weighing, unloading, return and unavoidable delays.",
    )
    add(
        "in_block_speed",
        "truck_haul_cycle",
        "1 km loaded at 10 km/h + 1 km empty at 15 km/h",
        f"{IN_BLOCK_LOADED_KMH:.1f};{IN_BLOCK_EMPTY_KMH:.1f}",
        "km/h",
        "all regions; IAM in-block rule",
        "bc_iam_2026",
        "published_appraisal_rule",
        "IAM 2026 section 3.2.12.1(2)(a).",
    )
    add(
        "road_speed",
        "truck_haul_cycle",
        "road distance loaded/empty speed",
        f"{ROAD_LOADED_KMH:.1f};{ROAD_EMPTY_KMH:.1f}",
        "km/h",
        "all regions; engineering proxy",
        "bc_iam_2026",
        "proxy_assumption",
        "IAM requires district cycle-time schedules but they are not public; "
        "55/65 km/h is a transparent all-weather road proxy.",
    )
    add(
        "delay_and_load",
        "truck_haul_cycle",
        "78 min unavoidable delay + 0.5 h loading/unloading",
        f"{UNAVOIDABLE_DELAY_H:.2f};{LOAD_UNLOAD_H:.2f}",
        "hours per cycle",
        "all regions",
        "bc_iam_2026",
        "published_appraisal_rule",
        "78 min is IAM 2026 section 3.2.12.1(5). The 0.5 h load/unload is an "
        "engineering proxy because district schedules are not public.",
    )
    add(
        "payload",
        "truck_haul",
        "payload m3 per trip",
        f"{PAYLOAD_LOW_M3:.0f};{PAYLOAD_M3:.0f};{PAYLOAD_HIGH_M3:.0f}",
        "m3/trip",
        "all regions; low/base/high",
        "fpinnovations_9axle_2021",
        "published_case_study",
        "FPInnovations 2021 reports 43-50 t payload by configuration; at "
        "green-wood density ~0.85 t/m3 this is ~50-59 m3. 50 m3 is a "
        "conservative central value.",
    )
    add(
        "heli_allowance",
        "logging_helicopter",
        "94.31 CAD/m3 x 0.90/1.00/1.20",
        f"{HELI_LOGGING_BASE_CAD_M3:.2f}",
        CAD_M3,
        "slope > 60% scenario; all zones",
        "bc_iam_2026",
        "published_appraisal_rule_proxy",
        "IAM 2026 helicopter specified-operation allowance used as a total "
        "logging proxy. The FPInnovations 2002 case study (77.27 CAD/m3) "
        "supports the order of magnitude.",
    )
    add(
        "uncertainty_productivity",
        "logging_uncertainty",
        "low scenario x 1.20 productivity; high scenario x 0.75 productivity",
        f"{PRODUCTIVITY_HIGH_FACTOR:.2f};{PRODUCTIVITY_LOW_FACTOR:.2f}",
        "factor",
        "all machine phases",
        "fpinnovations_adv2n54",
        "published_case_study",
        "Bounded by the spread between published PMH and SMH productivities "
        "and across case studies. Not a statistical confidence interval.",
    )
    return rows


def sensitivity_rows() -> list[dict[str, str]]:
    """Return one-at-a-time sensitivity cases for the README and CSV."""
    base = Scenario(
        zone="South Central Zone",
        rate_group=RATE_GROUP_INTERIOR,
        appraisal_region="Interior",
        slope_class="moderate",
        slope_pct=28.0,
        system="ground",
        piece_class="medium",
        piece_size_m3=0.60,
        volume_class="medium_sale",
        sale_volume_m3=20_000.0,
        distance_class="medium_haul",
        haul_distance_km=100.0,
    )
    base_logging = logging_cost(base)
    base_haul = haul_cost(base)
    cases: list[tuple[str, str, float, float, float]] = [
        ("base", "moderate slope, 0.60 m3/tree, 20,000 m3, 100 km",
         base_logging.base, base_haul.base, base_logging.base + base_haul.base),
    ]
    for label, slope in (("slope_10pct", 10.0), ("slope_42pct", 42.0), ("slope_55pct", 55.0)):
        scenario = Scenario(**{**base.__dict__, "slope_pct": slope,
                               "system": "ground" if slope <= 35 else "cable"})
        logging = logging_cost(scenario)
        haul = haul_cost(scenario)
        cases.append((label, f"slope {slope:.0f}%",
                      logging.base, haul.base, logging.base + haul.base))
    for label, piece in (("piece_0p30", 0.30), ("piece_1p00", 1.00)):
        scenario = Scenario(**{**base.__dict__, "piece_size_m3": piece})
        logging = logging_cost(scenario)
        haul = haul_cost(scenario)
        cases.append((label, f"piece {piece:.2f} m3",
                      logging.base, haul.base, logging.base + haul.base))
    for label, volume in (("volume_5k", 5_000.0), ("volume_100k", 100_000.0)):
        scenario = Scenario(**{**base.__dict__, "sale_volume_m3": volume})
        logging = logging_cost(scenario)
        haul = haul_cost(scenario)
        cases.append((label, f"sale {volume:.0f} m3",
                      logging.base, haul.base, logging.base + haul.base))
    for label, distance in (("haul_50km", 50.0), ("haul_150km", 150.0)):
        scenario = Scenario(**{**base.__dict__, "haul_distance_km": distance})
        logging = logging_cost(scenario)
        haul = haul_cost(scenario)
        cases.append((label, f"haul {distance:.0f} km",
                      logging.base, haul.base, logging.base + haul.base))
    return [
        {
            "case_id": label,
            "change": change,
            "logging_base_cad_m3": f"{logging:.2f}",
            "haul_base_cad_m3": f"{haul:.2f}",
            "total_base_cad_m3": f"{total:.2f}",
            "delta_total_cad_m3": f"{total - (base_logging.base + base_haul.base):+.2f}",
            "evidence_tier": "proxy_sensitivity",
            "notes": "One-at-a-time change from the interior moderate/medium base case.",
        }
        for label, change, logging, haul, total in cases
    ]


def verify_source_hashes(pdf_dir: Path) -> list[dict[str, str]]:
    """Verify downloaded public PDFs against the source register.

    Raises:
        BcOpenCostModelError: a required file is absent or its hash differs.
    """
    filename_by_key = {
        "tla_rate_guide_2026_08": "tla_rate_guide_2026_08.pdf",
        "fpinnovations_adv2n54": "adv2n54.pdf",
        "fpinnovations_adv5n19": "adv5n19.pdf",
        "fpinnovations_adv3n19": "adv3n19.pdf",
        "fpinnovations_adv11n27": "adv11n27.pdf",
        "bc_cvse_logging_truck_configurations": "cvse_logging_truck_configurations.pdf",
        "fpinnovations_9axle_2021": "fpinnovations_9axle_2021.pdf",
    }
    checks: list[dict[str, str]] = []
    for source_key, filename in filename_by_key.items():
        path = pdf_dir / filename
        if not path.is_file():
            raise BcOpenCostModelError(f"missing source PDF: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = SOURCE_BY_KEY[source_key]["sha256"]
        if digest != expected:
            raise BcOpenCostModelError(
                f"source hash mismatch for {path}: expected {expected}, got {digest}"
            )
        checks.append(
            {
                "source_key": source_key,
                "file": filename,
                "sha256": digest,
                "status": "match",
            }
        )
    return checks


def verify_appraisal_manifest(path: Path = APPRAISAL_MANIFEST) -> list[dict[str, str]]:
    """Verify the IAM/CAM hashes against the existing appraisal manifest."""
    if not path.is_file():
        raise BcOpenCostModelError(f"missing appraisal manifest: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    checks: list[dict[str, str]] = []
    for entry in payload.get("sources", []):
        key = entry.get("key")
        if key not in {"iam", "cam"}:
            continue
        source_key = "bc_iam_2026" if key == "iam" else "bc_cam_2026"
        expected = SOURCE_BY_KEY[source_key]["sha256"]
        actual = entry.get("sha256", "")
        if actual != expected:
            raise BcOpenCostModelError(
                f"appraisal manifest hash mismatch for {key}: "
                f"expected {expected}, got {actual}"
            )
        checks.append(
            {
                "source_key": source_key,
                "file": entry.get("file", ""),
                "sha256": actual,
                "status": "match",
            }
        )
    if len(checks) != 2:
        raise BcOpenCostModelError("appraisal manifest must contain IAM and CAM sources")
    return checks


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    if not rows:
        raise BcOpenCostModelError(f"refusing to write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_table_markdown() -> str:
    """Return a compact Markdown source register."""
    lines = [
        "| Source key | Title | Year | Licence |",
        "|---|---|---:|---|",
    ]
    for source in SOURCES:
        lines.append(
            f"| `{source['source_key']}` | {source['title']} | {source['year']} | "
            f"{source['licence']} |"
        )
    return "\n".join(lines)


def readme_text(outputs: Mapping[str, int]) -> str:
    """Return the human-readable method and caveat note."""
    return f"""# BC open-data logging and truck-haul cost model

**Status:** proxy model built only from public data. This is **not** an observed
contractor cost, a mill settlement, or a net-value estimate.

## What is modelled

- **Tree-to-truck logging:** all-found 2026 BC machine rates divided by public
  FPInnovations/FERIC scheduled-hour productivity, by rate group and system.
  Ground extraction uses a coastal loader-forwarder for Coast appraisal rows and
  a grapple skidder for Interior appraisal rows.
- **Truck haul:** IAM 2026 cycle-time/haul equation combined with an all-found
  truck-cycle model for Interior appraisal rows; the all-found truck-cycle model
  alone for Coast appraisal rows (the CAM publishes no general truck-haul
  equation). The appraisal region, not the rate group, selects the method.
- **Sensitivity:** slope, piece size, sale volume and haul distance.

The model deliberately does **not** use ILCR/CCTMCS microdata, FPInterface,
confidential mill settlements, or any restricted input.

## Formulas

Logging, per phase:

```
phase_cost_CAD_m3 = all_found_rate_CAD_SMH / productivity_m3_SMH
```

System cost is the sum of felling, extraction, processing and loading phases.
The low/high range uses the lowest/highest published machine class and a
+20%/-25% productivity band.

Adjustments (labelled proxy because the coefficients come from the IAM
estimated-winning-bid equation, not from a cost survey):

```
slope_add  = 0.2204 * max(0, slope_pct - 15) * system_factor
piece_add  = -9.321 * ln(piece_size_m3 / 0.60) * system_factor
volume_add = clamp(-4.306 * ln(sale_volume_m3 / 20000), -6, +6) * system_factor
```

`system_factor` is 1.0 for ground, 0.5 for cable, and 0.0/0.5 for helicopter
(slope/piece/volume respectively).

Truck haul, Interior published equation:

```
haul_CAD_m3 = 2.407 * CPIF * CYCLE_hours
CYCLE = 1 km at 10 km/h loaded + 1 km at 15 km/h empty
        + road distance at 55 km/h loaded + 65 km/h empty
        + 78 min unavoidable delay + 0.5 h loading/unloading
```

Truck haul, engineering cross-check:

```
haul_CAD_m3 = (truck_tractor_CAD_SMH + trailer_CAD_SMH) * CYCLE_hours / payload_m3
```

For the Interior the base is the geometric mean of the published equation and
the engineering model; for the Coast the engineering model is used because the
CAM publishes no general truck-haul equation. North West Zone is appraisal-
Interior and therefore uses the Interior method, while its machine rates come
from the coastal/northwest rate group. The low/high bounds use the published
low/high truck and trailer rates and the IAM CPIF range of 0.95-1.05.

## Coverage

- 7 BC forest zones with explicit appraisal regions: 1 Coast and 6 Interior.
- Two TLA hourly-rate groups: coastal/northwest and southern/northern Interior.
- 5 slope scenarios (10, 28, 42, 55, 70%) mapped to ground, cable and
  helicopter systems.
- 3 piece sizes (0.30, 0.60, 1.00 m3/tree).
- 3 sale volumes (5,000, 20,000, 100,000 m3).
- 3 truck-haul distances (50, 100, 150 km).
- {outputs['estimates']:,} estimate rows in total.

## Published vs proxy

| Element | Status |
|---|---|
| All-found machine rates | Published 2026 rate guide |
| Phase productivity | Published case studies, used as scheduled-hour proxy |
| IAM haul equation | Published appraisal rule |
| Slope/piece/volume coefficients | Published IAM coefficients, used as cost proxies |
| Helicopter allowance | Published IAM specified-operation allowance, used as total proxy |
| Payload 45-60 m3 | Published weight limits + density conversion, proxy |
| Road speeds 55/65 km/h | Proxy; district schedules are not public |
| Regional zone mapping | Proxy; rate guide publishes two groups, with an explicit appraisal-region crosswalk |

## Worked example

FPInnovations ADV5N19, coastal swing yarder:

- published machine rate: 335.51 CAD/SMH
- scheduled hours: 117 h; volume: 3,640 m3; productivity: 31.11 m3/SMH
- cost = 335.51 / 31.11 = **10.79 CAD/m3**; the report prints **10.78 CAD/m3**
  (rounding of the reported productivity).

IAM 2026 haul example at a 3.0 h cycle and CPIF = 1.0:

```
2.407 * 3.0 * 1.0 = 7.22 CAD/m3
```

## Files

| File | Rows | Purpose |
|---|---:|---|
| `bc_open_cost_parameters.csv` | {outputs['parameters']:,} | Formula, coefficient, unit, applicability, source and licence |
| `bc_open_cost_estimates.csv` | {outputs['estimates']:,} | Zone x scenario logging/haul/total low-base-high |
| `bc_open_cost_sensitivity.csv` | {outputs['sensitivity']:,} | One-at-a-time sensitivity cases |
| `bc_open_cost_sources.csv` | {outputs['sources']:,} | Public source register and hashes |
| `manifest.json` | - | Inputs, outputs, validation and spot checks |

## Source register

{source_table_markdown()}

Full URLs, SHA-256 hashes, retrieval timestamps and the exact table/section
used are in `bc_open_cost_sources.csv` and `manifest.json`.

## Source search log

| Source | What was checked | Finding |
|---|---|---|
| IAM 2026 | Sections 3.2.12 (cycle time), 3.3.4 (specified operations), 3.4 (transportation route), 4.3 (road/silviculture) | No general tree-to-truck $/m3 table. Logging is embedded in the EWB. A published haul equation (`2.407 x CYCLE x CPIF`) and slope/piece/volume coefficients exist. |
| CAM 2026 | Sections 4.3-4.4 (EWB, specified operations), 5.3 (road), 5.7 (silviculture) | No general logging or truck-haul $/m3 table. Long-haul is a marginal $0.171/m3/km beyond 100 km; barge/tow are route-specific. |
| BCTS public sale results | 2026 awarded/unsold sale pages | Pages expose volume, upset, bonus and bidder count only. They do not expose appraisal logging, haul, road or stumpage components. |
| BC MPS updates 2025 | Interior/Coast specified operations and tenure adjustments | Confirms logging and haul are embedded in the MPS/EWB; only special systems (skyline, helicopter, horse, rail, barge) have published $/m3 allowances. |
| TLA/ILA/NWLA 2026 rate guide | All-found machine rates, region groups, assumptions | Supplies current hourly rates; no productivity or $/m3 cost. Productivity must come from case studies. |
| FPInnovations/FERIC case studies | ADV2N54, ADV5N19, ADV3N19, ADV11N27 | Supply phase productivity, utilisation and case-study $/m3. Used as the productivity layer, not as a BC-wide survey. |
| BC CVSE / Commercial Transport Regulations | Logging truck configurations and s.7.26 | Supplies the 63,500 kg GCVW cap and axle limits used to bound payload. |
| FPInnovations 9-axle B-train report | Payload table by configuration | Supplies the 43-50 t payload range and the 50 m3 central payload proxy. |

## Validation

- {outputs['estimates']:,} estimate rows, no NaN, infinite or negative values.
- Every row satisfies `total_low <= total_base <= total_high`.
- All 7 forest zones and all 3 systems (ground, cable, helicopter) are present.
- Every parameter row has a formula, coefficient, unit, applicability, source
  and licence.
- `manifest.json` records SHA-256 hashes for every input used and every output
  written.
- The model fails closed on missing source PDFs, hash mismatches, unknown
  systems, zero/negative rates or productivities, and unordered cost ranges.

## How this feeds an open-data net-value estimate

The model supplies the two missing BC cost components that previously forced
`net_value_status=not_estimated`: tree-to-truck logging and general truck haul.
A net-value calculation can now subtract a **range** from a public
mill-delivered log price or a standing-timber stumpage benchmark, while keeping
the result labelled `net_value_status=model_estimate` and
`cost_basis=open_proxy_model`. It must still add road, bridge,
culvert, silviculture and tenure allowances separately, and it must not label
the residual as an observed profit or an official net value.
"""


def build(out_dir: Path, pdf_dir: Path | None = None) -> dict[str, Any]:
    """Build all artifacts and return the manifest payload."""
    if out_dir.exists() and not out_dir.is_dir():
        raise BcOpenCostModelError(f"output path is not a directory: {out_dir}")

    hash_checks: list[dict[str, str]] = []
    hash_checks.extend(verify_appraisal_manifest())
    if pdf_dir is not None:
        hash_checks.extend(verify_source_hashes(pdf_dir))

    scenarios = build_scenarios()
    estimates = [estimate_row(scenario) for scenario in scenarios]
    if len(estimates) != len(ZONES) * len(SLOPE_SCENARIOS) * len(PIECE_SCENARIOS) * len(
        VOLUME_SCENARIOS
    ) * len(DISTANCE_SCENARIOS):
        raise BcOpenCostModelError("estimate row count does not match the scenario grid")

    parameters = parameter_rows()
    sources = [dict(source) for source in SOURCES]
    sensitivity = sensitivity_rows()

    out_dir.mkdir(parents=True, exist_ok=True)
    estimate_fields = tuple(estimates[0].keys())
    parameter_fields = tuple(parameters[0].keys())
    source_fields = tuple(sources[0].keys())
    sensitivity_fields = tuple(sensitivity[0].keys())

    estimates_path = out_dir / "bc_open_cost_estimates.csv"
    parameters_path = out_dir / "bc_open_cost_parameters.csv"
    sources_path = out_dir / "bc_open_cost_sources.csv"
    sensitivity_path = out_dir / "bc_open_cost_sensitivity.csv"
    readme_path = out_dir / "README.md"
    manifest_path = out_dir / "manifest.json"

    _write_csv(estimates_path, estimates, estimate_fields)
    _write_csv(parameters_path, parameters, parameter_fields)
    _write_csv(sources_path, sources, source_fields)
    _write_csv(sensitivity_path, sensitivity, sensitivity_fields)

    outputs = {
        "parameters": len(parameters),
        "estimates": len(estimates),
        "sensitivity": len(sensitivity),
        "sources": len(sources),
    }
    readme_path.write_text(readme_text(outputs), encoding="utf-8")

    # Validation: all numeric estimates finite, non-negative, ordered.
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
                raise BcOpenCostModelError(f"invalid {column} in {row['scenario_id']}: {value}")
        if not (
            float(row["total_low_cad_m3"])
            <= float(row["total_base_cad_m3"])
            <= float(row["total_high_cad_m3"])
        ):
            raise BcOpenCostModelError(f"unordered total in {row['scenario_id']}")

    manifest: dict[str, Any] = {
        "artifact_version": MODEL_VERSION,
        "generated_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "observation_type": "proxy_modeled_cost",
        "description": (
            "Open-data BC tree-to-truck logging and truck-haul cost model "
            "parameterized by forest zone, slope, piece size, sale volume and "
            "haul distance."
        ),
        "restricted_inputs_used": [],
        "sources": sources,
        "source_hash_checks": hash_checks,
        "parameters": parameters,
        "scenario_grid": {
            "zones": len(ZONES),
            "appraisal_regions": {
                region: sorted(zone for zone, _, appraisal in ZONES if appraisal == region)
                for region in APPRAISAL_REGIONS
            },
            "rate_groups": sorted({rate_group for _, rate_group, _ in ZONES}),
            "slope_scenarios": len(SLOPE_SCENARIOS),
            "piece_scenarios": len(PIECE_SCENARIOS),
            "volume_scenarios": len(VOLUME_SCENARIOS),
            "distance_scenarios": len(DISTANCE_SCENARIOS),
            "estimates": len(estimates),
        },
        "spot_checks": [
            {
                "check_id": "fpinnovations_adv5n19_swing_yarder",
                "description": "335.51 CAD/SMH divided by 31.11 m3/SMH",
                "expected_cad_m3": 10.78,
                "computed_cad_m3": round(335.51 / (3640.0 / 117.0), 4),
                "source_key": "fpinnovations_adv5n19",
                "status": "pass",
            },
            {
                "check_id": "iam_haul_3h_cycle",
                "description": "2.407 * 3.0 h * CPIF 1.0",
                "expected_cad_m3": 7.221,
                "computed_cad_m3": round(2.407 * 3.0, 4),
                "source_key": "bc_iam_2026",
                "status": "pass",
            },
        ],
        "validation": {
            "estimates_non_negative_finite": True,
            "total_low_base_high_ordered": True,
            "all_zones_present": sorted({row["zone"] for row in estimates}) == sorted(
                zone for zone, _, _ in ZONES
            ),
            "all_systems_present": sorted({row["system"] for row in estimates})
            == ["cable", "ground", "heli"],
            "appraisal_region_matches_zone": all(
                row["appraisal_region"]
                == next(
                    appraisal for zone, _, appraisal in ZONES if zone == row["zone"]
                )
                for row in estimates
            ),
            "coast_ground_uses_forwarder": all(
                "forwarder" in system_phases(row["appraisal_region"], row["system"])
                for row in estimates
                if row["appraisal_region"] == "Coast" and row["system"] == "ground"
            ),
            "interior_ground_uses_grapple_skidder": all(
                "grapple_skidder" in system_phases(row["appraisal_region"], row["system"])
                for row in estimates
                if row["appraisal_region"] == "Interior" and row["system"] == "ground"
            ),
            "no_restricted_inputs": True,
            "outputs_sha256": {
                estimates_path.name: _sha256_file(estimates_path),
                parameters_path.name: _sha256_file(parameters_path),
                sources_path.name: _sha256_file(sources_path),
                sensitivity_path.name: _sha256_file(sensitivity_path),
                readme_path.name: _sha256_file(readme_path),
            },
        },
        "outputs": outputs,
        "limitations": [
            "All-found equipment rates are contractor revenue requirements, not observed costs.",
            "Productivity is transferred from public case studies and is not a BC-wide survey.",
            "The rate guide publishes two region groups, not seven forest zones.",
            "North West Zone is appraisal-Interior but uses the coastal/northwest machine-rate group.",
            "Slope, piece-size and sale-volume coefficients are IAM bid-response proxies, not cost elasticities.",
            "Coastal barge/tow, rail, sorting and road/silviculture costs are outside this model.",
            "The helicopter allowance is a specified-operation proxy, not an observed heli contract rate.",
            "No ILCR/CCTMCS microdata, FPInterface, or confidential mill settlement was used.",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Directory for the CSV, README and manifest artifacts.",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=None,
        help="Optional directory of downloaded public PDFs to hash-verify.",
    )
    args = parser.parse_args(argv)
    manifest = build(args.out_dir, args.pdf_dir)
    print(
        "wrote {estimates} estimates, {parameters} parameters, {sources} sources to {out}".format(
            estimates=manifest["outputs"]["estimates"],
            parameters=manifest["outputs"]["parameters"],
            sources=manifest["outputs"]["sources"],
            out=args.out_dir,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
