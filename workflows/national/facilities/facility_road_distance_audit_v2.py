"""Expand the open road-distance audit and fit province road factors.

Version 2 extends the v1 six-province, four-pair audit to every jurisdiction
that has eligible located facilities: up to ten facilities per province,
selected deterministically at equal-frequency haversine quantiles.  Public OSRM
demo routes are requested one pair at a time at the documented 1.2 s spacing.
No paid key is used.

The fitted quantity is the province median ``road_km / haversine_km`` ratio.
Outliers are flagged with physical bounds and, when a province has at least
eight successful routes, a Tukey fence on log ratios.  The final factor is
capped to ``[1.0, 3.0]``.  Every province with successful routes keeps its own
factor; a national single factor is never substituted where province data
exist.  Small-sample and dispersion flags are reported rather than hidden.

The artifact is ``model_estimate`` / ``observed=false`` /
``policy_grade=false``.  It does not change any assignment and makes no
national claim.  No UI or public OSeCAN surface is changed.
"""

from __future__ import annotations

import json
import math
import random
import statistics
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workflows.national.facilities import facility_road_distance_audit as v1
from workflows.national.facilities.national_facility_assignment import (
    haversine_km,
    load_province_centroids,
)
from workflows.national.facilities.national_facility_inventory import PROVINCES

REPO_ROOT = v1.REPO_ROOT
DATA_ROOT = v1.DATA_ROOT
CAPACITY_CSV = v1.CAPACITY_CSV
BOUNDARY_GEOJSON = v1.BOUNDARY_GEOJSON
V6_ASSIGNMENT_CSV = v1.V6_ASSIGNMENT_CSV
OUTPUT_DIR = (
    DATA_ROOT
    / "derived/national/facilities/facility_road_distance_audit_v2"
)

ARTIFACT = "facility_road_distance_audit_v2"
ARTIFACT_VERSION = "2.0.0"
SAMPLE_PROVINCES = tuple(PROVINCES)
SAMPLE_PER_PROVINCE = 10
MIN_FACTOR = 1.0
MAX_FACTOR = 3.0
MIN_PAIR_RATIO = 0.90
MAX_PAIR_RATIO = 5.0
TUKEY_FENCE_MULTIPLIER = 1.5
TUKEY_MIN_PAIRS = 8
BOOTSTRAP_RESAMPLES = 2_000
BOOTSTRAP_SEED = 20_261_123
RATE_LIMIT_SECONDS = v1.RATE_LIMIT_SECONDS
SNAP_WARNING_KM = v1.SNAP_WARNING_KM
GLOBAL_ROAD_FACTOR = v1.ROAD_FACTOR

EVIDENCE_LABEL = v1.EVIDENCE_LABEL
OBSERVED = v1.OBSERVED
POLICY_GRADE = v1.POLICY_GRADE
ASSIGNMENT_CHANGE = False
NATIONAL_CLAIM = False

SAMPLE_FIELDS = [
    "pair_id",
    "province",
    "distance_stratum",
    "origin_lat",
    "origin_lon",
    "facility_id",
    "facility_name",
    "facility_class",
    "facility_province",
    "facility_lat",
    "facility_lon",
    "facility_status",
    "evidence_tier",
    "haversine_km",
    "global_road_factor",
    "global_road_factor_km",
    "province_road_factor",
    "province_road_factor_km",
    "osrm_status",
    "osrm_http_status",
    "osrm_road_km",
    "osrm_duration_h",
    "osrm_speed_kmh",
    "road_haversine_ratio",
    "global_proxy_road_ratio",
    "global_proxy_error_pct",
    "province_proxy_road_ratio",
    "province_proxy_error_pct",
    "ratio_used_in_fit",
    "outlier_flag",
    "outlier_reason",
    "origin_snap_km",
    "destination_snap_km",
    "snap_warning",
    "used_in_v6_primary",
    "error",
    "evidence_label",
    "observed",
    "policy_grade",
]
FACTOR_FIELDS = [
    "province",
    "province_name",
    "eligible_facilities",
    "sampled_facilities",
    "n_pairs",
    "n_ok",
    "n_failed",
    "n_outliers",
    "n_fit",
    "selection",
    "median_road_haversine_ratio",
    "sum_road_haversine_ratio",
    "iqr",
    "relative_iqr",
    "ci90_low",
    "ci90_high",
    "factor_raw",
    "factor_applied",
    "factor_basis",
    "factor_change_vs_global_pct",
    "cap_applied",
    "low_n",
    "high_dispersion",
    "fit_status",
    "flags",
    "uncertainty_note",
    "evidence_label",
    "observed",
    "policy_grade",
]
PROVENANCE_FIELDS = v1.PROVENANCE_FIELDS


class RoadDistanceAuditV2Error(RuntimeError):
    """Raised when an audit v2 invariant fails."""


def _format_float(value: float | None, digits: int = 6) -> str:
    return v1._format_float(value, digits)


def _bool_text(value: bool) -> str:
    return v1._bool_text(value)


def _sha256(path: Path) -> str:
    return v1._sha256(path)


