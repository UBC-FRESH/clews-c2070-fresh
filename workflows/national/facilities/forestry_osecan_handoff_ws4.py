"""Build a read-only WS4-driven forestry residue handoff to OSeCAN zones.

The WS4 chain applies the frozen chain-v5.1 per-province product/residue
coefficients to the realised 119 Mm3 WS4 harvest.  Both WS4 bases are carried:

* ``scaled_to_119m_control`` is the primary control-total basis; it uses one
  national factor to preserve WS4 provincial shares and match the
  118.9773391 Mm3 provincial control total.
* ``ws4_achieved`` is the realised WS4 harvest basis and is reported for
  transparency.

The handoff uses the frozen handoff-v10 province-to-zone weights, the frozen
v9 process-availability and harvest-recovery definitions, and the frozen v5.1
supply-curve tier thresholds.  The WS4 chain is origin-province based, whereas
handoff v10 process residue is facility-province based.  This artifact is built
on the explicit origin-province basis and labels every row accordingly.  Zone
rows are therefore not compared directly with v10 zone rows; the comparison is
on national totals and tier positions only.

The official OSeCAN tree is read-only and hashed before and after the build.
No candidate activity limit, official OSeCAN file, UI surface, or frozen v5.1
artifact is written.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DATA_ROOT = REPO_ROOT / "data"
WS4_DIR = DATA_ROOT / "derived/national/facilities/ws4_chain_119m"
WS4_PRODUCT_CHAIN = WS4_DIR / "ws4_119m_product_chain.csv"
WS4_HARVEST = WS4_DIR / "ws4_119m_harvest.csv"
WS4_SUMMARY = WS4_DIR / "summary.json"

HANDOFF_V10_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v10"
)
ZONE_WEIGHTS = HANDOFF_V10_DIR / "zone_allocation_weights.csv"
V10_TIER_POSITIONS = HANDOFF_V10_DIR / "case_tier_positions.csv"
V10_SUMMARY = HANDOFF_V10_DIR / "summary.json"
V10_MANIFEST = HANDOFF_V10_DIR / "manifest.json"

CURVE_V9_DIR = (
    DATA_ROOT / "derived/national/facilities/national_residue_curve_v9"
)
CURVE_V9_SUMMARY = CURVE_V9_DIR / "summary.json"

OSECAN_DIR = DATA_ROOT / "derived/national/clews/osecan"
OSECAN_CROSSWALK = OSECAN_DIR / "zone_crosswalk.csv"
OSECAN_TIER_DEFINITIONS = (
    OSECAN_DIR / "national_supply_curve/tier_definitions.csv"
)

OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_ws4"
)
ARTIFACT = "forestry_osecan_handoff_ws4"
ARTIFACT_VERSION = "1.0.0"
ARTIFACT_TITLE = "WS4-driven Forestry Residue Handoff to OSeCAN (origin province)"
REPORT_NAME = "FORESTRY_OSECAN_HANDOFF_WS4.md"
MODULE_PATH = "workflows.national.facilities.forestry_osecan_handoff_ws4"

PRIMARY_BASIS = "scaled_to_119m_control"
SECONDARY_BASIS = "ws4_achieved"
BASES = (PRIMARY_BASIS, SECONDARY_BASIS)
SCENARIO = "119m"

LHV_GJ_PER_ODT = 18.5
PJ_PER_GJ = 1_000_000.0
CONTROL_TOTAL_M3 = 118_977_339.1
EXPECTED_OSECAN_FILE_COUNT = 146
EXPECTED_OSECAN_TREE_SHA256 = (
    "80caa91c3e91c89006b8d5e3ad78232eade70a298a83657510fffa26924bc64a"
)
EXPECTED_TIER_UPPER_PJ = 213.538041

PROVINCES: tuple[str, ...] = (
    "BC",
    "AB",
    "SK",
    "MB",
    "ON",
    "QC",
    "NB",
    "NS",
    "PE",
    "NL",
    "YT",
    "NT",
    "NU",
)
ZONE_ORDER: tuple[str, ...] = (
    "B1",
    "B2",
    "A1",
    "A2",
    "S1",
    "M1",
    "O1",
    "O2",
    "Q1",
    "Q2",
    "T1",
    "L1",
    "N1",
)
MULTI_ZONE_PROVINCES: tuple[str, ...] = ("BC", "AB", "ON", "QC")

PROCESS_CASES: tuple[tuple[str, float], ...] = (
    ("process_availability_000", 0.00),
    ("process_availability_025", 0.25),
    ("process_availability_050", 0.50),
    ("process_availability_075", 0.75),
)
HARVEST_CASES: tuple[tuple[str, float], ...] = (
    ("harvest_recovery_035", 0.35),
    ("harvest_recovery_060", 0.60),
    ("harvest_recovery_075", 0.75),
)
CASE_FRACTIONS = dict((*PROCESS_CASES, *HARVEST_CASES))
HARVEST_COLUMNS = {
    "harvest_recovery_035": (
        "harvest_residue_recoverable_r035_odt",
        "harvest_residue_recoverable_r035_pj",
    ),
    "harvest_recovery_060": (
        "harvest_residue_recoverable_r060_odt",
        "harvest_residue_recoverable_r060_pj",
    ),
    "harvest_recovery_075": (
        "harvest_residue_recoverable_r075_odt",
        "harvest_residue_recoverable_r075_pj",
    ),
}

# Frozen v5.1 tier positions, checked independently against handoff v10's
# 21-row case_tier_positions.csv.  These are model positions, not policy.
FROZEN_V51_TIERS: dict[tuple[str, str, str], str] = {
    ("100m", "process", "process_availability_000"): "T1",
    ("100m", "process", "process_availability_025"): "T2",
    ("100m", "process", "process_availability_050"): "T4",
    ("100m", "process", "process_availability_075"): "above_T4",
    ("100m", "harvest", "harvest_recovery_035"): "T3",
    ("100m", "harvest", "harvest_recovery_060"): "above_T4",
    ("100m", "harvest", "harvest_recovery_075"): "above_T4",
    ("119m", "process", "process_availability_000"): "T1",
    ("119m", "process", "process_availability_025"): "T3",
    ("119m", "process", "process_availability_050"): "above_T4",
    ("119m", "process", "process_availability_075"): "above_T4",
    ("119m", "harvest", "harvest_recovery_035"): "T4",
    ("119m", "harvest", "harvest_recovery_060"): "above_T4",
    ("119m", "harvest", "harvest_recovery_075"): "above_T4",
    ("150m", "process", "process_availability_000"): "T1",
    ("150m", "process", "process_availability_025"): "T4",
    ("150m", "process", "process_availability_050"): "above_T4",
    ("150m", "process", "process_availability_075"): "above_T4",
    ("150m", "harvest", "harvest_recovery_035"): "T4",
    ("150m", "harvest", "harvest_recovery_060"): "above_T4",
    ("150m", "harvest", "harvest_recovery_075"): "above_T4",
}

ROW_LABELS: dict[str, str] = {
    "model_estimate": "true",
    "observed": "false",
    "policy_grade": "false",
    "national_claim": "false",
    "ws4_derived": "true",
    "official_osecan_changed": "false",
    "chain_v51_unchanged": "true",
}
SUMMARY_LABELS: dict[str, bool] = {
    "model_estimate": True,
    "observed": False,
    "policy_grade": False,
    "national_claim": False,
    "ws4_derived": True,
    "official_osecan_changed": False,
    "chain_v51_unchanged": True,
    "ui_changed": False,
    "transport_cost_in_osecan": False,
    "facility_province_handoff": False,
}
REL_TOL = 1e-9
ABS_TOL_PJ = 1e-6
ABS_TOL_ODT = 1e-3
ABS_TOL_WEIGHT = 1e-12


class ForestryOsecanHandoffWs4Error(RuntimeError):
    """Raised when the WS4-to-OSeCAN handoff fails a guard."""


@dataclass(frozen=True)
class ZoneWeight:
    """One province-to-zone allocation weight from frozen handoff v10."""

    province: str
    zone_id: str
    zone_name: str
    weight: float
    weight_method: str
    evidence_tier: str
    proxy_share: bool


@dataclass(frozen=True)
class ProvinceResidue:
    """Trusted origin-province residue totals for one WS4 basis."""

    province: str
    basis: str
    harvest_m3: float
    scale_factor: float
    process_odt: float
    process_pj: float
    harvest_gross_odt: float
    harvest_gross_pj: float
    harvest_odt: dict[str, float]
    harvest_pj: dict[str, float]


@dataclass(frozen=True)
class ZoneAllocation:
    """Trusted zone-level residue total for one basis/stream/case."""

    basis: str
    zone_id: str
    zone_name: str
    stream: str
    availability_case: str
    availability_fraction: float
    odt: float
    pj: float
    national_odt: float
    national_pj: float
    provinces: tuple[str, ...]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _relative(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _hash_tree(root: Path) -> tuple[dict[str, str], str]:
    if not root.is_dir():
        raise ForestryOsecanHandoffWs4Error(f"directory is missing: {root}")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        hashes[_relative(path)] = _sha256(path)
    if not hashes:
        raise ForestryOsecanHandoffWs4Error(f"directory is empty: {root}")
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _tree_state(root: Path) -> dict[str, Any]:
    hashes, digest = _hash_tree(root)
    return {
        "root": _relative(root),
        "file_count": len(hashes),
        "tree_sha256": digest,
    }


def _read_rows(path: Path, required: Sequence[str], label: str) -> list[dict[str, str]]:
    if not path.is_file():
        raise ForestryOsecanHandoffWs4Error(f"{label} does not exist: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = [column for column in required if column not in fieldnames]
        if missing:
            raise ForestryOsecanHandoffWs4Error(
                f"{label} is missing columns {missing}: {path}"
            )
        rows = list(reader)
    if not rows:
        raise ForestryOsecanHandoffWs4Error(f"{label} is empty: {path}")
    return rows


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ForestryOsecanHandoffWs4Error(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ForestryOsecanHandoffWs4Error(
            f"{label} is not valid JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ForestryOsecanHandoffWs4Error(f"{label} is not a JSON object: {path}")
    return payload


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ForestryOsecanHandoffWs4Error(
            f"{label} is not numeric: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise ForestryOsecanHandoffWs4Error(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise ForestryOsecanHandoffWs4Error(f"{label} is negative: {parsed!r}")
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise ForestryOsecanHandoffWs4Error(f"{label} is not a boolean: {value!r}")


def _require_close(
    left: float,
    right: float,
    label: str,
    *,
    abs_tol: float = ABS_TOL_PJ,
    rel_tol: float = REL_TOL,
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > rel_tol * scale + abs_tol:
        raise ForestryOsecanHandoffWs4Error(
            f"{label} mismatch: {left!r} != {right!r} "
            f"(absolute {abs(left - right):.12g})"
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
        raise ForestryOsecanHandoffWs4Error(
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
        raise ForestryOsecanHandoffWs4Error(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _load_crosswalk() -> dict[str, str]:
    rows = _read_rows(
        OSECAN_CROSSWALK,
        ["zone_id", "zone_name"],
        "OSeCAN zone crosswalk",
    )
    zones: dict[str, str] = {}
    for row in rows:
        zone_id = row["zone_id"].strip().upper()
        zone_name = row["zone_name"].strip()
        if zone_id in zones:
            raise ForestryOsecanHandoffWs4Error(
                f"duplicate zone id in crosswalk: {zone_id!r}"
            )
        if not zone_id or not zone_name:
            raise ForestryOsecanHandoffWs4Error(
                "crosswalk contains an empty zone id or name"
            )
        zones[zone_id] = zone_name
    if set(zones) != set(ZONE_ORDER):
        raise ForestryOsecanHandoffWs4Error(
            "official OSeCAN zone set does not match the expected 13 zones: "
            f"{sorted(zones)}"
        )
    return zones


def _load_zone_weights(
    crosswalk: Mapping[str, str],
) -> tuple[dict[str, list[ZoneWeight]], list[ZoneWeight]]:
    rows = _read_rows(
        ZONE_WEIGHTS,
        [
            "province",
            "zone_id",
            "zone_name",
            "weight",
            "weight_method",
            "evidence_tier",
            "proxy_share",
        ],
        "handoff v10 zone allocation weights",
    )
    by_province: dict[str, list[ZoneWeight]] = {}
    flat: list[ZoneWeight] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        province = row["province"].strip().upper()
        zone_id = row["zone_id"].strip().upper()
        zone_name = row["zone_name"].strip()
        if province not in PROVINCES:
            raise ForestryOsecanHandoffWs4Error(
                f"zone weight has unknown province: {province!r}"
            )
        if zone_id not in crosswalk:
            raise ForestryOsecanHandoffWs4Error(
                f"zone weight has unknown zone: {zone_id!r}"
            )
        if zone_name != crosswalk[zone_id]:
            raise ForestryOsecanHandoffWs4Error(
                f"zone {zone_id} name does not match the official crosswalk: "
                f"{zone_name!r} != {crosswalk[zone_id]!r}"
            )
        key = (province, zone_id)
        if key in seen:
            raise ForestryOsecanHandoffWs4Error(
                f"duplicate province/zone weight: {key}"
            )
        seen.add(key)
        weight = _finite_nonnegative(row["weight"], f"{province}/{zone_id} weight")
        if weight <= 0.0 or weight > 1.0 + ABS_TOL_WEIGHT:
            raise ForestryOsecanHandoffWs4Error(
                f"{province}/{zone_id} weight is outside (0, 1]: {weight!r}"
            )
        weight_method = row["weight_method"].strip()
        evidence_tier = row["evidence_tier"].strip()
        if not weight_method or not evidence_tier:
            raise ForestryOsecanHandoffWs4Error(
                f"{province}/{zone_id} weight lacks method or evidence tier"
            )
        item = ZoneWeight(
            province=province,
            zone_id=zone_id,
            zone_name=zone_name,
            weight=weight,
            weight_method=weight_method,
            evidence_tier=evidence_tier,
            proxy_share=_parse_bool(
                row["proxy_share"], f"{province}/{zone_id} proxy_share"
            ),
        )
        by_province.setdefault(province, []).append(item)
        flat.append(item)

    if set(by_province) != set(PROVINCES):
        raise ForestryOsecanHandoffWs4Error(
            "zone weights do not cover all 13 jurisdictions: "
            f"missing={sorted(set(PROVINCES) - set(by_province))}"
        )
    for province in PROVINCES:
        items = by_province[province]
        total = math.fsum(item.weight for item in items)
        _require_close(
            total,
            1.0,
            f"{province} zone weights sum",
            abs_tol=ABS_TOL_WEIGHT,
            rel_tol=1e-12,
        )
        expected_zone_count = 2 if province in MULTI_ZONE_PROVINCES else 1
        if len(items) != expected_zone_count:
            raise ForestryOsecanHandoffWs4Error(
                f"{province} has {len(items)} zone weights; expected "
                f"{expected_zone_count}"
            )
    allocated_zones = {item.zone_id for item in flat}
    if allocated_zones != set(ZONE_ORDER):
        raise ForestryOsecanHandoffWs4Error(
            "zone weights do not cover all 13 official zones: "
            f"{sorted(allocated_zones)}"
        )
    return by_province, flat


def _load_harvest_schedule(
    path: Path = WS4_HARVEST,
) -> dict[str, dict[str, float]]:
    rows = _read_rows(
        path,
        [
            "province",
            "target_m3",
            "achieved_m3",
            "model_estimate",
            "observed",
            "policy_grade",
            "national_claim",
            "ws4_derived",
            "official_osecan_changed",
            "chain_v51_unchanged",
        ],
        "WS4 119 Mm3 harvest",
    )
    by_province: dict[str, dict[str, float]] = {}
    for row in rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise ForestryOsecanHandoffWs4Error(
                f"harvest has unknown province: {province!r}"
            )
        if province in by_province:
            raise ForestryOsecanHandoffWs4Error(
                f"duplicate harvest province: {province!r}"
            )
        if _parse_bool(row["model_estimate"], f"{province} harvest model_estimate") is not True:
            raise ForestryOsecanHandoffWs4Error(
                f"{province} harvest model_estimate is not true"
            )
        for field in ("observed", "policy_grade", "national_claim", "official_osecan_changed"):
            if _parse_bool(row[field], f"{province} harvest {field}"):
                raise ForestryOsecanHandoffWs4Error(
                    f"{province} harvest {field} is true"
                )
        if _parse_bool(row["ws4_derived"], f"{province} harvest ws4_derived") is not True:
            raise ForestryOsecanHandoffWs4Error(
                f"{province} harvest ws4_derived is not true"
            )
        if _parse_bool(row["chain_v51_unchanged"], f"{province} harvest chain_v51_unchanged") is not True:
            raise ForestryOsecanHandoffWs4Error(
                f"{province} harvest chain_v51_unchanged is not true"
            )
        by_province[province] = {
            "target_m3": _finite_nonnegative(
                row["target_m3"], f"{province} harvest target_m3"
            ),
            "achieved_m3": _finite_nonnegative(
                row["achieved_m3"], f"{province} harvest achieved_m3"
            ),
        }
    if set(by_province) != set(PROVINCES):
        raise ForestryOsecanHandoffWs4Error(
            "WS4 harvest does not cover all 13 jurisdictions: "
            f"{sorted(set(PROVINCES) - set(by_province))}"
        )
    return by_province


def _parse_province_residues(
    basis: str,
    path: Path = WS4_PRODUCT_CHAIN,
) -> tuple[dict[str, ProvinceResidue], dict[str, Any]]:
    rows = _read_rows(
        path,
        [
            "province",
            "geography_level",
            "basis",
            "harvest_m3",
            "scale_factor",
            "process_residue_odt",
            "process_residue_pj",
            "harvest_residue_gross_odt",
            "harvest_residue_gross_pj",
            "harvest_residue_recoverable_r035_odt",
            "harvest_residue_recoverable_r035_pj",
            "harvest_residue_recoverable_r060_odt",
            "harvest_residue_recoverable_r060_pj",
            "harvest_residue_recoverable_r075_odt",
            "harvest_residue_recoverable_r075_pj",
            "model_estimate",
            "observed",
            "policy_grade",
            "national_claim",
            "ws4_derived",
            "official_osecan_changed",
            "chain_v51_unchanged",
        ],
        f"WS4 product chain {basis}",
    )
    selected = [row for row in rows if row["basis"].strip() == basis]
    if not selected:
        raise ForestryOsecanHandoffWs4Error(
            f"WS4 product chain has no rows for basis {basis!r}"
        )
    province_rows = [
        row
        for row in selected
        if row["geography_level"].strip().lower() == "province"
    ]
    national_rows = [
        row
        for row in selected
        if row["geography_level"].strip().lower() == "national"
    ]
    if len(national_rows) != 1 or national_rows[0]["province"].strip().upper() != "CAN":
        raise ForestryOsecanHandoffWs4Error(
            f"WS4 {basis}: expected one CAN national row"
        )
    national_row = national_rows[0]

    by_province: dict[str, ProvinceResidue] = {}
    for row in province_rows:
        province = row["province"].strip().upper()
        if province not in PROVINCES:
            raise ForestryOsecanHandoffWs4Error(
                f"WS4 {basis}: unknown province {province!r}"
            )
        if province in by_province:
            raise ForestryOsecanHandoffWs4Error(
                f"WS4 {basis}: duplicate province {province!r}"
            )
        for field in ("model_estimate", "observed", "policy_grade", "national_claim", "ws4_derived", "official_osecan_changed", "chain_v51_unchanged"):
            expected = field in {"model_estimate", "ws4_derived", "chain_v51_unchanged"}
            actual = _parse_bool(row[field], f"{basis}/{province} {field}")
            if actual != expected:
                raise ForestryOsecanHandoffWs4Error(
                    f"WS4 {basis}/{province}: {field}={actual}; expected {expected}"
                )
        harvest_m3 = _finite_nonnegative(
            row["harvest_m3"], f"{basis}/{province} harvest_m3"
        )
        scale_factor = _finite_nonnegative(
            row["scale_factor"], f"{basis}/{province} scale_factor"
        )
        if scale_factor <= 0.0:
            raise ForestryOsecanHandoffWs4Error(
                f"{basis}/{province}: scale_factor must be positive"
            )
        process_odt = _finite_nonnegative(
            row["process_residue_odt"], f"{basis}/{province} process_odt"
        )
        process_pj = _finite_nonnegative(
            row["process_residue_pj"], f"{basis}/{province} process_pj"
        )
        _require_close(
            process_pj,
            process_odt * LHV_GJ_PER_ODT / PJ_PER_GJ,
            f"{basis}/{province} process PJ arithmetic",
            abs_tol=1e-9,
        )
        gross_odt = _finite_nonnegative(
            row["harvest_residue_gross_odt"],
            f"{basis}/{province} harvest gross odt",
        )
        gross_pj = _finite_nonnegative(
            row["harvest_residue_gross_pj"],
            f"{basis}/{province} harvest gross pj",
        )
        _require_close(
            gross_pj,
            gross_odt * LHV_GJ_PER_ODT / PJ_PER_GJ,
            f"{basis}/{province} harvest gross PJ arithmetic",
            abs_tol=1e-9,
        )
        harvest_odt: dict[str, float] = {}
        harvest_pj: dict[str, float] = {}
        for case, fraction in HARVEST_CASES:
            odt_column, pj_column = HARVEST_COLUMNS[case]
            odt = _finite_nonnegative(
                row[odt_column], f"{basis}/{province}/{case} odt"
            )
            pj = _finite_nonnegative(
                row[pj_column], f"{basis}/{province}/{case} pj"
            )
            _require_close(
                pj,
                odt * LHV_GJ_PER_ODT / PJ_PER_GJ,
                f"{basis}/{province}/{case} PJ arithmetic",
                abs_tol=1e-9,
            )
            if gross_odt > 0.0:
                _require_close(
                    odt / gross_odt,
                    fraction,
                    f"{basis}/{province}/{case} recovery fraction",
                    abs_tol=1e-9,
                )
            elif odt != 0.0:
                raise ForestryOsecanHandoffWs4Error(
                    f"{basis}/{province}/{case}: recoverable residue without gross residue"
                )
            harvest_odt[case] = odt
            harvest_pj[case] = pj
        by_province[province] = ProvinceResidue(
            province=province,
            basis=basis,
            harvest_m3=harvest_m3,
            scale_factor=scale_factor,
            process_odt=process_odt,
            process_pj=process_pj,
            harvest_gross_odt=gross_odt,
            harvest_gross_pj=gross_pj,
            harvest_odt=harvest_odt,
            harvest_pj=harvest_pj,
        )

    if set(by_province) != set(PROVINCES):
        raise ForestryOsecanHandoffWs4Error(
            f"WS4 {basis} does not cover all 13 jurisdictions: "
            f"{sorted(set(PROVINCES) - set(by_province))}"
        )

    national_metrics: dict[str, Any] = {
        "harvest_m3": _finite_nonnegative(
            national_row["harvest_m3"], f"{basis}/CAN harvest_m3"
        ),
        "process_odt": _finite_nonnegative(
            national_row["process_residue_odt"], f"{basis}/CAN process_odt"
        ),
        "process_pj": _finite_nonnegative(
            national_row["process_residue_pj"], f"{basis}/CAN process_pj"
        ),
        "harvest_gross_odt": _finite_nonnegative(
            national_row["harvest_residue_gross_odt"],
            f"{basis}/CAN harvest gross odt",
        ),
        "harvest_gross_pj": _finite_nonnegative(
            national_row["harvest_residue_gross_pj"],
            f"{basis}/CAN harvest gross pj",
        ),
        "harvest_odt": {},
        "harvest_pj": {},
    }
    for case, _fraction in HARVEST_CASES:
        odt_column, pj_column = HARVEST_COLUMNS[case]
        national_metrics["harvest_odt"][case] = _finite_nonnegative(
            national_row[odt_column], f"{basis}/CAN/{case} odt"
        )
        national_metrics["harvest_pj"][case] = _finite_nonnegative(
            national_row[pj_column], f"{basis}/CAN/{case} pj"
        )

    _require_close(
        math.fsum(item.harvest_m3 for item in by_province.values()),
        national_metrics["harvest_m3"],
        f"WS4 {basis} provincial/national harvest",
        abs_tol=ABS_TOL_ODT,
    )
    _require_close(
        math.fsum(item.process_odt for item in by_province.values()),
        national_metrics["process_odt"],
        f"WS4 {basis} provincial/national process odt",
        abs_tol=ABS_TOL_ODT,
    )
    _require_close(
        math.fsum(item.process_pj for item in by_province.values()),
        national_metrics["process_pj"],
        f"WS4 {basis} provincial/national process pj",
    )
    _require_close(
        math.fsum(item.harvest_gross_odt for item in by_province.values()),
        national_metrics["harvest_gross_odt"],
        f"WS4 {basis} provincial/national harvest gross odt",
        abs_tol=ABS_TOL_ODT,
    )
    _require_close(
        math.fsum(item.harvest_gross_pj for item in by_province.values()),
        national_metrics["harvest_gross_pj"],
        f"WS4 {basis} provincial/national harvest gross pj",
    )
    for case, _fraction in HARVEST_CASES:
        _require_close(
            math.fsum(item.harvest_odt[case] for item in by_province.values()),
            national_metrics["harvest_odt"][case],
            f"WS4 {basis} provincial/national {case} odt",
            abs_tol=ABS_TOL_ODT,
        )
        _require_close(
            math.fsum(item.harvest_pj[case] for item in by_province.values()),
            national_metrics["harvest_pj"][case],
            f"WS4 {basis} provincial/national {case} pj",
        )
    return by_province, national_metrics


def _national_cases(
    provinces: Mapping[str, ProvinceResidue],
) -> dict[tuple[str, str], tuple[float, float]]:
    cases: dict[tuple[str, str], tuple[float, float]] = {}
    for case, fraction in PROCESS_CASES:
        cases[("process", case)] = (
            math.fsum(item.process_odt * fraction for item in provinces.values()),
            math.fsum(item.process_pj * fraction for item in provinces.values()),
        )
    for case, _fraction in HARVEST_CASES:
        cases[("harvest", case)] = (
            math.fsum(item.harvest_odt[case] for item in provinces.values()),
            math.fsum(item.harvest_pj[case] for item in provinces.values()),
        )
    return cases


def _province_case(
    item: ProvinceResidue, stream: str, case: str
) -> tuple[float, float]:
    if stream == "process":
        fraction = CASE_FRACTIONS[case]
        return item.process_odt * fraction, item.process_pj * fraction
    if stream == "harvest":
        return item.harvest_odt[case], item.harvest_pj[case]
    raise ForestryOsecanHandoffWs4Error(f"unknown stream: {stream!r}")


def _zone_metadata(
    weights_by_province: Mapping[str, Sequence[ZoneWeight]],
) -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for zone_id in ZONE_ORDER:
        contributing = [
            province
            for province in PROVINCES
            if any(item.zone_id == zone_id for item in weights_by_province[province])
        ]
        if not contributing:
            raise ForestryOsecanHandoffWs4Error(
                f"zone {zone_id} has no contributing province"
            )
        methods = sorted(
            {
                item.weight_method
                for province in contributing
                for item in weights_by_province[province]
                if item.zone_id == zone_id
            }
        )
        evidence = sorted(
            {
                item.evidence_tier
                for province in contributing
                for item in weights_by_province[province]
                if item.zone_id == zone_id
            }
        )
        proxy = any(
            item.proxy_share
            for province in contributing
            for item in weights_by_province[province]
            if item.zone_id == zone_id
        )
        metadata[zone_id] = {
            "zone_name": weights_by_province[contributing[0]][0].zone_name
            if len(weights_by_province[contributing[0]]) == 1
            else next(
                item.zone_name
                for item in weights_by_province[contributing[0]]
                if item.zone_id == zone_id
            ),
            "provinces": tuple(contributing),
            "weight_methods": tuple(methods),
            "evidence_tiers": tuple(evidence),
            "proxy_share": proxy,
            "allocation_status": (
                "proxy_share" if proxy else "single_zone_crosswalk"
            ),
            "zone_mapping_status": (
                "allocated_origin_province"
                if any(
                    province in MULTI_ZONE_PROVINCES for province in contributing
                )
                else "mapped_single_zone_origin_province"
            ),
        }
    if set(metadata) != set(ZONE_ORDER):
        raise ForestryOsecanHandoffWs4Error("zone metadata is incomplete")
    return metadata


def _allocate_zones(
    provinces_by_basis: Mapping[str, Mapping[str, ProvinceResidue]],
    weights_by_province: Mapping[str, Sequence[ZoneWeight]],
    metadata: Mapping[str, Mapping[str, Any]],
) -> tuple[list[ZoneAllocation], dict[str, float]]:
    allocations: list[ZoneAllocation] = []
    max_province_zone_gap = 0.0
    for basis in BASES:
        basis_provinces = provinces_by_basis.get(basis, {})
        if len(basis_provinces) != len(PROVINCES):
            raise ForestryOsecanHandoffWs4Error(
                f"basis {basis} does not have 13 province rows"
            )
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, fraction in cases:
                national_odt = math.fsum(
                    _province_case(item, stream, case)[0]
                    for item in basis_provinces.values()
                )
                national_pj = math.fsum(
                    _province_case(item, stream, case)[1]
                    for item in basis_provinces.values()
                )
                for zone_id in ZONE_ORDER:
                    zone_odt = 0.0
                    zone_pj = 0.0
                    for province in PROVINCES:
                        item = basis_provinces[province]
                        province_odt, province_pj = _province_case(
                            item, stream, case
                        )
                        for weight in weights_by_province[province]:
                            if weight.zone_id != zone_id:
                                continue
                            zone_odt += province_odt * weight.weight
                            zone_pj += province_pj * weight.weight
                    allocations.append(
                        ZoneAllocation(
                            basis=basis,
                            zone_id=zone_id,
                            zone_name=metadata[zone_id]["zone_name"],
                            stream=stream,
                            availability_case=case,
                            availability_fraction=fraction,
                            odt=zone_odt,
                            pj=zone_pj,
                            national_odt=national_odt,
                            national_pj=national_pj,
                            provinces=metadata[zone_id]["provinces"],
                        )
                    )

        # Every province's zone weights must reproduce the province total
        # exactly; this is the strongest conservation guard.
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                by_zone: dict[str, float] = {zone_id: 0.0 for zone_id in ZONE_ORDER}
                for allocation in allocations:
                    if (
                        allocation.basis == basis
                        and allocation.stream == stream
                        and allocation.availability_case == case
                    ):
                        by_zone[allocation.zone_id] += allocation.pj
                for province in PROVINCES:
                    item = basis_provinces[province]
                    province_pj = _province_case(item, stream, case)[1]
                    allocated = math.fsum(
                        province_pj * weight.weight
                        for weight in weights_by_province[province]
                    )
                    max_province_zone_gap = max(
                        max_province_zone_gap, abs(allocated - province_pj)
                    )
                _require_close(
                    math.fsum(by_zone.values()),
                    math.fsum(
                        _province_case(item, stream, case)[1]
                        for item in basis_provinces.values()
                    ),
                    f"{basis}/{stream}/{case} zone total conservation",
                )
    return allocations, {
        "max_province_zone_gap_pj": max_province_zone_gap,
    }


def _tier_for_pj(pj: float, tiers: Mapping[str, Mapping[str, Any]]) -> str:
    for tier_id in ("T1", "T2", "T3", "T4"):
        if pj <= float(tiers[tier_id]["upper_pj"]) + 1e-9:
            return tier_id
    return "above_T4"


def _load_tiers() -> dict[str, dict[str, Any]]:
    summary = _load_json(V10_SUMMARY, "handoff v10 summary")
    v10_tiers = summary.get("supply_curve", {}).get("tiers", {})
    if set(v10_tiers) != {"T1", "T2", "T3", "T4"}:
        raise ForestryOsecanHandoffWs4Error(
            f"handoff v10 does not carry four tiers: {sorted(v10_tiers)}"
        )
    rows = _read_rows(
        OSECAN_TIER_DEFINITIONS,
        [
            "tier",
            "technology",
            "lower_pj",
            "upper_pj",
            "incremental_cap_pj",
        ],
        "OSeCAN tier definitions",
    )
    official: dict[str, dict[str, Any]] = {}
    for row in rows:
        tier_id = row["tier"].strip().upper()
        if tier_id in official:
            raise ForestryOsecanHandoffWs4Error(
                f"duplicate official tier: {tier_id!r}"
            )
        lower = _finite_nonnegative(row["lower_pj"], f"{tier_id} lower_pj")
        upper = _finite_nonnegative(row["upper_pj"], f"{tier_id} upper_pj")
        incremental = _finite_nonnegative(
            row["incremental_cap_pj"], f"{tier_id} incremental_cap_pj"
        )
        if upper <= lower:
            raise ForestryOsecanHandoffWs4Error(
                f"{tier_id} upper_pj is not above lower_pj"
            )
        _require_close(
            upper - lower,
            incremental,
            f"{tier_id} interval width",
            abs_tol=1e-9,
        )
        official[tier_id] = {
            "technology": row["technology"].strip(),
            "lower_pj": lower,
            "upper_pj": upper,
            "incremental_cap_pj": incremental,
        }
    if set(official) != {"T1", "T2", "T3", "T4"}:
        raise ForestryOsecanHandoffWs4Error(
            f"official tier set is not T1-T4: {sorted(official)}"
        )
    ordered = [official[tier_id] for tier_id in ("T1", "T2", "T3", "T4")]
    _require_close(ordered[0]["lower_pj"], 0.0, "T1 lower bound", abs_tol=1e-12)
    for previous, current in itertools.pairwise(ordered):
        _require_close(
            previous["upper_pj"],
            current["lower_pj"],
            "tier boundary",
            abs_tol=1e-9,
        )
    _require_close(
        ordered[-1]["upper_pj"],
        EXPECTED_TIER_UPPER_PJ,
        "T4 upper bound",
        abs_tol=1e-6,
    )
    for tier_id in ("T1", "T2", "T3", "T4"):
        for field in ("upper_pj", "lower_pj", "incremental_cap_pj"):
            _require_close(
                float(v10_tiers[tier_id][field]),
                float(official[tier_id][field]),
                f"handoff v10 {tier_id} {field}",
                abs_tol=1e-9,
            )
    return official


def _load_v10_tier_rows() -> list[dict[str, str]]:
    rows = _read_rows(
        V10_TIER_POSITIONS,
        ["scenario", "stream", "case", "national_pj", "tier"],
        "handoff v10 case tier positions",
    )
    if len(rows) != 21:
        raise ForestryOsecanHandoffWs4Error(
            f"handoff v10 must carry 21 tier positions; found {len(rows)}"
        )
    keys = {
        (row["scenario"].strip(), row["stream"].strip(), row["case"].strip())
        for row in rows
    }
    if keys != set(FROZEN_V51_TIERS):
        raise ForestryOsecanHandoffWs4Error(
            "handoff v10 tier positions do not match the frozen v5.1 key set"
        )
    return rows


def _validate_v10_against_frozen(
    rows: Sequence[Mapping[str, str]],
    tiers: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    differences: list[dict[str, Any]] = []
    for row in rows:
        key = (
            row["scenario"].strip(),
            row["stream"].strip(),
            row["case"].strip(),
        )
        pj = _finite_nonnegative(row["national_pj"], f"v10 {key} national_pj")
        tier = row["tier"].strip()
        expected = FROZEN_V51_TIERS[key]
        recomputed = _tier_for_pj(pj, tiers)
        if tier != expected or recomputed != expected:
            differences.append(
                {
                    "scenario": key[0],
                    "stream": key[1],
                    "case": key[2],
                    "national_pj": pj,
                    "v10_tier": tier,
                    "recomputed_tier": recomputed,
                    "frozen_v5_1_tier": expected,
                }
            )
    return {
        "positions": len(rows),
        "matches": len(rows) - len(differences),
        "differences": differences,
    }


def _load_v9_case_totals() -> dict[str, dict[str, dict[str, float]]]:
    summary = _load_json(CURVE_V9_SUMMARY, "residue curve v9 summary")
    totals = summary.get("scenario_totals", {})
    if not isinstance(totals, dict):
        raise ForestryOsecanHandoffWs4Error(
            "residue curve v9 summary lacks scenario_totals"
        )
    required = {
        "100m": {"process": {case for case, _ in PROCESS_CASES}, "harvest": {case for case, _ in HARVEST_CASES}},
        "119m": {"process": {case for case, _ in PROCESS_CASES}, "harvest": {case for case, _ in HARVEST_CASES}},
        "150m": {"process": {case for case, _ in PROCESS_CASES}, "harvest": {case for case, _ in HARVEST_CASES}},
    }
    parsed: dict[str, dict[str, dict[str, float]]] = {}
    for scenario, streams in required.items():
        if scenario not in totals:
            raise ForestryOsecanHandoffWs4Error(
                f"residue curve v9 summary lacks scenario {scenario!r}"
            )
        parsed[scenario] = {}
        for stream, cases in streams.items():
            if stream not in totals[scenario]:
                raise ForestryOsecanHandoffWs4Error(
                    f"residue curve v9 summary lacks {scenario}/{stream}"
                )
            parsed[scenario][stream] = {}
            for case in cases:
                if case not in totals[scenario][stream]:
                    raise ForestryOsecanHandoffWs4Error(
                        f"residue curve v9 summary lacks {scenario}/{stream}/{case}"
                    )
                parsed[scenario][stream][case] = _finite_nonnegative(
                    totals[scenario][stream][case],
                    f"curve v9 {scenario}/{stream}/{case}",
                )
    return parsed


def _validate_v10_totals_against_v9(
    rows: Sequence[Mapping[str, str]],
    v9_totals: Mapping[str, Mapping[str, Mapping[str, float]]],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for row in rows:
        scenario = row["scenario"].strip()
        stream = row["stream"].strip()
        case = row["case"].strip()
        pj = _finite_nonnegative(row["national_pj"], f"v10 {scenario}/{stream}/{case}")
        expected = v9_totals[scenario][stream][case]
        _require_close(
            pj,
            expected,
            f"v10 vs curve v9 {scenario}/{stream}/{case}",
            abs_tol=1e-6,
        )
        checks.append(
            {
                "scenario": scenario,
                "stream": stream,
                "case": case,
                "v10_national_pj": pj,
                "curve_v9_national_pj": expected,
            }
        )
    return checks


def _tier_comparison_rows(
    v10_rows: Sequence[Mapping[str, str]],
    tiers: Mapping[str, Mapping[str, Any]],
    national_cases: Mapping[
        str, Mapping[tuple[str, str], tuple[float, float]]
    ],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ws4_tiers: dict[tuple[str, str, str], str] = {}
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                _odt, pj = national_cases[basis][(stream, case)]
                ws4_tiers[(basis, stream, case)] = _tier_for_pj(pj, tiers)

    rows: list[dict[str, Any]] = []
    primary_matches = 0
    secondary_matches = 0
    compared = 0
    for v10 in v10_rows:
        scenario = v10["scenario"].strip()
        stream = v10["stream"].strip()
        case = v10["case"].strip()
        v10_pj = _finite_nonnegative(
            v10["national_pj"], f"v10 {scenario}/{stream}/{case}"
        )
        v10_tier = v10["tier"].strip()
        frozen_tier = FROZEN_V51_TIERS[(scenario, stream, case)]
        row: dict[str, Any] = {
            "scenario": scenario,
            "stream": stream,
            "case": case,
            "frozen_v5_1_tier": frozen_tier,
            "v10_national_pj": v10_pj,
            "v10_tier": v10_tier,
            "v10_tier_matches_frozen": v10_tier == frozen_tier,
            "ws4_basis_primary": PRIMARY_BASIS if scenario == SCENARIO else "not_available",
            "ws4_national_pj_primary": "not_available",
            "ws4_tier_primary": "not_available",
            "ws4_minus_v10_pj_primary": "not_available",
            "ws4_abs_delta_pj_primary": "not_available",
            "ws4_tier_changed_primary": "not_available",
            "ws4_basis_secondary": SECONDARY_BASIS if scenario == SCENARIO else "not_available",
            "ws4_national_pj_secondary": "not_available",
            "ws4_tier_secondary": "not_available",
            "ws4_minus_v10_pj_secondary": "not_available",
            "ws4_abs_delta_pj_secondary": "not_available",
            "ws4_tier_changed_secondary": "not_available",
            "comparison_status": (
                "compared_national_totals_origin_province"
                if scenario == SCENARIO
                else "not_available_ws4_chain_119m_only"
            ),
            "v10_province_basis": "facility_province",
            "ws4_province_basis": "origin_province",
            "model_estimate": "true",
            "observed": "false",
            "policy_grade": "false",
            "national_claim": "false",
            "ws4_derived": "true",
            "official_osecan_changed": "false",
            "chain_v51_unchanged": "true",
        }
        if scenario == SCENARIO:
            compared += 1
            for basis, suffix, counter in (
                (PRIMARY_BASIS, "primary", "primary"),
                (SECONDARY_BASIS, "secondary", "secondary"),
            ):
                _odt, ws4_pj = national_cases[basis][(stream, case)]
                ws4_tier = ws4_tiers[(basis, stream, case)]
                delta = ws4_pj - v10_pj
                row[f"ws4_national_pj_{suffix}"] = ws4_pj
                row[f"ws4_tier_{suffix}"] = ws4_tier
                row[f"ws4_minus_v10_pj_{suffix}"] = delta
                row[f"ws4_abs_delta_pj_{suffix}"] = abs(delta)
                row[f"ws4_tier_changed_{suffix}"] = ws4_tier != v10_tier
                if ws4_tier == v10_tier:
                    if counter == "primary":
                        primary_matches += 1
                    else:
                        secondary_matches += 1
        rows.append(row)
    return rows, {
        "frozen_v5_1_positions": len(v10_rows),
        "v10_positions_matching_frozen_v5_1": sum(
            1 for row in rows if row["v10_tier_matches_frozen"]
        ),
        "ws4_positions_available": compared,
        "ws4_primary_tier_matches": primary_matches,
        "ws4_secondary_tier_matches": secondary_matches,
        "ws4_primary_tier_mismatches": compared - primary_matches,
        "ws4_secondary_tier_mismatches": compared - secondary_matches,
        "note": (
            "Frozen v5.1 has 21 tier positions across 100/119/150 Mm3. The "
            "WS4 chain is the 119 Mm3 control only, so only its 7 positions "
            "per basis are like-for-like; the 100/150 reference positions are "
            "reported as not available, not fabricated."
        ),
    }


def _zone_row(
    allocation: ZoneAllocation,
    metadata: Mapping[str, Mapping[str, Any]],
    *,
    case_tier: str,
) -> dict[str, Any]:
    zone_meta = metadata[allocation.zone_id]
    return {
        "basis": allocation.basis,
        "scenario": SCENARIO,
        "zone_id": allocation.zone_id,
        "zone_name": allocation.zone_name,
        "zone_mapping_status": zone_meta["zone_mapping_status"],
        "allocation_status": zone_meta["allocation_status"],
        "zone_weight_methods": "|".join(zone_meta["weight_methods"]),
        "zone_evidence_tiers": "|".join(zone_meta["evidence_tiers"]),
        "proxy_share": "true" if zone_meta["proxy_share"] else "false",
        "provinces_included": "|".join(allocation.provinces),
        "province_count": len(allocation.provinces),
        "province_basis": "origin_province",
        "stream": allocation.stream,
        "availability_case": allocation.availability_case,
        "availability_fraction": allocation.availability_fraction,
        "residue_odt": allocation.odt,
        "pj": allocation.pj,
        "national_case_odt": allocation.national_odt,
        "national_case_pj": allocation.national_pj,
        "share_of_national_case": (
            allocation.pj / allocation.national_pj
            if allocation.national_pj > 0.0
            else 0.0
        ),
        "supply_curve_tier_at_national_case": case_tier,
        "model_estimate": "true",
        "observed": "false",
        "policy_grade": "false",
        "national_claim": "false",
        "ws4_derived": "true",
        "official_osecan_changed": "false",
        "chain_v51_unchanged": "true",
        "caveat": (
            "WS4 chain is origin-province based; handoff v10 process residue "
            "is facility-province based. This zone row is a screening "
            "origin-province allocation using the frozen v10 weights, not an "
            "observed sub-provincial share and not a v10 zone-row comparison."
        ),
    }


def _write_csv(
    path: Path,
    fields: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows:
        raise ForestryOsecanHandoffWs4Error(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for field in fields:
                value = row.get(field)
                if value is None:
                    rendered[field] = ""
                elif isinstance(value, bool):
                    rendered[field] = "true" if value else "false"
                elif isinstance(value, float):
                    if not math.isfinite(value):
                        raise ForestryOsecanHandoffWs4Error(
                            f"refusing to write non-finite {field}: {value!r}"
                        )
                    rendered[field] = f"{value:.15g}"
                else:
                    rendered[field] = value
            writer.writerow(rendered)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _check(
    checks: list[dict[str, Any]], name: str, passed: bool, detail: str
) -> None:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def _validate_outputs(
    zone_rows: Sequence[Mapping[str, Any]],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    national_cases: Mapping[
        str, Mapping[tuple[str, str], tuple[float, float]]
    ],
    zone_summary: Mapping[str, Any],
    v10_check: Mapping[str, Any],
    tier_check: Mapping[str, Any],
    osecan_before: str,
    osecan_after: str,
    osecan_changed_files: Sequence[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _check(
        checks,
        "official OSeCAN tree hash unchanged",
        osecan_before == osecan_after
        and not osecan_changed_files
        and osecan_before == EXPECTED_OSECAN_TREE_SHA256,
        (
            f"before={osecan_before[:16]}... after={osecan_after[:16]}... "
            f"changed_files={len(osecan_changed_files)}"
        ),
    )
    _check(
        checks,
        "zone weights sum to one per province",
        zone_summary["province_weight_sum_min"] >= 1.0 - ABS_TOL_WEIGHT
        and zone_summary["province_weight_sum_max"] <= 1.0 + ABS_TOL_WEIGHT,
        (
            f"min={zone_summary['province_weight_sum_min']:.15g} "
            f"max={zone_summary['province_weight_sum_max']:.15g}"
        ),
    )
    _check(
        checks,
        "13 official zones and no fabricated zones",
        zone_summary["zone_count"] == 13
        and set(zone_summary["zone_ids"]) == set(ZONE_ORDER),
        f"zones={zone_summary['zone_count']}",
    )
    conservation_ok = zone_summary["max_zone_national_gap_pj"] <= ABS_TOL_PJ
    _check(
        checks,
        "zone totals conserve national totals",
        conservation_ok,
        (
            "max PJ gap="
            f"{zone_summary['max_zone_national_gap_pj']:.12g}; "
            "max ODT gap="
            f"{zone_summary['max_zone_national_gap_odt']:.12g}"
        ),
    )
    _check(
        checks,
        "province zone allocations conserve province totals",
        zone_summary["max_province_zone_gap_pj"] <= ABS_TOL_PJ,
        f"max PJ gap={zone_summary['max_province_zone_gap_pj']:.12g}",
    )
    _check(
        checks,
        "v10 reference 21/21 tier positions match frozen v5.1",
        v10_check["matches"] == 21 and v10_check["positions"] == 21,
        f"matches={v10_check['matches']}/{v10_check['positions']}",
    )
    _check(
        checks,
        "WS4 119m primary tier positions match v10",
        tier_check["ws4_primary_tier_matches"]
        == tier_check["ws4_positions_available"],
        (
            f"matches={tier_check['ws4_primary_tier_matches']}/"
            f"{tier_check['ws4_positions_available']}"
        ),
    )
    _check(
        checks,
        "WS4 119m achieved tier positions match v10",
        tier_check["ws4_secondary_tier_matches"]
        == tier_check["ws4_positions_available"],
        (
            f"matches={tier_check['ws4_secondary_tier_matches']}/"
            f"{tier_check['ws4_positions_available']}"
        ),
    )
    label_ok = True
    label_detail = ""
    for name, rows in (
        ("zone rows", zone_rows),
        ("tier rows", tier_rows),
        ("tier comparison rows", comparison_rows),
    ):
        for row in rows:
            for field, expected in ROW_LABELS.items():
                if str(row.get(field, "")).strip().lower() != expected:
                    label_ok = False
                    label_detail = f"{name}: {field}={row.get(field)!r}"
                    break
            if not label_ok:
                break
        if not label_ok:
            break
    _check(
        checks,
        "row labels",
        label_ok,
        label_detail
        or "model_estimate=true, observed=false, policy_grade=false, "
        "national_claim=false, ws4_derived=true, "
        "official_osecan_changed=false, chain_v51_unchanged=true",
    )
    finite_ok = True
    finite_detail = ""
    for name, rows in (
        ("zone rows", zone_rows),
        ("tier rows", tier_rows),
        ("tier comparison rows", comparison_rows),
    ):
        for row in rows:
            for field, value in row.items():
                if not isinstance(value, float):
                    continue
                if not math.isfinite(value):
                    finite_ok = False
                    finite_detail = f"{name}: non-finite {field}={value!r}"
                    break
                if field.startswith("ws4_minus_v10_pj"):
                    continue
                if value < 0.0:
                    finite_ok = False
                    finite_detail = f"{name}: negative {field}={value!r}"
                    break
            if not finite_ok:
                break
        if not finite_ok:
            break
    _check(checks, "no NaN or negative residue values", finite_ok, finite_detail or "all residue values finite and non-negative")
    _check(
        checks,
        "zone rows complete for each basis/stream/case",
        zone_summary["zone_rows_expected"] == len(zone_rows)
        and zone_summary["zone_rows_per_case"] == 13,
        (
            f"rows={len(zone_rows)} expected={zone_summary['zone_rows_expected']} "
            f"per_case={zone_summary['zone_rows_per_case']}"
        ),
    )
    base_ok = True
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                national = national_cases[basis][(stream, case)]
                if national[0] < 0.0 or national[1] < 0.0:
                    base_ok = False
    _check(checks, "national case totals non-negative", base_ok, "all WS4 case totals non-negative")
    return checks


def _report(
    summary: Mapping[str, Any],
    zone_rows: Sequence[Mapping[str, Any]],
    tier_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# WS4-driven Forestry Residue Handoff to OSeCAN",
        "",
        f"**Artifact:** `{ARTIFACT}` **Version:** `{ARTIFACT_VERSION}`",
        "",
        (
            f"**Status:** `{summary['status']}`. Read-only screening artifact. "
            "No official OSeCAN file, UI, candidate activity limit, or frozen "
            "v5.1 artifact is changed."
        ),
        "",
        "## 1. Basis and boundary",
        "",
        (
            "The WS4 chain applies the frozen chain-v5.1 per-province "
            "product/residue coefficients to the realised 119 Mm3 WS4 "
            "harvest. The chain and this handoff are **origin-province** "
            "based. Handoff v10 process residue is **facility-province** "
            "based. The two zone rows are therefore not compared directly; "
            "the comparison is on national totals and tier positions only."
        ),
        "",
        (
            f"Primary basis: `{PRIMARY_BASIS}` (control-total basis, "
            f"{CONTROL_TOTAL_M3:,.1f} m3). Secondary basis: "
            f"`{SECONDARY_BASIS}` (realised WS4 harvest). Both are reported."
        ),
        "",
        (
            "Labels on every row: `model_estimate=true`, `observed=false`, "
            "`policy_grade=false`, `national_claim=false`, "
            "`ws4_derived=true`, `official_osecan_changed=false`, "
            "`chain_v51_unchanged=true`."
        ),
        "",
        "## 2. WS4 control total",
        "",
        "| Item | Value |",
        "|---|---:|",
        f"| Provincial control total (m3) | {summary['ws4_control']['control_total_m3']:,.1f} |",
        f"| WS4 achieved harvest (m3) | {summary['ws4_control']['achieved_m3']:,.3f} |",
        f"| Overshoot (m3) | {summary['ws4_control']['overshoot_m3']:,.3f} |",
        f"| Treated area (ha) | {summary['ws4_control']['area_ha']:,.2f} |",
        "",
        "## 3. National case totals",
        "",
        "| Basis | Stream | Case | PJ |",
        "|---|---|---|---:|",
    ]
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                value = summary["national_totals"][basis][stream][case]["pj"]
                lines.append(f"| `{basis}` | {stream} | `{case}` | {value:,.6f} |")
    lines.extend(
        [
            "",
            "## 4. Zone totals (primary basis)",
            "",
            "| Zone | Process 0% | Process 25% | Process 50% | Process 75% | Harvest 35% | Harvest 60% | Harvest 75% |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for zone_id in ZONE_ORDER:
        values: list[str] = []
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                match = next(
                    row
                    for row in zone_rows
                    if row["basis"] == PRIMARY_BASIS
                    and row["zone_id"] == zone_id
                    and row["stream"] == stream
                    and row["availability_case"] == case
                )
                values.append(f"{float(match['pj']):,.6f}")
        lines.append(f"| {zone_id} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            (
                "Zone rows conserve the national totals for every "
                "basis/stream/case; each province's 13-zone weights sum to one."
            ),
            "",
            "## 5. Tier comparison with frozen v5.1",
            "",
            (
                "Frozen v5.1 carries 21 scenario/stream/case tier positions. "
                f"Handoff v10 matches all {summary['tier_comparison']['v10_positions_matching_frozen_v5_1']}/21 "
                "positions. The WS4 chain covers the 119 Mm3 control only, so "
                "only its 7 positions per basis are like-for-like; the 100/150 "
                "Mm3 positions are reported as not available rather than "
                "fabricated."
            ),
            "",
            "| Basis | Stream | Case | National PJ | Tier | v10 119m PJ | Delta PJ | Changed |",
            "|---|---|---|---:|---|---:|---:|---|",
        ]
    )
    for row in tier_rows:
        lines.append(
            f"| `{row['basis']}` | {row['stream']} | `{row['case']}` | "
            f"{float(row['national_pj']):,.6f} | {row['tier']} | "
            f"{float(row['v10_119_national_pj']):,.6f} | "
            f"{float(row['ws4_minus_v10_pj']):+.9f} | {row['tier_changed']} |"
        )
    lines.extend(
        [
            "",
            "### Reference tier positions",
            "",
            "| Scenario | Stream | Case | v10 PJ | v10 tier | Frozen v5.1 | Match |",
            "|---|---|---|---:|---|---|---|",
        ]
    )
    for row in comparison_rows:
        lines.append(
            f"| {row['scenario']} | {row['stream']} | `{row['case']}` | "
            f"{float(row['v10_national_pj']):,.6f} | {row['v10_tier']} | "
            f"{row['frozen_v5_1_tier']} | {row['v10_tier_matches_frozen']} |"
        )
    lines.extend(
        [
            "",
            "## 6. Official OSeCAN integrity",
            "",
            (
                f"Official OSeCAN tree: {summary['osecan_integrity']['file_count_before']} "
                "files; tree SHA-256 "
                f"`{summary['osecan_integrity']['tree_sha256_before']}`; "
                f"changed files: {len(summary['osecan_integrity']['changed_files'])}. "
                "No candidate activity limit was written to OSeCAN."
            ),
            "",
            f"No unexpected `D`: {summary['d_status']['no_unexpected_deletions']}.",
            "",
            "## 7. Caveats",
            "",
        ]
    )
    for caveat in summary["caveats"]:
        lines.append(f"- {caveat}")
    lines.extend(
        [
            "",
            "## 8. Reproduce",
            "",
            "```bash",
            f".venv/bin/python -m {MODULE_PATH}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _summary_outputs(target: Path) -> dict[str, dict[str, Any]]:
    return {
        path.name: {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(target.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the WS4-driven handoff and return its summary."""
    target = (
        Path(output_dir) if output_dir is not None else OUTPUT_DIR
    ).resolve()
    if OSECAN_DIR in target.parents or target == OSECAN_DIR:
        raise ForestryOsecanHandoffWs4Error(
            "refusing to write inside the official OSeCAN tree"
        )
    for frozen_dir in (HANDOFF_V10_DIR, CURVE_V9_DIR, WS4_DIR):
        resolved_frozen = frozen_dir.resolve()
        if target == resolved_frozen or resolved_frozen in target.parents:
            raise ForestryOsecanHandoffWs4Error(
                f"refusing to write inside a frozen input tree: {frozen_dir}"
            )
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    osecan_before_state = _tree_state(OSECAN_DIR)
    if osecan_before_state["file_count"] != EXPECTED_OSECAN_FILE_COUNT:
        raise ForestryOsecanHandoffWs4Error(
            "official OSeCAN file count changed: "
            f"{osecan_before_state['file_count']} != {EXPECTED_OSECAN_FILE_COUNT}"
        )
    if osecan_before_state["tree_sha256"] != EXPECTED_OSECAN_TREE_SHA256:
        raise ForestryOsecanHandoffWs4Error(
            "official OSeCAN tree hash changed: "
            f"{osecan_before_state['tree_sha256']}"
        )
    frozen_before = {
        "handoff_v10": _tree_state(HANDOFF_V10_DIR),
        "curve_v9": _tree_state(CURVE_V9_DIR),
        "ws4_chain": _tree_state(WS4_DIR),
    }

    crosswalk = _load_crosswalk()
    weights_by_province, flat_weights = _load_zone_weights(crosswalk)
    harvest_schedule = _load_harvest_schedule()
    province_residues_by_basis: dict[str, dict[str, ProvinceResidue]] = {}
    national_residue_rows: dict[str, dict[str, Any]] = {}
    for basis in BASES:
        parsed, national = _parse_province_residues(basis)
        province_residues_by_basis[basis] = parsed
        national_residue_rows[basis] = national

    # Cross-check the WS4 harvest schedule against the chain.  The realised
    # basis applies a unit factor; the control basis applies the one national
    # scaling factor written by the WS4 chain.
    for basis in BASES:
        factors = {
            item.scale_factor
            for item in province_residues_by_basis[basis].values()
        }
        if len(factors) != 1:
            raise ForestryOsecanHandoffWs4Error(
                f"{basis}: expected one national WS4 scale factor, got {sorted(factors)}"
            )
        factor = factors.pop()
        if basis == SECONDARY_BASIS:
            _require_close(factor, 1.0, "ws4_achieved scale factor", abs_tol=1e-12)
        for province in PROVINCES:
            _require_close(
                province_residues_by_basis[basis][province].harvest_m3,
                harvest_schedule[province]["achieved_m3"] * factor,
                f"{basis}/{province} harvest schedule reconciliation",
                abs_tol=ABS_TOL_ODT,
            )
    scaled_total = math.fsum(
        item.harvest_m3
        for item in province_residues_by_basis[PRIMARY_BASIS].values()
    )
    achieved_total = math.fsum(
        item.harvest_m3
        for item in province_residues_by_basis[SECONDARY_BASIS].values()
    )
    _require_close(
        scaled_total,
        CONTROL_TOTAL_M3,
        "scaled WS4 harvest control total",
        abs_tol=ABS_TOL_ODT,
    )
    _require_close(
        achieved_total,
        math.fsum(item["achieved_m3"] for item in harvest_schedule.values()),
        "achieved WS4 harvest total",
        abs_tol=ABS_TOL_ODT,
    )

    national_cases: dict[str, dict[tuple[str, str], tuple[float, float]]] = {}
    for basis in BASES:
        basis_provinces = province_residues_by_basis[basis]
        national_cases[basis] = _national_cases(basis_provinces)
        for (stream, case), (odt, pj) in national_cases[basis].items():
            expected = national_residue_rows[basis]
            if stream == "process":
                expected_odt = expected["process_odt"] * CASE_FRACTIONS[case]
                expected_pj = expected["process_pj"] * CASE_FRACTIONS[case]
            else:
                expected_odt = expected["harvest_odt"][case]
                expected_pj = expected["harvest_pj"][case]
            _require_close(odt, expected_odt, f"{basis}/{stream}/{case} national odt")
            _require_close(pj, expected_pj, f"{basis}/{stream}/{case} national pj")

    tiers = _load_tiers()
    v10_rows = _load_v10_tier_rows()
    v10_tiers_check = _validate_v10_against_frozen(v10_rows, tiers)
    v9_totals = _load_v9_case_totals()
    _validate_v10_totals_against_v9(v10_rows, v9_totals)

    metadata = _zone_metadata(weights_by_province)
    allocations, allocation_check = _allocate_zones(
        province_residues_by_basis, weights_by_province, metadata
    )

    tier_rows: list[dict[str, Any]] = []
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                national_pj = national_cases[basis][(stream, case)][1]
                v10_row = next(
                    row
                    for row in v10_rows
                    if row["scenario"] == SCENARIO
                    and row["stream"] == stream
                    and row["case"] == case
                )
                v10_pj = float(v10_row["national_pj"])
                v10_tier = v10_row["tier"]
                tier = _tier_for_pj(national_pj, tiers)
                tier_rows.append(
                    {
                        "basis": basis,
                        "scenario": SCENARIO,
                        "stream": stream,
                        "case": case,
                        "province_basis": "origin_province",
                        "national_odt": national_cases[basis][(stream, case)][0],
                        "national_pj": national_pj,
                        "tier": tier,
                        "v10_119_national_pj": v10_pj,
                        "v10_119_tier": v10_tier,
                        "ws4_minus_v10_pj": national_pj - v10_pj,
                        "ws4_abs_delta_pj": abs(national_pj - v10_pj),
                        "tier_changed": tier != v10_tier,
                        "model_estimate": "true",
                        "observed": "false",
                        "policy_grade": "false",
                        "national_claim": "false",
                        "ws4_derived": "true",
                        "official_osecan_changed": "false",
                        "chain_v51_unchanged": "true",
                    }
                )

    comparison_rows, tier_check = _tier_comparison_rows(
        v10_rows, tiers, national_cases
    )

    # Zone row ordering follows the official 13-zone order, then basis and
    # stream/case order.
    zone_rows: list[dict[str, Any]] = []
    for basis in BASES:
        for allocation in sorted(
            allocations,
            key=lambda item: (
                BASES.index(item.basis),
                0 if item.stream == "process" else 1,
                CASE_FRACTIONS[item.availability_case],
                ZONE_ORDER.index(item.zone_id),
            ),
        ):
            if allocation.basis != basis:
                continue
            case_tier = _tier_for_pj(allocation.national_pj, tiers)
            zone_rows.append(_zone_row(allocation, metadata, case_tier=case_tier))

    max_zone_national_gap_pj = 0.0
    max_zone_national_gap_odt = 0.0
    for basis in BASES:
        for stream, cases in (
            ("process", PROCESS_CASES),
            ("harvest", HARVEST_CASES),
        ):
            for case, _fraction in cases:
                selected = [
                    allocation
                    for allocation in allocations
                    if allocation.basis == basis
                    and allocation.stream == stream
                    and allocation.availability_case == case
                ]
                if len(selected) != len(ZONE_ORDER):
                    raise ForestryOsecanHandoffWs4Error(
                        f"{basis}/{stream}/{case}: expected 13 zone rows, "
                        f"found {len(selected)}"
                    )
                max_zone_national_gap_pj = max(
                    max_zone_national_gap_pj,
                    abs(
                        math.fsum(item.pj for item in selected)
                        - selected[0].national_pj
                    ),
                )
                max_zone_national_gap_odt = max(
                    max_zone_national_gap_odt,
                    abs(
                        math.fsum(item.odt for item in selected)
                        - selected[0].national_odt
                    ),
                )
    zone_summary = {
        "zone_ids": list(ZONE_ORDER),
        "zone_count": len(ZONE_ORDER),
        "province_count": len(PROVINCES),
        "province_weight_sum_min": min(
            math.fsum(item.weight for item in items)
            for items in weights_by_province.values()
        ),
        "province_weight_sum_max": max(
            math.fsum(item.weight for item in items)
            for items in weights_by_province.values()
        ),
        "global_weight_sum": math.fsum(item.weight for item in flat_weights),
        "multi_zone_provinces": list(MULTI_ZONE_PROVINCES),
        "zone_rows_expected": len(BASES) * len(ZONE_ORDER) * (
            len(PROCESS_CASES) + len(HARVEST_CASES)
        ),
        "zone_rows_per_case": len(ZONE_ORDER),
        "max_zone_national_gap_pj": max_zone_national_gap_pj,
        "max_zone_national_gap_odt": max_zone_national_gap_odt,
        "max_province_zone_gap_pj": allocation_check["max_province_zone_gap_pj"],
    }

    # Write the data CSVs before the final official-hash check.
    target.mkdir(parents=True, exist_ok=True)
    zone_fields = [
        "basis",
        "scenario",
        "zone_id",
        "zone_name",
        "zone_mapping_status",
        "allocation_status",
        "zone_weight_methods",
        "zone_evidence_tiers",
        "proxy_share",
        "provinces_included",
        "province_count",
        "province_basis",
        "stream",
        "availability_case",
        "availability_fraction",
        "residue_odt",
        "pj",
        "national_case_odt",
        "national_case_pj",
        "share_of_national_case",
        "supply_curve_tier_at_national_case",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "official_osecan_changed",
        "chain_v51_unchanged",
        "caveat",
    ]
    process_rows = [row for row in zone_rows if row["stream"] == "process"]
    harvest_rows = [row for row in zone_rows if row["stream"] == "harvest"]
    _write_csv(target / "zone_process_residue.csv", zone_fields, process_rows)
    _write_csv(target / "zone_harvest_residue.csv", zone_fields, harvest_rows)
    _write_csv(target / "case_zone_summary.csv", zone_fields, zone_rows)

    tier_fields = [
        "basis",
        "scenario",
        "stream",
        "case",
        "province_basis",
        "national_odt",
        "national_pj",
        "tier",
        "v10_119_national_pj",
        "v10_119_tier",
        "ws4_minus_v10_pj",
        "ws4_abs_delta_pj",
        "tier_changed",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "official_osecan_changed",
        "chain_v51_unchanged",
    ]
    _write_csv(target / "case_tier_positions.csv", tier_fields, tier_rows)

    comparison_fields = [
        "scenario",
        "stream",
        "case",
        "frozen_v5_1_tier",
        "v10_national_pj",
        "v10_tier",
        "v10_tier_matches_frozen",
        "ws4_basis_primary",
        "ws4_national_pj_primary",
        "ws4_tier_primary",
        "ws4_minus_v10_pj_primary",
        "ws4_abs_delta_pj_primary",
        "ws4_tier_changed_primary",
        "ws4_basis_secondary",
        "ws4_national_pj_secondary",
        "ws4_tier_secondary",
        "ws4_minus_v10_pj_secondary",
        "ws4_abs_delta_pj_secondary",
        "ws4_tier_changed_secondary",
        "comparison_status",
        "v10_province_basis",
        "ws4_province_basis",
        "model_estimate",
        "observed",
        "policy_grade",
        "national_claim",
        "ws4_derived",
        "official_osecan_changed",
        "chain_v51_unchanged",
    ]
    _write_csv(target / "tier_comparison_vs_v10.csv", comparison_fields, comparison_rows)

    osecan_after_state = _tree_state(OSECAN_DIR)
    changed_files = (
        []
        if osecan_before_state["tree_sha256"]
        == osecan_after_state["tree_sha256"]
        else ["<official OSeCAN tree changed>"]
    )
    frozen_after = {
        "handoff_v10": _tree_state(HANDOFF_V10_DIR),
        "curve_v9": _tree_state(CURVE_V9_DIR),
        "ws4_chain": _tree_state(WS4_DIR),
    }
    for name, value in frozen_before.items():
        if value != frozen_after[name]:
            raise ForestryOsecanHandoffWs4Error(
                f"frozen input tree changed during build: {name}"
            )

    checks = _validate_outputs(
        zone_rows,
        tier_rows,
        comparison_rows,
        national_cases,
        zone_summary,
        v10_tiers_check,
        tier_check,
        osecan_before_state["tree_sha256"],
        osecan_after_state["tree_sha256"],
        changed_files,
    )
    failed = sum(1 for check in checks if not check["passed"])
    if failed:
        failed_names = ", ".join(
            check["check"] for check in checks if not check["passed"]
        )
        raise ForestryOsecanHandoffWs4Error(
            f"{failed} validation checks failed: {failed_names}"
        )
    if changed_files:
        raise ForestryOsecanHandoffWs4Error(
            "official OSeCAN files changed during the build: "
            + ", ".join(changed_files)
        )

    control_total = {
        "control_total_m3": CONTROL_TOTAL_M3,
        "achieved_m3": achieved_total,
        "overshoot_m3": achieved_total - CONTROL_TOTAL_M3,
        "area_ha": math.fsum(
            _finite_nonnegative(row["area_ha"], f"{row['province']} area_ha")
            for row in _read_rows(
                WS4_HARVEST,
                ["province", "area_ha"],
                "WS4 harvest area",
            )
        ),
    }

    national_totals = {
        basis: {
            stream: {
                case: {
                    "odt": national_cases[basis][(stream, case)][0],
                    "pj": national_cases[basis][(stream, case)][1],
                }
                for case, _fraction in cases
            }
            for stream, cases in (
                ("process", PROCESS_CASES),
                ("harvest", HARVEST_CASES),
            )
        }
        for basis in BASES
    }

    inputs = {
        _relative(WS4_PRODUCT_CHAIN): _sha256(WS4_PRODUCT_CHAIN),
        _relative(WS4_HARVEST): _sha256(WS4_HARVEST),
        _relative(WS4_SUMMARY): _sha256(WS4_SUMMARY),
        _relative(ZONE_WEIGHTS): _sha256(ZONE_WEIGHTS),
        _relative(V10_TIER_POSITIONS): _sha256(V10_TIER_POSITIONS),
        _relative(V10_SUMMARY): _sha256(V10_SUMMARY),
        _relative(V10_MANIFEST): _sha256(V10_MANIFEST),
        _relative(CURVE_V9_SUMMARY): _sha256(CURVE_V9_SUMMARY),
        _relative(OSECAN_CROSSWALK): _sha256(OSECAN_CROSSWALK),
        _relative(OSECAN_TIER_DEFINITIONS): _sha256(OSECAN_TIER_DEFINITIONS),
    }

    caveats = [
        (
            "The WS4 chain applies frozen chain-v5.1 coefficients to a "
            "whole-block 119 Mm3 WS4 harvest. It is not a sustainable harvest, "
            "AAC, policy result, or investment case."
        ),
        (
            "The WS4 chain is origin-province based; handoff v10 process "
            "residue is facility-province based. Zone rows are not directly "
            "comparable across the two bases. Only national totals and tier "
            "positions are compared."
        ),
        (
            "The WS4 chain covers the 119 Mm3 control only. Frozen v5.1 has "
            "100/119/150 Mm3 tier positions; the 100 and 150 Mm3 positions "
            "are reported as not available rather than fabricated."
        ),
        (
            "Zone weights are frozen handoff-v10 screening proxies, not "
            "observed sub-provincial residue shares or policy."
        ),
        (
            "Process residue and harvest residue remain separate streams and "
            "are never summed. PJ uses the frozen 18.5 GJ/ODT lower heating "
            "value; 0.60 harvest recovery is a reference case."
        ),
        (
            "No candidate activity limit, official OSeCAN file, UI surface, "
            "or frozen v5.1 artifact is changed."
        ),
        (
            "No transport, cost, revenue, tenure, AAC, capacity-expansion, or "
            "policy claim is made."
        ),
    ]

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": "pass",
        "scenario": SCENARIO,
        "basis": {
            "primary": PRIMARY_BASIS,
            "secondary": SECONDARY_BASIS,
            "bases": list(BASES),
            "origin_province": True,
            "facility_province_handoff": False,
            "control_total_m3": CONTROL_TOTAL_M3,
            "note": (
                "WS4 process and harvest residue are origin-province based. "
                "Handoff v10 process residue is facility-province based; no "
                "zone-row equality is claimed."
            ),
        },
        "labels": SUMMARY_LABELS,
        "inputs": inputs,
        "ws4_control": control_total,
        "national_totals": national_totals,
        "zone_weights": {
            "source": _relative(ZONE_WEIGHTS),
            "zone_count": len(ZONE_ORDER),
            "province_count": len(PROVINCES),
            "province_weight_sum_min": zone_summary["province_weight_sum_min"],
            "province_weight_sum_max": zone_summary["province_weight_sum_max"],
            "global_weight_sum": zone_summary["global_weight_sum"],
            "multi_zone_provinces": list(MULTI_ZONE_PROVINCES),
            "note": (
                "Each province's zone weights sum to one; the global sum is 13 "
                "because there are 13 provinces and 13 zones."
            ),
        },
        "zone_conservation": {
            "zone_count": zone_summary["zone_count"],
            "zone_rows": len(zone_rows),
            "zone_rows_expected": zone_summary["zone_rows_expected"],
            "zone_rows_per_case": zone_summary["zone_rows_per_case"],
            "max_zone_national_gap_pj": zone_summary["max_zone_national_gap_pj"],
            "max_zone_national_gap_odt": zone_summary["max_zone_national_gap_odt"],
            "max_province_zone_gap_pj": zone_summary["max_province_zone_gap_pj"],
        },
        "tiers": {
            "upper_pj": tiers["T4"]["upper_pj"],
            "tiers": tiers,
        },
        "tier_comparison": {
            **tier_check,
            "v10_rows": comparison_rows,
            "frozen_v5_1_tier_positions": {
                f"{scenario}/{stream}/{case}": tier
                for (scenario, stream, case), tier in FROZEN_V51_TIERS.items()
            },
            "ws4_case_tier": {
                f"{row['basis']}/{row['stream']}/{row['case']}": row["tier"]
                for row in tier_rows
            },
        },
        "osecan_integrity": {
            "root": osecan_before_state["root"],
            "file_count_before": osecan_before_state["file_count"],
            "file_count_after": osecan_after_state["file_count"],
            "tree_sha256_before": osecan_before_state["tree_sha256"],
            "tree_sha256_after": osecan_after_state["tree_sha256"],
            "unchanged": (
                osecan_before_state["tree_sha256"]
                == osecan_after_state["tree_sha256"]
                and not changed_files
            ),
            "changed_files": changed_files,
            "expected_tree_sha256": EXPECTED_OSECAN_TREE_SHA256,
        },
        "frozen_inputs_integrity": {
            "before": frozen_before,
            "after": frozen_after,
            "unchanged": frozen_before == frozen_after,
        },
        "d_status": {
            "no_unexpected_deletions": True,
            "checked_repositories": ["parent", "data"],
        },
        "validation": {
            "checks": checks,
            "passed": failed == 0,
            "failed_check_count": failed,
        },
        "caveats": caveats,
        "reproduce": f".venv/bin/python -m {MODULE_PATH}",
    }

    _write_json(target / "summary.json", summary)
    (target / REPORT_NAME).write_text(
        _report(summary, zone_rows, tier_rows, comparison_rows),
        encoding="utf-8",
    )
    outputs = _summary_outputs(target)
    manifest = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "title": ARTIFACT_TITLE,
        "generated_utc": generated_utc,
        "status": "pass",
        "scenario": SCENARIO,
        "labels": SUMMARY_LABELS,
        "inputs": inputs,
        "outputs": outputs,
        "national_totals": national_totals,
        "zone_conservation": summary["zone_conservation"],
        "tier_comparison": tier_check,
        "tiers": summary["tiers"],
        "osecan_integrity": summary["osecan_integrity"],
        "frozen_inputs_integrity": summary["frozen_inputs_integrity"],
        "d_status": summary["d_status"],
        "validation": summary["validation"],
        "reproduce": summary["reproduce"],
    }
    _write_json(target / "manifest.json", manifest)

    # Final integrity check after every write.
    assert_no_unexpected_deletions()
    osecan_final_state = _tree_state(OSECAN_DIR)
    if osecan_final_state["tree_sha256"] != osecan_before_state["tree_sha256"]:
        raise ForestryOsecanHandoffWs4Error(
            "official OSeCAN tree changed after the build"
        )
    frozen_final = {
        "handoff_v10": _tree_state(HANDOFF_V10_DIR),
        "curve_v9": _tree_state(CURVE_V9_DIR),
        "ws4_chain": _tree_state(WS4_DIR),
    }
    if frozen_final != frozen_before:
        raise ForestryOsecanHandoffWs4Error(
            "frozen input tree changed after the build"
        )
    summary["outputs"] = outputs
    return summary


def main() -> None:
    summary = build()
    tier = summary["tier_comparison"]
    print(
        json.dumps(
            {
                "artifact": summary["artifact"],
                "status": summary["status"],
                "scenario": summary["scenario"],
                "v10_frozen_v51_tiers": (
                    f"{tier['v10_positions_matching_frozen_v5_1']}/"
                    f"{tier['frozen_v5_1_positions']}"
                ),
                "ws4_primary_tiers": (
                    f"{tier['ws4_primary_tier_matches']}/"
                    f"{tier['ws4_positions_available']}"
                ),
                "ws4_secondary_tiers": (
                    f"{tier['ws4_secondary_tier_matches']}/"
                    f"{tier['ws4_positions_available']}"
                ),
                "official_osecan_unchanged": summary["osecan_integrity"][
                    "unchanged"
                ],
                "official_tree_sha256": summary["osecan_integrity"][
                    "tree_sha256_before"
                ],
                "no_unexpected_deletions": summary["d_status"][
                    "no_unexpected_deletions"
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
