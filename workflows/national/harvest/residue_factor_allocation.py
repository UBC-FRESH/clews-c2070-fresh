"""Harvest-weighted national residue factors from provincial allocations.

The RSYC national residue table is a species-resolved SCANFI area-share
apportionment of standing inventory.  It is not a harvest allocation.  When a
provincial harvest allocation is supplied, the national residue factor must be
weighted by harvested log volume instead::

    f_national = sum_p h_p * f_p / sum_p h_p

This module loads the provincial ``rsyc_backbone`` factors and the harvest
allocation LP artifacts, applies that weighting for the low/base/high recovery
cases, and writes a compact comparison artifact.  Missing province factors,
non-positive totals, and allocations that do not conserve their documented
national total are errors, never silent defaults.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
RESIDUE_FACTOR_PATH = (
    REPO_ROOT
    / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
)
ALLOCATION_DIR = REPO_ROOT / "data/derived/national/harvest_basis/provincial_allocation"
OUTPUT_DIR = REPO_ROOT / "data/derived/national/harvest_basis/residue_factor_by_allocation"

CASES = ("observed", "100m", "119m", "150m")
CASE_BY_SCENARIO_VALUE = {
    "observed": "observed",
    "lp_100m": "100m",
    "lp_119m": "119m",
    "lp_150m": "150m",
}
ALLOCATION_TOTALS_M3 = {
    "100m": 100_000_000.0,
    "119m": 118_977_339.0,
    "150m": 150_000_000.0,
    "observed": 118_977_339.0,
}
RECOVERY = {"low": 0.35, "base": 0.60, "high": 0.75}
LHV_GJ_PER_ODT = 18.5
ALLOCATION_TOLERANCE_M3 = 2.0
FACTOR_TOLERANCE = 1e-6

_REQUIRED_FACTOR_FIELDS = (
    "province",
    "odt_per_m3_log",
    "recoverable_odt_per_m3_low",
    "recoverable_odt_per_m3_base",
    "recoverable_odt_per_m3_high",
)


class ResidueFactorError(RuntimeError):
    """Raised when the residue-factor weighting cannot be formed safely."""


@dataclass(frozen=True)
class ProvinceResidueFactor:
    """One province's gross and recoverable residue factors."""

    province: str
    gross_odt_per_m3_log: float
    recoverable_odt_per_m3_low: float
    recoverable_odt_per_m3_base: float
    recoverable_odt_per_m3_high: float

    def recoverable(self, case: str) -> float:
        if case == "low":
            return self.recoverable_odt_per_m3_low
        if case == "base":
            return self.recoverable_odt_per_m3_base
        if case == "high":
            return self.recoverable_odt_per_m3_high
        raise ResidueFactorError(f"unknown recovery case {case!r}")


@dataclass(frozen=True)
class AllocationResidueFactor:
    """Harvest-weighted residue factor for one allocation case."""

    case: str
    total_harvest_m3: float
    gross_odt_per_m3_log: float
    odt_per_m3_log_low: float
    odt_per_m3_log_base: float
    odt_per_m3_log_high: float
    pj_per_100m_log_low: float
    pj_per_100m_log_base: float
    pj_per_100m_log_high: float
    national_total_pj_low: float
    national_total_pj_base: float
    national_total_pj_high: float

    def recoverable(self, case: str) -> float:
        if case == "low":
            return self.odt_per_m3_log_low
        if case == "base":
            return self.odt_per_m3_log_base
        if case == "high":
            return self.odt_per_m3_log_high
        raise ResidueFactorError(f"unknown recovery case {case!r}")

    def national_total_pj(self, case: str) -> float:
        if case == "low":
            return self.national_total_pj_low
        if case == "base":
            return self.national_total_pj_base
        if case == "high":
            return self.national_total_pj_high
        raise ResidueFactorError(f"unknown recovery case {case!r}")