def _write_csv(
    path: Path, fieldnames: Sequence[str], rows: Iterable[dict[str, Any]]
) -> None:
    v1._write_csv(path, fieldnames, rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    v1._write_json(path, payload)


def _percentile(values: Sequence[float], fraction: float) -> float | None:
    return v1._percentile(values, fraction)


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    return v1._distribution(values)


def _stratified_indices(count: int, target: int) -> list[int]:
    """Return one deterministic median index from each quantile stratum."""

    if count <= 0:
        return []
    if target <= 0:
        raise RoadDistanceAuditV2Error("sample target must be positive")
    if count <= target:
        return list(range(count))
    indices: list[int] = []
    for stratum in range(target):
        start = stratum * count // target
        end = (stratum + 1) * count // target
        if end <= start:
            raise RoadDistanceAuditV2Error(
                f"empty quantile stratum {stratum} for {count} facilities"
            )
        indices.append((start + end - 1) // 2)
    if len(set(indices)) != target:
        raise RoadDistanceAuditV2Error(
            f"duplicate quantile indices for {count} facilities / {target}"
        )
    return indices


def _eligible_by_province(
    census: v1.FacilityCensus,
) -> dict[str, list[v1.Facility]]:
    grouped: dict[str, list[v1.Facility]] = {province: [] for province in PROVINCES}
    for facility in census.facilities:
        if v1.is_eligible(facility):
            grouped[facility.province].append(facility)
    return grouped


def select_sample(
    census: v1.FacilityCensus,
    centroids: dict[str, Any],
    *,
    per_province: int = SAMPLE_PER_PROVINCE,
) -> tuple[v1.SamplePair, ...]:
    """Select up to ``per_province`` deterministic quantile facilities."""

    if per_province <= 0:
        raise RoadDistanceAuditV2Error("per_province must be positive")
    grouped = _eligible_by_province(census)
    sample: list[v1.SamplePair] = []
    for province in SAMPLE_PROVINCES:
        if province not in centroids:
            raise RoadDistanceAuditV2Error(
                f"missing province centroid for {province}"
            )
        located = grouped[province]
        if not located:
            continue
        origin = centroids[province]
        located.sort(
            key=lambda facility: (
                haversine_km(
                    origin.lat,
                    origin.lon,
                    facility.lat,
                    facility.lon,
                ),
                facility.facility_id,
            )
        )
        selected = _stratified_indices(len(located), per_province)
        for stratum, index in enumerate(selected, start=1):
            facility = located[index]
            sample.append(
                v1.SamplePair(
                    pair_id=f"{province}-{stratum:02d}",
                    province=province,
                    stratum=stratum,
                    facility=facility,
                    haversine_km=haversine_km(
                        origin.lat,
                        origin.lon,
                        facility.lat,
                        facility.lon,
                    ),
                )
            )
    if len({pair.facility.facility_id for pair in sample}) != len(sample):
        raise RoadDistanceAuditV2Error("sample contains duplicate facilities")
    return tuple(sample)


def _tukey_fence(values: Sequence[float]) -> tuple[float, float] | None:
    if len(values) < TUKEY_MIN_PAIRS:
        return None
    logs = [math.log(value) for value in values]
    q1 = _percentile(logs, 0.25)
    q3 = _percentile(logs, 0.75)
    if q1 is None or q3 is None:
        return None
    width = q3 - q1
    return (
        math.exp(q1 - TUKEY_FENCE_MULTIPLIER * width),
        math.exp(q3 + TUKEY_FENCE_MULTIPLIER * width),
    )


def _bootstrap_median_ci(
    values: Sequence[float], *, seed: int
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    generator = random.Random(seed)
    medians = [
        statistics.median(generator.choices(values, k=len(values)))
        for _ in range(BOOTSTRAP_RESAMPLES)
    ]
    return _percentile(medians, 0.025), _percentile(medians, 0.975)


def _flag_outliers(
    ratios: Sequence[float],
) -> tuple[list[str], tuple[float, float] | None]:
    reasons: list[str] = []
    for value in ratios:
        if not math.isfinite(value) or value <= 0.0:
            reasons.append("non_positive_or_non_finite")
        elif value < MIN_PAIR_RATIO:
            reasons.append("below_physical_floor")
        elif value > MAX_PAIR_RATIO:
            reasons.append("above_physical_cap")
        else:
            reasons.append("")
    fence = _tukey_fence(
        [value for value, reason in zip(ratios, reasons) if not reason]
    )
    if fence is not None:
        low, high = fence
        for position, value in enumerate(ratios):
            if reasons[position]:
                continue
            if value < low or value > high:
                reasons[position] = "outside_tukey_log_fence"
    return reasons, fence


def _fit_province(
    province: str,
    rows: Sequence[dict[str, Any]],
    *,
    eligible_count: int,
    sampled_count: int,
) -> dict[str, Any]:
    province_rows = [row for row in rows if row["province"] == province]
    successful = [
        row for row in province_rows if row["osrm_status"] == "ok"
    ]
    ratios = [
        float(row["osrm_road_km"]) / float(row["haversine_km"])
        for row in successful
    ]
    reasons, fence = _flag_outliers(ratios)
    included = [
        value for value, reason in zip(ratios, reasons) if not reason
    ]
    n_outliers = sum(1 for reason in reasons if reason)
    n_failed = len(province_rows) - len(successful)
    flags: list[str] = []
    if eligible_count == 0:
        return {
            "province": province,
            "province_name": v1.PROVINCES[province],
            "eligible_facilities": 0,
            "sampled_facilities": 0,
            "n_pairs": 0,
            "n_ok": 0,
            "n_failed": 0,
            "n_outliers": 0,
            "n_fit": 0,
            "selection": "no_eligible_located_facilities",
            "median_road_haversine_ratio": None,
            "sum_road_haversine_ratio": None,
            "iqr": None,
            "relative_iqr": None,
            "ci90_low": None,
            "ci90_high": None,
            "factor_raw": None,
            "factor_applied": None,
            "factor_basis": "no_data",
            "factor_change_vs_global_pct": None,
            "cap_applied": False,
            "low_n": True,
            "high_dispersion": True,
            "fit_status": "no_eligible_facilities",
            "flags": ["no_eligible_facilities"],
            "uncertainty_note": (
                "No eligible located facility exists in this jurisdiction; no "
                "road factor is fabricated."
            ),
            "evidence_label": EVIDENCE_LABEL,
            "observed": _bool_text(OBSERVED),
            "policy_grade": _bool_text(POLICY_GRADE),
        }
    if not successful:
        raise RoadDistanceAuditV2Error(
            f"{province}: {sampled_count} sampled pairs but no successful OSRM "
            "route; cannot fit a province factor"
        )
    if not included:
        raise RoadDistanceAuditV2Error(
            f"{province}: every successful pair was flagged as an outlier; "
            "cannot fit a province factor"
        )
    median_ratio = statistics.median(included)
    sum_ratio = math.fsum(
        float(successful[position]["osrm_road_km"])
        for position in range(len(successful))
        if not reasons[position]
    ) / math.fsum(
        float(successful[position]["haversine_km"])
        for position in range(len(successful))
        if not reasons[position]
    )
    q75 = _percentile(included, 0.75)
    q25 = _percentile(included, 0.25)
    iqr = (
        q75 - q25
        if len(included) >= 2 and q75 is not None and q25 is not None
        else 0.0
    )
    relative_iqr = iqr / median_ratio if median_ratio > 0.0 else None
    seed = BOOTSTRAP_SEED + sum(ord(character) for character in province)
    ci90_low, ci90_high = _bootstrap_median_ci(included, seed=seed)
    factor_raw = median_ratio
    factor_applied = min(MAX_FACTOR, max(MIN_FACTOR, factor_raw))
    cap_applied = factor_applied != factor_raw
    low_n = len(included) < 5
    high_dispersion = (
        relative_iqr is not None and relative_iqr > 0.35
    ) or len(included) < 3
    if len(included) < 3:
        flags.append("very_low_n")
    elif low_n:
        flags.append("low_n")
    if high_dispersion:
        flags.append("high_dispersion")
    if cap_applied:
        flags.append("factor_capped")
    if n_outliers:
        flags.append("outliers_excluded")
    if n_failed:
        flags.append("route_failures")
    if fence is None:
        flags.append("tukey_fence_not_applied_small_n")
    if not flags:
        flags.append("no_flags")
    fit_status = "fitted" if not low_n else "fitted_low_n"
    if cap_applied:
        fit_status = "fitted_capped"
    uncertainty_parts = [
        f"IQR {_format_float(iqr, 6)}",
        (
            "relative IQR "
            f"{_format_float(relative_iqr, 6) if relative_iqr is not None else 'n/a'}"
        ),
        (
            "90% bootstrap median CI "
            f"[{_format_float(ci90_low, 6)}, "
            f"{_format_float(ci90_high, 6)}]"
        ),
    ]
    if fence is not None:
        uncertainty_parts.append(
            "Tukey log fence "
            f"[{_format_float(fence[0], 6)}, {_format_float(fence[1], 6)}]"
        )
    return {
        "province": province,
        "province_name": v1.PROVINCES[province],
        "eligible_facilities": eligible_count,
        "sampled_facilities": sampled_count,
        "n_pairs": len(province_rows),
        "n_ok": len(successful),
        "n_failed": n_failed,
        "n_outliers": n_outliers,
        "n_fit": len(included),
        "selection": (
            "all eligible located facilities"
            if eligible_count <= SAMPLE_PER_PROVINCE
            else (
                f"{SAMPLE_PER_PROVINCE} equal-frequency haversine quantile "
                "strata; median facility per stratum"
            )
        ),
        "median_road_haversine_ratio": median_ratio,
        "sum_road_haversine_ratio": sum_ratio,
        "iqr": iqr,
        "relative_iqr": relative_iqr,
        "ci90_low": ci90_low,
        "ci90_high": ci90_high,
        "factor_raw": factor_raw,
        "factor_applied": factor_applied,
        "factor_basis": "median_road_haversine_ratio",
        "factor_change_vs_global_pct": (
            100.0 * (factor_applied - GLOBAL_ROAD_FACTOR) / GLOBAL_ROAD_FACTOR
        ),
        "cap_applied": cap_applied,
        "low_n": low_n,
        "high_dispersion": high_dispersion,
        "fit_status": fit_status,
        "flags": flags,
        "uncertainty_note": "; ".join(uncertainty_parts),
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }


def _sample_row(
    pair: v1.SamplePair,
    origin: Any,
    route: v1.RouteResult,
    *,
    factor_row: dict[str, Any],
    outlier_reason: str,
    used_in_v6: bool,
) -> dict[str, Any]:
    haversine = pair.haversine_km
    global_proxy_km = haversine * GLOBAL_ROAD_FACTOR
    factor_value = factor_row["factor_applied"]
    province_km = (
        haversine * float(factor_value)
        if factor_value is not None
        else None
    )
    row: dict[str, Any] = {
        "pair_id": pair.pair_id,
        "province": pair.province,
        "distance_stratum": pair.stratum,
        "origin_lat": _format_float(origin.lat),
        "origin_lon": _format_float(origin.lon),
        "facility_id": pair.facility.facility_id,
        "facility_name": pair.facility.name,
        "facility_class": pair.facility.facility_class,
        "facility_province": pair.facility.province,
        "facility_lat": _format_float(pair.facility.lat),
        "facility_lon": _format_float(pair.facility.lon),
        "facility_status": pair.facility.status,
        "evidence_tier": pair.facility.evidence_tier,
        "haversine_km": _format_float(haversine),
        "global_road_factor": _format_float(GLOBAL_ROAD_FACTOR),
        "global_road_factor_km": _format_float(global_proxy_km),
        "province_road_factor": _format_float(
            float(factor_value) if factor_value is not None else None,
            digits=12,
        ),
        "province_road_factor_km": _format_float(province_km),
        "osrm_status": route.status,
        "osrm_http_status": (
            route.http_status if route.http_status is not None else ""
        ),
        "osrm_road_km": _format_float(route.distance_km),
        "osrm_duration_h": _format_float(route.duration_h),
        "origin_snap_km": _format_float(route.origin_snap_km),
        "destination_snap_km": _format_float(route.destination_snap_km),
        "snap_warning": _bool_text(
            route.status == "ok"
            and max(
                route.origin_snap_km or 0.0,
                route.destination_snap_km or 0.0,
            )
            > SNAP_WARNING_KM
        ),
        "used_in_v6_primary": _bool_text(used_in_v6),
        "error": route.error,
        "evidence_label": EVIDENCE_LABEL,
        "observed": _bool_text(OBSERVED),
        "policy_grade": _bool_text(POLICY_GRADE),
    }
    if (
        route.status != "ok"
        or route.distance_km is None
        or route.duration_h is None
        or province_km is None
    ):
        row.update(
            {
                "osrm_speed_kmh": "",
                "road_haversine_ratio": "",
                "global_proxy_road_ratio": "",
                "global_proxy_error_pct": "",
                "province_proxy_road_ratio": "",
                "province_proxy_error_pct": "",
                "ratio_used_in_fit": "false",
                "outlier_flag": _bool_text(bool(outlier_reason)),
                "outlier_reason": outlier_reason,
            }
        )
        return row
    road_km = route.distance_km
    duration_h = route.duration_h
    row.update(
        {
            "osrm_speed_kmh": _format_float(road_km / duration_h),
            "road_haversine_ratio": _format_float(
                road_km / haversine, digits=9
            ),
            "global_proxy_road_ratio": _format_float(
                global_proxy_km / road_km, digits=9
            ),
            "global_proxy_error_pct": _format_float(
                100.0 * (global_proxy_km - road_km) / road_km, digits=6
            ),
            "province_proxy_road_ratio": _format_float(
                province_km / road_km, digits=9
            ),
            "province_proxy_error_pct": _format_float(
                100.0 * (province_km - road_km) / road_km, digits=6
            ),
            "ratio_used_in_fit": _bool_text(not outlier_reason),
            "outlier_flag": _bool_text(bool(outlier_reason)),
            "outlier_reason": outlier_reason,
        }
    )
    return row


def _comparison(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successful = [row for row in rows if row["osrm_status"] == "ok"]
    haversine_values = [float(row["haversine_km"]) for row in successful]
    road_values = [float(row["osrm_road_km"]) for row in successful]
    global_errors = [
        float(row["global_proxy_error_pct"]) for row in successful
    ]
    province_errors = [
        float(row["province_proxy_error_pct"]) for row in successful
    ]
    return {
        "n_ok": len(successful),
        "n_failed": len(rows) - len(successful),
        "haversine_km_total": math.fsum(haversine_values),
        "road_km_total": math.fsum(road_values),
        "sample_fitted_global_road_factor": (
            math.fsum(road_values) / math.fsum(haversine_values)
            if haversine_values
            else None
        ),
        "global_abs_proxy_error_pct": _distribution(
            [abs(value) for value in global_errors]
        ),
        "province_abs_proxy_error_pct": _distribution(
            [abs(value) for value in province_errors]
        ),
        "share_within_10_percent": {
            "global": (
                math.fsum(
                    1.0 for value in global_errors if abs(value) <= 10.0
                )
                / len(global_errors)
                if global_errors
                else None
            ),
            "province": (
                math.fsum(
                    1.0 for value in province_errors if abs(value) <= 10.0
                )
                / len(province_errors)
                if province_errors
                else None
            ),
        },
        "road_haversine_ratio": _distribution(
            [float(row["road_haversine_ratio"]) for row in successful]
        ),
    }


def _validation(
    *,
    sample: Sequence[v1.SamplePair],
    rows: Sequence[dict[str, Any]],
    factor_rows: Sequence[dict[str, Any]],
    summary: dict[str, Any],
    census: v1.FacilityCensus,
) -> dict[str, bool]:
    expected_counts = summary["sample"]["per_province_counts"]
    expected_total = sum(expected_counts.values())
    successful = [row for row in rows if row["osrm_status"] == "ok"]
    ratio_errors: list[str] = []
    numeric_errors: list[str] = []
    for row in successful:
        haversine = float(row["haversine_km"])
        road = float(row["osrm_road_km"])
        global_proxy = float(row["global_road_factor_km"])
        province_proxy = float(row["province_road_factor_km"])
        factor = float(row["province_road_factor"])
        if abs(global_proxy - haversine * GLOBAL_ROAD_FACTOR) > 1e-5:
            ratio_errors.append(f"{row['pair_id']}: global proxy")
        if abs(province_proxy - haversine * factor) > 1e-5:
            ratio_errors.append(f"{row['pair_id']}: province proxy")
        if abs(float(row["road_haversine_ratio"]) - road / haversine) > 1e-6:
            ratio_errors.append(f"{row['pair_id']}: road/haversine")
        values = [haversine, road, global_proxy, province_proxy, factor]
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            numeric_errors.append(row["pair_id"])
    fitted = [row for row in factor_rows if row["factor_applied"] is not None]
    factor_bounds = all(
        MIN_FACTOR <= float(row["factor_applied"]) <= MAX_FACTOR
        for row in fitted
    )
    factor_finite = all(
        math.isfinite(float(row["factor_applied"])) for row in fitted
    )
    n_fit_matches = all(
        row["n_fit"] == row["n_ok"] - row["n_outliers"]
        for row in factor_rows
    )
    province_factor_coverage = all(
        row["factor_applied"] is not None
        for row in factor_rows
        if row["eligible_facilities"] > 0
    )
    return {
        "sample_pair_count": len(sample) == expected_total,
        "sample_provinces": set(summary["sample"]["provinces"])
        == set(SAMPLE_PROVINCES),
        "per_province_count": all(
            summary["sample"]["per_province_counts"].get(province)
            == expected_counts[province]
            for province in SAMPLE_PROVINCES
        ),
        "unique_facilities": len({pair.facility.facility_id for pair in sample})
        == len(sample),
        "positive_haversine": all(pair.haversine_km > 0.0 for pair in sample),
        "facility_census": (
            census.total_records == v1.EXPECTED_TOTAL_RECORDS
            and census.mapped_records == v1.EXPECTED_MAPPED_RECORDS
        ),
        "ratio_arithmetic": not ratio_errors,
        "factor_arithmetic": n_fit_matches,
        "factor_bounds": factor_bounds,
        "factor_finite": factor_finite,
        "province_factor_coverage": province_factor_coverage,
        "no_nan_or_negative": not numeric_errors,
        "labels": (
            summary["labels"]["evidence_label"] == EVIDENCE_LABEL
            and summary["labels"]["observed"] is False
            and summary["labels"]["policy_grade"] is False
        ),
        "no_assignment_change": (
            summary["labels"]["assignment_change"] is False
            and summary["labels"]["national_claim"] is False
        ),
        "repository_hygiene": True,
    }


def _report(summary: dict[str, Any]) -> str:
    sample = summary["sample"]
    overall = summary["comparison"]["overall"]
    lines = [
        "# Facility road-distance audit v2",
        "",
        f"Generated: {summary['generated_utc']}",
        "",
        (
            "Status: **model estimate**. This audit fits province road factors "
            "but does not change any assignment and makes no national claim."
        ),
        "",
        "## Routing source",
        "",
        f"- Provider: {summary['routing']['provider']}",
        (f"- Endpoint: `{summary['routing']['base_url']}` "
        f"({summary['routing']['profile']} profile)"),
        (f"- Road data: {summary['routing']['osm_data']} "
        f"({summary['routing']['licence']}, "
        f"{summary['routing']['attribution']})"),
        (f"- Requests: {summary['routing']['requests_ok']} ok / "
        f"{summary['routing']['requests_attempted']} attempted; "
        f"{summary['routing']['requests_failed']} failed."),
        f"- Rate limit: one request every {RATE_LIMIT_SECONDS:.1f} s.",
        "",
        "## Sample",
        "",
        (
            f"- Target: up to {SAMPLE_PER_PROVINCE} eligible facilities per "
            f"province across {len(SAMPLE_PROVINCES)} jurisdictions."
        ),
        (f"- Realized: {sample['n_pairs']} pairs in "
        f"{len(sample['provinces_with_pairs'])} jurisdictions. "
        "Jurisdictions with fewer eligible facilities are sampled in full; "
        "NT and NU have no eligible located facility and are not fabricated."),
        ("- Selection: deterministic equal-frequency haversine quantile "
        "strata, median facility per stratum."),
        "",
        "## Province factors",
        "",
        ("The factor is the province median `road km / haversine km`; the "
        "global 1.3 screen is shown only for comparison. A national factor is "
        "never substituted where province data exist."),
        "",
        (
            "| Province | Eligible | Sampled | OK | Outliers | Fit n | "
            "Median ratio | IQR | 90% CI | Factor | Flags |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",    ]
    for row in summary["province_factors"]:
        lines.append(
            f"| {row['province']} | {row['eligible_facilities']} | "
            f"{row['sampled_facilities']} | {row['n_ok']} | "
            f"{row['n_outliers']} | {row['n_fit']} | "
            f"{_format_float(row['median_road_haversine_ratio'], 6) or 'n/a'} | "
            f"{_format_float(row['iqr'], 6) or 'n/a'} | "
            f"{_format_float(row['ci90_low'], 6) or 'n/a'}–"
            f"{_format_float(row['ci90_high'], 6) or 'n/a'} | "
            f"{_format_float(row['factor_applied'], 6) or 'n/a'} | "
            f"{row['flags']} |"
        )
    lines.extend(
        [
            "",
            "## Proxy comparison",
            "",
            "| Metric | Global 1.3 | Province factor |",
            "|---|---|---|",
            (
                "| Mean absolute proxy error % | "
                f"{_format_float(overall['global_abs_proxy_error_pct']['mean'], 6)} | "
                f"{_format_float(overall['province_abs_proxy_error_pct']['mean'], 6)} |"
            ),
            (
                "| Median absolute proxy error % | "
                f"{_format_float(overall['global_abs_proxy_error_pct']['median'], 6)} | "
                f"{_format_float(overall['province_abs_proxy_error_pct']['median'], 6)} |"
            ),
            (
                "| Within 10% of routed distance | "
                f"{_format_float(overall['share_within_10_percent']['global'], 6)} | "
                f"{_format_float(overall['share_within_10_percent']['province'], 6)} |"
            ),
            "",
            "## Method and limits",
            "",
            (
                "- A routed road distance cannot be materially shorter than "
                "the great-circle distance; pairs below 0.90 are failed, not "
                "fitted."
            ),
            (
                "- Outliers use physical bounds [0.90, 5.0] and, at n >= 8, a "
                "1.5x Tukey fence on log ratios. Excluded pairs remain in the "
                "sample table with `ratio_used_in_fit=false`."
            ),
            (
                "- Factors are capped to [1.0, 3.0]. Cap, low-n, wide "
                "dispersion, route failure, and small-n fence flags are "
                "reported in `province_road_factors.csv`."
            ),
            (
                "- OSRM routes start and end at the nearest road point; the "
                "off-road connector is not included. Large snap distances are "
                "reported and are a source of uncertainty, not a fabricated "
                "correction."
            ),
            (
                "- The OSRM car profile may route through the United States "
                "where that is the shortest path; no border delay or customs "
                "cost is modelled."
            ),
            (
                "- Distances are car-road distances. Resource roads, winter "
                "roads, ferries, weight limits, and log-truck restrictions are "
                "not validated."
            ),
            "",
            "## Labels",
            "",
            "- `model_estimate`; `observed=false`; `policy_grade=false`.",
            (
                "- No assignment change; no national single factor; no legal, "
                "AAC, or operating-status claim."
            ),
            "- No UI or public OSeCAN surface is changed.",
            "",
            "## Provenance",
            "",
        ]
    )
    for source in summary["provenance"]["sources"]:
        location = f"`{source['path']}` " if source["path"] else ""
        lines.append(
            f"- {source['name']}: {location}"
            f"({source['licence']}); `{source['url']}`"
        )
    lines.extend(
        [
            "",
            (
                "Reproduce: `.venv/bin/python -m "
                "workflows.national.facilities.facility_road_distance_audit_v2`"
            ),
            "",
        ]
    )
    return "\n".join(lines)


def build(
    output_dir: Path = OUTPUT_DIR,
    *,
    route_fetcher: Callable[..., v1.RouteResult] = v1.fetch_osrm_route,
    sleeper: Callable[[float], None] = time.sleep,
    deletion_checker: Callable[[], None] = (
        v1.assert_no_unexpected_deletions
    ),
) -> dict[str, Any]:
    """Run the expanded audit and write the data artifact."""

    deletion_checker()
    generated_utc = datetime.now(UTC).replace(microsecond=0).isoformat()
    census = v1.load_facilities()
    centroids, centroid_methods = load_province_centroids(BOUNDARY_GEOJSON)
    grouped = _eligible_by_province(census)
    sample = select_sample(census, centroids)
    used_in_v6 = v1.load_v6_used_facilities()
    sampled_counts = {
        province: sum(1 for pair in sample if pair.province == province)
        for province in SAMPLE_PROVINCES
    }
    expected_counts = {
        province: min(SAMPLE_PER_PROVINCE, len(grouped[province]))
        for province in SAMPLE_PROVINCES
    }
    if sampled_counts != expected_counts:
        raise RoadDistanceAuditV2Error(
            f"sample counts {sampled_counts} do not match expected "
            f"{expected_counts}"
        )

    routing_rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for position, pair in enumerate(sample):
        origin = centroids[pair.province]
        url = v1.osrm_route_url(origin, pair.facility)
        route = route_fetcher(url, expected_haversine_km=pair.haversine_km)
        routing_rows.append(
            {
                "pair_id": pair.pair_id,
                "province": pair.province,
                "facility_id": pair.facility.facility_id,
                "haversine_km": pair.haversine_km,
                "route": route,
            }
        )
        snapshots.append(
            v1._route_snapshot(
                pair,
                url,
                route,
                requested_at_utc=datetime.now(UTC)
                .replace(microsecond=0)
                .isoformat(),
            )
        )
        if position < len(sample) - 1:
            sleeper(RATE_LIMIT_SECONDS)

    successful = [
        item for item in routing_rows if item["route"].status == "ok"
    ]
    if not successful:
        errors = "; ".join(
            f"{item['pair_id']}: {item['route'].error}"
            for item in routing_rows[:3]
        )
        raise RoadDistanceAuditV2Error(
            "all OSRM route requests failed; no distances were fabricated. "
            f"First failures: {errors}"
        )

    probe_rows = [
        {
            "pair_id": item["pair_id"],
            "province": item["province"],
            "haversine_km": _format_float(item["haversine_km"]),
            "osrm_status": item["route"].status,
            "osrm_road_km": _format_float(item["route"].distance_km),
        }
        for item in routing_rows
    ]
    factor_rows = [
        _fit_province(
            province,
            probe_rows,
            eligible_count=len(grouped[province]),
            sampled_count=sampled_counts[province],
        )
        for province in SAMPLE_PROVINCES
    ]
    factor_by_province = {row["province"]: row for row in factor_rows}
    outlier_by_pair: dict[str, str] = {}
    for province in SAMPLE_PROVINCES:
        province_probe = [
            row for row in probe_rows if row["province"] == province
        ]
        ratios = [
            float(row["osrm_road_km"]) / float(row["haversine_km"])
            for row in province_probe
            if row["osrm_status"] == "ok"
        ]
        reasons, _ = _flag_outliers(ratios)
        reason_iter = iter(reasons)
        for row in province_probe:
            if row["osrm_status"] == "ok":
                outlier_by_pair[row["pair_id"]] = next(reason_iter)
            else:
                outlier_by_pair[row["pair_id"]] = ""

    rows: list[dict[str, Any]] = []
    for item in routing_rows:
        pair = next(
            candidate for candidate in sample if candidate.pair_id == item["pair_id"]
        )
        rows.append(
            _sample_row(
                pair,
                centroids[pair.province],
                item["route"],
                factor_row=factor_by_province[pair.province],
                outlier_reason=outlier_by_pair[pair.pair_id],
                used_in_v6=pair.facility.facility_id in used_in_v6,
            )
        )

    overall = _comparison(rows)
    failures = [
        {
            "pair_id": row["pair_id"],
            "facility_id": row["facility_id"],
            "http_status": row["osrm_http_status"],
            "error": row["error"],
        }
        for row in rows
        if row["osrm_status"] != "ok"
    ]
    origin_snaps = [
        float(row["origin_snap_km"])
        for row in rows
        if row["osrm_status"] == "ok" and row["origin_snap_km"] != ""
    ]
    destination_snaps = [
        float(row["destination_snap_km"])
        for row in rows
        if row["osrm_status"] == "ok" and row["destination_snap_km"] != ""
    ]
    fitted_count = sum(
        1 for row in factor_rows if row["factor_applied"] is not None
    )
    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "complete" if not failures else "partial",
        "labels": {
            "evidence_label": EVIDENCE_LABEL,
            "observed": OBSERVED,
            "policy_grade": POLICY_GRADE,
            "assignment_change": ASSIGNMENT_CHANGE,
            "national_claim": NATIONAL_CLAIM,
        },
        "routing": {
            "provider": "OSRM public demo server",
            "base_url": v1.OSRM_BASE_URL,
            "profile": v1.OSRM_PROFILE,
            "osm_data": "OpenStreetMap road network",
            "licence": v1.OSRM_LICENCE,
            "attribution": v1.OSRM_ATTRIBUTION,
            "usage_policy": (
                "Public demo server, best-effort; low-rate requests at "
                f"{RATE_LIMIT_SECONDS:.1f} s spacing for this province-factor "
                "audit only. No paid API key."
            ),
            "requests_attempted": len(sample),
            "requests_ok": len(successful),
            "requests_failed": len(failures),
            "rate_limit_seconds": RATE_LIMIT_SECONDS,
            "max_attempts": v1.MAX_ATTEMPTS,
            "user_agent": v1.USER_AGENT,
            "snap": {
                "warning_threshold_km": SNAP_WARNING_KM,
                "origin_snap_km": _distribution(origin_snaps),
                "destination_snap_km": _distribution(destination_snaps),
                "pairs_over_warning_threshold": sum(
                    1 for row in rows if row["snap_warning"] == "true"
                ),
            },
            "failures": failures,
        },
        "sample": {
            "provinces": list(SAMPLE_PROVINCES),
            "provinces_with_pairs": [
                province
                for province in SAMPLE_PROVINCES
                if sampled_counts[province] > 0
            ],
            "per_province_target": SAMPLE_PER_PROVINCE,
            "per_province_counts": sampled_counts,
            "eligible_counts": {
                province: len(grouped[province])
                for province in SAMPLE_PROVINCES
            },
            "n_pairs": len(sample),
            "selection": (
                "deterministic equal-frequency haversine quantile strata; "
                "median eligible facility per stratum; all eligible facilities "
                "where a province has fewer than the target"
            ),
            "universe_records": census.total_records,
            "universe_mapped_records": census.mapped_records,
            "universe_unmapped_records": census.unmapped_records,
            "eligible_filter": (
                "facility_class in "
                f"{list(v1.RELEVANT_CLASSES)} and status not in "
                f"{sorted(v1.NON_OPERATING_STATUSES)}"
            ),
            "centroid_methods": centroid_methods,
        },
        "comparison": {
            "global_road_factor": GLOBAL_ROAD_FACTOR,
            "factor_basis": "median_road_haversine_ratio",
            "factor_cap": [MIN_FACTOR, MAX_FACTOR],
            "overall": overall,
        },
        "province_factors": factor_rows,
        "factors_fitted": fitted_count,
        "validation": {},
        "caveats": [
            (
                "All values are model estimates (`model_estimate`); "
                "`observed=false` and `policy_grade=false`."
            ),
            (
                "The sample is distance-stratified and province-stratified; "
                "it is not a random national sample and does not support a "
                "national mean or a national single factor."
            ),
            (
                "OSRM demo is a public best-effort service; results are not a "
                "production routing service or a guarantee of current road "
                "conditions."
            ),
            (
                "Distances are car-road distances. Resource roads, winter "
                "roads, ferries, weight limits, and log-truck restrictions are "
                "not validated."
            ),
            (
                "OSRM may route through the United States where that is the "
                "shortest road path; no border delay or customs cost is "
                "modelled."
            ),
            (
                "Endpoint snap distances are reported; large snaps indicate "
                "that the province centroid or facility coordinate is not on "
                "the road network. OSRM routes omit the off-road connector, so "
                "routed distance is a lower-bound comparison."
            ),
            (
                "No facility assignment is changed by this audit and no UI or "
                "public OSeCAN surface is touched."
            ),
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.facility_road_distance_audit_v2"
        ),
    }

    validation = _validation(
        sample=sample,
        rows=rows,
        factor_rows=factor_rows,
        summary=summary,
        census=census,
    )
    summary["validation"] = {
        "checks": validation,
        "all_passed": all(validation.values()),
    }
    if not summary["validation"]["all_passed"]:
        raise RoadDistanceAuditV2Error(
            f"validation failed: {summary['validation']['checks']}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        output_dir / "facility_road_distance_sample.csv",
        SAMPLE_FIELDS,
        rows,
    )
    _write_csv(
        output_dir / "province_road_factors.csv",
        FACTOR_FIELDS,
        factor_rows,
    )
    _write_json(
        output_dir / "osrm_routes.json",
        {"routes": snapshots},
    )

    input_specs = [
        (
            "national_facility_capacity_v3",
            "input",
            "National facility capacity v3",
            CAPACITY_CSV,
            "Facility coordinates and eligibility fields",
            "project-derived; see source metadata",
        ),
        (
            "canada_adm1_geoboundaries_simplified",
            "input",
            "GeoBoundaries Canada ADM1 simplified",
            BOUNDARY_GEOJSON,
            "Province ADM1 centroids for origins",
            "CC BY 4.0",
        ),
    ]
    if V6_ASSIGNMENT_CSV.is_file():
        input_specs.append(
            (
                "national_facility_assignment_v6",
                "context",
                "National facility assignment v6",
                V6_ASSIGNMENT_CSV,
                "Context only: marks facilities used in the v6 primary run",
                "project-derived; see source metadata",
            )
        )
    sources = [
        {
            "source_id": source_id,
            "role": role,
            "name": name,
            "url": (
                "https://github.com/UBC-FRESH/clews-c2070-fresh-data"
                if role == "input"
                else "https://github.com/UBC-FRESH/clews-c2070-fresh"
            ),
            "licence": licence,
            "retrieved_utc": generated_utc,
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": _sha256(path),
            "notes": notes,
        }
        for source_id, role, name, path, notes, licence in input_specs
    ]
    sources.extend(
        [
            {
                "source_id": "osrm_demo",
                "role": "routing",
                "name": "OSRM public demo server",
                "url": f"{v1.OSRM_BASE_URL}/route/v1/{v1.OSRM_PROFILE}",
                "licence": v1.OSRM_LICENCE,
                "retrieved_utc": generated_utc,
                "path": "osrm_routes.json",
                "sha256": _sha256(output_dir / "osrm_routes.json"),
                "notes": (
                    "Route distance and duration for the province-factor "
                    "sample; raw responses stored in osrm_routes.json"
                ),
            },
            {
                "source_id": "openstreetmap",
                "role": "road_data",
                "name": "OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/copyright",
                "licence": v1.OSRM_LICENCE,
                "retrieved_utc": generated_utc,
                "path": "",
                "sha256": "",
                "notes": (
                    "Road network used by the OSRM demo server; attribution "
                    f"{v1.OSRM_ATTRIBUTION}"
                ),
            },
        ]
    )
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
        "sample_csv": "facility_road_distance_sample.csv",
        "province_factors_csv": "province_road_factors.csv",
        "routes_json": "osrm_routes.json",
        "provenance_csv": "provenance.csv",
        "report_md": "FACILITY_ROAD_DISTANCE_AUDIT_V2.md",
    }
    _write_json(output_dir / "summary.json", summary)
    (output_dir / "FACILITY_ROAD_DISTANCE_AUDIT_V2.md").write_text(
        _report(summary), encoding="utf-8"
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
        "routing": {
            key: summary["routing"][key]
            for key in (
                "provider",
                "base_url",
                "profile",
                "licence",
                "attribution",
                "requests_attempted",
                "requests_ok",
                "requests_failed",
                "rate_limit_seconds",
            )
        },
        "sample": {
            "n_pairs": summary["sample"]["n_pairs"],
            "provinces": summary["sample"]["provinces"],
            "per_province_target": summary["sample"]["per_province_target"],
            "per_province_counts": summary["sample"]["per_province_counts"],
        },
        "factors_fitted": summary["factors_fitted"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(output_dir / "manifest.json", manifest)
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "n_pairs": summary["sample"]["n_pairs"],
                "n_ok": summary["comparison"]["overall"]["n_ok"],
                "n_failed": summary["routing"]["requests_failed"],
                "factors_fitted": summary["factors_fitted"],
                "validation": summary["validation"]["all_passed"],
                "output_dir": str(OUTPUT_DIR.relative_to(REPO_ROOT)),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
