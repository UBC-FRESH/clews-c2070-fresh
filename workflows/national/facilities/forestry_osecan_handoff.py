"""Build a read-only forestry residue handoff to OSeCAN zones.

The builder consumes ``national_residue_curve_v1`` province/case rows and maps
each province to the OSeCAN zone crosswalk.  Provinces that span multiple
OSeCAN zones are kept explicit as ``UNMAPPED`` because the crosswalk publishes
no sub-provincial residue-split basis.

The handoff keeps the process and harvest residue streams separate.  Process
rows use the curve ``TOTAL`` row only, and harvest rows use the ``ALL`` row
only, so class-level and total-level rows are never added together.  Every row
carries ``model_estimate=true``, ``observed=false``, ``policy_grade=false``,
and ``official_osecan_changed=false``.

The builder compares national and BC case totals with the adopted OSeCAN
supply-curve tier caps and the 2050 OSeCAN supply context.  Those references
are context, not policy targets.  No official OSeCAN file is written; the
OSeCAN tree is hashed before and after the build and must be identical.

Fail-closed rules
-----------------
* Any unexpected ``D`` in the parent or ``data`` repository status halts the
  builder before it writes output.
* Every numeric input is parsed once, required finite and non-negative, and
  then used internally as a trusted value.
* Zone membership, province coverage, class/total conservation, national
  reconciliation, PJ conversion, row uniqueness, and labels are validated.
* OSeCAN hashes must be unchanged before the outputs are written.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import re
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
OUTPUT_DIR = (
    DATA_ROOT / "derived/national/facilities/forestry_osecan_handoff_v1"
)
CURVE_DIR = DATA_ROOT / "derived/national/facilities/national_residue_curve_v1"
PROVINCE_CURVE = CURVE_DIR / "province_residue_curve.csv"
NATIONAL_CURVE = CURVE_DIR / "national_residue_curve.csv"
CURVE_SUMMARY = CURVE_DIR / "summary.json"
OSECAN_DIR = DATA_ROOT / "derived/national/clews/osecan"
CROSSWALK = OSECAN_DIR / "zone_crosswalk.csv"
SUPPLY_CURVE_DIR = OSECAN_DIR / "national_supply_curve"
TIER_DEFINITIONS = SUPPLY_CURVE_DIR / "tier_definitions.csv"
TIER_LIMITS = SUPPLY_CURVE_DIR / "tier_activity_limits.csv"
BC_ZONAL_LIMITS = SUPPLY_CURVE_DIR / "bc_zonal_activity_limits.csv"
SUPPLY_DEFINITION = SUPPLY_CURVE_DIR / "supply_curve_definition.json"
DISPATCH = OSECAN_DIR / "national_base_119m/dispatch_by_zone.csv"
NATIONAL_MANIFEST = (
    OSECAN_DIR / "national_base_119m/manifest.json"
)

ARTIFACT = "forestry_osecan_handoff_v1"
ARTIFACT_VERSION = "1.0.0"
PJ_GJ = 1_000_000.0
EXPECTED_LHV_GJ_PER_ODT = 18.5
EXPECTED_TIER_UPPER_PJ = 213.538041
EXPECTED_BC_ZONAL_UPPER_PJ = 43.523816
EXPECTED_NATIONAL_2050_CONTEXT_PJ = 152.92277
EXPECTED_BC_2050_CONTEXT_PJ = 43.52377
REL_TOL = 1e-8
ABS_TOL = 1e-6
LABEL_TRUE = "true"
LABEL_FALSE = "false"
UNMAPPED_ZONE = "UNMAPPED"

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
PROVINCE_ORDER: tuple[str, ...] = tuple(PROVINCES)
CLASS_ORDER: tuple[str, ...] = ("SAW", "PULP", "PANEL", "PELLET", "BIO")
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
BC_ZONES: tuple[str, ...] = ("B1", "B2")
PROCESS_STREAM = "process"
HARVEST_STREAM = "harvest"
PROCESS_CLASS_TOTAL = "TOTAL"
HARVEST_CLASS_ALL = "ALL"

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
CASE_ORDER: tuple[str, ...] = tuple(CASE_FRACTIONS)


class ForestryOsecanHandoffError(RuntimeError):
    """Raised when the forestry-to-OSeCAN handoff fails a guard."""


@dataclass(frozen=True)
class Zone:
    """One official OSeCAN zone from the read-only crosswalk."""

    zone_id: str
    zone_name: str
    provinces: tuple[str, ...]
    mapping_basis: str
    official_status: str
    geometry_available: bool
    notes: str


@dataclass(frozen=True)
class ZoneAssignment:
    """Zone assignment for one province."""

    zone_id: str
    zone_name: str
    status: str
    candidates: tuple[str, ...]
    basis: str
    note: str


@dataclass(frozen=True)
class ProvinceCase:
    """Trusted province/stream/case residue total."""

    province: str
    province_name: str
    stream: str
    residue_class: str
    availability_case: str
    availability_fraction: float
    odt: float
    pj: float
    evidence_tier: str
    province_basis: str
    rsyc_comparable: bool
    caveat: str
    assignment: ZoneAssignment


@dataclass(frozen=True)
class NationalCase:
    """Trusted national/stream/case residue total."""

    stream: str
    residue_class: str
    availability_case: str
    availability_fraction: float
    odt: float
    pj: float


@dataclass(frozen=True)
class Tier:
    """One adopted OSeCAN supply-curve tier."""

    tier: str
    technology: str
    lower_pj: float
    upper_pj: float
    incremental_cap_pj: float


@dataclass(frozen=True)
class OsecanContext:
    """Read-only OSeCAN comparison references."""

    national_2050_supply_pj: float
    bc_2050_supply_pj: float
    bc_zonal_upper_pj: float
    tier_upper_pj: float
    dispatch_source: str
    dispatch_sha256: str
    manifest_status: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_tree(root: Path) -> tuple[dict[str, str], str]:
    if not root.is_dir():
        raise ForestryOsecanHandoffError(f"OSeCAN directory is missing: {root}")
    hashes: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            hashes[str(path.relative_to(REPO_ROOT))] = _sha256(path)
    if not hashes:
        raise ForestryOsecanHandoffError(f"OSeCAN directory is empty: {root}")
    digest = hashlib.sha256()
    for relative, value in sorted(hashes.items()):
        digest.update(f"{relative}:{value}\n".encode())
    return hashes, digest.hexdigest()


def _read_csv(path: Path, required: Sequence[str], label: str) -> pd.DataFrame:
    if not path.is_file():
        raise ForestryOsecanHandoffError(f"{label} does not exist: {path}")
    frame = pd.read_csv(path, keep_default_na=False, dtype=str)
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ForestryOsecanHandoffError(
            f"{label} is missing columns {missing}: {path}"
        )
    if frame.empty:
        raise ForestryOsecanHandoffError(f"{label} is empty: {path}")
    return frame


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ForestryOsecanHandoffError(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ForestryOsecanHandoffError(
            f"{label} is not valid JSON: {path}"
        ) from exc
    if not isinstance(payload, dict):
        raise ForestryOsecanHandoffError(f"{label} is not a JSON object: {path}")
    return payload


def _finite_nonnegative(value: Any, label: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ForestryOsecanHandoffError(
            f"{label} is not numeric: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise ForestryOsecanHandoffError(f"{label} is not finite: {parsed!r}")
    if parsed < 0.0:
        raise ForestryOsecanHandoffError(f"{label} is negative: {parsed!r}")
    return parsed


def _parse_bool(value: Any, label: str) -> bool:
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no", ""}:
        return False
    raise ForestryOsecanHandoffError(f"{label} is not a boolean: {value!r}")


def _require_close(
    left: float, right: float, label: str, *, abs_tol: float = ABS_TOL
) -> None:
    scale = max(abs(left), abs(right), 1.0)
    if abs(left - right) > REL_TOL * scale + abs_tol:
        raise ForestryOsecanHandoffError(
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
        raise ForestryOsecanHandoffError(
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
        raise ForestryOsecanHandoffError(
            "unexpected deletion status detected; halting: "
            + " | ".join(incidents)
        )


def _parse_zone_crosswalk() -> tuple[dict[str, Zone], dict[str, tuple[str, ...]]]:
    required = [
        "zone_id",
        "zone_name",
        "provinces_or_regions",
        "mapping_basis",
        "official_status",
        "geometry_available",
        "notes",
    ]
    frame = _read_csv(CROSSWALK, required, "OSeCAN zone crosswalk")
    zones: dict[str, Zone] = {}
    province_zones: dict[str, list[str]] = defaultdict(list)
    for _, record in frame.iterrows():
        zone_id = str(record["zone_id"]).strip().upper()
        if not re.fullmatch(r"[A-Z][0-9]", zone_id):
            raise ForestryOsecanHandoffError(
                f"unexpected zone id in crosswalk: {zone_id!r}"
            )
        if zone_id in zones:
            raise ForestryOsecanHandoffError(
                f"duplicate zone id in crosswalk: {zone_id!r}"
            )
        provinces = tuple(
            sorted(
                set(
                    re.findall(
                        r"\(([A-Z]{2})\)",
                        str(record["provinces_or_regions"]),
                    )
                )
            )
        )
        if not provinces:
            raise ForestryOsecanHandoffError(
                f"zone {zone_id} has no province codes in the crosswalk"
            )
        unexpected = set(provinces) - set(PROVINCES)
        if unexpected:
            raise ForestryOsecanHandoffError(
                f"zone {zone_id} has unknown provinces {sorted(unexpected)}"
            )
        zone = Zone(
            zone_id=zone_id,
            zone_name=str(record["zone_name"]).strip(),
            provinces=provinces,
            mapping_basis=str(record["mapping_basis"]).strip(),
            official_status=str(record["official_status"]).strip(),
            geometry_available=_parse_bool(
                record["geometry_available"], f"zone {zone_id} geometry"
            ),
            notes=str(record["notes"]).strip(),
        )
        zones[zone_id] = zone
        for province in provinces:
            province_zones[province].append(zone_id)

    if set(zones) != set(ZONE_ORDER):
        raise ForestryOsecanHandoffError(
            "OSeCAN zone set does not match the 13 expected zones: "
            f"{sorted(zones)}"
        )
    if set(province_zones) != set(PROVINCES):
        raise ForestryOsecanHandoffError(
            "zone crosswalk does not cover all 13 jurisdictions: "
            f"{sorted(set(PROVINCES) - set(province_zones))}"
        )
    normalized = {
        province: tuple(sorted(set(values)))
        for province, values in province_zones.items()
    }
    for province, values in normalized.items():
        if len(values) != len(set(values)):
            raise ForestryOsecanHandoffError(
                f"{province} has duplicate zone membership"
            )
    return zones, normalized


def _zone_assignment(
    province: str,
    zones: Mapping[str, Zone],
    province_zones: Mapping[str, tuple[str, ...]],
) -> ZoneAssignment:
    candidates = province_zones[province]
    if len(candidates) == 1:
        zone_id = candidates[0]
        zone = zones[zone_id]
        status = (
            "mapped_aggregate_zone"
            if len(zone.provinces) > 1
            else "mapped_single_zone"
        )
        return ZoneAssignment(
            zone_id=zone_id,
            zone_name=zone.zone_name,
            status=status,
            candidates=candidates,
            basis=f"zone_crosswalk:{zone.mapping_basis}",
            note=(
                "Province is a member of exactly one OSeCAN zone"
                + (
                    " aggregated with other provinces."
                    if len(zone.provinces) > 1
                    else "."
                )
            ),
        )
    if len(candidates) > 1:
        return ZoneAssignment(
            zone_id="",
            zone_name="Unmapped multi-zone province",
            status="unmapped_multi_zone_province",
            candidates=candidates,
            basis="no_subprovincial_residue_split_basis",
            note=(
                f"Province spans {len(candidates)} OSeCAN zones "
                f"({','.join(candidates)}); the crosswalk publishes no "
                "sub-provincial residue-split basis, so the province is kept "
                "in the explicit UNMAPPED bucket."
            ),
        )
    raise ForestryOsecanHandoffError(f"{province} has no OSeCAN zone")


def _parse_province_cases(
    zones: Mapping[str, Zone],
    province_zones: Mapping[str, tuple[str, ...]],
) -> list[ProvinceCase]:
    required = [
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
        "model_estimate",
        "observed",
        "policy_grade",
        "not_policy",
        "evidence_tier",
        "in_supply_curve",
        "caveat",
    ]
    frame = _read_csv(PROVINCE_CURVE, required, "province residue curve")
    frame["province"] = frame["province"].astype(str).str.strip().str.upper()
    frame["stream"] = frame["stream"].astype(str).str.strip().str.lower()
    frame["residue_class"] = (
        frame["residue_class"].astype(str).str.strip().str.upper()
    )
    frame["availability_case"] = (
        frame["availability_case"].astype(str).str.strip()
    )
    if set(frame["province"]) != set(PROVINCES):
        raise ForestryOsecanHandoffError(
            "province curve does not cover all 13 jurisdictions"
        )
    if frame["geography_level"].astype(str).str.strip().ne("province").any():
        raise ForestryOsecanHandoffError(
            "province curve contains non-province geography rows"
        )

    process = frame[frame["stream"] == PROCESS_STREAM]
    if set(process["availability_case"]) != {
        case for case, _ in PROCESS_CASES
    }:
        raise ForestryOsecanHandoffError(
            "process availability cases do not match the expected set"
        )
    harvest = frame[frame["stream"] == HARVEST_STREAM]
    if set(harvest["availability_case"]) != {
        case for case, _ in HARVEST_CASES
    }:
        raise ForestryOsecanHandoffError(
            "harvest recovery cases do not match the expected set"
        )
    if set(harvest["residue_class"]) != {HARVEST_CLASS_ALL}:
        raise ForestryOsecanHandoffError(
            "harvest rows must use the ALL class row only"
        )

    # Class rows are used only for conservation checks.  The handoff uses the
    # mutually exclusive TOTAL row so class and total rows are never added.
    for (province, case), group in process.groupby(
        ["province", "availability_case"], sort=True
    ):
        totals = group[group["residue_class"] == PROCESS_CLASS_TOTAL]
        classes = group[group["residue_class"].isin(CLASS_ORDER)]
        if len(totals) != 1:
            raise ForestryOsecanHandoffError(
                f"{province}/{case}: expected one process TOTAL row"
            )
        if set(classes["residue_class"]) != set(CLASS_ORDER):
            raise ForestryOsecanHandoffError(
                f"{province}/{case}: process class rows are incomplete"
            )
        total = totals.iloc[0]
        class_odt = math.fsum(
            _finite_nonnegative(value, f"{province}/{case} class odt")
            for value in classes["residue_odt"]
        )
        class_pj = math.fsum(
            _finite_nonnegative(value, f"{province}/{case} class pj")
            for value in classes["residue_pj"]
        )
        _require_close(
            class_odt,
            _finite_nonnegative(total["residue_odt"], f"{province}/{case} total odt"),
            f"{province}/{case} process class/total odt",
        )
        _require_close(
            class_pj,
            _finite_nonnegative(total["residue_pj"], f"{province}/{case} total pj"),
            f"{province}/{case} process class/total pj",
        )

    cases: dict[tuple[str, str, str], ProvinceCase] = {}
    for _, record in frame.iterrows():
        stream = str(record["stream"])
        residue_class = str(record["residue_class"])
        if stream == PROCESS_STREAM and residue_class != PROCESS_CLASS_TOTAL:
            continue
        if stream == HARVEST_STREAM and residue_class != HARVEST_CLASS_ALL:
            continue
        if stream not in {PROCESS_STREAM, HARVEST_STREAM}:
            raise ForestryOsecanHandoffError(
                f"unexpected residue stream: {stream!r}"
            )
        province = str(record["province"])
        case = str(record["availability_case"])
        fraction = _finite_nonnegative(
            record["availability_fraction"],
            f"{province}/{stream}/{case} availability_fraction",
        )
        expected_fraction = CASE_FRACTIONS.get(case)
        if expected_fraction is None:
            raise ForestryOsecanHandoffError(
                f"unexpected availability case: {case!r}"
            )
        _require_close(
            fraction,
            expected_fraction,
            f"{province}/{stream}/{case} availability fraction",
            abs_tol=1e-12,
        )
        lhv = _finite_nonnegative(
            record["lhv_gj_per_odt"], f"{province}/{stream}/{case} LHV"
        )
        _require_close(lhv, EXPECTED_LHV_GJ_PER_ODT, "LHV")
        odt = _finite_nonnegative(
            record["residue_odt"], f"{province}/{stream}/{case} odt"
        )
        pj = _finite_nonnegative(
            record["residue_pj"], f"{province}/{stream}/{case} pj"
        )
        _require_close(
            pj,
            odt * lhv / PJ_GJ,
            f"{province}/{stream}/{case} PJ conversion",
        )
        if _parse_bool(
            record["model_estimate"], f"{province}/{stream}/{case} model_estimate"
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: model_estimate is not true"
            )
        if _parse_bool(
            record["observed"], f"{province}/{stream}/{case} observed"
        ):
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: observed is true"
            )
        if _parse_bool(
            record["policy_grade"], f"{province}/{stream}/{case} policy_grade"
        ):
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: policy_grade is true"
            )
        if _parse_bool(
            record["not_policy"], f"{province}/{stream}/{case} not_policy"
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: not_policy is not true"
            )
        if _parse_bool(
            record["in_supply_curve"],
            f"{province}/{stream}/{case} in_supply_curve",
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: in_supply_curve is not true"
            )
        province_basis = str(record["province_basis"]).strip()
        expected_basis = (
            "facility_province"
            if stream == PROCESS_STREAM
            else "origin_province"
        )
        if province_basis != expected_basis:
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: unexpected province basis "
                f"{province_basis!r}"
            )
        facility_assigned = _parse_bool(
            record["facility_assigned"],
            f"{province}/{stream}/{case} facility_assigned",
        )
        if facility_assigned != (stream == PROCESS_STREAM):
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: facility_assigned mismatch"
            )
        rsyc_comparable = _parse_bool(
            record["rsyc_comparable"],
            f"{province}/{stream}/{case} rsyc_comparable",
        )
        if rsyc_comparable != (stream == HARVEST_STREAM):
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: rsyc_comparable mismatch"
            )
        evidence_tier = str(record["evidence_tier"]).strip()
        caveat = str(record["caveat"]).strip()
        if not evidence_tier or not caveat:
            raise ForestryOsecanHandoffError(
                f"{province}/{stream}/{case}: empty evidence tier or caveat"
            )
        key = (province, stream, case)
        if key in cases:
            raise ForestryOsecanHandoffError(
                f"duplicate province/stream/case row: {key}"
            )
        cases[key] = ProvinceCase(
            province=province,
            province_name=str(record["province_name"]).strip(),
            stream=stream,
            residue_class=residue_class,
            availability_case=case,
            availability_fraction=fraction,
            odt=odt,
            pj=pj,
            evidence_tier=evidence_tier,
            province_basis=province_basis,
            rsyc_comparable=rsyc_comparable,
            caveat=caveat,
            assignment=_zone_assignment(province, zones, province_zones),
        )

    expected_count = len(PROVINCES) * (len(PROCESS_CASES) + len(HARVEST_CASES))
    if len(cases) != expected_count:
        raise ForestryOsecanHandoffError(
            f"expected {expected_count} province/stream/case rows, "
            f"found {len(cases)}"
        )
    return [
        cases[(province, stream, case)]
        for province in PROVINCE_ORDER
        for stream in (PROCESS_STREAM, HARVEST_STREAM)
        for case, _ in (
            PROCESS_CASES if stream == PROCESS_STREAM else HARVEST_CASES
        )
    ]


def _parse_national_cases(
    province_cases: Sequence[ProvinceCase],
) -> dict[tuple[str, str], NationalCase]:
    required = [
        "geography_level",
        "province",
        "residue_type",
        "residue_class",
        "stream",
        "availability_case",
        "availability_fraction",
        "residue_odt",
        "residue_pj",
        "lhv_gj_per_odt",
        "model_estimate",
        "observed",
        "policy_grade",
        "not_policy",
        "in_supply_curve",
    ]
    frame = _read_csv(NATIONAL_CURVE, required, "national residue curve")
    frame["stream"] = frame["stream"].astype(str).str.strip().str.lower()
    frame["residue_class"] = (
        frame["residue_class"].astype(str).str.strip().str.upper()
    )
    frame["availability_case"] = (
        frame["availability_case"].astype(str).str.strip()
    )
    if set(frame["geography_level"].astype(str).str.strip()) != {"national"}:
        raise ForestryOsecanHandoffError(
            "national curve contains non-national geography rows"
        )
    selected = frame[
        (
            (frame["stream"] == PROCESS_STREAM)
            & (frame["residue_class"] == PROCESS_CLASS_TOTAL)
        )
        | (
            (frame["stream"] == HARVEST_STREAM)
            & (frame["residue_class"] == HARVEST_CLASS_ALL)
        )
    ]
    national: dict[tuple[str, str], NationalCase] = {}
    for _, record in selected.iterrows():
        stream = str(record["stream"])
        case = str(record["availability_case"])
        fraction = _finite_nonnegative(
            record["availability_fraction"], f"national/{stream}/{case} fraction"
        )
        expected_fraction = CASE_FRACTIONS.get(case)
        if expected_fraction is None:
            raise ForestryOsecanHandoffError(
                f"unexpected national case: {case!r}"
            )
        _require_close(
            fraction,
            expected_fraction,
            f"national/{stream}/{case} availability fraction",
            abs_tol=1e-12,
        )
        lhv = _finite_nonnegative(
            record["lhv_gj_per_odt"], f"national/{stream}/{case} LHV"
        )
        _require_close(lhv, EXPECTED_LHV_GJ_PER_ODT, "national LHV")
        odt = _finite_nonnegative(
            record["residue_odt"], f"national/{stream}/{case} odt"
        )
        pj = _finite_nonnegative(
            record["residue_pj"], f"national/{stream}/{case} pj"
        )
        _require_close(
            pj,
            odt * lhv / PJ_GJ,
            f"national/{stream}/{case} PJ conversion",
        )
        if _parse_bool(
            record["model_estimate"], f"national/{stream}/{case} model_estimate"
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"national/{stream}/{case}: model_estimate is not true"
            )
        if _parse_bool(record["observed"], f"national/{stream}/{case} observed"):
            raise ForestryOsecanHandoffError(
                f"national/{stream}/{case}: observed is true"
            )
        if _parse_bool(
            record["policy_grade"], f"national/{stream}/{case} policy_grade"
        ):
            raise ForestryOsecanHandoffError(
                f"national/{stream}/{case}: policy_grade is true"
            )
        if _parse_bool(
            record["not_policy"], f"national/{stream}/{case} not_policy"
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"national/{stream}/{case}: not_policy is not true"
            )
        if _parse_bool(
            record["in_supply_curve"],
            f"national/{stream}/{case} in_supply_curve",
        ) is not True:
            raise ForestryOsecanHandoffError(
                f"national/{stream}/{case}: in_supply_curve is not true"
            )
        key = (stream, case)
        if key in national:
            raise ForestryOsecanHandoffError(
                f"duplicate national/stream/case row: {key}"
            )
        national[key] = NationalCase(
            stream=stream,
            residue_class=(
                PROCESS_CLASS_TOTAL
                if stream == PROCESS_STREAM
                else HARVEST_CLASS_ALL
            ),
            availability_case=case,
            availability_fraction=fraction,
            odt=odt,
            pj=pj,
        )

    expected_count = len(PROCESS_CASES) + len(HARVEST_CASES)
    if len(national) != expected_count:
        raise ForestryOsecanHandoffError(
            f"expected {expected_count} national/stream/case rows, "
            f"found {len(national)}"
        )
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            province_total_odt = math.fsum(
                item.odt
                for item in province_cases
                if item.stream == stream and item.availability_case == case
            )
            province_total_pj = math.fsum(
                item.pj
                for item in province_cases
                if item.stream == stream and item.availability_case == case
            )
            national_case = national[(stream, case)]
            _require_close(
                province_total_odt,
                national_case.odt,
                f"national {stream}/{case} odt reconciliation",
                abs_tol=1e-3,
            )
            _require_close(
                province_total_pj,
                national_case.pj,
                f"national {stream}/{case} PJ reconciliation",
                abs_tol=1e-5,
            )
    return national


def _parse_tiers() -> dict[str, Tier]:
    required = [
        "tier",
        "technology",
        "lower_pj",
        "upper_pj",
        "incremental_cap_pj",
        "variable_cost_usd_per_gj",
        "cost_basis",
        "scenario_flag",
    ]
    frame = _read_csv(TIER_DEFINITIONS, required, "supply-curve tiers")
    if len(frame) != 4:
        raise ForestryOsecanHandoffError(
            f"expected four supply-curve tiers, found {len(frame)}"
        )
    tiers: dict[str, Tier] = {}
    for _, record in frame.iterrows():
        tier_id = str(record["tier"]).strip().upper()
        tier = Tier(
            tier=tier_id,
            technology=str(record["technology"]).strip(),
            lower_pj=_finite_nonnegative(
                record["lower_pj"], f"{tier_id} lower_pj"
            ),
            upper_pj=_finite_nonnegative(
                record["upper_pj"], f"{tier_id} upper_pj"
            ),
            incremental_cap_pj=_finite_nonnegative(
                record["incremental_cap_pj"], f"{tier_id} incremental_cap_pj"
            ),
        )
        if tier.tier in tiers:
            raise ForestryOsecanHandoffError(f"duplicate tier {tier_id}")
        if tier.upper_pj <= tier.lower_pj:
            raise ForestryOsecanHandoffError(
                f"{tier_id} upper_pj is not above lower_pj"
            )
        _require_close(
            tier.upper_pj - tier.lower_pj,
            tier.incremental_cap_pj,
            f"{tier_id} interval width",
        )
        tiers[tier.tier] = tier
    if set(tiers) != {"T1", "T2", "T3", "T4"}:
        raise ForestryOsecanHandoffError(
            f"unexpected tier set: {sorted(tiers)}"
        )
    ordered = [tiers[key] for key in ("T1", "T2", "T3", "T4")]
    if abs(ordered[0].lower_pj) > ABS_TOL:
        raise ForestryOsecanHandoffError("T1 does not start at zero")
    for previous, current in itertools.pairwise(ordered):
        _require_close(
            previous.upper_pj,
            current.lower_pj,
            f"{previous.tier}/{current.tier} boundary",
        )
    _require_close(
        ordered[-1].upper_pj,
        EXPECTED_TIER_UPPER_PJ,
        "national supply-curve upper bound",
        abs_tol=1e-6,
    )
    _require_close(
        math.fsum(tier.incremental_cap_pj for tier in ordered),
        EXPECTED_TIER_UPPER_PJ,
        "tier incremental cap sum",
        abs_tol=1e-6,
    )

    definition = _load_json(SUPPLY_DEFINITION, "supply-curve definition")
    checks = definition.get("checks", {})
    if checks.get("tier_cap_sum_equals_rest") is not True:
        raise ForestryOsecanHandoffError(
            "supply-curve definition does not confirm tier cap sum"
        )
    _require_close(
        _finite_nonnegative(
            checks.get("tier_cap_sum_pj"), "definition tier_cap_sum_pj"
        ),
        EXPECTED_TIER_UPPER_PJ,
        "definition tier cap sum",
        abs_tol=1e-6,
    )

    limits = _read_csv(
        TIER_LIMITS,
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "tier activity limits",
    )
    limits_2050 = limits[
        (limits["YEAR"].astype(str).str.strip() == "2050")
        & (limits["REGION"].astype(str).str.strip() == "CAN")
    ]
    limit_values = {
        str(record["TECHNOLOGY"]).strip(): _finite_nonnegative(
            record["VALUE"], f"tier limit {record['TECHNOLOGY']}"
        )
        for _, record in limits_2050.iterrows()
    }
    for tier in ordered:
        if tier.technology not in limit_values:
            raise ForestryOsecanHandoffError(
                f"tier activity limits lack {tier.technology}"
            )
        _require_close(
            limit_values[tier.technology],
            tier.incremental_cap_pj,
            f"{tier.technology} 2050 activity limit",
            abs_tol=1e-6,
        )
    if limit_values.get("SUPBCW", 0.0) != 0.0:
        raise ForestryOsecanHandoffError(
            "single SUPBCW cap is not disabled in the tiered curve"
        )
    return tiers


def _parse_osecan_context(tiers: Mapping[str, Tier]) -> OsecanContext:
    dispatch_frame = _read_csv(
        DISPATCH,
        ["run", "technology", "category", "zone", "year", "value_pj"],
        "OSeCAN dispatch",
    )
    stress = dispatch_frame[
        dispatch_frame["run"].astype(str).str.strip() == "stress_119m"
    ]
    if stress.empty:
        raise ForestryOsecanHandoffError(
            "OSeCAN dispatch lacks the stress_119m run"
        )
    stress_2050 = stress[stress["year"].astype(str).str.strip() == "2050"]
    if stress_2050.empty:
        raise ForestryOsecanHandoffError(
            "OSeCAN dispatch lacks 2050 stress rows"
        )
    supply = stress_2050[
        stress_2050["category"].astype(str).str.strip() == "supply"
    ].copy()
    supply["zone"] = supply["zone"].astype(str).str.strip().str.upper()
    if supply.empty:
        raise ForestryOsecanHandoffError(
            "OSeCAN dispatch has no 2050 supply rows"
        )
    values = {
        str(record["zone"]): _finite_nonnegative(
            record["value_pj"], f"OSeCAN {record['zone']} 2050 supply"
        )
        for _, record in supply.iterrows()
    }
    national = math.fsum(values.values())
    bc = math.fsum(values.get(zone, 0.0) for zone in BC_ZONES)
    _require_close(
        national,
        EXPECTED_NATIONAL_2050_CONTEXT_PJ,
        "OSeCAN 2050 national supply context",
        abs_tol=1e-5,
    )
    _require_close(
        bc,
        EXPECTED_BC_2050_CONTEXT_PJ,
        "OSeCAN 2050 BC supply context",
        abs_tol=1e-5,
    )

    bc_limits = _read_csv(
        BC_ZONAL_LIMITS,
        ["REGION", "TECHNOLOGY", "YEAR", "VALUE"],
        "BC zonal activity limits",
    )
    bc_2050 = bc_limits[
        (bc_limits["YEAR"].astype(str).str.strip() == "2050")
        & (bc_limits["REGION"].astype(str).str.strip() == "CAN")
        & (bc_limits["TECHNOLOGY"].astype(str).str.strip().isin(BC_ZONES_TECH))
    ]
    bc_zonal = math.fsum(
        _finite_nonnegative(record["VALUE"], "BC zonal cap")
        for _, record in bc_2050.iterrows()
    )
    _require_close(
        bc_zonal,
        EXPECTED_BC_ZONAL_UPPER_PJ,
        "BC zonal supply upper bound",
        abs_tol=1e-6,
    )

    manifest = _load_json(NATIONAL_MANIFEST, "OSeCAN national manifest")
    status = str(manifest.get("status", "")).strip()
    if status != "fresh_side_provisional_not_c2070_official":
        raise ForestryOsecanHandoffError(
            "OSeCAN national manifest status is not the expected "
            f"provisional status: {status!r}"
        )
    return OsecanContext(
        national_2050_supply_pj=national,
        bc_2050_supply_pj=bc,
        bc_zonal_upper_pj=bc_zonal,
        tier_upper_pj=tiers["T4"].upper_pj,
        dispatch_source=str(DISPATCH.relative_to(REPO_ROOT)),
        dispatch_sha256=_sha256(DISPATCH),
        manifest_status=status,
    )


# BC zonal supply technologies, kept as a module constant for the filter above.
BC_ZONES_TECH: tuple[str, ...] = ("SUPBCWB1", "SUPBCWB2")


def _tier_for_pj(pj: float, tiers: Mapping[str, Tier]) -> str:
    for tier_id in ("T1", "T2", "T3", "T4"):
        if pj <= tiers[tier_id].upper_pj + ABS_TOL:
            return tier_id
    return "above_T4"


def _tier_upper(pj: float, tiers: Mapping[str, Tier]) -> float:
    tier_id = _tier_for_pj(pj, tiers)
    if tier_id == "above_T4":
        return tiers["T4"].upper_pj
    return tiers[tier_id].upper_pj


def _supbcw_mapping(
    assignment: ZoneAssignment, province: str
) -> tuple[str, str, str]:
    if province == "BC":
        if assignment.zone_id in BC_ZONES:
            return (
                f"SUPBCWB{assignment.zone_id[1]}",
                "zone_specific_candidate",
                "BC zonal supply technology for the mapped B zone",
            )
        return (
            "SUPBCWB1|SUPBCWB2",
            "unmapped_province_candidate",
            "BC zonal candidates; no sub-provincial residue split",
        )
    return (
        "SUPBCWT1|SUPBCWT2|SUPBCWT3|SUPBCWT4",
        "rest_of_canada_tier_pool_candidate",
        "Rest-of-Canada tier pool; the tiers are not zone-specific",
    )


def _handoff_caveat(item: ProvinceCase, assignment: ZoneAssignment) -> str:
    if assignment.status == "unmapped_multi_zone_province":
        mapping = (
            "Province spans multiple OSeCAN zones and has no published "
            "sub-provincial residue split; zone_id is empty and the province "
            "is reported in the explicit UNMAPPED bucket."
        )
    else:
        mapping = (
            f"Province maps to one OSeCAN zone ({assignment.zone_id}) by "
            "code-defined crosswalk membership."
        )
    return (
        f"{mapping} SUPBCW technology is a candidate mapping, not an "
        "operating or policy assignment. No official OSeCAN file is changed. "
        f"Source caveat: {item.caveat}"
    )


def _handoff_rows(
    province_cases: Sequence[ProvinceCase],
    national: Mapping[tuple[str, str], NationalCase],
    tiers: Mapping[str, Tier],
    context: OsecanContext,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in province_cases:
        national_case = national[(item.stream, item.availability_case)]
        tier_id = _tier_for_pj(national_case.pj, tiers)
        technology, mapping_kind, mapping_note = _supbcw_mapping(
            item.assignment, item.province
        )
        rows.append(
            {
                "province": item.province,
                "province_name": item.province_name,
                "zone_id": item.assignment.zone_id,
                "zone_name": item.assignment.zone_name,
                "zone_mapped": LABEL_TRUE if item.assignment.zone_id else LABEL_FALSE,
                "zone_mapping_status": item.assignment.status,
                "zone_candidates": "|".join(item.assignment.candidates),
                "zone_mapping_basis": item.assignment.basis,
                "stream": item.stream,
                "residue_class": item.residue_class,
                "province_basis": item.province_basis,
                "availability_case": item.availability_case,
                "availability_fraction": item.availability_fraction,
                "odt": item.odt,
                "pj": item.pj,
                "lhv_gj_per_odt": EXPECTED_LHV_GJ_PER_ODT,
                "supbcw_technology_suggestion": technology,
                "supbcw_mapping": mapping_kind,
                "supbcw_mapping_note": mapping_note,
                "supply_curve_tier_at_national_case": tier_id,
                "supply_curve_tier_upper_pj": _tier_upper(
                    national_case.pj, tiers
                ),
                "evidence_tier": item.evidence_tier,
                "rsyc_comparable": (
                    LABEL_TRUE if item.rsyc_comparable else LABEL_FALSE
                ),
                "model_estimate": LABEL_TRUE,
                "observed": LABEL_FALSE,
                "policy_grade": LABEL_FALSE,
                "official_osecan_changed": LABEL_FALSE,
                "caveat": _handoff_caveat(item, item.assignment),
                "notes": (
                    "Source: national_residue_curve_v1/province_residue_curve.csv; "
                    f"stream={item.stream}; residue_class={item.residue_class}; "
                    f"province_basis={item.province_basis}. Process and harvest "
                    "streams are separate and are not summed."
                ),
            }
        )
    return rows


def _zone_summary_rows(
    province_cases: Sequence[ProvinceCase],
    national: Mapping[tuple[str, str], NationalCase],
    zones: Mapping[str, Zone],
) -> list[dict[str, Any]]:
    by_key = {
        (item.province, item.stream, item.availability_case): item
        for item in province_cases
    }
    rows: list[dict[str, Any]] = []
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            mapped_zone_ids = [
                zone_id
                for zone_id in ZONE_ORDER
                if any(
                    by_key[(province, stream, case)].assignment.zone_id == zone_id
                    for province in PROVINCES
                )
            ]
            for zone_id in mapped_zone_ids:
                members = [
                    province
                    for province in PROVINCE_ORDER
                    if by_key[
                        (province, stream, case)
                    ].assignment.zone_id
                    == zone_id
                ]
                zone = zones[zone_id]
                items = [by_key[(province, stream, case)] for province in members]
                odt = math.fsum(item.odt for item in items)
                pj = math.fsum(item.pj for item in items)
                technology, mapping_kind, _ = _supbcw_mapping(
                    ZoneAssignment(
                        zone_id=zone_id,
                        zone_name=zone.zone_name,
                        status=(
                            "mapped_aggregate_zone"
                            if len(zone.provinces) > 1
                            else "mapped_single_zone"
                        ),
                        candidates=(zone_id,),
                        basis=f"zone_crosswalk:{zone.mapping_basis}",
                        note="",
                    ),
                    members[0],
                )
                rows.append(
                    {
                        "zone_id": zone_id,
                        "zone_name": zone.zone_name,
                        "zone_mapping_status": (
                            "mapped_aggregate_zone"
                            if len(zone.provinces) > 1
                            else "mapped_single_zone"
                        ),
                        "provinces_included": "|".join(members),
                        "mapped_province_count": len(members),
                        "stream": stream,
                        "availability_case": case,
                        "odt": odt,
                        "pj": pj,
                        "national_case_pj": national_case.pj,
                        "share_of_national_case": (
                            pj / national_case.pj if national_case.pj > 0.0 else 0.0
                        ),
                        "share_status": (
                            "defined"
                            if national_case.pj > 0.0
                            else "national_case_zero"
                        ),
                        "supbcw_technology_suggestion": technology,
                        "supbcw_mapping": mapping_kind,
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "caveat": (
                            "Zone total sums mapped province rows once; "
                            "T1 and N1 are exact aggregate zones. No process "
                            "and harvest streams are combined."
                        ),
                    }
                )
            unmapped = [
                province
                for province in PROVINCE_ORDER
                if not by_key[
                    (province, stream, case)
                ].assignment.zone_id
            ]
            if unmapped:
                items = [by_key[(province, stream, case)] for province in unmapped]
                odt = math.fsum(item.odt for item in items)
                pj = math.fsum(item.pj for item in items)
                rows.append(
                    {
                        "zone_id": UNMAPPED_ZONE,
                        "zone_name": "Unmapped multi-zone provinces",
                        "zone_mapping_status": "unmapped_multi_zone_province",
                        "provinces_included": "|".join(unmapped),
                        "mapped_province_count": 0,
                        "stream": stream,
                        "availability_case": case,
                        "odt": odt,
                        "pj": pj,
                        "national_case_pj": national_case.pj,
                        "share_of_national_case": (
                            pj / national_case.pj if national_case.pj > 0.0 else 0.0
                        ),
                        "share_status": (
                            "defined"
                            if national_case.pj > 0.0
                            else "national_case_zero"
                        ),
                        "supbcw_technology_suggestion": (
                            "SUPBCWB1|SUPBCWB2|SUPBCWT1|SUPBCWT2|SUPBCWT3|SUPBCWT4"
                        ),
                        "supbcw_mapping": "mixed_unmapped_candidate_pool",
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "caveat": (
                            "Explicit unmapped bucket. These provinces span "
                            "multiple OSeCAN zones and have no published "
                            "sub-provincial residue split basis; the bucket is "
                            "not a zone and is not compared to a zone cap."
                        ),
                    }
                )
    return rows


def _comparison_rows(
    national: Mapping[tuple[str, str], NationalCase],
    province_cases: Sequence[ProvinceCase],
    tiers: Mapping[str, Tier],
    context: OsecanContext,
) -> list[dict[str, Any]]:
    bc_cases = {
        (item.stream, item.availability_case): item
        for item in province_cases
        if item.province == "BC"
    }
    national_refs = [
        ("national_supply_curve_t1_upper", "tier_cap", tiers["T1"].upper_pj,
         "Adopted T1 upper bound; not a policy target."),
        ("national_supply_curve_t2_upper", "tier_cap", tiers["T2"].upper_pj,
         "Adopted T2 upper bound; not a policy target."),
        ("national_supply_curve_t3_upper", "tier_cap", tiers["T3"].upper_pj,
         "Adopted T3 upper bound; not a policy target."),
        ("national_supply_curve_t4_upper", "tier_cap", tiers["T4"].upper_pj,
         "Adopted T4 upper bound and national supply-curve top; not a policy target."),
        (
            "osecan_2050_national_supply_context",
            "osecan_2050_context",
            context.national_2050_supply_pj,
            "2050 OSeCAN stress_119m national supply activity; context only, not a policy target.",
        ),
    ]
    bc_refs = [
        (
            "bc_zonal_supply_upper_2050",
            "bc_zonal_cap",
            context.bc_zonal_upper_pj,
            "2050 SUPBCWB1+SUPBCWB2 zonal upper bound; not a policy target.",
        ),
        (
            "osecan_2050_bc_supply_context",
            "osecan_2050_context",
            context.bc_2050_supply_pj,
            "2050 OSeCAN stress_119m BC supply activity; context only, not a policy target.",
        ),
    ]
    rows: list[dict[str, Any]] = []
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            national_tier = _tier_for_pj(national_case.pj, tiers)
            for reference, kind, value, note in national_refs:
                ratio = national_case.pj / value if value > 0.0 else 0.0
                rows.append(
                    {
                        "comparison_scope": "national",
                        "stream": stream,
                        "availability_case": case,
                        "case_odt": national_case.odt,
                        "case_pj": national_case.pj,
                        "case_tier": national_tier,
                        "reference": reference,
                        "reference_kind": kind,
                        "reference_pj": value,
                        "ratio": ratio,
                        "headroom_pj": value - national_case.pj,
                        "headroom_ratio": 1.0 - ratio,
                        "reference_is_policy_target": LABEL_FALSE,
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "comparison_note": note,
                    }
                )
            bc_case = bc_cases[(stream, case)]
            for reference, kind, value, note in bc_refs:
                ratio = bc_case.pj / value if value > 0.0 else 0.0
                rows.append(
                    {
                        "comparison_scope": "BC",
                        "stream": stream,
                        "availability_case": case,
                        "case_odt": bc_case.odt,
                        "case_pj": bc_case.pj,
                        "case_tier": "",
                        "reference": reference,
                        "reference_kind": kind,
                        "reference_pj": value,
                        "ratio": ratio,
                        "headroom_pj": value - bc_case.pj,
                        "headroom_ratio": 1.0 - ratio,
                        "reference_is_policy_target": LABEL_FALSE,
                        "model_estimate": LABEL_TRUE,
                        "observed": LABEL_FALSE,
                        "policy_grade": LABEL_FALSE,
                        "official_osecan_changed": LABEL_FALSE,
                        "comparison_note": note,
                    }
                )
    return rows


def _check(
    checks: list[dict[str, Any]], name: str, passed: bool, detail: str
) -> None:
    checks.append({"check": name, "passed": bool(passed), "detail": detail})


def _validate(
    province_cases: Sequence[ProvinceCase],
    national: Mapping[tuple[str, str], NationalCase],
    handoff_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
    tiers: Mapping[str, Tier],
    context: OsecanContext,
    osecan_tree_before: str,
    osecan_tree_after: str,
    osecan_changed_files: Sequence[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    _check(
        checks,
        "OSeCAN tree hash unchanged",
        osecan_tree_before == osecan_tree_after and not osecan_changed_files,
        (
            f"before={osecan_tree_before[:16]}... after={osecan_tree_after[:16]}... "
            f"changed_files={len(osecan_changed_files)}"
        ),
    )
    _check(
        checks,
        "no combined residue stream",
        not any(str(row["stream"]) == "combined" for row in handoff_rows),
        "process and harvest remain separate",
    )
    _check(
        checks,
        "handoff row count and uniqueness",
        len(handoff_rows)
        == len(PROVINCES) * (len(PROCESS_CASES) + len(HARVEST_CASES))
        and len(
            {
                (
                    row["province"],
                    row["stream"],
                    row["availability_case"],
                )
                for row in handoff_rows
            }
        )
        == len(handoff_rows),
        f"rows={len(handoff_rows)}",
    )
    _check(
        checks,
        "handoff labels",
        all(
            row["model_estimate"] == LABEL_TRUE
            and row["observed"] == LABEL_FALSE
            and row["policy_grade"] == LABEL_FALSE
            and row["official_osecan_changed"] == LABEL_FALSE
            for row in handoff_rows
        ),
        "model_estimate=true, observed=false, policy_grade=false, official_osecan_changed=false",
    )

    arithmetic_ok = True
    arithmetic_detail = ""
    for row in handoff_rows:
        expected = float(row["odt"]) * EXPECTED_LHV_GJ_PER_ODT / PJ_GJ
        if abs(float(row["pj"]) - expected) > 1e-6 + 1e-8 * abs(expected):
            arithmetic_ok = False
            arithmetic_detail = (
                f"{row['province']}/{row['stream']}/{row['availability_case']}: "
                f"{row['pj']} != {expected}"
            )
            break
    _check(checks, "handoff PJ arithmetic", arithmetic_ok, arithmetic_detail or "all rows")

    reconciliation_ok = True
    reconciliation_detail = ""
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            total_odt = math.fsum(
                float(row["odt"])
                for row in handoff_rows
                if row["stream"] == stream and row["availability_case"] == case
            )
            total_pj = math.fsum(
                float(row["pj"])
                for row in handoff_rows
                if row["stream"] == stream and row["availability_case"] == case
            )
            if (
                abs(total_odt - national_case.odt) > 1e-3
                or abs(total_pj - national_case.pj) > 1e-5
            ):
                reconciliation_ok = False
                reconciliation_detail = f"{stream}/{case} does not reconcile"
                break
        if not reconciliation_ok:
            break
    _check(
        checks,
        "handoff reconciles to national curve",
        reconciliation_ok,
        reconciliation_detail or "province rows sum once to national totals",
    )

    zone_reconciliation_ok = True
    zone_detail = ""
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            national_case = national[(stream, case)]
            selected = [
                row
                for row in zone_rows
                if row["stream"] == stream and row["availability_case"] == case
            ]
            total_odt = math.fsum(float(row["odt"]) for row in selected)
            total_pj = math.fsum(float(row["pj"]) for row in selected)
            if (
                abs(total_odt - national_case.odt) > 1e-3
                or abs(total_pj - national_case.pj) > 1e-5
            ):
                zone_reconciliation_ok = False
                zone_detail = f"{stream}/{case} zone rows do not reconcile"
                break
        if not zone_reconciliation_ok:
            break
    _check(
        checks,
        "zone summary reconciles without double count",
        zone_reconciliation_ok,
        zone_detail or "mapped zones plus UNMAPPED equal national totals",
    )

    t1_ok = True
    n1_ok = True
    t1_detail = ""
    n1_detail = ""
    for stream, cases in (
        (PROCESS_STREAM, PROCESS_CASES),
        (HARVEST_STREAM, HARVEST_CASES),
    ):
        for case, _ in cases:
            for zone_id, members, flag in (
                ("T1", ("NB", "NS", "PE"), "t1"),
                ("N1", ("YT", "NT", "NU"), "n1"),
            ):
                zone_row = next(
                    row
                    for row in zone_rows
                    if row["zone_id"] == zone_id
                    and row["stream"] == stream
                    and row["availability_case"] == case
                )
                expected_pj = math.fsum(
                    item.pj
                    for item in province_cases
                    if item.province in members
                    and item.stream == stream
                    and item.availability_case == case
                )
                if abs(float(zone_row["pj"]) - expected_pj) > 1e-5:
                    if flag == "t1":
                        t1_ok = False
                        t1_detail = f"T1 {stream}/{case} mismatch"
                    else:
                        n1_ok = False
                        n1_detail = f"N1 {stream}/{case} mismatch"
    _check(
        checks,
        "T1 Atlantic aggregate sums NB+NS+PE once",
        t1_ok,
        t1_detail or "T1 exact aggregate",
    )
    _check(
        checks,
        "N1 territorial aggregate sums YT+NT+NU once",
        n1_ok,
        n1_detail or "N1 exact aggregate",
    )

    unmapped_provinces = {
        item.province
        for item in province_cases
        if not item.assignment.zone_id
    }
    _check(
        checks,
        "unmapped provinces are explicit",
        unmapped_provinces == {"BC", "AB", "ON", "QC"},
        f"unmapped={sorted(unmapped_provinces)}",
    )
    _check(
        checks,
        "zone mapping coverage",
        len({item.province for item in province_cases if item.assignment.zone_id})
        == 9,
        "9 of 13 jurisdictions map to one OSeCAN zone",
    )
    _check(
        checks,
        "comparison rows and labels",
        len(comparison_rows)
        == (len(PROCESS_CASES) + len(HARVEST_CASES)) * 7
        and all(
            row["reference_is_policy_target"] == LABEL_FALSE
            and row["model_estimate"] == LABEL_TRUE
            and row["observed"] == LABEL_FALSE
            and row["policy_grade"] == LABEL_FALSE
            and row["official_osecan_changed"] == LABEL_FALSE
            for row in comparison_rows
        ),
        f"rows={len(comparison_rows)}",
    )
    expected_tiers = {
        ("process", "process_availability_000"): "T1",
        ("process", "process_availability_025"): "T3",
        ("process", "process_availability_050"): "above_T4",
        ("process", "process_availability_075"): "above_T4",
        ("harvest", "harvest_recovery_035"): "T1",
        ("harvest", "harvest_recovery_060"): "T2",
        ("harvest", "harvest_recovery_075"): "T3",
    }
    tier_ok = True
    tier_detail = ""
    for (stream, case), expected in expected_tiers.items():
        actual = _tier_for_pj(national[(stream, case)].pj, tiers)
        if actual != expected:
            tier_ok = False
            tier_detail = f"{stream}/{case}: {actual} != {expected}"
            break
    _check(checks, "national case tier positions", tier_ok, tier_detail or "as expected")
    _check(
        checks,
        "2050 context values",
        abs(context.national_2050_supply_pj - EXPECTED_NATIONAL_2050_CONTEXT_PJ) <= 1e-5
        and abs(context.bc_2050_supply_pj - EXPECTED_BC_2050_CONTEXT_PJ) <= 1e-5
        and abs(context.bc_zonal_upper_pj - EXPECTED_BC_ZONAL_UPPER_PJ) <= 1e-6,
        (
            f"national={context.national_2050_supply_pj:.5f} "
            f"BC={context.bc_2050_supply_pj:.5f} "
            f"BC_zonal={context.bc_zonal_upper_pj:.6f}"
        ),
    )
    _check(
        checks,
        "supply-curve upper bound",
        abs(tiers["T4"].upper_pj - EXPECTED_TIER_UPPER_PJ) <= 1e-6,
        f"T4 upper={tiers['T4'].upper_pj:.6f}",
    )
    return checks


def _write_csv(
    path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]
) -> None:
    if not rows:
        raise ForestryOsecanHandoffError(f"refusing to write empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fieldnames))
        writer.writeheader()
        for row in rows:
            rendered: dict[str, Any] = {}
            for key, value in row.items():
                if isinstance(value, float):
                    if not math.isfinite(value):
                        raise ForestryOsecanHandoffError(
                            f"refusing to write non-finite {key}: {value!r}"
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
    summary: Mapping[str, Any],
    handoff_rows: Sequence[Mapping[str, Any]],
    zone_rows: Sequence[Mapping[str, Any]],
    comparison_rows: Sequence[Mapping[str, Any]],
) -> str:
    coverage = summary["zone_mapping"]
    totals = summary["handoff_totals"]
    lines = [
        "# Forestry Residue Handoff to OSeCAN v1",
        "",
        (
            "This is a read-only forestry-side screening handoff from "
            "`national_residue_curve_v1` province/case totals to OSeCAN zones. "
            "It is not an official OSeCAN result, an operating plan, or a "
            "policy target."
        ),
        "",
        (
            "All values are model estimates: `model_estimate=true`, "
            "`observed=false`, `policy_grade=false`, and "
            "`official_osecan_changed=false`. Process and harvest residue "
            "remain separate streams and are never summed into a combined row."
        ),
        "",
        "## Labels and boundary",
        "",
        "- `official_osecan_changed=false` on every row.",
        "- No official OSeCAN file and no public OSeCAN surface is modified.",
        "- The OSeCAN tree is hashed before and after the build; hashes must match.",
        "- The 2050 OSeCAN supply values are context, not policy targets.",
        "",
        "## Zone mapping and coverage",
        "",
        (
            f"{coverage['mapped_province_count']} of "
            f"{coverage['province_count']} jurisdictions map to exactly one "
            "OSeCAN zone. The remaining provinces span multiple zones and are "
            "kept in the explicit `UNMAPPED` bucket."
        ),
        "",
        "| Zone | Provinces mapped | Status |",
        "|---|---|---|",
    ]
    for zone_id in ZONE_ORDER:
        zone = coverage["zones"][zone_id]
        lines.append(
            f"| {zone_id} | {', '.join(zone['mapped_provinces']) or '—'} | "
            f"{zone['status']} |"
        )
    lines.extend(
        [
            (
                f"| {UNMAPPED_ZONE} | "
                f"{', '.join(coverage['unmapped_provinces'])} | "
                "unmapped_multi_zone_province |"
            ),
            "",
            "Volume coverage by case (PJ):",
            "",
            "| Stream | Case | Mapped | UNMAPPED | Total |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        for case in (
            [name for name, _ in PROCESS_CASES]
            if stream == PROCESS_STREAM
            else [name for name, _ in HARVEST_CASES]
        ):
            mapped = sum(
                float(row["pj"])
                for row in zone_rows
                if row["stream"] == stream
                and row["availability_case"] == case
                and row["zone_id"] != UNMAPPED_ZONE
            )
            unmapped = sum(
                float(row["pj"])
                for row in zone_rows
                if row["stream"] == stream
                and row["availability_case"] == case
                and row["zone_id"] == UNMAPPED_ZONE
            )
            lines.append(
                f"| {stream} | `{case}` | {mapped:,.3f} | "
                f"{unmapped:,.3f} | {mapped + unmapped:,.3f} |"
            )
    lines.extend(
        [
            "",
            "## Handoff PJ by zone/case",
            "",
            "| Zone | Process 0% | Process 25% | Process 50% | Process 75% | Harvest 35% | Harvest 60% | Harvest 75% |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    zone_ids = list(
        dict.fromkeys(
            row["zone_id"]
            for row in zone_rows
            if row["stream"] == PROCESS_STREAM
        )
    )
    for zone_id in zone_ids:
        values: list[str] = []
        for stream, cases in (
            (PROCESS_STREAM, PROCESS_CASES),
            (HARVEST_STREAM, HARVEST_CASES),
        ):
            for case, _ in cases:
                record = next(
                    row
                    for row in zone_rows
                    if row["zone_id"] == zone_id
                    and row["stream"] == stream
                    and row["availability_case"] == case
                )
                values.append(f"{float(record['pj']):,.3f}")
        lines.append(f"| {zone_id} | " + " | ".join(values) + " |")
    lines.extend(
        [
            "",
            "National totals (PJ):",
            "",
            "| Stream | Case | PJ |",
            "|---|---|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        for case, pj in totals[stream].items():
            lines.append(f"| {stream} | `{case}` | {float(pj):,.6f} |")
    lines.extend(
        [
            "",
            "## Supply-curve comparison",
            "",
            (
                "Ratios are case PJ divided by the reference. Headroom is "
                "reference minus case; a negative headroom means the case "
                "exceeds the reference. The 2050 values are context, not "
                "policy targets."
            ),
            "",
            "### National",
            "",
            "| Case | PJ | Tier | T1 headroom | T2 headroom | T3 headroom | T4 headroom | 2050 context ratio | 2050 context headroom |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        cases = (
            [name for name, _ in PROCESS_CASES]
            if stream == PROCESS_STREAM
            else [name for name, _ in HARVEST_CASES]
        )
        for case in cases:
            selected = [
                row
                for row in comparison_rows
                if row["comparison_scope"] == "national"
                and row["stream"] == stream
                and row["availability_case"] == case
            ]
            by_ref = {row["reference"]: row for row in selected}
            case_row = selected[0]
            lines.append(
                f"| `{case}` | {float(case_row['case_pj']):,.3f} | "
                f"{case_row['case_tier']} | "
                f"{float(by_ref['national_supply_curve_t1_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t2_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t3_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['national_supply_curve_t4_upper']['headroom_pj']):,.3f} | "
                f"{float(by_ref['osecan_2050_national_supply_context']['ratio']):.3f} | "
                f"{float(by_ref['osecan_2050_national_supply_context']['headroom_pj']):,.3f} |"
            )
    lines.extend(
        [
            "",
            "### BC",
            "",
            "| Case | PJ | BC zonal cap ratio | BC zonal headroom | 2050 BC context ratio | 2050 BC context headroom |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for stream in (PROCESS_STREAM, HARVEST_STREAM):
        cases = (
            [name for name, _ in PROCESS_CASES]
            if stream == PROCESS_STREAM
            else [name for name, _ in HARVEST_CASES]
        )
        for case in cases:
            selected = [
                row
                for row in comparison_rows
                if row["comparison_scope"] == "BC"
                and row["stream"] == stream
                and row["availability_case"] == case
            ]
            by_ref = {row["reference"]: row for row in selected}
            case_row = selected[0]
            lines.append(
                f"| `{case}` | {float(case_row['case_pj']):,.3f} | "
                f"{float(by_ref['bc_zonal_supply_upper_2050']['ratio']):.3f} | "
                f"{float(by_ref['bc_zonal_supply_upper_2050']['headroom_pj']):,.3f} | "
                f"{float(by_ref['osecan_2050_bc_supply_context']['ratio']):.3f} | "
                f"{float(by_ref['osecan_2050_bc_supply_context']['headroom_pj']):,.3f} |"
            )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "- Zone mapping is screening-only and code-defined; no published sub-provincial zone polygon is used.",
            "- BC, AB, ON, and QC span multiple OSeCAN zones; they are not split because the crosswalk has no residue-split basis.",
            "- The T1 and N1 zones are exact aggregates of their member provinces; their totals are not duplicated province rows.",
            "- Process residue is facility-province based; harvest residue is origin-province based. They are separate and not summed.",
            "- Process rows use the `TOTAL` class row only; harvest rows use the `ALL` row only. Class detail is not double counted.",
            "- SUPBCW technology strings are candidate mappings, not operating assignments or official OSeCAN technology choices.",
            "- The 2050 OSeCAN national (152.923 PJ) and BC (43.524 PJ) values are model context, not policy targets.",
            "- The 213.54 PJ national supply-curve top is an adopted model bound, not a policy target.",
            "- No transport, cost, revenue, capacity-expansion, tenure, AAC, or policy claim is made.",
            "- No UI or public OSeCAN surface is changed.",
            "",
            "## Validation and status",
            "",
            (
                f"Build status: `{summary['status']}`. Validation checks: "
                f"{len(summary['validation']['checks'])}, failed: "
                f"{summary['validation']['failed_check_count']}."
            ),
            "",
            (
                "Run `.venv/bin/python -m "
                "workflows.national.facilities.forestry_osecan_handoff` to "
                "reproduce the artifact and `.venv/bin/python -m pytest "
                "tests/test_forestry_osecan_handoff.py -q` to run the "
                "regression tests."
            ),
            "",
            "No policy/AAC claim is made.",
            "",
        ]
    )
    return "\n".join(lines)


def build(output_dir: Path | None = None) -> dict[str, Any]:
    """Build the read-only forestry residue handoff and return its summary."""
    target_dir = Path(output_dir) if output_dir is not None else OUTPUT_DIR
    generated_utc = datetime.now(UTC).isoformat(timespec="seconds")
    assert_no_unexpected_deletions()

    osecan_hashes_before, osecan_tree_before = _hash_tree(OSECAN_DIR)

    zones, province_zones = _parse_zone_crosswalk()
    province_cases = _parse_province_cases(zones, province_zones)
    national = _parse_national_cases(province_cases)
    tiers = _parse_tiers()
    context = _parse_osecan_context(tiers)

    handoff_rows = _handoff_rows(province_cases, national, tiers, context)
    zone_rows = _zone_summary_rows(province_cases, national, zones)
    comparison_rows = _comparison_rows(
        national, province_cases, tiers, context
    )

    osecan_hashes_after, osecan_tree_after = _hash_tree(OSECAN_DIR)
    changed_files = sorted(
        path
        for path in set(osecan_hashes_before) | set(osecan_hashes_after)
        if osecan_hashes_before.get(path) != osecan_hashes_after.get(path)
    )

    checks = _validate(
        province_cases,
        national,
        handoff_rows,
        zone_rows,
        comparison_rows,
        tiers,
        context,
        osecan_tree_before,
        osecan_tree_after,
        changed_files,
    )
    failed = sum(1 for check in checks if not check["passed"])
    if failed:
        raise ForestryOsecanHandoffError(f"{failed} validation checks failed")
    if changed_files:
        raise ForestryOsecanHandoffError(
            "official OSeCAN files changed during the build: "
            + ", ".join(changed_files)
        )

    zone_mapping = {
        "province_count": len(PROVINCES),
        "mapped_province_count": 9,
        "unmapped_province_count": 4,
        "mapped_provinces": [
            province
            for province in PROVINCE_ORDER
            if next(
                item
                for item in province_cases
                if item.province == province
            ).assignment.zone_id
        ],
        "unmapped_provinces": ["BC", "AB", "ON", "QC"],
        "zones": {
            zone_id: {
                "zone_name": zones[zone_id].zone_name,
                "official_status": zones[zone_id].official_status,
                "mapping_basis": zones[zone_id].mapping_basis,
                "geometry_available": zones[zone_id].geometry_available,
                "mapped_provinces": [
                    province
                    for province in PROVINCE_ORDER
                    if province_zones[province] == (zone_id,)
                ],
                "status": (
                    "mapped_aggregate_zone"
                    if len(
                        [
                            province
                            for province in PROVINCE_ORDER
                            if province_zones[province] == (zone_id,)
                        ]
                    )
                    > 1
                    else (
                        "mapped_single_zone"
                        if any(
                            province_zones[province] == (zone_id,)
                            for province in PROVINCE_ORDER
                        )
                        else "not_mapped_from_province_rows"
                    )
                ),
            }
            for zone_id in ZONE_ORDER
        },
    }

    handoff_totals: dict[str, dict[str, float]] = {
        PROCESS_STREAM: {
            case: national[(PROCESS_STREAM, case)].pj
            for case, _ in PROCESS_CASES
        },
        HARVEST_STREAM: {
            case: national[(HARVEST_STREAM, case)].pj
            for case, _ in HARVEST_CASES
        },
    }
    comparison_summary = {
        row["reference"] + "|" + row["comparison_scope"] + "|" + row["stream"] + "|" + row["availability_case"]: {
            "case_pj": row["case_pj"],
            "reference_pj": row["reference_pj"],
            "ratio": row["ratio"],
            "headroom_pj": row["headroom_pj"],
        }
        for row in comparison_rows
        if (
            row["reference"] in {
                "national_supply_curve_t4_upper",
                "osecan_2050_national_supply_context",
                "bc_zonal_supply_upper_2050",
                "osecan_2050_bc_supply_context",
            }
        )
    }

    summary: dict[str, Any] = {
        "artifact": ARTIFACT,
        "artifact_version": ARTIFACT_VERSION,
        "generated_utc": generated_utc,
        "status": "pass",
        "labels": {
            "model_estimate": True,
            "observed": False,
            "policy_grade": False,
            "official_osecan_changed": False,
            "not_policy": True,
            "context_is_policy_target": False,
        },
        "zone_mapping": zone_mapping,
        "record_counts": {
            "handoff_rows": len(handoff_rows),
            "zone_summary_rows": len(zone_rows),
            "comparison_rows": len(comparison_rows),
            "provinces": len(PROVINCES),
            "mapped_provinces": 9,
            "unmapped_provinces": 4,
            "process_cases": len(PROCESS_CASES),
            "harvest_cases": len(HARVEST_CASES),
        },
        "handoff_totals": handoff_totals,
        "supply_curve": {
            "upper_pj": tiers["T4"].upper_pj,
            "tiers": {
                tier_id: {
                    "technology": tier.technology,
                    "lower_pj": tier.lower_pj,
                    "upper_pj": tier.upper_pj,
                    "incremental_cap_pj": tier.incremental_cap_pj,
                }
                for tier_id, tier in tiers.items()
            },
            "case_tier": {
                f"{stream}/{case}": _tier_for_pj(
                    national[(stream, case)].pj, tiers
                )
                for stream, cases in (
                    (PROCESS_STREAM, PROCESS_CASES),
                    (HARVEST_STREAM, HARVEST_CASES),
                )
                for case, _ in cases
            },
        },
        "osecan_context": {
            "run": "stress_119m",
            "year": 2050,
            "national_supply_pj": context.national_2050_supply_pj,
            "bc_supply_pj": context.bc_2050_supply_pj,
            "national_supply_pj_rounded": 152.923,
            "bc_supply_pj_rounded": 43.524,
            "bc_zonal_upper_pj": context.bc_zonal_upper_pj,
            "dispatch_source": context.dispatch_source,
            "dispatch_sha256": context.dispatch_sha256,
            "manifest_status": context.manifest_status,
            "context_is_policy_target": False,
        },
        "comparison": comparison_summary,
        "inputs": {
            str(path.relative_to(REPO_ROOT)): _sha256(path)
            for path in (
                PROVINCE_CURVE,
                NATIONAL_CURVE,
                CURVE_SUMMARY,
                CROSSWALK,
                TIER_DEFINITIONS,
                TIER_LIMITS,
                BC_ZONAL_LIMITS,
                SUPPLY_DEFINITION,
                DISPATCH,
                NATIONAL_MANIFEST,
            )
        },
        "osecan_integrity": {
            "root": str(OSECAN_DIR.relative_to(REPO_ROOT)),
            "file_count_before": len(osecan_hashes_before),
            "file_count_after": len(osecan_hashes_after),
            "tree_sha256_before": osecan_tree_before,
            "tree_sha256_after": osecan_tree_after,
            "unchanged": (
                osecan_tree_before == osecan_tree_after
                and not changed_files
            ),
            "changed_files": changed_files,
        },
        "validation": {
            "checks": checks,
            "passed": failed == 0,
            "failed_check_count": failed,
        },
        "caveats": [
            "All values are model estimates; observed=false and policy_grade=false.",
            "BC, AB, ON, and QC span multiple OSeCAN zones and are explicit UNMAPPED provinces; no sub-provincial split is invented.",
            "Process and harvest residue are separate streams and are never summed.",
            "Process rows use the TOTAL class row only; harvest rows use the ALL row only.",
            "SUPBCW technology strings are candidate mappings, not operating assignments.",
            "The 2050 OSeCAN and supply-curve values are context, not policy targets.",
            "No official OSeCAN file or public OSeCAN surface is changed.",
            "No transport, cost, tenure, AAC, or policy claim is made.",
        ],
        "reproduce": (
            ".venv/bin/python -m "
            "workflows.national.facilities.forestry_osecan_handoff"
        ),
    }

    handoff_fields = [
        "province",
        "province_name",
        "zone_id",
        "zone_name",
        "zone_mapped",
        "zone_mapping_status",
        "zone_candidates",
        "zone_mapping_basis",
        "stream",
        "residue_class",
        "province_basis",
        "availability_case",
        "availability_fraction",
        "odt",
        "pj",
        "lhv_gj_per_odt",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "supbcw_mapping_note",
        "supply_curve_tier_at_national_case",
        "supply_curve_tier_upper_pj",
        "evidence_tier",
        "rsyc_comparable",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
        "notes",
    ]
    zone_fields = [
        "zone_id",
        "zone_name",
        "zone_mapping_status",
        "provinces_included",
        "mapped_province_count",
        "stream",
        "availability_case",
        "odt",
        "pj",
        "national_case_pj",
        "share_of_national_case",
        "share_status",
        "supbcw_technology_suggestion",
        "supbcw_mapping",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "caveat",
    ]
    comparison_fields = [
        "comparison_scope",
        "stream",
        "availability_case",
        "case_odt",
        "case_pj",
        "case_tier",
        "reference",
        "reference_kind",
        "reference_pj",
        "ratio",
        "headroom_pj",
        "headroom_ratio",
        "reference_is_policy_target",
        "model_estimate",
        "observed",
        "policy_grade",
        "official_osecan_changed",
        "comparison_note",
    ]

    target_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        target_dir / "forestry_residue_handoff.csv",
        handoff_rows,
        handoff_fields,
    )
    _write_csv(target_dir / "zone_summary.csv", zone_rows, zone_fields)
    _write_csv(
        target_dir / "supply_curve_comparison.csv",
        comparison_rows,
        comparison_fields,
    )
    _write_json(target_dir / "summary.json", summary)
    (target_dir / "FORESTRY_OSECAN_HANDOFF_V1.md").write_text(
        _report(summary, handoff_rows, zone_rows, comparison_rows),
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
        "inputs": summary["inputs"],
        "outputs": outputs,
        "record_counts": {
            "handoff_rows": len(handoff_rows),
            "zone_summary_rows": len(zone_rows),
            "comparison_rows": len(comparison_rows),
            "provinces": len(PROVINCES),
            "mapped_provinces": 9,
            "unmapped_provinces": 4,
            "process_cases": len(PROCESS_CASES),
            "harvest_cases": len(HARVEST_CASES),
        },
        "zone_mapping": summary["zone_mapping"],
        "handoff_totals": summary["handoff_totals"],
        "supply_curve": summary["supply_curve"],
        "osecan_context": summary["osecan_context"],
        "osecan_integrity": summary["osecan_integrity"],
        "validation": summary["validation"],
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
                "handoff_rows": summary["record_counts"]["handoff_rows"],
                "process_50pct_pj": summary["handoff_totals"][PROCESS_STREAM][
                    "process_availability_050"
                ],
                "harvest_60pct_pj": summary["handoff_totals"][HARVEST_STREAM][
                    "harvest_recovery_060"
                ],
                "mapped_provinces": summary["zone_mapping"][
                    "mapped_province_count"
                ],
                "unmapped_provinces": summary["zone_mapping"][
                    "unmapped_provinces"
                ],
                "osecan_unchanged": summary["osecan_integrity"]["unchanged"],
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