def _finite_float(value: str | None, *, field: str, context: str) -> float:
    if value is None or not value.strip():
        raise ResidueFactorError(f"{context}: missing {field}")
    try:
        number = float(value)
    except ValueError as exc:
        raise ResidueFactorError(f"{context}: {field} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise ResidueFactorError(f"{context}: {field} is not finite: {value!r}")
    return number


def load_province_factors(
    path: Path = RESIDUE_FACTOR_PATH,
) -> dict[str, ProvinceResidueFactor]:
    """Load province residue factors, rejecting missing or inconsistent rows."""

    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle) if row.get("level") == "province"]
    except FileNotFoundError as exc:
        raise ResidueFactorError(f"province residue factor file is missing: {path}") from exc
    except OSError as exc:
        raise ResidueFactorError(f"cannot read province residue factors {path}: {exc}") from exc
    if not rows:
        raise ResidueFactorError(f"{path}: no province-level residue factor rows")

    factors: dict[str, ProvinceResidueFactor] = {}
    for row in rows:
        missing_fields = [field for field in _REQUIRED_FACTOR_FIELDS if field not in row]
        if missing_fields:
            raise ResidueFactorError(f"{path}: missing columns {missing_fields}")
        province = (row["province"] or "").strip().upper()
        if not province:
            raise ResidueFactorError(f"{path}: blank province code")
        if province in factors:
            raise ResidueFactorError(f"{path}: duplicate province factor for {province}")
        gross = _finite_float(
            row["odt_per_m3_log"], field="odt_per_m3_log", context=f"{path}:{province}"
        )
        if gross <= 0:
            raise ResidueFactorError(f"{path}:{province}: gross residue factor must be positive")
        low = _finite_float(
            row["recoverable_odt_per_m3_low"],
            field="recoverable_odt_per_m3_low",
            context=f"{path}:{province}",
        )
        base = _finite_float(
            row["recoverable_odt_per_m3_base"],
            field="recoverable_odt_per_m3_base",
            context=f"{path}:{province}",
        )
        high = _finite_float(
            row["recoverable_odt_per_m3_high"],
            field="recoverable_odt_per_m3_high",
            context=f"{path}:{province}",
        )
        for case, value in (("low", low), ("base", base), ("high", high)):
            if value < 0:
                raise ResidueFactorError(
                    f"{path}:{province}: recoverable {case} factor must be non-negative"
                )
            expected = gross * RECOVERY[case]
            if abs(value - expected) > FACTOR_TOLERANCE:
                raise ResidueFactorError(
                    f"{path}:{province}: recoverable {case} factor {value} does not match "
                    f"gross * {RECOVERY[case]} = {expected}"
                )
        factors[province] = ProvinceResidueFactor(
            province=province,
            gross_odt_per_m3_log=gross,
            recoverable_odt_per_m3_low=low,
            recoverable_odt_per_m3_base=base,
            recoverable_odt_per_m3_high=high,
        )
    return factors


def load_allocation_harvest(
    case: str,
    *,
    allocation_dir: Path = ALLOCATION_DIR,
) -> dict[str, float]:
    """Load one allocation case and verify its documented national total."""

    if case not in ALLOCATION_TOTALS_M3:
        raise ResidueFactorError(f"unknown allocation case {case!r}")
    filename = "119m.csv" if case == "observed" else f"{case}.csv"
    column = "observed_2024_m3" if case == "observed" else "harvest_m3"
    path = allocation_dir / filename
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
    except FileNotFoundError as exc:
        raise ResidueFactorError(f"allocation file is missing: {path}") from exc
    except OSError as exc:
        raise ResidueFactorError(f"cannot read allocation file {path}: {exc}") from exc
    if not rows:
        raise ResidueFactorError(f"{path}: no allocation rows")

    harvest: dict[str, float] = {}
    for row in rows:
        province = (row.get("province") or "").strip().upper()
        if not province:
            raise ResidueFactorError(f"{path}: blank province code")
        if province in harvest:
            raise ResidueFactorError(f"{path}: duplicate allocation for {province}")
        value = _finite_float(row.get(column), field=column, context=f"{path}:{province}")
        if value < 0:
            raise ResidueFactorError(f"{path}:{province}: harvest must be non-negative")
        harvest[province] = value

    total = math.fsum(harvest.values())
    expected = ALLOCATION_TOTALS_M3[case]
    if abs(total - expected) > ALLOCATION_TOLERANCE_M3:
        raise ResidueFactorError(
            f"{path}: allocation totals {total:,.3f} m3, expected {expected:,.3f} m3"
        )
    return harvest


def weighted_residue_factor(
    harvest: Mapping[str, float],
    factors: Mapping[str, ProvinceResidueFactor],
    *,
    case: str,
) -> AllocationResidueFactor:
    """Form the harvest-weighted national factor for one allocation."""

    if not harvest:
        raise ResidueFactorError("allocation is empty")
    missing = sorted(province for province in harvest if province not in factors)
    if missing:
        raise ResidueFactorError(f"missing province residue factors for {missing}")
    total = math.fsum(harvest.values())
    if total <= 0:
        raise ResidueFactorError("allocation total must be positive")

    gross = math.fsum(h * factors[p].gross_odt_per_m3_log for p, h in harvest.items()) / total
    recoverable = {
        recovery: math.fsum(h * factors[p].recoverable(recovery) for p, h in harvest.items())
        / total
        for recovery in RECOVERY
    }
    national_pj = {
        recovery: recoverable[recovery] * total * LHV_GJ_PER_ODT / 1_000_000
        for recovery in RECOVERY
    }
    return AllocationResidueFactor(
        case=case,
        total_harvest_m3=total,
        gross_odt_per_m3_log=gross,
        odt_per_m3_log_low=recoverable["low"],
        odt_per_m3_log_base=recoverable["base"],
        odt_per_m3_log_high=recoverable["high"],
        pj_per_100m_log_low=recoverable["low"] * 1e8 * LHV_GJ_PER_ODT / 1_000_000,
        pj_per_100m_log_base=recoverable["base"] * 1e8 * LHV_GJ_PER_ODT / 1_000_000,
        pj_per_100m_log_high=recoverable["high"] * 1e8 * LHV_GJ_PER_ODT / 1_000_000,
        national_total_pj_low=national_pj["low"],
        national_total_pj_base=national_pj["base"],
        national_total_pj_high=national_pj["high"],
    )


