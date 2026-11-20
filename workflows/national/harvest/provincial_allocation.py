"""Provincial harvest-allocation LP for the CLEWs harvest ladder.

The national harvest ladder is exogenous.  This module allocates a national
total to the twelve reporting jurisdictions under documented ceilings while
staying as close as possible to the observed 2024 provincial harvest shares.

Model
-----
Variables ``h_p`` are annual harvest (m3/yr) by province or territory.  The
national total ``T`` is an equality constraint.  Every province has an upper
bound: a documented AAC where one exists, a documented range midpoint or
planning volume where no single AAC exists, and an explicitly flagged proxy
where the value is not a legal AAC.

The primary objective is the L1 distance from the 2024 observed share:

    target_p = observed_2024_p * T / national_observed_2024
    minimize sum_p |h_p - target_p|

The L1 optimum can be non-unique.  A lexicographic second stage fixes the L1
optimum (within 1 m3) and minimizes the maximum proportional increase above the
2024-share target, so the shortfall is distributed in proportion to each
province's observed harvest rather than dumping volume into small
jurisdictions.  Cap-forced decreases are fixed by the ceilings and are not part
of that maximum.  Both stages are solved with PuLP/CBC; no external solver is
used.

The optional managed-landbase intensity cap (``<= I m3/ha/yr`` on the tenure
proxy) is a sensitivity only.  The proxy is not a THLB.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

import pulp

REPO_ROOT = Path(__file__).resolve().parents[3]
HARVEST_DIR = REPO_ROOT / "data/derived/national/harvest_basis"
TEMPLATE_PATH = HARVEST_DIR / "harvest_basis_template.csv"
PROVINCES_PATH = HARVEST_DIR / "provinces.csv"
MANAGED_LANDBASE_PATH = (
    REPO_ROOT / "data/derived/national/managed_landbase/managed_landbase_province_summary.csv"
)
OPERABLE_PROXY_PATH = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/operable_proxy/operable_proxy_by_province.csv"
)
OPERABLE_PROXY_SUMMARY_PATH = (
    REPO_ROOT
    / "data/derived/national/managed_landbase/operable_proxy/operable_proxy_summary.json"
)
OPERABLE_PROXY_THRESHOLD_CASE = "base"
FEASIBILITY_DOC_PATH = REPO_ROOT / "planning/harvest-aac-feasibility.md"
OUTPUT_DIR = HARVEST_DIR / "provincial_allocation"

PROVINCE_ORDER = ("BC", "AB", "SK", "MB", "ON", "QC", "NB", "NS", "NL", "PE", "YT", "NT", "NU")
REPORTING_PROVINCES = PROVINCE_ORDER[:-1]

NATIONAL_OBSERVED_2024_M3 = 118_977_339.0
LADDER_TOTALS = (("100m", 100_000_000.0), ("119m", 118_977_339.0), ("150m", 150_000_000.0))
DIAGNOSTIC_TOTAL_200M = 200_000_000.0
MAX_NORMAL_HARVEST_M3 = 150_000_000.0

DOCUMENTED_AAC_PARTIAL_SUM_M3 = 157_343_387.0
NFD_WOOD_SUPPLY_SUM_M3 = 207_680_142.0

MB_AAC_RANGE_M3 = (7_390_000.0, 7_990_000.0)
MB_AAC_MIDPOINT_M3 = sum(MB_AAC_RANGE_M3) / 2.0
NS_PRIVATE_AAC_M3 = 4_030_000.0
PE_PLANNING_VOLUME_M3 = 460_000.0
BC_REPORTED_CROWN_AAC_M3 = 60_000_000.0

L1_TOLERANCE_M3 = 1.0
CAP_TOLERANCE_M3 = 1.0

INTENSITY_CAP_LEVELS = ("1.0", "1.2")
INTENSITY_PROXY_CAVEAT = (
    "The operable-proxy landbase is a SCANFI merchantability screen intersected "
    "with a share-based 2016 tenure proxy. It is not a legal THLB: it carries no "
    "slope, road access, age/disturbance, growth, yield, or economic constraint. "
    "Intensity caps are screening sensitivities only."
)
MANAGED_PROXY_COMPARISON_CAVEAT = (
    "The 2016 tenure-union managed proxy (166.28 Mha) is retained only as a "
    "comparison basis. It is not a THLB and is superseded by the tighter "
    "operable proxy for the intensity screen."
)


class AllocationError(RuntimeError):
    """Raised when the allocation model cannot produce a documented answer."""


@dataclass(frozen=True)
class ProvinceBasis:
    province: str
    observed_2024_m3: float
    aac_m3: float | None
    nfd_m3: float | None
    managed_proxy_ha: float | None
    upper_bound_m3: float
    basis: str
    caveat: str
    operable_proxy_ha: float | None = None
    operable_missing: bool = False


@dataclass(frozen=True)
class AllocationRow:
    province: str
    harvest_m3: float
    observed_2024_m3: float
    observed_share: float
    allocated_share: float
    aac_m3: float | None
    nfd_m3: float | None
    upper_bound_m3: float
    binding: bool
    basis: str
    caveat: str
    operable_proxy_ha: float | None = None
    operable_missing: bool = False


@dataclass(frozen=True)
class CaseResult:
    label: str
    national_total_m3: float
    status: str
    rows: tuple[AllocationRow, ...]
    capacity_m3: float
    optimal_l1_deviation_m3: float | None
    achieved_l1_deviation_m3: float | None
    max_increase_m3: float | None
    max_relative_increase: float | None
    max_deviation_m3: float | None
    status_quo_exceedance_m3: float
    deficit_m3: float

    @property
    def is_optimal(self) -> bool:
        return self.status == "optimal"

    @property
    def binding_provinces(self) -> tuple[str, ...]:
        return tuple(row.province for row in self.rows if row.binding)

    @property
    def harvest_sum_m3(self) -> float:
        return sum(row.harvest_m3 for row in self.rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise AllocationError(f"required input is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _optional_float(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


def _require_float(value: str | None, *, field: str, province: str) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise AllocationError(f"{province}: required numeric field {field} is blank")
    return parsed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_operable_proxy() -> dict[str, float | None]:
    """Read the base-case operable-proxy area by province/territory.

    PE and NU have no accepted 2016 tenure coverage and are intentionally blank
    in the source.  Any other missing jurisdiction, or a non-positive area, is
    an input-contract failure rather than a zero.
    """
    rows = _read_csv(OPERABLE_PROXY_PATH)
    base_rows = [
        row
        for row in rows
        if row.get("ecoprovince") == "ALL"
        and row.get("threshold_case") == OPERABLE_PROXY_THRESHOLD_CASE
    ]
    if len(base_rows) != len(PROVINCE_ORDER):
        raise AllocationError(
            "operable proxy must have one province-total row per jurisdiction at "
            f"threshold_case={OPERABLE_PROXY_THRESHOLD_CASE!r}; got {len(base_rows)}"
        )
    if len({row["province"] for row in base_rows}) != len(base_rows):
        raise AllocationError("operable proxy has duplicate province-total rows")

    areas: dict[str, float | None] = {}
    for row in base_rows:
        province = row["province"]
        if province not in PROVINCE_ORDER:
            raise AllocationError(f"operable proxy has unexpected province {province!r}")
        area = _optional_float(row.get("operable_proxy_ha"))
        if area is not None and area <= 0.0:
            raise AllocationError(f"{province}: operable proxy area must be positive, got {area}")
        areas[province] = area

    missing = sorted(province for province, area in areas.items() if area is None)
    if missing != ["NU", "PE"]:
        raise AllocationError(
            "operable proxy is missing for unexpected jurisdictions: "
            f"{', '.join(missing) or 'none'}; expected only PE and NU"
        )
    return areas


def load_province_basis() -> tuple[ProvinceBasis, ...]:
    """Parse the 2024 template, tenure proxy, and operable proxy into trusted caps."""
    template_rows = _read_csv(TEMPLATE_PATH)
    observed_rows = {
        row["province"]: row
        for row in template_rows
        if row.get("year") == "2024" and row.get("province") in REPORTING_PROVINCES
    }
    missing = [province for province in REPORTING_PROVINCES if province not in observed_rows]
    if missing:
        raise AllocationError(f"harvest template is missing 2024 rows for: {', '.join(missing)}")

    proxies = {
        row["province"]: _optional_float(row.get("managed_proxy_ha"))
        for row in _read_csv(MANAGED_LANDBASE_PATH)
    }
    operable = load_operable_proxy()

    basis_rows: list[ProvinceBasis] = []
    for province in REPORTING_PROVINCES:
        row = observed_rows[province]
        observed = _require_float(row.get("actual_harvest_m3"), field="actual_harvest_m3", province=province)
        aac = _optional_float(row.get("aac_m3"))
        nfd = _optional_float(row.get("wood_supply_m3"))
        proxy = proxies.get(province)
        operable_ha = operable.get(province)
        operable_missing = operable_ha is None
        upper_bound, basis, caveat = _resolve_cap(province, aac, nfd)
        if operable_missing:
            basis = f"{basis}+operable_missing"
            caveat = (
                f"{caveat} No operable proxy (2016 tenure coverage is missing); "
                "the documented cap is retained and flagged operable_missing; "
                "the missing area is not substituted with zero."
            )
        basis_rows.append(
            ProvinceBasis(
                province=province,
                observed_2024_m3=observed,
                aac_m3=aac,
                nfd_m3=nfd,
                managed_proxy_ha=proxy,
                upper_bound_m3=upper_bound,
                basis=basis,
                caveat=caveat,
                operable_proxy_ha=operable_ha,
                operable_missing=operable_missing,
            )
        )

    basis_rows.append(
        ProvinceBasis(
            province="NU",
            observed_2024_m3=0.0,
            aac_m3=None,
            nfd_m3=None,
            managed_proxy_ha=None,
            upper_bound_m3=0.0,
            basis="not_assessed_no_data+operable_missing",
            caveat=(
                "No FSD harvest row, no AAC, no managed-landbase proxy, and no "
                "operable proxy; held at zero, not assumed commercially exhausted. "
                "operable_missing is flagged; no area is substituted."
            ),
            operable_proxy_ha=None,
            operable_missing=True,
        )
    )

    observed_sum = sum(row.observed_2024_m3 for row in basis_rows)
    if abs(observed_sum - NATIONAL_OBSERVED_2024_M3) > 1.0:
        raise AllocationError(
            "2024 observed provincial harvest does not reconcile to the national total: "
            f"{observed_sum:,.0f} vs {NATIONAL_OBSERVED_2024_M3:,.0f} m3"
        )
    return tuple(basis_rows)


def _resolve_cap(
    province: str, aac_m3: float | None, nfd_m3: float | None
) -> tuple[float, str, str]:
    """Return (upper bound, basis label, caveat) with explicit provenance."""
    if province == "BC":
        if aac_m3 is None:
            raise AllocationError("BC: repo-parsed TSA AAC is missing")
        return (
            aac_m3,
            "repo_parsed_tsa_aac",
            "Repo-parsed 2024 TSA AAC. Current reported Crown AAC is ~58-60M m3/yr and the 2028+ path is 43.37M; see the feasibility note.",
        )
    if province == "MB":
        return (
            MB_AAC_MIDPOINT_M3,
            "documented_range_midpoint_proxy",
            "AAC documented only as a 7.39-7.99M m3/yr range; midpoint used as a planning proxy, not a legal AAC. Non-binding at every ladder level.",
        )
    if province == "PE":
        return (
            PE_PLANNING_VOLUME_M3,
            "nfd_planning_volume_no_aac",
            "No statutory AAC; 460,000 m3/yr = 40,000 Crown + 420,000 private planning volume. Not a legal AAC.",
        )
    if province == "NS":
        if aac_m3 is None:
            raise AllocationError("NS: Crown AAC is missing")
        return (
            aac_m3 + NS_PRIVATE_AAC_M3,
            "crown_plus_documented_private_aac",
            "Crown AAC 1.72M plus documented private AAC 4.03M = 5.75M m3/yr; total harvest includes private wood.",
        )
    if province == "NT":
        if aac_m3 is None:
            raise AllocationError("NT: actual-mean proxy is missing")
        return (
            aac_m3,
            "actual_mean_proxy_no_territory_aac",
            "36,376 m3/yr is the 2020-2024 actual-harvest mean used as a proxy; there is no territory-wide legal AAC.",
        )
    if aac_m3 is not None:
        basis = "legal_crown_aac" if province in {"QC", "NB", "NL", "YT"} else "crown_aac_approx"
        caveat = "Crown AAC; total provincial harvest may include private wood." if province in {"QC", "NB"} else "Documented 2024/2025 AAC."
        return aac_m3, basis, caveat
    if nfd_m3 is not None:
        return nfd_m3, "nfd_wood_supply", "No AAC; 2024 NFD potential supply used as the ceiling."
    raise AllocationError(f"{province}: no documented AAC, NFD, or planning proxy")


def with_managed_intensity_cap(
    basis: Iterable[ProvinceBasis], intensity_m3_per_ha_yr: float
) -> tuple[ProvinceBasis, ...]:
    """Apply the legacy managed-proxy intensity screen (comparison only)."""
    if intensity_m3_per_ha_yr <= 0:
        raise AllocationError("managed-landbase intensity cap must be positive")
    capped: list[ProvinceBasis] = []
    for row in basis:
        if row.managed_proxy_ha is None or row.upper_bound_m3 == 0.0:
            capped.append(row)
            continue
        intensity_bound = intensity_m3_per_ha_yr * row.managed_proxy_ha
        capped.append(
            replace(
                row,
                upper_bound_m3=min(row.upper_bound_m3, intensity_bound),
                basis=f"{row.basis}+managed_intensity_{intensity_m3_per_ha_yr:g}",
                caveat=(
                    f"{row.caveat} Managed proxy {row.managed_proxy_ha / 1e6:.2f} Mha implies "
                    f"{intensity_bound / 1e6:.2f} Mm3/yr at {intensity_m3_per_ha_yr:g} m3/ha/yr."
                ),
            )
        )
    return tuple(capped)


def with_operable_intensity_cap(
    basis: Iterable[ProvinceBasis], intensity_m3_per_ha_yr: float
) -> tuple[ProvinceBasis, ...]:
    """Apply the operable-proxy intensity screen to each documented cap.

    ``upper_p = min(documented AAC/NFD/proxy, I * operable_proxy_ha)``.  PE and
    NU have no operable proxy: their documented caps are retained and flagged
    ``operable_missing``; the missing area is never replaced by zero.
    """
    if intensity_m3_per_ha_yr <= 0:
        raise AllocationError("operable-proxy intensity cap must be positive")
    capped: list[ProvinceBasis] = []
    for row in basis:
        if row.operable_proxy_ha is None:
            capped.append(
                replace(
                    row,
                    basis=f"{row.basis}+operable_missing_fallback",
                    caveat=(
                        f"{row.caveat} Operable proxy missing; documented cap "
                        f"{row.upper_bound_m3 / 1e6:.3f} Mm3/yr retained at "
                        f"{intensity_m3_per_ha_yr:g} m3/ha/yr (no zero substitution)."
                    ),
                )
            )
            continue
        if row.upper_bound_m3 == 0.0:
            capped.append(row)
            continue
        intensity_bound = intensity_m3_per_ha_yr * row.operable_proxy_ha
        effective = min(row.upper_bound_m3, intensity_bound)
        binding_basis = (
            "operable" if intensity_bound < row.upper_bound_m3 else "documented"
        )
        capped.append(
            replace(
                row,
                upper_bound_m3=effective,
                basis=f"{row.basis}+operable_intensity_{intensity_m3_per_ha_yr:g}",
                caveat=(
                    f"{row.caveat} Operable proxy {row.operable_proxy_ha / 1e6:.3f} Mha "
                    f"implies {intensity_bound / 1e6:.3f} Mm3/yr at "
                    f"{intensity_m3_per_ha_yr:g} m3/ha/yr; the {binding_basis} cap binds "
                    f"at {effective / 1e6:.3f} Mm3/yr."
                ),
            )
        )
    return tuple(capped)


def with_bc_cap(basis: Iterable[ProvinceBasis], bc_upper_bound_m3: float) -> tuple[ProvinceBasis, ...]:
    """Replace the BC cap for a documented-alternative sensitivity."""
    if bc_upper_bound_m3 <= 0:
        raise AllocationError("BC cap sensitivity must be positive")
    return tuple(
        replace(
            row,
            upper_bound_m3=bc_upper_bound_m3 if row.province == "BC" else row.upper_bound_m3,
            basis=f"{row.basis}+bc_cap_alternative" if row.province == "BC" else row.basis,
            caveat=(
                f"{row.caveat} Sensitivity uses {bc_upper_bound_m3 / 1e6:.1f} Mm3/yr reported current Crown AAC."
                if row.province == "BC"
                else row.caveat
            ),
        )
        for row in basis
    )


def _targets(basis: Iterable[ProvinceBasis], national_total_m3: float) -> dict[str, float]:
    return {
        row.province: row.observed_2024_m3 * national_total_m3 / NATIONAL_OBSERVED_2024_M3
        for row in basis
    }


def _status_quo_exceedance(basis: Iterable[ProvinceBasis], national_total_m3: float) -> float:
    targets = _targets(basis, national_total_m3)
    return sum(max(0.0, targets[row.province] - row.upper_bound_m3) for row in basis)


def solve_allocation(
    basis: Iterable[ProvinceBasis],
    national_total_m3: float,
    *,
    label: str,
) -> CaseResult:
    """Solve the two-stage L1/minimax allocation for one national total."""
    rows = tuple(basis)
    if national_total_m3 <= 0:
        raise AllocationError("national harvest total must be positive")
    available = sum(row.upper_bound_m3 for row in rows)
    status_quo_exceedance = _status_quo_exceedance(rows, national_total_m3)

    if national_total_m3 > available + CAP_TOLERANCE_M3:
        return CaseResult(
            label=label,
            national_total_m3=national_total_m3,
            status="infeasible",
            rows=(),
            capacity_m3=available,
            optimal_l1_deviation_m3=None,
            achieved_l1_deviation_m3=None,
            max_increase_m3=None,
            max_relative_increase=None,
            max_deviation_m3=None,
            status_quo_exceedance_m3=status_quo_exceedance,
            deficit_m3=national_total_m3 - available,
        )

    targets = _targets(rows, national_total_m3)
    scale = 1_000_000.0
    total_scaled = national_total_m3 / scale
    targets_scaled = {province: value / scale for province, value in targets.items()}
    model = pulp.LpProblem(f"provincial_allocation_{label}", pulp.LpMinimize)
    harvest = {
        row.province: pulp.LpVariable(
            f"h_{row.province}", lowBound=0, upBound=row.upper_bound_m3 / scale
        )
        for row in rows
    }
    deviation = {
        row.province: pulp.LpVariable(f"d_{row.province}", lowBound=0) for row in rows
    }

    model += pulp.lpSum(harvest.values()) == total_scaled
    for row in rows:
        model += deviation[row.province] >= harvest[row.province] - targets_scaled[row.province]
        model += deviation[row.province] >= targets_scaled[row.province] - harvest[row.province]

    solver = pulp.PULP_CBC_CMD(msg=False, threads=1)

    model += pulp.lpSum(deviation.values())
    status = model.solve(solver)
    if status != pulp.LpStatusOptimal:
        raise AllocationError(f"{label}: primary L1 solve failed with status {pulp.LpStatus[status]}")
    optimal_l1_scaled = float(pulp.value(model.objective))

    max_relative_increase = pulp.LpVariable("max_relative_increase", lowBound=0)
    for row in rows:
        increase = pulp.LpVariable(f"u_{row.province}", lowBound=0)
        model += increase >= harvest[row.province] - targets_scaled[row.province]
        if row.observed_2024_m3 > 0.0:
            model += max_relative_increase * (row.observed_2024_m3 / scale) >= increase
    model += pulp.lpSum(deviation.values()) <= optimal_l1_scaled + L1_TOLERANCE_M3 / scale
    model.setObjective(max_relative_increase)
    status = model.solve(solver)
    if status != pulp.LpStatusOptimal:
        raise AllocationError(f"{label}: minimax tie-break solve failed with status {pulp.LpStatus[status]}")

    allocated = {
        province: float(variable.value()) * scale for province, variable in harvest.items()
    }
    optimal_l1 = optimal_l1_scaled * scale
    if any(value is None or not math.isfinite(value) or value < -CAP_TOLERANCE_M3 for value in allocated.values()):
        raise AllocationError(f"{label}: solver returned a non-finite or negative harvest value")

    allocation_rows = tuple(
        AllocationRow(
            province=row.province,
            harvest_m3=max(0.0, allocated[row.province]),
            observed_2024_m3=row.observed_2024_m3,
            observed_share=row.observed_2024_m3 / NATIONAL_OBSERVED_2024_M3,
            allocated_share=allocated[row.province] / national_total_m3,
            aac_m3=row.aac_m3,
            nfd_m3=row.nfd_m3,
            upper_bound_m3=row.upper_bound_m3,
            binding=row.upper_bound_m3 > 0.0
            and row.upper_bound_m3 - allocated[row.province] <= CAP_TOLERANCE_M3,
            basis=row.basis,
            caveat=row.caveat,
            operable_proxy_ha=row.operable_proxy_ha,
            operable_missing=row.operable_missing,
        )
        for row in rows
    )

    achieved_l1 = sum(abs(row.harvest_m3 - targets[row.province]) for row in allocation_rows)
    increases = [
        (row.harvest_m3 - targets[row.province]) / row.observed_2024_m3
        for row in allocation_rows
        if row.observed_2024_m3 > 0.0
    ]
    solved_max_relative_increase = max(increases)
    solved_max_increase = max(
        max(0.0, row.harvest_m3 - targets[row.province]) for row in allocation_rows
    )
    solved_max_deviation = max(
        abs(row.harvest_m3 - targets[row.province]) for row in allocation_rows
    )
    return CaseResult(
        label=label,
        national_total_m3=national_total_m3,
        status="optimal",
        rows=allocation_rows,
        capacity_m3=available,
        optimal_l1_deviation_m3=optimal_l1,
        achieved_l1_deviation_m3=achieved_l1,
        max_increase_m3=solved_max_increase,
        max_relative_increase=solved_max_relative_increase,
        max_deviation_m3=solved_max_deviation,
        status_quo_exceedance_m3=status_quo_exceedance,
        deficit_m3=0.0,
    )


def _case_payload(result: CaseResult) -> dict[str, object]:
    return {
        "label": result.label,
        "national_total_m3": result.national_total_m3,
        "status": result.status,
        "feasible": result.is_optimal,
        "harvest_sum_m3": result.harvest_sum_m3 if result.is_optimal else None,
        "capacity_m3": result.capacity_m3,
        "optimal_l1_deviation_m3": result.optimal_l1_deviation_m3,
        "achieved_l1_deviation_m3": result.achieved_l1_deviation_m3,
        "max_increase_m3": result.max_increase_m3,
        "max_relative_increase": result.max_relative_increase,
        "max_deviation_m3": result.max_deviation_m3,
        "status_quo_exceedance_m3": result.status_quo_exceedance_m3,
        "deficit_m3": result.deficit_m3,
        "binding_provinces": list(result.binding_provinces),
        "allocation": [
            {
                "province": row.province,
                "harvest_m3": row.harvest_m3,
                "observed_2024_m3": row.observed_2024_m3,
                "observed_share": row.observed_share,
                "allocated_share": row.allocated_share,
                "aac_m3": row.aac_m3,
                "nfd_m3": row.nfd_m3,
                "upper_bound_m3": row.upper_bound_m3,
                "binding": row.binding,
                "basis": row.basis,
                "caveat": row.caveat,
                "operable_proxy_ha": row.operable_proxy_ha,
                "operable_missing": row.operable_missing,
            }
            for row in result.rows
        ],
    }


def load_operable_proxy_sensitivity() -> dict[str, object]:
    """Read the threshold sensitivity block from the operable-proxy summary."""
    if not OPERABLE_PROXY_SUMMARY_PATH.is_file():
        raise AllocationError(f"required input is missing: {OPERABLE_PROXY_SUMMARY_PATH}")
    payload = json.loads(OPERABLE_PROXY_SUMMARY_PATH.read_text(encoding="utf-8"))
    sensitivity = payload.get("sensitivity")
    thresholds = payload.get("thresholds")
    if not isinstance(sensitivity, dict) or not isinstance(thresholds, dict):
        raise AllocationError("operable proxy summary is missing sensitivity/thresholds")
    return {
        "default_case": thresholds.get("default_case"),
        "cases": thresholds.get("cases"),
        "national_merchantable_ha": payload.get("national"),
        "cases_area": {
            key: {
                "label": block.get("label"),
                "national_ha": block.get("national_ha"),
                "share_of_treed": block.get("share_of_treed"),
            }
            for key, block in sensitivity.items()
        },
    }


def build_summary(
    basis: tuple[ProvinceBasis, ...],
    cases: dict[str, CaseResult],
    intensity_results: dict[str, dict[str, CaseResult]],
    bc_sensitivity: CaseResult,
) -> dict[str, object]:
    upper_bound_sum = sum(row.upper_bound_m3 for row in basis)
    generous_basis = with_bc_cap(
        tuple(
            replace(row, upper_bound_m3=MB_AAC_RANGE_M3[1]) if row.province == "MB" else row
            for row in basis
        ),
        BC_REPORTED_CROWN_AAC_M3,
    )
    generous_sum = sum(row.upper_bound_m3 for row in generous_basis)
    cases_payload = {label: _case_payload(result) for label, result in cases.items()}
    cases_payload["200m"]["deficit_vs_generous_m3"] = DIAGNOSTIC_TOTAL_200M - generous_sum

    operable_area_ha = sum(
        row.operable_proxy_ha for row in basis if row.operable_proxy_ha is not None
    )
    operable_missing = sorted(row.province for row in basis if row.operable_missing)
    operable_capacities = {
        intensity: sum(
            row.upper_bound_m3 for row in with_operable_intensity_cap(basis, float(intensity))
        )
        for intensity in INTENSITY_CAP_LEVELS
    }
    national_envelope = {
        intensity: {
            "national_operable_proxy_ha": operable_area_ha,
            "capacity_m3": float(intensity) * operable_area_ha,
            "deficits_m3": {
                label: max(0.0, total - float(intensity) * operable_area_ha)
                for label, total in LADDER_TOTALS
            },
        }
        for intensity in INTENSITY_CAP_LEVELS
    }
    managed_capacities = {
        intensity: sum(
            row.upper_bound_m3 for row in with_managed_intensity_cap(basis, float(intensity))
        )
        for intensity in INTENSITY_CAP_LEVELS
    }
    managed_comparison = {
        "status": "comparison only; superseded by the operable-proxy screen",
        "proxy": MANAGED_PROXY_COMPARISON_CAVEAT,
        "national_managed_proxy_ha": sum(
            row.managed_proxy_ha for row in basis if row.managed_proxy_ha is not None
        ),
        "capacities_m3": managed_capacities,
        "deficits_m3": {
            intensity: {
                label: max(0.0, total - managed_capacities[intensity])
                for label, total in LADDER_TOTALS
            }
            for intensity in INTENSITY_CAP_LEVELS
        },
    }
    operable_sensitivity = load_operable_proxy_sensitivity()

    return {
        "product": "provincial harvest-allocation LP",
        "status": "screening allocation; documented ceilings are not a legal national AAC",
        "national_observed_2024_m3": NATIONAL_OBSERVED_2024_M3,
        "documented_aac_partial_sum_m3": DOCUMENTED_AAC_PARTIAL_SUM_M3,
        "nfd_wood_supply_sum_m3": NFD_WOOD_SUPPLY_SUM_M3,
        "upper_bound_sum_m3": upper_bound_sum,
        "upper_bound_sum_components": {
            "documented_aac_partial_sum_m3": DOCUMENTED_AAC_PARTIAL_SUM_M3,
            "mb_midpoint_m3": MB_AAC_MIDPOINT_M3,
            "pe_planning_m3": PE_PLANNING_VOLUME_M3,
            "ns_private_added_m3": NS_PRIVATE_AAC_M3,
        },
        "most_generous_documented_ceiling_m3": generous_sum,
        "most_generous_documented_ceiling_components": {
            "bc_reported_crown_aac_m3": BC_REPORTED_CROWN_AAC_M3,
            "mb_upper_range_m3": MB_AAC_RANGE_M3[1],
            "ns_combined_m3": 1_720_000.0 + NS_PRIVATE_AAC_M3,
            "pe_planning_m3": PE_PLANNING_VOLUME_M3,
        },
        "objective": {
            "primary": "minimize sum_p |h_p - observed_2024_share_p * national_total|",
            "secondary": "lexicographic: after fixing the L1 optimum within 1 m3, minimize the maximum proportional increase above the 2024-share target; cap-forced decreases are fixed",
            "solver": "PuLP 2.9.0 / CBC (bundled), threads=1",
        },
        "cases": cases_payload,
        "sensitivity_operable_intensity": {
            level: {
                intensity: _case_payload(result) for intensity, result in by_intensity.items()
            }
            for level, by_intensity in intensity_results.items()
        },
        "thlb_intensity_screen": {
            "intensity_values_m3_per_ha_yr": [float(value) for value in INTENSITY_CAP_LEVELS],
            "proxy": INTENSITY_PROXY_CAVEAT,
            "operable_proxy": {
                "path": str(OPERABLE_PROXY_PATH.relative_to(REPO_ROOT)),
                "threshold_case": OPERABLE_PROXY_THRESHOLD_CASE,
                "national_operable_proxy_ha": operable_area_ha,
                "national_managed_proxy_ha": managed_comparison["national_managed_proxy_ha"],
                "operable_share_of_managed_proxy": (
                    operable_area_ha / managed_comparison["national_managed_proxy_ha"]
                    if managed_comparison["national_managed_proxy_ha"] > 0
                    else None
                ),
                "missing_provinces": operable_missing,
                "missing_policy": "documented cap retained; flagged operable_missing; no zero substitution",
                "threshold_sensitivity": operable_sensitivity,
                "by_province": [
                    {
                        "province": row.province,
                        "operable_proxy_ha": row.operable_proxy_ha,
                        "operable_missing": row.operable_missing,
                    }
                    for row in basis
                ],
            },
            "output_dirs": [
                f"data/derived/national/harvest_basis/provincial_allocation/intensity_{intensity}"
                for intensity in INTENSITY_CAP_LEVELS
            ],
            "provincial_capacity_m3": operable_capacities,
            "deficits_m3": {
                level: {
                    intensity: by_intensity[intensity].deficit_m3
                    for intensity in INTENSITY_CAP_LEVELS
                }
                for level, by_intensity in intensity_results.items()
            },
            "status_by_case": {
                level: {
                    intensity: {
                        "provincial_lp": by_intensity[intensity].status,
                        "national_envelope": (
                            "feasible"
                            if national_envelope[intensity]["deficits_m3"][level] <= 0.0
                            else "infeasible"
                        ),
                    }
                    for intensity in INTENSITY_CAP_LEVELS
                }
                for level, by_intensity in intensity_results.items()
            },
            "national_envelope": national_envelope,
            "national_envelope_note": (
                "The national envelope is I * national operable area and ignores "
                "provincial documented-cap allocation. It is reported for comparison "
                "with the per-province min-cap LP, which is the model of record."
            ),
            "managed_proxy_comparison": managed_comparison,
        },
        "sensitivity_bc_crown_60m": _case_payload(bc_sensitivity),
        "caveats": [
            "Upper bounds are a mixed set of statutory AAC, approximate AAC, a repo TSA path, a documented range midpoint, planning volumes, and actual-mean proxies. Their sum is not a national AAC.",
            "NB, NS, and QC AACs are Crown-only; FSD harvest is total provincial harvest. NB private supply is undocumented, so the NB cap is conservative.",
            "The operable-proxy landbase is a SCANFI merchantability screen intersected with a share-based 2016 tenure proxy. It is not a legal THLB and has no slope, road access, age/disturbance, growth, yield, or economic content.",
            "PE and NU have no operable proxy; their documented caps are retained and flagged operable_missing, not set to zero.",
            "The managed-landbase proxy is retained only as a comparison basis and is not used for the intensity screen of record.",
            "The allocation minimizes deviation from 2024 shares. It is not a forecast, a sustainable-supply estimate, or an economic equilibrium.",
            "No harvest, transport, stumpage, silviculture, or forest-sector opportunity cost enters the objective.",
        ],
    }


def _format_float(value: float | None) -> str:
    return "" if value is None else f"{value:.1f}"


def write_case_csv(path: Path, result: CaseResult) -> None:
    if not result.is_optimal:
        raise AllocationError(f"refusing to write a CSV for non-optimal case {result.label}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = (
        "province",
        "harvest_m3",
        "observed_2024_m3",
        "observed_share",
        "allocated_share",
        "aac_m3",
        "nfd_m3",
        "operable_proxy_ha",
        "operable_missing",
        "upper_bound",
        "binding",
        "basis",
        "caveat",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in result.rows:
            writer.writerow(
                {
                    "province": row.province,
                    "harvest_m3": _format_float(row.harvest_m3),
                    "observed_2024_m3": _format_float(row.observed_2024_m3),
                    "observed_share": f"{row.observed_share:.9f}",
                    "allocated_share": f"{row.allocated_share:.9f}",
                    "aac_m3": _format_float(row.aac_m3),
                    "nfd_m3": _format_float(row.nfd_m3),
                    "operable_proxy_ha": _format_float(row.operable_proxy_ha),
                    "operable_missing": "true" if row.operable_missing else "false",
                    "upper_bound": _format_float(row.upper_bound_m3),
                    "binding": "true" if row.binding else "false",
                    "basis": row.basis,
                    "caveat": row.caveat,
                }
            )


def write_intensity_outputs(
    intensity_results: dict[str, dict[str, CaseResult]],
) -> tuple[Path, ...]:
    """Write one CSV per feasible case and a summary per intensity screen."""
    output_paths: list[Path] = []
    for intensity in INTENSITY_CAP_LEVELS:
        by_case = {
            label: intensity_results[label][intensity]
            for label in ("100m", "119m", "150m")
        }
        intensity_dir = OUTPUT_DIR / f"intensity_{intensity}"
        intensity_dir.mkdir(parents=True, exist_ok=True)

        for label, result in by_case.items():
            path = intensity_dir / f"{label}.csv"
            if not result.is_optimal:
                if path.exists():
                    path.unlink()
                continue
            write_case_csv(path, result)
            output_paths.append(path)

        capacity_m3 = next(iter(by_case.values())).capacity_m3
        operable_area_ha = sum(
            row.operable_proxy_ha for row in by_case["100m"].rows if row.operable_proxy_ha is not None
        )
        national_envelope_capacity_m3 = float(intensity) * operable_area_ha
        summary_payload = {
            "product": "provincial harvest-allocation LP - operable-proxy intensity screen",
            "status": "screening sensitivity only; the operable proxy is not a legal THLB",
            "intensity_m3_per_ha_yr": float(intensity),
            "capacity_m3": capacity_m3,
            "national_operable_proxy_ha": operable_area_ha,
            "national_envelope_capacity_m3": national_envelope_capacity_m3,
            "national_observed_2024_m3": NATIONAL_OBSERVED_2024_M3,
            "cases": {label: _case_payload(result) for label, result in by_case.items()},
            "national_envelope_deficits_m3": {
                label: max(0.0, result.national_total_m3 - national_envelope_capacity_m3)
                for label, result in by_case.items()
            },
            "status_by_case": {
                label: {
                    "provincial_lp": result.status,
                    "national_envelope": (
                        "feasible"
                        if result.national_total_m3 <= national_envelope_capacity_m3
                        else "infeasible"
                    ),
                }
                for label, result in by_case.items()
            },
            "province_caps": [
                {
                    "province": row.province,
                    "operable_proxy_ha": row.operable_proxy_ha,
                    "operable_missing": row.operable_missing,
                    "upper_bound_m3": row.upper_bound_m3,
                    "basis": row.basis,
                }
                for row in by_case["100m"].rows
            ],
            "caveats": [
                INTENSITY_PROXY_CAVEAT,
                "Effective caps are min(documented AAC/NFD/proxy, intensity * operable_proxy_ha).",
                "PE and NU have no operable proxy; their documented caps are retained and flagged operable_missing, not set to zero.",
                "The national envelope (intensity * national operable area) is a diagnostic; the per-province min-cap LP is the model of record.",
                "This is a landbase feasibility screen, not an energy-model run, legal AAC, or sustainable-supply estimate.",
            ],
        }
        summary_path = intensity_dir / "provincial_allocation_summary.json"
        summary_path.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
        output_paths.append(summary_path)
    return tuple(output_paths)


def _markdown_table(case: CaseResult) -> str:
    lines = [
        "| Province | Harvest (Mm3) | 2024 observed (Mm3) | 2024 share | Allocated share | AAC (Mm3) | NFD (Mm3) | Upper bound (Mm3) | Binding | Basis |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in case.rows:
        lines.append(
            "| {p} | {h:.3f} | {o:.3f} | {os:.3%} | {as_:.3%} | {aac} | {nfd} | {ub:.3f} | {b} | `{basis}` |".format(
                p=row.province,
                h=row.harvest_m3 / 1e6,
                o=row.observed_2024_m3 / 1e6,
                os=row.observed_share,
                as_=row.allocated_share,
                aac="—" if row.aac_m3 is None else f"{row.aac_m3 / 1e6:.3f}",
                nfd="—" if row.nfd_m3 is None else f"{row.nfd_m3 / 1e6:.3f}",
                ub=row.upper_bound_m3 / 1e6,
                b="yes" if row.binding else "",
                basis=row.basis,
            )
        )
    return "\n".join(lines)


def _status_quo_shortfalls(case: CaseResult) -> dict[str, float]:
    return {
        row.province: max(0.0, row.observed_share * case.national_total_m3 - row.upper_bound_m3)
        for row in case.rows
        if row.observed_share * case.national_total_m3 > row.upper_bound_m3 + CAP_TOLERANCE_M3
    }


def _reallocation_lines(case: CaseResult) -> list[str]:
    def delta(row: AllocationRow) -> float:
        target = row.observed_share * case.national_total_m3
        return row.harvest_m3 - target

    lines: list[str] = []
    for row in sorted(case.rows, key=delta):
        change = delta(row)
        if abs(change) < 1.0:
            continue
        direction = "gains" if change > 0 else "loses"
        lines.append(
            f"| {row.province} | {direction} | {change / 1e6:+.3f} | "
            f"{row.harvest_m3 / 1e6:.3f} | {row.upper_bound_m3 / 1e6:.3f} |"
        )
    return lines


def write_report(
    path: Path,
    basis: tuple[ProvinceBasis, ...],
    summary: dict[str, object],
    cases: dict[str, CaseResult],
    intensity_results: dict[str, dict[str, CaseResult]],
    bc_sensitivity: CaseResult,
) -> None:
    upper_sum = float(summary["upper_bound_sum_m3"])
    generous_sum = float(summary["most_generous_documented_ceiling_m3"])
    operable_area = sum(
        row.operable_proxy_ha for row in basis if row.operable_proxy_ha is not None
    )
    managed_area = sum(row.managed_proxy_ha for row in basis if row.managed_proxy_ha is not None)
    operable_share = operable_area / managed_area if managed_area > 0 else 0.0
    diag = cases["200m"]
    generous_deficit = diag.national_total_m3 - generous_sum

    lines = [
        "# Provincial Harvest-Allocation LP",
        "",
        "**Status: screening allocation under documented ceilings. This is not a legal national AAC, a sustainable-supply estimate, or a forecast.**",
        "",
        "Generated by `workflows/national/harvest/provincial_allocation.py` (PuLP/CBC).",
        "",
        "## Method",
        "",
        "The national harvest ladder is exogenous. For each national total `T`, the LP chooses provincial harvest `h_p` (m3/yr) subject to:",
        "",
        "```text",
        "sum_p h_p = T",
        "0 <= h_p <= upper_bound_p",
        "```",
        "",
        "Upper bounds use the documented AAC where one exists, otherwise the documented planning volume or a flagged proxy. No legal AAC is fabricated. NU has no FSD harvest, AAC, or managed-landbase row and is held at zero.",
        "",
        "## Objective",
        "",
        "Targets are the observed 2024 provincial shares scaled to the national total:",
        "",
        "```text",
        "target_p = observed_2024_p * T / 118,977,339",
        "primary:   minimize sum_p |h_p - target_p|",
        "secondary: minimize max_p (h_p - target_p)+ / observed_2024_p after fixing the primary optimum within 1 m3",
        "           (cap-forced decreases are fixed by the ceilings and excluded from that maximum)",
        "```",
        "",
        "The secondary stage breaks the L1 tie so the shortfall is distributed in proportion to each province's observed harvest, rather than concentrated in a few jurisdictions. It does not change the primary objective. Cap-forced decreases are not part of the minimax term.",
        "",
        "## Caps and basis",
        "",
        "| Province | 2024 observed (Mm3) | AAC (Mm3) | NFD (Mm3) | Managed proxy (Mha) | Operable proxy (Mha) | Upper bound (Mm3) | Basis |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in basis:
        lines.append(
            "| {p} | {o:.3f} | {aac} | {nfd} | {proxy} | {op} | {ub:.3f} | `{basis}` |".format(
                p=row.province,
                o=row.observed_2024_m3 / 1e6,
                aac="—" if row.aac_m3 is None else f"{row.aac_m3 / 1e6:.3f}",
                nfd="—" if row.nfd_m3 is None else f"{row.nfd_m3 / 1e6:.3f}",
                proxy="—" if row.managed_proxy_ha is None else f"{row.managed_proxy_ha / 1e6:.2f}",
                op="missing" if row.operable_missing else f"{row.operable_proxy_ha / 1e6:.2f}",
                ub=row.upper_bound_m3 / 1e6,
                basis=row.basis,
            )
        )
    lines.extend(
        [
            "",
            f"The documented AAC partial sum is {DOCUMENTED_AAC_PARTIAL_SUM_M3 / 1e6:.3f} Mm3/yr (mixed basis; excludes MB, PE, NU). Adding the MB range midpoint ({MB_AAC_MIDPOINT_M3 / 1e6:.3f}), the PE planning volume ({PE_PLANNING_VOLUME_M3 / 1e6:.3f}), and documented NS private AAC ({NS_PRIVATE_AAC_M3 / 1e6:.3f}) gives the model ceiling of **{upper_sum / 1e6:.3f} Mm3/yr**. The most generous documented set (BC 60M, MB 7.99M, NS 5.75M, PE 0.46M) gives **{generous_sum / 1e6:.3f} Mm3/yr**.",
            "",
            "## Results",
            "",
            "| Case | Status | Harvest (Mm3) | Optimal L1 (Mm3) | Max relative increase (%) | Status-quo exceedance (Mm3) | Binding provinces |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for label in ("100m", "119m", "150m", "200m"):
        case = cases[label]
        if case.is_optimal:
            lines.append(
                f"| {label.upper()} | optimal | {case.harvest_sum_m3 / 1e6:.3f} | {case.optimal_l1_deviation_m3 / 1e6:.3f} | {case.max_relative_increase:.3%} | {case.status_quo_exceedance_m3 / 1e6:.3f} | {', '.join(case.binding_provinces) or '—'} |"
            )
        else:
            lines.append(
                f"| {label.upper()} | infeasible | — | — | — | {case.status_quo_exceedance_m3 / 1e6:.3f} | deficit {case.deficit_m3 / 1e6:.3f} Mm3 |"
            )

    lines.extend(
        [
            "",
            "`Status-quo exceedance` is the volume by which the 2024-share mix would exceed caps before reallocation. It is the source of the L1 distance: the optimal L1 is twice the exceedance when aggregate headroom is sufficient.",
            "",
        ]
    )

    for label in ("100m", "119m"):
        case = cases[label]
        lines.extend(
            [
                f"### {label.upper()} allocation",
                "",
                _markdown_table(case),
                "",
                f"Binding provinces: **{', '.join(case.binding_provinces) or 'none'}**. Achieved L1 = {case.achieved_l1_deviation_m3 / 1e6:.3f} Mm3; maximum proportional increase = {case.max_relative_increase:.3%}; maximum absolute deviation = {case.max_deviation_m3 / 1e6:.3f} Mm3.",
                "",
            ]
        )

    case_150 = cases["150m"]
    shortfalls = _status_quo_shortfalls(case_150)
    shortfall_text = ", ".join(
        f"{province} {volume / 1e6:.3f}" for province, volume in shortfalls.items()
    )
    lines.extend(
        [
            "### 150M allocation and the BC/NB reallocation",
            "",
            _markdown_table(case_150),
            "",
            f"At 150M the 2024-share mix exceeds ceilings by **{case_150.status_quo_exceedance_m3 / 1e6:.3f} Mm3** ({shortfall_text} Mm3). The LP removes those volumes and redistributes them to provinces with headroom:",
            "",
            "| Province | Direction | Delta vs 2024 share (Mm3) | Allocation (Mm3) | Cap (Mm3) |",
            "|---|---|---:|---:|---:|",
            *_reallocation_lines(case_150),
            "",
            f"Binding provinces at 150M: **{', '.join(case_150.binding_provinces)}**. AB, QC, and NL are at their documented ceilings; the remaining gain is distributed across SK, MB, ON, NS, PE, and YT at a common 26.8% increase over their observed 2024 harvest. Achieved L1 = {case_150.achieved_l1_deviation_m3 / 1e6:.3f} Mm3; maximum proportional increase = {case_150.max_relative_increase:.3%}; maximum absolute deviation = {case_150.max_deviation_m3 / 1e6:.3f} Mm3.",
            "",
            f"Sensitivity with the separately reported BC current Crown AAC of 60M: BC no longer binds, the optimal L1 falls to {bc_sensitivity.optimal_l1_deviation_m3 / 1e6:.3f} Mm3, and the binding provinces are {', '.join(bc_sensitivity.binding_provinces)}. This does not remove the NB ceiling conflict.",
            "",
            "## 200M infeasibility",
            "",
            f"The 200M diagnostic is infeasible by construction: the model ceiling is {upper_sum / 1e6:.3f} Mm3/yr, so the exact deficit is **{diag.deficit_m3 / 1e6:.3f} Mm3**. Even the most generous documented ceiling ({generous_sum / 1e6:.3f} Mm3/yr: BC 60M, MB 7.99M, NS 5.75M, PE 0.46M) leaves a deficit of **{generous_deficit / 1e6:.3f} Mm3**. No allocation is forced; the case is reported as infeasible.",
            "",
            "## Operable-proxy intensity sensitivity",
            "",
            "The screening landbase is the national **operable merchantable-forest proxy** (SCANFI height >= 5 m, biomass >= 20 Mg/ha, closure >= 10%, intersected with a share-based 2016 tenure proxy). It is **not a legal THLB**. The intensity cap is applied as `upper_bound_p = min(documented AAC/NFD/planning proxy, I * operable_proxy_ha)`. PE and NU have no operable proxy: their documented caps are retained and flagged `operable_missing`; no area is substituted with zero. The 166.28 Mha managed proxy is retained only as a comparison basis.",
            "",
            f"National operable proxy: **{operable_area / 1e6:.3f} Mha** ({operable_share:.1%} of the 166.28 Mha managed proxy). The national envelope `I * national operable area` is {1.0 * operable_area / 1e6:.3f} Mm3/yr at I=1.0 and {1.2 * operable_area / 1e6:.3f} Mm3/yr at I=1.2. That envelope is a diagnostic only; the model of record applies the cap per province, so a documented cap below `I * operable_proxy_ha` still binds.",
            "",
            "| Case | Intensity (m3/ha/yr) | Status | Provincial capacity (Mm3) | National envelope (Mm3) | Binding provinces | L1 (Mm3) | Provincial deficit (Mm3) | Envelope deficit (Mm3) |",
            "|---|---:|---|---:|---:|---|---:|---:|---:|",
        ]
    )
    for label in ("100m", "119m", "150m"):
        for intensity, result in intensity_results[label].items():
            binding = ", ".join(result.binding_provinces) or "—"
            l1 = "—" if result.optimal_l1_deviation_m3 is None else f"{result.optimal_l1_deviation_m3 / 1e6:.3f}"
            deficit = "—" if result.deficit_m3 == 0.0 else f"{result.deficit_m3 / 1e6:.3f}"
            envelope_capacity = float(intensity) * operable_area
            envelope_deficit = max(0.0, result.national_total_m3 - envelope_capacity)
            envelope_deficit_text = "—" if envelope_deficit == 0.0 else f"{envelope_deficit / 1e6:.3f}"
            lines.append(
                f"| {label.upper()} | {intensity} | {result.status} | {result.capacity_m3 / 1e6:.3f} | {envelope_capacity / 1e6:.3f} | {binding} | {l1} | {deficit} | {envelope_deficit_text} |"
            )

    intensity_150_1_0 = intensity_results["150m"]["1.0"]
    intensity_150_1_2 = intensity_results["150m"]["1.2"]
    lines.extend(
        [
            "",
            f"At 150M the per-province min caps under a 1.0 m3/ha/yr screen sum to {intensity_150_1_0.capacity_m3 / 1e6:.3f} Mm3/yr, so the LP is infeasible by **{intensity_150_1_0.deficit_m3 / 1e6:.3f} Mm3**. The national operable envelope alone would be short by {max(0.0, 150_000_000.0 - operable_area) / 1e6:.3f} Mm3; the larger provincial deficit arises because BC, ON, YT, and NT documented caps bind below `1.0 * operable_proxy_ha`.",
            "",
            f"At 1.2 m3/ha/yr the per-province min caps sum to {intensity_150_1_2.capacity_m3 / 1e6:.3f} Mm3/yr, so the LP is **still infeasible by {intensity_150_1_2.deficit_m3 / 1e6:.3f} Mm3** under the per-province formula. The national envelope is {1.2 * operable_area / 1e6:.3f} Mm3/yr and would clear 150M by {max(0.0, 1.2 * operable_area - 150_000_000.0) / 1e6:.3f} Mm3, but it cannot be allocated because BC (45.224), ON (28.700), YT (0.261), and NT (0.036 Mm3/yr) documented caps bind first. Reporting 1.2 as feasible would require dropping either the documented-cap constraint or the per-province operable constraint; neither is done here.",
            "",
            "The 100M and 119M cases are feasible at both intensities. At 1.0 the binding sets are 100M `AB, NB, NS, NL` and 119M `BC, AB, SK, QC, NB, NS, NL, NT`; at 1.2 they are 100M `NB, NS, NL` and 119M `AB, NB, NS, NL, NT`. PE and NU retain their documented caps and are flagged `operable_missing`.",
            "",
            "For comparison, the superseded managed-proxy screen at 1.0 has a 150M deficit of 6.113 Mm3 and is feasible at 1.2. The tighter operable proxy therefore moves the 150M screen from '1.2 feasible' to '1.2 still short by 4.492 Mm3'. Per-intensity outputs are under `intensity_1.0/` and `intensity_1.2/` (CSVs for feasible cases plus `provincial_allocation_summary.json`). The 150M cases are infeasible at both intensities and have no CSV.",
            "",
            "## Caveats",
            "",
            "- The upper-bound sum is **not** a national AAC. It mixes statutory AAC, approximate AAC, a repo-parsed TSA path, a documented range midpoint, planning volumes, and an actual-mean proxy.",
            "- NB and QC report Crown AAC while FSD harvest is total provincial harvest. NB private supply is undocumented, so NB's cap is conservative; the 119M observed mix is already 3.11 Mm3 above it.",
            "- NS uses Crown 1.72M plus documented private 4.03M. The Crown-only comparison is retained in the feasibility note.",
            "- BC uses the repo-parsed 2024 TSA AAC (45.22M). The current reported Crown AAC (~58-60M) and the 2028+ path (43.37M) bracket this value; the 60M sensitivity is reported above.",
            "- MB uses the midpoint of the documented 7.39-7.99M range; it is non-binding at every ladder level, so the choice does not affect the allocations.",
            "- PE has no statutory AAC; 460,000 m3/yr is a planning volume. NT uses the 2020-2024 actual-harvest mean, not a legal AAC. NU is not assessed. PE and NU have no operable proxy and keep their documented caps with `operable_missing`.",
            "- The operable proxy has no slope, road access, age/disturbance, growth, yield, silvicultural, or economic content. Intensity caps are sensitivity screens, not constraints.",
            "- The objective is a share-deviation rule. It has no harvest cost, log value, stumpage, silviculture, transport, or forest-sector employment content.",
            "",
            "## Next steps",
            "",
            "1. Replace the share-based operable proxy with a legal THLB that removes slope, road-access, age/disturbance, and non-commercial stands before interpreting intensity.",
            "2. Build a harvest/residue cost curve (stumpage, harvest, road, haul, silviculture) and integrate it with the forest-sector LP.",
            "3. Carry the 100/119/150 provincial allocations into runner metadata as `screening_only`, not as a legal allocation.",
            "4. Revisit the NB private-supply gap and the BC current-Crown-AAC discrepancy with provincial sources.",
            "",
            "## Outputs",
            "",
            "- `100m.csv`, `119m.csv`, `150m.csv`: provincial allocations.",
            "- `provincial_allocation_summary.json`: totals, objectives, binding provinces, sensitivities, 200M deficit, THLB intensity deficits.",
            "- `manifest.json`: input/output hashes, solver, statuses, caveats.",
            "- `intensity_1.0/`, `intensity_1.2/`: feasible-case CSVs and per-intensity summary JSON for the operable-proxy screen.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_manifest(
    path: Path,
    summary: dict[str, object],
    input_paths: tuple[Path, ...],
    output_paths: tuple[Path, ...],
) -> None:
    manifest = {
        "product": "provincial harvest-allocation LP",
        "status": "screening allocation; not a legal AAC and not a forecast",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "solver": {
            "name": "PuLP",
            "version": pulp.__version__,
            "backend": "CBC (bundled)",
            "threads": 1,
        },
        "objective": summary["objective"],
        "national_observed_2024_m3": NATIONAL_OBSERVED_2024_M3,
        "documented_aac_partial_sum_m3": DOCUMENTED_AAC_PARTIAL_SUM_M3,
        "nfd_wood_supply_sum_m3": NFD_WOOD_SUPPLY_SUM_M3,
        "upper_bound_sum_m3": summary["upper_bound_sum_m3"],
        "case_statuses": {
            label: payload["status"] for label, payload in summary["cases"].items()
        },
        "inputs": {
            str(path.relative_to(REPO_ROOT)): {
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in input_paths
        },
        "outputs": {
            str(path.relative_to(REPO_ROOT)): {
                "sha256": _sha256(path),
                "bytes": path.stat().st_size,
            }
            for path in output_paths
        },
        "caveats": summary["caveats"],
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def run() -> dict[str, object]:
    """Build the basis, solve all cases and sensitivities, and write artifacts."""
    basis = load_province_basis()
    cases: dict[str, CaseResult] = {
        label: solve_allocation(basis, total, label=label) for label, total in LADDER_TOTALS
    }
    cases["200m"] = solve_allocation(basis, DIAGNOSTIC_TOTAL_200M, label="200m")

    intensity_results: dict[str, dict[str, CaseResult]] = {}
    for label, total in LADDER_TOTALS:
        intensity_results[label] = {
            intensity: solve_allocation(
                with_operable_intensity_cap(basis, float(intensity)),
                total,
                label=f"{label}_intensity_{intensity}",
            )
            for intensity in INTENSITY_CAP_LEVELS
        }

    bc_sensitivity = solve_allocation(
        with_bc_cap(basis, BC_REPORTED_CROWN_AAC_M3),
        150_000_000.0,
        label="150m_bc_crown_60m",
    )
    summary = build_summary(basis, cases, intensity_results, bc_sensitivity)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_paths: list[Path] = []
    for label in ("100m", "119m", "150m"):
        path = OUTPUT_DIR / f"{label}.csv"
        write_case_csv(path, cases[label])
        output_paths.append(path)

    summary_path = OUTPUT_DIR / "provincial_allocation_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    output_paths.append(summary_path)

    report_path = OUTPUT_DIR / "PROVINCIAL_ALLOCATION_REPORT.md"
    write_report(report_path, basis, summary, cases, intensity_results, bc_sensitivity)
    output_paths.append(report_path)

    output_paths.extend(write_intensity_outputs(intensity_results))

    manifest_path = OUTPUT_DIR / "manifest.json"
    write_manifest(
        manifest_path,
        summary,
        (
            TEMPLATE_PATH,
            PROVINCES_PATH,
            MANAGED_LANDBASE_PATH,
            OPERABLE_PROXY_PATH,
            OPERABLE_PROXY_SUMMARY_PATH,
            FEASIBILITY_DOC_PATH,
        ),
        tuple(output_paths),
    )
    return summary


if __name__ == "__main__":
    result = run()
    for case_label, case_payload in result["cases"].items():
        print(
            f"{case_label}: {case_payload['status']} "
            f"binding={','.join(case_payload['binding_provinces']) or '-'}"
        )
