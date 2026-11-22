"""Source register, licence classifications and frozen StatCan observations.

Every numeric input is either an assumption owned by this model or a value from
a source whose licence permits commercial reuse and redistribution with
attribution.  The register is deliberately explicit so a reviewer can check the
strict-open claim without opening a PDF.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_OUT_DIR = (
    REPO_ROOT / "data/derived/national/harvest_basis/bc_open_cost_model_strict"
)
AREA_WEIGHTS_PATH = (
    REPO_ROOT / "data/derived/national/assortments/bc_area_weights/bc_area_weights.csv"
)
SLOPE_OVERLAY_PATH = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/slope_screen/"
    "slope_operable_overlay.csv"
)
ROAD_CLASSES_PATH = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/operable_proxy_v2/"
    "road_distance_classes.csv"
)
REFERENCE_ESTIMATES_PATH = (
    REPO_ROOT / "data/derived/national/harvest_basis/bc_open_cost_model/"
    "bc_open_cost_estimates.csv"
)

MODEL_VERSION = "1.0.0"
MODEL_ID = "bc_open_cost_model_strict"

EVIDENCE = "model_estimate"
COST_BASIS = "strict_open_proxy"
LICENCE_TIER = "strict_open"
OBSERVED = "false"
POLICY_GRADE = "false"
CALIBRATION_CLAIM = "none_bc_empirical"

STATCAN_LICENCE = "Statistics Canada Open Licence"
STATCAN_LICENCE_URL = "https://www.statcan.gc.ca/en/reference/licence"
OGL_BC_LICENCE = "Open Government Licence - British Columbia 2.0"
OGL_BC_LICENCE_URL = "https://www2.gov.bc.ca/gov/content/data/open-data/open-government-licence-bc"
OGL_CANADA_LICENCE = "Open Government Licence - Canada 2.0"
OGL_CANADA_LICENCE_URL = "https://open.canada.ca/en/open-government-licence-canada"
CC_BY_LICENCE = "Creative Commons Attribution 4.0 International (CC BY 4.0)"
CC_BY_LICENCE_URL = "https://creativecommons.org/licenses/by/4.0/"
PUBLIC_DOMAIN_LICENCE = "Public domain (U.S. Government work)"
PUBLIC_DOMAIN_LICENCE_URL = "https://www.usa.gov/government-works"

RETRIEVED_UTC = "2026-11-22T05:00:00+00:00"

SOURCE_REGISTER: tuple[dict[str, str], ...] = (
    {
        "source_key": "model_assumptions_cc0",
        "title": "Explicit engineering assumptions of the strict-open BC cost model",
        "publisher": "This model",
        "year": "2026",
        "licence": "CC0 1.0 Universal public-domain dedication",
        "licence_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "licence_tier": "public_domain",
        "url": "",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Capital prices, machine life, utilization, fuel burn, productivity cycle times, payload, and overhead assumptions.",
        "evidence_tier": "model_assumption",
        "redistributed": "true",
        "notes": "Assumptions are not observations and are not BC empirical calibration.",
    },
    {
        "source_key": "statcan_18100004_cpi",
        "title": "Consumer Price Index, monthly, not seasonally adjusted (Table 18-10-0004-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1810000401-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Converting 2021 and 2025 nominal wages to 2026 CAD; reporting the price base.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Open-licence aggregate; no microdata.",
    },
    {
        "source_key": "statcan_18100001_diesel",
        "title": "Monthly average retail prices for gasoline and fuel oil, by geography (Table 18-10-0001-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1810000101-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Diesel fuel price, Vancouver self-service, 12-month low/base/high range.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Retail pump price includes taxes; a conservative upper-bound fuel input.",
    },
    {
        "source_key": "statcan_14100064_wages_industry",
        "title": "Employee wages by industry, annual (Table 14-10-0064-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1410006401-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "BC and Canada average hourly wage, forestry/fishing/mining/quarrying/oil and gas grouping.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Broad industry grouping; used as the upper wage anchor, not as a logging-specific rate.",
    },
    {
        "source_key": "statcan_14100417_wages_occupation",
        "title": "Employee wages by occupation, annual (Table 14-10-0417-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1410041701-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "BC lower wage anchor: natural-resource production occupations; truck/transport operator wage.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Broad occupational groups, not a logging-machine-operator survey.",
    },
    {
        "source_key": "statcan_16100114_logging_industry",
        "title": "Logging industries, principal statistics by North American Industry Classification System (Table 16-10-0114-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1610011401-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Canada logging production-worker wages and production-worker count, 2021, for the central wage anchor.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "2022+ production-worker values are suppressed in the public table; 2021 is the latest complete year.",
    },
    {
        "source_key": "statcan_18100283_mepi_commodity",
        "title": "Machinery and equipment price index, by commodity, monthly (Table 18-10-0283-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1810028301-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Escalating 2017 capital-price assumptions to 2026 CAD for logging/construction machinery.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Index level; 2017=100 by table convention. July 2026 is the stable reference month.",
    },
    {
        "source_key": "statcan_18100284_mepi_industry",
        "title": "Machinery and equipment price index, by industry of purchase, monthly (Table 18-10-0284-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1810028401-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Cross-check on the commodity MEPI escalation for forestry and logging purchases.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Index level; 2017=100 by table convention.",
    },
    {
        "source_key": "statcan_18100281_trucking_ppi",
        "title": "For-hire motor carrier freight services price index, monthly (Table 18-10-0281-01)",
        "publisher": "Statistics Canada",
        "year": "2026",
        "licence": STATCAN_LICENCE,
        "licence_url": STATCAN_LICENCE_URL,
        "licence_tier": "statcan_open",
        "url": "https://doi.org/10.25318/1810028101-eng",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Plausibility check on nominal truck-haul cost growth; not a direct model input.",
        "evidence_tier": "official_statistic",
        "redistributed": "false",
        "notes": "Used only in the summary cross-check, not in the cost equations.",
    },
    {
        "source_key": "bc_vri_2025_area_weights",
        "title": "BC VRI 2025 forest and THLB area weights (derived aggregate)",
        "publisher": "Government of British Columbia",
        "year": "2025",
        "licence": OGL_BC_LICENCE,
        "licence_url": OGL_BC_LICENCE_URL,
        "licence_tier": "ogl_bc",
        "url": "https://www2.gov.bc.ca/gov/content/industry/forestry/managing-our-forest-resources/forest-inventory",
        "retrieved_utc": "2026-11-18T14:35:42+00:00",
        "used_for": "Forest area by ecoprovince and species-group composition; regional slope weighting.",
        "evidence_tier": "open_derived_aggregate",
        "redistributed": "derived_aggregate_only",
        "notes": "The raw VRI geodatabase is not redistributed; only the committed aggregate is read.",
    },
    {
        "source_key": "nrcan_mrdem_30_slope",
        "title": "NRCan MRDEM-30 terrain slope screen (derived 960 m aggregate)",
        "publisher": "Natural Resources Canada",
        "year": "2026",
        "licence": OGL_CANADA_LICENCE,
        "licence_url": OGL_CANADA_LICENCE_URL,
        "licence_tier": "ogl_canada",
        "url": "https://datacube.services.geo.ca/stac/api/collections/mrdem-30",
        "retrieved_utc": "2026-11-21T20:58:47+00:00",
        "used_for": "BC regional slope distributions and slope-class weights.",
        "evidence_tier": "open_derived_aggregate",
        "redistributed": "derived_aggregate_only",
        "notes": "960 m screening surface, not a 30 m operability classification.",
    },
    {
        "source_key": "nrcan_nrn_bc_roads",
        "title": "National Road Network, British Columbia (derived road-distance classes)",
        "publisher": "Natural Resources Canada",
        "year": "2026",
        "licence": OGL_CANADA_LICENCE,
        "licence_url": OGL_CANADA_LICENCE_URL,
        "licence_tier": "ogl_canada",
        "url": "https://www.nrn-rrn.ca/en/",
        "retrieved_utc": "2026-11-21T20:32:35+00:00",
        "used_for": "Context for the haul-distance scenario range; not a direct distance input.",
        "evidence_tier": "open_derived_aggregate",
        "redistributed": "derived_aggregate_only",
        "notes": "Public roads only; resource and winter roads are absent.",
    },
    {
        "source_key": "sessions_berry_han_2021",
        "title": "Machine rate estimates and equipment utilization - a modified approach",
        "publisher": "Croatian Journal of Forest Engineering",
        "year": "2021",
        "licence": CC_BY_LICENCE,
        "licence_url": CC_BY_LICENCE_URL,
        "licence_tier": "cc_by",
        "url": "https://doi.org/10.5552/crojfe.2021.1026",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Machine-rate equation, utilization-adjusted machine life, and worked example.",
        "evidence_tier": "peer_reviewed_method",
        "redistributed": "false",
        "notes": "Open-access CC BY article; equations and worked example are reproducible.",
    },
    {
        "source_key": "miyata_1980",
        "title": "Determining fixed and operating costs of logging equipment",
        "publisher": "USDA Forest Service, North Central Forest Experiment Station",
        "year": "1980",
        "licence": PUBLIC_DOMAIN_LICENCE,
        "licence_url": PUBLIC_DOMAIN_LICENCE_URL,
        "licence_tier": "public_domain",
        "url": "https://doi.org/10.2737/NC-GTR-55",
        "retrieved_utc": RETRIEVED_UTC,
        "used_for": "Original fixed/operating cost identities, average annual investment, and worked example.",
        "evidence_tier": "public_domain_method",
        "redistributed": "false",
        "notes": "General Technical Report NC-55; U.S. Government work.",
    },
)

SOURCE_BY_KEY = {source["source_key"]: source for source in SOURCE_REGISTER}

EXCLUDED_SOURCE_FAMILIES: tuple[str, ...] = (
    "BC Interior Appraisal Manual (IAM) and Coast Appraisal Manual (CAM) - BC Crown copyright",
    "TLA/ILA/NWLA/MNP Timber Harvest Cost Model Rate Guide - industry publisher copyright",
    "FPInnovations/FERIC reports and case studies - publisher copyright",
    "BC Wildfire Service (BCWS) rates - BC Crown copyright",
    "Any other Crown-copyright PDF, proprietary dataset, or restricted microdata",
)

FORBIDDEN_TOKENS: tuple[str, ...] = (
    "appraisal manual",
    "rate guide",
    "fpinnovations",
    "feric",
    "tla",
    "ila",
    "nwla",
    "bcws",
    "crown copyright",
    "confidential",
    "restricted",
    "ilcr",
    "cctmcs",
    "fpinterface",
)


@dataclass(frozen=True)
class Observation:
    """One frozen public statistic used by the model."""

    observation_id: str
    source_key: str
    series_id: str
    vector_id: str
    coordinate: str
    reference_period: str
    value: float
    unit: str
    used_for: str

    def __post_init__(self) -> None:
        if self.source_key not in SOURCE_BY_KEY:
            raise ValueError(f"unknown observation source: {self.source_key!r}")
        if not math.isfinite(self.value):
            raise ValueError(f"non-finite observation {self.observation_id}: {self.value!r}")
        if self.unit not in {"index", "CAD/hour", "CAD/litre", "CAD/year", "persons", "CAD"}:
            raise ValueError(f"unsupported observation unit: {self.unit!r}")

    def as_row(self) -> dict[str, str]:
        return {
            "observation_id": self.observation_id,
            "source_key": self.source_key,
            "series_id": self.series_id,
            "vector_id": self.vector_id,
            "coordinate": self.coordinate,
            "reference_period": self.reference_period,
            "value": f"{self.value:.6f}".rstrip("0").rstrip("."),
            "unit": self.unit,
            "used_for": self.used_for,
        }


def _obs(
    observation_id: str,
    source_key: str,
    series_id: str,
    vector_id: str,
    coordinate: str,
    period: str,
    value: float,
    unit: str,
    used_for: str,
) -> Observation:
    return Observation(
        observation_id=observation_id,
        source_key=source_key,
        series_id=series_id,
        vector_id=vector_id,
        coordinate=coordinate,
        reference_period=period,
        value=value,
        unit=unit,
        used_for=used_for,
    )


OBSERVATIONS: tuple[Observation, ...] = (
    # CPI, British Columbia, all-items, 2002=100.  Monthly values are frozen so
    # the build is deterministic and does not depend on network access.
    *(
        _obs(
            f"cpi_bc_{period}",
            "statcan_18100004_cpi",
            "cpi_bc_all_items",
            "v41692462",
            "26.2.0.0.0.0.0.0.0.0",
            period,
            value,
            "index",
            "wage deflation and 2026 price base",
        )
        for period, value in (
            ("2021-01-01", 133.6),
            ("2021-02-01", 134.1),
            ("2021-03-01", 134.9),
            ("2021-04-01", 135.2),
            ("2021-05-01", 135.1),
            ("2021-06-01", 135.8),
            ("2021-07-01", 136.7),
            ("2021-08-01", 137.0),
            ("2021-09-01", 137.2),
            ("2021-10-01", 137.9),
            ("2021-11-01", 138.1),
            ("2021-12-01", 138.0),
            ("2025-01-01", 156.0),
            ("2025-02-01", 157.6),
            ("2025-03-01", 157.8),
            ("2025-04-01", 157.8),
            ("2025-05-01", 159.0),
            ("2025-06-01", 158.8),
            ("2025-07-01", 159.1),
            ("2025-08-01", 159.0),
            ("2025-09-01", 158.8),
            ("2025-10-01", 159.4),
            ("2025-11-01", 159.5),
            ("2025-12-01", 158.7),
            ("2026-01-01", 159.1),
            ("2026-02-01", 160.3),
            ("2026-03-01", 161.7),
            ("2026-04-01", 161.7),
            ("2026-05-01", 163.6),
            ("2026-06-01", 163.3),
            ("2026-07-01", 163.7),
            ("2026-08-01", 163.8),
            ("2026-09-01", 163.5),
            ("2026-10-01", 163.4),
        )
    ),
    # Diesel, Vancouver, self-service, cents per litre.
    *(
        _obs(
            f"diesel_vancouver_{period}",
            "statcan_18100001_diesel",
            "diesel_vancouver_self_service",
            "v735140",
            "16.6.0.0.0.0.0.0.0.0",
            period,
            value,
            "CAD/litre",
            "fuel price low/base/high",
        )
        for period, value in (
            ("2025-11-01", 177.0),
            ("2025-12-01", 161.2),
            ("2026-01-01", 166.4),
            ("2026-02-01", 181.3),
            ("2026-03-01", 235.7),
            ("2026-04-01", 255.3),
            ("2026-05-01", 242.6),
            ("2026-06-01", 222.9),
            ("2026-07-01", 235.4),
            ("2026-08-01", 251.7),
            ("2026-09-01", 285.3),
            ("2026-10-01", 285.9),
        )
    ),
    # Wages, annual average hourly rate.
    _obs(
        "wage_bc_forestry_2025",
        "statcan_14100064_wages_industry",
        "wage_bc_forestry_group",
        "v2296256",
        "11.2.2.4.1.1.0.0.0.0",
        "2025-01-01",
        48.64,
        "CAD/hour",
        "high wage anchor",
    ),
    _obs(
        "wage_canada_forestry_2025",
        "statcan_14100064_wages_industry",
        "wage_canada_forestry_group",
        "v2196715",
        "1.2.2.4.1.1.0.0.0.0",
        "2025-01-01",
        51.50,
        "CAD/hour",
        "BC/Canada wage ratio",
    ),
    _obs(
        "wage_bc_natural_resources_2025",
        "statcan_14100417_wages_occupation",
        "wage_bc_natural_resources_production",
        "v1481598005",
        "11.2.2.55.1.1.0.0.0.0",
        "2025-01-01",
        28.37,
        "CAD/hour",
        "low wage anchor",
    ),
    _obs(
        "wage_bc_transport_operators_2025",
        "statcan_14100417_wages_occupation",
        "wage_bc_trades_transport_operators",
        "v1481597921",
        "11.2.2.48.1.1.0.0.0.0",
        "2025-01-01",
        36.89,
        "CAD/hour",
        "truck-driver wage anchor",
    ),
    _obs(
        "logging_production_wages_canada_2021",
        "statcan_16100114_logging_industry",
        "logging_production_wages_canada",
        "v110574102",
        "1.12.1.0.0.0.0.0.0.0",
        "2021-01-01",
        1_502_606_000.0,
        "CAD/year",
        "central wage anchor numerator",
    ),
    _obs(
        "logging_production_workers_canada_2021",
        "statcan_16100114_logging_industry",
        "logging_production_workers_canada",
        "v110574201",
        "1.15.1.0.0.0.0.0.0.0",
        "2021-01-01",
        25_911.0,
        "persons",
        "central wage anchor denominator",
    ),
    # MEPI and freight PPI.
    _obs(
        "mepi_logging_machinery_2026_07",
        "statcan_18100283_mepi_commodity",
        "mepi_logging_construction_mining_machinery",
        "v1567106368",
        "1.14.1.0.0.0.0.0.0.0",
        "2026-07-01",
        139.3,
        "index",
        "capital price escalation",
    ),
    _obs(
        "mepi_forestry_purchases_2026_07",
        "statcan_18100284_mepi_industry",
        "mepi_forestry_logging_purchases",
        "v1571476338",
        "1.5.1.0.0.0.0.0.0.0",
        "2026-07-01",
        134.2,
        "index",
        "capital price cross-check",
    ),
    _obs(
        "trucking_ppi_2025_06",
        "statcan_18100281_trucking_ppi",
        "truck_transportation_ppi",
        "v1490434186",
        "1.1.0.0.0.0.0.0.0.0",
        "2025-06-01",
        120.9,
        "index",
        "haul cost plausibility check",
    ),
    _obs(
        "trucking_ppi_2026_06",
        "statcan_18100281_trucking_ppi",
        "truck_transportation_ppi",
        "v1490434186",
        "1.1.0.0.0.0.0.0.0.0",
        "2026-06-01",
        131.9,
        "index",
        "haul cost plausibility check",
    ),
)


def _mean(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot average an empty observation series")
    return fmean(values)


def _cpi(prefix: str) -> float:
    values = [
        observation.value
        for observation in OBSERVATIONS
        if observation.reference_period.startswith(prefix)
        and observation.source_key == "statcan_18100004_cpi"
    ]
    if len(values) not in {10, 12}:
        raise ValueError(f"incomplete CPI year {prefix}: {len(values)} months")
    return _mean(values)


CPI_2021 = _cpi("2021")
CPI_2025 = _cpi("2025")
CPI_2026_YTD = _cpi("2026")

DIESEL_VALUES = [
    observation.value
    for observation in OBSERVATIONS
    if observation.source_key == "statcan_18100001_diesel"
]
if len(DIESEL_VALUES) != 12:
    raise ValueError("diesel series must contain 12 consecutive months")

# StatCan reports cents per litre; convert to CAD per litre.
DIESEL_BASE = _mean(DIESEL_VALUES) / 100.0
DIESEL_LOW = min(DIESEL_VALUES) / 100.0
DIESEL_HIGH = max(DIESEL_VALUES) / 100.0

WAGE_BC_FORESTRY_2025 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "wage_bc_forestry_2025"
)
WAGE_CANADA_FORESTRY_2025 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "wage_canada_forestry_2025"
)
WAGE_BC_NATURAL_RESOURCES_2025 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "wage_bc_natural_resources_2025"
)
WAGE_BC_TRANSPORT_2025 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "wage_bc_transport_operators_2025"
)
LOGGING_PRODUCTION_WAGES_CANADA_2021 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "logging_production_wages_canada_2021"
)
LOGGING_PRODUCTION_WORKERS_CANADA_2021 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "logging_production_workers_canada_2021"
)
MEPI_LOGGING_2026_07 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "mepi_logging_machinery_2026_07"
)
MEPI_FORESTRY_2026_07 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "mepi_forestry_purchases_2026_07"
)
TRUCKING_PPI_2025_06 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "trucking_ppi_2025_06"
)
TRUCKING_PPI_2026_06 = next(
    observation.value
    for observation in OBSERVATIONS
    if observation.observation_id == "trucking_ppi_2026_06"
)

# 2,000 paid hours per production worker per year is the Miyata (1980)
# scheduled-time convention (8 h/day x 250 days).  It is an explicit
# assumption, not an observed BC payroll value.
PAID_HOURS_PER_YEAR = 2_000.0

# Escalate the 2021 Canada logging production-worker annual wage to 2026 CAD.
LOGGING_PRODUCTION_HOURLY_2021 = (
    LOGGING_PRODUCTION_WAGES_CANADA_2021
    / LOGGING_PRODUCTION_WORKERS_CANADA_2021
    / PAID_HOURS_PER_YEAR
)
LOGGING_PRODUCTION_HOURLY_2026 = (
    LOGGING_PRODUCTION_HOURLY_2021 * CPI_2026_YTD / CPI_2021
)
BC_WAGE_PREMIUM = WAGE_BC_FORESTRY_2025 / WAGE_CANADA_FORESTRY_2025
LOGGING_WAGE_CENTRAL = LOGGING_PRODUCTION_HOURLY_2026 * BC_WAGE_PREMIUM

WAGE_LOW = WAGE_BC_NATURAL_RESOURCES_2025 * CPI_2026_YTD / CPI_2025
WAGE_HIGH = WAGE_BC_FORESTRY_2025 * CPI_2026_YTD / CPI_2025
WAGE_BASE = 0.5 * (LOGGING_WAGE_CENTRAL + WAGE_HIGH)
HAUL_WAGE_LOW = WAGE_BC_TRANSPORT_2025 * CPI_2026_YTD / CPI_2025
HAUL_WAGE_BASE = WAGE_BASE + 2.0
HAUL_WAGE_HIGH = WAGE_HIGH

# 2017=100 MEPI convention.  The capital prices below are 2017 replacement
# price assumptions, escalated to 2026 with the commodity MEPI.
MEPI_BASE_2017 = 100.0
MEPI_ESCALATION_2026 = MEPI_LOGGING_2026_07 / MEPI_BASE_2017
MEPI_INDUSTRY_ESCALATION_2026 = MEPI_FORESTRY_2026_07 / MEPI_BASE_2017
TRUCKING_PPI_YOY = TRUCKING_PPI_2026_06 / TRUCKING_PPI_2025_06 - 1.0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def input_hashes() -> dict[str, str]:
    """Return SHA-256 hashes for every open derived input read by the model."""
    paths = {
        "bc_vri_area_weights": AREA_WEIGHTS_PATH,
        "nrcan_mrdem_slope_overlay": SLOPE_OVERLAY_PATH,
        "nrcan_nrn_road_classes": ROAD_CLASSES_PATH,
    }
    hashes: dict[str, str] = {}
    for key, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"missing strict-open input: {path}")
        hashes[key] = _sha256(path)
    return hashes


def validate_source_register() -> None:
    """Fail closed if a restricted or non-strict source slips into the register."""
    for source in SOURCE_REGISTER:
        if source["licence_tier"] not in {
            "statcan_open",
            "ogl_bc",
            "ogl_canada",
            "cc_by",
            "public_domain",
        }:
            raise ValueError(f"non-strict licence tier: {source}")
        haystack = " ".join(
            source[key] for key in ("title", "publisher", "licence", "url", "notes")
        ).lower()
        for token in FORBIDDEN_TOKENS:
            if token in haystack:
                raise ValueError(
                    f"forbidden source token {token!r} in {source['source_key']}"
                )


def source_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for source in SOURCE_REGISTER:
        row = dict(source)
        row["source_licence_tier"] = source["licence_tier"]
        row["licence_tier"] = LICENCE_TIER
        row["evidence"] = "source_register"
        row["cost_basis"] = COST_BASIS
        row["policy_grade"] = POLICY_GRADE
        row["observed"] = OBSERVED
        rows.append(row)
    return rows


def observations_payload() -> list[dict[str, str]]:
    return [observation.as_row() for observation in OBSERVATIONS]


def generated_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def derived_constants_payload() -> dict[str, float | str]:
    """Return the derived numeric anchors so a reviewer can audit the chain."""
    return {
        "cpi_2021_annual_average": round(CPI_2021, 6),
        "cpi_2025_annual_average": round(CPI_2025, 6),
        "cpi_2026_ytd_average": round(CPI_2026_YTD, 6),
        "diesel_base_cad_per_litre": round(DIESEL_BASE, 6),
        "diesel_low_cad_per_litre": round(DIESEL_LOW, 6),
        "diesel_high_cad_per_litre": round(DIESEL_HIGH, 6),
        "logging_production_hourly_2021_cad": round(LOGGING_PRODUCTION_HOURLY_2021, 6),
        "logging_production_hourly_2026_cad": round(LOGGING_PRODUCTION_HOURLY_2026, 6),
        "bc_wage_premium": round(BC_WAGE_PREMIUM, 6),
        "logging_wage_central_cad_per_hour": round(LOGGING_WAGE_CENTRAL, 6),
        "wage_low_cad_per_hour": round(WAGE_LOW, 6),
        "wage_base_cad_per_hour": round(WAGE_BASE, 6),
        "wage_high_cad_per_hour": round(WAGE_HIGH, 6),
        "haul_wage_low_cad_per_hour": round(HAUL_WAGE_LOW, 6),
        "haul_wage_base_cad_per_hour": round(HAUL_WAGE_BASE, 6),
        "haul_wage_high_cad_per_hour": round(HAUL_WAGE_HIGH, 6),
        "mepi_logging_2026_07_index": MEPI_LOGGING_2026_07,
        "mepi_forestry_2026_07_index": MEPI_FORESTRY_2026_07,
        "mepi_escalation_2026": round(MEPI_ESCALATION_2026, 6),
        "mepi_industry_escalation_2026": round(MEPI_INDUSTRY_ESCALATION_2026, 6),
        "trucking_ppi_yoy": round(TRUCKING_PPI_YOY, 6),
        "paid_hours_per_year": PAID_HOURS_PER_YEAR,
        "mepi_base_year_index": MEPI_BASE_2017,
    }


def json_dumps(payload: object) -> str:
    return json.dumps(payload, indent=2, allow_nan=False) + "\n"
