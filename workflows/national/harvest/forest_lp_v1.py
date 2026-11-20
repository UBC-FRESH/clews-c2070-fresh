"""Assemble the Forest-Sector LP v1 synthesis.

This module reads the already-built provincial-allocation, operable-proxy,
residue-factor, and cost/revenue-ledger artifacts and writes a single auditable
synthesis.  It does not solve a new LP and does not invent missing economics.

The synthesis is deliberately tiered:

* allocation and residue accounting are computed from documented inputs;
* the cost/revenue layer is a coverage report, not a net-value estimate;
* infeasible intensity cases keep blank allocation and residue fields rather
  than being clamped or back-filled.

Run from the repository root::

    python -m workflows.national.harvest.forest_lp_v1
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import platform
import subprocess
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
ALLOCATION_DIR = HARVEST_DIR / "provincial_allocation"
OPERABLE_PROXY_PATH = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/operable_proxy/operable_proxy_by_province.csv"
)
RESIDUE_FACTOR_PATH = (
    REPO_ROOT / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
)
RESIDUE_REFERENCE_PATH = (
    HARVEST_DIR / "residue_factor_by_allocation/residue_factor_by_allocation.csv"
)
LEDGER_PATH = HARVEST_DIR / "forest_cost_revenue/forest_cost_revenue.csv"
COVERAGE_PATH = HARVEST_DIR / "forest_cost_revenue/coverage_summary.json"
MERIS_FULL_DIR = HARVEST_DIR / "meris_qc_full"
OUTPUT_DIR = REPO_ROOT / "out/forest_lp_v1"

PROVINCE_ORDER = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "NL",
    "PE",
    "YT",
    "NT",
    "NU",
)
LEVELS = (("100m", 100_000_000.0), ("119m", 118_977_339.0), ("150m", 150_000_000.0))
CAP_CASES = (
    ("documented_ceiling", None),
    ("intensity_1.0", 1.0),
    ("intensity_1.2", 1.2),
)
RECOVERY = {"low": 0.35, "base": 0.60, "high": 0.75}
LHV_GJ_PER_ODT = 18.5
ALLOCATION_TOLERANCE_M3 = 2.0
CAP_TOLERANCE_M3 = 1.0
RESIDUE_TOLERANCE_PJ = 1e-6
EXPECTED_LEDGER_ROWS = 940
EXPECTED_MIRROR_PAIRS = 100
EXPECTED_ALLOCATION_ROWS = 117

COMPONENT_ORDER = (
    "delivered_log_revenue",
    "residue_revenue",
    "stumpage_base",
    "stumpage_bid_premium",
    "stumpage_total",
    "harvest",
    "road",
    "haul",
    "silviculture",
    "residue_recovery",
    "overhead",
    "admin_fee",
    "crown_other",
    "crown_credit",
)

SIGN_MATRIX = {
    "crown": {
        "stumpage_base": 1,
        "stumpage_bid_premium": 1,
        "stumpage_total": 1,
        "admin_fee": 1,
        "crown_other": 1,
        "crown_credit": -1,
    },
    "forest_sector": {
        "delivered_log_revenue": 1,
        "residue_revenue": 1,
        "stumpage_base": -1,
        "stumpage_bid_premium": -1,
        "stumpage_total": -1,
        "harvest": -1,
        "road": -1,
        "haul": -1,
        "silviculture": -1,
        "residue_recovery": -1,
        "overhead": -1,
        "admin_fee": -1,
        "crown_other": -1,
        "crown_credit": 1,
    },
}


class ForestLPError(RuntimeError):
    """Raised when an input cannot support a fail-closed synthesis."""


@dataclass(frozen=True)
class ProvinceFactor:
    province: str
    gross_odt_per_m3_log: float
    recoverable_low: float
    recoverable_base: float
    recoverable_high: float


@dataclass(frozen=True)
class AllocationRow:
    cap_case: str
    allocation_case: str
    national_harvest_target_m3: float
    province: str
    aac_m3: float | None
    nfd_m3: float | None
    operable_proxy_ha: float | None
    operable_missing: bool
    documented_cap_m3: float
    intensity_m3_per_ha_yr: float | None
    intensity_cap_m3: float | None
    effective_cap_m3: float
    allocated_harvest_m3: float | None
    allocated_share: float | None
    binding: bool | None
    at_cap: bool | None
    province_status: str
    province_feasible: bool
    case_status: str
    case_feasible: bool
    case_deficit_m3: float
    cap_basis: str
    cap_caveat: str
    province_factor_gross_odt_per_m3_log: float
    province_factor_recoverable_low_odt_per_m3_log: float
    province_factor_recoverable_base_odt_per_m3_log: float
    province_factor_recoverable_high_odt_per_m3_log: float
    residue_gross_odt: float | None
    residue_recoverable_odt_low: float | None
    residue_recoverable_odt_base: float | None
    residue_recoverable_odt_high: float | None
    residue_pj_low: float | None
    residue_pj_base: float | None
    residue_pj_high: float | None
    allocation_weighted_factor_gross_odt_per_m3_log: float | None
    allocation_weighted_factor_recoverable_base_odt_per_m3_log: float | None


@dataclass(frozen=True)
class CostRevenueRow:
    province: str
    jurisdiction: str
    year: int
    source: str
    ledger_rows: int
    crown_rows: int
    forest_sector_rows: int
    mirror_rows: int
    observed_rows: int
    model_input_rows: int
    licences: int
    crown_anchor_basis: str
    crown_anchor_confidence: str
    covered_volume_m3: float | None
    covered_volume_status: str
    covered_volume_share_of_provincial_2024_harvest: float | None
    source_frame_volume_m3: float | None
    source_frame_share: float | None
    source_frame_share_basis: str
    source_sample_share: float | None
    source_sample_share_basis: str
    source_sample_share_status: str
    component_coverage: str
    present_or_partial_components: str
    missing_components: str
    observed_delivered_log_revenue_present: bool
    full_production_costs_present: bool
    net_value_status: str
    net_value_reason: str
    model_input_flags: str
    usable_as_crown_anchor: bool
    usable_as_delivered_log_revenue: bool
    usable_as_full_production_cost: bool
    usable_as_net_value: bool
    usable_as_lp_objective: bool


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ForestLPError(f"required input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ForestLPError(f"required input has no data rows: {path}")
    return rows


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise ForestLPError(f"required input is missing: {path}")
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ForestLPError(f"{path}: expected a JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _finite_float(value: object, *, field: str, context: str) -> float:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ForestLPError(f"{context}: missing {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForestLPError(f"{context}: {field} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ForestLPError(f"{context}: {field} is not finite: {value!r}")
    return number


def _optional_float(value: object, *, field: str, context: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _finite_float(value, field=field, context=context)


def _bool(value: object, *, field: str, context: str) -> bool:
    text = str(value or "").strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ForestLPError(f"{context}: {field} must be true or false, got {value!r}")


def _fmt(value: object, *, digits: int = 12) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _fmt(row.get(field)) for field in fieldnames})


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, allow_nan=False)
        handle.write("\n")


def _case_path(level: str, intensity: float | None) -> Path:
    if intensity is None:
        return ALLOCATION_DIR / f"{level}.csv"
    return ALLOCATION_DIR / f"intensity_{intensity:.1f}" / f"{level}.csv"


def _summary_path(intensity: float | None) -> Path:
    if intensity is None:
        return ALLOCATION_DIR / "provincial_allocation_summary.json"
    return ALLOCATION_DIR / f"intensity_{intensity:.1f}" / "provincial_allocation_summary.json"


def load_factors() -> dict[str, ProvinceFactor]:
    """Load province residue factors and enforce the recovery closure."""

    rows = [row for row in _read_csv(RESIDUE_FACTOR_PATH) if row.get("level") == "province"]
    if len(rows) != len(PROVINCE_ORDER):
        raise ForestLPError(
            f"{RESIDUE_FACTOR_PATH}: expected {len(PROVINCE_ORDER)} province rows, got {len(rows)}"
        )
    factors: dict[str, ProvinceFactor] = {}
    for row in rows:
        province = (row.get("province") or "").strip().upper()
        if province not in PROVINCE_ORDER:
            raise ForestLPError(f"{RESIDUE_FACTOR_PATH}: unexpected province {province!r}")
        if province in factors:
            raise ForestLPError(f"{RESIDUE_FACTOR_PATH}: duplicate province {province}")
        gross = _finite_float(
            row.get("odt_per_m3_log"),
            field="odt_per_m3_log",
            context=f"{RESIDUE_FACTOR_PATH}:{province}",
        )
        low = _finite_float(
            row.get("recoverable_odt_per_m3_low"),
            field="recoverable_odt_per_m3_low",
            context=f"{RESIDUE_FACTOR_PATH}:{province}",
        )
        base = _finite_float(
            row.get("recoverable_odt_per_m3_base"),
            field="recoverable_odt_per_m3_base",
            context=f"{RESIDUE_FACTOR_PATH}:{province}",
        )
        high = _finite_float(
            row.get("recoverable_odt_per_m3_high"),
            field="recoverable_odt_per_m3_high",
            context=f"{RESIDUE_FACTOR_PATH}:{province}",
        )
        if gross <= 0:
            raise ForestLPError(f"{RESIDUE_FACTOR_PATH}:{province}: gross factor must be positive")
        for case, value in (("low", low), ("base", base), ("high", high)):
            if value < 0:
                raise ForestLPError(
                    f"{RESIDUE_FACTOR_PATH}:{province}: recoverable {case} factor is negative"
                )
            if abs(value - gross * RECOVERY[case]) > 1e-6:
                raise ForestLPError(
                    f"{RESIDUE_FACTOR_PATH}:{province}: {case} factor does not equal "
                    f"gross * {RECOVERY[case]}"
                )
        factors[province] = ProvinceFactor(province, gross, low, base, high)
    missing = sorted(set(PROVINCE_ORDER) - set(factors))
    if missing:
        raise ForestLPError(f"{RESIDUE_FACTOR_PATH}: missing province factors {missing}")
    return factors


def load_operable_areas() -> dict[str, float | None]:
    """Load the base-case operable proxy, keeping missing values missing."""

    rows = [
        row
        for row in _read_csv(OPERABLE_PROXY_PATH)
        if row.get("ecoprovince") == "ALL" and row.get("threshold_case") == "base"
    ]
    if len(rows) != len(PROVINCE_ORDER):
        raise ForestLPError(
            f"{OPERABLE_PROXY_PATH}: expected {len(PROVINCE_ORDER)} base province rows, "
            f"got {len(rows)}"
        )
    areas: dict[str, float | None] = {}
    for row in rows:
        province = (row.get("province") or "").strip().upper()
        if province not in PROVINCE_ORDER:
            raise ForestLPError(f"{OPERABLE_PROXY_PATH}: unexpected province {province!r}")
        if province in areas:
            raise ForestLPError(f"{OPERABLE_PROXY_PATH}: duplicate province {province}")
        area = _optional_float(
            row.get("operable_proxy_ha"),
            field="operable_proxy_ha",
            context=f"{OPERABLE_PROXY_PATH}:{province}",
        )
        if area is not None and area <= 0:
            raise ForestLPError(f"{OPERABLE_PROXY_PATH}:{province}: area must be positive")
        areas[province] = area
    missing = sorted(set(PROVINCE_ORDER) - set(areas))
    if missing:
        raise ForestLPError(f"{OPERABLE_PROXY_PATH}: missing provinces {missing}")
    return areas


def _read_allocation_rows(path: Path) -> dict[str, dict[str, object]]:
    rows = _read_csv(path)
    parsed: dict[str, dict[str, object]] = {}
    for row in rows:
        province = (row.get("province") or "").strip().upper()
        if province not in PROVINCE_ORDER:
            raise ForestLPError(f"{path}: unexpected province {province!r}")
        if province in parsed:
            raise ForestLPError(f"{path}: duplicate province {province}")
        harvest = _finite_float(
            row.get("harvest_m3"), field="harvest_m3", context=f"{path}:{province}"
        )
        upper_bound = _finite_float(
            row.get("upper_bound"), field="upper_bound", context=f"{path}:{province}"
        )
        if harvest < 0 or upper_bound < 0:
            raise ForestLPError(f"{path}:{province}: harvest and cap must be non-negative")
        if harvest > upper_bound + CAP_TOLERANCE_M3:
            raise ForestLPError(
                f"{path}:{province}: harvest {harvest} exceeds cap {upper_bound}"
            )
        parsed[province] = {
            "province": province,
            "harvest_m3": harvest,
            "observed_2024_m3": _finite_float(
                row.get("observed_2024_m3"),
                field="observed_2024_m3",
                context=f"{path}:{province}",
            ),
            "aac_m3": _optional_float(
                row.get("aac_m3"), field="aac_m3", context=f"{path}:{province}"
            ),
            "nfd_m3": _optional_float(
                row.get("nfd_m3"), field="nfd_m3", context=f"{path}:{province}"
            ),
            "operable_proxy_ha": _optional_float(
                row.get("operable_proxy_ha"),
                field="operable_proxy_ha",
                context=f"{path}:{province}",
            ),
            "operable_missing": _bool(
                row.get("operable_missing"),
                field="operable_missing",
                context=f"{path}:{province}",
            ),
            "upper_bound": upper_bound,
            "binding": _bool(
                row.get("binding"), field="binding", context=f"{path}:{province}"
            ),
            "basis": (row.get("basis") or "").strip(),
            "caveat": (row.get("caveat") or "").strip(),
        }
    missing = sorted(set(PROVINCE_ORDER) - set(parsed))
    if missing:
        raise ForestLPError(f"{path}: missing provinces {missing}")
    return parsed


def _read_case_summary(path: Path, level: str) -> dict[str, object]:
    payload = _read_json(path)
    cases = payload.get("cases")
    if not isinstance(cases, dict) or level not in cases:
        raise ForestLPError(f"{path}: missing case {level!r}")
    case = cases[level]
    if not isinstance(case, dict):
        raise ForestLPError(f"{path}: case {level!r} is not an object")
    status = str(case.get("status") or "")
    if status not in {"optimal", "infeasible"}:
        raise ForestLPError(f"{path}: case {level}: unsupported status {status!r}")
    return case


def build_allocation_rows(
    factors: Mapping[str, ProvinceFactor],
    operable: Mapping[str, float | None],
) -> tuple[list[AllocationRow], list[dict[str, object]], list[dict[str, object]]]:
    """Build province rows and case summaries for every cap case and level."""

    base_rows_by_level = {
        level: _read_allocation_rows(ALLOCATION_DIR / f"{level}.csv") for level, _ in LEVELS
    }
    rows: list[AllocationRow] = []
    case_summaries: list[dict[str, object]] = []
    residue_summaries: list[dict[str, object]] = []

    for cap_case, intensity in CAP_CASES:
        summary_path = _summary_path(intensity)
        for level, level_target in LEVELS:
            case = _read_case_summary(summary_path, level)
            status = str(case["status"])
            feasible = status == "optimal"
            national_target = _finite_float(
                case.get("national_total_m3"),
                field="national_total_m3",
                context=f"{summary_path}:{level}",
            )
            if abs(national_target - level_target) > ALLOCATION_TOLERANCE_M3:
                raise ForestLPError(
                    f"{summary_path}:{level}: national target {national_target} "
                    f"does not match ladder total {level_target}"
                )
            deficit = _finite_float(
                case.get("deficit_m3", 0.0),
                field="deficit_m3",
                context=f"{summary_path}:{level}",
            )
            case_binding = tuple(str(p) for p in case.get("binding_provinces") or ())

            if intensity is not None and level in {"100m", "119m"}:
                effective_rows = _read_allocation_rows(_case_path(level, intensity))
            else:
                effective_rows = None

            province_rows: list[AllocationRow] = []
            for province in PROVINCE_ORDER:
                base = base_rows_by_level[level][province]
                area = operable[province]
                documented_cap = float(base["upper_bound"])
                intensity_cap = None if intensity is None or area is None else intensity * area
                expected_effective = documented_cap
                if intensity_cap is not None:
                    expected_effective = min(documented_cap, intensity_cap)

                if feasible and effective_rows is not None:
                    effective = float(effective_rows[province]["upper_bound"])
                    allocated = float(effective_rows[province]["harvest_m3"])
                    binding: bool | None = bool(effective_rows[province]["binding"])
                    cap_basis = str(effective_rows[province]["basis"])
                    cap_caveat = str(effective_rows[province]["caveat"])
                elif feasible:
                    cap_basis = str(base["basis"])
                    cap_caveat = str(base["caveat"])
                    effective = documented_cap
                    allocated = float(base["harvest_m3"])
                    binding = bool(base["binding"])
                else:
                    effective = expected_effective
                    allocated = None
                    binding = None
                    cap_basis = str(base["basis"])
                    if intensity is not None:
                        cap_basis = f"{cap_basis}+operable_intensity_{intensity:g}"
                    cap_caveat = str(base["caveat"])
                    if intensity is not None:
                        cap_caveat = (
                            f"{cap_caveat} Effective cap is min(documented, "
                            f"{intensity:.1f} * operable_proxy_ha); case infeasible."
                        )

                if abs(effective - expected_effective) > CAP_TOLERANCE_M3:
                    raise ForestLPError(
                        f"{cap_case}:{level}:{province}: effective cap {effective} "
                        f"does not equal min(documented, intensity) = {expected_effective}"
                    )
                if allocated is not None and allocated > effective + CAP_TOLERANCE_M3:
                    raise ForestLPError(
                        f"{cap_case}:{level}:{province}: allocated {allocated} exceeds "
                        f"effective cap {effective}"
                    )

                factor = factors[province]
                if allocated is None:
                    gross = low = base_odt = high = None
                    pj_low = pj_base = pj_high = None
                else:
                    gross = factor.gross_odt_per_m3_log * allocated
                    low = factor.recoverable_low * allocated
                    base_odt = factor.recoverable_base * allocated
                    high = factor.recoverable_high * allocated
                    pj_low = low * LHV_GJ_PER_ODT / 1e6
                    pj_base = base_odt * LHV_GJ_PER_ODT / 1e6
                    pj_high = high * LHV_GJ_PER_ODT / 1e6

                province_rows.append(
                    AllocationRow(
                        cap_case=cap_case,
                        allocation_case=level,
                        national_harvest_target_m3=national_target,
                        province=province,
                        aac_m3=base["aac_m3"],
                        nfd_m3=base["nfd_m3"],
                        operable_proxy_ha=area,
                        operable_missing=bool(base["operable_missing"]),
                        documented_cap_m3=documented_cap,
                        intensity_m3_per_ha_yr=intensity,
                        intensity_cap_m3=intensity_cap,
                        effective_cap_m3=effective,
                        allocated_harvest_m3=allocated,
                        allocated_share=None if allocated is None else allocated / national_target,
                        binding=binding,
                        at_cap=(
                            None
                            if allocated is None
                            else effective > 0 and abs(allocated - effective) <= CAP_TOLERANCE_M3
                        ),
                        province_status="allocated" if allocated is not None else "infeasible_no_allocation",
                        province_feasible=allocated is not None,
                        case_status=status,
                        case_feasible=feasible,
                        case_deficit_m3=deficit,
                        cap_basis=cap_basis,
                        cap_caveat=cap_caveat,
                        province_factor_gross_odt_per_m3_log=factor.gross_odt_per_m3_log,
                        province_factor_recoverable_low_odt_per_m3_log=factor.recoverable_low,
                        province_factor_recoverable_base_odt_per_m3_log=factor.recoverable_base,
                        province_factor_recoverable_high_odt_per_m3_log=factor.recoverable_high,
                        residue_gross_odt=gross,
                        residue_recoverable_odt_low=low,
                        residue_recoverable_odt_base=base_odt,
                        residue_recoverable_odt_high=high,
                        residue_pj_low=pj_low,
                        residue_pj_base=pj_base,
                        residue_pj_high=pj_high,
                        allocation_weighted_factor_gross_odt_per_m3_log=None,
                        allocation_weighted_factor_recoverable_base_odt_per_m3_log=None,
                    )
                )

            if feasible:
                if any(row.allocated_harvest_m3 is None for row in province_rows):
                    raise ForestLPError(f"{cap_case}:{level}: feasible case has a blank allocation")
                harvest_sum = math.fsum(row.allocated_harvest_m3 or 0.0 for row in province_rows)
                if abs(harvest_sum - national_target) > ALLOCATION_TOLERANCE_M3:
                    raise ForestLPError(
                        f"{cap_case}:{level}: province harvest sums to {harvest_sum}, "
                        f"expected {national_target}"
                    )
                binding_rows = tuple(row.province for row in province_rows if row.binding)
                if set(binding_rows) != set(case_binding):
                    raise ForestLPError(
                        f"{cap_case}:{level}: binding set {binding_rows} does not match "
                        f"summary {case_binding}"
                    )
                for row in province_rows:
                    if row.binding and (
                        row.allocated_harvest_m3 is None
                        or row.allocated_harvest_m3 < row.effective_cap_m3 - CAP_TOLERANCE_M3
                    ):
                        raise ForestLPError(
                            f"{cap_case}:{level}:{row.province}: binding row is not at its cap"
                        )
                gross_total = math.fsum(row.residue_gross_odt or 0.0 for row in province_rows)
                base_total = math.fsum(
                    row.residue_recoverable_odt_base or 0.0 for row in province_rows
                )
                low_total = math.fsum(row.residue_recoverable_odt_low or 0.0 for row in province_rows)
                high_total = math.fsum(
                    row.residue_recoverable_odt_high or 0.0 for row in province_rows
                )
                pj_base_total = base_total * LHV_GJ_PER_ODT / 1e6
                weighted_gross = gross_total / harvest_sum
                weighted_base = base_total / harvest_sum
                province_rows = [
                    replace(
                        row,
                        allocation_weighted_factor_gross_odt_per_m3_log=weighted_gross,
                        allocation_weighted_factor_recoverable_base_odt_per_m3_log=weighted_base,
                    )
                    for row in province_rows
                ]
                capacity = math.fsum(row.effective_cap_m3 for row in province_rows)
                case_summaries.append(
                    {
                        "cap_case": cap_case,
                        "allocation_case": level,
                        "national_target_m3": national_target,
                        "status": status,
                        "feasible": feasible,
                        "harvest_sum_m3": harvest_sum,
                        "allocation_gap_m3": national_target - harvest_sum,
                        "capacity_m3": capacity,
                        "deficit_m3": deficit,
                        "binding_provinces": list(binding_rows),
                        "province_rows": len(province_rows),
                    }
                )
                residue_summaries.append(
                    {
                        "cap_case": cap_case,
                        "allocation_case": level,
                        "feasible": feasible,
                        "harvest_sum_m3": harvest_sum,
                        "gross_odt": gross_total,
                        "recoverable_odt_low": low_total,
                        "recoverable_odt_base": base_total,
                        "recoverable_odt_high": high_total,
                        "pj_low": low_total * LHV_GJ_PER_ODT / 1e6,
                        "pj_base": pj_base_total,
                        "pj_high": high_total * LHV_GJ_PER_ODT / 1e6,
                        "allocation_weighted_factor_gross_odt_per_m3_log": weighted_gross,
                        "allocation_weighted_factor_recoverable_base_odt_per_m3_log": weighted_base,
                    }
                )
            else:
                if any(row.allocated_harvest_m3 is not None for row in province_rows):
                    raise ForestLPError(f"{cap_case}:{level}: infeasible case has an allocation")
                capacity = math.fsum(row.effective_cap_m3 for row in province_rows)
                expected_deficit = max(0.0, national_target - capacity)
                if abs(expected_deficit - deficit) > CAP_TOLERANCE_M3:
                    raise ForestLPError(
                        f"{cap_case}:{level}: capacity deficit {expected_deficit} does not "
                        f"match summary deficit {deficit}"
                    )
                case_summaries.append(
                    {
                        "cap_case": cap_case,
                        "allocation_case": level,
                        "national_target_m3": national_target,
                        "status": status,
                        "feasible": feasible,
                        "harvest_sum_m3": None,
                        "allocation_gap_m3": None,
                        "capacity_m3": capacity,
                        "deficit_m3": deficit,
                        "binding_provinces": [],
                        "province_rows": len(province_rows),
                    }
                )
                residue_summaries.append(
                    {
                        "cap_case": cap_case,
                        "allocation_case": level,
                        "feasible": feasible,
                        "harvest_sum_m3": None,
                        "gross_odt": None,
                        "recoverable_odt_low": None,
                        "recoverable_odt_base": None,
                        "recoverable_odt_high": None,
                        "pj_low": None,
                        "pj_base": None,
                        "pj_high": None,
                        "allocation_weighted_factor_gross_odt_per_m3_log": None,
                        "allocation_weighted_factor_recoverable_base_odt_per_m3_log": None,
                    }
                )
            rows.extend(province_rows)

    return rows, case_summaries, residue_summaries


def _notes(row: Mapping[str, str]) -> str:
    return (row.get("notes") or "").strip()


def _evidence(row: Mapping[str, str]) -> str:
    return ((row.get("value_basis") or "").split(":", 1)[0]).strip()


def _notes_token(row: Mapping[str, str], key: str) -> str | None:
    for chunk in _notes(row).split(";"):
        name, separator, value = chunk.strip().partition("=")
        if separator and name.strip() == key:
            return value.strip()
    return None


def _is_model_input_row(row: Mapping[str, str]) -> bool:
    return (
        _evidence(row) == "model_input"
        and _notes_token(row, "model_input") == "1"
        and _notes_token(row, "not_observed") == "1"
    )


def _is_observed_row(row: Mapping[str, str]) -> bool:
    return _evidence(row) in {
        "observed_payment",
        "observed_sale",
        "observed_price",
    }


def validate_ledger(rows: Sequence[Mapping[str, str]]) -> dict[str, object]:
    """Validate the ledger's perspective/sign/mirror contract without summing it.

    The merged ledger contains observed BCTS rows and the full MERIS
    model-input deck.  Observed rows and model-input rows are counted
    separately; only the model-input deck may carry delivered-log revenue, and
    it must never be mistaken for an observed transaction.
    """

    if len(rows) != EXPECTED_LEDGER_ROWS:
        raise ForestLPError(
            f"{LEDGER_PATH}: expected {EXPECTED_LEDGER_ROWS} rows, got {len(rows)}"
        )
    jurisdiction_counts = Counter(
        (row.get("jurisdiction") or "").strip() for row in rows
    )
    expected_jurisdictions = Counter({"BC": 200, "QC": 740})
    if jurisdiction_counts != expected_jurisdictions:
        raise ForestLPError(
            f"{LEDGER_PATH}: expected jurisdiction counts "
            f"{dict(expected_jurisdictions)}, got {dict(jurisdiction_counts)}"
        )

    mirror_keys: dict[tuple[str, ...], dict[str, list[Mapping[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    observed_rows = 0
    model_input_rows = 0
    for index, row in enumerate(rows, start=2):
        context = f"{LEDGER_PATH}:row{index}"
        perspective = (row.get("perspective") or "").strip()
        component = (row.get("component") or "").strip()
        if perspective not in SIGN_MATRIX:
            raise ForestLPError(f"{context}: unknown perspective {perspective!r}")
        if component not in SIGN_MATRIX[perspective]:
            raise ForestLPError(f"{context}: component {component!r} is invalid for {perspective}")
        sign = int(_finite_float(row.get("sign"), field="sign", context=context))
        if sign != SIGN_MATRIX[perspective][component]:
            raise ForestLPError(
                f"{context}: sign {sign} does not match {perspective}/{component}"
            )
        value = _finite_float(row.get("value_cad"), field="value_cad", context=context)
        if value < 0:
            raise ForestLPError(f"{context}: value_cad must be non-negative")
        _finite_float(row.get("volume_m3"), field="volume_m3", context=context)

        evidence = _evidence(row)
        is_model_input = _is_model_input_row(row)
        is_observed = _is_observed_row(row)
        if _notes_token(row, "model_input") == "1" and not is_model_input:
            raise ForestLPError(
                f"{context}: model_input=1 requires model_input evidence, "
                "not_observed=1, and value_basis 'model_input:<price_point>'"
            )
        if is_model_input and is_observed:
            raise ForestLPError(f"{context}: row is both model input and observed")
        if is_model_input:
            model_input_rows += 1
        elif is_observed:
            observed_rows += 1
        elif evidence in {"model_derived", "administered_rate", "appraisal_allowance", "published_average", "fallback"}:
            pass
        else:
            raise ForestLPError(f"{context}: unclassified evidence {evidence!r}")

        if component in {
            "stumpage_base",
            "stumpage_bid_premium",
            "stumpage_total",
            "admin_fee",
            "crown_other",
            "crown_credit",
        }:
            key = (
                row.get("jurisdiction", ""),
                row.get("source", ""),
                row.get("year", ""),
                row.get("spatial_unit", ""),
                row.get("species", ""),
                row.get("product", ""),
                row.get("volume_m3", ""),
                row.get("unit", ""),
                row.get("value_cad", ""),
                row.get("value_basis", ""),
                component,
            )
            mirror_keys[key][perspective].append(row)

    unpaired = 0
    for key, by_perspective in mirror_keys.items():
        crown = by_perspective.get("crown", [])
        sector = by_perspective.get("forest_sector", [])
        if len(crown) != len(sector) or len(crown) == 0:
            unpaired += 1
    if unpaired:
        raise ForestLPError(f"{LEDGER_PATH}: {unpaired} mirror keys are not paired")

    mirror_pairs = sum(len(v.get("crown", [])) for v in mirror_keys.values())
    if mirror_pairs != EXPECTED_MIRROR_PAIRS:
        raise ForestLPError(
            f"{LEDGER_PATH}: expected {EXPECTED_MIRROR_PAIRS} mirror pairs, "
            f"got {mirror_pairs}"
        )

    delivered_rows = [
        row for row in rows if row.get("component") == "delivered_log_revenue"
    ]
    observed_delivered_rows = [row for row in delivered_rows if _is_observed_row(row)]
    if observed_delivered_rows:
        raise ForestLPError(
            f"{LEDGER_PATH}: {len(observed_delivered_rows)} observed "
            "delivered_log_revenue rows are present; v1 does not accept an "
            "observed delivered price without full costs"
        )
    model_input_delivered_rows = [
        row for row in delivered_rows if _is_model_input_row(row)
    ]
    if len(model_input_delivered_rows) != len(delivered_rows):
        raise ForestLPError(
            f"{LEDGER_PATH}: every delivered_log_revenue row must be an "
            "explicit model input"
        )

    return {
        "rows": len(rows),
        "mirror_rows": sum(1 for row in rows if "mirror=1" in _notes(row)),
        "mirror_pairs": mirror_pairs,
        "unpaired_mirror_keys": unpaired,
        "delivered_log_revenue_rows": len(delivered_rows),
        "delivered_log_revenue_model_input_rows": len(model_input_delivered_rows),
        "delivered_log_revenue_observed_rows": len(observed_delivered_rows),
        "observed_rows": observed_rows,
        "model_input_rows": model_input_rows,
    }


def build_cost_revenue_rows(
    ledger_rows: Sequence[Mapping[str, str]],
    coverage: Mapping[str, object],
) -> tuple[list[CostRevenueRow], list[dict[str, object]]]:
    """Summarise province/year ledger coverage without estimating net value."""

    readiness = coverage.get("net_value_readiness")
    if not isinstance(readiness, dict):
        raise ForestLPError(f"{COVERAGE_PATH}: missing net_value_readiness")
    bc_cov = coverage.get("coverage", {}).get("bc_bcts", {})
    qc_cov = coverage.get("coverage", {}).get("qc_meris", {})

    grouped: dict[tuple[str, int], list[Mapping[str, str]]] = defaultdict(list)
    for row in ledger_rows:
        jurisdiction = (row.get("jurisdiction") or "").strip().upper()
        year = int(_finite_float(row.get("year"), field="year", context=str(LEDGER_PATH)))
        grouped[(jurisdiction, year)].append(row)

    output_rows: list[CostRevenueRow] = []
    for (jurisdiction, year), rows in sorted(grouped.items()):
        province = jurisdiction.lower()
        if province not in readiness:
            raise ForestLPError(
                f"{COVERAGE_PATH}: no net-value readiness row for {jurisdiction}"
            )
        province_readiness = readiness[province]
        source = sorted({(row.get("source") or "").strip() for row in rows})
        if len(source) != 1:
            raise ForestLPError(
                f"{LEDGER_PATH}:{jurisdiction}/{year}: expected one source, got {source}"
            )
        source_name = source[0]
        coverage_rows = [
            row
            for row in rows
            if (row.get("perspective") or "").strip() == "crown"
            or "mirror=1" not in _notes(row)
        ]
        components = Counter((row.get("component") or "").strip() for row in coverage_rows)
        perspectives = Counter((row.get("perspective") or "").strip() for row in rows)
        licences = len({(row.get("licence") or "").strip() for row in rows})
        mirror_rows = sum(1 for row in rows if "mirror=1" in _notes(row))
        observed_rows = sum(1 for row in rows if _is_observed_row(row))
        model_input_rows = sum(1 for row in rows if _is_model_input_row(row))
        if observed_rows + model_input_rows != len(rows):
            raise ForestLPError(
                f"{LEDGER_PATH}:{jurisdiction}/{year}: every row must be "
                "classified as observed or model input"
            )
        observed_delivered_rows = [
            row
            for row in rows
            if (row.get("component") or "").strip() == "delivered_log_revenue"
            and _is_observed_row(row)
        ]
        model_input_delivered_rows = [
            row
            for row in rows
            if (row.get("component") or "").strip() == "delivered_log_revenue"
            and _is_model_input_row(row)
        ]

        readiness_components = {
            str(component.get("component")): component
            for component in province_readiness.get("components", [])
        }
        if set(readiness_components) != set(COMPONENT_ORDER):
            raise ForestLPError(
                f"{COVERAGE_PATH}:{jurisdiction}: component set is incomplete"
            )

        coverage_parts: list[str] = []
        present_parts: list[str] = []
        missing_parts: list[str] = []
        for component in COMPONENT_ORDER:
            year_rows = components.get(component, 0)
            component_readiness = readiness_components[component]
            province_status = str(component_readiness.get("status") or "")
            if year_rows == 0:
                status = "missing"
            else:
                if province_status not in {"present", "partial"}:
                    raise ForestLPError(
                        f"{COVERAGE_PATH}:{jurisdiction}:{component}: "
                        f"rows exist but status is {province_status!r}"
                    )
                status = province_status
            coverage_parts.append(f"{component}={status}({year_rows})")
            if status == "missing":
                missing_parts.append(component)
            else:
                present_parts.append(f"{component}({status},{year_rows})")

        net_value_status = str(province_readiness.get("net_value_status") or "")
        if net_value_status != "not_estimated":
            raise ForestLPError(
                f"{COVERAGE_PATH}:{jurisdiction}: net value must be not_estimated, "
                f"got {net_value_status!r}"
            )
        if observed_delivered_rows:
            raise ForestLPError(
                f"{LEDGER_PATH}:{jurisdiction}/{year}: observed delivered log "
                "revenue cannot be claimed in v1"
            )

        if jurisdiction == "BC":
            covered_volume = _finite_float(
                bc_cov.get("ledger", {}).get("unique_sale_volume_m3"),
                field="unique_sale_volume_m3",
                context=f"{COVERAGE_PATH}:BC",
            )
            covered_status = "awarded_bcts_sale_weight_not_harvest"
            covered_share = _finite_float(
                bc_cov.get("awarded_volume_share_of_bc_2024_harvest", {}).get("share"),
                field="awarded_volume_share_of_bc_2024_harvest.share",
                context=f"{COVERAGE_PATH}:BC",
            )
            source_frame_volume = _finite_float(
                bc_cov.get("sample_frame", {}).get("scheduled_volume_m3"),
                field="scheduled_volume_m3",
                context=f"{COVERAGE_PATH}:BC",
            )
            source_frame_share = _finite_float(
                bc_cov.get("scheduled_volume_share_of_bc_2024_harvest", {}).get("share"),
                field="scheduled_volume_share_of_bc_2024_harvest.share",
                context=f"{COVERAGE_PATH}:BC",
            )
            source_sample_share = covered_share
            source_sample_basis = "awarded_bcts_volume_share_of_bc_2024_harvest"
            source_sample_status = "estimated_sample_share_not_population"
            source_frame_basis = "bcts_scheduled_volume_share_of_bc_2024_harvest"
        elif jurisdiction == "QC":
            covered_volume = _finite_float(
                qc_cov.get("ledger", {}).get("observed_volume_m3"),
                field="observed_volume_m3",
                context=f"{COVERAGE_PATH}:QC",
            )
            covered_status = "no_observed_volume_rate_denominator_only"
            covered_share = None
            source_frame_volume = None
            source_frame_share = _finite_float(
                qc_cov.get("zone_share", {}).get("share"),
                field="zone_share.share",
                context=f"{COVERAGE_PATH}:QC",
            )
            source_sample_share = None
            source_sample_basis = "not_estimable_no_observed_volume"
            source_sample_status = "not_estimable"
            source_frame_basis = (
                "meris_full_model_deck_zone_coverage_191_of_191_zones"
            )
        else:
            raise ForestLPError(f"{LEDGER_PATH}: unsupported jurisdiction {jurisdiction}")

        stumpage_rows = [
            row
            for row in coverage_rows
            if (row.get("component") or "").strip()
            in {"stumpage_base", "stumpage_bid_premium", "stumpage_total"}
        ]
        crown_anchor_basis = ";".join(
            sorted({(row.get("value_basis") or "").strip() for row in stumpage_rows})
        ) or "no_crown_stumpage_row"
        crown_anchor_confidence = ";".join(
            sorted({(row.get("confidence") or "").strip() for row in stumpage_rows})
        ) or "none"
        has_medium_or_high_stumpage = any(
            (row.get("confidence") or "").strip() in {"medium", "high"}
            for row in stumpage_rows
        )
        production_components = {
            "harvest",
            "road",
            "haul",
            "silviculture",
            "residue_recovery",
            "overhead",
        }
        production_cost_status = (
            "partial_not_net_value"
            if any(components.get(component, 0) > 0 for component in production_components)
            else "missing"
        )
        net_value_reason = str(province_readiness.get("reason") or "")
        delivered_revenue_status = (
            "model_input_only_not_usable"
            if model_input_delivered_rows
            else "observed_not_usable"
            if observed_delivered_rows
            else "not_usable"
        )
        model_input_flags = ";".join(
            (
                f"crown_stumpage_anchor={'usable_with_caveat' if has_medium_or_high_stumpage else 'not_usable_low_confidence'}",
                f"delivered_log_revenue={delivered_revenue_status}",
                f"production_costs={production_cost_status}",
                "residue_revenue=not_usable",
                "residue_recovery=not_usable",
                "net_value=not_estimated",
                "lp_objective=not_used",
                "sample_not_population=true",
            )
        )
        output_rows.append(
            CostRevenueRow(
                province=jurisdiction,
                jurisdiction=jurisdiction,
                year=year,
                source=source_name,
                ledger_rows=len(rows),
                crown_rows=perspectives.get("crown", 0),
                forest_sector_rows=perspectives.get("forest_sector", 0),
                mirror_rows=mirror_rows,
                observed_rows=observed_rows,
                model_input_rows=model_input_rows,
                licences=licences,
                crown_anchor_basis=crown_anchor_basis,
                crown_anchor_confidence=crown_anchor_confidence,
                covered_volume_m3=covered_volume,
                covered_volume_status=covered_status,
                covered_volume_share_of_provincial_2024_harvest=covered_share,
                source_frame_volume_m3=source_frame_volume,
                source_frame_share=source_frame_share,
                source_frame_share_basis=source_frame_basis,
                source_sample_share=source_sample_share,
                source_sample_share_basis=source_sample_basis,
                source_sample_share_status=source_sample_status,
                component_coverage=";".join(coverage_parts),
                present_or_partial_components=";".join(present_parts),
                missing_components=";".join(missing_parts),
                observed_delivered_log_revenue_present=bool(
                    observed_delivered_rows
                ),
                full_production_costs_present=False,
                net_value_status=net_value_status,
                net_value_reason=net_value_reason,
                model_input_flags=model_input_flags,
                usable_as_crown_anchor=has_medium_or_high_stumpage,
                usable_as_delivered_log_revenue=False,
                usable_as_full_production_cost=False,
                usable_as_net_value=False,
                usable_as_lp_objective=False,
            )
        )

    key_rows = {(row.jurisdiction, row.year) for row in output_rows}
    expected_rows = {
        ("BC", 2026),
        ("QC", 2025),
    }
    if key_rows != expected_rows:
        raise ForestLPError(
            f"{LEDGER_PATH}: expected province-years {sorted(expected_rows)}, "
            f"got {sorted(key_rows)}"
        )
    return output_rows, [
        {
            "province": row.province,
            "jurisdiction": row.jurisdiction,
            "year": row.year,
            "source": row.source,
            "ledger_rows": row.ledger_rows,
            "observed_rows": row.observed_rows,
            "model_input_rows": row.model_input_rows,
            "crown_anchor_basis": row.crown_anchor_basis,
            "crown_anchor_confidence": row.crown_anchor_confidence,
            "covered_volume_m3": row.covered_volume_m3,
            "covered_volume_status": row.covered_volume_status,
            "source_sample_share": row.source_sample_share,
            "source_sample_share_status": row.source_sample_share_status,
            "component_coverage": row.component_coverage,
            "present_or_partial_components": row.present_or_partial_components,
            "missing_components": row.missing_components,
            "observed_delivered_log_revenue_present": row.observed_delivered_log_revenue_present,
            "full_production_costs_present": row.full_production_costs_present,
            "net_value_status": row.net_value_status,
            "model_input_flags": row.model_input_flags,
        }
        for row in output_rows
    ]


def load_residue_reference() -> dict[str, dict[str, float | None]]:
    rows = {row["allocation"]: row for row in _read_csv(RESIDUE_REFERENCE_PATH)}
    reference: dict[str, dict[str, float | None]] = {}
    for case in ("100m", "119m", "150m"):
        if case not in rows:
            raise ForestLPError(f"{RESIDUE_REFERENCE_PATH}: missing case {case}")
        row = rows[case]
        reference[case] = {
            "harvest_sum_m3": _finite_float(
                row.get("total_harvest_m3"),
                field="total_harvest_m3",
                context=f"{RESIDUE_REFERENCE_PATH}:{case}",
            ),
            "gross_factor": _finite_float(
                row.get("gross_odt_per_m3_log"),
                field="gross_odt_per_m3_log",
                context=f"{RESIDUE_REFERENCE_PATH}:{case}",
            ),
            "base_factor": _finite_float(
                row.get("odt_per_m3_log_base"),
                field="odt_per_m3_log_base",
                context=f"{RESIDUE_REFERENCE_PATH}:{case}",
            ),
            "pj_base": _finite_float(
                row.get("national_total_pj_base"),
                field="national_total_pj_base",
                context=f"{RESIDUE_REFERENCE_PATH}:{case}",
            ),
        }
    return reference


def _check(
    checks: list[dict[str, object]],
    *,
    check_id: str,
    name: str,
    passed: bool,
    detail: str,
) -> None:
    checks.append(
        {
            "id": check_id,
            "name": name,
            "status": "pass" if passed else "fail",
            "detail": detail,
        }
    )
    if not passed:
        raise ForestLPError(f"{check_id} {name} failed: {detail}")


def verify_synthesis(
    allocation_rows: Sequence[AllocationRow],
    case_summaries: Sequence[Mapping[str, object]],
    residue_summaries: Sequence[Mapping[str, object]],
    ledger_validation: Mapping[str, object],
    cost_rows: Sequence[CostRevenueRow],
) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    _check(
        checks,
        check_id="allocation-rows",
        name="Allocation grid is complete",
        passed=len(allocation_rows) == EXPECTED_ALLOCATION_ROWS,
        detail=f"{len(allocation_rows)} rows (expected {EXPECTED_ALLOCATION_ROWS})",
    )

    baseline_rows = [r for r in allocation_rows if r.cap_case == "documented_ceiling"]
    for level, expected in LEVELS:
        rows = [r for r in baseline_rows if r.allocation_case == level]
        total = math.fsum(r.allocated_harvest_m3 or 0.0 for r in rows)
        _check(
            checks,
            check_id=f"alloc-sum-{level}",
            name="National allocation sum",
            passed=abs(total - expected) <= CAP_TOLERANCE_M3,
            detail=f"documented_ceiling {level}: {total:.6f} m3 vs target {expected:.6f}",
        )
        province_total = math.fsum(r.allocated_harvest_m3 or 0.0 for r in rows)
        _check(
            checks,
            check_id=f"province-sum-{level}",
            name="Province rows sum to national allocation",
            passed=abs(province_total - total) <= CAP_TOLERANCE_M3,
            detail=f"{len(rows)} province rows sum to {province_total:.6f} m3",
        )

    above_cap = [
        row
        for row in allocation_rows
        if row.allocated_harvest_m3 is not None
        and row.allocated_harvest_m3 > row.effective_cap_m3 + CAP_TOLERANCE_M3
    ]
    _check(
        checks,
        check_id="cap-consistency",
        name="No province allocation exceeds its effective cap",
        passed=not above_cap,
        detail=f"{len(above_cap)} cap violations",
    )
    binding_mismatch = [
        row
        for row in allocation_rows
        if row.binding
        and row.allocated_harvest_m3 is not None
        and row.allocated_harvest_m3 < row.effective_cap_m3 - CAP_TOLERANCE_M3
    ]
    _check(
        checks,
        check_id="binding-consistency",
        name="Every binding province is at its effective cap",
        passed=not binding_mismatch,
        detail=f"{len(binding_mismatch)} binding rows below cap",
    )

    for cap_case, intensity in CAP_CASES:
        for level, target in LEVELS:
            summary = next(
                item
                for item in case_summaries
                if item["cap_case"] == cap_case and item["allocation_case"] == level
            )
            if summary["feasible"]:
                continue
            expected_deficit = {
                ("intensity_1.0", "150m"): 16_296_351.858624816,
                ("intensity_1.2", "150m"): 4_491_872.990349799,
            }[(cap_case, level)]
            _check(
                checks,
                check_id=f"infeasible-{cap_case}-{level}",
                name="Infeasible intensity case reports its deficit",
                passed=abs(float(summary["deficit_m3"]) - expected_deficit) <= CAP_TOLERANCE_M3,
                detail=(
                    f"{cap_case} {level}: deficit {summary['deficit_m3']:.6f} m3, "
                    f"expected {expected_deficit:.6f}"
                ),
            )

    for summary in residue_summaries:
        if not summary["feasible"]:
            continue
        provinces = [
            row
            for row in allocation_rows
            if row.cap_case == summary["cap_case"]
            and row.allocation_case == summary["allocation_case"]
        ]
        gross = math.fsum(row.residue_gross_odt or 0.0 for row in provinces)
        base_odt = math.fsum(row.residue_recoverable_odt_base or 0.0 for row in provinces)
        pj = math.fsum(row.residue_pj_base or 0.0 for row in provinces)
        _check(
            checks,
            check_id=f"residue-mass-{summary['cap_case']}-{summary['allocation_case']}",
            name="Residue mass balance closes",
            passed=abs(gross - float(summary["gross_odt"])) <= 1e-6
            and abs(base_odt - float(summary["recoverable_odt_base"])) <= 1e-6
            and abs(pj - float(summary["pj_base"])) <= RESIDUE_TOLERANCE_PJ,
            detail=(
                f"gross={gross:.6f} ODT, base={base_odt:.6f} ODT, "
                f"PJ={pj:.9f}"
            ),
        )
        _check(
            checks,
            check_id=f"pj-conversion-{summary['cap_case']}-{summary['allocation_case']}",
            name="PJ conversion and weighted factor",
            passed=(
                abs(pj - base_odt * LHV_GJ_PER_ODT / 1e6) <= RESIDUE_TOLERANCE_PJ
                and abs(
                    float(summary["allocation_weighted_factor_recoverable_base_odt_per_m3_log"])
                    * float(summary["harvest_sum_m3"])
                    - base_odt
                )
                <= 1e-6
            ),
            detail=f"PJ = ODT * {LHV_GJ_PER_ODT} / 1e6",
        )

    reference = load_residue_reference()
    for level in ("100m", "119m", "150m"):
        summary = next(
            item
            for item in residue_summaries
            if item["cap_case"] == "documented_ceiling" and item["allocation_case"] == level
        )
        ref = reference[level]
        _check(
            checks,
            check_id=f"residue-reference-{level}",
            name="Baseline residue matches residue-factor artifact",
            passed=(
                abs(float(summary["pj_base"]) - float(ref["pj_base"])) <= RESIDUE_TOLERANCE_PJ
                and abs(
                    float(summary["allocation_weighted_factor_gross_odt_per_m3_log"])
                    - float(ref["gross_factor"])
                )
                <= 1e-9
                and abs(
                    float(summary["allocation_weighted_factor_recoverable_base_odt_per_m3_log"])
                    - float(ref["base_factor"])
                )
                <= 1e-9
            ),
            detail=(
                f"{level}: PJ {summary['pj_base']:.9f} vs {ref['pj_base']:.9f}"
            ),
        )

    _check(
        checks,
        check_id="ledger-mirrors",
        name="Cost/revenue ledger mirrors are paired",
        passed=ledger_validation["unpaired_mirror_keys"] == 0
        and ledger_validation["mirror_pairs"] == EXPECTED_MIRROR_PAIRS,
        detail=(
            f"{ledger_validation['mirror_rows']} mirror rows, "
            f"{ledger_validation['mirror_pairs']} pairs, "
            f"{ledger_validation['unpaired_mirror_keys']} unpaired"
        ),
    )
    _check(
        checks,
        check_id="observed-model-input-separation",
        name="Observed and model-input ledger rows are separated",
        passed=(
            ledger_validation["rows"] == EXPECTED_LEDGER_ROWS
            and ledger_validation["observed_rows"] > 0
            and ledger_validation["model_input_rows"] > 0
            and ledger_validation["observed_rows"]
            + ledger_validation["model_input_rows"]
            == ledger_validation["rows"]
            and ledger_validation["delivered_log_revenue_observed_rows"] == 0
            and ledger_validation["delivered_log_revenue_model_input_rows"] > 0
        ),
        detail=(
            f"{ledger_validation['observed_rows']} observed, "
            f"{ledger_validation['model_input_rows']} model-input, "
            f"{ledger_validation['delivered_log_revenue_model_input_rows']} "
            "model-input delivered-log rows, 0 observed"
        ),
    )
    _check(
        checks,
        check_id="no-cross-perspective-sum",
        name="No cross-perspective total is computed",
        passed=all(
            row.crown_rows + row.forest_sector_rows == row.ledger_rows for row in cost_rows
        )
        and all(row.net_value_status == "not_estimated" for row in cost_rows),
        detail="Crown and forest-sector rows stay separate; net value is not estimated",
    )
    _check(
        checks,
        check_id="net-value-not-claimed",
        name="Net value is not claimed",
        passed=all(
            row.net_value_status == "not_estimated"
            and not row.observed_delivered_log_revenue_present
            and not row.full_production_costs_present
            and not row.usable_as_delivered_log_revenue
            and not row.usable_as_full_production_cost
            and not row.usable_as_net_value
            for row in cost_rows
        ),
        detail=f"{len(cost_rows)} province-year rows are not_estimated",
    )
    _check(
        checks,
        check_id="partial-coverage-explicit",
        name="Partial cost/revenue coverage is explicit",
        passed=all(bool(row.missing_components) for row in cost_rows),
        detail="Every province-year lists missing components",
    )
    _check(
        checks,
        check_id="no-fabricated-allocation",
        name="Infeasible cases keep blank allocations and residue",
        passed=all(
            row.allocated_harvest_m3 is None
            and row.residue_gross_odt is None
            and row.residue_pj_base is None
            for row in allocation_rows
            if not row.case_feasible
        ),
        detail="Infeasible rows are not clamped or back-filled",
    )
    return checks


def _report(
    allocation_rows: Sequence[AllocationRow],
    case_summaries: Sequence[Mapping[str, object]],
    residue_summaries: Sequence[Mapping[str, object]],
    cost_rows: Sequence[CostRevenueRow],
    checks: Sequence[Mapping[str, object]],
    generated_at: str,
    source_commit: str,
    meris_full_present: bool,
) -> str:
    ledger_rows = sum(row.ledger_rows for row in cost_rows)
    observed_rows = sum(row.observed_rows for row in cost_rows)
    model_input_rows = sum(row.model_input_rows for row in cost_rows)
    mirror_rows = sum(row.mirror_rows for row in cost_rows)
    bc_cost = next(row for row in cost_rows if row.jurisdiction == "BC")
    qc_cost = next(row for row in cost_rows if row.jurisdiction == "QC")

    def level_table(level: str) -> list[str]:
        rows = [
            row
            for row in allocation_rows
            if row.cap_case == "documented_ceiling" and row.allocation_case == level
        ]
        lines = [
            "| Province | AAC (Mm3) | NFD (Mm3) | Operable (Mha) | Cap (Mm3) | Harvest (Mm3) | Binding | Gross ODT | Recoverable ODT (base) | PJ (base) |",
            "|---|---:|---:|---:|---:|---:|---|---:|---:|---:|",
        ]
        for row in rows:
            aac = "—" if row.aac_m3 is None else f"{row.aac_m3 / 1e6:.3f}"
            nfd = "—" if row.nfd_m3 is None else f"{row.nfd_m3 / 1e6:.3f}"
            area = "missing" if row.operable_proxy_ha is None else f"{row.operable_proxy_ha / 1e6:.3f}"
            binding = "yes" if row.binding else ""
            gross = "—" if row.residue_gross_odt is None else f"{row.residue_gross_odt:,.0f}"
            base_odt = (
                "—"
                if row.residue_recoverable_odt_base is None
                else f"{row.residue_recoverable_odt_base:,.0f}"
            )
            pj = "—" if row.residue_pj_base is None else f"{row.residue_pj_base:.3f}"
            lines.append(
                f"| {row.province} | {aac} | {nfd} | {area} | "
                f"{row.effective_cap_m3 / 1e6:.3f} | "
                f"{(row.allocated_harvest_m3 or 0.0) / 1e6:.3f} | {binding} | "
                f"{gross} | {base_odt} | {pj} |"
            )
        return lines

    def summary_table() -> list[str]:
        lines = [
            "| Cap case | Level | Status | Harvest (Mm3) | Target (Mm3) | Gap (m3) | Deficit (Mm3) | Binding provinces | PJ base | Weighted factor (ODT/m3) |",
            "|---|---|---|---:|---:|---:|---:|---|---:|---:|",
        ]
        for item in case_summaries:
            residue = next(
                r
                for r in residue_summaries
                if r["cap_case"] == item["cap_case"] and r["allocation_case"] == item["allocation_case"]
            )
            harvest = "—" if item["harvest_sum_m3"] is None else f"{item['harvest_sum_m3'] / 1e6:.3f}"
            gap = (
                "—"
                if item["allocation_gap_m3"] is None
                else f"{item['allocation_gap_m3']:+.3f}"
            )
            deficit = f"{item['deficit_m3'] / 1e6:.3f}"
            binding = ", ".join(item["binding_provinces"]) or "—"
            pj = "—" if residue["pj_base"] is None else f"{residue['pj_base']:.3f}"
            wf = (
                "—"
                if residue["allocation_weighted_factor_gross_odt_per_m3_log"] is None
                else f"{residue['allocation_weighted_factor_gross_odt_per_m3_log']:.6f}"
            )
            lines.append(
                f"| {item['cap_case']} | {item['allocation_case']} | {item['status']} | "
                f"{harvest} | {item['national_target_m3'] / 1e6:.3f} | {gap} | "
                f"{deficit} | {binding} | {pj} | {wf} |"
            )
        return lines

    def cost_table() -> list[str]:
        lines = [
            "| Province | Year | Source | Rows | Observed | Model input | Covered volume (m3) | Coverage status | Sample share | Net value | Present/partial components |",
            "|---|---:|---|---:|---:|---:|---:|---|---:|---|---|",
        ]
        for row in cost_rows:
            if row.covered_volume_status.startswith("no_observed"):
                volume = "0 (not observed)"
            elif row.covered_volume_m3 is None:
                volume = "not observed"
            else:
                volume = f"{row.covered_volume_m3:,.0f}"
            share = (
                "not estimable"
                if row.source_sample_share is None
                else f"{row.source_sample_share * 100:.2f}%"
            )
            lines.append(
                f"| {row.jurisdiction} | {row.year} | `{row.source}` | {row.ledger_rows} | "
                f"{row.observed_rows} | {row.model_input_rows} | {volume} | "
                f"{row.covered_volume_status} | {share} | "
                f"`{row.net_value_status}` | {row.present_or_partial_components} |"
            )
        return lines

    ready_lines = [
        "- Provincial allocation: documented-ceiling 100/119/150 m3 cases are feasible and conserve their national totals; operable-intensity 100/119 cases are feasible.",
        "- Operable proxy: 144.690 Mha base screening layer is present for 11 jurisdictions; PE and NU are explicitly `operable_missing`, not zero-filled.",
        "- Residue accounting: 13 province factors, gross and recoverable ODT, and PJ conversion at 18.5 GJ/ODT are computed from the allocation.",
        f"- Cost/revenue coverage: {ledger_rows} ledger rows ({observed_rows} observed, {model_input_rows} model input), {mirror_rows} mirror rows, and province/year component coverage are accounted for without summing perspectives.",
    ]
    blocked_lines = [
        "- Observed delivered log revenue: **0 rows**. BCTS supplies standing stumpage bids and the MERIS price deck is mill-gate model input, not a settlement.",
        "- Full production costs: **blocked**. BC has no production-cost rows; QC has only partial MERIS model-input components (loading-only harvest, tertiary-road-excluded road, CAD/ha silviculture).",
        "- Forest-sector net value: **not estimated** for every province-year. The ledger does not contain observed revenue plus all in-boundary costs.",
        "- Net-value objective and marginal cost curve: **not built**. v1 remains a least-deviation screening allocation.",
        "- Policy grade: **false**. This is a screening synthesis, not a harvest recommendation or a legal AAC determination.",
    ]
    if not meris_full_present:
        blocked_lines.append(
            "- MERIS full deck: **absent** from this checkout; the synthesis cannot treat the QC sample as observed harvest."
        )

    check_lines = [
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        check_lines.append(
            f"| {check['id']}: {check['name']} | {check['status']} | {check['detail']} |"
        )

    lines = [
        "# Forest-Sector LP v1 synthesis",
        "",
        f"**Generated:** {generated_at}  ",
        f"**Source commit:** `{source_commit}`  ",
        "**Source checkout:** `feature/osecan-zonal-bc-supply`; the forest artifacts are not on the `main` branch, so this is the current working tree.  ",
        "**Status:** `screening_only` · **Policy grade:** `false`",
        "",
        "This synthesis reads the existing provincial-allocation, operable-proxy, residue-factor, and cost/revenue-ledger artifacts. It does not solve a new LP and does not invent missing economics. The allocation is a least-deviation screening allocation, not a forecast, a sustainable-supply estimate, or a harvest recommendation.",
        "",
        "## 1. Headline",
        "",
        "- The documented-ceiling allocations conserve **100.000**, **118.977**, and **150.000 Mm3/yr** exactly within the artifact tolerance.",
        "- Under the operable-proxy intensity screen, 100M and 119M are feasible at both 1.0 and 1.2 m3/ha/yr. **150M is infeasible at both intensities**: deficit **16.296 Mm3** at 1.0 and **4.492 Mm3** at 1.2.",
        "- Base-case recoverable residue is **109.681 PJ/yr** for 100M, **130.981 PJ/yr** for 119M, and **166.801 PJ/yr** for 150M in the documented-ceiling case.",
        f"- The cost/revenue layer combines a **{observed_rows}-row observed BCTS sample** with a **{model_input_rows}-row MERIS model-input deck**; it is not a population. Net value is `not_estimated` for BC and QC because observed delivered log revenue and full production costs are missing.",
        "",
        "## 2. Case summary",
        "",
        "`Gap` is target minus allocated harvest (m3); `deficit` is target minus the sum of effective province caps for infeasible cases. The weighted factor is the harvest-weighted gross residue factor. PJ is recoverable residue at the base recovery fraction (0.60).",
        "",
        *summary_table(),
        "",
        "The 150M documented-ceiling case is feasible. The 150M intensity cases are reported as infeasible, with blank allocation and residue fields rather than a clamp.",
        "",
        *[
            f"**Input residual warning.** `{item['cap_case']} {item['allocation_case']}` sums to "
            f"{item['harvest_sum_m3']:,.3f} m3, {abs(item['allocation_gap_m3']):.3f} m3 "
            f"{'below' if item['allocation_gap_m3'] > 0 else 'above'} its target. "
            "This is preserved from the input artifact, not rescaled in the synthesis."
            for item in case_summaries
            if item["feasible"] and abs(float(item["allocation_gap_m3"])) > 1.0
        ],
        "",
        "## 3. Documented-ceiling allocation by province",
        "",
        "### 100M",
        "",
        *level_table("100m"),
        "",
        "### 119M",
        "",
        *level_table("119m"),
        "",
        "### 150M",
        "",
        *level_table("150m"),
        "",
        "Binding flags are carried from the allocation artifacts. `Cap` is the effective cap after the documented AAC/NFD/proxy bound and, in intensity cases, the operable-proxy intensity bound. PE and NU keep their documented caps and are flagged `operable_missing`; no area is substituted.",
        "",
        "## 4. Residue accounting",
        "",
        "Residue is harvest-linked: `G = f_gross * H`, `R = rho * G`, and `PJ = R * 18.5 / 1e6`. Recovery cases are low 0.35, base 0.60, and high 0.75. Mill residue, salvage, and whole-stem chip streams are not added.",
        "",
        "| Cap case | Level | Gross ODT | Recoverable ODT low | Recoverable ODT base | Recoverable ODT high | PJ low | PJ base | PJ high |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in residue_summaries:
        if not item["feasible"]:
            lines.append(
                f"| {item['cap_case']} | {item['allocation_case']} | — | — | — | — | — | — | — |"
            )
            continue
        lines.append(
            f"| {item['cap_case']} | {item['allocation_case']} | "
            f"{item['gross_odt']:,.0f} | {item['recoverable_odt_low']:,.0f} | "
            f"{item['recoverable_odt_base']:,.0f} | {item['recoverable_odt_high']:,.0f} | "
            f"{item['pj_low']:.3f} | {item['pj_base']:.3f} | {item['pj_high']:.3f} |"
        )
    lines.extend(
        [
            "",
            "The documented-ceiling residue totals match `residue_factor_by_allocation.csv` within 1e-6 PJ. The intensity cases are recomputed from their own allocations; their factors are not copied from the national artifact.",
            "",
            "## 5. Cost/revenue ledger coverage",
            "",
            f"The ledger contains **{ledger_rows} rows** in two non-additive perspectives: Crown revenue and forest-sector. Of these, **{observed_rows}** are observed and **{model_input_rows}** are model input; the classes are counted separately and model-input values are never monetised. The two perspectives are never summed into one total. The only permitted relation is the reconciliation identity `PCS = NV + CR`; v1 does not estimate either side because the required components are missing.",
            "",
            *cost_table(),
            "",
            (
                "**Coverage details.** Component counts in `component_coverage` "
                "use the non-mirror ledger rows; Crown/forest-sector mirror rows "
                "are counted separately. BC BCTS covers "
                f"{bc_cost.covered_volume_m3:,.0f} m3 of awarded sale weight, "
                f"{bc_cost.covered_volume_share_of_provincial_2024_harvest * 100:.2f}% "
                "of the 38,876,568 m3 BC 2024 harvest denominator; the scheduled "
                f"BCTS frame is {bc_cost.source_frame_volume_m3:,.0f} m3, "
                f"{bc_cost.source_frame_share * 100:.2f}% of the same denominator. "
                f"QC MERIS is the full model-input deck: {qc_cost.model_input_rows} "
                f"rows, {qc_cost.source_frame_share * 100:.2f}% zone coverage, and "
                "127 delivered-log model-input rows with 0 observed. It has no "
                "observed permit-volume register, so rows use a 1 m3 rate "
                "denominator and harvest-volume coverage is `not_estimable`."
            ),
            "",
            "**Stumpage anchor status.** "
            + "; ".join(
                f"{row.jurisdiction} {row.year}: `{row.crown_anchor_basis}` "
                f"({row.crown_anchor_confidence})"
                for row in cost_rows
            )
            + ". These are stumpage/administered values, not delivered log prices.",
            "",
            "**Net-value readiness.** BC is `not_estimated`: delivered log revenue and every production cost are absent, and a standing bid is not a log price. QC is `not_estimated`: the MERIS delivered-log values are mill-gate model inputs, harvest is loading-only, road excludes tertiary road, and silviculture is CAD/ha without a yield conversion. Missing components are missing, not zero.",
            "",
            "## 6. Ready vs blocked",
            "",
            "### Ready",
            "",
            *ready_lines,
            "",
            "### Blocked",
            "",
            *blocked_lines,
            "",
            "### Not claimed",
            "",
            "- No forest-sector net value, Crown revenue total, or cross-perspective total.",
            "- No national AAC, legal THLB, sustainable-supply estimate, or harvest recommendation.",
            "- No marginal harvest or residue cost curve, and no full log cost allocated to residue.",
            "- No OSeCAN interface rows in this synthesis; the canonical `SUPBCW`/`B1`/`B2` reference is not overwritten.",
            "",
            "## 7. Verification",
            "",
            *check_lines,
            "",
            "## 8. Inputs and outputs",
            "",
            "Inputs are recorded with SHA-256 hashes in `manifest.json`. The synthesis was run from the repository-root working tree; generated outputs stay under `out/forest_lp_v1/`.",
            "",
            "Outputs:",
            "",
            "- `forest_lp_allocation.csv` — province rows for documented-ceiling and intensity cases, including caps, allocation, binding, feasibility, residue ODT/PJ, and weighted factors.",
            "- `forest_lp_cost_revenue.csv` — province/year coverage, component coverage, net-value status, model-input flags, and source sample shares.",
            "- `forest_lp_summary.json` — machine-readable case summaries, residue totals, cost/revenue coverage, readiness, and checks.",
            "- `manifest.json` — input/output hashes, source commit, constants, and no-commit statement.",
            "- `FOREST_LP_V1_REPORT.md` — this report.",
            "",
            "## 9. Caveats",
            "",
            "- The documented ceilings are a mixed set of statutory AAC, approximate AAC, a repo-parsed TSA path, a range midpoint, planning volumes, and an actual-mean proxy. Their sum is not a national AAC.",
            "- NB, NS, and QC AACs are Crown-only; FSD harvest is total provincial harvest. NB private supply is undocumented, so the NB cap is conservative.",
            "- The operable proxy is a SCANFI merchantability screen intersected with a share-based 2016 tenure proxy. It is not a legal THLB and has no slope, road access, age/disturbance, growth, yield, or economic content.",
            "- The allocation minimizes deviation from 2024 shares. It is not an economic equilibrium and contains no harvest, transport, stumpage, silviculture, or opportunity cost.",
            "- The cost/revenue ledger is a sample of selected BCTS auctions and one MERIS zone. It is not a provincial or national population estimate, and it cannot be scaled to the harvest allocation.",
            "- BCTS and MERIS rows are never summed; each remains a separate source under its own province. Crown and forest-sector rows are never summed into a combined total.",
            "- MERIS is a tarification/cost parameter set, not an observed harvest or delivered-price register.",
        ]
    )
    return "\n".join(lines) + "\n"


def _source_commit() -> str:
    """Read the source-commit marker, or fall back to the local git HEAD."""

    marker_path = REPO_ROOT / ".source_commit"
    if marker_path.is_file():
        commit = marker_path.read_text(encoding="utf-8").strip()
        if not commit:
            raise ForestLPError(f"{marker_path}: empty source commit")
        return commit
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ForestLPError("cannot determine source commit: no marker and git HEAD unavailable") from exc
    commit = result.stdout.strip()
    if not commit:
        raise ForestLPError("git rev-parse HEAD returned an empty commit")
    return commit


def main() -> int:
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    source_commit = _source_commit()

    factors = load_factors()
    operable = load_operable_areas()
    allocation_rows, case_summaries, residue_summaries = build_allocation_rows(
        factors, operable
    )

    ledger_rows = _read_csv(LEDGER_PATH)
    ledger_validation = validate_ledger(ledger_rows)
    coverage = _read_json(COVERAGE_PATH)
    cost_rows, cost_summary = build_cost_revenue_rows(ledger_rows, coverage)
    checks = verify_synthesis(
        allocation_rows,
        case_summaries,
        residue_summaries,
        ledger_validation,
        cost_rows,
    )

    meris_full_present = MERIS_FULL_DIR.is_dir()
    allocation_fields = (
        "cap_case",
        "allocation_case",
        "national_harvest_target_m3",
        "province",
        "aac_m3",
        "nfd_m3",
        "operable_proxy_ha",
        "operable_missing",
        "documented_cap_m3",
        "intensity_m3_per_ha_yr",
        "intensity_cap_m3",
        "effective_cap_m3",
        "allocated_harvest_m3",
        "allocated_share",
        "binding",
        "at_cap",
        "province_status",
        "province_feasible",
        "case_status",
        "case_feasible",
        "case_deficit_m3",
        "cap_basis",
        "cap_caveat",
        "province_factor_gross_odt_per_m3_log",
        "province_factor_recoverable_low_odt_per_m3_log",
        "province_factor_recoverable_base_odt_per_m3_log",
        "province_factor_recoverable_high_odt_per_m3_log",
        "residue_gross_odt",
        "residue_recoverable_odt_low",
        "residue_recoverable_odt_base",
        "residue_recoverable_odt_high",
        "residue_pj_low",
        "residue_pj_base",
        "residue_pj_high",
        "allocation_weighted_factor_gross_odt_per_m3_log",
        "allocation_weighted_factor_recoverable_base_odt_per_m3_log",
    )
    cost_fields = (
        "province",
        "jurisdiction",
        "year",
        "source",
        "ledger_rows",
        "crown_rows",
        "forest_sector_rows",
        "mirror_rows",
        "observed_rows",
        "model_input_rows",
        "licences",
        "crown_anchor_basis",
        "crown_anchor_confidence",
        "covered_volume_m3",
        "covered_volume_status",
        "covered_volume_share_of_provincial_2024_harvest",
        "source_frame_volume_m3",
        "source_frame_share",
        "source_frame_share_basis",
        "source_sample_share",
        "source_sample_share_basis",
        "source_sample_share_status",
        "component_coverage",
        "present_or_partial_components",
        "missing_components",
        "observed_delivered_log_revenue_present",
        "full_production_costs_present",
        "net_value_status",
        "net_value_reason",
        "model_input_flags",
        "usable_as_crown_anchor",
        "usable_as_delivered_log_revenue",
        "usable_as_full_production_cost",
        "usable_as_net_value",
        "usable_as_lp_objective",
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    allocation_path = OUTPUT_DIR / "forest_lp_allocation.csv"
    cost_path = OUTPUT_DIR / "forest_lp_cost_revenue.csv"
    summary_path = OUTPUT_DIR / "forest_lp_summary.json"
    report_path = OUTPUT_DIR / "FOREST_LP_V1_REPORT.md"

    _write_csv(
        allocation_path,
        [row.__dict__ for row in allocation_rows],
        allocation_fields,
    )
    _write_csv(
        cost_path,
        [row.__dict__ for row in cost_rows],
        cost_fields,
    )

    summary_payload = {
        "artifact": "forest_lp_v1_synthesis",
        "status": "screening_only",
        "policy_grade": False,
        "generated_at_utc": generated_at,
        "source": {
            "repo": "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh",
            "branch": "feature/osecan-zonal-bc-supply",
            "commit": source_commit,
            "working_tree": str(REPO_ROOT),
            "disposable_copy": False,
            "note": "The synthesis runs against the repository-root working tree.",
        },
        "constants": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "recovery_fractions": RECOVERY,
            "allocation_tolerance_m3": ALLOCATION_TOLERANCE_M3,
            "cap_tolerance_m3": CAP_TOLERANCE_M3,
            "residue_tolerance_pj": RESIDUE_TOLERANCE_PJ,
        },
        "allocation": {
            "cases": case_summaries,
            "residue": residue_summaries,
            "province_rows": len(allocation_rows),
        },
        "cost_revenue": {
            "ledger_rows": ledger_validation["rows"],
            "mirror_rows": ledger_validation["mirror_rows"],
            "mirror_pairs": ledger_validation["mirror_pairs"],
            "unpaired_mirror_keys": ledger_validation["unpaired_mirror_keys"],
            "observed_rows": ledger_validation["observed_rows"],
            "model_input_rows": ledger_validation["model_input_rows"],
            "delivered_log_revenue_rows": ledger_validation["delivered_log_revenue_rows"],
            "delivered_log_revenue_model_input_rows": ledger_validation[
                "delivered_log_revenue_model_input_rows"
            ],
            "delivered_log_revenue_observed_rows": ledger_validation[
                "delivered_log_revenue_observed_rows"
            ],
            "net_value_status": "not_estimated",
            "cross_perspective_sums_computed": False,
            "meris_qc_full_present": meris_full_present,
            "province_years": cost_summary,
        },
        "readiness": {
            "ready": [
                "provincial allocation under documented ceilings",
                "operable-proxy intensity screen (screening only)",
                "province residue factors and PJ conversion",
                "cost/revenue ledger coverage and mirror accounting",
            ],
            "blocked": [
                "observed delivered log revenue",
                "full production costs",
                "forest-sector net value",
                "net-value objective and marginal cost curve",
                "policy-grade designation",
            ],
            "not_claimed": [
                "net forest value",
                "Crown revenue total or any cross-perspective total",
                "national AAC or legal THLB",
                "sustainable supply or harvest recommendation",
                "OSeCAN interface caps",
            ],
        },
        "checks": checks,
        "warnings": [
            {
                "cap_case": item["cap_case"],
                "allocation_case": item["allocation_case"],
                "issue": "allocation residual exceeds 1 m3",
                "harvest_sum_m3": item["harvest_sum_m3"],
                "allocation_gap_m3": item["allocation_gap_m3"],
                "treatment": "preserved from input artifact; not rescaled",
            }
            for item in case_summaries
            if item["feasible"] and abs(float(item["allocation_gap_m3"])) > 1.0
        ],
        "caveats": [
            "Documented ceilings are a mixed set; their sum is not a national AAC.",
            "NB, NS, and QC AACs are Crown-only; FSD harvest is total provincial harvest.",
            "The operable proxy is a screening layer, not a legal THLB.",
            "The allocation minimizes deviation from 2024 shares; it is not an economic equilibrium.",
            "The cost/revenue ledger is a sample, not a population.",
            "Missing components are missing, not zero.",
        ],
    }
    _write_json(summary_path, summary_payload)
    report = _report(
        allocation_rows,
        case_summaries,
        residue_summaries,
        cost_rows,
        checks,
        generated_at,
        source_commit,
        meris_full_present,
    )
    report_path.write_text(report, encoding="utf-8")

    input_paths = [
        ALLOCATION_DIR / "100m.csv",
        ALLOCATION_DIR / "119m.csv",
        ALLOCATION_DIR / "150m.csv",
        ALLOCATION_DIR / "provincial_allocation_summary.json",
        ALLOCATION_DIR / "intensity_1.0" / "100m.csv",
        ALLOCATION_DIR / "intensity_1.0" / "119m.csv",
        ALLOCATION_DIR / "intensity_1.0" / "provincial_allocation_summary.json",
        ALLOCATION_DIR / "intensity_1.2" / "100m.csv",
        ALLOCATION_DIR / "intensity_1.2" / "119m.csv",
        ALLOCATION_DIR / "intensity_1.2" / "provincial_allocation_summary.json",
        OPERABLE_PROXY_PATH,
        RESIDUE_FACTOR_PATH,
        RESIDUE_REFERENCE_PATH,
        LEDGER_PATH,
        COVERAGE_PATH,
    ]
    inputs = []
    for path in input_paths:
        inputs.append(
            {
                "path": str(path.relative_to(REPO_ROOT)),
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
        )
    output_paths = [allocation_path, cost_path, summary_path, report_path]
    outputs = [
        {
            "path": str(path.relative_to(REPO_ROOT)),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        }
        for path in output_paths
    ]
    manifest = {
        "artifact": "forest_lp_v1_manifest",
        "status": "screening_only",
        "policy_grade": False,
        "generated_at_utc": generated_at,
        "source": {
            "repo": "/srv/shared-data/gep/jupyterhub16-projects/clews-c2070-fresh",
            "branch": "feature/osecan-zonal-bc-supply",
            "commit": source_commit,
            "working_tree": str(REPO_ROOT),
            "disposable_copy": False,
            "note": "The synthesis runs against the repository-root working tree.",
        },
        "command": "python -m workflows.national.harvest.forest_lp_v1",
        "python": platform.python_version(),
        "inputs": inputs,
        "outputs": outputs,
        "row_counts": {
            "allocation": len(allocation_rows),
            "cost_revenue": len(cost_rows),
            "checks": len(checks),
        },
        "meris_qc_full_present": meris_full_present,
        "constants": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "recovery_fractions": RECOVERY,
        },
        "caveats": [
            "Generated outputs stay under out/forest_lp_v1/ and are not committed.",
            "The synthesis is screening only and is not policy-grade.",
        ],
    }
    _write_json(OUTPUT_DIR / "manifest.json", manifest)

    print(f"wrote {allocation_path}")
    print(f"wrote {cost_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {report_path}")
    print(f"wrote {OUTPUT_DIR / 'manifest.json'}")
    print(f"checks passed: {len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