def allocation_residue_factor(
    case: str,
    *,
    repo_root: Path = REPO_ROOT,
) -> AllocationResidueFactor:
    """Load and weight one allocation under a repository root."""

    factors = load_province_factors(
        repo_root
        / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
    )
    harvest = load_allocation_harvest(
        case,
        allocation_dir=repo_root / "data/derived/national/harvest_basis/provincial_allocation",
    )
    return weighted_residue_factor(harvest, factors, case=case)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format(value: float) -> str:
    return f"{value:.12f}"


def build_artifact(repo_root: Path = REPO_ROOT) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Compute all allocation cases and return CSV rows plus a JSON payload."""

    factors = load_province_factors(
        repo_root
        / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
    )
    rows: list[dict[str, object]] = []
    cases: dict[str, object] = {}
    for case in CASES:
        harvest = load_allocation_harvest(
            case,
            allocation_dir=repo_root / "data/derived/national/harvest_basis/provincial_allocation",
        )
        weighted = weighted_residue_factor(harvest, factors, case=case)
        rows.append(
            {
                "allocation": case,
                "total_harvest_m3": _format(weighted.total_harvest_m3),
                "gross_odt_per_m3_log": _format(weighted.gross_odt_per_m3_log),
                "odt_per_m3_log_low": _format(weighted.odt_per_m3_log_low),
                "odt_per_m3_log_base": _format(weighted.odt_per_m3_log_base),
                "odt_per_m3_log_high": _format(weighted.odt_per_m3_log_high),
                "pj_per_100m_log_low": _format(weighted.pj_per_100m_log_low),
                "pj_per_100m_log_base": _format(weighted.pj_per_100m_log_base),
                "pj_per_100m_log_high": _format(weighted.pj_per_100m_log_high),
                "national_total_pj_low": _format(weighted.national_total_pj_low),
                "national_total_pj_base": _format(weighted.national_total_pj_base),
                "national_total_pj_high": _format(weighted.national_total_pj_high),
            }
        )
        cases[case] = {
            "total_harvest_m3": weighted.total_harvest_m3,
            "gross_odt_per_m3_log": weighted.gross_odt_per_m3_log,
            "recoverable_odt_per_m3_log": {
                "low": weighted.odt_per_m3_log_low,
                "base": weighted.odt_per_m3_log_base,
                "high": weighted.odt_per_m3_log_high,
            },
            "pj_per_100m_log": {
                "low": weighted.pj_per_100m_log_low,
                "base": weighted.pj_per_100m_log_base,
                "high": weighted.pj_per_100m_log_high,
            },
            "national_total_pj": {
                "low": weighted.national_total_pj_low,
                "base": weighted.national_total_pj_base,
                "high": weighted.national_total_pj_high,
            },
        }

    payload: dict[str, object] = {
        "product": "harvest-weighted national residue factors",
        "status": "screening comparison; provincial allocation is not a forecast",
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "basis": "sum_p(h_p * factor_p) / sum_p(h_p)",
        "constants": {
            "lhv_gj_per_odt": LHV_GJ_PER_ODT,
            "recovery_fractions": RECOVERY,
            "allocation_totals_m3": ALLOCATION_TOTALS_M3,
        },
        "area_share_reference": {
            "gross_odt_per_m3_log": 0.116820637,
            "recoverable_odt_per_m3_log_base": 0.070092382,
            "pj_per_100m_log_base": 129.670907,
            "note": (
                "Species-resolved SCANFI area-share apportionment of standing inventory; "
                "the province weights are not a harvest allocation."
            ),
        },
        "cases": cases,
        "province_factor_count": len(factors),
        "inputs": {
            "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv": {
                "sha256": _sha256(
                    repo_root
                    / "data/derived/national/clews_supply/rsyc_backbone/residue_factor_by_province.csv"
                )
            },
            "data/derived/national/harvest_basis/provincial_allocation/100m.csv": {
                "sha256": _sha256(
                    repo_root / "data/derived/national/harvest_basis/provincial_allocation/100m.csv"
                )
            },
            "data/derived/national/harvest_basis/provincial_allocation/119m.csv": {
                "sha256": _sha256(
                    repo_root / "data/derived/national/harvest_basis/provincial_allocation/119m.csv"
                )
            },
            "data/derived/national/harvest_basis/provincial_allocation/150m.csv": {
                "sha256": _sha256(
                    repo_root / "data/derived/national/harvest_basis/provincial_allocation/150m.csv"
                )
            },
        },
    }
    return rows, payload


def write_artifact(repo_root: Path = REPO_ROOT) -> Path:
    """Write the allocation-weighted factor CSV and JSON under ``data``."""

    rows, payload = build_artifact(repo_root)
    output_dir = repo_root / "data/derived/national/harvest_basis/residue_factor_by_allocation"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "residue_factor_by_allocation.csv"
    fields = tuple(rows[0])
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "residue_factor_by_allocation.json").write_text(
        json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return csv_path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: regenerate the allocation-weighted factor artifact."""

    del argv
    path = write_artifact()
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
