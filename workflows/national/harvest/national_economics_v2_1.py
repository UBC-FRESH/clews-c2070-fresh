"""National provincial economics v2.1 with a fixed base/sensitivity decision.

The base scenario is the v1 flat BC donor price (``donor_flat``).  The
stumpage-scaled v2 price proxies are retained only as a sensitivity
(``stumpage_scaled``).  The scaled scenario is neither preferred nor observed.

Run from the repository root::

    .venv/bin/python -m workflows.national.harvest.national_economics_v2_1
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.harvest import national_economics_v1 as v1
from workflows.national.harvest import national_economics_v2 as v2

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_BASIS_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
V1_DIR = HARVEST_BASIS_DIR / "national_economics_v1"
V2_DIR = HARVEST_BASIS_DIR / "national_economics_v2"
DEFAULT_OUT_DIR = HARVEST_BASIS_DIR / "national_economics_v2_1"

PROVINCES = v1.PROVINCES
PROVINCE_NAMES = v1.PROVINCE_NAMES
ALLOCATION_CSV = v1.PROVINCIAL_ALLOCATION_CSV

MODEL_ID = "national_economics_v2_1"
ARTIFACT_VERSION = "2.1.0"

BASE_SCENARIO = "donor_flat"
SENSITIVITY_SCENARIO = "stumpage_scaled"
SCENARIOS = (BASE_SCENARIO, SENSITIVITY_SCENARIO)
SCENARIO_ROLE = {
    BASE_SCENARIO: "base",
    SENSITIVITY_SCENARIO: "sensitivity",
}
SCENARIO_ORDER = {
    BASE_SCENARIO: 0,
    SENSITIVITY_SCENARIO: 1,
}

MARGIN_CSV = "national_margin_estimates.csv"
SUMMARY_CSV = "national_margin_summary.csv"
COMPARISON_CSV = "scenario_comparison.csv"
TIER_CSV = "tier_coverage.csv"
PRICE_CSV = "national_price_proxies.csv"
STUMPAGE_CSV = "provincial_stumpage_references.csv"
ASSUMPTIONS_CSV = "assumptions_donor_register.csv"
SOURCES_CSV = "sources.csv"
SUMMARY_JSON = "summary.json"
REPORT_MD = "NATIONAL_ECONOMICS_V2_1_REPORT.md"
MANIFEST_JSON = "manifest.json"

EVIDENCE = "model_estimate"
OBSERVED = "false"
POLICY_GRADE = "false"
NET_VALUE_STATUS = "model_estimate"
COMBINED_NATIONAL_NET_VALUE = "false"
NATIONAL_DIAGNOSTIC_LABEL = "model_estimate_not_policy"
STRICT_OPEN = "false"

COST_SOURCE_KEYS = "national_economics_v1"
RESTRICTED_MARKERS = ("confidential", "restricted", "microdata", "private_data")

DUES_NOT_COMPARABLE = (
    "Stumpage, dues, royalty and standing-value rates are charges on standing "
    "timber. They are not delivered log prices: a delivered price embeds "
    "harvest, haul, road and silviculture cost plus a margin. Subtracting the "
    "same cost stack from a scaled dues rate double counts those costs if the "
    "result is read as a mill margin. Rates also differ in unit, species/grade, "
    "tenure, scaling and vintage. Deltas are therefore method changes, not "
    "observed market changes."
)


class NationalEconomicsV21Error(ValueError):
    """Raised when an input or result violates the v2.1 contract."""


def _fmt(value: float, digits: int = 4) -> str:
    if not math.isfinite(value):
        raise NationalEconomicsV21Error(f"non-finite value: {value}")
    return f"{value:.{digits}f}"


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise NationalEconomicsV21Error(f"refusing to write empty table: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _price_source_keys(
    province: str,
    scenario: str,
    reference: v2.StumpageReference,
) -> tuple[str, ...]:
    if province == "BC":
        return ("bc_log_market_2025_12", "national_economics_v1")
    if scenario == BASE_SCENARIO:
        return ("national_economics_v1", "bc_log_market_2025_12")
    if reference.extracted:
        return (
            reference.source_key,
            "bcts_annual_2024_25",
            "bc_log_market_2025_12",
        )
    return ("national_economics_v1", "bc_log_market_2025_12")


def _source_urls(keys: Iterable[str]) -> str:
    urls = []
    for key in keys:
        source = v2.SOURCES.get(key)
        if source is not None:
            urls.append(source.url)
    return ";".join(dict.fromkeys(urls))


def _price_rows(prices: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    refs = v2._references()
    ordered = sorted(
        (dict(row) for row in prices),
        key=lambda row: (
            SCENARIO_ORDER[row["price_scenario"]],
            PROVINCES.index(row["province"]),
        ),
    )
    for row in ordered:
        scenario = row["price_scenario"]
        province = row["province"]
        keys = _price_source_keys(province, scenario, refs[province])
        if province == "BC":
            note = (
                "BC reported 2025-12 3-month mill-delivered average; the "
                "stumpage-scaled method is not applied to BC itself."
            )
        elif scenario == BASE_SCENARIO:
            note = (
                "Flat BC donor price used as the v2.1 base scenario. No "
                "provincial price index is materialised. Not observed revenue."
            )
        elif refs[province].extracted:
            note = (
                "Stumpage/dues/standing-value rate multiplied by the BC "
                "delivered-price/BCTS-stumpage ratio. Sensitivity only: not "
                "preferred, not a delivered price and not observed revenue."
            )
        else:
            note = (
                "No current provincial rate is materialised, so the flat BC "
                "donor is used as the sensitivity fallback. Not observed revenue."
            )
        row.update(
            {
                "scenario_role": SCENARIO_ROLE[scenario],
                "scenario_order": SCENARIO_ORDER[scenario],
                "price_scenario_order": SCENARIO_ORDER[scenario],
                "base_scenario": BASE_SCENARIO,
                "source_keys": ";".join(keys),
                "source_urls": _source_urls(keys),
                "evidence_tier": row["price_tier"],
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
                "notes": note,
            }
        )
        rows.append(row)
    return rows


def _margin_rows(
    margins: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    refs: Mapping[str, v2.StumpageReference],
) -> list[dict[str, Any]]:
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    rows: list[dict[str, Any]] = []
    for raw in sorted(
        margins,
        key=lambda row: (
            SCENARIO_ORDER[row["price_scenario"]],
            PROVINCES.index(row["province"]),
            int(row["scenario_order"]),
        ),
    ):
        row = dict(raw)
        province = row["province"]
        scenario = row["price_scenario"]
        price = price_by_key[(province, scenario)]
        keys = _price_source_keys(province, scenario, refs[province])
        row.update(
            {
                "scenario_role": SCENARIO_ROLE[scenario],
                "price_scenario_order": SCENARIO_ORDER[scenario],
                "base_scenario": BASE_SCENARIO,
                "price_low_cad_m3": price["price_low_cad_m3"],
                "price_base_cad_m3": price["price_base_cad_m3"],
                "price_high_cad_m3": price["price_high_cad_m3"],
                "evidence_tier": row["price_tier"],
                "price_source_keys": ";".join(keys),
                "price_source_urls": _source_urls(keys),
                "cost_source_keys": COST_SOURCE_KEYS,
                "source_keys": ";".join(
                    dict.fromkeys((*keys, COST_SOURCE_KEYS))
                ),
                "source_urls": _source_urls((*keys, COST_SOURCE_KEYS)),
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "net_value_status": NET_VALUE_STATUS,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "negative_margin_clipped": "false",
            }
        )
        rows.append(row)
    return rows


def _stumpage_rows(refs: Mapping[str, v2.StumpageReference]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in v2.build_stumpage_rows(refs):
        row = dict(row)
        row.update(
            {
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
            }
        )
        rows.append(row)
    return rows


def _volume_reference() -> dict[str, dict[str, str]]:
    return v2._volume_reference()


def _weighted(value_by_province: Mapping[str, float], allocation: Mapping[str, float]) -> float:
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsV21Error("allocation volume sum is zero")
    return sum(value_by_province[p] * allocation[p] for p in PROVINCES) / total_volume


def _raw_margin(
    province: str,
    scenario: str,
    case: str,
    refs: Mapping[str, v2.StumpageReference],
    blend: v1.BcPriceBlend,
    ratio: tuple[float, float, float],
    costs: Mapping[str, v1.CostProxy],
) -> float:
    """Unrounded margin for the national diagnostic, mirroring v1/v2 rules."""
    reference = refs[province]
    uses_scaled_rate = (
        province != "BC"
        and scenario == SENSITIVITY_SCENARIO
        and reference.extracted
    )
    if not uses_scaled_rate:
        price = getattr(blend, f"{case}_cad_m3")
    else:
        rate = getattr(reference, f"rate_{case}_cad_m3")
        if rate is None:
            raise NationalEconomicsV21Error(
                f"missing stumpage rate for {province}/{case}"
            )
        ratio_index = {"low": 0, "base": 1, "high": 2}[case]
        price = rate * ratio[ratio_index]
    cost_kind = {"low": "high", "base": "base", "high": "low"}[case]
    cost = costs[province]
    return price - (
        getattr(cost, f"logging_haul_{cost_kind}_cad_m3")
        + getattr(cost, f"road_{cost_kind}_cad_m3")
        + getattr(cost, f"silviculture_{cost_kind}_cad_m3")
    )


def _summary_rows(
    margins: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    refs: Mapping[str, v2.StumpageReference],
    allocation: Mapping[str, float],
    volume_reference: Mapping[str, Mapping[str, str]],
    blend: v1.BcPriceBlend,
    ratio: tuple[float, float, float],
    costs: Mapping[str, v1.CostProxy],
) -> list[dict[str, Any]]:
    margin_by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    total_volume = sum(allocation.values())
    if total_volume <= 0.0:
        raise NationalEconomicsV21Error("allocation volume sum is zero")

    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        for province in PROVINCES:
            low = margin_by_key[(province, scenario, "low")]
            base = margin_by_key[(province, scenario, "base")]
            high = margin_by_key[(province, scenario, "high")]
            price = price_by_key[(province, scenario)]
            keys = _price_source_keys(province, scenario, refs[province])
            ref = volume_reference[province]
            margin_base = float(base["margin_cad_m3"])
            margin_low = float(low["margin_cad_m3"])
            rows.append(
                {
                    "row_type": "province",
                    "scenario_role": SCENARIO_ROLE[scenario],
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": province,
                    "province_name": PROVINCE_NAMES[province],
                    "allocation_volume_m3": _fmt(allocation[province]),
                    "volume_share": _fmt(allocation[province] / total_volume, 8),
                    "volume_denominator_basis": (
                        "119 Mt provincial screening allocation; zero commercial "
                        "harvest asserted for NU"
                    ),
                    "observed_2024_volume_m3": ref["observed_2024_m3"],
                    "nfd_supply_m3": ref["nfd_m3"],
                    "documented_aac_m3": ref["aac_m3"],
                    "volume_reference_basis": ref["basis"],
                    "price_low_cad_m3": price["price_low_cad_m3"],
                    "price_base_cad_m3": price["price_base_cad_m3"],
                    "price_high_cad_m3": price["price_high_cad_m3"],
                    "price_basis": price["price_basis"],
                    "price_tier": price["price_tier"],
                    "evidence_tier": price["price_tier"],
                    "stumpage_rate_type": refs[province].rate_type,
                    "total_cost_base_cad_m3": base["total_cost_cad_m3"],
                    "margin_low_cad_m3": _fmt(margin_low),
                    "margin_base_cad_m3": _fmt(margin_base),
                    "margin_high_cad_m3": _fmt(float(high["margin_cad_m3"])),
                    "margin_status": (
                        "base_negative_documented"
                        if margin_base < 0.0
                        else "low_negative_documented"
                        if margin_low < 0.0
                        else "non_negative"
                    ),
                    "cost_tier": base["cost_tier"],
                    "source_keys": ";".join(
                        dict.fromkeys((*keys, COST_SOURCE_KEYS))
                    ),
                    "source_urls": _source_urls((*keys, COST_SOURCE_KEYS)),
                    "national_diagnostic_label": "",
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

    for scenario in SCENARIOS:
        margin_low = {
            p: _raw_margin(p, scenario, "low", refs, blend, ratio, costs)
            for p in PROVINCES
        }
        margin_base = {
            p: _raw_margin(p, scenario, "base", refs, blend, ratio, costs)
            for p in PROVINCES
        }
        margin_high = {
            p: _raw_margin(p, scenario, "high", refs, blend, ratio, costs)
            for p in PROVINCES
        }
        price_low = {
            p: float(price_by_key[(p, scenario)]["price_low_cad_m3"])
            for p in PROVINCES
        }
        price_base = {
            p: float(price_by_key[(p, scenario)]["price_base_cad_m3"])
            for p in PROVINCES
        }
        price_high = {
            p: float(price_by_key[(p, scenario)]["price_high_cad_m3"])
            for p in PROVINCES
        }
        cost_base = {
            p: float(margin_by_key[(p, scenario, "base")]["total_cost_cad_m3"])
            for p in PROVINCES
        }
        rows.append(
            {
                "row_type": "national",
                "scenario_role": SCENARIO_ROLE[scenario],
                "price_scenario": scenario,
                "base_scenario": BASE_SCENARIO,
                "province": "NATIONAL",
                "province_name": "Canada",
                "allocation_volume_m3": _fmt(total_volume),
                "volume_share": "1.00000000",
                "volume_denominator_basis": (
                    "119 Mt provincial screening allocation; national "
                    "volume-weighted diagnostic only"
                ),
                "observed_2024_volume_m3": _fmt(
                    sum(
                        float(row["observed_2024_m3"])
                        for row in volume_reference.values()
                    )
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
                    "sum of provincial 2024 observed volume, NFD supply and "
                    "documented AAC where published; not a legal national AAC"
                ),
                "price_low_cad_m3": _fmt(_weighted(price_low, allocation)),
                "price_base_cad_m3": _fmt(_weighted(price_base, allocation)),
                "price_high_cad_m3": _fmt(_weighted(price_high, allocation)),
                "price_basis": (
                    "flat_BC_donor_delivered_proxy"
                    if scenario == BASE_SCENARIO
                    else "mixed_reported_average_and_stumpage_scaled_proxy"
                ),
                "price_tier": "mixed",
                "evidence_tier": "mixed_model_estimate",
                "stumpage_rate_type": "mixed",
                "total_cost_base_cad_m3": _fmt(_weighted(cost_base, allocation)),
                "margin_low_cad_m3": _fmt(_weighted(margin_low, allocation)),
                "margin_base_cad_m3": _fmt(_weighted(margin_base, allocation)),
                "margin_high_cad_m3": _fmt(_weighted(margin_high, allocation)),
                "margin_status": NATIONAL_DIAGNOSTIC_LABEL,
                "cost_tier": "mixed_proxy",
                "source_keys": "national_economics_v1;bc_log_market_2025_12;bcts_annual_2024_25",
                "source_urls": _source_urls(
                    (
                        "national_economics_v1",
                        "bc_log_market_2025_12",
                        "bcts_annual_2024_25",
                    )
                ),
                "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "notes": (
                    "Volume-weighted across all 13 jurisdictions using the 119 Mt "
                    "allocation. Model estimate, not a policy result and not a "
                    "combined net value. Base scenario is donor_flat."
                ),
            }
        )
    return rows


def _comparison_rows(
    margins: Sequence[Mapping[str, Any]],
    refs: Mapping[str, v2.StumpageReference],
    allocation: Mapping[str, float],
) -> list[dict[str, Any]]:
    v1_base = v2._read_v1_base_margins()
    central = {
        (row["province"], row["price_scenario"]): row
        for row in margins
        if row["scenario_id"] == "base"
    }
    rows: list[dict[str, Any]] = []
    for province in (*PROVINCES, "NATIONAL"):
        if province == "NATIONAL":
            flat = _weighted(
                {
                    p: float(central[(p, BASE_SCENARIO)]["margin_cad_m3"])
                    for p in PROVINCES
                },
                allocation,
            )
            scaled = _weighted(
                {
                    p: float(central[(p, SENSITIVITY_SCENARIO)]["margin_cad_m3"])
                    for p in PROVINCES
                },
                allocation,
            )
            v1_flat = _weighted(
                {p: float(v1_base[p]["margin_cad_m3"]) for p in PROVINCES},
                allocation,
            )
            rate_type = "mixed"
            extracted = "mixed"
            province_name = "Canada"
            note = (
                "Volume-weighted diagnostic. A dues rate is not a delivered log "
                "price; the scaled-minus-donor-flat delta is a method effect, "
                "not an observed market change. No combined national net value "
                "is computed."
            )
        else:
            v1_flat = float(v1_base[province]["margin_cad_m3"])
            flat = float(central[(province, BASE_SCENARIO)]["margin_cad_m3"])
            scaled = float(central[(province, SENSITIVITY_SCENARIO)]["margin_cad_m3"])
            rate_type = refs[province].rate_type
            extracted = str(refs[province].extracted).lower()
            province_name = PROVINCE_NAMES[province]
            if province == "BC":
                note = (
                    "BC keeps the reported average in both scenarios; there is "
                    "no delta and no dues comparison. A stumpage rate is not a "
                    "delivered log price; the reported average is a price "
                    "observation input, not a provincial net-value observation."
                )
            elif refs[province].extracted:
                note = (
                    f"{rate_type} is a charge on standing timber, not a delivered "
                    "log price. The scaled-minus-donor-flat delta is a model "
                    "method effect. " + DUES_NOT_COMPARABLE
                )
            else:
                note = (
                    "No provincial rate is materialised, so both scenarios use "
                    "the same flat BC donor and the delta is zero. " +
                    DUES_NOT_COMPARABLE
                )
        rows.append(
            {
                "row_type": "national" if province == "NATIONAL" else "province",
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "province": province,
                "province_name": province_name,
                "stumpage_rate_type": rate_type,
                "stumpage_extracted": extracted,
                "v1_flat_margin_base_cad_m3": _fmt(v1_flat),
                "v2_1_donor_flat_margin_base_cad_m3": _fmt(flat),
                "v2_1_stumpage_scaled_margin_base_cad_m3": _fmt(scaled),
                "donor_flat_minus_v1_flat_cad_m3": _fmt(flat - v1_flat),
                "scaled_minus_donor_flat_cad_m3": _fmt(scaled - flat),
                "scaled_minus_v1_flat_cad_m3": _fmt(scaled - v1_flat),
                "dues_comparable_to_delivered_price": "false",
                "comparison_status": "method_change_not_observed_market_change",
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "notes": note,
            }
        )
    return rows


def _tier_rows(
    margins: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    price_by_key = {
        (row["province"], row["price_scenario"]): row for row in prices
    }
    margin_by_key = {
        (row["province"], row["price_scenario"], row["scenario_id"]): row
        for row in margins
    }
    rows: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        role = SCENARIO_ROLE[scenario]
        for province in PROVINCES:
            price = price_by_key[(province, scenario)]
            base = margin_by_key[(province, scenario, "base")]
            rows.append(
                {
                    "row_type": "province_scenario",
                    "scenario_role": role,
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": province,
                    "province_count": "1",
                    "price_tier": price["price_tier"],
                    "cost_tier": base["cost_tier"],
                    "tier_count": "1",
                    "all_model_estimate": "true",
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "notes": (
                        "One province in this price scenario."
                    ),
                }
            )
        for tier, count in sorted(
            Counter(
                price_by_key[(province, scenario)]["price_tier"]
                for province in PROVINCES
            ).items()
        ):
            rows.append(
                {
                    "row_type": "price_tier_count",
                    "scenario_role": role,
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": "",
                    "province_count": "",
                    "price_tier": tier,
                    "cost_tier": "",
                    "tier_count": str(count),
                    "all_model_estimate": "true",
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "notes": "Price-tier count for this scenario.",
                }
            )
        for tier, count in sorted(
            Counter(
                margin_by_key[(province, scenario, "base")]["cost_tier"]
                for province in PROVINCES
            ).items()
        ):
            rows.append(
                {
                    "row_type": "cost_tier_count",
                    "scenario_role": role,
                    "price_scenario": scenario,
                    "base_scenario": BASE_SCENARIO,
                    "province": "",
                    "province_count": "",
                    "price_tier": "",
                    "cost_tier": tier,
                    "tier_count": str(count),
                    "all_model_estimate": "true",
                    "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                    "evidence": EVIDENCE,
                    "observed": OBSERVED,
                    "policy_grade": POLICY_GRADE,
                    "notes": "Cost-tier count for this scenario.",
                }
            )
        rows.append(
            {
                "row_type": "scenario_total",
                "scenario_role": role,
                "price_scenario": scenario,
                "base_scenario": BASE_SCENARIO,
                "province": "",
                "province_count": str(len(PROVINCES)),
                "price_tier": "",
                "cost_tier": "",
                "tier_count": str(len(PROVINCES)),
                "all_model_estimate": "true",
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "notes": (
                    "All 13 jurisdictions are present; no not-estimated rows and "
                    "no observed or policy-grade claim."
                ),
            }
        )
    return rows


def _assumption_rows(ratio: tuple[float, float, float]) -> list[dict[str, Any]]:
    rows = [
        {
            "assumption_id": "scenario_decision",
            "category": "scenario",
            "applies_to": "all",
            "rule": "donor_flat is the base scenario; stumpage_scaled is sensitivity",
            "value": "base=donor_flat;sensitivity=stumpage_scaled",
            "unit": "text",
            "source": "v2.1 decision",
            "tier": "documented_constraint",
            "notes": (
                "The scaled scenario is not preferred, not observed and not policy "
                "grade. It is retained only to show the effect of the v2 method."
            ),
        },
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
                "The ratio transfers BC's delivered/stumpage relationship; it is "
                "not a provincial cost survey or an observed price."
            ),
        },
        {
            "assumption_id": "donor_flat_base",
            "category": "price",
            "applies_to": "all",
            "rule": "flat BC 2025-12 blended delivered price",
            "value": "donor_flat",
            "unit": "scenario",
            "source": "national_economics_v1",
            "tier": "reported_average",
            "notes": (
                "BC reported average; non-BC jurisdictions use the same BC donor "
                "with no provincial index materialised."
            ),
        },
        {
            "assumption_id": "stumpage_scaled_sensitivity",
            "category": "price",
            "applies_to": "BC,AB,SK,MB,ON,QC,NB,NS",
            "rule": "stumpage rate times BC delivered/BCTS ratio",
            "value": "stumpage_scaled",
            "unit": "scenario",
            "source": "this model",
            "tier": "model_estimate",
            "notes": (
                "Sensitivity only; labelled stumpage_scaled_proxy; not observed "
                "and not preferred."
            ),
        },
        {
            "assumption_id": "fallback_jurisdictions",
            "category": "price",
            "applies_to": "PE,NL,YT,NT,NU",
            "rule": "BC flat donor in both scenarios",
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
            "notes": "No new provincial cost survey is introduced in v2.1.",
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
            "notes": "No floor at zero; the low case is deliberately conservative.",
        },
        {
            "assumption_id": "dues_not_comparable",
            "category": "comparison",
            "applies_to": "all",
            "rule": "dues/stumpage rates are not delivered prices",
            "value": "not_comparable",
            "unit": "text",
            "source": "this model",
            "tier": "documented_constraint",
            "notes": DUES_NOT_COMPARABLE,
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
    for row in rows:
        row.update(
            {
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
            }
        )
    return rows


def _source_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in v2.build_sources():
        row = dict(row)
        row.update(
            {
                "base_scenario": BASE_SCENARIO,
                "sensitivity_scenario": SENSITIVITY_SCENARIO,
                "evidence": EVIDENCE,
                "observed": OBSERVED,
                "policy_grade": POLICY_GRADE,
                "combined_national_net_value": COMBINED_NATIONAL_NET_VALUE,
                "strict_open": STRICT_OPEN,
            }
        )
        rows.append(row)
    return rows


def _validate(
    margins: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
) -> None:
    if len(margins) != len(PROVINCES) * len(SCENARIOS) * 3:
        raise NationalEconomicsV21Error(
            f"expected {len(PROVINCES) * len(SCENARIOS) * 3} margin rows, "
            f"found {len(margins)}"
        )
    for row in margins:
        if row["base_scenario"] != BASE_SCENARIO:
            raise NationalEconomicsV21Error(
                f"margin row does not declare base_scenario=donor_flat: {row}"
            )
        if row["evidence"] != EVIDENCE or row["observed"] != OBSERVED:
            raise NationalEconomicsV21Error(f"margin row label violation: {row}")
        if row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV21Error(f"policy-grade claim: {row}")
        low = float(row["price_low_cad_m3"])
        base = float(row["price_base_cad_m3"])
        high = float(row["price_high_cad_m3"])
        if not 0.0 < low <= base <= high:
            raise NationalEconomicsV21Error(
                f"unordered price for {row['province']}/{row['price_scenario']}: "
                f"{low}, {base}, {high}"
            )
        if row["negative_margin_clipped"] != "false":
            raise NationalEconomicsV21Error(f"clipped margin: {row}")
    for scenario in SCENARIOS:
        provinces = {
            row["province"] for row in margins if row["price_scenario"] == scenario
        }
        if provinces != set(PROVINCES):
            raise NationalEconomicsV21Error(
                f"{scenario} missing provinces: {sorted(set(PROVINCES) - provinces)}"
            )
        national = next(
            row
            for row in summary_rows
            if row["row_type"] == "national" and row["price_scenario"] == scenario
        )
        if national["national_diagnostic_label"] != NATIONAL_DIAGNOSTIC_LABEL:
            raise NationalEconomicsV21Error("national diagnostic label missing")
        if not (
            float(national["margin_low_cad_m3"])
            <= float(national["margin_base_cad_m3"])
            <= float(national["margin_high_cad_m3"])
        ):
            raise NationalEconomicsV21Error(
                f"unordered national margins for {scenario}"
            )
    if len(summary_rows) != len(PROVINCES) * len(SCENARIOS) + len(SCENARIOS):
        raise NationalEconomicsV21Error("summary row count mismatch")
    for row in summary_rows:
        if row["base_scenario"] != BASE_SCENARIO:
            raise NationalEconomicsV21Error(
                f"summary row does not declare base_scenario=donor_flat: {row}"
            )
        if row["observed"] != OBSERVED or row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV21Error(f"summary label violation: {row}")
        if row["row_type"] == "national":
            if row["national_diagnostic_label"] != NATIONAL_DIAGNOSTIC_LABEL:
                raise NationalEconomicsV21Error("summary national label missing")
        elif not (
            float(row["margin_low_cad_m3"])
            <= float(row["margin_base_cad_m3"])
            <= float(row["margin_high_cad_m3"])
        ):
            raise NationalEconomicsV21Error(
                f"unordered summary margins for {row['province']}/{row['price_scenario']}"
            )
    if len(comparison) != len(PROVINCES) + 1:
        raise NationalEconomicsV21Error("comparison row count mismatch")
    for row in comparison:
        if row["observed"] != OBSERVED or row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV21Error(f"comparison label violation: {row}")
        if row["dues_comparable_to_delivered_price"] != "false":
            raise NationalEconomicsV21Error(
                "dues must not be labelled comparable to a delivered price"
            )
    if not tiers:
        raise NationalEconomicsV21Error("tier coverage is empty")
    for row in tiers:
        if row["observed"] != OBSERVED or row["policy_grade"] != POLICY_GRADE:
            raise NationalEconomicsV21Error(f"tier label violation: {row}")
    for row in prices:
        for marker in ("not_estimated", "not estimated"):
            if marker in " ".join(str(value) for value in row.values()).lower():
                raise NationalEconomicsV21Error("not-estimated marker in price row")


def _write_report(
    out_dir: Path,
    summary: Mapping[str, Any],
    summary_rows: Sequence[Mapping[str, Any]],
    comparison: Sequence[Mapping[str, Any]],
    margins: Sequence[Mapping[str, Any]],
    tiers: Sequence[Mapping[str, Any]],
) -> None:
    provinces = [
        row
        for row in summary_rows
        if row["row_type"] == "province"
    ]
    national = {
        row["price_scenario"]: row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    base_national = national[BASE_SCENARIO]
    sensitivity_national = national[SENSITIVITY_SCENARIO]
    negatives = [row for row in margins if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = Counter(row["price_scenario"] for row in negatives)
    base_negative = [
        row
        for row in negatives
        if row["price_scenario"] == BASE_SCENARIO
        and row["scenario_id"] == "base"
    ]
    scaled_negative = [
        row
        for row in negatives
        if row["price_scenario"] == SENSITIVITY_SCENARIO
        and row["scenario_id"] == "base"
    ]
    tier_counts = {
        scenario: {
            row["price_tier"]: int(row["tier_count"])
            for row in tiers
            if row["row_type"] == "price_tier_count"
            and row["price_scenario"] == scenario
        }
        for scenario in SCENARIOS
    }
    lines = [
        "# National Economics v2.1 - donor-flat base with stumpage-scaled sensitivity",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        "## Decision",
        "",
        (
            "**Base scenario: `donor_flat`.** All 13 jurisdictions use the v1 BC "
            "2025-12 blended mill-delivered donor price. The national base "
            f"diagnostic is **{base_national['margin_base_cad_m3']} CAD/m3** "
            f"(low {base_national['margin_low_cad_m3']}, high "
            f"{base_national['margin_high_cad_m3']})."
        ),
        (
            "**Sensitivity: `stumpage_scaled`.** The v2 provincial stumpage/dues "
            "rate proxies are retained only as a sensitivity and fallback "
            "exercise. The national sensitivity diagnostic is "
            f"**{sensitivity_national['margin_base_cad_m3']} CAD/m3** "
            f"(low {sensitivity_national['margin_low_cad_m3']}, high "
            f"{sensitivity_national['margin_high_cad_m3']})."
        ),
        (
            "The stumpage-scaled scenario is **not preferred, not observed and "
            "not policy grade**. It is a synthetic method sensitivity, not a "
            "delivered price series and not a mill margin."
        ),
        "",
        "## Coverage and evidence tiers",
        "",
        "- 13 jurisdictions: BC, AB, SK, MB, ON, QC, NB, NS, NL, PE, YT, NT, NU.",
        "- Two price scenarios: `donor_flat` (base) and `stumpage_scaled` (sensitivity).",
        "- No `not_estimated` rows; every jurisdiction has low/base/high margins.",
        (
            "- `donor_flat` tiers: "
            + ", ".join(
                f"`{tier}`={count}"
                for tier, count in sorted(tier_counts[BASE_SCENARIO].items())
            )
            + "."
        ),
        (
            "- `stumpage_scaled` tiers: "
            + ", ".join(
                f"`{tier}`={count}"
                for tier, count in sorted(
                    tier_counts[SENSITIVITY_SCENARIO].items()
                )
            )
            + "."
        ),
        (
            "- Cost tiers are unchanged from v1: `proxy_model` (BC), "
            "`donor_proxy_with_reported_road` (QC), `donor_proxy` (11)."
        ),
        "",
        "## Province results",
        "",
        "| Province | Base donor_flat low | Base donor_flat central | Base donor_flat high | Sensitivity scaled central |",
        "|---|---:|---:|---:|---:|",
    ]
    by_province = {
        (row["province"], row["price_scenario"]): row for row in provinces
    }
    for province in PROVINCES:
        base = by_province[(province, BASE_SCENARIO)]
        scaled = by_province[(province, SENSITIVITY_SCENARIO)]
        lines.append(
            f"| {province} | {base['margin_low_cad_m3']} | "
            f"{base['margin_base_cad_m3']} | {base['margin_high_cad_m3']} | "
            f"{scaled['margin_base_cad_m3']} |"
        )
    lines += [
        "",
        "## National diagnostic",
        "",
        "| Scenario | Role | Low | Base | High | Label | Combined net value |",
        "|---|---|---:|---:|---:|---|---|",
        (
            f"| donor_flat | base | {base_national['margin_low_cad_m3']} | "
            f"{base_national['margin_base_cad_m3']} | "
            f"{base_national['margin_high_cad_m3']} | "
            f"`{NATIONAL_DIAGNOSTIC_LABEL}` | false |"
        ),
        (
            f"| stumpage_scaled | sensitivity | "
            f"{sensitivity_national['margin_low_cad_m3']} | "
            f"{sensitivity_national['margin_base_cad_m3']} | "
            f"{sensitivity_national['margin_high_cad_m3']} | "
            f"`{NATIONAL_DIAGNOSTIC_LABEL}` | false |"
        ),
        "",
        (
            "The national numbers are volume-weighted diagnostics across all 13 "
            "jurisdictions on the 119 Mt screening allocation. They are not a "
            "combined national net value, not a policy result, and not a total "
            "of observed profits."
        ),
        "",
        "## Scenario comparison",
        "",
        (
            "`scenario_comparison.csv` reports the v1 flat donor, the v2.1 "
            "donor-flat base and the v2.1 stumpage-scaled sensitivity. Because "
            "the cost stack is unchanged, the donor-flat base reproduces v1."
        ),
        "",
        "### Why dues are not comparable",
        "",
        DUES_NOT_COMPARABLE,
        "",
        (
            "The `scaled_minus_donor_flat_cad_m3` column is therefore a method "
            "delta. It must not be read as an observed market change, a "
            "provincial stumpage effect, or a preferred estimate."
        ),
        "",
        "## Negative margins and labels",
        "",
        (
            f"{len(negatives)} of {len(margins)} margin rows are negative and are "
            "**unclipped**. Negatives are a result, not an error."
        ),
        (
            f"- `donor_flat`: {negative_by_scenario[BASE_SCENARIO]} negative rows, "
            f"{len(base_negative)} in the central case."
        ),
        (
            f"- `stumpage_scaled`: "
            f"{negative_by_scenario[SENSITIVITY_SCENARIO]} negative rows, "
            f"{len(scaled_negative)} in the central case."
        ),
        "",
        (
            "Every row carries `evidence=model_estimate`, `observed=false`, "
            "`policy_grade=false` and `combined_national_net_value=false`."
        ),
        "",
        "## Non-claims",
        "",
        "- Not observed profit, not a mill margin and not official net value.",
        "- Not a stumpage rate and not policy grade.",
        "- The stumpage-scaled sensitivity is not preferred and not observed.",
        "- No combined national net value is computed.",
        "- No strict-open licence claim is made; source documents are not redistributed.",
        "- No restricted, confidential or microdata input is used.",
    ]
    (out_dir / REPORT_MD).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_manifest(out_dir: Path, outputs: Sequence[Path], summary: Mapping[str, Any]) -> None:
    inputs = []
    for path in (
        V2_DIR / "summary.json",
        V2_DIR / "national_margin_estimates.csv",
        V2_DIR / "national_price_proxies.csv",
        V2_DIR / "provincial_stumpage_references.csv",
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
            raise NationalEconomicsV21Error(f"missing manifest input: {path}")
        if any(marker in str(path).lower() for marker in RESTRICTED_MARKERS):
            raise NationalEconomicsV21Error(f"restricted input path: {path}")
        inputs.append(
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": summary["generated_utc"],
        "observation_type": EVIDENCE,
        "base_scenario": BASE_SCENARIO,
        "sensitivity_scenario": SENSITIVITY_SCENARIO,
        "stumpage_scaled_preferred": False,
        "stumpage_scaled_observed": False,
        "combined_national_net_value": False,
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
                "source_key": source.key,
                "url": source.url,
                "sha256": source.sha256,
                "retrieved_utc": summary["source_retrieved_utc"],
                "redistributed": False,
            }
            for source in v2.SOURCES.values()
            if not source.url.startswith("data/")
        ],
        "outputs": [
            {"path": path.name, "sha256": _sha256(path)} for path in outputs
        ],
    }
    (out_dir / MANIFEST_JSON).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )


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
    refs = v2._references()
    for province in PROVINCES:
        reference = refs[province]
        if reference.extracted:
            assert reference.rate_low_cad_m3 is not None
            assert reference.rate_base_cad_m3 is not None
            assert reference.rate_high_cad_m3 is not None
            if not (
                0.0
                < reference.rate_low_cad_m3
                <= reference.rate_base_cad_m3
                <= reference.rate_high_cad_m3
            ):
                raise NationalEconomicsV21Error(
                    f"invalid stumpage range for {province}: {reference}"
                )
        elif reference.rate_type != v2.RATE_TYPE_FALLBACK:
            raise NationalEconomicsV21Error(f"{province} missing fallback label")
    ratio = v2._ratio(blend)
    if not 0.0 < ratio[0] <= ratio[1] <= ratio[2]:
        raise NationalEconomicsV21Error(f"invalid BC scaled ratio: {ratio}")

    price_rows = _price_rows(v2.build_prices(refs, blend, ratio))
    margin_rows = _margin_rows(
        v2.build_margins(price_rows, costs, allocation, refs), price_rows, refs
    )
    summary_rows = _summary_rows(
        margin_rows,
        price_rows,
        refs,
        allocation,
        volume_reference,
        blend,
        ratio,
        costs,
    )
    comparison_rows = _comparison_rows(margin_rows, refs, allocation)
    tier_rows = _tier_rows(margin_rows, price_rows)
    assumption_rows = _assumption_rows(ratio)
    source_rows = _source_rows()
    stumpage_rows = _stumpage_rows(refs)

    _validate(margin_rows, summary_rows, price_rows, comparison_rows, tier_rows)

    files = {
        MARGIN_CSV: margin_rows,
        SUMMARY_CSV: summary_rows,
        COMPARISON_CSV: comparison_rows,
        TIER_CSV: tier_rows,
        PRICE_CSV: price_rows,
        STUMPAGE_CSV: stumpage_rows,
        ASSUMPTIONS_CSV: assumption_rows,
        SOURCES_CSV: source_rows,
    }
    for filename, rows in files.items():
        _write_csv(out_dir / filename, rows)

    negatives = [row for row in margin_rows if float(row["margin_cad_m3"]) < 0.0]
    negative_by_scenario = {
        scenario: sum(
            1
            for row in negatives
            if row["price_scenario"] == scenario
        )
        for scenario in SCENARIOS
    }
    negative_central = {
        scenario: sum(
            1
            for row in negatives
            if row["price_scenario"] == scenario
            and row["scenario_id"] == "base"
        )
        for scenario in SCENARIOS
    }
    price_tier_counts = {
        scenario: dict(
            sorted(
                Counter(
                    row["price_tier"]
                    for row in price_rows
                    if row["price_scenario"] == scenario
                ).items()
            )
        )
        for scenario in SCENARIOS
    }
    national = {
        row["price_scenario"]: row
        for row in summary_rows
        if row["row_type"] == "national"
    }
    generated = datetime.now(UTC).replace(microsecond=0).isoformat()
    summary = {
        "model_id": MODEL_ID,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated,
        "status": "model_estimate_base_donor_flat_sensitivity_stumpage_scaled",
        "description": (
            "13-jurisdiction margins with donor_flat as the base scenario and "
            "stumpage_scaled as a non-preferred sensitivity."
        ),
        "decision": {
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "stumpage_scaled_preferred": False,
            "stumpage_scaled_observed": False,
            "stumpage_scaled_policy_grade": False,
            "reason": (
                "v1 flat BC donor is the consistent base. The v2 stumpage-scaled "
                "proxy is a method sensitivity because dues/royalties are not "
                "delivered prices and are not comparable to the donor."
            ),
        },
        "coverage": {
            "provinces": list(PROVINCES),
            "province_count": len(PROVINCES),
            "missing": [],
            "not_estimated_count": 0,
            "margin_rows": len(margin_rows),
            "summary_rows": len(summary_rows),
            "price_rows": len(price_rows),
            "scenario_count": len(SCENARIOS),
            "scenarios": list(SCENARIOS),
            "base_scenario": BASE_SCENARIO,
            "sensitivity_scenario": SENSITIVITY_SCENARIO,
            "stumpage_extracted_count": sum(1 for ref in refs.values() if ref.extracted),
            "fallback_count": sum(1 for ref in refs.values() if not ref.extracted),
            "nu_zero_allocation_volume": allocation["NU"] == 0.0,
        },
        "price_scenarios": {
            BASE_SCENARIO: {
                "role": "base",
                "price_basis": "flat_BC_donor_delivered_proxy_not_observed",
                "price_tier_counts": price_tier_counts[BASE_SCENARIO],
            },
            SENSITIVITY_SCENARIO: {
                "role": "sensitivity",
                "price_basis": "mixed_reported_average_and_stumpage_scaled_proxy_not_observed",
                "price_tier_counts": price_tier_counts[SENSITIVITY_SCENARIO],
            },
        },
        "bc_price_blend": {
            "low_cad_m3": blend.low_cad_m3,
            "base_cad_m3": blend.base_cad_m3,
            "high_cad_m3": blend.high_cad_m3,
            "price_basis": "mill_delivered_reported_average",
        },
        "stumpage_ratio": {
            "low": ratio[0],
            "base": ratio[1],
            "high": ratio[2],
            "formula": "BC delivered price / BCTS average stumpage rate",
            "scope": (
                "BC blended Coast+Interior; BCTS does not publish a regional "
                "stumpage revenue/volume split in the materialised source."
            ),
        },
        "national_diagnostic": {
            "allocation_volume_m3": float(national[BASE_SCENARIO]["allocation_volume_m3"]),
            "base_scenario": BASE_SCENARIO,
            "donor_flat": {
                "margin_low_cad_m3": float(
                    national[BASE_SCENARIO]["margin_low_cad_m3"]
                ),
                "margin_base_cad_m3": float(
                    national[BASE_SCENARIO]["margin_base_cad_m3"]
                ),
                "margin_high_cad_m3": float(
                    national[BASE_SCENARIO]["margin_high_cad_m3"]
                ),
            },
            "stumpage_scaled_sensitivity": {
                "margin_low_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_low_cad_m3"]
                ),
                "margin_base_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_base_cad_m3"]
                ),
                "margin_high_cad_m3": float(
                    national[SENSITIVITY_SCENARIO]["margin_high_cad_m3"]
                ),
            },
            "label": NATIONAL_DIAGNOSTIC_LABEL,
            "combined_national_net_value": False,
        },
        "margins": {
            "negative_row_count": len(negatives),
            "negative_by_scenario": negative_by_scenario,
            "negative_central_case_count": negative_central,
            "negative_documented": True,
            "negative_margin_clipped": False,
        },
        "tier_coverage": {
            "price_tier_counts": price_tier_counts,
            "row_count": len(tier_rows),
            "all_model_estimate": True,
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
            "national_diagnostic_label": NATIONAL_DIAGNOSTIC_LABEL,
            "strict_open": False,
        },
        "licensing": v2.LICENSING,
        "validation": {
            "all_provinces_present": True,
            "all_rows_model_estimate": True,
            "observed_claim": False,
            "policy_grade_claim": False,
            "strict_open_claim": False,
            "base_scenario_declared": True,
            "low_base_high_ordered": True,
            "negatives_unclipped": True,
            "dues_not_comparable_to_delivered_price": True,
            "combined_national_net_value_computed": False,
            "rate_source_hash_present": all(
                len(source.sha256) == 64 for source in v2.SOURCES.values()
            ),
        },
        "source_retrieved_utc": v2.RETRIEVED_UTC,
        "summary_rows": summary_rows,
    }
    (out_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    _write_report(
        out_dir, summary, summary_rows, comparison_rows, margin_rows, tier_rows
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
                "national_diagnostic": summary["national_diagnostic"],
                "negative_margins": summary["margins"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
