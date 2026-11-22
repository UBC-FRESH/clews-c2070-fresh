"""National provincial economics v2 with explicit stumpage-scaled price proxies.

v2 keeps the v1 cost donors and changes only the price side.  For provinces with
a published stumpage, royalty or standing-value rate, the rate is multiplied by
the BC delivered-price / BCTS-stumpage ratio to form a **price proxy**.  The
proxy is never described as a delivered price or as observed revenue.  A second
price scenario keeps the flat BC donor from v1 as a sensitivity.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_v2
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.harvest import national_economics_v1 as v1

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
V1_DIR = HARVEST_BASIS_DIR / "national_economics_v1"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_economics_v2"

PROVINCES = v1.PROVINCES
PROVINCE_NAMES = v1.PROVINCE_NAMES
ALLOCATION_CSV = v1.PROVINCIAL_ALLOCATION_CSV
V1_MARGIN_CSV = V1_DIR / "national_margin_estimates.csv"

STUMPAGE_CSV = "provincial_stumpage_references.csv"
PRICE_CSV = "national_price_proxies.csv"
MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "v1_v2_comparison.csv"
ASSUMPTIONS_CSV = "assumptions_donor_register.csv"
SOURCES_CSV = "sources.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NATIONAL_ECONOMICS_V2_REPORT.md"
MANIFEST_JSON = "manifest.json"

PRICE_SCENARIO_BASE = "stumpage_scaled"
PRICE_SCENARIO_SENSITIVITY = "donor_flat"
PRICE_SCENARIOS = (PRICE_SCENARIO_BASE, PRICE_SCENARIO_SENSITIVITY)

PRICE_TIER_REPORTED = "reported_average"
PRICE_TIER_SCALED = "stumpage_scaled_proxy"
PRICE_TIER_DONOR = "donor_proxy"

RATE_TYPE_DUES = "timber_dues_rate"
RATE_TYPE_ROYALTY = "timber_royalty_rate"
RATE_TYPE_CHARGES = "crown_timber_charges_rate"
RATE_TYPE_STANDING = "standing_value_rate"
RATE_TYPE_SURVEY = "standing_timber_value_survey"
RATE_TYPE_BCTS = "bcts_average_stumpage_rate"
RATE_TYPE_FALLBACK = "no_provincial_rate_fallback_donor"

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"
STRICT_OPEN = "false"
RETRIEVED_UTC = "2026-11-22T06:12:59Z"

# BCTS FY2024/25 annual public performance report: Harvesting-Stumpage
# revenue $137.3M and 4.0M m3 harvested.  This is a published aggregate,
# not a transaction-level observation.
BCTS_STUMPAGE_REVENUE_CAD = 137_300_000.0
BCTS_HARVEST_M3 = 4_000_000.0
BCTS_STUMPAGE_RATE_CAD_M3 = BCTS_STUMPAGE_REVENUE_CAD / BCTS_HARVEST_M3

RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

LICENSING = {
    "classification": "public_reference_copyrighted",
    "strict_open_licence": False,
    "source_documents_redistributed": False,
    "statement": (
        "No strict-open claim is made. Provincial rate pages, PDFs and "
        "spreadsheets are public-reference copyrighted sources. Only extracted "
        "rates, URLs and hashes are redistributed; source documents are not."
    ),
}


class NationalEconomicsV2Error(ValueError):
    """Raised when an input or result violates the v2 contract."""


@dataclass(frozen=True)
class SourceRef:
    key: str
    title: str
    publisher: str
    publication_date: str
    url: str
    sha256: str
    licence: str
    evidence_tier: str


@dataclass(frozen=True)
class StumpageReference:
    province: str
    rate_low_cad_m3: float | None
    rate_base_cad_m3: float | None
    rate_high_cad_m3: float | None
    rate_type: str
    rate_basis: str
    source_key: str
    published_range: str
    extraction_method: str
    notes: str

    @property
    def extracted(self) -> bool:
        return self.rate_base_cad_m3 is not None


SOURCES: dict[str, SourceRef] = {
    "ab_timber_dues_2026_11": SourceRef(
        "ab_timber_dues_2026_11",
        "Timber dues rates - coniferous lumber, pulp and roundwood timber",
        "Government of Alberta",
        "2026-11",
        "https://www.alberta.ca/timber-dues-rates-coniferous-pulpwood-and-roundwood",
        "ab1a72a7aaa78da47099fcb20ebb538499fe9825bdf157b669c3f27280b0a135",
        "Public-reference Crown copyright; not strict-open.",
        "reported_rate",
    ),
    "sk_dues_q3_2026_27": SourceRef(
        "sk_dues_q3_2026_27",
        "2026-27 Quarter 3 Dues and Fees Table",
        "Government of Saskatchewan",
        "2026-Q3",
        "https://publications.saskatchewan.ca/api/v1/products/129953/formats/157443/download",
        "7ae0f271bb1f59a50f2b06aa82a6f28c4641cc88144761d71cf7aff362c4952f",
        "Public-reference Crown copyright; not strict-open.",
        "reported_rate",
    ),
    "mb_crown_timber_dues_2026_11": SourceRef(
        "mb_crown_timber_dues_2026_11",
        "Crown Timber Dues for November 2026",
        "Government of Manitoba",
        "2026-11",
        "https://www.gov.mb.ca/nrnd/forest/forestry/crown_timber/history/index.html",
        "e6bc049e0e56e6d5fa987c932078084301f2c61b5581315e7e6b0a6ef2b36d46",
        "Public-reference Crown copyright; not strict-open.",
        "reported_rate",
    ),
    "on_stumpage_matrix_2026_27": SourceRef(
        "on_stumpage_matrix_2026_27",
        "Stumpage matrix 2026-27, Period 8 (November 2026)",
        "Government of Ontario",
        "2026-11",
        "https://data.ontario.ca/dataset/crown-timber-charges-for-forestry-companies/resource/159952fe-22ad-4fb7-9067-8aec29964d74",
        "e2742b33c7928e2d29d229267373c8c5e5c510e892afea320292ff20dd012c7b",
        "Open Government Licence - Ontario; source file not redistributed here.",
        "reported_rate",
    ),
    "qc_tarification_2026_27": SourceRef(
        "qc_tarification_2026_27",
        "Taux unitaires de la valeur marchande des bois sur pied, novembre 2026",
        "Gouvernement du Quebec, MRNF",
        "2026-11",
        "https://cdn-contenu.quebec.ca/cdn-contenu/forets/documents/entreprises/recolte/tarification/2026-2027/GR_taux_bois_2026-2027.xlsx",
        "db1c7a8bf275a6f3c04026758421d05126040c34debff18c757aa3616c9a7c52",
        "Public-reference Government of Quebec; not strict-open.",
        "reported_rate",
    ),
    "nb_timber_royalty_2026_27": SourceRef(
        "nb_timber_royalty_2026_27",
        "Timber royalty rates 2026-2027",
        "Government of New Brunswick",
        "2026-11",
        "https://gnb.ca/en/topic/environment-resources/forestry-conservation/forest-products/timber-royalty-rates-26-27.html",
        "0e2d7b2d37091986e2b63e56beaa1dd8a2709dc6d55cb84c7d2e914c759128a7",
        "Public-reference Crown copyright; not strict-open.",
        "reported_rate",
    ),
    "ns_standing_timber_2024": SourceRef(
        "ns_standing_timber_2024",
        "Report on Prices of Standing Timber, July 2024",
        "Nova Scotia Department of Natural Resources and Renewables",
        "2024-07",
        "https://novascotia.ca/natr/forestry/registry/pdf/prices-standing-timber-2022.pdf",
        "5f1e6e24d7b0f4b40ec4140137c95657d7c94df3a2d027d8b192620f5664bdbb",
        "Public-reference Crown copyright; not strict-open.",
        "reported_survey",
    ),
    "bcts_annual_2024_25": SourceRef(
        "bcts_annual_2024_25",
        "BC Timber Sales 2024/25 Annual Public Performance Report",
        "BC Timber Sales, Ministry of Forests",
        "2025",
        "https://gov.bc.ca/assets/gov/farming-natural-resources-and-industry/forestry/bc-timber-sales/business-plans-performance-reports/fy2024_bcts_annual_public_performance_report.pdf",
        "a98c37050786d886fa217ba22dc756d9d76fe5b96fe1baa4c4479479a6bbf4e1",
        "Public-reference BC Crown copyright; not strict-open.",
        "reported_aggregate",
    ),
    "bc_log_market_2025_12": SourceRef(
        "bc_log_market_2025_12",
        "BC Coast and Interior log-market reports, 2025-12 3-month",
        "BC Timber Pricing Branch, Ministry of Forests",
        "2025-12",
        "https://www2.gov.bc.ca/gov/content/industry/forestry/competitive-forest-industry/timber-pricing",
        v1.sha256_file(v1.BC_LOG_MARKET_PRICES_CSV),
        "Public-reference BC Crown copyright; not strict-open.",
        "reported_market_average",
    ),
    "national_economics_v1": SourceRef(
        "national_economics_v1",
        "National economics v1 donor costs and BC price panel",
        "This repository",
        "2026-11-22",
        str(V1_DIR.relative_to(REPO_ROOT)),
        v1.sha256_file(V1_DIR / "summary.json"),
        "Repository-generated model artifacts; not strict-open.",
        "model_estimate",
    ),
    "provincial_allocation_119m": SourceRef(
        "provincial_allocation_119m",
        "119 Mt provincial harvest allocation",
        "This repository",
        "2026-11-22",
        str(ALLOCATION_CSV.relative_to(REPO_ROOT)),
        v1.sha256_file(ALLOCATION_CSV),
        "Repository-generated screening allocation; not a legal AAC.",
        "model_estimate",
    ),
    "bc_economic_state_2025": SourceRef(
        "bc_economic_state_2025",
        "2025 Economic State of British Columbia's Forest Sector",
        "BC Ministry of Forests",
        "2026",
        "https://gov.bc.ca/assets/gov/farming-natural-resources-and-industry/forestry/forest-industry-economics/economic-state/2025_economic_state_of_the_bc_forest_sector.pdf",
        "f0623975c71a23bd40c319a873b12c925fa6f6cc3903259689784773e8602043",
        "Public-reference BC Crown copyright; not strict-open.",
        "reported_aggregate",
    ),
}


def _references() -> dict[str, StumpageReference]:
    """Extracted provincial rates; values are CAD/m3 nominal, not delivered prices."""
    refs = {
        "BC": StumpageReference(
            "BC",
            BCTS_STUMPAGE_RATE_CAD_M3,
            BCTS_STUMPAGE_RATE_CAD_M3,
            BCTS_STUMPAGE_RATE_CAD_M3,
            RATE_TYPE_BCTS,
            "BCTS harvesting-stumpage revenue divided by BCTS harvested volume",
            "bcts_annual_2024_25",
            "137.3M CAD / 4.0M m3 = 34.325 CAD/m3",
            "Published FY2024/25 aggregate; no transaction-level data used.",
            "Used only as the denominator of the BC delivered-price/stumpage ratio.",
        ),
        "AB": StumpageReference(
            "AB",
            2.66,
            5.53,
            12.68,
            RATE_TYPE_DUES,
            "General coniferous lumber, pulp and roundwood timber dues (Gov AB + FRIAA)",
            "ab_timber_dues_2026_11",
            "Nov 2026 5.53; 12-month range 2.66-12.68 CAD/m3",
            "Monthly rate table; low/high are the published 12-month endpoints.",
            "General dues rate, not the small-operator rate. Not a delivered price.",
        ),
        "SK": StumpageReference(
            "SK",
            2.60,
            15.65,
            15.65,
            RATE_TYPE_DUES,
            "S1a white spruce/black spruce/lodgepole pine >15 cm dues, 2026-27",
            "sk_dues_q3_2026_27",
            "Q1 2.60; Q2 8.15; Q3 15.65 CAD/m3",
            "Q3 (Oct-Dec 2026) S1a dues; low is Q1, high is Q3.",
            "Forest Management Fund fee (7.47 CAD/m3) is excluded to avoid double counting the silviculture cost donor.",
        ),
        "MB": StumpageReference(
            "MB",
            4.58,
            6.58,
            6.58,
            RATE_TYPE_DUES,
            "Softwood lumber Crown timber dues, standard/medium/long distance",
            "mb_crown_timber_dues_2026_11",
            "Standard 6.58; medium 5.58; long 4.58 CAD/m3",
            "Published November 2026 standard rate; low is the long-distance rate.",
            "Standard FMU rate used; not a delivered price.",
        ),
        "ON": StumpageReference(
            "ON",
            6.06,
            6.75,
            6.75,
            RATE_TYPE_CHARGES,
            "Spruce/Jack Pine/Scots Pine/Balsam Fir/Larch sawmill Crown charges",
            "on_stumpage_matrix_2026_27",
            "Minimum price 6.06; subtotal Crown charges 6.75 CAD/m3",
            "Period 8 (November 2026) sawmill column; includes minimum price, Forest Resources Inventory and Forestry Futures Trust.",
            "Subtotal Crown charge used; not a delivered price.",
        ),
        "QC": StumpageReference(
            "QC",
            12.88,
            14.08,
            22.49,
            RATE_TYPE_STANDING,
            "Spruce sawlog (qualite B) standing-value rate, November 2026",
            "qc_tarification_2026_27",
            "p10 12.88; median 14.08; p90 22.49 CAD/m3 across 191 zones",
            "Median and p10/p90 of the November 2026 Epinettes B column across zones 151-993.",
            "Zone distribution used because a single provincial rate does not exist. Not a delivered price.",
        ),
        "NB": StumpageReference(
            "NB",
            24.03,
            25.05,
            27.06,
            RATE_TYPE_ROYALTY,
            "Spruce/fir/jack pine sawlog royalty, 2026-27 season",
            "nb_timber_royalty_2026_27",
            "24.03-27.06; Nov 1-28 25.05 CAD/m3",
            "Current Nov 1-28 column; low/high are the published season range.",
            "Crown royalty rate, not a delivered price.",
        ),
        "NS": StumpageReference(
            "NS",
            29.87,
            36.40,
            36.40,
            RATE_TYPE_SURVEY,
            "Softwood sawlog standing-timber market value (SPF)",
            "ns_standing_timber_2024",
            "SPF sawlogs 36.40; all-species sawables 29.87 CAD/m3",
            "Published July 2024 survey table; provincial weighted average.",
            "The survey is used by the province to set Crown stumpage; it is not an observed Crown sale or a delivered price.",
        ),
    }
    for province in ("PE", "NL", "YT", "NT", "NU"):
        refs[province] = StumpageReference(
            province,
            None,
            None,
            None,
            RATE_TYPE_FALLBACK,
            "No current provincial stumpage/dues series materialised",
            "",
            "not available",
            "Fallback donor; no numeric rate asserted.",
            (
                "PE/NL/YT/NT have no verified current commercial stumpage series in "
                "the source map. For NU the open.canada record cited by the research "
                "map is an Ontario data mirror, not a Nunavut rate, so it is not used. "
                "BC flat donor applies in both price scenarios."
            ),
        )
    return refs


def _ratio(blend: v1.BcPriceBlend) -> tuple[float, float, float]:
    if BCTS_HARVEST_M3 <= 0.0 or BCTS_STUMPAGE_REVENUE_CAD <= 0.0:
        raise NationalEconomicsV2Error("BCTS revenue and volume must be positive")
    denominator = BCTS_STUMPAGE_REVENUE_CAD / BCTS_HARVEST_M3
    return (
        blend.low_cad_m3 / denominator,
        blend.base_cad_m3 / denominator,
        blend.high_cad_m3 / denominator,
    )


def _fmt(value: float, digits: int = 4) -> str:
    if not math.isfinite(value):
        raise NationalEconomicsV2Error(f"non-finite value: {value}")
    return f"{value:.{digits}f}"


def _read_v1_base_margins() -> dict[str, dict[str, str]]:
    if not V1_MARGIN_CSV.is_file():
        raise NationalEconomicsV2Error(f"missing v1 margin artifact: {V1_MARGIN_CSV}")
    with V1_MARGIN_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["scenario_id"] == "base":
            result[row["province"]] = row
    missing = set(PROVINCES) - set(result)
    if missing:
        raise NationalEconomicsV2Error(f"v1 base margins missing: {sorted(missing)}")
    return result


def build_prices(
    refs: Mapping[str, StumpageReference],
    blend: v1.BcPriceBlend,
    ratio: tuple[float, float, float],
) -> list[dict[str, Any]]:
    ratio_low, ratio_base, ratio_high = ratio
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        reference = refs[province]
        for order, scenario in enumerate(PRICE_SCENARIOS):
            if province == "BC":
                low, base, high = (
                    blend.low_cad_m3,
                    blend.base_cad_m3,
                    blend.high_cad_m3,
                )
                tier = PRICE_TIER_REPORTED
                donor = ""
                method = "bc_reported_average_unchanged"
                basis = "mill_delivered_observed_report_average"
                note = (
                    "BC reported 2025-12 3-month mill-delivered average; the "
                    "stumpage-scaled method is not applied to BC itself."
                )
            elif scenario == PRICE_SCENARIO_BASE and reference.extracted:
                assert reference.rate_low_cad_m3 is not None
                assert reference.rate_base_cad_m3 is not None
                assert reference.rate_high_cad_m3 is not None
                low = reference.rate_low_cad_m3 * ratio_low
                base = reference.rate_base_cad_m3 * ratio_base
                high = reference.rate_high_cad_m3 * ratio_high
                tier = PRICE_TIER_SCALED
                donor = ""
                method = "stumpage_rate_times_bc_delivered_bcts_ratio"
                basis = "stumpage_scaled_delivered_price_proxy_not_observed"
                note = (
                    "Stumpage/dues/standing-value rate multiplied by the BC "
                    "delivered-price/BCTS-stumpage ratio. This is a synthetic "
                    "price proxy, NOT a delivered price and NOT observed revenue."
                )
            else:
                low, base, high = (
                    blend.low_cad_m3,
                    blend.base_cad_m3,
                    blend.high_cad_m3,
                )
                tier = PRICE_TIER_DONOR
                donor = "BC"
                method = "bc_flat_donor"
                basis = "donor_flat_delivered_proxy_not_observed"
                note = (
                    "Flat BC donor price for sensitivity or because no current "
                    "provincial stumpage rate is materialised. Not observed revenue."
                )
            if not low <= base <= high:
                raise NationalEconomicsV2Error(
                    f"unordered price proxy for {province}/{scenario}: {low}, {base}, {high}"
                )
            rows.append(
                {
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "price_scenario": scenario,
                    "scenario_order": order,
                    "price_low_cad_m3": _fmt(low),
                    "price_base_cad_m3": _fmt(base),
                    "price_high_cad_m3": _fmt(high),
                    "price_basis": basis,
                    "price_tier": tier,
                    "proxy_method": method,
                    "donor_province": donor,
                    "stumpage_rate_low_cad_m3": (
                        _fmt(reference.rate_low_cad_m3)
                        if reference.rate_low_cad_m3 is not None
                        else ""
                    ),
                    "stumpage_rate_base_cad_m3": (
                        _fmt(reference.rate_base_cad_m3)
                        if reference.rate_base_cad_m3 is not None
                        else ""
                    ),
                    "stumpage_rate_high_cad_m3": (
                        _fmt(reference.rate_high_cad_m3)
                        if reference.rate_high_cad_m3 is not None
                        else ""
                    ),
                    "stumpage_rate_unit": "CAD_nominal_2026_per_m3",
                    "stumpage_rate_type": reference.rate_type,
                    "stumpage_source_key": reference.source_key,
                    "stumpage_source_url": (
                        SOURCES[reference.source_key].url
                        if reference.source_key
                        else ""
                    ),
                    "bc_scaled_ratio_low": _fmt(ratio_low),
                    "bc_scaled_ratio_base": _fmt(ratio_base),
                    "bc_scaled_ratio_high": _fmt(ratio_high),
                    "bc_delivered_price_basis": "CAD_nominal_2025_per_m3",
                    "price_index": "1.0000",
                    "price_index_basis": (
                        "No provincial wage/diesel/price deflator applied; "
                        "rate and delivered-price years differ."
                    ),
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "net_value_status": NET_VALUE_STATUS,
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "strict_open": STRICT_OPEN,
                    "notes": note,
                }
            )
    return rows


def build_margins(
    prices: Sequence[Mapping[str, Any]],
    costs: Mapping[str, v1.CostProxy],
    allocation: Mapping[str, float],
    refs: Mapping[str, StumpageReference],
) -> list[dict[str, Any]]:
    by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        cost = costs[province]
        for price_scenario in PRICE_SCENARIOS:
            price = by_key[(province, price_scenario)]
            for scenario, order, price_col, cost_kind, note in (
                (
                    "low",
                    0,
                    "price_low_cad_m3",
                    "high",
                    "Conservative envelope: low price proxy minus high cost.",
                ),
                (
                    "base",
                    1,
                    "price_base_cad_m3",
                    "base",
                    "Central proxy case.",
                ),
                (
                    "high",
                    2,
                    "price_high_cad_m3",
                    "low",
                    "Optimistic envelope: high price proxy minus low cost.",
                ),
            ):
                price_value = float(price[price_col])
                logging_haul = getattr(cost, f"logging_haul_{cost_kind}_cad_m3")
                road = getattr(cost, f"road_{cost_kind}_cad_m3")
                silviculture = getattr(cost, f"silviculture_{cost_kind}_cad_m3")
                total_cost = logging_haul + road + silviculture
                margin = price_value - total_cost
                if not math.isfinite(margin):
                    raise NationalEconomicsV2Error(
                        f"non-finite margin for {province}/{price_scenario}/{scenario}"
                    )
                rows.append(
                    {
                        "province": province,
                        "province_name": PROVINCE_NAMES[province],
                        "price_scenario": price_scenario,
                        "scenario_id": scenario,
                        "scenario_order": order,
                        "price_cad_m3": _fmt(price_value),
                        "logging_haul_cad_m3": _fmt(logging_haul),
                        "road_cad_m3": _fmt(road),
                        "silviculture_cad_m3": _fmt(silviculture),
                        "total_cost_cad_m3": _fmt(total_cost),
                        "margin_cad_m3": _fmt(margin),
                        "margin_formula": (
                            f"{price_col} - logging_haul_{cost_kind} - "
                            f"road_{cost_kind} - silviculture_{cost_kind}"
                        ),
                        "margin_status": (
                            "negative_documented" if margin < 0.0 else "non_negative"
                        ),
                        "price_tier": price["price_tier"],
                        "price_basis": price["price_basis"],
                        "proxy_method": price["proxy_method"],
                        "stumpage_rate_type": refs[province].rate_type,
                        "logging_haul_tier": cost.logging_haul_tier,
                        "road_tier": cost.road_tier,
                        "silviculture_tier": cost.silviculture_tier,
                        "cost_tier": cost.cost_tier,
                        "evidence": EVIDENCE,
                        "observed": OBSERVED,
                        "policy_grade": POLICY_GRADE,
                        "net_value_status": NET_VALUE_STATUS,
                        "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                        "allocation_volume_m3": _fmt(allocation[province]),
                        "volume_weighted_margin_cad_m3": _fmt(
                            margin * allocation[province]
                        ),
                        "negative_margin_clipped": "false",
                        "notes": note,
                    }
                )
    return rows


def build_comparison(
    prices: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    refs: Mapping[str, StumpageReference],
) -> list[dict[str, Any]]:
    v1_base = _read_v1_base_margins()
    base_margins = {
        row["province"]: row
        for row in margins
        if row["price_scenario"] == PRICE_SCENARIO_BASE
        and row["scenario_id"] == "base"
    }
    sensitivity = {
        row["province"]: row
        for row in margins
        if row["price_scenario"] == PRICE_SCENARIO_SENSITIVITY
        and row["scenario_id"] == "base"
    }
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        old = v1_base[province]
        new = base_margins[province]
        flat = sensitivity[province]
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "stumpage_rate_type": refs[province].rate_type,
                "stumpage_extracted": str(refs[province].extracted).lower(),
                "v1_price_base_cad_m3": old["price_cad_m3"],
                "v1_margin_base_cad_m3": old["margin_cad_m3"],
                "v2_stumpage_scaled_price_base_cad_m3": new["price_cad_m3"],
                "v2_stumpage_scaled_margin_base_cad_m3": new["margin_cad_m3"],
                "v2_donor_flat_margin_base_cad_m3": flat["margin_cad_m3"],
                "price_delta_cad_m3": _fmt(
                    float(new["price_cad_m3"]) - float(old["price_cad_m3"])
                ),
                "margin_delta_cad_m3": _fmt(
                    float(new["margin_cad_m3"]) - float(old["margin_cad_m3"])
                ),
                "v1_price_tier": old["price_tier"],
                "v2_price_tier": new["price_tier"],
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "notes": (
                    "BC unchanged; v2 applies the stumpage-scaled proxy to "
                    "jurisdictions with an extracted rate."
                    if province == "BC"
                    else "Price change is a method change, not an observed market change."
                ),
            }
        )
    return rows


def _volume_reference() -> dict[str, dict[str, str]]:
    if not ALLOCATION_CSV.is_file():
        raise NationalEconomicsV2Error(f"missing allocation table: {ALLOCATION_CSV}")
    with ALLOCATION_CSV.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result = {row["province"]: row for row in rows}
    missing = set(PROVINCES) - set(result)
    if missing:
        raise NationalEconomicsV2Error(
            f"allocation table missing provinces: {sorted(missing)}"
        )
    return result


def build_summary_rows(
    margins: Sequence[Mapping[str, Any]],
    allocation: Mapping[str, float],
    prices: Sequence[Mapping[str, Any]],
    refs: Mapping[str, StumpageReference],
    volume_reference: Mapping[str, Mapping[str, str]],
) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], Mapping[str, Any]] = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsV2Error("allocation volume sum is zero")
    base_price = {
        row["province"]: row
        for row in prices
        if row["price_scenario"] == PRICE_SCENARIO_BASE
    }
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        low = float(
            by_key[(province, PRICE_SCENARIO_BASE, "low")]["margin_cad_m3"]
        )
        base = float(
            by_key[(province, PRICE_SCENARIO_BASE, "base")]["margin_cad_m3"]
        )
        high = float(
            by_key[(province, PRICE_SCENARIO_BASE, "high")]["margin_cad_m3"]
        )
        flat_base = float(
            by_key[(province, PRICE_SCENARIO_SENSITIVITY, "base")]["margin_cad_m3"]
        )
        rows.append(
            {
                "row_type": "province",
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "allocation_volume_m3": _fmt(allocation[province]),
                "volume_share": _fmt(allocation[province] / total_volume, 8),
                "volume_denominator_basis": (
                    "119 Mt provincial screening allocation; zero commercial "
                    "harvest asserted for NU"
                ),
                "observed_2024_volume_m3": volume_reference[province][
                    "observed_2024_m3"
                ],
                "nfd_supply_m3": volume_reference[province]["nfd_m3"],
                "documented_aac_m3": volume_reference[province]["aac_m3"],
                "volume_reference_basis": volume_reference[province]["basis"],
                "price_base_cad_m3": base_price[province]["price_base_cad_m3"],
                "price_basis": base_price[province]["price_basis"],
                "price_tier": base_price[province]["price_tier"],
                "stumpage_rate_type": refs[province].rate_type,
                "total_cost_base_cad_m3": by_key[
                    (province, PRICE_SCENARIO_BASE, "base")
                ]["total_cost_cad_m3"],
                "margin_low_cad_m3": _fmt(low),
                "margin_base_cad_m3": _fmt(base),
                "margin_high_cad_m3": _fmt(high),
                "donor_flat_margin_base_cad_m3": _fmt(flat_base),
                "base_minus_donor_flat_cad_m3": _fmt(base - flat_base),
                "margin_status": (
                    "base_negative_documented"
                    if base < 0.0
                    else "low_negative_documented"
                    if low < 0.0
                    else "non_negative"
                ),
                "cost_tier": by_key[
                    (province, PRICE_SCENARIO_BASE, "base")
                ]["cost_tier"],
                "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "notes": (
                    "Zero allocation volume; proxy only and no commercial "
                    "harvest asserted."
                    if allocation[province] == 0.0
                    else "Allocation-volume weight from the 119 Mt case."
                ),
            }
        )

    def weighted_margin(scenario: str, price_scenario: str) -> float:
        return (
            sum(
                float(by_key[(p, price_scenario, scenario)]["margin_cad_m3"])
                * allocation[p]
                for p in PROVINCES
            )
            / total_volume
        )

    weighted_price = (
        sum(
            float(base_price[p]["price_base_cad_m3"]) * allocation[p]
            for p in PROVINCES
        )
        / total_volume
    )
    weighted_cost = (
        sum(
            float(
                by_key[(p, PRICE_SCENARIO_BASE, "base")]["total_cost_cad_m3"]
            )
            * allocation[p]
            for p in PROVINCES
        )
        / total_volume
    )
    rows.append(
        {
            "row_type": "national",
            "province": "NATIONAL",
            "province_name": "Canada",
            "allocation_volume_m3": _fmt(total_volume),
            "volume_share": "1.00000000",
            "volume_denominator_basis": (
                "119 Mt provincial screening allocation; national volume-weighted "
                "diagnostic only"
            ),
            "observed_2024_volume_m3": _fmt(
                sum(float(row["observed_2024_m3"]) for row in volume_reference.values())
            ),
            "nfd_supply_m3": _fmt(
                sum(
                    float(row["nfd_m3"])
                    for row in volume_reference.values()
                    if row["nfd_m3"]
                )
            ),
            "documented_aac_m3": _fmt(
                sum(
                    float(row["aac_m3"])
                    for row in volume_reference.values()
                    if row["aac_m3"]
                )
            ),
            "volume_reference_basis": (
                "sum of provincial 2024 observed volume, NFD supply and documented "
                "AAC where published; not a legal national AAC"
            ),
            "price_base_cad_m3": _fmt(weighted_price),
            "price_basis": "mixed_reported_average_and_stumpage_scaled_proxy",
            "price_tier": "mixed",
            "stumpage_rate_type": "mixed",
            "total_cost_base_cad_m3": _fmt(weighted_cost),
            "margin_low_cad_m3": _fmt(weighted_margin("low", PRICE_SCENARIO_BASE)),
            "margin_base_cad_m3": _fmt(weighted_margin("base", PRICE_SCENARIO_BASE)),
            "margin_high_cad_m3": _fmt(weighted_margin("high", PRICE_SCENARIO_BASE)),
            "donor_flat_margin_base_cad_m3": _fmt(
                weighted_margin("base", PRICE_SCENARIO_SENSITIVITY)
            ),
            "base_minus_donor_flat_cad_m3": _fmt(
                weighted_margin("base", PRICE_SCENARIO_BASE)
                - weighted_margin("base", PRICE_SCENARIO_SENSITIVITY)
            ),
            "margin_status": "model_estimate_not_policy",
            "cost_tier": "mixed_proxy",
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            "evidence": EVIDENCE,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "notes": (
                "Volume-weighted across all 13 jurisdictions using the 119 Mt "
                "allocation. Model estimate, not a policy result and not a "
                "combined BC+QC-only total."
            ),
        }
    )
    return rows


def build_assumptions(ratio: tuple[float, float, float]) -> list[dict[str, Any]]:
    return [
        {
            "assumption_id": "stumpage_rate_semantics",
            "category": "price",
            "applies_to": "all",
            "rule": (
                "Extracted provincial values are stumpage/dues/standing-value "
                "rates, not delivered prices and not observed revenue."
            ),
            "value": "semantic_label",
            "unit": "text",
            "source": "provincial rate sources",
            "tier": "documented_constraint",
            "notes": "No row labels stumpage as a delivered price.",
        },
        {
            "assumption_id": "bc_scaled_ratio",
            "category": "price",
            "applies_to": "non_BC_with_rate",
            "rule": (
                "Multiply provincial stumpage by the BC blended delivered price "
                "divided by the BCTS average stumpage rate."
            ),
            "value": f"{ratio[0]:.4f}/{ratio[1]:.4f}/{ratio[2]:.4f}",
            "unit": "ratio_low_base_high",
            "source": "bcts_annual_2024_25 + bc_log_market_2025_12",
            "tier": "model_estimate",
            "notes": (
                "137.3M CAD / 4.0M m3 = 34.325 CAD/m3. The ratio transfers BC's "
                "cost-plus-margin structure; it is not a provincial cost survey."
            ),
        },
        {
            "assumption_id": "stumpage_scaled_price_proxy",
            "category": "price",
            "applies_to": "BC,AB,SK,MB,ON,QC,NB,NS",
            "rule": "preferred base scenario price proxy",
            "value": "stumpage_scaled",
            "unit": "scenario",
            "source": "this model",
            "tier": "model_estimate",
            "notes": "Labelled stumpage_scaled_proxy; not observed and not policy grade.",
        },
        {
            "assumption_id": "donor_flat_sensitivity",
            "category": "price",
            "applies_to": "AB,SK,MB,ON,QC,NB,NS,NL,PE,YT,NT,NU",
            "rule": "flat BC donor price as sensitivity and fallback",
            "value": "donor_flat",
            "unit": "scenario",
            "source": "national_economics_v1",
            "tier": "donor_proxy",
            "notes": "Used for PE/NL/YT/NT/NU because no current rate is materialised.",
        },
        {
            "assumption_id": "fallback_jurisdictions",
            "category": "price",
            "applies_to": "PE,NL,YT,NT,NU",
            "rule": "BC flat donor only",
            "value": "5",
            "unit": "jurisdictions",
            "source": "research source map",
            "tier": "donor_proxy",
            "notes": (
                "NU open.canada record is an Ontario mirror and is not treated as "
                "a Nunavut rate."
            ),
        },
        {
            "assumption_id": "qc_reference_only",
            "category": "price",
            "applies_to": "QC",
            "rule": "QC purchase cost and BMMB historical rate are reference-only",
            "value": "reference_only",
            "unit": "text",
            "source": "national_economics_v1",
            "tier": "reference",
            "notes": "Neither is used as a delivered price or observed revenue.",
        },
        {
            "assumption_id": "cost_donors_unchanged",
            "category": "cost",
            "applies_to": "all",
            "rule": "v1 logging/haul, road and silviculture donors are retained",
            "value": "v1",
            "unit": "model_version",
            "source": "national_economics_v1",
            "tier": "proxy_model",
            "notes": "No new provincial cost survey is introduced in v2.",
        },
        {
            "assumption_id": "margin_formula",
            "category": "margin",
            "applies_to": "all",
            "rule": "price proxy - logging - haul - road - silviculture",
            "value": "price - logging_haul - road - silviculture",
            "unit": "CAD/m3",
            "source": "this model",
            "tier": "model_estimate",
            "notes": "Low uses high cost; high uses low cost.",
        },
        {
            "assumption_id": "negative_margins_unclipped",
            "category": "margin",
            "applies_to": "all",
            "rule": "negative margins are retained and labelled",
            "value": "unclipped",
            "unit": "boolean",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": "No floor at zero; low case is deliberately conservative.",
        },
        {
            "assumption_id": "national_weighting",
            "category": "aggregation",
            "applies_to": "national",
            "rule": "volume-weight provincial margins by the 119 Mt allocation",
            "value": "119000000",
            "unit": "m3_case",
            "source": "provincial_allocation_119m",
            "tier": "model_estimate",
            "notes": "Diagnostic only; model_estimate_not_policy.",
        },
        {
            "assumption_id": "no_observed_or_open_claim",
            "category": "label",
            "applies_to": "all",
            "rule": "all rows model_estimate, observed=false, policy_grade=false",
            "value": "false",
            "unit": "boolean",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": "No strict-open licence claim is made.",
        },
        {
            "assumption_id": "price_year_mismatch",
            "category": "price",
            "applies_to": "all",
            "rule": "no deflator applied between 2025 BC price and 2026 rates",
            "value": "1.0000",
            "unit": "index",
            "source": "this model",
            "tier": "assumption",
            "notes": "Nominal mismatch is explicit and not corrected.",
        },
    ]


def build_sources() -> list[dict[str, Any]]:
    rows = []
    for ref in SOURCES.values():
        rows.append(
            {
                "source_key": ref.key,
                "title": ref.title,
                "publisher": ref.publisher,
                "publication_date": ref.publication_date,
                "retrieved_utc": RETRIEVED_UTC,
                "url": ref.url,
                "sha256": ref.sha256,
                "licence": ref.licence,
                "evidence_tier": ref.evidence_tier,
                "strict_open": "false",
                "used_for": (
                    "provincial stumpage/dues reference"
                    if ref.key
                    in {
                        "ab_timber_dues_2026_11",
                        "sk_dues_q3_2026_27",
                        "mb_crown_timber_dues_2026_11",
                        "on_stumpage_matrix_2026_27",
                        "qc_tarification_2026_27",
                        "nb_timber_royalty_2026_27",
                        "ns_standing_timber_2024",
                    }
                    else "BC price/stumpage ratio or model input"
                ),
                "redistributed": "false",
            }
        )
    return rows


def build_stumpage_rows(refs: Mapping[str, StumpageReference]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for province in PROVINCES:
        ref = refs[province]
        source = SOURCES.get(ref.source_key)
        rows.append(
            {
                "province": province,
                "province_name": PROVINCE_NAMES[province],
                "rate_low_cad_m3": (
                    _fmt(ref.rate_low_cad_m3) if ref.rate_low_cad_m3 is not None else ""
                ),
                "rate_base_cad_m3": (
                    _fmt(ref.rate_base_cad_m3)
                    if ref.rate_base_cad_m3 is not None
                    else ""
                ),
                "rate_high_cad_m3": (
                    _fmt(ref.rate_high_cad_m3)
                    if ref.rate_high_cad_m3 is not None
                    else ""
                ),
                "unit": "CAD_nominal_per_m3",
                "rate_type": ref.rate_type,
                "rate_basis": ref.rate_basis,
                "published_range": ref.published_range,
                "extraction_method": ref.extraction_method,
                "source_key": ref.source_key,
                "source_title": source.title if source else "",
                "source_url": source.url if source else "",
                "source_publication_date": source.publication_date if source else "",
                "source_sha256": source.sha256 if source else "",
                "is_stumpage_or_dues_rate": (
                    "true" if ref.extracted else "false"
                ),
                "is_delivered_price": "false",
                "is_observed_revenue": "false",
                "fallback_donor": "false" if ref.extracted else "BC",
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
                "notes": ref.notes,
            }
        )
    return rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise NationalEconomicsV2Error(f"refusing to write empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(out_dir: Path, outputs: Sequence[Path], summary: dict) -> None:
    inputs = []
    for path in (
        V1_DIR / "summary.json",
        V1_DIR / "national_margin_estimates.csv",
        v1.BC_LOG_MARKET_PRICES_CSV,
        v1.BC_COST_ESTIMATES_CSV,
        v1.BC_APPRAISAL_COSTS_CSV,
        v1.QC_SURVEY_CSV,
        v1.QC_PROCUREMENT_CSV,
        v1.BMMB_HISTORICAL_CSV,
        ALLOCATION_CSV,
        v1.SLOPE_OVERLAY_CSV,
        v1.ROAD_DISTANCE_CSV,
    ):
        if not path.is_file():
            raise NationalEconomicsV2Error(f"missing manifest input: {path}")
        if any(marker in str(path).lower() for marker in RESTRICTED_MARKERS):
            raise NationalEconomicsV2Error(f"restricted input path: {path}")
        inputs.append(
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "model_id": "national_economics_v2",
        "artifact_version": "2.0.0",
        "generated_utc": summary["generated_utc"],
        "observation_type": EVIDENCE,
        "restricted_inputs_used": [],
        "labels": {
            "observed": False,
            "policy_grade": False,
            "combined_national_net_value": False,
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
        },
        "inputs": inputs,
        "external_sources": [
            {
                "source_key": ref.key,
                "url": ref.url,
                "sha256": ref.sha256,
                "retrieved_utc": RETRIEVED_UTC,
                "redistributed": False,
            }
            for ref in SOURCES.values()
            if not ref.url.startswith("data/")
        ],
        "outputs": [
            {"path": path.name, "sha256": _sha256(path)} for path in outputs
        ],
    }
    (out_dir / MANIFEST_JSON).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


def _write_report(
    out_dir: Path,
    summary: dict,
    stumpage: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
) -> None:
    base = [
        row
        for row in prices
        if row["price_scenario"] == PRICE_SCENARIO_BASE
    ]
    extracted = [row for row in stumpage if row["is_stumpage_or_dues_rate"] == "true"]
    fallback = [row for row in stumpage if row["is_stumpage_or_dues_rate"] == "false"]
    scaled = [row for row in base if row["price_tier"] == PRICE_TIER_SCALED]
    national = next(row for row in summary["summary_rows"] if row["row_type"] == "national")
    negatives = [row for row in margins if float(row["margin_cad_m3"]) < 0.0]
    lines = [
        "# National Economics v2 - provincial stumpage-scaled price proxies",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        "## What this is",
        "",
        (
            "A 13-jurisdiction **model estimate** of log-price proxies and margins. "
            "Provincial stumpage, dues, royalty or standing-value rates are "
            "multiplied by the BC delivered-price/BCTS-stumpage ratio. The result "
            "is labelled `stumpage_scaled_proxy`; it is not a delivered price "
            "and not observed revenue. A flat BC donor scenario is retained as "
            "sensitivity and as the fallback where no current rate was extracted."
        ),
        "",
        "## Stumpage references",
        "",
        (
            f"- Extracted current rates: **{len(extracted)} of 13** "
            "(BC, AB, SK, MB, ON, QC, NB, NS)."
        ),
        (
            f"- Fallback donor jurisdictions: **{', '.join(row['province'] for row in fallback)}**."
        ),
        (
            "- NU is a fallback: the open.canada record in the research map is an "
            "Ontario data mirror, not a Nunavut rate. No numeric NU rate is asserted."
        ),
        "- No rate is described as a delivered price; `is_delivered_price=false` on every row.",
        "",
        "| Province | Rate type | Low | Base | High | Source date |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in extracted:
        lines.append(
            f"| {row['province']} | {row['rate_type']} | "
            f"{row['rate_low_cad_m3']} | {row['rate_base_cad_m3']} | "
            f"{row['rate_high_cad_m3']} | {row['source_publication_date']} |"
        )
    lines += [
        "",
        "## Price proxy construction",
        "",
        (
            f"BC BCTS stumpage = {BCTS_STUMPAGE_REVENUE_CAD / 1e6:.1f}M CAD / "
            f"{BCTS_HARVEST_M3 / 1e6:.1f}M m3 = "
            f"{BCTS_STUMPAGE_RATE_CAD_M3:.2f} CAD/m3."
        ),
        (
            f"BC blended delivered price = {summary['bc_price_blend']['base_cad_m3']:.2f} "
            f"CAD/m3, so the base ratio is "
            f"{summary['stumpage_ratio']['base']:.4f}. Low/high ratios are "
            f"{summary['stumpage_ratio']['low']:.4f}/"
            f"{summary['stumpage_ratio']['high']:.4f}."
        ),
        (
            f"The preferred scenario produces **{len(scaled)} stumpage-scaled "
            "price proxies**; BC itself keeps its reported average and five "
            "jurisdictions use the flat donor."
        ),
        (
            "The ratio transfers BC's cost-plus-margin structure. It is not a "
            "provincial cost survey and can overshoot where a province's stumpage "
            "is high relative to BC."
        ),
        (
            "Scope: BC blended Coast+Interior. BCTS does not publish a regional "
            "stumpage revenue/volume split in the materialised source, so separate "
            "Coast and Interior ratios are not computed."
        ),
        "",
        "## Margins",
        "",
        (
            f"Base-scenario national volume-weighted margin: "
            f"**{national['margin_base_cad_m3']} CAD/m3** "
            f"(low {national['margin_low_cad_m3']}, high "
            f"{national['margin_high_cad_m3']})."
        ),
        (
            f"Flat-donor sensitivity national base margin: "
            f"**{national['donor_flat_margin_base_cad_m3']} CAD/m3**."
        ),
        (
            f"{len(negatives)} of {len(margins)} margin rows are negative and are "
            "**unclipped**. Negatives are a result, not an error."
        ),
        (
            "Costs are v1 donors: BC logging+haul distribution, QC 2022 road "
            "allowance and BC Interior silviculture allowance."
        ),
        "",
        "## Comparison with v1",
        "",
        "| Province | v1 base margin | v2 scaled base | v2 donor flat | Delta |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in comparison:
        lines.append(
            f"| {row['province']} | {row['v1_margin_base_cad_m3']} | "
            f"{row['v2_stumpage_scaled_margin_base_cad_m3']} | "
            f"{row['v2_donor_flat_margin_base_cad_m3']} | "
            f"{row['margin_delta_cad_m3']} |"
        )
    lines += [
        "",
        "## Tiers and coverage",
        "",
        "- 13 jurisdictions, no `not_estimated` rows.",
        "- Two price scenarios: `stumpage_scaled` (base) and `donor_flat` (sensitivity).",
        "- Price tiers: `reported_average` (BC), `stumpage_scaled_proxy` (8), `donor_proxy` (fallbacks).",
        (
            "- Volume references: 119 Mt allocation, 2024 observed harvest, NFD "
            "supply and documented AAC are carried in the summary table."
        ),
        (
            "- National diagnostic uses the 119 Mt allocation and is labelled "
            "`model_estimate_not_policy`."
        ),
        "",
        "## Non-claims",
        "",
        "- Every row: `evidence=model_estimate`, `observed=false`, `policy_grade=false`, `combined_national_net_value=false`.",
        "- No strict-open licence claim is made; source documents are not redistributed.",
        "- No restricted, confidential or microdata input is used.",
        "- Not a policy result, not a provincial cost survey and not a combined BC+QC-only total.",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(out_dir: Path = DEFAULT_OUT_DIR) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    blend = v1.load_bc_price_blend()
    qc_evidence = v1.load_qc_evidence()
    slope = v1.load_slope_context()
    road = v1.load_road_context()
    road_allowance = v1.load_road_allowance()
    silviculture = v1.load_silviculture_allowance()
    costs = v1.build_cost_proxies(
        v1.load_bc_cost_distribution(),
        slope,
        road,
        road_allowance,
        silviculture,
        qc_evidence,
    )
    allocation = v1.load_allocation_volumes()
    volume_reference = _volume_reference()
    refs = _references()
    for province in PROVINCES:
        ref = refs[province]
        if ref.extracted:
            assert ref.rate_low_cad_m3 is not None
            assert ref.rate_base_cad_m3 is not None
            assert ref.rate_high_cad_m3 is not None
            if not (
                0.0 < ref.rate_low_cad_m3 <= ref.rate_base_cad_m3 <= ref.rate_high_cad_m3
            ):
                raise NationalEconomicsV2Error(
                    f"invalid stumpage range for {province}: {ref}"
                )
        elif ref.rate_type != RATE_TYPE_FALLBACK:
            raise NationalEconomicsV2Error(f"{province} missing fallback label")
    ratio = _ratio(blend)
    if not 0.0 < ratio[0] <= ratio[1] <= ratio[2]:
        raise NationalEconomicsV2Error(f"invalid BC scaled ratio: {ratio}")

    stumpage_rows = build_stumpage_rows(refs)
    price_rows = build_prices(refs, blend, ratio)
    margin_rows = build_margins(price_rows, costs, allocation, refs)
    summary_rows = build_summary_rows(
        margin_rows, allocation, price_rows, refs, volume_reference
    )
    comparison_rows = build_comparison(price_rows, margin_rows, refs)
    assumption_rows = build_assumptions(ratio)
    source_rows = build_sources()

    files = {
        STUMPAGE_CSV: stumpage_rows,
        PRICE_CSV: price_rows,
        MARGIN_CSV: margin_rows,
        SUMMARY_CSV: summary_rows,
        COMPARISON_CSV: comparison_rows,
        ASSUMPTIONS_CSV: assumption_rows,
        SOURCES_CSV: source_rows,
    }
    for filename, rows in files.items():
        _write_csv(out_dir / filename, rows)

    negative_all = [
        row for row in margin_rows if float(row["margin_cad_m3"]) < 0.0
    ]
    negative_base = [
        row
        for row in margin_rows
        if row["price_scenario"] == PRICE_SCENARIO_BASE
        and row["scenario_id"] == "base"
        and float(row["margin_cad_m3"]) < 0.0
    ]
    tier_counts = Counter(row["price_tier"] for row in price_rows)
    national = next(row for row in summary_rows if row["row_type"] == "national")
    generated = datetime.now(UTC).replace(microsecond=0).isoformat()
    summary = {
        "model_id": "national_economics_v2",
        "artifact_version": "2.0.0",
        "generated_utc": generated,
        "status": "model_estimate_stumpage_scaled_price_proxy",
        "description": (
            "13-jurisdiction margins with explicit provincial stumpage "
            "references and BC ratio-scaled price proxies."
        ),
        "coverage": {
            "provinces": list(PROVINCES),
            "province_count": len(PROVINCES),
            "missing": [],
            "not_estimated_count": 0,
            "margin_rows": len(margin_rows),
            "price_rows": len(price_rows),
            "price_scenarios": list(PRICE_SCENARIOS),
            "stumpage_extracted_count": sum(1 for r in refs.values() if r.extracted),
            "fallback_count": sum(1 for r in refs.values() if not r.extracted),
            "nu_zero_allocation_volume": allocation["NU"] == 0.0,
        },
        "stumpage_references": {
            "extracted": [p for p in PROVINCES if refs[p].extracted],
            "fallback": [p for p in PROVINCES if not refs[p].extracted],
            "rate_semantics": (
                "stumpage/dues/standing-value rates; not delivered prices; not "
                "observed revenue"
            ),
            "unit": "CAD_nominal_per_m3",
        },
        "bc_price_blend": {
            "low_cad_m3": blend.low_cad_m3,
            "base_cad_m3": blend.base_cad_m3,
            "high_cad_m3": blend.high_cad_m3,
            "price_basis": "mill_delivered_reported_average",
        },
        "bcts_stumpage": {
            "stumpage_revenue_cad": BCTS_STUMPAGE_REVENUE_CAD,
            "harvest_m3": BCTS_HARVEST_M3,
            "rate_cad_m3": BCTS_STUMPAGE_RATE_CAD_M3,
            "rate_basis": "published_fy2024_25_aggregate",
        },
        "stumpage_ratio": {
            "low": ratio[0],
            "base": ratio[1],
            "high": ratio[2],
            "formula": "BC delivered price / BCTS average stumpage rate",
            "scope": (
                "BC blended Coast+Interior; BCTS does not publish a regional "
                "stumpage revenue/volume split in the materialised source, so "
                "separate Coast and Interior ratios are not computed."
            ),
        },
        "volume_denominators": {
            "allocation_case_m3": 119_000_000.0,
            "allocation_table_sum_m3": sum(allocation.values()),
            "observed_2024_sum_m3": sum(
                float(row["observed_2024_m3"])
                for row in volume_reference.values()
            ),
            "nfd_supply_sum_m3": sum(
                float(row["nfd_m3"])
                for row in volume_reference.values()
                if row["nfd_m3"]
            ),
            "documented_aac_sum_m3": sum(
                float(row["aac_m3"])
                for row in volume_reference.values()
                if row["aac_m3"]
            ),
            "basis": (
                "119 Mt provincial screening allocation; NFD/CFS volume "
                "denominators are references, not a legal national AAC"
            ),
        },
        "price_tiers": dict(sorted(tier_counts.items())),
        "margins": {
            "negative_count": len(negative_all),
            "negative_base_count": len(negative_base),
            "negative_documented": True,
            "clipped": False,
        },
        "national_diagnostic": {
            "allocation_volume_m3": float(national["allocation_volume_m3"]),
            "stumpage_scaled_margin_low_cad_m3": float(
                national["margin_low_cad_m3"]
            ),
            "stumpage_scaled_margin_base_cad_m3": float(
                national["margin_base_cad_m3"]
            ),
            "stumpage_scaled_margin_high_cad_m3": float(
                national["margin_high_cad_m3"]
            ),
            "donor_flat_margin_base_cad_m3": float(
                national["donor_flat_margin_base_cad_m3"]
            ),
            "label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": False,
        },
        "qc_evidence": {
            "purchase_cost_reference_cad_m3": qc_evidence.purchase_cost_cad_m3,
            "bmmb_crown_benchmark_cad_m3": qc_evidence.bmmb_crown_rate_cad_m3,
            "used_as_price": False,
            "used_as_observed_revenue": False,
        },
        "labels": {
            "evidence": EVIDENCE,
            "observed": False,
            "policy_grade": False,
            "net_value_status": NET_VALUE_STATUS,
            "combined_national_net_value": False,
            "strict_open": False,
        },
        "licensing": LICENSING,
        "validation": {
            "all_provinces_present": True,
            "all_rows_model_estimate": True,
            "observed_claim": False,
            "strict_open_claim": False,
            "rate_source_hash_present": all(
                len(SOURCE_META.sha256) == 64 for SOURCE_META in SOURCES.values()
            ),
        },
        "summary_rows": summary_rows,
    }
    (out_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(
        out_dir, summary, stumpage_rows, price_rows, margin_rows, comparison_rows
    )
    output_paths = [out_dir / name for name in files] + [
        out_dir / SUMMARY_JSON,
        out_dir / REPORT_MD,
    ]
    _write_manifest(out_dir, output_paths, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)
    summary = build(args.out)
    print(
        json.dumps(
            {
                "out_dir": str(args.out),
                "coverage": summary["coverage"],
                "stumpage_ratio": summary["stumpage_ratio"],
                "negative_margins": summary["margins"]["negative_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
