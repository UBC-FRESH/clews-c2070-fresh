"""Build a national residue supply-curve sensitivity for forestry screening.

The builder consumes ``facility_residue_supply_v1`` and produces a long-form
supply curve with two deliberately separate streams:

* **process residue** at facilities, aggregated by facility province and
  facility class, with 0%, 25%, 50%, and 75% availability scenarios applied to
  each class and to the class total;
* **harvest residue** by origin province, with 0.35, 0.60, and 0.75 recovery
  scenarios applied to gross harvest residue.

Every row is converted to PJ at the documented RSYC dry LHV of 18.5 GJ/odt.
Process and harvest residue are never combined into a single curve row.  The
RSYC 119 Mm3 comparison applies only to the harvest recoverable stream.

The OSeCAN national-base-119m ``dispatch_by_zone.csv`` is read for context
only.  The informative ``stress_119m`` run is used because the manifest records
the baseline as a controlled zero-biomass reference.  No OSeCAN file is
written or modified.

Fail-closed rules
-----------------
* Any unexpected ``D`` in the parent or ``data`` repository status halts the
  builder before it writes output.
* Every numeric input is parsed once, required finite and non-negative, and
  then used internally as a trusted value.
* Scenario arithmetic, class/province/national conservation, PJ conversion,
  the RSYC base, row uniqueness, and labels are validated before returning.
* The OSeCAN input hash is checked before and after the build.

Output labels
-------------
Every curve row is ``model_estimate=true``, ``observed=false``,
``policy_grade=false``, and ``not_policy=true``.  ``rsyc_comparable=true``
appears only on harvest recoverable rows.  This is a forestry-side screening
handoff, not an operating, policy, AAC, tenure, or investment claim.  No UI or
public OSeCAN surface is changed.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
OUTPUT_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v1"
FACILITY_DIR = (
    DATA_ROOT / "derived/national/facilities/facility_residue_supply_v1"
)
PRODUCT_CHAIN_DIR = (
    DATA_ROOT / "derived/national/facilities/national_product_chain_v1"
)
RSYC_SUMMARY = (
    DATA_ROOT
    / "derived/national/clews_supply/rsyc_backbone/"
    "national_residue_factor_summary.json"
)
OSECAN_DIR = (
    DATA_ROOT / "derived/national/clews/osecan/national_base_119m"
)
OSECAN_DISPATCH = OSECAN_DIR / "dispatch_by_zone.csv"
OSECAN_MANIFEST = OSECAN_DIR / "manifest.json"

ARTIFACT = "national_residue_curve_v1"
ARTIFACT_VERSION = "1.0.0"
PJ_GJ = 1_000_000.0
EXPECTED_LHV_GJ_PER_ODT = 18.5
EXPECTED_RSYC_119M_ODT = 7_080_062.0860263305
EXPECTED_RSYC_119M_PJ = 130.9811485914871
EXPECTED_RSYC_100M_ODT = 7_009_238.195978076
EXPECTED_RSYC_100M_PJ = 129.6709066255944
EXPECTED_HARVEST_BASIS_M3 = 118_977_339.0
RSYC_BASE_RECOVERY = 0.60
REL_TOL = 1e-8
ABS_TOL = 1e-6
LABEL_TRUE = "true"
LABEL_FALSE = "false"

PROVINCES: dict[str, str] = {
    "BC": "British Columbia",
    "AB": "Alberta",
    "SK": "Saskatchewan",
    "MB": "Manitoba",
    "ON": "Ontario",
    "QC": "Quebec",
    "NB": "New Brunswick",
    "NS": "Nova Scotia",
    "PE": "Prince Edward Island",
    "NL": "Newfoundland and Labrador",
    "YT": "Yukon",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
}
CLASS_ORDER: tuple[str, ...] = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
CLASS_RESIDUE_COLUMN = {
    "SAW": "sawlog_residue_odt",
    "PULP": "pulp_residue_odt",
    "PANEL": "panel_residue_odt",
    "PELLET": "pellet_residue_odt",
    "BIO": "bio_residue_odt",
}

PROCESS_AVAILABILITY_CASES: tuple[tuple[float, str], ...] = (
    (0.00, "process_availability_000"),
    (0.25, "process_availability_025"),
    (0.50, "process_availability_050"),
    (0.75, "process_availability_075"),
)
HARVEST_RECOVERY_CASES: tuple[tuple[float, str], ...] = (
    (0.35, "harvest_recovery_035"),
    (0.60, "harvest_recovery_060"),
    (0.75, "harvest_recovery_075"),
)

PROCESS_TYPE = "process_residue"
HARVEST_TYPE = "harvest_residue_recoverable"
PROCESS_STREAM = "process"
HARVEST_STREAM = "harvest"
TOTAL_CLASS = "TOTAL"
ALL_CLASS = "ALL"
NATIONAL_PROVINCE = "CAN"
NATIONAL_PROVINCE_NAME = "Canada"

PROCESS_CAVEAT = (
    "Process-residue availability is a screening fraction of facility "
    "process residue; it is not observed availability, not RSYC-comparable, "
    "and not policy."
)
PROCESS_TOTAL_CAVEAT = (
    "Total across mutually exclusive classes at one availability fraction; "
    "process residue is a separate stream from harvest residue and is not "
    "RSYC-comparable."
)
HARVEST_CAVEAT = (
    "Recoverable harvest residue is gross provincial harvest residue times "
    "the recovery fraction, attributed to the origin province; it is "
    "RSYC-comparable, not facility-assigned, and not policy."
)
NATIONAL_CAVEAT = (
    "National aggregate of province rows. Process and harvest residue remain "
    "separate streams; no combined process-plus-harvest curve row is written."
)


class NationalResidueCurveError(RuntimeError):
    """Raised when the national residue curve fails a guard."""


@dataclass(frozen=True)
class RsvcBasis:
    """Parsed RSYC energy and reference values."""

    lhv_gj_per_odt: float
    lhv_source: str
    recovery_fractions: tuple[float, ...]
    base_119m_odt: float
    base_119m_pj: float
    base_100m_odt: float
    base_100m_pj: float
    province_119m_odt: Mapping[str, float]


@dataclass(frozen=True)
class ProductChainProvince:
    """Trusted product-chain province row used for cross-checks."""

    province: str
    harvest_m3: float
    class_residue_odt: Mapping[str, float]
    harvest_residue_gross_odt: float
    harvest_residue_recoverable_odt: float
    harvest_residue_unrecovered_odt: float
    model_recoverable_odt_base: float
    rsyc_base_recoverable_odt: float
    coefficient_tier: str


@dataclass(frozen=True)
class ProcessBase:
    """Process residue at facilities in one province and class."""

    province: str
    residue_class: str
    residue_odt: float
    facility_count: int
    coefficient_tiers: tuple[str, ...]
    capacity_evidence: tuple[str, ...]


@dataclass(frozen=True)
class HarvestBase:
    """Gross harvest residue and RSYC reference for one origin province."""

    province: str
    gross_odt: float
    gross_m3: float
    model_recoverable_odt_base: float
    rsyc_base_odt: float
    coefficient_tier: str
    rsyc_ratio: float
    rsyc_status: str


@dataclass(frozen=True)
class OsecanContext:
    """OSeCAN dispatch_by_zone context for the stress national-base run."""

    run: str
    source: str
    source_sha256: str
    manifest_status: str
    national_supply_pj: Mapping[int, float]
    bc_supply_pj: Mapping[int, float]
    national_dispatch_pj: Mapping[int, float]
    bc_dispatch_pj: Mapping[int, float]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    if not path.is_file():
        raise NationalResidueCurveError(f"{label} does not exist: {path}")
    frame = pd.read_csv(path, keep_default_na=False, dtype=str)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise NationalResidueCurveError(
            f"{label} is missing columns {missing}: {path}"
        )
    if frame.empty:
        raise NationalResidueCurveError(f"{label} is empty: {path}")
    return frame


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise NationalResidueCurveError(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NationalResidueCurveError(
            f"{label} is not valid JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise NationalResidueCurveError(f"{label} is not a JSON object: {path}")
    return payload


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise NationalResidueCurveError(
            f"{label} is not numeric: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise NationalResidueCurveError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise NationalResidueCurveError(f"{label} is negative: {parsed!r}")
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise NationalResidueCurveError(f"{label} is not a boolean: {value!r}")


def _require_close(
    left: float, right: float, label: str, *, abs_tol: float = ABS_TOL
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale + abs_tol:
        raise NationalResidueCurveError(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(absolute {abs(left - right):.6g})"
        )


def _git_deletions(repository: Path) -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise NationalResidueCurveError(
            f"cannot inspect git status in {repository}: {result.stderr.strip()}"
        )
    deletions: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 2:
            continue
        if line[0] == "D" or line[1] == "D":
            deletions.append(line)
    return deletions


def assert_no_unexpected_deletions() -> None:
    """Fail closed on any ``D`` in the parent or data repository status."""
    incidents: list[str] = []
    for repository in (REPO_ROOT, DATA_ROOT):
        for line in _git_deletions(repository):
            incidents.append(f"{repository.name or repository}: {line}")
    if incidents:
        raise NationalResidueCurveError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _parse_product_chain() -> dict[str, ProductChainProvince]:
    required = [
        "province",
        "harvest_m3",
        *CLASS_RESIDUE_COLUMN.values(),
        "harvest_residue_gross_odt",
        "harvest_residue_recoverable_odt",
        "harvest_residue_unrecovered_odt",
        "model_recoverable_odt_base",
        "rsyc_base_recoverable_odt",
        "coefficient_tier",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
    ]
    path = PRODUCT_CHAIN_DIR / "product_chain_by_province.csv"
    frame = _read_csv(path, required, "product-chain province table")
    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    if set(frame["province"]) != set(PROVINCES):
        raise NationalResidueCurveError(
            "product-chain province set does not match the 13 jurisdictions"
        )
    if frame["province"].duplicated().any():
        raise NationalResidueCurveError(
            "product-chain province table has duplicate provinces"
        )

    chains: dict[str, ProductChainProvince] = {}
    for _, record in frame.iterrows():
        province = str(record["province"])
        harvest_m3 = _finite_nonnegative(
            record["harvest_m3"], f"{province} harvest_m3"
        )
        class_residue = {
            facility_class: _finite_nonnegative(
                record[CLASS_RESIDUE_COLUMN[facility_class]],
                f"{province}/{facility_class} residue",
            )
            for facility_class in CLASS_ORDER
        }
        gross = _finite_nonnegative(
            record["harvest_residue_gross_odt"],
            f"{province} harvest_residue_gross_odt",
        )
        recoverable = _finite_nonnegative(
            record["harvest_residue_recoverable_odt"],
            f"{province} harvest_residue_recoverable_odt",
        )
        unrecovered = _finite_nonnegative(
            record["harvest_residue_unrecovered_odt"],
            f"{province} harvest_residue_unrecovered_odt",
        )
        _require_close(
            recoverable + unrecovered,
            gross,
            f"{province} harvest residue conservation",
        )
        model_recoverable = _finite_nonnegative(
            record["model_recoverable_odt_base"],
            f"{province} model_recoverable_odt_base",
        )
        _require_close(
            model_recoverable,
            gross * RSYC_BASE_RECOVERY,
            f"{province} base recoverable harvest residue",
        )
        rsyc_base = _finite_nonnegative(
            record["rsyc_base_recoverable_odt"],
            f"{province} rsyc_base_recoverable_odt",
        )
        if harvest_m3 > 0.0 and rsyc_base <= 0.0:
            raise NationalResidueCurveError(
                f"{province}: RSYC base is zero with positive harvest"
            )
        if _parse_bool(record["model_estimate"], f"{province} model_estimate") is not True:
            raise NationalResidueCurveError(f"{province}: model_estimate is not true")
        if _parse_bool(record["observed"], f"{province} observed"):
            raise NationalResidueCurveError(f"{province}: observed is true")
        if _parse_bool(record["policy_grade"], f"{province} policy_grade"):
            raise NationalResidueCurveError(f"{province}: policy_grade is true")
        if _parse_bool(record["facility_assignment"], f"{province} facility_assignment"):
            raise NationalResidueCurveError(
                f"{province}: product chain is unexpectedly facility-assigned"
            )
        coefficient_tier = str(record["coefficient_tier"]).strip()
        if coefficient_tier not in {
            "bc_anchor_species_group",
            "donor_coefficient",
        }:
            raise NationalResidueCurveError(
                f"{province}: unexpected coefficient tier {coefficient_tier!r}"
            )
        chains[province] = ProductChainProvince(
            province=province,
            harvest_m3=harvest_m3,
            class_residue_odt=class_residue,
            harvest_residue_gross_odt=gross,
            harvest_residue_recoverable_odt=recoverable,
            harvest_residue_unrecovered_odt=unrecovered,
            model_recoverable_odt_base=model_recoverable,
            rsyc_base_recoverable_odt=rsyc_base,
            coefficient_tier=coefficient_tier,
        )

    national_harvest = math.fsum(chain.harvest_m3 for chain in chains.values())
    _require_close(
        national_harvest,
        EXPECTED_HARVEST_BASIS_M3,
        "national harvest basis",
        abs_tol=1e-3,
    )
    return chains


def _parse_rsyc_basis(
    product_chain: Mapping[str, ProductChainProvince],
) -> RsvcBasis:
    payload = _load_json(RSYC_SUMMARY, "RSYC residue factor summary")
    definitions = payload.get("definitions", {})
    lhv = _finite_nonnegative(
        definitions.get("lhv_gj_per_odt"), "RSYC lhv_gj_per_odt"
    )
    _require_close(lhv, EXPECTED_LHV_GJ_PER_ODT, "RSYC LHV")
    recovery = definitions.get("recovery", {})
    recovery_fractions = tuple(
        sorted(
            _finite_nonnegative(recovery.get(key), f"RSYC recovery {key}")
            for key in ("low", "base", "high")
        )
    )
    if recovery_fractions != (0.35, 0.60, 0.75):
        raise NationalResidueCurveError(
            f"unexpected RSYC recovery fractions: {recovery_fractions!r}"
        )

    factors: dict[str, float] = {}
    for row in payload.get("province_factors", []):
        if str(row.get("level", "")).strip().lower() != "province":
            continue
        province = str(row.get("province", "")).strip().upper()
        if province not in PROVINCES:
            raise NationalResidueCurveError(
                f"RSYC province factor has unexpected province {province!r}"
            )
        factor = _finite_nonnegative(
            row.get("recoverable_odt_per_m3_base"),
            f"RSYC {province} recoverable_odt_per_m3_base",
        )
        factors[province] = factor
    if set(factors) != set(PROVINCES):
        missing = sorted(set(PROVINCES) - set(factors))
        raise NationalResidueCurveError(
            f"RSYC province factors are missing provinces: {missing}"
        )

    province_119m_odt = {
        province: factors[province] * product_chain[province].harvest_m3
        for province in PROVINCES
    }
    base_119m_odt = math.fsum(province_119m_odt.values())
    base_119m_pj = base_119m_odt * lhv / PJ_GJ
    _require_close(
        base_119m_odt,
        EXPECTED_RSYC_119M_ODT,
        "RSYC 119 Mm3 recoverable odt",
        abs_tol=1e-3,
    )
    _require_close(
        base_119m_pj,
        EXPECTED_RSYC_119M_PJ,
        "RSYC 119 Mm3 recoverable PJ",
        abs_tol=1e-4,
    )

    base_100m_odt = 0.0
    base_100m_pj = 0.0
    for row in payload.get("harvest_sensitivity", []):
        if (
            str(row.get("case", "")).strip() == "base"
            and abs(
                float(row.get("harvest_level_million_m3_per_year", 0.0)) - 100.0
            )
            <= 1e-9
        ):
            base_100m_odt = _finite_nonnegative(
                row.get("recoverable_residue_odt_per_year"),
                "RSYC 100 Mm3 recoverable odt",
            )
            base_100m_pj = _finite_nonnegative(
                row.get("recoverable_pj_per_year"),
                "RSYC 100 Mm3 recoverable PJ",
            )
            break
    if base_100m_odt <= 0.0 or base_100m_pj <= 0.0:
        raise NationalResidueCurveError(
            "RSYC summary has no base 100 Mm3 harvest row"
        )
    _require_close(
        base_100m_odt,
        EXPECTED_RSYC_100M_ODT,
        "RSYC 100 Mm3 recoverable odt",
        abs_tol=1e-6,
    )
    _require_close(
        base_100m_pj,
        EXPECTED_RSYC_100M_PJ,
        "RSYC 100 Mm3 recoverable PJ",
        abs_tol=1e-6,
    )
    return RsvcBasis(
        lhv_gj_per_odt=lhv,
        lhv_source=str(RSYC_SUMMARY.relative_to(REPO_ROOT)),
        recovery_fractions=recovery_fractions,
        base_119m_odt=base_119m_odt,
        base_119m_pj=base_119m_pj,
        base_100m_odt=base_100m_odt,
        base_100m_pj=base_100m_pj,
        province_119m_odt=province_119m_odt,
    )


def _parse_province_harvest(
    province_frame: pd.DataFrame,
    product_chain: Mapping[str, ProductChainProvince],
    rsvc: RsvcBasis,
) -> dict[str, HarvestBase]:
    required = [
        "province",
        "residue_type",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "residue_province_basis",
        "facility_assigned",
        "evidence_tier",
        "rsyc_comparable",
        "rsyc_base_recoverable_odt",
        "model_recoverable_odt_base",
        "rsyc_ratio",
        "rsyc_status",
        "model_estimate",
        "observed",
        "policy_grade",
    ]
    missing = [column for column in required if column not in province_frame.columns]
    if missing:
        raise NationalResidueCurveError(
            f"facility province residue table is missing columns {missing}"
        )
    frame = province_frame.copy()
    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    harvest = frame[frame["residue_type"].astype(str).str.startswith("harvest")]
    if harvest.empty:
        raise NationalResidueCurveError("facility province table has no harvest rows")

    harvest_by_province: dict[str, dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for _, record in harvest.iterrows():
        province = str(record["province"])
        residue_type = str(record["residue_type"]).strip()
        if province not in PROVINCES:
            raise NationalResidueCurveError(
                f"harvest row has unexpected province {province!r}"
            )
        if residue_type in harvest_by_province[province]:
            raise NationalResidueCurveError(
                f"{province}/{residue_type}: duplicate harvest row"
            )
        harvest_by_province[province][residue_type] = record
    expected_types = {
        "harvest_residue_gross",
        "harvest_residue_recoverable",
        "harvest_residue_unrecovered",
    }
    if set(harvest_by_province) != set(PROVINCES):
        raise NationalResidueCurveError(
            "harvest rows do not cover all 13 jurisdictions"
        )

    bases: dict[str, HarvestBase] = {}
    for province in PROVINCES:
        rows = harvest_by_province[province]
        if set(rows) != expected_types:
            raise NationalResidueCurveError(
                f"{province}: unexpected harvest row set {sorted(rows)}"
            )
        gross_row = rows["harvest_residue_gross"]
        recoverable_row = rows["harvest_residue_recoverable"]
        unrecovered_row = rows["harvest_residue_unrecovered"]
        gross_odt = _finite_nonnegative(
            gross_row["residue_odt"], f"{province} gross harvest residue odt"
        )
        gross_pj = _finite_nonnegative(
            gross_row["residue_pj"], f"{province} gross harvest residue pj"
        )
        recoverable_odt = _finite_nonnegative(
            recoverable_row["residue_odt"],
            f"{province} recoverable harvest residue odt",
        )
        recoverable_pj = _finite_nonnegative(
            recoverable_row["residue_pj"],
            f"{province} recoverable harvest residue pj",
        )
        unrecovered_odt = _finite_nonnegative(
            unrecovered_row["residue_odt"],
            f"{province} unrecovered harvest residue odt",
        )
        _require_close(
            recoverable_odt + unrecovered_odt,
            gross_odt,
            f"{province} harvest residue conservation",
        )
        for residue_type, odt, pj in (
            ("gross", gross_odt, gross_pj),
            ("recoverable", recoverable_odt, recoverable_pj),
        ):
            _require_close(
                pj,
                odt * rsvc.lhv_gj_per_odt / PJ_GJ,
                f"{province} {residue_type} harvest PJ",
            )
        chain = product_chain[province]
        _require_close(
            gross_odt,
            chain.harvest_residue_gross_odt,
            f"{province} product-chain gross harvest residue",
        )
        _require_close(
            recoverable_odt,
            chain.harvest_residue_recoverable_odt,
            f"{province} product-chain recoverable harvest residue",
        )
        rsyc_base = _finite_nonnegative(
            recoverable_row["rsyc_base_recoverable_odt"],
            f"{province} rsyc base recoverable odt",
        )
        _require_close(
            rsyc_base,
            chain.rsyc_base_recoverable_odt,
            f"{province} product-chain RSYC base",
        )
        _require_close(
            rsyc_base,
            rsvc.province_119m_odt[province],
            f"{province} RSYC factor reproduction",
        )
        model_recoverable = _finite_nonnegative(
            recoverable_row["model_recoverable_odt_base"],
            f"{province} model recoverable odt",
        )
        _require_close(
            model_recoverable,
            gross_odt * RSYC_BASE_RECOVERY,
            f"{province} base recoverable harvest residue",
        )
        _require_close(
            model_recoverable,
            chain.model_recoverable_odt_base,
            f"{province} product-chain model recoverable harvest residue",
        )
        rsyc_ratio = _finite_nonnegative(
            recoverable_row["rsyc_ratio"], f"{province} rsyc_ratio"
        )
        if rsyc_base > 0.0:
            _require_close(
                rsyc_ratio,
                model_recoverable / rsyc_base,
                f"{province} RSYC ratio",
            )
        if _parse_bool(
            recoverable_row["model_estimate"], f"{province} model_estimate"
        ) is not True:
            raise NationalResidueCurveError(f"{province}: model_estimate is not true")
        if _parse_bool(recoverable_row["observed"], f"{province} observed"):
            raise NationalResidueCurveError(f"{province}: observed is true")
        if _parse_bool(recoverable_row["policy_grade"], f"{province} policy_grade"):
            raise NationalResidueCurveError(f"{province}: policy_grade is true")
        if _parse_bool(
            recoverable_row["rsyc_comparable"], f"{province} rsyc_comparable"
        ) is not True:
            raise NationalResidueCurveError(
                f"{province}: recoverable harvest row is not RSYC-comparable"
            )
        if str(recoverable_row["residue_province_basis"]).strip() != "origin_province":
            raise NationalResidueCurveError(
                f"{province}: harvest residue is not origin-province based"
            )
        if _parse_bool(
            recoverable_row["facility_assigned"], f"{province} facility_assigned"
        ):
            raise NationalResidueCurveError(
                f"{province}: harvest residue is unexpectedly facility-assigned"
            )
        bases[province] = HarvestBase(
            province=province,
            gross_odt=gross_odt,
            gross_m3=chain.harvest_m3,
            model_recoverable_odt_base=model_recoverable,
            rsyc_base_odt=rsyc_base,
            coefficient_tier=chain.coefficient_tier,
            rsyc_ratio=rsyc_ratio,
            rsyc_status=str(recoverable_row["rsyc_status"]).strip(),
        )
    return bases


def _parse_process_base(
    facility_frame: pd.DataFrame,
    province_frame: pd.DataFrame,
    product_chain: Mapping[str, ProductChainProvince],
    rsvc: RsvcBasis,
    facility_summary: Mapping[str, Any],
) -> dict[str, dict[str, ProcessBase]]:
    required = [
        "scenario",
        "province",
        "facility_id",
        "facility_class",
        "residue_type",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "coefficient_tier",
        "evidence_tier",
        "capacity_status",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
    ]
    missing = [column for column in required if column not in facility_frame.columns]
    if missing:
        raise NationalResidueCurveError(
            f"facility residue table is missing columns {missing}"
        )
    frame = facility_frame.copy()
    if set(frame["scenario"].astype(str).str.strip()) != {"province_capacity_floor"}:
        raise NationalResidueCurveError(
            "facility residue table has an unexpected scenario"
        )
    if set(frame["residue_type"].astype(str).str.strip()) != {PROCESS_TYPE}:
        raise NationalResidueCurveError(
            "facility residue table contains non-process residue rows"
        )

    groups: dict[
        tuple[str, str],
        dict[str, Any],
    ] = defaultdict(
        lambda: {
            "odt": [],
            "facilities": set(),
            "coefficient_tiers": set(),
            "capacity_evidence": set(),
        }
    )
    for _, record in frame.iterrows():
        province = str(record["province"]).strip().upper()
        if province not in PROVINCES:
            raise NationalResidueCurveError(
                f"facility residue row has unexpected province {province!r}"
            )
        facility_class = str(record["facility_class"]).strip().upper()
        if facility_class not in CLASS_ORDER:
            raise NationalResidueCurveError(
                f"{province}: unexpected facility class {facility_class!r}"
            )
        facility_id = str(record["facility_id"]).strip()
        if not facility_id:
            raise NationalResidueCurveError(
                f"{province}/{facility_class}: empty facility_id"
            )
        odt = _finite_nonnegative(
            record["residue_odt"], f"{facility_id} residue_odt"
        )
        pj = _finite_nonnegative(
            record["residue_pj"], f"{facility_id} residue_pj"
        )
        _require_close(
            pj,
            odt * rsvc.lhv_gj_per_odt / PJ_GJ,
            f"{facility_id} process residue PJ",
        )
        if _parse_bool(record["model_estimate"], f"{facility_id} model_estimate") is not True:
            raise NationalResidueCurveError(f"{facility_id}: model_estimate is not true")
        if _parse_bool(record["observed"], f"{facility_id} observed"):
            raise NationalResidueCurveError(f"{facility_id}: observed is true")
        if _parse_bool(record["policy_grade"], f"{facility_id} policy_grade"):
            raise NationalResidueCurveError(f"{facility_id}: policy_grade is true")
        if _parse_bool(
            record["facility_assignment"], f"{facility_id} facility_assignment"
        ) is not True:
            raise NationalResidueCurveError(
                f"{facility_id}: process residue is not facility-assigned"
            )
        coefficient_tier = str(record["coefficient_tier"]).strip()
        if coefficient_tier not in {
            "bc_anchor_species_group",
            "donor_coefficient",
        }:
            raise NationalResidueCurveError(
                f"{facility_id}: unexpected coefficient tier {coefficient_tier!r}"
            )
        active_group = groups[(province, facility_class)]
        active_group["odt"].append(odt)
        active_group["facilities"].add(facility_id)
        active_group["coefficient_tiers"].add(coefficient_tier)
        active_group["capacity_evidence"].add(str(record["capacity_status"]).strip())

    process_base: dict[str, dict[str, ProcessBase]] = {}
    for province in PROVINCES:
        process_base[province] = {}
        for facility_class in CLASS_ORDER:
            maybe_group = groups.get((province, facility_class))
            if maybe_group is None:
                odt = 0.0
                facilities = 0
                tiers: tuple[str, ...] = ()
                capacity: tuple[str, ...] = ()
            else:
                odt = math.fsum(maybe_group["odt"])
                facilities = len(maybe_group["facilities"])
                tiers = tuple(sorted(maybe_group["coefficient_tiers"]))
                capacity = tuple(sorted(maybe_group["capacity_evidence"]))
            process_base[province][facility_class] = ProcessBase(
                province=province,
                residue_class=facility_class,
                residue_odt=odt,
                facility_count=facilities,
                coefficient_tiers=tiers,
                capacity_evidence=capacity,
            )

    province_process = province_frame[
        province_frame["residue_type"].astype(str).str.strip() == PROCESS_TYPE
    ]
    if province_process.empty:
        raise NationalResidueCurveError(
            "facility province table has no process residue rows"
        )
    province_lookup: dict[str, Mapping[str, Any]] = {}
    for _, record in province_process.iterrows():
        province = str(record["province"]).strip().upper()
        if province in province_lookup:
            raise NationalResidueCurveError(
                f"{province}: duplicate province process residue row"
            )
        province_lookup[province] = record
    if set(province_lookup) != set(PROVINCES):
        raise NationalResidueCurveError(
            "province process residue rows do not cover all 13 jurisdictions"
        )
    for province in PROVINCES:
        expected = _finite_nonnegative(
            province_lookup[province]["residue_odt"],
            f"{province} province process residue odt",
        )
        actual = math.fsum(
            process_base[province][facility_class].residue_odt
            for facility_class in CLASS_ORDER
        )
        _require_close(actual, expected, f"{province} province process residue")

    national_class = {
        facility_class: math.fsum(
            process_base[province][facility_class].residue_odt
            for province in PROVINCES
        )
        for facility_class in CLASS_ORDER
    }
    for facility_class in CLASS_ORDER:
        expected = math.fsum(
            product_chain[province].class_residue_odt[facility_class]
            for province in PROVINCES
        )
        _require_close(
            national_class[facility_class],
            expected,
            f"national {facility_class} process residue versus product chain",
        )
    national_total = math.fsum(national_class.values())
    summary_total = _finite_nonnegative(
        facility_summary.get("national_totals", {}).get("process_residue_odt"),
        "facility summary process residue",
    )
    _require_close(
        national_total,
        summary_total,
        "national process residue versus facility summary",
    )
    return process_base


def _parse_osecan_context() -> OsecanContext:
    if not OSECAN_DISPATCH.is_file():
        raise NationalResidueCurveError(
            f"OSeCAN dispatch file is missing: {OSECAN_DISPATCH}"
        )
    source_hash = _sha256(OSECAN_DISPATCH)
    required = ["run", "technology", "category", "zone", "year", "value_pj"]
    frame = _read_csv(OSECAN_DISPATCH, required, "OSeCAN dispatch_by_zone")
    if "stress_119m" not in set(frame["run"].astype(str).str.strip()):
        raise NationalResidueCurveError(
            "OSeCAN dispatch file lacks the stress_119m run"
        )
    stress = frame[frame["run"].astype(str).str.strip() == "stress_119m"].copy()
    categories = set(stress["category"].astype(str).str.strip())
    if categories != {"supply", "dispatch"}:
        raise NationalResidueCurveError(
            f"OSeCAN stress run has unexpected categories: {sorted(categories)}"
        )

    national_supply: dict[int, list[float]] = defaultdict(list)
    bc_supply: dict[int, list[float]] = defaultdict(list)
    national_dispatch: dict[int, list[float]] = defaultdict(list)
    bc_dispatch: dict[int, list[float]] = defaultdict(list)
    for _, record in stress.iterrows():
        category = str(record["category"]).strip()
        zone = str(record["zone"]).strip().upper()
        if not zone:
            raise NationalResidueCurveError("OSeCAN dispatch row has an empty zone")
        try:
            year = int(str(record["year"]).strip())
        except ValueError as exc:
            raise NationalResidueCurveError(
                f"OSeCAN dispatch row has an invalid year: {record['year']!r}"
            ) from exc
        value = _finite_nonnegative(
            record["value_pj"], f"OSeCAN {year}/{zone}/{category} value_pj"
        )
        if category == "supply":
            national_supply[year].append(value)
            if zone in {"B1", "B2"}:
                bc_supply[year].append(value)
        else:
            national_dispatch[year].append(value)
            if zone in {"B1", "B2"}:
                bc_dispatch[year].append(value)

    years = tuple(sorted(national_supply))
    expected_years = tuple(range(2021, 2071))
    if years != expected_years:
        raise NationalResidueCurveError(
            "OSeCAN dispatch file does not cover 2021-2070"
        )
    supply = {year: math.fsum(national_supply[year]) for year in years}
    bc = {year: math.fsum(bc_supply[year]) for year in years}
    dispatch = {year: math.fsum(national_dispatch[year]) for year in years}
    bc_dispatch_values = {year: math.fsum(bc_dispatch[year]) for year in years}
    for year in years:
        if bc[year] > supply[year] + ABS_TOL:
            raise NationalResidueCurveError(
                f"OSeCAN {year}: BC supply exceeds national supply"
            )
        if bc_dispatch_values[year] > dispatch[year] + ABS_TOL:
            raise NationalResidueCurveError(
                f"OSeCAN {year}: BC dispatch exceeds national dispatch"
            )
        if dispatch[year] > supply[year] + ABS_TOL:
            raise NationalResidueCurveError(
                f"OSeCAN {year}: power dispatch exceeds biomass supply"
            )
    _require_close(
        supply[2050],
        152.92277,
        "OSeCAN 2050 national supply context",
        abs_tol=1e-5,
    )
    _require_close(
        bc[2050],
        43.52377,
        "OSeCAN 2050 BC supply context",
        abs_tol=1e-5,
    )
    _require_close(
        dispatch[2050],
        41.108271,
        "OSeCAN 2050 national power dispatch context",
        abs_tol=1e-6,
    )
    _require_close(
        bc_dispatch_values[2050],
        11.699938,
        "OSeCAN 2050 BC power dispatch context",
        abs_tol=1e-6,
    )

    manifest = _load_json(OSECAN_MANIFEST, "OSeCAN manifest")
    manifest_status = str(manifest.get("status", "")).strip()
    harvest_basis = manifest.get("harvest_basis", {})
    _require_close(
        _finite_nonnegative(
            harvest_basis.get("national_log_m3"),
            "OSeCAN national_log_m3",
        ),
        EXPECTED_HARVEST_BASIS_M3,
        "OSeCAN harvest basis",
        abs_tol=1e-3,
    )
    return OsecanContext(
        run="stress_119m",
        source=str(OSECAN_DISPATCH.relative_to(REPO_ROOT)),
        source_sha256=source_hash,
        manifest_status=manifest_status,
        national_supply_pj=supply,
        bc_supply_pj=bc,
        national_dispatch_pj=dispatch,
        bc_dispatch_pj=bc_dispatch_values,
    )


def _process_evidence_tier(
    base: Mapping[str, Mapping[str, ProcessBase]],
    province: str,
    residue_class: str,
) -> str:
    tiers: set[str] = set()
    for facility_class in CLASS_ORDER:
        if residue_class in {facility_class, TOTAL_CLASS}:
            tiers.update(base[province][facility_class].coefficient_tiers)
    if not tiers:
        return "facility_process_residue_v1:no_positive_residue"
    return "facility_process_residue_v1:" + "+".join(sorted(tiers))


def _process_capacity_evidence(
    base: Mapping[str, Mapping[str, ProcessBase]],
    province: str,
    residue_class: str,
) -> str:
    evidence: set[str] = set()
    for facility_class in CLASS_ORDER:
        if residue_class in {facility_class, TOTAL_CLASS}:
            evidence.update(base[province][facility_class].capacity_evidence)
    return ";".join(sorted(evidence))


def _national_process_evidence_tier(
    base: Mapping[str, Mapping[str, ProcessBase]],
) -> str:
    tiers: set[str] = set()
    for province in PROVINCES:
        for facility_class in CLASS_ORDER:
            tiers.update(base[province][facility_class].coefficient_tiers)
    if not tiers:
        return "facility_process_residue_v1:no_positive_residue"
    return "facility_process_residue_v1:national_aggregate:" + "+".join(
        sorted(tiers)
    )


def _national_process_capacity_evidence(
    base: Mapping[str, Mapping[str, ProcessBase]],
) -> str:
    evidence: set[str] = set()
    for province in PROVINCES:
        for facility_class in CLASS_ORDER:
            evidence.update(base[province][facility_class].capacity_evidence)
    return ";".join(sorted(evidence))


def _harvest_evidence_tier(province: str, harvest: HarvestBase) -> str:
    return f"rsyc_province_factor:{harvest.coefficient_tier}"


def _curve_row(
    *,
    geography_level: str,
    province: str,
    province_name: str,
    residue_type: str,
    residue_class: str,
    stream: str,
    availability_case: str,
    availability_fraction: float,
    base_odt: float,
    residue_odt: float,
    rsvc: RsvcBasis,
    province_basis: str,
    facility_assigned: bool,
    rsyc_comparable: bool,
    rsyc_base_odt: float,
    rsyc_base_pj: float,
    rsyc_ratio: float,
    evidence_tier: str,
    capacity_evidence_mix: str,
    caveat: str,
    notes: str,
) -> dict[str, Any]:
    return {
        "geography_level": geography_level,
        "province": province,
        "province_name": province_name,
        "residue_type": residue_type,
        "residue_class": residue_class,
        "stream": stream,
        "availability_case": availability_case,
        "availability_fraction": availability_fraction,
        "base_residue_odt": base_odt,
        "residue_odt": residue_odt,
        "residue_pj": residue_odt * rsvc.lhv_gj_per_odt / PJ_GJ,
        "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
        "province_basis": province_basis,
        "facility_assigned": LABEL_TRUE if facility_assigned else LABEL_FALSE,
        "rsyc_comparable": LABEL_TRUE if rsyc_comparable else LABEL_FALSE,
        "rsyc_base_119m_recoverable_odt": rsyc_base_odt,
        "rsyc_base_119m_recoverable_pj": rsyc_base_pj,
        "rsyc_ratio_to_119m_base": rsyc_ratio,
        "model_estimate": LABEL_TRUE,
        "observed": LABEL_FALSE,
        "policy_grade": LABEL_FALSE,
        "not_policy": LABEL_TRUE,
        "evidence_tier": evidence_tier,
        "capacity_evidence_mix": capacity_evidence_mix,
        "in_supply_curve": LABEL_TRUE,
        "caveat": caveat,
        "notes": notes,
    }


def _build_curve_rows(
    process_base: Mapping[str, Mapping[str, ProcessBase]],
    harvest_base: Mapping[str, HarvestBase],
    rsvc: RsvcBasis,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    province_rows: list[dict[str, Any]] = []
    national_rows: list[dict[str, Any]] = []

    def process_rows_for(
        level: str,
        province: str,
        province_name: str,
        base_by_class: Mapping[str, float],
        evidence_tier: str,
        capacity_mix: str,
    ) -> None:
        rows = province_rows if level == "province" else national_rows
        for fraction, case in PROCESS_AVAILABILITY_CASES:
            for residue_class in (*CLASS_ORDER, TOTAL_CLASS):
                base_odt = (
                    math.fsum(base_by_class[item] for item in CLASS_ORDER)
                    if residue_class == TOTAL_CLASS
                    else base_by_class[residue_class]
                )
                residue_odt = base_odt * fraction
                caveat = (
                    PROCESS_TOTAL_CAVEAT
                    if residue_class == TOTAL_CLASS
                    else PROCESS_CAVEAT
                )
                if level == "national":
                    caveat = f"{caveat} {NATIONAL_CAVEAT}"
                rows.append(
                    _curve_row(
                        geography_level=level,
                        province=province,
                        province_name=province_name,
                        residue_type=PROCESS_TYPE,
                        residue_class=residue_class,
                        stream=PROCESS_STREAM,
                        availability_case=case,
                        availability_fraction=fraction,
                        base_odt=base_odt,
                        residue_odt=residue_odt,
                        rsvc=rsvc,
                        province_basis="facility_province",
                        facility_assigned=True,
                        rsyc_comparable=False,
                        rsyc_base_odt=0.0,
                        rsyc_base_pj=0.0,
                        rsyc_ratio=0.0,
                        evidence_tier=evidence_tier,
                        capacity_evidence_mix=capacity_mix,
                        caveat=caveat,
                        notes=(
                            f"Base process residue {base_odt:,.3f} odt at "
                            f"{fraction:.0%} availability; class "
                            f"{residue_class}; facility province basis."
                        ),
                    )
                )

    def harvest_rows_for(
        level: str,
        province: str,
        province_name: str,
        gross_odt: float,
        gross_m3: float,
        evidence_tier: str,
    ) -> None:
        rows = province_rows if level == "province" else national_rows
        for fraction, case in HARVEST_RECOVERY_CASES:
            residue_odt = gross_odt * fraction
            caveat = HARVEST_CAVEAT
            if level == "national":
                caveat = f"{caveat} {NATIONAL_CAVEAT}"
            rows.append(
                _curve_row(
                    geography_level=level,
                    province=province,
                    province_name=province_name,
                    residue_type=HARVEST_TYPE,
                    residue_class=ALL_CLASS,
                    stream=HARVEST_STREAM,
                    availability_case=case,
                    availability_fraction=fraction,
                    base_odt=gross_odt,
                    residue_odt=residue_odt,
                    rsvc=rsvc,
                    province_basis="origin_province",
                    facility_assigned=False,
                    rsyc_comparable=True,
                    rsyc_base_odt=rsvc.base_119m_odt,
                    rsyc_base_pj=rsvc.base_119m_pj,
                    rsyc_ratio=(
                        residue_odt / rsvc.base_119m_odt
                        if rsvc.base_119m_odt > 0.0
                        else 0.0
                    ),
                    evidence_tier=evidence_tier,
                    capacity_evidence_mix="",
                    caveat=caveat,
                    notes=(
                        f"Gross harvest residue {gross_odt:,.3f} odt "
                        f"({gross_m3:,.3f} m3) at {fraction:.0%} recovery; "
                        "origin province basis."
                    ),
                )
            )

    for province, province_name in PROVINCES.items():
        province_classes = {
            facility_class: process_base[province][facility_class].residue_odt
            for facility_class in CLASS_ORDER
        }
        process_evidence = _process_evidence_tier(process_base, province, TOTAL_CLASS)
        process_capacity = _process_capacity_evidence(
            process_base, province, TOTAL_CLASS
        )
        process_rows_for(
            "province",
            province,
            province_name,
            province_classes,
            process_evidence,
            process_capacity,
        )
        harvest = harvest_base[province]
        harvest_rows_for(
            "province",
            province,
            province_name,
            harvest.gross_odt,
            harvest.gross_m3,
            _harvest_evidence_tier(province, harvest),
        )

    national_classes = {
        facility_class: math.fsum(
            process_base[province][facility_class].residue_odt
            for province in PROVINCES
        )
        for facility_class in CLASS_ORDER
    }
    national_process_evidence = _national_process_evidence_tier(process_base)
    national_capacity = _national_process_capacity_evidence(process_base)
    process_rows_for(
        "national",
        NATIONAL_PROVINCE,
        NATIONAL_PROVINCE_NAME,
        national_classes,
        national_process_evidence,
        national_capacity,
    )
    national_gross = math.fsum(
        harvest_base[province].gross_odt for province in PROVINCES
    )
    national_gross_m3 = math.fsum(
        harvest_base[province].gross_m3 for province in PROVINCES
    )
    harvest_rows_for(
        "national",
        NATIONAL_PROVINCE,
        NATIONAL_PROVINCE_NAME,
        national_gross,
        national_gross_m3,
        "rsyc_province_factor:national_province_factor_aggregate",
    )
    return province_rows, national_rows


def _scenario_totals(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {
        "process": {},
        "harvest": {},
    }
    for fraction, case in PROCESS_AVAILABILITY_CASES:
        selected = [
            row
            for row in rows
            if row["geography_level"] == "national"
            and row["availability_case"] == case
            and row["residue_class"] == TOTAL_CLASS
        ]
        if len(selected) != 1:
            raise NationalResidueCurveError(
                f"expected one national process total for {case}"
            )
        row = selected[0]
        totals["process"][case] = {
            "availability_fraction": fraction,
            "residue_odt": float(row["residue_odt"]),
            "residue_pj": float(row["residue_pj"]),
        }
    for fraction, case in HARVEST_RECOVERY_CASES:
        selected = [
            row
            for row in rows
            if row["geography_level"] == "national"
            and row["availability_case"] == case
        ]
        if len(selected) != 1:
            raise NationalResidueCurveError(
                f"expected one national harvest row for {case}"
            )
        row = selected[0]
        totals["harvest"][case] = {
            "availability_fraction": fraction,
            "recoverable_odt": float(row["residue_odt"]),
            "recoverable_pj": float(row["residue_pj"]),
            "rsyc_ratio_to_119m_base": float(
                row["rsyc_ratio_to_119m_base"]
            ),
        }
    return totals


def _check(
    checks: list[dict[str, Any]],
    check_id: str,
    passed: bool,
    detail: str,
    **metrics: Any,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "status": "PASS" if passed else "FAIL",
            "detail": detail,
            **metrics,
        }
    )
    if not passed:
        raise NationalResidueCurveError(f"{check_id}: {detail}")


def _validate(
    *,
    process_base: Mapping[str, Mapping[str, ProcessBase]],
    harvest_base: Mapping[str, HarvestBase],
    province_rows: Sequence[Mapping[str, Any]],
    national_rows: Sequence[Mapping[str, Any]],
    rsvc: RsvcBasis,
    osecan: OsecanContext,
    osecan_hash_before: str,
    osecan_manifest_hash_before: str,
    deletion_clean: bool,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    all_rows = [*province_rows, *national_rows]

    expected_province_rows = len(PROVINCES) * (
        len(PROCESS_AVAILABILITY_CASES) * (len(CLASS_ORDER) + 1)
        + len(HARVEST_RECOVERY_CASES)
    )
    expected_national_rows = (
        len(PROCESS_AVAILABILITY_CASES) * (len(CLASS_ORDER) + 1)
        + len(HARVEST_RECOVERY_CASES)
    )
    _check(
        checks,
        "row_counts",
        len(province_rows) == expected_province_rows
        and len(national_rows) == expected_national_rows,
        "province and national curve row counts match the scenario design",
        province_rows=len(province_rows),
        national_rows=len(national_rows),
        expected_province_rows=expected_province_rows,
        expected_national_rows=expected_national_rows,
    )

    keys = [
        (
            row["geography_level"],
            row["province"],
            row["residue_type"],
            row["residue_class"],
            row["availability_case"],
        )
        for row in all_rows
    ]
    _check(
        checks,
        "unique_row_keys",
        len(keys) == len(set(keys)),
        "curve row keys are unique",
        rows=len(keys),
        unique=len(set(keys)),
    )

    residue_types = {str(row["residue_type"]) for row in all_rows}
    _check(
        checks,
        "process_harvest_separate",
        residue_types == {PROCESS_TYPE, HARVEST_TYPE},
        "only process and harvest recoverable streams appear; no combined rows",
        residue_types=sorted(residue_types),
    )
    _check(
        checks,
        "no_gross_harvest_rows",
        all(
            row["residue_type"] != "harvest_residue_gross" for row in all_rows
        ),
        "gross harvest residue is a reference base, not a curve row",
    )

    arithmetic_errors: list[str] = []
    zero_errors: list[str] = []
    for row in all_rows:
        base = float(row["base_residue_odt"])
        fraction = float(row["availability_fraction"])
        expected = base * fraction
        if abs(float(row["residue_odt"]) - expected) > REL_TOL * max(
            expected, 1.0
        ) + ABS_TOL:
            arithmetic_errors.append(
                f"{row['geography_level']}/{row['province']}/"
                f"{row['residue_type']}/{row['residue_class']}/"
                f"{row['availability_case']}"
            )
        if fraction == 0.0 and (
            float(row["residue_odt"]) != 0.0 or float(row["residue_pj"]) != 0.0
        ):
            zero_errors.append(f"{row['province']}/{row['availability_case']}")
    _check(
        checks,
        "scenario_arithmetic",
        not arithmetic_errors,
        f"{len(arithmetic_errors)} rows do not equal base x fraction",
    )
    _check(
        checks,
        "zero_availability_case",
        not zero_errors,
        f"{len(zero_errors)} zero-case rows are non-zero",
    )

    pj_errors: list[str] = []
    for row in all_rows:
        expected = float(row["residue_odt"]) * rsvc.lhv_gj_per_odt / PJ_GJ
        if abs(float(row["residue_pj"]) - expected) > REL_TOL * max(
            expected, 1.0
        ) + ABS_TOL:
            pj_errors.append(
                f"{row['province']}/{row['residue_type']}/"
                f"{row['availability_case']}"
            )
    _check(
        checks,
        "pj_conversion",
        not pj_errors,
        f"{len(pj_errors)} rows fail the 18.5 GJ/odt conversion",
        lhv_gj_per_odt=rsvc.lhv_gj_per_odt,
    )

    conservation_errors: list[str] = []
    for province in PROVINCES:
        for fraction, case in PROCESS_AVAILABILITY_CASES:
            class_rows = [
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
                and row["residue_class"] in CLASS_ORDER
            ]
            total_rows = [
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
                and row["residue_class"] == TOTAL_CLASS
            ]
            if len(class_rows) != len(CLASS_ORDER) or len(total_rows) != 1:
                conservation_errors.append(f"{province}/{case}: row set")
                continue
            class_sum = math.fsum(float(row["residue_odt"]) for row in class_rows)
            total = float(total_rows[0]["residue_odt"])
            if abs(class_sum - total) > REL_TOL * max(total, 1.0) + ABS_TOL:
                conservation_errors.append(f"{province}/{case}: class total")
    for fraction, case in PROCESS_AVAILABILITY_CASES:
        for residue_class in (*CLASS_ORDER, TOTAL_CLASS):
            province_sum = math.fsum(
                float(row["residue_odt"])
                for row in province_rows
                if row["availability_case"] == case
                and row["residue_class"] == residue_class
            )
            national = [
                row
                for row in national_rows
                if row["availability_case"] == case
                and row["residue_class"] == residue_class
            ]
            if len(national) != 1:
                conservation_errors.append(f"national/{case}/{residue_class}: row")
                continue
            if abs(province_sum - float(national[0]["residue_odt"])) > REL_TOL * max(
                province_sum, 1.0
            ) + ABS_TOL:
                conservation_errors.append(
                    f"national/{case}/{residue_class}: province sum"
                )
    for fraction, case in HARVEST_RECOVERY_CASES:
        province_sum = math.fsum(
            float(row["residue_odt"])
            for row in province_rows
            if row["availability_case"] == case
        )
        national = [
            row
            for row in national_rows
            if row["availability_case"] == case
        ]
        if len(national) != 1:
            conservation_errors.append(f"national/{case}: harvest row")
            continue
        if abs(province_sum - float(national[0]["residue_odt"])) > REL_TOL * max(
            province_sum, 1.0
        ) + ABS_TOL:
            conservation_errors.append(f"national/{case}: harvest province sum")
    _check(
        checks,
        "province_national_conservation",
        not conservation_errors,
        f"{len(conservation_errors)} conservation failures",
        failures=conservation_errors[:5],
    )

    base_errors: list[str] = []
    for province in PROVINCES:
        for facility_class in CLASS_ORDER:
            base = process_base[province][facility_class].residue_odt
            rows = [
                row
                for row in province_rows
                if row["province"] == province
                and row["residue_class"] == facility_class
                and row["availability_case"] == PROCESS_AVAILABILITY_CASES[-1][1]
            ]
            if len(rows) != 1 or abs(float(rows[0]["base_residue_odt"]) - base) > (
                REL_TOL * max(base, 1.0) + ABS_TOL
            ):
                base_errors.append(f"{province}/{facility_class}")
        gross = harvest_base[province].gross_odt
        harvest_rows = [
            row
            for row in province_rows
            if row["province"] == province
            and row["residue_type"] == HARVEST_TYPE
        ]
        if len(harvest_rows) != len(HARVEST_RECOVERY_CASES) or any(
            abs(float(row["base_residue_odt"]) - gross)
            > REL_TOL * max(gross, 1.0) + ABS_TOL
            for row in harvest_rows
        ):
            base_errors.append(f"{province}/harvest")
    _check(
        checks,
        "base_reproduction",
        not base_errors,
        f"{len(base_errors)} base residue values do not reproduce source",
        failures=base_errors[:5],
    )

    _require_close(
        rsvc.base_119m_odt,
        EXPECTED_RSYC_119M_ODT,
        "RSYC 119 Mm3 base odt",
        abs_tol=1e-3,
    )
    _require_close(
        rsvc.base_119m_pj,
        EXPECTED_RSYC_119M_PJ,
        "RSYC 119 Mm3 base PJ",
        abs_tol=1e-4,
    )
    harvest_scenarios = {
        case: row
        for row in national_rows
        for fraction, case in HARVEST_RECOVERY_CASES
        if row["availability_case"] == case
    }
    if len(harvest_scenarios) != len(HARVEST_RECOVERY_CASES):
        raise NationalResidueCurveError("national harvest scenario rows are missing")
    for fraction, case in HARVEST_RECOVERY_CASES:
        row = harvest_scenarios[case]
        expected_odt = math.fsum(
            harvest_base[province].gross_odt for province in PROVINCES
        ) * fraction
        _require_close(
            float(row["residue_odt"]),
            expected_odt,
            f"harvest {case} recoverable odt",
            abs_tol=1e-3,
        )
        _require_close(
            float(row["rsyc_ratio_to_119m_base"]),
            expected_odt / rsvc.base_119m_odt,
            f"harvest {case} RSYC ratio",
        )
    _require_close(
        float(harvest_scenarios["harvest_recovery_060"]["rsyc_ratio_to_119m_base"]),
        0.7685618064711366,
        "base-recovery RSYC ratio",
        abs_tol=1e-9,
    )
    _check(
        checks,
        "rsyc_comparison",
        True,
        "RSYC 119 Mm3 base and harvest recovery cases reproduce",
        base_119m_recoverable_odt=rsvc.base_119m_odt,
        base_119m_recoverable_pj=rsvc.base_119m_pj,
        base_100m_recoverable_odt=rsvc.base_100m_odt,
        base_100m_recoverable_pj=rsvc.base_100m_pj,
        harvest_0p60_ratio=float(
            harvest_scenarios["harvest_recovery_060"]["rsyc_ratio_to_119m_base"]
        ),
    )

    label_errors: list[str] = []
    for row in all_rows:
        if (
            row["model_estimate"] != LABEL_TRUE
            or row["observed"] != LABEL_FALSE
            or row["policy_grade"] != LABEL_FALSE
            or row["not_policy"] != LABEL_TRUE
            or row["in_supply_curve"] != LABEL_TRUE
        ):
            label_errors.append(
                f"{row['province']}/{row['availability_case']}"
            )
        should_compare = row["residue_type"] == HARVEST_TYPE
        if (row["rsyc_comparable"] == LABEL_TRUE) != should_compare:
            label_errors.append(
                f"{row['province']}/{row['residue_type']}: rsyc flag"
            )
    _check(
        checks,
        "evidence_labels",
        not label_errors,
        f"{len(label_errors)} label violations",
        failures=label_errors[:5],
    )

    osecan_checks = [
        abs(osecan.national_supply_pj[2050] - 152.92277) <= 1e-5,
        abs(osecan.bc_supply_pj[2050] - 43.52377) <= 1e-5,
        abs(osecan.national_dispatch_pj[2050] - 41.108271) <= 1e-6,
        abs(osecan.bc_dispatch_pj[2050] - 11.699938) <= 1e-6,
    ]
    _check(
        checks,
        "osecan_context",
        all(osecan_checks),
        "OSeCAN 2050 supply and power-dispatch context values reproduce",
        national_supply_2050_pj=osecan.national_supply_pj[2050],
        bc_supply_2050_pj=osecan.bc_supply_pj[2050],
        national_power_dispatch_2050_pj=osecan.national_dispatch_pj[2050],
        bc_power_dispatch_2050_pj=osecan.bc_dispatch_pj[2050],
    )
    _check(
        checks,
        "osecan_input_unchanged",
        _sha256(OSECAN_DISPATCH) == osecan_hash_before
        and _sha256(OSECAN_MANIFEST) == osecan_manifest_hash_before,
        "OSeCAN dispatch and manifest hashes are unchanged",
        source=osecan.source,
    )
    _check(
        checks,
        "repository_hygiene",
        deletion_clean,
        "no unexpected D in parent or data git status"
        if deletion_clean
        else "unexpected deletion status",
    )
    return checks


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise NationalResidueCurveError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise NationalResidueCurveError(
                            f"refusing to write non-finite {key}: {value!r}"
                        )
                    if value < 0.0:
                        raise NationalResidueCurveError(
                            f"refusing to write negative {key}: {value!r}"
                        )
                    rendered[key] = f"{value:.12g}"
                else:
                    rendered[key] = value
            writer.writerow(rendered)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _format_number(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value:,.0f}"
    if abs(value) >= 1_000:
        return f"{value:,.1f}"
    if abs(value) >= 1:
        return f"{value:,.3f}"
    return f"{value:.6f}"


def _report(
    *,
    summary: Mapping[str, Any],
    national_rows: Sequence[Mapping[str, Any]],
    province_rows: Sequence[Mapping[str, Any]],
) -> str:
    totals = summary["scenario_totals"]
    process_totals = totals["process"]
    harvest_totals = totals["harvest"]
    rsvc = summary["rsyc_comparison"]
    osecan = summary["osecan_context"]
    lines = [
        "# National Residue Supply-Curve Sensitivity v1",
        "",
        (
            "This forestry-side screening artifact converts the "
            "`facility_residue_supply_v1` process and harvest residue streams "
            "into availability and recovery scenarios at 18.5 GJ/odt. Process "
            "residue is aggregated by facility province and class; harvest "
            "residue is aggregated by origin province. The streams remain "
            "separate and no combined curve row is written."
        ),
        "",
        (
            "All values are model estimates: `model_estimate=true`, "
            "`observed=false`, `policy_grade=false`, `not_policy=true`. "
            "`rsyc_comparable=true` appears only on harvest recoverable rows. "
            "The artifact is not an operating, policy, AAC, tenure, or "
            "investment statement."
        ),
        "",
        "## Scenario definitions",
        "",
        "| Family | Case | Fraction | Basis |",
        "|---|---|---:|---|",
        "| Process availability | `process_availability_000` | 0% | Facility process residue, each class and total |",
        "| Process availability | `process_availability_025` | 25% | Facility process residue, each class and total |",
        "| Process availability | `process_availability_050` | 50% | Facility process residue, each class and total |",
        "| Process availability | `process_availability_075` | 75% | Facility process residue, each class and total |",
        "| Harvest recovery | `harvest_recovery_035` | 35% | Gross harvest residue by origin province |",
        "| Harvest recovery | `harvest_recovery_060` | 60% | Gross harvest residue by origin province |",
        "| Harvest recovery | `harvest_recovery_075` | 75% | Gross harvest residue by origin province |",
        "",
        (
            "The process availability fraction is applied uniformly to every "
            "class in a case; the class rows let a reader reinterpret the "
            "fraction class by class, and the `TOTAL` row is the sum of the "
            "mutually exclusive classes at the same fraction. Harvest recovery "
            "is applied to gross harvest residue, not to the already "
            "recoverable base."
        ),
        "",
        "## National process residue (PJ)",
        "",
        "| Class | 0% | 25% | 50% | 75% |",
        "|---|---:|---:|---:|---:|",
    ]
    process_case_order = [case for _, case in PROCESS_AVAILABILITY_CASES]
    for residue_class in (*CLASS_ORDER, TOTAL_CLASS):
        values: list[str] = []
        for case in process_case_order:
            row = next(
                row
                for row in national_rows
                if row["availability_case"] == case
                and row["residue_class"] == residue_class
            )
            values.append(_format_number(float(row["residue_pj"])))
        lines.append(
            f"| {residue_class} | " + " | ".join(values) + " |"
        )
    lines.extend(
        [
            "",
            (
                f"Base process residue is "
                f"{_format_number(summary['process_base_national']['residue_odt'])} "
                f"odt / "
                f"{_format_number(summary['process_base_national']['residue_pj'])} "
                "PJ at the documented LHV."
            ),
            "",
            "## National harvest residue (gross and recoverable)",
            "",
            "| Recovery case | Recovery | Gross odt | Recoverable odt | Recoverable PJ | Ratio to RSYC 119 Mm3 base |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for fraction, case in HARVEST_RECOVERY_CASES:
        row = next(
            row for row in national_rows if row["availability_case"] == case
        )
        lines.append(
            f"| `{case}` | {fraction:.0%} | "
            f"{_format_number(float(row['base_residue_odt']))} | "
            f"{_format_number(float(row['residue_odt']))} | "
            f"{_format_number(float(row['residue_pj']))} | "
            f"{float(row['rsyc_ratio_to_119m_base']):.4f} |"
        )
    lines.extend(
        [
            "",
            "## Province process residue (PJ)",
            "",
            "| Province | 0% | 25% | 50% | 75% |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for province in PROVINCES:
        values = []
        for case in process_case_order:
            row = next(
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
                and row["residue_class"] == TOTAL_CLASS
            )
            values.append(_format_number(float(row["residue_pj"])))
        lines.append(f"| {province} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## Province harvest residue (recoverable PJ)",
            "",
            "| Province | 35% | 60% | 75% |",
            "|---|---:|---:|---:|",
        ]
    )
    for province in PROVINCES:
        values = []
        for fraction, case in HARVEST_RECOVERY_CASES:
            row = next(
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
            )
            values.append(_format_number(float(row["residue_pj"])))
        lines.append(f"| {province} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "## RSYC and OSeCAN comparison",
            "",
            (
                f"RSYC 119 Mm3 base recoverable residue is "
                f"{_format_number(rsvc['base_119m_recoverable_odt'])} odt / "
                f"{_format_number(rsvc['base_119m_recoverable_pj'])} PJ. The "
                "documented RSYC 100 Mm3 base is "
                f"{_format_number(rsvc['base_100m_recoverable_odt'])} odt / "
                f"{_format_number(rsvc['base_100m_recoverable_pj'])} PJ; the "
                "familiar 129.6709 PJ figure is that 100 Mm3 reference, not the "
                "119 Mm3 value."
            ),
            "",
            (
                "The harvest 60% case is "
                f"{_format_number(harvest_totals['harvest_recovery_060']['recoverable_odt'])} "
                f"odt / "
                f"{_format_number(harvest_totals['harvest_recovery_060']['recoverable_pj'])} "
                f"PJ, a ratio of "
                f"{harvest_totals['harvest_recovery_060']['rsyc_ratio_to_119m_base']:.4f} "
                "to the RSYC 119 Mm3 base. The 35% and 75% cases bracket it at "
                f"{harvest_totals['harvest_recovery_035']['rsyc_ratio_to_119m_base']:.4f} "
                f"and {harvest_totals['harvest_recovery_075']['rsyc_ratio_to_119m_base']:.4f}. "
                "Only these harvest recoverable rows are RSYC-comparable."
            ),
            "",
            (
                f"OSeCAN 2050 context from `{osecan['source']}` "
                f"(`{osecan['run']}`): national biomass supply activity "
                f"{_format_number(osecan['y2050']['national_supply_pj'])} PJ, "
                f"BC supply activity "
                f"{_format_number(osecan['y2050']['bc_supply_pj'])} PJ; national "
                f"power dispatch "
                f"{_format_number(osecan['y2050']['national_power_dispatch_pj'])} "
                f"PJ, BC power dispatch "
                f"{_format_number(osecan['y2050']['bc_power_dispatch_pj'])} PJ. "
                "This is model context, not a policy target."
            ),
            "",
            "| Comparison (2050) | National PJ | BC PJ |",
            "|---|---:|---:|",
            (
                f"| OSeCAN supply-activity context | "
                f"{_format_number(osecan['y2050']['national_supply_pj'])} | "
                f"{_format_number(osecan['y2050']['bc_supply_pj'])} |"
            ),
            (
                f"| Process residue, 50% availability | "
                f"{_format_number(process_totals['process_availability_050']['residue_pj'])} | "
                f"{_format_number(summary['province_totals']['BC']['process_availability_050_pj'])} |"
            ),
            (
                f"| Harvest residue, 60% recovery | "
                f"{_format_number(harvest_totals['harvest_recovery_060']['recoverable_pj'])} | "
                f"{_format_number(summary['province_totals']['BC']['harvest_recovery_060_pj'])} |"
            ),
            "",
            (
                "Process residue is not added to the RSYC comparison. The "
                "process and harvest streams are not summed into a combined "
                "curve row; any combined interpretation must add them "
                "explicitly outside this artifact."
            ),
            "",
            "## Caveats",
            "",
            "- Process residue is facility process residue aggregated by facility province and class; it is not a measured mill-by-mill availability survey.",
            "- The 0/25/50/75% process availability cases are screening fractions, not observed availability or policy targets.",
            "- Harvest recoverable residue is gross harvest residue times a recovery fraction; it is not facility-assigned and is not a harvest-level forecast.",
            "- The parent artifact's `harvest_residue_recoverable` row is the product-chain residue-feedstock volume, not the 0.60 recovery scenario; this curve applies the documented 0.35/0.60/0.75 recovery fractions to gross harvest residue.",
            "- The process stream and harvest stream are physically distinct here; the artifact does not add them into a single curve.",
            "- Facility process residue includes residue generated from class feedstock that may itself contain residue feedstock; this is a cascade, not a second count of the same provincial harvest residue.",
            "- OSeCAN context uses `stress_119m`, the manifest's informative supply case; `baseline_119m` is a controlled zero-biomass reference.",
            "- OSeCAN supply activity is used as the biomass-demand context; power dispatch is shown separately and is not the same quantity.",
            "- Conversion uses the RSYC dry LHV of 18.5 GJ/odt; no moisture, bark, or heat-rate adjustment is applied.",
            "- The usual 129.6709 PJ figure is the RSYC 100 Mm3 base, not the 119 Mm3 base.",
            "- No transport, cost, revenue, capacity-expansion, tenure, AAC, or policy claim is made. No UI or public OSeCAN surface is changed.",
            "",
            "## Tests and status",
            "",
            (
                f"Build status: `{summary['status']}`. Validation checks: "
                f"{len(summary['validation']['checks'])}, failed: "
                f"{summary['validation']['failed_check_count']}."
            ),
            "",
            (
                "Run `.venv/bin/python -m "
                "workflows.national.facilities.national_residue_curve` to "
                "reproduce the artifact and `.venv/bin/python -m pytest "
                "tests/test_national_residue_curve.py -q` to run the regression "
                "tests."
            ),
            "",
            "No policy/AAC claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the national residue curve artifact and return its summary."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    osecan_hash_before = _sha256(OSECAN_DISPATCH)
    osecan_manifest_hash_before = _sha256(OSECAN_MANIFEST)

    product_chain = _parse_product_chain()
    rsvc = _parse_rsyc_basis(product_chain)

    facility_summary = _load_json(
        FACILITY_DIR / "summary.json", "facility residue summary"
    )
    if facility_summary.get("artifact") != "facility_residue_supply_v1":
        raise NationalResidueCurveError(
            "unexpected facility residue artifact: "
            f"{facility_summary.get('artifact')!r}"
        )
    if facility_summary.get("status") != "pass":
        raise NationalResidueCurveError("facility residue status is not pass")
    if facility_summary.get("model_estimate") is not True:
        raise NationalResidueCurveError("facility residue is not model_estimate")
    if facility_summary.get("observed") is not False:
        raise NationalResidueCurveError("facility residue is not observed=false")
    if facility_summary.get("policy_grade") is not False:
        raise NationalResidueCurveError("facility residue is not policy_grade=false")
    if facility_summary.get("facility_assignment") is not True:
        raise NationalResidueCurveError(
            "facility residue is not facility-assigned"
        )

    facility_columns = [
        "scenario",
        "province",
        "facility_id",
        "facility_class",
        "residue_type",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "coefficient_tier",
        "evidence_tier",
        "capacity_status",
        "model_estimate",
        "observed",
        "policy_grade",
        "facility_assignment",
    ]
    facility_frame = _read_csv(
        FACILITY_DIR / "facility_residue_supply.csv",
        facility_columns,
        "facility residue table",
    )
    province_frame = _read_csv(
        FACILITY_DIR / "province_residue_supply.csv",
        [
            "province",
            "residue_type",
            "residue_odt",
            "residue_pj",
            "lhv_gj_per_odt",
            "residue_province_basis",
            "facility_assigned",
            "evidence_tier",
            "rsyc_comparable",
            "rsyc_base_recoverable_odt",
            "model_recoverable_odt_base",
            "rsyc_ratio",
            "rsyc_status",
            "model_estimate",
            "observed",
            "policy_grade",
        ],
        "facility province residue table",
    )

    process_base = _parse_process_base(
        facility_frame, province_frame, product_chain, rsvc, facility_summary
    )
    harvest_base = _parse_province_harvest(province_frame, product_chain, rsvc)
    osecan = _parse_osecan_context()

    province_rows, national_rows = _build_curve_rows(
        process_base, harvest_base, rsvc
    )
    scenario_totals = _scenario_totals([*province_rows, *national_rows])

    process_base_national_odt = math.fsum(
        process_base[province][facility_class].residue_odt
        for province in PROVINCES
        for facility_class in CLASS_ORDER
    )
    process_base_national_pj = (
        process_base_national_odt * rsvc.lhv_gj_per_odt / PJ_GJ
    )
    harvest_gross_odt = math.fsum(
        harvest_base[province].gross_odt for province in PROVINCES
    )
    harvest_gross_pj = harvest_gross_odt * rsvc.lhv_gj_per_odt / PJ_GJ
    harvest_base_recoverable_odt = math.fsum(
        harvest_base[province].model_recoverable_odt_base
        for province in PROVINCES
    )
    harvest_base_recoverable_pj = (
        harvest_base_recoverable_odt * rsvc.lhv_gj_per_odt / PJ_GJ
    )

    province_totals: dict[str, dict[str, Any]] = {}
    for province in PROVINCES:
        process_values = {
            case: next(
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
                and row["residue_class"] == TOTAL_CLASS
            )
            for _, case in PROCESS_AVAILABILITY_CASES
        }
        harvest_values = {
            case: next(
                row
                for row in province_rows
                if row["province"] == province
                and row["availability_case"] == case
            )
            for _, case in HARVEST_RECOVERY_CASES
        }
        province_totals[province] = {
            **{
                f"{case}_odt": float(process_values[case]["residue_odt"])
                for _, case in PROCESS_AVAILABILITY_CASES
            },
            **{
                f"{case}_pj": float(process_values[case]["residue_pj"])
                for _, case in PROCESS_AVAILABILITY_CASES
            },
            **{
                f"{case}_odt": float(harvest_values[case]["residue_odt"])
                for _, case in HARVEST_RECOVERY_CASES
            },
            **{
                f"{case}_pj": float(harvest_values[case]["residue_pj"])
                for _, case in HARVEST_RECOVERY_CASES
            },
            "process_base_odt": math.fsum(
                process_base[province][facility_class].residue_odt
                for facility_class in CLASS_ORDER
            ),
            "harvest_gross_odt": harvest_base[province].gross_odt,
            "harvest_gross_m3": harvest_base[province].gross_m3,
            "rsyc_base_119m_recoverable_odt": harvest_base[province].rsyc_base_odt,
            "rsyc_base_119m_recoverable_pj": (
                harvest_base[province].rsyc_base_odt
                * rsvc.lhv_gj_per_odt
                / PJ_GJ
            ),
            "rsyc_ratio_base_recovery": harvest_base[province].rsyc_ratio,
            "rsyc_status": harvest_base[province].rsyc_status,
        }

    osecan_years = sorted(osecan.national_supply_pj)
    osecan_context = {
        "source": osecan.source,
        "source_sha256": osecan.source_sha256,
        "run": osecan.run,
        "run_basis": (
            "stress_119m is the manifest's informative supply case; "
            "baseline_119m is a controlled zero-biomass reference"
        ),
        "manifest_status": osecan.manifest_status,
        "demand_metric": (
            "SUPBCW supply activity in PJ is used as the biomass-demand "
            "context; PWRBCW power dispatch is shown separately"
        ),
        "bc_zone_definition": "B1+B2",
        "years": osecan_years,
        "national_supply_pj": {
            str(year): osecan.national_supply_pj[year] for year in osecan_years
        },
        "bc_supply_pj": {
            str(year): osecan.bc_supply_pj[year] for year in osecan_years
        },
        "national_power_dispatch_pj": {
            str(year): osecan.national_dispatch_pj[year] for year in osecan_years
        },
        "bc_power_dispatch_pj": {
            str(year): osecan.bc_dispatch_pj[year] for year in osecan_years
        },
        "y2050": {
            "national_supply_pj": osecan.national_supply_pj[2050],
            "bc_supply_pj": osecan.bc_supply_pj[2050],
            "national_power_dispatch_pj": osecan.national_dispatch_pj[2050],
            "bc_power_dispatch_pj": osecan.bc_dispatch_pj[2050],
        },
        "note": (
            "OSeCAN files are read-only context and are not modified by this "
            "artifact. The values are model context, not a policy target."
        ),
    }

    checks = _validate(
        process_base=process_base,
        harvest_base=harvest_base,
        province_rows=province_rows,
        national_rows=national_rows,
        rsvc=rsvc,
        osecan=osecan,
        osecan_hash_before=osecan_hash_before,
        osecan_manifest_hash_before=osecan_manifest_hash_before,
        deletion_clean=True,
    )
    failed = sum(1 for check in checks if check["status"] != "PASS")
    if failed:
        raise NationalResidueCurveError(f"{failed} validation checks failed")

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "scope": (
            "National and provincial residue supply-curve sensitivity on "
            "facility_residue_supply_v1: process availability 0/25/50/75% by "
            "class and total, harvest recovery 0.35/0.60/0.75 of gross harvest "
            "residue, RSYC 119 Mm3 comparison, and OSeCAN 2050 context."
        ),
        "model_estimate": True,
        "observed": False,
        "policy_grade": False,
        "not_policy": True,
        "ui_or_public_osecan_changed": False,
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "not_policy": True,
            "rsyc_comparable": "harvest_recoverable_only",
            "ui_or_public_osecan_changed": False,
        },
        "method": {
            "process_base": (
                "facility_residue_supply_v1 process residue aggregated by "
                "facility province and facility class"
            ),
            "process_availability_cases": {
                case: fraction for fraction, case in PROCESS_AVAILABILITY_CASES
            },
            "harvest_base": (
                "facility_residue_supply_v1 gross harvest residue by origin "
                "province"
            ),
            "harvest_recovery_cases": {
                case: fraction for fraction, case in HARVEST_RECOVERY_CASES
            },
            "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
            "pj_gj": PJ_GJ,
            "process_vs_harvest": "kept separate; no combined curve row",
            "no_double_count": (
                "each process class is mutually exclusive within a case; the "
                "TOTAL row is the class sum; harvest gross and recoverable rows "
                "are not both included; process and harvest are separate streams"
            ),
        },
        "process_base_national": {
            "residue_odt": process_base_national_odt,
            "residue_pj": process_base_national_pj,
            "by_class_odt": {
                facility_class: math.fsum(
                    process_base[province][facility_class].residue_odt
                    for province in PROVINCES
                )
                for facility_class in CLASS_ORDER
            },
            "by_province_odt": {
                province: math.fsum(
                    process_base[province][facility_class].residue_odt
                    for facility_class in CLASS_ORDER
                )
                for province in PROVINCES
            },
        },
        "harvest_base_national": {
            "gross_odt": harvest_gross_odt,
            "gross_pj": harvest_gross_pj,
            "base_recoverable_0p60_odt": harvest_base_recoverable_odt,
            "base_recoverable_0p60_pj": harvest_base_recoverable_pj,
            "note": (
                "The 0.60 base is gross harvest residue x 0.60. The parent "
                "artifact's harvest_residue_recoverable row is the product-chain "
                "residue-feedstock volume, a different quantity."
            ),
        },
        "scenario_totals": scenario_totals,
        "province_totals": province_totals,
        "rsyc_comparison": {
            "lhv_gj_per_odt": rsvc.lhv_gj_per_odt,
            "lhv_source": rsvc.lhv_source,
            "base_recovery_fraction": RSYC_BASE_RECOVERY,
            "base_119m_recoverable_odt": rsvc.base_119m_odt,
            "base_119m_recoverable_pj": rsvc.base_119m_pj,
            "base_100m_recoverable_odt": rsvc.base_100m_odt,
            "base_100m_recoverable_pj": rsvc.base_100m_pj,
            "harvest_cases": scenario_totals["harvest"],
            "rsyc_comparable_stream": HARVEST_TYPE,
            "note": (
                "Only harvest recoverable rows are RSYC-comparable. The "
                "129.6709 PJ figure is the documented RSYC 100 Mm3 base, not "
                "the 119 Mm3 value. Process residue is never added to the RSYC "
                "stream."
            ),
        },
        "osecan_context": osecan_context,
        "validation": {
            "checks": checks,
            "passed": failed == 0,
            "failed_check_count": failed,
        },
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            "Process availability fractions are screening cases, not observed availability or policy targets.",
            "Harvest recovery fractions apply to gross harvest residue; harvest residue is not facility-assigned.",
            "The parent artifact's harvest_residue_recoverable row is the product-chain residue-feedstock volume, not the 0.60 recovery scenario; this curve applies the documented 0.35/0.60/0.75 recovery fractions to gross harvest residue.",
            "Process and harvest residue are separate streams and are not summed into a combined curve row.",
            "Facility process residue can include residue generated from residue feedstock; this is a cascade, not a second count of the same provincial harvest residue.",
            "Process residue is aggregated by facility province; harvest residue is aggregated by origin province.",
            "The RSYC comparison applies only to harvest recoverable residue; process residue is not RSYC-comparable.",
            "OSeCAN context uses stress_119m; baseline_119m is a zero-biomass reference. OSeCAN supply activity is the biomass-demand context, not a policy target.",
            "No UI or public OSeCAN surface is changed.",
            "No transport, cost, revenue, capacity-expansion, tenure, AAC, or policy claim is made.",
        ],
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                FACILITY_DIR / "facility_residue_supply.csv",
                FACILITY_DIR / "province_residue_supply.csv",
                FACILITY_DIR / "summary.json",
                PRODUCT_CHAIN_DIR / "product_chain_by_province.csv",
                RSYC_SUMMARY,
                OSECAN_DISPATCH,
                OSECAN_MANIFEST,
            )
        },
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.national_residue_curve"
        ),
    }

    curve_fields = [
        "geography_level",
        "province",
        "province_name",
        "residue_type",
        "residue_class",
        "stream",
        "availability_case",
        "availability_fraction",
        "base_residue_odt",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "province_basis",
        "facility_assigned",
        "rsyc_comparable",
        "rsyc_base_119m_recoverable_odt",
        "rsyc_base_119m_recoverable_pj",
        "rsyc_ratio_to_119m_base",
        "model_estimate",
        "observed",
        "policy_grade",
        "not_policy",
        "evidence_tier",
        "capacity_evidence_mix",
        "in_supply_curve",
        "caveat",
        "notes",
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target_dir / "national_residue_curve.csv",
        national_rows,
        curve_fields,
    )
    _write_csv(
        target_dir / "province_residue_curve.csv",
        province_rows,
        curve_fields,
    )
    _write_csv(
        target_dir / "osecan_demand_context.csv",
        [
            {
                "year": year,
                "run": osecan.run,
                "national_supply_pj": osecan.national_supply_pj[year],
                "bc_supply_pj": osecan.bc_supply_pj[year],
                "national_power_dispatch_pj": osecan.national_dispatch_pj[year],
                "bc_power_dispatch_pj": osecan.bc_dispatch_pj[year],
            }
            for year in osecan_years
        ],
        [
            "year",
            "run",
            "national_supply_pj",
            "bc_supply_pj",
            "national_power_dispatch_pj",
            "bc_power_dispatch_pj",
        ],
    )
    _write_json(target_dir / "summary.json", summary)
    (target_dir / "NATIONAL_RESIDUE_CURVE_V1.md").write_text(
        _report(
            summary=summary,
            national_rows=national_rows,
            province_rows=province_rows,
        ),
        encoding="utf-8",
    )

    outputs: dict[str, Any] = {}
    for path in sorted(target_dir.iterdir()):
        if not path.is_file() or path.name == "manifest.json":
            continue
        outputs[path.name] = {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "labels": summary["labels"],
        "scenario_definitions": {
            "process_availability": {
                case: fraction for fraction, case in PROCESS_AVAILABILITY_CASES
            },
            "harvest_recovery": {
                case: fraction for fraction, case in HARVEST_RECOVERY_CASES
            },
        },
        "inputs": summary["inputs"],
        "outputs": outputs,
        "record_counts": {
            "national_rows": len(national_rows),
            "province_rows": len(province_rows),
            "process_scenarios": len(PROCESS_AVAILABILITY_CASES),
            "harvest_scenarios": len(HARVEST_RECOVERY_CASES),
            "classes_per_process_case": len(CLASS_ORDER) + 1,
            "provinces": len(PROVINCES),
        },
        "validation": summary["validation"],
        "rsyc_comparison": summary["rsyc_comparison"],
        "osecan_context": {
            "source": osecan.source,
            "source_sha256": osecan.source_sha256,
            "run": osecan.run,
            "y2050": osecan_context["y2050"],
        },
        "reproduce": summary["reproduce"],
    }
    _write_json(target_dir / "manifest.json", manifest)
    summary["outputs"] = outputs
    assert_no_unexpected_deletions()
    return summary


def main() -> None:
    summary = build()
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "process_base_odt": summary["process_base_national"][
                    "residue_odt"
                ],
                "process_base_pj": summary["process_base_national"][
                    "residue_pj"
                ],
                "harvest_gross_odt": summary["harvest_base_national"][
                    "gross_odt"
                ],
                "harvest_gross_pj": summary["harvest_base_national"][
                    "gross_pj"
                ],
                "process_50pct_pj": summary["scenario_totals"]["process"][
                    "process_availability_050"
                ]["residue_pj"],
                "harvest_60pct_pj": summary["scenario_totals"]["harvest"][
                    "harvest_recovery_060"
                ]["recoverable_pj"],
                "rsyc_ratio_60pct": summary["rsyc_comparison"][
                    "harvest_cases"
                ]["harvest_recovery_060"]["rsyc_ratio_to_119m_base"],
                "osecan_national_2050_pj": summary["osecan_context"]["y2050"][
                    "national_supply_pj"
                ],
                "validation_failures": summary["validation"][
                    "failed_check_count"
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
